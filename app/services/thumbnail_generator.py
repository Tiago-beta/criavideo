"""
Thumbnail Generator — Uses GPT Image as default and
Google Nano Banana as fallback for YouTube/social thumbnails (1280x720).
"""
import base64
import io
import httpx
import mimetypes
import os
import logging
import re
import openai
from google import genai
from google.genai import types
from PIL import Image
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    google_client = genai.Client(api_key=settings.google_ai_api_key) if (settings.google_ai_api_key or "").strip() else None
except Exception as exc:
    logger.warning("Google image client init failed: %s", exc)
    google_client = None


def _normalize_provider_preference(provider_preference: str) -> str:
    value = str(provider_preference or "").strip().lower()
    if value in {"", "auto", "openai", "gpt", "gpt-image", "gpt-image-1"}:
        return "openai"
    if value in {"google", "gemini", "nano", "nano-banana", "banana"}:
        return "google"
    return "openai"


def _build_thumbnail_hook(title: str, mood: str = "") -> str:
    """Create a short, high-impact thumbnail text (2-4 words)."""
    import re

    source = f"{title or ''} {mood or ''}".strip()
    cleaned = re.sub(r"[^\w\s]", " ", source, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "FE PARA VENCER"

    stopwords = {
        "de", "da", "do", "das", "dos", "e", "em", "na", "no", "nas", "nos",
        "com", "por", "para", "um", "uma", "o", "a", "os", "as", "que",
    }
    words = [w for w in cleaned.split() if len(w) > 1]
    filtered = [w for w in words if w.lower() not in stopwords]
    base = filtered if filtered else words
    hook = " ".join(base[:4]).upper().strip()
    return hook[:32].strip() or "FE PARA VENCER"


def _extract_openai_image_bytes(image_response: object) -> bytes:
    data_items = getattr(image_response, "data", None) or []
    if not data_items:
        return b""

    first_item = data_items[0]
    b64_data = getattr(first_item, "b64_json", None)
    if not b64_data and isinstance(first_item, dict):
        b64_data = first_item.get("b64_json")
    if b64_data:
        try:
            return base64.b64decode(b64_data)
        except Exception:
            return b""

    image_url = getattr(first_item, "url", None)
    if not image_url and isinstance(first_item, dict):
        image_url = first_item.get("url")
    if not image_url:
        return b""

    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client_http:
            response = client_http.get(str(image_url))
            response.raise_for_status()
            return response.content or b""
    except Exception:
        return b""


def _save_thumbnail_bytes(image_bytes: bytes, output_path: str) -> str:
    if not image_bytes:
        raise RuntimeError("Imagem vazia recebida do modelo")

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image = image.resize((1280, 720), Image.LANCZOS)

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    image.save(output_path, "JPEG", quality=95)
    return output_path


def _extract_inline_image_bytes(response: object) -> bytes:
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
        if not data:
            continue

        try:
            return base64.b64decode(data) if isinstance(data, str) else bytes(data)
        except Exception:
            continue

    return b""


def _build_thumbnail_prompt(
    title_text: str,
    description_text: str,
    hook_text: str,
    mood: str,
    style_hint: str,
    strategy_prompt: str,
    has_reference_image: bool,
) -> str:
    mood_short = re.sub(r"\s+", " ", str(mood or "")).strip()
    style_short = re.sub(r"\s+", " ", str(style_hint or "")).strip()
    strategy_short = str(strategy_prompt or "").strip()
    audience_hint = "Publico brasileiro do YouTube interessado nesse tema."
    emotion_hint = mood_short[:120] if mood_short else "curiosidade, impacto e vontade de clicar"
    central_element_hint = "pessoa ou elemento principal ligado ao tema"

    base_prompt = f"""Voce e um diretor de arte senior especialista em thumbnails de alto CTR para YouTube.

Crie uma thumbnail profissional em 16:9 (1280x720), altamente clicavel e com leitura instantanea em menos de 1 segundo.

Tema do video: {title_text}
Publico-alvo: {audience_hint}
Emocao principal: {emotion_hint}
Elemento central: {central_element_hint}

TEXTO PRINCIPAL OBRIGATORIO (copiar exatamente, sem trocar letras):
\"{hook_text}\"

A formula campea que deve ser seguida:
1) Uma ideia principal apenas (nada poluido)
2) Um rosto/personagem com emocao forte e clara
3) Texto grande de 2 a 5 palavras, muito legivel no celular
4) Contraste forte entre fundo e texto
5) Elemento de curiosidade visual (seta, circulo, sombra, olhar, objeto misterioso)
6) Promessa verdadeira e fiel ao conteudo

REGRAS OBRIGATORIAS:
- todo texto na imagem em portugues brasileiro
- NUNCA inventar texto diferente do texto principal obrigatorio
- NUNCA usar frase longa
- texto com fonte bold, alto contraste e destaque visual
- sem logos, sem marcas d'agua, sem UI de app
- sem poluicao visual

CONTEXTO DO VIDEO:
Titulo: {title_text}
Descricao resumida: {description_text}
Tom/estilo visual: {style_short or 'cinematico, moderno e emocional'}
"""

    if strategy_short:
        base_prompt += f"""

BRIEF DE ESTRATEGIA (usar como prioridade):
{strategy_short}
"""

    if has_reference_image:
        base_prompt += """

IMAGEM DE REFERENCIA ANEXADA:
- use a pessoa/produto da imagem como elemento principal
- manter identidade facial/visual reconhecivel
- integrar naturalmente ao novo fundo, sem recortes artificiais
- preservar aparencia profissional e realista
"""

    base_prompt += """

Gere a imagem final agora. Nao explique e nao descreva. Retorne somente a thumbnail.
"""

    return base_prompt


def generate_thumbnail(
    title: str,
    artist: str = "",
    description: str = "",
    mood: str = "",
    style_hint: str = "",
    strategy_prompt: str = "",
    reference_image_path: str = "",
    provider_preference: str = "openai",
    output_path: str = "",
) -> str:
    """Generate a thumbnail image using GPT Image and/or Nano Banana."""
    title_text = (title or "").strip() or "Sem titulo"
    hook_text = _build_thumbnail_hook(title_text, mood)

    description_parts: list[str] = []
    if description and description.strip():
        description_parts.append(description.strip())
    if artist:
        description_parts.append(f"Artista: {artist.strip()}")
    if mood:
        mood_short = mood.split("\n")[0][:180].strip()
        if mood_short:
            description_parts.append(f"Contexto emocional: {mood_short}")
    if style_hint:
        description_parts.append(f"Estilo visual sugerido: {style_hint.strip()}")

    description_text = "\n".join(description_parts).strip() or "Sem descricao informada."

    strategy_text = str(strategy_prompt or "").strip()
    if len(strategy_text) > 2200:
        strategy_text = strategy_text[:2200].rsplit(" ", 1)[0].strip() or strategy_text[:2200]

    ref_path = str(reference_image_path or "").strip()
    has_reference_image = bool(ref_path and os.path.exists(ref_path))

    prompt = _build_thumbnail_prompt(
        title_text=title_text,
        description_text=description_text,
        hook_text=hook_text,
        mood=mood,
        style_hint=style_hint,
        strategy_prompt=strategy_text,
        has_reference_image=has_reference_image,
    )

    normalized_provider = _normalize_provider_preference(provider_preference)
    has_openai_key = bool((settings.openai_api_key or "").strip())
    has_google_key = google_client is not None

    if normalized_provider == "google":
        provider_order = ["google"]
        if has_openai_key:
            provider_order.append("openai")
    else:
        provider_order = []
        if has_openai_key:
            provider_order.append("openai")
        if has_google_key:
            provider_order.append("google")

    if not provider_order:
        raise RuntimeError("Nenhum provedor de imagem configurado (OpenAI/Google)")

    errors: list[str] = []

    for provider in provider_order:
        try:
            if provider == "openai":
                client = openai.OpenAI(api_key=settings.openai_api_key)
                model_name = settings.persona_image_openai_model or "gpt-image-1"
                prompt_text = prompt[:3900]

                if has_reference_image:
                    with open(ref_path, "rb") as image_file:
                        response = client.images.edit(
                            model=model_name,
                            image=image_file,
                            prompt=prompt_text,
                            size="1536x1024",
                        )
                else:
                    response = client.images.generate(
                        model=model_name,
                        prompt=prompt_text,
                        size="1536x1024",
                    )

                image_bytes = _extract_openai_image_bytes(response)
                saved = _save_thumbnail_bytes(image_bytes, output_path)
                logger.info("Thumbnail saved with OpenAI image model: %s", saved)
                return saved

            if provider == "google":
                if google_client is None:
                    raise RuntimeError("Google image client indisponivel")

                contents_payload: list = []
                if has_reference_image:
                    mime_type = mimetypes.guess_type(ref_path)[0] or "image/jpeg"
                    with open(ref_path, "rb") as ref_file:
                        ref_bytes = ref_file.read()
                    if not ref_bytes:
                        raise RuntimeError("Reference image is empty")
                    contents_payload.append(types.Part.from_bytes(data=ref_bytes, mime_type=mime_type))
                contents_payload.append(prompt)

                response = google_client.models.generate_content(
                    model=(settings.persona_image_google_model or "gemini-2.5-flash-image"),
                    contents=contents_payload,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(
                            aspect_ratio="16:9",
                        ),
                    )
                )

                image_bytes = _extract_inline_image_bytes(response)
                saved = _save_thumbnail_bytes(image_bytes, output_path)
                logger.info("Thumbnail saved with Google image model: %s", saved)
                return saved
        except Exception as exc:
            logger.warning("Thumbnail provider %s failed: %s", provider, exc)
            errors.append(f"{provider}: {exc}")

    raise RuntimeError(" | ".join(errors) or "Falha ao gerar thumbnail")


