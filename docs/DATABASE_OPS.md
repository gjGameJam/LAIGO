# DATABASE_OPS.md — Neon Postgres operations + debugging

LAIGO uses a single Neon Postgres database in production. This file is the
how-to / debug reference. Read top-to-bottom when you come back cold; jump to
"Debug playbook" when something's on fire.

Companions:
- `docs/PRE_RELEASE_PAYMENT_CHECKLIST.md` — the migration history (Phases A–F)
  and acceptance criteria. Don't re-litigate; this doc covers steady-state ops.
- `docs/CHECKOUT_AUDIT.md` — the audit FMEA. Most "why does this column
  exist?" questions point back here.
- `scripts/migrations/sql/000N_*.up.sql` — the canonical schema source.
- `scripts/db.py` — the asyncpg pool + boot guards.

---

## 1. What lives in Neon

Six tables. All schemas live in the `public` namespace. Owner role:
`neondb_owner`. DB name: `neondb`.

| Table          | One row per                     | Hot accessors                                                    |
|----------------|---------------------------------|------------------------------------------------------------------|
| `jobs`         | `POST /generate`                | `scripts/jobs_store_pg.py`                                       |
| `checkouts`    | `POST /quote`                   | `scripts/checkout/checkout_store_pg.py`                          |
| `sagas`        | `POST /confirm`                 | `scripts/checkout/checkout_store_pg.py`, `saga.py`, `saga_resume.py` |
| `payment_holds`| Each successful `create_hold`   | `scripts/checkout/payment_holds_store.py`, `reconcile.py`        |
| `audit_events` | Each `audit.emit(...)`          | `scripts/checkout/audit.py` (write only — append-only log)       |
| `schema_meta`  | Single-row app-level metadata   | currently unused; reserved                                       |

Plus `alembic_version` (managed by alembic — never write to it by hand).

### Key relationships

```
jobs (1) ───< checkouts (N) ───< sagas (1 active per job)
                                        │
                                        └──< payment_holds (N per checkout)

audit_events ←── job_id, checkout_id  (FK omitted; events outlive everything)
```

- `sagas.job_id` FK is `ON DELETE RESTRICT` — a non-terminal saga blocks
  job-row cleanup. This is intentional (cleanup_expired skips per-row).
- `checkouts.job_id` FK is `ON DELETE CASCADE` — deleting a job nukes its
  quotes too. Only the cleanup loop deletes jobs (terminal + TTL expired).
- `audit_events` has no FK constraints. It must survive job/checkout deletions
  for retention/forensics.

### Critical constraints

| Constraint                       | Where                       | What it protects                                          |
|----------------------------------|-----------------------------|-----------------------------------------------------------|
| `jobs_status_valid`              | `jobs.status`               | Only 5 known status values                                |
| `jobs_progress_range`            | `jobs.progress_pct`         | 0–100 inclusive                                           |
| `sagas_status_valid`             | `sagas.saga_status`         | Only 8 known status values                                |
| `sagas_one_active_per_job_idx`   | partial unique index        | B23 — at most one non-terminal saga per `job_id`          |
| `sagas.hold_disposition` CHECK   | added by migration 0002     | `cancel_safe` / `operator_decides` / NULL only (B55)      |

The partial unique index is the **structural fix** for the concurrent-
`/confirm` race that plagued the JSON backend. INSERT collision → asyncpg
`UniqueViolationError` → router translates to HTTP 422 with code
`ACTIVE_CHECKOUT_EXISTS`. Don't bypass.

---

## 2. Connection model

### Two endpoints, two purposes

Neon exposes every compute endpoint twice:

| Endpoint kind | Hostname shape                                       | Used by                          | Why                                                          |
|---------------|------------------------------------------------------|----------------------------------|--------------------------------------------------------------|
| **Pooler**    | `ep-xxx-pooler.region.aws.neon.tech`                 | App runtime (asyncpg)            | PgBouncer in transaction mode → cheap connection scale-out   |
| **Direct**   | `ep-xxx.region.aws.neon.tech`                         | Migrations (alembic), psql, debug| Session mode → multi-statement transactions, named prepares  |

