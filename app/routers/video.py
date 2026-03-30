"""
Video Router — Endpoints for creating video projects, generating scenes/renders.
"""
import json
import logging
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
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
            "thumbnail_url": _to_media_url(p.renders[0].thumbnail_path) if p.renders else None,
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


class FixTextRequest(BaseModel):
    text: str


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
    pause_level: str = "normal"
    enable_subtitles: bool = True


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
    result = await generate_script(
        topic=req.topic,
        tone=req.tone,
        duration_seconds=req.duration_seconds,
    )
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
    custom_image_uploads: list[UploadFile] = []
    if "multipart/form-data" in content_type:
        form = await request.form()
        enable_sub_raw = str(form.get("enable_subtitles", "true")).lower()
        req = GenerateTTSRequest(
            script=str(form.get("script", "")),
            voice=str(form.get("voice", "")),
            voice_profile_id=int(form.get("voice_profile_id", 0) or 0),
            title=str(form.get("title", "")),
            aspect_ratio=str(form.get("aspect_ratio", "16:9")),
            style_prompt=str(form.get("style_prompt", "")),
            pause_level=str(form.get("pause_level", "normal")),
            enable_subtitles=enable_sub_raw not in ("false", "0", "no"),
        )
        raw_upload = form.get("background_music")
        if isinstance(raw_upload, UploadFile) and raw_upload.filename:
            bgm_upload = raw_upload
        elif getattr(raw_upload, "filename", ""):
            bgm_upload = raw_upload

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
    else:
        payload = await request.json()
        req = GenerateTTSRequest(**payload)

    script_text = (req.script or "").strip()
    if not script_text and not custom_image_uploads:
        raise HTTPException(status_code=400, detail="Sem narracao, envie fotos para criar um video personalizado.")
    if not script_text and bgm_upload is None:
        raise HTTPException(status_code=400, detail="Sem narracao, envie um fundo musical para criar o video somente com fotos.")

    # Resolve voice from profile or direct parameter
    voice = req.voice or "onyx"
    tts_instructions = ""
    voice_type = "builtin"

    if req.voice_profile_id:
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
    has_custom_images = len(custom_image_uploads) > 0
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
        use_custom_images=has_custom_images,
        enable_subtitles=req.enable_subtitles,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # Save custom images uploaded by user (max 20, max 10MB each)
    if custom_image_uploads:
        img_dir = Path(settings.media_dir) / "images" / str(project.id)
        img_dir.mkdir(parents=True, exist_ok=True)
        allowed_ext = {".jpg", ".jpeg", ".png", ".webp"}
        for idx, img_upload in enumerate(custom_image_uploads[:20]):
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
            except Exception as e:
                logger.warning(f"Failed to save custom image {idx} for project {project.id}: {e}")

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

    try:
        if script_text:
            audio_path = await generate_tts_audio(
                text=req.script,
                voice=voice,
                project_id=project.id,
                tts_instructions=tts_instructions,
                voice_type=voice_type,
                pause_level=req.pause_level,
            )
            project.audio_path = audio_path

            # Estimate duration from word count (~2.5 words/sec for TTS)
            word_count = len(req.script.split())
            project.track_duration = round(word_count / 2.5)
        else:
            if not custom_bgm_path:
                raise HTTPException(status_code=400, detail="Sem narracao, envie um fundo musical valido.")
            from app.services.video_composer import _get_duration as get_audio_duration

            project.audio_path = custom_bgm_path
            bgm_duration = get_audio_duration(custom_bgm_path)
            project.track_duration = round(bgm_duration) if bgm_duration > 0 else 60
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
