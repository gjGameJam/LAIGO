# Pre-Release Payment Checklist

**Audience:** LAIGO maintainers — single source of truth for "what must be true before the first real $1 moves."

**Posture today:** Stripe wired + enabled in TEST mode (`STRIPE_ENABLED = True` in `stripe_provider.py`, `sk_test_...` in `.env.secrets`, `CHECKOUT_ENABLED=true` in `.env`, `DB_BACKEND=postgres`). BrickOwl catalog API not granted. LEGO.com Playwright never run in live. No live customer money has moved.

---

## 🛑 PAUSED 2026-05-20 — Stripe/DB work on hold; 3D preview API + frontend is active focus

**Why:** Pivoting to 3D preview work. 2D editor deferred until after Stripe/DB resumes.

> **One operator action required to finalize the pause cleanly** — apply migration 0002 to Neon `dev` so uvicorn boots without the schema-mismatch error. From any local shell with the Neon dev DIRECT DSN:
> ```powershell
> $env:ALEMBIC_DATABASE_URL = "<Neon dev DIRECT DSN — no '-pooler' in host>"
> alembic upgrade head
> # then: SELECT version_num FROM alembic_version;  -- expect '0002'
> ```
> Until this is run, every `uvicorn scripts.Main:app` will refuse to boot with `verify_schema()` mismatch. The migration is a single nullable column add + CHECK constraint — semantically inert until a saga writes MANUAL_REVIEW.

