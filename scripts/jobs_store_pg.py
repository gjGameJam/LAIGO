"""
Postgres backend for the mosaic job lifecycle (Phase D step 2).

This module owns ALL persistent state about jobs (the `jobs` table). It is
the successor to `scripts/jobs_db.py` (Phase D-foundation shadow writes) and
folds in those write helpers as full-fledged lifecycle methods.

Boundary:
- Persistent state (status, progress_pct, timestamps, error_message,
  mosaic_type, width_blocks, etc.) lives in the `jobs` table and is
  accessed ONLY through this module's public API.
- Runtime-only state (the `Future` returned by `executor.submit`, the
  local-disk `.progress` file path, the wallclock `deadline` for the
  timeout watchdog) is NOT part of the persistent contract. It stays in
  per-process side tables on `app.state` (`app.state.futures`,
  `app.state.progress`). `deadline` is computed from `started_at +
  JOB_TIMEOUT_SECONDS` on demand — no separate column needed.

This boundary is what makes the dispatcher pattern (Phase D step 2 step 3)
work: `jobs_store_dispatch.py` can swap between `jobs_store_pg` and
`jobs_store_json` because every function in the public API operates only
on persistent state.

Public API (mirrors `scripts/jobs_store_json.py` exactly):
- insert_queued(...)     — INSERT a new row, status='queued' (raises
                           UniqueViolationError on duplicate job_id, NOT
                           idempotent — see fn docstring for rationale)
- get_job(job_id)        — SELECT one row as a normalized dict (None if absent)
- list_queued()          — SELECT all queued rows, ordered by queued_at
- list_running()         — SELECT all running rows (for active-count + watchdog)
- count_active()         — len(list_running()) but cheaper (single SELECT COUNT)
- dequeue_next()         — atomic SELECT FOR UPDATE SKIP LOCKED + UPDATE→running
                           (replaces queue.Queue + scheduler thread coupling)
- mark_running(job_id)   — explicit UPDATE for paths that don't use dequeue_next
- mark_complete(job_id)
- mark_failed(job_id, error_message)
- mark_timed_out(job_id, error_message)
- write_progress(job_id, pct)
- cleanup_expired()      — DELETE…RETURNING expired terminal rows; returns job_ids
- delete(job_id)         — DELETE one row (used when a queue-intake fails AFTER
                           insert but BEFORE enqueue, so the row doesn't linger)

Sync wrappers (for thread contexts: scheduler, cleanup, executor callback):
- mark_running_from_thread, mark_complete_from_thread, mark_failed_from_thread,
  mark_timed_out_from_thread, write_progress_from_thread, delete_from_thread

Event loop capture (used by `*_from_thread` wrappers via
`asyncio.run_coroutine_threadsafe`):
- set_event_loop(loop), clear_event_loop()

Design contract:
- Every UPDATE goes through `_exec_under_lock(job_id, sql, ...)` —
  pg_advisory_xact_lock(hashtextextended(job_id, 0)) serializes concurrent
  lifecycle writes for the same job_id at the DB level. This is the same
  pattern as `checkout_store_pg.update()` and the inherited B41 fix.
- Terminal writes (mark_complete/failed/timed_out) use
  `started_at = COALESCE(started_at, NOW())` so a fast-failing job whose
  mark_running write lost the lock race still gets a started_at backfilled.
- The INSERT helper lists every column from §9.2.1 explicitly per the B45
  schema-evolution contract (CLAUDE.md "Schema evolution discipline").
  Adding a new NOT-NULL column without a DEFAULT requires updating the
  helper in the same PR as the migration.
- Best-effort error handling REMOVED from this module compared to
  `jobs_db.py`. Phase D-foundation's writes were best-effort because the
  in-memory dict was the source of truth. Now the DB row IS the source of
  truth; a failed write must propagate to the caller so the in-memory
  state doesn't drift. The `*_from_thread` wrappers still swallow
  exceptions at the submit boundary (logged CRITICAL) because thread
  callbacks have nowhere to bubble — but they log loud enough that
  reconciliation (Phase E) catches the drift.

Pattern reuse: the all-columns-explicit INSERT + per-row advisory-lock
UPDATE pattern matches `checkout_store_pg.py` (Phase C). Future tables
should follow the same pattern.
"""

import asyncio
import logging
from typing import Any, Optional

import asyncpg

from .db import get_pool

logger = logging.getLogger("laigo.jobs_store_pg")


# ─── Event loop capture (inherited from jobs_db.py) ──────────────────────────

