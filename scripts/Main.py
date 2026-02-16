from concurrent.futures import ProcessPoolExecutor  # Creates a pool of separate Python processes for CPU-bound work
from contextlib import asynccontextmanager  # Lets us define startup/shutdown behavior for the FastAPI app
from multiprocessing import Manager  # Provides shared state objects that are safe across multiple processes
from picToMosiac import pic_to_mosiac  # Your heavy image-processing function that generates the mosaic
from fastapi import FastAPI  # Web framework used to expose HTTP endpoints
from pydantic import BaseModel, Field  # Used for request validation and typed data models
import uuid  # Used to generate unique job IDs
from enum import Enum  # Used to constrain allowed values for mosaic type


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.executor = ProcessPoolExecutor(max_workers=2)  # Create a process pool when the API starts
    yield  # Control returns to FastAPI while the app runs
    app.state.executor.shutdown()  # Cleanly stop worker processes when the API shuts down


app = FastAPI(lifespan=lifespan)  # Attach the lifecycle manager so the executor is created/destroyed correctly


manager = Manager()  # Start a multiprocessing manager to coordinate shared memory safely
jobs = manager.dict()  # Shared dictionary where all processes can read/write job state


class MosaicType(str, Enum):
    TWO_D = "2d"  # Allowed value for 2D mosaic generation
    THREE_D = "3d"  # Allowed value for 3D mosaic generation


class MosaicSettings(BaseModel):
    mosiac_block_width: int = Field(..., ge=1)  # Required integer ≥ 1 controlling brick size
    mosaic_type: MosaicType  # Must be one of the enum values above
    background_color_percent: float = Field(0, ge=1, le=100)  # Percentage threshold, clamped between 1–100
    to_frame: bool = True  # Whether to add a frame around the mosaic (defaults to True)


class GenerateRequest(BaseModel):
    image_path: str  # Filesystem path to the source image
    settings: MosaicSettings  # Nested configuration object defined above


def run_job(job_id: str, request_dict: dict):
    jobs[job_id] = {"status": "running", "output": None}  # Mark job as running inside shared state

    try:
        settings = request_dict["settings"]  # Extract nested settings dictionary

        output = pic_to_mosiac(
            request_dict["image_path"],  # Path to the input image
            settings["mosiac_block_width"],  # Size of mosaic blocks
            settings["mosaic_type"],  # 2D vs 3D mode
            settings["background_color_percent"],  # Background filtering threshold
            settings["to_frame"]  # Whether to generate a frame
        )

        jobs[job_id] = {
            "status": "complete",  # Mark job successful
            "output": output  # Store whatever your generator returned (file path, data, etc.)
        }

    except Exception as e:
        jobs[job_id] = {
            "status": "failed",  # Mark job as failed if anything throws
            "error": str(e)  # Save error message for client inspection
        }


@app.get("/health")
async def health():
    return {"service": "laigo", "status": "running"}  # Simple liveness check endpoint


@app.post("/generate")
async def generate(request: GenerateRequest):
    job_id = str(uuid.uuid4())  # Generate a globally unique identifier for this job

    jobs[job_id] = {"status": "pending", "output": None}  # Initialize job record before dispatching work

    app.state.executor.submit(
        run_job,  # Function executed in another process
        job_id,  # Pass the job ID so the worker can update shared state
        request.model_dump()  # Convert validated Pydantic model → plain dict (safe to pickle across processes)
    )

    return {"job_id": job_id}  # Immediately return so the API stays non-blocking


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)  # Look up job in shared dictionary

    if not job:
        return {"status": "unknown"}  # Handle invalid or expired job IDs

    return job  # Return status/output/error so client can poll progress
