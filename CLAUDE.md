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
# Note: image path is hardcoded to ../images/stella1.jpg in __main__
```

**Color quantization demo** (standalone, not part of the pipeline):
```bash
python colorQuant.py <num_colors>
# Note: image path is hardcoded to ../images/labrador.jpg
```

There are no automated tests. Manual testing is done via the Swagger UI at `/docs`.

## Configuration

All runtime knobs live in `.env` (committed — no secrets):

| Variable | Default in code | Value in .env | Effect |
|---|---|---|---|
| `MAX_WORKERS` | — | 2 (ignored; hardcoded to 1 in `Main.py`) | Parallel processing workers |
| `MAX_QUEUE_SIZE` | — | — (hardcoded to 20 in `Main.py`, not read from env) | Max pending jobs before 429 |
| `MAX_MOSAIC_BLOCK_WIDTH` | 40 | 40 | Max blocks wide a mosaic can be |
| `STUD_WIDTH_OF_BLOCK` | 16 | 16 | Studs per baseplate block side |
| `JOB_TTL_SECONDS` | 600 | 3600 | Seconds before completed jobs are purged |
| `JOB_TIMEOUT_SECONDS` | 1800 | — (not set, uses code default) | Max seconds a running job may take before forced failure |
| `CLEANUP_INTERVAL` | 300 | — (not set, uses code default) | How often the cleanup thread runs (seconds) |
| `MAX_UPLOAD_SIZE_MB` | 250 | 250 | Max upload file size |
| `DEBUG` | False | True | Enables debug-level logging |
| `FRONTEND_ORIGIN` | — | set but **unused** | CORS origins are hardcoded in `Main.py`, not read from env |

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
| `logger.py` | Rotating file logger (`laigo.log`, max 25 MB, 1 backup) |
| `colorQuant.py` | Standalone KMeans color quantization demo (not used by the pipeline) |

### Concurrency model

- `ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)` — single worker, respawned after every job to release numpy/mediapipe/PIL memory back to the OS
- A Python `queue.Queue(maxsize=20)` decouples HTTP intake from the executor; a scheduler thread drains it
- Progress is tracked by writing a percentage to a small `.progress` file in `inputs/` rather than a multiprocessing Manager
- A cleanup thread (`CLEANUP_INTERVAL` seconds) evicts finished jobs older than `JOB_TTL_SECONDS` and deletes their output dirs

### Mosaic types

- **2D**: flat single-layer mosaic; lightness adjusted (+5 L*), dithered to palette
- **3D**: MediaPipe selfie segmentation separates foreground/background; each layer is dithered independently; background colors are simplified to at most `background_color_percent`% of their unique color count via `simplify_background_lego()`

### LEGO palette

Defined in `Util.py` as `LEGO_PALETTE_RGB_DICT` — 43 entries mapping `(R, G, B)` → LEGO element ID. Loaded once at module import into `LEGO_PALETTE_RGB` (NumPy array) and `PALETTE_LAB` (pre-converted to CIE Lab). Color matching uses `skimage.color.deltaE_ciede2000`. Adding or changing palette colors only requires editing that dict.

### Output structure (per job)

```
outputs/{job_id}/
  workspace/              # temp; deleted on success or failure
    OrderLists/
      order_list.json     # may have order_list_1.json etc. if >999 of any piece
    Instructions/
      1.png … N.png       # deleted after PDF is written
      instructions.pdf
    manifest.json         # written on success; ends up inside artifact.zip
  artifact.zip            # zip of workspace contents, served for download
  manifest_failed.json    # written on failure (outside workspace, persists)
```

### CORS

Allowed origins are hardcoded in `Main.py`: `https://laigo-frontend.onrender.com` and `http://localhost:5173` (Vite default). The `FRONTEND_ORIGIN` env var in `.env` is **not** read by the server — update the hardcoded list in `Main.py` if the frontend URL changes.

### Import note

`picToMosiac.py` appends `scripts/` to `sys.path` at module load time. This allows `Util.py` and `MosiacToOrder.py` to use bare (non-relative) imports (`from Util import ...`, `from logger import logger`) alongside the package-relative imports (`from .Util import ...`). Do not remove that `sys.path.append` call or change import order without verifying both styles still resolve.

## Known bugs

### `remove_background` channel swap (`picToMosiac.py:122`)
PIL gives an RGB numpy array but `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)` treats it as BGR, swapping R and B before passing to MediaPipe. Segmentation is shape-based so the mask is still usable, but the channel order fed to the model is wrong. Should be `cv2.COLOR_RGB2BGR` or just pass the PIL array directly since MediaPipe expects RGB.

### `background_color_percent` `k` miscalculation (`picToMosiac.py:265`)
```python
color_quant = max(1, int((background_color_percent/100) * len(np.unique(bg_idx[fg_mask_np==255]))))
```
`fg_mask_np==255` selects **foreground** pixels, so `k` is computed from background palette indices at foreground positions — the wrong region. The simplification loop itself uses the correct mask (`255-fg_mask_np`), so only background pixels are recolored, but the target color count `k` may be under- or over-estimated. Should use `fg_mask_np==0`.

### Two conflicting PNG sort strategies for the same file set
`_get_ordered_pngs` (inside `images_to_pdf`) sorts by `re.search(r'(\d+)', path.stem)` — first digit sequence. The call site in `GenerateInstructions` sorts the same directory with `int(p.stem.split("_")[-1]) if "_" in p.stem else 0`. These produce different orderings if filenames contain underscores or leading digits. One canonical sort function should be used everywhere.

