# Checkout Completion Plan — LAIGO → LEGO → Customer

**Purpose.** Everything required to finish automating the end-to-end purchase:
from a finished mosaic on the LAIGO frontend, through customer **payment**,
through automated ordering on **LEGO.com**, to **notifying the customer** and
delivering the building **instructions PDF** by email.

**How to use this doc.** Each workstream (A–E) is independently buildable except
where a dependency is called out. Start at `§9 Sequencing` for the critical
path. `§10 Open decisions` lists what needs a human answer before building.

> Status legend: ✅ done · 🟡 partial · ❌ not started · ⛔ gated/blocked

---

## 0. Current state snapshot

| Layer | State | Notes |
|---|---|---|
| Mosaic pipeline (photo → bricks + instructions PDF) | ✅ | Produces `outputs/{job_id}/order_list.json` + `artifact.zip` (contains `Instructions/instructions.pdf`) |
| `POST /jobs/{job_id}/checkout/quote` | ✅ | Returns price breakdown; 10-min TTL; collects `shipping_country`, `shipping_zip`, `customer_email` only |
| `POST /jobs/{job_id}/checkout/confirm` | ✅ | Async saga; expects `checkout_id` + `stripe_payment_method_id` |
| `GET /jobs/{job_id}/checkout/{checkout_id}/status` | ✅ | Poll saga; exposes `saga_status`, `customer_message`, hold/charge fields |
| Saga (hold → orders → capture + compensation) | ✅ | `scripts/checkout/saga.py`; defense-in-depth gate L0–L6 |
| Stripe provider | 🟡 | Wired in **TEST** mode (`STRIPE_ENABLED=True`, `sk_test_…`); manual-capture hold flow |
| LEGO.com Playwright — search/availability | ✅ | GraphQL Pick-a-Brick endpoint |
| LEGO.com Playwright — upload→cart→checkout (Steps B–D) | ✅ | Verified + ported (`docs/ORDER_OPTIMIZER.md §6.1`) |
| LEGO.com Playwright — place-order (Step E) | ⛔ | Gated; payment-page selectors not captured; real-charge |
| LEGO.com Playwright — runs on Render | ⛔ | **Cloudflare blocks headless** → needs VPS+proxy browser host (`docs/LEGO_BROWSER_HOST.md`) |
| Full shipping address collection | 🟡 | Backend plumbed (2026-06-11): `ShippingAddress` (US-only, validated) on `ConfirmRequest` → persisted to `checkouts.shipping_address` JSONB (migration `0004`) → loaded by saga → threaded into `order_from_lego(..., shipping_address)`. Step E form-fill (C2) + frontend form (B) still pending. **Migration `0004` must be applied to Neon before this code boots in postgres mode.** |
| Payment UI (frontend) | ❌ | No Stripe Elements; `/confirm` has no caller |
| Customer email + instructions delivery | 🟡 | Built (2026-06-11): `scripts/checkout/notifications.py` (Resend over httpx; never-raises; no-op until `RESEND_API_KEY`+`EMAIL_FROM` set; idempotent via `sagas.emails_sent`). Wired into the saga choke point — order-received at start; kit-on-the-way (PDF attached) / manual-review / not-charged at terminal status. `run_job` now drops `instructions.pdf` next to `order_list.json`. **Remaining (operator):** Resend account + domain SPF/DKIM/DMARC, set the env vars. Refund email helper exists but reconciler wiring deferred to §6. |
| BrickOwl / BrickLink ordering | ⛔ | `create_order` raises `NotImplementedError`; out of scope for v1 (see `§7`) |

**v1 sourcing assumption:** the optimizer currently single-sources **LEGO.com**
(BrickOwl ordering is not possible via its seller API). This plan therefore
treats **LEGO.com as the only fulfilment channel for v1**. Multi-source is `§7`.

---

## 1. Target end-to-end flow

**Customer journey (frontend):**
1. Upload photo → mosaic job → 3D preview (existing).
2. Click **"Order this kit."**
3. Enter **shipping address + email**; frontend calls `POST /quote` → shows
   price breakdown (pieces + shipping + LAIGO fee + total).
4. Enter card (**Stripe Elements**) → frontend creates a PaymentMethod →
   `POST /confirm`.
5. Frontend **polls `/status`** → shows live state, then a success screen.

