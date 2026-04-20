"""
Video Router — Endpoints for creating video projects, generating scenes/renders.
"""
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from typing import Optional
import openai
from app.auth import get_current_user
from app.database import get_db
from app.models import VideoProject, VideoScene, VideoRender, VideoStatus
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video", tags=["video"])
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)

# Voice demo config: name, label, demo phrase
VOICE_DEMOS = {
    "onyx":    {"name": "Lucas",   "label": "Masculina Grave",   "text": "Oi, eu sou o Lucas! Com minha voz grave e marcante, vou dar presença aos seus vídeos. Me escolha!"},
    "echo":    {"name": "Rafael",  "label": "Masculina Suave",   "text": "Olá, sou o Rafael! Minha voz suave e envolvente vai conquistar seu público. Me escolha!"},
    "ash":     {"name": "Pedro",   "label": "Masculina Natural", "text": "E aí, sou o Pedro! Com minha voz natural e autêntica, seus vídeos vão ficar incríveis. Me escolha!"},
    "nova":    {"name": "Clara",   "label": "Feminina Clara",    "text": "Oi, eu sou a Clara! Minha voz clara e vibrante vai dar vida aos seus vídeos. Me escolha!"},
    "shimmer": {"name": "Sofia",   "label": "Feminina Suave",    "text": "Olá, sou a Sofia! Com minha voz suave e delicada, vou encantar quem assistir. Me escolha!"},
    "coral":   {"name": "Beatriz", "label": "Feminina Natural",  "text": "Oi, eu sou a Beatriz! Minha voz natural e expressiva é perfeita para seus vídeos. Me escolha!"},
    "alloy":   {"name": "Alex",    "label": "Neutra",            "text": "Olá, sou Alex! Minha voz versátil se adapta a qualquer tipo de conteúdo. Me escolha!"},
    "fable":   {"name": "Mateus",  "label": "Narrativa",         "text": "Olá, sou o Mateus! Minha voz narrativa vai transformar seus vídeos em histórias inesquecíveis. Me escolha!"},
    "sage":    {"name": "Luna",    "label": "Calma e Clara",     "text": "Oi, eu sou a Luna! Com minha voz calma e clara, vou transmitir tranquilidade nos seus vídeos. Me escolha!"},
}

VOICE_DEMO_DIR = os.path.join(settings.media_dir, "voice_demos")
os.makedirs(VOICE_DEMO_DIR, exist_ok=True)

TEMP_UPLOAD_DIR = Path(settings.media_dir) / "temp_uploads"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
KARAOKE_PROGRESS_TTL_MINUTES = 120
_karaoke_progress_store: dict[str, dict] = {}
_REFERENCE_IMAGE_HINT_MARKERS = (
    "reference image",
    "uploaded image",
    "user-provided image",
    "first frame",
    "imagem de referencia",
    "foto enviada",
)
_INTERACTION_PERSONAS = {"homem", "mulher", "crianca", "familia", "natureza"}


def _ensure_reference_image_instruction(prompt: str) -> str:
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    lowered = base_prompt.lower()
    if any(marker in lowered for marker in _REFERENCE_IMAGE_HINT_MARKERS):
        return base_prompt

    reference_rule = (
        "Mandatory reference image rule: use the uploaded user image as the primary visual anchor. "
        "Keep the same main subject identity, face traits, hair, colors, and overall visual style from that reference image."
    )
    return f"{base_prompt}\n\n{reference_rule}"


def _normalize_interaction_persona(value: str) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "criança": "crianca",
        "crianca": "crianca",
        "família": "familia",
        "familia": "familia",
    }
    normalized = mapping.get(raw, raw)
    if normalized in _INTERACTION_PERSONAS:
        return normalized
    return "natureza"


def _cleanup_karaoke_progress_store() -> None:
    if not _karaoke_progress_store:
        return
    cutoff = datetime.utcnow() - timedelta(minutes=KARAOKE_PROGRESS_TTL_MINUTES)
    stale_keys = [
        op_id
        for op_id, state in _karaoke_progress_store.items()
        if datetime.fromisoformat(state.get("updated_at", "1970-01-01T00:00:00")) < cutoff
    ]
    for key in stale_keys:
        _karaoke_progress_store.pop(key, None)


def _set_karaoke_progress(
    operation_id: str,
    user_id: int,
    progress: int,
    message: str,
    *,
    status: str = "running",
    stage: str = "removing_vocals",
    error: str = "",
) -> None:
    if not operation_id:
        return
    _cleanup_karaoke_progress_store()
    now_iso = datetime.utcnow().isoformat()
    _karaoke_progress_store[operation_id] = {
        "operation_id": operation_id,
        "user_id": int(user_id),
        "status": status,
        "stage": stage,
        "progress": max(0, min(100, int(progress))),
        "message": message,
        "error": error,
        "updated_at": now_iso,
    }


@router.get("/karaoke-progress/{operation_id}")
async def get_karaoke_progress(operation_id: str, user: dict = Depends(get_current_user)):
    _cleanup_karaoke_progress_store()
    state = _karaoke_progress_store.get(operation_id)
    if not state or int(state.get("user_id", 0)) != int(user["id"]):
        return {
            "operation_id": operation_id,
            "status": "pending",
            "stage": "removing_vocals",
            "progress": 0,
            "message": "Aguardando inicio da remocao de voz...",
            "error": "",
            "updated_at": datetime.utcnow().isoformat(),
        }

    return {
        "operation_id": state.get("operation_id"),
        "status": state.get("status", "running"),
        "stage": state.get("stage", "removing_vocals"),
        "progress": state.get("progress", 0),
        "message": state.get("message", ""),
        "error": state.get("error", ""),
        "updated_at": state.get("updated_at", datetime.utcnow().isoformat()),
    }


def _temp_user_dir(user_id: int) -> Path:
    path = TEMP_UPLOAD_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_temp_file(user_id: int, upload_id: str, allowed_exts: set[str]) -> Path | None:
    # upload_id format: <uuid><ext>
    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return None
    ext = Path(upload_id).suffix.lower()
    if ext not in allowed_exts:
        return None
    candidate = _temp_user_dir(user_id) / upload_id
    return candidate if candidate.exists() else None


@router.post("/upload-temp-image")
async def upload_temp_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo de imagem invalido")
    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="Formato de imagem nao suportado")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem excede 10MB")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = _temp_user_dir(user["id"]) / upload_id
    with open(target, "wb") as f:
        f.write(content)
    return {"upload_id": upload_id, "size": len(content)}


