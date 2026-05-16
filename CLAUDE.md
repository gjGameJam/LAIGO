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
| `logger.py` | Rotating file logger (`laigo.log`, default 10 MB cap, 1 backup; tunable via `MAX_LOG_SIZE_MB`) |
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

`picToMosiac.py` appends `scripts/` to `sys.path` at module load time (line 12). This allows `Util.py` and `MosiacToOrder.py` to use bare (non-relative) imports (`from Util import ...`, `from logger import logger`) alongside the package-relative imports (`from .Util import ...`). Do not remove that `sys.path.append` call or change import order without verifying both styles still resolve.

## Module interfaces

Two trees: the mosaic pipeline (top-level `scripts/`) and the checkout pipeline (`scripts/checkout/`). They share nothing except the order-list file written to `outputs/{job_id}/order_list.json` and the `Main.py` lifespan that wires both together.

### Mosaic pipeline — call graph

```
Main.py (FastAPI + scheduler + cleanup threads + ProcessPoolExecutor)
  └─ run_job  (subprocess; respawned after every job)
        └─ picToMosiac.pic_to_mosaic
              ├─ remove_background       (MediaPipe; 3D only)
              ├─ adjust_lightness_lab    (skimage RGB↔LAB)
              ├─ image_to_lego_mosaic    (LANCZOS resize + CIEDE2000 Floyd-Steinberg)
              ├─ simplify_background_lego (3D only)
              ├─ MosiacToOrder.GenerateOrderList
              │     └─ Util.SaveDictAsJsonsOptimized → writes order_list.json
              └─ MosiacToInstruction.GenerateInstructions
                    ├─ VisualMaker.generate_baseplate_setup
                    ├─ VisualMaker.draw_plate_column
                    ├─ VisualMaker.draw_grid_setup_instruction
                    ├─ VisualMaker.draw_frame_instructions
                    ├─ VisualMaker.draw_final_view
                    ├─ VisualMaker.save_img_and_increment_step
                    └─ images_to_pdf (reportlab) → writes instructions.pdf
```

#### `Main.py` (1033 LOC) — FastAPI app + job lifecycle

- Imports: `.picToMosiac` (pic_to_mosaic, MosaicType), `.Util` (load_project_env), `.checkout.router` (checkout_router), `.checkout.debug_router` (debug_router), `.checkout.gate_router` (checkout_gate_router), `.checkout.cache` (start_cache_sweeper), `.checkout.gate` (compute_decision, is_truthy, CheckoutMode); lazy-imports `.checkout.payment.registry/base/stripe_provider` inside lifespan.
- HTTP routes: `GET /health`, `GET /`, `GET /queue`, `POST /generate`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/download`. Static mount: `/artifacts` → `OUTPUT_DIR`.
- Lifespan startup (in order): register StripeProvider in payment_registry → compute gate decision → L1 boot assert (refuse boot if `CHECKOUT_ENABLED=true` and gate=DISABLED, or if `sk_live_` outside Render) → `ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)` → scheduler thread → cleanup thread → `start_cache_sweeper()`.
- Shared mutable state on `app.state`: `jobs` dict (job_id → record), `progress` dict (job_id → progress-file Path), `queue_order` list, `job_queue` (queue.Queue maxsize=20), `executor`, `active_jobs` int, `jobs_lock`/`progress_lock`/`queue_lock`/`scheduler_cv`/`scheduler_shutdown` synchronization primitives.
- Worker subprocess `run_job(job_id, image_path, settings, output_root, studs_per_block, progress_path)` returns `dict {status, finished_at, ...}`. Writes progress to a small file (debounced 2s). On failure writes `manifest_failed.json` outside workspace so `get_job` can serve it after eviction.
- Order list handoff: after a successful job, `run_job` copies `workspace/OrderLists/order_list.json` → `outputs/{job_id}/order_list.json` (the stable path the checkout pipeline reads).

#### `picToMosiac.py` (318 LOC) — core image-to-mosaic transformation

- Imports: `.MosiacToOrder` (GenerateOrderList), `.MosiacToInstruction` (GenerateInstructions), `.Util` (GetPaletteRGBArray, load_project_env, log_*).
- Module load: `sys.path.append(scripts/)` so bare imports inside `Util.py`/`MosiacToOrder.py` resolve; computes `LEGO_PALETTE_RGB`, `PALETTE_LAB`, `PALETTE_LAB_RESHAPED` once.
- Public entry: `pic_to_mosaic(img_path, block_width, mosaic_type, background_color_percent, to_frame, output_dir=None, job_id=None, progress_callback=None)`. Drives the 2D vs 3D branch.
- Helpers: `image_to_lego_mosaic(img, studs_w, alpha_mask=None)` (LANCZOS resize → UnsharpMask → CIEDE2000 Floyd-Steinberg → returns `(PIL.Image, idx_array)`), `remove_background(pil_img)` (MediaPipe SelfieSegmentation), `simplify_background_lego(bg_idx, palette_lab, k, alpha_mask)` (per-unique-index remap → vectorized substitution), `adjust_lightness_lab(img, delta_L)` (RGBA-preserving L* shift), `nearest_palette_index_lab(pixel_lab)`.
- Enum: `MosaicType.TWO_D = "2d"`, `MosaicType.THREE_D = "3d"`.
- Reads env on import: `MAX_MOSAIC_BLOCK_WIDTH` (40), `STUD_WIDTH_OF_BLOCK` (16).

#### `MosiacToOrder.py` (152 LOC) — order-list JSON writer

- **Bare import** `from Util import ...` (depends on `picToMosiac` sys.path side-effect).
- Public: `GenerateOrderList(fg_out_rgba, bg_rgba, want_frame, output_dir)` — counts unique RGB pixels per layer via `np.unique(axis=0, return_counts=True)`, looks them up in `LEGO_PALETTE_RGB_DICT`, adds baseplate + optional frame parts, calls `Util.SaveDictAsJsonsOptimized` to write `{output_dir}/OrderLists/order_list.json` (splits into `order_list_1.json`, etc. when any qty > 999).
- Helpers: `GetBaseplatesForSize(width, height)`, `GetFrameForSize(width, height)` — return `{element_id: qty}` for structural parts.

#### `MosiacToInstruction.py` (218 LOC) — instruction PDF assembler

- Imports: `.VisualMaker` (draw_*, save_img_and_increment_step, generate_baseplate_setup), `.Util` (GetOutputPathDir, log_*).
- Public: `GenerateInstructions(fg_rgba, bg_rgba, composite, want_frame, output_dir, progress_callback=None)` — iterates baseplate blocks, draws each column twice (unhighlighted onto persistent canvas, then highlighted onto a copy that gets saved as the step PNG). Emits grid-setup + frame steps + final-view step. Composes all PNGs into a single PDF via `images_to_pdf` (reportlab) at 612×792.
- Step counter `step` is threaded through every call as a parameter and return value (antipattern flagged below).

#### `VisualMaker.py` (1876 LOC) — isometric LEGO stud rendering primitives

- Imports: `.Util` (GetOutputPathDir, log_*).
- Constants: `PLATE_WIDTH=30`, `PLATE_HEIGHT=15`, `STARTING_X=50`, `STARTING_Y=400`, stud sizing constants. Page is 612×792 (US Letter @ 72dpi).
- Public surface used by `MosiacToInstruction`: `draw_final_view`, `generate_baseplate_setup`, `draw_plate_column`, `save_img_and_increment_step`, `draw_frame_instructions`, `draw_grid_setup_instruction`.
- Internal: `draw_plate`, `draw_baseplate_top/bottom`, `draw_stud_with_neck`, `draw_corner_brick`, `draw_brick`, `draw_corner_plate`, `draw_ortho_plate`, `draw_frame_setup_instruction`, `draw_frame_for_mosiac`, coordinate helpers (`get_block_xy`, `iso`, `to_pillow`, `to_rgb`), `get_img_and_draw`, `get_file_name`, `get_font`.
- `save_img_and_increment_step(img, step, output_dir)` draws the step number on `img.copy()` (does NOT mutate `img`) and writes `{output_dir}/Instructions/{step}.png`, returning `step+1`. This is the contract that lets `generate_baseplate_setup` return `(step, img)` so callers can reuse the canvas.

#### `Util.py` (193 LOC) — palette + utility helpers

- **Bare import** `from logger import logger` (depends on `picToMosiac` sys.path side-effect).
- Public: `LEGO_PALETTE_RGB_DICT` (43 entries: `(R,G,B) → element_id`), `GetPaletteDict()`, `GetPaletteRGBArray()` (numpy uint8 array), `SaveDictAsJsonsOptimized(order_dict, output_path, max_per_item=999)`, `GetOutputPathDir()` (project_root/outputs), `load_project_env()` (loads `.env` and optional `.env.secrets`), `log_info/log_debug/log_error`.
- Reads env on import: `DEBUG`.

#### `logger.py` (38 LOC) — rotating file logger

- Public: `logger` (a `logging.Logger` named `"laigoLOG"`).
- Reads env on import: `LOG_FILE` (default `laigo.log`), `MAX_LOG_SIZE_MB` (default 10).
- Two handlers: `RotatingFileHandler` to `<project_root>/<LOG_FILE>` and a `StreamHandler` to stdout. `backupCount=1` so the rotation effectively truncates.

#### `colorQuant.py` — standalone KMeans demo, NOT used by the pipeline.

---

### Checkout pipeline — call graph

```
Main.py lifespan
  ├─ registers StripeProvider → payment.registry
  ├─ compute_decision() at boot (L1 assert)
  └─ start_cache_sweeper()                  (TTL store in cache.py)

