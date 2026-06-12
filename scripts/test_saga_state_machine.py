"""Saga state-machine transition tests (section 6.10 item 8).

For each non-terminal state, verify the documented next-state on success and
on failure. Driven against the JSON-backed checkout_store with a fake
PaymentProvider and a monkey-patched lego_client (the only marketplace today
whose `create_order`/`order_from_lego` is wired into the saga's success path -
BrickOwl's `create_order` is still a stub).

Transitions covered:

  None         -> INITIATED           (saga enters; provider acquired)
  INITIATED    -> FAILED              (create_hold permanent failure)
  INITIATED    -> FAILED              (create_hold transient failure)
  STRIPE_HELD  -> COMPENSATED         (LEGO order fails; cancel succeeds)
  STRIPE_HELD  -> MANUAL_REVIEW       (LEGO order fails; hold cancel also fails)
  ORDERS_PLACED -> PAYMENT_CAPTURED   (happy path)
  ORDERS_PLACED -> MANUAL_REVIEW      (capture permanent failure)
  ORDERS_PLACED -> MANUAL_REVIEW      (capture transient - retries exhausted)

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_saga_state_machine

Self-contained: no DB, no network. Uses tempdir for JSON checkout_store.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class _FakeProvider:
    """Configurable PaymentProvider that records calls + lets each method's
    behavior be swapped via attributes set after construction."""

    name = "fake"

    def __init__(self, mode_value: str = "test"):
        self._mode = mode_value
        self.currency = "usd"
        self.hold_behavior = "success"   # success | retryable | permanent
        self.capture_behavior = "success"  # success | retryable | permanent
        self.cancel_behavior = "success"   # success | retryable | permanent
        self.calls = {"create_hold": 0, "capture": 0, "cancel": 0}

    def mode(self) -> str:
        return self._mode

    async def create_hold(self, *, amount_cents, currency, payment_method_id, idempotency_key):
        from scripts.checkout.payment.base import (
            PaymentHold, PaymentRetryableError, PaymentPermanentError,
        )
        self.calls["create_hold"] += 1
        if self.hold_behavior == "retryable":
            raise PaymentRetryableError("simulated transient hold failure")
        if self.hold_behavior == "permanent":
            raise PaymentPermanentError("simulated card decline")
        if self.hold_behavior == "unexpected":
            # Simulates a code bug or unexpected SDK exception class —
            # the saga's `except Exception` branch (B30).
            raise RuntimeError("simulated unexpected SDK exception")
        return PaymentHold(
            hold_id=f"pi_test_{self.calls['create_hold']}",
            amount_authorized_cents=amount_cents,
            currency=currency,
            provider=self.name,
            mode=self._mode,
        )

    async def capture(self, *, hold_id, amount_cents, idempotency_key):
        from scripts.checkout.payment.base import (
            PaymentRetryableError, PaymentPermanentError,
        )
        self.calls["capture"] += 1
        if self.capture_behavior == "retryable":
            raise PaymentRetryableError("simulated transient capture failure")
        if self.capture_behavior == "permanent":
            raise PaymentPermanentError("simulated capture denied")
        return None

    async def cancel(self, *, hold_id, idempotency_key):
        from scripts.checkout.payment.base import (
            PaymentRetryableError, PaymentPermanentError,
        )
        self.calls["cancel"] += 1
        if self.cancel_behavior == "retryable":
            raise PaymentRetryableError("simulated transient cancel failure")
        if self.cancel_behavior == "permanent":
            raise PaymentPermanentError("simulated cancel denied")
        return None

    async def get_hold_status(self, hold_id):
        return "requires_capture"


def _make_allocation_lego_only():
    """All-LEGO allocation - bypasses BrickOwl's NotImplementedError create_order."""
    from scripts.checkout.models import AllocationResult, AllocationEntry
    from scripts.checkout.clients.lego_client import SELLER_ID as LEGO_ID
    return AllocationResult(
        seller_allocations=[AllocationEntry(
            seller_id=LEGO_ID,
            seller_name="LEGO.com",
            items={"3001": 5},
            piece_cost_cents=2000,
            shipping_cost_cents=599,
            subtotal_cents=2599,
        )],
        lego_fallback_items=[],
        lego_fallback_cost_cents=0,
        total_piece_cost_cents=2000,
        total_shipping_cents=599,
        grand_total_cents=2599,
        laigo_fee_cents=300,
        customer_total_cents=2899,
    )


