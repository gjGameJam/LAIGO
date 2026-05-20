"""Layered defense-in-depth test for Stripe-disabled bypass (§6.10 item 10).

The original landmine bug (FMEA #1, RPN 810): with STRIPE_ENABLED=False,
`/confirm` would silently swallow NotImplementedError from the payment stub
and place real marketplace orders at $0 charged. The fix is six layers
(L0-L5); this test exercises L0/L3/L4 to assert the bypass is structurally
impossible.

Coverage:
  L0 gate.compute_decision matrix    → DISABLED reasons enumerate every missing
                                       precondition the operator must fix
  L0 gate.require_open               → raises GateClosedError when DISABLED,
                                       returns decision when OPEN
  L3 dependencies (HTTP layer)       → HTTPException(503) wire body shape
                                       exactly matches frontend contract
  L4 saga pre-flight                 → execute_checkout_saga refuses to call
                                       provider.create_hold when gate is closed
                                       (watchdog provider; assertion fires if
                                        Stripe is touched)

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_gate_bypass

No external services required (DB_BACKEND forced to json; provider stubbed).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: minimal fake PaymentProvider implementations
# ─────────────────────────────────────────────────────────────────────────────


class _FakeProvider:
    """Bare-minimum PaymentProvider that returns the test/live mode it was told."""

    name = "fake"

    def __init__(self, mode_value: str = "test"):
        self._mode = mode_value
        self.currency = "usd"

    def mode(self) -> str:
        return self._mode

    async def create_hold(self, *a, **kw):  # pragma: no cover — should never run when gate open
        raise AssertionError("create_hold called unexpectedly in this test")

    async def capture(self, *a, **kw):  # pragma: no cover
        raise AssertionError("capture called unexpectedly")

    async def cancel(self, *a, **kw):  # pragma: no cover
        raise AssertionError("cancel called unexpectedly")

    async def get_hold_status(self, *a, **kw):  # pragma: no cover
        raise AssertionError("get_hold_status called unexpectedly")


class _WatchdogProvider(_FakeProvider):
    """Fails the test loudly if ANY method is invoked.

    Used by the L4 test: if the saga ever reaches the provider with a closed
    gate, that's the exact bug the gate is supposed to prevent. The
    AssertionError surfaces as a saga `error`, which the test then inspects.
    """

    name = "watchdog"


# ─────────────────────────────────────────────────────────────────────────────
# Env helpers
# ─────────────────────────────────────────────────────────────────────────────


def _clear_gate_env() -> dict:
    """Snapshot + clear all env keys the gate reads. Returns the snapshot."""
    keys = [
        "CHECKOUT_ENABLED", "STRIPE_SECRET_KEY", "BRICKOWL_API_KEY",
        "LEGO_EMAIL", "LEGO_PASSWORD", "RENDER",
    ]
    snap = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    return snap


def _restore_env(snap: dict) -> None:
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _all_marketplaces_set() -> None:
    os.environ["BRICKOWL_API_KEY"] = "bowl_test_xxx"
    os.environ["LEGO_EMAIL"] = "test@example.com"
    os.environ["LEGO_PASSWORD"] = "pw"


# ─────────────────────────────────────────────────────────────────────────────
# L0 — gate.compute_decision matrix
# ─────────────────────────────────────────────────────────────────────────────


def test_gate_disabled_when_master_flag_unset():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        decision = gate.compute_decision()
        assert decision.mode == gate.CheckoutMode.DISABLED
        assert decision.is_open is False
        assert any("CHECKOUT_ENABLED is not set" in r for r in decision.reasons), (
            f"reasons={decision.reasons}"
        )
        print("OK: gate DISABLED when CHECKOUT_ENABLED is not set")
    finally:
        _restore_env(snap)
        reg._reset_for_tests()


def test_gate_disabled_when_master_flag_falsy():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "false"
        decision = gate.compute_decision()
        assert decision.mode == gate.CheckoutMode.DISABLED
        assert any("is not truthy" in r for r in decision.reasons), (
            f"reasons={decision.reasons}"
        )
        print("OK: gate DISABLED when CHECKOUT_ENABLED='false' (explicit kill switch)")
    finally:
        _restore_env(snap)
        reg._reset_for_tests()


def test_gate_disabled_when_no_provider_registered():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "true"
        _all_marketplaces_set()
        decision = gate.compute_decision()
        assert decision.mode == gate.CheckoutMode.DISABLED
        assert decision.payment_provider is None
        assert any("No payment provider registered" in r for r in decision.reasons), (
            f"reasons={decision.reasons}"
        )
        print("OK: gate DISABLED with no payment provider (L5 registry empty)")
    finally:
        _restore_env(snap)
        reg._reset_for_tests()


def test_gate_disabled_when_no_marketplaces():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "true"
        os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"  # allow swap
        reg.register(_FakeProvider("test"))
        # No marketplace env vars
        decision = gate.compute_decision()
        assert decision.mode == gate.CheckoutMode.DISABLED
        assert decision.marketplaces_live == ()
        assert any("No marketplace credentials configured" in r for r in decision.reasons), (
            f"reasons={decision.reasons}"
        )
        print("OK: gate DISABLED with no marketplace credentials")
    finally:
        os.environ.pop("LAIGO_ALLOW_REGISTRY_REPLACE", None)
        _restore_env(snap)
        reg._reset_for_tests()


def test_gate_disabled_when_live_key_outside_render():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "true"
        os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"
        reg.register(_FakeProvider("live"))
        _all_marketplaces_set()
        # sk_live_ key set but RENDER not set
        os.environ["STRIPE_SECRET_KEY"] = "sk_live_abcdefgh"
        # explicitly do NOT set RENDER
        decision = gate.compute_decision()
        assert decision.mode == gate.CheckoutMode.DISABLED, (
            f"expected DISABLED for live-key-outside-Render, got {decision.mode}"
        )
        assert decision.payment_provider is None, (
            "live-key-outside-Render branch must null payment_provider"
        )
        assert any("Live Stripe key" in r and "outside the Render" in r
                   for r in decision.reasons), f"reasons={decision.reasons}"
        print("OK: gate DISABLED when sk_live_ key present outside Render env")
    finally:
        os.environ.pop("LAIGO_ALLOW_REGISTRY_REPLACE", None)
        _restore_env(snap)
        reg._reset_for_tests()


def test_gate_open_in_test_mode_with_full_config():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "true"
        os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"
        reg.register(_FakeProvider("test"))
        _all_marketplaces_set()
        decision = gate.compute_decision()
        assert decision.mode == gate.CheckoutMode.TEST
        assert decision.is_open is True
        assert decision.is_live is False
        assert decision.payment_provider == "fake"
        assert "brickowl" in decision.marketplaces_live
        assert "lego_official" in decision.marketplaces_live
        print("OK: gate TEST + is_open when fully configured with test-mode provider")
    finally:
        os.environ.pop("LAIGO_ALLOW_REGISTRY_REPLACE", None)
        _restore_env(snap)
        reg._reset_for_tests()


def test_require_open_raises_when_disabled():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        # All env unset → DISABLED for multiple reasons
        try:
            gate.require_open()
        except gate.GateClosedError as exc:
            msg = str(exc)
            assert "disabled" in msg.lower()
            print(f"OK: require_open raises GateClosedError when DISABLED ({msg[:60]}...)")
        else:
            raise AssertionError("require_open did not raise when gate was DISABLED")
    finally:
        _restore_env(snap)
        reg._reset_for_tests()


def test_require_open_returns_decision_when_open():
    from scripts.checkout import gate
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "true"
        os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"
        reg.register(_FakeProvider("test"))
        _all_marketplaces_set()
        decision = gate.require_open()
        assert decision.is_open is True
        assert decision.mode == gate.CheckoutMode.TEST
        print("OK: require_open returns GateDecision when gate is OPEN")
    finally:
        os.environ.pop("LAIGO_ALLOW_REGISTRY_REPLACE", None)
        _restore_env(snap)
        reg._reset_for_tests()


def test_gate_closed_error_does_not_inherit_from_swallowable_types():
    """GateClosedError must NOT inherit from NotImplementedError or ValueError
    so an upstream `except (NotImplementedError, ValueError):` cannot silently
    swallow it. This was the structural antipattern that caused RPN #1."""
    from scripts.checkout.gate import GateClosedError
    assert not issubclass(GateClosedError, NotImplementedError)
    assert not issubclass(GateClosedError, ValueError)
    assert issubclass(GateClosedError, RuntimeError), (
        "GateClosedError should inherit from RuntimeError for clarity"
    )
    print("OK: GateClosedError is structurally un-swallowable")


