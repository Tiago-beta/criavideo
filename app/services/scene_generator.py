"""
Scene Generator — Uses Google Nano Banana (Gemini 3.1 Flash Image)
to generate background images for each scene of the music video.
Includes Image Bank: reuses previously generated images via semantic tags.
"""
import os
import shutil
import asyncio
import logging
import uuid
import mimetypes
import re
import base64
import httpx
from pathlib import Path
from google import genai
from google.genai import types
import openai
from sqlalchemy import select, update, text
from app.config import get_settings
from app.database import async_session

logger = logging.getLogger(__name__)
settings = get_settings()

google_client = genai.Client(api_key=settings.google_ai_api_key)
openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

_TEMPORAL_PROMPT_HINT_RE = re.compile(
    r"(\bshot\s*\d+\b|\bcena\s*\d+\b|\bscene\s*\d+\b|\bsegundo(?:s)?\b|\bsecond(?:s)?\b|\b\d+(?:[.,]\d+)?\s*s\s*:|\[\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*\])",
    re.IGNORECASE,
)


async def analyze_lyrics_for_scenes(lyrics_text: str, lyrics_words: list, duration: float, style_hint: str = "") -> list[dict]:
    """Use GPT-4o-mini to split lyrics into scenes with visual descriptions."""
    # Scale scene count based on duration.
    # Short videos must not explode into many images (e.g. 10s => 1 image).
    if duration <= 12:
        scene_min, scene_max = 1, 1
    elif duration <= 20:
        scene_min, scene_max = 2, 2
    elif duration <= 45:
        scene_min, scene_max = 3, 4
    elif duration <= 120:
        scene_min, scene_max = 5, 8
    elif duration <= 300:
        scene_min, scene_max = 10, 14
    elif duration <= 900:
        scene_min, scene_max = 14, 20
    else:
        scene_min, scene_max = 20, 25

    if duration <= 12:
        per_scene_rule = "Use EXACTLY 1 scene that covers the full duration."
    elif duration <= 45:
        per_scene_rule = "Each scene should last between 6-15 seconds."
    else:
        per_scene_rule = "Each scene should last between 12-22 seconds."

    prompt = f"""You are a music video director. Given these song lyrics and total duration ({duration:.1f} seconds),
split the song into {scene_min}-{scene_max} visual scenes for a music video. {per_scene_rule}
{"NOTE: The video is very long. Focus on creating diverse, visually distinct scenes. They will be cycled/repeated throughout the video, so variety is key." if duration > 300 else ""}
{f"STYLE DIRECTION: {style_hint}. All visual_prompt descriptions MUST follow this style." if style_hint else ""}

CRITICAL: Each scene MUST have a UNIQUE visual setting. Do NOT repeat the same elements across scenes (e.g. do not put doves/birds in every scene). Vary the landscapes, lighting, time of day, and focal objects. Each scene should feel visually distinct.

THEME-AWARE VISUAL RULES:
- If the lyrics are gospel, religious, spiritual, Christian, worship, or faith-related: ALL scenes MUST use NATURE imagery (mountains, forests, rivers, waterfalls, oceans, sunrises, sunsets, starry skies, fields of flowers, rain, clouds with light rays, valleys, paths through woods, calm lakes). NEVER use random urban, sci-fi, abstract, or unrelated imagery for gospel content. The visuals must evoke peace, hope, divine presence, and spiritual connection through the beauty of nature.
- Match the visual theme to the actual content and emotion of the lyrics. If the lyrics talk about storms, show dramatic skies/ocean. If about peace, show serene landscapes. If about strength, show majestic mountains.

For each scene, provide:
- scene_index: sequential number starting from 0
- start_time: approximate start in seconds
- end_time: approximate end in seconds
- lyrics_segment: the lyrics for this section
- visual_prompt: a detailed description for generating a background image (describe mood, colors, setting, objects — NO text/words in the image, NO people faces, NO repeated elements from other scenes)
- tags: an array of 5-8 English keywords describing the image content. Include diverse categories: setting (forest, city, ocean), time (night, dawn, sunset), mood (melancholic, joyful, dramatic), colors (golden, blue, red), objects (candle, rain, mountains), style (abstract, realistic, aerial). Example: ["sunset", "ocean", "warm", "golden", "beach", "peaceful", "waves"]
- is_chorus: whether this is a chorus/highlight moment

The lyrics:
{lyrics_text}

Word timestamps (first 20): {str(lyrics_words[:20]) if lyrics_words else 'not available'}

Respond ONLY with a JSON array. No markdown, no explanation."""

    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        response_format={"type": "json_object"}
    )

    import json
    result = json.loads(response.choices[0].message.content)
    scenes = result if isinstance(result, list) else result.get("scenes", [])

    normalized = [s for s in scenes if isinstance(s, dict)]
    if not normalized:
        normalized = [{
            "scene_index": 0,
            "start_time": 0,
            "end_time": max(float(duration or 1), 1.0),
            "lyrics_segment": (lyrics_text or "").strip()[:500],
            "visual_prompt": "Cinematic scene inspired by the song mood, colors and emotional arc.",
            "tags": ["cinematic", "music", "mood", "dynamic", "atmospheric"],
            "is_chorus": True,
        }]

    if len(normalized) > scene_max:
        normalized = normalized[:scene_max]

    # Deterministic fix for short videos: keep exactly one image for ~10s clips.
    if duration <= 12 and normalized:
        normalized = [normalized[0]]

    total_duration = max(float(duration or 1), 1.0)
    block = total_duration / max(len(normalized), 1)
    for idx, scene in enumerate(normalized):
        scene["scene_index"] = idx
        scene["start_time"] = round(idx * block, 3)
        scene["end_time"] = round(total_duration if idx == len(normalized) - 1 else (idx + 1) * block, 3)

        if not str(scene.get("lyrics_segment", "")).strip():
            scene["lyrics_segment"] = (lyrics_text or "").strip()[:500]
        if not str(scene.get("visual_prompt", "")).strip():
            scene["visual_prompt"] = "Cinematic scene inspired by the song mood, colors and emotional arc."
        tags = scene.get("tags", [])
        if not isinstance(tags, list):
            scene["tags"] = ["cinematic", "music", "mood", "atmospheric"]

    scenes = normalized
    return scenes


