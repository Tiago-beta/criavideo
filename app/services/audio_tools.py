"""
Audio tools for karaoke flows.

Strategy:
1) Prefer Olevita Demucs separation API (async job + playback mix without vocals).
2) Fallback to legacy/custom vocal-removal endpoint when configured.
3) Final fallback to local FFmpeg center-channel attenuation.
"""
import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
OLEVITA_JOB_TIMEOUT_SECONDS = 20 * 60
OLEVITA_JOB_POLL_SECONDS = 3


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


def _levita_auth_headers(auth_token: str = "") -> dict[str, str]:
    token = (auth_token or settings.levita_api_token or "").strip()
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def _levita_base_url() -> str:
    return (settings.levita_url or "https://levita.pro").strip().rstrip("/")


def _to_absolute_url(base_url: str, maybe_url: str) -> str:
    value = str(maybe_url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return urljoin(base_url + "/", value.lstrip("/"))


async def _download_with_optional_auth(url: str, output_path: str, headers: dict[str, str]) -> str:
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        Path(output_path).write_bytes(resp.content)
    return output_path


async def _try_remove_vocals_with_olevita_demucs(input_path: str, output_path: str, auth_token: str = "") -> str:
    base_url = _levita_base_url()
    start_url = f"{base_url}/api/separate/start"
    headers = _levita_auth_headers(auth_token)

    if "Authorization" not in headers:
        logger.warning("Skipping Olevita Demucs: no auth token available")
        return ""

    logger.info(f"Trying Olevita Demucs separation: {start_url}")

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            with open(input_path, "rb") as f:
                start_resp = await client.post(
                    start_url,
                    headers=headers,
                    files={"audio": (Path(input_path).name, f, "application/octet-stream")},
                )

            if start_resp.status_code >= 400:
                logger.warning(f"Olevita separate/start failed ({start_resp.status_code}): {start_resp.text[:240]}")
                return ""

            try:
                start_payload = start_resp.json()
            except Exception:
                logger.warning("Olevita separate/start returned non-JSON response")
                return ""

            # Some deployments may respond synchronously with final payload.
            job_payload = start_payload
            job_id = str(start_payload.get("jobId") or start_payload.get("job_id") or "").strip()

            if job_id:
                status_url = f"{base_url}/api/separate/status/{job_id}"
                deadline = time.monotonic() + OLEVITA_JOB_TIMEOUT_SECONDS

                while time.monotonic() < deadline:
                    status_resp = await client.get(status_url, headers=headers)
                    if status_resp.status_code >= 400:
                        logger.warning(f"Olevita status check failed ({status_resp.status_code}): {status_resp.text[:240]}")
                        return ""

                    try:
                        job_payload = status_resp.json()
                    except Exception:
                        logger.warning("Olevita status endpoint returned non-JSON response")
                        return ""

                    status = str(job_payload.get("status") or "").strip().lower()
                    if status == "completed":
                        break
                    if status == "failed":
                        logger.warning(f"Olevita Demucs job failed: {job_payload.get('error')}")
                        return ""

                    await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                else:
                    logger.warning("Olevita Demucs job timed out while waiting for completion")
                    return ""

            track_id = job_payload.get("trackId") or job_payload.get("track_id")
            if track_id:
                mix_url = f"{base_url}/api/mix/{track_id}?mode=playback"
                try:
                    await _download_with_optional_auth(mix_url, output_path, headers)
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        return output_path
                except Exception as mix_err:
                    logger.warning(f"Failed to download Olevita playback mix for track {track_id}: {mix_err}")

            stems = job_payload.get("stems") or {}
            if isinstance(stems, dict):
                for key in ("no_vocals", "instrumental", "other", "guitar", "piano", "bass", "drums"):
                    stem_url = _to_absolute_url(base_url, stems.get(key))
                    if not stem_url:
                        continue
                    try:
                        await _download_with_optional_auth(stem_url, output_path, headers)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            return output_path
                    except Exception as stem_err:
                        logger.warning(f"Failed to download stem '{key}' from Olevita: {stem_err}")

            logger.warning("Olevita Demucs completed but no usable instrumental output was found")
            return ""
    except Exception as exc:
        logger.warning(f"Olevita Demucs unavailable: {exc}")
        return ""


async def _try_remove_vocals_with_legacy_endpoint(input_path: str, output_path: str, auth_token: str = "") -> str:
    endpoint = (settings.levita_remove_vocals_url or "").strip()
    if not endpoint:
        return ""

    headers = _levita_auth_headers(auth_token)

    logger.info(f"Trying legacy Levita vocal-removal endpoint: {endpoint}")
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
            if url.startswith("/"):
                return await _download_with_optional_auth(_to_absolute_url(_levita_base_url(), url), output_path, headers)

        for key in ("instrumental_path", "path", "file_path"):
            local_path = str(data.get(key) or "").strip()
            if local_path and os.path.exists(local_path):
                shutil.copy2(local_path, output_path)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return output_path

        return ""
    except Exception as exc:
        logger.warning(f"Legacy Levita vocal-removal endpoint unavailable: {exc}")
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


async def remove_vocals_track(input_path: str, project_id: int, auth_token: str = "") -> str:
    """Return an instrumental version of input audio for karaoke flows."""
    out_dir = _ensure_output_dir(project_id)
    output_path = str(out_dir / "instrumental_no_vocals.mp3")

    olevita_path = await _try_remove_vocals_with_olevita_demucs(input_path, output_path, auth_token=auth_token)
    if olevita_path:
        logger.info(f"Vocal removal completed via Olevita Demucs: {olevita_path}")
        return olevita_path

    legacy_path = await _try_remove_vocals_with_legacy_endpoint(input_path, output_path, auth_token=auth_token)
    if legacy_path:
        logger.info(f"Vocal removal completed via legacy Levita endpoint: {legacy_path}")
        return legacy_path

    logger.info("Olevita vocal removal unavailable. Using FFmpeg fallback.")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _remove_vocals_ffmpeg, input_path, output_path)
