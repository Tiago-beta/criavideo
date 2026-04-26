"""
Wan Video — Uses Atlas Cloud API to call Alibaba Wan 2.7
for realistic AI video generation (text-to-video and image-to-video).
"""
import os
import time
import mimetypes
import logging
import asyncio
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ATLAS_VIDEO_API_BASE_URL = (settings.atlascloud_api_base_url or "https://api.atlascloud.ai/api/v1").rstrip("/")
WAN_T2V_MODEL = (settings.atlascloud_wan_t2v_model or "alibaba/wan-2.7/text-to-video").strip()
WAN_I2V_MODEL = (settings.atlascloud_wan_i2v_model or "alibaba/wan-2.7/image-to-video").strip()
WAN_DEFAULT_RESOLUTION = "720P"
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
                    raise RuntimeError("Wan 2.7 esta com alta demanda no momento (429).")
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
            uploaded_url = ""
            if isinstance(data, dict):
                uploaded_url = str(data.get("url") or "").strip()
                if not uploaded_url:
                    inner = data.get("data")
                    if isinstance(inner, dict):
                        uploaded_url = str(inner.get("url") or "").strip()

            if not uploaded_url:
                raise RuntimeError("Upload da imagem de referencia retornou URL vazia")

            return uploaded_url

    raise RuntimeError("Nao foi possivel enviar a imagem de referencia para o Atlas Cloud")


async def generate_wan_video(
    prompt: str,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    image_path: str | None = None,
    timeout_seconds: int = 900,
    on_progress=None,
) -> str:
    """Generate a realistic video using Wan 2.7 via Atlas Cloud.

    If image_path is provided, uses image-to-video.
    Otherwise, uses text-to-video.

    Returns the local path to the downloaded MP4 video.
    """
    api_key = _atlas_api_key()
    if not api_key:
        raise RuntimeError("ATLASCLOUD_API_KEY not configured")

    wan_duration = max(2, min(int(duration or 5), 15))
    resolved_aspect = _resolve_aspect_ratio(aspect_ratio)

    submit_url = f"{ATLAS_VIDEO_API_BASE_URL}/model/generateVideo"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Choose model based on whether we have a reference image.
    use_i2v = image_path and os.path.exists(image_path)
    model_id = WAN_I2V_MODEL if use_i2v else WAN_T2V_MODEL

    payload = {
        "model": model_id,
        "prompt": prompt,
        "duration": wan_duration,
        "aspect_ratio": resolved_aspect,
        "resolution": WAN_DEFAULT_RESOLUTION,
        "prompt_extend": False,
    }

    # Add reference image URL for image-to-video.
    if use_i2v:
        uploaded_image_url = await _upload_media_to_atlas(image_path, api_key)
        payload["image_url"] = uploaded_image_url
        logger.info("Wan 2.7 image-to-video: uploaded %s", image_path)

    # Step 1: Submit async job.
    prediction_id = ""
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(5):
            try:
                resp = await client.post(submit_url, headers=headers, json=payload)
            except httpx.RequestError as e:
                if attempt >= 4:
                    raise RuntimeError(f"Falha de conexao ao iniciar Wan 2.7: {e}")
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
                    raise RuntimeError("Wan 2.7 esta com alta demanda no momento (429).")
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
                raise RuntimeError(f"Erro ao iniciar Wan 2.7 (HTTP {resp.status_code}): {details}")

            response_payload = resp.json() if resp.content else {}
            data_node = response_payload.get("data") if isinstance(response_payload, dict) else None
            prediction_id = str((data_node or {}).get("id") or response_payload.get("id") or "").strip()
            if not prediction_id:
                raise RuntimeError("Atlas Cloud nao retornou prediction id para o Wan 2.7")
            break

    if not prediction_id:
        raise RuntimeError("Nao foi possivel iniciar a geracao no Wan 2.7.")

    status = "processing"
    mode = "I2V" if use_i2v else "T2V"
    logger.info("Wan 2.7 %s job submitted: %s", mode, prediction_id)

    if on_progress:
        await on_progress(20, "Gerando video realista com Wan 2.7...")

    # Step 2: Poll for completion.
    poll_url = f"{ATLAS_VIDEO_API_BASE_URL}/model/prediction/{prediction_id}"
    start_time = time.time()
    last_progress = 20

    async with httpx.AsyncClient(timeout=60) as client:
        while (time.time() - start_time) < timeout_seconds:
            try:
                resp = await client.get(poll_url, headers={"Authorization": f"Bearer {api_key}"})
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
                raise RuntimeError(f"Erro ao consultar status do Wan 2.7 (HTTP {resp.status_code}): {details}")

            data = resp.json() if resp.content else {}
            data_node = data.get("data") if isinstance(data, dict) else {}
            status = str((data_node or {}).get("status") or data.get("status") or "").strip().lower()

            if status in {"completed", "succeeded", "success"}:
                outputs = (data_node or {}).get("outputs")
                if isinstance(outputs, list) and outputs:
                    video_url = str(outputs[0]).strip()
                elif isinstance(outputs, str):
                    video_url = outputs.strip()
                else:
                    fallback_output = (data_node or {}).get("output") or data.get("output")
                    video_url = str(fallback_output or "").strip()
                if not video_url:
                    raise RuntimeError("Wan 2.7 returned empty output")
                break
            elif status in {"failed", "error", "canceled", "cancelled"}:
                error = (data_node or {}).get("error") or data.get("error") or "Unknown error"
                raise RuntimeError(f"Wan 2.7 generation failed: {error}")

            # Update progress based on elapsed time
            elapsed = time.time() - start_time
            progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
            if progress > last_progress and on_progress:
                last_progress = progress
                await on_progress(progress, "Gerando video realista com Wan 2.7...")

            await asyncio.sleep(5)
        else:
            raise TimeoutError(f"Wan 2.7 generation timed out after {timeout_seconds}s")

    if on_progress:
        await on_progress(80, "Baixando video gerado...")

    # Step 3: Download the video.
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        downloaded = False
        for attempt in range(4):
            try:
                resp = await client.get(video_url)
            except httpx.RequestError as e:
                if attempt >= 3:
                    raise RuntimeError(f"Falha ao baixar video gerado: {e}")
                wait_s = min(12, 2 ** (attempt + 1))
                logger.warning(
                    "Wan download request error (attempt %d/4): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 3:
                    raise RuntimeError("Wan 2.7 esta com alta demanda no momento (429).")
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                logger.warning(
                    "Wan download rate-limited (attempt %d/4). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)
            downloaded = True
            break

        if not downloaded:
            raise RuntimeError("Nao foi possivel baixar o video do Wan 2.7.")

    file_size = os.path.getsize(output_path)
    logger.info(f"Wan 2.7 video downloaded: {output_path} ({file_size} bytes)")
    return output_path
