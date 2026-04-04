"""
Publish Router — Endpoints for publishing videos to social platforms.
"""
import os
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import openai
from app.auth import get_current_user
from app.database import get_db
from app.models import PublishJob, PublishStatus, VideoProject, VideoRender, SocialAccount, Platform
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/publish", tags=["publish"])
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)


class PublishRequest(BaseModel):
    render_id: int
    platforms: list[str]  # ["youtube", "tiktok", "instagram"]
    title: str = ""
    description: str = ""
    tags: list[str] = []
    scheduled_at: Optional[str] = None  # ISO datetime or null for immediate


@router.post("/")
async def publish_video(
    req: PublishRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create publish jobs for one or more platforms."""
    # Verify render exists and belongs to user
    render = await db.get(VideoRender, req.render_id)
    if not render:
        raise HTTPException(status_code=404, detail="Render not found")

    scheduled = None
    if req.scheduled_at:
        scheduled = datetime.fromisoformat(req.scheduled_at)

    jobs = []
    for platform_name in req.platforms:
        try:
            platform = Platform(platform_name)
        except ValueError:
            continue

        # Find connected account for this platform
        result = await db.execute(
            select(SocialAccount)
            .where(SocialAccount.user_id == user["id"])
            .where(SocialAccount.platform == platform)
            .limit(1)
        )
        account = result.scalar_one_or_none()
        if not account:
            jobs.append({"platform": platform_name, "error": "No connected account"})
            continue

        job = PublishJob(
            user_id=user["id"],
            render_id=req.render_id,
            platform=platform,
            social_account_id=account.id,
            title=req.title,
            description=req.description,
            tags=req.tags,
            scheduled_at=scheduled,
            status=PublishStatus.SCHEDULED if scheduled else PublishStatus.PENDING,
        )
        db.add(job)
        await db.flush()

        if not scheduled:
            from app.tasks.publish_tasks import run_publish_job
            background_tasks.add_task(run_publish_job, job.id)

        jobs.append({"platform": platform_name, "job_id": job.id, "status": job.status.value})

    await db.commit()
    return {"jobs": jobs}


@router.get("/jobs")
async def list_publish_jobs(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all publish jobs for the current user."""
    result = await db.execute(
        select(PublishJob)
        .where(PublishJob.user_id == user["id"])
        .order_by(PublishJob.created_at.desc())
        .limit(50)
    )
    jobs = result.scalars().all()
    return [
        {
            "id": j.id,
            "platform": j.platform.value,
            "status": j.status.value,
            "title": j.title,
            "scheduled_at": j.scheduled_at.isoformat() if j.scheduled_at else None,
            "published_at": j.published_at.isoformat() if j.published_at else None,
            "platform_url": j.platform_url,
            "error_message": j.error_message,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_publish_job(
    job_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get publish job status."""
    job = await db.get(PublishJob, job_id)
    if not job or job.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "platform": job.platform.value,
        "status": job.status.value,
        "title": job.title,
        "description": job.description,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "published_at": job.published_at.isoformat() if job.published_at else None,
        "platform_post_id": job.platform_post_id,
        "platform_url": job.platform_url,
        "error_message": job.error_message,
    }


class AISuggestRequest(BaseModel):
    render_id: int


@router.post("/ai-suggest")
async def ai_suggest(
    req: AISuggestRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate viral title, description, hashtags using AI."""
    render = await db.get(VideoRender, req.render_id)
    if not render:
        raise HTTPException(status_code=404, detail="Render not found")

    project = await db.get(VideoProject, render.project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    # Build context for AI
    context_parts = []
    if project.title:
        context_parts.append(f"Titulo do projeto: {project.title}")
    if project.track_title:
        context_parts.append(f"Musica: {project.track_title}")
    if project.track_artist:
        context_parts.append(f"Artista: {project.track_artist}")
    if project.style_prompt:
        context_parts.append(f"Estilo visual: {project.style_prompt}")
    if project.lyrics_text:
        lyrics_preview = project.lyrics_text[:500]
        context_parts.append(f"Letra da musica:\n{lyrics_preview}")
    if project.description:
        context_parts.append(f"Descricao do projeto: {project.description}")

    context = "\n".join(context_parts) or "Video musical sem detalhes adicionais"

    prompt = f"""Voce e um especialista em marketing digital e viralizacao no YouTube, TikTok e Instagram.
Com base nas informacoes abaixo sobre um video musical, gere sugestoes OTIMIZADAS para MÁXIMO alcance e viralizacao.

INFORMACOES DO VIDEO:
{context}

Responda EXATAMENTE neste formato JSON (sem markdown, sem ```):
{{
  "title": "Um titulo CHAMATIVO, CURTO (max 80 chars), que gere curiosidade e cliques. Use emojis estrategicamente. Deve funcionar no YouTube, TikTok e Instagram.",
  "description": "Uma descricao ENVOLVENTE de 2-3 paragrafos que: 1) Capte atencao nos primeiros 2 segundos de leitura, 2) Conte uma mini-historia ou gere emocao, 3) Inclua call-to-action (curtir, compartilhar, inscrever). Max 300 palavras. Em portugues BR.",
  "hashtags": "#hashtag1 #hashtag2 #hashtag3 ... (15-20 hashtags relevantes e populares em portugues e ingles, misturando nicho e alcance amplo)",
  "tags": ["tag1", "tag2", "tag3", "..."]
}}

REGRAS:
- Titulo deve ser IMPOSSIVEL de ignorar no feed
- Descricao deve ter tom emocional e pessoal
- Hashtags devem misturar tags populares (#music #viral #fyp) com tags de nicho
- Tags para SEO do YouTube (10-15 tags relevantes)
- Tudo em portugues BR (exceto hashtags universais em ingles)"""

    try:
        resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=1000,
        )
        import json
        raw = resp.choices[0].message.content.strip()
        # Remove markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        data = json.loads(raw)
        return {
            "title": data.get("title", project.title or ""),
            "description": data.get("description", ""),
            "hashtags": data.get("hashtags", ""),
            "tags": data.get("tags", []),
        }
    except Exception as e:
        logger.error(f"AI suggest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar sugestoes: {e}")


class ThumbnailRequest(BaseModel):
    render_id: int


@router.post("/generate-thumbnail")
async def generate_publish_thumbnail(
    req: ThumbnailRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a viral thumbnail for the video using Nano Banana (Gemini)."""
    render = await db.get(VideoRender, req.render_id)
    if not render:
        raise HTTPException(status_code=404, detail="Render not found")

    project = await db.get(VideoProject, render.project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    # Build output path
    thumb_dir = os.path.join(settings.media_dir, "thumbnails", str(project.id))
    os.makedirs(thumb_dir, exist_ok=True)
    output_path = os.path.join(thumb_dir, f"publish_thumb_{render.id}.jpg")

    title = project.track_title or project.title or "Music Video"
    artist = project.track_artist or ""
    mood = ""
    style_hint = project.style_prompt or ""

    # If we have lyrics, extract a mood hint
    if project.lyrics_text:
        lyrics_preview = project.lyrics_text[:200]
        mood = lyrics_preview

    try:
        import asyncio
        from app.services.thumbnail_generator import generate_thumbnail

        path = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_thumbnail(
                title=title,
                artist=artist,
                mood=mood,
                style_hint=style_hint,
                output_path=output_path,
            ),
        )

        # Update render thumbnail_path
        render.thumbnail_path = path
        await db.commit()

        # Convert to URL
        media_prefix = settings.media_dir.rstrip("/")
        thumb_url = None
        if path.startswith(media_prefix):
            thumb_url = "/video/media" + path[len(media_prefix):]

        return {"thumbnail_url": thumb_url, "thumbnail_path": path}

    except Exception as e:
        logger.error(f"Thumbnail generation failed: {e}", exc_info=True)
        # Fallback: try frame extraction if render has a video file
        if render.file_path and os.path.exists(render.file_path):
            try:
                import asyncio
                from app.services.thumbnail_generator import generate_thumbnail_from_frame

                path = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: generate_thumbnail_from_frame(
                        video_path=render.file_path,
                        title=title,
                        artist=artist,
                        output_path=output_path,
                    ),
                )
                render.thumbnail_path = path
                await db.commit()

                media_prefix = settings.media_dir.rstrip("/")
                thumb_url = None
                if path.startswith(media_prefix):
                    thumb_url = "/video/media" + path[len(media_prefix):]

                return {"thumbnail_url": thumb_url, "thumbnail_path": path}
            except Exception as e2:
                logger.error(f"Fallback thumbnail also failed: {e2}", exc_info=True)

        raise HTTPException(status_code=500, detail=f"Erro ao gerar thumbnail: {e}")