def _clean_prompt_line_for_scene_merge(line: str) -> str:
    cleaned = str(line or "").strip()
    if not cleaned:
        return ""

    strip_patterns = (
        r"^\s*\[\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*\]\s*",
        r"^\s*\d+\s*(?:segundo(?:s)?|second(?:s)?|sec|s)\s*[:\-]\s*",
        r"^\s*(?:shot|scene|cena)\s*\d+\s*[:\-]\s*",
        r"^\s*\d+\s*[\)\.\-:]\s*",
    )
    for pattern in strip_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -;,.")
    return cleaned


def _extract_prompt_beats(prompt: str) -> list[str]:
    raw_text = str(prompt or "").replace("\r", "\n")
    lines = [line.strip() for line in raw_text.split("\n") if line and line.strip()]

    if len(lines) == 1 and _TEMPORAL_PROMPT_HINT_RE.search(lines[0]):
        chunks = re.split(
            r"(?:(?<=\.)\s+)?(?=\d+\s*(?:segundo(?:s)?|second(?:s)?|s)\s*:)",
            lines[0],
            flags=re.IGNORECASE,
        )
        split_lines = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
        if split_lines:
            lines = split_lines

    beats: list[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = _clean_prompt_line_for_scene_merge(line)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        beats.append(cleaned)

    return beats


def _build_single_scene_anchor_fallback(prompt: str, duration_seconds: int = 0) -> str:
    beats = _extract_prompt_beats(prompt)
    if beats:
        merged = "; ".join(beats[:6])
    else:
        merged = re.sub(r"\s+", " ", str(prompt or "")).strip()

    if len(merged) > 1000:
        merged = merged[:1000].rsplit(" ", 1)[0].strip() or merged[:1000]

    duration_hint = f"{int(duration_seconds)}s" if duration_seconds else "short"
    return (
        "Single coherent cinematic frame. "
        "Do not create collage, split screen, storyboard, grid, triptych, diptych, or multiple panels. "
        "All key elements must appear naturally in the same place and same moment of the story. "
        f"Story context ({duration_hint}): {merged}. "
        "Keep one clear protagonist identity and realistic proportions. "
        "If multiple cars are required, place them in one believable composition "
        "(for example rear-view mirror, side window, or deep background), never as separate tiles. "
        "No text, no logos, no captions."
    )


async def build_single_scene_anchor_prompt(source_prompt: str, duration_seconds: int = 0) -> str:
    """Convert temporal/multi-shot prompt into one coherent still-frame prompt for Nano Banana."""
    base_prompt = str(source_prompt or "").strip()
    fallback_prompt = _build_single_scene_anchor_fallback(base_prompt, duration_seconds=duration_seconds)
    if not base_prompt:
        return fallback_prompt

    should_rewrite = bool(_TEMPORAL_PROMPT_HINT_RE.search(base_prompt)) or len(base_prompt) > 420
    if not should_rewrite:
        return fallback_prompt

    if not (settings.openai_api_key or "").strip():
        return fallback_prompt

    system_prompt = (
        "You rewrite temporal video prompts into one coherent still-frame prompt for an image model. "
        "Return only one paragraph in English. "
        "Keep all critical entities and actions, but merge them into one plausible instant. "
        "Never output collage, split-screen, storyboard, panel grid, triptych, or diptych. "
        "Use one camera viewpoint and one physically coherent environment. "
        "No markdown, no bullet points, no explanations."
    )
    user_prompt = (
        f"Duration: {int(duration_seconds) if duration_seconds else 0}s\n\n"
        f"Source prompt:\n{base_prompt}\n\n"
        "Return the final single-frame prompt now."
    )

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        rewritten = (response.choices[0].message.content or "").strip()
        rewritten = re.sub(r"\s+", " ", rewritten).strip()
        if not rewritten:
            return fallback_prompt

        forbidden_tokens = (
            "split screen",
            "split-screen",
            "storyboard",
            "multi-panel",
            "multi panel",
            "collage",
            "panel grid",
            "triptych",
            "diptych",
        )
        lowered = rewritten.lower()
        if any(token in lowered for token in forbidden_tokens):
            return fallback_prompt

        if len(rewritten) > 1800:
            rewritten = rewritten[:1800].rsplit(" ", 1)[0].strip() or rewritten[:1800]
        return rewritten
    except Exception as e:
        logger.warning("Single-scene prompt rewrite failed; using fallback merge: %s", e)
        return fallback_prompt


def merge_reference_images_with_nano_banana(
    image_paths: list[str],
    scene_prompt: str,
    aspect_ratio: str = "16:9",
    output_path: str = "",
) -> str:
    """Merge multiple uploaded references into one coherent image using Nano Banana.

    This is used by Similar mode so a scene can consume several user images while still
    generating from a single consolidated reference frame.
    """
    valid_paths: list[str] = []
    for raw in (image_paths or []):
        path = str(raw or "").strip()
        if path and os.path.exists(path) and path not in valid_paths:
            valid_paths.append(path)

    if not valid_paths:
        raise RuntimeError("Nenhuma imagem valida foi enviada para fusao")

    if len(valid_paths) == 1:
        if not output_path:
            return valid_paths[0]
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        shutil.copy2(valid_paths[0], output_path)
        if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
            raise RuntimeError("Falha ao preparar imagem unica de referencia")
        return output_path

    target_path = output_path or valid_paths[0]
    if not target_path:
        raise RuntimeError("Caminho de saida invalido para fusao de imagens")

    try:
        scene_goal = re.sub(r"\s+", " ", str(scene_prompt or "")).strip()
        if not scene_goal:
            scene_goal = "Cena cinematografica coerente, realista e com continuidade visual."

        contents_payload: list = []
        for path in valid_paths[:6]:
            mime_type = mimetypes.guess_type(path)[0] or "image/png"
            with open(path, "rb") as ref_file:
                ref_bytes = ref_file.read()
            if not ref_bytes:
                continue
            contents_payload.append(types.Part.from_bytes(data=ref_bytes, mime_type=mime_type))

        if len(contents_payload) < 2:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(valid_paths[0], target_path)
            return target_path

        contents_payload.append(
            (
                "Combine TODAS as imagens de referencia enviadas em UMA unica composicao cinematografica coerente. "
                "Nao crie collage, split-screen, grid, storyboard, triptych, diptych ou paineis separados. "
                "Mantenha os elementos principais de cada referencia em um mesmo ambiente visual plausivel, "
                "com enquadramento natural e continuidade de luz/cor. "
                "Sem textos, logos, marcas d'agua ou sobreposicoes. "
                f"Objetivo da cena: {scene_goal}"
            )
        )

        response = google_client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=contents_payload,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )

        parts = []
        direct_parts = getattr(response, "parts", None)
        if isinstance(direct_parts, list):
            parts.extend(direct_parts)

        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            cand_parts = getattr(content, "parts", None) if content is not None else None
            if isinstance(cand_parts, list):
                parts.extend(cand_parts)

        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is None:
                continue

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            try:
                image = part.as_image()
                image.save(target_path)
            except Exception:
                data = getattr(inline_data, "data", None)
                if not data:
                    continue
                raw = bytes(data) if not isinstance(data, str) else base64.b64decode(data)
                with open(target_path, "wb") as out:
                    out.write(raw)

            if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                return target_path

    except Exception as e:
        logger.warning("Nano Banana merge failed: %s", e)
        raise RuntimeError("Falha ao unir as imagens com Nano Banana") from e

    raise RuntimeError("Nano Banana nao retornou imagem ao unir referencias")


