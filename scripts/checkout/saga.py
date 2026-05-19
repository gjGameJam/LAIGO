"""
Saga orchestrator for the checkout pipeline.

State machine:
  initiated → stripe_held → orders_placed → payment_captured
  Any step can transition to → failed (with compensation attempted)
  Capture failures with orders already placed → manual_review (no compensation;
      orders stay; operator decides recovery)

Checkpoints are written to outputs/{job_id}/checkout_state.json after each step
so status polling always reflects the latest progress. If the server crashes
mid-saga, the operator can read the state file to determine what compensation
is needed (see docs/ORDER_OPTIMIZER.md → Crash Recovery).

Routing logic:
  seller_id="lego_official"  → lego_client.order_from_lego (Playwright)
  seller_id="brickowl_*"     → brickowl_client.create_order (pending API access)
  seller_id="bricklink_*"    → bricklink_client (pending setup)

LOCK NOTE: checkout_store.update() holds an asyncio.Lock internally. Never call
checkout_store.load() or checkout_store.update() while already inside an
update() call — asyncio.Lock is NOT reentrant and will deadlock.

PAYMENT NOTE (L5): all Stripe interaction goes through the PaymentProvider
registered in scripts/checkout/payment/registry.py. The Saga does not import
stripe directly. Error classification (transient vs permanent) is the
provider's responsibility; the Saga's job is the retry-and-escalate policy.

TIMEOUT POLICY (B4): the entire Saga is wrapped in a single asyncio.wait_for()
with deadline SAGA_TIMEOUT_SECONDS (default 900s = 15 min). This is the ONLY
authoritative ceiling on Saga lifetime. On expiry, the inner coroutine is
cancelled and _handle_saga_timeout() inspects the checkpointed state to
classify recovery:

  - no payment_hold_id yet → FAILED (no money moved)
  - hold exists, no orders placed → attempt provider.cancel(); FAILED on
        success, MANUAL_REVIEW if the cancel itself fails
  - any orders placed → MANUAL_REVIEW (capture state unknowable from here;
        operator inspects Stripe + marketplace dashboards)

DO NOT add per-call timeouts on top of this. Stripe SDK has its own
request_timeout. Playwright has its own navigation/action timeouts. Nesting
asyncio.wait_for() at intermediate awaits produces timeout-layering chaos
where the inner timeout fires, the outer thinks the operation succeeded,
and state diverges from reality. The single Saga-level deadline is the
contract; the per-component timeouts are implementation details.
"""

import asyncio
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import AllocationResult, ERROR_MESSAGES, SagaStatus, StockoutError
from . import audit
from . import checkout_store_dispatch as checkout_store
from . import payment_holds_store
from .clients import lego_client, brickowl_client, bricklink_client
from .gate import require_open, compute_decision, GateClosedError
from .payment import registry as payment_registry
from .payment.base import (
    PaymentHold,
    PaymentPermanentError,
    PaymentProviderUnavailable,
    PaymentRetryableError,
)

logger = logging.getLogger("laigo")

_LEGO_SELLER_ID = lego_client.SELLER_ID

# Authorization buffer applied to the quote total when creating the Stripe
# hold. Captures the difference between quote-time pricing and the actual
# allocated total (which may shift slightly after a stockout-driven
# re-optimization). 1.05 = 5% — the value agreed in CHECKOUT_AUDIT.md §9
# "Resolved 2026-05-15".
#
# Stripe permits capturing LESS than the authorized amount; the unused
# authorization decays automatically without charging the customer. So a
# 5% buffer means LAIGO can absorb up to 5% upward drift without prompting
# the customer to re-confirm a new price. Drift beyond 5% deliberately
# fails-closed at capture time (the capture call is for the actual total,
# which would exceed authorized) — that path escalates to MANUAL_REVIEW.
_HOLD_BUFFER_MULTIPLIER: float = 1.05

# Capture retry budget. Stripe capture is idempotent for the same
# PaymentIntent ID, and stripe-python's idempotency-key cache covers
# transient network failures within 24h. Three attempts with exponential
# backoff covers the common transient cases (network blip, brief rate
# limit, single Stripe 5xx) without delaying MANUAL_REVIEW escalation
# unreasonably on persistent failures.
#
# DO NOT increase these without thinking carefully: each attempt holds an
# asyncio coroutine open, which keeps the running-sagas registry full and
# delays the customer's status response. 21s total worst case (1+4+16) is
# already significant.
_CAPTURE_BACKOFFS_SECONDS: tuple[int, ...] = (1, 4, 16)

# B16/H10: bounded concurrency for BrickOwl cancellation calls. When the real
# Playwright-based BrickOwl cancel ships (roadmap #5), each session is
# ~10-30s; sequential cancels at production order sizes (~50 sellers) would
# take 15-30 minutes — long enough for the customer's polling UI to give up.
# 5 concurrent is conservative: BrickOwl's standard rate limit is 600 req/min,
# but the more binding constraint is concurrent Playwright sessions on a
# shared LAIGO buyer account. Bump only after live testing.
_BRICKOWL_CANCEL_CONCURRENCY: int = 5


async def _parallel_brickowl_cancels(
    order_ids: list[str],
) -> list[tuple[str, str | None]]:
    """Cancel BrickOwl orders in parallel with bounded concurrency.

    Returns a list of (order_id, error_or_None) preserving the input order's
    presence (NOT necessarily the temporal completion order). On exception,
    `error_or_None` is the exception's stringified message; on success it is
    None.

    Used by both BrickOwl-cancel sites:
      1. _compensate Phase 1 (full rollback)
      2. Stockout-retry inline cancel inside _execute_checkout_saga_inner

    Callers MUST inspect the result list and decide policy. _compensate folds
    them into a _CompensationOutcome; the stockout-retry path treats ANY
    non-None error as a fatal MANUAL_REVIEW signal.
    """
    if not order_ids:
        return []

    sem = asyncio.Semaphore(_BRICKOWL_CANCEL_CONCURRENCY)

    async def _cancel_one(oid: str) -> tuple[str, str | None]:
        async with sem:
            try:
                await brickowl_client.cancel_order(oid)
                return (oid, None)
            except Exception as exc:
                return (oid, str(exc))

    return await asyncio.gather(*[_cancel_one(o) for o in order_ids])


# B4 — single authoritative Saga timeout.
#
# Sized to cover the legitimate worst case path comfortably:
#   - Stripe hold:                     ~5s
#   - 2 stockout retries × (BrickOwl orders + re-fetch + re-optimize): ~80s
#   - LEGO.com Playwright session:     ~120s
#   - Capture retries (1+4+16 worst):  ~21s
#   - Network / GC / event-loop slack: ~remainder
# Total budget 900s = 15 min. Configurable via env for staging tests; the
# default is the production value.
#
# This is the ONLY timeout that wraps Saga work. Per-call timeouts live in
# the providers' own SDKs and must NOT be layered on top of this. See the
# module docstring "TIMEOUT POLICY (B4)" section.
_SAGA_TIMEOUT_SECONDS: int = int(os.environ.get("SAGA_TIMEOUT_SECONDS", "900"))

# Saga statuses that are already terminal. If a timeout fires after the inner
# saga has finalized to one of these, the timeout handler is a no-op — the
# saga genuinely completed before the deadline; the timeout race lost.
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    SagaStatus.PAYMENT_CAPTURED.value,
    SagaStatus.FAILED.value,
    SagaStatus.COMPENSATED.value,
    SagaStatus.MANUAL_REVIEW.value,
})


