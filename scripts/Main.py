# run command: uvicorn main:app

import multiprocessing as mp
mp.set_start_method("spawn", force=True)  # REQUIRED for Windows-safe multiprocessing

from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from picToMosiac import pic_to_mosaic, MosaicType
import uuid
import os
import shutil
import time
import threading
import json
from pathlib import Path
import traceback
from Util import load_project_env, log_debug, log_info, log_error
load_project_env() #also being done in pic to mosaic but doing it here ensures env vars are loaded for the API process as well

# -----------------------------
# ROOT STORAGE DIRECTORIES
# -----------------------------
INPUT_DIR = Path(os.getenv("INPUT_DIR"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR"))

INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Job retention configuration
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS"))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL"))
max_worker_number = int(os.getenv("MAX_WORKERS"))
max_mosaic_block_width = int(os.getenv("MAX_MOSAIC_BLOCK_WIDTH"))
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK"))

# -----------------------------
# FASTAPI LIFECYCLE
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Executor exists ONLY in API process
    app.state.executor = ProcessPoolExecutor(max_workers=max_worker_number)

    # In-memory job registry
    app.state.jobs = {}

    cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
    cleanup_thread.start()

    yield

    app.state.executor.shutdown()


app = FastAPI(lifespan=lifespan)

# Static exposure for already-built artifacts (future CDN replacement point)
# meaning access is possible like: http://<server_ip>:<port>/artifacts/<job_id>/artifact.zip
app.mount("/artifacts", StaticFiles(directory=OUTPUT_DIR), name="artifacts")


# -----------------------------
# REQUEST MODELS
# -----------------------------
class MosaicSettings(BaseModel):
    mosaic_block_width: int = Field(1, ge=1, le=max_mosaic_block_width, alias="mosaic_block_width")
    mosaic_type: MosaicType
    background_color_percent: float = Field(100, ge=1, le=100)
    to_frame: bool = True

    model_config = ConfigDict(populate_by_name=True)


class GenerateRequest(BaseModel):
    image_path: Path
    settings: MosaicSettings


# -----------------------------
# INPUT PATH VALIDATION
# -----------------------------
def validate_input_path(path: str) -> Path:
    """
    Ensures user cannot escape INPUT_DIR via path tricks.
    """
    root = INPUT_DIR.resolve(strict=True)
    real = Path(path).resolve(strict=True)

    if os.path.commonpath([str(real), str(root)]) != str(root):
        raise ValueError("Image must be inside INPUT_DIR")

    return real


# -----------------------------
# WORKER FUNCTION (SUBPROCESS)
# -----------------------------
def run_job(job_id: str, request_dict: dict) -> dict:
    """
    Fully isolated execution.
    No shared filesystem assumptions.
    """

    job_root = OUTPUT_DIR / job_id
    workspace = job_root / "workspace"     # build happens here
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        settings = request_dict["settings"]

        image_path = Path(request_dict["image_path"])
        width = int(settings["mosaic_block_width"]) * STUDS_PER_BLOCK
        m_type = MosaicType(settings["mosaic_type"])
        background_color_percent = float(settings["background_color_percent"])
        frame = bool(settings["to_frame"])

        # -----------------------------
        # YOUR ORIGINAL PIPELINE CALL (UNCHANGED)
        # -----------------------------
        result_dir = pic_to_mosaic(
            image_path,
            width,
            m_type,
            background_color_percent,
            frame,
            output_dir=workspace,   # <- critical isolation injection
            job_id=job_id,
        ) or workspace

        # -----------------------------
        # WRITE MANIFEST (job metadata)
        # -----------------------------
        manifest = {
            "schema": "laigo.manifest.v1",
            "job_id": job_id,
            "created_at": time.time(),
            "settings": request_dict
        }

        with open(result_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # -----------------------------
        # ZIP CREATED OUTSIDE WORKSPACE
        # -----------------------------
        archive_base = job_root / "artifact"
        archive_path = shutil.make_archive(str(archive_base), "zip", result_dir)

        # Remove only the build workspace (leave final zip)
        shutil.rmtree(workspace, ignore_errors=True)

        return {
            "status": "complete",
            "file": Path(archive_path).name,
            "artifact_path": str(Path(archive_path)),
            "finished_at": time.time()
        }

    except Exception as e:
        shutil.rmtree(job_root, ignore_errors=True)

        return {
            "status": "failed",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
            "finished_at": time.time()
        }


# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health")
async def health():
    return {"service": "laigo", "status": "running"}


@app.post("/generate")
async def generate(request: GenerateRequest):
    try:
        validated_path = validate_input_path(request.image_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = str(uuid.uuid4())

    payload = request.model_dump(mode="json")
    payload["image_path"] = str(validated_path)

    future = app.state.executor.submit(run_job, job_id, payload)

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

    future = job["future"]

    if future.done() and job["status"] == "running":
        job.update(future.result())

    return {k: v for k, v in job.items() if k != "future"}


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    job = app.state.jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != "complete":
        raise HTTPException(status_code=409, detail="Job not finished")

    return FileResponse(
        job["artifact_path"],
        filename=f"mosaic_{job_id}.zip",
        media_type="application/zip"
    )


# -----------------------------
# BACKGROUND CLEANUP
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


# {
#   "image_path": "C:/Users/bgern/Desktop/AIEng/LAIGO/scripts/inputs/stella1.jpg",
#   "settings": {
#     "mosaic_block_width": 4,
#     "mosaic_type": "3d",
#     "background_color_percent": 100,
#     "to_frame": true
#   }
# }