from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_PROMPT_BLOCKLIST_MARKERS = (
    "ignore previous",
    "ignore all",
    "desconsidere",
    "output rules",
    "return only",
    "responda somente",
    "system prompt",
    "assistant:",
    "developer:",
    "user:",
)

_STYLIZED_MARKERS = (
    "anime",
    "cartoon",
    "desenho",
    "ilustracao",
    "illustration",
    "manga",
    "comic",
    "hq",
    "paint",
    "painting",
    "watercolor",
    "aquarela",
    "cgi",
    "3d render",
    "pixar",
    "cel shading",
    "cel-shaded",
    "vector art",
    "sketch",
)

_HUMAN_PERSONA_MARKERS = (
    "human",
    "humano",
    "persona",
    "personagem",
    "artist",
    "artista",
    "singer",
    "cantor",
    "cantora",
    "man",
    "woman",
    "homem",
    "mulher",
    "boy",
    "girl",
    "casal",
    "couple",
    "band",
    "banda",
)

_NATURE_PERSONA_MARKERS = (
    "nature",
    "natureza",
    "landscape",
    "paisagem",
    "forest",
    "floresta",
    "mountain",
    "montanha",
    "sea",
    "ocean",
    "mar",
    "river",
    "rio",
    "sky",
    "ceu",
    "weather",
    "clima",
)


@dataclass(frozen=True)
class CoverGuidanceDecision:
    visual_mode: str
    performance_mode: str
    sanitized_cover_context: str
    sanitized_cover_custom_prompt: str
    stylized_markers: tuple[str, ...]
    use_cover_anchor: bool
    anchor_is_official_cover: bool
    cover_source: str


