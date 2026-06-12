"""
Customer transactional email (Workstream D of docs/CHECKOUT_COMPLETION_PLAN.md).

Provider: Resend (https://resend.com). Called over plain HTTPS via httpx — no
SDK dependency. Decision locked 2026-06-11.

Two hard contracts (mirroring audit.py):

  1. NEVER raises. A failed email must not break checkout. Every failure path
     logs "[email] FAILED" at CRITICAL and returns. Callers (saga, router,
     reconciler) await these freely without try/except.

  2. No-op when unconfigured. If RESEND_API_KEY or EMAIL_FROM is unset (local
     dev, or prod before DNS/DKIM is set up), every send logs at INFO and
     returns without calling the network. This lets the saga run end-to-end in
     environments that haven't wired email yet.

Idempotency: the public send_* helpers route through _emit_once(job_id,
event_key, payload), which records the event key in sagas.emails_sent (JSONB,
migration 0004) and skips a resend if the key is already present. This makes
saga retries / reconciler passes safe against double-sends.

Customer-facing copy: for error states (manual review / not charged) the
message text comes from the saga's `customer_message` (sourced from
models.ERROR_MESSAGES) and is passed in — this module does NOT invent new
customer-facing error wording (coordinated-change rule, CLAUDE.md).

Env:
  RESEND_API_KEY  (.env.secrets)  — Resend API key. Unset => email disabled.
  EMAIL_FROM      (.env)          — verified sender, e.g. "LAIGO <orders@laigo.app>".
  EMAIL_REPLY_TO  (.env, optional)— reply-to header.
"""

import base64
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

from . import checkout_store_dispatch as checkout_store

logger = logging.getLogger("laigo.notifications")

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10

# Event keys recorded in sagas.emails_sent for idempotency. Stable strings —
# changing one would let a previously-sent email re-fire once.
EVENT_ORDER_CONFIRMATION = "order_confirmation"
EVENT_KIT_ON_THE_WAY     = "kit_on_the_way"
EVENT_MANUAL_REVIEW      = "manual_review_notice"
EVENT_NOT_CHARGED        = "not_charged_notice"
EVENT_REFUND             = "refund_notice"


# ── Config helpers ───────────────────────────────────────────────────────────

def _api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


def _from_addr() -> str:
    return os.environ.get("EMAIL_FROM", "").strip()


def is_enabled() -> bool:
    """True when both the API key and a from-address are configured."""
    return bool(_api_key() and _from_addr())


# ── Low-level send ───────────────────────────────────────────────────────────

