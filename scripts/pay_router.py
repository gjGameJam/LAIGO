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
  1. User clicks "Download build pack" → modal asks for an amount + email.
  2. amount == 0  → POST {amount_cents: 0, email}; we record a free download.
  3. amount  > 0  → frontend collects the card with Stripe Elements, creates a
     PaymentMethod, POSTs {amount_cents, payment_method_id, email}; we charge it.
     If Stripe needs 3DS we return {status: "requires_action", client_secret}
     for the frontend to finish, then it proceeds to download.
  4. Frontend calls GET /jobs/{job_id}/download (which is ungated).
  5. Every completed checkout ($0 or paid) also emails the build pack to the
     given address via scripts/emailer.py (fire-and-forget BackgroundTasks —
     a send failure never fails the charge). For card payments the address
     rides in the PaymentIntent metadata so the webhook can email after 3DS
     completions without any server-side storage; the emailer's email.json
     sentinel dedupes when both the sync and webhook paths fire.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import emailer

from .checkout.payment import registry as payment_registry
from .checkout.payment.base import (
    PaymentPermanentError,
    PaymentProviderUnavailable,
    PaymentRetryableError,
    WebhookVerificationError,
)
from .checkout.payment.stripe_provider import construct_webhook_event

logger = logging.getLogger("laigo")

# Mirror Main.py's OUTPUT_DIR resolution so payment.json lands beside the
# job's artifact.zip / order_list.json.
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()

# Stripe's minimum charge for USD is 50 cents. Amounts of 1–49 cents are
# rejected with a clear message; 0 is free.
_STRIPE_MIN_CHARGE_CENTS = 50

# Upper bound = Stripe's documented max charge for USD ($999,999.99). Rejecting
# above this returns a clean 422 instead of a Stripe round-trip, and caps
# fat-finger / abusive amounts. Lower it if you want a tighter business limit.
_MAX_CHARGE_CENTS = 99_999_999

# Legit job ids are UUIDs from Main.py. Validate the charset before using
# job_id in any filesystem path (payment.json write / artifact lookup) so a
# crafted value (e.g. containing '..' or separators) can't escape OUTPUT_DIR.
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Deliberately loose shape check (something@something.tld). Resend/Stripe are
# the real validators; pydantic's EmailStr would pull in the email-validator
# dependency for no additional guarantee of deliverability.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

pay_router = APIRouter()


class PayRequest(BaseModel):
    amount_cents: int = Field(
        ..., ge=0, le=_MAX_CHARGE_CENTS,
        description="What the customer chooses to pay, in US cents. 0 = free.",
    )
    payment_method_id: Optional[str] = Field(
        None,
        description="Stripe PaymentMethod id (pm_...) from Stripe Elements. "
                    "Required when amount_cents > 0.",
    )
    email: str = Field(
        ..., max_length=254,
        description="Where the build pack is sent. Required, including for $0 "
                    "downloads.",
    )

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("must be a valid email address")
        return v


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
        record_path = job_dir / "payment.json"
        # Never downgrade an authoritative paid record. Guards against a $0
        # re-submit (or a stray sync/webhook ordering) clobbering a real charge.
        if status != "paid" and record_path.exists():
            try:
                prior = json.loads(record_path.read_text(encoding="utf-8"))
                if prior.get("status") == "paid":
                    return
            except Exception:
                pass  # unreadable prior record — fall through and overwrite
        record = {
            "job_id": job_id,
            "amount_cents": amount_cents,
            "status": status,
            "payment_intent_id": payment_intent_id,
            "recorded_at": time.time(),
        }
        record_path.write_text(
            json.dumps(record, indent=2), encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover - logging only
        logger.warning("pay.record_failed job_id=%s err=%s", job_id, exc)


@pay_router.post("/{job_id}/pay")
async def pay(job_id: str, body: PayRequest, background_tasks: BackgroundTasks):
    # ── Validate job_id (path-traversal guard) ───────────────────────────────
    if not _SAFE_JOB_ID.match(job_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid job id.", "code": "INVALID_JOB_ID"},
        )

    # ── Only accept payment for a real, completed build pack ─────────────────
    # The artifact is the on-disk source of truth (restart-safe even though the
    # JSON jobs store is in-memory). Mirrors GET /download's 404 behaviour.
    if not (OUTPUT_DIR / job_id / "artifact.zip").exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "Build pack not found for this job.",
                    "code": "JOB_NOT_FOUND"},
        )

    amount = body.amount_cents

    # ── Free download ────────────────────────────────────────────────────────
    if amount == 0:
        _record_payment(job_id, amount_cents=0, status="free", payment_intent_id=None)
        background_tasks.add_task(
            emailer.send_build_pack_email,
            job_id=job_id, to_email=body.email, amount_cents=0,
            job_dir=OUTPUT_DIR / job_id,
        )
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
            # Idempotency keyed on (job_id, amount): a double-click / network
            # retry of the SAME amount dedupes to a single charge (Stripe returns
            # the original within its 24h window), while a deliberate later
            # contribution of a DIFFERENT amount is allowed through as a new
            # charge instead of failing with a confusing IdempotencyError.
            idempotency_key=f"charge-{job_id}-{amount}",
            # Carried on the PaymentIntent so the webhook can map a later
            # payment_intent.succeeded back to this job — including the
            # address to email the build pack to (the PaymentIntent is the
            # only place it is stored).
            metadata={"job_id": job_id, "source": "laigo_pay",
                      "email": body.email},
            # Stripe's own payment receipt (live mode only).
            receipt_email=body.email,
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
    background_tasks.add_task(
        emailer.send_build_pack_email,
        job_id=job_id, to_email=body.email, amount_cents=amount,
        job_dir=OUTPUT_DIR / job_id,
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


# ─────────────────────────────────────────────────────────────────────────────
# Donate — global, client-confirm tip endpoint.
#
# The simpler sibling of /pay. The server only MINTS an unconfirmed
# PaymentIntent and returns its client_secret; the frontend's Stripe.js collects
# the card and calls confirmPayment(secret), handling 3DS natively and
# triggering the (ungated) download on success. The server never touches the
# card. No job scope, no payment.json write here — the existing
# POST /webhooks/stripe is the only authoritative recorder, and only when a
# job_id rides along in metadata (optional). Mounted at the app root.
# ─────────────────────────────────────────────────────────────────────────────

donate_router = APIRouter()


class DonateRequest(BaseModel):
    amount_cents: int = Field(
        ...,
        description="Tip amount in US cents. Must be >= 50 (Stripe minimum).",
    )
    job_id: Optional[str] = Field(
        None,
        description="Optional job to attribute the tip to. When present and "
                    "well-formed, it is added to the PaymentIntent metadata so "
                    "the payment_intent.succeeded webhook records "
                    "outputs/{job_id}/payment.json.",
    )


@donate_router.post("/donate")
async def donate(body: DonateRequest):
    amount = body.amount_cents

    # ── Validate amount (explicit 400 to honour the documented contract) ─────
    # Pydantic field constraints would surface as 422; an explicit check keeps
    # the {detail:{error,code}} shape the frontend matches on and the 400 the
    # endpoint spec promises. 0 is NOT free here (unlike /pay) — a tip of $0
    # has nothing to charge; the frontend simply skips the call.
    if amount < _STRIPE_MIN_CHARGE_CENTS or amount > _MAX_CHARGE_CENTS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Enter a tip of at least $0.50.",
                "code": "INVALID_AMOUNT",
                "min_cents": _STRIPE_MIN_CHARGE_CENTS,
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

    metadata = {"type": "tip"}
    if body.job_id and _SAFE_JOB_ID.match(body.job_id):
        metadata["job_id"] = body.job_id

    try:
        result = await provider.create_payment_intent(
            amount_cents=amount,
            metadata=metadata,
            description="LAIGO tip",
        )
    except PaymentRetryableError as exc:
        logger.warning("donate.retryable err=%s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Payment is temporarily unavailable. Please try again.",
                "code": "PAYMENT_RETRYABLE",
            },
        )
    except PaymentPermanentError as exc:
        logger.error("donate.error err=%s", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Could not start the payment. Please try again later.",
                "code": "PAYMENT_ERROR",
            },
        )

    logger.info(
        "donate.intent_created pi=%s amount=%d job_id=%s",
        result["payment_intent_id"], amount, body.job_id or "-",
    )
    return {"client_secret": result["client_secret"]}


