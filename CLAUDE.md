# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

LAIGO converts photos into LEGO mosaic building kits. Given an image, it:
1. Quantizes colors to the 43-color LEGO palette using CIEDE2000 delta-E with Floyd-Steinberg dithering
2. Produces a LEGO brick purchase order list (JSON, uploadable to lego.com Pick-a-Brick)
3. Generates step-by-step building instructions as a multi-page PDF

## Running locally

Activate the virtual environment and navigate into the scripts package before running anything:

```bash
.venv\Scripts\activate.bat   # Windows
cd LAIGO/scripts             # needed so relative imports resolve
```

**API server** (primary interface):
```bash
uvicorn Main:app --reload
# Swagger UI: http://127.0.0.1:8000/docs
```

**Standalone CLI** (for quick local testing):
```bash
python picToMosiac.py <width_blocks> <2d|3d> <background_pct> <True|False>
# Example: python picToMosiac.py 5 3d 50 True
```

**Color quantization demo** (standalone, not part of the pipeline):
```bash
python colorQuant.py <num_colors>
```

There are no automated tests. Manual testing is done via the Swagger UI at `/docs`.

## Configuration

All runtime knobs live in `.env` (committed — no secrets):

| Variable | Default | Effect |
|---|---|---|
| `MAX_WORKERS` | 2 (hardcoded to 1 in Main.py) | Parallel processing workers |
| `MAX_QUEUE_SIZE` | 20 | Max pending jobs before 429 |
| `MAX_MOSAIC_BLOCK_WIDTH` | 40 | Max blocks wide a mosaic can be |
| `STUD_WIDTH_OF_BLOCK` | 16 | Studs per baseplate block side |
| `JOB_TTL_SECONDS` | 3600 | Seconds before completed jobs are purged |
| `DEBUG` | True | Enables debug-level logging |

## Architecture

### Processing pipeline

`POST /generate` → upload saved to `inputs/` → job queued → worker process runs `pic_to_mosaic()` → outputs zipped to `outputs/{job_id}/artifact.zip` → poll `GET /jobs/{job_id}` → download via `GET /jobs/{job_id}/download`

### Module layout (`LAIGO/scripts/`)

| File | Role |
|---|---|
| `Main.py` | FastAPI app, job lifecycle, scheduler/cleanup threads |
| `picToMosiac.py` | Core pipeline: color mapping, dithering, background separation |
| `MosiacToOrder.py` | Generates brick purchase JSONs (splits >999-qty items across multiple files) |
| `MosiacToInstruction.py` | Sequences instruction PNG steps → PDF |
| `VisualMaker.py` | Draws isometric LEGO stud visuals for each instruction step |
| `Util.py` | LEGO palette (43 RGB colors → element IDs), logging wrappers, JSON serialization |
| `logger.py` | Rotating file logger (`laigo.log`, max 25 MB) |
| `colorQuant.py` | Standalone KMeans color quantization demo (not used by the pipeline) |

### Concurrency model

- `ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)` — single worker, respawned after every job to release numpy/mediapipe/PIL memory back to the OS
- A Python `queue.Queue(maxsize=20)` decouples HTTP intake from the executor; a scheduler thread drains it
- Progress is tracked by writing a percentage to a small `.progress` file in `inputs/` rather than a multiprocessing Manager
- A cleanup thread (`CLEANUP_INTERVAL` seconds) evicts finished jobs older than `JOB_TTL_SECONDS` and deletes their output dirs

### Mosaic types

- **2D**: flat single-layer mosaic; lightness adjusted, dithered to palette
- **3D**: MediaPipe selfie segmentation separates foreground/background; each layer is dithered independently; background colors are simplified to at most `background_color_percent`% of their unique color count via `simplify_background_lego()`

### LEGO palette

Defined in `Util.py` as `LEGO_PALETTE_RGB_DICT` — 43 entries mapping `(R, G, B)` → LEGO element ID. Loaded once at module import into `LEGO_PALETTE_RGB` (NumPy array) and `PALETTE_LAB` (pre-converted to CIE Lab). Color matching uses `skimage.color.deltaE_ciede2000`. Adding or changing palette colors only requires editing that dict.

### Output structure (per job)

```
outputs/{job_id}/
  workspace/              # temp; deleted after success
    OrderLists/
      order_list.json     # may have order_list_1.json etc. if >999 of any piece
    Instructions/
      1.png … N.png       # deleted after PDF is written
      instructions.pdf
    manifest.json
  artifact.zip            # zip of workspace contents, served for download
  manifest_failed.json    # only present on failure
```

### CORS

Allowed origins are hardcoded in `Main.py`: `https://laigo-frontend.onrender.com` and `http://localhost:5173` (Vite default). Update both if the frontend URL changes.
