# Pre-Release Payment Checklist

**Audience:** LAIGO maintainers — single source of truth for "what must be true before the first real $1 moves."
**Scope:** Everything that gates LAIGO's go-live for monetized checkout. Items in this document are launch-blocking unless explicitly tagged otherwise.
**Posture:** Stripe wired but disabled (`STRIPE_ENABLED = False`). BrickOwl catalog API not granted. LEGO.com Playwright never run in live. No customer money has moved.

This file consolidates material that previously lived in `docs/CHECKOUT_AUDIT.md` §8/§9/§10 (L6) plus undocumented bugs discovered during the L5 review. The audit doc remains the diagnostic record; this file is the **gating checklist**.

---

## 0. Status snapshot

| Area | State | Where |
|---|---|---|
| L0 gate.py | ✅ Shipped | `scripts/checkout/gate.py` |
| L1 lifespan boot assertion | ✅ Shipped | `scripts/Main.py` |
| L2 `/checkout/gate` | ✅ Shipped | `scripts/checkout/gate_router.py` |
| L3 `/confirm` Depends | ✅ Shipped | `scripts/checkout/dependencies.py` |
| L4 Saga pre-flight | ✅ Shipped | `scripts/checkout/saga.py` |
| L5 PaymentProvider + registry + Stripe provider | ✅ Shipped | `scripts/checkout/payment/` |
| L6 audit log subsystem | ❌ Not built | This document, §2 |
| Capture retry + MANUAL_REVIEW | ✅ Shipped | `scripts/checkout/saga.py` |
| 5% hold buffer + drift fail-closed | ✅ Shipped | `scripts/checkout/saga.py` |
| Postgres-backed state + resume-on-restart | 🟡 Phase A shipped 2026-05-16. Phases B–F pending; per-phase status in §9.6.1. Host = **Neon** (PG 17.8 / us-east-1; locked 2026-05-16; §9.3.11.1). 6 tables; 6.5 engineer-days planned. **Per-phase operational playbooks: §9.5. Live progress dashboard: §9.6.** | §3 roadmap item 2 / §9 |
| Pre-commit revalidation | ❌ Not built | §3 roadmap item 3 |
| MarketplaceAdapter Protocol | ❌ Not built | §3 roadmap item 4 |
| BrickOwl cancellation (Playwright) | ❌ Not built | §3 roadmap item 5 |
| Per-source rate limit + shared HTTPX | ❌ Not built | §3 roadmap item 7 |
| Listing parse sanity checks | ❌ Not built | §3 roadmap item 8 |
| Optimizer Pass 2 redesign | ❌ Not built | §3 roadmap item 9 |
| Bounded financial exposure circuit breaker | ❌ Not built | §3 roadmap item 10 |
| Test suite | ❌ Not built | §3 roadmap item 11 |
| LEGO.com semaphore + duplicate-order detection | ❌ Not built | §3 roadmap item 13 |
| Customer confirmation email | ❌ Not built | §3 roadmap item 14 |
| B4 Saga-level timeout | ✅ Shipped 2026-05-15 (Phase 1.1) | `scripts/checkout/saga.py` |
| B3 Compensation outcome tracking (bundles B2, B18, B20–B22) | ✅ Shipped 2026-05-15 (Phase 1.2) | `scripts/checkout/saga.py` |
| B5 Pre-placement drift check | ✅ Shipped 2026-05-16 (Phase 1.3) | `scripts/checkout/saga.py` |
| B1 Lock TOCTOU lookalike-race | ✅ Shipped 2026-05-16 (Phase 2.1) | `scripts/checkout/checkout_store.py` |
| B19 Order placed but state write fails | ✅ Shipped 2026-05-16 (Phase 2.2) | `scripts/checkout/saga.py` |
| B13 Master-flag reason wording | ✅ Shipped 2026-05-16 (Phase 3.1) | `scripts/checkout/gate.py` |
| B17 Registry replace safety | ✅ Shipped 2026-05-16 (Phase 3.2) | `scripts/checkout/payment/registry.py` |
| B14 `/confirm` 409 disambiguation | ✅ Shipped 2026-05-16 (Phase 3.3) | `scripts/checkout/router.py` |
| Pre-DB-migration bundle: B6, B7, B8 (Option B), B9, B10, B12, B15, B16 | ✅ Shipped 2026-05-16 | §4 (per-bug entries flipped); §8 (H1+H2/H3/H4/H5/H7/H8/H9/H10/H12/H14) |
| Pre-DB-migration final sweep: B24, B25, H6 | ✅ Shipped 2026-05-16 | §4 (B24, B25); §8 (H6) |
| Pre-DB-migration audit pass: B32 (saga_status pending mismatch), B33 (StockoutError wrapping), B34 (optimizer ghost entries), B37 (debug_optimize parity), B38 (debug_optimize dead lego_available), B39 (Main.py lenient sk_live), B40 (router missing customer_message) | ✅ Shipped 2026-05-16 | §4 (B32–B40 entries) |
| Newly surfaced (open, post-bundle): B27 (currency allowlist hardcoded), B28 (BrickOwl cancel no per-order retry — LATENT until roadmap #5), B29 (LEGO StockoutError plumbed but not raised), B30 (unexpected-hold mapped to transient), B31 (audit-doc mapping table drift) | ❌ Open (none launch-blocking; B28 LATENT) | §4 |
| Newly surfaced from audit (open, DB-coupled): B35 (non-atomic file writes), B36 (semaphore per-call) | ⏳ Subsumed by §9 (JSON files disappear / future concurrent-saga design) | §4 |
| Still open (DB-coupled, deferred to §9): B11, B23, B26 | ⏳ Subsumed/restructured by Postgres migration | §4, §9 |

**Hard launch gate:** items §3 #1–#7, plus B1–B5 in §4, must be resolved before any real Stripe key is configured.

**Phase 3.4 (B12 — customer-facing error translation) shipped 2026-05-16** as part of the pre-DB-migration bundle. **Pre-DB-migration runway is now CLEAR** — every open defect either (a) requires the DB migration to fix without throwaway work, (b) is newly-surfaced LOW/LATENT/DOC (B27–B31), or (c) is gated on other deferred work (roadmap #5 for B28, DOM-detection for B29, production data for B30). Next major work item is §9 Phase A (Neon provisioning).

---

## 1. Why this exists

The checkout system's correctness is defended by six layers (L0–L6). Five are shipped. The sixth — the structured audit log — is the only post-hoc signal that any layer fails. Without it, a regression in L0–L5 produces silent loss until a customer or auditor surfaces it.

The roadmap items below close the remaining structural risks identified in the FMEA (`docs/CHECKOUT_AUDIT.md §7`). The bugs in §4 are real defects in the L5 implementation that surfaced during code review but were not folded into the audit doc.

If any item below is open at launch time, the system **can** still place orders — but the failure surface is unbounded. Each item has a specific RPN-based mitigation rationale. Do not skip an item without writing down the explicit cost-of-not-doing-it.

---

## 2. L6 — Audit log subsystem

L6 is the post-hoc detection layer for everything else. L0–L5 stop regressions from causing immediate damage; L6 makes sure that if a regression does land, an alert fires before five customers are affected instead of five thousand.

### 2.1 Event envelope (canonical schema)

```python
{
  "event": "gate.confirm_rejected",         # snake_case, dot-separated namespace
  "ts": "2026-05-15T10:30:00.123Z",          # ISO 8601 UTC, millisecond precision
  "request_id": "req_abc123",                # from FastAPI middleware (future), or null
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
  "data": { ... }                            # event-specific payload, JSON-serializable
}
```

Frozen schema. New events extend `data`; never rename top-level keys.

### 2.2 Event vocabulary (locked names; do not rename without migration)

| Event | Trigger | Data payload |
|---|---|---|
| `gate.confirm_rejected` | L3 returns 503 to `/confirm` | `{mode, reasons[]}` |
| `gate.saga_rejected` | L4 aborts Saga before any work | `{mode, reasons[], invocation_source: "confirm"\|"resumption"\|"direct"}` |
| `saga.started` | execute_checkout_saga begins | `{mode, payment_provider}` |
| `saga.stripe_held` | Stripe hold succeeds | `{hold_id, amount_cents, currency}` |
| `saga.orders_placed` | All marketplace orders submitted | `{order_ids: {seller: [ids]}}` |
| `saga.captured` | Stripe capture succeeds | `{hold_id, captured_amount_cents}` |
| `saga.failed` | Saga transitions to FAILED | `{error_message, last_step}` |
| `saga.compensated` | Compensation finished | `{cancelled_orders, manual_required}` |
| `saga.manual_review` | Saga enters MANUAL_REVIEW | `{reason, hold_id, authorized_cents, last_error}` |
| `marketplace.order_placed` | Single marketplace order succeeded | `{seller_id, marketplace_order_id, items_count}` |
| `marketplace.order_cancelled` | Cancellation succeeded | `{seller_id, marketplace_order_id}` |
| `marketplace.cancel_failed` | Cancellation API failed | `{seller_id, order_id, error}` — **always alerts** |
| `payment.hold_created` | Payment hold via provider | `{hold_id, amount_cents, mode}` |
| `payment.captured` | Capture via provider | `{hold_id, captured_amount_cents}` |
| `payment.cancelled` | Cancel via provider | `{hold_id}` |
| `payment.skipped` | (LEGACY — must never appear in prod after L4) | `{reason}` |

A non-zero `payment.skipped` count in production is **a P0 page**. The event is wired only to detect a regression that would otherwise reintroduce RPN #1.

### 2.3 Storage

**Option A — Append-only NDJSON (recommended for v1 launch):**
- Path: `outputs/audit/events-YYYY-MM-DD.ndjson` (one event per line, daily rotation)
- Compressed after rotation (`gzip` the previous day's file)
- Indexed by grep + jq for ad-hoc queries
- Cost: ~zero
- Drawback: hard to query at scale, single host only

**Option B — Postgres table (eventual target):**
- Table: `audit_events(id BIGSERIAL, ts TIMESTAMPTZ, event TEXT, request_id TEXT, job_id TEXT, data JSONB)`
- Indexed: `(ts DESC)`, `(event, ts DESC)`, GIN on `data`
- Easier alerting (SQL), survives host loss
- Drawback: needs Postgres (also coming for §3 roadmap item 2)

**Recommendation:** ship L6 with Option A. Migrate to Option B when roadmap item 2 (Postgres state) lands — same migration window, low marginal cost.

### 2.4 Retention

| Class | Retention | Why |
|---|---|---|
| Financial (`payment.*`, `saga.*`, `marketplace.*`) | 1 year | Chargeback window (180 days) + reconciliation |
| Control-plane (`gate.*`) | 90 days | Operational diagnosis only |

### 2.5 Public API (locked)

```python
# scripts/checkout/audit.py
def emit(
    event: str,
    *,
    subject: dict | None = None,    # {job_id, checkout_id, session_id}
    actor: dict | None = None,      # {type, ip, user_agent}
    data: dict | None = None,       # event-specific payload
    request_id: str | None = None,  # from middleware (future)
) -> None: ...
```

Sync function. Internally buffered + flushed asynchronously to avoid blocking request paths. **Never raises** — audit must not break checkout. Logs errors via stderr.

### 2.6 Migration of existing call sites

| Site | Today | After L6 |
|---|---|---|
| `dependencies.py::require_checkout_gate_open` | `logger.warning("checkout.gate.l3_rejected ...")` | `audit.emit("gate.confirm_rejected", subject={...}, data={...})` |
| `saga.py::execute_checkout_saga` (gate-closed path) | `logger.critical("[saga] ... GATE CLOSED ...")` | `audit.emit("gate.saga_rejected", ...)` |
| `saga.py` state transitions | `logger.info(...)` | `audit.emit("saga.*", ...)` at each transition |
| `saga.py` MANUAL_REVIEW writes | `logger.critical("[saga] ... MANUAL_REVIEW — ...")` | `audit.emit("saga.manual_review", ...)` |

Keep the existing logger lines for a month after L6 ships so we can cross-check audit completeness, then remove.

### 2.7 Alerting (post-L6)

| Alert | Threshold | Action |
|---|---|---|
| `count(event="payment.skipped") > 0 in 24h` | Impossible state | **Page on-call immediately — RPN #1 regression** |
| `count(event="gate.confirm_rejected") > 10 per 5min` | Frontend bug or attack | Page on-call |
| Any `event="marketplace.cancel_failed"` | Manual review needed | Slack notify with order_id |
| Any `event="saga.manual_review"` | Operator action required | Slack notify with reason |
| `count(event="saga.failed") / count(event="saga.started") > 0.05 in 1h` | Reliability drop | Slack notify |

### 2.8 Audit-log gap today (without L6)

The only signal that any layer rejected a customer is a WARNING line on stdout:
```
checkout.gate.l3_rejected mode=disabled reasons=['CHECKOUT_ENABLED is not set to true', ...]
```
That lands in Render's log stream and stays there until log retention rolls it out. No structured event, no counter, no alert wiring, no per-customer correlation, no per-attempt audit row.

**Three operational scenarios where this bites today:**

1. **Frontend bug masquerading as customer error.** A frontend that doesn't poll `/checkout/gate` before showing "Confirm" fires `/confirm` POSTs into a closed gate. Each one logs a WARNING. Without aggregation you can't easily distinguish "one user hit Confirm twice" from "1000 users blocked by stale UI."
2. **Probing / abuse detection.** Someone scripting against `/confirm` to fingerprint the system generates many `l3_rejected` events. Without rate aggregation this hides in normal log noise.
3. **Kill-switch verification.** When you flip `CHECKOUT_ENABLED` off and want proof traffic actually got blocked (compliance / postmortem trail), you have only the WARNING log. Greppable, not auditable.

**Interim mitigation until L6 ships:** configure a saved log search in Render for the strings `checkout.gate.l3_rejected`, `MANUAL_REVIEW`, and `payment.skipped`. Set a notification rule on volume > 0 if you want passive alerting.

---

## 3. Pre-launch roadmap

Items 1–7 are the **minimum-viable pre-launch set**. Items 8–14 reduce tail-risk and are strongly recommended but can ship in the first month post-launch.

| # | Item | RPN(s) addressed | Effort | Status |
|---|---|---|---|---|
| 1 | Layered Stripe-disabled defense (gate + boot + router + Saga + provider + audit) | #1 (810) | S–M | ✅ L0–L5 shipped 2026-05-15. L6 outstanding (§2). |
| 2 | Move Saga state to Postgres; add resume-on-startup; partial-unique-index per-job | #3 (450), #5 (324), #17 (112), #16 (80) | M | ❌ Open. Single most-impactful pre-launch change after L5. |
| 3 | Pre-commit revalidation per adapter | #4 (448), #14 (120), #15 (126) | M | ❌ Open. Roughly half the cart-snatching window closes here. |
| 4 | MarketplaceAdapter Protocol; refactor LEGO.com + BrickOwl into adapters | #2 (504) prep; #20 (120); structural | M | ❌ Open. Required for everything else to compose cleanly. |
| 5 | Implement BrickOwl Playwright cancellation; reorder commits so reversible first | #2 (504) | L | ❌ Open. **Cannot launch without this if BrickOwl is in the loop.** |
| 6 | Capture retry with backoff + operator alert; explicit `MANUAL_REVIEW` state | #7 (252) | S | ✅ Shipped 2026-05-15 (bundled with L5). |
| 7 | Per-source rate-limit budgets via `AsyncLimiter` + lifespan HTTP clients | #8 (240) | S | ❌ Open. |
| 8 | Listing parse sanity checks (price/qty bounds) | #10 (243) | S | ❌ Open. |
| 9 | Optimizer Pass 2 redesign + sanity tests | #11 (150), #20 (120) | M | ❌ Open. |
| 10 | Bounded financial exposure circuit breaker | #19 (216) | S | ❌ Open. |
| 11 | Test suite covering CHECKOUT_AUDIT §6.10 minimums | #21 (189) | M | ❌ Open. |
| 12 | Drift tolerance + customer "confirm new price" round-trip | #9 (245) | M | ❌ Open. |
| 13 | LEGO.com semaphore + recent-order-list duplicate-detection heuristic | #6 (288), #18 (168) | M | ❌ Open. |
| 14 | Customer confirmation email | #24 (40) | S | ❌ Open. |

**Effort scale:** "S" = ≤1 engineer-day, "M" = ≤1 engineer-week, "L" = >1 engineer-week.

---

## 4. Undocumented bugs

These were discovered during the post-L5 Saga walkthrough. Each is a real defect in current code, none of them are in the audit doc, and several are launch-blocking. Severity uses the same scale as the audit FMEA (S × O × D).

### B1 — `_get_lock()` TOCTOU race on first access — **HIGH** — ✅ SHIPPED 2026-05-16 (Phase 2.1)

**File:line:** `scripts/checkout/checkout_store.py:31` (single line)

**Original symptom:** The `if job_id not in _locks: _locks[job_id] = asyncio.Lock()` pattern reads like a TOCTOU race even though it is actually atomic under pure single-threaded asyncio (no `await` between check and write, so no coroutine can interleave). The pattern was a latent landmine: any future refactor that introduces a thread pool, multi-process worker, or an `await` between check and write would turn the lookalike race into a real one.

**What shipped:** `return _locks.setdefault(job_id, asyncio.Lock())` — one line. `dict.setdefault` is a single C-level atomic operation under the GIL. Behavior is identical for current single-threaded asyncio; the new code is explicitly atomic and immune to the future refactor risks above.

**Verified:** same `job_id` always returns the same `Lock` instance; different `job_id`s return distinct locks; 1000-call stress passes; full save/load/update round trip still works.

**Cost:** the `setdefault` call constructs an `asyncio.Lock()` argument every call (immediately GC'd when the key already exists). For a per-`job_id` dict that allocation overhead is negligible.

---

### B2 — `SagaStatus.COMPENSATED` is dead terminal state — **HIGH** — ✅ SHIPPED 2026-05-15 (bundled with B3)

**File:line:** `scripts/checkout/saga.py:135` + every caller of `_compensate()`

**Symptom:** `_compensate()` writes `saga_status = COMPENSATED` (line 135). Every caller of `_compensate()` immediately overwrites it with `FAILED` on the next `update()` call. The terminal state for every compensated Saga is `FAILED`. The `COMPENSATED` enum value defined in `models.py:91` exists but is never observable as a terminal state.

```python
# saga.py:301-306
await _compensate(job_id)                              # writes COMPENSATED
await checkout_store.update(job_id, {                  # overwrites with FAILED
    "saga_status": SagaStatus.FAILED,
    "error": f"BrickOwl order failed: {exc}",
})
```

**Why it's not in the audit:** This is implementation drift introduced after the audit was written. The audit's pseudo-code in §6.4 ends compensation at `state=COMPENSATED`; the actual code adds the overwriting FAILED write.

**Impact:** Frontend or operator dashboard conditioning on `saga_status == "compensated"` will never see it. Distinguishing "compensated cleanly" from "failed with no compensation attempt" is impossible from state alone.

**Fix:** Pick one:
- **(a)** Remove the post-`_compensate` FAILED write — terminal state stays `COMPENSATED`. Frontend treats COMPENSATED as a failure variant ("we cancelled successfully, your money was never charged").
- **(b)** Delete `SagaStatus.COMPENSATED` from the enum. FAILED is the only terminal failure state. Documents the operator-visible reality.

(a) is the better fix because it preserves the distinction in B3 below.

**Effort:** 30 min including frontend coordination.

**Launch-blocking?** No, but resolves with B3 in one commit.

---

### B3 — `_compensate()` reports success regardless of per-order cancel outcomes — **HIGH** — ✅ SHIPPED 2026-05-15 (Phase 1.2)

**File:line:** `scripts/checkout/saga.py` (`_compensate` and three call sites)

**Original symptom:** Each cancel failure was logged but the loop continued; final write was unconditional `COMPENSATED`, which was immediately overwritten with `FAILED` by the caller (B2). Operators had no MANUAL_REVIEW signal even when real BrickOwl orders or Stripe holds were stranded. Today's BrickOwl `cancel_order` is a no-op stub, so the bug was latent — but it would have become catastrophic the moment the real Playwright cancellation (roadmap #5) shipped.

**What shipped (bundled fixes):**

- New `_CompensationOutcome` dataclass tracks per-cancel results (BrickOwl succeeded/failed lists, LEGO uncancellable order, Stripe outcome + skip reason).
- New `_cancel_hold_with_retry(provider, checkout_id, hold_id)` helper mirrors `_capture_with_retry`'s policy: 1s/4s/16s backoff on `PaymentRetryableError`, immediate fail on `PaymentPermanentError`, stable `cancel-{checkout_id}` idempotency key across all attempts. **Closes B21.**
- New `_compose_compensation_reason()` formats the operator-facing MANUAL_REVIEW reason string with every resource enumerated and a runbook checklist.
- `_compensate(job_id, *, original_error: str)` — new required keyword arg, single state write at the end. **Decision rule:**

  | Pre-state | Outcome | Terminal status |
  |---|---|---|
  | State load failed (filesystem corrupt) | n/a | `MANUAL_REVIEW` ("manual reconciliation required") |
  | Already-terminal saga_status (re-entrant call) | n/a | no-op |
  | All BrickOwl cancels OK, no LEGO, Stripe cancel OK (or no hold) | clean | `COMPENSATED` |
  | Any BrickOwl cancel raised | partial | `MANUAL_REVIEW` (lists failed IDs + succeeded IDs) |
  | LEGO order in state | always | `MANUAL_REVIEW` (LEGO has no API cancel) |
  | Stripe cancel retries exhausted / permanent error | partial | `MANUAL_REVIEW` |
  | Missing `checkout_id` while hold present | unsafe | `MANUAL_REVIEW` (refuses to construct divergent idempotency key) |
  | Provider unavailable mid-compensation | partial | `MANUAL_REVIEW` |

- **Three call sites simplified.** Each formerly did `await _compensate(job_id); await checkout_store.update(job_id, {"saga_status": FAILED, ...})`. Now: `await _compensate(job_id, original_error="...")`. **Callers must NOT overwrite** — this is the contract that activates COMPENSATED as a real terminal state.

**Bundled bug fixes:**

- **B2** — `SagaStatus.COMPENSATED` is no longer dead. Callers don't overwrite. Clean rollbacks now surface as `compensated` to frontend and operators.
- **B18** — Missing `checkout_id` no longer silently falls back to `job_id` (which would produce a divergent Stripe idempotency key). Refuses to cancel and escalates to MANUAL_REVIEW.
- **B20** — State load failure (filesystem corrupt, JSON parse error) no longer silently `or {}`'d into a fake-empty state. Captured into `outcome.state_load_error` and escalates to MANUAL_REVIEW.
- **B21** — Stripe cancel now retries on `PaymentRetryableError` with the same 1s/4s/16s backoff as capture. A one-second network blip during compensation no longer permanently strands the customer's hold. Same stable idempotency key (`cancel-{checkout_id}`) makes the retry safe.
- **B22** — Precondition check: `_compensate` is a no-op when state is already terminal. Safe against any future refactor that double-calls.

**Verified:** 35-check / 12-scenario behavioral test suite exercises every branch of the decision matrix:
- COMPENSATED no-op (1)
- COMPENSATED with successful BrickOwl cancels (2)
- MANUAL_REVIEW on BrickOwl partial failure with succeeded + failed IDs in reason (3)
- MANUAL_REVIEW forced by LEGO order presence (4, 11)
- COMPENSATED via Stripe retry recovery (5)
- MANUAL_REVIEW on exhausted Stripe retries (6)
- MANUAL_REVIEW on permanent Stripe error with no retry attempts (7)
- MANUAL_REVIEW on missing `checkout_id` with no cancel attempt (8)
- MANUAL_REVIEW on provider unavailable (9)
- Terminal-state precondition no-op for all 4 terminal statuses (10 × 4)
- `original_error` preserved verbatim in `error` field across special characters (12)

**Deferred (separate edits):**

- **B16** — Parallel BrickOwl cancels via `asyncio.gather`. The outcome data structure permits it but concurrency on a shared BrickOwl account is its own risk.
- **B11** — Destructive per-iteration `brickowl_order_ids` checkpoint inside the stockout-retry loop. Unrelated to compensation.
- **B19** (newly discovered) — LEGO/BrickOwl order placed but state write fails afterward. The order ID lives only in a local variable; `_compensate` reads stale state. Needs to split state-update out of the marketplace-call try block. Separate small fix.

---

### B4 — No Saga-level timeout — **HIGH** — ✅ SHIPPED 2026-05-15 (Phase 1.1)

**File:line:** `scripts/checkout/saga.py` (was line 143; wrapper now at top of public entry point)

**Original symptom:** The Saga had no overall deadline. If `lego_client.order_from_lego` hung (Playwright stuck on a slow page, browser pool deadlock, network drop mid-Playwright), the asyncio task would sit in `_running_sagas` forever, `checkout_state.json` would stay at `STRIPE_HELD` or `ORDERS_PLACED`, and the Stripe authorization would burn its full 7-day window.

**What shipped:**

- New module constant `_SAGA_TIMEOUT_SECONDS = int(os.environ.get("SAGA_TIMEOUT_SECONDS", "900"))` — 15 min default; env-overridable for staging tests.
- `execute_checkout_saga(...)` is now a thin wrapper around `asyncio.wait_for(_execute_checkout_saga_inner(...), timeout=_SAGA_TIMEOUT_SECONDS)`. The previous body was renamed to `_execute_checkout_saga_inner` (private; do not call directly).
- New `_handle_saga_timeout(job_id, checkout_id)` reads the last checkpointed state and routes recovery:
  | Pre-timeout state | Action | Terminal status |
  |---|---|---|
  | Already terminal (race) | no-op | unchanged |
  | No `payment_hold_id` | clean exit | `FAILED` |
  | Hold exists, no orders | best-effort `provider.cancel(hold_id, idempotency_key=cancel-{cid})` | `FAILED` on success, `MANUAL_REVIEW` on failure |
  | Any orders placed | escalate; cannot infer capture state | `MANUAL_REVIEW` with verbose `manual_review_reason` |
- The timeout wrapper is the **only** authoritative ceiling. No nested `wait_for` on intermediate awaits. Stripe SDK has its own `request_timeout`; Playwright has its own action/navigation timeouts. Those are implementation details below the Saga's contract.

**Verified:** 8-scenario smoke test exercising every branch of `_handle_saga_timeout` plus an end-to-end test that stubs the inner saga to hang past a 1-second timeout and confirms `CancelledError` propagates to the inner coroutine while the wrapper writes the correct terminal state.

**Module docstring:** new "TIMEOUT POLICY (B4)" section documents the single-timeout invariant and explicitly forbids adding nested per-call timeouts.

---

### B5 — Drift check happens only at capture time, after all orders placed — **HIGH** — ✅ SHIPPED 2026-05-16 (Phase 1.3)

**File:line:** `scripts/checkout/saga.py` — pre-placement check at top of `while True:` loop (`_execute_checkout_saga_inner`); post-placement check kept as defense-in-depth.

**Original symptom:** The 5%-buffer drift check ran *after* every BrickOwl + LEGO.com order was placed. If a stockout retry pushed the new allocation beyond the buffer, the Saga placed the new (expensive) orders, then escalated to MANUAL_REVIEW at capture time. Orders were real and LAIGO had paid for them.

**What shipped:**

- **Pre-placement drift check at the top of the `while True:` loop.** Fires before any orders are placed in any iteration. Reuses `_compensate(job_id, original_error="...")` from the B3 machinery so the rollback inherits hold-cancel-with-retry, MANUAL_REVIEW escalation on cancel failure, missing-checkout_id safety, etc.
- **Iteration math invariant:** iteration 1 cannot trigger drift because `hold = ceil(quote * 1.05) >= quote`. Verified across quote values 1, 99, 100, 199, 999, 10000, 99999. The check is placed at the top of the loop as defense-in-depth (defends against future changes to the hold formula).
- **Post-placement drift check kept as belt-and-suspenders.** Updated log message and comments to mark it as a defense-in-depth invariant guard. If it ever fires under correct logic, the pre-placement check was bypassed somehow — comment instructs operator to investigate.
- **Terminal status on detection:** state was `STRIPE_HELD` with no orders → `_compensate` routes to `COMPENSATED` (hold released cleanly) or `MANUAL_REVIEW` (hold cancel failed). Customer gets a refund either way; orders are never placed at the inflated total.

**Verified:** 17-check / 4-scenario test suite:
- (1) iteration-1 invariant across 7 quote values — drift check mathematically a no-op on iteration 1
- (2) drift detected pre-placement → `_compensate` → COMPENSATED with correct error message
- (3) drift detected + hold cancel exhausts retries → MANUAL_REVIEW with 4 cancel attempts
- (4) static AST check: drift comparison physically present at top of `while True:` body inside `_execute_checkout_saga_inner`

**Closes audit RPN #9** ("allocation drift mid-Saga undercharges"). Combined with the 5% buffer and the post-placement MANUAL_REVIEW safety net, the drift problem is structurally resolved: within 5% absorbed silently; beyond 5% fail-closed before any orders are placed; impossible-to-reach post-placement case still routes to MANUAL_REVIEW.

---

### B6 — Stripe key length rule disagreement between gate and provider — **MEDIUM** — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle / H4)

**File:line:** `scripts/checkout/payment/key_format.py` (new module — single source of truth); `scripts/checkout/gate.py:109` and `scripts/checkout/payment/stripe_provider.py:101` (both now import from key_format).

**Original symptom:** `gate._stripe_key_mode()` accepted `sk_test_` / `sk_live_` with *any* suffix length. `stripe_provider._key_mode()` required ≥ 8 chars beyond the prefix (the D3 fix). `STRIPE_SECRET_KEY=sk_live_` (bare prefix) produced two different `/checkout/gate` reason strings depending on the value of `RENDER`.

**What shipped:**

- New `scripts/checkout/payment/key_format.py` exporting `key_mode(key) -> str | None` and `_MIN_SUFFIX_CHARS = 8` and `_PREFIXES = {"sk_test_": "test", "sk_live_": "live"}`. Dependency-free.
- `gate.py` replaced its local `_stripe_key_mode` with `from .payment.key_format import key_mode as _stripe_key_mode`. The strict 8-char rule is now what gate enforces.
- `stripe_provider.py` replaced its local `_key_mode` with `from .key_format import key_mode as _key_mode`. The two layers' contract is identical by construction.
- Decision rationale (chose option B from the original entry — shared helper module): keeps `gate.py` from importing a `payment/` implementation detail. The provider is the implementation; the key format is the contract.

**Verified:** behavioral check via `python -c`:
- `key_mode("sk_live_")` → `None` (bare prefix rejected)
- `key_mode("sk_test_")` → `None` (bare prefix rejected)
- `key_mode("sk_test_short")` → `None` (suffix < 8 chars)
- `key_mode("sk_test_abcd1234")` → `"test"`
- `key_mode("sk_live_abcd1234")` → `"live"`
- `key_mode("")` → `None`
- `key_mode("pk_test_abcd1234")` → `None` (publishable key rejected)

**Operator implication:** the `/checkout/gate` body's `reasons[]` array no longer disagrees with the StripeProvider's construction failure reason. A bare-prefix typo surfaces as: gate reports `"No payment provider registered (see boot logs for the construction failure reason)"` AND `StripeProvider.__init__` raised `PaymentProviderUnavailable("STRIPE_SECRET_KEY is missing or malformed...")`. One config defect → one consistent reason chain.

---

### B7 — Currency read at Saga runtime, not at provider construction — **MEDIUM** — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle / H5)

**File:line:** `scripts/checkout/payment/stripe_provider.py` (`_ALLOWED_CURRENCIES`, `_ENV_STRIPE_CURRENCY`, `self.currency` set in `__init__`); `scripts/checkout/payment/base.py` (Protocol now declares `currency: str`); `scripts/checkout/saga.py:~830` (now reads `provider.currency`).

**Original symptom:** the currency was read fresh on every `create_hold` call via `os.environ.get("STRIPE_CURRENCY", "usd")`. Env mutation between hold and capture (after restart-while-saga-in-flight) could target different currencies. No allowlist — `STRIPE_CURRENCY=zzz` would only fail at the first Stripe API call (after the hold attempt was already in flight).

**What shipped:**

- Module-level `_ALLOWED_CURRENCIES: Final = frozenset({"usd", "eur", "gbp", "cad"})` and `_ENV_STRIPE_CURRENCY: Final = "STRIPE_CURRENCY"` in `stripe_provider.py`.
- New construction step (step 5 in the atomic `__init__` order): read `STRIPE_CURRENCY`, lowercase + strip, validate against `_ALLOWED_CURRENCIES`, raise `PaymentProviderUnavailable` on miss with a message that lists the allowed values, lock as `self.currency: Final`.
- `PaymentProvider` Protocol in `payment/base.py` now declares `currency: str` as a required attribute with a docstring explaining the contract (B7/H5: locked at construction, NOT re-read by callers).
- `saga.py` `create_hold` site now passes `currency=provider.currency` with a comment pointing to B7/H5.

**Verified:** AST parse + Protocol declaration. The misconfig case `STRIPE_CURRENCY=zzz` would now surface as a `/checkout/gate` reason `"No payment provider registered (see boot logs)"` plus a startup log line `PaymentProviderUnavailable: STRIPE_CURRENCY='zzz' is not in the allowlist ['cad', 'eur', 'gbp', 'usd']`.

**Operator implication:** adding a new currency (e.g., AUD when LAIGO expands to Australia) now requires a code change + deploy. See B27 below for a doc-only follow-up that flags this as a known constraint.

**Frontend implication:** none. Customers never see currency strings in `/status` responses.

---

### B8 — LEGO.com stockouts are not retryable — **MEDIUM** — ✅ SHIPPED 2026-05-16 (Option B; pre-DB-migration bundle / H9)

**File:line:** `scripts/checkout/saga.py:~1085-1106` (the new `except StockoutError` arm in the LEGO branch of `_execute_checkout_saga_inner`'s `while True:` body, sitting above the pre-existing `except Exception`).

**What shipped (Option B chosen):**

- New `except StockoutError as exc:` branch directly above the generic `except Exception` for the LEGO order call.
- Behavior on this branch:
  1. WARNING log `"LEGO stockout on element(s) {exc.element_id!r}: compensating without retry (LEGO is the primary source; re-routing would not help)."`
  2. Calls `_compensate(job_id, original_error=f"LEGO.com stockout (no retry path): {exc}", extra_brickowl_orders=placed_brickowl_ids)` → clean compensation path; customer refunded; any BrickOwl orders placed earlier this iteration get cancelled.
  3. Returns.
- Documented in the source comment that LEGO is the primary inventory source. Re-routing to "LEGO again" wouldn't help (BrickOwl already explored if cheaper). When LEGO surfaces a secondary source (wishlist queue, backorder API), the fallback wires here.

**Important caveat (B29 below):** the new branch is plumbed but **currently unreachable** because `lego_client.order_from_lego` doesn't yet raise `StockoutError` — it returns the Playwright order ID on success and raises generic `Exception` (caught by the `except Exception` arm) on any failure including DOM-detected stockouts. The Option B fix establishes the contract; wiring `lego_client` to actually raise `StockoutError` is filed as B29 (LOW; doc-only / minor refinement).

**Operator implication:** support-staff runbook for "customer says LEGO order failed" no longer needs to distinguish "LEGO stockout" from "LEGO website broke" — the `error` field will say `"LEGO.com stockout (no retry path)"` once B29 is wired, vs `"LEGO.com order failed: <exc>"` for everything else. Until B29 ships, all LEGO failures still surface as the generic message.

---

### B9 — Stockout retry only invalidates BrickOwl listings cache — **MEDIUM** — ✅ SHIPPED 2026-05-16 (bundled with B10; pre-DB-migration / H8)

**File:line:** `scripts/checkout/saga.py:~1037-1041` (the `asyncio.gather` over all three clients' `invalidate_listing(stockout_eid)`).

**What shipped (bundled with B10):**

Each client module now exposes an `invalidate_listing(element_id) -> Awaitable[None]` that owns its own cache key naming. Saga's stockout-retry path replaced the single `cache_delete(f"brickowl_listings:{stockout_eid}")` with:

```python
await asyncio.gather(
    brickowl_client.invalidate_listing(stockout_eid),
    lego_client.invalidate_listing(stockout_eid),
    bricklink_client.invalidate_listing(stockout_eid),
)
```

Bricklink's `invalidate_listing` is a no-op stub (BrickLink isn't caching listings yet — see its module docstring), kept for contract symmetry.

**Verified:** AST + key-symmetry check — each client's `invalidate_listing` uses the same key string its `get_all_listings` writes (`brickowl_listings:{eid}` for BrickOwl, `lego_raw:{eid}` for LEGO).

**Operator implication:** when a piece becomes stockout-prone (e.g., a discontinued color), retries no longer chew through cached-stale data across marketplaces. Latency per stockout retry stays consistent regardless of which marketplaces' data was cached at quote time.

---

### B10 — Cache key construction is duck-typed across modules — **MEDIUM** — ✅ SHIPPED 2026-05-16 (bundled with B9; pre-DB-migration / H8)

**File:line:** `scripts/checkout/clients/brickowl_client.py` (`invalidate_listing`), `scripts/checkout/clients/lego_client.py` (`invalidate_listing`), `scripts/checkout/clients/bricklink_client.py` (`invalidate_listing` stub).

**What shipped:** the saga no longer constructs cache key strings. Each client module owns its own key naming and exposes `invalidate_listing(eid)`. A one-character typo at the saga site is no longer possible — there are no key strings there.

See B9 (bundled). Same touch surface, same commit.

**Operator implication:** if a future caching change reshapes the keys (e.g., switching to namespaced keys per shipping country), only the client module needs to update. The saga doesn't care.

---

### B11 — Cross-iteration `placed_brickowl_ids` checkpoint is destructive — **MEDIUM**

**File:line:** `scripts/checkout/saga.py:279, 295, 338`

**Symptom:** Each `while True:` iteration rebuilds `placed_brickowl_ids = []` (line 279), then checkpoints it (line 295). At the end of a stockout retry (line 338) the list is reset to `[]`. If a process crash happens between iterations — after old orders were cancelled but before the next iteration's first order checkpoint — the state file shows `brickowl_order_ids=[]`. Combined with B3, that's a recipe for losing track of which marketplace orders were actually placed during which iteration.

**Why it's not in the audit:** Crash-safety is documented as a known gap (§3.4) but the specific destructive-checkpoint pattern between retry iterations is not.

**Impact:** During Postgres-resumption work (§3 roadmap item 2), the resume code reading `brickowl_order_ids=[]` will assume nothing was placed in the current iteration, even if a cancel-then-replace cycle was mid-flight when the crash happened.

**Fix:** Move the state model from a single list to a per-iteration log:
```json
{
  "iterations": [
    {"iteration": 0, "placed": ["bo_111", "bo_222"], "cancelled": ["bo_111", "bo_222"], "outcome": "stockout"},
    {"iteration": 1, "placed": ["bo_333"], "cancelled": [], "outcome": "in_progress"}
  ]
}
```
Each iteration's placed/cancelled lists are immutable once written. Resumption examines the latest in-progress iteration.

**Effort:** 1 day including resumption code coordination. Defer to §3 roadmap item 2.

**Launch-blocking?** No (single-process today), but blocking for roadmap #2.

---

### B12 — Provider exception text leaks into customer-facing `error` field — **LOW (security-adjacent)** — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle / H1+H2)

**File:line:** `scripts/checkout/models.py` (`ERROR_MESSAGES` table + `customer_message` field on `CheckoutStatusResponse`); `scripts/checkout/saga.py` (paired `customer_message` at all 15 error-write sites); `scripts/checkout/router.py:~297` (passes `customer_message` through in `get_checkout_status`).

**What shipped:**

- New `ERROR_MESSAGES: dict[str, str]` in `models.py` with seven categories:
  - `payment_permanent` — "Your payment method was declined. Please use a different card."
  - `payment_transient` — "Our payment system is temporarily unavailable. Please retry shortly."
  - `marketplace_failure` — "We couldn't complete one of your orders. Your card was not charged."
  - `manual_review` — "Your order is being reviewed by our team. We'll email you within 24 hours."
  - `drift_buffer` — "The price of your order changed. Please request a new quote." (RESERVED — no current saga path uses it; reserved for the customer-facing pre-quote-recompute UX once that ships)
  - `gate_closed` — "Checkout is temporarily unavailable. Please try again shortly."
  - `timeout` — "Your order took longer than expected. Our team is reviewing — no action required."
- `CheckoutStatusResponse` gains `customer_message: Optional[str] = None` alongside the now-clearly-operator-facing `error`. Docstrings on both fields document the contract.
- Every saga `checkout_store.update(...)` write that sets `"error": ...` now also sets `"customer_message": ERROR_MESSAGES[<category>]`. 15 sites paired (verified `grep -c '"error":'` == `grep -c '"customer_message":'` == 15).
- The initial INITIATED save sets `"customer_message": None` explicitly so the field is always present in state.
- Category assignment per site:
  - Compensation MANUAL_REVIEW → `manual_review`
  - Compensation clean COMPENSATED → `marketplace_failure` ("Your card was not charged")
  - Timeout w/ orders placed → `manual_review` (orders are real; operator review)
  - Timeout cleanup success → `timeout`
  - Timeout cleanup cancel failed → `manual_review`
  - Timeout no money moved → `timeout`
  - Gate closed at saga start → `gate_closed`
  - Provider unavailable → `gate_closed`
  - Payment permanent → `payment_permanent`
  - Payment transient → `payment_transient`
  - Payment unexpected → `payment_transient` (see B30 below — possibly misleading on genuine code bugs; documented as a refinement opportunity)
  - Stockout-retry compensation failed → `manual_review`
  - Drift post-placement → `manual_review` (orders are real; the `drift_buffer` category is reserved for the future pre-placement customer prompt)
  - Capture failed → `manual_review`

**Verified:** AST parse + `grep` count match (15:15). The audit check noted in CLAUDE.md is `grep -n '"error":' scripts/checkout/saga.py` returning only sites where the next line is `"customer_message":`.

**Frontend contract (NEW — coordinate before changing):**
- `customer_message` is the ONLY string safe to surface from `/status` to customers.
- `error` is operator-only — may contain Stripe IDs, exception class names, internal request IDs.
- `customer_message` is `null` when (a) state is pre-error (INITIATED, STRIPE_HELD, etc.) OR (b) saga completed successfully (PAYMENT_CAPTURED). Render `null` as your normal "processing…" / "complete" UI; do not assume `null` means "no error info available" mid-failure.
- Adding a new category requires coordinated frontend change. Don't change existing strings without coordinating.

**Operator implication:** when triaging via `/status`, read both fields. `customer_message` confirms what the customer is seeing; `error` is the actionable signal.

---

### B13 — Master-flag reason text reads as "missing" when set falsy — **LOW** — ✅ SHIPPED 2026-05-16 (Phase 3.1)

**File:line:** `scripts/checkout/gate.py:137-143`

**Original symptom:** When `CHECKOUT_ENABLED` was set to `false`, `0`, or `no`, the gate reason read `"CHECKOUT_ENABLED is not set to true"`. Operator could not tell whether they forgot to set it or set it deliberately as a kill switch.

**What shipped:**
```python
master_raw = os.environ.get(_ENV_MASTER_FLAG)
master_enabled = is_truthy(master_raw)
if not master_enabled:
    if master_raw is None:
        reasons.append(f"{_ENV_MASTER_FLAG} is not set")
    else:
        reasons.append(f"{_ENV_MASTER_FLAG}={master_raw!r} is not truthy")
```

**Verified edge cases:**
- `CHECKOUT_ENABLED` unset → `"CHECKOUT_ENABLED is not set"`
- `CHECKOUT_ENABLED=""` → `"CHECKOUT_ENABLED='' is not truthy"`
- `CHECKOUT_ENABLED="   "` → `"CHECKOUT_ENABLED='   ' is not truthy"`
- `CHECKOUT_ENABLED=False` (typo of `false`, capital F) → `"CHECKOUT_ENABLED='False' is not truthy"`
- `CHECKOUT_ENABLED=true` → no reason emitted (gate opens)

**Operator implication (NEW):** the `/checkout/gate` body's `reasons[]` array now distinguishes the "you forgot to set it" path from the "you deliberately disabled it" path. Alerting rules that match on the reason string should account for both forms.

---

### B14 — `/confirm` 409 reason doesn't distinguish in-flight vs finished — **LOW** — ✅ SHIPPED 2026-05-16 (Phase 3.3)

**File:line:** `scripts/checkout/router.py:217-230`

**Original symptom:** `"This checkout has already been confirmed"` was returned for both (a) Saga mid-flight and (b) Saga finalized minutes ago. Customer / frontend could not disambiguate "wait" from "restart" without a second `/status` call.

**What shipped:**
```python
existing = await checkout_store.load(job_id)
if existing and existing.get("checkout_id") == body.checkout_id:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "This checkout has already been confirmed",
            "saga_status": existing.get("saga_status"),
            "poll_url": f"/jobs/{job_id}/checkout/{body.checkout_id}/status",
        },
    )
```

**Frontend contract (NEW — do not break without coordinated change):**
- 409 `detail` is always a JSON object (no longer a flat string).
- Always contains keys `error`, `saga_status`, `poll_url`.
- `saga_status` is one of the `SagaStatus` enum values (`initiated`, `stripe_held`, `orders_placed`, `payment_captured`, `compensated`, `failed`, `manual_review`) OR the literal `"pending"` if the row exists but no saga has yet written its first transition.
- `poll_url` is the absolute path; frontend can `fetch(poll_url)` directly.

**Scope NOT covered by B14 — adjacent gap (filed as [B23](#b23--concurrent-confirm-with-different-checkout_id-for-same-job_id-clobbers-in-flight-state--medium) below):** the 409 only fires when `existing.checkout_id == body.checkout_id`. A second `/confirm` from a *new* quote (different `checkout_id`) for the same `job_id` falls through to `checkout_store.save(...)` and silently clobbers the in-flight saga's row.

---

### B15 — `stripe.api_version` and `stripe.api_key` are module-global, not per-instance — **LOW (forward-looking)** — ✅ SHIPPED 2026-05-16 (doc-only; pre-DB-migration / H7)

**File:line:** `scripts/checkout/payment/stripe_provider.py` (in-code comments above the two module-global writes: `stripe.api_key = key` and `stripe.api_version = api_version`).

**What shipped:** the in-code mitigation from the original entry. Two comments now make the constraint visible at the modification site:

- Above `stripe.api_key = key`: a 9-line comment block explaining (a) the field is module-global on `stripe-python`, (b) single-active enforcement in `payment.registry.register()` is what keeps this safe today, (c) instantiating `StripeProvider` outside the lifespan path or registering a second instance silently overwrites the key for any in-flight saga holding the OLD provider reference, (d) the multi-active migration path is `stripe.StripeClient(api_key=key)` per PRE_RELEASE §4 B15.
- Above `stripe.api_version = api_version`: a short comment noting that `api_version` is the SAME constraint.

**Multi-active migration NOT shipped (deferred):** the migration to per-instance `stripe.StripeClient(...)` remains a 1-day effort, justified only when LAIGO actually needs multi-active (e.g., a tooling staging process that holds both test + live providers in one Python process). Today's single-active design (B17 replacement guard) is the operational mitigation.

**Operator implication:** unchanged. Production must NOT set `LAIGO_ALLOW_REGISTRY_REPLACE=1`. The in-code comments are visible to future contributors who might be tempted to instantiate `StripeProvider` outside lifespan (e.g., for an ad-hoc operator tool).

---

### B16 — Sequential BrickOwl cancellation has no batching (two sites) — **LOW** — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle / H10)

**File:line:** `scripts/checkout/saga.py:~109-140` (`_parallel_brickowl_cancels` helper + `_BRICKOWL_CANCEL_CONCURRENCY = 5`); `scripts/checkout/saga.py:~458-470` (site #1 — `_compensate` Phase 1); `scripts/checkout/saga.py:~1004-1028` (site #2 — stockout-retry inline cancel).

**What shipped:**

- New module-level constant `_BRICKOWL_CANCEL_CONCURRENCY: int = 5`.
- New helper `async def _parallel_brickowl_cancels(order_ids: list[str]) -> list[tuple[str, str | None]]`:
  - Uses `asyncio.Semaphore(_BRICKOWL_CANCEL_CONCURRENCY)` + `asyncio.gather`.
  - Returns `[(order_id, error_or_None), ...]` preserving input-order presence.
  - Caller decides policy — no terminal state writes inside the helper.
- **Site #1 (`_compensate` Phase 1):** the `for order_id in reversed(...)` loop is replaced by one `_parallel_brickowl_cancels(list(reversed(...)))` call. Results fold into `outcome.brickowl_succeeded` / `outcome.brickowl_failed` after the gather. Comment notes that LIFO order no longer reflects temporal completion — the outcome dataclass is a fact set, not an ordered log.
- **Site #2 (stockout-retry inline cancel):** behavioral change. Previously, the loop aborted on the FIRST failure and the rest of the orders were never cancelled, leaving them stranded in the customer's BrickOwl account on top of the MANUAL_REVIEW write. Now ALL cancels are attempted; on ANY failure, a single MANUAL_REVIEW write enumerates EVERY failure (`failed_summary = "; ".join(f"{oid}: {err}" for oid, err in failures)`) so the operator's cleanup work is bounded by the actual failure set.

**Verified:** AST + grep checks:
- `brickowl_client.cancel_order(` appears exactly ONCE (inside the helper).
- `_parallel_brickowl_cancels` has 1 `async def` + 2 `await ... (` call sites.
- The old `for order_id in reversed(` and `for oid in reversed(placed_brickowl_ids):` patterns are gone.

**Behavioral change to flag:** see B28 below — individual cancel failures inside the helper are NOT retried. The single-shot policy is appropriate for the current stub `cancel_order` (a no-op log), but **will be insufficient** when the real Playwright-based BrickOwl cancel (roadmap #5) ships, where transient Playwright failures are common. B28 is the open follow-up.

**Operator implication:** compensation latency at production order sizes (~50 sellers) drops from sequential 15-30 min (with real Playwright cancel) to bounded ~30-90 sec (5-way concurrent). Stockout-retry compensation also bounds: rather than failing fast on first cancel error and leaving N-1 orders stranded, the customer's account gets fully cleaned and the operator gets one comprehensive failure report.

---

### B17 — `register()` replaces silently with only a WARNING — **LOW** — ✅ SHIPPED 2026-05-16 (Phase 3.2)

**File:line:** `scripts/checkout/payment/registry.py:49-90`

**Original symptom:** `register()` accepted a second provider in the same process with only a `logger.warning(...)` line. A test that forgot to call `_reset_for_tests()` would silently swap out the production-registered provider mid-process. The same-instance no-op case (`register(p)` called twice with the same `p`) was not distinguished from the swap case.

**What shipped:**
```python
_REPLACE_ALLOWED_ENV = "LAIGO_ALLOW_REGISTRY_REPLACE"

def _is_replace_allowed() -> bool:
    return (os.environ.get(_REPLACE_ALLOWED_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on",
    )

def register(provider: PaymentProvider) -> None:
    global _active
    if _active is not None and _active is not provider:
        if not _is_replace_allowed():
            raise RuntimeError(
                f"Refusing to replace registered provider {_active.name!r} with "
                f"{provider.name!r}. This catches accidental test leaks. If "
                f"intentional, set {_REPLACE_ALLOWED_ENV}=1 (tests should call "
                "_reset_for_tests() first instead)."
            )
        logger.warning(
            "payment.registry.replaced previous=%s new=%s — "
            "%s=truthy permits replacement; ensure the previous provider isn't "
            "still referenced by an in-flight Saga.",
            _active.name, provider.name, _REPLACE_ALLOWED_ENV,
        )
    _active = provider
    logger.info(...)
```

**Identity-based check rationale:** the guard uses `is not provider`, not `name != provider.name`. Two distinct `StripeProvider()` instances would NOT be `is` each other even though they share `name = "stripe"`. This is by design — a test that calls `StripeProvider()` twice expecting "no-op because same name" must instead reuse the same instance or set the env flag.

**Verified behavior:**
- First `register(p1)` → succeeds; `_active = p1`.
- Same-instance re-register (`register(p1)` again) → no-op; `_active = p1`.
- Different instance, env flag unset → raises `RuntimeError`; `_active` unchanged.
- Different instance, `LAIGO_ALLOW_REGISTRY_REPLACE=1` → succeeds with WARNING; `_active = p_new`.
- `_reset_for_tests()` clears `_active = None`; next `register()` is a fresh path.

**Operator implication (NEW):** there is now a public env-gated escape hatch (`LAIGO_ALLOW_REGISTRY_REPLACE=1`). It must NOT be set in production. The `/checkout/gate` body does not surface this state today — operators rely on log scraping for `payment.registry.replaced`.

**Lifespan-replay caveat (latent, see "Hardening list" §8):** `Main.py` lifespan does NOT catch `RuntimeError` from `register()`. If FastAPI lifespan ever runs twice in the same Python process (TestClient `with TestClient(app):` twice; some test frameworks; future hot-reload tooling), the second `register(StripeProvider())` raises and crashes lifespan startup. **Mitigation:** either call `payment_registry._reset_for_tests()` in `Main.py` lifespan shutdown (one line), or set `LAIGO_ALLOW_REGISTRY_REPLACE=1` in the test runner. Production today is unaffected because `uvicorn` (with or without `--reload`) spawns fresh child processes for each lifespan.

---

### B18 — `_compensate` checkout_id fallback to job_id breaks idempotency — **LOW** — ✅ SHIPPED 2026-05-15 (bundled with B3)

**File:line:** `scripts/checkout/saga.py:115`

**Symptom:**
```python
checkout_id = state.get("checkout_id", job_id)
provider = payment_registry.get_active()
await provider.cancel(
    hold_id=hold_id,
    idempotency_key=f"cancel-{checkout_id}",
)
```
If state somehow lacks `checkout_id`, the cancel uses `job_id` as the suffix — different from `f"cancel-{checkout_id}"` Stripe expects on any re-compensate retry. Could break the cancel-idempotency guarantee in a fallback path.

**Why it's not in the audit:** No audit coverage; surfaced during L5 walkthrough.

**Fix:**
```python
checkout_id = state.get("checkout_id")
if not checkout_id:
    logger.error(
        f"[saga] [{job_id}] State missing checkout_id during compensation; "
        "cannot safely cancel payment hold. Operator must cancel in Stripe dashboard."
    )
    return  # bail; do not generate a non-deterministic idempotency key
```

**Effort:** 10 min.

**Launch-blocking?** No (the fallback should never trigger in practice).

---

### Bug summary

| ID | Title | Severity | Launch-blocking? | Effort / Status |
|---|---|---|---|---|
| B1 | `_get_lock()` TOCTOU race | HIGH | No (single-worker) | ✅ Shipped 2026-05-16 |
| B2 | `COMPENSATED` is dead state | HIGH | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B3 | `_compensate()` silently loses cancel failures | HIGH | **Yes** | ✅ Shipped 2026-05-15 |
| B4 | No Saga-level timeout | HIGH | **Yes** | ✅ Shipped 2026-05-15 |
| B5 | Drift check only at capture time | HIGH | **Yes** | ✅ Shipped 2026-05-16 |
| B6 | Stripe key length disagreement | MEDIUM | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; key_format.py) |
| B7 | Currency read at Saga runtime | MEDIUM | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; provider.currency) |
| B8 | LEGO stockouts not retryable | MEDIUM | No (option B 30min OK) | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; Option B doc-only branch) |
| B9 | Stockout retry invalidates only BrickOwl cache | MEDIUM | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; bundled with B10) |
| B10 | Cache key duck-typed across modules | MEDIUM | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; bundled with B9) |
| B11 | Destructive per-iteration checkpoint | MEDIUM | No (blocks roadmap #2) | Open — subsumed by §9 Postgres migration (becomes `sagas.iterations` JSONB) |
| B12 | Exception text leaks to /status | LOW (security-adjacent) | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; ERROR_MESSAGES table + customer_message paired with all 15 error-write sites) |
| B13 | Master-flag reason wording | LOW | No | ✅ Shipped 2026-05-16 (Phase 3.1) |
| B14 | /confirm 409 doesn't say in-flight vs done | LOW | No | ✅ Shipped 2026-05-16 (Phase 3.3) |
| B15 | stripe.api_version is module-global (api_key also global) | LOW | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; in-code comments at both module-global writes) |
| B16 | Sequential BrickOwl cancels (two sites) | LOW | No | ✅ Shipped 2026-05-16 (pre-DB-migration bundle; `_parallel_brickowl_cancels` helper, both sites + behavioral change in stockout-retry site) |
| B17 | `register()` replaces silently | LOW | No | ✅ Shipped 2026-05-16 (Phase 3.2) |
| B18 | checkout_id→job_id fallback | LOW | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B19 | Order placed but state write fails post-call | MEDIUM | No | ✅ Shipped 2026-05-16 |
| B20 | `_compensate` silently swallows state-load failure | MEDIUM | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B21 | Stripe cancel had no retry on transient errors | MEDIUM | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B22 | `_compensate` had no terminal-state precondition | LOW | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B23 | Concurrent `/confirm` with different `checkout_id` for same `job_id` clobbers in-flight state | MEDIUM | No (single-customer rare) | Open — solved more elegantly by §9 Postgres `sagas_one_active_per_job_idx` partial unique index + `pg_advisory_xact_lock` |
| B24 | Cache sweeper task is GC-vulnerable (no strong reference) | LOW | No | ✅ Shipped 2026-05-16 (pre-DB-migration final sweep; `_sweeper_task` module global + idempotent start) |
| B25 | `_handle_saga_timeout` leaks non-terminal state if load() raises | MEDIUM | No (low likelihood, high severity) | ✅ Shipped 2026-05-16 (pre-DB-migration final sweep; defensive MANUAL_REVIEW write + inner try/except) |
| B26 | `checkout_store.update()` crashes on corrupted JSON; no recovery | LOW | No (subsumed by Postgres migration) | Open — subsumed by §9 (JSON files disappear) |
| B27 | `_ALLOWED_CURRENCIES` is a hardcoded frozenset; adding a currency requires code change + deploy | LOW | No | Open — see B27 entry below |
| B28 | `_parallel_brickowl_cancels` does not retry individual transient cancel failures | MEDIUM | No (LATENT — fires when real BrickOwl Playwright cancel ships per roadmap #5) | Open — see B28 entry below |
| B29 | LEGO `StockoutError` branch is plumbed but `order_from_lego` doesn't raise it today | LOW | No | Open — see B29 entry below |
| B30 | "Hold failed unexpectedly" error categorized as transient — may misguide customer on permanent bugs | LOW | No | Open — see B30 entry below |
| B31 | CHECKOUT_AUDIT.md "now lives at" mapping table will drift as saga.py grows | DOC | No | Open — see B31 entry below |
| B32 | Router writes `saga_status="pending"` but no `SagaStatus.PENDING` exists | MEDIUM | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass; router writes `SagaStatus.INITIATED.value`) |
| B33 | `order_from_lego` wraps `StockoutError` as `RuntimeError` — defeats B8/H9 plumbing | MEDIUM | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass; explicit `except StockoutError: raise` before generic wrap) |
| B34 | Optimizer Pass 2 creates ghost `{eid: 0}` entries via `defaultdict` reads | LOW (LATENT) | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass; `.get()` chain mirrors Pass 1) |
| B35 | `checkout_store.save/update` write files non-atomically | LOW | No | Open — subsumed by §9 Postgres migration (JSON files disappear) |
| B36 | `_parallel_brickowl_cancels` semaphore is per-call, not per-process | LOW (LATENT) | No | Open — subsumed by §9 future concurrent-saga design |
| B37 | `debug_optimize` doesn't match `/quote` flow (missing BrickLink + free-shipping) | LOW | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass) |
| B38 | `debug_optimize` `lego_available` initialized but never populated | LOW | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass; dead variable removed) |
| B39 | Main.py L1 boot block used lenient `sk_live_` startswith (vs canonical `key_mode`) | LOW | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass) |
| B40 | Router initial save missing `customer_message: None` key (B12 contract violation) | DOC | No | ✅ Shipped 2026-05-16 (pre-DB-migration audit pass) |

