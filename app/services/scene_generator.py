"""
Scene Generator — Uses Google Nano Banana (Gemini 3.1 Flash Image)
to generate background images for each scene of the music video.
"""
import os
import asyncio
import logging
from pathlib import Path
from google import genai
from google.genai import types
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

google_client = genai.Client(api_key=settings.google_ai_api_key)
openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)


async def analyze_lyrics_for_scenes(lyrics_text: str, lyrics_words: list, duration: float) -> list[dict]:
    """Use GPT-4o-mini to split lyrics into scenes with visual descriptions."""
    prompt = f"""You are a music video director. Given these song lyrics and total duration ({duration:.1f} seconds),
split the song into 15-20 visual scenes for a music video. Each scene should last between 8-15 seconds.

For each scene, provide:
- scene_index: sequential number starting from 0
- start_time: approximate start in seconds
- end_time: approximate end in seconds
- lyrics_segment: the lyrics for this section
- visual_prompt: a detailed description for generating a background image (describe mood, colors, setting, objects — NO text/words in the image)
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
    return scenes


def generate_scene_image(prompt: str, aspect_ratio: str = "16:9", output_path: str = "") -> str:
    """Generate a single scene image using Nano Banana. Synchronous (runs in thread)."""
    style_prefix = (
        "Cinematic, high quality, moody lighting, music video aesthetic. "
        "No text or words in the image. "
    )
    full_prompt = style_prefix + prompt

    response = google_client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[full_prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
            ),
        )
    )

    for part in response.parts:
        if part.inline_data is not None:
            image = part.as_image()
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            image.save(output_path)
            logger.info(f"Scene image saved: {output_path}")
            return output_path

    raise RuntimeError("Nano Banana did not return an image")


async def generate_all_scenes(
    project_id: int,
    lyrics_text: str,
    lyrics_words: list,
    duration: float,
    aspect_ratio: str = "16:9",
    style_hint: str = "",
) -> list[dict]:
    """Full pipeline: analyze lyrics → generate images for each scene."""
    media_dir = Path(settings.media_dir) / "images" / str(project_id)
    media_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Analyze lyrics into scenes
    scenes = await analyze_lyrics_for_scenes(lyrics_text, lyrics_words, duration)

    # Step 2: Generate images for each scene (in thread pool, sequential to avoid rate limits)
    loop = asyncio.get_event_loop()
    results = []

    for scene in scenes:
        idx = scene.get("scene_index", len(results))
        visual_prompt = scene.get("visual_prompt", "Abstract colorful background")
        if style_hint:
            visual_prompt = f"{style_hint}. {visual_prompt}"

        output_path = str(media_dir / f"scene_{idx:03d}.png")

        try:
            path = await loop.run_in_executor(
                None, generate_scene_image, visual_prompt, aspect_ratio, output_path
            )
            scene["image_path"] = path
        except Exception as e:
            logger.error(f"Failed to generate scene {idx}: {e}")
            scene["image_path"] = None

        results.append(scene)

    return results
