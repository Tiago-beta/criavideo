"""
Thumbnail Generator — Uses Google Nano Banana to generate
YouTube/social media thumbnails (1280x720).
"""
import base64
import mimetypes
import os
import logging
import re
from google import genai
from google.genai import types
from PIL import Image
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

google_client = genai.Client(api_key=settings.google_ai_api_key)


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

    base_prompt = f"""Voce e um diretor de arte especialista em thumbnails de alta performance para YouTube.

Crie uma thumbnail profissional para YouTube em formato 16:9, resolucao 1280x720, estilo altamente clicavel, com composicao limpa e forte contraste.

Tema do video: {title_text}
Publico-alvo: {audience_hint}
Emocao principal: {emotion_hint}
Elemento central: {central_element_hint}
Texto grande na imagem: \"{hook_text}\"

A imagem deve ter:
- fundo simples e impactante
- rosto ou objeto principal em destaque
- iluminacao dramatica/profissional
- cores com alto contraste
- texto grande, legivel no celular
- espaco livre sem poluicao visual
- composicao que desperte curiosidade sem parecer falsa
- aparencia moderna, viral e profissional

REGRAS OBRIGATORIAS:
- TODO texto renderizado na imagem deve ser em portugues brasileiro
- use entre 2 e 5 palavras no texto principal
- evitar frases longas
- sem logos, marcas d'agua, interfaces ou textos pequenos
- sem clickbait mentiroso
- legibilidade maxima em tela de celular

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
    output_path: str = "",
) -> str:
    """Generate a thumbnail image using Nano Banana and a high-CTR prompt template."""
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
        model="gemini-2.5-flash-image",
        contents=contents_payload,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio="16:9",
            ),
        )
    )

    image_bytes = _extract_inline_image_bytes(response)
    if image_bytes:
        import io

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image = image.resize((1280, 720), Image.LANCZOS)
        output_dir = os.path.dirname(output_path) or "."
        os.makedirs(output_dir, exist_ok=True)
        image.save(output_path, "JPEG", quality=95)
        logger.info(f"Thumbnail saved: {output_path}")
        return output_path

    raise RuntimeError("Nano Banana did not return a thumbnail image")


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
