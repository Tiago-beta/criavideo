import json
import logging
import re
from math import ceil
from typing import Any

import openai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key) if (settings.openai_api_key or "").strip() else None

_ALLOWED_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4"}
_ALLOWED_PERSONA_TYPES = {"homem", "mulher", "crianca", "familia", "natureza", "desenho", "personalizado"}
_PERSONA_TYPE_ALIASES = {
    "man": "homem",
    "male": "homem",
    "boy": "homem",
    "homem": "homem",
    "mulher": "mulher",
    "woman": "mulher",
    "female": "mulher",
    "girl": "mulher",
    "crianca": "crianca",
    "crianca": "crianca",
    "child": "crianca",
    "kid": "crianca",
    "familia": "familia",
    "family": "familia",
    "natureza": "natureza",
    "animal": "natureza",
    "pet": "natureza",
    "nature": "natureza",
    "desenho": "desenho",
    "anime": "desenho",
    "cartoon": "desenho",
    "drawing": "desenho",
    "personalizado": "personalizado",
    "custom": "personalizado",
    "character": "personalizado",
}


def _pick_model() -> str:
    configured = [item.strip() for item in str(settings.openai_analysis_models or "").split(",") if item.strip()]
    return configured[0] if configured else "gpt-5"


def _completion_request_kwargs(model_name: str, token_limit: int) -> dict[str, Any]:
    normalized = str(model_name or "").strip().lower()
    if normalized.startswith("gpt-5"):
        return {"max_completion_tokens": token_limit}
    return {
        "max_tokens": token_limit,
        "temperature": 0.35,
    }


def _clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _normalize_aspect_ratio(value: Any, fallback: str = "16:9") -> str:
    raw = str(value or "").strip().lower().replace(" ", "")
    if raw in _ALLOWED_ASPECT_RATIOS:
        return raw
    return fallback if fallback in _ALLOWED_ASPECT_RATIOS else "16:9"


def _normalize_persona_type(value: Any, fallback: str = "personalizado") -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    normalized = _PERSONA_TYPE_ALIASES.get(raw, raw)
    if normalized in _ALLOWED_PERSONA_TYPES:
        return normalized
    return fallback


def _safe_positive_int(value: Any, fallback: int, minimum: int = 1, maximum: int = 24) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = fallback
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _safe_positive_seconds(value: Any, fallback: int, minimum: int = 15, maximum: int = 14400) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = fallback
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _extract_aspect_ratio(message: str, fallback: str = "16:9") -> str:
    match = re.search(r"\b(1:1|16:9|9:16|4:3|3:4)\b", str(message or ""))
    return _normalize_aspect_ratio(match.group(1) if match else fallback, fallback)


def _extract_total_duration_seconds(message: str, fallback: int) -> int:
    text = str(message or "")
    minute_match = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*(?:minutos|minuto|min)\b", text, flags=re.IGNORECASE)
    if minute_match:
        try:
            return _safe_positive_seconds(float(minute_match.group(1).replace(",", ".")) * 60, fallback)
        except Exception:
            pass
    second_match = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*(?:segundos|segundo|secs|sec|s)\b", text, flags=re.IGNORECASE)
    if second_match:
        try:
            return _safe_positive_seconds(float(second_match.group(1).replace(",", ".")), fallback)
        except Exception:
            pass
    return _safe_positive_seconds(fallback, fallback)


def _extract_episode_duration_seconds(message: str, total_duration_seconds: int, fallback: int = 60) -> int:
    text = str(message or "")
    episode_match = re.search(
        r"epis(?:odio|odios|ódio|ódios|odio|odio)s?[^\d]{0,24}(\d+(?:[\.,]\d+)?)\s*(minutos|minuto|min|segundos|segundo|secs|sec|s)",
        text,
        flags=re.IGNORECASE,
    )
    if episode_match:
        raw_value = float(episode_match.group(1).replace(",", "."))
        unit = episode_match.group(2).lower()
        seconds = raw_value * 60 if unit.startswith("min") else raw_value
        return _safe_positive_seconds(seconds, fallback, minimum=15, maximum=max(15, total_duration_seconds))
    return _safe_positive_seconds(fallback, fallback, minimum=15, maximum=max(15, total_duration_seconds))


