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
from fastapi.middleware.cors import CORSMiddleware
from .Util import load_project_env

# Only load .env when running locally
if os.getenv("RENDER") is None:
    load_project_env()

# -----------------------------
# ENV CONFIG (API PROCESS ONLY)
# -----------------------------
INPUT_DIR = Path(os.getenv("INPUT_DIR", "./inputs")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", 3600))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", 300))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", mp.cpu_count()))
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK", 16))
upload_mbs = int(os.getenv("MAX_UPLOAD_SIZE_MB", 250))
MAX_UPLOAD_SIZE = upload_mbs * 1024 * 1024

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------
# FASTAPI LIFECYCLE
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = mp.Manager()
    app.state.executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)
    app.state.jobs = {}
    app.state.progress = manager.dict()  # shared across processes

    cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
    cleanup_thread.start()

    yield

    app.state.executor.shutdown()
    manager.shutdown()


app = FastAPI(lifespan=lifespan)

app.mount("/artifacts", StaticFiles(directory=OUTPUT_DIR), name="artifacts")

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
# WORKER FUNCTION (PURE)
# -----------------------------
def run_job(job_id: str,
            image_path: str,
            settings: dict,
            output_root: str,
            studs_per_block: int,
            progress: object) -> dict:
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
            progress_callback=lambda pct: progress.__setitem__(job_id, pct),
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

        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass

        progress[job_id] = 100

        return {
            "status": "complete",
            "finished_at": time.time()
        }

    except Exception as e:
        shutil.rmtree(job_root, ignore_errors=True)
        try:
            Path(image_path).unlink(missing_ok=True)
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
    input_file = INPUT_DIR / f"{job_id}.upload"

    size = 0
    with open(input_file, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_SIZE:
                f.close()
                input_file.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File too large")
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())

    try:
        with Image.open(input_file) as img:
            img.load()
    except UnidentifiedImageError:
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file")

    settings = {
        "mosaic_block_width": mosaic_block_width,
        "mosaic_type": mosaic_type,
        "background_color_percent": background_color_percent,
        "to_frame": to_frame
    }

    # Initialise progress to 0 before submitting so the first poll has a value
    app.state.progress[job_id] = 0

    future = app.state.executor.submit(
        run_job,
        job_id,
        str(input_file),
        settings,
        str(OUTPUT_DIR),
        STUDS_PER_BLOCK,
        app.state.progress,   # shared Manager dict
    )

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

    return {
        **{k: v for k, v in job.items() if k != "future"},
        "progress": app.state.progress.get(job_id, 0),
    }


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    artifact = OUTPUT_DIR / job_id / "artifact.zip"

    if not artifact.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(
        artifact,
        filename=f"mosaic_{job_id}.zip",
        media_type="application/zip"
    )


# -----------------------------
# CLEANUP THREAD
# -----------------------------
def cleanup_loop(app: FastAPI):
    while True:
        try:
            now = time.time()
            for job_id, job in list(app.state.jobs.items()):
                if job.get("status") in ("complete", "failed"):
                    if now - job.get("finished_at", now) > JOB_TTL_SECONDS:
                        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
                        app.state.jobs.pop(job_id, None)
                        app.state.progress.pop(job_id, None)  # clean up progress entry too
        except Exception:
            traceback.print_exc()

        time.sleep(CLEANUP_INTERVAL)