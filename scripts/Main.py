# main.py
# Run with: uvicorn main:app --host 0.0.0.0 --port 8000

import multiprocessing as mp
mp.set_start_method("spawn", force=True)  # Windows-safe

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
import gc
from PIL import Image, UnidentifiedImageError

from .picToMosiac import pic_to_mosaic, MosaicType
from .Util import load_project_env

# -----------------------------
# LOGGING
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

for _dir in [INPUT_DIR, OUTPUT_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)
    probe = _dir / ".write_probe"
    probe.write_text("ok")
    probe.unlink()

# -----------------------------
# FASTAPI LIFECYCLE
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting LAIGO API...")
    manager = mp.Manager()
    app.state.executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)
    app.state.jobs = {}
    app.state.progress = manager.dict()  # Windows-safe shared dict
    cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
    cleanup_thread.start()
    log.info("LAIGO API ready")
    yield
    log.info("Shutting down LAIGO API...")
    try:
        app.state.executor.shutdown(wait=True)
        manager.shutdown()
        log.info("Executor and manager shut down cleanly")
    except Exception as e:
        log.error(f"Error during shutdown: {e}", exc_info=True)

app = FastAPI(lifespan=lifespan)
app.mount("/artifacts", StaticFiles(directory=OUTPUT_DIR), name="artifacts")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def _write_error_manifest(job_root: Path, job_id: str, settings: dict, error: str, tb: str):
    try:
        manifest = {
            "status": "failed",
            "progress": 0,
            "job_id": job_id,
            "error": error,
            "traceback": tb,
            "finished_at": time.time(),
            "settings": settings,
        }
        (job_root / "manifest_failed.json").write_text(json.dumps(manifest, indent=2))
    except Exception as e:
        print(f"[ERROR] Could not write error manifest for job {job_id}: {e}", file=sys.stderr, flush=True)

def run_job(job_id: str, image_path: str, settings: dict, output_root: str, studs_per_block: int, progress_dict):
    """Worker function executed in a separate process."""
    import gc
    import shutil
    import time
    import traceback
    from pathlib import Path
    from PIL import Image

    job_root = Path(output_root) / job_id
    workspace = job_root / "workspace"
    last_update = 0

    def report(pct: int):
        """Throttle progress updates to once every 1s"""
        nonlocal last_update
        now = time.time()
        if now - last_update >= 1 or pct >= 100:
            try:
                progress_dict[job_id] = pct
            except Exception:
                pass
            last_update = now

    def fail(msg: str, tb_str: str):
        try:
            shutil.rmtree(workspace, ignore_errors=True)
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass
        report(0)
        _write_error_manifest(job_root, job_id, settings, msg, tb_str)
        gc.collect()
        return {"status": "failed", "progress": 0, "error": msg, "traceback": tb_str, "finished_at": time.time()}

    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        tb_str = traceback.format_exc()
        return fail(f"Could not create workspace: {e}", tb_str)

    # --- Parse settings ---
    try:
        width = int(settings["mosaic_block_width"]) * studs_per_block
        mosaic_type = MosaicType(settings["mosaic_type"])
        background_pct = float(settings["background_color_percent"])
        to_frame = bool(settings["to_frame"])
    except Exception as e:
        tb_str = traceback.format_exc()
        return fail(f"Invalid settings: {e}", tb_str)

    # --- Run mosaic generation ---
    try:
        pic_to_mosaic(
            Path(image_path),
            width,
            mosaic_type,
            background_pct,
            to_frame,
            output_dir=workspace,
            job_id=job_id,
            progress_callback=report,
        )
    except Exception as e:
        tb_str = traceback.format_exc()
        return fail(str(e), tb_str)

    # --- Write manifest ---
    try:
        manifest = {
            "schema": "laigo.manifest.v1",
            "job_id": job_id,
            "created_at": time.time(),
            "settings": settings,
        }
        (workspace / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except Exception:
        pass

    # --- Archive artifact ---
    try:
        archive_path = job_root / "artifact"
        shutil.make_archive(str(archive_path), "zip", workspace)
    except Exception as e:
        tb_str = traceback.format_exc()
        return fail(f"Archive creation failed: {e}", tb_str)

    # --- Cleanup ---
    shutil.rmtree(workspace, ignore_errors=True)
    try:
        Path(image_path).unlink(missing_ok=True)
    except Exception:
        pass

    # --- Final progress ---
    report(100)
    gc.collect()
    return {"status": "complete", "progress": 100, "finished_at": time.time()}

# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health")
async def health():
    return {"status": "running"}

@app.post("/generate")
async def generate(file: UploadFile = File(...),
                   mosaic_block_width: int = Form(...),
                   mosaic_type: str = Form(...),
                   background_color_percent: float = Form(100),
                   to_frame: bool = Form(True)):
    job_id = str(uuid.uuid4())
    input_file = INPUT_DIR / f"{job_id}.upload"

    size = 0
    try:
        with open(input_file, "wb") as f:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    input_file.unlink(missing_ok=True)
                    await file.close()
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except HTTPException:
        raise
    except Exception as e:
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
    await file.close()

    # Validate image
    try:
        with Image.open(input_file) as img:
            img.verify()
    except UnidentifiedImageError:
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file")

    settings = {
        "mosaic_block_width": mosaic_block_width,
        "mosaic_type": mosaic_type,
        "background_color_percent": background_color_percent,
        "to_frame": to_frame,
    }

    app.state.progress[job_id] = 0
    future = app.state.executor.submit(run_job, job_id, str(input_file), settings, str(OUTPUT_DIR), STUDS_PER_BLOCK, app.state.progress)
    app.state.jobs[job_id] = {"status": "running", "future": future, "created_at": time.time()}

    return {"job_id": job_id}

@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = app.state.jobs.get(job_id)
    if job and job.get("status") == "running" and job["future"].done():
        try:
            result = job["future"].result()
        except Exception as e:
            result = {"status": "failed", "progress": 0, "error": str(e), "traceback": traceback.format_exc(), "finished_at": time.time()}
        job.update(result)
        job.pop("future", None)

    if job:
        status = job["status"]
        progress_val = app.state.progress.get(job_id, 0)
        if status == "complete":
            progress_val = 100
        elif status == "failed":
            progress_val = 0
        return {**{k: v for k, v in job.items() if k != "future"}, "progress": progress_val}

    # Check disk manifest fallback
    error_manifest = OUTPUT_DIR / job_id / "manifest_failed.json"
    if error_manifest.exists():
        return json.loads(error_manifest.read_text())

    raise HTTPException(status_code=404, detail="Job not found")

@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    artifact = OUTPUT_DIR / job_id / "artifact.zip"
    if not artifact.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(artifact, filename=f"mosaic_{job_id}.zip", media_type="application/zip")

# -----------------------------
# CLEANUP THREAD
# -----------------------------
def cleanup_loop(app: FastAPI):
    clog = logging.getLogger("cleanup")
    while True:
        try:
            now = time.time()
            for job_id, job in list(app.state.jobs.items()):
                status = job.get("status")
                if status in ("complete", "failed"):
                    age = now - job.get("finished_at", now)
                    if age > JOB_TTL_SECONDS:
                        try:
                            shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
                        except Exception as e:
                            clog.error(f"Failed to remove output dir for job {job_id}: {e}", exc_info=True)
                        app.state.jobs.pop(job_id, None)
                        app.state.progress.pop(job_id, None)
        except Exception:
            clog.error("Unexpected error in cleanup loop", exc_info=True)
        time.sleep(CLEANUP_INTERVAL)