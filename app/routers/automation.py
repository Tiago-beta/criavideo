"""
Automation Router — CRUD for auto-schedules (automated video creation + publishing).
"""
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional, Union
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, String
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
import httpx

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import AppUser, AutoChannelPilot, AutoSchedule, AutoScheduleTheme, Platform, SocialAccount
from app.services.persona_image import normalize_persona_type
from app.services.credit_pricing import estimate_auto_theme_credits
from app.services.pilot_schedule import PILOT_TOTAL_SHORTS_PER_CYCLE

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/automation", tags=["automation"])


def _tevoxi_base_url() -> str:
    return str(settings.tevoxi_api_url or "https://levita.pro").strip().rstrip("/")


def _tevoxi_signup_url() -> str:
    configured = str(settings.tevoxi_signup_url or "").strip()
    if configured:
        return configured.rstrip("/")
    return "https://tevoxi.com"


def _tevoxi_error_detail(code: str, message: str) -> dict:
    return {
        "code": str(code or "tevoxi_error"),
        "message": str(message or "Erro ao validar conta Tevoxi."),
        "signup_url": _tevoxi_signup_url(),
    }


async def _resolve_tevoxi_token_for_user(user: dict, db: AsyncSession) -> tuple[str, str]:
    if not settings.tevoxi_jwt_secret:
        raise HTTPException(
            status_code=500,
            detail=_tevoxi_error_detail(
                "tevoxi_not_configured",
                "Integração do Tevoxi não configurada no servidor.",
            ),
        )

    raw_user_id = user.get("id")
    try:
        app_user_id = int(raw_user_id or 0)
    except (TypeError, ValueError):
        app_user_id = 0

    app_user = await db.get(AppUser, app_user_id) if app_user_id > 0 else None
    if not app_user or not app_user.is_active:
        raise HTTPException(status_code=401, detail="Usuário inválido.")

    source = str(app_user.auth_source or "").strip().lower()
    external_user_id = str(app_user.external_user_id or "").strip()
    if source != "levita" or not external_user_id:
        raise HTTPException(
            status_code=409,
            detail=_tevoxi_error_detail(
                "tevoxi_account_required",
                "Conecte sua conta Tevoxi para acessar suas músicas.",
            ),
        )

    try:
        tevoxi_user_id = int(external_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=_tevoxi_error_detail(
                "tevoxi_account_required",
                "Conecte sua conta Tevoxi para acessar suas músicas.",
            ),
        ) from exc

    if tevoxi_user_id <= 0:
        raise HTTPException(
            status_code=409,
            detail=_tevoxi_error_detail(
                "tevoxi_account_required",
                "Conecte sua conta Tevoxi para acessar suas músicas.",
            ),
        )

    email = str(app_user.email or user.get("email") or "").strip().lower()
    if not email:
        email = f"user-{tevoxi_user_id}@tevoxi.local"

    from jose import jwt as jose_jwt

    now = int(time.time())
    payload = {
        "id": tevoxi_user_id,
        "email": email,
        "role": "user",
        "iat": now,
        "exp": now + 3600,
    }
    token = jose_jwt.encode(payload, settings.tevoxi_jwt_secret, algorithm="HS256")
    return token, email


async def _probe_tevoxi_account(token: str) -> tuple[bool, str, str]:
    api_url = _tevoxi_base_url()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{api_url}/api/auth/me", headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Tevoxi status probe failed: %s", exc)
        return False, "tevoxi_unavailable", "Tevoxi indisponível no momento. Tente novamente."

    if resp.status_code == 200:
        return True, "ready", "Conta Tevoxi conectada."

    if resp.status_code in (401, 403, 404):
        return (
            False,
            "tevoxi_account_required",
            "Conta Tevoxi não encontrada para este usuário.",
        )

    logger.warning("Tevoxi status probe unexpected status: %s", resp.status_code)
    return False, "tevoxi_unavailable", "Não foi possível validar sua conta no Tevoxi agora."


def _local_to_utc(time_local: str, tz_name: str) -> str:
    """Convert HH:MM from user timezone to UTC."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return time_local  # fallback: treat as UTC
    h, m = map(int, time_local.split(":"))
    today = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)
    utc_time = today.astimezone(ZoneInfo("UTC"))
    return utc_time.strftime("%H:%M")


def _utc_to_local(time_utc: str, tz_name: str) -> str:
    """Convert HH:MM from UTC to user timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return time_utc
    h, m = map(int, time_utc.split(":"))
    today = datetime.now(ZoneInfo("UTC")).replace(hour=h, minute=m, second=0, microsecond=0)
    local_time = today.astimezone(tz)
    return local_time.strftime("%H:%M")


# ── Request / Response schemas ──

