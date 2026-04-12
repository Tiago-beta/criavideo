"""
Automation Router — CRUD for auto-schedules (automated video creation + publishing).
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_db
from app.models import AutoSchedule, AutoScheduleTheme, SocialAccount

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["automation"])


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
    themes: list[str] = Field(default_factory=list)


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
    themes: list[str]


class ReorderThemesRequest(BaseModel):
    theme_ids: list[int]


# ── Helpers ──

def _schedule_to_dict(s: AutoSchedule, theme_count: int = 0) -> dict:
    account_label = ""
    if s.social_account:
        account_label = s.social_account.account_label or s.social_account.platform_username or ""
    next_theme = ""
    pending_count = 0
    if s.themes:
        pending = sorted(
            [t for t in s.themes if t.status == "pending"],
            key=lambda t: t.position,
        )
        pending_count = len(pending)
        if pending:
            next_theme = pending[0].theme
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


# ── Endpoints ──

@router.post("/schedules")
async def create_auto_schedule(
    req: CreateAutoScheduleRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Nome da automacao e obrigatorio.")
    if req.video_type not in ("narration", "music"):
        raise HTTPException(status_code=400, detail="Tipo de video invalido.")
    if req.creation_mode not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="Modo de criacao invalido.")
    if req.frequency not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="Frequencia invalida.")

    # Validate social account if provided
    if req.social_account_id:
        acct = await db.get(SocialAccount, req.social_account_id)
        if not acct or acct.user_id != user["id"]:
            raise HTTPException(status_code=400, detail="Conta social nao encontrada.")

    schedule = AutoSchedule(
        user_id=user["id"],
        name=req.name.strip(),
        video_type=req.video_type,
        creation_mode=req.creation_mode,
        platform=req.platform,
        social_account_id=req.social_account_id,
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
    for i, theme_text in enumerate(req.themes):
        theme_text = (theme_text or "").strip()
        if not theme_text:
            continue
        theme = AutoScheduleTheme(
            auto_schedule_id=schedule.id,
            theme=theme_text,
            position=i,
            status="pending",
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
        raise HTTPException(status_code=404, detail="Automacao nao encontrada.")

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
        raise HTTPException(status_code=404, detail="Automacao nao encontrada.")

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
    if req.social_account_id is not None:
        schedule.social_account_id = req.social_account_id
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
        raise HTTPException(status_code=404, detail="Automacao nao encontrada.")

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
        raise HTTPException(status_code=404, detail="Automacao nao encontrada.")

    # Find max position
    result = await db.execute(
        select(func.max(AutoScheduleTheme.position))
        .where(AutoScheduleTheme.auto_schedule_id == schedule_id)
    )
    max_pos = result.scalar() or -1

    added = []
    for i, theme_text in enumerate(req.themes):
        theme_text = (theme_text or "").strip()
        if not theme_text:
            continue
        theme = AutoScheduleTheme(
            auto_schedule_id=schedule_id,
            theme=theme_text,
            position=max_pos + 1 + i,
            status="pending",
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
        raise HTTPException(status_code=404, detail="Tema nao encontrado.")

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
        raise HTTPException(status_code=404, detail="Automacao nao encontrada.")

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
