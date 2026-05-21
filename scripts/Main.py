# run with: uvicorn scripts.Main:app --reload    (from project root)

import multiprocessing as mp
mp.set_start_method("spawn", force=True)

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import uuid
import os
import shutil
import time
import threading
import json
import traceback
import logging
import sys
from PIL import Image
from .picToMosiac import pic_to_mosaic, MosaicType, MAX_BLOCK_WIDTH, MIN_BLOCK_WIDTH
from .Util import load_project_env
from .checkout.router import checkout_router
from .checkout.debug_router import debug_router
from .checkout.gate_router import checkout_gate_router
from .checkout.cache import start_cache_sweeper
from .checkout.gate import compute_decision, is_truthy, CheckoutMode
from . import jobs_store_dispatch as jobs_store
import gc

# -----------------------------
# LOGGING SETUP
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("laigo")

# -----------------------------
# ENV SETUP
# -----------------------------
# is_truthy not is-None: RENDER="false" should NOT skip local .env loading
# (otherwise an operator with a stale RENDER=false in their shell would silently
# bypass .env loading). Same defect class as D1 in CHECKOUT_AUDIT.md §10.
# is_truthy was imported on line 30 via .checkout.gate; it lives in
# .checkout._env (dependency-free) and is safe to call before .env is loaded.
if not is_truthy(os.getenv("RENDER")):
    load_project_env()

INPUT_DIR = Path(os.getenv("INPUT_DIR", "./inputs")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", 600))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", 300))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", 1800))
# Keep single-worker behavior for now; queueing controls waiting jobs.
MAX_WORKERS = 1
MAX_QUEUE_SIZE = 20
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK", 16))
upload_mbs = int(os.getenv("MAX_UPLOAD_SIZE_MB", 250))
MAX_UPLOAD_SIZE = upload_mbs * 1024 * 1024

# How long the scheduler sleeps when no work is available, in seconds. The
# scheduler loop polls `jobs_store.dequeue_next()` rather than blocking on a
# `queue.Queue` (S6 — replaced the legacy queue in Phase D step 2). 0.5s
# matches the previous queue.get(timeout=0.5) behavior so dispatch latency is
# unchanged from the customer's perspective.
SCHEDULER_IDLE_POLL_SECONDS = 0.5

# Fail loudly on startup if directories can't be created or written to
try:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    log.critical(f"Failed to create required directories: {e}")
    raise

for _dir, _name in [(INPUT_DIR, "INPUT_DIR"), (OUTPUT_DIR, "OUTPUT_DIR")]:
    _probe = _dir / ".write_probe"
    try:
        _probe.write_text("ok")
        _probe.unlink()
    except Exception as e:
        log.critical(f"{_name} ({_dir}) is not writable: {e}")
        raise RuntimeError(f"{_name} is not writable") from e

log.info(f"INPUT_DIR:        {INPUT_DIR}")
log.info(f"OUTPUT_DIR:       {OUTPUT_DIR}")
log.info(f"MAX_WORKERS:      {MAX_WORKERS}")
log.info(f"MAX_QUEUE_SIZE:   {MAX_QUEUE_SIZE}")
log.info(f"JOB_TTL_SECONDS:  {JOB_TTL_SECONDS}")
log.info(f"CLEANUP_INTERVAL: {CLEANUP_INTERVAL}")
log.info(f"JOB_TIMEOUT:      {JOB_TIMEOUT_SECONDS}s")
log.info(f"MAX_UPLOAD_MB:    {upload_mbs}")
log.info(f"STUDS_PER_BLOCK:  {STUDS_PER_BLOCK}")


