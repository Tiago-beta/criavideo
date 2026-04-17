"""
Publish Router — Endpoints for publishing videos to social platforms.
"""
import os
import logging
import json
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
    account_ids: dict[str, int] = {}
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

        account = None
        requested_account_id = None
        if isinstance(req.account_ids, dict):
            requested_account_id = req.account_ids.get(platform_name)

        if requested_account_id:
            account = await db.get(SocialAccount, int(requested_account_id))
            if not account or account.user_id != user["id"] or account.platform != platform:
                jobs.append({"platform": platform_name, "error": "Invalid social account"})
                continue
        else:
            # Backward compatibility: if account is not explicitly selected,
            # use the first connected account for this platform.
            result = await db.execute(
                select(SocialAccount)
                .where(SocialAccount.user_id == user["id"])
                .where(SocialAccount.platform == platform)
                .order_by(SocialAccount.connected_at.desc(), SocialAccount.id.desc())
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

        jobs.append({
            "platform": platform_name,
            "job_id": job.id,
            "status": job.status.value,
            "social_account_id": account.id,
            "account_label": account.account_label or account.platform_username or "Conta conectada",
        })

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

    account_ids = {j.social_account_id for j in jobs if j.social_account_id}
    accounts_by_id: dict[int, SocialAccount] = {}
    if account_ids:
        accounts_result = await db.execute(
            select(SocialAccount)
            .where(SocialAccount.user_id == user["id"])
            .where(SocialAccount.id.in_(account_ids))
        )
        accounts = accounts_result.scalars().all()
        accounts_by_id = {a.id: a for a in accounts}

    def _account_name(account: SocialAccount | None) -> str:
        if not account:
            return "Conta conectada"
        return account.account_label or account.platform_username or "Conta conectada"

    return [
        {
            "id": j.id,
            "platform": j.platform.value,
            "social_account_id": j.social_account_id,
            "account_label": _account_name(accounts_by_id.get(j.social_account_id)),
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
    """Generate title/description suggestions using a 2-step AI review flow."""
    render = await db.get(VideoRender, req.render_id)
    if not render:
        raise HTTPException(status_code=404, detail="Render not found")

    project = await db.get(VideoProject, render.project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    def _parse_json_response(raw_text: str) -> dict:
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start : end + 1])
            raise

    def _strip_lyrics_blocks(text: str) -> str:
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

    tema = project.track_title or project.title or "Video musical"
    resumo = context[:2200]
    tags = [str(tag).strip() for tag in (project.tags or []) if str(tag).strip()]
    publico = "Publico brasileiro do YouTube interessado em musica e conteudo emocional."
    if tags:
        publico = f"Publico principal ligado a: {', '.join(tags[:6])}."
    objetivo = "Maximizar CTR sem clickbait enganoso e melhorar clareza para o algoritmo."
    tom_desejado = project.style_prompt or "envolvente, premium e humano"

    stage1_prompt = f"""Voce e um estrategista de crescimento para canais pequenos de musica no YouTube.

Sua tarefa e transformar o conteudo de um video em:
- 3 titulos fortes com potencial de CTR e clareza de busca
- 1 descricao final enxuta para descoberta

Antes de escrever, descubra:
- promessa principal da musica
- emocao principal (forca, cura, esperanca, fe, etc)
- palavras-chave de busca mais naturais para esse tema
- melhor angulo para gerar clique sem enganar

REGRAS DE TITULO:
- sempre em portugues brasileiro
- formato preferencial: "<identidade da musica> | <frase de busca clara>"
- combinar nome/identidade da musica com intencao de busca
- maximo 80 caracteres
- sem nomes de IA/plataforma/marca
- sem clickbait enganoso

REGRAS DE DESCRICAO:
- 3 a 5 linhas curtas
- linha 1: gancho emocional forte
- linha 2: reforco com 2 ou 3 palavras-chave naturais
- linha 3: CTA simples (ouca completa, curta, compartilhe, inscreva-se)
- nao incluir letra completa da musica
- nao iniciar com bloco de letra
- nao usar texto tecnico sobre producao

Entregue:
1. palavras-chave principais
2. angulo central
3. 3 titulos
4. melhor titulo escolhido
5. descricao final pronta para colar no YouTube

DADOS DO VIDEO:
Tema: {tema}
Resumo: {resumo}
Publico: {publico}
Objetivo: {objetivo}
Tom desejado: {tom_desejado}

Retorne SOMENTE JSON (sem markdown) neste formato:
{{
  "keywords": ["...", "..."],
  "angle": "...",
  "titles": ["titulo 1", "titulo 2", "titulo 3"],
  "selected_title": "...",
  "description": "..."
}}"""

    try:
        # Step 1: ideation model generates 3 title options + first description.
        stage1_resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": stage1_prompt}],
            temperature=0.9,
            max_tokens=1400,
        )

        stage1_data = _parse_json_response(stage1_resp.choices[0].message.content or "{}")

        raw_keywords = stage1_data.get("keywords", [])
        if isinstance(raw_keywords, str):
            keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
        elif isinstance(raw_keywords, list):
            keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
        else:
            keywords = []

        angle = str(stage1_data.get("angle", "")).strip()

        raw_titles = stage1_data.get("titles", [])
        if isinstance(raw_titles, str):
            raw_titles = [raw_titles]
        title_options = [str(t).strip() for t in raw_titles if str(t).strip()]
        if not title_options:
            title_options = [project.title or project.track_title or "Novo video"]
        while len(title_options) < 3:
            title_options.append(title_options[-1])
        title_options = title_options[:3]

        selected_title_stage1 = str(
            stage1_data.get("selected_title")
            or stage1_data.get("best_title")
            or ""
        ).strip()
        if selected_title_stage1 and selected_title_stage1 not in title_options:
            title_options[0] = selected_title_stage1

        draft_description = str(
            stage1_data.get("description")
            or stage1_data.get("final_description")
            or project.description
            or ""
        ).strip()
        draft_description = _strip_lyrics_blocks(draft_description)

        stage2_prompt = f"""Voce e uma segunda IA de revisao editorial para YouTube.

Sua funcao e revisar e pontuar 3 titulos gerados por uma IA anterior, depois escolher o melhor e refinar a descricao final.

CONTEXTO REAL DO VIDEO:
{context}

PALAVRAS-CHAVE (IA 1): {', '.join(keywords[:6]) if keywords else 'nao informado'}
ANGULO CENTRAL (IA 1): {angle or 'nao informado'}

TITULOS CANDIDATOS:
1) {title_options[0]}
2) {title_options[1]}
3) {title_options[2]}

DESCRICAO CANDIDATA:
{draft_description or 'nao informado'}

CRITERIOS DE REVISAO:
- clareza da promessa
- potencial de CTR sem clickbait enganoso
- leitura em mobile
- aderencia ao conteudo real
- combinacao entre identidade da musica e intencao de busca
- uso estrategico de maiusculas/minusculas para destaque natural
- descricao curta, objetiva e sem letra completa

REGRAS OBRIGATORIAS DE SAIDA:
- tudo em portugues brasileiro
- titulo final com no maximo 80 caracteres
- priorize formato "identidade da musica | frase de busca"
- nao incluir letra completa da musica na descricao
- sem nomes de IA/plataforma/marca

Retorne SOMENTE JSON (sem markdown) neste formato:
{{
  "title_scores": [
    {{"title": "...", "score": 0, "why": "..."}},
    {{"title": "...", "score": 0, "why": "..."}},
    {{"title": "...", "score": 0, "why": "..."}}
  ],
  "chosen_title": "...",
  "description": "...",
  "hashtags": "#... #...",
  "tags": ["...", "...", "..."]
}}"""

        # Step 2: reviewer model scores and selects the best title/description.
        stage2_data: dict = {}
        try:
            stage2_resp = await _openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": stage2_prompt}],
                temperature=0.6,
                max_tokens=1200,
            )
            stage2_data = _parse_json_response(stage2_resp.choices[0].message.content or "{}")
        except Exception as review_err:
            logger.warning(f"AI second-pass review failed, using first-pass result: {review_err}")

        chosen_title = str(
            stage2_data.get("chosen_title")
            or stage2_data.get("best_title")
            or selected_title_stage1
            or title_options[0]
        ).strip()
        if len(chosen_title) > 90:
            chosen_title = (chosen_title[:90].rsplit(" ", 1)[0] or chosen_title[:90]).strip()
        if not chosen_title:
            chosen_title = project.title or project.track_title or "Novo video"

        final_description = str(
            stage2_data.get("description")
            or stage2_data.get("final_description")
            or draft_description
            or project.description
            or ""
        ).strip()
        final_description = _strip_lyrics_blocks(final_description)

        hashtags = str(stage2_data.get("hashtags") or "").strip()
        raw_tags = stage2_data.get("tags", [])
        if isinstance(raw_tags, str):
            tags = [item.strip() for item in raw_tags.split(",") if item.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(item).strip() for item in raw_tags if str(item).strip()]
        else:
            tags = []

        if not tags and keywords:
            tags = keywords[:12]

        if not hashtags and tags:
            hashtags = " ".join(f"#{tag.replace(' ', '')}" for tag in tags[:15])

        title_reviews = stage2_data.get("title_scores", [])
        if not isinstance(title_reviews, list):
            title_reviews = []

        return {
            "title": chosen_title,
            "description": final_description,
            "hashtags": hashtags,
            "tags": tags,
            "title_options": title_options,
            "title_reviews": title_reviews,
            "keywords": keywords,
            "angle": angle,
        }
    except Exception as e:
        logger.error(f"AI suggest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar sugestoes: {e}")


