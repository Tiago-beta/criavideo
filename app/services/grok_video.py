"""
Grok Video — Uses xAI's grok-imagine-video to generate video clips
from images (image-to-video) for realistic AI video generation.
"""
import os
import time
import logging
import hashlib
import httpx
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

XAI_BASE_URL = "https://api.x.ai/v1"

_GROK_SYSTEM_PROMPT = """Voce e um engenheiro de prompt especialista no modelo xAI grok-imagine-video.

Sua tarefa: converter a descricao do usuario em um prompt otimizado para gerar video no Grok.

REGRAS:
1. Responda SOMENTE com o prompt final. Sem explicacoes e sem markdown.
2. O prompt final deve estar 100% em portugues do Brasil (pt-BR).
3. Nao use rotulos em ingles como "Style:", "Shot", "Scene", "Lighting" ou "Duration".
4. O video tem {duration} segundos.
5. Descreva a cena com riqueza visual cinematografica: camera, luz, ambiente, textura, movimento e acao.
6. Se houver fala, narracao ou qualquer audio com palavras, todo texto falado deve estar em pt-BR entre aspas duplas.
7. Sons ambientes e efeitos sonoros devem ser descritos naturalmente (vento, chuva, cidade, natureza etc).
8. Preserve a intencao criativa do usuario sem mudar o assunto principal.
9. TRAVA DE TEMA PRINCIPAL (obrigatoria): nunca substitua personagens, local ou acao central definidos pelo usuario por ideias genericas.
    Se houver secao "TEMA PRINCIPAL", ela tem prioridade absoluta e deve ser preservada no significado.
    Contexto auxiliar serve apenas como apoio e nao pode sobrepor o tema principal.
10. Mantenha o prompt objetivo e detalhado (ate 500 palavras).
11. Se houver imagem de referencia, ela e obrigatoria como ancora visual principal. Preserve identidade, rosto, cabelo, tons de pele, idade aparente e estilo geral.
12. Nao crie um protagonista novo, nao troque rosto e nao faca morphing de identidade.
13. Se houver sinais de continuidade (ex.: "Continue from previous scene", "CHARACTER_LOCK", "WORLD_LOCK"), preserve esses locks sem alterar os personagens.
14. Priorize consistencia visual e de identidade em close-up quando houver referencia.
15. Seguranca de conteudo: evite conteudo sexual, violento ou controverso."""

_PT_BR_REWRITE_SYSTEM_PROMPT = """Reescreva o prompt abaixo para portugues do Brasil (pt-BR) mantendo o mesmo significado visual.

REGRAS:
1. Responda somente com o prompt final.
2. Nao use rotulos em ingles (Style, Shot, Scene, Lighting, Duration).
3. Preserve integralmente locks de identidade/continuidade e restricoes de imagem de referencia.
4. Nao adicione personagens, objetos centrais ou eventos que nao existam no texto original.
5. Se houver fala, mantenha as falas em pt-BR entre aspas duplas."""

_SUPPORTED_VIDEO_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
_RUNTIME_REFERENCE_LOCK_MARKERS = (
    "trava de fidelidade de referencia",
    "first frame lock",
    "primeiro frame",
)


def _looks_like_english_template(prompt: str) -> bool:
    lower = (prompt or "").lower()
    english_markers = (
        "style:",
        "shot ",
        "scene ",
        "lighting",
        "duration:",
        "[00:",
    )
    return any(marker in lower for marker in english_markers)


def _normalize_grok_aspect_ratio(value: str) -> str:
    raw = str(value or "").strip()
    if raw in _SUPPORTED_VIDEO_ASPECT_RATIOS:
        return raw
    if raw:
        logger.warning("Grok aspect_ratio '%s' not supported; falling back to 16:9", raw)
    return "16:9"


def _ensure_runtime_reference_lock(prompt: str) -> str:
    base_prompt = (prompt or "").strip()
    lowered = base_prompt.lower()
    if base_prompt and any(marker in lowered for marker in _RUNTIME_REFERENCE_LOCK_MARKERS):
        return base_prompt

    lock = (
        "TRAVA DE FIDELIDADE DE REFERENCIA (OBRIGATORIA): use a imagem enviada como PRIMEIRO FRAME e ancora visual absoluta. "
        "Mantenha exatamente os mesmos personagens, rostos, roupas, cores e proporcoes da cena de referencia. "
        "Nao adicionar, remover, trocar ou mesclar personagens. Nao alterar figurino principal nem objetos centrais. "
        "Nao recriar outra cena. Apenas anime a cena existente com movimentos naturais (olhos, respiracao, microgestos, cabelo/roupa e camera suave)."
    )
    return f"{base_prompt}\n\n{lock}" if base_prompt else lock


