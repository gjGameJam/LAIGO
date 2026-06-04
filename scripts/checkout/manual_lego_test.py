"""
Manual LEGO.com checkout verification harness — incremental, throwaway.

Purpose: drive `clients/lego_client.py::_run_checkout()` step-by-step against
live LEGO.com with `headless=False` so a human can watch and tell Claude which
selectors are wrong. Once each step is verified, the working selectors get
ported back into `_run_checkout()` proper.

Run from project root OR from this directory:
    python scripts\checkout\manual_lego_test.py
    cd scripts\checkout && python manual_lego_test.py

Current scope: STEP 1 ONLY (login).

TODOs for future steps (do not implement until Step 1 is verified):
  - Step 2: navigate to Pick-a-Brick (_PAB_URL)
  - Step 3: upload cart JSON ([{"elementId": "302421", "quantity": 10}])
  - Step 4: click checkout button, wait for /checkout/** URL
  - Step 5: verify payment form is reached and visible — STOP HERE.

CRITICAL TODO — DO NOT IMPLEMENT WITHOUT EXPLICIT GO-AHEAD:
  Step 6 (clicking [data-test='place-order-button']) places a REAL order
  charged to LAIGO's saved card on lego.com. Per project-payment-architecture
  decision 2026-05-31, LAIGO is merchant of record and uses its own saved
  card — but during verification we are deliberately NOT placing real orders.
  Only wire the place-order click after end-to-end testing strategy
  is finalized (sandbox account? throwaway $5 test orders to cancel
  immediately via lego.com/profile/orders? — needs explicit decision).
"""

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = PROJECT_ROOT / ".env.secrets"
DEBUG_DIR = PROJECT_ROOT / "outputs" / "lego_debug"

# Start at Pick-a-Brick because that's where Step 2 will pick up after login.
# Login itself is a redirect chain to identity.lego.com and back; the starting
# URL just decides where we land after auth.
START_URL = "https://www.lego.com/en-us/pick-and-build/pick-a-brick"
TIMEOUT_MS = 90_000
# How long to wait for each popup to render before assuming it's not coming.
# Generous because LEGO defers popup JS until after networkidle fires.
POPUP_APPEAR_TIMEOUT_MS = 10_000

# Multi-step login selectors (verified 2026-06-03 per user-supplied HTML).
# NOTE: lego.com uses `data-test`; identity.lego.com uses `data-testid`.
# Same attribute family, different spelling. Do not consolidate.
HEADER_SIGNIN_BTN_SEL = "[data-test='header-account-cta']"      # lego.com header
DIALOG_SIGNIN_LINK_SEL = "[data-test='legoid-login-button']"    # opens identity.lego.com
USERNAME_SEL = "[data-testid='usernameField']"                  # identity.lego.com step 1
CONTINUE_BTN_SEL = "[data-testid='loginBtn']"                   # identity.lego.com step 1 ("Continue")
PASSWORD_SEL = "[data-testid='passwordField']"                  # identity.lego.com step 2
# Same selector as CONTINUE_BTN_SEL — LEGO reuses the form across steps and
# only changes the visible label from "Continue" to "Sign in". Not a typo.
SUBMIT_BTN_SEL = "[data-testid='loginBtn']"                     # identity.lego.com step 2 ("Sign in")

# Popups LEGO.com shows on a fresh session (no cookies). Each entry is
# (label, selector). dismiss_popups() iterates these and clicks any that
# are present; missing popups are non-fatal so the same code works on
# subsequent runs where cookies remember the dismissal.
POPUPS: list[tuple[str, str]] = [
    ("age gate (grown-up)", "[data-test='age-gate-grown-up-cta']"),
    ("cookie banner (accept all)", "[data-test='cookie-accept-all']"),
    # TODO: add more entries as identified (region picker, newsletter modal, …)
]


async def dismiss_popups(page) -> None:
    """Click through known LEGO.com popups. Each popup gets up to
    POPUP_APPEAR_TIMEOUT_MS to render before we give up and move on
    (cookies may remember a prior dismissal, so missing is fine).

    Network-idle isn't a reliable signal — LEGO injects modals from JS
    that runs after the initial load completes, so we wait per-locator.
    """
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


def load_secrets() -> dict[str, str]:
    """Minimal .env parser. KEY=VALUE, #-comments, optional quotes."""
    if not SECRETS_PATH.exists():
        raise FileNotFoundError(f".env.secrets not found at {SECRETS_PATH}")
    env: dict[str, str] = {}
    for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        env[k] = v
    return env


