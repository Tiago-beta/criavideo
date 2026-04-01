"""
Script & Audio Generator — Creates video scripts with AI and generates TTS narration via OpenAI.
"""
import os
import logging
import re
import subprocess
import tempfile
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
    voice_type: str = "builtin",
    pause_level: str = "normal",
) -> str:
    """Generate TTS audio and save to media directory. Returns file path.

    For custom voices (voice_type="custom"), uses Fish Audio with the voice as reference_id.
    For builtin voices, uses OpenAI TTS.
    pause_level: "normal" | "relaxed" | "deep" — controls silence insertion between segments.
    """
    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / "narration.mp3"

    # Enhance TTS instructions based on pause level (for gpt-4o-mini-tts)
    if pause_level in ("relaxed", "deep"):
        pacing = _get_pacing_instructions(pause_level)
        tts_instructions = f"{tts_instructions}\n{pacing}" if tts_instructions else pacing

    try:
        # For non-normal pause levels, use segment-based generation with silence insertion
        if pause_level in ("relaxed", "deep"):
            await _generate_with_pauses(
                text, voice, tts_instructions, str(output_path),
                pause_level, voice_type, audio_dir,
            )
        # Custom voices use Fish Audio
        elif voice_type == "custom" and voice:
            from app.services.fish_audio import generate_tts_long
            ok = await generate_tts_long(text, voice, str(output_path))
            if not ok:
                raise RuntimeError("Fish Audio TTS generation failed")
        # For long texts, split into chunks and concatenate
        elif len(text) > 4000:
            chunks = _split_text_for_tts(text, max_chars=3800)
            logger.info(f"Long text ({len(text)} chars) split into {len(chunks)} TTS chunks")
            chunk_paths = []
            for i, chunk in enumerate(chunks):
                chunk_path = audio_dir / f"narration_chunk_{i:03d}.mp3"
                await _generate_single_tts(chunk, voice, tts_instructions, str(chunk_path))
                chunk_paths.append(str(chunk_path))
            _concat_audio_files(chunk_paths, str(output_path))
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


def _get_pacing_instructions(pause_level: str) -> str:
    """Return TTS pacing instructions based on pause level."""
    if pause_level == "relaxed":
        return (
            "Você é um narrador profissional de audiobooks e meditações guiadas. "
            "Fale com SENTIMENTO e EMOÇÃO — cada frase deve transmitir a emoção do conteúdo. "
            "Fale de forma calma, pausada e envolvente. "
            "ENFATIZE palavras-chave com variação real de tom — palavras importantes devem soar mais suaves, mais profundas ou mais intensas. "
            "Faça pausas naturais nas reticências (...). "
            "REGRA DE ENTONAÇÃO: Quando a frase termina com reticências (...), DESÇA o tom — fique mais grave e profundo. "
            "NUNCA suba o tom nas reticências. Só eleve o tom em frases com exclamação (!) ou interrogação (?). "
            "Varie a entonação: suba o tom para curiosidade (apenas com ? ou !), desça para seriedade e conforto. "
            "Nunca soe monótono, robótico ou como quem está lendo um texto. "
            "Soe como alguém que SENTE profundamente o que está dizendo."
        )
    elif pause_level == "deep":
        return (
            "Você é um HIPNOTERAPEUTA PROFISSIONAL renomado conduzindo uma sessão real de hipnose terapêutica. "
            "Isto NÃO é uma leitura de texto — é uma INDUÇÃO HIPNÓTICA. Cada palavra deve carregar INTENÇÃO e SENTIMENTO. "
            "\n\nREGRA FUNDAMENTAL DE ENTONAÇÃO (OBRIGATÓRIA):"
            "\n- Quando a frase termina com reticências (...), a voz DEVE descer o tom, ficando mais grave e profunda no final. NUNCA suba o tom nas reticências. As reticências significam aprofundamento, condução ao transe — a voz deve BAIXAR, ficar mais grave, mais lenta, como se mergulhasse para dentro."
            "\n- Só eleve o tom quando houver exclamação (!) ou interrogação (?). Esses são os ÚNICOS momentos em que o tom pode subir."
            "\n- Frases que terminam com ponto final (.) devem ter entonação neutra ou levemente descendente."
            "\n- Esta regra é ABSOLUTAMENTE INEGOCIÁVEL. Em hipnose, subir o tom nas reticências QUEBRA o transe."
            "\n\nCOMO FALAR:"
            "\n- Fale MUITO lentamente, com profundidade emocional. Respire entre as frases."
            "\n- Dê PESO emocional a cada palavra — como se cada sílaba importasse."
            "\n- Nas reticências (...) faça pausas longas e significativas, deixando o silêncio trabalhar. A voz DESCE antes da pausa."
            "\n- Palavras como 'relaxar', 'profundo', 'calma', 'mente', 'corpo', 'respiração', 'soltar', 'confortável', 'tranquilo', 'suave' devem ser ditas com SUAVIDADE especial — mais lentas, mais graves, quase sussurradas."
            "\n- Palavras como 'feche os olhos', 'solte', 'deixe ir', 'permita-se' devem soar como um CONVITE gentil e acolhedor."
            "\n\nTOM E EMOÇÃO:"
            "\n- Tom grave, suave, aveludado e profundamente ACOLHEDOR."
            "\n- A tendência geral da voz é SEMPRE descendente — como uma onda que vai ficando mais profunda."
            "\n- ÚNICA exceção para subir o tom: frases com ! ou ? — e mesmo assim, suba de forma suave."
            "\n- Transmita SEGURANÇA e CONFIANÇA — o ouvinte precisa sentir que está em boas mãos."
            "\n- Soe empático, caloroso e genuinamente presente — como se estivesse sentado ao lado da pessoa."
            "\n- NUNCA soe como um robô, narrador de notícias ou leitor de teleprompter."
            "\n- Imagine que você é Milton Erickson conduzindo uma indução — cada palavra é medicine."
        )
    return ""


