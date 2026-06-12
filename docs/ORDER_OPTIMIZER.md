# Order Optimizer — Operator & Developer Reference

## 1. Overview

After a LAIGO mosaic job completes, the order optimizer automates the entire LEGO piece purchasing workflow on the customer's behalf.

**Three-step customer flow:**

1. **Quote** — The customer calls `POST /quote` with their shipping country. The server fetches live listings from all active sourcing platforms concurrently, runs a two-pass greedy allocation to minimize total cost (piece prices + per-seller shipping), and returns a priced breakdown. No money moves. Quote valid 10 minutes.

2. **Confirm** — The customer approves the quote and calls `POST /confirm` with a Stripe payment method token. The server immediately returns a poll URL and launches the checkout Saga as a background task.

3. **Saga** — Holds funds via Stripe (hold, not charge), places orders with each seller, then captures (charges) the Stripe payment. The customer polls `GET /status` until `payment_captured`.

**LAIGO fee:** `max($3.00, 5% of piece+shipping total)` added to customer total before any charge.

### Current MVP state

LEGO.com Pick-a-Brick is the only active sourcing platform. BrickOwl and BrickLink are implemented but gated pending setup (see Section 6). When both come online, the optimizer will allocate across all three sources automatically — no architectural changes needed.

---

## 2. Architecture

```
                         ┌──────────────────────────────────────────────────────────┐
POST /jobs/{id}/quote    │  fetch listings from all active clients (concurrent)      │
  ─────────────────────► │    lego_client.get_all_listings()     → active (MVP)      │
                         │    brickowl_client.get_all_listings() → blocked (403)     │
                         │    bricklink_client.get_all_listings()→ disabled (setup)  │
                         │                                                            │
                         │  merge_listings() → optimize() → apply_free_shipping()   │
                         └──────────────────────────────────────────────────────────┘
                               ▲                        ▲
                         LEGO.com internal         In-process TTL
                         search API                cache (cache.py)


                         ┌──────────────────────────────────────────────────────────┐
POST /jobs/{id}/confirm  │  load cached quote → save state → launch Saga task       │
  ─────────────────────► │                                                          │ ─► 202 + poll_url
                         └──────────────────────────────────────────────────────────┘
                               │
                               ▼ (background asyncio task)
                         ┌───────────────────────────────────────┐
                         │  Saga (saga.py)                        │
                         │                                        │
                         │  1. Stripe hold (PaymentIntent)        │
                         │  2a. BrickOwl sub-orders (pending)     │
                         │      └─ stockout retry (max 2)         │
                         │  2b. LEGO.com order (Playwright)       │
                         │  3. Stripe capture                     │
                         │                                        │
                         │  Routing by seller_id prefix:          │
                         │    "lego_official"  → lego_client      │
                         │    "brickowl_*"     → brickowl_client  │
                         │    "bricklink_*"    → bricklink_client │
                         │                                        │
                         │  Checkpoints to:                       │
                         │  outputs/{job_id}/checkout_state.json  │
                         └───────────────────────────────────────┘


GET /jobs/{id}/checkout/{co_id}/status
  ─────────────────────► read checkout_state.json ─► CheckoutStatusResponse
```

### File layout (`scripts/checkout/`)

| File | Role |
|---|---|
| `router.py` | FastAPI routes; fetches + merges listings from all clients, runs optimizer |
| `models.py` | All Pydantic models and `StockoutError` exception |
| `optimizer.py` | Two-pass greedy allocator — pure function, no I/O |
| `saga.py` | Saga orchestrator; routes orders by seller_id prefix |
| `cache.py` | In-process TTL cache; sweep task started from `Main.py lifespan` |
| `checkout_store.py` | Disk-backed (JSON) checkout state. Used when `DB_BACKEND=json`. |
| `checkout_store_pg.py` + `checkout_store_dispatch.py` | Postgres backend + runtime dispatcher (Phase C, 2026-05-18). Same `load/save/update` API. Used when `DB_BACKEND=postgres`. |
| `payment_holds_store.py` | `payment_holds` reconciliation index. Wired at saga create_hold (INSERT) + capture/cancel (UPDATE). Phase E partial — `reconcile_orphan_holds()` still pending. |
| `saga_resume.py` | Boot-time recovery: routes every non-terminal saga to a terminal state at lifespan startup. Phase E step 1. |
| `audit.py` | L6 structured audit log — writes events to `audit_events` table. First call site (`gate.confirm_rejected`) wired; rest tracked in PRE_RELEASE_PAYMENT_CHECKLIST.md §2.6. |
| `payment/base.py` | Provider Protocol + `PaymentHold` value type + retryable/permanent/unavailable exception hierarchy. Dependency-free (no SDK imports). |
| `payment/registry.py` | Single-active provider registry: `register()`, `get_active()`, `is_configured()`, `active_name()`, `active_mode()`. Populated once per process at lifespan startup. |
| `payment/stripe_provider.py` | `StripeProvider` implementing the Protocol. Houses the `STRIPE_ENABLED` operator flag. Translates Stripe error classes into Retryable/Permanent. **Not the safety boundary** — see `gate.py`. |
| `gate.py` | Layered checkout-gate single source of truth (`CheckoutMode`, `compute_decision`, `require_open`, `GateClosedError`). Reads `payment.registry`. Read this and `docs/CHECKOUT_AUDIT.md §10` before touching checkout flow. |
| `gate_router.py` | `GET /checkout/gate` — public, always 200, no cache. Reports gate state. **Render's healthcheck must stay on `/health`** (not `/checkout/gate`). |
| `dependencies.py` | FastAPI dependencies for checkout. `require_checkout_gate_open` gates `/confirm` (Layer 3) with 503 when gate closed. |
| `debug_router.py` | Swagger test endpoints (prefix `/checkout-debug`) |
| `clients/__init__.py` | Empty; makes `clients` a sub-package |
| `clients/lego_client.py` | LEGO.com: pricing, availability, Playwright ordering |
| `clients/brickowl_client.py` | BrickOwl: two-step listing lookup (pending API access) |
| `clients/bricklink_client.py` | BrickLink: stub (pending seller account + setup) |

---

## 3. Implementation Status

| Feature | Status | Blocker |
|---|---|---|
| LEGO.com pricing via search API | ✅ Implemented | Field names unverified — test `GET /checkout-debug/lego/element/{id}/listing` |
| LEGO.com availability check | ✅ Implemented | Endpoint unverified against live traffic |
| LEGO.com Playwright login | ✅ Superseded by `storage_state` seeding | Headless login is impossible (email-only 2FA). Production loads a cached session from Neon (`external_sessions`), seeded once via `python -m scripts.seed_lego_session`. `_run_checkout` Step A only probes `/profile`. |
| LEGO.com Playwright cart upload (Step C) | ✅ Verified + ported 2026-06-10 | "Upload List" → file input → "Add to Bag" selectors confirmed against live PaB and ported into `_run_checkout`. See §6.1 Step 3. |
| LEGO.com Playwright checkout (Step D) | ✅ Verified + ported 2026-06-11 | Add to Bag → "Updated My Bag" modal → View My Bag → `/cart` → `checkout-securely-button-desktop`. See §6.1 Step 4. Step E (payment/place-order) deliberately not captured — gated. |
| LEGO.com Playwright — headless Cloudflare wall | ⛔ **Prod blocker (2026-06-11)** | Cloudflare hard-blocks **headless** on `pick-a-brick`; only **headed** passes (stealth drivers incl. patchright all fail — see §6.1 probe matrix). **Chosen fix: VPS + residential-proxy headed browser reached over CDP** (`LEGO_BROWSER_CDP_URL`). Design + runbook: `docs/LEGO_BROWSER_HOST.md`. Provisioning is the next step; `§7` lists the decisions needed. |
| LEGO.com Playwright place-order click | ⛔ Gated | Payment-architecture decision 2026-05-31: LAIGO uses its own saved card and accepts chargeback liability — but the click that places a real order is intentionally not wired until the end-to-end test strategy is finalized (sandbox? throwaway low-$ orders cancelled via lego.com/profile/orders?). |
| BrickOwl BOID lookup | ✅ Working | — |
| BrickOwl catalog/availability | ❌ Blocked | Must contact BrickOwl at brickowl.com/contact to request access |
| BrickOwl order placement | ❌ No API | BrickOwl is a seller API; no buyer order/create endpoint exists |
| BrickLink price guide | ❌ Not started | Need seller account + API credential setup |
| BrickLink order placement | ❌ Not started | Need seller account; BrickLink API does not expose per-store listings |
| Stripe payment hold/capture | ✅ Real | Wired via `StripeProvider`. Set `STRIPE_ENABLED = True` in `payment/stripe_provider.py` AND `STRIPE_SECRET_KEY=sk_test_...` in env to enable. |
| Capture retry + MANUAL_REVIEW | ✅ Working | 3 attempts on transient Stripe errors (`APIConnectionError`/`RateLimitError`/`APIError`); backoff 1s/4s/16s; permanent errors skip retry. Exhaust → `SagaStatus.MANUAL_REVIEW`. |
| Hold authorization buffer | ✅ 5% | Hold = `ceil(quote_total × 1.05)`; capture = actual allocated total. Capture > authorized → MANUAL_REVIEW. |
| LEGO.com free shipping threshold ($35) | ✅ Working | Applied automatically; recalculates all downstream totals |
| Multi-seller shipping consolidation | ✅ Working | Only effective once BrickOwl/BrickLink return per-seller listings |
| Unsourceable piece blocking | ✅ Working | Blocks `/confirm` if any piece unavailable on all active sources |

---

## 4. First-Time Setup

### 4.1 Secrets (`.env.secrets` — gitignored, never commit)

```
BRICKOWL_API_KEY=<from brickowl.com/developer>
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
# DEPRECATED for ordering — login is now via storage_state seeding
# (docs/LEGO_SESSION.md). Still read by gate.compute_decision() for the
# marketplaces_live check, so keep them set until the gate is refactored.
LEGO_EMAIL=<LAIGO LEGO.com account email>
LEGO_PASSWORD=<LAIGO LEGO.com account password>
```

