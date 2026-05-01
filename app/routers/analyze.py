"""
Analyze Router — Channel diagnostics and growth recommendations.
"""
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import openai
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import ChannelAnalysisReport, Platform, PublishJob, PublishStatus, SocialAccount, VideoRender
from app.services.pilot_prompt import normalize_interaction_personas, summarize_interaction_personas

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analyze", tags=["analyze"])
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

_DEFAULT_ANALYSIS_MODELS = ("gpt-5", "gpt-4.1", "gpt-4o")


def _resolve_analysis_models() -> list[str]:
    raw = str(getattr(settings, "openai_analysis_models", "") or "").strip()
    if not raw:
        return list(_DEFAULT_ANALYSIS_MODELS)

    parsed = [part.strip() for part in raw.split(",") if part.strip()]
    return parsed or list(_DEFAULT_ANALYSIS_MODELS)


_ANALYSIS_MODELS = _resolve_analysis_models()

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_TOKEN_URI = "https://oauth2.googleapis.com/token"

PT_STOPWORDS = {
    "a", "ao", "aos", "aquela", "aquele", "aqueles", "aquilo", "as", "ate", "com",
    "como", "da", "das", "de", "dela", "dele", "deles", "depois", "do", "dos", "e",
    "ela", "ele", "em", "entre", "era", "essa", "esse", "esta", "este", "eu", "foi",
    "ha", "isso", "isto", "ja", "la", "mais", "mas", "me", "mesmo", "meu", "minha",
    "na", "nas", "nem", "no", "nos", "nossa", "nosso", "o", "os", "ou", "para",
    "pela", "pelo", "por", "pra", "que", "se", "sem", "ser", "seu", "sua", "sobre",
    "sao", "tambem", "te", "tem", "tendo", "ter", "teu", "tua", "um", "uma", "uns",
    "umas", "vai", "voce", "voces",
}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _iso_duration_to_seconds(duration: str | None) -> int:
    raw = str(duration or "").strip().upper()
    if not raw:
        return 0
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", raw)
    if not match:
        return 0
    hours = _safe_int(match.group(1))
    minutes = _safe_int(match.group(2))
    seconds = _safe_int(match.group(3))
    return (hours * 3600) + (minutes * 60) + seconds


def _normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", (value or "").lower(), flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_keywords(texts: list[str], limit: int = 12) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        normalized = _normalize_text(text)
        if not normalized:
            continue
        for token in normalized.split(" "):
            if len(token) < 3 or token in PT_STOPWORDS or token.isdigit():
                continue
            counter[token] += 1
    return [word for word, _ in counter.most_common(limit)]


def _parse_json_response(raw_text: str) -> dict:
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return {}

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _coerce_list(value: Any, max_items: int = 8) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split("\n") if part.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    return items[:max_items]