# ─────────────────────────────────────────────────────────────────────────────
# L3 — FastAPI dependency contract
# ─────────────────────────────────────────────────────────────────────────────


async def _l3_test_disabled():
    from fastapi import HTTPException
    from scripts.checkout.dependencies import require_checkout_gate_open
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        # Build a minimal Request-shaped stub. The dependency reads:
        #   request.client.host, request.headers.get(...), request.path_params.get(...)
        fake_request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"user-agent": "test-suite"},
            path_params={"job_id": "test-job-1"},
        )
        try:
            await require_checkout_gate_open(fake_request)
        except HTTPException as exc:
            assert exc.status_code == 503, f"expected 503, got {exc.status_code}"
            body = exc.detail
            assert isinstance(body, dict), f"detail must be dict, got {type(body)}"
            # Wire contract — exact strings (load-bearing for frontend)
            assert body.get("code") == "CHECKOUT_GATE_CLOSED", (
                f"code mismatch: {body.get('code')!r}"
            )
            assert body.get("mode") == "disabled", f"mode mismatch: {body.get('mode')!r}"
            assert "error" in body and isinstance(body["error"], str)
            # The customer-facing 'error' string must NOT leak operator reasons
            assert "CHECKOUT_ENABLED" not in body["error"], (
                "L3 must not surface operator-facing env-key names to customers"
            )
            assert "Stripe" not in body["error"], (
                "L3 must not leak provider implementation details to customers"
            )
            print("OK: L3 require_checkout_gate_open returns 503 with exact wire shape")
        else:
            raise AssertionError("L3 dependency did not raise HTTPException")
    finally:
        _restore_env(snap)
        reg._reset_for_tests()


