"""One-shot smoke test for jobs_store_json (Phase D step 2 step 2).

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_jobs_store_json

Exercises every public API path. No DB required — pure in-memory.
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta


async def main() -> int:
    from scripts import jobs_store_json as js
    js._reset_for_tests()

    # --- insert_queued ---
    await js.insert_queued(
        job_id="job-1",
        mosaic_type="3d",
        width_blocks=20,
        background_pct=50,
        ttl_seconds=3600,
    )
    print("OK: insert_queued")

    try:
        await js.insert_queued(
            job_id="job-1",
            mosaic_type="2d",
            width_blocks=10,
            background_pct=None,
            ttl_seconds=3600,
        )
        raise AssertionError("expected ValueError on duplicate insert")
    except ValueError as e:
        print(f"OK: duplicate insert raises ValueError ({e})")

    # --- get_job shape & types ---
    row = await js.get_job("job-1")
    expected_keys = {
        "job_id", "status", "mosaic_type", "width_blocks", "background_pct",
        "dither", "upload_filename", "progress_pct", "error_message",
        "queued_at", "started_at", "completed_at", "ttl_expires_at",
        "customer_email", "customer_ip", "user_agent",
    }
    assert set(row.keys()) == expected_keys, f"key mismatch: {set(row.keys()) ^ expected_keys}"
    assert row["status"] == "queued"
    assert row["progress_pct"] == 0
    assert row["started_at"] is None
    assert row["completed_at"] is None
    assert isinstance(row["queued_at"], datetime)
    assert row["queued_at"].tzinfo is not None, "datetime must be tz-aware"
    assert isinstance(row["ttl_expires_at"], datetime)
    assert row["dither"] is True
    print("OK: get_job returns _JOB_COLS shape with tz-aware datetimes")

    # Returned dict is a copy
    row["status"] = "running"
    fresh = await js.get_job("job-1")
    assert fresh["status"] == "queued", f"mutation leaked! got {fresh['status']}"
    print("OK: get_job returns a copy (mutation isolation)")

    assert await js.get_job("nonexistent") is None
    print("OK: get_job returns None for unknown id")

    # --- count_queued / count_active ---
    assert await js.count_queued() == 1
    assert await js.count_active() == 0
    print("OK: count_queued=1 count_active=0")

    # --- mark_running ---
    assert await js.mark_running("job-1") is True
    row = await js.get_job("job-1")
    assert row["status"] == "running"
    assert row["started_at"] is not None
    print("OK: mark_running, started_at set")

    assert await js.mark_running("job-1") is False
    print("OK: mark_running re-fire returns False (status guard)")

    assert await js.mark_running("nonexistent") is False
    print("OK: mark_running unknown id returns False")

    # --- write_progress (clamp + int) ---
    await js.write_progress("job-1", 42.7)
    row = await js.get_job("job-1")
    assert row["progress_pct"] == 42
    await js.write_progress("job-1", 150)
    row = await js.get_job("job-1")
    assert row["progress_pct"] == 100
    await js.write_progress("job-1", -5)
    row = await js.get_job("job-1")
    assert row["progress_pct"] == 0
    print("OK: write_progress clamps to [0,100] and ints")

    # --- mark_complete ---
    await js.mark_complete("job-1")
    row = await js.get_job("job-1")
    assert row["status"] == "complete"
    assert row["completed_at"] is not None
    assert row["progress_pct"] == 100
    assert row["error_message"] is None
    print("OK: mark_complete")

    # write_progress on terminal row is no-op
    prev_pct = row["progress_pct"]
    await js.write_progress("job-1", 50)
    row = await js.get_job("job-1")
    assert row["progress_pct"] == prev_pct
    print("OK: write_progress no-op on terminal row")

    # --- mark_failed + COALESCE backfill (B41) ---
    await js.insert_queued(
        job_id="job-fast-fail",
        mosaic_type="2d",
        width_blocks=10,
        background_pct=None,
        ttl_seconds=3600,
    )
    await js.mark_failed("job-fast-fail", "Worker crashed before mark_running")
    row = await js.get_job("job-fast-fail")
    assert row["status"] == "failed"
    assert row["started_at"] is not None, "COALESCE backfill broken"
    assert row["completed_at"] is not None
    assert "crashed" in row["error_message"]
    print("OK: mark_failed backfills started_at (B41 contract)")

    huge = "x" * 5000
    await js.mark_failed("job-fast-fail", huge)
    row = await js.get_job("job-fast-fail")
    assert len(row["error_message"]) == 4096
    print("OK: error_message truncated to 4 KiB")

    # --- mark_timed_out ---
    await js.insert_queued(
        job_id="job-stuck",
        mosaic_type="3d",
        width_blocks=30,
        background_pct=80,
        ttl_seconds=3600,
    )
    await js.mark_timed_out("job-stuck", "exceeded 1800s")
    row = await js.get_job("job-stuck")
    assert row["status"] == "timed_out", f"got status={row['status']!r}"
    print("OK: mark_timed_out uses 'timed_out' (not 'failed')")

    # --- list_queued / list_running (FIFO) ---
    js._reset_for_tests()
    for jid in ["a", "b", "c"]:
        await js.insert_queued(
            job_id=jid, mosaic_type="2d", width_blocks=10,
            background_pct=None, ttl_seconds=3600,
        )
        await asyncio.sleep(0.001)
    await js.mark_running("b")
    queued = await js.list_queued()
    running = await js.list_running()
    assert [r["job_id"] for r in queued] == ["a", "c"]
    assert [r["job_id"] for r in running] == ["b"]
    assert queued[0]["queued_at"] < queued[1]["queued_at"]
    print("OK: list_queued (sorted by queued_at), list_running")

    # --- dequeue_next ---
    js._reset_for_tests()
    for jid in ["x", "y", "z"]:
        await js.insert_queued(
            job_id=jid, mosaic_type="2d", width_blocks=10,
            background_pct=None, ttl_seconds=3600,
        )
        await asyncio.sleep(0.001)
    picked = await js.dequeue_next()
    assert picked["job_id"] == "x"
    assert picked["status"] == "running"
    assert picked["started_at"] is not None
    stored = await js.get_job("x")
    assert stored["status"] == "running"
    picked2 = await js.dequeue_next()
    assert picked2["job_id"] == "y"
    picked3 = await js.dequeue_next()
    assert picked3["job_id"] == "z"
    assert await js.dequeue_next() is None
    print("OK: dequeue_next FIFO, returns None when empty")

    # --- cleanup_expired ---
    js._reset_for_tests()
    await js.insert_queued(
        job_id="ancient", mosaic_type="2d", width_blocks=10,
        background_pct=None, ttl_seconds=1,
    )
    await js.mark_running("ancient")
    await js.mark_complete("ancient")
    js._jobs["ancient"]["ttl_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=10)
    await js.insert_queued(
        job_id="fresh", mosaic_type="2d", width_blocks=10,
        background_pct=None, ttl_seconds=3600,
    )
    await js.mark_complete("fresh")
    deleted = await js.cleanup_expired()
    assert deleted == ["ancient"], f"expected ['ancient'], got {deleted}"
    assert await js.get_job("ancient") is None
    assert await js.get_job("fresh") is not None
    print("OK: cleanup_expired removed only expired terminal jobs")

    # Non-terminal rows past TTL are NOT deleted
    await js.insert_queued(
        job_id="stalled-queued", mosaic_type="2d", width_blocks=10,
        background_pct=None, ttl_seconds=1,
    )
    js._jobs["stalled-queued"]["ttl_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=10)
    deleted = await js.cleanup_expired()
    assert "stalled-queued" not in deleted
    assert await js.get_job("stalled-queued") is not None
    print("OK: cleanup_expired ignores non-terminal jobs (even if past TTL)")

    # --- delete ---
    await js.delete("fresh")
    assert await js.get_job("fresh") is None
    await js.delete("fresh")
    await js.delete("never-existed")
    print("OK: delete (idempotent)")

    print()
    print("All JSON backend smoke tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