Same data, same branch — different wire-level connection routing.

### The Render env vars

| Env var               | Endpoint kind | Read by                                                              |
|-----------------------|---------------|----------------------------------------------------------------------|
| `DATABASE_URL`        | Pooler        | `scripts/db.py:init_pool()` (the asyncpg pool)                       |
| `DATABASE_URL_DIRECT` | Direct        | Render's pre-deploy hook: `ALEMBIC_DATABASE_URL=$DATABASE_URL_DIRECT alembic upgrade head` |

**Both env vars MUST point at the same Neon branch.** Only the hostname
differs (one has `-pooler`, the other doesn't). Diverging them is the bug
that bit the Phase F cutover on 2026-05-22 — see "Debug playbook → Cutover
gotcha" below.

### Pool tuning (don't change without reading the docstring)

`scripts/db.py:init_pool()`:

```python
min_size=2, max_size=10
statement_cache_size=0          # required for PgBouncer transaction mode
command_timeout=10.0            # per-query ceiling
max_inactive_connection_lifetime=300
init=_register_jsonb_codec      # JSONB ↔ Python dict transparency
```

- `statement_cache_size=0` is non-negotiable on the pooler — transaction-mode
  PgBouncer can't safely route named prepared statements.
- `command_timeout=10` is the per-query belt. The saga's 900s outer ceiling
  is the authoritative timeout for orchestration; this is just "don't hang
  on a single round-trip."
- The JSONB codec lets call sites pass `dict`/`list` directly. **Never
  `json.dumps()` JSONB values at the call site — that double-encodes** and
  stores a quoted string. Same anti-pattern that broke initial Phase E
  tests.

### Boot order (lifespan startup)

`scripts/Main.py` lifespan, in strict order:

1. `verify_alembic_head_matches_expected()` — code-time drift check
2. `init_pool()` → `SELECT 1` smoke test
3. `verify_schema()` — compares DB's `alembic_version` against
   `_EXPECTED_SCHEMA_VERSION` in `db.py`
4. Event loop captured by `jobs_store_dispatch`
5. Payment provider registered (Stripe)
6. L1 boot invariants (`CHECKOUT_ENABLED` + gate; live key off-Render;
   `CHECKOUT_ENABLED` + JSON backend = refused)
7. `resume_in_flight_sagas()` (postgres only)
8. ProcessPoolExecutor + scheduler + cleanup threads
9. Cache sweeper
10. Reconcile task (postgres only)

Any of 1–7 raising `RuntimeError` causes uvicorn to exit. Partial-up is
worse than fail-loud.

---

## 3. Migration workflow

### Files per migration

For migration `000N`, three files (all under `scripts/migrations/`):

```
sql/000N_<slug>.up.sql      # the actual DDL
sql/000N_<slug>.down.sql    # exact inverse — for emergency rollback
versions/000N_<slug>.py     # thin alembic wrapper (revision = "000N", down_revision = "000(N-1)")
```

Plus: bump `_EXPECTED_SCHEMA_VERSION` in `scripts/db.py` to `"000N"` in the
SAME PR. `verify_alembic_head_matches_expected()` runs unconditionally at
boot and refuses to start if these drift.

### Schema evolution discipline (B45 contract)

For any new NOT NULL column **without a DEFAULT**, update the explicit INSERT
helper(s) in the same PR:

- `jobs` → `_JOB_COLS` + `insert_queued` in `scripts/jobs_store_pg.py`
- `checkouts` → `_CHECKOUT_COLS` + `_insert_checkouts` in `scripts/checkout/checkout_store_pg.py`
- `sagas` → `_SAGA_COLS` + `_insert_sagas` in `scripts/checkout/checkout_store_pg.py`

Otherwise the next INSERT fails on the missing column with a confusing
asyncpg error. The column-tuple-in-code pattern is what catches this.

### Inside the .up.sql file

**No inner `BEGIN;` / `COMMIT;`**. Alembic wraps the upgrade in its own
transaction. An inner COMMIT commits that wrapper prematurely (Postgres has
no nested transactions). If the connection drops between the inner COMMIT
and the `alembic_version` write, the schema is committed but unrecorded —
unrecoverable except by manually dropping every table.

### Applying a migration

**Local dev (against Neon `dev` branch):**

```powershell
# Get the DIRECT endpoint DSN from Neon dashboard, branch=dev, toggle
# "Pooled connection" OFF, copy.
$env:ALEMBIC_DATABASE_URL = "postgresql://...@ep-xxx.region.aws.neon.tech/neondb?sslmode=require"
alembic upgrade head
# Look for: Running upgrade 000(N-1) -> 000N
```

`scripts/migrations/env.py` refuses pooler DSNs with an actionable error
("drop `-pooler` from the host"). If you forget, it tells you.

**Render production:**

A pre-deploy hook runs `ALEMBIC_DATABASE_URL="$DATABASE_URL_DIRECT" alembic
upgrade head` automatically on every deploy. Verify in the Render deploy log
for the line `Running upgrade 0001 -> 0002` (or whatever revisions apply).

If the pre-deploy hook ever appears to silently no-op (no `Running upgrade`
line, no error), one of these is true:

1. The target DB is already at head (the common case — re-deploys).
2. `DATABASE_URL_DIRECT` points at a different DB than `DATABASE_URL`.
   Confirm with diagnose_db.py against both env values.
3. The hook is not actually wired. Check Render → Settings → Build & Deploy
   → Pre-Deploy Command. Expected value:
   `ALEMBIC_DATABASE_URL="$DATABASE_URL_DIRECT" alembic upgrade head`

### Adding migration `000N` end-to-end

1. Author `sql/000N_<slug>.up.sql` (no inner BEGIN/COMMIT).
2. Author `sql/000N_<slug>.down.sql` — exact inverse.
3. Author `versions/000N_<slug>.py` with the correct `revision` /
   `down_revision`.
4. Bump `_EXPECTED_SCHEMA_VERSION` in `scripts/db.py`.
5. Update column tuples + INSERT helpers if applicable (B45).
6. Test on a throwaway Neon branch first:
   - Neon dashboard → Branches → Create branch (copy-on-write, free).
   - Run `alembic upgrade head` against that branch's DIRECT endpoint.
   - Verify expected shape with `\d+ <table>` in psql or Neon's SQL editor.
7. Apply to `dev` branch.
8. Open PR. When merged + deployed to Render, pre-deploy hook applies to
   the production branch.

### Rolling back a migration

`alembic downgrade -1` against the DIRECT endpoint. Only do this if you've
confirmed no app instance is running against the same DB — otherwise the
running app's `verify_schema()` will refuse next boot.

For production rollbacks, prefer a **forward-only hot-fix migration**
(`000N+1` that reverses what `000N` did) so the schema graph stays linear.
True downgrades are reserved for "I made a mistake in dev, undo."

---

## 4. Operator queries

Run these in Neon's SQL editor (Neon dashboard → laigo project → branch
`main` → SQL Editor). Or via psql with the direct endpoint.