BrickLink keys (add when ready):
```
BRICKLINK_CONSUMER_KEY=
BRICKLINK_CONSUMER_SECRET=
BRICKLINK_TOKEN=
BRICKLINK_TOKEN_SECRET=
```

### 4.2 Install dependencies

```bash
pip install httpx~=0.27 stripe~=10.0 playwright~=1.44
playwright install chromium
```

### 4.3 LEGO.com account requirements

LAIGO's LEGO.com account must have a saved payment method (credit card). Automated checkout in `lego_client.order_from_lego()` clicks "Place Order" against whatever payment method is saved — there is no way to specify a different card at Playwright runtime without selecting it explicitly.

### 4.4 First-run verification

Start the server (`uvicorn Main:app --reload` from `scripts/`) and hit these debug endpoints in order:

1. `GET /checkout-debug/lego/element/302421/listing` — verify LEGO.com pricing is parsed. If `price_per_cent` is 0, the API response field names have changed; see Section 6.1.
2. `GET /checkout-debug/lego/element/302421` — verify availability check returns `available_on_lego: true`.
3. `GET /checkout-debug/brickowl/element/302421/raw` — verify BOID lookup works and note the `step2_error` (expected `403` until catalog access is granted).
4. `POST /checkout-debug/job/{job_id}/optimize` with a completed job — verify full optimization preview returns sensible prices.
5. `POST /jobs/{job_id}/checkout/quote` — end-to-end quote.

---

## 5. Environment Variables Reference

| Variable | File | Secret | Default | Effect |
|---|---|---|---|---|
| `BRICKOWL_API_KEY` | `.env.secrets` | YES | — | Required for all BrickOwl calls |
| `STRIPE_SECRET_KEY` | `.env.secrets` | YES | — | Stripe key (`sk_test_...` or `sk_live_...`) |
| `STRIPE_PUBLISHABLE_KEY` | `.env.secrets` | YES | — | Frontend Stripe.js key (not used server-side) |
| `STRIPE_WEBHOOK_SECRET` | `.env.secrets` | YES | — | Webhook signature validation (optional) |
| `LEGO_EMAIL` | `.env.secrets` | YES | — | **Deprecated for ordering** (storage_state seeding replaced it). Still read by `gate.compute_decision()`. |
| `LEGO_PASSWORD` | `.env.secrets` | YES | — | **Deprecated for ordering** (see `LEGO_EMAIL`). |
| `LEGO_BROWSER_CDP_URL` | Render env | — (URL embeds token) | — (unset) | If set, `order_from_lego` connects to a remote headed browser over CDP instead of launching locally — the Cloudflare-bypass path. See `docs/LEGO_BROWSER_HOST.md`. ⚠️ embeds the browserless token; treat as sensitive. |
| `LEGO_PLAYWRIGHT_HEADLESS` | `.env` / Render env | no | `true` | Local-launch headless toggle (ignored when `LEGO_BROWSER_CDP_URL` is set). Must be `false` for a headed Xvfb deploy. |
| `BRICKLINK_CONSUMER_KEY` | `.env.secrets` | YES | — | BrickLink OAuth consumer key (not yet used) |
| `BRICKLINK_CONSUMER_SECRET` | `.env.secrets` | YES | — | BrickLink OAuth consumer secret (not yet used) |
| `BRICKLINK_TOKEN` | `.env.secrets` | YES | — | BrickLink OAuth token (not yet used) |
| `BRICKLINK_TOKEN_SECRET` | `.env.secrets` | YES | — | BrickLink OAuth token secret (not yet used) |
| `BRICKOWL_BASE_URL` | `.env` | no | `https://api.brickowl.com/v1` | Override for mocking |
| `BRICKOWL_CACHE_TTL_SECONDS` | `.env` | no | `3600` | How long BrickOwl listings are cached |
| `BRICKOWL_REQUEST_TIMEOUT_SECONDS` | `.env` | no | `10` | Per-request timeout for BrickOwl API |
| `BRICKOWL_CONCURRENT_REQUEST_LIMIT` | `.env` | no | `10` | Max parallel BrickOwl requests (asyncio Semaphore) |
| `OPTIMIZER_MAX_STOCKOUT_RETRIES` | `.env` | no | `2` | Max re-optimizations after a stockout before Saga fails |
| `LEGO_SHIPPING_COST_CENTS` | `.env` | no | `599` | Flat shipping charge for LEGO.com orders ($5.99) |
| `LEGO_FREE_SHIPPING_THRESHOLD_CENTS` | `.env` | no | `3500` | Orders ≥ this many cents get free LEGO.com shipping ($35.00) |
| `LEGO_MAX_QTY_PER_ITEM` | `.env` | no | `9999` | Available quantity cap per LEGO.com element (stock not exposed by API) |
| `LEGO_FALLBACK_TIMEOUT_SECONDS` | `.env` | no | `90` | Playwright page load / action timeout |
| `STRIPE_CURRENCY` | `.env` | no | `usd` | Stripe charge currency |
| `LAIGO_FEE_MIN_CENTS` | `.env` | no | `300` | Minimum LAIGO service fee in cents ($3.00) |
| `LAIGO_FEE_PERCENT` | `.env` | no | `5` | LAIGO fee as percentage of grand total |
| `BRICKLINK_ENABLED` | `.env` | no | `false` | Set `true` to activate BrickLink client |

---

## 6. Sourcing Clients (`scripts/checkout/clients/`)

All clients implement the same interface for the optimizer:

```python
async def get_all_listings(
    order_items: list[dict],      # [{elementId: str, quantity: int}, ...]
    shipping_country: str,        # ISO 3166-1 alpha-2
    shipping_zip: str,
    cache_ttl: int = 3600,
) -> dict[str, list[SellerListing]]   # elementId → [SellerListing, ...]
```

`router.py` calls all three concurrently, merges the results via `merge_listings()` (in `optimizer.py`), and passes the combined dict to `optimize()`. Items with no listings from any client become `unsourceable_items` and block `/confirm`.

### 6.1 LEGO.com (`lego_client.py`)

**Seller ID:** `"lego_official"`

**Pricing and availability:** Uses LEGO.com's internal Pick-a-Brick GraphQL endpoint (derived from browser network traffic — not a public API):

```
POST https://www.lego.com/api/graphql/PickABrickQuery
Content-Type: application/json
Body: {"operationName":"PickABrickQuery", "variables":{"input":{"query":"{element_id}", ...}}, "query":"<full GraphQL>"}
```

Anonymous calls work — no Bearer JWT, no session cookie — but the request must carry browser-realistic headers (`Origin`, `User-Agent`, `x-locale`) and the EXACT GraphQL query string LEGO's frontend emits. Simplifying the query (removing `__typename` selections or fragments) returns HTTP 400 "Validation error". The query string lives in `_LEGO_GRAPHQL_QUERY` in `clients/lego_client.py`.

LEGO retired the legacy REST search (`api/product/search/en-US`) in 2026-05; the silent symptom was every quote routing to unsourceable (B64). The `marketplace.endpoint_unavailable` audit event (alerts P0) now fires on the next such outage.

To re-derive if the endpoint moves again:
1. Open browser DevTools → Network on `lego.com/en-us/pick-and-build/pick-a-brick`
2. Search for any element ID
3. Find the `PickABrickQuery` POST → Payload → copy the JSON body verbatim
4. Update `_LEGO_SEARCH_URL`, `_LEGO_GRAPHQL_QUERY`, and the `data.searchElements.results` navigation inside `_search()`

The response is parsed by `_parse_available()` and `_parse_price_cents()`. Price lives at `results[].price.centAmount`; availability at `results[].availability` (uppercase token, e.g. `"AVAILABLE"`, `"OUT_OF_STOCK"`).

**Availability statuses treated as available:** `instock`, `available`, `limitedavailability`, `available for sale` — case-insensitive, underscores stripped, so the GraphQL token `"AVAILABLE"` matches. All other statuses (including API errors) treat the piece as unavailable — conservative by design to block uncertain orders.

**Shipping:** One flat charge per order (`LEGO_SHIPPING_COST_CENTS`, default `$5.99`). Applied to the `SellerListing` for each element. Because all LEGO.com elements share `seller_id="lego_official"`, the optimizer counts shipping once for the combined LEGO.com allocation entry regardless of how many distinct elements come from LEGO.com.

**Available quantity:** Set to `LEGO_MAX_QTY_PER_ITEM` (default `9999`) per element. LEGO.com stock levels are not exposed via the search API. A 40-block mosaic in a single color can require 160+ of one element, so the cap is set high enough that it is effectively never the binding constraint. If a specific order genuinely exceeds LEGO.com stock, the optimizer will route the shortfall to `lego_fallback_items` (unsourceable in MVP).

**Free shipping:** `apply_free_shipping_thresholds()` (in `optimizer.py`) zeros out LEGO.com shipping when the LEGO.com piece total meets `LEGO_FREE_SHIPPING_THRESHOLD_CENTS` (default `$35.00`). All downstream totals (`total_shipping_cents`, `grand_total_cents`, `laigo_fee_cents`, `customer_total_cents`) are recalculated on the reduced base so the customer receives the full benefit. For a typical mosaic the piece cost far exceeds $35, so LEGO.com shipping is usually free.

**Caching:**
- Raw search result: `lego_raw:{element_id}` — TTL `BRICKOWL_CACHE_TTL_SECONDS` (1h default)
- Availability: piggybacked on raw result cache

**Playwright ordering** (`order_from_lego`):

The full upload→cart→checkout flow (Steps 2–4 below) is **verified against live
PaB and ported into `_run_checkout`** (2026-06-10/11). **Login is NOT part of
this flow** — it is handled by Google-SSO `storage_state` seeding (see
`docs/LEGO_SESSION.md`); `_run_checkout` Step A only probes `/profile`. Steps
5–6 (payment / place-order) are **gated** — deliberately not captured.

