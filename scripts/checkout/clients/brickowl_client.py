"""
Async BrickOwl API wrapper.

Auth: query-string parameter `key=<BRICKOWL_API_KEY>` on all requests.

Getting listings for a LEGO element ID is a TWO-STEP process:
  1. catalog/id_lookup  — convert LEGO element ID (e.g. 302421) → BrickOwl BOID
  2. catalog/availability — fetch all lots for sale for that BOID

NOTE: catalog/availability requires special API permission ("granted on a case by
case basis"). Contact BrickOwl at https://www.brickowl.com/contact to request access.
Without it, this client returns empty listings and all pieces fall through to other sources.

NOTE on ordering: BrickOwl's public API has NO order/create endpoint for buyers.
The API is designed for store operators (sellers). Order placement must be handled
separately — see docs/ORDER_OPTIMIZER.md → Ordering Strategy.

Rate limits: 600 req/min standard, 100 req/min bulk.
Exponential backoff (2s base, max 3 retries) on 429 and 5xx.
"""

import os
import asyncio
import logging
import httpx
from typing import Optional

from ..models import SellerListing
from ..cache import cache_get, cache_set

logger = logging.getLogger("laigo")

SELLER_ID_PREFIX = "brickowl_"

BRICKOWL_BASE = os.environ.get("BRICKOWL_BASE_URL", "https://api.brickowl.com/v1")
_TIMEOUT = float(os.environ.get("BRICKOWL_REQUEST_TIMEOUT_SECONDS", "10"))
_CONCURRENCY_LIMIT = int(os.environ.get("BRICKOWL_CONCURRENT_REQUEST_LIMIT", "10"))
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)
    return _semaphore


