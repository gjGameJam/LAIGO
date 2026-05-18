-- 0001_initial_schema.up.sql
-- Initial LAIGO schema. Six tables. Translated verbatim from
-- docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.2.1 + §9.2.2.
--
-- Design contract notes (do not edit lightly):
--   * All id columns are TEXT to preserve existing string IDs.
--   * All timestamps are TIMESTAMPTZ — zone-less TIMESTAMP is a Postgres footgun.
--   * JSONB shapes are owned by Pydantic models in scripts/checkout/models.py
--     (and scripts/models.py for the mosaic side). Do NOT query into JSONB via
--     ->>/-> in application code; deserialize via Pydantic.
--   * sagas_one_active_per_job_idx is the DB-level enforcement of B23
--     (at most one non-terminal saga per job_id). The router converts the
--     resulting UniqueViolationError into a 422.
--
-- NO inner BEGIN/COMMIT here. Alembic wraps the upgrade in a transaction via
-- `context.begin_transaction()`; an inner COMMIT would commit the alembic-
-- managed outer transaction prematurely (Postgres has no nested transactions).
-- If the connection then dropped before alembic wrote the `alembic_version`
-- row, the schema would be committed but unrecorded, and a re-run of
-- `alembic upgrade head` would fail on "relation jobs already exists" with no
-- way to recover except manually dropping every table. Trust alembic's
-- transactional wrapping. For psql replay outside alembic, wrap the apply with
-- `BEGIN;` and `COMMIT;` at the shell level instead (e.g., `psql -1 -f ...`).

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() for generated IDs

-- ─── jobs ───────────────────────────────────────────────────────────────────
-- Mosaic generation lifecycle. One row per POST /generate.
CREATE TABLE jobs (
    job_id              TEXT PRIMARY KEY,
    status              TEXT NOT NULL,                  -- 'queued'|'running'|'complete'|'failed'|'timed_out'
    mosaic_type         TEXT NOT NULL,                  -- '2d'|'3d'
    width_blocks        INTEGER NOT NULL,
    background_pct      INTEGER,
    dither              BOOLEAN NOT NULL,
    upload_filename     TEXT,                           -- original filename from upload
    progress_pct        SMALLINT NOT NULL DEFAULT 0,    -- 0-100
    error_message       TEXT,
    queued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    ttl_expires_at      TIMESTAMPTZ NOT NULL,           -- when cleanup_loop should purge artifacts
    -- Customer-facing metadata for L6 audit correlation
    customer_email      TEXT,
    customer_ip         INET,
    user_agent          TEXT,
    CONSTRAINT jobs_status_valid CHECK (status IN ('queued','running','complete','failed','timed_out')),
    CONSTRAINT jobs_progress_range CHECK (progress_pct BETWEEN 0 AND 100)
);

-- ─── checkouts ──────────────────────────────────────────────────────────────
-- One row per POST /quote. Holds the optimized allocation + customer metadata.
-- A `job_id` can have multiple `checkouts` (different shipping addresses, retried quotes).
CREATE TABLE checkouts (
    checkout_id         TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    shipping_country    CHAR(2) NOT NULL,
    shipping_zip        TEXT NOT NULL,
    customer_email      TEXT NOT NULL,
    -- AllocationResult Pydantic model serialized. ~5 KB typical.
    -- Shape owned by scripts/checkout/models.py — DO NOT query into this from SQL.
    allocation          JSONB NOT NULL,
    -- Items with no listings from any source. Empty list = quote can_proceed.
    unsourceable_items  JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,           -- created_at + 10 min by default
    CONSTRAINT checkouts_country_iso CHECK (LENGTH(shipping_country) = 2)
);

-- ─── sagas ──────────────────────────────────────────────────────────────────
-- One row per POST /confirm. Holds the full saga state.
-- This table replaces outputs/{job_id}/checkout_state.json.
CREATE TABLE sagas (
    checkout_id             TEXT PRIMARY KEY REFERENCES checkouts(checkout_id) ON DELETE RESTRICT,
    job_id                  TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    saga_status             TEXT NOT NULL,
    -- Payment-side fields, provider-agnostic (L5 naming)
    payment_provider        TEXT,                       -- 'stripe' today
    payment_mode            TEXT,                       -- 'test'|'live'
    payment_hold_id         TEXT,                       -- pi_... for Stripe
    payment_authorized_cents INTEGER,
    total_charged_cents     INTEGER,
    -- Marketplace order tracking
    brickowl_order_ids      JSONB NOT NULL DEFAULT '[]'::JSONB,  -- list[str]
    lego_order_id           TEXT,
    -- Error / review fields
    error_message           TEXT,                       -- internal/operator-facing text
    customer_message        TEXT,                       -- customer-facing translation (post-B12/H1)
    manual_review_reason    TEXT,                       -- verbose runbook when saga_status='manual_review'
    -- Timing
    initiated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_transition_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    CONSTRAINT sagas_status_valid CHECK (saga_status IN (
        'initiated','stripe_held','orders_placed','fallback_ordered',
        'payment_captured','compensated','failed','manual_review'
    )),
    -- B23 enforcement at the DB level: at most one non-terminal saga per job_id.
    -- Implemented as a partial unique index below.
    CONSTRAINT sagas_payment_amounts CHECK (
        (payment_authorized_cents IS NULL OR payment_authorized_cents >= 0) AND
        (total_charged_cents IS NULL OR total_charged_cents >= 0)
    )
);