**System sequence (backend):**
```
/quote  → optimize → cache quote (checkout_id, 10-min TTL)
/confirm→ saga.execute_checkout_saga:
            Stripe hold (auth, manual capture)
            → order_from_lego(items, job_id, SHIPPING_ADDRESS)   ← drop-ships to CUSTOMER
                via VPS+proxy headed browser (Cloudflare-safe)
            → Stripe capture
            → emit emails (confirmation, then "on the way" + instructions PDF)
```

**Two physical things reach the customer:**
- **Loose bricks** — shipped by LEGO directly to the customer's address (LAIGO
  is merchant of record; see memory `project_payment_architecture` — do NOT
  re-introduce a forward/handoff flow).
- **Building instructions PDF** — LEGO ships only bricks, so LAIGO emails the
  generated `instructions.pdf` separately. **This is the "email with
  instructions" the customer needs to assemble the mosaic.**

---

## 2. Workstream A — Shipping address + email plumbing (backend prerequisite)

**Why first:** both LEGO checkout (drop-ship) and email need a full address +
email persisted to the checkout state. Today only `country`+`zip`+`email` are
collected and the address never reaches `order_from_lego`.

**Build:**
1. **Extend `QuoteRequest`** (`models.py`) — keep `shipping_country/zip` for
   the cost estimate, and add a structured `shipping_address`:
   `full_name, line1, line2?, city, state, postal_code, country, phone?`.
   (Collecting at quote keeps one form; or collect at confirm — see `§10`.)
2. **Persist** address + `customer_email` into the checkout/saga state so the
   saga and the email layer can read them. Add columns to the `checkouts`
   table (migration `000N`) following the B45 NOT-NULL discipline in
   `CLAUDE.md`, OR store as a JSONB `shipping` blob. Thread through
   `checkout_store_pg.save()` (`_CHECKOUT_COLS` + `_insert_checkouts`).
3. **Pass address into `order_from_lego(items, job_id, shipping_address)`** and
   into `_run_checkout` Step E (address form fill — see `§4`).
4. **Validation** — server-side address validation (required fields, country
   allowlist = whatever LEGO ships to from the chosen storefront). Reject at
   `/quote` or `/confirm` with the `{detail:{code}}` shape.

**Acceptance:** a confirmed checkout has a complete, validated shipping address
and email in `checkout_state`, retrievable by both the saga and the email layer.

---

## 3. Workstream B — Payment UI (frontend)

**Goal:** let the customer pay. Backend `/confirm` already expects a Stripe
`pm_…`; nothing creates one today.

**Build (on `laigo-frontend`):**
1. **Load Stripe.js** with `STRIPE_PUBLISHABLE_KEY` (already in `.env.secrets`;
   expose the publishable key to the frontend build).
2. **Quote screen** — call `/quote`, render `QuoteResponse`: per-seller
   subtotals, `total_cost_cents`, `laigo_service_fee_cents`, `grand_total_cents`,
   and `expires_at` (show a countdown; re-quote on expiry). Block the pay button
   when `can_proceed == false` (unsourceable items present).
3. **Payment screen** — Stripe **Payment Element** (preferred over Card Element;
   supports wallets + future SCA). On submit:
   - If staying with the current server-side manual-capture flow:
     `stripe.createPaymentMethod()` → `pm_…` → `POST /confirm {checkout_id,
     stripe_payment_method_id}`.
   - **SCA/3DS caveat** (`§8`): `createPaymentMethod` does NOT perform 3DS. If a
     card requires authentication, the server-side hold returns
     `requires_action` and fails. For production resilience, consider switching
     to a **PaymentIntent client-confirmation** flow (server creates the intent
     with `capture_method=manual`, returns `client_secret`, frontend
     `stripe.confirmPayment()` handles 3DS). This is a backend+frontend change —
     decide in `§10`.
4. **Status screen** — poll `GET /status`; map `saga_status` →
   UI state. Render **`customer_message`** verbatim on any error (never
   `error`). Handle the documented response shapes:
   - `409` duplicate confirm `{detail:{error, saga_status, poll_url}}`
   - `422` active checkout `{detail:{error, code:"ACTIVE_CHECKOUT_EXISTS"}}`
   - `503` gate closed `{detail:{error, code:"CHECKOUT_GATE_CLOSED", mode}}`
5. **Success screen** — "Order confirmed; bricks shipping from LEGO; building
   instructions emailed to you."

**Backend touchpoints:** none beyond Workstream A, unless adopting the
PaymentIntent SCA flow (then add a `client_secret` to `ConfirmResponse` or a new
endpoint).