# -----------------------------
# FASTAPI LIFECYCLE
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting LAIGO API...")

    # ─────────────────────────────────────────────────────────────────────────
    # DB POOL (Phase 9 — Neon Postgres migration).
    #
    # init_pool() is a no-op when DB_BACKEND != postgres (default 'json'), so
    # local dev without a provisioned Neon project still boots. When
    # DB_BACKEND=postgres, the call refuses to boot on missing/malformed
    # DATABASE_URL or wrong endpoint (direct vs pooler). Pool must exist
    # BEFORE payment registration, gate computation, or any router can serve
    # — checkout_store and the jobs lifecycle (Phase D step 2) acquire from it.
    # See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.2.4.
    # ─────────────────────────────────────────────────────────────────────────
    from .db import (
        init_pool,
        close_pool,
        is_postgres_backend,
        verify_schema,
        verify_alembic_head_matches_expected,
    )

    # B43 — fail fast on code drift between scripts/db.py:_EXPECTED_SCHEMA_VERSION
    # and the head of scripts/migrations/versions/. Pure local check; no DB
    # connection required. Runs UNCONDITIONALLY (even on the JSON path) since
    # it's a code-time invariant. Catches the "added/removed a migration but
    # forgot to bump the constant" bug at boot — including local dev where
    # verify_schema() is a no-op because DB_BACKEND=json.
    verify_alembic_head_matches_expected()

    await init_pool()
    if is_postgres_backend():
        log.info("DB pool initialized (Neon Postgres backend active)")
    else:
        log.info("DB pool skipped (DB_BACKEND=json; Phase F not yet flipped)")

    # Phase B step 5 — refuse boot if the deployed app expects a different
    # alembic revision than what's actually in the DB. No-op on the JSON path.
    # See docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5.B step 5.
    await verify_schema()
    if is_postgres_backend():
        log.info("DB schema verified (alembic revision matches _EXPECTED_SCHEMA_VERSION)")

    # Capture the FastAPI event loop so the scheduler / cleanup / executor-
    # callback threads can submit coroutines to the loop. The dispatcher fans
    # the loop ref out to both backends (PG + JSON) so flipping DB_BACKEND
    # mid-process during tests keeps the wrappers working.
    loop = asyncio.get_running_loop()
    jobs_store.set_event_loop(loop)
    app.state.event_loop = loop

    # ─────────────────────────────────────────────────────────────────────────
    # PAYMENT PROVIDER REGISTRATION (Layer 5).
    #
    # Construct + register the active PaymentProvider BEFORE computing the
    # gate decision. compute_decision() consults the registry to determine
    # whether checkout can be opened; if registration fails here, the gate
    # reports DISABLED with a clear reason and L1 (below) refuses boot when
    # CHECKOUT_ENABLED=true.
    #
    # PaymentProviderUnavailable is a *configuration-level* failure, not a
    # boot blocker on its own — local dev with no Stripe key should be able
    # to start the server and serve /quote (read-only) while leaving
    # /confirm gated. Only the L1 block below decides whether that
    # combination should refuse boot.
    # ─────────────────────────────────────────────────────────────────────────
    from .checkout.payment import registry as payment_registry
    from .checkout.payment.base import PaymentProviderUnavailable
    from .checkout.payment.stripe_provider import StripeProvider
    try:
        payment_registry.register(StripeProvider())
    except PaymentProviderUnavailable as exc:
        log.warning(f"Payment provider not registered: {exc}")
        # Continue boot — the gate's L1 block decides if this is fatal.

    # ─────────────────────────────────────────────────────────────────────────
    # CHECKOUT GATE — Layer 1: refuse to boot if checkout is misconfigured.
    # See scripts/checkout/gate.py for the full defense-in-depth design.
    #
    # This block enforces three safety invariants at startup:
    #   (a) If CHECKOUT_ENABLED=true, the gate MUST compute TEST or LIVE.
    #       If env says "enabled" but reality says DISABLED (missing key,
    #       wrong prefix, no marketplace creds, provider registration
    #       failed), refuse to boot — better to have the server down than
    #       to accept /confirm without payment.
    #   (b) A LIVE Stripe key outside the Render environment is forbidden,
    #       regardless of the master flag. This catches the case where a
    #       live key leaks into a dev .env.secrets file by accident.
    #       (Note: StripeProvider.__init__ refuses to construct in this
    #       case, so registration above already failed and the gate is
    #       already DISABLED. The explicit check here is defense-in-depth
    #       — if registration somehow succeeded with a live key outside
    #       Render via a future code path, this still refuses boot.)
    #   (c) If CHECKOUT_ENABLED=true, DB_BACKEND MUST be 'postgres'.
    #       The JSON backend has an open B23 defect (concurrent /confirm
    #       with different checkout_ids for the same job_id clobbers
    #       saga state). Postgres mode closes the gap structurally via the
    #       sagas_one_active_per_job_idx partial unique index. Flipping
    #       DB_BACKEND back to json with CHECKOUT_ENABLED=true would
    #       silently re-open the race in production. (B47 fix; tracked in
    #       PRE_RELEASE §4.)
    # ─────────────────────────────────────────────────────────────────────────
    gate_decision = compute_decision()
    log.info(f"Checkout gate: mode={gate_decision.mode.value} "
             f"payment_provider={gate_decision.payment_provider} "
             f"marketplaces_live={list(gate_decision.marketplaces_live)}")
    for reason in gate_decision.reasons:
        log.info(f"  gate reason: {reason}")

    if is_truthy(os.environ.get("CHECKOUT_ENABLED")) and gate_decision.mode == CheckoutMode.DISABLED:
        log.critical(
            "CHECKOUT_ENABLED=true but gate computed DISABLED. "
            f"Reasons: {'; '.join(gate_decision.reasons)}. "
            "Refusing to boot — fix the configuration or unset CHECKOUT_ENABLED."
        )
        raise RuntimeError("Checkout gate misconfigured — see CRITICAL log line above")

    # B47 — invariant (c): CHECKOUT_ENABLED requires DB_BACKEND=postgres.
    # is_postgres_backend re-reads env so this matches the actual runtime
    # backend selection rather than a cached snapshot. Order matters:
    # check after (a) so the operator sees gate diagnostics first, but
    # before the L1 LIVE-key check (b) so this fires regardless of key mode.
    if is_truthy(os.environ.get("CHECKOUT_ENABLED")) and not is_postgres_backend():
        log.critical(
            "CHECKOUT_ENABLED=true but DB_BACKEND != 'postgres' "
            f"(actual: {os.environ.get('DB_BACKEND', 'json')!r}). "
            "The JSON checkout backend has an open B23 defect (concurrent "
            "/confirm with different checkout_ids for the same job_id clobbers "
            "saga state). Postgres mode closes this via the partial unique "
            "index. Refusing to boot — either set DB_BACKEND=postgres OR "
            "unset CHECKOUT_ENABLED until cutover."
        )
        raise RuntimeError("B47: DB_BACKEND must be 'postgres' when CHECKOUT_ENABLED=true")

    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    # B39: use the canonical key_mode helper so this defense-in-depth check
    # agrees with gate.py and stripe_provider.py. The previous lenient
    # `startswith("sk_live_")` could mis-classify a too-short malformed key as
    # "live" (StripeProvider's strict ≥8-char check would have rejected it
    # entirely). With the canonical helper, all three layers (L1 boot,
    # L0 gate, L5 provider) classify keys identically.
    # is_truthy (not raw truthiness): RENDER="false"/"0"/"no" must NOT count
    # as "we're on Render." Defect D1 in CHECKOUT_AUDIT.md §10.
    from .checkout.payment.key_format import key_mode as _stripe_key_mode
    if _stripe_key_mode(stripe_key) == "live" and not is_truthy(os.environ.get("RENDER")):
        log.critical(
            "Live Stripe key (sk_live_...) detected outside the Render environment. "
            "This is never allowed — use sk_test_... for local development. "
            "Refusing to boot."
        )
        raise RuntimeError("Live Stripe key outside Render is forbidden")

    # ─────────────────────────────────────────────────────────────────────────
    # SAGA RESUME-ON-STARTUP (Phase E step 1).
    #
    # The behavior the entire DB migration exists for. Inspect every saga
    # that was in a non-terminal state when the process died, and route each
    # to a safe terminal state (FAILED or MANUAL_REVIEW). Audit FMEA #3
    # ("Saga crashes mid-flight, no resumption", RPN 450) is closed by this
    # single call.
    #
    # Order: AFTER `init_pool()` (above), `verify_schema()`, and payment
    # provider registration (so `stripe_held` recovery can find the
    # provider); BEFORE the executor + scheduler + cleanup threads start
    # (so resume completes before any new work begins).
    #
    # No-op when DB_BACKEND != postgres. The JSON-mode runtime has no
    # sagas table to recover from.
    #
    # Per-saga recovery failures are swallowed inside `resume_in_flight_sagas`
    # (logged CRITICAL) so a single stuck row cannot block boot. A connection-
    # level failure on the top-level fetch DOES propagate — if Neon goes
    # unreachable between `init_pool`'s SELECT 1 and resume's fetch, the
    # lifespan fails and uvicorn refuses to boot. That's intentional: the
    # whole app needs DB; partial-up is worse than fail-loud.
    # ─────────────────────────────────────────────────────────────────────────
    if is_postgres_backend():
        from .checkout.saga_resume import resume_in_flight_sagas
        await resume_in_flight_sagas()

    try:
        # max_tasks_per_child=1: worker exits and is respawned after every job,
        # releasing all memory (numpy, skimage, PIL, cv2, mediapipe, palette globals)
        # back to the OS. Requires Python 3.12+.
        # Progress is tracked via small files on disk; no Manager is needed.
        app.state.executor = ProcessPoolExecutor(
            max_workers=MAX_WORKERS,
            max_tasks_per_child=1
        )

        # Runtime-only side tables (Phase D step 2 — persistent state moved
        # to jobs_store dispatcher; only un-serializable / transient refs
        # remain on app.state).
        #
        # progress  : job_id → Path of the worker's .progress file. The file
        #             stays the source of truth for the worker subprocess
        #             (which has no DB connection); the scheduler tick mirrors
        #             into the store on every iteration (S5).
        # futures   : job_id → concurrent.futures.Future returned by
        #             executor.submit. Cannot be persisted; also the sentinel
        #             that arbitrates the done-callback vs timeout-watchdog
        #             race (whoever pops first owns the cleanup).
        # deadlines : job_id → wallclock float when JOB_TIMEOUT_SECONDS expires.
        #             Computed at dispatch and consulted by the watchdog. Not
        #             persisted — a server restart restarts the deadline
        #             (Phase E resume-on-restart will surface long-running
        #             pre-restart jobs explicitly).
        # intake    : job_id → full settings dict captured at /generate. Used
        #             by the timeout watchdog to write `settings` into
        #             `manifest_failed.json` for operator forensics. The store
        #             only persists the typed columns (mosaic_type, width_blocks,
        #             background_pct, dither); `to_frame` is not in the schema,
        #             so intake preserves it for the manifest-write path.
        app.state.progress = {}
        app.state.progress_lock = threading.Lock()
        app.state.futures: dict[str, object] = {}
        app.state.deadlines: dict[str, float] = {}
        app.state.intake: dict[str, dict] = {}

        # Scheduler synchronization — same primitives, retained verbatim.
        # active_jobs is the in-memory MAX_WORKERS gate; we deliberately do
        # not delegate this to jobs_store.count_active() because the
        # increment/decrement must be transactional with the cv.wait() loop,
        # and an async round-trip per scheduler tick would needlessly bound
        # dispatch latency on a hot path.
        app.state.scheduler_shutdown = threading.Event()
        app.state.scheduler_cv = threading.Condition()
        app.state.active_jobs = 0

        log.info(f"ProcessPoolExecutor started with {MAX_WORKERS} worker(s), max_tasks_per_child=1")
    except Exception as e:
        log.critical(f"Failed to initialise executor: {e}", exc_info=True)
        raise

    try:
        scheduler_thread = threading.Thread(target=scheduler_loop, args=(app,), daemon=True)
        scheduler_thread.start()
        app.state.scheduler_thread = scheduler_thread
        log.info("Scheduler thread started")
    except Exception as e:
        log.critical(f"Failed to start scheduler thread: {e}", exc_info=True)
        raise

    try:
        cleanup_thread = threading.Thread(target=cleanup_loop, args=(app,), daemon=True)
        cleanup_thread.start()
        log.info("Cleanup thread started")
    except Exception as e:
        log.critical(f"Failed to start cleanup thread: {e}", exc_info=True)
        raise

    start_cache_sweeper()
    log.info("Cache sweeper started")

    # ─────────────────────────────────────────────────────────────────────────
    # ORPHAN-HOLD RECONCILER (Phase E step 2, 2026-05-19).
    #
    # Periodic task that detects divergence between our `payment_holds.
    # last_known_status` and the provider's view (Stripe). Catches out-of-band
    # operator actions (capture/cancel from the Stripe dashboard) and stuck
    # sagas that resume-on-startup missed.
    #
    # No-op when DB_BACKEND != postgres OR no PaymentProvider is registered
    # (gate closed). The task is held by a module-level strong reference in
    # `scripts/checkout/reconcile.py` per the C1/B24 GC-resilience pattern.
    # ─────────────────────────────────────────────────────────────────────────
    from .checkout.reconcile import start_reconcile_task
    start_reconcile_task()
    log.info("Reconcile periodic task started (or skipped per DB_BACKEND)")

    log.info("LAIGO API ready")
    yield

    log.info("Shutting down LAIGO API...")
    try:
        app.state.scheduler_shutdown.set()
        with app.state.scheduler_cv:
            app.state.scheduler_cv.notify_all()

        scheduler_thread = getattr(app.state, "scheduler_thread", None)
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=5)

        app.state.executor.shutdown()
        log.info("Executor shut down cleanly")
    except Exception as e:
        log.error(f"Error during shutdown: {e}", exc_info=True)

    # H6 (B17 followup): clear the single-active payment provider registry on
    # shutdown so a second lifespan run in the same Python process (TestClient
    # used twice, future hot-reload tooling) doesn't hit the B17 replacement
    # guard and crash startup. Production today (uvicorn child-per-lifespan)
    # is unaffected either way; this just removes a latent test-framework
    # crash. The function is safe in production: it clears a module-level
    # `_active` and nothing else.
    try:
        from .checkout.payment import registry as payment_registry
        payment_registry._reset_for_tests()
    except Exception:
        log.debug("registry reset on shutdown skipped (already cleared)")

    # Drop the event-loop reference so any worker thread still finishing late
    # doesn't try to submit to a dead loop. Idempotent. Fans out to both
    # backends in the dispatcher.
    try:
        jobs_store.clear_event_loop()
    except Exception:
        log.debug("jobs_store event-loop clear skipped")

    # Stop the reconcile periodic task BEFORE close_pool — the task uses the
    # asyncpg pool; if we close the pool first, its in-flight queries would
    # raise on closed-pool errors mid-shutdown. 5-second cancel timeout
    # protects shutdown from a wedged tick.
    try:
        from .checkout.reconcile import stop_reconcile_task
        await stop_reconcile_task(timeout=5.0)
    except Exception:
        log.debug("reconcile task stop skipped (never started or already done)")

    # DB pool LAST — every other subsystem may still want to write a final
    # audit row, flush a state checkpoint, etc. close_pool() is idempotent
    # and a no-op when init_pool() was skipped (DB_BACKEND=json).
    try:
        await close_pool()
        log.info("DB pool closed cleanly")
    except Exception as e:
        log.error(f"Error closing DB pool: {e}", exc_info=True)


