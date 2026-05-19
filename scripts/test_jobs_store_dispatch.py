"""One-shot smoke test for jobs_store_dispatch (Phase D step 2 step 3).

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_jobs_store_dispatch

Exercises every dispatcher public entry against the JSON backend (no DB
needed). Confirms the dispatcher routes correctly under DB_BACKEND=json
and that import + symbol-presence work cleanly under DB_BACKEND=postgres
(without actually hitting Neon).
"""
import asyncio
import os
import sys


async def main() -> int:
    os.environ["DB_BACKEND"] = "json"

    from scripts import jobs_store_dispatch as jobs_store
    from scripts import jobs_store_json, jobs_store_pg

    jobs_store._reset_for_tests()

    # --- API surface parity (every dispatcher export resolves to a callable) ---
    for name in jobs_store.__all__:
        obj = getattr(jobs_store, name)
        assert callable(obj), f"{name} is not callable"
    print(f"OK: dispatcher exports {len(jobs_store.__all__)} callables")

    # --- Backend selection ---
    assert jobs_store._backend() is jobs_store_json
    print("OK: DB_BACKEND=json routes to jobs_store_json")

    os.environ["DB_BACKEND"] = "postgres"
    assert jobs_store._backend() is jobs_store_pg
    print("OK: DB_BACKEND=postgres routes to jobs_store_pg")

    os.environ["DB_BACKEND"] = "JSON"  # case-insensitive
    assert jobs_store._backend() is jobs_store_json
    print("OK: DB_BACKEND is case-insensitive (mixed-case 'JSON' -> json)")

    os.environ["DB_BACKEND"] = "json"

    # --- Full lifecycle through the dispatcher (JSON backend) ---
    await jobs_store.insert_queued(
        job_id="dispatch-1",
        mosaic_type="3d",
        width_blocks=15,
        background_pct=60,
        ttl_seconds=3600,
    )
    row = await jobs_store.get_job("dispatch-1")
    assert row is not None and row["status"] == "queued"
    print("OK: insert_queued + get_job through dispatcher")

    # Storage actually landed in the JSON backend (not PG)
    assert "dispatch-1" in jobs_store_json._jobs
    print("OK: write landed in jobs_store_json._jobs")

    # dequeue_next
    picked = await jobs_store.dequeue_next()
    assert picked is not None and picked["job_id"] == "dispatch-1"
    assert picked["status"] == "running"
    print("OK: dequeue_next through dispatcher")

    # counts
    assert await jobs_store.count_active() == 1
    assert await jobs_store.count_queued() == 0
    print("OK: count_active / count_queued through dispatcher")

    # progress
    await jobs_store.write_progress("dispatch-1", 75)
    row = await jobs_store.get_job("dispatch-1")
    assert row["progress_pct"] == 75
    print("OK: write_progress through dispatcher")

    # mark_complete
    await jobs_store.mark_complete("dispatch-1")
    row = await jobs_store.get_job("dispatch-1")
    assert row["status"] == "complete"
    print("OK: mark_complete through dispatcher")

    # cleanup + delete
    await jobs_store.delete("dispatch-1")
    assert await jobs_store.get_job("dispatch-1") is None
    print("OK: delete through dispatcher")

    # --- Event loop fan-out: set_event_loop touches BOTH backends ---
    jobs_store_pg.clear_event_loop()
    jobs_store_json.clear_event_loop()
    loop = asyncio.get_running_loop()
    jobs_store.set_event_loop(loop)
    assert jobs_store_pg._loop_ref is loop, "set_event_loop didn't fan out to PG backend"
    assert jobs_store_json._loop_ref is loop, "set_event_loop didn't fan out to JSON backend"
    print("OK: set_event_loop fans out to both backends")

    jobs_store.clear_event_loop()
    assert jobs_store_pg._loop_ref is None
    assert jobs_store_json._loop_ref is None
    print("OK: clear_event_loop fans out to both backends")

    # --- _reset_for_tests fan-out ---
    await jobs_store.insert_queued(
        job_id="reset-canary",
        mosaic_type="2d",
        width_blocks=10,
        background_pct=None,
        ttl_seconds=3600,
    )
    assert "reset-canary" in jobs_store_json._jobs
    jobs_store._reset_for_tests()
    assert jobs_store_json._jobs == {}
    print("OK: _reset_for_tests clears JSON backend")

    # --- Symbol presence under DB_BACKEND=postgres ---
    # (We can't actually hit Neon here — no DSN — but we can verify the
    # dispatcher resolves the right module without raising.)
    os.environ["DB_BACKEND"] = "postgres"
    backend = jobs_store._backend()
    assert backend is jobs_store_pg
    # The PG backend's functions exist; calling them would raise without a pool,
    # but the dispatcher doesn't need to test PG round-trips here (that's S8).
    assert hasattr(backend, "insert_queued")
    assert hasattr(backend, "dequeue_next")
    print("OK: DB_BACKEND=postgres routes to a backend with full API surface")

    os.environ["DB_BACKEND"] = "json"
    print()
    print("All dispatcher smoke tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
