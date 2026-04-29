"""
Auto-creation tasks — Automated video generation triggered by scheduler.
"""
import asyncio
import hashlib
import logging
import math
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.config import get_settings
from app.models import (
    AutoSchedule, AutoScheduleTheme, VideoProject, VideoStatus,
    PublishJob, PublishStatus, SocialAccount, VideoRender,
)
from app.services.persona_registry import (
    build_persona_reference_montage,
    resolve_persona_reference_image,
    resolve_persona_reference_images,
)
from app.services.credit_pricing import estimate_auto_theme_credits

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

_INTERACTION_PERSONAS = {"homem", "mulher", "crianca", "familia", "natureza", "desenho", "personalizado"}

_NICHE_KEYWORDS = {
    "gospel": (
        "deus", "senhor", "jesus", "cristo", "louvor", "adoracao", "gospel", "fe", "oracao",
        "biblia", "espirito", "igreja", "worship", "god", "lord", "faith", "graca", "milagre",
    ),
    "meditacao": (
        "acalmar", "calma", "ansiedade", "relaxar", "serenidade", "paz", "respirar", "meditacao",
        "mindfulness", "sono", "antiestresse", "tranquilidade",
    ),
    "motivacional": (
        "motivacao", "forca", "superacao", "coragem", "foco", "disciplina", "vencer", "persistencia",
        "recomecar", "nao desistir", "proposito", "conquista",
    ),
    "relacionamento": (
        "amor", "casal", "relacionamento", "namoro", "familia", "marido", "esposa", "carinho",
        "dialogo", "separacao", "cuidado", "companheirismo",
    ),
    "financas": (
        "dinheiro", "renda", "investimento", "economia", "divida", "poupanca", "financeiro", "faturamento",
        "negocio", "cliente", "vendas", "lucro", "cartao",
    ),
    "fitness": (
        "treino", "fitness", "musculacao", "emagrecer", "dieta", "academia", "massa", "saude",
        "corrida", "energia", "shape", "metabolismo",
    ),
    "beleza": (
        "beleza", "skincare", "maquiagem", "pele", "cabelo", "estetica", "autocuidado", "glow",
        "hidratar", "antiidade", "cosmetico",
    ),
    "educacao": (
        "aprender", "estudo", "educacao", "didatica", "resumo", "aula", "prova", "enem",
        "concurso", "idioma", "memorizacao", "explicacao",
    ),
    "humor": (
        "humor", "engracado", "risada", "meme", "piada", "zoeira", "comedia", "sarcasmo",
    ),
    "games": (
        "game", "gamer", "jogo", "gameplay", "rank", "fps", "rpg", "battle royale", "console",
        "pc", "dica gamer", "nivel",
    ),
    "viagem": (
        "viagem", "destino", "roteiro", "turismo", "hotel", "praia", "montanha", "ferias",
        "passagem", "hostel", "aventura",
    ),
    "culinaria": (
        "receita", "cozinha", "prato", "sobremesa", "bolo", "massa", "tempero", "forno",
        "airfryer", "culinaria", "gastronomia",
    ),
    "empreendedorismo": (
        "empreender", "empreendedor", "startup", "negocio", "cliente", "vendas", "marketing",
        "escala", "ticket", "conversao", "lideranca",
    ),
}

_SEO_HOOKS_BY_NICHE = {
    "gospel": [
        "Mensagem de Deus para hoje",
        "Se essa musica te encontrou",
        "Louvor para renovar a fe",
        "Palavra de fe para {kw}",
        "Hino para fortalecer sua fe",
        "Musica gospel para acalmar",
    ],
    "meditacao": [
        "Musica para acalmar a mente",
        "Som para reduzir ansiedade",
        "Pausa guiada para respirar",
        "Trilha calma para {kw}",
        "Audio para relaxar em minutos",
        "Momento de paz para o seu dia",
    ],
    "motivacional": [
        "Mensagem de forca para hoje",
        "Nao desista: isso e para voce",
        "Palavras de superacao real",
        "Empurrao diario para {kw}",
        "Motivacao para seguir em frente",
        "Coragem para recomecar agora",
    ],
    "relacionamento": [
        "Mensagem para tocar o coracao",
        "Reflexao sobre amor e cuidado",
        "Se voce vive {kw}, ouca isso",
        "Conselho curto para relacionamentos",
        "Sinais que voce precisa ouvir",
        "Palavra certa para o casal",
    ],
    "financas": [
        "Dica pratica de dinheiro hoje",
        "Passo a passo para organizar {kw}",
        "Erro financeiro que trava sua renda",
        "Como economizar sem sofrimento",
        "Guia rapido para sair do aperto",
        "Estrategia simples para crescer renda",
    ],
    "fitness": [
        "Treino motivador para hoje",
        "Disciplina que muda o corpo",
        "Ative seu foco para {kw}",
        "Dica fitness para resultado real",
        "Rotina curta para ganhar consistencia",
        "Comece hoje sem desculpas",
    ],
    "beleza": [
        "Dica de beleza que funciona",
        "Skincare simples para {kw}",
        "Erro comum que envelhece a pele",
        "Passo a passo de autocuidado",
        "Resultado visivel com rotina curta",
        "Truque rapido para realcar sua beleza",
    ],
    "educacao": [
        "Aprenda {kw} de forma simples",
        "Resumo rapido para estudar melhor",
        "Dica de estudo que acelera resultado",
        "Entenda isso em poucos minutos",
        "Metodo pratico para memorizar",
        "Guia direto para aprender mais rapido",
    ],
    "humor": [
        "Se rir disso, compartilha",
        "Piada do dia para aliviar",
        "Humor rapido para seu feed",
        "Esse momento define {kw}",
        "Cena que todo mundo ja viveu",
        "Rindo da vida real",
    ],
    "games": [
        "Dica gamer para subir nivel",
        "Estrategia rapida para {kw}",
        "Erro que todo jogador comete",
        "Gameplay curto e direto",
        "Truque para melhorar no game",
        "Se voce joga, precisa ver",
    ],
    "viagem": [
        "Destino perfeito para relaxar",
        "Roteiro rapido para {kw}",
        "Dica de viagem que economiza tempo",
        "Lugar lindo para conhecer agora",
        "Guia pratico para viajar melhor",
        "Inspiracao de viagem para o fim de semana",
    ],
    "culinaria": [
        "Receita pratica para hoje",
        "Como fazer {kw} sem erro",
        "Truque de cozinha que facilita tudo",
        "Sabor caseiro em poucos passos",
        "Dica culinaria para ganhar tempo",
        "Prato rapido que surpreende",
    ],
    "empreendedorismo": [
        "Estrategia para vender mais hoje",
        "Dica de negocio para {kw}",
        "Erro de empreendedor iniciante",
        "Como atrair clientes sem complicar",
        "Passo a passo para crescer faturamento",
        "Mindset para escalar seu negocio",
    ],
    "general": [
        "Conteudo que prende atencao",
        "Se isso te encontrou, assista",
        "Dica direta para o seu dia",
        "Mensagem certa para este momento",
        "Resumo pratico para aplicar hoje",
        "Valor real em poucos segundos",
    ],
}