# -----------------------------------------------------------------------------
# Harness - set up env, register provider, monkey-patch LEGO client
# -----------------------------------------------------------------------------


def _open_the_gate():
    """Set every env / registry precondition for an OPEN gate."""
    os.environ["CHECKOUT_ENABLED"] = "true"
    os.environ["BRICKOWL_API_KEY"] = "bowl_test_xxx"
    os.environ["LEGO_EMAIL"] = "test@example.com"
    os.environ["LEGO_PASSWORD"] = "pw"
    os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"


def _close_the_gate_env():
    for k in ("CHECKOUT_ENABLED", "BRICKOWL_API_KEY", "LEGO_EMAIL",
              "LEGO_PASSWORD", "LAIGO_ALLOW_REGISTRY_REPLACE"):
        os.environ.pop(k, None)


async def _setup(tmpdir: Path):
    """Returns (provider, checkout_store, job_id, checkout_id, lego_client_module)."""
    os.environ["DB_BACKEND"] = "json"
    os.environ["OUTPUT_DIR"] = str(tmpdir)
    _open_the_gate()

    from scripts.checkout import checkout_store_dispatch as cstore
    from scripts.checkout.payment import registry as reg
    from scripts.checkout.clients import lego_client

    reg._reset_for_tests()
    provider = _FakeProvider("test")
    reg.register(provider)

    # Monkey-patch the LEGO order so we don't launch Playwright.
    # Save the original so each test can restore between runs.
    return provider, cstore, lego_client


def _teardown():
    from scripts.checkout.payment import registry as reg
    reg._reset_for_tests()
    _close_the_gate_env()
    os.environ.pop("DB_BACKEND", None)
    os.environ.pop("OUTPUT_DIR", None)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


async def test_happy_path_initiated_to_captured(tmpdir: Path):
    """Verifies: INITIATED -> STRIPE_HELD -> ORDERS_PLACED -> PAYMENT_CAPTURED."""
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        # Monkey-patch LEGO order to succeed with a fake ID
        async def fake_lego_order(items, job_id, shipping_address=None):
            return "lego_test_order_001"
        original = lego_client.order_from_lego
        lego_client.order_from_lego = fake_lego_order

        try:
            from scripts.checkout import saga

            job_id = "sm-happy"
            checkout_id = "sm-happy-co"
            await cstore.save(job_id, {
                "checkout_id": checkout_id,
                "saga_status": "initiated",
            })

            await saga.execute_checkout_saga(
                job_id=job_id,
                checkout_id=checkout_id,
                allocation=_make_allocation_lego_only(),
                payment_method_id="pm_test_fake",
            )

            final = await cstore.load(job_id)
            assert final["saga_status"] == "payment_captured", (
                f"expected payment_captured, got {final['saga_status']!r}"
            )
            assert final["payment_hold_id"] == "pi_test_1"
            assert final["lego_order_id"] == "lego_test_order_001"
            assert final["total_charged_cents"] == 2899
            assert final["completed_at"] is not None
            assert final.get("error") is None
            assert provider.calls["create_hold"] == 1
            assert provider.calls["capture"] == 1
            assert provider.calls["cancel"] == 0
            print("OK: INITIATED -> STRIPE_HELD -> ORDERS_PLACED -> PAYMENT_CAPTURED (happy path)")
        finally:
            lego_client.order_from_lego = original
    finally:
        _teardown()


async def test_hold_permanent_failure_to_failed(tmpdir: Path):
    """Verifies: INITIATED -> FAILED on PaymentPermanentError at hold."""
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        provider.hold_behavior = "permanent"
        from scripts.checkout import saga
        from scripts.checkout.models import ERROR_MESSAGES

        job_id = "sm-hold-perm"
        checkout_id = "sm-hold-perm-co"
        await cstore.save(job_id, {
            "checkout_id": checkout_id,
            "saga_status": "initiated",
        })

        await saga.execute_checkout_saga(
            job_id=job_id,
            checkout_id=checkout_id,
            allocation=_make_allocation_lego_only(),
            payment_method_id="pm_test_decline",
        )

        final = await cstore.load(job_id)
        assert final["saga_status"] == "failed", (
            f"expected failed, got {final['saga_status']!r}"
        )
        assert "permanent" in (final.get("error") or "").lower()
        assert final.get("customer_message") == ERROR_MESSAGES["payment_permanent"]
        assert provider.calls["create_hold"] == 1
        # No orders placed, no capture attempt, no cancel
        assert provider.calls["capture"] == 0
        assert provider.calls["cancel"] == 0
        print("OK: INITIATED -> FAILED on hold PaymentPermanentError (no orders, no cancel)")
    finally:
        _teardown()


