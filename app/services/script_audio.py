"""
Script & Audio Generator — Creates video scripts with AI and generates TTS narration via OpenAI.
"""
import os
import logging
import subprocess
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
    """Use GPT-4o to generate a viral video narration script."""
    word_target = int(duration_seconds * 2.5)

    # For long videos (>5 min), adapt the prompt for long-form content
    is_long = duration_seconds > 300
    if is_long:
        mins = duration_seconds // 60
        structure = f"""ESTRUTURA OBRIGATÓRIA PARA VÍDEO LONGO (~{mins} minutos):
1. GANCHO (primeiros 10 segundos): Uma abertura CHOCANTE e envolvente.
2. INTRODUÇÃO: Apresente o tema com contexto e promessa do que será abordado.
3. DESENVOLVIMENTO: Divida em {max(3, mins // 3)} blocos temáticos, cada um com sub-tópicos, exemplos reais, dados e histórias. Mantenha transições naturais entre blocos.
4. CLÍMAX: O momento mais impactante ou revelador do vídeo.
5. CONCLUSÃO: Resumo dos pontos-chave e chamada para ação poderosa.

IMPORTANTE: O texto PRECISA ter aproximadamente {word_target} palavras para preencher {mins} minutos de narração."""
    else:
        structure = """ESTRUTURA OBRIGATÓRIA:
1. GANCHO (primeiros 3 segundos): Uma frase CHOCANTE, reveladora ou provocativa que torne impossível parar de assistir. Use padrões como: "Você sabia que...", "Ninguém te conta isso sobre...", "O segredo que...", "A verdade sobre..."
2. DESENVOLVIMENTO: Revele informações de forma crescente, criando tensão e curiosidade. Cada frase deve fazer o espectador querer ouvir a próxima. Use dados reais, histórias ou exemplos concretos.
3. CLÍMAX: O momento de revelação ou insight principal — a informação mais valiosa ou emocionante.
4. FECHAMENTO: Chamada para ação poderosa ou frase de reflexão que fica na mente."""

    prompt = f"""Você é um roteirista VIRAL{"" if is_long else " de vídeos curtos"} que acumula milhões de views no YouTube{" Shorts, TikTok e Instagram Reels" if not is_long else ""}.
Seu estilo combina storytelling emocional, ganchos psicológicos e linguagem que prende desde o primeiro segundo.

TEMA: {topic}
TOM: {tone}
DURAÇÃO: ~{duration_seconds} segundos (~{word_target} palavras)

{structure}

REGRAS DE OURO:
- Escreva APENAS o texto falado (narração pura, sem indicações técnicas como [CENA])
- Use frases CURTAS e DIRETAS — como se estivesse conversando com um amigo
- Provoque EMOÇÃO: surpresa, curiosidade, urgência, empatia
- Use pausas retóricas com "..." para criar suspense natural
- Inclua pelo menos 1 dado surpreendente, fato real ou história concreta
- Linguagem brasileira natural, acessível, com energia e ritmo
- PROIBIDO: ser genérico, usar clichês vazios, soar como robô ou texto de blog

Responda SOMENTE com o texto do roteiro. Sem títulos, sem formatação."""

    # Scale max_tokens based on duration: ~1.3 tokens per word, with buffer
    max_tokens = min(max(2000, int(word_target * 1.5)), 16000)

    try:
        resp = await _openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Você é o melhor roteirista de vídeos virais do Brasil. Seus textos são magnéticos — quem ouve não consegue parar."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=max_tokens,
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
    tts_instructions: str = "",
) -> str:
    """Generate TTS audio using OpenAI and save to media directory. Returns file path.

    Supports both built-in voice names (str) and custom voice IDs.
    Uses gpt-4o-mini-tts when instructions are provided, otherwise tts-1-hd.
    Automatically chunks long texts (>4000 chars) and concatenates.
    """
    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / "narration.mp3"

    try:
        # For long texts, split into chunks and concatenate
        if len(text) > 4000:
            chunks = _split_text_for_tts(text, max_chars=3800)
            logger.info(f"Long text ({len(text)} chars) split into {len(chunks)} TTS chunks")
            chunk_paths = []
            for i, chunk in enumerate(chunks):
                chunk_path = audio_dir / f"narration_chunk_{i:03d}.mp3"
                await _generate_single_tts(chunk, voice, tts_instructions, str(chunk_path))
                chunk_paths.append(str(chunk_path))
            _concat_audio_files(chunk_paths, str(output_path))
            # Clean up chunks
            for cp in chunk_paths:
                try:
                    os.remove(cp)
                except OSError:
                    pass
        else:
            await _generate_single_tts(text, voice, tts_instructions, str(output_path))

        logger.info("TTS audio saved: %s", output_path)
        return str(output_path)
    except Exception as e:
        logger.error("TTS generation failed: %s", e)
        raise


