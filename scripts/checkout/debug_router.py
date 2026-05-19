"""
Debug/testing endpoints for the checkout pipeline.

All routes are exposed in the Swagger UI at /docs. Use them to verify BrickOwl
API connectivity, LEGO.com availability, and optimizer output before running
a real checkout.

Register in Main.py:
  from checkout.debug_router import debug_router
  app.include_router(debug_router)

Endpoints:
  GET  /checkout-debug/brickowl/element/{element_id}   — single element BrickOwl listings
  POST /checkout-debug/brickowl/elements               — batch BrickOwl listings
  GET  /checkout-debug/lego/element/{element_id}       — single element LEGO.com availability
  POST /checkout-debug/lego/elements                   — batch LEGO.com availability
  GET  /checkout-debug/job/{job_id}/order-list         — raw order_list.json for a completed job
  POST /checkout-debug/job/{job_id}/optimize           — full optimization preview (no orders placed)
"""

import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .models import SellerListing, SellerAllocationResponse
from . import checkout_store_dispatch as checkout_store
from .clients import brickowl_client, lego_client, bricklink_client
from .clients.brickowl_client import raw_id_lookup, raw_availability, get_boid_for_element
from .clients.lego_client import check_element_available, check_elements_available
from .optimizer import optimize, apply_free_shipping_thresholds

logger = logging.getLogger("laigo")
debug_router = APIRouter(prefix="/checkout-debug", tags=["checkout-debug"])


# ── Request / response models for debug endpoints ────────────────────────────

class BatchListingsRequest(BaseModel):
    element_ids: list[str] = Field(..., min_length=1, max_length=100,
                                   description="LEGO element IDs to look up")
    shipping_country: str = Field("US", min_length=2, max_length=2,
                                  description="ISO 3166-1 alpha-2 country code")
    shipping_zip: str = Field("90210", min_length=1, max_length=20,
                              description="ZIP/postal code for shipping cost")


class ElementListingsResponse(BaseModel):
    element_id: str
    listing_count: int
    cheapest_price_cents: Optional[int]
    most_stock: Optional[int]
    listings: list[SellerListing]


class BatchListingsResponse(BaseModel):
    requested: int
    found: int                             # elements with at least one listing
    not_found: list[str]                   # element IDs with no BrickOwl listings
    results: dict[str, ElementListingsResponse]


class LegoAvailabilityResponse(BaseModel):
    element_id: str
    available_on_lego: bool


class LegoAvailabilityBatchRequest(BaseModel):
    element_ids: list[str] = Field(..., min_length=1, max_length=100)


class LegoAvailabilityBatchResponse(BaseModel):
    available: list[str]
    unavailable: list[str]
    results: dict[str, bool]


class OptimizePreviewRequest(BaseModel):
    shipping_country: str = Field("US", min_length=2, max_length=2)
    shipping_zip: str = Field("90210", min_length=1, max_length=20)


class OptimizePreviewResponse(BaseModel):
    job_id: str
    total_items: int
    sellers: list[SellerAllocationResponse]
    lego_fallback_items: list[dict]
    lego_fallback_item_count: int
    unsourceable_items: list[dict]
    unsourceable_count: int
    can_proceed: bool
    total_piece_cost_cents: int
    total_shipping_cents: int
    grand_total_cents: int
    laigo_fee_cents: int
    customer_total_cents: int


# ── BrickOwl endpoints ────────────────────────────────────────────────────────

@debug_router.get(
    "/brickowl/element/{element_id}/raw",
    summary="BrickOwl: raw API responses for one element (use this first to verify field names)",
    description=(
        "Returns the unprocessed JSON from both BrickOwl API steps: "
        "1) catalog/id_lookup (element ID → BOID) and "
        "2) catalog/availability (BOID → lots). "
        "Use this endpoint to see the actual field names in BrickOwl's responses "
        "and verify that brickowl_client._parse_lot() is mapping them correctly. "
        "If listings are empty despite this showing lots, update _parse_lot() in "
        "brickowl_client.py with the correct field names from step2_availability."
    ),
)
async def debug_brickowl_element_raw(
    element_id: str,
    shipping_country: str = Query("US", min_length=2, max_length=2),
    id_type: str = Query("item_no", description="id_type for catalog/id_lookup: item_no, design_id, bl_item_no, set_number"),
):
    result: dict = {
        "element_id": element_id,
        "step1_id_lookup": None,
        "step1_error": None,
        "boid_extracted": None,
        "step2_availability": None,
        "step2_error": None,
    }

    try:
        raw_lookup = await raw_id_lookup(element_id, id_type)
        result["step1_id_lookup"] = raw_lookup
    except Exception as exc:
        result["step1_error"] = str(exc)
        return result

    boid = await get_boid_for_element(element_id)
    result["boid_extracted"] = boid

    if boid:
        try:
            raw_avail = await raw_availability(boid, shipping_country)
            result["step2_availability"] = raw_avail
        except Exception as exc:
            result["step2_error"] = str(exc)

    return result


