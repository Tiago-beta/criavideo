"""
Dialogue audio orchestration for realistic videos.

Flow:
1) Generate compact multi-character dialogue turns from the user prompt.
2) Resolve voice per character from selected/default voice profiles.
3) Synthesize each turn with TTS and compose a timeline audio track.
4) Optionally generate a separate instrumental background track.
"""
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import openai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import VoiceProfile
from app.services.audio_tools import compose_dialogue_tracks
from app.services.script_audio import generate_tts_audio
from app.services.suno_music import generate_suno_music
from app.services.video_composer import _get_duration

logger = logging.getLogger(__name__)
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)

_DEFAULT_CHARACTERS = ["Personagem"]
_DEFAULT_BUILTIN_VOICES = ["onyx", "nova", "echo", "shimmer", "alloy", "coral", "ash", "sage"]
_VALID_MOODS = {
    "inspiracional",
    "informativo",
    "misterioso",
    "motivacional",
    "urgente",
    "reflexivo",
    "dramatico",
}
_STYLE_TO_MOOD = {
    "cinematic": "inspiracional",
    "documentary": "informativo",
    "dramatic": "dramatico",
    "mystery": "misterioso",
    "motivational": "motivacional",
    "urgent": "urgente",
    "reflective": "reflexivo",
}


def _estimate_turn_count(target_duration: float) -> int:
    duration = max(3.0, float(target_duration or 0.0))
    if duration <= 4.0:
        return 1
    if duration <= 6.0:
        return 2
    estimated = int(round(duration / 2.6))
    return max(2, min(10, estimated))


def _normalize_characters(raw_names: list[str] | None, limit: int = 4) -> list[str]:
    names: list[str] = []
    for raw in raw_names or []:
        cleaned = re.sub(r"\s+", " ", str(raw or "").strip())
        if not cleaned:
            continue
        cleaned = cleaned[:40]
        if cleaned not in names:
            names.append(cleaned)
        if len(names) >= limit:
            break
    if len(names) < 1:
        return _DEFAULT_CHARACTERS.copy()
    return names


def _fallback_dialogue_turns(prompt_text: str, characters: list[str], target_duration: float) -> list[dict]:
    turns_count = _estimate_turn_count(target_duration)
    topic = re.sub(r"\s+", " ", (prompt_text or "").strip())[:200]
    if not topic:
        topic = "o tema do video"

    templates = [
        "Voce viu isso? Parece inacreditavel, mas faz sentido quando observamos melhor.",
        "Concordo, e o detalhe principal e como tudo muda em poucos segundos.",
        "Exato. Se a gente prestar atencao, a explicacao fica clara e envolvente.",
        "E isso conecta com a emocao da cena, sem perder ritmo.",
        "Vamos resumir de forma direta para o publico entender rapido.",
        "Fechou. O ponto central e simples: foco, contexto e impacto visual.",
    ]

    turns: list[dict] = []
    for idx in range(turns_count):
        speaker = characters[idx % len(characters)]
        base = templates[idx % len(templates)]
        text = base
        if idx == 0:
            text = f"Sobre {topic}, olha isso: {base}"
        turns.append({"speaker": speaker, "text": text[:220]})
    return turns


async def _generate_dialogue_turns(
    prompt_text: str,
    characters: list[str],
    target_duration: float,
    tone: str,
    interaction_persona: str,
) -> list[dict]:
    if not settings.openai_api_key:
        return _fallback_dialogue_turns(prompt_text, characters, target_duration)

    turns_count = _estimate_turn_count(target_duration)
    system_msg = (
        "Voce escreve dialogos curtos para locucao de video. "
        "Responda APENAS JSON valido no formato {\"turns\":[{\"speaker\":\"...\",\"text\":\"...\"}]}. "
        "Cada fala deve ser curta (8 a 22 palavras), natural e falada. "
        "Nao use narracao externa, somente falas dos personagens."
    )
    user_msg = (
        f"Tema: {prompt_text}\n"
        f"Personagens permitidos: {', '.join(characters)}\n"
        f"Quantidade alvo de falas: {turns_count}\n"
        f"Tom: {tone}\n"
        f"Persona de interacao: {interaction_persona}\n"
        "Regras: alternar personagens quando possivel, evitar repeticao, manter clareza."
    )

    try:
        resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=1000,
        )
        payload_raw = (resp.choices[0].message.content or "{}").strip()
        payload = json.loads(payload_raw)
        raw_turns = payload.get("turns") if isinstance(payload, dict) else None
        if not isinstance(raw_turns, list) or not raw_turns:
            return _fallback_dialogue_turns(prompt_text, characters, target_duration)

        turns: list[dict] = []
        for idx, item in enumerate(raw_turns):
            if not isinstance(item, dict):
                continue
            speaker_raw = str(item.get("speaker") or "").strip()
            text_raw = re.sub(r"\s+", " ", str(item.get("text") or "").strip())
            if not text_raw:
                continue
            if speaker_raw not in characters:
                speaker_raw = characters[idx % len(characters)]
            turns.append({"speaker": speaker_raw, "text": text_raw[:280]})
            if len(turns) >= turns_count:
                break

        if len(turns) < 1:
            return _fallback_dialogue_turns(prompt_text, characters, target_duration)
        return turns
    except Exception as exc:
        logger.warning(f"Dialogue generation failed, using fallback: {exc}")
        return _fallback_dialogue_turns(prompt_text, characters, target_duration)


