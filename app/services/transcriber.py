"""
Transcriber — Uses OpenAI Whisper API to transcribe audio with word-level timestamps.
Provides accurate lyrics and timing for karaoke subtitles.
"""
import os
import logging
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_client = openai.OpenAI(api_key=settings.openai_api_key)

# Whisper API accepts max 25 MB
_MAX_FILE_SIZE = 25 * 1024 * 1024


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
        return {"text": "", "words": []}

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
    logger.info(f"Transcription complete: {len(words)} words, {len(text)} chars")
    return {"text": text, "words": words, "language": detected_language}
