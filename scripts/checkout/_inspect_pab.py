"""Throwaway: auto-dump real Pick-a-Brick DOM selectors using the cached session.

Loads outputs/lego_debug/_verify_storage_state.json (saved by manual_lego_test),
navigates to Pick-a-Brick **headed** (Cloudflare walls headless Chromium), and
writes the actual attributes of every button + the Upload List control, the file
input it reveals, and the Add-to-Bag controls to:

    outputs/lego_debug/selectors_dump.txt

so we can replace the scaffold guesses in lego_client._run_checkout Steps C/D
with verified selectors. Read-only: opens the upload UI but places no order.

Run headed (a Chromium window will appear) from project root:
    .\\.venv\\Scripts\\python.exe -m scripts.checkout._inspect_pab
Pass --upload to ALSO exercise the upload end-to-end (adds a tiny 2-element
test list to the bag — reversible, NO order placed) and then open the cart to
dump the Step D "Checkout" CTA selector. Without the flag the run is read-only.
"""

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEBUG_DIR = PROJECT_ROOT / "outputs" / "lego_debug"
STATE_FILE = DEBUG_DIR / "_verify_storage_state.json"
DUMP_FILE = DEBUG_DIR / "selectors_dump.txt"
PAB_URL = "https://www.lego.com/en-us/pick-and-build/pick-a-brick"
TIMEOUT_MS = 90_000

POPUPS = [
    ("age gate", "[data-test='age-gate-grown-up-cta']"),
    ("cookie accept", "[data-test='cookie-accept-all']"),
]

_LINES: list[str] = []


def out(s: str = "") -> None:
    print(s)
    _LINES.append(s)


_DESCRIBE_JS = """e => ({
    tag: e.tagName,
    type: e.getAttribute('type'),
    dataTest: e.getAttribute('data-test'),
    dataTestId: e.getAttribute('data-testid'),
    id: e.id || null,
    name: e.getAttribute('name'),
    ariaLabel: e.getAttribute('aria-label'),
    role: e.getAttribute('role'),
    cls: (e.getAttribute('class') || '').slice(0, 100),
    text: (e.innerText || e.value || '').trim().slice(0, 50),
    outer: e.outerHTML.slice(0, 220),
})"""


async def describe(page, locator, label: str, limit: int = 8) -> None:
    try:
        count = await locator.count()
    except Exception as exc:
        out(f"  [{label}] count() failed: {exc}")
        return
    out(f"  [{label}] match count = {count}")
    for i in range(min(count, limit)):
        try:
            info = await locator.nth(i).evaluate(_DESCRIBE_JS)
            out(f"    #{i}: {info}")
        except Exception as exc:
            out(f"    #{i}: evaluate failed: {exc}")


# Tiny reversible test list (2 real PaB elements). NO order is placed — this
# only fills the bag so we can reach + dump the checkout CTA. Clear it after via
# the cart drawer or lego.com/en-us/cart.
SAMPLE_ITEMS = [
    {"elementId": "302421", "quantity": 2},
    {"elementId": "300126", "quantity": 2},
]


async def maybe_overwrite(page, where: str = "") -> None:
    """If the 'Overwrite pieces?' modal appears, confirm it. No-op otherwise.

    Shown only when the bag already holds picked pieces and a new list is
    uploaded. Tolerant: short wait, click if present, never fatal.
    """
    btn = page.locator("[data-test='overwrite-pieces-modal-overwrite-button']")
    try:
        await btn.wait_for(state="visible", timeout=4000)
    except Exception:
        return
    try:
        await btn.click()
        out(f"[overwrite] confirmed 'Yes, overwrite' ({where})")
        await page.wait_for_timeout(1000)
    except Exception as exc:
        out(f"[overwrite] click failed ({where}): {type(exc).__name__}: {exc}")