def _normalize_keyword_text(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = raw.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _sanitize_cover_text(value: str, max_chars: int = 900) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    cleaned_lines: list[str] = []
    char_count = 0
    for line in raw.split("\n"):
        candidate = re.sub(r"\s+", " ", line).strip()
        if not candidate:
            continue

        lowered = _normalize_keyword_text(candidate)
        if any(marker in lowered for marker in _PROMPT_BLOCKLIST_MARKERS):
            continue

        if len(candidate) > 240:
            shortened = candidate[:240].rsplit(" ", 1)[0].strip()
            candidate = shortened or candidate[:240]

        projected = char_count + len(candidate) + (1 if cleaned_lines else 0)
        if projected > max_chars:
            remaining = max_chars - char_count - (1 if cleaned_lines else 0)
            if remaining <= 0:
                break
            candidate = candidate[:remaining].rsplit(" ", 1)[0].strip() or candidate[:remaining]
            cleaned_lines.append(candidate)
            break

        cleaned_lines.append(candidate)
        char_count = projected

    return "\n".join(cleaned_lines).strip()


def _collect_stylized_markers(*values: str) -> tuple[str, ...]:
    haystack = "\n".join(_normalize_keyword_text(value) for value in values if str(value or "").strip())
    if not haystack:
        return ()

    found: list[str] = []
    for marker in _STYLIZED_MARKERS:
        if marker in haystack and marker not in found:
            found.append(marker)
    return tuple(found)


def _resolve_cover_persona_mode(cover_persona: str, has_saved_persona: bool) -> str:
    normalized = _normalize_keyword_text(cover_persona)
    if any(marker in normalized for marker in _NATURE_PERSONA_MARKERS):
        return "nature"
    if has_saved_persona or any(marker in normalized for marker in _HUMAN_PERSONA_MARKERS):
        return "human"
    return "neutral"


def decide_cover_guidance(
    *,
    requested_visual_mode: str = "",
    prompt: str = "",
    style: str = "",
    cover_context: str = "",
    cover_custom_prompt: str = "",
    cover_persona: str = "",
    cover_source: str = "",
    tevoxi_has_official_cover_reference: bool = False,
    has_saved_persona: bool = False,
    has_reference_image: bool = False,
    image_is_cover_anchor: bool = False,
    music_driven: bool = False,
) -> CoverGuidanceDecision:
    normalized_requested_mode = _normalize_keyword_text(requested_visual_mode)
    normalized_cover_source = _normalize_keyword_text(cover_source)
    stylized_markers = _collect_stylized_markers(prompt, style, cover_custom_prompt)

    if normalized_requested_mode == "stylized":
        visual_mode = "stylized"
    elif normalized_requested_mode == "photorealistic":
        visual_mode = "photorealistic"
    elif stylized_markers:
        visual_mode = "stylized"
    else:
        visual_mode = "photorealistic"

    cover_persona_mode = _resolve_cover_persona_mode(cover_persona, has_saved_persona=has_saved_persona)
    if cover_persona_mode == "human" and music_driven:
        performance_mode = "human_music_performance"
    elif cover_persona_mode == "nature":
        performance_mode = "nature_scene"
    else:
        performance_mode = "neutral"

    use_cover_anchor = bool(has_reference_image or image_is_cover_anchor or normalized_cover_source or tevoxi_has_official_cover_reference)
    anchor_is_official_cover = bool(
        tevoxi_has_official_cover_reference
        or normalized_cover_source in {"tevoxi", "official_cover", "official-cover", "cover"}
    )

    return CoverGuidanceDecision(
        visual_mode=visual_mode,
        performance_mode=performance_mode,
        sanitized_cover_context=_sanitize_cover_text(cover_context),
        sanitized_cover_custom_prompt=_sanitize_cover_text(cover_custom_prompt),
        stylized_markers=stylized_markers,
        use_cover_anchor=use_cover_anchor,
        anchor_is_official_cover=anchor_is_official_cover,
        cover_source=normalized_cover_source,
    )


def build_cover_optimizer_tone(base_style: str = "", visual_mode: str = "photorealistic") -> str:
    base = str(base_style or "").strip()
    normalized_mode = _normalize_keyword_text(visual_mode)

    if normalized_mode == "stylized":
        return base or "stylized"

    photorealistic_hint = "photorealistic live-action, highly realistic skin, hair, lighting, anatomy"
    if not base:
        return photorealistic_hint

    normalized_base = _normalize_keyword_text(base)
    if "photorealistic" in normalized_base or "live-action" in normalized_base:
        return base
    return f"{photorealistic_hint}, {base}"


def apply_cover_guidance(prompt: str, decision: CoverGuidanceDecision) -> str:
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    lowered = base_prompt.lower()
    blocks: list[str] = []

    if "visual mode lock" not in lowered:
        if decision.visual_mode == "stylized":
            blocks.append(
                "VISUAL MODE LOCK (MANDATORY): stylized visuals are allowed only because the request explicitly asked for them. "
                "Keep the same cover identity, composition cues and emotional atmosphere while honoring the requested stylized language."
            )
        else:
            blocks.append(
                "VISUAL MODE LOCK (MANDATORY): default to highly realistic live-action photorealism. "
                "Preserve realistic skin texture, hair strands, physically plausible lighting, anatomy, fabric response and lens behavior. "
                "Avoid anime, cartoon, illustration, painting and stylized CGI unless explicitly requested."
            )

    if decision.use_cover_anchor and "cover fidelity lock" not in lowered:
        anchor_rule = (
            "COVER FIDELITY LOCK (MANDATORY): when a cover image is provided, treat it as the primary visual anchor. "
            "Preserve the same protagonist identity or the same landscape symbolism, key shapes, palette intent and cover atmosphere."
        )
        if decision.anchor_is_official_cover:
            anchor_rule = (
                "COVER FIDELITY LOCK (MANDATORY): the official Tevoxi cover reference is the primary visual anchor. "
                "Keep the same main character or central artwork identity from the cover, preserving key facial traits, hair, silhouette, palette intent, wardrobe mood and symbolic atmosphere."
            )
        blocks.append(anchor_rule)

    if decision.sanitized_cover_context and "cover context lock" not in lowered:
        blocks.append(
            "COVER CONTEXT LOCK (MANDATORY): preserve these canon cover details whenever they do not conflict with the current scene intent:\n"
            f"{decision.sanitized_cover_context}"
        )

    if decision.sanitized_cover_custom_prompt and "cover custom prompt lock" not in lowered:
        blocks.append(
            "COVER CUSTOM PROMPT LOCK (MANDATORY): merge these saved cover directions into the final scene without changing the main identity from the cover:\n"
            f"{decision.sanitized_cover_custom_prompt}"
        )

    if decision.performance_mode == "human_music_performance" and "performance lock" not in lowered:
        blocks.append(
            "PERFORMANCE LOCK (MANDATORY): if the cover subject is human or persona-based, keep exactly the same protagonist identity from the cover. "
            "Prefer performance-focused live-action shots with singing, emoting or interpreting the song naturally, with believable lip movement, gestures and stage presence coherent with the music."
        )
    elif decision.performance_mode == "nature_scene" and "nature cover lock" not in lowered:
        blocks.append(
            "NATURE COVER LOCK (MANDATORY): if the cover focus is landscape or nature, preserve the atmosphere, landscape scale, weather, symbolism and emotional mood. "
            "Do not insert a human singer by default unless the request explicitly asks for one."
        )

    if not blocks:
        return base_prompt
    return f"{base_prompt}\n\n" + "\n\n".join(blocks)