def _extract_episode_count(message: str, kind: str, total_duration_seconds: int, episode_duration_seconds: int, fallback: int) -> int:
    if kind == "film":
        return 1
    text = str(message or "")
    explicit = re.search(r"\b(\d{1,2})\s*epis(?:odio|odios|ódio|ódios|odio|odio)s?\b", text, flags=re.IGNORECASE)
    if explicit:
        return _safe_positive_int(explicit.group(1), fallback, minimum=1, maximum=24)
    if episode_duration_seconds > 0:
        inferred = max(1, int(round(total_duration_seconds / max(episode_duration_seconds, 1))))
        return _safe_positive_int(inferred, fallback, minimum=1, maximum=24)
    return _safe_positive_int(fallback, fallback, minimum=1, maximum=24)


def _default_title(kind: str) -> str:
    if kind == "film":
        return "Novo filme"
    if kind == "drama":
        return "Novo drama"
    return "Nova serie"


def _infer_title(existing_title: str, message: str, kind: str) -> str:
    cleaned_existing = _clean_text(existing_title)
    if cleaned_existing and cleaned_existing.lower() not in {"series", "séries", "nova serie", "novo filme", "novo drama"}:
        return cleaned_existing

    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text:
        return _default_title(kind)

    snippet = text[:72].strip(" .,:;!?")
    if len(snippet) < 8:
        return _default_title(kind)
    return snippet[:1].upper() + snippet[1:]


def _build_scene_blueprint(base_label: str, summary: str, characters: list[dict[str, Any]], objects: list[dict[str, Any]], duration_seconds: int, count: int) -> list[dict[str, Any]]:
    total = max(1, count)
    per_scene = max(8, int(round(duration_seconds / total)))
    character_names = [str(item.get("name") or "").strip() for item in characters if str(item.get("name") or "").strip()]
    object_names = [str(item.get("name") or "").strip() for item in objects if str(item.get("name") or "").strip()]
    scenes: list[dict[str, Any]] = []
    for index in range(total):
        scene_number = index + 1
        if scene_number == 1:
            beat = f"Apresenta {base_label.lower()} e estabelece o conflito principal."
        elif scene_number == total:
            beat = f"Fecha {base_label.lower()} com gancho ou virada para o proximo passo."
        else:
            beat = f"Aprofunda a progressao dramatica de {base_label.lower()} com obstaculos e decisoes."
        scenes.append(
            {
                "scene_number": scene_number,
                "title": f"Cena {scene_number}",
                "beat": beat,
                "location": "Definir no storyboard",
                "duration_seconds": per_scene,
                "characters": character_names[:4],
                "objects": object_names[:4],
                "image_prompt": (
                    f"{summary} Cena {scene_number}. Mostrar acao central, ambiente, figurino, iluminacao e objetos-chave "
                    f"com clareza cinematografica."
                ).strip(),
            }
        )
    return scenes


