"""
Pay-what-you-want endpoint for the digital build pack.

Replaces the shelved checkout saga (hold → marketplace order → capture). The
build pack is a digital product: the customer names their price (>= $0, zero
allowed), pays once via Stripe, then downloads. There is no fulfillment behind
the charge — nothing to hold, capture, or compensate.

This module deliberately imports ONLY the payment provider registry + error
types. It does not touch saga.py / optimizer.py / clients/ / gate.py — those
are shelved.

Flow (the modal + Stripe Elements live in the separate laigo-frontend repo):
  1. User clicks "Download build pack" → modal asks for an amount.
  2. amount == 0  → POST {amount_cents: 0}; we record a free download.
  3. amount  > 0  → frontend collects the card with Stripe Elements, creates a
     PaymentMethod, POSTs {amount_cents, payment_method_id}; we charge it.
     If Stripe needs 3DS we return {status: "requires_action", client_secret}
     for the frontend to finish, then it proceeds to download.
  4. Frontend calls GET /jobs/{job_id}/download (which is ungated).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .checkout.payment import registry as payment_registry
from .checkout.payment.base import (
    PaymentPermanentError,
    PaymentProviderUnavailable,
    PaymentRetryableError,
)

logger = logging.getLogger("laigo")

# Mirror Main.py's OUTPUT_DIR resolution so payment.json lands beside the
# job's artifact.zip / order_list.json.
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()

# Stripe's minimum charge for USD is 50 cents. Amounts of 1–49 cents are
# rejected with a clear message; 0 is free.
_STRIPE_MIN_CHARGE_CENTS = 50

pay_router = APIRouter()


class PayRequest(BaseModel):
    amount_cents: int = Field(
        ..., ge=0,
        description="What the customer chooses to pay, in US cents. 0 = free.",
    )
    payment_method_id: Optional[str] = Field(
        None,
        description="Stripe PaymentMethod id (pm_...) from Stripe Elements. "
                    "Required when amount_cents > 0.",
    )


def _record_payment(
    job_id: str,
    *,
    amount_cents: int,
    status: str,
    payment_intent_id: Optional[str],
) -> None:
    """Best-effort revenue log at outputs/{job_id}/payment.json.

    Never raises — a failed write must not fail the customer's charge. Mirrors
    the per-job JSON pattern used by checkout/checkout_store.py. This is a log,
    NOT a download gate (GET /download stays ungated).
    """
    try:
        job_dir = OUTPUT_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "job_id": job_id,
            "amount_cents": amount_cents,
            "status": status,
            "payment_intent_id": payment_intent_id,
            "recorded_at": time.time(),
        }
        (job_dir / "payment.json").write_text(json.dumps(record, indent=2))
    except Exception as exc:  # pragma: no cover - logging only
        logger.warning("pay.record_failed job_id=%s err=%s", job_id, exc)


@pay_router.post("/{job_id}/pay")
async def pay(job_id: str, body: PayRequest):
    amount = body.amount_cents

    # ── Free download ────────────────────────────────────────────────────────
    if amount == 0:
        _record_payment(job_id, amount_cents=0, status="free", payment_intent_id=None)
        logger.info("pay.free job_id=%s", job_id)
        return {"status": "free", "amount_cents": 0}

    # ── Below Stripe's minimum ───────────────────────────────────────────────
    if amount < _STRIPE_MIN_CHARGE_CENTS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Enter $0 to download for free, or at least $0.50 to "
                         "contribute. Amounts under $0.50 can't be processed.",
                "code": "AMOUNT_BELOW_MINIMUM",
                "min_cents": _STRIPE_MIN_CHARGE_CENTS,
            },
        )

    # ── Paid: charge the card ────────────────────────────────────────────────
    if not body.payment_method_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "payment_method_id is required when paying.",
                "code": "PAYMENT_METHOD_REQUIRED",
            },
        )

    try:
        provider = payment_registry.get_active()
    except PaymentProviderUnavailable:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Payments are not configured right now.",
                "code": "PAYMENTS_UNAVAILABLE",
            },
        )

    try:
        result = await provider.charge(
            amount_cents=amount,
            payment_method_id=body.payment_method_id,
            idempotency_key=f"charge-{job_id}",
        )
    except PaymentRetryableError as exc:
        logger.warning("pay.retryable job_id=%s err=%s", job_id, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Payment is temporarily unavailable. Please try again.",
                "code": "PAYMENT_RETRYABLE",
            },
        )
    except PaymentPermanentError as exc:
        logger.warning("pay.declined job_id=%s err=%s", job_id, exc)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Your payment could not be completed. Please check "
                         "your card details and try again.",
                "code": "PAYMENT_FAILED",
            },
        )

    # ── 3DS / SCA: hand the client_secret back for the frontend to finish ────
    if result["status"] == "requires_action":
        logger.info(
            "pay.requires_action job_id=%s pi=%s",
            job_id, result["payment_intent_id"],
        )
        return {
            "status": "requires_action",
            "client_secret": result["client_secret"],
            "payment_intent_id": result["payment_intent_id"],
        }

    # ── Succeeded ────────────────────────────────────────────────────────────
    _record_payment(
        job_id, amount_cents=amount, status="paid",
        payment_intent_id=result["payment_intent_id"],
    )
    logger.info(
        "pay.succeeded job_id=%s pi=%s amount=%d",
        job_id, result["payment_intent_id"], amount,
    )
    return {
        "status": "paid",
        "amount_cents": amount,
        "payment_intent_id": result["payment_intent_id"],
    }
