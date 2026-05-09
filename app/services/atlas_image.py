"""Atlas image generation helpers for the new-project image creator modal."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
from typing import Any

import httpx
import openai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ATLAS_IMAGE_API_BASE_URL = (settings.atlascloud_api_base_url or "https://api.atlascloud.ai/api/v1").rstrip("/")
_SUPPORTED_MODELS: dict[str, dict[str, Any]] = {
    "google/nano-banana-pro/text-to-image": {
        "label": "Nano Banana Pro",
        "kind": "text",
        "supports_aspect_ratio": True,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "google/nano-banana-2/text-to-image": {
        "label": "Nano Banana 2",
        "kind": "text",
        "supports_aspect_ratio": True,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "google/nano-banana/text-to-image": {
        "label": "Nano Banana",
        "kind": "text",
        "supports_aspect_ratio": True,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "openai/gpt-image-1/text-to-image": {
        "label": "GPT Image",
        "kind": "text",
        "supports_aspect_ratio": True,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "baidu/ERNIE-Image-Turbo/text-to-image": {
        "label": "Baidu ERNIE Turbo",
        "kind": "text",
        "supports_aspect_ratio": False,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 0,
    },
    "z-image/turbo": {
        "label": "Z-Image Turbo",
        "kind": "text",
        "supports_aspect_ratio": False,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 0,
    },
    "bytedance/seedream-v5.0-lite/sequential": {
        "label": "Seedream v5.0 Lite Sequential",
        "kind": "text",
        "supports_aspect_ratio": False,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 0,
    },
    "bytedance/seedream-v5.0-lite/edit-sequential": {
        "label": "Seedream v5.0 Lite Edit Sequential",
        "kind": "edit",
        "supports_aspect_ratio": False,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "bytedance/seedream-v4.5": {
        "label": "Seedream v4.5",
        "kind": "text",
        "supports_aspect_ratio": False,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 0,
    },
    "bytedance/seedream-v4.5/edit": {
        "label": "Seedream v4.5 Edit",
        "kind": "edit",
        "supports_aspect_ratio": False,
        "supports_size": False,
        "supports_thinking_mode": False,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "alibaba/wan-2.6/text-to-image": {
        "label": "WAN 2.6 Texto para Imagem",
        "kind": "text",
        "supports_aspect_ratio": False,
        "supports_size": True,
        "supports_thinking_mode": True,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "alibaba/wan-2.6/image-edit": {
        "label": "WAN 2.6 Imagem para Imagem",
        "kind": "edit",
        "supports_aspect_ratio": False,
        "supports_size": True,
        "supports_thinking_mode": True,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 9,
    },
    "alibaba/wan-2.7/text-to-image": {
        "label": "WAN 2.7 Texto para Imagem",
        "kind": "text",
        "supports_aspect_ratio": False,
        "supports_size": True,
        "supports_thinking_mode": True,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "alibaba/wan-2.7/image-edit": {
        "label": "WAN 2.7 Imagem para Imagem",
        "kind": "edit",
        "supports_aspect_ratio": False,
        "supports_size": True,
        "supports_thinking_mode": True,
        "supports_batch_request": False,
        "max_outputs": 4,
        "max_references": 9,
    },
}
_SCRIPT_IMAGE_MODEL_ALIASES: dict[str, dict[str, Any]] = {
    "ultra-high-3.0": {
        "label": "Ultra High 3.0",
        "description": "Criador de imagens geral sem restricao.",
        "text_model": "z-image/turbo",
        "edit_model": "alibaba/wan-2.6/image-edit",
        "supports_size": True,
        "supports_thinking_mode": True,
        "max_outputs": 4,
        "max_references": 5,
    },
    "z-image/turbo": {
        "label": "Ultra High 3.0",
        "description": "Criador de imagens geral sem restricao.",
        "text_model": "z-image/turbo",
        "edit_model": "alibaba/wan-2.6/image-edit",
        "supports_size": True,
        "supports_thinking_mode": True,
        "max_outputs": 4,
        "max_references": 5,
    },
    "bytedance/seedream-v5.0-lite/sequential": {
        "label": "Mega 5.0 Anime",
        "description": "Ideal para gerar imagens estilo anime sem restricao.",
        "text_model": "bytedance/seedream-v5.0-lite/sequential",
        "edit_model": "bytedance/seedream-v5.0-lite/edit-sequential",
        "supports_size": False,
        "supports_thinking_mode": False,
        "max_outputs": 4,
        "max_references": 5,
    },
    "bytedance/seedream-v4.5": {
        "label": "Mega 5.0 Real",
        "description": "Cria imagens em 4K com fidelidade e sem restricao.",
        "text_model": "bytedance/seedream-v4.5",
        "edit_model": "bytedance/seedream-v4.5/edit",
        "supports_size": False,
        "supports_thinking_mode": False,
        "max_outputs": 4,
        "max_references": 5,
    },
}
_OPENAI_DIRECT_MODEL = "openai/gpt-image-1/text-to-image"
_BAIDU_TURBO_MODEL = "baidu/ERNIE-Image-Turbo/text-to-image"
_Z_IMAGE_TURBO_MODEL = "z-image/turbo"
_SEEDREAM_V5_LITE_SEQUENTIAL_MODEL = "bytedance/seedream-v5.0-lite/sequential"
_SEEDREAM_V5_LITE_EDIT_SEQUENTIAL_MODEL = "bytedance/seedream-v5.0-lite/edit-sequential"
_SEEDREAM_V45_MODEL = "bytedance/seedream-v4.5"
_SEEDREAM_V45_EDIT_MODEL = "bytedance/seedream-v4.5/edit"
_ALLOWED_ASPECT_RATIOS = {"1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
_ALLOWED_WAN_TEXT_SIZES = {"1K", "2K", "4K"}
_ALLOWED_WAN_EDIT_SIZES = {"1K", "2K"}
_WAN_26_MODELS = {
    "alibaba/wan-2.6/text-to-image",
    "alibaba/wan-2.6/image-edit",
    "alibaba/wan-2.7/text-to-image",
    "alibaba/wan-2.7/image-edit",
}
_WAN_26_SIZE_PRESETS = {
    "1K": {
        "1:1": "1024*1024",
        "16:9": "1024*576",
        "9:16": "576*1024",
    },
    "2K": {
        "1:1": "1280*1280",
        "16:9": "1280*720",
        "9:16": "720*1280",
    },
    "4K": {
        "1:1": "2048*2048",
        "16:9": "2048*1152",
        "9:16": "1152*2048",
    },
}
_BAIDU_IMAGE_SIZE_PRESETS = {
    "1:1": "1024x1024",
    "3:2": "1216x832",
    "2:3": "832x1216",
    "3:4": "864x1152",
    "4:3": "1152x864",
    "4:5": "896x1120",
    "5:4": "1120x896",
    "9:16": "768x1376",
    "16:9": "1376x768",
    "21:9": "1472x640",
}
_Z_IMAGE_SIZE_PRESETS = {
    "1:1": "1024*1024",
    "3:2": "1216*832",
    "2:3": "832*1216",
    "3:4": "864*1152",
    "4:3": "1152*864",
    "4:5": "1024*1280",
    "5:4": "1280*1024",
    "9:16": "720*1280",
    "16:9": "1280*720",
    "21:9": "1536*672",
}
_SEEDREAM_V5_SIZE_PRESETS = {
    "1:1": "2048*2048",
    "3:2": "3456*2304",
    "2:3": "2304*3456",
    "3:4": "2304*3072",
    "4:3": "3072*2304",
    "4:5": "2304*2880",
    "5:4": "2880*2304",
    "9:16": "2304*4096",
    "16:9": "4096*2304",
    "21:9": "4096*1760",
}
_SEEDREAM_V45_SIZE_PRESETS = {
    "1:1": "4096*4096",
    "3:2": "4608*3072",
    "2:3": "3072*4608",
    "3:4": "3072*4096",
    "4:3": "4096*3072",
    "4:5": "3040*3800",
    "5:4": "3800*3040",
    "9:16": "3040*5504",
    "16:9": "5504*3040",
    "21:9": "5504*2352",
}
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")


def normalize_supported_model(model: str) -> str:
    candidate = str(model or "").strip()
    return candidate if candidate in _SUPPORTED_MODELS else ""


def normalize_script_image_model(model: str) -> str:
    candidate = str(model or "").strip()
    if candidate in _SCRIPT_IMAGE_MODEL_ALIASES:
        return candidate
    return normalize_supported_model(candidate)


def is_supported_atlas_image_model(model: str) -> bool:
    return bool(normalize_supported_model(model))


def is_supported_script_image_model(model: str) -> bool:
    return bool(normalize_script_image_model(model))


def get_supported_model_meta(model: str) -> dict[str, Any]:
    normalized = normalize_supported_model(model)
    return dict(_SUPPORTED_MODELS.get(normalized, {}))


def get_script_image_model_meta(model: str) -> dict[str, Any]:
    normalized = normalize_script_image_model(model)
    if normalized in _SCRIPT_IMAGE_MODEL_ALIASES:
        return dict(_SCRIPT_IMAGE_MODEL_ALIASES.get(normalized, {}))
    return get_supported_model_meta(normalized)


def resolve_script_image_model(model: str, has_reference: bool = False) -> str:
    normalized = normalize_script_image_model(model)
    if normalized in _SCRIPT_IMAGE_MODEL_ALIASES:
        alias_meta = _SCRIPT_IMAGE_MODEL_ALIASES[normalized]
        return str(alias_meta["edit_model"] if has_reference else alias_meta["text_model"])
    return normalize_supported_model(normalized)


def model_requires_reference(model: str) -> bool:
    normalized = normalize_supported_model(model)
    return bool(normalized and _SUPPORTED_MODELS[normalized]["kind"] == "edit")


def model_supports_aspect_ratio(model: str) -> bool:
    normalized = normalize_supported_model(model)
    return bool(normalized and _SUPPORTED_MODELS[normalized]["supports_aspect_ratio"])


def model_supports_size(model: str) -> bool:
    normalized = normalize_supported_model(model)
    return bool(normalized and _SUPPORTED_MODELS[normalized]["supports_size"])


def model_supports_thinking_mode(model: str) -> bool:
    normalized = normalize_supported_model(model)
    return bool(normalized and _SUPPORTED_MODELS[normalized]["supports_thinking_mode"])


def model_max_outputs(model: str) -> int:
    normalized = normalize_supported_model(model)
    if not normalized:
        return 1
    return int(_SUPPORTED_MODELS[normalized]["max_outputs"] or 1)


def model_max_references(model: str) -> int:
    normalized = normalize_supported_model(model)
    if not normalized:
        return 0
    return int(_SUPPORTED_MODELS[normalized]["max_references"] or 0)


def model_supports_batch_request(model: str) -> bool:
    normalized = normalize_supported_model(model)
    return bool(normalized and _SUPPORTED_MODELS[normalized].get("supports_batch_request"))


def model_uses_wan_26_payload(model: str) -> bool:
    normalized = normalize_supported_model(model)
    return bool(normalized and normalized in _WAN_26_MODELS)


def resolve_aspect_ratio(aspect_ratio: str) -> str:
    candidate = str(aspect_ratio or "1:1").strip()
    if candidate in _ALLOWED_ASPECT_RATIOS:
        return candidate
    return "1:1"


def resolve_size(model: str, size: str, aspect_ratio: str = "1:1") -> str:
    normalized = normalize_supported_model(model)
    candidate = str(size or "2K").strip().upper()
    if not normalized:
        return "2K"
    allowed = _ALLOWED_WAN_EDIT_SIZES if model_requires_reference(normalized) else _ALLOWED_WAN_TEXT_SIZES
    if candidate in allowed:
        resolved_size = candidate
    else:
        resolved_size = "2K"
    if model_uses_wan_26_payload(normalized):
        resolved_aspect_ratio = resolve_aspect_ratio(aspect_ratio)
        preset = _WAN_26_SIZE_PRESETS.get(resolved_size) or _WAN_26_SIZE_PRESETS["2K"]
        return str(preset.get(resolved_aspect_ratio) or preset["1:1"])
    return resolved_size


def _is_openai_direct_model(model: str) -> bool:
    return normalize_supported_model(model) == _OPENAI_DIRECT_MODEL


def _is_baidu_turbo_model(model: str) -> bool:
    return normalize_supported_model(model) == _BAIDU_TURBO_MODEL


def _is_z_image_turbo_model(model: str) -> bool:
    return normalize_supported_model(model) == _Z_IMAGE_TURBO_MODEL


def _is_seedream_v5_lite_sequential_model(model: str) -> bool:
    return normalize_supported_model(model) == _SEEDREAM_V5_LITE_SEQUENTIAL_MODEL


def _is_seedream_v5_lite_edit_sequential_model(model: str) -> bool:
    return normalize_supported_model(model) == _SEEDREAM_V5_LITE_EDIT_SEQUENTIAL_MODEL


def _is_seedream_v45_model(model: str) -> bool:
    return normalize_supported_model(model) == _SEEDREAM_V45_MODEL


def _is_seedream_v45_edit_model(model: str) -> bool:
    return normalize_supported_model(model) == _SEEDREAM_V45_EDIT_MODEL


def _baidu_image_size_for_aspect_ratio(aspect_ratio: str) -> str:
    resolved = resolve_aspect_ratio(aspect_ratio)
    return str(_BAIDU_IMAGE_SIZE_PRESETS.get(resolved) or _BAIDU_IMAGE_SIZE_PRESETS["1:1"])


def _z_image_size_for_aspect_ratio(aspect_ratio: str) -> str:
    resolved = resolve_aspect_ratio(aspect_ratio)
    return str(_Z_IMAGE_SIZE_PRESETS.get(resolved) or _Z_IMAGE_SIZE_PRESETS["1:1"])


def _seedream_v5_size_for_aspect_ratio(aspect_ratio: str) -> str:
    resolved = resolve_aspect_ratio(aspect_ratio)
    return str(_SEEDREAM_V5_SIZE_PRESETS.get(resolved) or _SEEDREAM_V5_SIZE_PRESETS["1:1"])


def _seedream_v45_size_for_aspect_ratio(aspect_ratio: str) -> str:
    resolved = resolve_aspect_ratio(aspect_ratio)
    return str(_SEEDREAM_V45_SIZE_PRESETS.get(resolved) or _SEEDREAM_V45_SIZE_PRESETS["1:1"])


def _atlas_api_key() -> str:
    key = (settings.atlascloud_api_key or "").strip()
    if key:
        return key
    return (os.getenv("ATLASCLOUD_API_KEY") or "").strip()


def _openai_api_key() -> str:
    key = (settings.openai_api_key or "").strip()
    if key:
        return key
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def _openai_image_model_name() -> str:
    return (settings.persona_image_openai_model or "gpt-image-1").strip() or "gpt-image-1"


def _openai_image_size_for_aspect_ratio(aspect_ratio: str) -> str:
    resolved = resolve_aspect_ratio(aspect_ratio)
    if resolved in {"9:16", "4:5", "3:4", "2:3"}:
        return "1024x1536"
    if resolved in {"16:9", "21:9", "4:3", "5:4", "3:2"}:
        return "1536x1024"
    return "1024x1024"


def _extract_openai_image_bytes_from_response(response: object) -> bytes:
    data_items = getattr(response, "data", None) or []
    if not data_items:
        return b""

    item = data_items[0]
    b64_data = getattr(item, "b64_json", None)
    if not b64_data and isinstance(item, dict):
        b64_data = item.get("b64_json")
    if b64_data:
        return base64.b64decode(b64_data)

    img_url = getattr(item, "url", None)
    if not img_url and isinstance(item, dict):
        img_url = item.get("url")
    if not img_url:
        return b""

    with httpx.Client(timeout=120, follow_redirects=True) as client_http:
        resp = client_http.get(img_url)
        resp.raise_for_status()
        return resp.content or b""


def _generate_single_openai_image(prompt: str, aspect_ratio: str, reference_paths: list[str]) -> dict[str, Any]:
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    client = openai.OpenAI(api_key=api_key)
    model_name = _openai_image_model_name()
    prompt_text = str(prompt or "").strip()[:3800]
    image_size = _openai_image_size_for_aspect_ratio(aspect_ratio)

    if reference_paths:
        file_handles = []
        try:
            for path in reference_paths[:5]:
                file_handles.append(open(path, "rb"))
            response = client.images.edit(
                model=model_name,
                image=file_handles,
                prompt=prompt_text,
                size=image_size,
            )
        finally:
            for handle in file_handles:
                try:
                    handle.close()
                except Exception:
                    pass
    else:
        response = client.images.generate(
            model=model_name,
            prompt=prompt_text,
            size=image_size,
        )

    image_bytes = _extract_openai_image_bytes_from_response(response)
    if not image_bytes:
        raise RuntimeError("OpenAI nao retornou imagem utilizavel")

    return {
        "bytes": image_bytes,
        "mime_type": "image/png",
        "source": model_name,
    }


async def _generate_openai_images(
    *,
    prompt: str,
    aspect_ratio: str,
    count: int,
    reference_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    requested_count = max(1, int(count or 1))
    refs = [str(path or "").strip() for path in (reference_paths or []) if str(path or "").strip()]
    outputs: list[dict[str, Any]] = []
    for _ in range(requested_count):
        outputs.append(await asyncio.to_thread(_generate_single_openai_image, prompt, aspect_ratio, refs))
    return outputs


def _build_atlas_generation_payload(
    *,
    model: str,
    prompt: str,
    aspect_ratio: str,
    size: str,
    remaining: int,
    seed: int,
    thinking_mode: bool,
    uploaded_refs: list[str],
) -> dict[str, Any]:
    normalized_model = normalize_supported_model(model)
    resolved_aspect_ratio = resolve_aspect_ratio(aspect_ratio)
    prompt_text = str(prompt or "").strip()

    if _is_baidu_turbo_model(normalized_model):
        return {
            "model": normalized_model,
            "prompt": prompt_text,
            "size": _baidu_image_size_for_aspect_ratio(resolved_aspect_ratio),
            "n": 1,
            "seed": int(seed if seed is not None else -1),
            "use_pe": True,
            "num_inference_steps": 8,
            "guidance_scale": 1,
            "enable_sync_mode": False,
            "enable_base64_output": False,
        }

    if _is_z_image_turbo_model(normalized_model):
        return {
            "model": normalized_model,
            "prompt": prompt_text,
            "prompt_extend": False,
            "seed": int(seed if seed is not None else -1),
            "size": _z_image_size_for_aspect_ratio(resolved_aspect_ratio),
            "enable_base64_output": False,
            "enable_sync_mode": False,
        }

    if _is_seedream_v5_lite_sequential_model(normalized_model) or _is_seedream_v5_lite_edit_sequential_model(normalized_model):
        payload = {
            "model": normalized_model,
            "prompt": prompt_text,
            "size": _seedream_v5_size_for_aspect_ratio(resolved_aspect_ratio),
            "max_images": 1,
            "output_format": "jpeg",
            "enable_base64_output": False,
            "enable_sync_mode": False,
        }
        if uploaded_refs and model_max_references(normalized_model) > 0:
            payload["images"] = uploaded_refs[:model_max_references(normalized_model)]
        return payload

    if _is_seedream_v45_model(normalized_model) or _is_seedream_v45_edit_model(normalized_model):
        payload = {
            "model": normalized_model,
            "prompt": prompt_text,
            "size": _seedream_v45_size_for_aspect_ratio(resolved_aspect_ratio),
            "enable_base64_output": False,
            "enable_sync_mode": False,
        }
        if uploaded_refs and model_max_references(normalized_model) > 0:
            payload["images"] = uploaded_refs[:model_max_references(normalized_model)]
        return payload

    prompt_payload = prompt_text
    if not model_supports_aspect_ratio(normalized_model) and not model_uses_wan_26_payload(normalized_model):
        prompt_payload = f"{prompt_text}\n\nDesired aspect ratio: {resolved_aspect_ratio}."

    payload: dict[str, Any] = {
        "model": normalized_model,
        "prompt": prompt_payload,
        "enable_sync_mode": False,
        "enable_base64_output": False,
    }
    if model_supports_aspect_ratio(normalized_model):
        payload["aspect_ratio"] = resolved_aspect_ratio
    if model_supports_size(normalized_model):
        payload["size"] = resolve_size(normalized_model, size, resolved_aspect_ratio)
    if model_supports_thinking_mode(normalized_model):
        payload["enable_prompt_expansion"] = bool(thinking_mode)
        payload["seed"] = int(seed if seed is not None else -1)
    if model_supports_batch_request(normalized_model) and remaining > 1:
        payload["n"] = remaining
    if uploaded_refs and model_max_references(normalized_model) > 0:
        payload["images"] = uploaded_refs
    return payload


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

    nodes: list[dict[str, Any]] = [payload]
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


def _extract_output_ref(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""

    for key in ("url", "image", "image_url", "output", "download_url", "public_url", "secure_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_output_refs(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    nodes: list[Any] = [payload]
    data_node = payload.get("data")
    if isinstance(data_node, dict):
        nodes.append(data_node)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        outputs = node.get("outputs")
        if not isinstance(outputs, list):
            continue
        for item in outputs:
            ref = _extract_output_ref(item)
            if ref:
                refs.append(ref)

    return refs


def _data_url_to_bytes(data_url: str) -> tuple[bytes, str]:
    header, _, body = str(data_url or "").partition(",")
    if not header.startswith("data:") or not body:
        raise RuntimeError("Atlas retornou um data URL invalido")
    mime_type = "image/png"
    if ";" in header:
        mime_type = header[5:].split(";", 1)[0].strip() or mime_type
    raw = base64.b64decode(body)
    return raw, mime_type


def _looks_like_base64(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped or len(stripped) < 64:
        return False
    return bool(_BASE64_RE.fullmatch(stripped))


async def _fetch_output_bytes(client: httpx.AsyncClient, output_ref: str) -> tuple[bytes, str]:
    ref = str(output_ref or "").strip()
    if not ref:
        raise RuntimeError("Atlas nao retornou imagem valida")

    if ref.startswith("data:"):
        return _data_url_to_bytes(ref)

    if _HTTP_URL_RE.match(ref):
        resp = await client.get(ref)
        resp.raise_for_status()
        mime_type = (resp.headers.get("content-type") or "image/png").split(";", 1)[0].strip() or "image/png"
        return resp.content, mime_type

    if _looks_like_base64(ref):
        return base64.b64decode(ref), "image/png"

    raise RuntimeError("Atlas retornou uma referencia de imagem nao suportada")


async def _upload_media_to_atlas(client: httpx.AsyncClient, file_path: str, api_key: str) -> str:
    endpoint = f"{ATLAS_IMAGE_API_BASE_URL}/model/uploadMedia"
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    with open(file_path, "rb") as source:
        files = {"file": (os.path.basename(file_path), source, mime_type)}
        resp = await client.post(endpoint, headers={"Authorization": f"Bearer {api_key}"}, files=files)

    if resp.status_code >= 400:
        message = _extract_atlas_error_message(resp) or f"HTTP {resp.status_code}"
        raise RuntimeError(f"Falha ao enviar imagem de referencia para o Atlas Cloud: {message}")

    payload = resp.json()
    ref = _extract_upload_reference(payload)
    if ref:
        return ref

    raise RuntimeError("Atlas Cloud nao retornou URL temporaria para a imagem enviada")


async def _submit_generation(client: httpx.AsyncClient, payload: dict[str, Any], api_key: str) -> str:
    endpoint = f"{ATLAS_IMAGE_API_BASE_URL}/model/generateImage"

    for attempt in range(3):
        resp = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code == 429:
            await asyncio.sleep(_retry_delay_from_header(resp.headers.get("Retry-After"), 5))
            continue
        if resp.status_code >= 400:
            message = _extract_atlas_error_message(resp) or f"HTTP {resp.status_code}"
            raise RuntimeError(f"Falha ao iniciar geracao no Atlas Cloud: {message}")

        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            prediction_id = str(data.get("id") or "").strip()
            if prediction_id:
                return prediction_id
        prediction_id = str(body.get("id") or "").strip() if isinstance(body, dict) else ""
        if prediction_id:
            return prediction_id
        raise RuntimeError("Atlas Cloud nao retornou prediction id para a imagem")

    raise RuntimeError("Atlas Cloud esta com alta demanda para gerar a imagem")


async def _fetch_result_outputs(client: httpx.AsyncClient, prediction_id: str, api_key: str) -> list[str]:
    endpoint = f"{ATLAS_IMAGE_API_BASE_URL}/model/result/{prediction_id}"
    resp = await client.get(endpoint, headers={"Authorization": f"Bearer {api_key}"})
    if resp.status_code >= 400:
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    return _extract_output_refs(payload)


async def _wait_for_outputs(client: httpx.AsyncClient, prediction_id: str, api_key: str, timeout_seconds: int) -> list[str]:
    endpoint = f"{ATLAS_IMAGE_API_BASE_URL}/model/prediction/{prediction_id}"
    start_time = asyncio.get_running_loop().time()

    while (asyncio.get_running_loop().time() - start_time) < timeout_seconds:
        resp = await client.get(endpoint, headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code == 429:
            await asyncio.sleep(_retry_delay_from_header(resp.headers.get("Retry-After"), 3))
            continue
        if resp.status_code >= 400:
            message = _extract_atlas_error_message(resp) or f"HTTP {resp.status_code}"
            raise RuntimeError(f"Falha ao consultar o Atlas Cloud: {message}")

        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            data = payload if isinstance(payload, dict) else {}

        status = str(data.get("status") or "").strip().lower()
        if status in {"completed", "succeeded", "success"}:
            refs = _extract_output_refs(payload)
            if refs:
                return refs
            fallback_refs = await _fetch_result_outputs(client, prediction_id, api_key)
            if fallback_refs:
                return fallback_refs
            raise RuntimeError("Atlas Cloud concluiu a geracao, mas nao retornou imagens")

        if status in {"failed", "error", "cancelled", "canceled"}:
            message = str(data.get("error") or data.get("message") or "Falha ao gerar imagem").strip()
            raise RuntimeError(message or "Falha ao gerar imagem")

        await asyncio.sleep(2)

    raise RuntimeError("Tempo limite excedido ao aguardar a imagem do Atlas Cloud")


async def generate_atlas_images(
    *,
    prompt: str,
    model: str,
    aspect_ratio: str = "1:1",
    size: str = "2K",
    count: int = 1,
    seed: int = -1,
    thinking_mode: bool = False,
    reference_paths: list[str] | None = None,
    timeout_seconds: int = 240,
) -> list[dict[str, Any]]:
    normalized_model = normalize_supported_model(model)
    if not normalized_model:
        raise RuntimeError("Modelo de imagem nao suportado")

    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise RuntimeError("Descreva a imagem antes de gerar")
    resolved_aspect_ratio = resolve_aspect_ratio(aspect_ratio)

    requested_count = max(1, min(int(count or 1), model_max_outputs(normalized_model)))
    source_refs = [str(path or "").strip() for path in (reference_paths or []) if str(path or "").strip()]

    if _is_openai_direct_model(normalized_model):
        return await _generate_openai_images(
            prompt=prompt_text,
            aspect_ratio=resolved_aspect_ratio,
            count=requested_count,
            reference_paths=source_refs,
        )

    api_key = _atlas_api_key()
    if not api_key:
        raise RuntimeError("ATLASCLOUD_API_KEY not configured")

    uploaded_refs: list[str] = []
    supports_batch_request = model_supports_batch_request(normalized_model)

    timeout = httpx.Timeout(60.0, connect=20.0, read=60.0, write=60.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if model_requires_reference(normalized_model) and not source_refs:
            raise RuntimeError("Envie pelo menos uma imagem de referencia para este motor")

        for ref_path in source_refs[:model_max_references(normalized_model)]:
                uploaded_refs.append(await _upload_media_to_atlas(client, ref_path, api_key))

        results: list[dict[str, Any]] = []
        while len(results) < requested_count:
            remaining = requested_count - len(results)
            payload = _build_atlas_generation_payload(
                model=normalized_model,
                prompt=prompt_text,
                aspect_ratio=resolved_aspect_ratio,
                size=size,
                remaining=remaining,
                seed=seed,
                thinking_mode=bool(thinking_mode),
                uploaded_refs=uploaded_refs,
            )

            prediction_id = await _submit_generation(client, payload, api_key)
            output_refs = await _wait_for_outputs(client, prediction_id, api_key, timeout_seconds)

            batch_target = remaining if supports_batch_request else 1
            batch_results: list[dict[str, Any]] = []
            for output_ref in output_refs[:batch_target]:
                raw_bytes, mime_type = await _fetch_output_bytes(client, output_ref)
                if not raw_bytes:
                    continue
                batch_results.append({
                    "bytes": raw_bytes,
                    "mime_type": mime_type,
                    "source": output_ref,
                })

            if not batch_results:
                break

            results.extend(batch_results)
            if supports_batch_request:
                break

        if results:
            return results[:requested_count]

    raise RuntimeError("Atlas Cloud nao retornou imagem utilizavel")