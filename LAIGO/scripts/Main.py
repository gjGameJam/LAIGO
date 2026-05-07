# run with: uvicorn main:app --host 0.0.0.0 --port 8000

import multiprocessing as mp
mp.set_start_method("spawn", force=True)

from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import uuid
import os
import shutil
import time
import threading
import queue
import json
import traceback
import logging
import sys
from PIL import Image
from .picToMosiac import pic_to_mosaic, MosaicType
from .Util import load_project_env
import gc

# -----------------------------
# LOGGING SETUP
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("laigo")

# -----------------------------
# ENV SETUP
# -----------------------------
if os.getenv("RENDER") is None:
    load_project_env()

INPUT_DIR = Path(os.getenv("INPUT_DIR", "./inputs")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", 600))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", 300))
# Keep single-worker behavior for now; queueing controls waiting jobs.
MAX_WORKERS = 1
MAX_QUEUE_SIZE = 20
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK", 16))
upload_mbs = int(os.getenv("MAX_UPLOAD_SIZE_MB", 250))
MAX_UPLOAD_SIZE = upload_mbs * 1024 * 1024

# Fail loudly on startup if directories can't be created or written to
try:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    log.critical(f"Failed to create required directories: {e}")
    raise

for _dir, _name in [(INPUT_DIR, "INPUT_DIR"), (OUTPUT_DIR, "OUTPUT_DIR")]:
    _probe = _dir / ".write_probe"
    try:
        _probe.write_text("ok")
        _probe.unlink()
    except Exception as e:
        log.critical(f"{_name} ({_dir}) is not writable: {e}")
        raise RuntimeError(f"{_name} is not writable") from e

log.info(f"INPUT_DIR:        {INPUT_DIR}")
log.info(f"OUTPUT_DIR:       {OUTPUT_DIR}")
log.info(f"MAX_WORKERS:      {MAX_WORKERS}")
log.info(f"MAX_QUEUE_SIZE:   {MAX_QUEUE_SIZE}")
log.info(f"JOB_TTL_SECONDS:  {JOB_TTL_SECONDS}")
log.info(f"CLEANUP_INTERVAL: {CLEANUP_INTERVAL}")
log.info(f"MAX_UPLOAD_MB:    {upload_mbs}")
log.info(f"STUDS_PER_BLOCK:  {STUDS_PER_BLOCK}")


# -----------------------------
# FASTAPI LIFECYCLE
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting LAIGO API...")
    try:
        # max_tasks_per_child=1: worker exits and is respawned after every job,
        # releasing all memory (numpy, skimage, PIL, cv2, mediapipe, palette globals)
        # back to the OS. Requires Python 3.12+.
        # Progress is tracked via small files on disk; no Manager is needed.
        app.state.executor = ProcessPoolExecutor(
            max_workers=MAX_WORKERS,
            max_tasks_per_child=1
        )
        app.state.jobs = {}
        app.state.progress = {}  # job_id -> Path to progress file
        app.state.jobs_lock = threading.Lock()
        app.state.progress_lock = threading.Lock()
        app.state.queue_order = []  # job ids in queued order, for queue UI
        app.state.queue_lock = threading.Lock()
        app.state.job_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        app.state.scheduler_shutdown = threading.Event()
        app.state.scheduler_cv = threading.Condition()
        app.state.active_jobs = 0
        log.info(f"ProcessPoolExecutor started with {MAX_WORKERS} worker(s), max_tasks_per_child=1")
    except Exception as e:
        log.critical(f"Failed to initialise executor: {e}", exc_info=True)
        raise

    try:
        scheduler_thread = threading.Thread(target=scheduler_loop, args=(app,), daemon=True)
        scheduler_thread.start()
        app.state.scheduler_thread = scheduler_thread
        log.info("Scheduler thread started")
    except Exception as e:
        log.critical(f"Failed to start scheduler thread: {e}", exc_info=True)
        raise

    try:
        cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
        cleanup_thread.start()
        log.info("Cleanup thread started")
    except Exception as e:
        log.critical(f"Failed to start cleanup thread: {e}", exc_info=True)
        raise

    log.info("LAIGO API ready")
    yield

    log.info("Shutting down LAIGO API...")
    try:
        app.state.scheduler_shutdown.set()
        with app.state.scheduler_cv:
            app.state.scheduler_cv.notify_all()

        scheduler_thread = getattr(app.state, "scheduler_thread", None)
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=5)

        app.state.executor.shutdown()
        log.info("Executor shut down cleanly")
    except Exception as e:
        log.error(f"Error during shutdown: {e}", exc_info=True)


