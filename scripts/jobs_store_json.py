"""
In-memory backend for the mosaic job lifecycle (Phase D step 2).

This module is the legacy-path counterpart to `scripts/jobs_store_pg.py`.
It owns ALL persistent state about jobs (for the JSON-mode runtime)
through an internal process-local `_jobs` dict that mirrors the shape of
the Postgres `jobs` table row-for-row.

The dispatcher (`scripts/jobs_store_dispatch.py`) routes calls to this
module when `DB_BACKEND != 'postgres'`, and to `jobs_store_pg` when
`DB_BACKEND == 'postgres'`. Both modules expose the same public API and
return dicts shaped identically — callers don't need to know which
backend is active.

Why not just keep `app.state.jobs`?
- `app.state.jobs` historically mixed PERSISTENT state (status, error,
  timestamps, settings) with RUNTIME-ONLY refs (the `Future` returned by
  `executor.submit`, the local-disk `.progress` path, the wallclock
  `deadline` for the timeout watchdog). The Phase D step 2 design splits
  these: persistent state moves here (or to PG); runtime-only refs stay
  on `app.state` (`app.state.futures`, `app.state.progress`).
- Centralizing persistent state behind a module-level API makes the
  dispatcher swap possible (Step 3) and removes the need for Main.py to
  read/write `app.state.jobs` directly (Step 4).

Storage shape
- `_jobs: dict[str, dict]` — keyed by job_id; each value is a dict whose
  keys are EXACTLY the columns of the `jobs` table per §9.2.1
  (`_JOB_COLS`). No extra keys; no missing keys for known columns
  (defaults to None for nullables).
- Timestamps are timezone-aware `datetime` objects (UTC). The Postgres
  backend returns the same type via asyncpg's native datetime codec.
  Callers that need epoch floats for legacy HTTP-response contracts
  convert at the response boundary in Step 4.
- Status enum matches the §9.2.1 CHECK constraint:
  'queued' | 'running' | 'complete' | 'failed' | 'timed_out'.
  Note that today's GET /jobs/{id} response collapses 'timed_out' →
  'failed' for frontend compatibility; Step 4 keeps that normalization
  at the route boundary.

Concurrency
- `_lock: threading.Lock` guards every read and write. The lock is
  defensive against the partial-migration window where some Main.py
  callers may still touch state from non-event-loop threads before Step
  4 fully wires the dispatcher. The critical sections are dict reads /
  writes (microseconds); acquiring from async context costs nothing
  meaningful.
- The `*_from_thread` wrappers submit coroutines to the FastAPI event
  loop via `asyncio.run_coroutine_threadsafe`, matching the PG backend
  exactly so the dispatcher swap is fully transparent. They could
  technically run synchronously (the JSON backend has no I/O), but
  matching the API contract is worth more than the micro-optimization.

Failure modes
- The JSON backend has no I/O failures (in-memory dict mutation cannot
  fail except on OOM, which is unrecoverable). The async functions
  therefore never raise from "normal" operations. They DO raise
  `KeyError` for `get_job(unknown_id)` paths via the underlying dict,
  but the API contract returns None instead (matching PG's
  fetchrow→None semantics).

Public API (mirrors jobs_store_pg.py EXACTLY):
  Event loop: set_event_loop, clear_event_loop
  Lifecycle:  insert_queued, get_job, list_queued, list_running,
              count_active, count_queued, dequeue_next, mark_running,
              mark_complete, mark_failed, mark_timed_out, write_progress,
              delete, cleanup_expired
  Thread:     mark_running_from_thread, mark_complete_from_thread,
              mark_failed_from_thread, mark_timed_out_from_thread,
              write_progress_from_thread, delete_from_thread
  Test:       _reset_for_tests
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("laigo.jobs_store_json")


# ─── Event loop capture (matches jobs_store_pg.py contract) ──────────────────

_loop_ref: Optional[asyncio.AbstractEventLoop] = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the FastAPI event loop for use by `*_from_thread` wrappers."""
    global _loop_ref
    _loop_ref = loop