### Active queue + in-flight jobs

```sql
SELECT job_id, status, mosaic_type, width_blocks, progress_pct,
       queued_at, started_at
FROM jobs
WHERE status IN ('queued', 'running')
ORDER BY queued_at;
```

### Recent failures

```sql
SELECT job_id, status, error_message, queued_at, completed_at
FROM jobs
WHERE status IN ('failed', 'timed_out')
  AND completed_at > NOW() - INTERVAL '1 day'
ORDER BY completed_at DESC
LIMIT 50;
```

### Sagas needing operator attention

The two queries to alert on daily:

```sql
-- Active MANUAL_REVIEW sagas (last day)
SELECT checkout_id, job_id, saga_status, manual_review_reason,
       payment_hold_id, hold_disposition, last_transition_at
FROM sagas
WHERE saga_status = 'manual_review'
  AND last_transition_at > NOW() - INTERVAL '1 day'
ORDER BY last_transition_at DESC;

-- Non-terminal sagas stuck >1h (the reconciler's stuck-saga threshold)
SELECT checkout_id, job_id, saga_status, payment_hold_id,
       last_transition_at, NOW() - last_transition_at AS stale_for
FROM sagas
WHERE saga_status NOT IN ('payment_captured','compensated','failed','manual_review')
  AND last_transition_at < NOW() - INTERVAL '1 hour';
```

