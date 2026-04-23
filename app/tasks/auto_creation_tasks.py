"""
Auto-creation tasks — Automated video generation triggered by scheduler.
"""
import asyncio
import logging
import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.config import get_settings
from app.models import (
    AutoPilotCycleRun, AutoSchedule, AutoScheduleTheme, VideoProject, VideoStatus,
    PublishJob, PublishStatus, SocialAccount, VideoRender,
)
from app.services.persona_registry import (
    build_persona_reference_montage,
    resolve_persona_reference_image,
    resolve_persona_reference_images,
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
    raw = _clean_title_part(ai_title, max_len=90)
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


async def run_auto_creation(auto_schedule_id: int):
    """Main auto-creation pipeline: pick next theme, create video, publish."""
    pilot_cycle_key = ""
    pilot_stream = ""
    schedule_user_id = 0
    theme_entry_id = 0
    theme_entry_text = ""

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

        schedule_user_id = int(schedule.user_id)
        theme_entry_id = int(theme_entry.id)
        theme_entry_text = str(theme_entry.theme or "")
        pilot_cycle_key = str((theme_entry.custom_settings or {}).get("pilot_cycle_key") or "").strip()
        pilot_stream = str((schedule.default_settings or {}).get("pilot_stream") or "").strip().lower()

        logger.info(
            "Auto-creation started: schedule=%d, theme=%d '%s', mode=%s, type=%s",
            auto_schedule_id, theme_entry.id, theme_entry.theme,
            schedule.creation_mode, schedule.video_type,
        )

    if pilot_cycle_key:
        await _mark_pilot_cycle_status(cycle_key=pilot_cycle_key, status="running")

    # Run the pipeline outside the DB session to avoid long-held connections
    try:
        project_id = await _create_video_for_theme(
            schedule_id=auto_schedule_id,
            theme_id=theme_entry_id,
            theme_text=theme_entry_text,
            user_id=schedule_user_id,
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
                    user_id=schedule_user_id,
                    platform=schedule.platform,
                    social_account_id=schedule.social_account_id,
                )
            else:
                logger.info(
                    "Auto-creation in test mode (no publish): schedule=%d, theme=%d, project=%d",
                    auto_schedule_id,
                    theme_entry_id,
                    project_id,
                )

            async with async_session() as db:
                theme = await db.get(AutoScheduleTheme, theme_entry_id)
                if theme:
                    theme.status = "completed"
                    theme.video_project_id = project_id
                    await db.commit()

            if pilot_stream == "long":
                enqueue_result = await _enqueue_pilot_shorts_from_long_project(
                    long_schedule_id=auto_schedule_id,
                    long_theme_id=theme_entry_id,
                    long_project_id=project_id,
                )
                added_shorts = int(enqueue_result.get("added", 0))
                short_schedule_id = int(enqueue_result.get("short_schedule_id", 0))

                if added_shorts > 0 and short_schedule_id:
                    for _ in range(added_shorts):
                        await run_auto_creation(short_schedule_id)
                elif pilot_cycle_key:
                    await _mark_pilot_cycle_status(cycle_key=pilot_cycle_key, status="completed")

            if pilot_stream == "short" and pilot_cycle_key:
                await _mark_pilot_short_completed(cycle_key=pilot_cycle_key)

            logger.info("Auto-creation completed: schedule=%d, theme=%d, project=%d", auto_schedule_id, theme_entry_id, project_id)
        else:
            async with async_session() as db:
                theme = await db.get(AutoScheduleTheme, theme_entry_id)
                if theme:
                    theme.status = "failed"
                    theme.error_message = "Video rendering timed out or failed"
                    await db.commit()

            if pilot_cycle_key:
                await _mark_pilot_cycle_status(
                    cycle_key=pilot_cycle_key,
                    status="failed",
                    error_message="Video rendering timed out or failed",
                )

    except Exception as e:
        logger.error("Auto-creation failed: schedule=%d, theme=%d, error=%s", auto_schedule_id, theme_entry_id, e)
        async with async_session() as db:
            theme = await db.get(AutoScheduleTheme, theme_entry_id)
            if theme:
                theme.status = "failed"
                theme.error_message = str(e)[:500]
                await db.commit()

        if pilot_cycle_key:
            await _mark_pilot_cycle_status(
                cycle_key=pilot_cycle_key,
                status="failed",
                error_message=str(e)[:500],
            )


def _resolve_short_render_modes(shorts_per_cycle: int, short_mix_mode: str) -> list[str]:
    count = max(1, int(shorts_per_cycle or 1))
    mode = str(short_mix_mode or "realistic_all").strip().lower()

    if mode == "image_all":
        return ["image"] * count

    if mode == "mixed_realistic2_image1":
        if count == 1:
            return ["realistic"]
        if count == 2:
            return ["realistic", "image"]
        resolved = ["realistic", "realistic"]
        while len(resolved) < count:
            resolved.append("image")
        return resolved

    return ["realistic"] * count


def _build_short_clip_starts(total_duration: float, clip_duration: float, count: int) -> list[float]:
    total = max(0.0, float(total_duration or 0.0))
    clip = max(6.0, float(clip_duration or 10.0))
    qty = max(1, int(count or 1))

    max_start = max(0.0, total - clip - 0.5)
    if max_start <= 0:
        return [0.0] * qty

    first = min(max_start, max(0.0, min(18.0, total * 0.2)))
    if qty == 1:
        return [round(first, 2)]

    end = max(first, max_start - min(max_start, max(0.0, total * 0.06)))
    if end <= first:
        end = max_start

    step = (end - first) / max(1, qty - 1)
    starts = [round(first + (step * idx), 2) for idx in range(qty)]
    return [max(0.0, min(max_start, value)) for value in starts]


async def _mark_pilot_cycle_status(cycle_key: str, status: str, error_message: str = "") -> None:
    if not cycle_key:
        return

    async with async_session() as db:
        result = await db.execute(
            select(AutoPilotCycleRun)
            .where(AutoPilotCycleRun.cycle_key == cycle_key)
            .limit(1)
        )
        cycle = result.scalar_one_or_none()
        if not cycle:
            return

        now = datetime.utcnow()
        cycle.status = str(status or "planned")
        if cycle.status == "running" and not cycle.started_at:
            cycle.started_at = now
        if cycle.status in {"completed", "failed"}:
            cycle.completed_at = now
        if error_message:
            cycle.error_message = str(error_message)[:1000]
        elif cycle.status == "completed":
            cycle.error_message = None

        await db.commit()


async def _mark_pilot_short_completed(cycle_key: str) -> None:
    if not cycle_key:
        return

    async with async_session() as db:
        result = await db.execute(
            select(AutoPilotCycleRun)
            .where(AutoPilotCycleRun.cycle_key == cycle_key)
            .limit(1)
        )
        cycle = result.scalar_one_or_none()
        if not cycle:
            return

        now = datetime.utcnow()
        if not cycle.started_at:
            cycle.started_at = now

        planned = max(1, int(cycle.planned_shorts or 1))
        done = int(cycle.completed_shorts or 0) + 1
        cycle.completed_shorts = min(done, planned)

        if cycle.completed_shorts >= planned:
            cycle.status = "completed"
            cycle.completed_at = now
            cycle.error_message = None
        else:
            cycle.status = "running"

        await db.commit()


async def _enqueue_pilot_shorts_from_long_project(
    long_schedule_id: int,
    long_theme_id: int,
    long_project_id: int,
) -> dict:
    """Create short themes from a completed long music project."""
    async with async_session() as db:
        long_schedule = await db.get(AutoSchedule, long_schedule_id)
        long_theme = await db.get(AutoScheduleTheme, long_theme_id)
        project = await db.get(VideoProject, long_project_id)

        if not long_schedule or not long_theme or not project:
            return {"added": 0, "short_schedule_id": 0}

        long_cfg = dict(long_schedule.default_settings or {})
        if not long_cfg.get("pilot_mode") or str(long_cfg.get("pilot_stream") or "").lower() != "long":
            return {"added": 0, "short_schedule_id": 0}

        short_schedule_id = int(long_cfg.get("pilot_short_schedule_id") or 0)
        if short_schedule_id <= 0:
            return {"added": 0, "short_schedule_id": 0}

        short_schedule = await db.get(AutoSchedule, short_schedule_id)
        if not short_schedule or short_schedule.user_id != long_schedule.user_id:
            return {"added": 0, "short_schedule_id": 0}

        audio_local_path = str(project.audio_path or "").strip()
        project_tags = project.tags if isinstance(project.tags, dict) else {}
        audio_remote_url = str(project_tags.get("tevoxi_audio_url") or "").strip()
        if not audio_local_path and not audio_remote_url:
            logger.warning(
                "Pilot long project has no reusable audio source: schedule=%d theme=%d project=%d",
                long_schedule_id,
                long_theme_id,
                long_project_id,
            )
            return {"added": 0, "short_schedule_id": short_schedule_id}

        custom_cfg = long_theme.custom_settings if isinstance(long_theme.custom_settings, dict) else {}
        cycle_key = str(custom_cfg.get("pilot_cycle_key") or "").strip()
        short_mix_mode = str(custom_cfg.get("pilot_short_mix_mode") or long_cfg.get("pilot_short_mix_mode") or "realistic_all").strip().lower()

        try:
            shorts_per_cycle = int(custom_cfg.get("pilot_shorts_per_cycle") or long_cfg.get("pilot_shorts_per_cycle") or 3)
        except Exception:
            shorts_per_cycle = 3
        shorts_per_cycle = max(1, min(shorts_per_cycle, 6))

        short_modes_cfg = custom_cfg.get("pilot_short_modes")
        if isinstance(short_modes_cfg, list) and short_modes_cfg:
            short_modes = [str(mode or "realistic").strip().lower() for mode in short_modes_cfg][:shorts_per_cycle]
            while len(short_modes) < shorts_per_cycle:
                short_modes.append("realistic")
        else:
            short_modes = _resolve_short_render_modes(shorts_per_cycle, short_mix_mode)

        song_duration = float(project.track_duration or 0)
        clip_duration = 10.0
        if song_duration > 0:
            if song_duration >= 90:
                clip_duration = 14.0
            elif song_duration >= 60:
                clip_duration = 12.0
            elif song_duration >= 40:
                clip_duration = 10.0
            else:
                clip_duration = max(8.0, min(10.0, round(song_duration * 0.25, 1)))

        clip_starts = _build_short_clip_starts(song_duration, clip_duration, shorts_per_cycle)

        existing_result = await db.execute(
            select(AutoScheduleTheme)
            .where(AutoScheduleTheme.auto_schedule_id == short_schedule_id)
            .order_by(AutoScheduleTheme.position.asc())
        )
        existing_themes = existing_result.scalars().all()
        max_pos = max([item.position for item in existing_themes], default=-1)

        base_theme_text = str(long_theme.theme or project.title or "Tema musical").strip()
        persona_type = str(project_tags.get("interaction_persona") or "natureza").strip().lower() or "natureza"
        persona_profile_ids = project_tags.get("persona_profile_ids") or []
        persona_profile_id = int(project_tags.get("persona_profile_id") or 0)

        added = 0
        for idx in range(shorts_per_cycle):
            max_pos += 1
            short_mode = short_modes[idx] if idx < len(short_modes) else "realistic"
            short_theme_title = f"{base_theme_text} - Short {idx + 1}"

            custom_settings = {
                "pilot_mode": True,
                "pilot_stream": "short",
                "pilot_cycle_key": cycle_key,
                "segment_index": idx,
                "clip_start": clip_starts[idx] if idx < len(clip_starts) else 0,
                "clip_duration": clip_duration,
                "tevoxi_audio_local_path": audio_local_path,
                "tevoxi_audio_url": audio_remote_url,
                "tevoxi_job_id": str(project_tags.get("tevoxi_job_id") or ""),
                "tevoxi_title": project.track_title or project.title or base_theme_text,
                "tevoxi_lyrics": project.lyrics_text or "",
                "tevoxi_duration": float(project.track_duration or 0),
                "interaction_persona": persona_type,
                "persona_profile_id": persona_profile_id,
                "persona_profile_ids": persona_profile_ids,
                "short_render_mode": "image" if short_mode == "image" else "realistic",
            }

            theme_entry = AutoScheduleTheme(
                auto_schedule_id=short_schedule_id,
                theme=short_theme_title,
                status="pending",
                position=max_pos,
                custom_settings=custom_settings,
            )
            db.add(theme_entry)
            added += 1

        if cycle_key:
            cycle_result = await db.execute(
                select(AutoPilotCycleRun)
                .where(AutoPilotCycleRun.cycle_key == cycle_key)
                .limit(1)
            )
            cycle = cycle_result.scalar_one_or_none()
            if cycle:
                cycle.status = "running"
                if not cycle.started_at:
                    cycle.started_at = datetime.utcnow()
                cycle.planned_shorts = shorts_per_cycle
                cycle.short_mix_mode = short_mix_mode

        await db.commit()

    return {"added": added, "short_schedule_id": short_schedule_id}


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
        short_render_mode = str(cfg.get("short_render_mode", "realistic") or "realistic").strip().lower()
        if short_render_mode == "image":
            return await _create_musical_image_short(theme_text, user_id, cfg, custom_settings)
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
        project_tags = {
            "audio_source": "tevoxi",
            "force_karaoke_two_line": True,
            "tevoxi_job_id": str(music_result.get("job_id") or ""),
            "tevoxi_audio_url": str(music_result.get("audio_url") or ""),
            "tevoxi_duration": float(music_result.get("duration") or 0),
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
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        credits_needed = CREDITS_PER_MINUTE  # 1 credit unit per realistic video
        await deduct_credits(db, user_id, credits_needed)

    # Build tags for the project
    tags = {
        "realistic_style": realistic_style,
        "interaction_persona": interaction_persona,
        "reference_source": "persona",
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
    from app.tasks.video_tasks import run_realistic_video_pipeline

    tevoxi_audio_url = cfg.get("tevoxi_audio_url", "")
    tevoxi_audio_local_path = cfg.get("tevoxi_audio_local_path", "")
    tevoxi_job_id = cfg.get("tevoxi_job_id", "")
    tevoxi_title = cfg.get("tevoxi_title", theme_text)
    clip_start = float(custom_settings.get("clip_start", 0))
    clip_duration = float(custom_settings.get("clip_duration", 10))
    segment_index = int(custom_settings.get("segment_index", 0))
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

    if not tevoxi_audio_url and not tevoxi_audio_local_path:
        raise RuntimeError("Audio Tevoxi nao configurado para short musical.")

    # Credit check (1 credit per short)
    async with async_session() as db:
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        credits_needed = CREDITS_PER_MINUTE  # 1 minute worth for each short
        await deduct_credits(db, user_id, credits_needed)

    # 1. Download full audio from Tevoxi
    source_key = tevoxi_job_id or str(user_id)
    audio_dir = Path(settings.media_dir) / "audio" / f"short_{source_key}_{segment_index}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    full_audio_path = audio_dir / "full_music.mp3"

    if tevoxi_audio_local_path and Path(str(tevoxi_audio_local_path)).exists():
        import shutil
        if not full_audio_path.exists():
            shutil.copy2(str(tevoxi_audio_local_path), str(full_audio_path))

    if not full_audio_path.exists() and tevoxi_audio_url:
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

    if not full_audio_path.exists():
        raise RuntimeError("Nao foi possivel obter o audio para gerar short musical")

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
    try:
        from app.services.transcriber import transcribe_audio
        lyrics_hint = cfg.get("tevoxi_lyrics", "")
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: transcribe_audio(segment_audio_path, prompt=lyrics_hint),
        )
        transcribed = (result.get("text", "") if isinstance(result, dict) else "").strip()
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
                "reference_source": "persona",
                "has_reference_image": True,
                "persona_profile_id": resolved_persona_profile_id,
                "persona_profile_ids": resolved_persona_profile_ids,
            },
            style_prompt=reference_image_path,
            aspect_ratio="9:16",
            track_title=tevoxi_title,
            track_artist="",
            track_duration=clip_duration,
            lyrics_text=visual_prompt,
            lyrics_words=[],
            audio_path=engine,  # engine selection stored here
            enable_subtitles=False,
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


async def _create_musical_image_short(
    theme_text: str,
    user_id: int,
    cfg: dict,
    custom_settings: dict,
) -> int:
    """Create a musical short using image pipeline (non-realistic) plus Tevoxi segment audio."""
    from app.tasks.video_tasks import run_video_pipeline

    tevoxi_audio_url = cfg.get("tevoxi_audio_url", "")
    tevoxi_audio_local_path = cfg.get("tevoxi_audio_local_path", "")
    tevoxi_job_id = cfg.get("tevoxi_job_id", "")
    tevoxi_title = cfg.get("tevoxi_title", theme_text)
    clip_start = float(custom_settings.get("clip_start", 0))
    clip_duration = float(custom_settings.get("clip_duration", 10))
    segment_index = int(custom_settings.get("segment_index", 0))

    if not tevoxi_audio_url and not tevoxi_audio_local_path:
        raise RuntimeError("Audio Tevoxi nao configurado para short musical (imagem).")

    async with async_session() as db:
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        await deduct_credits(db, user_id, CREDITS_PER_MINUTE)

    source_key = tevoxi_job_id or str(user_id)
    audio_dir = Path(settings.media_dir) / "audio" / f"short_img_{source_key}_{segment_index}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    full_audio_path = audio_dir / "full_music.mp3"

    if tevoxi_audio_local_path and Path(str(tevoxi_audio_local_path)).exists():
        import shutil
        if not full_audio_path.exists():
            shutil.copy2(str(tevoxi_audio_local_path), str(full_audio_path))

    if not full_audio_path.exists() and tevoxi_audio_url:
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

    if not full_audio_path.exists():
        raise RuntimeError("Nao foi possivel obter o audio para short musical (imagem)")

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
        raise RuntimeError("Falha ao extrair segmento de audio para short imagem")

    short_lyrics = str(cfg.get("tevoxi_lyrics", "") or "").strip()
    short_prompt = ""
    if short_lyrics:
        short_prompt = (
            f'Trecho da musica: "{" ".join(short_lyrics.split())[:420]}". '
            "Crie uma sequencia visual cinematografica em imagens, com narrativa forte e coerencia emocional."
        )
    if not short_prompt:
        short_prompt = (
            "Crie uma sequencia visual cinematografica em imagens para short musical, "
            "com ritmo dinamico e narrativa clara para mobile."
        )

    style_prompt = cfg.get("style_prompt", "cinematic, dramatic lighting, textured details")

    async with async_session() as db:
        project = VideoProject(
            user_id=user_id,
            track_id=0,
            title=f"{tevoxi_title} — Short {segment_index + 1}",
            description=short_prompt,
            tags={
                "musical_short": True,
                "short_render_mode": "image",
                "segment_index": segment_index,
                "clip_start": clip_start,
                "clip_duration": clip_duration,
                "segment_audio_path": segment_audio_path,
            },
            style_prompt=style_prompt,
            aspect_ratio="9:16",
            track_title=tevoxi_title,
            track_artist="",
            track_duration=clip_duration,
            lyrics_text=short_prompt,
            lyrics_words=[],
            audio_path=segment_audio_path,
            enable_subtitles=True,
            zoom_images=True,
            no_background_music=True,
            is_realistic=False,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        project_id = project.id

    await run_video_pipeline(project_id)
    return project_id


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

    prompt = f"""Voce e um estrategista de crescimento para canais novos no YouTube. Gere metadados otimizados para descoberta, clique e retencao.

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
