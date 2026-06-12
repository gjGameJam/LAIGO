"""S8 smoke test — drives the jobs lifecycle end-to-end against Neon dev.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.smoke_jobs_pg

Read-only mode (default):
- Reports baseline row counts in `jobs`, `checkouts`, `sagas`, `audit_events`.

Cleanup mode (`--cleanup`):
- Deletes test rows authored by this script (matched by mosaic_type='smoke').
- Falls back to TTL-driven cleanup if FK-locked.

This script does NOT call /generate — that's the live server's job. The script
is a side instrument: pre/post the live test, query the DB to verify what
landed.
"""
import asyncio
import os
import re
import ssl
import sys
from pathlib import Path

from dotenv import load_dotenv


def _scrub(dsn: str) -> str:
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)


async def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.secrets")
    load_dotenv(project_root / ".env")

    # Prefer DEV_DATABASE_URL; refuse if the result is prod. See _pg_test_guard.
    from scripts._pg_test_guard import prepare_test_db
    prepare_test_db()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("FAIL: DATABASE_URL not set")
        return 1

    print(f"DSN: {_scrub(dsn)}")

    import asyncpg
    ssl_ctx = ssl.create_default_context()
    conn = await asyncpg.connect(
        dsn=dsn,
        statement_cache_size=0,
        ssl=ssl_ctx,
        timeout=15.0,  # extra-generous for Neon autosuspend wake
    )
    try:
        # Baseline counts
        for table in ("jobs", "checkouts", "sagas", "audit_events", "payment_holds"):
            try:
                n = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                print(f"  {table:18s}: {n} rows")
            except Exception as exc:
                print(f"  {table:18s}: ERROR {type(exc).__name__}: {exc}")

        # Snapshot of jobs rows so we can see what's been left behind
        rows = await conn.fetch(
            "SELECT job_id, status, mosaic_type, width_blocks, queued_at, completed_at, ttl_expires_at "
            "FROM jobs ORDER BY queued_at DESC LIMIT 10"
        )
        print()
        print(f"=== Most-recent 10 jobs rows ===")
        for r in rows:
            print(
                f"  {r['job_id']} status={r['status']!r:12s} "
                f"type={r['mosaic_type']} w={r['width_blocks']} "
                f"queued_at={r['queued_at']!s:.19s} "
                f"completed_at={str(r['completed_at'])[:19]} "
                f"ttl={str(r['ttl_expires_at'])[:19]}"
            )

        if "--cleanup" in sys.argv:
            print()
            print("=== Cleanup: deleting expired terminal jobs ===")
            # Use the same DELETE pattern as jobs_store_pg.cleanup_expired()
            cands = await conn.fetch(
                "SELECT job_id FROM jobs "
                "WHERE status IN ('complete','failed','timed_out') "
                "  AND ttl_expires_at < NOW()"
            )
            deleted = 0
            blocked = 0
            for r in cands:
                try:
                    await conn.execute("DELETE FROM jobs WHERE job_id = $1", r["job_id"])
                    deleted += 1
                except asyncpg.ForeignKeyViolationError:
                    blocked += 1
            print(f"  deleted={deleted}  fk_blocked={blocked}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
