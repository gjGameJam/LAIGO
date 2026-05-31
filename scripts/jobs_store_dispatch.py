"""
Backend dispatcher for the mosaic job lifecycle (Phase D step 2).

Routes every public call to either `jobs_store_pg` (Postgres, on Neon) or
`jobs_store_json` (in-process dict) based on the `DB_BACKEND` env var,
re-checked per call.

Lifetime of this module
=======================
This is migration scaffolding. After Phase F cuts over to Postgres and
the one-week observation window closes, the JSON backend is deleted and
this dispatcher collapses into a single `from .jobs_store_pg import *`.
See `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.F` final cleanup commit.

Until then, the dual-backend setup gives us three things:
- Local dev without Neon (devs run `uvicorn` without provisioning a DB)
- Rollback insurance for Phase F (flip env back, no redeploy)
- Per-environment routing (dev = JSON, staging = Neon dev branch,
  prod-post-cutover = Neon `main` branch)

Why runtime check (not import-time)
===================================
The Phase B retrospective (AD3) established that import-time dispatch
locks the backend for the process lifetime and breaks pytest scenarios
that need to switch backends between cases. Per-call `os.environ.get()`
costs microseconds compared to the actual lifecycle work — fully
acceptable. In production, `DB_BACKEND` is set once at deploy and never
changes, so the runtime check is effectively a constant branch.

The same pattern is used by `scripts/checkout/checkout_store_dispatch.py`
(Phase C). That dispatcher has a tiny local `_is_postgres()` helper to
avoid a circular import via the `payment` subpackage; this dispatcher
lives at `scripts/` and uses the canonical `db.is_postgres_backend()`
directly.

Per-call vs per-backend semantics
=================================
- Lifecycle writes / reads (insert_queued, get_job, mark_*, etc.) route
  ONLY to the active backend.
- set_event_loop / clear_event_loop / _reset_for_tests fan out to BOTH
  backends. The inactive one needs a valid loop ref in case a test
  swaps backends; resets must clean ALL state so test isolation holds
  regardless of starting backend.
"""

from typing import Any, Optional

import asyncio

from . import jobs_store_json, jobs_store_pg
from .db import is_postgres_backend

__all__ = (
    # Event loop capture
    "set_event_loop",
    "clear_event_loop",
    # Lifecycle: INSERT
    "insert_queued",
    # Lifecycle: SELECT
    "get_job",
    "list_queued",
    "list_running",
    "count_active",
    "count_queued",
    # Lifecycle: atomic dequeue
    "dequeue_next",
    # Lifecycle: UPDATE
    "mark_running",
    "mark_complete",
    "mark_failed",
    "mark_timed_out",
    "write_progress",
    # Lifecycle: DELETE
    "delete",
    "cleanup_expired",
    "cleanup_terminal_sagas",
    # Sync wrappers (thread → event loop)
    "mark_running_from_thread",
    "mark_complete_from_thread",
    "mark_failed_from_thread",
    "mark_timed_out_from_thread",
    "write_progress_from_thread",
    "delete_from_thread",
    # Test helpers
    "_reset_for_tests",
)


def _backend():
    """Return the active backend module per the current `DB_BACKEND` env."""
    return jobs_store_pg if is_postgres_backend() else jobs_store_json


# ─── Event loop capture (fan out to BOTH backends) ───────────────────────────


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the FastAPI event loop on BOTH backends.

    Both modules' `*_from_thread` wrappers need a loop ref. Fanning out
    is microscopic cost and protects against test scenarios that flip
    `DB_BACKEND` mid-process — the inactive backend's wrappers would
    silently no-op otherwise (clean degradation, but confusing).
    """
    jobs_store_pg.set_event_loop(loop)
    jobs_store_json.set_event_loop(loop)


def clear_event_loop() -> None:
    """Drop the event-loop ref on BOTH backends."""
    jobs_store_pg.clear_event_loop()
    jobs_store_json.clear_event_loop()


# ─── Lifecycle (route to active backend only) ────────────────────────────────


async def insert_queued(
    *,
    job_id: str,
    mosaic_type: str,
    width_blocks: int,
    background_pct: Optional[float],
    ttl_seconds: int,
    dither: bool = True,
    upload_filename: Optional[str] = None,
    customer_email: Optional[str] = None,
    customer_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    return await _backend().insert_queued(
        job_id=job_id,
        mosaic_type=mosaic_type,
        width_blocks=width_blocks,
        background_pct=background_pct,
        ttl_seconds=ttl_seconds,
        dither=dither,
        upload_filename=upload_filename,
        customer_email=customer_email,
        customer_ip=customer_ip,
        user_agent=user_agent,
    )


async def get_job(job_id: str) -> Optional[dict]:
    return await _backend().get_job(job_id)


async def list_queued() -> list[dict]:
    return await _backend().list_queued()


async def list_running() -> list[dict]:
    return await _backend().list_running()


async def count_active() -> int:
    return await _backend().count_active()


async def count_queued() -> int:
    return await _backend().count_queued()


async def dequeue_next() -> Optional[dict]:
    return await _backend().dequeue_next()


async def mark_running(job_id: str) -> bool:
    return await _backend().mark_running(job_id)


async def mark_complete(job_id: str) -> None:
    return await _backend().mark_complete(job_id)


async def mark_failed(job_id: str, error_message: str) -> None:
    return await _backend().mark_failed(job_id, error_message)


async def mark_timed_out(job_id: str, error_message: str) -> None:
    return await _backend().mark_timed_out(job_id, error_message)


async def write_progress(job_id: str, pct: float) -> None:
    return await _backend().write_progress(job_id, pct)


async def delete(job_id: str) -> None:
    return await _backend().delete(job_id)


async def cleanup_expired() -> list[str]:
    return await _backend().cleanup_expired()


async def cleanup_terminal_sagas(retention_days: int = 90) -> list[str]:
    return await _backend().cleanup_terminal_sagas(retention_days)


# ─── Sync wrappers (route to active backend) ─────────────────────────────────


def mark_running_from_thread(job_id: str) -> None:
    _backend().mark_running_from_thread(job_id)


def mark_complete_from_thread(job_id: str) -> None:
    _backend().mark_complete_from_thread(job_id)


def mark_failed_from_thread(job_id: str, error_message: str) -> None:
    _backend().mark_failed_from_thread(job_id, error_message)


def mark_timed_out_from_thread(job_id: str, error_message: str) -> None:
    _backend().mark_timed_out_from_thread(job_id, error_message)


def write_progress_from_thread(job_id: str, pct: float) -> None:
    _backend().write_progress_from_thread(job_id, pct)


def delete_from_thread(job_id: str) -> None:
    _backend().delete_from_thread(job_id)


# ─── Test helpers (fan out) ──────────────────────────────────────────────────


def _reset_for_tests() -> None:
    """Reset state on BOTH backends so test isolation is independent of
    which backend a prior test exercised. PG side is a no-op (jobs_store_pg
    has no process-local state); JSON side clears `_jobs` + the event-loop
    ref."""
    jobs_store_pg._reset_for_tests()
    jobs_store_json._reset_for_tests()
