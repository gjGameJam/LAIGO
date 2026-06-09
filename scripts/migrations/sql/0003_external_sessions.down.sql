-- 0003_external_sessions.down.sql
--
-- Reverse of 0003_external_sessions.up.sql. Dropping the table is
-- destructive — any seeded session rows are lost and the LEGO ordering
-- path will fail with LegoSessionExpiredError('not_seeded') until
-- re-seeded against the previous schema's table.

DROP TABLE IF EXISTS external_sessions;
