"""
Social Router — OAuth connection/disconnection for YouTube, TikTok, Instagram.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.auth import get_current_user
from app.database import get_db
from app.models import SocialAccount, Platform
from app.config import get_settings

router = APIRouter(prefix="/api/social", tags=["social"])
settings = get_settings()
STATE_MAX_AGE_SECONDS = 10 * 60


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _state_signature(payload_b64: str) -> str:
    secret = (settings.jwt_secret or "change_me").encode("utf-8")
    digest = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def _build_social_redirect(platform: str | None = None, reason: str = "") -> str:
    params = {}
    if platform and not reason:
        params["social_connected"] = str(platform)
    if reason:
        params["social_error"] = str(platform or "social")
        params["social_reason"] = str(reason)[:240]
    query = urlencode(params)
    base = f"{settings.site_url}/video"
    return f"{base}?{query}#/social" if query else f"{base}#/social"


def _encode_oauth_state(payload: dict) -> str:
    data = {
        "u": int(payload.get("u", 0) or 0),
        "n": str(payload.get("n", "") or "").strip()[:255],
        "iat": int(time.time()),
        # Random nonce avoids predictable/reusable state values.
        "nonce": secrets.token_urlsafe(16),
    }
    if payload.get("tk"):
        data["tk"] = str(payload.get("tk") or "")
    if payload.get("ts"):
        data["ts"] = str(payload.get("ts") or "")

    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    payload_b64 = _b64url_encode(raw)
    return f"{payload_b64}.{_state_signature(payload_b64)}"


def _decode_oauth_state(state: str) -> dict:
    """Decode and validate signed state. Returns u(user_id), n(label), tk, ts."""
    if not state or "." not in state:
        return {"u": 0, "n": ""}

    payload_b64, signature = state.rsplit(".", 1)
    expected_sig = _state_signature(payload_b64)
    if not hmac.compare_digest(signature, expected_sig):
        return {"u": 0, "n": ""}

    try:
        decoded = _b64url_decode(payload_b64).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        return {"u": 0, "n": ""}

    issued_at = int(payload.get("iat", 0) or 0)
    if not issued_at or (int(time.time()) - issued_at) > STATE_MAX_AGE_SECONDS:
        return {"u": 0, "n": ""}

    return {
        "u": int(payload.get("u", 0) or 0),
        "n": str(payload.get("n", "") or "").strip()[:255],
        "tk": str(payload.get("tk", "") or ""),
        "ts": str(payload.get("ts", "") or ""),
    }


# ─── OAuth configuration (user must register apps on each platform) ───

YOUTUBE_OAUTH_CONFIG = {
    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube",
    "redirect_path": "/api/social/callback/youtube",
}

TIKTOK_OAUTH_CONFIG = {
    "auth_uri": "https://www.tiktok.com/v2/auth/authorize/",
    "token_uri": "https://open.tiktokapis.com/v2/oauth/token/",
    "scope": "user.info.basic,video.publish",
    "redirect_path": "/api/social/callback/tiktok",
}

INSTAGRAM_OAUTH_CONFIG = {
    "auth_uri": "https://www.facebook.com/v19.0/dialog/oauth",
    "token_uri": "https://graph.facebook.com/v19.0/oauth/access_token",
    "scope": "instagram_basic,instagram_content_publish,pages_read_engagement",
    "redirect_path": "/api/social/callback/instagram",
}


class UpdateAccountLabelRequest(BaseModel):
    account_label: str


@router.get("/accounts")
async def list_accounts(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all connected social accounts."""
    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.user_id == user["id"])
        .order_by(SocialAccount.connected_at.desc(), SocialAccount.id.desc())
    )
    accounts = result.scalars().all()
    return [
        {
            "id": a.id,
            "platform": a.platform.value,
            "account_label": a.account_label,
            "platform_username": a.platform_username,
            "publish_links": a.publish_links or "",
            "connected_at": a.connected_at.isoformat() if a.connected_at else None,
        }
        for a in accounts
    ]


@router.get("/connect/{platform}")
async def connect_platform(
    platform: str,
    account_label: str = "",
    client_key: str = "",
    client_secret: str = "",
    user: dict = Depends(get_current_user),
):
    """Initiate OAuth flow for a platform. Returns the authorization URL."""
    if platform not in ("youtube", "tiktok", "instagram"):
        raise HTTPException(status_code=400, detail="Invalid platform")

    redirect_uri = f"{settings.site_url}/api/social/callback/{platform}"

    # For TikTok, allow user-provided keys
    tiktok_key = settings.tiktok_client_key
    tiktok_secret = settings.tiktok_client_secret
    if platform == "tiktok" and client_key and client_secret:
        tiktok_key = client_key.strip()
        tiktok_secret = client_secret.strip()

    state_data = {"u": user["id"], "n": (account_label or "").strip()[:255]}
    if platform == "tiktok" and tiktok_key != settings.tiktok_client_key:
        state_data["tk"] = tiktok_key
        state_data["ts"] = tiktok_secret
    state_payload = _encode_oauth_state(state_data)

    if platform == "youtube":
        if not settings.google_oauth_client_id:
            raise HTTPException(status_code=500, detail="Google OAuth client_id não configurado no servidor")
        config = YOUTUBE_OAUTH_CONFIG
        query_params = {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": config["scope"],
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state_payload,
        }
        auth_url = f"{config['auth_uri']}?{urlencode(query_params)}"
    elif platform == "tiktok":
        if not tiktok_key:
            raise HTTPException(status_code=500, detail="Informe o Client Key e Client Secret do TikTok")
        config = TIKTOK_OAUTH_CONFIG
        query_params = {
            "client_key": tiktok_key,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": config["scope"],
            "state": state_payload,
        }
        auth_url = f"{config['auth_uri']}?{urlencode(query_params)}"
    elif platform == "instagram":
        missing_keys = []
        if not settings.facebook_app_id:
            missing_keys.append("FACEBOOK_APP_ID")
        if not settings.facebook_app_secret:
            missing_keys.append("FACEBOOK_APP_SECRET")
        if missing_keys:
            missing_text = ", ".join(missing_keys)
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Instagram OAuth nao configurado no servidor ({missing_text}). "
                    "Configure essas variaveis no .env e execute o deploy."
                ),
            )
        config = INSTAGRAM_OAUTH_CONFIG
        query_params = {
            "client_id": settings.facebook_app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": config["scope"],
            "state": state_payload,
        }
        auth_url = f"{config['auth_uri']}?{urlencode(query_params)}"

    return {"auth_url": auth_url}


