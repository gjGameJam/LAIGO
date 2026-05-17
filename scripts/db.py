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
import os
import ssl
from typing import Optional

import asyncpg

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

        if "-pooler" not in dsn and "neon" in dsn:
            raise RuntimeError(
                "DATABASE_URL points at the Neon DIRECT endpoint, not the pooler. "
                "App traffic must use the pooled endpoint (host suffix contains '-pooler'). "
                "The direct endpoint is reserved for migrations and psql debugging."
            )

        ssl_ctx: Optional[ssl.SSLContext]
        if "sslmode=" in dsn or "neon" in dsn:
            ssl_ctx = ssl.create_default_context()
        else:
            ssl_ctx = None

        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            max_inactive_connection_lifetime=300,
            statement_cache_size=0,
            command_timeout=10.0,
            ssl=ssl_ctx,
        )

        async with _pool.acquire() as conn:
            await conn.execute("SELECT 1")


async def close_pool() -> None:
    """Close the pool. Idempotent."""
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


async def _reset_for_tests() -> None:
    """Test-only escape hatch — close and clear the pool between scenarios.

    Mirrors `payment.registry._reset_for_tests()`. Safe to call from
    production shutdown too (close_pool already handles the None case).
    """
    await close_pool()
