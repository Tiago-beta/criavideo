"""
Audio tools for karaoke flows.

Strategy:
1) Prefer Olevita Demucs separation API (async job + playback mix without vocals).
2) Fallback to legacy/custom vocal-removal endpoint when configured.
3) Final fallback to local FFmpeg center-channel attenuation.
"""
import asyncio
import inspect
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urljoin
from typing import Callable, Awaitable

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
OLEVITA_SYNC_TIMEOUT_SECONDS = 30 * 60
OLEVITA_JOB_TIMEOUT_SECONDS = 45 * 60
OLEVITA_JOB_POLL_SECONDS = 3
OLEVITA_JOB_FAIL_GRACE_SECONDS = 5 * 60
ProgressCallback = Callable[[int, str], Awaitable[None] | None]


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


async def _emit_progress(progress_callback: ProgressCallback | None, progress: int, message: str) -> None:
    if not progress_callback:
        return
    try:
        safe_progress = max(0, min(100, int(progress)))
        result = progress_callback(safe_progress, message)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.debug(f"Progress callback failed: {exc}")


def _normalized_bearer_token(auth_token: str = "") -> str:
    token = (auth_token or settings.levita_api_token or "").strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    return token


def _mix_candidate_urls(base_url: str, track_id: str | int, auth_token: str = "") -> list[str]:
    urls = [f"{base_url}/api/mix/{track_id}?mode=playback"]
    token = _normalized_bearer_token(auth_token)
    if token:
        urls.append(f"{base_url}/api/mix/{track_id}?mode=playback&token={quote(token)}")
    return urls


