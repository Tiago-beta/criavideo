"""
Video Router — Endpoints for creating video projects, generating scenes/renders.
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
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
        .where(VideoProject.user_id == user["id"])
        .order_by(VideoProject.created_at.desc())
    )
    projects = result.scalars().all()
    return [
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
            "lyrics_text": p.lyrics_text or "",
            "style_prompt": p.style_prompt or "",
        }
        for p in projects
    ]


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
        select(VideoRender).where(VideoRender.project_id == project_id)
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
                "video_url": _to_media_url(r.file_path),
                "thumbnail_url": _to_media_url(r.thumbnail_path),
            }
            for r in renders
        ],
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

    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    project.error_message = None
    await db.commit()

    from app.tasks.video_tasks import run_video_pipeline
    background_tasks.add_task(run_video_pipeline, project_id)

    return {"status": "started", "project_id": project_id}


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
    # Ask AI to generate creative metadata from song info
    ai_prompt = f"""Você é um produtor criativo de vídeos musicais.
Com base nos dados desta música, gere metadados criativos para um videoclipe.

Título da música: {req.song_title or 'Desconhecido'}
Artista: {req.song_artist or 'Desconhecido'}
Duração: {req.duration:.0f} segundos
Trecho da letra:
{(req.lyrics or 'Sem letra disponível')[:800]}

Responda SOMENTE um JSON com:
- "title": título curto e criativo para o projeto de vídeo (máx 60 chars, em português)
- "description": descrição envolvente para redes sociais (máx 200 chars, em português)
- "style_prompt": prompt em INGLÊS descrevendo o estilo visual ideal (cores, cenário, mood, iluminação — máx 120 chars)
- "tags": lista de 3-5 tags relevantes em português

JSON apenas, sem markdown."""

    title = req.song_title or "Meu Vídeo"
    description = ""
    style_prompt = "cinematic, vibrant colors, dynamic lighting"
    tags = []

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


class GenerateScriptRequest(BaseModel):
    topic: str
    tone: str = "informativo"
    duration_seconds: int = 60


class GenerateTTSRequest(BaseModel):
    script: str
    voice: str = ""
    voice_profile_id: int = 0
    title: str = ""
    aspect_ratio: str = "16:9"
    style_prompt: str = ""


@router.post("/generate-script")
async def generate_script_endpoint(
    req: GenerateScriptRequest,
    user: dict = Depends(get_current_user),
):
    """Generate a video narration script using AI."""
    from app.services.script_audio import generate_script
    result = await generate_script(
        topic=req.topic,
        tone=req.tone,
        duration_seconds=req.duration_seconds,
    )
    return result


@router.post("/generate-audio")
async def generate_audio_endpoint(
    req: GenerateTTSRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate TTS audio from script, create project, and start video pipeline."""
    from app.services.script_audio import generate_tts_audio
    from app.models import VoiceProfile

    # Resolve voice from profile or direct parameter
    voice = req.voice or "onyx"
    tts_instructions = ""

    if req.voice_profile_id:
        profile = await db.get(VoiceProfile, req.voice_profile_id)
        if profile and profile.user_id == user["id"]:
            if profile.openai_voice_id:
                voice = profile.openai_voice_id
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
            elif default_profile.builtin_voice:
                voice = default_profile.builtin_voice
            tts_instructions = default_profile.tts_instructions or ""

    # Create project first to get an ID for the audio path
    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=req.title or "Video com IA",
        description="",
        tags=[],
        style_prompt=req.style_prompt or "cinematic, vibrant colors, dynamic lighting",
        aspect_ratio=req.aspect_ratio,
        track_title=req.title or "Narração IA",
        track_artist="CriaVideo AI",
        track_duration=0,
        lyrics_text=req.script,
        lyrics_words=[],
        audio_path="",
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    try:
        audio_path = await generate_tts_audio(
            text=req.script,
            voice=voice,
            project_id=project.id,
            tts_instructions=tts_instructions,
        )
        project.audio_path = audio_path

        # Estimate duration from word count (~2.5 words/sec for TTS)
        word_count = len(req.script.split())
        project.track_duration = round(word_count / 2.5)

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
    except Exception as e:
        project.status = VideoStatus.FAILED
        project.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Erro ao gerar audio: {e}")
