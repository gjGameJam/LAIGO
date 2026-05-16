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
| Postgres-backed state + resume-on-restart | ❌ Not built — host = **Neon** (locked 2026-05-16; §9.3.11.1); 6 tables; 6.5 engineer-days planned | §3 roadmap item 2 / §9 |
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
| Undocumented bugs B6–B12, B15, B16, B23, B24, B25, B26 | ❌ Open | §4 |

**Hard launch gate:** items §3 #1–#7, plus B1–B5 in §4, must be resolved before any real Stripe key is configured.

**Phase 3.4 (B12 — customer-facing error translation) is paused** mid-implementation. Resuming this is the next planned work item; the audit doc in this file's §4 entry for B12 is the authoritative spec.

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

### B6 — Stripe key length rule disagreement between gate and provider — **MEDIUM**

**File:line:** `scripts/checkout/gate.py:108-118` vs `scripts/checkout/payment/stripe_provider.py:103-114`

**Symptom:** `gate._stripe_key_mode()` accepts `sk_test_` / `sk_live_` with *any* suffix length. `stripe_provider._key_mode()` requires ≥ 8 chars beyond the prefix (the D3 fix). So `STRIPE_SECRET_KEY=sk_live_` (bare prefix) produces:
- **`RENDER` unset:** gate fires the "live key outside Render" reason. Registry empty because provider refused construction. Both reasons present.
- **`RENDER=true`:** gate classifies key as "live", checks Render → passes. Registry still empty. Single reason: "No payment provider registered."

Two different reason-string outputs for the same bad config.

**Why it's not in the audit:** D3 was added inline during L5 review; the gate's older `_stripe_key_mode` was not updated to match.

**Fix:** Either import `_key_mode` from `stripe_provider` (or extract a shared helper module) so both layers agree on what counts as a valid key.

```python
# gate.py — replace local _stripe_key_mode with:
from .payment.stripe_provider import _key_mode as _stripe_key_mode
```

(Or move both helpers to a new `scripts/checkout/payment/key_format.py` to avoid the gate importing a provider implementation detail.)

**Effort:** 15 min.

**Launch-blocking?** No. Both paths end DISABLED, just with different reason text.

---

### B7 — Currency read at Saga runtime, not at provider construction — **MEDIUM**

**File:line:** `scripts/checkout/saga.py:772` (current, post-Phase-1/2; originally `:228` pre-Phase-1)

**Symptom:**
```python
hold = await provider.create_hold(
    ...
    currency=os.environ.get("STRIPE_CURRENCY", "usd"),
    ...
)
```
The currency is read fresh on every hold. If env mutates between hold and capture (unlikely in production but possible after restart-while-Saga-in-flight), the capture call could target a different currency. Stripe rejects → PaymentPermanentError → MANUAL_REVIEW. The failure mode is "your env changed mid-Saga," difficult to diagnose.

Also: no whitelist. `STRIPE_CURRENCY=zzz` passes through to Stripe and gets refused only on first call. The provider doesn't surface this misconfiguration at boot.

**Why it's not in the audit:** L5 design didn't surface currency as a config concern; the StripeProvider accepts whatever caller passes.

**Fix:** Read once in `StripeProvider.__init__`, validate against an allowlist, expose as `provider.currency`.

```python
# stripe_provider.py
_ALLOWED_CURRENCIES = frozenset({"usd", "eur", "gbp", "cad"})

def __init__(self) -> None:
    ...
    currency = os.environ.get("STRIPE_CURRENCY", "usd").strip().lower()
    if currency not in _ALLOWED_CURRENCIES:
        raise PaymentProviderUnavailable(
            f"STRIPE_CURRENCY={currency!r} not in {sorted(_ALLOWED_CURRENCIES)}"
        )
    self.currency: Final = currency
```

Saga drops the env read entirely and uses `provider.currency`.

**Effort:** 20 min.

**Launch-blocking?** No (default "usd" is correct), but tightens the contract.

---

### B8 — LEGO.com stockouts are not retryable — **MEDIUM**

**File:line:** `scripts/checkout/saga.py:999-1013` (current, post-Phase-1/2/B19; originally `:374-386` pre-Phase-1). Look for the LEGO branch inside `_execute_checkout_saga_inner`'s `while True:` body — specifically the `try: lego_order_id = await lego_client.order_from_lego(...)` and its `except Exception:` arm.

**Symptom:** The Saga's retry loop only handles `StockoutError` from BrickOwl. Any failure in `lego_client.order_from_lego` — including a LEGO.com stockout — falls through to the generic `except Exception:`, triggering full compensation and FAILED. The Saga has zero recovery path for "LEGO.com showed an out-of-stock UI for one piece out of 200."

**Why it's not in the audit:** §1.2 mentions "LEGO.com has no stockout retry path at all" but flags it as a problem in the original code. Post-L5 the retry loop did get added — but only for BrickOwl. LEGO.com's status is unchanged.

