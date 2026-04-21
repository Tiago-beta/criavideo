"""
Grok Video — Uses xAI's grok-imagine-video to generate video clips
from images (image-to-video) for realistic AI video generation.
"""
import os
import time
import logging
import httpx
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

XAI_BASE_URL = "https://api.x.ai/v1"

_GROK_SYSTEM_PROMPT = """You are an expert prompt engineer for xAI's grok-imagine-video model.

Your job: convert the user's video description (usually in Brazilian Portuguese) into an optimized prompt for Grok video generation.

RULES:
1. Output ONLY the final prompt. No explanations, no markdown.
2. The video is {duration} seconds long.
3. Describe the visual scene in vivid, cinematic detail: camera movements, lighting, mood, specific actions.
4. CRITICAL — LANGUAGE: If the scene involves people speaking, narration, dialogue, or any audio with words, ALL speech MUST be in Brazilian Portuguese (pt-BR). Write the dialogue in Portuguese using double quotes.
   Example: A mulher olha para a camera e diz: "Que dia lindo para passear!"
5. For sound effects and ambient audio, describe them naturally (birds singing, city noise, rain, etc).
6. Focus on concrete visual details: colors, textures, motion, physics.
7. Include camera direction: slow push in, pan, dolly, tracking shot, crane, etc.
8. Keep the prompt concise but detailed — under 500 words.
9. Preserve the user's creative intent while enhancing with cinematic quality.
10. CONTENT SAFETY: Avoid violent, sexual, or controversial content.
11. If the user says there is a reference image, explicitly anchor the scene to that image and preserve the same subject identity and key visual traits.
12. CHARACTER CONTINUITY: if the input includes continuation cues (e.g. "Continue from previous scene", "CHARACTER_LOCK", "WORLD_LOCK"), keep those continuity details unchanged and do not alter the main characters."""


async def optimize_prompt_for_grok(
    user_description: str,
    duration: int = 7,
    has_reference_image: bool = False,
) -> str:
    """Convert user's description into an optimized Grok video prompt with PT-BR audio."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    system = _GROK_SYSTEM_PROMPT.replace("{duration}", str(duration))
    user_msg = user_description
    if has_reference_image:
        user_msg += (
            "\n\nMANDATORY REFERENCE IMAGE RULE: The user uploaded a reference image. "
            "The prompt must preserve the same subject identity and key visual traits from that image."
        )

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        optimized = resp.choices[0].message.content.strip()
        logger.info(f"Grok prompt optimized: {len(optimized)} chars")
        return optimized
    except Exception as e:
        logger.warning(f"Grok prompt optimization failed, using original: {e}")
        return user_description


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
