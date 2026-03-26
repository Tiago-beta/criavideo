"""
Video Router — Endpoints for creating video projects, generating scenes/renders.
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
from app.auth import get_current_user
from app.database import get_db
from app.models import VideoProject, VideoScene, VideoRender, VideoStatus
from app.config import get_settings

router = APIRouter(prefix="/api/video", tags=["video"])
settings = get_settings()


class CreateProjectRequest(BaseModel):
    track_id: int
    title: str
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
