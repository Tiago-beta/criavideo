"""
Google RISC receiver — Cross-Account Protection security events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt as jose_jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AppUser, Platform, SecurityEventReceipt, SocialAccount

router = APIRouter(prefix="/api/security", tags=["security"])
settings = get_settings()
logger = logging.getLogger(__name__)

RISC_CONFIGURATION_URL = "https://accounts.google.com/.well-known/risc-configuration"
RISC_CACHE_TTL_SECONDS = 3600

_risc_config_cache: dict[str, Any] = {"expires_at": 0, "data": None}
_risc_jwks_cache: dict[str, Any] = {"expires_at": 0, "uri": "", "keys": None}
_risc_cache_lock = asyncio.Lock()


def _parse_csv(value: str) -> list[str]:
    items: list[str] = []
    for item in str(value or "").split(","):
        clean = item.strip()
        if clean:
            items.append(clean)
    return items


def _allowed_risc_audiences() -> list[str]:
    # Google validates `aud` against OAuth client IDs.
    values = set(_parse_csv(settings.risc_allowed_audiences))
    if settings.google_oauth_client_id:
        values.add(settings.google_oauth_client_id.strip())
    return sorted(v for v in values if v)


def _allowed_risc_issuers(issuer: str) -> list[str]:
    values = {"accounts.google.com", "https://accounts.google.com", issuer.strip()}
    normalized = set()
    for value in values:
        if not value:
            continue
        normalized.add(value.rstrip("/"))
        normalized.add(value.rstrip("/") + "/")
    return sorted(v for v in normalized if v)


async def _get_risc_configuration() -> dict[str, Any]:
    now = int(time.time())
    cached = _risc_config_cache.get("data")
    if cached and int(_risc_config_cache.get("expires_at", 0)) > now:
        return cached

    async with _risc_cache_lock:
        now = int(time.time())
        cached = _risc_config_cache.get("data")
        if cached and int(_risc_config_cache.get("expires_at", 0)) > now:
            return cached

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(RISC_CONFIGURATION_URL)
            resp.raise_for_status()
            config = resp.json()

        issuer = str(config.get("issuer") or "").strip()
        jwks_uri = str(config.get("jwks_uri") or "").strip()
        if not issuer or not jwks_uri:
            raise HTTPException(status_code=503, detail="Google RISC configuration unavailable")

        _risc_config_cache["data"] = config
        _risc_config_cache["expires_at"] = now + RISC_CACHE_TTL_SECONDS
        return config


async def _get_risc_jwks(jwks_uri: str) -> dict[str, Any]:
    now = int(time.time())
    same_uri = _risc_jwks_cache.get("uri") == jwks_uri
    cached = _risc_jwks_cache.get("keys")
    if same_uri and cached and int(_risc_jwks_cache.get("expires_at", 0)) > now:
        return cached

    async with _risc_cache_lock:
        now = int(time.time())
        same_uri = _risc_jwks_cache.get("uri") == jwks_uri
        cached = _risc_jwks_cache.get("keys")
        if same_uri and cached and int(_risc_jwks_cache.get("expires_at", 0)) > now:
            return cached

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
            jwks = resp.json()

        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list) or not keys:
            raise HTTPException(status_code=503, detail="Google JWKS is empty")

        _risc_jwks_cache["uri"] = jwks_uri
        _risc_jwks_cache["keys"] = jwks
        _risc_jwks_cache["expires_at"] = now + RISC_CACHE_TTL_SECONDS
        return jwks


def _pick_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        return None
    if kid:
        for key in keys:
            if isinstance(key, dict) and key.get("kid") == kid:
                return key
    for key in keys:
        if isinstance(key, dict):
            return key
    return None


async def _extract_security_token(request: Request) -> str:
    body = await request.body()
    text = body.decode("utf-8", errors="ignore").strip()
    content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()

    if content_type in {"application/secevent+jwt", "application/jwt", "text/plain"}:
        return text

    if content_type == "application/json":
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            for key in ("jwt", "signedJwt", "token", "security_event_token", "assertion"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if text.count(".") >= 2:
            return text

    if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        try:
            form = await request.form()
            for key in ("jwt", "signedJwt", "token", "security_event_token", "assertion"):
                value = form.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except Exception:
            pass

    if text.count(".") >= 2:
        return text

    return ""


async def _validate_risc_token(token: str) -> dict[str, Any]:
    audiences = _allowed_risc_audiences()
    if not audiences:
        raise HTTPException(status_code=503, detail="RISC audiences not configured")

    config = await _get_risc_configuration()
    issuer = str(config.get("issuer") or "https://accounts.google.com").strip()
    jwks_uri = str(config.get("jwks_uri") or "").strip()
    if not jwks_uri:
        raise HTTPException(status_code=503, detail="Google RISC jwks_uri not available")

    jwks = await _get_risc_jwks(jwks_uri)

    try:
        header = jose_jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid RISC token header") from exc

    algorithm = str(header.get("alg") or "RS256")
    kid = str(header.get("kid") or "")
    jwk = _pick_jwk(jwks, kid)
    if not jwk:
        raise HTTPException(status_code=400, detail="Unable to resolve Google signing key")

    try:
        payload = jose_jwt.decode(
            token,
            jwk,
            algorithms=[algorithm],
            audience=audiences,
            issuer=_allowed_risc_issuers(issuer),
            options={"verify_exp": False},
        )
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid RISC token") from exc

    events = payload.get("events")
    if not isinstance(events, dict) or not events:
        raise HTTPException(status_code=400, detail="RISC token without events")

    return payload


def _extract_subject_sub(events: dict[str, Any]) -> str:
    for details in events.values():
        if not isinstance(details, dict):
            continue
        subject = details.get("subject")
        if not isinstance(subject, dict):
            continue
        sub = str(subject.get("sub") or "").strip()
        if sub:
            return sub
    return ""


async def _disconnect_user_youtube_accounts(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        delete(SocialAccount).where(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == Platform.YOUTUBE,
        )
    )
    return int(result.rowcount or 0)


async def _apply_risc_actions(
    db: AsyncSession,
    user: AppUser | None,
    events: dict[str, Any],
) -> list[str]:
    if not user:
        return []

    actions: list[str] = []
    event_types = list(events.keys())
    should_disconnect_youtube = any(
        (
            "oauth/event-type/token-revoked" in event_type
            or "oauth/event-type/tokens-revoked" in event_type
            or "risc/event-type/sessions-revoked" in event_type
            or "risc/event-type/account-disabled" in event_type
        )
        for event_type in event_types
    )

    if should_disconnect_youtube:
        removed = await _disconnect_user_youtube_accounts(db, user.id)
        if removed:
            actions.append(f"youtube_disconnected:{removed}")

    for event_type in event_types:
        if "risc/event-type/account-disabled" in event_type:
            if user.auth_source == "google" and not user.password_hash:
                user.is_active = False
                actions.append("google_user_deactivated")
            else:
                user.google_sub = None
                actions.append("google_link_removed")
            break

    return actions


@router.get("/risc/status")
async def risc_status() -> dict[str, Any]:
    audiences = _allowed_risc_audiences()
    receiver_url = f"{settings.site_url}{settings.risc_endpoint_path}"
    return {
        "ready": bool(audiences),
        "receiver_url": receiver_url,
        "audiences": audiences,
    }


@router.post("/risc/events", status_code=202)
async def receive_risc_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    token = await _extract_security_token(request)
    if not token:
        raise HTTPException(status_code=400, detail="Missing security event token")

    payload = await _validate_risc_token(token)
    jti = str(payload.get("jti") or "").strip()
    if not jti:
        raise HTTPException(status_code=400, detail="RISC token missing jti")

    existing = await db.execute(
        select(SecurityEventReceipt.id).where(
            SecurityEventReceipt.provider == "google_risc",
            SecurityEventReceipt.event_jti == jti,
        )
    )
    if existing.first():
        return {"accepted": True, "duplicate": True}

    events = payload.get("events") if isinstance(payload.get("events"), dict) else {}
    event_types = sorted(events.keys())
    subject_sub = _extract_subject_sub(events)

    user = None
    if subject_sub:
        user_result = await db.execute(select(AppUser).where(AppUser.google_sub == subject_sub))
        user = user_result.scalar_one_or_none()

    actions = await _apply_risc_actions(db, user, events)

    receipt = SecurityEventReceipt(
        provider="google_risc",
        event_jti=jti,
        event_type=",".join(event_types)[:255] if event_types else "unknown",
        audience=str(payload.get("aud") or "")[:320],
        issuer=str(payload.get("iss") or "")[:255],
        subject=subject_sub[:255],
        payload=payload,
    )
    db.add(receipt)
    await db.commit()

    logger.info(
        "RISC event accepted: jti=%s events=%s actions=%s user_id=%s",
        jti,
        event_types,
        actions,
        user.id if user else None,
    )

    return {
        "accepted": True,
        "events": event_types,
        "actions": actions,
    }
