"""Atlas avatar video generation via the Atlas Cloud video API."""
import os
import time
import mimetypes
import logging
import asyncio
import base64

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ATLAS_VIDEO_API_BASE_URL = (settings.atlascloud_api_base_url or "https://api.atlascloud.ai/api/v1").rstrip("/")
DEFAULT_AVATAR_MODEL = (settings.atlascloud_avatar_model or "kwaivgi/kling-v2.6-std/avatar").strip()
_ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}


def _atlas_api_key() -> str:
    key = (settings.atlascloud_api_key or "").strip()
    if key:
        return key
    return (os.getenv("ATLASCLOUD_API_KEY") or "").strip()


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
        "audio",
        "audio_url",
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
        logger.warning("Avatar result lookup request failed: %s", e)
        return []

    if resp.is_error:
        logger.warning("Avatar result lookup failed (HTTP %s)", resp.status_code)
        return []

    payload = resp.json() if resp.content else {}
    candidates: list[str] = []
    _collect_video_url_candidates(payload, candidates)
    return _dedupe_preserve_order(candidates)


async def _upload_media_to_atlas(file_path: str, api_key: str, engine_label: str = "Avatar 3.1 Plus") -> str:
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
                    raise RuntimeError(f"Falha no upload de media para o {engine_label}: {e}")
                wait_s = min(12, 2 ** (attempt + 1))
                logger.warning(
                    "Avatar upload request error (attempt %d/4): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 3:
                    raise RuntimeError(f"{engine_label} esta com alta demanda no momento (429).")
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                logger.warning(
                    "Avatar upload rate-limited (attempt %d/4). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                message = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Falha no upload de media do {engine_label} (HTTP {resp.status_code}): {message}")

            data = resp.json() if resp.content else {}
            uploaded_reference = _extract_upload_reference(data) if isinstance(data, dict) else ""
            if uploaded_reference:
                return uploaded_reference

            logger.warning("Avatar upload sem URL/asset. Usando fallback Base64 inline.")
            return _file_to_data_uri(file_path)

    raise RuntimeError("Nao foi possivel enviar a media de referencia para o Atlas Cloud")


async def generate_avatar_video(
    prompt: str,
    image_path: str,
    audio_source: str,
    output_path: str,
    aspect_ratio: str = "16:9",
    timeout_seconds: int = 900,
    on_progress=None,
    model_id_override: str | None = None,
    engine_label: str = "Avatar 3.1 Plus",
    resolution: str | None = None,
) -> str:
    engine_name = str(engine_label or "Avatar 3.1 Plus").strip() or "Avatar 3.1 Plus"
    selected_model = str(model_id_override or DEFAULT_AVATAR_MODEL).strip() or DEFAULT_AVATAR_MODEL
    api_key = _atlas_api_key()
    if not api_key:
        raise RuntimeError("ATLASCLOUD_API_KEY not configured")
    if not image_path or not os.path.exists(image_path):
        raise RuntimeError(f"{engine_name} exige uma imagem de referencia valida")
    if not audio_source:
        raise RuntimeError(f"{engine_name} exige um audio valido")

    submit_url = f"{ATLAS_VIDEO_API_BASE_URL}/model/generateVideo"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    uploaded_image_ref = await _upload_media_to_atlas(image_path, api_key, engine_name)
    if _is_http_url(audio_source):
        uploaded_audio_ref = audio_source.strip()
    elif os.path.exists(audio_source):
        uploaded_audio_ref = await _upload_media_to_atlas(audio_source, api_key, engine_name)
    else:
        raise RuntimeError(f"Audio do {engine_name} nao foi encontrado")

    payload = {
        "model": selected_model,
        "audio": uploaded_audio_ref,
        "image": uploaded_image_ref,
    }
    cleaned_prompt = str(prompt or "").strip()
    if cleaned_prompt:
        payload["prompt"] = cleaned_prompt
    cleaned_resolution = str(resolution or "").strip().lower()
    if cleaned_resolution:
        payload["resolution"] = cleaned_resolution

    prediction_id = ""
    selected_variant = "default"
    async with httpx.AsyncClient(timeout=120) as client:
        last_error_message = ""
        for attempt in range(5):
            try:
                resp = await client.post(submit_url, headers=headers, json=payload)
            except httpx.RequestError as e:
                if attempt >= 4:
                    raise RuntimeError(f"Falha de conexao ao iniciar {engine_name}: {e}")
                wait_s = min(20, 2 ** attempt)
                logger.warning(
                    "Avatar create request error (%s, attempt %d/5): %s. Retrying in %ds",
                    selected_variant,
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 4:
                    raise RuntimeError(f"{engine_name} esta com alta demanda no momento (429).")
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(30, 2 ** (attempt + 2)))
                logger.warning(
                    "Avatar rate-limited on create (%s, attempt %d/5). Retrying in %ds",
                    selected_variant,
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                details = _extract_atlas_error_message(resp)
                last_error_message = f"Erro ao iniciar {engine_name} (HTTP {resp.status_code}): {details}"
                raise RuntimeError(last_error_message)

            response_payload = resp.json() if resp.content else {}
            data_node = response_payload.get("data") if isinstance(response_payload, dict) else None
            prediction_id = str((data_node or {}).get("id") or response_payload.get("id") or "").strip()
            if not prediction_id:
                raise RuntimeError(f"Atlas Cloud nao retornou prediction id para o {engine_name}")
            break

        if not prediction_id and last_error_message:
            raise RuntimeError(last_error_message)

    if not prediction_id:
        raise RuntimeError(f"Nao foi possivel iniciar a geracao no {engine_name}.")

    logger.info("%s prediction created: %s (variant=%s, model=%s)", engine_name, prediction_id, selected_variant or "default", selected_model)

    if on_progress:
        await on_progress(20, f"Gerando video com {engine_name}...")

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
                logger.warning("Avatar poll request error: %s", e)
                await asyncio.sleep(5)
                continue

            if resp.status_code == 429:
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=6)
                logger.warning("Avatar poll rate-limited. Retrying in %ds", wait_s)
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                details = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Erro ao consultar status do {engine_name} (HTTP {resp.status_code}): {details}")

            data = resp.json() if resp.content else {}
            data_node = data.get("data") if isinstance(data, dict) else {}
            status = str((data_node or {}).get("status") or data.get("status") or "").strip().lower()

            if status in {"completed", "succeeded", "success"}:
                poll_candidates: list[str] = []
                _collect_video_url_candidates(data_node, poll_candidates)
                _collect_video_url_candidates(data, poll_candidates)
                candidate_urls = _dedupe_preserve_order(poll_candidates)
                if not candidate_urls:
                    raise RuntimeError(f"{engine_name} returned empty output")
                break
            if status in {"failed", "error", "canceled", "cancelled"}:
                error = (data_node or {}).get("error") or data.get("error") or "Unknown error"
                raise RuntimeError(f"{engine_name} generation failed: {error}")

            elapsed = time.time() - start_time
            progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
            if progress > last_progress and on_progress:
                last_progress = progress
                await on_progress(progress, f"Gerando video com {engine_name}...")

            await asyncio.sleep(5)
        else:
            raise TimeoutError(f"{engine_name} generation timed out after {timeout_seconds}s")

    result_candidates = await _fetch_result_video_candidates(prediction_id, api_key)
    if result_candidates:
        candidate_urls = _dedupe_preserve_order(result_candidates + candidate_urls)

    if not candidate_urls:
        raise RuntimeError(f"{engine_name} nao retornou URL de video valida")

    if on_progress:
        await on_progress(80, "Baixando video gerado...")

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        selected_url = ""
        for idx, video_url in enumerate(candidate_urls):
            for attempt in range(4):
                try:
                    resp = await client.get(video_url)
                except httpx.RequestError as e:
                    if attempt >= 3:
                        logger.warning("Falha ao baixar Avatar URL %s: %s", video_url, e)
                        break
                    wait_s = min(12, 2 ** (attempt + 1))
                    logger.warning(
                        "Avatar download request error (url %d/%d, attempt %d/4): %s. Retrying in %ds",
                        idx + 1,
                        len(candidate_urls),
                        attempt + 1,
                        e,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue

                if resp.status_code == 429:
                    wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                    logger.warning(
                        "Avatar download rate-limited (url %d/%d, attempt %d/4). Retrying in %ds",
                        idx + 1,
                        len(candidate_urls),
                        attempt + 1,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue

                if resp.is_error:
                    logger.warning("Avatar download failed (HTTP %s) for %s", resp.status_code, video_url)
                    break

                with open(output_path, "wb") as f:
                    f.write(resp.content)

                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    selected_url = video_url
                    break
            if selected_url:
                break

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Falha ao baixar o video final do {engine_name}")

    logger.info("%s video ready: %s", engine_name, output_path)
    return output_path