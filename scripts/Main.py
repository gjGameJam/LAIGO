# run with: uvicorn scripts.Main:app --reload    (from project root)

import multiprocessing as mp
# D-027: request spawn WITHOUT force=True. spawn is required because the pooled
# callable (worker.run_job) and its picToMosiac/mediapipe deps are not fork-safe
# on Linux. The old force=True ran on every `import scripts.Main` and reset the
# process-wide start method — hostile to test runners. Tolerate spawn already
# being set; fail loud only if an incompatible method is already active.
try:
    mp.set_start_method("spawn")
except RuntimeError:
    if mp.get_start_method() != "spawn":
        raise RuntimeError(
            f"scripts.Main requires multiprocessing start method 'spawn', got "
            f"{mp.get_start_method()!r}. Cannot continue."
        )

# ── Corporate TLS interception fix (local dev only) ───────────────────────────
# On dev machines behind a TLS-intercepting corporate proxy, Python's certifi
# bundle lacks the corp root CA, so outbound HTTPS (Stripe, etc.) fails with
# CERTIFICATE_VERIFY_FAILED. truststore makes Python use the OS trust store
# (which has the corp CA). Skipped on Render (RENDER set) — prod uses standard
# CAs and we don't alter its TLS path. Must run before any SSL context is
# created (stripe / httpx / asyncpg), hence this early placement.
import os
if not os.environ.get("RENDER"):
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception as _truststore_err:  # never block boot
        import logging
        logging.getLogger("laigo").warning(
            "truststore injection skipped (%s); outbound HTTPS may fail behind "
            "a TLS-intercepting proxy", _truststore_err,
        )

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pathlib import Path
import uuid
import os
import secrets
import shutil
import time
import threading
import json
import traceback
import logging
import sys
from PIL import Image
from .picToMosiac import MosaicType, MAX_BLOCK_WIDTH, MIN_BLOCK_WIDTH, STUDS_PER_BLOCK
# D-010: run_job + _write_error_manifest live in the leaf module scripts/worker.py
# (imports only picToMosiac + stdlib). ProcessPoolExecutor spawn-mode re-imports
# the callable's defining module in every worker; keeping run_job out of Main.py
# stops each spawn from re-importing the FastAPI + checkout + asyncpg tree.
from .worker import run_job, _write_error_manifest
from .Util import load_project_env
# NOTE: the checkout saga pipeline (checkout.router / debug_router — quote /
# confirm / status + marketplace ordering) is SHELVED. It stays on disk but is
# no longer imported or mounted. The build pack is now a pay-what-you-want
# digital product served by pay_router; see scripts/pay_router.py.
from .pay_router import pay_router, webhook_router, donate_router
from .pricing import load_price_table, estimate_cost_cents
from .checkout.gate_router import checkout_gate_router
from .checkout.cache import start_cache_sweeper
from .checkout.gate import compute_decision, is_truthy, CheckoutMode
from . import jobs_store_dispatch as jobs_store

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
_IS_RENDER = is_truthy(os.getenv("RENDER"))
if not _IS_RENDER:
    load_project_env()