def _build_fallback_plan(kind: str, existing_title: str, message: str, language: str = "pt-BR") -> dict[str, Any]:
    default_total = 1800 if kind == "film" else (1200 if kind == "drama" else 900)
    total_duration_seconds = _extract_total_duration_seconds(message, default_total)
    episode_duration_seconds = total_duration_seconds if kind == "film" else _extract_episode_duration_seconds(message, total_duration_seconds, 60)
    episode_count = _extract_episode_count(message, kind, total_duration_seconds, episode_duration_seconds, 1 if kind == "film" else 5)
    title = _infer_title(existing_title, message, kind)
    aspect_ratio = _extract_aspect_ratio(message, "16:9")

    protagonist_label = "Protagonista"
    mentor_label = "Aliado central"
    object_label = "Objeto-chave"
    if "empresa" in message.lower() or "empre" in message.lower():
        object_label = "Documento ou plano do negocio"

    characters = [
        {
            "id": "char-protagonista",
            "name": protagonist_label,
            "role": "Conduz a jornada principal e carrega o arco emocional da historia.",
            "summary": _clean_text(message, "Personagem principal da obra."),
            "persona_type": "personalizado",
        }
    ]
    if kind != "film":
        characters.append(
            {
                "id": "char-aliado",
                "name": mentor_label,
                "role": "Apoia, confronta ou acelera a transformacao do protagonista.",
                "summary": "Figura recorrente que reforca conflito, apoio ou contraste.",
                "persona_type": "personalizado",
            }
        )

    objects = [
        {
            "id": "object-chave",
            "name": object_label,
            "summary": "Elemento visual recorrente que ajuda a contar a historia e reforcar objetivo, status ou memoria.",
            "image_prompt": f"Objeto cinematografico: {object_label}. Fundo limpo, detalhes claros, iluminacao dramatica e acabamento premium.",
        }
    ]

    episodes: list[dict[str, Any]] = []
    for index in range(episode_count):
        episode_number = index + 1
        if kind == "film":
            episode_title = title
            synopsis = "Estrutura unica da obra, com apresentacao, escalada, crise e resolucao."
        elif kind == "drama":
            episode_title = f"Capitulo {episode_number}"
            synopsis = f"Capitulo {episode_number} aprofunda o drama, os conflitos e os ganchos emocionais da historia."
        else:
            episode_title = f"Episodio {episode_number}"
            synopsis = f"Episodio {episode_number} expande o arco principal, avanca a transformacao do protagonista e deixa um gancho para o proximo bloco."
        summary = f"{title}. {synopsis}"
        scenes = _build_scene_blueprint(episode_title, summary, characters, objects, episode_duration_seconds, 4 if episode_duration_seconds >= 50 else 3)
        episodes.append(
            {
                "episode_number": episode_number,
                "title": episode_title,
                "synopsis": synopsis,
                "duration_seconds": episode_duration_seconds if kind != "film" else total_duration_seconds,
                "scenes": scenes,
            }
        )

    scene_bank = []
    for episode in episodes[: min(len(episodes), 6)]:
        first_scene = episode["scenes"][0] if episode["scenes"] else None
        if not first_scene:
            continue
        scene_bank.append(
            {
                "id": f"scene-{episode['episode_number']}",
                "name": f"{episode['title']} - {first_scene['title']}",
                "summary": first_scene["beat"],
                "image_prompt": first_scene["image_prompt"],
            }
        )

    return {
        "title": title,
        "project_overview": _clean_text(
            message,
            "Projeto longo estruturado para desenvolver historia, personagens, cenas recorrentes e material visual.",
        ),
        "build_requirements": [
            "Definir o arco principal e o objetivo dramático da obra.",
            "Organizar episodios, ganchos e progressao do conflito.",
            "Listar personagens recorrentes, cenarios e objetos essenciais.",
            "Preparar cenas e referencias visuais para o storyboard e a timeline.",
        ],
        "aspect_ratio": aspect_ratio,
        "language": language or "pt-BR",
        "target_duration_seconds": total_duration_seconds,
        "episode_count": episode_count,
        "characters": characters,
        "scenes": scene_bank,
        "objects": objects,
        "episodes": episodes,
        "assistant_reply": (
            f"Estruturei o projeto em {episode_count} bloco(s) com duracao total de {total_duration_seconds}s. "
            "A aba Projeto agora concentra a visao geral, a duracao total e os episodios; em Roteiro deixei a decomposicao por cenas para voce seguir no proximo passo."
        ),
    }


def _normalize_character(item: Any, index: int) -> dict[str, Any]:
    raw = item if isinstance(item, dict) else {}
    name = _clean_text(raw.get("name"), f"Personagem {index + 1}")
    role = _clean_text(raw.get("role"), "Personagem recorrente na estrutura principal.")
    summary = _clean_text(raw.get("summary") or raw.get("description"), role)
    return {
        "id": _clean_text(raw.get("id"), f"char-{index + 1}"),
        "name": name,
        "role": role,
        "summary": summary,
        "persona_type": _normalize_persona_type(raw.get("persona_type") or raw.get("type")),
    }


