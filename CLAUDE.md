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
the frontend modal asks them to name a price (≥ $0, zero allowed) **and a
required email address**. The endpoint `POST /jobs/{job_id}/pay` (see
`scripts/pay_router.py`) charges that
amount once via Stripe (immediate-capture PaymentIntent; `job_id`, `source`,
and `email` in metadata; `receipt_email` set)
and records it to `outputs/{job_id}/payment.json`. `POST /webhooks/stripe`
(signature-verified with `STRIPE_WEBHOOK_SECRET`) is the authoritative recorder
— it catches 3DS completions and charges whose sync response was lost.
`GET /jobs/{job_id}/download` stays **ungated** — since $0 is allowed there is
nothing to protect. `pay`/`charge` are covered by `scripts/test_pay_router.py`.

**Email delivery (2026-07-05):** every completed checkout ($0 or paid) also
emails the build pack (instructions PDF extracted from `artifact.zip` +
`order_list.json`) to the given address via `scripts/emailer.py` (Resend REST
API over httpx; leaf module; fire-and-forget through FastAPI BackgroundTasks —
a send failure never fails a charge). The webhook path emails too (3DS / lost
responses), reading the address back from PaymentIntent metadata — LAIGO
stores nothing; the `outputs/{job_id}/email.json` sentinel (atomic `open(x)`
claim) dedupes sync-vs-webhook races and Stripe event redelivery, and is
purged with the job dir at TTL. Tips (`/donate`, `type=tip`) never trigger a
build-pack email. Oversize packs (> ~35 MB encoded) fall back to
order-list-only + an expiring download link. Full design + go-live runbook:
`docs/EMAIL_DELIVERY.md`. Covered by `scripts/test_emailer.py` and the
send-trigger tests in `test_pay_router.py`.

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
`.venv\Scripts\python.exe -m scripts.test_<name>`. Live PWYW coverage:
`test_pay_router`, `test_donate_router`, `test_emailer`. Mosaic pipeline: `test_preview`,
`test_background_budget`, `test_order_list`, `test_piece_specs`,
`test_stats_endpoint`. Mosaic jobs
store (json mode): `test_jobs_store_json`, `test_jobs_store_edge`. The remaining
suites exercise the
**shelved** checkout pipeline: `test_optimizer`, `test_gate_bypass`,
`test_saga_state_machine` (no DB/network), plus the Postgres-only
`test_jobs_store_dispatch` / `test_phase_e_pg` / `test_reconcile_pg` (need
`DB_BACKEND=postgres` + a Neon DSN). For UI / API walkthroughs, use the Swagger
UI at `/docs`.

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

### Database migrations (alembic) -- dormant

Migrations only apply when running the Postgres backend (`DB_BACKEND=postgres`),
which is **off** under the PWYW model. Schema lives in
`scripts/migrations/sql/000N_<slug>.{up,down}.sql` with alembic wrappers in
`scripts/migrations/versions/`; the head / `_EXPECTED_SCHEMA_VERSION` is `0003`.
The full workflow (authoring a migration, the no-inner-`BEGIN`/`COMMIT` rule, the
B45 NOT-NULL-column discipline, applying against a Neon direct endpoint) is in
the dormant doc `docs/DATABASE_OPS.md §3`. To turn the Postgres backend on at
all, see `docs/BACKEND_SWITCHING.md`.

## Configuration

All runtime knobs live in `.env` (committed — no secrets):

| Variable | Default in code | Value in .env | Effect |
|---|---|---|---|
| `MAX_WORKERS` | `1` | `1` | Parallel processing workers. Read from env (D-028). Kept at 1: raising to 2 needs Render **Standard tier** (2 GB) — two workers ≈ 1.15 GB peak — and is only safe with the logging/worker-isolation fixes (D-009/D-010/D-011) already in place. |
| `MAX_QUEUE_SIZE` | `20` | `20` | Max pending jobs before 429. Read from env (D-028). |
| `MAX_MOSAIC_BLOCK_WIDTH` | 40 | 40 | Max blocks wide a mosaic can be |
| `MAX_PROCESSING_DIMENSION` | 2048 | 2048 | Max long-edge (px) the input is downscaled to (via `cap_processing_resolution`) before the heavy full-res ops (`adjust_lightness_lab` LAB round-trip, `remove_background` MediaPipe). Mosaic output is ≤640 studs, so 2048 oversamples >3× (no perceptible quality change) while bounding the float64 LAB arrays that OOM'd the worker on multi-megapixel uploads (D-012). |

