"""Centralized credit pricing rules used by estimation and deduction paths.

This module is the single source of truth for credit calculations.
"""
from __future__ import annotations

from copy import deepcopy
import math
from dataclasses import dataclass
from typing import Any

CREDIT_PRICING_RULES_VERSION = "v3.2"

# Business rules
USD_TO_BRL = 4.9756
USD_PER_CREDIT = 0.01
USD_CENTS_PER_USD = 100
MARGIN_MULTIPLIER = 1.5
INITIAL_FREE_CREDITS = 100
ANNUAL_PLAN_MONTHS = 12
MONTHLY_PLAN_DAYS = 30
ANNUAL_PLAN_DAYS = 365
ANNUAL_PLAN_DISCOUNT_PERCENT = 20


def _usd_to_brl_amount(value: float | int) -> float:
    return round(max(0.0, float(value or 0.0)) * USD_TO_BRL, 2)


def _credits_to_usd_amount(credits: int) -> float:
    return round(max(0, int(credits or 0)) * USD_PER_CREDIT, 2)


def _billed_usd_per_unit_from_credits(credits: int) -> float:
    return round(max(0, int(credits or 0)) * USD_PER_CREDIT, 4)


def _normalize_billing_period(billing_period: str) -> str:
    normalized = str(billing_period or "monthly").strip().lower() or "monthly"
    return "annual" if normalized == "annual" else "monthly"


def _annual_plan_price_usd(monthly_price_usd: float | int) -> float:
    normalized_price = max(0.0, float(monthly_price_usd or 0.0))
    discounted_total = normalized_price * ANNUAL_PLAN_MONTHS * (1 - (ANNUAL_PLAN_DISCOUNT_PERCENT / 100.0))
    return round(discounted_total, 2)


def _annotate_subscription_plan(plan: dict[str, Any]) -> None:
    monthly_price_usd = round(max(0.0, float(plan.get("priceUsd") or 0.0)), 2)
    monthly_credits = max(0, int(plan.get("monthlyCredits") or 0))
    is_free_plan = str(plan.get("code") or "free") == "free"
    annual_credits = 0 if is_free_plan else monthly_credits * ANNUAL_PLAN_MONTHS
    annual_price_usd = 0.0 if is_free_plan else _annual_plan_price_usd(monthly_price_usd)
    monthly_brl = _usd_to_brl_amount(monthly_price_usd)
    annual_brl = _usd_to_brl_amount(annual_price_usd)
    monthly_label = plan.get("billingLabel") or f"Ciclo mensal de {MONTHLY_PLAN_DAYS} dias"
    annual_label = monthly_label if is_free_plan else f"Ciclo anual de {ANNUAL_PLAN_DAYS} dias"

    plan["monthlyPriceUsd"] = monthly_price_usd
    plan["monthlyPrice"] = monthly_brl
    plan["annualPriceUsd"] = annual_price_usd
    plan["annualPrice"] = annual_brl
    plan["annualCredits"] = annual_credits
    plan["annualDiscountPercent"] = ANNUAL_PLAN_DISCOUNT_PERCENT
    plan["billing"] = {
        "monthly": {
            "period": "monthly",
            "label": "Mensal",
            "days": MONTHLY_PLAN_DAYS,
            "credits": monthly_credits,
            "priceUsd": monthly_price_usd,
            "price": monthly_brl,
            "billingLabel": monthly_label,
        },
        "annual": {
            "period": "annual",
            "label": "Anual",
            "days": ANNUAL_PLAN_DAYS,
            "credits": annual_credits,
            "priceUsd": annual_price_usd,
            "price": annual_brl,
            "billingLabel": annual_label,
        },
    }

# Public package catalog used by backend + frontend.
CREDIT_PACKAGES = [
    {
        "code": "topup-500",
        "credits": 500,
        "priceUsd": _credits_to_usd_amount(500),
        "price": _usd_to_brl_amount(_credits_to_usd_amount(500)),
        "label": "Recarga 500",
        "description": "Complemento rápido para continuar gerando.",
    },
    {
        "code": "topup-1500",
        "credits": 1500,
        "priceUsd": _credits_to_usd_amount(1500),
        "price": _usd_to_brl_amount(_credits_to_usd_amount(1500)),
        "label": "Recarga 1.500",
        "badge": "Mais usada",
        "description": "Ideal para reforçar o ciclo atual sem trocar de plano.",
    },
    {
        "code": "topup-5000",
        "credits": 5000,
        "priceUsd": _credits_to_usd_amount(5000),
        "price": _usd_to_brl_amount(_credits_to_usd_amount(5000)),
        "label": "Recarga 5.000",
        "badge": "Maior saldo",
        "description": "Volume extra para lotes grandes de imagens e vídeos.",
    },
]

