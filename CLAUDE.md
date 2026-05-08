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

## Image quality / detail preservation

### ~~Initial resize uses `Image.NEAREST`, discarding most source detail~~ — **fixed** (`picToMosiac.py:88`)
`Image.NEAREST` samples a single pixel from the source block and discards the rest. For a 40-block mosaic from a 4000px-wide phone photo (6.25× downscale), each stud's color was determined by 1 out of every ~39 source pixels — ~97% of source color information thrown away before dithering. For smaller mosaics (10 blocks, 25× downscale) this worsened to 99.8% loss. Now uses `Image.LANCZOS`, which averages all source pixels in each stud's footprint with a weighted filter. The dithering now sees a representative color for each stud rather than a random sample.

### UnsharpMask tuned to match resize method (`picToMosiac.py:89`)
Previously `UnsharpMask(radius=1, percent=350, threshold=3)` ran at full input resolution before NEAREST resize — the sharpening was almost entirely discarded by the sampling. After the LANCZOS fix, the filter now runs on the downscaled image at 150% intensity (reduced from 350%), restoring perceptual crispness that LANCZOS softens without creating haloing artifacts that distort palette matching.

### `adjust_lightness_lab` runs at full input resolution (`picToMosiac.py:258–290`)
`adjust_lightness_lab` is called on the full-resolution image before it is passed to `image_to_lego_mosaic`, which immediately resizes it. The function does a full RGB→LAB→RGB round-trip on megapixel data just to add +5 to the L channel. Eliminating the separate call and applying the L* shift directly to the LAB array after the LANCZOS resize (already computed for dithering) would be cheaper and equivalent. Reverted from a previous attempt due to visual regression concerns — re-examine after LANCZOS change is validated.

### `remove_background` channel swap corrupts background colors in 3D mode (`picToMosiac.py:124`)
Documented in Known bugs above. PIL gives an RGB array; `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)` treats it as BGR, swapping R and B before the segmentation mask is produced. Segmentation is shape-based so the mask is usable, but the background image returned to the pipeline has its R and B channels swapped before palette matching — causing e.g. reds to match blue LEGO colors in the background layer.

---

## CPU performance

Current single-job CPU load on Render: **~70% of 1 vCPU**. Goal: ≤50% per job so two jobs can run concurrently. Running two workers requires Render **Standard tier (2 GB RAM)** minimum — each worker holds mediapipe + numpy + PIL + cv2 in memory (~400–550 MB RSS).

### Hotspot map

| Priority | Location | Operation | Why expensive | Status |
|----------|----------|-----------|---------------|--------|
| 1 | `picToMosiac.py:100–116` | Floyd-Steinberg dithering loop | Pure Python O(W×H), sequential by design | **open** |
| 2 | `picToMosiac.py:37–41` | `nearest_palette_index_lab` | Called per pixel; runs full `deltaE_ciede2000` against all 43 palette entries | **open** |
| 3 | `picToMosiac.py:258–290` | `adjust_lightness_lab` on full-res image | Full-res RGB→LAB→RGB round-trip before resize | **open** |
| 4 | `picToMosiac.py:123–134` | MediaPipe segmentation at full resolution | Model inference on megapixel image; mask only needs mosaic resolution | **open** |
| 5 | `MosiacToInstruction.py:137–160` | Double `draw_plate_column` per step | Column drawn twice per instruction step (unhighlighted + highlighted) | **open** |
| 6 | `Main.py:99` | `max_tasks_per_child=1` | Worker cold-starts after every job; 3–5s import overhead per job | **open** |
| 7 | `Main.py:52` | `MAX_WORKERS=1` | No concurrency; second job waits for first to complete | **open** |
| 8 | `MosiacToOrder.py:40–56` | Brick-count pixel loops | ~~O(W×H) Python pixel accessor~~ | **fixed** |
| 9 | `picToMosiac.py:115` | Rebuild `out_rgb` | ~~Python list comprehension over all pixels~~ | **fixed** |
| 10 | `picToMosiac.py:88–89` | UnsharpMask at full resolution | ~~Ran on megapixel image before resize~~ | **fixed** |

### Open items — detail and estimated impact

**#1 — Floyd-Steinberg dithering loop** (`picToMosiac.py:100–116`)
O(W×H) iterations. At max mosaic size (~409,600 pixels for a 40-block job), this is the dominant CPU cost. Error propagation makes full numpy vectorization non-trivial. Two approaches:
- **KDTree nearest-color** (`scipy.spatial.KDTree` over the 43 palette LAB entries): replaces `deltaE_ciede2000` (complex perceptual math in Python) with Euclidean LAB distance in C. No new dependency (scipy is already a transitive dep of scikit-image). Estimated 25–35% reduction in total job time. Trade-off: Euclidean LAB ≈ deltaE76, not CIEDE2000 — tested and reverted once due to visual concerns; worth re-testing on more images before dismissing.
- **Numba JIT** (`@numba.njit` on the whole loop + inlined distance math): eliminates Python interpreter overhead entirely. Estimated 50–60% reduction in total job time. Requires new dependency (~200 MB), JIT warm-up call at worker startup, and inlining the distance calculation. Only pursue if KDTree is insufficient.

