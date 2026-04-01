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
MAX_WORKERS = int(os.getenv("MAX_WORKERS", mp.cpu_count()))
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
        manager = mp.Manager()
        app.state.executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)
        app.state.jobs = {}
        app.state.progress = manager.dict()
        log.info(f"ProcessPoolExecutor started with {MAX_WORKERS} worker(s)")
    except Exception as e:
        log.critical(f"Failed to initialise executor or manager: {e}", exc_info=True)
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
        app.state.executor.shutdown()
        manager.shutdown()
        log.info("Executor and manager shut down cleanly")
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
# WORKER HELPERS
# -----------------------------
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
            progress: object) -> dict:

    # Worker is a separate spawned process — needs its own logging config
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] worker: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    wlog = logging.getLogger(f"laigo.worker.{job_id[:8]}")
    wlog.info(f"Job {job_id} started | settings: {settings}")

    OUTPUT_DIR = Path(output_root)
    job_root = OUTPUT_DIR / job_id
    workspace = job_root / "workspace"

    last_update_time = 0

    def throttled_progress(pct):
        nonlocal last_update_time
        now = time.time()
        if now - last_update_time >= 2:
            try:
                progress[job_id] = pct
            except Exception as prog_err:
                wlog.warning(f"Job {job_id} could not update progress: {prog_err}")
            last_update_time = now

    def fail(msg: str, tb: str) -> dict:
        """Single failure path: cleans up workspace and input file, marks
        progress as -1, writes the error manifest, and returns the failure dict.
        Always called from inside an except block so tb should be pre-captured
        via traceback.format_exc() before any further exceptions can overwrite it."""
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass
        try:
            progress[job_id] = -1
        except Exception as prog_err:
            wlog.warning(f"Job {job_id} could not set progress to -1: {prog_err}")
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
            progress_callback=throttled_progress,
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

    progress[job_id] = 100
    del result_dir
    gc.collect()

    wlog.info(f"Job {job_id} complete")
    return {
        "status": "complete",
        "finished_at": time.time(),
    }


# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health")
async def health():
    return {"status": "running"}

@app.get("/")
async def root():
    return {"status": "running", "message": "LAIGO API online. Use /health or /docs for info."}

@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    mosaic_block_width: int = Form(...),
    mosaic_type: str = Form(...),
    background_color_percent: float = Form(100),
    to_frame: bool = Form(True),
):
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

    app.state.progress[job_id] = 0

    try:
        future = app.state.executor.submit(
            run_job,
            job_id,
            str(input_file),
            settings,
            str(OUTPUT_DIR),
            STUDS_PER_BLOCK,
            app.state.progress,
        )
    except Exception as e:
        log.error(f"Job {job_id} failed to submit to executor: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        app.state.progress.pop(job_id, None)
        raise HTTPException(status_code=500, detail=f"Failed to queue job: {e}")

    app.state.jobs[job_id] = {
        "status": "running",
        "future": future,
        "created_at": time.time(),
    }

    log.info(f"Job {job_id} queued successfully")
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = app.state.jobs.get(job_id)
    progress_val = app.state.progress.get(job_id, 0)

    if job and job.get("status") == "running" and job["future"].done():
        try:
            result = job["future"].result()
        except Exception as e:
            # run_job catches all exceptions internally so this should never
            # happen, but guard against it explicitly so it's never silent
            log.error(f"Job {job_id} future raised unexpectedly: {e}", exc_info=True)
            result = {
                "status": "failed",
                "progress": 0,
                "error": f"Worker raised unexpectedly: {e}",
                "traceback": traceback.format_exc(),
                "finished_at": time.time(),
            }

        job.update(result)
        del job["future"]
        progress_val = app.state.progress.get(job_id, progress_val)

        if result.get("status") == "complete":
            log.info(f"Job {job_id} resolved as complete")
        else:
            log.warning(f"Job {job_id} resolved as failed | error={result.get('error')}")

    if job:
        return {**{k: v for k, v in job.items() if k != "future"}, "progress": progress_val}

    # Fallback to error manifest on disk
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
            for job_id, job in list(app.state.jobs.items()):
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
                        job.pop("future", None)
                        app.state.jobs.pop(job_id, None)
                        try:
                            app.state.progress.pop(job_id, None)
                        except Exception as e:
                            clog.error(
                                f"Failed to remove progress entry for job {job_id}: {e}",
                                exc_info=True
                            )
                        clog.info(f"Job {job_id} cleaned up")
        except Exception:
            clog.error("Unexpected error in cleanup loop", exc_info=True)

        time.sleep(CLEANUP_INTERVAL)