app = FastAPI(lifespan=lifespan)
app.include_router(checkout_router, prefix="/jobs")
app.include_router(debug_router)
app.include_router(checkout_gate_router)

try:
    app.mount("/artifacts", StaticFiles(directory=OUTPUT_DIR), name="artifacts")
except Exception as e:
    log.critical(f"Failed to mount /artifacts static files: {e}", exc_info=True)
    raise

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://laigo-frontend.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# GLOBAL EXCEPTION HANDLER
# Catches any unhandled exception in a route so it never fails silently
# -----------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error(
        f"Unhandled exception on {request.method} {request.url}: "
        f"{type(exc).__name__}: {exc}",
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"}
    )


# -----------------------------
# SHARED HELPERS
# -----------------------------
def _dt_to_epoch(dt) -> float | None:
    """Convert a tz-aware datetime (asyncpg / jobs_store_json native) to epoch
    seconds for the legacy /jobs/{id} response shape. Returns None unchanged."""
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        return float(dt)
    return dt.timestamp()


def _store_row_to_response(row: dict, settings: dict | None) -> dict:
    """Translate a jobs_store dict (16 canonical columns) into the legacy
    /jobs/{id} response shape the frontend has historically consumed.

    Rules:
    - `timed_out` is normalized to `failed` so the frontend's "your job
      didn't complete" branch handles both. The internal DB column keeps
      the distinct value for operator triage.
    - Timestamps converted from datetime to epoch float.
    - `settings` is the full intake dict (preserves `to_frame`, which is
      NOT a column in the jobs table). Reconstructed from typed columns
      when the runtime intake mapping has been cleared (e.g., after a
      terminal transition + intake pop).
    """
    raw_status = row["status"]
    response_status = "failed" if raw_status == "timed_out" else raw_status

    if settings is None:
        settings = {
            "mosaic_block_width": row.get("width_blocks"),
            "mosaic_type": row.get("mosaic_type"),
            "background_color_percent": row.get("background_pct"),
            # `to_frame` is not persisted — fall back to the API default.
            "to_frame": True,
        }

    response: dict = {
        "status": response_status,
        "progress": int(row.get("progress_pct") or 0),
        "created_at": _dt_to_epoch(row.get("queued_at")),
        "queued_at": _dt_to_epoch(row.get("queued_at")),
        "settings": settings,
    }
    started_at = _dt_to_epoch(row.get("started_at"))
    if started_at is not None:
        response["started_at"] = started_at
    completed_at = _dt_to_epoch(row.get("completed_at"))
    if completed_at is not None:
        response["finished_at"] = completed_at
    if row.get("error_message"):
        response["error"] = row["error_message"]
    return response