@debug_router.get(
    "/brickowl/element/{element_id}",
    response_model=ElementListingsResponse,
    summary="BrickOwl: listings for one element",
    description=(
        "Fetch all BrickOwl seller listings for a single LEGO element ID. "
        "Use this to verify BrickOwl API connectivity and inspect price/stock data. "
        "Results are cached for BRICKOWL_CACHE_TTL_SECONDS (default 1 hour)."
    ),
)
async def debug_brickowl_element(
    element_id: str,
    shipping_country: str = Query("US", min_length=2, max_length=2,
                                  description="ISO 3166-1 alpha-2 country code"),
    shipping_zip: str = Query("90210", min_length=1, max_length=20,
                              description="ZIP/postal code for shipping estimate"),
):
    try:
        listings = await brickowl_client.get_listings_for_element(
            element_id=element_id,
            shipping_country=shipping_country,
            shipping_zip=shipping_zip,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"BrickOwl API error: {exc}")

    return ElementListingsResponse(
        element_id=element_id,
        listing_count=len(listings),
        cheapest_price_cents=min((l.price_per_cent for l in listings), default=None),
        most_stock=max((l.available_qty for l in listings), default=None),
        listings=listings,
    )


@debug_router.post(
    "/brickowl/elements",
    response_model=BatchListingsResponse,
    summary="BrickOwl: listings for multiple elements",
    description=(
        "Fetch BrickOwl listings for up to 100 element IDs concurrently. "
        "Elements with no seller listings appear in not_found. "
        "Useful for checking what fraction of an order list is sourceable on BrickOwl."
    ),
)
async def debug_brickowl_elements(body: BatchListingsRequest):
    cache_ttl = int(os.environ.get("BRICKOWL_CACHE_TTL_SECONDS", "3600"))
    try:
        listings_map = await brickowl_client.get_all_listings(
            order_items=[{"elementId": eid} for eid in body.element_ids],
            shipping_country=body.shipping_country,
            shipping_zip=body.shipping_zip,
            cache_ttl=cache_ttl,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"BrickOwl API error: {exc}")

    results = {}
    not_found = []
    for eid in body.element_ids:
        lst = listings_map.get(eid, [])
        if not lst:
            not_found.append(eid)
        results[eid] = ElementListingsResponse(
            element_id=eid,
            listing_count=len(lst),
            cheapest_price_cents=min((l.price_per_cent for l in lst), default=None),
            most_stock=max((l.available_qty for l in lst), default=None),
            listings=lst,
        )

    return BatchListingsResponse(
        requested=len(body.element_ids),
        found=len(body.element_ids) - len(not_found),
        not_found=not_found,
        results=results,
    )


# ── LEGO.com availability endpoints ──────────────────────────────────────────

@debug_router.get(
    "/lego/element/{element_id}",
    response_model=LegoAvailabilityResponse,
    summary="LEGO.com: availability for one element",
    description=(
        "Check whether a LEGO element ID is currently in stock on Pick-a-Brick. "
        "Results are cached for 30 minutes. "
        "If the check consistently returns False for known-available pieces, "
        "inspect the LEGO.com search API and update lego_availability.py."
    ),
)
async def debug_lego_element(element_id: str):
    available = await check_element_available(element_id)
    return LegoAvailabilityResponse(element_id=element_id, available_on_lego=available)


@debug_router.post(
    "/lego/elements",
    response_model=LegoAvailabilityBatchResponse,
    summary="LEGO.com: availability for multiple elements",
    description=(
        "Check LEGO.com Pick-a-Brick availability for up to 100 element IDs concurrently. "
        "Elements in 'unavailable' will block a checkout confirm if they appear as "
        "lego_fallback_items in the optimizer output."
    ),
)
async def debug_lego_elements(body: LegoAvailabilityBatchRequest):
    availability = await check_elements_available(body.element_ids)
    available = [eid for eid, ok in availability.items() if ok]
    unavailable = [eid for eid, ok in availability.items() if not ok]
    return LegoAvailabilityBatchResponse(
        available=available,
        unavailable=unavailable,
        results=availability,
    )


# ── Job-level debug endpoints ────────────────────────────────────────────────