Note: the former `STUD_WIDTH_OF_BLOCK` knob was **removed** (D-035, fixed 2026-06-29). The 16-stud block is now a fixed structural constant `STUDS_PER_BLOCK` in `scripts/mosaic_types.py`, imported by every module that needs it (the baseplate art is hand-built for 16×16, so it was never a safe runtime knob).
| `JOB_TTL_SECONDS` | 600 | 3600 | Seconds before completed jobs are purged |
| `JOB_TIMEOUT_SECONDS` | 1800 | — (not set, uses code default) | Max seconds a running job may take before forced failure |
| `CLEANUP_INTERVAL` | 300 | — (not set, uses code default) | How often the cleanup thread runs (seconds) |
| `JOB_SAGA_RETENTION_DAYS` | 90 | — (not set, uses code default) | B59 (postgres-only; **dormant under `json`** — `cleanup_terminal_sagas` no-ops): jobs with terminal sagas are reaped when `sagas.completed_at` is older than this many days. 90d is past the 60d chargeback dispute window. |
| `MAX_UPLOAD_SIZE_MB` | 250 | 250 | Max upload file size |
| `DEBUG` | — | True | Effectively unused: the only reader (`Util.DEBUG`) was removed (D-031), and `logger.py` logs at DEBUG unconditionally. |
| `FRONTEND_ORIGIN` | — | set but **unused** | CORS origins are hardcoded in `Main.py`, not read from env |
| `DB_BACKEND` | `json` | `json` | `json` keeps the in-memory jobs store + JSON checkout_store; `postgres` activates the (now-shelved) Neon-backed saga/holds/reconcile path. Reverted to `json` 2026-06-13 alongside the pay-what-you-want pivot. To flip backends, see `docs/BACKEND_SWITCHING.md`. |
| `CHECKOUT_ENABLED` | — | `false` | Master gate (L0) for the SHELVED checkout saga. The pay-what-you-want endpoint does NOT consult it (it checks the payment registry directly). Kept `false`; setting `true` while `DB_BACKEND=json` trips the B47 boot refusal. |
| `DATABASE_URL` | — | (none in committed .env; belongs in `.env.secrets`) | Neon **pooler** DSN (host must contain `-pooler`). Read only when `DB_BACKEND=postgres`. Direct endpoint is reserved for `alembic upgrade head` + psql debugging. |
| `EMAIL_ENABLED` | `false` (unset) | `true` | Master switch for build-pack emails (`scripts/emailer.py`). With no `RESEND_API_KEY`, sends are skipped (per-job log + boot warning) — never a boot failure. |
| `EMAIL_FROM` | `LAIGO <onboarding@resend.dev>` | same | Sender identity. The resend.dev sender is dev-mode (delivers only to the Resend account owner). Production switch = verify a domain in Resend + change only this var. |
| `PUBLIC_API_BASE_URL` | — | (empty) | Origin for the `/jobs/{id}/download` link inside emails (oversize/link-only fallback). Falls back to Render's auto-set `RENDER_EXTERNAL_URL`. |
| `RESEND_API_KEY` | — | (belongs in `.env.secrets`) | Resend API key. Read at send time, not import time. |

## Architecture

### Processing pipeline

`POST /generate` → upload saved to `inputs/` → job queued → worker process runs `pic_to_mosaic()` → outputs zipped to `outputs/{job_id}/artifact.zip` (plus stable `order_list.json` + `preview.json` + `stats.json` alongside) → poll `GET /jobs/{job_id}` → download via `GET /jobs/{job_id}/download`; 3D-renderable mosaic via `GET /jobs/{job_id}/preview`; piece count + cost estimate via `GET /jobs/{job_id}/stats`

### Module layout (`LAIGO/scripts/`)

| File | Role |
|---|---|
| `Main.py` | FastAPI app, job lifecycle, scheduler/cleanup threads. Imports the worker entry point from `worker.py` (does not define it). |
| `worker.py` | Leaf module holding `run_job` + `_write_error_manifest`, the code that runs in the spawned worker subprocess. Imports only `picToMosiac` + stdlib so a spawn does not re-import FastAPI/checkout/asyncpg (D-010). |
| `picToMosiac.py` | Core pipeline: color mapping, dithering, background separation |
| `mosaic_types.py` | Dependency-free leaf module holding the `MosaicType` enum, shared by `picToMosiac` and `preview_builder` without a circular import (D-032). |
| `MosiacToOrder.py` | Generates brick purchase JSONs (splits >999-qty items across multiple files) |
| `MosiacToInstruction.py` | Sequences instruction PNG steps → PDF |
| `VisualMaker.py` | Draws isometric LEGO stud visuals for each instruction step |
| `preview_builder.py` | Pure `build_preview_payload(...)` + atomic `write_preview_atomic(...)` for the 3D preview JSON. Consumed by `GET /jobs/{id}/preview`. See `docs/PREVIEW_API.md`. |
| `pricing.py` | Stdlib-only leaf: loads the static price table `scripts/piece_prices.json` (element_id → US cents, null = unknown; refresh = edit file + bump `as_of` — a running server picks it up via mtime-checked cache, no restart) and computes the all-or-null `estimate_cost_cents(...)` for `GET /jobs/{id}/stats`. Static by design — no live LEGO.com fetch (Cloudflare-fronted, drifting fields, non-PaB structural parts). |
| `emailer.py` | Leaf module (stdlib + httpx): emails the build pack after a PWYW checkout via Resend. Extracts the PDF from `artifact.zip`, attaches `order_list.json`, dedupes with the `email.json` sentinel. Never raises. See `docs/EMAIL_DELIVERY.md`. |
| `Util.py` | LEGO palette (43 RGB colors → element IDs), logging wrappers, JSON serialization |
| `logger.py` | Rotating file logger (`laigo.log`, 10 MB cap, 1 backup; tunable via `MAX_LOG_SIZE_MB`). Parent process owns the file handler; worker subprocesses log to stdout only (D-009). `propagate=False` (D-002). |
| `colorQuant.py` | Standalone KMeans color quantization demo (not used by the pipeline) |
| `db.py` | asyncpg pool for Neon — `init_pool/close_pool/get_pool/is_postgres_backend/verify_schema/verify_alembic_head_matches_expected`. JSONB type codec registered per-connection. No-op when `DB_BACKEND=json`. |
| `jobs_store_pg.py` / `jobs_store_json.py` / `jobs_store_dispatch.py` | Mosaic-job lifecycle storage. Dispatcher routes per-call to PG (Neon) or JSON (in-process dict). Both backends expose the same 23-function API. `dequeue_next()` atomic via `SELECT FOR UPDATE SKIP LOCKED` on PG. |
| `smoke_test_db.py` / `smoke_jobs_pg.py` / `test_jobs_store_*.py` / `test_phase_e_pg.py` | Smoke/edge/integration tests for the DB layer. |

