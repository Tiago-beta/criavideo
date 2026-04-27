"""
Seedance Video — Uses Atlas Cloud API to call ByteDance Seedance
for realistic AI video generation (text-to-video and image-to-video).
"""
import os
import time
import logging
import asyncio
import base64
import subprocess
import mimetypes
import httpx
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ATLAS_VIDEO_API_BASE_URL = (settings.atlascloud_api_base_url or "https://api.atlascloud.ai/api/v1").rstrip("/")
SEEDANCE_T2V_MODEL = (settings.atlascloud_seedance_t2v_model or "bytedance/seedance-2.0/text-to-video").strip()
_SEEDANCE_I2V_DEFAULT_MODEL = "bytedance/seedance-v1.5-pro/image-to-video-fast"
_seedance_i2v_cfg = (settings.atlascloud_seedance_i2v_model or "").strip()
if _seedance_i2v_cfg in {"", "bytedance/seedance-2.0/image-to-video"}:
    SEEDANCE_I2V_MODEL = _SEEDANCE_I2V_DEFAULT_MODEL
else:
    SEEDANCE_I2V_MODEL = _seedance_i2v_cfg
SEEDANCE_RATE_LIMIT_MSG = (
    "Seedance esta com alta demanda no momento (429). "
    "Tente novamente em alguns segundos ou use MiniMax/Wan 2.6."
)
_ALLOWED_ASPECT_RATIOS = {"21:9", "16:9", "9:16", "1:1", "4:3", "3:4"}


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
        logger.warning("Seedance result lookup request failed: %s", e)
        return []

    if resp.is_error:
        logger.warning("Seedance result lookup failed (HTTP %s)", resp.status_code)
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


def _clamp_prompt_temperature(value: float | None, default_value: float = 0.7) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default_value
    return max(0.0, min(parsed, 1.0))

