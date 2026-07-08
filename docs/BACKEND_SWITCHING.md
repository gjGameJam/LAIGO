# BACKEND_SWITCHING.md — switching the storage backend (JSON ⇄ Neon Postgres)

LAIGO can run its job/checkout state on either of two backends, selected by the
single env var `DB_BACKEND`:

| Value | Backend | State lives in | Survives restart? |
|-------|---------|----------------|-------------------|
| `json` (default) | In-memory dict + on-disk artifacts | process memory (job metadata) + `outputs/` (zips, payments) | **No** for job metadata; **yes** for finished artifacts |
| `postgres` | Neon Postgres via asyncpg | `jobs` / `checkouts` / `sagas` / … tables on Neon | **Yes** |

This file is the **switch runbook** — how to flip between the two safely, in
both directions, locally and on Render. For steady-state Neon operations
(migrations, operator SQL, debug playbook) see `docs/DATABASE_OPS.md`; this doc
only covers the *cutover*.

> **Current state (2026-06-18):** `DB_BACKEND=json`. Neon is parked because the
> free-tier compute quota is exhausted (the pre-deploy `alembic upgrade head`
> fails with `exceeded the compute time quota`). JSON is the intended backend
> for the pay-what-you-want phase — see the pivot note in `CLAUDE.md`.

---

## 0. TL;DR

**To JSON** (no DB needed):

1. `DB_BACKEND=json`
2. `CHECKOUT_ENABLED=false` (B47 refuses boot otherwise)
3. On Render: **clear the pre-deploy command** (the `alembic upgrade head` line)

**To Neon Postgres** (DB required):

1. Neon project reachable + schema migrated to head (currently `0003`)
2. `DATABASE_URL` = Neon **pooler** DSN, `DATABASE_URL_DIRECT` = Neon **direct** DSN (should already exist)
3. `DB_BACKEND=postgres`
4. On Render: set pre-deploy command to
   `ALEMBIC_DATABASE_URL="$DATABASE_URL_DIRECT" alembic upgrade head`

The dispatchers re-read `DB_BACKEND` **per call**, so on Render a backend flip is
an env-var change + restart — **no code change, no redeploy of the image**.

---

## 1. What `DB_BACKEND` actually controls

The value is read (case-insensitively) by `db.is_postgres_backend()`
(`scripts/db.py:74`) and by the two dispatcher modules. **The only value that
selects Postgres is the literal string `postgres`.** Anything else — including
`neon`, `pg`, an empty string, or a typo — falls through to JSON:

```python
return os.environ.get("DB_BACKEND", "json").lower() == "postgres"
```

When `DB_BACKEND=postgres`, these routes change:

| Subsystem | JSON path | Postgres path |
|-----------|-----------|----------------|
| Mosaic job lifecycle | `jobs_store_json` (in-process dict) | `jobs_store_pg` (`jobs` table) — via `jobs_store_dispatch` |
| Checkout state (shelved saga) | `checkout_store` (JSON file) | `checkout_store_pg` (`checkouts`+`sagas`) — via `checkout_store_dispatch` |
| `init_pool()` | no-op | opens the asyncpg pool, runs `SELECT 1` |
| `verify_schema()` | no-op | refuses boot unless DB revision == `_EXPECTED_SCHEMA_VERSION` |
| `resume_in_flight_sagas()` | no-op | routes non-terminal sagas to terminal at boot |
| `start_reconcile_task()` | no-op | starts the periodic orphan-hold reconciler |

What `DB_BACKEND` does **not** control:

- **Pay-what-you-want payments.** `POST /jobs/{id}/pay` and the Stripe webhook
  write `private/{job_id}/payment.json` to disk (a non-web-served dir — see
  `docs/SECURITY.md`) and record in Stripe — never the DB. Switching backends
  does not move or lose payment records.
- **Finished mosaic artifacts.** `artifact.zip`, `order_list.json`,
  `preview.json`, `manifest_failed.json` are always on disk under
  `outputs/{job_id}/`, regardless of backend. The per-job PII/financial
  sidecars (`payment.json`, `email.json`) sit under `private/{job_id}/`.
- **The shelved checkout saga routers.** `DB_BACKEND=postgres` re-points the
  *storage* of checkout state, but it does **not** re-mount the saga endpoints.
  See §8.

---

## 2. The full set of knobs

