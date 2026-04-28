"""
Persona image generation service.
Creates realistic persona portraits using OpenAI gpt-image-1.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import uuid
from pathlib import Path

import httpx
import openai

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

PERSONA_TYPES = ("homem", "mulher", "crianca", "familia", "natureza", "desenho", "personalizado")
PERSONA_LABELS = {
    "homem": "Homem",
    "mulher": "Mulher",
    "crianca": "Crianca",
    "familia": "Familia",
    "natureza": "Natureza",
    "desenho": "Desenho",
    "personalizado": "Personalizado",
}
NATURE_SUBTYPES = {"gato", "cachorro", "papagaio", "outros"}
DRAWING_STYLES = {
    "cartoon",
    "3d",
    "anime",
    "comic",
    "manga",
    "pixar",
    "pixel_art",
    "aquarela",
    "outros",
}

_LONG_TEXT_MAX_LEN_BY_KEY = {
    "descricao_extra": 1800,
    "descricao_persona": 1000,
    "personagem_base": 420,
    "estilo_desenho_custom": 260,
    "outros_texto": 260,
    "referencia_visual": 1400,
}

_FULL_BODY_HINTS = (
    "corpo inteiro",
    "full body",
    "head to toe",
    "da cabeca aos pes",
    "dos pes a cabeca",
    "roupa",
    "calca",
    "tenis",
    "sapato",
    "vestido",
    "jaqueta",
)

_DRAWING_STYLE_GUIDANCE = {
    "3d": (
        "Render style: hyper-detailed cinematic 3D character, realistic materials, physically based rendering, "
        "sharp micro-details, dramatic studio lighting."
    ),
    "anime": "Render style: premium anime illustration, clean linework, expressive eyes, dynamic shading.",
    "comic": "Render style: high-end comic art, bold forms, strong contrast, dynamic posing.",
    "manga": "Render style: manga quality, crisp lines, controlled screentone shading, expressive features.",
    "pixar": "Render style: stylized 3D family-film look, polished materials, cinematic lighting.",
    "pixel_art": "Render style: high-quality pixel art with intentional palette and readable silhouette.",
    "aquarela": "Render style: watercolor illustration, textured brushwork, soft tonal transitions.",
    "cartoon": "Render style: premium cartoon illustration with clear silhouette and rich color harmony.",
}


def normalize_persona_type(value: str) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "desenho": "desenho",
        "personalizado": "personalizado",
        "personalizada": "personalizado",
        "custom": "personalizado",
        "crianca": "crianca",
        "familia": "familia",
    }
    normalized = mapping.get(raw, raw)
    if normalized in PERSONA_TYPES:
        return normalized
    return "natureza"


def _clean_text(value: object, max_len: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_len].strip()


def _attribute_max_len(key: str) -> int:
    return _LONG_TEXT_MAX_LEN_BY_KEY.get(key, 140)


def _attributes_joined_text(attributes: dict) -> str:
    if not isinstance(attributes, dict):
        return ""
    values = [str(v or "") for v in attributes.values()]
    return " ".join(values)


def _should_use_full_body_composition(persona_type: str, attributes: dict) -> bool:
    normalized_type = normalize_persona_type(persona_type)
    if normalized_type in {"desenho", "personalizado"}:
        return True

    joined = _clean_text(_attributes_joined_text(attributes), max_len=2400).lower()
    return any(hint in joined for hint in _FULL_BODY_HINTS)


def _pick_persona_image_size(persona_type: str, attributes: dict) -> str:
    if _should_use_full_body_composition(persona_type, attributes):
        return "1024x1536"
    return "1024x1024"


def _mime_to_extension(mime_type: str, fallback: str = ".png") -> str:
    normalized = str(mime_type or "").lower().strip()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    return mapping.get(normalized, fallback)


def default_persona_attributes(persona_type: str) -> dict:
    persona_type = normalize_persona_type(persona_type)
    if persona_type == "homem":
        return {
            "idade_aparente": "adulto jovem",
            "cor_pele": "morena",
            "etnia": "latino",
            "cabelo": "castanho curto",
            "roupa": "camisa casual neutra",
            "expressao": "calma e confiante",
            "cenario": "ambiente externo natural",
        }
    if persona_type == "mulher":
        return {
            "idade_aparente": "adulta jovem",
            "cor_pele": "morena clara",
            "etnia": "latina",
            "cabelo": "castanho medio",
            "roupa": "blusa casual neutra",
            "expressao": "calma e acolhedora",
            "cenario": "ambiente externo natural",
        }
    if persona_type == "crianca":
        return {
            "idade_aparente": "8 a 10 anos",
            "cor_pele": "morena clara",
            "etnia": "latina",
            "cabelo": "castanho",
            "roupa": "roupa casual confortavel",
            "expressao": "alegre e curiosa",
            "cenario": "parque ao ar livre",
        }
    if persona_type == "familia":
        return {
            "composicao": "casal com uma crianca",
            "faixa_etaria": "adultos jovens",
            "cor_pele": "morena clara",
            "etnia": "latina",
            "cabelo": "castanho",
            "roupa": "casual harmonizada",
            "expressao": "afeto e unidade",
            "cenario": "ambiente externo natural",
        }
    if persona_type == "desenho":
        return {
            "estilo_desenho": "cartoon",
            "personagem_base": "heroina simpatica",
            "paleta": "cores vivas e harmonicas",
            "expressao": "confiante e amigavel",
            "cenario": "fundo simples com profundidade",
        }
    if persona_type == "personalizado":
        return {
            "descricao_persona": "personagem autoral com identidade visual unica",
            "estilo_visual": "cinematico realista",
            "expressao": "natural e cativante",
            "cenario": "fundo neutro com luz suave",
        }
    return {
        "subtipo": "gato",
        "raca_ou_tipo": "domestico",
        "cor": "caramelo e branco",
        "expressao": "olhar atento e sereno",
        "cenario": "jardim ao por do sol",
    }


def normalize_persona_attributes(persona_type: str, attributes: dict | None) -> dict:
    persona_type = normalize_persona_type(persona_type)
    raw = attributes if isinstance(attributes, dict) else {}
    normalized: dict[str, str] = {}

    if persona_type in ("homem", "mulher", "crianca"):
        keys = [
            "idade_aparente",
            "cor_pele",
            "etnia",
            "cabelo",
            "roupa",
            "expressao",
            "cenario",
            "descricao_extra",
        ]
    elif persona_type == "familia":
        keys = [
            "composicao",
            "faixa_etaria",
            "cor_pele",
            "etnia",
            "cabelo",
            "roupa",
            "expressao",
            "cenario",
            "descricao_extra",
        ]
    elif persona_type == "desenho":
        keys = [
            "estilo_desenho",
            "estilo_desenho_custom",
            "personagem_base",
            "paleta",
            "expressao",
            "cenario",
            "descricao_extra",
        ]
    elif persona_type == "personalizado":
        keys = [
            "descricao_persona",
            "estilo_visual",
            "expressao",
            "cenario",
            "descricao_extra",
        ]
    else:
        keys = [
            "subtipo",
            "raca_ou_tipo",
            "cor",
            "expressao",
            "cenario",
            "outros_texto",
            "descricao_extra",
        ]

    for key in keys:
        value = _clean_text(raw.get(key, ""), max_len=_attribute_max_len(key))
        if value:
            normalized[key] = value

    if persona_type == "natureza":
        subtype = normalized.get("subtipo", "").lower()
        subtype_map = {
            "cat": "gato",
            "dog": "cachorro",
            "parrot": "papagaio",
            "other": "outros",
        }
        subtype = subtype_map.get(subtype, subtype)
        if subtype not in NATURE_SUBTYPES:
            subtype = "gato"
        normalized["subtipo"] = subtype
    elif persona_type == "desenho":
        style = normalized.get("estilo_desenho", "").lower()
        style_map = {
            "3d": "3d",
            "cartoon": "cartoon",
            "anime": "anime",
            "comic": "comic",
            "manga": "manga",
            "pixar": "pixar",
            "pixel": "pixel_art",
            "pixelart": "pixel_art",
            "pixel_art": "pixel_art",
            "aquarela": "aquarela",
            "watercolor": "aquarela",
            "outros": "outros",
            "outro": "outros",
        }
        style = style_map.get(style, style)
        if style not in DRAWING_STYLES:
            style = "cartoon"
        normalized["estilo_desenho"] = style
    elif persona_type == "personalizado":
        if not normalized.get("descricao_persona"):
            normalized["descricao_persona"] = "personagem autoral com identidade visual unica"

    if not normalized:
        normalized = default_persona_attributes(persona_type)

    reference_visual = _clean_text(raw.get("referencia_visual", ""), max_len=_attribute_max_len("referencia_visual"))
    if reference_visual:
        normalized["referencia_visual"] = reference_visual

    return normalized


def build_default_persona_name(persona_type: str) -> str:
    label = PERSONA_LABELS.get(normalize_persona_type(persona_type), "Persona")
    return f"Padrao {label}"


def _build_persona_prompt(persona_type: str, attributes: dict) -> str:
    persona_type = normalize_persona_type(persona_type)
    full_body = _should_use_full_body_composition(persona_type, attributes)
    framing_rule = (
        "full-body framing from head to toe, shoes visible, centered single subject"
        if full_body
        else "chest-up framing, centered single subject"
    )
    strict_adherence = (
        "Strict prompt adherence is mandatory: preserve every explicit attribute from the brief, including "
        "subject design, body texture/material, outfit pieces, colors, accessories, facial expression, and mood. "
        "Do not simplify details. Do not add random logos, text, capes, or superhero emblems unless explicitly requested."
    )

    if persona_type == "desenho":
        base_rules = (
            "Create one high-quality illustrated character reference image for video continuity. "
            "No text, no watermark, no logo, no collage. Keep one clear subject centered, "
            f"{framing_rule}. Maintain consistent facial traits, body proportions, and outfit details."
        )
    elif persona_type == "personalizado":
        base_rules = (
            "Create one high-quality character reference portrait for video continuity. "
            "No text, no watermark, no logo, no collage. Keep one clear subject centered, "
            f"{framing_rule}. Keep strong identity consistency and coherent style."
        )
    else:
        base_rules = (
            "Create one ultra-realistic portrait photo for a video reference persona. "
            "The image must look like a high-quality real camera photo, sharp focus, natural skin textures, "
            "balanced cinematic lighting, neutral background depth, no text, no watermark, no logo, no collage. "
            f"Keep only one clear main subject, centered, {framing_rule}, facing camera with slight natural pose."
        )

    handled_extra_as_primary = False

    if persona_type == "homem":
        details = (
            f"Subject: adult man. Age appearance: {attributes.get('idade_aparente', 'adulto jovem')}. "
            f"Skin tone: {attributes.get('cor_pele', 'morena')}. "
            f"Ethnicity: {attributes.get('etnia', 'latino')}. Hair: {attributes.get('cabelo', 'castanho curto')}. "
            f"Clothing: {attributes.get('roupa', 'casual neutra')}. Expression: {attributes.get('expressao', 'calma e confiante')}. "
            f"Environment mood: {attributes.get('cenario', 'ambiente externo natural')}."
        )
    elif persona_type == "mulher":
        details = (
            f"Subject: adult woman. Age appearance: {attributes.get('idade_aparente', 'adulta jovem')}. "
            f"Skin tone: {attributes.get('cor_pele', 'morena clara')}. "
            f"Ethnicity: {attributes.get('etnia', 'latina')}. Hair: {attributes.get('cabelo', 'castanho medio')}. "
            f"Clothing: {attributes.get('roupa', 'casual neutra')}. Expression: {attributes.get('expressao', 'calma e acolhedora')}. "
            f"Environment mood: {attributes.get('cenario', 'ambiente externo natural')}."
        )
    elif persona_type == "crianca":
        details = (
            f"Subject: child. Age appearance: {attributes.get('idade_aparente', '8 a 10 anos')}. "
            f"Skin tone: {attributes.get('cor_pele', 'morena clara')}. "
            f"Ethnicity: {attributes.get('etnia', 'latina')}. Hair: {attributes.get('cabelo', 'castanho')}. "
            f"Clothing: {attributes.get('roupa', 'casual confortavel')}. Expression: {attributes.get('expressao', 'alegre e curiosa')}. "
            f"Environment mood: {attributes.get('cenario', 'parque ao ar livre')}."
        )
    elif persona_type == "familia":
        details = (
            f"Subject: family group in one frame. Composition: {attributes.get('composicao', 'casal com uma crianca')}. "
            f"Age range: {attributes.get('faixa_etaria', 'adultos jovens')}. Skin tone: {attributes.get('cor_pele', 'morena clara')}. "
            f"Ethnicity: {attributes.get('etnia', 'latina')}. Hair: {attributes.get('cabelo', 'castanho')}. "
            f"Clothing style: {attributes.get('roupa', 'casual harmonizada')}. Expression: {attributes.get('expressao', 'afeto e unidade')}. "
            f"Environment mood: {attributes.get('cenario', 'ambiente externo natural')}."
        )
    elif persona_type == "natureza":
        subtype = attributes.get("subtipo", "gato")
        if subtype == "outros":
            nature_subject = attributes.get("outros_texto", "animal de estimacao exotico")
        else:
            nature_subject = subtype
        details = (
            f"Subject: {nature_subject}. Type or breed: {attributes.get('raca_ou_tipo', 'domestico')}. "
            f"Color pattern: {attributes.get('cor', 'caramelo e branco')}. Expression: {attributes.get('expressao', 'serena e atenta')}. "
            f"Environment mood: {attributes.get('cenario', 'jardim ao por do sol')}."
        )
    elif persona_type == "desenho":
        drawing_style = attributes.get("estilo_desenho", "cartoon")
        if drawing_style == "outros":
            drawing_style = attributes.get("estilo_desenho_custom", "estilo autoral")
        style_guidance = _DRAWING_STYLE_GUIDANCE.get(
            drawing_style,
            "Render style: polished illustration with strong material and lighting definition.",
        )
        extra_brief = attributes.get("descricao_extra", "")
        if extra_brief:
            handled_extra_as_primary = True
            details = (
                f"Primary character brief (mandatory): {extra_brief}. "
                f"Drawing style: {drawing_style}. {style_guidance} "
                "Interpret the brief literally and preserve all requested elements without omission."
            )
        else:
            details = (
                f"Subject: illustrated character. Drawing style: {drawing_style}. {style_guidance} "
                f"Character concept: {attributes.get('personagem_base', 'heroina simpatica')}. "
                f"Color palette: {attributes.get('paleta', 'cores vivas e harmonicas')}. "
                f"Expression: {attributes.get('expressao', 'confiante e amigavel')}. "
                f"Environment mood: {attributes.get('cenario', 'fundo simples com profundidade')}."
            )
    else:
        details = (
            f"Subject concept: {attributes.get('descricao_persona', 'personagem autoral com identidade visual unica')}. "
            f"Visual style: {attributes.get('estilo_visual', 'cinematico realista')}. "
            f"Expression: {attributes.get('expressao', 'natural e cativante')}. "
            f"Environment mood: {attributes.get('cenario', 'fundo neutro com luz suave')}."
        )

    extra = attributes.get("descricao_extra", "")
    if extra and not handled_extra_as_primary:
        details = f"{details} Extra details (mandatory, do not omit): {extra}."

    reference_visual = attributes.get("referencia_visual", "")
    if reference_visual:
        details = (
            f"{details} Reference image guidance: {reference_visual}. "
            "Match facial identity traits, hairstyle, skin tone, outfit details, accessories, and color palette "
            "as closely as possible to the reference. Keep framing and lighting similar when possible. "
            "Do not include logos, text overlays, or exact copyrighted character replicas."
        )

    return f"{base_rules} {details} {strict_adherence}"


def _persona_storage_dir(user_id: int, persona_type: str) -> Path:
    target = Path(settings.media_dir) / "personas" / str(user_id) / normalize_persona_type(persona_type)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _extract_chat_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text" and item.get("text"):
                chunks.append(str(item.get("text")))
            continue

        text = getattr(item, "text", "")
        if text:
            chunks.append(str(text))

    return " ".join(chunks)


def _extract_json_object(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        return {}

    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}

    return {}


def _build_reference_guidance_from_json(data: dict) -> str:
    if not isinstance(data, dict):
        return ""

    fields = [
        ("identity_signature", "identity"),
        ("face_shape", "face shape"),
        ("eye_details", "eyes"),
        ("nose_mouth_details", "nose and mouth"),
        ("hairstyle", "hair"),
        ("skin_tone", "skin tone"),
        ("body_proportions", "body proportions"),
        ("outfit", "outfit"),
        ("accessories", "accessories"),
        ("color_palette", "color palette"),
        ("pose_and_framing", "pose and framing"),
        ("lighting", "lighting"),
        ("style_family", "style"),
        ("keep_unchanged", "must preserve"),
    ]

    parts: list[str] = []
    for key, label in fields:
        value = _clean_text(data.get(key, ""), max_len=220)
        if value:
            parts.append(f"{label}: {value}")

    return _clean_text(" | ".join(parts), max_len=1400)


async def _describe_reference_image(reference_image_path: str) -> str:
    path = Path(str(reference_image_path or "")).expanduser()
    if not path.exists():
        return ""

    try:
        raw = path.read_bytes()
    except Exception as exc:
        logger.warning("Could not read persona reference image %s: %s", path, exc)
        return ""

    if not raw:
        return ""

    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    analysis_models = ("gpt-4.1-mini", "gpt-4o-mini")

    for model_name in analysis_models:
        try:
            analysis = await client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                max_tokens=520,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior visual identity analyst for character consistency. "
                            "Return only objective visual descriptors."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Analyze this reference image and return ONLY a compact JSON object with keys: "
                                    "identity_signature, face_shape, eye_details, nose_mouth_details, hairstyle, skin_tone, "
                                    "body_proportions, outfit, accessories, color_palette, pose_and_framing, lighting, "
                                    "style_family, keep_unchanged. "
                                    "Be concrete and specific about details that help replicate the same character look. "
                                    "Do not include brand names or text from the image."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": data_url,
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
            )
        except Exception as exc:
            logger.info("Reference analysis model %s unavailable or failed for %s: %s", model_name, path, exc)
            continue

        choices = getattr(analysis, "choices", None) or []
        if not choices:
            continue

        message = getattr(choices[0], "message", None)
        if not message:
            continue

        text = _extract_chat_text(getattr(message, "content", ""))
        if not text:
            continue

        parsed = _extract_json_object(text)
        guidance = _build_reference_guidance_from_json(parsed)
        if guidance:
            return guidance

        fallback = _clean_text(text, max_len=1400)
        if fallback:
            return fallback

    logger.warning("Reference image analysis failed for %s in all fallback models", path)
    return ""


async def _generate_with_reference_image_edit(
    client: openai.AsyncOpenAI,
    reference_image_path: str,
    prompt: str,
    image_size: str,
):
    reference_path = Path(str(reference_image_path or "")).expanduser()
    if not reference_path.exists():
        raise RuntimeError("Reference image file not found for edit mode")

    with reference_path.open("rb") as image_file:
        return await client.images.edit(
            model=(settings.persona_image_openai_model or "gpt-image-1"),
            image=image_file,
            prompt=prompt[:3800],
            size=image_size,
        )


def _google_aspect_ratio_from_size(image_size: str) -> str:
    if image_size == "1024x1536":
        return "9:16"
    if image_size == "1536x1024":
        return "16:9"
    return "1:1"


def _extract_google_image_payload(response: object) -> tuple[bytes, str]:
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

        data = getattr(inline_data, "data", None)
        mime_type = str(getattr(inline_data, "mime_type", "") or "").strip().lower()
        if not data:
            continue

        try:
            if isinstance(data, str):
                raw = base64.b64decode(data)
            else:
                raw = bytes(data)
        except Exception:
            continue

        if raw:
            return raw, mime_type

    return b"", ""


def _generate_with_google_image(prompt: str, image_size: str) -> tuple[bytes, str]:
    if genai is None or genai_types is None:
        raise RuntimeError("google-genai indisponivel")
    if not (settings.google_ai_api_key or "").strip():
        raise RuntimeError("Google AI API key nao configurada")

    client = genai.Client(api_key=settings.google_ai_api_key)
    response = client.models.generate_content(
        model=(settings.persona_image_google_model or "gemini-2.5-flash-image"),
        contents=[prompt],
        config=genai_types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=genai_types.ImageConfig(
                aspect_ratio=_google_aspect_ratio_from_size(image_size),
            ),
        ),
    )

    raw, mime_type = _extract_google_image_payload(response)
    if not raw:
        raise RuntimeError("Gemini image response without inline image")
    return raw, mime_type


def _sanitize_prompt_text(prompt_text: str, max_len: int = 3800) -> str:
    text = str(prompt_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


async def _generate_persona_image_with_prompt(
    user_id: int,
    normalized_type: str,
    normalized_attrs: dict,
    prompt: str,
) -> dict:
    has_openai_key = bool((settings.openai_api_key or "").strip())
    has_google_key = bool((settings.google_ai_api_key or "").strip())

    if not has_openai_key and not has_google_key:
        raise RuntimeError("Nenhuma chave de IA configurada para gerar imagem de persona")

    output_dir = _persona_storage_dir(user_id, normalized_type)
    output_stem = output_dir / uuid.uuid4().hex
    output_path = output_stem.with_suffix(".png")
    size_attrs = normalized_attrs if normalized_attrs else {"descricao_extra": prompt}
    image_size = _pick_persona_image_size(normalized_type, size_attrs)

    try:
        image_response = None

        if has_openai_key:
            try:
                client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
                image_response = await client.images.generate(
                    model=(settings.persona_image_openai_model or "gpt-image-1"),
                    prompt=prompt[:3800],
                    size=image_size,
                )
            except Exception as exc:
                logger.info(
                    "OpenAI persona generation failed for user=%s type=%s; trying Google fallback when available: %s",
                    user_id,
                    normalized_type,
                    exc,
                )
                image_response = None

        if image_response is None and has_google_key:
            google_bytes, google_mime = await asyncio.to_thread(
                _generate_with_google_image,
                prompt,
                image_size,
            )
            output_path = output_stem.with_suffix(_mime_to_extension(google_mime, fallback=".png"))
            output_path.write_bytes(google_bytes)
            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("Falha ao salvar imagem de persona (fallback Google)")

            return {
                "persona_type": normalized_type,
                "attributes": normalized_attrs,
                "prompt_text": prompt,
                "image_path": str(output_path),
            }

        if image_response is None:
            raise RuntimeError("Nao foi possivel gerar a imagem da persona agora")
    except Exception as exc:
        logger.error("Persona image generation failed for user=%s type=%s: %s", user_id, normalized_type, exc)
        raise RuntimeError("Nao foi possivel gerar a imagem da persona agora")

    data_items = getattr(image_response, "data", None) or []
    if not data_items:
        raise RuntimeError("A IA nao retornou imagem de persona")

    first_item = data_items[0]
    b64_data = getattr(first_item, "b64_json", None)
    if not b64_data and isinstance(first_item, dict):
        b64_data = first_item.get("b64_json")

    if b64_data:
        raw = base64.b64decode(b64_data)
        output_path.write_bytes(raw)
    else:
        image_url = getattr(first_item, "url", None)
        if not image_url and isinstance(first_item, dict):
            image_url = first_item.get("url")
        if not image_url:
            raise RuntimeError("Resposta da IA sem dados de imagem")

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client_http:
            response = await client_http.get(image_url)
            response.raise_for_status()
            output_path.write_bytes(response.content)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("Falha ao salvar imagem de persona")

    return {
        "persona_type": normalized_type,
        "attributes": normalized_attrs,
        "prompt_text": prompt,
        "image_path": str(output_path),
    }


async def generate_persona_image(
    user_id: int,
    persona_type: str,
    attributes: dict | None,
    reference_image_path: str = "",
) -> dict:
    normalized_type = normalize_persona_type(persona_type)
    normalized_attrs = normalize_persona_attributes(normalized_type, attributes)

    if reference_image_path:
        reference_visual = await _describe_reference_image(reference_image_path)
        if reference_visual:
            normalized_attrs["referencia_visual"] = reference_visual

    prompt = _build_persona_prompt(normalized_type, normalized_attrs)

    if reference_image_path:
        has_openai_key = bool((settings.openai_api_key or "").strip())
        if has_openai_key:
            image_size = _pick_persona_image_size(normalized_type, normalized_attrs)
            output_dir = _persona_storage_dir(user_id, normalized_type)
            output_stem = output_dir / uuid.uuid4().hex
            output_path = output_stem.with_suffix(".png")

            try:
                client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
                image_response = await _generate_with_reference_image_edit(
                    client=client,
                    reference_image_path=reference_image_path,
                    prompt=prompt,
                    image_size=image_size,
                )

                data_items = getattr(image_response, "data", None) or []
                if data_items:
                    first_item = data_items[0]
                    b64_data = getattr(first_item, "b64_json", None)
                    if not b64_data and isinstance(first_item, dict):
                        b64_data = first_item.get("b64_json")

                    if b64_data:
                        output_path.write_bytes(base64.b64decode(b64_data))
                    else:
                        image_url = getattr(first_item, "url", None)
                        if not image_url and isinstance(first_item, dict):
                            image_url = first_item.get("url")
                        if image_url:
                            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client_http:
                                response = await client_http.get(image_url)
                                response.raise_for_status()
                                output_path.write_bytes(response.content)

                    if output_path.exists() and output_path.stat().st_size > 0:
                        return {
                            "persona_type": normalized_type,
                            "attributes": normalized_attrs,
                            "prompt_text": prompt,
                            "image_path": str(output_path),
                        }
            except Exception as exc:
                logger.warning(
                    "Reference-conditioned persona edit failed for user=%s type=%s, using standard generation: %s",
                    user_id,
                    normalized_type,
                    exc,
                )

    return await _generate_persona_image_with_prompt(
        user_id=user_id,
        normalized_type=normalized_type,
        normalized_attrs=normalized_attrs,
        prompt=prompt,
    )


async def generate_persona_image_from_prompt(
    user_id: int,
    persona_type: str,
    prompt_text: str,
    attributes: dict | None = None,
) -> dict:
    normalized_type = normalize_persona_type(persona_type)
    normalized_attrs = normalize_persona_attributes(normalized_type, attributes)
    prompt = _sanitize_prompt_text(prompt_text, max_len=3800)
    if len(prompt) < 12:
        raise RuntimeError("Prompt muito curto para gerar persona")

    return await _generate_persona_image_with_prompt(
        user_id=user_id,
        normalized_type=normalized_type,
        normalized_attrs=normalized_attrs,
        prompt=prompt,
    )
