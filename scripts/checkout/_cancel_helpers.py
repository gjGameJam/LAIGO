"""Shared retry helper for `provider.cancel(hold_id, ...)` calls.

Pulled out of `saga.py` (B56/B57) so the same retry policy applies wherever a
saga code path releases a Stripe hold:

  - `saga._compensate` — Phase 3 of compensation (canceled here pre-extraction)
  - `saga.execute_checkout_saga` — record_hold-failure rollback (B56)
  - `saga_resume._recover_stripe_held` — boot recovery (B57)

Retry policy
============
1-3 retries on `PaymentRetryableError` only (1s / 4s / 16s backoff — same
budget as `_capture_with_retry`). `PaymentPermanentError` fails immediately.
Unexpected non-provider exceptions fail immediately with stack trace in the
log. Total worst case ~21s, sized to recover transient network blips without
delaying MANUAL_REVIEW escalation on persistent failures.

Idempotency
===========
Stable `f"cancel-{checkout_id}"` key across all attempts. Stripe's 24h
idempotency cache returns the original response on retry, so a transient
network error on the first attempt followed by a successful retry is safe
— the second call hits the cache, not a fresh cancel API call.

Side effects on success
=======================
- `audit.emit("payment.cancelled", ..., data={"hold_id", "reason": audit_reason})`
- `payment_holds_store.mark_status(hold_id, "canceled")` (best-effort; logs
  on failure so the reconciler can catch up).

Side effects on failure
=======================
None. The caller decides whether to escalate to MANUAL_REVIEW, emit a
`payment.hold_orphan` audit event (B56's unrecoverable case), or take other
action based on the returned `(False, error_message)` tuple.

Cross-cutting concerns
======================
- Audit emit MUST NOT raise (audit module's contract). If it does, this
  helper still returns True and the success log line still fires.
- payment_holds_store.mark_status failures are logged and swallowed —
  reconciliation is the safety net.

NEVER swallows GateClosedError (provider.cancel should never raise it; if it
ever does, propagating respects the gate doctrine in gate.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from . import audit
from . import payment_holds_store
from .payment.base import PaymentPermanentError, PaymentRetryableError

logger = logging.getLogger("laigo")

# Backoff sequence between cancel retries. Matches `_capture_with_retry`'s
# `_CAPTURE_BACKOFFS_SECONDS` so cancellation and capture share a recovery
# budget — easier to reason about end-to-end saga timing.
_CANCEL_BACKOFFS_SECONDS: tuple[int, ...] = (1, 4, 16)


async def cancel_hold_with_retry(
    *,
    provider,
    checkout_id: str,
    hold_id: str,
    audit_reason: str = "compensation",
    audit_subject: Optional[dict] = None,
) -> tuple[bool, str | None]:
    """Cancel a hold with bounded retries on transient errors only.

    Args:
        provider: PaymentProvider instance with an async `.cancel(hold_id,
            idempotency_key)` method.
        checkout_id: used to derive the stable idempotency key
            `f"cancel-{checkout_id}"`. The same key is reused on every retry
            so Stripe's idempotency cache short-circuits duplicate calls.
        hold_id: the Stripe PaymentIntent ID (or future-provider equivalent).
        audit_reason: written to `data.reason` in the `payment.cancelled`
            audit event on success. Conventional values:
              - "compensation" (saga._compensate Phase 3, the default)
              - "record_hold_failed_rollback" (B56 — saga's record_hold
                rollback path)
              - "resume.restart" (B57 — saga_resume._recover_stripe_held)
        audit_subject: dict overriding the default `{"checkout_id": ...}`.
            Pass `{"job_id": ..., "checkout_id": ...}` when the caller has
            job_id in scope so operator dashboards can correlate.

    Returns:
        (True, None) on successful cancel.
        (False, error_message) on permanent failure OR exhausted retries.
        The caller decides the next step (MANUAL_REVIEW write, orphan audit,
        etc.). This helper does NOT touch saga state.
    """
    idempotency_key = f"cancel-{checkout_id}"
    max_attempts = len(_CANCEL_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            await provider.cancel(hold_id=hold_id, idempotency_key=idempotency_key)
            if attempt > 0:
                logger.info(
                    f"[cancel_helper] cancel succeeded for hold {hold_id} "
                    f"on attempt {attempt + 1}"
                )

            # L6 audit: provider-level success. NEVER raises (audit module's
            # contract); if it logs CRITICAL on internal failure, this helper
            # still reports success because the cancel itself succeeded.
            subject = audit_subject if audit_subject is not None else {"checkout_id": checkout_id}
            await audit.emit(
                "payment.cancelled",
                subject=subject,
                data={"hold_id": hold_id, "reason": audit_reason},
            )

            # Mirror the terminal Stripe state onto payment_holds so the
            # reconciler doesn't ask Stripe again. Best-effort — if the
            # row doesn't exist (e.g. record_hold-failure rollback path
            # where the INSERT never happened) this is a no-op UPDATE.
            try:
                await payment_holds_store.mark_status(hold_id, "canceled")
            except Exception as exc:
                logger.error(
                    f"[cancel_helper] payment_holds.mark_status(canceled) "
                    f"failed for hold {hold_id}: {exc}. Reconciler will catch up.",
                    exc_info=True,
                )
            return True, None

        except PaymentRetryableError as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                wait_s = _CANCEL_BACKOFFS_SECONDS[attempt]
                logger.warning(
                    f"[cancel_helper] cancel attempt {attempt + 1} transient "
                    f"failure for hold {hold_id}: {exc}. Retrying in {wait_s}s."
                )
                await asyncio.sleep(wait_s)
                continue
            break  # exhausted retries

        except PaymentPermanentError as exc:
            logger.error(
                f"[cancel_helper] cancel permanent failure for hold {hold_id}: "
                f"{exc}. Skipping remaining retries."
            )
            return False, f"permanent: {exc}"

        except Exception as exc:
            logger.error(
                f"[cancel_helper] cancel unexpected exception for hold {hold_id}",
                exc_info=True,
            )
            return False, f"unexpected: {exc}"

    return False, f"exhausted {max_attempts} attempts: {last_error}"
