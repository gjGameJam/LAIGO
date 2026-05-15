# Checkout / Order Optimizer — Technical Audit & Target Architecture

**Audience:** LAIGO maintainers — design doc for the pre-launch v1 ship.
**Scope:** Everything under `scripts/checkout/`, plus the `Main.py` integration points.
**Posture:** Pre-launch. Stripe disabled, BrickOwl catalog API not yet granted, LEGO.com Playwright untested in live. No customer money has moved yet. Recommendations are framed as **"what must be true before the first real charge."**
**Authoring date:** 2026-05-15.

---

## 0. Executive summary

The current implementation is structurally an honest Saga: Stripe hold → place orders → Stripe capture, with a checkpoint file and a compensation routine. That outer shape is correct. **What is missing is everything in between** — the system today has no defenses against the failure modes a real marketplace integration actually produces. Specifically:

1. **There is no concept of an inventory reservation.** A quote captured at T+0s is replayed at T+600s (or later, after stockout retries) against listing data that may be up to 1 hour old. There is no "lock" call, no pre-charge revalidation step, and no atomic acceptance check before Stripe is captured.
2. **The "atomic buy" path does not exist for the primary marketplace.** `brickowl_client.create_order()` raises `NotImplementedError`; BrickOwl has no buyer-side order API. The current architecture pretends there is one. Whatever ordering mechanism actually ships (Playwright, cart-URL redirect) will not behave like an atomic API call, and the Saga is not designed to tolerate that.
3. **The Saga is not crash-safe.** It runs as an `asyncio.create_task` with no resumption code. If the process dies between `stripe_held` and `payment_captured`, customer funds are held with no automatic recovery. The checkpoint file exists but nothing reads it on startup.
4. **Stripe is silently bypassed in dev mode.** `_check_enabled()` raises `NotImplementedError`, which the Saga catches and continues past — meaning orders can be placed today with no payment hold at all. This is acceptable only as long as ordering is also stubbed; the moment either side goes live and the other does not, the system **places real orders with no payment**.
5. **The optimizer's complexity is fine for today's scale but pathological at the upper bound** — Pass 2 is O(S³·N·L) with a full restart after every merge, and a 2000-piece × 15-vendor mosaic measurably degrades the quote experience.
6. **The marketplace integration is duck-typed, not interface-typed.** The Saga hardcodes `seller_id.startswith("brickowl_")` routing and `apply_free_shipping_thresholds` hardcodes `lego_official`. Adding a marketplace today means editing three files; it should mean adding one.
7. **Cart-snatching is not just possible — it is the default.** Pricing is read from a 1-hour TTL cache, the customer has 10 minutes to confirm, and the Saga has no upper bound (Playwright timeouts are 90s × multiple steps × stockout retries × multiple sellers). Real failure window from quote-fetch to atomic-commit can exceed 30 minutes.

Sections 1–5 below ground each of these in specific file:line references. Section 6 proposes a target architecture. Section 7 is the quantitative FMEA.

---

## 1. Gap Analysis — Transactional Integrity & Race Conditions

### 1.1 The "Cart-Snatching" window is the entire system

There are **three** independent cart-snatching windows stacked in series:

| Window | Span | Cause |
|---|---|---|
| **W1 — listing-cache freshness** | up to `BRICKOWL_CACHE_TTL_SECONDS` (1 h default) before the quote even runs | `brickowl_listings:{eid}` cached 1 h (`cache.py` + `brickowl_client.py:284`); `lego_raw:{eid}` cached 1 h (`lego_client.py:_PRICE_CACHE_TTL`) |
| **W2 — quote-to-confirm** | up to 600 s | `quote:{checkout_id}` cached 10 min (`router.py:118-125`); customer browses and clicks "Confirm" |
| **W3 — confirm-to-order-placed** | up to ~10 min worst-case | Saga runs as a background task: Stripe hold → BrickOwl orders (sequential, per seller) → LEGO.com Playwright (~90 s × multiple steps) → stockout retry (re-fetch + re-optimize + retry, up to 2 times → `OPTIMIZER_MAX_STOCKOUT_RETRIES`) |

**Worst-case quoted-price-to-commit latency: ~75 minutes.** A piece quoted at T+0 may not actually be reserved at the seller until T+75min, by which point any popular piece has a non-trivial probability of being sold to someone else.

The optimizer treats `SellerListing.available_qty` as authoritative at order time (`optimizer.py:88-106`), but `available_qty` is a snapshot from W1. Nothing in the code re-checks availability between W2-end and the actual order placement in W3.

### 1.2 The single stockout-detection mechanism is post-hoc and incomplete

`saga.py:137-147` catches `StockoutError` raised by `brickowl_client.create_order()`. This is the **only** stockout signal in the system.

Problems:

1. **There is no `create_order` yet.** `brickowl_client.py:325-334` is a `NotImplementedError` stub. Whatever implementation lands (Playwright cart automation per Option A in `docs/ORDER_OPTIMIZER.md §17`) will need to detect stockout from DOM scraping or screenshot heuristics — not as a clean exception. The Saga's `except StockoutError:` branch will not fire unless someone faithfully translates "cart says out of stock" into the right exception.
2. **LEGO.com has no stockout retry path at all.** `saga.py:196-208` wraps `order_from_lego` in a generic `except Exception` that triggers compensation and `FAILED`. If LEGO.com's checkout fails because a piece sold out between quote and Playwright session, the customer gets a refund (good) but no automatic re-routing to another source (bad — exactly the case the Saga's retry loop is supposed to solve).
3. **A "phantom stockout" cancels innocent orders.** When BrickOwl seller 3 of 4 reports stockout, `saga.py:158-159` cancels the orders already placed at sellers 1 and 2, then re-optimizes. This is correct as compensation theory but **wasted shipping fees, restocking fees, and possible seller-side rate-limit triggers** are real costs. The current code has no concept of "preferred resilience" — keep orders 1 and 2, only re-route piece X.

### 1.3 There is no per-job confirmation lock

`router.py:182-184` only blocks **re-confirmation of the same `checkout_id`**. Two distinct quotes for the same `job_id` produce two different `checkout_id` values, and both can be confirmed and run their Sagas concurrently:

```
Customer opens two tabs:
  Tab 1: POST /quote → checkout_id=co_AAA
  Tab 2: POST /quote → checkout_id=co_BBB
  Tab 1: POST /confirm co_AAA → Saga A starts, places LEGO order #1
  Tab 2: POST /confirm co_BBB → Saga B starts, places LEGO order #2
  Customer pays twice, receives two complete mosaic kits.
```

The shared LAIGO LEGO.com account also has no per-process concurrency control — both Playwright sessions log into the same account in parallel, with undefined outcomes (cart corruption, account lockout, doubled orders).

### 1.4 The Allocation drift problem

`saga.py:115-183`'s stockout retry re-runs `optimize()` with fresh listings (`saga.py:170-177`), producing a **new** `current_allocation` whose `customer_total_cents` may differ from the original Stripe hold amount.

The new total is stamped into the state file at `saga.py:235-239`:
```python
"total_charged_cents": current_allocation.customer_total_cents,
```

But the Stripe capture call at `saga.py:217` (`capture_payment(intent_id)`) captures up to the **held** amount, not the new total. If the new total is **higher** (e.g. fallback to LEGO.com after a cheap-seller stockout), the customer is undercharged. If **lower**, the hold over-authorized but capture is partial — defensible, but the customer paid for orders A+B and was actually charged for A+C without the audit trail showing which.

There is no code path that adjusts the Stripe hold amount mid-Saga, no code path that aborts when the new total exceeds the old total by some threshold, and no test that the recomputed allocation respects the customer's authorized amount.

### 1.5 Recommended frame: Pre-check/Lock vs Atomic-Buy

The two viable patterns are not interchangeable:

| Pattern | When valid | What it requires from the marketplace |
|---|---|---|
| **Pre-check / Lock** (reservation API) | When the marketplace exposes "reserve N of SKU for T seconds" semantics | A reservation endpoint with a TTL, plus a release endpoint. The Saga reserves before Stripe hold; the reservation TTL must exceed the maximum Saga duration. |
| **Atomic Buy** (best-effort commit, compensate on failure) | When no reservation API exists | A reliable, idempotent "place order" call and a reliable cancel path; the Saga places, then captures payment only on confirmation. |
| **Customer-completes** (cart-URL redirect) | When neither of the above is feasible | Stripe does NOT hold the marketplace portion — the customer pays the marketplace directly. The Saga's responsibility shrinks to "handed off cart URLs." |

**The current marketplaces map to these as follows:**

