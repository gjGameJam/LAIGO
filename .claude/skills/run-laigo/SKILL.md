---
name: run-laigo
description: Build, launch, and drive the LAIGO API locally. Use when asked to run, start, boot, serve, smoke-test, or screenshot LAIGO, hit /generate or /pay, or confirm a code change works against the real running FastAPI server (not just unit tests).
---

# Run LAIGO

LAIGO is a **FastAPI server** that turns a photo into a LEGO mosaic build pack
(color-quantized layout `order_list.json`, a step-by-step `instructions.pdf`,
and a 3D `preview.json`), sold pay-what-you-want. There is no GUI in this repo
— the product surface is the HTTP API. You drive it by launching `uvicorn` in
the background and hitting it with the committed Python driver
(`.claude/skills/run-laigo/driver.py`, uses `httpx`) or plain `curl`.

**All paths below are relative to the repo root**
(`C:\Users\Grant Benson\OneDrive\Desktop\LAIGO`), which is `<unit>/`. Commands are
PowerShell unless noted. The driver and these commands were all run and verified
on this machine (Windows 11, venv Python **3.11.9**).

## Prerequisites

The `.venv` already exists and has every dependency (`fastapi`, `uvicorn`,
`httpx`, `numpy`, `pillow`, `scikit-image`, `opencv-python-headless`,
`mediapipe`, `reportlab`, `stripe`, …). Confirm it:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import httpx, fastapi, uvicorn; print('deps ok')"
```

To rebuild from scratch instead: `py -3.12 -m venv .venv` then
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt` (3.12 preferred —
see Gotchas; 3.11 works). Behind a corporate TLS proxy add
`--trusted-host pypi.org --trusted-host files.pythonhosted.org` to the pip line.

No database, Neon, or marketplace creds are needed: `.env` ships
`DB_BACKEND=json` and `CHECKOUT_ENABLED=false`, so the DB pool / saga / reconcile
paths all no-op at boot. `.env.secrets` (gitignored) holds a Stripe **test** key
on this machine — present, the tip/paid endpoints work; absent, only those
return 503 (the whole mosaic flow + free `$0` pay still work). It also holds a
**real `RESEND_API_KEY`** — see the email gotcha below before driving `/pay`.

## Run (agent path) — background server + driver

LAIGO **must** be started from the repo root with the package-qualified path
`scripts.Main:app` (its modules use relative imports; `cd scripts; uvicorn
Main:app` fails). Boot takes ~10–20 s — importing `picToMosiac` pulls in
mediapipe/cv2/numpy.

**1. Pick a free port** (8000 is the `.env` default but is often already taken
by a leftover instance — this repo's `/run` launches the server and leaves it
up):

```powershell
foreach ($p in 8000,8011,8123,8765) { $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if ($c) { "$p : IN USE" } else { "$p : FREE" } }
```

**2. Launch in the background**, redirecting logs to files (pick a FREE port —
8011 used here):

```powershell
$root = "C:\Users\Grant Benson\OneDrive\Desktop\LAIGO"
$out = Join-Path $env:TEMP "laigo_boot_out.log"; $err = Join-Path $env:TEMP "laigo_boot_err.log"
Remove-Item $out,$err -ErrorAction SilentlyContinue
Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","scripts.Main:app","--host","127.0.0.1","--port","8011" `
  -WorkingDirectory $root -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
```

**3. Wait for boot, then confirm health** (curl retries until it answers — no
manual sleep):

```powershell
curl.exe -s --retry 20 --retry-delay 1 --retry-connrefused --retry-all-errors -w "`n/health HTTP %{http_code}`n" http://127.0.0.1:8011/health
```

Expected: `{"status":"running"}` and `/health HTTP 200`. The app banner appears
in the stdout log (`$env:TEMP\laigo_boot_out.log`): `ProcessPoolExecutor
started: 1 worker(s), respawn=off` then `LAIGO API ready`.

**4. Drive the full customer flow** with the committed driver. It does NOT
launch the server — it drives the one you just started:

```powershell
.\.venv\Scripts\python.exe .claude\skills\run-laigo\driver.py --base-url http://127.0.0.1:8011 --width 5 --type 3d
```

This uploads `images/stella1.jpg` to `POST /generate`, polls `GET /jobs/{id}`
0→100 %, downloads the artifact zip, fetches the 3D preview, and exercises
`POST /jobs/{id}/pay` ($0 free path, with the now-required `email` field —
the driver sends `driver-smoke@example.com`) + `POST /donate`. Last line on
success:

```
[PASS] core flow green: health -> generate -> complete -> download -> pay
```

Flags: `--type 2d` (faster — skips mediapipe segmentation), `--width N`
(blocks, 1–40; keep small, it's CPU-bound), `--base-url`, `--out`
(artifact zip; default system temp), `--poll-timeout`. A 5-block 3D job took
~20 s; the artifact (~7.7 MB) contains `Instructions/instructions.pdf`,
`OrderLists/order_list*.json`, `preview.json`, `manifest.json`.

**5. Stop the server** when done (find the PID on the port, kill it):

```powershell
$c = Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; "stopped" } else { "nothing on 8011" }
```

### One-off curl probes (no driver)

```powershell
curl.exe -s http://127.0.0.1:8011/queue
curl.exe -s http://127.0.0.1:8011/checkout/gate
curl.exe -s -F "file=@images/stella1.jpg" -F "mosaic_block_width=5" -F "mosaic_type=3d" -F "background_color_percent=50" -F "to_frame=true" http://127.0.0.1:8011/generate
```

## Run (human path)

For interactive poking, run in the foreground with autoreload and open Swagger:

```powershell
.\.venv\Scripts\activate.ps1
uvicorn scripts.Main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>. Ctrl-C to stop. (Headless agents
should prefer the background + driver path above — `--reload` blocks the shell
and spawns a reloader child that complicates PID cleanup.)

