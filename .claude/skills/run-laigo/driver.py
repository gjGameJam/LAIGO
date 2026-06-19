#!/usr/bin/env python
"""
LAIGO smoke driver — drives a *running* LAIGO API end-to-end.

This does NOT launch the server. Start uvicorn first (see SKILL.md), then point
this at it. It exercises the real customer flow plus the pay-what-you-want
endpoints, and prints a PASS/FAIL line per step.

    .venv\\Scripts\\python.exe .claude\\skills\\run-laigo\\driver.py
    .venv\\Scripts\\python.exe .claude\\skills\\run-laigo\\driver.py --base-url http://127.0.0.1:8001

Flow:
  GET  /health                         must be {"status":"running"}
  GET  /                               banner
  GET  /checkout/gate                  gate mode (DISABLED is expected/fine)
  GET  /queue                          queue depth + worker headroom
  POST /generate         (multipart)   uploads the test image, returns job_id
  GET  /jobs/{id}        (poll)        until status == complete | failed
  GET  /jobs/{id}/download             saves artifact.zip, lists its contents
  GET  /jobs/{id}/preview              3D preview JSON (3d mosaics only)
  POST /jobs/{id}/pay   {amount:0}     pay-what-you-want, free path -> "free"
  POST /donate          {amount:500}   tip intent (503 if Stripe unconfigured)

Exit code: 0 if the core flow (health -> generate -> complete -> download)
passed, 1 otherwise. The Stripe-dependent steps are reported but never fail
the run when no key is configured (that's an expected local-dev state).

Requires httpx (already in requirements.txt / the project venv).
"""
from __future__ import annotations

import argparse
import io
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

# repo root = three levels up from .claude/skills/run-laigo/driver.py
ROOT = Path(__file__).resolve().parents[3]

OK = "PASS"
BAD = "FAIL"
INFO = "INFO"


