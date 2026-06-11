"""Throwaway: probe whether a given Playwright variant beats Cloudflare HEADLESS.

Loads the cached LEGO storage_state, launches Pick-a-Brick headless under the
chosen driver/mode, and reports whether we got the app (title 'LEGO® Pick a
Brick…') or the Cloudflare wall ('Attention Required'). Read-only.

Usage:
    python -m scripts.checkout._cf_probe playwright            # baseline (old headless)
    python -m scripts.checkout._cf_probe playwright-stealth    # init-script evasions
    python -m scripts.checkout._cf_probe patchright            # patched undetectable driver
    python -m scripts.checkout._cf_probe patchright-chrome     # patchright + channel=chrome
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / "outputs" / "lego_debug" / "_verify_storage_state.json"
PAB_URL = "https://www.lego.com/en-us/pick-and-build/pick-a-brick"

# Minimal navigator.webdriver / chrome runtime evasions (the cheap "stealth").
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
"""


async def probe(mode: str) -> None:
    if mode.startswith("patchright"):
        from patchright.async_api import async_playwright
    else:
        from playwright.async_api import async_playwright

    channel = "chrome" if mode.endswith("chrome") else None
    state = str(STATE_FILE) if STATE_FILE.exists() else None

    async with async_playwright() as pw:
        launch_kwargs = {"headless": True}
        if channel:
            launch_kwargs["channel"] = channel
        browser = await pw.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            storage_state=state,
        )
        if mode == "playwright-stealth":
            await ctx.add_init_script(_STEALTH_JS)
        page = await ctx.new_page()
        try:
            await page.goto(PAB_URL, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            title = await page.title()
            html_len = len(await page.content())
            inputs = await page.locator("input").count()
            blocked = ("Cloudflare" in title) or ("Attention Required" in title)
            verdict = "BLOCKED (Cloudflare)" if blocked else (
                "PASSED (app rendered)" if inputs > 0 else "UNKNOWN (no inputs)")
            print(f"[{mode}] channel={channel or 'bundled'} title={title!r} "
                  f"html_len={html_len} inputs={inputs} -> {verdict}")
        finally:
            await browser.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "playwright"
    asyncio.run(probe(mode))