_loop_ref: Optional[asyncio.AbstractEventLoop] = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the FastAPI event loop for use by `*_from_thread` wrappers.

    Called once during lifespan startup BEFORE any scheduler/cleanup thread
    starts. Idempotent — a second call replaces the reference (correct
    behavior for hot-reload scenarios).
    """
    global _loop_ref
    _loop_ref = loop


def clear_event_loop() -> None:
    """Drop the event-loop reference at lifespan shutdown.

    After this returns, `*_from_thread` calls silently no-op (the
    coroutine is closed). Late-finishing worker threads won't crash trying
    to submit work to a dead loop.
    """
    global _loop_ref
    _loop_ref = None


async def _swallow(coro: Any, name: str, job_id: str) -> Any:
    """Adapt an async-raising coroutine into one that logs CRITICAL on error.

    The async API (`mark_*`, `write_progress`, `delete`) propagates DB
    failures — callers in request-handler context translate to 500. Thread
    callbacks have nowhere to bubble, so this wrapper makes the failure
    visible in logs without crashing the event loop or the calling thread.

    Phase E reconciliation will surface any rows stuck in non-terminal
    states because of swallowed writes.
    """
    try:
        return await coro
    except Exception as exc:
        logger.critical(
            f"[jobs_store_pg] {name}({job_id}) failed from thread context: "
            f"{type(exc).__name__}: {exc}",
            exc_info=True,
        )
        return None


def _submit_from_thread(coro: Any) -> None:
    """Fire-and-forget submit of a coroutine to the FastAPI event loop.

    Safe to call from non-async threads (scheduler, cleanup, executor
    done-callback). If the loop isn't available (pre-startup, shutdown,
    unit tests), the coroutine is closed and a DEBUG line is emitted.

    Type annotation is `Any` rather than `Coroutine` because the standard
    library doesn't expose a usable runtime-checkable Coroutine type on
    `asyncio` (it lives in `typing`/`collections.abc`); `Any` keeps the
    annotation honest without dragging in a generic-parameter type alias
    for a private helper.
    """
    loop = _loop_ref
    if loop is None or not loop.is_running():
        coro.close()
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError as exc:
        logger.debug(f"[jobs_store_pg] thread submit raced loop close: {exc}")
        coro.close()


# ─── Row serialization ───────────────────────────────────────────────────────

# Columns of `jobs` from migrations/sql/0001_initial_schema.up.sql §9.2.1.
# Listed explicitly per the B45 schema-evolution contract — adding a new
# column to the table requires touching this list in the SAME PR as the
# migration. Drift becomes a fail-fast runtime error instead of silent
# data loss.
_JOB_COLS = (
    "job_id",
    "status",
    "mosaic_type",
    "width_blocks",
    "background_pct",
    "dither",
    "upload_filename",
    "progress_pct",
    "error_message",
    "queued_at",
    "started_at",
    "completed_at",
    "ttl_expires_at",
    "customer_email",
    "customer_ip",
    "user_agent",
)


def _row_to_dict(row: Optional[asyncpg.Record]) -> Optional[dict]:
    """Normalize an asyncpg.Record into a dict with consistent shape.

    Returns None when row is None so callers can `if row is None: return None`
    after a single fetchrow. Timestamps remain as `datetime` objects (the
    asyncpg native type); callers that need epoch floats for the legacy
    API contract convert at the boundary. This module returns datetimes
    because that's the authoritative DB type.
    """
    if row is None:
        return None
    return {col: row[col] for col in _JOB_COLS}


# ─── Lifecycle: INSERT (all-columns-explicit per B45) ────────────────────────


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
    """INSERT a new job row at /generate time.

    All 16 columns from §9.2.1 are listed explicitly per the B45 contract
    (see CLAUDE.md "Schema evolution discipline"). Defaults for
    operationally-set columns:
      status='queued', progress_pct=0, error_message=NULL,
      queued_at=NOW(), started_at=NULL, completed_at=NULL.

    `dither=True` until the API exposes a toggle (B51). `customer_*`
    fields default to NULL until Phase E wires audit correlation.

    NOT idempotent — a duplicate `job_id` raises `asyncpg.UniqueViolationError`
    so the caller surfaces the bug rather than silently no-op'ing on top of
    an existing row with the wrong settings. The Phase D-foundation
    `jobs_db.insert_queued` used `ON CONFLICT (job_id) DO NOTHING` because
    it was a best-effort shadow write — the in-memory dict was the source
    of truth. Now the DB row IS the source of truth, so silent swallowing
    of a duplicate is a correctness bug, not a courtesy. `/generate`
    generates `uuid4()` per request; a collision is astronomically rare,
    and if one ever shows up it's a real defect operators should triage,
    not absorb. The request-handler caller propagates UniqueViolationError
    as 500.

    NOT best-effort — raises on DB error. The caller is the request
    handler, which is awaited and can translate to a 500 cleanly. This is
    the behavior change vs Phase D-foundation's `jobs_db.insert_queued`:
    now the row MUST exist before the scheduler picks up the queue item,
    so a silent failure is unacceptable.
    """
    pool = get_pool()
    await pool.execute(
        f"""
        INSERT INTO jobs ({", ".join(_JOB_COLS)}) VALUES (
            $1, 'queued', $2, $3, $4, $5,
            $6, 0, NULL,
            NOW(), NULL, NULL, NOW() + make_interval(secs => $7),
            $8, $9::inet, $10
        )
        """,
        job_id,
        mosaic_type,
        width_blocks,
        int(background_pct) if background_pct is not None else None,
        dither,
        upload_filename,
        ttl_seconds,
        customer_email,
        customer_ip,
        user_agent,
    )


# ─── Lifecycle: UPDATE (all routed through advisory-lock helper) ─────────────


async def _exec_under_lock(job_id: str, sql: str, *args) -> str:
    """Run a single UPDATE inside a per-job_id advisory-lock transaction.

    All `jobs` lifecycle UPDATEs route through here so concurrent writes
    (mark_running, mark_complete, mark_failed, mark_timed_out,
    write_progress) for the same job_id serialize at the DB level.
    Eliminates B41 (mark_complete winning the row-lock race against
    mark_running and leaving started_at NULL).

    Hash function is `hashtextextended($1, 0)` (INT8) — matches the
    pattern in `checkout_store_pg.update()`. Lower collision rate than
    the older `hashtext($1)` INT4 variant.

    Returns the asyncpg command tag string (e.g., "UPDATE 1" or
    "UPDATE 0") so callers can check whether the row matched without an
    additional SELECT.

    See PRE_RELEASE §4 B41.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                job_id,
            )
            return await conn.execute(sql, job_id, *args)