async def _l3_test_open():
    from scripts.checkout.dependencies import require_checkout_gate_open
    from scripts.checkout.payment import registry as reg

    snap = _clear_gate_env()
    reg._reset_for_tests()
    try:
        os.environ["CHECKOUT_ENABLED"] = "true"
        os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"
        reg.register(_FakeProvider("test"))
        _all_marketplaces_set()
        fake_request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"user-agent": "test-suite"},
            path_params={"job_id": "test-job-2"},
        )
        decision = await require_checkout_gate_open(fake_request)
        assert decision.is_open is True
        print("OK: L3 require_checkout_gate_open returns GateDecision when OPEN")
    finally:
        os.environ.pop("LAIGO_ALLOW_REGISTRY_REPLACE", None)
        _restore_env(snap)
        reg._reset_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# L4 — Saga pre-flight refuses to call provider when gate is closed
# ─────────────────────────────────────────────────────────────────────────────


async def _l4_test_saga_refuses_with_closed_gate(tmp_output_dir: Path):
    """Watchdog provider proves no Stripe call is made when gate is closed.

    Calls execute_checkout_saga with an AllocationResult that WOULD trigger a
    create_hold under normal conditions. The gate is DISABLED, so the saga
    must short-circuit to FAILED before reaching the provider. If the watchdog
    provider's create_hold is ever invoked, it raises AssertionError, which
    the saga catches and surfaces as the FAILED `error` field — the test then
    asserts the error contains the "Gate closed" sentinel, NOT the watchdog
    string.
    """
    # Force JSON backend so saga.update() works without a DB
    os.environ["DB_BACKEND"] = "json"
    os.environ["OUTPUT_DIR"] = str(tmp_output_dir)

    snap = _clear_gate_env()
    from scripts.checkout.payment import registry as reg
    reg._reset_for_tests()

    try:
        # Register a watchdog provider, but leave the gate DISABLED by NOT
        # setting CHECKOUT_ENABLED. compute_decision() will return DISABLED
        # for "CHECKOUT_ENABLED is not set" even though the provider exists.
        os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"
        reg.register(_WatchdogProvider("test"))
        _all_marketplaces_set()

        # Deferred imports so env settings above are picked up at module load
        from scripts.checkout import saga
        from scripts.checkout.models import AllocationResult, AllocationEntry
        from scripts.checkout import checkout_store_dispatch as cstore

        job_id = "gate-bypass-test-job"
        checkout_id = "gate-bypass-test-co"

        # Pre-populate a checkout row so saga.update() has something to merge
        await cstore.save(job_id, {
            "checkout_id": checkout_id,
            "saga_status": "initiated",
        })

        allocation = AllocationResult(
            seller_allocations=[AllocationEntry(
                seller_id="brickowl_test",
                seller_name="Test Seller",
                items={"3001": 1},
                piece_cost_cents=100,
                shipping_cost_cents=500,
                subtotal_cents=600,
            )],
            lego_fallback_items=[],
            lego_fallback_cost_cents=0,
            total_piece_cost_cents=100,
            total_shipping_cents=500,
            grand_total_cents=600,
            laigo_fee_cents=300,
            customer_total_cents=900,
        )

        # Call saga directly. With gate DISABLED, the first statement of the
        # inner saga calls gate.require_open() which raises GateClosedError —
        # the saga catches it, writes FAILED, and returns. Provider is never
        # touched (watchdog assertion would fire).
        await saga.execute_checkout_saga(
            job_id=job_id,
            checkout_id=checkout_id,
            allocation=allocation,
            payment_method_id="pm_test_fake",
        )

        final = await cstore.load(job_id)
        assert final is not None, "checkout state vanished"
        assert final.get("saga_status") == "failed", (
            f"expected saga_status='failed', got {final.get('saga_status')!r}. "
            f"State: {final}"
        )
        err = final.get("error") or ""
        assert "Gate closed" in err, (
            f"saga `error` should reference gate closure, got: {err!r}"
        )
        # The smoking gun: if the watchdog had fired, the error would contain
        # 'create_hold called unexpectedly' — anything but that.
        assert "called unexpectedly" not in err, (
            f"WATCHDOG FIRED — saga reached the provider despite closed gate! "
            f"This is the exact bypass the gate exists to prevent. Error: {err!r}"
        )
        # Customer-facing message present (B12 contract)
        assert final.get("customer_message"), (
            "saga must set customer_message when setting error (B12 / H1)"
        )
        print("OK: L4 saga refuses to call provider when gate is DISABLED (watchdog silent)")
    finally:
        os.environ.pop("LAIGO_ALLOW_REGISTRY_REPLACE", None)
        os.environ.pop("DB_BACKEND", None)
        os.environ.pop("OUTPUT_DIR", None)
        _restore_env(snap)
        reg._reset_for_tests()


# ─────────────────────────────────────────────────────────────────────────────


async def amain() -> int:
    # Pure / sync tests
    test_gate_disabled_when_master_flag_unset()
    test_gate_disabled_when_master_flag_falsy()
    test_gate_disabled_when_no_provider_registered()
    test_gate_disabled_when_no_marketplaces()
    test_gate_disabled_when_live_key_outside_render()
    test_gate_open_in_test_mode_with_full_config()
    test_require_open_raises_when_disabled()
    test_require_open_returns_decision_when_open()
    test_gate_closed_error_does_not_inherit_from_swallowable_types()

    # L3 (async)
    await _l3_test_disabled()
    await _l3_test_open()

    # L4 (async, needs tempdir for JSON-backed checkout_store)
    with tempfile.TemporaryDirectory() as td:
        await _l4_test_saga_refuses_with_closed_gate(Path(td))

    print()
    print("All gate-bypass tests PASSED (§6.10 item 10).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
