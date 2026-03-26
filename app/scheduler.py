"""
Scheduler — APScheduler-based periodic task runner for automated posting.
"""
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from app.database import async_session
from app.models import PublishSchedule, PublishJob, PublishStatus, VideoRender, Platform
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
            .where(PublishJob.status == PublishStatus.PENDING)
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
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
