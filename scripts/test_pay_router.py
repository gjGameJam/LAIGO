"""Tests for scripts/pay_router.py — the pay-what-you-want endpoint.

Covers every branch of POST /jobs/{job_id}/pay against a fake payment provider.
No network, no DB. OUTPUT_DIR is redirected to a tempdir and a dummy artifact is
seeded so the build-pack-exists gate passes for the valid job.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_pay_router
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import pay_router as pr
from scripts.checkout.payment import registry as reg
from scripts.checkout.payment.base import (
    PaymentPermanentError,
    PaymentRetryableError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fake provider — only charge() is exercised; the rest raise if touched so a
# wrong code path surfaces loudly. Implements the full PaymentProvider surface
# so it stays valid if charge() is added to the runtime_checkable Protocol.
# ─────────────────────────────────────────────────────────────────────────────


class _FakeChargeProvider:
    name = "fake"

    def __init__(self, *, result=None, error=None, mode_value="test"):
        self._mode = mode_value
        self.currency = "usd"
        self._result = result
        self._error = error
        self.calls = []

    def mode(self) -> str:
        return self._mode

    async def charge(self, *, amount_cents, payment_method_id, idempotency_key,
                     metadata=None):
        self.calls.append({
            "amount_cents": amount_cents,
            "payment_method_id": payment_method_id,
            "idempotency_key": idempotency_key,
            "metadata": metadata,
        })
        if self._error is not None:
            raise self._error
        return self._result

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


def _payment_path(job: str) -> Path:
    return pr.OUTPUT_DIR / job / "payment.json"


def _clear_payment_json(job: str) -> None:
    p = _payment_path(job)
    if p.exists():
        p.unlink()


def _read_payment_json(job: str) -> dict:
    return json.loads(_payment_path(job).read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_free_path():
    _use(None)  # free path never touches the registry
    _clear_payment_json(VALID_JOB)
    r = _CLIENT.post(f"/jobs/{VALID_JOB}/pay", json={"amount_cents": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "free" and body["amount_cents"] == 0, body
    rec = _read_payment_json(VALID_JOB)
    assert rec["status"] == "free"
    assert rec["amount_cents"] == 0
    assert rec["payment_intent_id"] is None
    print("OK: free path -> {status:free} and records a free payment.json")


def test_below_min():
    r = _CLIENT.post(f"/jobs/{VALID_JOB}/pay", json={"amount_cents": 25})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "AMOUNT_BELOW_MINIMUM", r.text
    print("OK: 1-49c -> 422 AMOUNT_BELOW_MINIMUM")


def test_negative_amount():
    r = _CLIENT.post(f"/jobs/{VALID_JOB}/pay", json={"amount_cents": -5})
    assert r.status_code == 422, r.text  # pydantic ge=0
    print("OK: negative amount -> 422 (pydantic ge=0)")


def test_over_max_amount():
    r = _CLIENT.post(f"/jobs/{VALID_JOB}/pay", json={"amount_cents": 100_000_000})
    assert r.status_code == 422, r.text  # pydantic le
    print("OK: over-max amount -> 422 (pydantic le)")


def test_missing_payment_method():
    _use(_FakeChargeProvider())
    r = _CLIENT.post(f"/jobs/{VALID_JOB}/pay", json={"amount_cents": 500})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "PAYMENT_METHOD_REQUIRED", r.text
    print("OK: paying with no payment_method_id -> 422 PAYMENT_METHOD_REQUIRED")


def test_provider_unavailable():
    _use(None)  # no provider registered
    r = _CLIENT.post(
        f"/jobs/{VALID_JOB}/pay",
        json={"amount_cents": 500, "payment_method_id": "pm_x"},
    )
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "PAYMENTS_UNAVAILABLE", r.text
    print("OK: no provider registered -> 503 PAYMENTS_UNAVAILABLE")


def test_charge_success():
    prov = _FakeChargeProvider(result={
        "status": "succeeded",
        "payment_intent_id": "pi_fake_123",
        "amount_cents": 500,
        "client_secret": None,
    })
    _use(prov)
    _clear_payment_json(VALID_JOB)
    r = _CLIENT.post(
        f"/jobs/{VALID_JOB}/pay",
        json={"amount_cents": 500, "payment_method_id": "pm_card_visa"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paid", body
    assert body["payment_intent_id"] == "pi_fake_123", body
    assert body["amount_cents"] == 500, body
    # charge() called with the expected, deterministic idempotency key + amount
    assert len(prov.calls) == 1, prov.calls
    assert prov.calls[0]["amount_cents"] == 500
    assert prov.calls[0]["payment_method_id"] == "pm_card_visa"
    assert prov.calls[0]["idempotency_key"] == f"charge-{VALID_JOB}-500"
    # job_id carried in metadata so the webhook can map the charge back.
    assert prov.calls[0]["metadata"]["job_id"] == VALID_JOB
    rec = _read_payment_json(VALID_JOB)
    assert rec["status"] == "paid"
    assert rec["amount_cents"] == 500
    assert rec["payment_intent_id"] == "pi_fake_123"
    print("OK: charge success -> 200 paid, correct charge args, payment.json recorded")


def test_requires_action():
    prov = _FakeChargeProvider(result={
        "status": "requires_action",
        "payment_intent_id": "pi_3ds",
        "amount_cents": 500,
        "client_secret": "pi_3ds_secret_abc",
    })
    _use(prov)
    _clear_payment_json(VALID_JOB)
    r = _CLIENT.post(
        f"/jobs/{VALID_JOB}/pay",
        json={"amount_cents": 500, "payment_method_id": "pm_3ds"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "requires_action", body
    assert body["client_secret"] == "pi_3ds_secret_abc", body
    # No payment recorded yet — the charge isn't captured until 3DS completes.
    assert not _payment_path(VALID_JOB).exists(), (
        "requires_action must NOT write payment.json (not captured yet)"
    )
    print("OK: requires_action -> 200 client_secret, no premature payment.json")


def test_retryable_error():
    _use(_FakeChargeProvider(error=PaymentRetryableError("transient boom")))
    r = _CLIENT.post(
        f"/jobs/{VALID_JOB}/pay",
        json={"amount_cents": 500, "payment_method_id": "pm_x"},
    )
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "PAYMENT_RETRYABLE", r.text
    print("OK: PaymentRetryableError -> 503 PAYMENT_RETRYABLE")


def test_permanent_error():
    _use(_FakeChargeProvider(error=PaymentPermanentError("card declined")))
    r = _CLIENT.post(
        f"/jobs/{VALID_JOB}/pay",
        json={"amount_cents": 500, "payment_method_id": "pm_x"},
    )
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == "PAYMENT_FAILED", r.text
    print("OK: PaymentPermanentError -> 402 PAYMENT_FAILED")


def test_invalid_job_id():
    # 'bad.name' routes to the handler (single path segment) but fails the
    # charset guard -> 400 before any filesystem / charge work.
    r = _CLIENT.post("/jobs/bad.name/pay", json={"amount_cents": 0})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_JOB_ID", r.text
    print("OK: unsafe job_id -> 400 INVALID_JOB_ID")


def test_job_not_found():
    # Charset-valid id, but no artifact.zip on disk -> 404.
    r = _CLIENT.post("/jobs/ghost-job-xyz/pay", json={"amount_cents": 0})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "JOB_NOT_FOUND", r.text
    print("OK: nonexistent build pack -> 404 JOB_NOT_FOUND")


# ── Webhook ──────────────────────────────────────────────────────────────────


def _stripe_sig(payload: bytes, secret: str) -> str:
    """Build a valid Stripe-Signature header for `payload` (mirrors Stripe's
    t=<ts>,v1=<HMAC_SHA256(ts + '.' + payload)> scheme)."""
    ts = int(time.time())
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_no_secret():
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    r = _CLIENT.post("/webhooks/stripe", content=b"{}",
                     headers={"stripe-signature": "x"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "WEBHOOK_NOT_CONFIGURED", r.text
    print("OK: webhook without STRIPE_WEBHOOK_SECRET -> 503")


def test_webhook_bad_signature():
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret"
    try:
        r = _CLIENT.post("/webhooks/stripe", content=b'{"id":"evt"}',
                         headers={"stripe-signature": "t=1,v1=deadbeef"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_SIGNATURE", r.text
        print("OK: webhook bad signature -> 400 INVALID_SIGNATURE")
    finally:
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)


def test_webhook_records_paid():
    secret = "whsec_test_secret"
    os.environ["STRIPE_WEBHOOK_SECRET"] = secret
    _clear_payment_json(VALID_JOB)
    try:
        event = {
            "id": "evt_1",
            "type": "payment_intent.succeeded",
            "data": {"object": {
                "id": "pi_wh_1",
                "amount": 700,
                "amount_received": 700,
                "metadata": {"job_id": VALID_JOB},
            }},
        }
        payload = json.dumps(event).encode()
        header = _stripe_sig(payload, secret)
        r = _CLIENT.post("/webhooks/stripe", content=payload,
                         headers={"stripe-signature": header,
                                  "content-type": "application/json"})
        assert r.status_code == 200, r.text
        assert r.json()["received"] is True
        rec = _read_payment_json(VALID_JOB)
        assert rec["status"] == "paid"
        assert rec["amount_cents"] == 700
        assert rec["payment_intent_id"] == "pi_wh_1"
        print("OK: webhook payment_intent.succeeded -> records paid payment.json")
    finally:
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)


def test_webhook_ignores_other_events():
    secret = "whsec_test_secret"
    os.environ["STRIPE_WEBHOOK_SECRET"] = secret
    try:
        event = {"id": "evt_2", "type": "charge.refunded", "data": {"object": {}}}
        payload = json.dumps(event).encode()
        header = _stripe_sig(payload, secret)
        r = _CLIENT.post("/webhooks/stripe", content=payload,
                         headers={"stripe-signature": header,
                                  "content-type": "application/json"})
        assert r.status_code == 200, r.text  # verified but ignored
        print("OK: webhook ignores non-payment_intent.succeeded events (200)")
    finally:
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)


def test_record_no_downgrade():
    # A paid record must survive a subsequent free ($0) record.
    _clear_payment_json(VALID_JOB)
    pr._record_payment(VALID_JOB, amount_cents=900, status="paid",
                       payment_intent_id="pi_keep")
    pr._record_payment(VALID_JOB, amount_cents=0, status="free",
                       payment_intent_id=None)
    rec = _read_payment_json(VALID_JOB)
    assert rec["status"] == "paid", rec
    assert rec["payment_intent_id"] == "pi_keep", rec
    print("OK: _record_payment never downgrades paid -> free")


# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    global _CLIENT
    with tempfile.TemporaryDirectory() as td:
        # Redirect OUTPUT_DIR (read as a module global inside the handler at
        # call time) and seed a dummy build pack for the valid job.
        pr.OUTPUT_DIR = Path(td)
        (pr.OUTPUT_DIR / VALID_JOB).mkdir(parents=True)
        (pr.OUTPUT_DIR / VALID_JOB / "artifact.zip").write_text("dummy")

        app = FastAPI()
        app.include_router(pr.pay_router, prefix="/jobs")
        app.include_router(pr.webhook_router)
        _CLIENT = TestClient(app)

        try:
            test_free_path()
            test_below_min()
            test_negative_amount()
            test_over_max_amount()
            test_missing_payment_method()
            test_provider_unavailable()
            test_charge_success()
            test_requires_action()
            test_retryable_error()
            test_permanent_error()
            test_invalid_job_id()
            test_job_not_found()
            test_webhook_no_secret()
            test_webhook_bad_signature()
            test_webhook_records_paid()
            test_webhook_ignores_other_events()
            test_record_no_downgrade()
        finally:
            reg._reset_for_tests()
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)

    print()
    print("All pay_router tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