_SEO_STOPWORDS = {
    "para", "com", "sem", "sobre", "entre", "depois", "antes", "ainda", "porque", "quando",
    "essa", "esse", "isso", "voce", "voces", "nos", "eles", "elas", "sua", "seu", "suas", "seus",
    "mais", "menos", "muito", "muita", "pouco", "pouca", "todo", "toda", "todos", "todas",
    "video", "videos", "musica", "musicas", "mensagem", "tema", "trecho", "short", "canal",
}


def _normalize_text_for_matching(text: str) -> str:
    raw = str(text or "")
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return normalized.lower()


def _detect_ctr_niche(*texts: str) -> str:
    merged = " ".join(_normalize_text_for_matching(t) for t in texts if t)
    tokens = set(re.findall(r"[a-z0-9]+", merged))
    best_niche = "general"
    best_score = 0

    for niche, keywords in _NICHE_KEYWORDS.items():
        score = 0
        for kw in keywords:
            k = _normalize_text_for_matching(kw)
            if " " in k:
                if k in merged:
                    score += 2
            else:
                if k in tokens:
                    score += 1
        if score > best_score:
            best_niche = niche
            best_score = score

    return best_niche


def _extract_focus_keywords(*texts: str, max_keywords: int = 5) -> list[str]:
    scores = {}
    for text in texts:
        normalized = _normalize_text_for_matching(text)
        for token in re.findall(r"[a-z0-9]{4,}", normalized):
            if token in _SEO_STOPWORDS:
                continue
            scores[token] = scores.get(token, 0) + 1

    ordered = sorted(scores.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [token for token, _ in ordered[:max_keywords]]


def _clean_title_part(text: str, max_len: int = 72) -> str:
    value = " ".join(str(text or "").split())
    value = re.sub(r"\s+[—-]\s*short\s*\d+\b", "", value, flags=re.IGNORECASE)
    value = value.strip(" -|,")
    if len(value) > max_len:
        value = value[:max_len].rstrip(" -|,")
    return value


def _pick_seo_hook(project: VideoProject, ai_title: str) -> str:
    title_text = project.track_title or project.title or ""
    style_text = project.style_prompt or ""
    desc_text = project.description or ""
    lyrics_text = (project.lyrics_text or "")[:300]
    context_text = " ".join([title_text, style_text, desc_text, lyrics_text, ai_title])

    niche = _detect_ctr_niche(context_text)
    hooks = _SEO_HOOKS_BY_NICHE.get(niche) or _SEO_HOOKS_BY_NICHE["general"]

    segment_index = 0
    if isinstance(project.tags, dict):
        try:
            segment_index = int(project.tags.get("segment_index", 0) or 0)
        except Exception:
            segment_index = 0

    seed = int(project.id or 0) * 13 + segment_index * 7 + len(ai_title or "")
    template = hooks[seed % len(hooks)]

    keywords = _extract_focus_keywords(title_text, ai_title, desc_text, lyrics_text)
    kw = keywords[0] if keywords else (
        "fe" if niche == "gospel" else "resultado"
    )
    hook = template.replace("{kw}", kw)
    return _clean_title_part(hook, max_len=52)


def _compose_seo_automation_title(project: VideoProject, ai_title: str) -> str:
    is_short = isinstance(project.tags, dict) and bool(project.tags.get("musical_short"))

    raw = _clean_title_part(ai_title, max_len=90)

    if is_short and raw and len(raw) >= 15:
        return _clean_title_part(raw, max_len=90).strip(" -|,")

    right_part = ""
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|", 1)]
        right_part = _clean_title_part(parts[1] if len(parts) > 1 else "")
    if not right_part:
        right_part = _clean_title_part(project.track_title or project.title or raw)
    if not right_part:
        niche = _detect_ctr_niche(project.title, project.description)
        if niche == "gospel":
            right_part = "Louvor de fe e esperanca"
        elif niche == "financas":
            right_part = "Guia pratico para crescer"
        elif niche == "fitness":
            right_part = "Disciplina para transformar"
        else:
            right_part = "Conteudo para ouvir hoje"

    hook = _clean_title_part(_pick_seo_hook(project, raw), max_len=52)
    final_title = f"{hook} | {right_part}"

    if len(final_title) > 90:
        prefix = f"{hook} | "
        remaining = max(12, 90 - len(prefix))
        final_title = prefix + _clean_title_part(right_part, max_len=remaining)

    return final_title.strip(" -|,")


def _normalize_interaction_persona(value: str) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "criança": "crianca",
        "crianca": "crianca",
        "família": "familia",
        "familia": "familia",
        "personalizada": "personalizado",
        "custom": "personalizado",
    }
    normalized = mapping.get(raw, raw)
    if normalized in _INTERACTION_PERSONAS:
        return normalized
    return "natureza"


def _normalize_pilot_persona_candidates(experiment: dict) -> list[dict]:
    if not isinstance(experiment, dict) or not bool(experiment.get("enabled")):
        return []
    candidates = experiment.get("candidates") or []
    normalized: list[dict] = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        persona_type = _normalize_interaction_persona(
            item.get("persona_type") or item.get("type") or item.get("interaction_persona") or ""
        )
        if not persona_type:
            continue
        profile_ids: list[int] = []
        for raw_id in (item.get("persona_profile_ids") or item.get("profile_ids") or []):
            try:
                pid = int(raw_id)
            except Exception:
                continue
            if pid > 0 and pid not in profile_ids:
                profile_ids.append(pid)
        try:
            profile_id = int(item.get("persona_profile_id") or item.get("profile_id") or 0)
        except Exception:
            profile_id = 0
        if profile_id > 0 and profile_id not in profile_ids:
            profile_ids.insert(0, profile_id)
        disable_ref = bool(item.get("disable_persona_reference") or item.get("grok_text_only"))
        key = f"{persona_type}:{','.join(str(pid) for pid in profile_ids)}:{1 if disable_ref else 0}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "persona_type": persona_type,
                "persona_profile_id": profile_ids[0] if profile_ids else 0,
                "persona_profile_ids": profile_ids,
                "disable_persona_reference": disable_ref,
            }
        )
    return normalized[:8]


def _pick_pilot_persona_candidate(experiment: dict, variant_index: int) -> dict | None:
    candidates = _normalize_pilot_persona_candidates(experiment)
    if not candidates:
        return None
    winner = experiment.get("winner") if isinstance(experiment, dict) else None
    if isinstance(winner, dict) and str(experiment.get("phase") or "").lower() == "exploit":
        winner_type = _normalize_interaction_persona(winner.get("persona_type") or "")
        if winner_type:
            return {
                "persona_type": winner_type,
                "persona_profile_id": int(winner.get("persona_profile_id") or 0),
                "persona_profile_ids": winner.get("persona_profile_ids") or [],
                "disable_persona_reference": bool(winner.get("disable_persona_reference") or winner.get("grok_text_only")),
            }
    return candidates[max(0, int(variant_index or 0)) % len(candidates)]


