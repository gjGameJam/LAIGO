"""
Phase A smoke test — validates the Neon DATABASE_URL and alembic state.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.smoke_test_db

Checks:
  1. DATABASE_URL is set in .env.secrets (loaded via python-dotenv)
  2. Host contains '-pooler' (refuses direct endpoint per scripts/db.py guard)
  3. TLS handshake succeeds (sslmode=require)
  4. SELECT 1 round-trips
  5. SELECT version() returns the Postgres major version
  6. statement_cache_size=0 doesn't blow up against the transaction-mode pooler
  7. psycopg2 is importable (alembic needs it; Phase A originally missed this —
     the smoke test passed because it only exercised asyncpg, hiding the gap;
     B50 added this check) — see PRE_RELEASE §4 B50.
  8. alembic_version table exists and matches scripts/db.py:_EXPECTED_SCHEMA_VERSION

Prints the password as '***' so it doesn't appear in console output / logs.
Exits 0 on success, 1 on any failure.
"""

import asyncio
import os
import re
import ssl
import sys
from pathlib import Path

from dotenv import load_dotenv


def _scrub(dsn: str) -> str:
    """Replace the password in a DSN with '***' for safe printing."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)


async def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.secrets")
    load_dotenv(project_root / ".env")

    # B50 — verify alembic's sync driver is installed BEFORE touching the DB.
    # Phase A's original smoke test only used asyncpg; psycopg2-binary was
    # missing from requirements.txt and the gap stayed invisible until Phase B
    # tried to run `alembic upgrade head`. Importing here catches the gap
    # proactively whenever someone runs the smoke test on a fresh checkout.
    try:
        import psycopg2  # noqa: F401
        print(f"OK:     psycopg2 importable ({psycopg2.__version__}) — alembic will be able to connect")
    except ImportError as exc:
        print(f"FAIL:   psycopg2 not installed — alembic will fail to connect to Postgres.")
        print(f"        Install with: pip install --trusted-host pypi.org --trusted-host pypi.python.org "
              f"--trusted-host files.pythonhosted.org 'psycopg2-binary~=2.9'")
        print(f"        Underlying error: {exc}")
        return 1

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("FAIL: DATABASE_URL not set in .env.secrets")
        return 1

    print(f"DSN:    {_scrub(dsn)}")

    if "-pooler" not in dsn:
        print("FAIL: DSN host does not contain '-pooler' (you're using the direct endpoint).")
        print("      Flip 'Pooled connection' ON in the Neon dashboard and re-copy.")
        return 1

    if "sslmode=" not in dsn:
        print("WARN: DSN does not include sslmode= — TLS may not be enforced.")

    import asyncpg

    ssl_ctx = ssl.create_default_context()

    print("Connecting (asyncpg, statement_cache_size=0, TLS)...")
    try:
        conn = await asyncpg.connect(
            dsn=dsn,
            statement_cache_size=0,
            ssl=ssl_ctx,
            timeout=10.0,
        )
    except Exception as exc:
        print(f"FAIL: connect raised {type(exc).__name__}: {exc}")
        return 1

    try:
        one = await conn.fetchval("SELECT 1")
        if one != 1:
            print(f"FAIL: SELECT 1 returned {one!r}, expected 1")
            return 1
        print("OK:     SELECT 1 round-trip")

        version = await conn.fetchval("SELECT version()")
        print(f"server: {version}")

        major_match = re.search(r"PostgreSQL (\d+)", version or "")
        if major_match:
            major = int(major_match.group(1))
            if major >= 17:
                print(f"OK:     PostgreSQL {major} (>= 17, target version)")
            elif major == 16:
                print(f"WARN:   PostgreSQL 16 detected. Target was 17 — pick PG 17 next time.")
            else:
                print(f"WARN:   PostgreSQL {major} detected. Older than target (17).")

        current_db = await conn.fetchval("SELECT current_database()")
        current_user = await conn.fetchval("SELECT current_user")
        print(f"db/user: {current_db} / {current_user}")

        # B50 — verify alembic state. Catches two distinct failure modes:
        #   (a) Schema never applied to this branch (alembic_version table
        #       missing). asyncpg raises UndefinedTableError on the SELECT.
        #   (b) Schema applied at a different revision than this code expects
        #       (alembic_version row != _EXPECTED_SCHEMA_VERSION). Indicates
        #       a deploy ordering bug — usually the operator forgot to run
        #       `alembic upgrade head` before booting the new code.
        # Import _EXPECTED_SCHEMA_VERSION here (late) so the smoke test doesn't
        # touch the asyncpg pool from scripts/db.py — we want this to use its
        # own connection, isolated from any in-process state.
        from scripts.db import _EXPECTED_SCHEMA_VERSION
        try:
            current_rev = await conn.fetchval("SELECT version_num FROM alembic_version")
        except asyncpg.UndefinedTableError:
            print()
            print(f"FAIL:   alembic_version table missing.")
            print(f"        Schema migrations were never applied to this Neon branch.")
            print(f"        From a shell pointed at this branch's DIRECT (non-pooler) endpoint:")
            print(f"            $env:ALEMBIC_DATABASE_URL = '<direct DSN>'")
            print(f"            alembic upgrade head")
            return 1

        if current_rev is None:
            print()
            print(f"FAIL:   alembic_version table exists but is empty.")
            print(f"        A migration likely failed mid-apply. Inspect manually, then re-run "
                  f"`alembic upgrade head`.")
            return 1

        if current_rev != _EXPECTED_SCHEMA_VERSION:
            print()
            print(f"FAIL:   schema version mismatch:")
            print(f"        DB at:      {current_rev!r}")
            print(f"        Code wants: {_EXPECTED_SCHEMA_VERSION!r}")
            print(f"        Run `alembic upgrade head` against this branch's DIRECT endpoint, "
                  f"OR check out the matching code commit.")
            return 1

        print(f"OK:     alembic_version = {current_rev!r} matches "
              f"scripts/db.py:_EXPECTED_SCHEMA_VERSION")
    finally:
        await conn.close()

    print()
    print("Phase A smoke test PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