def clear_event_loop() -> None:
    """Drop the event-loop reference at lifespan shutdown."""
    global _loop_ref
    _loop_ref = None


async def _swallow(coro: Any, name: str, job_id: str) -> Any:
    """Logs CRITICAL on error and returns None.

    The JSON backend's mutations themselves cannot fail, but a future
    schema-evolution mistake (e.g., a caller passing an unexpected key)
    could raise. Matching the PG backend's swallow contract keeps the
    dispatcher fully transparent.
    """
    try:
        return await coro
    except Exception as exc:
        logger.critical(
            f"[jobs_store_json] {name}({job_id}) failed from thread context: "
            f"{type(exc).__name__}: {exc}",
            exc_info=True,
        )
        return None


def _submit_from_thread(coro: Any) -> None:
    """Fire-and-forget submit of a coroutine to the FastAPI event loop."""
    loop = _loop_ref
    if loop is None or not loop.is_running():
        coro.close()
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError as exc:
        logger.debug(f"[jobs_store_json] thread submit raced loop close: {exc}")
        coro.close()


# ─── Storage ─────────────────────────────────────────────────────────────────

# Columns of the `jobs` table per §9.2.1. MUST stay in sync with
# `jobs_store_pg._JOB_COLS` — any schema change touches both files.
# (Centralizing this constant in a third module would be cleaner long-term
# but would create an import that flows JSON→PG and adds coupling; the
# duplication is tiny and the parity is verified by `_reset_for_tests`
# imports + visual review during schema-change PRs.)
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

_TERMINAL_STATUSES = frozenset({"complete", "failed", "timed_out"})

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _now_utc() -> datetime:
    """UTC-aware `datetime` matching the PG backend's NOW() return type."""
    return datetime.now(timezone.utc)


def _make_row(
    *,
    job_id: str,
    mosaic_type: str,
    width_blocks: int,
    background_pct: Optional[float],
    ttl_seconds: int,
    dither: bool,
    upload_filename: Optional[str],
    customer_email: Optional[str],
    customer_ip: Optional[str],
    user_agent: Optional[str],
) -> dict:
    """Build a fresh row dict shaped EXACTLY like `_JOB_COLS`.

    All columns present (no missing keys); operationally-defaulted columns
    get the same defaults the PG backend's INSERT statement uses
    (queued_at=NOW(), progress_pct=0, started_at/completed_at/error_message
    None). This is the JSON backend's analogue to PG's "all-columns-
    explicit INSERT" — same B45 contract: adding a new column to `jobs`
    requires touching this builder AND `_JOB_COLS`.

    background_pct is normalized to int (matching the SMALLINT column on
    the PG side); None passes through unchanged.
    """
    now = _now_utc()
    return {
        "job_id":           job_id,
        "status":           "queued",
        "mosaic_type":      mosaic_type,
        "width_blocks":     width_blocks,
        "background_pct":   int(background_pct) if background_pct is not None else None,
        "dither":           dither,
        "upload_filename":  upload_filename,
        "progress_pct":     0,
        "error_message":    None,
        "queued_at":        now,
        "started_at":       None,
        "completed_at":     None,
        "ttl_expires_at":   now + timedelta(seconds=ttl_seconds),
        "customer_email":   customer_email,
        "customer_ip":      customer_ip,
        "user_agent":       user_agent,
    }


def _copy_row(row: Optional[dict]) -> Optional[dict]:
    """Return a shallow copy so callers can mutate without affecting storage.

    Mirrors the PG backend, which returns fresh dicts on every fetchrow.
    Without copying, a caller doing `row = await get_job(jid); row["x"] = ...`
    would mutate the in-memory storage directly — a subtle aliasing bug
    that would NOT manifest in the PG path.
    """
    return None if row is None else dict(row)


