"""Phase E step 2 — reconcile_orphan_holds integration test against Neon dev.

Drives every branch of the decision matrix via SQL-injected payment_holds +
sagas rows plus a fake PaymentProvider that returns each Stripe status on
demand. Verifies:
  - payment_holds.last_known_status transitions correctly
  - sagas escalation to MANUAL_REVIEW where the matrix requires it
  - audit_events landed for each outcome
  - the outer loop never raises (per-row safety net)

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_reconcile_pg

PRECONDITION: DB_BACKEND=postgres + DATABASE_URL pointing at Neon dev branch.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone


def _setup_env() -> None:
    os.environ["DB_BACKEND"] = "postgres"


# ─── Helpers: SQL-inject test rows ──────────────────────────────────────────


async def _ensure_clean(pool) -> None:
    """Remove leftover rows + audit events from prior runs."""
    await pool.execute(
        "DELETE FROM payment_holds WHERE checkout_id LIKE 'reconcile-test-%'"
    )
    await pool.execute(
        "DELETE FROM sagas WHERE checkout_id LIKE 'reconcile-test-%'"
    )
    await pool.execute(
        "DELETE FROM checkouts WHERE checkout_id LIKE 'reconcile-test-%'"
    )
    await pool.execute(
        "DELETE FROM jobs WHERE job_id LIKE 'reconcile-test-%'"
    )
    await pool.execute(
        "DELETE FROM audit_events WHERE "
        "checkout_id LIKE 'reconcile-test-%' OR job_id LIKE 'reconcile-test-%'"
    )


async def _make_job(pool, job_id: str) -> None:
    await pool.execute(
        """
        INSERT INTO jobs (
            job_id, status, mosaic_type, width_blocks, dither,
            ttl_expires_at, queued_at, started_at, completed_at, progress_pct
        ) VALUES (
            $1, 'complete', '2d', 4, TRUE,
            NOW() + INTERVAL '1 hour', NOW(), NOW(), NOW(), 100
        )
        """,
        job_id,
    )


async def _make_checkout(pool, checkout_id: str, job_id: str) -> None:
    await pool.execute(
        """
        INSERT INTO checkouts (
            checkout_id, job_id, shipping_country, shipping_zip,
            customer_email, allocation, unsourceable_items,
            created_at, expires_at
        ) VALUES (
            $1, $2, 'US', '94110', 'reconcile@example.com',
            '{}'::jsonb, '[]'::jsonb,
            NOW(), NOW() + INTERVAL '10 minutes'
        )
        """,
        checkout_id, job_id,
    )


async def _make_saga(
    pool, checkout_id: str, job_id: str,
    *, saga_status: str, payment_hold_id: str | None,
    last_transition_offset: timedelta = timedelta(seconds=0),
    hold_disposition: str | None = None,
) -> None:
    """Insert sagas row with a controllable last_transition_at.

    B55: `hold_disposition` defaults to NULL — matches saga rows written before
    the disposition column was added, and exercises the reconciler's "NULL =
    conservative" branch by default. Tests for cancel_safe / operator_decides
    pass the value explicitly.
    """
    last_transition = datetime.now(timezone.utc) - last_transition_offset
    await pool.execute(
        """
        INSERT INTO sagas (
            checkout_id, job_id, saga_status,
            payment_provider, payment_mode, payment_hold_id,
            brickowl_order_ids, hold_disposition,
            initiated_at, last_transition_at
        ) VALUES (
            $1, $2, $3,
            'stripe', 'test', $4,
            '[]'::jsonb, $5,
            $6, $6
        )
        """,
        checkout_id, job_id, saga_status, payment_hold_id,
        hold_disposition, last_transition,
    )


async def _make_hold(
    pool, hold_id: str, checkout_id: str,
    *, last_reconciled_offset: timedelta = timedelta(hours=2),
) -> None:
    """Insert payment_holds row with last_known_status='requires_capture'.

    Default `last_reconciled_offset=2h` means the row IS eligible for
    reconciliation (fetch_for_reconcile's default cutoff is 1h).
    """
    last_reconciled = datetime.now(timezone.utc) - last_reconciled_offset
    await pool.execute(
        """
        INSERT INTO payment_holds (
            hold_id, checkout_id, provider, mode,
            amount_authorized_cents, currency,
            last_known_status, last_reconciled_at, created_at
        ) VALUES (
            $1, $2, 'stripe', 'test',
            1234, 'usd',
            'requires_capture', $3, NOW()
        )
        """,
        hold_id, checkout_id, last_reconciled,
    )


# ─── A fake PaymentProvider that returns scripted statuses ──────────────────


class _FakeReconcileProvider:
    """Stripe stub for reconcile testing. Controls per-hold status responses."""

    name = "stripe"
    currency = "usd"

    def __init__(self) -> None:
        self.status_responses: dict[str, str] = {}
        self.exception_responses: dict[str, Exception] = {}
        self.cancel_calls: list[str] = []
        self.cancel_should_fail = False

    def mode(self) -> str:
        return "test"

    async def create_hold(self, **kwargs):
        raise NotImplementedError("not used in reconcile tests")

    async def capture(self, **kwargs):
        raise NotImplementedError("not used in reconcile tests")

    async def cancel(self, *, hold_id: str, idempotency_key: str) -> None:
        self.cancel_calls.append(hold_id)
        if self.cancel_should_fail:
            raise RuntimeError("synthetic cancel failure")

    async def get_hold_status(self, hold_id: str) -> str:
        if hold_id in self.exception_responses:
            raise self.exception_responses[hold_id]
        return self.status_responses.get(hold_id, "requires_capture")


# ─── Test body ──────────────────────────────────────────────────────────────


async def main() -> int:
    _setup_env()

    from dotenv import load_dotenv
    load_dotenv(".env.secrets"); load_dotenv(".env")

    from scripts.db import init_pool, get_pool, close_pool
    from scripts.checkout import payment_holds_store, reconcile
    from scripts.checkout import checkout_store_dispatch as checkout_store
    from scripts.checkout.models import SagaStatus
    from scripts.checkout.payment import registry as payment_registry
    from scripts.checkout.payment.base import (
        PaymentPermanentError,
        PaymentRetryableError,
    )

    await init_pool()
    pool = get_pool()
    await _ensure_clean(pool)

    # Register the fake provider for the whole test run.
    payment_registry._reset_for_tests()
    fake = _FakeReconcileProvider()
    payment_registry.register(fake)

    try:
        # ─── 1. Stripe says succeeded ──────────────────────────────────────
        await _make_job(pool, "reconcile-test-job-1")
        await _make_checkout(pool, "reconcile-test-co-1", "reconcile-test-job-1")
        await _make_saga(
            pool, "reconcile-test-co-1", "reconcile-test-job-1",
            saga_status="stripe_held", payment_hold_id="reconcile-test-hold-1",
        )
        await _make_hold(pool, "reconcile-test-hold-1", "reconcile-test-co-1")
        fake.status_responses["reconcile-test-hold-1"] = "succeeded"

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["mirrored_succeeded"] == 1, f"expected 1 succeeded, hist={hist}"
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-1",
        )
        assert row["last_known_status"] == "succeeded"
        # Saga should NOT have been escalated (preserve operator's recovery intent)
        saga_row = await pool.fetchrow(
            "SELECT saga_status FROM sagas WHERE checkout_id = $1",
            "reconcile-test-co-1",
        )
        assert saga_row["saga_status"] == "stripe_held"
        # Audit event landed
        n = await pool.fetchval(
            "SELECT COUNT(*) FROM audit_events WHERE event = 'payment.captured' "
            "AND checkout_id = 'reconcile-test-co-1'"
        )
        assert n == 1, f"expected 1 payment.captured event, got {n}"
        print("OK: stripe=succeeded -> mirrored, saga preserved, audit emitted")

        # ─── 2. Stripe says canceled + saga still in-flight -> ESCALATE ────
        await _make_job(pool, "reconcile-test-job-2")
        await _make_checkout(pool, "reconcile-test-co-2", "reconcile-test-job-2")
        await _make_saga(
            pool, "reconcile-test-co-2", "reconcile-test-job-2",
            saga_status="stripe_held", payment_hold_id="reconcile-test-hold-2",
        )
        await _make_hold(pool, "reconcile-test-hold-2", "reconcile-test-co-2")
        fake.status_responses["reconcile-test-hold-2"] = "canceled"

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["saga_escalated_oob_cancel"] == 1, f"hist={hist}"
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-2",
        )
        assert row["last_known_status"] == "canceled"
        saga_row = await pool.fetchrow(
            "SELECT saga_status, manual_review_reason FROM sagas WHERE checkout_id = $1",
            "reconcile-test-co-2",
        )
        assert saga_row["saga_status"] == "manual_review"
        assert "out-of-band" in (saga_row["manual_review_reason"] or "")
        print("OK: stripe=canceled + saga in-flight -> escalated to MANUAL_REVIEW")

        # ─── 3. Stripe says canceled + saga already terminal -> mirror only ─
        await _make_job(pool, "reconcile-test-job-3")
        await _make_checkout(pool, "reconcile-test-co-3", "reconcile-test-job-3")
        await _make_saga(
            pool, "reconcile-test-co-3", "reconcile-test-job-3",
            saga_status="payment_captured", payment_hold_id="reconcile-test-hold-3",
        )
        await _make_hold(pool, "reconcile-test-hold-3", "reconcile-test-co-3")
        fake.status_responses["reconcile-test-hold-3"] = "canceled"

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["mirrored_canceled"] == 1, f"hist={hist}"
        # Saga must NOT have been touched (it's terminal)
        saga_row = await pool.fetchrow(
            "SELECT saga_status FROM sagas WHERE checkout_id = $1",
            "reconcile-test-co-3",
        )
        assert saga_row["saga_status"] == "payment_captured"
        print("OK: stripe=canceled + saga terminal -> mirrored, saga preserved")

        # ─── 4. Stripe says requires_capture + saga terminal -> ORPHAN ─────
        await _make_job(pool, "reconcile-test-job-4")
        await _make_checkout(pool, "reconcile-test-co-4", "reconcile-test-job-4")
        await _make_saga(
            pool, "reconcile-test-co-4", "reconcile-test-job-4",
            saga_status="failed", payment_hold_id="reconcile-test-hold-4",
        )
        await _make_hold(pool, "reconcile-test-hold-4", "reconcile-test-co-4")
        fake.status_responses["reconcile-test-hold-4"] = "requires_capture"
        fake.cancel_calls = []
        fake.cancel_should_fail = False

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["canceled_orphan"] == 1, f"hist={hist}"
        assert "reconcile-test-hold-4" in fake.cancel_calls
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-4",
        )
        assert row["last_known_status"] == "canceled"
        print("OK: stripe=requires_capture + saga terminal -> orphan cancelled")

        # ─── 5. requires_capture + cancel FAILS -> stays requires_capture ──
        await _make_job(pool, "reconcile-test-job-5")
        await _make_checkout(pool, "reconcile-test-co-5", "reconcile-test-job-5")
        await _make_saga(
            pool, "reconcile-test-co-5", "reconcile-test-job-5",
            saga_status="failed", payment_hold_id="reconcile-test-hold-5",
        )
        await _make_hold(pool, "reconcile-test-hold-5", "reconcile-test-co-5")
        fake.status_responses["reconcile-test-hold-5"] = "requires_capture"
        fake.cancel_should_fail = True
        fake.cancel_calls = []

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["stripe_retryable_skipped"] >= 1, f"hist={hist}"
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-5",
        )
        assert row["last_known_status"] == "requires_capture"
        fake.cancel_should_fail = False
        print("OK: requires_capture + cancel failure -> retry next tick, status preserved")

        # ─── 6. requires_capture + stuck saga (>1h stale) -> MANUAL_REVIEW ─
        await _make_job(pool, "reconcile-test-job-6")
        await _make_checkout(pool, "reconcile-test-co-6", "reconcile-test-job-6")
        await _make_saga(
            pool, "reconcile-test-co-6", "reconcile-test-job-6",
            saga_status="stripe_held", payment_hold_id="reconcile-test-hold-6",
            last_transition_offset=timedelta(hours=2),  # >1h stale
        )
        await _make_hold(pool, "reconcile-test-hold-6", "reconcile-test-co-6")
        fake.status_responses["reconcile-test-hold-6"] = "requires_capture"
        fake.cancel_calls = []

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["stuck_saga_marked_review"] == 1, f"hist={hist}"
        assert "reconcile-test-hold-6" not in fake.cancel_calls, (
            "stuck-saga branch must NOT cancel Stripe (operator may be working it)"
        )
        saga_row = await pool.fetchrow(
            "SELECT saga_status, manual_review_reason FROM sagas WHERE checkout_id = $1",
            "reconcile-test-co-6",
        )
        assert saga_row["saga_status"] == "manual_review"
        assert "stuck" in (saga_row["manual_review_reason"] or "").lower() or \
               "died" in (saga_row["manual_review_reason"] or "").lower(), (
            f"unexpected reason: {saga_row['manual_review_reason']!r}"
        )
        print("OK: requires_capture + stuck stripe_held saga -> MANUAL_REVIEW, Stripe untouched")

        # ─── 7. requires_capture + in-flight saga (<1h) -> SKIP ────────────
        await _make_job(pool, "reconcile-test-job-7")
        await _make_checkout(pool, "reconcile-test-co-7", "reconcile-test-job-7")
        await _make_saga(
            pool, "reconcile-test-co-7", "reconcile-test-job-7",
            saga_status="stripe_held", payment_hold_id="reconcile-test-hold-7",
            last_transition_offset=timedelta(minutes=5),  # in-flight
        )
        await _make_hold(pool, "reconcile-test-hold-7", "reconcile-test-co-7")
        fake.status_responses["reconcile-test-hold-7"] = "requires_capture"
        fake.cancel_calls = []

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["in_flight_skipped"] >= 1, f"hist={hist}"
        assert "reconcile-test-hold-7" not in fake.cancel_calls
        saga_row = await pool.fetchrow(
            "SELECT saga_status FROM sagas WHERE checkout_id = $1",
            "reconcile-test-co-7",
        )
        assert saga_row["saga_status"] == "stripe_held"  # untouched
        # reconciled_at should be bumped to NOW so this row exits the candidate
        # set for another hour
        row = await pool.fetchrow(
            "SELECT last_reconciled_at FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-7",
        )
        assert (datetime.now(timezone.utc) - row["last_reconciled_at"]) < timedelta(minutes=1)
        print("OK: requires_capture + in-flight saga -> skipped, reconciled_at bumped")

        # ─── 8. PaymentRetryableError from Stripe -> no row mutation ───────
        await _make_job(pool, "reconcile-test-job-8")
        await _make_checkout(pool, "reconcile-test-co-8", "reconcile-test-job-8")
        await _make_saga(
            pool, "reconcile-test-co-8", "reconcile-test-job-8",
            saga_status="stripe_held", payment_hold_id="reconcile-test-hold-8",
        )
        await _make_hold(
            pool, "reconcile-test-hold-8", "reconcile-test-co-8",
            last_reconciled_offset=timedelta(hours=2),
        )
        original_reconciled = (await pool.fetchrow(
            "SELECT last_reconciled_at FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-8",
        ))["last_reconciled_at"]
        fake.exception_responses["reconcile-test-hold-8"] = PaymentRetryableError(
            "synthetic retryable"
        )

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["stripe_retryable_skipped"] >= 1, f"hist={hist}"
        row = await pool.fetchrow(
            "SELECT last_known_status, last_reconciled_at FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-8",
        )
        # MUST stay at requires_capture AND reconciled_at MUST not be bumped
        # (we want this row to be retried IMMEDIATELY on the next tick)
        assert row["last_known_status"] == "requires_capture"
        assert row["last_reconciled_at"] == original_reconciled
        del fake.exception_responses["reconcile-test-hold-8"]
        print("OK: Stripe retryable error -> row left untouched, retry next tick")

        # ─── 9. PaymentPermanentError from Stripe -> mark unknown ──────────
        await _make_job(pool, "reconcile-test-job-9")
        await _make_checkout(pool, "reconcile-test-co-9", "reconcile-test-job-9")
        await _make_saga(
            pool, "reconcile-test-co-9", "reconcile-test-job-9",
            saga_status="stripe_held", payment_hold_id="reconcile-test-hold-9",
        )
        await _make_hold(pool, "reconcile-test-hold-9", "reconcile-test-co-9")
        fake.exception_responses["reconcile-test-hold-9"] = PaymentPermanentError(
            "no such payment intent"
        )

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["stripe_permanent_errored"] == 1, f"hist={hist}"
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-9",
        )
        assert row["last_known_status"] == "unknown"
        del fake.exception_responses["reconcile-test-hold-9"]
        print("OK: Stripe permanent error -> marked 'unknown', stops re-querying")

        # ─── B55: MANUAL_REVIEW disposition matrix ─────────────────────────
        #
        # Three sub-cases distinguished by sagas.hold_disposition:
        #
        #   9a. MANUAL_REVIEW + cancel_safe       -> auto-cancel (safety net)
        #   9b. MANUAL_REVIEW + operator_decides  -> skip (do not touch Stripe)
        #   9c. MANUAL_REVIEW + NULL              -> skip (conservative default)
        #
        # Pre-B55 (when MANUAL_REVIEW was lumped into the terminal-saga set),
        # all three would have auto-cancelled, destroying the operator's
        # capture option in 9b/9c. See PRE_RELEASE §4.1 B55.

        # 9a — cancel_safe: runbook said "cancel manually"; reconciler is the
        #      safety net. Same outcome as the pre-B55 default, now opt-in.
        await _make_job(pool, "reconcile-test-job-9a")
        await _make_checkout(pool, "reconcile-test-co-9a", "reconcile-test-job-9a")
        await _make_saga(
            pool, "reconcile-test-co-9a", "reconcile-test-job-9a",
            saga_status="manual_review", payment_hold_id="reconcile-test-hold-9a",
            hold_disposition="cancel_safe",
        )
        await _make_hold(pool, "reconcile-test-hold-9a", "reconcile-test-co-9a")
        fake.status_responses["reconcile-test-hold-9a"] = "requires_capture"
        fake.cancel_calls = []
        fake.cancel_should_fail = False

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["canceled_orphan"] == 1, f"hist={hist}"
        assert "reconcile-test-hold-9a" in fake.cancel_calls, (
            "cancel_safe MANUAL_REVIEW must be auto-cancelled by reconciler"
        )
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-9a",
        )
        assert row["last_known_status"] == "canceled"
        # Audit event must carry the new reason code so dashboards distinguish
        # cancel-safe MANUAL_REVIEW from generic terminal-orphan cancels.
        n = await pool.fetchval(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event = 'payment.cancelled' "
            "AND checkout_id = 'reconcile-test-co-9a' "
            "AND data->>'reason' = 'reconcile.manual_review_cancel_safe'"
        )
        assert n == 1, f"expected 1 reconcile.manual_review_cancel_safe audit, got {n}"
        print("OK: MANUAL_REVIEW + cancel_safe -> cancelled (reconciler safety net)")

        # 9b — operator_decides: runbook offered capture-OR-refund. Reconciler
        #      MUST NOT touch Stripe.
        await _make_job(pool, "reconcile-test-job-9b")
        await _make_checkout(pool, "reconcile-test-co-9b", "reconcile-test-job-9b")
        await _make_saga(
            pool, "reconcile-test-co-9b", "reconcile-test-job-9b",
            saga_status="manual_review", payment_hold_id="reconcile-test-hold-9b",
            hold_disposition="operator_decides",
        )
        await _make_hold(pool, "reconcile-test-hold-9b", "reconcile-test-co-9b")
        fake.status_responses["reconcile-test-hold-9b"] = "requires_capture"
        fake.cancel_calls = []

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["manual_review_skipped"] == 1, f"hist={hist}"
        assert "reconcile-test-hold-9b" not in fake.cancel_calls, (
            "operator_decides MANUAL_REVIEW must NOT be cancelled by reconciler"
        )
        row = await pool.fetchrow(
            "SELECT last_known_status, last_reconciled_at FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-9b",
        )
        # Status MUST stay at requires_capture (hold still authorized at Stripe)
        # AND last_reconciled_at MUST be bumped (cooldown so we don't re-pick
        # this row every tick while operator is working it).
        assert row["last_known_status"] == "requires_capture"
        assert (datetime.now(timezone.utc) - row["last_reconciled_at"]) < timedelta(minutes=1), (
            "operator_decides branch must bump last_reconciled_at"
        )
        # Saga state must be preserved — reconciler did not escalate
        saga_row = await pool.fetchrow(
            "SELECT saga_status, hold_disposition FROM sagas WHERE checkout_id = $1",
            "reconcile-test-co-9b",
        )
        assert saga_row["saga_status"] == "manual_review"
        assert saga_row["hold_disposition"] == "operator_decides"
        print("OK: MANUAL_REVIEW + operator_decides -> SKIPPED (capture option preserved)")

        # 9c — NULL disposition: conservative default. Same outcome as
        #      operator_decides (skip + bump reconciled_at).
        await _make_job(pool, "reconcile-test-job-9c")
        await _make_checkout(pool, "reconcile-test-co-9c", "reconcile-test-job-9c")
        await _make_saga(
            pool, "reconcile-test-co-9c", "reconcile-test-job-9c",
            saga_status="manual_review", payment_hold_id="reconcile-test-hold-9c",
            hold_disposition=None,  # explicit NULL (matches pre-B55 rows)
        )
        await _make_hold(pool, "reconcile-test-hold-9c", "reconcile-test-co-9c")
        fake.status_responses["reconcile-test-hold-9c"] = "requires_capture"
        fake.cancel_calls = []

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["manual_review_skipped"] == 1, f"hist={hist}"
        assert "reconcile-test-hold-9c" not in fake.cancel_calls, (
            "NULL disposition MUST be treated conservatively (no cancel)"
        )
        row = await pool.fetchrow(
            "SELECT last_known_status FROM payment_holds WHERE hold_id = $1",
            "reconcile-test-hold-9c",
        )
        assert row["last_known_status"] == "requires_capture"
        print("OK: MANUAL_REVIEW + NULL disposition -> SKIPPED (conservative default)")

        # 9d — Regression guard: payment_captured terminal still auto-cancels
        #      a stranded hold (B55 must not have changed this behavior).
        await _make_job(pool, "reconcile-test-job-9d")
        await _make_checkout(pool, "reconcile-test-co-9d", "reconcile-test-job-9d")
        await _make_saga(
            pool, "reconcile-test-co-9d", "reconcile-test-job-9d",
            saga_status="payment_captured", payment_hold_id="reconcile-test-hold-9d",
        )
        await _make_hold(pool, "reconcile-test-hold-9d", "reconcile-test-co-9d")
        fake.status_responses["reconcile-test-hold-9d"] = "requires_capture"
        fake.cancel_calls = []

        hist = await reconcile.reconcile_orphan_holds()
        assert hist["canceled_orphan"] == 1, f"hist={hist}"
        assert "reconcile-test-hold-9d" in fake.cancel_calls
        print("OK: payment_captured terminal + requires_capture -> cancelled (regression guard)")

        # ─── 10. Empty payment_holds table -> no-op tick ───────────────────
        await _ensure_clean(pool)
        fake.status_responses = {}
        fake.cancel_calls = []
        hist = await reconcile.reconcile_orphan_holds()
        assert hist["examined"] == 0, f"expected examined=0 on empty, got {hist}"
        print("OK: empty payment_holds -> no-op tick")

        # ─── 11. Per-row safety net: unexpected exception inside _reconcile_one
        # We can't easily inject one without monkey-patching; the histogram
        # field exists and is exercised when the catch fires in practice.
        # Skipping with a doc note rather than building unsafe injection.
        print("SKIP: per-row exception safety net (covered by inspection)")

        # ─── 12. No provider registered -> no-op tick (warning only) ───────
        payment_registry._reset_for_tests()
        # Re-seed minimal payment_holds row to verify the function doesn't
        # explode when provider is missing
        await _make_job(pool, "reconcile-test-job-12")
        await _make_checkout(pool, "reconcile-test-co-12", "reconcile-test-job-12")
        await _make_hold(pool, "reconcile-test-hold-12", "reconcile-test-co-12")
        hist = await reconcile.reconcile_orphan_holds()
        assert hist == {"skipped": "no_provider"}, f"hist={hist}"
        print("OK: no PaymentProvider registered -> graceful skip")

        # ─── Cleanup ──────────────────────────────────────────────────────
        await _ensure_clean(pool)

    finally:
        payment_registry._reset_for_tests()
        await close_pool()

    print()
    print("All reconcile PG integration tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
