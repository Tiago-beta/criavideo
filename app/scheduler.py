"""
Scheduler — APScheduler-based periodic task runner for automated posting.
"""
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import async_session
from app.models import PublishSchedule, PublishJob, PublishStatus, VideoRender, VideoProject, Platform, AutoSchedule, AutoScheduleTheme
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler()


async def check_scheduled_posts():
    """Runs every minute. Checks for schedules that are due and creates publish jobs."""
    now = datetime.utcnow()
    current_time = now.strftime("%H:%M")
    current_dow = now.weekday()  # 0=Mon

    async with async_session() as db:
        result = await db.execute(
            select(PublishSchedule).where(PublishSchedule.is_active == True)
        )
        schedules = result.scalars().all()

        for schedule in schedules:
            if not schedule.queue:
                continue

            # Check if it's time to post
            if schedule.time_utc != current_time:
                continue

            if schedule.frequency == "weekly" and schedule.day_of_week != current_dow:
                continue

            # Pop next render from queue
            queue = list(schedule.queue)
            render_id = queue.pop(0)
            schedule.queue = queue

            # Verify render exists
            render = await db.get(VideoRender, render_id)
            if not render:
                logger.warning(f"Render {render_id} not found for schedule {schedule.id}")
                await db.commit()
                continue

            # Create publish job
            job = PublishJob(
                user_id=schedule.user_id,
                render_id=render_id,
                platform=schedule.platform,
                social_account_id=schedule.social_account_id,
                title=f"Auto-posted video",
                status=PublishStatus.PENDING,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

            # Execute publish
            from app.tasks.publish_tasks import run_publish_job
            try:
                await run_publish_job(job.id)
            except Exception as e:
                logger.error(f"Scheduled publish failed: {e}")

            logger.info(f"Scheduled post executed: schedule={schedule.id}, render={render_id}, platform={schedule.platform.value}")


async def check_pending_publish_jobs():
    """Check for pending publish jobs that haven't been picked up (e.g., after restart)."""
    async with async_session() as db:
        result = await db.execute(
            select(PublishJob)
            .where(PublishJob.status.in_([PublishStatus.PENDING, PublishStatus.SCHEDULED]))
            .where(PublishJob.scheduled_at.isnot(None))
            .where(PublishJob.scheduled_at <= datetime.utcnow())
            .limit(5)
        )
        jobs = result.scalars().all()

        for job in jobs:
            from app.tasks.publish_tasks import run_publish_job
            try:
                await run_publish_job(job.id)
            except Exception as e:
                logger.error(f"Pending job {job.id} failed: {e}")


RENDER_EXPIRY_HOURS = 48


def _is_tevoxi_music_project(project: Optional[VideoProject]) -> bool:
    """Return True when the project uses music sourced from Tevoxi."""
    if not project:
        return False

    tags = project.tags if isinstance(project.tags, dict) else {}

    if str(tags.get("audio_source", "")).strip().lower() == "tevoxi":
        return True
    if str(tags.get("tevoxi_audio_url", "")).strip():
        return True
    if str(tags.get("tevoxi_job_id", "")).strip():
        return True
    if bool(tags.get("musical_short")):
        return True

    audio_url = str(tags.get("audio_url", "")).strip().lower()
    if "/api/create-music/audio/" in audio_url or "tevoxi" in audio_url:
        return True

    return str(project.track_artist or "").strip().lower() == "tevoxi"


def _parse_theme_override_date(raw_value: object):
    value = str(raw_value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _has_pending_theme_due_today(schedule: AutoSchedule, pending_themes: list[AutoScheduleTheme]) -> bool:
    try:
        tz = ZoneInfo(schedule.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    today_local = datetime.now(tz).date()

    for theme in pending_themes:
        custom_settings = theme.custom_settings if isinstance(theme.custom_settings, dict) else {}
        override_date = _parse_theme_override_date(custom_settings.get("scheduled_date_override"))
        if override_date and override_date <= today_local:
            return True

    return False


async def check_auto_schedules():
    """Runs every minute. Checks for auto-schedules that are due and triggers video creation."""
    now = datetime.utcnow()
    current_time = now.strftime("%H:%M")
    current_dow = now.weekday()

    async with async_session() as db:
        result = await db.execute(
            select(AutoSchedule).where(AutoSchedule.is_active == True)
        )
        schedules = result.scalars().all()

        for schedule in schedules:
            if schedule.time_utc != current_time:
                continue

            # Check if there are pending themes
            theme_result = await db.execute(
                select(AutoScheduleTheme)
                .where(
                    AutoScheduleTheme.auto_schedule_id == schedule.id,
                    AutoScheduleTheme.status.in_(["pending", "processing"]),
                )
            )
            themes = theme_result.scalars().all()
            processing = [t for t in themes if t.status == "processing"]
            pending = [t for t in themes if t.status == "pending"]

            if processing:
                logger.info(f"Auto-schedule {schedule.id}: already processing, skipping")
                continue

            if not pending:
                logger.info(f"Auto-schedule {schedule.id}: no pending themes")
                continue

            has_manual_due_today = _has_pending_theme_due_today(schedule, pending)

            if schedule.frequency == "weekly" and schedule.day_of_week != current_dow:
                if not has_manual_due_today:
                    continue
                logger.info(
                    "Auto-schedule %d: triggering outside weekly day due to manual theme date",
                    schedule.id,
                )

            active_weekdays = None
            if isinstance(schedule.default_settings, dict):
                active_weekdays = schedule.default_settings.get("active_weekdays")
            if schedule.frequency == "daily" and active_weekdays:
                try:
                    allowed_days = {int(day) for day in active_weekdays}
                except Exception:
                    allowed_days = set()
                if allowed_days and current_dow not in allowed_days:
                    continue

            logger.info(f"Auto-schedule {schedule.id} triggered at {current_time}")

            # Fire and forget — run in background
            import asyncio
            from app.tasks.auto_creation_tasks import run_auto_creation
            asyncio.create_task(run_auto_creation(schedule.id))


async def check_auto_channel_pilots():
    """Runs every 15 minutes. Re-analyzes enabled pilot channels and replenishes themes."""
    from app.tasks.auto_pilot_tasks import run_due_channel_pilots

    try:
        await run_due_channel_pilots()
    except Exception as err:
        logger.error("Auto channel pilot cycle failed: %s", err)


async def cleanup_expired_renders():
    """Delete render files older than 48 hours to free server storage.

    Tevoxi-music projects are excluded from automatic cleanup and can only be
    removed by explicit user deletion.
    """
    cutoff = datetime.utcnow() - timedelta(hours=RENDER_EXPIRY_HOURS)
    media_dir = settings.media_dir

    async with async_session() as db:
        result = await db.execute(
            select(VideoRender)
            .options(selectinload(VideoRender.project))
            .where(
                VideoRender.created_at < cutoff,
                VideoRender.file_path.isnot(None),
            )
        )
        expired_renders = result.scalars().all()

        deleted_count = 0
        skipped_tevoxi_count = 0
        for render in expired_renders:
            if _is_tevoxi_music_project(render.project):
                skipped_tevoxi_count += 1
                continue

            # Delete video file
            if render.file_path and os.path.exists(render.file_path):
                try:
                    os.remove(render.file_path)
                except OSError:
                    pass

            # Delete thumbnail file
            if render.thumbnail_path and os.path.exists(render.thumbnail_path):
                try:
                    os.remove(render.thumbnail_path)
                except OSError:
                    pass

            # Clean up empty render directory
            if render.file_path:
                render_dir = Path(render.file_path).parent
                if render_dir.exists() and not any(render_dir.iterdir()):
                    shutil.rmtree(render_dir, ignore_errors=True)

            # Clear paths in DB but keep the record
            render.file_path = None
            render.thumbnail_path = None
            deleted_count += 1

        if deleted_count:
            await db.commit()
            logger.info(f"Cleanup: removed files for {deleted_count} expired render(s)")

        if skipped_tevoxi_count:
            logger.info(f"Cleanup: skipped {skipped_tevoxi_count} Tevoxi render(s) from auto-deletion")

        # Also clean up source assets (images, clips, subtitles, audio) for projects
        # where ALL renders have expired (file_path is None)
        result2 = await db.execute(
            select(VideoProject).where(VideoProject.status == "completed")
        )
        projects = result2.scalars().all()
        for project in projects:
            if _is_tevoxi_music_project(project):
                continue

            # Check if project has any render with files still on disk
            r_result = await db.execute(
                select(VideoRender).where(
                    VideoRender.project_id == project.id,
                    VideoRender.file_path.isnot(None),
                )
            )
            if r_result.scalars().first():
                continue  # Still has active renders

            # All renders expired — clean up source directories
            for dir_name in ["images", "clips", "subtitles"]:
                dir_path = Path(media_dir) / dir_name / str(project.id)
                if dir_path.exists():
                    shutil.rmtree(dir_path, ignore_errors=True)


def start_scheduler():
    """Start the APScheduler with periodic tasks."""
    scheduler.add_job(
        check_scheduled_posts,
        trigger=IntervalTrigger(minutes=1),
        id="check_scheduled_posts",
        replace_existing=True,
    )
    scheduler.add_job(
        check_pending_publish_jobs,
        trigger=IntervalTrigger(minutes=5),
        id="check_pending_publish_jobs",
        replace_existing=True,
    )
    scheduler.add_job(
        cleanup_expired_renders,
        trigger=IntervalTrigger(hours=1),
        id="cleanup_expired_renders",
        replace_existing=True,
    )
    scheduler.add_job(
        check_auto_schedules,
        trigger=IntervalTrigger(minutes=1),
        id="check_auto_schedules",
        replace_existing=True,
    )
    scheduler.add_job(
        check_auto_channel_pilots,
        trigger=IntervalTrigger(minutes=15),
        id="check_auto_channel_pilots",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
