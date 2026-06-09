"""
LEGO.com Pick-a-Brick client: price lookup, availability checking, and Playwright ordering.

All elements sourced through this client use seller_id="lego_official" in the optimizer.
Since all LEGO.com items go into one order (one Playwright session), the optimizer treats
LEGO.com as a single seller and counts shipping once for the whole order.

Pricing / availability:
  Uses LEGO.com's internal Pick-a-Brick GraphQL endpoint (POST to
  /api/graphql/PickABrickQuery). Derived from network traffic analysis — not
  a public API. Anonymous calls work; LEGO previously ran a REST search
  endpoint (api/product/search/en-US) which was retired in 2026-05.
  To re-derive if it moves again: open DevTools → Network on
    https://www.lego.com/en-us/pick-and-build/pick-a-brick
  search for any element ID, find the PickABrickQuery POST, copy the request
  body verbatim (LEGO validates the exact GraphQL shape — simplification
  trips a 400 "Validation error"). Update _LEGO_SEARCH_URL, _LEGO_GRAPHQL_QUERY,
  and the field navigation inside _search() below.

Shipping:
  LEGO.com charges flat shipping per order (LEGO_SHIPPING_COST_CENTS env var, default
  599 = $5.99). The optimizer counts this once for the "lego_official" seller regardless
  of how many elements are sourced from LEGO.com.

Ordering:
  Uses Playwright Chromium automation with a cached `storage_state` JSON
  loaded from the Neon-backed `external_sessions` table (provider='lego').
  LEGO.com 2FA is email-only — see project memory [[project_lego_2fa]] —
  so headless login is impossible. Instead the operator seeds the session
  once via `python -m scripts.seed_lego_session` (headed Playwright, manual
  Google SSO + email verification) and `order_from_lego` reuses the cached
  cookies/localStorage on every order.

  When the cached state expires (LEGO rotates session cookies, or the row
  is missing on a fresh deploy), `order_from_lego` raises
  `LegoSessionExpiredError` and the saga writes MANUAL_REVIEW with
  reason='lego_session_expired'. The operator re-runs the seed script to
  restore service — no redeploy needed.

  Requires:
    - migration 0003 applied (external_sessions table exists)
    - a seeded row in external_sessions for provider='lego'
    - a saved payment method on LAIGO's LEGO.com account
    - `playwright install chromium` run once after pip install

  Screenshots are saved to outputs/lego_debug/ on every step and on failure.
  SELECTOR FRAGILITY: LEGO.com is a React SPA. If selectors break, inspect the
  current DOM and update _run_checkout() accordingly.

  DEPRECATED env vars: LEGO_EMAIL / LEGO_PASSWORD are no longer used by the
  ordering flow (storage_state replaced the headless login). Kept readable
  for now in case a future fallback path needs them; do not rely on them.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, BrowserContext, Page

from ..models import SellerListing, StockoutError, LegoSessionExpiredError
from ..cache import cache_get, cache_set, cache_delete
from .. import audit
from .. import lego_session_store

logger = logging.getLogger("laigo")

SELLER_ID = "lego_official"
SELLER_NAME = "LEGO.com"

# Shipping cost per order in cents. Configurable so it can be set to 0 for testing
# or adjusted when LEGO.com changes their policy.
_SHIPPING_COST_CENTS = int(os.environ.get("LEGO_SHIPPING_COST_CENTS", "599"))
_MAX_QTY_PER_ITEM = int(os.environ.get("LEGO_MAX_QTY_PER_ITEM", "9999"))

# LEGO.com Pick-a-Brick GraphQL endpoint. As of 2026-05 the legacy REST search
# (api/product/search/en-US) was retired and replaced by this Apollo-style
# GraphQL POST. Anonymous calls work — no JWT, no session cookie — provided
# Origin, User-Agent, x-locale, and the EXACT query string below are sent.
# Simplifying the query (removing __typename or fragments) trips the server-side
# validator with HTTP 400 "Validation error". Re-derive against live network
# traffic if this breaks: DevTools → Network → PickABrickQuery → Payload.
_LEGO_SEARCH_URL = "https://www.lego.com/api/graphql/PickABrickQuery"
_SEARCH_TIMEOUT = 10.0
_AVAIL_CACHE_TTL = 1800   # 30 minutes
_PRICE_CACHE_TTL = 3600   # 1 hour

# Headers must be browser-realistic — Cloudflare in front of LEGO.com challenges
# default httpx UAs. The set below has been verified against the live endpoint.
_LEGO_SEARCH_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.lego.com",
    "Referer": "https://www.lego.com/en-us/pick-and-build/pick-a-brick",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    ),
    "x-locale": "en-US",
}

# Exact GraphQL query string LEGO's Pick-a-Brick frontend emits. Whitespace
# doesn't matter to GraphQL validators, but field selections and fragments do —
# leave this alone unless re-deriving from a fresh DevTools capture.
_LEGO_GRAPHQL_QUERY = (
    "query PickABrickQuery($input: ElementQueryInput!, $sku: String) {\n"
    "  searchElements(input: $input) {\n"
    "    results { ...ElementLeaf __typename }\n"
    "    facets { ...FacetData __typename }\n"
    "    set {\n"
    "      id type name imageUrl instructionsUrl pieces inStock\n"
    "      price { formattedAmount __typename }\n"
    "      __typename\n"
    "    }\n"
    "    total count __typename\n"
    "  }\n"
    "}\n"
    "\n"
    "fragment FacetData on Facet {\n"
    "  id key name\n"
    "  labels {\n"
    "    count key name\n"
    "    children {\n"
    "      count key name\n"
    "      ... on FacetValue { value __typename }\n"
    "      __typename\n"
    "    }\n"
    "    ... on FacetValue { value __typename }\n"
    "    ... on FacetRange { from to __typename }\n"
    "    __typename\n"
    "  }\n"
    "  __typename\n"
    "}\n"
    "\n"
    "fragment ElementLeaf on SearchResultElement {\n"
    "  id designId collapseDesignId name imageUrl maxOrderQuantity deliveryChannel\n"
    "  colorHex contrastColorHex\n"
    "  price { centAmount formattedAmount currencyCode formattedValue __typename }\n"
    "  quantityInSet(sku: $sku)\n"
    "  facets {\n"
    "    category { ...ElementFacetCategory __typename }\n"
    "    subcategory { ...ElementFacetCategory __typename }\n"
    "    color { ...ElementFacetCategory __typename }\n"
    "    colorFamily { ...ElementFacetCategory __typename }\n"
    "    system __typename\n"
    "  }\n"
    "  siblings {\n"
    "    id colorHex contrastColorHex availability\n"
    "    price { formattedAmount formattedValue __typename }\n"
    "    __typename\n"
    "  }\n"
    "  availability __typename\n"
    "}\n"
    "\n"
    "fragment ElementFacetCategory on ElementCategory { name key __typename }"
)

# `availability` is now a single uppercase token in the GraphQL response
# ("AVAILABLE" / "OUT_OF_STOCK"). `_parse_available` lowercases + strips
# underscores, so "AVAILABLE" → "available" → matches the set below.
# Older legacy tokens kept for defensive parsing in case LEGO ships a mixed
# response during catalog migrations.
_AVAILABLE_STATUSES = {"instock", "available", "limitedavailability", "available for sale"}

# Once-per-process dedup for endpoint-outage alerts. Key: (status_code, url).
# Distinct outage classes still alert, but a single 50-element quote against
# a dead endpoint produces one CRITICAL line + one audit row, not fifty. The
# set is cleared by process restart (intentional — operator decides when to
# re-arm by restarting after a fix).
_OUTAGE_ALERTED: set[tuple[int, str]] = set()

# Playwright config
# LEGO_EMAIL / LEGO_PASSWORD are deprecated — replaced by storage_state caching
# via lego_session_store (see module docstring). Kept readable so a future
# fallback path can re-use them without re-adding env var plumbing.
LEGO_EMAIL    = os.environ.get("LEGO_EMAIL", "")
LEGO_PASSWORD = os.environ.get("LEGO_PASSWORD", "")
_TIMEOUT_MS   = int(os.environ.get("LEGO_FALLBACK_TIMEOUT_SECONDS", "90")) * 1000
_DEBUG_DIR    = Path(os.environ.get("OUTPUT_DIR", "./outputs")).resolve() / "lego_debug"
_LEGO_BASE    = "https://www.lego.com/en-us"
_LOGIN_URL    = f"{_LEGO_BASE}/profile/login"
_PROFILE_URL  = f"{_LEGO_BASE}/profile"
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

    body = {
        "operationName": "PickABrickQuery",
        "variables": {
            "input": {
                "page": 1,
                "perPage": 50,
                "sort": {"key": "RELEVANCE", "direction": "DESC"},
                # Ask for both states so an out-of-stock element still returns
                # one result with availability="OUT_OF_STOCK" (instead of zero
                # results, which would conflate OOS with "not in catalog").
                "availability": ["AVAILABLE", "OUT_OF_STOCK"],
                "query": str(element_id),
                "fetchSiblings": True,
            }
        },
        "query": _LEGO_GRAPHQL_QUERY,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _LEGO_SEARCH_URL,
                json=body,
                headers=_LEGO_SEARCH_HEADERS,
                timeout=_SEARCH_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()

        results = (
            data.get("data", {}).get("searchElements", {}).get("results", [])
        )
        for item in results:
            if str(item.get("id", "")) == str(element_id):
                await cache_set(cache_key, item, _PRICE_CACHE_TTL)
                return item

        await cache_set(cache_key, None, _AVAIL_CACHE_TTL)
        return None

    except httpx.HTTPStatusError as exc:
        # Marketplace endpoint returned a non-success status. 429 is transient
        # (rate limit) — log, skip the alert, let the caller retry next quote.
        # Everything else is an endpoint-down signal: route gone (404), auth
        # broken (401/403), server down (5xx). Without the alert, the symptom
        # presents as "every quote is 100% unsourceable" with no P0 signal —
        # the exact failure mode that lost LEGO sourcing in 2026-05.
        status = exc.response.status_code
        if status == 429:
            logger.warning(
                f"LEGO.com search rate-limited (HTTP 429) for element {element_id}"
            )
            return None

        key = (status, _LEGO_SEARCH_URL)
        if key not in _OUTAGE_ALERTED:
            _OUTAGE_ALERTED.add(key)
            logger.critical(
                f"[outage] LEGO.com search endpoint returned HTTP {status} at "
                f"{_LEGO_SEARCH_URL!r} — every quote will route to unsourceable "
                f"until the endpoint is restored or _LEGO_SEARCH_URL is "
                f"re-derived against live Pick-a-Brick traffic. First element "
                f"that hit this: {element_id}."
            )
            await audit.emit(
                "marketplace.endpoint_unavailable",
                data={
                    "seller_id": SELLER_ID,
                    "endpoint": "search",
                    "status_code": status,
                    "url": _LEGO_SEARCH_URL,
                    "triggered_by_element": element_id,
                },
            )
        return None

    except httpx.RequestError as exc:
        # Network-class error (timeout, DNS, connection reset). Element-scope
        # rather than endpoint-down. Log per element; do not alert.
        logger.warning(
            f"LEGO.com search network error for element {element_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    except Exception as exc:
        # JSON parse / unexpected shape / programmer error. Element-scope.
        logger.warning(
            f"LEGO.com search failed for element {element_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


# ── Cache invalidation ────────────────────────────────────────────────────────
# Owned by this module so the cache key naming is in exactly one place.
# Mirror functions exist on brickowl_client and bricklink_client. (B9/B10/H8)

async def invalidate_listing(element_id: str) -> None:
    """Drop any cached LEGO.com search result for `element_id`.

    Idempotent. Safe to call even if no cache entry exists. Used by the
    Saga stockout-retry path to force a fresh fetch on the next quote.
    """
    await cache_delete(f"lego_raw:{element_id}")


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
    Load the cached LEGO.com session, upload items to Pick-a-Brick,
    and complete checkout. Returns the LEGO.com order confirmation number.

    Requires:
      - migration 0003 applied (external_sessions table exists)
      - a seeded row in external_sessions for provider='lego' (run
        `python -m scripts.seed_lego_session` locally to seed)
      - a saved payment method on LAIGO's LEGO.com account
      - `playwright install chromium` run once after pip install

    Raises:
      LegoSessionExpiredError - cached session is missing ('not_seeded') OR
        no longer accepted by LEGO.com ('cookie_expired'). Saga catches this
        and writes MANUAL_REVIEW with reason='lego_session_expired'.
      StockoutError - LEGO flagged a piece as out of stock at order time.
        Saga catches this and writes COMPENSATED (no retry — see B8/H9).
      RuntimeError - any other failure (DOM selector change, network, etc.).
        Saga catches this generically and routes to _compensate.
    """
    storage_state = await lego_session_store.load_storage_state()
    if storage_state is None:
        # Fail loud BEFORE launching the browser. Saving the browser-start
        # cost on a known-broken path also keeps the failure mode crisp:
        # MANUAL_REVIEW with reason='lego_session_expired' / detail='not_seeded'.
        raise LegoSessionExpiredError("not_seeded")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            storage_state=storage_state,
        )
        page: Page = await context.new_page()
        try:
            return await _run_checkout(page, items, job_id)
        except LegoSessionExpiredError:
            # Cached state loaded but LEGO bounced us to /profile/login on
            # the session-probe step. Screenshot for operator diagnosis;
            # saga writes MANUAL_REVIEW; operator re-seeds.
            await _screenshot(page, job_id, "ERROR_lego_session_expired")
            raise
        except StockoutError:
            # B33: preserve StockoutError so saga's B8/H9 branch can match.
            # Wrapping as RuntimeError silently defeats the documented contract
            # that LEGO stockouts get the "no retry; compensate cleanly" path
            # rather than the generic "LEGO.com order failed" path.
            await _screenshot(page, job_id, "ERROR_lego_stockout")
            raise
        except Exception as exc:
            await _screenshot(page, job_id, "ERROR_final_state")
            raise RuntimeError(f"LEGO.com order failed: {exc}") from exc
        finally:
            await browser.close()


async def _run_checkout(page: Page, items: list[dict], job_id: str) -> str:
    # ── Step A: Verify cached session still works ────────────────────────────
    # Navigate to /profile (a logged-in-only page). If LEGO accepted the cached
    # cookies, we stay on /profile/<something>. If they're expired, LEGO
    # redirects us to /profile/login and we fail loud with cookie_expired.
    # This probe is cheap (~1 navigation) and isolates "session dead" from
    # "DOM selector changed" — without it, an expired session presents as a
    # generic selector-not-found error and routes through the wrong saga branch.
    logger.info(f"[lego_client] [{job_id}] verifying cached session")
    await page.goto(_PROFILE_URL, timeout=_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)
    if "/login" in page.url:
        logger.warning(
            f"[lego_client] [{job_id}] session probe redirected to "
            f"{page.url!r} — cached storage_state is no longer valid."
        )
        raise LegoSessionExpiredError("cookie_expired")
    await _screenshot(page, job_id, "01_session_ok")

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
