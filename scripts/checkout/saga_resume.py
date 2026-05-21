"""Saga resume-on-startup — the behavior the entire DB migration exists for.

`resume_in_flight_sagas()` is called ONCE during FastAPI lifespan startup,
after `init_pool()` + `verify_schema()` + payment-provider registration,
and BEFORE the scheduler/cleanup threads start. It inspects every saga
that was in a non-terminal state when the process died, and routes each
to a safe terminal state (FAILED or MANUAL_REVIEW) so no customer money
is left in limbo by a server restart.

This single function closes audit FMEA #3 ("Saga crashes mid-flight, no
resumption", RPN 450) and turns every Render restart from a customer-
money-at-risk event into 30 seconds of routing logic.

Design contract (see `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.2.7`)
====================================================================

Routing decisions mirror `saga._handle_saga_timeout` exactly — same
classification, same terminal states. The difference: this runs from a
CLEAN process at boot, so we don't have the saga's in-flight context.
Every routing decision is based on what's persisted in `sagas`:

| Persisted saga_status        | Resume action                                |
|------------------------------|----------------------------------------------|
| `initiated`                  | mark FAILED — no hold, no orders, no money   |
| `stripe_held`                | provider.cancel(hold_id); FAILED on success, |
|                              | MANUAL_REVIEW if cancel fails OR no provider |
| `orders_placed`              | MANUAL_REVIEW — orders are real; operator    |
| `fallback_ordered`           | MANUAL_REVIEW — same, LEGO fallback in flux  |
| terminal (already resolved)  | (filtered out by WHERE clause; no action)    |

Each row is routed in its own update() call — the dispatcher's per-job
advisory lock serializes against concurrent writers (rare in practice
since the scheduler hasn't started yet at resume time, but defensive).

The function NEVER RAISES. A failing recovery for one saga logs CRITICAL
and continues to the next. Boot must not be blocked by a single stuck
saga; the operator can reconcile manually via SQL.

Audit events
============
Every routing decision emits a corresponding audit event so the recovery
trail is queryable for postmortems:

- `saga.failed` for initiated->FAILED and stripe_held->FAILED-via-cancel
- `saga.manual_review` for orders_placed/fallback_ordered->MANUAL_REVIEW
  and stripe_held->MANUAL_REVIEW-on-cancel-failure
- `payment.cancelled` when provider.cancel succeeds (matches the saga's
  own audit pattern for its non-resume cancel sites)

No PII in the audit data — `subject` carries `job_id`/`checkout_id`,
`data` carries reason codes and amounts only.

Idempotency
===========
Re-running resume_in_flight_sagas() on a database where it already ran
is a no-op: the previous run moved every in-flight saga to a terminal
state, and terminal states are filtered out by the WHERE clause. This
matters if the boot itself crashes between resume completing and the
scheduler starting (e.g., out-of-memory) and the operator restarts.

Concurrent-boot caveat
======================
Two server instances booting simultaneously (e.g., during a Render
rolling-restart deploy) would BOTH `fetch` the same in-flight rows.
The per-job advisory lock inside `checkout_store.update` serializes
the actual UPDATE — only one instance moves the saga to its terminal
state — but BOTH instances will still call `audit.emit(...)` for that
saga, producing duplicate audit events. Today LAIGO runs single-
instance on Render so this is theoretical. To make it strict-
idempotent under multi-instance recovery, change the fetch to
`SELECT ... FOR UPDATE SKIP LOCKED` and re-check the saga_status
after acquiring the lock. Deferred — not worth the SQL complexity
until multi-instance becomes a real deploy pattern.

Cancel-succeeded-but-Python-saw-an-error edge case
==================================================
`provider.cancel` can succeed at Stripe (hold released) but raise on
the Python side (network timeout reading the response, SDK bug, etc.).
This code routes to MANUAL_REVIEW, but the hold IS actually canceled.
`payment_holds.last_known_status` stays at `requires_capture` until
the future `reconcile_orphan_holds` (Phase E step 2) re-queries Stripe
and updates it to `canceled`. Operator runbook for MANUAL_REVIEW
sagas must mention: check Stripe dashboard before assuming the hold
is still authorized.

Pre-existing gap (documented, not fixed by Phase E)
====================================================
The saga's create_hold → record_hold sequence has a small window where
a process crash leaves a Stripe hold orphaned with no corresponding
`payment_holds` row AND `sagas.saga_status='initiated'` (still pre-
hold). `resume_in_flight_sagas` sees `initiated` and routes to FAILED.
Stripe's 7-day auto-cancel eventually releases the hold; the customer
sees a confused statement entry for that week. Mitigation requires
querying Stripe by metadata-filter (`metadata.checkout_id = X`) on
resume, which Stripe's API doesn't currently support. Phase E reduces
this window's IMPACT by ensuring crashes AFTER record_hold but BEFORE
the sagas STRIPE_HELD update leave a payment_holds row visible to the
reconciler — but the create_hold → record_hold window itself remains.

Backend gating
==============
- No-op when `DB_BACKEND != postgres`. The JSON-mode runtime has no
  sagas table to query. Local dev with `DB_BACKEND=json` boots without
  this function doing anything.
- Phase F cutover keeps `DB_BACKEND=postgres` and this function fires
  on every restart from that point onward.
"""