INPUT_DIR = Path(os.getenv("INPUT_DIR", "./inputs")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()
# Per-job PII / financial sidecars (payment.json, email.json) live HERE, never
# in OUTPUT_DIR: OUTPUT_DIR is web-served via the /artifacts mount, so customer
# email + payment records must not sit inside it. pay_router.py and emailer.py
# resolve the same dir from PRIVATE_DIR; cleanup_loop purges it at job TTL.
PRIVATE_DIR = Path(os.getenv("PRIVATE_DIR", "./private")).resolve()

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", 600))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", 300))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", 1800))
# Retention for jobs with terminal sagas (B59). Default 90d is past the
# 60d chargeback dispute window, with a buffer. Set to a smaller value
# (e.g. 7) in staging if testing the cleanup path.
JOB_SAGA_RETENTION_DAYS = int(os.getenv("JOB_SAGA_RETENTION_DAYS", 90))
# D-028: read from env instead of hardcoding (the vars were documented + present
# in .env but ignored). Defaults preserve single-worker behavior. NOTE: raising
# MAX_WORKERS above 1 requires Render Standard tier (2 GB) — each worker holds
# mediapipe+numpy+PIL+cv2 (~400-550 MB RSS) — AND the logging/worker-isolation
# fixes (D-009/D-010/D-011). .env ships MAX_WORKERS=1 to keep the switch off.
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "1"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20"))
# D-035: STUDS_PER_BLOCK is imported from .picToMosiac (sourced from
# .mosaic_types) — a fixed structural constant, no longer the STUD_WIDTH_OF_BLOCK
# env var the downstream instruction/order code ignored.
upload_mbs = int(os.getenv("MAX_UPLOAD_SIZE_MB", 250))
MAX_UPLOAD_SIZE = upload_mbs * 1024 * 1024

# How long the scheduler sleeps when no work is available, in seconds. The
# scheduler loop polls `jobs_store.dequeue_next()` rather than blocking on a
# `queue.Queue` (S6 — replaced the legacy queue in Phase D step 2). 0.5s
# matches the previous queue.get(timeout=0.5) behavior so dispatch latency is
# unchanged from the customer's perspective.
SCHEDULER_IDLE_POLL_SECONDS = 0.5

# -----------------------------
# RATE LIMITING (per-IP, /generate only)
# -----------------------------
# /generate is the one heavy endpoint — it spawns a worker holding
# mediapipe+numpy+PIL. /pay and /donate are light Stripe calls and are
# deliberately NOT limited. One request per GENERATE_RATE_LIMIT_SECONDS per IP.
#
# State is in-process, which is correct ONLY because the web layer is a single
# uvicorn process (LAIGO runs one web process + a ProcessPoolExecutor for jobs;
# its own docs warn multi-worker breaks in-process state). If the Render start
# command ever grows `--workers N`, move this to a shared store (Redis). The key
# is the real client IP (resolved by real_ip_middleware) — there is no auth
# product, so IP is the only stable per-caller key available.
GENERATE_RATE_LIMIT_SECONDS = int(os.getenv("GENERATE_RATE_LIMIT_SECONDS", "20"))
_generate_rl_lock = threading.Lock()
_last_generate_by_ip: dict[str, float] = {}

# Bound concurrent /generate INTAKE (the streaming upload write + the full
# ~80 MP image decode in _validate_image) in the shared web process. The
# count_queued() cap only bounds DISPATCHED work — the store row isn't written
# until AFTER the decode — so without this a burst of concurrent uploads each
# streams up to MAX_UPLOAD_SIZE to disk and decodes ~0.5 GB of pixels in the
# web process before any queue rejection, exhausting disk / OOM-killing the web
# process on the 2 GB Standard tier. Default 3 keeps the transient cost bounded
# (≤3 concurrent writes and ≤3 concurrent decodes) while comfortably serving
# real single-product traffic; excess callers await a permit briefly. The
# semaphore is created lazily on first use so it binds to the running loop
# (avoids cross-loop issues under tests that spin up their own loops).
GENERATE_INTAKE_CONCURRENCY = int(os.getenv("GENERATE_INTAKE_CONCURRENCY", "3"))
_generate_intake_semaphore: "asyncio.Semaphore | None" = None


def _get_intake_semaphore() -> "asyncio.Semaphore":
    global _generate_intake_semaphore
    if _generate_intake_semaphore is None:
        _generate_intake_semaphore = asyncio.Semaphore(GENERATE_INTAKE_CONCURRENCY)
    return _generate_intake_semaphore


