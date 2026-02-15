from concurrent.futures import ThreadPoolExecutor
from picToMosiac import generate_images
from fastapi import FastAPI
from pydantic import BaseModel
import uuid

app = FastAPI()

executor = ThreadPoolExecutor(max_workers=2)
jobs = {}

class GenerateRequest(BaseModel):
    param1: str
    param2: str

def run_job(job_id, p1, p2):
    jobs[job_id]["status"] = "running"
    try:
        output = generate_images(p1, p2)
        jobs[job_id]["status"] = "complete"
        jobs[job_id]["output"] = output
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)

@app.post("/generate")
def generate(request: GenerateRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "pending", "output": None}
    executor.submit(run_job, job_id, request.param1, request.param2)
    return {"job_id": job_id}
