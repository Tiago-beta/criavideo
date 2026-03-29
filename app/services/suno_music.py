"""
Suno Music Generator — Generates real instrumental background music via Suno API.
"""
import asyncio
import logging
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SUNO_BASE_URL = "https://api.sunoapi.org/api/v1"

# Map mood to instrumental music styles
MOOD_STYLES = {
    "inspiracional": "ambient cinematic instrumental, inspirational, soft piano, gentle strings, uplifting",
    "informativo": "corporate background instrumental, light electronic, upbeat, clean",
    "misterioso": "dark ambient instrumental, mysterious, suspenseful, atmospheric pads, tension",
    "motivacional": "upbeat motivational instrumental, energetic, cinematic drums, powerful",
    "urgente": "tense dramatic instrumental, fast-paced, urgent, electronic percussion",
    "reflexivo": "calm ambient instrumental, peaceful piano, meditation, serene, gentle",
    "dramatico": "emotional cinematic instrumental, dramatic orchestral, strings, epic",
}


async def generate_suno_music(
    output_path: str,
    duration: float,
    mood: str = "inspiracional",
    topic: str = "",
) -> str:
    """Generate instrumental background music using Suno API.

    Returns the output_path on success, empty string on failure.
    """
    api_key = settings.suno_api_key
    if not api_key:
        logger.warning("SUNO_API_KEY not configured, skipping Suno music generation")
        return ""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Build style from mood + topic context
    style = MOOD_STYLES.get(mood.lower(), MOOD_STYLES["inspiracional"])
    if topic:
        style = f"{style}, {topic[:80]}"

    # Ensure short duration for background (Suno generates ~2-4 min tracks)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "customMode": True,
        "instrumental": True,
        "model": "V4",
        "prompt": "",
        "style": style,
        "title": "Background Music",
        "callBackUrl": f"{settings.site_url}/api/suno-callback/bgm",
    }

    logger.info(f"Suno: requesting instrumental music (mood={mood}, style={style[:60]}...)")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Start generation
            resp = await client.post(
                f"{SUNO_BASE_URL}/generate",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            resp_data = resp.json()
            logger.info(f"Suno: generate response: {resp_data}")
            
            # Handle various response formats
            inner = resp_data.get("data") or resp_data
            if isinstance(inner, dict):
                task_id = inner.get("taskId") or inner.get("task_id")
            else:
                task_id = None
            
            if not task_id:
                logger.warning(f"Suno: no taskId in response: {resp_data}")
                return ""

            logger.info(f"Suno: generation started, taskId={task_id}")

            # Step 2: Poll for completion (max 5 min)
            audio_url = await _poll_suno_task(client, headers, task_id, max_wait=300)
            if not audio_url:
                return ""

            # Step 3: Download the MP3
            logger.info(f"Suno: downloading audio from {audio_url[:80]}...")
            dl_resp = await client.get(audio_url, timeout=60, follow_redirects=True)
            dl_resp.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(dl_resp.content)

            file_size = Path(output_path).stat().st_size
            logger.info(f"Suno: music saved to {output_path} ({file_size} bytes)")
            return output_path

    except Exception as e:
        logger.warning(f"Suno music generation failed: {e}")
        return ""


async def _poll_suno_task(
    client: httpx.AsyncClient,
    headers: dict,
    task_id: str,
    max_wait: int = 300,
) -> str:
    """Poll Suno API for task completion. Returns audio_url or empty string.
    
    Suno response format on success:
    {
        "data": {
            "status": "SUCCESS",
            "response": {
                "sunoData": [
                    {"audioUrl": "https://...", "duration": 257.28, ...},
                    ...
                ]
            }
        }
    }
    """
    elapsed = 0
    interval = 5

    while elapsed < max_wait:
        await asyncio.sleep(interval)
        elapsed += interval

        try:
            resp = await client.get(
                f"{SUNO_BASE_URL}/generate/record-info",
                params={"taskId": task_id},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

            if elapsed <= 10:
                logger.info(f"Suno: poll response (truncated): code={result.get('code')}, status={result.get('data', {}).get('status')}")

            inner = result.get("data") or {}
            if not isinstance(inner, dict):
                inner = {}
            status = inner.get("status", "")

            if status == "SUCCESS":
                # Tracks are in data.response.sunoData[] with audioUrl (camelCase)
                response_obj = inner.get("response") or {}
                tracks = response_obj.get("sunoData", [])
                if isinstance(tracks, list) and tracks:
                    audio_url = tracks[0].get("audioUrl") or tracks[0].get("audio_url") or ""
                    if audio_url:
                        duration = tracks[0].get("duration")
                        logger.info(f"Suno: task completed after {elapsed}s, duration={duration}s")
                        return audio_url
                    else:
                        logger.warning(f"Suno: SUCCESS but no audioUrl in tracks: {tracks[0].keys()}")
                
                # Also try legacy format data.data[]
                legacy_tracks = inner.get("data", [])
                if isinstance(legacy_tracks, list) and legacy_tracks:
                    audio_url = legacy_tracks[0].get("audio_url") or legacy_tracks[0].get("audioUrl") or ""
                    if audio_url:
                        logger.info(f"Suno: task completed (legacy format) after {elapsed}s")
                        return audio_url

                logger.warning(f"Suno: SUCCESS but no audio URL found in response")
                return ""

            elif status == "FAILED":
                err = inner.get("errorMessage") or inner.get("errorCode") or "unknown"
                logger.warning(f"Suno: task failed after {elapsed}s, error: {err}")
                return ""

            # Still PENDING, continue polling
            if elapsed % 30 == 0:
                logger.info(f"Suno: still generating... ({elapsed}s), status={status}")

        except Exception as e:
            logger.warning(f"Suno poll error: {e}")
            # Continue polling on transient errors

    logger.warning(f"Suno: timed out after {max_wait}s")
    return ""
