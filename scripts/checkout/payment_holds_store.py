"""payment_holds table writes — the reconciliation index for Stripe holds.

`payment_holds` is the side-table that lets `reconcile_orphan_holds` (Phase E
step 2) find holds that diverged from saga state. It is independent of the
saga's main state (`sagas` table): a hold can outlive its saga in scenarios
where the customer-facing saga state is purged for retention but the hold
record needs to survive the chargeback window (~180 days).

Schema (`scripts/migrations/sql/0001_initial_schema.up.sql`):
    hold_id PRIMARY KEY
    checkout_id FK → checkouts (ON DELETE RESTRICT)
    provider, mode
    amount_authorized_cents, currency
    last_known_status ('requires_capture'|'succeeded'|'canceled'|'unknown')
    last_reconciled_at, created_at

Public API:
    record_hold(checkout_id, hold)  — INSERT after provider.create_hold succeeds
    mark_status(hold_id, status)    — UPDATE last_known_status + reconciled_at
    fetch_for_reconcile()           — SELECT candidates for reconcile_orphan_holds

All writes are NO-OPs when `DB_BACKEND != postgres`. The JSON-mode runtime
has no separate hold index; the saga's checkout_state.json carries the
hold id, and reconciliation simply isn't possible without persistent state.

Idempotency contracts
=====================
- `record_hold` uses `ON CONFLICT (hold_id) DO NOTHING`. Stripe's idempotency
  key (`hold-{checkout_id}`) means a resumed saga after restart could
  invoke create_hold again and receive the same hold_id back; the second
  INSERT is a no-op. This preserves the original `created_at` for chargeback
  audit purposes. Recovery paths that need to know "did we record this?"
  should query `fetch` first.
- `mark_status` clobbers `last_known_status` with the new value and bumps
  `last_reconciled_at = NOW()`. Status transitions in practice are
  monotonic (requires_capture → succeeded|canceled); re-writes are safe.
  The reconciliation scan filters by `last_known_status='requires_capture'`,
  so once we mark `succeeded` or `canceled`, the row is no longer eligible
  for reconciliation (correct).

Why a separate module (not on `checkout_store_pg.py`)?
- Different access pattern: keyed by `hold_id` (Stripe ID), not `job_id`.
  Doesn't use the per-job advisory lock pattern.
- Different retention: payment_holds rows survive saga purges.
- Different lifecycle: the reconciler is the only background reader.

After Phase F + 1 week (when the JSON code path is deleted), the
`is_postgres_backend()` guards can collapse but the module stays.
"""

import logging
from typing import Optional

import asyncpg

from .payment.base import PaymentHold
from ..db import get_pool, is_postgres_backend

logger = logging.getLogger("laigo.payment_holds_store")

# Allowlist for last_known_status values. The schema doesn't constrain this
# with a CHECK, but every value we write should be one of these. Drift here
# would silently break the reconciliation filter `WHERE last_known_status =
# 'requires_capture'` and let reconcilable holds become invisible.
_ALLOWED_STATUSES = frozenset({"requires_capture", "succeeded", "canceled", "unknown"})


