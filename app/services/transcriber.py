"""
Transcriber — Uses OpenAI Whisper API to transcribe audio with word-level timestamps.
Provides accurate lyrics and timing for karaoke subtitles.
"""
import os
import logging
from typing import Any
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_client = openai.OpenAI(api_key=settings.openai_api_key)

# Whisper API accepts max 25 MB
_MAX_FILE_SIZE = 25 * 1024 * 1024


def _response_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)

    attr = getattr(value, name, default)
    if attr is not None:
        return attr

    try:
        dumped = value.model_dump()
    except Exception:
        dumped = None
    if isinstance(dumped, dict):
        return dumped.get(name, default)
    return default


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _extract_whisper_segments(response: Any) -> list[dict[str, Any]]:
    raw_segments = _response_field(response, "segments", []) or []
    segments: list[dict[str, Any]] = []
    if not isinstance(raw_segments, list):
        return segments

    for item in raw_segments:
        text = str(_response_field(item, "text", "") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "text": text,
                "avg_logprob": _safe_float(_response_field(item, "avg_logprob")),
                "compression_ratio": _safe_float(_response_field(item, "compression_ratio")),
                "no_speech_prob": _safe_float(_response_field(item, "no_speech_prob")),
            }
        )
    return segments


def _whisper_detected_speech(
    transcript_text: str,
    words: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> bool:
    normalized_text = str(transcript_text or "").strip()
    if not normalized_text:
        return False

    if not segments:
        return len(words) >= 2 or len(normalized_text.split()) >= 3

    validated_segments = 0
    for segment in segments:
        segment_text = str(segment.get("text") or "").strip()
        if not segment_text:
            continue

        no_speech_prob = _safe_float(segment.get("no_speech_prob"))
        avg_logprob = _safe_float(segment.get("avg_logprob"))
        compression_ratio = _safe_float(segment.get("compression_ratio"))

        if no_speech_prob is not None and no_speech_prob >= 0.72:
            continue
        if avg_logprob is not None and avg_logprob <= -1.25 and (no_speech_prob is None or no_speech_prob >= 0.45):
            continue
        if compression_ratio is not None and compression_ratio >= 2.4 and len(segment_text.split()) < 3 and (no_speech_prob is None or no_speech_prob >= 0.45):
            continue

        validated_segments += 1

    return validated_segments > 0


def transcribe_audio(audio_path: str, language: str = "pt", prompt: str = "") -> dict:
    """Transcribe audio file using OpenAI Whisper API.

    Args:
        audio_path: Path to audio file.
        language: Language code (default: "pt").
        prompt: Optional text to guide Whisper (e.g. known lyrics). Improves accuracy.

    Returns: {"text": str, "words": [{"word": str, "start": float, "end": float}, ...], "language": str}
    """
    file_size = os.path.getsize(audio_path)
    if file_size > _MAX_FILE_SIZE:
        logger.warning(f"Audio file too large for Whisper ({file_size / 1024 / 1024:.1f} MB > 25 MB), skipping")
        return {"text": "", "words": [], "language": "", "speech_detected": False}

    logger.info(f"Transcribing audio: {audio_path} (prompt_len={len(prompt)})")

    # Truncate prompt to Whisper's 224-token limit (~800 chars is safe)
    whisper_prompt = prompt[:800].strip() if prompt else None

    normalized_language = str(language or "").strip()

    with open(audio_path, "rb") as f:
        kwargs = dict(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
        if normalized_language:
            kwargs["language"] = normalized_language
        if whisper_prompt:
            kwargs["prompt"] = whisper_prompt

        response = _client.audio.transcriptions.create(**kwargs)

    words = []
    if hasattr(response, "words") and response.words:
        for w in response.words:
            words.append({
                "word": w.word.strip(),
                "start": w.start,
                "end": w.end,
            })

    text = response.text if hasattr(response, "text") else ""
    detected_language = ""
    if hasattr(response, "language"):
        detected_language = str(response.language or "").strip().lower()
    if not detected_language and normalized_language:
        detected_language = normalized_language.lower()
    segments = _extract_whisper_segments(response)
    speech_detected = _whisper_detected_speech(text, words, segments)
    logger.info(f"Transcription complete: {len(words)} words, {len(text)} chars")
    return {
        "text": text,
        "words": words,
        "language": detected_language,
        "speech_detected": speech_detected,
    }