async def mark_running(job_id: str) -> bool:
    """UPDATE jobs SET status='running', started_at=NOW().

    The `AND status = 'queued'` filter prevents a re-fired mark_running
    from downgrading a terminal status (e.g., if mark_complete won the
    lock race). Returns True if the row was updated, False otherwise.

    Used by paths that need explicit "queued → running" transitions
    (today: the JSON-backend scheduler dispatch). The PG-backend
    scheduler should prefer `dequeue_next()`, which combines SELECT FOR
    UPDATE SKIP LOCKED + the status flip in a single transaction (safer
    for multi-worker).
    """
    tag = await _exec_under_lock(
        job_id,
        """
        UPDATE jobs
        SET status = 'running', started_at = NOW()
        WHERE job_id = $1 AND status = 'queued'
        """,
    )
    return tag.endswith(" 1")


async def mark_complete(job_id: str) -> None:
    """UPDATE jobs SET status='complete', completed_at=NOW(), progress_pct=100.

    `started_at = COALESCE(started_at, NOW())` backfills the timestamp if
    the prior mark_running write lost the lock race (the executor callback
    can fire before mark_running's first await flushes, common for very
    fast jobs). Operator dashboards always have a non-NULL started_at to
    compute job duration from.
    """
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


async def mark_failed(job_id: str, error_message: str) -> None:
    """UPDATE jobs SET status='failed', completed_at=NOW(), error_message=$.

    The `error_message` is the same internal string written to
    `manifest_failed.json` — operator-facing, may contain stack-trace
    fragments. Truncated to 4 KiB to keep the column reasonable.

    `started_at = COALESCE(started_at, NOW())` for the same race rationale
    as `mark_complete`.
    """
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


async def mark_timed_out(job_id: str, error_message: str) -> None:
    """UPDATE jobs SET status='timed_out', completed_at=NOW(), error_message=$.

    Distinct terminal status from 'failed' so operators can triage stuck
    jobs separately from clean failures. A timed-out job by definition
    started, so `started_at` will almost always be set; COALESCE handles
    the race-loser edge case.
    """
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


