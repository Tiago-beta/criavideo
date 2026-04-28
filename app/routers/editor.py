"""
Editor Router — Endpoints for the video editor (trim, text overlays, subtitles,
filters, music replacement, stickers, quality enhancement, export).
"""
import json
import logging
import math
import os
import shutil
import subprocess
import asyncio
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import VideoProject, VideoRender, VideoStatus
from app.services.tevoxi_music import generate_music_from_theme

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video/editor", tags=["editor"])
settings = get_settings()

# In-memory export jobs
_export_jobs: dict[str, dict] = {}
_EDITOR_EXPORT_PRESET = "veryfast"
_EDITOR_EXPORT_CRF = "23"
_EDITOR_EXPORT_AUDIO_BITRATE = "160k"
_EDITOR_TEVOXI_MOOD_MAP = {
    "calmo": "calmo",
    "calma": "calmo",
    "drama": "drama",
    "dramatico": "drama",
    "dramático": "drama",
    "alegre": "alegre",
    "animado": "alegre",
    "emocional": "drama",
}
_EDITOR_TEVOXI_MOOD_SETTINGS = {
    "calmo": {
        "api_mood": "calmo reflexivo",
        "genre": "ambient",
        "theme_hint": "atmosfera suave, piano leve, texturas calmas, sem percussao agressiva",
    },
    "drama": {
        "api_mood": "dramatico poderoso agressivo",
        "genre": "cinematic trailer",
        "theme_hint": "trilha dramatica agressiva de trailer, tensao crescente, impactos fortes, cordas intensas, sem voz",
    },
    "alegre": {
        "api_mood": "alegre motivacional",
        "genre": "pop",
        "theme_hint": "energia positiva, ritmo animado, clima otimista, sem voz",
    },
}


def _resolve_render_video_path(render: VideoRender) -> str | None:
    """Resolve render.file_path or legacy media URL to a local file path."""
    source = (getattr(render, "file_path", "") or "").strip()
    if not source:
        return None

    if source.startswith("/video/media/"):
        source = os.path.join(settings.media_dir, source.split("/video/media/")[-1].lstrip("/"))
    elif "/video/media/" in source:
        source = os.path.join(settings.media_dir, source.split("/video/media/")[-1].lstrip("/"))
    elif not os.path.isabs(source):
        source = os.path.join(settings.media_dir, source.lstrip("/"))

    return source


def _to_media_url(path: str | None) -> str | None:
    if not path:
        return None
    media_prefix = os.path.normpath(settings.media_dir)
    target = os.path.normpath(path)
    try:
        rel = os.path.relpath(target, media_prefix)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    rel_url = rel.replace("\\", "/").lstrip("/")
    return f"/video/media/{rel_url}"


