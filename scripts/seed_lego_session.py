"""
Seed LEGO.com Playwright session for production ordering.

LEGO.com 2FA is email-only (no TOTP/SMS option), so headless automation
cannot complete the login challenge. Instead, we seed a logged-in
`storage_state` ONCE via this script — you complete Google SSO + any
email verification in a real browser window — and cache the cookies +
localStorage in Neon for production reuse. The saga's lego_client then
loads this state on every order, skipping login entirely.

When the cached state expires (the saga writes MANUAL_REVIEW with
reason='lego_session_expired' and emits a `lego.session_expired` audit
event), re-run this script to refresh. No redeploy needed.

Usage (from project root):
    .venv\\Scripts\\python.exe -m scripts.seed_lego_session

Prerequisites:
    - DB_BACKEND=postgres in .env
    - DATABASE_URL in .env.secrets (Neon pooler DSN — host contains '-pooler')
    - Migration 0003 applied: `alembic upgrade head` against the Neon
      DIRECT endpoint (see CLAUDE.md "Database migrations" section)
    - `playwright install chromium` run once after pip install

Flow:
    1. Loads .env / .env.secrets and opens the asyncpg pool.
    2. Opens a HEADED Chromium window at the LEGO login page.
    3. You complete the login (Google SSO recommended — bypasses LEGO's
       email 2FA since LEGO trusts Google's identity verification).
    4. When you press ENTER, the script probes /profile to verify the
       session stuck, extracts storage_state, and UPSERTs into
       external_sessions for provider='lego'.
    5. Closes browser + pool. Exit code 0 on success.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from .checkout.lego_session_store import save_storage_state
from .db import init_pool, close_pool, is_postgres_backend


def _load_env() -> None:
    """Load .env and optional .env.secrets from the project root.

    Inlined rather than imported from scripts/Util.py: Util does a bare
    `from logger import logger` that only resolves when picToMosiac.py
    has run first and patched sys.path (D-020 in CLAUDE.md). Fresh
    entry points hit ModuleNotFoundError. Replicating the two-file
    load_dotenv pattern here keeps this script independent of that
    latent defect.
    """
    project_root = Path(__file__).resolve().parent.parent
    env = project_root / ".env"
    if env.exists():
        load_dotenv(dotenv_path=env)
    secrets = project_root / ".env.secrets"
    if secrets.exists():
        load_dotenv(dotenv_path=secrets, override=False)

_LEGO_BASE   = "https://www.lego.com/en-us"
_LOGIN_URL   = f"{_LEGO_BASE}/profile/login"
_PROFILE_URL = f"{_LEGO_BASE}/profile"


async def seed() -> int:
    """Run the seeding flow. Returns process exit code (0 ok, non-zero error)."""
    _load_env()

    if not is_postgres_backend():
        print(
            "[seed_lego_session] ERROR: DB_BACKEND != 'postgres'. Set "
            "DB_BACKEND=postgres in .env, then re-run.",
            file=sys.stderr,
        )
        return 2
    if not os.environ.get("DATABASE_URL"):
        print(
            "[seed_lego_session] ERROR: DATABASE_URL not set. Add the Neon "
            "pooler DSN to .env.secrets, then re-run.",
            file=sys.stderr,
        )
        return 2

    await init_pool()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await context.new_page()

            print("[seed_lego_session] Navigating to LEGO login...")
            await page.goto(_LOGIN_URL)

            # Auto-click the Google SSO button so the operator only has to
            # deal with whatever Google challenges appear, not find the
            # button. LEGO 2FA is email-only (see [[project_lego_2fa]]) —
            # Google SSO bypasses it because LEGO trusts Google's
            # identity verification. If LEGO ever removes the Google
            # button (selector breaks), fall back to manual login: just
            # remove this block and update the printed instructions.
            try:
                print("[seed_lego_session] Looking for 'Continue with Google' button...")
                await page.wait_for_selector("[data-testid='googleBtn']", timeout=30000)
                print("[seed_lego_session] Clicking 'Continue with Google'...")
                await page.click("[data-testid='googleBtn']")
            except Exception as exc:
                # Don't fail seeding if the selector breaks — let the
                # operator complete login manually instead.
                print(
                    f"[seed_lego_session] WARNING: could not auto-click Google "
                    f"button ({type(exc).__name__}: {exc}). Complete login "
                    "manually in the browser."
                )

            print()
            print("=" * 64)
            print(" Complete login in the browser window:")
            print("   1. Sign in with Google (LEGO 2FA is bypassed via SSO)")
            print("   2. Complete any Google verification that appears")
            print("   3. Wait until you see LEGO as a logged-in user")
            print(" Then return to this terminal and press ENTER.")
            print("=" * 64)
            print()
            # input() blocks the event loop. asyncio.to_thread keeps Playwright
            # responsive (the browser stays interactive while we wait).
            await asyncio.to_thread(input, "Press ENTER when logged in: ")

            print("[seed_lego_session] Verifying session via /profile probe...")
            await page.goto(_PROFILE_URL)
            await page.wait_for_load_state("networkidle")
            if "/login" in page.url:
                print(
                    f"[seed_lego_session] ERROR: /profile bounced to "
                    f"{page.url!r}. Login did not stick. Aborting without "
                    "writing to DB.",
                    file=sys.stderr,
                )
                await browser.close()
                return 1

            print("[seed_lego_session] Session valid. Extracting storage_state...")
            storage_state = await context.storage_state()
            cookies_count = len(storage_state.get("cookies", []))
            origins_count = len(storage_state.get("origins", []))
            print(
                f"[seed_lego_session] Captured {cookies_count} cookies, "
                f"{origins_count} origin records."
            )

            timestamp = datetime.now(timezone.utc).isoformat()
            notes = f"Seeded {timestamp} via seed_lego_session.py"
            await save_storage_state(storage_state, notes=notes)

            print(
                "[seed_lego_session] SUCCESS: storage_state upserted into "
                "external_sessions (provider='lego')."
            )
            print(f"[seed_lego_session]   notes: {notes}")

            await browser.close()
        return 0
    finally:
        await close_pool()


def main() -> None:
    sys.exit(asyncio.run(seed()))


if __name__ == "__main__":
    main()