def _split_at_pause_markers(text: str, pause_level: str) -> list[dict]:
    """Split text into segments at pause markers, returning segments with silence durations.
    
    Returns list of {"text": str, "silence_after": float} dicts.
    """
    if pause_level == "relaxed":
        # Split at "..." markers — insert 1.8s silence
        parts = re.split(r'(\.{3,}|…)', text)
        segments = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            if re.match(r'^(\.{3,}|…)$', part):
                # This is an ellipsis — add silence to previous segment
                if segments:
                    segments[-1]["silence_after"] = 1.8
            else:
                segments.append({"text": part, "silence_after": 0.5})
        return segments if segments else [{"text": text, "silence_after": 0}]

    elif pause_level == "deep":
        # For deep/hypnosis: split at EVERY "..." to force real silence pauses.
        # Group 2-3 short phrases together so model keeps emotional context,
        # but never let blocks get too big (max ~350 chars).
        raw_parts = re.split(r'(\.{3,}|…)', text)
        # Merge text parts with their trailing ellipsis for context
        phrases = []
        current = ""
        for part in raw_parts:
            part_stripped = part.strip()
            if not part_stripped:
                continue
            if re.match(r'^(\.{3,}|…)$', part_stripped):
                # Ellipsis — append to current phrase and mark as pause point
                current += "..."
                if current.strip():
                    phrases.append(current.strip())
                    current = ""
            else:
                current = f"{current} {part_stripped}" if current else part_stripped
        if current.strip():
            phrases.append(current.strip())

        # Now group phrases: max 2 phrases per segment, or max ~350 chars
        segments = []
        group = ""
        group_count = 0
        for phrase in phrases:
            if group and (group_count >= 2 or len(group) + len(phrase) + 1 > 350):
                segments.append({"text": group.strip(), "silence_after": 2.5})
                group = phrase
                group_count = 1
            else:
                group = f"{group} {phrase}" if group else phrase
                group_count += 1
        if group.strip():
            segments.append({"text": group.strip(), "silence_after": 0})

        return segments if segments else [{"text": text, "silence_after": 0}]

    return [{"text": text, "silence_after": 0}]