CREDIT_SUBSCRIPTION_PLANS = [
    {
        "code": "free",
        "name": "Gratuito",
        "shortName": "Gratuito",
        "monthlyCredits": 0,
        "comparisonCredits": INITIAL_FREE_CREDITS,
        "priceUsd": 0.0,
        "price": 0.0,
        "billingLabel": "100 créditos iniciais",
        "accent": "free",
        "ctaLabel": "Pacote atual",
        "description": "Teste o fluxo completo e recarregue quando precisar.",
        "benefits": [
            "100 créditos para começar",
            "Recarga extra a qualquer momento",
            "Uso em modelos de imagem e vídeo",
            "Comparativo de custo por modelo",
            "Editor, automações e similar incluídos",
        ],
    },
    {
        "code": "starter",
        "name": "Iniciante",
        "shortName": "Iniciante",
        "monthlyCredits": 1600,
        "comparisonCredits": 1600,
        "priceUsd": 16.0,
        "price": _usd_to_brl_amount(16.0),
        "billingLabel": "Ciclo mensal de 30 dias",
        "accent": "starter",
        "description": "Para validar campanhas, criativos e vídeos curtos toda semana.",
        "benefits": [
            "1600 créditos por ciclo",
            "Use em imagens, vídeos e workflow",
            "Recarga extra sem trocar de plano",
            "Custos por segundo e por imagem visíveis",
            "Ideal para operação leve e recorrente",
        ],
    },
    {
        "code": "basic",
        "name": "Básico",
        "shortName": "Básico",
        "monthlyCredits": 2900,
        "comparisonCredits": 2900,
        "priceUsd": 29.0,
        "price": _usd_to_brl_amount(29.0),
        "billingLabel": "Ciclo mensal de 30 dias",
        "accent": "basic",
        "description": "Mais folga para rodar imagens, editor e vídeos realistas no mês.",
        "benefits": [
            "2900 créditos por ciclo",
            "Melhor margem para workflow e similar",
            "Recarga extra sem perder o plano",
            "Tabela comparativa com todos os modelos",
            "Pensado para produção semanal consistente",
        ],
    },
    {
        "code": "professional",
        "name": "Profissional",
        "shortName": "Profissional",
        "monthlyCredits": 6900,
        "comparisonCredits": 6900,
        "priceUsd": 69.0,
        "price": _usd_to_brl_amount(69.0),
        "billingLabel": "Ciclo mensal de 30 dias",
        "accent": "professional",
        "badge": "Mais popular",
        "recommended": True,
        "description": "Volume ideal para operação contínua com vídeo, imagem e automação.",
        "benefits": [
            "6900 créditos por ciclo",
            "Cobertura forte para imagem e vídeo",
            "Recargas extras quando o saldo apertar",
            "Leitura rápida de custo por modelo",
            "Feito para rotina pesada de produção",
        ],
    },
    {
        "code": "supreme",
        "name": "Supremo",
        "shortName": "Supremo",
        "monthlyCredits": 20500,
        "comparisonCredits": 20500,
        "priceUsd": 199.0,
        "price": _usd_to_brl_amount(199.0),
        "billingLabel": "Ciclo mensal de 30 dias",
        "accent": "supreme",
        "badge": "Melhor valor",
        "description": "Para volume alto de geração e lotes intensos no mesmo mês.",
        "benefits": [
            "20500 créditos por ciclo",
            "Maior cobertura mensal do catálogo",
            "Recargas extras para picos de demanda",
            "Comparativo completo por modelo",
            "Indicado para operação de alto volume",
        ],
    },
]

for _plan in CREDIT_SUBSCRIPTION_PLANS:
    _annotate_subscription_plan(_plan)

PAID_PLAN_CODES = {plan["code"] for plan in CREDIT_SUBSCRIPTION_PLANS if plan["code"] != "free"}