def _normalize_visual_item(item: Any, index: int, prefix: str) -> dict[str, Any]:
    raw = item if isinstance(item, dict) else {}
    name = _clean_text(raw.get("name"), f"{prefix.title()} {index + 1}")
    summary = _clean_text(raw.get("summary") or raw.get("description"), f"{prefix.title()} importante para a historia.")
    return {
        "id": _clean_text(raw.get("id"), f"{prefix}-{index + 1}"),
        "name": name,
        "summary": summary,
        "image_prompt": _clean_text(raw.get("image_prompt"), f"{name}. {summary}"),
    }


def _normalize_scene(item: Any, index: int, episode_title: str, characters: list[dict[str, Any]], objects: list[dict[str, Any]], default_duration: int) -> dict[str, Any]:
    raw = item if isinstance(item, dict) else {}
    name = _clean_text(raw.get("title") or raw.get("name"), f"Cena {index + 1}")
    beat = _clean_text(raw.get("beat") or raw.get("summary") or raw.get("description"), f"Acontecimento central de {episode_title.lower()}.")
    duration_seconds = _safe_positive_seconds(raw.get("duration_seconds") or raw.get("duration") or default_duration, default_duration, minimum=5, maximum=900)
    available_character_names = [entry["name"] for entry in characters if entry.get("name")]
    available_object_names = [entry["name"] for entry in objects if entry.get("name")]

    def _normalize_name_list(values: Any, fallback_values: list[str]) -> list[str]:
        if not isinstance(values, list):
            return fallback_values[:3]
        cleaned: list[str] = []
        for value in values:
            text = _clean_text(value)
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned[:4] if cleaned else fallback_values[:3]

    return {
        "scene_number": _safe_positive_int(raw.get("scene_number") or raw.get("index") or (index + 1), index + 1, minimum=1, maximum=99),
        "title": name,
        "beat": beat,
        "location": _clean_text(raw.get("location"), "Definir no storyboard"),
        "duration_seconds": duration_seconds,
        "characters": _normalize_name_list(raw.get("characters"), available_character_names),
        "objects": _normalize_name_list(raw.get("objects"), available_object_names),
        "image_prompt": _clean_text(raw.get("image_prompt"), f"{episode_title}. {name}. {beat}"),
    }


def _normalize_episode(item: Any, index: int, kind: str, total_duration_seconds: int, episode_duration_seconds: int, characters: list[dict[str, Any]], objects: list[dict[str, Any]]) -> dict[str, Any]:
    raw = item if isinstance(item, dict) else {}
    episode_number = _safe_positive_int(raw.get("episode_number") or raw.get("number") or (index + 1), index + 1, minimum=1, maximum=99)
    if kind == "film":
        fallback_title = _clean_text(raw.get("title"), "Filme principal")
    elif kind == "drama":
        fallback_title = _clean_text(raw.get("title"), f"Capitulo {episode_number}")
    else:
        fallback_title = _clean_text(raw.get("title"), f"Episodio {episode_number}")
    synopsis = _clean_text(raw.get("synopsis") or raw.get("summary"), f"Bloco {episode_number} da historia principal.")
    duration_seconds = _safe_positive_seconds(
        raw.get("duration_seconds") or raw.get("duration") or (total_duration_seconds if kind == "film" else episode_duration_seconds),
        total_duration_seconds if kind == "film" else episode_duration_seconds,
        minimum=15,
        maximum=max(total_duration_seconds, episode_duration_seconds, 15),
    )
    scene_default_duration = max(8, int(round(duration_seconds / 4)))
    raw_scenes = raw.get("scenes") if isinstance(raw.get("scenes"), list) else []
    scenes = [
        _normalize_scene(scene, scene_index, fallback_title, characters, objects, scene_default_duration)
        for scene_index, scene in enumerate(raw_scenes[:12])
    ]
    if not scenes:
        scenes = _build_scene_blueprint(fallback_title, synopsis, characters, objects, duration_seconds, 4 if duration_seconds >= 50 else 3)
    return {
        "episode_number": episode_number,
        "title": fallback_title,
        "synopsis": synopsis,
        "duration_seconds": duration_seconds,
        "scenes": scenes,
    }


