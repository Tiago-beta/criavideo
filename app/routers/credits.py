"""
Credits Router — Purchase and manage video generation credits.
Integrates with Mercado Pago for PIX and Card payments.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/credits", tags=["credits"])
settings = get_settings()

INITIAL_CREDITS = 50
CREDITS_PER_MINUTE = 5

CREDIT_PACKAGES = [
    {"credits": 100, "price": 4.99, "label": "100 créditos"},
    {"credits": 250, "price": 9.99, "label": "250 créditos"},
    {"credits": 600, "price": 19.99, "label": "600 créditos"},
]


def _generate_reference() -> str:
    return f"CV{secrets.token_hex(8).upper()}"


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
        text("SELECT credits FROM auth_users WHERE id = :uid"),
        {"uid": user["id"]},
    )
    credits = row.scalar() or 0
    return {
        "credits": credits,
        "creditsPerMinute": CREDITS_PER_MINUTE,
        "packages": CREDIT_PACKAGES,
    }


# ── POST /api/credits/purchase/pix ──
class PurchaseRequest(BaseModel):
    packageIndex: int


@router.post("/purchase/pix")
async def purchase_pix(
    req: PurchaseRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.packageIndex < 0 or req.packageIndex >= len(CREDIT_PACKAGES):
        raise HTTPException(status_code=400, detail="Pacote inválido.")

    pkg = CREDIT_PACKAGES[req.packageIndex]
    reference = _generate_reference()

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
    reference = _generate_reference()

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
            "checkoutUrl": result.get("init_point", ""),
        }
    else:
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

    if purchase["status"] == "pending" and purchase["mp_payment_id"]:
        mp = await _mp_request(f"/v1/payments/{purchase['mp_payment_id']}")
        if mp.get("status") == "approved":
            await _confirm_credit_purchase(db, reference, purchase["mp_payment_id"])
            return {"status": "confirmed", "credits": purchase["credits"]}

    return {"status": purchase["status"], "credits": purchase["credits"]}


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
    await db.commit()
    logger.info(f"[Credits] Confirmed {reference} — user {user_id} received {credits} credits")


# ── Helper: deduct credits (called from video router) ──
async def deduct_credits(db: AsyncSession, user_id: int, amount: int) -> int:
    """Deduct credits. Returns remaining balance. Raises HTTPException if insufficient."""
    row = await db.execute(
        text("SELECT credits FROM auth_users WHERE id = :uid"),
        {"uid": user_id},
    )
    current = row.scalar() or 0
    if current < amount:
        raise HTTPException(
            status_code=402,
            detail=f"Créditos insuficientes. Você tem {current} créditos, precisa de {amount}.",
        )
    await db.execute(
        text("UPDATE auth_users SET credits = credits - :amount WHERE id = :uid"),
        {"amount": amount, "uid": user_id},
    )
    await db.commit()
    return current - amount
