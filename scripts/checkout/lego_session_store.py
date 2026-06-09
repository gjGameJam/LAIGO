"""
LEGO.com Playwright storage_state persistence (Neon-backed).

Loads/saves the Playwright session cookie+localStorage bundle for the
LAIGO-owned LEGO.com account. Seeded once via scripts/seed_lego_session.py
after a manual Google SSO login — LEGO.com 2FA is email-only (no TOTP/SMS
option), so we cannot complete the login challenge headlessly. The cached
state is reused by lego_client.order_from_lego on every order.

When the cached state expires, lego_client raises LegoSessionExpiredError
and the saga writes MANUAL_REVIEW; the operator re-runs the seed script.
See docs/LEGO_SESSION.md (TBD in task #8) for the refresh runbook.

Backend behavior:
- DB_BACKEND=postgres: reads/writes the external_sessions row keyed by
  provider='lego'. Schema added in migration 0003.
- DB_BACKEND=json (legacy / dev without Neon): load returns None and
  logs a warning; save raises RuntimeError. There is no on-disk fallback
  by design — storage_state is sensitive enough that we want a single
  authoritative location (the DB) and a single seeding entry point.

JSONB codec: the per-connection codec registered in scripts/db.py encodes
Python dict <-> JSONB transparently. Pass storage_state as a dict; do NOT
json.dumps at the call site.
"""

import logging
from typing import Optional

from ..db import get_pool, is_postgres_backend

logger = logging.getLogger("laigo.lego_session_store")

_PROVIDER = "lego"


async def load_storage_state() -> Optional[dict]:
    """Return the cached Playwright storage_state dict, or None if not seeded.

    None is the expected "no row yet" signal for lego_client; it should
    raise LegoSessionExpiredError('not_seeded') so the saga writes
    MANUAL_REVIEW with a runbook entry pointing to scripts/seed_lego_session.py.

    Returns None (with a WARN) when DB_BACKEND != postgres. Production
    runs on postgres exclusively post-Phase-F, so this branch is only hit
    in dev environments that haven't switched yet.
    """
    if not is_postgres_backend():
        logger.warning(
            "lego_session_store.load: DB_BACKEND != postgres; storage_state "
            "caching is unavailable. Switch to DB_BACKEND=postgres to seed."
        )
        return None

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT storage_state FROM external_sessions WHERE provider = $1",
        _PROVIDER,
    )
    if row is None:
        return None
    return row["storage_state"]


async def save_storage_state(
    storage_state: dict,
    notes: Optional[str] = None,
) -> None:
    """UPSERT storage_state for provider='lego'.

    `storage_state` is the dict returned by Playwright's
    BrowserContext.storage_state() — a {cookies: [...], origins: [...]}
    shape. The JSONB codec encodes it; do NOT json.dumps here.

    `notes` is optional free text surfaced in the refresh runbook
    (e.g. 'seeded 2026-06-09 via Google SSO').

    Raises RuntimeError if DB_BACKEND != postgres — the seed script
    requires Neon-backed state by design.
    """
    if not is_postgres_backend():
        raise RuntimeError(
            "lego_session_store.save_storage_state requires DB_BACKEND=postgres. "
            "Set DB_BACKEND=postgres in .env before seeding."
        )

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO external_sessions (provider, storage_state, notes, updated_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (provider) DO UPDATE
        SET storage_state = EXCLUDED.storage_state,
            notes         = EXCLUDED.notes,
            updated_at    = NOW()
        """,
        _PROVIDER,
        storage_state,
        notes,
    )
    logger.info(
        f"lego_session_store: storage_state saved for provider={_PROVIDER!r} "
        f"(notes={notes!r})"
    )