def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive a running LAIGO API.")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000",
                    help="Running server base URL (default: http://127.0.0.1:8000).")
    ap.add_argument("--image", default=str(ROOT / "images" / "stella1.jpg"),
                    help="Image to upload (default: images/stella1.jpg).")
    ap.add_argument("--width", type=int, default=5,
                    help="mosaic_block_width in blocks (default: 5 — keep small, it's CPU-bound).")
    ap.add_argument("--type", default="3d", choices=["2d", "3d"],
                    help="Mosaic type (default: 3d).")
    ap.add_argument("--bg", type=float, default=50.0,
                    help="background_color_percent (default: 50).")
    ap.add_argument("--frame", default="true", choices=["true", "false"],
                    help="to_frame (default: true).")
    ap.add_argument("--poll-timeout", type=int, default=300,
                    help="Seconds to wait for the job to finish (default: 300).")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "laigo_smoke_artifact.zip"),
                    help="Where to save the downloaded artifact.zip (default: system temp).")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    img = Path(args.image)
    failures = 0

    log(INFO, f"base_url    = {base}")
    log(INFO, f"image       = {img}")
    log(INFO, f"width/type  = {args.width} blocks / {args.type}  (bg={args.bg}, frame={args.frame})")

    if not img.exists():
        log(BAD, f"image not found: {img}")
        return 1

    with httpx.Client(timeout=30.0) as c:
        # 1. health -------------------------------------------------------------
        try:
            r = c.get(f"{base}/health")
            ok = r.status_code == 200 and r.json().get("status") == "running"
            log(OK if ok else BAD, f"GET /health -> {r.status_code} {r.text.strip()}")
            failures += 0 if ok else 1
        except Exception as e:
            log(BAD, f"GET /health raised {type(e).__name__}: {e}")
            log(BAD, "Is the server up? Start uvicorn first (see SKILL.md).")
            return 1

        # 2. root banner --------------------------------------------------------
        r = c.get(f"{base}/")
        log(INFO, f"GET / -> {r.status_code} {r.text.strip()}")

        # 3. checkout gate (always 200; DISABLED locally is expected) -----------
        r = c.get(f"{base}/checkout/gate")
        try:
            g = r.json()
            log(INFO, f"GET /checkout/gate -> mode={g.get('mode')} is_open={g.get('is_open')} "
                      f"provider={g.get('payment_provider')}")
        except Exception:
            log(INFO, f"GET /checkout/gate -> {r.status_code} {r.text[:120]}")

        # 4. queue --------------------------------------------------------------
        r = c.get(f"{base}/queue")
        log(INFO, f"GET /queue -> {r.status_code} {r.text.strip()}")

        # 5. generate -----------------------------------------------------------
        files = {"file": (img.name, img.read_bytes(), "image/jpeg")}
        data = {
            "mosaic_block_width": str(args.width),
            "mosaic_type": args.type,
            "background_color_percent": str(args.bg),
            "to_frame": args.frame,
        }
        r = c.post(f"{base}/generate", files=files, data=data)
        if r.status_code != 200:
            log(BAD, f"POST /generate -> {r.status_code} {r.text[:300]}")
            return 1
        job_id = r.json()["job_id"]
        log(OK, f"POST /generate -> {r.status_code} job_id={job_id}")

        # 6. poll ---------------------------------------------------------------
        deadline = time.time() + args.poll_timeout
        status = "queued"
        last_pct = -1
        while time.time() < deadline:
            r = c.get(f"{base}/jobs/{job_id}")
            if r.status_code != 200:
                log(BAD, f"GET /jobs/{job_id} -> {r.status_code} {r.text[:200]}")
                return 1
            body = r.json()
            status = body.get("status")
            pct = body.get("progress", 0)
            if pct != last_pct or status not in ("queued", "running"):
                log(INFO, f"  job {status:9s} progress={pct}%")
                last_pct = pct
            if status in ("complete", "failed"):
                break
            time.sleep(2)

        if status != "complete":
            log(BAD, f"job did not complete (final status={status})")
            if status == "failed":
                log(BAD, f"  error: {r.json().get('error')}")
            return 1
        log(OK, f"job complete")

        # 7. download -----------------------------------------------------------
        r = c.get(f"{base}/jobs/{job_id}/download")
        if r.status_code != 200:
            log(BAD, f"GET /download -> {r.status_code} {r.text[:200]}")
            failures += 1
        else:
            out = Path(args.out)
            out.write_bytes(r.content)
            try:
                names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
            except Exception as e:
                names = [f"<unreadable zip: {e}>"]
            log(OK, f"GET /download -> {len(r.content)} bytes saved to {out}")
            log(INFO, f"  artifact contents: {names}")

        # 8. preview (3d only) --------------------------------------------------
        if args.type == "3d":
            r = c.get(f"{base}/jobs/{job_id}/preview")
            if r.status_code == 200:
                p = r.json()
                keys = list(p.keys()) if isinstance(p, dict) else type(p).__name__
                log(OK, f"GET /preview -> {len(r.content)} bytes, keys={keys}")
            else:
                log(INFO, f"GET /preview -> {r.status_code} {r.text[:160]}")

        # 9. pay-what-you-want, free path --------------------------------------
        r = c.post(f"{base}/jobs/{job_id}/pay", json={"amount_cents": 0})
        if r.status_code == 200 and r.json().get("status") == "free":
            log(OK, f"POST /pay (amount=0) -> free download recorded")
        else:
            log(BAD, f"POST /pay (amount=0) -> {r.status_code} {r.text[:200]}")
            failures += 1

        # 10. donate (needs a Stripe key; 503 locally is expected) --------------
        r = c.post(f"{base}/donate", json={"amount_cents": 500})
        if r.status_code == 200:
            log(OK, f"POST /donate -> 200 client_secret returned (Stripe configured)")
        elif r.status_code == 503:
            log(INFO, f"POST /donate -> 503 PAYMENTS_UNAVAILABLE (no Stripe key — expected locally)")
        else:
            log(INFO, f"POST /donate -> {r.status_code} {r.text[:200]}")

    print("-" * 60, flush=True)
    if failures == 0:
        log(OK, "core flow green: health -> generate -> complete -> download -> pay")
        return 0
    log(BAD, f"{failures} step(s) failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
