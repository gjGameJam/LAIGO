-- 0004_shipping_address_and_email.down.sql
--
-- Reverse of 0004_shipping_address_and_email.up.sql. Drops both columns.
-- Data loss on downgrade is acceptable: shipping_address and emails_sent are
-- only populated for checkouts created after this migration, and a downgrade
-- implies rolling back the code that reads them.
--
-- NO inner BEGIN/COMMIT — alembic wraps this in a transaction.

ALTER TABLE sagas
    DROP COLUMN IF EXISTS emails_sent;

ALTER TABLE checkouts
    DROP COLUMN IF EXISTS shipping_address;