def generate_scene_image(
    prompt: str,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    allow_faces: bool = False,
    reference_image_path: str = "",
    provider_preference: str = "",
    metadata: dict | None = None,
    reference_mode: str = "",
) -> str:
    """Generate a single scene image using Nano Banana. Synchronous (runs in thread).
    Set allow_faces=True for realistic/multi-clip videos that need character consistency.
    If reference_image_path is provided, Nano Banana receives that exact image as identity anchor."""
    if allow_faces:
        style_prefix = (
            "Cinematic, high quality, professional lighting, photorealistic. "
            "No text or words in the image. "
        )
    else:
        style_prefix = (
            "Cinematic, high quality, professional lighting, music video aesthetic. "
            "No text or words in the image. No human faces. "
        )
    # Detect gospel/religious content. Keep strict nature mode only for no-face scenes.
    # For realistic/face-enabled scenes, preserve lyric-specific imagery to avoid repetitive outputs.
    _gospel_kw = ["god", "lord", "faith", "pray", "worship", "church", "gospel",
                  "heaven", "divine", "spirit", "holy", "jesus", "christ", "deus",
                  "senhor", "louvor", "adoracao", "gospel", "fe", "oracao", "ceu"]
    prompt_lower = prompt.lower()
    if any(kw in prompt_lower for kw in _gospel_kw):
        if allow_faces:
            style_prefix = (
                "Cinematic, high quality, professional lighting, photorealistic. "
                "Spiritual and emotional atmosphere aligned with the lyrics. "
                "Use the exact symbols and actions requested in the prompt. "
                "Do not default to generic wheat fields, white robes, or repeated pastoral clichés unless explicitly requested. "
                "No text or words in the image. "
            )
        else:
            style_prefix = (
                "Beautiful nature landscape, cinematic, high quality, professional lighting. "
                "Majestic scenery: mountains, forests, rivers, sunlight through clouds, serene lakes, golden hour. "
                "Spiritual and peaceful atmosphere through nature. "
                "No text or words in the image. No human faces. No religious symbols or objects. "
            )
    reference_mode_normalized = str(reference_mode or "").strip().lower()
    face_identity_only = reference_mode_normalized in {"face_identity_only", "face_only", "persona_face"}
    full_prompt = style_prefix + prompt
    has_reference_image = bool(reference_image_path and os.path.exists(reference_image_path))
    if has_reference_image:
        if face_identity_only:
            full_prompt = (
                f"{style_prefix}{prompt}\n\n"
                "FACE IDENTITY LOCK (MANDATORY): use the attached reference image only for facial identity. "
                "Preserve face structure, eyes, nose, lips, jawline, skin tone, apparent age, and hairline/color. "
                "Do not preserve clothing, background, pose, framing, lighting, color palette, props, or environment from the reference photo. "
                "Generate new wardrobe, location, action, composition, and mood from the prompt."
            )
        else:
            full_prompt = (
                f"{style_prefix}{prompt}\n\n"
                "REFERENCE IMAGE LOCK (MANDATORY): Use the attached reference image as the exact subject identity anchor. "
                "Preserve the same face, skin tone, hair color/style, age appearance, and overall likeness. "
                "Only change scene composition, camera movement, and environment requested by the prompt."
            )
    provider_pref = str(provider_preference or "").strip().lower()

    def _set_provider_meta(provider_name: str) -> None:
        if isinstance(metadata, dict):
            metadata["provider"] = provider_name

    def _extract_parts(resp):
        parts = []
        direct_parts = getattr(resp, "parts", None)
        if isinstance(direct_parts, list):
            parts.extend(direct_parts)
        elif direct_parts:
            try:
                parts.extend(list(direct_parts))
            except TypeError:
                pass

        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            cand_parts = getattr(content, "parts", None) if content is not None else None
            if isinstance(cand_parts, list):
                parts.extend(cand_parts)
            elif cand_parts:
                try:
                    parts.extend(list(cand_parts))
                except TypeError:
                    pass
        return parts

    def _save_inline_part(part) -> bool:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is None:
            return False

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            image = part.as_image()
            image.save(output_path)
            _set_provider_meta("gemini")
            return True
        except Exception:
            data = getattr(inline_data, "data", None)
            if not data:
                return False
            try:
                import base64

                if isinstance(data, str):
                    raw = base64.b64decode(data)
                else:
                    raw = bytes(data)

                with open(output_path, "wb") as f:
                    f.write(raw)
                ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
                if ok:
                    _set_provider_meta("gemini")
                return ok
            except Exception:
                return False

    def _build_reference_part() -> types.Part | None:
        if not has_reference_image:
            return None

        mime_type = mimetypes.guess_type(reference_image_path)[0] or "image/png"
        try:
            with open(reference_image_path, "rb") as ref_file:
                ref_bytes = ref_file.read()
            if not ref_bytes:
                return None
            return types.Part.from_bytes(data=ref_bytes, mime_type=mime_type)
        except Exception as e:
            logger.warning("Failed to load reference image for Nano Banana (%s): %s", reference_image_path, e)
            return None

    def _openai_image_size(ar: str) -> str:
        if ar == "9:16":
            return "1024x1536"
        if ar == "1:1":
            return "1024x1024"
        return "1536x1024"

    def _save_openai_reference_edit_image(source_prompt: str) -> bool:
        if not has_reference_image:
            return False
        if not (settings.openai_api_key or "").strip():
            return False

        try:
            client = openai.OpenAI(api_key=settings.openai_api_key)
            with open(reference_image_path, "rb") as ref_file:
                img_resp = client.images.edit(
                    model=(settings.persona_image_openai_model or "gpt-image-1"),
                    image=ref_file,
                    prompt=(source_prompt or "")[:3800],
                    size=_openai_image_size(aspect_ratio),
                )

            data_items = getattr(img_resp, "data", None) or []
            if not data_items:
                return False

            item = data_items[0]
            b64_data = getattr(item, "b64_json", None)
            if not b64_data and isinstance(item, dict):
                b64_data = item.get("b64_json")

            if b64_data:
                import base64

                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                raw = base64.b64decode(b64_data)
                with open(output_path, "wb") as f:
                    f.write(raw)
                ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
                if ok:
                    _set_provider_meta("openai-edit")
                return ok

            img_url = getattr(item, "url", None)
            if not img_url and isinstance(item, dict):
                img_url = item.get("url")

            if img_url:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with httpx.Client(timeout=120, follow_redirects=True) as client_http:
                    resp = client_http.get(img_url)
                    resp.raise_for_status()
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
                if ok:
                    _set_provider_meta("openai-edit")
                return ok
        except Exception as e:
            logger.warning(f"OpenAI reference edit image generation failed: {e}")

        return False

    def _save_openai_fallback_image(source_prompt: str) -> bool:
        if not (settings.openai_api_key or "").strip():
            return False

        try:
            client = openai.OpenAI(api_key=settings.openai_api_key)
            img_resp = client.images.generate(
                model="gpt-image-1",
                prompt=(source_prompt or "")[:3800],
                size=_openai_image_size(aspect_ratio),
            )

            data_items = getattr(img_resp, "data", None) or []
            if not data_items:
                return False

            item = data_items[0]
            b64_data = getattr(item, "b64_json", None)
            if not b64_data and isinstance(item, dict):
                b64_data = item.get("b64_json")

            if b64_data:
                import base64

                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                raw = base64.b64decode(b64_data)
                with open(output_path, "wb") as f:
                    f.write(raw)
                ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
                if ok:
                    _set_provider_meta("openai-generate")
                return ok

            img_url = getattr(item, "url", None)
            if not img_url and isinstance(item, dict):
                img_url = item.get("url")

            if img_url:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with httpx.Client(timeout=120, follow_redirects=True) as client_http:
                    resp = client_http.get(img_url)
                    resp.raise_for_status()
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
                if ok:
                    _set_provider_meta("openai-generate")
                return ok
        except Exception as e:
            logger.warning(f"OpenAI fallback image generation failed: {e}")

        return False

    def _save_reference_passthrough_image() -> bool:
        if not has_reference_image:
            return False
        if face_identity_only:
            return False
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            shutil.copy2(reference_image_path, output_path)
            ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
            if ok:
                _set_provider_meta("reference-passthrough")
            return ok
        except Exception as e:
            logger.warning(f"Reference passthrough fallback failed: {e}")
            return False

    def _save_local_placeholder_image() -> bool:
        try:
            from PIL import Image, ImageDraw

            if aspect_ratio == "9:16":
                width, height = 1080, 1920
            elif aspect_ratio == "1:1":
                width, height = 1080, 1080
            else:
                width, height = 1920, 1080

            seed = sum(ord(ch) for ch in (prompt or "")) % 255
            base_top = (20 + seed // 4, 45 + seed // 6, 78 + seed // 8)
            base_bottom = (90 + seed // 3, 65 + seed // 5, 40 + seed // 7)

            img = Image.new("RGB", (width, height), base_top)
            draw = ImageDraw.Draw(img)

            for y in range(height):
                t = y / max(1, height - 1)
                r = int(base_top[0] * (1 - t) + base_bottom[0] * t)
                g = int(base_top[1] * (1 - t) + base_bottom[1] * t)
                b = int(base_top[2] * (1 - t) + base_bottom[2] * t)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

            # Add subtle focal ellipse so fallback does not look flat.
            pad = int(min(width, height) * 0.18)
            draw.ellipse((pad, int(height * 0.12), width - pad, int(height * 0.72)), outline=(235, 220, 170), width=6)

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            img.save(output_path)
            ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
            if ok:
                _set_provider_meta("local-placeholder")
            return ok
        except Exception as e:
            logger.warning(f"Local placeholder image generation failed: {e}")
            return False

    if provider_pref == "openai":
        if _save_openai_reference_edit_image(full_prompt):
            logger.info(f"Scene image saved via OpenAI preferred reference-edit: {output_path}")
            return output_path

        if _save_openai_fallback_image(full_prompt):
            logger.info(f"Scene image saved via OpenAI preferred generate: {output_path}")
            return output_path

        logger.warning("OpenAI preferred provider failed; falling back to default provider chain")

    # Retry with a simplified prompt when provider returns metadata without inline image.
    second_attempt = style_prefix + "Single clear cinematic composition, one focal subject, no text, no overlays."
    if face_identity_only and has_reference_image:
        second_attempt += (
            " Use the reference image only for facial identity. Create new clothing, background, pose, lighting, and environment."
        )
    prompt_attempts = [
        full_prompt,
        second_attempt,
    ]
    last_response_text = ""
    reference_part = _build_reference_part()

    for idx, attempt_prompt in enumerate(prompt_attempts, start=1):
        contents_payload = [attempt_prompt]
        if reference_part is not None:
            contents_payload = [reference_part, attempt_prompt]

        try:
            response = google_client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=contents_payload,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                    ),
                )
            )
        except Exception as e:
            if reference_part is None:
                raise
            logger.warning("Nano Banana reference-image call failed, retrying without image: %s", e)
            response = google_client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=[attempt_prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                    ),
                )
            )

        parts = _extract_parts(response)
        for part in parts:
            if _save_inline_part(part):
                logger.info(f"Scene image saved: {output_path}")
                return output_path

        # Some SDK versions expose text warnings for image responses.
        # Only inspect text when image parts are missing.
        try:
            response_text = (getattr(response, "text", "") or "").strip()
            if response_text:
                last_response_text = response_text[:240]
        except Exception:
            pass

        logger.warning(
            "Nano Banana returned no inline image part (attempt %d/%d)",
            idx,
            len(prompt_attempts),
        )

    # Fallback 1: OpenAI image generation if Gemini image returns empty payload.
    if _save_reference_passthrough_image():
        logger.warning(f"Scene image saved via reference passthrough fallback: {output_path}")
        return output_path

    # Fallback 2: OpenAI image generation if Gemini image returns empty payload.
    if _save_openai_fallback_image(full_prompt):
        logger.info(f"Scene image saved via OpenAI fallback: {output_path}")
        return output_path

    # Fallback 3: Local generated placeholder so the render pipeline can continue.
    if _save_local_placeholder_image():
        logger.warning(f"Scene image saved via local placeholder fallback: {output_path}")
        return output_path

    if last_response_text:
        raise RuntimeError(f"Nano Banana did not return an image: {last_response_text}")
    raise RuntimeError("Nano Banana did not return an image")