**Launch-blocking remaining: 0** — B3, B4, B5 all shipped. Phase 1 complete.
**Pre-DB-migration bundle (2026-05-16): SHIPPED** — B6, B7, B8, B9, B10, B12, B15, B16. Newly-surfaced: B27, B28, B29, B30, B31.
**Pre-DB-migration final sweep (2026-05-16): SHIPPED** — B24, B25.
**Pre-DB-migration audit pass (2026-05-16): SHIPPED** — B32, B33, B34, B37, B38, B39, B40. Newly-surfaced from audit: B35, B36 (both DB-coupled, deferred).
**Phase-3-shipped:** B13, B14, B17.
**Open (DB-coupled, deferred to §9):** B11, B23, B26, B35, B36.
**Open (independent of DB, gated on other work):** B27, B28, B29, B30, B31.

### B19 — Order placed but state write fails post-call — **MEDIUM** — ✅ SHIPPED 2026-05-16 (Phase 2.2)

**File:line:** `scripts/checkout/saga.py` — BrickOwl loop and LEGO branch in `_execute_checkout_saga_inner`, plus `_compensate` signature

**Original symptom:** Marketplace call and state checkpoint lived in the same `try` block. If `client.order(...)` succeeded but the subsequent `checkout_store.update(...)` raised (filesystem flap, JSON encode error, asyncio.Lock corruption), the order ID lived only in a local variable. The outer `except Exception` called `_compensate(job_id, original_error=...)`, which read state, saw no record of the just-placed order, and wrote COMPENSATED. **Real marketplace order shipped; LAIGO ate the cost; operators got no MANUAL_REVIEW signal.**