# Curated Seedance prompt engineering system prompt
_SEEDANCE_SYSTEM_PROMPT = """You are an expert prompt engineer for Seedance 2.0, ByteDance's state-of-the-art AI video generation model.

Your job: convert the user's video description (usually in Portuguese) into an optimized English prompt for Seedance 2.0.

RULES:
1. Output ONLY the final English prompt. No explanations, no markdown.
2. The video is {duration} seconds long. Structure shots within that time.
3. CONTENT SAFETY (CRITICAL): Seedance has a strict content filter. You MUST:
   - NEVER use explicit religious references (God, Jesus, church, prayer, psalm, Bible, faith, worship, angel, demon, etc.)
   - NEVER use political, violent, sexual, or controversial terms.
   - Convert religious/spiritual themes into VISUAL METAPHORS: e.g. "shepherd on a hill at golden hour", "peaceful valley with sheep", "sunrise over mountains", "person walking a serene path through nature".
   - Focus on NATURE, LANDSCAPES, EMOTIONS, and HUMAN MOMENTS — not abstract concepts.
   - If the user's theme is spiritual, translate it into a beautiful cinematic nature/human scene without any religious words.
4. Use this proven Seedance prompt structure:

   Style: [Visual style], [Aesthetic], [Mood], [Technical look].
   Duration: {duration}s.
   [00:00-XX:XX] Shot 1: [Shot Name].
   Scene: [Visual description with specific details].
   Action: [Movement, interaction, emotion].
   Camera: [Camera movement - push in, pan, dolly, crane, tracking, slow motion, etc].
   Lighting: [Specific lighting - golden hour, neon, dramatic shadows, etc].

5. Include SPECIFIC visual details:
   - Camera movements: push in, pull back, pan left/right, dolly, crane up/down, tracking shot, slow motion, whip pan, rack focus
   - Lighting: golden hour, blue hour, neon, candlelight, dramatic rim light, volumetric fog, lens flare, cinematic shadows
   - Texture/mood: film grain, depth of field, bokeh, desaturated, vibrant, moody, ethereal
   - Physics: rain, smoke, particles, fabric movement, hair physics, water ripples

6. For short durations (5-7s), use 1-2 shots max. For 10s, use 2-3 shots.
7. Be VERY specific about what appears in frame — Seedance excels with concrete visual descriptions.
8. Do NOT include dialogue or subtitle cues — focus on visuals, motion, and atmosphere.
9. If the user mentions a product, brand, or specific object, describe it precisely in the scene.
10. Preserve the user's creative intent while enhancing with cinematic details.
11. If the user says there is a reference image, explicitly anchor the scene to that image and preserve the same subject identity and key visual traits.
12. MAIN THEME LOCK (MANDATORY): never replace the user's main subject, characters, location, or central action with unrelated generic ideas.
    If the input contains "TEMA PRINCIPAL", that section is absolute priority and must be preserved exactly in meaning.
    Any auxiliary context is support-only and cannot override the main theme.

EXAMPLES OF GREAT SEEDANCE PROMPTS:

Example 1 (7s cinematic):
Style: IMAX Cinematic, Golden Hour, Epic Scale, Warm Tones.
Duration: 7s.
[00:00-00:04] Shot 1: The Reveal.
Scene: A cozy wooden cabin nestled in a dense snowy forest at night. Warm amber light glows from frosted windows. Fresh snow covers the ground and pine branches.
Camera: Slow push in through the trees, branches gently parting.
Lighting: Warm interior glow contrasting with cool blue moonlit snow. Gentle snowfall catches the light.
[00:04-00:07] Shot 2: The Detail.
Scene: Close-up of snowflakes landing on a windowsill, the warm interior visible through glass.
Camera: Slow dolly right along the window.
Lighting: Bokeh from interior lights, crystalline snow detail.

Example 2 (5s commercial):
Style: Premium Commercial, Clean, High-End Product Photography in Motion.
Duration: 5s.
[00:00-00:05] Shot 1: Hero Product Reveal.
Scene: A sleek glass perfume bottle rotates slowly on a reflective black surface. Golden liquid catches light. Delicate mist particles float around it.
Camera: Smooth 180-degree orbit around the bottle, slight low angle.
Lighting: Key light from above creating dramatic reflections and caustics on the glass. Subtle rim light separating product from dark background.

Example 3 (10s social media):
Style: Hyperrealistic CG, Comedic Surrealism, Viral Meme Aesthetic, 8K.
Duration: 10s.
[00:00-00:04] Shot 1: The Setup.
Scene: A bustling city street with modern skyscrapers. Normal daily life — people walking, traffic flowing.
Camera: Handheld street-level perspective, slight shake for authenticity.
[00:04-00:07] Shot 2: The Reveal.
Scene: Camera tilts up to reveal a giant orange tabby cat the size of a building, lounging between two skyscrapers, lazily swatting at a helicopter.
Camera: Dramatic tilt up, wide angle lens distortion.
[00:07-00:10] Shot 3: The Punchline.
Scene: The giant cat yawns enormously, causing papers and hats to blow away from pedestrians below.
Camera: Ground-level looking up at the massive yawning cat face.
"""


async def optimize_prompt_for_seedance(
    user_description: str,
    duration: int = 7,
    tone: str | None = None,
    has_reference_image: bool = False,
    temperature: float | None = None,
) -> str:
    """Convert user's description (Portuguese) into an optimized English Seedance 2.0 prompt."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    system = _SEEDANCE_SYSTEM_PROMPT.replace("{duration}", str(duration))
    user_msg = user_description
    if tone:
        user_msg += f"\n\nTom/estilo desejado: {tone}"
    if has_reference_image:
        user_msg += (
            "\n\nMANDATORY REFERENCE IMAGE RULE: The user uploaded a reference image. "
            "The prompt must preserve the same subject identity and key visual traits from that image."
        )

    prompt_temperature = _clamp_prompt_temperature(temperature, default_value=0.7)

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=prompt_temperature,
            max_tokens=800,
        )
        optimized = resp.choices[0].message.content.strip()
        logger.info(f"Seedance prompt optimized: {len(optimized)} chars")
        return optimized
    except Exception as e:
        logger.warning(f"Prompt optimization failed, using original: {e}")
        return user_description


_SANITIZE_PROMPT = """You are a content-safety filter for AI video generation prompts.

The following prompt was REJECTED by the AI video model's content filter (error E005: flagged as sensitive).
Your job: rewrite it to convey the SAME visual scene but remove ALL potentially sensitive words/themes.

