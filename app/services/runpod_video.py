"""
Wan Video — Uses Atlas Cloud API to call Alibaba Wan
for realistic AI video generation (text-to-video and image-to-video).
"""
import os
import time
import mimetypes
import logging
import asyncio
import base64
import subprocess
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ATLAS_VIDEO_API_BASE_URL = (settings.atlascloud_api_base_url or "https://api.atlascloud.ai/api/v1").rstrip("/")
WAN_T2V_MODEL = (settings.atlascloud_wan_t2v_model or "alibaba/wan-2.7/text-to-video").strip()
WAN_I2V_MODEL = (settings.atlascloud_wan_i2v_model or "alibaba/wan-2.6/image-to-video").strip()
WAN_DEFAULT_RESOLUTION = "720p"
WAN_I2V_ALLOWED_DURATIONS = (5, 10, 15)
_ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}


def _atlas_api_key() -> str:
    key = (settings.atlascloud_api_key or "").strip()
    if key:
        return key
    return (os.getenv("ATLASCLOUD_API_KEY") or "").strip()


def _resolve_aspect_ratio(aspect_ratio: str) -> str:
    candidate = str(aspect_ratio or "16:9").strip()
    if candidate in _ALLOWED_ASPECT_RATIOS:
        return candidate
    return "16:9"


def _extract_atlas_error_message(resp: httpx.Response) -> str:
    body_text = (resp.text or "").strip()
    try:
        payload = resp.json()
    except Exception:
        return body_text

    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            nested = detail.get("message") or detail.get("error")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        if isinstance(detail, list) and detail:
            parts: list[str] = []
            for item in detail:
                if isinstance(item, str):
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    msg = item.get("msg") or item.get("message") or str(item)
                    parts.append(str(msg).strip())
                else:
                    parts.append(str(item).strip())
            joined = " | ".join(part for part in parts if part)
            if joined:
                return joined

        data = payload.get("data")
        if isinstance(data, dict):
            nested = data.get("error") or data.get("message")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()

    return body_text


def _retry_delay_from_header(retry_after: str | None, default_seconds: int = 5) -> int:
    if not retry_after:
        return default_seconds
    try:
        return max(1, min(int(float(retry_after)), 90))
    except Exception:
        return default_seconds