async def test_hold_transient_failure_to_failed(tmpdir: Path):
    """Verifies: INITIATED -> FAILED on PaymentRetryableError at hold.

    NOTE: the saga does NOT auto-retry holds. Retrying create_hold automatically
    risks double-authorization if the first request succeeded but the response
    was lost - the saga aborts cleanly and lets the customer retry from /confirm.
    """
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        provider.hold_behavior = "retryable"
        from scripts.checkout import saga
        from scripts.checkout.models import ERROR_MESSAGES

        job_id = "sm-hold-trans"
        checkout_id = "sm-hold-trans-co"
        await cstore.save(job_id, {
            "checkout_id": checkout_id,
            "saga_status": "initiated",
        })

        await saga.execute_checkout_saga(
            job_id=job_id,
            checkout_id=checkout_id,
            allocation=_make_allocation_lego_only(),
            payment_method_id="pm_test_blip",
        )

        final = await cstore.load(job_id)
        assert final["saga_status"] == "failed"
        assert "transient" in (final.get("error") or "").lower()
        assert final.get("customer_message") == ERROR_MESSAGES["payment_transient"]
        assert provider.calls["create_hold"] == 1
        assert provider.calls["capture"] == 0
        print("OK: INITIATED -> FAILED on hold PaymentRetryableError (clean abort, customer retries)")
    finally:
        _teardown()


async def test_hold_unexpected_failure_to_manual_review(tmpdir: Path):
    """Verifies B30: INITIATED -> MANUAL_REVIEW on bare Exception at hold.

    Bugs in our code or the provider SDK surface here. Routing this to FAILED
    with "transient — retry shortly" creates an infinite customer-retry loop
    against a broken path with no operator escalation. MANUAL_REVIEW is the
    only safe terminal state: customer is told it's under review, operator
    gets a row to investigate.
    """
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        provider.hold_behavior = "unexpected"
        from scripts.checkout import saga
        from scripts.checkout.models import ERROR_MESSAGES

        job_id = "sm-hold-unexpected"
        checkout_id = "sm-hold-unexpected-co"
        await cstore.save(job_id, {
            "checkout_id": checkout_id,
            "saga_status": "initiated",
        })

        await saga.execute_checkout_saga(
            job_id=job_id,
            checkout_id=checkout_id,
            allocation=_make_allocation_lego_only(),
            payment_method_id="pm_test_unexpected",
        )

        final = await cstore.load(job_id)
        assert final["saga_status"] == "manual_review", (
            f"expected manual_review, got {final['saga_status']!r}"
        )
        assert final.get("customer_message") == ERROR_MESSAGES["manual_review"], (
            "B30: customer must NOT see payment_transient (would cause "
            "retry-loop). Must see manual_review message instead."
        )
        # Operator-facing runbook must include the actionable Stripe lookup hint.
        runbook = final.get("manual_review_reason") or ""
        assert "Stripe Dashboard" in runbook, (
            f"manual_review_reason should reference Stripe Dashboard lookup, got: {runbook!r}"
        )
        assert f"hold-{checkout_id}" in runbook, (
            "manual_review_reason should include the idempotency_key so the "
            "operator can find the candidate hold quickly"
        )
        assert provider.calls["create_hold"] == 1
        assert provider.calls["capture"] == 0
        assert provider.calls["cancel"] == 0
        print("OK: INITIATED -> MANUAL_REVIEW on bare Exception at hold (B30 customer-retry-loop guard)")
    finally:
        _teardown()