RULES:
1. Remove ALL religious terms (God, Lord, Jesus, faith, prayer, church, psalm, worship, angel, shepherd in religious context, etc.)
2. Remove ALL political, violent, sexual, or controversial references.
3. Keep the VISUAL ESSENCE: landscapes, nature, people, lighting, camera movements.
4. Replace abstract/spiritual concepts with concrete visual descriptions.
5. Output ONLY the rewritten prompt. No explanations.

Example:
- "A shepherd guided by divine light walks through a valley of faith" →
  "A man with a wooden staff walks through a lush green valley at golden hour, warm sunlight streaming through mountain peaks"
"""


async def sanitize_prompt_for_retry(rejected_prompt: str) -> str:
    """Rewrite a prompt that was flagged by Seedance's content filter."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SANITIZE_PROMPT},
                {"role": "user", "content": rejected_prompt},
            ],
            temperature=0.5,
            max_tokens=800,
        )
        sanitized = resp.choices[0].message.content.strip()
        logger.info(f"Prompt sanitized for retry: {len(sanitized)} chars")
        return sanitized
    except Exception as e:
        logger.warning(f"Prompt sanitization failed: {e}")
        raise RuntimeError("Nao foi possivel reformular o prompt para evitar o filtro de conteudo.")


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
                    "Seedance upload request error (attempt %d/4): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 3:
                    raise RuntimeError(SEEDANCE_RATE_LIMIT_MSG)
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                logger.warning(
                    "Seedance upload rate-limited (attempt %d/4). Retrying in %ds",
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
            logger.warning("Seedance upload sem URL/asset. Usando fallback Base64 inline.")
            return _file_to_data_uri(file_path)

    raise RuntimeError("Nao foi possivel enviar a imagem de referencia para o Atlas Cloud")


async def generate_realistic_video(
    prompt: str,
    duration: int = 7,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    seed: int | None = None,
    resolution: str = "720p",
    generate_audio: bool = True,
    image_path: str | None = None,
    timeout_seconds: int = 600,
    on_progress=None,
) -> str:
    """Generate a realistic video using Seedance via Atlas Cloud API.

    Returns the local path to the downloaded MP4 video.
    """
    api_key = _atlas_api_key()
    if not api_key:
        raise RuntimeError("ATLASCLOUD_API_KEY not configured")

    use_i2v = bool(image_path and os.path.exists(image_path))
    # Seedance v1.5 I2V fast uses 4..12s according to Atlas docs.
    duration = max(4, min(int(duration or 5), 12)) if use_i2v else max(1, min(int(duration or 7), 10))
    aspect_ratio = _resolve_aspect_ratio(aspect_ratio)
    resolution = str(resolution or "720p").strip() or "720p"
    model_id = SEEDANCE_I2V_MODEL if use_i2v else SEEDANCE_T2V_MODEL

    payload = {
        "model": model_id,
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "ratio": aspect_ratio,
        "resolution": resolution,
        "generate_audio": generate_audio,
        "camera_fixed": False,
    }

    if seed is not None:
        payload["seed"] = int(seed)

    # Add reference image URL if provided.
    if use_i2v:
        uploaded_image_ref = await _upload_media_to_atlas(image_path, api_key)
        payload["model"] = SEEDANCE_I2V_MODEL
        payload["image"] = uploaded_image_ref
        logger.info("Seedance I2V fast: uploaded %s", image_path)

    # Step 1: Create prediction.
    prediction_id = ""
    submit_url = f"{ATLAS_VIDEO_API_BASE_URL}/model/generateVideo"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(5):
            try:
                resp = await client.post(submit_url, headers=headers, json=payload)
            except httpx.RequestError as e:
                if attempt >= 4:
                    raise RuntimeError(f"Falha de conexao ao iniciar Seedance: {e}")
                wait_s = min(20, 2 ** attempt)
                logger.warning(
                    "Seedance request error on create (attempt %d/5): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 4:
                    raise RuntimeError(SEEDANCE_RATE_LIMIT_MSG)
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(30, 2 ** (attempt + 2)))
                logger.warning(
                    "Seedance rate-limited on create (attempt %d/5). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                details = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Erro ao iniciar Seedance (HTTP {resp.status_code}): {details}")

            response_payload = resp.json() if resp.content else {}
            data_node = response_payload.get("data") if isinstance(response_payload, dict) else None
            prediction_id = str((data_node or {}).get("id") or response_payload.get("id") or "").strip()
            if not prediction_id:
                raise RuntimeError("Atlas Cloud nao retornou prediction id para o Seedance")
            break

    if not prediction_id:
        raise RuntimeError("Nao foi possivel iniciar a geracao no Seedance.")

    logger.info("Seedance prediction created: %s (model=%s)", prediction_id, payload.get("model"))

    if on_progress:
        await on_progress(20, "Gerando video realista com Seedance...")

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
                logger.warning("Seedance poll request error: %s", e)
                await asyncio.sleep(5)
                continue

            if resp.status_code == 429:
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=6)
                logger.warning("Seedance rate-limited on poll. Retrying in %ds", wait_s)
                await asyncio.sleep(wait_s)
                continue

            if resp.is_error:
                details = _extract_atlas_error_message(resp)
                raise RuntimeError(f"Erro ao consultar status do Seedance (HTTP {resp.status_code}): {details}")

            data = resp.json() if resp.content else {}
            data_node = data.get("data") if isinstance(data, dict) else {}
            status = str((data_node or {}).get("status") or data.get("status") or "").strip().lower()

            if status in {"completed", "succeeded", "success"}:
                poll_candidates: list[str] = []
                _collect_video_url_candidates(data_node, poll_candidates)
                _collect_video_url_candidates(data, poll_candidates)
                candidate_urls = _dedupe_preserve_order(poll_candidates)

                if not candidate_urls:
                    raise RuntimeError("Seedance returned empty output")
                break
            elif status in {"failed", "error", "canceled", "cancelled"}:
                error = (data_node or {}).get("error") or data.get("error") or "Unknown error"
                raise RuntimeError(f"Seedance generation failed: {error}")

            # Update progress based on elapsed time
            elapsed = time.time() - start_time
            progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
            if progress > last_progress and on_progress:
                last_progress = progress
                await on_progress(progress, "Gerando video realista com Seedance...")

            await asyncio.sleep(5)
        else:
            raise TimeoutError(f"Seedance generation timed out after {timeout_seconds}s")

    result_candidates = await _fetch_result_video_candidates(prediction_id, api_key)
    if result_candidates:
        candidate_urls = _dedupe_preserve_order(result_candidates + candidate_urls)

    if not candidate_urls:
        raise RuntimeError("Seedance nao retornou URL de video valida")

    if on_progress:
        await on_progress(80, "Baixando video gerado...")

    # Step 3: Download the video.
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    selected_url = ""
    downloaded_without_audio = False
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for idx, video_url in enumerate(candidate_urls):
            downloaded = False
            for attempt in range(4):
                try:
                    resp = await client.get(video_url)
                except httpx.RequestError as e:
                    if attempt >= 3:
                        logger.warning("Falha ao baixar Seedance URL %s: %s", video_url, e)
                        break
                    wait_s = min(12, 2 ** (attempt + 1))
                    logger.warning(
                        "Seedance download request error (url %d/%d, attempt %d/4): %s. Retrying in %ds",
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
                        logger.warning("Seedance candidate URL rate-limited too many times: %s", video_url)
                        break
                    wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                    logger.warning(
                        "Seedance rate-limited on download (url %d/%d, attempt %d/4). Retrying in %ds",
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
                    logger.warning("Skipping Seedance non-video candidate URL (%s): %s", content_type, video_url)
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
                    "Seedance candidate URL %d/%d has no audio stream. Trying next candidate.",
                    idx + 1,
                    len(candidate_urls),
                )
                continue

            selected_url = video_url
            break

    if not selected_url and not downloaded_without_audio:
        raise RuntimeError("Nao foi possivel baixar o video do Seedance.")
    if selected_url:
        logger.info("Seedance downloaded using candidate URL: %s", selected_url)
    elif downloaded_without_audio:
        logger.warning("Seedance video baixado sem trilha de audio apos testar todas as URLs candidatas.")

    file_size = os.path.getsize(output_path)
    logger.info(f"Seedance video downloaded: {output_path} ({file_size} bytes)")

    return output_path