async def main() -> None:
    secrets = load_secrets()
    email = secrets.get("LEGO_EMAIL") or os.environ.get("LEGO_EMAIL", "")
    password = secrets.get("LEGO_PASSWORD") or os.environ.get("LEGO_PASSWORD", "")

    if not email or not password:
        print("ERROR: LEGO_EMAIL / LEGO_PASSWORD missing from .env.secrets", file=sys.stderr)
        sys.exit(1)

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    masked = f"{email[:3]}***@***{email.split('@')[-1][-4:]}" if "@" in email else "***"
    print(f"[step1] email: {masked}")
    print(f"[step1] screenshots -> {DEBUG_DIR}")

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
        )
        page = await context.new_page()

        try:
            # ── 1a. Land on PaB; dismiss popups ──────────────────────────────
            print(f"[step1] navigating to {START_URL}")
            await page.goto(START_URL, timeout=TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            await dismiss_popups(page)
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            await page.screenshot(path=str(DEBUG_DIR / "step1_00_pab_loaded.png"))
            print(f"[step1] PaB loaded. URL: {page.url}")

            # ── 1b. Click header Sign In → opens auth dialog ────────────────
            print(f"[step1] clicking header Sign In ({HEADER_SIGNIN_BTN_SEL})")
            await page.locator(HEADER_SIGNIN_BTN_SEL).first.click(timeout=10_000)
            await page.screenshot(path=str(DEBUG_DIR / "step1_01_dialog_open.png"))

            # ── 1c. Click dialog Sign In → redirects to identity.lego.com ──
            print(f"[step1] clicking dialog Sign In link ({DIALOG_SIGNIN_LINK_SEL})")
            await page.locator(DIALOG_SIGNIN_LINK_SEL).first.click(timeout=10_000)

            # ── 1d. Wait for identity.lego.com redirect ─────────────────────
            print("[step1] waiting for identity.lego.com redirect...")
            try:
                await page.wait_for_url("**identity.lego.com/**", timeout=30_000)
                print(f"[step1] on identity page: {page.url}")
            except Exception:
                print(f"[step1] no identity redirect within 30s — URL: {page.url}")
                await page.screenshot(path=str(DEBUG_DIR / "step1_ERROR_no_identity.png"))
                raise RuntimeError("identity.lego.com redirect failed")

            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            # Age gate + cookie banner only appear on initial lego.com nav;
            # cookies set during the first dismiss carry across to identity.lego.com,
            # so no second dismiss_popups() call needed here.
            await page.screenshot(path=str(DEBUG_DIR / "step1_02_identity_loaded.png"))

            # ── 1e. Fill username, click Continue ───────────────────────────
            print(f"[step1] filling username ({USERNAME_SEL})")
            await page.locator(USERNAME_SEL).first.fill(email, timeout=10_000)
            await page.screenshot(path=str(DEBUG_DIR / "step1_03_username_filled.png"))

            print(f"[step1] clicking Continue ({CONTINUE_BTN_SEL})")
            await page.locator(CONTINUE_BTN_SEL).first.click(timeout=10_000)
            # Password screen renders client-side; brief wait then screenshot.
            await page.wait_for_timeout(2_000)
            await page.screenshot(path=str(DEBUG_DIR / "step1_04_password_screen.png"))
            print(f"[step1] reached password screen. URL: {page.url}")

            # ── 1f. PAUSE — grab password + submit selectors from DOM ───────
            print("[step1] opening Inspector — use 'Pick locator' to grab:")
            print("        1. password input field")
            print("        2. final submit / sign-in button")
            print("        Then paste them to Claude. Click 'Resume' to continue.")
            

            # ── 1g. Fill password + submit (only runs if selectors set above) ─
            if PASSWORD_SEL and SUBMIT_BTN_SEL:
                await page.locator(PASSWORD_SEL).first.fill(password, timeout=10_000)
                print("[step1] password filled")
                await page.screenshot(path=str(DEBUG_DIR / "step1_05_password_filled.png"))
                await page.locator(SUBMIT_BTN_SEL).first.click(timeout=10_000)
                print("[step1] submit clicked; waiting for redirect back to lego.com...")
                try:
                    await page.wait_for_url("**www.lego.com/**", timeout=30_000)
                    print(f"[step1] SUCCESS — back on lego.com: {page.url}")
                except Exception:
                    print(f"[step1] no lego.com redirect within 30s — URL: {page.url}")
                await page.screenshot(path=str(DEBUG_DIR / "step1_06_post_login.png"))
            else:
                print("[step1] PASSWORD_SEL / SUBMIT_BTN_SEL not set — skipping password step")

            print("[step1] pausing 30s for observation. Ctrl-C to exit sooner.")
            await asyncio.sleep(30)

        except Exception as exc:
            print(f"[step1] UNHANDLED: {type(exc).__name__}: {exc}")
        finally:
            await browser.close()
            print(f"[step1] done. screenshots in {DEBUG_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
