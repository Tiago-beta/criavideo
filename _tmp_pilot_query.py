import asyncio
from sqlalchemy import select, func

from app.database import async_session
from app.models import AutoSchedule, AutoScheduleTheme, VideoProject


async def main():
    async with async_session() as db:
        schedule = await db.get(AutoSchedule, 32)
        print(f"schedule id=32 found={bool(schedule)} active={getattr(schedule, 'is_active', None)} video_type={getattr(schedule, 'video_type', None)}")
        if schedule:
            print(f"default_settings={schedule.default_settings}")

        counts = await db.execute(
            select(AutoScheduleTheme.status, func.count(AutoScheduleTheme.id))
            .where(AutoScheduleTheme.auto_schedule_id == 32)
            .group_by(AutoScheduleTheme.status)
        )
        for status, qty in counts.all():
            print(f"theme_status {status}={qty}")

        pending = await db.execute(
            select(AutoScheduleTheme)
            .where(AutoScheduleTheme.auto_schedule_id == 32)
            .where(AutoScheduleTheme.status == "pending")
            .order_by(AutoScheduleTheme.position.asc(), AutoScheduleTheme.id.asc())
            .limit(5)
        )
        for theme in pending.scalars().all():
            print(
                "pending_theme "
                f"id={theme.id} position={theme.position} title={theme.theme_title!r} "
                f"custom={theme.custom_settings}"
            )

        latest = await db.execute(
            select(VideoProject)
            .where(VideoProject.video_type == "musical_shorts")
            .order_by(VideoProject.created_at.desc())
            .limit(5)
        )
        for project in latest.scalars().all():
            print(
                "latest_short "
                f"id={project.id} status={project.status} title={project.title!r} "
                f"youtube={project.youtube_url or ''}"
            )


asyncio.run(main())
