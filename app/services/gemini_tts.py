"""
Gemini TTS helper.

Provides short and long synthesis helpers using Gemini 3.1 Flash TTS Preview.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

from app.config import get_settings
from app.services.voice_catalog import build_gemini_ptbr_instructions

logger = logging.getLogger(__name__)
settings = get_settings()

_GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
_MAX_TEXT_CHARS = 4500
_gemini_client = (
    genai.Client(api_key=settings.google_ai_api_key)
    if genai is not None and (settings.google_ai_api_key or "").strip()
    else None
)


def _ensure_client() -> None:
    if _gemini_client is not None and genai_types is not None:
        return
    if not (settings.google_ai_api_key or "").strip():
        raise RuntimeError("GOOGLE_AI_API_KEY not configured")
    raise RuntimeError("google-genai is not available in this environment")


def _split_text_for_gemini(text: str, max_chars: int = _MAX_TEXT_CHARS) -> list[str]:
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


def _build_gemini_prompt(text: str, voice_name: str, tts_instructions: str = "") -> str:
    notes = str(tts_instructions or "").strip() or build_gemini_ptbr_instructions(voice_name)
    transcript = str(text or "").strip()
    return (
        "Synthesize speech audio for the transcript below.\n"
        "Speak only the transcript section in Brazilian Portuguese.\n"
        "Do not read the director notes, headings, labels, or metadata aloud.\n\n"
        "### DIRECTOR'S NOTES\n"
        f"{notes}\n\n"
        "### TRANSCRIPT\n"
        f"{transcript}"
    )


def _extract_audio_bytes(response) -> bytes:
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None) if inline_data is not None else None
            if data:
                return data
    return b""


def _write_wave_file(path: str, pcm_data: bytes, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> None:
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm_data)


def _synthesize_sync(text: str, voice_name: str, tts_instructions: str, output_wav_path: str) -> None:
    _ensure_client()
    prompt = _build_gemini_prompt(text, voice_name, tts_instructions)
    response = _gemini_client.models.generate_content(
        model=_GEMINI_TTS_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=voice_name,
                    )
                )
            ),
        ),
    )
    audio_bytes = _extract_audio_bytes(response)
    if not audio_bytes:
        raise RuntimeError("Gemini TTS returned no audio data")
    _write_wave_file(output_wav_path, audio_bytes)


def _convert_wav_to_mp3_sync(input_wav_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", input_wav_path,
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "1",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"Gemini TTS ffmpeg conversion failed: {result.stderr[-300:]}")


async def generate_tts(
    text: str,
    voice_name: str,
    output_path: str,
    tts_instructions: str = "",
) -> bool:
    normalized_text = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized_text:
        raise RuntimeError("Empty text for Gemini synthesis")

    voice = str(voice_name or "").strip()
    if not voice:
        raise RuntimeError("Missing Gemini voice name")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()

    with tempfile.TemporaryDirectory() as temp_dir:
        wav_path = os.path.join(temp_dir, "gemini_tts.wav")
        last_error = None
        for _ in range(2):
            try:
                await loop.run_in_executor(None, _synthesize_sync, normalized_text, voice, tts_instructions, wav_path)
                await loop.run_in_executor(None, _convert_wav_to_mp3_sync, wav_path, output_path)
                return os.path.exists(output_path) and os.path.getsize(output_path) > 0
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini TTS attempt failed for voice %s: %s", voice, exc)
        raise RuntimeError(str(last_error) if last_error else "Gemini TTS generation failed")


async def generate_tts_long(
    text: str,
    voice_name: str,
    output_path: str,
    tts_instructions: str = "",
) -> bool:
    chunks = _split_text_for_gemini(text)
    if not chunks:
        raise RuntimeError("Empty text for Gemini synthesis")

    if len(chunks) == 1:
        return await generate_tts(chunks[0], voice_name, output_path, tts_instructions=tts_instructions)

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    part_paths: list[str] = []
    tmp_list_file = None
    try:
        for idx, chunk in enumerate(chunks):
            part_path = str(out_dir / f"gemini_part_{idx:03d}.mp3")
            ok = await generate_tts(chunk, voice_name, part_path, tts_instructions=tts_instructions)
            if not ok:
                raise RuntimeError(f"Gemini chunk synthesis failed at chunk {idx}")
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
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=300),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Gemini concat failed: {proc.stderr[-300:]}")

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