| Knob | Where it lives | Role in the switch |
|------|----------------|---------------------|
| `DB_BACKEND` | `.env` (committed) / Render env | `json` or `postgres` — the master switch |
| `DATABASE_URL` | `.env.secrets` (local) / Render env | Neon **pooler** DSN (host contains `-pooler`). Read by `init_pool()` only when `postgres`. |
| `DATABASE_URL_DIRECT` | Render env (or your shell as `ALEMBIC_DATABASE_URL`) | Neon **direct** DSN (no `-pooler`). Used **only** by the pre-deploy migration. |
| `CHECKOUT_ENABLED` | `.env` / Render env | Must be `false` whenever `DB_BACKEND≠postgres` (B47). |
| Render **Pre-Deploy Command** | Render dashboard → Settings → Build & Deploy | Runs `alembic upgrade head` against Neon on every deploy. Must be **present** for postgres, **cleared** for json. |
| `_EXPECTED_SCHEMA_VERSION` | `scripts/db.py:178` | Code constant the DB revision must match in postgres mode. Currently `"0003"`. |

`DATABASE_URL` belongs in `.env.secrets` (gitignored), never in committed `.env`.

---

## 3. Config precedence — the `override=False` gotcha

`load_project_env()` calls `load_dotenv(...)` with the default `override=False`
(`scripts/Util.py:23`). That means:

> **A real OS / Render-dashboard environment variable WINS over the committed
> `.env`.** The `.env` file only fills in values that are not already set in the
> environment.

Consequences:

- On **Render**, dashboard env vars are the source of truth. If the dashboard
  has `DB_BACKEND=postgres`, the committed `.env`'s `DB_BACKEND=json` is ignored
  — the app will try to open Neon and crash at boot if Neon is unreachable.
- **Locally**, if you `setx`/`$env:DB_BACKEND` in your shell, that overrides
  `.env` for that process. To rely on `.env`, make sure no shell/OS var shadows
  it.
- To go to JSON, it is **not enough** to edit `.env` if a dashboard/shell
  override exists — you must change it where the override lives.

---

## 4. Boot invariants that gate the switch

The `Main.py` lifespan enforces these in order (all raise `RuntimeError` → uvicorn
exits — fail loud, never partial-up):

1. **`verify_alembic_head_matches_expected()`** — runs **unconditionally**, even
   on JSON. Refuses boot if `_EXPECTED_SCHEMA_VERSION` (`scripts/db.py`) drifts
   from the head of `scripts/migrations/versions/`. *This can bite a JSON-mode
   boot too* — if you add/remove a migration file, bump the constant in the same
   change.
2. **`init_pool()`** (postgres only) — refuses boot if `DATABASE_URL` is missing,
   malformed, or points at the **direct** endpoint instead of the pooler.
3. **`verify_schema()`** (postgres only) — refuses boot if the DB's
   `alembic_version` ≠ `_EXPECTED_SCHEMA_VERSION`, or the table is missing/empty.
4. **B47** (`Main.py:279`) — refuses boot if `CHECKOUT_ENABLED=true` **and**
   `DB_BACKEND≠postgres`. This is why JSON mode requires `CHECKOUT_ENABLED=false`.
5. **L1 gate** — if `CHECKOUT_ENABLED=true` but the gate computes `DISABLED`
   (missing/invalid Stripe key, etc.), refuses boot.

Practical rule of thumb:

