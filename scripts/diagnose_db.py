"""Debug utility — connects to the DSN in env var DIAG_DSN and reports which
database / schema / user / alembic_version it actually reaches.

When to use:
- "App is on Postgres but I'm not sure which Neon branch/project."
- "alembic_version mismatch at boot — is my DATABASE_URL pointing where I think?"
- "Render env vars look right but the app sees stale schema."
- Sanity-check any DSN before pasting it into Render or .env.secrets.

Run from project root:
    $env:DIAG_DSN = "<paste full DSN — pooler or direct, either works>"
    .\.venv\Scripts\python.exe scripts\diagnose_db.py

Output is four labelled lines. Caught the Phase F cutover endpoint mismatch
on 2026-05-22 (DATABASE_URL and DATABASE_URL_DIRECT were pointed at two
different Neon endpoints). See docs/DATABASE_OPS.md "Debug playbook" for the
common patterns.
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