# ── Image Bank: search & save ──

async def search_image_bank(user_id: int, tags: list[str], aspect_ratio: str,
                            style: str, exclude_ids: set[int] | None = None) -> dict | None:
    """Search the image bank for a reusable image matching the given tags.
    Requires at least 3 tags in common. Prefers same style, then most tags matched.
    exclude_ids prevents reusing the same bank image twice in one video.
    Searches across ALL users for maximum reuse."""
    if not tags or len(tags) < 3:
        return None

    async with async_session() as db:
        tag_array = "{" + ",".join(tags) + "}"

        # Build exclusion clause
        exclude_clause = ""
        params = {
            "ar": aspect_ratio,
            "tag_arr": tag_array,
            "style": style or "",
        }
        if exclude_ids:
            exclude_clause = "AND id != ALL(:excluded)"
            params["excluded"] = list(exclude_ids)

        query = text(f"""
            SELECT id, file_path, tags, style, prompt,
                   array_length(
                       ARRAY(SELECT unnest(tags) INTERSECT SELECT unnest(:tag_arr::text[])),
                       1
                   ) AS match_count
            FROM image_bank
            WHERE aspect_ratio = :ar
              AND tags && :tag_arr::text[]
              {exclude_clause}
            ORDER BY
                CASE WHEN style = :style THEN 0 ELSE 1 END,
                match_count DESC
            LIMIT 1
        """)
        result = await db.execute(query, params)
        row = result.fetchone()

        if row and row.match_count and row.match_count >= 3:
            # Increment reuse count
            await db.execute(
                text("UPDATE image_bank SET reuse_count = reuse_count + 1 WHERE id = :id"),
                {"id": row.id},
            )
            await db.commit()
            logger.info(f"Image bank HIT: id={row.id}, matched {row.match_count} tags, path={row.file_path}")
            return {"id": row.id, "file_path": row.file_path, "tags": row.tags, "prompt": row.prompt}

    return None