def _generate_silence(duration: float, output_path: str):
    """Generate a silence audio file of the given duration using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration),
        "-c:a", "libmp3lame", "-b:a", "64k", "-ar", "44100",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg silence generation failed: {result.stderr[-300:]}")


async def _generate_with_pauses(
    text: str, voice: str, tts_instructions: str, output_path: str,
    pause_level: str, voice_type: str, audio_dir: Path,
):
    """Generate TTS with real silence inserted between segments based on pause_level."""
    segments = _split_at_pause_markers(text, pause_level)
    logger.info(f"Pause level '{pause_level}': split into {len(segments)} segments")

    all_parts = []  # paths of audio files (TTS + silence) to concatenate
    
    for i, seg in enumerate(segments):
        seg_text = seg["text"].strip()
        if not seg_text:
            continue
        
        # Generate TTS for this segment
        seg_path = str(audio_dir / f"pause_seg_{i:04d}.mp3")
        
        if voice_type == "custom" and voice:
            from app.services.fish_audio import generate_tts, generate_tts_long
            if len(seg_text) > 4000:
                ok = await generate_tts_long(seg_text, voice, seg_path)
            else:
                ok = await generate_tts(seg_text, voice, seg_path)
            if not ok:
                raise RuntimeError(f"Fish Audio TTS failed for segment {i}")
        elif len(seg_text) > 4000:
            # Long segment — split into sub-chunks
            chunks = _split_text_for_tts(seg_text, max_chars=3800)
            chunk_paths = []
            for ci, chunk in enumerate(chunks):
                cp = str(audio_dir / f"pause_seg_{i:04d}_chunk_{ci:03d}.mp3")
                await _generate_single_tts(chunk, voice, tts_instructions, cp)
                chunk_paths.append(cp)
            _concat_audio_files(chunk_paths, seg_path)
            for cp in chunk_paths:
                try:
                    os.remove(cp)
                except OSError:
                    pass
        else:
            await _generate_single_tts(seg_text, voice, tts_instructions, seg_path)
        
        all_parts.append(seg_path)
        
        # Add silence after this segment (if needed)
        silence_dur = seg["silence_after"]
        if silence_dur > 0 and i < len(segments) - 1:
            sil_path = str(audio_dir / f"pause_sil_{i:04d}.mp3")
            _generate_silence(silence_dur, sil_path)
            all_parts.append(sil_path)

    if len(all_parts) == 1:
        os.rename(all_parts[0], output_path)
    else:
        _concat_audio_files_reencode(all_parts, output_path)
    
    # Cleanup temp files
    for p in all_parts:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


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


def _get_default_tts_instructions() -> str:
    """Default TTS instructions for natural, expressive narration."""
    return (
        "Você é um narrador profissional de vídeos virais. "
        "Fale com EMOÇÃO e SENTIMENTO — cada frase deve transmitir a emoção do conteúdo. "
        "ENFATIZE palavras-chave com variação real de tom e intensidade. "
        "Varie a entonação constantemente: surpreenda, provoque curiosidade, crie suspense. "
        "Nunca soe monótono, robótico ou como quem está apenas lendo um texto. "
        "Use ritmo dinâmico: acelere na empolgação e desacelere na reflexão. "
        "Soe como alguém que ACREDITA no que diz e quer envolver o ouvinte."
    )


async def _generate_single_tts(text: str, voice: str, tts_instructions: str, output_path: str):
    """Generate a single TTS audio file via OpenAI. Always uses gpt-4o-mini-tts for expressiveness."""
    instructions = tts_instructions or _get_default_tts_instructions()
    tts_kwargs = {
        "model": "gpt-4o-mini-tts",
        "voice": voice,
        "input": text,
        "instructions": instructions,
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


def _concat_audio_files_reencode(paths: list[str], output_path: str):
    """Concatenate audio files with re-encoding to normalize sample rate/channels."""
    list_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    try:
        for p in paths:
            safe = p.replace("'", "'\\''")
            list_file.write(f"file '{safe}'\n")
        list_file.close()
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file.name,
            "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "1",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat-reencode failed: {result.stderr[-300:]}")
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
