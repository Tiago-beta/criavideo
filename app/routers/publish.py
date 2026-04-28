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

    def _pick_primary_keyword(keywords_list: list[str], theme_seed: str) -> str:
        for candidate in (keywords_list or []):
            value = re.sub(r"\s+", " ", str(candidate or "")).strip(" #")
            if len(value) >= 4:
                return value

        fallback = re.sub(r"\s+", " ", str(theme_seed or "")).strip(" #")
        if not fallback:
            return "hipnose guiada"
        if len(fallback) > 48:
            fallback = (fallback[:48].rsplit(" ", 1)[0] or fallback[:48]).strip()
        return fallback.lower()

    def _enforce_title_formula(raw_title: str, primary_keyword: str) -> str:
        title = re.sub(r"\s+", " ", str(raw_title or "")).strip(" -|:")
        keyword = re.sub(r"\s+", " ", str(primary_keyword or "")).strip(" -|:")
        if not keyword:
            keyword = "hipnose guiada"

        benefit_fallback = "Desperte Seu Potencial Mental"
        if not title:
            title = benefit_fallback

        title_lower = title.lower()
        keyword_lower = keyword.lower()

        if keyword_lower not in title_lower:
            title = f"{keyword}: {title}"

        if ":" not in title and "|" not in title:
            title = f"{keyword}: {title if title.lower() != keyword_lower else benefit_fallback}"

        title = re.sub(r"\s+", " ", title).strip()
        if len(title) > 80:
            title = (title[:80].rsplit(" ", 1)[0] or title[:80]).strip()

        if len(title) < 45:
            extension = " | Foco, Memoria e Clareza"
            if len(title) + len(extension) <= 80:
                title = title + extension

        return title

    def _description_looks_strong(text: str, primary_keyword: str) -> bool:
        body = str(text or "").strip()
        if len(body) < 260:
            return False

        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if len(lines) < 4:
            return False

        first_block = " ".join(lines[:2]).lower()
        keyword = str(primary_keyword or "").strip().lower()
        if keyword and keyword not in first_block:
            return False

        return True

    def _normalize_ptbr_copy(text: str) -> str:
        content = str(text or "").strip()
        if not content:
            return ""

        accent_map = {
            r"\bvoce\b": "você",
            r"\bsessao\b": "sessão",
            r"\bconteudos\b": "conteúdos",
            r"\batencao\b": "atenção",
            r"\bconcentracao\b": "concentração",
            r"\bconfianca\b": "confiança",
            r"\bmemoria\b": "memória",
            r"\bmusica\b": "música",
            r"\bpublico\b": "público",
            r"\bemocao\b": "emoção",
            r"\bdescricao\b": "descrição",
            r"\bvideo\b": "vídeo",
            r"\baudio\b": "áudio",
            r"\breprogramacao\b": "reprogramação",
            r"\bequilibrio\b": "equilíbrio",
        }

        def _replace_with_case(match: re.Match[str], replacement: str) -> str:
            token = match.group(0)
            if token.isupper():
                return replacement.upper()
            if token[:1].isupper():
                return replacement[:1].upper() + replacement[1:]
            return replacement

        for pattern, replacement in accent_map.items():
            content = re.sub(
                pattern,
                lambda m, rep=replacement: _replace_with_case(m, rep),
                content,
                flags=re.IGNORECASE,
            )

        normalized_lines = []
        for raw_line in content.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                normalized_lines.append("")
                continue
            if line.startswith("-") or line.startswith("#") or line.endswith((".", "!", "?", ":")):
                normalized_lines.append(line)
                continue
            normalized_lines.append(f"{line}.")

        content = "\n".join(normalized_lines)
        content = re.sub(r"([,;:.!?])(?!\s|$)", r"\1 ", content)
        content = re.sub(r"\s{2,}", " ", content)
        return content.strip()

    def _build_description_template(
        main_keyword: str,
        angle_text: str,
        audience_text: str,
        keywords_list: list[str],
    ) -> str:
        keyword = re.sub(r"\s+", " ", str(main_keyword or "")).strip()
        if not keyword:
            keyword = "Hipnose guiada para foco"

        angle_clean = re.sub(r"\s+", " ", str(angle_text or "")).strip()
        if not angle_clean:
            angle_clean = "mais foco, memória e clareza mental"

        audience_clean = re.sub(r"\s+", " ", str(audience_text or "")).strip()
        if not audience_clean:
            audience_clean = "quem busca concentração, aprendizado e equilíbrio emocional"

        extra_keywords = [k for k in (keywords_list or []) if k and k.lower() != keyword.lower()][:4]
        bullets = [
            f"{keyword}",
            extra_keywords[0] if len(extra_keywords) >= 1 else "Reprogramação mental positiva",
            extra_keywords[1] if len(extra_keywords) >= 2 else "Relaxamento profundo para clareza",
            extra_keywords[2] if len(extra_keywords) >= 3 else "Fortalecimento de foco e memória",
        ]

        lines = [
            f"{keyword}: {angle_clean}.",
            f"Nesta sessão guiada, você entra em estado de relaxamento para melhorar concentração, confiança e desempenho mental.",
            f"Ideal para {audience_clean}.",
            "",
            "Neste vídeo você encontra:",
            *[f"- {item}" for item in bullets],
            "",
            "Use fones, fique em local tranquilo e permita que sua mente absorva cada sugestão positiva.",
            "Use este áudio apenas em local seguro; nunca dirigindo ou realizando atividades que exigem atenção.",
            "Inscreva-se no canal para receber novos conteúdos de foco, memória e reprogramação mental.",
        ]

        return _normalize_ptbr_copy("\n".join(lines).strip())

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

    stage1_prompt = f"""Você é um estrategista sênior de YouTube SEO + CTR no Brasil.

Sua tarefa é criar metadados que maximizem descoberta em busca e cliques qualificados sem enganar.

GUIDE OBRIGATORIO DE THUMBNAIL (seguir estritamente):
- a thumbnail precisa ter 1 ideia principal e ser entendida em menos de 1 segundo
- usar rosto/personagem com emocao clara
- usar poucas palavras grandes (2 a 5)
- contraste forte para funcionar no celular
- incluir elemento de curiosidade visual
- promessa verdadeira e fiel ao video

GUIDE OBRIGATORIO DE TITULO E DESCRICAO:
- titulo com palavra-chave principal no comeco
- formula do titulo: [palavra-chave principal] + [beneficio forte] + [curiosidade ou promessa]
- evitar exageros falsos
- descricao com 2 a 3 primeiras linhas fortes para vender o clique antes do "mostrar mais"
- linha 1 deve repetir a palavra-chave principal
- incluir CTA e hashtags relevantes sem excesso

BANCO DE TERMOS SEO (usar quando fizer sentido):
hipnose para foco, hipnose para estudar, hipnose para inteligencia, hipnose guiada,
reprogramacao mental, aumentar concentracao, memoria e foco, mente poderosa

DADOS DO VIDEO:
Tema: {tema}
Resumo: {resumo}
Publico: {publico}
Objetivo: {objetivo}
Tom desejado: {tom_desejado}

Regras de saida:
- portugues brasileiro com acentuacao e pontuacao corretas
- titulo final ate 80 caracteres
- gerar 5 opcoes de titulo
- descricao pronta para colar no YouTube
- thumbnail_hook com 2 a 5 palavras
- thumbnail_prompt pronto para gerar arte
- nunca remover acentos (ex.: vídeo, você, sessão, descrição)

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
        stage2_prompt = f"""Você é o editor-chefe de performance de um canal no YouTube.

    Revise tudo com rigor e selecione a versão final mais forte para SEO + CTR + retenção.

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
- SEO: palavra-chave forte no comeco
- CTR: beneficio claro + curiosidade sem mentira
- Mobile: leitura rapida de titulo e thumbnail
- Aderencia: total fidelidade ao conteudo