async def save_to_image_bank(user_id: int, tags: list[str], style: str,
                              aspect_ratio: str, prompt: str, source_path: str) -> None:
    """Save a newly generated image to the image bank for future reuse."""
    if not tags or not os.path.exists(source_path):
        return

    # Copy to bank directory with unique name
    bank_dir = Path(settings.media_dir) / "image_bank" / str(user_id)
    bank_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(source_path).suffix or ".png"
    bank_filename = f"{uuid.uuid4().hex}{ext}"
    bank_path = str(bank_dir / bank_filename)

    try:
        shutil.copy2(source_path, bank_path)
    except Exception as e:
        logger.warning(f"Failed to copy image to bank: {e}")
        return

    async with async_session() as db:
        tag_array = "{" + ",".join(tags) + "}"
        await db.execute(
            text("""
                INSERT INTO image_bank (user_id, tags, style, aspect_ratio, prompt, file_path)
                VALUES (:uid, :tags::text[], :style, :ar, :prompt, :path)
            """),
            {
                "uid": user_id,
                "tags": tag_array,
                "style": style or "",
                "ar": aspect_ratio,
                "prompt": prompt,
                "path": bank_path,
            },
        )
        await db.commit()
        logger.info(f"Image saved to bank: {bank_path} tags={tags}")


