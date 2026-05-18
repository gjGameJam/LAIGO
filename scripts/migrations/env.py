"""alembic environment — reads DATABASE_URL from environment, not alembic.ini.

The Neon DIRECT endpoint is required for alembic operations: session mode is
needed for migration transactions (the pooler's PgBouncer transaction mode
breaks things like CREATE INDEX CONCURRENTLY and named prepared statements).

To run against Neon:
    1. In the Neon dashboard, copy the DIRECT (non-pooler) connection string
       for the target branch (throwaway test branch first, then `main`).
    2. Set ALEMBIC_DATABASE_URL=<direct DSN> in your shell (NOT in .env).
    3. Run: alembic upgrade head

If ALEMBIC_DATABASE_URL is unset, falls back to DATABASE_URL (which the app
points at the pooler). The pooler works for plain CREATE TABLE but is unsafe
for future concurrent DDL — always prefer ALEMBIC_DATABASE_URL.

See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.B and §9.2.10.
"""
import os
from logging.config import fileConfig
from urllib.parse import urlsplit

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

dsn = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not dsn:
    raise RuntimeError(
        "Neither ALEMBIC_DATABASE_URL nor DATABASE_URL is set. "
        "Set ALEMBIC_DATABASE_URL to the Neon DIRECT (non-pooler) DSN before running alembic."
    )

# SQLAlchemy expects 'postgresql://' not 'postgres://'. Neon dashboard sometimes
# emits the latter. Normalize so operators don't have to remember.
if dsn.startswith("postgres://"):
    dsn = "postgresql://" + dsn[len("postgres://") :]

# Parallel guard to scripts/db.py — refuse to run alembic against the Neon
# pooler endpoint. The pooler runs PgBouncer in transaction mode, which breaks
# alembic's session-scoped DDL (multi-statement transactions, named prepared
# statements, advisory locks). The direct endpoint has session mode.
# Drop "-pooler" from the host to fix.
#
# Hostname parsed via urlsplit instead of substring-matching the whole DSN —
# a password coincidentally containing 'pooler' would otherwise mis-trigger
# this guard.
_host = (urlsplit(dsn).hostname or "").lower()
if "neon.tech" in _host and "-pooler" in _host:
    raise RuntimeError(
        "ALEMBIC_DATABASE_URL points at the Neon POOLER endpoint. Alembic needs "
        "the DIRECT endpoint (session mode) for migrations. "
        "Drop '-pooler' from the host: "
        "ep-xxxxxx-pooler.<region>.aws.neon.tech  ->  ep-xxxxxx.<region>.aws.neon.tech "
        "(or in the Neon dashboard: Connect > toggle Pooled connection OFF)."
    )

config.set_main_option("sqlalchemy.url", dsn)


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
