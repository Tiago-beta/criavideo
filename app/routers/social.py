"""
Social Router — OAuth connection/disconnection for YouTube, TikTok, Instagram.
"""
import base64
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.auth import get_current_user
from app.database import get_db
from app.models import SocialAccount, Platform
from app.config import get_settings

router = APIRouter(prefix="/api/social", tags=["social"])
settings = get_settings()


def _encode_oauth_state(user_id: int, account_label: str = "") -> str:
    payload = {
        "u": int(user_id),
        "n": str(account_label or "").strip()[:255],
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_oauth_state(state: str) -> tuple[int, str]:
    if not state:
        return 0, ""
    if state.isdigit():
        return int(state), ""

    padded = state + ("=" * (-len(state) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        return 0, ""

    user_id = int(payload.get("u", 0) or 0)
    account_label = str(payload.get("n", "") or "").strip()[:255]
    return user_id, account_label


# ─── OAuth configuration (user must register apps on each platform) ───

YOUTUBE_OAUTH_CONFIG = {
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
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
            "connected_at": a.connected_at.isoformat() if a.connected_at else None,
        }
        for a in accounts
    ]


@router.get("/connect/{platform}")
async def connect_platform(
    platform: str,
    account_label: str = "",
    user: dict = Depends(get_current_user),
):
    """Initiate OAuth flow for a platform. Returns the authorization URL."""
    if platform not in ("youtube", "tiktok", "instagram"):
        raise HTTPException(status_code=400, detail="Invalid platform")

    redirect_uri = f"{settings.site_url}/api/social/callback/{platform}"
    state_payload = _encode_oauth_state(user["id"], account_label)

    if platform == "youtube":
        if not settings.google_oauth_client_id:
            raise HTTPException(status_code=500, detail="Google OAuth client_id não configurado no servidor")
        config = YOUTUBE_OAUTH_CONFIG
        auth_url = (
            f"{config['auth_uri']}?"
            f"client_id={settings.google_oauth_client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={config['scope']}"
            f"&access_type=offline"
            f"&prompt=consent"
            f"&state={state_payload}"
        )
    elif platform == "tiktok":
        if not settings.tiktok_client_key:
            raise HTTPException(status_code=500, detail="TikTok client_key não configurado no servidor")
        config = TIKTOK_OAUTH_CONFIG
        auth_url = (
            f"{config['auth_uri']}?"
            f"client_key={settings.tiktok_client_key}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={config['scope']}"
            f"&state={state_payload}"
        )
    elif platform == "instagram":
        if not settings.facebook_app_id:
            raise HTTPException(status_code=500, detail="Facebook app_id não configurado no servidor")
        config = INSTAGRAM_OAUTH_CONFIG
        auth_url = (
            f"{config['auth_uri']}?"
            f"client_id={settings.facebook_app_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={config['scope']}"
            f"&state={state_payload}"
        )

    return {"auth_url": auth_url}


@router.get("/callback/{platform}")
async def oauth_callback(
    platform: str,
    code: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth callback — exchange code for tokens and store."""
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    user_id, requested_label = _decode_oauth_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

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
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            })
        elif platform == "instagram":
            resp = await client.get(INSTAGRAM_OAUTH_CONFIG["token_uri"], params={
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            })
        else:
            raise HTTPException(status_code=400, detail="Invalid platform")

        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text[:200]}")

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

    account = SocialAccount(
        user_id=user_id,
        platform=platform_enum,
        account_label=account_label,
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token", ""),
        platform_user_id=token_data.get("open_id", ""),
        platform_username=platform_username,
        extra_data=token_data,
    )
    db.add(account)
    await db.commit()

    # Redirect to dashboard
    return RedirectResponse(url=f"{settings.site_url}/video/#/social?connected={platform}")


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
