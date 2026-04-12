"""
Schedule Router — Endpoints for managing automated posting schedules.
"""
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.auth import get_current_user
from app.database import get_db
from app.models import PublishSchedule, SocialAccount, Platform
from app.config import get_settings

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def _local_to_utc(time_local: str, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return time_local
    h, m = map(int, time_local.split(":"))
    today = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)
    return today.astimezone(ZoneInfo("UTC")).strftime("%H:%M")


def _utc_to_local(time_utc: str, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return time_utc
    h, m = map(int, time_utc.split(":"))
    today = datetime.now(ZoneInfo("UTC")).replace(hour=h, minute=m, second=0, microsecond=0)
    return today.astimezone(tz).strftime("%H:%M")


class CreateScheduleRequest(BaseModel):
    platform: str  # "youtube", "tiktok", "instagram"
    social_account_id: int
    frequency: str = "daily"  # "daily" or "weekly"
    time_local: str = "14:00"  # HH:MM
    timezone: str = "UTC"
    day_of_week: Optional[int] = None  # 0=Mon, only for weekly


class AddToQueueRequest(BaseModel):
    render_ids: list[int]


@router.post("/")
async def create_schedule(
    req: CreateScheduleRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new automated posting schedule."""
    # Verify social account belongs to user
    account = await db.get(SocialAccount, req.social_account_id)
    if not account or account.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Social account not found")

    try:
        platform = Platform(req.platform)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid platform")

    if account.platform != platform:
        raise HTTPException(status_code=400, detail="Selected account does not match the platform")

    schedule = PublishSchedule(
        user_id=user["id"],
        platform=platform,
        social_account_id=req.social_account_id,
        frequency=req.frequency,
        time_utc=_local_to_utc(req.time_local, req.timezone),
        timezone=req.timezone,
        day_of_week=req.day_of_week,
        queue=[],
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return {"id": schedule.id, "status": "created"}


@router.get("/")
async def list_schedules(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all schedules for the current user."""
    result = await db.execute(
        select(PublishSchedule).where(PublishSchedule.user_id == user["id"])
    )
    schedules = result.scalars().all()

    account_ids = {s.social_account_id for s in schedules if s.social_account_id}
    accounts_by_id: dict[int, SocialAccount] = {}
    if account_ids:
        accounts_result = await db.execute(
            select(SocialAccount)
            .where(SocialAccount.user_id == user["id"])
            .where(SocialAccount.id.in_(account_ids))
        )
        accounts = accounts_result.scalars().all()
        accounts_by_id = {a.id: a for a in accounts}

    def _account_name(account: SocialAccount | None) -> str:
        if not account:
            return "Conta conectada"
        return account.account_label or account.platform_username or "Conta conectada"

    return [
        {
            "id": s.id,
            "platform": s.platform.value,
            "social_account_id": s.social_account_id,
            "account_label": _account_name(accounts_by_id.get(s.social_account_id)),
            "frequency": s.frequency,
            "time_utc": s.time_utc,
            "time_local": _utc_to_local(s.time_utc, s.timezone or "UTC"),
            "timezone": s.timezone or "UTC",
            "day_of_week": s.day_of_week,
            "is_active": s.is_active,
            "queue_length": len(s.queue) if s.queue else 0,
        }
        for s in schedules
    ]


@router.post("/{schedule_id}/queue")
async def add_to_queue(
    schedule_id: int,
    req: AddToQueueRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add render IDs to a schedule's queue."""
    schedule = await db.get(PublishSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Schedule not found")

    current_queue = list(schedule.queue or [])
    current_queue.extend(req.render_ids)
    schedule.queue = current_queue
    await db.commit()
    return {"queue_length": len(current_queue)}


@router.patch("/{schedule_id}")
async def toggle_schedule(
    schedule_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle a schedule on/off."""
    schedule = await db.get(PublishSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Schedule not found")

    schedule.is_active = not schedule.is_active
    await db.commit()
    return {"id": schedule.id, "is_active": schedule.is_active}


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a schedule."""
    schedule = await db.get(PublishSchedule, schedule_id)
    if not schedule or schedule.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Schedule not found")

    await db.delete(schedule)
    await db.commit()
    return {"deleted": True}