### Orphan / stuck payment_holds

```sql
-- Holds Stripe still authorizes (per our records) and the reconciler hasn't touched
SELECT h.hold_id, h.checkout_id, h.amount_authorized_cents, h.currency,
       h.last_known_status, h.last_reconciled_at,
       s.saga_status, s.hold_disposition
FROM payment_holds h
LEFT JOIN sagas s USING (checkout_id)
WHERE h.last_known_status = 'requires_capture'
  AND h.last_reconciled_at < NOW() - INTERVAL '2 hours'
ORDER BY h.last_reconciled_at;
```

### Audit log — recent gate rejections

```sql
SELECT ts, event, actor_ip, actor_user_agent,
       data->>'mode' AS gate_mode,
       data->'reasons' AS reasons,
       job_id
FROM audit_events
WHERE event = 'gate.confirm_rejected'
  AND ts > NOW() - INTERVAL '1 hour'
ORDER BY ts DESC;
```

### Audit log — per-job timeline

When investigating a specific customer issue:

```sql
SELECT ts, event, actor_type, data
FROM audit_events
WHERE job_id = '<job_id>'
ORDER BY ts;
```

### "Show me everything for one checkout"

```sql
-- Replace :cid with the checkout_id
WITH c AS (SELECT :cid::text AS cid)
SELECT 'checkout' AS kind, row_to_json(checkouts) AS row
FROM checkouts, c WHERE checkout_id = c.cid
UNION ALL
SELECT 'saga', row_to_json(sagas) FROM sagas, c WHERE checkout_id = c.cid
UNION ALL
SELECT 'payment_hold', row_to_json(payment_holds) FROM payment_holds, c WHERE checkout_id = c.cid
UNION ALL
SELECT 'audit', row_to_json(audit_events) FROM audit_events, c WHERE checkout_id = c.cid
ORDER BY 1;
```

### Schema version sanity check

```sql
SELECT version_num FROM alembic_version;
-- Should match scripts/db.py:_EXPECTED_SCHEMA_VERSION
```

---

## 5. Debug playbook

### Symptom: app refuses to boot with `Schema version mismatch: DB at 'X', app expects 'Y'`

**Root cause:** The DB the app connects to (`DATABASE_URL`) is at a
different alembic revision than the code expects.

**Steps:**

1. Check Render deploy log for the line `DATABASE_URL host='ep-xxx-pooler...'`
   (printed by `init_pool` before the pool opens). That tells you which
   Neon endpoint the app reached.
2. Run `diagnose_db.py` against the SAME DSN that env var points to (see
   §6 below).
3. If the DB is genuinely behind: run `alembic upgrade head` against the
   DIRECT endpoint of that same branch.
4. If the DB is ahead (code is older): redeploy app code that matches.
5. **Cutover gotcha**: confirm `DATABASE_URL` and `DATABASE_URL_DIRECT`
   share the same `ep-XXXX` prefix. See "Cutover gotcha" below.