class CreateAutoScheduleRequest(BaseModel):
    name: str
    video_type: str = "narration"  # "narration" | "music"
    creation_mode: str = "auto"  # "auto" | "manual"
    platform: str = "youtube"
    social_account_id: Optional[int] = None
    frequency: str = "daily"
    time_local: str = "14:00"
    timezone: str = "UTC"
    day_of_week: Optional[int] = None
    default_settings: Optional[dict] = Field(default=None)
    themes: list[Union[str, dict]] = Field(default_factory=list)


class UpdateAutoScheduleRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    frequency: Optional[str] = None
    time_local: Optional[str] = None
    timezone: Optional[str] = None
    day_of_week: Optional[int] = None
    platform: Optional[str] = None
    social_account_id: Optional[int] = None
    default_settings: Optional[dict] = None


class AddThemesRequest(BaseModel):
    themes: list[Union[str, dict]]


class ReorderThemesRequest(BaseModel):
    theme_ids: list[int]


class UpdateThemeRequest(BaseModel):
    scheduled_date: Optional[str] = None  # YYYY-MM-DD (or DD/MM/YYYY)


class ToggleChannelPilotRequest(BaseModel):
    enabled: bool
    analysis_interval_hours: Optional[int] = None
    min_pending_themes: Optional[int] = None
    themes_per_cycle: Optional[int] = None
    channel_mode: Optional[str] = None
    short_mix_mode: Optional[str] = None
    shorts_per_cycle: Optional[int] = None
    interaction_persona: Optional[str] = None
    persona_profile_id: Optional[int] = None
    persona_profile_ids: Optional[list[int]] = None
    pilot_persona_types: Optional[list[str]] = None
    pilot_persona_candidates: Optional[list[dict]] = None


# ── Helpers ──

def _normalize_pilot_persona_candidates(
    persona_types: Optional[list[str]] = None,
    persona_candidates: Optional[list[dict]] = None,
) -> list[dict]:
    candidates: list[dict] = []
    seen = set()

    def _add_candidate(
        persona_type: str,
        profile_id: int = 0,
        profile_ids: list[int] | None = None,
        disable_persona_reference: bool = False,
    ):
        try:
            normalized_type = normalize_persona_type(str(persona_type or "").strip().lower())
        except Exception:
            return
        if not normalized_type:
            return
        ids = []
        for raw in (profile_ids or []):
            try:
                pid = int(raw)
            except Exception:
                continue
            if pid > 0 and pid not in ids:
                ids.append(pid)
        if profile_id > 0 and profile_id not in ids:
            ids.insert(0, int(profile_id))
        disable_ref = bool(disable_persona_reference)
        key = f"{normalized_type}:{','.join(str(pid) for pid in ids)}:{1 if disable_ref else 0}"
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "persona_type": normalized_type,
                "persona_profile_id": ids[0] if ids else 0,
                "persona_profile_ids": ids,
                "disable_persona_reference": disable_ref,
            }
        )

    for item in persona_candidates or []:
        if not isinstance(item, dict):
            continue
        raw_profile_id = item.get("persona_profile_id") or item.get("profile_id") or 0
        try:
            profile_id = int(raw_profile_id or 0)
        except Exception:
            profile_id = 0
        raw_ids = item.get("persona_profile_ids") or item.get("profile_ids") or []
        disable_ref = bool(item.get("disable_persona_reference") or item.get("grok_text_only"))
        _add_candidate(
            item.get("persona_type") or item.get("type") or item.get("interaction_persona") or "",
            profile_id=profile_id,
            profile_ids=raw_ids if isinstance(raw_ids, list) else [],
            disable_persona_reference=disable_ref,
        )

    for persona_type in persona_types or []:
        _add_candidate(persona_type)

    return candidates[:8]


def _build_pilot_persona_experiment(candidates: list[dict]) -> dict:
    return {
        "enabled": bool(candidates),
        "phase": "explore" if candidates else "off",
        "scope": ["shorts", "title", "description", "thumbnail"],
        "candidates": candidates,
        "winner": None,
        "selection_reason": "primeira_rodada_testa_todas_as_personas",
    }


