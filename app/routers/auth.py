from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    authenticate_local_user,
    build_access_token,
    find_user_by_email,
    get_current_user,
    hash_password,
    is_valid_tevoxy_integration,
    sync_legacy_levita_user_from_token,
    sync_tevoxy_user_from_token,
    user_to_dict,
)
from app.config import get_settings
from app.database import get_db
from app.models import AppUser

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=20)


class TokenExchangeRequest(BaseModel):
    token: str = Field(min_length=20)


class LevitaLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=128)


def _session_response(user: AppUser) -> dict:
    return {
        "access_token": build_access_token(user),
        "token_type": "bearer",
        "user": user_to_dict(user),
    }


async def _migrate_legacy_levita_login(
    email: str,
    password: str,
    db: AsyncSession,
    existing_user: AppUser | None,
) -> AppUser:
    levita_base = (settings.levita_url or "https://levita.pro").rstrip("/")
    levita_login_url = f"{levita_base}/api/auth/login"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                levita_login_url,
                json={
                    "email": email,
                    "password": password,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Falha ao validar credenciais legadas no Levita") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=401, detail="Email ou senha inválidos")

    payload = response.json()
    payload_user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    levita_token = payload.get("token") or payload.get("access_token")
    if not levita_token:
        raise HTTPException(status_code=502, detail="Levita não retornou token de sessão")

    migrated_user = existing_user
    try:
        migrated_user = await sync_legacy_levita_user_from_token(str(levita_token), db)
    except HTTPException:
        pass

    if not migrated_user:
        migrated_user = AppUser(
            email=email,
            display_name=str(payload_user.get("name") or payload_user.get("display_name") or email.split("@", 1)[0]).strip(),
            auth_source="local",
            role=str(payload_user.get("role") or "user").strip() or "user",
            is_active=True,
            email_verified=True,
            last_login_at=datetime.utcnow(),
        )
        db.add(migrated_user)

    migrated_user.email = email
    if not str(migrated_user.external_user_id or "").strip():
        external_user_id = payload_user.get("id") or payload_user.get("sub")
        if external_user_id is not None:
            migrated_user.external_user_id = str(external_user_id)
    migrated_user.display_name = str(
        payload_user.get("name") or payload_user.get("display_name") or migrated_user.display_name or email.split("@", 1)[0]
    ).strip()
    migrated_user.role = str(payload_user.get("role") or migrated_user.role or "user").strip() or "user"
    migrated_user.password_hash = hash_password(password)
    migrated_user.auth_source = "local"
    migrated_user.is_active = True
    migrated_user.email_verified = True
    migrated_user.last_login_at = datetime.utcnow()

    await db.commit()
    await db.refresh(migrated_user)
    return migrated_user


@router.get("/providers")
async def get_auth_providers():
    return {
        "google_enabled": bool(settings.google_oauth_client_id),
        "google_client_id": settings.google_oauth_client_id,
    }


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"user": user}


@router.post("/register")
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    email = req.email.strip().lower()
    existing = await find_user_by_email(email, db)
    if existing:
        raise HTTPException(status_code=409, detail="Este email ja esta em uso")

    user = AppUser(
        email=email,
        display_name=req.name.strip(),
        password_hash=hash_password(req.password),
        auth_source="local",
        role="user",
        is_active=True,
        email_verified=False,
        last_login_at=datetime.utcnow(),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _session_response(user)


@router.post("/login")
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    normalized_email = req.email.strip().lower()
    user = await authenticate_local_user(normalized_email, req.password, db)
    if not user:
        existing = await find_user_by_email(normalized_email, db)
        if existing and existing.auth_source == "levita":
            migrated_user = await _migrate_legacy_levita_login(
                email=normalized_email,
                password=req.password,
                db=db,
                existing_user=existing,
            )
            return _session_response(migrated_user)
        if existing and existing.auth_source == "google" and not existing.password_hash:
            raise HTTPException(status_code=401, detail="Esta conta usa login Google. Clique em 'Fazer login com o Google'.")
        raise HTTPException(status_code=401, detail="Email ou senha inválidos")
    return _session_response(user)


@router.post("/google")
async def google_login(
    req: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    if not settings.google_oauth_client_id:
        raise HTTPException(status_code=400, detail="Google login não configurado")

    try:
        payload = google_id_token.verify_oauth2_token(
            req.credential,
            GoogleRequest(),
            settings.google_oauth_client_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Falha ao validar login Google") from exc

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google não retornou email")

    user = await find_user_by_email(email, db)
    if user:
        user.display_name = payload.get("name") or user.display_name
        user.google_sub = payload.get("sub") or user.google_sub
        user.email_verified = bool(payload.get("email_verified", True))
        user.last_login_at = datetime.utcnow()
    else:
        user = AppUser(
            email=email,
            display_name=payload.get("name") or email.split("@", 1)[0],
            auth_source="google",
            google_sub=payload.get("sub"),
            role="user",
            is_active=True,
            email_verified=bool(payload.get("email_verified", True)),
            last_login_at=datetime.utcnow(),
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)
    return _session_response(user)


@router.post("/exchange/levita")
async def exchange_levita_token(
    req: TokenExchangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    integration_name = str(request.headers.get("x-tevoxy-integration") or "").strip().lower()
    integration_secret = str(request.headers.get("x-tevoxy-secret") or "").strip()

    if not is_valid_tevoxy_integration(integration_name, integration_secret):
        raise HTTPException(
            status_code=410,
            detail="Login via Levita foi descontinuado. Entre com email e senha no CriaVideo.",
        )

    user = await sync_tevoxy_user_from_token(req.token, db)
    return _session_response(user)


@router.post("/login/levita")
async def login_with_levita_credentials(
    req: LevitaLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    raise HTTPException(
        status_code=410,
        detail="Login via Levita foi descontinuado. Entre com email e senha no CriaVideo.",
    )


@router.post("/logout")
async def logout():
    return {"ok": True}