@router.get("/callback/{platform}")
async def oauth_callback(
    platform: str,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth callback — exchange code for tokens and store."""
    if platform not in ("youtube", "tiktok", "instagram"):
        raise HTTPException(status_code=400, detail="Invalid platform")

    if error:
        reason = (error_description or error or "Autorizacao cancelada").strip()
        return RedirectResponse(url=_build_social_redirect(platform=platform, reason=reason))

    if not code:
        return RedirectResponse(
            url=_build_social_redirect(
                platform=platform,
                reason="Fluxo OAuth invalido: codigo de autorizacao ausente. Inicie a conexao novamente.",
            )
        )

    state_data = _decode_oauth_state(state)
    user_id = state_data["u"]
    requested_label = state_data["n"]
    if not user_id:
        return RedirectResponse(
            url=_build_social_redirect(
                platform=platform,
                reason="State OAuth invalido ou expirado. Tente conectar a conta novamente.",
            )
        )

    # For TikTok, use user-provided keys from state if available
    tiktok_key = state_data.get("tk") or settings.tiktok_client_key
    tiktok_secret = state_data.get("ts") or settings.tiktok_client_secret

    redirect_uri = f"{settings.site_url}/api/social/callback/{platform}"

    import httpx
    async with httpx.AsyncClient() as client:
        if platform == "youtube":
            resp = await client.post(YOUTUBE_OAUTH_CONFIG["token_uri"], data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
        elif platform == "tiktok":
            resp = await client.post(TIKTOK_OAUTH_CONFIG["token_uri"], json={
                "client_key": tiktok_key,
                "client_secret": tiktok_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            })
        elif platform == "instagram":
            if not settings.facebook_app_id or not settings.facebook_app_secret:
                raise HTTPException(
                    status_code=500,
                    detail="Instagram OAuth nao configurado no servidor (FACEBOOK_APP_ID/FACEBOOK_APP_SECRET)",
                )
            resp = await client.get(INSTAGRAM_OAUTH_CONFIG["token_uri"], params={
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            })
        else:
            raise HTTPException(status_code=400, detail="Invalid platform")

        if resp.status_code != 200:
            return RedirectResponse(
                url=_build_social_redirect(
                    platform=platform,
                    reason=f"Falha na troca de token ({resp.status_code}).",
                )
            )

        token_data = resp.json()

    # Save to database
    platform_enum = Platform(platform)
    platform_username = (
        token_data.get("username")
        or token_data.get("user_name")
        or token_data.get("name")
        or ""
    )
    account_label = (requested_label or "").strip()
    if not account_label:
        account_label = str(platform_username or "").strip()
    if not account_label:
        account_label = f"{platform.capitalize()} {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"

    # Store user-provided TikTok keys in extra_data for future token refresh
    extra = dict(token_data)
    if platform == "tiktok" and tiktok_key and tiktok_key != settings.tiktok_client_key:
        extra["user_client_key"] = tiktok_key
        extra["user_client_secret"] = tiktok_secret

    account = SocialAccount(
        user_id=user_id,
        platform=platform_enum,
        account_label=account_label,
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token", ""),
        platform_user_id=token_data.get("open_id", ""),
        platform_username=platform_username,
        extra_data=extra,
    )
    db.add(account)
    await db.commit()

    # Redirect to dashboard
    return RedirectResponse(url=_build_social_redirect(platform=platform))


@router.delete("/accounts/{account_id}")
async def disconnect_account(
    account_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a social account."""
    account = await db.get(SocialAccount, account_id)
    if not account or account.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Account not found")

    await db.delete(account)
    await db.commit()
    return {"deleted": True}


@router.patch("/accounts/{account_id}")
async def update_account_label(
    account_id: int,
    req: UpdateAccountLabelRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update custom label for a connected social account."""
    account = await db.get(SocialAccount, account_id)
    if not account or account.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Account not found")

    label = (req.account_label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Nome da conta nao pode ficar vazio")
    if len(label) > 255:
        raise HTTPException(status_code=400, detail="Nome da conta muito longo (maximo 255 caracteres)")

    account.account_label = label
    await db.commit()
    await db.refresh(account)

    return {
        "id": account.id,
        "platform": account.platform.value,
        "account_label": account.account_label,
        "platform_username": account.platform_username,
    }
