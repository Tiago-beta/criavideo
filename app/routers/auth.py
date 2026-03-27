from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    authenticate_local_user,
    build_access_token,
    find_user_by_email,
    get_current_user,
    hash_password,
    resolve_user_from_token,
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


def _session_response(user: AppUser) -> dict:
    return {
        "access_token": build_access_token(user),
        "token_type": "bearer",
        "user": user_to_dict(user),
    }


@router.get("/providers")
async def get_auth_providers():
    return {
        "google_enabled": bool(settings.google_oauth_client_id),
        "google_client_id": settings.google_oauth_client_id,
        "levita_url": settings.levita_url,
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
    user = await authenticate_local_user(req.email, req.password, db)
    if not user:
        existing = await find_user_by_email(req.email, db)
        if existing and existing.auth_source == "levita" and not existing.password_hash:
            raise HTTPException(status_code=401, detail="Esta conta usa login pelo Levita. Clique em 'Entrar com Levita'.")
        if existing and existing.auth_source == "google" and not existing.password_hash:
            raise HTTPException(status_code=401, detail="Esta conta usa login Google. Clique em 'Fazer login com o Google'.")
        raise HTTPException(status_code=401, detail="Email ou senha invalidos")
    return _session_response(user)


@router.post("/google")
async def google_login(
    req: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    if not settings.google_oauth_client_id:
        raise HTTPException(status_code=400, detail="Google login nao configurado")

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
        raise HTTPException(status_code=400, detail="Google nao retornou email")

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
    db: AsyncSession = Depends(get_db),
):
    user = await resolve_user_from_token(req.token, db)
    return _session_response(user)


@router.post("/logout")
async def logout():
    return {"ok": True}