def _enforce_generate_rate_limit(ip: str | None) -> None:
    """Raise 429 (+ Retry-After) if `ip` did an allowed /generate within the
    cooldown. The cooldown runs from the last ALLOWED request, so a caller who is
    rejected does not extend their own lockout. No-op when `ip` is unknown (can't
    key it) — on Render X-Forwarded-For is always present so this is rare."""
    if not ip:
        return
    now = time.monotonic()
    with _generate_rl_lock:
        # Opportunistic sweep so the dict stays bounded by IPs-seen-in-the-window,
        # not total distinct IPs ever seen.
        expired = [k for k, t in _last_generate_by_ip.items()
                   if now - t >= GENERATE_RATE_LIMIT_SECONDS]
        for k in expired:
            del _last_generate_by_ip[k]
        last = _last_generate_by_ip.get(ip)
        if last is not None:
            retry_after = max(1, int(GENERATE_RATE_LIMIT_SECONDS - (now - last)) + 1)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit: one image per {GENERATE_RATE_LIMIT_SECONDS}s. "
                    f"Try again in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        _last_generate_by_ip[ip] = now

# Fail loudly on startup if directories can't be created or written to
try:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
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
log.info(f"JOB_SAGA_RETENTION_DAYS: {JOB_SAGA_RETENTION_DAYS}")
log.info(f"MAX_UPLOAD_MB:    {upload_mbs}")
log.info(f"STUDS_PER_BLOCK:  {STUDS_PER_BLOCK}")
log.info(f"EMAIL_ENABLED:    {os.getenv('EMAIL_ENABLED', 'false')}")
log.info(f"EMAIL_FROM:       {os.getenv('EMAIL_FROM', '(default)')}")


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

    # Build-pack email (scripts/emailer.py) — non-fatal config check. A
    # misconfigured emailer must never block boot: sends are fire-and-forget
    # and every skip is logged per-job, but a boot-time warning makes the
    # "why is nobody getting emails" case obvious in the startup log.
    if is_truthy(os.getenv("EMAIL_ENABLED")) and not os.getenv("RESEND_API_KEY", "").strip():
        log.warning(
            "EMAIL_ENABLED is true but RESEND_API_KEY is not set "
            "(.env.secrets / Render env) — build-pack emails will be skipped."
        )

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

    # D-030: max_tasks_per_child was added to ProcessPoolExecutor in Python 3.12.
    # On 3.12+ we set it to 1 so the worker exits and is respawned after every
    # job, releasing all memory (numpy, skimage, PIL, cv2, mediapipe, palette
    # globals) back to the OS. On 3.11 or earlier the kwarg raises an opaque
    # TypeError, so we omit it and log a warning — the server still runs, but the
    # single worker persists across jobs (no per-job memory reclaim). That's fine
    # for local dev; Render prod pins 3.12 via runtime.txt where the kwarg is on.
    # MediaPipe is context-managed (D-011), so its native graph is released each
    # job regardless of respawn — the only thing forgone on 3.11 is RSS reclaim.
    # Progress is tracked via small files on disk; no Manager is needed.
    executor_kwargs = {"max_workers": MAX_WORKERS}
    if sys.version_info >= (3, 12):
        executor_kwargs["max_tasks_per_child"] = 1
    else:
        log.warning(
            "Python %d.%d < 3.12: worker respawn disabled "
            "(max_tasks_per_child unavailable; no per-job memory reclaim). "
            "Fine for local dev; Render prod pins 3.12 via runtime.txt.",
            sys.version_info.major, sys.version_info.minor,
        )

    try:
        app.state.executor = ProcessPoolExecutor(**executor_kwargs)

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

        _respawn = "on" if "max_tasks_per_child" in executor_kwargs else "off"
        log.info(f"ProcessPoolExecutor started: {MAX_WORKERS} worker(s), respawn={_respawn}")
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


