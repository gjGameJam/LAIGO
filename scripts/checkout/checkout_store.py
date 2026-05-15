"""
Disk-backed checkout state per job.

State file: outputs/{job_id}/checkout_state.json
One asyncio.Lock per job_id prevents concurrent writes from the Saga coroutine
and the status-polling endpoint.

The order_list.json is read directly from the job's workspace. The multi-file
split (order_list_1.json etc.) existed only for LEGO.com's 999-unit upload cap
and is irrelevant to the optimizer, which always reads the single canonical file.
"""

import json
import asyncio
import os
from pathlib import Path
from typing import Optional

_locks: dict[str, asyncio.Lock] = {}


def _output_dir() -> Path:
    """Resolve OUTPUT_DIR the same way Main.py does, so paths always agree."""
    return Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()


def _state_path(job_id: str) -> Path:
    return _output_dir() / job_id / "checkout_state.json"


def _get_lock(job_id: str) -> asyncio.Lock:
    if job_id not in _locks:
        _locks[job_id] = asyncio.Lock()
    return _locks[job_id]


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
