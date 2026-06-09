"""
Postgres connection pool for LAIGO (Phase 9 — Neon migration).

Single asyncpg pool per FastAPI app instance. Created in lifespan startup,
closed in shutdown. All persistent-state callers (checkout_store, job
lifecycle, audit log) acquire connections from this pool.

Design contract (see docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.2.4):
- Neon pooler endpoint ONLY for app traffic (host suffix contains `-pooler`).
  Direct endpoint is reserved for `alembic upgrade head` and psql debugging.
- `statement_cache_size=0` because Neon's pooler runs PgBouncer in
  transaction mode, which is incompatible with asyncpg's prepared statements.
- `command_timeout=10s` is a per-query belt; the saga-level timeout
  (`SAGA_TIMEOUT_SECONDS=900`, B4) remains the authoritative ceiling.
- TLS is mandatory for Neon. asyncpg's default SSL context is used when the
  DSN host contains `neon` or the DSN includes `sslmode=`.

The pool is gated behind `DB_BACKEND=postgres`. During the staged migration
(Phases B–E) the JSON-backed code paths still run; flipping the env var at
Phase F switches to DB-backed state. See §9.3.11.11.
"""

import asyncio
import json
import os
import ssl
from typing import Optional
from urllib.parse import urlsplit

import asyncpg


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Per-connection init callback for asyncpg.create_pool(init=...).

    Registers a typecodec so JSONB columns transparently encode/decode as Python
    dict/list — callers can pass `[{...}]` or `{"key": ...}` directly to
    asyncpg without `json.dumps()`, and SELECT results come back as Python
    objects, not strings.

    Without this codec, asyncpg with `statement_cache_size=0` (required for
    Neon's transaction-mode pooler) returns JSONB as `str` and requires
    callers to `json.loads()` manually, polluting every query site.

    `format='text'` (NOT 'binary') is correct for transaction-mode pooler:
    PgBouncer in transaction mode doesn't reliably support the binary JSONB
    protocol that asyncpg uses by default — text format is universally safe.
    See https://www.postgresql.org/docs/current/datatype-json.html and
    https://magicstack.github.io/asyncpg/current/usage.html#example-automatic-type-conversion.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


def get_pool() -> asyncpg.Pool:
    """Return the live pool. Raises if init_pool() has not run."""
    if _pool is None:
        raise RuntimeError(
            "DB pool not initialized. "
            "Either DB_BACKEND is not 'postgres' or lifespan startup order is wrong "
            "(init_pool must run before any caller acquires a connection)."
        )
    return _pool


def is_postgres_backend() -> bool:
    """Return True iff DB_BACKEND env requests the Postgres path.

    Until Phase F flips the flag, the JSON-backed checkout_store and
    in-memory job lifecycle remain authoritative. This helper exists so
    callers can branch without each one re-reading the env.
    """
    return os.environ.get("DB_BACKEND", "json").lower() == "postgres"


async def init_pool() -> None:
    """Create the asyncpg pool from DATABASE_URL. Idempotent.

    Raises if DB_BACKEND=postgres but DATABASE_URL is missing or malformed.
    No-op when DB_BACKEND != postgres (intentional: dev can still boot
    without provisioning Neon while Phase B–E are in progress).
    """
    global _pool
    if not is_postgres_backend():
        return

    async with _pool_lock:
        if _pool is not None:
            return  # already initialized; lifespan re-run guard

        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "DB_BACKEND=postgres but DATABASE_URL is not set. "
                "Set it in .env.secrets (Neon pooler DSN — host must contain '-pooler')."
            )

        # Parse the host explicitly instead of substring-matching the whole DSN —
        # a password coincidentally containing 'pooler' or 'neon' would otherwise
        # mis-trigger this guard.
        host = (urlsplit(dsn).hostname or "").lower()
        # Boot-visible "which DB are we actually on" signal. Cheap; one log
        # line per process. Caught the Phase F cutover ep-mismatch on
        # 2026-05-22 where DATABASE_URL and DATABASE_URL_DIRECT pointed at
        # different Neon endpoints. See docs/DATABASE_OPS.md "Debug playbook".
        import logging as _logging
        _logging.getLogger("laigo.db").info(f"DATABASE_URL host={host!r}")
        if "neon.tech" in host and "-pooler" not in host:
            raise RuntimeError(
                "DATABASE_URL points at the Neon DIRECT endpoint, not the pooler. "
                "App traffic must use the pooled endpoint (host suffix contains '-pooler'). "
                "The direct endpoint is reserved for migrations and psql debugging."
            )

        # TLS: enable when the DSN explicitly requests it OR when targeting a
        # Neon host (Neon always requires TLS). Hostname parsed via urlsplit
        # for the same reason as the pooler check above — avoid password
        # substring false positives.
        ssl_ctx: Optional[ssl.SSLContext]
        if "sslmode=" in dsn or "neon.tech" in host:
            ssl_ctx = ssl.create_default_context()
        else:
            ssl_ctx = None

        # Atomicity: assign the module-level _pool ONLY after the connectivity
        # smoke test succeeds. Previously, create_pool() assigned _pool first
        # and then SELECT 1 ran — if the smoke test failed, _pool stayed set to
        # an unhealthy pool, and a retry of init_pool() would short-circuit at
        # the `if _pool is not None` guard above and falsely report success.
        new_pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            max_inactive_connection_lifetime=300,
            statement_cache_size=0,
            command_timeout=10.0,
            ssl=ssl_ctx,
            # Per-connection init: register the JSONB codec so callers can
            # pass/receive Python dict/list for JSONB columns transparently.
            # See _register_jsonb_codec docstring above.
            init=_register_jsonb_codec,
        )
        try:
            async with new_pool.acquire() as conn:
                await conn.execute("SELECT 1")
        except Exception:
            # Best-effort close so we don't leak the half-initialized pool's
            # background connections. asyncpg.Pool.close() awaits in-flight
            # work; on a fresh pool with no callers this is near-instant.
            await new_pool.close()
            raise

        _pool = new_pool