# Swagger UI (/docs), ReDoc (/redoc), and the raw OpenAPI schema (/openapi.json)
# enumerate the entire API surface. They're useful in dev but are a recon aid in
# prod, so disable them on Render. The RENDER switch mirrors the D-048 idiom used
# for .env loading above.
_docs_kwargs = (
    dict(docs_url=None, redoc_url=None, openapi_url=None) if _IS_RENDER else {}
)
app = FastAPI(lifespan=lifespan, **_docs_kwargs)
app.include_router(pay_router, prefix="/jobs")
app.include_router(webhook_router)
app.include_router(donate_router)  # global POST /donate (client-confirm tip)
app.include_router(checkout_gate_router)

class _AllowlistStaticFiles(StaticFiles):
    """Serve ONLY public build-pack files from OUTPUT_DIR.

    The output dir also holds per-job sidecars that must never be web-served —
    payment.json (amount + PaymentIntent id) and email.json (customer email
    PII), plus manifest_failed.json (tracebacks + the full settings dict). A raw
    StaticFiles mount served all of them to anyone holding a job_id. This
    allowlists known-safe basenames and 404s everything else, so any NEW sidecar
    added under outputs/ later is denied by default (fail closed). Traversal is
    already blocked by StaticFiles; this narrows *which* in-tree files are served.
    """

    _ALLOWED_NAMES = {"artifact.zip", "preview.json", "stats.json", "manifest.json"}

    @classmethod
    def _is_allowed(cls, rel_path: str) -> bool:
        name = Path(rel_path).name
        if name in cls._ALLOWED_NAMES:
            return True
        # order_list.json plus split order_list_1.json, order_list_2.json, ...
        return name.startswith("order_list") and name.endswith(".json")

    async def get_response(self, path, scope):
        if not self._is_allowed(path):
            return Response("Not Found", status_code=404)
        return await super().get_response(path, scope)


try:
    app.mount("/artifacts", _AllowlistStaticFiles(directory=OUTPUT_DIR), name="artifacts")
except Exception as e:
    log.critical(f"Failed to mount /artifacts static files: {e}", exc_info=True)
    raise

