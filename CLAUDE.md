# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

LAIGO converts photos into LEGO mosaic building kits. Given an image, it:
1. Quantizes colors to the 43-color LEGO palette using CIEDE2000 delta-E with Floyd-Steinberg dithering
2. Produces a LEGO brick purchase order list (JSON, uploadable to lego.com Pick-a-Brick)
3. Generates step-by-step building instructions as a multi-page PDF

## Active focus — pay-what-you-want pivot (2026-06-13)

The build pack is now a **digital product sold pay-what-you-want**, not an
automated physical-brick order. When the user clicks "Download build pack",
the frontend modal asks them to name a price (≥ $0, zero allowed). The new
endpoint `POST /jobs/{job_id}/pay` (see `scripts/pay_router.py`) charges that
amount once via Stripe (immediate-capture PaymentIntent, `job_id` in metadata)
and records it to `outputs/{job_id}/payment.json`. `POST /webhooks/stripe`
(signature-verified with `STRIPE_WEBHOOK_SECRET`) is the authoritative recorder
— it catches 3DS completions and charges whose sync response was lost.
`GET /jobs/{job_id}/download` stays **ungated** — since $0 is allowed there is
nothing to protect. `pay`/`charge` are covered by `scripts/test_pay_router.py`.

A second, simpler endpoint `POST /donate` (global, no job scope — `donate_router`
in `scripts/pay_router.py`) uses the **client-confirm** Stripe pattern instead:
it mints an *unconfirmed* PaymentIntent via `StripeProvider.create_payment_intent()`
and returns just `{client_secret}` for the frontend's Stripe.js to confirm (3DS
handled client-side). It writes no `payment.json` itself — pass an optional
`job_id` in the body and the existing webhook records it. `metadata={"type":"tip"}`,
`description="LAIGO tip"`. Covered by `scripts/test_donate_router.py`. Both
endpoints coexist; the frontend uses one or the other, not both.

The entire **checkout saga pipeline is SHELVED** (kept on disk, no longer
imported or mounted): `checkout/saga.py`, `saga_resume.py`, `reconcile.py`,
`optimizer.py`, `_cancel_helpers.py`, `payment_holds_store.py`,
`lego_session_store.py`, `clients/`, `checkout/router.py`,
`checkout/debug_router.py`, and the `StripeProvider` hold/capture/cancel
methods. No automated marketplace ordering, no payment holds, no capture saga,
no physical-goods chargeback liability. Re-enable by restoring the imports +
router includes in `Main.py` and flipping `.env` back.

`DB_BACKEND=json` and `CHECKOUT_ENABLED=false`. The Postgres/Neon path
(`init_pool`, `verify_schema`, `resume_in_flight_sagas`, `start_reconcile_task`)
all no-op under json. The JSON jobs store is in-memory (job metadata lost on
restart; completed artifacts persist on disk). The Neon-era operator docs
(`docs/DATABASE_OPS.md`, `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md`) describe the
shelved system and are retained for reference / future re-enable.

## Running locally

Activate the virtual environment from the project root:

```powershell
.venv\Scripts\activate.ps1   # Windows / PowerShell
```

**API server** (primary interface). Run from the **project root** with the
package-qualified app path — `Main.py` uses relative imports (`from .picToMosiac …`)
that only resolve when imported as `scripts.Main`:

```powershell
uvicorn scripts.Main:app --reload
# Swagger UI: http://127.0.0.1:8000/docs
```

(`cd scripts; uvicorn Main:app` will fail with
`attempted relative import with no known parent package`.)

**Standalone CLIs** (for quick local testing). These are scripts, not modules,
so run them from inside `scripts/`:
```powershell
cd scripts
python picToMosiac.py <width_blocks> <2d|3d> <background_pct> <True|False>
# Example: python picToMosiac.py 5 3d 50 True
# Note: image path is hardcoded to ../images/stella1.jpg in __main__

python colorQuant.py <num_colors>
# Note: image path is hardcoded to ../images/labrador.jpg
```

Automated tests are runnable as bare modules from project root:
`.venv\Scripts\python.exe -m scripts.test_<name>`. See `docs/ORDER_OPTIMIZER.md §16` for the
full list — optimizer / gate / saga state machine run with no DB or network; jobs-store
dispatcher + Phase E + reconciler suites need `DB_BACKEND=postgres` + a Neon DSN. For UI / API
walkthroughs, use the Swagger UI at `/docs`.

### Troubleshooting pip installs (corporate SSL proxy)

Some environments intercept TLS and break pip's certificate verification with
`SSLError(SSLCertVerificationError(...))`. Workaround that ships throughout
this project's playbooks:

```powershell
.\.venv\Scripts\python.exe -m pip install --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org <package>
```

Use sparingly — the right long-term fix is installing the corporate root CA
into Python's certifi bundle (see open user action U1 in
`docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5`).

### Database migrations (alembic)

Schema lives in `scripts/migrations/sql/000N_<slug>.{up,down}.sql`; thin
alembic wrappers in `scripts/migrations/versions/000N_<slug>.py`. To apply
against a Neon branch:

```powershell
# Direct (non-pooler) endpoint required — alembic needs session mode.
$env:ALEMBIC_DATABASE_URL = "<direct DSN — no '-pooler' in host>"
alembic upgrade head
```

The `env.py` guard refuses pooler DSNs with an actionable error.

**Adding a new migration `000N`:**
1. Author `scripts/migrations/sql/000N_<slug>.up.sql` and `.down.sql`. Do
   NOT wrap in `BEGIN; … COMMIT;` — alembic wraps in a transaction; an inner
   COMMIT commits prematurely (Postgres has no nested transactions).
2. Author the wrapper `scripts/migrations/versions/000N_<slug>.py` with
   `revision = "000N"`, `down_revision = "000(N-1)"`.
3. Bump `_EXPECTED_SCHEMA_VERSION` in `scripts/db.py` to `"000N"`.
   `verify_alembic_head_matches_expected()` runs at boot and refuses to
   start if the code constant doesn't match the alembic head.
