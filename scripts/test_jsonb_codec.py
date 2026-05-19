"""
One-shot smoke test for the JSONB codec registered on the asyncpg pool.

Run from the project root with the venv activated and DB_BACKEND=postgres
in the environment:

    $env:DB_BACKEND = "postgres"
    .\.venv\Scripts\python.exe -m scripts.test_jsonb_codec

Exits 0 on success, non-zero on failure. Does NOT require uvicorn to be
running — opens its own pool, runs three checks, closes the pool.

Checks:
  1. JSONB literal returns as Python list (codec decode works).
  2. JSONB literal with nested dict returns as Python dict.
  3. Round-trip a Python dict through a temp table — confirms encode works
     (and not just decode of literals).

Does NOT mutate any production tables. The round-trip uses a TEMP TABLE
which is automatically dropped at the end of the session.
"""

import asyncio
import os
import sys


async def main() -> int:
    # Force the postgres path. If DB_BACKEND wasn't set, the smoke test
    # has no chance — it'd silently no-op on init_pool.
    os.environ.setdefault("DB_BACKEND", "postgres")

    # Late import so DB_BACKEND is read at module-load of db.py with the
    # right value (db.py's is_postgres_backend() reads env at call time,
    # but importing late costs us nothing and keeps the script readable).
    from scripts.db import init_pool, get_pool, close_pool

    print("Initializing pool...")
    await init_pool()
    pool = get_pool()

    failures = []

    # Check 1: JSONB list literal decoded as Python list
    print("Check 1: SELECT '[1, 2, 3]'::jsonb")
    row = await pool.fetchrow("SELECT '[1, 2, 3]'::jsonb AS j")
    j = row["j"]
    if j != [1, 2, 3] or not isinstance(j, list):
        failures.append(f"  Expected [1, 2, 3] (list), got {j!r} ({type(j).__name__})")
    else:
        print(f"  OK: got {j!r} ({type(j).__name__})")

    # Check 2: JSONB dict literal decoded as Python dict
    print("Check 2: SELECT '{\"a\": \"b\", \"n\": 42}'::jsonb")
    row = await pool.fetchrow("""SELECT '{"a": "b", "n": 42}'::jsonb AS j""")
    j = row["j"]
    if j != {"a": "b", "n": 42} or not isinstance(j, dict):
        failures.append(f"  Expected dict, got {j!r} ({type(j).__name__})")
    else:
        print(f"  OK: got {j!r} ({type(j).__name__})")

    # Check 3: Round-trip a Python dict through a temp table
    print("Check 3: round-trip Python dict through a TEMP TABLE")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "CREATE TEMP TABLE _codec_test (id INT, data JSONB) ON COMMIT DROP"
            )
            payload = {
                "checkout_id": "co-test",
                "items": [{"eid": "3001", "qty": 4}, {"eid": "3002", "qty": 1}],
                "total_cents": 1234,
            }
            await conn.execute(
                "INSERT INTO _codec_test (id, data) VALUES ($1, $2)",
                1,
                payload,
            )
            row = await conn.fetchrow(
                "SELECT data FROM _codec_test WHERE id = $1", 1
            )
            roundtripped = row["data"]
            if roundtripped != payload or not isinstance(roundtripped, dict):
                failures.append(
                    f"  Round-trip mismatch:\n"
                    f"    sent: {payload!r}\n"
                    f"    got:  {roundtripped!r} ({type(roundtripped).__name__})"
                )
            else:
                print("  OK: dict survived INSERT + SELECT without manual json.dumps/loads")

    await close_pool()

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f)
        return 1

    print("\nAll JSONB codec checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