def _api_key() -> str:
    key = os.environ.get("BRICKOWL_API_KEY", "")
    if not key:
        raise RuntimeError(
            "BRICKOWL_API_KEY is not set. "
            "Add the key to .env.secrets."
        )
    return key


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict | list:
    """GET with exponential backoff on 429/5xx (max 3 retries)."""
    params = {"key": _api_key(), **params}
    delay = 2.0
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with _get_semaphore():
                resp = await client.get(
                    f"{BRICKOWL_BASE}{path}", params=params, timeout=_TIMEOUT
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 2:
                    resp.raise_for_status()
                await asyncio.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                await asyncio.sleep(delay)
                delay *= 2
    raise RuntimeError(f"BrickOwl request failed after retries: {last_exc}")


# ── Step 1: element ID → BOID ─────────────────────────────────────────────────

async def get_boid_for_element(element_id: str) -> Optional[str]:
    """
    Convert a LEGO element ID to a BrickOwl BOID via catalog/id_lookup.

    Valid id_type values: item_no, design_id, bl_item_no, set_number.
    We try item_no first (LEGO element/item numbers), then without id_type
    as a broader fallback.
    """
    cache_key = f"boid:{element_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        for id_type in ["item_no", None]:
            params: dict = {"id": element_id, "type": "Part"}
            if id_type:
                params["id_type"] = id_type
            try:
                data = await _get(client, "/catalog/id_lookup", params)
                logger.debug(f"id_lookup({element_id}, id_type={id_type}): {data}")

                boid = _extract_boid(data)
                if boid:
                    await cache_set(cache_key, boid, ttl_seconds=86400)  # 24h
                    return boid
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400 and id_type is not None:
                    continue  # try next id_type
                raise

    logger.info(f"No BOID found for element {element_id} — not on BrickOwl")
    await cache_set(cache_key, None, ttl_seconds=3600)
    return None


def _extract_boid(data: dict | list) -> Optional[str]:
    """
    Pull the first BOID out of a catalog/id_lookup response.
    BrickOwl's response format is not fully documented; handle both known shapes.
    """
    raw: Optional[str] = None

    if isinstance(data, list) and data:
        first = data[0]
        for field in ("boid", "id", "owl_id"):
            if field in first and first[field]:
                raw = str(first[field])
                break
    if raw is None and isinstance(data, dict):
        for field in ("boid", "boids", "id"):
            val = data.get(field)
            if isinstance(val, list) and val:
                raw = str(val[0])
                break
            if val:
                raw = str(val)
                break

    if raw is None:
        return None

    # BOIDs from id_lookup often include a color suffix (e.g. "322944-42").
    # catalog/availability only accepts the bare numeric BOID ("322944").
    if "-" in raw:
        prefix = raw.split("-", 1)[0]
        if prefix.isdigit():
            raw = prefix

    return raw


# ── Step 2: BOID → seller listings ───────────────────────────────────────────

async def get_lots_for_boid(
    boid: str,
    shipping_country: str,
) -> list[SellerListing]:
    """
    Fetch all seller lots for a BOID via catalog/availability.

    Requires catalog/availability API permission — contact BrickOwl to request it.
    403 from this endpoint means the API key lacks access.

    Response field names are not fully documented by BrickOwl. Use the debug endpoint
    GET /checkout-debug/brickowl/element/{id}/raw to inspect live field names and
    update _parse_lot() below if listings come back empty for known-available items.
    """
    try:
        async with httpx.AsyncClient() as client:
            data = await _get(client, "/catalog/availability", {
                "boid": boid,
                "country": shipping_country,
            })
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise PermissionError(
                f"BrickOwl catalog/availability returned 403 for BOID {boid!r}. "
                "Your API key does not have access to this endpoint. "
                "Contact BrickOwl at https://www.brickowl.com/contact to request "
                "catalog/availability access."
            ) from exc
        raise

    logger.debug(f"availability(boid={boid}, country={shipping_country}): {data}")

    if isinstance(data, dict):
        lots_raw = data.get("lots", data.get("results", []))
    elif isinstance(data, list):
        lots_raw = data
    else:
        return []

    listings: list[SellerListing] = []
    for lot in lots_raw:
        try:
            listings.append(_parse_lot(lot))
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug(f"Skipping malformed lot (boid={boid}): {exc} — {lot}")

    return listings


def _parse_lot(lot: dict) -> SellerListing:
    """
    Map a BrickOwl lot dict to a SellerListing.

    Field name candidates tried in priority order. Update once the actual response
    is confirmed via the /checkout-debug/brickowl/element/{id}/raw endpoint.
    """
    def _first(*keys, default=None):
        for k in keys:
            if k in lot and lot[k] is not None:
                return lot[k]
        return default

    raw_seller_id = str(_first("store_id", "seller_id", "shop_id", default=""))
    seller_id = f"{SELLER_ID_PREFIX}{raw_seller_id}"
    seller_name = str(_first("store_name", "seller_name", "shop_name", default=f"BrickOwl Store {raw_seller_id}"))
    lot_id = str(_first("lot_id", "id", default=""))

    price_raw = _first("price", "unit_price", "price_each", default=0)
    price_cents = round(float(price_raw) * 100)

    qty = int(_first("qty", "quantity", "stock_qty", default=0))

    shipping_raw = _first("shipping_cost", "shipping", default=0)
    shipping_cents = round(float(shipping_raw) * 100)

    return SellerListing(
        seller_id=seller_id,
        seller_name=seller_name,
        price_per_cent=price_cents,
        available_qty=qty,
        shipping_cost_cents=shipping_cents,
        lot_id=lot_id,
    )


# ── Combined: element ID → SellerListings ─────────────────────────────────────

async def get_listings_for_element(
    element_id: str,
    shipping_country: str,
    shipping_zip: str,       # kept for API compatibility; BrickOwl uses country only
) -> list[SellerListing]:
    """
    Full two-step lookup: element ID → BOID → seller listings.
    Returns empty list if no BOID found, no listings available, or catalog access blocked.
    """
    boid = await get_boid_for_element(element_id)
    if not boid:
        return []
    try:
        return await get_lots_for_boid(boid, shipping_country)
    except PermissionError as exc:
        logger.warning(f"BrickOwl catalog/availability blocked for element {element_id}: {exc}")
        return []


async def get_all_listings(
    order_items: list[dict],
    shipping_country: str,
    shipping_zip: str,
    cache_ttl: int = 3600,
) -> dict[str, list[SellerListing]]:
    """
    Fetch listings for all pieces concurrently, using the TTL cache.
    Returns {element_id: [SellerListing, ...]}.
    """
    results: dict[str, list[SellerListing]] = {}
    to_fetch: list[str] = []

    for item in order_items:
        eid = item["elementId"]
        cached = await cache_get(f"brickowl_listings:{eid}")
        if cached is not None:
            results[eid] = cached
        else:
            to_fetch.append(eid)

    async def _fetch(eid: str) -> None:
        listings = await get_listings_for_element(eid, shipping_country, shipping_zip)
        await cache_set(f"brickowl_listings:{eid}", listings, cache_ttl)
        results[eid] = listings

    await asyncio.gather(*[_fetch(eid) for eid in to_fetch])
    return results


# ── Raw lookup for debugging ──────────────────────────────────────────────────

async def raw_id_lookup(element_id: str, id_type: Optional[str] = "item_no") -> dict | list:
    """Return the unprocessed catalog/id_lookup response. Used by the debug endpoint."""
    params: dict = {"id": element_id, "type": "Part"}
    if id_type:
        params["id_type"] = id_type
    async with httpx.AsyncClient() as client:
        return await _get(client, "/catalog/id_lookup", params)


async def raw_availability(boid: str, shipping_country: str) -> dict | list:
    """Return the unprocessed catalog/availability response. Used by the debug endpoint."""
    async with httpx.AsyncClient() as client:
        return await _get(client, "/catalog/availability", {
            "boid": boid,
            "country": shipping_country,
        })


# ── Order placement (NOT SUPPORTED via API) ───────────────────────────────────
#
# BrickOwl's public API is a SELLER API. There is no order/create or order/cancel
# endpoint for buyers. Both functions below are stubs.
# See docs/ORDER_OPTIMIZER.md → Ordering Strategy for alternatives.

async def create_order(
    seller_id: str,
    items: dict[str, int],
    shipping_method_id: str = "standard",
) -> str:
    raise NotImplementedError(
        "BrickOwl order placement is not yet implemented. "
        "The BrickOwl API provides no buyer-side order/create endpoint. "
        "See docs/ORDER_OPTIMIZER.md → Ordering Strategy for options."
    )


async def cancel_order(brickowl_order_id: str) -> None:
    logger.warning(
        f"cancel_order({brickowl_order_id}) called — BrickOwl has no API order/cancel "
        "endpoint. Cancel manually via the BrickOwl seller portal if needed."
    )