import logging
from typing import Optional

from . import audit
from ._cancel_helpers import cancel_hold_with_retry
from . import checkout_store_dispatch as checkout_store
from . import payment_holds_store
from .models import ERROR_MESSAGES, HoldDisposition, SagaStatus
from .payment import registry as payment_registry
from .payment.base import PaymentProviderUnavailable
from ..db import get_pool, is_postgres_backend

logger = logging.getLogger("laigo.saga_resume")


# Statuses that need recovery action. Anything not in this list is either
# already terminal (filtered out by WHERE) or a status that shouldn't exist
# (would be a programmer error in saga.py).
_RECOVERABLE_STATUSES = frozenset({
    SagaStatus.INITIATED.value,
    SagaStatus.STRIPE_HELD.value,
    SagaStatus.ORDERS_PLACED.value,
    SagaStatus.FALLBACK_ORDERED.value,
})


async def resume_in_flight_sagas() -> None:
    """Inspect every non-terminal saga and route it to a terminal state.

    Called once during lifespan startup. No-op when DB_BACKEND != postgres.
    Idempotent — safe to re-run if a previous attempt was interrupted.

    Returns None. Logs INFO with the count of examined sagas on success.
    Failures within a single saga's recovery log CRITICAL and continue to
    the next saga (one stuck row must not block boot).
    """
    if not is_postgres_backend():
        return

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT checkout_id, job_id, saga_status, payment_hold_id,
               brickowl_order_ids, lego_order_id, initiated_at
        FROM sagas
        WHERE saga_status NOT IN (
            'payment_captured', 'compensated', 'failed', 'manual_review'
        )
        ORDER BY initiated_at
        """
    )

    failed = 0
    for row in rows:
        state = dict(row)
        try:
            await _route_recovery(state)
        except Exception as exc:
            # Don't let one stuck saga block boot. Log loud enough for
            # operator alerting; the row stays in its current non-terminal
            # status until a manual operator action moves it.
            failed += 1
            logger.critical(
                f"[resume] saga {state.get('checkout_id')} "
                f"(job {state.get('job_id')}) recovery FAILED: "
                f"{type(exc).__name__}: {exc}. "
                "Manual operator action required.",
                exc_info=True,
            )

    # Single summary line — operators reading boot logs see both the total
    # and the failure subset at a glance. CRITICAL stack traces above tell
    # them which sagas need attention.
    if failed:
        logger.warning(
            f"[resume] examined {len(rows)} in-flight sagas; "
            f"{failed} recovery FAILED (see CRITICAL lines above)"
        )
    else:
        logger.info(f"[resume] examined {len(rows)} in-flight sagas (all routed cleanly)")


async def _route_recovery(state: dict) -> None:
    """Route a single non-terminal saga to a terminal state.

    Caller MUST have already filtered out terminal sagas. This function
    will silently no-op on an unknown saga_status (defensive; would
    indicate a schema/code drift bug).
    """
    job_id = state["job_id"]
    checkout_id = state["checkout_id"]
    last_status = state["saga_status"]

    if last_status not in _RECOVERABLE_STATUSES:
        # Should be unreachable given the WHERE clause + enum constraint,
        # but log+skip is safer than ValueError.
        logger.warning(
            f"[resume] saga {checkout_id} has unknown saga_status "
            f"{last_status!r}; skipping. Schema/code drift?"
        )
        return

    if last_status == SagaStatus.INITIATED.value:
        await _recover_initiated(job_id, checkout_id)
        return

    if last_status == SagaStatus.STRIPE_HELD.value:
        await _recover_stripe_held(job_id, checkout_id, state)
        return

    # ORDERS_PLACED or FALLBACK_ORDERED — orders are real, capture state
    # cannot be inferred from a clean boot. MANUAL_REVIEW is the only
    # safe terminal.
    await _recover_orders_placed(job_id, checkout_id, state, last_status)


async def _recover_initiated(job_id: str, checkout_id: str) -> None:
    """initiated -> FAILED. No hold, no orders, no money moved."""
    reason = "Saga abandoned by process restart before payment hold"
    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.FAILED.value,
        "error": reason,
        # No customer_message here — the customer's /confirm POST raised a
        # 5xx (process died mid-handling) and they already saw a retry
        # prompt. Setting a customer_message would surface "your order is
        # being reviewed" on a /status poll for a customer who hasn't even
        # been billed.
    })
    await audit.emit(
        "saga.failed",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={"reason": "resume.initiated_abandoned"},
    )
    logger.info(f"[resume] {checkout_id}: initiated -> failed ({reason})")


async def _recover_stripe_held(job_id: str, checkout_id: str, state: dict) -> None:
    """stripe_held -> cancel hold, then FAILED. MANUAL_REVIEW on cancel failure.

    Three failure modes that route to MANUAL_REVIEW:
      (a) No PaymentProvider registered (gate is closed at boot).
      (b) Provider exists but cancel() raises (network, API key, etc.).
      (c) Hold ID is missing from the saga row (data corruption).

    All three leave the Stripe hold authorized (or in unknown state).
    Operator must reconcile via the Stripe dashboard.
    """
    hold_id: Optional[str] = state.get("payment_hold_id")
    if not hold_id:
        # Saga row says stripe_held but no hold_id. Either a schema/code
        # drift bug or a partial write that committed the status flip but
        # not the id. Treat as MANUAL_REVIEW so operator inspects.
        reason = (
            f"Saga was in stripe_held but has no payment_hold_id. "
            "Inspect sagas row directly; if Stripe has a hold for the "
            "checkout_id, cancel it from the dashboard."
        )
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW.value,
            "manual_review_reason": reason,
            "error": "Resume: stripe_held with no hold_id",
            "customer_message": ERROR_MESSAGES["manual_review"],
            # B55 — no hold_id in our state. We don't know if Stripe has a
            # hold to dispose. NULL is the conservative default (reconciler
            # won't touch). If a hold exists at Stripe under this checkout_id,
            # the reconciler will only find it via payment_holds; if that row
            # exists, the reconciler will use its OWN saga-status branch.
            "hold_disposition": None,
        })
        await audit.emit(
            "saga.manual_review",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={"reason": "resume.stripe_held_no_hold_id"},
        )
        logger.critical(f"[resume] {checkout_id}: {reason}")
        return

    # Try to get the provider. If the gate has closed between the saga
    # being created and this boot (operator flipped CHECKOUT_ENABLED off,
    # Stripe key was removed, etc.), the registry is empty.
    try:
        provider = payment_registry.get_active()
    except PaymentProviderUnavailable as exc:
        reason = (
            f"Resume-on-startup: hold {hold_id} requires cancellation but no "
            f"PaymentProvider is registered ({exc}). Cancel from Stripe dashboard."
        )
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW.value,
            "manual_review_reason": reason,
            "error": f"No provider for resume cancel: {exc}",
            "customer_message": ERROR_MESSAGES["manual_review"],
            # B55 — hold authorized; runbook says "cancel from Stripe dashboard".
            # CANCEL_SAFE because once the provider re-registers (operator fixes
            # the env), the reconciler's next tick can release the hold without
            # operator intervention.
            "hold_disposition": HoldDisposition.CANCEL_SAFE,
        })
        await audit.emit(
            "saga.manual_review",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={"reason": "resume.no_provider", "hold_id": hold_id},
        )
        logger.critical(f"[resume] {checkout_id}: {reason}")
        return

    # B57 — use the retry helper so a transient Stripe error during boot
    # recovery doesn't escalate to MANUAL_REVIEW prematurely. The helper
    # handles audit emit (`payment.cancelled` with reason="resume.restart")
    # and `payment_holds_store.mark_status('canceled')` on success.
    cancel_succeeded, cancel_error = await cancel_hold_with_retry(
        provider=provider,
        checkout_id=checkout_id,
        hold_id=hold_id,
        audit_reason="resume.restart",
        audit_subject={"job_id": job_id, "checkout_id": checkout_id},
    )

    if not cancel_succeeded:
        # Retries exhausted (~21s). Mark MANUAL_REVIEW with cancel_safe
        # disposition so the reconciler is the next safety net (B55).
        reason = (
            f"Resume-on-startup: hold {hold_id} could not be cancelled "
            f"after retries ({cancel_error}). Cancel in Stripe dashboard."
        )
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW.value,
            "manual_review_reason": reason,
            "error": f"Resume cancel failed: {cancel_error}",
            "customer_message": ERROR_MESSAGES["manual_review"],
            # B55 — runbook says "Cancel in Stripe dashboard". With the B57
            # retry helper above, reaching this branch means retries already
            # exhausted; reconciler is the next safety net.
            "hold_disposition": HoldDisposition.CANCEL_SAFE,
        })
        await audit.emit(
            "saga.manual_review",
            subject={"job_id": job_id, "checkout_id": checkout_id},
            data={
                "reason": "resume.cancel_failed",
                "hold_id": hold_id,
                "cancel_error": cancel_error,
            },
        )
        logger.critical(f"[resume] {checkout_id}: {reason}")
        return

    # Cancel succeeded — hold released, no money moved.
    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.FAILED.value,
        "error": "Resumed after restart; hold released",
        "customer_message": ERROR_MESSAGES["timeout"],
    })
    await audit.emit(
        "saga.failed",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        # Include hold_id so operator forensic queries can correlate
        # `saga.failed` rows with `payment.cancelled` rows by hold_id —
        # otherwise the only join is checkout_id, which loses the link to
        # the actual Stripe object.
        data={"reason": "resume.stripe_held_cancelled", "hold_id": hold_id},
    )
    logger.info(
        f"[resume] {checkout_id}: stripe_held -> failed (hold {hold_id} cancelled)"
    )


async def _recover_orders_placed(
    job_id: str, checkout_id: str, state: dict, last_status: str
) -> None:
    """orders_placed / fallback_ordered -> MANUAL_REVIEW.

    Real marketplace orders are out. The capture state of the Stripe hold
    cannot be inferred from a clean boot (the saga might have completed
    capture and died before writing the status; or it might have died
    before capture and the hold is still authorized). The only safe
    terminal is MANUAL_REVIEW with a verbose runbook for the operator.
    """
    hold_id = state.get("payment_hold_id")
    brickowl_orders = state.get("brickowl_order_ids") or []
    lego_order = state.get("lego_order_id")

    reason = (
        f"Saga was in {last_status} when the process restarted. Operator must: "
        f"(1) check Stripe for hold {hold_id!r} status; "
        f"(2) verify each BrickOwl order in {brickowl_orders} actually shipped; "
        f"(3) verify LEGO order {lego_order!r} actually placed; "
        "(4) reconcile: capture remaining hold OR refund placed orders."
    )
    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.MANUAL_REVIEW.value,
        "manual_review_reason": reason,
        "error": "Process restart with orders placed",
        "customer_message": ERROR_MESSAGES["manual_review"],
        # B55 — runbook offers capture-OR-refund (item 4 above). Reconciler
        # MUST NOT auto-cancel; orders are real and operator may want to
        # capture for them.
        "hold_disposition": HoldDisposition.OPERATOR_DECIDES,
    })
    await audit.emit(
        "saga.manual_review",
        subject={"job_id": job_id, "checkout_id": checkout_id},
        data={
            "reason": "resume.orders_placed",
            "last_status": last_status,
            "hold_id": hold_id,
            "brickowl_order_count": len(brickowl_orders),
            "lego_order_id": lego_order,
        },
    )
    logger.critical(
        f"[resume] {checkout_id}: {last_status} -> manual_review ({reason})"
    )