def _build_interaction_persona_instruction(interaction_persona: str) -> str:
    persona = _normalize_interaction_persona(interaction_persona)
    if persona == "homem":
        return (
            "Inclua um homem em cena interagindo com o ambiente e com a emocao do trecho "
            "(por exemplo, orando, cantando, caminhando ou contemplando), sem perder o sentido da letra."
        )
    if persona == "mulher":
        return (
            "Inclua uma mulher em cena interagindo com o ambiente e com a emocao do trecho "
            "(por exemplo, orando, cantando, caminhando ou contemplando), sem perder o sentido da letra."
        )
    if persona == "crianca":
        return (
            "Inclua uma crianca em cena interagindo com o ambiente e com a emocao do trecho, "
            "com linguagem visual sensivel e respeitosa."
        )
    if persona == "familia":
        return (
            "Inclua uma familia (duas ou mais pessoas) interagindo de forma natural com o ambiente e com a emocao do trecho."
        )
    if persona == "desenho":
        return (
            "Inclua um personagem em estilo desenho/animacao (cartoon, 3D, anime, etc.) interagindo com o ambiente "
            "e com a emocao do trecho, mantendo coerencia visual cinematografica."
        )
    if persona == "personalizado":
        return (
            "Inclua a persona personalizada definida pelo usuario, respeitando os tracos, estilo e identidade visual "
            "da referencia escolhida."
        )
    return (
        "Priorize natureza viva e inclua obrigatoriamente pelo menos um elemento visual de conexao "
        "(animal, flor, ave, borboleta ou outro ser vivo natural) em destaque e coerente com o trecho."
    )


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


