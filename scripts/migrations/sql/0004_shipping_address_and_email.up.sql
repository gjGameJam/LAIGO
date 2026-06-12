-- 0004_shipping_address_and_email.up.sql
--
-- Workstream A + D of docs/CHECKOUT_COMPLETION_PLAN.md.
--
-- Two additions, both JSONB so the type codec in scripts/db.py round-trips
-- Python dict/list transparently (no json.dumps at call sites):
--
--   1. checkouts.shipping_address — the customer's full, validated drop-ship
--      address, collected at /confirm (decision 2026-06-11: address point =
--      /confirm). Shape mirrors models.ShippingAddress:
--        {full_name, line1, line2?, city, state, postal_code, country, phone?}
--      NULLABLE with NO DEFAULT: rows created before this migration, and any
--      quote-only flow, legitimately have no address. /confirm always supplies
--      one (Pydantic-validated, US-only for v1). The saga reads it from state
--      and passes it into order_from_lego(...) for the LEGO drop-ship (Step E).
--
--   2. sagas.emails_sent — idempotency ledger for the customer notification
--      layer (Workstream D). A JSON array of event keys already emailed
--      (e.g. ["order_confirmation","kit_on_the_way"]). notifications.py /
--      saga.py check membership before sending so a saga retry or reconciler
--      pass never double-sends. NOT NULL DEFAULT '[]' so existing rows and any
--      code path that forgets to set it still read a usable empty list.
--
-- B45 schema-evolution contract: both columns are reachable by the explicit
-- INSERT helpers in checkout_store_pg.py (_insert_checkouts / _insert_sagas).
-- This migration ships in the SAME change as those helper updates plus the
-- _CHECKOUT_COLS / _SAGA_COLS additions and the _EXPECTED_SCHEMA_VERSION bump
-- to "0004" in scripts/db.py. shipping_address is nullable-no-default and
-- emails_sent has a DEFAULT, so neither breaks the all-columns-explicit INSERT
-- even if a value is omitted.
--
-- NO inner BEGIN/COMMIT — alembic wraps this in a transaction.

ALTER TABLE checkouts
    ADD COLUMN shipping_address JSONB;

ALTER TABLE sagas
    ADD COLUMN emails_sent JSONB NOT NULL DEFAULT '[]'::jsonb;
