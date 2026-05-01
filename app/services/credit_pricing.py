"""Centralized credit pricing rules used by estimation and deduction paths.

This module is the single source of truth for credit calculations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CREDIT_PRICING_RULES_VERSION = "v2.1"

# Business rules
USD_TO_BRL = 4.9756
MARGIN_MULTIPLIER = 1.30

# Public package catalog used by backend + frontend.
# Entry package starts at R$ 19.99 as requested.
CREDIT_PACKAGES = [
    {"credits": 500, "price": 19.99, "label": "500 creditos"},
    {"credits": 1300, "price": 49.99, "label": "1300 creditos"},
    {"credits": 2800, "price": 99.99, "label": "2800 creditos"},
]

# Realistic engine baseline (8 seconds)
REALISTIC_BASE_CREDITS_8S = {
    "wan2": 100,
    "grok": 90,
    "seedance": 170,
    "minimax": 110,
}

# Approximate provider costs in USD per second
REALISTIC_ENGINE_USD_PER_SEC = {
    "wan2": 0.060,
    "grok": 0.050,
    "seedance": 0.085,
    "minimax": 0.056,
}

# Additional operation costs
ANCHOR_IMAGE_USD = 0.039
TEVOXI_MUSIC_USD = 0.07034
TTS_USD_PER_SEC = 0.00048
STT_USD_PER_SEC = 0.00010
CUSTOM_VIDEO_PROCESS_USD_PER_MIN = 0.010
KARAOKE_REMOVE_VOCALS_USD = 0.030


@dataclass
class CreditEstimate:
    credits_needed: int
    credits_exact: float
    provider_cost_usd: float
    provider_cost_brl: float
    billed_cost_brl: float
    brl_per_credit: float
    breakdown: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules_version": CREDIT_PRICING_RULES_VERSION,
            "credits_needed": int(self.credits_needed),
            "credits_exact": round(float(self.credits_exact), 2),
            "provider_cost_usd": round(float(self.provider_cost_usd), 6),
            "provider_cost_brl": round(float(self.provider_cost_brl), 4),
            "billed_cost_brl": round(float(self.billed_cost_brl), 4),
            "brl_per_credit": round(float(self.brl_per_credit), 6),
            "margin_multiplier": MARGIN_MULTIPLIER,
            "breakdown": self.breakdown,
        }


def get_credit_packages() -> list[dict[str, Any]]:
    return [dict(pkg) for pkg in CREDIT_PACKAGES]


def get_credit_value_brl(packages: list[dict[str, Any]] | None = None) -> float:
    catalog = packages or CREDIT_PACKAGES
    ratios: list[float] = []
    for pkg in catalog:
        try:
            credits = int(pkg.get("credits", 0) or 0)
            price = float(pkg.get("price", 0.0) or 0.0)
        except Exception:
            continue
        if credits > 0 and price > 0:
            ratios.append(price / credits)
    return min(ratios) if ratios else (99.99 / 2800.0)


def _safe_duration_seconds(duration_seconds: float | int | None, word_count: int | None = None) -> float:
    duration = float(duration_seconds or 0)
    if duration > 0:
        return duration

    words = int(word_count or 0)
    if words > 0:
        # 150 words per minute ~= 2.5 words per second
        return max(8.0, words / 2.5)

    return 60.0


def _ai_image_scene_count(duration_seconds: float) -> int:
    # Keep estimate aligned with existing pipeline behavior where scenes are grouped.
    if duration_seconds <= 12:
        return 1
    return max(2, math.ceil(duration_seconds / 12.0))


def _credits_from_provider_cost(provider_cost_usd: float, floor_credits: int = 1) -> CreditEstimate:
    provider_cost_brl = max(0.0, float(provider_cost_usd)) * USD_TO_BRL
    billed_cost_brl = provider_cost_brl * MARGIN_MULTIPLIER
    brl_per_credit = get_credit_value_brl(CREDIT_PACKAGES)

    credits_exact = billed_cost_brl / brl_per_credit if brl_per_credit > 0 else 0.0
    credits_needed = max(int(floor_credits), int(math.ceil(credits_exact)), 1)

    return CreditEstimate(
        credits_needed=credits_needed,
        credits_exact=credits_exact,
        provider_cost_usd=provider_cost_usd,
        provider_cost_brl=provider_cost_brl,
        billed_cost_brl=billed_cost_brl,
        brl_per_credit=brl_per_credit,
        breakdown={},
    )


def estimate_realistic_credits(
    engine: str,
    duration_seconds: float | int,
    has_reference_image: bool = True,
    add_music: bool = False,
    add_narration: bool = False,
    enable_subtitles: bool = False,
    use_external_audio: bool = False,
) -> dict[str, Any]:
    normalized_engine = str(engine or "wan2").strip().lower()
    if normalized_engine not in REALISTIC_ENGINE_USD_PER_SEC:
        normalized_engine = "wan2"

    duration = max(1.0, _safe_duration_seconds(duration_seconds))
    video_usd = REALISTIC_ENGINE_USD_PER_SEC[normalized_engine] * duration
    anchor_usd = ANCHOR_IMAGE_USD if has_reference_image else 0.0
    music_usd = TEVOXI_MUSIC_USD if (add_music and not use_external_audio) else 0.0
    narration_usd = (duration * TTS_USD_PER_SEC) if add_narration else 0.0
    subtitles_usd = (duration * STT_USD_PER_SEC) if enable_subtitles else 0.0

    provider_cost_usd = video_usd + anchor_usd + music_usd + narration_usd + subtitles_usd

    base_8s = REALISTIC_BASE_CREDITS_8S.get(normalized_engine, REALISTIC_BASE_CREDITS_8S["wan2"])
    linear_floor = int(math.ceil((base_8s * duration) / 8.0))

    estimate = _credits_from_provider_cost(provider_cost_usd, floor_credits=linear_floor)
    estimate.breakdown = {
        "mode": "realistic",
        "engine": normalized_engine,
        "duration_seconds": round(duration, 2),
        "linear_floor_credits": linear_floor,
        "components_usd": {
            "video_generation": round(video_usd, 6),
            "anchor_reference": round(anchor_usd, 6),
            "music": round(music_usd, 6),
            "narration": round(narration_usd, 6),
            "subtitles": round(subtitles_usd, 6),
        },
    }
    return estimate.to_dict()


def estimate_standard_credits(
    duration_seconds: float | int | None = None,
    word_count: int | None = None,
    has_ai_images: bool = True,
    has_custom_images: bool = False,
    has_custom_video: bool = False,
    use_custom_audio: bool = False,
    use_tevoxi_audio: bool = False,
    enable_subtitles: bool = True,
    add_narration: bool = True,
    add_music: bool = True,
    audio_is_music: bool = False,
    remove_vocals: bool = False,
) -> dict[str, Any]:
    duration = max(1.0, _safe_duration_seconds(duration_seconds, word_count=word_count))

    ai_images_count = _ai_image_scene_count(duration) if has_ai_images else 0
    ai_images_usd = ai_images_count * ANCHOR_IMAGE_USD

    narration_usd = duration * TTS_USD_PER_SEC if add_narration else 0.0
    subtitles_usd = duration * STT_USD_PER_SEC if enable_subtitles else 0.0

    music_usd = 0.0
    if add_music and not use_custom_audio and not use_tevoxi_audio:
        music_usd = TEVOXI_MUSIC_USD

    custom_video_minutes = duration / 60.0 if has_custom_video else 0.0
    custom_video_usd = custom_video_minutes * CUSTOM_VIDEO_PROCESS_USD_PER_MIN

    karaoke_usd = KARAOKE_REMOVE_VOCALS_USD if (audio_is_music and remove_vocals) else 0.0

    provider_cost_usd = ai_images_usd + narration_usd + subtitles_usd + music_usd + custom_video_usd + karaoke_usd

    if has_custom_video:
        floor_credits = 30
    elif has_ai_images:
        floor_credits = 40
    elif has_custom_images:
        floor_credits = 24
    elif use_custom_audio or use_tevoxi_audio:
        floor_credits = 22
    else:
        floor_credits = 12

    estimate = _credits_from_provider_cost(provider_cost_usd, floor_credits=floor_credits)
    estimate.breakdown = {
        "mode": "standard",
        "duration_seconds": round(duration, 2),
        "floor_credits": floor_credits,
        "components_usd": {
            "ai_images": round(ai_images_usd, 6),
            "narration": round(narration_usd, 6),
            "subtitles": round(subtitles_usd, 6),
            "music": round(music_usd, 6),
            "custom_video_processing": round(custom_video_usd, 6),
            "karaoke_remove_vocals": round(karaoke_usd, 6),
        },
        "details": {
            "ai_images_count": ai_images_count,
            "has_custom_images": bool(has_custom_images),
            "has_custom_video": bool(has_custom_video),
            "use_custom_audio": bool(use_custom_audio),
            "use_tevoxi_audio": bool(use_tevoxi_audio),
            "add_music": bool(add_music),
            "add_narration": bool(add_narration),
            "enable_subtitles": bool(enable_subtitles),
        },
    }
    return estimate.to_dict()


def estimate_quick_create_credits(duration_seconds: float | int) -> dict[str, Any]:
    # Quick-create uses existing audio and IA-generated scenes with subtitles.
    return estimate_standard_credits(
        duration_seconds=duration_seconds,
        has_ai_images=True,
        has_custom_images=False,
        has_custom_video=False,
        use_custom_audio=True,
        use_tevoxi_audio=False,
        enable_subtitles=True,
        add_narration=False,
        add_music=False,
    )


def estimate_auto_theme_credits(
    video_type: str,
    default_settings: dict[str, Any] | None,
    custom_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_type = str(video_type or "narration").strip().lower()
    ds = dict(default_settings or {})
    cs = dict(custom_settings or {})

    if normalized_type == "realistic":
        use_tevoxi = bool(cs.get("tevoxi_audio_url") or ds.get("use_tevoxi"))
        duration = float(
            cs.get("clip_duration")
            or ds.get("clip_duration")
            or ds.get("duration")
            or ds.get("duration_seconds")
            or 8
        )
        engine = str(ds.get("engine") or "grok").strip().lower()
        disable_reference = bool(cs.get("disable_persona_reference") or ds.get("disable_persona_reference"))

        return estimate_realistic_credits(
            engine=engine,
            duration_seconds=duration,
            has_reference_image=not disable_reference,
            add_music=bool(ds.get("add_music")) and not use_tevoxi,
            add_narration=bool(ds.get("add_narration")),
            enable_subtitles=bool(ds.get("enable_subtitles")),
            use_external_audio=use_tevoxi,
        )

    if normalized_type == "musical_shorts":
        duration = float(cs.get("clip_duration") or ds.get("clip_duration") or 10)
        disable_reference = bool(cs.get("disable_persona_reference") or ds.get("disable_persona_reference"))
        return estimate_realistic_credits(
            engine="grok",
            duration_seconds=duration,
            has_reference_image=not disable_reference,
            add_music=False,
            add_narration=False,
            enable_subtitles=False,
            use_external_audio=True,
        )

    if normalized_type == "music":
        duration = float(ds.get("music_duration") or ds.get("duration_seconds") or 120)
        return estimate_standard_credits(
            duration_seconds=duration,
            has_ai_images=True,
            has_custom_images=False,
            has_custom_video=False,
            use_custom_audio=False,
            use_tevoxi_audio=False,
            enable_subtitles=True,
            add_narration=False,
            add_music=True,
            audio_is_music=True,
            remove_vocals=False,
        )

    # narration / default image AI schedule
    duration = float(ds.get("duration_seconds") or ds.get("duration") or 60)
    return estimate_standard_credits(
        duration_seconds=duration,
        has_ai_images=True,
        has_custom_images=False,
        has_custom_video=False,
        use_custom_audio=False,
        use_tevoxi_audio=False,
        enable_subtitles=True,
        add_narration=True,
        add_music=True,
    )