# Realistic engine baseline (8 seconds)
REALISTIC_BASE_CREDITS_8S = {
    "wan2": 100,
    "grok": 90,
    "lite2": 24,
    "mega15": 112,
    "seedance": 170,
    "viduq3": 50,
    "avatar31": 85,
}

# Approximate provider costs in USD per second
REALISTIC_ENGINE_USD_PER_SEC = {
    "wan2": 0.060,
    "grok": 0.050,
    "lite2": 0.018,
    "mega15": 0.076,
    "seedance": 0.085,
    "viduq3": 0.042,
    "avatar31": 0.048,
}
REALISTIC_ENGINE_MIN_CREDITS_PER_SEC = {
    "seedance": 15,
}

# Additional operation costs
ANCHOR_IMAGE_USD = 0.039
TEVOXI_MUSIC_USD = 0.07034
TTS_USD_PER_SEC = 0.00048
STT_USD_PER_SEC = 0.00010
CUSTOM_VIDEO_PROCESS_USD_PER_MIN = 0.010
KARAOKE_REMOVE_VOCALS_USD = 0.030
SIMILAR_ANALYSIS_FRAME_USD = 0.0042
SIMILAR_ANALYSIS_SUMMARY_USD = 0.010
SIMILAR_ANALYSIS_SCENE_PROMPT_USD = 0.007
SIMILAR_ANALYSIS_GENERAL_PROMPT_USD = 0.014
SIMILAR_ANALYSIS_SCENE_SECONDS = 2.0
SIMILAR_ANALYSIS_CONTEXT_FRAMES = 6
IMAGE_GENERATION_MODEL_USD = {
    "google/nano-banana-pro/text-to-image": 0.020,
    "google/nano-banana-2/text-to-image": 0.017,
    "google/nano-banana/text-to-image": 0.014,
    "openai/gpt-image-1/text-to-image": 0.032,
    "baidu/ERNIE-Image-Turbo/text-to-image": 0.0,
    "z-image/turbo": 0.010,
    "bytedance/seedream-v5.0-lite/sequential": 0.032,
    "bytedance/seedream-v5.0-lite/edit-sequential": 0.032,
    "bytedance/seedream-v4.5": 0.036,
    "bytedance/seedream-v4.5/edit": 0.036,
    "alibaba/wan-2.6/text-to-image": 0.040,
    "alibaba/wan-2.6/image-edit": 0.044,
}
IMAGE_GENERATION_SIZE_MULTIPLIERS = {
    "1K": 1.0,
    "2K": 1.35,
    "4K": 1.85,
}
IMAGE_GENERATION_REFERENCE_USD = 0.0032
IMAGE_GENERATION_THINKING_MULTIPLIER = 1.12
IMAGE_GENERATION_BASE_FLOOR = {
    "google/nano-banana-pro/text-to-image": 8,
    "google/nano-banana-2/text-to-image": 7,
    "google/nano-banana/text-to-image": 6,
    "openai/gpt-image-1/text-to-image": 11,
    "baidu/ERNIE-Image-Turbo/text-to-image": 0,
    "z-image/turbo": 2,
    "bytedance/seedream-v5.0-lite/sequential": 6,
    "bytedance/seedream-v5.0-lite/edit-sequential": 6,
    "bytedance/seedream-v4.5": 7,
    "bytedance/seedream-v4.5/edit": 7,
    "alibaba/wan-2.6/text-to-image": 13,
    "alibaba/wan-2.6/image-edit": 15,
}

IMAGE_COMPARISON_MODELS = [
    {
        "key": "baidu-ernie-turbo",
        "label": "Baidu ERNIE Turbo",
        "kind": "image",
        "model": "baidu/ERNIE-Image-Turbo/text-to-image",
        "size": "2K",
        "featured": True,
    },
    {
        "key": "ultra-high-3-0",
        "label": "Ultra High 3.0",
        "kind": "image",
        "model": "z-image/turbo",
        "size": "2K",
        "featured": True,
    },
    {
        "key": "nano-banana",
        "label": "Nano Banana",
        "kind": "image",
        "model": "google/nano-banana/text-to-image",
        "size": "2K",
        "featured": True,
    },
    {
        "key": "nano-banana-pro",
        "label": "Nano Banana Pro",
        "kind": "image",
        "model": "google/nano-banana-pro/text-to-image",
        "size": "2K",
        "featured": True,
    },
    {
        "key": "mega-anime",
        "label": "Mega 5.0 Anime",
        "kind": "image",
        "model": "bytedance/seedream-v5.0-lite/sequential",
        "size": "2K",
        "featured": True,
    },
    {
        "key": "mega-real",
        "label": "Mega 5.0 Real",
        "kind": "image",
        "model": "bytedance/seedream-v4.5",
        "size": "2K",
        "featured": True,
    },
    {
        "key": "gpt-image",
        "label": "GPT Image",
        "kind": "image",
        "model": "openai/gpt-image-1/text-to-image",
        "size": "2K",
        "featured": True,
    },
]