> **The production blocker is Cloudflare-blocks-headless** (callout below).
> The chosen fix is a VPS + residential-proxy **headed** browser the saga
> reaches over CDP — full design + runbook in **`docs/LEGO_BROWSER_HOST.md`**.
> Selectors are correct; nothing ships to Render ordering until the browser
> host exists.

Re-verify selectors against the live DOM with `scripts/checkout/_inspect_pab.py`
(headed; dumps to `outputs/lego_debug/selectors_dump.txt`) if LEGO's SPA drifts.

**Verified flow (live, 2026-06-10 → 2026-06-11):**

| Step | What happens | Verified? | Selector(s) |
|---|---|---|---|
| — | (Login) Handled out-of-band by `storage_state` seeding — see `docs/LEGO_SESSION.md`. On a *fresh* (unseeded) session, lego.com shows age-gate + cookie-banner popups (`[data-test='age-gate-grown-up-cta']`, `[data-test='cookie-accept-all']`); the seeded session carries cookies so they don't reappear in `_run_checkout`. LEGO defers popup JS until after `networkidle` — wait per-locator. | ✅ | (seeding only) |
| 2 | Navigate to Pick-a-Brick | ✅ 2026-06-10 | Logged-in PaB at `_PAB_URL` renders correctly with the cached `storage_state`. |
| 3 | Upload list → View All Pieces → Pick selected pieces → cart drawer | ✅ 2026-06-10 (full sequence verified end-to-end; cart shows the exact uploaded items + prices) | Open modal: `[data-test='pab-listUploader-open-modal-desktop-button']` (mobile twin: `…-mobile-button`). File input: `[data-test='pab-listUploader-input']` (`id=list-upload`, `accept=".csv,.json,.lxfml"`). **JSON shape confirmed by PaB's own template (`pab-listUploader-download-json-template`): `[{"elementId","quantity"}, …]` — identical to what `_run_checkout` builds.** After parse: `[data-test='pab-listUploader-viewPieces-button']` ("View All Pieces") closes the upload modal; `[data-test='pab-listupload-button-addRemove']` ("Pick selected pieces") adds all parsed pieces to the cart and opens the cart drawer. **Conditional:** if the bag already held pieces, an "Overwrite pieces?" modal (`[data-test='pab-overwrite-pieces']`) appears — confirm with `[data-test='overwrite-pieces-modal-overwrite-button']` ("Yes, overwrite"). Cart drawer: `[data-test='pick-a-brick-cart']`; items `[data-test='element-cart-item']`; tabs `[data-test='element-cart-tabs-child']`. Bottom bar: `[data-test='pab-cart-bar-open-cart-button']`; header cart link `[data-test='util-bar-cart']` (`href=/en-us/cart`). Ported to `_run_checkout` Step C 2026-06-10. |
| 4 | Add to Bag → "Updated My Bag" modal → View My Bag → /cart → Checkout Securely | ✅ 2026-06-11 | The cart drawer has NO separate checkout button — `[data-test='pab-cart-add-to-main-cart-button']` ("Add to Bag", `type=submit`) commits the PaB cart and opens the "Updated My Bag" confirmation modal (`[data-test='pab-add-to-bag-confirmation']`). Its "View My Bag" button (`[data-test='pab-add-to-bag-confirmation-button-cart']`) navigates to `https://www.lego.com/en-us/cart` ("My Cart \| LEGO Shop"). There the checkout CTA is `[data-test='checkout-securely-button-desktop']` (role=link; mobile twin `checkout-securely-button-mobile`, `id=mobileCheckoutButton`). Order total: `[data-test='cart-order-total']`. Ported to `_run_checkout` Step D 2026-06-11. Clicking Checkout Securely enters the payment flow (Step 5/6, gated — not captured). |
| 5 | Verify payment screen renders (saved card already on account) | ❌ Unverified | — |
| 6 | Click "Place Order" → wait for `[data-test='order-confirmation-number']` | ⛔ **Gated — DO NOT WIRE** | Per payment-architecture decision 2026-05-31, this is a real charge to LAIGO's saved card. Not wired until end-to-end test strategy is finalized. Selector is still a scaffold guess. |

> **⚠️ Production blocker — Cloudflare blocks headless (discovered 2026-06-10, characterized 2026-06-11).**
> `order_from_lego` defaults to `headless=True`. A headless Chromium hitting `pick-a-brick` is hard-walled by **Cloudflare** (`title='Attention Required! | Cloudflare'`, ~4 KB challenge page, zero app DOM). The same navigation **headed** renders fine with the cached `storage_state` — and there is **no interactive challenge headed**, so this is a *fingerprint* problem, not a CAPTCHA problem.
>
> Probe matrix (`scripts/checkout/_cf_probe.py`, run from a **residential IP** so this is not IP reputation):
>
> | Driver / mode | Result |
> |---|---|
> | Bundled Chromium, headless | ❌ Blocked |
> | + init-script stealth (webdriver/plugins masks) | ❌ Blocked |
> | Real Chrome channel (`channel=chrome`), headless | ❌ Blocked |
> | patchright (patched undetectable), headless | ❌ Blocked |
> | patchright + real Chrome, headless | ❌ Blocked |
> | **Headed** (any driver) | ✅ Passes |
>
> **Conclusion: no headless approach works; production must run a real headed browser.** The launch is env-driven (deployment-agnostic): `LEGO_BROWSER_CDP_URL` → `connect_over_cdp` to a remote headed browser; else local launch gated by `LEGO_PLAYWRIGHT_HEADLESS` (default `true`).
>
> **Chosen fix (2026-06-11): VPS + residential-proxy headed browser over CDP** — `browserless/chromium` on a VPS, routed through a residential proxy (IP-whitelist auth) so the exit IP is residential, reached from Render via `LEGO_BROWSER_CDP_URL`. This keeps the LEGO `storage_state` cookies on infra you control and sidesteps both the headless block and the datacenter-IP question. Full design, security model, docker-compose, and setup runbook: **`docs/LEGO_BROWSER_HOST.md`** (`§7` lists the provider decisions still open). Render's native runtime can't run Xvfb itself (no root), which is why the browser is hosted off-Render.