### Symptom: pre-deploy alembic silently completes (no `Running upgrade` line) but app boot still mismatches

Pre-deploy migrated one DB; the app reads from a different DB. This is
exactly the Phase F cutover bug from 2026-05-22.

```bash
# Pre-deploy hook reads $DATABASE_URL_DIRECT
# App's init_pool reads $DATABASE_URL
# If they point at DIFFERENT Neon branches/projects, you see this exact pattern.
```

Fix: make both env vars in Render share the same `ep-XXXX` prefix. Only
`-pooler` should differ. See §2 above for the convention.

### Cutover gotcha (the 2026-05-22 lesson)

The Phase F cutover took 90 minutes longer than expected because Render's
`DATABASE_URL` and `DATABASE_URL_DIRECT` were silently pointing at two
**different** Neon endpoints (different projects, in fact). Pre-deploy
alembic was migrating one branch up to `0002`; the app was reading from a
branch still at `0001`. The Neon SQL editor "looks the same" because both
projects had `neondb` databases with the same default naming.

**Diagnostic that found it:** a one-line `log.info` at boot in
`scripts/db.py` printing `DATABASE_URL host='...'`. That line is now
permanent — every boot logs which Neon endpoint the app actually connects
to. If something feels off, check the boot log first.

**Lesson:** never assume two env vars match just because the Neon dashboard
"looks" consistent. The compute endpoint prefix (`ep-XXXXX`) is the only
reliable identity. Compare byte-for-byte.

### Symptom: pool init fails at boot — `connection refused` / `name resolution failure`

Neon endpoint is down or wrong. Check:

1. `diagnose_db.py` from your laptop with the same DSN — does it work
   directly? If yes, it's a Render → Neon network issue (rare).
2. Neon dashboard → branch status → is the compute endpoint "Active"?
   Free-tier compute endpoints autosuspend after idle; the cold-start
   typically resolves itself but can exceed `command_timeout=10s`.
3. Was the password rotated? Compare Render env value against Neon's
   "Reset password" timestamps.

### Symptom: `verify_schema` raises `alembic_version table missing`

The DB was never migrated. From a shell pointing at the DIRECT endpoint:

```powershell
$env:ALEMBIC_DATABASE_URL = "<direct DSN>"
alembic upgrade head
```

### Symptom: lots of `[audit] FAILED` CRITICAL log lines

The audit log writer caught an exception. Per design contract, `audit.emit`
never raises — failures land in stdout, never break checkout. Common causes:

- DB connectivity blip → expect spike to clear once Neon recovers
- Schema drift (a renamed column in `audit_events`) → check the column
  list against `0001_initial_schema.up.sql`
- Malformed payload (rare — `data` should be plain JSON-serializable)

### Symptom: orphan Stripe holds reported by Stripe Dashboard

Check `payment_holds` for matching `hold_id`. Then check the joined `sagas`
row for `hold_disposition` (B55):

- `cancel_safe` — reconciler will auto-cancel as safety net.
- `operator_decides` — runbook said "you decide cancel vs capture";
  reconciler will skip. Operator action required.
- `NULL` — pre-B55 row OR uncovered case; reconciler skips conservatively.

The reconciler logs decisions at INFO with `[reconcile]` prefix in Render
logs. Search for `[reconcile] <hold_id>` for that hold's history.

### Symptom: saga stuck at `stripe_held` after restart

`saga_resume.py` runs at every boot and should route this to FAILED or
MANUAL_REVIEW. If you see a `stripe_held` row >1 hour past
`last_transition_at`, either:

1. The reconciler hasn't ticked yet (it runs every `RECONCILE_INTERVAL_SECONDS`,
   default 300s). Wait one tick.
2. Saga-resume crashed for THIS saga on the previous boot (search Render
   logs for `[resume] saga <checkout_id> ... recovery FAILED`). Manual
   action needed — see runbook in CHECKOUT_AUDIT.md.

