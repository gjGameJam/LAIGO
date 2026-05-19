"""L6 audit log — structured event writer.

Implements the API locked in `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §2.5`,
backed by the Postgres `audit_events` table (schema §9.2.1).

Why L6 exists
=============
L0–L5 stop regressions from causing immediate damage; L6 is the post-hoc
detection layer that catches damage if a regression DOES land. Without L6,
the only signal that any safety layer rejected a customer is a WARNING
line on stdout (Render's log stream). With L6, every rejection is a
queryable row with structured `event`, actor, subject, and payload.

Contracts (LOCKED — do not change without coordinated frontend change)
=======================================================================

1. **emit() NEVER raises.** Audit must never break checkout. Any failure
   (DB outage, JSON serialization error, programmer error in the payload)
   logs CRITICAL via the standard logger and returns. Operators must alert
   on `[audit] FAILED` log lines to catch silent gaps.

2. **emit() is async.** §2.5 nominally says "sync function with async
   buffered flush" — that's a future optimization. v1 is plain async:
   callers `await audit.emit(...)`. The cost is one extra await on
   already-async hot paths (router dependencies, saga state transitions),
   which is negligible vs. the network round-trip to Neon.

3. **Event vocabulary is locked (§2.2).** New events extend `data`; do not
   rename top-level fields (`event`, `ts`, `request_id`, `actor`, `subject`,
   `data`). The schema reflects this — top-level keys are typed columns,
   only `data` is JSONB.

4. **No PII filtering in emit().** Callers are responsible for what they
   pass. Don't put unhashed customer email into `data`. The `actor.ip`
   field is the one exception — INET column, stored to support abuse
   detection per §2.7 alerting rules.

5. **No-op when DB_BACKEND != postgres.** During the staged migration
   (Phases A–E shipped, F not yet flipped), local dev with `DB_BACKEND=json`
   should boot and run with audit events silently dropped. After Phase F
   + 1 week observation, the JSON code path is deleted and this guard
   becomes dead code (to be removed in the same cleanup commit).

Storage shape
=============
The `audit_events` table maps the §2.1 envelope as follows:

    Envelope key      → Column
    ────────────────────────────────────────
    event             → event (TEXT NOT NULL)
    ts                → ts (TIMESTAMPTZ NOT NULL DEFAULT NOW())
    request_id        → request_id (TEXT)
    subject.job_id    → job_id (TEXT)
    subject.checkout_id → checkout_id (TEXT)
    actor.type        → actor_type (TEXT)
    actor.ip          → actor_ip (INET)
    actor.user_agent  → actor_user_agent (TEXT)
    data              → data (JSONB NOT NULL DEFAULT '{}')

The JSONB codec registered in `scripts/db.py:_register_jsonb_codec` lets
us pass `data` as a Python dict directly — no `json.dumps` at the call site.

Migration notes
===============
- A future column addition to `audit_events` (e.g., `tenant_id` for
  multi-tenancy) is additive: update this module + the schema migration
  in the same PR. Old code that doesn't pass the new field will continue
  to work — the column will be nullable or have a DB-side DEFAULT.
- A future change to `subject` / `actor` structure (adding `session_id`
  etc.) goes into `data` first; only after a stable shape emerges does
  it migrate to its own column. Keeps event evolution cheap.
"""

import logging
from typing import Optional

from ..db import get_pool, is_postgres_backend

logger = logging.getLogger("laigo.audit")


async def emit(
    event: str,
    *,
    subject: Optional[dict] = None,
    actor: Optional[dict] = None,
    data: Optional[dict] = None,
    request_id: Optional[str] = None,
) -> None:
    """Write one structured audit event to the `audit_events` table.

    NEVER RAISES — failures log CRITICAL and return. The audit log must
    never break checkout. Operators alert on `[audit] FAILED` to detect
    silent gaps.

    Args:
        event: event name, snake_case dot-separated (e.g., "gate.confirm_rejected").
            Locked vocabulary lives in §2.2. New events extend `data`,
            never the envelope shape.
        subject: what's being acted on. Recognized keys: `job_id`,
            `checkout_id`. Extras are silently ignored (extra keys go in
            `data` if they need persistence).
        actor: who initiated. Recognized keys: `type` ("customer" | "system"
            | "operator"), `ip` (string, will be cast to INET), `user_agent`.
        data: event-specific JSON-serializable payload. Stored verbatim in
            the JSONB column.
        request_id: correlation ID from FastAPI middleware (future). Null
            until that middleware lands.

    No-op when DB_BACKEND != postgres.
    """
    if not is_postgres_backend():
        return

    subject = subject or {}
    actor = actor or {}
    data = data or {}

    try:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO audit_events (
                event, ts, request_id,
                job_id, checkout_id,
                actor_type, actor_ip, actor_user_agent,
                data
            ) VALUES (
                $1, NOW(), $2,
                $3, $4,
                $5, $6::inet, $7,
                $8::jsonb
            )
            """,
            event,
            request_id,
            subject.get("job_id"),
            subject.get("checkout_id"),
            actor.get("type"),
            actor.get("ip"),
            actor.get("user_agent"),
            # Pass the dict directly — `db._register_jsonb_codec` does
            # `json.dumps` on the way in. Calling json.dumps here would
            # double-encode and store a quoted JSON string instead of a
            # JSONB object (broke initial Phase E tests). Same convention
            # as `checkout_store_pg.py` for `allocation` and
            # `brickowl_order_ids`.
            data,
        )
    except Exception as exc:
        # NEVER RAISE. Audit must not break checkout. Log loud enough that
        # operator alerting (§2.7) can detect silent gaps in the audit
        # stream. Include the event name so a flood of failures for one
        # event type is greppable.
        logger.critical(
            f"[audit] FAILED for event={event!r}: {type(exc).__name__}: {exc}",
            exc_info=True,
        )
