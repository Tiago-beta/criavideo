"""
Automation Router — CRUD for auto-schedules (automated video creation + publishing).
"""
import logging
from datetime import datetime, timedelta
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
from app.models import AutoChannelPilot, AutoSchedule, AutoScheduleTheme, Platform, SocialAccount
from app.services.persona_image import normalize_persona_type

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/automation", tags=["automation"])


def _get_tevoxi_token() -> str:
    """Generate or return Tevoxi API token."""
    token = settings.tevoxi_api_token
    if not token and settings.tevoxi_jwt_secret:
        from jose import jwt as jose_jwt
        import time
        payload = {
            "id": settings.tevoxi_jwt_user_id,
            "email": settings.tevoxi_jwt_email,
            "role": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = jose_jwt.encode(payload, settings.tevoxi_jwt_secret, algorithm="HS256")
    return token


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

def _schedule_to_dict(s: AutoSchedule, theme_count: int = 0) -> dict:
    account_label = ""
    if s.social_account:
        account_label = s.social_account.account_label or s.social_account.platform_username or ""
    elif not s.social_account_id:
        account_label = "Conta de teste (sem publicacao)"
    next_theme = ""
    pending_count = 0
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
        from datetime import timezone as dt_tz
        utc_ref = datetime.now(ZoneInfo("UTC")).replace(hour=h, minute=mn, second=0, microsecond=0)
        local_ref = utc_ref.astimezone(tz)
        local_h, local_m = local_ref.hour, local_ref.minute

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

        pending_idx = 0
        for t in all_sorted:
            td = _theme_to_dict(t)
            if t.status == "pending":
                delta = timedelta(weeks=pending_idx) if s.frequency == "weekly" else timedelta(days=pending_idx)
                scheduled = next_run + delta
                td["scheduled_date"] = scheduled.strftime("%d/%m/%Y")
                pending_idx += 1
            else:
                td["scheduled_date"] = None
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
        "next_theme": next_theme,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _theme_to_dict(t: AutoScheduleTheme) -> dict:
    return {
        "id": t.id,
        "theme": t.theme,
        "status": t.status,
        "position": t.position,
        "custom_settings": t.custom_settings,
        "video_project_id": t.video_project_id,
        "error_message": t.error_message,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _pilot_summary_dict(pilot: AutoChannelPilot | None) -> dict:
    if not pilot:
        return {
            "enabled": False,
            "channel_mode": "auto",
            "short_mix_mode": "realistic_all",
            "shorts_per_cycle": 3,
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
        "shorts_per_cycle": int(pilot.shorts_per_cycle or 3),
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

@router.get("/tevoxi-songs")
async def list_tevoxi_songs(user: dict = Depends(get_current_user)):
    """Fetch user's songs from Tevoxi/Levita."""
    token = _get_tevoxi_token()
    if not token:
        raise HTTPException(status_code=500, detail="Tevoxi não configurado.")

    api_url = settings.tevoxi_api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{api_url}/api/feed/my-created-music", headers=headers)
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
async def proxy_tevoxi_audio(job_id: str, user: dict = Depends(get_current_user)):
    """Proxy Tevoxi audio through this backend to avoid browser CORS/auth issues."""
    token = _get_tevoxi_token()
    if not token:
        raise HTTPException(status_code=500, detail="Tevoxi não configurado.")
    if not job_id or not job_id.strip():
        raise HTTPException(status_code=400, detail="job_id inválido.")

    api_url = settings.tevoxi_api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    audio_url = f"{api_url}/api/create-music/audio/{job_id.strip()}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(audio_url, headers=headers)
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
            shorts_per_cycle=3,
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
    if req.shorts_per_cycle is not None:
        pilot.shorts_per_cycle = max(1, min(6, int(req.shorts_per_cycle)))

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
        [_theme_to_dict(t) for t in schedule.themes],
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