async def record_hold(checkout_id: str, hold: PaymentHold) -> None:
    """INSERT a new hold record with `last_known_status='requires_capture'`.

    Called by the saga immediately after `provider.create_hold()` succeeds,
    BEFORE the corresponding `sagas` UPDATE writes the `payment_hold_id`.
    Order matters: if the `payment_holds` INSERT fails (DB transient outage),
    the saga's `checkout_store.update` would still write the hold_id into
    the sagas table, but the reconciler wouldn't find it. By doing this
    insert first, a failure propagates to the saga's exception handler and
    the hold can be cancelled by the saga's own compensation path.

    No-op when DB_BACKEND != postgres. The saga's checkout_state.json on
    disk carries the hold_id in JSON mode; reconciliation simply isn't
    supported there.

    Raises asyncpg errors (FK violation, etc.) so the saga's exception
    handler can react. Does NOT raise on duplicate hold_id (ON CONFLICT
    DO NOTHING — handles the rare resumed-saga case where create_hold's
    idempotency key returns the same hold).
    """
    if not is_postgres_backend():
        return

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO payment_holds (
            hold_id, checkout_id, provider, mode,
            amount_authorized_cents, currency,
            last_known_status, last_reconciled_at, created_at
        ) VALUES (
            $1, $2, $3, $4,
            $5, $6,
            'requires_capture', NOW(), NOW()
        )
        ON CONFLICT (hold_id) DO NOTHING
        """,
        hold.hold_id,
        checkout_id,
        hold.provider,
        hold.mode,
        hold.amount_authorized_cents,
        hold.currency,
    )


async def mark_status(hold_id: str, status: str) -> None:
    """UPDATE last_known_status + bump last_reconciled_at.

    Called from the saga's capture/cancel success paths to advance the row
    out of the reconciliation candidate set. Also called from
    `reconcile_orphan_holds` (Phase E step 2, not yet shipped) when the
    Stripe API confirms a terminal status.

    `status` must be one of `_ALLOWED_STATUSES`; passing anything else
    raises ValueError REGARDLESS of backend — this is a programmer-error
    check that should catch typos in any code path. Callers that get a
    status string from the Stripe API should map it to one of these
    values BEFORE calling this function.

    For a valid `status`, the function no-ops when DB_BACKEND != postgres
    OR when the hold_id isn't in payment_holds (idempotent — the saga can
    fire this for a hold that was never recorded, e.g., when the INSERT
    raced or `record_hold` was skipped for any reason).
    """
    if status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"Invalid payment_holds status {status!r}. "
            f"Allowed: {sorted(_ALLOWED_STATUSES)}."
        )

    if not is_postgres_backend():
        return

    pool = get_pool()
    await pool.execute(
        """
        UPDATE payment_holds
        SET last_known_status = $2,
            last_reconciled_at = NOW()
        WHERE hold_id = $1
        """,
        hold_id,
        status,
    )


async def fetch_for_reconcile(*, older_than_seconds: int = 3600) -> list[dict]:
    """Return candidate rows for `reconcile_orphan_holds` (Phase E step 2).

    A row is a candidate if:
      - `last_known_status = 'requires_capture'` (uncaptured hold), AND
      - `last_reconciled_at < NOW() - older_than_seconds` (not recently checked)

    The LEFT JOIN to `sagas` is for the reconciler's decision matrix —
    saga state determines whether to cancel (saga is terminal but hold
    still authorized) or escalate (saga still in stripe_held but stale).

    Returns an empty list when DB_BACKEND != postgres. Step 2 won't run
    in JSON mode anyway, but the helper stays consistent with the rest
    of the module.
    """
    if not is_postgres_backend():
        return []

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT ph.hold_id, ph.checkout_id, ph.provider, ph.mode,
               ph.amount_authorized_cents, ph.currency,
               ph.last_known_status, ph.last_reconciled_at, ph.created_at,
               s.saga_status, s.job_id, s.last_transition_at
        FROM payment_holds ph
        LEFT JOIN sagas s ON s.payment_hold_id = ph.hold_id
        WHERE ph.last_known_status = 'requires_capture'
          AND ph.last_reconciled_at < NOW() - make_interval(secs => $1)
        ORDER BY ph.created_at
        """,
        older_than_seconds,
    )
    # Each row keys: hold_id, checkout_id, provider, mode, amount_authorized_cents,
    # currency, last_known_status, last_reconciled_at, created_at, saga_status,
    # job_id, last_transition_at. saga_status / job_id / last_transition_at are
    # NULL when the LEFT JOIN doesn't match (rare — payment_holds.checkout_id
    # has a FK to checkouts, but the sagas row may have been purged or never
    # written if a saga crashed between record_hold and the sagas update).
    return [dict(r) for r in rows]
