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


def transcribe_audio(audio_path: str, language: str = "pt") -> dict:
    """Transcribe audio file using OpenAI Whisper API.

    Returns: {"text": str, "words": [{"word": str, "start": float, "end": float}, ...]}
    """
    file_size = os.path.getsize(audio_path)
    if file_size > _MAX_FILE_SIZE:
        logger.warning(f"Audio file too large for Whisper ({file_size / 1024 / 1024:.1f} MB > 25 MB), skipping")
        return {"text": "", "words": []}

    logger.info(f"Transcribing audio: {audio_path}")

    with open(audio_path, "rb") as f:
        response = _client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = []
    if hasattr(response, "words") and response.words:
        for w in response.words:
            words.append({
                "word": w.word.strip(),
                "start": w.start,
                "end": w.end,
            })

    text = response.text if hasattr(response, "text") else ""
    logger.info(f"Transcription complete: {len(words)} words, {len(text)} chars")

    return {"text": text, "words": words}
