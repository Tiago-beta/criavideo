"""
Schedule Router — Endpoints for managing automated posting schedules.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
from app.auth import get_current_user
from app.database import get_db
from app.models import PublishSchedule, SocialAccount, Platform
from app.config import get_settings

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


class CreateScheduleRequest(BaseModel):
    platform: str  # "youtube", "tiktok", "instagram"
    social_account_id: int
    frequency: str = "daily"  # "daily" or "weekly"
    time_utc: str = "14:00"  # HH:MM
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

    schedule = PublishSchedule(
        user_id=user["id"],
        platform=platform,
        social_account_id=req.social_account_id,
        frequency=req.frequency,
        time_utc=req.time_utc,
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
    return [
        {
            "id": s.id,
            "platform": s.platform.value,
            "frequency": s.frequency,
            "time_utc": s.time_utc,
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