**State at pause (the work to come back to):**
- B55, B56, B57 — HIGH-severity audit fixes — **code shipped 2026-05-19** (see §4.2).
- Migration 0002 (`sagas.hold_disposition`) — **assumed-applied to Neon `dev` 2026-05-20** (run the command above if you haven't). Pending on Neon `main` until Phase F cutover window.
- Phase F prerequisites: U3 ✅ U4 ✅ U5 ❌ (Neon Launch upgrade) U6 ✅ on dev, ❌ on main.
- All HIGH-severity work compiles; no in-flight work; task ledger is closed.
- Tests at pause time: `test_optimizer`, `test_gate_bypass`, `test_saga_state_machine`, `test_jobs_store_json` all green (no-DB). `test_reconcile_pg`, `test_phase_e_pg`, `test_jobs_store_dispatch` green against Neon dev with migration 0002 applied.

### To resume the Stripe/DB work — exact sequence

1. **Re-read this section + §4.2 catalog (B55/B56/B57 entries)** — refreshes context in <5 min.
2. **Verify Neon `dev` state** — `SELECT version_num FROM alembic_version;` should return `'0002'`. If it doesn't, re-run `alembic upgrade head` against the dev DIRECT DSN.
3. **Re-run integration smoke tests against dev** —
   ```powershell
   .venv\Scripts\python.exe -m scripts.test_reconcile_pg
   .venv\Scripts\python.exe -m scripts.test_phase_e_pg
   .venv\Scripts\python.exe -m scripts.test_jobs_store_dispatch
   ```
   All three must pass before proceeding. Requires `.env.secrets` has the Neon dev DSN as `DATABASE_URL` and `DB_BACKEND=postgres` in `.env`.
4. **Coordinate Phase F cutover window** — see §9.4. Specifically: U5 (Neon Launch tier $19/mo) MUST be done in the same maintenance window as the `DB_BACKEND=postgres` flag flip on Render, otherwise Free-tier autosuspend cold-starts the first `/status` poll.
5. **Apply migration 0002 to Neon `main`** during the cutover window. Render's pre-deploy hook (U3) will run `alembic upgrade head` automatically on the first deploy after the env vars are in place — verify by tailing the Render deploy logs for `Running upgrade 0001 -> 0002`.
6. **Phase F observation week** — see §9.4 Step 6. Daily MANUAL_REVIEW + saga-status SQL queries.
7. **Phase F Step 7 cleanup** after 7 clean days — delete the JSON code path (see §9.4 Step 7 file list).
8. **Pick up §3 roadmap items #3–#10 + #12–#14** in parallel or after.

### Freeze zone (do not modify during the pause)

Touching any of these while building 3D preview risks regressing the audit work and creating subtle latent bugs that won't surface until resume:

- `scripts/checkout/**` (entire package — saga, reconcile, audit, payment providers, stores)
- `scripts/migrations/**` (don't author 0003 until 0002 is on Neon `main`)
- `scripts/db.py` (especially `_EXPECTED_SCHEMA_VERSION`, `init_pool`, `verify_schema`)
- `scripts/jobs_store_pg.py`, `scripts/jobs_store_json.py`, `scripts/jobs_store_dispatch.py`
- `scripts/Main.py` lifespan (the `async def lifespan(app)` block — boot invariants are load-bearing)
- `.env` keys: `DB_BACKEND`, `CHECKOUT_ENABLED`, `DATABASE_URL`, `STRIPE_*`
- This file (§0–§9). `docs/CHECKOUT_AUDIT.md`, `docs/ORDER_OPTIMIZER.md`.

If 3D preview work genuinely needs to touch any of these, **treat the pause as ended** — re-read this section, the freeze-zone constraints don't apply mid-resume.

### Scope of the new (3D preview) work

3D preview API + frontend is mosaic-pipeline territory:
- Likely touches: `scripts/picToMosiac.py`, `scripts/VisualMaker.py`, `scripts/MosiacToInstruction.py`, possibly new modules under `scripts/`, and new routes in `scripts/Main.py` outside the lifespan block.
- Does NOT need: checkout, payment, reconciler, audit log, payment_holds. New HTTP endpoints for 3D preview are read-side (return preview data) — they do NOT need `Depends(require_checkout_gate_open)` per the doctrine in `scripts/checkout/dependencies.py` docstring.
- If a new endpoint mutates state, follow `{detail: {error, code, ...}}` shape (CLAUDE.md "Public API contracts" bullet).

### Resuming under "I forgot everything"

If enough time passes that the resumption sequence above isn't enough, re-read in this order:
1. This section (§0 PAUSED) — restores immediate context.
2. `CLAUDE.md` — module map + boot invariants + doctrine bullets. The single most concentrated source of project knowledge.
3. §4.2 B55/B56/B57 entries — explains exactly what was changed and why.
4. §9 (migration history + Phase F playbook) — the operational sequence.

---

## 0. Status snapshot — where we are right now

### What's working

| Area | State | Code |
|---|---|---|
| L0 gate (single source of truth for "is checkout safe") | ✅ | `scripts/checkout/gate.py` |
| L1 lifespan boot invariants | ✅ | `scripts/Main.py` |
| L2 `/checkout/gate` operator visibility | ✅ | `scripts/checkout/gate_router.py` |
| L3 `/confirm` 503 dependency | ✅ | `scripts/checkout/dependencies.py` |
| L4 Saga pre-flight | ✅ | `scripts/checkout/saga.py` |
| L5 PaymentProvider Protocol + registry + StripeProvider | ✅ | `scripts/checkout/payment/` |
| L6 audit log (`audit_events` table) | ✅ module + saga_resume + saga.py call sites all wired (§2.6 complete 2026-05-19) | `scripts/checkout/audit.py`, `scripts/checkout/saga.py`, `scripts/checkout/saga_resume.py` |
| Saga capture retry + MANUAL_REVIEW state | ✅ | `scripts/checkout/saga.py` |
| 5% hold buffer + drift fail-closed | ✅ | `scripts/checkout/saga.py` |
| Postgres-backed state | ✅ Phases A + B + C + D (incl. step 2) complete | `scripts/db.py`, `scripts/checkout/checkout_store_pg.py`, `scripts/jobs_store_pg.py` |
| Saga resume-on-startup | ✅ Phase E step 1 | `scripts/checkout/saga_resume.py` |
| `payment_holds` index (wired at saga create_hold + capture/cancel) | ✅ Phase E partial | `scripts/checkout/payment_holds_store.py` |
| Orphan-hold reconciler (periodic task) | ✅ Phase E step 2 — code shipped, no-op until Stripe-backed sagas populate `payment_holds` | `scripts/checkout/reconcile.py` |
| `PaymentProvider.get_hold_status()` | ✅ Phase E step 2 prerequisite | `scripts/checkout/payment/{base,stripe_provider}.py` |

### What's next (in priority order)

| # | Item | Blocker |
|---|---|---|
| 1 | ~~Stripe TEST credentials wired + `STRIPE_ENABLED=True`~~ | ✅ Done (Action A). Verify by `curl http://127.0.0.1:8000/checkout/gate` → `{"mode":"test","is_open":true}`. |
| 2 | ~~Phase E step 2 — `reconcile_orphan_holds()` periodic task~~ | ✅ Shipped 2026-05-19 |
| 3 | ~~Phase E step 4 — end-to-end mid-saga restart test~~ | ✅ Covered by `test_phase_e_pg` (15 assertions, every routing branch) + Action A boot-log verification. Live Stripe exercise deferred until U1 (SSL fix); harness at `scripts/phase_e_4_inject.py`. |
| 4 | ~~Wire remaining L6 audit call sites (§2.6 checklist)~~ | ✅ Shipped 2026-05-19 — 26 emits in saga.py |
| 5 | ~~Roadmap §3 #11 — §6.10 minimum test suite~~ | ✅ Shipped 2026-05-19 — `test_optimizer.py`, `test_gate_bypass.py`, `test_saga_state_machine.py` (28 assertions; no DB, no network) |
| 6 | ~~B55 — Reconciler MANUAL_REVIEW handling~~ | ✅ Code shipped 2026-05-19; migration 0002 applied to Neon `dev` 2026-05-20. HoldDisposition column threaded through saga.py + saga_resume.py + reconcile.py; test_reconcile_pg adds 4 cases. |
| 7 | ~~B56+B57 — Hold-cancel retry helper + orphan audit~~ | ✅ Code shipped 2026-05-19. New module `scripts/checkout/_cancel_helpers.py` (`cancel_hold_with_retry`). All naked `provider.cancel` sites in `saga.py` + `saga_resume.py` migrated. `payment.hold_orphan` audit event added to §2.2 vocabulary; **always alerts** in production. |
| 8 | **🛑 PAUSED 2026-05-20 — 3D preview API + frontend** | Active focus shifted away from Stripe/DB. See pause section at top of this file for resume sequence + freeze zones. |
| 9 | **Phase F — `DB_BACKEND=postgres` cutover on Render** | Resume after pause. U5 (Neon Launch upgrade) + flag flip. U6b (apply 0002 to main) is handled by Render's pre-deploy hook. |
| 10 | Roadmap §3 #3–#10 + #12–#14 (pre-commit revalidation, MarketplaceAdapter, BrickOwl cancel, rate limit, etc.) | None — engineering work; pick up in parallel with Phase F. |

### Hard launch gate

Before any real `sk_live_` Stripe key is configured, ALL of the following must be true:

- Items (2), (3), (5) in the "what's next" table above are complete.
- §3 roadmap items #3–#7 + #10 + #11 + #13 + #14 are shipped.
- Go-live checklist §6 has been walked once in TEST mode.

### Remaining operator actions

**A — Stripe TEST credentials** — ✅ Done (`.env.secrets` has `STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_ENABLED=True` in `stripe_provider.py`, `CHECKOUT_ENABLED=true` + `DB_BACKEND=postgres` in `.env`).

**B — Local smoke** — runnable any time. See "Running the tests" in `README` for the full command list; the postgres-backed suites (`test_jobs_store_dispatch`, `test_phase_e_pg`, `test_reconcile_pg`) need `DB_BACKEND=postgres` + a Neon DSN in `.env.secrets`. Boot log on `uvicorn scripts.Main:app --reload` should show `[resume] examined N in-flight sagas` AND `[reconcile] periodic task started (interval=300s)`.

**C — Render + Neon configuration (Phase F prerequisite)**
- ~~**U3:**~~ ✅ Done 2026-05-19. Render pre-deploy command `alembic upgrade head` configured.
- ~~**U4:**~~ ✅ Done 2026-05-19. `DATABASE_URL=<Neon main pooler DSN>` + `DATABASE_URL_DIRECT=<Neon main direct DSN>` in Render env. `DB_BACKEND` still `json` until cutover.
- **U5:** Neon dashboard → Billing → Free → Launch ($19/mo). Do this in the same window as the flag flip — Free tier autosuspends after 5 min of idle.
- ~~**U6a:**~~ ✅ Done 2026-05-20. Migration 0002 applied to Neon `dev` via local `alembic upgrade head`.
- **U6b:** Migration 0002 applies to Neon `main` automatically via U3's pre-deploy hook on the first cutover deploy. No separate operator action.

**D — Product decisions (block roadmap items)**
- BrickOwl ordering strategy: Option A (Playwright order + LAIGO collects payment) vs Option B (cart URL redirect). Picks roadmap #5's path.
- Drift tolerance: ≤2% silent eat, OR always prompt customer for new price. Drives roadmap #12.
- Hourly financial-exposure cap: $500? $5000? Drives roadmap #10.
- Customer concurrency policy: can a customer have two open `/quote`s for the same job? Drives the active-quote partial unique index.
- Refund policy on capture-failed-after-orders-placed: contact for alt payment, refund and eat cost, or both? Drives MANUAL_REVIEW runbook.

### Open defects (see §4.1 for full table)

| Severity | Open IDs |
|---|---|
| HIGH (code-shipped) | B55, B56, B57 — all moved to §4.2 closed catalog. Migration 0002 applied to Neon `dev` 2026-05-20; pending on `main` (handled by Render pre-deploy hook at Phase F cutover — U6b). |
| MEDIUM | B58 (500 leaks exception class), B59 (jobs cleanup blocked by sagas FK → unbounded disk), B60 (audit actor.ip masked by LB) |
| MEDIUM (LATENT) | B11 (cross-iteration brickowl_order_ids checkpoint — only when roadmap #5 ships), B23 in json mode, B28 (per-order cancel retry — when roadmap #5 ships), B36 (semaphore-per-call) |
| LOW | B26, B27, B29, B30, B31, B35, B61 (CORS env unread), B63 (no stop_cache_sweeper), B64 (Stripe msg substring), B65 (422 missing code) |
| DOC | B31 |
| COSMETIC | B48, B49 |
| FUTURE-PROOFING | B51 (dither hardcoded), B52 (started_at == completed_at on fast jobs) |

**HIGH-severity items are resolved in code.** Migration 0002 is applied to Neon `dev` (U6a, 2026-05-20); apply to `main` is U6b, handled by Render's pre-deploy hook on the next deploy. After Phase F (U5 + DB_BACKEND flip), the live-Stripe flip is unblocked.

### Where to read after `/clear`

1. **🛑 PAUSED section at the top of this file** — the active project state. If you're returning to Stripe/DB work, the resume checklist there is your entry point.
2. **This file §0 (here)** — current state, what's working, what's next.
3. **`CLAUDE.md`** — module map + boot invariants + doctrine bullets. Also flags the active focus and freeze zones.
4. **§9.3 here** — reconciler decision matrix (includes B55's MANUAL_REVIEW disposition branch).
5. **§9.4 here** — Phase F (Render cutover) playbook.
6. **§4** — open defects + §4.2 closed catalog (B55-B57 entries with file lists).

---

## 1. Why this exists

The checkout system's correctness is defended by six layers (L0–L6). L0–L5 stop regressions from causing immediate damage; L6 makes sure that if a regression DOES land, an alert fires before five customers are affected instead of five thousand.

This document gates LAIGO's go-live for monetized checkout. Items here are launch-blocking unless explicitly tagged otherwise. The diagnostic record (FMEA, gap analyses, target architecture) lives in `docs/CHECKOUT_AUDIT.md`; this file is the **gating checklist**.

---

## 2. L6 — Audit log subsystem

L6 is the post-hoc detection layer for everything else.

### 2.1 Event envelope (canonical schema — LOCKED)

```python
{
  "event": "gate.confirm_rejected",         # snake_case, dot-separated
  "ts": "2026-05-15T10:30:00.123Z",          # ISO 8601 UTC, ms precision
  "request_id": "req_abc123",                # from middleware (future) or null
  "actor": {                                 # who initiated (best-effort)
    "type": "customer" | "system" | "operator",
    "ip": "1.2.3.4",                         # null if not available
    "user_agent": "Mozilla/..."
  },
  "subject": {                               # what's being acted on
    "job_id": "abc123",
    "checkout_id": "co_def456",
    "session_id": null
  },
  "data": { ... }                            # event-specific JSON payload
}
```

Frozen schema. New events extend `data`; never rename top-level keys.

### 2.2 Event vocabulary (locked names)

| Event | Trigger | Data payload |
|---|---|---|
| `gate.confirm_rejected` | L3 returns 503 to `/confirm` | `{mode, reasons[]}` |
| `gate.saga_rejected` | L4 aborts Saga before any work | `{mode, reasons[], invocation_source}` |
| `saga.started` | execute_checkout_saga begins | `{mode, payment_provider}` |
| `saga.stripe_held` | Stripe hold succeeds | `{hold_id, amount_cents, currency}` |
| `saga.orders_placed` | All marketplace orders submitted | `{order_ids: {seller: [ids]}}` |
| `saga.captured` | Stripe capture succeeds | `{hold_id, captured_amount_cents}` |
| `saga.failed` | Saga transitions to FAILED | `{reason, error_message?, hold_id?}` |
| `saga.compensated` | Compensation finished | `{cancelled_orders, manual_required}` |
| `saga.manual_review` | Saga enters MANUAL_REVIEW | `{reason, hold_id, authorized_cents, last_error}` |
| `marketplace.order_placed` | Single marketplace order succeeded | `{seller_id, marketplace_order_id, items_count}` |
| `marketplace.order_cancelled` | Cancellation succeeded | `{seller_id, marketplace_order_id}` |
| `marketplace.cancel_failed` | Cancellation API failed | `{seller_id, order_id, error}` — **always alerts** |
| `payment.hold_created` | Payment hold via provider | `{hold_id, amount_cents, mode}` |
| `payment.captured` | Capture via provider | `{hold_id, captured_amount_cents}` |
| `payment.cancelled` | Cancel via provider | `{hold_id, reason?}` |
| `payment.hold_orphan` | Hold authorized at Stripe but rollback could not release it (B56). Saga writes FAILED with no payment_holds row, so this event is the only persistent record. | `{hold_id, amount_authorized_cents, currency, reason, record_hold_error?, cancel_error?}` — **always alerts** |
| `payment.skipped` | (LEGACY — must never appear in prod after L4) | `{reason}` |

A non-zero `payment.skipped` count in production is **a P0 page**. The event is wired only to detect a regression that would otherwise reintroduce RPN #1.

A non-zero `payment.hold_orphan` count is **also a P0 page**. The event fires only when the record_hold rollback path exhausted retries — the customer's authorization is stranded at Stripe until manual cancellation or the 7-day auto-expiry. Operator runbook: query the event, take the `hold_id` to the Stripe dashboard, cancel manually.

### 2.3 Storage

**Postgres `audit_events` table** (schema §8 below). Indexed: `(ts DESC)`, `(event, ts DESC)`, `(job_id, ts DESC) WHERE job_id IS NOT NULL`.

### 2.4 Retention

| Class | Retention | Why |
|---|---|---|
| Financial (`payment.*`, `saga.*`, `marketplace.*`) | 1 year | Chargeback window (180d) + reconciliation |
| Control-plane (`gate.*`) | 90 days | Operational diagnosis only |

### 2.5 Public API

```python
# scripts/checkout/audit.py
async def emit(
    event: str,
    *,
    subject: dict | None = None,    # {job_id, checkout_id, session_id}
    actor: dict | None = None,      # {type, ip, user_agent}
    data: dict | None = None,       # event-specific payload (Python dict — codec handles JSONB)
    request_id: str | None = None,
) -> None: ...
```

**NEVER raises.** Audit must not break checkout. Failures log `[audit] FAILED` at CRITICAL.

### 2.6 Call-site migration checklist

| Site | Status |
|---|---|
| `dependencies.py::require_checkout_gate_open` → `gate.confirm_rejected` | ✅ |
| `saga_resume.py::_recover_*` → `saga.failed`, `saga.manual_review`, `payment.cancelled` | ✅ |
| `saga.py::execute_checkout_saga` (gate-closed) → `gate.saga_rejected` | ✅ |
| `saga.py` state transitions → `saga.*` per transition | ✅ |
| `saga.py` MANUAL_REVIEW writes → `saga.manual_review` | ✅ |
| `saga.py` `provider.create_hold` success → `payment.hold_created` | ✅ |
| `saga.py` `provider.capture` success → `payment.captured` | ✅ |
| `saga.py` `provider.cancel` (in-saga) success → `payment.cancelled` | ✅ |

All 6 saga.py call sites wired 2026-05-19. Saga emits 26 audit rows total: 1 gate, 1 started, 1 stripe_held, 1 orders_placed, 1 captured, 1 compensated, 7 manual_review, 8 failed, 1 hold_created, 1 captured (payment.*), 3 cancelled.

Keep existing `logger.warning/info` lines for one month after each call-site migration so audit completeness can be cross-checked, then remove.

### 2.7 Alerting (post-L6)

| Alert | Threshold | Action |
|---|---|---|
| `count(event="payment.skipped") > 0 in 24h` | Impossible state | **Page on-call — RPN #1 regression** |
| `count(event="gate.confirm_rejected") > 10 per 5min` | Frontend bug or attack | Page on-call |
| Any `event="marketplace.cancel_failed"` | Manual review needed | Slack notify with order_id |
| Any `event="saga.manual_review"` | Operator action required | Slack notify with reason |
| `count(event="saga.failed") / count(event="saga.started") > 0.05 in 1h` | Reliability drop | Slack notify |

---

## 3. Pre-launch roadmap

Items 1–7 are the minimum-viable pre-launch set. Items 8–14 reduce tail-risk and are strongly recommended but can ship in the first month post-launch.

| # | Item | RPN(s) | Status |
|---|---|---|---|
| 1 | Layered Stripe-disabled defense (gate + boot + router + Saga + provider + audit) | #1 (810) | ✅ L0–L5 shipped; L6 partial (§2.6) |
| 2 | Move Saga state to Postgres; add resume-on-startup; partial-unique-index per-job | #3 (450), #5, #17, #16 | ✅ A+B+C+D+E.1 shipped; E.2 + F pending |
| 3 | Pre-commit revalidation per adapter | #4 (448), #14, #15 | ❌ Open. Closes ~half of cart-snatching window. |
| 4 | MarketplaceAdapter Protocol; refactor LEGO + BrickOwl into adapters | #2 prep, #20 | ❌ Open. Structural — enables everything else. |
| 5 | Implement BrickOwl Playwright cancellation; reorder commits so reversible first | #2 (504) | ❌ Open. **Cannot launch without this if BrickOwl is in the loop.** |
| 6 | Capture retry with backoff + operator alert; MANUAL_REVIEW state | #7 (252) | ✅ Shipped (bundled with L5) |
| 7 | Per-source rate-limit budgets via `AsyncLimiter` + lifespan HTTP clients | #8 (240) | ❌ Open |
| 8 | Listing parse sanity checks (price/qty bounds) | #10 | ❌ Open |
| 9 | Optimizer Pass 2 redesign + sanity tests | #11, #20 | ❌ Open |
| 10 | Bounded financial exposure circuit breaker | #19 | ❌ Open |
| 11 | Test suite covering CHECKOUT_AUDIT §6.10 minimums | #21 | ✅ Shipped 2026-05-19. `scripts/test_optimizer.py` (items 1–7, 9 tests), `scripts/test_gate_bypass.py` (item 10, 12 tests), `scripts/test_saga_state_machine.py` (item 8, 7 transitions). Item 9 (resume-from-non-terminal) covered-by-integration via `test_phase_e_pg.py` (Phase E.1). Hot-path acceptance tests (network-fixture: LEGO/BrickOwl parse, HAR playback) intentionally deferred — they belong to roadmap #3 (pre-commit revalidation) and #4 (MarketplaceAdapter). |
| 12 | Drift tolerance + customer "confirm new price" round-trip | #9 | ❌ Open |
| 13 | LEGO.com semaphore + recent-order-list duplicate-detection heuristic | #6, #18 | ❌ Open |
| 14 | Customer confirmation email | #24 | ❌ Open |

**Effort scale:** S ≤1 engineer-day, M ≤1 week, L >1 week.

---

## 4. Defects

### 4.1 Open

| ID | Severity | Summary | Where |
|---|---|---|---|
| B11 | MEDIUM | Cross-iteration `placed_brickowl_ids` checkpoint is destructive | `scripts/checkout/saga.py` |
| B23 (json mode) | MEDIUM | Concurrent `/confirm` w/ different checkout_id for same job_id clobbers state — PG mode closes via partial unique index; JSON mode still open | `scripts/checkout/checkout_store.py` |
| B26 | LOW | `checkout_store.update()` crashes on corrupted JSON with no recovery (JSON-mode only; disappears post-Phase-F) | `scripts/checkout/checkout_store.py` |
| B27 | LOW | `_ALLOWED_CURRENCIES` hardcoded; adding currency requires code change + deploy | `scripts/checkout/payment/stripe_provider.py` |
| B28 | MEDIUM (LATENT until roadmap #5) | `_parallel_brickowl_cancels` doesn't retry individual transient cancel failures | `scripts/checkout/saga.py` |
| B29 | LOW | LEGO `StockoutError` branch plumbed but `order_from_lego` doesn't raise it today | `scripts/checkout/clients/lego_client.py` |
| B30 | LOW | "Hold failed unexpectedly" categorized as `payment_transient` — may misguide on permanent bugs | `scripts/checkout/saga.py` |
| B31 | DOC | CHECKOUT_AUDIT.md "now lives at" mapping table will drift as saga.py grows | `docs/CHECKOUT_AUDIT.md` |
| B35 | LOW | JSON mode non-atomic file writes (disappears post-Phase-F) | `scripts/checkout/checkout_store.py` |
| B36 | LOW (LATENT) | `_parallel_brickowl_cancels` semaphore per-call, not per-process (future concurrent-saga design) | `scripts/checkout/saga.py` |
| B48 | COSMETIC | `pgcrypto` extension loaded but unused | `scripts/migrations/sql/0001_initial_schema.up.sql` |
| B49 | COSMETIC | `schema_meta` table created but never used | `scripts/migrations/sql/0001_initial_schema.up.sql` |
| B51 | LOW (FUTURE-PROOFING) | `dither` hardcoded TRUE in jobs INSERT — fires when /generate exposes a dither param | `scripts/checkout/jobs_store_pg.py`, `scripts/jobs_store_json.py` |
| B52 | LOW | Race-induced `started_at == completed_at` on fast jobs (residual after B41) | `scripts/jobs_store_pg.py` |
| B58 | MEDIUM | `unhandled_exception_handler` leaks exception class + message to clients (info disclosure in prod) | `scripts/Main.py` |
| B59 | MEDIUM | `jobs.cleanup_expired` cannot delete jobs once a saga exists (FK ON DELETE RESTRICT) — `outputs/{job_id}/` grows unbounded | `scripts/jobs_store_pg.py`, schema |
| B60 | MEDIUM | `actor.ip` audit field captures Render LB IP, not real customer IP — no `X-Forwarded-For` parsing middleware | `scripts/checkout/dependencies.py` |
| B61 | LOW | CORS allowed origins hardcoded in `Main.py`; `FRONTEND_ORIGIN` env exists but is never read | `scripts/Main.py` |
| B63 | LOW | Cache sweeper has no `stop_cache_sweeper()` — relies on event-loop close to cancel (asymmetric with reconcile task) | `scripts/checkout/cache.py`, `scripts/Main.py` |
| B64 | LOW | `stripe_provider._is_already_captured` / `_is_already_terminal_state` match Stripe error messages by substring — fragile to wording changes | `scripts/checkout/payment/stripe_provider.py` |
| B65 | LOW | `/confirm` 422 for unsourceable items lacks the `code:` discriminator the rest of the API uses (`{error, code, ...}` shape) | `scripts/checkout/router.py` |

**B55, B56, B57 (HIGH severity) — code shipped 2026-05-19**; see §4.2 closed catalog. Migration 0002 applied to Neon `dev` 2026-05-20; pending on `main` (U6b). B58 should be resolved before any public frontend points at the deployed API. B28/B29/B35/B36 are gated on other roadmap work; B48/B49/B51/B52 are cosmetic / future-proofing.

#### B11 — Cross-iteration `placed_brickowl_ids` checkpoint

**Symptom:** On stockout retry, the saga re-optimizes and re-iterates. The `placed_brickowl_ids` list is appended to across iterations. If a prior-iteration order is cancelled in the retry, the id stays in the list and `_compensate` would attempt to cancel it again.

**Mitigation today:** BrickOwl `cancel_order` is a no-op stub (not yet implemented per roadmap #5). When real cancellation lands, B11 becomes a real defect.

**Fix:** On stockout retry, remove cancelled ids from `placed_brickowl_ids` before continuing. Subsumed by Phase C JSONB `iterations` column work if it ships.

#### B27 — Currency allowlist hardcoded

**File:** `scripts/checkout/payment/stripe_provider.py:_ALLOWED_CURRENCIES`

**Symptom:** Adding a supported currency requires code change + deploy. Frozenset literal lives in one place.

**Fix when needed:** Move to env var (`STRIPE_ALLOWED_CURRENCIES=usd,eur,gbp`). One-line change.

#### B28 — `_parallel_brickowl_cancels` no per-order retry

**File:** `scripts/checkout/saga.py:_parallel_brickowl_cancels`

**Symptom:** When the real BrickOwl cancel ships (roadmap #5), a single transient failure stops compensation entirely.

**Fix:** Wrap each cancel in `_cancel_with_retry` analogous to `_cancel_hold_with_retry`. Wire when roadmap #5 ships.

#### B29 — LEGO `StockoutError` plumbed but unraised

**File:** `scripts/checkout/clients/lego_client.py:order_from_lego`

**Symptom:** The saga has a stockout-retry branch for LEGO orders gated on `StockoutError`. The Playwright order code today never inspects the DOM for "out of stock"; it succeeds or raises generic `Exception`.

**Fix:** Add DOM-detection in `order_from_lego` to raise `StockoutError` on detected stockouts.

#### B30 — "Hold failed unexpectedly" → payment_transient

**File:** `scripts/checkout/saga.py` (hold-creation `except Exception` branch)

**Symptom:** Real bugs (e.g., wrong Stripe SDK call) show customers "Our payment system is temporarily unavailable. Please retry shortly." — they retry forever. Should escalate to operator.

**Fix:** Distinguish "known Stripe error class we have no policy for" (rare) vs "code bug" (catch in a stricter `except` chain). Code bugs → MANUAL_REVIEW with operator notification.

#### B48/B49 — Unused schema features

`pgcrypto` extension + `schema_meta` table created in migration 0001 but never queried. Drop in a 0002 migration when convenient, OR commit to a use case (e.g., `schema_meta` could store the cutover date + last-rotation marker).

#### B51 — `dither` hardcoded TRUE

`/generate` API has no dither toggle today (Floyd-Steinberg is the only color-mapping path). When that changes, update three sites: `scripts/Main.py:/generate` (form param), `scripts/jobs_store_pg.py:insert_queued` (pass through), `scripts/jobs_store_json.py:_make_row` (pass through).

#### B52 — `started_at == completed_at` on fast jobs

**Symptom:** Mark_complete's COALESCE(started_at, NOW()) backfills started_at when mark_running's update lost the race. For sub-millisecond jobs, started_at and completed_at end up equal. Operators computing duration get 0.

**Mitigation today:** Accepted imprecision. Real jobs take >1s.

**Fix:** None planned. If duration histograms matter for ops, switch to NOW() ± some default minimum.

#### B58 — `unhandled_exception_handler` leaks exception class + message

**File:** `scripts/Main.py` — `unhandled_exception_handler`.

**Symptom:**
```python
content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"}
```
Reveals internal Python class names, file paths in tracebacks, query fragments, Stripe IDs, etc. to anyone hitting the API on a 500.

**Impact:** Info disclosure once the API is publicly reachable. Useful in dev; problematic in prod.

**Fix:** Return a static `"Internal server error"` detail; preserve the full `log.error(..., exc_info=True)` server-side. Optionally include a short request-correlation ID (`secrets.token_hex(8)`) that the operator can grep for.

#### B59 — Mosaic-job `cleanup_expired` blocked by sagas FK → unbounded disk

**File:** `scripts/jobs_store_pg.py` — `cleanup_expired`; schema `sagas.job_id REFERENCES jobs(job_id) ON DELETE RESTRICT`.

**Symptom:** Once any customer confirms a checkout, a `sagas` row exists pointing at the job. The FK is `ON DELETE RESTRICT`. Even after the saga reaches a terminal status (PAYMENT_CAPTURED, COMPENSATED, FAILED, MANUAL_REVIEW), the FK still blocks deletion of the `jobs` row. `cleanup_expired` catches the ForeignKeyViolationError per-row and skips, so the corresponding `outputs/{job_id}/` directory is never `rmtree`'d.

A typical mosaic artifact zip is 1–10 MB. At production traffic (1 confirmed checkout = forever-retained directory), disk usage grows monotonically.

**Impact:** Not visible pre-launch. Will surface as a Render disk-full or Neon-disk-exceeded alert weeks-to-months after monetized traffic starts.

**Fix:** Add a second cleanup task targeting jobs whose latest saga is **terminal** AND `sagas.completed_at` is older than a retention window (90 days is a sensible default — past the typical chargeback dispute window). Delete in dependency order: `audit_events` rows (no FK; lenient delete by job_id), then `payment_holds`, then `sagas`, then `checkouts`, then `jobs`. *Important constraint:* `outputs/{job_id}/order_list.json` must remain readable while any saga is non-terminal — the FK currently provides this guarantee, and the new cleanup must preserve it (only delete after the saga is terminal + retention window).

#### B60 — `actor.ip` audit field captures LB IP, not customer IP

**File:** `scripts/checkout/dependencies.py` — `require_checkout_gate_open` (already documented as TODO inline; promoted here for tracking).

**Symptom:** `request.client.host` returns the immediate hop's IP. Behind Render's load balancer, that's the LB IP for every customer. The §2.7 abuse-detection use case ("rate-limit per customer IP") can't function — every rejected `/confirm` looks like it came from the same IP.

**Impact:** Audit log's actor.ip column carries zero signal in production. Not currently used by any alert, so impact is latent until L6 alerting is wired.

**Fix:** Add a small FastAPI middleware that parses `X-Forwarded-For` (take the *first* IP, since Render is the only trusted proxy) and stuffs it onto `request.state.real_ip`. `require_checkout_gate_open` reads from there. Document the single-proxy trust assumption in the middleware so a future multi-proxy setup doesn't silently trust spoofed XFF headers.

#### B61 — `FRONTEND_ORIGIN` env unused; CORS origins hardcoded

**File:** `scripts/Main.py` — `app.add_middleware(CORSMiddleware, allow_origins=[...])`.

**Symptom:** `.env` carries `FRONTEND_ORIGIN` but `Main.py` does not read it. Origins are hardcoded to `https://laigo-frontend.onrender.com` and `http://localhost:5173`. Already documented as antipattern in CLAUDE.md; tracked here for the fix.

**Impact:** Frontend URL changes require a code edit + redeploy.

**Fix:** Read `FRONTEND_ORIGIN` env (comma-separated to support multi-origin), fall back to the current hardcoded list when unset. One-line change.

#### B63 — No `stop_cache_sweeper()` for graceful shutdown

**File:** `scripts/checkout/cache.py`; `scripts/Main.py` lifespan shutdown.

**Symptom:** `start_cache_sweeper` has no shutdown counterpart. The sweep task is cancelled when the event loop closes — works in practice (sweeper uses no DB pool, just an in-memory dict), but inconsistent with the reconcile task which has `stop_reconcile_task(timeout=5.0)` called explicitly before `close_pool()`.

**Impact:** Minor. Any future addition that makes the sweeper touch external state would inherit a race.

**Fix:** Add `async def stop_cache_sweeper(timeout=5.0)` modeled on `stop_reconcile_task`. Call it from lifespan shutdown right after `stop_reconcile_task` (cache sweeper has no pool dependency, so ordering relative to `close_pool` is flexible).

#### B64 — Stripe error message substring matching is fragile

**File:** `scripts/checkout/payment/stripe_provider.py` — `_is_already_captured`, `_is_already_terminal_state`.

**Symptom:** Stripe could change the wording of `InvalidRequestError` messages ("has already been captured", "already been canceled", etc.) and our idempotent-retry detection silently breaks. The error code (`payment_intent_unexpected_state`) covers multiple states and isn't safe to match alone — hence the message-substring approach.

**Impact:** Currently no incident. A Stripe message change would cause a "second-attempt cancel of a hold that's already cancelled" to surface as PaymentPermanentError instead of being treated as success.

**Fix:** Add a unit test that constructs a fake `InvalidRequestError` with each current message string and asserts the detector returns True. A regression in detector logic OR a Stripe wording change shows up at CI rather than in production.

#### B65 — `/confirm` 422 for unsourceable items lacks `code:` discriminator

**File:** `scripts/checkout/router.py` — `confirm_checkout` unsourceable-items branch.

**Symptom:** Returns `{detail: {error, unsourceable_items}}`. The rest of the API follows `{detail: {error, code, ...}}` so the frontend can match on `code`. This pre-existing 422 doesn't.

**Impact:** Frontend matches on HTTP status today, so behaviorally fine. If a second 422 reason ever lands on this endpoint, the frontend can't distinguish them without parsing the `error` string.

**Fix:** Add `"code": "UNSOURCEABLE_ITEMS"` to the detail dict. Coordinated change with the frontend if it currently matches on the error-string shape.

### 4.2 Closed defects (catalog)

For full context on any shipped fix, see git history. One-line summary kept for "did we already address this?" queries:

| ID | Shipped | Summary |
|---|---|---|
| B1 | 2026-05-16 | `checkout_store._get_lock` race lookalike — `dict.setdefault` |
| B2 | 2026-05-15 | `SagaStatus.COMPENSATED` dead state — preserved as terminal |
| B3 | 2026-05-15 | `_compensate` outcome tracking (bundles B2, B18, B20–B22) |
| B4 | 2026-05-15 | Saga-level `wait_for(timeout=900s)` |
| B5 | 2026-05-16 | Pre-placement drift check |
| B6 | 2026-05-16 | Stripe key length rule unified in `key_format.py` |
| B7 | 2026-05-16 | `currency` locked at provider construction |
| B8 | 2026-05-16 | LEGO stockouts route to MANUAL_REVIEW (Option B) |
| B9 + B10 | 2026-05-16 | Cache invalidation owned by client modules |
| B12 | 2026-05-16 | `customer_message` translation table; paired with every `error` write |
| B13 | 2026-05-16 | Master-flag reason wording |
| B14 | 2026-05-16 | `/confirm` 409 disambiguation (`{error, saga_status, poll_url}`) |
| B15 | 2026-05-16 | `stripe.api_key` module-global documented |
| B16 | 2026-05-16 | `_parallel_brickowl_cancels` helper |
| B17 | 2026-05-16 | `payment_registry.register` replacement guard |
| B18 | 2026-05-15 | `_compensate` checkout_id fallback removed (bundled with B3) |
| B19 | 2026-05-16 | Order-call vs state-write split into two try blocks |
| B20 | 2026-05-15 | `_compensate` state-load failure raises (bundled with B3) |
| B21 | 2026-05-15 | Stripe cancel retry (bundled with B3) |
| B22 | 2026-05-15 | `_compensate` terminal precondition (bundled with B3) |
| B24 | 2026-05-16 | Cache sweeper strong reference (`_sweeper_task` module global) |
| B25 | 2026-05-16 | `_handle_saga_timeout` writes terminal state even on load failure |
| B32 | 2026-05-16 | Router initial save uses `SagaStatus.INITIATED.value`, not `"pending"` |
| B33 | 2026-05-16 | `order_from_lego` propagates `StockoutError` unwrapped |
| B34 | 2026-05-16 | Optimizer Pass 2 uses `.get().get()` (no defaultdict ghosts) |
| B37 + B38 | 2026-05-16 | `debug_optimize` matches /quote flow; dead `lego_available` removed |
| B39 | 2026-05-16 | L1 boot uses canonical `key_mode("live")` check |
| B40 | 2026-05-16 | Router initial save writes `customer_message: None` |
| B41 | 2026-05-18 | `jobs` lifecycle UPDATEs serialized via `pg_advisory_xact_lock` |
| B42 | 2026-05-19 | Two sources of truth removed (app.state.jobs → dispatcher only) |
| B43 | 2026-05-18 | `verify_alembic_head_matches_expected()` at boot |
| B44 | 2026-05-19 | Shadow writes deleted; dispatcher is single write path |
| B45 | 2026-05-18 | `checkout_store_pg.save` all-columns-explicit INSERT |
| B46 | 2026-05-18 | `_warn_unknown_keys` typo trap |
| B47 | 2026-05-18 | L1 boot refuses `CHECKOUT_ENABLED=true + DB_BACKEND=json` |
| B50 | 2026-05-18 | `smoke_test_db.py` imports psycopg2 + checks alembic_version |
| B53 | 2026-05-18 | `/generate` insert ordering + pre-dispatch shadow writes |
| B54 | 2026-05-18 | `/generate` validates `mosaic_block_width / mosaic_type / background_color_percent` |
| B55 | 2026-05-19 (code); 2026-05-20 (migration on Neon `dev`); main pending Phase F via U6b | Reconciler MANUAL_REVIEW handling via `sagas.hold_disposition` (HoldDisposition enum). Decision matrix updated in §9.3. New module-scope name `_CLEAN_TERMINAL_SAGA_STATUSES`. `test_reconcile_pg` cases 9a-9d. |
| B56 | 2026-05-19 | `record_hold` failure rollback now uses `cancel_hold_with_retry`; persistent failures emit `payment.hold_orphan` audit event (vocabulary §2.2). `saga.failed` data extended with `rollback_succeeded` / `rollback_error`. |
| B57 | 2026-05-19 | `saga_resume._recover_stripe_held` + `_handle_saga_timeout` branch 2 (saga.py) both use the shared `cancel_hold_with_retry` helper (new module `scripts/checkout/_cancel_helpers.py`). Naked `provider.cancel` removed from both sites. |
| B62 | 2026-05-19 | `CLAUDE.md` `cache.py` block updated — removed stale "Open bug B24" callout; B24's strong-reference fix shipped 2026-05-16. |

---

## 5. Open product questions

These are product-strategy questions that engineering cannot answer alone. Resolve before v1 ships:

1. **BrickOwl ordering strategy** (`docs/ORDER_OPTIMIZER.md §17.2`): Option A (Playwright; LAIGO collects payment) vs Option B (Cart URL redirect; customer pays BrickOwl directly).
2. **Allocation drift tolerance:** When fresh revalidation produces a higher total, does LAIGO eat the small delta (≤2%) or prompt the customer for "confirm new price"?
3. **Hourly financial exposure cap:** Max at-risk amount before `/confirm` returns 503? $500? $5000?
4. **Customer concurrency policy:** If a customer has an active Saga for a job, is a second `/quote` for the same job permitted, or is the job locked?
5. **Refund policy on commit-but-not-capture failures:** Contact customer for alternate payment, refund the marketplace orders (eating cost), or both?

**Resolved 2026-05-15 (kept for context):**
- LAIGO is the reseller (customer pays LAIGO; LAIGO pays suppliers).
- All-or-nothing partial-fail policy (any unrecoverable failure → compensation + refund).
- 5% authorization buffer; drift beyond → MANUAL_REVIEW.

---

## 6. Operational go-live checklist

Execute in order. Do not skip steps.

### 6.1 Code preconditions

- [ ] All §4 open defects either resolved or explicitly documented as deferred.
- [ ] §3 roadmap items 1–7 shipped (L0–L6 layered defense, Postgres state, pre-commit revalidation, MarketplaceAdapter, BrickOwl cancellation, capture retry, rate limit).
- [ ] §3 roadmap items 10 + 11 shipped (financial-exposure circuit breaker, test suite).
- [ ] L6 (§2.6) — all call sites migrated.

### 6.2 Configuration preconditions

- [ ] `.env.secrets` contains valid `STRIPE_SECRET_KEY=sk_test_...` (≥8 chars beyond prefix).
- [ ] `STRIPE_ENABLED = True` in `scripts/checkout/payment/stripe_provider.py` committed.
- [ ] `BRICKOWL_API_KEY` set and verified against `/checkout-debug/brickowl/...`.
- [ ] `LEGO_EMAIL` + `LEGO_PASSWORD` set; Playwright order verified in staging.
- [ ] `CHECKOUT_ENABLED=true` in Render dashboard.
- [ ] `DB_BACKEND=postgres` in Render dashboard.
- [ ] `DATABASE_URL` set (Neon pooler DSN — host contains `-pooler`).
- [ ] `RENDER=true` (auto-set by Render — verify).
- [ ] `STRIPE_API_VERSION` pinned.
- [ ] `STRIPE_CURRENCY=usd` (or whatever intended).
- [ ] Alerting destinations configured (Slack webhook, PagerDuty, email).

### 6.3 Pre-deploy verification

- [ ] `git status` clean; commit hash recorded.
- [ ] Test mode end-to-end against real Stripe TEST + real BrickOwl + LEGO.com staging — verify state transitions to `payment_captured`.
- [ ] MANUAL_REVIEW path tested (force a capture failure mid-Saga).
- [ ] Compensation tested (force a BrickOwl cancel failure during compensation).
- [ ] Saga timeout tested (monkey-patch `order_from_lego` to sleep > timeout).
- [ ] Drift fail-closed tested (force re-optimization > 5% buffer).
- [ ] Resume-on-restart tested: SQL-inject saga at `stripe_held`, restart, verify recovery.

### 6.4 Post-deploy verification (within 30 minutes of go-live)

- [ ] `curl https://<backend>/checkout/gate` returns `mode: "live"`, `is_open: true`, expected commit SHA.
- [ ] `curl https://<backend>/health` returns 200.
- [ ] Render healthcheck path is `/health`, **not** `/checkout/gate`.
- [ ] Boot log contains `payment.registry.registered provider=stripe mode=live` AND `[resume] examined N in-flight sagas`.
- [ ] `SELECT COUNT(*) FROM audit_events WHERE event='payment.skipped'` returns 0.
- [ ] First test order: $1 mosaic with internal email — verify all phases complete + audit log has expected event sequence.

### 6.5 Rollback plan

- **Soft disable (kill switch):** unset `CHECKOUT_ENABLED` in Render → restart. `/checkout/gate` flips to `mode: disabled` within ~30 seconds. In-flight Sagas continue (intentional).
- **Hard rollback:** redeploy previous commit. Postgres state survives; in-flight Sagas may need manual MANUAL_REVIEW.
- **Stripe-side panic:** rotate `STRIPE_SECRET_KEY` in Render. Next boot fails L1; service refuses traffic until env is fixed.

### 6.6 First-day operator vigilance

- Watch audit log for `saga.failed` and `saga.manual_review` rates.
- Watch Stripe dashboard for `requires_capture` PaymentIntents older than 1 hour (stuck Saga).
- Watch BrickOwl/LEGO order pages for unexpected duplicates.
- Have the kill switch one click away.

---

## 7. Cross-references

- **Diagnostic record:** `docs/CHECKOUT_AUDIT.md` — FMEA, gap analyses, target architecture, L0–L5 implementation history.
- **Operational reference:** `docs/ORDER_OPTIMIZER.md` — checkout module layout, configuration, manual-test workflows.
- **Project guide:** `CLAUDE.md` — module map, runtime invariants, key patterns for everyday changes.

---

## 8. Schema reference (current truth)

Six tables, migration `scripts/migrations/sql/0001_initial_schema.up.sql`.

### `jobs`
Mosaic generation lifecycle. One row per `POST /generate`. Source of truth via `scripts/jobs_store_pg.py`.

Columns: `job_id` PK, `status` (queued/running/complete/failed/timed_out), `mosaic_type` (2d/3d), `width_blocks`, `background_pct`, `dither`, `upload_filename`, `progress_pct`, `error_message`, `queued_at`, `started_at`, `completed_at`, `ttl_expires_at`, `customer_email`, `customer_ip`, `user_agent`.

Indices: `(status, queued_at DESC) WHERE status IN ('queued','running')`; `(ttl_expires_at) WHERE status IN ('complete','failed','timed_out')`.

### `checkouts`
One row per `POST /quote`. Holds the optimized allocation + customer metadata.

Columns: `checkout_id` PK, `job_id` FK → jobs (CASCADE), `shipping_country` (CHAR(2)), `shipping_zip`, `customer_email`, `allocation` JSONB, `unsourceable_items` JSONB, `created_at`, `expires_at`.

### `sagas`
One row per `POST /confirm`. Source of truth for saga state.

Columns: `checkout_id` PK FK → checkouts (RESTRICT), `job_id` FK → jobs (RESTRICT), `saga_status` (8 values), `payment_provider`, `payment_mode`, `payment_hold_id`, `payment_authorized_cents`, `total_charged_cents`, `brickowl_order_ids` JSONB, `lego_order_id`, `error_message` (operator-internal), `customer_message` (customer-facing), `manual_review_reason`, **`hold_disposition`** (NULLABLE TEXT, CHECK `IN ('cancel_safe','operator_decides')` OR NULL — B55), `initiated_at`, `last_transition_at`, `completed_at`.

**B23 enforcement:** `sagas_one_active_per_job_idx` is a partial unique index on `(job_id)` where saga_status is non-terminal. Router translates the resulting `UniqueViolationError` to HTTP 422 with `code="ACTIVE_CHECKOUT_EXISTS"`.

**B55 — `hold_disposition`:** read by `reconcile_orphan_holds` only when `saga_status='manual_review'`. `cancel_safe` permits the reconciler to release the hold as a safety net; `operator_decides` / NULL keep the reconciler hands-off (preserves operator's capture option). Set by every MANUAL_REVIEW write site in `saga.py` + `saga_resume.py`. See `HoldDisposition` in `scripts/checkout/models.py`.

### `payment_holds`
Reconciliation index. Every Stripe hold ever created. Source of truth via `scripts/checkout/payment_holds_store.py`.

Columns: `hold_id` PK, `checkout_id` FK → checkouts (RESTRICT), `provider`, `mode`, `amount_authorized_cents`, `currency` (CHAR(3)), `last_known_status` (requires_capture/succeeded/canceled/unknown), `last_reconciled_at`, `created_at`.

### `audit_events`
L6 audit log. Append-only.

Columns: `id` BIGSERIAL PK, `event`, `ts`, `request_id`, `job_id`, `checkout_id`, `actor_type`, `actor_ip` INET, `actor_user_agent`, `data` JSONB.

Indices: `(ts DESC)`, `(event, ts DESC)`, `(job_id, ts DESC) WHERE job_id IS NOT NULL`.

### `schema_meta`
Single-row app-level metadata. Currently unused (B49).

### Cross-table contracts

- **All id columns TEXT** to preserve existing string IDs (no UUID type column).
- **All timestamps TIMESTAMPTZ** — zone-less TIMESTAMP is a Postgres footgun.
- **JSONB shapes owned by Pydantic models** in `scripts/checkout/models.py`. Do NOT query into JSONB via `->>`/`->`from application code; deserialize via Pydantic.

### Schema evolution discipline (B45 contract)

When adding a column to any table:
- **NOT NULL with DB-side DEFAULT** — INSERTs still work. Update the column tuple (`_JOB_COLS`, `_CHECKOUT_COLS`, `_SAGA_COLS`) only if update paths need it.
- **NOT NULL without DEFAULT** — REQUIRED to update the explicit INSERT helper AND the column tuple in the SAME PR as the migration. Migration alone crashes production at first write.
- **Nullable** — INSERTs default to NULL via explicit VALUES list. Update column tuple if partial-update paths should write it.

**For `jobs` specifically:** schema changes touch THREE files because of the dual backend — `jobs_store_pg.py`, `jobs_store_json.py`, and the migration. Post-Phase-F (after JSON backend deleted), this collapses to one file.

---

## 9. Database migration

### 9.1 Terminology (READ FIRST)

"Branch" is overloaded:
- **git branch** = VCS branch in the LAIGO repo.
- **Neon branch** = copy-on-write database clone in Neon.

Both have a default branch called `main`. Qualify every reference.

### 9.2 Phase history (shipped)

| Phase | Shipped | What it delivered |
|---|---|---|
| A — Provision & connect | 2026-05-16 | Neon `laigo` project (PG 17.8, us-east-1, ARM64). Pooler DSN in `.env.secrets`. `scripts/db.py` asyncpg pool with `init_pool/close_pool/get_pool/is_postgres_backend`. JSONB type codec. |
| B — Schema + alembic | 2026-05-17 | `0001_initial_schema` applied to Neon `main` and `dev`. `verify_schema()` + `_EXPECTED_SCHEMA_VERSION` smoke-test on boot. Alembic wrapper around raw SQL. `verify_alembic_head_matches_expected` at boot for code-drift detection. |
| D-foundation — Jobs-table shadow writes | 2026-05-18 | `scripts/jobs_db.py` (since deleted) populated `jobs` rows at lifecycle points so Phase C's FK constraints would resolve. |
| C — Checkout state to Postgres | 2026-05-18 | `checkout_store_pg.py` + `checkout_store_dispatch.py`. `ActiveCheckoutExistsError` raised on B23 violation → router 422 with `code="ACTIVE_CHECKOUT_EXISTS"`. saga.py + router.py + debug_router.py switched to dispatcher. |
| D step 2 — Replace `app.state.jobs` with DB | 2026-05-19 | Trio `jobs_store_{pg,json,dispatch}.py` with 23/23 API parity. Main.py fully on the dispatcher (`queue.Queue` removed; `dequeue_next()` with `SELECT FOR UPDATE SKIP LOCKED`; progress mirroring; cleanup_expired). `jobs_db.py` deleted. Resolves B42 + B44. |
| E.1 — Saga resume-on-startup | 2026-05-19 | `saga_resume.py` routes every non-terminal saga to a safe terminal state at boot. `audit.py` writes structured events. First call site `gate.confirm_rejected` wired. `payment_holds_store.py` wired at saga create_hold + capture/cancel. Closes audit FMEA #3 (RPN 450). |
| 0002 — `sagas.hold_disposition` | 2026-05-19 (code); 2026-05-20 (Neon `dev` applied); Neon `main` pending Phase F (U6b) | Resolves B55. Nullable TEXT column with CHECK constraint `IN ('cancel_safe','operator_decides')` OR NULL. Set by every MANUAL_REVIEW write site (saga.py × 6, saga_resume.py × 3). Reconciler reads it via the `fetch_for_reconcile` LEFT JOIN to choose between safety-net cancel and hands-off. |

For commit-level detail, see git log filtered by phase tag.

### 9.3 Phase E (partial — remaining steps)

**Shipped (2026-05-19):** Step 1 (`resume_in_flight_sagas`), Step 3 (`audit.emit` + first call site), payment_holds wiring at create_hold/capture/cancel, **Step 2 (`reconcile_orphan_holds` periodic task + `PaymentProvider.get_hold_status` Protocol extension)**.

**Step 2 (shipped 2026-05-19) — `reconcile_orphan_holds()` periodic task**

`scripts/checkout/reconcile.py` runs every `RECONCILE_INTERVAL_SECONDS` (default 300s, env-configurable, floor 60s). The task is started from `Main.py` lifespan after the cache sweeper (same C1/B24 strong-reference pattern as `_running_sagas` and `_sweeper_task`) and cancelled cleanly during shutdown before `close_pool`.

Backend-gated: no-op when `DB_BACKEND != postgres`. Caller-gated: no-op when no `PaymentProvider` is registered (gate closed).

Decision matrix (Stripe status × persisted saga_status × `sagas.hold_disposition`):

| Stripe says | Saga says | Disposition | Action |
|---|---|---|---|
| `succeeded` | anything | — | Mirror payment_holds.last_known_status='succeeded'; preserve saga |
| `canceled` | clean terminal (payment_captured / compensated / failed) OR MANUAL_REVIEW | — | Mirror payment_holds; preserve saga |
| `canceled` | non-terminal (stripe_held/orders_placed/fallback_ordered) | — | Mirror + escalate saga to MANUAL_REVIEW with verbose reason |
| `requires_capture` | NULL / clean terminal / `initiated` | — | Orphan — call provider.cancel; mirror on success |
| `requires_capture` | MANUAL_REVIEW | `cancel_safe` | Safety net — call provider.cancel (B55) |
| `requires_capture` | MANUAL_REVIEW | `operator_decides` OR NULL | **Skip** — bump reconciled_at, do NOT touch Stripe (B55 — preserves operator's capture option) |
| `requires_capture` | `stripe_held` AND stale >1h | — | Stuck — mark saga MANUAL_REVIEW (no disposition set); do NOT touch Stripe |
| `requires_capture` | `orders_placed`/`fallback_ordered` AND stale >1h | — | Same as stuck stripe_held |
| `requires_capture` | non-terminal AND recent <1h | — | Skip — saga healthy; bump reconciled_at (1h cooldown) |
| `unknown` (unmapped Stripe status) | anything | — | Mark payment_holds='unknown'; stop re-querying |
| `PaymentRetryableError` from provider | anything | — | Skip — do NOT bump reconciled_at; retry next tick |
| `PaymentPermanentError` from provider | anything | — | Mark payment_holds='unknown'; stop re-querying |

The matrix is exercised by `scripts/test_reconcile_pg.py` (15 assertions, all branches including B55's 4 disposition cases).

**Step 4 — End-to-end recovery test** — closed-by-integration 2026-05-19

Live `/confirm` exercise was deferred because:

1. The intended timing (kill uvicorn during the millisecond-wide `stripe_held` window) is unreliable in a single-worker dev environment, and
2. The local venv's certifi bundle doesn't include the corporate root CA (operator action **U1** in §9.5), so the SDK can't verify `api.stripe.com`.

Coverage substitution: `scripts/test_phase_e_pg.py` exercises every `resume_in_flight_sagas` routing branch end-to-end against real Neon Postgres (15 assertions):

- `initiated` → FAILED + `saga.failed` audit emitted
- `stripe_held` → MANUAL_REVIEW when no provider registered
- `stripe_held` → FAILED via provider.cancel + `payment_holds` mirrored
- `stripe_held` → MANUAL_REVIEW on cancel failure
- `orders_placed` → MANUAL_REVIEW with full runbook
- idempotency: re-run produces no new audit events
- defensive: `stripe_held` with NULL hold_id → MANUAL_REVIEW
- `audit.emit` envelope + failure-swallowing contract
- `payment_holds` record_hold / mark_status / fetch_for_reconcile

The boot path itself was verified during Action A: `[resume] examined 0 in-flight sagas (all routed cleanly)` confirms lifespan calls `resume_in_flight_sagas()`.

**To run the deferred live exercise when U1 is resolved:**

```powershell
.venv\Scripts\python.exe -m scripts.phase_e_4_inject inject
# Restart uvicorn — boot log should show "[resume] examined 1 in-flight sagas"
.venv\Scripts\python.exe -m scripts.phase_e_4_inject verify <checkout_id>
.venv\Scripts\python.exe -m scripts.phase_e_4_inject cleanup <checkout_id>
```

🛑 The harness REFUSES to start with `sk_live_` keys (sk_test_ prefix check).

**Phase E exit criteria:**
- ✅ `resume_in_flight_sagas()` routes every documented saga_status.
- ✅ `reconcile_orphan_holds()` runs every `RECONCILE_INTERVAL_SECONDS`, held by strong reference, idempotent — shipped 2026-05-19 (code only; live exercise needs Stripe TEST + actual sagas).
- ✅ `audit.emit()` works; first call site wired.
- ✅ Mid-saga restart test — closed-by-integration via `test_phase_e_pg` + boot-log verification 2026-05-19 (live Stripe exercise deferred until U1).
- 📦 §0 + §9.5 updated when complete.

### 9.4 Phase F — Cutover (≈½ day + 1 week observation + 1 hour cleanup)

**Goal:** Flip production. `DB_BACKEND=postgres` on Render. Validate. Wait one week. Delete the JSON code path.

**Prerequisites:**
- ✅ Phases B+C+D+E complete on git `main`.
- ✅ B55-B57 code shipped 2026-05-19 (HIGH-severity audit fixes; live-Stripe gating).
- ✅ U3 — Render pre-deploy hook configured for `alembic upgrade head` (done 2026-05-19).
- ✅ U4 — `DATABASE_URL` + `DATABASE_URL_DIRECT` env vars added to Render (done 2026-05-19).
- ❌ U5 — Neon project upgraded Free → Launch ($19/mo) in the same window. Free-tier autosuspend would cold-start the first `/status` poll.
- ✅ U6a — Migration 0002 applied to Neon `dev` (2026-05-20). ❌ U6b — Apply to Neon `main` during the cutover window; Render's pre-deploy hook (U3) handles this automatically on the first deploy after Phase F env vars are in place. Verify in Render deploy logs (`Running upgrade 0001 -> 0002`).
- 🛑 Schedule cutover during low-traffic hours. Pre-launch this is moot; make it habit.

**Step 1 — Deploy with `DB_BACKEND=json` first** (≈15 min)

🛑 Do NOT flip `DB_BACKEND` and deploy at the same time.

Deploy git `main` to Render with `DB_BACKEND=json`. Pool initializes via pre-deploy hook (`alembic upgrade head`); no app code reads from it.

Verify:
- `GET /health` returns 200.
- `POST /generate` with test image succeeds.
- `GET /checkout/gate` returns `mode=test` (TEST keys) or `mode=live` (live keys).

**Step 2 — Smoke test pool is open** (≈5 min)

Confirm the lifespan log line `"DB pool skipped (DB_BACKEND=json; Phase F not yet flipped)"` appears. That proves `DB_BACKEND=json` is the active path.

**Step 3 — Flip the switch** (≈5 min)

Render dashboard → Environment → `DB_BACKEND=postgres`. Save. Render restarts.

Watch deploy logs for:
- `"DB pool initialized (Neon Postgres backend active)"` ← MUST appear
- `verify_schema()` success ← MUST appear
- `[resume] examined 0 in-flight sagas (all routed cleanly)` ← expected (empty DB pre-launch)
- Service comes up healthy.

🛑 If lifespan errors, flip `DB_BACKEND=json` immediately and investigate offline.

**Step 4 — End-to-end production smoke** (≈30 min)

Run the full flow against the live URL (still TEST mode, no real money). Verify rows land in `jobs`, `checkouts`, `sagas`, `payment_holds`.

**Step 5 — Upgrade to Neon Launch tier** (≈5 min, same window)

Neon dashboard → laigo project → Billing → Launch tier ($19/mo). Confirms autosuspend OFF.

**Step 6 — One-week observation window**

🛑 **Do NOT delete the JSON code path yet.** Rollback to JSON must remain a single env-var flip for one week.

Daily for 7 days:
- `SELECT COUNT(*) FROM sagas WHERE saga_status='manual_review' AND last_transition_at > NOW() - INTERVAL '1 day'`.
- Check Render error log for `verify_schema` mismatches or pool exhaustion.
- Check Neon dashboard for slow queries or connection-cap warnings.

If any flare: roll back to `DB_BACKEND=json`, file the issue, fix offline, re-attempt.

**Step 7 — Delete the JSON code path** (≈1 hour, after 7 clean days)

🔧 Once 7 consecutive clean days pass:
- Delete `scripts/checkout/checkout_store.py` (JSON impl).
- Delete `scripts/checkout/checkout_store_dispatch.py`.
- Delete `scripts/jobs_store_json.py`.
- Delete `scripts/jobs_store_dispatch.py`.
- Rename `scripts/checkout/checkout_store_pg.py` → `scripts/checkout/checkout_store.py`.
- Rename `scripts/jobs_store_pg.py` → `scripts/jobs_store.py`.
- Update all import sites to drop the dispatch layer.
- Remove `DB_BACKEND` env var everywhere.
- Remove `is_postgres_backend()` from `scripts/db.py`.
- Remove `init_pool()`'s no-op-when-json branch.
- Update CLAUDE.md to drop `DB_BACKEND` from config table.

📦 Separate commit ≥7 days after Step 3.

**Phase F exit criteria:**
- ✅ Live service on `DB_BACKEND=postgres` ≥7 days.
- ✅ Neon Launch tier active.
- ✅ JSON path deleted in follow-up commit.
- ✅ `DB_BACKEND` env var removed everywhere.

### 9.5 Open operator actions

| # | Action | Phase | Blocker level |
|---|---|---|---|
| U1 | Resolve corporate-proxy SSL cert root cause (so `pip install` works without `--trusted-host`) | Eventually | LOW |
| ~~U3~~ | ~~Configure Render pre-deploy hook for `alembic upgrade head`~~ | F | ✅ Done 2026-05-19 |
| ~~U4~~ | ~~Add `DATABASE_URL` + `DATABASE_URL_DIRECT` env vars to Render~~ | F | ✅ Done 2026-05-19 |
| U5 | Upgrade Neon project Free → Launch ($19/mo) | F (same window as `DB_BACKEND=postgres` flag flip on Render) | HIGH at cutover |
| U6a | ~~Apply migration 0002 to Neon `dev`~~ via `alembic upgrade head` against the DIRECT DSN. | B55 | ✅ Done 2026-05-20 |
| U6b | Apply migration 0002 to Neon `main` — Render's pre-deploy hook (U3) handles this automatically on the next deploy after Phase F env vars are in place. Verify in deploy logs. | B55 / Phase F | HIGH at cutover |
| U7 | Commit packaging strategy for all Phase A-E + B55-B57 work | Whenever | LOW |

### 9.6 Risk register

| Risk | Phase | Mitigation in place | Still needed |
|---|---|---|---|
| Schema bug discovered after deploy | F | Throwaway Neon branch test before applying to `main` | Hot-fix migration (forward-only) preferred over rollback |
| Pool exhaustion under load | F+ | `max_size=10` headroom; `command_timeout=10s` | Bump to 20 if `MAX_WORKERS>1`; alert on `PoolExhausted` |
| Neon outage during cutover | F | Pre-launch — no customer impact | Documented incident response post-launch |
| `--trusted-host` install becomes habit | n/a | Documented as one-shot bypass | U1: solve root cert problem |
| `DB_BACKEND` flag left at `postgres` locally without DSN | C+ | `init_pool()` refuses boot with clear error | None additional |

### 9.7 Adding a new alembic migration

Schema lives in `scripts/migrations/sql/000N_<slug>.{up,down}.sql`; thin alembic wrappers in `scripts/migrations/versions/000N_<slug>.py`.

1. Author `000N_<slug>.up.sql` and `.down.sql`. Do NOT wrap in `BEGIN; ... COMMIT;` — alembic wraps in its own transaction (Postgres has no nested transactions).
2. Author the wrapper `000N_<slug>.py` with `revision = "000N"`, `down_revision = "000(N-1)"`.
3. Bump `_EXPECTED_SCHEMA_VERSION` in `scripts/db.py` to `"000N"`.
4. Test on a throwaway Neon branch: `$env:ALEMBIC_DATABASE_URL = '<direct DSN>'; alembic upgrade head`.
5. After validation, apply to Neon `main` and `dev` branches.

`env.py` refuses pooler DSNs with an actionable error — alembic needs session mode (direct endpoint).