**Acceptance:** a customer can complete quote → pay → see the saga result
entirely from the UI, against TEST Stripe.

---

## 4. Workstream C — LEGO checkout completion (browser host + Step E)

This is two sub-parts: **(C1)** make Playwright run at all on the server
(Cloudflare), **(C2)** finish the place-order steps.

### C1 — Cloudflare-safe browser host (BLOCKING for any live ordering)
- **Decided approach:** VPS + residential-proxy **headed** `browserless/chromium`,
  reached from Render via `LEGO_BROWSER_CDP_URL`. Full design, security model,
  docker-compose, and runbook: **`docs/LEGO_BROWSER_HOST.md`**.
- **Next action:** make the `§7` provider decisions in that doc (residential
  proxy w/ IP-whitelist, VPS+region, hostname, image), provision, and smoke-test
  that `connect_over_cdp` renders PaB (not the Cloudflare wall) from a
  **residential** exit IP.
- Code side already done: `order_from_lego` branches on `LEGO_BROWSER_CDP_URL` /
  `LEGO_PLAYWRIGHT_HEADLESS` (`scripts/checkout/clients/lego_client.py`).

### C2 — Step E: address → delivery → payment → place order
Steps B–D (upload → cart → "Checkout Securely") are verified+ported. Step E (the
`/cart` → checkout → place-order pages) is **gated and uncaptured**. To finish:
1. **Capture selectors** with `scripts/checkout/_inspect_pab.py` (extend it to
   click "Checkout Securely" and dump the checkout pages) — stopping before the
   final place-order button. Needed selectors:
   - **Shipping address form** — must set the address to the **customer's**
     (Workstream A), not LAIGO's saved address. This is the drop-ship step.
   - **Delivery/shipping method** selection.
   - **Payment** — confirm LAIGO's saved card is selected/used.
   - **Place Order** button + **order confirmation number** locator.
2. **Implement Step E** in `_run_checkout` (replace the scaffold guesses
   `place-order-button` / `order-confirmation-number`).
3. **Test strategy (`§10`)** — verifying a real place-order means a **real
   charge** to LAIGO's card. Options: a low-value throwaway order cancelled
   immediately at `lego.com/profile/orders`, or a dedicated test account. Decide
   before un-gating.
4. **Un-gate carefully** — Step E stays behind `CHECKOUT_ENABLED` + the L0–L6
   gate; flip only after end-to-end TEST passes. Keep `STRIPE_ENABLED` TEST until
   `§6`.

**Edge cases to handle in Step E:** address rejected/needs-validation by LEGO,
item out of stock at order time (`StockoutError` branch already exists),
delivery unavailable to the address, saved card declined on LEGO's side,
session expired mid-checkout (`LegoSessionExpiredError` branch already exists).

**Acceptance:** with C1 live, a TEST run drives PaB → checkout → (throwaway)
real order → confirmation number captured → saga reaches `PAYMENT_CAPTURED`.

---

## 5. Workstream D — Customer email + instructions delivery

**Goal:** transactional emails at each lifecycle point, and deliver the
building `instructions.pdf`. No email infrastructure exists today.

**Build:**
1. **Choose a provider** (`§10`): Resend (simple API, good DX), Postmark
   (transactional deliverability), AWS SES (cheapest at scale), or SendGrid.
   Add `EMAIL_API_KEY` to `.env.secrets`, `EMAIL_FROM` + domain to `.env`.
2. **Domain auth for deliverability** — SPF, DKIM, DMARC on the sending domain.
   Without these, order emails land in spam. (Operator/DNS task.)
3. **New module `scripts/checkout/notifications.py`** — mirror the `audit.py`
   contract: **never raises** (a failed email must not break checkout; log
   `[email] FAILED` at CRITICAL). Functions like
   `send_order_confirmation(...)`, `send_kit_on_the_way(..., instructions_pdf)`,
   `send_manual_review_notice(...)`, `send_refund_notice(...)`.
4. **Deliver the instructions PDF.** Today `instructions.pdf` lives inside
   `outputs/{job_id}/artifact.zip` (`Instructions/instructions.pdf`). Either:
   - **(a)** add a `run_job` copy step that drops `instructions.pdf` next to
     `order_list.json` at `outputs/{job_id}/instructions.pdf` (mirrors the
     existing order-list handoff), then attach it; or
   - **(b)** serve a signed/expiring download link (the app already mounts
     `/artifacts`); email the link instead of a large attachment.
   Recommendation: **(a) attach** for ≤ a few MB, else **(b) link**.