| Marketplace | Reservation API? | Atomic Order API? | Practical pattern |
|---|---|---|---|
| **LEGO.com** | No (cart is per-session, expires at session end) | No public API — Playwright only | Hybrid: Playwright session functions as a short-lived implicit lock (the items are in *our* cart), so order-at-end is "as atomic as it gets." Compensation is **manual cancellation only**, which makes Stripe capture order-of-operations critical. |
| **BrickOwl** | Unknown — `catalog/cart_basic` may serve this role (`docs/ORDER_OPTIMIZER.md §17.4.4`) | No buyer order/create endpoint — Playwright or cart-URL only | Atomic-Buy via Playwright (Option A) or Customer-completes via cart URL (Option B). The Saga must pick one **per marketplace** and never assume the API contract is uniform. |
| **BrickLink** | No (price-guide is anonymous) | No per-store identity | Likely unusable for atomic ordering. Use for pricing comparison only. |

**The architecture must allow each marketplace adapter to declare its own commit semantics**, and the Saga must orchestrate them as a heterogeneous transaction rather than assuming all of them implement `create_order(...)` with the same guarantees.

---

## 2. Gap Analysis — Heuristic vs Exact Cost Optimization

### 2.1 Complexity, measured

**Pass 1** (`optimizer.py:64-109`): For each of `P` distinct elements, sort the listings (length `L`) by `(seller_already_chosen, price)`, then iterate. **O(P · L log L)**.

**Pass 2** (`optimizer.py:111-166`): Restart-after-each-merge structure.
- Outer loop: up to `S` iterations (one merge per pass, in the worst case).
- For each outer iteration: enumerate seller pairs `(A, B)` → S² pairs.
- For each pair: iterate B's items (up to `I_B` items per seller), for each item lookup A's listing with `next(...)` over `listings[eid]` (O(L) per lookup).
- Merge step itself: another `next(...)` over `listings[eid]` per moved item.

**Total: O(S² · S · I · L) = O(S³ · I · L)** where `I` is average items-per-seller.

**Numerics for the stated scenario (2000 pieces, 15 vendors):**
- Pass 1: 2000 elements × avg ~15 listings × log₂(15) ≈ 100,000 comparisons. Negligible.
- Pass 2: 15³ × 130 items/seller × 15 listing-lookups = **6.6M comparisons** per "round," and the worst case is 15 rounds before convergence = **~100M operations**. In pure Python with `next()` generator-scan inside `next()` generator-scan, this realistically takes **5–15 seconds**.

For the quote endpoint's stated <20 s target (`router.py:6-7`), Pass 2 alone consumes most of that budget. Add 2000 cache-miss network calls (LEGO.com search API) on a cold cache and the quote latency at the upper bound is **30–90 seconds**, plus the user is staring at a spinner with no progress feedback.

### 2.2 Pass 2 is also incorrect at the threshold-jump boundary

The consolidation decision is `extra_cost < b_shipping`. It ignores **free-shipping thresholds** that the destination seller A may have or may cross after the merge:

```
Scenario:
  Seller A has $25 of pieces allocated, charges $0 shipping above $30 (BrickOwl seller threshold).
  Seller B has 1 piece at $4, ships flat $6.
  extra_cost = $0.50 (A's price is slightly higher)
  b_shipping = $6
  Merge condition: 0.50 < 6  → merge happens. Good.
  But: A now has $25 + $4.50 = $29.50, still under threshold, A's shipping unchanged.

  Now imagine A's threshold is $20 (already crossed) and there's a seller C with 1 piece at $0.10, ships flat $15:
  extra_cost = ~$0.30
  c_shipping = $15
  Merge C into A: 0.30 < 15 → merge. Saves $15. Good.

  But the inverse — when A is JUST UNDER threshold and adding C would push over —
  is exactly what the optimizer fails to see, because A's pre-merge shipping is
  already counted; the post-merge "negative cost" of crossing a threshold is invisible.
```

`apply_free_shipping_thresholds()` (`optimizer.py:203-249`) runs **after** the optimizer and only handles LEGO.com's $35 threshold as a one-time pass. It cannot influence Pass 1 / Pass 2 decisions. So the optimizer cannot deliberately "round up to free shipping" by adding the cheapest plausible piece.

