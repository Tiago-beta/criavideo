"""Background tasks and helpers for the Similar Video workflow."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

import openai
try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import async_session
from app.models import VideoProject, VideoRender, VideoScene, VideoStatus
from app.services.baixatudo_client import BaixaTudoClient, BaixaTudoError
from app.services.grok_video import generate_video_clip
from app.services.minimax_video import generate_minimax_video
from app.services.multi_clip import concatenate_clips
from app.services.runpod_video import generate_wan_video
from app.services.scene_generator import (
    build_similar_scene_continuity_prompt,
    generate_scene_image,
    merge_reference_images_with_nano_banana,
)
from app.services.seedance_video import generate_realistic_video
from app.services.thumbnail_generator import generate_thumbnail_from_frame
from app.services.video_composer import _get_duration as get_duration


logger = logging.getLogger(__name__)
settings = get_settings()
_google_scene_analysis_client = (
    genai.Client(api_key=settings.google_ai_api_key)
    if genai is not None and (settings.google_ai_api_key or "").strip()
    else None
)
_SIMILAR_SCENE_TIME_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_SIMILAR_SCENE_DETECT_THRESHOLD = 0.22
_SIMILAR_SCENE_MIN_SECONDS = 0.85
_SIMILAR_SCENE_MAX_COUNT = 180
_SIMILAR_REFERENCE_CLIP_FPS = 30
_SIMILAR_GOOGLE_ANALYSIS_MODEL = "gemini-2.5-flash"
_SIMILAR_CONTEXT_FRAME_SAMPLE_COUNT = 6
_SIMILAR_CONTEXT_SUMMARY_LIMIT = 1800
_SIMILAR_TRANSCRIPT_LIMIT = 4200
_SIMILAR_SCENE_DIALOGUE_LIMIT = 420


def _safe_tags_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _safe_error_message(err: Exception, fallback: str) -> str:
    try:
        raw = str(err or "").strip()
    except Exception:
        raw = ""
    if raw and raw not in {"{}", "[]", "None", "null", "[object Object]"}:
        return raw
    return fallback


def _normalize_source_url(raw_url: object) -> str:
    raw = str(raw_url or "").strip()
    if not raw:
        return ""

    try:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "https").lower()
        host = (parsed.netloc or "").lower()
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")

        keep_query: list[tuple[str, str]] = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            key_l = (key or "").lower().strip()
            if key_l in {"v", "video_id", "story_fbid", "id", "reel_id"} and value:
                keep_query.append((key_l, value))

        query = urlencode(keep_query)
        normalized = f"{scheme}://{host}{path}"
        if query:
            normalized = f"{normalized}?{query}"
        return normalized
    except Exception:
        return raw.lower().rstrip("/")


async def _try_reuse_cached_reference_video(
    db,
    *,
    project_id: int,
    user_id: int,
    source_url: str,
    target_output_path: str,
) -> dict | None:
    normalized_target = _normalize_source_url(source_url)
    if not normalized_target:
        return None

    result = await db.execute(
        select(VideoProject.id, VideoProject.tags)
        .where(VideoProject.user_id == int(user_id), VideoProject.id != int(project_id))
        .order_by(VideoProject.id.desc())
        .limit(120)
    )
    rows = result.all()

    target_file = Path(target_output_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)

    for row in rows:
        candidate_id = int(row[0])
        tags = _safe_tags_dict(row[1])
        if str(tags.get("type") or "").strip().lower() != "similar":
            continue

        candidate_norm = _normalize_source_url(tags.get("similar_normalized_url"))
        candidate_src = _normalize_source_url(tags.get("similar_source_url"))
        if normalized_target not in {candidate_norm, candidate_src}:
            continue

        cached_path_raw = str(tags.get("similar_local_video_path") or "").strip()
        if not cached_path_raw:
            continue

        cached_path = Path(cached_path_raw)
        if not cached_path.exists() or cached_path.stat().st_size <= 0:
            continue

        try:
            if cached_path.resolve() != target_file.resolve():
                shutil.copy2(cached_path, target_file)
            if not target_file.exists() or target_file.stat().st_size <= 0:
                continue
        except Exception:
            continue

        return {
            "output_path": str(target_file),
            "task_id": f"reused:{candidate_id}",
            "source_url": str(tags.get("similar_source_url") or source_url),
            "normalized_url": str(tags.get("similar_normalized_url") or source_url),
            "reused_project_id": candidate_id,
        }

    return None


async def _ffprobe_duration(video_path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffprobe failed to read video duration")
    try:
        value = float((stdout or b"").decode().strip())
    except Exception as exc:
        raise RuntimeError("Could not parse video duration") from exc
    if value <= 0:
        raise RuntimeError("Video duration is zero")
    return value


async def _extract_frame(video_path: str, timestamp_seconds: float, output_path: str) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, float(timestamp_seconds)):.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 0:
        details = (stderr or b"").decode(errors="ignore")[-500:]
        raise RuntimeError(f"Frame extraction failed: {details}")


async def _detect_scene_change_timestamps(
    video_path: str,
    threshold: float = _SIMILAR_SCENE_DETECT_THRESHOLD,
) -> list[float]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-i",
        video_path,
        "-an",
        "-vf",
        f"select=gt(scene\\,{float(threshold):.3f}),showinfo",
        "-vsync",
        "vfr",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    raw_output = (stderr or b"").decode(errors="ignore")
    if proc.returncode != 0 and not raw_output.strip():
        raise RuntimeError("ffmpeg falhou ao detectar cortes do video de referencia")

    cut_times: list[float] = []
    for match in _SIMILAR_SCENE_TIME_RE.finditer(raw_output):
        try:
            value = float(match.group(1))
        except Exception:
            continue
        if value > 0:
            cut_times.append(value)
    return cut_times


def _compress_similar_scene_ranges(
    ranges: list[tuple[float, float]],
    *,
    max_count: int = _SIMILAR_SCENE_MAX_COUNT,
) -> list[tuple[float, float]]:
    compressed = [(float(start), float(end)) for start, end in ranges if float(end) - float(start) > 0.05]
    while len(compressed) > max_count:
        shortest_idx = min(
            range(len(compressed)),
            key=lambda idx: max(0.0, compressed[idx][1] - compressed[idx][0]),
        )
        if shortest_idx == 0 and len(compressed) > 1:
            _, first_end = compressed[0]
            _, second_end = compressed[1]
            compressed[0:2] = [(0.0, max(first_end, second_end))]
        else:
            prev_start, _ = compressed[shortest_idx - 1]
            _, current_end = compressed[shortest_idx]
            compressed[shortest_idx - 1:shortest_idx + 1] = [(prev_start, current_end)]
    return [(round(start, 3), round(end, 3)) for start, end in compressed]


def _build_similar_scene_ranges(
    duration_seconds: float,
    cut_times: list[float],
    *,
    target_chunk_seconds: float = 5.0,
    min_seconds: float = _SIMILAR_SCENE_MIN_SECONDS,
    max_count: int = _SIMILAR_SCENE_MAX_COUNT,
) -> list[tuple[float, float]]:
    total_duration = max(float(duration_seconds or 0), 0.1)
    safe_chunk = max(float(target_chunk_seconds or 5.0), min_seconds)
    normalized_cuts: list[float] = []
    for raw_cut in sorted(float(value) for value in (cut_times or [])):
        if raw_cut <= min_seconds * 0.35 or raw_cut >= total_duration - (min_seconds * 0.35):
            continue
        if normalized_cuts and raw_cut - normalized_cuts[-1] < min_seconds * 0.55:
            continue
        normalized_cuts.append(round(raw_cut, 3))

    boundaries = [0.0, *normalized_cuts, total_duration]
    raw_ranges: list[tuple[float, float]] = []
    for idx in range(len(boundaries) - 1):
        start = float(boundaries[idx])
        end = float(boundaries[idx + 1])
        if end - start > 0.05:
            raw_ranges.append((start, end))

    merged_ranges: list[tuple[float, float]] = []
    for start, end in raw_ranges:
        if end - start < min_seconds and merged_ranges:
            prev_start, _ = merged_ranges[-1]
            merged_ranges[-1] = (prev_start, end)
        else:
            merged_ranges.append((start, end))

    if len(merged_ranges) > 1 and (merged_ranges[0][1] - merged_ranges[0][0]) < min_seconds:
        first_start, _ = merged_ranges[0]
        _, second_end = merged_ranges[1]
        merged_ranges = [(first_start, second_end), *merged_ranges[2:]]

    final_ranges: list[tuple[float, float]] = []
    for start, end in merged_ranges or [(0.0, total_duration)]:
        segment_duration = end - start
        if segment_duration <= safe_chunk + 0.05:
            final_ranges.append((start, end))
            continue

        segment_count = max(2, int(math.ceil(segment_duration / safe_chunk)))
        while segment_count > 1 and (segment_duration / segment_count) < min_seconds:
            segment_count -= 1

        for idx in range(segment_count):
            part_start = start + ((segment_duration * idx) / segment_count)
            part_end = end if idx == segment_count - 1 else start + ((segment_duration * (idx + 1)) / segment_count)
            if part_end - part_start > 0.05:
                final_ranges.append((part_start, part_end))

    if not final_ranges:
        final_ranges = [(0.0, total_duration)]

    return _compress_similar_scene_ranges(final_ranges, max_count=max_count)


def _extract_similar_reference_frames(tags: dict | None) -> dict[str, str]:
    raw_map = tags.get("similar_reference_frames") if isinstance(tags, dict) else {}
    if not isinstance(raw_map, dict):
        return {}

    reference_frames: dict[str, str] = {}
    for key, raw_path in raw_map.items():
        path = str(raw_path or "").strip()
        if path and os.path.exists(path):
            reference_frames[str(key)] = path
    return reference_frames


def _get_similar_scene_reference_frame_path(
    scene: VideoScene,
    reference_frames_by_scene_index: dict[str, str] | None = None,
) -> str:
    scene_key = str(int(scene.scene_index or 0))
    candidate = str((reference_frames_by_scene_index or {}).get(scene_key) or "").strip()
    if candidate and os.path.exists(candidate):
        return candidate
    return ""


def _clear_similar_unified_clip_tags(tags: dict | None) -> dict:
    cleaned = _safe_tags_dict(tags)
    for stale_key in (
        "similar_unified_clip_path",
        "similar_unified_clip_engine",
        "similar_unified_clip_duration",
        "similar_unified_clip_generated_at",
        "similar_unified_reference_image_path",
        "similar_unified_reference_frame_count",
    ):
        cleaned.pop(stale_key, None)
    return cleaned


def _collect_similar_reference_frame_paths(
    scenes: list[VideoScene],
    reference_frames_by_scene_index: dict[str, str] | None = None,
) -> list[str]:
    ordered_paths: list[str] = []

    for scene in scenes or []:
        candidate = _get_similar_scene_reference_frame_path(scene, reference_frames_by_scene_index)
        if candidate and candidate not in ordered_paths:
            ordered_paths.append(candidate)

    if ordered_paths:
        return ordered_paths

    for _, raw_path in sorted(
        (reference_frames_by_scene_index or {}).items(),
        key=lambda item: int(item[0]) if str(item[0]).isdigit() else 999999,
    ):
        candidate = str(raw_path or "").strip()
        if candidate and os.path.exists(candidate) and candidate not in ordered_paths:
            ordered_paths.append(candidate)

    return ordered_paths


async def _prepare_similar_unified_reference_image(
    reference_image_paths: list[str],
    prompt_text: str,
    aspect_ratio: str,
    output_path: str,
) -> str:
    valid_paths = [path for path in (reference_image_paths or []) if path and os.path.exists(path)]
    if not valid_paths:
        return ""

    if len(valid_paths) == 1:
        single_path = valid_paths[0]
        if output_path and os.path.abspath(single_path) != os.path.abspath(output_path):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(single_path, output_path)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path
        return single_path

    prompt_seed = str(prompt_text or "").strip() or "Cena cinematografica coerente e realista."
    loop = asyncio.get_running_loop()
    merged_path = await loop.run_in_executor(
        None,
        merge_reference_images_with_nano_banana,
        valid_paths,
        prompt_seed[:1600],
        aspect_ratio,
        output_path,
    )
    candidate = str(merged_path or "").strip()
    if candidate and os.path.exists(candidate) and os.path.getsize(candidate) > 0:
        return candidate
    return valid_paths[0]


def _image_file_to_data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    raw = Path(path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _normalize_similar_context_text(raw_text: object, *, limit: int = 900) -> str:
    cleaned = re.sub(r"\s+", " ", str(raw_text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0].strip() or cleaned[:limit]


def _build_similar_scene_speech_lock(prompt_text: str, spoken_text: object = "") -> str:
    base_prompt = str(prompt_text or "").strip()
    spoken_excerpt = _normalize_similar_context_text(spoken_text, limit=320).strip().strip('"“”')
    if not spoken_excerpt:
        return base_prompt

    if "fala obrigatoria em pt-br" in base_prompt.lower() or "fala obrigatória em pt-br" in base_prompt.lower():
        return base_prompt

    speech_lock = (
        'FALA OBRIGATORIA EM PT-BR: use exatamente esta fala no audio da cena, sem resumir, '\
        f'reescrever ou trocar palavras: "{spoken_excerpt}". '
        'Mantenha a fala natural, coerente com a acao visual e claramente audivel.'
    )
    return f"{base_prompt}\n\n{speech_lock}".strip()


def _build_scene_analysis_instruction(
    start_time: float,
    end_time: float,
    duration_seconds: float,
    *,
    global_context: str = "",
    spoken_context: str = "",
) -> str:
    global_excerpt = _normalize_similar_context_text(global_context, limit=1200)
    spoken_excerpt = _normalize_similar_context_text(spoken_context, limit=_SIMILAR_SCENE_DIALOGUE_LIMIT)

    lines = [
        "Analise este frame e crie um prompt cinematográfico em português do Brasil.",
        "Escreva com ortografia, acentuação e pontuação corretas do pt-BR.",
        "Descreva com riqueza de detalhes o sujeito principal, a ação visível, o enquadramento, o ambiente, a luz, as cores, a textura e o movimento de câmera.",
        "Evite frases genéricas como 'cena cinematográfica' sem contexto visual real.",
        f"A cena representa o trecho de {start_time:.1f}s até {end_time:.1f}s de um vídeo de {duration_seconds:.1f}s.",
    ]

    if global_excerpt:
        lines.append(f"Contexto geral do vídeo: {global_excerpt}")
    if spoken_excerpt:
        lines.append(f"Falas, narração ou áudio neste trecho: {spoken_excerpt}")

    lines.extend(
        [
            "Use o contexto geral e o áudio apenas como apoio narrativo, sem inventar elementos que contradigam o frame.",
            "Se houver conversa ou narração, reflita isso nas expressões, gestos, intenção dramática e situação da cena quando fizer sentido visualmente.",
            "Retorne somente o prompt final em um único parágrafo, sem marcadores e sem JSON.",
        ]
    )
    return " ".join(line.strip() for line in lines if line and line.strip())


def _pick_similar_context_frame_paths(frame_paths: list[str], *, max_items: int = _SIMILAR_CONTEXT_FRAME_SAMPLE_COUNT) -> list[str]:
    valid_paths = [str(path or "").strip() for path in (frame_paths or []) if str(path or "").strip()]
    if len(valid_paths) <= max_items:
        return valid_paths

    selected: list[str] = []
    total = len(valid_paths)
    for idx in range(max_items):
        pick_index = round((idx * (total - 1)) / max(max_items - 1, 1))
        candidate = valid_paths[pick_index]
        if candidate not in selected:
            selected.append(candidate)
    return selected


def _extract_scene_transcript_excerpt(
    words: list[dict] | None,
    start_time: float,
    end_time: float,
    *,
    padding_seconds: float = 0.8,
    limit: int = _SIMILAR_SCENE_DIALOGUE_LIMIT,
) -> str:
    if not isinstance(words, list) or not words:
        return ""

    lower_bound = max(0.0, float(start_time or 0.0) - padding_seconds)
    upper_bound = max(lower_bound, float(end_time or lower_bound) + padding_seconds)

    tokens: list[str] = []
    for item in words:
        if not isinstance(item, dict):
            continue
        try:
            word_start = float(item.get("start", 0.0) or 0.0)
            word_end = float(item.get("end", word_start) or word_start)
        except Exception:
            continue
        if word_end < lower_bound or word_start > upper_bound:
            continue
        token = str(item.get("word") or "").strip()
        if token:
            tokens.append(token)

    if not tokens:
        return ""
    return _normalize_similar_context_text(" ".join(tokens), limit=limit)


async def _extract_audio_track_for_similar_context(video_path: str, output_path: str) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        details = (stderr or b"").decode(errors="ignore")[-500:]
        raise RuntimeError(f"Falha ao extrair áudio do vídeo de referência: {details}")
    return output_path


async def _transcribe_similar_video_context(video_path: str) -> tuple[str, list[dict]]:
    audio_path = str(Path(video_path).with_name("reference_audio_context.mp3"))
    try:
        await _extract_audio_track_for_similar_context(video_path, audio_path)
        from app.services.transcriber import transcribe_audio

        result = await asyncio.to_thread(transcribe_audio, audio_path, "pt", "")
        if not isinstance(result, dict):
            return "", []

        transcript_text = _normalize_similar_context_text(result.get("text", ""), limit=_SIMILAR_TRANSCRIPT_LIMIT)
        transcript_words = result.get("words", []) if isinstance(result.get("words", []), list) else []
        return transcript_text, transcript_words
    except Exception as exc:
        logger.warning("Similar audio context transcription failed: %s", exc)
        return "", []
    finally:
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception:
            pass


def _request_similar_video_context_from_google_sync(
    frame_paths: list[str],
    transcript_text: str,
    duration_seconds: float,
) -> str:
    if _google_scene_analysis_client is None or genai_types is None:
        raise RuntimeError("Google video-context client indisponível")

    contents: list[object] = [
        (
            "Analise estes frames representativos do mesmo vídeo e produza um resumo curto em português do Brasil, "
            "com ortografia, acentuação e pontuação corretas. "
            "Explique o contexto geral do que acontece no vídeo, quem são os personagens ou elementos principais, "
            "qual é a situação dramática, o ambiente, a progressão da ação e, se houver, o assunto das falas, da narração ou do áudio. "
            f"O vídeo completo tem {duration_seconds:.1f}s. "
            "Não descreva frame por frame separadamente. Responda em um único parágrafo objetivo."
        )
    ]

    transcript_excerpt = _normalize_similar_context_text(transcript_text, limit=_SIMILAR_TRANSCRIPT_LIMIT)
    if transcript_excerpt:
        contents.append(f"Transcrição do áudio do vídeo: {transcript_excerpt}")

    for frame_path in _pick_similar_context_frame_paths(frame_paths):
        mime_type = mimetypes.guess_type(frame_path)[0] or "image/jpeg"
        frame_bytes = Path(frame_path).read_bytes()
        if not frame_bytes:
            continue
        contents.append(genai_types.Part.from_bytes(data=frame_bytes, mime_type=mime_type))

    response = _google_scene_analysis_client.models.generate_content(
        model=_SIMILAR_GOOGLE_ANALYSIS_MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=700,
        ),
    )
    return _normalize_similar_context_text(getattr(response, "text", "") or response, limit=_SIMILAR_CONTEXT_SUMMARY_LIMIT)


async def _request_similar_video_context_from_google(
    frame_paths: list[str],
    transcript_text: str,
    duration_seconds: float,
) -> str:
    return await asyncio.to_thread(
        _request_similar_video_context_from_google_sync,
        frame_paths,
        transcript_text,
        duration_seconds,
    )


async def _build_similar_video_context(
    video_path: str,
    duration_seconds: float,
    frame_paths: list[str],
) -> tuple[str, str, list[dict]]:
    transcript_text, transcript_words = await _transcribe_similar_video_context(video_path)

    context_summary = ""
    try:
        if frame_paths and _google_scene_analysis_client is not None:
            context_summary = await _request_similar_video_context_from_google(
                frame_paths,
                transcript_text,
                duration_seconds,
            )
    except Exception as exc:
        logger.warning("Similar global video-context summary failed: %s", exc)

    if not context_summary and transcript_text:
        context_summary = transcript_text

    return (
        _normalize_similar_context_text(context_summary, limit=_SIMILAR_CONTEXT_SUMMARY_LIMIT),
        transcript_text,
        transcript_words,
    )


def _coerce_openai_content_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("scene_prompt", "text", "output_text", "content", "value", "prompt", "description"):
            if key in value:
                return _coerce_openai_content_to_text(value.get(key))
        return ""
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragment = _coerce_openai_content_to_text(item)
            if fragment:
                fragments.append(fragment)
        return "\n".join(fragments).strip()

    for attr in ("scene_prompt", "text", "output_text", "content", "value"):
        if hasattr(value, attr):
            return _coerce_openai_content_to_text(getattr(value, attr))
    return ""


def _extract_scene_prompt_from_content(content: object) -> str:
    candidate = _coerce_openai_content_to_text(content)
    if not candidate:
        return ""

    candidate = candidate.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()

    parsed = None
    if candidate.startswith("{") or candidate.startswith("["):
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None

    if parsed is not None:
        if isinstance(parsed, dict):
            for key in ("scene_prompt", "prompt", "description", "output", "text"):
                value = str(parsed.get(key) or "").strip()
                if value:
                    candidate = value
                    break
            else:
                candidate = ""
        elif isinstance(parsed, list):
            chunks = [_extract_scene_prompt_from_content(item) for item in parsed]
            candidate = " ".join(chunk for chunk in chunks if chunk).strip()
        elif isinstance(parsed, str):
            candidate = parsed.strip()
        else:
            candidate = ""

    candidate = re.sub(r"^\s*(?:scene_prompt|prompt|descricao|descri[cç][aã]o)\s*:\s*", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip().strip('"\'')
    candidate = re.sub(r"\s+", " ", candidate).strip()
    return candidate


def _is_quota_exhausted_error(exc: Exception) -> bool:
    raw = str(exc or "").strip().lower()
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 and ("insufficient_quota" in raw or "quota" in raw or "billing" in raw):
        return True
    return "insufficient_quota" in raw


def _request_scene_prompt_from_google_sync(
    frame_path: str,
    start_time: float,
    end_time: float,
    duration_seconds: float,
    global_context: str = "",
    spoken_context: str = "",
) -> str:
    if _google_scene_analysis_client is None or genai_types is None:
        raise RuntimeError("Google scene-analysis client indisponível")

    mime_type = mimetypes.guess_type(frame_path)[0] or "image/jpeg"
    image_bytes = Path(frame_path).read_bytes()
    if not image_bytes:
        raise RuntimeError("Frame image is empty")

    instruction_text = _build_scene_analysis_instruction(
        start_time,
        end_time,
        duration_seconds,
        global_context=global_context,
        spoken_context=spoken_context,
    )

    response = _google_scene_analysis_client.models.generate_content(
        model=_SIMILAR_GOOGLE_ANALYSIS_MODEL,
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            instruction_text,
        ],
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=450,
        ),
    )
    return _extract_scene_prompt_from_content(getattr(response, "text", "") or response)


async def _request_scene_prompt_from_google(
    frame_path: str,
    start_time: float,
    end_time: float,
    duration_seconds: float,
    global_context: str = "",
    spoken_context: str = "",
) -> str:
    return await asyncio.to_thread(
        _request_scene_prompt_from_google_sync,
        frame_path,
        start_time,
        end_time,
        duration_seconds,
        global_context,
        spoken_context,
    )


async def _request_scene_prompt_from_model(
    client: openai.AsyncOpenAI,
    model_name: str,
    image_data_url: str,
    start_time: float,
    end_time: float,
    duration_seconds: float,
    *,
    structured: bool,
    global_context: str = "",
    spoken_context: str = "",
) -> str:
    instruction_text = _build_scene_analysis_instruction(
        start_time,
        end_time,
        duration_seconds,
        global_context=global_context,
        spoken_context=spoken_context,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Você analisa frames de vídeo e escreve prompts cinematográficos em português do Brasil. "
                "Use ortografia, acentuação e pontuação corretas do pt-BR. "
                "Descreva a cena de forma concreta, citando sujeito, ação, ambiente, objetos, enquadramento, luz e movimento de câmera. "
                + ("Retorne JSON com chave 'scene_prompt'." if structured else "Retorne apenas o prompt final em um único parágrafo, sem JSON e sem marcadores.")
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": instruction_text,
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url},
                },
            ],
        },
    ]

    request_kwargs = {
        "model": model_name,
        "temperature": 0.2,
        "max_tokens": 450,
        "messages": messages,
    }
    if structured:
        request_kwargs["response_format"] = {"type": "json_object"}

    resp = await client.chat.completions.create(**request_kwargs)
    content = resp.choices[0].message.content if getattr(resp, "choices", None) else ""
    return _extract_scene_prompt_from_content(content)


async def _analyze_frame_prompt(
    client: openai.AsyncOpenAI,
    frame_path: str,
    start_time: float,
    end_time: float,
    duration_seconds: float,
    global_context: str = "",
    spoken_context: str = "",
) -> str:
    image_data_url = _image_file_to_data_url(frame_path)
    preferred_model = (settings.similar_analysis_model or "gpt-4o").strip() or "gpt-4o"
    attempts: list[tuple[str, bool]] = [
        (preferred_model, True),
        (preferred_model, False),
    ]
    if preferred_model != "gpt-4o":
        attempts.append(("gpt-4o", False))

    for attempt_model, structured in attempts:
        try:
            prompt = await _request_scene_prompt_from_model(
                client,
                attempt_model,
                image_data_url,
                start_time,
                end_time,
                duration_seconds,
                structured=structured,
                global_context=global_context,
                spoken_context=spoken_context,
            )
            if prompt:
                return prompt[:1600]
        except Exception as exc:
            logger.warning(
                "Frame analysis retry activated for model=%s structured=%s: %s",
                attempt_model,
                structured,
                exc,
            )
            if _is_quota_exhausted_error(exc):
                logger.warning(
                    "Frame analysis OpenAI quota exhausted for model=%s; switching to Google fallback",
                    attempt_model,
                )
                break

    try:
        prompt = await _request_scene_prompt_from_google(
            frame_path,
            start_time,
            end_time,
            duration_seconds,
            global_context=global_context,
            spoken_context=spoken_context,
        )
        if prompt:
            logger.info("Frame analysis fallback succeeded with Google model=%s", _SIMILAR_GOOGLE_ANALYSIS_MODEL)
            return prompt[:1600]
    except Exception as exc:
        logger.warning("Frame analysis Google fallback failed: %s", exc)

    return (
        "Cena cinematografica ultra detalhada com composicao fiel ao frame de referencia, "
        "movimento de camera suave, iluminacao natural e continuidade visual consistente."
    )


def _build_temporal_prompt(scenes: list[dict]) -> str:
    chunks: list[str] = []
    for scene in scenes:
        start = float(scene.get("start_time", 0) or 0)
        end = float(scene.get("end_time", start) or start)
        prompt = str(scene.get("prompt") or "").strip()
        if not prompt:
            continue
        chunks.append(f"{start:.1f}s - {end:.1f}s\n{prompt}")
    return "\n\n".join(chunks).strip()


def _normalize_detected_mode(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"static_narrated", "static", "slideshow", "static_images_narration"}:
        return "static_narrated"
    if raw in {"realistic", "live_action", "cinematic", "real"}:
        return "realistic"
    return "unknown"


def _suggest_engine_for_detected_mode(mode: str) -> str:
    normalized = _normalize_detected_mode(mode)
    if normalized == "realistic":
        return "wan2"
    return "grok"


def _heuristic_detect_reference_mode(scene_payloads: list[dict]) -> tuple[str, float, str]:
    prompts = [str(item.get("prompt") or "").lower() for item in scene_payloads]
    combined = "\n".join(prompts)
    if not combined.strip():
        return (
            "unknown",
            0.45,
            "Nao houve texto suficiente para identificar com seguranca o estilo visual.",
        )

    static_terms = (
        "imagem estatica",
        "foto estatica",
        "slideshow",
        "carrossel",
        "colagem",
        "ilustracao",
        "ilustração",
        "anime",
        "render 3d",
        "desenho",
        "arte digital",
        "still frame",
        "sem movimento",
    )
    realistic_terms = (
        "live action",
        "fotorealista",
        "cinematograf",
        "cinematográf",
        "camera handheld",
        "camera tracking",
        "plano sequencia",
        "plano sequência",
        "movimento de camera",
        "movimento de câmera",
        "personagem real",
        "filmagem real",
    )

    static_score = sum(combined.count(term) for term in static_terms)
    realistic_score = sum(combined.count(term) for term in realistic_terms)

    if static_score >= realistic_score + 2:
        confidence = min(0.9, 0.56 + 0.05 * float(static_score - realistic_score))
        return (
            "static_narrated",
            confidence,
            "As cenas indicam composicoes de imagem parada/ilustrada com pouca progressao de acao.",
        )

    if realistic_score >= static_score + 2:
        confidence = min(0.9, 0.56 + 0.05 * float(realistic_score - static_score))
        return (
            "realistic",
            confidence,
            "As cenas mostram progressao de acao e movimento de camera tipicos de fluxo realista.",
        )

    return (
        "unknown",
        0.5,
        "Os sinais visuais ficaram mistos entre composicao estatica e fluxo cinematografico.",
    )


async def _detect_reference_mode(
    client: openai.AsyncOpenAI,
    scene_payloads: list[dict],
) -> dict:
    heuristic_mode, heuristic_confidence, heuristic_reason = _heuristic_detect_reference_mode(scene_payloads)
    if not scene_payloads:
        return {
            "mode": heuristic_mode,
            "confidence": heuristic_confidence,
            "reason": heuristic_reason,
            "suggested_engine": _suggest_engine_for_detected_mode(heuristic_mode),
        }

    model_name = (settings.similar_analysis_model or "gpt-4o").strip() or "gpt-4o"
    samples: list[str] = []
    for item in scene_payloads[:12]:
        start = float(item.get("start_time") or 0)
        end = float(item.get("end_time") or start)
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        samples.append(f"{start:.1f}s-{end:.1f}s: {prompt[:420]}")

    if not samples:
        return {
            "mode": heuristic_mode,
            "confidence": heuristic_confidence,
            "reason": heuristic_reason,
            "suggested_engine": _suggest_engine_for_detected_mode(heuristic_mode),
        }

    try:
        resp = await client.chat.completions.create(
            model=model_name,
            temperature=0,
            max_tokens=220,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classifique o estilo dominante de um video em uma destas classes: "
                        "'static_narrated', 'realistic', 'unknown'. "
                        "Use static_narrated quando parecer video de imagens estaticas/ilustradas com narracao. "
                        "Use realistic quando parecer fluxo continuo cinematografico/live action. "
                        "Retorne JSON com mode, confidence (0-1) e reason (maximo 140 caracteres)."
                    ),
                },
                {
                    "role": "user",
                    "content": "\n\n".join(samples),
                },
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(raw) if raw else {}
        mode = _normalize_detected_mode(parsed.get("mode"))

        confidence_raw = parsed.get("confidence", heuristic_confidence)
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = heuristic_confidence
        confidence = max(0.0, min(1.0, confidence))

        reason = str(parsed.get("reason") or "").strip()
        if not reason:
            reason = heuristic_reason

        return {
            "mode": mode,
            "confidence": confidence,
            "reason": reason[:220],
            "suggested_engine": _suggest_engine_for_detected_mode(mode),
        }
    except Exception as exc:
        logger.warning("Similar mode detection fallback activated: %s", exc)
        return {
            "mode": heuristic_mode,
            "confidence": heuristic_confidence,
            "reason": heuristic_reason,
            "suggested_engine": _suggest_engine_for_detected_mode(heuristic_mode),
        }


def _scene_duration(scene: VideoScene) -> int:
    start = float(scene.start_time or 0)
    end = float(scene.end_time or start)
    raw = max(0.0, end - start)
    floor = 1
    ceil = max(floor, int(settings.similar_scene_max_seconds or 15))
    if raw <= 0:
        return floor
    return max(floor, min(ceil, int(math.ceil(raw))))


def _scene_duration_seconds(scene: VideoScene) -> float:
    start = float(scene.start_time or 0)
    end = float(scene.end_time or start)
    return max(0.1, end - start)


def _normalize_engine(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"grok", "wan2", "minimax", "seedance"}:
        return raw
    if "seedance" in raw:
        return "seedance"
    if "mini" in raw:
        return "minimax"
    if "wan" in raw or "ultra" in raw:
        return "wan2"
    return "grok"


def _engine_duration(engine: str, duration: int) -> int:
    safe = max(1, int(duration or 5))
    if engine == "grok":
        return max(1, min(15, safe))
    if engine == "wan2":
        allowed = (5, 10, 15)
        if safe in allowed:
            return safe
        return min(allowed, key=lambda candidate: (abs(candidate - safe), candidate))
    if engine in {"minimax", "seedance"}:
        return max(5, min(10, safe))
    return max(5, min(15, safe))


def _engine_min_duration(engine: str) -> float:
    normalized_engine = _normalize_engine(engine)
    if normalized_engine == "grok":
        return 1.0
    if normalized_engine in {"wan2", "minimax"}:
        return 5.0
    if normalized_engine == "seedance":
        return 4.0
    return 1.0


def _build_similar_scene_generation_context(
    scene: VideoScene,
    anchor_scene: VideoScene | None = None,
    reference_frames_by_scene_index: dict[str, str] | None = None,
) -> tuple[str, str]:
    current_scene_index = int(scene.scene_index or 0)
    anchor_scene_index = int(anchor_scene.scene_index or 0) if anchor_scene else 0
    prompt = build_similar_scene_continuity_prompt(
        (scene.prompt or "").strip() or "Cena cinematografica detalhada.",
        anchor_prompt=(anchor_scene.prompt or "") if anchor_scene else "",
        current_scene_index=current_scene_index,
        anchor_scene_index=anchor_scene_index,
    )
    prompt = _build_similar_scene_speech_lock(prompt, scene.lyrics_segment)

    reference_image_path = _get_similar_scene_reference_frame_path(scene, reference_frames_by_scene_index)
    if not reference_image_path and anchor_scene and current_scene_index > anchor_scene_index:
        candidate = str(anchor_scene.image_path or "").strip()
        if candidate and os.path.exists(candidate) and int(anchor_scene.id or 0) != int(scene.id or 0):
            reference_image_path = candidate

    return prompt, reference_image_path


async def _render_reference_frame_clip(
    image_path: str,
    output_path: str,
    duration_seconds: float,
    aspect_ratio: str,
) -> str:
    if not image_path or not os.path.exists(image_path):
        raise RuntimeError("Imagem de referencia nao encontrada para microclip")

    clip_duration = max(0.6, float(duration_seconds or 0.6))
    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    elif aspect_ratio == "1:1":
        width, height = 1080, 1080
    else:
        width, height = 1920, 1080

    upscale_factor = 2
    frames = max(int(math.ceil(clip_duration * _SIMILAR_REFERENCE_CLIP_FPS)), 1)
    filter_chain = (
        f"scale={width * upscale_factor}:{height * upscale_factor},"
        f"zoompan=z='1.0+0.035*(on/{frames})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={_SIMILAR_REFERENCE_CLIP_FPS},"
        "format=yuv420p,setsar=1"
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        image_path,
        "-vf",
        filter_chain,
        "-t",
        f"{clip_duration:.3f}",
        "-r",
        str(_SIMILAR_REFERENCE_CLIP_FPS),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        details = (stderr or b"").decode(errors="ignore")[-500:]
        raise RuntimeError(f"Falha ao gerar microclip de referencia: {details}")
    return output_path


async def _trim_clip_duration(input_path: str, duration_seconds: float, output_path: str) -> str:
    target_duration = max(0.6, float(duration_seconds or 0.6))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-t",
        f"{target_duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        details = (stderr or b"").decode(errors="ignore")[-500:]
        raise RuntimeError(f"Falha ao cortar clip curto: {details}")
    return output_path


async def _ensure_scene_image(
    scene: VideoScene,
    aspect_ratio: str,
    target_dir: Path,
    *,
    anchor_scene: VideoScene | None = None,
    reference_frames_by_scene_index: dict[str, str] | None = None,
) -> str:
    if scene.image_path and os.path.exists(scene.image_path):
        return str(scene.image_path)

    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(target_dir / f"similar_scene_{int(scene.scene_index or 0):03d}.png")
    prompt, reference_image_path = _build_similar_scene_generation_context(
        scene,
        anchor_scene,
        reference_frames_by_scene_index,
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        generate_scene_image,
        prompt[:1200],
        aspect_ratio,
        out_path,
        False,
        reference_image_path,
    )

    if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        raise RuntimeError("Falha ao gerar imagem da cena")

    scene.image_path = out_path
    return out_path


async def _generate_clip_for_scene(
    scene: VideoScene,
    *,
    engine: str,
    aspect_ratio: str,
    clip_dir: Path,
    image_dir: Path,
    anchor_scene: VideoScene | None = None,
    reference_frames_by_scene_index: dict[str, str] | None = None,
) -> str:
    normalized_engine = _normalize_engine(engine)
    scene_duration_seconds = _scene_duration_seconds(scene)
    clip_duration = _engine_duration(normalized_engine, _scene_duration(scene))
    prompt, reference_image_path = _build_similar_scene_generation_context(
        scene,
        anchor_scene,
        reference_frames_by_scene_index,
    )

    clip_dir.mkdir(parents=True, exist_ok=True)
    manual_image_path = str(scene.image_path or "").strip()
    if not (manual_image_path and os.path.exists(manual_image_path)):
        manual_image_path = ""
    output_path = str(clip_dir / f"similar_scene_{int(scene.scene_index or 0):03d}.mp4")

    base_reference_image = manual_image_path or reference_image_path
    if normalized_engine == "seedance" and base_reference_image and scene_duration_seconds < _engine_min_duration(normalized_engine):
        seedance_target_duration = min(
            float(_engine_min_duration(normalized_engine)),
            max(3.0, float(scene_duration_seconds or 0.0)),
        )
        tmp_output_path = str(clip_dir / f"similar_scene_{int(scene.scene_index or 0):03d}_seedance_full.mp4")
        await generate_realistic_video(
            prompt=prompt,
            duration=int(_engine_min_duration(normalized_engine)),
            aspect_ratio=aspect_ratio,
            output_path=tmp_output_path,
            resolution="480p",
            generate_audio=True,
            image_path=base_reference_image,
            on_progress=None,
        )
        await _trim_clip_duration(tmp_output_path, seedance_target_duration, output_path)
        try:
            if os.path.exists(tmp_output_path):
                os.remove(tmp_output_path)
        except Exception:
            pass
        clip_duration = seedance_target_duration
    elif base_reference_image and scene_duration_seconds < _engine_min_duration(normalized_engine):
        await _render_reference_frame_clip(
            base_reference_image,
            output_path,
            scene_duration_seconds,
            aspect_ratio,
        )
        clip_duration = max(0.6, scene_duration_seconds)
    else:
        image_path = manual_image_path or reference_image_path
        if not image_path:
            image_path = await _ensure_scene_image(
                scene,
                aspect_ratio,
                image_dir,
                anchor_scene=anchor_scene,
                reference_frames_by_scene_index=reference_frames_by_scene_index,
            )

        if normalized_engine == "grok":
            await generate_video_clip(
                image_path=image_path,
                prompt=prompt,
                output_path=output_path,
                duration=clip_duration,
                aspect_ratio=aspect_ratio,
                on_progress=None,
                reference_mode="",
            )
        elif normalized_engine == "minimax":
            await generate_minimax_video(
                prompt=prompt,
                duration=clip_duration,
                aspect_ratio=aspect_ratio,
                output_path=output_path,
                image_path=image_path,
                on_progress=None,
            )
        elif normalized_engine == "wan2":
            await generate_wan_video(
                prompt=prompt,
                duration=clip_duration,
                aspect_ratio=aspect_ratio,
                output_path=output_path,
                image_path=image_path,
                generate_audio=True,
                on_progress=None,
            )
        else:
            await generate_realistic_video(
                prompt=prompt,
                duration=clip_duration,
                aspect_ratio=aspect_ratio,
                output_path=output_path,
                resolution="480p",
                generate_audio=True,
                image_path=image_path,
                on_progress=None,
            )

    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("Falha ao gerar clip da cena")

    scene.clip_path = output_path
    scene.scene_type = "video_clip"
    scene.end_time = float(scene.start_time or 0) + float(clip_duration)
    return output_path


def _is_similar_project(project: VideoProject) -> bool:
    tags = _safe_tags_dict(project.tags)
    return str(tags.get("type") or "").strip().lower() == "similar"


async def run_similar_reference_analysis(
    project_id: int,
    source_url: str,
    source_upload_path: str = "",
    source_upload_name: str = "",
) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        source_type = "upload" if str(source_upload_path or "").strip() else "url"
        tags = _safe_tags_dict(project.tags)
        tags.update(
            {
                "type": "similar",
                "similar_stage": "downloading_reference",
                "similar_source_url": source_url,
                "similar_source_type": source_type,
                "similar_source_upload_name": source_upload_name,
            }
        )
        project.tags = tags
        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 2
        project.error_message = None
        await db.commit()

        try:
            work_dir = Path(settings.media_dir) / "similar" / str(project_id)
            frames_dir = work_dir / "frames"
            work_dir.mkdir(parents=True, exist_ok=True)
            frames_dir.mkdir(parents=True, exist_ok=True)

            resolved_video_path = str(work_dir / "reference_video.mp4")
            download_task_id = ""
            resolved_source_url = source_url
            resolved_normalized_url = source_url
            reused_project_id = 0
            upload_source_path = Path(str(source_upload_path or "").strip()) if str(source_upload_path or "").strip() else None

            reused_video = None
            if upload_source_path:
                if not upload_source_path.exists() or upload_source_path.stat().st_size <= 0:
                    raise RuntimeError("Video enviado nao foi encontrado para analise")
                shutil.copy2(upload_source_path, resolved_video_path)
                resolved_source_url = ""
                resolved_normalized_url = ""
            else:
                reused_video = await _try_reuse_cached_reference_video(
                    db,
                    project_id=project_id,
                    user_id=int(project.user_id or 0),
                    source_url=source_url,
                    target_output_path=resolved_video_path,
                )

            if reused_video:
                resolved_video_path = str(reused_video.get("output_path") or resolved_video_path)
                download_task_id = str(reused_video.get("task_id") or "")
                resolved_source_url = str(reused_video.get("source_url") or source_url)
                resolved_normalized_url = str(reused_video.get("normalized_url") or source_url)
                reused_project_id = int(reused_video.get("reused_project_id") or 0)
                logger.info(
                    "Similar project %s reused cached reference video from project %s",
                    project_id,
                    reused_project_id,
                )
            else:
                client = BaixaTudoClient(
                    base_url=settings.baixatudo_api_url,
                    api_key=settings.baixatudo_api_key,
                    timeout_seconds=settings.baixatudo_timeout_seconds,
                    poll_interval_seconds=settings.baixatudo_poll_interval_seconds,
                    max_wait_seconds=settings.baixatudo_max_wait_seconds,
                )

                download_result = await client.download_video(
                    source_url=source_url,
                    output_path=resolved_video_path,
                    formato="video_melhor",
                )
                resolved_video_path = str(download_result.output_path)
                download_task_id = str(download_result.task_id)
                resolved_source_url = str(download_result.source_url or source_url)
                resolved_normalized_url = str(download_result.normalized_url or source_url)

            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "analyzing_reference",
                    "similar_download_task_id": download_task_id,
                    "similar_source_url": resolved_source_url,
                    "similar_normalized_url": resolved_normalized_url,
                    "similar_local_video_path": resolved_video_path,
                    "similar_reused_cache": bool(reused_video),
                    "similar_source_type": source_type,
                    "similar_source_upload_name": source_upload_name,
                }
            )
            if reused_project_id > 0:
                tags["similar_reused_from_project_id"] = reused_project_id
            project.tags = tags
            project.progress = 15
            await db.commit()

            duration_seconds = await _ffprobe_duration(resolved_video_path)
            scene_seconds = max(1.0, float(settings.similar_scene_default_seconds or 5))
            detected_cut_times = await _detect_scene_change_timestamps(resolved_video_path)
            scene_ranges = _build_similar_scene_ranges(
                duration_seconds,
                detected_cut_times,
                target_chunk_seconds=scene_seconds,
            )
            scene_count = len(scene_ranges)

            openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            scene_payloads: list[dict] = []
            scene_frame_payloads: list[dict] = []
            reference_frames_by_scene_index: dict[str, str] = {}

            for idx, (start, end) in enumerate(scene_ranges):
                midpoint = min(duration_seconds - 0.05, start + ((end - start) / 2.0))
                if midpoint < 0:
                    midpoint = 0

                frame_path = str(frames_dir / f"frame_{idx:03d}.jpg")
                await _extract_frame(resolved_video_path, midpoint, frame_path)

                scene_frame_payloads.append(
                    {
                        "scene_index": idx,
                        "start_time": start,
                        "end_time": end,
                        "frame_path": frame_path,
                    }
                )
                reference_frames_by_scene_index[str(idx)] = frame_path

            context_summary, transcript_text, transcript_words = await _build_similar_video_context(
                resolved_video_path,
                duration_seconds,
                [payload.get("frame_path", "") for payload in scene_frame_payloads],
            )

            for idx, payload in enumerate(scene_frame_payloads):
                start = float(payload.get("start_time", 0.0) or 0.0)
                end = float(payload.get("end_time", start) or start)
                frame_path = str(payload.get("frame_path") or "").strip()
                spoken_excerpt = _extract_scene_transcript_excerpt(transcript_words, start, end)
                prompt = await _analyze_frame_prompt(
                    client=openai_client,
                    frame_path=frame_path,
                    start_time=start,
                    end_time=end,
                    duration_seconds=duration_seconds,
                    global_context=context_summary,
                    spoken_context=spoken_excerpt,
                )

                scene_payloads.append(
                    {
                        "scene_index": int(payload.get("scene_index", idx) or idx),
                        "start_time": start,
                        "end_time": end,
                        "prompt": prompt,
                        "reference_frame_path": frame_path,
                        "spoken_context": spoken_excerpt,
                    }
                )

                progress = 20 + int(55 * ((idx + 1) / max(scene_count, 1)))
                project.progress = min(80, progress)
                await db.commit()

            if not scene_payloads:
                raise RuntimeError("Nenhuma cena foi extraida do video de referencia")

            detected_profile = await _detect_reference_mode(openai_client, scene_payloads)
            detected_mode = _normalize_detected_mode(detected_profile.get("mode"))
            detected_engine = _normalize_engine(
                str(detected_profile.get("suggested_engine") or _suggest_engine_for_detected_mode(detected_mode))
            )
            try:
                detected_confidence = float(detected_profile.get("confidence", 0.5) or 0.5)
            except Exception:
                detected_confidence = 0.5
            detected_confidence = max(0.0, min(1.0, detected_confidence))
            detected_reason = str(detected_profile.get("reason") or "").strip()[:220]

            await db.execute(delete(VideoScene).where(VideoScene.project_id == project_id))

            for payload in scene_payloads:
                db.add(
                    VideoScene(
                        project_id=project_id,
                        scene_index=int(payload["scene_index"]),
                        scene_type="image",
                        prompt=str(payload["prompt"]),
                        image_path="",
                        clip_path="",
                        start_time=float(payload["start_time"]),
                        end_time=float(payload["end_time"]),
                        lyrics_segment=str(payload.get("spoken_context") or ""),
                        is_user_uploaded=False,
                    )
                )

            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "analysis_ready",
                    "similar_scene_seconds": scene_seconds,
                    "similar_scene_count": len(scene_payloads),
                    "similar_scene_strategy": "shot_detect",
                    "similar_scene_detect_threshold": _SIMILAR_SCENE_DETECT_THRESHOLD,
                    "similar_reference_frames": reference_frames_by_scene_index,
                    "similar_total_duration": duration_seconds,
                    "similar_context_summary": context_summary,
                    "similar_transcript_excerpt": _normalize_similar_context_text(transcript_text, limit=900),
                    "similar_detected_mode": detected_mode,
                    "similar_detected_confidence": detected_confidence,
                    "similar_detected_reason": detected_reason,
                    "similar_engine_suggested": detected_engine,
                }
            )
            tags.setdefault("similar_engine", detected_engine)
            project.tags = tags
            project.track_duration = float(duration_seconds)
            project.lyrics_text = _build_temporal_prompt(scene_payloads)
            project.status = VideoStatus.PENDING
            project.progress = 0
            project.error_message = None
            await db.commit()

        except Exception as exc:
            logger.error("Similar analysis failed for project %s: %s", project_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "analysis_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao analisar o video de referencia")[:1000]
            await db.commit()


async def run_similar_generate_previews(project_id: int, engine: str, aspect_ratio: str) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "generating_previews",
                    "similar_engine": _normalize_engine(engine),
                    "similar_aspect_ratio": aspect_ratio,
                }
            )
            project.tags = tags
            project.status = VideoStatus.GENERATING_CLIPS
            project.progress = 5
            project.error_message = None
            await db.commit()

            result = await db.execute(
                select(VideoScene)
                .where(VideoScene.project_id == project_id)
                .order_by(VideoScene.scene_index.asc())
            )
            scenes = result.scalars().all()
            if not scenes:
                raise RuntimeError("Nenhuma cena encontrada para gerar previews")

            clip_dir = Path(settings.media_dir) / "clips" / str(project_id)
            image_dir = Path(settings.media_dir) / "images" / str(project_id)
            anchor_scene = scenes[0] if scenes else None
            reference_frames_by_scene_index = _extract_similar_reference_frames(tags)

            for idx, scene in enumerate(scenes):
                tags = _safe_tags_dict(project.tags)
                tags.update(
                    {
                        "type": "similar",
                        "similar_stage": "generating_previews",
                        "similar_current_scene_id": int(scene.id or 0),
                        "similar_current_scene_index": idx + 1,
                        "similar_total_scenes": len(scenes),
                    }
                )
                project.tags = tags
                await db.commit()

                await _generate_clip_for_scene(
                    scene,
                    engine=engine,
                    aspect_ratio=aspect_ratio,
                    clip_dir=clip_dir,
                    image_dir=image_dir,
                    anchor_scene=anchor_scene,
                    reference_frames_by_scene_index=reference_frames_by_scene_index,
                )
                project.progress = 10 + int(80 * ((idx + 1) / max(len(scenes), 1)))
                await db.commit()

            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "preview_ready"})
            tags.pop("similar_current_scene_id", None)
            tags.pop("similar_current_scene_index", None)
            tags.pop("similar_total_scenes", None)
            project.tags = tags
            project.status = VideoStatus.PENDING
            project.progress = 0
            await db.commit()

        except Exception as exc:
            logger.error("Similar preview generation failed for project %s: %s", project_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "preview_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao gerar previews das cenas")[:1000]
            await db.commit()


async def run_similar_regenerate_scene(project_id: int, scene_id: int, engine: str, aspect_ratio: str) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            scene = await db.get(VideoScene, scene_id)
            if not scene or scene.project_id != project_id:
                raise RuntimeError("Cena nao encontrada para regeneracao")

            has_existing_clip = bool(str(scene.clip_path or "").strip() and os.path.exists(str(scene.clip_path or "").strip()))
            scene_stage = "regenerating_scene" if has_existing_clip else "generating_scene"

            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": scene_stage,
                    "similar_regenerating_scene_id": scene_id,
                    "similar_current_scene_id": int(scene.id or 0),
                    "similar_current_scene_index": int(scene.scene_index or 0) + 1,
                    "similar_engine": _normalize_engine(engine),
                    "similar_aspect_ratio": aspect_ratio,
                }
            )
            project.tags = tags
            project.status = VideoStatus.GENERATING_CLIPS
            project.progress = 20
            await db.commit()

            clip_dir = Path(settings.media_dir) / "clips" / str(project_id)
            image_dir = Path(settings.media_dir) / "images" / str(project_id)
            reference_frames_by_scene_index = _extract_similar_reference_frames(tags)
            anchor_scene = None
            if int(scene.scene_index or 0) > 0:
                anchor_result = await db.execute(
                    select(VideoScene)
                    .where(VideoScene.project_id == project_id)
                    .order_by(VideoScene.scene_index.asc())
                    .limit(1)
                )
                anchor_candidate = anchor_result.scalars().first()
                if anchor_candidate and int(anchor_candidate.id or 0) != int(scene.id or 0):
                    anchor_scene = anchor_candidate

            await _generate_clip_for_scene(
                scene,
                engine=engine,
                aspect_ratio=aspect_ratio,
                clip_dir=clip_dir,
                image_dir=image_dir,
                anchor_scene=anchor_scene,
                reference_frames_by_scene_index=reference_frames_by_scene_index,
            )

            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "preview_ready"})
            tags.pop("similar_current_scene_id", None)
            tags.pop("similar_current_scene_index", None)
            project.tags = tags
            project.status = VideoStatus.PENDING
            project.progress = 0
            project.error_message = None
            await db.commit()

        except Exception as exc:
            logger.error("Similar scene regeneration failed for project %s scene %s: %s", project_id, scene_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "regenerate_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao regenerar a cena")[:1000]
            await db.commit()


async def run_similar_generate_unified_scene(
    project_id: int,
    engine: str,
    aspect_ratio: str,
    duration_seconds: int,
) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        normalized_engine = _normalize_engine(engine)
        requested_duration = max(5, min(15, int(duration_seconds or 10)))

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            result = await db.execute(
                select(VideoScene)
                .where(VideoScene.project_id == project_id)
                .order_by(VideoScene.scene_index.asc())
            )
            scenes = result.scalars().all()
            if not scenes:
                raise RuntimeError("Projeto nao possui cenas analisadas para gerar a cena unica")

            tags = _safe_tags_dict(project.tags)
            unified_prompt = str(tags.get("similar_unified_prompt") or "").strip()
            if not unified_prompt:
                raise RuntimeError("Gere o prompt unico antes de criar a cena")

            reference_frames_by_scene_index = _extract_similar_reference_frames(tags)
            reference_image_paths = _collect_similar_reference_frame_paths(scenes, reference_frames_by_scene_index)
            if not reference_image_paths:
                raise RuntimeError("Nenhum frame de referencia foi encontrado para a cena unica")

            clip_dir = Path(settings.media_dir) / "clips" / str(project_id)
            image_dir = Path(settings.media_dir) / "images" / str(project_id)
            clip_dir.mkdir(parents=True, exist_ok=True)
            image_dir.mkdir(parents=True, exist_ok=True)

            output_path = str(clip_dir / "similar_unified.mp4")
            merged_reference_path = ""

            tags = _clear_similar_unified_clip_tags(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "generating_unified_scene",
                    "similar_unified_clip_engine": normalized_engine,
                    "similar_unified_clip_duration": requested_duration,
                    "similar_unified_reference_frame_count": len(reference_image_paths),
                }
            )
            project.tags = tags
            project.status = VideoStatus.GENERATING_CLIPS
            project.progress = 8
            project.error_message = None
            await db.commit()

            if normalized_engine == "seedance":
                await generate_realistic_video(
                    prompt=unified_prompt,
                    duration=requested_duration,
                    aspect_ratio=aspect_ratio,
                    output_path=output_path,
                    resolution="480p",
                    generate_audio=True,
                    image_paths=reference_image_paths,
                    image_path=reference_image_paths[0],
                    on_progress=None,
                )
            else:
                merged_reference_path = await _prepare_similar_unified_reference_image(
                    reference_image_paths,
                    unified_prompt,
                    aspect_ratio,
                    str(image_dir / "similar_unified_reference.png"),
                )
                if not merged_reference_path:
                    raise RuntimeError("Nao foi possivel consolidar os frames de referencia")

                if normalized_engine == "grok":
                    await generate_video_clip(
                        image_path=merged_reference_path,
                        prompt=unified_prompt,
                        output_path=output_path,
                        duration=requested_duration,
                        aspect_ratio=aspect_ratio,
                        on_progress=None,
                        reference_mode="",
                    )
                elif normalized_engine == "minimax":
                    await generate_minimax_video(
                        prompt=unified_prompt,
                        duration=requested_duration,
                        aspect_ratio=aspect_ratio,
                        output_path=output_path,
                        image_path=merged_reference_path,
                        on_progress=None,
                    )
                else:
                    await generate_wan_video(
                        prompt=unified_prompt,
                        duration=requested_duration,
                        aspect_ratio=aspect_ratio,
                        output_path=output_path,
                        image_path=merged_reference_path,
                        generate_audio=True,
                        on_progress=None,
                    )

            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                raise RuntimeError("Falha ao gerar a cena unica")

            tags = _clear_similar_unified_clip_tags(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "unified_scene_ready",
                    "similar_unified_clip_path": output_path,
                    "similar_unified_clip_engine": normalized_engine,
                    "similar_unified_clip_duration": requested_duration,
                    "similar_unified_clip_generated_at": datetime.utcnow().isoformat() + "Z",
                    "similar_unified_reference_frame_count": len(reference_image_paths),
                }
            )
            if merged_reference_path and os.path.exists(merged_reference_path):
                tags["similar_unified_reference_image_path"] = merged_reference_path
            project.tags = tags
            project.status = VideoStatus.PENDING
            project.progress = 0
            project.error_message = None
            await db.commit()

        except Exception as exc:
            logger.error(
                "Similar unified scene generation failed for project %s: %s",
                project_id,
                exc,
                exc_info=True,
            )
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _clear_similar_unified_clip_tags(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "unified_scene_failed",
                    "similar_unified_clip_engine": normalized_engine,
                    "similar_unified_clip_duration": requested_duration,
                }
            )
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao gerar a cena unica")[:1000]
            await db.commit()


async def run_similar_merge(project_id: int, aspect_ratio: str, scene_ids: list[int] | None = None) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "merging_scenes", "similar_aspect_ratio": aspect_ratio})
            project.tags = tags
            project.status = VideoStatus.RENDERING
            project.progress = 10
            project.error_message = None
            await db.commit()

            result = await db.execute(
                select(VideoScene)
                .where(VideoScene.project_id == project_id)
                .order_by(VideoScene.scene_index.asc())
            )
            scenes = result.scalars().all()
            if scene_ids:
                selected: set[int] = set()
                for raw_id in scene_ids:
                    try:
                        parsed_id = int(raw_id)
                    except Exception:
                        continue
                    if parsed_id > 0:
                        selected.add(parsed_id)
                scenes = [scene for scene in scenes if int(scene.id or 0) in selected]

            clip_paths = [str(scene.clip_path) for scene in scenes if scene.clip_path and os.path.exists(scene.clip_path)]
            if not clip_paths:
                raise RuntimeError("Nenhum clip pronto para unir")

            render_dir = Path(settings.media_dir) / "renders" / str(project_id)
            render_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(render_dir / f"video_{aspect_ratio.replace(':', 'x')}_similar.mp4")

            use_hard_cuts = str(tags.get("similar_scene_strategy") or "").strip().lower() == "shot_detect"
            await concatenate_clips(clip_paths, output_path, crossfade_dur=0.0 if use_hard_cuts else 0.5)

            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                raise RuntimeError("Falha ao unir os clips")

            duration = float(get_duration(output_path) or 0)
            file_size = int(os.path.getsize(output_path))

            thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = str(thumb_dir / "thumbnail.jpg")
            try:
                generate_thumbnail_from_frame(
                    video_path=output_path,
                    title=project.title or "Video Semelhante",
                    artist="Semelhante",
                    output_path=thumb_path,
                )
            except Exception as thumb_exc:
                logger.warning("Similar merge thumbnail failed for project %s: %s", project_id, thumb_exc)
                thumb_path = ""

            db.add(
                VideoRender(
                    project_id=project_id,
                    format=aspect_ratio,
                    file_path=output_path,
                    file_size=file_size,
                    thumbnail_path=thumb_path,
                    duration=duration,
                )
            )

            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "merged"})
            project.tags = tags
            project.status = VideoStatus.COMPLETED
            project.progress = 100
            project.aspect_ratio = aspect_ratio
            project.track_duration = duration or float(project.track_duration or 0)
            await db.commit()

        except Exception as exc:
            logger.error("Similar merge failed for project %s: %s", project_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "merge_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao unir as cenas")[:1000]
            await db.commit()