# ─────────────────────────────────────────────────────────────────────────────
# Compensation
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _CompensationOutcome:
    """In-memory record of what `_compensate` accomplished.

    Built up as each cancel is attempted, then inspected once at the end to
    decide between COMPENSATED (clean rollback) and MANUAL_REVIEW (real money
    or inventory stranded; operator action required).
    """

    # State-load failure produces an empty state dict; we can't safely
    # cancel anything because we don't know what was placed. Always
    # MANUAL_REVIEW.
    state_load_error: str | None = None

    # BrickOwl orders we successfully cancelled and those we failed to
    # cancel (with the exception text).
    brickowl_succeeded: list[str] = field(default_factory=list)
    brickowl_failed: list[tuple[str, str]] = field(default_factory=list)

    # LEGO.com has no API cancel. Any LEGO order present means MANUAL_REVIEW.
    lego_uncancellable: str | None = None

    # Stripe cancel outcome.
    stripe_succeeded: bool = False
    stripe_failed: bool = False
    # When stripe was not attempted, why. None when stripe_attempted is True.
    # Values: "no_hold" (nothing to cancel), "missing_checkout_id" (state
    # corrupt / cannot construct stable idempotency key — MANUAL_REVIEW),
    # "provider_unavailable" (registry empty mid-saga — MANUAL_REVIEW).
    stripe_skip_reason: str | None = None
    stripe_error: str | None = None

    def needs_manual_review(self) -> bool:
        """True iff compensation left real money or inventory stranded."""
        return (
            self.state_load_error is not None
            or bool(self.brickowl_failed)
            or self.lego_uncancellable is not None
            or self.stripe_failed
            or self.stripe_skip_reason in ("missing_checkout_id", "provider_unavailable")
        )