POST /jobs/{job_id}/checkout/quote
  → router.get_quote
        ├─ checkout_store.read_order_list(job_id)
        ├─ asyncio.gather(
        │     lego_client.get_all_listings,
        │     brickowl_client.get_all_listings,
        │     bricklink_client.get_all_listings)
        ├─ optimizer.merge_listings → optimizer.optimize → apply_free_shipping_thresholds
        └─ cache.cache_set("quote:{checkout_id}", …, ttl=600)

POST /jobs/{job_id}/checkout/confirm  (Depends require_checkout_gate_open  — L3)
  → router.confirm_checkout
        ├─ cache.cache_get("quote:{checkout_id}")
        ├─ checkout_store.load → 409 if duplicate confirm
        ├─ checkout_store.save(initial state)
        └─ asyncio.create_task(saga.execute_checkout_saga) → tracked in _running_sagas

saga.execute_checkout_saga
  └─ asyncio.wait_for(_execute_checkout_saga_inner, timeout=900)   (B4)
        ├─ gate.require_open                                       (L4)
        ├─ payment.registry.get_active                              (L5)
        ├─ provider.create_hold(1.05× quote, key="hold-{cid}")
        ├─ while True:
        │     ├─ B5 pre-placement drift check
        │     ├─ brickowl_client.create_order  ×N
        │     │     (split try blocks: order + checkpoint — B19)
        │     ├─ lego_client.order_from_lego   (Playwright)
        │     └─ break / continue (stockout retry; cache_delete listings)
        ├─ post-placement drift check (defense-in-depth)
        ├─ _capture_with_retry(provider, key="capture-{cid}", 1s/4s/16s)
        └─ checkpoint PAYMENT_CAPTURED + completed_at
  on TimeoutError:
        └─ _handle_saga_timeout → route by checkpointed state
              · no hold → FAILED
              · hold, no orders → provider.cancel; FAILED on success else MANUAL_REVIEW
              · any orders placed → MANUAL_REVIEW

GET /jobs/{job_id}/checkout/{checkout_id}/status
  → router.get_checkout_status → checkout_store.load → CheckoutStatusResponse

GET /checkout/gate
  → gate_router.checkout_gate → compute_decision (L2)