5. **Trigger points (wire into `saga.py` + router):**
   | When | Email | Content |
   |---|---|---|
   | `/confirm` accepted (saga `INITIATED`) | Order received | summary + total; "we're placing your order" |
   | `PAYMENT_CAPTURED` (success) | **Kit on the way + instructions** | LEGO order #, what ships, **attach/link `instructions.pdf`**, assembly tips |
   | `MANUAL_REVIEW` | Soft delay notice | the `customer_message` (intentionally vague); "we'll email within 24h" |
   | `COMPENSATED` / `FAILED` | Not charged | the `customer_message`; how to retry |
   | Refund (reconciler / operator) | Refund confirmation | amount, reason |
6. **Idempotency** — persist an `emails_sent` flag/set on the checkout state so
   retried saga steps or reconciler passes don't double-send. (Mirror the
   `payment_holds`/audit best-effort pattern.)
7. **Source of truth for copy** — customer-facing strings come from
   `ERROR_MESSAGES` (`models.py`) where applicable; don't invent new
   error wording in the email layer (coordinated change rule).

**Acceptance:** a successful TEST checkout sends a confirmation email and a
"kit on the way" email carrying the correct `instructions.pdf`; a forced
MANUAL_REVIEW sends the soft-delay notice; no double-sends on saga retry.

---

## 6. Workstream E — Go-live

1. **Stripe LIVE flip** — follow `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md`: swap
   `sk_live_…` (Render only; the L1 boot invariant refuses `sk_live_` off
   Render), keep `STRIPE_ENABLED`, verify the gate reports `LIVE`.
2. **Un-gate ordering** — only after C2 + D pass end-to-end in TEST. Keep the
   L0–L6 gate; `CHECKOUT_ENABLED=true` is already set.
3. **Monitoring/alerting** — the audit log already emits P0 events
   (`payment.hold_orphan`, `lego.session_expired`, `marketplace.endpoint_unavailable`).
   Wire these to real paging (email/Slack/PagerDuty) before taking money.
4. **Reconciler/refund** — orphan-hold reconciler exists; confirm refund emails
   (`§5`) fire on its cancel path.
5. **Soft launch** — low order cap; watch the saga + audit closely; the
   MANUAL_REVIEW path is the safety net (hold + orders preserved).

---

## 7. Out of scope for v1 (deferred, but tracked)

- **BrickOwl ordering** — `create_order` raises `NotImplementedError`; BrickOwl's
  API is seller-side (no buyer ordering). Would need Playwright automation à la
  LEGO, or be left as price-comparison only. v1 single-sources LEGO.com. See
  `docs/ORDER_OPTIMIZER.md §6.2.1`. Memory `project_bricklink_deferred`.
- **BrickLink** — stub; behind `BRICKLINK_ENABLED`. Post-v1.
- **Multi-source allocation at order time** — the optimizer can already allocate
  across sellers, but ordering only works for LEGO, so keep allocation
  LEGO-only until a second channel can actually be ordered from.

---

## 8. Cross-cutting concerns

- **SCA / 3DS** (`§3`): the current `createPaymentMethod` + server-side hold does
  NOT handle 3DS. US cards often skip it; EU/UK will not. Decide whether to move
  to a PaymentIntent client-confirmation flow before serving non-US cards.
- **Pricing accuracy — LEGO PaB service fee:** during verification, LEGO added a
  **$7.00 "service fee"** to a $0.54 Pick-a-Brick order (small-order penalty).
  **Mechanism added 2026-06-11:** `optimizer.apply_lego_service_fee()` folds a
  configurable order-level fee into the LEGO seller line, wired into the router
  quote + saga re-optimize. Env: `LEGO_SERVICE_FEE_CENTS` (default **0 = no-op**),
  `LEGO_SERVICE_FEE_WAIVER_THRESHOLD_CENTS` (LEGO piece subtotal at/above which
  the fee is waived; 0 = never waive). **Still open:** the real fee amount +
  waiver threshold must be measured against a live LEGO cart total in Step E (C2)
  before enabling — until set, the quote does NOT include the fee (default-off
  avoids over-charging with a guess, but also means small orders under-charge).
  Step E has a TODO to capture LEGO's real total and verify it's ≤ the hold.