### Concurrency model

- `ProcessPoolExecutor(max_workers=MAX_WORKERS, max_tasks_per_child=1)` — single worker (default), respawned after every job to release numpy/mediapipe/PIL memory back to the OS. The submitted callable is `worker.run_job`, so each spawn re-imports only `scripts.worker` (→ `picToMosiac` + stdlib), not the FastAPI/checkout tree (D-010).
- `max_tasks_per_child` requires Python **3.12+**. The lifespan checks `sys.version_info`: on 3.12+ it passes `max_tasks_per_child=1` (worker respawned after every job); on 3.11 or earlier it omits the kwarg and logs a warning (`respawn=off`), so the server still runs but the single worker persists across jobs — no per-job RSS reclaim. MediaPipe's native graph is still released each job via its context manager (D-011), so the only thing forgone on 3.11 is OS-level memory return. `runtime.txt` pins `python-3.12` for Render, where respawn is on (D-030).
- The scheduler thread polls `jobs_store.dequeue_next()` (atomic `SELECT FOR UPDATE SKIP LOCKED` on PG; single-lock scan-and-flip on JSON) every `SCHEDULER_IDLE_POLL_SECONDS` and submits to the executor — there is no in-process `queue.Queue`; the jobs store is the queue, bounded by `MAX_QUEUE_SIZE` at intake.
- Progress is tracked by writing a percentage to a small `.progress` file in `inputs/` rather than a multiprocessing Manager.
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
  order_list.json         # stable copy of the FIRST order-list file (999-capped per element when split — do not sum it)
  preview.json            # stable copy; served by GET /jobs/{id}/preview
  stats.json              # full per-element piece counts + total; served by GET /jobs/{id}/stats
  manifest_failed.json    # written on failure (outside workspace, persists)
```

### CORS

Allowed origins are hardcoded in `Main.py`: `https://laigo-frontend.onrender.com` and `http://localhost:5173` (Vite default). The `FRONTEND_ORIGIN` env var in `.env` is **not** read by the server — update the hardcoded list in `Main.py` if the frontend URL changes.

### Import graph (how the modules wire together)

Every intra-package import is **package-relative** (`from .Util import ...`). The
old `sys.path.append("scripts/")` hack in `picToMosiac.py` (which let `Util.py` /
`MosiacToOrder.py` use bare `from Util import ...` / `from logger import logger`)
is **gone** (D-019/D-020). Two consequences worth keeping in mind:

- **Standalone imports work.** `from scripts import Util, MosiacToOrder, preview_builder`
  resolves from a fresh interpreter — no need to import `picToMosiac` first to
  prime `sys.path`. Tests rely on this.
- **`mosaic_types.py` is the shared leaf.** `MosaicType` lives there (not in
  `picToMosiac`) so `preview_builder` can validate `mosaic_type` without importing
  `picToMosiac` — which would be circular, since `picToMosiac` imports
  `preview_builder`. `picToMosiac` re-exports `MosaicType` for back-compat.

The dependency direction is: `picToMosiac` → {`mosaic_types`, `MosiacToOrder`,
`MosiacToInstruction`, `preview_builder`, `Util`}; `MosiacToInstruction` →
`VisualMaker` → `Util` → `logger`. No cycles.

## Module interfaces

This section documents the **active mosaic pipeline** (top-level `scripts/`). The
checkout pipeline (`scripts/checkout/`) is shelved — see "Shelved subsystem:
automated checkout pipeline" below for the condensed pointer.

### Mosaic pipeline — call graph

