"""
Backend dispatcher for `checkout_store`.

Routes load/save/update/read_order_list calls to the JSON or Postgres
backend based on the `DB_BACKEND` env var, re-checked per call.

Why runtime check (not import-time):
    The original §9.5.C draft used import-time dispatch (`from .checkout_store_pg
    import ...` inside an `if` block at module load). That locks the backend
    for the process lifetime and breaks pytest scenarios that need to switch
    backends without restarting the Python process — which conflicts with the
    `_reset_for_tests()` convention already used in `payment.registry` and
    `db.py`. The per-call env lookup costs microseconds (os.environ.get is
    a dict access) and is negligible compared to the actual I/O work.

    In production, `DB_BACKEND` is read once at deploy time and never
    changes, so the runtime check is effectively a constant branch.

Re-export semantics:
    `ActiveCheckoutExistsError` is re-exported here so callers (router,
    saga) can `from .checkout_store_dispatch import ActiveCheckoutExistsError`
    without knowing which backend is active. Same exception object regardless
    of backend — the Postgres backend raises it; the JSON backend doesn't
    (B23 is an open defect for JSON-only mode, see CHECKOUT_AUDIT § B23).

Filesystem artifacts:
    `read_order_list` is always filesystem-backed — `order_list.json` is an
    artifact written by the mosaic pipeline, not state. Both backends
    re-export the same implementation from `checkout_store.py`.
"""

import os
from typing import Optional

# Import the exception type unconditionally — both backends raise / accept it.
from .checkout_store import ActiveCheckoutExistsError, read_order_list  # noqa: F401

__all__ = ("load", "save", "update", "read_order_list", "ActiveCheckoutExistsError")


def _is_postgres() -> bool:
    """True iff DB_BACKEND env var requests the Postgres backend.

    Default is "json" — matches `scripts/db.py:is_postgres_backend()`.
    Two helpers exist (not deduplicated) because this module lives under
    `scripts/checkout/` and `db.py` lives at `scripts/` — importing across
    that boundary at module load creates a circular import risk via the
    `scripts/checkout/payment` packages. The cost is one tiny duplicate
    function; the benefit is no import-time coupling.
    """
    return os.environ.get("DB_BACKEND", "json").lower() == "postgres"


async def load(job_id: str) -> Optional[dict]:
    if _is_postgres():
        from .checkout_store_pg import load as _impl
    else:
        from .checkout_store import load as _impl
    return await _impl(job_id)


async def save(job_id: str, state: dict) -> None:
    if _is_postgres():
        from .checkout_store_pg import save as _impl
    else:
        from .checkout_store import save as _impl
    return await _impl(job_id, state)


async def update(job_id: str, partial: dict) -> dict:
    if _is_postgres():
        from .checkout_store_pg import update as _impl
    else:
        from .checkout_store import update as _impl
    return await _impl(job_id, partial)
