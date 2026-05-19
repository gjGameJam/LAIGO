"""
Postgres shadow-write helpers for the mosaic job lifecycle (Phase D-foundation).

This module is intentionally minimal. It provides the **smallest** set of
writes needed to keep the `jobs` table consistent with `app.state.jobs` —
just enough for the checkout pipeline's FK constraints (`checkouts.job_id`,
`sagas.job_id`) to resolve at /confirm time.

It is NOT the full Phase D refactor. `app.state.jobs` remains the source of
truth for the runtime lifecycle (the scheduler, cleanup, and /jobs/{id}
endpoint all still read in-memory state). The DB rows here are a write-only
mirror used as the FK target for the checkout tables.

Design contract:
- All writes are **best-effort, fire-and-forget**. Exceptions are logged
  CRITICAL and swallowed; the in-memory lifecycle event already succeeded.
  This matches the user's scope choice (D-foundation, not synchronous-fatal).
  See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.D follow-up for the
  source-of-truth flip (Phase D step 2).
- All writes are **no-ops** when DB_BACKEND != postgres. The JSON-mode
  path doesn't need (or have) any DB rows.
- Callers in async contexts await the coroutine directly.
- Callers in thread contexts (scheduler, cleanup, executor done-callback)
  use the `*_from_thread` wrappers, which submit to the FastAPI event loop
  via `asyncio.run_coroutine_threadsafe` (fire-and-forget).

The FastAPI event loop reference is captured once at lifespan startup via
`set_event_loop()`. If the loop isn't captured (early startup, late
shutdown, or unit tests), `*_from_thread` calls silently drop the write
and log DEBUG — the in-memory lifecycle is unaffected.

Schema mapping (per §9.2.1):
  jobs.job_id          ← caller-provided UUID
  jobs.status          ← state machine: queued → running → complete|failed|timed_out
  jobs.mosaic_type     ← settings["mosaic_type"] ('2d' | '3d')
  jobs.width_blocks    ← settings["mosaic_block_width"]
  jobs.background_pct  ← settings["background_color_percent"]
  jobs.dither          ← always TRUE today (the API doesn't expose a toggle;
                          Floyd-Steinberg is the only color-mapping path
                          in picToMosiac.py). Schema column reserved for a
                          future API param.
  jobs.ttl_expires_at  ← queued_at + JOB_TTL_SECONDS
  jobs.started_at      ← set by mark_running()
  jobs.completed_at    ← set by mark_complete/failed/timed_out
  jobs.error_message   ← set by mark_failed/timed_out

Schema columns NOT shadow-written:
  jobs.progress_pct    — would require per-percent UPDATEs, too noisy for
                          shadow-write scope. The .progress file remains
                          the source of truth for progress in D-foundation.
  jobs.upload_filename — would be useful for audit; deferred to Phase D step 2.
  jobs.customer_email/customer_ip/user_agent — Phase E (audit correlation).
"""

import asyncio
import logging
from typing import Optional

import asyncpg

from .db import get_pool, is_postgres_backend

logger = logging.getLogger("laigo.jobs_db")