def _build_payload_variants(
    prompt: str,
    image_url: str,
    duration: int,
    aspect_ratio: str,
) -> list[tuple[str, dict]]:
    """Build canonical and compatibility payload variants for xAI video API."""
    canonical_prompt = _ensure_runtime_reference_lock(prompt)

    base_payload = {
        "model": "grok-imagine-video",
        "prompt": canonical_prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
    }

    return [
        (
            "canonical-image-and-reference",
            {
                **base_payload,
                "image": {"url": image_url},
                # Reuse same image as explicit reference to reduce character/costume drift.
                "reference_images": [{"url": image_url}],
            },
        ),
        (
            "canonical-image-only",
            {
                **base_payload,
                "image": {"url": image_url},
            },
        ),
        (
            "compat-input-reference",
            {
                **base_payload,
                "input_reference": {"url": image_url},
            },
        ),
        (
            "legacy-image-url",
            {
                **base_payload,
                "image_url": image_url,
            },
        ),
    ]


def _build_text_payload_variants(
    prompt: str,
    duration: int,
    aspect_ratio: str,
) -> list[tuple[str, dict]]:
    """Build payload variants for prompt-only Grok video generation."""
    base_payload = {
        "model": "grok-imagine-video",
        "prompt": (prompt or "").strip(),
        "duration": duration,
        "aspect_ratio": aspect_ratio,
    }

    return [
        ("canonical-text-only", base_payload),
        ("compat-input-prompt", {"model": "grok-imagine-video", "input": {"prompt": base_payload["prompt"], "duration": duration, "aspect_ratio": aspect_ratio}}),
    ]


def _extract_error_message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return (resp.text or "").strip()[:500]

    if isinstance(payload, dict):
        detail = payload.get("detail")
        error = payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(error, dict):
            msg = error.get("message") or error.get("detail")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
            return str(error)[:500]
        if isinstance(error, str) and error.strip():
            return error.strip()
        return str(payload)[:500]

    return str(payload)[:500]


async def generate_video_from_prompt(
    prompt: str,
    output_path: str,
    duration: int = 6,
    aspect_ratio: str = "16:9",
    timeout_seconds: int = 600,
    on_progress=None,
) -> str:
    """Generate a Grok video from prompt only, without sending a reference image."""
    headers = {
        "Authorization": f"Bearer {settings.xai_api_key}",
        "Content-Type": "application/json",
    }

    safe_aspect_ratio = _normalize_grok_aspect_ratio(aspect_ratio)
    safe_duration = max(1, min(duration, 15))

    logger.info(
        "Grok prompt-only generation prepared (aspect=%s duration=%s prompt_chars=%s)",
        safe_aspect_ratio,
        safe_duration,
        len(prompt or ""),
    )

    if on_progress:
        await on_progress(20, "Iniciando geracao Grok sem imagem...")

    request_id = ""
    last_error = ""
    payload_variants = _build_text_payload_variants(
        prompt=prompt,
        duration=safe_duration,
        aspect_ratio=safe_aspect_ratio,
    )

    async with httpx.AsyncClient(timeout=30) as client:
        for variant_name, payload in payload_variants:
            try:
                resp = await client.post(f"{XAI_BASE_URL}/videos/generations", headers=headers, json=payload)
            except Exception as e:
                last_error = str(e)
                logger.warning("Grok text payload variant '%s' request failed: %s", variant_name, e)
                continue

            if resp.status_code < 400:
                data = resp.json() if resp.content else {}
                request_id = str(data.get("request_id") or "").strip()
                if request_id:
                    logger.info("Grok text payload variant accepted: %s", variant_name)
                    break
                last_error = f"{variant_name}: resposta sem request_id"
                logger.warning("Grok text payload variant '%s' returned no request_id", variant_name)
                continue

            error_detail = _extract_error_message(resp)
            last_error = f"HTTP {resp.status_code}: {error_detail}"
            logger.warning(
                "Grok text payload variant '%s' rejected (%s): %s",
                variant_name,
                resp.status_code,
                error_detail,
            )
            if resp.status_code not in (400, 404, 415, 422):
                resp.raise_for_status()

    if not request_id:
        raise RuntimeError(f"Falha ao iniciar geracao Grok sem imagem: {last_error or 'sem detalhe'}")

    logger.info(f"Grok prompt-only video generation started: {request_id}")

    if on_progress:
        await on_progress(30, "Grok gerando video sem imagem...")

    start_time = time.time()
    poll_count = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while (time.time() - start_time) < timeout_seconds:
            resp = await client.get(f"{XAI_BASE_URL}/videos/{request_id}", headers=headers)
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status")
            if status == "done":
                video = data.get("video") or {}
                video_url = str(video.get("url") or "").strip()
                if not video_url:
                    raise RuntimeError(f"Grok retornou status=done sem URL do video: {data}")
                logger.info(
                    "Grok prompt-only video generation done (request_id=%s model=%s progress=%s)",
                    request_id,
                    data.get("model"),
                    data.get("progress"),
                )
                break
            elif status in ("failed", "expired"):
                raise RuntimeError(f"Grok prompt-only video generation {status}: {data}")

            poll_count += 1
            if on_progress and poll_count % 3 == 0:
                pct = min(30 + poll_count, 70)
                await on_progress(pct, "Grok gerando video sem imagem...")

            await _async_sleep(5)
        else:
            raise TimeoutError(f"Grok prompt-only video generation timed out after {timeout_seconds}s")

    if on_progress:
        await on_progress(75, "Baixando video gerado...")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(video_url)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)

    logger.info(f"Grok prompt-only video saved: {output_path}")
    return output_path


