# Security posture & audit remediation (ACTIVE — PWYW product)

Scope: the live pay-what-you-want product surface — `scripts/Main.py`,
`scripts/pay_router.py`, `scripts/emailer.py`, `scripts/picToMosiac.py`,
`scripts/Util.py`, and the single-active Stripe provider. The **shelved**
checkout/marketplace pipeline is out of scope (its historical FMEA lives in
`docs/CHECKOUT_AUDIT.md`).

A safety/security audit was run **2026-07-06**, benchmarked against the OWASP
API Top-10, Stripe webhook guidance, Pillow decompression-bomb hardening, and
FastAPI production hardening. The **payment core was found solid**: the webhook
fails closed (bad signature → reject), paid records are unforgeable, and the
email dedup sentinel is atomic. Everything below is defense-in-depth on top of
that.

This document is the source of truth for **what shipped** and **what is still
open**. When an open item ships, move it up to "Shipped" (git history is the
archive). CLAUDE.md's "Security posture" subsection is the one-paragraph index
into this doc.

---

## Shipped

Three review waves, all landed on `main` (commits `a765056`, `9212a89`,
`a233153`). Covered by `scripts/test_web_hardening.py` (plus the existing
payment suites `test_pay_router` / `test_donate_router` / `test_emailer`).

### PII / financial data never web-served

- **`/artifacts` is allowlisted** (`_AllowlistStaticFiles` in `Main.py`). Only
  `artifact.zip`, `order_list*.json`, `preview.json`, `stats.json`, and
  `manifest.json` are served; everything else 404s (fail-closed — a *new*
  sidecar dropped under `outputs/` later is denied by default).
- **Per-job PII/financial sidecars moved out of the web tree** into
  `private/{job_id}/` (`PRIVATE_DIR`, default `./private`): `payment.json`
  (amount + PaymentIntent id) and `email.json` (customer address + send-dedup
  sentinel). `pay_router.py` and `emailer.py` (via a `sentinel_dir` param) both
  write there; the cleanup thread purges `PRIVATE_DIR/{job_id}` at the same TTL
  as the job. `manifest_failed.json` (tracebacks + settings) stays under
  `outputs/` but is now 404'd by the allowlist.
- **`/queue` no longer leaks job UUIDs** (was returning enumerable job ids).
- **Email PII scrubbed from logs.** `emailer._mask_email()` → `g***@domain` for
  INFO lines; the `email.send_failed` path logs only `http=<status>
  resend_error=<name>`, never the Resend response body (which echoes the
  recipient). The raw address only ever lands in the TTL-purged
  `private/{job_id}/email.json`. The `pay_router` webhook missing-`job_id`
  branch logs `source`/`type`/`has_email`, not the full metadata dict.

### Abuse / DoS bounds on `/generate`

- **Per-IP rate limit** (`_enforce_generate_rate_limit`,
  `GENERATE_RATE_LIMIT_SECONDS`=20): one image per window per IP → 429 +
  `Retry-After`. Dependency-free in-process limiter (correct only because the
  web layer is a single uvicorn process — move to Redis if `--workers N` is ever
  added). `/pay` and `/donate` are deliberately unlimited (light Stripe calls).
- **Intake concurrency cap** (`GENERATE_INTAKE_CONCURRENCY`=3): an
  `asyncio.Semaphore` held across the streaming upload write + the full-res
  decode. The queue cap (`MAX_QUEUE_SIZE`) only bounds *dispatched* work — the
  store row is written *after* the decode — so without this a burst of uploads
  each streams up to `MAX_UPLOAD_SIZE_MB` to disk and decodes ~0.5 GB of pixels
  in the web process, OOM-killing the 2 GB tier.
- **Decompression-bomb cap**: `picToMosiac.py` sets `Image.MAX_IMAGE_PIXELS`
  from `MAX_IMAGE_PIXELS` (80 MP) — an oversized image raises instead of
  decoding.