### Symptom: connection pool exhausted (`PoolExhausted` errors)

`max_size=10` — should be ample at single-worker. Causes:

- Long-running query blocking a connection (uncommon — `command_timeout=10s`
  caps each query).
- Connection leak (a code path forgetting to release a connection back to
  the pool — should never happen with `async with pool.acquire()` pattern).
- `MAX_WORKERS` bumped without raising `max_size`. If you increase
  `MAX_WORKERS`, scale `max_size` proportionally (rough rule: ~3–5 per
  worker for headroom).

Check active connections from Neon dashboard → Monitoring.

---

## 6. The `diagnose_db.py` tool

`scripts/diagnose_db.py` — connects to whatever DSN is in `$env:DIAG_DSN`
and prints database / user / schema / alembic_version. Bypasses the pooler
guards in alembic's `env.py`, so it works with either pooler or direct
DSNs.

**Use it when:**

- Boot says one schema version, you think it should be another.
- About to paste a DSN into Render and want to sanity-check first.
- Verifying the post-cutover state matches expectation.
- Confirming `DATABASE_URL` and `DATABASE_URL_DIRECT` actually point at the
  same DB.

```powershell
$env:DIAG_DSN = "<paste full DSN — with password — pooler or direct, either works>"
.\.venv\Scripts\python.exe scripts\diagnose_db.py
```

Output:

```
database : neondb
user     : neondb_owner
schema   : public
version  : 0002
```

If `version` is `None`, the `alembic_version` table is missing (migration
never ran on this DB). If the script raises `asyncpg.PostgresConnectionError`,
the DSN is wrong (typo, wrong password, suspended endpoint, missing
`sslmode=require`).

---

## 7. Emergency rollback

### Code rollback (DB stays on postgres)

Render dashboard → service → Deploys → pick a known-good commit → "Redeploy".

Pre-deploy hook still runs `alembic upgrade head`, which is a no-op if the
new code's `_EXPECTED_SCHEMA_VERSION` matches the DB (the typical rollback
case). If the rollback target expects an earlier schema, the boot guard
will refuse — at which point downgrade the DB or pick a different rollback
target.

### Backend rollback (postgres → JSON) — ONLY if structurally broken

`DB_BACKEND=postgres` was flipped on Render 2026-05-22. The JSON code path
still exists (Phase F Step 7 deletion has not yet happened — see
`PRE_RELEASE_PAYMENT_CHECKLIST.md §9.4`). To roll back:

1. Render → Environment → `DB_BACKEND=json` → Save. Render restarts.
2. The dispatchers (`jobs_store_dispatch`, `checkout_store_dispatch`)
   re-check the env per call, so no redeploy needed.
3. **Caveat:** rows written during the postgres window won't be visible to
   the JSON backend. For pre-launch (no real customer money), acceptable.
   Once live, prefer fixing forward.

After Phase F Step 7 cleanup ships, this rollback path disappears. Plan
accordingly.

### Restoring from Neon point-in-time

Neon's branching is also your backup. Need state from 30 minutes ago?

1. Neon dashboard → Branches → "Create branch" → "Point in time" → pick
   timestamp.
2. The new branch has its own pooler + direct endpoints.
3. Use `diagnose_db.py` to confirm.
4. Either repoint Render env vars at the new branch (full restore) OR
   query the branch with psql to extract specific rows and copy them
   back into main.

Free tier retains ~24 hours of history; Launch tier extends to 7 days.

---

## 8. Neon-specific quirks

- **Hostname conventions.** `ep-XXXX` = compute endpoint identifier (unique
  per Neon project + branch). `ep-XXXX-pooler` = PgBouncer in front of it.
  Two env vars must share `ep-XXXX`; only `-pooler` differs.
- **`channel_binding=require`.** Neon's default DSN has this. asyncpg
  handles it transparently. Don't strip it just because alembic logs are
  noisy.