4. Test on a throwaway Neon branch before applying to `main` and `dev`.
5. Schema evolution discipline (B45 contract): if the new column is
   NOT NULL without DEFAULT, you MUST update the explicit INSERT helper
   in the same PR — see the column tuples (`_CHECKOUT_COLS`, `_SAGA_COLS`,
   `_JOB_COLS`) plus the corresponding `insert_*` functions.

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
| `JOB_SAGA_RETENTION_DAYS` | 90 | — (not set, uses code default) | B59: jobs with terminal sagas are reaped when `sagas.completed_at` is older than this many days. 90d is past the 60d chargeback dispute window. |
| `MAX_UPLOAD_SIZE_MB` | 250 | 250 | Max upload file size |
| `DEBUG` | False | True | Enables debug-level logging |
| `FRONTEND_ORIGIN` | — | set but **unused** | CORS origins are hardcoded in `Main.py`, not read from env |
| `DB_BACKEND` | `json` | `json` | `json` keeps the in-memory jobs store + JSON checkout_store; `postgres` activates the (now-shelved) Neon-backed saga/holds/reconcile path. Reverted to `json` 2026-06-13 alongside the pay-what-you-want pivot. |
| `CHECKOUT_ENABLED` | — | `false` | Master gate (L0) for the SHELVED checkout saga. The pay-what-you-want endpoint does NOT consult it (it checks the payment registry directly). Kept `false`; setting `true` while `DB_BACKEND=json` trips the B47 boot refusal. |
| `DATABASE_URL` | — | (none in committed .env; belongs in `.env.secrets`) | Neon **pooler** DSN (host must contain `-pooler`). Read only when `DB_BACKEND=postgres`. Direct endpoint is reserved for `alembic upgrade head` + psql debugging. |

## Architecture

### Processing pipeline

`POST /generate` → upload saved to `inputs/` → job queued → worker process runs `pic_to_mosaic()` → outputs zipped to `outputs/{job_id}/artifact.zip` (plus stable `order_list.json` + `preview.json` alongside) → poll `GET /jobs/{job_id}` → download via `GET /jobs/{job_id}/download`; 3D-renderable mosaic via `GET /jobs/{job_id}/preview`

### Module layout (`LAIGO/scripts/`)

| File | Role |
|---|---|
| `Main.py` | FastAPI app, job lifecycle, scheduler/cleanup threads |
| `picToMosiac.py` | Core pipeline: color mapping, dithering, background separation |
| `MosiacToOrder.py` | Generates brick purchase JSONs (splits >999-qty items across multiple files) |
| `MosiacToInstruction.py` | Sequences instruction PNG steps → PDF |
| `VisualMaker.py` | Draws isometric LEGO stud visuals for each instruction step |
| `preview_builder.py` | Pure `build_preview_payload(...)` + atomic `write_preview_atomic(...)` for the 3D preview JSON. Consumed by `GET /jobs/{id}/preview`. See `docs/PREVIEW_API.md`. |
| `Util.py` | LEGO palette (43 RGB colors → element IDs), logging wrappers, JSON serialization |
| `logger.py` | Rotating file logger (`laigo.log`, default 10 MB cap, 1 backup; tunable via `MAX_LOG_SIZE_MB`) |
| `colorQuant.py` | Standalone KMeans color quantization demo (not used by the pipeline) |
| `db.py` | asyncpg pool for Neon — `init_pool/close_pool/get_pool/is_postgres_backend/verify_schema/verify_alembic_head_matches_expected`. JSONB type codec registered per-connection. No-op when `DB_BACKEND=json`. |
| `jobs_store_pg.py` / `jobs_store_json.py` / `jobs_store_dispatch.py` | Mosaic-job lifecycle storage. Dispatcher routes per-call to PG (Neon) or JSON (in-process dict). Both backends expose the same 23-function API. `dequeue_next()` atomic via `SELECT FOR UPDATE SKIP LOCKED` on PG. |
| `smoke_test_db.py` / `smoke_jobs_pg.py` / `test_jobs_store_*.py` / `test_phase_e_pg.py` | Smoke/edge/integration tests for the DB layer. |

### Concurrency model

- `ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)` — single worker, respawned after every job to release numpy/mediapipe/PIL memory back to the OS
- A Python `queue.Queue(maxsize=20)` decouples HTTP intake from the executor; a scheduler thread drains it
- Progress is tracked by writing a percentage to a small `.progress` file in `inputs/` rather than a multiprocessing Manager
- A cleanup thread (`CLEANUP_INTERVAL` seconds) runs two passes per tick: (1) `cleanup_expired` evicts finished jobs older than `JOB_TTL_SECONDS` whose FK is empty (no /confirm); (2) `cleanup_terminal_sagas` reaps jobs with terminal sagas past `JOB_SAGA_RETENTION_DAYS`. Both delete their output dirs.

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

#### `Main.py` — FastAPI app + job lifecycle