def _normalize_persona_composition_from_tags(tags: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(tags, dict):
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_candidate(
        persona_type: Any,
        persona_profile_id: Any = 0,
        persona_profile_ids: Any = None,
        disable_persona_reference: Any = False,
    ):
        personas = normalize_interaction_personas([str(persona_type or "")])
        if not personas:
            return
        normalized_type = personas[0]

        ids: list[int] = []
        raw_ids = persona_profile_ids if isinstance(persona_profile_ids, list) else []
        for raw_id in raw_ids:
            try:
                pid = int(raw_id)
            except Exception:
                continue
            if pid > 0 and pid not in ids:
                ids.append(pid)

        try:
            profile_id = int(persona_profile_id or 0)
        except Exception:
            profile_id = 0
        if profile_id > 0 and profile_id not in ids:
            ids.insert(0, profile_id)

        disable_ref = bool(disable_persona_reference)
        key = f"{normalized_type}:{','.join(str(pid) for pid in ids)}:{1 if disable_ref else 0}"
        if key in seen:
            return
        seen.add(key)

        normalized.append(
            {
                "persona_type": normalized_type,
                "persona_profile_id": 0 if disable_ref else (ids[0] if ids else 0),
                "persona_profile_ids": [] if disable_ref else ids,
                "disable_persona_reference": disable_ref,
            }
        )

    raw_candidates = tags.get("persona_composition") if isinstance(tags.get("persona_composition"), list) else []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        _add_candidate(
            persona_type=item.get("persona_type") or item.get("type") or item.get("interaction_persona") or "",
            persona_profile_id=item.get("persona_profile_id") or item.get("profile_id") or 0,
            persona_profile_ids=item.get("persona_profile_ids") or item.get("profile_ids") or [],
            disable_persona_reference=bool(item.get("disable_persona_reference") or item.get("grok_text_only")),
        )

    if not normalized:
        _add_candidate(
            persona_type=tags.get("interaction_persona") or "",
            persona_profile_id=tags.get("persona_profile_id") or 0,
            persona_profile_ids=tags.get("persona_profile_ids") or [],
            disable_persona_reference=bool(tags.get("disable_persona_reference") or tags.get("grok_text_only")),
        )

    return normalized[:8]


def _build_persona_insights(top_videos: list[dict[str, Any]], persona_records: list[dict[str, Any]]) -> dict[str, Any]:
    insights = {
        "available": False,
        "tracked_published_videos": 0,
        "matched_top_videos": 0,
        "summary": "A analise ainda nao tem metadados suficientes de personas para este canal.",
        "top_combinations": [],
        "top_elements": [],
        "recommendations": [],
    }

    if not persona_records:
        return insights

    top_metrics_by_id = {
        str(item.get("id") or "").strip(): item
        for item in top_videos or []
        if str(item.get("id") or "").strip()
    }

    combination_stats: dict[str, dict[str, Any]] = {}
    element_stats: dict[str, dict[str, Any]] = {}
    matched_top_count = 0

    for record in persona_records:
        candidates = record.get("persona_candidates") if isinstance(record.get("persona_candidates"), list) else []
        persona_types = normalize_interaction_personas([
            candidate.get("persona_type") for candidate in candidates if isinstance(candidate, dict)
        ])
        if not persona_types:
            continue

        canonical_types = sorted(persona_types)
        combo_key = "|".join(canonical_types)
        combo_label = summarize_interaction_personas(persona_types)
        matched_top = top_metrics_by_id.get(str(record.get("platform_post_id") or "").strip())
        if matched_top:
            matched_top_count += 1

        combo_entry = combination_stats.setdefault(
            combo_key,
            {
                "label": combo_label,
                "persona_types": persona_types,
                "published_count": 0,
                "matched_top_videos": 0,
                "views_sum": 0,
                "likes_sum": 0,
                "comments_sum": 0,
                "best_video_title": "",
                "best_video_views": 0,
            },
        )
        combo_entry["published_count"] += 1
        if matched_top:
            combo_entry["matched_top_videos"] += 1
            combo_entry["views_sum"] += _safe_int(matched_top.get("views"))
            combo_entry["likes_sum"] += _safe_int(matched_top.get("likes"))
            combo_entry["comments_sum"] += _safe_int(matched_top.get("comments"))
            current_views = _safe_int(matched_top.get("views"))
            if current_views >= combo_entry["best_video_views"]:
                combo_entry["best_video_views"] = current_views
                combo_entry["best_video_title"] = str(matched_top.get("title") or record.get("title") or "").strip()

        for persona_type in persona_types:
            element_entry = element_stats.setdefault(
                persona_type,
                {
                    "persona_type": persona_type,
                    "label": summarize_interaction_personas([persona_type]),
                    "published_count": 0,
                    "matched_top_videos": 0,
                    "views_sum": 0,
                    "likes_sum": 0,
                    "comments_sum": 0,
                },
            )
            element_entry["published_count"] += 1
            if matched_top:
                element_entry["matched_top_videos"] += 1
                element_entry["views_sum"] += _safe_int(matched_top.get("views"))
                element_entry["likes_sum"] += _safe_int(matched_top.get("likes"))
                element_entry["comments_sum"] += _safe_int(matched_top.get("comments"))

    combo_rows: list[dict[str, Any]] = []
    for entry in combination_stats.values():
        matched = max(1, int(entry.get("matched_top_videos") or 0))
        combo_rows.append(
            {
                "label": entry["label"],
                "persona_types": entry["persona_types"],
                "published_count": int(entry.get("published_count") or 0),
                "matched_top_videos": int(entry.get("matched_top_videos") or 0),
                "avg_views": int(round(float(entry.get("views_sum") or 0) / matched)) if entry.get("matched_top_videos") else 0,
                "avg_likes": int(round(float(entry.get("likes_sum") or 0) / matched)) if entry.get("matched_top_videos") else 0,
                "avg_comments": int(round(float(entry.get("comments_sum") or 0) / matched)) if entry.get("matched_top_videos") else 0,
                "best_video_title": entry.get("best_video_title") or "",
                "best_video_views": int(entry.get("best_video_views") or 0),
            }
        )

    combo_rows.sort(
        key=lambda item: (
            int(item.get("matched_top_videos") or 0),
            int(item.get("avg_views") or 0),
            int(item.get("published_count") or 0),
        ),
        reverse=True,
    )

    element_rows: list[dict[str, Any]] = []
    for entry in element_stats.values():
        matched = max(1, int(entry.get("matched_top_videos") or 0))
        element_rows.append(
            {
                "persona_type": entry["persona_type"],
                "label": entry["label"],
                "published_count": int(entry.get("published_count") or 0),
                "matched_top_videos": int(entry.get("matched_top_videos") or 0),
                "avg_views": int(round(float(entry.get("views_sum") or 0) / matched)) if entry.get("matched_top_videos") else 0,
                "avg_likes": int(round(float(entry.get("likes_sum") or 0) / matched)) if entry.get("matched_top_videos") else 0,
                "avg_comments": int(round(float(entry.get("comments_sum") or 0) / matched)) if entry.get("matched_top_videos") else 0,
            }
        )

    element_rows.sort(
        key=lambda item: (
            int(item.get("matched_top_videos") or 0),
            int(item.get("avg_views") or 0),
            int(item.get("published_count") or 0),
        ),
        reverse=True,
    )

    top_combo = combo_rows[0] if combo_rows else None
    recommendations: list[str] = []
    if top_combo and top_combo.get("matched_top_videos"):
        recommendations.append(
            f"Repita a composicao {top_combo['label']} em novos temas e mantenha cada persona separada no mesmo frame, sem transformacao entre elas."
        )
        if len(top_combo.get("persona_types") or []) > 1:
            recommendations.append(
                "Use multi-persona quando a letra ou a mensagem pedir contraste visual claro; trate cada presenca como personagem proprio, com funcao visual distinta."
            )
    top_element = element_rows[0] if element_rows else None
    if top_element and top_element.get("matched_top_videos"):
        recommendations.append(
            f"A persona {top_element['label']} aparece com frequencia nos videos internos que mais performam; preserve esse eixo visual como base dos proximos testes."
        )
    if not recommendations and combo_rows:
        recommendations.append(
            "Continue registrando a composicao de personas nos videos publicados para que a analise consiga identificar quais combinacoes realmente viram vencedoras."
        )

    tracked_count = len(persona_records)
    if matched_top_count:
        summary = (
            f"{matched_top_count} top videos do canal puderam ser cruzados com metadados internos de personas. "
            "Esses sinais usam a composicao salva nos videos publicados pelo CriaVideo; ainda nao existe leitura visual frame a frame do video pronto."
        )
    else:
        summary = (
            f"{tracked_count} videos publicados pelo CriaVideo tem metadados de personas, mas nenhum deles apareceu entre os top videos atuais do canal. "
            "A analise usa metadata interna; ainda nao faz visao computacional do video pronto."
        )

    return {
        "available": bool(combo_rows or element_rows),
        "tracked_published_videos": tracked_count,
        "matched_top_videos": matched_top_count,
        "summary": summary,
        "top_combinations": combo_rows[:6],
        "top_elements": element_rows[:8],
        "recommendations": recommendations[:6],
    }


async def _collect_persona_insights(
    user_id: int,
    social_account_id: int,
    top_videos: list[dict[str, Any]],
    db: AsyncSession,
) -> dict[str, Any]:
    result = await db.execute(
        select(PublishJob)
        .options(selectinload(PublishJob.render).selectinload(VideoRender.project))
        .where(PublishJob.user_id == user_id)
        .where(PublishJob.social_account_id == social_account_id)
        .where(PublishJob.status == PublishStatus.PUBLISHED)
        .order_by(PublishJob.published_at.desc(), PublishJob.id.desc())
        .limit(200)
    )
    jobs = result.scalars().all()

    persona_records: list[dict[str, Any]] = []
    for job in jobs:
        project = job.render.project if job.render and getattr(job.render, "project", None) else None
        tags = project.tags if project and isinstance(project.tags, dict) else {}
        persona_candidates = _normalize_persona_composition_from_tags(tags)
        if not persona_candidates:
            continue
        persona_records.append(
            {
                "publish_job_id": int(job.id or 0),
                "platform_post_id": str(job.platform_post_id or "").strip(),
                "title": str(job.title or (project.title if project else "") or "").strip(),
                "persona_candidates": persona_candidates,
            }
        )

    return _build_persona_insights(top_videos=top_videos, persona_records=persona_records)


def _normalize_title(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    chunk = cleaned[:limit]
    if " " in chunk:
        chunk = chunk.rsplit(" ", 1)[0]
    return chunk.strip()


def _build_description_template(main_keyword: str, second_keyword: str) -> str:
    primary = main_keyword or "este tema"
    secondary = second_keyword or "crescimento"
    return (
        f"Quer evoluir em {primary} sem enrolacao? Este video mostra o caminho mais rapido.\n\n"
        f"Aqui voce vai aprender estrategias praticas sobre {primary} e {secondary}, com exemplos claros para aplicar hoje.\n\n"
        "Comente sua maior duvida para eu trazer o proximo episodio da serie.\n"
        "Inscreva-se e ative o sino para nao perder os proximos videos."
    )


def _format_hour_label(hour_utc: int | None) -> str:
    if hour_utc is None:
        return "Sem padrao suficiente"
    return f"{hour_utc:02d}:00 UTC"


def _build_tool_study(platform: str) -> list[dict[str, str]]:
    base = [
        {
            "name": "YouTube Data API v3",
            "phase": "Imediato",
            "effort": "Baixo",
            "focus": "Mapear videos top, frequencia e metadados que performam melhor.",
            "why": "Entrega diagnostico direto para sugerir titulos, descricao e thumbnail com contexto real.",
            "system_use": "Coletar videos top e atualizar ideias de temas semanalmente.",
        },
        {
            "name": "YouTube Analytics API",
            "phase": "Proxima etapa",
            "effort": "Medio",
            "focus": "Tempo de exibicao, retencao por video e origem de trafego.",
            "why": "Permite orientar melhorias por queda de retencao e nao apenas por visualizacao.",
            "system_use": "Priorizar formatos com maior retencao e reduzir formatos com queda no inicio.",
        },
        {
            "name": "Google Trends + YouTube Suggest",
            "phase": "Imediato",
            "effort": "Baixo",
            "focus": "Descobrir temas com demanda crescente antes de produzir.",
            "why": "Aumenta chance de discovery organico para canais pequenos e medios.",
            "system_use": "Atualizar fila de temas com tendencias relacionadas ao nicho principal.",
        },
        {
            "name": "Pipeline de scoring de thumbnail (Vision)",
            "phase": "Proxima etapa",
            "effort": "Medio",
            "focus": "Pontuar contraste, legibilidade e clareza de promessa visual.",
            "why": "Reduz thumbnails fracas e aumenta CTR antes da publicacao.",
            "system_use": "Aprovar thumbnails com score minimo antes da publicacao automatica.",
        },
        {
            "name": "Data Warehouse de performance",
            "phase": "Escala",
            "effort": "Alto",
            "focus": "Historico consolidado com cohort por formato, tema e horario.",
            "why": "Base para modelos preditivos de crescimento e recomendacao automatica.",
            "system_use": "Ajustar cadencia e topicos com base em historico de crescimento mensal.",
        },
    ]

    if platform == "tiktok":
        base.insert(
            1,
            {
                "name": "TikTok Content Posting + Analytics",
                "phase": "Proxima etapa",
                "effort": "Medio",
                "focus": "Integrar dados reais de retencao, reproducoes e perfil da audiencia.",
                "why": "Permite recomendacao mais precisa para formato curto e gancho inicial.",
                "system_use": "Reordenar temas curtos por taxa de conclusao e replays.",
            },
        )
    if platform == "instagram":
        base.insert(
            1,
            {
                "name": "Instagram Graph API Insights",
                "phase": "Proxima etapa",
                "effort": "Medio",
                "focus": "Alcance, reproducoes e salvamentos de Reels por tema.",
                "why": "Melhora sugeroes de capa e legenda com base em dados de engajamento real.",
                "system_use": "Ajustar hooks e capas com base em salvamentos e compartilhamentos.",
            },
        )
    return base[:6]


async def _refresh_google_access_token(account: SocialAccount, db: AsyncSession) -> bool:
    if not account.refresh_token:
        return False
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        return False

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(YOUTUBE_TOKEN_URI, data=payload)

    if resp.status_code != 200:
        logger.warning("Google token refresh failed (%s): %s", resp.status_code, resp.text[:500])
        return False

    data = resp.json()
    new_access_token = str(data.get("access_token") or "").strip()
    if not new_access_token:
        return False

    account.access_token = new_access_token
    expires_in = _safe_int(data.get("expires_in"))
    if expires_in > 0:
        account.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    extra = dict(account.extra_data or {})
    extra["token_refreshed_at"] = datetime.utcnow().isoformat()
    account.extra_data = extra
    await db.commit()
    return True


async def _youtube_get(
    account: SocialAccount,
    db: AsyncSession,
    endpoint: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {account.access_token}"}
        resp = await client.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, headers=headers)

        if resp.status_code == 401:
            refreshed = await _refresh_google_access_token(account, db)
            if refreshed:
                headers = {"Authorization": f"Bearer {account.access_token}"}
                resp = await client.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, headers=headers)

    if resp.status_code != 200:
        logger.warning("YouTube API error [%s]: %s", resp.status_code, resp.text[:600])
        raise HTTPException(
            status_code=502,
            detail=(
                "Falha ao consultar dados do YouTube. "
                "Reconecte sua conta e tente novamente."
            ),
        )

    return resp.json()


async def _collect_publish_history(user_id: int, social_account_id: int, db: AsyncSession) -> dict[str, Any]:
    result = await db.execute(
        select(PublishJob)
        .where(PublishJob.user_id == user_id)
        .where(PublishJob.social_account_id == social_account_id)
        .order_by(PublishJob.created_at.desc())
        .limit(150)
    )
    jobs = result.scalars().all()

    published_jobs = [job for job in jobs if job.status == PublishStatus.PUBLISHED]
    failed_jobs = [job for job in jobs if job.status == PublishStatus.FAILED]
    scheduled_jobs = [job for job in jobs if job.status == PublishStatus.SCHEDULED]

    now_utc = datetime.utcnow()
    threshold_30d = now_utc - timedelta(days=30)
    last_30d_published = [
        job for job in published_jobs
        if (job.published_at or job.created_at or now_utc) >= threshold_30d
    ]

    hour_counter: Counter[int] = Counter()
    for job in published_jobs:
        ref_date = job.published_at or job.created_at
        if ref_date:
            hour_counter[ref_date.hour] += 1
    best_hour = hour_counter.most_common(1)[0][0] if hour_counter else None

    publish_dates = [job.published_at for job in published_jobs if job.published_at]
    publish_dates.sort(reverse=True)
    gaps_days: list[float] = []
    for idx in range(len(publish_dates) - 1):
        delta = publish_dates[idx] - publish_dates[idx + 1]
        gaps_days.append(max(delta.total_seconds() / 86400.0, 0.0))
    avg_gap_days = round(sum(gaps_days) / len(gaps_days), 1) if gaps_days else None

    title_samples = [str(job.title).strip() for job in published_jobs if str(job.title or "").strip()]
    description_samples = [
        str(job.description).strip()
        for job in published_jobs
        if str(job.description or "").strip()
    ]
    combined_keywords = _extract_keywords(title_samples + description_samples, limit=16)

    recent_jobs_payload = []
    for job in jobs[:15]:
        ref_date = job.published_at or job.created_at
        recent_jobs_payload.append(
            {
                "id": job.id,
                "status": job.status.value if job.status else "pending",
                "title": (job.title or "").strip(),
                "description": (job.description or "").strip()[:260],
                "platform_url": (job.platform_url or "").strip(),
                "published_at": ref_date.isoformat() if ref_date else "",
            }
        )

    return {
        "total_jobs": len(jobs),
        "published_jobs": len(published_jobs),
        "failed_jobs": len(failed_jobs),
        "scheduled_jobs": len(scheduled_jobs),
        "last_30d_published": len(last_30d_published),
        "best_publish_hour_utc": best_hour,
        "best_publish_window": _format_hour_label(best_hour),
        "avg_gap_days": avg_gap_days,
        "keyword_candidates": combined_keywords,
        "recent_titles": title_samples[:20],
        "recent_jobs": recent_jobs_payload,
    }


async def _fetch_youtube_snapshot(account: SocialAccount, db: AsyncSession) -> dict[str, Any]:
    channel_data = await _youtube_get(
        account=account,
        db=db,
        endpoint="channels",
        params={
            "part": "snippet,statistics,contentDetails",
            "mine": "true",
            "maxResults": 1,
        },
    )

    channel_items = channel_data.get("items") or []
    if not channel_items:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nao foi possivel carregar o canal do YouTube com esta conta. "
                "Reconecte a conta para renovar as permissoes."
            ),
        )

    channel_item = channel_items[0]
    channel_snippet = channel_item.get("snippet", {})
    channel_stats = channel_item.get("statistics", {})
    channel_details = channel_item.get("contentDetails", {})

    uploads_playlist_id = (
        channel_details.get("relatedPlaylists", {}) or {}
    ).get("uploads")

    collected_video_ids: list[str] = []
    seen_ids: set[str] = set()
    if uploads_playlist_id:
        next_page = ""
        while len(collected_video_ids) < 60:
            playlist_resp = await _youtube_get(
                account=account,
                db=db,
                endpoint="playlistItems",
                params={
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": 50,
                    "pageToken": next_page,
                },
            )

            playlist_items = playlist_resp.get("items") or []
            for playlist_item in playlist_items:
                content_details = playlist_item.get("contentDetails", {})
                video_id = str(content_details.get("videoId") or "").strip()
                if not video_id or video_id in seen_ids:
                    continue
                seen_ids.add(video_id)
                collected_video_ids.append(video_id)
                if len(collected_video_ids) >= 60:
                    break

            next_page = str(playlist_resp.get("nextPageToken") or "")
            if not next_page:
                break

    videos_by_id: dict[str, dict[str, Any]] = {}
    for i in range(0, len(collected_video_ids), 50):
        chunk = collected_video_ids[i:i + 50]
        if not chunk:
            continue
        videos_resp = await _youtube_get(
            account=account,
            db=db,
            endpoint="videos",
            params={
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(chunk),
                "maxResults": len(chunk),
            },
        )
        for video_item in videos_resp.get("items") or []:
            vid = str(video_item.get("id") or "").strip()
            if vid:
                videos_by_id[vid] = video_item

    videos: list[dict[str, Any]] = []
    for video_id in collected_video_ids:
        item = videos_by_id.get(video_id)
        if not item:
            continue

        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        details = item.get("contentDetails", {})
        thumbs = snippet.get("thumbnails", {}) or {}
        thumb_url = (
            (thumbs.get("maxres") or {}).get("url")
            or (thumbs.get("high") or {}).get("url")
            or (thumbs.get("medium") or {}).get("url")
            or (thumbs.get("default") or {}).get("url")
            or ""
        )

        views = _safe_int(statistics.get("viewCount"))
        likes = _safe_int(statistics.get("likeCount"))
        comments = _safe_int(statistics.get("commentCount"))
        duration_seconds = _iso_duration_to_seconds(details.get("duration"))
        engagement = likes + comments
        engagement_rate = round((engagement / views) * 100, 2) if views > 0 else 0.0

        videos.append(
            {
                "id": video_id,
                "title": (snippet.get("title") or "").strip(),
                "published_at": str(snippet.get("publishedAt") or "").strip(),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail_url": thumb_url,
                "views": views,
                "likes": likes,
                "comments": comments,
                "duration_seconds": duration_seconds,
                "engagement_rate": engagement_rate,
            }
        )

    videos_by_date = sorted(
        videos,
        key=lambda item: _parse_iso_datetime(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    top_videos = sorted(videos, key=lambda item: item.get("views", 0), reverse=True)

    recent_cut = videos_by_date[:12]
    avg_views_recent = int(sum(v.get("views", 0) for v in recent_cut) / len(recent_cut)) if recent_cut else 0
    avg_duration_seconds = int(
        sum(v.get("duration_seconds", 0) for v in recent_cut) / len(recent_cut)
    ) if recent_cut else 0

    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    threshold_30d = now_utc - timedelta(days=30)
    uploads_last_30d = 0
    for video in videos_by_date:
        dt = _parse_iso_datetime(video.get("published_at"))
        if dt and dt >= threshold_30d:
            uploads_last_30d += 1

    title_keywords = _extract_keywords([v.get("title", "") for v in top_videos[:20]], limit=12)

    channel_thumb = (
        (channel_snippet.get("thumbnails", {}).get("high") or {}).get("url")
        or (channel_snippet.get("thumbnails", {}).get("medium") or {}).get("url")
        or (channel_snippet.get("thumbnails", {}).get("default") or {}).get("url")
        or ""
    )

    channel_payload = {
        "title": (channel_snippet.get("title") or account.account_label or "Canal YouTube").strip(),
        "handle": (channel_snippet.get("customUrl") or account.platform_username or "").strip(),
        "description": (channel_snippet.get("description") or "").strip()[:500],
        "thumbnail_url": channel_thumb,
        "subscribers": _safe_int(channel_stats.get("subscriberCount")),
        "total_views": _safe_int(channel_stats.get("viewCount")),
        "total_videos": _safe_int(channel_stats.get("videoCount")),
        "uploads_last_30d": uploads_last_30d,
        "avg_views_recent": avg_views_recent,
        "avg_duration_seconds": avg_duration_seconds,
    }

    return {
        "channel": channel_payload,
        "top_videos": top_videos[:12],
        "recent_videos": videos_by_date[:12],
        "keyword_candidates": title_keywords,
    }


def _build_non_youtube_snapshot(account: SocialAccount, history: dict[str, Any]) -> dict[str, Any]:
    recent_jobs = history.get("recent_jobs", [])
    pseudo_top: list[dict[str, Any]] = []
    for item in recent_jobs:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        pseudo_top.append(
            {
                "id": str(item.get("id") or ""),
                "title": title,
                "published_at": item.get("published_at") or "",
                "url": item.get("platform_url") or "",
                "thumbnail_url": "",
                "views": 0,
                "likes": 0,
                "comments": 0,
                "duration_seconds": 0,
                "engagement_rate": 0.0,
            }
        )
        if len(pseudo_top) >= 10:
            break

    channel_payload = {
        "title": (account.account_label or account.platform_username or "Conta conectada").strip(),
        "handle": (account.platform_username or "").strip(),
        "description": "Analise baseada no historico interno de publicacoes desta conta.",
        "thumbnail_url": "",
        "subscribers": 0,
        "total_views": 0,
        "total_videos": history.get("published_jobs", 0),
        "uploads_last_30d": history.get("last_30d_published", 0),
        "avg_views_recent": 0,
        "avg_duration_seconds": 0,
    }

    return {
        "channel": channel_payload,
        "top_videos": pseudo_top,
        "recent_videos": pseudo_top,
        "keyword_candidates": history.get("keyword_candidates", []),
    }


def _build_fallback_recommendations(
    platform: str,
    channel_payload: dict[str, Any],
    top_videos: list[dict[str, Any]],
    history: dict[str, Any],
) -> dict[str, Any]:
    keyword_pool = []
    keyword_pool.extend(channel_payload.get("keyword_candidates", []))
    keyword_pool.extend(history.get("keyword_candidates", []))
    keyword_pool.extend(_extract_keywords([str(item.get("title") or "") for item in top_videos[:12]], limit=10))
    keyword_pool = [str(word).strip() for word in keyword_pool if str(word).strip()]

    if len(keyword_pool) < 2:
        keyword_pool.extend(["tema central", "dicas praticas", "resultado real", "passo a passo"])

    main_kw = keyword_pool[0]
    second_kw = keyword_pool[1] if len(keyword_pool) > 1 else "resultado"
    third_kw = keyword_pool[2] if len(keyword_pool) > 2 else "crescimento"

    title_ideas = [
        _normalize_title(f"Como evoluir em {main_kw} com passos simples"),
        _normalize_title(f"{main_kw.title()} na pratica: o que realmente funciona"),
        _normalize_title(f"Guia direto de {main_kw} para melhorar resultados"),
        _normalize_title(f"{second_kw.title()} aplicado a {main_kw}: estrategia clara"),
        _normalize_title(f"Erros comuns em {main_kw} e como corrigir rapido"),
        _normalize_title(f"Plano de 30 dias para crescer com {main_kw}"),
        _normalize_title(f"Rotina simples de {main_kw} com foco em {third_kw}"),
        _normalize_title(f"{main_kw.title()} sem enrolacao: metodo para manter constancia"),
    ]

    description_template = _build_description_template(main_kw, second_kw)
    hashtags = [f"#{word.replace(' ', '')}" for word in keyword_pool[:6]]

    uploads_last_30d = _safe_int(channel_payload.get("uploads_last_30d"))
    avg_gap_days = history.get("avg_gap_days")
    publish_window = history.get("best_publish_window") or "Sem padrao"

    growth_actions = [
        "Defina 2 formatos fixos de conteudo (ex.: tutorial curto + estudo de caso) para facilitar recorrencia.",
        "Use gancho forte nos primeiros 7 segundos e antecipe o beneficio principal no inicio.",
        "Padronize thumbnail com 1 promessa visual clara e texto curto de alto contraste.",
        f"Teste publicar no horario com mais historico ({publish_window}) por 4 semanas e compare resultados.",
        "Republique temas vencedores com nova abordagem (angulo diferente ou nova promessa).",
        "Inclua CTA de comentario orientado por pergunta para aumentar sinais de engajamento.",
    ]

    if uploads_last_30d < 4:
        growth_actions.insert(
            0,
            "Aumente a cadencia para pelo menos 1 a 2 publicacoes por semana para acelerar aprendizado do algoritmo.",
        )

    if isinstance(avg_gap_days, (int, float)) and avg_gap_days > 10:
        growth_actions.append(
            "Reduza o intervalo medio entre uploads para manter distribuicao de impressao mais estavel."
        )

    top_titles = [item.get("title", "") for item in top_videos[:8]]
    recurring_keywords = _extract_keywords(top_titles, limit=8)

    content_gaps = [
        "Criar serie recorrente baseada nos topicos que mais repetem nos videos com melhor desempenho.",
        "Explorar conteudo de comparacao (antes vs depois, erro vs acerto, estrategia A vs B).",
        "Adicionar videos de resposta para duvidas frequentes dos comentarios.",
        "Publicar versoes curtas dos temas que performaram bem para ampliar alcance.",
    ]

    if recurring_keywords:
        content_gaps.insert(
            0,
            f"Focar em cluster de temas com palavras-chave: {', '.join(recurring_keywords[:4])}.",
        )

    thumbnail_ideas = [
        f"Rosto em close + texto de 2 palavras destacando '{main_kw}'.",
        "Composicao antes/depois com seta forte e contraste alto.",
        f"Fundo simples + objeto principal relacionado a '{second_kw}' + numero grande.",
        "Expressao de surpresa/curiosidade + elemento visual unico no canto superior.",
        "Capa com uma pergunta curta que gera curiosidade e promete transformacao.",
        "Variante minimalista sem texto para teste A/B com foco em imagem central.",
    ]

    return {
        "title_ideas": title_ideas[:10],
        "description_template": description_template,
        "thumbnail_ideas": thumbnail_ideas[:8],
        "growth_actions": growth_actions[:10],
        "content_gaps": content_gaps[:8],
        "hashtags": hashtags,
        "keyword_focus": keyword_pool[:10],
        "platform_note": (
            "Analise completa com YouTube API ativa."
            if platform == "youtube"
            else "Analise baseada no historico interno. Integração analitica da plataforma sera adicionada em fase seguinte."
        ),
    }


def _merge_recommendations(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return base

    merged = dict(base)
    merged["title_ideas"] = _coerce_list(override.get("title_ideas"), max_items=10) or base["title_ideas"]
    merged["thumbnail_ideas"] = _coerce_list(override.get("thumbnail_ideas"), max_items=8) or base["thumbnail_ideas"]
    merged["growth_actions"] = _coerce_list(override.get("growth_actions"), max_items=10) or base["growth_actions"]
    merged["content_gaps"] = _coerce_list(override.get("content_gaps"), max_items=8) or base["content_gaps"]
    merged["hashtags"] = _coerce_list(override.get("hashtags"), max_items=10) or base["hashtags"]
    merged["keyword_focus"] = _coerce_list(override.get("keyword_focus"), max_items=10) or base["keyword_focus"]

    description_template = str(override.get("description_template") or "").strip()
    merged["description_template"] = description_template or base["description_template"]

    platform_note = str(override.get("platform_note") or "").strip()
    merged["platform_note"] = platform_note or base["platform_note"]

    return merged


def _title_to_theme(raw_title: str) -> str:
    text = str(raw_title or "").strip()
    if not text:
        return ""

    text = re.sub(r"^\d+[\.)\-:\s]+", "", text)
    if "|" in text:
        left, right = [part.strip() for part in text.split("|", 1)]
        text = right or left

    text = re.sub(r"#[\w\-]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -|,.;:")

    if len(text) > 120:
        text = text[:120].rsplit(" ", 1)[0].strip()

    return text


def _build_automation_blueprint(
    recommendations: dict[str, Any],
    tool_study: list[dict[str, str]],
    history: dict[str, Any],
    persona_insights: dict[str, Any],
    platform_supported: bool,
) -> dict[str, Any]:
    title_ideas = _coerce_list(recommendations.get("title_ideas"), max_items=12)
    content_gaps = _coerce_list(recommendations.get("content_gaps"), max_items=8)
    growth_actions = _coerce_list(recommendations.get("growth_actions"), max_items=8)

    priority_themes: list[str] = []
    seen: set[str] = set()

    for candidate in title_ideas + content_gaps:
        theme = _title_to_theme(candidate)
        key = theme.lower()
        if not theme or key in seen:
            continue
        seen.add(key)
        priority_themes.append(theme)
        if len(priority_themes) >= 12:
            break

    cadence_hint = "daily" if _safe_int(history.get("last_30d_published")) >= 6 else "3x_week"

    return {
        "for_system_use": True,
        "priority_themes": priority_themes,
        "cadence_hint": cadence_hint,
        "best_publish_window": history.get("best_publish_window") or "Sem padrao",
        "keyword_focus": _coerce_list(recommendations.get("keyword_focus"), max_items=10),
        "top_actions": growth_actions,
        "preferred_persona_compositions": [
            {
                "label": item.get("label") or "",
                "persona_types": item.get("persona_types") or [],
                "avg_views": int(item.get("avg_views") or 0),
            }
            for item in (persona_insights.get("top_combinations") or [])[:4]
            if isinstance(item, dict)
        ],
        "tool_study": tool_study,
        "data_quality": "high" if platform_supported else "medium",
    }


async def _generate_ai_recommendations(
    platform: str,
    account: SocialAccount,
    channel_snapshot: dict[str, Any],
    top_videos: list[dict[str, Any]],
    history: dict[str, Any],
    persona_insights: dict[str, Any],
    fallback: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if not _openai:
        return None, None

    channel = channel_snapshot.get("channel", {})
    compact_top = []
    for item in top_videos[:8]:
        compact_top.append(
            {
                "title": item.get("title", ""),
                "views": item.get("views", 0),
                "likes": item.get("likes", 0),
                "comments": item.get("comments", 0),
                "duration_seconds": item.get("duration_seconds", 0),
                "published_at": item.get("published_at", ""),
            }
        )

    payload = {
        "platform": platform,
        "account_name": account.account_label or account.platform_username or "Conta",
        "channel": {
            "title": channel.get("title", ""),
            "handle": channel.get("handle", ""),
            "subscribers": channel.get("subscribers", 0),
            "total_views": channel.get("total_views", 0),
            "total_videos": channel.get("total_videos", 0),
            "uploads_last_30d": channel.get("uploads_last_30d", 0),
            "avg_views_recent": channel.get("avg_views_recent", 0),
            "avg_duration_seconds": channel.get("avg_duration_seconds", 0),
        },
        "top_videos": compact_top,
        "history": {
            "total_jobs": history.get("total_jobs", 0),
            "published_jobs": history.get("published_jobs", 0),
            "failed_jobs": history.get("failed_jobs", 0),
            "last_30d_published": history.get("last_30d_published", 0),
            "best_publish_window": history.get("best_publish_window", ""),
            "avg_gap_days": history.get("avg_gap_days"),
            "keyword_candidates": history.get("keyword_candidates", []),
        },
        "persona_insights": {
            "available": bool(persona_insights.get("available")),
            "summary": str(persona_insights.get("summary") or ""),
            "top_combinations": persona_insights.get("top_combinations") or [],
            "top_elements": persona_insights.get("top_elements") or [],
            "recommendations": persona_insights.get("recommendations") or [],
        },
        "fallback_recommendations": fallback,
    }

    prompt = f"""Voce e um estrategista senior de crescimento para canais de video.

Objetivo: gerar recomendacoes de alta qualidade, altamente coerentes com o historico real do canal.

DADOS:
{json.dumps(payload, ensure_ascii=False)}

REGRAS IMPORTANTES:
- Use portugues brasileiro natural, sem frases roboticas.
- Nao invente nicho diferente do que aparece nos videos top e no historico.
- Evite titulos genericos, vagos ou sem conexao com os temas do canal.
- Entregue titulos curtos e claros, de leitura facil em mobile.
- Mantenha descricao humana, com CTA simples.
- Quando houver persona_insights, considere explicitamente as composicoes de personas que performaram melhor e reflita isso nas acoes de crescimento, lacunas e direcoes criativas.
- Se os dados forem limitados, sinalize isso em platform_note e use historico interno.

QUANTIDADE:
- 6 a 10 titulos
- 5 a 8 ideias de thumbnail
- 6 a 10 acoes de crescimento
- 4 a 8 lacunas de conteudo

Responda SOMENTE JSON no formato:
{{
  "title_ideas": ["..."],
  "description_template": "...",
  "thumbnail_ideas": ["..."],
  "growth_actions": ["..."],
  "content_gaps": ["..."],
  "hashtags": ["#..."],
  "keyword_focus": ["..."],
  "platform_note": "..."
}}"""

    for model_name in _ANALYSIS_MODELS:
        try:
            response = await _openai.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.45,
                max_tokens=1800,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            data = _parse_json_response(content)
            if isinstance(data, dict) and data:
                return data, model_name
        except Exception as err:
            logger.warning("AI analysis recommendations failed for model %s: %s", model_name, err)
            continue

    return None, None


async def build_channel_analysis_payload(
    user_id: int,
    account: SocialAccount,
    db: AsyncSession,
) -> dict[str, Any]:
    platform = account.platform.value if account.platform else ""
    history = await _collect_publish_history(user_id=user_id, social_account_id=account.id, db=db)

    if account.platform == Platform.YOUTUBE:
        snapshot = await _fetch_youtube_snapshot(account=account, db=db)
        platform_supported = True
    else:
        snapshot = _build_non_youtube_snapshot(account=account, history=history)
        platform_supported = False

    channel_data = snapshot.get("channel", {})
    top_videos = snapshot.get("top_videos", [])
    persona_insights = await _collect_persona_insights(
        user_id=user_id,
        social_account_id=account.id,
        top_videos=top_videos,
        db=db,
    )
    channel_for_fallback = dict(channel_data or {})
    channel_for_fallback["keyword_candidates"] = snapshot.get("keyword_candidates", [])

    fallback = _build_fallback_recommendations(
        platform=platform,
        channel_payload=channel_for_fallback,
        top_videos=top_videos,
        history=history,
    )
    ai_recommendations, ai_model_used = await _generate_ai_recommendations(
        platform=platform,
        account=account,
        channel_snapshot=snapshot,
        top_videos=top_videos,
        history=history,
        persona_insights=persona_insights,
        fallback=fallback,
    )
    recommendations = _merge_recommendations(fallback, ai_recommendations)
    tool_study = _build_tool_study(platform)
    automation_blueprint = _build_automation_blueprint(
        recommendations=recommendations,
        tool_study=tool_study,
        history=history,
        persona_insights=persona_insights,
        platform_supported=platform_supported,
    )

    return {
        "account": {
            "id": account.id,
            "platform": platform,
            "account_label": account.account_label or "",
            "platform_username": account.platform_username or "",
        },
        "platform_supported": platform_supported,
        "channel": channel_data,
        "top_videos": top_videos,
        "history": history,
        "persona_insights": persona_insights,
        "recommendations": recommendations,
        "tool_study": tool_study,
        "automation_blueprint": automation_blueprint,
        "source": {
            "youtube_api": account.platform == Platform.YOUTUBE,
            "openai_used": bool(ai_recommendations),
            "persona_metadata_used": bool(persona_insights.get("available")),
            "analysis_model": ai_model_used,
            "generated_at": datetime.utcnow().isoformat(),
        },
    }


@router.get("/channel")
async def analyze_channel(
    social_account_id: int = Query(..., gt=0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await db.get(SocialAccount, social_account_id)
    if not account or account.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Conta social nao encontrada")

    payload = await build_channel_analysis_payload(user_id=user["id"], account=account, db=db)

    # Store each run so users can revisit past analyses without rerunning API calls.
    serializable_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    channel_title = str((payload.get("channel") or {}).get("title") or "").strip()
    platform_value = account.platform.value if isinstance(account.platform, Platform) else str(account.platform or "")

    report = ChannelAnalysisReport(
        user_id=user["id"],
        social_account_id=account.id,
        platform=platform_value or "youtube",
        account_label=account.account_label or "",
        platform_username=account.platform_username or "",
        channel_title=channel_title,
        payload=serializable_payload,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    source = dict(payload.get("source") or {})
    source["analysis_id"] = int(report.id)
    source["saved_at"] = report.created_at.isoformat() if report.created_at else datetime.utcnow().isoformat()
    payload["source"] = source

    return payload


@router.get("/history")
async def list_analysis_history(
    social_account_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=30, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ChannelAnalysisReport)
        .where(ChannelAnalysisReport.user_id == user["id"])
        .order_by(ChannelAnalysisReport.created_at.desc(), ChannelAnalysisReport.id.desc())
        .limit(limit)
    )
    if social_account_id:
        query = query.where(ChannelAnalysisReport.social_account_id == social_account_id)

    result = await db.execute(query)
    reports = result.scalars().all()

    payload: list[dict[str, Any]] = []
    for report in reports:
        payload.append(
            {
                "id": report.id,
                "social_account_id": report.social_account_id,
                "platform": report.platform,
                "account_label": report.account_label,
                "platform_username": report.platform_username,
                "channel_title": report.channel_title,
                "created_at": report.created_at.isoformat() if report.created_at else None,
            }
        )

    return payload


@router.get("/history/{report_id}")
async def get_analysis_history_report(
    report_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await db.get(ChannelAnalysisReport, report_id)
    if not report or report.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Analise nao encontrada")

    payload = dict(report.payload or {})

    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    account.setdefault("id", report.social_account_id or 0)
    account.setdefault("platform", report.platform or "youtube")
    account.setdefault("account_label", report.account_label or "")
    account.setdefault("platform_username", report.platform_username or "")
    payload["account"] = account

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source["analysis_id"] = int(report.id)
    source["saved_at"] = report.created_at.isoformat() if report.created_at else None
    payload["source"] = source

    return payload