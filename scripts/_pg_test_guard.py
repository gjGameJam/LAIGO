"""Safety guard: refuse to run a write-heavy PG test/smoke against PRODUCTION.

Why this exists
===============
The Phase E / reconcile / smoke suites read ``DATABASE_URL`` and WRITE rows
(jobs, checkouts, sagas, payment_holds, audit_events). On 2026-06-11 a suite
ran against prod ``main`` because ``.env.secrets``'s ``DATABASE_URL`` still
pointed there — leaving orphaned ``phase-e-test-*`` fixtures that the prod
reconciler then churned on. This guard turns that silent footgun into a loud,
early failure.

Usage
=====
Call ``assert_safe_test_db()`` at the top of every PG test/smoke ``main()``,
AFTER ``load_dotenv(...)`` (so ``DATABASE_URL`` is populated) and BEFORE the
first DB connection / ``init_pool()``.

The production endpoint(s) are matched as host substrings. Defaults cover the
current Neon prod endpoint; override or extend with the
``PROD_DB_HOST_MARKERS`` env var (comma-separated) if prod ever moves.

There is intentionally NO bypass flag: these suites must never write to prod.
To run them, point ``DATABASE_URL`` at a dev/test Neon branch.
"""

import os
import re
import sys

# Host substrings that identify the PRODUCTION database. A DATABASE_URL whose
# host contains any of these is refused. Keep this list in sync if the prod
# Neon endpoint changes (or set PROD_DB_HOST_MARKERS at runtime).
_DEFAULT_PROD_MARKERS = ("ep-delicate-math-apvsnvjx",)


def _prod_markers() -> tuple[str, ...]:
    env = os.environ.get("PROD_DB_HOST_MARKERS", "").strip()
    if env:
        return tuple(m.strip() for m in env.split(",") if m.strip())
    return _DEFAULT_PROD_MARKERS


def _db_host(dsn: str) -> str:
    m = re.search(r"@([^/:\s]+)", dsn or "")
    return m.group(1) if m else ""


def assert_safe_test_db() -> None:
    """Raise SystemExit(2) if DATABASE_URL points at the production database.

    No-op when DATABASE_URL is unset (the test will fail later with a clearer
    connection error) or when its host matches no production marker.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    host = _db_host(dsn)
    for marker in _prod_markers():
        if marker and marker in host:
            sys.stderr.write(
                "\n[PG TEST GUARD] REFUSING TO RUN against the PRODUCTION database.\n"
                f"  DATABASE_URL host contains the prod marker {marker!r} "
                f"(host={host!r}).\n"
                "  These suites WRITE rows and must target a dev/test Neon branch.\n"
                "  Fix: set DEV_DATABASE_URL in .env.secrets to your dev-branch DSN "
                "(preferred), or point DATABASE_URL at the dev branch.\n"
                "  (Override markers via PROD_DB_HOST_MARKERS if prod moved.)\n\n"
            )
            raise SystemExit(2)


def use_dev_db_if_set() -> None:
    """Route DATABASE_URL to DEV_DATABASE_URL for this process, if set.

    Lets operators keep ``DATABASE_URL`` pointed at prod (what the app/runtime
    uses) while the PG test/smoke suites target a dedicated dev Neon branch via
    ``DEV_DATABASE_URL`` — no swapping of the prod DSN in .env.secrets. No-op
    when DEV_DATABASE_URL is unset/empty.
    """
    dev = os.environ.get("DEV_DATABASE_URL", "").strip()
    if dev:
        os.environ["DATABASE_URL"] = dev


def prepare_test_db() -> None:
    """Call at the top of every PG test/smoke ``main()`` (after ``load_dotenv``).

    Prefers ``DEV_DATABASE_URL`` if present, then refuses to run if the
    resulting ``DATABASE_URL`` still points at production. This is the single
    entry point the suites should use.
    """
    use_dev_db_if_set()
    assert_safe_test_db()