The "Threshold Jump" the prompt describes — where adding one $0.10 brick can either *save* $15 (crosses threshold) or *cost* $15 (triggers a new seller's flat fee) — has the right intuition. Pass 2 sees the *cost* case but not the *save* case.

### 2.3 The optimizer is greedy with no cost ceiling

The optimizer can return an allocation whose `customer_total_cents` is wildly above the customer's expected price. There is no:
- Maximum-acceptable-cost check (would block a quote whose total is > the customer's pre-quote budget signal).
- Per-piece price sanity check (a misparsed listing at $99/piece will silently inflate the quote).
- Cross-source price-drift detector (LEGO.com piece is normally $0.15 but the API returned $1500 due to a field mismatch).

`_parse_price_cents()` at `lego_client.py:134-166` has multiple field-name fallbacks; if any one of them returns dollars when assumed to be cents (or vice versa), the optimizer happily commits a 100× overpriced quote.

### 2.4 Suggested improvements

1. **Replace Pass 2's "restart after every merge" with a stable iteration order and a single pass.** Sort sellers by ascending `shipping_cost_cents`; for each B in that order, attempt one merge into the best A; do **not** restart. This is provably no worse than the current heuristic for a greedy local-optimum and runs in O(S² · I · L).

2. **Precompute a seller→listing index per element.** Replace the `next(l for l in listings[eid] if l.seller_id == X)` scans with `listings_by_seller[eid][X]` — a dict-of-dicts built once. This eliminates the L factor entirely.

3. **Make threshold-aware merges first-class.** When evaluating "merge B into A," include in `extra_cost`:
   - The shipping A would *save* by crossing its free-shipping threshold post-merge.
   - The shipping A would *gain* by exceeding a max-order weight/quantity threshold.

4. **Add a "round up to threshold" pass.** After Pass 2, for each seller within `$X` of a free-shipping threshold, check whether any tiny ($<$0.50) piece can be moved from another seller (or duplicated as filler) to push the threshold and save shipping. This needs business buy-in: customers may not want a filler piece they did not ask for.

5. **Cap the optimization at a known-good complexity.** For mosaics above some threshold (e.g., > 1000 pieces or > 8 candidate sellers), fall back to "skip Pass 2" and quote with whatever Pass 1 produced, with a logged note. Real-world saving in Pass 2 is bounded by `S × max_shipping`; for large orders the absolute saving is small relative to the per-piece cost.

6. **Sanity-check returned prices.** Reject any individual listing whose price-per-unit exceeds 10× the median across all listings for that element. Surfaces parsing errors before they reach the customer.

---

## 3. Gap Analysis — The Payment–Purchase Bridge

### 3.1 The Saga's order-of-operations is correct in theory, dangerous in practice

The intended order is:
1. Stripe HOLD (authorize, no capture).
2. Place orders at all marketplaces.
3. Stripe CAPTURE.
4. On failure between 1 and 3: compensate (cancel orders, release Stripe hold).

This is the Saga pattern, and the broad shape is right. The **specific failure points** that are not handled:

#### 3.1.1 Stripe-disabled-in-dev silently bypasses payment

`saga.py:104-106`:
```python
except NotImplementedError:
    logger.warning("Stripe not configured — skipping payment hold")
    await checkout_store.update(job_id, {"saga_status": SagaStatus.STRIPE_HELD})
```

This catches the dev-mode `NotImplementedError` from `stripe_client._check_enabled()`. It sets `saga_status=STRIPE_HELD` despite no hold existing, then proceeds to place real orders.

**This means there is currently a code path where LEGO.com orders are placed with no payment hold whatsoever.** Today this is "safe" because LEGO ordering is also untested, but the moment `lego_client.order_from_lego` works in live and Stripe is still stubbed, the first customer to hit the live endpoint costs LAIGO the entire order amount.

The dev-mode bypass should fail hard, not warn. Or there should be a single feature flag (`CHECKOUT_ENABLED`) that gates *the entire checkout endpoint*, not per-component bypasses.

#### 3.1.2 Capture-after-orders has no recovery if capture fails

`saga.py:217-233`:
```python
try:
    await stripe_client.capture_payment(intent_id)
except Exception as exc:
    # Orders already placed — do NOT compensate; flag for manual review
    logger.error(...)
    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.FAILED,
        "error": f"Stripe capture failed after orders were placed: {exc}. "
                 "Orders ARE placed. Manual review required.",
    })
    return
```

Capture can fail because:
- The hold expired (Stripe holds typically last **7 days** but card-issuer-dependent — some issuers release in 1–3 days). If a Saga is restarted from checkpoint days later, capture can silently fail with `payment_intent_authentication_failure`.
- The customer's card was cancelled / reported lost between hold and capture.
- A Stripe API outage during capture.

In all three cases, **orders are placed at the marketplace and LAIGO has no money**. The current code logs and stops. There is no:
- Automatic retry of capture (Stripe captures are idempotent and safe to retry).
- Notification to the operator (no email/Slack/PagerDuty hook).
- Compensating refund / alternate-payment flow.
- Bounded financial exposure (no cap on outstanding "orders placed, payment failed" amount before the endpoint is auto-disabled).

#### 3.1.3 BrickOwl and LEGO.com cancellation is best-effort

`_compensate()` at `saga.py:37-63`:
- BrickOwl: calls `brickowl_client.cancel_order(oid)` which `logger.warning`s "no API endpoint exists" and returns silently. **No cancellation actually happens.**
- LEGO.com: logs "cannot be cancelled via API — cancel manually." **No cancellation actually happens.**
- Stripe: cancels the PaymentIntent. Idempotent. ✓

So compensation today is: release the Stripe hold, log a warning about the marketplace orders, and call it "compensated." If only the LEGO.com order succeeded and Stripe hold failed, the customer pays LAIGO nothing, but LAIGO already paid LEGO.com via the LAIGO LEGO.com account's saved card. **LAIGO eats the loss.**

The current code's claim of "compensated" status is, in this case, misleading — the financial state is "LAIGO out-of-pocket, marketplace order shipped."

### 3.2 Partial failure (4 of 5 succeed, 5th fails)

For the BrickOwl Playwright path: orders 1-4 succeed, order 5 fails. The current code (`saga.py:140-147`) catches the generic `Exception`, calls `_compensate()`, sets `FAILED`. `_compensate()` then tries to cancel orders 1-4 via `cancel_order` — which is the stub — so **orders 1-4 ship** and **the customer is refunded the Stripe hold**. LAIGO eats the cost of orders 1-4.

For the LEGO.com path: this is conceptually a single atomic order (one Playwright session, one cart, one "Place Order" click), so "4 of 5 succeed" doesn't apply within LEGO.com. It applies at the Saga level — BrickOwl orders 1, 2, 3 succeed; LEGO.com order fails. Same disposition: BrickOwl orders cannot be cancelled (stub), LAIGO eats cost.

**Until `cancel_order` is implemented (via Playwright or seller-contact-message), partial failure has unbounded financial downside.** This is the single largest pre-launch risk.

### 3.3 Idempotency keys

| Operation | Idempotency key | Status |
|---|---|---|
| Stripe hold | `f"hold-{checkout_id}"` (`saga.py:98`) | ✓ Correct — replayable safely |
| Stripe capture | none | ✗ Stripe's PaymentIntent.capture is naturally idempotent (subsequent calls return the same captured intent), but a defensive idempotency key (`f"capture-{checkout_id}"`) would protect against Stripe-side anomalies |
| Stripe cancel | none | ✗ Same — naturally idempotent in Stripe, defensive key recommended |
| BrickOwl create_order | none | ✗ The stub takes no key; Playwright cannot enforce idempotency without per-cart fingerprinting (e.g., a hash of items committed to the seller's order ID via a custom note field). **Critical: a retry of `create_order` during a network glitch would place a duplicate order.** |
| LEGO.com order_from_lego | none | ✗ Same concern — Playwright sessions are not naturally idempotent. A Saga retry mid-`order_from_lego` could double-submit. |
| Saga itself | `checkout_id` acts as one (router.py:182-184) | Partial — same `checkout_id` rejected at confirm, but no key flows into the actual marketplace calls |

**The marketplace calls have no idempotency story at all.** This is unsolvable for Playwright without a pre-flight check ("does this seller already have an order from us with `checkout_id` X today?"). For BrickOwl with cart-URL redirect (Option B), idempotency is the customer's responsibility — clicking the link twice produces two orders.

### 3.4 The Saga is not crash-safe

`router.py:203-209`:
```python
asyncio.create_task(saga_module.execute_checkout_saga(...))
```

This is fire-and-forget. If the FastAPI process crashes between `STRIPE_HELD` and `PAYMENT_CAPTURED`:
- The checkpoint file `outputs/{job_id}/checkout_state.json` records the last known state.
- **No code reads this on startup.** A restarted server has no awareness that a Saga was in flight, no resume routine, no operator alert.
- The customer's Stripe hold persists for up to 7 days, then auto-releases without orders ever being placed.
- The customer polls `/status`, sees `STRIPE_HELD` forever.

On a platform like Render where the filesystem is ephemeral, the checkpoint file itself is gone after restart. Even manual recovery is impossible.

### 3.5 The asyncio.Lock per-job-id problem

`checkout_store.py:19-34` keeps one `asyncio.Lock` per `job_id`, never cleaned up (already documented as Known Issue #8 in `docs/ORDER_OPTIMIZER.md`). The bigger problem is that **`asyncio.Lock` is bound to the event loop that created it**. If the FastAPI server is run multi-worker (multiple uvicorn workers, multi-process), each worker has its own lock dict and the locks are not coordinated. The status-polling handler in worker B can race the Saga in worker A.

The deployment target (Render, currently single-process) hides this today. Any horizontal scaling exposes it immediately.

---

## 4. Gap Analysis — Scalability & External Constraints

### 4.1 Rate limits

| Source | Documented limit | Current handling |
|---|---|---|
| **BrickOwl** | 600 req/min standard, 100 req/min bulk | `brickowl_client.py:35-45`: process-wide semaphore (default 10 concurrent); exponential backoff (2 s base, 3 retries) on 429/5xx. |
| **LEGO.com** | Undocumented; **not a public API** | `lego_client.py:83-117`: no semaphore, no rate-limit-aware backoff, only a 10 s timeout. Concurrent fan-out is unbounded by `get_all_listings`. |
| **BrickLink** | Documented at ~5000 req/day | Stub. |

**LEGO.com is the biggest exposure.** A 100-piece order on a cold cache results in 100 concurrent HTTPS requests to `lego.com/api/product/search/en-US`. From a single IP this looks like a bot. LEGO has anti-bot infrastructure (Akamai). The User-Agent is a generic `Mozilla/5.0 (compatible; LAIGO/1.0)` — not authentic, easy to flag.

If LEGO.com starts rate-limiting or blocking, the entire MVP source goes dark. There is no:
- Per-source circuit breaker.
- Backoff on LEGO.com requests.
- Concurrency cap on LEGO.com requests.
- Identifying header (e.g., a polite scraping convention like `From:` with a contact email).
- Fallback when LEGO.com returns 403/429 for one request — `_search` returns None and the optimizer treats the element as unsourceable, which **fails the entire quote** at `can_proceed=false`.

### 4.2 Data staleness

`docs/ORDER_OPTIMIZER.md §11` documents the cache:

| Key pattern | TTL | Implication |
|---|---|---|
| `lego_raw:{element_id}` | 1 h | LEGO.com price/availability may be 1 h stale at quote time. Up to 1 h + W2 + W3 stale at order time. |
| `boid:{element_id}` | 24 h | BOID rarely changes; safe. |
| `brickowl_listings:{element_id}` | 1 h | BrickOwl listings may be 1 h stale. |
| `quote:{checkout_id}` | 10 min | Quote freshness gate. |

The cache is **in-process**. A restart clears it. The cache is **per-instance**. Multi-worker deployment fragments it (10 workers = 10 separate caches). Neither is a problem at v1 scale, but the design assumes single-instance forever.

The freshness gate at quote-time is **10 minutes from quote creation**, but the data fed into the quote can be **1 hour old**. So the real freshness contract the customer sees is up to **70 minutes between price discovery and price commitment** — and then up to W3 more before the order actually lands at the seller.

**There is no pre-order revalidation step.** Before placing the BrickOwl order or the LEGO.com order, the Saga should re-check (at least sample) availability and price; it does not. This is the single most-effective change for closing the cart-snatching window: a 1-second re-validation immediately before commit shrinks W3's staleness from "minutes" to "seconds."

### 4.3 No backpressure / no global rate budget

`router.py:50-149` has no concurrency limit on the quote endpoint itself. The job processing pipeline (`Main.py`) caps concurrent mosaic generation at 1 worker, but the checkout endpoints are independent of that. 50 customers hitting `POST /quote` concurrently fan out to 50 × N concurrent LEGO.com requests, instantly tripping LEGO's anti-bot.

A global request budget (e.g., `Main.py` lifespan installs a per-source `AsyncLimiter` with a known req/min budget) would protect the integration even under organic load spikes.

### 4.4 No reusable HTTPX client

`brickowl_client.py` and `lego_client.py` both create a new `httpx.AsyncClient` per request (`async with httpx.AsyncClient() as client:`). This forces TCP + TLS handshake on every call. For a 100-piece quote, that is 100 unnecessary handshakes at LEGO.com and up to 200 at BrickOwl. Singleton clients on the lifespan would cut quote latency 30–50%.

---

## 5. Gap Analysis — Cross-cutting issues

### 5.1 Marketplace integration is duck-typed, not interface-typed

There is an implicit contract:

```python
async def get_all_listings(
    order_items: list[dict],
    shipping_country: str,
    shipping_zip: str,
    cache_ttl: int = 3600,
) -> dict[str, list[SellerListing]]
```

…but it is enforced only by convention. Adding a new marketplace requires:

1. Creating `clients/{new}_client.py` with `get_all_listings`, `create_order`, `cancel_order`.
2. Importing it in `router.py:42-43` and adding it to the `asyncio.gather(...)` at `router.py:73-93`.
3. Adding the seller_id prefix check in `saga.py:125-128` and adding the if-branch for routing orders.
4. Adding the free-shipping-threshold logic in `optimizer.py:203-249` (currently LEGO.com-only).
5. Adding the routing for the stockout retry path in `saga.py:166-177`.

Five separate touch-points for what is conceptually one addition. **This is exactly the kind of horizontal change Section 6.1 below proposes to abstract.**

### 5.2 Hardcoded LEGO.com everywhere

- `optimizer.py:215`: `from .clients.lego_client import SELLER_ID as _LEGO_ID` — couples the optimizer to a specific client.
- `optimizer.py:217`: `LEGO_FREE_SHIPPING_THRESHOLD_CENTS` is a single env var; not a per-marketplace property.
- `saga.py:34`: `_LEGO_SELLER_ID = lego_client.SELLER_ID`.

These need to be properties on a marketplace adapter, not constants on the optimizer.

### 5.3 Error visibility is poor

When a Saga fails, the `error` field on the state file is a short string. The Saga itself logs at the `error` level, but there is:
- No persistent error catalog (which errors are happening across all checkouts? A daily summary?).
- No alerting hook.
- No customer-facing translation. A user polling `/status` sees the raw exception text in the `error` field, which may include internal paths or credentials in stack traces.

### 5.4 Tests do not exist

`docs/ORDER_OPTIMIZER.md §16` lists test scripts but they are docstrings, not files. There is no `tests/` directory. The optimizer is a pure function — it is the single most-test-worthy module in the codebase. **Pre-launch, this is the highest-leverage debt to pay off.**

### 5.5 The free-shipping pass mutates a copy but the optimizer doesn't know

`apply_free_shipping_thresholds()` returns a new `AllocationResult` via `model_copy(update={...})`. It only mutates LEGO.com entries (`optimizer.py:222-226`). If two LEGO.com entries existed in the allocation (currently impossible since all LEGO.com items get the same `seller_id`, but the optimizer doesn't enforce this), only some would be zeroed.

More importantly: the free-shipping decision is made on the *output* of the optimizer. The optimizer might have routed pieces to BrickOwl because LEGO.com's $35 threshold wasn't crossed. If `apply_free_shipping_thresholds` then crosses it, BrickOwl pieces could (in principle) be re-routed to LEGO.com for free — but this re-optimization never happens. Quotes can be sub-optimal by single-digit dollars in this corner.

---

## 6. Proposed Target Architecture

The target is a **layered architecture** with three distinct concerns separated:

```
┌──────────────────────────────────────────────────────────────────────┐
│  API layer (router.py)                                                │
│    Validates input, returns responses. No business logic.             │
├──────────────────────────────────────────────────────────────────────┤
│  Orchestration layer (saga + recovery)                                │
│    Coordinates Stripe + N marketplace adapters as a transaction.      │
│    Crash-safe: every state transition persists, on startup resumes.   │
├──────────────────────────────────────────────────────────────────────┤
│  Marketplace adapter layer (MarketplaceAdapter protocol)              │
│    Each marketplace implements the same interface. Capabilities       │
│    (supports_reservation, supports_atomic_order, supports_cancel)     │
│    are declared by the adapter, not hardcoded in the saga.            │
├──────────────────────────────────────────────────────────────────────┤
│  Pricing / optimizer (pure function)                                  │
│    Takes listings + capabilities → allocation. Capability-aware       │
│    threshold optimization. No I/O.                                    │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.1 Marketplace adapter protocol

Replace the implicit duck-typed contract with a `typing.Protocol`. Each adapter declares its capabilities up front so the Saga can compose them safely.

```python
# scripts/checkout/marketplace.py

from typing import Protocol, runtime_checkable
from dataclasses import dataclass
from enum import Enum

class CommitMode(Enum):
    RESERVE_THEN_COMMIT = "reserve_then_commit"  # Has reservation API
    ATOMIC_ORDER        = "atomic_order"          # API order/create endpoint
    PLAYWRIGHT_SESSION  = "playwright_session"    # Browser automation
    CUSTOMER_REDIRECT   = "customer_redirect"     # Cart URL handoff

@dataclass(frozen=True)
class MarketplaceCapabilities:
    seller_id_prefix: str            # "brickowl_", "lego_official", etc.
    commit_mode: CommitMode
    supports_idempotency_key: bool   # True if commit takes an idempotency key
    supports_api_cancel: bool        # False for LEGO.com; True for BrickOwl-via-portal-automation
    free_ship_thresholds: list[dict] # [{above_cents: 3500, ship_cents: 0}]
    listing_ttl_seconds: int         # max acceptable staleness of listing data
    max_concurrent_commits: int      # 1 if all items go in one order; >1 if per-seller
    requires_pre_commit_revalidation: bool  # True if listings can become stale faster than ttl
    rate_limit_rpm: int              # for the central limiter

@runtime_checkable
class MarketplaceAdapter(Protocol):
    capabilities: MarketplaceCapabilities

    async def fetch_listings(
        self, order_items: list[OrderItem], destination: ShippingDestination
    ) -> dict[ElementID, list[SellerListing]]:
        ...

    async def revalidate(
        self, allocation_entries: list[AllocationEntry]
    ) -> RevalidationResult:
        """
        Pre-commit availability + price check. Returns:
          - confirmed: entries that are still valid as quoted
          - drifted:   entries whose price/availability changed (with new values)
          - stockout:  entries that are no longer available
        """
        ...

    async def reserve(self, entry: AllocationEntry, ttl_seconds: int) -> ReservationToken | None:
        """For RESERVE_THEN_COMMIT only. Returns None for other modes."""
        ...

    async def commit(
        self,
        entry: AllocationEntry,
        idempotency_key: str,
        reservation_token: ReservationToken | None = None,
    ) -> CommitReceipt:
        """Place the actual order. Receipt includes a stable order_id for cancellation."""
        ...

    async def cancel(self, receipt: CommitReceipt) -> CancellationResult:
        """Returns SUCCESS, MANUAL_REQUIRED, or NOT_SUPPORTED."""
        ...
```

**Adding a new marketplace becomes one new file** that satisfies the Protocol and one line in a `MARKETPLACES: list[MarketplaceAdapter]` registry in `__init__.py`. The Saga, the router, and the optimizer all iterate the registry — no other code touches the new marketplace.

The router's listing fetch becomes:

```python
listings_by_source = await asyncio.gather(*[
    m.fetch_listings(order_items, destination) for m in MARKETPLACES
])
merged = merge_listings(*listings_by_source)
```

### 6.2 Reservation model: per-adapter, not global

The decision of "pre-check/lock vs atomic-buy" cannot be made for the system as a whole — it must be made per marketplace. The Saga inspects `adapter.capabilities.commit_mode` and dispatches:

```
For each allocation entry:
   match adapter.commit_mode:
     RESERVE_THEN_COMMIT  → adapter.reserve() → keep token
     ATOMIC_ORDER         → no reservation; commit later relies on pre-commit revalidate
     PLAYWRIGHT_SESSION   → no reservation; commit holds an implicit cart-level lock
     CUSTOMER_REDIRECT    → no reservation; Saga returns the cart URL and exits
```

For LAIGO's actual marketplaces today:
- **LEGO.com → PLAYWRIGHT_SESSION**: cart-level implicit lock + pre-commit Playwright DOM-state check.
- **BrickOwl → ATOMIC_ORDER (eventually)** or **CUSTOMER_REDIRECT (interim)**: depends on whether `catalog/cart_basic` provides reservation TTL.
- **BrickLink → never reach commit**: pricing-only.

### 6.3 Pre-commit revalidation — the single most-impactful change

Before the Saga calls Stripe capture (or before BrickOwl `commit` for CUSTOMER_REDIRECT), a revalidation pass:

```
revalidation = await asyncio.gather(*[
    m.revalidate(entries_for_m)
    for m in MARKETPLACES_USED
])
```

Each adapter's `revalidate` does its cheapest-possible availability check:
- **LEGO.com**: one search call per element (cache-bypass, fresh). Latency ~ 100ms × elements with bounded concurrency.
- **BrickOwl**: `catalog/availability` per BOID for elements in the allocation. Same.
- **BrickLink**: N/A.

The result is one of three things:

1. **All confirmed** → proceed to commit with no allocation change.
2. **Price/quantity drift within tolerance** (configurable, e.g., total <2% above quote) → proceed, log drift, charge the *quoted* amount (LAIGO absorbs the small delta).
3. **Stockout or out-of-tolerance drift** → re-optimize with fresh data, present the customer with a "Price has changed — confirm new total?" flow rather than silently re-charging.

**Behavior #3 is missing today.** Today, allocation drift is silent (`saga.py:170-177` re-optimizes and proceeds). A "Are you sure?" round-trip costs UX latency but eliminates the silent-overcharge / silent-undercharge problem entirely.

### 6.4 The transaction wrapper (pseudo-code)

```python
# scripts/checkout/saga_v2.py

async def execute_checkout(
    job_id: str,
    checkout_id: str,
    allocation: AllocationResult,
    payment_method_id: str,
) -> None:
    """
    Crash-safe Saga with explicit commit modes per marketplace.
    All state transitions persist to outputs/{job_id}/checkout_state.json
    BEFORE any external call so resumption is always possible.
    """
    ctx = SagaContext(job_id, checkout_id, allocation)
    await ctx.persist(state=INITIATED)

    try:
        # ── Phase 1: Reservations (where supported) ───────────────────────
        # Reservations close W3 (commit-time cart snatching) for adapters
        # that support them.
        for entry in allocation.seller_allocations:
            adapter = adapter_for(entry.seller_id)
            if adapter.capabilities.commit_mode == CommitMode.RESERVE_THEN_COMMIT:
                token = await adapter.reserve(entry, ttl_seconds=SAGA_MAX_DURATION_S * 2)
                if token is None:
                    raise StockoutAtReservation(entry)
                await ctx.record_reservation(entry.seller_id, token)
        await ctx.persist(state=RESERVED)

        # ── Phase 2: Stripe hold ──────────────────────────────────────────
        # Hold AFTER reservation so we never hold money for inventory
        # we can't lock. Idempotency key survives retries.
        intent_id = await stripe.create_payment_hold(
            amount_cents=allocation.customer_total_cents,
            payment_method_id=payment_method_id,
            idempotency_key=f"hold-{checkout_id}",
        )
        await ctx.persist(state=STRIPE_HELD, stripe_intent_id=intent_id)

        # ── Phase 3: Pre-commit revalidation ──────────────────────────────
        # Fresh availability check for ATOMIC_ORDER / PLAYWRIGHT_SESSION
        # adapters. For RESERVE_THEN_COMMIT adapters this is skipped —
        # the reservation IS the validation.
        revalidation = await revalidate_non_reserved(allocation, ctx)
        if revalidation.has_drift_above_tolerance:
            # Compensate: release reservations, cancel Stripe hold.
            await abort_saga(ctx, reason="price_drift_above_tolerance")
            return

        # ── Phase 4: Commit each marketplace ──────────────────────────────
        for entry in allocation.seller_allocations:
            adapter = adapter_for(entry.seller_id)
            idempotency_key = f"commit-{checkout_id}-{entry.seller_id}"
            try:
                receipt = await adapter.commit(
                    entry=entry,
                    idempotency_key=idempotency_key,
                    reservation_token=ctx.reservation_for(entry.seller_id),
                )
                await ctx.persist_commit(entry.seller_id, receipt)
            except StockoutError as e:
                # Stockout AT commit time — adapter-specific. Compensate.
                await abort_saga(ctx, reason=f"stockout_at_commit:{e.element_id}")
                return
            except Exception as e:
                # Any other failure aborts. Compensation handles all
                # already-committed entries.
                await abort_saga(ctx, reason=f"commit_failed:{e}")
                return

        await ctx.persist(state=ALL_COMMITTED)

        # ── Phase 5: Stripe capture ───────────────────────────────────────
        # Capture is the LAST step. Orders are placed; payment claims the
        # held funds. Idempotent — safe to retry.
        await stripe.capture_payment(
            intent_id,
            idempotency_key=f"capture-{checkout_id}",
        )
        await ctx.persist(state=PAYMENT_CAPTURED, completed_at=utcnow())

    except CompensableException as e:
        await abort_saga(ctx, reason=str(e))
    except Exception as e:
        # Unknown failure — go to MANUAL_REVIEW state, do NOT auto-compensate.
        # Operator must inspect.
        await ctx.persist(state=MANUAL_REVIEW, error=str(e))
        await alert_operator(ctx, e)


async def abort_saga(ctx: SagaContext, reason: str) -> None:
    """
    Compensation: cancel commits, release reservations, cancel Stripe hold.
    Each step is best-effort and idempotent; failures are logged but do not
    block downstream compensation.
    """
    # Cancel commits in REVERSE order of placement
    for entry_id, receipt in reversed(ctx.commits()):
        adapter = adapter_for(entry_id)
        result = await adapter.cancel(receipt)
        if result == CancellationResult.MANUAL_REQUIRED:
            await alert_operator(
                ctx, f"Manual cancel required: {entry_id} order {receipt.order_id}"
            )

    # Release reservations
    for entry_id, token in ctx.reservations():
        adapter = adapter_for(entry_id)
        await adapter.release_reservation(token)  # idempotent, swallow errors

    # Release Stripe hold (idempotent)
    if ctx.stripe_intent_id:
        await stripe.cancel_payment_hold(
            ctx.stripe_intent_id,
            idempotency_key=f"cancel-{ctx.checkout_id}",
        )

    await ctx.persist(state=COMPENSATED, error=reason)
```

### 6.5 Crash recovery

The Saga must be resumable. On startup, `Main.py lifespan` runs:

```python
async def resume_in_flight_sagas():
    for state_file in glob("outputs/*/checkout_state.json"):
        state = load(state_file)
        if state["saga_status"] not in TERMINAL_STATES:
            asyncio.create_task(resume_saga(state))

async def resume_saga(state):
    match state["saga_status"]:
        case INITIATED:        # nothing external happened, restart from scratch
            await execute_checkout(...)
        case RESERVED:         # reservations exist; check TTL, decide proceed-or-abort
            if reservations_still_valid(state):
                await continue_from_stripe_hold(state)
            else:
                await abort_saga(...)
        case STRIPE_HELD:      # money held; revalidate + commit + capture
            await continue_from_revalidation(state)
        case ALL_COMMITTED:    # orders placed; just retry capture
            await retry_capture_only(state)
        case MANUAL_REVIEW:    # never resume automatically
            log(...)
        # COMPENSATED, PAYMENT_CAPTURED, FAILED: terminal, do nothing.
```

This is the **single largest pre-launch deliverable.** Without it, any unplanned process restart can leak money or inventory.

Note: this requires the checkpoint file to be on **durable storage**. On Render, that means moving from local filesystem to either a managed Postgres or a persistent disk. Pre-launch, Postgres-backed state is the right call — it also unblocks horizontal scaling (Section 6.7).

### 6.6 Per-source rate-limit budgets + shared connection pool

Replace per-call `httpx.AsyncClient` with lifespan-scoped singletons. Add a per-source `AsyncLimiter` (e.g., from the `aiolimiter` package):

```python
# scripts/checkout/http_clients.py
from aiolimiter import AsyncLimiter

_clients: dict[str, httpx.AsyncClient] = {}
_limiters: dict[str, AsyncLimiter] = {}

async def http_client_for(source: str) -> httpx.AsyncClient:
    return _clients[source]

async def request_budget(source: str) -> AsyncLimiter:
    return _limiters[source]

# usage in a client:
async def _search(element_id: str):
    async with request_budget("lego_com"):
        async with http_client_for("lego_com") as client:
            ...
```

Each marketplace adapter declares its `rate_limit_rpm` in `capabilities`; lifespan builds the limiters from those values, with a safety margin (e.g., 0.7× the documented limit). This applies globally across all in-flight quotes + Saga revalidations.

### 6.7 Persistent state

Move from local-filesystem JSON to a small Postgres schema (or SQLite + a managed disk; the schema below is the same):

```sql
CREATE TABLE checkout (
    checkout_id        TEXT PRIMARY KEY,
    job_id             TEXT NOT NULL,
    saga_status        TEXT NOT NULL,
    stripe_intent_id   TEXT,
    customer_email     TEXT NOT NULL,
    shipping_country   TEXT NOT NULL,
    shipping_zip       TEXT NOT NULL,
    quoted_total_cents INT  NOT NULL,
    charged_total_cents INT,
    error              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);
CREATE UNIQUE INDEX checkout_one_active_per_job ON checkout(job_id)
    WHERE saga_status NOT IN ('compensated', 'failed', 'payment_captured');

CREATE TABLE checkout_commit (
    checkout_id   TEXT NOT NULL REFERENCES checkout(checkout_id),
    seller_id     TEXT NOT NULL,
    order_id      TEXT NOT NULL,    -- marketplace's order ID
    idempotency_key TEXT NOT NULL,
    state         TEXT NOT NULL,    -- PLACED / CANCELLED / MANUAL_REQUIRED
    receipt_json  JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (checkout_id, seller_id, idempotency_key)
);

CREATE TABLE checkout_reservation (
    checkout_id   TEXT NOT NULL REFERENCES checkout(checkout_id),
    seller_id     TEXT NOT NULL,
    token         TEXT NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (checkout_id, seller_id)
);
```

The partial unique index on `(job_id) WHERE saga_status NOT IN (terminal_states)` **enforces "at most one active Saga per job"** at the database level, eliminating the two-tab cart-snatching race (Section 1.3) without needing application-level coordination.

### 6.8 Bounded financial exposure circuit breaker

A simple counter in Postgres:

```python
async def check_financial_circuit_breaker(amount_cents: int):
    today_total = await db.fetch_value(
        "SELECT COALESCE(SUM(quoted_total_cents),0) FROM checkout "
        "WHERE saga_status IN ('stripe_held','all_committed') "
        "AND created_at > now() - interval '1 hour'"
    )
    if today_total + amount_cents > MAX_HOURLY_AT_RISK_CENTS:
        raise CircuitBreakerOpen(...)
```

If the in-flight at-risk total exceeds a configured cap (e.g., $500 for a brand-new launch), `/confirm` returns 503 and surfaces an "we're temporarily at capacity" message. This buys a human review window for unexpected demand or a systemic failure cascading into per-customer losses.

### 6.9 Optimizer changes summary

1. Replace Pass 2's restart-after-merge with single-pass, sorted-by-shipping-cost iteration.
2. Precompute `listings_by_seller: dict[(ElementID, SellerID), SellerListing]`.
3. Capability-aware free-shipping consideration *inside* Pass 2, not as a post-pass.
4. Sanity check: reject any individual listing whose price is 10× the median or 0.1× the median; log + skip.
5. For very large mosaics (>1000 pieces), skip Pass 2 and quote with Pass 1 only.
6. Make `LAIGO fee` calculation pure and unit-tested.

### 6.10 Testing — pre-launch minimums

The optimizer is a pure function — it deserves unit tests today. Minimum coverage to ship:

| Test target | Why |
|---|---|
| `optimize()` happy-path single seller | Smoke. |
| `optimize()` shipping consolidation | Verifies Pass 2 saves money where expected. |
| `optimize()` threshold jump | Verifies free-shipping promotion. |
| `optimize()` stockout fall-through | Verifies items with no listings route to fallback. |
| `optimize()` 2000-piece × 15-seller perf | Time bound: must complete in <2s. |
| `merge_listings()` cross-source ordering | Verifies merged dict ordering invariants. |
| `compute_laigo_fee()` boundary | $3 floor at $59.99 grand total, 5% at $60.01. |
| Saga state-machine transitions | Each state has expected next-state on success and on failure. |
| Saga resumption from each non-terminal state | Resumption produces the same final state as a straight-through run. |
| Stripe-disabled-bypass should fail hard | The current silent-bypass is a deployment landmine. |

Hot-path acceptance tests (network-dependent, run in CI with a mock server):
- LEGO.com listing parse from a captured response fixture.
- BrickOwl listing parse from a captured fixture.
- Playwright order from `lego.com/en-us` against a recorded HAR.

---

## 7. Failure Mode & Effects Analysis (FMEA)

**Scoring scale (1–10 each):**

- **Severity (S):** Customer + LAIGO impact. 1 = cosmetic; 10 = LAIGO insolvency / customer chargeback / regulatory exposure.
- **Occurrence (O):** Likelihood at v1 launch given current code, no mitigations.  1 = essentially never; 10 = will happen on the first 10 orders.
- **Detection (D):** How well the *current code* detects the failure before damage compounds. 1 = caught immediately by the system; 10 = invisible until a customer or auditor complains.
- **RPN = S × O × D.** Sorted descending. Anything ≥ 200 is launch-blocking by industry convention; ≥ 100 deserves a documented mitigation; < 100 is monitor-but-acceptable.

| # | Failure mode | Cause (file:line) | Effect | S | O | D | RPN | Mitigation |
|---|---|---|---|---|---|---|---|---|
| 1 | **Stripe disabled, orders ship anyway** | `saga.py:104-106` silently swallows `NotImplementedError` from `stripe_client._check_enabled()` | LAIGO ships order with zero payment hold. Direct revenue loss per order. | 10 | 9 | 9 | **810** → **~600 in-flight** | **Layered fix in progress (see §10).** L0 `gate.py` + L1 boot assertion shipped 2026-05-15: prevents *future deploy misconfigurations* (boot refuses when `CHECKOUT_ENABLED=true` but env is incomplete, or `sk_live_` key outside Render). **L3 router 503 and L4 Saga pre-flight still pending — the actual silent-bypass code path at `saga.py:104-106` is unchanged.** Full retirement requires L3 + L4 + deleting the `except NotImplementedError: pass` blocks at `saga.py:104` and `:218`. |
| 2 | **Partial multi-seller failure leaves orders un-cancellable** | `brickowl_client.cancel_order` is a no-op stub (`brickowl_client.py:337-341`); `lego_client` has no cancel API | If N-1 of N orders succeed and the last fails, the N-1 ship and customer is refunded. LAIGO eats $X per failure. | 9 | 7 | 8 | **504** | Implement Playwright-based cancellation for BrickOwl (Option A §17.5). LEGO.com: cancellation is manual; the Saga must order LEGO.com LAST and only commit it after BrickOwl successes are confirmed. Add `commit_order` adapter property `is_reversible: bool` and order commits by reversibility (reversible first, irreversible last). |
| 3 | **Saga crashes mid-flight, no resumption** | `router.py:203-209` `asyncio.create_task` is fire-and-forget; no startup resume code reads `outputs/{job_id}/checkout_state.json` | Funds held on customer card for up to 7 days; orders may or may not have been placed. Customer support burden + chargebacks. | 10 | 5 | 9 | **450** | Implement Section 6.5 resumption. Move state to Postgres so it survives Render restarts. |
| 4 | **Listing data is up to 70 min stale at commit time** | 1h `BRICKOWL_CACHE_TTL_SECONDS` + 10min quote window + ~10min saga duration; no pre-commit revalidation | Customer is charged for inventory that's gone. Stockout retry helps, but doesn't help LEGO.com (single Playwright session, no retry-after-stockout path). | 8 | 7 | 8 | **448** | Implement Section 6.3 pre-commit revalidation. Shrink cache TTL during checkout flows specifically (use `cache_get_fresh(key, max_age=60)` for revalidation reads). |
| 5 | **Concurrent quotes/confirms for the same job** | `router.py:182-184` only locks `checkout_id`, not `job_id`; no database constraint | Customer pays 2× and receives 2× kits, or one Saga's Playwright session corrupts the other's cart. | 9 | 4 | 9 | **324** | Partial-unique-index in Postgres (Section 6.7). Application-level: reject `/confirm` if any non-terminal Saga exists for the job_id. |
| 6 | **LEGO.com Playwright duplicate order on retry** | No idempotency in `lego_client.order_from_lego`; network glitch + Saga retry = two cart submissions | Customer pays once, ships twice. Direct revenue loss to LAIGO. | 9 | 4 | 8 | **288** | Before clicking "Place Order," check the customer's recent-order list on LEGO.com via Playwright; abort if an order with matching item count + total appeared in the last 30 minutes. This is imperfect but the best Playwright can do. Add the Saga's `checkout_id` to the Playwright session's order notes if LEGO.com supports a note field. |
| 7 | **Capture fails after orders placed** | `saga.py:217-233` logs error, sets FAILED, returns. No retry, no alert | Orders placed, payment unreceived. LAIGO eats cost. | 9 | 4 | 7 | **252** | Add retry-with-backoff on capture (Stripe capture is idempotent). Add operator alert via webhook on capture failure. Add a `MANUAL_REVIEW` terminal state separate from `FAILED` so a runbook can find these cases. |
| 8 | **LEGO.com search API blocks/bans LAIGO IP** | No rate limiting, generic Mozilla UA, unbounded concurrent requests (`lego_client.py:83-117`) | Entire LEGO.com sourcing goes dark. All quotes fail `can_proceed=false`. Launch-day outage. | 8 | 5 | 6 | **240** | Add `AsyncLimiter` for LEGO.com (e.g., 30 req/min). Use a real LAIGO-branded UA with contact email. On 403/429, exponential backoff with jitter, raise a circuit breaker after 5 consecutive failures. |
| 9 | **Allocation drift mid-Saga undercharges customer** | Stockout retry re-runs `optimize()` and stamps new total to `total_charged_cents` (`saga.py:235-237`), but Stripe captures held amount only | Customer authorized $100, new optimization needs $130, only $100 captured. LAIGO eats $30. | 7 | 5 | 7 | **245** | If revalidation produces a higher total, fail closed: abort and re-quote, do not proceed with old hold. Codify allowed drift tolerance (e.g., -10% to 0%); anything else triggers a customer "Confirm new price?" round-trip. |
| 10 | **Listing parse error silently sets price to 0 or 100×** | `lego_client._parse_price_cents` (`lego_client.py:134-166`) tries multiple field names, one of which interprets dollars as cents on misfield | Customer is quoted/charged 0× or 100× actual. Optimizer trusts the value blindly. | 9 | 3 | 9 | **243** | Sanity-check at parse: reject prices outside [1¢, $1000¢]. Reject any listing whose price is 10× median across all listings for that element. Fail the quote if no parse succeeds; do not default to 0. |
| 11 | **Pass 2 optimizer latency on large orders** | O(S³ · N · L) with restart-after-merge (`optimizer.py:114-166`) | 2000 pieces / 15 sellers: 5–15s pure-Python work inside the quote endpoint. Customer drops off. | 5 | 6 | 5 | **150** | Section 6.9 — single-pass Pass 2, precomputed seller index. Cap Pass 2 at S=10 or skip it for orders >1000 pieces. |
| 12 | **BrickOwl 403 on catalog/availability is permanent** | API access not granted; `brickowl_client.py:184-191` returns `[]` for every element | All quotes route to LEGO.com only. Shipping-consolidation benefits don't materialize until access is granted. | 4 | 9 | 2 | **72** | Get BrickOwl access *before* launch. Until then, document the LEGO.com-only behavior in the quote response. |
| 13 | **In-process cache and per-job-lock dict don't survive restart** | `cache.py` is in-process; `checkout_store._locks` is in-process | Cache cold-starts hurt latency briefly. Locks evaporate — but the only thing they guard is concurrent state writes for one job, which the database constraint will replace. | 3 | 8 | 3 | **72** | Acceptable v1. Move to Redis when multi-instance scaling becomes real. |
| 14 | **`_search` returns None for transient LEGO.com error, treats as unsourceable** | `lego_client.py:115-117` swallows all exceptions and returns None | A 503 from LEGO.com during quote falsely fails the entire quote with `can_proceed=false`. | 6 | 5 | 4 | **120** | Distinguish "definitely unavailable" from "couldn't check"; surface the latter as a quote-level error ("LEGO.com unreachable, please retry") rather than rolling it into `unsourceable_items`. |
| 15 | **Quote freshness gate is 10min, but underlying data is 60min** | `quote:{checkout_id}` TTL 600s; `lego_raw` / `brickowl_listings` TTL 3600s | Customer sees "price valid for 10 minutes" but the price they confirmed could have been generated from 70-minute-old data. | 6 | 7 | 3 | **126** | Either reduce listing-cache TTL to 60s during a `/quote` call (with a separate "warm" cache for autocomplete/preview), or include "data age" in the quote response and let the frontend communicate it. |
| 16 | **`checkout_store._locks` grows unbounded** | `checkout_store.py:31-34`; `_locks[job_id] = asyncio.Lock()` never removed | Slow memory leak; one Lock per ever-quoted job. | 2 | 10 | 4 | **80** | LRU eviction or clear-on-job-purge integration with `Main.py`'s cleanup thread. Subsumed by Postgres-backed state. |
| 17 | **`asyncio.Lock` cross-event-loop breakage on multi-worker** | `checkout_store.py:31-34`; Lock bound to event loop of first caller | Multi-worker uvicorn behaviour undefined. | 7 | 2 | 8 | **112** | Postgres advisory locks (`pg_advisory_xact_lock(hashtext(job_id))`) instead of in-memory locks. Subsumed by Section 6.7. |
| 18 | **Concurrent LEGO.com Playwright sessions share one account** | `lego_client.LEGO_EMAIL/PASSWORD` is module-level; no per-Saga serialization | LEGO.com may flag the account for concurrent logins; cart state could corrupt between sessions. | 7 | 4 | 6 | **168** | Application-level `asyncio.Lock` serializing all LEGO.com commits (one at a time). Already implicit if only one Saga runs at a time per Section 6.7's job-uniqueness constraint, but LEGO.com is shared *across* jobs, so a separate per-source `Semaphore(1)` is also needed. |
| 19 | **No bounded financial exposure** | Nothing caps total in-flight at-risk amount | A bug causing 100 simultaneous "orders placed, capture failed" cases can produce 5-figure losses before anyone notices. | 9 | 3 | 8 | **216** | Section 6.8 circuit breaker. |
| 20 | **Threshold-jump optimization gap** | `apply_free_shipping_thresholds` runs after `optimize`, can't influence allocation; Pass 2 doesn't know about free-ship thresholds (`optimizer.py:111-166`) | Suboptimal allocation; customer pays $5-15 more than necessary on borderline cases. | 4 | 6 | 5 | **120** | Section 6.9: capability-aware free-shipping consideration inside Pass 2. |
| 21 | **No tests** | No `tests/` directory exists | Refactors break things silently. Pre-launch confidence is low. | 7 | 9 | 3 | **189** | Section 6.10 minimum coverage. |
| 22 | **Cancellation receipts have no audit trail** | `_compensate()` logs but does not persist which orders were cancelled vs failed-to-cancel (`saga.py:37-63`) | After a partial-failure storm, operators can't tell which seller orders shipped vs were cancelled. | 6 | 4 | 7 | **168** | `checkout_commit.state` column (Section 6.7) records PLACED/CANCELLED/MANUAL_REQUIRED with timestamps. |
| 23 | **Multi-source unsourceable items hide LEGO.com pricing-API outage** | `router.py:103-108` treats anything not in `seller_allocations` as unsourceable, mixing "no source has this" with "the only source failed transiently" | False negatives block confirmation. | 5 | 4 | 5 | **100** | Pair-up unsourceable items with their *reason* (`{"reason": "source_unavailable"|"genuinely_out_of_stock"}`). |
| 24 | **Customer never receives marketplace confirmation** | Orders are placed on LAIGO's accounts; customer email is stored but unused | Customer has no proof of purchase from BrickOwl/LEGO; trust impact at scale. | 4 | 10 | 1 | **40** | Send a LAIGO confirmation email post-`PAYMENT_CAPTURED` including the per-seller order IDs. Lower priority. |
| 25 | **BrickOwl cart-URL strategy (Option B) splits payment surface** | Documented in `docs/ORDER_OPTIMIZER.md §17.2` Option B; not yet implemented | If Option B is chosen, Stripe holds only the LEGO.com portion; BrickOwl portion is collected by the customer at BrickOwl. Audit trail bifurcates. | 5 | 4 | 6 | **120** | Decision: choose Option A (Playwright) for parity, accept the Playwright fragility cost, and tag this risk closed. If Option B is chosen, formalize the bifurcated payment audit. |

**Sorted by RPN (top 10):**

| Rank | # | Failure mode | RPN |
|---|---|---|---|
| 1 | 1 | Stripe disabled, orders ship anyway | **810** |
| 2 | 2 | Partial multi-seller failure leaves orders un-cancellable | **504** |
| 3 | 3 | Saga crashes mid-flight, no resumption | **450** |
| 4 | 4 | Listing data up to 70 min stale at commit | **448** |
| 5 | 5 | Concurrent quotes/confirms for same job | **324** |
| 6 | 6 | LEGO.com Playwright duplicate on retry | **288** |
| 7 | 7 | Capture fails after orders placed | **252** |
| 8 | 9 | Allocation drift mid-Saga undercharges | **245** |
| 9 | 10 | Listing parse error 0× / 100× price | **243** |
| 10 | 8 | LEGO.com IP banned | **240** |

**Six failure modes have RPN ≥ 300 — all six are launch-blocking by the convention used.**

---

## 8. Prioritized roadmap to v1

The order below trades off RPN reduction per engineering-hour. Items 1–6 must be done before the first real $1 moves.

| # | Item | RPN(s) addressed | Effort | Notes |
|---|---|---|---|---|
| 1 | Replace Stripe-disabled silent-bypass with a layered defense (gate + boot + router + Saga + provider + audit) | #1 (810) | S–M | **In progress.** L0 + L1 shipped 2026-05-15. L3 + L4 are what actually close the bypass; L2/L5/L6 are scalability + observability. See §10 for layer-by-layer status. |
| 2 | Move Saga state to Postgres; add resume-on-startup; add partial-unique-index per-job | #3 (450), #5 (324), #17 (112), #16 (80) | M | Single most-impactful pre-launch change. |
| 3 | Implement pre-commit revalidation per adapter | #4 (448), #14 (120), #15 (126) | M | Roughly half the cart-snatching window closes here. |
| 4 | Marketplace adapter Protocol; refactor LEGO.com + BrickOwl into adapters | #2 (504) prep; #20 (120); structural | M | Required for everything else to compose cleanly. |
| 5 | Implement BrickOwl Playwright cancellation (Option A §17.5); reorder commits so reversible ones happen first | #2 (504) | L | Cannot launch without this if BrickOwl is in the loop. |
| 6 | Capture retry with backoff + operator alert; explicit `MANUAL_REVIEW` state | #7 (252) | S | |
| 7 | Per-source rate-limit budgets via `AsyncLimiter` + lifespan HTTP clients | #8 (240) | S | |
| 8 | Listing parse sanity checks (price/qty bounds) | #10 (243) | S | |
| 9 | Optimizer Pass 2 redesign + sanity tests | #11 (150), #20 (120) | M | |
| 10 | Bounded financial exposure circuit breaker | #19 (216) | S | |
| 11 | Test suite covering Section 6.10 minimums | #21 (189) | M | |
| 12 | Drift tolerance + customer "confirm new price" round-trip | #9 (245) | M | |
| 13 | LEGO.com semaphore + recent-order-list duplicate-detection heuristic | #6 (288), #18 (168) | M | |
| 14 | Customer confirmation email | #24 (40) | S | |

**"S" = ≤1 engineer-day, "M" = ≤1 engineer-week, "L" = >1 engineer-week.**

The minimum-viable pre-launch is items 1–7. Items 8–14 are quality-of-life and reducing tail-risk.

---

## 9. Appendix — open questions for product

These are product-strategy questions that the audit surfaced but cannot answer alone. They should be resolved before v1 ships:

1. **BrickOwl ordering strategy (§17.2):** Option A (Playwright; LAIGO collects payment) vs Option B (Cart URL redirect; customer pays BrickOwl directly). Option A preserves the "fully automated" promise but exposes LAIGO to Playwright fragility and float-financing the BrickOwl portion. Option B preserves the brand promise less perfectly but eliminates ~half the Saga complexity.
2. **Allocation drift tolerance:** When fresh revalidation produces a higher total, does LAIGO eat the small delta (≤2%), or does the customer get a "confirm new price" prompt unconditionally? The answer drives both Saga design and customer-facing flow.
3. **Hourly financial exposure cap:** What is the maximum at-risk amount that should be permitted before `/confirm` returns 503? $500? $5000? This is a business risk-appetite question, not an engineering one.
4. **Customer concurrency policy:** If a customer has an active Saga for a job, is a second `/quote` for the same job (different shipping address, different country, etc.) permitted, or is the job locked until the Saga terminates?
5. **Refund policy on commit-but-not-capture failures:** When orders are placed and capture fails, does LAIGO contact the customer for an alternate payment, refund the marketplace orders (eating cost), or both?

**Resolved 2026-05-15 (partial):**
- Merchant of record: **LAIGO is the reseller.** Customer pays LAIGO via Stripe for bricks + shipping + service fee; LAIGO uses its own marketplace accounts to place supplier orders.
- Partial-fail policy: **all-or-nothing.** Any unrecoverable partial failure triggers compensation + full customer refund. This guarantee is conditional on real cancellation working (RPN #2) — see §10.
- Authorization buffer: **5%.** Stripe hold is `1.05 × quote`; capture is for the exact allocated total; the unused authorization decays. Standard pattern; Stripe permits capturing less than authorized.

---

## 10. Implementation status — RPN #1 layered fix

The fix for RPN #1 ("Stripe disabled, orders ship anyway") is a six-layer defense-in-depth design, not a single edit. Layers fire at different times against different failure scenarios. No single layer covers all of them; together they make a silent-bypass regression structurally impossible.

### Threat model (re-stated)

| ID | Scenario | Today's behavior |
|----|---|---|
| T1 | `STRIPE_ENABLED=False` in code, customer hits `/confirm` in dev | **Places orders at $0 charged** (the bug) |
| T2 | `STRIPE_ENABLED=True` but `STRIPE_SECRET_KEY` missing or wrong prefix | Fails at first Stripe call; orders may be queued first |
| T3 | Live Stripe API unreachable mid-Saga | Silent catch swallows the error |
| T4 | Future code path invokes Saga directly (admin tool, batch, new endpoint) | No defense at all |
| T5 | New payment provider added later (PayPal, etc.) without going through the gate | Silent bypass returns |
| T6 | Server resumes a Saga after restart with stale "hold skipped" state | Resumes against a hold that never existed |
| T7 | Env drift on Render — `CHECKOUT_ENABLED=true` set but key not rotated | Boots, accepts confirms |

### Layer status

| Layer | What it does | Status | Blocks |
|---|---|---|---|
| **L0** Gate module (`scripts/checkout/gate.py`) | Single source of truth: `compute_decision()`, `require_open()`, `GateClosedError`, `CheckoutMode {DISABLED, TEST, LIVE}` | ✅ **Shipped 2026-05-15** | Foundation for all other layers |
| **L1** Boot assertion (`scripts/Main.py` lifespan) | Logs gate state on boot; refuses to start if `CHECKOUT_ENABLED=true` but gate is DISABLED; refuses any `sk_live_` outside Render | ✅ **Shipped 2026-05-15** | T2, T7 |
| **L2** Health endpoint (`/health/checkout`) | Operational visibility — returns gate JSON for Render healthcheck / dashboards | ❌ Not built | Post-deploy regression detection |
| **L3** Router gate (`/confirm` returns 503) | HTTP-layer short-circuit when gate is DISABLED | ❌ Not built | T1 at HTTP layer, T4 for routes that go through router |
| **L4** Saga pre-flight (`gate.require_open()` + delete `except NotImplementedError: pass` at `saga.py:104,218`) | Refuses to advance before any external call. **The actual bypass closure.** | ❌ Not built | T1, T3, T4, T6 — the keystone layer |
| **L5** PaymentProvider Protocol + registry | No stub providers exist; `get_active()` raises if none constructed. Each provider's `__init__` validates SDK + env atomically. | ❌ Not built | T1, T5 |
| **L6** Audit log (`audit.emit()` at every Saga state transition) | `hold.skipped` event would be the unmistakable signal of a regression — should be permanently zero in prod | ❌ Not built | Post-hoc detection of any future regression |

### What changed today (concretely)

- **`scripts/checkout/gate.py`** (new): pure-function `compute_decision()` returns a frozen `GateDecision` carrying mode + reasons + payment_provider + marketplaces_live. `require_open()` is the enforcement primitive for Saga + router; raises `GateClosedError` (does not inherit from `NotImplementedError` so existing silent-catches can't swallow it). `is_truthy()` is exposed publicly for env-flag parsing.
- **`scripts/Main.py`** lifespan: logs the gate decision at every boot, refuses to boot if env-says-enabled-but-reality-disagrees. Mirror of `stripe_client._log_mode()`'s live-key safeguard, evaluated at boot rather than first Stripe call.

### Antipatterns identified during review

| Tag | Issue | Disposition |
|---|---|---|
| P1 | RPN #1 not fully retired by L0+L1 alone; the bypass code path at `saga.py:104-106` is unchanged | Documented above; L3+L4 will close |
| P2 | `_is_truthy` duplicated between gate and Main.py | **Fixed** — `is_truthy()` exported from gate |
| P3 | "Live Stripe key outside Render" rule lives in 3 sites (`stripe_client._log_mode`, gate, Main.py boot) | **Intentional** — defense in depth. Each fires at a different time. `stripe_client._log_mode` becomes redundant when L5 lands and `StripeProvider.__init__` subsumes its checks |
| P4 | Gate hardcodes marketplace credential probes (`BRICKOWL_API_KEY`, `LEGO_EMAIL`+`LEGO_PASSWORD`) | TODO comment in gate.py; resolved when L5 MarketplaceAdapter Protocol lands and adapters declare their own `is_configured()` |
| P5 | `compute_decision()` called twice on boot | Acceptable — pure function |
| I1 | `saga.py:58` (`_compensate`) catches `NotImplementedError` on Stripe cancel | Will be removed in same commit as L4 |
| I2 | `stripe_client._log_mode()` redundant with gate's check | Keep until L5 supersedes it |
| I3 | `CLAUDE.md` stale (no mention of gate.py) | **Fixed** 2026-05-15 |
| I4 | `docs/ORDER_OPTIMIZER.md` line 465 calls dev-mode bypass "safe for development" | Update when L4 lands and the wording becomes false |

### What "RPN #1 retired" requires

L3 + L4 in a single commit:
1. `router.confirm_checkout`: call `compute_decision()`, return 503 with the gate's reasons if not `is_open`.
2. `saga.execute_checkout_saga`: call `require_open()` at the top, before any state mutation or external call.
3. Delete `try/except NotImplementedError: pass` at `saga.py:104-106` and `:218-219`.
4. Delete `try/except NotImplementedError: pass` at `saga.py:58` (Stripe cancel in compensate).

Estimated effort: 45 minutes including a manual test of `/confirm` against the four states (DISABLED, TEST, LIVE, gate-flips-after-quote).

These should be written into a brief product doc and referenced from the implementation tickets so they don't get rediscovered ad-hoc during build.