def _parse_theme_release_date(custom_settings: dict) -> date | None:
    raw = str(custom_settings.get("scheduled_date_override") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _is_theme_due_today(theme: AutoScheduleTheme, schedule_timezone: str) -> bool:
    custom_settings = theme.custom_settings if isinstance(theme.custom_settings, dict) else {}
    release_date = _parse_theme_release_date(custom_settings)
    if not release_date:
        return True
    try:
        tz = ZoneInfo(schedule_timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    return release_date <= datetime.now(tz).date()


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

        if schedule.video_type in {"music", "musical_shorts"}:
            due_pending = [t for t in pending if _is_theme_due_today(t, schedule.timezone or "UTC")]
            if not due_pending:
                logger.info(
                    "Auto-schedule %d: no pending themes due today (music schedule)",
                    auto_schedule_id,
                )
                return
            theme_entry = due_pending[0]
        else:
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
            else:
                logger.info(
                    "Auto-creation in test mode (no publish): schedule=%d, theme=%d, project=%d",
                    auto_schedule_id,
                    theme_entry.id,
                    project_id,
                )

            async with async_session() as db:
                theme = await db.get(AutoScheduleTheme, theme_entry.id)
                if theme:
                    theme.status = "completed"
                    theme.video_project_id = project_id
                    await db.commit()

            logger.info("Auto-creation completed: schedule=%d, theme=%d, project=%d", auto_schedule_id, theme_entry.id, project_id)

            # Pilot: after long video completes, enqueue shorts automatically
            pilot_cycle_key = (theme_entry.custom_settings or {}).get("pilot_cycle_key")
            if pilot_cycle_key and schedule.video_type == "music":
                try:
                    await _enqueue_pilot_shorts_from_long(
                        theme_entry_id=theme_entry.id,
                        project_id=project_id,
                        schedule_id=auto_schedule_id,
                    )
                except Exception as pilot_err:
                    logger.error(
                        "Pilot shorts enqueue failed: schedule=%d, theme=%d, error=%s",
                        auto_schedule_id, theme_entry.id, pilot_err,
                    )
            elif pilot_cycle_key and schedule.video_type == "musical_shorts":
                try:
                    await _mark_pilot_short_completed(pilot_cycle_key)
                except Exception as pilot_err:
                    logger.error(
                        "Pilot short cycle update failed: schedule=%d, theme=%d, error=%s",
                        auto_schedule_id, theme_entry.id, pilot_err,
                    )
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

    if video_type == "musical_shorts":
        return await _create_musical_short(theme_text, user_id, cfg, custom_settings)

    if video_type == "realistic":
        return await _create_realistic_video(theme_text, user_id, cfg)

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
        from app.routers.credits import deduct_credits

        estimate = estimate_auto_theme_credits(
            video_type="narration",
            default_settings=cfg,
        )
        credits_needed = int(estimate.get("credits_needed", 0) or 0)
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
        from app.routers.credits import deduct_credits

        estimate = estimate_auto_theme_credits(
            video_type="music",
            default_settings=cfg,
        )
        credits_needed = int(estimate.get("credits_needed", 0) or 0)
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
        project_tags = {
            "audio_source": "tevoxi",
            "force_karaoke_two_line": True,
            "tevoxi_audio_url": music_result.get("audio_url", ""),
            "tevoxi_job_id": music_result.get("job_id", ""),
            "tevoxi_duration": music_duration,
        }
        project = VideoProject(
            user_id=user_id,
            track_id=0,
            title=title,
            description=f"Auto-generated music video: {theme_text}",
            tags=project_tags,
            style_prompt=cfg.get("style_prompt", "cinematic, vibrant colors, dynamic lighting"),
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            track_title=title,
            track_artist="Tevoxi",
            track_duration=music_duration,
            lyrics_text=lyrics,
            lyrics_words=[],
            audio_path="",
            enable_subtitles=True,
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

    # 4. Transcribe audio to improve subtitle timing
    transcribed_words = []
    transcribed_text = ""
    try:
        from app.services.transcriber import transcribe_audio
        transcribed = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: transcribe_audio(str(final_audio_path), prompt=lyrics or ""),
        )
        if isinstance(transcribed, dict):
            raw_words = transcribed.get("words", [])
            if isinstance(raw_words, list):
                transcribed_words = [w for w in raw_words if isinstance(w, dict) and w.get("word")]
            transcribed_text = (transcribed.get("text", "") or "").strip()
    except Exception as e:
        logger.warning("Transcription failed for music video %d: %s", project_id, e)

    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        project.audio_path = str(final_audio_path)
        if transcribed_words:
            project.lyrics_words = transcribed_words
        if transcribed_text and not (project.lyrics_text or "").strip():
            project.lyrics_text = transcribed_text
        has_subtitle_text = bool((project.lyrics_text or "").strip()) or bool(project.lyrics_words)
        project.enable_subtitles = has_subtitle_text
        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 0
        await db.commit()

    # 5. Run video pipeline
    await run_video_pipeline(project_id)

    return project_id


async def _create_realistic_video(theme_text: str, user_id: int, cfg: dict) -> int:
    """Create a realistic video from a theme prompt.

    Uses the realistic video pipeline (Wan2/Grok/Seedance/MiniMax).
    Optionally adds Tevoxi music or background music.
    """
    from app.tasks.video_tasks import run_realistic_video_pipeline

    engine = str(cfg.get("engine", "wan2") or "wan2").strip().lower()
    duration = int(cfg.get("duration", 7))
    aspect_ratio = cfg.get("aspect_ratio", "9:16")
    realistic_style = cfg.get("realistic_style", "cinematic")
    interaction_persona = _normalize_interaction_persona(cfg.get("interaction_persona", "natureza"))
    requested_persona_profile_id = int(cfg.get("persona_profile_id", 0) or 0)
    requested_persona_profile_ids: list[int] = []
    for raw_pid in (cfg.get("persona_profile_ids") or []):
        try:
            parsed_pid = int(raw_pid)
        except Exception:
            continue
        if parsed_pid > 0 and parsed_pid not in requested_persona_profile_ids:
            requested_persona_profile_ids.append(parsed_pid)
    if requested_persona_profile_id and requested_persona_profile_id not in requested_persona_profile_ids:
        requested_persona_profile_ids.insert(0, requested_persona_profile_id)
    add_music = cfg.get("add_music", False)
    use_tevoxi = cfg.get("use_tevoxi", False)
    enable_subtitles = cfg.get("enable_subtitles", False)
    subtitle_settings = cfg.get("subtitle_settings") if isinstance(cfg.get("subtitle_settings"), dict) else {}
    tevoxi_lyrics = str(cfg.get("tevoxi_lyrics", "") or "").strip()

    reference_image_path = ""
    resolved_persona_profile_id = 0
    resolved_persona_profile_ids: list[int] = []
    async with async_session() as db:
        try:
            if requested_persona_profile_ids:
                resolved_personas, persona_image_paths = await resolve_persona_reference_images(
                    db=db,
                    user_id=user_id,
                    persona_type=interaction_persona,
                    persona_profile_ids=requested_persona_profile_ids,
                    ensure_default=False,
                )
                if persona_image_paths:
                    reference_image_path = build_persona_reference_montage(
                        user_id=user_id,
                        image_paths=persona_image_paths,
                        prefix="auto_persona_refs",
                    )
                    resolved_persona_profile_ids = [int(profile.id) for profile in resolved_personas]
                    if resolved_persona_profile_ids:
                        resolved_persona_profile_id = resolved_persona_profile_ids[0]

            if not reference_image_path:
                resolved_persona, single_reference_path = await resolve_persona_reference_image(
                    db=db,
                    user_id=user_id,
                    persona_type=interaction_persona,
                    persona_profile_id=requested_persona_profile_id,
                    ensure_default=False,
                )
                reference_image_path = single_reference_path
                if resolved_persona:
                    resolved_persona_profile_id = int(resolved_persona.id)
                    resolved_persona_profile_ids = [resolved_persona_profile_id]
        except RuntimeError as exc:
            raise RuntimeError(str(exc))
        if not reference_image_path:
            raise RuntimeError("Crie uma persona de interacao antes de rodar automacao realista")

    # Backend guardrail: Tevoxi realistic automation always uses Grok.
    if use_tevoxi:
        engine = "grok"

    # Credit check
    async with async_session() as db:
        from app.routers.credits import deduct_credits

        estimate = estimate_auto_theme_credits(
            video_type="realistic",
            default_settings=cfg,
        )
        credits_needed = int(estimate.get("credits_needed", 0) or 0)
        await deduct_credits(db, user_id, credits_needed)

    # Build tags for the project
    tags = {
        "realistic_style": realistic_style,
        "interaction_persona": interaction_persona,
        "reference_source": "persona",
        "reference_mode": "face_identity_only",
        "has_reference_image": True,
        "persona_profile_id": resolved_persona_profile_id,
        "persona_profile_ids": resolved_persona_profile_ids,
    }
    if enable_subtitles and subtitle_settings:
        tags["subtitle_settings"] = subtitle_settings
    if use_tevoxi:
        tevoxi_audio_url = cfg.get("tevoxi_audio_url", "")
        tevoxi_job_id = cfg.get("tevoxi_job_id", "")
        tevoxi_title = cfg.get("tevoxi_title", "")
        clip_start = float(cfg.get("clip_start", 0))
        clip_dur = float(cfg.get("clip_duration", duration))
        if tevoxi_audio_url:
            tags["audio_url"] = tevoxi_audio_url
            tags["tevoxi_job_id"] = tevoxi_job_id
            tags["tevoxi_title"] = tevoxi_title
            if tevoxi_lyrics:
                tags["lyrics"] = tevoxi_lyrics
            tags["clip_start"] = clip_start
            tags["clip_duration"] = clip_dur

    prompt_seed = (theme_text or "").strip()
    persona_instruction = _build_interaction_persona_instruction(interaction_persona)
    if use_tevoxi:
        if tevoxi_lyrics:
            lyrics_slice = " ".join(tevoxi_lyrics.split())[:420]
            prompt_seed = (
                f'Trecho da musica: "{lyrics_slice}". '
                "Crie uma cena realista cinematografica baseada nessas palavras. "
                "Baseie a cena somente nesse trecho, sem puxar elementos de outros versos. "
                "Nao force personagem humano quando o trecho nao pedir isso. "
                "Evite repetir cliches visuais (campo de trigo, roupa branca, poses padrao) "
                "quando isso nao estiver claramente no trecho cantado."
            )
        else:
            prompt_seed = (
                "Crie uma cena realista cinematografica inspirada no trecho cantado. "
                "Baseie a cena somente no trecho atual, sem puxar elementos de outros versos. "
                "Nao force personagem humano quando o trecho nao pedir isso. "
                "Evite repetir cliches visuais (campo de trigo, roupa branca, poses padrao) "
                "quando isso nao estiver claramente no trecho cantado."
            )

    if persona_instruction:
        prompt_seed = f"{prompt_seed} {persona_instruction}"

    # Create project
    async with async_session() as db:
        project = VideoProject(
            user_id=user_id,
            track_id=0,
            title=theme_text[:100],
            description=f"Auto-generated realistic video: {prompt_seed}",
            tags=tags,
            style_prompt=reference_image_path,
            aspect_ratio=aspect_ratio,
            track_title=theme_text[:100],
            track_artist="",
            track_duration=duration,
            lyrics_text=prompt_seed,
            lyrics_words=[],
            audio_path=engine,
            enable_subtitles=enable_subtitles,
            zoom_images=False,
            no_background_music=not add_music,
            is_realistic=True,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        project_id = project.id

    # Run realistic video pipeline
    await run_realistic_video_pipeline(project_id)

    # If Tevoxi music is configured, combine audio with video
    if use_tevoxi and cfg.get("tevoxi_audio_url"):
        try:
            # If no explicit clip_start, auto-select a segment with vocals
            # (skip the intro, usually ~20-30s of instrumental)
            if "clip_start" not in cfg or float(cfg.get("clip_start", 0)) == 0:
                song_dur = float(cfg.get("tevoxi_duration", 120))
                # Start at ~25% of the song (past intro, into first verse)
                auto_start = max(15, song_dur * 0.25)
                # Ensure we don't exceed song length
                if auto_start + duration > song_dur:
                    auto_start = max(0, song_dur - duration - 5)
                cfg = {**cfg, "clip_start": auto_start}
                logger.info("Auto-selected clip start at %.1fs for Tevoxi song (song_dur=%.1f)", auto_start, song_dur)
            await _download_and_combine_tevoxi_audio(project_id, cfg, duration)
        except Exception as e:
            logger.warning("Failed to combine Tevoxi audio for project %d: %s", project_id, e)

    return project_id


async def _download_and_combine_tevoxi_audio(project_id: int, cfg: dict, clip_duration: float):
    """Download Tevoxi audio and merge it with the realistic video output."""
    from app.config import get_settings
    settings = get_settings()

    tevoxi_audio_url = cfg.get("tevoxi_audio_url", "")
    if not tevoxi_audio_url:
        return

    audio_dir = Path(settings.media_dir) / "audio" / f"realistic_{project_id}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "tevoxi_music.mp3"

    # Download audio
    token = settings.tevoxi_api_token
    if not token and settings.tevoxi_jwt_secret:
        from jose import jwt as jose_jwt
        import time
        payload = {
            "id": settings.tevoxi_jwt_user_id,
            "email": settings.tevoxi_jwt_email,
            "role": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = jose_jwt.encode(payload, settings.tevoxi_jwt_secret, algorithm="HS256")

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(tevoxi_audio_url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download Tevoxi audio: HTTP {resp.status_code}")
        with open(audio_path, "wb") as f:
            f.write(resp.content)

    # Trim to clip segment (clip_start + clip_duration)
    clip_start = float(cfg.get("clip_start", 0))
    trimmed_path = str(audio_dir / "trimmed.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
    ]
    if clip_start > 0:
        cmd += ["-ss", str(clip_start)]
    cmd += [
        "-t", str(clip_duration),
        "-c:a", "libmp3lame", "-q:a", "2", trimmed_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()

    if not Path(trimmed_path).exists():
        trimmed_path = str(audio_path)  # fallback to full audio

    await _combine_short_audio(project_id, trimmed_path, clip_duration)


async def _create_musical_short(
    theme_text: str, user_id: int, cfg: dict, custom_settings: dict,
) -> int:
    """Create a 10-second realistic video short from a Tevoxi music segment.

    Flow: download audio → extract segment → transcribe → generate realistic video
    → combine audio + video → done.
    """
    import shutil
    import subprocess
    from app.tasks.video_tasks import run_realistic_video_pipeline

    tevoxi_audio_url = cfg.get("tevoxi_audio_url", "")
    tevoxi_job_id = cfg.get("tevoxi_job_id", "")
    tevoxi_title = cfg.get("tevoxi_title", theme_text)
    clip_start = float(custom_settings.get("clip_start", 0))
    clip_duration = float(custom_settings.get("clip_duration", 10))
    segment_index = int(custom_settings.get("segment_index", 0))
    disable_persona_reference = bool(
        custom_settings.get("disable_persona_reference")
        or cfg.get("disable_persona_reference")
    )
    interaction_persona = _normalize_interaction_persona(
        custom_settings.get("interaction_persona") or cfg.get("interaction_persona", "natureza")
    )
    requested_persona_profile_id = int(
        custom_settings.get("persona_profile_id")
        or cfg.get("persona_profile_id")
        or 0
    )
    requested_persona_profile_ids: list[int] = []
    for raw_pid in (custom_settings.get("persona_profile_ids") or cfg.get("persona_profile_ids") or []):
        try:
            parsed_pid = int(raw_pid)
        except Exception:
            continue
        if parsed_pid > 0 and parsed_pid not in requested_persona_profile_ids:
            requested_persona_profile_ids.append(parsed_pid)
    if requested_persona_profile_id and requested_persona_profile_id not in requested_persona_profile_ids:
        requested_persona_profile_ids.insert(0, requested_persona_profile_id)
    engine = "grok"  # musical shorts are Grok-only

    reference_image_path = ""
    resolved_persona_profile_id = 0
    resolved_persona_profile_ids: list[int] = []
    if not disable_persona_reference:
        async with async_session() as db:
            try:
                if requested_persona_profile_ids:
                    resolved_personas, persona_image_paths = await resolve_persona_reference_images(
                        db=db,
                        user_id=user_id,
                        persona_type=interaction_persona,
                        persona_profile_ids=requested_persona_profile_ids,
                        ensure_default=False,
                    )
                    if persona_image_paths:
                        reference_image_path = build_persona_reference_montage(
                            user_id=user_id,
                            image_paths=persona_image_paths,
                            prefix="short_persona_refs",
                        )
                        resolved_persona_profile_ids = [int(profile.id) for profile in resolved_personas]
                        if resolved_persona_profile_ids:
                            resolved_persona_profile_id = resolved_persona_profile_ids[0]

                if not reference_image_path:
                    resolved_persona, single_reference_path = await resolve_persona_reference_image(
                        db=db,
                        user_id=user_id,
                        persona_type=interaction_persona,
                        persona_profile_id=requested_persona_profile_id,
                        ensure_default=False,
                    )
                    reference_image_path = single_reference_path
                    if resolved_persona:
                        resolved_persona_profile_id = int(resolved_persona.id)
                        resolved_persona_profile_ids = [resolved_persona_profile_id]
            except RuntimeError as exc:
                raise RuntimeError(str(exc))
            if not reference_image_path:
                raise RuntimeError("Crie uma persona de interacao antes de gerar short realista")

    if not tevoxi_audio_url:
        raise RuntimeError("URL do audio Tevoxi nao configurada.")

    # Credit check
    async with async_session() as db:
        from app.routers.credits import deduct_credits

        estimate = estimate_auto_theme_credits(
            video_type="musical_shorts",
            default_settings=cfg,
            custom_settings=custom_settings,
        )
        credits_needed = int(estimate.get("credits_needed", 0) or 0)
        await deduct_credits(db, user_id, credits_needed)

    # 1. Download full audio from Tevoxi
    audio_dir = Path(settings.media_dir) / "audio" / f"short_{tevoxi_job_id}_{segment_index}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    full_audio_path = audio_dir / "full_music.mp3"

    if not full_audio_path.exists():
        from app.config import get_settings
        s = get_settings()
        token = s.tevoxi_api_token
        if not token and s.tevoxi_jwt_secret:
            from jose import jwt as jose_jwt
            import time
            payload = {
                "id": s.tevoxi_jwt_user_id,
                "email": s.tevoxi_jwt_email,
                "role": "admin",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            }
            token = jose_jwt.encode(payload, s.tevoxi_jwt_secret, algorithm="HS256")

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(tevoxi_audio_url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Falha ao baixar audio do Tevoxi: HTTP {resp.status_code}")
            with open(full_audio_path, "wb") as f:
                f.write(resp.content)
        logger.info("Tevoxi audio downloaded: %s (%d bytes)", full_audio_path, len(resp.content))

    # 2. Extract audio segment
    segment_audio_path = str(audio_dir / f"segment_{segment_index}.mp3")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(full_audio_path),
        "-ss", str(clip_start),
        "-t", str(clip_duration),
        "-c:a", "libmp3lame", "-q:a", "2",
        segment_audio_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if not Path(segment_audio_path).exists():
        raise RuntimeError(f"Falha ao extrair segmento de audio (start={clip_start}, dur={clip_duration})")

    # 3. Transcribe segment for visual prompt context
    visual_prompt = (
        "Crie um video realista cinematografico inspirado no trecho cantado. "
        "Baseie a cena somente no trecho atual, sem puxar elementos de outros versos. "
        "Nao force personagem humano quando o trecho nao pedir isso. "
        "Evite repetir cliches visuais (campo de trigo, roupa branca, poses padrao) "
        "quando isso nao estiver claramente no trecho cantado."
    )
    persona_instruction = _build_interaction_persona_instruction(interaction_persona)
    if persona_instruction:
        visual_prompt = f"{visual_prompt} {persona_instruction}"
    segment_transcription = ""
    segment_transcription_words = []
    try:
        from app.services.transcriber import transcribe_audio
        lyrics_hint = cfg.get("tevoxi_lyrics", "")
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: transcribe_audio(segment_audio_path, prompt=lyrics_hint),
        )
        transcribed = (result.get("text", "") if isinstance(result, dict) else "").strip()
        raw_words = result.get("words", []) if isinstance(result, dict) else []
        if isinstance(raw_words, list):
            segment_transcription_words = [
                w for w in raw_words if isinstance(w, dict) and w.get("word")
            ]
        if transcribed:
            segment_transcription = transcribed
            snippet = " ".join(transcribed.split())[:420]
            visual_prompt = (
                f'Trecho transcrito da musica: "{snippet}". '
                "Crie uma cena realista cinematografica baseada nessas palavras, "
                "somente nesse trecho e sem puxar elementos de outros versos. "
                "Nao force personagem humano quando o trecho nao pedir isso. "
                "Evite repetir cliches visuais (campo de trigo, roupa branca, poses padrao) "
                "quando isso nao estiver claramente no trecho cantado."
            )
            if persona_instruction:
                visual_prompt = f"{visual_prompt} {persona_instruction}"
            logger.info("Short %d transcribed: %s", segment_index, transcribed[:200])
        elif lyrics_hint:
            hint_slice = " ".join(str(lyrics_hint).split())[:420]
            visual_prompt = (
                f'Trecho de letra de referencia: "{hint_slice}". '
                "Crie uma cena realista cinematografica baseada nessas palavras, "
                "somente nesse trecho e sem puxar elementos de outros versos. "
                "Nao force personagem humano quando o trecho nao pedir isso. "
                "Evite repetir cliches visuais (campo de trigo, roupa branca, poses padrao) "
                "quando isso nao estiver claramente no trecho cantado."
            )
            if persona_instruction:
                visual_prompt = f"{visual_prompt} {persona_instruction}"
    except Exception as e:
        logger.warning("Transcription failed for short %d: %s", segment_index, e)
        if cfg.get("tevoxi_lyrics"):
            hint_slice = " ".join(str(cfg.get("tevoxi_lyrics", "")).split())[:420]
            visual_prompt = (
                f'Trecho de letra de referencia: "{hint_slice}". '
                "Crie uma cena realista cinematografica baseada nessas palavras, "
                "somente nesse trecho e sem puxar elementos de outros versos. "
                "Nao force personagem humano quando o trecho nao pedir isso. "
                "Evite repetir cliches visuais (campo de trigo, roupa branca, poses padrao) "
                "quando isso nao estiver claramente no trecho cantado."
            )
            if persona_instruction:
                visual_prompt = f"{visual_prompt} {persona_instruction}"

    # 4. Create VideoProject for realistic pipeline
    async with async_session() as db:
        project = VideoProject(
            user_id=user_id,
            track_id=0,
            title=f"{tevoxi_title} — Short {segment_index + 1}",
            description=visual_prompt,
            tags={
                "musical_short": True,
                "segment_index": segment_index,
                "clip_start": clip_start,
                "clip_duration": clip_duration,
                "segment_audio_path": segment_audio_path,
                "segment_transcription": segment_transcription,
                "interaction_persona": interaction_persona,
                "reference_source": "" if disable_persona_reference else "persona",
                "reference_mode": "" if disable_persona_reference else "face_identity_only",
                "has_reference_image": bool(reference_image_path),
                "disable_persona_reference": disable_persona_reference,
                "grok_text_only": disable_persona_reference,
                "persona_profile_id": resolved_persona_profile_id,
                "persona_profile_ids": resolved_persona_profile_ids,
                "pilot_variant": custom_settings.get("pilot_variant") or {},
                "pilot_persona_experiment": custom_settings.get("pilot_persona_experiment") or {},
            },
            style_prompt=reference_image_path,
            aspect_ratio="9:16",
            track_title=tevoxi_title,
            track_artist="",
            track_duration=clip_duration,
            lyrics_text=visual_prompt,
            lyrics_words=segment_transcription_words,
            audio_path=engine,  # engine selection stored here
            enable_subtitles=bool(segment_transcription_words),
            zoom_images=False,
            no_background_music=True,
            is_realistic=True,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        project_id = project.id

    # 5. Run realistic video pipeline (generates 10s video)
    await run_realistic_video_pipeline(project_id)

    # 6. After pipeline completes, combine audio segment with video
    await _combine_short_audio(project_id, segment_audio_path, clip_duration)

    return project_id


async def _extract_emotional_segments(
    lyrics_text: str,
    lyrics_words: list[dict],
    num_segments: int = 3,
    clip_duration: float = 10.0,
) -> list[dict]:
    """Use GPT to pick the most emotionally powerful segments from transcribed lyrics.

    Returns list of {"clip_start": float, "clip_duration": float,
                      "lyrics_snippet": str, "segment_index": int}
    """
    import json
    import openai

    if not lyrics_text or not lyrics_words:
        return []

    total_duration = 0.0
    for w in reversed(lyrics_words):
        if isinstance(w, dict) and w.get("end"):
            total_duration = float(w["end"])
            break

    if total_duration < clip_duration * 2:
        return []

    word_timeline = []
    for w in lyrics_words:
        if isinstance(w, dict) and w.get("word") and w.get("start") is not None:
            word_timeline.append(f"{w['start']:.1f}s: {w['word']}")
    timeline_text = "\n".join(word_timeline[:300])

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = (
        f"Analise a letra da musica abaixo com timestamps e selecione exatamente "
        f"{num_segments} trechos de ~{clip_duration} segundos cada que contem as "
        f"palavras mais FORTES EMOCIONALMENTE.\n\n"
        f"Priorize: momentos de climax emocional, palavras de fe/esperanca/forca/"
        f"amor/superacao, refroes impactantes, frases que tocam o coracao.\n"
        f"Evite: trechos instrumentais (sem palavras), repeticoes da mesma parte, "
        f"inicio e fim da musica.\n"
        f"Os trechos devem ser de partes DIFERENTES da musica, bem espacados entre si.\n\n"
        f"LETRA COM TIMESTAMPS:\n{timeline_text}\n\n"
        f"DURACAO TOTAL: {total_duration:.1f}s\n\n"
        f"Retorne SOMENTE um JSON com a chave \"segments\" contendo um array de "
        f"{num_segments} objetos:\n"
        f'{{"segments": [{{"clip_start": <segundo float>, '
        f'"lyrics_snippet": "<trecho de 5-15 palavras>"}}]}}\n\n'
        f"Regras para clip_start:\n"
        f"- Cada clip tera {clip_duration}s de duracao\n"
        f"- clip_start + {clip_duration} nao pode ultrapassar {total_duration:.1f}\n"
        f"- Espacar os clips em pelo menos {clip_duration + 5}s entre si\n"
        f"- Comecar o clip ~1s antes da primeira palavra emocional do trecho"
    )

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content or "{}")

        items = raw
        if isinstance(raw, dict):
            for key in ("segments", "trechos", "clips", "results"):
                if isinstance(raw.get(key), list):
                    items = raw[key]
                    break
            else:
                vals = list(raw.values())
                items = vals[0] if vals and isinstance(vals[0], list) else []

        if not isinstance(items, list):
            return _fallback_emotional_segments(lyrics_words, total_duration, num_segments, clip_duration)

        segments = []
        for i, seg in enumerate(items[:num_segments]):
            clip_start = max(0, float(seg.get("clip_start", 0)))
            if clip_start + clip_duration > total_duration:
                clip_start = max(0, total_duration - clip_duration - 1)
            segments.append({
                "clip_start": round(clip_start, 1),
                "clip_duration": clip_duration,
                "lyrics_snippet": str(seg.get("lyrics_snippet", "")).strip(),
                "segment_index": i,
            })

        return segments if segments else _fallback_emotional_segments(
            lyrics_words, total_duration, num_segments, clip_duration,
        )
    except Exception as e:
        logger.warning("GPT emotional segment extraction failed: %s", e)
        return _fallback_emotional_segments(lyrics_words, total_duration, num_segments, clip_duration)


def _fallback_emotional_segments(
    lyrics_words: list[dict],
    total_duration: float,
    num_segments: int,
    clip_duration: float,
) -> list[dict]:
    """Evenly space segments through the song, skipping intro/outro."""
    if total_duration < clip_duration * 2:
        return []

    start_bound = total_duration * 0.15
    end_bound = total_duration * 0.90 - clip_duration
    usable = end_bound - start_bound
    if usable < clip_duration:
        return []

    step = usable / max(1, num_segments)
    segments = []
    for i in range(num_segments):
        clip_start = round(start_bound + i * step, 1)
        if clip_start + clip_duration > total_duration:
            break

        snippet_words = []
        for w in lyrics_words:
            if isinstance(w, dict) and w.get("start") is not None:
                ws = float(w["start"])
                if clip_start <= ws <= clip_start + clip_duration:
                    snippet_words.append(w.get("word", ""))

        segments.append({
            "clip_start": clip_start,
            "clip_duration": clip_duration,
            "lyrics_snippet": " ".join(snippet_words[:15]),
            "segment_index": i,
        })
    return segments


async def _enqueue_pilot_shorts_from_long(
    theme_entry_id: int,
    project_id: int,
    schedule_id: int,
):
    """After a long pilot video completes, extract emotional segments and enqueue shorts."""
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            logger.warning("Project %d not found for pilot shorts", project_id)
            return

        theme_entry = await db.get(AutoScheduleTheme, theme_entry_id)
        if not theme_entry:
            return

        custom = theme_entry.custom_settings or {}
        pilot_cycle_key = custom.get("pilot_cycle_key")
        if not pilot_cycle_key:
            return

        shorts_per_cycle = int(custom.get("pilot_shorts_per_cycle", 3))

        long_schedule = await db.get(AutoSchedule, schedule_id)
        if not long_schedule:
            return

        long_settings = long_schedule.default_settings or {}
        shorts_schedule_id = long_settings.get("pilot_short_schedule_id")
        if not shorts_schedule_id:
            logger.warning("No shorts schedule ID in pilot long schedule %d", schedule_id)
            return

        shorts_schedule = await db.get(AutoSchedule, shorts_schedule_id)
        if not shorts_schedule:
            logger.warning("Shorts schedule %d not found", shorts_schedule_id)
            return

        tags = project.tags if isinstance(project.tags, dict) else {}
        audio_url = tags.get("tevoxi_audio_url", "")
        job_id = tags.get("tevoxi_job_id", "")

        if not audio_url:
            logger.warning("No Tevoxi audio URL in project %d, cannot enqueue shorts", project_id)
            return

        lyrics_text = project.lyrics_text or ""
        lyrics_words = project.lyrics_words or []

        segments = await _extract_emotional_segments(
            lyrics_text=lyrics_text,
            lyrics_words=lyrics_words,
            num_segments=shorts_per_cycle,
            clip_duration=10.0,
        )

        if not segments:
            logger.warning("No emotional segments extracted for project %d", project_id)
            return

        existing_cycle_result = await db.execute(
            select(AutoScheduleTheme)
            .where(AutoScheduleTheme.auto_schedule_id == shorts_schedule_id)
        )
        existing_cycle_themes = [
            theme for theme in existing_cycle_result.scalars().all()
            if (theme.custom_settings or {}).get("pilot_cycle_key") == pilot_cycle_key
        ]
        if existing_cycle_themes:
            logger.info(
                "Pilot shorts already enqueued for cycle=%s, skipping duplicate enqueue (%d existing)",
                pilot_cycle_key,
                len(existing_cycle_themes),
            )
            return

        shorts_defaults = shorts_schedule.default_settings or {}
        interaction_persona = (
            shorts_defaults.get("interaction_persona")
            or long_settings.get("interaction_persona")
            or "natureza"
        )
        disable_persona_reference = bool(
            shorts_defaults.get("disable_persona_reference")
            or long_settings.get("disable_persona_reference")
        )
        persona_profile_id = (
            shorts_defaults.get("persona_profile_id")
            or long_settings.get("persona_profile_id")
            or 0
        )
        persona_profile_ids = (
            shorts_defaults.get("persona_profile_ids")
            or long_settings.get("persona_profile_ids")
            or []
        )
        persona_experiment = (
            shorts_defaults.get("pilot_persona_experiment")
            or long_settings.get("pilot_persona_experiment")
            or {}
        )

        result = await db.execute(
            select(AutoScheduleTheme)
            .where(AutoScheduleTheme.auto_schedule_id == shorts_schedule_id)
            .order_by(AutoScheduleTheme.position.desc())
        )
        existing = result.scalars().all()
        max_pos = max([t.position for t in existing], default=-1)
        experiment_variant_offset = sum(
            1
            for theme in existing
            if isinstance(theme.custom_settings, dict)
            and isinstance((theme.custom_settings or {}).get("pilot_variant"), dict)
            and ((theme.custom_settings or {}).get("pilot_variant") or {}).get("kind") == "persona"
        )

        for seg in segments:
            max_pos += 1
            variant_index = experiment_variant_offset + int(seg["segment_index"] or 0)
            selected_persona = _pick_pilot_persona_candidate(persona_experiment, variant_index)
            short_interaction_persona = interaction_persona
            short_disable_persona_reference = disable_persona_reference
            short_persona_profile_id = persona_profile_id
            short_persona_profile_ids = persona_profile_ids
            pilot_variant = {}
            if selected_persona:
                short_interaction_persona = selected_persona.get("persona_type") or interaction_persona
                short_disable_persona_reference = bool(
                    selected_persona.get("disable_persona_reference")
                    or selected_persona.get("grok_text_only")
                )
                short_persona_profile_id = int(selected_persona.get("persona_profile_id", 0) or 0)
                short_persona_profile_ids = selected_persona.get("persona_profile_ids") or []
                pilot_variant = {
                    "kind": "persona",
                    "phase": str(persona_experiment.get("phase") or "explore"),
                    "variant_index": variant_index,
                    "persona_type": short_interaction_persona,
                    "persona_profile_id": short_persona_profile_id,
                    "persona_profile_ids": short_persona_profile_ids,
                    "disable_persona_reference": short_disable_persona_reference,
                    "metrics_status": "pending",
                }
            short_custom = {
                "tevoxi_audio_url": audio_url,
                "tevoxi_job_id": job_id,
                "tevoxi_title": project.track_title or project.title or "",
                "tevoxi_lyrics": lyrics_text,
                "clip_start": seg["clip_start"],
                "clip_duration": seg["clip_duration"],
                "segment_index": seg["segment_index"],
                "interaction_persona": short_interaction_persona,
                "disable_persona_reference": short_disable_persona_reference,
                "grok_text_only": short_disable_persona_reference,
                "persona_profile_id": short_persona_profile_id,
                "persona_profile_ids": short_persona_profile_ids,
                "pilot_cycle_key": pilot_cycle_key,
                "lyrics_snippet": seg["lyrics_snippet"],
                "pilot_persona_experiment": persona_experiment,
                "pilot_variant": pilot_variant,
            }

            short_theme = AutoScheduleTheme(
                auto_schedule_id=shorts_schedule_id,
                theme=f"{project.track_title or project.title} — Trecho {seg['segment_index'] + 1}",
                status="pending",
                position=max_pos,
                custom_settings=short_custom,
            )
            db.add(short_theme)

        await db.commit()

        logger.info(
            "Pilot shorts enqueued for scheduled creation: project=%d, shorts_schedule=%d, segments=%d",
            project_id, shorts_schedule_id, len(segments),
        )


async def _mark_pilot_short_completed(cycle_key: str):
    """Track pilot short completion as spaced shorts are created by the scheduler."""
    from app.models import AutoPilotCycleRun

    async with async_session() as db:
        result = await db.execute(
            select(AutoPilotCycleRun)
            .where(AutoPilotCycleRun.cycle_key == cycle_key)
        )
        cycle_run = result.scalar_one_or_none()
        if cycle_run:
            if not cycle_run.started_at:
                cycle_run.started_at = datetime.utcnow()
            cycle_run.status = "running"
            cycle_run.completed_shorts = int(cycle_run.completed_shorts or 0) + 1
            if cycle_run.completed_shorts >= int(cycle_run.planned_shorts or 0):
                cycle_run.status = "completed"
                cycle_run.completed_at = datetime.utcnow()
            await db.commit()

    logger.info(
        "Pilot short completed: cycle=%s",
        cycle_key,
    )


async def _combine_short_audio(project_id: int, segment_audio_path: str, clip_duration: float):
    """Merge audio segment with the realistic video output."""
    import subprocess

    async with async_session() as db:
        result = await db.execute(
            select(VideoRender)
            .where(VideoRender.project_id == project_id)
            .order_by(VideoRender.created_at.desc())
        )
        render = result.scalar_one_or_none()
        if not render or not render.file_path:
            logger.warning("No render found for musical short %d", project_id)
            return

        video_path = render.file_path
        if not Path(video_path).exists():
            logger.warning("Render file missing for musical short %d: %s", project_id, video_path)
            return

        # Combine: video + audio segment → final output
        render_dir = Path(video_path).parent
        final_path = str(render_dir / "short_final.mp4")

        fade_start = max(0, clip_duration - 2)
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", segment_audio_path,
            "-filter_complex",
            f"[1:a]aresample=44100,volume=0.8,afade=t=out:st={fade_start}:d=2[aout]",
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(clip_duration),
            "-shortest",
            final_path,
        ]

        proc = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120),
        )
        if proc.returncode != 0:
            logger.error("FFmpeg short audio merge failed: %s", proc.stderr[:300])
            return

        # Replace render file with the combined version
        if Path(final_path).exists() and Path(final_path).stat().st_size > 0:
            import shutil
            shutil.move(final_path, video_path)
            render.file_size = Path(video_path).stat().st_size
            render.duration = clip_duration
            await db.commit()
            logger.info("Musical short %d: audio merged successfully", project_id)


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

            project_tags = dict(project.tags or {}) if isinstance(project.tags, dict) else {}
            pilot_variant = dict(project_tags.get("pilot_variant") or {}) if isinstance(project_tags.get("pilot_variant"), dict) else {}
            if pilot_variant:
                pilot_variant["publish_title"] = title
                pilot_variant["description_fingerprint"] = hashlib.sha1((description or "").encode("utf-8")).hexdigest()[:12]
                pilot_variant["tags"] = tags
                pilot_variant["thumbnail_path"] = render.thumbnail_path or ""
                pilot_variant["publish_job_id"] = 0
                pilot_variant["metrics_status"] = "published_pending_metrics"
                project_tags["pilot_variant"] = pilot_variant
                project.tags = project_tags

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
        if project:
            project_tags = dict(project.tags or {}) if isinstance(project.tags, dict) else {}
            pilot_variant = dict(project_tags.get("pilot_variant") or {}) if isinstance(project_tags.get("pilot_variant"), dict) else {}
            if pilot_variant:
                pilot_variant["publish_job_id"] = job_id
                project_tags["pilot_variant"] = pilot_variant
                project.tags = project_tags
                await db.commit()

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

    tags_data = project.tags if isinstance(project.tags, dict) else {}
    is_short = bool(tags_data.get("musical_short"))
    segment_index = int(tags_data.get("segment_index", 0) or 0)
    lyrics_snippet = str(tags_data.get("segment_transcription", "") or "").strip()

    context = "\n".join(context_parts) or "Video musical sem detalhes adicionais"
    if is_short and lyrics_snippet:
        context += f"\nTrecho cantado neste short: {lyrics_snippet[:300]}"
    tema = project.track_title or project.title or "Video musical"

    short_instruction = ""
    if is_short:
        short_instruction = (
            "\nATENCAO — Este video e um SHORT (vertical, <15s). "
            "O titulo DEVE ser diferente de outros shorts da mesma musica. "
            f"Este e o trecho {segment_index + 1} da musica. "
            "Use como gancho uma frase inspirada no trecho cantado acima, "
            "nunca repita o mesmo gancho generico. "
            "O gancho deve refletir a emocao ESPECIFICA deste trecho.\n"
        )

    prompt = f"""Voce e um estrategista de crescimento para canais novos no YouTube. Gere metadados otimizados para descoberta, clique e retencao.

DADOS DO VIDEO:
Tema: {tema}
Contexto: {context[:2000]}{short_instruction}

Gere:
1. Um titulo forte, curto, com alto potencial de CTR e clareza de busca (max 80 chars)
2. Uma descricao natural para YouTube (3-5 linhas), estruturada para canal pequeno crescer
3. Hashtags relevantes (5-8 hashtags)
4. Tags para SEO (5-10 palavras-chave)

REGRAS OBRIGATORIAS:
- TUDO em portugues brasileiro, natural e humano
- Primeiro identifique o nicho principal do video (gospel, meditacao, motivacional, relacionamento, financas, fitness, beleza, educacao, humor, games, viagem, culinaria, empreendedorismo ou geral)
- O titulo deve combinar GANCHO DE BUSCA + IDENTIDADE DA MUSICA
- Formato obrigatorio de titulo: "<gancho SEO variavel> | <identidade da musica ou tema>"
- A parte antes de "|" deve variar entre videos, mesmo quando for a mesma musica
- A parte antes de "|" deve trazer intencao de busca/atencao (ex.: "Musica para acalmar", "Musica gospel", "Mensagem de Deus", "Se essa musica te encontrou")
- O gancho antes de "|" deve ser coerente com o nicho identificado e com o tema do video
- Se o nicho nao estiver claro, use gancho geral com beneficio pratico e linguagem simples
- Evite repetir sempre o mesmo prefixo antes de "|"
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
        raw_title = str(data.get("title", "")).strip()
        final_title = _compose_seo_automation_title(project, raw_title)
        return {
            "title": final_title,
            "description": _strip_lyrics_from_description(str(data.get("description", "")).strip()),
            "hashtags": str(data.get("hashtags", "")).strip(),
            "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
        }
    except Exception as e:
        logger.warning("AI publish metadata generation failed: %s", e)
        return {
            "title": _compose_seo_automation_title(project, project.title or "Video automatico"),
            "description": project.description or "",
            "hashtags": "",
            "tags": [],
        }
