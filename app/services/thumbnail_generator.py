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
    # Build a short, impactful display title (max ~4 words, all caps feel)
    display_title = title.upper() if len(title) <= 40 else title[:40].upper()

    prompt = (
        f'Create a VIRAL YouTube thumbnail image. '
        f'The thumbnail MUST have HUGE, BOLD, 3D text that says exactly: "{display_title}". '
        f'The text must be the DOMINANT element — enormous, centered, impossible to miss. '
        f'Text style: modern 3D extruded letters with strong shadows, neon glow effects, '
        f'gradient colors (gold, cyan, white, or fire orange). The letters should look like '
        f'they are popping out of the screen with depth and shine. '
        f'Background: dramatic, cinematic, dark with colorful light rays, bokeh, or energy effects '
        f'behind the text. NOT a landscape photo — the focus is 100% on the massive text. '
    )
    if artist:
        prompt += f'Include smaller text at the bottom: "{artist}". '
    if mood:
        # Use mood as thematic hint, not full lyrics
        mood_short = mood.split('\n')[0][:100]
        prompt += f"Theme/vibe: {mood_short}. "
    if style_hint:
        prompt += f"Color palette inspiration: {style_hint}. "
    prompt += (
        'Style references: top viral YouTube thumbnails with massive 3D text, '
        'MrBeast-style impact, bright contrasting colors on dark background. '
        'The text MUST be clearly readable and be the main visual element. '
        'Resolution: crisp, high quality, 16:9 aspect ratio. '
        'Do NOT make a landscape or scenery image. The TEXT is the star.'
    )

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

    draw.text((60, 600), title, fill="white", font=font_title, stroke_width=3, stroke_fill="black")
    draw.text((60, 665), artist, fill=(200, 200, 200), font=font_artist, stroke_width=2, stroke_fill="black")

    img.save(output_path, "JPEG", quality=95)
    os.remove(frame_path)

    logger.info(f"Thumbnail (frame-based) saved: {output_path}")
    return output_path