class ThumbnailRequest(BaseModel):
    render_id: int
    custom_title: str = ""  # Optional override for thumbnail text
    custom_description: str = ""  # Optional override for description/context


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
    import time as _time
    output_path = os.path.join(thumb_dir, f"publish_thumb_{render.id}_{int(_time.time())}.jpg")

    # Build punchy title for thumbnail
    raw_title = req.custom_title or project.track_title or project.title or "Music Video"
    # Remove emojis and keep it short for big text
    import re
    clean_title = re.sub(r'[^\w\s\(\)\-\!\?]', '', raw_title, flags=re.UNICODE).strip()
    if len(clean_title) > 40:
        clean_title = clean_title[:40].rsplit(' ', 1)[0]

    artist = project.track_artist or ""
    mood = ""
    style_hint = project.style_prompt or ""
    raw_description = req.custom_description or project.description or ""
    clean_description = (raw_description or "").strip()[:1200]

    # If we have lyrics, extract a mood hint (just first line)
    if project.lyrics_text:
        first_line = project.lyrics_text.strip().split('\n')[0][:100]
        mood = first_line

    try:
        import asyncio
        from app.services.thumbnail_generator import generate_thumbnail

        path = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_thumbnail(
                title=clean_title,
                description=clean_description,
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
                        title=clean_title,
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


# ---- Publish Links (user social/important links for descriptions) ----

class PublishLinksRequest(BaseModel):
    links: str = ""


@router.put("/links/{account_id}")
async def save_publish_links(
    account_id: int,
    req: PublishLinksRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save publish links for a specific social account."""
    account = await db.get(SocialAccount, account_id)
    if not account or account.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Account not found")
    account.publish_links = req.links
    await db.commit()
    return {"ok": True}
