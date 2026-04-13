"""
MiniMax Hailuo Video — Uses MiniMax API for realistic AI video generation.
Supports text-to-video and image-to-video via Hailuo 2.3 model.
"""
import os
import time
import logging
import asyncio
import base64
import mimetypes
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MINIMAX_API_BASE = "https://api.minimax.io/v1"
MINIMAX_MODEL = "MiniMax-Hailuo-2.3"


async def generate_minimax_video(
    prompt: str,
    duration: int = 6,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    resolution: str = "1080P",
    image_path: str | None = None,
    timeout_seconds: int = 600,
    on_progress=None,
) -> str:
    """Generate a video using MiniMax Hailuo 2.3 API.

    Returns the local path to the downloaded MP4 video.
    """
    token = settings.minimax_api_key
    if not token:
        raise RuntimeError("MINIMAX_API_KEY not configured")

    # MiniMax supports duration 6 or 10
    duration = 6 if duration <= 7 else 10

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MINIMAX_MODEL,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "prompt_optimizer": True,
    }

    # Image-to-video: encode as base64 data URI
    if image_path and os.path.exists(image_path):
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")
        payload["first_frame_image"] = f"data:{mime_type};base64,{img_data}"
        logger.info(f"MiniMax image-to-video mode: {image_path} ({mime_type})")

    # Step 1: Create generation task
    if on_progress:
        await on_progress(10, "Enviando para MiniMax Hailuo...")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{MINIMAX_API_BASE}/video_generation",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax task creation failed: {base_resp.get('status_msg', 'Unknown error')}")

    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError("MiniMax returned no task_id")

    logger.info(f"MiniMax task created: {task_id}")

    if on_progress:
        await on_progress(20, "MiniMax Hailuo esta gerando seu video...")

    # Step 2: Poll for completion
    poll_url = f"{MINIMAX_API_BASE}/query/video_generation"
    poll_headers = {"Authorization": f"Bearer {token}"}
    start_time = time.time()
    last_progress = 20

    while (time.time() - start_time) < timeout_seconds:
        await asyncio.sleep(8)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    poll_url,
                    headers=poll_headers,
                    params={"task_id": task_id},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"MiniMax poll error: {e}")
            continue

        status = data.get("status", "")
        logger.info(f"MiniMax task {task_id} status: {status}")

        if status == "Success":
            file_id = data.get("file_id")
            if not file_id:
                raise RuntimeError("MiniMax returned Success but no file_id")

            if on_progress:
                await on_progress(80, "Baixando video gerado...")

            # Step 3: Get download URL
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{MINIMAX_API_BASE}/files/retrieve",
                    headers=poll_headers,
                    params={"file_id": file_id},
                )
                resp.raise_for_status()
                file_data = resp.json()

            download_url = file_data.get("file", {}).get("download_url")
            if not download_url:
                raise RuntimeError("MiniMax returned no download URL")

            # Step 4: Download video
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get(download_url)
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    f.write(resp.content)

            file_size = os.path.getsize(output_path)
            logger.info(f"MiniMax video downloaded: {output_path} ({file_size} bytes)")
            return output_path

        elif status == "Fail":
            error_msg = data.get("error_message", "Unknown error")
            raise RuntimeError(f"MiniMax generation failed: {error_msg}")

        # Update progress
        elapsed = time.time() - start_time
        progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
        if progress > last_progress and on_progress:
            last_progress = progress
            await on_progress(progress, "MiniMax Hailuo esta gerando seu video...")

    raise TimeoutError(f"MiniMax generation timed out after {timeout_seconds}s")
