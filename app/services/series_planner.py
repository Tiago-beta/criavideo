import json
import logging
import re
from math import ceil
from typing import Any
import unicodedata

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
_NUMBER_WORDS = {
    "zero": 0,
    "meio": 0.5,
    "meia": 0.5,
    "one": 1,
    "um": 1,
    "uma": 1,
    "two": 2,
    "dois": 2,
    "duas": 2,
    "three": 3,
    "tres": 3,
    "four": 4,
    "quatro": 4,
    "five": 5,
    "cinco": 5,
    "six": 6,
    "seis": 6,
    "seven": 7,
    "sete": 7,
    "eight": 8,
    "oito": 8,
    "nine": 9,
    "nove": 9,
    "ten": 10,
    "dez": 10,
    "eleven": 11,
    "onze": 11,
    "twelve": 12,
    "doze": 12,
    "thirteen": 13,
    "treze": 13,
    "fourteen": 14,
    "quatorze": 14,
    "catorze": 14,
    "fifteen": 15,
    "quinze": 15,
    "sixteen": 16,
    "dezesseis": 16,
    "dezasseis": 16,
    "seventeen": 17,
    "dezessete": 17,
    "eighteen": 18,
    "dezoito": 18,
    "nineteen": 19,
    "dezenove": 19,
    "twenty": 20,
    "vinte": 20,
    "thirty": 30,
    "trinta": 30,
    "forty": 40,
    "quarenta": 40,
    "fifty": 50,
    "cinquenta": 50,
    "sixty": 60,
    "sessenta": 60,
    "seventy": 70,
    "setenta": 70,
    "eighty": 80,
    "oitenta": 80,
    "ninety": 90,
    "noventa": 90,
    "hundred": 100,
    "cem": 100,
    "cento": 100,
}
_NUMBER_PATTERN = "|".join(sorted((re.escape(key) for key in _NUMBER_WORDS), key=len, reverse=True))
_NUMBER_FRAGMENT_PATTERN = rf"(?:\d+(?:[\.,]\d+)?|(?:{_NUMBER_PATTERN})(?:\s+e\s+(?:{_NUMBER_PATTERN}))*)"


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


def _normalize_search_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def _parse_number_fragment(fragment: str) -> float | None:
    text = _normalize_search_text(fragment).strip()
    if not text:
        return None
    direct_match = re.fullmatch(r"\d+(?:[\.,]\d+)?", text)
    if direct_match:
        return float(text.replace(",", "."))

    total = 0.0
    for token in [item.strip() for item in text.split(" e ") if item.strip()]:
        if token not in _NUMBER_WORDS:
            return None
        total += float(_NUMBER_WORDS[token])
    return total if total > 0 else None


