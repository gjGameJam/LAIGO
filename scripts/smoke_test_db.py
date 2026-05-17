"""
Phase A smoke test — validates the Neon DATABASE_URL.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.smoke_test_db

Checks:
  1. DATABASE_URL is set in .env.secrets (loaded via python-dotenv)
  2. Host contains '-pooler' (refuses direct endpoint per scripts/db.py guard)
  3. TLS handshake succeeds (sslmode=require)
  4. SELECT 1 round-trips
  5. SELECT version() returns the Postgres major version
  6. statement_cache_size=0 doesn't blow up against the transaction-mode pooler

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
    finally:
        await conn.close()

    print()
    print("Phase A smoke test PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