async def _queue_position_async(job_id: str) -> tuple[int | None, int]:
    """Find the 1-based position of `job_id` in the queued list and the
    total queue length. Returns (None, length) if the job isn't queued.

    Backed by `list_queued()` (ordered by queued_at) — O(N) for N≤20.
    """
    queued_rows = await jobs_store.list_queued()
    for idx, row in enumerate(queued_rows):
        if row["job_id"] == job_id:
            return idx + 1, len(queued_rows)
    return None, len(queued_rows)


def _write_error_manifest(job_root: Path, job_id: str, settings: dict,
                           error: str, tb: str) -> None:
    """Write failure details to disk so get_job can serve them after the job
    is evicted from the store. Accepts pre-captured traceback string so this
    can safely be called outside an except block."""
    try:
        error_info = {
            "status": "failed",
            "progress": 0,
            "job_id": job_id,
            "error": error,
            "traceback": tb,
            "finished_at": time.time(),
            "settings": settings,
        }
        error_path = job_root / "manifest_failed.json"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump(error_info, f, indent=2)
    except Exception as write_err:
        print(
            f"[ERROR] Could not write error manifest for job {job_id}: {write_err}",
            file=sys.stderr, flush=True
        )


def _run_coro_blocking(coro, loop: asyncio.AbstractEventLoop, timeout: float = 30.0):
    """Submit a coroutine to the FastAPI event loop from a non-async thread
    and BLOCK until it returns (or times out).

    Distinct from `jobs_store.*_from_thread` wrappers (fire-and-forget). Use
    this when the caller needs the return value — `dequeue_next()` in the
    scheduler, `cleanup_expired()` in the cleanup loop. The timeout caps the
    wait so a wedged event loop doesn't hang the worker thread forever.
    """
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