# Module-level event-loop reference, captured at lifespan startup. Used by
# `*_from_thread` wrappers to submit coroutines from the scheduler/cleanup
# threads to the FastAPI event loop. None means "not captured yet" or "in
# shutdown" — sync wrappers silently no-op in either case.
_loop_ref: Optional[asyncio.AbstractEventLoop] = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the FastAPI event loop for use by *_from_thread wrappers.

    Called once during lifespan startup BEFORE any scheduler/cleanup thread
    starts. Idempotent (a second call replaces the reference, which is the
    correct behavior for hot-reload scenarios).
    """
    global _loop_ref
    _loop_ref = loop


def clear_event_loop() -> None:
    """Drop the event-loop reference at lifespan shutdown.

    After this returns, *_from_thread calls will silently no-op. Worker
    threads finishing late in shutdown won't crash trying to submit work
    to a dead loop.
    """
    global _loop_ref
    _loop_ref = None


def _submit_from_thread(coro: "asyncio.Coroutine") -> None:
    """Fire-and-forget submit of a coroutine to the FastAPI event loop.

    Safe to call from a non-async thread (scheduler, cleanup, executor
    done-callback). If the event loop isn't available, the coroutine is
    closed (no-op) and a DEBUG log line is emitted — the caller's
    lifecycle event in memory already succeeded.
    """
    loop = _loop_ref
    if loop is None or not loop.is_running():
        # Drop quietly. Don't log CRITICAL here — this happens normally
        # during shutdown and pre-startup, and would be alarm fatigue.
        coro.close()
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError as exc:
        # Loop closed between the check and the submit. Rare race.
        logger.debug(f"[jobs_db] thread submit raced loop close: {exc}")
        coro.close()


# ─── Lifecycle write functions (async) ───────────────────────────────────────


async def insert_queued(
    *,
    job_id: str,
    mosaic_type: str,
    width_blocks: int,
    background_pct: Optional[float],
    ttl_seconds: int,
) -> None:
    """INSERT INTO jobs at /generate time. Idempotent via ON CONFLICT.

    No-op when DB_BACKEND != postgres. Best-effort.

    `ON CONFLICT (job_id) DO NOTHING` means a retry from the API layer
    (e.g., a client double-POSTs the same upload due to a flaky connection
    that re-uses the same job_id by accident) doesn't fail loudly. The
    real defense against duplicate job_ids is the uuid4 generation in the
    route handler.
    """
    if not is_postgres_backend():
        return
    pool = get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO jobs (
                job_id, status, mosaic_type, width_blocks, background_pct,
                dither, ttl_expires_at, queued_at
            ) VALUES (
                $1, 'queued', $2, $3, $4, TRUE,
                NOW() + make_interval(secs => $5),
                NOW()
            )
            ON CONFLICT (job_id) DO NOTHING
            """,
            job_id,
            mosaic_type,
            width_blocks,
            int(background_pct) if background_pct is not None else None,
            ttl_seconds,
        )
    except Exception as exc:
        logger.critical(
            f"[jobs_db] insert_queued({job_id}) failed (DB shadow write): {exc}. "
            "Lifecycle continues; checkout FK may fail for this job_id if /confirm "
            "is called before reconciliation."
        )