VIDEO_COMPARISON_MODELS = [
    {
        "key": "grok-video",
        "label": "Cria 3.0 Speed",
        "kind": "video",
        "engine": "grok",
        "featured": True,
    },
    {
        "key": "wan-video",
        "label": "Ultra High 1.0",
        "kind": "video",
        "engine": "wan2",
        "featured": True,
    },
    {
        "key": "seedance-video",
        "label": "Mega 2.0 Ultra",
        "kind": "video",
        "engine": "seedance",
        "featured": True,
    },
    {
        "key": "lite2-video",
        "label": "Lite 2.0 Fast",
        "kind": "video",
        "engine": "lite2",
        "featured": True,
    },
    {
        "key": "mega15-video",
        "label": "Mega 1.5 Real",
        "kind": "video",
        "engine": "mega15",
        "featured": True,
    },
    {
        "key": "viduq3-video",
        "label": "Pro 3.1 Start",
        "kind": "video",
        "engine": "viduq3",
        "featured": True,
    },
    {
        "key": "avatar-video",
        "label": "Avatar 3.1 Plus",
        "kind": "video",
        "engine": "avatar31",
        "featured": True,
    },
]


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
    return deepcopy(CREDIT_PACKAGES)


def get_subscription_plans() -> list[dict[str, Any]]:
    return deepcopy(CREDIT_SUBSCRIPTION_PLANS)


def get_paid_plan_codes() -> set[str]:
    return set(PAID_PLAN_CODES)


def get_subscription_plan(plan_code: str) -> dict[str, Any]:
    normalized = str(plan_code or "free").strip().lower()
    if normalized == "pro":
        normalized = "professional"
    for plan in CREDIT_SUBSCRIPTION_PLANS:
        if plan["code"] == normalized:
            return deepcopy(plan)
    return deepcopy(CREDIT_SUBSCRIPTION_PLANS[0])


def get_subscription_plan_billing(plan_code: str, billing_period: str = "monthly") -> dict[str, Any]:
    plan = get_subscription_plan(plan_code)
    normalized_period = _normalize_billing_period(billing_period)
    billing = deepcopy(plan.get("billing", {}).get(normalized_period) or plan.get("billing", {}).get("monthly") or {})
    billing["period"] = normalized_period
    billing["planCode"] = plan.get("code", "free")
    billing["planName"] = plan.get("name", "Gratuito")
    billing["accent"] = plan.get("accent", "free")
    return billing


def _plan_credit_budget(plan: dict[str, Any]) -> int:
    return int(plan.get("comparisonCredits") or plan.get("monthlyCredits") or 0)


def _credits_from_usd_value(provider_cost_usd: float | int, minimum: int = 1) -> int:
    normalized_cost = max(0.0, float(provider_cost_usd or 0.0))
    if normalized_cost <= 0:
        return max(0, int(minimum or 0))
    exact = normalized_cost * USD_CENTS_PER_USD * MARGIN_MULTIPLIER
    return max(int(minimum or 0), int(math.ceil(exact - 1e-9)))


def _realistic_engine_credits_per_second(engine: str) -> int:
    normalized_engine = str(engine or "grok").strip().lower()
    usd_per_second = REALISTIC_ENGINE_USD_PER_SEC.get(normalized_engine, REALISTIC_ENGINE_USD_PER_SEC["grok"])
    minimum = REALISTIC_ENGINE_MIN_CREDITS_PER_SEC.get(normalized_engine, 1)
    return _credits_from_usd_value(usd_per_second, minimum=minimum)


def _estimate_image_unit_cost_usd(model: str, size: str) -> float:
    normalized_model = str(model or "google/nano-banana-pro/text-to-image").strip()
    if normalized_model not in IMAGE_GENERATION_MODEL_USD:
        normalized_model = "google/nano-banana-pro/text-to-image"

    normalized_size = str(size or "2K").strip().upper()
    if normalized_size not in IMAGE_GENERATION_SIZE_MULTIPLIERS:
        normalized_size = "2K"

    return IMAGE_GENERATION_MODEL_USD[normalized_model] * IMAGE_GENERATION_SIZE_MULTIPLIERS[normalized_size]