# The Vite dev origin (localhost:5173) is only needed for local frontend work;
# in prod (Render) the only legitimate browser origin is the deployed frontend.
_cors_origins = ["https://laigo-frontend.onrender.com"]
if not _IS_RENDER:
    _cors_origins.append("http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# TRUSTED HOST (Host-header defense)
# -----------------------------
# Prod-gated to avoid any chance of a local/dev outage: on Render, requests
# arrive at the service's *.onrender.com host (Render's assigned hostname and
# health checks both use it). If the API is ever fronted by a CUSTOM domain,
# add it via the TRUSTED_HOSTS env var (comma-separated) — otherwise those
# requests would 400. Off Render the middleware isn't installed at all.
if _IS_RENDER:
    _trusted_hosts = ["*.onrender.com", "localhost", "127.0.0.1", "testserver"]
    _render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if _render_host and _render_host not in _trusted_hosts:
        _trusted_hosts.append(_render_host)
    _trusted_hosts += [
        h.strip() for h in os.getenv("TRUSTED_HOSTS", "").split(",") if h.strip()
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)


# -----------------------------
# SECURITY HEADERS
# -----------------------------
# Defense-in-depth response headers on every reply. HSTS is honored only over
# HTTPS (Render terminates TLS); browsers ignore it on plain-HTTP local dev. The
# strict CSP is prod-only: locally /docs (Swagger) pulls its assets from a CDN
# and a `default-src 'none'` policy would break it — and /docs is disabled in
# prod anyway, where every response is JSON/zip/plain (no inline scripts).
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    if _IS_RENDER:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
    return response


# -----------------------------
# X-FORWARDED-FOR MIDDLEWARE (B60)
# -----------------------------
# Resolves the real customer IP from `X-Forwarded-For` and stashes it on
# `request.state.real_ip`. Read by checkout/dependencies.py for audit
# actor.ip; intended as the single source of truth for any future
# request-scope IP need (rate limiting, abuse detection, request logging).
#
# Trust model — single proxy: Render is the ONLY trusted hop in front of this
# app. CRITICAL: Render does NOT reset/clear a client-supplied X-Forwarded-For
# — it only APPENDS the real client IP to whatever the client sent (verified
# against Render's own feature board, "Send the correct X-Forwarded-For").
# So the LEFTMOST entry is attacker-controllable: a client can send its own
# `X-Forwarded-For: <spoofed>` and that value survives at position 0. Taking
# XFF[0] as the client IP therefore lets an attacker rotate the header to mint
# a fresh rate-limit key per request and bypass the /generate throttle.
#
# Fix: trust exactly ONE appended hop — take the RIGHTMOST entry, which Render
# appends after everything the client sent and which the client cannot append
# past. That value is spoof-proof.
#
# DO NOT switch back to the leftmost entry, and DO NOT skip additional hops
# from the right without an explicit allowlist of trusted proxy IPs — either
# re-opens the spoofing gap.
@app.middleware("http")
async def real_ip_middleware(request: Request, call_next):
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # "spoofable, ..., <render-appended real client>" — rightmost is the
        # entry Render itself appended, so it is the trustworthy client IP.
        last = xff.rsplit(",", 1)[-1].strip()
        request.state.real_ip = last or (request.client.host if request.client else None)
    else:
        request.state.real_ip = request.client.host if request.client else None
    return await call_next(request)


# -----------------------------
# GLOBAL EXCEPTION HANDLER
# Catches any unhandled exception in a route so it never fails silently
# -----------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # B58: do NOT echo exception class/message to the client (info disclosure).
    # Operator greps laigo.log for request_id to find the full traceback. (This
    # relies on the D-034 wiring: logger.py attaches the file handler to the
    # "laigo" logger; without it these records reach stdout only.)
    request_id = secrets.token_hex(8)
    log.error(
        f"Unhandled exception [request_id={request_id}] on {request.method} {request.url}: "
        f"{type(exc).__name__}: {exc}",
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id}
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
    # Remove the transient upload file. Normal completion already deletes it in
    # the worker; this covers terminal paths where the worker never ran to
    # completion (timeout / wedged worker, submission failure) and would
    # otherwise strand an up-to-MAX_UPLOAD_SIZE file in INPUT_DIR.
    try:
        (INPUT_DIR / f"{job_id}.upload").unlink(missing_ok=True)
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
    return {"status": "running", "message": "LAIGO API online. Use /health for status."}


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

    with app.state.scheduler_cv:
        active_jobs = app.state.active_jobs

    # NOTE: individual queued job_ids are deliberately NOT returned here. A
    # job_id is the de-facto capability token for /download, /artifacts,
    # /preview, /stats and /pay (there is no auth), so listing live ids on an
    # unauthenticated endpoint let anyone harvest them and read other customers'
    # artifacts/PII. Only aggregate counts are exposed. The frontend polls
    # /jobs/{id} for per-job state and never needed this list.
    return {
        "queued_jobs": queued_count,
        "max_queue_size": MAX_QUEUE_SIZE,
        "active_jobs": active_jobs,
        "max_workers": MAX_WORKERS,
        "counts": {
            "queued": queued_count,
            "running": running_count,
        },
    }


def _validate_image(path: Path) -> None:
    """Full pixel decode + RGB conversion to reject truncated/invalid uploads up
    front (D-006: a header sniff alone passes a truncated JPEG that then fails in
    the worker 5-30s later). Synchronous and potentially slow — img.load() decodes
    the entire upload — so callers must run it via asyncio.to_thread (D-038), never
    inline on the event loop. HEIC/HEIF decode here comes from the pillow-heif
    opener registered at import in picToMosiac (imported at module load); AVIF is
    native to Pillow (>=11.3)."""
    with Image.open(path) as img:
        img.load()
        img.convert("RGB")


@app.post("/generate")
async def generate(
    request: Request,
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

    # Per-IP rate limit AFTER validation (a 422 shouldn't burn the caller's
    # cooldown) but BEFORE the disk write / full image decode / worker spawn.
    # real_ip is set for every request by real_ip_middleware.
    _enforce_generate_rate_limit(getattr(request.state, "real_ip", None))

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
    # Hold the intake permit across BOTH the streaming write and the decode so
    # concurrent uploads can't flood disk / OOM the web process (see
    # _get_intake_semaphore). Released on exit; the store insert below is cheap
    # and runs without it.
    async with _get_intake_semaphore():
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
                # D-038: no os.fsync here — it forced a blocking disk sync on the event
                # loop for durability we don't need (a transient upload; if the server
                # crashes the job is lost anyway). Closing the file (end of this `with`)
                # flushes to the OS, which is all the worker subprocess needs to read it.
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Job {job_id} failed during file upload: {e}", exc_info=True)
            input_file.unlink(missing_ok=True)
            # Don't echo the raw exception text to the client (info disclosure);
            # the full error + traceback is in the log above.
            raise HTTPException(status_code=500, detail="Upload failed")

        await file.close()
        log.info(f"Job {job_id} file saved | size={size} bytes")

        try:
            # D-006: a header sniff (img.verify()) passes a truncated JPEG/TIFF that
            # then fails inside the worker 5-30s later, so force a full pixel decode +
            # RGB conversion to reject bad uploads with an immediate 400.
            # D-038: run that decode in a worker thread — img.load() on an up-to-250 MB
            # upload would otherwise block the event loop (every /health and /jobs poll).
            await asyncio.to_thread(_validate_image, input_file)
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
        # Fixed client message; the exception detail stays in the log above.
        raise HTTPException(status_code=500, detail="Failed to register job")

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
        # Route through _job_dir() so a crafted job_id can't escape OUTPUT_DIR
        # (path containment; consistent with the /preview + /stats handlers).
        error_manifest = _job_dir(job_id) / "manifest_failed.json"
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


def _job_dir(job_id: str) -> Path:
    """Resolve OUTPUT_DIR/job_id, refusing any value that escapes OUTPUT_DIR.

    The job_id path param is attacker-controllable; without this a crafted
    value (e.g. one containing '..') could read files outside the output tree.
    OUTPUT_DIR is already resolved at module load. 404 on escape (non-revealing).
    """
    candidate = (OUTPUT_DIR / job_id).resolve()
    if not candidate.is_relative_to(OUTPUT_DIR):
        raise HTTPException(status_code=404, detail="Not found")
    return candidate


@app.get("/jobs/{job_id}/preview")
async def get_job_preview(job_id: str):
    """Return the 3D-preview payload for a completed job.

    File existence is the sole readiness signal — the frontend polls
    /jobs/{job_id} for status, and only fetches /preview once status==complete.
    The bytes are read into memory and returned via Response (NOT FileResponse)
    so the file descriptor closes immediately, avoiding races with cleanup_loop
    rmtreeing the job directory on Windows.
    """
    preview_path = _job_dir(job_id) / "preview.json"
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


@app.get("/jobs/{job_id}/stats")
async def get_job_stats(job_id: str):
    """Authoritative piece count + optional static-price cost estimate.

    Reads outputs/{job_id}/stats.json (full per-element counts written at
    generation time — the stable order_list.json can NOT be summed instead:
    it is 999-capped per element when the order splits). File existence is
    the readiness signal, same as /preview; jobs completed before stats.json
    shipped simply 404, which the frontend renders as "stats unavailable".

    Pricing is joined at request time from scripts/piece_prices.json, so
    editing the table retroactively fixes estimates. estimated_cost_cents is
    null unless every element in the order has a price; a broken/missing
    price table degrades to null rather than failing the count.
    """
    stats_path = _job_dir(job_id) / "stats.json"
    try:
        stats = json.loads(stats_path.read_bytes())
        piece_counts = stats["piece_counts"]
        piece_count = int(stats["total_pieces"])
    except FileNotFoundError:
        log.info(f"Stats requested for job {job_id} but file not found")
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Stats are not available for this job.",
                "code": "STATS_NOT_AVAILABLE",
            },
        )
    except Exception as e:
        log.error(f"Stats file unreadable for job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Stats file is unreadable.",
                "code": "STATS_CORRUPTED",
            },
        )

    estimated_cost_cents = None
    currency = None
    pricing_as_of = None
    try:
        table = load_price_table()
        currency = table.get("currency")
        pricing_as_of = table.get("as_of")
        estimated_cost_cents = estimate_cost_cents(piece_counts, table)
    except Exception as e:
        log.error(f"Price table unavailable, serving null estimate: {e}")

    return {
        "piece_count": piece_count,
        "estimated_cost_cents": estimated_cost_cents,
        "currency": currency,
        "pricing_as_of": pricing_as_of,
    }