async def optimize_prompt_for_grok(
    user_description: str,
    duration: int = 7,
    has_reference_image: bool = False,
    tone: str = "",
) -> str:
    """Convert user's description into an optimized Grok video prompt with PT-BR audio."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    system = _GROK_SYSTEM_PROMPT.replace("{duration}", str(duration))
    user_msg = user_description
    style_map = {
        "cinematic": "cinematografico epico",
        "commercial": "comercial premium de produto",
        "meme": "meme viral engracado",
        "anime": "anime japones",
        "drama": "drama emotivo",
        "vfx": "efeitos visuais surrealistas",
    }
    normalized_tone = str(tone or "").strip().lower()
    if normalized_tone:
        tone_hint = style_map.get(normalized_tone, normalized_tone)
        user_msg += (
            "\n\nESTILO VISUAL OBRIGATORIO: "
            f"{tone_hint}. Preserve este estilo durante todo o prompt."
        )

    if has_reference_image:
        user_msg += (
            "\n\nREGRA OBRIGATORIA DE REFERENCIA: o usuario enviou imagem de referencia. "
            "O prompt deve preservar a mesma identidade e os mesmos tracos visuais principais dessa imagem. "
            "TRAVA DE CLOSE-UP: manter exatamente geometria facial, olhos, nariz, labios, mandibula, tom de pele, linha e cor do cabelo e idade aparente. "
            "Nao introduza protagonista diferente e nao faca morphing de rosto."
        )

    # Lower creativity when identity lock is required, so prompt drift is reduced.
    temperature = 0.20 if has_reference_image else 0.55

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=temperature,
            max_tokens=800,
        )
        optimized = resp.choices[0].message.content.strip()
        if _looks_like_english_template(optimized):
            rewrite = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": _PT_BR_REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": optimized},
                ],
                temperature=0.1,
                max_tokens=900,
            )
            rewritten = (rewrite.choices[0].message.content or "").strip()
            if rewritten:
                optimized = rewritten

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
        raw_image = f.read()
    image_data = base64.b64encode(raw_image).decode("utf-8")

    # Detect mime type
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/png")
    image_url = f"data:{mime_type};base64,{image_data}"
    safe_aspect_ratio = _normalize_grok_aspect_ratio(aspect_ratio)
    safe_duration = max(1, min(duration, 15))
    image_sha = hashlib.sha256(raw_image).hexdigest()[:16]

    logger.info(
        "Grok input image prepared (path=%s bytes=%s sha=%s aspect=%s duration=%s)",
        image_path,
        len(raw_image),
        image_sha,
        safe_aspect_ratio,
        safe_duration,
    )

    if on_progress:
        await on_progress(20, "Iniciando geracao Grok...")

    # Step 1: Start generation (try canonical schema first; fallback for compatibility)
    request_id = ""
    last_error = ""
    payload_variants = _build_payload_variants(
        prompt=prompt,
        image_url=image_url,
        duration=safe_duration,
        aspect_ratio=safe_aspect_ratio,
    )

    async with httpx.AsyncClient(timeout=30) as client:
        for variant_name, payload in payload_variants:
            try:
                resp = await client.post(f"{XAI_BASE_URL}/videos/generations", headers=headers, json=payload)
            except Exception as e:
                last_error = str(e)
                logger.warning("Grok payload variant '%s' request failed: %s", variant_name, e)
                continue

            if resp.status_code < 400:
                data = resp.json() if resp.content else {}
                request_id = str(data.get("request_id") or "").strip()
                if request_id:
                    logger.info("Grok payload variant accepted: %s", variant_name)
                    break
                last_error = f"{variant_name}: resposta sem request_id"
                logger.warning("Grok payload variant '%s' returned no request_id", variant_name)
                continue

            error_detail = _extract_error_message(resp)
            last_error = f"HTTP {resp.status_code}: {error_detail}"
            logger.warning(
                "Grok payload variant '%s' rejected (%s): %s",
                variant_name,
                resp.status_code,
                error_detail,
            )

            # Only continue fallback chain on client-side validation errors.
            if resp.status_code not in (400, 404, 415, 422):
                resp.raise_for_status()

    if not request_id:
        raise RuntimeError(f"Falha ao iniciar geracao Grok com imagem de referencia: {last_error or 'sem detalhe'}")

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
                video = data.get("video") or {}
                video_url = str(video.get("url") or "").strip()
                if not video_url:
                    raise RuntimeError(f"Grok retornou status=done sem URL do video: {data}")

                logger.info(
                    "Grok video generation done (request_id=%s model=%s progress=%s)",
                    request_id,
                    data.get("model"),
                    data.get("progress"),
                )
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