async def _cancel_hold_with_retry(
    *,
    provider,
    checkout_id: str,
    hold_id: str,
) -> tuple[bool, str | None]:
    """Cancel a payment hold with bounded retries on transient errors only.

    Mirrors `_capture_with_retry`'s policy:
      - PaymentRetryableError → retry with `_CAPTURE_BACKOFFS_SECONDS` backoff
      - PaymentPermanentError → fail immediately (won't recover)
      - Unexpected Exception → fail immediately, log with stack trace

    Returns (True, None) on success, (False, error_message) on permanent
    failure or exhausted retries.

    Idempotency: stable f"cancel-{checkout_id}" across all attempts. Stripe's
    24h idempotency cache returns the original response on retry, so a
    transient network error on the first attempt followed by a successful
    retry is safe — the second call hits the cache, not a fresh cancel.

    Same retry budget as capture (1s/4s/16s, ~21s worst case). Compensation
    is already a degraded path; we want either a recovered-by-retry success
    or a fast escalation to MANUAL_REVIEW, not an unbounded loop.
    """
    idempotency_key = f"cancel-{checkout_id}"
    max_attempts = len(_CAPTURE_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            await provider.cancel(hold_id=hold_id, idempotency_key=idempotency_key)
            if attempt > 0:
                logger.info(
                    f"[saga] cancel succeeded for hold {hold_id} on attempt {attempt + 1}"
                )
            # L6: payment.cancelled — provider-level success. Subject carries
            # only checkout_id (job_id is not in scope here; the caller's
            # state-write contains the join key via checkouts.job_id FK).
            await audit.emit(
                "payment.cancelled",
                subject={"checkout_id": checkout_id},
                data={"hold_id": hold_id, "reason": "compensation"},
            )
            # Mirror the terminal Stripe state onto payment_holds (same
            # rationale as the capture site — keep the reconciliation
            # index in sync so the reconciler doesn't ask Stripe again).
            try:
                await payment_holds_store.mark_status(hold_id, "canceled")
            except Exception as exc:
                logger.error(
                    f"[saga] payment_holds.mark_status(canceled) failed for "
                    f"hold {hold_id}: {exc}. Reconciler will catch up.",
                    exc_info=True,
                )
            return True, None

        except PaymentRetryableError as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                wait_s = _CAPTURE_BACKOFFS_SECONDS[attempt]
                logger.warning(
                    f"[saga] cancel attempt {attempt + 1} transient failure for "
                    f"hold {hold_id}: {exc}. Retrying in {wait_s}s."
                )
                await asyncio.sleep(wait_s)
                continue
            break  # exhausted retries

        except PaymentPermanentError as exc:
            logger.error(
                f"[saga] cancel permanent failure for hold {hold_id}: {exc}. "
                "Skipping remaining retries."
            )
            return False, f"permanent: {exc}"

        except Exception as exc:
            logger.error(
                f"[saga] cancel unexpected exception for hold {hold_id}",
                exc_info=True,
            )
            return False, f"unexpected: {exc}"

    return False, f"exhausted {max_attempts} attempts: {last_error}"


def _compose_compensation_reason(
    *,
    original_error: str,
    outcome: _CompensationOutcome,
) -> str:
    """Format the operator-facing MANUAL_REVIEW reason string.

    The reason enumerates every resource that requires manual action: failed
    BrickOwl cancels (with IDs), uncancellable LEGO orders (with ID), Stripe
    hold status (released or stranded). Operators read this directly via
    /status or the state file.
    """
    parts: list[str] = [
        f"Compensation partial failure (original trigger: {original_error})."
    ]

    if outcome.state_load_error:
        parts.append(
            f"Checkout state could not be loaded ({outcome.state_load_error}); "
            "manual reconciliation required: inspect Stripe dashboard for any "
            "active hold under this job, and check BrickOwl + LEGO.com order "
            "history for orders that may have been placed."
        )

    if outcome.brickowl_succeeded:
        parts.append(
            f"BrickOwl orders successfully cancelled: {outcome.brickowl_succeeded}."
        )

    if outcome.brickowl_failed:
        failed_summary = "; ".join(
            f"{oid} ({err})" for oid, err in outcome.brickowl_failed
        )
        parts.append(
            f"BrickOwl orders REQUIRING MANUAL CANCELLATION: {failed_summary}."
        )

    if outcome.lego_uncancellable:
        parts.append(
            f"LEGO.com order {outcome.lego_uncancellable} REQUIRES MANUAL "
            "CANCELLATION at lego.com/profile/orders (no API)."
        )

    if outcome.stripe_skip_reason == "missing_checkout_id":
        parts.append(
            "Stripe hold NOT cancelled: state is missing checkout_id, so a "
            "safe idempotency key cannot be constructed. Cancel the hold "
            "manually in the Stripe dashboard."
        )
    elif outcome.stripe_skip_reason == "provider_unavailable":
        parts.append(
            f"Stripe hold NOT cancelled: payment provider unavailable "
            f"({outcome.stripe_error}). Cancel manually in the Stripe dashboard."
        )
    elif outcome.stripe_failed:
        parts.append(
            f"Stripe hold cancel FAILED ({outcome.stripe_error}); cancel "
            "manually in the Stripe dashboard so the customer's authorization "
            "is released."
        )
    elif outcome.stripe_succeeded:
        parts.append("Stripe hold released successfully.")

    parts.append(
        "Operator runbook: "
        "(1) cancel each stranded marketplace order via the seller portal; "
        "(2) ensure the Stripe hold is released; "
        "(3) notify the customer of the outcome."
    )

    return " ".join(parts)


async def _compensate(
    job_id: str,
    *,
    original_error: str,
    extra_brickowl_orders: list[str] | None = None,
    extra_lego_order: str | None = None,
) -> None:
    """Roll back marketplace orders and release the payment hold.

    Writes its OWN terminal saga_status — callers MUST NOT overwrite. This
    is the contract that distinguishes COMPENSATED (clean rollback) from
    MANUAL_REVIEW (some real resource stranded; operator must intervene).

    Decision rule:
        - state load failed                                → MANUAL_REVIEW
        - any BrickOwl cancel raised                       → MANUAL_REVIEW
        - any LEGO order is present (uncancellable by API) → MANUAL_REVIEW
        - Stripe cancel failed after retries               → MANUAL_REVIEW
        - state missing checkout_id (Stripe cancel unsafe) → MANUAL_REVIEW
        - provider unavailable for cancel                  → MANUAL_REVIEW
        - everything succeeded (or nothing to do)          → COMPENSATED

    `original_error` is the upstream error that triggered compensation. It
    is stored on the state's `error` field so /status can render it for the
    customer (clean COMPENSATED case) or as supplementary context to the
    operator (MANUAL_REVIEW case).

    `extra_brickowl_orders` and `extra_lego_order` are B19 safety kwargs.
    When a marketplace call succeeds but the subsequent state checkpoint
    fails (filesystem flap, JSON encode error, etc.), the order ID lives
    only in the caller's local variable. Passing it here ensures the order
    is included in cancellation (BrickOwl) or in the MANUAL_REVIEW reason
    (LEGO). If an extra is already in state, it's deduplicated — extras
    are strictly additive; they never replace state. An extras-only order
    (one that wasn't in state) emits a WARNING log so operators have a
    signal that a state-write was lost.

    Idempotency: pre-condition check skips when state is already terminal,
    so an accidental double-call is a no-op. Each provider cancel uses a
    stable idempotency key derived from checkout_id; the BrickOwl cancel
    stub is currently a no-op (real implementation per roadmap #5 must
    accept duplicate cancels gracefully).

    See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4 B3/B2/B18/B19/B20/B21/B22
    for the deferred-vs-bundled decisions documented at fix time.
    """
    outcome = _CompensationOutcome()

    # ── Read state defensively ───────────────────────────────────────────────
    try:
        state = await checkout_store.load(job_id)
        if state is None:
            state = {}
    except Exception as exc:
        outcome.state_load_error = str(exc)
        state = {}
        logger.critical(
            f"[saga] [{job_id}] _compensate could not load state: {exc}. "
            "Will write MANUAL_REVIEW for operator reconciliation."
        )

    # ── Precondition: already-terminal state → no-op (re-entrant safety) ────
    last_status = state.get("saga_status")
    if last_status in _TERMINAL_STATUSES:
        logger.warning(
            f"[saga] [{job_id}] _compensate called with already-terminal state "
            f"{last_status!r}; no-op."
        )
        return

    # ── B19: merge state's order records with caller-supplied extras ────────
    # Extras are in-memory order IDs that a state-write may not have captured.
    # We union them with state (dedup), so we cancel the right thing even if
    # state is partially stale.
    state_brickowl_orders = list(state.get("brickowl_order_ids") or [])
    brickowl_orders_to_cancel = list(state_brickowl_orders)
    if extra_brickowl_orders:
        missing_from_state = [
            oid for oid in extra_brickowl_orders if oid not in brickowl_orders_to_cancel
        ]
        if missing_from_state:
            logger.warning(
                f"[saga] [{job_id}] _compensate including {len(missing_from_state)} "
                f"BrickOwl order(s) from extras (not in state): {missing_from_state}. "
                "Caller signaled a post-placement state-write failure (B19)."
            )
            brickowl_orders_to_cancel.extend(missing_from_state)

    state_lego_order = state.get("lego_order_id")
    lego_order_for_review: str | None = state_lego_order or extra_lego_order
    if extra_lego_order and state_lego_order is None:
        logger.warning(
            f"[saga] [{job_id}] _compensate using LEGO order {extra_lego_order} "
            "from extras (not in state). Caller signaled a post-placement "
            "state-write failure (B19)."
        )

    # ── Phase 1: cancel BrickOwl orders in parallel (B16/H10) ───────────────
    # Reversed input order preserves the LIFO intent: the input list to
    # `_parallel_brickowl_cancels` has the most-recent orders first. The
    # outcome dataclass is a set of facts, not an ordered log, so the
    # temporal completion order doesn't matter.
    cancel_results = await _parallel_brickowl_cancels(
        list(reversed(brickowl_orders_to_cancel))
    )
    for order_id, error in cancel_results:
        if error is None:
            outcome.brickowl_succeeded.append(order_id)
        else:
            outcome.brickowl_failed.append((order_id, error))
            logger.error(
                f"[saga] [{job_id}] BrickOwl cancel failed for {order_id}: {error}"
            )

    # ── Phase 2: LEGO.com order (uncancellable via API) ──────────────────────
    if lego_order_for_review:
        outcome.lego_uncancellable = lego_order_for_review
        logger.warning(
            f"[saga] [{job_id}] LEGO.com order {lego_order_for_review} was placed and "
            "cannot be cancelled via API. Cancel manually at lego.com/profile/orders."
        )

    # ── Phase 3: release Stripe hold (with retry) ────────────────────────────
    hold_id = state.get("payment_hold_id")
    if not hold_id:
        outcome.stripe_skip_reason = "no_hold"
    else:
        checkout_id = state.get("checkout_id")
        if not checkout_id:
            # Cannot construct a stable idempotency key. Refuse the cancel
            # rather than risk a divergent key that would defeat Stripe's
            # safe-retry guarantee. Operator releases manually.
            outcome.stripe_skip_reason = "missing_checkout_id"
            outcome.stripe_error = (
                "state missing checkout_id; refusing to cancel without a stable key"
            )
            logger.critical(
                f"[saga] [{job_id}] _compensate cannot cancel hold {hold_id}: "
                "state is missing checkout_id. Cancel manually in the Stripe dashboard."
            )
        else:
            try:
                provider = payment_registry.get_active()
            except PaymentProviderUnavailable as exc:
                outcome.stripe_skip_reason = "provider_unavailable"
                outcome.stripe_error = str(exc)
                logger.error(
                    f"[saga] [{job_id}] No payment provider for cancel: {exc}. "
                    f"Cancel hold {hold_id} manually in the Stripe dashboard."
                )
            else:
                succeeded, error_msg = await _cancel_hold_with_retry(
                    provider=provider,
                    checkout_id=checkout_id,
                    hold_id=hold_id,
                )
                if succeeded:
                    outcome.stripe_succeeded = True
                else:
                    outcome.stripe_failed = True
                    outcome.stripe_error = error_msg

    # ── Decide terminal status (single state write) ──────────────────────────
    if outcome.needs_manual_review():
        terminal_status = SagaStatus.MANUAL_REVIEW
        update_fields = {
            "saga_status": terminal_status,
            "manual_review_reason": _compose_compensation_reason(
                original_error=original_error,
                outcome=outcome,
            ),
            "error": "Compensation partial failure (see manual_review_reason)",
            "customer_message": ERROR_MESSAGES["manual_review"],
        }
        log_fn = logger.critical
        log_msg = f"[saga] [{job_id}] MANUAL_REVIEW — compensation partial failure"
    else:
        terminal_status = SagaStatus.COMPENSATED
        update_fields = {
            "saga_status": terminal_status,
            "error": original_error,
            "customer_message": ERROR_MESSAGES["marketplace_failure"],
        }
        log_fn = logger.info
        log_msg = (
            f"[saga] [{job_id}] COMPENSATED cleanly — "
            f"brickowl_cancelled={len(outcome.brickowl_succeeded)}, "
            f"stripe={'released' if outcome.stripe_succeeded else outcome.stripe_skip_reason}"
        )

    try:
        await checkout_store.update(job_id, update_fields)
        log_fn(log_msg)
    except Exception as exc:
        # Filesystem failure on the terminal write — the saga state is now
        # indeterminate. Log critical so the operator can reconstruct from
        # log history; do not re-raise (caller just returns).
        logger.critical(
            f"[saga] [{job_id}] _compensate could not write terminal state "
            f"({terminal_status}): {exc}. State file may be corrupt; "
            "manual inspection required."
        )
        return

    # L6 audit emit AFTER the state write so an unwritable terminal doesn't
    # produce a misleading audit row. checkout_id is best-effort from state
    # (None if state-load failed earlier in the call).
    audit_checkout_id = state.get("checkout_id")
    audit_hold_id = state.get("payment_hold_id")
    if terminal_status == SagaStatus.MANUAL_REVIEW:
        await audit.emit(
            "saga.manual_review",
            subject={"job_id": job_id, "checkout_id": audit_checkout_id},
            data={
                "reason": "compensation_partial_failure",
                "hold_id": audit_hold_id,
                "authorized_cents": state.get("payment_authorized_cents"),
                "last_error": original_error,
                "brickowl_cancelled": list(outcome.brickowl_succeeded),
                "brickowl_failed": [oid for oid, _ in outcome.brickowl_failed],
                "lego_uncancellable": outcome.lego_uncancellable,
                "stripe_outcome": (
                    "succeeded" if outcome.stripe_succeeded
                    else outcome.stripe_skip_reason or "failed"
                ),
            },
        )
    else:
        await audit.emit(
            "saga.compensated",
            subject={"job_id": job_id, "checkout_id": audit_checkout_id},
            data={
                "cancelled_orders": {
                    "brickowl": list(outcome.brickowl_succeeded),
                    "lego": [],
                },
                "manual_required": False,
                "trigger": original_error,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Timeout handler
# ─────────────────────────────────────────────────────────────────────────────


async def _handle_saga_timeout(job_id: str, checkout_id: str) -> None:
    """Single-policy recovery for a Saga that exceeded _SAGA_TIMEOUT_SECONDS.

    Reads the last checkpointed state and routes:

      - state already terminal (race won by inner saga finalizing just before
        timeout): no-op
      - no payment_hold_id: FAILED (no money moved)
      - hold exists but no marketplace orders placed: best-effort
        provider.cancel(); FAILED on success, MANUAL_REVIEW if cancel fails
      - any marketplace orders placed: MANUAL_REVIEW (capture state cannot
        be inferred from here; operator inspects Stripe + marketplaces)

    This handler is intentionally NOT wrapped in its own timeout. It runs
    only state reads, one state write, and at most one provider.cancel()
    call (which has its own SDK-level request_timeout). Nesting another
    wait_for() here would re-introduce the timeout-layering chaos the
    single Saga-level deadline is designed to prevent.

    The handler MUST always write a terminal state (or leave the existing
    terminal state in place). The caller has already exited the saga
    coroutine; this is the last chance to make /status reflect reality.
    """
    try:
        state = await checkout_store.load(job_id) or {}
    except Exception as exc:
        # B25: state unreadable (JSON corruption, asyncpg query failure,
        # disk full, etc.). Without state we can't know what was placed —
        # MANUAL_REVIEW with a verbose runbook is the only safe terminal
        # state. The handler's contract (see docstring) says it MUST write
        # a terminal state; the original code violated this by just logging.
        logger.critical(
            f"[saga] [{checkout_id}] TIMEOUT handler could not load state: {exc}. "
            "State file may be corrupt; writing best-effort MANUAL_REVIEW."
        )
        try:
            await checkout_store.update(job_id, {
                "saga_status": SagaStatus.MANUAL_REVIEW,
                "manual_review_reason": (
                    f"Saga timed out after {_SAGA_TIMEOUT_SECONDS}s AND state "
                    f"load failed ({exc}). Operator must: "
                    "(1) check Stripe dashboard for any hold under this job, "
                    "(2) check BrickOwl and LEGO.com for any orders placed in "
                    "the last hour, (3) reconcile manually."
                ),
                "error": f"Timeout + state load failure: {exc}",
                "customer_message": ERROR_MESSAGES["manual_review"],
            })
            await audit.emit(
                "saga.manual_review",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={
                    "reason": "timeout_state_load_failed",
                    "timeout_seconds": _SAGA_TIMEOUT_SECONDS,
                    "last_error": str(exc),
                },
            )
        except Exception as write_exc:
            # Inner write ALSO failed (state file permission, DB unreachable).
            # Nothing more we can do — /status will return whatever the last
            # successful write left behind, but at least the audit log shows
            # both failures.
            logger.critical(
                f"[saga] [{checkout_id}] could not write fallback MANUAL_REVIEW "
                f"after state-load failure: {write_exc}. /status is stuck on "
                "the last persisted state until operator intervention."
            )
        return

    last_status = state.get("saga_status")
    if last_status in _TERMINAL_STATUSES:
        # The inner saga finalized in the same scheduler tick the timeout
        # fired. Whatever state it wrote is the truth.
        logger.warning(
            f"[saga] [{checkout_id}] timeout race: inner saga already finalized "
            f"to {last_status!r}; no action taken."
        )
        return

    brickowl_orders = state.get("brickowl_order_ids") or []
    lego_order = state.get("lego_order_id")
    hold_id = state.get("payment_hold_id")
    orders_placed = bool(brickowl_orders) or lego_order is not None

    logger.critical(
        f"[saga] [{checkout_id}] TIMEOUT after {_SAGA_TIMEOUT_SECONDS}s; "
        f"last_status={last_status!r}, hold_id={hold_id!r}, "
        f"brickowl_orders={brickowl_orders}, lego_order={lego_order!r}"
    )

    # ── Branch 1: orders placed → MANUAL_REVIEW (cannot auto-recover) ────────
    if orders_placed:
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW,
            "manual_review_reason": (
                f"Saga exceeded {_SAGA_TIMEOUT_SECONDS}s deadline with orders "
                f"already placed (last status: {last_status}). Capture state "
                f"cannot be inferred from here. Operator must: "
                f"(1) check Stripe dashboard for hold {hold_id} — is it captured, "
                f"authorized, or cancelled? "
                f"(2) verify each BrickOwl order in {brickowl_orders} actually "
                f"shipped (or did not). "
                f"(3) verify LEGO.com order {lego_order!r} actually placed. "
                f"(4) reconcile: capture remaining hold OR refund placed orders."
            ),
            "error": "Saga deadline exceeded with orders placed",
            "customer_message": ERROR_MESSAGES["manual_review"],
        })
        await audit.emit(
            "saga.manual_review",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "reason": "timeout_orders_placed",
                "timeout_seconds": _SAGA_TIMEOUT_SECONDS,
                "last_status": last_status,
                "hold_id": hold_id,
                "brickowl_order_count": len(brickowl_orders),
                "lego_order_id": lego_order,
            },
        )
        return

    # ── Branch 2: hold exists, no orders → best-effort cancel hold ───────────
    if hold_id:
        cancel_succeeded = False
        cancel_error: str | None = None
        try:
            provider = payment_registry.get_active()
            await provider.cancel(
                hold_id=hold_id,
                idempotency_key=f"cancel-{checkout_id}",
            )
            cancel_succeeded = True
            # Mirror the cancel onto payment_holds. Best-effort — same
            # rationale as _cancel_hold_with_retry's mark_status call.
            try:
                await payment_holds_store.mark_status(hold_id, "canceled")
            except Exception as exc:
                logger.error(
                    f"[saga] [{checkout_id}] timeout-cancel: payment_holds."
                    f"mark_status(canceled) failed for hold {hold_id}: {exc}. "
                    "Reconciler will catch up.",
                    exc_info=True,
                )
        except PaymentProviderUnavailable as exc:
            cancel_error = (
                f"No payment provider registered when releasing hold: {exc}"
            )
        except Exception as exc:
            cancel_error = str(exc)

        if cancel_succeeded:
            await checkout_store.update(job_id, {
                "saga_status": SagaStatus.FAILED,
                "error": (
                    f"Saga exceeded {_SAGA_TIMEOUT_SECONDS}s deadline before "
                    "orders were placed. Payment hold released."
                ),
                "customer_message": ERROR_MESSAGES["timeout"],
            })
            await audit.emit(
                "payment.cancelled",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={"hold_id": hold_id, "reason": "timeout.no_orders"},
            )
            await audit.emit(
                "saga.failed",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={
                    "reason": "timeout_pre_orders_hold_released",
                    "timeout_seconds": _SAGA_TIMEOUT_SECONDS,
                    "hold_id": hold_id,
                },
            )
        else:
            # We have a hold and could not release it. Operator must cancel
            # in the Stripe dashboard to free the customer's authorization.
            await checkout_store.update(job_id, {
                "saga_status": SagaStatus.MANUAL_REVIEW,
                "manual_review_reason": (
                    f"Saga timed out before orders placed, but failed to cancel "
                    f"hold {hold_id}: {cancel_error}. "
                    f"Cancel manually in the Stripe dashboard so the customer's "
                    f"authorization is released."
                ),
                "error": f"Timeout cleanup cancel failed: {cancel_error}",
                "customer_message": ERROR_MESSAGES["manual_review"],
            })
            await audit.emit(
                "saga.manual_review",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={
                    "reason": "timeout_cancel_failed",
                    "timeout_seconds": _SAGA_TIMEOUT_SECONDS,
                    "hold_id": hold_id,
                    "last_error": cancel_error,
                },
            )
        return

    # ── Branch 3: no hold, no orders → clean FAILED ──────────────────────────
    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.FAILED,
        "error": (
            f"Saga exceeded {_SAGA_TIMEOUT_SECONDS}s deadline before "
            "the payment hold was created. No money moved; no orders placed."
        ),
        "customer_message": ERROR_MESSAGES["timeout"],
    })
    await audit.emit(
        "saga.failed",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "reason": "timeout_no_hold",
            "timeout_seconds": _SAGA_TIMEOUT_SECONDS,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Saga entry point — timeout wrapper
# ─────────────────────────────────────────────────────────────────────────────


async def execute_checkout_saga(
    job_id: str,
    checkout_id: str,
    allocation: AllocationResult,
    payment_method_id: str,
    max_stockout_retries: int = 2,
) -> None:
    """
    Full checkout Saga. Runs as a background asyncio task.

    B4 timeout wrapper: the entire inner Saga runs inside a single
    asyncio.wait_for() bounded by _SAGA_TIMEOUT_SECONDS. On expiry the inner
    coroutine is cancelled and _handle_saga_timeout() classifies recovery
    based on the last checkpointed state. See module docstring section
    "TIMEOUT POLICY (B4)" for the routing matrix.

    Do not call _execute_checkout_saga_inner() directly — bypassing this
    wrapper means an unbounded Saga lifetime, which can pin _running_sagas
    forever and hold customer money for up to 7 days.
    """
    try:
        await asyncio.wait_for(
            _execute_checkout_saga_inner(
                job_id=job_id,
                checkout_id=checkout_id,
                allocation=allocation,
                payment_method_id=payment_method_id,
                max_stockout_retries=max_stockout_retries,
            ),
            timeout=_SAGA_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # Inner coroutine was cancelled at the await it was blocked on.
        # The handler reads checkpointed state to decide recovery.
        await _handle_saga_timeout(job_id, checkout_id)


# ─────────────────────────────────────────────────────────────────────────────
# Saga entry point — inner implementation
# ─────────────────────────────────────────────────────────────────────────────


async def _execute_checkout_saga_inner(
    job_id: str,
    checkout_id: str,
    allocation: AllocationResult,
    payment_method_id: str,
    max_stockout_retries: int = 2,
) -> None:
    """
    Inner Saga body. ALWAYS reached via execute_checkout_saga() so the
    timeout wrapper bounds lifetime.

    Layer 4 of the checkout defense-in-depth: the first statement below calls
    gate.require_open() so a Saga can never run against a closed gate. This
    catches:
      - The race where /confirm passed L3 but the gate closed before the
        background task started running
      - Any direct invocation that bypasses /confirm (admin tools, batch jobs,
        future routes, test fixtures, Saga resumption after restart)
    See docs/CHECKOUT_AUDIT.md §10 "L4 design" for the full contract.
    """
    from .optimizer import optimize

    # ── Layer 4 — gate pre-flight ────────────────────────────────────────────
    # MUST be the first statement. No state mutation, no external calls before
    # this. GateClosedError is intentionally caught ONLY here in the codebase
    # (see gate.py docstring rule). Failure path writes a clear FAILED state
    # so /status reflects the gate-induced abort.
    try:
        gate_decision = require_open()
    except GateClosedError as exc:
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"Gate closed at Saga start: {exc}",
            "customer_message": ERROR_MESSAGES["gate_closed"],
        })
        # L6: gate.saga_rejected — operator-visible signal that L4 fired.
        # Re-read the decision so reasons[] reflects state at rejection time
        # (env may have changed since /confirm enqueued this task).
        decision_at_reject = compute_decision()
        await audit.emit(
            "gate.saga_rejected",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "mode": decision_at_reject.mode.value,
                "reasons": list(decision_at_reject.reasons),
                "invocation_source": "saga",
            },
        )
        await audit.emit(
            "saga.failed",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={"reason": "gate_closed_at_start", "error_message": str(exc)},
        )
        logger.critical(
            f"[saga] [{checkout_id}] GATE CLOSED — refusing to run. {exc}"
        )
        return  # graceful failure — do NOT re-raise (would crash the asyncio task)
    # ─────────────────────────────────────────────────────────────────────────

    # ── Layer 5 — payment provider obtained ──────────────────────────────────
    # The gate just told us a provider is registered. Acquire it once for the
    # whole Saga so all payment ops use the same instance. If get_active()
    # raises here, gate and registry have disagreed — a real defect — and the
    # Saga must abort before placing orders.
    try:
        provider = payment_registry.get_active()
    except PaymentProviderUnavailable as exc:
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"Payment provider unavailable despite open gate: {exc}",
            "customer_message": ERROR_MESSAGES["gate_closed"],
        })
        await audit.emit(
            "saga.failed",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "reason": "provider_unavailable_after_gate_open",
                "error_message": str(exc),
            },
        )
        logger.critical(
            f"[saga] [{checkout_id}] PROVIDER UNAVAILABLE — refusing to run. {exc}"
        )
        return
    # ─────────────────────────────────────────────────────────────────────────

    await checkout_store.update(job_id, {
        "checkout_id": checkout_id,
        "saga_status": SagaStatus.INITIATED,
        "brickowl_order_ids": [],
        "lego_order_id": None,
        "payment_hold_id": None,
        "payment_authorized_cents": None,
        "payment_provider": provider.name,
        "payment_mode": provider.mode(),
        "total_charged_cents": None,
        "error": None,
        "customer_message": None,
        "manual_review_reason": None,
        "completed_at": None,
    })
    await audit.emit(
        "saga.started",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={"mode": provider.mode(), "payment_provider": provider.name},
    )

    # ── Step 1: Payment hold (with 5% buffer) ─────────────────────────────────
    # The hold is for 1.05× the quoted customer total. Capture later is for
    # the actual allocated total — possibly different after stockout retries.
    # See _HOLD_BUFFER_MULTIPLIER docstring above.
    quote_total = allocation.customer_total_cents
    hold_amount = int(math.ceil(quote_total * _HOLD_BUFFER_MULTIPLIER))

    hold: PaymentHold
    try:
        hold = await provider.create_hold(
            amount_cents=hold_amount,
            # B7/H5: currency is locked at provider construction (validated
            # against an allowlist there). Reading env at saga time risked a
            # hold-vs-capture currency mismatch after a mid-saga env change.
            currency=provider.currency,
            payment_method_id=payment_method_id,
            idempotency_key=f"hold-{checkout_id}",
        )
    except PaymentPermanentError as exc:
        # Card declined, auth bug, invalid request. No orders placed yet, so
        # no compensation needed. Customer needs a different payment method
        # — the frontend should display this and re-prompt.
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"Payment hold failed (permanent): {exc}",
            "customer_message": ERROR_MESSAGES["payment_permanent"],
        })
        await audit.emit(
            "saga.failed",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={"reason": "hold_permanent", "error_message": str(exc)},
        )
        logger.warning(
            f"[saga] [{checkout_id}] Hold failed permanently: {exc}"
        )
        return
    except PaymentRetryableError as exc:
        # Transient at hold time — no orders placed yet, safest action is to
        # abort cleanly and let the customer retry from /confirm. Retrying
        # the hold automatically would risk double-authorizing if the first
        # request actually succeeded but the response was lost.
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"Payment hold failed (transient — please retry): {exc}",
            "customer_message": ERROR_MESSAGES["payment_transient"],
        })
        await audit.emit(
            "saga.failed",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={"reason": "hold_transient", "error_message": str(exc)},
        )
        logger.warning(
            f"[saga] [{checkout_id}] Hold transiently failed: {exc}"
        )
        return
    except Exception as exc:
        # Unexpected non-provider error — bug in our code or in the SDK.
        # Treat as transient from the customer's perspective: don't blame the
        # card (this might not even be card-related); they should retry.
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"Payment hold failed unexpectedly: {exc}",
            "customer_message": ERROR_MESSAGES["payment_transient"],
        })
        await audit.emit(
            "saga.failed",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "reason": "hold_unexpected",
                "error_message": str(exc),
                "error_class": type(exc).__name__,
            },
        )
        logger.error(
            f"[saga] [{checkout_id}] Hold failed unexpectedly", exc_info=True
        )
        return

    # L6: hold succeeded — emit BEFORE record_hold so a record_hold failure
    # doesn't suppress the audit row for an authorization that genuinely
    # exists at Stripe.
    await audit.emit(
        "payment.hold_created",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "hold_id": hold.hold_id,
            "amount_cents": hold.amount_authorized_cents,
            "mode": hold.mode,
        },
    )

    # Record the hold in the reconciliation index BEFORE the sagas update.
    # If `record_hold` fails (DB transient), we want the saga to abort here
    # — better to refund a brand-new hold via the saga's exception path than
    # to commit `saga_status='stripe_held'` without a reconciliation row.
    # No-op when DB_BACKEND != postgres. ON CONFLICT DO NOTHING handles the
    # rare resumed-saga case where create_hold's idempotency key returns
    # the same hold.
    try:
        await payment_holds_store.record_hold(checkout_id, hold)
    except Exception as exc:
        # Roll back: cancel the brand-new hold (best-effort; the reconciler
        # would catch a stranded hold eventually but we have the provider in
        # hand right now). Then fail the saga so the customer sees a
        # retryable error rather than silently stranding funds.
        logger.error(
            f"[saga] [{checkout_id}] payment_holds.record_hold failed after "
            f"create_hold succeeded: {exc}. Attempting cancel.",
            exc_info=True,
        )
        try:
            await provider.cancel(
                hold_id=hold.hold_id,
                idempotency_key=f"cancel-{checkout_id}",
            )
            await audit.emit(
                "payment.cancelled",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={
                    "hold_id": hold.hold_id,
                    "reason": "record_hold_failed_rollback",
                },
            )
        except Exception as cancel_exc:
            logger.critical(
                f"[saga] [{checkout_id}] cancel after record_hold failure "
                f"ALSO failed: {cancel_exc}. Hold {hold.hold_id} is stranded "
                "until reconciler picks it up — but reconciler won't see it "
                "either (no payment_holds row). Operator must inspect Stripe "
                "dashboard manually.",
                exc_info=True,
            )
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"payment_holds INSERT failed: {exc}",
            "customer_message": ERROR_MESSAGES["payment_transient"],
        })
        await audit.emit(
            "saga.failed",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "reason": "payment_holds_insert_failed",
                "hold_id": hold.hold_id,
                "error_message": str(exc),
            },
        )
        return

    await checkout_store.update(job_id, {
        "payment_hold_id": hold.hold_id,
        "payment_authorized_cents": hold.amount_authorized_cents,
        "saga_status": SagaStatus.STRIPE_HELD,
    })
    await audit.emit(
        "saga.stripe_held",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "hold_id": hold.hold_id,
            "amount_cents": hold.amount_authorized_cents,
            "currency": hold.currency,
        },
    )

    # ── Step 2: Place orders per seller ──────────────────────────────────────
    current_allocation = allocation
    retries_left = max_stockout_retries

    while True:
        # ── B5: pre-placement allocation-drift check ─────────────────────────
        # Stockout retries can re-optimize `current_allocation` to a higher
        # total. If the new total exceeds the authorized hold (which is
        # ceil(quote * _HOLD_BUFFER_MULTIPLIER)), we cannot capture — detect
        # HERE, before any orders are placed in this iteration, so the
        # customer is cleanly refunded rather than stranded with real
        # marketplace orders that exceed authorization.
        #
        # Iteration 1 is mathematically a no-op (hold >= ceil(quote * 1.05)
        # >= quote). The check is still placed here as defense-in-depth
        # against future changes to the hold formula. Iterations N>1 are
        # where this has real teeth.
        #
        # No orders are placed at this point in the current iteration
        # (orders from a prior iteration were cancelled inline in the
        # stockout-retry handler before `continue`). So _compensate sees
        # only a hold to release; clean path routes to COMPENSATED (or
        # MANUAL_REVIEW if the cancel itself fails).
        if current_allocation.customer_total_cents > hold.amount_authorized_cents:
            logger.critical(
                f"[saga] [{checkout_id}] PRE-PLACEMENT DRIFT > BUFFER — "
                f"new total {current_allocation.customer_total_cents} > "
                f"authorized {hold.amount_authorized_cents} "
                f"(after {max_stockout_retries - retries_left} stockout retries). "
                "Aborting before placing orders; hold will be released."
            )
            await _compensate(
                job_id,
                original_error=(
                    f"Stockout retry produced an allocation "
                    f"(${current_allocation.customer_total_cents / 100:.2f}) "
                    f"exceeding the authorization buffer "
                    f"(${hold.amount_authorized_cents / 100:.2f}). "
                    "Aborting before placing orders to avoid charging more "
                    "than authorized."
                ),
            )
            return

        placed_brickowl_ids: list[str] = []
        lego_order_id: str | None = None
        stockout_eid: str | None = None

        # ── BrickOwl sellers ─────────────────────────────────────────────────
        # B19: marketplace call and state checkpoint are intentionally in
        # SEPARATE try blocks. If the order is placed (Step A) but the state
        # write fails (Step B), the order ID lives only in
        # `placed_brickowl_ids` — we pass that to _compensate as extras so
        # the order is still cancelled rather than silently shipped.
        brickowl_entries = [
            e for e in current_allocation.seller_allocations
            if e.seller_id.startswith(brickowl_client.SELLER_ID_PREFIX)
        ]
        for entry in brickowl_entries:
            # Step A: place the marketplace order.
            try:
                order_id = await brickowl_client.create_order(
                    seller_id=entry.seller_id,
                    items=entry.items,
                )
            except StockoutError as e:
                stockout_eid = e.element_id
                break
            except Exception as exc:
                # create_order itself raised; this entry has no order_id to
                # track. Earlier entries in this iteration are in
                # `placed_brickowl_ids` (and should be in state). Pass as
                # extras for defensive coverage against a prior B19 race.
                await _compensate(
                    job_id,
                    original_error=f"BrickOwl order failed: {exc}",
                    extra_brickowl_orders=placed_brickowl_ids,
                )
                return

            # Step B: order succeeded; checkpoint state.
            placed_brickowl_ids.append(order_id)
            try:
                await checkout_store.update(
                    job_id, {"brickowl_order_ids": list(placed_brickowl_ids)}
                )
            except Exception as state_exc:
                # B19: marketplace order is real but state-write failed.
                # `placed_brickowl_ids` (incl. the just-placed order_id) is
                # the only record. Hand it to _compensate so the order is
                # cancelled rather than lost.
                logger.critical(
                    f"[saga] [{checkout_id}] BrickOwl order {order_id} placed but "
                    f"state-write failed: {state_exc}. Halting iteration and "
                    "routing to compensation."
                )
                await _compensate(
                    job_id,
                    original_error=(
                        f"BrickOwl state-write failed after order {order_id} "
                        f"was placed: {state_exc}"
                    ),
                    extra_brickowl_orders=placed_brickowl_ids,
                )
                return

        if stockout_eid:
            if retries_left == 0:
                # _compensate writes its own terminal status (B2 contract).
                await _compensate(
                    job_id,
                    original_error=(
                        f"Stockout on '{stockout_eid}' after "
                        f"{max_stockout_retries} retries"
                    ),
                    extra_brickowl_orders=placed_brickowl_ids,
                )
                return

            # B16/H10: cancel placed BrickOwl orders in parallel (bounded
            # concurrency). On ANY failure, write a single MANUAL_REVIEW with
            # all failing orders enumerated — do not abort mid-list, since
            # leaving more orders un-cancelled increases the operator's
            # cleanup work and worsens the customer's stranded inventory.
            cancel_results = await _parallel_brickowl_cancels(
                list(reversed(placed_brickowl_ids))
            )
            failures = [(oid, err) for oid, err in cancel_results if err is not None]
            if failures:
                failed_summary = "; ".join(f"{oid}: {err}" for oid, err in failures)
                await checkout_store.update(job_id, {
                    "saga_status": SagaStatus.MANUAL_REVIEW,
                    "manual_review_reason": (
                        f"Stockout-retry cancel failed for "
                        f"{len(failures)}/{len(placed_brickowl_ids)} BrickOwl orders. "
                        f"Failures: {failed_summary}. "
                        f"Cancel hold {hold.hold_id} manually in Stripe and verify all "
                        f"BrickOwl orders in {placed_brickowl_ids} are cancelled."
                    ),
                    "error": (
                        f"Stockout-retry compensation failed: {len(failures)} cancel(s) raised"
                    ),
                    "customer_message": ERROR_MESSAGES["manual_review"],
                })
                await audit.emit(
                    "saga.manual_review",
                    subject={"job_id": job_id, "checkout_id": checkout_id},
                    data={
                        "reason": "stockout_retry_cancel_failed",
                        "hold_id": hold.hold_id,
                        "authorized_cents": hold.amount_authorized_cents,
                        "brickowl_failed": [oid for oid, _ in failures],
                        "brickowl_attempted": list(placed_brickowl_ids),
                    },
                )
                logger.critical(
                    f"[saga] [{checkout_id}] MANUAL_REVIEW — "
                    f"{len(failures)} stockout-retry cancel(s) failed: {failed_summary}"
                )
                return
            await checkout_store.update(job_id, {"brickowl_order_ids": []})
            # B9/B10/H8: invalidate the stockout element across ALL marketplaces
            # so the re-fetch below sees live data everywhere — not just from
            # BrickOwl. Each client owns its own cache-key naming.
            await asyncio.gather(
                brickowl_client.invalidate_listing(stockout_eid),
                lego_client.invalidate_listing(stockout_eid),
                bricklink_client.invalidate_listing(stockout_eid),
            )

            state = await checkout_store.load(job_id) or {}
            order_items = checkout_store.read_order_list(job_id)

            from .optimizer import merge_listings, apply_free_shipping_thresholds
            country = state.get("shipping_country", "US")
            zipp = state.get("shipping_zip", "")
            bo_listings, lego_listings, bl_listings = await asyncio.gather(
                brickowl_client.get_all_listings(order_items, country, zipp),
                lego_client.get_all_listings(order_items, country, zipp),
                bricklink_client.get_all_listings(order_items, country, zipp),
            )
            current_allocation = apply_free_shipping_thresholds(
                optimize(order_items, merge_listings(lego_listings, bo_listings, bl_listings))
            )
            retries_left -= 1
            logger.info(
                f"[saga] [{checkout_id}] stockout on '{stockout_eid}', "
                f"retrying ({retries_left} retries left)"
            )
            continue  # retry the while loop

        # ── LEGO.com primary order ────────────────────────────────────────────
        lego_entries = [
            e for e in current_allocation.seller_allocations
            if e.seller_id == _LEGO_SELLER_ID
        ]
        if lego_entries:
            lego_items: dict[str, int] = {}
            for entry in lego_entries:
                for eid, qty in entry.items.items():
                    lego_items[eid] = lego_items.get(eid, 0) + qty

            # B19: split marketplace call from state checkpoint. Step A's
            # success without Step B succeeding is the silent-loss case —
            # extra_lego_order is the safety net.

            # Step A: place the LEGO order via Playwright.
            try:
                lego_order_id = await lego_client.order_from_lego(
                    items=[{"elementId": eid, "quantity": qty} for eid, qty in lego_items.items()],
                    job_id=job_id,
                )
            except StockoutError as exc:
                # B8/H9 (Option B — documented asymmetry):
                # LEGO is the primary inventory source, not a fallback. A
                # stockout there means the piece is totally unavailable across
                # our supply chain — re-routing back to LEGO (the only thing
                # the stockout-retry loop knows how to do) wouldn't help.
                # Compensate cleanly rather than burn retries. The customer
                # gets a refund; operator-side, this surfaces as a "real"
                # stockout signal worth following up on (likely a recently
                # discontinued part). If LEGO ever exposes a secondary source
                # for the same eid (e.g. a wishlist queue or backorder), this
                # is the place to add the routing fallback.
                logger.warning(
                    f"[saga] [{checkout_id}] LEGO stockout on element(s) "
                    f"{exc.element_id!r}: compensating without retry "
                    "(LEGO is the primary source; re-routing would not help)."
                )
                await _compensate(
                    job_id,
                    original_error=f"LEGO.com stockout (no retry path): {exc}",
                    extra_brickowl_orders=placed_brickowl_ids,
                )
                return
            except Exception as exc:
                # order_from_lego raised; no LEGO order_id to track. BrickOwl
                # orders from earlier in this iteration are all in state, but
                # pass extras for defensive coverage against a prior B19 race.
                await _compensate(
                    job_id,
                    original_error=f"LEGO.com order failed: {exc}",
                    extra_brickowl_orders=placed_brickowl_ids,
                )
                return

            # Step B: order succeeded; checkpoint state.
            try:
                await checkout_store.update(job_id, {"lego_order_id": lego_order_id})
            except Exception as state_exc:
                # B19: LEGO order placed at lego.com but state-write failed.
                # The order is real and uncancellable (no LEGO API cancel).
                # extra_lego_order ensures _compensate routes to MANUAL_REVIEW
                # with the order ID included in the operator runbook.
                logger.critical(
                    f"[saga] [{checkout_id}] LEGO order {lego_order_id} placed but "
                    f"state-write failed: {state_exc}. Routing to compensation; "
                    "MANUAL_REVIEW is the only safe outcome (LEGO has no API cancel)."
                )
                await _compensate(
                    job_id,
                    original_error=(
                        f"LEGO state-write failed after order {lego_order_id} "
                        f"was placed: {state_exc}"
                    ),
                    extra_brickowl_orders=placed_brickowl_ids,
                    extra_lego_order=lego_order_id,
                )
                return

        break  # all orders placed

    await checkout_store.update(job_id, {"saga_status": SagaStatus.ORDERS_PLACED})
    await audit.emit(
        "saga.orders_placed",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "order_ids": {
                "brickowl": list(placed_brickowl_ids),
                "lego": [lego_order_id] if lego_order_id else [],
            },
        },
    )

    # ── Step 3: Capture payment ───────────────────────────────────────────────
    # The capture amount is the FINAL allocated total — possibly different
    # from quote_total if stockout retries re-optimized. Must be <= the
    # authorized amount (the 5% buffer absorbs upward drift).
    #
    # Allocation-drift safety: if the re-optimized total exceeds the
    # authorized amount, fail-closed to MANUAL_REVIEW. The alternative would
    # be capturing only the authorized amount and quietly absorbing the
    # delta — that's the "left to dry" failure mode. Forcing MANUAL_REVIEW
    # makes the loss visible to an operator.
    #
    # DEFENSE IN DEPTH (B5): the pre-placement drift check at the top of the
    # while-True loop should catch this case BEFORE any orders are placed
    # (clean COMPENSATED path, no operator action needed). This post-placement
    # check is kept as a safety net — it would only fire if a future refactor
    # introduced new drift sources between iteration start and capture. If it
    # does fire, MANUAL_REVIEW is the only safe state because orders are real.
    capture_amount = current_allocation.customer_total_cents
    if capture_amount > hold.amount_authorized_cents:
        logger.critical(
            f"[saga] [{checkout_id}] ALLOCATION DRIFT > BUFFER (post-placement) — "
            f"capture {capture_amount} > authorized {hold.amount_authorized_cents}. "
            "Orders are placed; escalating to MANUAL_REVIEW. "
            "(B5 pre-placement check should have caught this — investigate.)"
        )
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW,
            "manual_review_reason": (
                f"Allocation drift exceeded authorization buffer: "
                f"final total ${capture_amount/100:.2f} > authorized "
                f"${hold.amount_authorized_cents/100:.2f}. "
                f"Orders are placed. Either capture {hold.amount_authorized_cents} "
                f"cents from hold {hold.hold_id} and bill the customer separately "
                f"for the difference, or refund the placed orders."
            ),
            "error": "Allocation drift exceeded hold buffer",
            "customer_message": ERROR_MESSAGES["manual_review"],
        })
        await audit.emit(
            "saga.manual_review",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "reason": "drift_post_placement",
                "hold_id": hold.hold_id,
                "authorized_cents": hold.amount_authorized_cents,
                "capture_amount_cents": capture_amount,
            },
        )
        return

    capture_succeeded = await _capture_with_retry(
        provider=provider,
        job_id=job_id,
        checkout_id=checkout_id,
        hold=hold,
        capture_amount=capture_amount,
    )
    if not capture_succeeded:
        # _capture_with_retry already wrote the MANUAL_REVIEW or FAILED state.
        return

    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.PAYMENT_CAPTURED,
        "total_charged_cents": capture_amount,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    await audit.emit(
        "saga.captured",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "hold_id": hold.hold_id,
            "captured_amount_cents": capture_amount,
        },
    )
    logger.info(
        f"[saga] [{checkout_id}] complete — charged {capture_amount} cents "
        f"(authorized {hold.amount_authorized_cents}, mode={hold.mode})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Capture retry loop
# ─────────────────────────────────────────────────────────────────────────────


async def _capture_with_retry(
    *,
    provider,
    job_id: str,
    checkout_id: str,
    hold: PaymentHold,
    capture_amount: int,
) -> bool:
    """Attempt capture with bounded retries on transient errors only.

    Returns True iff capture succeeded. False means the state was already
    updated to MANUAL_REVIEW (or FAILED for unexpected non-provider errors) —
    the caller should return without further state mutation.

    Idempotency: a single stable key is used across all attempts so Stripe
    returns the cached response for any retry within its idempotency window
    (24h). This means a network error on the first attempt followed by a
    successful retry will not double-capture even if the first call actually
    succeeded server-side.
    """
    # Stable idempotency key — same across all retry attempts on purpose.
    idempotency_key = f"capture-{checkout_id}"

    max_attempts = len(_CAPTURE_BACKOFFS_SECONDS) + 1  # initial + retries
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            await provider.capture(
                hold_id=hold.hold_id,
                amount_cents=capture_amount,
                idempotency_key=idempotency_key,
            )
            if attempt > 0:
                logger.info(
                    f"[saga] [{checkout_id}] capture succeeded on attempt {attempt + 1}"
                )
            # L6: payment.captured — provider-level success event (paired
            # with saga.captured at the outer call site once state writes).
            await audit.emit(
                "payment.captured",
                subject={"job_id": job_id, "checkout_id": checkout_id},
                data={
                    "hold_id": hold.hold_id,
                    "captured_amount_cents": capture_amount,
                },
            )
            # Mirror the terminal Stripe state onto payment_holds so the
            # reconciler won't try to cancel a captured hold. Best-effort:
            # if this UPDATE fails, the reconciler would still see the row
            # at 'requires_capture' and ask Stripe — which would correctly
            # return 'succeeded' and update the row then. No double-capture
            # risk (Stripe is idempotent + already captured).
            try:
                await payment_holds_store.mark_status(hold.hold_id, "succeeded")
            except Exception as exc:
                logger.error(
                    f"[saga] [{checkout_id}] payment_holds.mark_status(succeeded) "
                    f"failed: {exc}. Reconciler will catch up.",
                    exc_info=True,
                )
            return True

        except PaymentRetryableError as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                wait_s = _CAPTURE_BACKOFFS_SECONDS[attempt]
                logger.warning(
                    f"[saga] [{checkout_id}] capture attempt {attempt + 1} "
                    f"transient failure: {exc}. Retrying in {wait_s}s "
                    f"({max_attempts - attempt - 1} attempt(s) left)."
                )
                await asyncio.sleep(wait_s)
                continue
            # Out of retries — orders are already placed, escalate to MANUAL_REVIEW.
            break

        except PaymentPermanentError as exc:
            # Skip remaining retries — these will not recover. Escalate now.
            logger.error(
                f"[saga] [{checkout_id}] capture permanent failure: {exc}. "
                "Skipping remaining retries."
            )
            last_error = exc
            break

        except Exception as exc:
            # Unexpected — not a provider error class. Likely a bug. Don't
            # retry blindly; escalate so an operator sees it.
            logger.error(
                f"[saga] [{checkout_id}] capture unexpected exception",
                exc_info=True,
            )
            last_error = exc
            break

    # All paths that reach here have orders placed and capture not done. The
    # only safe terminal state is MANUAL_REVIEW — the customer is not refunded,
    # because we don't know if Stripe's other side actually captured (Stripe's
    # idempotency cache should cover this on next retry, but the Saga itself
    # has spent its budget). Operator decides: re-attempt capture out-of-band,
    # refund the placed orders, or bill the customer via an alternate channel.
    reason = (
        f"Stripe capture exhausted retries after orders were placed. "
        f"Hold {hold.hold_id} is still authorized (amount "
        f"{hold.amount_authorized_cents} cents). "
        f"Last error: {last_error}. "
        f"Next steps: (1) check Stripe dashboard for the actual hold state; "
        f"(2) if captured, mark the saga PAYMENT_CAPTURED manually; "
        f"(3) if still authorized, retry capture from the dashboard or "
        f"cancel the hold and refund the placed orders."
    )
    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.MANUAL_REVIEW,
        "manual_review_reason": reason,
        "error": f"Capture failed after orders placed: {last_error}",
        "customer_message": ERROR_MESSAGES["manual_review"],
    })
    await audit.emit(
        "saga.manual_review",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "reason": "capture_exhausted_retries",
            "hold_id": hold.hold_id,
            "authorized_cents": hold.amount_authorized_cents,
            "capture_amount_cents": capture_amount,
            "last_error": str(last_error) if last_error else None,
            "last_error_class": type(last_error).__name__ if last_error else None,
        },
    )
    logger.critical(f"[saga] [{checkout_id}] MANUAL_REVIEW — {reason}")
    return False