async def generate_all_scenes(
    project_id: int,
    lyrics_text: str,
    lyrics_words: list,
    duration: float,
    aspect_ratio: str = "16:9",
    style_hint: str = "",
    user_id: int = 0,
    on_progress=None,
) -> list[dict]:
    """Full pipeline: analyze lyrics → search bank / generate images for each scene."""
    media_dir = Path(settings.media_dir) / "images" / str(project_id)
    media_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Analyze lyrics into scenes (now includes tags)
    scenes = await analyze_lyrics_for_scenes(lyrics_text, lyrics_words, duration, style_hint=style_hint)

    # Step 2: For each scene, try bank first, then generate
    loop = asyncio.get_event_loop()
    results = []
    total = len(scenes)
    reused = 0
    used_bank_ids = set()  # prevent same bank image in multiple scenes

    for i, scene in enumerate(scenes):
        idx = scene.get("scene_index", len(results))
        visual_prompt = scene.get("visual_prompt", "Abstract colorful background")
        scene_tags = scene.get("tags", [])
        if style_hint:
            visual_prompt = f"{style_hint}. {visual_prompt}"

        output_path = str(media_dir / f"scene_{idx:03d}.png")

        # Try image bank first
        bank_hit = None
        if user_id and scene_tags:
            try:
                bank_hit = await search_image_bank(user_id, scene_tags, aspect_ratio, style_hint, used_bank_ids)
            except Exception as e:
                logger.warning(f"Image bank search failed: {e}")

        if bank_hit and os.path.exists(bank_hit["file_path"]):
            # Reuse from bank — copy to project dir
            try:
                shutil.copy2(bank_hit["file_path"], output_path)
                scene["image_path"] = output_path
                scene["from_bank"] = True
                used_bank_ids.add(bank_hit["id"])
                reused += 1
                logger.info(f"Scene {idx}: reused from bank (tags match: {scene_tags})")
            except Exception as e:
                logger.warning(f"Bank copy failed, generating new: {e}")
                bank_hit = None

        if not bank_hit or not scene.get("image_path"):
            # Generate new image
            try:
                path = await loop.run_in_executor(
                    None, generate_scene_image, visual_prompt, aspect_ratio, output_path
                )
                scene["image_path"] = path
                scene["from_bank"] = False

                # Save to bank for future reuse
                if user_id and scene_tags:
                    try:
                        await save_to_image_bank(
                            user_id, scene_tags, style_hint, aspect_ratio, visual_prompt, path
                        )
                    except Exception as e:
                        logger.warning(f"Failed to save to image bank: {e}")
            except Exception as e:
                logger.error(f"Failed to generate scene {idx}: {e}")
                scene["image_path"] = None

        results.append(scene)

        if on_progress and total > 0:
            await on_progress(i + 1, total)

    if reused > 0:
        logger.info(f"Image bank summary: {reused}/{total} scenes reused, {total - reused} newly generated")

    return results
