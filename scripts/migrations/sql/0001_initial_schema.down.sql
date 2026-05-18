-- 0001_initial_schema.down.sql
-- Reverse of 0001_initial_schema.up.sql.
--
-- NO inner BEGIN/COMMIT — alembic wraps `downgrade()` in a transaction. An
-- inner COMMIT would commit prematurely (Postgres has no nested transactions)
-- and leave alembic_version out of sync with reality. For psql replay outside
-- alembic, use `psql -1 -f 0001_initial_schema.down.sql` to wrap at the shell.
--
-- In production, a downgrade is a last-resort operation — prefer a
-- forward-only hot-fix migration (see §9.6.4 risk register).
--
-- Order: drop tables in reverse FK dependency order so RESTRICT-style FKs
-- don't block the drop even without CASCADE. payment_holds and sagas both
-- reference checkouts; sagas also references jobs. CASCADE is still belt-
-- and-suspenders so a partial downgrade leaves no orphan indices.

DROP TABLE IF EXISTS audit_events  CASCADE;
DROP TABLE IF EXISTS payment_holds CASCADE;
DROP TABLE IF EXISTS sagas         CASCADE;
DROP TABLE IF EXISTS checkouts     CASCADE;
DROP TABLE IF EXISTS jobs          CASCADE;
DROP TABLE IF EXISTS schema_meta   CASCADE;

-- pgcrypto was created by 0001; remove it last. IF EXISTS guards against
-- environments where the extension was pre-installed for other reasons.
DROP EXTENSION IF EXISTS pgcrypto;
