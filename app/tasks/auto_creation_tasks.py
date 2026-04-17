"""
Auto-creation tasks — Automated video generation triggered by scheduler.
"""
import asyncio
import logging
import math
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.config import get_settings
from app.models import (
    AutoSchedule, AutoScheduleTheme, VideoProject, VideoStatus,
    PublishJob, PublishStatus, SocialAccount, VideoRender,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# Default settings for auto mode when AI doesn't specify
_AUTO_DEFAULTS = {
    "tone": "informativo",
    "voice": "onyx",
    "style_prompt": "cinematic, vibrant colors, dynamic lighting",
    "duration_seconds": 60,
    "aspect_ratio": "16:9",
    "pause_level": "normal",
}


def _strip_lyrics_from_description(text: str) -> str:
    """Remove lyrics-like blocks from publish descriptions and keep it concise."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    markers = [
        "🎵 letra da musica",
        "letra da musica",
        "letra da música",
        "[verso",
        "[refr",
        "[ponte",
        "[bridge",
        "[chorus",
    ]
    lower = cleaned.lower()
    cut_idx = None
    for marker in markers:
        idx = lower.find(marker)
        if idx != -1:
            cut_idx = idx if cut_idx is None else min(cut_idx, idx)
    if cut_idx is not None:
        cleaned = cleaned[:cut_idx].strip()

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return "\n".join(lines[:5]).strip()


async def ai_select_video_settings(theme: str) -> dict:
    """Use GPT-4o-mini to select video settings based on theme."""
    import openai
    import json

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Voce e um diretor de conteudo. Dado um tema de video, escolha as melhores configuracoes. "
                        "REGRA IMPORTANTE: Se o tema for gospel, religioso, cristao, louvor, adoracao ou espiritual, "
                        "o style_prompt DEVE ser 'natureza' para usar paisagens naturais. "
                        "Responda APENAS um JSON valido com: "
                        '{"tone": "informativo|inspirador|descontraido|profundo|dramatico|motivacional", '
                        '"style_prompt": "descricao visual em ingles (ex: cinematic warm tones, minimalist clean) — para gospel/religioso use: natureza", '
                        '"duration_seconds": 60 a 300, '
                        '"suggested_title": "titulo atraente em portugues"}'
                    ),
                },
                {"role": "user", "content": f"Tema: {theme}"},
            ],
            temperature=0.7,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return {
            "tone": data.get("tone", "informativo"),
            "style_prompt": data.get("style_prompt", "cinematic, vibrant colors"),
            "duration_seconds": max(60, min(data.get("duration_seconds", 60), 300)),
            "suggested_title": data.get("suggested_title", theme),
        }
    except Exception as e:
        logger.warning("AI settings selection failed, using defaults: %s", e)
        return {
            "tone": "informativo",
            "style_prompt": "cinematic, vibrant colors, dynamic lighting",
            "duration_seconds": 60,
            "suggested_title": theme,
        }


async def run_auto_creation(auto_schedule_id: int):
    """Main auto-creation pipeline: pick next theme, create video, publish."""
    async with async_session() as db:
        result = await db.execute(
            select(AutoSchedule)
            .options(selectinload(AutoSchedule.themes))
            .where(AutoSchedule.id == auto_schedule_id)
        )
        schedule = result.scalar_one_or_none()
        if not schedule:
            logger.warning("Auto-schedule %d not found", auto_schedule_id)
            return

        if not schedule.is_active:
            return

        # Check if any theme is already processing (prevent duplicates)
        processing = [t for t in schedule.themes if t.status == "processing"]
        if processing:
            logger.info("Auto-schedule %d: theme %d already processing, skipping", auto_schedule_id, processing[0].id)
            return

        # Pick next pending theme (lowest position)
        pending = sorted(
            [t for t in schedule.themes if t.status == "pending"],
            key=lambda t: t.position,
        )
        if not pending:
            logger.info("Auto-schedule %d: no pending themes", auto_schedule_id)
            return

        theme_entry = pending[0]
        theme_entry.status = "processing"
        await db.commit()

        logger.info(
            "Auto-creation started: schedule=%d, theme=%d '%s', mode=%s, type=%s",
            auto_schedule_id, theme_entry.id, theme_entry.theme,
            schedule.creation_mode, schedule.video_type,
        )

    # Run the pipeline outside the DB session to avoid long-held connections
    try:
        project_id = await _create_video_for_theme(
            schedule_id=auto_schedule_id,
            theme_id=theme_entry.id,
            theme_text=theme_entry.theme,
            user_id=schedule.user_id,
            video_type=schedule.video_type,
            creation_mode=schedule.creation_mode,
            default_settings=schedule.default_settings or {},
            custom_settings=theme_entry.custom_settings or {},
        )

        # Wait for video to complete (poll every 10s, max 30 min)
        completed = await _wait_for_project_completion(project_id, timeout_minutes=30)

        if completed:
            # Auto-publish if social account is configured
            if schedule.social_account_id:
                await _auto_publish(
                    project_id=project_id,
                    user_id=schedule.user_id,
                    platform=schedule.platform,
                    social_account_id=schedule.social_account_id,
                )

            async with async_session() as db:
                theme = await db.get(AutoScheduleTheme, theme_entry.id)
                if theme:
                    theme.status = "completed"
                    theme.video_project_id = project_id
                    await db.commit()

            logger.info("Auto-creation completed: schedule=%d, theme=%d, project=%d", auto_schedule_id, theme_entry.id, project_id)
        else:
            async with async_session() as db:
                theme = await db.get(AutoScheduleTheme, theme_entry.id)
                if theme:
                    theme.status = "failed"
                    theme.error_message = "Video rendering timed out or failed"
                    await db.commit()

    except Exception as e:
        logger.error("Auto-creation failed: schedule=%d, theme=%d, error=%s", auto_schedule_id, theme_entry.id, e)
        async with async_session() as db:
            theme = await db.get(AutoScheduleTheme, theme_entry.id)
            if theme:
                theme.status = "failed"
                theme.error_message = str(e)[:500]
                await db.commit()


async def _create_video_for_theme(
    schedule_id: int,
    theme_id: int,
    theme_text: str,
    user_id: int,
    video_type: str,
    creation_mode: str,
    default_settings: dict,
    custom_settings: dict,
) -> int:
    """Create a video project from theme and start the pipeline. Returns project_id."""
    from app.services.script_audio import generate_script, generate_tts_audio
    from app.tasks.video_tasks import run_video_pipeline

    # Merge settings: custom overrides default
    cfg = {**_AUTO_DEFAULTS, **default_settings, **custom_settings}

    if video_type == "music":
        return await _create_music_video(theme_text, user_id, cfg)

    # Narration video
    if creation_mode == "auto":
        ai_settings = await ai_select_video_settings(theme_text)
        cfg["tone"] = ai_settings.get("tone", cfg["tone"])
        cfg["style_prompt"] = ai_settings.get("style_prompt", cfg["style_prompt"])
        cfg["duration_seconds"] = ai_settings.get("duration_seconds", cfg["duration_seconds"])
        title = ai_settings.get("suggested_title", theme_text)
    else:
        title = cfg.get("title", theme_text)

    # 1. Generate script
    script_result = await generate_script(
        topic=theme_text,
        tone=cfg["tone"],
        duration_seconds=cfg["duration_seconds"],
    )
    script_text = script_result.get("script", "")
    if not script_text:
        raise RuntimeError("Script generation returned empty text")

    # 2. Create project
    async with async_session() as db:
        # Credit check
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        word_count = len(script_text.split())
        est_minutes = max(1, math.ceil(word_count / 150))
        credits_needed = est_minutes * CREDITS_PER_MINUTE
        await deduct_credits(db, user_id, credits_needed)

        project = VideoProject(
            user_id=user_id,
            track_id=0,
            title=title,
            description=f"Auto-generated from theme: {theme_text}",
            tags=[],
            style_prompt=cfg["style_prompt"],
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            track_title=title,
            track_artist="",
            track_duration=0,
            lyrics_text=script_text,
            lyrics_words=[],
            audio_path="",
            enable_subtitles=True,
            zoom_images=True,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        project_id = project.id

    # 3. Generate TTS audio
    voice = cfg.get("voice", "onyx")
    audio_path = await generate_tts_audio(
        text=script_text,
        voice=voice,
        project_id=project_id,
        pause_level=cfg.get("pause_level", "normal"),
        tone=cfg["tone"],
    )

    # 4. Update project and start pipeline
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        project.audio_path = audio_path
        word_count = len(script_text.split())
        project.track_duration = round(word_count / 2.5)
        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 0
        await db.commit()

    # 5. Run video pipeline (this runs in the same async context)
    await run_video_pipeline(project_id)

    return project_id


async def _create_music_video(theme_text: str, user_id: int, cfg: dict) -> int:
    """Create a music video: generate music via Tevoxi, then create video from it."""
    from app.services.tevoxi_music import generate_music_from_theme
    from app.tasks.video_tasks import run_video_pipeline

    # Credit check
    async with async_session() as db:
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        est_minutes = max(1, math.ceil(cfg.get("duration_seconds", 120) / 60))
        credits_needed = est_minutes * CREDITS_PER_MINUTE
        await deduct_credits(db, user_id, credits_needed)

    # 1. Generate music via Tevoxi
    manual_music = None
    if cfg.get("music_mode"):
        # Manual music settings from user
        manual_music = {
            "music_mode": cfg.get("music_mode", "generate"),
            "music_mood": cfg.get("music_mood", ""),
            "music_genre": cfg.get("music_genre", "pop"),
            "music_vocalist": cfg.get("music_vocalist", "female"),
            "music_duration": cfg.get("music_duration"),
            "music_language": cfg.get("music_language", "pt-BR"),
            "music_lyrics": cfg.get("music_lyrics", ""),
        }

    music_result = await generate_music_from_theme(
        theme=theme_text,
        project_id=0,
        duration=cfg.get("duration_seconds", 120),
        manual_settings=manual_music,
    )

    audio_path = music_result["audio_path"]
    title = theme_text  # Always use the user's theme as title (in Portuguese)
    lyrics = music_result.get("lyrics", "")
    music_duration = music_result.get("duration", 120)

    # Detect gospel/religious themes and force nature style
    _gospel_keywords = ["deus", "senhor", "jesus", "cristo", "louvor", "adoração",
                        "adoracao", "gospel", "fé", "fe", "oração", "oracao", "céu",
                        "ceu", "espírito", "espirito", "santo", "igreja", "worship",
                        "god", "lord", "faith", "pray", "heaven", "divine", "holy",
                        "ungido", "bíblia", "biblia", "salvação", "salvacao", "graça",
                        "graca", "milagre", "profecia", "glória", "gloria", "aleluia",
                        "hosana", "cordeiro", "redenção", "redencao"]
    _theme_lower = theme_text.lower()
    _lyrics_lower = lyrics.lower() if lyrics else ""
    if any(kw in _theme_lower or kw in _lyrics_lower for kw in _gospel_keywords):
        cfg["style_prompt"] = "natureza"
        logger.info("Gospel theme detected for '%s', using natureza style", theme_text)

    # 2. Create project
    async with async_session() as db:
        project = VideoProject(
            user_id=user_id,
            track_id=0,
            title=title,
            description=f"Auto-generated music video: {theme_text}",
            tags=[],
            style_prompt=cfg.get("style_prompt", "cinematic, vibrant colors, dynamic lighting"),
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            track_title=title,
            track_artist="",
            track_duration=music_duration,
            lyrics_text=lyrics,
            lyrics_words=[],
            audio_path="",
            enable_subtitles=bool(lyrics),
            zoom_images=True,
            no_background_music=True,
            is_karaoke=False,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        project_id = project.id

    # 3. Move audio to project directory
    import shutil
    final_audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    final_audio_dir.mkdir(parents=True, exist_ok=True)
    final_audio_path = final_audio_dir / "tevoxi_music.mp3"
    if audio_path != str(final_audio_path):
        shutil.move(audio_path, final_audio_path)

    # 4. Transcribe for subtitles if lyrics available
    if lyrics:
        try:
            from app.services.transcriber import transcribe_audio
            transcribed = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: transcribe_audio(str(final_audio_path)),
            )
            words = transcribed.get("words", []) if isinstance(transcribed, dict) else []
            async with async_session() as db:
                project = await db.get(VideoProject, project_id)
                if words:
                    project.lyrics_words = words
                project.audio_path = str(final_audio_path)
                project.status = VideoStatus.GENERATING_SCENES
                project.progress = 0
                await db.commit()
        except Exception as e:
            logger.warning("Transcription failed for music video %d: %s", project_id, e)
            async with async_session() as db:
                project = await db.get(VideoProject, project_id)
                project.audio_path = str(final_audio_path)
                project.status = VideoStatus.GENERATING_SCENES
                project.progress = 0
                await db.commit()
    else:
        async with async_session() as db:
            project = await db.get(VideoProject, project_id)
            project.audio_path = str(final_audio_path)
            project.enable_subtitles = False
            project.status = VideoStatus.GENERATING_SCENES
            project.progress = 0
            await db.commit()

    # 5. Run video pipeline
    await run_video_pipeline(project_id)

    return project_id


async def _wait_for_project_completion(project_id: int, timeout_minutes: int = 30) -> bool:
    """Poll project status until completed or failed. Returns True if completed."""
    max_checks = timeout_minutes * 6  # every 10 seconds
    for _ in range(max_checks):
        await asyncio.sleep(10)
        async with async_session() as db:
            project = await db.get(VideoProject, project_id)
            if not project:
                return False
            if project.status == VideoStatus.COMPLETED:
                return True
            if project.status == VideoStatus.FAILED:
                return False
    return False


async def _auto_publish(
    project_id: int,
    user_id: int,
    platform: str,
    social_account_id: int,
):
    """Create a publish job for the completed video and run it."""
    from app.tasks.publish_tasks import run_publish_job

    async with async_session() as db:
        # Find the render
        result = await db.execute(
            select(VideoRender)
            .where(VideoRender.project_id == project_id)
            .order_by(VideoRender.created_at.desc())
        )
        render = result.scalar_one_or_none()
        if not render:
            logger.warning("No render found for auto-publish: project=%d", project_id)
            return

        project = await db.get(VideoProject, project_id)
        title = "Video automatico"
        description = ""
        tags = []

        if project:
            title = project.title or title
            # Generate AI title/description/hashtags
            try:
                ai_result = await _generate_publish_metadata(project)
                title = ai_result.get("title") or title
                description = _strip_lyrics_from_description(ai_result.get("description") or "")
                hashtags = ai_result.get("hashtags") or ""
                tags = ai_result.get("tags") or []
                # Append hashtags at the end
                if hashtags:
                    description = (description + "\n\n" + hashtags).strip() if description else hashtags
            except Exception as e:
                logger.warning("AI metadata generation failed for auto-publish: %s", e)
                description = project.description or ""

        job = PublishJob(
            user_id=user_id,
            render_id=render.id,
            platform=platform,
            social_account_id=social_account_id,
            title=title,
            description=description,
            tags=tags,
            status=PublishStatus.PENDING,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    try:
        await run_publish_job(job_id)
        logger.info("Auto-publish completed: project=%d, job=%d", project_id, job_id)
    except Exception as e:
        logger.error("Auto-publish failed: project=%d, error=%s", project_id, e)


async def _generate_publish_metadata(project: VideoProject) -> dict:
    """Generate title, description, hashtags via AI for auto-publish."""
    import json
    import openai

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    # Build context
    context_parts = []
    if project.title:
        context_parts.append(f"Tema do video: {project.title}")
    if project.style_prompt:
        context_parts.append(f"Linha editorial/estilo: {project.style_prompt}")
    if project.lyrics_text:
        context_parts.append(f"Letra da musica:\n{project.lyrics_text[:500]}")

    context = "\n".join(context_parts) or "Video musical sem detalhes adicionais"
    tema = project.track_title or project.title or "Video musical"

    prompt = f"""Voce e um estrategista de crescimento para canais novos de musica no YouTube. Gere metadados otimizados para descoberta, clique e retenção.

DADOS DO VIDEO:
Tema: {tema}
Contexto: {context[:2000]}

Gere:
1. Um titulo forte, curto, com alto potencial de CTR e clareza de busca (max 80 chars)
2. Uma descricao natural para YouTube (3-5 linhas), estruturada para canal pequeno crescer
3. Hashtags relevantes (5-8 hashtags)
4. Tags para SEO (5-10 palavras-chave)

REGRAS OBRIGATORIAS:
- TUDO em portugues brasileiro, natural e humano
- O titulo deve combinar IDENTIDADE DA MUSICA + INTENCAO DE BUSCA
- Formato preferencial de titulo: "<identidade da musica> | <frase de busca clara>"
- Exemplo de estrutura: "Tudo Posso em Cristo | Louvor de Forca e Superacao"
- Use palavras-chave naturais do nicho quando fizer sentido: louvor, fe, forca, superacao, oracao, adoracao, esperanca
- NUNCA mencione nomes de IA, ferramentas, plataformas ou marcas (nada de Tevoxi, CriaVideo, OpenAI, etc)
- NUNCA use termos tecnicos como "cinematografico", "experiencia visual", "experiencia cinematografica"
- Nao use clickbait enganoso
- Titulo curto, forte e direto ao ponto
- Descricao deve seguir esta ordem:
  1) Gancho emocional curto na primeira linha
  2) Reforco com 2 ou 3 palavras-chave naturais do tema
  3) CTA simples (ouca completa, curta, compartilhe, inscreva-se)
- NUNCA incluir letra completa da musica na descricao
- NUNCA comecar a descricao com bloco de letra
- Nao falar como a musica foi produzida; focar na mensagem e no beneficio para quem escuta
- Hashtags comecam com #
- Tom envolvente, emocional e autentico

Retorne SOMENTE JSON:
{{
  "title": "...",
  "description": "...",
  "hashtags": "#tag1 #tag2 ...",
  "tags": ["tag1", "tag2", ...]
}}"""

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "title": str(data.get("title", "")).strip()[:90],
            "description": _strip_lyrics_from_description(str(data.get("description", "")).strip()),
            "hashtags": str(data.get("hashtags", "")).strip(),
            "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
        }
    except Exception as e:
        logger.warning("AI publish metadata generation failed: %s", e)
        return {
            "title": project.title or "Video automatico",
            "description": project.description or "",
            "hashtags": "",
            "tags": [],
        }