```
Main.py (FastAPI + scheduler + cleanup threads + ProcessPoolExecutor)
  └─ worker.run_job  (subprocess; respawned after every job — leaf module, D-010)
        └─ picToMosiac.pic_to_mosaic
              ├─ remove_background       (MediaPipe RGB, context-managed; 3D only)
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

- Imports: `.picToMosiac` (MosaicType, MAX_BLOCK_WIDTH, MIN_BLOCK_WIDTH), `.worker` (run_job, _write_error_manifest), `.Util` (load_project_env), `.pricing` (load_price_table, estimate_cost_cents), `.pay_router` (pay_router, webhook_router, donate_router), `.checkout.gate_router` (checkout_gate_router), `.checkout.cache` (start_cache_sweeper), `.checkout.gate` (compute_decision, is_truthy, CheckoutMode), `.jobs_store_dispatch`; lazy-imports `.db` + `.checkout.payment.{registry,base,stripe_provider}` inside lifespan. The shelved `.checkout.router` / `.checkout.debug_router` / `.checkout.saga_resume` are **not** imported or mounted.
- HTTP routes (mosaic): `GET /health`, `GET /`, `GET /queue`, `POST /generate`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/preview` (3D preview JSON — see `docs/PREVIEW_API.md`), `GET /jobs/{job_id}/stats` (authoritative piece count from `stats.json` + optional static-price estimate — `{piece_count, estimated_cost_cents, currency, pricing_as_of}`; cost is null unless every element is priced in `piece_prices.json`; 404 `STATS_NOT_AVAILABLE` when the file is absent, which the frontend renders as "no chip"), `GET /jobs/{job_id}/download` (ungated — $0 is allowed so there's nothing to protect). Static mount: `/artifacts` → `OUTPUT_DIR`.
- HTTP routes (payment, via mounted routers): `POST /jobs/{job_id}/pay` (`pay_router`, prefix `/jobs`), `POST /donate` (`donate_router`), `POST /webhooks/stripe` (`webhook_router`), `GET /checkout/gate` (`checkout_gate_router` — operational visibility only, always 200). See the PWYW section at the top of this file.
- Lifespan startup (in order — see "Boot invariants" below): alembic-head check → `init_pool()` (no-op on json) → `verify_schema()` (no-op on json) → capture event loop → StripeProvider registration (powers `pay`/`donate`) → gate computation (DISABLED under PWYW) → L1 boot invariants (incl. B47) → `resume_in_flight_sagas()` (no-op on json) → version-conditional `ProcessPoolExecutor` (`max_tasks_per_child=1` on Python 3.12+, omitted with a `respawn=off` warning on ≤3.11 — D-030) + scheduler + cleanup threads + cache sweeper → `start_reconcile_task()` (no-op on json).
- Runtime-only side tables on `app.state` (persistent state lives in `jobs_store_dispatch`): `futures` (job_id → Future, also the arbitration sentinel for done-callback vs watchdog), `deadlines` (job_id → wallclock deadline), `intake` (job_id → full settings dict; preserves `to_frame` until TTL eviction), `progress` (job_id → .progress Path), `event_loop`. Plus `executor`, `active_jobs`, `progress_lock`, `scheduler_cv`, `scheduler_shutdown`.
- Scheduler tick: timeout watchdog → progress mirroring (`.progress` file → store) → `dequeue_next()` (atomic `SELECT FOR UPDATE SKIP LOCKED` on PG; single-lock scan-and-flip on JSON) → executor.submit.
- `_drop_runtime_state(app, jid, drop_intake=False)` is the single helper for clearing transient refs. Terminal callers use the default (intake preserved until TTL); cleanup_loop passes `drop_intake=True`.
- `run_job` (the worker entry point) and `_write_error_manifest` are **imported from `.worker`** (D-010) — `Main.py` no longer defines them. `Main.py`'s own error paths (timeout watchdog, submission-failure) call the imported `_write_error_manifest`.
- Order list handoff: after a successful job, `run_job` (in `worker.py`) copies `workspace/OrderLists/order_list.json` → `outputs/{job_id}/order_list.json` (a stable path; bundled into `artifact.zip` for download, and read by the shelved checkout pipeline if re-enabled). It copies `workspace/stats.json` → `outputs/{job_id}/stats.json` the same way (full per-element counts for `GET /jobs/{id}/stats` — the stable `order_list.json` must NOT be summed instead: when the order splits, it holds only the first 999-capped chunk of each element).

#### `worker.py` — worker-subprocess entry point

- Imports ONLY `picToMosiac` + stdlib (gc, json, logging, shutil, sys, time, traceback, pathlib). Never import FastAPI / checkout / db / jobs_store here — that re-introduces the cold-start cost this module exists to eliminate (D-010).
- Public: `run_job(job_id, image_path, settings, output_root, studs_per_block, progress_path) → dict {status, finished_at, ...}`. Writes progress to a small `.progress` file (debounced 2s); on failure writes `manifest_failed.json` outside the workspace so `get_job` can serve it after eviction. `_write_error_manifest(...)` is the shared failure writer (also called by `Main.py`).
- ProcessPoolExecutor (spawn mode) re-imports this module in each worker to resolve the `run_job` reference — keeping it a leaf is what makes a spawn cheap.

#### `picToMosiac.py` — core image-to-mosaic transformation

- Imports (all package-relative): `.mosaic_types` (MosaicType), `.MosiacToOrder` (GenerateOrderList, BuildStatsPayload), `.MosiacToInstruction` (GenerateInstructions), `.preview_builder` (build_preview_payload, write_preview_atomic), `.Util` (GetPaletteRGBArray, load_project_env, log_*).
- Module load: computes `LEGO_PALETTE_RGB`, `PALETTE_LAB`, `PALETTE_LAB_RESHAPED` once. (The legacy `sys.path.append(scripts/)` hack is gone — D-019/D-020 made every intra-package import relative.)
- Public entry: `pic_to_mosaic(img_path, block_width, mosaic_type, background_color_percent, to_frame, output_dir=None, job_id=None, progress_callback=None)`. Drives the 2D vs 3D branch. Immediately after `open_image`, calls `cap_processing_resolution(img)` so the heavy full-res ops in **both** branches see a bounded-resolution image (D-012).
- Helpers: `image_to_lego_mosaic(img, studs_w, alpha_mask=None, build_image=True)` (LANCZOS resize → UnsharpMask → CIEDE2000 Floyd-Steinberg → returns `(PIL.Image | None, idx_array)`; `build_image=False` skips the PIL image for callers that only need the index array — the 3D bg, D-045), `cap_processing_resolution(img, max_dim=None)` (shrink-only, aspect-preserving `Image.thumbnail` to `MAX_PROCESSING_DIMENSION`; no-op for images already within the cap — bounds the float64 LAB arrays that OOM'd the worker, D-012), `remove_background(pil_img)` (MediaPipe SelfieSegmentation in a `with` block so its native graph is released — D-011; feeds PIL's native RGB straight in, no cv2 channel swap — D-007; threshold pinned as `_SEGMENTATION_FG_THRESHOLD` — D-016), `background_color_budget(bg_idx, fg_mask_np, pct, unique_indices=None)` (pure helper: distinct LEGO colors to keep, scaled over the **background** region `fg_mask_np==0` — D-001), `simplify_background_lego(bg_idx, palette_lab, k, alpha_mask, unique_counts=None)` (per-unique-index remap → vectorized substitution; the optional `unique_indices`/`unique_counts` let the 3D call site share one `np.unique` between the two — D-042), `adjust_lightness_lab(img, delta_L)` (RGBA-preserving L* shift), `nearest_palette_index_lab(pixel_lab)`.
- `MosaicType` is imported from `.mosaic_types` and re-exported (so `from .picToMosiac import MosaicType` still works).
- Reads env on import: `MAX_MOSAIC_BLOCK_WIDTH` (40), `MAX_PROCESSING_DIMENSION` (2048). `STUDS_PER_BLOCK` (16) is imported from `.mosaic_types` (a fixed structural constant, re-exported here for back-compat — no longer the `STUD_WIDTH_OF_BLOCK` env var; D-035).

#### `MosiacToOrder.py` — order-list JSON writer

- Imports (package-relative): `from .Util import GetPaletteDict, SaveDictAsJsonsOptimized, log_info`.
- Public: `GenerateOrderList(fg_idx, fg_visible_mask, bg_idx, want_frame, output_dir)` — `output_dir` is **required** (the CLI fallback was removed, D-013). Counts the palette-**index** arrays the pipeline already computed via `np.bincount` and maps index→element_id through `PALETTE_ELEMENT_IDS` (D-036 — no RGB round-trip / `np.unique(axis=0)` sort). `bg_idx` is counted in full; `fg_idx` only where `fg_visible_mask` is True (None in 2D). Adds baseplate + optional frame parts, calls `Util.SaveDictAsJsonsOptimized` to write `{output_dir}/OrderLists/order_list.json` (splits into `order_list_1.json`, etc. when any qty > 999). Off-palette is now structurally impossible (every index is a valid slot); an out-of-range **index** still **raises** `RuntimeError` (successor to the D-008 guard) — it never silently drops bricks.
- Helpers: `GetBaseplatesForSize(width, height)`, `GetFrameForSize(width, height)` — return `{element_id: qty}` for structural parts. `BuildStatsPayload(order)` — pure: full order dict → `{"piece_counts": {element_id_str: qty}, "total_pieces": N}`, the stats.json body (written by `pic_to_mosaic`, non-fatally, right after `GenerateOrderList` in both branches).

#### `MosiacToInstruction.py` — instruction PDF assembler

- Imports: `.VisualMaker` (draw_*, save_img_and_increment_step, generate_baseplate_setup), `.Util` (log_*).
- Public: `GenerateInstructions(fg_rgba, bg_rgba, composite, want_frame, output_dir, progress_callback=None)` — `output_dir` is **required** (D-013). Validates inputs with explicit `raise` (not `assert`, D-023). Iterates baseplate blocks, draws each column twice (unhighlighted onto persistent canvas, then highlighted onto a copy saved as the step PNG). Emits grid-setup + frame steps + final-view step. Composes all PNGs into a single PDF via `images_to_pdf` (reportlab) at 612×792.
- Step counter `step` is still threaded through every call as a parameter and return value (antipattern; StepCounter refactor pending — D-025).

#### `VisualMaker.py` (1876 LOC) — isometric LEGO stud rendering primitives

- Imports: `.Util` (GetOutputPathDir, log_*).
- Constants: `PLATE_WIDTH=30`, `PLATE_HEIGHT=15`, `STARTING_X=50`, `STARTING_Y=400`, stud sizing constants. Page is 612×792 (US Letter @ 72dpi).
- Public surface used by `MosiacToInstruction`: `draw_final_view`, `generate_baseplate_setup`, `draw_plate_column`, `save_img_and_increment_step`, `draw_frame_instructions`, `draw_grid_setup_instruction`.
- Internal: `draw_plate`, `draw_baseplate_top/bottom`, `draw_stud_with_neck`, `draw_corner_brick`, `draw_brick`, `draw_corner_plate`, `draw_ortho_plate`, `draw_frame_setup_instruction`, `draw_frame_for_mosiac`, coordinate helpers (`get_block_xy`, `iso`, `to_pillow`, `to_rgb`), `get_img_and_draw`, `get_file_name`, `get_font`.
- `save_img_and_increment_step(img, step, output_dir)` draws the step number on `img.copy()` (does NOT mutate `img`) and writes `{output_dir}/Instructions/{step}.png`, returning `step+1`. This is the contract that lets `generate_baseplate_setup` return `(step, img)` so callers can reuse the canvas.

#### `mosaic_types.py` — shared enum leaf module

- Dependency-free. Defines `MosaicType.TWO_D = "2d"`, `MosaicType.THREE_D = "3d"`. Imported by `picToMosiac` (re-exported) and `preview_builder` (for `mosaic_type` validation), breaking what would otherwise be a circular import between those two (D-032). Also defines `STUDS_PER_BLOCK = 16` (D-035) — the fixed structural block edge, imported by `picToMosiac`/`Main`/`MosiacToOrder`/`MosiacToInstruction`/`VisualMaker` as the single source of truth (replacing the per-module literal `16` and the removed `STUD_WIDTH_OF_BLOCK` env var).

#### `Util.py` — palette + utility helpers

- Imports (package-relative): `from .logger import logger`.
- Public: `LEGO_PALETTE_RGB_DICT` (43 entries: `(R,G,B) → element_id`), `GetPaletteDict()`, `GetPaletteRGBArray()` (numpy uint8 array), `SaveDictAsJsonsOptimized(order_dict, output_path, max_per_item=999)`, `GetOutputPathDir()` (project_root/outputs; still used by `VisualMaker`), `load_project_env()` (loads `.env` and optional `.env.secrets`), `log_info/log_debug/log_error`.
- No env reads (the dead `DEBUG = bool(os.getenv("DEBUG"))` constant was removed — D-031).

#### `logger.py` — rotating file logger

- Public: `logger` (a `logging.Logger` named `"laigoLOG"`, `propagate=False`).
- Reads env on import: `LOG_FILE` (default `laigo.log`), `MAX_LOG_SIZE_MB` (default 10).
- Parent process: `RotatingFileHandler` to `<project_root>/<LOG_FILE>` (`backupCount=1`) **plus** a `StreamHandler`. Worker subprocesses (`multiprocessing.parent_process() is not None`): `StreamHandler` only — no file handle, so concurrent workers can't race the rotation (D-009).

#### `colorQuant.py` — standalone KMeans demo, NOT used by the pipeline.

---

### Shelved subsystem: automated checkout pipeline (`scripts/checkout/`)

The entire automated-ordering checkout pipeline is **shelved** under the
pay-what-you-want model (2026-06-13). Its routers (`checkout/router.py`,
`checkout/debug_router.py`) are no longer imported or mounted; the saga, the
two-pass optimizer, the marketplace clients (BrickOwl / LEGO / BrickLink), the
Stripe payment holds, the orphan-hold reconciler, and the Neon-backed
checkout/saga stores are all dormant.

Only three slivers of `scripts/checkout/` are still live in the PWYW product:

- `checkout/gate_router.py` -> `GET /checkout/gate` (operational visibility; always 200).
- `checkout/payment/` + `checkout/gate.py` -> the single-active StripeProvider
  registry, which powers the PWYW `pay` / `donate` endpoints (the saga's
  hold/capture/cancel methods are NOT used). See "Payment provider (active)" below.
- `checkout/cache.py` -> the in-process TTL cache sweeper (started in the lifespan).

**Re-enabling** the saga means restoring the shelved imports +
`app.include_router(...)` calls in `Main.py` and setting `CHECKOUT_ENABLED=true`
with `DB_BACKEND=postgres` (B47 refuses any other combination). The full design,
call graph, per-module interfaces, saga invariants, and provider-state contracts
for the shelved code live in the SHELVED docs `docs/ORDER_OPTIMIZER.md`,
`docs/CHECKOUT_AUDIT.md`, and `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` -- treat
those as re-enable references, not current runtime behavior.

**Cross-tree boundary (when re-enabled):** the only handoff is
`outputs/{job_id}/order_list.json`, which `worker.run_job` copies from the
workspace before deletion (it is also bundled into the downloadable
`artifact.zip`). The mosaic pipeline never imports the checkout pipeline.

## Known defects (mosaic pipeline)

Full per-defect documentation — root cause, reproduction, fix sketch,
verification, and cross-references — lives in
`docs/MOSAIC_DEFECTS.md`. The table below is the index only; click an
ID to jump to the entry.

> **Status (2026-06-30):** The 24 defects closed in `fix/mosaic-defects-sweep`
> (commit `6a3b1fa`) plus 14 more fixed in the 2026-06-29 performance &
> interface-redundancy sweep have been **pruned** from the ledger per its
> open-only convention (git history is the archive — `git log -p -- docs/MOSAIC_DEFECTS.md`).
> The table below is the remaining open / deferred / wontfix work;
> `docs/MOSAIC_DEFECTS.md` carries the authoritative per-defect detail.

For the shelved checkout pipeline's historical defect ledger, see the SHELVED
docs `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` and `docs/CHECKOUT_AUDIT.md`.

| ID | Severity | Status | Title | File |
|----|----------|--------|-------|------|
| [D-040](docs/MOSAIC_DEFECTS.md#d-040) | **P2** | open | Timeout watchdog can't reclaim the pool worker (ghost slot) | Main.py |
| [D-041](docs/MOSAIC_DEFECTS.md#d-041) | **P2** | deferred (bundle with D-025) | Per-step PNG written to disk then re-decoded for the PDF | VisualMaker / MosiacToInstruction |
| [D-044](docs/MOSAIC_DEFECTS.md#d-044) | **P2** | wontfix-by-design (A/B: 38–47% studs change) | Redundant LAB↔RGB round-trips (the deferred CPU-fold lever) | picToMosiac.py |
| [D-017](docs/MOSAIC_DEFECTS.md#d-017) | **P3** | open | `pic_to_mosaic` returns `None`; caller masks with `or workspace` | picToMosiac.py / worker.py |
| [D-018](docs/MOSAIC_DEFECTS.md#d-018) | **P3** | open | `give_exception_message` is in-band log-and-reraise | picToMosiac.py |
| [D-024](docs/MOSAIC_DEFECTS.md#d-024) | **P3** | open | Inconsistent `step` return shape across instruction helpers | MosiacToInstruction.py + VisualMaker.py |
| [D-025](docs/MOSAIC_DEFECTS.md#d-025) | **P3** | open | `step` counter threaded through every function as a return value | MosiacToInstruction.py + VisualMaker.py |
| [D-026](docs/MOSAIC_DEFECTS.md#d-026) | **P3** | open | `_mark_submission_failed` does a redundant `rmtree(job_root)` | Main.py |
| [D-029](docs/MOSAIC_DEFECTS.md#d-029) | **P3** | open | `FRONTEND_ORIGIN` env var set but never read | Main.py |
| [D-047](docs/MOSAIC_DEFECTS.md#d-047) | **P3** | open | `colorQuant.py` executes at import (no `__main__` guard) | colorQuant.py |
| [D-048](docs/MOSAIC_DEFECTS.md#d-048) | **P3** | open | Scattered env reads; `RENDER` idiom diverges; `.env` loaded twice | Main.py / picToMosiac.py |
| [D-049](docs/MOSAIC_DEFECTS.md#d-049) | **P3** | open | Job `settings` dict crosses 3 boundaries with no schema | Main.py / worker.py |
| [D-050](docs/MOSAIC_DEFECTS.md#d-050) | **P3** | open | Page-geometry magic numbers duplicated (`612×792`) | VisualMaker / MosiacToInstruction |
| [D-051](docs/MOSAIC_DEFECTS.md#d-051) | **P3** | open | `pic_to_mosaic(block_width=...)` actually carries studs | picToMosiac.py / worker.py |
| [D-052](docs/MOSAIC_DEFECTS.md#d-052) | **P3** | open | Minor cluster (no-op / dead / duplication / taxonomy) | various |

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
| 3 | `picToMosiac.py:adjust_lightness_lab` | Full-res RGB→LAB→RGB round-trip before resize. Memory now bounded by `MAX_PROCESSING_DIMENSION`; removing the round-trip (the CPU-fold) is **wontfix** — D-044 A/B changed 38–47% of studs. |
| 4 | `picToMosiac.py:remove_background` | MediaPipe SelfieSegmentation on megapixel image; mask only needs mosaic resolution |
| 5 | `MosiacToInstruction.py` instruction loop | Each column drawn TWICE (unhighlighted + highlighted) |
| 6 | `Main.py` `max_tasks_per_child=1` | Worker cold-starts every job. The FastAPI/checkout/asyncpg re-import is **fixed** (D-010 moved `run_job` to the leaf `worker.py`); residual cost is the `picToMosiac`/mediapipe import. Remaining lever: raise `max_tasks_per_child` so one worker serves several jobs. |
| 7 | `Main.py` `MAX_WORKERS=1` | No concurrency; second job waits |

### Open items — fixes + estimated impact

- **#1 Floyd-Steinberg:** KDTree over palette LAB (Euclidean ≈ deltaE76) → 25–35% total. Numba JIT → 50–60% total (200 MB dep). KDTree first; Numba only if insufficient. Cheap micro-optimization available before either: hoist the inner-loop error diffusion to plain Python floats and only `np.clip` in chunks — `np.clip` on a 1-element scalar is dominated by numpy's per-call overhead.
- **#2 `adjust_lightness_lab`:** **wontfix (D-044).** Folding the +5 L\* shift into the post-resize LAB was implemented + A/B-tested (2026-06-29) and changed 38–47% of studs — dithering is chaotically sensitive to the reordered nonlinear shift. The full-res round-trip stays; its memory is bounded by the `MAX_PROCESSING_DIMENSION` cap.
- **#3 MediaPipe:** Downscale input to ~2× mosaic resolution before segmentation, upscale mask back. → 10–20% total.
- **#4 Double `draw_plate_column`:** Draw once, copy, apply only highlight outline to the copy via a new `draw_highlight_column` helper. → 12–18% total.
- **#5 Worker cold-start:** The structural fix shipped — `run_job` is now in the leaf `scripts/worker.py` (D-010), and the process-safe logging prerequisite is done (D-009 parent-only file handler). Remaining lever: raise `max_tasks_per_child` to 3–5 so one worker serves several jobs (memory bounded by `MAX_WORKERS × peak-RSS`; Python 3.12+ enforced by the boot guard).
- **#6 Single-worker concurrency:** Raise `MAX_WORKERS` (now read from env — D-028) to 2. Memory ≈ 1.15 GB peak. Requires Render Standard tier (2 GB). Logging prerequisite (D-009) already satisfied.
- **(shipped) preview grid materialization:** `_grid_to_python_ints` now returns `remapped.tolist()` (D-003) — a single C-level call, 10–50× faster than the old double-comprehension on 640×640 grids.
- **(shipped 2026-06-29) smaller per-job wins:** `get_font` is `lru_cache`d (D-039 — was 1000+ TTF parses/job); `count_colors` uses a packed 1-D `np.unique` instead of the `axis=0` lexsort ×2/job (D-043); `simplify_background_lego` batches its per-index ΔE into one call (D-053). All byte-identical-verified.

### CPU reduction estimates (cumulative)

| Changes applied | Estimated single-job CPU |
|-----------------|--------------------------|
| Baseline | ~70% |
| + MediaPipe resolution fix (item #3) | ~55–62% |
| + Double draw_plate_column fix (item #4) | ~42–52% |
| + KDTree nearest-color (item #1 partial) | ~28–38% |
| + max_workers=2 + max_tasks_per_child=3 | two jobs at ~25–35% each |

> Note: the old top row "+ `adjust_lightness_lab` fix (item #2) → ~58–62%" was
> removed — that fold is **wontfix** (D-044). Figures above are rough, independent
> estimates re-baselined off ~70%.

---

## Antipatterns

### `step` counter threaded as a return value through every function
Every instruction function takes `step: int`, returns `step + N`, and the caller must capture it. Any refactor that adds or reorders steps silently produces wrong step numbers. A stateful counter object would make this contract explicit.

### Inconsistent return shape across instruction helpers (`VisualMaker.py`, `MosiacToInstruction.py`)
Related but distinct from the `step` antipattern: `generate_baseplate_setup` returns `(step, img)` but `draw_grid_setup_instruction`, `draw_frame_instructions`, and `draw_final_view` return only `step`. Every caller has to remember which functions return what. A stateful counter object (or a small mutable wrapper passed by reference) would unify the shape and close the original antipattern.

### `FRONTEND_ORIGIN` env var set but never read (`Main.py`)
CORS origins are hardcoded in `app.add_middleware(...)`. Either wire the env var or remove it from `.env`.

### `give_exception_message` is an in-band log-and-reraise (`picToMosiac.py`)
```python
def give_exception_message(e):
    tb = traceback.extract_tb(e.__traceback__)[-1]
    log_error(f"[ERROR] {type(e).__name__}: {e}")
    log_error(f"File: {tb.filename}")
    ...
    raise
```
The bare `raise` re-raises the current exception of the calling frame, which works only because every call site is inside an `except` block — fragile contract. Also formats only the last frame instead of the full traceback. `logger.exception(...)` already emits class + message + full traceback in one call. Replace with `log.exception("pic_to_mosaic failed"); raise` at each call site and delete this helper.

### CLI/API duality decaying in shared utility modules (partially resolved)
The top-level CLI path was removed (D-013): `GenerateOrderList` / `GenerateInstructions` now **require** `output_dir`, the `empty_*_folder` helpers and the `picToMosiac.__main__` block are gone. What remains is the **leaf** `output_dir is None` fallback to `GetOutputPathDir()` inside `VisualMaker.py` (`get_file_name`) and `GenerateBasePlateInstructions` — never reached now that the top-level callers require a real dir. That residue is folded into the StepCounter refactor (D-025, Wave 8), which rewrites those signatures anyway.

---

## Payment provider (active -- powers pay/donate)

The PWYW `pay` / `donate` / `webhooks/stripe` endpoints use Stripe via the
single-active provider registry -- the same `StripeProvider` + `payment.registry`
+ `gate.py` machinery the shelved saga used, minus the hold/capture/cancel
methods. Still load-bearing for the live product:

- `payment.registry` is single-active; `StripeProvider` is constructed and
  registered exactly once, in the `Main.py` lifespan. Do NOT instantiate it
  elsewhere -- `stripe.api_key` is a process-global. `LAIGO_ALLOW_REGISTRY_REPLACE=1`
  is TEST-ONLY (production MUST NOT set it).
- Do not import `stripe` outside `checkout/payment/stripe_provider.py`.
- Stripe key validation (test/live + >=8 chars beyond the prefix) lives in
  `checkout/payment/key_format.py:key_mode(key)`; `gate.py`, `stripe_provider.py`,
  and the `Main.py` L1 boot block all import it -- do not add a fourth divergent check.
- Secrets live in `.env.secrets` (gitignored): `STRIPE_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET`, and `RESEND_API_KEY` (build-pack emails). The
  `whsec_...` signing secret is required by
  `POST /webhooks/stripe` (get it from the Stripe dashboard, or from
  `stripe listen` for local testing) — and the webhook is also what emails
  the build pack after 3DS completions, so it matters even under PWYW.

## Boot invariants (`scripts/Main.py` lifespan, in order)

Every boot runs these; the Postgres/saga steps no-op under `DB_BACKEND=json`.

1. `verify_alembic_head_matches_expected()` -- code-time drift check; runs unconditionally (even on json).
2. `init_pool()` -- DB connectivity; no-op on json.
3. `verify_schema()` -- `alembic_version` vs `_EXPECTED_SCHEMA_VERSION`; no-op on json.
4. Capture the event loop on the jobs_store dispatcher.
5. StripeProvider registration (powers pay/donate; `PaymentProviderUnavailable` is non-fatal -- the gate decides).
6. Gate computation + L1 boot invariants: (a) `CHECKOUT_ENABLED=true` + gate DISABLED -> refuse; (b) `sk_live_` key outside Render -> refuse; (c) B47: `CHECKOUT_ENABLED=true` + `DB_BACKEND!=postgres` -> refuse.
7. `resume_in_flight_sagas()` -- routes non-terminal sagas to terminal; no-op on json.
8. ProcessPoolExecutor + scheduler + cleanup threads + cache sweeper start.
9. `start_reconcile_task()` -- periodic orphan-hold reconcile; no-op on json.

All boot refusals raise `RuntimeError` after `log.critical(...)`. Fail loud, fail early.

## Shelved checkout -- re-enable reference

The automated checkout/marketplace/saga code under `scripts/checkout/` (saga,
optimizer, marketplace clients, payment holds, reconciler, Neon-backed stores)
is shelved -- see "Shelved subsystem: automated checkout pipeline" above. Its
saga invariants, provider hold/capture rules, DB-layer contracts, audit-log
conventions, and public API contracts apply ONLY when re-enabling it, and live
in the SHELVED docs below. Do not treat them as current behavior.

## Where to find what

**Active (current product):**

- **3D preview API + payload schema (frontend-facing):** `docs/PREVIEW_API.md`.
- **Build-pack email delivery (Resend) — design, email.json schema, go-live runbook:** `docs/EMAIL_DELIVERY.md`.
- **Piece price table (powers `GET /jobs/{id}/stats`):** `scripts/piece_prices.json` — element_id → US cents, `null` = unknown (estimate goes null until all 62 are filled). To refresh: edit the values + bump `as_of`; no code change and no server restart (`load_price_table` invalidates its cache on the file's mtime — uvicorn `--reload` only watches `.py` files). Deliberately static — no cron/live fetch (LEGO's price API is Cloudflare-fronted with drifting field names, and structural parts aren't on Pick-a-Brick).
- **Backend switch runbook (JSON <-> Neon):** `docs/BACKEND_SWITCHING.md` -- how to flip `DB_BACKEND` in both directions, locally and on Render (env vars, pre-deploy command, the `override=False` precedence gotcha, B47, verification signals).
- **Mosaic pipeline defects ledger:** `docs/MOSAIC_DEFECTS.md` -- per-defect root cause, reproduction, fix sketch, verification. Index mirrored in "Known defects (mosaic pipeline)" above.

**Shelved / dormant (re-enable reference only -- each carries a banner):**

- **Checkout module reference:** `docs/ORDER_OPTIMIZER.md`.
- **Checkout FMEA + audit history:** `docs/CHECKOUT_AUDIT.md`.
- **Pre-launch payment gating checklist:** `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md`.
- **Neon DB operations + debug playbook:** `docs/DATABASE_OPS.md` (dormant -- applies whenever the Postgres backend is active).
- **LEGO.com Playwright session operator reference:** `docs/LEGO_SESSION.md`.
- **LEGO browser host (VPS + residential proxy, Cloudflare bypass):** `docs/LEGO_BROWSER_HOST.md`.