@debug_router.get(
    "/job/{job_id}/order-list",
    summary="Raw order list for a completed job",
    description=(
        "Returns the order_list.json content for a completed job. "
        "The file lives at outputs/{job_id}/order_list.json after the workspace is cleaned up."
    ),
)
async def debug_order_list(job_id: str):
    try:
        items = checkout_store.read_order_list(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "job_id": job_id,
        "item_count": len(items),
        "total_pieces": sum(i.get("quantity", 0) for i in items),
        "items": items,
    }


@debug_router.post(
    "/job/{job_id}/optimize",
    response_model=OptimizePreviewResponse,
    summary="Full optimization preview for a job",
    description=(
        "Runs the full optimization pipeline — fetches BrickOwl listings, runs the "
        "greedy optimizer, checks LEGO.com availability for fallback items — and returns "
        "a detailed cost breakdown. No orders are placed and no payment is taken. "
        "Use this to preview what /quote will return before the customer sees it."
    ),
)
async def debug_optimize(job_id: str, body: OptimizePreviewRequest):
    try:
        order_items = checkout_store.read_order_list(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not order_items:
        raise HTTPException(status_code=422, detail="Order list is empty")

    cache_ttl = int(os.environ.get("BRICKOWL_CACHE_TTL_SECONDS", "3600"))
    try:
        # B37: match the saga's quote flow exactly — include BrickLink (today
        # returns empty unless BRICKLINK_ENABLED=true, but kept in for parity
        # so operators previewing /quote totals see the same allocation the
        # customer would).
        lego_listings, brickowl_listings, bricklink_listings = await asyncio.gather(
            lego_client.get_all_listings(
                order_items=order_items,
                shipping_country=body.shipping_country,
                shipping_zip=body.shipping_zip,
                cache_ttl=cache_ttl,
            ),
            brickowl_client.get_all_listings(
                order_items=order_items,
                shipping_country=body.shipping_country,
                shipping_zip=body.shipping_zip,
                cache_ttl=cache_ttl,
            ),
            bricklink_client.get_all_listings(
                order_items=order_items,
                shipping_country=body.shipping_country,
                shipping_zip=body.shipping_zip,
                cache_ttl=cache_ttl,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Listings fetch error: {exc}")

    from .optimizer import merge_listings
    listings = merge_listings(lego_listings, brickowl_listings, bricklink_listings)
    # B37: apply_free_shipping_thresholds mirrors the saga's quote flow so the
    # preview totals match what the customer would see.
    allocation = apply_free_shipping_thresholds(optimize(order_items, listings))

    # B38: with LEGO as a primary source in seller_allocations,
    # `lego_fallback_items` reflects pieces the optimizer couldn't source from
    # ANY marketplace (BrickOwl, LEGO.com, BrickLink). These ARE the
    # unsourceable items — the old separate `lego_available` distinction was
    # vestigial from the pre-LEGO-as-primary design. Report the same list in
    # both fields for backward-compat of the response shape; future cleanup
    # can collapse the duplicate field.
    unsourceable = allocation.lego_fallback_items

    sellers = [
        SellerAllocationResponse(
            seller_id=e.seller_id,
            seller_name=e.seller_name,
            pieces_count=sum(e.items.values()),
            piece_cost_cents=e.piece_cost_cents,
            shipping_cost_cents=e.shipping_cost_cents,
            subtotal_cents=e.subtotal_cents,
        )
        for e in allocation.seller_allocations
    ]

    return OptimizePreviewResponse(
        job_id=job_id,
        total_items=sum(i["quantity"] for i in order_items),
        sellers=sellers,
        lego_fallback_items=unsourceable,
        lego_fallback_item_count=len(unsourceable),
        unsourceable_items=unsourceable,
        unsourceable_count=len(unsourceable),
        can_proceed=len(unsourceable) == 0,
        total_piece_cost_cents=allocation.total_piece_cost_cents,
        total_shipping_cents=allocation.total_shipping_cents,
        grand_total_cents=allocation.grand_total_cents,
        laigo_fee_cents=allocation.laigo_fee_cents,
        customer_total_cents=allocation.customer_total_cents,
    )


# ── LEGO.com listing debug endpoints ─────────────────────────────────────────

@debug_router.get(
    "/lego/element/{element_id}/listing",
    summary="LEGO.com: raw listing data for one element",
    description=(
        "Returns the parsed SellerListing for a single element from LEGO.com Pick-a-Brick, "
        "including the price extracted from LEGO.com's internal search API. "
        "If price shows as 0, update _parse_price_cents() in clients/lego_client.py "
        "with the correct field names from the raw API response."
    ),
)
async def debug_lego_listing(element_id: str):
    listing = await lego_client.get_listing_for_element(element_id, "US")
    if listing is None:
        return {"element_id": element_id, "available": False, "listing": None}
    return {"element_id": element_id, "available": True, "listing": listing}
