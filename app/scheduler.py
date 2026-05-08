"""
Scheduler — APScheduler-based periodic task runner for automated posting.
"""
import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import async_session
from app.models import AutoSchedule, AutoScheduleTheme, Platform, PublishJob, PublishSchedule, PublishStatus, VideoProject, VideoRender

logger = logging.getLogger(__name__)
settings = get_settings()
_UTC = ZoneInfo("UTC")

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

            if schedule.time_utc != current_time:
                continue

            if schedule.frequency == "weekly" and schedule.day_of_week != current_dow:
                continue

            queue = list(schedule.queue)
            render_id = queue.pop(0)
            schedule.queue = queue

            render = await db.get(VideoRender, render_id)
            if not render:
                logger.warning(f"Render {render_id} not found for schedule {schedule.id}")
                await db.commit()
                continue

            job = PublishJob(
                user_id=schedule.user_id,
                render_id=render_id,
                platform=schedule.platform,
                social_account_id=schedule.social_account_id,
                title="Auto-posted video",
                status=PublishStatus.PENDING,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

            from app.tasks.publish_tasks import run_publish_job

            try:
                await run_publish_job(job.id)
            except Exception as err:
                logger.error(f"Scheduled publish failed: {err}")

            logger.info(
                "Scheduled post executed: schedule=%s, render=%s, platform=%s",
                schedule.id,
                render_id,
                schedule.platform.value,
            )


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
            except Exception as err:
                logger.error(f"Pending job {job.id} failed: {err}")


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


def _has_pending_theme_due_today(
    schedule: AutoSchedule,
    pending_themes: list[AutoScheduleTheme],
    reference_now: datetime | None = None,
) -> bool:
    try:
        tz = ZoneInfo(schedule.timezone or "UTC")
    except Exception:
        tz = _UTC

    now_value = reference_now or datetime.utcnow().replace(tzinfo=_UTC)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=_UTC)
    today_local = now_value.astimezone(tz).date()

    for theme in pending_themes:
        custom_settings = theme.custom_settings if isinstance(theme.custom_settings, dict) else {}
        override_date = _parse_theme_override_date(custom_settings.get("scheduled_date_override"))
        if override_date and override_date <= today_local:
            return True

    return False


def _parse_hhmm_to_minutes(raw_value: str | None) -> int | None:
    value = str(raw_value or "").strip()
    if not value or ":" not in value:
        return None
    try:
        hour, minute = value.split(":", 1)
        parsed = (int(hour) * 60) + int(minute)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed < (24 * 60) else None


def _resolve_schedule_local_state(schedule: AutoSchedule, now_utc: datetime) -> tuple[datetime, int | None]:
    reference_now = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=_UTC)
    reference_now = reference_now.astimezone(_UTC)
    try:
        tz = ZoneInfo(schedule.timezone or "UTC")
    except Exception:
        tz = _UTC

    local_now = reference_now.astimezone(tz)
    scheduled_minutes = _parse_hhmm_to_minutes(schedule.time_utc)
    if scheduled_minutes is None:
        return local_now, None

    scheduled_utc = reference_now.replace(
        hour=scheduled_minutes // 60,
        minute=scheduled_minutes % 60,
        second=0,
        microsecond=0,
    )
    scheduled_local = scheduled_utc.astimezone(tz)
    return local_now, (scheduled_local.hour * 60) + scheduled_local.minute


def _should_trigger_auto_schedule(
    schedule: AutoSchedule,
    pending_themes: list[AutoScheduleTheme],
    now_utc: datetime,
    allow_missed_window: bool = False,
) -> tuple[bool, str | None]:
    reference_now = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=_UTC)
    reference_now = reference_now.astimezone(_UTC)
    current_time_utc = reference_now.strftime("%H:%M")
    current_dow_utc = reference_now.weekday()
    local_now, local_run_minutes = _resolve_schedule_local_state(schedule, reference_now)
    local_now_minutes = (local_now.hour * 60) + local_now.minute
    local_dow = local_now.weekday()

    has_manual_due_today = _has_pending_theme_due_today(
        schedule,
        pending_themes,
        reference_now=reference_now,
    )
    exact_match = schedule.time_utc == current_time_utc
    missed_window = bool(
        allow_missed_window
        and not exact_match
        and local_run_minutes is not None
        and local_now_minutes >= local_run_minutes
    )

    if not exact_match and not missed_window:
        return False, None

    weekday_ref = current_dow_utc if exact_match else local_dow
    trigger_reason = None

    if schedule.frequency == "weekly" and schedule.day_of_week != weekday_ref:
        if not has_manual_due_today:
            return False, None
        trigger_reason = "triggering outside weekly day due to manual theme date"

    active_weekdays = None
    if isinstance(schedule.default_settings, dict):
        active_weekdays = schedule.default_settings.get("active_weekdays")

    if schedule.frequency == "daily" and active_weekdays:
        try:
            allowed_days = {int(day) for day in active_weekdays}
        except Exception:
            allowed_days = set()
        if allowed_days and weekday_ref not in allowed_days:
            if not has_manual_due_today:
                return False, None
            trigger_reason = "triggering outside active weekdays due to manual theme date"

    if missed_window and trigger_reason is None:
        trigger_reason = "recovering missed auto-schedule run after scheduler startup"

    return True, trigger_reason


