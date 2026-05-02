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
            return "vídeo"
        if len(fallback) > 48:
            fallback = (fallback[:48].rsplit(" ", 1)[0] or fallback[:48]).strip()
        return fallback.lower()

    def _enforce_title_formula(raw_title: str, primary_keyword: str) -> str:
        title = re.sub(r"\s+", " ", str(raw_title or "")).strip(" -|:")
        keyword = re.sub(r"\s+", " ", str(primary_keyword or "")).strip(" -|:")
        if not keyword:
            keyword = "vídeo"

        benefit_fallback = "Entenda a Mensagem Principal"
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
            extension = " | Mensagem, Emoção e Contexto"
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

    def _has_off_context_niche_terms(text: str, source_context: str) -> bool:
        body = str(text or "").lower()
        context_lower = str(source_context or "").lower()
        niche_terms = [
            "hipnose",
            "hipnótico",
            "hipnotico",
            "reprogramação mental",
            "reprogramacao mental",
            "mente poderosa",
            "sessão guiada",
            "sessao guiada",
            "sugestão positiva",
            "sugestao positiva",
        ]
        return any(term in body and term not in context_lower for term in niche_terms)

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

    def _normalize_project_tags(raw_tags) -> list[str]:
        if isinstance(raw_tags, dict):
            candidates = []
            for key, value in raw_tags.items():
                key_label = re.sub(r"[_\-]+", " ", str(key or "")).strip()
                if key_label:
                    candidates.append(key_label)

                if isinstance(value, str):
                    value_label = re.sub(r"\s+", " ", value).strip()
                    if value_label and len(value_label) <= 48:
                        candidates.append(value_label)
                elif isinstance(value, (int, float)) and value:
                    candidates.append(str(value))
                elif isinstance(value, list):
                    for item in value[:4]:
                        item_label = re.sub(r"\s+", " ", str(item or "")).strip()
                        if item_label and len(item_label) <= 48:
                            candidates.append(item_label)
            return _normalize_tags(candidates, max_items=12)

        return _normalize_tags(raw_tags, max_items=12)

    def _normalize_render_source_path(raw_path: str) -> str:
        source = str(raw_path or "").strip()
        if not source:
            return ""

        if source.startswith("/video/media/"):
            source = os.path.join(settings.media_dir, source.split("/video/media/")[-1].lstrip("/"))
        elif "/video/media/" in source:
            source = os.path.join(settings.media_dir, source.split("/video/media/")[-1].lstrip("/"))
        elif not os.path.isabs(source):
            source = os.path.join(settings.media_dir, source.lstrip("/\\"))

        return source

    def _derive_theme_seed(primary_context: str, fallback: str) -> str:
        cleaned = _strip_lyrics_blocks(primary_context)
        for raw_line in cleaned.splitlines():
            line = str(raw_line or "").strip().lstrip("- ").strip()
            if not line:
                continue
            lowered = line.lower()
            if lowered.startswith("tema visual observado:"):
                return _trim_title(line.split(":", 1)[-1].strip(), 72) or _trim_title(fallback, 72) or "Vídeo musical"
            if lowered.startswith("angulo narrativo sugerido:"):
                return _trim_title(line.split(":", 1)[-1].strip(), 72) or _trim_title(fallback, 72) or "Vídeo musical"

        sentences = [part.strip(" -") for part in re.split(r"[\n\.!?]", cleaned) if part.strip()]
        for sentence in sentences:
            word_count = len(re.findall(r"[\wÀ-ÿ]+", sentence, flags=re.UNICODE))
            if word_count >= 4:
                return _trim_title(sentence, 72) or _trim_title(fallback, 72) or "Vídeo musical"

        words = re.findall(r"[\wÀ-ÿ]+", cleaned, flags=re.UNICODE)
        if len(words) >= 6:
            return _trim_title(" ".join(words[:16]), 72)
        return _trim_title(fallback, 72) or "Vídeo musical"

    def _context_has_enough_signal(text: str) -> bool:
        cleaned = _strip_lyrics_blocks(text)
        if len(cleaned) >= 80:
            return True
        words = re.findall(r"[\wÀ-ÿ]+", cleaned, flags=re.UNICODE)
        return len(words) >= 12

    async def _load_render_transcription() -> tuple[str, bool]:
        render_path = _normalize_render_source_path(render.file_path)
        if not render_path or not os.path.exists(render_path):
            return "", False

        temp_dir = Path(settings.media_dir) / "tmp" / "publish_transcribe" / str(project.id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        temp_audio_path = ""
        try:
            import asyncio
            import subprocess
            import uuid
            from app.services.transcriber import transcribe_audio

            def _extract_audio_track() -> str:
                target = temp_dir / f"publish_{render.id}_{uuid.uuid4().hex[:8]}.mp3"
                proc = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", render_path,
                        "-map", "0:a:0", "-vn",
                        "-t", "240",
                        "-ac", "1", "-ar", "16000",
                        "-c:a", "libmp3lame", "-b:a", "32k",
                        str(target),
                    ],
                    capture_output=True,
                    timeout=120,
                )
                if proc.returncode != 0:
                    stderr_text = (proc.stderr or b"").decode(errors="ignore")
                    if "Stream map '0:a:0'" in stderr_text or "matches no streams" in stderr_text:
                        return ""
                    raise RuntimeError(stderr_text[-800:])
                return str(target)

            temp_audio_path = await asyncio.get_event_loop().run_in_executor(None, _extract_audio_track)
            if not temp_audio_path:
                return "", False

            hint = (_strip_lyrics_blocks(project.lyrics_text or "") or project.description or project.track_title or project.title or "")[:800]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: transcribe_audio(temp_audio_path, prompt=hint),
            )
            transcript_text = _strip_lyrics_blocks(str((result or {}).get("text") or ""))
            return transcript_text, bool(transcript_text)
        except Exception as transcribe_err:
            logger.warning(
                "Publish AI context transcription failed for render %s: %s",
                render.id,
                transcribe_err,
            )
            return "", False
        finally:
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except OSError:
                    pass

    async def _load_render_visual_context() -> str:
        render_path = _normalize_render_source_path(render.file_path)
        if not render_path or not os.path.exists(render_path):
            return ""

        temp_dir = Path(settings.media_dir) / "tmp" / "publish_frames" / str(project.id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        frame_paths: list[str] = []
        try:
            import asyncio
            import subprocess
            import uuid
            from app.services.script_audio import analyze_images_for_context

            duration_seconds = 0.0
            for candidate in (render.duration, project.track_duration):
                try:
                    parsed = float(candidate or 0)
                except Exception:
                    parsed = 0.0
                if parsed > 0.5:
                    duration_seconds = parsed
                    break

            if duration_seconds <= 0.5:
                probe = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            render_path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    ),
                )
                if probe.returncode == 0:
                    try:
                        duration_seconds = float((probe.stdout or "").strip() or 0)
                    except Exception:
                        duration_seconds = 0.0

            if duration_seconds <= 0.5:
                duration_seconds = 30.0

            timestamps = []
            for factor in (0.15, 0.45, 0.78):
                timestamp = max(0.0, min(duration_seconds - 0.2, duration_seconds * factor))
                rounded = round(timestamp, 2)
                if rounded not in timestamps:
                    timestamps.append(rounded)
            if not timestamps:
                timestamps = [0.0]

            def _extract_frames() -> list[str]:
                outputs: list[str] = []
                for idx, timestamp in enumerate(timestamps):
                    target = temp_dir / f"publish_frame_{render.id}_{idx}_{uuid.uuid4().hex[:6]}.jpg"
                    proc = subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-ss",
                            str(timestamp),
                            "-i",
                            render_path,
                            "-frames:v",
                            "1",
                            "-q:v",
                            "4",
                            str(target),
                        ],
                        capture_output=True,
                        timeout=45,
                    )
                    if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                        outputs.append(str(target))
                return outputs

            frame_paths = await asyncio.get_event_loop().run_in_executor(None, _extract_frames)
            if not frame_paths:
                return ""

            topic_hint = project.title or project.track_title or project.description or "Vídeo"
            tone_hint = project.style_prompt or "informativo, envolvente e objetivo"
            visual_context = await analyze_images_for_context(
                frame_paths,
                topic=topic_hint,
                tone=tone_hint,
                duration_seconds=max(1, int(round(duration_seconds))),
            )
            return _normalize_ptbr_copy(str(visual_context or "").strip())
        except Exception as visual_err:
            logger.warning(
                "Publish visual context analysis failed for render %s: %s",
                render.id,
                visual_err,
            )
            return ""
        finally:
            for frame_path in frame_paths:
                if frame_path and os.path.exists(frame_path):
                    try:
                        os.remove(frame_path)
                    except OSError:
                        pass

    def _build_description_template(
        main_keyword: str,
        angle_text: str,
        audience_text: str,
        keywords_list: list[str],
        theme_text: str,
    ) -> str:
        keyword = re.sub(r"\s+", " ", str(main_keyword or "")).strip()
        if not keyword:
            keyword = re.sub(r"\s+", " ", str(theme_text or "")).strip() or "Vídeo"

        angle_clean = re.sub(r"\s+", " ", str(angle_text or "")).strip()
        if not angle_clean:
            angle_clean = "uma mensagem envolvente ligada ao tema do vídeo"

        audience_clean = re.sub(r"\s+", " ", str(audience_text or "")).strip()
        if not audience_clean:
            audience_clean = "quem se interessa por esse assunto e quer entender a mensagem com mais profundidade"

        extra_keywords = [k for k in (keywords_list or []) if k and k.lower() != keyword.lower()][:4]
        bullets = [
            f"{keyword}",
            extra_keywords[0] if len(extra_keywords) >= 1 else "Mensagem principal do conteúdo",
            extra_keywords[1] if len(extra_keywords) >= 2 else "Contexto e emoção do vídeo",
            extra_keywords[2] if len(extra_keywords) >= 3 else "Pontos mais importantes para o público",
        ]

        lines = [
            f"{keyword}: {angle_clean}.",
            f"Neste vídeo, você acompanha uma abordagem direta e envolvente sobre {keyword}.",
            f"Ideal para {audience_clean}.",
            "",
            "Neste vídeo você encontra:",
            *[f"- {item}" for item in bullets],
            "",
            "Assista até o final para acompanhar a ideia completa e perceber os detalhes mais importantes.",
            "Inscreva-se no canal para receber novos conteúdos sobre temas como este.",
        ]

        return _normalize_ptbr_copy("\n".join(lines).strip())

    render_transcription, has_spoken_transcription = await _load_render_transcription()
    render_visual_context = ""
    if (not has_spoken_transcription) or (not _context_has_enough_signal(render_transcription)):
        render_visual_context = await _load_render_visual_context()

    primary_context_seed = render_transcription if _context_has_enough_signal(render_transcription) else render_visual_context
    if not primary_context_seed:
        primary_context_seed = project.description or project.track_title or project.title or ""

    transcription_preview = render_transcription[:1800]
    visual_context_preview = render_visual_context[:1200]
    project_tags = _normalize_project_tags(project.tags)

    context_parts = []
    if project.title:
        context_parts.append(f"Titulo do projeto: {project.title}")
    if project.track_title:
        context_parts.append(f"Musica: {project.track_title}")
    if project.track_artist:
        context_parts.append(f"Artista: {project.track_artist}")
    if project.style_prompt:
        context_parts.append(f"Estilo visual: {project.style_prompt}")
    if transcription_preview:
        context_parts.append(f"Transcricao/contexto real do video:\n{transcription_preview}")
    elif project.lyrics_text:
        lyrics_preview = project.lyrics_text[:700]
        context_parts.append(f"Trecho da letra/roteiro:\n{lyrics_preview}")
    if visual_context_preview:
        context_parts.append(f"Resumo visual extraido dos frames:\n{visual_context_preview}")
    if project.description:
        context_parts.append(f"Descricao do projeto: {project.description}")
    if project_tags:
        context_parts.append(f"Tags base: {', '.join(project_tags[:12])}")

    context = "\n".join(context_parts) or "Vídeo musical sem detalhes adicionais."

    tema = _derive_theme_seed(
        primary_context_seed,
        project.track_title or project.title or "Vídeo musical",
    )
    resumo = context[:2800]
    title_seed = _trim_title(tema or project.title or project.track_title or "Novo video", 80)
    publico = "Público brasileiro do YouTube interessado no tema específico deste vídeo."
    if project_tags:
        publico = f"Publico principal ligado a: {', '.join(project_tags[:6])}."
    objetivo = "Maximizar CTR sem clickbait enganoso e melhorar descoberta em busca/sugeridos."
    tom_desejado = project.style_prompt or "envolvente, premium e humano"
    context_priority = (
        "Use primeiro a transcrição/contexto real do vídeo para entender o assunto. "
        "Se não houver falas suficientes, use o resumo visual extraído dos frames do vídeo. "
        "Só use o nome do projeto, do arquivo ou da música como apoio quando esses contextos não trouxerem informação suficiente."
    )

    stage1_prompt = f"""Você é um estrategista sênior de YouTube SEO + CTR no Brasil.

Sua tarefa é criar metadados que maximizem descoberta em busca e cliques qualificados sem enganar.

REGRA DE CONTEXTO (OBRIGATORIA):
- priorize a transcrição/contexto real do vídeo acima do nome do projeto ou do arquivo
- se o vídeo não tiver falas suficientes, use o resumo visual dos frames para descobrir o contexto real
- se o título atual for vago, descubra o assunto principal pela transcrição e pela descrição real
- só use nome de música, nome do projeto ou rótulos genéricos como apoio secundário

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

MODELO EDITORIAL (usar como estrutura, nunca como texto fixo):
- identificar a palavra-chave real do vídeo a partir de título, letra, descrição e tags
- transformar o tema em benefício claro para o público certo
- criar curiosidade sem trocar o assunto e sem copiar exemplos de outros nichos
- nunca inserir termos que não aparecem no contexto do vídeo

DADOS DO VIDEO:
Tema: {tema}
Resumo: {resumo}
Publico: {publico}
Objetivo: {objetivo}
Tom desejado: {tom_desejado}
Prioridade de contexto: {context_priority}

Regras de saida:
- português brasileiro com acentuação e pontuação corretas
- título final até 80 caracteres
- gerar 5 opções de título
- descrição pronta para colar no YouTube
- thumbnail_hook com 2 a 5 palavras
- thumbnail_prompt pronto para gerar arte
- nunca remover acentos (ex.: vídeo, você, sessão, descrição)
- usar o modelo editorial apenas como estrutura, sem copiar nichos ou termos de exemplo

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
        keywords = [item for item in keywords if not _has_off_context_niche_terms(item, context)]

        angle = str(stage1_data.get("angle") or stage1_data.get("promise") or "").strip()
        audience = str(stage1_data.get("audience") or publico).strip()
        emotion = str(stage1_data.get("emotion") or "impacto").strip()
        element = str(stage1_data.get("element") or "pessoa ou elemento do tema").strip()

        raw_titles = stage1_data.get("titles", stage1_data.get("title_options", []))
        if isinstance(raw_titles, str):
            raw_titles = [raw_titles]
        title_options = [_trim_title(str(t).strip(), 80) for t in raw_titles if str(t).strip()]
        if not title_options:
            title_options = [title_seed]
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
- use a transcrição/contexto real do vídeo como fonte principal de verdade antes de considerar nomes vagos
- se a transcrição estiver vazia ou fraca, use o resumo visual dos frames como fonte principal de verdade
- proibido inserir assuntos, terapias, benefícios ou comandos que não estejam no contexto do vídeo
- se o vídeo for religioso, musical, infantil, educativo, produto ou outro nicho, adaptar tudo ao nicho real

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
        if not chosen_title or _has_off_context_niche_terms(chosen_title, context):
            chosen_title = title_seed

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

        if _has_off_context_niche_terms(final_description, context):
            final_description = ""

        if not _description_looks_strong(final_description, primary_keyword):
            final_description = _build_description_template(
                main_keyword=primary_keyword,
                angle_text=angle,
                audience_text=audience,
                keywords_list=keywords,
                theme_text=tema,
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

