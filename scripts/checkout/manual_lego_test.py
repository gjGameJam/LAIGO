"""
Manual LEGO.com checkout verification harness — incremental, throwaway.

Purpose: drive the cart -> checkout flow against live LEGO.com with
`headless=False` so a human can watch and tell Claude which selectors are
wrong. Once each step is verified, the working selectors get ported back into
`clients/lego_client.py::_run_checkout()`.

Login is NOT the focus anymore — production login is handled by
`scripts/seed_lego_session.py` (storage_state cached in Neon). For verification
we cache a logged-in `storage_state` to a LOCAL file under outputs/ (gitignored)
so you only complete LEGO's email-2FA login ONCE, then every subsequent harness
run reloads the cached state and jumps straight to the unverified cart steps.

Run from project root OR from this directory:
    python scripts\checkout\manual_lego_test.py
    cd scripts\checkout && python manual_lego_test.py

Flags:
    --relogin    Ignore the cached state file and force a fresh manual login
                 (use when the cached session has expired).

Current scope (what THIS harness verifies):
  - Step 2: navigate to Pick-a-Brick, confirm logged in.
  - Step 3: locate the JSON / bulk-upload control and confirm the cart
            populates. <-- WE ARE HERE. Harness pauses for you to grab the
            real upload selector via the Playwright Inspector.

TODO (do not implement until Step 3 is verified):
  - Step 4: click "Checkout", wait for /checkout/** URL.
  - Step 5: verify the payment screen renders (saved card on account) — STOP.

CRITICAL — DO NOT WIRE WITHOUT EXPLICIT GO-AHEAD:
  Step 6 (place-order click) charges a REAL order to LAIGO's saved card on
  lego.com. Per project-payment-architecture decision 2026-05-31, LAIGO is
  merchant of record on its own saved card — but during verification we are
  deliberately NOT placing real orders. The place-order click stays unwired
  until the end-to-end test strategy is finalized (sandbox? throwaway low-$
  orders cancelled immediately via lego.com/profile/orders?).
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEBUG_DIR = PROJECT_ROOT / "outputs" / "lego_debug"
# Local, gitignored (outputs/ is in .gitignore) cache of the logged-in browser
# state. NOT the production path — production loads storage_state from Neon via
# lego_session_store. This file only exists so verification doesn't re-trigger
# LEGO's email 2FA on every iteration.
STATE_FILE = DEBUG_DIR / "_verify_storage_state.json"

LEGO_BASE = "https://www.lego.com/en-us"
LOGIN_URL = f"{LEGO_BASE}/profile/login"
PROFILE_URL = f"{LEGO_BASE}/profile"
PAB_URL = f"{LEGO_BASE}/pick-and-build/pick-a-brick"
TIMEOUT_MS = 90_000
# LEGO defers popup JS until after networkidle, so wait per-locator for these.
POPUP_APPEAR_TIMEOUT_MS = 10_000

# Tiny sample cart for upload verification (2 distinct elements). Element IDs
# are real LEGO Pick-a-Brick parts. Keep quantities low — once Step 3 works we
# want a cart that's cheap to clear out manually.
SAMPLE_ITEMS = [
    {"elementId": "302421", "quantity": 4},
    {"elementId": "300126", "quantity": 4},
]

# Popups LEGO.com shows on a fresh session. (label, selector). Verified
# 2026-06-03. Missing popups are non-fatal (cookies may remember dismissal).
POPUPS: list[tuple[str, str]] = [
    ("age gate (grown-up)", "[data-test='age-gate-grown-up-cta']"),
    ("cookie banner (accept all)", "[data-test='cookie-accept-all']"),
]


async def dismiss_popups(page) -> None:
    """Click through known LEGO.com popups, each with its own appear-timeout."""
    for label, sel in POPUPS:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=POPUP_APPEAR_TIMEOUT_MS)
        except Exception:
            print(f"[popup] not present within {POPUP_APPEAR_TIMEOUT_MS}ms (skipped): {label}")
            continue
        try:
            await loc.click(timeout=5_000)
            print(f"[popup] dismissed: {label}")
            await page.wait_for_timeout(500)
        except Exception as exc:
            print(f"[popup] {label} click failed: {type(exc).__name__}: {exc}")


async def manual_login(context, page) -> bool:
    """Drive a one-time manual login and save storage_state to STATE_FILE.

    The human completes the whole login in the headed window — including
    Google SSO and/or email 2FA — exactly as in seed_lego_session.py. We only
    auto-navigate to the login landing page and verify via a /profile probe.
    Returns True if the session stuck and was saved.
    """
    print(f"[login] navigating to {LOGIN_URL}")
    await page.goto(LOGIN_URL, timeout=TIMEOUT_MS)
    await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    await dismiss_popups(page)

    print()
    print("=" * 64)
    print(" Complete login in the browser window:")
    print("   1. Click 'Sign In', then sign in (Google SSO recommended —")
    print("      it bypasses LEGO's email 2FA).")
    print("   2. Complete any verification that appears.")
    print("   3. Wait until you see LEGO as a logged-in user.")
    print(" Then return here and press ENTER.")
    print("=" * 64)
    print()
    await asyncio.to_thread(input, "Press ENTER when logged in: ")

    print("[login] verifying via /profile probe...")
    await page.goto(PROFILE_URL, timeout=TIMEOUT_MS)
    await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    if "/login" in page.url:
        print(f"[login] ERROR: /profile bounced to {page.url!r}. Login did not stick.",
              file=sys.stderr)
        return False

    await context.storage_state(path=str(STATE_FILE))
    print(f"[login] session valid. storage_state saved -> {STATE_FILE}")
    return True


async def main() -> None:
    relogin = "--relogin" in sys.argv
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    have_state = STATE_FILE.exists() and not relogin
    print(f"[harness] cached state: {'FOUND' if have_state else 'none'}"
          f"{' (--relogin: ignoring)' if relogin and STATE_FILE.exists() else ''}")
    print(f"[harness] screenshots -> {DEBUG_DIR}")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed.", file=sys.stderr)
        print("  pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            storage_state=str(STATE_FILE) if have_state else None,
        )
        page = await context.new_page()

        try:
            # ── Establish a logged-in session ────────────────────────────────
            if not have_state:
                if not await manual_login(context, page):
                    return

            # ── Step 2: navigate to Pick-a-Brick, confirm logged in ──────────
            print(f"[step2] navigating to {PAB_URL}")
            await page.goto(PAB_URL, timeout=TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            await dismiss_popups(page)
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            await page.screenshot(path=str(DEBUG_DIR / "step2_00_pab_loaded.png"))
            print(f"[step2] PaB loaded. URL: {page.url}")

            if "/login" in page.url:
                print("[step2] ERROR: redirected to login — cached session expired.",
                      file=sys.stderr)
                print("        Re-run with --relogin to refresh the cached state.",
                      file=sys.stderr)
                return

            # ── Step 3: locate the JSON / bulk-upload control ────────────────
            # The production scaffold assumes `input[type='file']` + a
            # `[data-test='add-to-cart-button']` — both unverified guesses.
            # Report what's actually present so we can fix _run_checkout Step C.
            print()
            print("=" * 64)
            print(" STEP 3 — find the JSON / bulk-upload control on Pick-a-Brick.")
            print(" In the browser:")
            print("   1. Open the upload / bulk-import / 'upload a list' UI.")
            print("   2. Open DevTools (F12) -> inspect the file <input> (or the")
            print("      button that opens the file picker).")
            print("   3. Note a stable selector (data-test=..., id=..., aria-label).")
            print("   4. Paste it back to Claude.")
            print()
            file_inputs = await page.locator("input[type='file']").count()
            print(f" Diagnostic: input[type='file'] elements on page right now: {file_inputs}")
            print("=" * 64)
            print()
            await page.screenshot(path=str(DEBUG_DIR / "step3_00_before_upload.png"))

            print("[step3] holding browser open 180s for inspection. Ctrl-C to exit sooner.")
            await asyncio.sleep(180)

        except Exception as exc:
            print(f"[harness] UNHANDLED: {type(exc).__name__}: {exc}")
            try:
                await page.screenshot(path=str(DEBUG_DIR / "ERROR_state.png"))
            except Exception:
                pass
        finally:
            await browser.close()
            print(f"[harness] done. screenshots in {DEBUG_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