@app.get("/jobs/{job_id}/download")
async def download(job_id: str):
    artifact = _job_dir(job_id) / "artifact.zip"

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

    Two passes per tick:
      1. `cleanup_expired` — jobs that never had a /confirm (sagas FK
         empty). Original Phase D step 2 S7 behavior.
      2. `cleanup_terminal_sagas` — jobs whose sagas are ALL terminal and
         past the retention window (B59). Without this pass, every
         confirmed job retains its outputs/{job_id}/ directory forever
         because `sagas.job_id REFERENCES jobs ON DELETE RESTRICT`.

    Both return the list of deleted job_ids so we can rmtree the output
    directories and drop any leftover runtime side-table entries
    (defensive: terminal transitions already clean these, but a server
    restart that interrupted a terminal path could leave stragglers).
    """
    clog = logging.getLogger("laigo.cleanup")
    while True:
        try:
            deleted_ids: list[str] = _run_coro_blocking(
                jobs_store.cleanup_expired(),
                app.state.event_loop,
                timeout=30.0,
            )
            # B59 second pass — confirmed jobs past saga retention window.
            # No-op on the JSON backend; cheap NOT EXISTS query on PG.
            deleted_ids.extend(
                _run_coro_blocking(
                    jobs_store.cleanup_terminal_sagas(JOB_SAGA_RETENTION_DAYS),
                    app.state.event_loop,
                    timeout=30.0,
                )
            )
            for jid in deleted_ids:
                try:
                    shutil.rmtree(OUTPUT_DIR / jid, ignore_errors=True)
                    # Purge the private sidecar dir (payment.json / email.json)
                    # on the same TTL — LAIGO retains no PII past the job.
                    shutil.rmtree(PRIVATE_DIR / jid, ignore_errors=True)
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

            # Sweep orphaned intake files. The in-memory JSON jobs store is lost
            # on restart, so a queued/running job interrupted by a redeploy
            # leaves its inputs/{job_id}.upload (+ .progress) with no store row
            # and no runtime side-table entry — cleanup_expired never sees it and
            # it accumulates toward disk exhaustion. Any intake file older than
            # the job TTL is definitively orphaned: a live job is force-failed by
            # the timeout watchdog (JOB_TIMEOUT_SECONDS, < JOB_TTL_SECONDS) and a
            # completed job's upload is deleted by the worker, so no live job's
            # file is ever this old.
            cutoff = time.time() - JOB_TTL_SECONDS
            for stale in list(INPUT_DIR.glob("*.upload")) + list(INPUT_DIR.glob("*.progress")):
                try:
                    if stale.stat().st_mtime < cutoff:
                        stale.unlink(missing_ok=True)
                        clog.info(f"Swept orphaned intake file {stale.name}")
                except FileNotFoundError:
                    pass
                except Exception as e:
                    clog.error(f"Failed to sweep intake file {stale.name}: {e}")
        except Exception:
            clog.error("Unexpected error in cleanup loop", exc_info=True)

        time.sleep(CLEANUP_INTERVAL)
