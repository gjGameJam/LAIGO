# LEGO.com Playwright Session — Operator Reference

## 1. Overview

LEGO.com orders are placed via Playwright Chromium automation in
`scripts/checkout/clients/lego_client.py`. **LEGO.com 2FA is email-only**
— no TOTP, no SMS, no authenticator-app option — so headless login is
impossible. Instead, we cache a logged-in `storage_state` JSON (cookies
+ localStorage) in the Neon `external_sessions` table and reuse it on
every order. The session is seeded **once** via manual Google SSO through
`scripts/seed_lego_session.py` (Google SSO bypasses LEGO's email 2FA
because LEGO trusts Google's identity verification).

When the cached state expires (LEGO rotates cookies, or the row is
missing on a fresh deploy), the saga writes `MANUAL_REVIEW` with
`manual_review_reason` starting with `"LEGO Playwright session unavailable"`
and emits a `lego.session_expired` audit event. An operator re-runs the
seed script to restore service — **no redeploy required.**

---

## 2. Component map

| File | Role |
|---|---|
| `scripts/migrations/sql/0003_external_sessions.{up,down}.sql` | Schema (migration 0003) |
| `scripts/checkout/lego_session_store.py` | `load_storage_state()` / `save_storage_state()` against Neon |
| `scripts/checkout/clients/lego_client.py` | Loads state in `order_from_lego`; probes `/profile`; raises `LegoSessionExpiredError` on bounce-to-login |
| `scripts/checkout/saga.py` | Catches the error → `MANUAL_REVIEW` with `hold_disposition=OPERATOR_DECIDES`; emits `lego.session_expired` |
| `scripts/seed_lego_session.py` | Standalone seeder (headed Playwright + DB write) |
| `scripts/checkout/models.py` | `LegoSessionExpiredError` + `ERROR_MESSAGES["lego_session_expired"]` |

---

## 3. Schema

`external_sessions` (added in migration `0003`):

```sql
CREATE TABLE external_sessions (
    provider       TEXT          PRIMARY KEY,
    storage_state  JSONB         NOT NULL,
    notes          TEXT,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
```

Currently exactly one row: `provider='lego'`. Future providers (e.g.,
BrickOwl session-based scraping) can land here without schema changes.

---

## 4. Initial seeding (one-time per Neon branch)

Run on your local machine. Prerequisites:

- `DB_BACKEND=postgres` (in `.env` or inline `$env:DB_BACKEND = "postgres"`)
- `DATABASE_URL=<Neon pooler DSN for the target branch>` in `.env.secrets`
- Migration `0003` applied to **that same branch** (verify via `scripts/diagnose_db.py`)
- `playwright install chromium` once

```powershell
$env:DB_BACKEND = "postgres"
.\.venv\Scripts\python.exe -m scripts.seed_lego_session
```

A headed Chromium window opens at the LEGO login page. The script
auto-clicks "Continue with Google" (selector `[data-testid='googleBtn']`).
Complete whatever Google asks for. End state: LEGO loads as your
logged-in account. Return to the terminal and press **ENTER**. The
script probes `/profile`, extracts `storage_state`, and UPSERTs the row.

### Branch mismatch — the common first-seed pitfall

If `ALEMBIC_DATABASE_URL` and `DATABASE_URL` point at different Neon
branches, seeding fails with `UndefinedTableError: relation
"external_sessions" does not exist` even though `alembic upgrade head`
reported success. Diagnose with:

```powershell
$env:DIAG_DSN = "<the DSN you want to check>"
.\.venv\Scripts\python.exe scripts\diagnose_db.py
```

Run it against both DSNs; `alembic_version` should match. If not, either
re-apply alembic to the branch `DATABASE_URL` points at, or repoint
`DATABASE_URL` at the branch that's already at the expected version.

---

## 5. Refresh runbook (when session expires)

### How expiry presents

- `GET /jobs/{job_id}/checkout/{checkout_id}/status` returns
  `saga_status: "manual_review"` with `manual_review_reason` starting
  `"LEGO Playwright session unavailable (...)"`.
- An audit row in `audit_events` with event `lego.session_expired`.
- The customer sees the deliberately-vague string from
  `ERROR_MESSAGES["lego_session_expired"]`:
  *"Finalizing your order. You will get an email soon."* — we do NOT
  surface "our automation broke."

### Recovery steps

1. **Re-seed the session** by re-running the same command from §4.
   UPSERT replaces the stale row.
2. **For each stuck MANUAL_REVIEW order**, the operator chooses:
   - **Complete it out-of-band:** place the LEGO order at
     `lego.com/profile/orders` using the item list from the order's
     `allocation`, then capture the Stripe hold in the dashboard.
   - **Refund:** cancel the Stripe hold in the dashboard. BrickOwl
     orders already placed need manual cancellation through BrickOwl
     until automatic cancel ships (roadmap #5).

The `hold_disposition` field is set to `operator_decides` for this case,
so the orphan-hold reconciler (`reconcile_orphan_holds`) will **not**
auto-cancel — your option to capture stays open. See PRE_RELEASE
§9.3 + `HoldDisposition` in `models.py`.

---

## 6. Caveats

- **No automated re-seed.** Email-OTP automation via IMAP polling is a
  future option but deliberately not built. The trade-off was discussed
  on 2026-06-09 and the manual-refresh path was chosen for v1 simplicity.
- **`storage_state` is sensitive.** Treat the `external_sessions` row
  like a password. It carries LEGO.com authentication for the LAIGO
  account. Access to the row = ability to place orders against the
  saved payment method.
- **Stripe hold auto-expiry is 7 days.** A MANUAL_REVIEW order left
  untouched past 7 days will lose its hold; BrickOwl orders are STILL
  real until manually cancelled. Don't let `lego.session_expired` events
  sit untouched.
- **Render egress IP.** If LEGO honors a "remember this device" cookie
  keyed to IP, the cached session can live for weeks. If Render's
  egress IP rotates, sessions die more often. Measure session lifetime
  in production before optimizing.
- **Production env mismatch is hard to diagnose without `diagnose_db.py`.**
  Always confirm `ALEMBIC_DATABASE_URL` and `DATABASE_URL` reach the
  same Neon branch (matching `alembic_version`) before assuming a seed
  worked.