**Things worth knowing if the DOM drifts (re-verify with `scripts/checkout/_inspect_pab.py`):**
- `data-test` (lego.com) vs `data-testid` (identity.lego.com) differ by domain. Easy to mix up; don't consolidate.
- The age-gate + cookie-banner popups render via JS that fires AFTER `networkidle`; use per-locator `wait_for(state="visible")`. (Only appear on a fresh/unseeded session — the seeded `storage_state` carries the dismissal cookies, so `_run_checkout` doesn't hit them.)
- The PaB file uploader is a multi-step modal flow, not a single input: Upload List → file input → View All Pieces → Pick selected pieces → (Overwrite? → Yes) → cart drawer → Add to Bag → Updated-My-Bag modal → View My Bag → `/cart` → Checkout Securely. See §6.1 Steps 3–4 for every selector.

**Debug screenshots:** dropped at `outputs/lego_debug/` by `order_from_lego` (`01_session_ok` → `06_confirmed`, plus `ERROR_*`) and by `_inspect_pab.py` (`step2_*`, `step3_*`, `exercise_*`, `checkout_*`).

**LEGO.com order cancellation:** There is no API for this. If the Saga fails after a LEGO.com order is placed, the `_compensate()` function logs a warning with the order ID and instructs manual cancellation at `lego.com/profile/orders`.

### 6.2 BrickOwl (`brickowl_client.py`)

**Seller ID prefix:** `"brickowl_"` (e.g. `"brickowl_12345"`)

**Status: Blocked on `catalog/availability` API permission.**

BrickOwl grants access to `catalog/availability` on a case-by-case basis. Contact them at https://www.brickowl.com/contact. Until access is granted, `get_listings_for_element()` logs a warning and returns an empty list — all pieces fall through to other sources.

**Two-step lookup:**

Step 1 — `GET /catalog/id_lookup` — converts LEGO element ID to BrickOwl BOID:
```
GET https://api.brickowl.com/v1/catalog/id_lookup
    ?key=...&id={element_id}&id_type=item_no&type=Part
```
`id_type` is tried as `item_no` first; falls back without `id_type` on 400. Valid `id_type` values: `item_no`, `design_id`, `bl_item_no`, `set_number`. **`lego_element` is invalid** (causes 400).

BOID extraction strips color suffixes: `322944-42` → `322944`. `catalog/availability` only accepts the bare numeric BOID.

Step 2 — `GET /catalog/availability` — returns all seller lots for a BOID:
```
GET https://api.brickowl.com/v1/catalog/availability
    ?key=...&boid={boid}&country={shipping_country}
```
Currently returns `403 Forbidden` for standard developer keys. A `PermissionError` is raised and caught by `get_listings_for_element()` which returns `[]`.

**BOID caching:** `boid:{element_id}` — TTL 24h. If a bad BOID was cached before the suffix-stripping fix, it persists until the TTL expires or the server restarts.

**BrickOwl order placement:** Not possible via the API. BrickOwl's API is a seller API. `create_order()` raises `NotImplementedError`. See Section 6.2.1 for ordering strategy options.

**Rate limits:** 600 req/min standard, 100 req/min bulk. Exponential backoff: 2s base, up to 3 retries on 429 and 5xx. Concurrent requests bounded by `BRICKOWL_CONCURRENT_REQUEST_LIMIT` via asyncio Semaphore.

#### 6.2.1 BrickOwl ordering strategy options

Once catalog access is granted, a separate decision is required for order placement:

| Option | How it works | Trade-offs |
|---|---|---|
| **Playwright automation** | Log into BrickOwl buyer account, automate cart + checkout per seller | Most automated; requires LAIGO BrickOwl buyer account; fragile to UI changes |
| **Cart URL redirect** | Use `catalog/cart_basic` to generate a pre-filled cart ID; redirect customer to `brickowl.com/catalog_cart_load/{cart_id}` | Customer completes purchase themselves; no LAIGO payment collection for BrickOwl portion |
| **Shopping list download** | Deliver optimizer output as per-seller CSVs; customer places orders manually | Fully manual; LAIGO does not collect payment for BrickOwl portion |

This decision also affects whether Stripe should hold the BrickOwl portion or collect separately.

### 6.3 BrickLink (`bricklink_client.py`)

**Status: Disabled (`BRICKLINK_ENABLED=false`)**

Returns empty listings until enabled. Set `BRICKLINK_ENABLED=true` in `.env` after completing setup.

**Setup steps:**
1. Create a BrickLink seller account at `bricklink.com/register.asp` (seller account required — API is not available to buyer-only accounts)
2. Register an API app at `bricklink.com/v3/api.page`
3. Generate consumer key + consumer secret + token + token secret
4. Add all four keys to `.env.secrets`
5. Verify endpoint responses against live API before activating

**API capability note:** BrickLink's public API v3 is primarily a seller management API. The price guide (`GET /catalog/item/{type}/{item_no}/price`) returns per-lot price and quantity data but **does not include store identity**. This means the optimizer cannot perform shipping consolidation across BrickLink lots — each lot is assigned a synthetic seller ID, preventing the Pass 2 merge logic from saving shipping costs. BrickLink is still valuable for price comparison (cheapest-lot allocation) but less effective than BrickOwl (which provides real store IDs) for shipping optimization.

---

## 7. Greedy Optimizer (`optimizer.py`)

Pure functions — no I/O, no side effects, safe to unit test without any API keys.

### `merge_listings(*sources)`

Combines per-element listing dicts from multiple clients into one. Called by `router.py` after fetching concurrently from all active clients:

```python
all_listings = merge_listings(lego_listings, brickowl_listings, bricklink_listings)
```

Listings for the same element from different clients are concatenated. The optimizer then chooses the cheapest across all sources transparently.

### `optimize(order_items, listings)`

```python
def optimize(
    order_items: list[dict],                    # [{elementId, quantity}, ...]
    listings: dict[str, list[SellerListing]],   # elementId → [SellerListing, ...]
) -> AllocationResult
```

The optimizer does not know or care which client produced each `SellerListing`. All sources are treated identically.

### `apply_free_shipping_thresholds(allocation)`

Applies free-shipping promotions after optimization. Currently handles LEGO.com only: if the `lego_official` piece cost meets `LEGO_FREE_SHIPPING_THRESHOLD_CENTS` (default $35.00), sets that entry's `shipping_cost_cents` to 0 and recalculates `total_shipping_cents`, `grand_total_cents`, `laigo_fee_cents`, and `customer_total_cents` on the reduced base.

Called by `router.py` after `optimize()` and by the stockout-retry path in `saga.py`.

### Pass 1: Greedy allocation

1. Separate items with no listings into `lego_fallback_items` (unsourceable given current active clients).
2. Sort remaining items by descending `quantity × cheapest_unit_price` — commit high-value pieces first to give the consolidation pass the most leverage.
3. For each piece, sort available listings by `(0 if seller already chosen else 1, price_per_cent)`. Already-chosen sellers are tried first — this consolidates shipping organically during allocation.
4. Fill each seller up to their `available_qty`. Remainder after all sellers exhausted → `lego_fallback_items`.

### Pass 2: Consolidation

For each seller B with `shipping_cost_cents > 0`:
- For each other seller A: verify A has enough stock for every piece B holds, then compute `extra_cost = Σ qty × (A_price − B_price)` for all of B's pieces.
- If `extra_cost < B_shipping_cost` AND A is feasible: merge B into A. Save `B_shipping_cost`, pay `extra_cost` more per unit.
- Choose A with minimum `extra_cost`; restart after any merge (dict changed).

Terminates when no merges remain. O(S² × P) per pass; S typically 2–5 in practice.

**Important:** With only LEGO.com active (MVP), all pieces share `seller_id="lego_official"` and shipping_cost_cents is the same value, so Pass 2 finds no merges. Consolidation becomes effective once BrickOwl returns listings for some pieces but not others.

### Service fee

```python
max(LAIGO_FEE_MIN_CENTS, int(grand_total_cents * LAIGO_FEE_PERCENT / 100))
```

Defaults: `max(300, grand_total_cents × 0.05)` — minimum $3.00, or 5% of grand total, whichever is larger.

### Result fields

| Field | Meaning |
|---|---|
| `seller_allocations` | List of `AllocationEntry` — one per chosen seller across all clients |
| `lego_fallback_items` | Items with no listings from any active source (unsourceable in MVP) |
| `lego_fallback_cost_cents` | Always `0` — LEGO.com pricing is now in `seller_allocations` (when LEGO.com is active), not here |
| `total_piece_cost_cents` | Sum of piece costs across all seller allocations |
| `total_shipping_cents` | Sum of per-seller shipping charges |
| `grand_total_cents` | `total_piece_cost + total_shipping` |
| `laigo_fee_cents` | LAIGO service fee |
| `customer_total_cents` | `grand_total + laigo_fee` — amount charged to customer |

---

## 8. Saga State Machine (`saga.py`)

```
    ┌──────────┐
    │ initiated│
    └────┬─────┘
         │ Stripe hold attempted (1.05× quote total, 5% buffer)
         ▼
    ┌──────────────┐
    │ stripe_held  │◄─────────────────────────────┐
    └──────┬───────┘                               │ stockout retry
           │ BrickOwl + LEGO.com orders placed     │
           ▼                                       │
    ┌───────────────┐                              │
    │ orders_placed │──── BrickOwl stockout ───────┘
    └──────┬────────┘
           │ Stripe capture (1s/4s/16s retries on transient errors)
           ▼
    ┌──────────────────┐
    │ payment_captured │  ← terminal (success)
    └──────────────────┘

    Any failure before orders placed → ┌─────────────┐
                                       │   failed    │  ← terminal (failure)
                                       └─────────────┘
    Any failure with orders placed, AND clean cancel possible:
       (BrickOwl cancels all succeed, Stripe cancel succeeds,
        NO LEGO.com order present)
                                     → ┌─────────────┐
                                       │ compensated │  ← terminal (clean rollback)
                                       └─────────────┘
    Any failure where clean cancel is NOT possible:
       (LEGO.com order placed, OR any BrickOwl cancel fails,
        OR Stripe cancel fails after retries, OR capture failed
        after orders placed, OR allocation drift > 5% post-placement)
                                     → ┌───────────────┐
                                       │ manual_review │  ← terminal (operator action required)
                                       └───────────────┘
```

**Terminal states (all distinguishable by `saga_status` field):**

| State | Customer outcome | Operator action |
|---|---|---|
| `payment_captured` | Order placed, card charged | None |
| `compensated` | No order, no charge — money was never moved | None |
| `failed` | No order, no charge — failure happened before any orders were placed | None (read `error` field for context) |
| `manual_review` | Orders MAY be placed; Stripe hold MAY be active | **Required.** Read `manual_review_reason` — it describes every stranded resource (BrickOwl orders to cancel by hand, LEGO.com order to cancel via lego.com/profile/orders, Stripe hold to release in the dashboard). See §9 below for the runbook. |

**Notes on intermediate (non-terminal) states:**

- `initiated`, `stripe_held`, `orders_placed`: in-flight saga. Frontend should keep polling — these are not endpoints to surface to the customer as "your status."
- `fallback_ordered`: exists in the enum but unreachable in MVP (router sets `lego_fallback_items=[]` in the cached quote). Reserved for the future state where BrickOwl supplies some pieces and LEGO.com handles overflow. Do not write this status from any new code path without coordinating with the optimizer's `lego_fallback_items` semantics.

**Saga-level deadline (B4, shipped 2026-05-15):** the entire saga runs inside a single `asyncio.wait_for(...)` bounded by `SAGA_TIMEOUT_SECONDS` (default 900s = 15 min, env-overridable). If the deadline expires, `_handle_saga_timeout` reads the checkpointed state and routes recovery:
- No `payment_hold_id` yet → `failed` (no money moved).
- Hold exists, no orders → best-effort `provider.cancel`; `failed` on success, `manual_review` on failure.
- Any orders placed → `manual_review` (cannot infer capture state from here; operator inspects Stripe + marketplaces).

### Order routing logic

```python
# BrickOwl sellers (prefix "brickowl_")
for entry in allocation.seller_allocations:
    if entry.seller_id.startswith("brickowl_"):
        order_id = await brickowl_client.create_order(...)   # NotImplementedError until ordering is built

# LEGO.com (seller_id "lego_official")
lego_entries = [e for e in allocation.seller_allocations if e.seller_id == "lego_official"]
if lego_entries:
    # Collect all LEGO.com items into one Playwright session
    await lego_client.order_from_lego(items_list, job_id)
```

### Stockout retry flow

If `brickowl_client.create_order()` raises `StockoutError(element_id)`:
1. Cancel all sub-orders placed so far in this attempt
2. Invalidate `brickowl_listings:{element_id}` from cache
3. Re-fetch listings from all clients with fresh data
4. Re-run optimizer on new listings
5. Retry (up to `OPTIMIZER_MAX_STOCKOUT_RETRIES`)

If retries exhausted: compensate and mark Saga `failed`.

### State meanings and compensating actions

| State | Meaning | If server crashes here |
|---|---|---|
| `initiated` | Saga started, no external calls made | Nothing to compensate |
| `stripe_held` | Stripe PaymentIntent created (funds held at `1.05 × quote_total`, not charged) | Cancel PaymentIntent manually: Stripe Dashboard → Payments → Cancel. Use idempotency key `cancel-{checkout_id}` if cancelling via CLI to stay consistent with the saga's automated retry. |
| `orders_placed` | All seller orders placed (BrickOwl and/or LEGO.com) | Cancel BrickOwl orders via portal; LEGO.com: cancel at lego.com/profile/orders if not shipped; then cancel Stripe hold |
| `fallback_ordered` | (Not reachable in MVP — see note above) | Same as `orders_placed` |
| `payment_captured` | Payment captured at the actual allocated total (may be < authorized — the unused portion of the 5% buffer decays automatically) | No action needed |
| `compensated` | Compensation succeeded — money was never moved, no orders are live | No action needed |
| `failed` | Saga failed BEFORE any orders were placed | Read `error` field for context; no operator action — `_handle_saga_timeout` / saga internal handlers already released the hold (if any). |
| `manual_review` | Orders may be placed AND/OR Stripe hold may still be active. Set in 6 conditions: capture-exhausted-retries with orders placed; capture permanent error with orders placed; post-placement drift > 5%; saga timed out with orders placed; compensation hit a permanent error during cancel; LEGO.com order placed (uncancellable via API). | **REQUIRED.** Read `manual_review_reason` — every stranded resource is enumerated with order IDs and runbook steps. See §9 below. |

---

## 9. Operator Crash Recovery

**Phase E step 1 (2026-05-19) automated most of this.** On lifespan boot in postgres mode, `scripts/checkout/saga_resume.py:resume_in_flight_sagas()` inspects every non-terminal saga and routes it to a safe terminal state:

- `initiated` → FAILED with reason "Saga abandoned by process restart before payment hold"
- `stripe_held` → `provider.cancel(hold_id)` + FAILED on success, MANUAL_REVIEW on cancel failure or missing hold_id or no provider
- `orders_placed` / `fallback_ordered` → MANUAL_REVIEW with verbose `manual_review_reason` runbook

Each routing decision emits a corresponding L6 audit event (`saga.failed`, `saga.manual_review`, `payment.cancelled`).

**For JSON-mode deployments** (or when reading state by hand), checkpoint lives at `outputs/{job_id}/checkout_state.json`. In postgres mode the same state is in the `sagas` table; query `SELECT * FROM sagas WHERE job_id = '<id>'`.

**`manual_review` (terminal):** the saga deliberately stopped because something needs operator judgement. Read `manual_review_reason` first — DO NOT cancel orders without it. Two common causes:

- **Capture exhausted retries after orders were placed.** The hold may have actually been captured (Stripe's idempotency cache returned a network error but accepted the capture server-side). Check the Stripe dashboard first.
  - If captured: mark the saga `payment_captured` manually (`UPDATE sagas SET saga_status='payment_captured' WHERE checkout_id='...'`).
  - If still authorized: retry capture from the dashboard, OR cancel the hold and refund the placed orders.
- **Allocation drift exceeded the 5% buffer.** Final total > authorized. Capture the authorized amount, bill the customer for the difference via an alternate channel, OR refund the placed orders and cancel the hold.

**`stripe_held` MANUAL_REVIEW from resume failure.** Either the provider wasn't registered at boot (gate closed) or `provider.cancel` raised. Cancel the hold via Stripe Dashboard manually; then `UPDATE sagas SET saga_status='failed' WHERE checkout_id='...'`.

**Important caveat:** `provider.cancel` can succeed at Stripe but raise on the Python side (network timeout reading the response). In that case the saga is routed to MANUAL_REVIEW even though the hold is actually canceled. Check Stripe before any other action. Phase E step 2's `reconcile_orphan_holds()` (deferred — see PRE_RELEASE_PAYMENT_CHECKLIST.md §9.3) will fix this once shipped.

---

## 10. Stripe Integration (Layer 5 — shipped)

Real Stripe API calls are wired through `StripeProvider` in `scripts/checkout/payment/stripe_provider.py`. The provider's constructor atomically validates SDK + env + safety rules; if any check fails it raises `PaymentProviderUnavailable` and the registry stays empty, which the gate sees as DISABLED with a reason.

The safety boundary is the **layered checkout gate** (see `scripts/checkout/gate.py` and `docs/CHECKOUT_AUDIT.md §10`). In DISABLED gate state, `POST /confirm` returns **HTTP 503** with `code: "CHECKOUT_GATE_CLOSED"` — no Saga is started, no orders are placed. This is enforced at the HTTP layer by Layer 3 and reinforced at the Saga layer by Layer 4.

The legacy "Stripe-disabled bypass" behavior — where `NotImplementedError` was caught and the Saga proceeded silently — has been removed. Calling `execute_checkout_saga()` directly (bypassing `/confirm`) while the gate is closed produces `saga_status: "failed"` with `error: "Gate closed at Saga start: ..."`. There is no longer a code path that places orders without a payment hold.

**To enable real Stripe flows for development:**
1. Set `STRIPE_SECRET_KEY=sk_test_...` in `.env.secrets`
2. Set `BRICKOWL_API_KEY=...` (or `LEGO_EMAIL`+`LEGO_PASSWORD`) in `.env.secrets` so at least one marketplace is "live"
3. Set `CHECKOUT_ENABLED=true` in `.env`
4. Flip `STRIPE_ENABLED = True` at the top of `scripts/checkout/payment/stripe_provider.py`
5. Restart the server; check the boot log for `payment.registry.registered provider=stripe mode=test`
6. Verify `GET /checkout/gate` returns `mode: "test"` with no `reasons`

### Enabling Stripe (deliberate two-step operator action)

The "flip the code constant AND set env" pattern is intentional defense-in-depth. Setting env alone — e.g., copy-pasting an `sk_test_` key into `.env.secrets` while testing — does NOT enable real payment calls. Flipping `STRIPE_ENABLED` alone without env is caught by `StripeProvider.__init__` immediately, with a specific reason. Both signals must align before the provider constructs.

1. Add `STRIPE_SECRET_KEY=sk_test_...` (or `sk_live_...` for production) to `.env.secrets`
2. Set `STRIPE_ENABLED = True` at the top of `scripts/checkout/payment/stripe_provider.py`
3. Test a single hold/capture/cancel cycle using `pm_card_visa` against your test mode dashboard
4. Only after step 3 succeeds: deploy with the same flag value to Render

### Capture retry policy

The Saga retries Stripe capture on transient errors only — see `_capture_with_retry()` in `saga.py`.

| Stripe error | Mapped to | Saga behavior |
|---|---|---|
| `APIConnectionError` / `RateLimitError` / `APIError` (5xx) | `PaymentRetryableError` | retry with 1s/4s/16s backoff |
| `CardError` (declined at capture, e.g. card cancelled) | `PaymentPermanentError` | skip retry → MANUAL_REVIEW |
| `AuthenticationError` / `InvalidRequestError` / `PermissionError` / `IdempotencyError` / `SignatureVerificationError` | `PaymentPermanentError` | skip retry → MANUAL_REVIEW |

The same idempotency key (`f"capture-{checkout_id}"`) is used across all attempts. Stripe's idempotency cache (24h) makes this safe — if the first attempt actually captured but the response was lost to a network error, the retry returns the cached success rather than double-capturing.

### Authorization buffer (5%)

The hold is for `ceil(quote_total × 1.05)`. Capture is for the actual allocated total (may differ from quote total after stockout re-optimization). Stripe permits capturing less than authorized; the unused portion of the authorization releases automatically without charging the customer. This absorbs allocation drift up to 5% without prompting a customer "confirm new price" round-trip.

If drift exceeds 5%, the Saga fails-closed to MANUAL_REVIEW BEFORE attempting capture. Hard-coded constants in `saga.py`: `_HOLD_BUFFER_MULTIPLIER = 1.05`, `_CAPTURE_BACKOFFS_SECONDS = (1, 4, 16)`.

### PaymentIntent pattern

```
create_payment_hold(amount, pm_id, idempotency_key="hold-{checkout_id}")
    → PaymentIntent(capture_method="manual", confirm=True)
    → funds held on card, not charged
    → returns pi_...

[all orders placed successfully]

capture_payment(pi_...)
    → PaymentIntent.capture()
    → card charged

[if any order fails]
cancel_payment_hold(pi_...)
    → PaymentIntent.cancel()
    → hold released, card never charged
```

### Frontend requirements

The frontend uses Stripe.js/Stripe Elements to tokenize the card client-side:
```javascript
const { paymentMethod } = await stripe.createPaymentMethod({
  type: 'card',
  card: cardElement,
});
// POST /confirm with paymentMethod.id as stripe_payment_method_id
```
Raw card numbers never reach LAIGO servers.

### Test payment method

`pm_card_visa` — use this as `stripe_payment_method_id` in `POST /confirm` when testing.

### Live mode guard

`StripeProvider.__init__` in `payment/stripe_provider.py` raises `PaymentProviderUnavailable` if an `sk_live_*` key is used outside of a Render deployment (checks `RENDER` env var with `is_truthy()` so `RENDER="false"` does NOT count as on-Render). The gate also independently checks this so an operator sees a specific, actionable reason on `/checkout/gate` even if the provider somehow registered. This prevents accidentally charging real cards during local development.

### Payment provider registry — single-active contract (B17, Phase 3.2)

The registry (`scripts/checkout/payment/registry.py`) holds exactly one active provider per process. It is populated once at lifespan startup by `Main.py` calling `payment_registry.register(StripeProvider())`. After that, reads via `get_active()`, `is_configured()`, `active_name()`, `active_mode()` are lock-free.

**Replacement guard (B17, shipped 2026-05-16):** `register()` refuses to replace an already-registered provider unless the same instance is passed (`is` check, not `name` check) OR the env flag `LAIGO_ALLOW_REGISTRY_REPLACE=1` is set. The guard raises `RuntimeError` rather than warn-and-replace.

```
# Production: env unset
register(StripeProvider())   # first call: succeeds
register(StripeProvider())   # second call: RuntimeError (different instance)

# Test: same instance reused
p = StripeProvider()
register(p); register(p)     # no-op on second call

# Test: env flag set
os.environ["LAIGO_ALLOW_REGISTRY_REPLACE"] = "1"
register(StripeProvider())   # logs WARNING; replacement permitted

# Test: explicit reset
payment_registry._reset_for_tests()
register(StripeProvider())   # treated as a fresh process
```

**Operator implications:**
- `LAIGO_ALLOW_REGISTRY_REPLACE` MUST NOT be set in production. The /checkout/gate body does NOT surface this state; operators rely on log scraping for `payment.registry.replaced` lines.
- `_reset_for_tests()` is the canonical way to reset state between test scenarios; despite the name, it is safe to call from production shutdown paths.

**Note:** `Main.py` lifespan shutdown calls `payment_registry._reset_for_tests()` (added 2026-05-16) so the second lifespan run in the same Python process — TestClient used twice, future hot-reload tooling — does not crash on the B17 replacement guard. Production with `uvicorn`-spawned-per-lifespan child processes is unaffected either way.

### Webhook (optional)

For production: add `POST /stripe/webhook` that validates `Stripe-Signature` against `STRIPE_WEBHOOK_SECRET` and handles `payment_intent.payment_failed` events to trigger saga compensation if the hold fails asynchronously.

---

## 11. Cache (`cache.py`)

In-process TTL dict. Async-safe (single event loop, cooperative scheduling). A background sweep task evicts expired entries every 5 minutes. Started from `Main.py` lifespan via `start_cache_sweeper()`.

**The cache is in-process only.** A server restart clears all cached data. No distributed cache.

| Key pattern | Value type | TTL | Set by |
|---|---|---|---|
| `lego_raw:{element_id}` | `dict` (raw search result) | `BRICKOWL_CACHE_TTL_SECONDS` (1h) | `lego_client._search()` |
| `boid:{element_id}` | `str` (BrickOwl BOID) or `None` | 24h (BOID), 1h (None/not found) | `brickowl_client.get_boid_for_element()` |
| `brickowl_listings:{element_id}` | `list[SellerListing]` | `BRICKOWL_CACHE_TTL_SECONDS` (1h) | `brickowl_client.get_all_listings()` |
| `quote:{checkout_id}` | `dict` (allocation + metadata) | 600s (10 min) | `router.get_quote()` |

**Force-invalidate a listing** (used during stockout retries):
```python
await cache_delete(f"brickowl_listings:{element_id}")
```

**Known issue — stale BOID cache:** Before the BOID suffix-stripping fix, incorrect values like `"322944-42"` were cached for 24h under `boid:{element_id}`. Affected entries remain until TTL expires or the server restarts. If BrickOwl catalog access is granted and elements still 403, restart the server to clear the BOID cache.

---

## 12. Debug Endpoints (`/checkout-debug/`)

All registered via `debug_router` in `Main.py`. Visible in Swagger UI at `/docs`.

| Endpoint | Method | Purpose |
|---|---|---|
| `/checkout-debug/brickowl/element/{id}/raw` | GET | **Start here for BrickOwl debugging.** Raw JSON from both API steps (id_lookup + availability). Shows actual field names and extracted BOID. |
| `/checkout-debug/brickowl/element/{id}` | GET | Parsed `SellerListing` objects for one element. Empty if catalog/availability access is blocked. |
| `/checkout-debug/brickowl/elements` | POST | Batch parsed listings for up to 100 elements. |
| `/checkout-debug/lego/element/{id}/listing` | GET | **Start here for LEGO.com pricing debugging.** Parsed `SellerListing` including `price_per_cent`. If price is 0, update `_parse_price_cents()` in `lego_client.py`. |
| `/checkout-debug/lego/element/{id}` | GET | LEGO.com availability (true/false) for one element. |
| `/checkout-debug/lego/elements` | POST | Batch LEGO.com availability for up to 100 elements. |
| `/checkout-debug/job/{job_id}/order-list` | GET | Raw `order_list.json` content for a completed job. |
| `/checkout-debug/job/{job_id}/optimize` | POST | Full optimization preview: fetches from all active clients, merges, optimizes, returns cost breakdown. No orders placed, no payment taken. |

---

## 13. Data Flow: Quote to Order

```
POST /jobs/{job_id}/checkout/quote
  body: {shipping_country, shipping_zip, customer_email}

  1. checkout_store.read_order_list(job_id)
       reads: outputs/{job_id}/order_list.json
       written by: Main.py run_job() before workspace deletion

  2. lego_client.get_all_listings() + brickowl_client.get_all_listings()
     + bricklink_client.get_all_listings()  (concurrent)
       LEGO.com: search API → SellerListing(seller_id="lego_official", price=X, shipping=599)
       BrickOwl: blocked → []
       BrickLink: disabled → []

  3. merge_listings(lego, brickowl, bricklink) → {element_id: [SellerListing, ...]}

  4. optimize(order_items, merged_listings)
       → AllocationResult(
           seller_allocations=[AllocationEntry(seller_id="lego_official", ...)],
           lego_fallback_items=[items not on any active source],
         )

  4b. apply_free_shipping_thresholds(allocation)
       → zeros LEGO.com shipping if piece total ≥ $35; recalculates all totals

  5. Cache quote: "quote:{checkout_id}" → {allocation, unsourceable_items, metadata}
     (allocation.lego_fallback_items set to [] before caching — unsourceable tracked separately)

  6. Return QuoteResponse
       sellers: [lego_official allocation]
       unsourceable_items: [items not available anywhere]
       can_proceed: true if unsourceable is empty

─────────────────────────────────────────────────────────────────────────────

POST /jobs/{job_id}/checkout/confirm
  body: {checkout_id, stripe_payment_method_id}

  1. Load cached quote → verify job_id match → verify no unsourceable items
  2. checkout_store.save() → writes initial checkout_state.json
  3. asyncio.create_task(execute_checkout_saga(...)) → returns immediately
  4. Return ConfirmResponse(poll_url=".../status")

─────────────────────────────────────────────────────────────────────────────

Background: execute_checkout_saga()
  Wrapped in asyncio.wait_for(..., timeout=SAGA_TIMEOUT_SECONDS=900s).
  On timeout: _handle_saga_timeout() inspects state and writes failed/manual_review.

  Layer 4 — gate.require_open() at the first line; FAILED if closed.
  Layer 5 — payment_registry.get_active() acquires provider for the whole saga.

  1. Stripe hold via PaymentProvider.create_hold (idempotency: hold-{checkout_id})
     - amount = ceil(quote_total × 1.05) — 5% buffer absorbs stockout drift
     - PaymentPermanentError → FAILED (no orders placed)
     - PaymentRetryableError → FAILED ("please retry")
  2. While-loop with B5 pre-placement drift check at the top:
     - If new total > authorized → _compensate (clean COMPENSATED path, no orders)
     - BrickOwl sub-orders (skipped in MVP: create_order raises NotImplementedError;
       real path: B19 splits create + checkpoint into separate try blocks)
     - LEGO.com order via Playwright → lego_client.order_from_lego(items, job_id)
       → writes lego_order_id to checkout_state.json (also B19-split)
     - Stockout retry up to OPTIMIZER_MAX_STOCKOUT_RETRIES; re-fetch + re-optimize
  3. Stripe capture via PaymentProvider.capture (idempotency: capture-{checkout_id})
     - 3 attempts on PaymentRetryableError; backoff 1s / 4s / 16s
     - Post-placement drift safety: capture_amount > authorized → MANUAL_REVIEW
       (orders placed, can't safely capture; operator decides recovery)
     - Exhausted retries OR PaymentPermanentError → MANUAL_REVIEW
  4. Update saga_status → payment_captured
```

---

## 14. Known Issues

Operational issues only — defects in the saga / payment layer are tracked in `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4` (B1–B23) and the hardening sequence is in §8 of that file. Do not duplicate here.

1. **`fallback_ordered` saga state is unreachable in MVP.** The router sets `lego_fallback_items=[]` before caching the quote allocation, so the saga's LEGO.com fallback block never fires. This state becomes reachable once BrickOwl is active and some pieces overflow to LEGO.com. (Tracked as H14 — keep as reserved.)

2. **LEGO.com pricing endpoint is unverified against live traffic.** `_parse_price_cents()` in `lego_client.py` tries several field name candidates based on expected API shape. If LEGO.com's response uses a different field name, prices return as `0`. Test via `GET /checkout-debug/lego/element/{id}/listing`.

3. **LEGO.com availability endpoint is unverified.** `_parse_availability()` also uses assumed field names. If it consistently returns `False` for known-available parts, verify the field names against live network traffic.

4. **Stale BOID cache after suffix-stripping fix.** Any `boid:{element_id}` entries cached before the `322944-42` → `322944` fix contain incorrect values that persist for up to 24h. Server restart clears them.

5. **Concurrent LEGO.com sessions share one account.** Two simultaneous checkouts both call `lego_client.order_from_lego()` using the same LEGO.com credentials. Playwright sessions are independent processes and should not conflict, but LEGO.com may rate-limit or flag the account for concurrent automated logins. Roadmap #13 introduces a per-account semaphore.

6. **LEGO.com actual stock levels are unknown.** The optimizer assumes each element has `LEGO_MAX_QTY_PER_ITEM` (default 9999) available. LEGO.com's search API does not expose stock counts. If an element is truly out of stock and the API still marks it available, orders will fail at Playwright checkout time (not at quote time). No workaround without scraping stock data. (B8 in PRE_RELEASE §4 — option B is the recommended pre-launch mitigation: document the asymmetry; option A makes LEGO stockouts retryable.)

7. **No per-customer order visibility.** Orders are placed from LAIGO's service accounts. The customer receives no BrickOwl or LEGO.com confirmation email directly. `customer_email` is stored in `checkout_state.json` but no notification is sent after order placement. (Tracked as roadmap #14 in PRE_RELEASE §3.)

8. **`checkout_store._locks` dict grows unbounded.** A new `asyncio.Lock` is created per `job_id` and never removed. For a long-running server with many jobs, this accumulates locks indefinitely. Low memory impact in practice, but worth cleaning up when a job's TTL expires. (Tracked as H13 — defer to roadmap #2 if Postgres state lands; otherwise tier-4 cleanup.)

9. **B23 — concurrent `/confirm` with different `checkout_id` for the same `job_id`.** PG mode closes this structurally via the `sagas_one_active_per_job_idx` partial unique index (router translates the resulting `UniqueViolationError` to HTTP 422 with `code="ACTIVE_CHECKOUT_EXISTS"`). JSON-mode deployments remain exposed — disappears post-Phase-F. See PRE_RELEASE §4 B23.

---

## 15. Pending Work

This table is operational follow-ups for client/launch work. **Pure correctness/hardening bugs** (B1–B23) and their per-phase shipping status live in `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4`; the prioritized hardening order is in that doc's §8. Do not duplicate entries between the two docs.

Listed in priority order:

| # | Task | Prerequisite |
|---|---|---|
| 1 | Verify LEGO.com pricing field names in `_parse_price_cents()` | Test `GET /checkout-debug/lego/element/{id}/listing` |
| 2 | Verify LEGO.com availability field names in `_parse_availability()` | Test `GET /checkout-debug/lego/element/{id}` |
| 3 | Finish LEGO.com Playwright verification via `scripts/checkout/manual_lego_test.py` | In progress 2026-06-02 →. Login flow verified through username/password fill (see §6.1 table). Remaining: end-to-end login success, then Steps 2–5 (PaB → cart upload → checkout → payment screen). Step 6 (place-order click) gated on test-strategy decision. After all steps verified, port working selectors back into `lego_client.py::_run_checkout()`. |
| 4 | Contact BrickOwl for `catalog/availability` API access | See Section 6.2 |
| 5 | Decide BrickOwl ordering strategy | See Section 17 (BrickOwl Ordering TODO) |
| 6 | Implement BrickOwl order placement once strategy decided | BrickOwl API access + strategy decision |
| 7 | Create BrickLink seller account; obtain API credentials | — |
| 8 | Implement BrickLink price guide client | API credentials |
| 9 | ~~Enable Stripe (`STRIPE_ENABLED=True` + `sk_test_...` + `CHECKOUT_ENABLED=true`)~~ | ✅ Done 2026-05-19 (TEST mode). Live mode still requires §6 go-live checklist + the U3/U4/U5 Render actions in `PRE_RELEASE_PAYMENT_CHECKLIST.md §9.5`. |
| 10 | Send customer confirmation email after saga completes | Email service (e.g. SendGrid / SES). Fire on `saga_status == payment_captured` AND on `saga_status == manual_review` (different templates). |
| 11 | Add `checkout_store._locks` cleanup on job TTL expiry | Subsumed by Phase F — JSON backend disappears once `DB_BACKEND=postgres` is the only path on Render. |
| 12 | ~~B12 customer-facing error translation~~ | ✅ Shipped 2026-05-16. |
| 13 | ~~L6 audit log — wire remaining call sites~~ | ✅ Shipped 2026-05-19 — 26 emits in `saga.py`. Schema + envelope in `PRE_RELEASE_PAYMENT_CHECKLIST.md §2`. |
| 14 | ~~Postgres-backed state + resume-on-restart~~ | ✅ Phases A–E shipped 2026-05-16 → 2026-05-19. Remaining: Phase F (Render cutover) per `PRE_RELEASE_PAYMENT_CHECKLIST.md §9.4`. |

---

## 16. Testing

### Automated test suites (shipped 2026-05-19, §6.10 coverage)

| File | Coverage | Requires |
|---|---|---|
| `scripts/test_optimizer.py` | optimizer happy path, shipping consolidation, free-shipping threshold, stockout fall-through, 2000×15 perf, `merge_listings` ordering, `compute_laigo_fee` boundary | nothing — pure functions |
| `scripts/test_gate_bypass.py` | L0 gate matrix, `require_open` raise/return, L3 503 wire contract, L4 watchdog (saga never reaches provider when gate closed) | nothing — JSON-backed |
| `scripts/test_saga_state_machine.py` | 7 saga state transitions (happy path + hold permanent/transient + STRIPE_HELD→COMPENSATED/MANUAL_REVIEW + capture failures) | nothing — fake provider + monkey-patched LEGO client |
| `scripts/test_jobs_store_json.py` / `_edge.py` | jobs lifecycle API parity, FIFO dequeue, TTL eviction | nothing |
| `scripts/test_jobs_store_dispatch.py` | dispatcher backend routing parity | `DB_BACKEND=postgres` + Neon DSN |
| `scripts/test_phase_e_pg.py` | `saga_resume` routing branches + `payment_holds_store` + `audit.emit` failure-swallowing | same |
| `scripts/test_reconcile_pg.py` | `reconcile_orphan_holds` decision matrix | same |

Run any test from the project root: `.venv\Scripts\python.exe -m scripts.test_<name>`. None require Stripe or marketplace network access.

### Optimizer unit test (no API key needed) — inline example

```python
from checkout.optimizer import optimize
from checkout.models import SellerListing

listings = {
    "302421": [
        SellerListing(seller_id="lego_official", seller_name="LEGO.com",
                      price_per_cent=15, available_qty=99, shipping_cost_cents=599),
        SellerListing(seller_id="brickowl_A", seller_name="Store Alpha",
                      price_per_cent=10, available_qty=500, shipping_cost_cents=499),
    ],
    "302423": [
        SellerListing(seller_id="lego_official", seller_name="LEGO.com",
                      price_per_cent=18, available_qty=99, shipping_cost_cents=599),
        SellerListing(seller_id="brickowl_A", seller_name="Store Alpha",
                      price_per_cent=14, available_qty=300, shipping_cost_cents=499),
    ],
}
order = [{"elementId": "302421", "quantity": 50}, {"elementId": "302423", "quantity": 50}]
result = optimize(order, listings)
# Expected: brickowl_A wins on both (cheaper units), one $4.99 shipping charge.
# LEGO.com not used since brickowl_A is cheaper and has stock.
print(result.seller_allocations)
print(result.customer_total_cents)
```

### LEGO.com client integration test (requires network)

```python
import asyncio
from checkout.clients.lego_client import get_listing_for_element, check_element_available

# 302421 = red 1×1 plate; should be available on Pick-a-Brick
listing = asyncio.run(get_listing_for_element("302421", "US"))
assert listing is not None, "302421 should be available on LEGO.com"
assert listing.price_per_cent > 0, "price should be non-zero — check _parse_price_cents()"
assert listing.seller_id == "lego_official"
print(f"302421: ${listing.price_per_cent / 100:.2f} per piece")
```

### BrickOwl client test (requires live API key)

```python
import asyncio
from checkout.clients.brickowl_client import get_boid_for_element

boid = asyncio.run(get_boid_for_element("302421"))
print(f"BOID: {boid}")   # expect a clean numeric string like "322944"
assert boid is not None
assert "-" not in boid, "BOID should not contain color suffix"
# catalog/availability will return 403 until access is granted — expected
```

### End-to-end quote test (Swagger UI)

**Precondition:** `GET /checkout/gate` must return `is_open: true` for `/confirm` to work. In DISABLED mode (dev default), `/confirm` returns 503 — see `docs/CHECKOUT_AUDIT.md §10` for how to bring the gate into TEST or LIVE.

1. `uvicorn Main:app --reload` from `scripts/`
2. Open http://127.0.0.1:8000/docs
3. **Verify gate**: `GET /checkout/gate` — if `mode != "test"` and `mode != "live"`, the rest of this test will 503 at step 5. Either skip to step 4 (quote works regardless of gate) or configure env per the "Enabling Stripe" section above.
4. Complete a mosaic job (`POST /generate` → poll `GET /jobs/{id}` → status `complete`)
5. `POST /jobs/{job_id}/checkout/quote` — expect `can_proceed: true`, `sellers` shows `lego_official`. Works in DISABLED mode (quote is read-only).
6. `POST /jobs/{job_id}/checkout/confirm` with `stripe_payment_method_id: "pm_card_visa"` — expect 200 with `saga_status: "initiated"` when gate is open; expect 503 with `code: "CHECKOUT_GATE_CLOSED"` when gate is DISABLED.
7. Poll `GET /jobs/{job_id}/checkout/{checkout_id}/status` — expect `saga_status: "payment_captured"` on success. Once Layer 4 is shipped, a gate flip mid-Saga produces `saga_status: "failed"` with `error: "Gate closed at Saga start: ..."`.

### Stripe test (requires `STRIPE_ENABLED=True` in `payment/stripe_provider.py`)

Use `pm_card_visa` as `stripe_payment_method_id`. Verify in Stripe Dashboard → Payments that a PaymentIntent with `capture_method=manual` appears, transitions to captured.

### LEGO.com Playwright test (pre-production)

Before deploying, run with `headless=False` in `lego_client.py`:
```python
browser = await pw.chromium.launch(headless=False)
```
Step through the checkout flow visually. Verify each numbered screenshot is saved. Confirm order appears in LAIGO's LEGO.com account. Revert to `headless=True` before deploying.

---

## 17. BrickOwl Ordering — TODO

> **Status:** Deferred. `catalog/availability` API access is still pending (403). This section documents everything known, understood, and decided so far, so that when access is granted the implementation path is clear.

### 17.1 Current blockers

**Access blocker:** BrickOwl's `catalog/availability` endpoint returns `403 Forbidden` for standard developer API keys. Access must be requested manually. Email brickowl.com/contact; reference the developer API documentation and explain the use case (LEGO mosaic service needing per-seller price and availability data).

**No buyer order API:** BrickOwl's API is built entirely for sellers. There is no `POST /order/create` equivalent for buyers. All three ordering strategies below work around this at the cost of automation fidelity.

### 17.2 The three ordering strategy options

#### Option A — Playwright buyer account automation

LAIGO controls a BrickOwl buyer account. After the optimizer selects sellers, automate: log in → search for each seller's store → add the specified lots to cart → checkout using LAIGO's saved payment method → capture the confirmation numbers.

**What works:**
- Fully automated: no human in the loop, consistent with how LEGO.com ordering already works
- LAIGO can collect payment for BrickOwl items through Stripe (LAIGO holds Stripe funds, pays BrickOwl sellers independently using LAIGO's card)
- Returns a BrickOwl order ID per seller → stored in `checkout_state.json` as `brickowl_order_ids`
- Compensation is possible: BrickOwl seller portal allows order cancellation; `cancel_order()` can be implemented as Playwright automation against the buyer portal

**What's hard:**
- BrickOwl's storefront UI is not a clean SPA — selectors may vary by seller store theme. Unlike LEGO.com's uniform checkout, each BrickOwl seller's store page may look different
- The cart is per-store (no global cart), so the automation must make one separate Playwright session per seller, serialized or with careful concurrency limits
- Lot-level selection requires searching within a specific seller's store and adding the exact lot/color variant — fragile if BrickOwl changes pagination or lot URLs
- A BrickOwl buyer account must be created, have a verified payment method on file, and be logged in during Playwright sessions
- Stockout detection during Playwright checkout (lot sold out between quote and order) requires screenshot-based error detection or DOM scraping of error messages

**Integration with saga.py:**
- The seller ID prefix `"brickowl_"` is already in place. `create_order(seller_id, items)` in `brickowl_client.py` would be implemented as a Playwright session for that one seller.
- Multiple BrickOwl sellers are iterated sequentially in the saga's BrickOwl loop (not parallel, to avoid concurrent logins to the same buyer account).
- `cancel_order(brickowl_order_id)` would automate: log in → Orders → find order → Cancel.
- `StockoutError` should be raised when a lot is no longer available at Playwright time; caught by the saga's retry loop.

**Stripe scope:** LAIGO collects the full customer payment via Stripe (including the BrickOwl portion). LAIGO's buyer account pays BrickOwl sellers using LAIGO's own card (float/fronting). Stripe hold covers `customer_total_cents` which includes BrickOwl pieces. No change to the Stripe integration needed beyond what is already designed.

**Account requirements:**
- BrickOwl buyer account (separate from the developer API key account, or same account if BrickOwl supports both roles)
- A payment method saved on the BrickOwl buyer account
- The account's email/password stored in `.env.secrets` as `BRICKOWL_EMAIL` and `BRICKOWL_PASSWORD`

---

#### Option B — Cart URL redirect (customer-pays BrickOwl directly)

BrickOwl provides a `catalog/cart_basic` API endpoint (verify exact name against BrickOwl developer docs) that creates a pre-filled cart for a buyer. The cart URL looks like `brickowl.com/catalog_cart_load/{cart_id}`. After the optimizer runs, LAIGO generates one cart URL per BrickOwl seller and returns them to the customer, who visits each URL and completes the purchase themselves on BrickOwl.com using their own payment method.

**What works:**
- No Playwright fragility; the customer handles their own checkout
- No BrickOwl buyer account needed for LAIGO
- No float/fronting — customer pays BrickOwl directly

**What's hard:**
- LAIGO cannot collect the BrickOwl portion of the order through Stripe — the customer pays BrickOwl and LAIGO separately, splitting the payment experience
- Customer confirmation is manual and untracked: LAIGO has no way to know whether the customer completed their BrickOwl purchases
- The saga cannot verify order placement, so `checkout_state.json` has no `brickowl_order_ids` — no compensation possible
- No stockout detection at checkout: if a lot is sold out by the time the customer clicks the cart URL, BrickOwl will show an error to the customer with no automatic recovery
- The saga would terminate after generating the cart URLs (saga_status becomes something like `"brickowl_redirect_required"`) — a new terminal state not currently in the enum
- API verification needed: confirm `catalog/cart_basic` is available with standard API key (not behind the same access wall as `catalog/availability`)

**Integration with saga.py:**
- The BrickOwl loop in saga.py would call `brickowl_client.generate_cart_url(seller_id, items)` per seller instead of `create_order()`
- Cart URLs returned to the client via a new field on `CheckoutStatusResponse` or a new endpoint
- Saga would write `brickowl_cart_urls: {seller_id: url}` to `checkout_state.json` and set a new status
- No compensation path needed for BrickOwl (customer never placed an order through LAIGO)

**Stripe scope:** Stripe would only hold/capture the LEGO.com and BrickLink portion. The BrickOwl portion is excluded from LAIGO's Stripe charge — the optimizer output would need to split the total accordingly. This complicates fee calculation and the customer-facing price breakdown.

---

#### Option C — Shopping list download (fully manual)

The optimizer output is delivered to the customer as a set of per-seller shopping lists (CSV or structured display). The customer places all orders manually. LAIGO provides a summary: "Buy these pieces from Store A, these from Store B."

**What works:**
- Zero implementation complexity; the optimizer already produces everything needed
- No automation fragility
- No accounts needed

**What's hard:**
- Fully manual: eliminates the core value proposition of LAIGO (automated purchasing)
- LAIGO cannot collect payment for the BrickOwl portion
- No order tracking; no compensation; no confirmation
- Only acceptable as a temporary fallback while blocking issues are resolved

**Integration with saga.py:**
- No saga integration needed. The quote endpoint would include the per-seller breakdown, and the customer handles purchasing outside LAIGO.
- This is effectively the current state (MVP with only LEGO.com) extended with a shopping list display.

---

### 17.3 Strategy comparison

| | Option A (Playwright) | Option B (Cart URL) | Option C (Manual) |
|---|---|---|---|
| LAIGO collects BrickOwl payment | Yes | No | No |
| Customer friction | None (automated) | One click per seller | High (manual) |
| Playwright fragility | High | None | None |
| Saga compensation possible | Yes | N/A | N/A |
| BrickOwl account required | Yes (buyer) | No | No |
| Stockout detection at checkout | Hard | None | None |
| Implementation effort | High | Medium | Low |

**Recommended path:** Option A (Playwright) once `catalog/availability` access is granted, as it maintains the fully automated promise. Option B is an acceptable stepping stone if API access is delayed and the team wants to ship BrickOwl price benefits sooner. Option C is a fallback only.

### 17.4 API research needed before implementation

Regardless of which option is chosen, confirm the following by inspecting the BrickOwl developer portal once API access is active:

1. **`catalog/availability` response shape** — exact field names for: seller store name, seller ID, price per unit, available quantity, shipping options per destination country. Update `_parse_lot()` in `brickowl_client.py` with the verified field names.

2. **Seller ID format in `catalog/availability`** — the optimizer prefixes seller IDs as `"brickowl_{seller_id}"`. Verify that seller IDs returned by the API are stable numeric strings (not session-dependent).

3. **Shipping cost in `catalog/availability`** — does the response include per-destination shipping costs, or does LAIGO need a second API call per seller? If a second call is needed, identify the endpoint and add it to `get_listings_for_element()`.

4. **`catalog/cart_basic` endpoint** (Option B only) — confirm this endpoint exists, its parameter format (seller ID, lot IDs, quantities), and whether it requires the same elevated API access as `catalog/availability` or works with a standard key.

5. **Rate limits under production load** — BrickOwl standard API: 600 req/min. With 50 unique elements and 10 BrickOwl sellers per element, a single quote could make up to 500 API calls. Verify this stays within rate limits, or consider batching if a batch endpoint exists.

### 17.5 Compensation design (Option A)

For Option A, `cancel_order(brickowl_order_id)` must be implemented before placing any live orders. Without cancellation, the saga cannot compensate a partial failure (e.g., seller A's order succeeds, seller B's fails — seller A's order is stuck without a cancellation path).

Options for implementing `cancel_order`:
- **Portal automation (Playwright):** Log in to LAIGO's BrickOwl buyer account → Orders → find order by ID → click Cancel. Same fragility concerns as ordering.
- **BrickOwl seller contact:** BrickOwl allows buyers to contact sellers through the platform. Cancellation requests could be sent as messages — fully manual, not automatable.
- **API inquiry:** When contacting BrickOwl for `catalog/availability` access, also ask whether a buyer-side order management or cancellation endpoint exists.

The saga already calls `brickowl_client.cancel_order(order_id)` in `_compensate()`. Until this function is implemented, it raises `NotImplementedError` silently (logged but not propagated). This is acceptable in dev but must be resolved before enabling BrickOwl ordering in production.

### 17.6 Seller selection and shipping consolidation (how it connects to the optimizer)

When `catalog/availability` returns listings, each lot has a `seller_id`. The optimizer assigns lots to sellers to minimize `piece_cost + shipping`. Pass 2 consolidation merges small sellers into larger ones when the extra unit-price cost is less than the shipping fee saved.

**What makes BrickOwl consolidation effective:**
- BrickOwl has thousands of sellers, each with a flat per-order shipping rate. Pass 2 aggressively merges smaller orders into sellers with free-shipping thresholds.
- Some BrickOwl sellers offer free shipping above a threshold (e.g., "free shipping on orders over $20"). When this data is available in the API response, `apply_free_shipping_thresholds()` in `optimizer.py` can be extended to handle BrickOwl thresholds the same way it handles LEGO.com's $35 threshold.
- With multiple BrickOwl sellers active, the optimizer's seller allocation becomes the primary cost driver. The number of sellers chosen directly determines total shipping paid.

**Connection to the saga:**
- The optimizer produces `AllocationEntry` objects with `seller_id="brickowl_{id}"`.
- The saga's BrickOwl loop iterates these entries and calls `create_order(entry.seller_id, entry.items)` per seller.
- If any one BrickOwl order fails with `StockoutError`, all BrickOwl orders placed in the current attempt are cancelled, the cache for the stockout element is invalidated, and the full optimizer runs again with fresh listings.
- This retry loop (max `OPTIMIZER_MAX_STOCKOUT_RETRIES`, default 2) handles the race condition between quote time and order time without requiring atomic inventory reservation from BrickOwl.

### 17.7 BRICKOWL_EMAIL and BRICKOWL_PASSWORD (Option A)

If Playwright automation is chosen, add to `.env.secrets`:
```
BRICKOWL_EMAIL=<LAIGO BrickOwl buyer account email>
BRICKOWL_PASSWORD=<LAIGO BrickOwl buyer account password>
```

And add to `clients/brickowl_client.py`:
```python
BRICKOWL_EMAIL    = os.environ.get("BRICKOWL_EMAIL", "")
BRICKOWL_PASSWORD = os.environ.get("BRICKOWL_PASSWORD", "")
```

The BrickOwl buyer account must be separate from (or dual-role to) the API developer account. Verify whether BrickOwl allows the same account to hold both a developer API key and buyer privileges.
