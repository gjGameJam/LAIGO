"""
In-process TTL cache for listings, quotes, and API responses.

Async-safe: FastAPI runs on a single event loop with cooperative scheduling.
A get + set in the same coroutine frame are effectively atomic — no locks needed.
Two concurrent cache misses may both fetch and both write; last-write-wins, which
is fine since both fetches return equivalent fresh data.

Cache key conventions:
  lego_raw:{element_id}               -> dict  (raw LEGO.com search result)
  boid:{element_id}                   -> str | None  (BrickOwl BOID)
  brickowl_listings:{element_id}      -> list[SellerListing]
  quote:{checkout_id}                 -> dict  (serialized allocation + metadata)
"""

import time
import asyncio
from typing import Any, Optional

_store: dict[str, tuple[float, Any]] = {}  # key -> (expiry_timestamp, value)

# B24: strong reference to the sweep task so the event loop doesn't GC it.
# Python 3.11+ docs: "the event loop only keeps weak references to tasks. A
# task that isn't referenced elsewhere may be garbage collected at any time,
# even before it's done." Same defect class as the C1 fix that introduced
# `_running_sagas` in router.py. Idempotency check prevents double-start.
_sweeper_task: Optional[asyncio.Task] = None


async def cache_get(key: str) -> Optional[Any]:
    entry = _store.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if time.time() > expiry:
        _store.pop(key, None)
        return None
    return value


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    _store[key] = (time.time() + ttl_seconds, value)


async def cache_delete(key: str) -> None:
    """Force-invalidate an entry (used during stockout retries)."""
    _store.pop(key, None)


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(300)
        now = time.time()
        expired = [k for k, (exp, _) in list(_store.items()) if exp < now]
        for k in expired:
            _store.pop(k, None)


def start_cache_sweeper() -> None:
    """Schedule the background sweep task. Call once from Main.py lifespan.

    Idempotent — calling twice (or after a previous task finished) re-schedules
    safely. The module-level `_sweeper_task` strong reference is what keeps the
    event loop from GC'ing the task (B24).
    """
    global _sweeper_task
    if _sweeper_task is not None and not _sweeper_task.done():
        return  # already running
    _sweeper_task = asyncio.create_task(_sweep_loop())