def _normalize_plan(raw_payload: Any, fallback_plan: dict[str, Any], kind: str, existing_title: str) -> dict[str, Any]:
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    normalized = dict(fallback_plan)
    normalized["title"] = _clean_text(raw.get("title"), fallback_plan["title"])
    normalized["project_overview"] = _clean_text(raw.get("project_overview") or raw.get("overview") or raw.get("logline"), fallback_plan["project_overview"])
    normalized["build_requirements"] = [
        _clean_text(item) for item in (raw.get("build_requirements") if isinstance(raw.get("build_requirements"), list) else []) if _clean_text(item)
    ] or fallback_plan["build_requirements"]
    normalized["aspect_ratio"] = _normalize_aspect_ratio(raw.get("aspect_ratio"), fallback_plan["aspect_ratio"])
    normalized["language"] = _clean_text(raw.get("language"), fallback_plan["language"])
    normalized["target_duration_seconds"] = _safe_positive_seconds(raw.get("target_duration_seconds"), fallback_plan["target_duration_seconds"], minimum=30, maximum=14400)

    characters = [
        _normalize_character(item, index)
        for index, item in enumerate(raw.get("characters") if isinstance(raw.get("characters"), list) else fallback_plan["characters"])
    ]
    if not characters:
        characters = fallback_plan["characters"]
    normalized["characters"] = characters[:12]

    objects = [
        _normalize_visual_item(item, index, "object")
        for index, item in enumerate(raw.get("objects") if isinstance(raw.get("objects"), list) else fallback_plan["objects"])
    ]
    if not objects:
        objects = fallback_plan["objects"]
    normalized["objects"] = objects[:18]

    requested_episode_count = raw.get("episode_count") or raw.get("episodes_count") or len(raw.get("episodes") or []) or fallback_plan["episode_count"]
    normalized_episode_count = _safe_positive_int(requested_episode_count, fallback_plan["episode_count"], minimum=1, maximum=24)
    if kind == "film":
        normalized_episode_count = 1

    raw_episodes = raw.get("episodes") if isinstance(raw.get("episodes"), list) else []
    episodes = [
        _normalize_episode(item, index, kind, normalized["target_duration_seconds"], max(15, int(round(normalized["target_duration_seconds"] / max(normalized_episode_count, 1)))), normalized["characters"], normalized["objects"])
        for index, item in enumerate(raw_episodes[:normalized_episode_count])
    ]
    if not episodes:
        episodes = fallback_plan["episodes"]

    while len(episodes) < normalized_episode_count:
        fallback_episode = fallback_plan["episodes"][min(len(episodes), len(fallback_plan["episodes"]) - 1)]
        duplicate = json.loads(json.dumps(fallback_episode))
        duplicate["episode_number"] = len(episodes) + 1
        if kind == "drama":
            duplicate["title"] = f"Capitulo {duplicate['episode_number']}"
        elif kind != "film":
            duplicate["title"] = f"Episodio {duplicate['episode_number']}"
        episodes.append(duplicate)
    normalized["episodes"] = episodes[:normalized_episode_count]
    normalized["episode_count"] = len(normalized["episodes"])

    scenes = [
        _normalize_visual_item(item, index, "scene")
        for index, item in enumerate(raw.get("scenes") if isinstance(raw.get("scenes"), list) else [])
    ]
    if not scenes:
        scenes = fallback_plan["scenes"]
    normalized["scenes"] = scenes[:24]

    assistant_reply = _clean_text(raw.get("assistant_reply"), "")
    if not assistant_reply:
        assistant_reply = (
            f"Estruturei {normalized['episode_count']} bloco(s) para {normalized['title']}. "
            "A aba Projeto agora concentra a visao geral e a duracao total, enquanto a aba Roteiro recebeu a decomposicao por cenas pronta para o proximo passo."
        )
    normalized["assistant_reply"] = assistant_reply

    if not normalized["title"]:
        normalized["title"] = _infer_title(existing_title, fallback_plan["project_overview"], kind)

    return normalized