async def _resolve_voice_configs(
    db: AsyncSession,
    user_id: int,
    characters: list[str],
    voice_profile_ids: list[int],
) -> dict[str, dict]:
    config_by_character: dict[str, dict] = {}

    profile_map: dict[int, VoiceProfile] = {}
    clean_ids: list[int] = []
    for pid in (voice_profile_ids or []):
        try:
            parsed = int(pid)
        except Exception:
            continue
        if parsed > 0 and parsed not in clean_ids:
            clean_ids.append(parsed)
        if len(clean_ids) >= 4:
            break
    if clean_ids:
        result = await db.execute(
            select(VoiceProfile).where(
                VoiceProfile.user_id == int(user_id),
                VoiceProfile.id.in_(clean_ids),
            )
        )
        profiles = result.scalars().all()
        profile_map = {int(profile.id): profile for profile in profiles}

    default_profile = None
    default_result = await db.execute(
        select(VoiceProfile)
        .where(VoiceProfile.user_id == int(user_id), VoiceProfile.is_default == True)
        .limit(1)
    )
    default_profile = default_result.scalar_one_or_none()

    for idx, character in enumerate(characters):
        selected_profile = None
        if idx < len(clean_ids):
            selected_profile = profile_map.get(clean_ids[idx])
        if not selected_profile:
            selected_profile = default_profile

        if selected_profile:
            profile_voice_type = str(selected_profile.voice_type or "builtin").strip().lower()
            profile_custom_id = str(selected_profile.openai_voice_id or "").strip()
            fallback_voice = selected_profile.builtin_voice or "onyx"

            if profile_voice_type == "elevenlabs" and profile_custom_id:
                config_by_character[character] = {
                    "voice_type": "elevenlabs",
                    "voice": profile_custom_id,
                    "fallback_voice": fallback_voice,
                    "tts_instructions": (selected_profile.tts_instructions or "")[:1200],
                }
            elif profile_voice_type == "custom" and profile_custom_id:
                config_by_character[character] = {
                    "voice_type": "custom",
                    "voice": profile_custom_id,
                    "fallback_voice": fallback_voice,
                    "tts_instructions": (selected_profile.tts_instructions or "")[:1200],
                }
            else:
                config_by_character[character] = {
                    "voice_type": "builtin",
                    "voice": fallback_voice,
                    "fallback_voice": "onyx",
                    "tts_instructions": (selected_profile.tts_instructions or "")[:1200],
                }
            continue

        fallback_voice = _DEFAULT_BUILTIN_VOICES[idx % len(_DEFAULT_BUILTIN_VOICES)]
        config_by_character[character] = {
            "voice_type": "builtin",
            "voice": fallback_voice,
            "fallback_voice": "onyx",
            "tts_instructions": "",
        }

    return config_by_character


def _pick_music_mood(realistic_style: str, dialogue_tone: str) -> str:
    style_key = str(realistic_style or "").strip().lower()
    if style_key in _STYLE_TO_MOOD:
        return _STYLE_TO_MOOD[style_key]

    tone_key = str(dialogue_tone or "").strip().lower()
    if tone_key in _VALID_MOODS:
        return tone_key
    return "inspiracional"