def get_credit_comparison_sections() -> list[dict[str, Any]]:
    plans = get_subscription_plans()
    sections: list[dict[str, Any]] = []

    image_rows: list[dict[str, Any]] = []
    for item in IMAGE_COMPARISON_MODELS:
        usd_per_unit = _estimate_image_unit_cost_usd(item["model"], item["size"])
        credits_per_unit = _credits_from_usd_value(usd_per_unit, minimum=0 if usd_per_unit <= 0 else 1)
        billed_usd_per_unit = _billed_usd_per_unit_from_credits(credits_per_unit)
        is_unlimited = credits_per_unit <= 0
        usage = {}
        for plan in plans:
            plan_budget = _plan_credit_budget(plan)
            included_units = None if is_unlimited else int(math.floor(plan_budget / credits_per_unit))
            usage[plan["code"]] = {
                "includedUnits": included_units,
                "budgetCredits": plan_budget,
                "unlimited": is_unlimited,
            }
        image_rows.append({
            "key": item["key"],
            "label": item["label"],
            "kind": item["kind"],
            "unit": "image",
            "creditsPerUnit": credits_per_unit,
            "usdPerUnit": billed_usd_per_unit,
            "brlPerUnit": _usd_to_brl_amount(billed_usd_per_unit),
            "featured": bool(item.get("featured")),
            "plans": usage,
        })

    sections.append({
        "key": "image-models",
        "title": "Modelos de imagem",
        "rows": image_rows,
        "defaultVisibleRows": len(image_rows),
    })

    video_rows: list[dict[str, Any]] = []
    for item in VIDEO_COMPARISON_MODELS:
        engine = str(item["engine"] or "grok").strip().lower()
        credits_per_unit = _realistic_engine_credits_per_second(engine)
        billed_usd_per_unit = _billed_usd_per_unit_from_credits(credits_per_unit)
        display_usd_per_unit = REALISTIC_ENGINE_USD_PER_SEC.get(engine, billed_usd_per_unit) if engine == "viduq3" else billed_usd_per_unit
        usage = {}
        for plan in plans:
            plan_budget = _plan_credit_budget(plan)
            included_units = int(math.floor(plan_budget / credits_per_unit)) if credits_per_unit > 0 else 0
            usage[plan["code"]] = {
                "includedUnits": included_units,
                "budgetCredits": plan_budget,
            }
        video_rows.append({
            "key": item["key"],
            "label": item["label"],
            "kind": item["kind"],
            "unit": "second",
            "creditsPerUnit": credits_per_unit,
            "usdPerUnit": round(display_usd_per_unit, 6),
            "brlPerUnit": _usd_to_brl_amount(display_usd_per_unit),
            "featured": bool(item.get("featured")),
            "plans": usage,
        })

    sections.append({
        "key": "video-models",
        "title": "Modelos de video",
        "rows": video_rows,
        "defaultVisibleRows": len(video_rows),
    })

    return sections


