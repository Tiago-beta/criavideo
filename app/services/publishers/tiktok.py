"""
TikTok Publisher — Upload videos using TikTok Content Posting API.
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


async def upload_to_tiktok(
    video_path: str,
    title: str,
    access_token: str,
    privacy_level: str = "PUBLIC_TO_EVERYONE",
) -> dict:
    """Upload a video to TikTok using FILE_UPLOAD.

    Returns: {"publish_id": str}
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    file_size = os.path.getsize(video_path)
    chunk_size = min(file_size, 10_000_000)  # 10MB chunks
    total_chunks = (file_size + chunk_size - 1) // chunk_size

    # Step 1: Initialize upload
    init_payload = {
        "post_info": {
            "title": title[:150],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TIKTOK_API_BASE}/post/publish/video/init/",
            headers=headers,
            json=init_payload,
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"TikTok init failed: {data}")

    publish_id = data["data"]["publish_id"]
    upload_url = data["data"]["upload_url"]

    # Step 2: Upload video chunks
    async with httpx.AsyncClient(timeout=300) as client:
        with open(video_path, "rb") as f:
            for chunk_idx in range(total_chunks):
                chunk_data = f.read(chunk_size)
                start_byte = chunk_idx * chunk_size
                end_byte = start_byte + len(chunk_data) - 1

                resp = await client.put(
                    upload_url,
                    content=chunk_data,
                    headers={
                        "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                        "Content-Type": "video/mp4",
                    },
                )
                logger.info(f"TikTok upload chunk {chunk_idx + 1}/{total_chunks}")

    logger.info(f"TikTok upload complete: {publish_id}")
    return {"publish_id": publish_id}


async def check_tiktok_status(publish_id: str, access_token: str) -> dict:
    """Check the publish status of a TikTok video."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
            headers=headers,
            json={"publish_id": publish_id},
        )
        resp.raise_for_status()
        return resp.json()
