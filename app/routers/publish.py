"""
Publish Router — Endpoints for publishing videos to social platforms.
"""
import os
import logging
import json
import re
from pathlib import Path
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
TEMP_UPLOAD_DIR = Path(settings.media_dir) / "temp_uploads"
TEMP_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


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


def _resolve_temp_image_upload(user_id: int, upload_id: str) -> Path | None:
    token = str(upload_id or "").strip()
    if not token or "/" in token or "\\" in token:
        return None

    ext = Path(token).suffix.lower()
    if ext not in TEMP_IMAGE_EXTS:
        return None

    candidate = TEMP_UPLOAD_DIR / str(int(user_id)) / token
    return candidate if candidate.exists() else None


@router.post("/ai-suggest")
async def ai_suggest(
    req: AISuggestRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate stronger title/description/thumbnail prompts using a 2-step AI editorial flow."""
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
            "🎵 letra da música",
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
        return "\n".join(lines[:8]).strip()

    def _trim_title(raw: str, max_len: int = 80) -> str:
        title = re.sub(r"\s+", " ", str(raw or "")).strip()
        if len(title) <= max_len:
            return title
        return (title[:max_len].rsplit(" ", 1)[0] or title[:max_len]).strip()

    def _normalize_tags(raw_tags, max_items: int = 15) -> list[str]:
        if isinstance(raw_tags, str):
            candidates = re.split(r"[,\n;|]", raw_tags)
        elif isinstance(raw_tags, list):
            candidates = [str(item) for item in raw_tags]
        else:
            candidates = []

        seen = set()
        final = []
        for item in candidates:
            tag = re.sub(r"\s+", " ", str(item or "")).strip().lstrip("#")
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            final.append(tag)
            if len(final) >= max_items:
                break
        return final

    def _normalize_hashtags(raw_hashtags: str, fallback_tags: list[str], max_items: int = 15) -> str:
        tokens = re.findall(r"#?[\wÀ-ÿ]+", str(raw_hashtags or ""), flags=re.UNICODE)
        if not tokens and fallback_tags:
            tokens = [f"#{tag.replace(' ', '')}" for tag in fallback_tags]

        seen = set()
        cleaned = []
        for token in tokens:
            label = token.lstrip("#").strip()
            if not label:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(f"#{label}")
            if len(cleaned) >= max_items:
                break
        return " ".join(cleaned)

    def _derive_thumbnail_hook(seed: str) -> str:
        words = re.findall(r"[\wÀ-ÿ]+", str(seed or ""), flags=re.UNICODE)
        stopwords = {
            "de", "da", "do", "das", "dos", "e", "em", "na", "no", "nas", "nos",
            "com", "por", "para", "um", "uma", "o", "a", "os", "as", "que",
        }
        chosen = [w for w in words if len(w) > 1 and w.lower() not in stopwords]
        if not chosen:
            chosen = words
        hook = " ".join(chosen[:5]).upper().strip()
        if len(hook) > 32:
            hook = (hook[:32].rsplit(" ", 1)[0] or hook[:32]).strip()
        return hook or "ALTA PERFORMANCE"

    def _build_thumbnail_prompt_template(
        theme: str,
        audience: str,
        emotion: str,
        element: str,
        hook_text: str,
    ) -> str:
        return (
            "Crie uma thumbnail profissional para YouTube em formato 16:9, resolucao 1280x720, "
            "estilo altamente clicavel, com composicao limpa e forte contraste.\n\n"
            f"Tema do video: {theme}\n"
            f"Publico-alvo: {audience}\n"
            f"Emocao principal: {emotion}\n"
            f"Elemento central: {element}\n"
            f"Texto grande na imagem: \"{hook_text}\"\n\n"
            "A imagem deve ter:\n"
            "- fundo simples e impactante\n"
            "- rosto ou objeto principal em destaque\n"
            "- iluminacao dramatica/profissional\n"
            "- cores com alto contraste\n"
            "- texto grande, legivel no celular\n"
            "- espaco livre sem poluicao visual\n"
            "- composicao que desperte curiosidade sem parecer falsa\n"
            "- aparencia moderna, viral e profissional"
        )

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
        lyrics_preview = project.lyrics_text[:700]
        context_parts.append(f"Trecho da letra:\n{lyrics_preview}")
    if project.description:
        context_parts.append(f"Descricao do projeto: {project.description}")
    if project.tags:
        context_parts.append(f"Tags base: {', '.join(str(tag) for tag in project.tags[:12])}")

    context = "\n".join(context_parts) or "Video musical sem detalhes adicionais."

    tema = project.track_title or project.title or "Video musical"
    resumo = context[:2800]
    project_tags = [str(tag).strip() for tag in (project.tags or []) if str(tag).strip()]
    publico = "Publico brasileiro do YouTube interessado em musica, foco e bem-estar."
    if project_tags:
        publico = f"Publico principal ligado a: {', '.join(project_tags[:6])}."
    objetivo = "Maximizar CTR sem clickbait enganoso e melhorar descoberta em busca/sugeridos."
    tom_desejado = project.style_prompt or "envolvente, premium e humano"

    stage1_prompt = f"""Voce e um estrategista senior de YouTube SEO + CTR no Brasil.

Sua tarefa e criar metadados de alto desempenho para um video, equilibrando descoberta (SEO) e clique (CTR) sem promessas falsas.

PRINCIPIOS:
- titulo forte responde ao mesmo tempo: "sobre o que e" + "por que clicar"
- primeiro bloco da descricao precisa vender o clique antes do "mostrar mais"
- usar palavras de busca naturais e especificas do tema

REGRAS DE TITULO:
- portugues brasileiro
- ate 80 caracteres
- preferir estrutura: "palavra-chave principal + beneficio forte + curiosidade/promessa"
- evitar exageros tipo "genio em 5 minutos"
- sem nomes de IA/plataforma/marca

REGRAS DE DESCRICAO:
- 4 a 8 linhas curtas e objetivas
- linha 1 com promessa e palavra-chave principal
- linhas seguintes com beneficio pratico e contexto de uso
- incluir CTA simples no final
- nao colar letra completa da musica
- nao usar texto tecnico de producao

REGRAS DE THUMBNAIL:
- criar frase curta de capa (2 a 5 palavras)
- propor prompt pronto no formato solicitado pelo usuario
- foco em contraste alto, composicao limpa e curiosidade real

DADOS DO VIDEO:
Tema: {tema}
Resumo: {resumo}
Publico: {publico}
Objetivo: {objetivo}
Tom desejado: {tom_desejado}

Retorne SOMENTE JSON (sem markdown):
{{
  "keywords": ["...", "..."],
  "angle": "...",
  "audience": "...",
  "emotion": "...",
  "element": "...",
  "titles": ["...", "...", "...", "...", "..."],
  "selected_title": "...",
  "description": "...",
  "hashtags": "#... #...",
  "tags": ["...", "...", "..."],
  "thumbnail_hook": "...",
  "thumbnail_prompt": "..."
}}"""

    try:
        stage1_resp = await _openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": stage1_prompt}],
            temperature=0.85,
            max_tokens=1800,
        )

        stage1_data = _parse_json_response(stage1_resp.choices[0].message.content or "{}")

        raw_keywords = stage1_data.get("keywords", [])
        if isinstance(raw_keywords, str):
            keywords = [k.strip() for k in re.split(r"[,\n;]", raw_keywords) if k.strip()]
        elif isinstance(raw_keywords, list):
            keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
        else:
            keywords = []

        angle = str(stage1_data.get("angle") or stage1_data.get("promise") or "").strip()
        audience = str(stage1_data.get("audience") or publico).strip()
        emotion = str(stage1_data.get("emotion") or "impacto").strip()
        element = str(stage1_data.get("element") or "pessoa ou elemento do tema").strip()

        raw_titles = stage1_data.get("titles", stage1_data.get("title_options", []))
        if isinstance(raw_titles, str):
            raw_titles = [raw_titles]
        title_options = [_trim_title(str(t).strip(), 80) for t in raw_titles if str(t).strip()]
        if not title_options:
            title_options = [_trim_title(project.title or project.track_title or "Novo video", 80)]
        while len(title_options) < 3:
            title_options.append(title_options[-1])
        title_options = title_options[:5]

        selected_title_stage1 = _trim_title(
            str(
                stage1_data.get("selected_title")
                or stage1_data.get("best_title")
                or ""
            ).strip(),
            80,
        )
        if selected_title_stage1 and selected_title_stage1 not in title_options:
            title_options.insert(0, selected_title_stage1)
            title_options = title_options[:5]

        draft_description = str(
            stage1_data.get("description")
            or stage1_data.get("final_description")
            or project.description
            or ""
        ).strip()
        draft_description = _strip_lyrics_blocks(draft_description)

        stage1_hashtags = str(stage1_data.get("hashtags") or "").strip()
        stage1_tags = _normalize_tags(stage1_data.get("tags", []))
        stage1_thumbnail_hook = str(stage1_data.get("thumbnail_hook") or "").strip()
        stage1_thumbnail_prompt = str(stage1_data.get("thumbnail_prompt") or "").strip()

        titles_block = "\n".join(
            f"{idx + 1}) {title}" for idx, title in enumerate(title_options)
        )
        stage2_prompt = f"""Voce e o editor-chefe de performance de um canal no YouTube.

Revise os candidatos e escolha a melhor combinacao de titulo + descricao + thumbnail brief.

CONTEXTO DO VIDEO:
{context}

PALAVRAS-CHAVE BASE: {', '.join(keywords[:10]) if keywords else 'nao informado'}
ANGULO BASE: {angle or 'nao informado'}
PUBLICO: {audience or 'nao informado'}
EMOCAO: {emotion or 'nao informado'}

TITULOS CANDIDATOS:
{titles_block}

DESCRICAO CANDIDATA:
{draft_description or 'nao informado'}

THUMBNAIL HOOK CANDIDATO:
{stage1_thumbnail_hook or 'nao informado'}

THUMBNAIL PROMPT CANDIDATO:
{stage1_thumbnail_prompt or 'nao informado'}

CRITERIOS DE NOTA (0-10):
- relevancia para busca
- vontade de clicar sem enganar
- clareza em mobile
- aderencia ao conteudo real

REGRAS DE SAIDA:
- tudo em portugues brasileiro
- titulo final ate 80 caracteres
- descricao objetiva (4 a 8 linhas) sem letra completa
- hashtags limpas (formato #tag #tag)
- thumbnail_hook com 2 a 5 palavras
- thumbnail_prompt pronto para gerador de imagem

Retorne SOMENTE JSON:
{{
  "title_scores": [
    {{"title": "...", "score": 0, "why": "..."}}
  ],
  "chosen_title": "...",
  "description": "...",
  "hashtags": "#... #...",
  "tags": ["...", "..."],
  "thumbnail_hook": "...",
  "thumbnail_prompt": "..."
}}"""

        stage2_data: dict = {}
        try:
            stage2_resp = await _openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": stage2_prompt}],
                temperature=0.35,
                max_tokens=1600,
            )
            stage2_data = _parse_json_response(stage2_resp.choices[0].message.content or "{}")
            if not isinstance(stage2_data, dict):
                stage2_data = {}
        except Exception as review_err:
            logger.warning(f"AI second-pass review failed, using first-pass result: {review_err}")

        chosen_title = _trim_title(
            str(
                stage2_data.get("chosen_title")
                or stage2_data.get("best_title")
                or selected_title_stage1
                or title_options[0]
            ).strip(),
            80,
        )
        if not chosen_title:
            chosen_title = _trim_title(project.title or project.track_title or "Novo video", 80)

        final_description = str(
            stage2_data.get("description")
            or stage2_data.get("final_description")
            or draft_description
            or project.description
            or ""
        ).strip()
        final_description = _strip_lyrics_blocks(final_description)
        if len(final_description) > 1800:
            final_description = (
                final_description[:1800].rsplit(" ", 1)[0].strip() or final_description[:1800]
            )

        tags = _normalize_tags(stage2_data.get("tags", []))
        if not tags:
            tags = _normalize_tags(stage1_tags or keywords)
        if not tags:
            tags = _normalize_tags(project_tags)

        hashtags = _normalize_hashtags(str(stage2_data.get("hashtags") or "").strip(), tags)
        if not hashtags:
            hashtags = _normalize_hashtags(stage1_hashtags, tags)

        thumbnail_hook = str(
            stage2_data.get("thumbnail_hook")
            or stage1_thumbnail_hook
            or ""
        ).strip()
        thumbnail_hook = _derive_thumbnail_hook(thumbnail_hook or chosen_title)

        fallback_thumbnail_prompt = _build_thumbnail_prompt_template(
            theme=chosen_title,
            audience=audience or publico,
            emotion=emotion or "impacto",
            element=element or "pessoa ou elemento central do tema",
            hook_text=thumbnail_hook,
        )
        thumbnail_prompt = str(
            stage2_data.get("thumbnail_prompt")
            or stage1_thumbnail_prompt
            or fallback_thumbnail_prompt
        ).strip()
        if len(thumbnail_prompt) > 2600:
            thumbnail_prompt = (
                thumbnail_prompt[:2600].rsplit(" ", 1)[0].strip() or thumbnail_prompt[:2600]
            )

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
            "audience": audience,
            "emotion": emotion,
            "thumbnail_hook": thumbnail_hook,
            "thumbnail_prompt": thumbnail_prompt,
        }
    except Exception as e:
        logger.error(f"AI suggest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar sugestoes: {e}")


class ThumbnailRequest(BaseModel):
    render_id: int
    custom_title: str = ""  # Optional override for thumbnail text
    custom_description: str = ""  # Optional override for description/context
    thumbnail_prompt: str = ""  # Optional ready-to-use prompt/brief
    reference_image_upload_id: str = ""  # Optional temp image id uploaded by user


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
    custom_prompt = (req.thumbnail_prompt or "").strip()
    if len(custom_prompt) > 2200:
        custom_prompt = custom_prompt[:2200].rsplit(" ", 1)[0].strip() or custom_prompt[:2200]

    reference_image_path = ""
    reference_upload_id = str(req.reference_image_upload_id or "").strip()
    if reference_upload_id:
        resolved_ref = _resolve_temp_image_upload(user["id"], reference_upload_id)
        if not resolved_ref:
            raise HTTPException(status_code=400, detail="Imagem de referencia nao encontrada. Reenvie a imagem.")
        reference_image_path = str(resolved_ref)

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
                strategy_prompt=custom_prompt,
                reference_image_path=reference_image_path,
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
        if reference_image_path:
            raise HTTPException(
                status_code=500,
                detail=f"Erro ao gerar thumbnail com imagem de referencia: {e}",
            )

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