## Direct invocation — tests

Test suites run as bare modules from the repo root (no server, no DB, no
network for these):

```powershell
.\.venv\Scripts\python.exe -m scripts.test_pay_router
.\.venv\Scripts\python.exe -m scripts.test_donate_router
.\.venv\Scripts\python.exe -m scripts.test_optimizer
```

There is **no working standalone CLI** for the mosaic pipeline. CLAUDE.md /
README still describe `cd scripts; python picToMosiac.py 5 3d 50 True`, but that
is stale: the `__main__` block was removed (D-013) and every module now uses
package-relative imports, so running the file as a bare script dies with
`attempted relative import with no known parent package`. Drive the pipeline
through the API (the driver above) or the test suite — those are the only live
entry points.

## Gotchas

- **Run from the repo root, package-qualified.** `uvicorn scripts.Main:app`
  from root works; `cd scripts; uvicorn Main:app` dies with `attempted relative
  import with no known parent package`.
- **Port 8000 is usually already taken.** This repo's `/run` convention is
  "launch and leave it up," so prior instances linger. Scan for a free port
  (step 1) before launching, or you get `[Errno 10048] only one usage of each
  socket address` in the boot log while a *different* server happily answers
  `/health` on that port — confusing. Verified here: 8000 and 8001 were both
  occupied by leftovers.
- **Python 3.11 logs `respawn=off` at boot — harmless locally.**
  `max_tasks_per_child` needs 3.12+; on 3.11 the executor omits it (worker
  isn't respawned per job, so no per-job RSS reclaim) and logs a WARNING. The
  server runs fine. Render prod pins 3.12 via `runtime.txt`.
- **`/checkout/gate` always returns 200 with `mode=disabled`.** That's correct
  — `CHECKOUT_ENABLED=false` and the shelved saga is unmounted. Do NOT point a
  healthcheck at it; use `/health`.
- **The JSON jobs store is in-memory.** Job *metadata* is lost on restart, but
  completed *artifacts* persist on disk under `outputs/{job_id}/`. `POST /pay`
  and `GET /download` check `outputs/{job_id}/artifact.zip` on disk, so they
  still work after a restart even though `GET /jobs/{id}` returns 404.
- **`/generate` validates before queuing**: `mosaic_block_width` must be 1–40
  (422 otherwise), `mosaic_type` must be `2d`/`3d`, `background_color_percent`
  0–100. A truncated/invalid image is rejected 400 (full pixel decode, not just
  a header sniff).
- **Stripe key is real (test mode).** `.env.secrets` has an `sk_test_…` key, so
  `POST /donate` returns 200 with a `client_secret` and paid `/pay` charges the
  test card. The gate still reports DISABLED (that gates the *shelved* saga, not
  these endpoints). Delete/rename `.env.secrets` to simulate the no-Stripe case
  → tip/paid endpoints 503, free `$0` pay + mosaic flow unaffected.
- **`/pay` requires `email` and REALLY SENDS EMAIL on this machine.**
  `POST /jobs/{id}/pay` needs `{"amount_cents": N, "email": "..."}` (422
  without it — including $0), and after any completed checkout the server
  emails the build pack via Resend (`scripts/emailer.py`,
  `docs/EMAIL_DELIVERY.md`). `.env.secrets` here holds a REAL `RESEND_API_KEY`
  in dev mode: sends to the account owner's own address (grantjbenson@icloud.com)
  actually deliver to his inbox; sends to any other address are accepted by
  the API route (200) but rejected by Resend — recorded as `"failed"` in
  `outputs/{id}/email.json`, which is fine for smoke tests. The driver uses
  `driver-smoke@example.com` for exactly this reason. Duplicate sends are
  suppressed per job via the `email.json` sentinel (`"duplicate"` in the log);
  delete that file to force a resend. To silence email entirely, set
  `EMAIL_ENABLED=false` in the process env before launching.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `attempted relative import with no known parent package` | You ran from `scripts/`. Run `uvicorn scripts.Main:app` from the repo root. |
| Boot log shows `[Errno 10048] ... bind on address` | Port taken by a leftover instance. Pick a free port (step 1). A server answering `/health` on that port is the *old* one, not yours. |
| Driver prints `Is the server up?` / connection refused | Server not booted yet (mediapipe import is slow) or wrong `--base-url` port. Re-run the health curl (step 3) first. |
| `POST /generate` → 422 | Bad params — `mosaic_block_width` out of 1–40, or `mosaic_type` not `2d`/`3d`. |
| `POST /pay` → 422 with `loc: ["body","email"]` | The `email` field is required for all amounts (incl. $0) since 2026-07-05. Add `"email": "driver-smoke@example.com"` to the body. |
| Paid $0 but no email arrived | Expected unless the recipient is the Resend account owner (dev mode) — check `outputs/{id}/email.json` for `sent`/`failed`/`skipped` and the server log for `email.*` lines. |
| `POST /donate` / paid `/pay` → 503 `PAYMENTS_UNAVAILABLE` | No Stripe key. Add `STRIPE_SECRET_KEY=sk_test_…` to `.env.secrets`. Free `$0` pay and the mosaic flow don't need it. |
| `pip install` → `SSLCertVerificationError` | Corporate TLS proxy. Add `--trusted-host pypi.org --trusted-host files.pythonhosted.org` to the pip command. |
| Job stuck at low % then fails ~30 min later | Width too large for the input; keep `--width` small. The 30-min timeout watchdog force-fails runaway jobs. |