def _split_text_for_tts(text: str, max_chars: int = 3800) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_chars."""
    import re
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]


async def _generate_single_tts(text: str, voice: str, tts_instructions: str, output_path: str):
    """Generate a single TTS audio file."""
    if tts_instructions:
        tts_kwargs = {
            "model": "gpt-4o-mini-tts",
            "voice": voice,
            "input": text,
            "instructions": tts_instructions,
            "response_format": "mp3",
        }
    else:
        tts_kwargs = {
            "model": "tts-1-hd",
            "voice": voice,
            "input": text,
            "response_format": "mp3",
        }
    response = await _openai.audio.speech.create(**tts_kwargs)
    response.stream_to_file(output_path)


def _concat_audio_files(paths: list[str], output_path: str):
    """Concatenate multiple audio files using FFmpeg concat demuxer."""
    import tempfile
    list_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    try:
        for p in paths:
            safe = p.replace("'", "'\\''")
            list_file.write(f"file '{safe}'\n")
        list_file.close()
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file.name,
            "-c", "copy", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-300:]}")
    finally:
        os.unlink(list_file.name)


def generate_background_music(
    output_path: str,
    duration: float,
    mood: str = "inspiracional",
) -> str:
    """Generate soft ambient background music using FFmpeg audio synthesis.

    Creates a gentle atmospheric pad with harmonically related tones
    that works as background under narration.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Musical chord tones (Hz) by mood
    mood_chords = {
        "inspiracional": [130.81, 164.81, 196.00, 261.63],   # C major
        "informativo":   [146.83, 185.00, 220.00, 293.66],   # D major
        "misterioso":    [110.00, 130.81, 164.81, 220.00],   # A minor
        "motivacional":  [146.83, 185.00, 220.00, 293.66],   # D major
        "urgente":       [130.81, 155.56, 196.00, 261.63],   # C minor
        "reflexivo":     [123.47, 146.83, 185.00, 246.94],   # B minor
        "dramatico":     [110.00, 130.81, 164.81, 220.00],   # A minor
    }

    freqs = mood_chords.get(mood.lower(), mood_chords["inspiracional"])
    dur = duration + 2  # extra margin for fades

    # Build layered sine tones with gentle volume modulation
    inputs = []
    labels = []
    for i, freq in enumerate(freqs):
        vol = max(0.5 - (i * 0.1), 0.2)
        # tremolo freq must be >= 0.1 Hz per FFmpeg spec
        trem = 0.1 + (i * 0.05)
        inputs.extend([
            "-f", "lavfi", "-i",
            f"sine=f={freq}:d={dur},tremolo=f={trem}:d=0.3,volume={vol}",
        ])
        labels.append(f"[{i}:a]")

    # Soft pink-noise bed
    ni = len(freqs)
    inputs.extend([
        "-f", "lavfi", "-i",
        f"anoisesrc=d={dur}:c=pink:a=0.15,lowpass=f=400,highpass=f=60",
    ])
    labels.append(f"[{ni}:a]")

    n = len(labels)
    fade_out_start = max(duration - 4, 0)
    fc = (
        "".join(labels)
        + f"amix=inputs={n}:duration=longest:normalize=0,"
          f"lowpass=f=2000,"
          f"afade=t=in:d=3,afade=t=out:st={fade_out_start}:d=4"
    )

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc,
        "-c:a", "libmp3lame", "-q:a", "4",
        "-t", str(duration),
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning("Background music generation failed: %s", result.stderr[-300:])
            return ""
        logger.info("Background music generated: %s", output_path)
        return output_path
    except Exception as e:
        logger.warning("Background music error: %s", e)
        return ""