async def test_order_fails_after_hold_to_compensated(tmpdir: Path):
    """Verifies: STRIPE_HELD -> COMPENSATED when LEGO order fails AND cancel succeeds."""
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        async def failing_lego_order(items, job_id, shipping_address=None):
            raise RuntimeError("simulated playwright crash")
        original = lego_client.order_from_lego
        lego_client.order_from_lego = failing_lego_order

        try:
            from scripts.checkout import saga

            job_id = "sm-order-fail"
            checkout_id = "sm-order-fail-co"
            await cstore.save(job_id, {
                "checkout_id": checkout_id,
                "saga_status": "initiated",
            })

            await saga.execute_checkout_saga(
                job_id=job_id,
                checkout_id=checkout_id,
                allocation=_make_allocation_lego_only(),
                payment_method_id="pm_test_fake",
            )

            final = await cstore.load(job_id)
            assert final["saga_status"] == "compensated", (
                f"expected compensated (clean rollback), got {final['saga_status']!r}. "
                f"State: {final}"
            )
            # Hold was created AND cancelled
            assert provider.calls["create_hold"] == 1
            assert provider.calls["cancel"] == 1
            # Capture never attempted
            assert provider.calls["capture"] == 0
            print("OK: STRIPE_HELD -> COMPENSATED (LEGO order failed; hold cancelled cleanly)")
        finally:
            lego_client.order_from_lego = original
    finally:
        _teardown()


async def test_order_fails_then_cancel_also_fails_to_manual_review(tmpdir: Path):
    """Verifies: STRIPE_HELD -> MANUAL_REVIEW when LEGO order fails AND cancel ALSO fails.

    The hold is stranded - operator must release it manually."""
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        provider.cancel_behavior = "permanent"  # cancel itself fails

        async def failing_lego_order(items, job_id, shipping_address=None):
            raise RuntimeError("simulated playwright crash")
        original = lego_client.order_from_lego
        lego_client.order_from_lego = failing_lego_order

        try:
            from scripts.checkout import saga
            from scripts.checkout.models import ERROR_MESSAGES

            job_id = "sm-cancel-fail"
            checkout_id = "sm-cancel-fail-co"
            await cstore.save(job_id, {
                "checkout_id": checkout_id,
                "saga_status": "initiated",
            })

            await saga.execute_checkout_saga(
                job_id=job_id,
                checkout_id=checkout_id,
                allocation=_make_allocation_lego_only(),
                payment_method_id="pm_test_fake",
            )

            final = await cstore.load(job_id)
            assert final["saga_status"] == "manual_review", (
                f"expected manual_review (stranded hold), got {final['saga_status']!r}"
            )
            assert final.get("manual_review_reason"), "operator runbook missing"
            assert "Stripe" in final["manual_review_reason"], (
                "runbook should mention Stripe (the stranded resource)"
            )
            assert final.get("customer_message") == ERROR_MESSAGES["manual_review"]
            assert provider.calls["create_hold"] == 1
            assert provider.calls["cancel"] >= 1
            assert provider.calls["capture"] == 0
            print("OK: STRIPE_HELD -> MANUAL_REVIEW (cancel also failed; hold stranded)")
        finally:
            lego_client.order_from_lego = original
    finally:
        _teardown()


async def test_capture_permanent_failure_to_manual_review(tmpdir: Path):
    """Verifies: ORDERS_PLACED -> MANUAL_REVIEW on PaymentPermanentError at capture.

    Orders were placed; capture refuses; no compensation because LEGO orders
    can't be cancelled via API. Operator decides recovery."""
    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        provider.capture_behavior = "permanent"

        async def fake_lego_order(items, job_id, shipping_address=None):
            return "lego_test_order_002"
        original = lego_client.order_from_lego
        lego_client.order_from_lego = fake_lego_order

        try:
            from scripts.checkout import saga
            from scripts.checkout.models import ERROR_MESSAGES

            job_id = "sm-cap-perm"
            checkout_id = "sm-cap-perm-co"
            await cstore.save(job_id, {
                "checkout_id": checkout_id,
                "saga_status": "initiated",
            })

            await saga.execute_checkout_saga(
                job_id=job_id,
                checkout_id=checkout_id,
                allocation=_make_allocation_lego_only(),
                payment_method_id="pm_test_fake",
            )

            final = await cstore.load(job_id)
            assert final["saga_status"] == "manual_review", (
                f"expected manual_review, got {final['saga_status']!r}. State: {final}"
            )
            assert final["lego_order_id"] == "lego_test_order_002", (
                "LEGO order must be checkpointed before capture is attempted"
            )
            assert final.get("manual_review_reason"), "operator runbook missing"
            assert final.get("customer_message") == ERROR_MESSAGES["manual_review"]
            assert provider.calls["create_hold"] == 1
            assert provider.calls["capture"] == 1, (
                "permanent capture failure must NOT retry"
            )
            assert provider.calls["cancel"] == 0, (
                "MANUAL_REVIEW with placed orders must NOT auto-cancel"
            )
            print("OK: ORDERS_PLACED -> MANUAL_REVIEW on capture PaymentPermanentError")
        finally:
            lego_client.order_from_lego = original
    finally:
        _teardown()