- Imports: `.picToMosiac`, `.Util`, `.checkout.router/debug_router/gate_router`, `.checkout.cache`, `.checkout.gate`, `.jobs_store_dispatch`; lazy-imports `.checkout.payment.{registry,base,stripe_provider}` + `.checkout.saga_resume` inside lifespan.
- HTTP routes: `GET /health`, `GET /`, `GET /queue`, `POST /generate`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/preview` (3D preview JSON — see `docs/PREVIEW_API.md`), `GET /jobs/{job_id}/download`. Static mount: `/artifacts` → `OUTPUT_DIR`.
- Lifespan startup (in order — see boot invariants below): alembic-head check → `init_pool()` → `verify_schema()` → capture event loop → payment provider registration → gate computation → L1 boot invariants → `resume_in_flight_sagas()` (if postgres) → `ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)` + scheduler + cleanup threads + cache sweeper.
- Runtime-only side tables on `app.state` (persistent state lives in `jobs_store_dispatch`): `futures` (job_id → Future, also the arbitration sentinel for done-callback vs watchdog), `deadlines` (job_id → wallclock deadline), `intake` (job_id → full settings dict; preserves `to_frame` until TTL eviction), `progress` (job_id → .progress Path), `event_loop`. Plus `executor`, `active_jobs`, `progress_lock`, `scheduler_cv`, `scheduler_shutdown`.
- Scheduler tick: timeout watchdog → progress mirroring (`.progress` file → store) → `dequeue_next()` (atomic `SELECT FOR UPDATE SKIP LOCKED` on PG; single-lock scan-and-flip on JSON) → executor.submit.
- `_drop_runtime_state(app, jid, drop_intake=False)` is the single helper for clearing transient refs. Terminal callers use the default (intake preserved until TTL); cleanup_loop passes `drop_intake=True`.
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
- Internal: `_execute_checkout_saga_inner`, `_handle_saga_timeout`, `_compensate`, `_compose_compensation_reason`, `_capture_with_retry`, `_CompensationOutcome` dataclass. Hold cancellation goes through `cancel_hold_with_retry` imported from `_cancel_helpers.py` (B56/B57 — also used by `saga_resume.py` and `_handle_saga_timeout` branch 2).
- Module constants: `_HOLD_BUFFER_MULTIPLIER = 1.05`, `_CAPTURE_BACKOFFS_SECONDS = (1, 4, 16)`, `_SAGA_TIMEOUT_SECONDS = int(env "SAGA_TIMEOUT_SECONDS", "900")`, `_TERMINAL_STATUSES` frozenset, `_LEGO_SELLER_ID`.
- Reads env at runtime: `STRIPE_CURRENCY` (default `usd`).

#### `checkout/checkout_store.py` — JSON (legacy) backend for saga state

- Public: `async load(job_id)`, `async save(job_id, state)`, `async update(job_id, partial)`, `read_order_list(job_id)`, `ActiveCheckoutExistsError`.
- Writes: `{OUTPUT_DIR}/{job_id}/checkout_state.json`. Reads `order_list.json` (mosaic artifact) from the same dir.
- Used only when `DB_BACKEND=json`. Deleted in Phase F cleanup.
- **Lock contract**: `asyncio.Lock` is not reentrant. Never nest `load()`/`update()` calls.

#### `checkout/checkout_store_pg.py` + `checkout_store_dispatch.py` — Postgres backend + runtime dispatcher

- Same `load/save/update` API as the JSON backend.
- `save()` does INSERT checkouts → INSERT sagas (FK ordering) in a single transaction via all-columns-explicit helpers (`_insert_checkouts` / `_insert_sagas`). B45 contract: adding a NOT NULL column without DEFAULT requires updating the helper in the same PR.
- `update()` uses `pg_advisory_xact_lock(hashtextextended(job_id, 0))` + `SELECT FOR UPDATE` + merge + UPDATE in one transaction. Always bumps `last_transition_at`.
- `save()` raises `ActiveCheckoutExistsError` on B23 partial-unique-index violation (router translates to 422 with `code="ACTIVE_CHECKOUT_EXISTS"`).
- `read_order_list` is always filesystem-backed (artifact, not state).
- Dispatcher decides per-call based on `DB_BACKEND` env var (NOT cached at import).

#### `checkout/payment_holds_store.py` — Stripe-hold reconciliation index

- Public: `record_hold(checkout_id, hold)`, `mark_status(hold_id, status)`, `fetch_for_reconcile(*, older_than_seconds)`.
- `record_hold` uses `ON CONFLICT (hold_id) DO NOTHING` (idempotent under Stripe idempotency-key resumed-saga case).
- `mark_status` validates against `_ALLOWED_STATUSES = {"requires_capture", "succeeded", "canceled", "unknown"}` regardless of backend; no-op on JSON.
- Called by `saga.py` after `create_hold` (INSERT), capture (UPDATE → succeeded), cancel (UPDATE → canceled).

#### `checkout/lego_session_store.py` — Playwright `storage_state` cache for LEGO.com

- Public: `async load_storage_state() -> dict | None`, `async save_storage_state(state: dict, notes: str | None = None) -> None`.
- Backed by the `external_sessions` table (migration `0003`, keyed by `provider`). One row today: `provider='lego'`.
- `load` returns `None` if the row is absent OR `DB_BACKEND != postgres` (logs WARN); `lego_client.order_from_lego` translates `None` to `LegoSessionExpiredError('not_seeded')`.
- `save` UPSERTs (ON CONFLICT (provider) DO UPDATE). Raises `RuntimeError` when `DB_BACKEND != postgres` — seeding requires Neon by design.
- JSONB codec encodes the dict transparently — do NOT `json.dumps` at call sites.
- Seeded once via `python -m scripts.seed_lego_session` (headed Playwright + manual Google SSO). See `docs/LEGO_SESSION.md` for the refresh runbook.

#### `checkout/saga_resume.py` — boot-time saga recovery (Phase E step 1)

- Public: `resume_in_flight_sagas()` — called once during lifespan, AFTER payment-registry registration, BEFORE scheduler.
- Routes every non-terminal saga to a terminal state: `initiated`→FAILED; `stripe_held`→provider.cancel + FAILED or MANUAL_REVIEW (no hold_id, no provider, or cancel failed); `orders_placed`/`fallback_ordered`→MANUAL_REVIEW with runbook.
- Per-saga errors swallowed (logged CRITICAL) so one stuck row can't block boot. Top-level DB errors DO propagate — boot fails if DB unreachable (fail-loud > partial-up).
- Closes audit FMEA #3 (saga crashes mid-flight, RPN 450).
- No-op when `DB_BACKEND != postgres`.

#### `checkout/audit.py` — L6 structured audit log (Phase E step 3)

- Public: `async def emit(event, *, subject=None, actor=None, data=None, request_id=None) -> None`.
- **NEVER raises** — failures log `[audit] FAILED` at CRITICAL. Audit must not break checkout.
- `data` dict passed directly — the asyncpg JSONB codec encodes. Do NOT `json.dumps(data)` at call site (double-encode → quoted string in DB).
- Schema: `audit_events` table (per PRE_RELEASE §8). First call site wired: `dependencies.py::require_checkout_gate_open` emits `gate.confirm_rejected`. Event vocabulary locked in PRE_RELEASE §2.2 — `payment.hold_orphan` is the **P0-alerting** event for B56's unrecoverable-rollback case.

#### `checkout/_cancel_helpers.py` (B56/B57) — shared hold-cancel retry helper

- Public: `async def cancel_hold_with_retry(*, provider, checkout_id, hold_id, audit_reason="compensation", audit_subject=None) -> tuple[bool, str | None]`.
- 1s/4s/16s retry budget on `PaymentRetryableError`. Immediate fail on `PaymentPermanentError` or unexpected exceptions. Returns `(True, None)` on success, `(False, error_message)` after retries.
- On success: emits `payment.cancelled` audit (with caller-supplied `audit_reason`) AND mirrors `payment_holds_store.mark_status(hold_id, "canceled")` (best-effort). Caller decides what to do on failure (MANUAL_REVIEW write, orphan audit emit, etc.).
- Used by: `saga._compensate` Phase 3, `saga` record_hold-rollback (B56), `saga._handle_saga_timeout` branch 2 (B57), `saga_resume._recover_stripe_held` (B57). DO NOT call `provider.cancel` directly anywhere else — the helper is the canonical path.

#### `checkout/reconcile.py` — orphan-hold reconciler (Phase E step 2)

- Public: `async def reconcile_orphan_holds() -> dict` (single tick; returns outcome histogram), `start_reconcile_task() / async stop_reconcile_task()` (lifecycle), `_reset_for_tests()`.
- Periodic task runs every `RECONCILE_INTERVAL_SECONDS` (env, default 300s, floor 60s); held by module-level `_reconcile_task: Optional[asyncio.Task]` strong reference (C1/B24 pattern).
- For each `payment_holds` row with `last_known_status='requires_capture'` AND `last_reconciled_at < NOW - older_than_seconds`: asks Stripe (`provider.get_hold_status`), then applies the decision matrix in PRE_RELEASE §9.3 (mirror succeeded/canceled; cancel orphans; MANUAL_REVIEW stuck sagas; skip in-flight; mark unknown on Stripe errors).
- **Stuck-saga branch DOES NOT touch Stripe** — operator may be working it manually. Bumps `last_reconciled_at` to space alerts.
- **B55 — MANUAL_REVIEW + `sagas.hold_disposition`:** when Stripe says `requires_capture` and saga is MANUAL_REVIEW, the reconciler reads `hold_disposition`. `cancel_safe` → auto-cancel (safety net for "cancel manually" runbooks). `operator_decides` OR NULL → skip + bump reconciled_at (preserves operator's capture option for "capture-or-refund" runbooks). See `HoldDisposition` in `models.py`.
- No-op when `DB_BACKEND != postgres` OR no provider registered (gate closed). Loop NEVER raises; per-row safety net + histogram.

#### `checkout/cache.py` (54 LOC) — in-process TTL cache

- Imports: stdlib only.
- Public: `async cache_get(key)`, `async cache_set(key, value, ttl_seconds)`, `async cache_delete(key)`, `start_cache_sweeper()`.
- Sweeper runs every 300s. Started from `Main.py` lifespan. Held by module-level `_sweeper_task: Optional[asyncio.Task]` strong reference (C1/B24 pattern) so Python 3.11+ asyncio doesn't GC it. No `stop_cache_sweeper()` today — cancelled when the event loop closes (B63, latent).
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

- Imports: `..models.{SellerListing, StockoutError, LegoSessionExpiredError}`, `..cache.{cache_get, cache_set, cache_delete}`, `..lego_session_store`, `.. import audit`. Lazy-imports `playwright.async_api` inside `order_from_lego`.
- Public: `SELLER_ID = "lego_official"`, `SELLER_NAME`, `check_element_available(eid)`, `check_elements_available(eids)`, `get_listing_for_element(eid, country)`, `get_all_listings(order_items, country, zip, cache_ttl=3600) → {eid: [SellerListing]}`, `order_from_lego(items, job_id) → confirmation_number`, `invalidate_listing(eid)`.
- Reads env: `LEGO_SHIPPING_COST_CENTS` (599), `LEGO_MAX_QTY_PER_ITEM` (9999), `LEGO_FALLBACK_TIMEOUT_SECONDS` (90), `OUTPUT_DIR`. **Deprecated:** `LEGO_EMAIL`, `LEGO_PASSWORD` are still read into module globals but no longer used by the ordering flow — replaced by `storage_state` caching via `lego_session_store`. Kept readable for future fallback paths; do not rely on them.
- `order_from_lego` loads `storage_state` from `lego_session_store.load_storage_state()` and passes it into `browser.new_context(storage_state=...)`. `_run_checkout` Step A is a session probe: navigates to `/profile` and raises `LegoSessionExpiredError('cookie_expired')` if LEGO bounces to `/profile/login`. Saga catches `LegoSessionExpiredError` in a dedicated branch (between `StockoutError` and `Exception`) and writes MANUAL_REVIEW with `hold_disposition=OPERATOR_DECIDES` + `lego.session_expired` audit. Refresh runbook: `python -m scripts.seed_lego_session`. See `docs/LEGO_SESSION.md`.
- Playwright screenshots dropped at `{OUTPUT_DIR}/lego_debug/{job_id}_{label}.png` at every step + on failure (including `ERROR_lego_session_expired` for the expiry case).

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

## Known defects (mosaic pipeline)

Full per-defect documentation — root cause, reproduction, fix sketch,
verification, and cross-references — lives in
`docs/MOSAIC_DEFECTS.md`. The table below is the index only; click an
ID to jump to the entry.

For checkout-pipeline defects see `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4`.

| ID | Severity | Title | File |
|----|----------|-------|------|
| [D-001](docs/MOSAIC_DEFECTS.md#d-001) | **P0** | `background_color_percent` slider is non-functional in 3D mosaics | picToMosiac.py |
| [D-002](docs/MOSAIC_DEFECTS.md#d-002) | **P1** | Duplicate stdout from `laigoLOG` propagating to root | logger.py + Main.py |
| [D-003](docs/MOSAIC_DEFECTS.md#d-003) | **P1** | `_grid_to_python_ints` uses Python loop on 640×640 grid | preview_builder.py |
| [D-004](docs/MOSAIC_DEFECTS.md#d-004) | **P1** | `log_info(fg_colors)` / `log_info(bg_colors)` dump full sets at INFO | MosiacToInstruction.py |
| [D-005](docs/MOSAIC_DEFECTS.md#d-005) | **P1** | Triple recomputation of fg/bg unique colors | MosiacToInstruction.py |
| [D-006](docs/MOSAIC_DEFECTS.md#d-006) | **P1** | `Image.verify()` is a header sniff, not a decode | Main.py |
| [D-007](docs/MOSAIC_DEFECTS.md#d-007) | **P2** | `cv2.cvtColor(img, COLOR_BGR2RGB)` feeds wrong colorspace to MediaPipe | picToMosiac.py |
| [D-008](docs/MOSAIC_DEFECTS.md#d-008) | **P2** | `GenerateOrderList` silently drops pieces on `KeyError` | MosiacToOrder.py |
| [D-009](docs/MOSAIC_DEFECTS.md#d-009) | **P2** | `RotatingFileHandler` opened by every worker subprocess | logger.py |
| [D-010](docs/MOSAIC_DEFECTS.md#d-010) | **P2** | Worker subprocess re-imports the FastAPI + checkout tree | Main.py |
| [D-011](docs/MOSAIC_DEFECTS.md#d-011) | **P2** | MediaPipe `SelfieSegmentation` never closed | picToMosiac.py |
| [D-012](docs/MOSAIC_DEFECTS.md#d-012) | **P2** | `adjust_lightness_lab` runs at full input resolution | picToMosiac.py |
| [D-013](docs/MOSAIC_DEFECTS.md#d-013) | **P2** | CLI / API duality in shared utility modules | cross-cutting |
| [D-014](docs/MOSAIC_DEFECTS.md#d-014) | **P2** | Logging architecture: per-process file handler + duplicate propagation | cross-cutting |
| [D-015](docs/MOSAIC_DEFECTS.md#d-015) | **P3** | `make_difference_transparent` doc string and name describe the inverse of the code | picToMosiac.py |
| [D-016](docs/MOSAIC_DEFECTS.md#d-016) | **P3** | `mask > 0.51` magic threshold | picToMosiac.py |
| [D-017](docs/MOSAIC_DEFECTS.md#d-017) | **P3** | `pic_to_mosaic` returns `None`; caller masks with `or workspace` | picToMosiac.py / Main.py |
| [D-018](docs/MOSAIC_DEFECTS.md#d-018) | **P3** | `give_exception_message` is in-band log-and-reraise | picToMosiac.py |
| [D-019](docs/MOSAIC_DEFECTS.md#d-019) | **P3** | Bare `from Util import …` | MosiacToOrder.py |
| [D-020](docs/MOSAIC_DEFECTS.md#d-020) | **P3** | Bare `from logger import logger` | Util.py |
| [D-021](docs/MOSAIC_DEFECTS.md#d-021) | **P3** | Two PNG sort strategies, one dead | MosiacToInstruction.py |
| [D-022](docs/MOSAIC_DEFECTS.md#d-022) | **P3** | `empty_instructions_folder` raises when folder is missing | MosiacToInstruction.py |
| [D-023](docs/MOSAIC_DEFECTS.md#d-023) | **P3** | `assert` used for runtime invariants | MosiacToInstruction.py |
| [D-024](docs/MOSAIC_DEFECTS.md#d-024) | **P3** | Inconsistent `step` return shape across instruction helpers | MosiacToInstruction.py + VisualMaker.py |
| [D-025](docs/MOSAIC_DEFECTS.md#d-025) | **P3** | `step` counter threaded through every function as a return value | MosiacToInstruction.py + VisualMaker.py |
| [D-026](docs/MOSAIC_DEFECTS.md#d-026) | **P3** | `_mark_submission_failed` does a redundant `rmtree(job_root)` | Main.py |
| [D-027](docs/MOSAIC_DEFECTS.md#d-027) | **P3** | `mp.set_start_method("spawn", force=True)` at module import | Main.py |
| [D-028](docs/MOSAIC_DEFECTS.md#d-028) | **P3** | `MAX_WORKERS` and `MAX_QUEUE_SIZE` env vars documented but ignored | Main.py |
| [D-029](docs/MOSAIC_DEFECTS.md#d-029) | **P3** | `FRONTEND_ORIGIN` env var set but never read | Main.py |
| [D-030](docs/MOSAIC_DEFECTS.md#d-030) | **P3** | `max_tasks_per_child=1` requires Python 3.12+, unenforced | Main.py / requirements.txt |
| [D-031](docs/MOSAIC_DEFECTS.md#d-031) | **P3** | `DEBUG = bool(os.getenv("DEBUG"))` is the D1 typo bug, latent | Util.py |
| [D-032](docs/MOSAIC_DEFECTS.md#d-032) | **P3** | `preview_builder` accepts string literals instead of `MosaicType` enum | preview_builder.py |
| [D-033](docs/MOSAIC_DEFECTS.md#d-033) | **P3** | Preview palette index 0 reserved but not enforced | preview_builder.py |

### Adding a defect

Use the template at the bottom of `docs/MOSAIC_DEFECTS.md`. Required:
ID, severity, location, symptom, root cause, impact, fix sketch,
verification, and cross-references. After adding, mirror the row in
this table.

---

## CPU performance

Current single-job CPU load on Render: **~70% of 1 vCPU**. Goal: ≤50% per job so two jobs can run concurrently. Running two workers requires Render **Standard tier (2 GB RAM)** minimum — each worker holds mediapipe + numpy + PIL + cv2 in memory (~400–550 MB RSS).

### Hotspot map (open items only — shipped optimizations removed for brevity)

| Priority | Location | Operation | Why expensive |
|---|---|---|---|
| 1 | `picToMosiac.py` Floyd-Steinberg loop | Pure Python O(W×H), sequential by design; inner loop allocates a tiny numpy array per pixel via `np.clip(err[y,x+1]+e*7/16,-128,128)` × 4 neighbors |
| 2 | `picToMosiac.py:nearest_palette_index_lab` | Called per pixel; full `deltaE_ciede2000` against all 43 palette entries |
| 3 | `picToMosiac.py:adjust_lightness_lab` | Full-res RGB→LAB→RGB round-trip before resize |
| 4 | `picToMosiac.py:remove_background` | MediaPipe SelfieSegmentation on megapixel image; mask only needs mosaic resolution |
| 5 | `MosiacToInstruction.py` instruction loop | Each column drawn TWICE (unhighlighted + highlighted) |
| 6 | `Main.py` `max_tasks_per_child=1` | Worker cold-starts every job; 3–5s import overhead (FastAPI + checkout + asyncpg all re-imported, see "Worker module owns the FastAPI / checkout import surface" antipattern) |
| 7 | `Main.py` `MAX_WORKERS=1` | No concurrency; second job waits |
| 8 | `preview_builder.py:_grid_to_python_ints` | Python list-comprehension over numpy 2D array; `[[int(v) for v in row] for row in remapped]` for 640×640 = 409,600 Python `int()` calls per layer (×2 for 3D) |

### Open items — fixes + estimated impact

- **#1 Floyd-Steinberg:** KDTree over palette LAB (Euclidean ≈ deltaE76) → 25–35% total. Numba JIT → 50–60% total (200 MB dep). KDTree first; Numba only if insufficient. Cheap micro-optimization available before either: hoist the inner-loop error diffusion to plain Python floats and only `np.clip` in chunks — `np.clip` on a 1-element scalar is dominated by numpy's per-call overhead.
- **#2 `adjust_lightness_lab`:** Add `delta_L=0` param to `image_to_lego_mosaic`, fold into post-resize LAB. → 8–12% total. Risk: prior attempt caused visual regression; re-test.
- **#3 MediaPipe:** Downscale input to ~2× mosaic resolution before segmentation, upscale mask back. → 10–20% total.
- **#4 Double `draw_plate_column`:** Draw once, copy, apply only highlight outline to the copy via a new `draw_highlight_column` helper. → 12–18% total.
- **#5 Worker cold-start:** Two compounding levers. (a) Raise `max_tasks_per_child` to 3–5 (memory bounded by `MAX_WORKERS × peak-RSS`; requires Python 3.12+ already). (b) STRUCTURAL: move `run_job` out of `Main.py` into a leaf `scripts/worker.py` that only imports `picToMosiac` + stdlib. Today every spawn re-imports the whole FastAPI + checkout + asyncpg tree — see the "Worker module owns the FastAPI / checkout import surface" antipattern. The leaf-module fix is the right one to land before raising `max_tasks_per_child`, otherwise each chained job still pays the worker-side `Main` import. Prerequisite for raising either lever: `RotatingFileHandler` is not process-safe (see Known bugs); fix logging first.
- **#6 Single-worker concurrency:** Raise `MAX_WORKERS=2`. Memory ≈ 1.15 GB peak. Requires Render Standard tier (2 GB). Same logging prerequisite as #5.
- **#8 preview_builder grid materialization:** Replace `_grid_to_python_ints(remapped)` with `remapped.tolist()` (a single C-level call). 10–50× faster on 640×640 grids; estimated 200–500 ms saved on the largest mosaic. Two call sites (`background_grid` + `foreground_grid`).

### CPU reduction estimates (cumulative)

| Changes applied | Estimated single-job CPU |
|-----------------|--------------------------|
| Baseline (today before fixes) | ~70% |
| + `adjust_lightness_lab` fix (item #2) | ~58–62% |
| + MediaPipe resolution fix (item #3) | ~46–52% |
| + Double draw_plate_column fix (item #4) | ~35–42% |
| + KDTree nearest-color (item #1 partial) | ~22–30% |
| + max_workers=2 + max_tasks_per_child=3 | two jobs at ~25–35% each |

---

## Antipatterns

### `step` counter threaded as a return value through every function
Every instruction function takes `step: int`, returns `step + N`, and the caller must capture it. Any refactor that adds or reorders steps silently produces wrong step numbers. A stateful counter object would make this contract explicit.

### Inconsistent return shape across instruction helpers (`VisualMaker.py`, `MosiacToInstruction.py`)
Related but distinct from the `step` antipattern: `generate_baseplate_setup` returns `(step, img)` but `draw_grid_setup_instruction`, `draw_frame_instructions`, and `draw_final_view` return only `step`. Every caller has to remember which functions return what. A stateful counter object (or a small mutable wrapper passed by reference) would unify the shape and close the original antipattern.

### `FRONTEND_ORIGIN` env var set but never read (`Main.py`)
CORS origins are hardcoded in `app.add_middleware(...)`. Either wire the env var or remove it from `.env`.

### `MAX_WORKERS` / `MAX_QUEUE_SIZE` env vars documented but ignored (`Main.py:68-69`, `.env`)
Both are listed in the configuration table at the top of this doc, both are present in `.env` — and both are hardcoded in `Main.py` (`MAX_WORKERS = 1`, `MAX_QUEUE_SIZE = 20`). An operator who sets `MAX_QUEUE_SIZE=50` in `.env` sees no effect and no warning. Either wire the env vars or delete them from `.env` and the table.

### `log_info` used for full data structures at INFO level (`MosiacToInstruction.py`, `Util.py`)
`log_info(fg_colors)`, `log_info(bg_colors)`, and `log_info(order_dict)` emit full Python set/dict reprs at INFO level. In production this fills the rotating log with noise. These should be `log_debug`.

### Triple recomputation of fg/bg color sets purely for logging (`MosiacToInstruction.py:101-121`)
`GenerateInstructions` builds `fg_colors` and `bg_colors` via `set(map(tuple, ...))` (full materialization of every unique RGB), then immediately calls `count_colors(...)` which does `np.unique` independently. Three different ways of computing the same thing, all O(H·W·log(H·W)), all just for log lines. Nothing downstream uses these values. Delete the whole block (paired with the `log_info`-of-data antipattern fix above).

### Bare imports alongside relative imports (`MosiacToOrder.py:9`, `Util.py:8`)
`from Util import GetPaletteDict` and `from logger import logger` are bare imports that only resolve because `picToMosiac.py` appends `scripts/` to `sys.path` at module load time. If either module is ever imported without going through `picToMosiac` first, both fail with `ModuleNotFoundError`. All imports in the package should be relative.

### `give_exception_message` is an in-band log-and-reraise (`picToMosiac.py:218-226`)
```python
def give_exception_message(e):
    tb = traceback.extract_tb(e.__traceback__)[-1]
    log_error(f"[ERROR] {type(e).__name__}: {e}")
    log_error(f"File: {tb.filename}")
    ...
    raise