def _file_to_data_uri(file_path: str) -> str:
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as source:
        encoded = base64.b64encode(source.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_upload_reference(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""

    url_keys = (
        "url",
        "image",
        "image_url",
        "file_url",
        "media_url",
        "public_url",
        "secure_url",
        "download_url",
        "asset",
        "asset_ref",
    )
    id_keys = ("asset_id", "assetId", "media_id", "file_id", "id")

    nodes: list[dict] = [payload]
    data_node = payload.get("data")
    if isinstance(data_node, dict):
        nodes.append(data_node)

    for node in nodes:
        for key in url_keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    for node in nodes:
        for key in id_keys:
            value = node.get(key)
            if value is None:
                continue
            raw = str(value).strip()
            if not raw:
                continue
            if raw.startswith(("asset://", "http://", "https://", "data:")):
                return raw
            return f"asset://{raw}"

    return ""


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _is_http_url(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _looks_like_video_url(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    if not _is_http_url(lowered):
        return False
    if any(ext in lowered for ext in (".mp4", ".mov", ".webm", ".m3u8", "/video")):
        return True
    return True


def _collect_video_url_candidates(node, output: list[str]) -> None:
    if isinstance(node, str):
        candidate = node.strip()
        if _looks_like_video_url(candidate):
            output.append(candidate)
        return

    if isinstance(node, list):
        for item in node:
            _collect_video_url_candidates(item, output)
        return

    if isinstance(node, dict):
        preferred_keys = (
            "video_url",
            "video",
            "output",
            "outputs",
            "url",
            "urls",
            "result",
            "data",
            "files",
            "artifacts",
        )
        for key in preferred_keys:
            if key in node:
                _collect_video_url_candidates(node.get(key), output)

        for key, value in node.items():
            if key in preferred_keys:
                continue
            key_lower = str(key).lower()
            if any(token in key_lower for token in ("video", "output", "result", "file", "url")):
                _collect_video_url_candidates(value, output)


async def _fetch_result_video_candidates(prediction_id: str, api_key: str) -> list[str]:
    endpoint = f"{ATLAS_VIDEO_API_BASE_URL}/model/result/{prediction_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(endpoint, headers=headers)
    except Exception as e:
        logger.warning("Wan result lookup request failed: %s", e)
        return []

    if resp.is_error:
        logger.warning("Wan result lookup failed (HTTP %s)", resp.status_code)
        return []

    payload = resp.json() if resp.content else {}
    candidates: list[str] = []
    _collect_video_url_candidates(payload, candidates)
    return _dedupe_preserve_order(candidates)


def _file_has_audio_stream(file_path: str) -> bool:
    if not file_path or not os.path.exists(file_path):
        return False

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return False

    if proc.returncode != 0:
        return False
    return "audio" in (proc.stdout or "").lower()


def _resolve_i2v_duration(duration: int) -> int:
    raw = max(1, int(duration or 5))
    if raw in WAN_I2V_ALLOWED_DURATIONS:
        return raw
    return min(WAN_I2V_ALLOWED_DURATIONS, key=lambda candidate: (abs(candidate - raw), candidate))


async def _upload_media_to_atlas(file_path: str, api_key: str) -> str:
    if not file_path or not os.path.exists(file_path):
        raise RuntimeError("Arquivo de referencia nao encontrado para upload")

    endpoint = f"{ATLAS_VIDEO_API_BASE_URL}/model/uploadMedia"
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(4):
            try:
                with open(file_path, "rb") as source:
                    resp = await client.post(
                        endpoint,
                        headers=headers,
                        files={"file": (os.path.basename(file_path), source, mime_type)},
                    )
            except httpx.RequestError as e:
                if attempt >= 3:
                    raise RuntimeError(f"Falha no upload da imagem de referencia: {e}")
                wait_s = min(12, 2 ** (attempt + 1))
                logger.warning(
                    "Wan upload request error (attempt %d/4): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 3:
                    raise RuntimeError("Wan esta com alta demanda no momento (429).")
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                logger.warning(
                    "Wan upload rate-limited (attempt %d/4). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                message = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Falha no upload da imagem de referencia (HTTP {resp.status_code}): {message}")

            data = resp.json() if resp.content else {}
            uploaded_reference = _extract_upload_reference(data) if isinstance(data, dict) else ""
            if uploaded_reference:
                return uploaded_reference

            # Atlas can also receive Base64 directly in the image field.
            logger.warning("Wan upload sem URL/asset. Usando fallback Base64 inline.")
            return _file_to_data_uri(file_path)

    raise RuntimeError("Nao foi possivel enviar a imagem de referencia para o Atlas Cloud")


async def generate_wan_video(
    prompt: str,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    image_path: str | None = None,
    generate_audio: bool = True,
    timeout_seconds: int = 900,
    on_progress=None,
) -> str:
    """Generate a realistic video using Wan via Atlas Cloud.

    If image_path is provided, uses image-to-video.
    Otherwise, uses text-to-video.

    For I2V, defaults to Wan 2.6 model and Atlas duration presets (5/10/15).

    Returns the local path to the downloaded MP4 video.
    """
    api_key = _atlas_api_key()
    if not api_key:
        raise RuntimeError("ATLASCLOUD_API_KEY not configured")

    resolved_aspect = _resolve_aspect_ratio(aspect_ratio)

    submit_url = f"{ATLAS_VIDEO_API_BASE_URL}/model/generateVideo"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Choose model based on whether we have a reference image.
    use_i2v = bool(image_path and os.path.exists(image_path))
    model_id = WAN_I2V_MODEL if use_i2v else WAN_T2V_MODEL
    wan_duration = _resolve_i2v_duration(duration) if use_i2v else max(2, min(int(duration or 5), 15))

    payload = {
        "model": model_id,
        "prompt": prompt,
        "duration": wan_duration,
        "ratio": resolved_aspect,
        "resolution": WAN_DEFAULT_RESOLUTION,
        "prompt_extend": False,
        "generate_audio": bool(generate_audio),
    }

    # Add reference image URL for image-to-video.
    if use_i2v:
        uploaded_image_ref = await _upload_media_to_atlas(image_path, api_key)
        payload["image"] = uploaded_image_ref
        logger.info("Wan image-to-video: uploaded %s", image_path)

    # Step 1: Submit async job.
    prediction_id = ""
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(5):
            try:
                resp = await client.post(submit_url, headers=headers, json=payload)
            except httpx.RequestError as e:
                if attempt >= 4:
                    raise RuntimeError(f"Falha de conexao ao iniciar Wan: {e}")
                wait_s = min(20, 2 ** attempt)
                logger.warning(
                    "Wan create request error (attempt %d/5): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 4:
                    raise RuntimeError("Wan esta com alta demanda no momento (429).")
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(30, 2 ** (attempt + 2)))
                logger.warning(
                    "Wan create rate-limited (attempt %d/5). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                details = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Erro ao iniciar Wan (HTTP {resp.status_code}): {details}")

            response_payload = resp.json() if resp.content else {}
            data_node = response_payload.get("data") if isinstance(response_payload, dict) else None
            prediction_id = str((data_node or {}).get("id") or response_payload.get("id") or "").strip()
            if not prediction_id:
                raise RuntimeError("Atlas Cloud nao retornou prediction id para o Wan")
            break

    if not prediction_id:
        raise RuntimeError("Nao foi possivel iniciar a geracao no Wan.")

    mode = "I2V" if use_i2v else "T2V"
    logger.info("Wan %s job submitted: %s", mode, prediction_id)

    if on_progress:
        await on_progress(20, "Gerando video realista com Wan...")

    # Step 2: Poll for completion.
    poll_url = f"{ATLAS_VIDEO_API_BASE_URL}/model/prediction/{prediction_id}"
    poll_headers = {"Authorization": f"Bearer {api_key}"}
    candidate_urls: list[str] = []
    start_time = time.time()
    last_progress = 20

    async with httpx.AsyncClient(timeout=60) as client:
        while (time.time() - start_time) < timeout_seconds:
            try:
                resp = await client.get(poll_url, headers=poll_headers)
            except httpx.RequestError as e:
                logger.warning("Wan poll request error: %s", e)
                await asyncio.sleep(5)
                continue

            if resp.status_code == 429:
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=6)
                logger.warning("Wan poll rate-limited. Retrying in %ds", wait_s)
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                details = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Erro ao consultar status do Wan (HTTP {resp.status_code}): {details}")

            data = resp.json() if resp.content else {}
            data_node = data.get("data") if isinstance(data, dict) else {}
            status = str((data_node or {}).get("status") or data.get("status") or "").strip().lower()

            if status in {"completed", "succeeded", "success"}:
                poll_candidates: list[str] = []
                _collect_video_url_candidates(data_node, poll_candidates)
                _collect_video_url_candidates(data, poll_candidates)
                candidate_urls = _dedupe_preserve_order(poll_candidates)
                if not candidate_urls:
                    raise RuntimeError("Wan returned empty output")
                break
            elif status in {"failed", "error", "canceled", "cancelled"}:
                error = (data_node or {}).get("error") or data.get("error") or "Unknown error"
                raise RuntimeError(f"Wan generation failed: {error}")

            # Update progress based on elapsed time
            elapsed = time.time() - start_time
            progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
            if progress > last_progress and on_progress:
                last_progress = progress
                await on_progress(progress, "Gerando video realista com Wan...")

            await asyncio.sleep(5)
        else:
            raise TimeoutError(f"Wan generation timed out after {timeout_seconds}s")

    result_candidates = await _fetch_result_video_candidates(prediction_id, api_key)
    if result_candidates:
        candidate_urls = _dedupe_preserve_order(result_candidates + candidate_urls)

    if not candidate_urls:
        raise RuntimeError("Wan nao retornou URL de video valida")

    if on_progress:
        await on_progress(80, "Baixando video gerado...")

    # Step 3: Download the video.
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        selected_url = ""
        downloaded_without_audio = False
        for idx, video_url in enumerate(candidate_urls):
            downloaded = False
            for attempt in range(4):
                try:
                    resp = await client.get(video_url)
                except httpx.RequestError as e:
                    if attempt >= 3:
                        logger.warning("Falha ao baixar Wan URL %s: %s", video_url, e)
                        break
                    wait_s = min(12, 2 ** (attempt + 1))
                    logger.warning(
                        "Wan download request error (url %d/%d, attempt %d/4): %s. Retrying in %ds",
                        idx + 1,
                        len(candidate_urls),
                        attempt + 1,
                        e,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue

                if resp.status_code == 429:
                    if attempt >= 3:
                        logger.warning("Wan candidate URL rate-limited too many times: %s", video_url)
                        break
                    wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                    logger.warning(
                        "Wan rate-limited on download (url %d/%d, attempt %d/4). Retrying in %ds",
                        idx + 1,
                        len(candidate_urls),
                        attempt + 1,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue

                resp.raise_for_status()
                content_type = str(resp.headers.get("Content-Type") or "").lower().strip()
                if content_type.startswith("audio/"):
                    logger.warning("Skipping Wan non-video candidate URL (%s): %s", content_type, video_url)
                    break

                with open(output_path, "wb") as f:
                    f.write(resp.content)
                downloaded = True
                break

            if not downloaded:
                continue

            if generate_audio and not _file_has_audio_stream(output_path):
                downloaded_without_audio = True
                logger.warning(
                    "Wan candidate URL %d/%d has no audio stream. Trying next candidate.",
                    idx + 1,
                    len(candidate_urls),
                )
                continue

            selected_url = video_url
            break

        if not selected_url and not downloaded_without_audio:
            raise RuntimeError("Nao foi possivel baixar o video do Wan.")

        if selected_url:
            logger.info("Wan downloaded using candidate URL: %s", selected_url)
        elif downloaded_without_audio:
            logger.warning("Wan video baixado sem trilha de audio apos testar todas as URLs candidatas.")

    file_size = os.path.getsize(output_path)
    logger.info(f"Wan video downloaded: {output_path} ({file_size} bytes)")
    return output_path
