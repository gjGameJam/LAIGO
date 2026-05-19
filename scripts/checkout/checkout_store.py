"""
Disk-backed checkout state per job.

State file: outputs/{job_id}/checkout_state.json
One asyncio.Lock per job_id prevents concurrent writes from the Saga coroutine
and the status-polling endpoint.

The order_list.json is read directly from the job's workspace. The multi-file
split (order_list_1.json etc.) existed only for LEGO.com's 999-unit upload cap
and is irrelevant to the optimizer, which always reads the single canonical file.

DISPATCHER NOTE (Phase C): Callers should import from
`checkout_store_dispatch` rather than directly from this module, so the
DB_BACKEND env switch routes between the JSON path (this file) and the
Postgres path (`checkout_store_pg.py`). The exception type
`ActiveCheckoutExistsError` is defined here so both backends can raise it
without creating a circular import.
"""

import json
import asyncio
import os
from pathlib import Path
from typing import Optional

_locks: dict[str, asyncio.Lock] = {}


class ActiveCheckoutExistsError(Exception):
    """A non-terminal saga already exists for this job_id (B23 enforcement).

    Raised by the Postgres backend's `save()` when the partial unique index
    `sagas_one_active_per_job_idx` fires on INSERT. The router catches this
    and returns 422 with `code="ACTIVE_CHECKOUT_EXISTS"`.

    The JSON backend doesn't raise this — B23 is an open defect for the
    JSON-only mode (the application-level check in router.py:217 covers
    the SAME-checkout_id case but not the DIFFERENT-checkout_id case).
    Postgres mode closes that gap structurally via the partial unique index.

    The `job_id` attribute is populated so callers can include it in
    response bodies / logs without re-deriving it.
    """

    def __init__(self, job_id: str, message: Optional[str] = None) -> None:
        self.job_id = job_id
        super().__init__(
            message
            or f"An active (non-terminal) checkout already exists for job_id={job_id!r}"
        )


def _output_dir() -> Path:
    """Resolve OUTPUT_DIR the same way Main.py does, so paths always agree."""
    return Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()


def _state_path(job_id: str) -> Path:
    return _output_dir() / job_id / "checkout_state.json"


def _get_lock(job_id: str) -> asyncio.Lock:
    # B1: dict.setdefault is atomic under the GIL (single C-level op), making
    # the get-or-create explicit. The previous `if not in / assign` pattern
    # is atomic under pure single-threaded asyncio because there is no `await`
    # between the check and the write, but the pattern reads like a TOCTOU
    # race and would become a real bug if this module is ever called from a
    # thread pool or multi-process worker pool. setdefault costs one extra
    # Lock() allocation when the key already exists (immediately GC'd); for a
    # per-job_id dict that allocation overhead is negligible.
    return _locks.setdefault(job_id, asyncio.Lock())


async def load(job_id: str) -> Optional[dict]:
    path = _state_path(job_id)
    if not path.exists():
        return None
    async with _get_lock(job_id):
        return json.loads(path.read_text(encoding="utf-8"))


async def save(job_id: str, state: dict) -> None:
    path = _state_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with _get_lock(job_id):
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")


async def update(job_id: str, partial: dict) -> dict:
    """Load existing state, merge partial, save, return merged state."""
    path = _state_path(job_id)
    async with _get_lock(job_id):
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        current.update(partial)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return current


def read_order_list(job_id: str) -> list[dict]:
    """
    Read the canonical order list for a completed job.
    Returns list of {elementId: str, quantity: int}.

    Main.py copies order_list.json to outputs/{job_id}/order_list.json before
    deleting the workspace, so this stable path is valid for completed jobs.

    Raises FileNotFoundError if the job hasn't completed yet.
    """
    path = _output_dir() / job_id / "order_list.json"
    if not path.exists():
        raise FileNotFoundError(
            f"order_list.json not found for job '{job_id}'. "
            "Ensure the job has completed successfully before calling /quote."
        )
    return json.loads(path.read_text(encoding="utf-8"))
