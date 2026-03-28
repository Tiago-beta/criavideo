"""
Script & Audio Generator — Creates video scripts with AI and generates TTS narration via OpenAI.
"""
import logging
from pathlib import Path

import openai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_script(
    topic: str,
    tone: str = "informativo",
    duration_seconds: int = 60,
) -> dict:
    """Use GPT-4o-mini to generate a video narration script."""
    prompt = f"""Você é um roteirista profissional de vídeos para redes sociais.

Crie um roteiro de narração para um vídeo sobre o tema abaixo.

Tema: {topic}
Tom: {tone}
Duração aproximada: {duration_seconds} segundos de narração

Regras:
- Escreva APENAS o texto da narração (o que será falado no vídeo)
- Use linguagem natural e envolvente para o público brasileiro
- Não inclua indicações técnicas como [CENA], [CORTE], etc.
- O texto deve fluir naturalmente quando lido em voz alta
- Adapte o tamanho para caber em ~{duration_seconds} segundos (média 150 palavras por minuto)
- Comece com uma frase de impacto para prender atenção
- Termine com uma chamada para ação ou reflexão

Responda SOMENTE com o texto do roteiro, sem títulos ou formatação extra."""

    try:
        resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=2000,
        )
        script_text = resp.choices[0].message.content.strip()
        word_count = len(script_text.split())
        estimated_duration = round(word_count / 2.5)  # ~150 wpm = 2.5 wps

        return {
            "script": script_text,
            "word_count": word_count,
            "estimated_duration": estimated_duration,
        }
    except Exception as e:
        logger.error("Script generation failed: %s", e)
        raise


async def generate_tts_audio(
    text: str,
    voice: str = "onyx",
    project_id: int = 0,
) -> str:
    """Generate TTS audio using OpenAI and save to media directory. Returns file path."""
    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / "narration.mp3"

    try:
        response = await _openai.audio.speech.create(
            model="tts-1-hd",
            voice=voice,
            input=text,
            response_format="mp3",
        )
        response.stream_to_file(str(output_path))
        logger.info("TTS audio saved: %s", output_path)
        return str(output_path)
    except Exception as e:
        logger.error("TTS generation failed: %s", e)
        raise