**What shipped:**

- **`_compensate` signature extended** with two safety kwargs:
  ```python
  async def _compensate(
      job_id: str,
      *,
      original_error: str,
      extra_brickowl_orders: list[str] | None = None,
      extra_lego_order: str | None = None,
  ) -> None
  ```
- **Merge logic:** state's `brickowl_order_ids` is unioned with `extra_brickowl_orders` (dedup, preserving state's order); `lego_order_id` falls back to `extra_lego_order` if state has none. Extras-only orders (not in state) emit a WARNING log so operators have a signal that a state-write was lost.
- **BrickOwl loop split** — `create_order` and `checkout_store.update` are now in SEPARATE try blocks (Step A and Step B). Marketplace failure → compensate; state-write failure → compensate WITH `extra_brickowl_orders=placed_brickowl_ids`.
- **LEGO branch split** — same Step A / Step B pattern. State-write failure passes both `extra_brickowl_orders` (for any prior BrickOwl orders this iteration) and `extra_lego_order=lego_order_id`. The LEGO order, being uncancellable via API, forces MANUAL_REVIEW.
- **All other `_compensate` call sites in order-placement code defensively pass `extra_brickowl_orders=placed_brickowl_ids`** so any earlier-iteration B19 race is also caught. The B5 drift-check call site does not pass extras (no orders placed at that point).

**Verified:** 29-check / 9-scenario test suite:
- (1) extras-only BrickOwl orders cancelled in LIFO order; WARNING log fires with the missing IDs
- (2) state ∪ extras with overlap → 3 distinct cancels, dedup is correct, bo_b cancelled exactly once
- (3) `extra_lego_order` with no state LEGO → MANUAL_REVIEW, reason names the order
- (4) state has LEGO + matching extra → MANUAL_REVIEW with no double-WARN
- (5) `extras=None` preserves previous B3 behavior verbatim
- (6) cancel of extras-only BrickOwl fails → MANUAL_REVIEW with the order in the reason
- (7) B3 terminal-state precondition still no-ops across all four terminals even with extras passed
- (8) B5 drift path still works with no extras
- (9) AST verification: 2 separate Try blocks each in BrickOwl loop body and LEGO if-body; 6 `_compensate` call sites total with the correct kwargs distribution

**Net effect:** the silent-loss failure mode is closed. A state-write failure after a successful marketplace order now produces `MANUAL_REVIEW` (LEGO) or proper cancellation attempt (BrickOwl) instead of a fake COMPENSATED.

### B20 — `_compensate` silently swallows state-load failure — **MEDIUM** — ✅ SHIPPED 2026-05-15 (bundled with B3)

State-load failure (filesystem flap, JSON corruption) previously fell through to `state = {}`. Compensation then pretended no orders/hold existed and wrote `COMPENSATED`. Reality: real orders + real hold still active; nothing was cancelled. Fixed: `_CompensationOutcome.state_load_error` captures the exception text; `needs_manual_review()` returns True; `_compose_compensation_reason` emits a "manual reconciliation required" entry.

### B21 — Stripe cancel had no retry on PaymentRetryableError — **MEDIUM** — ✅ SHIPPED 2026-05-15 (bundled with B3)

Capture retried on transient errors; cancel did not. A one-second network blip during compensation could permanently strand the customer's hold. Fixed: new `_cancel_hold_with_retry` helper uses the same `_CAPTURE_BACKOFFS_SECONDS` schedule (1s/4s/16s) and the same stable idempotency key (`cancel-{checkout_id}`). Stripe's 24h idempotency cache makes the retries safe — a transient error followed by a retry never double-cancels.

### B22 — `_compensate` had no terminal-state precondition — **LOW** — ✅ SHIPPED 2026-05-15 (bundled with B3)