```

#### `checkout/router.py` (297 LOC) — public HTTP surface for checkout

- Imports: `.models`, `.checkout_store`, `.saga as saga_module`, `.clients.{lego,brickowl,bricklink}_client`, `.optimizer`, `.cache`, `.dependencies` (require_checkout_gate_open), `.gate` (GateDecision).
- Endpoints (prefix `/jobs`): `POST /{job_id}/checkout/quote`, `POST /{job_id}/checkout/confirm`, `GET /{job_id}/checkout/{checkout_id}/status`.
- Module state: `_running_sagas: set[asyncio.Task]` — strong reference set so Python 3.11+ asyncio doesn't GC in-flight Saga tasks (C1 fix). Tasks add themselves on create + remove themselves via `add_done_callback`.
- The 409 on duplicate `/confirm` (same checkout_id) returns `{detail: {error, saga_status, poll_url}}` (B14 shape; public API contract). The L3 503 returns `{detail: {error, code: "CHECKOUT_GATE_CLOSED", mode}}`.

#### `checkout/saga.py` (1203 LOC) — saga orchestrator

- Imports: `.models` (AllocationResult, SagaStatus, StockoutError), `.checkout_store`, `.clients.{lego,brickowl}_client`, `.cache` (cache_delete), `.gate` (require_open, GateClosedError), `.payment.registry`, `.payment.base` (PaymentHold + error types). Deferred imports inside the loop: `.optimizer.{optimize, merge_listings, apply_free_shipping_thresholds}`, `.clients.bricklink_client`.
- Public: `execute_checkout_saga(job_id, checkout_id, allocation, payment_method_id, max_stockout_retries=2)` — the **only** legitimate entry point. Wraps `_execute_checkout_saga_inner` in a single `asyncio.wait_for(..., timeout=_SAGA_TIMEOUT_SECONDS)`.
- Internal: `_execute_checkout_saga_inner`, `_handle_saga_timeout`, `_compensate`, `_compose_compensation_reason`, `_capture_with_retry`, `_cancel_hold_with_retry`, `_CompensationOutcome` dataclass.
- Module constants: `_HOLD_BUFFER_MULTIPLIER = 1.05`, `_CAPTURE_BACKOFFS_SECONDS = (1, 4, 16)`, `_SAGA_TIMEOUT_SECONDS = int(env "SAGA_TIMEOUT_SECONDS", "900")`, `_TERMINAL_STATUSES` frozenset, `_LEGO_SELLER_ID`.
- Reads env at runtime: `STRIPE_CURRENCY` (default `usd`).

#### `checkout/checkout_store.py` (85 LOC) — disk-backed saga state

- Imports: stdlib only.
- Public: `async load(job_id)`, `async save(job_id, state)`, `async update(job_id, partial)`, `read_order_list(job_id)` (sync; raises `FileNotFoundError` if the job hasn't completed).
- Module state: `_locks: dict[str, asyncio.Lock]` populated atomically via `dict.setdefault` (B1).
- Writes: `{OUTPUT_DIR}/{job_id}/checkout_state.json`. Reads `order_list.json` from the same dir.
- **Lock contract**: `asyncio.Lock` is not reentrant. Never call `load()`/`update()` while inside an `update()` already.

#### `checkout/cache.py` (54 LOC) — in-process TTL cache

- Imports: stdlib only.
- Public: `async cache_get(key)`, `async cache_set(key, value, ttl_seconds)`, `async cache_delete(key)`, `start_cache_sweeper()`.
- Sweeper runs every 300s. Started from `Main.py` lifespan. **Open bug B24**: the sweep task is created with `asyncio.create_task(_sweep_loop())` without a strong reference, leaving it GC-vulnerable like the pre-C1 Saga tasks.
- Key conventions: `lego_raw:{eid}`, `boid:{eid}`, `brickowl_listings:{eid}`, `quote:{checkout_id}`.

#### `checkout/optimizer.py` (249 LOC) — pure two-pass greedy allocator

- Imports: `.models` (SellerListing, AllocationEntry, AllocationResult). Deferred inside `apply_free_shipping_thresholds`: `.clients.lego_client.SELLER_ID`.
- Public: `merge_listings(*sources)`, `compute_laigo_fee(grand_total_cents)`, `optimize(order_items, listings) → AllocationResult`, `apply_free_shipping_thresholds(allocation) → AllocationResult`.
- Pure function — no I/O. Env reads (`LAIGO_FEE_MIN_CENTS=300`, `LAIGO_FEE_PERCENT=5`, `LEGO_FREE_SHIPPING_THRESHOLD_CENTS=3500`) happen at call time so tests can override via env.

#### `checkout/gate.py` (267 LOC) — Layer 0 of defense-in-depth

- Imports: `.payment.registry`.
- Public: `compute_decision()` → `GateDecision`, `require_open()` (raises `GateClosedError`), `require_live()`, `is_truthy(v)`, `_stripe_key_mode(key)`, `CheckoutMode` enum (`DISABLED`/`TEST`/`LIVE`), `GateDecision` frozen dataclass.
- Reads env on each call (no caching): `CHECKOUT_ENABLED`, `STRIPE_SECRET_KEY`, `BRICKOWL_API_KEY`, `LEGO_EMAIL`, `LEGO_PASSWORD`, `RENDER`.
- `compute_decision()` consults `payment.registry.is_configured()` / `active_name()` / `active_mode()` as the source of truth for provider state.

#### `checkout/gate_router.py` (187 LOC) — Layer 2 (operational visibility)

- Imports: `.gate.compute_decision`.
- Endpoint: `GET /checkout/gate` — always 200, `Cache-Control: no-store`. Returns `{mode, is_open, payment_provider, marketplaces_live, reasons[], commit}`. Reads `RENDER_GIT_COMMIT` env.
- **Mounting rule**: Render's healthcheck must stay on `/health`. Pointing it at `/checkout/gate` would mask intentional disablement (the endpoint always returns 200).

#### `checkout/dependencies.py` (143 LOC) — Layer 3 FastAPI dependencies

- Imports: `.gate` (compute_decision, GateDecision).
- Public: `async require_checkout_gate_open() → GateDecision` — raises `HTTPException(503, detail={error, code: "CHECKOUT_GATE_CLOSED", mode})`. Currently applied only to `/confirm`. Any new write-side endpoint MUST include this dependency.

#### `checkout/models.py` (135 LOC) — Pydantic models + enum + StockoutError

- Request: `QuoteRequest` (shipping_country, shipping_zip, customer_email), `ConfirmRequest` (checkout_id, stripe_payment_method_id).
- Internal data: `SellerListing`, `AllocationEntry`, `AllocationResult` (incl. `laigo_fee_cents`, `customer_total_cents`).
- Response: `SellerAllocationResponse`, `QuoteResponse`, `ConfirmResponse`, `CheckoutStatusResponse` (incl. `payment_hold_id`, `payment_authorized_cents`, `manual_review_reason`).
- `SagaStatus` enum: `INITIATED`, `STRIPE_HELD`, `ORDERS_PLACED`, `FALLBACK_ORDERED` (unused — see hardening list), `PAYMENT_CAPTURED`, `COMPENSATED`, `FAILED`, `MANUAL_REVIEW`.
- `StockoutError(element_id)` — raised by `brickowl_client.create_order` (when ordering ships per ORDER_OPTIMIZER.md §17).

#### `checkout/debug_router.py` (385 LOC) — read-only debug surface

- Imports: `.models`, `.checkout_store`, `.clients.{brickowl,lego}_client`, `.clients.brickowl_client.{raw_id_lookup, raw_availability, get_boid_for_element}`, `.clients.lego_client.{check_element_available, check_elements_available}`, `.optimizer.optimize`.
- Endpoints (prefix `/checkout-debug`): single + batch BrickOwl listings, single + batch LEGO availability, raw `id_lookup`/`availability` dumps, order-list inspection, full optimizer preview.
- All endpoints are read-only — no orders placed, no state mutated. Safe to expose in Swagger.

#### `checkout/payment/base.py` (206 LOC) — Protocol contract + exceptions

- No internal imports (dependency-free; `gate.py` imports this transitively via registry).
- Public:
  - `PaymentProvider` (Protocol, `runtime_checkable`) — `name: str`, `mode() → "test"|"live"`, `async create_hold(amount_cents, currency, payment_method_id, idempotency_key) → PaymentHold`, `async capture(hold_id, amount_cents, idempotency_key)`, `async cancel(hold_id, idempotency_key)`.
  - `PaymentHold` (frozen dataclass) — `hold_id`, `amount_authorized_cents`, `currency`, `provider`, `mode`.
  - Exception hierarchy: `PaymentProviderUnavailable` (construction-time), `PaymentRetryableError` (transient runtime), `PaymentPermanentError` (terminal runtime). None inherit from common types (`NotImplementedError`, `ValueError`, …) so they can't be silently swallowed.

#### `checkout/payment/registry.py` (140 LOC) — single-active registry

- Imports: `.base`.
- Public: `register(provider)`, `get_active() → PaymentProvider` (raises `PaymentProviderUnavailable` if empty), `is_configured()`, `active_name()`, `active_mode()`, `_reset_for_tests()`.
- Module state: `_active: PaymentProvider | None`. Populated exactly once per process at lifespan startup.
- **B17 replacement guard**: `register()` refuses to replace a different instance unless `LAIGO_ALLOW_REGISTRY_REPLACE` is set (test-only). Production MUST NOT set this env.

#### `checkout/payment/stripe_provider.py` (373 LOC) — only concrete provider

- Imports: `.base`. Lazy-imports `stripe` SDK inside `__init__`.
- Public: `StripeProvider` class, module-level `STRIPE_ENABLED: bool = False` (operator code-flip).
- Construction (atomic — fails fast on any precondition): `STRIPE_ENABLED` flag → SDK importable → `STRIPE_SECRET_KEY` well-formed (sk_test_/sk_live_ + ≥8 char body) → live key requires `RENDER` truthy → set `stripe.api_key` + optional `stripe.api_version`.
- Async wrappers around sync Stripe SDK via `asyncio.to_thread`. Error classification translates Stripe's exception classes into `PaymentRetryable*`/`Permanent*` (see error-classification table inside the module).

#### `checkout/clients/lego_client.py` (345 LOC) — LEGO.com search + Playwright order

- Imports: `..models.SellerListing`, `..cache.{cache_get, cache_set}`. Lazy-imports `playwright.async_api` inside `order_from_lego`.
- Public: `SELLER_ID = "lego_official"`, `SELLER_NAME`, `check_element_available(eid)`, `check_elements_available(eids)`, `get_listing_for_element(eid, country)`, `get_all_listings(order_items, country, zip, cache_ttl=3600) → {eid: [SellerListing]}`, `order_from_lego(items, job_id) → confirmation_number`.
- Reads env: `LEGO_SHIPPING_COST_CENTS` (599), `LEGO_MAX_QTY_PER_ITEM` (9999), `LEGO_EMAIL`, `LEGO_PASSWORD`, `LEGO_FALLBACK_TIMEOUT_SECONDS` (90), `OUTPUT_DIR`.
- Playwright screenshots dropped at `{OUTPUT_DIR}/lego_debug/{job_id}_{label}.png` at every step + on failure.

#### `checkout/clients/brickowl_client.py` (341 LOC) — BrickOwl API wrapper

- Imports: `..models.SellerListing`, `..cache.{cache_get, cache_set}`.
- Public: `SELLER_ID_PREFIX = "brickowl_"`, `get_boid_for_element(eid)`, `get_lots_for_boid(boid, country)`, `get_listings_for_element(eid, country, zip)`, `get_all_listings(order_items, country, zip, cache_ttl=3600)`, `raw_id_lookup(eid, id_type)`, `raw_availability(boid, country)`, `create_order(seller_id, items, shipping_method_id)` → **raises `NotImplementedError`** (ordering pending per ORDER_OPTIMIZER §17), `cancel_order(brickowl_order_id)` → logs warning + no-op.
- Reads env: `BRICKOWL_BASE_URL`, `BRICKOWL_REQUEST_TIMEOUT_SECONDS` (10), `BRICKOWL_CONCURRENT_REQUEST_LIMIT` (10), `BRICKOWL_API_KEY` (lazily, in `_api_key()`).
- Exponential backoff with 3 retries on 429 + 5xx. Module-level `_semaphore` bounds concurrent requests across all callers.

#### `checkout/clients/bricklink_client.py` (70 LOC) — stub

- Imports: `..models.SellerListing`.
- Public: `SELLER_ID_PREFIX = "bricklink_"`, `get_all_listings(...)` — returns empty per-eid dict unless `BRICKLINK_ENABLED=true`, in which case it raises `NotImplementedError`. Setup steps documented in the module docstring.

### Cross-tree boundary

The only path-based handoff between the two trees is `outputs/{job_id}/order_list.json`. `Main.py.run_job` copies the workspace's `order_list.json` to this stable location before deleting the workspace; `checkout_store.read_order_list(job_id)` reads it for the quote flow. Apart from this file and the shared FastAPI app registration in `Main.py` lifespan, the mosaic pipeline does not know the checkout pipeline exists, and vice versa.

## Known bugs

### `remove_background` channel swap (`picToMosiac.py:124`)
PIL gives an RGB numpy array but `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)` treats it as BGR, swapping R and B before passing to MediaPipe. Segmentation is shape-based so the mask is still usable, but the channel order fed to the model is wrong. Should be `cv2.COLOR_RGB2BGR` or just pass the PIL array directly since MediaPipe expects RGB.

### `background_color_percent` `k` miscalculation (`picToMosiac.py:268`)
```python
color_quant = max(1, int((background_color_percent/100) * len(np.unique(bg_idx[fg_mask_np==255]))))
```
`fg_mask_np==255` selects **foreground** pixels, so `k` is computed from background palette indices at foreground positions — the wrong region. The simplification loop itself uses the correct mask (`255-fg_mask_np`), so only background pixels are recolored, but the target color count `k` may be under- or over-estimated. Should use `fg_mask_np==0`.

### Two conflicting PNG sort strategies for the same file set
`_get_ordered_pngs` (inside `images_to_pdf` at `MosiacToInstruction.py:30-42`) sorts by `re.search(r'(\d+)', path.stem)` — first digit sequence. The call site in `GenerateInstructions` (`MosiacToInstruction.py:179-182`) sorts the same directory with `int(p.stem.split("_")[-1]) if "_" in p.stem else 0`. These produce different orderings if filenames contain underscores or leading digits. One canonical sort function should be used everywhere.

### `pic_to_mosaic` returns `None` silently; caller depends on `or workspace` fallback (`Main.py:415`)
The function never returns a path. `run_job` handles this with `result_dir = pic_to_mosaic(...) or workspace`. If someone adds a return value to `pic_to_mosaic` with a falsy result the fallback silently activates.

### ~~No timeout on the worker process~~ — **fixed**
`_check_timed_out_jobs` runs each scheduler loop tick. Jobs running longer than `JOB_TIMEOUT_SECONDS` (default 1800s) are marked failed and `active_jobs` is decremented. `_job_done_callback` is guarded against double-finalizing via a "future popped" sentinel.

### `max_tasks_per_child=1` requires Python 3.12+ (unenforced, `Main.py:178-181`)
`ProcessPoolExecutor.__init__` did not accept `max_tasks_per_child` before Python 3.12. On earlier versions the server fails to start with a `TypeError`. This is not documented in `requirements.txt` or anywhere else.

---

## Image quality / detail preservation

### ~~Initial resize uses `Image.NEAREST`, discarding most source detail~~ — **fixed** (`picToMosiac.py:88`)
`Image.NEAREST` samples a single pixel from the source block and discards the rest. For a 40-block mosaic from a 4000px-wide phone photo (6.25× downscale), each stud's color was determined by 1 out of every ~39 source pixels — ~97% of source color information thrown away before dithering. For smaller mosaics (10 blocks, 25× downscale) this worsened to 99.8% loss. Now uses `Image.LANCZOS`, which averages all source pixels in each stud's footprint with a weighted filter. The dithering now sees a representative color for each stud rather than a random sample.

### UnsharpMask tuned to match resize method (`picToMosiac.py:89`)
Previously `UnsharpMask(radius=1, percent=350, threshold=3)` ran at full input resolution before NEAREST resize — the sharpening was almost entirely discarded by the sampling. After the LANCZOS fix, the filter now runs on the downscaled image at 150% intensity (reduced from 350%), restoring perceptual crispness that LANCZOS softens without creating haloing artifacts that distort palette matching.

### `adjust_lightness_lab` runs at full input resolution (`picToMosiac.py:138–168`, called from `pic_to_mosaic` at lines 258, 260, 290)
`adjust_lightness_lab` is called on the full-resolution image before it is passed to `image_to_lego_mosaic`, which immediately resizes it. The function does a full RGB→LAB→RGB round-trip on megapixel data just to add +5 to the L channel. Eliminating the separate call and applying the L* shift directly to the LAB array after the LANCZOS resize (already computed for dithering) would be cheaper and equivalent. Reverted from a previous attempt due to visual regression concerns — re-examine after LANCZOS change is validated.

### `remove_background` channel swap corrupts background colors in 3D mode (`picToMosiac.py:124`)
Documented in Known bugs above. PIL gives an RGB array; `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)` treats it as BGR, swapping R and B before the segmentation mask is produced. Segmentation is shape-based so the mask is usable, but the background image returned to the pipeline has its R and B channels swapped before palette matching — causing e.g. reds to match blue LEGO colors in the background layer.

---

## CPU performance

Current single-job CPU load on Render: **~70% of 1 vCPU**. Goal: ≤50% per job so two jobs can run concurrently. Running two workers requires Render **Standard tier (2 GB RAM)** minimum — each worker holds mediapipe + numpy + PIL + cv2 in memory (~400–550 MB RSS).

### Hotspot map

| Priority | Location | Operation | Why expensive | Status |
|----------|----------|-----------|---------------|--------|
| 1 | `picToMosiac.py:100–117` | Floyd-Steinberg dithering loop | Pure Python O(W×H), sequential by design | **open** |
| 2 | `picToMosiac.py:38–41` | `nearest_palette_index_lab` | Called per pixel; runs full `deltaE_ciede2000` against all 43 palette entries | **open** |
| 3 | `picToMosiac.py:138–168` | `adjust_lightness_lab` on full-res image (called from `pic_to_mosaic` at lines 258, 260, 290) | Full-res RGB→LAB→RGB round-trip before resize | **open** |
| 4 | `picToMosiac.py:121–134` | MediaPipe segmentation at full resolution | Model inference on megapixel image; mask only needs mosaic resolution | **open** |
| 5 | `MosiacToInstruction.py:137–160` | Double `draw_plate_column` per step | Column drawn twice per instruction step (unhighlighted + highlighted) | **open** |
| 6 | `Main.py:178–181` | `max_tasks_per_child=1` | Worker cold-starts after every job; 3–5s import overhead per job | **open** |
| 7 | `Main.py:63` | `MAX_WORKERS=1` (hardcoded; env value ignored) | No concurrency; second job waits for first to complete | **open** |
| 8 | `MosiacToOrder.py:40–73` | Brick-count pixel loops | ~~O(W×H) Python pixel accessor~~ | **fixed** |
| 9 | `picToMosiac.py:115` | Rebuild `out_rgb` | ~~Python list comprehension over all pixels~~ | **fixed** |
| 10 | `picToMosiac.py:88–89` | UnsharpMask at full resolution | ~~Ran on megapixel image before resize~~ | **fixed** |

### Open items — detail and estimated impact

**#1 — Floyd-Steinberg dithering loop** (`picToMosiac.py:100–117`)
O(W×H) iterations. At max mosaic size (~409,600 pixels for a 40-block job), this is the dominant CPU cost. Error propagation makes full numpy vectorization non-trivial. Two approaches:
- **KDTree nearest-color** (`scipy.spatial.KDTree` over the 43 palette LAB entries): replaces `deltaE_ciede2000` (complex perceptual math in Python) with Euclidean LAB distance in C. No new dependency (scipy is already a transitive dep of scikit-image). Estimated 25–35% reduction in total job time. Trade-off: Euclidean LAB ≈ deltaE76, not CIEDE2000 — tested and reverted once due to visual concerns; worth re-testing on more images before dismissing.
- **Numba JIT** (`@numba.njit` on the whole loop + inlined distance math): eliminates Python interpreter overhead entirely. Estimated 50–60% reduction in total job time. Requires new dependency (~200 MB), JIT warm-up call at worker startup, and inlining the distance calculation. Only pursue if KDTree is insufficient.

**#2 — `adjust_lightness_lab` at full resolution** (`picToMosiac.py:138–168`, called from `pic_to_mosaic` at lines 258, 260, 290)
Called on full-res images (e.g. 4000×3000) before passing to `image_to_lego_mosaic`, which immediately resizes. Fix: add `delta_L=0` parameter to `image_to_lego_mosaic`, apply `lab[..., 0] = np.clip(lab[..., 0] + delta_L, 0, 100)` immediately after `color.rgb2lab(rgb)`, and remove the separate call sites. Estimated 8–12% reduction in total job time. Risk: previous attempt caused visual regression; re-examine after LANCZOS change is validated.

**#3 — MediaPipe at full input resolution** (`picToMosiac.py:121–134`)
`SelfieSegmentation` runs on whatever size PIL image is passed in. The mask only needs to be as fine as the mosaic (e.g. 640×480 for a 40-block job). Fix: downscale the input to ~2× mosaic resolution before calling MediaPipe, then upscale the resulting mask back to full resolution before use. Estimated 10–20% reduction in total job time (MediaPipe inference cost scales with pixel count).

**#4 — Double `draw_plate_column` per instruction step** (`MosiacToInstruction.py:137–160`)
For each column, the pattern is: (1) draw column without highlight on `img`, (2) copy `img` to `to_reuse`, (3) draw same column again with highlight on `to_reuse`. The geometry is identical except for a yellow outline added by `draw_plate` when `highlight=True` (lines 943–955 in `VisualMaker.py`). Fix: draw the column once, copy, then apply only the highlight outline to the copy via a new `draw_highlight_column` helper. Estimated 12–18% reduction in total job time.

**#5 — Worker cold-start overhead** (`Main.py:178–181`)
`max_tasks_per_child=1` respawns the worker process after every job. Each startup imports numpy, PIL, scikit-image, mediapipe, and cv2 — roughly 3–5 seconds of pure overhead per job. Fix: raise to `max_tasks_per_child=3–5`. Memory stays bounded by `MAX_WORKERS × peak-per-worker`. Requires Python 3.12+ (already true).

**#6 — Single-worker concurrency** (`Main.py:63`)
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

### `FRONTEND_ORIGIN` env var is set in `.env` but never read (`Main.py:247–256`)
CORS origins are hardcoded in the `app.add_middleware(CORSMiddleware, ...)` call. The env var implies you can change the frontend URL via config, but you cannot. Either wire the env var into `Main.py` or remove it from `.env`.

### `log_info` used for full data structures at INFO level (`MosiacToInstruction.py:103, 108`, `Util.py:146`)
`log_info(fg_colors)`, `log_info(bg_colors)`, and `log_info(order_dict)` emit full Python set/dict reprs at INFO level. In production this fills the rotating log with noise. These should be `log_debug`.

### Bare imports alongside relative imports (`MosiacToOrder.py:9`, `Util.py:8`)
`from Util import GetPaletteDict` (MosiacToOrder.py:9) and `from logger import logger` (Util.py:8) are bare imports that only resolve because `picToMosiac.py` appends `scripts/` to `sys.path` at module load time (`picToMosiac.py:12`). If either module is ever imported without going through `picToMosiac` first, both fail with `ModuleNotFoundError`. All imports in the package should be relative.

### `queue.Queue` state is not persisted across server restarts
If the server restarts mid-queue (e.g., Render cold start), all queued and running jobs are lost with no notification to users. Users must discover this by polling and resubmit manually.

---

## Order Optimizer (checkout pipeline)

Source: `scripts/checkout/`. Full documentation: `docs/ORDER_OPTIMIZER.md`.

### What it does

After a mosaic job completes, the checkout pipeline reads `order_list.json` (copied to `outputs/{job_id}/order_list.json` before workspace deletion), fetches live BrickOwl listings, runs a greedy two-pass optimizer to minimize total cost (piece prices + per-seller shipping), then executes a Saga: Stripe hold → BrickOwl sub-orders (with stockout retry) → LEGO.com Playwright fallback → Stripe capture. LAIGO adds a service fee of `max($3.00, 5% of grand_total)`.

### Endpoints

```
POST /jobs/{job_id}/checkout/quote                     → QuoteResponse       (no charge, 10-min TTL)
POST /jobs/{job_id}/checkout/confirm                   → ConfirmResponse     (async Saga, poll for result)
GET  /jobs/{job_id}/checkout/{checkout_id}/status      → CheckoutStatusResponse
```

### Module layout (`scripts/checkout/`)

| File | Role |
|---|---|
| `router.py` | FastAPI router; registered in `Main.py` via `app.include_router(checkout_router, prefix="/jobs")`. Includes `_running_sagas` strong-reference set (audit fix C1) so `asyncio.create_task` saga handles aren't GC'd mid-flight. |
| `models.py` | All Pydantic models, `SagaStatus` enum (incl. `MANUAL_REVIEW`), and `StockoutError` exception |
| `optimizer.py` | Two-pass greedy allocator + `apply_free_shipping_thresholds` — pure function, no I/O |
| `saga.py` | Saga orchestrator; B4 timeout wrapper around `_execute_checkout_saga_inner`; `_compensate` with outcome tracking (B3); `_capture_with_retry`/`_cancel_hold_with_retry` (B21); checkpoints to `outputs/{job_id}/checkout_state.json` |
| `clients/lego_client.py` | LEGO.com pricing/availability (httpx) + Playwright Pick-a-Brick ordering. Was historically named `lego_fallback.py` (renamed when LEGO became the primary source). Exports `SELLER_ID="lego_official"`, `check_element_available`, `get_all_listings`, `order_from_lego`. |
| `clients/brickowl_client.py` | Async BrickOwl API wrapper (httpx, semaphore-bounded, exponential backoff). `create_order` is a `NotImplementedError` stub until ordering strategy ships (see ORDER_OPTIMIZER.md §17). |
| `clients/bricklink_client.py` | BrickLink stub — returns empty listings until `BRICKLINK_ENABLED=true` |
| `cache.py` | In-process TTL cache (listings, quotes); sweep task started from `Main.py lifespan` |
| `checkout_store.py` | Disk-backed checkout state + `read_order_list()`. B1 fix: `_get_lock` uses `dict.setdefault` (atomic). |
| `debug_router.py` | `GET /checkout-debug/*` Swagger endpoints (BrickOwl/LEGO listing inspection, optimizer preview). Read-only. |
| `payment/base.py` | `PaymentProvider` Protocol + `PaymentHold` dataclass + retryable/permanent/unavailable exception hierarchy. Dependency-free (no SDK imports). |
| `payment/registry.py` | Single-active provider registry: `register()`, `get_active()`, `is_configured()`, `active_name()`, `active_mode()`. Populated once per process at lifespan startup. **B17 (Phase 3.2):** `register()` refuses to replace a different instance unless `LAIGO_ALLOW_REGISTRY_REPLACE=1` is set (test escape hatch) or the same instance is passed again. Production must never set the env flag. Identity check is `is not provider`, NOT name-based. `_reset_for_tests()` is the canonical way to reset between scenarios — safe to call from production shutdown too. |
| `payment/stripe_provider.py` | `StripeProvider` implementing the Protocol. Houses the `STRIPE_ENABLED` operator flag (was `stripe_client.STRIPE_ENABLED` pre-L5). Atomic construction validation — refuses to construct on missing/malformed key, live key outside Render, or SDK missing. **Not the safety boundary** — see `gate.py`. |
| `gate.py` | Single source of truth for "is checkout safe to run?" — `compute_decision()`, `require_open()`, `GateClosedError`, `CheckoutMode {DISABLED, TEST, LIVE}`. Consults `payment.registry` for provider state. Boot-time assertion in `Main.py` lifespan refuses startup if `CHECKOUT_ENABLED=true` but env is misconfigured |
| `gate_router.py` | `GET /checkout/gate` — public, always 200, no cache. Returns `{mode, is_open, payment_provider, marketplaces_live, reasons[], commit}`. Separate from the liveness `/health` in `Main.py`. **Render's healthcheck setting must stay on `/health`** — pointing it at `/checkout/gate` would keep the service "healthy" even when checkout is intentionally disabled (the endpoint always returns 200). `is_open` is True for both TEST and LIVE; for real-money alerting match `mode == "live"`, not `is_open` |
| `dependencies.py` | FastAPI dependencies for the checkout package. Today: `require_checkout_gate_open` — Layer 3 of the checkout defense. Returns 503 with `{detail: {error, code: "CHECKOUT_GATE_CLOSED", mode}}` when the gate is closed. Applied to `/confirm` via `Depends()`. Read the module docstring before adding endpoints — any new write-side endpoint (places orders, holds money) MUST include this dependency |

### Key design notes

- **Order list path**: `Main.py run_job()` copies `workspace/OrderLists/order_list.json` to `outputs/{job_id}/order_list.json` before deleting the workspace. `checkout_store.read_order_list()` reads this stable path.
- **Saga locking**: `checkout_store.update()` holds an `asyncio.Lock` internally. Never nest `load()`/`update()` calls — `asyncio.Lock` is not reentrant and will deadlock.
- **BrickOwl endpoints**: Verify `/catalog/id_lookup`, `/order/create`, `/order/cancel` paths against https://www.brickowl.com/developer once the API key is active. Field names in `get_listings_for_element()` may need adjustment based on the live API response.
- **Stripe / Checkout gate**: `STRIPE_ENABLED = False` in `payment/stripe_provider.py` is one of several construction-time checks. The full gate also requires `CHECKOUT_ENABLED=true` in env, a valid `STRIPE_SECRET_KEY` prefix (`sk_test_` or `sk_live_`, with at least 8 chars beyond the prefix), at least one marketplace's credentials, and `is_truthy(RENDER)` for live keys. The gate consults `payment.registry`; the registry is populated only if `StripeProvider.__init__` succeeds. Flipping `STRIPE_ENABLED = True` alone is **not sufficient** to put checkout into TEST or LIVE mode. See `docs/CHECKOUT_AUDIT.md §10` for the full layered defense and rollout playbook. **As of 2026-05-15 RPN #1 is structurally retired** — L0–L5 are all shipped. L0 (gate.py) + L1 (boot) + L2 (/checkout/gate) + L3 (router 503) + L4 (Saga pre-flight) + L5 (PaymentProvider Protocol + registry + StripeProvider). All three silent `except NotImplementedError: pass` blocks have been deleted; `stripe_client.py` has been removed. The Saga now makes real Stripe API calls via the registered provider, holds 1.05× the quote total, and retries capture on transient errors (3 attempts, 1s/4s/16s) before escalating to the new `SagaStatus.MANUAL_REVIEW` terminal state.
- **Saga timeout (B4, shipped 2026-05-15)**: `execute_checkout_saga` is wrapped in a single `asyncio.wait_for(..., timeout=SAGA_TIMEOUT_SECONDS=900s)`. This is the ONLY authoritative ceiling on saga lifetime. **Never add nested `wait_for` on intermediate awaits.** Stripe SDK has its own `request_timeout`; Playwright has its own action/navigation timeouts. Layering creates timeout-chaos where inner-fires-then-outer-thinks-success → state diverges from reality. The single saga-level deadline is the contract; per-component timeouts are implementation details. On expiry `_handle_saga_timeout()` routes based on checkpointed state: no hold → FAILED; hold exists, no orders → best-effort cancel; any orders placed → MANUAL_REVIEW.
- **MANUAL_REVIEW state**: when orders are placed but capture fails (exhausted retries OR Stripe `CardError`/`AuthenticationError`/`InvalidRequestError` etc.), the Saga writes `saga_status=manual_review` with a verbose `manual_review_reason`. Operator decides recovery — see ORDER_OPTIMIZER.md §9 for the runbook. Frontend should treat this distinct from FAILED (render "your order is being reviewed by our team", not "your payment failed").
- **Allocation drift safety**: TWO checks. **B5 pre-placement** (saga.py:823) at the top of the `while True:` loop refuses to place ANY orders if the re-optimized total exceeds the authorized hold — clean COMPENSATED path. **Post-placement** at capture time (saga.py:1061) is defense-in-depth; if it ever fires after B5 was supposed to catch it, MANUAL_REVIEW is the only safe state (orders are real). The 5% buffer absorbs normal stockout-retry drift silently.
- **B19 separated try blocks (Phase 2.2)**: every marketplace call is in its own try block, with the post-call `checkout_store.update(...)` checkpoint in a SECOND try block. If the marketplace order succeeds but the state-write fails, the in-memory order ID is passed to `_compensate(..., extra_brickowl_orders=..., extra_lego_order=...)` so the order is cancelled (BrickOwl) or surfaced in MANUAL_REVIEW (LEGO). Never combine the order call and the checkpoint into one try block — that's the silent-loss bug B19 closed.
- **Compensation outcomes (B3, Phase 1.2)**: `_compensate(job_id, *, original_error, extra_brickowl_orders=None, extra_lego_order=None)` writes its OWN terminal status — callers MUST NOT overwrite. Decision rule: any stranded BrickOwl/LEGO/Stripe resource → MANUAL_REVIEW with verbose `manual_review_reason`; clean rollback → COMPENSATED. The `original_error` kwarg is required and verbatim-preserved in the `error` field for operator triage.
- **Playwright selectors**: LEGO.com's React SPA can break `clients/lego_client.py` silently. Screenshots at every step aid debugging (`outputs/lego_debug/`).
- **`/confirm` 409 contract (B14, Phase 3.3)**: when a customer re-submits `/confirm` with the SAME `checkout_id` that's already saved, the 409 body is `{detail: {error, saga_status, poll_url}}` (no longer a flat string). Frontend matches on `saga_status` to decide "keep polling" vs "start fresh quote." Do not rename these fields. **Adjacent open gap (B23):** the 409 only fires when checkout_ids match; a SECOND `/confirm` for the same `job_id` with a DIFFERENT `checkout_id` (from a fresh `/quote`) silently clobbers in-flight state — see PRE_RELEASE_PAYMENT_CHECKLIST.md §4 B23.

### Secrets

All secrets in `.env.secrets` (gitignored): `BRICKOWL_API_KEY`, `STRIPE_SECRET_KEY`, `LEGO_EMAIL`, `LEGO_PASSWORD`. Never commit this file.

### First-time setup

```bash
pip install httpx stripe playwright
playwright install chromium
# Populate .env.secrets with the four keys above
```

### Antipatterns to avoid

- Do not import `stripe` anywhere except `payment/stripe_provider.py`. Any new payment provider goes in `payment/<name>_provider.py` and implements the `PaymentProvider` Protocol from `payment/base.py`.
- Do not catch `PaymentRetryableError` or `PaymentPermanentError` outside `saga.py`. They are the boundary between provider-specific errors and the Saga's policy; classifying them at any other site duplicates logic and risks divergence.
- Do not call BrickOwl API outside `brickowl_client.py`.
- Do not call Playwright outside `clients/lego_client.py`.
- Keep `optimizer.py` as a pure function — no I/O, no env reads at call time.
- Do not catch `GateClosedError` anywhere except the Saga's top-level error handler. The exception is designed to be loud against `except NotImplementedError:` and `except ValueError:` patterns. Adding new catch sites defeats the layered defense.
- Do not add per-component bypasses like `try: do_payment(); except NotImplementedError: pass`. Use `gate.require_open()` at the top of the entry point instead.
- Any new HTTP endpoint that places marketplace orders or holds payments MUST include `Depends(require_checkout_gate_open)` from `scripts/checkout/dependencies.py`. Read-side endpoints (quotes, status polls, diagnostics) may opt out. There is no router-level enforcement — each endpoint declares its own gating in its signature.
- The 503 body shape `{detail: {error, code: "CHECKOUT_GATE_CLOSED", mode}}` is a public API contract. The frontend matches on `code === "CHECKOUT_GATE_CLOSED"` to render the "checkout unavailable" UI. Do not rename these fields without a coordinated frontend change.
- When Layer 6 (structured audit log) ships, replace the `logger.warning(...)` call in `require_checkout_gate_open` with `audit.emit("gate.confirm_rejected", ...)`. See `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` §2 for the migration steps, event schema, and full L6 design contract.
- Pre-release launch checklist (must-dos for stable payments before any real $1 moves) lives in `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md`. Read it before touching the Saga or doing any go-live work. The prioritized hardening sequence (H1–H15) lives in §8 of that file. The database migration plan (3 rounds + binding decisions log) lives in §9 — start at §9.4 for the operating summary and §9.3.11 for the resolved decisions and their future-revisit triggers (**Neon** chosen as host on 2026-05-16, Free tier during dev → Launch $19/mo at go-live, Neon dev branch for local; hard-switch cutover behind `DB_BACKEND` flag, alembic with raw SQL, periodic DELETE for audit retention).
- `LAIGO_ALLOW_REGISTRY_REPLACE=1` is a test-only env flag for `payment.registry.register()`. Production MUST NOT set it. The replacement guard catches forgotten `_reset_for_tests()` calls between scenarios. See `payment/registry.py:49-90`.
- `is_truthy` (the public env-flag parser) is defined in `gate.py:99` and re-implemented inline in three other places (`stripe_provider.py`, `registry.py`, `Main.py`). If you touch the truthy set `{"1", "true", "yes", "on"}`, touch all four sites or they'll drift. Consolidation is tracked as H3 in PRE_RELEASE §8.
- `stripe.api_key` and `stripe.api_version` are module-globals on `stripe-python` — single-active enforcement in `payment.registry` is what keeps this safe today. Do NOT instantiate `StripeProvider` outside the lifespan path; multi-active would require migration to `stripe.StripeClient(...)`. See PRE_RELEASE §4 B15.
- When a saga writes `error: ...` to state, it MUST also write `customer_message: ...` using a key from `ERROR_MESSAGES` in `scripts/checkout/models.py` (B12 / H1 shipped 2026-05-16). `error` is operator-internal (Stripe IDs, exception class names, internal request IDs — never surface to customers). `customer_message` is the only string the frontend should render from /status. Categories: `payment_permanent`, `payment_transient`, `marketplace_failure`, `manual_review`, `drift_buffer`, `gate_closed`, `timeout`. Adding a new category requires coordinated frontend change. Audit check: `grep -n '"error":' scripts/checkout/saga.py` should return only sites where the next line is `"customer_message":`.
- The /confirm 409 body shape `{detail: {error, saga_status, poll_url}}` (B14) and the 503 shape `{detail: {error, code: "CHECKOUT_GATE_CLOSED", mode}}` (L3) are public API contracts. Future error responses should follow the same `{detail: {error, code, ...}}` shape so the frontend can match on `code`.
