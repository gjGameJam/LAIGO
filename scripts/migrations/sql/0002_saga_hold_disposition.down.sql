-- 0002_saga_hold_disposition.down.sql
--
-- Reverse of 0002_saga_hold_disposition.up.sql. Drops the constraint first
-- (referenced by name) so the column drop doesn't fail on dependency.

ALTER TABLE sagas DROP CONSTRAINT IF EXISTS sagas_hold_disposition_valid;
ALTER TABLE sagas DROP COLUMN IF EXISTS hold_disposition;