# ─────────────────────────────────────────────────────────────────────────────
# Stripe webhook — authoritative payment recording.
#
# Mounted at the app root (NOT under /jobs). Stripe POSTs payment_intent.*
# events here; we record payment.json from the verified event. This is the
# source of truth for revenue: it catches 3DS completions and any charge whose
# synchronous /pay response was lost to a client disconnect. The signature is
# verified with STRIPE_WEBHOOK_SECRET; unverified bodies are rejected 400.
# ─────────────────────────────────────────────────────────────────────────────

webhook_router = APIRouter()


@webhook_router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        logger.error("stripe webhook hit but STRIPE_WEBHOOK_SECRET is not set")
        raise HTTPException(
            status_code=503,
            detail={"error": "Webhook not configured.",
                    "code": "WEBHOOK_NOT_CONFIGURED"},
        )

    # Raw body bytes — the signature is computed over exactly what Stripe sent.
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = construct_webhook_event(payload, sig_header, secret)
    except WebhookVerificationError as exc:
        logger.warning("webhook.verification_failed err=%s", exc)
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid signature.", "code": "INVALID_SIGNATURE"},
        )

    event_type = event.get("type")
    if event_type == "payment_intent.succeeded":
        # Defensive .get-chaining: a verified-but-oddly-shaped event must not
        # 500 (Stripe would then retry the same event forever).
        intent = (event.get("data") or {}).get("object") or {}
        metadata = intent.get("metadata") or {}
        job_id = metadata.get("job_id")
        pi_id = intent.get("id")
        amount = intent.get("amount_received") or intent.get("amount") or 0
        if job_id and _SAFE_JOB_ID.match(job_id):
            _record_payment(
                job_id, amount_cents=int(amount), status="paid",
                payment_intent_id=pi_id,
            )
            # Email the build pack for /pay charges (3DS completions and
            # charges whose sync response was lost). Gated on the metadata
            # /pay stamps at charge time: tips (/donate, type=tip) carry no
            # email and must never trigger a build-pack send. The emailer's
            # sentinel dedupes against the sync path and event redelivery.
            email = (metadata.get("email") or "").strip()
            if (email and _EMAIL_RE.match(email)
                    and metadata.get("source") == "laigo_pay"
                    and metadata.get("type") != "tip"):
                background_tasks.add_task(
                    emailer.send_build_pack_email,
                    job_id=job_id, to_email=email, amount_cents=int(amount),
                    job_dir=OUTPUT_DIR / job_id,
                )
            logger.info(
                "webhook.recorded job_id=%s pi=%s amount=%s", job_id, pi_id, amount,
            )
        else:
            logger.warning(
                "webhook.payment_intent_succeeded missing/invalid job_id "
                "metadata pi=%s metadata=%s", pi_id, metadata,
            )
    else:
        logger.debug("webhook.ignored type=%s", event_type)

    # Always 200 for a verified event so Stripe stops retrying.
    return {"received": True}