async def _post(payload: dict) -> bool:
    """POST a built payload to Resend. Returns True on 2xx, else False.

    Never raises. No-op (returns False) when email is disabled.
    """
    subject = payload.get("subject")
    if not is_enabled():
        logger.info("[email] disabled (RESEND_API_KEY/EMAIL_FROM unset) — "
                    "skipping send subject=%r", subject)
        return False
    if not payload.get("to") or not payload["to"][0]:
        logger.error("[email] FAILED — empty recipient for subject=%r", subject)
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _RESEND_URL,
                headers={
                    "Authorization": f"Bearer {_api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code // 100 == 2:
            logger.info("[email] sent subject=%r to=%s", subject, payload["to"][0])
            return True
        logger.critical(
            "[email] FAILED status=%s subject=%r body=%s",
            resp.status_code, subject, resp.text[:500],
        )
        return False
    except Exception as exc:  # network, DNS, timeout, anything
        logger.critical("[email] FAILED subject=%r: %s", subject, exc)
        return False


def _build_payload(
    to_email: str,
    subject: str,
    html: str,
    *,
    attachments: Optional[list[dict]] = None,
) -> dict:
    payload: dict = {
        "from": _from_addr(),
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    reply_to = os.environ.get("EMAIL_REPLY_TO", "").strip()
    if reply_to:
        payload["reply_to"] = reply_to
    if attachments:
        payload["attachments"] = attachments
    return payload


def _pdf_attachment(pdf_path) -> Optional[dict]:
    """Read a PDF and return a Resend attachment dict, or None if unavailable.

    Never raises — a missing/unreadable PDF degrades to sending the email
    without the attachment (the customer still gets the "on the way" notice;
    operator sees the ERROR log to follow up).
    """
    try:
        p = Path(pdf_path)
        if not p.is_file():
            logger.error("[email] instructions PDF missing at %s — "
                         "sending without attachment", p)
            return None
        content = base64.b64encode(p.read_bytes()).decode("ascii")
        return {"filename": "instructions.pdf", "content": content}
    except Exception as exc:
        logger.error("[email] failed reading PDF %s: %s — sending without "
                     "attachment", pdf_path, exc)
        return None


# ── Idempotent dispatch ──────────────────────────────────────────────────────

async def _emit_once(job_id: str, event_key: str, payload: dict) -> None:
    """Send `payload` unless `event_key` is already in sagas.emails_sent.

    On a successful send, append the key to emails_sent so a saga retry or a
    reconciler pass won't re-send. Never raises.
    """
    try:
        state = await checkout_store.load(job_id)
    except Exception as exc:
        # Can't read the ledger — we still attempt the send (better a rare
        # duplicate than a silently-dropped order email), but we cannot record
        # it, so a retry may duplicate. Log loudly.
        logger.critical("[email] FAILED to load emails_sent ledger job=%s "
                        "event=%s: %s", job_id, event_key, exc)
        state = None

    sent = list((state or {}).get("emails_sent") or [])
    if event_key in sent:
        logger.info("[email] %s already sent for job=%s — skipping", event_key, job_id)
        return

    ok = await _post(payload)
    if not ok:
        return  # _post already logged; leave the ledger untouched so a retry can resend

    try:
        await checkout_store.update(job_id, {"emails_sent": sent + [event_key]})
    except Exception as exc:
        # The email WENT OUT but we couldn't record it. A subsequent retry may
        # duplicate this one email. Acceptable failure mode; log for operators.
        logger.critical("[email] sent but FAILED to record %s for job=%s: %s",
                        event_key, job_id, exc)


# ── Public senders ───────────────────────────────────────────────────────────

async def send_order_confirmation(
    job_id: str,
    to_email: str,
    *,
    customer_total_cents: Optional[int],
    checkout_id: str,
) -> None:
    """Fired when the saga starts (status INITIATED): 'we received your order'."""
    total = f"${customer_total_cents / 100:,.2f}" if customer_total_cents else "your order total"
    html = (
        "<p>Thanks for your order with LAIGO!</p>"
        f"<p>We've received your order and are placing it now. "
        f"Your total is <strong>{total}</strong>.</p>"
        "<p>We'll email you again as soon as your bricks are on the way, "
        "along with your building instructions.</p>"
        f"<p style='color:#888;font-size:12px'>Order reference: {checkout_id}</p>"
    )
    await _emit_once(
        job_id, EVENT_ORDER_CONFIRMATION,
        _build_payload(to_email, "We received your LAIGO order", html),
    )


async def send_kit_on_the_way(
    job_id: str,
    to_email: str,
    *,
    lego_order_id: Optional[str],
    instructions_pdf_path,
) -> None:
    """Fired on PAYMENT_CAPTURED: bricks shipping + instructions PDF attached."""
    order_line = (
        f"<p>Your bricks are shipping from LEGO (order <strong>{lego_order_id}</strong>).</p>"
        if lego_order_id else
        "<p>Your bricks are on the way from LEGO.</p>"
    )
    html = (
        "<p>Great news — your LAIGO kit is on the way!</p>"
        + order_line +
        "<p>LEGO ships the loose bricks directly to you. Your step-by-step "
        "building instructions are attached to this email as a PDF — keep it "
        "handy while you assemble your mosaic.</p>"
        "<p>Happy building!</p>"
    )
    att = _pdf_attachment(instructions_pdf_path)
    await _emit_once(
        job_id, EVENT_KIT_ON_THE_WAY,
        _build_payload(
            to_email,
            "Your LAIGO kit is on the way — building instructions attached",
            html,
            attachments=[att] if att else None,
        ),
    )


async def send_manual_review_notice(
    job_id: str,
    to_email: str,
    *,
    customer_message: Optional[str],
) -> None:
    """Fired on MANUAL_REVIEW: soft-delay notice. `customer_message` from ERROR_MESSAGES."""
    msg = customer_message or "Your order is being reviewed by our team."
    html = (
        f"<p>{msg}</p>"
        "<p>We'll follow up within 24 hours — no action is needed from you.</p>"
    )
    await _emit_once(
        job_id, EVENT_MANUAL_REVIEW,
        _build_payload(to_email, "An update on your LAIGO order", html),
    )


async def send_not_charged_notice(
    job_id: str,
    to_email: str,
    *,
    customer_message: Optional[str],
) -> None:
    """Fired on COMPENSATED / FAILED: order didn't complete; not charged."""
    msg = customer_message or "We couldn't complete your order."
    html = (
        f"<p>{msg}</p>"
        "<p>You have <strong>not</strong> been charged. You're welcome to try "
        "again from your mosaic page.</p>"
    )
    await _emit_once(
        job_id, EVENT_NOT_CHARGED,
        _build_payload(to_email, "Your LAIGO order could not be completed", html),
    )


async def send_refund_notice(
    job_id: str,
    to_email: str,
    *,
    amount_cents: Optional[int],
    reason: Optional[str] = None,
) -> None:
    """Fired when a hold/charge is refunded (reconciler or operator path)."""
    amount = f"${amount_cents / 100:,.2f}" if amount_cents else "your payment"
    reason_line = f"<p>{reason}</p>" if reason else ""
    html = (
        f"<p>We've refunded {amount} to your original payment method.</p>"
        + reason_line +
        "<p>Refunds typically take 5–10 business days to appear, depending on "
        "your bank.</p>"
    )
    await _emit_once(
        job_id, EVENT_REFUND,
        _build_payload(to_email, "Your LAIGO refund", html),
    )