app = FastAPI(lifespan=lifespan)

try:
    app.mount("/artifacts", StaticFiles(directory=OUTPUT_DIR), name="artifacts")
except Exception as e:
    log.critical(f"Failed to mount /artifacts static files: {e}", exc_info=True)
    raise

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://laigo-frontend.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# GLOBAL EXCEPTION HANDLER
# Catches any unhandled exception in a route so it never fails silently
# -----------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error(
        f"Unhandled exception on {request.method} {request.url}: "
        f"{type(exc).__name__}: {exc}",
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"}
    )


# -----------------------------
# SHARED HELPERS
# -----------------------------
def _job_snapshot(job: dict | None) -> dict | None:
    if not job:
        return None
    return {k: v for k, v in job.items() if k not in ("future", "progress_file")}


def _queue_position(app: FastAPI, job_id: str) -> tuple[int | None, int]:
    with app.state.queue_lock:
        try:
            idx = app.state.queue_order.index(job_id)
            return idx + 1, len(app.state.queue_order)
        except ValueError:
            return None, len(app.state.queue_order)


def _remove_from_queue_order(app: FastAPI, job_id: str) -> None:
    with app.state.queue_lock:
        try:
            app.state.queue_order.remove(job_id)
        except ValueError:
            pass


def _write_error_manifest(job_root: Path, job_id: str, settings: dict,
                           error: str, tb: str) -> None:
    """Write failure details to disk so get_job can serve them after the job
    is evicted from app.state.jobs. Accepts pre-captured traceback string so
    this can safely be called outside an except block."""
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
    Runs in a separate spawned process (max_tasks_per_child=1 so it exits
    after this returns, freeing all memory at the OS level).
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


# -----------------------------
# SCHEDULER HELPERS
# -----------------------------
def _mark_submission_failed(app: FastAPI, item: dict, error: str, tb: str) -> None:
    """Handles failures before the worker process starts.

    This cleans up the uploaded image, progress file, and any scratch output,
    then marks the job as failed in memory and writes an error manifest.
    """
    job_id = item["job_id"]
    image_path = Path(item["image_path"])
    progress_path = Path(item["progress_path"])
    settings = item["settings"]
    job_root = OUTPUT_DIR / job_id

    shutil.rmtree(job_root / "workspace", ignore_errors=True)
    shutil.rmtree(job_root, ignore_errors=True)

    try:
        image_path.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        progress_path.unlink(missing_ok=True)
    except Exception:
        pass

    _write_error_manifest(job_root, job_id, settings, error, tb)

    with app.state.progress_lock:
        app.state.progress.pop(job_id, None)

    _remove_from_queue_order(app, job_id)

    with app.state.jobs_lock:
        job = app.state.jobs.get(job_id)
        if job is not None:
            job.update({
                "status": "failed",
                "progress": 0,
                "error": error,
                "traceback": tb,
                "finished_at": time.time(),
            })
            job.pop("future", None)

    log.error(f"Job {job_id} failed before execution: {error}")


def _job_done_callback(app: FastAPI, job_id: str, future) -> None:
    """Finalizes a completed worker future in the main process.

    This is the canonical place where running -> complete/failed is finalized.
    The get_job route also has a safety-net resolution path.
    """
    try:
        result = future.result()
        if not isinstance(result, dict):
            result = {
                "status": "failed",
                "progress": 0,
                "error": f"Worker returned unexpected result type: {type(result).__name__}",
                "traceback": "",
                "finished_at": time.time(),
            }
    except Exception as e:
        tb = traceback.format_exc()
        result = {
            "status": "failed",
            "progress": 0,
            "error": f"Worker raised unexpectedly: {e}",
            "traceback": tb,
            "finished_at": time.time(),
        }

    with app.state.jobs_lock:
        job = app.state.jobs.get(job_id)
        if job is not None:
            job.update(result)
            job["finished_at"] = result.get("finished_at", time.time())
            job.pop("future", None)

    with app.state.progress_lock:
        app.state.progress.pop(job_id, None)

    with app.state.scheduler_cv:
        app.state.active_jobs = max(0, app.state.active_jobs - 1)
        app.state.scheduler_cv.notify_all()