async def exercise_upload_and_cart(page) -> None:
    """Upload SAMPLE_ITEMS, add to bag, open cart, dump the checkout CTA.

    Places NO order. Stops at the cart/checkout page. The point is to capture
    the Step D 'Checkout' selector that's still a scaffold guess.
    """
    out("\n=== EXERCISE: upload SAMPLE_ITEMS -> add to bag ===")
    try:
        # The read-only dump above already clicked "Upload List", so the modal
        # may already be open. Only open it if the file input isn't present yet.
        upload_input = page.locator("[data-test='pab-listUploader-input']")
        if await upload_input.count() == 0:
            await page.click("[data-test='pab-listUploader-open-modal-desktop-button']", timeout=10000)
        await upload_input.wait_for(state="attached", timeout=15000)
        # set_input_files works on a hidden/dialog-nested file input — no click.
        await upload_input.set_input_files(files=[{
            "name": "order_list.json",
            "mimeType": "application/json",
            "buffer": json.dumps(SAMPLE_ITEMS).encode("utf-8"),
        }])
        # If the bag already had pieces, an "Overwrite pieces?" modal blocks here.
        await maybe_overwrite(page, "after upload")
        out("[exercise] file set; waiting for 'View All Pieces'...")
        view_pieces = page.locator("[data-test='pab-listUploader-viewPieces-button']")
        await view_pieces.wait_for(state="visible", timeout=20000)
        await view_pieces.click()
        out("[exercise] clicked View All Pieces ✓")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(DEBUG_DIR / "exercise_01_pieces.png"))

        out("[exercise] waiting for 'Pick selected pieces'...")
        pick = page.locator("[data-test='pab-listupload-button-addRemove']")
        await pick.wait_for(state="visible", timeout=20000)
        await pick.click()
        out("[exercise] clicked Pick selected pieces ✓ (adds to cart, opens cart modal)")
        await maybe_overwrite(page, "after pick")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(DEBUG_DIR / "exercise_02_cart_modal.png"))
    except Exception as exc:
        out(f"[exercise] upload/add failed: {type(exc).__name__}: {exc}")
        await page.screenshot(path=str(DEBUG_DIR / "exercise_ERROR_upload.png"))
        return

    # We STOP at the cart modal. Its "Add to Bag" (pab-cart-add-to-main-cart-button)
    # starts checkout (Step D/E territory) — do NOT click it here. Just dump the
    # modal's buttons so we can capture the real checkout CTA(s).
    out("\n=== EXERCISE: dump cart-modal buttons (NOT clicking Add to Bag) ===")
    try:
        out("\n--- Add to Bag (cart modal commit / checkout entry) ---")
        await describe(page, page.locator("[data-test='pab-cart-add-to-main-cart-button']"),
                       "pab-cart-add-to-main-cart-button", limit=3)
        out("\n--- cart drawer/modal container ---")
        await describe(page, page.locator("[data-test='pick-a-brick-cart']"), "pick-a-brick-cart", limit=2)
        out("\n--- buttons/links mentioning checkout/secure/proceed/bag ---")
        await describe(page, page.locator(
            "text=/checkout|secure|proceed|continue to|view bag|go to bag/i"),
            "checkout-copy", limit=12)
        out("\n--- data-test containing checkout/order/cart/bag ---")
        await describe(page, page.locator(
            "[data-test*='checkout'],[data-test*='order'],[data-test*='cart'],[data-test*='bag']"),
            "checkout attrs", limit=20)
    except Exception as exc:
        out(f"[exercise] cart-modal dump failed: {type(exc).__name__}: {exc}")
        await page.screenshot(path=str(DEBUG_DIR / "exercise_ERROR_cart.png"))


async def dump_checkout_page(page) -> None:
    """Click 'Add to Bag' ONCE, then dump the resulting bag/checkout page.

    This is the only place we cross into the checkout flow. It places NO order —
    it clicks Add to Bag (which starts checkout) and then exclusively READS the
    DOM. It must never click a place-order/pay button. Gated behind --checkout.
    """
    out("\n=== CHECKOUT: clicking 'Add to Bag' (starts checkout — NO order placed) ===")
    add = page.locator("[data-test='pab-cart-add-to-main-cart-button']")
    try:
        await add.wait_for(state="visible", timeout=15000)
        if (await add.get_attribute("aria-disabled")) == "true":
            out("[checkout] Add to Bag is disabled — aborting (cart not ready).")
            return
        url_before = page.url
        await add.click()
        out("[checkout] clicked Add to Bag ✓ — waiting for 'Updated My Bag' modal...")
        # 'Updated My Bag' confirmation modal: continue via "View My Bag".
        view_bag = page.locator("[data-test='pab-add-to-bag-confirmation-button-cart']")
        try:
            await view_bag.wait_for(state="visible", timeout=20000)
            await page.screenshot(path=str(DEBUG_DIR / "checkout_01_added_modal.png"))
            out("[checkout] confirmation modal shown; clicking 'View My Bag'...")
            await view_bag.click()
        except Exception:
            out("[checkout] no confirmation modal — proceeding with current page.")
        try:
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        out(f"[checkout] url before={url_before}")
        out(f"[checkout] url after ={page.url}")
        await page.screenshot(path=str(DEBUG_DIR / "checkout_02_bag_page.png"), full_page=True)
    except Exception as exc:
        out(f"[checkout] Add to Bag click failed: {type(exc).__name__}: {exc}")
        await page.screenshot(path=str(DEBUG_DIR / "checkout_ERROR.png"))
        return

    out(f"\n[checkout] page title: {await page.title()!r}")
    out("\n--- all links (href) ---")
    await describe(page, page.locator("a[href]"), "links", limit=30)
    out("\n--- buttons/links mentioning checkout/pay/place/order/continue/proceed ---")
    await describe(page, page.locator(
        "text=/checkout|pay now|place order|continue|proceed|complete|review order|go to bag/i"),
        "checkout-copy", limit=20)
    out("\n--- data-test containing checkout/pay/order/place/bag/basket ---")
    await describe(page, page.locator(
        "[data-test*='checkout'],[data-test*='pay'],[data-test*='order'],"
        "[data-test*='place'],[data-test*='bag'],[data-test*='basket'],"
        "[data-testid*='checkout'],[data-testid*='pay']"),
        "checkout attrs", limit=30)
    out("\n[checkout] DONE dumping. NO order placed. Window holds open below.")