async def _build_openai_plan(kind: str, existing_title: str, message: str, language: str, target_tab: str, fallback_plan: dict[str, Any], existing_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if _openai is None:
        return fallback_plan

    safe_context = existing_context if isinstance(existing_context, dict) else {}
    context_json = json.dumps(safe_context, ensure_ascii=False)
    system_prompt = (
        "Voce e um showrunner senior e diretor de producao audiovisual. "
        "Sua tarefa e transformar um briefing curto em um projeto longo pronto para pre-producao. "
        "Responda APENAS um JSON valido. Nao use markdown, nao use comentarios e nao envolva em crases. "
        "O JSON deve conter exatamente estas chaves principais: "
        "title, project_overview, build_requirements, aspect_ratio, language, target_duration_seconds, episode_count, characters, scenes, objects, episodes, assistant_reply. "
        "Regras: "
        "1) project_overview em PT-BR, direto e concreto. "
        "2) build_requirements deve listar o que precisa existir para construir a obra. "
        "3) characters deve trazer name, role, summary e persona_type. persona_type deve ser um destes valores: homem, mulher, crianca, familia, natureza, desenho, personalizado. "
        "4) scenes e objects devem ser bancos reutilizaveis para storyboard, cada item com name, summary e image_prompt. "
        "5) episodes deve ser uma lista de blocos prontos para a aba Roteiro. Cada episodio precisa de episode_number, title, synopsis, duration_seconds e scenes. "
        "6) Cada scene interna precisa de scene_number, title, beat, location, duration_seconds, characters, objects e image_prompt. "
        "7) A duracao total e a quantidade de episodios devem respeitar o briefing do usuario sempre que houver informacao explicita. "
        "8) Se kind for film, gere apenas um episodio principal. "
        "9) Use nomes e detalhes fortes, mas sem excesso de floreio. "
        "10) assistant_reply deve resumir o que foi montado e orientar o usuario para o proximo passo."
    )
    user_prompt = (
        f"kind={kind}\n"
        f"target_tab={target_tab}\n"
        f"language={language or 'pt-BR'}\n"
        f"current_title={existing_title or _default_title(kind)}\n"
        f"fallback_json={json.dumps(fallback_plan, ensure_ascii=False)}\n"
        f"existing_context_json={context_json}\n"
        "briefing_usuario:\n"
        f"{message}"
    )

    model_candidates = [_pick_model(), "gpt-4o-mini"]
    last_error: Exception | None = None
    for model_name in model_candidates:
        try:
            response = await _openai.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                **_completion_request_kwargs(model_name, 3200),
            )
            raw_content = _clean_text(response.choices[0].message.content)
            if not raw_content:
                continue
            payload = json.loads(raw_content)
            if isinstance(payload, dict):
                return _normalize_plan(payload, fallback_plan, kind, existing_title)
        except Exception as exc:
            last_error = exc
            logger.warning("Series planner failed with %s: %s", model_name, exc)
    if last_error is not None:
        logger.warning("Series planner fallback activated: %s", last_error)
    return fallback_plan


async def build_series_workspace_plan(
    *,
    kind: str,
    existing_title: str,
    message: str,
    language: str = "pt-BR",
    target_tab: str = "projeto",
    existing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback_plan = _build_fallback_plan(kind, existing_title, message, language=language)
    return await _build_openai_plan(
        kind=kind,
        existing_title=existing_title,
        message=message,
        language=language,
        target_tab=target_tab,
        fallback_plan=fallback_plan,
        existing_context=existing_context,
    )