def generate_thumbnail_from_frame(
    video_path: str,
    title: str,
    artist: str,
    output_path: str,
    timestamp: float = 5.0,
) -> str:
    """Fallback: extract a frame from the video and overlay text using Pillow."""
    import subprocess
    from PIL import ImageDraw, ImageFont

    # Extract frame with ffmpeg
    frame_path = output_path.replace(".jpg", "_frame.png")
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(timestamp),
        "-i", video_path, "-frames:v", "1",
        "-vf", "scale=1280:720",
        frame_path
    ], capture_output=True, timeout=30)

    if not os.path.exists(frame_path):
        raise RuntimeError("Failed to extract frame")

    img = Image.open(frame_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Semi-transparent gradient at bottom
    for y in range(500, 720):
        alpha = int(180 * (y - 500) / 220)
        draw.rectangle([(0, y), (1280, y)], fill=(0, 0, 0, alpha))

    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Draw text
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52)
        font_artist = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    except OSError:
        font_title = ImageFont.load_default()
        font_artist = ImageFont.load_default()

    hook_text = _build_thumbnail_hook(title)
    draw.text((60, 600), hook_text, fill="white", font=font_title, stroke_width=3, stroke_fill="black")
    if artist:
        draw.text((60, 665), artist, fill=(200, 200, 200), font=font_artist, stroke_width=2, stroke_fill="black")

    img.save(output_path, "JPEG", quality=95)
    os.remove(frame_path)

    logger.info(f"Thumbnail (frame-based) saved: {output_path}")
    return output_path