```
The bare `raise` re-raises the current exception of the calling frame, which works only because every call site is inside an `except` block — fragile contract. Also formats only the last frame instead of the full traceback. `logger.exception(...)` already emits class + message + full traceback in one call. Replace with `log.exception("pic_to_mosaic failed"); raise` at each call site and delete this helper.

### `mp.set_start_method("spawn", force=True)` at module import (`Main.py:3-4`)
Runs at module top level, with `force=True`, on every `import scripts.Main`. Any test that imports `Main` resets the multiprocessing context globally — bad for testability and easy to break in CI. Wrap in `if __name__ == "__main__":`, or use the non-force form with a try/except (raising `RuntimeError` if already set is the safer signal). On Windows "spawn" is already the default so the call is mostly a no-op there; the real risk is the side effect on test runners and on Linux/macOS imports.

### `preview_builder.py` uses string literals instead of `MosaicType` enum (`preview_builder.py:88`)
```python
if mosaic_type not in ("2d", "3d"):
```
`MosaicType.TWO_D.value == "2d"` exists; the caller in `picToMosiac.py:278-322` passes literal `"3d"`/`"2d"`. Stringly typed contract that silently rejects any future `MosaicType` addition. Either accept `MosaicType` directly (`mosaic_type: MosaicType`) or validate against `{m.value for m in MosaicType}`.

### `assert` used for runtime invariants (`MosiacToInstruction.py:82-83, 112-114`)
`assert isinstance(bg_rgba, Image.Image)`, `assert bg_rgba.mode == "RGBA"`, `assert C == 4`, `assert W % 16 == 0` — all vanish under `python -O`. Convert to explicit `if … raise ValueError(...)` if they're load-bearing. If they're not, delete them.

### CLI/API duality decaying in shared utility modules
`MosiacToOrder.py`, `MosiacToInstruction.py`, `VisualMaker.py` all carry an `output_dir is None` branch that falls back to `GetOutputPathDir()` (legacy CLI) plus an explicit-path branch (API). The CLI path is no longer hooked into Main, runs against hard-coded image paths in each `__main__`, and has bit-rot (`empty_instructions_folder` raises on missing folder; see Known bugs). Two control flows in shared utility functions tax every future edit. Pick one: either delete the CLI path entirely or wrap it in a single CLI module that calls the API code path with a temp `output_dir`.

### Worker module owns the FastAPI / checkout import surface (`Main.py`)
`run_job` lives in `Main.py`. Because `spawn`-mode subprocesses re-import the function's defining module, every job spawn re-runs all of `Main.py`'s module-level code: `fastapi`, `uvicorn` deps, `checkout.router/debug_router/gate_router/cache/gate`, `jobs_store_dispatch` (which fans into postgres/asyncpg), the env-load and `mkdir`/`write_probe` block (Main.py:82-96), and 9 module-level `log.info(...)` lines (Main.py:98-107) — all of which print to stdout once per spawn. This is the structural cause of the 3-5 s cold start logged as CPU hotspot #6. Fix: move `run_job` (and `_write_error_manifest`) into a leaf module like `scripts/worker.py` whose only imports are `picToMosiac`, `pathlib`, `shutil`, `time`, `json`.

### Logging fan-out is mis-configured across processes
Three problems compound here:
1. `RotatingFileHandler` is opened by every worker subprocess against the same file (race-prone — see Known bugs C2).
2. The `"laigoLOG"` named logger propagates to root, which has its own `StreamHandler` from `Main.py`'s `basicConfig` (duplicate stdout — see Known bugs C3).
3. `Util.log_info` / `log_debug` / `log_error` resolve to the `"laigoLOG"` logger, so every pipeline log line traverses both handler chains.

The right architecture: parent process owns the file (use a `QueueHandler` from workers), workers stream to stdout only, one root-level formatter. Do C2 + C3 first; this is the cleanup that lands on top.

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

Module layout per file is in the "Checkout pipeline" subsection of `## Module interfaces` above. Saga design rules, public API contracts, and provider-state invariants are consolidated under `## Antipatterns to avoid` below.