def scheduler_loop(app: FastAPI):
    """Dispatches queued jobs into the process pool, bounded by MAX_WORKERS.

    The queue is explicit and separate from the executor so the executor does
    not become an unbounded hidden queue.
    """
    slog = logging.getLogger("laigo.scheduler")

    while True:
        if app.state.scheduler_shutdown.is_set():
            break

        with app.state.scheduler_cv:
            while not app.state.scheduler_shutdown.is_set() and app.state.active_jobs >= MAX_WORKERS:
                app.state.scheduler_cv.wait(timeout=0.5)

        if app.state.scheduler_shutdown.is_set():
            break

        try:
            item = app.state.job_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        job_id = item["job_id"]
        _remove_from_queue_order(app, job_id)

        if app.state.scheduler_shutdown.is_set():
            _mark_submission_failed(
                app,
                item,
                "Server is shutting down",
                "Scheduler stopped before job dispatch"
            )
            continue

        with app.state.jobs_lock:
            job = app.state.jobs.get(job_id)

        if job is None:
            _mark_submission_failed(
                app,
                item,
                "Job record missing before dispatch",
                "Job state was absent when scheduler tried to dispatch"
            )
            continue

        with app.state.scheduler_cv:
            while not app.state.scheduler_shutdown.is_set() and app.state.active_jobs >= MAX_WORKERS:
                app.state.scheduler_cv.wait(timeout=0.5)
            if app.state.scheduler_shutdown.is_set():
                _mark_submission_failed(
                    app,
                    item,
                    "Server is shutting down",
                    "Scheduler stopped before job dispatch"
                )
                continue
            app.state.active_jobs += 1

        try:
            future = app.state.executor.submit(
                run_job,
                job_id,
                item["image_path"],
                item["settings"],
                str(OUTPUT_DIR),
                STUDS_PER_BLOCK,
                item["progress_path"],
            )
        except Exception as e:
            tb = traceback.format_exc()
            slog.error(f"Failed to submit job {job_id} to executor: {e}", exc_info=True)
            with app.state.scheduler_cv:
                app.state.active_jobs = max(0, app.state.active_jobs - 1)
                app.state.scheduler_cv.notify_all()
            _mark_submission_failed(app, item, f"Failed to dispatch job: {e}", tb)
            continue

        with app.state.jobs_lock:
            job = app.state.jobs.get(job_id)
            if job is not None:
                job["status"] = "running"
                job["started_at"] = time.time()
                job["future"] = future

        future.add_done_callback(lambda fut, jid=job_id: _job_done_callback(app, jid, fut))
        with app.state.scheduler_cv:
            active_jobs = app.state.active_jobs
        slog.info(f"Dispatched job {job_id} to executor | active_jobs={active_jobs}")


# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health")
async def health():
    return {"status": "running"}


@app.get("/")
async def root():
    return {"status": "running", "message": "LAIGO API online. Use /health or /docs for info."}


