"""
ElevenLabs TTS helper.

Provides short and long synthesis helpers with chunking + FFmpeg concat.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
_MAX_TEXT_CHARS = 2400


def _split_text_for_elevenlabs(text: str, max_chars: int = _MAX_TEXT_CHARS) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    parts = re.split(r"(?<=[.!?…])\s+", cleaned)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        if len(part) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(part):
                end = start + max_chars
                chunks.append(part[start:end].strip())
                start = end
            continue

        candidate = part if not current else f"{current} {part}"
        if len(candidate) > max_chars:
            chunks.append(current.strip())
            current = part
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())
    return chunks


def _voice_settings_from_instructions(tts_instructions: str = "") -> dict:
    text = str(tts_instructions or "").lower()
    stability = 0.45
    style = 0.2
    similarity_boost = 0.82

    if any(token in text for token in ("calma", "suave", "serena", "lento", "reflexivo")):
        stability = 0.6
        style = 0.1
    elif any(token in text for token in ("alegre", "energet", "dinam", "animad", "urgente")):
        stability = 0.32
        style = 0.45

    return {
        "stability": stability,
        "similarity_boost": similarity_boost,
        "style": style,
        "use_speaker_boost": True,
    }


async def generate_tts(
    text: str,
    voice_id: str,
    output_path: str,
    tts_instructions: str = "",
) -> bool:
    api_key = (getattr(settings, "elevenlabs_api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not configured")

    normalized_text = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized_text:
        raise RuntimeError("Empty text for ElevenLabs synthesis")

    voice = str(voice_id or "").strip()
    if not voice:
        raise RuntimeError("Missing ElevenLabs voice ID")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "text": normalized_text,
        "model_id": _ELEVENLABS_MODEL_ID,
        "voice_settings": _voice_settings_from_instructions(tts_instructions),
        "output_format": "mp3_44100_128",
    }

    url = f"{_ELEVENLABS_API_BASE}/text-to-speech/{voice}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"ElevenLabs TTS failed ({response.status_code}): {response.text[:240]}")
        Path(output_path).write_bytes(response.content)

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


async def generate_tts_long(
    text: str,
    voice_id: str,
    output_path: str,
    tts_instructions: str = "",
) -> bool:
    chunks = _split_text_for_elevenlabs(text)
    if not chunks:
        raise RuntimeError("Empty text for ElevenLabs synthesis")

    if len(chunks) == 1:
        return await generate_tts(chunks[0], voice_id, output_path, tts_instructions=tts_instructions)

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    part_paths: list[str] = []
    tmp_list_file = None
    try:
        for idx, chunk in enumerate(chunks):
            part_path = str(out_dir / f"eleven_part_{idx:03d}.mp3")
            ok = await generate_tts(chunk, voice_id, part_path, tts_instructions=tts_instructions)
            if not ok:
                raise RuntimeError(f"ElevenLabs chunk synthesis failed at chunk {idx}")
            part_paths.append(part_path)

        tmp_list_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        for part_path in part_paths:
            safe = part_path.replace("'", "'\\''")
            tmp_list_file.write(f"file '{safe}'\n")
        tmp_list_file.close()

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", tmp_list_file.name,
            "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "1",
            output_path,
        ]
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=300),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ElevenLabs concat failed: {proc.stderr[-300:]}")

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    finally:
        if tmp_list_file is not None:
            try:
                os.unlink(tmp_list_file.name)
            except OSError:
                pass
        for part_path in part_paths:
            try:
                if os.path.exists(part_path):
                    os.remove(part_path)
            except OSError:
                pass
