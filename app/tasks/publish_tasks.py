"""
Publish Tasks — Async background tasks for publishing to social platforms.
"""
import logging
from datetime import datetime
from app.database import async_session
from app.models import PublishJob, PublishStatus, VideoRender, SocialAccount, Platform
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_publish_job(job_id: int):
    """Execute a single publish job."""
    async with async_session() as db:
        try:
            job = await db.get(PublishJob, job_id)
            if not job:
                return

            job.status = PublishStatus.UPLOADING
            await db.commit()

            render = await db.get(VideoRender, job.render_id)
            account = await db.get(SocialAccount, job.social_account_id)

            if not render or not render.file_path:
                raise FileNotFoundError("Render file not found")
            if not account:
                raise ValueError("Social account not found")

            result = {}

            if job.platform == Platform.YOUTUBE:
                from app.services.publishers.youtube import upload_to_youtube
                result = await upload_to_youtube(
                    video_path=render.file_path,
                    title=job.title or "Music Video",
                    description=job.description or "",
                    tags=job.tags or [],
                    thumbnail_path=render.thumbnail_path,
                    access_token=account.access_token,
                    refresh_token=account.refresh_token,
                )
                job.platform_post_id = result.get("video_id")
                job.platform_url = result.get("url")

            elif job.platform == Platform.TIKTOK:
                from app.services.publishers.tiktok import upload_to_tiktok
                result = await upload_to_tiktok(
                    video_path=render.file_path,
                    title=job.title or "Music Video",
                    access_token=account.access_token,
                )
                job.platform_post_id = result.get("publish_id")

            elif job.platform == Platform.INSTAGRAM:
                from app.services.publishers.instagram import upload_to_instagram
                # Instagram needs a public URL for the video
                video_url = f"{settings.site_url}/video/media/renders/{render.project_id}/{render.file_path.split('/')[-1]}"
                ig_user_id = account.extra_data.get("instagram_user_id", account.platform_user_id)
                result = await upload_to_instagram(
                    video_url=video_url,
                    caption=f"{job.title}\n\n{job.description}",
                    access_token=account.access_token,
                    ig_user_id=ig_user_id,
                )
                job.platform_post_id = result.get("media_id")

            job.status = PublishStatus.PUBLISHED
            job.published_at = datetime.utcnow()
            await db.commit()

            logger.info(f"Publish job {job_id} completed: {job.platform.value} → {result}")

        except Exception as e:
            logger.error(f"Publish job {job_id} failed: {e}", exc_info=True)
            job = await db.get(PublishJob, job_id)
            if job:
                job.status = PublishStatus.FAILED
                job.error_message = str(e)[:1000]
                await db.commit()