- **Spoof-proof client IP** (`real_ip_middleware`): takes the **rightmost**
  `X-Forwarded-For` entry. Render *appends* the real client to whatever XFF the
  client sent (it does not reset it), so the leftmost entry is
  attacker-controllable — taking it let a caller rotate the header to mint a
  fresh rate-limit key per request and bypass the throttle. Rightmost is the
  entry Render itself appended. **Do not revert to leftmost.**

### Prod hardening (gated on `_IS_RENDER = is_truthy(os.getenv("RENDER"))`)

- `/docs`, `/redoc`, `/openapi.json` disabled on Render (API-surface recon aid;
  still available in local dev).
- `TrustedHostMiddleware` (Host-header defense) — base allowlist
  `*.onrender.com` + `RENDER_EXTERNAL_HOSTNAME` + localhosts + `testserver`;
  extend via the `TRUSTED_HOSTS` env var if a custom API domain is added
  (otherwise it 400s). Off Render the middleware isn't installed.
- **Security headers on every response** (`security_headers_middleware`):
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, HSTS always; strict CSP
  (`default-src 'none'; frame-ancestors 'none'; base-uri 'none'`) prod-only so
  local Swagger's CDN assets still load.
- `http://localhost:5173` dropped from CORS on Render (only the deployed
  frontend origin remains).
- `/generate` errors return a fixed message (no raw `{e}` echo); the root
  handler no longer advertises `/docs`.

### Local-dev correctness

- `load_project_env()` loads `.env.secrets` with `override=True` so a committed
  `.env` value can't shadow a real secret (local dev only — Render loads
  neither, per D-048).
- `.gitignore` hardened; all committed runtime/debug/test artifacts removed from
  the tree (LEGO debug captures, a test upload, a build pack, `laigo.log.1`,
  redundant `stella2-4` — `images/stella1.jpg` kept for the smoke driver). See
  commits `9212a89`, `5c78787`.

---

## Open / pending

> **Resolved 2026-07-07 — captured LEGO.com / Google sessions.** The
> now-deleted `outputs/lego_debug/_verify_storage_state.json` held a live
> LEGO.com session (from the shelved Playwright automation). The Google session
> was revoked 2026-07-06, and both the LEGO.com and Google sessions have since
> **timed out on their own** — the captured credential is dead. No action
> remains; this was the only item with real credential exposure.

### 1. Deferred infra hardening (not code-only — needs a deploy/CI decision)

- **Webhook `event.id` idempotency ledger.** Currently harmless — the email
  sentinel + the idempotent `_record_payment` already dedupe Stripe event
  redelivery — but an explicit processed-event ledger is the belt-and-suspenders
  fix.
- **Dependency lockfile with hashes** (`pip-compile --generate-hashes`). Changes
  the Render build command, hence deferred.
- **Secret-scanning in CI**: `gitleaks` pre-commit + a TruffleHog history scan.

### 2. Optional git-history rewrite — nice-to-have

`git filter-repo --path outputs/ --path inputs/ --path laigo.log --path
laigo.log.1 --invert-paths` would purge the deleted runtime files from history.
**Downgraded to optional**: a PII review found the history is *all first-party*
(owner email/name, a placeholder DOB `2000-01-01`, a nickname), **no
customer/third-party PII**, passwords are masked in the screenshots, and the
repo is private. Do it only before going public or adding collaborators.

---

## Deploy-watch

- **Rightmost-XFF assumption.** The `real_ip_middleware` fix assumes Render is
  the single appending hop. If Render ever adds a second *internal* appending
  hop, the rightmost entry could become a constant internal IP → a single global
  throttle for all callers. Sanity-check periodically that observed
  `request.state.real_ip` values look like diverse real client IPs.
- **Single web process.** The in-process rate limiter and intake semaphore are
  correct only while the app runs as one uvicorn process. If the Render start
  command ever grows `--workers N`, both must move to a shared store (Redis).