def _trim_audio_to_duration(input_path: str, output_path: str, target_duration: float) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", f"{max(0.1, float(target_duration)):.3f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Dialogue trim failed: {result.stderr[-300:]}")
    return output_path


async def generate_dialogue_audio_bundle(
    db: AsyncSession,
    user_id: int,
    project_id: int,
    prompt_text: str,
    target_duration: float,
    characters: list[str] | None = None,
    voice_profile_ids: list[int] | None = None,
    tone: str = "informativo",
    interaction_persona: str = "natureza",
    realistic_style: str = "",
    add_music: bool = True,
) -> dict:
    """Generate dialogue script + spoken dialogue track (+ optional separate music track)."""
    normalized_characters = _normalize_characters(characters)
    target_duration = max(3.0, float(target_duration or 8.0))

    turns = await _generate_dialogue_turns(
        prompt_text=prompt_text,
        characters=normalized_characters,
        target_duration=target_duration,
        tone=tone,
        interaction_persona=interaction_persona,
    )
    if not turns:
        raise RuntimeError("Could not generate dialogue turns")

    voice_configs = await _resolve_voice_configs(
        db=db,
        user_id=user_id,
        characters=normalized_characters,
        voice_profile_ids=voice_profile_ids or [],
    )

    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    audio_dir.mkdir(parents=True, exist_ok=True)

    timeline_segments: list[dict] = []
    timeline_meta: list[dict] = []
    current_start = 0.0

    for idx, turn in enumerate(turns):
        if current_start >= target_duration - 0.05:
            break

        speaker = turn["speaker"]
        text = turn["text"]
        cfg = voice_configs.get(speaker) or {
            "voice_type": "builtin",
            "voice": "onyx",
            "fallback_voice": "onyx",
            "tts_instructions": "",
        }

        output_name = f"dialogue_seg_{idx:03d}.mp3"
        segment_path = ""
        try:
            segment_path = await generate_tts_audio(
                text=text,
                voice=cfg["voice"],
                project_id=project_id,
                tts_instructions=cfg.get("tts_instructions", ""),
                voice_type=cfg.get("voice_type", "builtin"),
                pause_level="normal",
                tone=tone,
                output_filename=output_name,
            )
        except Exception as tts_error:
            logger.warning(f"Dialogue TTS failed for speaker '{speaker}', retrying with builtin voice: {tts_error}")
            segment_path = await generate_tts_audio(
                text=text,
                voice=cfg.get("fallback_voice", "onyx") or "onyx",
                project_id=project_id,
                tts_instructions="",
                voice_type="builtin",
                pause_level="normal",
                tone=tone,
                output_filename=output_name,
            )

        if not segment_path or not os.path.exists(segment_path):
            continue

        duration = _get_duration(segment_path)
        if duration <= 0:
            duration = max(0.8, len(text.split()) / 2.7)

        remaining = target_duration - current_start
        if idx > 0 and remaining < 0.8:
            break
        if idx > 0 and duration > remaining + 0.5:
            continue

        timeline_segments.append({
            "path": segment_path,
            "start": current_start,
            "volume": 1.0,
        })
        timeline_meta.append({
            "speaker": speaker,
            "text": text,
            "start": round(current_start, 3),
            "end": round(min(current_start + duration, target_duration), 3),
            "audio_path": segment_path,
        })

        current_start += duration + 0.12
        if current_start >= target_duration - 0.05:
            break

    if not timeline_segments:
        raise RuntimeError("No dialogue audio segments were generated")

    dialogue_audio_path = str(audio_dir / "dialogue_mix.mp3")
    dialogue_audio_path = await compose_dialogue_tracks(timeline_segments, dialogue_audio_path)
    mixed_duration = _get_duration(dialogue_audio_path)
    if mixed_duration > target_duration + 0.25:
        trimmed_path = str(audio_dir / "dialogue_mix_trimmed.mp3")
        dialogue_audio_path = _trim_audio_to_duration(dialogue_audio_path, trimmed_path, target_duration)

    mood = _pick_music_mood(realistic_style=realistic_style, dialogue_tone=tone)
    music_path = ""
    if add_music:
        music_output = str(audio_dir / "dialogue_music.mp3")
        music_path = await generate_suno_music(
            output_path=music_output,
            duration=max(target_duration + 1.0, 6.0),
            mood=mood,
            topic=(prompt_text or "")[:120],
        )

    script_text = "\n".join([f"{item['speaker']}: {item['text']}" for item in timeline_meta])
    estimated_duration = _get_duration(dialogue_audio_path)
    if estimated_duration <= 0:
        estimated_duration = max(min(current_start - 0.12, target_duration), 0.0)

    return {
        "audio_path": dialogue_audio_path,
        "music_path": music_path or "",
        "turns": timeline_meta,
        "script": script_text,
        "characters": normalized_characters,
        "estimated_duration": round(min(estimated_duration, target_duration), 2),
    }