async def write_progress(job_id: str, pct: float) -> None:
    """UPDATE jobs SET progress_pct=$ for a running job.

    Clamps to [0, 100] and casts to int (the column is SMALLINT 0-100,
    CHECKed). The scheduler tick mirrors the worker's `.progress` file
    here on every iteration; the file remains the in-process source of
    truth (the worker subprocess writes to it without a DB connection per
    §9.3.11.10).

    Guarded by `WHERE status='running'` so progress updates that arrive
    after a terminal transition (rare race) don't resurrect a completed
    row's progress_pct.
    """
    clamped = max(0, min(100, int(pct)))
    await _exec_under_lock(
        job_id,
        """
        UPDATE jobs
        SET progress_pct = $2
        WHERE job_id = $1 AND status = 'running'
        """,
        clamped,
    )


# ─── Lifecycle: DELETE ───────────────────────────────────────────────────────


async def delete(job_id: str) -> None:
    """DELETE one job row (used by queue-intake-failure rollback).

    If /generate calls `insert_queued` successfully but then `put_nowait`
    raises `Queue.Full`, the row would linger at 'queued' with no
    scheduler ever picking it up. The intake-failure path calls this to
    remove the orphan.

    Cascades to `checkouts` and `audit_events` via FK ON DELETE CASCADE.
    Blocked by the `sagas` FK (ON DELETE RESTRICT) if a saga exists —
    impossible for a row that never left 'queued', but defensive. Raises
    `asyncpg.ForeignKeyViolationError` in that case; the caller logs and
    accepts the orphan row (Phase E reconciliation will clean up).
    """
    pool = get_pool()
    await pool.execute("DELETE FROM jobs WHERE job_id = $1", job_id)


async def cleanup_expired() -> list[str]:
    """DELETE jobs whose TTL has passed AND are in a terminal status.

    Returns the list of successfully-deleted job_ids so the caller can
    `shutil.rmtree` each `outputs/{job_id}` directory OUTSIDE the
    transaction. Doing the filesystem cleanup inside the transaction
    would hold the row lock across slow disk I/O.

    Per-job deletion (NOT bulk DELETE … RETURNING) — the `sagas.job_id`
    FK is ON DELETE RESTRICT, so any expired job that still has a
    non-terminal saga referencing it would fail the FK check. A bulk
    DELETE fails the entire batch on a single FK conflict, blocking
    cleanup of every other eligible job for that tick. The Phase
    D-foundation `jobs_db.delete_expired` had the same handling per-job;
    we preserve it here.

    Realistic scenario for FK conflict: customer hits /confirm right
    before the job's TTL expires; saga is in-flight (non-terminal) when
    cleanup runs. The job stays in the DB until the saga reaches a
    terminal status; the artifact dir is also retained (this function
    doesn't return its job_id, so the caller doesn't rmtree it). Phase
    E reconciliation will surface persistently-orphaned cases.

    `WHERE status IN (...) AND ttl_expires_at < NOW()` matches the
    partial index `jobs_ttl_idx` so the SELECT scan is cheap. Per-row
    DELETEs are individually fast and the per-tick batch size is
    typically a handful of jobs.
    """
    pool = get_pool()
    candidate_rows = await pool.fetch(
        """
        SELECT job_id FROM jobs
        WHERE status IN ('complete', 'failed', 'timed_out')
          AND ttl_expires_at < NOW()
        """
    )
    deleted: list[str] = []
    for row in candidate_rows:
        jid = row["job_id"]
        try:
            await pool.execute("DELETE FROM jobs WHERE job_id = $1", jid)
            deleted.append(jid)
        except asyncpg.ForeignKeyViolationError as exc:
            # A saga in a non-terminal status still references this job_id.
            # Skip — Phase E reconciliation handles persistent orphans.
            logger.warning(
                f"[jobs_store_pg] cleanup_expired skipped {jid} "
                f"(FK from sagas): {exc}. Row retained until saga resolves."
            )
        except Exception as exc:
            # Transient DB error on a single row shouldn't block the rest.
            logger.error(
                f"[jobs_store_pg] cleanup_expired failed for {jid}: "
                f"{type(exc).__name__}: {exc}"
            )
    return deleted


# ─── Lifecycle: SELECT ───────────────────────────────────────────────────────


