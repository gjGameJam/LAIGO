"""Phase E integration tests against Neon `dev` branch.

Covers:
  - audit.emit writes to audit_events + handles failures without raising
  - payment_holds_store record_hold + mark_status + fetch_for_reconcile
  - saga_resume routes each saga_status case correctly (with a mock provider)

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_phase_e_pg

PRECONDITION: DB_BACKEND=postgres + DATABASE_URL pointing at Neon dev branch.
This script sets DB_BACKEND=postgres in the process env on entry.

The tests SQL-inject saga + checkout + job rows directly so they don't depend
on the saga's full pipeline. Each test cleans up after itself. Test rows use
a `phase-e-test-` prefix on every id to make cleanup unambiguous and to make
this script safe to re-run.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone


def _setup_env() -> None:
    os.environ["DB_BACKEND"] = "postgres"


async def _ensure_clean(pool) -> None:
    """Remove any leftover phase-e-test rows from a prior run."""
    # Delete in FK-respecting order: payment_holds -> sagas -> checkouts -> jobs.
    # The audit_events table has no FKs and gets cleaned by event prefix.
    await pool.execute(
        "DELETE FROM payment_holds WHERE checkout_id LIKE 'phase-e-test-%'"
    )
    await pool.execute(
        "DELETE FROM sagas WHERE checkout_id LIKE 'phase-e-test-%'"
    )
    await pool.execute(
        "DELETE FROM checkouts WHERE checkout_id LIKE 'phase-e-test-%'"
    )
    await pool.execute(
        "DELETE FROM jobs WHERE job_id LIKE 'phase-e-test-%'"
    )
    await pool.execute(
        "DELETE FROM audit_events WHERE event LIKE 'phase-e-test.%' "
        "   OR checkout_id LIKE 'phase-e-test-%' "
        "   OR job_id LIKE 'phase-e-test-%'"
    )


async def _make_job(pool, job_id: str) -> None:
    """Insert a minimal completed jobs row so checkouts/sagas FKs resolve."""
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
    """Insert a minimal checkouts row referencing the job."""
    await pool.execute(
        """
        INSERT INTO checkouts (
            checkout_id, job_id, shipping_country, shipping_zip,
            customer_email, allocation, unsourceable_items,
            created_at, expires_at
        ) VALUES (
            $1, $2, 'US', '94110', 'phase-e@example.com',
            '{}'::jsonb, '[]'::jsonb,
            NOW(), NOW() + INTERVAL '10 minutes'
        )
        """,
        checkout_id, job_id,
    )


async def _make_saga(
    pool, checkout_id: str, job_id: str,
    *, saga_status: str, payment_hold_id: str | None = None,
    brickowl_order_ids: list[str] | None = None,
    lego_order_id: str | None = None,
) -> None:
    """Insert a sagas row in the desired state for resume testing."""
    await pool.execute(
        """
        INSERT INTO sagas (
            checkout_id, job_id, saga_status,
            payment_provider, payment_mode, payment_hold_id,
            payment_authorized_cents, total_charged_cents,
            brickowl_order_ids, lego_order_id,
            error_message, customer_message, manual_review_reason,
            initiated_at, last_transition_at, completed_at
        ) VALUES (
            $1, $2, $3,
            'stripe', 'test', $4,
            NULL, NULL,
            $5::jsonb, $6,
            NULL, NULL, NULL,
            NOW(), NOW(), NULL
        )
        """,
        checkout_id, job_id, saga_status, payment_hold_id,
        brickowl_order_ids or [],
        lego_order_id,
    )


# ─── A fake PaymentProvider for testing resume's stripe_held branch ─────────

class _FakeHoldProvider:
    """Minimal PaymentProvider stub — only `cancel` is exercised by tests."""

    name = "stripe"
    currency = "usd"
    cancel_calls: list[str] = []
    cancel_should_fail = False

    def mode(self) -> str:
        return "test"

    async def create_hold(self, **kwargs):
        raise NotImplementedError("not used in resume tests")

    async def capture(self, **kwargs):
        raise NotImplementedError("not used in resume tests")

    async def cancel(self, *, hold_id: str, idempotency_key: str) -> None:
        self.cancel_calls.append(hold_id)
        if self.cancel_should_fail:
            raise RuntimeError("synthetic cancel failure")


async def main() -> int:
    _setup_env()

    from dotenv import load_dotenv
    load_dotenv(".env.secrets"); load_dotenv(".env")

    from scripts.db import init_pool, get_pool, close_pool
    from scripts.checkout import audit, payment_holds_store, saga_resume
    from scripts.checkout import checkout_store_dispatch as checkout_store
    from scripts.checkout.models import SagaStatus
    from scripts.checkout.payment import registry as payment_registry
    from scripts.checkout.payment.base import PaymentHold

    await init_pool()
    pool = get_pool()
    await _ensure_clean(pool)

    # ─── 1. audit.emit writes to audit_events ──────────────────────────────
    await audit.emit(
        "phase-e-test.basic",
        subject={"job_id": "phase-e-test-job-1", "checkout_id": "phase-e-test-co-1"},
        actor={"type": "customer", "ip": "192.0.2.1", "user_agent": "test"},
        data={"reason": "smoke", "count": 1},
    )
    rows = await pool.fetch(
        "SELECT event, job_id, checkout_id, actor_type, actor_ip::text, actor_user_agent, data "
        "FROM audit_events WHERE event = 'phase-e-test.basic'"
    )
    assert len(rows) == 1, f"expected 1 audit row, got {len(rows)}"
    r = rows[0]
    assert r["event"] == "phase-e-test.basic"
    assert r["job_id"] == "phase-e-test-job-1"
    assert r["checkout_id"] == "phase-e-test-co-1"
    assert r["actor_type"] == "customer"
    assert r["actor_ip"] == "192.0.2.1/32"  # PostgreSQL inet always has /32 mask for /32 hosts
    assert r["actor_user_agent"] == "test"
    assert r["data"] == {"reason": "smoke", "count": 1}, f"data mismatch: {r['data']}"
    print("OK: audit.emit writes the full envelope to audit_events")

    # ─── 2. audit.emit handles None subject/actor/data ─────────────────────
    await audit.emit("phase-e-test.minimal")
    r = await pool.fetchrow(
        "SELECT job_id, checkout_id, actor_type, data FROM audit_events "
        "WHERE event = 'phase-e-test.minimal'"
    )
    assert r["job_id"] is None and r["checkout_id"] is None
    assert r["actor_type"] is None
    assert r["data"] == {}
    print("OK: audit.emit with no kwargs writes a minimal envelope")

    # ─── 3. audit.emit never raises (even on bad payload) ──────────────────
    # asyncpg INET will reject "not-an-ip" -> exception inside emit. Must be
    # swallowed; no row written.
    await audit.emit(
        "phase-e-test.bad-ip",
        actor={"type": "customer", "ip": "not-an-ip"},
    )
    n = await pool.fetchval(
        "SELECT COUNT(*) FROM audit_events WHERE event = 'phase-e-test.bad-ip'"
    )
    assert n == 0, f"emit should have swallowed the INET failure, got {n} rows"
    print("OK: audit.emit swallows failures (no row, no raise)")

    # ─── 4. payment_holds_store.record_hold + mark_status ──────────────────
    # Need a checkout row to satisfy FK.
    await _make_job(pool, "phase-e-test-job-2")
    await _make_checkout(pool, "phase-e-test-co-2", "phase-e-test-job-2")
    hold = PaymentHold(
        hold_id="phase-e-test-hold-2",
        amount_authorized_cents=1234,
        currency="usd",
        provider="stripe",
        mode="test",
    )
    await payment_holds_store.record_hold("phase-e-test-co-2", hold)
    row = await pool.fetchrow(
        "SELECT * FROM payment_holds WHERE hold_id = 'phase-e-test-hold-2'"
    )
    assert row is not None
    assert row["amount_authorized_cents"] == 1234
    assert row["currency"] == "usd"
    assert row["last_known_status"] == "requires_capture"
    print("OK: record_hold INSERTs with last_known_status='requires_capture'")

    # Idempotency
    await payment_holds_store.record_hold("phase-e-test-co-2", hold)
    n = await pool.fetchval(
        "SELECT COUNT(*) FROM payment_holds WHERE hold_id = 'phase-e-test-hold-2'"
    )
    assert n == 1, "record_hold must be idempotent (ON CONFLICT DO NOTHING)"
    print("OK: record_hold is idempotent")

    # Status transition
    await payment_holds_store.mark_status("phase-e-test-hold-2", "succeeded")
    row = await pool.fetchrow(
        "SELECT last_known_status FROM payment_holds WHERE hold_id = 'phase-e-test-hold-2'"
    )
    assert row["last_known_status"] == "succeeded"
    print("OK: mark_status transitions to succeeded")

    # Invalid status raises ValueError
    try:
        await payment_holds_store.mark_status("phase-e-test-hold-2", "bogus")
        assert False, "expected ValueError on bogus status"
    except ValueError:
        pass
    print("OK: mark_status rejects invalid status values")

    # mark_status on unknown hold is a clean no-op
    await payment_holds_store.mark_status("phase-e-test-no-such-hold", "succeeded")
    print("OK: mark_status on unknown hold_id is a no-op")

    # ─── 5. fetch_for_reconcile picks up the right rows ───────────────────
    # Reset the test hold to requires_capture and rewind reconciled_at by 2 hours
    await pool.execute(
        "UPDATE payment_holds SET last_known_status='requires_capture', "
        "last_reconciled_at = NOW() - INTERVAL '2 hours' "
        "WHERE hold_id = 'phase-e-test-hold-2'"
    )
    candidates = await payment_holds_store.fetch_for_reconcile(older_than_seconds=3600)
    test_candidates = [c for c in candidates if c["hold_id"] == "phase-e-test-hold-2"]
    assert len(test_candidates) == 1, f"expected our test hold in candidates, got {len(test_candidates)}"
    assert test_candidates[0]["saga_status"] is None, "no saga linked yet — LEFT JOIN should yield NULL"
    print("OK: fetch_for_reconcile picks up old uncaptured holds")

    # ─── 6. saga_resume: initiated -> FAILED ───────────────────────────────
    await _make_job(pool, "phase-e-test-job-init")
    await _make_checkout(pool, "phase-e-test-co-init", "phase-e-test-job-init")
    await _make_saga(
        pool, "phase-e-test-co-init", "phase-e-test-job-init",
        saga_status="initiated",
    )
    await saga_resume.resume_in_flight_sagas()
    state = await checkout_store.load("phase-e-test-job-init")
    assert state["saga_status"] == "failed", f"expected failed, got {state['saga_status']}"
    assert "abandoned by process restart" in (state.get("error") or "")
    # Verify the audit event landed
    n = await pool.fetchval(
        "SELECT COUNT(*) FROM audit_events WHERE event = 'saga.failed' "
        "AND checkout_id = 'phase-e-test-co-init'"
    )
    assert n == 1, f"expected saga.failed audit event, got {n}"
    print("OK: resume routes initiated -> failed + emits saga.failed audit")

    # ─── 7. saga_resume: stripe_held with NO provider -> MANUAL_REVIEW ─────
    payment_registry._reset_for_tests()  # ensure no provider
    await _make_job(pool, "phase-e-test-job-held-noprov")
    await _make_checkout(pool, "phase-e-test-co-held-noprov", "phase-e-test-job-held-noprov")
    await _make_saga(
        pool, "phase-e-test-co-held-noprov", "phase-e-test-job-held-noprov",
        saga_status="stripe_held", payment_hold_id="phase-e-test-hold-noprov",
    )
    await saga_resume.resume_in_flight_sagas()
    state = await checkout_store.load("phase-e-test-job-held-noprov")
    assert state["saga_status"] == "manual_review"
    assert "no PaymentProvider" in (state.get("manual_review_reason") or "")
    print("OK: resume routes stripe_held -> MANUAL_REVIEW when no provider")

    # ─── 8. saga_resume: stripe_held with provider -> cancel + FAILED ──────
    payment_registry._reset_for_tests()
    fake = _FakeHoldProvider()
    fake.cancel_calls = []
    fake.cancel_should_fail = False
    payment_registry.register(fake)

    # Pre-record the hold so we can verify mark_status flows through.
    await _make_job(pool, "phase-e-test-job-held-ok")
    await _make_checkout(pool, "phase-e-test-co-held-ok", "phase-e-test-job-held-ok")
    await payment_holds_store.record_hold(
        "phase-e-test-co-held-ok",
        PaymentHold(
            hold_id="phase-e-test-hold-ok",
            amount_authorized_cents=5000,
            currency="usd",
            provider="stripe",
            mode="test",
        ),
    )
    await _make_saga(
        pool, "phase-e-test-co-held-ok", "phase-e-test-job-held-ok",
        saga_status="stripe_held", payment_hold_id="phase-e-test-hold-ok",
    )
    await saga_resume.resume_in_flight_sagas()
    state = await checkout_store.load("phase-e-test-job-held-ok")
    assert state["saga_status"] == "failed"
    assert "hold released" in (state.get("error") or "")
    assert "phase-e-test-hold-ok" in fake.cancel_calls, "fake provider should have been called"
    # mark_status should have flipped the hold to canceled
    row = await pool.fetchrow(
        "SELECT last_known_status FROM payment_holds WHERE hold_id = 'phase-e-test-hold-ok'"
    )
    assert row["last_known_status"] == "canceled", f"expected canceled, got {row['last_known_status']}"
    print("OK: resume routes stripe_held -> failed via provider.cancel + payment_holds updated")

    # ─── 9. saga_resume: stripe_held + cancel fails -> MANUAL_REVIEW ──────
    payment_registry._reset_for_tests()
    fake = _FakeHoldProvider()
    fake.cancel_should_fail = True
    payment_registry.register(fake)

    await _make_job(pool, "phase-e-test-job-held-fail")
    await _make_checkout(pool, "phase-e-test-co-held-fail", "phase-e-test-job-held-fail")
    await _make_saga(
        pool, "phase-e-test-co-held-fail", "phase-e-test-job-held-fail",
        saga_status="stripe_held", payment_hold_id="phase-e-test-hold-fail",
    )
    await saga_resume.resume_in_flight_sagas()
    state = await checkout_store.load("phase-e-test-job-held-fail")
    assert state["saga_status"] == "manual_review"
    assert "could not be cancelled" in (state.get("manual_review_reason") or "")
    print("OK: resume routes stripe_held -> MANUAL_REVIEW on cancel failure")

    # ─── 10. saga_resume: orders_placed -> MANUAL_REVIEW ──────────────────
    await _make_job(pool, "phase-e-test-job-ord")
    await _make_checkout(pool, "phase-e-test-co-ord", "phase-e-test-job-ord")
    await _make_saga(
        pool, "phase-e-test-co-ord", "phase-e-test-job-ord",
        saga_status="orders_placed",
        payment_hold_id="phase-e-test-hold-ord",
        brickowl_order_ids=["ord_phase_e_1", "ord_phase_e_2"],
        lego_order_id="lego_phase_e_1",
    )
    await saga_resume.resume_in_flight_sagas()
    state = await checkout_store.load("phase-e-test-job-ord")
    assert state["saga_status"] == "manual_review"
    reason = state.get("manual_review_reason") or ""
    assert "orders_placed" in reason
    assert "ord_phase_e_1" in reason and "ord_phase_e_2" in reason
    print("OK: resume routes orders_placed -> MANUAL_REVIEW with full runbook")

    # ─── 11. resume is idempotent (re-running finds nothing) ──────────────
    # All our test sagas are now terminal; resume should find zero in-flight.
    # Insert a sentinel audit event to count baseline, run resume, expect no new events.
    baseline_audit_count = await pool.fetchval(
        "SELECT COUNT(*) FROM audit_events WHERE event IN "
        "('saga.failed', 'saga.manual_review', 'payment.cancelled') "
        "AND checkout_id LIKE 'phase-e-test-%'"
    )
    await saga_resume.resume_in_flight_sagas()
    final_count = await pool.fetchval(
        "SELECT COUNT(*) FROM audit_events WHERE event IN "
        "('saga.failed', 'saga.manual_review', 'payment.cancelled') "
        "AND checkout_id LIKE 'phase-e-test-%'"
    )
    assert final_count == baseline_audit_count, (
        f"re-run should be a no-op; audit count went {baseline_audit_count} -> {final_count}"
    )
    print("OK: resume is idempotent (re-run produces no new audit events)")

    # ─── 12. saga_resume: stripe_held with NO hold_id -> MANUAL_REVIEW ─────
    await _make_job(pool, "phase-e-test-job-held-nohold")
    await _make_checkout(pool, "phase-e-test-co-held-nohold", "phase-e-test-job-held-nohold")
    await _make_saga(
        pool, "phase-e-test-co-held-nohold", "phase-e-test-job-held-nohold",
        saga_status="stripe_held", payment_hold_id=None,
    )
    await saga_resume.resume_in_flight_sagas()
    state = await checkout_store.load("phase-e-test-job-held-nohold")
    assert state["saga_status"] == "manual_review"
    assert "no payment_hold_id" in (state.get("manual_review_reason") or "").lower()
    print("OK: resume routes stripe_held with NULL hold_id -> MANUAL_REVIEW (defensive)")

    # ─── Cleanup ──────────────────────────────────────────────────────────
    payment_registry._reset_for_tests()
    await _ensure_clean(pool)
    await close_pool()

    print()
    print("All Phase E PG integration tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
