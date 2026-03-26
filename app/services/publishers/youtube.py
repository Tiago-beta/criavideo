"""
YouTube Publisher — Upload videos using YouTube Data API v3.
"""
import os
import logging
import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)


async def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    thumbnail_path: str | None,
    access_token: str,
    refresh_token: str | None = None,
    category_id: str = "10",  # Music category
) -> dict:
    """Upload a video to YouTube.

    Returns: {"video_id": str, "url": str}
    """
    import asyncio

    def _upload():
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
        )

        youtube = build("youtube", "v3", credentials=credentials)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:30],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            chunksize=1024 * 1024 * 8,  # 8MB chunks
            resumable=True,
        )

        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"YouTube upload progress: {int(status.progress() * 100)}%")

        video_id = response["id"]
        logger.info(f"YouTube upload complete: {video_id}")

        # Upload thumbnail if available
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
                ).execute()
                logger.info(f"YouTube thumbnail set for {video_id}")
            except Exception as e:
                logger.warning(f"Failed to set YouTube thumbnail: {e}")

        return {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        }

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload)
