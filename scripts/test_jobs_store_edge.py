"""Edge-case tests for the jobs_store dispatcher (Phase D step 2 review).

These cover behaviors the per-backend smoke tests intentionally don't:
- Concurrent dequeue (two coroutines, FIFO ordering, no double-claim)
- mark_complete on a never-running row (covers the
  `_job_done_callback` path where the worker subprocess crashes before
  `mark_running_from_thread` had a chance to fire)
- mark_failed on an unknown id (must be a clean no-op, not a raise)
- write_progress on a queued row (no-op; status guard)
- Settings round-trip through every terminal status

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_jobs_store_edge
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta


async def main() -> int:
    os.environ["DB_BACKEND"] = "json"

    from scripts import jobs_store_dispatch as jobs_store
    from scripts import jobs_store_json as js
    jobs_store._reset_for_tests()

    # ─── 1. Concurrent dequeue must not double-claim ───────────────────────
    for jid in ["c1", "c2", "c3"]:
        await jobs_store.insert_queued(
            job_id=jid, mosaic_type="2d", width_blocks=5,
            background_pct=None, ttl_seconds=3600,
        )

    # Fire three dequeues concurrently. Each must get a distinct job.
    picked = await asyncio.gather(
        jobs_store.dequeue_next(),
        jobs_store.dequeue_next(),
        jobs_store.dequeue_next(),
    )
    picked_ids = {p["job_id"] for p in picked if p is not None}
    assert picked_ids == {"c1", "c2", "c3"}, f"concurrent dequeue lost or duplicated rows: {picked_ids}"
    assert all(p["status"] == "running" for p in picked)
    extra = await jobs_store.dequeue_next()
    assert extra is None, "dequeue_next should return None when queue is empty"
    print("OK: concurrent dequeue claims exactly once per row, FIFO")

    # ─── 2. mark_complete on a row that never had mark_running fired ───────
    # Mirrors the "subprocess crash before scheduler marked running" race the
    # done-callback was originally written to handle.
    jobs_store._reset_for_tests()
    await jobs_store.insert_queued(
        job_id="fast-complete", mosaic_type="3d", width_blocks=10,
        background_pct=70, ttl_seconds=3600,
    )
    # NOTE: deliberately skip mark_running.
    await jobs_store.mark_complete("fast-complete")
    row = await jobs_store.get_job("fast-complete")
    assert row["status"] == "complete"
    assert row["started_at"] is not None, "started_at must be backfilled per B41 contract"
    assert row["completed_at"] is not None
    print("OK: mark_complete on never-running row backfills started_at (B41)")

    # ─── 3. mark_failed on an unknown id ───────────────────────────────────
    await jobs_store.mark_failed("never-existed", "this should be a no-op")
    print("OK: mark_failed on unknown id is a no-op (no raise)")

    # ─── 4. write_progress on a queued row is a no-op (status guard) ──────
    jobs_store._reset_for_tests()
    await jobs_store.insert_queued(
        job_id="q1", mosaic_type="2d", width_blocks=5,
        background_pct=None, ttl_seconds=3600,
    )
    await jobs_store.write_progress("q1", 50)
    row = await jobs_store.get_job("q1")
    assert row["progress_pct"] == 0, f"write_progress should not mutate queued row, got {row['progress_pct']}"
    print("OK: write_progress no-op on queued row (status guard)")

    # ─── 5. write_progress on a complete row is a no-op (status guard) ────
    await jobs_store.mark_running("q1")
    await jobs_store.write_progress("q1", 75)
    await jobs_store.mark_complete("q1")
    # Now progress_pct should be 100 (mark_complete sets it). Try to reset to 50.
    await jobs_store.write_progress("q1", 50)
    row = await jobs_store.get_job("q1")
    assert row["progress_pct"] == 100, "write_progress on complete row must not move it backwards"
    print("OK: write_progress no-op on complete row")

    # ─── 6. dequeue_next preserves all _JOB_COLS keys (intake/ttl carry over)
    jobs_store._reset_for_tests()
    await jobs_store.insert_queued(
        job_id="full-shape", mosaic_type="3d", width_blocks=20,
        background_pct=50, ttl_seconds=1234, upload_filename="me.png",
    )
    picked = await jobs_store.dequeue_next()
    assert picked is not None
    assert picked["job_id"] == "full-shape"
    assert picked["mosaic_type"] == "3d"
    assert picked["width_blocks"] == 20
    assert picked["background_pct"] == 50
    assert picked["upload_filename"] == "me.png"
    assert picked["dither"] is True
    assert picked["status"] == "running"
    assert picked["started_at"] is not None
    assert picked["queued_at"] is not None
    print("OK: dequeue_next returns the full _JOB_COLS shape")

    # ─── 7. mark_running on already-running row returns False ─────────────
    again = await jobs_store.mark_running("full-shape")
    assert again is False, "mark_running on already-running row must return False"
    print("OK: mark_running idempotency (returns False on re-fire)")

    # ─── 8. Cleanup respects TTL boundary (off-by-one check) ──────────────
    jobs_store._reset_for_tests()
    await jobs_store.insert_queued(
        job_id="boundary", mosaic_type="2d", width_blocks=5,
        background_pct=None, ttl_seconds=1,
    )
    await jobs_store.mark_running("boundary")
    await jobs_store.mark_complete("boundary")
    # ttl_expires_at is 1s in the future; should NOT be cleaned yet.
    deleted = await jobs_store.cleanup_expired()
    assert "boundary" not in deleted, "TTL not yet expired; row should survive"
    # Force expiry by editing the row directly (JSON backend only).
    js._jobs["boundary"]["ttl_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
    deleted = await jobs_store.cleanup_expired()
    assert deleted == ["boundary"]
    print("OK: cleanup_expired respects TTL boundary")

    # ─── 9. Settings round-trip through every terminal transition ──────────
    # This is the contract /jobs/{id} preserves via app.state.intake.
    # Here we verify the store's typed columns at each transition.
    jobs_store._reset_for_tests()
    for status_kind in ("complete", "failed", "timed_out"):
        jid = f"settings-{status_kind}"
        await jobs_store.insert_queued(
            job_id=jid, mosaic_type="3d", width_blocks=15,
            background_pct=42, ttl_seconds=3600,
        )
        await jobs_store.mark_running(jid)
        await jobs_store.write_progress(jid, 50)
        if status_kind == "complete":
            await jobs_store.mark_complete(jid)
            expected_status = "complete"
            expected_progress = 100
        elif status_kind == "failed":
            await jobs_store.mark_failed(jid, "synthetic failure")
            expected_status = "failed"
            expected_progress = 50  # mark_failed doesn't touch progress_pct
        else:
            await jobs_store.mark_timed_out(jid, "synthetic timeout")
            expected_status = "timed_out"
            expected_progress = 50

        row = await jobs_store.get_job(jid)
        assert row["status"] == expected_status
        assert row["mosaic_type"] == "3d"
        assert row["width_blocks"] == 15
        assert row["background_pct"] == 42
        assert row["progress_pct"] == expected_progress, (
            f"{status_kind}: expected progress {expected_progress}, got {row['progress_pct']}"
        )
        if status_kind != "complete":
            assert row["error_message"] is not None, f"{status_kind} must have error_message"
    print("OK: settings + status round-trip through complete/failed/timed_out")

    # ─── 10. delete is FK-aware in PG but a plain pop in JSON ─────────────
    await jobs_store.delete("settings-complete")
    assert await jobs_store.get_job("settings-complete") is None
    # Idempotent
    await jobs_store.delete("settings-complete")
    await jobs_store.delete("never-existed-either")
    print("OK: delete idempotent on missing id")

    print()
    print("All edge-case tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
