"""
Persona image generation service.
Creates realistic persona portraits using OpenAI gpt-image-1.
"""

import base64
import logging
import uuid
from pathlib import Path

import httpx
import openai

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
        value = _clean_text(raw.get(key, ""))
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

    return normalized


def build_default_persona_name(persona_type: str) -> str:
    label = PERSONA_LABELS.get(normalize_persona_type(persona_type), "Persona")
    return f"Padrao {label}"


def _build_persona_prompt(persona_type: str, attributes: dict) -> str:
    persona_type = normalize_persona_type(persona_type)

    if persona_type == "desenho":
        base_rules = (
            "Create one high-quality illustrated character reference image for video continuity. "
            "No text, no watermark, no logo, no collage. Keep one clear subject centered, chest-up framing, "
            "clean silhouette and consistent facial traits."
        )
    elif persona_type == "personalizado":
        base_rules = (
            "Create one high-quality character reference portrait for video continuity. "
            "No text, no watermark, no logo, no collage. Keep one clear subject centered, chest-up framing, "
            "strong identity consistency and coherent style."
        )
    else:
        base_rules = (
            "Create one ultra-realistic portrait photo for a video reference persona. "
            "The image must look like a high-quality real camera photo, sharp focus, natural skin textures, "
            "balanced cinematic lighting, neutral background depth, no text, no watermark, no logo, no collage. "
            "Keep only one clear main subject, centered, from chest-up framing, facing camera with slight natural pose."
        )

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
        details = (
            f"Subject: illustrated character. Drawing style: {drawing_style}. "
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
    if extra:
        details = f"{details} Extra details: {extra}."

    return f"{base_rules} {details}"


def _persona_storage_dir(user_id: int, persona_type: str) -> Path:
    target = Path(settings.media_dir) / "personas" / str(user_id) / normalize_persona_type(persona_type)
    target.mkdir(parents=True, exist_ok=True)
    return target


async def generate_persona_image(user_id: int, persona_type: str, attributes: dict | None) -> dict:
    normalized_type = normalize_persona_type(persona_type)
    normalized_attrs = normalize_persona_attributes(normalized_type, attributes)
    prompt = _build_persona_prompt(normalized_type, normalized_attrs)

    if not (settings.openai_api_key or "").strip():
        raise RuntimeError("OpenAI API key nao configurada para gerar imagem de persona")

    output_dir = _persona_storage_dir(user_id, normalized_type)
    output_path = output_dir / f"{uuid.uuid4().hex}.png"

    try:
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        image_response = await client.images.generate(
            model="gpt-image-1",
            prompt=prompt[:3800],
            size="1024x1024",
        )
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
