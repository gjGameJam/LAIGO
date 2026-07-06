"""Regression tests for the Wave-1 web-security hardening.

1. The /artifacts static mount must serve ONLY public build-pack files and
   refuse the per-job PII/financial sidecars (payment.json, email.json,
   manifest_failed.json) — the primary fix for the /queue -> /artifacts
   disclosure chain. Even though those sidecars now live under PRIVATE_DIR, the
   mount must also fail closed if any sensitive file ever reappears under
   OUTPUT_DIR.
2. The per-IP /generate rate limiter allows the first request and rejects a
   second within the cooldown with 429 + Retry-After.

No network, no DB, no worker. Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_web_hardening
"""

import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from scripts import Main
from scripts.Main import _AllowlistStaticFiles

JOB = "job-abc-123"

_ALLOWED = [
    "artifact.zip", "preview.json", "stats.json", "manifest.json",
    "order_list.json", "order_list_1.json", "order_list_12.json",
]
_DENIED = [
    "payment.json", "email.json", "manifest_failed.json",
    "secrets.txt", ".env", "notes",
]


def test_artifacts_allowlist():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        job = root / JOB
        job.mkdir(parents=True)
        for name in _ALLOWED + _DENIED:
            (job / name).write_text("x")

        app = FastAPI()
        app.mount("/artifacts", _AllowlistStaticFiles(directory=root), name="artifacts")
        client = TestClient(app)

        for name in _ALLOWED:
            r = client.get(f"/artifacts/{JOB}/{name}")
            assert r.status_code == 200, f"allowed {name} should be 200, got {r.status_code}"
        print(f"OK: {len(_ALLOWED)} public build-pack files served (200)")

        for name in _DENIED:
            r = client.get(f"/artifacts/{JOB}/{name}")
            assert r.status_code == 404, f"denied {name} should be 404, got {r.status_code}"
        print(f"OK: {len(_DENIED)} sensitive/unknown files denied (404) — "
              "incl. payment.json, email.json, manifest_failed.json")

        # A directory request must not list contents.
        r = client.get(f"/artifacts/{JOB}/")
        assert r.status_code == 404, f"directory request should 404, got {r.status_code}"
        print("OK: directory request denied (no listing)")


def test_generate_rate_limiter():
    Main._last_generate_by_ip.clear()
    ip = "203.0.113.7"

    Main._enforce_generate_rate_limit(ip)  # first request: allowed

    raised = False
    try:
        Main._enforce_generate_rate_limit(ip)  # second within cooldown: rejected
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 429, exc.status_code
        assert "Retry-After" in (exc.headers or {}), f"missing Retry-After: {exc.headers}"
    assert raised, "second /generate within the cooldown must raise 429"

    # A different IP is independent.
    Main._enforce_generate_rate_limit("198.51.100.9")
    # Unknown IP (no XFF) is a no-op — nothing to key on.
    Main._enforce_generate_rate_limit(None)
    Main._last_generate_by_ip.clear()
    print("OK: rate limiter — 1st allowed, 2nd within window -> 429 + Retry-After; "
          "distinct IP independent; None ip no-op")


def main() -> int:
    test_artifacts_allowlist()
    test_generate_rate_limiter()
    print("\nAll web-hardening tests PASSED.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