REGRAS FINAIS OBRIGATORIAS:
- titulo entre 45 e 80 caracteres
- titulo no formato: [palavra-chave] + [beneficio] + [promessa]
- descricao com primeiras 2 linhas muito fortes para clique
- descricao sem letra completa
- thumbnail_hook com 2 a 5 palavras em portugues
- thumbnail_prompt com foco em 1 ideia principal, contraste forte e texto grande legivel
- ortografia revisada em pt-BR, com acentuacao e pontuacao natural

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

        primary_keyword = _pick_primary_keyword(keywords, tema)
        chosen_title = _enforce_title_formula(chosen_title, primary_keyword)

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
        final_description = _normalize_ptbr_copy(final_description)

        if not _description_looks_strong(final_description, primary_keyword):
            final_description = _build_description_template(
                main_keyword=primary_keyword,
                angle_text=angle,
                audience_text=audience,
                keywords_list=keywords,
            )
            final_description = _normalize_ptbr_copy(final_description)

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
        thumbnail_hook = _derive_thumbnail_hook(thumbnail_hook or chosen_title or primary_keyword)

        base_thumbnail_prompt = _build_thumbnail_prompt_template(
            theme=chosen_title,
            audience=audience or publico,
            emotion=emotion or "impacto",
            element=element or "pessoa ou elemento central do tema",
            hook_text=thumbnail_hook,
        )
        extra_thumbnail_notes = str(stage2_data.get("thumbnail_prompt") or stage1_thumbnail_prompt or "").strip()
        fallback_thumbnail_prompt = base_thumbnail_prompt
        if extra_thumbnail_notes:
            fallback_thumbnail_prompt = f"{base_thumbnail_prompt}\n\nDirecoes extras do editor:\n{extra_thumbnail_notes}"

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
    provider_preference: str = "openai"  # openai (default) | google


@router.post("/generate-thumbnail")
async def generate_publish_thumbnail(
    req: ThumbnailRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a viral thumbnail for the video (GPT Image default, Nano Banana fallback)."""
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
                provider_preference=req.provider_preference,
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

