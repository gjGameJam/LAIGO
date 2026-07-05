# Build-Pack Email Delivery (ACTIVE — PWYW product)

After a pay-what-you-want checkout — including $0 — the customer is emailed
their build pack (instructions PDF + Pick-a-Brick order list) via
[Resend](https://resend.com). The email **is** the delivery: files are read
from `outputs/{job_id}/` at send time and attached, so nothing (including the
customer's address) is retained beyond the existing job TTL (~1h).

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

- `order_list.json` — stable copy at the job root.
- `instructions.pdf` — exists **only inside `artifact.zip`** (the workspace is
  deleted after zipping); extracted in-memory from the
  `Instructions/instructions.pdf` member at send time.
- Resend caps the message at 40 MB **after base64** (×4/3 inflation). If the
  raw bytes would exceed `_ENCODED_CAP_BYTES` (35 MB encoded), or the PDF
  member is missing, the email attaches the order list only and carries the
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

The pay modal must collect a required email and include it in the
`POST /jobs/{id}/pay` body for both $0 and paid flows; missing/malformed
email now returns 422. **Deploy the frontend change before or together with
the backend** — after the backend lands, email-less `/pay` calls fail.
