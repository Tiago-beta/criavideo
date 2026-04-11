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


async def analyze_lyrics_for_scenes(lyrics_text: str, lyrics_words: list, duration: float) -> list[dict]:
    """Use GPT-4o-mini to split lyrics into scenes with visual descriptions."""
    # Scale scene count based on duration — cap at 40 unique scenes to limit API cost
    if duration <= 300:
        scene_min, scene_max = 15, 20
    elif duration <= 900:
        scene_min, scene_max = 20, 30
    else:
        scene_min, scene_max = 30, 40

    prompt = f"""You are a music video director. Given these song lyrics and total duration ({duration:.1f} seconds),
split the song into {scene_min}-{scene_max} visual scenes for a music video. Each scene should last between 8-15 seconds.
{"NOTE: The video is very long. Focus on creating diverse, visually distinct scenes. They will be cycled/repeated throughout the video, so variety is key." if duration > 300 else ""}

IMPORTANT GENRE RULES:
- If the lyrics are gospel, worship, christian, or spiritual in theme: use ONLY uplifting imagery. Prefer beautiful nature scenes (mountains, rivers, sunsets, starry skies, green fields, oceans, waterfalls, forests with sunlight), well-known Biblical scenes (shepherd with sheep, peaceful gardens, parting of waters, dove of peace, bread and wine, olive trees), and joyful people (diverse people with hands raised in worship, families, communities together, people praying peacefully). NEVER use dark, horror, scary, or violent imagery for gospel/worship songs. Keep the mood warm, radiant, hopeful, and divine.

For each scene, provide:
- scene_index: sequential number starting from 0
- start_time: approximate start in seconds
- end_time: approximate end in seconds
- lyrics_segment: the lyrics for this section
- visual_prompt: a detailed description for generating a background image (describe mood, colors, setting, objects — NO text/words in the image)
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
    return scenes


def generate_scene_image(prompt: str, aspect_ratio: str = "16:9", output_path: str = "") -> str:
    """Generate a single scene image using Nano Banana. Synchronous (runs in thread)."""
    # Detect gospel/worship theme to adjust style
    prompt_lower = prompt.lower()
    is_gospel = any(w in prompt_lower for w in [
        "worship", "gospel", "spiritual", "biblical", "divine", "prayer",
        "church", "faith", "god", "jesus", "holy", "praise", "shepherd",
        "psalm", "hymn", "adoração", "louvor", "evangel",
    ])
    if is_gospel:
        style_prefix = (
            "Beautiful, warm, radiant, uplifting, photorealistic. "
            "Golden hour lighting, soft divine glow, peaceful and hopeful atmosphere. "
            "Nature-inspired or Biblical scene. No dark or horror elements. "
            "No text or words in the image. "
        )
    else:
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


# ── Image Bank: search & save ──

async def search_image_bank(user_id: int, tags: list[str], aspect_ratio: str,
                            style: str, exclude_ids: set[int] | None = None) -> dict | None:
    """Search the image bank for a reusable image matching the given tags.
    Requires at least 3 tags in common. Prefers same style, then most tags matched.
    exclude_ids prevents reusing the same bank image twice in one video."""
    if not tags or len(tags) < 3:
        return None

    async with async_session() as db:
        tag_array = "{" + ",".join(tags) + "}"

        # Build exclusion clause
        exclude_clause = ""
        params = {
            "uid": user_id,
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
            WHERE user_id = :uid
              AND aspect_ratio = :ar
              AND tags && :tag_arr::text[]
              {exclude_clause}
            ORDER BY
                CASE WHEN style = :style THEN 0 ELSE 1 END,
                match_count DESC
            LIMIT 1
        """)
        result = await db.execute(query, {
            "uid": user_id,
            "ar": aspect_ratio,
            "tag_arr": tag_array,
            "style": style or "",
        })
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
    scenes = await analyze_lyrics_for_scenes(lyrics_text, lyrics_words, duration)

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
