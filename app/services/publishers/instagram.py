"""
Instagram Publisher — Upload Reels using Instagram Graph API.
"""
import os
import time
import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


async def upload_to_instagram(
    video_url: str,
    caption: str,
    access_token: str,
    ig_user_id: str,
) -> dict:
    """Upload a Reel to Instagram.

    NOTE: video_url must be publicly accessible (the video needs to be served
    via a public URL, e.g. nginx on the VPS).

    Returns: {"media_id": str}
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: Create media container
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media",
            headers=headers,
            json={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption[:2200],
            },
        )
        resp.raise_for_status()
        container_id = resp.json()["id"]

    logger.info(f"Instagram container created: {container_id}")

    # Step 2: Wait for processing
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(30):  # max 5 minutes waiting
            resp = await client.get(
                f"{GRAPH_API_BASE}/{container_id}",
                params={"fields": "status_code", "access_token": access_token},
            )
            status = resp.json().get("status_code")
            if status == "FINISHED":
                break
            elif status == "ERROR":
                raise RuntimeError(f"Instagram processing failed: {resp.json()}")
            await _async_sleep(10)
        else:
            raise TimeoutError("Instagram video processing timed out")

    # Step 3: Publish
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
            headers=headers,
            json={"creation_id": container_id},
        )
        resp.raise_for_status()
        media_id = resp.json()["id"]

    logger.info(f"Instagram Reel published: {media_id}")
    return {"media_id": media_id}


async def _async_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)