- **Free-tier autosuspend** sleeps the compute endpoint after ~5 minutes
  idle. First request after sleep cold-starts in 3–10 seconds — close to
  the `command_timeout=10s` boundary. Production should be on Launch tier
  ($19/mo) which disables autosuspend. As of 2026-05-22 this upgrade (U5)
  is still **pending** — first request after idle may lag.
- **Branches are copy-on-write.** Free; creating a `dev` branch from `main`
  takes seconds. Use for migration smoke tests before applying to `main`.
  Don't confuse with git branches (see CLAUDE.md "Branch terminology").
- **The pooler ≠ the direct endpoint** for DDL. Alembic against the pooler
  fails on multi-statement transactions and prepared statements. `env.py`
  refuses pooler DSNs with an actionable error.

---

## 9. Where each piece of state lives — quick reference

| Concern                          | Lives in                                              |
|----------------------------------|-------------------------------------------------------|
| Mosaic job lifecycle             | `jobs` table                                          |
| Customer's quote (allocation)    | `checkouts.allocation` JSONB                          |
| Active saga state                | `sagas` table (one active per job)                    |
| Saga reason text for operator    | `sagas.manual_review_reason`                          |
| Customer-facing error string     | `sagas.customer_message`                              |
| Stripe hold reconciliation index | `payment_holds` table                                 |
| Hold disposition for MANUAL_REVIEW| `sagas.hold_disposition` (B55, migration 0002)       |
| Every gate rejection / capture / cancel | `audit_events` table (append-only)               |
| Mosaic artifact (zip, PDF)       | `outputs/{job_id}/artifact.zip` (filesystem)          |
| `order_list.json` for /quote     | `outputs/{job_id}/order_list.json` (filesystem)       |
| Worker progress %                | `inputs/{job_id}.progress` (file) → mirrored to `jobs.progress_pct` |
| Failure manifest (forensics)     | `outputs/{job_id}/manifest_failed.json` (filesystem)  |
| In-process quote cache           | `scripts/checkout/cache.py` (memory, 10min TTL)       |
| `app.state.futures` / `intake`   | Process memory; lost on restart (regenerated)         |

When in doubt about whether something is in the DB or on disk: persistent
operator-visible state goes in the DB; large blobs and worker-private
ephemera stay on disk.

---

## 10. Where the code lives — quick reference

| Module                                              | Role                                                              |
|-----------------------------------------------------|-------------------------------------------------------------------|
| `scripts/db.py`                                     | asyncpg pool + boot guards (`verify_schema`, version check)       |
| `scripts/jobs_store_pg.py`                          | `jobs` table CRUD + atomic dequeue                                |
| `scripts/jobs_store_dispatch.py`                    | Per-call routing between PG and JSON backends (transitional)      |
| `scripts/checkout/checkout_store_pg.py`             | `checkouts` + `sagas` CRUD with advisory locks                    |
| `scripts/checkout/payment_holds_store.py`           | `payment_holds` writes (reconciliation index)                     |
| `scripts/checkout/audit.py`                         | `audit_events` writer — never raises                              |
| `scripts/checkout/saga_resume.py`                   | Boot-time saga recovery                                           |
| `scripts/checkout/reconcile.py`                     | Periodic orphan-hold scanner                                      |
| `scripts/migrations/sql/`                           | Raw `.up.sql` / `.down.sql` files (canonical schema)              |
| `scripts/migrations/versions/`                      | Alembic wrappers (`revision` / `down_revision`)                   |
| `scripts/migrations/env.py`                         | Alembic config — refuses pooler DSNs                              |
| `scripts/diagnose_db.py`                            | Standalone DSN sanity check (see §6)                              |

For runtime invariants (the L0–L6 defense-in-depth gate, the asyncpg pool
contract, the saga state machine), see CLAUDE.md "Antipatterns to avoid".