def _parse_schedule_date_value(raw_value: str) -> Optional[date]:
    value = str(raw_value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _get_theme_date_override(theme: AutoScheduleTheme) -> Optional[date]:
    custom = theme.custom_settings if isinstance(theme.custom_settings, dict) else {}
    raw_date = custom.get("scheduled_date_override")
    if raw_date is None:
        return None
    return _parse_schedule_date_value(str(raw_date))


def _get_theme_credit_estimate(schedule: AutoSchedule, theme: AutoScheduleTheme) -> dict:
    default_settings = schedule.default_settings if isinstance(schedule.default_settings, dict) else {}
    custom_settings = theme.custom_settings if isinstance(theme.custom_settings, dict) else {}
    try:
        estimate = estimate_auto_theme_credits(
            video_type=schedule.video_type,
            default_settings=default_settings,
            custom_settings=custom_settings,
        )
        credits = int(estimate.get("credits_needed", 0) or 0)
        billed_cost_brl = round(float(estimate.get("billed_cost_brl", 0.0) or 0.0), 2)
        return {
            "estimated_credits": credits,
            "estimated_cost_brl": billed_cost_brl,
            "estimated_pricing_version": str(estimate.get("rules_version") or ""),
        }
    except Exception:
        return {
            "estimated_credits": 0,
            "estimated_cost_brl": 0.0,
            "estimated_pricing_version": "",
        }


def _schedule_to_dict(s: AutoSchedule, theme_count: int = 0) -> dict:
    account_label = ""
    if s.social_account:
        account_label = s.social_account.account_label or s.social_account.platform_username or ""
    elif not s.social_account_id:
        account_label = "Conta de teste (sem publicacao)"
    next_theme = ""
    pending_count = 0
    pending_estimated_credits = 0
    pending_estimated_cost_brl = 0.0
    themes_with_dates = []
    if s.themes:
        all_sorted = sorted(s.themes, key=lambda t: t.position)
        pending = [t for t in all_sorted if t.status == "pending"]
        pending_count = len(pending)
        if pending:
            next_theme = pending[0].theme

        # Calculate scheduled dates for pending themes
        tz_name = s.timezone or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")

        now_local = datetime.now(tz)
        h, mn = 14, 0
        try:
            h, mn = map(int, (s.time_utc or "14:00").split(":"))
        except Exception:
            pass
        # Convert to local time for display
        utc_ref = datetime.now(ZoneInfo("UTC")).replace(hour=h, minute=mn, second=0, microsecond=0)
        local_ref = utc_ref.astimezone(tz)
        local_h, local_m = local_ref.hour, local_ref.minute

        active_weekdays = None
        if isinstance(s.default_settings, dict):
            active_weekdays = s.default_settings.get("active_weekdays")
        allowed_days = set()
        if s.frequency == "daily" and active_weekdays:
            try:
                allowed_days = {int(day) for day in active_weekdays}
            except Exception:
                allowed_days = set()

        # Find next run date in local time
        next_run = now_local.replace(hour=local_h, minute=local_m, second=0, microsecond=0)
        if next_run <= now_local:
            if s.frequency == "weekly":
                next_run += timedelta(days=7)
            else:
                next_run += timedelta(days=1)
        if s.frequency == "weekly" and s.day_of_week is not None:
            while next_run.weekday() != s.day_of_week:
                next_run += timedelta(days=1)
        elif s.frequency == "daily" and allowed_days:
            while next_run.weekday() not in allowed_days:
                next_run += timedelta(days=1)

        next_pending_run = next_run
        for t in all_sorted:
            td = _theme_to_dict(t, s)
            if t.status == "pending":
                pending_estimated_credits += int(td.get("estimated_credits", 0) or 0)
                pending_estimated_cost_brl += float(td.get("estimated_cost_brl", 0.0) or 0.0)
                override_date = _get_theme_date_override(t)
                if override_date:
                    td["scheduled_date"] = override_date.strftime("%d/%m/%Y")
                    td["scheduled_date_iso"] = override_date.isoformat()
                    td["scheduled_date_overridden"] = True
                else:
                    scheduled = next_pending_run
                    td["scheduled_date"] = scheduled.strftime("%d/%m/%Y")
                    td["scheduled_date_iso"] = scheduled.date().isoformat()
                    td["scheduled_date_overridden"] = False
                    if s.frequency == "weekly":
                        next_pending_run = scheduled + timedelta(weeks=1)
                    else:
                        next_pending_run = scheduled + timedelta(days=1)
                        if allowed_days:
                            while next_pending_run.weekday() not in allowed_days:
                                next_pending_run += timedelta(days=1)
            else:
                td["scheduled_date"] = None
                td["scheduled_date_iso"] = None
                td["scheduled_date_overridden"] = False
            themes_with_dates.append(td)
    return {
        "id": s.id,
        "name": s.name,
        "video_type": s.video_type,
        "creation_mode": s.creation_mode,
        "platform": s.platform,
        "social_account_id": s.social_account_id,
        "account_label": account_label,
        "frequency": s.frequency,
        "time_utc": s.time_utc,
        "time_local": _utc_to_local(s.time_utc, s.timezone or "UTC"),
        "timezone": s.timezone or "UTC",
        "day_of_week": s.day_of_week,
        "default_settings": s.default_settings or {},
        "is_active": s.is_active,
        "themes": themes_with_dates,
        "theme_count": theme_count or len(s.themes) if s.themes else 0,
        "pending_count": pending_count,
        "pending_estimated_credits": pending_estimated_credits,
        "pending_estimated_cost_brl": round(pending_estimated_cost_brl, 2),
        "next_theme": next_theme,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _theme_to_dict(t: AutoScheduleTheme, schedule: AutoSchedule | None = None) -> dict:
    payload = {
        "id": t.id,
        "theme": t.theme,
        "status": t.status,
        "position": t.position,
        "custom_settings": t.custom_settings,
        "video_project_id": t.video_project_id,
        "error_message": t.error_message,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if schedule is not None:
        payload.update(_get_theme_credit_estimate(schedule, t))
    return payload


def _pilot_summary_dict(pilot: AutoChannelPilot | None) -> dict:
    if not pilot:
        return {
            "enabled": False,
            "channel_mode": "auto",
            "short_mix_mode": "realistic_all",
            "shorts_per_cycle": PILOT_TOTAL_SHORTS_PER_CYCLE,
            "analysis_interval_hours": 24,
            "min_pending_themes": 5,
            "themes_per_cycle": 4,
            "last_analysis_at": None,
            "last_run_at": None,
            "last_error": None,
            "last_summary": {},
            "schedule_id": None,
            "long_schedule_id": None,
            "shorts_schedule_id": None,
            "pilot_persona_experiment": {},
        }

    return {
        "enabled": bool(pilot.is_enabled),
        "channel_mode": str(pilot.channel_mode or "auto"),
        "short_mix_mode": str(pilot.short_mix_mode or "realistic_all"),
        "shorts_per_cycle": int(pilot.shorts_per_cycle or PILOT_TOTAL_SHORTS_PER_CYCLE),
        "analysis_interval_hours": int(pilot.analysis_interval_hours or 24),
        "min_pending_themes": int(pilot.min_pending_themes or 5),
        "themes_per_cycle": int(pilot.themes_per_cycle or 4),
        "last_analysis_at": pilot.last_analysis_at.isoformat() if pilot.last_analysis_at else None,
        "last_run_at": pilot.last_run_at.isoformat() if pilot.last_run_at else None,
        "last_error": pilot.last_error,
        "last_summary": pilot.last_summary or {},
        "schedule_id": pilot.auto_schedule_id,
        "long_schedule_id": pilot.long_schedule_id,
        "shorts_schedule_id": pilot.shorts_schedule_id,
        "pilot_persona_experiment": (pilot.last_summary or {}).get("pilot_persona_experiment", {}),
    }


# ── Endpoints ──

@router.get("/tevoxi-account-status")
async def tevoxi_account_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return whether current user has an active Tevoxi account link."""
    try:
        token, tevoxi_email = await _resolve_tevoxi_token_for_user(user, db)
    except HTTPException as exc:
        if exc.status_code in (409, 500):
            detail = exc.detail if isinstance(exc.detail, dict) else {
                "code": "tevoxi_account_required",
                "message": str(exc.detail or "Conta Tevoxi não conectada."),
            }
            return {
                "connected": False,
                "reason": str(detail.get("code") or "tevoxi_account_required"),
                "message": str(detail.get("message") or "Conta Tevoxi não conectada."),
                "signup_url": str(detail.get("signup_url") or _tevoxi_signup_url()),
            }
        raise

    connected, reason, message = await _probe_tevoxi_account(token)
    return {
        "connected": bool(connected),
        "reason": str(reason),
        "message": str(message),
        "signup_url": _tevoxi_signup_url(),
        "tevoxi_email": tevoxi_email if connected else "",
    }

@router.get("/tevoxi-songs")
async def list_tevoxi_songs(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch user's songs from Tevoxi/Levita."""
    token, _tevoxi_email = await _resolve_tevoxi_token_for_user(user, db)

    api_url = _tevoxi_base_url()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{api_url}/api/feed/my-created-music", headers=headers)
            if resp.status_code in (401, 403, 404):
                raise HTTPException(
                    status_code=409,
                    detail=_tevoxi_error_detail(
                        "tevoxi_account_required",
                        "Conta Tevoxi não conectada para este usuário.",
                    ),
                )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Erro ao buscar músicas do Tevoxi.")
            data = resp.json()
            songs = data.get("songs", data) if isinstance(data, dict) else data
            # Return simplified list
            return [
                {
                    "job_id": s.get("job_id", ""),
                    "title": s.get("title", "Sem título"),
                    "duration": s.get("duration", 0),
                    "audio_url": f"{api_url}{s['audio_url']}" if s.get("audio_url", "").startswith("/") else s.get("audio_url", ""),
                    "lyrics": s.get("lyrics", ""),
                    "genres": s.get("genres", []),
                    "created_at": s.get("created_at", ""),
                }
                for s in (songs if isinstance(songs, list) else [])
                if s.get("job_id")
            ]
    except httpx.HTTPError as e:
        logger.warning("Failed to fetch Tevoxi songs: %s", e)
        raise HTTPException(status_code=502, detail="Erro de conexão com Tevoxi.")


@router.get("/tevoxi-audio/{job_id}")
async def proxy_tevoxi_audio(
    job_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Proxy Tevoxi audio through this backend to avoid browser CORS/auth issues."""
    token, _tevoxi_email = await _resolve_tevoxi_token_for_user(user, db)
    if not job_id or not job_id.strip():
        raise HTTPException(status_code=400, detail="job_id inválido.")

    api_url = _tevoxi_base_url()
    headers = {"Authorization": f"Bearer {token}"}
    audio_url = f"{api_url}/api/create-music/audio/{job_id.strip()}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(audio_url, headers=headers)
            if resp.status_code in (401, 403, 404):
                raise HTTPException(
                    status_code=409,
                    detail=_tevoxi_error_detail(
                        "tevoxi_account_required",
                        "Conta Tevoxi não conectada para este usuário.",
                    ),
                )
            if resp.status_code != 200:
                logger.warning("Tevoxi audio proxy failed for %s with status %s", job_id, resp.status_code)
                raise HTTPException(status_code=502, detail="Erro ao buscar áudio do Tevoxi.")

            media_type = resp.headers.get("content-type", "audio/mpeg")
            return StreamingResponse(
                iter([resp.content]),
                media_type=media_type,
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )
    except httpx.HTTPError as e:
        logger.warning("Tevoxi audio proxy connection error for %s: %s", job_id, e)
        raise HTTPException(status_code=502, detail="Erro de conexão com Tevoxi.")


@router.post("/schedules")
async def create_auto_schedule(
    req: CreateAutoScheduleRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Nome da automação é obrigatório.")

    if req.video_type not in ("narration", "music", "musical_shorts", "realistic"):
        raise HTTPException(status_code=400, detail="Tipo de vídeo inválido.")
    if req.creation_mode not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="Modo de criação inválido.")
    if req.frequency not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="Frequência inválida.")

    # Normalize special/empty values: no social account means "test account" mode.
    social_account_id = req.social_account_id if (req.social_account_id and req.social_account_id > 0) else None

    # Validate social account if provided
    if social_account_id is not None:
        acct = await db.get(SocialAccount, social_account_id)
        if not acct or acct.user_id != user["id"]:
            raise HTTPException(status_code=400, detail="Conta social não encontrada.")

    schedule = AutoSchedule(
        user_id=user["id"],
        name=req.name.strip(),
        video_type=req.video_type,
        creation_mode=req.creation_mode,
        platform=req.platform,
        social_account_id=social_account_id,
        frequency=req.frequency,
        time_utc=_local_to_utc(req.time_local, req.timezone),
        timezone=req.timezone,
        day_of_week=req.day_of_week,
        default_settings=req.default_settings,
        is_active=True,
    )
    db.add(schedule)
    await db.flush()

    # Add initial themes
    for i, raw_theme in enumerate(req.themes):
        if isinstance(raw_theme, dict):
            theme_text = (raw_theme.get("text") or "").strip()
            custom_settings = raw_theme.get("custom_settings")
        else:
            theme_text = (raw_theme or "").strip()
            custom_settings = None
        if not theme_text:
            continue
        theme = AutoScheduleTheme(
            auto_schedule_id=schedule.id,
            theme=theme_text,
            position=i,
            status="pending",
            custom_settings=custom_settings,
        )
        db.add(theme)

    await db.commit()
    await db.refresh(schedule, ["themes", "social_account"])

    return _schedule_to_dict(schedule)


@router.get("/schedules")
async def list_auto_schedules(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AutoSchedule)
        .options(selectinload(AutoSchedule.themes), selectinload(AutoSchedule.social_account))
        .where(AutoSchedule.user_id == user["id"])
        .order_by(AutoSchedule.created_at.desc())
    )
    schedules = result.scalars().all()
    return [_schedule_to_dict(s) for s in schedules]


@router.get("/pilot/channels")
async def list_pilot_channels(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    accounts_result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.user_id == user["id"])
        .where(func.lower(cast(SocialAccount.platform, String)) == "youtube")
        .order_by(SocialAccount.connected_at.desc(), SocialAccount.id.desc())
    )
    accounts = accounts_result.scalars().all()
    if not accounts:
        return []

    account_ids = [account.id for account in accounts]

    pilots_result = await db.execute(
        select(AutoChannelPilot)
        .where(AutoChannelPilot.user_id == user["id"])
        .where(AutoChannelPilot.social_account_id.in_(account_ids))
    )
    pilots = pilots_result.scalars().all()
    pilot_by_account = {pilot.social_account_id: pilot for pilot in pilots}

    schedule_ids_set = set()
    for pilot in pilots:
        if pilot.auto_schedule_id:
            schedule_ids_set.add(int(pilot.auto_schedule_id))
        if pilot.long_schedule_id:
            schedule_ids_set.add(int(pilot.long_schedule_id))
        if pilot.shorts_schedule_id:
            schedule_ids_set.add(int(pilot.shorts_schedule_id))

    schedule_ids = list(schedule_ids_set)
    schedules_by_id = {}
    if schedule_ids:
        schedule_result = await db.execute(
            select(AutoSchedule)
            .where(AutoSchedule.user_id == user["id"])
            .where(AutoSchedule.id.in_(schedule_ids))
        )
        schedules = schedule_result.scalars().all()
        schedules_by_id = {schedule.id: schedule for schedule in schedules}

    counts_by_schedule: dict[int, dict[str, int]] = {}
    if schedule_ids:
        counts_result = await db.execute(
            select(AutoScheduleTheme.auto_schedule_id, AutoScheduleTheme.status, func.count(AutoScheduleTheme.id))
            .where(AutoScheduleTheme.auto_schedule_id.in_(schedule_ids))
            .group_by(AutoScheduleTheme.auto_schedule_id, AutoScheduleTheme.status)
        )
        for schedule_id, status, qty in counts_result.all():
            entry = counts_by_schedule.setdefault(int(schedule_id), {"pending": 0, "completed": 0})
            status_key = str(status or "").lower()
            if status_key == "pending":
                entry["pending"] += int(qty or 0)
            if status_key in ("completed", "done"):
                entry["completed"] += int(qty or 0)

    payload = []
    for account in accounts:
        pilot = pilot_by_account.get(account.id)
        pilot_data = _pilot_summary_dict(pilot)

        long_schedule = schedules_by_id.get(
            pilot_data.get("long_schedule_id") or pilot_data.get("schedule_id") or 0
        )
        short_schedule = schedules_by_id.get(pilot_data.get("shorts_schedule_id") or 0)

        sched_defaults = {}
        if long_schedule and isinstance(long_schedule.default_settings, dict):
            sched_defaults = long_schedule.default_settings
        pilot_data["interaction_persona"] = sched_defaults.get("interaction_persona", "")
        pilot_data["persona_profile_id"] = int(sched_defaults.get("persona_profile_id", 0) or 0)
        pilot_data["persona_profile_ids"] = sched_defaults.get("persona_profile_ids") or []
        pilot_data["disable_persona_reference"] = bool(sched_defaults.get("disable_persona_reference"))
        pilot_data["pilot_persona_experiment"] = sched_defaults.get("pilot_persona_experiment") or pilot_data.get("pilot_persona_experiment") or {}

        long_counts = counts_by_schedule.get(long_schedule.id if long_schedule else 0, {"pending": 0, "completed": 0})
        short_counts = counts_by_schedule.get(short_schedule.id if short_schedule else 0, {"pending": 0, "completed": 0})

        pending_total = int(long_counts.get("pending", 0)) + int(short_counts.get("pending", 0))
        completed_total = int(long_counts.get("completed", 0)) + int(short_counts.get("completed", 0))

        payload.append(
            {
                "social_account_id": account.id,
                "platform": "youtube",
                "account_label": account.account_label or account.platform_username or "Canal YouTube",
                "platform_username": account.platform_username or "",
                "connected_at": account.connected_at.isoformat() if account.connected_at else None,
                "pilot": {
                    **pilot_data,
                    "schedule_name": long_schedule.name if long_schedule else None,
                    "schedule_is_active": bool(long_schedule.is_active) if long_schedule else False,
                    "long_schedule_name": long_schedule.name if long_schedule else None,
                    "shorts_schedule_name": short_schedule.name if short_schedule else None,
                    "long_schedule_is_active": bool(long_schedule.is_active) if long_schedule else False,
                    "shorts_schedule_is_active": bool(short_schedule.is_active) if short_schedule else False,
                    "pending_themes": pending_total,
                    "completed_themes": completed_total,
                    "pending_themes_long": int(long_counts.get("pending", 0)),
                    "completed_themes_long": int(long_counts.get("completed", 0)),
                    "pending_themes_shorts": int(short_counts.get("pending", 0)),
                    "completed_themes_shorts": int(short_counts.get("completed", 0)),
                },
            }
        )

    return payload


@router.patch("/pilot/channels/{social_account_id}")
async def toggle_pilot_channel(
    social_account_id: int,
    req: ToggleChannelPilotRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await db.get(SocialAccount, social_account_id)
    if not account or account.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Conta social nao encontrada.")

    account_platform = account.platform.value if isinstance(account.platform, Platform) else str(account.platform or "")
    if str(account_platform).strip().lower() != "youtube":
        raise HTTPException(status_code=400, detail="Piloto automatico disponivel apenas para YouTube.")

    pilot_result = await db.execute(
        select(AutoChannelPilot)
        .where(AutoChannelPilot.user_id == user["id"])
        .where(AutoChannelPilot.social_account_id == social_account_id)
        .limit(1)
    )
    pilot = pilot_result.scalar_one_or_none()

    if not pilot:
        pilot = AutoChannelPilot(
            user_id=user["id"],
            social_account_id=social_account_id,
            is_enabled=bool(req.enabled),
            channel_mode="auto",
            short_mix_mode="realistic_all",
            shorts_per_cycle=PILOT_TOTAL_SHORTS_PER_CYCLE,
            analysis_interval_hours=24,
            min_pending_themes=5,
            themes_per_cycle=4,
        )
        db.add(pilot)
        await db.flush()

    pilot.is_enabled = bool(req.enabled)

    if req.analysis_interval_hours is not None:
        pilot.analysis_interval_hours = max(1, min(168, int(req.analysis_interval_hours)))
    if req.min_pending_themes is not None:
        pilot.min_pending_themes = max(1, min(20, int(req.min_pending_themes)))
    if req.themes_per_cycle is not None:
        pilot.themes_per_cycle = max(1, min(20, int(req.themes_per_cycle)))
    if req.channel_mode is not None:
        mode = str(req.channel_mode or "").strip().lower()
        pilot.channel_mode = mode if mode in {"auto", "music", "general"} else "auto"
    if req.short_mix_mode is not None:
        mix_mode = str(req.short_mix_mode or "").strip().lower()
        pilot.short_mix_mode = (
            mix_mode
            if mix_mode in {"realistic_all", "image_all", "mixed_realistic2_image1"}
            else "realistic_all"
        )
    pilot.shorts_per_cycle = PILOT_TOTAL_SHORTS_PER_CYCLE

    experiment_candidates: list[dict] | None = None
    if req.pilot_persona_types is not None or req.pilot_persona_candidates is not None:
        experiment_candidates = _normalize_pilot_persona_candidates(
            persona_types=req.pilot_persona_types,
            persona_candidates=req.pilot_persona_candidates,
        )

    if (
        req.interaction_persona is not None
        or req.persona_profile_id is not None
        or req.persona_profile_ids is not None
        or experiment_candidates is not None
    ):
        persona_patch = {}
        if experiment_candidates is not None:
            persona_patch["pilot_persona_experiment"] = _build_pilot_persona_experiment(experiment_candidates)
            if experiment_candidates:
                first_candidate = experiment_candidates[0]
                persona_patch["interaction_persona"] = first_candidate.get("persona_type", "")
                persona_patch["persona_profile_id"] = int(first_candidate.get("persona_profile_id", 0) or 0)
                persona_patch["persona_profile_ids"] = first_candidate.get("persona_profile_ids") or []
                disable_ref = bool(first_candidate.get("disable_persona_reference"))
                persona_patch["disable_persona_reference"] = disable_ref
                persona_patch["grok_text_only"] = disable_ref
        if req.interaction_persona is not None:
            persona_patch["interaction_persona"] = str(req.interaction_persona).strip().lower()
        if req.persona_profile_id is not None:
            persona_patch["persona_profile_id"] = int(req.persona_profile_id)
        if req.persona_profile_ids is not None:
            persona_patch["persona_profile_ids"] = [int(pid) for pid in req.persona_profile_ids if int(pid) > 0]
        for sid in [pilot.long_schedule_id, pilot.shorts_schedule_id]:
            if not sid:
                continue
            sched = await db.get(AutoSchedule, sid)
            if sched and sched.user_id == user["id"]:
                ds = dict(sched.default_settings or {})
                ds.update(persona_patch)
                sched.default_settings = ds
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(sched, "default_settings")

    schedule_ids_to_toggle = {
        int(sid)
        for sid in [pilot.auto_schedule_id, pilot.long_schedule_id, pilot.shorts_schedule_id]
        if sid
    }
    for schedule_id in schedule_ids_to_toggle:
        schedule = await db.get(AutoSchedule, schedule_id)
        if schedule and schedule.user_id == user["id"]:
            schedule.is_active = bool(req.enabled)

    if not req.enabled:
        pilot.last_error = None

    await db.commit()
    await db.refresh(pilot)

    if req.enabled:
        from app.tasks.auto_pilot_tasks import run_channel_pilot_cycle

        background_tasks.add_task(run_channel_pilot_cycle, pilot.id)

    return {
        "ok": True,
        "social_account_id": social_account_id,
        "pilot": _pilot_summary_dict(pilot),
    }


@router.get("/schedules/{schedule_id}")
async def get_auto_schedule(
    schedule_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AutoSchedule)
        .options(selectinload(AutoSchedule.themes), selectinload(AutoSchedule.social_account))
        .where(AutoSchedule.id == schedule_id, AutoSchedule.user_id == user["id"])
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")

    data = _schedule_to_dict(schedule)
    data["themes"] = sorted(
        [_theme_to_dict(t, schedule) for t in schedule.themes],
        key=lambda t: t["position"],
    )
    return data


@router.patch("/schedules/{schedule_id}")
async def update_auto_schedule(
    schedule_id: int,
    req: UpdateAutoScheduleRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(AutoSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")

    if req.name is not None:
        schedule.name = req.name.strip()
    if req.is_active is not None:
        schedule.is_active = req.is_active
    if req.frequency is not None:
        schedule.frequency = req.frequency
    if req.time_local is not None:
        tz = req.timezone or schedule.timezone or "UTC"
        schedule.time_utc = _local_to_utc(req.time_local, tz)
    if req.timezone is not None:
        schedule.timezone = req.timezone
        # Recalculate UTC if time_local was also sent
        if req.time_local is None:
            # Re-convert existing local time with new timezone
            local_time = _utc_to_local(schedule.time_utc, schedule.timezone)
            schedule.time_utc = _local_to_utc(local_time, req.timezone)
    if req.day_of_week is not None:
        schedule.day_of_week = req.day_of_week
    if req.platform is not None:
        schedule.platform = req.platform

    # Allow explicit clear (null) for test-account mode.
    fields_set = getattr(req, "model_fields_set", None) or getattr(req, "__fields_set__", set())
    if "social_account_id" in fields_set:
        if req.social_account_id and req.social_account_id > 0:
            acct = await db.get(SocialAccount, req.social_account_id)
            if not acct or acct.user_id != user["id"]:
                raise HTTPException(status_code=400, detail="Conta social não encontrada.")
            schedule.social_account_id = req.social_account_id
        else:
            schedule.social_account_id = None

    if req.default_settings is not None:
        schedule.default_settings = req.default_settings

    schedule.updated_at = datetime.utcnow()
    await db.commit()

    return {"ok": True, "is_active": schedule.is_active}


@router.delete("/schedules/{schedule_id}")
async def delete_auto_schedule(
    schedule_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(AutoSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")

    await db.delete(schedule)
    await db.commit()
    return {"ok": True}


@router.post("/schedules/{schedule_id}/themes")
async def add_themes(
    schedule_id: int,
    req: AddThemesRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(AutoSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")

    # Find max position
    result = await db.execute(
        select(func.max(AutoScheduleTheme.position))
        .where(AutoScheduleTheme.auto_schedule_id == schedule_id)
    )
    max_pos = result.scalar() or -1

    added = []
    for i, raw_theme in enumerate(req.themes):
        if isinstance(raw_theme, dict):
            theme_text = (raw_theme.get("text") or "").strip()
            custom_settings = raw_theme.get("custom_settings")
        else:
            theme_text = (raw_theme or "").strip()
            custom_settings = None
        if not theme_text:
            continue
        theme = AutoScheduleTheme(
            auto_schedule_id=schedule_id,
            theme=theme_text,
            position=max_pos + 1 + i,
            status="pending",
            custom_settings=custom_settings,
        )
        db.add(theme)
        added.append(theme)

    await db.commit()
    return {"ok": True, "added": len(added)}


@router.patch("/themes/{theme_id}")
async def update_theme(
    theme_id: int,
    req: UpdateThemeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AutoScheduleTheme)
        .join(AutoSchedule)
        .where(AutoScheduleTheme.id == theme_id, AutoSchedule.user_id == user["id"])
    )
    theme = result.scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail="Tema não encontrado.")

    fields_set = getattr(req, "model_fields_set", None) or getattr(req, "__fields_set__", set())
    if "scheduled_date" in fields_set:
        custom_settings = dict(theme.custom_settings or {}) if isinstance(theme.custom_settings, dict) else {}
        raw_date = (req.scheduled_date or "").strip()

        if not raw_date:
            custom_settings.pop("scheduled_date_override", None)
        else:
            parsed = _parse_schedule_date_value(raw_date)
            if not parsed:
                raise HTTPException(status_code=400, detail="Data inválida. Use YYYY-MM-DD.")
            custom_settings["scheduled_date_override"] = parsed.isoformat()

        theme.custom_settings = custom_settings or None

    await db.commit()
    return {"ok": True}


@router.delete("/themes/{theme_id}")
async def delete_theme(
    theme_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AutoScheduleTheme)
        .join(AutoSchedule)
        .where(AutoScheduleTheme.id == theme_id, AutoSchedule.user_id == user["id"])
    )
    theme = result.scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail="Tema não encontrado.")

    await db.delete(theme)
    await db.commit()
    return {"ok": True}


@router.post("/schedules/{schedule_id}/reorder")
async def reorder_themes(
    schedule_id: int,
    req: ReorderThemesRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(AutoSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")

    result = await db.execute(
        select(AutoScheduleTheme)
        .where(AutoScheduleTheme.auto_schedule_id == schedule_id)
    )
    themes_map = {t.id: t for t in result.scalars().all()}

    for i, tid in enumerate(req.theme_ids):
        if tid in themes_map:
            themes_map[tid].position = i

    await db.commit()
    return {"ok": True}