**Impact:** Compensation cancels every BrickOwl order placed in the same Saga and refunds the customer. Customer must restart, eating the latency of a fresh quote + the retried Playwright session. Marketplace shipping fees on the cancelled BrickOwl orders are lost.

**Fix (option A — symmetric):** Make `lego_client.order_from_lego` raise a `StockoutError` when its Playwright flow detects the LEGO.com stockout UI. Add the LEGO path to the same retry loop. This requires reliable DOM detection in Playwright, which is fragile but doable.

**Fix (option B — document the asymmetry):** Add an explicit `except StockoutError as e:` branch above the generic Exception handler for the LEGO path, explaining that LEGO is the primary source and a stockout there indicates total unavailability (re-routing to LEGO again wouldn't help). Make the failure mode customer-visible and accept compensation.

**Effort:** Option A 1 day; Option B 30 min.

**Launch-blocking?** No, but option B documentation should land before launch so operators can explain customer complaints.

---

### B9 — Stockout retry only invalidates BrickOwl listings cache — **MEDIUM**

**File:line:** `scripts/checkout/saga.py:959` (current, post-Phase-1/2; originally `:339` pre-Phase-1). The `await cache_delete(f"brickowl_listings:{stockout_eid}")` line inside the stockout-retry branch of `_execute_checkout_saga_inner`.

**Symptom:**
```python
await cache_delete(f"brickowl_listings:{stockout_eid}")
```
The re-fetch at lines 348-355 hits all three marketplaces, but only BrickOwl's cache entry for that element was invalidated. If LEGO.com or BrickLink data is also stale (and they share a 1-hour TTL), the re-optimization can keep selecting the same listing that was just out-of-stock at one source and stale at another. The retry loop chews through retries on cached-stale data until `retries_left` exhausts.

**Why it's not in the audit:** The audit precedes the stockout-retry path's current implementation.

**Fix:** Invalidate all cached listings for the stockout element across every marketplace:
```python
for prefix in ("brickowl_listings", "lego_raw", "bricklink_listings"):
    await cache_delete(f"{prefix}:{stockout_eid}")
```

Even better: have the `cache.py` module expose `cache_delete_listing(element_id)` that knows about all listing keys.

**Effort:** 20 min.

**Launch-blocking?** No, but worsens latency on stockout-prone elements.

---

### B10 — Cache key construction is duck-typed across modules — **MEDIUM**

**File:line:** `scripts/checkout/saga.py:959` (current, post-Phase-1/2; originally `:339` pre-Phase-1) and wherever `brickowl_client.get_all_listings` stores cache entries

**Symptom:** The Saga constructs `f"brickowl_listings:{stockout_eid}"` to invalidate; the actual key is decided inside `brickowl_client.get_all_listings`. If those two strings ever drift, the delete is a silent no-op and the next retry re-fetches the cached (stockout) data → effectively infinite stockout retries until `retries_left` decrements to zero. The retry loop *looks* like it's doing work but is just hitting stale cache.

**Why it's not in the audit:** No documentation in audit; this is a hidden coupling between Saga and client.

**Fix:** Expose `brickowl_client.invalidate_listing(element_id)`. The cache key lives in exactly one place. Same for `lego_client`, `bricklink_client`.

**Effort:** 30 min.

**Launch-blocking?** No, but a one-character typo here causes a non-obvious customer-visible failure.

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

### B12 — Provider exception text leaks into customer-facing `error` field — **LOW (security-adjacent)**

**File:line:** Throughout `scripts/checkout/saga.py` (e.g. `:238`, `:251`, `:261`, `:541`)

**Symptom:** `error: f"Payment hold failed (permanent): {exc}"` formats may include Stripe internal reasons, PaymentIntent IDs, request IDs, or in pathological cases pieces of stack traces. `GET /status` surfaces this verbatim to the customer's frontend.

**Why it's not in the audit:** §5.3 documents the general "no customer-facing translation" issue. The L5 changes added a dozen new sites with the same problem.

**Impact:** Information leak. Stripe IDs, internal request IDs, and exception type names visible to anyone who can read /status responses.

**Fix:** Two-tier error field:
- `error_internal`: full exception text. Operator-only via debug endpoint or audit log.
- `error_customer`: short, friendly, classification-based. Surfaced in `/status` response.

```python
ERROR_MESSAGES = {
    "payment_permanent": "Your payment method was declined. Please use a different card.",
    "payment_transient": "Our payment system is temporarily unavailable. Please retry shortly.",
    "marketplace_failure": "We couldn't complete one of your orders. Your card was not charged.",
    "manual_review": "Your order is being reviewed by our team. We'll email you within 24 hours.",
}
```

**Effort:** 2 hours.

**Launch-blocking?** No, but flag as launch-day hardening.

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

### B15 — `stripe.api_version` and `stripe.api_key` are module-global, not per-instance — **LOW (forward-looking)**

**File:line:** `scripts/checkout/payment/stripe_provider.py:168` (`stripe.api_key = key`) and `:172-174` (`stripe.api_version = api_version`)

**Symptom:** `stripe-python` stores BOTH the API key AND the API version on the `stripe` module, not on a client instance. If H2 ever moves to multi-active providers (or tests instantiate `StripeProvider` more than once with different keys/versions), the **last constructor wins** — even live PaymentIntent calls made through an "older" provider instance will go through the most-recently-set key.

Today's single-active design (enforced by `payment/registry.py` — see [B17](#b17--register-replaces-silently-with-only-a-warning--low--shipped-2026-05-16-phase-32)) hides this. The B17 replacement guard prevents *accidental* re-registration; the module-global side-effect of `StripeProvider.__init__` still happens on intentional replacement (`LAIGO_ALLOW_REGISTRY_REPLACE=1` test path).

**Why it's not in the audit:** L5 explicitly locked single-active for v1.

**Fix when multi-active is needed:**
- Migrate to `stripe.StripeClient(api_key=key)` (stripe-python ≥ 7.0 supports per-instance clients).
- Each `StripeProvider` holds its own client; module globals become irrelevant.
- The Saga's existing `provider = payment_registry.get_active()` flow does not change.

**Doc-only mitigation in code today:** add a comment above `stripe.api_key = key` referencing this entry so the constraint is visible at the modification site.

**Effort:** 2 min for the in-code comment; 1 day for the multi-active migration.

**Launch-blocking?** No.

---

### B16 — Sequential BrickOwl cancellation has no batching (two sites) — **LOW**

**File:line:** Two sites — easy to miss one:
1. `scripts/checkout/saga.py:413-421` — `_compensate()` Phase 1: cancel BrickOwl orders in reverse (LIFO undo)
2. `scripts/checkout/saga.py:937-957` — stockout-retry inline cancel loop inside `_execute_checkout_saga_inner`'s `while True:` body

Both await `brickowl_client.cancel_order(oid)` one at a time. The original audit entry only referenced site #1; site #2 was added later when the stockout-retry path matured.

**Symptom:** For a hypothetical 50-seller mosaic, either site runs 50 cancels in series. When the real Playwright-based BrickOwl cancel (roadmap #5) ships, each session is ~10-30s; total compensation could take 15-30 minutes — long enough for the customer to time out the polling UI before a terminal state lands.

**Fix (must cover BOTH sites or it's incomplete):**
```python
sem = asyncio.Semaphore(5)
async def _cancel_one(oid):
    async with sem:
        try:
            await brickowl_client.cancel_order(oid)
            return (oid, None)
        except Exception as exc:
            return (oid, str(exc))

results = await asyncio.gather(*[_cancel_one(o) for o in brickowl_order_ids])
```

**Implementation notes:**
- Site #1 needs a small reshape — currently appends to `outcome.brickowl_succeeded` / `outcome.brickowl_failed` inline; after parallelizing, fold the `results` list into the outcome at the end. Order of items in `outcome.brickowl_succeeded` no longer reflects LIFO; this is fine — the dataclass is a set of facts, not an ordered log.
- Site #2 must still abort to MANUAL_REVIEW if ANY cancel raises (current behavior — see line 945-957). Easy to get wrong when parallelizing: a failed-cancel in the middle of a gather() must still produce a single MANUAL_REVIEW write, not multiple.
- `Semaphore(5)` is a conservative starting point. BrickOwl rate limits are 600 req/min standard. Five concurrent Playwright sessions on a shared LAIGO buyer account is the more binding constraint — bump up only after testing.

**Effort:** 30 min for both sites + outcome reshape.

**Launch-blocking?** No (small orders today), but ship before the BrickOwl Playwright cancel (roadmap #5) goes live — sequential cancels at production order sizes are the difference between a 90-second compensation and a 30-minute one.

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
| B6 | Stripe key length disagreement | MEDIUM | No | 15 min |
| B7 | Currency read at Saga runtime | MEDIUM | No | 20 min |
| B8 | LEGO stockouts not retryable | MEDIUM | No (option B 30min OK) | 1 day / 30 min |
| B9 | Stockout retry invalidates only BrickOwl cache | MEDIUM | No | 20 min |
| B10 | Cache key duck-typed across modules | MEDIUM | No | 30 min |
| B11 | Destructive per-iteration checkpoint | MEDIUM | No (blocks roadmap #2) | 1 day |
| B12 | Exception text leaks to /status | LOW (security-adjacent) | No | 2 hr — **paused mid-Phase-3, top of resume queue** |
| B13 | Master-flag reason wording | LOW | No | ✅ Shipped 2026-05-16 (Phase 3.1) |
| B14 | /confirm 409 doesn't say in-flight vs done | LOW | No | ✅ Shipped 2026-05-16 (Phase 3.3) |
| B15 | stripe.api_version is module-global (api_key also global) | LOW | No | 2 min (doc-only — see entry for caveats) |
| B16 | Sequential BrickOwl cancels (two sites) | LOW | No | 30 min |
| B17 | `register()` replaces silently | LOW | No | ✅ Shipped 2026-05-16 (Phase 3.2) |
| B18 | checkout_id→job_id fallback | LOW | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B19 | Order placed but state write fails post-call | MEDIUM | No | ✅ Shipped 2026-05-16 |
| B20 | `_compensate` silently swallows state-load failure | MEDIUM | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B21 | Stripe cancel had no retry on transient errors | MEDIUM | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B22 | `_compensate` had no terminal-state precondition | LOW | No | ✅ Shipped 2026-05-15 (bundled with B3) |
| B23 | Concurrent `/confirm` with different `checkout_id` for same `job_id` clobbers in-flight state | MEDIUM | No (single-customer rare) | 30 min — see entry below |
| B24 | Cache sweeper task is GC-vulnerable (no strong reference) | LOW | No | 10 min |
| B25 | `_handle_saga_timeout` leaks non-terminal state if load() raises | MEDIUM | No (low likelihood, high severity) | 30 min |
| B26 | `checkout_store.update()` crashes on corrupted JSON; no recovery | LOW | No (subsumed by Postgres migration) | 30 min |

**Launch-blocking remaining: 0** — B3, B4, B5 all shipped. Phase 1 complete.
**Phase-3-shipped:** B13, B14, B17. **Phase-3-paused:** B12 (resume first). **Open:** B6–B11, B12, B15 (doc-only), B16, B23, B24, B25, B26.

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

### B24 — Cache sweeper task is GC-vulnerable (same class as the Saga task GC bug) — **LOW**

**File:line:** `scripts/checkout/cache.py:52-54` (`start_cache_sweeper`)

**Symptom:** `asyncio.create_task(_sweep_loop())` is fire-and-forget; the returned Task is dropped immediately. Python 3.11+ docs: "the event loop only keeps weak references to tasks. A task that isn't referenced elsewhere may be garbage collected at any time, even before it's done." This is the EXACT defect class that the router fixed with `_running_sagas` (audit C1).

**Likelihood:** low — the sweep loop is in `while True:` with an `asyncio.sleep(300)`, so it has an active stack frame most of the time, and the event loop holds the task while it's running. But during the first scheduling slot (before the first `await`) it's eligible for GC.

**Impact:** if collected, the cache stops sweeping. Expired entries are still evicted on read (via `cache_get`'s expiry check), so this is a slow leak — not a customer-visible failure — but unbounded growth of cold keys (e.g. completed quote IDs no one is polling for) eventually consumes memory.

**Fix:** mirror the `_running_sagas` pattern:
```python
_sweeper_task: asyncio.Task | None = None

def start_cache_sweeper() -> None:
    global _sweeper_task
    if _sweeper_task is not None and not _sweeper_task.done():
        return  # idempotent
    _sweeper_task = asyncio.create_task(_sweep_loop())
```

**Verification:** instrument with `gc.get_referrers` on the task object; confirm only the module global references it.

**Launch-blocking?** No.

---

### B25 — `_handle_saga_timeout` can leave state non-terminal if state-load fails — **MEDIUM**

**File:line:** `scripts/checkout/saga.py:539-548` (the state load at the top of the timeout handler)

**Symptom:**
```python
async def _handle_saga_timeout(job_id, checkout_id):
    try:
        state = await checkout_store.load(job_id) or {}
    except Exception as exc:
        logger.critical(f"[saga] [{checkout_id}] TIMEOUT handler could not load state: {exc}. ...")
        return  # ← BUG: no terminal write
```

The handler's own contract (saga.py:537) says: "The handler MUST always write a terminal state (or leave the existing terminal state in place)." The current code violates this in the load-failure branch.

**Impact:** if `checkout_store.load()` raises during timeout cleanup (JSON corruption, filesystem flap, disk full), the saga's checkpoint stays at whatever was last written (e.g. `stripe_held` or `orders_placed`). The inner asyncio task was already cancelled. `_running_sagas.discard()` fires from the done callback. Result:
- `/status` returns `saga_status=stripe_held` forever — frontend keeps polling, never gets a terminal state.
- Stripe authorization holds the customer's funds for up to 7 days with no automatic release.
- No operator-visible signal beyond the CRITICAL log line.

**Likelihood:** low — load() failures require a corrupted JSON file or filesystem error.
**Severity:** high (financial — customer money held with no recovery).

**Fix:** even when load fails, attempt a best-effort terminal write. Without state, we can't know what was placed, so MANUAL_REVIEW with a verbose reason is the only safe terminal:
```python
try:
    state = await checkout_store.load(job_id) or {}
except Exception as exc:
    logger.critical(f"[saga] [{checkout_id}] TIMEOUT handler could not load state: {exc}")
    try:
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.MANUAL_REVIEW,
            "manual_review_reason": (
                f"Saga timed out after {_SAGA_TIMEOUT_SECONDS}s AND state load failed "
                f"({exc}). Operator must: (1) check Stripe dashboard for any hold under "
                f"this job, (2) check BrickOwl and LEGO.com for any orders placed in the "
                f"last hour, (3) reconcile."
            ),
            "error": f"Timeout + state load failure: {exc}",
        })
    except Exception as write_exc:
        logger.critical(f"[saga] [{checkout_id}] Could not write fallback MANUAL_REVIEW: {write_exc}")
    return
```

**Verification:** monkey-patch `checkout_store.load` to raise; verify timeout handler writes MANUAL_REVIEW; verify if `update()` ALSO fails, two CRITICAL logs are emitted.

**Launch-blocking?** No (low likelihood) but ship before launch if Postgres migration is delayed — corrupted-JSON-on-disk is more likely with filesystem state than with a DB.

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

## 8. Hardening list (post-Phase-3)

Defects + latent landmines that remain after Phases 1/2/3 closed B1, B3, B4, B5, B13, B14, B17, B18–B22. Listed in implementation priority. Each entry: severity, location, what to do, how to verify, and **dependencies on other items** so the work can be batched coherently.

This list IS authoritative for "what comes next." Do not work from memory of an earlier list.

### Tier 1 — Customer-visible / security-adjacent (do first)

#### H1. B12 — Customer-facing error translation **[PAUSED — RESUME FIRST]**

**Why first:** Every other open defect is internal. B12 leaks operator-facing strings (Stripe error text, exception type names, internal gate reason strings, PaymentIntent IDs) to anyone polling `/status`. Customer-visible information leak.

**Scope:**
- Add `customer_message: Optional[str]` to `CheckoutStatusResponse` in `scripts/checkout/models.py`.
- Add `customer_message: str` to the state dict written by every saga path that sets `error` (saga.py has ~8 sites — gate-closed, provider-unavailable, hold-permanent, hold-transient, hold-unexpected, capture-failed, post-placement drift, timeout handler).
- Frontend renders `customer_message` only; `error` becomes operator-only.
- Translation table — single source of truth in `models.py`:
  ```python
  ERROR_MESSAGES = {
      "payment_permanent":   "Your payment method was declined. Please use a different card.",
      "payment_transient":   "Our payment system is temporarily unavailable. Please retry shortly.",
      "marketplace_failure": "We couldn't complete one of your orders. Your card was not charged.",
      "manual_review":       "Your order is being reviewed by our team. We'll email you within 24 hours.",
      "drift_buffer":        "The price of your order changed. Please request a new quote.",
      "gate_closed":         "Checkout is temporarily unavailable. Please try again shortly.",
      "timeout":             "Your order took longer than expected. Our team is reviewing — no action required.",
  }
  ```
- Each saga write site picks the right key and includes both `error` (full internal text) and `customer_message` (translated).

**Verification:**
- Curl `/status` after each saga failure path; verify `customer_message` does not contain Stripe IDs, exception class names, or env var names.
- The full `error` field stays populated for operator triage.
- Frontend contract update: explicit text in `docs/ORDER_OPTIMIZER.md §8` about which field to surface.

**Dependencies:** none. Self-contained.

---

#### H2. Saga `error` field audit — find any remaining leak sites

After H1, sweep `saga.py` for any `error: ...` write that doesn't have a matched `customer_message: ...` write. Add a static check (grep-based or AST-based) to CI later so new sites don't drift.

**Verification:** `grep -n 'error.*: f"' scripts/checkout/saga.py` should return only paired sites.

**Dependencies:** must come AFTER H1.

---

### Tier 2 — Drift / duplication (fast wins, ship together)

#### H3. Consolidate `is_truthy` across 4 sites

Today: `gate.py:99` (canonical, public), `stripe_provider.py:97` (private duplicate to avoid cross-import), `registry.py:43-46` (inlined inside `_is_replace_allowed`), `Main.py:52-53` (inlined during pre-import bootstrap).

All four use the same truthy set `{"1", "true", "yes", "on"}`. Drift risk if one is changed without the others.

**Fix:** move `is_truthy` to `scripts/checkout/_env.py` (new, dependency-free module) and import from there in:
- `gate.py` (re-export as public-name for backward compat)
- `stripe_provider.py` (drop `_is_truthy`)
- `registry.py` (drop the inline set)
- `Main.py` (after import order is verified — currently bootstraps env BEFORE the checkout package is reachable, may need ordering tweak)

**Verification:** static check that there is exactly one `("1", "true", "yes", "on")` literal in `scripts/checkout/`.

**Dependencies:** none, but bundle with H4 (same touch surface).

---

#### H4. B6 — Stripe key length disagreement between gate and provider

`gate._stripe_key_mode` accepts `sk_test_`/`sk_live_` with any suffix. `stripe_provider._key_mode` requires ≥8 chars beyond the prefix.

**Fix:** move `_key_mode` to a new `scripts/checkout/payment/key_format.py`. Both `gate.py` and `stripe_provider.py` import it. Single source of validation.

**Verification:** test fixture `STRIPE_SECRET_KEY=sk_test_` produces identical gate reason text in both code paths.

**Dependencies:** none, but bundle with H3 (related cleanup).

---

#### H5. B7 — Validate currency at provider construction

Saga currently reads `os.environ.get("STRIPE_CURRENCY", "usd")` fresh on every hold (`saga.py:772`). Drift-risk on restart-mid-saga; no allowlist.

**Fix:** read once in `StripeProvider.__init__`, validate against `_ALLOWED_CURRENCIES = frozenset({"usd", "eur", "gbp", "cad"})`, expose as `provider.currency`. Saga uses `provider.currency`.

**Verification:** `STRIPE_CURRENCY=zzz` causes `StripeProvider.__init__` to raise `PaymentProviderUnavailable` at boot (visible in `/checkout/gate` reasons).

**Dependencies:** none.

---

#### H6. B17-followup — Wire `_reset_for_tests()` into Main.py lifespan shutdown

Today, if FastAPI lifespan ever runs twice in the same Python process, the second `register(StripeProvider())` raises `RuntimeError` (B17 safety check) and crashes lifespan startup. Production today is unaffected (uvicorn spawns fresh child processes per lifespan) but the contract is unstated.

**Fix:** one line in `Main.py` lifespan shutdown block, AFTER the executor shutdown:
```python
try:
    from .checkout.payment import registry as payment_registry
    payment_registry._reset_for_tests()
except Exception:
    log.debug("registry reset on shutdown skipped (already cleared)")
```

Despite the name, the function is safe to call in production shutdown — it just clears the module-level `_active`. Rename to `_clear_active()` in a follow-up if the test-only naming feels misleading.

**Verification:** subprocess test that runs `with TestClient(app):` twice in a row — second `with` block enters lifespan startup without RuntimeError.

**Dependencies:** none.

---

#### H7. Add B15 in-code comment for `stripe.api_key` global

One-line comment above `stripe.api_key = key` (stripe_provider.py:168) referencing B15. Future multi-active work won't miss the key global the way the current doc-only entry would have.

**Verification:** comment present.

**Dependencies:** none. 2-minute change.

---

### Tier 3 — Marketplace / cache correctness

#### H8. B9 + B10 — Cache invalidation symmetry across marketplaces

**B9 (saga.py:959):** stockout retry only invalidates `brickowl_listings:{eid}`. LEGO and BrickLink caches retain stale data.
**B10 (saga.py:959):** the key string is constructed by the Saga, but the actual key is owned by `brickowl_client`. Typo would silently miss.

**Fix (bundle both):**
- Each client module gets `invalidate_listing(element_id) -> Awaitable[None]` that owns its own key naming.
- Saga calls `await asyncio.gather(brickowl_client.invalidate_listing(eid), lego_client.invalidate_listing(eid), bricklink_client.invalidate_listing(eid))` on stockout.

**Verification:**
- Unit: each client's `invalidate_listing(eid)` matches the key its `get_all_listings` writes.
- Integration: stockout retry behavior unchanged in single-marketplace test, no longer reuses stale LEGO/BrickLink cache.

**Dependencies:** none, but bundle B9 and B10 — same touch surface.

---

#### H9. B8 — LEGO.com stockout retryability (or document the asymmetry)

Option B (30 min): explicit `except StockoutError` for LEGO path with a comment that LEGO is the primary source and re-routing to LEGO wouldn't help.
Option A (1 day): make `lego_client.order_from_lego` raise `StockoutError` on DOM-detected stockout; add LEGO path to the retry loop.

Recommend Option B before launch; Option A as a Tier-3 follow-up.

**Dependencies:** none for Option B. Option A requires reliable Playwright stockout DOM detection.

---

#### H10. B16 — Parallelize BrickOwl cancels (BOTH sites)

See the updated B16 entry above. Two sites: `_compensate` (saga.py:413) and stockout-retry inline cancel (saga.py:937). Both must be parallelized.

**Verification:** test fixture with N=10 stub cancels confirms the gather pattern; failed cancel in the middle still produces a single MANUAL_REVIEW (site #2) or single outcome update (site #1).

**Dependencies:** ideally ship before roadmap #5 (real BrickOwl Playwright cancel) goes live.

---

### Tier 4 — Hygiene / latent

#### H11. B23 — Concurrent `/confirm` with different `checkout_id` for same `job_id`

See the new B23 entry above. Option B (reject unless existing is terminal) is the v1 recommendation. 30 min.

**Verification:** integration test that fires two `/confirm` calls with different checkout_ids and confirms the second returns 409 with `code: "JOB_HAS_ACTIVE_CHECKOUT"`.

**Dependencies:** none.

---

#### H12. Documentation: refresh stale line numbers in CHECKOUT_AUDIT.md

`docs/CHECKOUT_AUDIT.md` has many `saga.py:XXX` references that pre-date Phase 1/2/3. The saga grew from ~500 to 1200+ lines. Operators following the audit doc may chase wrong line numbers.

**Fix:** pass through CHECKOUT_AUDIT.md §1–§6, sed-replace stale references. Reasonable acceptance criterion: every `saga.py:N` reference in the doc points at a line that exists and is plausibly related to the topic of the surrounding paragraph.

**Verification:** spot-check 20 random `saga.py:N` references for plausibility.

**Dependencies:** none.

---

#### H13. `_locks` dict grows unbounded (audit FMEA #16)

`checkout_store._locks[job_id]` is added on first access and never removed. Per-job entry; memory leak proportional to lifetime job count.

**Fix:** integrate with `Main.py`'s cleanup thread — when a `job_id`'s output directory is purged on TTL, also `_locks.pop(job_id, None)`. Note: an in-flight saga still holds a strong reference to its lock via `_get_lock(job_id)`'s acquire, so pop-during-saga is safe (lock just becomes the saga's only reference).

**Verification:** stress test with 10k synthetic jobs followed by cleanup; `len(_locks)` should fall back to 0.

**Dependencies:** subsumed by roadmap #2 (Postgres state) when that ships — locks become per-row, no in-process dict. Tier-4 cleanup only if Postgres is delayed.

---

#### H14. `SagaStatus.FALLBACK_ORDERED` — unused enum value

Defined in `models.py:89`, never written by any saga path. Either dead code or unimplemented feature.

**Decision:** ORDER_OPTIMIZER.md §8 notes this is preserved for forward compatibility (will be reachable when BrickOwl supplies some pieces and LEGO.com handles overflow). Keep but document with a `# RESERVED — see ORDER_OPTIMIZER.md §8` comment in `models.py`.

**Dependencies:** none.

---

#### H15. `load/save/update` in `checkout_store` use sync IO under asyncio.Lock

`path.read_text()` / `path.write_text()` block the event loop while the `asyncio.Lock` is held. Negligible at current scale (small JSON, low traffic), would bite at scale.

**Fix when needed:** wrap file IO in `asyncio.to_thread`. Already a pattern used in `stripe_provider.py` for SDK calls.

**Dependencies:** subsumed by roadmap #2 (Postgres state).

---

### Hardening summary table

| H# | Bug ID | Severity | Effort | Bundleable with | Customer-visible? |
|---|---|---|---|---|---|
| H1 | B12 | LOW (security) | 2 hr | H2 | **YES** |
| H2 | — | LOW | 30 min | H1 | indirect |
| H3 | — | LOW | 30 min | H4 | No |
| H4 | B6 | MEDIUM | 30 min | H3 | No |
| H5 | B7 | MEDIUM | 30 min | — | No |
| H6 | B17 followup | LOW | 5 min | — | No |
| H7 | B15 | LOW | 2 min | H3/H4 | No |
| H8 | B9 + B10 | MEDIUM | 1 hr | — | indirect (retry latency) |
| H9 | B8 (Option B) | MEDIUM | 30 min | — | indirect |
| H10 | B16 | LOW | 30 min | — | indirect (compensation latency) |
| H11 | B23 | MEDIUM | 30 min | — | **YES** |
| H12 | doc-drift | doc | 1 hr | — | No |
| H13 | FMEA-16 | LOW | 30 min | roadmap #2 | No |
| H14 | enum cleanup | doc | 5 min | — | No |
| H15 | sync-IO-under-lock | LOW | — | roadmap #2 | No |

**Recommended sequencing (pre-database-migration view):**
1. **H1 + H2** (B12) — customer-visible info leak; resume the paused Phase 3.4 work.
2. **H6** (B17 lifespan) — protect against latent test-framework crash; 5 min.
3. **H3 + H4 + H7** (drift cleanup bundle) — touch the same files (gate.py, stripe_provider.py, registry.py); ship together.
4. **H5** (currency validation) — adjacent to H4.
5. **H11** (B23) — customer-visible scenario, even if rare.
6. **H8** (B9 + B10) — marketplace cache correctness.
7. **H10** (B16) — must ship before roadmap #5 (real BrickOwl Playwright cancel).
8. **H9** (B8 Option B doc-only) — before launch so support staff can explain LEGO stockout customer complaints.
9. **H12 + H14** — doc cleanup pass.
10. **H13 + H15** — defer to roadmap #2 (Postgres state).

Total for items 1–9: roughly **7 hours focused work**, ignoring per-item testing time. Add ~2 hours for testing per Tier-1/2 item if integration tests need to be written from scratch.

**Sequencing decision (2026-05-16):** the database migration (§9 below) supersedes this order. Several hardening items (H6, H13, H15, B11, B25, B26) become moot once persistent state lands. Recommended new sequence:

1. **§9 database migration — Rounds 1/2/3.** All Tier-0 structural defects (S1, S4 partial) close together.
2. **H1 + H2** (B12) — only remaining Tier-1 customer-visible defect.
3. **H11** (B23) — solved more elegantly with row-level locks in Postgres than with app-level checks.
4. **H3 + H4 + H7** (drift cleanup) — independent of DB; can be done any time.
5. **L6 audit log** — schema already designed; write directly to Postgres rather than NDJSON. Saves a migration round trip.
6. **Remaining H5, H8, H9, H10** — independent of DB; can interleave.

---

## 9. Database migration plan

**Authoring date:** 2026-05-16.
**Owner:** Grant Benson.
**Driver decision:** asyncpg (raw SQL, no ORM). Rationale in §9.1.4.
**Scope:** ALL persistent state — mosaic generation jobs AND checkout state. Operator can see job failures in one place.
**Host decision:** OPEN — Round 1 §9.1.3 compares four options in depth; user makes the call.

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
5. (Optional pre-Phase-B) Create a `dev` branch off main for local development: `neon branch create dev`.

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

**Decision: Neon dev branch.** Create a `dev` branch off `main` once Phase A provisioning is done; point each developer's local `DATABASE_URL` at it (or per-developer sub-branches if/when we grow beyond a solo dev).

**Why:**
- **Production parity.** Same asyncpg config, same pooler behavior (transaction-mode PgBouncer, statement_cache_size=0), same TLS handshake. Bugs related to those (statement-cache misconfigurations, TLS cert chain issues, pooler-specific timing) reproduce locally instead of surfacing only after deploy.
- **Branch reset = clean DB.** `neon branch delete dev && neon branch create dev` wipes everything in seconds — no `docker volume rm` choreography. Useful when alembic migrations are mid-iteration and you want to start fresh.
- **No drift risk between local schema and prod.** Same alembic migrations applied to both.
- **Free** within the free-tier compute-hours budget (which is generous for a solo dev).

**Fallback: Docker Postgres locally** (the alternative path) if any of these become true:
- Compute-hours budget exhausted (multiple developers working in parallel against branches that don't autosuspend).
- Working offline routinely (Neon needs an internet connection).
- Local iteration is hot enough that the network round-trip latency to Neon matters (Neon median is ~10-30ms; Docker localhost is <1ms).

If you switch to Docker, accept the drift risk and validate against a real Neon branch before each merge to main:

```bash
docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 -d postgres:16
DATABASE_URL=postgres://postgres:dev@localhost:5432/postgres alembic upgrade head
```

**Action during Phase A:**
1. Create the `dev` branch: `neon branch create dev --parent main`.
2. Copy its pooler connection string into a gitignored `.env.local`: `DATABASE_URL=postgresql://...?sslmode=require`.
3. Confirm `alembic upgrade head` runs cleanly against `dev` (it will be a no-op on a fresh branch until Phase B writes the first migration).

**NEVER point local dev at the production branch.** Use `.env.local` (gitignored) for the dev connection string; `.env.secrets` holds the prod string and lives only on Render.

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
2. **Create the `dev` branch** off `main`: `neon branch create dev`. Copy its pooler connection string into a gitignored `.env.local`. This is where local dev points.
3. **Write the schema migration.** Phase B. Test on a throwaway branch (`neon branch create migration-test-<date>`), then delete the branch and apply to `main`.
4. **Migrate `checkout_store`.** Phase C. Keep the JSON path behind `DB_BACKEND` flag (Q11 decision — see §9.3.11.11).
5. **Migrate mosaic lifecycle.** Phase D.
6. **Add resume-on-startup + reconciliation.** Phase E. Add `emit()` for L6 audit log (Q8).
7. **Flip the feature flag.** Phase F. Set `DATABASE_URL=...` (Neon pooler URI) and `DB_BACKEND=postgres` in Render. **Upgrade Neon project to Launch ($19/mo)** in the same window so autosuspend doesn't cold-start the first customer.
8. **Watch for 7 days.** Verify resume-on-startup behaved correctly through one or two Render restarts (forced if necessary). Then run the JSON-path cleanup commit (§9.3.11.11).
9. **Set calendar triggers** for the deferred items in §9.3.11.X (audit table growth check, pool exhaustion check, Neon storage usage approaching 10 GB).

After this lands, the remaining H1–H15 hardening items proceed against a much-easier-to-reason-about system. The whole "what happens on restart" class of bugs is closed.
