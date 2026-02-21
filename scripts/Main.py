# run command: uvicorn main:app

import multiprocessing as mp
mp.set_start_method("spawn", force=True)  # REQUIRED for Windows-safe multiprocessing

from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from picToMosiac import pic_to_mosaic, MosaicType
import uuid
import os
import shutil
import time
import threading
import json
from pathlib import Path
import traceback


# Directory where staged input images must live
INPUT_DIR = "./inputs"
os.makedirs(INPUT_DIR, exist_ok=True)

# Directory where final artifacts live
OUTPUT_DIR = "./outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# Job retention configuration
JOB_TTL_SECONDS = 3600
CLEANUP_INTERVAL = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Executor lives ONLY inside lifespan (prevents recursive spawn)
    app.state.executor = ProcessPoolExecutor(max_workers=2)

    # In-memory job table (owned by API process only)
    app.state.jobs = {}

    cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
    cleanup_thread.start()

    yield

    app.state.executor.shutdown()


app = FastAPI(lifespan=lifespan)


class MosaicSettings(BaseModel):
    mosaic_block_width: int = Field(1, ge=1, alias="mosaic_block_width")
    mosaic_type: MosaicType
    background_color_percent: float = Field(100, ge=1, le=100)
    to_frame: bool = True

    model_config = ConfigDict(populate_by_name=True)


class GenerateRequest(BaseModel):
    image_path: Path
    settings: MosaicSettings


def validate_input_path(path: str) -> str:
    root = Path(INPUT_DIR).resolve(strict=True)
    real = Path(path).resolve(strict=True)

    if os.path.commonpath([str(real), str(root)]) != str(root):
        raise ValueError("Image must be inside INPUT_DIR")

    return str(real)


# -----------------------------
# Worker Function (runs in subprocess)
# -----------------------------
def run_job(job_id: str, request_dict: dict) -> dict:
    """
    Pure function:
    Takes input → produces zip → returns metadata.
    No shared state mutation.
    """

    workspace = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(workspace, exist_ok=True)

    try:
        settings = request_dict["settings"]

        # Rehydrate types lost during JSON serialization
        image_path = Path(request_dict["image_path"])
        width = int(settings["mosaic_block_width"]) * 16  # convert block count to stud count
        m_type = MosaicType(settings["mosaic_type"])
        background_color_percent = float(settings["background_color_percent"])
        frame = bool(settings["to_frame"])
        

        result_dir = pic_to_mosaic(
            image_path,
            width,
            m_type,
            background_color_percent,
            frame,
            output_dir=workspace,
            job_id=job_id,
        ) or workspace

        manifest = {
            "schema": "laigo.manifest.v1",
            "job_id": job_id,
            "created_at": time.time(),
            "settings": request_dict
        }

        with open(os.path.join(result_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        archive_base = os.path.join(OUTPUT_DIR, job_id)
        archive_path = shutil.make_archive(archive_base, "zip", result_dir)

        shutil.rmtree(result_dir, ignore_errors=True)

        return {
            "status": "complete",
            "file": os.path.basename(archive_path),
            "finished_at": time.time()
        }

    except Exception as e:
        # Clean up workspace even on failure
        shutil.rmtree(workspace, ignore_errors=True)

        # Build a detailed error object
        error_info = {
            "status": "failed",
            "error_type": type(e).__name__,                   # Exception class
            "error_message": str(e),                           # Exception message
            "traceback": traceback.format_exc(),              # Full stack trace
            "input_path": request_dict.get("image_path"),     # What file was being processed
            "settings": request_dict.get("settings"),         # Settings used
            "workspace": workspace,                            # Folder being used
            "finished_at": time.time()
        }

        return error_info


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
    payload["image_path"] = validated_path

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
        result = future.result()
        job.update(result)

    return {k: v for k, v in job.items() if k != "future"}


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    job = app.state.jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != "complete":
        raise HTTPException(status_code=409, detail="Job not finished")

    file_path = os.path.join(OUTPUT_DIR, job["file"])

    return FileResponse(file_path, filename=f"mosaic_{job_id}.zip", media_type="application/zip")


def cleanup_loop(app: FastAPI):
    while True:
        now = time.time()

        for job_id, job in list(app.state.jobs.items()):
            if job.get("status") in ("complete", "failed"):
                if now - job.get("finished_at", now) > JOB_TTL_SECONDS:
                    try:
                        os.remove(os.path.join(OUTPUT_DIR, job["file"]))
                    except Exception:
                        pass
                    app.state.jobs.pop(job_id, None)

        time.sleep(CLEANUP_INTERVAL)


#test schema:
# {
#   "image_path": "C:/Users/bgern/Desktop/AIEng/LAIGO/scripts/inputs/stella1.jpg",
#   "settings": {
#     "mosaic_block_width": 4,
#     "mosaic_type": "3d",
#     "background_color_percent": 100,
#     "to_frame": true
#   }
# }