**#2 — `adjust_lightness_lab` at full resolution** (`picToMosiac.py:258–290`)
Called on full-res images (e.g. 4000×3000) before passing to `image_to_lego_mosaic`, which immediately resizes. Fix: add `delta_L=0` parameter to `image_to_lego_mosaic`, apply `lab[..., 0] = np.clip(lab[..., 0] + delta_L, 0, 100)` immediately after `color.rgb2lab(rgb)`, and remove the separate call sites. Estimated 8–12% reduction in total job time. Risk: previous attempt caused visual regression; re-examine after LANCZOS change is validated.

**#3 — MediaPipe at full input resolution** (`picToMosiac.py:123–134`)
`SelfieSegmentation` runs on whatever size PIL image is passed in. The mask only needs to be as fine as the mosaic (e.g. 640×480 for a 40-block job). Fix: downscale the input to ~2× mosaic resolution before calling MediaPipe, then upscale the resulting mask back to full resolution before use. Estimated 10–20% reduction in total job time (MediaPipe inference cost scales with pixel count).

**#4 — Double `draw_plate_column` per instruction step** (`MosiacToInstruction.py:137–160`)
For each column, the pattern is: (1) draw column without highlight on `img`, (2) copy `img` to `to_reuse`, (3) draw same column again with highlight on `to_reuse`. The geometry is identical except for a yellow outline added by `draw_plate` when `highlight=True` (lines 943–955 in `VisualMaker.py`). Fix: draw the column once, copy, then apply only the highlight outline to the copy via a new `draw_highlight_column` helper. Estimated 12–18% reduction in total job time.

**#5 — Worker cold-start overhead** (`Main.py:99`)
`max_tasks_per_child=1` respawns the worker process after every job. Each startup imports numpy, PIL, scikit-image, mediapipe, and cv2 — roughly 3–5 seconds of pure overhead per job. Fix: raise to `max_tasks_per_child=3–5`. Memory stays bounded by `MAX_WORKERS × peak-per-worker`. Requires Python 3.12+ (already true).

**#6 — Single-worker concurrency** (`Main.py:52`)
`MAX_WORKERS=1` means jobs are serialized. To run two concurrent jobs, raise to `MAX_WORKERS=2`. Memory prerequisite: 2 workers × ~500 MB RSS + main process ~150 MB ≈ 1.15 GB peak. Render free/starter tiers (512 MB) will OOM. Requires **Render Standard tier (2 GB RAM)** or higher.

### CPU reduction estimates (cumulative)

| Changes applied | Estimated single-job CPU |
|-----------------|--------------------------|
| Baseline (today before fixes) | ~70% |
| + `adjust_lightness_lab` fix (item #2) | ~58–62% |
| + MediaPipe resolution fix (item #3) | ~46–52% |
| + Double draw_plate_column fix (item #4) | ~35–42% |
| + KDTree nearest-color (item #1 partial) | ~22–30% |
| + max_workers=2 + max_tasks_per_child=3 | two jobs at ~25–35% each |

### ~~Fixed CPU items~~

### ~~Instruction generation: disk used as inter-step shared memory~~ — **fixed**
`save_img_and_increment_step` now draws the step number on an `img.copy()` and saves that, leaving the in-memory canvas untouched. `generate_baseplate_setup` reuses the same `img` across its three steps and returns `(step, img)` so the caller skips the disk read. `erase_step_number` and all call sites removed.

### ~~`simplify_background_lego`: pure Python per-pixel~~ — **fixed**
Builds an `index_remap` array by running `deltaE_ciede2000` once per unique palette index (≤43 iterations). Applies the remap to the full image with a single NumPy indexed assignment; masked pixels are restored in one vectorized step.

### ~~Brick-count pixel loops in MosiacToOrder.py~~ — **fixed** (`MosiacToOrder.py:40–56`)
Background and foreground pixel loops replaced with `np.unique(axis=0, return_counts=True)` on numpy arrays. O(W×H) Python iterations reduced to a single vectorized call. The foreground path masks by alpha channel before counting (`fg_arr[:, :, 3] > 0`).

### ~~`out_rgb` rebuilt with a Python list comprehension~~ — **fixed** (`picToMosiac.py:115`)
`[LEGO_PALETTE_RGB[i] for i in out_idx.flatten()]` replaced with `LEGO_PALETTE_RGB[out_idx]` — numpy fancy indexing; exact same output, no Python iteration.

### ~~UnsharpMask ran at full input resolution before resize~~ — **fixed** (`picToMosiac.py:88–89`)
Filter now runs on the already-resized small image at 150% intensity. Intensity reduced from 350% because 350% on a NEAREST-downscaled blocky image created haloing that distorted palette selection; 150% after LANCZOS is a mild perceptual sharpness restoration.

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
