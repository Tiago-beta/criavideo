"""
Fish Audio Service — Voice cloning and TTS via Fish Audio API.
Instant voice cloning from audio samples, no consent recording needed.
"""
import logging
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

FISH_API_BASE = "https://api.fish.audio"


def _headers():
    return {
        "Authorization": f"Bearer {settings.fish_audio_api_key}",
    }


async def create_voice_clone(sample_path: str, name: str) -> str | None:
    """Create an instant voice clone from an audio sample.

    Returns the model ID (reference_id) or None on failure.
    """
    if not settings.fish_audio_api_key:
        logger.error("Fish Audio API key not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            with open(sample_path, "rb") as f:
                sample_data = f.read()

            ext = Path(sample_path).suffix.lstrip(".")
            mime_map = {
                "webm": "audio/webm", "wav": "audio/wav", "mp3": "audio/mpeg",
                "m4a": "audio/mp4", "ogg": "audio/ogg", "flac": "audio/flac",
            }
            mime = mime_map.get(ext, "audio/webm")

            resp = await client.post(
                f"{FISH_API_BASE}/model",
                headers=_headers(),
                data={
                    "type": "tts",
                    "title": name,
                    "train_mode": "fast",
                    "visibility": "private",
                    "enhance_audio_quality": "true",
                },
                files={
                    "voices": (f"sample.{ext}", sample_data, mime),
                },
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            model_id = data.get("_id")
            logger.info(f"Fish Audio voice clone created: {model_id} for '{name}'")
            return model_id
        else:
            logger.error(f"Fish Audio clone failed ({resp.status_code}): {resp.text}")
            return None

    except Exception as e:
        logger.error(f"Fish Audio clone error: {e}")
        return None


async def generate_tts(text: str, reference_id: str, output_path: str,
                       pause_level: str = "normal") -> bool:
    """Generate TTS audio using a cloned voice on Fish Audio (S2-Pro).

    Returns True on success.
    pause_level: controls prosody tag insertion for relaxed/deep modes.
    """
    if not settings.fish_audio_api_key:
        logger.error("Fish Audio API key not configured")
        return False

    # Pre-process text with S2-Pro prosody tags based on pause_level
    processed_text = _add_prosody_tags(text, pause_level)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{FISH_API_BASE}/v1/tts",
                headers={
                    **_headers(),
                    "Content-Type": "application/json",
                    "model": "s2-pro",
                },
                json={
                    "text": processed_text,
                    "reference_id": reference_id,
                    "format": "mp3",
                    "language": "pt",
                    "normalize": False,
                },
            )

        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(resp.content)
            logger.info(f"Fish Audio TTS saved: {output_path}")
            return True
        else:
            logger.error(f"Fish Audio TTS failed ({resp.status_code}): {resp.text[:500]}")
            return False

    except Exception as e:
        logger.error(f"Fish Audio TTS error: {e}")
        return False


def _add_prosody_tags(text: str, pause_level: str) -> str:
    """Insert S2-Pro [bracket] prosody tags into text based on pause_level.
    
    S2-Pro interprets [bracket] tags as natural language emotion/prosody cues.
    This replaces the OpenAI 'instructions' approach that doesn't work with Fish Audio.
    """
    import re

    if pause_level == "normal":
        return text

    if pause_level == "relaxed":
        # Add calm, expressive narration cues
        # Replace ellipsis with pause + descending tone cue
        text = re.sub(
            r'\.{6,}',
            ' [long pause] [soft tone] ',
            text,
        )
        text = re.sub(
            r'\.{3,5}',
            ' [pause] [soft tone] ',
            text,
        )
        text = text.replace('\u2026', ' [pause] [soft tone] ')
        return text

    if pause_level == "deep":
        # Hypnosis mode: deep, slow, descending tone, heavy pauses
        # Extended ellipsis (6+ dots) = very long pause
        text = re.sub(
            r'\.{6,}',
            ' [long pause] [whisper] [soft tone] ',
            text,
        )
        # Normal ellipsis = pause with descending tone
        text = re.sub(
            r'\.{3,5}',
            ' [pause] [soft tone] ',
            text,
        )
        text = text.replace('\u2026', ' [pause] [soft tone] ')

        # Key hypnosis words get emphasis tags
        hypno_words = (
            r'\b(relaxar|profundo|profunda|calma|mente|corpo|respira\w*|'
            r'soltar|solte|confort\w*|tranquil\w*|suave|feche os olhos|'
            r'deixe ir|permita-se|sono|dormir|paz|sereno|serenidade)\b'
        )
        text = re.sub(
            hypno_words,
            lambda m: f'[emphasis] {m.group(0)} [soft tone]',
            text,
            flags=re.IGNORECASE,
        )
        return text

    return text


async def generate_tts_long(text: str, reference_id: str, output_path: str,
                            pause_level: str = "normal") -> bool:
    """Generate TTS for long texts by chunking and concatenating."""
    if len(text) <= 4000:
        return await generate_tts(text, reference_id, output_path, pause_level=pause_level)

    import re
    import os
    import subprocess

    sentences = re.split(r'(?<=[.!?…])\s+', text)
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 > 3800 and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        chunks = [text]

    out_dir = Path(output_path).parent
    chunk_paths = []

    for i, chunk in enumerate(chunks):
        chunk_path = str(out_dir / f"fish_chunk_{i:03d}.mp3")
        ok = await generate_tts(chunk, reference_id, chunk_path, pause_level=pause_level)
        if not ok:
            logger.error(f"Fish Audio chunk {i} failed, falling back")
            for cp in chunk_paths:
                try:
                    os.remove(cp)
                except OSError:
                    pass
            return False
        chunk_paths.append(chunk_path)

    if len(chunk_paths) == 1:
        os.replace(chunk_paths[0], output_path)
        return True

    # Concatenate with FFmpeg
    import tempfile
    list_content = "\n".join(f"file '{p}'" for p in chunk_paths)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=str(out_dir)) as lf:
        lf.write(list_content)
        list_path = lf.name

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path],
            capture_output=True, timeout=60,
        )
    finally:
        os.unlink(list_path)
        for cp in chunk_paths:
            try:
                os.remove(cp)
            except OSError:
                pass

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0