async def check_auto_schedules(allow_missed_window: bool = False):
    """Runs every minute. Checks for auto-schedules that are due and triggers video creation."""
    now = datetime.utcnow().replace(tzinfo=_UTC)
    current_time = now.astimezone(_UTC).strftime("%H:%M")

    async with async_session() as db:
        result = await db.execute(
            select(AutoSchedule).where(AutoSchedule.is_active == True)
        )
        schedules = result.scalars().all()

        for schedule in schedules:
            theme_result = await db.execute(
                select(AutoScheduleTheme)
                .where(
                    AutoScheduleTheme.auto_schedule_id == schedule.id,
                    AutoScheduleTheme.status.in_(["pending", "processing"]),
                )
            )
            themes = theme_result.scalars().all()
            processing = [theme for theme in themes if theme.status == "processing"]
            pending = [theme for theme in themes if theme.status == "pending"]

            if processing:
                logger.info(f"Auto-schedule {schedule.id}: already processing, skipping")
                continue

            if not pending:
                logger.info(f"Auto-schedule {schedule.id}: no pending themes")
                continue

            should_trigger, trigger_reason = _should_trigger_auto_schedule(
                schedule,
                pending,
                now,
                allow_missed_window=allow_missed_window,
            )
            if not should_trigger:
                continue

            if trigger_reason:
                logger.info("Auto-schedule %d: %s", schedule.id, trigger_reason)

            logger.info(f"Auto-schedule {schedule.id} triggered at {current_time}")

            from app.tasks.auto_creation_tasks import run_auto_creation

            asyncio.create_task(run_auto_creation(schedule.id))


async def check_auto_channel_pilots():
    """Runs every 15 minutes. Re-analyzes enabled pilot channels and replenishes themes."""
    from app.tasks.auto_pilot_tasks import run_due_channel_pilots

    try:
        await run_due_channel_pilots()
    except Exception as err:
        logger.error("Auto channel pilot cycle failed: %s", err)


RENDER_EXPIRY_HOURS = 48


def _resolve_media_storage_path(raw_path: str | None) -> Path | None:
    source = str(raw_path or "").strip()
    if not source:
        return None

    if source.startswith("/video/media/"):
        source = os.path.join(settings.media_dir, source.split("/video/media/", 1)[-1].lstrip("/"))
    elif "/video/media/" in source:
        source = os.path.join(settings.media_dir, source.split("/video/media/", 1)[-1].lstrip("/"))
    elif not os.path.isabs(source):
        source = os.path.join(settings.media_dir, source.lstrip("/"))

    return Path(os.path.normpath(source))


async def cleanup_expired_renders():
    """Delete render files older than 48 hours to free server storage."""
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
        for render in expired_renders:
            video_path = _resolve_media_storage_path(render.file_path)
            thumbnail_path = _resolve_media_storage_path(render.thumbnail_path)
            render_updated = False
            video_removed = False
            thumbnail_removed = False

            if video_path and video_path.exists():
                try:
                    os.remove(video_path)
                    video_removed = True
                except OSError as err:
                    logger.warning("Cleanup: failed to delete expired render file %s: %s", video_path, err)
            if render.file_path and (video_path is None or video_removed or not video_path.exists()):
                render.file_path = None
                render_updated = True

            if thumbnail_path and thumbnail_path.exists():
                try:
                    os.remove(thumbnail_path)
                    thumbnail_removed = True
                except OSError as err:
                    logger.warning("Cleanup: failed to delete expired thumbnail file %s: %s", thumbnail_path, err)
            if render.thumbnail_path and (thumbnail_path is None or thumbnail_removed or not thumbnail_path.exists()):
                render.thumbnail_path = None
                render_updated = True

            if video_path:
                render_dir = video_path.parent
                if render_dir.exists() and not any(render_dir.iterdir()):
                    shutil.rmtree(render_dir, ignore_errors=True)

            if thumbnail_path:
                thumb_dir = thumbnail_path.parent
                if thumb_dir.exists() and not any(thumb_dir.iterdir()):
                    shutil.rmtree(thumb_dir, ignore_errors=True)

            if render_updated:
                deleted_count += 1

        if deleted_count:
            await db.commit()
            logger.info(f"Cleanup: removed files for {deleted_count} expired render(s)")

        # Also clean up source assets (images, clips, subtitles, audio) for projects
        # where ALL renders have expired (file_path is None)
        result2 = await db.execute(
            select(VideoProject).where(VideoProject.status == "completed")
        )
        projects = result2.scalars().all()
        for project in projects:
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
            for dir_name in ["images", "clips", "subtitles", "audio", "renders", "thumbnails"]:
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
    try:
        asyncio.get_running_loop().create_task(check_auto_schedules(allow_missed_window=True))
    except RuntimeError:
        logger.warning("Scheduler startup catch-up skipped: no running event loop")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