# -----------------------------
# WORKER FUNCTION
# -----------------------------
def run_job(job_id: str,
            image_path: str,
            settings: dict,
            output_root: str,
            studs_per_block: int,
            progress_path: str) -> dict:
    """
    Runs in a separate spawned process (max_tasks_per_child=1 so it exits
    after this returns, freeing all memory at the OS level).
    Progress is written to a small file on disk rather than a Manager dict,
    since the Manager process has been eliminated.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] worker: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    wlog = logging.getLogger(f"laigo.worker.{job_id[:8]}")
    wlog.info(f"Job {job_id} started | settings: {settings}")

    OUTPUT_DIR_LOCAL = Path(output_root)
    job_root = OUTPUT_DIR_LOCAL / job_id
    workspace = job_root / "workspace"
    progress_file = Path(progress_path)
    last_update_time = 0

    def write_progress(pct):
        nonlocal last_update_time
        now = time.time()
        if now - last_update_time >= 2:
            try:
                progress_file.write_text(str(pct))
            except Exception as prog_err:
                wlog.warning(f"Job {job_id} could not write progress: {prog_err}")
            last_update_time = now

    def delete_progress():
        try:
            progress_file.unlink(missing_ok=True)
        except Exception:
            pass

    def fail(msg: str, tb: str) -> dict:
        """Single failure path: cleans up workspace, input file, and progress
        file, writes the error manifest, and returns the failure dict."""
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass
        delete_progress()
        _write_error_manifest(job_root, job_id, settings, msg, tb)
        gc.collect()
        return {
            "status": "failed",
            "progress": 0,
            "error": msg,
            "traceback": tb,
            "finished_at": time.time(),
        }

    # --- Create workspace ---
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} failed to create workspace {workspace}: {e}", exc_info=True)
        return fail(f"Could not create workspace: {e}", tb)

    # --- Parse settings ---
    try:
        width = int(settings["mosaic_block_width"]) * studs_per_block
        mosaic_type = MosaicType(settings["mosaic_type"])
        background_pct = float(settings["background_color_percent"])
        to_frame = bool(settings["to_frame"])
    except (KeyError, ValueError, TypeError) as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} has invalid settings: {e}", exc_info=True)
        return fail(f"Invalid job settings: {e}", tb)

    # --- Run mosaic generation ---
    try:
        wlog.info(f"Job {job_id} calling pic_to_mosaic | width={width} type={mosaic_type}")
        result_dir = pic_to_mosaic(
            Path(image_path),
            width,
            mosaic_type,
            background_pct,
            to_frame,
            output_dir=workspace,
            job_id=job_id,
            progress_callback=write_progress,
        ) or workspace
        wlog.info(f"Job {job_id} pic_to_mosaic complete | result_dir={result_dir}")
    except Exception as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} pic_to_mosaic raised: {type(e).__name__}: {e}", exc_info=True)
        return fail(str(e), tb)

    # --- Write success manifest (non-fatal if it fails) ---
    try:
        manifest = {
            "schema": "laigo.manifest.v1",
            "job_id": job_id,
            "created_at": time.time(),
            "settings": settings,
        }
        with open(result_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        wlog.info(f"Job {job_id} manifest written")
    except Exception as e:
        wlog.error(f"Job {job_id} failed to write manifest: {e}", exc_info=True)

    # --- Archive workspace ---
    try:
        archive_base = job_root / "artifact"
        wlog.info(f"Job {job_id} archiving to {archive_base}.zip")
        shutil.make_archive(str(archive_base), "zip", result_dir)
        wlog.info(f"Job {job_id} archive created")
    except Exception as e:
        tb = traceback.format_exc()
        wlog.error(f"Job {job_id} failed to create archive: {e}", exc_info=True)
        return fail(f"Archive creation failed: {e}", tb)

    # Copy order_list.json to a stable location before workspace deletion so the
    # checkout optimizer can read it after the workspace is gone.
    _order_list_src = workspace / "OrderLists" / "order_list.json"
    if _order_list_src.exists():
        try:
            shutil.copy2(_order_list_src, job_root / "order_list.json")
        except Exception as e:
            wlog.warning(f"Job {job_id} could not copy order_list.json: {e}")

    # Copy preview.json (3D-preview payload for the frontend) to the stable
    # location before workspace deletion. Mirrors the order_list handoff above.
    _preview_src = workspace / "preview.json"
    if _preview_src.exists():
        try:
            shutil.copy2(_preview_src, job_root / "preview.json")
        except Exception as e:
            wlog.warning(f"Job {job_id} could not copy preview.json: {e}")

    # --- Success cleanup ---
    shutil.rmtree(workspace, ignore_errors=True)

    try:
        Path(image_path).unlink(missing_ok=True)
    except Exception as e:
        wlog.warning(f"Job {job_id} could not delete input file {image_path}: {e}")

    delete_progress()

    del result_dir
    gc.collect()

    wlog.info(f"Job {job_id} complete")
    return {
        "status": "complete",
        "finished_at": time.time(),
    }


# -----------------------------
# SCHEDULER HELPERS
# -----------------------------
def _drop_runtime_state(app: FastAPI, job_id: str, *, drop_intake: bool = False) -> None:
    """Pop runtime-only side-table entries for a job. Idempotent.

    Called from terminal paths (done-callback, watchdog, submission-failure)
    AND from cleanup at TTL eviction. The store row is owned by the
    dispatcher; this only clears un-serializable / transient refs.

    `drop_intake` is False for terminal transitions because the `/jobs/{id}`
    contract surfaces `settings` until TTL eviction (preserves `to_frame`,
    which is not a column in the jobs table, and float precision on
    background_pct that would be lost on reconstruction from the SMALLINT
    column). cleanup_loop passes True after the row is gone.
    """
    app.state.futures.pop(job_id, None)
    app.state.deadlines.pop(job_id, None)
    if drop_intake:
        app.state.intake.pop(job_id, None)
    with app.state.progress_lock:
        progress_file = app.state.progress.pop(job_id, None)
    if progress_file:
        try:
            Path(progress_file).unlink(missing_ok=True)
        except Exception:
            pass


def _mark_submission_failed(app: FastAPI, job_id: str, error: str, tb: str) -> None:
    """Handles failures AFTER `dequeue_next()` has flipped the row to
    'running' but BEFORE the worker future actually exists.

    Two callers today: (a) scheduler sees shutdown after claiming a row,
    (b) `executor.submit` raises. In both cases the row is at 'running'
    in the store; we must roll it forward to 'failed' so the row doesn't
    linger and so /jobs/{id} surfaces the failure. Worker artifacts (input
    file, partial workspace) are best-effort cleaned.

    Settings come from `app.state.intake[job_id]`, which was populated at
    /generate and survives past the dispatch step.
    """
    settings = app.state.intake.get(job_id, {})
    job_root = OUTPUT_DIR / job_id

    image_path = INPUT_DIR / f"{job_id}.upload"
    progress_path = INPUT_DIR / f"{job_id}.progress"

    shutil.rmtree(job_root / "workspace", ignore_errors=True)
    shutil.rmtree(job_root, ignore_errors=True)

    try:
        image_path.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        progress_path.unlink(missing_ok=True)
    except Exception:
        pass

    _write_error_manifest(job_root, job_id, settings, error, tb)

    _drop_runtime_state(app, job_id)

    # Persistent terminal write. Fire-and-forget from this thread — the
    # `*_from_thread` wrapper logs CRITICAL on failure but never raises here.
    jobs_store.mark_failed_from_thread(job_id, error)

    log.error(f"Job {job_id} failed before execution: {error}")


def _process_terminal(app: FastAPI, job_id: str, future) -> None:
    """Persist the terminal outcome of a worker future. Caller must have
    already claimed ownership via an atomic `app.state.futures.pop()`.

    Two callers: `_job_done_callback` (normal path) and `_check_timed_out_jobs`
    when it observes a future that completed concurrently. Both must end up
    at the same store state, run `_drop_runtime_state`, and decrement
    `active_jobs` exactly once.

    Worker subprocess crashes (`future.result()` raises rather than returns
    a failure dict) get a synthetic manifest_failed.json here — the worker
    never got to write one itself, so without this the failure would
    disappear after TTL eviction.
    """
    worker_wrote_manifest = True
    try:
        result = future.result()
        if not isinstance(result, dict):
            result = {
                "status": "failed",
                "progress": 0,
                "error": f"Worker returned unexpected result type: {type(result).__name__}",
                "traceback": "",
                "finished_at": time.time(),
            }
            worker_wrote_manifest = False  # malformed return — manifest absent
    except Exception as e:
        tb = traceback.format_exc()
        result = {
            "status": "failed",
            "progress": 0,
            "error": f"Worker raised unexpectedly: {e}",
            "traceback": tb,
            "finished_at": time.time(),
        }
        worker_wrote_manifest = False  # subprocess crashed before fail() could fire

    status = result.get("status")
    if status == "complete":
        jobs_store.mark_complete_from_thread(job_id)
    else:
        if not worker_wrote_manifest:
            # Subprocess crash or malformed return — synthesize the manifest
            # so /jobs/{id} can still serve forensics after the store row
            # gets TTL-evicted.
            settings = app.state.intake.get(job_id, {})
            _write_error_manifest(
                OUTPUT_DIR / job_id, job_id, settings,
                result["error"], result["traceback"],
            )
        error_msg = result.get("error") or "Worker failed without error message"
        jobs_store.mark_failed_from_thread(job_id, error_msg)

    _drop_runtime_state(app, job_id)

    with app.state.scheduler_cv:
        app.state.active_jobs = max(0, app.state.active_jobs - 1)
        app.state.scheduler_cv.notify_all()


def _check_timed_out_jobs(app: FastAPI, slog) -> None:
    """Force-fail any running job whose deadline has passed.

    `app.state.deadlines` is the runtime-only watchdog ledger populated at
    dispatch time. `app.state.futures.pop(job_id)` is the arbitration
    sentinel: whoever pops the future first (timeout vs done-callback)
    owns the cleanup. The other side sees `None` and returns.

    If the pop wins the race but the future is actually already done (worker
    finished right at the deadline boundary), we OWN the cleanup — we cannot
    reinsert and defer to the done-callback because the callback fires only
    once and, having seen our pop return None for it, will short-circuit.
    Instead we route through `_process_terminal`, which is the same logic
    the done-callback would have run.
    """
    now = time.time()
    # Snapshot the deadlines map — the done-callback (running in the
    # executor's management thread) can pop entries via _drop_runtime_state
    # at any moment, and iterating a `dict.items()` view while another
    # thread mutates the dict raises `RuntimeError: dictionary changed
    # size during iteration`. `list(...)` materializes the items into a
    # fresh list of tuples in one atomic-w.r.t.-GIL step.
    candidates = [
        (jid, dl) for jid, dl in list(app.state.deadlines.items()) if now > dl
    ]

    for job_id, _deadline in candidates:
        future = app.state.futures.pop(job_id, None)
        if future is None:
            # done-callback already finalized this job.
            continue
        if future.done():
            # Worker finished naturally; we beat the done-callback to the
            # claim. Process completion ourselves — see docstring for why
            # reinserting would leak the future.
            _process_terminal(app, job_id, future)
            continue

        slog.error(f"Job {job_id} exceeded timeout of {JOB_TIMEOUT_SECONDS}s — forcing failure")
        tb_str = f"Job exceeded timeout of {JOB_TIMEOUT_SECONDS}s"
        settings = app.state.intake.get(job_id, {})
        _write_error_manifest(OUTPUT_DIR / job_id, job_id, settings, "Job timed out", tb_str)

        # Distinct DB status='timed_out' so operators triage stuck jobs
        # separately from clean failures. /jobs/{id} normalizes back to
        # 'failed' for the frontend.
        jobs_store.mark_timed_out_from_thread(
            job_id, f"Job timed out after {JOB_TIMEOUT_SECONDS}s"
        )

        _drop_runtime_state(app, job_id)

        with app.state.scheduler_cv:
            app.state.active_jobs = max(0, app.state.active_jobs - 1)
            app.state.scheduler_cv.notify_all()


def _job_done_callback(app: FastAPI, job_id: str, future) -> None:
    """Finalize a completed worker future in the main process.

    This is the canonical place where running -> complete/failed is finalized.
    `app.state.futures.pop` is the arbitration sentinel against the timeout
    watchdog — if it returns None, the watchdog already finalized this job.
    """
    if app.state.futures.pop(job_id, None) is None:
        # Timeout watchdog already finalized this job and wrote the terminal
        # state. Do not double-decrement active_jobs.
        return
    _process_terminal(app, job_id, future)


def _mirror_progress_files(app: FastAPI) -> None:
    """S5 — read each running job's .progress file and mirror to the store.

    Called from the scheduler tick. The worker subprocess owns the file (no
    DB connection per §9.3.11.10); the main process is the only writer of
    `jobs.progress_pct`. We skip the store write when nothing changed to
    avoid noise.

    Lives next to the dispatch loop because the scheduler thread already
    has the event-loop ref it needs for `*_from_thread` submissions.
    """
    # Snapshot the futures map — iteration vs concurrent mutation by
    # done-callback / watchdog otherwise.
    for job_id in list(app.state.futures.keys()):
        with app.state.progress_lock:
            progress_file = app.state.progress.get(job_id)
        if not progress_file or not os.path.exists(progress_file):
            continue
        try:
            pct = int(float(Path(progress_file).read_text().strip()))
        except Exception:
            continue
        # write_progress is guarded by status='running' in both backends, so
        # a write that races a terminal transition silently no-ops.
        jobs_store.write_progress_from_thread(job_id, pct)


def scheduler_loop(app: FastAPI):
    """Dispatch queued jobs into the process pool, bounded by MAX_WORKERS.

    Phase D step 2 S6 — replaced `queue.Queue.get(timeout=0.5)` with
    `jobs_store.dequeue_next()` (a single transaction in PG mode using
    `SELECT FOR UPDATE SKIP LOCKED`; a single-lock scan-and-flip in JSON
    mode). The dispatcher is the only source of "what's next" — no
    secondary in-memory queue can drift from the store.

    Each tick:
      1. Run the timeout watchdog (`_check_timed_out_jobs`).
      2. Mirror running jobs' .progress files into the store (S5).
      3. Wait if we're at MAX_WORKERS; otherwise try to claim one queued
         row. If no work is available, sleep briefly and loop.
    """
    slog = logging.getLogger("laigo.scheduler")

    while True:
        if app.state.scheduler_shutdown.is_set():
            break

        _check_timed_out_jobs(app, slog)
        _mirror_progress_files(app)

        # Wait briefly if we're at MAX_WORKERS. CRITICAL: use `if`, not `while`.
        # The legacy code used a `while` here, which trapped the scheduler in
        # an inner cv.wait loop until active_jobs dropped below MAX_WORKERS —
        # meaning the timeout watchdog (called at the TOP of the outer loop)
        # never fired while a long-running worker was in flight. A runaway
        # worker that never finished would never trip its deadline. Using
        # `if` lets every cv timeout (every SCHEDULER_IDLE_POLL_SECONDS) re-
        # tick the outer loop, re-running both `_check_timed_out_jobs` and
        # `_mirror_progress_files`. The cost is a tiny amount of extra
        # bookkeeping per tick; the win is a watchdog that actually watches.
        with app.state.scheduler_cv:
            if not app.state.scheduler_shutdown.is_set() and app.state.active_jobs >= MAX_WORKERS:
                app.state.scheduler_cv.wait(timeout=SCHEDULER_IDLE_POLL_SECONDS)
            at_capacity = app.state.active_jobs >= MAX_WORKERS

        if app.state.scheduler_shutdown.is_set():
            break

        if at_capacity:
            # Still at MAX_WORKERS — skip the dequeue attempt this tick. The
            # next iteration's `_check_timed_out_jobs` may free up a slot,
            # and a real done-callback decrement will notify the cv and wake
            # us up early.
            continue

        try:
            claimed = _run_coro_blocking(
                jobs_store.dequeue_next(), app.state.event_loop, timeout=10.0
            )
        except Exception as e:
            # Transient DB hiccup or run-coro timeout. Log, sleep, retry —
            # not fatal; the next tick will try again.
            slog.error(f"dequeue_next failed: {type(e).__name__}: {e}", exc_info=True)
            with app.state.scheduler_cv:
                if not app.state.scheduler_shutdown.is_set():
                    app.state.scheduler_cv.wait(timeout=SCHEDULER_IDLE_POLL_SECONDS)
            continue

        if claimed is None:
            # Queue empty — wait briefly for new work. The cv is notified by
            # /generate so a fresh insert wakes us promptly.
            with app.state.scheduler_cv:
                if not app.state.scheduler_shutdown.is_set():
                    app.state.scheduler_cv.wait(timeout=SCHEDULER_IDLE_POLL_SECONDS)
            continue

        job_id = claimed["job_id"]

        # Reconstruct the executor input from runtime side tables + canonical
        # input paths. dequeue_next() returned a row already at 'running'; if
        # any of these lookups fail, we must roll the row forward to 'failed'.
        settings = app.state.intake.get(job_id)
        if settings is None:
            slog.error(f"Job {job_id} has no intake settings — rolling forward to failed")
            _mark_submission_failed(
                app,
                job_id,
                "Job state lost between /generate and dispatch",
                "intake side table missing settings dict",
            )
            continue

        image_path = INPUT_DIR / f"{job_id}.upload"
        progress_path = INPUT_DIR / f"{job_id}.progress"

        if app.state.scheduler_shutdown.is_set():
            _mark_submission_failed(
                app,
                job_id,
                "Server is shutting down",
                "Scheduler stopped after claiming job; before dispatch",
            )
            continue

        with app.state.scheduler_cv:
            # Re-check the MAX_WORKERS gate; a concurrent dispatch may have
            # bumped active_jobs since the outer wait. With MAX_WORKERS=1 the
            # outer loop's wait is sufficient, but the recheck future-proofs
            # for the optional MAX_WORKERS>1 follow-up.
            while not app.state.scheduler_shutdown.is_set() and app.state.active_jobs >= MAX_WORKERS:
                app.state.scheduler_cv.wait(timeout=SCHEDULER_IDLE_POLL_SECONDS)
            if app.state.scheduler_shutdown.is_set():
                _mark_submission_failed(
                    app,
                    job_id,
                    "Server is shutting down",
                    "Scheduler stopped before executor.submit",
                )
                continue
            app.state.active_jobs += 1

        try:
            future = app.state.executor.submit(
                run_job,
                job_id,
                str(image_path),
                settings,
                str(OUTPUT_DIR),
                STUDS_PER_BLOCK,
                str(progress_path),
            )
        except Exception as e:
            tb = traceback.format_exc()
            slog.error(f"Failed to submit job {job_id} to executor: {e}", exc_info=True)
            with app.state.scheduler_cv:
                app.state.active_jobs = max(0, app.state.active_jobs - 1)
                app.state.scheduler_cv.notify_all()
            _mark_submission_failed(app, job_id, f"Failed to dispatch job: {e}", tb)
            continue

        # Runtime side tables: future for the done-callback / watchdog
        # arbitration, deadline for the watchdog.
        app.state.futures[job_id] = future
        app.state.deadlines[job_id] = time.time() + JOB_TIMEOUT_SECONDS

        future.add_done_callback(lambda fut, jid=job_id: _job_done_callback(app, jid, fut))
        with app.state.scheduler_cv:
            active_jobs = app.state.active_jobs
        slog.info(f"Dispatched job {job_id} to executor | active_jobs={active_jobs}")


# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health")
async def health():
    return {"status": "running"}


@app.get("/")
async def root():
    return {"status": "running", "message": "LAIGO API online. Use /health or /docs for info."}


@app.get("/queue")
async def queue_status():
    """Operator/debug surface — current queue + worker headroom.

    `complete` and `failed` counts are not reported here because the store
    deletes terminal rows after JOB_TTL_SECONDS; long-running counts would
    be misleading. Frontend status polling reads /jobs/{id} for per-job
    state, not /queue.
    """
    queued_count = await jobs_store.count_queued()
    running_count = await jobs_store.count_active()
    queued_rows = await jobs_store.list_queued()
    queued_job_ids = [r["job_id"] for r in queued_rows]

    with app.state.scheduler_cv:
        active_jobs = app.state.active_jobs

    return {
        "queued_jobs": queued_count,
        "queued_job_ids": queued_job_ids,
        "max_queue_size": MAX_QUEUE_SIZE,
        "active_jobs": active_jobs,
        "max_workers": MAX_WORKERS,
        "counts": {
            "queued": queued_count,
            "running": running_count,
        },
    }


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    mosaic_block_width: int = Form(...),
    mosaic_type: str = Form(...),
    background_color_percent: float = Form(100),
    to_frame: bool = Form(True),
):
    # B54 — validate inputs BEFORE anything else (including the shutdown check)
    # so callers with bad input get "fix your request" (422) rather than "retry
    # later" (503) for a request that would never succeed regardless of server
    # state. Previously any width was accepted; widths above MAX_BLOCK_WIDTH
    # allocated huge memory in the worker and made progress at <1% before the
    # 30-min timeout watchdog eventually killed them. We had the constants
    # (MIN_BLOCK_WIDTH=1, MAX_BLOCK_WIDTH from env, default 40) but never
    # enforced them. Doing it at the API layer saves a worker subprocess spawn
    # + the in-memory + DB round-trip for an obviously-invalid request.
    if not (MIN_BLOCK_WIDTH <= mosaic_block_width <= MAX_BLOCK_WIDTH):
        raise HTTPException(
            status_code=422,
            detail=(
                f"mosaic_block_width must be between {MIN_BLOCK_WIDTH} and "
                f"{MAX_BLOCK_WIDTH} (inclusive); got {mosaic_block_width}. "
                f"Each block is {STUDS_PER_BLOCK}x{STUDS_PER_BLOCK} studs."
            ),
        )

    # MosaicType is a str-enum ("2d"/"3d"); reject anything else with a clear
    # error rather than letting the worker fail mid-flight on a ValueError.
    try:
        MosaicType(mosaic_type)
    except ValueError:
        valid = ", ".join(repr(m.value) for m in MosaicType)
        raise HTTPException(
            status_code=422,
            detail=f"mosaic_type must be one of {valid}; got {mosaic_type!r}.",
        )

    # background_color_percent is a percentage (0-100); the worker's
    # simplify_background_lego treats it as a fraction. Negative or >100 values
    # produce nonsense color counts.
    if not (0.0 <= background_color_percent <= 100.0):
        raise HTTPException(
            status_code=422,
            detail=(
                f"background_color_percent must be between 0 and 100 (inclusive); "
                f"got {background_color_percent}."
            ),
        )

    # Shutdown check runs AFTER input validation so callers with bad input get
    # the actionable 422 rather than a 503 they'd retry forever.
    if app.state.scheduler_shutdown.is_set():
        raise HTTPException(status_code=503, detail="Server is shutting down")

    # Queue-full check BEFORE upload — saves the bytes-on-disk cost when we'd
    # reject anyway. Small TOCTOU race window (two concurrent /generate both
    # passing the check at queue_size=19, ending at 21) is tolerable for a
    # 20-item operator queue; the dispatcher's queue-size docstring documents
    # the choice in detail.
    if await jobs_store.count_queued() >= MAX_QUEUE_SIZE:
        raise HTTPException(status_code=429, detail="Queue full. Try again once space opens.")

    job_id = str(uuid.uuid4())
    log.info(
        f"Job {job_id} received | filename={file.filename} "
        f"width={mosaic_block_width} type={mosaic_type} "
        f"bg_pct={background_color_percent} frame={to_frame}"
    )

    input_file = INPUT_DIR / f"{job_id}.upload"

    size = 0
    try:
        with open(input_file, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    f.close()
                    input_file.unlink(missing_ok=True)
                    await file.close()
                    log.warning(f"Job {job_id} rejected — file too large ({size} bytes)")
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Job {job_id} failed during file upload: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    await file.close()
    log.info(f"Job {job_id} file saved | size={size} bytes")

    try:
        with Image.open(input_file) as img:
            img.verify()
    except Exception as e:
        log.warning(f"Job {job_id} rejected — invalid image: {type(e).__name__}: {e}")
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file")

    settings = {
        "mosaic_block_width": mosaic_block_width,
        "mosaic_type": mosaic_type,
        "background_color_percent": background_color_percent,
        "to_frame": to_frame,
    }

    progress_file = INPUT_DIR / f"{job_id}.progress"
    try:
        progress_file.write_text("0")
    except Exception as e:
        log.error(f"Job {job_id} failed to create progress file: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to initialise job")

    # Side tables FIRST, then the persistent insert. Reversing this order
    # opens a race: between `insert_queued` returning and `intake[jid]` being
    # set, the scheduler thread can call `dequeue_next()`, claim the row,
    # look up `intake.get(jid) → None`, and roll the job forward to 'failed'
    # via the "no intake settings" defensive branch. With the side tables
    # populated first, the scheduler can never see a 'queued' row without
    # also seeing its intake/progress. The defensive branch remains useful
    # only for post-restart recovery (queued row in DB, intake empty because
    # the process restarted) — exactly when we DO want a fail-forward.
    app.state.intake[job_id] = settings
    with app.state.progress_lock:
        app.state.progress[job_id] = progress_file

    try:
        await jobs_store.insert_queued(
            job_id=job_id,
            mosaic_type=mosaic_type,
            width_blocks=mosaic_block_width,
            background_pct=background_color_percent,
            ttl_seconds=JOB_TTL_SECONDS,
            upload_filename=file.filename,
        )
    except Exception as e:
        # Roll back the side tables so a failed insert doesn't leak entries
        # for a job that never made it to the store. The scheduler can't see
        # the job (no row), but a future /jobs/{id} lookup would otherwise
        # find stale intake + return a synthetic response.
        app.state.intake.pop(job_id, None)
        with app.state.progress_lock:
            app.state.progress.pop(job_id, None)
        log.error(f"Job {job_id} failed to persist: {e}", exc_info=True)
        input_file.unlink(missing_ok=True)
        progress_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to register job: {e}")

    # Wake the scheduler so dispatch latency stays sub-tick when the worker
    # is idle. Without this the scheduler would poll on its own
    # SCHEDULER_IDLE_POLL_SECONDS timeout (still correct, just slower).
    with app.state.scheduler_cv:
        app.state.scheduler_cv.notify_all()

    log.info(f"Job {job_id} queued successfully")
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    row = await jobs_store.get_job(job_id)

    if row is None:
        # Cleanup may have removed the row but left manifest_failed.json on
        # disk briefly (worker writes it inside the output dir, which cleanup
        # rmtrees together with the row delete — so this fallback is rare in
        # the new design). Preserved for parity with the legacy contract.
        error_manifest = OUTPUT_DIR / job_id / "manifest_failed.json"
        if error_manifest.exists():
            log.info(f"Job {job_id} served from error manifest on disk")
            try:
                with open(error_manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data
            except Exception as e:
                log.error(f"Job {job_id} failed to read error manifest: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Failed to read error manifest")
        log.warning(f"Job {job_id} not found in store or on disk")
        raise HTTPException(status_code=404, detail="Job not found")

    settings = app.state.intake.get(job_id)
    response = _store_row_to_response(row, settings)

    # While queued/running, mirror the latest .progress file value through to
    # the response so the frontend's polled progress is sub-tick fresh. The
    # store's progress_pct is updated by the scheduler tick (S5), which lags
    # the file by up to SCHEDULER_IDLE_POLL_SECONDS — fine for operator views
    # but visible as "stuck at 42%" to a fast-polling customer.
    raw_status = row["status"]
    if raw_status in ("queued", "running"):
        with app.state.progress_lock:
            progress_file = app.state.progress.get(job_id)
        if progress_file and os.path.exists(progress_file):
            try:
                response["progress"] = int(float(Path(progress_file).read_text().strip()))
            except Exception:
                pass

    if raw_status == "complete":
        response["progress"] = 100
    elif raw_status in ("failed", "timed_out"):
        response["progress"] = 0

    if raw_status == "queued":
        queue_position, queue_length = await _queue_position_async(job_id)
        response["queue_position"] = queue_position
        response["queue_length"] = queue_length

    return response


@app.get("/jobs/{job_id}/preview")
async def get_job_preview(job_id: str):
    """Return the 3D-preview payload for a completed job.

    File existence is the sole readiness signal — the frontend polls
    /jobs/{job_id} for status, and only fetches /preview once status==complete.
    The bytes are read into memory and returned via Response (NOT FileResponse)
    so the file descriptor closes immediately, avoiding races with cleanup_loop
    rmtreeing the job directory on Windows.
    """
    preview_path = OUTPUT_DIR / job_id / "preview.json"
    try:
        data = preview_path.read_bytes()
    except FileNotFoundError:
        log.info(f"Preview requested for job {job_id} but file not found")
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Preview is not available for this job.",
                "code": "PREVIEW_NOT_AVAILABLE",
            },
        )
    except Exception as e:
        log.error(f"Preview file unreadable for job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Preview file is unreadable.",
                "code": "PREVIEW_CORRUPTED",
            },
        )
    return Response(content=data, media_type="application/json")


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    artifact = OUTPUT_DIR / job_id / "artifact.zip"

    if not artifact.exists():
        log.warning(f"Download requested for job {job_id} but artifact not found")
        raise HTTPException(status_code=404, detail="Artifact not found")

    log.info(f"Serving artifact for job {job_id}")
    return FileResponse(
        artifact,
        filename=f"mosaic_{job_id}.zip",
        media_type="application/zip",
    )


# -----------------------------
# CLEANUP THREAD
# -----------------------------
def cleanup_loop(app: FastAPI):
    """Periodically reap expired terminal jobs.

    Phase D step 2 S7 — delegates the "find expired terminals" scan to
    `jobs_store.cleanup_expired()`, which returns the list of deleted
    job_ids. We then rmtree each output directory and drop any leftover
    runtime side-table entries (defensive: terminal transitions already
    clean these, but a server restart that interrupted a terminal path
    could leave stragglers).
    """
    clog = logging.getLogger("laigo.cleanup")
    while True:
        try:
            deleted_ids = _run_coro_blocking(
                jobs_store.cleanup_expired(),
                app.state.event_loop,
                timeout=30.0,
            )
            for jid in deleted_ids:
                try:
                    shutil.rmtree(OUTPUT_DIR / jid, ignore_errors=True)
                except Exception as e:
                    clog.error(
                        f"Failed to remove output dir for job {jid}: {e}",
                        exc_info=True
                    )
                # Final eviction — drop the intake side table here. Terminal
                # transitions intentionally leave intake in place so
                # /jobs/{id} keeps surfacing `settings` until TTL.
                _drop_runtime_state(app, jid, drop_intake=True)
                clog.info(f"Job {jid} cleaned up")
        except Exception:
            clog.error("Unexpected error in cleanup loop", exc_info=True)

        time.sleep(CLEANUP_INTERVAL)