If `_compensate` was somehow called when state was already terminal (defensive: shouldn't happen, but a future refactor could trigger it), it would still try to cancel and overwrite the terminal state. Fixed: precondition check skips when state is already `PAYMENT_CAPTURED` / `FAILED` / `COMPENSATED` / `MANUAL_REVIEW`. Logs a warning so the unexpected re-entry is visible.

---

### B24 — Cache sweeper task is GC-vulnerable (same class as the Saga task GC bug) — **LOW** — ✅ SHIPPED 2026-05-16 (pre-DB-migration final sweep)

**File:line:** `scripts/checkout/cache.py` (`_sweeper_task` module global + idempotent `start_cache_sweeper`).

**What shipped:**

- New module-level `_sweeper_task: Optional[asyncio.Task] = None` strong reference.
- `start_cache_sweeper()` now assigns to it and skips re-start if a non-done task already exists. Idempotent — calling twice (or after a previous task finished) re-schedules safely.

Mirror of the `_running_sagas` pattern from C1.

**Verified:** AST + presence check (`_sweeper_task` and `global _sweeper_task` both present). The cache sweep task is no longer GC-eligible during the first scheduling slot.

**Operator implication:** none in production today (the latent leak was unbounded cold-key growth over weeks of uptime). The fix removes a Render-restart cliff that would have surfaced as "memory growing slowly for no apparent reason" months from now.

---

### B25 — `_handle_saga_timeout` can leave state non-terminal if state-load fails — **MEDIUM** — ✅ SHIPPED 2026-05-16 (pre-DB-migration final sweep)

**File:line:** `scripts/checkout/saga.py:~590-625` (the state-load `except Exception` arm at the top of `_handle_saga_timeout`).

**What shipped:**

When `checkout_store.load(job_id)` raises during timeout cleanup, the handler now attempts a best-effort MANUAL_REVIEW write:

```python
try:
    state = await checkout_store.load(job_id) or {}
except Exception as exc:
    logger.critical(...)
    try:
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW,
            "manual_review_reason": (
                f"Saga timed out after {_SAGA_TIMEOUT_SECONDS}s AND state "
                f"load failed ({exc}). Operator must: ..."
            ),
            "error": f"Timeout + state load failure: {exc}",
            "customer_message": ERROR_MESSAGES["manual_review"],
        })
    except Exception as write_exc:
        logger.critical(...)  # inner failure; nothing more we can do
    return
```

The inner `update()` is wrapped in its own try/except so a second failure (state file unwritable, DB unreachable) emits a CRITICAL log but does not crash the timeout coroutine.

**Verified:** AST parse + presence check (`"Timeout + state load failure"` and `"best-effort MANUAL_REVIEW"` both in saga.py). The handler's contract ("MUST always write a terminal state") is now respected on the load-failure branch.

**DB-migration transferability:** the fix structure is identical for filesystem state and Postgres state. `except Exception` catches both `JSONDecodeError` and `asyncpg.PostgresError` — no rework needed in §9.

**Operator implication:** the "customer money held with no /status signal" failure mode is closed. A corrupted JSON file (or future DB query failure) during timeout cleanup now produces a clear MANUAL_REVIEW with verbose runbook, instead of a stuck `stripe_held` state polling forever.

---

### B26 — `checkout_store.update()` crashes on corrupted JSON with no recovery path — **LOW**

**File:line:** `scripts/checkout/checkout_store.py:58-66`

**Symptom:** `update()` does `json.loads(path.read_text(...))` inside the lock. If the file exists but contains malformed JSON (interrupted prior write, disk corruption, manual operator edit gone wrong), the function raises `JSONDecodeError`. The exception propagates up through:
- Saga checkpoint writes → saga aborts mid-flight via generic `except Exception:` (some sites) or crashes the task (others).
- `/status` polling → 500 to the customer (FastAPI's `unhandled_exception_handler` catches but the response leaks the exception text — B12 again).

**Impact:** depends on caller. Worst case is the saga's first `checkout_store.update(...)` after a corrupted-state event abandons the saga in an inconsistent in-memory state — no terminal write, hold not released, marketplace orders possibly placed.

**Fix:** `update()` should treat JSON parse failure as "state is corrupt, recover by overwriting with `partial`" — but that loses prior fields. Better: rename `path` to `path + .corrupt-{timestamp}`, log CRITICAL, then create a fresh file from `partial`. The original is preserved on disk for operator inspection; saga progresses with a fresh checkpoint.

**Or simpler:** add a `repair_or_raise` helper that catches `JSONDecodeError`, archives the corrupt file, returns `{}` (fresh state). All callers continue to work.

**Verification:** write a corrupted JSON file (`echo '{broken' > checkout_state.json`); call `update(...)`; verify the corrupt file is archived to `.corrupt-{ts}` and the new state is written fresh.

**Launch-blocking?** No (subsumed by Postgres migration which removes JSON files entirely).

---

### B23 — Concurrent `/confirm` with different `checkout_id` for same `job_id` clobbers in-flight state — **MEDIUM**

**File:line:** `scripts/checkout/router.py:216-265` (`confirm_checkout` between the B14 same-id check and the `checkout_store.save(...)` call)

**Symptom:** B14's 409 guard only fires when `existing.checkout_id == body.checkout_id` (line 217). If a customer opens a second browser tab, makes a fresh `/quote` (which mints a new `checkout_id`), and submits `/confirm` for the new id while the FIRST checkout's Saga is still running, the router:

1. Loads existing state — sees `checkout_id=co_AAA` (different from `body.checkout_id=co_BBB`).
2. Skips the 409 because the IDs don't match.
3. Falls through to `checkout_store.save(job_id, {..., checkout_id: co_BBB, saga_status: "pending", ...})`.
4. Launches a second `asyncio.Task` for the same `job_id`.

**Resulting race:**
- Both Sagas now serialize their state writes on `checkout_store._locks[job_id]` (B1 made this atomic). They take turns clobbering each other's `saga_status`, `payment_hold_id`, and `brickowl_order_ids` fields.
- `/status` for `co_AAA` returns 404 once `co_BBB` has overwritten the row (the GET handler matches on `state.checkout_id == checkout_id` at router.py:283).
- Two Stripe holds may exist on the customer's card (one for each Saga); only one is observable from state.
- If both Sagas place marketplace orders, the customer receives two complete kits.

**Why not in audit:** §1.3 calls out this class of bug ("there is no per-job confirmation lock") but the recommended fix never landed. B14 closed the *same-id* race; this is the *different-id* race.

**Fix options (operator must pick before implementation):**

| Option | Behavior on second `/confirm` | Trade-off |
|---|---|---|
| **A. Reject** | Return 409 with `existing.checkout_id` exposed and `code: "JOB_HAS_ACTIVE_CHECKOUT"`; refuse to start the second Saga. | Simplest and safest. Customer has to wait for first Saga to finalize (or operator manual-cancels). May leave a "stuck" job unrecoverable if the first Saga crashed without writing terminal state. |
| **B. Reject unless terminal** | Same as A, but check `existing.saga_status` — if terminal (`payment_captured`, `failed`, `compensated`, `manual_review`), let the new Saga proceed. | Recovers stuck states automatically. Edge case: between the terminal write and the new save, the old Saga's `asyncio.Task` may still be holding resources. |
| **C. Cancel-and-replace** | Signal the old Saga to abort (requires the task to honor an interrupt flag — not built today) before starting the new one. | Most user-friendly; lots of new machinery (interrupt protocol, hold-release ordering). |

Option **B** is the v1 recommendation. Concrete shape:

```python
# In confirm_checkout, AFTER B14's same-id 409, BEFORE the save:
existing = await checkout_store.load(job_id)
if existing and existing.get("checkout_id") != body.checkout_id:
    last_status = existing.get("saga_status")
    if last_status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "This job already has an active checkout in flight",
                "code": "JOB_HAS_ACTIVE_CHECKOUT",
                "active_checkout_id": existing.get("checkout_id"),
                "active_saga_status": last_status,
                "poll_url": f"/jobs/{job_id}/checkout/{existing['checkout_id']}/status",
            },
        )
```

`_TERMINAL_STATUSES` already exists in `saga.py` as a frozenset — either import it or duplicate the constant in the router (drift risk; prefer import via shared `models.py` if/when).

**Frontend contract for the 409 body (must add when shipped):**
- `code = "JOB_HAS_ACTIVE_CHECKOUT"`
- `active_checkout_id`: lets the frontend redirect the customer to the in-flight checkout's status page rather than show a dead end.
- `active_saga_status`: same enum values as `/status` returns; frontend can render the right message.

**Effort:** 30 min.

**Launch-blocking?** No (single-customer scenario is rare in practice), but ship before any marketing that surfaces multiple checkout entry points (e.g., "edit shipping address" UI that re-quotes).

---

### B27 — `_ALLOWED_CURRENCIES` is a hardcoded frozenset; adding a currency requires code change + deploy — **LOW**

**Surfaced by:** B7/H5 shipping on 2026-05-16. The fix moved currency validation to provider construction with an in-code allowlist.

**File:line:** `scripts/checkout/payment/stripe_provider.py` (`_ALLOWED_CURRENCIES: Final = frozenset({"usd", "eur", "gbp", "cad"})`).

**Symptom:** when LAIGO expands to a market that needs a new currency (e.g., AUD for Australia), the new currency is rejected at boot with `PaymentProviderUnavailable: STRIPE_CURRENCY='aud' is not in the allowlist [...]`. The fix is a code change + redeploy — not an env-var change.

**Why this is open:** the trade is intentional. An env-driven allowlist (e.g., `STRIPE_ALLOWED_CURRENCIES=usd,eur,gbp,cad,aud`) would let an operator add currencies without a deploy, but also lets a typo silently widen the surface area (`STRIPE_ALLOWED_CURRENCIES=usd,ueo,gbp` would accept "ueo" as valid). The in-code allowlist requires a PR + code review for every new currency, which is the right friction at LAIGO's scale.

**Impact:** medium-friction at expansion time. LAIGO ships in USD today; adding EUR/GBP/CAD requires (a) adding to the allowlist, (b) deploying, (c) updating `.env` on Render. Three steps; ~10 min total.

**Fix when needed:** edit `_ALLOWED_CURRENCIES`, add the new code in lowercase, deploy. The validation error message at boot already lists the allowed values, so the gap is loud (not silent).

**Effort:** 10 min when the time comes; no work needed pre-launch.

**Launch-blocking?** No.

---

### B28 — `_parallel_brickowl_cancels` does not retry individual transient cancel failures — **MEDIUM** (LATENT until roadmap #5)

**Surfaced by:** B16/H10 shipping on 2026-05-16. The new helper wraps `brickowl_client.cancel_order(oid)` in `try/except Exception` and returns `(oid, error_or_None)` — a single-shot policy, no retries on transient errors.

**File:line:** `scripts/checkout/saga.py:~109-140` (`_parallel_brickowl_cancels` helper).

**Symptom (latent today):** `brickowl_client.cancel_order` is currently a logged no-op stub. The single-shot policy is fine — there's nothing to fail. When the real Playwright-based BrickOwl cancel (roadmap #5) ships, individual Playwright sessions are subject to transient failures (network flap, BrickOwl rate-limit response, browser pool exhaustion). A single transient failure during compensation produces a single `(oid, str(exc))` result, which the caller (both sites — `_compensate` and stockout-retry) treats as a permanent failure → MANUAL_REVIEW.

**Why this is open:** `_cancel_hold_with_retry` exists for Stripe cancel and uses the same `_CAPTURE_BACKOFFS_SECONDS = (1, 4, 16)` schedule. The BrickOwl cancel helper does NOT mirror this. Adding per-order retry inside the helper is the right structural fix when the real cancel ships:

```python
async def _cancel_one(oid: str) -> tuple[str, str | None]:
    async with sem:
        for attempt in range(len(_CAPTURE_BACKOFFS_SECONDS) + 1):
            try:
                await brickowl_client.cancel_order(oid)
                return (oid, None)
            except <PlaywrightTransientError> as exc:
                if attempt == len(_CAPTURE_BACKOFFS_SECONDS):
                    return (oid, str(exc))
                await asyncio.sleep(_CAPTURE_BACKOFFS_SECONDS[attempt])
            except Exception as exc:
                return (oid, str(exc))
```

The blocker is: `<PlaywrightTransientError>` isn't a class today — `brickowl_client.cancel_order` doesn't classify failures. The fix requires both (a) the real cancel implementation, AND (b) a transient/permanent classification (the same pattern as `PaymentRetryableError` vs `PaymentPermanentError` from `payment/base.py`).

**Impact (latent):** at production order sizes (~50 sellers), a single transient blip during compensation forces MANUAL_REVIEW for the whole saga. Operator picks up a customer report; ~10-30 min cleanup per stuck cancel.

**Fix when needed:** ships alongside roadmap #5 (real BrickOwl Playwright cancel). Bundle as one commit: classify failure modes in `brickowl_client`, then add per-order retry inside `_parallel_brickowl_cancels`. Mirror the `_cancel_hold_with_retry` shape exactly so the policy is uniform across BrickOwl + Stripe cancellation.

**Effort:** 2-3 hours when the real cancel ships. Not before.

**Launch-blocking?** No (latent; current stub doesn't fail). MUST ship before roadmap #5 goes to production.

---

### B29 — LEGO `StockoutError` branch is plumbed but `order_from_lego` doesn't raise it today — **LOW**

**Surfaced by:** B8/H9 shipping on 2026-05-16 (Option B). The new `except StockoutError as exc:` branch in the LEGO order step is present but currently unreachable.

**File:line:** `scripts/checkout/saga.py:~1085-1106` (the new branch); `scripts/checkout/clients/lego_client.py` (`order_from_lego` — does NOT raise `StockoutError`).

**Symptom:** `lego_client.order_from_lego` catches all Playwright/HTTP failures and re-raises them as generic `Exception`. The B8 branch matches `StockoutError`, which never fires today. Every LEGO failure — including a DOM-detected stockout — surfaces as `"LEGO.com order failed: <exc>"` in the `error` field rather than the dedicated `"LEGO.com stockout (no retry path): <exc>"`.

**Why this is open:** Option A from the original B8 entry (make `order_from_lego` raise `StockoutError` when its Playwright flow detects the out-of-stock UI) is a 1-day effort because it requires reliable DOM-state detection. Shipped as deferred follow-up.

**Impact:** operator triage is slightly less precise. Support-staff explanation to a customer is the same either way (LEGO order failed; customer refunded). The dedicated branch was shipped to establish the contract — wiring is a clean follow-up.

**Fix:** in `lego_client.order_from_lego`, detect the stockout DOM signal (an out-of-stock badge in the cart's product row) and raise `StockoutError(element_id)`. Same exception class as BrickOwl uses — already exported from `models.py`. No saga changes needed.

**Effort:** 1 day including DOM-detection robustness testing across the LEGO.com UI variants.

**Launch-blocking?** No. The Option B contract is shipped; B29 is purely a wiring follow-up that improves the operator runbook precision.

---

### B30 — "Hold failed unexpectedly" categorized as `payment_transient` — may misguide on permanent bugs — **LOW**

**Surfaced by:** B12/H1+H2 shipping on 2026-05-16. The third hold-failure site (generic `except Exception` after `PaymentPermanentError` and `PaymentRetryableError`) was mapped to `ERROR_MESSAGES["payment_transient"]`.

**File:line:** `scripts/checkout/saga.py:~815-820` (the `except Exception as exc:` arm in the hold step).

**Symptom:** when a hold attempt raises an unexpected exception (a bug in our code, a bug in the Stripe SDK, an OS-level failure), the customer sees `"Our payment system is temporarily unavailable. Please retry shortly."` Customer retries with the SAME card; same bug fires; same message. The customer's retry budget is wasted on a non-transient condition.

**Why this is open:** the alternative is to map "unexpected" to `payment_permanent` ("Your payment method was declined. Please use a different card.") — but that's also wrong, because the issue may not be card-related at all. There is no fourth category like `"system_error"` ("Something went wrong on our end; please contact support.") today.

**Impact:** low. The vast majority of hold failures will surface as `PaymentPermanentError` or `PaymentRetryableError` (Stripe's own exception classifications). The unexpected-arm is a defensive catch-all that should rarely fire.

**Fix (when justified):** add a new `ERROR_MESSAGES["system_error"]` category and update the unexpected-arm to use it. Frontend renders a "contact support" CTA rather than a "retry" prompt.

**Effort:** 30 min including frontend coordination.

**Launch-blocking?** No. Acceptable launch-day behavior; refine after a few real fail-data points show the unexpected-arm actually fires in production.

---

### B31 — CHECKOUT_AUDIT.md "now lives at" mapping table will drift as saga.py grows — **DOC**

**Surfaced by:** H12 shipping on 2026-05-16. The 15-row mapping table at the top of `docs/CHECKOUT_AUDIT.md` (under the historical disclaimer) translates audit-doc line refs to current saga.py positions.

**File:line:** `docs/CHECKOUT_AUDIT.md` (the "Quick map of the most-cited historical references" table inside the boxed disclaimer at the top).

**Symptom:** every saga.py edit that crosses one of the listed line ranges silently invalidates a row in the mapping table. Within ~3 future commits, the table will be lying about where things are.

**Why this is open:** the mapping table is a useful artifact today (an operator reading the frozen audit doc gets a fresh pointer), but maintaining it on every commit is friction. Two viable strategies:

- **Strategy A — accept drift, refresh occasionally.** Set a quarterly calendar trigger to re-run a sed-update on the mapping table. Operators understand that the table is a "best effort at last refresh" pointer.
- **Strategy B — replace line refs with stable anchors.** Tag the saga.py code with comments like `# AUDIT_ANCHOR: 0008_lego_order_step` and have the mapping table reference anchors instead of line numbers. Grep-able + drift-immune.

Strategy A is acceptable for v1. Strategy B is the right structural fix if the audit doc becomes a regularly-consulted artifact post-launch.

**Effort:** Strategy A: 30 min refresh per quarter. Strategy B: 1-2 hours to introduce anchors + update doc + update grep guide in CLAUDE.md.

**Launch-blocking?** No.

---

### B32 — Router writes `saga_status="pending"` but no `SagaStatus.PENDING` exists — **MEDIUM** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit (after the pre-DB-migration final sweep).

**File:line:** `scripts/checkout/router.py:~240` (initial `checkout_store.save()` call in `confirm_checkout`).

**Original symptom:** the router's initial state save wrote `"saga_status": "pending"` as a literal string. The `SagaStatus` enum in `models.py` has no `PENDING` member — its values are `initiated`, `stripe_held`, `orders_placed`, `fallback_ordered`, `payment_captured`, `compensated`, `failed`, `manual_review`. A customer polling `/status` in the brief window between the router's `save()` and the saga's first `INITIATED` checkpoint (which happens after gate pre-flight + provider acquisition + the initial INITIATED `update`) would hit `CheckoutStatusResponse`'s Pydantic validation:

```python
class CheckoutStatusResponse(BaseModel):
    saga_status: SagaStatus   # required to be a valid enum value
```

Pydantic would try `SagaStatus("pending")` → `ValueError` → 500 internal server error. The PRE_RELEASE §4 B14 documentation explicitly said `"pending"` was valid; the contract didn't match the code.

**What shipped:**

- Router's initial save now writes `"saga_status": SagaStatus.INITIATED.value` (the string `"initiated"`).
- `SagaStatus` added to router.py's `from .models import (...)` block.
- No new enum value added — the brief window now correctly serializes as INITIATED (which is what the saga overwrites it to anyway, milliseconds later).
- Frontend contract note removed from B14 entry (well, not literally removed — B14's mention of `"pending"` is stale but the saga shape now never produces that value, so it's a frozen historical contract reference).

**Verified:** AST + grep — `"saga_status": "pending"` no longer present in router.py; `SagaStatus.INITIATED.value` is the new literal.

**Frontend implication:** the `"pending"` value listed as valid in B14's saga_status enumeration is **never written** by current code. Frontend can drop any special handling. The saga_status field on `/status` is always a real `SagaStatus` member.

---

### B33 — `order_from_lego` wraps `StockoutError` as `RuntimeError` — **MEDIUM** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit. Discovered while auditing B8/H9 plumbing for completeness.

**File:line:** `scripts/checkout/clients/lego_client.py:~305-322` (the try/except wrapping `_run_checkout` in `order_from_lego`).

**Original symptom:** the wrapping `except Exception as exc: raise RuntimeError(...) from exc` caught *every* exception including `StockoutError`. Even if a future `_run_checkout` raised `StockoutError` (per B29's Option A wiring), the saga's B8/H9 `except StockoutError` branch would never match — saga would see `RuntimeError` and fall through to the generic "LEGO.com order failed" compensation path. The B8 plumbing was unreachable for TWO independent reasons: (a) B29 (no DOM detection yet) AND (b) B33 (the exception wrapping).

**What shipped:**

- Explicit `except StockoutError:` branch added above the generic `except Exception` in `order_from_lego`. Takes a debug screenshot (`ERROR_lego_stockout`) and bare-`raise`s — preserving the exception class so the saga's match works.
- `StockoutError` imported from `..models` in lego_client.py.

**Verified:** AST + grep — `except StockoutError:` appears before `except Exception as exc` in `order_from_lego`. StockoutError import present.

**Operator implication:** the B8/H9 contract is now correctly wired for the future B29 work. Once B29 ships (LEGO DOM stockout detection raising `StockoutError`), the saga's dedicated branch will fire automatically — no further coordination required.

---

### B34 — Optimizer Pass 2 creates ghost `defaultdict` entries — **LOW (LATENT)** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit while reviewing optimizer for correctness.

**File:line:** `scripts/checkout/optimizer.py:~142` (Pass 2 consolidation feasibility check).

**Original symptom:** Pass 2 iterates over `b_sid` (seller B) and inspects every other seller `a_sid` (seller A) as a potential merge target. For each piece B has, the code checked A's remaining stock via `a_already = allocation[a_sid][eid]`. Because `allocation` is `defaultdict(lambda: defaultdict(int))`, this read with a missing `eid` key **created** `allocation[a_sid][eid] = 0` as a ghost entry. If the merge then DIDN'T happen (because `extra_cost >= b_shipping` or A wasn't the best target), the ghost stayed.

Downstream, the build-result loop iterates `allocation[sid].items()` and includes the ghost `{eid: 0}` in `AllocationEntry.items`. Two consequences:

1. The customer-facing `/quote` response shows `{eid: 0}` entries in the per-seller breakdown — visually ugly but not catastrophic.
2. `brickowl_client.create_order(items=entry.items)` (today a `NotImplementedError` stub; per roadmap #5) would be called with 0-quantity items when it ships — likely a 400 from BrickOwl or a silent no-op.

LEGO aggregation tolerates this (`lego_items[eid] += 0` is harmless), so the LEGO ordering path is unaffected.

Pass 1 explicitly avoids this same defect via `.get()` (see comment at optimizer.py:95-96). Pass 2 forgot the pattern.

**What shipped:**

- Replaced `a_already = allocation[a_sid][eid]` with `a_already = allocation.get(a_sid, {}).get(eid, 0)` — the same defaultdict-safe `.get()` chain Pass 1 uses.
- Added a B34 comment explaining the pattern.
- Did NOT touch the post-merge writes (`allocation[best_a_sid][eid] += qty` at line 161): those are legitimate writes that intentionally create the entry.

**Verified:** AST + grep — the `.get(a_sid, {}).get(eid, 0)` pattern is in place; the bare `allocation[a_sid][eid]` read is gone.

**Operator implication:** quote responses no longer show 0-qty ghost entries. The LATENT brickowl `create_order` issue is closed before it surfaces.

---

### B35 — `checkout_store.save()` and `update()` write files non-atomically — **LOW** — Open (subsumed by §9 Postgres migration)

**Surfaced by:** 2026-05-16 codebase audit.

**File:line:** `scripts/checkout/checkout_store.py:55, 65`.

**Symptom:** `path.write_text(json.dumps(...))` opens, truncates, writes. If the process is killed mid-write (Render kill -9, disk full mid-flush, OS crash), the file is left in a half-written corrupt state. The next `load()` raises `JSONDecodeError`.

**Why it's open:** the B25 fix (defensive write on timeout-handler state-load failure) handles the load-failure case from the saga's perspective. But the on-disk corruption persists across restarts and would re-fire on every poll. Fix would be the classic temp-and-rename pattern:

```python
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
tmp.replace(path)  # atomic on POSIX; atomic on Windows for same-drive
```

**Status:** §9 Postgres migration eliminates JSON files entirely; asyncpg transactions are atomic by construction. Fixing pre-§9 is throwaway work (~10 min) that would be re-removed during Phase C cutover. Acceptable to defer.

**Effort:** 10 min if shipped pre-§9. Zero post-§9 (the surface goes away).

**Launch-blocking?** No.

---

### B36 — `_parallel_brickowl_cancels` semaphore is per-call, not per-process — **LOW (LATENT)** — Open (subsumed by §9 future concurrent-saga design)

**Surfaced by:** 2026-05-16 codebase audit while reviewing B16/H10 implementation.

**File:line:** `scripts/checkout/saga.py:~130` (`sem = asyncio.Semaphore(_BRICKOWL_CANCEL_CONCURRENCY)` inside `_parallel_brickowl_cancels`).

**Symptom:** the `Semaphore(5)` is created fresh per call to `_parallel_brickowl_cancels`. If two compensation paths run concurrently (different sagas, different `job_id`s), each gets its own Sem(5), so total concurrent BrickOwl cancels could reach 5×N. The intent was a global cap.

**Why it's open:** single-active saga today means concurrent compensations cannot occur — the saga timeout (B4) ensures at most one in-flight saga per checkout_id, and `_running_sagas` doesn't prevent multi-job parallelism but each job has its own state lock. The latent case fires when (a) multi-saga concurrency becomes real (post-§9 with row-level locks + multi-worker), AND (b) the real BrickOwl Playwright cancel ships (roadmap #5) so each cancel is heavy enough that the 5×N concurrency matters for account lockouts.

**Fix when needed:** move the semaphore to module level so all callers share it:

```python
_brickowl_cancel_sem = asyncio.Semaphore(_BRICKOWL_CANCEL_CONCURRENCY)

async def _parallel_brickowl_cancels(order_ids):
    ...
    async def _cancel_one(oid):
        async with _brickowl_cancel_sem:
            ...
```

But: module-level `asyncio.Semaphore()` binds to the event loop at construction. Lifespan-time construction is fine; import-time construction would fail if no loop is running. The lazy `_get_semaphore()` pattern in brickowl_client.py (lines 41-45) is the proven shape — mirror it here.

**Effort:** 15 min when needed.

**Launch-blocking?** No.

---

### B37 — `debug_optimize` doesn't match `/quote` flow (missing BrickLink + free-shipping) — **LOW** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit while sweeping `debug_router.py`.

**File:line:** `scripts/checkout/debug_router.py:~315-334`.

**Original symptom:** `debug_optimize` called `merge_listings(lego, brickowl)` — only 2 sources — AND skipped `apply_free_shipping_thresholds`. The customer-facing `/quote` flow includes BrickLink AND applies the free-shipping threshold. Operators previewing `/quote` totals via the debug endpoint saw **higher** totals than the customer would.

**What shipped:**

- `debug_optimize` now calls `merge_listings(lego_listings, brickowl_listings, bricklink_listings)` — matching the saga's quote flow.
- Wraps the result in `apply_free_shipping_thresholds(...)` — same as saga.
- `bricklink_client` import + `apply_free_shipping_thresholds` import added to `debug_router.py`.

**Verified:** AST + grep — `bricklink_client.get_all_listings` and `apply_free_shipping_thresholds(optimize` both present in `debug_optimize`.

**Operator implication:** debug previews now match what customers actually see. Bug-hunting via the debug endpoint no longer has hidden price drift.

---

### B38 — `debug_optimize` `lego_available` initialized but never populated — **LOW** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit (paired discovery with B37).

**File:line:** `scripts/checkout/debug_router.py:~337-338`.

**Original symptom:** `lego_available = []` was set unconditionally and never appended to. The response returned `lego_fallback_items=lego_available` (always empty) and `unsourceable_items=allocation.lego_fallback_items`. With LEGO as a primary source (post-L5), `lego_fallback_items` IS the set of unsourceable pieces — the old `lego_available` distinction was vestigial from the pre-LEGO-as-primary design.

**What shipped:**

- Removed the dead `lego_available = []` variable.
- Both `lego_fallback_items` and `unsourceable_items` in the response now point to the same `unsourceable` list (`allocation.lego_fallback_items`).
- Comment documenting that the duplicate response field is kept for backward-compat of the response shape; collapse it in a future cleanup.

**Verified:** AST + grep — `lego_available = []` is gone.

**Operator implication:** debug response previously reported `lego_fallback_items: []` always, which was misleading. Now it correctly reflects unsourceable pieces.

---

### B39 — Main.py L1 boot block used lenient `sk_live_` startswith — **LOW** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit while sweeping the L1 boot block for consistency with the B6/H4 (key_format.py) work.

**File:line:** `scripts/Main.py:~164`.

**Original symptom:** the L1 defense-in-depth check used `stripe_key.startswith("sk_live_")` to classify the key as live before refusing boot. This is the LENIENT check — would classify a too-short malformed key (e.g., `sk_live_xyz`) as "live". The B6/H4 work moved key validation to `payment/key_format.py` (strict ≥8 chars beyond prefix). With B6 shipped, gate.py and stripe_provider.py both use `key_format.key_mode`; Main.py's L1 was the third site and was still using the old lenient check.

In practice the inconsistency was invisible because line 153's "CHECKOUT_ENABLED true + gate DISABLED" check fires first for a malformed key (StripeProvider refuses to construct → registry empty → gate DISABLED → line 153 refuses boot). But the duplication wasn't aligned with the canonical helper.

**What shipped:**

- Replaced `stripe_key.startswith("sk_live_")` with `_stripe_key_mode(stripe_key) == "live"` (importing `key_mode as _stripe_key_mode` from `.checkout.payment.key_format`).
- All three layers (L0 gate, L1 boot, L5 provider) now classify keys identically.

**Verified:** AST + grep — the startswith call is gone; the canonical helper import is in place.

**Operator implication:** none in current operation. The fix is structural — eliminates a divergent classification that could surface later if the L1 block is ever moved or restructured.

---

### B40 — Router initial save missing `customer_message: None` key — **DOC** — ✅ SHIPPED 2026-05-16 (pre-DB-migration audit pass)

**Surfaced by:** 2026-05-16 codebase audit while verifying the B12 grep invariant.

**File:line:** `scripts/checkout/router.py:~250` (the `customer_message` key in the initial `checkout_store.save()` dict).

**Original symptom:** the router's initial state save dict included `"error": None` but not `"customer_message": None`. The B12 contract (documented in CLAUDE.md) says: every site that writes `error` to state must also write a paired `customer_message`. The router's initial save violated this for the "no error yet" case.

Functionally fine — Pydantic's `Optional[str] = None` default handled missing keys — but the audit-grep invariant in CLAUDE.md was broken.

**What shipped:**

- Added `"customer_message": None` to the router's initial save dict, paired with the existing `"error": None`.
- Comment referencing B12 contract.

**Verified:** AST + grep — `"customer_message": None` present in router.py.

**Operator implication:** the CLAUDE.md audit grep (`grep -n '"error":' scripts/checkout/`) now finds matched `"customer_message":` lines at every site, including the router's initial save. The B12 contract is fully enforced.

---

## 5. Open product questions

These are product-strategy questions that the audit surfaced but cannot answer alone. They should be resolved before v1 ships:

1. **BrickOwl ordering strategy** (`docs/ORDER_OPTIMIZER.md §17.2`): Option A (Playwright; LAIGO collects payment) vs Option B (Cart URL redirect; customer pays BrickOwl directly). Option A preserves the "fully automated" promise but exposes LAIGO to Playwright fragility and float-financing the BrickOwl portion. Option B preserves the brand promise less perfectly but eliminates ~half the Saga complexity.
2. **Allocation drift tolerance:** When fresh revalidation produces a higher total, does LAIGO eat the small delta (≤2%), or does the customer get a "confirm new price" prompt unconditionally? Drives both Saga design and customer-facing flow.
3. **Hourly financial exposure cap:** What is the maximum at-risk amount that should be permitted before `/confirm` returns 503? $500? $5000? Business risk-appetite question, not engineering.
4. **Customer concurrency policy:** If a customer has an active Saga for a job, is a second `/quote` for the same job (different shipping address, different country, etc.) permitted, or is the job locked until the Saga terminates?
5. **Refund policy on commit-but-not-capture failures:** When orders are placed and capture fails (MANUAL_REVIEW), does LAIGO contact the customer for alternate payment, refund the marketplace orders (eating cost), or both?

**Resolved 2026-05-15 (kept here for context):**
- **Merchant of record:** LAIGO is the reseller. Customer pays LAIGO via Stripe for bricks + shipping + service fee; LAIGO uses its own marketplace accounts to place supplier orders.
- **Partial-fail policy:** all-or-nothing. Any unrecoverable partial failure triggers compensation + full customer refund. Guarantee is conditional on real cancellation working (RPN #2) — see roadmap item 5.
- **Authorization buffer:** 5%. Stripe hold is `1.05 × quote`; capture is for the exact allocated total; the unused authorization decays. Drift beyond 5% → MANUAL_REVIEW.

---

## 6. Operational go-live checklist

Execute in order. Do not skip steps.

### 6.1 Code preconditions

- [ ] All §4 launch-blocking bugs resolved (B3, B4, B5) and unit-tested.
- [ ] §3 roadmap items 1–5 shipped (L0–L6 layered defense, Postgres state, pre-commit revalidation, MarketplaceAdapter, BrickOwl cancellation).
- [ ] §3 roadmap item 7 shipped (per-source rate limiter + shared HTTPX clients).
- [ ] §3 roadmap item 10 shipped (financial-exposure circuit breaker).
- [ ] §3 roadmap item 11: test suite covers `optimize()`, `compute_laigo_fee()`, Saga state transitions, Saga resumption.
- [ ] L6 (this document §2) shipped: `scripts/checkout/audit.py` exists; all migration sites updated; daily NDJSON rotation working.

### 6.2 Configuration preconditions

- [ ] `.env.secrets` contains valid `STRIPE_SECRET_KEY=sk_test_...` (≥ 8 chars beyond prefix).
- [ ] `STRIPE_ENABLED = True` in `scripts/checkout/payment/stripe_provider.py` committed.
- [ ] `BRICKOWL_API_KEY` set and verified against `/checkout-debug/brickowl/...`.
- [ ] `LEGO_EMAIL` + `LEGO_PASSWORD` set; Playwright order verified against headless=False in staging.
- [ ] `CHECKOUT_ENABLED=true` set in Render dashboard.
- [ ] `RENDER=true` set automatically by Render (verify in shell).
- [ ] `STRIPE_API_VERSION` pinned to a known-good version (recommend pinning).
- [ ] `STRIPE_CURRENCY=usd` (or whatever's intended).
- [ ] Alerting destinations configured (Slack webhook, PagerDuty key, email).

### 6.3 Pre-deploy verification

- [ ] `git status` clean; commit hash recorded.
- [ ] `git log -1 --format=%H` matches the SHA you intend to deploy.
- [ ] Test mode end-to-end: real Stripe test key, real BrickOwl, real LEGO.com staging account, run a full mosaic order with `pm_card_visa` — verify state transitions all the way to `payment_captured`.
- [ ] MANUAL_REVIEW path tested: force a capture failure (use `pm_card_chargeDeclinedFraudulent` mid-Saga); verify state ends MANUAL_REVIEW with full `manual_review_reason`.
- [ ] Compensation path tested: force a BrickOwl cancel failure during compensation; verify B3's MANUAL_REVIEW fix fires (not silent FAILED).
- [ ] Saga timeout tested: monkey-patch `lego_client.order_from_lego` to sleep > timeout; verify MANUAL_REVIEW fires.
- [ ] Drift fail-closed tested: force re-optimization > 5% buffer; verify B5's pre-placement check fires (no orders placed).

### 6.4 Post-deploy verification (within 30 minutes of go-live)

- [ ] `curl https://<backend>/checkout/gate` returns:
  ```json
  {
    "mode": "live",
    "is_open": true,
    "payment_provider": "stripe",
    "marketplaces_live": ["brickowl", "lego_official"],
    "reasons": [],
    "commit": "<expected SHA>"
  }
  ```
- [ ] `curl https://<backend>/health` returns 200.
- [ ] Audit log file `outputs/audit/events-YYYY-MM-DD.ndjson` exists and is appending.
- [ ] Render's healthcheck path is confirmed `/health`, **not** `/checkout/gate`.
- [ ] Boot log contains `payment.registry.registered provider=stripe mode=live`.
- [ ] No `payment.skipped` events in audit log. (Should be permanently zero in prod.)
- [ ] First test order: $1 mosaic with internal email — verify all phases complete and audit log contains the expected event sequence.

### 6.5 Rollback plan

- **Soft disable (kill switch):** unset `CHECKOUT_ENABLED` in Render dashboard → restart service. `/checkout/gate` flips to `mode: disabled` within ~30 seconds. `/confirm` returns 503 for new customers. In-flight Sagas continue (intentional — they have money held).
- **Hard rollback:** redeploy previous commit. Postgres state survives (once roadmap #2 lands); current-iteration Sagas may need manual MANUAL_REVIEW.
- **Stripe-side panic:** rotate `STRIPE_SECRET_KEY` in Render. Next boot fails L1; service refuses traffic until env is fixed.

### 6.6 First-day operator vigilance

- Watch the audit log for `saga.failed` and `saga.manual_review` rates.
- Watch Stripe dashboard for unexpected `requires_capture` PaymentIntents older than 1 hour (a stuck Saga).
- Watch BrickOwl/LEGO.com order pages for unexpected duplicates.
- Have the kill switch one click away.

---

## 7. Cross-references

- **Diagnostic record:** `docs/CHECKOUT_AUDIT.md` — keeps the FMEA, gap analyses, target architecture, and L0–L5 implementation history.
- **Operational reference:** `docs/ORDER_OPTIMIZER.md` — module layout, configuration, manual-test workflows.
- **Project guide:** `CLAUDE.md` — antipatterns, module table, key design decisions for everyday changes.

When a launch-blocking item in this file moves to ✅, update §0 and link the commit. When new gaps surface in production, add to §4 and re-evaluate launch-blocking status.

---

## 8. Hardening list (post-Phase-3 + pre-DB-migration bundle)

Defects + latent landmines that remain after Phases 1/2/3 closed B1, B3, B4, B5, B13, B14, B17, B18–B22 AND the pre-DB-migration bundle (2026-05-16) closed B6, B7, B8, B9, B10, B12, B15, B16. Listed in implementation priority. Each entry: severity, location, what to do, how to verify, and **dependencies on other items** so the work can be batched coherently.

This list IS authoritative for "what comes next." Do not work from memory of an earlier list.

### Tier 1 — Customer-visible / security-adjacent

#### H1. B12 — Customer-facing error translation — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Full details under the B12 entry in §4. Summary of what shipped:

- `ERROR_MESSAGES` table in `scripts/checkout/models.py` with seven categories (payment_permanent, payment_transient, marketplace_failure, manual_review, drift_buffer [reserved], gate_closed, timeout).
- `customer_message: Optional[str]` added to `CheckoutStatusResponse`; threaded through `router.py` `get_checkout_status`.
- All 15 saga `"error":` write sites paired with a `"customer_message":` write.
- Audit check: `grep -c '"error":'` == `grep -c '"customer_message":'` == 15.

---

#### H2. Saga `error` field audit — ✅ SHIPPED 2026-05-16 (bundled with H1)

The static check fires cleanly today (15:15 paired). Re-run the check after any future error-write addition:

```
grep -n '"error":' scripts/checkout/saga.py
# every match should be followed on the next line by `"customer_message":`
```

CLAUDE.md memorialized this audit-check pattern in its anti-patterns section.

---

### Tier 2 — Drift / duplication

#### H3. Consolidate `is_truthy` across 4 sites — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

- New `scripts/checkout/_env.py` (dependency-free) exports `is_truthy(value)` + the `_TRUTHY` frozenset.
- `gate.py` re-exports for backward compat (`from ._env import is_truthy`).
- `payment/registry.py` and `payment/stripe_provider.py` import directly.
- `Main.py` boot block replaced its inline literal check with `if not is_truthy(os.getenv("RENDER"))`.
- Verified: exactly one `("1", "true", "yes", "on")` literal under `scripts/checkout/` (in `_env.py`).

---

#### H4. B6 — Stripe key length disagreement — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Full details under the B6 entry in §4. Summary:

- New `scripts/checkout/payment/key_format.py` exports strict `key_mode(key)` (≥8 chars beyond `sk_test_`/`sk_live_` prefix).
- `gate.py` and `stripe_provider.py` both import it. Identical contract by construction.
- Behavioral verification: `key_mode("sk_live_")` → `None`; `key_mode("sk_test_abcd1234")` → `"test"`; `key_mode("pk_test_abcd1234")` → `None`.

---

#### H5. B7 — Validate currency at provider construction — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Full details under the B7 entry in §4. Summary:

- `_ALLOWED_CURRENCIES = frozenset({"usd", "eur", "gbp", "cad"})` in `stripe_provider.py`.
- `STRIPE_CURRENCY` read once in `StripeProvider.__init__`, validated, locked as `self.currency: Final`.
- `PaymentProvider` Protocol declares `currency: str` as required.
- Saga reads `provider.currency`.
- Follow-up flagged as B27: hardcoded allowlist requires deploy for new currencies.

---

#### H6. B17-followup — Wire `_reset_for_tests()` into Main.py lifespan shutdown — ✅ SHIPPED 2026-05-16 (pre-DB-migration final sweep)

**File:line:** `scripts/Main.py` lifespan shutdown block (after `app.state.executor.shutdown()`).

**What shipped:**
```python
try:
    from .checkout.payment import registry as payment_registry
    payment_registry._reset_for_tests()
except Exception:
    log.debug("registry reset on shutdown skipped (already cleared)")
```

Despite the name, the function is safe to call in production shutdown — it just clears the module-level `_active`. The `try/except` covers the case where the registry was already cleared (idempotent).

**Verified:** AST parse + presence check (`payment_registry._reset_for_tests()` appears in Main.py). Future test that runs `with TestClient(app):` twice will no longer hit the B17 RuntimeError on the second lifespan startup.

**Operator implication:** none in production today (uvicorn still spawns fresh child processes per lifespan). The fix is preventative against future test-framework or hot-reload tooling that re-runs lifespan in the same process.

---

#### H7. Add B15 in-code comment for `stripe.api_key` global — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Two in-code comment blocks added above `stripe.api_key = key` and `stripe.api_version = api_version` in `stripe_provider.py`, referencing B15 and the multi-active migration path. See the B15 entry in §4 for full text.

---

### Tier 3 — Marketplace / cache correctness

#### H8. B9 + B10 — Cache invalidation symmetry across marketplaces — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Full details under the B9 + B10 entries in §4. Summary:

- `brickowl_client.invalidate_listing`, `lego_client.invalidate_listing`, `bricklink_client.invalidate_listing` (stub) — each owns its own cache key naming.
- Saga calls `asyncio.gather(...)` over all three on stockout.
- Verified: each client's `invalidate_listing` matches the cache key its `get_all_listings` writes.

---

#### H9. B8 — LEGO.com stockout asymmetry — ✅ SHIPPED 2026-05-16 (Option B; pre-DB-migration bundle)

Full details under the B8 entry in §4. Summary:

- Option B shipped: explicit `except StockoutError as exc:` branch above the generic `except Exception` in the LEGO order step. Compensates without retry; documents that LEGO is the primary source so re-routing wouldn't help.
- B29 (LOW) tracks the follow-up: wire `lego_client.order_from_lego` to actually raise `StockoutError` on DOM-detected stockouts. Branch is currently plumbed but unreachable.

---

#### H10. B16 — Parallelize BrickOwl cancels (BOTH sites) — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Full details under the B16 entry in §4. Summary:

- `_parallel_brickowl_cancels` helper uses `Semaphore(5)` + `asyncio.gather`.
- Both sites refactored (`_compensate` Phase 1 + stockout-retry inline cancel).
- Stockout-retry site behavior changed: no longer aborts on first failure; waits for all, writes one MANUAL_REVIEW with all failures enumerated.
- B28 (MEDIUM, LATENT) tracks the follow-up: per-order retry inside the helper, gated on the real BrickOwl Playwright cancel shipping (roadmap #5).

---

### Tier 4 — Hygiene / latent

#### H11. B23 — Concurrent `/confirm` with different `checkout_id` for same `job_id` — OPEN (deferred to §9)

See the B23 entry in §4. Solved more elegantly in §9's Postgres design via `sagas_one_active_per_job_idx` partial unique index + `pg_advisory_xact_lock(hashtext(job_id))`. App-level 422 catch on `UniqueViolationError` replaces the manual TOCTOU check. Bundle with §9 Phase C.

---

#### H12. Documentation: refresh stale line numbers in CHECKOUT_AUDIT.md — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

- Strengthened the HISTORICAL disclaimer at the top of `docs/CHECKOUT_AUDIT.md` (line count updated from "1200+" to "1280+"; explicit list of post-disclaimer hardening waves).
- Added a 15-row "audit doc says → now lives at" mapping table inside the disclaimer covering the most-cited references.
- B31 (DOC) tracks the follow-up risk: the mapping table itself will drift as saga.py continues to grow.

---

#### H13. `_locks` dict grows unbounded (audit FMEA #16) — OPEN (subsumed by §9)

`checkout_store._locks[job_id]` is added on first access and never removed. Per-job entry; memory leak proportional to lifetime job count.

**Fix:** integrate with `Main.py`'s cleanup thread — when a `job_id`'s output directory is purged on TTL, also `_locks.pop(job_id, None)`. Note: an in-flight saga still holds a strong reference to its lock via `_get_lock(job_id)`'s acquire, so pop-during-saga is safe.

**Status:** subsumed by §9 Phase C (Postgres state). Locks become per-row via `pg_advisory_xact_lock`; in-process dict disappears. Tier-4 cleanup ONLY if Postgres is delayed; not worth touching pre-§9.

---

#### H14. `SagaStatus.FALLBACK_ORDERED` reservation comment — ✅ SHIPPED 2026-05-16 (pre-DB-migration bundle)

Added `# RESERVED — see docs/ORDER_OPTIMIZER.md §8` comment in `models.py` above `FALLBACK_ORDERED`, documenting that the enum value is preserved for the future BrickOwl-primary + LEGO-overflow flow.

---

#### H15. `load/save/update` in `checkout_store` use sync IO under asyncio.Lock — OPEN (subsumed by §9)

`path.read_text()` / `path.write_text()` block the event loop while the `asyncio.Lock` is held. Negligible at current scale.

**Fix when needed:** wrap file IO in `asyncio.to_thread`.

**Status:** subsumed by §9 Phase C. asyncpg replaces file IO entirely; not worth touching pre-§9.

---

### Hardening summary table (post-2026-05-16 bundle)

| H# | Bug ID | Severity | Effort | Status |
|---|---|---|---|---|
| H1 | B12 | LOW (security) | 2 hr | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H2 | — | LOW | 30 min | ✅ Shipped 2026-05-16 (bundled with H1) |
| H3 | — | LOW | 30 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H4 | B6 | MEDIUM | 30 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H5 | B7 | MEDIUM | 30 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H6 | B17 followup | LOW | 5 min | ✅ Shipped 2026-05-16 (pre-DB-migration final sweep) |
| H7 | B15 | LOW | 2 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H8 | B9 + B10 | MEDIUM | 1 hr | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H9 | B8 (Option B) | MEDIUM | 30 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H10 | B16 | LOW | 30 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H11 | B23 | MEDIUM | 30 min | ❌ Open — subsumed by §9 Phase C |
| H12 | doc-drift | doc | 1 hr | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H13 | FMEA-16 | LOW | 30 min | ❌ Open — subsumed by §9 |
| H14 | enum cleanup | doc | 5 min | ✅ Shipped 2026-05-16 (pre-DB-migration bundle) |
| H15 | sync-IO-under-lock | LOW | — | ❌ Open — subsumed by §9 |

**Newly-surfaced from the 2026-05-16 bundle (see §4 for full entries):**

| ID | Title | Severity | Trigger |
|---|---|---|---|
| B27 | `_ALLOWED_CURRENCIES` hardcoded; new currency requires deploy | LOW | LAIGO ships in a market needing a new currency |
| B28 | `_parallel_brickowl_cancels` no per-order retry | MEDIUM (LATENT) | Roadmap #5 (real BrickOwl Playwright cancel) ships |
| B29 | LEGO `StockoutError` plumbed but not raised | LOW | Whenever Option A is justified (DOM-detection work) |
| B30 | "Hold failed unexpectedly" mapped to transient | LOW | Production data shows unexpected-arm actually firing |
| B31 | CHECKOUT_AUDIT.md mapping table drift | DOC | Saga.py edit crosses listed line ranges |

---

**Recommended sequencing (post-2026-05-16 final sweep):**

The pre-DB-migration bundle + final sweep closed every defect that was fixable without DB-migration tech debt. The remaining order:

1. **§9 database migration — Rounds 1/2/3** (Neon locked in as host). DB-coupled items collapse:
   - H11 (B23) → solved by `sagas_one_active_per_job_idx` partial unique index.
   - H13 (`_locks` unbounded) → in-process dict deleted; row locks via `pg_advisory_xact_lock`.
   - H15 (sync-IO-under-lock) → asyncpg replaces file IO.
   - B11 (destructive iteration checkpoint) → becomes `sagas.iterations` JSONB design.
   - B26 (corrupted JSON) → JSON files disappear.
2. **B28** — bundle with roadmap #5 (real BrickOwl Playwright cancel). LATENT until then.
3. **B29** — bundle with the LEGO DOM-stockout-detection work (when justified).
4. **B27, B30, B31** — DOC/LOW; opportunistic.

**Zero independent-of-DB pre-launch work remaining.** The 2026-05-16 bundle + final sweep cleared the pre-§9 runway completely. Every remaining open item is either (a) gated on the DB migration, (b) gated on other deferred work (roadmap #5 / DOM detection / production data), or (c) opportunistic doc cleanup.

---

## 9. Database migration plan

**Authoring date:** 2026-05-16.
**Owner:** Grant Benson.
**Driver decision:** asyncpg (raw SQL, no ORM). Rationale in §9.1.4.
**Scope:** ALL persistent state — mosaic generation jobs AND checkout state. Operator can see job failures in one place.
**Host decision:** Neon (locked in 2026-05-16; see §9.3.11.1).

### Terminology (READ FIRST)

This section uses the word "branch" for two different things. Always read it qualified:

| Term | Meaning |
|---|---|
| **git branch** | A version-control branch in the LAIGO repo (e.g., the `E2E` git branch, "merge to git main"). Lives in `.git/`. |
| **Neon branch** | A copy-on-write database clone in Neon (e.g., the Neon `dev` branch, the Neon `main` branch). Lives in your Neon project; created via dashboard or `neon branch create`. Completely independent of git. |

When this document says **`main`** without qualification inside a Neon context (e.g., "create the `dev` branch off `main`"), `main` is the Neon `main` branch (Neon's default production branch — the one your `DATABASE_URL` points at in Render). When you see "merge to main" in a git/PR context, that's the git `main` branch.

A useful mental model: Neon branches are to your database what git branches are to your code. The two systems share a naming convention by coincidence, not by design.

This is a **three-round document** by design:
- **Round 1** (§9.1) — goals, scope, host comparison, driver rationale, schema sketch, sequencing.
- **Round 2** (§9.2) — full DDL, indices, JSONB usage, asyncpg pool design, transaction semantics, cutover plan, dual-write phase.
- **Round 3** (§9.3) — migration tooling, rollback, observability, saga resume-on-startup, L6 integration, open questions to resolve before coding.

Each round assumes the prior round has been read. **Read all three before writing any code.**

---

### 9.1 Round 1 — High-level plan

#### 9.1.1 What problem this solves

Today's state architecture:

| State category | Storage | Lifetime |
|---|---|---|
| Mosaic generation job tracking | `app.state.jobs` dict in memory + filesystem (`outputs/{job_id}/`) | Process lifetime (lost on restart) |
| Mosaic queue order | `app.state.queue_order` list | Process lifetime |
| Active job counter | `app.state.active_jobs` int | Process lifetime |
| Per-job processing locks | `app.state.jobs_lock`, `progress_lock`, `queue_lock` (threading.Lock) | Process lifetime |
| Checkout state per job | `outputs/{job_id}/checkout_state.json` (file) | Until job TTL purges output dir |
| Per-job checkout locks | `checkout_store._locks` dict in memory | Process lifetime |
| In-flight saga task handles | `router._running_sagas` set | Process lifetime |
| Active payment provider | `payment.registry._active` module global | Process lifetime |
| Listings + quote cache | `cache._store` dict in memory | TTL-based |

**What a Render restart loses RIGHT NOW (every single one of these is a customer-impact event):**
1. Every queued or running mosaic job. Customers see their upload disappear with no email.
2. Every in-flight saga. Stripe authorizations are orphaned on customer cards for up to 7 days. No automatic release. No operator visibility from `/status` (state file persists but no resume code reads it).
3. Every cached quote. Customer who saw a $42 quote 2 minutes ago sees a different price on retry.
4. Every per-job lock. If a future multi-worker setup recovers a saga from disk, it has no continuity with the lock that was guarding it.
5. The audit trail of the lost jobs — only what's already on disk (mosaic outputs/, checkout_state.json) survives. No "job X failed because Y at time Z" record exists.

**What persistent state buys:**
- Crash safety: saga resume-on-startup releases orphaned holds, retries timed-out captures, surfaces stranded MANUAL_REVIEW cases.
- Multi-worker safety: `MAX_WORKERS > 1` becomes feasible (row-level locks + DB advisory locks replace in-process `_locks` and `threading.Lock`).
- Single-pane-of-glass observability: `SELECT * FROM jobs WHERE status='failed' ORDER BY ts DESC LIMIT 100;` shows every failure path across mosaic + checkout.
- Audit log target: L6 writes events into the same DB; correlations between mosaic failures and checkout failures become one JOIN.
- Reconciliation: a periodic task can `SELECT * FROM checkout_state WHERE saga_status='stripe_held' AND updated_at < NOW() - INTERVAL '1 hour'` and surface orphaned holds.

**Closes immediately:** S1 (persistent state), partial S4 (testable surface), B11, B24, B25, B26, H6, H13, H15, audit FMEA #3, #5, #16, #17.

#### 9.1.2 Goals and non-goals

**Goals (v1 — what this migration MUST accomplish):**
1. All persistent state lives in Postgres. Filesystem usage shrinks to artifact storage only (`outputs/{job_id}/artifact.zip`, debug screenshots).
2. Saga resume-on-startup: every non-terminal saga is examined and routed (resume, recover, manual review) within 30 seconds of boot.
3. Mosaic job queue survives restart: queued jobs re-enter the queue; running jobs are reset to queued (Render restarts are rare and idempotent re-run is cheap relative to manual reschedule).
4. `_locks` dict gone. Per-row locks use Postgres advisory locks (`pg_advisory_xact_lock`) keyed by `hashtext(job_id)`.
5. Operator can answer "what's the state of job X?" via a single SQL query, regardless of whether the job is in mosaic or checkout phase.
6. Stripe hold reconciliation: a 5-minute periodic task `SELECT` orphaned holds and releases them.
7. Schema migrations checked into the repo (alembic OR raw SQL files in `migrations/` — see §9.2).

**Non-goals (NOT in v1):**
- Multi-region deployment. One Postgres instance, one region.
- Read replicas. v1 fits comfortably on a single instance.
- Sharding. Single-tenant; pre-launch traffic; sharding decisions are absurd at this scale.
- Schema versioning per-row. Today's quote / checkout state shapes are good enough; if they evolve, add nullable columns and backfill.
- Backfilling existing filesystem state. Pre-launch. No production data. Cut over hard.
- Postgres-backed cache. The in-process cache stays in memory; it's a different correctness regime (eviction-tolerant) and a DB call per cache_get would 10× the quote latency.

#### 9.1.3 Postgres host comparison (in depth)

You said you might migrate off Render. The host choice should not couple to Render. Below are the four realistic options with the full trade-off matrix.

##### Option A — Render Managed Postgres

**Service:** Render's first-party Postgres offering. Sits next to the web service in the Render dashboard.

**Pricing (2026):**
- Starter: $7/mo, 256 MB RAM, 1 GB storage, single zone.
- Standard: $20/mo, 1 GB RAM, 10 GB storage, single zone.
- Pro: $90/mo, 4 GB RAM, 50 GB storage, single zone.

**Pros:**
- Lowest setup friction. Provision in Render dashboard; connection string appears as an env var (`DATABASE_URL`) on the web service automatically.
- Network-local to the LAIGO API: same Render region, internal network. Sub-millisecond latency to DB. No public-internet hop.
- Backups: automatic daily, 7-day retention on starter, longer on higher tiers.
- Single vendor relationship. One bill, one dashboard, one support channel.

**Cons:**
- **Lock-in.** If you leave Render, this DB has no migration path that doesn't involve `pg_dump` + manual restore on a new host + cutover. The web service moves first; DB moves second. Painful.
- No branching. No "spin up a copy for testing migrations" feature.
- Limited observability. The Render dashboard shows DB metrics but no query-level inspection (pg_stat_statements is enabled but no UI on top of it).
- Free tier doesn't exist for Postgres (web service has a free tier; DB does not).
- Restoring from a backup is operator-initiated through support — not self-service.

**Migration-off cost:** medium. `pg_dump` to local, `pg_restore` to new host, update `DATABASE_URL`. Downtime ~10 min for a small DB. No data lost.

**When this wins:** "I want minimum friction, single vendor, pre-launch scale." If you're sure you're staying on Render for the next 6 months, this is the path of least resistance.

##### Option B — Neon

**Service:** serverless Postgres with native branching. Standalone from any cloud provider.

**Pricing (2026):**
- Free tier: 0.5 GB storage, 1 compute unit autosuspending after 5 min idle, branch support. Genuinely usable for pre-launch.
- Launch: $19/mo, 10 GB storage, no autosuspend, 1 production branch + multiple dev branches.
- Scale: $69/mo, 50 GB, multi-region read replicas, point-in-time recovery to any second in last 7 days.

**Pros:**
- **Branching is the killer feature.** `neon branch create migration-test` creates a copy-on-write clone of production. Run schema migrations against the branch, verify, merge back. **This is the right tool for the v1 cutover** — you can test the cutover end-to-end without risking the (currently-empty) production DB.
- Free tier covers all of pre-launch development without spending money.
- Provider-agnostic. Neon runs on AWS / GCP / Azure; you point your app at a connection string. If you leave Render, the DB stays. Update one env var on the new host.
- Point-in-time recovery to any second (paid tiers) is more granular than Render's daily backups.
- Excellent observability: built-in slow query log, EXPLAIN viewer, table size dashboard.
- Connection pooler built in (PgBouncer-compatible) — important for asyncpg + FastAPI (see §9.1.4).

**Cons:**
- **Cold start on autosuspend (free tier).** If the DB has been idle 5+ minutes, the first connection takes 300-500ms while the compute unit spins up. Unacceptable for `/status` polling latency. Solution: pay for Launch ($19/mo) to disable autosuspend, OR keep a heartbeat query running every 4 minutes (ugly).
- Branching has a learning curve. The CLI is good; the mental model (branches are first-class but billed as separate compute units) takes a day to internalize.
- Free tier has bandwidth limits (untested at LAIGO scale; likely fine).
- Network hop from Render → Neon → reply. Round-trip latency ~10–30ms depending on region pairing. Render's internal network would be <1ms. Per query the difference is small; per saga (which makes ~20 queries) it adds up to ~400ms additional saga lifetime. Noticeable on `/status` polling.

**Migration-off cost:** zero — Neon IS the standalone DB. If you leave Render, change the web service's `DATABASE_URL` env var. Done.

**When this wins:** "I want optionality, branching for safe migrations, and I might not stay on Render." This is my recommendation for LAIGO given your "I might migrate from Render" comment.

##### Option C — Supabase

**Service:** Postgres + auto-generated REST/realtime APIs + auth + storage. Built on top of standard Postgres.

**Pricing (2026):**
- Free: 0.5 GB DB, 1 GB storage, 50K monthly active auth users, 2 GB egress.
- Pro: $25/mo, 8 GB DB, 100 GB storage, 250 GB egress, no project-paused-after-inactivity.
- Team: $599/mo, additional features (SSO, audit, advanced backups).

**Pros:**
- Built-in Postgres dashboard with query editor, table editor, log viewer. Easiest "open the DB and look at what's in it" experience of the four options.
- Auth + storage + DB in one product. If you later want customer accounts, Supabase Auth is one-click; you wouldn't reach for Render's nothing here.
- Free tier is generous for pre-launch.
- Provider-agnostic (runs on AWS); leaving Render doesn't affect Supabase.

**Cons:**
- **Auto-generated REST API surface area you don't want.** Supabase by default exposes every table as a REST endpoint via PostgREST. You have to actively disable this or it leaks your schema. Easy to get wrong.
- **Auth is opinionated.** If you don't want Supabase Auth (and LAIGO doesn't, yet), the rest of the product still leans into it. Some features assume `auth.users` exists.
- **Project pauses after 7 days idle (free tier).** Real risk for a slow-traffic pre-launch system.
- Free-tier compute is bursty; cold-path queries can be 200ms+ randomly.
- Heavier surface area than Neon for "just give me Postgres."

**Migration-off cost:** medium. `pg_dump` from Supabase is supported (not the auto-generated REST stuff). Standard Postgres data is portable.

**When this wins:** "I want a database + future user accounts + storage in one product, and I'll explicitly disable the auto-REST features." For LAIGO today, the auto-features are surface area you don't want.

##### Option D — Self-hosted (on a small VPS or your own machine)

**Service:** install Postgres on a Linux box you control. Hetzner, Vultr, Linode, DigitalOcean, or even a Raspberry Pi.

**Pricing:** $5–15/mo VPS depending on provider.

**Pros:**
- Total control. Any extension, any config, any version.
- Cheapest at scale (Hetzner CX21: $5.83/mo, 4 GB RAM, 80 GB disk — would handle LAIGO scale for years).
- Best learning experience for understanding Postgres internals.

**Cons:**
- **You are the DBA.** Backups, upgrades, security patches, monitoring, disaster recovery — all your problem.
- A failure on a Saturday morning is your Saturday morning.
- No automatic failover. If the VPS dies, the DB dies until you bring it back.
- TLS setup is your problem. Postgres → asyncpg over public internet without TLS is a non-starter; setting up Let's Encrypt + pg_hba.conf is a half-day's work.
- No built-in branching, point-in-time recovery, or observability tooling. You build it from scratch.

**Migration-off cost:** zero — you already own the box.

**When this wins:** "I want maximum control, I'm willing to operate a database, and the per-month cost matters." For LAIGO pre-launch, this is the wrong trade.

##### Comparison summary

| Dimension | Render Managed | Neon | Supabase | Self-hosted |
|---|---|---|---|---|
| Setup time | 5 min | 10 min | 15 min | 2-4 hours |
| Lock-in to Render | **High** | None | None | None |
| Branching | No | **Yes** | No | DIY |
| Free tier covers pre-launch | No | **Yes** | Yes (with auto-pause risk) | Cost ~$5/mo |
| Cold-start latency | 0ms | 0ms (paid) / 300-500ms (free idle) | Variable | 0ms |
| Backups | Daily auto, 7d | Continuous PITR (paid) | Daily (paid) | DIY |
| Observability | Basic | **Best** | Best (with surface-area cost) | DIY |
| Web dashboard | Render UI | Neon UI | **Best** | DIY |
| Cost at LAIGO v1 scale | $7-20/mo | $0-19/mo | $0-25/mo | $5-15/mo |
| Migration-off cost | Medium | None | Low-medium | None |

**Recommendation:** **Neon**. Reasoning:
- You explicitly named "I might migrate from Render" as a constraint. Neon decouples the DB from Render entirely.
- Branching is the right tool for the v1 cutover (test the migration safely against a copy of nothing → less compelling pre-launch BUT the branching habit is valuable for future schema changes).
- Free tier covers all development. You pay $19/mo only when launching, not during the migration work itself.
- Provider-agnostic. Leaving Render is one env var change.
- The cold-start drawback is solved by the paid tier; on the free tier, mitigate during pre-launch with a 4-minute heartbeat query (already wanted for connection pool warmup anyway).

**If you disagree:** the second-best is Render Managed Postgres for its zero-friction setup, accepting the lock-in. Supabase is third (good DB, too much auto-magic). Self-hosted is wrong pre-launch.

**Open question for you:** Neon vs Render Managed — which do you want? Decision blocks Round 2 schema work (DDL is identical, but connection string + pool config differ slightly).

#### 9.1.4 Driver: asyncpg, with caveats

**Decision (you):** asyncpg.

**Why this is the right call:**
- asyncpg is the fastest Python Postgres driver, by a lot. ~3× psycopg-binary, ~5× psycopg2. Raw SQL goes through C-level protocol parsing.
- Native asyncio support. No event-loop adapter shims (unlike SQLAlchemy core's async wrapper which goes through an `asyncio.run_in_executor` for some operations).
- No ORM. The saga / job lifecycle is 4-6 tables. ORM ceremony (model classes, migration drift, lazy-load bugs) buys nothing at that scale.
- Connection pool built in (`asyncpg.create_pool`). One pool per FastAPI app instance, sized to `MAX_WORKERS + saga concurrency`.

**Caveats and decisions you'll need to make (Round 2):**

1. **No ORM means no automatic migration tooling.** asyncpg gives you `pool.execute(sql)`. You need to pick a migration tool separately:
   - **alembic** (the SQLAlchemy migration tool) — works fine without using SQLAlchemy models. You write raw SQL in versioned files. Standard, widely understood. **Recommended.**
   - **yoyo-migrations** — simpler, less commonly seen. Works fine.
   - **raw SQL files + a tiny apply-on-boot helper** — minimum machinery, maximum visibility. Tempting for 4-table schema. Risk: when the schema grows, you reinvent alembic poorly.

2. **No ORM means you write the same SQL repeatedly.** asyncpg's `Record` type is a tuple subclass; you can do `dict(record)` but it's manual. Round 2 settles whether to write thin model classes (just `from_record` classmethods) or use raw dicts.

3. **PgBouncer transaction-mode incompatibility** with prepared statements. asyncpg uses prepared statements by default. PgBouncer in transaction-pooling mode breaks them. Neon's built-in pooler IS PgBouncer transaction-mode. **Solution:** either disable prepared statements (`statement_cache_size=0` on the pool) or connect to Neon's direct port instead of the pooler port. Round 2 picks the right one (statement caching matters less than connection pool sharing for v1 scale).

4. **TLS.** asyncpg supports TLS natively (`ssl=...` parameter). All three managed providers (Render, Neon, Supabase) require TLS for non-localhost connections. Round 2 documents the exact `ssl=...` config.

5. **Type adapters.** asyncpg doesn't auto-convert Python `datetime` ↔ Postgres `TIMESTAMPTZ` without registering a codec. JSON/JSONB is fine out of the box. UUIDs are fine. Round 2 lists every type adapter we need.

6. **Transactions: asyncpg.transaction()** vs `pool.acquire() + manual BEGIN`. Round 2 decides per call site.

**Tradeoff explicitly accepted:** typed-models, schema-as-code, and migration-as-code conveniences are lost. The judgment is that 4-6 tables don't need them. If the schema grows past ~15 tables, revisit and consider SQLAlchemy 2.x async + asyncpg as the driver underneath.

#### 9.1.5 Schema sketch (high-level)

Six tables. Names final pending Round 2. All `id` columns are `UUID` (existing job_ids and checkout_ids are already string-shaped opaque; switch to UUIDs OR keep TEXT — Round 2 decides).

| Table | Purpose | Rows per customer order |
|---|---|---|
| `jobs` | Mosaic generation jobs. One row per `POST /generate`. Holds metadata, status, timing, error info. | 1 |
| `job_progress` | Progress snapshots from worker. Append-only. Optional — could be a JSONB column on `jobs`. | many (currently disk-based `.progress` file) |
| `checkouts` | One row per `POST /quote`. Holds quote allocation (JSONB), checkout_id, customer_email, expiry. | 1-N (one per quote attempt) |
| `sagas` | One row per `POST /confirm`. Holds saga_status, payment_hold_id, marketplace order IDs, MANUAL_REVIEW reason, timing. | 1 per confirmed checkout |
| `audit_events` | L6 audit log. Append-only. Event type + JSONB payload + ts + request_id + subject (job_id/checkout_id). | many |
| `payment_holds` | Optional reconciliation index — every Stripe hold created, with last-known status + last reconciliation timestamp. Helps the orphan-hold scanner. | 1 per saga |

**Cardinality:** at LAIGO's launch traffic (10 quotes/day → 1 confirm/day), the year-1 numbers are:
- `jobs`: ~3650 rows
- `checkouts`: ~3650 rows
- `sagas`: ~365 rows
- `audit_events`: ~10k events/year
- `payment_holds`: ~365

Postgres laughs at this scale. The schema design needs no sharding / partitioning thinking; v1 fits on the smallest paid tier.

**JSONB usage:**
- Allocation result (quote details, seller breakdown) → JSONB column on `checkouts`.
- Marketplace order IDs (variable count, per-seller dict) → JSONB columns on `sagas`.
- Audit event payload → JSONB column on `audit_events`.
- Everything else (status enums, timestamps, IDs, amounts) → real columns. Indexed where queried.

**Why this split:** indexable scalars in real columns, variable-shape grab bags in JSONB. Postgres' JSONB is excellent but querying `WHERE allocation->>'seller_id' = X` is slower than `WHERE seller_id = X` AND breaks the schema invariant. The grab-bag fields are write-mostly (saga writes, status endpoint reads); their JSONB shape is owned by `models.py` Pydantic classes.

#### 9.1.6 Sequencing — five phases

Round 2 fleshes out each phase with DDL and code. Round 3 fleshes out tooling and rollback.

**Phase A — Provision and connect (½ day).** Pick host, provision DB, set `DATABASE_URL`, write a minimal `db.py` that exposes `get_pool() -> asyncpg.Pool`. Verify lifespan opens + closes the pool cleanly. No table writes yet.

**Phase B — Schema + migration tool (1 day).** alembic init, write the six tables as one initial migration, run against a Neon branch, verify.

**Phase C — Checkout state migration (2 days).** Replace `checkout_store.{load, save, update}` with DB queries. Saga keeps the same call sites — `checkout_store` is the boundary. Per-job locks become Postgres advisory locks via `pg_advisory_xact_lock`. The cache layer stays untouched. Manual smoke test in dev.

**Phase D — Mosaic job lifecycle migration (2 days).** Replace `app.state.jobs` / `app.state.queue_order` / `app.state.active_jobs` with DB-backed equivalents. Scheduler and cleanup threads read/write DB. ProcessPoolExecutor itself unchanged — workers still execute pic_to_mosaic on local disk; only the metadata moves.

**Phase E — Saga resume-on-startup + reconciliation (1 day).** Lifespan startup queries `WHERE saga_status NOT IN (terminal_set)` and routes:
- `initiated` → no money moved → cancel.
- `stripe_held` (no orders) → release hold via provider.cancel.
- Any orders placed → MANUAL_REVIEW with a clear reason.

A separate 5-minute periodic task reconciles `payment_holds` against Stripe's actual state.

**Phase F — Cutover (½ day).** Flip a feature flag (`DB_BACKEND=postgres` env). Both backends co-exist briefly for `/quote` (the only fully read-side endpoint); checkout state writes happen ONLY to DB once the flag flips. Validate. Remove the JSON-backed code path one week later.

**Total:** 6.5 engineer-days (1.5 weeks calendar with testing breaks).

**Critical-path question:** can Phase E (resume-on-startup) ship in the same release as Phase C? It must — once checkout state is in DB, the gap between "saga running" and "process restarted" is the exact thing this whole migration exists to close. Round 2 confirms this is sequencing-safe.

---

#### 9.1.7 Open decisions before Round 2

These must be settled before DDL is written:

1. **Host: Neon vs Render Managed Postgres?** (recommendation: Neon for branching + portability.)
2. **ID columns: UUID or TEXT?** Existing IDs are `uuid.uuid4().hex[:N]` style strings. TEXT preserves them; UUID is cleaner long-term but requires a migration step for the IDs already in flight. Recommendation: TEXT, with a CHECK constraint for length/format.
3. **Migration tool: alembic, yoyo, or raw SQL files?** Recommendation: alembic.
4. **Schema migration cutover: drain-and-cut or dual-write?** With zero production data, drain-and-cut. Recommendation: hard cutover behind a feature flag.
5. **Connection pool size?** Round 2 calculates from worker count, but a starting point: `min_size=2, max_size=10`.
6. **PgBouncer pooler mode?** Neon defaults to transaction pooling. If asyncpg uses prepared statements (default), conflict. Recommendation: disable asyncpg statement cache (`statement_cache_size=0`) — the per-query overhead is microseconds at LAIGO scale.

When you've decided 1-6, Round 2 is unblocked.

---

### 9.2 Round 2 — Detailed schema + migration mechanics

This round assumes Round 1's open decisions resolved as: **Neon, TEXT IDs, alembic, hard cutover behind feature flag, asyncpg pool min=2/max=10, `statement_cache_size=0`.** If you choose differently in §9.1.7, adjust §9.2.4 and §9.2.10 accordingly — the DDL itself doesn't change.

#### 9.2.1 Full DDL

Six tables. One initial alembic migration creates them all. All `id` columns are `TEXT` (preserves existing string IDs). All timestamps are `TIMESTAMPTZ` (always store with timezone; PG default zone-less is a footgun).

```sql
-- migrations/0001_initial_schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() for generated IDs

-- ─── jobs ───────────────────────────────────────────────────────────────────
-- Mosaic generation lifecycle. One row per POST /generate.
CREATE TABLE jobs (
    job_id              TEXT PRIMARY KEY,
    status              TEXT NOT NULL,                  -- 'queued'|'running'|'complete'|'failed'|'timed_out'
    mosaic_type         TEXT NOT NULL,                  -- '2d'|'3d'
    width_blocks        INTEGER NOT NULL,
    background_pct      INTEGER,
    dither              BOOLEAN NOT NULL,
    upload_filename     TEXT,                           -- original filename from upload
    progress_pct        SMALLINT NOT NULL DEFAULT 0,    -- 0-100
    error_message       TEXT,
    queued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    ttl_expires_at      TIMESTAMPTZ NOT NULL,           -- when cleanup_loop should purge artifacts
    -- Customer-facing metadata for L6 audit correlation
    customer_email      TEXT,
    customer_ip         INET,
    user_agent          TEXT,
    CONSTRAINT jobs_status_valid CHECK (status IN ('queued','running','complete','failed','timed_out')),
    CONSTRAINT jobs_progress_range CHECK (progress_pct BETWEEN 0 AND 100)
);

-- ─── checkouts ──────────────────────────────────────────────────────────────
-- One row per POST /quote. Holds the optimized allocation + customer metadata.
-- A `job_id` can have multiple `checkouts` (different shipping addresses, retried quotes).
CREATE TABLE checkouts (
    checkout_id         TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    shipping_country    CHAR(2) NOT NULL,
    shipping_zip        TEXT NOT NULL,
    customer_email      TEXT NOT NULL,
    -- AllocationResult Pydantic model serialized. ~5 KB typical.
    -- Shape owned by scripts/checkout/models.py — DO NOT query into this from SQL.
    allocation          JSONB NOT NULL,
    -- Items with no listings from any source. Empty list = quote can_proceed.
    unsourceable_items  JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,           -- created_at + 10 min by default
    CONSTRAINT checkouts_country_iso CHECK (LENGTH(shipping_country) = 2)
);

-- ─── sagas ──────────────────────────────────────────────────────────────────
-- One row per POST /confirm. Holds the full saga state.
-- This table replaces outputs/{job_id}/checkout_state.json.
CREATE TABLE sagas (
    checkout_id             TEXT PRIMARY KEY REFERENCES checkouts(checkout_id) ON DELETE RESTRICT,
    job_id                  TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    saga_status             TEXT NOT NULL,
    -- Payment-side fields, provider-agnostic (L5 naming)
    payment_provider        TEXT,                       -- 'stripe' today
    payment_mode            TEXT,                       -- 'test'|'live'
    payment_hold_id         TEXT,                       -- pi_... for Stripe
    payment_authorized_cents INTEGER,
    total_charged_cents     INTEGER,
    -- Marketplace order tracking
    brickowl_order_ids      JSONB NOT NULL DEFAULT '[]'::JSONB,  -- list[str]
    lego_order_id           TEXT,
    -- Error / review fields
    error_message           TEXT,                       -- internal/operator-facing text
    customer_message        TEXT,                       -- customer-facing translation (post-B12/H1)
    manual_review_reason    TEXT,                       -- verbose runbook when saga_status='manual_review'
    -- Timing
    initiated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_transition_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    CONSTRAINT sagas_status_valid CHECK (saga_status IN (
        'initiated','stripe_held','orders_placed','fallback_ordered',
        'payment_captured','compensated','failed','manual_review'
    )),
    -- B23 enforcement at the DB level: at most one non-terminal saga per job_id.
    -- Implemented as a partial unique index below.
    CONSTRAINT sagas_payment_amounts CHECK (
        (payment_authorized_cents IS NULL OR payment_authorized_cents >= 0) AND
        (total_charged_cents IS NULL OR total_charged_cents >= 0)
    )
);

-- ─── payment_holds ──────────────────────────────────────────────────────────
-- Reconciliation index. Every Stripe hold ever created. Periodic scanner uses
-- this to find orphans. Separate from `sagas` because a saga row can be
-- archived/purged while the hold record needs longer retention for chargebacks.
CREATE TABLE payment_holds (
    hold_id                 TEXT PRIMARY KEY,           -- pi_... for Stripe
    checkout_id             TEXT NOT NULL REFERENCES checkouts(checkout_id) ON DELETE RESTRICT,
    provider                TEXT NOT NULL,              -- 'stripe'
    mode                    TEXT NOT NULL,              -- 'test'|'live'
    amount_authorized_cents INTEGER NOT NULL,
    currency                CHAR(3) NOT NULL,
    -- Status mirrored from Stripe; updated by reconciliation task
    last_known_status       TEXT NOT NULL,              -- 'requires_capture'|'succeeded'|'canceled'|'unknown'
    last_reconciled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── audit_events ───────────────────────────────────────────────────────────
-- L6 audit log. Append-only. Indexed for time-range and per-job queries.
CREATE TABLE audit_events (
    id                  BIGSERIAL PRIMARY KEY,
    event               TEXT NOT NULL,                  -- 'gate.confirm_rejected', 'saga.captured', etc.
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id          TEXT,
    job_id              TEXT,                           -- FK omitted; events outlive jobs (retention asymmetry)
    checkout_id         TEXT,
    actor_type          TEXT,                           -- 'customer'|'system'|'operator'
    actor_ip            INET,
    actor_user_agent    TEXT,
    data                JSONB NOT NULL DEFAULT '{}'::JSONB
);

-- ─── schema_meta ────────────────────────────────────────────────────────────
-- Single-row metadata. alembic writes its own version table; this is for
-- application-level invariants (e.g., "what's the oldest record we've migrated").
CREATE TABLE schema_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 9.2.2 Indices

```sql
-- jobs: ad-hoc operator queries (active queue, recent failures, TTL cleanup)
CREATE INDEX jobs_status_queued_at_idx ON jobs (status, queued_at DESC)
    WHERE status IN ('queued','running');
CREATE INDEX jobs_ttl_idx ON jobs (ttl_expires_at)
    WHERE status IN ('complete','failed','timed_out');

-- checkouts: lookup by job_id (for B23 same-job check), expiry sweep
CREATE INDEX checkouts_job_id_idx ON checkouts (job_id, created_at DESC);
CREATE INDEX checkouts_expiry_idx ON checkouts (expires_at);

-- sagas: B23 partial unique index — at most one non-terminal saga per job_id
CREATE UNIQUE INDEX sagas_one_active_per_job_idx ON sagas (job_id)
    WHERE saga_status NOT IN ('payment_captured','compensated','failed','manual_review');
-- sagas: operator dashboards
CREATE INDEX sagas_status_idx ON sagas (saga_status, last_transition_at DESC);
CREATE INDEX sagas_completed_at_idx ON sagas (completed_at DESC)
    WHERE saga_status = 'payment_captured';

-- payment_holds: orphan scanner (every 5 min)
CREATE INDEX payment_holds_reconcile_idx ON payment_holds (last_reconciled_at)
    WHERE last_known_status = 'requires_capture';

-- audit_events: time-range, per-event-type, per-job
CREATE INDEX audit_events_ts_idx ON audit_events (ts DESC);
CREATE INDEX audit_events_event_ts_idx ON audit_events (event, ts DESC);
CREATE INDEX audit_events_job_ts_idx ON audit_events (job_id, ts DESC)
    WHERE job_id IS NOT NULL;
-- JSONB GIN for ad-hoc payload queries; defer until query patterns are known
-- CREATE INDEX audit_events_data_gin_idx ON audit_events USING GIN (data);
```

**Key invariant: `sagas_one_active_per_job_idx`** — partial unique index that **enforces B23 at the database level**. Any attempt to INSERT a second non-terminal saga for the same `job_id` raises `UniqueViolationError`. The router's B23 fix (Option B from §4) becomes a 422 response on this exception rather than an application-level check, removing the TOCTOU race entirely.

#### 9.2.3 JSONB column shapes (owned by Pydantic models)

JSONB columns store grab-bag fields. Their **shape is owned by Pydantic models** in `scripts/checkout/models.py` and `scripts/models.py` (new file for the mosaic side). DO NOT query into these via SQL operators (`->>`, `->`) in application code — always deserialize via Pydantic.

Tracked shapes (Round 3 documents the model classes):

| Column | Pydantic model | Approx size | Indexable? |
|---|---|---|---|
| `checkouts.allocation` | `AllocationResult` | 2-10 KB | No |
| `checkouts.unsourceable_items` | `list[dict[str, str|int]]` | < 1 KB | No |
| `sagas.brickowl_order_ids` | `list[str]` | < 1 KB | No (use materialized columns if querying) |
| `audit_events.data` | `dict[str, Any]` | < 5 KB | Yes (GIN index when query patterns settle) |

#### 9.2.4 asyncpg pool configuration

One pool per app instance. Created in lifespan startup, closed in shutdown.

```python
# scripts/db.py
import asyncpg
import os
import ssl
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; check lifespan order in Main.py")
    return _pool

async def init_pool() -> None:
    global _pool
    dsn = os.environ["DATABASE_URL"]
    # Neon: USE THE POOLER ENDPOINT (host contains "-pooler"). The pooler
    # multiplexes our asyncpg connections over a smaller set of Postgres
    # connections, which is what makes the free-tier compute unit's 100-connection
    # cap workable. The direct endpoint (drop "-pooler" from the host) is
    # reserved for `alembic upgrade head` (migrations want session mode) and
    # any debugging via psql. NEVER point app traffic at the direct endpoint —
    # connection exhaustion is on you.
    ssl_ctx = ssl.create_default_context() if "sslmode=" in dsn or "neon" in dsn else None
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        max_inactive_connection_lifetime=300,  # close idle conns after 5 min
        # PgBouncer transaction mode (Neon pooler default) breaks named
        # prepared statements. Disable asyncpg's statement cache.
        statement_cache_size=0,
        # Per-connection timeout for individual queries. Saga-level timeout
        # is the authoritative ceiling (B4); this is a per-call belt.
        command_timeout=10.0,
        ssl=ssl_ctx,
    )

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
```

**Pool sizing rationale:**
- Mosaic generation worker uses NO DB connection (work happens in subprocess; metadata writes are from the FastAPI process via the scheduler thread).
- Each in-flight saga holds a connection only during a query (microseconds), not for its lifetime. Saga concurrency is bounded by `_running_sagas`, which is bounded by `MAX_QUEUE_SIZE=20`.
- Status polling: at peak, 20 customers × 1 poll/2s × ~1ms query = ~10 concurrent DB calls.
- `max_size=10` covers peak + headroom. Bump to 20 if you ever raise `MAX_WORKERS` above 1.

**Lifespan order in `Main.py`:**
1. `await init_pool()` — must succeed before anything reads DB.
2. `payment_registry.register(StripeProvider())` — unchanged.
3. `gate.compute_decision()` — unchanged.
4. **NEW:** `await resume_in_flight_sagas()` — see §9.2.7.
5. Executor + scheduler + cleanup threads start.
6. `start_cache_sweeper()` — unchanged.
7. `yield`
8. Shutdown: executor + scheduler + cleanup, then `await close_pool()` last.

If `init_pool()` raises (DB unreachable), the app refuses to boot. This is correct — without DB, there's no consistent state.

#### 9.2.5 Transaction semantics

Three patterns. Round 3 documents which is used at which call site.

**Pattern 1: single-query reads.** No explicit transaction. Pool implicit.
```python
async def get_saga(checkout_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM sagas WHERE checkout_id=$1", checkout_id)
    return dict(row) if row else None
```

**Pattern 2: read-modify-write inside one transaction.** Replaces the current `checkout_store.update()` lock-acquire / read / merge / write pattern. asyncpg's `pool.acquire()` + `conn.transaction()` is the right shape.
```python
async def update_saga(checkout_id: str, partial: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM sagas WHERE checkout_id=$1 FOR UPDATE",
                checkout_id,
            )
            if row is None:
                raise ValueError(f"saga {checkout_id} not found")
            # Merge in Python; write back; commit on transaction exit.
            ...
```

`FOR UPDATE` is the row-level lock that replaces `_locks[job_id]`. Multiple workers can serialize their writes safely.

**Pattern 3: advisory lock for cross-row coordination.** When a saga needs to coordinate with non-row state (e.g., "is any other saga running for this job_id?"), use `pg_advisory_xact_lock(hashtext(job_id))`. Acquired at transaction start, released on commit/rollback. No application code needs to remember to unlock.

```python
async def claim_job_for_saga(job_id: str, checkout_id: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", job_id)
            # Now safely check + insert saga without B23 race.
            existing = await conn.fetchrow(
                "SELECT saga_status FROM sagas WHERE job_id=$1 AND saga_status NOT IN ('payment_captured','compensated','failed','manual_review')",
                job_id,
            )
            if existing:
                raise HTTPException(409, ...)
            await conn.execute("INSERT INTO sagas ...", ...)
```

The partial unique index `sagas_one_active_per_job_idx` is the belt; the advisory lock is the suspenders. Belt-and-suspenders is appropriate when real money is at stake.

#### 9.2.6 Module organization

```
scripts/
  db.py                      ← NEW: pool init/close, get_pool(), thin query helpers
  db_models.py               ← NEW: dataclasses or TypedDicts mirroring DB rows
  migrations/                ← NEW: alembic env + versioned SQL files
    env.py
    versions/
      0001_initial_schema.py
  checkout/
    checkout_store.py        ← REWRITTEN: same public API, DB underneath
    saga.py                  ← UNCHANGED public behavior; internal queries via checkout_store
    audit.py                 ← NEW: L6 emit() function writing to audit_events
  Main.py                    ← UPDATED: lifespan order, scheduler/cleanup use DB
```

**Key invariant: `checkout_store` keeps its public API.** `load()`, `save()`, `update()`, `read_order_list()` all stay. Their internals change. The saga doesn't need to know it's talking to Postgres. This is the cheapest refactor surface.

`read_order_list()` is a special case — `order_list.json` is in the mosaic workspace and isn't moving to DB (it's an artifact, like the PDF). Keep this function as filesystem-backed.

#### 9.2.7 Saga resume-on-startup

The single most important new behavior. Adds a function called once in lifespan AFTER `init_pool()` and BEFORE the scheduler starts.

```python
# scripts/checkout/saga_resume.py
async def resume_in_flight_sagas() -> None:
    """Inspect every non-terminal saga and route recovery.

    Called once during lifespan startup. Idempotent — safe to re-run if a
    previous attempt was interrupted. Each saga is routed in its own
    transaction so a partial failure doesn't block the others.
    """
    pool = get_pool()
    in_flight = await pool.fetch("""
        SELECT * FROM sagas
        WHERE saga_status NOT IN ('payment_captured','compensated','failed','manual_review')
        ORDER BY initiated_at
    """)
    for row in in_flight:
        try:
            await _route_recovery(dict(row))
        except Exception as exc:
            # Don't let one stuck saga block boot; log + continue.
            logger.critical(
                f"[resume] saga {row['checkout_id']} recovery failed: {exc}. "
                "Manual operator action required."
            )
    logger.info(f"[resume] examined {len(in_flight)} in-flight sagas")

async def _route_recovery(state: dict) -> None:
    job_id, checkout_id = state["job_id"], state["checkout_id"]
    last_status = state["saga_status"]

    # Mirror _handle_saga_timeout's classification, but from a clean process.
    if last_status == "initiated":
        # No hold, no orders. Just mark FAILED.
        await update_saga(checkout_id, {
            "saga_status": "failed",
            "error_message": "Saga abandoned by process restart before payment hold",
        })
        return

    if last_status == "stripe_held":
        hold_id = state["payment_hold_id"]
        provider = payment_registry.get_active()
        try:
            await provider.cancel(hold_id=hold_id, idempotency_key=f"cancel-{checkout_id}")
            await update_saga(checkout_id, {"saga_status": "failed", "error_message": "Resumed after restart; hold released"})
        except Exception as exc:
            await update_saga(checkout_id, {
                "saga_status": "manual_review",
                "manual_review_reason": f"Resume-on-startup: held {hold_id} could not be cancelled: {exc}. Cancel in Stripe dashboard.",
                "error_message": f"Resume cancel failed: {exc}",
            })
        return

    # ORDERS_PLACED, FALLBACK_ORDERED → orders are real; cannot infer capture state.
    await update_saga(checkout_id, {
        "saga_status": "manual_review",
        "manual_review_reason": (
            f"Saga was in {last_status} when the process restarted. "
            f"Operator must: (1) check Stripe for hold {state['payment_hold_id']} status, "
            f"(2) verify each BrickOwl order in {state['brickowl_order_ids']} actually shipped, "
            f"(3) verify LEGO order {state['lego_order_id']!r} actually placed, "
            f"(4) reconcile: capture remaining hold OR refund placed orders."
        ),
        "error_message": "Process restart with orders placed",
    })
```

This single function closes audit FMEA #3 (`Saga crashes mid-flight, no resumption`, RPN 450) and turns every Render restart from "customer-money-at-risk event" into "30 seconds of routing logic."

#### 9.2.8 Mosaic job lifecycle changes

Today (in `Main.py`):
- `app.state.jobs` dict — replaced by `jobs` table.
- `app.state.queue_order` list — replaced by `SELECT job_id FROM jobs WHERE status='queued' ORDER BY queued_at`.
- `app.state.active_jobs` int — replaced by `SELECT COUNT(*) FROM jobs WHERE status='running'`.
- `app.state.jobs_lock`, `progress_lock`, `queue_lock` — replaced by transactions + advisory locks.
- `.progress` file on disk — replaced by `UPDATE jobs SET progress_pct=$1 WHERE job_id=$2` from the worker. (Actually: worker is a subprocess that doesn't have a DB connection. Worker writes to .progress on disk; scheduler thread reads .progress and writes to DB. Simpler than putting asyncpg into the subprocess.)

Cleanup thread queries:
```sql
DELETE FROM jobs WHERE status IN ('complete','failed','timed_out') AND ttl_expires_at < NOW();
```
Followed by `shutil.rmtree(outputs/{job_id})` for each deleted row.

#### 9.2.9 Cutover plan

Pre-launch = no production data. Hard cutover behind a feature flag is correct.

**Step 1.** Deploy code with `DB_BACKEND=json` (default). Behavior identical to today. Validate.
**Step 2.** Set `DATABASE_URL=...` in Render. App boots, init_pool succeeds, but no code reads/writes DB yet.
**Step 3.** Flip `DB_BACKEND=postgres` in Render env. Restart. Lifespan now calls `resume_in_flight_sagas()` against the (empty) sagas table — no-op. All new requests go to DB.
**Step 4.** Smoke test: generate one mosaic, confirm one checkout in test mode. Validate `SELECT * FROM jobs`, `sagas`.
**Step 5.** Remove `DB_BACKEND` flag and all JSON code paths one week later.

`DB_BACKEND` is the **only** application-level switch. It is read once in lifespan; `checkout_store` and the mosaic lifecycle code branch on it at import time. No per-request branching.

#### 9.2.10 alembic configuration

```
# alembic.ini
[alembic]
script_location = scripts/migrations
sqlalchemy.url = ${DATABASE_URL}
file_template = %%(rev)s_%%(slug)s
```

```python
# scripts/migrations/env.py
import os
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()

run_migrations_online()
```

Migration files are raw SQL inside `op.execute(...)` calls (we're not using SQLAlchemy models):

```python
# scripts/migrations/versions/0001_initial_schema.py
from alembic import op

revision = "0001"
down_revision = None

def upgrade():
    op.execute(open("scripts/migrations/sql/0001_initial_schema.up.sql").read())

def downgrade():
    op.execute(open("scripts/migrations/sql/0001_initial_schema.down.sql").read())
```

Keep the actual DDL in plain `.sql` files for diffability. The Python wrapper is just alembic's plumbing.

**Apply migrations on boot OR via separate command?** Recommendation: separate command (`alembic upgrade head`) run before the app boots. Render has a "pre-deploy command" hook for this. Mixing schema migration into app boot is a footgun — if the migration fails, the app appears down without a clear "schema migration failed" signal. Pre-deploy isolation is cleaner.

---

### 9.3 Round 3 — Operational mechanics, edge cases, open questions

This round assumes Rounds 1 + 2 are read. The shape of the work is fixed; this round is "how to actually operate it day-to-day."

#### 9.3.1 alembic workflow — daily-use cheatsheet

```bash
# Create a new migration (autogenerate is OFF since we use raw SQL — alembic
# would otherwise try to introspect models we don't have).
alembic revision -m "add_some_column"
# Edit the new file in scripts/migrations/versions/ — write op.execute("ALTER TABLE...").

# Apply migrations to whatever DATABASE_URL points at.
alembic upgrade head

# Roll back the most recent migration.
alembic downgrade -1

# Show current revision.
alembic current

# Show history.
alembic history
```

**Pre-deploy command on Render:** `alembic upgrade head && python -c "print('migrations applied')"`. The `&&` short-circuit ensures the deploy halts if migration fails. The print is a single confirmation line in the deploy log.

**Neon branching for testing migrations:**
```bash
# Spin up a copy of production for migration testing.
neon branch create migration-test-2026-05-30

# Get its connection string.
neon connection-string migration-test-2026-05-30
# → postgres://...

# Apply the new migration against the branch.
DATABASE_URL=postgres://... alembic upgrade head

# If it works, merge by deleting the branch and applying to main.
neon branch delete migration-test-2026-05-30
DATABASE_URL=$PROD_URL alembic upgrade head
```

This is the workflow that justifies Neon over Render Managed Postgres. Render-managed has no branching equivalent; the closest is `pg_dump` → restore-to-local, which is slower and doesn't preserve identity.

#### 9.3.2 Local dev workflow

Two options for "I want to run LAIGO locally with Postgres":

**Option 1 — Neon free-tier branch.** Create a branch off main (or off an empty `dev` parent), point local `DATABASE_URL` at it. Free until you exhaust the 0.5 GB / compute-hours budget. Works the same as production. Recommended.

**Option 2 — Local Postgres in Docker.** `docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16`. `DATABASE_URL=postgres://postgres:dev@localhost:5432/postgres`. Faster (no network hop), but you must remember to `alembic upgrade head` after every schema change pulled from main.

Either way: NEVER point local dev at the production DB. Use a `.env.local` file (gitignored) with the dev `DATABASE_URL`.

#### 9.3.3 Connection failure modes

asyncpg's pool handles most transient failures invisibly (reconnect on stale connection). The remaining cases:

| Failure | Symptom | Handling |
|---|---|---|
| Pool exhausted (all 10 connections in use) | `asyncpg.PoolExhausted` after `command_timeout=10s` | Bump `max_size` OR find the leak (transactions not closing). Log CRITICAL with stack trace. |
| DB unreachable at boot | `init_pool()` raises `OSError` | Lifespan crashes. Render reports the app as failing to start. Operator's job to investigate. |
| DB unreachable mid-request | asyncpg raises `ConnectionDoesNotExistError` | Per-request: 500 to customer with generic message; logged. Pool reconnects on next request. For saga: `_handle_saga_timeout`-equivalent recovery; in v1, the saga timeout (B4) catches this naturally — the saga's stuck `await` times out at 15 min, handler routes MANUAL_REVIEW. |
| DB slow (>10s for a query) | `asyncio.TimeoutError` raised by asyncpg's `command_timeout` | Same as unreachable mid-request: 500, log. Saga: 15-min timeout catches it. |
| Migration version mismatch | App boots successfully; first query fails on missing column | Boot-time check: app reads `alembic_version` table, refuses to start if version != expected. Recommended: add this check to lifespan startup AFTER `init_pool()`. |

**Boot-time schema version check:**
```python
async def verify_schema() -> None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT version_num FROM alembic_version")
    if row is None:
        raise RuntimeError("alembic_version table empty — migrations never applied")
    current = row["version_num"]
    expected = "0001"  # bumped by every migration
    if current != expected:
        raise RuntimeError(f"Schema version mismatch: DB at {current}, app expects {expected}. Run alembic upgrade head.")
```

This catches the deploy-order mistake: app deployed before migration ran. Without this check, the app boots and starts failing requests with cryptic "column does not exist" errors.

#### 9.3.4 Observability

What to log, what to query, what to alert on.

**Logging (in code, surfacing into Render's log stream):**
- Every pool init/close → INFO.
- Every `resume_in_flight_sagas()` call → INFO with count.
- Every `_route_recovery` decision → WARNING (resume) or CRITICAL (manual_review).
- Every `verify_schema()` mismatch → CRITICAL (refuses boot).
- Every transaction rollback → WARNING with exception class.

**Operator SQL queries — keep these in `docs/operator_sql.md` (new file, future):**
```sql
-- What's stuck right now?
SELECT job_id, saga_status, last_transition_at,
       NOW() - last_transition_at AS stuck_for
FROM sagas
WHERE saga_status NOT IN ('payment_captured','compensated','failed','manual_review')
ORDER BY last_transition_at;

-- MANUAL_REVIEW cases needing attention
SELECT checkout_id, job_id, manual_review_reason, last_transition_at
FROM sagas
WHERE saga_status = 'manual_review'
  AND last_transition_at > NOW() - INTERVAL '7 days'
ORDER BY last_transition_at DESC;

-- Orphaned Stripe holds
SELECT ph.hold_id, ph.amount_authorized_cents, ph.created_at,
       NOW() - ph.created_at AS age,
       s.saga_status
FROM payment_holds ph
LEFT JOIN sagas s ON s.payment_hold_id = ph.hold_id
WHERE ph.last_known_status = 'requires_capture'
  AND ph.created_at < NOW() - INTERVAL '1 hour';

-- Recent job failures
SELECT job_id, status, error_message, started_at, completed_at
FROM jobs
WHERE status IN ('failed','timed_out')
  AND completed_at > NOW() - INTERVAL '24 hours'
ORDER BY completed_at DESC;

-- Audit trail for one specific customer email
SELECT e.event, e.ts, e.data
FROM audit_events e
WHERE e.checkout_id IN (
    SELECT checkout_id FROM checkouts WHERE customer_email = 'customer@example.com'
)
ORDER BY e.ts DESC;
```

**Alerting (post-L6):**
- `count(saga_status='manual_review')` increases → page on-call.
- Pool exhaustion log line → page on-call.
- Schema mismatch log line → page on-call.
- `count(audit_events WHERE event='payment.skipped')` > 0 → P0 (RPN #1 regression).

#### 9.3.5 Backups + restore

**Neon (recommended):**
- Free tier: 7 days of point-in-time recovery, settable to any second.
- Paid: 30 days PITR.
- Restore via dashboard: select timestamp → "create branch at this point" → cutover.

**Render Managed:**
- Daily snapshots, 7-day retention on starter.
- Restore = open Render support ticket → they restore to a new instance → you cut over.

**Either way, before launch:** practice the restore once. Document the exact steps in `docs/disaster_recovery.md`. Untested backups don't count.

#### 9.3.6 Periodic reconciliation tasks

Two background tasks added in Phase E:

**Task 1 — Orphan hold scanner (every 5 minutes):**
```python
async def reconcile_payment_holds() -> None:
    pool = get_pool()
    rows = await pool.fetch("""
        SELECT hold_id FROM payment_holds
        WHERE last_known_status = 'requires_capture'
          AND last_reconciled_at < NOW() - INTERVAL '5 minutes'
        LIMIT 100
    """)
    provider = payment_registry.get_active()
    for row in rows:
        try:
            # Stripe-specific: query the PaymentIntent for current status
            intent = await asyncio.to_thread(stripe.PaymentIntent.retrieve, row["hold_id"])
            new_status = intent.status
            await pool.execute(
                "UPDATE payment_holds SET last_known_status=$1, last_reconciled_at=NOW() WHERE hold_id=$2",
                new_status, row["hold_id"],
            )
            # If Stripe says succeeded/canceled but our saga says otherwise → drift; log CRITICAL.
            saga = await pool.fetchrow("SELECT saga_status FROM sagas WHERE payment_hold_id=$1", row["hold_id"])
            if saga and saga["saga_status"] == "stripe_held" and new_status in ("succeeded","canceled"):
                logger.critical(
                    f"[reconcile] Stripe<->saga drift: hold {row['hold_id']} is {new_status} "
                    f"in Stripe but saga is {saga['saga_status']}. Investigate."
                )
        except Exception as exc:
            logger.error(f"[reconcile] hold {row['hold_id']} reconcile failed: {exc}")
```

Run via a separate task in lifespan:
```python
asyncio.create_task(_reconcile_loop())  # strong ref via app.state.reconcile_task
```

**Task 2 — Job TTL cleanup (every 5 minutes — replaces today's threading.Thread):**
```python
async def cleanup_expired_jobs() -> None:
    pool = get_pool()
    rows = await pool.fetch("""
        DELETE FROM jobs
        WHERE status IN ('complete','failed','timed_out')
          AND ttl_expires_at < NOW()
        RETURNING job_id
    """)
    for row in rows:
        shutil.rmtree(OUTPUT_DIR / row["job_id"], ignore_errors=True)
    logger.info(f"[cleanup] purged {len(rows)} expired jobs")
```

Both tasks run on the asyncio event loop, not in threads — simpler.

#### 9.3.7 L6 audit log integration

L6 lands in the same migration. The audit_events table is created in Phase B; the `emit()` function is added in Phase E. Migration sites listed in §2.6 are updated as part of Phase F (cutover).

Function shape:
```python
# scripts/checkout/audit.py
async def emit(
    event: str,
    *,
    subject: dict | None = None,    # {job_id, checkout_id}
    actor: dict | None = None,
    data: dict | None = None,
    request_id: str | None = None,
) -> None:
    """Best-effort audit log. NEVER raises."""
    try:
        pool = get_pool()
        await pool.execute("""
            INSERT INTO audit_events (event, request_id, job_id, checkout_id,
                                       actor_type, actor_ip, actor_user_agent, data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
            event, request_id,
            (subject or {}).get("job_id"),
            (subject or {}).get("checkout_id"),
            (actor or {}).get("type"),
            (actor or {}).get("ip"),
            (actor or {}).get("user_agent"),
            data or {},
        )
    except Exception as exc:
        # Audit must not break checkout. Fall back to stderr only.
        print(f"AUDIT EMIT FAILED: {event} {exc}", file=sys.stderr)
```

The `try/except` is the load-bearing safety. An audit-emit failure during a saga MUST NOT propagate; the saga's correctness does not depend on the audit log.

#### 9.3.8 Testing strategy

Until the test suite exists (S4 in the strategic list), testing is manual. With the DB in place, testing gets MUCH cheaper because state is queryable.

**Three test types added in Phase B:**

1. **Migration tests** (per-migration). For each new migration: create a Neon branch, apply, verify with a known query, rollback, verify state. Manual today; can move to CI later.

2. **Saga state-transition tests** (write these in Phase C). Pytest fixture provisions a clean Neon branch per test. Each test:
   - Inserts a row in `sagas` with a known state.
   - Calls a saga function (`_compensate`, `_handle_saga_timeout`, etc.).
   - Asserts the DB row's final state.
   - These were tested manually in Phases 1/2/3; now they get codified.

3. **Reconciliation tests** (Phase E). Mock the Stripe SDK to return various PaymentIntent states; verify the reconciler updates `payment_holds.last_known_status` correctly and logs CRITICAL on drift.

**Recommended pytest config:**
```python
# conftest.py
import pytest_asyncio

@pytest_asyncio.fixture
async def db_pool():
    """Per-test Neon branch. Created at test start, deleted at end."""
    # Implementation TBD per chosen host.
    ...
```

#### 9.3.9 Expected performance impact

LAIGO is read-light, write-light. Numbers:

| Operation | Today | Post-migration | Delta |
|---|---|---|---|
| `POST /quote` | 50-200ms (network-bound, listings fetch dominates) | 60-220ms | +10ms (one INSERT into checkouts) |
| `POST /confirm` | 5-15s (saga launch + Stripe hold) | 5-15s | +20ms (3 INSERTs, advisory lock acquisition) |
| `GET /status` | <5ms (JSON file read) | <5ms (SELECT by PK) | negligible |
| Saga write transition | 1-2ms (file write under lock) | 2-5ms (UPDATE in transaction) | +2-3ms per transition. ~10 transitions per saga = ~30ms total. |
| Boot time | 2s | 3-5s (init_pool + resume_in_flight_sagas) | +1-3s. Acceptable. |

Postgres scale is "not the bottleneck" until tens-of-thousands of customers/day. We are nowhere near that.

#### 9.3.10 Rollback plan

If Phase F (cutover) goes wrong:
1. Set `DB_BACKEND=json` in Render dashboard.
2. Restart service.
3. App reverts to filesystem state.
4. In-flight sagas: any saga that ran against `DB_BACKEND=postgres` is now invisible to the JSON path. **These must be manually finalized** via Stripe dashboard + filesystem JSON file creation. Document this in `docs/disaster_recovery.md`.
5. Investigate root cause; fix; redeploy.

Because pre-launch traffic is ~zero, the rollback "in-flight saga" set is empty in practice. Step 4 is theoretical.

#### 9.3.11 Decisions Log

Resolved 2026-05-16. Answers below are now binding for the implementation work. Each decision is followed by impact notes and future-revisit triggers.

| # | Question | Decision | Confidence | Revisit if… |
|---|---|---|---|---|
| 1 | Host | **Neon** (locked in 2026-05-16 — see §9.3.11.1 for the rationale that overrode the earlier Supabase-leaning) | High | Neon pricing changes materially; or we hit a Neon-specific outage pattern; or we move to a multi-region deployment Neon can't serve |
| 1a | Neon tier path | **Free during pre-launch dev → Launch $19/mo at go-live** (Free tier autosuspends after 5 min idle = 300-500ms cold start; unacceptable for /status polling once real customers exist; Launch disables autosuspend) | High | Free tier becomes operationally annoying earlier than expected — pull Launch forward; or Launch limits hit and we need Scale ($69/mo) |
| 2 | ID column type | **TEXT** with CHECK constraint | High | We expose IDs in URLs and want them unguessable (TEXT can still be a token) |
| 3 | Migration tool | **alembic** | High | Schema churn becomes painful at 30+ migrations (consider squashing) |
| 4 | Cutover | **Hard switch** behind `DB_BACKEND` env flag | High | We get real customers BEFORE cutover (would force dual-write) |
| 5 | Pool size | **min=2, max=10** per uvicorn worker (see §9.3.11.5 for what those numbers mean) | High | Connection-exhaustion errors in logs (raise max); idle costs (lower min) |
| 6 | asyncpg `statement_cache_size` | **0** (Neon's built-in pooler is PgBouncer transaction-mode — same constraint as Supavisor would have been) | High | Switching to Neon's direct endpoint (port-suffix `-pooler` removed from host) for session features — could re-enable cache then |
| 7 | Apply migrations | **Pre-deploy command** (Render hook), not app-boot | High | Render removes pre-deploy hooks; switch to first-instance-only boot migration |
| 8 | L6 audit log in same release | **Yes** | High | Migration scope balloons past 2 weeks (could defer L6 to a follow-on) |
| 9 | Local dev DB | **Neon dev branch** (production parity; reset by deleting + re-creating the branch; uses free-tier compute-hours budget). Docker Postgres is the fallback if compute-hours become a constraint — see §9.3.11.9 | High | Compute-hours budget exhausted (>1 active dev), or team grows beyond solo dev |
| 10 | Worker subprocess connection | **Keep `.progress` file** | Medium | Add `events` table where workers write progress rows; would let frontend get richer status — see §9.3.11.10 |
| 11 | JSON code-path retention | **Keep both code paths for one week post-cutover**, then delete the JSON paths in a follow-up commit. See §9.3.11.11 for what this means | Medium | A bug in the Postgres path surfaces after a few days — keep JSON longer |
| 12 | Audit retention | **Periodic DELETE in v1**, partitioning when `audit_events` > 1M rows | Medium | `audit_events` row count crosses 1M (~2 years at current scale projection) — see §9.3.11.12 |

##### 9.3.11.1 Q1 follow-up — Neon locked in (2026-05-16)

**Decision:** **Neon**. The earlier Supabase-leaning text (preserved in git history) is superseded.

**What drove the decision:**
1. **Portability matched the stated constraint.** "I might migrate from Render" was an explicit user constraint. Neon is provider-agnostic — it runs on AWS/GCP/Azure but you only touch a connection string. Leaving Render is one env-var change. Supabase is similarly portable for raw Postgres, but its auto-magic features (PostgREST, Auth, Realtime, Storage) accumulate lock-in surface that LAIGO doesn't need.
2. **Branching is the right tool for the v1 cutover** AND for every subsequent schema migration. `neon branch create migration-test-<date>` creates a copy-on-write clone of production; test the migration end-to-end without risking the (currently-empty) production DB. Supabase has no equivalent — its "staging" pattern is a separate project with re-applied schema and no shared underlying storage.
3. **"Postgres, nothing more" matches LAIGO's needs.** We don't use auth (Supabase's centerpiece), we don't use realtime, we don't use storage (outputs/ lives on Render's disk). PostgREST would have been an active risk — auto-exposing every table over HTTPS, requiring explicit disable or RLS policies on day one. The failure mode (silent data leak from a forgotten table) is severe enough that "don't even open that door" beats "remember to close that door."
4. **Free tier covers all of pre-launch development.** 0.5 GB storage, 1 compute unit, branching included. Pay $0 until go-live.
5. **Cold-start drawback is paid-tier-solvable.** Free-tier autosuspend after 5 min idle causes 300-500ms cold-start on the first connection. Acceptable during dev (we're the only ones hitting it). At go-live we upgrade to Launch ($19/mo), which disables autosuspend entirely.

**Why not the others (short version — full comparison in §9.1.3):**
- **Render Managed Postgres** — lowest setup friction (5 min), but lock-in to Render. Doesn't satisfy the portability constraint.
- **Supabase** — generous tier and best dashboard, but PostgREST surface area is a risk and the auto-magic features are dead weight for our case. No branching.
- **Self-hosted** — wrong trade pre-launch. Backup/upgrade/security is our problem; a Saturday outage is our Saturday.

**Tier path (committed):**
- **Pre-launch / dev work:** **Free tier**. Autosuspend is fine because no real customer is polling `/status`. Saves $19/mo during the migration work itself.
- **At go-live (flipping `CHECKOUT_ENABLED=true`):** **Launch ($19/mo)**. Disables autosuspend → no cold-start during customer requests. This is the trigger to upgrade; do not delay past it.
- **Future Scale ($69/mo):** triggered by (a) DB > 10 GB (we'll cross this around the 50K-mosaic mark), (b) need for multi-region read replicas, (c) need for >7 days of PITR.

**Connection string (placeholder until provisioned):**

```
postgresql://<user>:<password>@<endpoint>-pooler.<region>.aws.neon.tech/<dbname>?sslmode=require
```

The `-pooler` host-suffix is what selects Neon's built-in PgBouncer-transaction-mode pooler. Use the **pooler endpoint** for the FastAPI app's `DATABASE_URL`. The **direct endpoint** (drop `-pooler` from the host) is reserved for `alembic upgrade head` (migrations want session-mode), the saga-resume-on-startup scanner if it ever needs LISTEN/NOTIFY, and any debugging session with `psql`.

**Impact on the rest of the plan:**

| Area | Impact of choosing Neon |
|---|---|
| Connection string | `postgresql://<user>:<password>@<endpoint>-pooler.<region>.aws.neon.tech/<dbname>?sslmode=require`. Pooler endpoint for app traffic; direct endpoint (without `-pooler`) for migrations and ops tools. |
| asyncpg config | `statement_cache_size=0`, `ssl=ssl.create_default_context()`. Neon's pooler is PgBouncer transaction-mode and does NOT support named prepared statements. **Hard requirement.** §9.2.4 has the full pool config. |
| Branching | First-class. `neon branch create <name>` makes a copy-on-write clone in seconds. Used for: (a) testing each alembic migration before applying to main, (b) per-developer local dev (Q9), (c) staging the Phase F cutover. |
| Local dev | Neon dev branch (Q9 = Neon dev branch). Docker Postgres is the fallback if free-tier compute-hours become a constraint. See §9.3.2. |
| Backups | Free tier: 7 days of point-in-time recovery, settable to any second. Launch tier: same. Scale tier: 30 days PITR. Restore: dashboard → "create branch at this timestamp" → cutover. **No `pg_dump` snapshots needed pre-migration** — the branch itself IS the snapshot. |
| Observability | Neon dashboard has slow query log, EXPLAIN viewer, table-size dashboard. Stripe-level / Saga-level observability still comes from our own logger + (eventually) the L6 audit log. |
| Pricing path | $0 → $19/mo at go-live → $69/mo if/when DB > 10 GB (50K-mosaic mark). |
| Migration-off cost | Effectively zero. Standard `pg_dump`/restore between Neon and any other Postgres host (Render Managed, Supabase, self-hosted). Connection string change is the only app-side edit. |

**If we ever flip away from Neon (revisit trigger in §9.3.11.X):**
- Connection string changes (different host/pooler syntax).
- Branching becomes "create a staging project + re-apply schema" (Supabase-style) or `pg_dump`/restore (Render Managed style).
- Local dev moves to Docker (the §9.3.11.9 fallback).
- Schema DDL itself doesn't change — it's vanilla Postgres.

**Action required before Phase A:**
1. Create the Neon project (dashboard or CLI: `neon project create laigo`).
2. Copy the **pooled** connection string (host contains `-pooler`).
3. Save in `.env.secrets` as `DATABASE_URL=postgresql://...?sslmode=require`. The `.env.secrets` file is gitignored.
4. Verify TLS works: `python -c "import asyncpg, asyncio, os; asyncio.run(asyncpg.connect(os.environ['DATABASE_URL']).close())"` exits cleanly.
5. (Optional pre-Phase-B) Create a `dev` Neon branch off the Neon `main` branch for local development: `neon branch create dev`.

##### 9.3.11.5 Q5 follow-up — pool size numbers explained

`asyncpg.create_pool(min_size=2, max_size=10, ...)` creates an async-managed pool of database connections that your application reuses for the lifetime of the worker process.

**What `min_size=2` means:**
The pool keeps at least 2 connections open to Postgres at all times, even when nothing is happening. The first time anyone needs a connection, it's already established — no TCP handshake, no TLS negotiation, no `SELECT 1` warmup. Costs you 2 idle TCP sockets and 2 slots out of the host's connection cap.

- **Why not min=0?** First request after an idle period would pay ~80–150ms of connection setup (TCP + TLS + Postgres auth handshake) on top of the actual query. For LAIGO's mostly-idle pre-launch traffic, this would hit on every customer's first request.
- **Why not min=10?** We'd be holding open 10 idle sockets per worker. With Neon's pooler each pooler-side connection multiplexes ours, so this is cheap — but if we ever bypass the pooler (direct endpoint), we'd be wasting half the free-tier compute's connection cap.

**What `max_size=10` means:**
The pool will create up to 10 simultaneous connections to Postgres before any further request blocks waiting for one to free up. If we have 11 concurrent `await conn.execute(...)` calls in flight, the 11th waits behind one of the first ten.

- **Why 10?** At today's saga model, a single in-flight saga uses ~3 simultaneous connections at peak (saga writes + concurrent `/status` polls + audit log writes). With `MAX_WORKERS=1` we're capped at 1 saga, so 10 is well over the realistic peak. Headroom matters because `asyncpg` blocks rather than failing when the pool is exhausted — under load you'd see request latency climb instead of getting clean errors.
- **What if it's too low?** Symptoms: slow requests during burst traffic, `asyncio.TimeoutError` on `pool.acquire()`, request queue building up. Fix: raise max_size.
- **What if it's too high?** Symptoms: hitting the database's connection cap (Neon's pooler endpoint: ~10,000 client connections — effectively unbounded for us; Neon's direct endpoint on the free-tier compute: 100 connections). With 10 max_size × number of workers, you can do the math. If we move to `MAX_WORKERS=2`, that's still 20 max connections — comfortably under either cap.

**Impact decision: do we need to raise max_size if we go to MAX_WORKERS=2?**
No, 10 per worker is generous. 2 × 10 = 20 connections, well under Neon's pooler cap and even the direct-endpoint cap.

**Future revisit:** if logs show `asyncio.TimeoutError` from `pool.acquire()`, or the Saga gets visibly slower during peak periods, raise `max_size` to 20 and `min_size` to 4. Document in Round 2 §9.2.

##### 9.3.11.9 Q9 follow-up — Neon dev branch (chosen 2026-05-16)

With Q1=Neon locked in, the local-dev question gets a much cleaner answer than under Supabase.

**Decision: Neon dev branch.** Create a `dev` Neon branch off the Neon `main` branch once Phase A provisioning is done; point each developer's local `DATABASE_URL` at the `dev` branch's pooler DSN (or per-developer sub-branches if/when we grow beyond a solo dev).

**Why:**
- **Production parity.** Same asyncpg config, same pooler behavior (transaction-mode PgBouncer, statement_cache_size=0), same TLS handshake. Bugs related to those (statement-cache misconfigurations, TLS cert chain issues, pooler-specific timing) reproduce locally instead of surfacing only after deploy.
- **Branch reset = clean DB.** `neon branch delete dev && neon branch create dev` wipes everything in seconds — no `docker volume rm` choreography. Useful when alembic migrations are mid-iteration and you want to start fresh.
- **No drift risk between local schema and prod.** Same alembic migrations applied to both.
- **Free** within the free-tier compute-hours budget (which is generous for a solo dev).

**Fallback: Docker Postgres locally** (the alternative path) if any of these become true:
- Compute-hours budget exhausted (multiple developers working in parallel against branches that don't autosuspend).
- Working offline routinely (Neon needs an internet connection).
- Local iteration is hot enough that the network round-trip latency to Neon matters (Neon median is ~10-30ms; Docker localhost is <1ms).

If you switch to Docker, accept the drift risk and validate against a real Neon branch before each merge to the git `main` branch:

```bash
docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 -d postgres:16
DATABASE_URL=postgres://postgres:dev@localhost:5432/postgres alembic upgrade head
```

**Action during Phase A:**
1. Create the `dev` Neon branch: `neon branch create dev --parent main` (where `--parent main` is the Neon `main` branch — Neon's default production branch — not the git `main` branch).
2. Copy its pooler connection string into a gitignored `.env.local`: `DATABASE_URL=postgresql://...?sslmode=require`.
3. Confirm `alembic upgrade head` runs cleanly against the `dev` Neon branch (it will be a no-op on a fresh Neon branch until Phase B writes the first migration).

**NEVER point local dev at the Neon production branch (Neon `main`).** Use `.env.local` (gitignored) for the dev connection string pointing at the `dev` Neon branch; `.env.secrets` holds the production string pointing at Neon `main` and lives only on Render.

##### 9.3.11.10 Q10 follow-up — keep `.progress` file (caveat)

Keeping the `.progress` file means the mosaic-generation worker subprocess does not need asyncpg or a database connection. Cleaner separation, less to fail.

**The "long term" alternative worth knowing about:** add an `events(job_id, ts, kind, payload)` table where the worker writes "20% done, 40% done, …" rows via a tiny synchronous psycopg client (or, more elegantly, by appending to a Unix socket the main process consumes and turns into DB rows). This would:
- Give the frontend richer status (recent events log instead of a single %), useful UX for paid customers waiting on a 90-second job.
- Centralize all state in Postgres (currently `.progress` is the last filesystem holdout).
- Let us query historical job progress for debugging (e.g., "this job spent 70% of its time on quantization").

**Cost:** ~3 hours to plumb. Not needed for v1; revisit after the database migration settles.

**Mark in Round 2 §9.2.5:** when `events` table is added, set `worker.progress.backend` env to `db` and remove the `.progress` write path. Until then, leave the .progress file in place exactly as today.

##### 9.3.11.11 Q11 follow-up — "keep JSON for a week" explained

The migration adds Postgres as the new state backend behind `DB_BACKEND=postgres`. Today's `checkout_store.py` reads/writes JSON files in `outputs/{job_id}/`. After cutover:
- **Option A (delete immediately):** at cutover, replace `checkout_store.py` entirely with the Postgres version. Old JSON files become read-only artifacts. Rollback is hard — you'd have to revert the deploy.
- **Option B (one-week co-existence — chosen):** keep BOTH code paths. `checkout_store.py` becomes:
  ```python
  if os.environ.get("DB_BACKEND", "json") == "postgres":
      # new implementation
  else:
      # old implementation, unchanged
  ```
  At cutover, flip `DB_BACKEND=postgres` in Render. If something breaks in the first week, flip it back to `json` and we're back to the pre-migration state. After one week of stable Postgres operation, delete the `else` branch in a follow-up commit.

**Why a week:** long enough to catch the bugs that only show up under uncommon flows (a failed saga, a manual checkout, an exact-at-restart timing window). Short enough that we don't lose momentum on subsequent hardening work.

**What you'd lose by skipping co-existence:** if the Postgres path has a bug that causes saga state to corrupt for a real customer in week 1, your only recovery is "redeploy the pre-migration code," which can't read any data created during the broken period. With co-existence, you flip a flag and continue.

**What you commit to by accepting it:** ~10 extra lines of branching in `checkout_store.py` for 7 days, plus a calendar reminder to delete them.

**Action:** ship Phase F with both paths present, flag flipped to `postgres`. Calendar reminder: 7 days after cutover, run the cleanup commit.

##### 9.3.11.12 Q12 follow-up — periodic DELETE for v1

`audit_events` is the L6 audit log table. Compliance/retention rules from §2.4: 1 year for financial events (payment.*), 90 days for control-plane events (gate.*, registry.*).

**Option chosen — periodic DELETE:**
```sql
-- runs daily via Render cron job
DELETE FROM audit_events
WHERE (kind LIKE 'payment.%' AND ts < NOW() - INTERVAL '1 year')
   OR (kind NOT LIKE 'payment.%' AND ts < NOW() - INTERVAL '90 days');
```

**Trade-offs:**
- **Simple:** no partitioning, no special access patterns. One cron job.
- **Slow at scale:** DELETE on a non-partitioned table over millions of rows can take minutes and bloat the WAL.
- **Acceptable at LAIGO v1 scale:** projected `audit_events` growth is ~20 rows per checkout. At 1000 checkouts/day that's 20K rows/day, ~7.3M/year. DELETE on 7M rows is slow (~30s). At 100 checkouts/day, ~2K rows/day, ~730K/year — fine.

**When to migrate to partitioning:**

| Trigger | Action |
|---|---|
| `audit_events` row count > 1M | Plan partitioning migration (split into monthly partitions, drop old partitions instead of DELETE) |
| DELETE job takes > 60s | Same |
| DB size growth > 10% per month | Same |
| Audit queries (operator dashboards) get slow | Add index OR partition |

**Documentation that must be updated when this changes:**
- `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` §9.2 (Round 2 DDL) — replace `CREATE TABLE audit_events (...)` with `CREATE TABLE audit_events (...) PARTITION BY RANGE (ts)` + per-month partition creation.
- `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` §9.3.6 (reconciliation tasks) — change the cleanup task from `DELETE` to `DROP PARTITION`.
- `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` §9.3.7 (L6 audit log) — note that `emit()` now writes to the current partition.
- `CLAUDE.md` — add a note that `audit_events` is partitioned and queries should include a `ts` predicate.

**Set a calendar trigger:** check `SELECT COUNT(*) FROM audit_events` quarterly. When it crosses 250K, start drafting the partitioning migration so we're not scrambling at the 1M threshold.

##### 9.3.11.X — Future-revisit triggers (consolidated)

| Decision | Revisit when… | What changes |
|---|---|---|
| Q1: Neon | Neon pricing changes materially; Neon-specific outage pattern emerges; we move to multi-region | Migrate to another Postgres via `pg_dump`/restore. Estimated 1 day. |
| Q1a: Free → Launch tier | Flipping `CHECKOUT_ENABLED=true` for real customers OR free-tier autosuspend causes a noticeable cold-start in operator testing | Upgrade Neon project to Launch ($19/mo). One dashboard click; no code changes. |
| Q5: pool size | `asyncio.TimeoutError` from `pool.acquire()` in logs; or `MAX_WORKERS > 2` | Raise `max_size` (and `min_size` proportionally). Edit §9.2 pool config. |
| Q6: `statement_cache_size=0` | Move off Neon pooler to direct endpoint for session features | Re-enable cache for ~5% query latency improvement. |
| Q9: local dev | Compute-hours budget exhausted; or working offline routinely; or team grows | Switch to Docker Postgres (fallback documented in §9.3.11.9); validate against Neon branch pre-merge. |
| Q10: worker progress | Want richer status UI (events feed) | Add `events` table, plumb worker, deprecate `.progress` file. 3 hours. |
| Q11: JSON paths | 7 days post-cutover, no issues | Delete `else` branches; remove `DB_BACKEND` flag. ~30 min commit. |
| Q12: audit retention | `audit_events` > 1M rows OR DELETE > 60s OR DB growth > 10%/month | Migrate to monthly RANGE-partitioned table. ~1 day. Docs in 4 places to update (listed in §9.3.11.12). |

**Decision sequencing:** Q1 (Neon) is locked in. Q5–Q9 follow from Q1 and are also bound. Q1a (tier upgrade) triggers at go-live. Q2–Q4 and Q7–Q8 are independent and already binding. Q10–Q12 are deferrable.

#### 9.3.12 Out-of-scope (for clarity)

What this migration plan does NOT cover:
- BrickOwl Playwright order/cancel (roadmap #5) — independent work.
- Customer email after capture (roadmap #14) — independent.
- Rate limiter (roadmap #7) — independent; could share DB connection but doesn't have to.
- Multi-region / read replicas / sharding — pre-launch is single-instance.
- Customer accounts (Supabase Auth or otherwise) — explicit non-goal for v1.
- Per-iteration saga history (B11) — a JSONB column on `sagas` called `iterations` could hold this when wanted; not in v1.

---

### 9.4 Summary — what to do when you return to this

**Status (2026-05-16):** All §9.3.11 decisions resolved. Q1 = **Neon** (locked in — see §9.3.11.1 for rationale). Q1a tier path = **Free during dev → Launch $19/mo at go-live**. Q9 = **Neon dev branch** for local. All other questions previously bound.

1. **Provision the Neon project.** Dashboard or CLI (`neon project create laigo`). Copy the **pooler** connection string (host suffix contains `-pooler`). Store as `DATABASE_URL` in `.env.secrets` (gitignored). Verify TLS handshake with a one-line asyncpg connect test.
2. **Create the `dev` Neon branch** off the Neon `main` branch: `neon branch create dev`. Copy its pooler connection string into a gitignored `.env.local`. This is where local dev points. (Neon branch, not git branch — see Terminology block at top of §9.)
3. **Write the schema migration.** Phase B. Test on a throwaway Neon branch (`neon branch create migration-test-<date>`), then delete that Neon branch and apply the migration to the Neon `main` branch.
4. **Migrate `checkout_store`.** Phase C. Keep the JSON path behind `DB_BACKEND` flag (Q11 decision — see §9.3.11.11).
5. **Migrate mosaic lifecycle.** Phase D.
6. **Add resume-on-startup + reconciliation.** Phase E. Add `emit()` for L6 audit log (Q8).
7. **Flip the feature flag.** Phase F. Set `DATABASE_URL=...` (Neon pooler URI) and `DB_BACKEND=postgres` in Render. **Upgrade Neon project to Launch ($19/mo)** in the same window so autosuspend doesn't cold-start the first customer.
8. **Watch for 7 days.** Verify resume-on-startup behaved correctly through one or two Render restarts (forced if necessary). Then run the JSON-path cleanup commit (§9.3.11.11).
9. **Set calendar triggers** for the deferred items in §9.3.11.X (audit table growth check, pool exhaustion check, Neon storage usage approaching 10 GB).

After this lands, the remaining H1–H15 hardening items proceed against a much-easier-to-reason-about system. The whole "what happens on restart" class of bugs is closed.

---

### 9.5 Per-phase operational playbook

Rounds 1–3 above explain *what* and *why*. This section is the *how* — step-by-step for an operator (you or a future maintainer) executing each phase. Each playbook is self-contained: it says which files to create/edit, which commands to run, what to verify before moving on, and when to commit. References to deeper technical content point back to §9.1/§9.2/§9.3.

**Conventions used in this section:**
- 🔧 = code/file change
- 💻 = shell command
- ✅ = verification step (must pass before moving on)
- 📦 = commit checkpoint
- 🛑 = stop / get human confirmation
- All "branch" references are qualified per the §9 Terminology block (Neon branch vs git branch).

---

#### 9.5.A Phase A — Provision & connect ✅ SHIPPED 2026-05-16

**Status:** Complete. Documented here for reference and rollback guidance.

**What shipped:**
- Neon `laigo` project on PostgreSQL 17.8 / AWS us-east-1 / ARM64 compute.
- Pooler DSN saved to `.env.secrets` as `DATABASE_URL` (with `channel_binding=require` for SCRAM hardening).
- `scripts/db.py` — pool init/close, `DB_BACKEND`-gated, refuses direct endpoint, `statement_cache_size=0`, `command_timeout=10s`.
- `Main.py` lifespan wiring — `init_pool()` first, `close_pool()` last; no-op when `DB_BACKEND=json`.
- `scripts/smoke_test_db.py` — reusable diagnostic.
- `requirements.txt` — `asyncpg~=0.29`, `alembic~=1.13` (resolved to asyncpg 0.31 + alembic 1.18 locally).
- Terminology block at top of §9.
- CLAUDE.md design note for `scripts/db.py`.

**Verified:**
- ✅ `SELECT 1` round-trip via asyncpg + TLS + pooler + `statement_cache_size=0`.
- ✅ Neon `main` branch on PG 17.8 confirmed by `SELECT version()`.
- ✅ Region alignment (Render us-east + Neon us-east-1).

**Rollback (if Phase A becomes a problem retroactively):**
- Set `DB_BACKEND=json` in `.env`. `scripts/db.py` no-ops; everything else behaves as pre-Phase-A. Pool isn't even created.
- Delete the Neon project from the Neon dashboard if you decide to switch hosts (no production data to lose).

---

#### 9.5.B Phase B — Schema + alembic (≈1 day)

**Goal:** Versioned, repeatable schema migrations. Six tables created in one initial migration. Verified on a throwaway Neon branch before touching production.

**Prerequisites:**
- ✅ Phase A complete (DATABASE_URL works, asyncpg installed).
- ✅ alembic installed (was installed alongside asyncpg in Phase A).
- 🛑 **Make a fresh git branch for Phase B work:** `git checkout -b phase-b-schema-migration`. Keeps the WIP isolated and lets you abandon cleanly if needed.

**Step 1 — Initialize alembic skeleton** (≈ 15 min)

🔧 Create `alembic.ini` at the project root:

```ini
[alembic]
script_location = scripts/migrations
file_template = %%(rev)s_%%(slug)s
# DO NOT set sqlalchemy.url here — env.py reads it from os.environ
# to avoid the URL appearing in committed config.

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

🔧 Create `scripts/migrations/` directory structure:

```
scripts/migrations/
  env.py              ← (next step)
  script.py.mako      ← (alembic-provided template — copy from `alembic init` scratch dir)
  versions/           ← empty for now; the first migration lands here
  sql/                ← .up.sql / .down.sql files; raw DDL lives here
```

The cleanest way to get `script.py.mako` is to run `alembic init alembic-scratch/` in a throwaway dir, copy `script.py.mako` into `scripts/migrations/`, then delete `alembic-scratch/`. (Alembic ships the template inside its package, but the file isn't trivially accessible via stdlib paths.)

🔧 Create `scripts/migrations/env.py` (per §9.2.10 with a small hardening):

```python
"""alembic environment — reads DATABASE_URL from environment, not alembic.ini.

The direct endpoint is required for alembic operations (session mode is needed
for the migration transactions; PgBouncer transaction mode would break things
like CREATE INDEX CONCURRENTLY in a future migration). To run against Neon:
    1. In the Neon dashboard, copy the DIRECT (non-pooler) connection string.
    2. Set ALEMBIC_DATABASE_URL=<direct DSN> in your shell (NOT in .env).
    3. Run: alembic upgrade head
If ALEMBIC_DATABASE_URL is unset, falls back to DATABASE_URL (pooler) — works
for app-side reads but may not for all schema operations.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

dsn = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not dsn:
    raise RuntimeError(
        "Neither ALEMBIC_DATABASE_URL nor DATABASE_URL is set. "
        "Set ALEMBIC_DATABASE_URL to the Neon DIRECT (non-pooler) DSN before running alembic."
    )
config.set_main_option("sqlalchemy.url", dsn)


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

✅ Verify alembic is configured: `alembic current` should print `(empty)` (no migrations applied yet to the configured DSN). If it errors with a connection issue, fix `ALEMBIC_DATABASE_URL` first.

📦 **Commit checkpoint 1:** "Phase B step 1 — alembic skeleton wired (env.py reads ALEMBIC_DATABASE_URL or DATABASE_URL; no migrations yet)."

**Step 2 — Write the initial schema migration** (≈ 2 hours)

🔧 Create `scripts/migrations/sql/0001_initial_schema.up.sql` — copy the full DDL from §9.2.1 (six `CREATE TABLE` statements + the indices block). Do NOT modify the schema during this step; that's a separate decision with its own RFC. The job here is "translate §9.2.1 verbatim into a file."

🔧 Create `scripts/migrations/sql/0001_initial_schema.down.sql`:

```sql
-- Reverse of 0001_initial_schema.up.sql
-- Order: drop indices implicitly via DROP TABLE; drop tables in reverse FK order.
DROP TABLE IF EXISTS audit_events CASCADE;
DROP TABLE IF EXISTS payment_holds CASCADE;
DROP TABLE IF EXISTS sagas CASCADE;
DROP TABLE IF EXISTS checkouts CASCADE;
DROP TABLE IF EXISTS job_progress CASCADE;
DROP TABLE IF EXISTS jobs CASCADE;
DROP EXTENSION IF EXISTS pgcrypto;
```

🔧 Create the alembic version file `scripts/migrations/versions/0001_initial_schema.py`:

```python
"""initial schema — jobs, job_progress, checkouts, sagas, payment_holds, audit_events"""

from pathlib import Path
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "0001_initial_schema.up.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "0001_initial_schema.down.sql").read_text(encoding="utf-8"))
```

✅ Verify the migration parses (without running it): `alembic check` (alembic ≥1.9). Or just `alembic history` — should show one revision `0001`.

📦 **Commit checkpoint 2:** "Phase B step 2 — initial schema migration written (not yet applied)."

**Step 3 — Test against a throwaway Neon branch** (≈ 30 min)

This is the killer-feature use of Neon. We create a disposable Neon branch off Neon `main`, apply the migration, verify, then delete the Neon branch. Neon `main` stays untouched until we're confident.

💻 Create the throwaway Neon branch via dashboard or CLI:

```bash
# CLI form (requires `neon` CLI installed + logged in):
neon branch create --project-id <laigo-project-id> --name migration-test-2026-MM-DD --parent main

# Then grab its DIRECT endpoint DSN (NOT pooler — alembic uses direct):
neon connection-string --project-id <laigo-project-id> --branch migration-test-2026-MM-DD --pooled false
```

If you don't have the `neon` CLI: do this in the dashboard. Neon Console → laigo project → Branches → New Branch → parent `main` → name `migration-test-2026-MM-DD`. Then open the branch detail and copy the **direct** (non-pooled) connection string.

💻 Apply the migration to the throwaway Neon branch:

```bash
$env:ALEMBIC_DATABASE_URL = "postgresql://...direct.../neondb?sslmode=require"   # PowerShell
alembic upgrade head
```

✅ Expected output: `Running upgrade  -> 0001, initial schema — jobs, job_progress, checkouts, sagas, payment_holds, audit_events`.

✅ Verify table presence:

```bash
$env:ALEMBIC_DATABASE_URL = "postgresql://...direct.../neondb?sslmode=require"
python -c "import asyncio, asyncpg, os; asyncio.run((lambda: asyncpg.connect(os.environ['ALEMBIC_DATABASE_URL']))().__anext__()) if False else None"  # placeholder
```

A simpler verify: open the Neon dashboard → throwaway-branch SQL editor → run:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' ORDER BY table_name;
-- Expected: alembic_version, audit_events, checkouts, job_progress, jobs, payment_holds, sagas

SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname;
-- Expected: includes sagas_one_active_per_job_idx (the B23 partial unique index)

SELECT version_num FROM alembic_version;
-- Expected: 0001
```

🛑 **Pause:** look at every table in the dashboard's table editor. Confirm column types match §9.2.1. JSONB columns appear as `jsonb`. Timestamps appear as `timestamp with time zone`. If anything looks wrong, fix the `.up.sql` and re-test on a fresh throwaway branch (`neon branch delete` + `neon branch create` again — cheap).

**Step 4 — Apply to Neon `main` (production)** (≈ 5 min)

💻 With the throwaway branch verified, point `ALEMBIC_DATABASE_URL` at the Neon `main` direct endpoint:

```bash
$env:ALEMBIC_DATABASE_URL = "postgresql://...main-direct.../neondb?sslmode=require"
alembic upgrade head
```

✅ Re-run the same dashboard SQL queries against Neon `main`. Same expected output.

💻 Delete the throwaway Neon branch:

```bash
neon branch delete --project-id <laigo-project-id> --name migration-test-2026-MM-DD
```

**Step 5 — Wire boot-time schema verification** (≈ 30 min)

Per §9.3.3, the app should refuse to boot if the deployed code expects a schema version different from what's in the DB. Implement `verify_schema()` in `scripts/db.py`:

🔧 Add to `scripts/db.py`:

```python
_EXPECTED_SCHEMA_VERSION = "0001"  # bump this string each time a new alembic migration is added


async def verify_schema() -> None:
    """Refuse boot if DB schema version != expected. Catches deploy-order mistakes."""
    if not is_postgres_backend():
        return
    pool = get_pool()
    row = await pool.fetchrow("SELECT version_num FROM alembic_version")
    if row is None:
        raise RuntimeError(
            "alembic_version table empty — schema migrations never applied. "
            "Run: ALEMBIC_DATABASE_URL=<direct DSN> alembic upgrade head"
        )
    current = row["version_num"]
    if current != _EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Schema version mismatch: DB at {current}, app expects {_EXPECTED_SCHEMA_VERSION}. "
            f"Run alembic upgrade head against the production DSN before redeploying."
        )
```

🔧 In `Main.py` lifespan, immediately after `init_pool()`:

```python
from .db import init_pool, close_pool, is_postgres_backend, verify_schema
await init_pool()
await verify_schema()  # ← NEW: refuse boot on schema mismatch
```

✅ Verify: with `DB_BACKEND=postgres` and DATABASE_URL pointing at Neon `main` (now at revision `0001`), run `uvicorn Main:app` — should boot cleanly with `INFO` log "DB pool initialized." If you intentionally bump `_EXPECTED_SCHEMA_VERSION` to `"9999"` for a test, boot should fail with the mismatch error.

📦 **Commit checkpoint 3:** "Phase B step 5 — boot-time schema version check added; refuses boot on alembic_version mismatch."

**Step 6 — Render pre-deploy hook** (≈ 15 min, only when ready to deploy to Render)

Render lets you configure a "pre-deploy command" that runs before the web service starts. This is where alembic should live in production — never bundled with app boot.

🛑 **Don't do this until you're ready for Phase F.** Phase B's job is to prove the schema works against Neon. The Render pre-deploy hook is configured when we actually want it to run on every deploy.

When ready (Phase F): Render dashboard → laigo service → Settings → Build & Deploy → **Pre-deploy command**:

```bash
ALEMBIC_DATABASE_URL=$DATABASE_URL_DIRECT alembic upgrade head
```

(Where `DATABASE_URL_DIRECT` is a separate Render env var pointing at the Neon **direct** endpoint, since alembic needs session mode.)

**Phase B exit criteria:**
- ✅ `alembic upgrade head` runs cleanly against a throwaway Neon branch.
- ✅ All six tables + indices + extensions present per §9.2.1.
- ✅ `alembic upgrade head` runs cleanly against Neon `main`.
- ✅ Throwaway Neon branch deleted.
- ✅ `verify_schema()` shipped + wired into lifespan.
- ✅ Phase B git branch merged to git `main`.
- 📦 §0 status snapshot updated; §9.6 progress dashboard updated.

---

#### 9.5.C Phase C — Checkout state to Postgres (≈2 days)

**Goal:** `checkout_store.{load, save, update, read_order_list}` keeps its exact public API; internals become DB queries when `DB_BACKEND=postgres`. The saga is untouched. Per-job locks become Postgres advisory locks. The B23 partial unique index becomes the authoritative race-condition defense.

**Prerequisites:**
- ✅ Phase B complete (schema deployed, `verify_schema()` wired).
- ✅ Neon `dev` branch created (needed for local-against-Postgres dev — see §9.3.11.9).
- 🛑 **Fresh git branch:** `git checkout -b phase-c-checkout-store-pg`.

**Step 1 — Create `checkout_store_pg.py` alongside the existing module** (≈ 4 hours)

Don't edit `checkout_store.py` in place. Add `scripts/checkout/checkout_store_pg.py` with the same public surface. The dispatcher (Step 2) chooses one at import time based on `DB_BACKEND`. This keeps the JSON path runnable until Phase F.

🔧 Public surface mirror (must match `checkout_store.py` exactly):

```python
# scripts/checkout/checkout_store_pg.py
async def load(job_id: str) -> Optional[dict]: ...
async def save(job_id: str, state: dict) -> None: ...
async def update(job_id: str, partial: dict) -> dict: ...
def read_order_list(job_id: str) -> dict: ...   # stays filesystem-backed — see §9.2.6
```

🔧 `save()` implementation maps to two table inserts: one row in `checkouts` (the quote-time row) and one row in `sagas` (the saga lifecycle row). The split mirrors the schema design from §9.2.1 — `checkouts` is the quote artifact, `sagas` is the run.

🔧 `update()` uses Pattern 2 from §9.2.5 (single-transaction read-modify-write with `SELECT ... FOR UPDATE`). The asyncpg.Lock that `checkout_store.py` uses today disappears entirely — Postgres' row-level lock handles it.

🔧 `load()` is a single `SELECT * FROM sagas WHERE job_id=$1 ORDER BY initiated_at DESC LIMIT 1` joined to the latest checkouts row. Returns the merged dict.

🔧 `read_order_list()` stays **unchanged** — `order_list.json` is an artifact written by the mosaic pipeline (see §9.2.6). Importable from `checkout_store_pg.py` directly: `from .checkout_store import read_order_list`.

**Step 2 — Add the dispatcher** (≈ 30 min)

🔧 New file `scripts/checkout/checkout_store_dispatch.py`:

```python
"""Resolves checkout_store to the correct backend at import time.

DB_BACKEND=json (default): re-exports the existing checkout_store module.
DB_BACKEND=postgres:       re-exports checkout_store_pg.

The dispatch happens ONCE at import time. Lifespan reads env once; the rest
of the codebase imports from this module so callers don't branch.
"""
import os

if os.environ.get("DB_BACKEND", "json").lower() == "postgres":
    from .checkout_store_pg import load, save, update, read_order_list  # noqa: F401
else:
    from .checkout_store import load, save, update, read_order_list  # noqa: F401
```

🔧 Update every caller to import from the dispatcher instead of the concrete module:

- `scripts/checkout/saga.py` — change `from . import checkout_store` to `from . import checkout_store_dispatch as checkout_store`.
- `scripts/checkout/router.py` — same change.
- `scripts/checkout/debug_router.py` — same change.

✅ Verify: `grep -rn "from .checkout_store import\|from . import checkout_store" scripts/checkout/` returns only the dispatcher line.

📦 **Commit checkpoint 1:** "Phase C step 1-2 — checkout_store_pg.py added (no callers yet); dispatch module routes via DB_BACKEND."

**Step 3 — B23 partial unique index race handling** (≈ 1 hour)

Today's B23 fix is application-level (router checks for an active saga before insert). With the partial unique index `sagas_one_active_per_job_idx` enforcing at the DB layer, the application check becomes redundant AND a UniqueViolationError needs to translate to the existing 422 response.

🔧 In `scripts/checkout/router.py` (or wherever the saga INSERT lives in `checkout_store_pg.save`):

```python
from asyncpg.exceptions import UniqueViolationError

try:
    await checkout_store.save(job_id, initial_state)
except UniqueViolationError as exc:
    # B23: another non-terminal saga already exists for this job_id.
    # The partial unique index enforces this at the DB level — the previous
    # application-level check is redundant in DB_BACKEND=postgres mode but
    # left in place for DB_BACKEND=json compatibility.
    raise HTTPException(status_code=422, detail={
        "error": "An active checkout already exists for this job_id",
        "code": "ACTIVE_CHECKOUT_EXISTS",
    })
```

🔧 The application-level check stays in place for `DB_BACKEND=json` (where there's no DB index to enforce it). Both paths produce the same 422 to the customer. The two implementations converge after Phase F's JSON-path deletion.

**Step 4 — Per-job advisory lock for write coordination** (≈ 2 hours)

Today: `checkout_store._locks: dict[str, asyncio.Lock]` is acquired inside `update()`. Works for one FastAPI process. Breaks for multiple.

With Postgres: drop `_locks` entirely from `checkout_store_pg.py`. Use `pg_advisory_xact_lock(hashtext($1))` inside the transaction. Pattern 3 from §9.2.5 is the template.

🔧 Inside `checkout_store_pg.update()`:

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", job_id)
        # Now this transaction is the only one mutating this job_id's saga.
        row = await conn.fetchrow("SELECT * FROM sagas WHERE job_id=$1 FOR UPDATE", job_id)
        ...  # merge partial, UPDATE, return merged dict
```

✅ Verify: stress-test locally with 100 concurrent `update()` calls for the same `job_id`. All should serialize cleanly; no `UniqueViolationError`, no lost updates.

📦 **Commit checkpoint 2:** "Phase C step 3-4 — B23 enforced by partial unique index; pg_advisory_xact_lock replaces _locks for DB backend."

**Step 5 — Test against the Neon `dev` branch end-to-end** (≈ 4 hours)

🛑 **You need the Neon `dev` branch by this point.** If you haven't created it, do it now (`neon branch create dev --parent main`). Save its **pooler** DSN into `.env.local` (gitignored — confirm with `git status` after touching it). Phase C is the first phase that requires local Postgres iteration speed.

💻 Local run with Postgres backend:

```bash
$env:DB_BACKEND = "postgres"
$env:DATABASE_URL = "postgresql://...dev-pooler.../neondb?sslmode=require"
uvicorn Main:app --reload
```

✅ Smoke test the saga flow via Swagger UI at http://127.0.0.1:8000/docs:
1. `POST /generate` with an image → wait for job complete.
2. `POST /jobs/{job_id}/checkout/quote` → confirm 200 with allocation breakdown.
3. `POST /jobs/{job_id}/checkout/confirm` → confirm 200 with checkout_id, saga starts.
4. `GET /jobs/{job_id}/checkout/{checkout_id}/status` → poll until saga_status reaches a terminal state.
5. In Neon SQL editor: `SELECT * FROM sagas WHERE job_id=$1`. Verify state matches what `/status` returned.

🛑 **CRITICAL:** the saga itself MUST be in TEST mode for this — never run Phase C verification against `sk_live_` keys. The L0–L5 gate will refuse this automatically if not configured, but double-check `GET /checkout/gate` returns `mode=test`.

**Step 6 — Verify B23 enforcement works in practice** (≈ 30 min)

✅ Race-test B23 with two concurrent `/confirm` calls for the same `job_id` from two different checkout_ids:

```bash
# Generate quote A, generate quote B (both for same job_id, different checkout_ids).
# Then fire both confirms back-to-back. Expected:
# - First /confirm: 200, saga starts.
# - Second /confirm: 422 with ACTIVE_CHECKOUT_EXISTS (NOT a 500).
```

If the second `/confirm` returns 500 or wins the race, the partial unique index isn't doing its job — check the index exists with `\d sagas` in psql.

📦 **Commit checkpoint 3:** "Phase C step 5-6 — end-to-end saga verified against Neon dev branch; B23 race-tested."

**Phase C exit criteria:**
- ✅ `checkout_store_pg.py` has the full public API.
- ✅ Dispatcher routes via `DB_BACKEND` at import time.
- ✅ End-to-end saga in TEST mode runs cleanly against Neon dev branch.
- ✅ `_locks` dict is gone from `checkout_store_pg.py`; advisory locks replace it.
- ✅ B23 race produces 422, not 500.
- ✅ Phase C git branch merged to git `main`.
- 📦 §0 + §9.6 updated.

**Phase C non-goal:** The mosaic pipeline (`Main.py` `app.state.jobs` etc.) stays JSON-backed at the end of Phase C. That's Phase D's job. The split lets us validate the simpler half (checkout) first.

---

#### 9.5.D Phase D — Mosaic job lifecycle to Postgres (≈2 days)

**Goal:** `app.state.jobs / queue_order / active_jobs / jobs_lock / progress_lock / queue_lock` go away. Replaced by `jobs` and `job_progress` table rows + Postgres locks. Worker subprocess unchanged (still writes `.progress` file; scheduler thread mirrors to DB).

**Prerequisites:**
- ✅ Phase C complete (checkout side validated; the DB path is no longer hypothetical).
- 🛑 **Fresh git branch:** `git checkout -b phase-d-mosaic-lifecycle-pg`.

**Step 1 — Add `jobs_store_pg.py` mirroring `Main.py`'s in-memory shape** (≈ 6 hours)

Today `Main.py` mutates `app.state.jobs[job_id] = {...}` directly. Centralize all access through a new module:

🔧 New `scripts/jobs_store_pg.py`:

```python
async def insert_job(job_id: str, image_path: str, settings: dict) -> None: ...
async def get_job(job_id: str) -> Optional[dict]: ...
async def list_queued() -> list[dict]: ...                    # for scheduler
async def list_running() -> list[dict]: ...                   # for active_jobs count
async def mark_running(job_id: str) -> None: ...
async def mark_complete(job_id: str, manifest: dict) -> None: ...
async def mark_failed(job_id: str, error: str) -> None: ...
async def mark_timed_out(job_id: str) -> None: ...
async def write_progress(job_id: str, pct: float) -> None: ...
async def cleanup_expired() -> list[str]: ...                 # returns job_ids whose dirs to rm
```

🔧 Each function uses Pattern 2 (single-tx RMW with `FOR UPDATE` on the row). `cleanup_expired()` runs a single DELETE...RETURNING to get the deleted job_ids, then the cleanup thread `shutil.rmtree(outputs/{job_id})` outside the transaction.

**Step 2 — Add `jobs_store_dispatch.py`** (≈ 30 min)

Mirror Phase C's dispatcher pattern:

🔧 New `scripts/jobs_store_dispatch.py`:

```python
import os
if os.environ.get("DB_BACKEND", "json").lower() == "postgres":
    from .jobs_store_pg import *  # noqa: F401, F403
else:
    from .jobs_store_json import *  # noqa: F401, F403  ← create this as a wrapper around app.state.jobs
```

🔧 Extract today's `app.state.jobs` mutations from `Main.py` into a new `scripts/jobs_store_json.py` module with the SAME public API as `jobs_store_pg.py`. This is the larger refactor.

**Step 3 — Worker progress file → DB scheduler poll** (≈ 2 hours)

Today the worker subprocess writes `.progress` files; the main process reads them on demand. With the DB backend, the scheduler thread reads `.progress` and mirrors to `job_progress` (or a `progress_pct` column on `jobs`).

🔧 Scheduler thread tick (every loop iteration):

```python
for running_job in await jobs_store.list_running():
    pct = read_progress_file(running_job["job_id"])
    if pct is not None and pct != running_job["progress_pct"]:
        await jobs_store.write_progress(running_job["job_id"], pct)
```

🔧 Worker stays untouched. It doesn't need a DB connection — that's the cleaner design and avoids putting asyncpg into the subprocess (see §9.3.11.10).

**Step 4 — Cleanup thread** (≈ 1 hour)

🔧 Replace `app.state.jobs` iteration + manual TTL check with:

```python
expired_job_ids = await jobs_store.cleanup_expired()
for jid in expired_job_ids:
    shutil.rmtree(OUTPUT_DIR / jid, ignore_errors=True)
```

**Step 5 — Queue draining** (≈ 1 hour)

Today: `queue.Queue` decouples HTTP intake from the executor. With DB backend, the scheduler queries `SELECT job_id FROM jobs WHERE status='queued' ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED` to safely dequeue across multiple workers.

🔧 Scheduler tick (DB backend):

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        row = await conn.fetchrow("""
            SELECT job_id FROM jobs WHERE status='queued'
            ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED
        """)
        if row is None:
            return  # nothing to do
        await conn.execute("UPDATE jobs SET status='running', started_at=NOW() WHERE job_id=$1", row["job_id"])
# Submit to executor outside the transaction
app.state.executor.submit(run_job, row["job_id"], ...)
```

`FOR UPDATE SKIP LOCKED` is the multi-worker-safe pattern — if worker A grabbed the row, worker B's query skips it without blocking.

**Step 6 — End-to-end test** (≈ 2 hours)

✅ With `DB_BACKEND=postgres`, run a full mosaic generation + checkout against Neon dev branch. Verify:
- `jobs` row created on `POST /generate`.
- Status transitions visible via `SELECT status, ... FROM jobs WHERE job_id=$1`.
- `job_progress` updates visible.
- After cleanup TTL: row deleted AND output dir removed.

📦 **Commit checkpoint:** "Phase D — mosaic lifecycle on Postgres; queue + progress + cleanup migrated."

**Phase D exit criteria:**
- ✅ `app.state.jobs / queue_order / active_jobs / *_lock` removed in `DB_BACKEND=postgres` path.
- ✅ Worker subprocess unchanged; scheduler mirrors `.progress` to DB.
- ✅ `FOR UPDATE SKIP LOCKED` proven to handle concurrent workers.
- ✅ Cleanup deletes both DB rows and output dirs.
- ✅ Phase D git branch merged to git `main`.
- 📦 §0 + §9.6 updated.

**Optional after Phase D:** raise `MAX_WORKERS` above 1. With Postgres-coordinated locks, multi-worker becomes safe. Memory requirement: ~500MB per worker (see CLAUDE.md CPU section). Render Standard tier or higher only.

---

#### 9.5.E Phase E — Resume-on-startup + reconciliation (≈1 day)

**Goal:** The behavior the entire migration exists for. Every restart routes orphaned sagas. A periodic task reconciles stripe holds against the live API. The L6 audit log (§2) finds its writer.

**Prerequisites:**
- ✅ Phases B+C+D complete (DB is authoritative for state).
- 🛑 **Fresh git branch:** `git checkout -b phase-e-resume-reconcile`.

**Step 1 — Implement `resume_in_flight_sagas`** (≈ 2 hours)

The reference implementation is in §9.2.7 verbatim. Copy it to `scripts/checkout/saga_resume.py`.

🔧 Wire into `Main.py` lifespan AFTER `init_pool()` and `verify_schema()`, BEFORE the executor + scheduler start:

```python
await init_pool()
await verify_schema()
if is_postgres_backend():
    from .checkout.saga_resume import resume_in_flight_sagas
    await resume_in_flight_sagas()
# … then payment registry, gate, executor, scheduler …
```

✅ Verify routing decisions for each saga_status case using SQL-injected test rows:

| Test setup | Expected outcome |
|---|---|
| `INSERT ... saga_status='initiated'` | `resume_in_flight_sagas` marks it FAILED with reason "abandoned by process restart" |
| `INSERT ... saga_status='stripe_held', payment_hold_id='pi_test_...'` | provider.cancel called; saga marked FAILED on success, MANUAL_REVIEW on cancel failure |
| `INSERT ... saga_status='orders_placed', brickowl_order_ids=['ord_1']` | marked MANUAL_REVIEW with full runbook in manual_review_reason |

🛑 **Use the Stripe TEST API for the `stripe_held` case.** Never resume-cancel against `sk_live_` keys during testing.

**Step 2 — Implement orphan-hold reconciliation task** (≈ 3 hours)

🔧 New `scripts/checkout/reconcile.py`:

```python
async def reconcile_orphan_holds() -> None:
    """Every 5 minutes: ask Stripe about old uncaptured holds; release stranded ones."""
    pool = get_pool()
    rows = await pool.fetch("""
        SELECT ph.hold_id, ph.amount_authorized_cents, ph.created_at,
               s.saga_status, s.checkout_id, s.job_id
        FROM payment_holds ph
        LEFT JOIN sagas s ON s.payment_hold_id = ph.hold_id
        WHERE ph.last_known_status = 'requires_capture'
          AND ph.last_reconciled_at < NOW() - INTERVAL '1 hour'
        ORDER BY ph.created_at
    """)
    provider = payment_registry.get_active()
    for row in rows:
        try:
            await _reconcile_one(row, provider, pool)
        except Exception as exc:
            logger.error(f"reconcile: hold {row['hold_id']} failed: {exc}")
```

Decision matrix inside `_reconcile_one`:

| Saga status | Stripe status | Action |
|---|---|---|
| terminal (`payment_captured`, `compensated`, `failed`, `manual_review`) | `requires_capture` | Stripe still has the hold — cancel it; update payment_holds last_known_status |
| `stripe_held` AND saga older than 1hr (saga itself orphaned, didn't restart-recover) | `requires_capture` | mark saga MANUAL_REVIEW (something prevented resume from running); leave Stripe alone for operator |
| any | `succeeded` (already captured by us) | update payment_holds.last_known_status='succeeded'; no action |
| any | `canceled` | update payment_holds.last_known_status='canceled'; no action |
| any | error from Stripe (network, API key issue) | log + retry next tick |

🔧 Start the task from `Main.py` lifespan after the scheduler is up, holding a strong reference per the C1/B24 pattern:

```python
import asyncio

_reconcile_task: Optional[asyncio.Task] = None

if is_postgres_backend():
    async def _reconcile_loop():
        while True:
            await asyncio.sleep(300)
            try:
                await reconcile_orphan_holds()
            except Exception as exc:
                logger.error(f"reconcile loop iteration failed: {exc}")
    _reconcile_task = asyncio.create_task(_reconcile_loop())  # strong reference
```

Same GC-resilience pattern as `_running_sagas` (C1) and `_sweeper_task` (B24). Don't lose the reference.

**Step 3 — Implement L6 `audit.emit`** (≈ 3 hours)

This is L6 from §2. It's been deferred until Postgres exists; Phase E is the right moment.

🔧 New `scripts/checkout/audit.py` per the §2.1 envelope schema:

```python
async def emit(event: str, *, request_id: Optional[str] = None,
               actor: Optional[dict] = None, subject: Optional[dict] = None,
               data: Optional[dict] = None) -> None:
    """Write an audit event to audit_events table. Never raises — failures
    log CRITICAL and are swallowed (audit must never fail the request)."""
    try:
        pool = get_pool()
        await pool.execute("""
            INSERT INTO audit_events (event, ts, request_id, job_id, checkout_id, actor, data)
            VALUES ($1, NOW(), $2, $3, $4, $5::jsonb, $6::jsonb)
        """,
        event, request_id,
        (subject or {}).get("job_id"),
        (subject or {}).get("checkout_id"),
        json.dumps(actor or {}),
        json.dumps(data or {}))
    except Exception as exc:
        logger.critical(f"AUDIT EMIT FAILED for event={event}: {exc}", exc_info=True)
```

🔧 Replace the `logger.warning(...)` call in `dependencies.require_checkout_gate_open` with `audit.emit("gate.confirm_rejected", ...)` per the CLAUDE.md design note. This is the first call site; the rest of §2.2's event vocabulary gets wired one event at a time over the following commits (track in `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §2` checklist).

**Step 4 — End-to-end recovery test** (≈ 2 hours)

✅ Force a restart mid-saga and verify recovery:
1. Submit a `/confirm` in TEST mode → saga reaches `stripe_held` state.
2. While the saga is in `stripe_held`, kill the uvicorn process (`Ctrl+C` or `taskkill /F /IM python.exe`).
3. Restart `uvicorn Main:app`.
4. Lifespan boot should log `[resume] examined 1 in-flight sagas`.
5. `SELECT * FROM sagas WHERE checkout_id='...'` should show `saga_status='failed'` with reason "Resumed after restart; hold released".
6. Stripe Dashboard → Payments → the test hold should show `canceled`.

📦 **Commit checkpoint:** "Phase E — saga resume + orphan reconciliation + L6 audit emit shipped."

**Phase E exit criteria:**
- ✅ `resume_in_flight_sagas()` routes every documented saga_status.
- ✅ `reconcile_orphan_holds()` runs every 5min, held by strong reference, idempotent.
- ✅ `audit.emit()` works against `audit_events`; first call site (`gate.confirm_rejected`) wired.
- ✅ Mid-saga restart test passes.
- ✅ Phase E git branch merged to git `main`.
- 📦 §0 + §9.6 updated.

---

#### 9.5.F Phase F — Cutover (≈½ day, plus 1-week observation window)

**Goal:** Flip the production switch. `DB_BACKEND=postgres` on Render. Validate. Wait one week. Delete the JSON code path.

**Prerequisites:**
- ✅ Phases B+C+D+E complete on the git `main` branch.
- ✅ Render env has `DATABASE_URL` set (Neon `main` pooler DSN).
- ✅ Render env has `DATABASE_URL_DIRECT` set (Neon `main` direct DSN, for alembic).
- ✅ Render pre-deploy hook configured per §9.5.B Step 6.
- 🛑 **Schedule the cutover during low-traffic hours.** Pre-launch this is moot, but make it a habit.
- 🛑 **Upgrade Neon project to Launch tier ($19/mo) in the same window.** Free tier autosuspend would cold-start the first `/status` poll of the day. Per §9.4 step 7.

**Step 1 — Deploy with `DB_BACKEND=json` first** (≈ 15 min)

🛑 Critical sequencing: do NOT flip `DB_BACKEND` and deploy at the same time.

💻 In Render dashboard, deploy the current git `main` (which has Phases B-E code AND defaults to `DB_BACKEND=json`). This proves the new code coexists with the old behavior. The DB pool initializes (pre-deploy hook runs `alembic upgrade head`), but no app code reads from it.

✅ Verify on the live URL:
- `GET /health` returns 200.
- `GET /` returns the existing landing.
- `POST /generate` with a test image succeeds (mosaic pipeline still JSON-backed).
- `GET /checkout/gate` returns `mode=test` (assuming TEST keys; live keys would be `mode=live`).

**Step 2 — Smoke test the DB pool is open even though unused** (≈ 5 min)

💻 SSH/Render shell: `psql $DATABASE_URL -c "SELECT 1"` from a Render shell tab (if accessible). Otherwise: trust the lifespan log line `"DB pool initialized (Neon Postgres backend active)"` — wait, that log line only appears when `DB_BACKEND=postgres`. With `DB_BACKEND=json`, the line is `"DB pool skipped (DB_BACKEND=json; Phase F not yet flipped)"`.

✅ Confirm the "DB pool skipped" log line appears. That confirms `DB_BACKEND=json` is the active path.

**Step 3 — Flip the switch** (≈ 5 min)

💻 Render dashboard → laigo service → Environment → `DB_BACKEND=postgres`. Save. Render restarts the service.

✅ Watch the deploy logs:
- `"DB pool initialized (Neon Postgres backend active)"` ← MUST appear
- `verify_schema()` success ← MUST appear (proves alembic_version matches)
- `[resume] examined 0 in-flight sagas` ← expected (empty DB; pre-launch)
- Service comes up healthy.

🛑 **If anything in the lifespan errors,** flip `DB_BACKEND=json` immediately, redeploy, investigate offline. The deploy halts on lifespan failure — Render will report the service as unhealthy.

**Step 4 — End-to-end production smoke** (≈ 30 min)

✅ Run the full flow against the live URL (still TEST mode, no real money):
1. `POST /generate` with a real image → job completes.
2. `POST /jobs/{job_id}/checkout/quote` → 200 with allocation.
3. `POST /jobs/{job_id}/checkout/confirm` → 200, saga starts.
4. `GET /jobs/{job_id}/checkout/{checkout_id}/status` → poll to terminal.
5. Neon SQL editor: `SELECT * FROM jobs WHERE job_id='...'` and `SELECT * FROM sagas WHERE job_id='...'`. Confirm rows exist.

**Step 5 — Upgrade to Neon Launch tier** (≈ 5 min, in same window)

💻 Neon dashboard → laigo project → Billing → upgrade to Launch ($19/mo). Confirms autosuspend OFF, multi-branch limit raised.

✅ Verify the project status badge changes to "Launch."

**Step 6 — One-week observation window** (calendar)

🛑 **Do NOT delete the JSON code path yet.** Leave the dispatchers + `checkout_store.py` / `jobs_store_json.py` in place. Rollback to JSON must remain a single-env-var flip for one week.

Daily for 7 days:
- Check `SELECT COUNT(*) FROM sagas WHERE saga_status='manual_review' AND last_transition_at > NOW() - INTERVAL '1 day'`.
- Check Render error log for any `verify_schema` mismatches or pool exhaustion.
- Check Neon dashboard for slow queries or connection-cap warnings.

If any of those flare: roll back to `DB_BACKEND=json`, file the issue, fix offline, re-attempt cutover.

**Step 7 — Delete the JSON code path** (≈ 1 hour, after 7 clean days)

🔧 Once 7 consecutive days have passed without incident:
- Delete `scripts/checkout/checkout_store.py` (the JSON impl).
- Delete `scripts/checkout/checkout_store_dispatch.py` (dispatcher).
- Delete `scripts/jobs_store_json.py`.
- Delete `scripts/jobs_store_dispatch.py`.
- Rename `scripts/checkout/checkout_store_pg.py` → `scripts/checkout/checkout_store.py`.
- Rename `scripts/jobs_store_pg.py` → `scripts/jobs_store.py`.
- Update all import sites to drop the dispatch layer.
- Remove the `DB_BACKEND` env var from `.env` and Render config.
- Remove `is_postgres_backend()` from `scripts/db.py` (no longer needed; pool is always required).
- Remove `init_pool()`'s no-op-when-json branch.
- Update CLAUDE.md to drop `DB_BACKEND` from the config table.

🔧 Update `Main.py` lifespan to require pool on boot (no graceful skip).

📦 **Commit checkpoint (separate commit, ≥7 days after Step 3):** "Phase F cleanup — delete DB_BACKEND dispatcher, JSON backend, no-op branches."

**Phase F exit criteria:**
- ✅ Live service running on `DB_BACKEND=postgres` for ≥7 days.
- ✅ Neon Launch tier active.
- ✅ JSON path deleted in a follow-up commit.
- ✅ `DB_BACKEND` env var removed everywhere.
- 📦 §0 status updated to "Phase 9 complete."
- 📦 Project memory file updated.
- 📦 Audit FMEA #3 (saga crashes mid-flight, RPN 450) closed.

**Phase F rollback (any time before Step 7):**
- Render dashboard → set `DB_BACKEND=json` → restart. The JSON path resumes immediately. Any rows already in Postgres are orphaned (pre-launch, this is fine — recreate the database from scratch on the next attempt).

---

### 9.6 Current progress dashboard

**Last updated:** 2026-05-16.
**Single-pane status:** everything-you-need-to-know in one table. Update this when you complete a phase OR a sub-step.

#### 9.6.1 Phase-by-phase status

| Phase | Status | Owner | Notes |
|---|---|---|---|
| A — Provision & connect | ✅ Shipped 2026-05-16 | Grant | Neon `laigo` / PG 17.8 / us-east-1 / ARM64. Pooler DSN in `.env.secrets`. `scripts/db.py` + `Main.py` lifespan + smoke test. asyncpg 0.31 + alembic 1.18 installed locally via `--trusted-host` workaround. Neon `dev` branch deferred to Phase C. |
| B — Schema + alembic | 🟦 Ready to start | Grant | All prerequisites met. Estimated 1 engineer-day. Playbook: §9.5.B. |
| C — Checkout state to Postgres | ⏸ Blocked by B | Grant | Estimated 2 days. Requires Neon `dev` branch (not yet created). Playbook: §9.5.C. |
| D — Mosaic job lifecycle to Postgres | ⏸ Blocked by C | Grant | Estimated 2 days. Playbook: §9.5.D. Optional `MAX_WORKERS>1` follow-up requires Render Standard tier. |
| E — Resume-on-startup + reconciliation | ⏸ Blocked by D | Grant | Estimated 1 day. The actual point of the migration. L6 audit emit ships here. Playbook: §9.5.E. |
| F — Cutover | ⏸ Blocked by E | Grant | ≈½ day for the flip + 1 week observation + 1 hour cleanup. Triggers Neon Free→Launch tier change. Playbook: §9.5.F. |

#### 9.6.2 Open user actions (operator decisions / external work)

| # | Action | Phase trigger | Blocker level |
|---|---|---|---|
| U1 | Resolve corporate-proxy SSL cert root cause (so `pip install` works without `--trusted-host`) | Eventually | LOW — `--trusted-host` is a working bypass for now |
| U2 | Create Neon `dev` branch (Neon dashboard → Branches → New Branch off `main`) | Phase C | MEDIUM — Phase C cannot run locally without it (Phase B doesn't need it) |
| U3 | Configure Render pre-deploy hook for `alembic upgrade head` | Phase F | MEDIUM — Phase F cannot proceed without it |
| U4 | Add `DATABASE_URL` + `DATABASE_URL_DIRECT` env vars to Render | Phase F | MEDIUM — Phase F prerequisite |
| U5 | Upgrade Neon project Free → Launch ($19/mo) | Phase F (in same window as flag flip) | HIGH at cutover — autosuspend would cold-start the first customer poll |
| U6 | Commit packaging strategy for Phase A scaffolding (no commits made yet during entire L0-L5 + Phase A work) | Whenever | LOW — work is local and reversible until committed |

#### 9.6.3 Defect catalog interaction (which defects this migration resolves)

The DB migration is the structural fix for several open defects. Phase rows show when each defect lands:

| Defect | Resolved by | Mechanism |
|---|---|---|
| S1 (persistent state needed) | Phase C+D | Postgres replaces all in-memory state |
| B11 (per-iteration saga history) | Phase C (optional — JSONB column on `sagas`) | `iterations` column accumulates per-iteration state if/when wanted |
| B23 (same-job re-confirm race) | Phase C | `sagas_one_active_per_job_idx` partial unique index enforces at DB level |
| B26 (orphaned saga state files) | Phase F | JSON files deleted entirely; rows have explicit TTL via cleanup |
| B35 (non-atomic JSON file writes) | Phase F | JSON files gone |
| B36 (semaphore-per-call concurrency) | Phase D+ | Once `MAX_WORKERS>1` is enabled, refactor to module-global semaphore is in scope |
| H6 (registry reset on shutdown — already shipped) | n/a | Shipped 2026-05-16, but DB-backed registry is a future-proofing option not pursued |
| H11 (saga state persistence beyond restart) | Phase E | resume_in_flight_sagas + reconcile |
| H13 (orphan-hold reconciliation) | Phase E | reconcile_orphan_holds task |
| H15 (audit log persistence) | Phase E | audit.emit writes to audit_events |
| Audit FMEA #3 (saga crashes mid-flight, RPN 450) | Phase E | resume_in_flight_sagas |
| Audit FMEA #5 (no operator dashboard) | Phase D+E | `docs/operator_sql.md` queries become possible |
| Audit FMEA #16 (no reconciliation surface) | Phase E | reconcile_orphan_holds |
| Audit FMEA #17 (orphan Stripe holds) | Phase E | reconcile_orphan_holds |

#### 9.6.4 Risk register for in-flight migration

| Risk | Phase | Mitigation already in place | Mitigation still needed |
|---|---|---|---|
| Schema bug discovered after deploy | B → F | Throwaway Neon branch test before applying to `main` | Hot-fix migration (forward-only) preferred over rollback |
| Pool exhaustion under load | D+ | `max_size=10` headroom; `command_timeout=10s` | Bump to 20 if `MAX_WORKERS>1`; alert on `PoolExhausted` |
| Neon outage during cutover | F | Pre-launch — no customer impact | Eventually: documented incident response |
| `--trusted-host` install becomes habit | n/a | Documented as one-shot bypass | U1: solve root cert problem |
| Engineer forgets `ALEMBIC_DATABASE_URL` setup | B-F | Documented in §9.5.B Step 3 + env.py error message | Shell alias / helper script |
| `DB_BACKEND` flag left at `postgres` locally without DSN | C+ | `init_pool()` refuses boot with clear error | None additional needed |

#### 9.6.5 What to read next when picking this up

If you're returning to this work cold:

1. Read the Terminology block at the top of §9 (Neon branch vs git branch).
2. Read §9.6.1 to see where you are.
3. Open the playbook for the next non-shipped phase (§9.5.B, etc.).
4. Resolve any open user actions in §9.6.2 that block the next phase.
5. Begin.

Do not skip step 1 even on a quick return — the branch terminology is the single most common source of confusion in this migration.
