"""Orphan-hold reconciliation (Phase E step 2).

`reconcile_orphan_holds()` runs periodically (every 5 minutes by default) and
detects divergence between our recorded `payment_holds.last_known_status` and
the provider's actual state. It catches three failure modes that the saga
alone cannot:

  1. Operator action out of band — operator captured or cancelled a hold from
     the Stripe dashboard while the saga was still in stripe_held. Without
     reconciliation the saga thinks the auth is still live.
  2. Saga crashed AFTER provider call but BEFORE state-write — `create_hold`
     succeeded at Stripe but the `payment_holds` INSERT or `sagas` UPDATE
     failed. Resume-on-startup catches the saga side; reconciliation catches
     the Stripe side.
  3. Stuck saga — saga is at `stripe_held` for hours, didn't progress, didn't
     time out (because the saga timeout watchdog only fires while the asyncio
     task is alive). Resume-on-startup catches this on restart; reconciliation
     catches it without a restart.

Decision matrix (Stripe status × persisted saga_status):

| Stripe says        | Saga says                       | Action                                              |
|--------------------|---------------------------------|-----------------------------------------------------|
| `succeeded`        | anything                        | Mirror to payment_holds; don't touch saga          |
| `canceled`         | terminal                        | Mirror to payment_holds                            |
| `canceled`         | non-terminal                    | Mirror + escalate saga to MANUAL_REVIEW            |
| `requires_capture` | NULL / clean terminal           | Orphan — cancel hold; mirror on success             |
| `requires_capture` | MANUAL_REVIEW + cancel_safe     | Cancel hold (B55 — operator was asked to do this manually; reconciler is the safety net) |
| `requires_capture` | MANUAL_REVIEW + operator_decides | **Skip** (B55 — runbook offered capture-or-refund; do not destroy operator's capture option) |
| `requires_capture` | MANUAL_REVIEW + NULL disposition | Skip (B55 — conservative default: don't touch)     |
| `requires_capture` | stripe_held, stale >1h          | Stuck — MANUAL_REVIEW the saga; leave Stripe alone |
| `requires_capture` | orders_placed stale             | Same as stuck stripe_held                          |
| `requires_capture` | in-flight (<1h)                 | Skip — saga is healthy; bump reconciled_at         |
| `requires_capture` | initiated                       | Orphan — cancel hold (saga never advanced)         |
| `unknown`          | anything                        | Mark payment_holds=unknown; stop re-querying        |

Every row decision emits a corresponding `audit.emit(...)` event for the
operator audit trail.

The outer loop NEVER raises. A per-row failure logs CRITICAL and counts;
the loop continues to the next row. A successful tick logs the count of
rows examined + outcomes histogram. Backend-gated: no-op when
`DB_BACKEND != postgres`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import audit
from . import checkout_store_dispatch as checkout_store
from . import payment_holds_store
from .models import ERROR_MESSAGES, HoldDisposition, SagaStatus
from .payment import registry as payment_registry
from .payment.base import (
    PaymentPermanentError,
    PaymentProvider,
    PaymentProviderUnavailable,
    PaymentRetryableError,
)
from ..db import is_postgres_backend

logger = logging.getLogger("laigo.reconcile")

# Module-level strong reference for the periodic task. Matches the
# `_running_sagas` (C1) and `_sweeper_task` (B24) pattern: Python 3.11+ GCs
# weakly-referenced tasks, so bare `asyncio.create_task(...)` without a
# saved handle is prone to being collected before it runs.
_reconcile_task: Optional[asyncio.Task] = None


# Saga statuses we treat as "clean terminal" for reconciliation purposes —
# the saga reached a known-good conclusion AND we never need to consult the
# operator about hold disposition. Stripe still holding funds for one of
# these is a true orphan that should be auto-cancelled.
#
# NOTE (B55): MANUAL_REVIEW is intentionally NOT in this set. It's a
# *terminal-but-pending-operator* state — the saga finished writing but the
# operator hasn't decided what to do with the hold yet. Auto-cancelling
# would destroy the operator's "capture this hold" option for the
# MANUAL_REVIEW sites whose runbook offers capture-or-refund. The dedicated
# branch in `_reconcile_one` reads `hold_disposition` to choose between
# cancel-as-safety-net and hands-off.
_CLEAN_TERMINAL_SAGA_STATUSES = frozenset({
    SagaStatus.PAYMENT_CAPTURED.value,
    SagaStatus.COMPENSATED.value,
    SagaStatus.FAILED.value,
})

# Saga statuses where Stripe still holding funds means the saga is in flight.
# When stale (>STUCK_THRESHOLD), these are "stuck" and need MANUAL_REVIEW.
_IN_FLIGHT_WITH_HOLD = frozenset({
    SagaStatus.STRIPE_HELD.value,
    SagaStatus.ORDERS_PLACED.value,
    SagaStatus.FALLBACK_ORDERED.value,
})

# How old `sagas.last_transition_at` must be for a non-terminal saga to count
# as "stuck." The saga's own timeout is 900s = 15min, so anything older than
# that means the saga's asyncio task died without writing a terminal state.
# 1 hour is a comfortable safety margin past 15min.
_STUCK_THRESHOLD = timedelta(hours=1)


async def reconcile_orphan_holds(
    *,
    older_than_seconds: int = 3600,
) -> dict:
    """Single-pass reconciliation. Called from the periodic task in lifespan.

    `older_than_seconds` controls which payment_holds rows are eligible — only
    those whose `last_reconciled_at` is older than this threshold. Default
    1 hour balances "catch stuck holds quickly" against "don't hammer Stripe
    for every recently-touched row."

    Returns a histogram dict for the periodic task's INFO log. NEVER raises —
    per-row failures are caught + counted; transient Stripe blips are not a
    reconciliation failure (just a deferred decision).

    No-op when DB_BACKEND != postgres. Reconciliation requires the postgres
    payment_holds index; the JSON-mode runtime has no equivalent.
    """
    if not is_postgres_backend():
        return {"skipped": "DB_BACKEND != postgres"}

    try:
        provider = payment_registry.get_active()
    except PaymentProviderUnavailable as exc:
        # No provider registered — gate is closed. No point asking Stripe
        # for hold statuses. The reconciler is a no-op until the gate opens.
        # Log once at WARN so operators see this if they wonder why
        # reconciliation isn't running, but don't spam — the periodic task
        # will keep firing on its schedule.
        logger.warning(
            "[reconcile] no PaymentProvider registered (%s); skipping tick", exc,
        )
        return {"skipped": "no_provider"}

    rows = await payment_holds_store.fetch_for_reconcile(
        older_than_seconds=older_than_seconds,
    )

    histogram: dict[str, int] = {
        "examined": len(rows),
        "mirrored_succeeded": 0,
        "mirrored_canceled": 0,
        "saga_escalated_oob_cancel": 0,
        "canceled_orphan": 0,
        "stuck_saga_marked_review": 0,
        "in_flight_skipped": 0,
        # B55 — counts `requires_capture` + saga MANUAL_REVIEW rows we
        # deliberately did NOT touch (operator_decides disposition OR NULL).
        "manual_review_skipped": 0,
        "stripe_unknown_marked": 0,
        "stripe_retryable_skipped": 0,
        "stripe_permanent_errored": 0,
        "per_row_failed": 0,
    }

    for row in rows:
        try:
            outcome = await _reconcile_one(row, provider)
            histogram[outcome] = histogram.get(outcome, 0) + 1
        except Exception as exc:
            # Per-row safety net: never let a single row crash the tick.
            # Per-row errors that the _reconcile_one body intentionally
            # propagates would be a programmer error — this catch makes
            # those visible as CRITICAL without blocking other rows.
            histogram["per_row_failed"] += 1
            logger.critical(
                "[reconcile] unexpected per-row failure hold_id=%s "
                "checkout_id=%s: %s: %s",
                row.get("hold_id"), row.get("checkout_id"),
                type(exc).__name__, exc, exc_info=True,
            )

    if rows:
        logger.info("[reconcile] %s", histogram)
    return histogram


async def _reconcile_one(row: dict, provider: PaymentProvider) -> str:
    """Process a single candidate row. Returns the histogram outcome key.

    The body never raises in normal operation — Stripe-transient errors are
    classified into `stripe_retryable_skipped` / `stripe_permanent_errored`
    outcomes and the row is left for the next tick (or marked 'unknown'
    permanently). Unexpected exceptions DO propagate to
    `reconcile_orphan_holds`'s safety net.
    """
    hold_id: str = row["hold_id"]
    checkout_id: Optional[str] = row.get("checkout_id")
    job_id: Optional[str] = row.get("job_id")
    saga_status: Optional[str] = row.get("saga_status")
    last_transition_at: Optional[datetime] = row.get("last_transition_at")
    # B55 — only meaningful when saga_status == MANUAL_REVIEW. NULL for
    # other states (or any pre-B55 row); the MANUAL_REVIEW branch below
    # treats NULL the same as OPERATOR_DECIDES (conservative).
    hold_disposition: Optional[str] = row.get("hold_disposition")

    # ─── 1. Ask Stripe what it thinks ───────────────────────────────────────
    try:
        stripe_status = await provider.get_hold_status(hold_id)
    except PaymentRetryableError as exc:
        # Network blip / 5xx / rate limit. Don't bump last_reconciled_at —
        # we want this row reconsidered immediately on the next tick (no
        # 1-hour cooldown for a transient issue).
        logger.warning(
            "[reconcile] %s: Stripe transient error, retry next tick: %s",
            hold_id, exc,
        )
        return "stripe_retryable_skipped"

    except PaymentPermanentError as exc:
        # "No such PaymentIntent" / auth misconfig / etc. The hold isn't
        # something Stripe can answer about. Mark payment_holds=unknown
        # so we stop re-querying every tick. Operator investigation needed.
        logger.error(
            "[reconcile] %s: Stripe permanent error: %s. Marking "
            "payment_holds.last_known_status='unknown' to stop re-querying.",
            hold_id, exc,
        )
        await payment_holds_store.mark_status(hold_id, "unknown")
        await audit.emit(
            "payment.cancelled",  # closest existing event; data.reason distinguishes
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "hold_id": hold_id,
                "reason": "reconcile.stripe_permanent_error",
                "error_class": type(exc).__name__,
            },
        )
        return "stripe_permanent_errored"

    # ─── 2. Stripe terminal states ──────────────────────────────────────────
    if stripe_status == "succeeded":
        # Out-of-band capture (operator pressed Capture in Stripe Dashboard)
        # OR a saga capture write that landed at Stripe but not in our DB.
        # Either way, the hold is captured. Mirror the truth.
        # DO NOT touch the saga — the operator may be mid-recovery from a
        # MANUAL_REVIEW state, and overwriting it now would defeat their work.
        await payment_holds_store.mark_status(hold_id, "succeeded")
        await audit.emit(
            "payment.captured",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "hold_id": hold_id,
                "captured_amount_cents": row.get("amount_authorized_cents"),
                "reason": "reconcile.observed_succeeded",
                "saga_status_at_observation": saga_status,
            },
        )
        logger.info(
            "[reconcile] %s: Stripe says succeeded (saga at %r); mirrored to payment_holds",
            hold_id, saga_status,
        )
        return "mirrored_succeeded"

    if stripe_status == "canceled":
        await payment_holds_store.mark_status(hold_id, "canceled")
        # If saga is still non-terminal, escalate to MANUAL_REVIEW —
        # something outside our control released the hold and the saga's
        # in-memory expectations are now wrong.
        if job_id is not None and saga_status in _IN_FLIGHT_WITH_HOLD:
            now_iso = datetime.now(timezone.utc).isoformat()
            reason = (
                f"Hold {hold_id} was cancelled out-of-band (observed by "
                f"reconciler at {now_iso}). Saga was still at {saga_status!r}; "
                "no further capture attempt is possible. Verify with Stripe "
                "dashboard, then either re-quote/re-charge the customer or "
                "refund any marketplace orders already placed."
            )
            await checkout_store.update(job_id, {
                "saga_status": SagaStatus.MANUAL_REVIEW.value,
                "manual_review_reason": reason,
                "error": f"Hold {hold_id} cancelled out-of-band",
                "customer_message": ERROR_MESSAGES["manual_review"],
            })
            await audit.emit(
                "saga.manual_review",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={"reason": "reconcile.oob_cancel", "hold_id": hold_id},
            )
            logger.critical(
                "[reconcile] %s: Stripe says canceled; escalated saga %s to MANUAL_REVIEW",
                hold_id, checkout_id,
            )
            return "saga_escalated_oob_cancel"

        await audit.emit(
            "payment.cancelled",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "hold_id": hold_id,
                "reason": "reconcile.observed_canceled",
                "saga_status_at_observation": saga_status,
            },
        )
        logger.info(
            "[reconcile] %s: Stripe says canceled (saga at %r); mirrored",
            hold_id, saga_status,
        )
        return "mirrored_canceled"

    if stripe_status == "unknown":
        # Provider returned a Stripe status we don't have in the map.
        # Mark payment_holds=unknown so we stop re-querying. This is
        # different from PaymentPermanentError above — the call SUCCEEDED
        # but the response was out of our enum.
        await payment_holds_store.mark_status(hold_id, "unknown")
        await audit.emit(
            "payment.cancelled",  # nearest existing event; data carries the why
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "hold_id": hold_id,
                "reason": "reconcile.unmapped_stripe_status",
            },
        )
        logger.warning(
            "[reconcile] %s: Stripe returned unmapped status; marked unknown",
            hold_id,
        )
        return "stripe_unknown_marked"

    # ─── 3. Stripe says requires_capture — orphan? stuck? in-flight? ────────
    assert stripe_status == "requires_capture", (
        f"unreachable: get_hold_status returned {stripe_status!r}"
    )

    # 3a. No saga row, OR saga is in a clean terminal state — pure orphan.
    # Stripe is holding funds for a customer whose order is either
    # non-existent or completed/failed/compensated. Cancel the hold.
    if saga_status is None or saga_status in _CLEAN_TERMINAL_SAGA_STATUSES:
        return await _cancel_orphan_hold(
            hold_id=hold_id,
            checkout_id=checkout_id,
            job_id=job_id,
            saga_status=saga_status,
            provider=provider,
            reason_code=(
                "reconcile.orphan_no_saga" if saga_status is None
                else "reconcile.orphan_terminal_saga"
            ),
        )

    # 3a'. MANUAL_REVIEW — operator-pending terminal. The hold's disposition
    # decides whether the reconciler may cancel as a safety net (cancel_safe:
    # runbook said "cancel manually") or must stay hands-off (operator_decides
    # / NULL: runbook offered capture-or-refund, OR pre-B55 row of unknown
    # intent). See B55 in PRE_RELEASE_PAYMENT_CHECKLIST.md §4.1.
    if saga_status == SagaStatus.MANUAL_REVIEW.value:
        if hold_disposition == HoldDisposition.CANCEL_SAFE.value:
            return await _cancel_orphan_hold(
                hold_id=hold_id,
                checkout_id=checkout_id,
                job_id=job_id,
                saga_status=saga_status,
                provider=provider,
                reason_code="reconcile.manual_review_cancel_safe",
            )
        # OPERATOR_DECIDES, NULL, or any unknown value: leave Stripe alone.
        # Bump last_reconciled_at to space alerts (1-hour cooldown) so we don't
        # re-pick this row every tick while the operator is still working it.
        await payment_holds_store.mark_status(hold_id, "requires_capture")
        logger.info(
            "[reconcile] %s: saga %s at MANUAL_REVIEW with disposition=%r — "
            "skipping (operator decides hold fate)",
            hold_id, checkout_id, hold_disposition,
        )
        return "manual_review_skipped"

    # 3b. Saga is "initiated" — saga never wrote the hold_id but a
    # payment_holds row exists with this hold_id. This means create_hold
    # succeeded and record_hold succeeded but the sagas UPDATE didn't.
    # Treat as orphan — cancel the hold; saga will be FAILED on next
    # restart-recovery anyway.
    if saga_status == SagaStatus.INITIATED.value:
        return await _cancel_orphan_hold(
            hold_id=hold_id,
            checkout_id=checkout_id,
            job_id=job_id,
            saga_status=saga_status,
            provider=provider,
            reason_code="reconcile.orphan_saga_initiated",
        )

    # 3c. Saga is in `_IN_FLIGHT_WITH_HOLD` — check whether stuck or healthy.
    if saga_status in _IN_FLIGHT_WITH_HOLD:
        is_stale = (
            last_transition_at is not None and
            datetime.now(timezone.utc) - last_transition_at > _STUCK_THRESHOLD
        )

        if is_stale:
            # Saga's been at this status for >1hr. Saga timeout is 15min,
            # so this means the asyncio task died without writing a
            # terminal state AND restart-recovery didn't fire (or this
            # IS post-restart-recovery, in which case something went very
            # wrong). MANUAL_REVIEW with a verbose runbook.
            #
            # DO NOT cancel the Stripe hold from here — the operator may
            # be in the middle of recovering it. Touching Stripe could
            # collide with their manual action. The hold's `last_known_status`
            # stays `requires_capture` so reconciliation keeps re-noticing
            # this row, but `mark_status` below bumps `last_reconciled_at`
            # so we don't keep selecting it every minute (1-hour cooldown
            # gives operator time to work without alert spam).
            now_iso = datetime.now(timezone.utc).isoformat()
            stale_for = datetime.now(timezone.utc) - last_transition_at
            reason = (
                f"Saga has been at {saga_status!r} for {stale_for}, last "
                f"transition at {last_transition_at.isoformat()}. The saga's "
                f"asyncio task must have died without writing a terminal "
                f"state. The Stripe hold {hold_id} is still authorized — DO "
                f"NOT cancel it here without operator confirmation; the "
                f"resume-on-startup routine should have caught this on the "
                f"last server boot. Investigate why it didn't. Reconciler "
                f"noticed at {now_iso}."
            )
            await checkout_store.update(job_id, {
                "saga_status": SagaStatus.MANUAL_REVIEW.value,
                "manual_review_reason": reason,
                "error": "Reconciler detected stuck saga",
                "customer_message": ERROR_MESSAGES["manual_review"],
            })
            # Bump reconciled_at via a same-status mark so this row exits
            # the candidate set for the next hour.
            await payment_holds_store.mark_status(hold_id, "requires_capture")
            await audit.emit(
                "saga.manual_review",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={
                    "reason": "reconcile.stuck_saga",
                    "saga_status_at_observation": saga_status,
                    "stale_for_seconds": int(stale_for.total_seconds()),
                    "hold_id": hold_id,
                },
            )
            logger.critical(
                "[reconcile] %s: stuck saga %s at %r for %s — escalated",
                hold_id, checkout_id, saga_status, stale_for,
            )
            return "stuck_saga_marked_review"

        # In-flight — saga is healthy. Bump reconciled_at so we don't pick
        # this row again for another hour. The saga will reach a terminal
        # state on its own.
        await payment_holds_store.mark_status(hold_id, "requires_capture")
        logger.info(
            "[reconcile] %s: saga %s in-flight at %r (last txn %s); skipping",
            hold_id, checkout_id, saga_status, last_transition_at,
        )
        return "in_flight_skipped"

    # 3d. Unreachable — `_IN_FLIGHT_WITH_HOLD`, `_CLEAN_TERMINAL_SAGA_STATUSES`,
    # MANUAL_REVIEW, INITIATED, and None together cover every value in the
    # schema CHECK constraint. If a new SagaStatus is added without updating
    # this function, we hit this branch. Defensive log + bump reconciled_at.
    logger.error(
        "[reconcile] %s: unknown saga_status %r — defensive skip. "
        "Update _reconcile_one's branches in scripts/checkout/reconcile.py.",
        hold_id, saga_status,
    )
    await payment_holds_store.mark_status(hold_id, "requires_capture")
    return "in_flight_skipped"  # closest-fitting outcome


async def _cancel_orphan_hold(
    *,
    hold_id: str,
    checkout_id: Optional[str],
    job_id: Optional[str],
    saga_status: Optional[str],
    provider: PaymentProvider,
    reason_code: str,
) -> str:
    """Call provider.cancel on an orphan hold; mirror state on success.

    Used by the "Stripe says requires_capture but saga doesn't need it"
    branches: no saga row, saga terminal, saga initiated. All three resolve
    to "cancel the hold, release the customer's auth."

    Cancel failure here does NOT escalate to MANUAL_REVIEW — the hold WILL
    auto-expire at Stripe (default 7 days for cards), and the reconciler
    will retry every tick until success. Logging is enough.
    """
    try:
        await provider.cancel(
            hold_id=hold_id,
            idempotency_key=f"reconcile-cancel-{hold_id}",
        )
    except Exception as exc:
        # Same row will be re-picked on next tick (bump reconciled_at to
        # space attempts an hour apart, not every tick).
        await payment_holds_store.mark_status(hold_id, "requires_capture")
        logger.error(
            "[reconcile] %s: orphan cancel failed (%s); will retry next tick",
            hold_id, exc, exc_info=True,
        )
        return "stripe_retryable_skipped"

    # Success — mirror to payment_holds and emit audit.
    await payment_holds_store.mark_status(hold_id, "canceled")
    await audit.emit(
        "payment.cancelled",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "hold_id": hold_id,
            "reason": reason_code,
            "saga_status_at_observation": saga_status,
        },
    )
    logger.info(
        "[reconcile] %s: orphan cancelled (saga at %r, reason=%s)",
        hold_id, saga_status, reason_code,
    )
    return "canceled_orphan"


# ─── Periodic task lifecycle ────────────────────────────────────────────────


def _interval_seconds() -> int:
    """Reconcile cadence, configurable via env. Default 5 minutes.

    Lower bound 60s — anything faster would hammer Stripe for marginal
    benefit and risk rate-limit pressure. No upper bound today; operators
    can run hourly or daily if traffic is low.
    """
    try:
        v = int(os.environ.get("RECONCILE_INTERVAL_SECONDS", "300"))
    except ValueError:
        logger.warning(
            "RECONCILE_INTERVAL_SECONDS is not a valid integer; "
            "falling back to 300s default."
        )
        return 300
    return max(60, v)


async def _reconcile_loop() -> None:
    """The periodic-task body. Runs until cancelled.

    Sleeps FIRST, then reconciles — so a fresh-boot tick doesn't race the
    saga-resume routine that already ran during lifespan startup. The first
    productive tick fires roughly `RECONCILE_INTERVAL_SECONDS` after boot.

    Per-tick errors are caught inside `reconcile_orphan_holds` (it never
    raises). The `except Exception` here is belt-and-suspenders against
    a future regression that lets something propagate.

    On `CancelledError`, exit cleanly so shutdown completes promptly.
    """
    interval = _interval_seconds()
    logger.info("[reconcile] periodic task started (interval=%ds)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[reconcile] periodic task cancelled; exiting")
            raise
        try:
            await reconcile_orphan_holds()
        except asyncio.CancelledError:
            logger.info("[reconcile] periodic task cancelled mid-tick; exiting")
            raise
        except Exception as exc:
            # reconcile_orphan_holds() shouldn't raise (per-row safety net
            # already covers each row), but if a future bug lets something
            # through, we want the loop to survive.
            logger.critical(
                "[reconcile] tick raised unexpectedly: %s: %s",
                type(exc).__name__, exc, exc_info=True,
            )


def start_reconcile_task() -> None:
    """Spawn the periodic task. Idempotent. No-op when DB_BACKEND != postgres.

    Holds a strong reference at module scope (see `_reconcile_task` global)
    to prevent Python 3.11+ event-loop GC of weakly-referenced tasks.
    Re-calling skips the spawn if a non-done task already exists.

    Must be called from inside the FastAPI lifespan (or any async context
    where the event loop is running). Calling from a non-async thread
    raises `RuntimeError` from `asyncio.get_running_loop()`.
    """
    global _reconcile_task
    if not is_postgres_backend():
        logger.info("[reconcile] periodic task skipped (DB_BACKEND != postgres)")
        return
    if _reconcile_task is not None and not _reconcile_task.done():
        logger.debug("[reconcile] periodic task already running; skip")
        return
    _reconcile_task = asyncio.create_task(_reconcile_loop())


async def stop_reconcile_task(*, timeout: float = 5.0) -> None:
    """Cancel + await the periodic task. Idempotent. Called from shutdown.

    Cancels the task and awaits it to exit. A 5-second timeout protects
    against a wedged tick — after which we log + give up + leave it as a
    daemon (process exit will kill it anyway).

    Safe to call when no task was started (no-op).
    """
    global _reconcile_task
    task = _reconcile_task
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.CancelledError:
        # Expected — task exits via the cancel path.
        pass
    except asyncio.TimeoutError:
        logger.warning(
            "[reconcile] periodic task did not exit within %.1fs of cancel; "
            "leaving as daemon (process exit will kill it)", timeout,
        )
    except Exception as exc:
        logger.error(
            "[reconcile] unexpected error during task cancellation: %s",
            exc, exc_info=True,
        )
    _reconcile_task = None


def _reset_for_tests() -> None:
    """Test helper. Drops the module-level task handle without cancelling.

    Tests that call `start_reconcile_task()` repeatedly OR want to assert
    a clean baseline should invoke this between scenarios.
    """
    global _reconcile_task
    _reconcile_task = None
