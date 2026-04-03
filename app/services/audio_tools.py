"""
Audio Tools - Utilities for karaoke flows with optional vocal removal.

Strategy:
1) If a Levita vocal-removal API URL is configured, use it.
2) Otherwise, use a local FFmpeg center-channel attenuation fallback.
"""
import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _ensure_output_dir(project_id: int) -> Path:
    out_dir = Path(settings.media_dir) / "audio" / str(project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


async def _download_to_path(url: str, output_path: str) -> str:
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        Path(output_path).write_bytes(resp.content)
    return output_path


async def _try_remove_vocals_with_levita(input_path: str, output_path: str) -> str:
    endpoint = (settings.levita_remove_vocals_url or "").strip()
    if not endpoint:
        return ""

    headers = {}
    if settings.levita_api_token:
        headers["Authorization"] = f"Bearer {settings.levita_api_token}"

    logger.info(f"Trying Levita vocal removal endpoint: {endpoint}")
    try:
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            with open(input_path, "rb") as f:
                resp = await client.post(
                    endpoint,
                    headers=headers,
                    files={"file": (Path(input_path).name, f, "application/octet-stream")},
                    data={"mode": "karaoke"},
                )

        if resp.status_code >= 400:
            logger.warning(f"Levita vocal removal failed ({resp.status_code}): {resp.text[:240]}")
            return ""

        content_type = (resp.headers.get("content-type") or "").lower()

        # API can return audio directly.
        if content_type.startswith("audio/"):
            Path(output_path).write_bytes(resp.content)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path
            return ""

        # Or JSON with URL/path fields.
        data = resp.json()
        for key in ("instrumental_url", "url", "instrumental", "audio_url"):
            url = str(data.get(key) or "").strip()
            if url.startswith(("http://", "https://")):
                return await _download_to_path(url, output_path)

        for key in ("instrumental_path", "path", "file_path"):
            local_path = str(data.get(key) or "").strip()
            if local_path and os.path.exists(local_path):
                shutil.copy2(local_path, output_path)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return output_path

        return ""
    except Exception as exc:
        logger.warning(f"Levita vocal removal unavailable: {exc}")
        return ""


def _remove_vocals_ffmpeg(input_path: str, output_path: str) -> str:
    # Approximate center-channel attenuation fallback when stem separation API is unavailable.
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-af",
        "stereotools=mlev=0.03,highpass=f=120,lowpass=f=12000,volume=1.15",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        err = "\n".join(result.stderr.splitlines()[-30:])
        raise RuntimeError(f"FFmpeg vocal reduction failed: {err}")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("FFmpeg vocal reduction did not produce output")

    return output_path


async def remove_vocals_track(input_path: str, project_id: int) -> str:
    """Return an instrumental version of input audio for karaoke flows."""
    out_dir = _ensure_output_dir(project_id)
    output_path = str(out_dir / "instrumental_no_vocals.mp3")

    levita_path = await _try_remove_vocals_with_levita(input_path, output_path)
    if levita_path:
        logger.info(f"Vocal removal completed via Levita API: {levita_path}")
        return levita_path

    logger.info("Levita vocal removal not configured/available. Using FFmpeg fallback.")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _remove_vocals_ffmpeg, input_path, output_path)
