"""
Credits Router — Purchase and manage video generation credits.
Integrates with Mercado Pago for PIX and Card payments.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.services.credit_pricing import (
    CREDIT_PACKAGES,
    CREDIT_PRICING_RULES_VERSION,
    USD_PER_CREDIT,
    get_credit_comparison_sections,
    get_credit_packages,
    get_paid_plan_codes,
    get_subscription_plan,
    get_subscription_plans,
    get_credit_value_brl,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/credits", tags=["credits"])
settings = get_settings()

INITIAL_CREDITS = 100
CREDITS_PER_MINUTE = 5
CREDIT_REFERENCE_PREFIX = "CVCR"
PLAN_REFERENCE_PREFIX = "CVPLN"


def _extract_error_detail_message(detail) -> str:
    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, dict):
        for key in ("message", "detail", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(detail, list):
        messages: list[str] = []
        for item in detail:
            message = _extract_error_detail_message(item)
            if message:
                messages.append(message)
        return " | ".join(messages)
    return ""


def extract_api_error_message(err, fallback: str = "Erro inesperado") -> str:
    message = _extract_error_detail_message(getattr(err, "detail", None))
    if message:
        return message

    raw = str(err or "").strip()
    if raw:
        return raw
    return fallback


def build_insufficient_credits_message(current: int, amount: int) -> str:
    return (
        f"Créditos insuficientes. Você tem {current} créditos, mas precisa de {amount}. "
        "O vídeo não foi gerado."
    )


async def get_credit_balance(db: AsyncSession, user_id: int) -> int:
    row = await db.execute(
        text("SELECT credits FROM auth_users WHERE id = :uid"),
        {"uid": user_id},
    )
    return int(row.scalar() or 0)


def _generate_reference() -> str:
    return _generate_purchase_reference()


def _generate_purchase_reference(kind: str = "credits", plan_code: str = "") -> str:
    token = secrets.token_hex(6).upper()
    normalized_kind = str(kind or "credits").strip().lower()
    if normalized_kind == "plan":
        normalized_plan = "".join(ch for ch in str(plan_code or "").lower() if ch.isalnum()) or "starter"
        return f"{PLAN_REFERENCE_PREFIX}-{normalized_plan.upper()}-{token}"
    return f"{CREDIT_REFERENCE_PREFIX}-{token}"


def _parse_reference_metadata(reference: str) -> dict[str, str]:
    raw = str(reference or "").strip()
    upper = raw.upper()
    if upper.startswith(f"{PLAN_REFERENCE_PREFIX}-"):
        parts = raw.split("-", 2)
        plan_code = parts[1].strip().lower() if len(parts) >= 2 else ""
        return {"kind": "plan", "plan_code": plan_code}
    return {"kind": "credits", "plan_code": ""}


def _normalize_plan_code(plan_code: str) -> str:
    normalized = str(plan_code or "free").strip().lower() or "free"
    return "professional" if normalized == "pro" else normalized


async def _mp_request(endpoint: str, method: str = "GET", body: dict | None = None, idempotency_key: str = "") -> dict:
    mp_token = settings.mp_access_token
    if not mp_token:
        raise HTTPException(status_code=503, detail="Pagamentos não configurados.")
    url = f"https://api.mercadopago.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {mp_token}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=30) as client:
        if method == "POST":
            resp = await client.post(url, json=body, headers=headers)
        else:
            resp = await client.get(url, headers=headers)
        data = resp.json()
        if not resp.is_success:
            logger.error(f"[MP] {endpoint} → {resp.status_code}: {str(data)[:500]}")
        return data


# ── GET /api/credits — user balance ──
@router.get("")
async def get_credits(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        text(
            """
            SELECT credits, COALESCE(plan, 'free') AS plan, plan_expires_at
            FROM auth_users
            WHERE id = :uid
            """
        ),
        {"uid": user["id"]},
    )
    profile = row.mappings().first() or {}
    credits = int(profile.get("credits") or 0)
    current_plan = _normalize_plan_code(profile.get("plan") or "free")
    plan_expires_at = profile.get("plan_expires_at")
    if current_plan != "free" and plan_expires_at and plan_expires_at <= datetime.utcnow():
        current_plan = "free"

    return {
        "credits": credits,
        "creditsPerMinute": CREDITS_PER_MINUTE,
        "packages": get_credit_packages(),
        "plans": get_subscription_plans(),
        "comparisonSections": get_credit_comparison_sections(),
        "creditValueBrl": round(get_credit_value_brl(CREDIT_PACKAGES), 6),
        "creditValueUsd": round(float(USD_PER_CREDIT), 6),
        "pricingVersion": CREDIT_PRICING_RULES_VERSION,
        "currentPlan": current_plan,
        "planExpiresAt": plan_expires_at,
    }


# ── POST /api/credits/purchase/pix ──
class PurchaseRequest(BaseModel):
    packageIndex: int


class PlanPurchaseRequest(BaseModel):
    planCode: str


@router.post("/purchase/pix")
async def purchase_pix(
    req: PurchaseRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.packageIndex < 0 or req.packageIndex >= len(CREDIT_PACKAGES):
        raise HTTPException(status_code=400, detail="Pacote inválido.")

    pkg = CREDIT_PACKAGES[req.packageIndex]
    reference = _generate_purchase_reference(kind="credits")

    # Get user email
    row = await db.execute(
        text("SELECT email FROM auth_users WHERE id = :uid"),
        {"uid": user["id"]},
    )
    email = row.scalar() or "user@criavideo.pro"

    # Save pending purchase
    await db.execute(
        text("""
            INSERT INTO credit_purchases (user_id, credits, amount, type, status, reference)
            VALUES (:uid, :credits, :amount, 'pix', 'pending', :ref)
        """),
        {"uid": user["id"], "credits": pkg["credits"], "amount": pkg["price"], "ref": reference},
    )
    await db.commit()

    # Create Mercado Pago PIX payment
    payment_data = {
        "transaction_amount": pkg["price"],
        "description": f"CriaVideo — {pkg['credits']} créditos",
        "payment_method_id": "pix",
        "payer": {"email": email},
        "external_reference": reference,
        "notification_url": f"{settings.site_url}/api/credits/webhook",
    }

    result = await _mp_request("/v1/payments", method="POST", body=payment_data, idempotency_key=reference)

    if result.get("id"):
        await db.execute(
            text("UPDATE credit_purchases SET mp_payment_id = :mpid WHERE reference = :ref"),
            {"mpid": str(result["id"]), "ref": reference},
        )
        await db.commit()

        pix_info = result.get("point_of_interaction", {}).get("transaction_data", {})
        return {
            "ok": True,
            "reference": reference,
            "credits": pkg["credits"],
            "kind": "credits",
            "pixCopiaECola": pix_info.get("qr_code", ""),
            "qrBase64": pix_info.get("qr_code_base64", ""),
        }
    else:
        await db.execute(
            text("UPDATE credit_purchases SET status = 'failed' WHERE reference = :ref"),
            {"ref": reference},
        )
        await db.commit()
        raise HTTPException(status_code=400, detail=result.get("message", "Erro ao criar pagamento PIX."))


# ── POST /api/credits/purchase/card ──
@router.post("/purchase/card")
async def purchase_card(
    req: PurchaseRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.packageIndex < 0 or req.packageIndex >= len(CREDIT_PACKAGES):
        raise HTTPException(status_code=400, detail="Pacote inválido.")

    pkg = CREDIT_PACKAGES[req.packageIndex]
    reference = _generate_purchase_reference(kind="credits")

    row = await db.execute(
        text("SELECT email FROM auth_users WHERE id = :uid"),
        {"uid": user["id"]},
    )
    email = row.scalar() or "user@criavideo.pro"

    await db.execute(
        text("""
            INSERT INTO credit_purchases (user_id, credits, amount, type, status, reference)
            VALUES (:uid, :credits, :amount, 'card', 'pending', :ref)
        """),
        {"uid": user["id"], "credits": pkg["credits"], "amount": pkg["price"], "ref": reference},
    )
    await db.commit()

    payment_data = {
        "transaction_amount": pkg["price"],
        "description": f"CriaVideo — {pkg['credits']} créditos",
        "payment_method_id": "master",
        "payer": {"email": email},
        "external_reference": reference,
        "notification_url": f"{settings.site_url}/api/credits/webhook",
        "back_url": f"{settings.site_url}/video?payment=credits",
    }

    result = await _mp_request("/v1/payments", method="POST", body=payment_data, idempotency_key=reference)

    if result.get("id"):
        await db.execute(
            text("UPDATE credit_purchases SET mp_payment_id = :mpid WHERE reference = :ref"),
            {"mpid": str(result["id"]), "ref": reference},
        )
        await db.commit()
        return {
            "ok": True,
            "reference": reference,
            "credits": pkg["credits"],
            "kind": "credits",
            "checkoutUrl": result.get("init_point", ""),
        }
    else:
        await db.execute(
            text("UPDATE credit_purchases SET status = 'failed' WHERE reference = :ref"),
            {"ref": reference},
        )
        await db.commit()
        raise HTTPException(status_code=400, detail=result.get("message", "Erro ao criar pagamento."))


@router.post("/purchase/plan/pix")
async def purchase_plan_pix(
    req: PlanPurchaseRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = get_subscription_plan(req.planCode)
    if plan["code"] not in get_paid_plan_codes():
        raise HTTPException(status_code=400, detail="Plano mensal invalido.")

    reference = _generate_purchase_reference(kind="plan", plan_code=plan["code"])

    row = await db.execute(
        text("SELECT email FROM auth_users WHERE id = :uid"),
        {"uid": user["id"]},
    )
    email = row.scalar() or "user@criavideo.pro"

    await db.execute(
        text(
            """
            INSERT INTO credit_purchases (user_id, credits, amount, type, status, reference)
            VALUES (:uid, :credits, :amount, 'pix', 'pending', :ref)
            """
        ),
        {
            "uid": user["id"],
            "credits": plan["monthlyCredits"],
            "amount": plan["price"],
            "ref": reference,
        },
    )
    await db.commit()

    payment_data = {
        "transaction_amount": plan["price"],
        "description": f"CriaVideo — Plano {plan['name']} ({plan['monthlyCredits']} creditos)",
        "payment_method_id": "pix",
        "payer": {"email": email},
        "external_reference": reference,
        "notification_url": f"{settings.site_url}/api/credits/webhook",
    }

    result = await _mp_request("/v1/payments", method="POST", body=payment_data, idempotency_key=reference)

    if result.get("id"):
        await db.execute(
            text("UPDATE credit_purchases SET mp_payment_id = :mpid WHERE reference = :ref"),
            {"mpid": str(result["id"]), "ref": reference},
        )
        await db.commit()

        pix_info = result.get("point_of_interaction", {}).get("transaction_data", {})
        return {
            "ok": True,
            "reference": reference,
            "credits": plan["monthlyCredits"],
            "kind": "plan",
            "planCode": plan["code"],
            "planName": plan["name"],
            "pixCopiaECola": pix_info.get("qr_code", ""),
            "qrBase64": pix_info.get("qr_code_base64", ""),
        }

    await db.execute(
        text("UPDATE credit_purchases SET status = 'failed' WHERE reference = :ref"),
        {"ref": reference},
    )
    await db.commit()
    raise HTTPException(status_code=400, detail=result.get("message", "Erro ao criar pagamento PIX."))


@router.post("/purchase/plan/card")
async def purchase_plan_card(
    req: PlanPurchaseRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = get_subscription_plan(req.planCode)
    if plan["code"] not in get_paid_plan_codes():
        raise HTTPException(status_code=400, detail="Plano mensal invalido.")

    reference = _generate_purchase_reference(kind="plan", plan_code=plan["code"])

    row = await db.execute(
        text("SELECT email FROM auth_users WHERE id = :uid"),
        {"uid": user["id"]},
    )
    email = row.scalar() or "user@criavideo.pro"

    await db.execute(
        text(
            """
            INSERT INTO credit_purchases (user_id, credits, amount, type, status, reference)
            VALUES (:uid, :credits, :amount, 'card', 'pending', :ref)
            """
        ),
        {
            "uid": user["id"],
            "credits": plan["monthlyCredits"],
            "amount": plan["price"],
            "ref": reference,
        },
    )
    await db.commit()

    payment_data = {
        "transaction_amount": plan["price"],
        "description": f"CriaVideo — Plano {plan['name']} ({plan['monthlyCredits']} creditos)",
        "payment_method_id": "master",
        "payer": {"email": email},
        "external_reference": reference,
        "notification_url": f"{settings.site_url}/api/credits/webhook",
        "back_url": f"{settings.site_url}/video?payment=plan",
    }

    result = await _mp_request("/v1/payments", method="POST", body=payment_data, idempotency_key=reference)

    if result.get("id"):
        await db.execute(
            text("UPDATE credit_purchases SET mp_payment_id = :mpid WHERE reference = :ref"),
            {"mpid": str(result["id"]), "ref": reference},
        )
        await db.commit()
        return {
            "ok": True,
            "reference": reference,
            "credits": plan["monthlyCredits"],
            "kind": "plan",
            "planCode": plan["code"],
            "planName": plan["name"],
            "checkoutUrl": result.get("init_point", ""),
        }

    await db.execute(
        text("UPDATE credit_purchases SET status = 'failed' WHERE reference = :ref"),
        {"ref": reference},
    )
    await db.commit()
    raise HTTPException(status_code=400, detail=result.get("message", "Erro ao criar pagamento."))


# ── GET /api/credits/status/:reference ──
@router.get("/status/{reference}")
async def check_status(
    reference: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        text("SELECT * FROM credit_purchases WHERE reference = :ref AND user_id = :uid"),
        {"ref": reference, "uid": user["id"]},
    )
    purchase = row.mappings().first()
    if not purchase:
        raise HTTPException(status_code=404, detail="Compra não encontrada.")

    purchase_meta = _parse_reference_metadata(reference)

    if purchase["status"] == "pending" and purchase["mp_payment_id"]:
        mp = await _mp_request(f"/v1/payments/{purchase['mp_payment_id']}")
        if mp.get("status") == "approved":
            await _confirm_credit_purchase(db, reference, purchase["mp_payment_id"])
            return {
                "status": "confirmed",
                "credits": purchase["credits"],
                "kind": purchase_meta["kind"],
                "planCode": purchase_meta["plan_code"] or None,
            }

    return {
        "status": purchase["status"],
        "credits": purchase["credits"],
        "kind": purchase_meta["kind"],
        "planCode": purchase_meta["plan_code"] or None,
    }


# ── POST /api/credits/webhook — Mercado Pago IPN/Webhook ──
@router.post("/webhook")
async def webhook(request: Request, db: AsyncSession = Depends(get_db)):
    # Always respond 200 immediately
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    topic = request.query_params.get("topic") or request.query_params.get("type")
    q_id = request.query_params.get("id") or request.query_params.get("data.id")

    payment_id = None

    # IPN format
    if topic == "payment" and q_id:
        payment_id = str(q_id)
    # Webhook format
    if not payment_id and body.get("type") == "payment" and body.get("data", {}).get("id"):
        payment_id = str(body["data"]["id"])
    if not payment_id and body.get("action", ""):
        if "payment" in body.get("action", "") and body.get("data", {}).get("id"):
            payment_id = str(body["data"]["id"])

    if payment_id:
        try:
            mp = await _mp_request(f"/v1/payments/{payment_id}")
            if mp.get("status") == "approved":
                ref = mp.get("external_reference")
                if ref:
                    await _confirm_credit_purchase(db, ref, payment_id)
        except Exception as e:
            logger.error(f"[Webhook] Error processing payment {payment_id}: {e}")

    # Merchant order
    if topic == "merchant_order" and q_id:
        try:
            order = await _mp_request(f"/merchant_orders/{q_id}")
            for p in order.get("payments", []):
                if p.get("status") == "approved":
                    mp = await _mp_request(f"/v1/payments/{p['id']}")
                    ref = mp.get("external_reference")
                    if ref:
                        await _confirm_credit_purchase(db, ref, str(p["id"]))
        except Exception as e:
            logger.error(f"[Webhook] Error processing merchant_order {q_id}: {e}")

    return {"ok": True}


async def _confirm_credit_purchase(db: AsyncSession, reference: str, mp_payment_id: str):
    row = await db.execute(
        text("SELECT user_id, credits FROM credit_purchases WHERE reference = :ref AND status != 'confirmed'"),
        {"ref": reference},
    )
    purchase = row.mappings().first()
    if not purchase:
        return

    user_id = purchase["user_id"]
    credits = purchase["credits"]

    await db.execute(
        text("UPDATE credit_purchases SET status = 'confirmed', mp_payment_id = :mpid WHERE reference = :ref"),
        {"mpid": mp_payment_id, "ref": reference},
    )
    await db.execute(
        text("UPDATE auth_users SET credits = credits + :credits WHERE id = :uid"),
        {"credits": credits, "uid": user_id},
    )

    purchase_meta = _parse_reference_metadata(reference)
    if purchase_meta["kind"] == "plan" and purchase_meta["plan_code"] in get_paid_plan_codes():
        now = datetime.utcnow()
        expires_row = await db.execute(
            text("SELECT plan_expires_at FROM auth_users WHERE id = :uid"),
            {"uid": user_id},
        )
        current_expires_at = expires_row.scalar()
        base_date = current_expires_at if isinstance(current_expires_at, datetime) and current_expires_at > now else now
        new_expires_at = base_date + timedelta(days=30)
        await db.execute(
            text(
                """
                UPDATE auth_users
                SET plan = :plan, plan_expires_at = :expires_at
                WHERE id = :uid
                """
            ),
            {
                "plan": purchase_meta["plan_code"],
                "expires_at": new_expires_at,
                "uid": user_id,
            },
        )

    await db.commit()
    logger.info(f"[Credits] Confirmed {reference} — user {user_id} received {credits} credits")


# ── Helper: deduct credits (called from video router) ──
async def is_levita_credit_bypass_user(
    db: AsyncSession,
    user: dict | None = None,
    user_id: int | None = None,
) -> bool:
    """Levita credit bypass is disabled after auth separation between apps."""
    _ = db
    _ = user
    _ = user_id
    return False


async def deduct_credits(db: AsyncSession, user_id: int, amount: int) -> int:
    """Deduct credits. Returns remaining balance. Raises HTTPException if insufficient."""
    normalized_amount = max(0, int(amount or 0))
    if normalized_amount <= 0:
        return await get_credit_balance(db, user_id)

    result = await db.execute(
        text(
            """
            UPDATE auth_users
            SET credits = credits - :amount
            WHERE id = :uid AND credits >= :amount
            RETURNING credits
            """
        ),
        {"amount": normalized_amount, "uid": user_id},
    )
    remaining = result.scalar()
    if remaining is None:
        current = await get_credit_balance(db, user_id)
        raise HTTPException(
            status_code=402,
            detail=build_insufficient_credits_message(current, normalized_amount),
        )
    await db.commit()
    return int(remaining or 0)