### `pic_to_mosaic` returns `None` silently; caller depends on `or workspace` fallback (`Main.py:335`)
The function never returns a path. `run_job` handles this with `result_dir = pic_to_mosaic(...) or workspace`. If someone adds a return value to `pic_to_mosaic` with a falsy result the fallback silently activates.

### ~~No timeout on the worker process~~ — **fixed**
`_check_timed_out_jobs` runs each scheduler loop tick. Jobs running longer than `JOB_TIMEOUT_SECONDS` (default 1800s) are marked failed and `active_jobs` is decremented. `_job_done_callback` is guarded against double-finalizing via a "future popped" sentinel.

### `max_tasks_per_child=1` requires Python 3.12+ (unenforced, `Main.py:96`)
`ProcessPoolExecutor.__init__` did not accept `max_tasks_per_child` before Python 3.12. On earlier versions the server fails to start with a `TypeError`. This is not documented in `requirements.txt` or anywhere else.

---

## Performance issues

### ~~Instruction generation: disk used as inter-step shared memory~~ — **fixed**
`save_img_and_increment_step` now draws the step number on a `img.copy()` and saves that, leaving the in-memory canvas untouched. `generate_baseplate_setup` reuses the same `img` across its three steps (no disk read between steps 1 and 2) and returns `(step, img)` so the caller skips its own `get_img_and_draw(False)` disk read. `erase_step_number` and all its call sites removed as dead code.

### Double-draw of identical geometry per column step (`MosiacToInstruction.py:154–157`)
Each column step draws all 16 plates twice — once unhighlighted on `img`, once highlighted on `to_reuse`. The highlight is only a yellow outline. Fix: draw the column once on `img`, copy, then draw only the outline on the copy.

### Floyd-Steinberg dithering: pure Python per-pixel loop (`picToMosiac.py:97–111`)
At max mosaic size (~307,000 iterations), each iteration calls `deltaE_ciede2000` against 43 palette entries. The error propagation makes full vectorization non-trivial, but the inner `nearest_palette_index_lab` call could be batched and the loop ported to Cython/Numba for a 50–100× speedup.

### `simplify_background_lego`: pure Python per-pixel, but fully vectorizable (`picToMosiac.py:60–67`)
Every pixel is independent. Fix: compute the nearest top-color for each of the (at most 43) unique palette indices once, build a lookup array, then replace all values in `bg_idx` with a single NumPy indexed assignment. Reduces O(H×W) Python to O(43) + one vectorized op.

### UnsharpMask applied at full input resolution before resize (`picToMosiac.py:73`)
`img.filter(ImageFilter.UnsharpMask(...))` runs on the original photo (potentially megapixels) before `img.resize(studs_w, studs_h)` immediately discards most of that work. Resize first, then sharpen the small image.

### MediaPipe segmentation at full input resolution (`picToMosiac.py:119–131`)
The segmentation mask only needs to be as fine as the mosaic resolution (max 640×480). Downscaling to ~2× mosaic resolution before calling MediaPipe, then upscaling the mask, gives near-identical results with a fraction of the compute and memory.

### `adjust_lightness_lab` does two full-resolution RGB↔LAB round-trips for a +5 L* shift (`picToMosiac.py:135–165`)
Two full-resolution colorspace conversions just to clamp-add 5 to the L channel. This should be applied after the image is resized, not before.

### Worker process cold-start overhead (`Main.py:95–98`)
`max_tasks_per_child=1` respawns the worker after every job. Each startup imports numpy, PIL, scikit-image, mediapipe, and cv2 — roughly 3–5 seconds of import overhead per job. Consider raising to `max_tasks_per_child=3–5` once memory usage is better characterized, so imports are amortized over several jobs per worker lifetime.

---

## Antipatterns

### ~~Disk used as inter-step state~~ — **fixed**
`get_img_and_draw(False)` disk reads eliminated. Canvas flows in memory via the `(step, img)` return from `generate_baseplate_setup`.

### ~~Step number burned into pixel data, erased with a white rectangle~~ — **fixed**
`save_img_and_increment_step` now overlays the step number on a throw-away copy; the source `img` is never mutated. `erase_step_number` and all call sites removed.

### `step` counter threaded as a return value through every function
Every instruction function takes `step: int`, returns `step + N`, and the caller must capture it. Any refactor that adds or reorders steps silently produces wrong step numbers. A stateful counter object would make this contract explicit.

### `FRONTEND_ORIGIN` env var is set in `.env` but never read (`Main.py:159–166`)
CORS origins are hardcoded. The env var implies you can change the frontend URL via config, but you cannot. Either wire the env var into `Main.py` or remove it from `.env`.

### `log_info` used for full data structures at INFO level (`MosiacToInstruction.py:114,119`, `Util.py:139`)
`log_info(fg_colors)`, `log_info(bg_colors)`, and `log_info(order_dict)` emit full Python set/dict reprs at INFO level. In production this fills the rotating log with noise. These should be `log_debug`.

### Bare imports alongside relative imports (`MosiacToOrder.py:3`, `Util.py:10`)
`from Util import GetPaletteDict` and `from logger import logger` are bare imports that only resolve because `picToMosiac.py` appends `scripts/` to `sys.path` at module load time. If either module is ever imported without going through `picToMosiac` first, both fail with `ModuleNotFoundError`. All imports in the package should be relative.

### `queue.Queue` state is not persisted across server restarts
If the server restarts mid-queue (e.g., Render cold start), all queued and running jobs are lost with no notification to users. Users must discover this by polling and resubmit manually.