@app.get("/queue")
async def queue_status():
    with app.state.jobs_lock:
        jobs = list(app.state.jobs.values())
        counts = {
            "queued": sum(1 for j in jobs if j.get("status") == "queued"),
            "running": sum(1 for j in jobs if j.get("status") == "running"),
            "complete": sum(1 for j in jobs if j.get("status") == "complete"),
            "failed": sum(1 for j in jobs if j.get("status") == "failed"),
        }

    with app.state.scheduler_cv:
        active_jobs = app.state.active_jobs

    with app.state.queue_lock:
        queued_job_ids = list(app.state.queue_order)

    return {
        "queued_jobs": len(queued_job_ids),
        "queued_job_ids": queued_job_ids,
        "max_queue_size": MAX_QUEUE_SIZE,
        "active_jobs": active_jobs,
        "max_workers": MAX_WORKERS,
        "known_jobs": len(jobs),
        "counts": counts,
    }


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    mosaic_block_width: int = Form(...),
    mosaic_type: str = Form(...),
    background_color_percent: float = Form(100),
    to_frame: bool = Form(True),
):
    if app.state.scheduler_shutdown.is_set():
        raise HTTPException(status_code=503, detail="Server is shutting down")

    job_id = str(uuid.uuid4())
    log.info(
        f"Job {job_id} received | filename={file.filename} "
        f"width={mosaic_block_width} type={mosaic_type} "
        f"bg_pct={background_color_percent} frame={to_frame}"
    )

    input_file = INPUT_DIR / f"{job_id}.upload"

    size = 0
    try:
        with open(input_file, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    f.close()
                    input_file.unlink(missing_ok=True)
                    await file.close()
                    log.warning(f"Job {job_id} rejected — file too large ({size} bytes)")
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Job {job_id} failed during file upload: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    await file.close()
    log.info(f"Job {job_id} file saved | size={size} bytes")

    try:
        with Image.open(input_file) as img:
            img.verify()
    except Exception as e:
        log.warning(f"Job {job_id} rejected — invalid image: {type(e).__name__}: {e}")
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file")

    settings = {
        "mosaic_block_width": mosaic_block_width,
        "mosaic_type": mosaic_type,
        "background_color_percent": background_color_percent,
        "to_frame": to_frame,
    }

    progress_file = INPUT_DIR / f"{job_id}.progress"
    try:
        progress_file.write_text("0")
    except Exception as e:
        log.error(f"Job {job_id} failed to create progress file: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to initialise job")

    with app.state.progress_lock:
        app.state.progress[job_id] = progress_file

    # Create the job record before enqueueing so the scheduler always has state.
    with app.state.jobs_lock:
        app.state.jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "created_at": time.time(),
            "queued_at": time.time(),
            "settings": settings,
            "progress_file": str(progress_file),
        }

    queue_item = {
        "job_id": job_id,
        "image_path": str(input_file),
        "progress_path": str(progress_file),
        "settings": settings,
    }

    with app.state.queue_lock:
        app.state.queue_order.append(job_id)

    try:
        app.state.job_queue.put_nowait(queue_item)
    except queue.Full:
        log.warning(f"Queue full — rejecting job {job_id}")
        input_file.unlink(missing_ok=True)
        progress_file.unlink(missing_ok=True)
        _remove_from_queue_order(app, job_id)
        with app.state.progress_lock:
            app.state.progress.pop(job_id, None)
        with app.state.jobs_lock:
            app.state.jobs.pop(job_id, None)
        raise HTTPException(status_code=429, detail="Queue full. Try again once space opens.") #if over max queue size and new job is requested
    except Exception as e:
        log.error(f"Job {job_id} failed to queue: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        progress_file.unlink(missing_ok=True)
        _remove_from_queue_order(app, job_id)
        with app.state.progress_lock:
            app.state.progress.pop(job_id, None)
        with app.state.jobs_lock:
            app.state.jobs.pop(job_id, None)
        raise HTTPException(status_code=500, detail=f"Failed to queue job: {e}")

    log.info(f"Job {job_id} queued successfully")
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    with app.state.jobs_lock:
        job = app.state.jobs.get(job_id)
        future = job.get("future") if job else None
        status = job.get("status") if job else None
        snapshot = _job_snapshot(job)

    if snapshot is None:
        error_manifest = OUTPUT_DIR / job_id / "manifest_failed.json"
        if error_manifest.exists():
            log.info(f"Job {job_id} served from error manifest on disk")
            try:
                with open(error_manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data
            except Exception as e:
                log.error(f"Job {job_id} failed to read error manifest: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Failed to read error manifest")

        log.warning(f"Job {job_id} not found in memory or on disk")
        raise HTTPException(status_code=404, detail="Job not found")

    progress_val = snapshot.get("progress", 0)
    with app.state.progress_lock:
        progress_file = app.state.progress.get(job_id)

    # Read progress from file while queued/running.
    if status in ("queued", "running") and progress_file and os.path.exists(progress_file):
        try:
            progress_val = float(Path(progress_file).read_text().strip())
            with app.state.jobs_lock:
                live_job = app.state.jobs.get(job_id)
                if live_job is not None:
                    live_job["progress"] = progress_val
        except Exception:
            progress_val = snapshot.get("progress", 0)

    # Safety-net finalization if the worker future is done but callback has not
    # yet updated the job state.
    if status == "running" and future is not None and future.done():
        try:
            result = future.result()
            if not isinstance(result, dict):
                result = {
                    "status": "failed",
                    "progress": 0,
                    "error": f"Worker returned unexpected result type: {type(result).__name__}",
                    "traceback": "",
                    "finished_at": time.time(),
                }
        except Exception as e:
            log.error(f"Job {job_id} future raised unexpectedly: {e}", exc_info=True)
            result = {
                "status": "failed",
                "progress": 0,
                "error": f"Worker raised unexpectedly: {e}",
                "traceback": traceback.format_exc(),
                "finished_at": time.time(),
            }

        with app.state.jobs_lock:
            live_job = app.state.jobs.get(job_id)
            if live_job is not None:
                live_job.update(result)
                live_job["finished_at"] = result.get("finished_at", time.time())
                live_job.pop("future", None)
                snapshot = _job_snapshot(live_job)

        with app.state.progress_lock:
            app.state.progress.pop(job_id, None)

        progress_val = 100 if result.get("status") == "complete" else 0

        if result.get("status") == "complete":
            log.info(f"Job {job_id} resolved as complete")
        else:
            log.warning(f"Job {job_id} resolved as failed | error={result.get('error')}")

    queue_position, queue_length = (None, 0)
    if status == "queued":
        queue_position, queue_length = _queue_position(app, job_id)

    response = {
        **snapshot,
        "progress": 100 if snapshot.get("status") == "complete" else 0 if snapshot.get("status") == "failed" else progress_val,
    }
    if status == "queued":
        response["queue_position"] = queue_position
        response["queue_length"] = queue_length

    return response


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    artifact = OUTPUT_DIR / job_id / "artifact.zip"

    if not artifact.exists():
        log.warning(f"Download requested for job {job_id} but artifact not found")
        raise HTTPException(status_code=404, detail="Artifact not found")

    log.info(f"Serving artifact for job {job_id}")
    return FileResponse(
        artifact,
        filename=f"mosaic_{job_id}.zip",
        media_type="application/zip",
    )


# -----------------------------
# CLEANUP THREAD
# -----------------------------
def cleanup_loop(app: FastAPI):
    clog = logging.getLogger("laigo.cleanup")
    while True:
        try:
            now = time.time()
            with app.state.jobs_lock:
                jobs_snapshot = list(app.state.jobs.items())

            for job_id, job in jobs_snapshot:
                if job.get("status") in ("complete", "failed"):
                    age = now - job.get("finished_at", now)
                    if age > JOB_TTL_SECONDS:
                        clog.info(f"TTL expired for job {job_id} (age={int(age)}s) — cleaning up")
                        try:
                            shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
                        except Exception as e:
                            clog.error(
                                f"Failed to remove output dir for job {job_id}: {e}",
                                exc_info=True
                            )

                        with app.state.jobs_lock:
                            current = app.state.jobs.get(job_id)
                            if current and current.get("status") in ("complete", "failed"):
                                current.pop("future", None)
                                app.state.jobs.pop(job_id, None)

                        with app.state.progress_lock:
                            progress_file = app.state.progress.pop(job_id, None)
                        if progress_file:
                            try:
                                Path(progress_file).unlink(missing_ok=True)
                            except Exception:
                                pass

                        _remove_from_queue_order(app, job_id)
                        clog.info(f"Job {job_id} cleaned up")
        except Exception:
            clog.error("Unexpected error in cleanup loop", exc_info=True)

        time.sleep(CLEANUP_INTERVAL)
