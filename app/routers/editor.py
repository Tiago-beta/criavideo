"""
Editor Router — Endpoints for the video editor (trim, text overlays, subtitles,
filters, music replacement, stickers, quality enhancement, export).
"""
import json
import logging
import os
import shutil
import subprocess
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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video/editor", tags=["editor"])
settings = get_settings()

# In-memory export jobs
_export_jobs: dict[str, dict] = {}


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


# ── Upload music ──────────────────────────────────────
@router.post("/upload-music")
async def upload_music(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    if not file.content_type or not file.content_type.startswith("audio"):
        raise HTTPException(400, "Arquivo deve ser de audio")
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
    return {"path": str(dest)}


@router.post("/upload-video")
async def upload_video(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.content_type or not file.content_type.startswith("video"):
        raise HTTPException(400, "Arquivo deve ser de video")

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
    title = (Path(file.filename or "Video enviado").stem or "Video enviado").strip()[:500]
    if not title:
        title = "Video enviado"

    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=title,
        description="Video enviado para edicao",
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

    media_prefix = settings.media_dir.rstrip("/")
    video_url = "/video/media" + str(dest)[len(media_prefix):] if str(dest).startswith(media_prefix) else None
    return {
        "project_id": project.id,
        "video_url": video_url,
        "duration": duration,
        "aspect_ratio": project.aspect_ratio,
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
        raise HTTPException(404, "Projeto nao encontrado")
    render = next((r for r in sorted(project.renders, key=lambda rr: rr.id or 0, reverse=True) if r.file_path), None)
    if not render:
        raise HTTPException(400, "Nenhum video disponivel")

    src_video = _resolve_render_video_path(render)
    if not src_video or not os.path.exists(src_video):
        src_video = _fallback_project_video_path(project.id)
    if not src_video:
        raise HTTPException(400, "Arquivo de video nao encontrado")

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
                raise HTTPException(400, "Este video nao possui faixa de audio para transcricao")
            raise HTTPException(500, "Falha ao extrair audio do video")
    except subprocess.TimeoutExpired:
        logger.error("[editor] Transcribe audio extraction timeout project_id=%s src=%s", project.id, src_video)
        raise HTTPException(500, "Timeout ao extrair audio do video")
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
        raise HTTPException(404, "Projeto nao encontrado")
    render = next((r for r in sorted(project.renders, key=lambda rr: rr.id or 0, reverse=True) if r.file_path), None)
    if not render:
        raise HTTPException(400, "Nenhum video disponivel para editar")

    job_id = uuid.uuid4().hex[:12]
    _export_jobs[job_id] = {
        "status": "processing",
        "progress": 0,
        "message": "Iniciando exportacao...",
        "error": None,
        "output_url": None,
    }

    background_tasks.add_task(
        _run_export, job_id, project, render, req, user["id"]
    )
    return {"job_id": job_id}


# ── Export status polling ──────────────────────────────
@router.get("/export/{job_id}/status")
async def export_status(job_id: str, user=Depends(get_current_user)):
    job = _export_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")
    return job


# ── Background export function ─────────────────────────
def _run_export(job_id: str, project, render, req: ExportRequest, user_id: int):
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
                job["error"] = "Arquivo de video nao encontrado no servidor"
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

        logger.info(
            "[editor] Export video_segments=%s audio_segments=%s use_vf=%s use_af=%s",
            video_segments,
            audio_segments,
            use_video_segment_filter,
            use_audio_segment_filter,
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
                fontsize = txt.font_size
                color = txt.color.lstrip("#")
                x_expr = f"(w*{txt.x/100})"
                y_expr = f"(h*{txt.y/100})"
                escaped_text = txt.content.replace("'", "'\\\\\\''").replace(":", "\\:")
                dt = f"drawtext=text='{escaped_text}':fontsize={fontsize}:fontcolor=0x{color}:x={x_expr}-tw/2:y={y_expr}-th/2:enable='between(t,{st},{et})':shadowcolor=black:shadowx=2:shadowy=2"
                vfilters.append(dt)

        # Subtitle overlays
        for sub in req.subtitles:
            mapped_ranges = _map_source_interval_to_output(sub.start_time, sub.end_time, video_segments)
            for st, et in mapped_ranges:
                color = sub.font_color.lstrip("#") if sub.font_color else "FFFFFF"
                fsize = sub.font_size or 28
                x_expr = f"(w*{sub.x/100})-tw/2" if sub.x else "(w-tw)/2"
                y_expr = f"(h*{sub.y/100})-th/2" if sub.y else "h-80"
                escaped_text = sub.text.replace("'", "'\\\\\\\\''").replace(":", "\\:")
                font_family = (sub.font_family or "Arial").split(",")[0].strip()
                dt_parts = [
                    f"drawtext=text='{escaped_text}'",
                    f"fontsize={fsize}",
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
                    dt_parts.append(f"box=1:boxcolor=0x{bg}@0.6:boxborderw=8")
                if sub.outline_color:
                    border_c = sub.outline_color.lstrip("#")[:6]
                    dt_parts.append(f"borderw=2:bordercolor=0x{border_c}")
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

        job["progress"] = 20
        job["message"] = "Renderizando video..."

        # Video filter
        if vfilters:
            cmd += ["-vf", ",".join(vfilters)]

        # Audio handling
        if has_music:
            orig_vol = req.original_volume / 100
            music_vol = req.music_volume / 100
            if source_has_audio:
                base_audio_label = "[0:a]"
                audio_chain: list[str] = []
                if use_audio_segment_filter:
                    audio_chain.append(f"[0:a]aselect='{audio_select_expr}',asetpts=N/SR/TB[a_src]")
                    base_audio_label = "[a_src]"
                audio_chain.append(f"{base_audio_label}volume={orig_vol}[a0]")
                audio_chain.append(f"[1:a]volume={music_vol}[a1]")
                audio_chain.append("[a0][a1]amix=inputs=2:duration=shortest[a_mix]")
                if output_video_duration > 0:
                    audio_chain.append(f"[a_mix]atrim=0:{output_video_duration:.6f}[aout]")
                else:
                    audio_chain.append("[a_mix]anull[aout]")
                cmd += [
                    "-filter_complex", ";".join(audio_chain),
                    "-map", "0:v",
                    "-map", "[aout]",
                ]
            else:
                if output_video_duration > 0:
                    music_chain = f"[1:a]volume={music_vol}[a1];[a1]atrim=0:{output_video_duration:.6f}[aout]"
                else:
                    music_chain = f"[1:a]volume={music_vol}[aout]"
                cmd += [
                    "-filter_complex", music_chain,
                    "-map", "0:v",
                    "-map", "[aout]",
                ]
        else:
            orig_vol = req.original_volume / 100
            afilters: list[str] = []
            if source_has_audio and use_audio_segment_filter:
                afilters.append(f"aselect='{audio_select_expr}',asetpts=N/SR/TB")
            if source_has_audio and orig_vol != 1.0:
                afilters.append(f"volume={orig_vol}")
            if source_has_audio and output_video_duration > 0:
                afilters.append(f"atrim=0:{output_video_duration:.6f}")
            if afilters:
                cmd += ["-af", ",".join(afilters)]

        # Output settings
        if has_music:
            cmd += ["-shortest"]

        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            out_file,
        ]

        job["progress"] = 30
        job["message"] = "Processando com FFmpeg..."

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
            job["message"] = "Processando com FFmpeg..."

        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            logger.error(f"[editor] FFmpeg failed: {stderr.decode()[:500]}")
            job["status"] = "failed"
            job["error"] = "FFmpeg falhou ao processar o video"
            return

        job["progress"] = 95
        job["message"] = "Finalizando..."

        # Register as a new render
        from app.database import async_session
        import asyncio

        async def _save_render():
            async with async_session() as db:
                new_render = VideoRender(
                    project_id=project.id,
                    format=selected_aspect,
                    file_path=out_file,
                    file_size=os.path.getsize(out_file) if os.path.exists(out_file) else None,
                    thumbnail_path=render.thumbnail_path,
                )
                db.add(new_render)
                await db.commit()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_save_render())
        loop.close()

        job["progress"] = 100
        job["status"] = "completed"
        job["message"] = "Exportacao concluida!"
        logger.info(f"[editor] Export completed: {out_file}")

    except Exception as e:
        logger.exception(f"[editor] Export error: {e}")
        _export_jobs[job_id]["status"] = "failed"
        _export_jobs[job_id]["error"] = str(e)