def _normalize_editor_tevoxi_mood(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "calmo"
    return _EDITOR_TEVOXI_MOOD_MAP.get(raw, raw[:40])


def _fallback_project_video_path(project_id: int) -> str | None:
    """Fallback for old projects when render path is missing/inconsistent."""
    candidates = [
        os.path.join(settings.media_dir, str(project_id), "output.mp4"),
        os.path.join(settings.media_dir, str(project_id), "final.mp4"),
        os.path.join(settings.media_dir, "renders", str(project_id), "realistic_video_final.mp4"),
        os.path.join(settings.media_dir, "renders", str(project_id), "final.mp4"),
        os.path.join(settings.media_dir, "renders", str(project_id), "output.mp4"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _normalize_aspect_ratio(value: str | None) -> str:
    val = (value or "").strip()
    return val if val in {"16:9", "9:16", "1:1"} else ""


def _build_aspect_pad_filter(aspect_ratio: str | None) -> str | None:
    ar = _normalize_aspect_ratio(aspect_ratio)
    if not ar:
        return None
    if ar == "9:16":
        return "pad=w='ceil(max(iw,ih*9/16)/2)*2':h='ceil(max(ih,iw*16/9)/2)*2':x='(ow-iw)/2':y='(oh-ih)/2':color=black"
    if ar == "16:9":
        return "pad=w='ceil(max(iw,ih*16/9)/2)*2':h='ceil(max(ih,iw*9/16)/2)*2':x='(ow-iw)/2':y='(oh-ih)/2':color=black"
    return "pad=w='ceil(max(iw,ih)/2)*2':h='ceil(max(iw,ih)/2)*2':x='(ow-iw)/2':y='(oh-ih)/2':color=black"


def _round_up_even(value: float) -> int:
    iv = int(math.ceil(float(value or 0)))
    if iv <= 0:
        return 0
    return iv if iv % 2 == 0 else iv + 1


def _estimate_padded_canvas_size(width: int, height: int, aspect_ratio: str | None) -> tuple[int, int]:
    """Mirror pad() geometry used in FFmpeg so overlay px sizing matches preview scale."""
    w = max(1, int(width or 0))
    h = max(1, int(height or 0))
    ar = _normalize_aspect_ratio(aspect_ratio)
    if not ar:
        return w, h

    if ar == "9:16":
        out_w = max(float(w), float(h) * 9.0 / 16.0)
        out_h = max(float(h), float(w) * 16.0 / 9.0)
    elif ar == "16:9":
        out_w = max(float(w), float(h) * 16.0 / 9.0)
        out_h = max(float(h), float(w) * 9.0 / 16.0)
    else:  # 1:1
        mx = float(max(w, h))
        out_w = mx
        out_h = mx

    return _round_up_even(out_w), _round_up_even(out_h)


def _probe_video_metadata(video_path: str) -> tuple[float, str]:
    duration = 0.0
    aspect_ratio = "16:9"
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return duration, aspect_ratio

        payload = json.loads(proc.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width > 0 and height > 0:
            ratio = width / height
            if abs(ratio - 1.0) <= 0.12:
                aspect_ratio = "1:1"
            elif ratio < 1:
                aspect_ratio = "9:16"
            else:
                aspect_ratio = "16:9"

        duration = float((payload.get("format") or {}).get("duration") or 0)
    except Exception as exc:
        logger.warning("[editor] Failed to probe metadata for %s: %s", video_path, exc)

    return duration, aspect_ratio


def _probe_media_dimensions(path: str) -> tuple[int, int]:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return 0, 0
        payload = json.loads(proc.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        return width, height
    except Exception:
        return 0, 0


def _probe_has_audio_stream(video_path: str) -> bool:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0 and bool((proc.stdout or "").strip())
    except Exception:
        return False


def _normalize_trim_segments(
    raw_segments: list,
    trim_start: float,
    trim_end: float,
    src_duration: float,
) -> list[tuple[float, float]]:
    segments: list[tuple[float, float]] = []
    max_duration = max(0.0, float(src_duration or 0.0))

    for seg in raw_segments or []:
        if isinstance(seg, dict):
            st = float(seg.get("start") or 0)
            et = float(seg.get("end") or 0)
        else:
            st = float(getattr(seg, "start", 0) or 0)
            et = float(getattr(seg, "end", 0) or 0)

        st = max(0.0, st)
        et = max(st, et)
        if max_duration > 0:
            st = min(st, max_duration)
            et = min(et, max_duration)
        if et - st >= 0.05:
            segments.append((st, et))

    if not segments and trim_end > trim_start:
        st = max(0.0, float(trim_start or 0))
        et = max(st, float(trim_end or 0))
        if max_duration > 0:
            st = min(st, max_duration)
            et = min(et, max_duration)
        if et - st >= 0.05:
            segments.append((st, et))

    if not segments and trim_start > 0 and max_duration > trim_start:
        segments.append((float(trim_start), max_duration))

    if not segments:
        if max_duration > 0:
            segments.append((0.0, max_duration))
        else:
            segments.append((0.0, 1e9))

    segments.sort(key=lambda item: item[0])

    # Merge overlapping or adjacent ranges to avoid duplicated frames.
    merged: list[list[float]] = []
    for st, et in segments:
        if not merged:
            merged.append([st, et])
            continue
        prev = merged[-1]
        if st <= prev[1] + 0.01:
            prev[1] = max(prev[1], et)
        else:
            merged.append([st, et])

    return [(item[0], item[1]) for item in merged if item[1] - item[0] >= 0.05]


def _build_segment_select_expr(segments: list[tuple[float, float]]) -> str:
    parts = [f"between(t\\,{st:.6f}\\,{et:.6f})" for st, et in segments]
    return "+".join(parts)


def _map_source_interval_to_output(
    start_time: float,
    end_time: float,
    segments: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    start = max(0.0, float(start_time or 0.0))
    end = max(start, float(end_time or 0.0))
    if end - start < 0.001:
        return []

    mapped: list[tuple[float, float]] = []
    offset = 0.0
    for seg_start, seg_end in segments:
        overlap_start = max(start, seg_start)
        overlap_end = min(end, seg_end)
        if overlap_end - overlap_start >= 0.02:
            out_start = offset + (overlap_start - seg_start)
            out_end = out_start + (overlap_end - overlap_start)
            mapped.append((out_start, out_end))
        offset += seg_end - seg_start
    return mapped


def _map_source_interval_to_output_detailed(
    start_time: float,
    end_time: float,
    segments: list[tuple[float, float]],
) -> list[tuple[float, float, float, float]]:
    """Map source [start,end] to output timeline preserving overlap source ranges.

    Returns tuples: (output_start, output_end, overlap_start_in_source, overlap_end_in_source).
    """
    start = max(0.0, float(start_time or 0.0))
    end = max(start, float(end_time or 0.0))
    if end - start < 0.001:
        return []

    mapped: list[tuple[float, float, float, float]] = []
    offset = 0.0
    for seg_start, seg_end in segments:
        overlap_start = max(start, seg_start)
        overlap_end = min(end, seg_end)
        if overlap_end - overlap_start >= 0.02:
            out_start = offset + (overlap_start - seg_start)
            out_end = out_start + (overlap_end - overlap_start)
            mapped.append((out_start, out_end, overlap_start, overlap_end))
        offset += seg_end - seg_start

    return mapped


# ── Models ──────────────────────────────────────────────
class TextOverlay(BaseModel):
    content: str
    start_time: float
    end_time: float
    x: float = 50
    y: float = 50
    font_size: int = 36
    color: str = "#ffffff"
    bold: bool = True
    italic: bool = False


class SubtitleEntry(BaseModel):
    text: str
    start_time: float
    end_time: float
    x: float = 50
    y: float = 82
    font_size: int = 28
    font_color: str = "#ffffff"
    bg_color: str = ""
    outline_color: str = "#000000"
    font_family: str = "Arial"
    bold: bool = True
    italic: bool = False


class StickerEntry(BaseModel):
    emoji: str
    x: float = 50
    y: float = 50
    start_time: float = 0
    end_time: float = 5
    size: int = 48


class MediaLayerEntry(BaseModel):
    kind: str = "image"  # image | video
    media_type: Optional[str] = None
    path: str
    x: float = 0
    y: float = 0
    width: float = 100
    volume: float = 100
    audio_only: bool = False
    start_time: float = 0
    end_time: float = 0
    duration: float = 0
    source_offset: float = 0


class TrimSegment(BaseModel):
    start: float
    end: float


class ExportRequest(BaseModel):
    project_id: int
    aspect_ratio: str = ""
    trim_start: float = 0
    trim_end: float = 0
    trim_video_segments: list[TrimSegment] = []
    trim_audio_segments: list[TrimSegment] = []
    trim_segments: list[TrimSegment] = []
    filter: str = "none"
    quality: str = "original"
    original_volume: int = 100
    music_volume: int = 80
    music_path: str = ""
    texts: list[TextOverlay] = []
    subtitles: list[SubtitleEntry] = []
    stickers: list[StickerEntry] = []
    media_layers: list[MediaLayerEntry] = []


class AddLayerVideoFromLibraryRequest(BaseModel):
    project_id: int


class GenerateTevoxiMusicRequest(BaseModel):
    project_id: int
    mood: str = "calmo"
    characteristics: str = ""
    duration_seconds: float = 0


@router.post("/generate-tevoxi-music")
async def generate_tevoxi_music(
    req: GenerateTevoxiMusicRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = int(req.project_id or 0)
    if project_id <= 0:
        raise HTTPException(400, "Projeto inválido")

    result = await db.execute(
        select(VideoProject)
        .where(VideoProject.id == project_id, VideoProject.user_id == user["id"])
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Projeto não encontrado")

    mood = _normalize_editor_tevoxi_mood(req.mood)
    mood_settings = _EDITOR_TEVOXI_MOOD_SETTINGS.get(mood, _EDITOR_TEVOXI_MOOD_SETTINGS["calmo"])
    characteristics = (req.characteristics or "").strip()

    theme_parts: list[str] = []
    project_title = (project.title or project.track_title or "").strip()
    if project_title:
        theme_parts.append(f"Tema do vídeo: {project_title[:120]}")
    theme_parts.append(f"Direção sonora: {mood_settings['theme_hint']}")
    if characteristics:
        theme_parts.append(f"Características desejadas: {characteristics[:220]}")
    if mood == "drama" and not characteristics:
        theme_parts.append("Deixe mais agressivo e épico, com sensação de urgência cinematográfica.")
    theme_parts.append("Trilha instrumental para fundo de narração, sem voz cantada.")
    theme = " | ".join(theme_parts)

    requested_duration = float(req.duration_seconds or 0.0)
    fallback_duration = float(project.track_duration or 0.0)
    target_duration = int(round(requested_duration if requested_duration > 0 else fallback_duration))
    target_duration = max(30, min(240, target_duration or 60))

    try:
        tevoxi_result = await generate_music_from_theme(
            theme=theme,
            project_id=project.id,
            duration=target_duration,
            language="pt-BR",
            manual_settings={
                "music_mode": "instrumental",
                "music_genre": mood_settings["genre"],
                "music_vocalist": "",
                "music_mood": mood_settings["api_mood"],
                "music_duration": target_duration,
                "music_language": "pt-BR",
            },
        )
    except Exception as exc:
        err_text = str(exc or "").strip()
        logger.warning(
            "[editor] Tevoxi music generation failed project_id=%s mood=%s: %s",
            project.id,
            mood,
            err_text or "unknown",
        )
        if "não configurado" in err_text.lower() or "nao configurado" in err_text.lower():
            raise HTTPException(503, "Serviço de música Tevoxi não está configurado no servidor")
        raise HTTPException(502, "Falha ao gerar áudio via Tevoxi. Tente novamente em instantes")

    audio_path = str(tevoxi_result.get("audio_path") or "").strip()
    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(500, "Tevoxi retornou sem arquivo de áudio válido")

    media_url = _to_media_url(audio_path)
    if not media_url:
        raise HTTPException(500, "Falha ao mapear mídia de áudio gerada")

    generated_duration = float(tevoxi_result.get("duration") or target_duration)
    return {
        "path": audio_path,
        "media_url": media_url,
        "title": str(tevoxi_result.get("title") or "Áudio IA Tevoxi"),
        "duration": generated_duration,
        "mood": mood,
        "source": "tevoxi",
    }


# ── Upload music ──────────────────────────────────────
@router.post("/upload-music")
async def upload_music(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    if not file.content_type or not file.content_type.startswith("audio"):
        raise HTTPException(400, "Arquivo deve ser de áudio")
    upload_dir = Path(settings.media_dir) / "editor_uploads" / str(user["id"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "audio.mp3").suffix or ".mp3"
    filename = f"music_{uuid.uuid4().hex[:8]}{ext}"
    dest = upload_dir / filename
    with open(dest, "wb") as f:
        content = await file.read()
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(400, "Arquivo muito grande (max 50MB)")
        f.write(content)
    return {
        "path": str(dest),
        "media_url": _to_media_url(str(dest)),
    }


@router.post("/upload-video-audio")
async def upload_video_audio(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    allowed_video_exts = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
    file_ext = Path(file.filename or "").suffix.lower()
    has_video_mime = bool(file.content_type and file.content_type.startswith("video"))
    if not has_video_mime and file_ext not in allowed_video_exts:
        raise HTTPException(400, "Arquivo deve ser de vídeo")

    upload_dir = Path(settings.media_dir) / "editor_uploads" / str(user["id"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    src_ext = file_ext or ".mp4"
    src_video = upload_dir / f"music_src_{uuid.uuid4().hex[:8]}{src_ext}"
    out_audio = upload_dir / f"music_from_video_{uuid.uuid4().hex[:8]}.m4a"

    max_size = 500 * 1024 * 1024  # 500MB
    written = 0
    with open(src_video, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_size:
                out.close()
                src_video.unlink(missing_ok=True)
                raise HTTPException(400, "Arquivo muito grande (max 500MB)")
            out.write(chunk)

    if written <= 0:
        src_video.unlink(missing_ok=True)
        raise HTTPException(400, "Arquivo vazio")

    if not _probe_has_audio_stream(str(src_video)):
        src_video.unlink(missing_ok=True)
        raise HTTPException(400, "Este vídeo não possui faixa de áudio")

    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(src_video),
                "-map", "0:a:0",
                "-vn",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(out_audio),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.error("[editor] Failed to extract audio from uploaded video: %s", (proc.stderr or "")[-1200:])
            out_audio.unlink(missing_ok=True)
            raise HTTPException(500, "Falha ao extrair áudio do vídeo")

        if not out_audio.exists() or out_audio.stat().st_size <= 0:
            out_audio.unlink(missing_ok=True)
            raise HTTPException(500, "Falha ao extrair áudio do vídeo")

        return {
            "path": str(out_audio),
            "media_url": _to_media_url(str(out_audio)),
        }
    except subprocess.TimeoutExpired:
        out_audio.unlink(missing_ok=True)
        raise HTTPException(500, "Timeout ao extrair áudio do vídeo")
    finally:
        src_video.unlink(missing_ok=True)


@router.post("/upload-video")
async def upload_video(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.content_type or not file.content_type.startswith("video"):
        raise HTTPException(400, "Arquivo deve ser de vídeo")

    upload_dir = Path(settings.media_dir) / "editor_uploads" / str(user["id"]) / "videos"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    filename = f"video_{uuid.uuid4().hex[:10]}{ext}"
    dest = upload_dir / filename

    max_size = 500 * 1024 * 1024  # 500MB
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_size:
                out.close()
                if dest.exists():
                    dest.unlink(missing_ok=True)
                raise HTTPException(400, "Arquivo muito grande (max 500MB)")
            out.write(chunk)

    if written <= 0:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise HTTPException(400, "Arquivo vazio")

    duration, detected_aspect = _probe_video_metadata(str(dest))
    title = (Path(file.filename or "Vídeo enviado").stem or "Vídeo enviado").strip()[:500]
    if not title:
        title = "Vídeo enviado"

    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=title,
        description="Vídeo enviado para edição",
        aspect_ratio=detected_aspect or "16:9",
        status=VideoStatus.COMPLETED,
        progress=100,
        use_custom_video=True,
        track_duration=duration if duration > 0 else None,
    )
    db.add(project)
    await db.flush()

    render = VideoRender(
        project_id=project.id,
        format=project.aspect_ratio,
        file_path=str(dest),
        file_size=written,
        duration=duration if duration > 0 else None,
    )
    db.add(render)
    await db.commit()

    video_url = _to_media_url(str(dest))
    return {
        "project_id": project.id,
        "video_url": video_url,
        "duration": duration,
        "aspect_ratio": project.aspect_ratio,
    }


@router.post("/upload-layer-video")
async def upload_layer_video(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    if not file.content_type or not file.content_type.startswith("video"):
        raise HTTPException(400, "Arquivo deve ser de vídeo")

    upload_dir = Path(settings.media_dir) / "editor_uploads" / str(user["id"]) / "layers" / "videos"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "layer.mp4").suffix.lower() or ".mp4"
    filename = f"layer_video_{uuid.uuid4().hex[:10]}{ext}"
    dest = upload_dir / filename

    max_size = 500 * 1024 * 1024
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_size:
                out.close()
                if dest.exists():
                    dest.unlink(missing_ok=True)
                raise HTTPException(400, "Arquivo muito grande (max 500MB)")
            out.write(chunk)

    if written <= 0:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise HTTPException(400, "Arquivo vazio")

    duration, _ = _probe_video_metadata(str(dest))
    width, height = _probe_media_dimensions(str(dest))
    return {
        "path": str(dest),
        "media_url": _to_media_url(str(dest)),
        "duration": duration,
        "width": width,
        "height": height,
        "name": Path(file.filename or "Camada vídeo").stem,
    }


@router.post("/add-layer-video-from-library")
async def add_layer_video_from_library(
    req: AddLayerVideoFromLibraryRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VideoProject)
        .options(selectinload(VideoProject.renders))
        .where(VideoProject.id == req.project_id, VideoProject.user_id == user["id"])
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Vídeo não encontrado na sua biblioteca")

    ordered_renders = sorted(
        list(project.renders or []),
        key=lambda render: (render.id or 0),
        reverse=True,
    )
    latest_render = next((render for render in ordered_renders if render.file_path), None)
    if not latest_render:
        raise HTTPException(400, "Este projeto não possui render disponível")

    src_video = _resolve_render_video_path(latest_render)
    if not src_video or not os.path.exists(src_video):
        src_video = _fallback_project_video_path(project.id)
    if not src_video or not os.path.exists(src_video):
        raise HTTPException(400, "Arquivo do vídeo original não foi encontrado")

    max_size = 500 * 1024 * 1024
    src_size = os.path.getsize(src_video)
    if src_size <= 0:
        raise HTTPException(400, "Arquivo de vídeo da biblioteca está vazio")
    if src_size > max_size:
        raise HTTPException(400, "Vídeo da biblioteca muito grande (max 500MB)")

    upload_dir = Path(settings.media_dir) / "editor_uploads" / str(user["id"]) / "layers" / "videos"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(src_video).suffix.lower() or ".mp4"
    filename = f"layer_video_lib_{project.id}_{uuid.uuid4().hex[:10]}{ext}"
    dest = upload_dir / filename
    shutil.copy2(src_video, dest)

    duration, _ = _probe_video_metadata(str(dest))
    width, height = _probe_media_dimensions(str(dest))
    title = (project.title or project.track_title or f"Projeto {project.id}").strip()

    return {
        "path": str(dest),
        "media_url": _to_media_url(str(dest)),
        "duration": duration,
        "width": width,
        "height": height,
        "name": title[:120] if title else "Vídeo da biblioteca",
    }


@router.post("/upload-layer-image")
async def upload_layer_image(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(400, "Formato inválido. Envie JPG, PNG ou WebP.")

    upload_dir = Path(settings.media_dir) / "editor_uploads" / str(user["id"]) / "layers" / "images"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    ext = ext_map.get(file.content_type, Path(file.filename or "layer.jpg").suffix.lower() or ".jpg")
    filename = f"layer_image_{uuid.uuid4().hex[:10]}{ext}"
    dest = upload_dir / filename

    content = await file.read()
    if not content:
        raise HTTPException(400, "Arquivo vazio")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Imagem muito grande (max 10MB)")

    with open(dest, "wb") as out:
        out.write(content)

    width, height = _probe_media_dimensions(str(dest))
    return {
        "path": str(dest),
        "media_url": _to_media_url(str(dest)),
        "width": width,
        "height": height,
        "name": Path(file.filename or "Camada imagem").stem,
    }


# ── Transcribe audio for subtitles ───────────────────
@router.post("/transcribe/{project_id}")
async def transcribe_video(
    project_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Extract audio from video and transcribe using Whisper for auto-subtitles."""
    result = await db.execute(
        select(VideoProject)
        .options(selectinload(VideoProject.renders))
        .where(VideoProject.id == project_id, VideoProject.user_id == user["id"])
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Projeto não encontrado")
    render = next((r for r in sorted(project.renders, key=lambda rr: rr.id or 0, reverse=True) if r.file_path), None)
    if not render:
        raise HTTPException(400, "Nenhum vídeo disponível")

    src_video = _resolve_render_video_path(render)
    if not src_video or not os.path.exists(src_video):
        src_video = _fallback_project_video_path(project.id)
    if not src_video:
        raise HTTPException(400, "Arquivo de vídeo não encontrado")

    # Extract audio to temp WAV
    tmp_audio_dir = os.path.join(settings.media_dir, "tmp", "transcribe", str(project.id))
    os.makedirs(tmp_audio_dir, exist_ok=True)
    tmp_audio = os.path.join(tmp_audio_dir, f"_transcribe_{uuid.uuid4().hex[:6]}.wav")
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_video,
                "-map", "0:a:0", "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1", tmp_audio,
            ],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            stderr_text = (proc.stderr or b"").decode(errors="ignore")
            logger.error(
                "[editor] Transcribe audio extraction failed project_id=%s src=%s stderr=%s",
                project.id,
                src_video,
                stderr_text[-1200:],
            )
            if "Stream map '0:a:0'" in stderr_text or "matches no streams" in stderr_text:
                raise HTTPException(400, "Este vídeo não possui faixa de áudio para transcrição")
            raise HTTPException(500, "Falha ao extrair áudio do vídeo")
    except subprocess.TimeoutExpired:
        logger.error("[editor] Transcribe audio extraction timeout project_id=%s src=%s", project.id, src_video)
        raise HTTPException(500, "Timeout ao extrair áudio do vídeo")
    try:

        # Transcribe (sync function, run in thread pool)
        from app.services.transcriber import transcribe_audio
        import asyncio
        result = await asyncio.to_thread(transcribe_audio, tmp_audio, "pt")
        return {"text": result.get("text", ""), "words": result.get("words", [])}
    finally:
        if os.path.exists(tmp_audio):
            os.remove(tmp_audio)


# ── Export (start background job) ──────────────────────
@router.post("/export")
async def start_export(
    req: ExportRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate project
    result = await db.execute(
        select(VideoProject)
        .options(selectinload(VideoProject.renders))
        .where(VideoProject.id == req.project_id, VideoProject.user_id == user["id"])
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Projeto não encontrado")
    render = next((r for r in sorted(project.renders, key=lambda rr: rr.id or 0, reverse=True) if r.file_path), None)
    if not render:
        raise HTTPException(400, "Nenhum vídeo disponível para editar")

    job_id = uuid.uuid4().hex[:12]
    _export_jobs[job_id] = {
        "status": "processing",
        "progress": 0,
        "message": "Iniciando exportacao...",
        "error": None,
        "output_url": None,
    }

    main_loop = asyncio.get_running_loop()
    background_tasks.add_task(
        _run_export, job_id, project, render, req, user["id"], main_loop
    )
    return {"job_id": job_id}


# ── Export status polling ──────────────────────────────
@router.get("/export/{job_id}/status")
async def export_status(job_id: str, user=Depends(get_current_user)):
    job = _export_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return job


# ── Background export function ─────────────────────────
def _run_export(job_id: str, project, render, req: ExportRequest, user_id: int, main_loop: asyncio.AbstractEventLoop):
    try:
        job = _export_jobs[job_id]
        job["progress"] = 5
        job["message"] = "Preparando arquivos..."

        # Resolve source video path
        src_video = _resolve_render_video_path(render)

        if not src_video or not os.path.exists(src_video):
            src_video = _fallback_project_video_path(project.id)
            if not src_video:
                job["status"] = "failed"
                job["error"] = "Arquivo de vídeo não encontrado no servidor"
                return

        # Output directory
        out_dir = os.path.join(settings.media_dir, str(project.id), "edited")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"edited_{uuid.uuid4().hex[:8]}.mp4")

        job["progress"] = 10
        job["message"] = "Construindo filtros FFmpeg..."

        src_duration, _ = _probe_video_metadata(src_video)
        source_has_audio = _probe_has_audio_stream(src_video)
        video_segments = _normalize_trim_segments(
            req.trim_video_segments or req.trim_segments,
            req.trim_start,
            req.trim_end,
            src_duration,
        )
        audio_segments = _normalize_trim_segments(
            req.trim_audio_segments or req.trim_segments,
            req.trim_start,
            req.trim_end,
            src_duration,
        )

        use_video_segment_filter = not (
            len(video_segments) == 1
            and video_segments[0][0] <= 0.01
            and (src_duration <= 0 or video_segments[0][1] >= max(0.0, src_duration - 0.01))
        )
        use_audio_segment_filter = not (
            len(audio_segments) == 1
            and audio_segments[0][0] <= 0.01
            and (src_duration <= 0 or audio_segments[0][1] >= max(0.0, src_duration - 0.01))
        )

        video_select_expr = _build_segment_select_expr(video_segments)
        audio_select_expr = _build_segment_select_expr(audio_segments)
        output_video_duration = sum(max(0.0, et - st) for st, et in video_segments)

        valid_media_layers: list[dict] = []
        allowed_layer_root = os.path.normpath(
            str(Path(settings.media_dir) / "editor_uploads" / str(user_id) / "layers")
        )
        for layer_idx, layer in enumerate(req.media_layers or []):
            kind = str(getattr(layer, "kind", "") or getattr(layer, "media_type", "") or "").strip().lower()
            if kind not in {"image", "video"}:
                continue

            raw_path = str(getattr(layer, "path", "") or "").strip()
            if not raw_path:
                continue

            resolved_path = raw_path
            if raw_path.startswith("/video/media/"):
                resolved_path = os.path.join(settings.media_dir, raw_path.split("/video/media/")[-1].lstrip("/"))
            elif "/video/media/" in raw_path:
                resolved_path = os.path.join(settings.media_dir, raw_path.split("/video/media/")[-1].lstrip("/"))
            elif not os.path.isabs(raw_path):
                resolved_path = os.path.join(settings.media_dir, raw_path.lstrip("/"))
            resolved_path = os.path.normpath(resolved_path)

            if not os.path.exists(resolved_path):
                continue
            if not resolved_path.startswith(allowed_layer_root):
                logger.warning("[editor] Ignoring layer outside user scope: %s", resolved_path)
                continue

            width_pct = max(8.0, min(100.0, float(getattr(layer, "width", 100) or 100)))
            x_pct = max(0.0, min(100.0, float(getattr(layer, "x", 0) or 0)))
            y_pct = max(0.0, min(100.0, float(getattr(layer, "y", 0) or 0)))
            volume_pct = max(0.0, min(200.0, float(getattr(layer, "volume", 100) or 100)))
            audio_only = bool(getattr(layer, "audio_only", False))
            start_time = max(0.0, float(getattr(layer, "start_time", 0) or 0))
            end_time = max(start_time, float(getattr(layer, "end_time", 0) or 0))
            source_offset = max(0.0, float(getattr(layer, "source_offset", 0) or 0))

            layer_duration = 0.0
            available_video_duration = 0.0
            if kind == "video":
                layer_duration, _ = _probe_video_metadata(resolved_path)
                if layer_duration > 0:
                    source_offset = min(source_offset, max(0.0, layer_duration - 0.05))
                    available_video_duration = max(0.0, layer_duration - source_offset)

            if end_time <= start_time + 0.02:
                if kind == "video" and available_video_duration > 0:
                    end_time = start_time + available_video_duration
                else:
                    end_time = start_time + 0.1

            if kind == "video" and available_video_duration > 0:
                end_time = min(end_time, start_time + available_video_duration)
            if end_time <= start_time + 0.02:
                continue

            mapped_ranges = _map_source_interval_to_output_detailed(start_time, end_time, video_segments)
            candidate_ranges: list[tuple[float, float, float]] = []
            mapped_duration = 0.0
            for mapped_start, mapped_end, overlap_start, _overlap_end in mapped_ranges:
                clip_duration = max(0.0, mapped_end - mapped_start)
                if clip_duration <= 0.02:
                    continue
                mapped_source_offset = source_offset + max(0.0, overlap_start - start_time)
                candidate_ranges.append((mapped_start, mapped_end, mapped_source_offset))
                mapped_duration += clip_duration

            # Layers can live in the extended timeline even when there is no base-video overlap.
            if not candidate_ranges:
                if start_time >= max(0.0, output_video_duration - 0.02):
                    candidate_ranges.append((start_time, end_time, source_offset))
                else:
                    continue
            elif end_time > output_video_duration + 0.02:
                tail_start = max(start_time, output_video_duration)
                if end_time > tail_start + 0.02:
                    candidate_ranges.append((tail_start, end_time, source_offset + mapped_duration))

            for range_idx, (mapped_start, mapped_end, mapped_source_offset) in enumerate(candidate_ranges):
                clip_duration = max(0.0, mapped_end - mapped_start)
                if clip_duration <= 0.02:
                    continue

                if kind == "video" and layer_duration > 0:
                    mapped_source_offset = min(mapped_source_offset, max(0.0, layer_duration - 0.05))
                    max_clip_duration = max(0.0, layer_duration - mapped_source_offset)
                    if max_clip_duration <= 0.02:
                        continue
                    clip_duration = min(clip_duration, max_clip_duration)

                mapped_end = mapped_start + clip_duration
                if mapped_end <= mapped_start + 0.02:
                    continue

                valid_media_layers.append(
                    {
                        "kind": kind,
                        "path": resolved_path,
                        "width_pct": width_pct,
                        "x_pct": x_pct,
                        "y_pct": y_pct,
                        "volume_pct": volume_pct,
                        "audio_only": audio_only,
                        "start_time": mapped_start,
                        "end_time": mapped_end,
                        "duration": max(0.0, layer_duration),
                        "source_offset": mapped_source_offset,
                        "layer_ref": f"{layer_idx}:{range_idx}",
                    }
                )

        layer_timeline_end = max(
            (float(layer.get("end_time", 0.0) or 0.0) for layer in valid_media_layers),
            default=0.0,
        )
        final_output_duration = max(output_video_duration, layer_timeline_end)
        if final_output_duration <= 0 and src_duration > 0:
            final_output_duration = src_duration
        final_output_duration = max(0.1, final_output_duration)

        logger.info(
            "[editor] Export video_segments=%s audio_segments=%s use_vf=%s use_af=%s base_dur=%.3f final_dur=%.3f",
            video_segments,
            audio_segments,
            use_video_segment_filter,
            use_audio_segment_filter,
            output_video_duration,
            final_output_duration,
        )

        # Build FFmpeg command
        cmd = ["ffmpeg", "-y"]
        cmd += ["-i", src_video]

        # Music input
        has_music = bool(req.music_path) and os.path.exists(req.music_path)
        if has_music:
            cmd += ["-i", req.music_path]

        # Build video filter chain
        vfilters = []
        selected_aspect = _normalize_aspect_ratio(req.aspect_ratio) or _normalize_aspect_ratio(render.format) or "16:9"
        src_width, src_height = _probe_media_dimensions(src_video)
        _, overlay_canvas_height = _estimate_padded_canvas_size(src_width, src_height, selected_aspect)
        overlay_scale = (overlay_canvas_height / 720.0) if overlay_canvas_height > 0 else 1.0

        if use_video_segment_filter:
            vfilters.append(f"select='{video_select_expr}',setpts=N/FRAME_RATE/TB")

        # CSS-like filter mapping for FFmpeg
        filter_map = {
            "grayscale": "colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3",
            "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
            "warm": "eq=saturation=1.3:brightness=0.05,hue=h=-10",
            "cool": "eq=saturation=0.9:brightness=0.05,hue=h=15",
            "vintage": "curves=vintage,eq=contrast=1.1:brightness=-0.05",
            "vivid": "eq=saturation=1.6:contrast=1.1",
            "dramatic": "eq=contrast=1.4:brightness=-0.1:saturation=0.8",
            "fade": "eq=brightness=0.1:saturation=0.7:contrast=0.9",
            "noir": "colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3,eq=contrast=1.3:brightness=-0.15",
            "cinematic": "eq=contrast=1.15:saturation=1.1:brightness=-0.05,curves=vintage",
            "retro": "curves=vintage,hue=h=-15,eq=saturation=1.2",
        }
        if req.filter != "none" and req.filter in filter_map:
            vfilters.append(filter_map[req.filter])

        # Apply output canvas aspect ratio before overlays so positions match preview
        aspect_filter = _build_aspect_pad_filter(selected_aspect)
        if aspect_filter:
            vfilters.append(aspect_filter)

        # Text overlays using drawtext
        for txt in req.texts:
            mapped_ranges = _map_source_interval_to_output(txt.start_time, txt.end_time, video_segments)
            for st, et in mapped_ranges:
                base_fontsize = max(8, int(txt.font_size or 36))
                fontsize_px = max(8, int(round(base_fontsize * overlay_scale)))
                color = txt.color.lstrip("#")
                x_expr = f"(w*{txt.x/100})"
                y_expr = f"(h*{txt.y/100})"
                escaped_text = txt.content.replace("'", "'\\\\\\''").replace(":", "\\:")
                dt = f"drawtext=text='{escaped_text}':fontsize={fontsize_px}:fontcolor=0x{color}:x={x_expr}-tw/2:y={y_expr}-th/2:enable='between(t,{st},{et})':shadowcolor=black:shadowx=2:shadowy=2"
                vfilters.append(dt)

        # Subtitle overlays
        for sub in req.subtitles:
            mapped_ranges = _map_source_interval_to_output(sub.start_time, sub.end_time, video_segments)
            for st, et in mapped_ranges:
                color = sub.font_color.lstrip("#") if sub.font_color else "FFFFFF"
                base_fontsize = max(8, int(sub.font_size or 28))
                fontsize_px = max(8, int(round(base_fontsize * overlay_scale)))
                outline_width_px = max(2, int(round(fontsize_px * 0.08)))
                box_padding_px = max(1, int(round(8 * overlay_scale)))
                x_expr = f"(w*{sub.x/100})-tw/2" if sub.x else "(w-tw)/2"
                y_expr = f"(h*{sub.y/100})-th/2" if sub.y else "h-80"
                escaped_text = sub.text.replace("'", "'\\\\\\\\''").replace(":", "\\:")
                font_family = (sub.font_family or "Arial").split(",")[0].strip()
                dt_parts = [
                    f"drawtext=text='{escaped_text}'",
                    f"fontsize={fontsize_px}",
                    f"fontcolor=0x{color}",
                    f"x={x_expr}",
                    f"y={y_expr}",
                    f"enable='between(t,{st},{et})'",
                    "shadowcolor=black",
                    "shadowx=2",
                    "shadowy=2",
                ]
                if sub.bg_color:
                    bg = sub.bg_color.lstrip("#")[:6] if sub.bg_color.startswith("#") else "000000"
                    dt_parts.append(f"box=1:boxcolor=0x{bg}@0.6:boxborderw={box_padding_px}")
                if sub.outline_color:
                    border_c = sub.outline_color.lstrip("#")[:6]
                    dt_parts.append(f"borderw={outline_width_px}:bordercolor=0x{border_c}")
                dt = ":".join(dt_parts)
                vfilters.append(dt)

        # Sticker/emoji overlays (drawtext with emoji font)
        for stk in req.stickers:
            mapped_ranges = _map_source_interval_to_output(stk.start_time, stk.end_time, video_segments)
            for st, et in mapped_ranges:
                x_expr = f"(w*{stk.x/100})"
                y_expr = f"(h*{stk.y/100})"
                escaped = stk.emoji.replace("'", "'\\\\\\''").replace(":", "\\:")
                dt = f"drawtext=text='{escaped}':fontsize={stk.size}:x={x_expr}-tw/2:y={y_expr}-th/2:enable='between(t,{st},{et})'"
                vfilters.append(dt)

        # Quality scaling
        if req.quality == "hd":
            vfilters.append("scale=-2:720")
        elif req.quality == "fullhd":
            vfilters.append("scale=-2:1080")
        elif req.quality == "enhance":
            vfilters.append("unsharp=5:5:0.8:5:5:0.4")

        if final_output_duration > output_video_duration + 0.02:
            extra_tail = max(0.0, final_output_duration - output_video_duration)
            vfilters.append(f"tpad=stop_mode=clone:stop_duration={extra_tail:.6f}")

        job["progress"] = 20
        job["message"] = "Renderizando vídeo..."

        video_filter_chain = ",".join(vfilters)

        # Audio handling
        if has_music:
            orig_vol = req.original_volume / 100
            music_vol = req.music_volume / 100
            filter_complex_parts: list[str] = []
            video_map = "0:v"

            # Keep video filtering inside filter_complex when mixing audio so the
            # mapped video stream always comes from the edited (trimmed) output.
            if video_filter_chain:
                filter_complex_parts.append(f"[0:v]{video_filter_chain}[vout]")
                video_map = "[vout]"

            if source_has_audio:
                base_audio_label = "[0:a]"
                if use_audio_segment_filter:
                    filter_complex_parts.append(f"[0:a]aselect='{audio_select_expr}',asetpts=N/SR/TB[a_src]")
                    base_audio_label = "[a_src]"
                filter_complex_parts.append(f"{base_audio_label}volume={orig_vol}[a0]")
                filter_complex_parts.append(f"[1:a]volume={music_vol}[a1]")
                filter_complex_parts.append("[a0][a1]amix=inputs=2:duration=longest[a_mix]")
                if final_output_duration > 0:
                    filter_complex_parts.append(f"[a_mix]atrim=0:{final_output_duration:.6f}[aout]")
                else:
                    filter_complex_parts.append("[a_mix]anull[aout]")
                cmd += [
                    "-filter_complex", ";".join(filter_complex_parts),
                    "-map", video_map,
                    "-map", "[aout]",
                ]
            else:
                if final_output_duration > 0:
                    filter_complex_parts.append(f"[1:a]volume={music_vol}[a1]")
                    filter_complex_parts.append(f"[a1]atrim=0:{final_output_duration:.6f}[aout]")
                else:
                    filter_complex_parts.append(f"[1:a]volume={music_vol}[aout]")
                cmd += [
                    "-filter_complex", ";".join(filter_complex_parts),
                    "-map", video_map,
                    "-map", "[aout]",
                ]
        else:
            # Simple path: no external music mixing required.
            if video_filter_chain:
                cmd += ["-vf", video_filter_chain]

            orig_vol = req.original_volume / 100
            afilters: list[str] = []
            if source_has_audio and use_audio_segment_filter:
                afilters.append(f"aselect='{audio_select_expr}',asetpts=N/SR/TB")
            if source_has_audio and orig_vol != 1.0:
                afilters.append(f"volume={orig_vol}")
            if source_has_audio and final_output_duration > 0:
                afilters.append(f"atrim=0:{final_output_duration:.6f}")
            if afilters:
                cmd += ["-af", ",".join(afilters)]

        # Output settings

        cmd += [
            "-c:v", "libx264",
            "-preset", _EDITOR_EXPORT_PRESET,
            "-crf", _EDITOR_EXPORT_CRF,
            "-c:a", "aac",
            "-b:a", _EDITOR_EXPORT_AUDIO_BITRATE,
            "-movflags", "+faststart",
            out_file,
        ]

        job["progress"] = 30
        job["message"] = "Processando..."

        logger.info(f"[editor] Export cmd: {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Wait for completion with progress simulation
        import time
        progress = 30
        while proc.poll() is None:
            time.sleep(2)
            progress = min(progress + 5, 90)
            job["progress"] = progress
            job["message"] = "Processando..."

        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            err_text = stderr.decode(errors="ignore")
            logger.error("[editor] FFmpeg failed: %s", err_text[-1800:])
            job["status"] = "failed"
            job["error"] = "FFmpeg falhou ao processar o vídeo"
            return

        final_out_file = out_file
        if valid_media_layers:
            job["progress"] = 92
            job["message"] = "Compondo camadas adicionais..."

            layered_out_file = os.path.join(out_dir, f"edited_layers_{uuid.uuid4().hex[:8]}.mp4")
            layer_cmd = ["ffmpeg", "-y", "-i", out_file]
            for idx, layer in enumerate(valid_media_layers, start=1):
                layer["input_idx"] = idx
                if layer["kind"] == "image":
                    layer_cmd += ["-loop", "1", "-i", layer["path"]]
                else:
                    layer_cmd += ["-i", layer["path"]]

            visual_layers = [
                layer
                for layer in valid_media_layers
                if layer["kind"] == "image" or (layer["kind"] == "video" and not layer.get("audio_only"))
            ]
            audio_layers = [layer for layer in valid_media_layers if layer["kind"] == "video"]

            overlay_parts: list[str] = []
            current_video_label = "[0:v]"

            # First uploaded layer stays on top, so overlays are applied from last to first.
            for step, layer in enumerate(reversed(visual_layers)):
                src_label = f"l{step}_src"
                lay_label = f"l{step}"
                ref_label = f"vref{step}"
                out_label = f"vout{step}"

                if layer["kind"] == "video":
                    clip_duration = max(0.02, float(layer["end_time"]) - float(layer["start_time"]))
                    source_offset = max(0.0, float(layer.get("source_offset", 0.0) or 0.0))
                    overlay_parts.append(
                        f"[{layer['input_idx']}:v]"
                        f"trim=start={source_offset:.6f}:duration={clip_duration:.6f},"
                        f"setpts=PTS-STARTPTS+{layer['start_time']:.6f}/TB"
                        f"[{src_label}]"
                    )
                else:
                    overlay_parts.append(f"[{layer['input_idx']}:v]setpts=PTS-STARTPTS[{src_label}]")
                overlay_parts.append(
                    f"[{src_label}]{current_video_label}"
                    f"scale2ref=w='trunc(main_w*{layer['width_pct']/100.0:.6f}/2)*2':h='-2'"
                    f"[{lay_label}][{ref_label}]"
                )

                overlay_expr = (
                    f"[{ref_label}][{lay_label}]overlay="
                    f"x='(W-w)*{layer['x_pct']/100.0:.6f}':"
                    f"y='(H-h)*{layer['y_pct']/100.0:.6f}':"
                    f"enable='between(t,{layer['start_time']:.6f},{layer['end_time']:.6f})':"
                    "eof_action=pass"
                )
                overlay_expr += f"[{out_label}]"
                overlay_parts.append(overlay_expr)
                current_video_label = f"[{out_label}]"

            filter_parts = list(overlay_parts)
            base_has_audio = _probe_has_audio_stream(out_file)
            layer_audio_labels: list[str] = []

            for audio_idx, layer in enumerate(audio_layers):
                layer_path = layer["path"]
                if not _probe_has_audio_stream(layer_path):
                    continue

                start_time = max(0.0, float(layer.get("start_time", 0.0) or 0.0))
                configured_end = max(start_time, float(layer.get("end_time", 0.0) or 0.0))
                layer_duration = max(0.0, float(layer.get("duration", 0.0) or 0.0))
                source_offset = max(0.0, float(layer.get("source_offset", 0.0) or 0.0))

                clip_duration = max(0.0, configured_end - start_time)
                if clip_duration <= 0.02:
                    clip_duration = layer_duration if layer_duration > 0 else max(0.0, final_output_duration - start_time)
                if layer_duration > 0:
                    clip_duration = min(clip_duration, max(0.0, layer_duration - source_offset))
                if final_output_duration > 0:
                    clip_duration = min(clip_duration, max(0.0, final_output_duration - start_time))
                if clip_duration <= 0.02:
                    continue

                volume_factor = max(0.0, min(2.0, float(layer.get("volume_pct", 100.0)) / 100.0))
                if volume_factor <= 0.0001:
                    continue

                delay_ms = max(0, int(round(start_time * 1000)))
                out_label = f"la{audio_idx}"
                filter_parts.append(
                    f"[{layer['input_idx']}:a]"
                    f"atrim=start={source_offset:.6f}:duration={clip_duration:.6f},"
                    f"asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},"
                    f"volume={volume_factor:.4f}[{out_label}]"
                )
                layer_audio_labels.append(f"[{out_label}]")

            audio_map = "0:a?"
            audio_filtering_applied = False
            if layer_audio_labels:
                mix_inputs: list[str] = []
                if base_has_audio:
                    filter_parts.append("[0:a]anull[a_base]")
                    mix_inputs.append("[a_base]")
                mix_inputs.extend(layer_audio_labels)

                if len(mix_inputs) > 1:
                    filter_parts.append(
                        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=0[a_mix]"
                    )
                    current_audio = "[a_mix]"
                else:
                    current_audio = mix_inputs[0]

                if final_output_duration > 0:
                    filter_parts.append(f"{current_audio}atrim=0:{final_output_duration:.6f}[a_out]")
                    audio_map = "[a_out]"
                else:
                    audio_map = current_audio
                audio_filtering_applied = True

            video_map = current_video_label if overlay_parts else "0:v"
            visual_filtering_applied = bool(overlay_parts)

            if not visual_filtering_applied and not audio_filtering_applied:
                # Nothing effectively changed in this second pass.
                final_out_file = out_file
            else:
                if filter_parts:
                    layer_cmd += ["-filter_complex", ";".join(filter_parts)]
                layer_cmd += [
                    "-map", video_map,
                    "-map", audio_map,
                ]

                if visual_filtering_applied:
                    layer_cmd += [
                        "-c:v", "libx264",
                        "-preset", _EDITOR_EXPORT_PRESET,
                        "-crf", _EDITOR_EXPORT_CRF,
                    ]
                else:
                    layer_cmd += ["-c:v", "copy"]

                if audio_filtering_applied:
                    layer_cmd += ["-c:a", "aac", "-b:a", _EDITOR_EXPORT_AUDIO_BITRATE]
                else:
                    layer_cmd += ["-c:a", "copy"]

                layer_cmd += [
                    "-movflags", "+faststart",
                    layered_out_file,
                ]

                logger.info(f"[editor] Layer export cmd: {' '.join(layer_cmd)}")
                layer_proc = subprocess.run(layer_cmd, capture_output=True)
                if layer_proc.returncode != 0:
                    logger.error("[editor] Layer overlay FFmpeg failed: %s", (layer_proc.stderr or b"")[:600])
                    job["status"] = "failed"
                    job["error"] = "Falha ao compor camadas de vídeo/imagem"
                    return

                final_out_file = layered_out_file

        job["progress"] = 95
        job["message"] = "Finalizando..."

        # Register as a new project so the source project remains unchanged.
        from app.database import async_session

        async def _save_render():
            async with async_session() as db:
                source_title = (project.title or project.track_title or "Vídeo").strip() or "Vídeo"
                edited_title = f"{source_title} (Editado)"
                if len(edited_title) > 500:
                    edited_title = edited_title[:500]

                exported_project = VideoProject(
                    user_id=project.user_id,
                    track_id=int(project.track_id or 0),
                    title=edited_title,
                    description=project.description or "",
                    tags=project.tags or [],
                    style_prompt=project.style_prompt or "",
                    aspect_ratio=selected_aspect,
                    status=VideoStatus.COMPLETED,
                    progress=100,
                    track_title=project.track_title,
                    track_artist=project.track_artist,
                    track_duration=float(final_output_duration or project.track_duration or 0) or None,
                    lyrics_text=project.lyrics_text,
                    lyrics_words=project.lyrics_words,
                    audio_path=project.audio_path,
                    use_custom_images=bool(project.use_custom_images),
                    use_custom_video=True,
                    enable_subtitles=bool(project.enable_subtitles),
                    zoom_images=bool(project.zoom_images),
                    image_display_seconds=project.image_display_seconds,
                    no_background_music=bool(project.no_background_music),
                    is_karaoke=bool(project.is_karaoke),
                    is_realistic=bool(project.is_realistic),
                )
                db.add(exported_project)
                await db.flush()

                new_render = VideoRender(
                    project_id=exported_project.id,
                    format=selected_aspect,
                    file_path=final_out_file,
                    file_size=os.path.getsize(final_out_file) if os.path.exists(final_out_file) else None,
                    thumbnail_path=render.thumbnail_path,
                    duration=float(final_output_duration or 0) or None,
                )
                db.add(new_render)
                await db.commit()

                return exported_project.id

        future = asyncio.run_coroutine_threadsafe(_save_render(), main_loop)
        try:
            exported_project_id = future.result(timeout=60)
        except Exception as save_error:
            logger.exception("[editor] Failed to persist export render: %s", save_error)
            job["status"] = "failed"
            job["error"] = "Falha ao salvar o vídeo exportado"
            return

        job["progress"] = 100
        job["status"] = "completed"
        job["message"] = "Exportacao concluida!"
        job["output_url"] = _to_media_url(final_out_file)
        job["output_project_id"] = exported_project_id
        logger.info(f"[editor] Export completed: {final_out_file}")

    except Exception as e:
        logger.exception(f"[editor] Export error: {e}")
        _export_jobs[job_id]["status"] = "failed"
        _export_jobs[job_id]["error"] = str(e)

