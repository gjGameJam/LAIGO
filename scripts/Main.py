from concurrent.futures import ProcessPoolExecutor  # For CPU-bound parallelism in separate processes
from contextlib import asynccontextmanager  # To define FastAPI startup/shutdown behavior
from multiprocessing import Manager  # For process-safe shared state
from fastapi import FastAPI, HTTPException  # Web framework and exception handling
from fastapi.responses import FileResponse  # Streams files to clients safely
from pydantic import BaseModel, Field  # For typed request models
from enum import Enum  # For constrained string choices
from picToMosiac import pic_to_mosiac  # Your CPU-heavy mosaic function
import uuid  # To generate globally unique job IDs
import os  # Filesystem operations
import shutil  # For moving files between directories
import time  # For timestamps in TTL logic
import threading  # To run background cleanup worker


# Directory where all generated mosaic files will be stored
OUTPUT_DIR = "./generated"
os.makedirs(OUTPUT_DIR, exist_ok=True)  # Ensure directory exists


# -----------------------------
# Job retention configuration
# -----------------------------
JOB_TTL_SECONDS = 3600  # Jobs and files live for 1 hour
CLEANUP_INTERVAL = 300  # Sweep expired jobs every 5 minutes


# -----------------------------
# FastAPI app with lifecycle
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.executor = ProcessPoolExecutor(max_workers=2)  # Start process pool for CPU-heavy work

    # Start cleanup thread in background to remove old jobs/files
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()

    yield  # App runs while this yield is in effect

    app.state.executor.shutdown()  # Cleanly shut down pool on program exit


app = FastAPI(lifespan=lifespan)  # Attach lifespan manager to program


# -----------------------------
# Shared process-safe state
# -----------------------------
manager = Manager()  # Multiprocessing manager to safely share dict between processes
jobs = manager.dict()  # Job tracking dictionary shared across processes


# -----------------------------
# Enumerations
# -----------------------------
class MosaicType(str, Enum):
    TWO_D = "2d"  # 2D mosaic
    THREE_D = "3d"  # 3D mosaic


# -----------------------------
# Nested settings model
# -----------------------------
class MosaicSettings(BaseModel):
    mosiac_block_width: int = Field(..., ge=1)  # Block width ≥1
    mosaic_type: MosaicType  # Must be "2d" or "3d"
    background_color_percent: float = Field(0, ge=1, le=100)  # % of background color considered
    to_frame: bool = True  # Whether to add frame around mosaic


# -----------------------------
# Job submission request model
# -----------------------------
class GenerateRequest(BaseModel):
    image_path: str  # Server-side path to source image
    settings: MosaicSettings  # Nested mosaic configuration


# -----------------------------
# Job runner (runs in separate process)
# -----------------------------
def run_job(job_id: str, request_dict: dict):
    created_at = jobs[job_id]["created_at"]  # Preserve original creation timestamp
    jobs[job_id] = {"status": "running", "created_at": created_at}  # Mark job as running

    try:
        settings = request_dict["settings"]  # Extract settings dict

        # Run mosaic generation (CPU-heavy)
        temp_output_path = pic_to_mosiac(
            request_dict["image_path"],
            settings["mosiac_block_width"],
            settings["mosaic_type"],
            settings["background_color_percent"],
            settings["to_frame"]
        )

        # Assign internal filename using job_id (never expose real paths)
        internal_name = f"{job_id}.dat"
        final_path = os.path.join(OUTPUT_DIR, internal_name)

        # Move file to controlled output directory
        shutil.move(temp_output_path, final_path)

        # Update job status with internal file name and preserved timestamp
        jobs[job_id] = {
            "status": "complete",
            "file": internal_name,
            "created_at": created_at
        }

    except Exception as e:
        # Mark job failed and preserve timestamp
        jobs[job_id] = {
            "status": "failed",
            "error": str(e),
            "created_at": created_at
        }


# -----------------------------
# Health endpoint
# -----------------------------
@app.get("/health")
async def health():
    return {"service": "laigo", "status": "running"}  # Simple liveness check


# -----------------------------
# Submit a new mosaic job
# -----------------------------
@app.post("/generate")
async def generate(request: GenerateRequest):
    job_id = str(uuid.uuid4())  # Generate unique job ID

    # Store initial job state with timestamp
    jobs[job_id] = {"status": "pending", "created_at": time.time()}

    # Submit job to process pool (non-blocking)
    app.state.executor.submit(run_job, job_id, request.model_dump())

    return {"job_id": job_id}  # Return immediately to client


# -----------------------------
# Check job status
# -----------------------------
@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)  # Retrieve job from shared dict

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")  # Invalid job_id

    return {"status": job["status"]}  # Only return status to client


# -----------------------------
# Download finished mosaic
# -----------------------------
@app.get("/jobs/{job_id}/download")
async def download_job(job_id: str):
    job = jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != "complete":
        raise HTTPException(status_code=409, detail="Job not finished")

    internal_name = job["file"]  # Get internal filename
    file_path = os.path.join(OUTPUT_DIR, internal_name)

    # Verify file exists and is inside controlled directory
    if not os.path.isfile(file_path) or not os.path.abspath(file_path).startswith(os.path.abspath(OUTPUT_DIR)):
        raise HTTPException(status_code=500, detail="Output file missing")

    # Stream file to client safely with user-friendly name
    return FileResponse(
        file_path,
        filename=f"mosaic_{job_id}.png",
        media_type="application/octet-stream"
    )


# -----------------------------
# Background cleanup worker
# -----------------------------
def cleanup_loop():
    while True:
        now = time.time()
        expired = []

        # Identify jobs older than TTL
        for job_id, job in list(jobs.items()):
            if now - job["created_at"] > JOB_TTL_SECONDS:
                expired.append((job_id, job))

        # Remove expired jobs and delete their files
        for job_id, job in expired:
            if job.get("file"):
                file_path = os.path.join(OUTPUT_DIR, job["file"])
                if os.path.exists(file_path):
                    os.remove(file_path)  # Delete output file

            del jobs[job_id]  # Remove job metadata from memory

        time.sleep(CLEANUP_INTERVAL)  # Wait before next sweep