# ─── Lifecycle: INSERT ───────────────────────────────────────────────────────


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
    """Create a new in-memory job row, status='queued'.

    NOT idempotent — a duplicate `job_id` raises `ValueError` (matching
    the PG backend's `UniqueViolationError` semantics). `/generate`
    generates uuid4 per request; a collision is a real bug to surface,
    not absorb. See jobs_store_pg.insert_queued for the full rationale.
    """
    row = _make_row(
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
    with _lock:
        if job_id in _jobs:
            raise ValueError(
                f"insert_queued: job_id {job_id!r} already exists "
                "(call delete() first if you intended to replace it)"
            )
        _jobs[job_id] = row


# ─── Lifecycle: UPDATE ───────────────────────────────────────────────────────


async def mark_running(job_id: str) -> bool:
    """Transition queued → running, set started_at = NOW().

    Matches PG's `WHERE status = 'queued'` guard: a re-fired mark_running
    against an already-terminal row is a no-op (returns False). Returns
    True if the row was actually transitioned.
    """
    now = _now_utc()
    with _lock:
        row = _jobs.get(job_id)
        if row is None or row["status"] != "queued":
            return False
        row["status"] = "running"
        row["started_at"] = now
        return True


def _backfill_started_at(row: dict, now: datetime) -> None:
    """COALESCE(started_at, NOW()) — matches PG's terminal-write contract.

    Phase D-foundation B41 fix: terminal writes (mark_complete/failed/
    timed_out) must backfill started_at if the prior mark_running write
    lost the lock race (the executor callback can fire before
    mark_running's first await flushes, common for very fast jobs).
    Operator dashboards always have a non-NULL started_at to compute
    duration from.
    """
    if row.get("started_at") is None:
        row["started_at"] = now


async def mark_complete(job_id: str) -> None:
    """Transition any → complete; backfill started_at, set completed_at."""
    now = _now_utc()
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        row["status"] = "complete"
        _backfill_started_at(row, now)
        row["completed_at"] = now
        row["progress_pct"] = 100
        row["error_message"] = None


async def mark_failed(job_id: str, error_message: str) -> None:
    """Transition any → failed; backfill started_at, set completed_at + error.

    error_message truncated to 4 KiB to match PG's column-budget contract.
    """
    now = _now_utc()
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        row["status"] = "failed"
        _backfill_started_at(row, now)
        row["completed_at"] = now
        row["error_message"] = (error_message or "")[:4096]


async def mark_timed_out(job_id: str, error_message: str) -> None:
    """Transition any → timed_out; backfill started_at, set completed_at.

    DISTINCT terminal status from 'failed' — operators triage stuck jobs
    separately from clean failures. HTTP responses normalize 'timed_out'
    → 'failed' at the route boundary in Step 4 for frontend compatibility
    (the existing API contract returns 'failed' for both).
    """
    now = _now_utc()
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        row["status"] = "timed_out"
        _backfill_started_at(row, now)
        row["completed_at"] = now
        row["error_message"] = (error_message or "")[:4096]


async def write_progress(job_id: str, pct: float) -> None:
    """Update progress_pct for a running job. Clamps to [0, 100].

    `status == 'running'` guard prevents resurrection of a terminal row's
    progress field (matches PG's `WHERE status='running'`).
    """
    clamped = max(0, min(100, int(pct)))
    with _lock:
        row = _jobs.get(job_id)
        if row is None or row["status"] != "running":
            return
        row["progress_pct"] = clamped


# ─── Lifecycle: DELETE ───────────────────────────────────────────────────────


async def delete(job_id: str) -> None:
    """Remove a single job row.

    Idempotent: deleting a non-existent job_id is a no-op. Matches PG's
    DELETE semantics (DELETE matches 0 rows = no error). The PG version's
    `ForeignKeyViolationError` doesn't apply here (JSON backend has no
    FK enforcement); checkout state lives in checkout_store.* which is
    backend-decoupled from this module.
    """
    with _lock:
        _jobs.pop(job_id, None)


async def cleanup_expired() -> list[str]:
    """Remove all expired terminal jobs; return their job_ids.

    Matches PG: `status IN (terminal_set) AND ttl_expires_at < NOW()`.
    Returns the deleted job_ids so the caller can `shutil.rmtree` each
    output dir OUTSIDE the lock. (The lock is brief in this backend, but
    rmtree is slow filesystem I/O — keeping it out of the critical
    section matches the PG pattern where it would hold the row lock.)

    Note: the PG backend's per-job FK-violation handling does NOT apply
    here — the JSON backend doesn't enforce sagas FK. The Postgres path
    is the one where this distinction matters; here we delete cleanly.
    """
    now = _now_utc()
    to_delete: list[str] = []
    with _lock:
        for jid, row in _jobs.items():
            if row["status"] in _TERMINAL_STATUSES and row["ttl_expires_at"] < now:
                to_delete.append(jid)
        for jid in to_delete:
            del _jobs[jid]
    return to_delete


# ─── Lifecycle: SELECT ───────────────────────────────────────────────────────


async def get_job(job_id: str) -> Optional[dict]:
    """Return a copy of the row for job_id, or None if absent."""
    with _lock:
        return _copy_row(_jobs.get(job_id))


async def list_queued() -> list[dict]:
    """Return copies of all queued rows, oldest queued_at first."""
    with _lock:
        rows = [
            _copy_row(r) for r in _jobs.values() if r["status"] == "queued"
        ]
    rows.sort(key=lambda r: r["queued_at"])
    return rows


async def list_running() -> list[dict]:
    """Return copies of all running rows. Order unspecified."""
    with _lock:
        return [
            _copy_row(r) for r in _jobs.values() if r["status"] == "running"
        ]


async def count_active() -> int:
    """Return the number of running jobs. Replaces app.state.active_jobs."""
    with _lock:
        return sum(1 for r in _jobs.values() if r["status"] == "running")


async def count_queued() -> int:
    """Return the number of queued jobs. Used by the queue-full check."""
    with _lock:
        return sum(1 for r in _jobs.values() if r["status"] == "queued")


# ─── Lifecycle: atomic dequeue (mirrors PG's FOR UPDATE SKIP LOCKED) ─────────


async def dequeue_next() -> Optional[dict]:
    """Atomically claim the oldest queued job and flip it to 'running'.

    Single critical section under `_lock` → analogous to the PG backend's
    single-transaction SELECT FOR UPDATE SKIP LOCKED + UPDATE. Returns
    a copy of the row with status='running' and started_at set, OR None
    if no queued jobs exist.

    The caller is responsible for submitting the work to the executor
    after this returns (the row state is already committed to the store
    by the time the lock releases).
    """
    now = _now_utc()
    with _lock:
        # Inline scan rather than calling list_queued + mark_running so
        # the whole find-and-flip happens under one lock acquisition
        # (matches PG's single-transaction semantics).
        oldest_jid: Optional[str] = None
        oldest_queued_at: Optional[datetime] = None
        for jid, row in _jobs.items():
            if row["status"] != "queued":
                continue
            if oldest_queued_at is None or row["queued_at"] < oldest_queued_at:
                oldest_jid = jid
                oldest_queued_at = row["queued_at"]
        if oldest_jid is None:
            return None
        row = _jobs[oldest_jid]
        row["status"] = "running"
        row["started_at"] = now
        return _copy_row(row)


# ─── Sync wrappers (thread → event loop), match PG exactly ───────────────────


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


# ─── Test helpers ────────────────────────────────────────────────────────────


def _reset_for_tests() -> None:
    """Drop in-memory state. Matches `db._reset_for_tests()` pattern.

    Safe to call from production shutdown too — it's idempotent and
    side-effect-free beyond clearing the dict.
    """
    with _lock:
        _jobs.clear()
    clear_event_loop()