- **JSON** ⇒ `CHECKOUT_ENABLED=false`, no `DATABASE_URL` needed.
- **Postgres** ⇒ `DATABASE_URL` (pooler) set + schema at head. `CHECKOUT_ENABLED`
  may be `true` *or* `false` (postgres without the saga is a valid "durable jobs
  store" config — see §8).

---

## 5. Switching TO JSON  (postgres → json)

Use this when Neon is parked/over quota, or for local dev without a DB. This is
the current production configuration.

### 5a. Local

1. In `.env` (or wherever your override lives) set:
   ```
   DB_BACKEND=json
   CHECKOUT_ENABLED=false
   ```
2. Make sure no shell var shadows it:
   ```powershell
   # Should print nothing (or 'json'):
   $env:DB_BACKEND
   # If it prints 'postgres', clear it for this session:
   Remove-Item Env:\DB_BACKEND
   ```
3. Start the server from the project root:
   ```powershell
   .venv\Scripts\activate.ps1
   uvicorn scripts.Main:app --reload
   ```
4. Confirm the boot log shows:
   ```
   DB pool skipped (DB_BACKEND=json; Phase F not yet flipped)
   ```
   `DATABASE_URL` can stay set or unset — it is ignored in JSON mode.

### 5b. Render

1. **Clear the pre-deploy command** — this is the step that fixes the
   `exceeded the compute time quota` failure. Render → service → **Settings →
   Build & Deploy → Pre-Deploy Command** → delete
   `ALEMBIC_DATABASE_URL="$DATABASE_URL_DIRECT" alembic upgrade head` → Save.
   With JSON there is no schema to migrate.
2. Render → **Environment**:
   - Set `DB_BACKEND=json` (or delete the var so the committed `.env` default
     applies — but an explicit value is clearer).
   - Set `CHECKOUT_ENABLED=false` (delete it if present and truthy).
   - You may leave `DATABASE_URL` / `DATABASE_URL_DIRECT` in place — they are
     dead vars under JSON and make re-enabling Neon a one-toggle job later.
3. Save → Render restarts the service automatically (no manual redeploy needed;
   the dispatchers re-read the env on boot).
4. Confirm in the deploy/runtime log:
   ```
   DB pool skipped (DB_BACKEND=json; Phase F not yet flipped)
   ```

**Caveat — JSON job metadata is in-memory.** A job that is queued/running, or
whose status someone is polling, is **lost on restart** (Render restarts on
deploy and on free-tier idle). Finished `artifact.zip` files persist on disk.
For the pay-what-you-want download flow this is acceptable; just know it's the
tradeoff vs. Neon.

---

## 6. Switching TO Neon Postgres  (json → postgres)

Use this when you want durable job metadata (and/or to re-enable the checkout
saga — see §8). Requires a Neon project with available compute.

### 6a. Prerequisites

1. **A reachable Neon project/branch.** If the existing one is over quota,
   either upgrade the Neon plan (Launch, ~$19/mo, also disables free-tier
   autosuspend) or create a fresh project. See `docs/DATABASE_OPS.md §8`.
2. **Two DSNs for the same branch** (only `-pooler` differs in the host):
   - Pooler → `DATABASE_URL` (app traffic)
   - Direct → `DATABASE_URL_DIRECT` (migrations)
   Both **must** point at the same Neon branch — diverging them is the
   2026-05-22 cutover bug (`DATABASE_OPS.md §5 "Cutover gotcha"`).
3. **Schema migrated to head.** Current head / `_EXPECTED_SCHEMA_VERSION` is
   `0003`. `verify_schema()` refuses boot if the DB is behind.

### 6b. Local

1. Put the secret DSNs in `.env.secrets` (gitignored — never commit):
   ```
   DATABASE_URL=postgresql://USER:PASS@ep-xxxx-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```
2. Apply migrations against the **direct** endpoint (alembic refuses the pooler):
   ```powershell
   $env:ALEMBIC_DATABASE_URL = "postgresql://USER:PASS@ep-xxxx.us-east-1.aws.neon.tech/neondb?sslmode=require"
   .venv\Scripts\activate.ps1
   alembic upgrade head
   # Expect: Running upgrade 000x -> 0003   (or "already at head")
   ```
3. Set the backend:
   ```
   # .env
   DB_BACKEND=postgres
   ```
   (Leave `CHECKOUT_ENABLED=false` unless you are also re-enabling the saga — §8.)
4. Boot and confirm the log:
   ```
   DATABASE_URL host='ep-xxxx-pooler.us-east-1.aws.neon.tech'
   DB pool initialized (Neon Postgres backend active)
   DB schema verified (alembic revision matches _EXPECTED_SCHEMA_VERSION)
   ```
5. Sanity-check the DSN any time with:
   ```powershell
   $env:DIAG_DSN = "<pooler or direct DSN>"
   .\.venv\Scripts\python.exe scripts\diagnose_db.py    # prints db/user/schema/version
   ```

### 6c. Render

1. Render → **Environment**:
   - `DATABASE_URL` = pooler DSN
   - `DATABASE_URL_DIRECT` = direct DSN (same branch, no `-pooler`)
   - `DB_BACKEND=postgres`
2. Render → **Settings → Build & Deploy → Pre-Deploy Command**:
   ```
   ALEMBIC_DATABASE_URL="$DATABASE_URL_DIRECT" alembic upgrade head
   ```
3. Deploy. Watch the deploy log for the pre-deploy line
   `Running upgrade 000x -> 0003` (or a clean no-op if already at head), then the
   runtime log for `DB pool initialized (Neon Postgres backend active)`.
4. If boot refuses with `Schema version mismatch` or `alembic_version table
   missing`, the pre-deploy didn't migrate the DB the app reads — confirm
   `DATABASE_URL` and `DATABASE_URL_DIRECT` share the same `ep-XXXX` prefix
   (`DATABASE_OPS.md §5`).

---

## 7. Verifying which backend is actually live

| Signal | JSON | Postgres |
|--------|------|----------|
| Boot log | `DB pool skipped (DB_BACKEND=json; …)` | `DB pool initialized (Neon Postgres backend active)` + `DATABASE_URL host='…-pooler…'` |
| `diagnose_db.py` | n/a | prints `database/user/schema/version` |
| Job durability | metadata gone after restart | metadata survives restart |

Always trust the **boot log line**, not what you *think* the env is set to — the
`override=False` precedence (§3) means the dashboard can be overriding `.env`.

---

## 8. What does NOT switch with `DB_BACKEND`: the shelved checkout saga

`DB_BACKEND=postgres` only changes *where state is stored*. The entire checkout
saga pipeline (hold → marketplace order → capture) is **shelved**: its routers
are not mounted in `Main.py` (only `pay_router`, `webhook_router`,
`donate_router`, `checkout_gate_router` are). Flipping to Postgres gives you a
**durable jobs store**, not automated ordering.

Re-enabling the saga is a **separate, larger change** (see the pivot note in
`CLAUDE.md`): restore the shelved imports + `app.include_router(...)` calls in
`Main.py`, and set `CHECKOUT_ENABLED=true`. That, in turn, *requires*
`DB_BACKEND=postgres` (B47), the marketplace/Stripe secrets, and the LEGO
Playwright session seeded (`docs/LEGO_SESSION.md`). Do not conflate "turn the DB
back on" with "turn checkout back on."

So three valid configurations exist:

| `DB_BACKEND` | `CHECKOUT_ENABLED` | Result | Boots? |
|--------------|--------------------|--------|--------|
| `json` | `false` | Current PWYW config — in-memory jobs, no saga | ✅ |
| `postgres` | `false` | Durable jobs store, saga still shelved | ✅ |
| `postgres` | `true` | Durable jobs + saga re-enabled (needs router re-mount + secrets) | ✅ |
| `json` | `true` | — | ❌ B47 refuses boot |

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Pre-deploy fails: `exceeded the compute time quota` | Neon free-tier compute exhausted; pre-deploy still runs `alembic upgrade head` | Going JSON: **clear the pre-deploy command** (§5b). Staying Neon: upgrade the Neon plan. |
| Boot crash: `DATABASE_URL points at the Neon DIRECT endpoint, not the pooler` | `DATABASE_URL` has no `-pooler` in host | Use the pooler DSN for `DATABASE_URL`; keep the direct one in `DATABASE_URL_DIRECT`. |
| Boot crash: `Schema version mismatch: DB at 'X', app expects 'Y'` | DB not migrated to `_EXPECTED_SCHEMA_VERSION` | Run `alembic upgrade head` against the **direct** endpoint of the branch the app reads. |
| Boot crash: `alembic_version table missing` | DB never migrated | Same as above — apply migrations to that branch. |
| Boot crash: `B47: DB_BACKEND must be 'postgres' when CHECKOUT_ENABLED=true` | JSON backend with checkout enabled | Set `CHECKOUT_ENABLED=false` (JSON) or `DB_BACKEND=postgres` (re-enable). |
| Set `DB_BACKEND=json` in `.env` but app still hits Neon | A Render dashboard / shell env var overrides `.env` (§3) | Change/clear `DB_BACKEND` where the override actually lives. |
| App reports JSON but you expected Neon (or vice versa) | Reading intent instead of reality | Check the boot log line (§7); run `diagnose_db.py`. |
| Switched postgres → json; old rows "disappeared" | JSON backend can't see rows written during the postgres window | Expected. Switch back to postgres to see them, or accept the loss (pre-launch only). |

---

## 10. Cross-references

- `docs/DATABASE_OPS.md` — Neon connection model, migration workflow, operator
  SQL, debug playbook, `diagnose_db.py`, emergency rollback (incl. the
  postgres→JSON backend rollback in §7).
- `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` — migration history (Phases A–F) and
  the Phase F cutover/cleanup playbook (§9.4).
- `CLAUDE.md` → **Configuration** table (the `DB_BACKEND` / `CHECKOUT_ENABLED`
  rows) and the pay-what-you-want pivot note.
- `scripts/db.py` — `is_postgres_backend()`, `init_pool()`, `verify_schema()`,
  `_EXPECTED_SCHEMA_VERSION`, the pooler/direct guards.
- `scripts/jobs_store_dispatch.py` / `scripts/checkout/checkout_store_dispatch.py`
  — the per-call routing this switch toggles.
- `scripts/migrations/env.py` — alembic config; refuses pooler DSNs.