async def test_capture_transient_exhausts_retries_to_manual_review(tmpdir: Path):
    """Verifies: ORDERS_PLACED -> MANUAL_REVIEW when transient capture failures
    exhaust the retry budget (1s + 4s + 16s)."""
    # Suppress real backoff sleeps so the test doesn't take 21s - monkey-patch
    # asyncio.sleep used inside _capture_with_retry.
    from scripts.checkout import saga as saga_mod
    real_sleep = asyncio.sleep
    async def fast_sleep(_):
        return None
    saga_mod.asyncio.sleep = fast_sleep  # type: ignore[attr-defined]

    provider, cstore, lego_client = await _setup(tmpdir)
    try:
        provider.capture_behavior = "retryable"

        async def fake_lego_order(items, job_id, shipping_address=None):
            return "lego_test_order_003"
        original = lego_client.order_from_lego
        lego_client.order_from_lego = fake_lego_order

        try:
            from scripts.checkout import saga
            from scripts.checkout.models import ERROR_MESSAGES

            job_id = "sm-cap-trans"
            checkout_id = "sm-cap-trans-co"
            await cstore.save(job_id, {
                "checkout_id": checkout_id,
                "saga_status": "initiated",
            })

            await saga.execute_checkout_saga(
                job_id=job_id,
                checkout_id=checkout_id,
                allocation=_make_allocation_lego_only(),
                payment_method_id="pm_test_fake",
            )

            final = await cstore.load(job_id)
            assert final["saga_status"] == "manual_review", (
                f"expected manual_review after retries exhausted, "
                f"got {final['saga_status']!r}"
            )
            assert final.get("customer_message") == ERROR_MESSAGES["manual_review"]
            # Retries: 1 initial + len(_CAPTURE_BACKOFFS_SECONDS) retries
            expected_attempts = 1 + len(saga_mod._CAPTURE_BACKOFFS_SECONDS)
            assert provider.calls["capture"] == expected_attempts, (
                f"expected {expected_attempts} capture attempts, "
                f"got {provider.calls['capture']}"
            )
            assert provider.calls["cancel"] == 0
            print(
                f"OK: ORDERS_PLACED -> MANUAL_REVIEW after {expected_attempts} "
                "transient capture failures (retry budget exhausted)"
            )
        finally:
            lego_client.order_from_lego = original
    finally:
        saga_mod.asyncio.sleep = real_sleep  # type: ignore[attr-defined]
        _teardown()


# -----------------------------------------------------------------------------


async def amain() -> int:
    with tempfile.TemporaryDirectory() as td1:
        await test_happy_path_initiated_to_captured(Path(td1))
    with tempfile.TemporaryDirectory() as td2:
        await test_hold_permanent_failure_to_failed(Path(td2))
    with tempfile.TemporaryDirectory() as td3:
        await test_hold_transient_failure_to_failed(Path(td3))
    with tempfile.TemporaryDirectory() as td3b:
        await test_hold_unexpected_failure_to_manual_review(Path(td3b))
    with tempfile.TemporaryDirectory() as td4:
        await test_order_fails_after_hold_to_compensated(Path(td4))
    with tempfile.TemporaryDirectory() as td5:
        await test_order_fails_then_cancel_also_fails_to_manual_review(Path(td5))
    with tempfile.TemporaryDirectory() as td6:
        await test_capture_permanent_failure_to_manual_review(Path(td6))
    with tempfile.TemporaryDirectory() as td7:
        await test_capture_transient_exhausts_retries_to_manual_review(Path(td7))
    print()
    print("All saga state-machine tests PASSED (section 6.10 item 8).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
