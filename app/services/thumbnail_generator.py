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


def generate_thumbnail(
    title: str,
    artist: str,
    mood: str = "",
    style_hint: str = "",
    output_path: str = "",
) -> str:
    """Generate a thumbnail image using Nano Banana with text overlay."""
    prompt = (
        f"Create a YouTube music video thumbnail. "
        f'The title "{title}" by "{artist}" should be prominently displayed in large, bold, stylized text. '
        f"Design it as a professional, eye-catching thumbnail with vibrant colors. "
        f"Include dramatic lighting and a cinematic feel. "
    )
    if mood:
        prompt += f"The mood/genre is: {mood}. "
    if style_hint:
        prompt += f"Visual style: {style_hint}. "
    prompt += "Make it look like a professional music video thumbnail that gets clicks on YouTube."

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
            image = part.as_image()
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

    draw.text((60, 600), title, fill="white", font=font_title, stroke_width=3, stroke_fill="black")
    draw.text((60, 665), artist, fill=(200, 200, 200), font=font_artist, stroke_width=2, stroke_fill="black")

    img.save(output_path, "JPEG", quality=95)
    os.remove(frame_path)

    logger.info(f"Thumbnail (frame-based) saved: {output_path}")
    return output_path
