from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AppUser

security = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Invalid token") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 600_000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${iterations}${salt}${digest}".format(
        iterations=iterations,
        salt=base64.b64encode(salt).decode("ascii"),
        digest=base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False

    try:
        algorithm, iterations, salt, digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt.encode("ascii")),
            int(iterations),
        )
        return hmac.compare_digest(
            base64.b64encode(derived).decode("ascii"),
            digest,
        )
    except (TypeError, ValueError):
        return False


def build_access_token(user: AppUser) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "criavideo",
        "sub": str(user.id),
        "id": user.id,
        "email": user.email,
        "name": user.display_name,
        "role": user.role,
        "source": user.auth_source,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.auth_token_expiration_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError as exc:
        raise _unauthorized() from exc


def _normalize_email(payload: dict, external_user_id: str) -> str:
    email = (payload.get("email") or "").strip().lower()
    if email:
        return email
    source = (payload.get("source") or payload.get("iss") or "external").strip().lower()
    return f"{source}-user-{external_user_id}@local.invalid"


def _normalize_name(payload: dict, email: str) -> str:
    return (
        payload.get("name")
        or payload.get("username")
        or payload.get("full_name")
        or payload.get("given_name")
        or email.split("@", 1)[0]
        or "Cliente"
    )


def user_to_dict(user: AppUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.display_name,
        "role": user.role,
        "source": user.auth_source,
        "email_verified": user.email_verified,
    }


async def find_user_by_email(email: str, db: AsyncSession) -> AppUser | None:
    result = await db.execute(select(AppUser).where(AppUser.email == email.strip().lower()))
    return result.scalar_one_or_none()


async def find_user_by_id(user_id: int, db: AsyncSession) -> AppUser | None:
    return await db.get(AppUser, user_id)


async def resolve_user_from_token(token: str, db: AsyncSession) -> AppUser:
    payload = _decode_token(token)

    if payload.get("iss") == "criavideo":
        user_id = payload.get("id") or payload.get("sub")
        if user_id is None:
            raise _unauthorized()
        user = await find_user_by_id(int(user_id), db)
        if not user or not user.is_active:
            raise _unauthorized("User not found")
        user.last_login_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)
        return user

    external_user_id = payload.get("id") or payload.get("sub")
    if external_user_id is None:
        raise _unauthorized()

    external_user_id = str(external_user_id)
    email = _normalize_email(payload, external_user_id)
    display_name = _normalize_name(payload, email)
    role = payload.get("role") or "user"

    result = await db.execute(
        select(AppUser).where(
            AppUser.auth_source == "levita",
            AppUser.external_user_id == external_user_id,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        user = await find_user_by_email(email, db)

    if user:
        user.email = email
        user.display_name = display_name
        user.external_user_id = external_user_id
        user.auth_source = "levita"
        user.role = role
        user.email_verified = True
        user.last_login_at = datetime.utcnow()
    else:
        user = AppUser(
            email=email,
            display_name=display_name,
            auth_source="levita",
            external_user_id=external_user_id,
            role=role,
            is_active=True,
            email_verified=True,
            last_login_at=datetime.utcnow(),
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_local_user(email: str, password: str, db: AsyncSession) -> AppUser | None:
    user = await find_user_by_email(email, db)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not credentials or not credentials.credentials:
        raise _unauthorized("Missing token")
    user = await resolve_user_from_token(credentials.credentials, db)
    return user_to_dict(user)
