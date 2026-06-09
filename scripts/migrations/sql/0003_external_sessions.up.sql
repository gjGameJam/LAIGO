-- 0003_external_sessions.up.sql
--
-- Store Playwright `storage_state` JSON for external accounts that LAIGO
-- automates against. Keyed by `provider` so we can add brickowl etc. later
-- without a new table.
--
-- First consumer: provider='lego'. LEGO.com 2FA is email-only (no TOTP),
-- so we cannot automate the login challenge headlessly. Instead we seed
-- this row once via a headed Playwright session (Google SSO login,
-- context.storage_state() exported, written here), and the LEGO ordering
-- path in lego_client.py loads this row into browser.new_context(
-- storage_state=...) on every order. When the cached state expires (LEGO
-- rotates cookies, session drops), `order_from_lego` raises
-- LegoSessionExpiredError, the saga writes MANUAL_REVIEW, and an operator
-- re-seeds via scripts/seed_lego_session.py.
--
-- Schema notes:
--   * provider is the natural primary key (one row per provider).
--   * storage_state is JSONB so the JSONB type codec registered in
--     scripts/db.py handles encode/decode — callers pass a Python dict.
--     storage_state is small in practice (4-20KB for a logged-in LEGO
--     session); no TOAST concerns.
--   * notes is optional free text — e.g. "seeded 2026-06-09 via Google
--     SSO from Grant's laptop". Surfaces in the refresh runbook.
--   * updated_at is set explicitly by save_storage_state() on every UPSERT.
--     No trigger — the store module is the single write site.
--
-- B45 schema-evolution contract: storage_state is NOT NULL with no DEFAULT,
-- but this is a fresh table — no existing rows to break. There is NO insert
-- helper to update because lego_session_store.py issues an explicit two-column
-- INSERT ... ON CONFLICT UPDATE for this table only.
--
-- NO inner BEGIN/COMMIT — alembic wraps in a transaction.

CREATE TABLE external_sessions (
    provider       TEXT          PRIMARY KEY,
    storage_state  JSONB         NOT NULL,
    notes          TEXT,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
