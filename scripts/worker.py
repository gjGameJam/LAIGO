"""Worker-side mosaic job runner — executes in spawned subprocesses.

D-010: keep this module's import surface MINIMAL. ProcessPoolExecutor in spawn
mode re-imports the defining module of the submitted callable in every worker
subprocess to resolve the function reference. `run_job` previously lived in
`Main.py`, so every job spawn re-imported the entire FastAPI + checkout +
asyncpg tree (~3-5 s cold start; CPU hotspot #6). This module imports only
`picToMosiac` + stdlib.

NEVER import FastAPI, the checkout package, db, or jobs_store from here — doing
so re-introduces the cold-start cost this module exists to eliminate.
"""
import gc
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path

from .picToMosiac import pic_to_mosaic, MosaicType


def _write_error_manifest(job_root: Path, job_id: str, settings: dict,
                           error: str, tb: str) -> None:
    """Write failure details to disk so get_job can serve them after the job
    is evicted from the store. Accepts pre-captured traceback string so this
    can safely be called outside an except block."""
    try:
        error_info = {
            "status": "failed",
            "progress": 0,
            "job_id": job_id,
            "error": error,
            "traceback": tb,
            "finished_at": time.time(),
            "settings": settings,
        }
        error_path = job_root / "manifest_failed.json"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump(error_info, f, indent=2)
    except Exception as write_err:
        print(
            f"[ERROR] Could not write error manifest for job {job_id}: {write_err}",
            file=sys.stderr, flush=True
        )


# -----------------------------
# WORKER FUNCTION
# -----------------------------
def run_job(job_id: str,
            image_path: str,
            settings: dict,
            output_root: str,
            studs_per_block: int,
            progress_path: str) -> dict:
    """
    Runs in a spawned worker process. On Python 3.12+ the pool sets
    max_tasks_per_child=1 so the worker exits after this returns, freeing all
    memory at the OS level; on <=3.11 that kwarg is unavailable, so the worker
    persists across jobs — run_job is stateless per call, so this is safe, only
    OS-level memory reclaim is forgone (see D-030).
    Progress is written to a small file on disk rather than a Manager dict,
    since the Manager process has been eliminated.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] worker: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    wlog = logging.getLogger(f"laigo.worker.{job_id[:8]}")
    wlog.info(f"Job {job_id} started | settings: {settings}")

    OUTPUT_DIR_LOCAL = Path(output_root)
    job_root = OUTPUT_DIR_LOCAL / job_id
    workspace = job_root / "workspace"
    progress_file = Path(progress_path)
    last_update_time = 0

    def write_progress(pct):
        nonlocal last_update_time
        now = time.time()
        if now - last_update_time >= 2:
            try:
                progress_file.write_text(str(pct))
            except Exception as prog_err:
                wlog.warning(f"Job {job_id} could not write progress: {prog_err}")
            last_update_time = now

    def delete_progress():
        try:
            progress_file.unlink(missing_ok=True)
        except Exception:
            pass

    def fail(msg: str, tb: str) -> dict:
        """Single failure path: cleans up workspace, input file, and progress
        file, writes the error manifest, and returns the failure dict."""
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass
        delete_progress()
        _write_error_manifest(job_root, job_id, settings, msg, tb)
        gc.collect()
        return {
            "status": "failed",
            "progress": 0,
            "error": msg,
            "traceback": tb,
            "finished_at": time.time(),
        }

    # --- Create workspace ---
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} failed to create workspace {workspace}: {e}", exc_info=True)
        return fail(f"Could not create workspace: {e}", tb)

    # --- Parse settings ---
    try:
        width = int(settings["mosaic_block_width"]) * studs_per_block
        mosaic_type = MosaicType(settings["mosaic_type"])
        background_pct = float(settings["background_color_percent"])
        to_frame = bool(settings["to_frame"])
    except (KeyError, ValueError, TypeError) as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} has invalid settings: {e}", exc_info=True)
        return fail(f"Invalid job settings: {e}", tb)

    # --- Run mosaic generation ---
    try:
        wlog.info(f"Job {job_id} calling pic_to_mosaic | width={width} type={mosaic_type}")
        # NOTE: `or workspace` masks pic_to_mosaic's implicit None return (D-017,
        # fixed in Wave 7 — pic_to_mosaic will return output_dir and this falls
        # away). Preserved here so the worker-module move stays behavior-neutral.
        result_dir = pic_to_mosaic(
            Path(image_path),
            width,
            mosaic_type,
            background_pct,
            to_frame,
            output_dir=workspace,
            job_id=job_id,
            progress_callback=write_progress,
        ) or workspace
        wlog.info(f"Job {job_id} pic_to_mosaic complete | result_dir={result_dir}")
    except Exception as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} pic_to_mosaic raised: {type(e).__name__}: {e}", exc_info=True)
        return fail(str(e), tb)

    # --- Write success manifest (non-fatal if it fails) ---
    try:
        manifest = {
            "schema": "laigo.manifest.v1",
            "job_id": job_id,
            "created_at": time.time(),
            "settings": settings,
        }
        with open(result_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        wlog.info(f"Job {job_id} manifest written")
    except Exception as e:
        wlog.error(f"Job {job_id} failed to write manifest: {e}", exc_info=True)

    # --- Archive workspace ---
    try:
        archive_base = job_root / "artifact"
        wlog.info(f"Job {job_id} archiving to {archive_base}.zip")
        shutil.make_archive(str(archive_base), "zip", result_dir)
        wlog.info(f"Job {job_id} archive created")
    except Exception as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} failed to create archive: {e}", exc_info=True)
        return fail(f"Archive creation failed: {e}", tb)

    # Copy order_list.json to a stable location before workspace deletion so the
    # checkout optimizer can read it after the workspace is gone.
    _order_list_src = workspace / "OrderLists" / "order_list.json"
    if _order_list_src.exists():
        try:
            shutil.copy2(_order_list_src, job_root / "order_list.json")
        except Exception as e:
            wlog.warning(f"Job {job_id} could not copy order_list.json: {e}")

    # Copy preview.json (3D-preview payload for the frontend) to the stable
    # location before workspace deletion. Mirrors the order_list handoff above.
    _preview_src = workspace / "preview.json"
    if _preview_src.exists():
        try:
            shutil.copy2(_preview_src, job_root / "preview.json")
        except Exception as e:
            wlog.warning(f"Job {job_id} could not copy preview.json: {e}")

    # Copy stats.json (authoritative piece counts for GET /jobs/{id}/stats) to
    # the stable location before workspace deletion. Mirrors the handoffs above.
    _stats_src = workspace / "stats.json"
    if _stats_src.exists():
        try:
            shutil.copy2(_stats_src, job_root / "stats.json")
        except Exception as e:
            wlog.warning(f"Job {job_id} could not copy stats.json: {e}")

    # --- Success cleanup ---
    shutil.rmtree(workspace, ignore_errors=True)

    try:
        Path(image_path).unlink(missing_ok=True)
    except Exception as e:
        wlog.warning(f"Job {job_id} could not delete input file {image_path}: {e}")

    delete_progress()

    del result_dir
    gc.collect()

    wlog.info(f"Job {job_id} complete")
    return {
        "status": "complete",
        "finished_at": time.time(),
    }