async def _exec_under_lock(job_id: str, sql: str, *args) -> None:
    """Run a single UPDATE inside a per-job_id advisory-lock transaction.

    All jobs-lifecycle UPDATEs route through here so concurrent writes
    (mark_running, mark_complete, mark_failed, mark_timed_out) for the
    same job_id serialize at the DB level — eliminates B41
    (mark_complete winning the row-lock race against mark_running and
    leaving started_at NULL). The advisory lock is xact-scoped, released
    automatically on COMMIT/ROLLBACK.

    Hash function is `hashtextextended($1, 0)` (INT8) — matches the
    pattern in checkout_store_pg.update(). Lower collision rate than the
    older `hashtext($1)` INT4 variant.

    See PRE_RELEASE §4 B41.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                job_id,
            )
            await conn.execute(sql, job_id, *args)


async def mark_running(job_id: str) -> None:
    """UPDATE jobs SET status='running', started_at=NOW() at scheduler dispatch.

    No-op when DB_BACKEND != postgres. Best-effort.

    Serialized with other jobs-lifecycle UPDATEs for the same job_id via
    pg_advisory_xact_lock (B41 fix). The `AND status = 'queued'` filter
    is preserved so a re-fired mark_running cannot downgrade a terminal
    status; if mark_complete won the lock, mark_running matches 0 rows
    and the terminal write's COALESCE(started_at, NOW()) backfills.

    Does NOT touch progress_pct — shadow writes don't mirror progress in
    D-foundation. Phase D step 2 wires the scheduler thread to mirror
    .progress file → DB on every tick.
    """
    if not is_postgres_backend():
        return
    try:
        await _exec_under_lock(
            job_id,
            """
            UPDATE jobs
            SET status = 'running', started_at = NOW()
            WHERE job_id = $1 AND status = 'queued'
            """,
        )
    except Exception as exc:
        logger.critical(f"[jobs_db] mark_running({job_id}) failed: {exc}")


async def mark_complete(job_id: str) -> None:
    """UPDATE jobs SET status='complete', completed_at=NOW(), progress_pct=100.

    No-op when DB_BACKEND != postgres. Best-effort.

    started_at uses COALESCE — if mark_running's UPDATE lost the row-lock
    race (the executor callback can fire before mark_running's first
    await flushes, common for very fast jobs) and never matched, started_at
    would otherwise stay NULL. With COALESCE the terminal write
    backfills it so /jobs/{id} and operator dashboards always have a
    started_at to compute job duration from.
    """
    if not is_postgres_backend():
        return
    try:
        await _exec_under_lock(
            job_id,
            """
            UPDATE jobs
            SET status = 'complete',
                started_at = COALESCE(started_at, NOW()),
                completed_at = NOW(),
                progress_pct = 100,
                error_message = NULL
            WHERE job_id = $1
            """,
        )
    except Exception as exc:
        logger.critical(f"[jobs_db] mark_complete({job_id}) failed: {exc}")


async def mark_failed(job_id: str, error_message: str) -> None:
    """UPDATE jobs SET status='failed', completed_at=NOW(), error_message=$.

    No-op when DB_BACKEND != postgres. Best-effort.

    The `error_message` is the same internal string written to
    manifest_failed.json — operator-facing, may contain stack-trace
    fragments. Truncated to 4 KiB to keep the column reasonable.

    started_at uses COALESCE so a fast-failing job whose mark_running
    UPDATE lost the row-lock race still gets a started_at backfilled.
    See mark_complete docstring for full race rationale.
    """
    if not is_postgres_backend():
        return
    try:
        await _exec_under_lock(
            job_id,
            """
            UPDATE jobs
            SET status = 'failed',
                started_at = COALESCE(started_at, NOW()),
                completed_at = NOW(),
                error_message = $2
            WHERE job_id = $1
            """,
            (error_message or "")[:4096],
        )
    except Exception as exc:
        logger.critical(f"[jobs_db] mark_failed({job_id}) failed: {exc}")


async def mark_timed_out(job_id: str, error_message: str) -> None:
    """UPDATE jobs SET status='timed_out', completed_at=NOW(), error_message=$.

    No-op when DB_BACKEND != postgres. Best-effort.

    Distinct terminal status from 'failed' — used when the watchdog forces
    a job that exceeded JOB_TIMEOUT_SECONDS. Allows operators to triage
    "stuck jobs" separately from "failed jobs."

    started_at uses COALESCE. A timed-out job by definition started, so
    even if the mark_running UPDATE lost its race, this terminal write
    will surface a started_at value (operators can compute "stuck-for"
    duration as completed_at - started_at without NULL handling).
    """
    if not is_postgres_backend():
        return
    try:
        await _exec_under_lock(
            job_id,
            """
            UPDATE jobs
            SET status = 'timed_out',
                started_at = COALESCE(started_at, NOW()),
                completed_at = NOW(),
                error_message = $2
            WHERE job_id = $1
            """,
            (error_message or "")[:4096],
        )
    except Exception as exc:
        logger.critical(f"[jobs_db] mark_timed_out({job_id}) failed: {exc}")


async def delete_expired(job_id: str) -> None:
    """DELETE FROM jobs after TTL expiry.

    No-op when DB_BACKEND != postgres. Best-effort.

    Cascades to `checkouts` and `audit_events` via FK ON DELETE CASCADE.
    The `sagas` FK is ON DELETE RESTRICT — sagas rows must be deleted
    explicitly first (or aged out via separate retention policy). For
    D-foundation, this is fine because Phase C+ writes the sagas rows
    only when a real /confirm happens, and the job's TTL is much shorter
    than a typical saga lifespan.
    """
    if not is_postgres_backend():
        return
    pool = get_pool()
    try:
        await pool.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
    except asyncpg.ForeignKeyViolationError as exc:
        # A saga row exists for this job_id and didn't reach a terminal
        # state that allows deletion. Defensive log — operator should
        # check why a saga is still referencing an expired job.
        logger.warning(
            f"[jobs_db] delete_expired({job_id}) blocked by FK from sagas: {exc}. "
            "Job artifacts cleaned up but DB row retained until saga resolves."
        )
    except Exception as exc:
        logger.critical(f"[jobs_db] delete_expired({job_id}) failed: {exc}")


# ─── Thread-safe wrappers (fire-and-forget submit to event loop) ─────────────


def mark_running_from_thread(job_id: str) -> None:
    _submit_from_thread(mark_running(job_id))


def mark_complete_from_thread(job_id: str) -> None:
    _submit_from_thread(mark_complete(job_id))


def mark_failed_from_thread(job_id: str, error_message: str) -> None:
    _submit_from_thread(mark_failed(job_id, error_message))


def mark_timed_out_from_thread(job_id: str, error_message: str) -> None:
    _submit_from_thread(mark_timed_out(job_id, error_message))


def delete_expired_from_thread(job_id: str) -> None:
    _submit_from_thread(delete_expired(job_id))