async def close_pool() -> None:
    """Close the pool. Idempotent."""
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


# ─── schema version verification (Phase B step 5) ───────────────────────────
# Bump this string each time a new alembic migration is added. The value MUST
# equal the latest revision identifier under scripts/migrations/versions/.
# Refusing boot on a mismatch catches the classic deploy-order mistake:
# new app code shipped against an old DB schema (or vice versa).
_EXPECTED_SCHEMA_VERSION = "0003"


async def verify_schema() -> None:
    """Refuse boot if the DB schema revision != _EXPECTED_SCHEMA_VERSION.

    No-op when DB_BACKEND != postgres (the JSON path has no schema concept).
    Raises RuntimeError on:
      * alembic_version table missing (migrations never applied)
      * alembic_version table empty (initial migration partially applied)
      * version mismatch (deploy ordering bug)

    Call AFTER init_pool() in the lifespan; the pool must be live.

    See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.B step 5 and §9.3.3.
    """
    if not is_postgres_backend():
        return

    pool = get_pool()
    try:
        row = await pool.fetchrow("SELECT version_num FROM alembic_version")
    except asyncpg.UndefinedTableError as exc:
        raise RuntimeError(
            "alembic_version table missing — schema migrations were never applied "
            "to this database. From a shell pointed at the Neon DIRECT endpoint:\n"
            "    $env:ALEMBIC_DATABASE_URL = '<direct DSN>'\n"
            "    alembic upgrade head"
        ) from exc

    if row is None:
        raise RuntimeError(
            "alembic_version table is empty — a migration likely failed mid-apply. "
            "Inspect the DB, then re-run: alembic upgrade head"
        )

    current = row["version_num"]
    if current != _EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Schema version mismatch: DB at {current!r}, app expects "
            f"{_EXPECTED_SCHEMA_VERSION!r}. Run `alembic upgrade head` against "
            f"the Neon DIRECT endpoint BEFORE redeploying app code."
        )


def verify_alembic_head_matches_expected() -> None:
    """Refuse boot if `_EXPECTED_SCHEMA_VERSION` drifts from alembic's head.

    Code-time invariant: runs UNCONDITIONALLY (regardless of DB_BACKEND).
    Catches the "developer added a 000N migration but forgot to bump the
    constant" (or vice versa) bug at boot — even in JSON-mode dev where
    `verify_schema()` is a no-op.

    No DB connection required; reads scripts/migrations/versions/ from
    disk via alembic.script.ScriptDirectory. ~30ms.

    Resolution is anchored to this file's location (not the working
    directory), so `uvicorn scripts.Main:app` works regardless of where
    it was launched from.

    See PRE_RELEASE §4 B43.
    """
    # Late import — alembic is in requirements.txt but we don't need it
    # at module load. Keeps `from .db import ...` cheap for callers that
    # don't trigger verification.
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script_location = str(Path(__file__).resolve().parent / "migrations")
    cfg = Config()
    cfg.set_main_option("script_location", script_location)

    try:
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as exc:
        # ScriptDirectory raises CommandError on multi-head (branched
        # migrations). If we ever support branched migrations,
        # _EXPECTED_SCHEMA_VERSION becomes a tuple and this check needs
        # to use get_heads(); until then, surface the multi-head case
        # loudly so it can't ship silently.
        raise RuntimeError(
            f"verify_alembic_head_matches_expected: failed to read alembic "
            f"versions at {script_location}: {type(exc).__name__}: {exc}. "
            f"If alembic now has multiple heads (branched migrations), "
            f"_EXPECTED_SCHEMA_VERSION needs to be updated to a tuple."
        ) from exc

    if head != _EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Code drift: scripts/db.py:_EXPECTED_SCHEMA_VERSION = "
            f"{_EXPECTED_SCHEMA_VERSION!r}, but the alembic versions/ head is "
            f"{head!r}. Either bump _EXPECTED_SCHEMA_VERSION to {head!r}, OR "
            f"remove the orphan migration file under "
            f"scripts/migrations/versions/."
        )


async def _reset_for_tests() -> None:
    """Test-only escape hatch — close and clear the pool between scenarios.

    Mirrors `payment.registry._reset_for_tests()`. Safe to call from
    production shutdown too (close_pool already handles the None case).
    """
    await close_pool()