def get_credit_value_brl(packages: list[dict[str, Any]] | None = None) -> float:
    _ = packages
    return round(USD_TO_BRL * USD_PER_CREDIT, 6)


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

    credits_exact = max(0.0, float(provider_cost_usd or 0.0)) * USD_CENTS_PER_USD * MARGIN_MULTIPLIER
    minimum_credits = max(0, int(floor_credits or 0))
    if provider_cost_usd > 0:
        minimum_credits = max(1, minimum_credits)
    credits_needed = max(minimum_credits, int(math.ceil(credits_exact - 1e-9)))

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

    credits_per_second = _realistic_engine_credits_per_second(normalized_engine)
    floor_credits = max(1, int(math.ceil((duration * credits_per_second) - 1e-9)))
    estimate = _credits_from_provider_cost(provider_cost_usd, floor_credits=floor_credits)
    estimate.breakdown = {
        "mode": "realistic",
        "engine": normalized_engine,
        "duration_seconds": round(duration, 2),
        "credits_per_second": credits_per_second,
        "floor_credits": floor_credits,
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

    floor_credits = 1 if provider_cost_usd > 0 else 0

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


def estimate_image_generation_credits(
    model: str,
    image_count: int = 1,
    size: str = "2K",
    reference_image_count: int = 0,
    thinking_mode: bool = False,
) -> dict[str, Any]:
    normalized_model = str(model or "google/nano-banana-pro/text-to-image").strip()
    estimated_outputs = max(1, min(int(image_count or 1), 20))
    has_references = int(reference_image_count or 0) > 0
    if normalized_model == "ultra-high-3.0":
        normalized_model = "alibaba/wan-2.6/image-edit" if has_references else "z-image/turbo"
    elif normalized_model == "z-image/turbo":
        normalized_model = "alibaba/wan-2.6/image-edit" if has_references else "z-image/turbo"
    elif normalized_model == "bytedance/seedream-v5.0-lite/sequential":
        normalized_model = "bytedance/seedream-v5.0-lite/edit-sequential" if has_references else normalized_model
    elif normalized_model == "bytedance/seedream-v4.5":
        normalized_model = "bytedance/seedream-v4.5/edit" if has_references else normalized_model
    if normalized_model == "baidu/ERNIE-Image-Turbo/text-to-image":
        estimate = CreditEstimate(
            credits_needed=0,
            credits_exact=0.0,
            provider_cost_usd=0.0,
            provider_cost_brl=0.0,
            billed_cost_brl=0.0,
            brl_per_credit=get_credit_value_brl(CREDIT_PACKAGES),
            breakdown={
                "mode": "image_generation",
                "model": normalized_model,
                "image_count": estimated_outputs,
                "size": "FREE",
                "reference_image_count": 0,
                "thinking_mode": False,
                "floor_credits": 0,
                "components_usd": {
                    "images": 0.0,
                    "references": 0.0,
                },
            },
        )
        return estimate.to_dict()
    if normalized_model not in IMAGE_GENERATION_MODEL_USD:
        normalized_model = "google/nano-banana-pro/text-to-image"

    normalized_size = str(size or "2K").strip().upper()
    if normalized_size not in IMAGE_GENERATION_SIZE_MULTIPLIERS:
        normalized_size = "2K"

    outputs = estimated_outputs
    references = max(0, min(int(reference_image_count or 0), 9))
    thinking_enabled = bool(thinking_mode)

    base_usd = IMAGE_GENERATION_MODEL_USD[normalized_model]
    size_multiplier = IMAGE_GENERATION_SIZE_MULTIPLIERS[normalized_size]
    thinking_multiplier = IMAGE_GENERATION_THINKING_MULTIPLIER if thinking_enabled else 1.0
    provider_cost_usd = (base_usd * size_multiplier * thinking_multiplier * outputs) + (references * IMAGE_GENERATION_REFERENCE_USD)

    floor_credits = 0 if provider_cost_usd <= 0 else 1

    estimate = _credits_from_provider_cost(provider_cost_usd, floor_credits=floor_credits)
    estimate.breakdown = {
        "mode": "image_generation",
        "model": normalized_model,
        "image_count": outputs,
        "size": normalized_size,
        "reference_image_count": references,
        "thinking_mode": thinking_enabled,
        "floor_credits": floor_credits,
        "components_usd": {
            "images": round(base_usd * size_multiplier * thinking_multiplier * outputs, 6),
            "references": round(references * IMAGE_GENERATION_REFERENCE_USD, 6),
        },
    }
    return estimate.to_dict()


def estimate_similar_scene_credits(
    engine: str,
    duration_seconds: float | int,
) -> dict[str, Any]:
    return estimate_realistic_credits(
        engine=engine,
        duration_seconds=duration_seconds,
        has_reference_image=True,
        add_music=False,
        add_narration=False,
        enable_subtitles=False,
        use_external_audio=False,
    )


def estimate_similar_previews_credits(
    engine: str,
    scene_durations: list[float | int] | tuple[float | int, ...],
) -> dict[str, Any]:
    normalized_durations = [max(1.0, float(item or 0)) for item in (scene_durations or []) if float(item or 0) > 0]
    if not normalized_durations:
        normalized_durations = [5.0]

    scene_estimates = [estimate_similar_scene_credits(engine=engine, duration_seconds=duration) for duration in normalized_durations]
    total_credits = sum(int(item.get("credits_needed", 0) or 0) for item in scene_estimates)
    total_exact = sum(float(item.get("credits_exact", 0.0) or 0.0) for item in scene_estimates)
    total_provider_usd = sum(float(item.get("provider_cost_usd", 0.0) or 0.0) for item in scene_estimates)
    total_provider_brl = sum(float(item.get("provider_cost_brl", 0.0) or 0.0) for item in scene_estimates)
    total_billed_brl = sum(float(item.get("billed_cost_brl", 0.0) or 0.0) for item in scene_estimates)

    return {
        "rules_version": CREDIT_PRICING_RULES_VERSION,
        "credits_needed": total_credits,
        "credits_exact": round(total_exact, 2),
        "provider_cost_usd": round(total_provider_usd, 6),
        "provider_cost_brl": round(total_provider_brl, 4),
        "billed_cost_brl": round(total_billed_brl, 4),
        "brl_per_credit": round(get_credit_value_brl(CREDIT_PACKAGES), 6),
        "margin_multiplier": MARGIN_MULTIPLIER,
        "breakdown": {
            "mode": "similar_previews",
            "engine": str(engine or "").strip().lower() or "wan2",
            "scene_count": len(normalized_durations),
            "scene_durations": [round(float(duration), 2) for duration in normalized_durations],
        },
    }


def estimate_similar_analysis_credits(
    duration_seconds: float | int,
    analysis_mode: str = "scene",
) -> dict[str, Any]:
    duration = max(1.0, _safe_duration_seconds(duration_seconds))
    normalized_mode = str(analysis_mode or "scene").strip().lower()
    if normalized_mode not in {"scene", "general"}:
        normalized_mode = "scene"

    scene_count = max(1, int(math.ceil(duration / SIMILAR_ANALYSIS_SCENE_SECONDS)))
    transcript_usd = duration * STT_USD_PER_SEC

    if normalized_mode == "general":
        reference_frame_count = min(8, max(4, int(math.ceil(duration / 6.0))))
        provider_cost_usd = (
            transcript_usd
            + (reference_frame_count * SIMILAR_ANALYSIS_FRAME_USD)
            + SIMILAR_ANALYSIS_SUMMARY_USD
            + SIMILAR_ANALYSIS_GENERAL_PROMPT_USD
        )
        floor_credits = max(6, (int(math.ceil(duration / 20.0)) * 2) + 4)
    else:
        reference_frame_count = scene_count + SIMILAR_ANALYSIS_CONTEXT_FRAMES
        provider_cost_usd = (
            transcript_usd
            + (reference_frame_count * SIMILAR_ANALYSIS_FRAME_USD)
            + (scene_count * SIMILAR_ANALYSIS_SCENE_PROMPT_USD)
            + SIMILAR_ANALYSIS_SUMMARY_USD
        )
        floor_credits = max(10, scene_count * 3)

    estimate = _credits_from_provider_cost(provider_cost_usd, floor_credits=floor_credits)
    estimate.breakdown = {
        "mode": "similar_analysis",
        "analysis_mode": normalized_mode,
        "duration_seconds": round(duration, 2),
        "scene_count": scene_count,
        "reference_frame_count": reference_frame_count,
        "floor_credits": floor_credits,
        "components_usd": {
            "transcript": round(transcript_usd, 6),
            "frame_analysis": round(reference_frame_count * SIMILAR_ANALYSIS_FRAME_USD, 6),
            "summary": round(SIMILAR_ANALYSIS_SUMMARY_USD, 6),
            "prompt_structuring": round(
                SIMILAR_ANALYSIS_GENERAL_PROMPT_USD if normalized_mode == "general"
                else scene_count * SIMILAR_ANALYSIS_SCENE_PROMPT_USD,
                6,
            ),
        },
    }
    return estimate.to_dict()


def estimate_local_video_processing_credits(duration_seconds: float | int) -> dict[str, Any]:
    duration = max(1.0, _safe_duration_seconds(duration_seconds))
    provider_cost_usd = (duration / 60.0) * CUSTOM_VIDEO_PROCESS_USD_PER_MIN
    estimate = _credits_from_provider_cost(provider_cost_usd, floor_credits=1)
    estimate.breakdown = {
        "mode": "local_video_processing",
        "duration_seconds": round(duration, 2),
        "floor_credits": 1,
        "components_usd": {
            "local_processing": round(provider_cost_usd, 6),
        },
    }
    return estimate.to_dict()


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