def _find_number_fragment(patterns: list[str], text: str) -> float | None:
    normalized_text = _normalize_search_text(text)
    for pattern in patterns:
        match = re.search(pattern, normalized_text, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = _parse_number_fragment(match.group(1))
        if parsed is not None:
            return parsed
    return None


def _build_story_treatment(
    title: str,
    kind: str,
    project_overview: str,
    message: str,
    characters: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
) -> str:
    cleaned_title = _clean_text(title, _default_title(kind))
    lead_character_names = [str(item.get("name") or "").strip() for item in characters if str(item.get("name") or "").strip()]
    lead_block = ", ".join(lead_character_names[:3]) if lead_character_names else "os personagens principais"
    intro = (
        f"{cleaned_title} acompanha {lead_block} em uma historia continua que parte do ponto de partida descrito no briefing e avanca do começo ao fim com conflitos, viradas e consequencias claras. "
        f"O eixo principal do projeto e este: {project_overview or _clean_text(message, 'A historia principal sera desenvolvida em etapas bem definidas.')}"
    )
    tension = (
        "A estrutura precisa sustentar uma progressao emocional longa, com cada bloco ampliando o impacto do anterior. "
        "Nada deve parecer solto: o que e apresentado no inicio vira conflito no meio e cobra resolucao no final, para que Projeto, Roteiro, Personagens e Storyboard apontem para a mesma direcao dramatica."
    )
    parts: list[str] = [intro, tension]
    for episode in episodes:
        episode_number = int(episode.get("episode_number") or 0)
        episode_title = _clean_text(episode.get("title"), f"Bloco {episode_number or 1}")
        synopsis = _clean_text(episode.get("synopsis"), "Sem sinopse definida ainda.")
        scenes = [item for item in (episode.get("scenes") if isinstance(episode.get("scenes"), list) else []) if isinstance(item, dict)]
        scene_lines = []
        for scene in scenes[:6]:
            scene_title = _clean_text(scene.get("title"), "Cena")
            scene_beat = _clean_text(scene.get("beat"), "A cena ainda precisa ser detalhada.")
            scene_lines.append(f"{scene_title}: {scene_beat}")
        middle = " ".join(scene_lines)
        parts.append(
            f"{episode_title} apresenta esta etapa da historia: {synopsis} {middle}".strip()
        )
    ending = (
        "Ao final, a obra precisa entregar sensacao de percurso completo: o protagonista nao apenas atravessa eventos, mas muda de posicao, encara o passado, redefine suas relacoes e fecha o arco principal com consequencia visual e emocional."
    )
    parts.append(ending)
    return "\n\n".join(part for part in parts if part.strip())


def _extract_briefing_constraints(message: str, kind: str) -> dict[str, Any]:
    normalized_message = _normalize_search_text(message)
    explicit_aspect_ratio = _extract_aspect_ratio(message, "")
    total_minutes_value = _find_number_fragment(
        [
            rf"duracao\s+total[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
            rf"tempo\s+total[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
            rf"(?:serie|filme|drama|obra|video|historia)\s+(?:curta|curto|longa|longo)?[^\d\w]{{0,20}}({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
            rf"({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
        ],
        normalized_message,
    )
    total_seconds_value = _find_number_fragment(
        [
            rf"duracao\s+total[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
            rf"tempo\s+total[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
            rf"({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
        ],
        normalized_message,
    )
    episode_minutes_value = _find_number_fragment(
        [
            rf"episodios?\s+de\s+({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
            rf"capitulos?\s+de\s+({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
            rf"cada\s+episodio[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
            rf"cada\s+capitulo[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:minutos|minuto|min)",
        ],
        normalized_message,
    )
    episode_seconds_value = _find_number_fragment(
        [
            rf"episodios?\s+de\s+({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
            rf"capitulos?\s+de\s+({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
            rf"cada\s+episodio[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
            rf"cada\s+capitulo[^\d\w]*({_NUMBER_FRAGMENT_PATTERN})\s*(?:segundos|segundo|secs|sec|s)",
        ],
        normalized_message,
    )
    episode_count_value = _find_number_fragment(
        [
            rf"({_NUMBER_FRAGMENT_PATTERN})\s*episodios?\b",
            rf"({_NUMBER_FRAGMENT_PATTERN})\s*capitulos?\b",
            rf"em\s+({_NUMBER_FRAGMENT_PATTERN})\s*partes\b",
        ],
        normalized_message,
    )

    total_duration_seconds = 0
    if total_minutes_value is not None:
        total_duration_seconds = int(round(total_minutes_value * 60))
    elif total_seconds_value is not None:
        total_duration_seconds = int(round(total_seconds_value))

    episode_duration_seconds = 0
    if kind != "film":
        if episode_minutes_value is not None:
            episode_duration_seconds = int(round(episode_minutes_value * 60))
        elif episode_seconds_value is not None:
            episode_duration_seconds = int(round(episode_seconds_value))

    episode_count = int(round(episode_count_value)) if episode_count_value is not None else 0
    if kind == "film":
        episode_count = 1
    elif not episode_count and total_duration_seconds and episode_duration_seconds:
        episode_count = max(1, int(round(total_duration_seconds / max(episode_duration_seconds, 1))))

    return {
        "aspect_ratio": explicit_aspect_ratio,
        "has_explicit_aspect_ratio": bool(explicit_aspect_ratio),
        "target_duration_seconds": total_duration_seconds,
        "has_explicit_target_duration": total_duration_seconds > 0,
        "episode_duration_seconds": episode_duration_seconds,
        "has_explicit_episode_duration": episode_duration_seconds > 0,
        "episode_count": episode_count,
        "has_explicit_episode_count": episode_count > 0,
    }


def _rebalance_scene_durations(scenes: list[dict[str, Any]], total_duration_seconds: int) -> list[dict[str, Any]]:
    if not scenes:
        return scenes
    count = max(1, len(scenes))
    base = max(5, int(total_duration_seconds // count))
    remainder = max(0, int(total_duration_seconds - (base * count)))
    balanced: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        extra = 1 if index < remainder else 0
        next_scene = dict(scene)
        next_scene["duration_seconds"] = base + extra
        balanced.append(next_scene)
    return balanced


def _normalize_aspect_ratio(value: Any, fallback: str = "16:9") -> str:
    raw = _normalize_search_text(value).replace(" ", "")
    raw = raw.replace("x", ":").replace("/", ":").replace("×", ":")
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
    normalized = _normalize_search_text(message)
    for width, height in re.findall(r"\b(1|3|4|9|16)\s*(?::|x|/|×|por)\s*(1|3|4|9|16)\b", normalized):
        ratio = f"{width}:{height}"
        if ratio in _ALLOWED_ASPECT_RATIOS:
            return ratio
    if any(token in normalized for token in ["vertical", "reels", "shorts", "tiktok", "retrato", "portrait"]):
        return "9:16"
    if any(token in normalized for token in ["quadrado", "square"]):
        return "1:1"
    if any(token in normalized for token in ["horizontal", "landscape", "youtube"]):
        return "16:9"
    return _normalize_aspect_ratio(fallback, fallback)


def _extract_total_duration_seconds(message: str, fallback: int) -> int:
    constraints = _extract_briefing_constraints(message, "series")
    if constraints.get("has_explicit_target_duration"):
        return _safe_positive_seconds(constraints.get("target_duration_seconds"), fallback)
    return _safe_positive_seconds(fallback, fallback)


def _extract_episode_duration_seconds(message: str, total_duration_seconds: int, fallback: int = 60) -> int:
    constraints = _extract_briefing_constraints(message, "series")
    if constraints.get("has_explicit_episode_duration"):
        return _safe_positive_seconds(constraints.get("episode_duration_seconds"), fallback, minimum=15, maximum=max(15, total_duration_seconds))
    return _safe_positive_seconds(fallback, fallback, minimum=15, maximum=max(15, total_duration_seconds))


def _extract_episode_count(message: str, kind: str, total_duration_seconds: int, episode_duration_seconds: int, fallback: int) -> int:
    if kind == "film":
        return 1
    constraints = _extract_briefing_constraints(message, kind)
    if constraints.get("has_explicit_episode_count"):
        return _safe_positive_int(constraints.get("episode_count"), fallback, minimum=1, maximum=24)
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
    constraints = _extract_briefing_constraints(message, kind)
    default_total = 1800 if kind == "film" else (1200 if kind == "drama" else 900)
    total_duration_seconds = _safe_positive_seconds(constraints.get("target_duration_seconds") or _extract_total_duration_seconds(message, default_total), default_total)
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

    story_treatment = _build_story_treatment(
        title=title,
        kind=kind,
        project_overview=_clean_text(message, "Projeto longo estruturado para desenvolver historia, personagens, cenas recorrentes e material visual."),
        message=message,
        characters=characters,
        episodes=episodes,
    )

    return {
        "title": title,
        "project_overview": _clean_text(
            message,
            "Projeto longo estruturado para desenvolver historia, personagens, cenas recorrentes e material visual.",
        ),
        "story_treatment": story_treatment,
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
        "_briefing_constraints": constraints,
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
    constraints = fallback_plan.get("_briefing_constraints") if isinstance(fallback_plan.get("_briefing_constraints"), dict) else {}
    normalized["title"] = _clean_text(raw.get("title"), fallback_plan["title"])
    normalized["project_overview"] = _clean_text(raw.get("project_overview") or raw.get("overview") or raw.get("logline"), fallback_plan["project_overview"])
    normalized["story_treatment"] = _clean_text(raw.get("story_treatment") or raw.get("story_summary") or raw.get("full_story"), fallback_plan.get("story_treatment", normalized["project_overview"]))
    normalized["build_requirements"] = [
        _clean_text(item) for item in (raw.get("build_requirements") if isinstance(raw.get("build_requirements"), list) else []) if _clean_text(item)
    ] or fallback_plan["build_requirements"]
    normalized["aspect_ratio"] = _normalize_aspect_ratio(raw.get("aspect_ratio"), fallback_plan["aspect_ratio"])
    normalized["language"] = _clean_text(raw.get("language"), fallback_plan["language"])
    normalized["target_duration_seconds"] = _safe_positive_seconds(raw.get("target_duration_seconds"), fallback_plan["target_duration_seconds"], minimum=30, maximum=14400)
    if constraints.get("has_explicit_aspect_ratio"):
        normalized["aspect_ratio"] = _normalize_aspect_ratio(constraints.get("aspect_ratio"), normalized["aspect_ratio"])
    if constraints.get("has_explicit_target_duration"):
        normalized["target_duration_seconds"] = _safe_positive_seconds(constraints.get("target_duration_seconds"), normalized["target_duration_seconds"], minimum=30, maximum=14400)

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
    if constraints.get("has_explicit_episode_count"):
        normalized_episode_count = _safe_positive_int(constraints.get("episode_count"), normalized_episode_count, minimum=1, maximum=24)
    elif kind != "film" and constraints.get("has_explicit_target_duration") and constraints.get("has_explicit_episode_duration"):
        inferred_from_constraints = max(1, int(round(normalized["target_duration_seconds"] / max(int(constraints.get("episode_duration_seconds") or 1), 1))))
        normalized_episode_count = _safe_positive_int(inferred_from_constraints, normalized_episode_count, minimum=1, maximum=24)
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
    if kind != "film":
        explicit_episode_duration = int(constraints.get("episode_duration_seconds") or 0)
        if explicit_episode_duration > 0:
            for episode in episodes:
                episode["duration_seconds"] = explicit_episode_duration
                episode["scenes"] = _rebalance_scene_durations(episode.get("scenes") or [], explicit_episode_duration)
        elif normalized_episode_count > 0 and constraints.get("has_explicit_target_duration"):
            distributed_duration = max(15, int(round(normalized["target_duration_seconds"] / max(normalized_episode_count, 1))))
            for episode in episodes:
                episode["duration_seconds"] = distributed_duration
                episode["scenes"] = _rebalance_scene_durations(episode.get("scenes") or [], distributed_duration)
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
        "title, project_overview, story_treatment, build_requirements, aspect_ratio, language, target_duration_seconds, episode_count, characters, scenes, objects, episodes, assistant_reply. "
        "Regras: "
        "1) project_overview em PT-BR, direto e concreto. "
        "2) story_treatment deve ser um texto longo, completo e corrido, contando a historia do começo ao fim e explicando o que acontece em cada parte principal. "
        "3) build_requirements deve listar o que precisa existir para construir a obra. "
        "4) characters deve trazer name, role, summary e persona_type. persona_type deve ser um destes valores: homem, mulher, crianca, familia, natureza, desenho, personalizado. "
        "5) scenes e objects devem ser bancos reutilizaveis para storyboard, cada item com name, summary e image_prompt. "
        "6) episodes deve ser uma lista de blocos prontos para a aba Roteiro. Cada episodio precisa de episode_number, title, synopsis, duration_seconds e scenes. "
        "7) Cada scene interna precisa de scene_number, title, beat, location, duration_seconds, characters, objects e image_prompt. "
        "8) A duracao total, o formato e a quantidade de episodios devem respeitar o briefing do usuario sempre que houver informacao explicita. "
        "9) Se kind for film, gere apenas um episodio principal. "
        "10) Use nomes e detalhes fortes, mas sem excesso de floreio. "
        "11) assistant_reply deve resumir o que foi montado e orientar o usuario para o proximo passo."
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
