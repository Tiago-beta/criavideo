"""
Thumbnail Generator — Uses Google Nano Banana to generate
YouTube/social media thumbnails (1280x720).
"""
import os
import logging
from pathlib import Path
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


def generate_thumbnail(
    title: str,
    artist: str = "",
    description: str = "",
    mood: str = "",
    style_hint: str = "",
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

    # Keep the prompt structure requested by the user while injecting real title/description values.
    prompt = f"""Voce e um especialista em design de thumbnails para YouTube com profundo conhecimento em psicologia visual, CTR e engenharia de prompts para IA.

Analise o titulo e descricao do video abaixo e gere DIRETAMENTE uma imagem de thumbnail profissional para YouTube com as seguintes especificacoes:

TITULO DO VIDEO: {title_text}
DESCRICAO DO VIDEO: {description_text}
TEXTO CURTO SUGERIDO PARA A CAPA: {hook_text}

REGRAS OBRIGATORIAS para a thumbnail gerada:
- TODO texto na imagem DEVE ser em PORTUGUES BRASILEIRO — NUNCA use ingles
- A thumbnail deve funcionar como um outdoor de 1 segundo
- Texto principal curto e memoravel: 2 a 4 palavras (maximo 32 caracteres), em CAIXA ALTA
- NUNCA usar frase longa no texto da imagem
- Formato: 16:9, proporcao widescreen, alta resolucao 4K
- Ponto focal unico e dominante, sem poluicao visual
- Alto contraste entre foreground e background para visibilidade mobile
- Hierarquia visual: elemento principal ocupa 60-70% do frame
- Iluminacao dramatica com volumetric lighting e profundidade
- Cores saturadas e vibrantes que se destacam na interface do YouTube
- Sensacao de urgencia ou curiosidade que forca o clique em 0.3 segundos
- Legibilidade perfeita em tela de 300px (celular)
- Estilo fotorrealista ou cinematografico de alto impacto
- NUNCA coloque nomes de marcas, IA ou plataformas na imagem

INSTRUCOES DE COMPOSICAO baseadas no conteudo analisado:
- Se o tema envolve dinheiro/resultado: inclua numero ou valor especifico em destaque dourado/amarelo
- Se e tutorial/como fazer: mostre o resultado final ou transformacao
- Se e entretenimento/viral: rosto humano com expressao exagerada em close-up
- Se e review/produto: produto centralizado com iluminacao dramatica + expressao de surpresa
- Se e educacional: elemento visual que representa a pergunta ou curiosidade do tema
- Se e lifestyle/vlog: atmosfera calorosa, cores quentes, energia positiva
- Se e musica/inspiracional: imagem emocional e impactante que transmita o sentimento do tema
- Se e gospel/religioso: natureza grandiosa + luz dramatica + atmosfera de fe, esperanca e superacao

TEXTO NA IMAGEM (se aplicavel):
Renderize o texto principal EM PORTUGUES com fonte bold, sans-serif, cor altamente contrastante ao fundo, tamanho que ocupe no minimo 25% da largura da imagem, com stroke/sombra leve para legibilidade. O texto deve ser chamativo e despertar curiosidade.
Use o TEXTO CURTO SUGERIDO como base e, se ajustar, altere no maximo 1 palavra para aumentar impacto.

Gere a thumbnail agora. Nao descreva, crie a imagem diretamente."""

    response = google_client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio="16:9",
            ),
        )
    )

    for part in response.parts:
        if part.inline_data is not None:
            # Convert raw bytes to PIL Image (part.as_image() returns SDK Image, not PIL)
            import io
            image = Image.open(io.BytesIO(part.inline_data.data))
            # Resize to standard YouTube thumbnail size
            image = image.resize((1280, 720), Image.LANCZOS)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
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