-- ─── payment_holds ──────────────────────────────────────────────────────────
-- Reconciliation index. Every Stripe hold ever created. Periodic scanner uses
-- this to find orphans. Separate from `sagas` because a saga row can be
-- archived/purged while the hold record needs longer retention for chargebacks.
CREATE TABLE payment_holds (
    hold_id                 TEXT PRIMARY KEY,           -- pi_... for Stripe
    checkout_id             TEXT NOT NULL REFERENCES checkouts(checkout_id) ON DELETE RESTRICT,
    provider                TEXT NOT NULL,              -- 'stripe'
    mode                    TEXT NOT NULL,              -- 'test'|'live'
    amount_authorized_cents INTEGER NOT NULL,
    currency                CHAR(3) NOT NULL,
    -- Status mirrored from Stripe; updated by reconciliation task
    last_known_status       TEXT NOT NULL,              -- 'requires_capture'|'succeeded'|'canceled'|'unknown'
    last_reconciled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── audit_events ───────────────────────────────────────────────────────────
-- L6 audit log. Append-only. Indexed for time-range and per-job queries.
CREATE TABLE audit_events (
    id                  BIGSERIAL PRIMARY KEY,
    event               TEXT NOT NULL,                  -- 'gate.confirm_rejected', 'saga.captured', etc.
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id          TEXT,
    job_id              TEXT,                           -- FK omitted; events outlive jobs (retention asymmetry)
    checkout_id         TEXT,
    actor_type          TEXT,                           -- 'customer'|'system'|'operator'
    actor_ip            INET,
    actor_user_agent    TEXT,
    data                JSONB NOT NULL DEFAULT '{}'::JSONB
);

-- ─── schema_meta ────────────────────────────────────────────────────────────
-- Single-row metadata. alembic writes its own version table; this is for
-- application-level invariants (e.g., "what's the oldest record we've migrated").
CREATE TABLE schema_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Indices (§9.2.2) ───────────────────────────────────────────────────────
-- Plain CREATE INDEX (no CONCURRENTLY) — the table is empty at migration time,
-- the operation is fast, and CONCURRENTLY is incompatible with alembic's
-- transactional wrapper anyway. Future indices on populated tables should use
-- CONCURRENTLY + their own migration file (see §9.5.B Step 6 deploy notes).

-- jobs: ad-hoc operator queries (active queue, recent failures, TTL cleanup)
CREATE INDEX jobs_status_queued_at_idx ON jobs (status, queued_at DESC)
    WHERE status IN ('queued','running');
CREATE INDEX jobs_ttl_idx ON jobs (ttl_expires_at)
    WHERE status IN ('complete','failed','timed_out');

-- checkouts: lookup by job_id (for B23 same-job check), expiry sweep
CREATE INDEX checkouts_job_id_idx ON checkouts (job_id, created_at DESC);
CREATE INDEX checkouts_expiry_idx ON checkouts (expires_at);

-- sagas: B23 partial unique index — at most one non-terminal saga per job_id.
-- This is the structural fix for B23 (concurrent /confirm race). Any second
-- INSERT for the same job_id with a non-terminal status raises
-- UniqueViolationError; the router catches and returns 422.
CREATE UNIQUE INDEX sagas_one_active_per_job_idx ON sagas (job_id)
    WHERE saga_status NOT IN ('payment_captured','compensated','failed','manual_review');
-- sagas: operator dashboards
CREATE INDEX sagas_status_idx ON sagas (saga_status, last_transition_at DESC);
CREATE INDEX sagas_completed_at_idx ON sagas (completed_at DESC)
    WHERE saga_status = 'payment_captured';

-- payment_holds: orphan scanner (every 5 min)
CREATE INDEX payment_holds_reconcile_idx ON payment_holds (last_reconciled_at)
    WHERE last_known_status = 'requires_capture';

-- audit_events: time-range, per-event-type, per-job
CREATE INDEX audit_events_ts_idx ON audit_events (ts DESC);
CREATE INDEX audit_events_event_ts_idx ON audit_events (event, ts DESC);
CREATE INDEX audit_events_job_ts_idx ON audit_events (job_id, ts DESC)
    WHERE job_id IS NOT NULL;
-- JSONB GIN for ad-hoc payload queries; deferred until query patterns are known
-- (see §9.2.2 last comment).
-- CREATE INDEX audit_events_data_gin_idx ON audit_events USING GIN (data);
