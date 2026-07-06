# Build-Pack Email Delivery (ACTIVE — PWYW product)

> **Rollout status (2026-07-05):**
> - ✅ Backend code complete, tested, pushed. Verified end-to-end locally:
>   a real $0 checkout delivered the pack (both attachments) to the owner's
>   inbox via Resend dev mode; duplicate suppression and failure/retry
>   confirmed live.
> - ✅ Local `.env.secrets` has `RESEND_API_KEY` and `STRIPE_WEBHOOK_SECRET`
>   (dashboard destination secret) set.
> - ✅ Stripe **test-mode** event destination created:
>   `https://laigo.onrender.com/webhooks/stripe`, event
>   `payment_intent.succeeded` only, snapshot payloads.
> - ❌ **Render env vars NOT set yet**: `RESEND_API_KEY` and
>   `STRIPE_WEBHOOK_SECRET` must be added to the `laigo` service (the local
>   `.env.secrets` never deploys — it's gitignored). Until then, production
>   skips email sends (fails soft) and answers the webhook 503 (Stripe will
>   retry, and eventually pauses the destination).
> - ✅ **laigo-frontend email field shipped and LIVE** (verified 2026-07-05:
>   the deployed bundle at laigo-frontend.onrender.com contains the email
>   input and sends `email` in both the $0 and paid `/pay` bodies —
>   frontend repo commit `041cb6a`). Production checkout no longer 422s.
>   Spec remains in "Frontend contract" below. One cosmetic deviation:
>   buttons validate email on click rather than being disabled until valid.
> - ❌ Custom domain not verified — dev-mode Resend delivers ONLY to the
>   Resend account owner's own address; customers receive nothing until a
>   domain is verified and `EMAIL_FROM` is updated.
> - ⏳ Live-mode Stripe: when switching off `sk_test_`, create a second
>   live-mode event destination (its own `whsec_`) and update Render.

After a pay-what-you-want checkout — including $0 — the customer is emailed
their build pack (instructions PDF + every Pick-a-Brick order list, splits
included) via [Resend](https://resend.com). The email **is** the delivery:
files are read from `outputs/{job_id}/` at send time and attached, so nothing
(including the customer's address) is retained beyond the existing job TTL
(~1h). Subject: "Your LAIGO Mosaic Maker build pack is ready" (appears only
as the subject, never in the body); the body carries step-by-step Pick a
Brick ordering instructions — sign-in optional, upload the list, then the
exact button flow "View All Pieces" → "Pick Selected Pieces" → "Add To Bag" →
"View Bag" → checkout, plus the multi-file note for split orders (all pieces
are assumed available; the PDF is flagged as the build guide only) — and ends
with the job id as its very last element.

Module: `scripts/emailer.py` (leaf — stdlib + httpx; the Resend API is one
JSON POST, no SDK dependency). Tests: `python -m scripts.test_emailer`,
plus the send-trigger coverage in `test_pay_router`.

## Trigger points (all in `scripts/pay_router.py`, via FastAPI BackgroundTasks)

```
POST /jobs/{id}/pay  amount==0        ─→ send (email from request body)
POST /jobs/{id}/pay  sync success     ─→ send (email from request body)
POST /webhooks/stripe payment_intent.succeeded
                                      ─→ send (email from PaymentIntent
                                          metadata — covers 3DS completions
                                          and lost sync responses)
```

- `email` is a **required** field on `PayRequest` (loose regex validation;
  Resend/Stripe are the real validators). The frontend pay modal collects it.
- For card payments `/pay` stamps the PaymentIntent with
  `metadata={job_id, source: "laigo_pay", email}` and `receipt_email` (Stripe
  sends its own payment receipt in live mode). The PaymentIntent is the only
  place the address is stored server-side-adjacent — LAIGO keeps nothing.
- The webhook sends only when metadata has a valid `email`,
  `source == "laigo_pay"`, and `type != "tip"`. `/donate` tips never trigger a
  build-pack email.
- Sends are fire-and-forget: `send_build_pack_email` never raises and runs
  after the HTTP response is flushed. A send failure can never fail a charge.

## Duplicate protection — `outputs/{job_id}/email.json`

The sync success path and the webhook can both fire for one payment (and
Stripe redelivers events). The first sender claims the sentinel atomically
(`open(..., "x")`); later attempts return `"duplicate"`. Status `"failed"`
releases the claim so a webhook redelivery retries. Schema:

```json
{
  "job_id": "…", "to": "customer@example.com",
  "status": "sent" | "failed",
  "detail": "<resend id or error>",
  "attachments": ["order_list.json", "instructions.pdf"],
  "link_only_fallback": false,
  "recorded_at": 1783600000.0
}
```

`"skipped"` (disabled / no API key) writes **no** sentinel, so enabling email
later still allows a webhook-replay send. The file is purged with the job dir
by the TTL cleanup.

## Attachments and the oversize fallback

- Order lists — **all** `OrderLists/order_list*.json` members of
  `artifact.zip`, extracted in-memory at send time and sorted numerically
  (`order_list.json`, `order_list_1.json`, …; large mosaics split when any
  element exceeds 999). Falls back to the stable job-root `order_list.json`
  (first 999-capped chunk only) when the zip is missing or unreadable.
- `instructions.pdf` — exists **only inside `artifact.zip`** (the workspace is
  deleted after zipping); extracted in-memory from the
  `Instructions/instructions.pdf` member at send time.
- Resend caps the message at 40 MB **after base64** (×4/3 inflation). If the
  raw bytes would exceed `_ENCODED_CAP_BYTES` (35 MB encoded), or the PDF
  member is missing, the email attaches the order lists only and carries the
  `GET /jobs/{id}/download` link with an "expires in about an hour" note
  (`link_only_fallback: true`).
- Link origin: `PUBLIC_API_BASE_URL` env, falling back to Render's auto-set
  `RENDER_EXTERNAL_URL`. With neither, the fallback copy says to use the
  browser tab.

## Configuration

| Variable | Where | Meaning |
|---|---|---|
| `EMAIL_ENABLED` | `.env` | Master switch. `true` in the committed .env. |
| `EMAIL_FROM` | `.env` | Sender identity. Dev default `LAIGO <onboarding@resend.dev>`. |
| `PUBLIC_API_BASE_URL` | `.env` | Origin for download links; empty on Render (fallback below). |
| `RESEND_API_KEY` | `.env.secrets` / Render env | Secret. Empty ⇒ sends skipped + boot warning. |
| `RENDER_EXTERNAL_URL` | set by Render | Automatic link-origin fallback. |

All env reads happen at call time — a key added to `.env.secrets` takes
effect on the next send after a restart; no code change for any of this.

## Resend account / go-live

1. **Dev (now):** create a Resend account + API key → `RESEND_API_KEY` in
   `.env.secrets`. With the `onboarding@resend.dev` sender, Resend delivers
   **only to the account owner's own address** — perfect for testing, useless
   for customers.
2. **Production:** verify a custom domain in Resend (add its SPF + DKIM DNS
   records), then set `EMAIL_FROM=LAIGO <builds@yourdomain.com>`. That's the
   whole switch.
3. **3DS coverage prerequisite:** the webhook path only works once
   `STRIPE_WEBHOOK_SECRET` is set (currently empty ⇒ webhook returns 503).
   Local: `stripe listen --forward-to localhost:8000/webhooks/stripe`. Prod:
   Stripe Dashboard → Webhooks → endpoint for `payment_intent.succeeded`.
4. **Pricing guardrail:** free tier is 3,000 emails/mo (100/day); next tier
   $20/mo for 50k. If volume ever outgrows that, Amazon SES ($0.10/1k) is the
   swap — the provider surface is the single `_http_post` seam in
   `scripts/emailer.py`.

## Frontend contract (laigo-frontend repo)

The pay modal must collect a **required** email and include it in the
`POST /jobs/{id}/pay` body for both $0 and paid flows. The backend with this
requirement is already pushed — email-less `/pay` calls 422, so this is the
blocking frontend task.

Request body:

```json
{"amount_cents": 0, "payment_method_id": "pm_…", "email": "customer@example.com"}
```

- `email`: required for ALL amounts including 0; max length 254; trimmed
  server-side; must match `^[^@\s]+@[^@\s]+\.[^@\s]+$` (loose by design —
  the frontend should mirror this, not be stricter).
- `payment_method_id`: unchanged — only required when `amount_cents > 0`.

**Two distinct 422 shapes** the frontend must handle:

1. Field validation (missing/malformed email, pydantic) — `detail` is an
   **array**; find entries whose `loc` contains `"email"` and render inline
   on the email input:
   ```json
   {"detail": [{"type": "missing", "loc": ["body", "email"], "msg": "Field required"}]}
   {"detail": [{"type": "value_error", "loc": ["body", "email"],
                "msg": "Value error, must be a valid email address"}]}
   ```
2. Business rules (existing behavior) — `detail` is an **object**:
   `{"detail": {"error": "…", "code": "AMOUNT_BELOW_MINIMUM", "min_cents": 50}}`
   (also `PAYMENT_METHOD_REQUIRED`, `INVALID_JOB_ID` 400, `JOB_NOT_FOUND` 404,
   `PAYMENTS_UNAVAILABLE`/`PAYMENT_RETRYABLE` 503, `PAYMENT_FAILED` 402).

Validation order: body shape (422) → job_id charset (400) → artifact exists
(404) — a bad email wins over a bad job id.

Success responses are unchanged (`free` / `paid` / `requires_action`).
**3DS:** after `requires_action`, run Stripe.js confirmation with the
returned `client_secret` as today and do NOT re-call `/pay` — the backend
webhook detects the completion and sends the email itself (the address rides
in the PaymentIntent metadata).

UX requirements: email input required before both the free-download button
and the card submit; success states should say the pack was emailed (mention
checking spam); keep the existing ungated download button — email is a
parallel channel, not a replacement. `/pay` responses never surface email
send success/failure (fire-and-forget by design), so "no email arrived at a
test address" in dev mode is expected, not a bug. `POST /donate` is
unchanged (no email, never emailed).
