# run with:
# uvicorn main:app --host 0.0.0.0 --port 8000

import multiprocessing as mp
mp.set_start_method("spawn", force=True)  # REQUIRED for Windows + Render

from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import os
import shutil
import time
import threading
import json
from pathlib import Path
import traceback
from PIL import Image, UnidentifiedImageError
from .picToMosiac import pic_to_mosaic, MosaicType
from .Util import load_project_env

# Load .env BEFORE anything else
load_project_env()

# -----------------------------
# ENV CONFIG (API PROCESS ONLY)
# -----------------------------
INPUT_DIR = Path(os.getenv("INPUT_DIR", "./inputs")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", 3600))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", 300))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 2))
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK", 16))
upload_mbs = int(os.getenv("MAX_UPLOAD_SIZE_MB", 250))
MAX_UPLOAD_SIZE = upload_mbs * 1024 * 1024 # convert MB to bytes

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------
# FASTAPI LIFECYCLE
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)
    app.state.jobs = {}

    cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
    cleanup_thread.start()

    yield

    app.state.executor.shutdown()


app = FastAPI(lifespan=lifespan)

# Static serving of built artifacts (CDN-ready later)
app.mount("/artifacts", StaticFiles(directory=OUTPUT_DIR), name="artifacts")


# -----------------------------
# WORKER FUNCTION (PURE)
# -----------------------------
def run_job(job_id: str,
            image_path: str,
            settings: dict,
            output_root: str,
            studs_per_block: int) -> dict:
    """
    This function must be PURE.
    No globals. Everything passed explicitly.
    """

    OUTPUT_DIR = Path(output_root)

    job_root = OUTPUT_DIR / job_id
    workspace = job_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        width = int(settings["mosaic_block_width"]) * studs_per_block
        mosaic_type = MosaicType(settings["mosaic_type"])
        background_pct = float(settings["background_color_percent"])
        to_frame = bool(settings["to_frame"])

        result_dir = pic_to_mosaic(
            Path(image_path),
            width,
            mosaic_type,
            background_pct,
            to_frame,
            output_dir=workspace,
            job_id=job_id,
        ) or workspace

        manifest = {
            "schema": "laigo.manifest.v1",
            "job_id": job_id,
            "created_at": time.time(),
            "settings": settings
        }

        with open(result_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        archive_base = job_root / "artifact"
        archive_path = shutil.make_archive(str(archive_base), "zip", result_dir)

        shutil.rmtree(workspace, ignore_errors=True)

        # Remove uploaded source image (no longer needed)
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass

        return {
            "status": "complete",
            "artifact_path": archive_path,
            "finished_at": time.time()
        }

    except Exception as e:
        shutil.rmtree(job_root, ignore_errors=True)
        try:
            Path(image_path).unlink(missing_ok=True) # Remove uploaded source image (no longer needed) even if job failed
        except Exception:
            pass
        return {
            "status": "failed",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "finished_at": time.time()
        }


# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health")
async def health():
    return {"status": "running"}


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    mosaic_block_width: int = Form(...),
    mosaic_type: str = Form(...),
    background_color_percent: float = Form(100),
    to_frame: bool = Form(True),
):
    """
    Accepts an uploaded image file, validates it, and starts a processing job.
    """
    # --- 1. Read content and check size ---
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {upload_mbs} MB."
        )

    # --- 2. Generate unique job ID ---
    job_id = str(uuid.uuid4())

    # --- 3. Save uploaded file safely ---
    input_file = INPUT_DIR / f"{job_id}_{file.filename}"
    with open(input_file, "wb") as f:
        f.write(contents)
        f.flush()
        os.fsync(f.fileno())  # ensure fully written

    # --- 4. Validate the image ---
    try:
        with Image.open(input_file) as img:
            img.verify()  # check that file is an actual image
    except UnidentifiedImageError:
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file")

    # --- 5. Prepare job settings ---
    settings = {
        "mosaic_block_width": mosaic_block_width,
        "mosaic_type": mosaic_type,
        "background_color_percent": background_color_percent,
        "to_frame": to_frame
    }

    # --- 6. Submit job to ProcessPoolExecutor ---
    future = app.state.executor.submit(
        run_job,
        job_id,
        str(input_file),
        settings,
        str(OUTPUT_DIR),
        STUDS_PER_BLOCK
    )

    # --- 7. Register job in in-memory registry ---
    app.state.jobs[job_id] = {
        "status": "running",
        "future": future,
        "created_at": time.time()
    }

    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = app.state.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["future"].done() and job["status"] == "running":
        job.update(job["future"].result())

    return {k: v for k, v in job.items() if k != "future"}


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    job = app.state.jobs.get(job_id)

    if not job or job["status"] != "complete":
        raise HTTPException(status_code=404, detail="Artifact not ready")

    return FileResponse(
        job["artifact_path"],
        filename=f"mosaic_{job_id}.zip",
        media_type="application/zip"
    )


# -----------------------------
# CLEANUP THREAD
# -----------------------------
def cleanup_loop(app: FastAPI):
    while True:
        now = time.time()

        for job_id, job in list(app.state.jobs.items()):
            if job.get("status") in ("complete", "failed"):
                if now - job.get("finished_at", now) > JOB_TTL_SECONDS:
                    shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
                    app.state.jobs.pop(job_id, None)

        time.sleep(CLEANUP_INTERVAL)