"""Tests for the donate_router in scripts/pay_router.py — POST /donate.

Covers every branch of the global, client-confirm tip endpoint against a fake
payment provider. No network, no DB, no filesystem (donate is global — no
job scope, no payment.json write).

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_donate_router
"""

import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import pay_router as pr
from scripts.checkout.payment import registry as reg
from scripts.checkout.payment.base import (
    PaymentPermanentError,
    PaymentRetryableError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fake provider — only create_payment_intent() is exercised; the rest raise if
# touched so a wrong code path surfaces loudly.
# ─────────────────────────────────────────────────────────────────────────────


class _FakeIntentProvider:
    name = "fake"

    def __init__(self, *, result=None, error=None, mode_value="test"):
        self._mode = mode_value
        self.currency = "usd"
        self._result = result or {
            "payment_intent_id": "pi_fake_1",
            "client_secret": "pi_fake_1_secret_xyz",
            "amount_cents": 99,
        }
        self._error = error
        self.calls = []

    def mode(self) -> str:
        return self._mode

    async def create_payment_intent(self, *, amount_cents, metadata=None,
                                    description=None):
        self.calls.append({
            "amount_cents": amount_cents,
            "metadata": metadata,
            "description": description,
        })
        if self._error is not None:
            raise self._error
        return self._result

    async def charge(self, *a, **kw):  # pragma: no cover
        raise AssertionError("charge called unexpectedly")

    async def create_hold(self, *a, **kw):  # pragma: no cover
        raise AssertionError("create_hold called unexpectedly")

    async def capture(self, *a, **kw):  # pragma: no cover
        raise AssertionError("capture called unexpectedly")

    async def cancel(self, *a, **kw):  # pragma: no cover
        raise AssertionError("cancel called unexpectedly")

    async def get_hold_status(self, *a, **kw):  # pragma: no cover
        raise AssertionError("get_hold_status called unexpectedly")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

VALID_JOB = "valid-job-123"
_CLIENT: TestClient = None  # set in main()


def _use(provider) -> None:
    """Reset the registry, then register `provider` (or leave it empty)."""
    reg._reset_for_tests()
    if provider is not None:
        reg.register(provider)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_valid_amount():
    prov = _FakeIntentProvider(result={
        "payment_intent_id": "pi_donate_1",
        "client_secret": "pi_donate_1_secret_abc",
        "amount_cents": 99,
    })
    _use(prov)
    r = _CLIENT.post("/donate", json={"amount_cents": 99})
    assert r.status_code == 200, r.text
    body = r.json()
    # Response shape is exactly {client_secret} per the endpoint spec.
    assert body == {"client_secret": "pi_donate_1_secret_abc"}, body
    # Provider called once with the tip metadata + description, no job_id.
    assert len(prov.calls) == 1, prov.calls
    assert prov.calls[0]["amount_cents"] == 99
    assert prov.calls[0]["metadata"] == {"type": "tip"}, prov.calls[0]["metadata"]
    assert prov.calls[0]["description"] == "LAIGO tip"
    print("OK: valid amount -> 200 {client_secret}, metadata type=tip, no job_id")


def test_exactly_min():
    prov = _FakeIntentProvider()
    _use(prov)
    r = _CLIENT.post("/donate", json={"amount_cents": 50})
    assert r.status_code == 200, r.text
    assert "client_secret" in r.json()
    assert prov.calls[0]["amount_cents"] == 50
    print("OK: amount == 50 (boundary) -> 200")


def test_below_min():
    prov = _FakeIntentProvider()
    _use(prov)  # registered, but amount is checked first
    r = _CLIENT.post("/donate", json={"amount_cents": 10})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_AMOUNT", r.text
    # 400 short-circuits before the provider is touched.
    assert len(prov.calls) == 0, prov.calls
    print("OK: 1-49c -> 400 INVALID_AMOUNT (provider not called)")


def test_negative_amount():
    _use(_FakeIntentProvider())
    r = _CLIENT.post("/donate", json={"amount_cents": -5})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_AMOUNT", r.text
    print("OK: negative amount -> 400 INVALID_AMOUNT")


def test_above_max():
    _use(_FakeIntentProvider())
    r = _CLIENT.post("/donate", json={"amount_cents": 100_000_000})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_AMOUNT", r.text
    print("OK: over-max amount -> 400 INVALID_AMOUNT")


def test_no_provider():
    _use(None)  # no provider registered
    r = _CLIENT.post("/donate", json={"amount_cents": 99})
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "PAYMENTS_UNAVAILABLE", r.text
    print("OK: no provider registered -> 503 PAYMENTS_UNAVAILABLE")


def test_retryable_error():
    _use(_FakeIntentProvider(error=PaymentRetryableError("transient boom")))
    r = _CLIENT.post("/donate", json={"amount_cents": 99})
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "PAYMENT_RETRYABLE", r.text
    print("OK: PaymentRetryableError -> 503 PAYMENT_RETRYABLE")


def test_permanent_error():
    _use(_FakeIntentProvider(error=PaymentPermanentError("bad request")))
    r = _CLIENT.post("/donate", json={"amount_cents": 99})
    assert r.status_code == 500, r.text
    assert r.json()["detail"]["code"] == "PAYMENT_ERROR", r.text
    print("OK: PaymentPermanentError -> 500 PAYMENT_ERROR")


def test_job_id_flows_into_metadata():
    prov = _FakeIntentProvider()
    _use(prov)
    r = _CLIENT.post("/donate", json={"amount_cents": 99, "job_id": VALID_JOB})
    assert r.status_code == 200, r.text
    assert prov.calls[0]["metadata"] == {"type": "tip", "job_id": VALID_JOB}, \
        prov.calls[0]["metadata"]
    print("OK: valid job_id -> added to metadata for the webhook")


def test_unsafe_job_id_dropped():
    # A charset-invalid job_id must be silently dropped (not added to metadata),
    # never reach Stripe, but the donation still proceeds.
    prov = _FakeIntentProvider()
    _use(prov)
    r = _CLIENT.post("/donate", json={"amount_cents": 99, "job_id": "../etc"})
    assert r.status_code == 200, r.text
    assert prov.calls[0]["metadata"] == {"type": "tip"}, prov.calls[0]["metadata"]
    print("OK: unsafe job_id -> dropped from metadata, donation still 200")


# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    global _CLIENT
    app = FastAPI()
    app.include_router(pr.donate_router)
    _CLIENT = TestClient(app)

    try:
        test_valid_amount()
        test_exactly_min()
        test_below_min()
        test_negative_amount()
        test_above_max()
        test_no_provider()
        test_retryable_error()
        test_permanent_error()
        test_job_id_flows_into_metadata()
        test_unsafe_job_id_dropped()
    finally:
        reg._reset_for_tests()

    print()
    print("All donate_router tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