- **Idempotency everywhere** — Stripe keys (`hold-{cid}`, `capture-{cid}`) exist;
  add for emails (`§5.6`) and ensure LEGO order placement isn't double-run on
  saga resume (`saga_resume.py` routes in-flight sagas — confirm a placed LEGO
  order isn't re-placed).
- **Security** — the browser-host CDP endpoint controls a LEGO session with a
  saved card; lock down per `docs/LEGO_BROWSER_HOST.md §3`. Customer PII (full
  address) now lives in the DB — confirm it's covered by the same handling as
  other sensitive columns.
- **Address ↔ shipping-cost consistency** — quote shipping is estimated from
  `country/zip`; the real LEGO checkout computes exact shipping. Reconcile drift
  with the existing B5 pre-placement drift check (refuse if real total exceeds
  the authorized hold + buffer).

---

## 9. Sequencing & milestones

Dependency-ordered (each milestone is demoable):

```
M1  C1  Browser host provisioned + PaB renders from residential IP   ← unblocks all live ordering
M2  A   Shipping address + email plumbed end-to-end (DB + saga)      ← unblocks C2 drop-ship & D
M3  C2  Step E captured + implemented; TEST throwaway order works    (needs M1, M2)
M4  B   Payment UI: quote → pay → status, against TEST Stripe        (needs A for the address form)
M5  D   Email notifications + instructions PDF delivery              (needs M2; M3 for success email)
M6  E   Stripe LIVE + un-gate + paging + soft launch                 (needs M3, M4, M5)
```

Parallelizable: **B (M4)** and **D (M5)** can proceed alongside C2 once A is
done. **C1** can be provisioned independently at any time and is the longest
external-dependency lead (VPS + proxy signup).

---

## 10. Open decisions (need a human answer before/within each workstream)

- [x] **Address collection point** — **at `/confirm`** (decided 2026-06-11).
      `/quote` stays a country+zip cost estimate; full `ShippingAddress` rides
      on `ConfirmRequest`.
- [ ] **SCA/3DS** — keep simple PaymentMethod flow (US-only risk) or adopt
      PaymentIntent client-confirmation now? (US-only v1 makes this deferrable.)
- [x] **Email provider** — **Resend** (decided 2026-06-11). Module built;
      operator still owns the account + SPF/DKIM/DMARC DNS.
- [x] **Instructions delivery** — **attach the PDF** (decided 2026-06-11).
      `run_job` drops `outputs/{job_id}/instructions.pdf`; notifications attaches it.
- [ ] **LEGO Step E test strategy** — throwaway low-$ orders cancelled via
      `/profile/orders`, or a dedicated test account/sandbox?
- [ ] **Browser-host providers** — `docs/LEGO_BROWSER_HOST.md §7` (residential
      proxy w/ IP-whitelist, VPS+region, hostname, browser image).
- [x] **Shipping destinations** — **US only for v1** (decided 2026-06-11).
      Drives US-only `ShippingAddress` validation, lego.com US storefront, and
      lets the current Stripe PaymentMethod flow stand. Expand post-v1.

---

## 11. Testing & launch checklist

- [ ] C1: `connect_over_cdp` renders PaB (not Cloudflare) from residential exit IP.
- [ ] A: confirmed checkout persists full validated address + email.
- [ ] C2: TEST throwaway order → confirmation number → saga `PAYMENT_CAPTURED`;
      order ships to the **customer** address (drop-ship verified).
- [ ] B: full UI quote→pay→status against TEST Stripe, incl. 409/422/503 handling.
- [ ] D: confirmation + "on the way (with `instructions.pdf`)" + manual-review
      emails fire; no double-sends on saga retry; PDF is the correct job's.
- [ ] Pricing: quote total matches real LEGO checkout total (incl. PaB service fee).
- [ ] Saga resume does not re-place an already-placed LEGO order.
- [ ] E: Stripe LIVE reported by gate; P0 audit events page a human.
- [ ] Soft launch with order cap; MANUAL_REVIEW path exercised once intentionally.

---

## Related docs
- `docs/ORDER_OPTIMIZER.md` — checkout pipeline reference; `§6.1` LEGO Playwright
  selector map.
- `docs/LEGO_BROWSER_HOST.md` — Cloudflare-safe browser host (Workstream C1).
- `docs/LEGO_SESSION.md` — `storage_state` seeding + refresh runbook.
- `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` — Stripe LIVE flip + gating.
- `docs/DATABASE_OPS.md` — migrations for the new address columns (Workstream A).
