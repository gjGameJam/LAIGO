"""
Postgres-backed checkout state.

Mirrors the public API of `checkout_store.py` (load / save / update /
read_order_list) so callers via `checkout_store_dispatch` work
identically whether DB_BACKEND is "json" or "postgres".

Design contract:
- Single source of truth: the `sagas` row keyed by `job_id`. The
  partial-unique index `sagas_one_active_per_job_idx` (per §9.2.2)
  guarantees at most ONE non-terminal saga per job_id at any time,
  so "the saga for this job_id" is unambiguous in the active window.
  Terminal sagas accumulate; load() returns the most recent by
  `initiated_at DESC` to surface "this job's current/last saga."

- save() INSERTs into BOTH tables in a single transaction:
  `checkouts` row first (FK target), then `sagas` row (depends on
  checkouts via FK). Wrapped in `conn.transaction()` for atomicity —
  either both rows commit or neither does. On B23 violation, raises
  `ActiveCheckoutExistsError` so the router can return 422.

- update() uses Pattern 2 (§9.2.5): advisory lock + FOR UPDATE + merge +
  UPDATE. The advisory lock is `pg_advisory_xact_lock(hashtextextended(
  job_id, 0))` — INT8 hash (lower collision rate than the INT4 `hashtext`
  per §9.5.C Step 4 retro note). FOR UPDATE on the saga row is the
  row-level belt; the advisory lock is the cross-coroutine suspender.

- JSONB columns (`checkouts.allocation`, `sagas.brickowl_order_ids`)
  pass through transparently — the asyncpg type codec registered in
  `scripts/db.py` handles encode/decode. Callers pass/receive Python
  dict/list directly.

- The state dict's key names mostly match the schema, with three explicit
  renames (see `_SAGA_RENAMES`):
    state["error"]              <-> sagas.error_message
  Plus four state keys that don't have DB columns and are silently
  dropped on save/update:
    state["traceback"]          (legacy debug field)
    Anything else not in _CHECKOUT_COLS or _SAGA_COLS.

Lock semantics:
- NO module-level asyncio.Locks (in contrast to checkout_store.py's
  `_locks` dict). Postgres advisory locks + row-level FOR UPDATE replace
  it entirely.
- The advisory lock is xact-scoped — released automatically on COMMIT /
  ROLLBACK. No `pg_advisory_xact_unlock` needed.
- Multiple FastAPI processes (future MAX_WORKERS>1 scenario) serialize
  cleanly via the advisory lock; pure-asyncio behavior is preserved
  inside a single process via the same primitive.

Idempotency:
- save() is NOT idempotent. A second save() for the same checkout_id
  raises asyncpg.UniqueViolationError on the checkouts PK. The router's
  B14 check (load() returning the same checkout_id → 409) prevents
  this in practice.
- update() is idempotent for repeated partials (merge is commutative
  for non-overlapping keys; last-write-wins for overlapping keys).
- read_order_list() is re-exported from the JSON module — order_list.json
  is a mosaic-pipeline artifact, not state. Always filesystem-backed.

Schema mapping (per §9.2.1):

  State key                      → Table.column
  ─────────────────────────────────────────────────────────────────────
  checkout_id                    → checkouts.checkout_id (PK)
                                   AND sagas.checkout_id (PK + FK)
  job_id                         → checkouts.job_id (FK)
                                   AND sagas.job_id (FK)
                                   (passed as func arg, not state key)
  shipping_country               → checkouts.shipping_country
  shipping_zip                   → checkouts.shipping_zip
  customer_email                 → checkouts.customer_email
  allocation                     → checkouts.allocation (JSONB)
  saga_status                    → sagas.saga_status
  payment_provider               → sagas.payment_provider
  payment_mode                   → sagas.payment_mode
  payment_hold_id                → sagas.payment_hold_id
  payment_authorized_cents       → sagas.payment_authorized_cents
  total_charged_cents            → sagas.total_charged_cents
  brickowl_order_ids             → sagas.brickowl_order_ids (JSONB)
  lego_order_id                  → sagas.lego_order_id
  error                          → sagas.error_message       ← RENAME
  customer_message               → sagas.customer_message
  manual_review_reason           → sagas.manual_review_reason
  completed_at                   → sagas.completed_at
  initiated_at                   → sagas.initiated_at (DB-managed default)
  last_transition_at             → sagas.last_transition_at (DB-managed)

See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.C and §9.2.5.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from ..db import get_pool
from .checkout_store import ActiveCheckoutExistsError, read_order_list  # noqa: F401 (re-export)

logger = logging.getLogger("laigo.checkout_store_pg")

# ─── Schema mapping ──────────────────────────────────────────────────────────

# State keys that map to columns on the `checkouts` table. Used by save()
# and update() to route writes to the right table.
# Value = column name (None if state key matches column name).
_CHECKOUT_COLS: dict[str, str] = {
    "checkout_id":      "checkout_id",
    "shipping_country": "shipping_country",
    "shipping_zip":     "shipping_zip",
    "customer_email":   "customer_email",
    "allocation":       "allocation",
    # Workstream A — full drop-ship address (JSONB), collected at /confirm.
    # NULL for quote-only / pre-0004 rows. See ShippingAddress in models.py.
    "shipping_address": "shipping_address",
}

# State keys that map to columns on the `sagas` table.
_SAGA_COLS: dict[str, str] = {
    "checkout_id":              "checkout_id",
    "saga_status":              "saga_status",
    "payment_provider":         "payment_provider",
    "payment_mode":             "payment_mode",
    "payment_hold_id":          "payment_hold_id",
    "payment_authorized_cents": "payment_authorized_cents",
    "total_charged_cents":      "total_charged_cents",
    "brickowl_order_ids":       "brickowl_order_ids",
    "lego_order_id":            "lego_order_id",
    "error":                    "error_message",   # rename
    "customer_message":         "customer_message",
    "manual_review_reason":     "manual_review_reason",
    # B55 — orphan-hold reconciler reads this to disambiguate "operator wants
    # this cancelled" (cancel_safe) from "operator must decide" (operator_decides).
    # Set at every MANUAL_REVIEW write site in saga.py + saga_resume.py. NULL
    # for any non-MANUAL_REVIEW state OR for MANUAL_REVIEW with no hold to
    # dispose. See HoldDisposition in models.py.
    "hold_disposition":         "hold_disposition",
    # Workstream D — idempotency ledger for the customer email layer. JSON
    # array of event keys already sent (e.g. ["order_confirmation"]). The
    # notification helpers check membership before sending so saga retries /
    # reconciler passes never double-send. NOT NULL DEFAULT '[]' in the schema.
    "emails_sent":              "emails_sent",
    "completed_at":             "completed_at",
}

# Reverse rename for reads: DB column → state key. Most match directly;
# only `error_message` is renamed back to `error` on the way out.
_DB_TO_STATE_KEY: dict[str, str] = {"error_message": "error"}

# State keys whose values may arrive as ISO 8601 strings but need to be
# datetime objects for asyncpg. Outgoing rows convert back to ISO strings
# (matching the JSON backend's representation).
_TIMESTAMP_KEYS = {"completed_at", "initiated_at", "last_transition_at"}

# Union of all state keys the Postgres backend understands — used by
# `_warn_unknown_keys` to detect typos in caller-supplied state dicts.
# Any state key NOT in this set is silently dropped by `_build_update_clause`
# (matching the JSON backend's "save any dict" semantics at the data layer),
# but we log a WARN so operators can spot the drift in production.
_ALL_KNOWN_STATE_KEYS: frozenset[str] = frozenset(_CHECKOUT_COLS) | frozenset(_SAGA_COLS)


def _coerce_for_db(key: str, value):
    """Coerce a state value to an asyncpg-acceptable form on the way in."""
    if value is None:
        return None
    # Pydantic enum value (e.g., SagaStatus.INITIATED, HoldDisposition.CANCEL_SAFE)
    # — extract .value so the DB sees the string the CHECK constraint expects.
    # Listed explicitly (not generalized to "any enum") to keep the conversion
    # surface intentional; adding a new enum-bearing column requires touching
    # this list, which is the right level of friction.
    if key in ("saga_status", "hold_disposition") and hasattr(value, "value"):
        return value.value
    # Timestamp keys may arrive as ISO strings (the JSON backend's format).
    if key in _TIMESTAMP_KEYS and isinstance(value, str):
        # `fromisoformat` handles "+00:00" but Python < 3.11 doesn't accept
        # the trailing "Z" form; normalize for safety.
        s = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return value  # last-resort: let asyncpg try
    return value


def _coerce_from_db(row: asyncpg.Record) -> dict:
    """Convert an asyncpg.Record row to a plain state dict for the caller.

    Renames `error_message` → `error` to match the JSON backend's state
    shape. Converts timestamp columns to ISO 8601 strings (also matching
    the JSON backend). JSONB columns are already Python dict/list thanks
    to the per-connection codec registered in `scripts/db.py`.
    """
    result: dict = {}
    for key in row.keys():
        value = row[key]
        # Rename DB column → state key if applicable.
        out_key = _DB_TO_STATE_KEY.get(key, key)
        if isinstance(value, datetime):
            result[out_key] = value.isoformat()
        else:
            result[out_key] = value
    return result


def _warn_unknown_keys(partial: dict, where: str) -> None:
    """Log WARN if `partial` contains state keys we don't know how to persist.

    The Postgres backend has a fixed schema; unknown keys are silently
    dropped by `_build_update_clause`. The JSON backend stores any dict
    unchanged, so a typo like `state["chekout_id"]` is invisible there
    but pathological here (the intended update silently no-ops).

    This helper surfaces that drift to operator logs. WARN-level (not
    ERROR/CRITICAL) because the call still succeeds at the DB level —
    the dropped key is the SAME outcome the JSON backend would produce
    if the key were ignored on read. Engineering is expected to fix the
    typo when the WARN shows up in monitoring.

    See PRE_RELEASE §4 B46.
    """
    unmapped = set(partial.keys()) - _ALL_KNOWN_STATE_KEYS
    if unmapped:
        # ASCII-only message — Windows console (cp1252 default) cannot
        # render unicode set-union, would crash operator stdout.
        logger.warning(
            "[checkout_store_pg.%s] %d state key(s) silently dropped "
            "(not in _CHECKOUT_COLS or _SAGA_COLS): %s. Possible typo. "
            "Known keys: %s",
            where,
            len(unmapped),
            sorted(unmapped),
            sorted(_ALL_KNOWN_STATE_KEYS),
        )


# ─── Public API ──────────────────────────────────────────────────────────────


async def load(job_id: str) -> Optional[dict]:
    """Return the latest saga state for `job_id`, joined with its checkouts row.

    Latest = highest `sagas.initiated_at`. B23's partial unique index
    guarantees ≤1 non-terminal saga per job_id, so when multiple sagas
    exist for one job_id, all but the latest are terminal. Caller decides
    whether to act on the returned state's `saga_status`.

    Returns None when no saga has been created yet for this job_id (e.g.,
    /quote has been called but /confirm has not).
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            s.checkout_id,
            s.saga_status,
            s.payment_provider,
            s.payment_mode,
            s.payment_hold_id,
            s.payment_authorized_cents,
            s.total_charged_cents,
            s.brickowl_order_ids,
            s.lego_order_id,
            s.error_message,
            s.customer_message,
            s.manual_review_reason,
            s.hold_disposition,
            s.emails_sent,
            s.initiated_at,
            s.last_transition_at,
            s.completed_at,
            c.shipping_country,
            c.shipping_zip,
            c.customer_email,
            c.allocation,
            c.shipping_address
        FROM sagas s
        JOIN checkouts c ON s.checkout_id = c.checkout_id
        WHERE s.job_id = $1
        ORDER BY s.initiated_at DESC
        LIMIT 1
        """,
        job_id,
    )
    return _coerce_from_db(row) if row else None


async def save(job_id: str, state: dict) -> None:
    """Initial saga state — INSERT checkouts row, then INSERT sagas row.

    Single transaction. FK ordering matters: `sagas.checkout_id` references
    `checkouts.checkout_id`, so checkouts must be inserted first within
    the same tx.

    Raises:
        ActiveCheckoutExistsError: if a non-terminal saga already exists
            for this job_id (the B23 partial unique index fires on the
            sagas INSERT). Router catches and returns 422.
        asyncpg.UniqueViolationError: if `state["checkout_id"]` already
            exists in `checkouts` (caller passed a duplicate checkout_id).
            The router's B14 check prevents this in practice.
        asyncpg.ForeignKeyViolationError: if `jobs.job_id` doesn't exist.
            Phase D-foundation populates jobs at /generate; this would
            indicate a /confirm before /generate, which is also gated
            by the application flow.

    `state` keys are documented in the module docstring. Each INSERT lists
    every known column explicitly (B45 fix): if a future migration adds a
    NOT-NULL column to either table without a DEFAULT clause, the INSERT
    fails at runtime instead of silently corrupting state via the trailing
    UPDATE pattern this function used to use. See CLAUDE.md "Schema
    evolution discipline" for the rule.

    Missing state keys default to None for nullable columns. Defaults for
    NOT-NULL JSONB columns (`brickowl_order_ids`, `unsourceable_items`)
    come from the empty-list/empty-dict literal here, NOT the schema's
    DEFAULT clause, so we don't depend on the DB to fill them — keeps
    this code testable against migrations that don't preserve defaults.
    """
    _warn_unknown_keys(state, "save")
    pool = get_pool()
    checkout_id = state["checkout_id"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _insert_checkouts(conn, job_id, state)
            try:
                await _insert_sagas(conn, job_id, state)
            except asyncpg.UniqueViolationError as exc:
                # Distinguish the B23 violation (partial unique index on job_id)
                # from a PK violation (duplicate checkout_id). The B23 case
                # warrants an application-level ActiveCheckoutExistsError so
                # callers can translate to 422 cleanly; PK violation is a
                # programmer error and stays as UniqueViolationError.
                constraint = getattr(exc, "constraint_name", "") or ""
                if "sagas_one_active_per_job_idx" in constraint:
                    raise ActiveCheckoutExistsError(job_id) from exc
                raise


# ─── INSERT helpers (B45 — all-columns-explicit pattern) ─────────────────────


async def _insert_checkouts(conn: asyncpg.Connection, job_id: str, state: dict) -> None:
    """INSERT a row in checkouts with every column listed explicitly.

    Every column from §9.2.1's checkouts schema appears in this INSERT's
    column list. Adding a new column to checkouts in a future migration
    REQUIRES updating this INSERT — caught immediately by the SQL planner
    if the new column is NOT NULL without a DEFAULT.

    `created_at` and `expires_at` use SQL NOW()/make_interval rather than
    Python-computed values so the DB clock is authoritative (avoids
    clock-skew between app instance and Neon).
    """
    await conn.execute(
        """
        INSERT INTO checkouts (
            checkout_id,
            job_id,
            shipping_country,
            shipping_zip,
            customer_email,
            allocation,
            unsourceable_items,
            shipping_address,
            created_at,
            expires_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8,
            NOW(),
            NOW() + make_interval(secs => 600)
        )
        """,
        state["checkout_id"],
        job_id,
        state.get("shipping_country", "US"),
        state.get("shipping_zip", ""),
        state.get("customer_email", ""),
        state.get("allocation", {}),
        state.get("unsourceable_items", []),
        # JSONB; the codec encodes a Python dict transparently. NULL when the
        # caller didn't supply an address (quote-only paths never call save()).
        state.get("shipping_address"),
    )


async def _insert_sagas(conn: asyncpg.Connection, job_id: str, state: dict) -> None:
    """INSERT a row in sagas with every column listed explicitly.

    Every column from §9.2.1's sagas schema appears in this INSERT's
    column list. Adding a new column to sagas in a future migration
    REQUIRES updating this INSERT — caught immediately by the SQL planner
    if the new column is NOT NULL without a DEFAULT.

    Triggers B23 enforcement: `asyncpg.UniqueViolationError` with
    `constraint_name="sagas_one_active_per_job_idx"` if another
    non-terminal saga already exists for this job_id. Caller (save())
    catches and translates to `ActiveCheckoutExistsError`.
    """
    await conn.execute(
        """
        INSERT INTO sagas (
            checkout_id,
            job_id,
            saga_status,
            payment_provider,
            payment_mode,
            payment_hold_id,
            payment_authorized_cents,
            total_charged_cents,
            brickowl_order_ids,
            lego_order_id,
            error_message,
            customer_message,
            manual_review_reason,
            hold_disposition,
            emails_sent,
            initiated_at,
            last_transition_at,
            completed_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
            NOW(),
            NOW(),
            $16
        )
        """,
        state["checkout_id"],
        job_id,
        _coerce_for_db("saga_status", state.get("saga_status", "initiated")),
        state.get("payment_provider"),
        state.get("payment_mode"),
        state.get("payment_hold_id"),
        state.get("payment_authorized_cents"),
        state.get("total_charged_cents"),
        state.get("brickowl_order_ids", []),
        state.get("lego_order_id"),
        state.get("error"),  # state["error"] -> column error_message
        state.get("customer_message"),
        state.get("manual_review_reason"),
        # B45 contract: hold_disposition is NULLABLE without a DEFAULT. Initial
        # saves never set it (no MANUAL_REVIEW at /confirm). saga.py +
        # saga_resume.py set it via update() at MANUAL_REVIEW transitions.
        _coerce_for_db("hold_disposition", state.get("hold_disposition")),
        # Workstream D — email idempotency ledger. Default empty list at insert;
        # notification helpers append keys via update() as emails go out.
        state.get("emails_sent", []),
        _coerce_for_db("completed_at", state.get("completed_at")),
    )


async def update(job_id: str, partial: dict) -> dict:
    """Merge `partial` into the latest saga for `job_id` and return the merged state.

    Pattern 2 from §9.2.5: advisory xact lock + SELECT FOR UPDATE + merge +
    UPDATE. The advisory lock serializes concurrent update() calls for the
    same job_id at the connection-pool level (cheaper than scanning sagas
    for the FOR UPDATE row contention path).

    Raises:
        ValueError: if no saga row exists yet for this job_id. Caller
            should have called save() first.

    Returns the merged state dict (post-update), matching the JSON
    backend's contract — callers rely on this to update their local
    in-memory copy without a second load() round-trip.
    """
    _warn_unknown_keys(partial, "update")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Advisory lock — INT8 hash (B3 from Phase B retro). Released on
            # COMMIT/ROLLBACK; no explicit unlock needed.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                job_id,
            )

            # Latch the latest saga's checkout_id under FOR UPDATE so it
            # can't be raced by another transaction not using our advisory
            # lock (defense-in-depth; in practice all writers go through here).
            row = await conn.fetchrow(
                """
                SELECT checkout_id
                FROM sagas
                WHERE job_id = $1
                ORDER BY initiated_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                job_id,
            )
            if row is None:
                raise ValueError(
                    f"No saga exists for job_id={job_id!r}. Call save() first."
                )
            checkout_id = row["checkout_id"]

            # Build the saga UPDATE. Always bump last_transition_at — every
            # successful update() shifts the saga's "last activity" timestamp
            # for operator dashboards.
            saga_sql, saga_args = _build_update_clause(partial, _SAGA_COLS)
            if saga_sql:
                saga_sql_with_ts = saga_sql + ", last_transition_at = NOW()"
            else:
                # No saga-mapped keys but the caller still called update();
                # still bump last_transition_at so /status reflects the
                # invocation.
                saga_sql_with_ts = "last_transition_at = NOW()"

            saga_args.append(checkout_id)
            await conn.execute(
                f"UPDATE sagas SET {saga_sql_with_ts} WHERE checkout_id = ${len(saga_args)}",
                *saga_args,
            )

            # Build the checkouts UPDATE (rare; only fires if caller mutates
            # shipping info post-confirm). checkout_id excluded — it's the PK,
            # never updated.
            checkout_sql, checkout_args = _build_update_clause(
                partial, _CHECKOUT_COLS, skip={"checkout_id"},
            )
            if checkout_sql:
                checkout_args.append(checkout_id)
                await conn.execute(
                    f"UPDATE checkouts SET {checkout_sql} WHERE checkout_id = ${len(checkout_args)}",
                    *checkout_args,
                )

        # Re-read post-commit so the caller gets a consistent merged view.
        # (Reading inside the same transaction would also work, but the
        # JOIN query is simpler to express against the already-released
        # advisory lock context.)
    merged = await load(job_id)
    if merged is None:  # pragma: no cover — would only fire on race with delete
        raise ValueError(f"saga for job_id={job_id!r} disappeared mid-update")
    return merged


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _build_update_clause(
    partial: dict,
    col_map: dict[str, str],
    *,
    skip: Optional[set] = None,
) -> tuple[str, list]:
    """Build a parameterized "col1 = $1, col2 = $2, ..." UPDATE clause.

    Returns ``(sql_fragment, args_list)``. Caller is responsible for
    appending the WHERE-clause args and computing the final placeholder
    index when concatenating.

    Keys in `partial` that aren't in `col_map` are silently dropped —
    matches the JSON backend's "save any dict" semantics (no schema).
    Keys in `skip` are also dropped, used by save() to avoid clobbering
    columns it already INSERTed.
    """
    skip = skip or set()
    sets: list[str] = []
    args: list = []
    for key, value in partial.items():
        if key in skip:
            continue
        col = col_map.get(key)
        if col is None:
            continue
        placeholder = f"${len(args) + 1}"
        sets.append(f"{col} = {placeholder}")
        args.append(_coerce_for_db(key, value))
    return ", ".join(sets), args