### Secrets

All secrets in `.env.secrets` (gitignored): `BRICKOWL_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `LEGO_EMAIL`, `LEGO_PASSWORD`. Never commit this file. `STRIPE_WEBHOOK_SECRET` (the `whsec_…` signing secret) is required by `POST /webhooks/stripe`; get it from the Stripe dashboard webhook endpoint, or from `stripe listen` for local testing. The marketplace/LEGO secrets are unused while the saga is shelved.

### First-time setup

```bash
pip install httpx stripe playwright
playwright install chromium
# Populate .env.secrets with the four keys above
```

### Antipatterns to avoid

**Module boundaries (load-bearing):**

- Do not import `stripe` outside `payment/stripe_provider.py`. New payment providers go in `payment/<name>_provider.py` implementing the `PaymentProvider` Protocol from `payment/base.py`.
- Do not call BrickOwl API outside `brickowl_client.py`.
- Do not call Playwright outside `clients/lego_client.py`.
- Do not import from `checkout_store.py` or `checkout_store_pg.py` directly — always go through `checkout_store_dispatch`. Same for `jobs_store_*` → `jobs_store_dispatch`.
- Keep `optimizer.py` pure — no I/O, no env reads at call time.

**Exception classes are boundaries:**

- Do not catch `PaymentRetryableError` / `PaymentPermanentError` outside `saga.py`. They're the boundary between provider errors and saga policy.
- Do not catch `GateClosedError` anywhere except the saga's top-level handler. It's designed to be loud against `except NotImplementedError:` / `except ValueError:` patterns.
- Do not add per-component bypasses like `try: do_payment(); except NotImplementedError: pass`. Use `gate.require_open()` at the entry point.

**Public API contracts (frontend depends on these — coordinated change required to rename):**

- `/confirm` 409: `{detail: {error, saga_status, poll_url}}` for duplicate-checkout_id (same id).
- `/confirm` 422: `{detail: {error, code: "ACTIVE_CHECKOUT_EXISTS"}}` for different-checkout_id same-job_id (B23 PG-mode only).
- `/confirm` 503: `{detail: {error, code: "CHECKOUT_GATE_CLOSED", mode}}` (L3).
- Any new error response should follow the `{detail: {error, code, ...}}` shape so the frontend can match on `code`.
- Any new HTTP endpoint that places marketplace orders OR holds payments MUST include `Depends(require_checkout_gate_open)` from `dependencies.py`. Read-side endpoints (quotes, status, debug) may opt out.

**Saga invariants:**

- The saga is wrapped in a single `asyncio.wait_for(timeout=SAGA_TIMEOUT_SECONDS=900s)`. NEVER add nested `wait_for` on intermediate awaits — layering creates timeout-chaos.
- Every saga write that sets `"error"` MUST also set `"customer_message"` using a key from `ERROR_MESSAGES` (in `models.py`). `error` is operator-internal; `customer_message` is the only customer-facing string the frontend renders from /status. Audit: `grep -n '"error":' scripts/checkout/saga.py` should always be followed by `"customer_message":`.
- `_compensate(job_id, *, original_error, extra_brickowl_orders=None, extra_lego_order=None)` writes its OWN terminal status; callers MUST NOT overwrite. Rule: any stranded resource → MANUAL_REVIEW; clean rollback → COMPENSATED.
- Marketplace order calls and their state-write checkpoints go in SEPARATE try blocks (B19). If the order succeeds but the checkpoint fails, the in-memory order ID is passed to `_compensate(extra_brickowl_orders=...)` so the order is cancelled.
- Two drift checks: B5 pre-placement (top of `while True:`) refuses to place orders if re-optimized total exceeds the authorized hold (clean COMPENSATED). Post-placement check at capture time is defense-in-depth — if it ever fires after B5 should have caught it, MANUAL_REVIEW is the only safe state.
- `_handle_saga_timeout` MUST always write a terminal state. The load-failure arm writes best-effort MANUAL_REVIEW with runbook.
- LEGO ordering has an `except StockoutError: ...` branch BEFORE the generic `except Exception`. `lego_client.order_from_lego` propagates `StockoutError` unwrapped (do NOT consolidate). Today the branch is unreachable (B29 — `order_from_lego` doesn't detect stockouts yet); becomes live when DOM detection ships.
- LEGO ordering ALSO has an `except LegoSessionExpiredError: ...` branch (between `StockoutError` and `Exception`). `order_from_lego` raises this when the cached Playwright `storage_state` is missing or LEGO bounces to `/profile/login`. The branch writes MANUAL_REVIEW with `hold_disposition=OPERATOR_DECIDES` — hold + already-placed BrickOwl orders are intentionally preserved, NOT compensated, so the operator can complete the order out-of-band after re-seeding via `python -m scripts.seed_lego_session`. Emits `lego.session_expired` audit (P0-paging per PRE_RELEASE §2.7). Do NOT consolidate with the generic `Exception` branch — the silent-compensate path is the wrong outcome here. See `docs/LEGO_SESSION.md`.

**Provider-state safety:**

- `payment.registry` is single-active. `LAIGO_ALLOW_REGISTRY_REPLACE=1` is TEST-ONLY (production MUST NOT set it). `Main.py` shutdown calls `_reset_for_tests()` to handle TestClient re-runs.
- `stripe.api_key` and `stripe.api_version` are module-globals on `stripe-python`. Single-active registry enforcement is what keeps this safe. Do NOT instantiate `StripeProvider` outside the lifespan.
- `STRIPE_CURRENCY` is read ONCE in `StripeProvider.__init__`, validated against `_ALLOWED_CURRENCIES`, exposed as `provider.currency`. The saga reads `provider.currency`, NOT `os.environ.get(...)`. New providers MUST set `currency: str` in `__init__`.
- Stripe key validation (test/live + ≥8 chars beyond prefix) lives in `payment/key_format.py:key_mode(key)`. `gate.py`, `stripe_provider.py`, and `Main.py` L1 boot block all import it — DO NOT add a fourth divergent check.

**DB layer:**

- The asyncpg pool in `scripts/db.py` requires `command_timeout=10s` and `statement_cache_size=0` for Neon's transaction-mode PgBouncer pooler. The JSONB type codec is registered on every connection — JSONB columns are written/read as Python dict/list. Do NOT `json.dumps` JSONB values at call sites (double-encode → quoted string in DB).
- `init_pool` refuses boot if `DB_BACKEND=postgres` and DSN host doesn't contain `-pooler`. Hostname parsed via `urllib.parse.urlsplit` (not substring-match — password containing `-pooler` would false-trigger).
- `alembic upgrade head` requires the DIRECT endpoint (`ALEMBIC_DATABASE_URL`, no `-pooler` in host). The pool refuses pooler DSNs from alembic with an actionable error.
- Schema evolution discipline (B45): NOT NULL without DEFAULT requires updating the explicit INSERT helper AND the column tuple in the SAME PR. For `jobs` specifically, changes touch three files (`jobs_store_pg.py`, `jobs_store_json.py`, migration); post-Phase-F this collapses to one.

**Audit log (L6):**

- `audit.emit` NEVER raises — failures log `[audit] FAILED` at CRITICAL. Audit must not break checkout.
- Pass `data` as a Python dict — the JSONB codec encodes. Do NOT `json.dumps(data)` at the call site.
- Event vocabulary is locked in PRE_RELEASE §2.2. New events extend `data`; never rename top-level fields.

**Terminology / cache / GC:**

- `is_truthy` lives in `scripts/checkout/_env.py` (dependency-free). The `frozenset({"1","true","yes","on"})` literal is HERE and ONLY here. Audit: `grep -rn '"1"\s*,\s*"true"' scripts/checkout/` returns one hit.
- Long-lived asyncio tasks (`_running_sagas` in router, `_sweeper_task` in cache, `_reconcile_task` future) MUST be held by a module-level strong reference. Python 3.11+ GCs weakly-referenced tasks. Don't replace with bare `asyncio.create_task(...)`.
- BrickOwl cancels are parallelized via `_parallel_brickowl_cancels` (`Semaphore(5)`). Used by both `_compensate` and stockout-retry inline cancel. Per-order retry is OPEN (B28 — wire when real cancel ships in roadmap #5).
- Each marketplace client owns its own cache-key naming via `invalidate_listing(element_id)`. The saga calls `asyncio.gather(*[client.invalidate_listing(eid) for client in clients])` on stockout. New marketplaces add an `invalidate_listing` mirror.
- **Branch terminology:** "branch" is overloaded. **git branch** = VCS branch in the LAIGO repo. **Neon branch** = copy-on-write database clone in Neon. Both have a default called `main`. Always qualify ("Neon `main`" vs "git `main`") when the surrounding context doesn't make it obvious.

**Boot invariants enforced by `scripts/Main.py` lifespan, in order:**

1. `verify_alembic_head_matches_expected()` — code-time drift; runs unconditionally.
2. `init_pool()` — DB connectivity if `DB_BACKEND=postgres`.
3. `verify_schema()` — alembic_version vs `_EXPECTED_SCHEMA_VERSION`; runs if postgres.
4. Capture event loop on the jobs_store dispatcher.
5. Payment-provider registration (StripeProvider; `PaymentProviderUnavailable` is non-fatal — gate decides).
6. L1 (a): `CHECKOUT_ENABLED=true` + gate DISABLED → refuse boot.
7. L1 (b): `sk_live_` key outside Render → refuse boot.
8. L1 (c): `CHECKOUT_ENABLED=true` + `DB_BACKEND≠postgres` → refuse boot (B47).
9. `resume_in_flight_sagas()` if postgres — routes every non-terminal saga to a terminal state.
10. ProcessPoolExecutor + scheduler + cleanup threads + cache sweeper start.
11. `start_reconcile_task()` — periodic `reconcile_orphan_holds()` if postgres. Held by module-level strong reference in `reconcile.py`; cancelled cleanly in lifespan shutdown BEFORE `close_pool` (task uses the pool).

ALL boot refusals raise `RuntimeError` after `log.critical(...)`. Fail loud, fail early.

**Where to find what:**

- **Pre-launch gating checklist:** `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` — read §0 for current state, §3 for roadmap, §4 for open defects, §8 for schema, §9.3 for Phase E remaining, §9.4 for Phase F playbook.
- **Diagnostic record (FMEA, audit history):** `docs/CHECKOUT_AUDIT.md`.
- **Checkout module reference:** `docs/ORDER_OPTIMIZER.md`.
- **3D preview API + payload schema (frontend-facing):** `docs/PREVIEW_API.md`.
- **DB operations + debug playbook (Neon):** `docs/DATABASE_OPS.md` — connection model, migration workflow, operator queries, debug playbook, the diagnose_db.py tool.
- **Mosaic pipeline defects ledger:** `docs/MOSAIC_DEFECTS.md` — per-defect root cause, reproduction, fix sketch, verification. Index mirrored in `## Known defects (mosaic pipeline)` above.
- **LEGO.com Playwright session operator reference:** `docs/LEGO_SESSION.md` — schema (`external_sessions`, migration `0003`), initial seeding via `python -m scripts.seed_lego_session`, refresh runbook when `lego.session_expired` fires, branch-mismatch debugging.
