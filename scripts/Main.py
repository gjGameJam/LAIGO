from concurrent.futures import ThreadPoolExecutor
from picToMosiac import pic_to_mosiac
from fastapi import FastAPI
from pydantic import BaseModel, Field
import uuid
from enum import Enum


app = FastAPI()
executor = ThreadPoolExecutor(max_workers=2)
jobs = {}


class MosaicType(str, Enum):
    TWO_D = "2d"
    THREE_D = "3d"


class MosaicSettings(BaseModel):
    mosiac_block_width: int = Field(..., ge=1)
    mosaic_type: MosaicType
    background_color_percent: float = Field(0, ge=1, le=100)
    to_frame: bool = True


class GenerateRequest(BaseModel):
    image_path: str
    settings: MosaicSettings


def run_job(job_id, request: GenerateRequest):
    jobs[job_id]["status"] = "running"
    try:
        output = pic_to_mosiac(
            request.image_path,
            request.settings.mosiac_block_width,
            request.settings.mosaic_type,
            request.settings.background_color_percent,
            request.settings.to_frame
        )
        jobs[job_id]["status"] = "complete"
        jobs[job_id]["output"] = output
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


@app.get("/health")
def health():
    return {"service": "laigo", "status": "running"}


@app.post("/generate")
def generate(request: GenerateRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "pending", "output": None}
    executor.submit(run_job, job_id, request.param1, request.param2)
    return {"job_id": job_id}