async def get_job(job_id: str) -> Optional[dict]:
    """SELECT a single job by id. Returns None if absent.

    Result keys match `_JOB_COLS` exactly (every column from §9.2.1).
    Timestamps are `datetime` objects; callers that need epoch floats
    convert at the boundary.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        f"SELECT {', '.join(_JOB_COLS)} FROM jobs WHERE job_id = $1",
        job_id,
    )
    return _row_to_dict(row)


async def list_queued() -> list[dict]:
    """SELECT all queued jobs, oldest first.

    Used by the scheduler / `/queue` UI endpoint. Result rows have the
    full `_JOB_COLS` shape. The list is unbounded today (real queues stay
    small because `MAX_QUEUE_SIZE=20`); add LIMIT if/when that changes.
    """
    pool = get_pool()
    rows = await pool.fetch(
        f"""
        SELECT {", ".join(_JOB_COLS)}
        FROM jobs
        WHERE status = 'queued'
        ORDER BY queued_at
        """
    )
    return [_row_to_dict(row) for row in rows]


async def list_running() -> list[dict]:
    """SELECT all running jobs.

    Used by:
      (a) the timeout watchdog (`_check_timed_out_jobs`) — needs
          `started_at` to compute the deadline.
      (b) `count_active()` callers that also need row data.

    For pure count, `count_active()` is cheaper (single SELECT COUNT).
    """
    pool = get_pool()
    rows = await pool.fetch(
        f"""
        SELECT {", ".join(_JOB_COLS)}
        FROM jobs
        WHERE status = 'running'
        """
    )
    return [_row_to_dict(row) for row in rows]


async def count_active() -> int:
    """Return the number of running jobs.

    Replaces `app.state.active_jobs`. The scheduler reads this to enforce
    `MAX_WORKERS`. Single SELECT COUNT — cheaper than list_running for
    the common case. COUNT(*) is guaranteed non-NULL and returns a Python
    int via asyncpg's BIGINT codec, so no defensive `or 0` is needed.
    """
    pool = get_pool()
    return await pool.fetchval(
        "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
    )


# ─── Lifecycle: atomic dequeue (FOR UPDATE SKIP LOCKED) ──────────────────────


async def dequeue_next() -> Optional[dict]:
    """Atomically claim the oldest queued job and flip it to 'running'.

    `FOR UPDATE SKIP LOCKED` is the multi-worker-safe pattern: if worker
    A grabbed the row, worker B's query skips it without blocking. With
    `MAX_WORKERS=1` today the SKIP LOCKED part is overkill but free, and
    it future-proofs the path for the optional `MAX_WORKERS>1` upgrade
    documented in §9.5.D exit criteria.

    Returns the claimed row as a dict (same shape as `get_job`), or None
    if no queued jobs exist. Caller is responsible for submitting the
    work to the executor outside the transaction (asyncpg holds the row
    lock until the surrounding transaction commits, which happens when
    this coroutine returns).

    Note on serialization: the SELECT acquires a row-level lock; we do
    NOT add a `pg_advisory_xact_lock` here because that would serialize
    ALL queue draining, defeating SKIP LOCKED. Per-row UPDATEs from other
    sites (mark_complete, etc.) still take the advisory lock for the
    *individual* job_id — different job_ids do not collide.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                SELECT {", ".join(_JOB_COLS)}
                FROM jobs
                WHERE status = 'queued'
                ORDER BY queued_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            if row is None:
                return None
            # UPDATE … RETURNING gets the DB's actual NOW() for started_at
            # rather than a Python wallclock that diverges by milliseconds.
            # Without this, callers comparing the returned dict's started_at
            # against a later DB read of the same column would see drift.
            new_started_at = await conn.fetchval(
                """
                UPDATE jobs
                SET status = 'running', started_at = NOW()
                WHERE job_id = $1
                RETURNING started_at
                """,
                row["job_id"],
            )
            result = _row_to_dict(row)
            assert result is not None
            result["status"] = "running"
            result["started_at"] = new_started_at
            return result


# ─── Sync wrappers (thread → event loop) ─────────────────────────────────────


def mark_running_from_thread(job_id: str) -> None:
    _submit_from_thread(_swallow(mark_running(job_id), "mark_running", job_id))


def mark_complete_from_thread(job_id: str) -> None:
    _submit_from_thread(_swallow(mark_complete(job_id), "mark_complete", job_id))


def mark_failed_from_thread(job_id: str, error_message: str) -> None:
    _submit_from_thread(
        _swallow(mark_failed(job_id, error_message), "mark_failed", job_id)
    )


def mark_timed_out_from_thread(job_id: str, error_message: str) -> None:
    _submit_from_thread(
        _swallow(mark_timed_out(job_id, error_message), "mark_timed_out", job_id)
    )


def write_progress_from_thread(job_id: str, pct: float) -> None:
    _submit_from_thread(
        _swallow(write_progress(job_id, pct), "write_progress", job_id)
    )


def delete_from_thread(job_id: str) -> None:
    _submit_from_thread(_swallow(delete(job_id), "delete", job_id))
