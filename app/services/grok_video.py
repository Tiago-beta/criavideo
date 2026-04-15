"""
Grok Video — Uses xAI's grok-imagine-video to generate video clips
from images (image-to-video) for realistic AI video generation.
"""
import os
import time
import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

XAI_BASE_URL = "https://api.x.ai/v1"


async def generate_video_clip(
    image_path: str,
    prompt: str,
    output_path: str,
    duration: int = 6,
    aspect_ratio: str = "16:9",
    timeout_seconds: int = 600,
    on_progress=None,
) -> str:
    """Generate a short video clip from an image using Grok grok-imagine-video.

    Returns the local path to the downloaded video clip.
    """
    headers = {
        "Authorization": f"Bearer {settings.xai_api_key}",
        "Content-Type": "application/json",
    }

    # Read image and encode as base64 data URI
    import base64
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # Detect mime type
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/png")
    image_url = f"data:{mime_type};base64,{image_data}"

    if on_progress:
        await on_progress(20, "Iniciando geracao Grok...")

    # Step 1: Start generation
    payload = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "image_url": image_url,
        "duration": max(1, min(duration, 15)),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{XAI_BASE_URL}/videos/generations", headers=headers, json=payload)
        resp.raise_for_status()
        request_id = resp.json()["request_id"]

    logger.info(f"Grok video generation started: {request_id}")

    if on_progress:
        await on_progress(30, "Grok gerando video...")

    # Step 2: Poll for result
    start_time = time.time()
    poll_count = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while (time.time() - start_time) < timeout_seconds:
            resp = await client.get(f"{XAI_BASE_URL}/videos/{request_id}", headers=headers)
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status")
            if status == "done":
                video_url = data["video"]["url"]
                break
            elif status in ("failed", "expired"):
                raise RuntimeError(f"Grok video generation {status}: {data}")

            poll_count += 1
            if on_progress and poll_count % 3 == 0:
                pct = min(30 + poll_count, 70)
                await on_progress(pct, "Grok gerando video...")

            await _async_sleep(5)
        else:
            raise TimeoutError(f"Grok video generation timed out after {timeout_seconds}s")

    if on_progress:
        await on_progress(75, "Baixando video gerado...")

    # Step 3: Download video
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(video_url)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)

    logger.info(f"Grok video clip saved: {output_path}")
    return output_path


async def _async_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)
