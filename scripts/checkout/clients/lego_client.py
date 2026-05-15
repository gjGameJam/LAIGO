"""
LEGO.com Pick-a-Brick client: price lookup, availability checking, and Playwright ordering.

All elements sourced through this client use seller_id="lego_official" in the optimizer.
Since all LEGO.com items go into one order (one Playwright session), the optimizer treats
LEGO.com as a single seller and counts shipping once for the whole order.

Pricing / availability:
  Uses LEGO.com's internal search API derived from network traffic analysis of the
  Pick-a-Brick page. Not a public API — may break if LEGO updates their frontend.
  To re-derive the endpoint: open DevTools → Network on
    https://www.lego.com/en-us/pick-and-build/pick-a-brick
  search for any element ID, and find the XHR request returning product data.
  Update _LEGO_SEARCH_URL and the field mappings in _parse_result() below.

Shipping:
  LEGO.com charges flat shipping per order (LEGO_SHIPPING_COST_CENTS env var, default
  599 = $5.99). The optimizer counts this once for the "lego_official" seller regardless
  of how many elements are sourced from LEGO.com.

Ordering:
  Uses Playwright Chromium automation. Requires:
    - LEGO_EMAIL and LEGO_PASSWORD set in .env.secrets
    - A saved payment method on LAIGO's LEGO.com account
    - `playwright install chromium` run once after pip install
  Screenshots are saved to outputs/lego_debug/ on every step and on failure.
  SELECTOR FRAGILITY: LEGO.com is a React SPA. If selectors break, inspect the
  current DOM and update _run_checkout() accordingly.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, BrowserContext, Page

from ..models import SellerListing
from ..cache import cache_get, cache_set

logger = logging.getLogger("laigo")

SELLER_ID = "lego_official"
SELLER_NAME = "LEGO.com"

# Shipping cost per order in cents. Configurable so it can be set to 0 for testing
# or adjusted when LEGO.com changes their policy.
_SHIPPING_COST_CENTS = int(os.environ.get("LEGO_SHIPPING_COST_CENTS", "599"))
_MAX_QTY_PER_ITEM = int(os.environ.get("LEGO_MAX_QTY_PER_ITEM", "9999"))

# LEGO.com internal search endpoint (verify against live network traffic if broken)
_LEGO_SEARCH_URL = "https://www.lego.com/api/product/search/en-US"
_SEARCH_TIMEOUT = 10.0
_AVAIL_CACHE_TTL = 1800   # 30 minutes
_PRICE_CACHE_TTL = 3600   # 1 hour

_AVAILABLE_STATUSES = {"instock", "available", "limitedavailability", "available for sale"}

# Playwright config
LEGO_EMAIL    = os.environ.get("LEGO_EMAIL", "")
LEGO_PASSWORD = os.environ.get("LEGO_PASSWORD", "")
_TIMEOUT_MS   = int(os.environ.get("LEGO_FALLBACK_TIMEOUT_SECONDS", "90")) * 1000
_DEBUG_DIR    = Path(os.environ.get("OUTPUT_DIR", "./outputs")).resolve() / "lego_debug"
_LEGO_BASE    = "https://www.lego.com/en-us"
_LOGIN_URL    = f"{_LEGO_BASE}/profile/login"
_PAB_URL      = f"{_LEGO_BASE}/pick-and-build/pick-a-brick"


# ── Shared search API call ────────────────────────────────────────────────────

async def _search(element_id: str) -> Optional[dict]:
    """
    Call the LEGO.com search API for a single element.
    Returns the matching result dict, or None if not found / request failed.
    """
    cache_key = f"lego_raw:{element_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _LEGO_SEARCH_URL,
                params={
                    "q": element_id,
                    "page": 1,
                    "pageSize": 10,
                    "searchTypes": "PickABrick",
                },
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en-US",
                    "User-Agent": "Mozilla/5.0 (compatible; LAIGO/1.0)",
                },
                timeout=_SEARCH_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()

        # Find the matching result
        for item in data.get("results", []):
            item_eid = str(item.get("elementId", item.get("id", "")))
            if item_eid == str(element_id):
                await cache_set(cache_key, item, _PRICE_CACHE_TTL)
                return item

        await cache_set(cache_key, None, _AVAIL_CACHE_TTL)
        return None

    except Exception as exc:
        logger.warning(f"LEGO.com search failed for element {element_id}: {exc}")
        return None


def _parse_available(item: dict) -> bool:
    """Return True if the item dict indicates available stock on Pick-a-Brick."""
    raw = str(item.get("availability", item.get("availabilityStatus", ""))).lower()
    raw = raw.replace("_", "").replace("-", "").strip()
    if raw in _AVAILABLE_STATUSES:
        return True
    avail_obj = item.get("availabilityDetails", {})
    if isinstance(avail_obj, dict):
        nested = str(avail_obj.get("status", "")).lower().replace("_", "")
        if nested in _AVAILABLE_STATUSES:
            return True
    return False


def _parse_price_cents(item: dict) -> Optional[int]:
    """
    Extract price in US cents from a LEGO.com search result.

    Field names as of 2026-05; update against live API if this returns None.
    Candidates tried in order:
      price.centAmount, price.amount (assumed dollars), priceValue (assumed dollars),
      prices[0].centAmount
    """
    price_obj = item.get("price", {})
    if isinstance(price_obj, dict):
        if "centAmount" in price_obj:
            return int(price_obj["centAmount"])
        if "amount" in price_obj:
            try:
                return round(float(price_obj["amount"]) * 100)
            except (ValueError, TypeError):
                pass

    raw = item.get("priceValue", item.get("price_value"))
    if raw is not None:
        try:
            return round(float(raw) * 100)
        except (ValueError, TypeError):
            pass

    prices = item.get("prices", [])
    if isinstance(prices, list) and prices:
        first = prices[0]
        if isinstance(first, dict) and "centAmount" in first:
            return int(first["centAmount"])

    return None


# ── Public availability API ───────────────────────────────────────────────────

async def check_element_available(element_id: str) -> bool:
    """
    Return True if the element is currently purchasable on LEGO.com Pick-a-Brick.
    Returns False on any error — conservative: blocks ordering on uncertainty.
    """
    result = await _search(element_id)
    if result is None:
        return False
    return _parse_available(result)


async def check_elements_available(element_ids: list[str]) -> dict[str, bool]:
    """Check LEGO.com availability for multiple element IDs concurrently."""
    if not element_ids:
        return {}
    results = await asyncio.gather(
        *[check_element_available(eid) for eid in element_ids],
        return_exceptions=True,
    )
    return {
        eid: (r if isinstance(r, bool) else False)
        for eid, r in zip(element_ids, results)
    }


# ── Optimizer-facing listing API ─────────────────────────────────────────────

async def get_listing_for_element(
    element_id: str,
    shipping_country: str,
) -> Optional[SellerListing]:
    """
    Return a SellerListing for this element if it's available on LEGO.com Pick-a-Brick,
    None otherwise.

    Shipping cost is set only on the first listing fetched per order (conceptually).
    In practice, the optimizer uses seller_meta[SELLER_ID].shipping_cost_cents from
    whichever listing it processes last — all listings have the same shipping_cost_cents
    value, so the order doesn't matter. The optimizer counts it once per seller.
    """
    result = await _search(element_id)
    if result is None or not _parse_available(result):
        return None

    price_cents = _parse_price_cents(result)
    if price_cents is None:
        logger.warning(
            f"LEGO.com price unknown for element {element_id} — "
            "update _parse_price_cents() field names against live API. Using 0."
        )
        price_cents = 0

    return SellerListing(
        seller_id=SELLER_ID,
        seller_name=SELLER_NAME,
        price_per_cent=price_cents,
        available_qty=_MAX_QTY_PER_ITEM,
        shipping_cost_cents=_SHIPPING_COST_CENTS,
        lot_id=element_id,
    )


async def get_all_listings(
    order_items: list[dict],
    shipping_country: str,
    shipping_zip: str,       # kept for interface consistency; LEGO.com ignores zip
    cache_ttl: int = 3600,
) -> dict[str, list[SellerListing]]:
    """
    Return {element_id: [SellerListing]} for all available items.
    Elements unavailable on LEGO.com return an empty list, causing the optimizer
    to route them to lego_fallback_items (unsourceable in single-source mode).
    """
    async def _fetch(eid: str) -> tuple[str, list[SellerListing]]:
        listing = await get_listing_for_element(eid, shipping_country)
        return eid, ([listing] if listing else [])

    pairs = await asyncio.gather(*[_fetch(item["elementId"]) for item in order_items])
    return dict(pairs)


# ── Playwright ordering ───────────────────────────────────────────────────────

async def _screenshot(page: Page, job_id: str, label: str) -> None:
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(_DEBUG_DIR / f"{job_id}_{label}.png"))
    except Exception as exc:
        logger.warning(f"[lego_client] screenshot '{label}' failed: {exc}")


async def order_from_lego(
    items: list[dict],   # [{elementId: str, quantity: int}]
    job_id: str,
) -> str:
    """
    Log into LEGO.com with LAIGO's account, upload items to Pick-a-Brick,
    and complete checkout. Returns the LEGO.com order confirmation number.

    Requires:
      - LEGO_EMAIL and LEGO_PASSWORD set in .env.secrets
      - A saved payment method on LAIGO's LEGO.com account
      - `playwright install chromium` run once after pip install

    Raises RuntimeError on any step failure (screenshots saved first).
    """
    if not LEGO_EMAIL or not LEGO_PASSWORD:
        raise RuntimeError(
            "LEGO_EMAIL and LEGO_PASSWORD must be set in .env.secrets "
            "for LEGO.com ordering."
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page: Page = await context.new_page()
        try:
            return await _run_checkout(page, items, job_id)
        except Exception as exc:
            await _screenshot(page, job_id, "ERROR_final_state")
            raise RuntimeError(f"LEGO.com order failed: {exc}") from exc
        finally:
            await browser.close()


async def _run_checkout(page: Page, items: list[dict], job_id: str) -> str:
    # ── Step A: Log in ────────────────────────────────────────────────────────
    logger.info(f"[lego_client] [{job_id}] navigating to login")
    await page.goto(_LOGIN_URL, timeout=_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)
    await page.fill("[data-test='email-input']", LEGO_EMAIL)
    await page.fill("[data-test='password-input']", LEGO_PASSWORD)
    await page.click("[data-test='login-button']")
    await page.wait_for_url("**/profile/**", timeout=_TIMEOUT_MS)
    await _screenshot(page, job_id, "01_logged_in")

    # ── Step B: Navigate to Pick-a-Brick ─────────────────────────────────────
    await page.goto(_PAB_URL, timeout=_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)
    await _screenshot(page, job_id, "02_pick_a_brick")

    # ── Step C: Upload JSON cart ──────────────────────────────────────────────
    # LEGO.com Pick-a-Brick accepts a JSON file upload via an "Import" button.
    # If this selector breaks, look for: button with text "Import"/"Upload",
    # or a data-test attribute near the search bar.
    upload_input = page.locator("input[type='file']").first
    json_bytes = json.dumps(items).encode("utf-8")
    await upload_input.set_input_files(files=[{
        "name": "order_list.json",
        "mimeType": "application/json",
        "buffer": json_bytes,
    }])
    await page.wait_for_selector("[data-test='add-to-cart-button']", timeout=_TIMEOUT_MS)
    await page.click("[data-test='add-to-cart-button']")
    await page.wait_for_selector("[data-test='cart-count']", timeout=_TIMEOUT_MS)
    await _screenshot(page, job_id, "03_cart_loaded")
    logger.info(f"[lego_client] [{job_id}] cart populated with {len(items)} items")

    # ── Step D: Proceed to checkout ───────────────────────────────────────────
    await page.click("[data-test='checkout-button']")
    await page.wait_for_url("**/checkout/**", timeout=_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)
    await _screenshot(page, job_id, "04_checkout")

    # ── Step E: Place order (uses saved payment method on LAIGO's account) ────
    await page.click("[data-test='place-order-button']")
    await page.wait_for_selector("[data-test='order-confirmation-number']", timeout=_TIMEOUT_MS)
    await _screenshot(page, job_id, "05_confirmed")

    confirmation = (await page.inner_text("[data-test='order-confirmation-number']")).strip()
    logger.info(f"[lego_client] [{job_id}] order confirmed: {confirmation}")
    return confirmation