@router.post("/upload-temp-audio")
async def upload_temp_audio(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo de audio invalido")
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(status_code=400, detail="Formato de audio nao suportado")

    content = await file.read()
    if len(content) > 80 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio excede 80MB")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = _temp_user_dir(user["id"]) / upload_id
    with open(target, "wb") as f:
        f.write(content)
    return {"upload_id": upload_id, "size": len(content)}


@router.post("/upload-temp-video")
async def upload_temp_video(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo de video invalido")
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(status_code=400, detail="Formato de video nao suportado. Use MP4, MOV, AVI ou WEBM.")

    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Video excede 500MB")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = _temp_user_dir(user["id"]) / upload_id
    with open(target, "wb") as f:
        f.write(content)
    return {"upload_id": upload_id, "size": len(content)}


@router.post("/upload-temp-chunk/start")
async def upload_temp_chunk_start(
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await request.json()
    filename = str(payload.get("filename", "")).strip()
    kind = str(payload.get("kind", "image")).strip().lower() or "image"
    size = int(payload.get("size", 0) or 0)

    if not filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo invalido")

    ext = Path(filename).suffix.lower()
    if kind == "audio":
        allowed = AUDIO_EXTS
        max_size = 80 * 1024 * 1024
    else:
        allowed = IMAGE_EXTS
        max_size = 10 * 1024 * 1024

    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Formato de arquivo nao suportado")
    if size <= 0 or size > max_size:
        raise HTTPException(status_code=400, detail="Tamanho de arquivo invalido")

    user_dir = _temp_user_dir(user["id"])
    session_id = uuid.uuid4().hex
    part_path = user_dir / f"{session_id}.part"
    meta_path = user_dir / f"{session_id}.json"

    with open(part_path, "wb") as f:
        f.truncate(size)

    meta = {
        "filename": filename,
        "ext": ext,
        "kind": kind,
        "size": size,
        "received": 0,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return {"session_id": session_id, "chunk_size": 512 * 1024}


@router.post("/upload-temp-chunk/{session_id}")
async def upload_temp_chunk(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    user_dir = _temp_user_dir(user["id"])
    meta_path = user_dir / f"{session_id}.json"
    part_path = user_dir / f"{session_id}.part"
    if not meta_path.exists() or not part_path.exists():
        raise HTTPException(status_code=404, detail="Sessao de upload nao encontrada")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Metadados de upload invalidos")

    try:
        offset = int(request.headers.get("x-upload-offset", "0"))
    except Exception:
        raise HTTPException(status_code=400, detail="Offset invalido")

    received = int(meta.get("received", 0))
    if offset != received:
        return {"received": received, "size": int(meta.get("size", 0)), "mismatch": True}

    chunk = await file.read()
    if not chunk:
        raise HTTPException(status_code=400, detail="Chunk vazio")

    size = int(meta.get("size", 0))
    new_received = received + len(chunk)
    if new_received > size:
        raise HTTPException(status_code=400, detail="Chunk excede tamanho total")

    with open(part_path, "r+b") as f:
        f.seek(offset)
        f.write(chunk)

    meta["received"] = new_received
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return {"received": new_received, "size": size, "done": new_received >= size}


@router.post("/upload-temp-chunk/{session_id}/finish")
async def upload_temp_chunk_finish(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    user_dir = _temp_user_dir(user["id"])
    meta_path = user_dir / f"{session_id}.json"
    part_path = user_dir / f"{session_id}.part"
    if not meta_path.exists() or not part_path.exists():
        raise HTTPException(status_code=404, detail="Sessao de upload nao encontrada")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Metadados de upload invalidos")

    size = int(meta.get("size", 0))
    received = int(meta.get("received", 0))
    if received < size:
        raise HTTPException(status_code=400, detail="Upload incompleto")

    ext = str(meta.get("ext", "")).lower()
    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = user_dir / upload_id
    os.replace(part_path, target)
    try:
        meta_path.unlink(missing_ok=True)
    except Exception:
        pass

    return {"upload_id": upload_id, "size": size, "kind": meta.get("kind", "image")}


@router.get("/voice-demo/{voice_id}")
async def get_voice_demo(voice_id: str):
    """Return a cached TTS demo for the given voice. Generates on first request."""
    if voice_id not in VOICE_DEMOS:
        raise HTTPException(404, "Voice not found")

    cache_path = os.path.join(VOICE_DEMO_DIR, f"{voice_id}.mp3")
    if not os.path.exists(cache_path):
        demo = VOICE_DEMOS[voice_id]
        resp = await _openai.audio.speech.create(
            model="tts-1",
            voice=voice_id,
            input=demo["text"],
            response_format="mp3",
        )
        Path(cache_path).write_bytes(resp.content)

    return FileResponse(cache_path, media_type="audio/mpeg")


def _to_media_url(path: str | None) -> str | None:
    """Convert absolute file path to web-accessible URL."""
    if not path:
        return None
    media_prefix = settings.media_dir.rstrip("/")
    if path.startswith(media_prefix):
        return "/video/media" + path[len(media_prefix):]
    return None


class CreateProjectRequest(BaseModel):
    track_id: int = 0
    title: str = ""
    description: str = ""
    tags: list[str] = []
    style_prompt: str = ""
    aspect_ratio: str = "16:9"
    # Track data from Levita
    track_title: str = ""
    track_artist: str = ""
    track_duration: float = 0
    lyrics_text: str = ""
    lyrics_words: list[dict] = []
    audio_path: str = ""


class QuickCreateRequest(BaseModel):
    """Request from Levita's "Criar Vídeo" button — minimal data, AI fills the rest."""
    song_title: str = ""
    song_artist: str = ""
    audio_url: str
    lyrics: str = ""
    duration: float = 0
    aspect_ratio: str = "16:9"
    style_prompt: str = ""     # optional: user-chosen style override
    description: str = ""      # optional: user-provided description/topic


class CopyFormatRequest(BaseModel):
    aspect_ratio: str = "9:16"


class RenameProjectRequest(BaseModel):
    title: str


class ProjectResponse(BaseModel):
    id: int
    status: str
    progress: int
    title: str
    track_title: str | None = None
    track_artist: str | None = None
    aspect_ratio: str
    error_message: str | None = None
    created_at: str


@router.post("/projects", response_model=dict)
async def create_project(
    req: CreateProjectRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new video project from a Levita track."""
    project = VideoProject(
        user_id=user["id"],
        track_id=req.track_id,
        title=req.title or req.track_title or "Untitled Video",
        description=req.description,
        tags=req.tags,
        style_prompt=req.style_prompt,
        aspect_ratio=req.aspect_ratio,
        track_title=req.track_title,
        track_artist=req.track_artist,
        track_duration=req.track_duration,
        lyrics_text=req.lyrics_text,
        lyrics_words=req.lyrics_words,
        audio_path=req.audio_path,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return {"id": project.id, "status": project.status.value}


@router.get("/projects")
async def list_projects(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all video projects for the current user."""
    result = await db.execute(
        select(VideoProject)
        .options(selectinload(VideoProject.renders))
        .where(VideoProject.user_id == user["id"])
        .order_by(VideoProject.created_at.desc())
    )
    projects = result.scalars().all()

    def _ordered_renders(renders: list[VideoRender]) -> list[VideoRender]:
        return sorted(
            renders or [],
            key=lambda r: (r.created_at or datetime.min, r.id or 0),
            reverse=True,
        )

    payload = []
    for p in projects:
        ordered = _ordered_renders(list(p.renders or []))
        latest_any = ordered[0] if ordered else None
        latest_active = next((r for r in ordered if r.file_path), None)
        display_render = latest_active or latest_any

        payload.append(
            {
                "id": p.id,
                "title": p.title,
                "track_title": p.track_title,
                "track_artist": p.track_artist,
                "status": p.status.value,
                "progress": p.progress,
                "aspect_ratio": p.aspect_ratio,
                "error_message": p.error_message,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "render_created_at": display_render.created_at.isoformat() if display_render and display_render.created_at else None,
                "video_expired": bool(ordered) and latest_active is None,
                "lyrics_text": p.lyrics_text or "",
                "style_prompt": p.style_prompt or "",
                "thumbnail_url": _to_media_url(display_render.thumbnail_path) if display_render else None,
            }
        )

    return payload


@router.get("/projects/{project_id}")
async def get_project(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get project details with scenes and renders."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    result_scenes = await db.execute(
        select(VideoScene).where(VideoScene.project_id == project_id).order_by(VideoScene.scene_index)
    )
    scenes = result_scenes.scalars().all()

    result_renders = await db.execute(
        select(VideoRender)
        .where(VideoRender.project_id == project_id)
        .order_by(VideoRender.created_at.desc(), VideoRender.id.desc())
    )
    renders = result_renders.scalars().all()

    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "tags": project.tags,
        "status": project.status.value,
        "progress": project.progress,
        "aspect_ratio": project.aspect_ratio,
        "track_title": project.track_title,
        "track_artist": project.track_artist,
        "track_duration": project.track_duration,
        "error_message": project.error_message,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "scenes": [
            {
                "id": s.id,
                "scene_index": s.scene_index,
                "scene_type": s.scene_type,
                "prompt": s.prompt,
                "image_path": s.image_path,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "lyrics_segment": s.lyrics_segment,
            }
            for s in scenes
        ],
        "renders": [
            {
                "id": r.id,
                "format": r.format,
                "file_path": r.file_path,
                "file_size": r.file_size,
                "thumbnail_path": r.thumbnail_path,
                "duration": r.duration,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "video_url": _to_media_url(r.file_path),
                "thumbnail_url": _to_media_url(r.thumbnail_path),
            }
            for r in renders
        ],
    }


@router.patch("/projects/{project_id}/title")
async def rename_project(
    project_id: int,
    req: RenameProjectRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a video project title."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    new_title = (req.title or "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Titulo nao pode ficar vazio")
    if len(new_title) > 500:
        raise HTTPException(status_code=400, detail="Titulo muito longo (maximo 500 caracteres)")

    project.title = new_title
    await db.commit()
    await db.refresh(project)
    return {"id": project.id, "title": project.title}


@router.post("/projects/{project_id}/thumbnail")
async def update_project_thumbnail(
    project_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload/replace thumbnail for a completed project."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate image type
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Formato invalido. Envie JPG, PNG ou WebP.")
    if file.size and file.size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande (maximo 10MB)")

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(file.content_type, ".jpg")
    thumb_dir = Path("thumbnails") / str(project_id)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"thumbnail{ext}"

    # Remove old thumbnails with different extensions
    for old in thumb_dir.glob("thumbnail.*"):
        old.unlink(missing_ok=True)

    data = await file.read()
    thumb_path.write_bytes(data)

    # Update render record
    result = await db.execute(
        select(VideoRender).where(VideoRender.project_id == project_id)
    )
    render = result.scalars().first()
    if render:
        render.thumbnail_path = str(thumb_path)
        await db.commit()

    return {"thumbnail_path": str(thumb_path)}


@router.post("/projects/{project_id}/images")
async def upload_project_images(
    project_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more custom images to an existing project."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    form = await request.form()
    try:
        raw_images = form.getlist("images")
    except Exception:
        raw_images = []

    uploads = [item for item in raw_images if getattr(item, "filename", "")]
    if not uploads:
        raise HTTPException(status_code=400, detail="Nenhuma imagem enviada")

    img_dir = Path(settings.media_dir) / "images" / str(project.id)
    img_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(img_dir.glob("user_*.*"))
    next_idx = len(existing)
    max_total = 20
    remaining_slots = max_total - next_idx
    if remaining_slots <= 0:
        raise HTTPException(status_code=400, detail="Limite de 20 imagens por projeto atingido")

    saved_files: list[str] = []
    for image in uploads[:remaining_slots]:
        filename = str(getattr(image, "filename", "") or "").strip()
        if not filename:
            continue

        ext = Path(filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            raise HTTPException(status_code=400, detail=f"Formato nao suportado para {filename}. Use JPG, PNG ou WebP.")

        content = await image.read()
        if not content:
            continue
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Imagem {filename} excede 10MB")

        target_name = f"user_{next_idx:03d}{ext}"
        target_path = img_dir / target_name
        with open(target_path, "wb") as f:
            f.write(content)

        saved_files.append(target_name)
        next_idx += 1

    if not saved_files:
        raise HTTPException(status_code=400, detail="Nenhuma imagem valida enviada")

    if not bool(getattr(project, "use_custom_video", False)):
        project.use_custom_images = True
    await db.commit()

    return {
        "project_id": project.id,
        "saved_count": len(saved_files),
        "images": saved_files,
        "total_images": next_idx,
    }


@router.post("/projects/{project_id}/generate")
async def generate_video(
    project_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start the full video generation pipeline (async background task)."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (VideoStatus.PENDING, VideoStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Project is already {project.status.value}")

    # If audio is missing but we have the script, regenerate TTS
    if (not project.audio_path or not os.path.exists(project.audio_path)) and project.lyrics_text:
        from app.services.script_audio import generate_tts_audio
        try:
            voice = "onyx"
            tts_instructions = ""
            voice_type = "builtin"

            # Check if user has a default voice profile
            from app.models import VoiceProfile
            from sqlalchemy import select
            result = await db.execute(
                select(VoiceProfile).where(
                    VoiceProfile.user_id == user["id"],
                    VoiceProfile.is_default == True
                )
            )
            default_profile = result.scalar_one_or_none()
            if default_profile:
                if default_profile.openai_voice_id:
                    voice = default_profile.openai_voice_id
                    voice_type = "custom"
                elif default_profile.builtin_voice:
                    voice = default_profile.builtin_voice
                tts_instructions = default_profile.tts_instructions or ""

            audio_path = await generate_tts_audio(
                text=project.lyrics_text,
                voice=voice,
                project_id=project.id,
                tts_instructions=tts_instructions,
                voice_type=voice_type,
            )
            project.audio_path = audio_path
            word_count = len(project.lyrics_text.split())
            project.track_duration = round(word_count / 2.5)
        except Exception as e:
            project.status = VideoStatus.FAILED
            project.error_message = f"Erro ao gerar audio: {e}"
            await db.commit()
            raise HTTPException(status_code=500, detail=f"Erro ao gerar audio: {e}")

    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    project.error_message = None
    await db.commit()

    from app.tasks.video_tasks import run_video_pipeline
    background_tasks.add_task(run_video_pipeline, project_id)

    return {"status": "started", "project_id": project_id}


@router.post("/projects/{project_id}/copy-format")
async def copy_project_with_format(
    project_id: int,
    req: CopyFormatRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an exact copy of a completed project and re-render in another aspect ratio."""
    if req.aspect_ratio not in {"16:9", "9:16", "1:1"}:
        raise HTTPException(status_code=400, detail="Formato invalido. Use 16:9, 9:16 ou 1:1")

    source = await db.get(VideoProject, project_id)
    if not source or source.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")
    if source.status != VideoStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Somente projetos concluidos podem ser copiados")

    source_render_res = await db.execute(
        select(VideoRender)
        .where(VideoRender.project_id == source.id)
        .order_by(VideoRender.created_at.desc())
    )
    source_render = source_render_res.scalars().first()
    if not source_render or not source_render.file_path:
        raise HTTPException(status_code=400, detail="Projeto origem sem video renderizado")
    if not os.path.exists(source_render.file_path):
        raise HTTPException(status_code=400, detail="Arquivo do video origem nao foi encontrado")

    title = (source.title or source.track_title or "Video").strip()
    new_title = f"{title} [{req.aspect_ratio}]"

    project = VideoProject(
        user_id=source.user_id,
        track_id=source.track_id,
        title=new_title,
        description=source.description or "",
        tags=source.tags or [],
        style_prompt=source.style_prompt or "",
        aspect_ratio=req.aspect_ratio,
        track_title=source.track_title or "",
        track_artist=source.track_artist or "",
        track_duration=source.track_duration or 0,
        lyrics_text=source.lyrics_text or "",
        lyrics_words=source.lyrics_words or [],
        audio_path=source.audio_path or "",
        use_custom_images=bool(getattr(source, "use_custom_images", False)),
        enable_subtitles=bool(getattr(source, "enable_subtitles", True)),
        zoom_images=bool(getattr(source, "zoom_images", True)),
        image_display_seconds=float(getattr(source, "image_display_seconds", 0) or 0),
        status=VideoStatus.RENDERING,
        progress=10,
        error_message=None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    from app.tasks.video_tasks import run_video_format_copy_pipeline
    background_tasks.add_task(run_video_format_copy_pipeline, project.id, source_render.file_path)

    return {
        "id": project.id,
        "status": "started",
        "source_project_id": source.id,
        "aspect_ratio": project.aspect_ratio,
    }


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a video project and all associated files."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    # Clean up files
    import shutil
    from pathlib import Path
    for dir_name in ["images", "clips", "renders", "subtitles"]:
        dir_path = Path(settings.media_dir) / dir_name / str(project_id)
        if dir_path.exists():
            shutil.rmtree(dir_path, ignore_errors=True)

    await db.delete(project)
    await db.commit()
    return {"deleted": True}


@router.post("/quick-create")
async def quick_create(
    req: QuickCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One-click video creation: AI generates title/description/style, creates project, starts pipeline."""
    # Detect gospel/worship genre from lyrics and title
    _text_lower = f"{req.song_title or ''} {req.song_artist or ''} {(req.lyrics or '')[:500]}".lower()
    _is_gospel = any(w in _text_lower for w in [
        "gospel", "worship", "louvor", "adoração", "adoracao", "deus", "senhor",
        "jesus", "cristo", "espírito", "espirito", "santo", "glória", "gloria",
        "redenção", "redencao", "fé", "oração", "oracao", "salvação", "salvacao",
        "graça", "graca", "igreja", "aleluia", "hallelujah", "amém", "amen",
    ])

    _gospel_style_instruction = """
IMPORTANT: This is a GOSPEL/WORSHIP song. The style_prompt MUST reflect spiritual, uplifting imagery:
- Use nature landscapes: mountains, valleys, rivers, sunrise, sunset, golden light, green pastures, calm waters, starry sky, fields of wheat, olive trees, gentle rain, waterfalls, meadows, oceans
- Use warm, golden, celestial lighting — NOT dark, horror, or scary imagery
- Do NOT mention birds, doves, or animals in the style_prompt — focus on landscapes and light
- NEVER use dark/horror/scary/gothic themes for gospel music""" if _is_gospel else ""

    # Ask AI to generate creative metadata from song info
    ai_prompt = f"""Você é um produtor criativo de vídeos musicais.
Com base nos dados desta música, gere metadados criativos para um videoclipe.

Título da música: {req.song_title or 'Desconhecido'}
Artista: {req.song_artist or 'Desconhecido'}
Duração: {req.duration:.0f} segundos
Trecho da letra:
{(req.lyrics or 'Sem letra disponível')[:800]}
{_gospel_style_instruction}

Responda SOMENTE um JSON com:
- "title": título curto e criativo para o projeto de vídeo (máx 60 chars, em português)
- "description": descrição envolvente para redes sociais (máx 200 chars, em português)
- "style_prompt": prompt em INGLÊS descrevendo o estilo visual ideal (cores, cenário, mood, iluminação — máx 120 chars)
- "tags": lista de 3-5 tags relevantes em português

JSON apenas, sem markdown."""

    title = req.song_title or "Meu Vídeo"
    description = req.description or ""
    style_prompt = req.style_prompt or "cinematic, vibrant colors, dynamic lighting"
    tags = []

    # If user provided style_prompt, skip AI generation for style
    if req.style_prompt:
        # Still generate title/description/tags via AI if description not provided
        if not req.description:
            try:
                resp = await _openai.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": ai_prompt}],
                    temperature=0.8,
                    max_tokens=300,
                )
                data = json.loads(resp.choices[0].message.content)
                title = data.get("title", title)
                description = data.get("description", description)
                tags = data.get("tags", tags)
            except Exception as e:
                logger.warning("AI metadata generation failed, using defaults: %s", e)
    else:
        try:
            resp = await _openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": ai_prompt}],
                temperature=0.8,
                max_tokens=300,
            )
            data = json.loads(resp.choices[0].message.content)
            title = data.get("title", title)
            description = data.get("description", description)
            style_prompt = data.get("style_prompt", style_prompt)
            tags = data.get("tags", tags)
        except Exception as e:
            logger.warning("AI metadata generation failed, using defaults: %s", e)

    # ── Credit check: deduct based on song duration ──
    # Skip for Levita users (credits handled by Levita backend)
    if user.get("source") != "levita":
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        import math
        est_minutes = max(1, math.ceil((req.duration or 60) / 60))
        credits_needed = est_minutes * CREDITS_PER_MINUTE
        await deduct_credits(db, user["id"], credits_needed)

    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=title,
        description=description,
        tags=tags,
        style_prompt=style_prompt,
        aspect_ratio=req.aspect_ratio,
        track_title=req.song_title or "",
        track_artist=req.song_artist or "",
        track_duration=req.duration,
        lyrics_text=req.lyrics or "",
        lyrics_words=[],
        audio_path=req.audio_url,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # Auto-start generation
    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    await db.commit()

    from app.tasks.video_tasks import run_video_pipeline
    background_tasks.add_task(run_video_pipeline, project.id)

    return {
        "id": project.id,
        "title": title,
        "description": description,
        "style_prompt": style_prompt,
        "tags": tags,
        "status": "generating_scenes",
    }


# ── Script & Audio Generation ──────────────────────────────────


class FixTextRequest(BaseModel):
    text: str


class GenerateScriptRequest(BaseModel):
    topic: str
    tone: str = "informativo"
    duration_seconds: int = 60
    custom_image_ids: list[str] = Field(default_factory=list)


class GenerateTTSRequest(BaseModel):
    script: str
    voice: str = ""
    voice_profile_id: int = 0
    voice_type: str = ""  # "suno" for Suno AI narration, empty for auto-detect
    title: str = ""
    aspect_ratio: str = "16:9"
    style_prompt: str = ""
    pause_level: str = "normal"
    tone: str = "informativo"
    enable_subtitles: bool = True
    zoom_images: bool = True
    image_display_seconds: float = 0
    no_background_music: bool = False
    use_custom_audio: bool = False
    audio_is_music: bool = False
    remove_vocals: bool = False


@router.post("/fix-text")
async def fix_text_endpoint(
    req: FixTextRequest,
    user: dict = Depends(get_current_user),
):
    """Fix spelling, grammar and punctuation errors in user text using GPT."""
    import openai
    from app.config import get_settings
    settings = get_settings()
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": (
                "Voce e um corretor ortografico e gramatical de portugues brasileiro. "
                "Corrija APENAS erros de ortografia, acentuacao, pontuacao e gramatica no texto. "
                "NAO altere o significado, o estilo, o tom ou a estrutura do texto. "
                "NAO remova nem adicione frases. NAO reescreva o texto. "
                "Mantenha exatamente as reticencias (...), quebras de linha e formatacao original. "
                "Retorne SOMENTE o texto corrigido, sem explicacoes."
            )},
            {"role": "user", "content": req.text},
        ],
        temperature=0.1,
        max_tokens=min(len(req.text) * 2, 16000),
    )
    corrected = resp.choices[0].message.content.strip()

    # Count approximate changes
    original_words = req.text.split()
    corrected_words = corrected.split()
    changes = sum(1 for a, b in zip(original_words, corrected_words) if a != b)
    changes += abs(len(original_words) - len(corrected_words))

    return {"text": corrected, "changes": changes}


@router.post("/generate-script")
async def generate_script_endpoint(
    req: GenerateScriptRequest,
    user: dict = Depends(get_current_user),
):
    """Generate a video narration script using AI."""
    from app.services.script_audio import generate_script

    image_paths: list[str] = []
    for upload_id in (req.custom_image_ids or [])[:8]:
        resolved = _resolve_temp_file(user["id"], str(upload_id).strip(), IMAGE_EXTS)
        if resolved:
            image_paths.append(str(resolved))

    result = await generate_script(
        topic=req.topic,
        tone=req.tone,
        duration_seconds=req.duration_seconds,
        image_paths=image_paths,
    )

    if image_paths:
        result["image_context_used"] = True
        result["image_count_used"] = len(image_paths)

    return result


@router.post("/generate-audio")
async def generate_audio_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate TTS audio from script, create project, and start video pipeline."""
    from app.services.script_audio import generate_tts_audio
    from app.models import VoiceProfile

    # Accept both JSON and multipart/form-data (with optional background_music upload)
    content_type = request.headers.get("content-type", "")
    bgm_upload: UploadFile | None = None
    custom_audio_upload: UploadFile | None = None
    custom_image_uploads: list[UploadFile] = []
    custom_image_ids: list[str] = []
    background_music_id: str = ""
    custom_audio_id: str = ""
    custom_video_id: str = ""
    custom_thumbnail_id: str = ""
    karaoke_operation_id: str = ""
    if "multipart/form-data" in content_type:
        form = await request.form()
        enable_sub_raw = str(form.get("enable_subtitles", "true")).lower()
        zoom_raw = str(form.get("zoom_images", "true")).lower()
        image_seconds_raw = form.get("image_display_seconds", 0)
        no_bgm_raw = str(form.get("no_background_music", "false")).lower()
        use_custom_audio_raw = str(form.get("use_custom_audio", "false")).lower()
        audio_is_music_raw = str(form.get("audio_is_music", "false")).lower()
        remove_vocals_raw = str(form.get("remove_vocals", "false")).lower()
        req = GenerateTTSRequest(
            script=str(form.get("script", "")),
            voice=str(form.get("voice", "")),
            voice_profile_id=int(form.get("voice_profile_id", 0) or 0),
            voice_type=str(form.get("voice_type", "")),
            title=str(form.get("title", "")),
            aspect_ratio=str(form.get("aspect_ratio", "16:9")),
            style_prompt=str(form.get("style_prompt", "")),
            pause_level=str(form.get("pause_level", "normal")),
            tone=str(form.get("tone", "informativo")),
            enable_subtitles=enable_sub_raw not in ("false", "0", "no"),
            zoom_images=zoom_raw not in ("false", "0", "no"),
            image_display_seconds=float(image_seconds_raw or 0),
            no_background_music=no_bgm_raw in ("true", "1", "yes"),
            use_custom_audio=use_custom_audio_raw in ("true", "1", "yes"),
            audio_is_music=audio_is_music_raw in ("true", "1", "yes"),
            remove_vocals=remove_vocals_raw in ("true", "1", "yes"),
        )
        raw_upload = form.get("background_music")
        if isinstance(raw_upload, UploadFile) and raw_upload.filename:
            bgm_upload = raw_upload
        elif getattr(raw_upload, "filename", ""):
            bgm_upload = raw_upload

        raw_main_audio = form.get("custom_audio")
        if isinstance(raw_main_audio, UploadFile) and raw_main_audio.filename:
            custom_audio_upload = raw_main_audio
        elif getattr(raw_main_audio, "filename", ""):
            custom_audio_upload = raw_main_audio

        # Collect custom image uploads (multiple files under "custom_images")
        try:
            uploaded_images = form.getlist("custom_images")
        except Exception:
            uploaded_images = []
        for value in uploaded_images:
            if isinstance(value, UploadFile) and value.filename:
                custom_image_uploads.append(value)
            elif getattr(value, "filename", ""):
                custom_image_uploads.append(value)
        try:
            custom_image_ids = [str(v).strip() for v in form.getlist("custom_image_ids") if str(v).strip()]
        except Exception:
            custom_image_ids = []
        background_music_id = str(form.get("background_music_id", "")).strip()
        custom_audio_id = str(form.get("custom_audio_id", "")).strip()
        custom_video_id = str(form.get("custom_video_id", "")).strip()
        custom_thumbnail_id = str(form.get("custom_thumbnail_id", "")).strip()
        karaoke_operation_id = str(form.get("karaoke_operation_id", "")).strip()
    else:
        payload = await request.json()
        karaoke_operation_id = str(payload.get("karaoke_operation_id", "")).strip()
        req = GenerateTTSRequest(**payload)

    if karaoke_operation_id:
        _set_karaoke_progress(
            karaoke_operation_id,
            user["id"],
            5,
            "Preparando remocao de voz no Levita...",
            status="running",
            stage="removing_vocals",
        )

    script_text = (req.script or "").strip()
    has_custom_audio = bool(custom_audio_id) or bool(custom_audio_upload and custom_audio_upload.filename)
    if req.use_custom_audio and not has_custom_audio:
        raise HTTPException(status_code=400, detail="Usar meu audio esta ativo, mas nenhum arquivo foi enviado.")

    if req.use_custom_audio and req.audio_is_music:
        req.remove_vocals = True
        req.enable_subtitles = True

    if not script_text and not custom_image_uploads and not custom_image_ids and not has_custom_audio:
        raise HTTPException(status_code=400, detail="Sem narracao, envie fotos ou audio para criar um video personalizado.")

    # ── Credit check: estimate duration → deduct credits ──
    from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
    import math
    if has_custom_audio and custom_audio_id:
        from app.services.video_composer import _get_duration as get_audio_duration

        src_audio = _resolve_temp_file(user["id"], custom_audio_id, AUDIO_EXTS)
        audio_seconds = get_audio_duration(str(src_audio)) if src_audio else 0
        est_minutes = max(1, math.ceil(audio_seconds / 60)) if audio_seconds > 0 else 1
    elif script_text:
        word_count = len(script_text.split())
        est_minutes = max(1, math.ceil(word_count / 150))  # ~150 words/min narration
    else:
        est_minutes = 1  # photo-only / audio-only fallback: minimum 1 min
    credits_needed = est_minutes * CREDITS_PER_MINUTE
    await deduct_credits(db, user["id"], credits_needed)

    # Resolve voice from profile or direct parameter
    voice = req.voice or "onyx"
    tts_instructions = ""
    voice_type = req.voice_type or "builtin"
    is_suno_narration = voice_type == "suno" or (req.voice or "").startswith("suno_narrator_")
    if is_suno_narration:
        voice_type = "suno"
        voice = req.voice  # e.g. "suno_narrator_male_deep"
    elif req.voice_profile_id:
        profile = await db.get(VoiceProfile, req.voice_profile_id)
        if profile and profile.user_id == user["id"]:
            if profile.openai_voice_id:
                voice = profile.openai_voice_id
                voice_type = "custom"
            elif profile.builtin_voice:
                voice = profile.builtin_voice
            tts_instructions = profile.tts_instructions or ""
    elif not req.voice:
        # Try user's default voice profile
        from sqlalchemy import select
        result = await db.execute(
            select(VoiceProfile).where(
                VoiceProfile.user_id == user["id"],
                VoiceProfile.is_default == True
            )
        )
        default_profile = result.scalar_one_or_none()
        if default_profile:
            if default_profile.openai_voice_id:
                voice = default_profile.openai_voice_id
                voice_type = "custom"
            elif default_profile.builtin_voice:
                voice = default_profile.builtin_voice
            tts_instructions = default_profile.tts_instructions or ""

    # Create project first to get an ID for the audio path
    has_custom_images = len(custom_image_uploads) > 0 or len(custom_image_ids) > 0
    has_custom_audio = req.use_custom_audio and has_custom_audio
    has_custom_video = bool(custom_video_id)
    image_display_seconds = req.image_display_seconds if req.image_display_seconds and req.image_display_seconds > 0 else 0
    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=req.title or "Video com IA",
        description="",
        tags=[],
        style_prompt=req.style_prompt or "cinematic, vibrant colors, dynamic lighting",
        aspect_ratio=req.aspect_ratio,
        track_title=req.title or ("Video enviado" if has_custom_video else "Audio enviado" if has_custom_audio else "Narração IA"),
        track_artist="Usuario" if (has_custom_audio or has_custom_video) else "CriaVideo AI",
        track_duration=0,
        lyrics_text=req.script,
        lyrics_words=[],
        audio_path="",
        use_custom_images=has_custom_images and not has_custom_video,
        use_custom_video=has_custom_video,
        enable_subtitles=req.enable_subtitles,
        zoom_images=req.zoom_images,
        image_display_seconds=image_display_seconds,
        no_background_music=(req.no_background_music or has_custom_audio or has_custom_video),
        is_karaoke=(req.use_custom_audio and req.audio_is_music and req.remove_vocals),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # Save custom images uploaded by user (max 20, max 10MB each)
    if custom_image_uploads or custom_image_ids:
        img_dir = Path(settings.media_dir) / "images" / str(project.id)
        img_dir.mkdir(parents=True, exist_ok=True)
        idx = 0

        for upload_id in custom_image_ids[:20]:
            try:
                src = _resolve_temp_file(user["id"], upload_id, IMAGE_EXTS)
                if not src:
                    logger.warning(f"Invalid temp image ID for project {project.id}: {upload_id}")
                    continue
                ext = src.suffix.lower()
                target = img_dir / f"user_{idx:03d}{ext}"
                shutil.copy2(src, target)
                idx += 1
            except Exception as e:
                logger.warning(f"Failed to move temp image {upload_id} for project {project.id}: {e}")

        allowed_ext = IMAGE_EXTS
        for img_upload in custom_image_uploads[: max(20 - idx, 0)]:
            try:
                ext = Path(img_upload.filename).suffix.lower()
                if ext not in allowed_ext:
                    ext = ".jpg"
                target = img_dir / f"user_{idx:03d}{ext}"
                content = await img_upload.read()
                if len(content) > 10 * 1024 * 1024:
                    logger.warning(f"Skipping image {img_upload.filename}: exceeds 10MB")
                    continue
                with open(target, "wb") as f:
                    f.write(content)
                logger.info(f"Saved custom image {idx} for project {project.id}: {target}")
                idx += 1
            except Exception as e:
                logger.warning(f"Failed to save custom image {idx} for project {project.id}: {e}")

    # Save custom video uploaded by user
    custom_video_path = ""
    if has_custom_video and custom_video_id:
        try:
            src = _resolve_temp_file(user["id"], custom_video_id, VIDEO_EXTS)
            if not src:
                raise HTTPException(status_code=400, detail="Video enviado nao foi encontrado.")
            vid_dir = Path(settings.media_dir) / "videos" / str(project.id)
            vid_dir.mkdir(parents=True, exist_ok=True)
            ext = src.suffix.lower() if src.suffix else ".mp4"
            target = vid_dir / f"user_video{ext}"
            shutil.copy2(src, target)
            custom_video_path = str(target)

            from app.services.video_composer import _get_duration as get_video_duration
            vid_dur = get_video_duration(custom_video_path)
            if vid_dur > 0:
                project.track_duration = round(vid_dur)
            logger.info(f"Custom video saved for project {project.id}: {custom_video_path} ({vid_dur:.1f}s)")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Failed to save custom video for project {project.id}: {e}")
            raise HTTPException(status_code=400, detail=f"Falha ao processar video enviado: {e}")

    # Save custom thumbnail uploaded by user
    if custom_thumbnail_id:
        try:
            src = _resolve_temp_file(user["id"], custom_thumbnail_id, IMAGE_EXTS)
            if src:
                thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project.id)
                thumb_dir.mkdir(parents=True, exist_ok=True)
                ext = src.suffix.lower() if src.suffix else ".jpg"
                target = thumb_dir / f"custom_thumbnail{ext}"
                shutil.copy2(src, target)
                logger.info(f"Custom thumbnail saved for project {project.id}: {target}")
        except Exception as e:
            logger.warning(f"Failed to save custom thumbnail for project {project.id}: {e}")

    # Save optional custom background music. The pipeline will prioritize this file over Suno.
    custom_bgm_path = ""
    if bgm_upload and bgm_upload.filename:
        try:
            ext = Path(bgm_upload.filename).suffix.lower()
            if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"}:
                ext = ".mp3"
            music_dir = Path(settings.media_dir) / "audio" / str(project.id)
            music_dir.mkdir(parents=True, exist_ok=True)
            target = music_dir / f"custom_background_music{ext}"
            with open(target, "wb") as f:
                f.write(await bgm_upload.read())
            custom_bgm_path = str(target)
            logger.info(f"Custom background music uploaded for project {project.id}: {target}")
        except Exception as e:
            logger.warning(f"Failed to save custom background music for project {project.id}: {e}")
    elif background_music_id:
        try:
            src = _resolve_temp_file(user["id"], background_music_id, AUDIO_EXTS)
            if src:
                ext = src.suffix.lower()
                music_dir = Path(settings.media_dir) / "audio" / str(project.id)
                music_dir.mkdir(parents=True, exist_ok=True)
                target = music_dir / f"custom_background_music{ext}"
                shutil.copy2(src, target)
                custom_bgm_path = str(target)
                logger.info(f"Custom temp background music moved for project {project.id}: {target}")
        except Exception as e:
            logger.warning(f"Failed to move custom temp background music for project {project.id}: {e}")

    # Save optional custom main audio. If present, this becomes the primary video track.
    custom_main_audio_path = ""
    if has_custom_audio:
        try:
            audio_dir = Path(settings.media_dir) / "audio" / str(project.id)
            audio_dir.mkdir(parents=True, exist_ok=True)

            source_path = None
            ext = ".mp3"

            if custom_audio_id:
                source_path = _resolve_temp_file(user["id"], custom_audio_id, AUDIO_EXTS)
                if not source_path:
                    raise HTTPException(status_code=400, detail="Audio enviado nao foi encontrado.")
                ext = source_path.suffix.lower() if source_path.suffix else ".mp3"

            target = audio_dir / f"user_main_audio{ext}"

            if source_path:
                shutil.copy2(source_path, target)
            elif custom_audio_upload and custom_audio_upload.filename:
                ext = Path(custom_audio_upload.filename).suffix.lower()
                if ext not in AUDIO_EXTS:
                    ext = ".mp3"
                target = audio_dir / f"user_main_audio{ext}"
                with open(target, "wb") as f:
                    f.write(await custom_audio_upload.read())
            else:
                raise HTTPException(status_code=400, detail="Audio principal nao enviado.")

            custom_main_audio_path = str(target)
            project.audio_path = custom_main_audio_path

            from app.services.video_composer import _get_duration as get_audio_duration

            audio_dur = get_audio_duration(custom_main_audio_path)
            project.track_duration = round(audio_dur) if audio_dur > 0 else 0
            logger.info(f"Custom main audio saved for project {project.id}: {custom_main_audio_path}")

            # For karaoke/music mode, transcribe original audio before optional vocal removal.
            if req.audio_is_music:
                try:
                    from app.services.transcriber import transcribe_audio
                    import asyncio

                    # Pass user-provided lyrics as prompt to guide Whisper accuracy
                    lyrics_hint = (project.lyrics_text or "").strip()
                    transcribed = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: transcribe_audio(custom_main_audio_path, prompt=lyrics_hint),
                    )
                    words = transcribed.get("words", []) if isinstance(transcribed, dict) else []
                    text = (transcribed.get("text", "") if isinstance(transcribed, dict) else "").strip()
                    if words:
                        project.lyrics_words = words
                    if text and not (project.lyrics_text or "").strip():
                        project.lyrics_text = text
                    logger.info(f"Karaoke transcription ready for project {project.id}: {len(words)} words")
                except Exception as e:
                    logger.warning(f"Failed to transcribe custom music for project {project.id}: {e}")

                if req.remove_vocals:
                    from app.services.audio_tools import remove_vocals_track

                    levita_auth_token = ""
                    auth_header = str(request.headers.get("authorization") or "").strip()
                    if auth_header.lower().startswith("bearer "):
                        levita_auth_token = auth_header.split(" ", 1)[1].strip()
                    if not levita_auth_token:
                        levita_auth_token = str(request.cookies.get("token") or "").strip()
                    if not levita_auth_token:
                        levita_auth_token = str(settings.levita_api_token or "").strip()

                    async def _karaoke_progress_callback(progress: int, message: str):
                        if not karaoke_operation_id:
                            return
                        _set_karaoke_progress(
                            karaoke_operation_id,
                            user["id"],
                            progress,
                            message,
                            status="running",
                            stage="removing_vocals",
                        )

                    try:
                        instrumental_path = await remove_vocals_track(
                            custom_main_audio_path,
                            project.id,
                            auth_token=levita_auth_token,
                            allow_ffmpeg_fallback=False,
                            progress_callback=_karaoke_progress_callback,
                        )
                    except Exception as sep_err:
                        logger.warning(f"Karaoke vocal removal failed on Olevita for project {project.id}: {sep_err}")
                        if karaoke_operation_id:
                            _set_karaoke_progress(
                                karaoke_operation_id,
                                user["id"],
                                100,
                                "Falha ao remover voz no Levita.",
                                status="failed",
                                stage="removing_vocals",
                                error=str(sep_err),
                            )
                        raise HTTPException(
                            status_code=502,
                            detail="Nao foi possivel concluir a remocao de voz agora. Tente novamente em alguns minutos.",
                        )

                    if not instrumental_path or not os.path.exists(instrumental_path):
                        if karaoke_operation_id:
                            _set_karaoke_progress(
                                karaoke_operation_id,
                                user["id"],
                                100,
                                "Nao foi possivel baixar o audio sem voz.",
                                status="failed",
                                stage="removing_vocals",
                                error="instrumental_output_missing",
                            )
                        raise HTTPException(status_code=500, detail="Nao foi possivel remover a voz do audio.")

                    if karaoke_operation_id:
                        _set_karaoke_progress(
                            karaoke_operation_id,
                            user["id"],
                            100,
                            "Voz removida com sucesso.",
                            status="completed",
                            stage="removing_vocals",
                        )

                    project.audio_path = instrumental_path
                    logger.info(f"Karaoke instrumental created for project {project.id}: {instrumental_path}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Failed to save custom main audio for project {project.id}: {e}")
            raise HTTPException(status_code=400, detail=f"Falha ao processar audio enviado: {e}")

    try:
        if custom_main_audio_path:
            if project.track_duration <= 0:
                from app.services.video_composer import _get_duration as get_audio_duration

                custom_duration = get_audio_duration(project.audio_path)
                project.track_duration = round(custom_duration) if custom_duration > 0 else 60
        elif script_text:
            if is_suno_narration:
                from app.services.suno_narration import generate_suno_narration
                audio_path = await generate_suno_narration(
                    text=req.script,
                    voice_preset=voice,
                    project_id=project.id,
                    tone=req.tone,
                )
                if not audio_path:
                    raise Exception("Falha ao gerar narracao Suno AI. Tente novamente.")
                project.audio_path = audio_path
                # Suno narration includes background music — skip separate BGM
                project.no_background_music = True
            else:
                audio_path = await generate_tts_audio(
                    text=req.script,
                    voice=voice,
                    project_id=project.id,
                    tts_instructions=tts_instructions,
                    voice_type=voice_type,
                    pause_level=req.pause_level,
                    tone=req.tone,
                )
                project.audio_path = audio_path

            # Estimate duration from word count (~2.5 words/sec for TTS)
            word_count = len(req.script.split())
            project.track_duration = round(word_count / 2.5)
        else:
            if custom_bgm_path:
                from app.services.video_composer import _get_duration as get_audio_duration

                project.audio_path = custom_bgm_path
                bgm_duration = get_audio_duration(custom_bgm_path)
                project.track_duration = round(bgm_duration) if bgm_duration > 0 else 60
            else:
                # No narration + no uploaded music: pipeline will generate instrumental music automatically.
                project.audio_path = ""
                project.track_duration = 0
            if not has_custom_video:
                project.enable_subtitles = False

        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 0
        await db.commit()

        from app.tasks.video_tasks import run_video_pipeline
        background_tasks.add_task(run_video_pipeline, project.id)

        return {
            "id": project.id,
            "title": project.title,
            "status": "generating_scenes",
            "estimated_duration": project.track_duration,
        }
    except HTTPException:
        project.status = VideoStatus.FAILED
        project.error_message = "Configuracao invalida para geracao de audio"
        await db.commit()
        raise
    except Exception as e:
        project.status = VideoStatus.FAILED
        project.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Erro ao gerar audio: {e}")


# ── Realistic Video (Seedance 2.0) ──────────────────────────────


class GenerateRealisticPromptRequest(BaseModel):
    topic: str
    style: str = "cinematic"
    engine: str = "seedance"
    has_reference_image: bool = False


@router.post("/generate-realistic-prompt")
async def generate_realistic_prompt_endpoint(
    req: GenerateRealisticPromptRequest,
    user: dict = Depends(get_current_user),
):
    """Generate an optimized Seedance 2.0 prompt from a simple topic/theme."""
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Descreva o tema do video.")
    if len(topic) > 2000:
        raise HTTPException(status_code=400, detail="Tema muito longo (maximo 2000 caracteres).")

    engine = req.engine if req.engine in ("seedance", "minimax", "wan2", "grok") else "seedance"
    prompt_for_optimizer = _ensure_reference_image_instruction(topic) if req.has_reference_image else topic

    if engine == "grok":
        from app.services.grok_video import optimize_prompt_for_grok

        optimized = await optimize_prompt_for_grok(
            user_description=prompt_for_optimizer,
            duration=7,
            has_reference_image=req.has_reference_image,
        )
    else:
        from app.services.seedance_video import optimize_prompt_for_seedance

        optimized = await optimize_prompt_for_seedance(
            user_description=prompt_for_optimizer,
            duration=7,
            tone=req.style,
            has_reference_image=req.has_reference_image,
        )

    if req.has_reference_image:
        optimized = _ensure_reference_image_instruction(optimized)

    return {"prompt": optimized}


class GenerateRealisticRequest(BaseModel):
    prompt: str
    duration: int = 7
    aspect_ratio: str = "16:9"
    generate_audio: bool = True
    add_music: bool = True
    add_narration: bool = False
    narration_text: str = ""
    narration_voice: str = "onyx"
    title: str = ""
    image_upload_id: str = ""
    engine: str = "seedance"  # "seedance" or "minimax"
    audio_url: str = ""       # External audio URL (e.g. from Tevoxi)
    lyrics: str = ""          # Lyrics/transcription for the audio clip
    clip_start: float = 0     # Start time in seconds for audio clip
    clip_duration: float = 0  # Duration of the audio clip (0 = full)
    prompt_optimized: bool = False
    realistic_style: str = ""
    interaction_persona: str = "natureza"


@router.post("/generate-realistic")
async def generate_realistic_endpoint(
    req: GenerateRealisticRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a realistic AI video using Seedance 2.0, MiniMax Hailuo, or Wan 2.2."""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Descreva a cena que voce quer ver no video.")
    if len(prompt) > 5000:
        raise HTTPException(status_code=400, detail="Descricao muito longa (maximo 5000 caracteres).")

    engine = req.engine if req.engine in ("seedance", "minimax", "wan2", "grok") else "seedance"
    max_dur = 60 if engine == "grok" else 10
    duration = max(1, min(req.duration, max_dur))

    if req.aspect_ratio not in {"16:9", "9:16", "1:1"}:
        raise HTTPException(status_code=400, detail="Formato invalido. Use 16:9, 9:16 ou 1:1.")

    # Resolve reference image if provided
    image_path_str = ""
    if req.image_upload_id:
        resolved = _resolve_temp_file(user["id"], req.image_upload_id, IMAGE_EXTS)
        if not resolved:
            raise HTTPException(status_code=400, detail="Imagem de referencia nao encontrada. Envie a foto novamente.")
        image_path_str = str(resolved)
    has_reference_image = bool(image_path_str)
    if has_reference_image:
        prompt = _ensure_reference_image_instruction(prompt)

    # Credit check — multi-clip costs more (1 credit per 15s segment)
    from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
    num_clips = -(-duration // 15) if engine == "grok" and duration > 15 else 1
    credits_needed = CREDITS_PER_MINUTE * num_clips
    await deduct_credits(db, user["id"], credits_needed)

    # Use custom title if provided
    project_title = (req.title or "").strip()
    if not project_title:
        project_title = prompt[:100]

    engine_labels = {"minimax": "MiniMax Hailuo", "wan2": "Wan 2.2", "seedance": "Seedance 2.0", "grok": "Grok"}
    engine_label = engine_labels.get(engine, "Seedance 2.0")

    # Narration config stored in tags JSON
    narration_text = (req.narration_text or "").strip() if req.add_narration else ""
    narration_voice = req.narration_voice or "onyx"
    interaction_persona = _normalize_interaction_persona(req.interaction_persona)
    external_audio_url = (req.audio_url or "").strip()
    external_lyrics = (req.lyrics or "").strip()
    tags_data = {
        "type": "realista",
        "engine": engine,
        "has_reference_image": has_reference_image,
        "add_music": req.add_music or bool(external_audio_url),
        "add_narration": req.add_narration and bool(narration_text),
        "narration_voice": narration_voice,
        "prompt_optimized": bool(req.prompt_optimized),
        "realistic_style": (req.realistic_style or "").strip(),
        "interaction_persona": interaction_persona,
    }
    if external_audio_url:
        tags_data["audio_url"] = external_audio_url
        tags_data["clip_start"] = req.clip_start
        tags_data["clip_duration"] = req.clip_duration
    if external_lyrics:
        tags_data["lyrics"] = external_lyrics

    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=project_title,
        description=narration_text,
        tags=tags_data,
        style_prompt=image_path_str,
        aspect_ratio=req.aspect_ratio,
        track_title=project_title,
        track_artist=engine_label,
        track_duration=float(duration),
        lyrics_text=prompt,
        lyrics_words=[],
        audio_path=engine,
        is_realistic=True,
        no_background_music=not req.add_music,
        enable_subtitles=False,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    await db.commit()

    from app.tasks.video_tasks import run_realistic_video_pipeline
    background_tasks.add_task(run_realistic_video_pipeline, project.id)

    return {
        "id": project.id,
        "title": project.title,
        "status": "generating_scenes",
        "duration": duration,
    }