async def _download_and_merge_stems(
    stem_urls: dict[str, str],
    headers: dict[str, str],
    output_path: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Download individual non-vocal stems and merge them with FFmpeg amix."""
    import tempfile
    temp_files: list[str] = []
    try:
        await _emit_progress(progress_callback, 89, "Removendo voz: baixando stems individuais...")
        for stem_name, stem_url in stem_urls.items():
            try:
                tmp = tempfile.NamedTemporaryFile(
                    suffix=f"_{stem_name}.wav", delete=False
                )
                tmp.close()
                await _download_with_optional_auth(stem_url, tmp.name, headers)
                if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
                    temp_files.append(tmp.name)
                else:
                    os.unlink(tmp.name)
            except Exception as dl_err:
                logger.warning(f"Failed to download stem '{stem_name}': {dl_err}")

        if not temp_files:
            return ""

        await _emit_progress(
            progress_callback, 93, f"Removendo voz: mixando {len(temp_files)} stems..."
        )

        if len(temp_files) == 1:
            shutil.move(temp_files[0], output_path)
            temp_files.clear()
        else:
            inputs: list[str] = []
            for tf in temp_files:
                inputs.extend(["-i", tf])
            cmd = [
                "ffmpeg", "-y", *inputs,
                "-filter_complex",
                f"amix=inputs={len(temp_files)}:duration=longest:normalize=0",
                "-ac", "2", "-ar", "44100", output_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
            if proc.returncode != 0:
                logger.warning(f"FFmpeg amix failed: {proc.stderr[-400:]}")
                return ""

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            await _emit_progress(progress_callback, 96, "Instrumental montado a partir das stems.")
            return output_path
        return ""
    finally:
        for tf in temp_files:
            try:
                os.unlink(tf)
            except OSError:
                pass


async def _extract_olevita_instrumental(
    payload: dict,
    base_url: str,
    headers: dict[str, str],
    output_path: str,
    auth_token: str = "",
    progress_callback: ProgressCallback | None = None,
) -> str:
    track_id = payload.get("trackId") or payload.get("track_id")
    if track_id:
        await _emit_progress(progress_callback, 88, "Removendo voz: baixando instrumental do Levita...")
        for mix_url in _mix_candidate_urls(base_url, track_id, auth_token):
            try:
                await _download_with_optional_auth(mix_url, output_path, headers)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    await _emit_progress(progress_callback, 96, "Instrumental recebido do Levita.")
                    return output_path
            except Exception as mix_err:
                logger.warning(f"Failed to download Olevita playback mix for track {track_id}: {mix_err}")

    stems = payload.get("stems") or {}
    if isinstance(stems, dict):
        # Try pre-mixed no_vocals / instrumental first
        for key in ("no_vocals", "instrumental"):
            stem_url = _to_absolute_url(base_url, stems.get(key))
            if not stem_url:
                continue
            try:
                await _emit_progress(progress_callback, 90, f"Removendo voz: baixando stem {key}...")
                await _download_with_optional_auth(stem_url, output_path, headers)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    await _emit_progress(progress_callback, 96, "Instrumental recebido do Levita.")
                    return output_path
            except Exception as stem_err:
                logger.warning(f"Failed to download Olevita stem '{key}': {stem_err}")

        # Fallback: download all non-vocal stems and merge with FFmpeg
        non_vocal_stems = {
            k: _to_absolute_url(base_url, v)
            for k, v in stems.items()
            if k.lower() != "vocals" and _to_absolute_url(base_url, v)
        }
        if non_vocal_stems:
            merged = await _download_and_merge_stems(
                non_vocal_stems, headers, output_path, progress_callback
            )
            if merged:
                return merged

    for key in ("instrumental_url", "instrumental", "audio_url", "url"):
        candidate_url = _to_absolute_url(base_url, payload.get(key))
        if not candidate_url:
            continue
        try:
            await _emit_progress(progress_callback, 90, "Removendo voz: baixando instrumental...")
            await _download_with_optional_auth(candidate_url, output_path, headers)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                await _emit_progress(progress_callback, 96, "Instrumental recebido do Levita.")
                return output_path
        except Exception as url_err:
            logger.warning(f"Failed to download Olevita instrumental URL from key '{key}': {url_err}")

    return ""


async def _try_remove_vocals_with_olevita_demucs_sync(
    input_path: str,
    output_path: str,
    auth_token: str = "",
    progress_callback: ProgressCallback | None = None,
) -> str:
    base_url = _levita_base_url()
    separate_url = f"{base_url}/api/separate"
    headers = _levita_auth_headers(auth_token)

    if "Authorization" not in headers:
        logger.warning("Skipping Olevita Demucs sync: no auth token available")
        return ""

    logger.info(f"Trying Olevita Demucs sync separation: {separate_url}")
    await _emit_progress(progress_callback, 15, "Removendo voz: enviando audio para o Levita...")
    try:
        async with httpx.AsyncClient(timeout=OLEVITA_SYNC_TIMEOUT_SECONDS, follow_redirects=True) as client:
            with open(input_path, "rb") as f:
                resp = await client.post(
                    separate_url,
                    headers=headers,
                    files={"audio": (Path(input_path).name, f, "application/octet-stream")},
                )

        if resp.status_code >= 400:
            logger.warning(f"Olevita sync separation failed ({resp.status_code}): {resp.text[:240]}")
            return ""

        try:
            payload = resp.json()
        except Exception:
            logger.warning("Olevita sync separation returned non-JSON response")
            return ""

        return await _extract_olevita_instrumental(
            payload,
            base_url,
            headers,
            output_path,
            auth_token=auth_token,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        logger.warning(f"Olevita sync separation unavailable: {exc}")
        return ""


async def _try_remove_vocals_with_olevita_demucs_async(
    input_path: str,
    output_path: str,
    auth_token: str = "",
    progress_callback: ProgressCallback | None = None,
) -> str:
    base_url = _levita_base_url()
    start_url = f"{base_url}/api/separate/start"
    headers = _levita_auth_headers(auth_token)

    if "Authorization" not in headers:
        logger.warning("Skipping Olevita Demucs async: no auth token available")
        return ""

    logger.info(f"Trying Olevita Demucs async separation: {start_url}")
    await _emit_progress(progress_callback, 12, "Removendo voz: iniciando separacao no Levita...")

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

            job_payload = start_payload
            job_id = str(start_payload.get("jobId") or start_payload.get("job_id") or "").strip()

            if job_id:
                status_url = f"{base_url}/api/separate/status/{job_id}"
                deadline = time.monotonic() + OLEVITA_JOB_TIMEOUT_SECONDS
                failed_since = 0.0

                while time.monotonic() < deadline:
                    try:
                        status_resp = await client.get(status_url, headers=headers)
                    except Exception as status_exc:
                        logger.warning(f"Olevita status request failed for job {job_id}: {status_exc}")
                        await _emit_progress(progress_callback, 15, "Removendo voz: aguardando status do Levita...")
                        await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                        continue

                    if status_resp.status_code in (401, 403):
                        logger.warning(f"Olevita status auth failed ({status_resp.status_code}) for job {job_id}")
                        return ""

                    if status_resp.status_code == 404 or status_resp.status_code >= 500:
                        logger.warning(
                            f"Olevita status temporarily unavailable ({status_resp.status_code}) for job {job_id}: {status_resp.text[:240]}"
                        )
                        await _emit_progress(progress_callback, 15, "Removendo voz: fila do Levita em andamento...")
                        await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                        continue

                    if status_resp.status_code >= 400:
                        logger.warning(f"Olevita status check failed ({status_resp.status_code}): {status_resp.text[:240]}")
                        return ""

                    try:
                        job_payload = status_resp.json()
                    except Exception:
                        logger.warning("Olevita status endpoint returned non-JSON response")
                        await _emit_progress(progress_callback, 15, "Removendo voz: aguardando resposta valida do Levita...")
                        await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                        continue

                    remote_progress_raw = job_payload.get("progress")
                    remote_message = str(job_payload.get("message") or "Removendo voz no Levita...").strip()
                    if isinstance(remote_progress_raw, (int, float)):
                        remote_progress = int(remote_progress_raw)
                    else:
                        remote_progress = 0
                    mapped_progress = max(15, min(88, remote_progress))
                    await _emit_progress(progress_callback, mapped_progress, f"Removendo voz: {remote_message}")

                    status = str(job_payload.get("status") or "").strip().lower()
                    if status == "completed":
                        extracted_path = await _extract_olevita_instrumental(
                            job_payload,
                            base_url,
                            headers,
                            output_path,
                            auth_token=auth_token,
                            progress_callback=progress_callback,
                        )
                        if extracted_path:
                            return extracted_path
                        await _emit_progress(progress_callback, 92, "Removendo voz: finalizando arquivos no Levita...")
                        await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                        continue
                    if status in {"failed", "error"}:
                        if not failed_since:
                            failed_since = time.monotonic()
                            await _emit_progress(
                                progress_callback,
                                mapped_progress,
                                "Removendo voz: Levita ainda processando, aguardando nova tentativa...",
                            )
                        if time.monotonic() - failed_since < OLEVITA_JOB_FAIL_GRACE_SECONDS:
                            await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                            continue
                        logger.warning(f"Olevita Demucs job failed after grace period: {job_payload.get('error')}")
                        return ""
                    else:
                        failed_since = 0.0

                    await asyncio.sleep(OLEVITA_JOB_POLL_SECONDS)
                else:
                    logger.warning("Olevita Demucs async job timed out while waiting for completion")
                    return ""

            return await _extract_olevita_instrumental(
                job_payload,
                base_url,
                headers,
                output_path,
                auth_token=auth_token,
                progress_callback=progress_callback,
            )
    except Exception as exc:
        logger.warning(f"Olevita Demucs async unavailable: {exc}")
        return ""


async def _try_remove_vocals_with_olevita_demucs(
    input_path: str,
    output_path: str,
    auth_token: str = "",
    progress_callback: ProgressCallback | None = None,
) -> str:
    # Async flow first gives meaningful progress updates from Olevita.
    async_path = await _try_remove_vocals_with_olevita_demucs_async(
        input_path,
        output_path,
        auth_token=auth_token,
        progress_callback=progress_callback,
    )
    if async_path:
        return async_path

    sync_path = await _try_remove_vocals_with_olevita_demucs_sync(
        input_path,
        output_path,
        auth_token=auth_token,
        progress_callback=progress_callback,
    )
    if sync_path:
        return sync_path

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


async def remove_vocals_track(
    input_path: str,
    project_id: int,
    auth_token: str = "",
    allow_ffmpeg_fallback: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Return an instrumental version of input audio for karaoke flows."""
    out_dir = _ensure_output_dir(project_id)
    output_path = str(out_dir / "instrumental_no_vocals.mp3")

    await _emit_progress(progress_callback, 8, "Removendo voz: preparando arquivo...")

    olevita_path = await _try_remove_vocals_with_olevita_demucs(
        input_path,
        output_path,
        auth_token=auth_token,
        progress_callback=progress_callback,
    )
    if olevita_path:
        logger.info(f"Vocal removal completed via Olevita Demucs: {olevita_path}")
        await _emit_progress(progress_callback, 100, "Voz removida com sucesso.")
        return olevita_path

    legacy_path = await _try_remove_vocals_with_legacy_endpoint(input_path, output_path, auth_token=auth_token)
    if legacy_path:
        logger.info(f"Vocal removal completed via legacy Levita endpoint: {legacy_path}")
        await _emit_progress(progress_callback, 100, "Voz removida com sucesso.")
        return legacy_path

    if not allow_ffmpeg_fallback:
        raise RuntimeError("Olevita vocal removal did not return an instrumental track")

    logger.info("Olevita vocal removal unavailable. Using FFmpeg fallback.")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _remove_vocals_ffmpeg, input_path, output_path)
