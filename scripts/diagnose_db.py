"""One-off diagnostic — connects to the DSN in env var DIAG_DSN and reports
which database/schema/user/alembic_version it actually reaches.

Run from project root:
    $env:DIAG_DSN = "<paste the full DATABASE_URL from Render>"
    .\.venv\Scripts\python.exe scripts\diagnose_db.py

Delete this file once the cutover is confirmed.
"""

import asyncio
import os
import sys

import asyncpg


async def main() -> None:
    dsn = os.environ.get("DIAG_DSN")
    if not dsn:
        print("ERROR: set $env:DIAG_DSN to the DSN you want to inspect", file=sys.stderr)
        sys.exit(2)

    conn = await asyncpg.connect(dsn)
    try:
        db = await conn.fetchval("SELECT current_database()")
        user = await conn.fetchval("SELECT current_user")
        schema = await conn.fetchval("SELECT current_schema()")
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        print(f"database : {db}")
        print(f"user     : {user}")
        print(f"schema   : {schema}")
        print(f"version  : {version}")
    finally:
        await conn.close()


asyncio.run(main())