async def main() -> None:
    if not STATE_FILE.exists():
        out(f"ERROR: {STATE_FILE} missing. Run manual_lego_test.py first to seed it.")
        return

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # headed — Cloudflare walls headless
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            storage_state=str(STATE_FILE),
        )
        page = await context.new_page()
        try:
            out(f"[inspect] goto {PAB_URL}")
            await page.goto(PAB_URL, timeout=TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            for label, sel in POPUPS:
                try:
                    loc = page.locator(sel).first
                    await loc.wait_for(state="visible", timeout=5000)
                    await loc.click(timeout=5000)
                    out(f"[inspect] dismissed popup: {label}")
                except Exception:
                    pass
            await page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

            title = await page.title()
            out(f"[diag] title={title!r}  url={page.url}")
            if "Cloudflare" in title or "Attention Required" in title:
                out("[diag] CLOUDFLARE WALL even headed — stop. (Try solving the challenge "
                    "in the window, then re-run.)")
                return
            # Confirm the SPA hydrated before querying.
            try:
                await page.wait_for_selector("input", timeout=20000)
                out("[diag] page hydrated (found <input>)")
            except Exception:
                out("[diag] WARNING: no <input> appeared — page may not have hydrated")

            out("\n=== ALL buttons on page (search bar row holds Upload List) ===")
            await describe(page, page.locator("button"), "button", limit=40)

            out("\n=== Upload List, by text ===")
            await describe(page, page.get_by_text("Upload List"), "get_by_text('Upload List')")

            out("\n=== file inputs BEFORE Upload List click ===")
            await describe(page, page.locator("input[type='file']"), "input[type=file] pre")

            out("\n=== clicking Upload List ===")
            clicked = False
            for cand_label, cand in (
                ("role=button", page.get_by_role("button", name="Upload List")),
                ("text", page.get_by_text("Upload List").first),
            ):
                try:
                    await cand.first.click(timeout=8000)
                    clicked = True
                    out(f"[inspect] clicked Upload List via {cand_label}")
                    break
                except Exception as exc:
                    out(f"[inspect] click via {cand_label} failed: {type(exc).__name__}: {exc}")
            if clicked:
                await page.wait_for_timeout(2500)
                try:
                    await page.screenshot(path=str(DEBUG_DIR / "inspect_upload_modal.png"))
                except Exception:
                    pass
                out("\n=== file inputs AFTER Upload List click ===")
                await describe(page, page.locator("input[type='file']"), "input[type=file] post")
                out("\n=== dialog / modal containers ===")
                await describe(page, page.locator("[role='dialog'], [aria-modal='true']"), "dialog")
                out("\n=== elements mentioning json/browse/drag/drop/choose ===")
                await describe(page, page.locator("text=/json|browse|drag|drop|choose a file|select file/i"), "upload-copy")

            out("\n=== Add to Bag (per-piece) ===")
            await describe(page, page.get_by_role("button", name="Add to Bag"), "role=button Add to Bag", limit=4)
            await describe(page, page.locator("[data-test*='add'], [data-testid*='add']"), "attr*=add", limit=6)

            out("\n=== cart / bag count indicator ===")
            await describe(page, page.locator("[data-test*='cart'],[data-test*='bag'],[data-testid*='cart'],[data-testid*='bag']"), "cart/bag attrs", limit=6)

            if "--upload" in sys.argv:
                await exercise_upload_and_cart(page)
            if "--checkout" in sys.argv:
                await dump_checkout_page(page)

            out("\n[inspect] holding window open 20s...")
            await page.wait_for_timeout(20000)
        finally:
            await browser.close()
            DUMP_FILE.write_text("\n".join(_LINES), encoding="utf-8")
            print(f"\n[inspect] selectors written to {DUMP_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
