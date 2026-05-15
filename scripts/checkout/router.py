"""
FastAPI router for the checkout pipeline.

Endpoints:
  POST /jobs/{job_id}/checkout/quote
      Fetches live listings from all active sources, runs the optimizer, and returns
      a priced quote. Quote cached for 600 seconds. Target: <20s for a full order list.

  POST /jobs/{job_id}/checkout/confirm
      Accepts a quote + Stripe payment method, launches the checkout Saga as a
      background task. Blocked (422) if the quote contains any unsourceable items.

  GET  /jobs/{job_id}/checkout/{checkout_id}/status
      Returns current Saga state from the checkout_state.json checkpoint file.

Active sources (MVP):
  - LEGO.com Pick-a-Brick (lego_client) — primary source for all pieces

Pending sources (enabled when API access is confirmed):
  - BrickOwl (brickowl_client) — requires catalog/availability API permission
  - BrickLink (bricklink_client) — requires seller account + API setup

Register in Main.py:
  from checkout.router import checkout_router
  app.include_router(checkout_router, prefix="/jobs")
"""

import asyncio
import logging
import os
import secrets
import time

from fastapi import APIRouter, HTTPException

from .models import (
    QuoteRequest, QuoteResponse, SellerAllocationResponse,
    ConfirmRequest, ConfirmResponse,
    CheckoutStatusResponse, AllocationResult,
)
from . import checkout_store, saga as saga_module
from .clients import lego_client, brickowl_client, bricklink_client
from .optimizer import optimize, merge_listings, apply_free_shipping_thresholds
from .cache import cache_get, cache_set

logger = logging.getLogger("laigo")
checkout_router = APIRouter(tags=["checkout"])


@checkout_router.post("/{job_id}/checkout/quote", response_model=QuoteResponse)
async def get_quote(job_id: str, body: QuoteRequest):
    """
    Return an optimized price quote for all pieces in a completed job.

    Flow:
      1. Read order_list.json for the job
      2. Fetch live listings from all active sources concurrently
      3. Run two-pass greedy optimizer to minimize piece cost + shipping
      4. Items with no listings from any source → unsourceable (blocks confirm)
    """
    try:
        order_items = checkout_store.read_order_list(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not order_items:
        raise HTTPException(status_code=422, detail="Order list is empty")

    cache_ttl = int(os.environ.get("BRICKOWL_CACHE_TTL_SECONDS", "3600"))

    # Fetch from all active sources concurrently.
    # BrickOwl and BrickLink return empty dicts until their API access is configured.
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
        return_exceptions=False,
    )

    all_listings = merge_listings(lego_listings, brickowl_listings, bricklink_listings)
    allocation = optimize(order_items, all_listings)
    allocation = apply_free_shipping_thresholds(allocation)

    # Items the optimizer couldn't fill from any source are unsourceable.
    # In MVP (LEGO.com only), these are pieces not on LEGO.com Pick-a-Brick.
    # The optimizer puts them in lego_fallback_items; once BrickOwl/BrickLink are
    # active this field will shrink as more sources are available.
    unsourceable = allocation.lego_fallback_items

    if unsourceable:
        logger.info(
            f"Quote for job {job_id}: {len(unsourceable)} unsourceable piece(s): "
            + ", ".join(i["elementId"] for i in unsourceable)
        )

    checkout_id = f"co_{secrets.token_hex(8)}"
    expires_at = int(time.time()) + 600

    # Rebuild allocation with empty lego_fallback_items — unsourceable items are
    # tracked separately in the cached quote and never passed to the saga.
    allocation = allocation.model_copy(update={"lego_fallback_items": []})

    await cache_set(f"quote:{checkout_id}", {
        "allocation": allocation.model_dump(),
        "unsourceable_items": unsourceable,
        "shipping_country": body.shipping_country,
        "shipping_zip": body.shipping_zip,
        "customer_email": body.customer_email,
        "job_id": job_id,
    }, ttl_seconds=600)

    return QuoteResponse(
        checkout_id=checkout_id,
        expires_at=expires_at,
        pieces_total=sum(i["quantity"] for i in order_items),
        sellers=[
            SellerAllocationResponse(
                seller_id=e.seller_id,
                seller_name=e.seller_name,
                pieces_count=sum(e.items.values()),
                piece_cost_cents=e.piece_cost_cents,
                shipping_cost_cents=e.shipping_cost_cents,
                subtotal_cents=e.subtotal_cents,
            )
            for e in allocation.seller_allocations
        ],
        lego_fallback_items=[],
        lego_fallback_cost_cents=0,
        unsourceable_items=unsourceable,
        can_proceed=len(unsourceable) == 0,
        total_cost_cents=allocation.grand_total_cents,
        laigo_service_fee_cents=allocation.laigo_fee_cents,
        grand_total_cents=allocation.customer_total_cents,
    )


@checkout_router.post("/{job_id}/checkout/confirm", response_model=ConfirmResponse)
async def confirm_checkout(job_id: str, body: ConfirmRequest):
    """
    Confirm a quote and begin the checkout Saga.

    Returns 422 immediately if the quote has unsourceable pieces.
    The Saga runs in the background; poll /status for progress.
    """
    cached = await cache_get(f"quote:{body.checkout_id}")
    if cached is None:
        raise HTTPException(
            status_code=409,
            detail="Quote expired or not found. Please call /quote again."
        )
    if cached["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="checkout_id does not belong to this job")

    unsourceable = cached.get("unsourceable_items", [])
    if unsourceable:
        raise HTTPException(
            status_code=422,
            detail={
                "error": (
                    "Cannot confirm: one or more pieces are unavailable on all sourcing "
                    "platforms. Remove or substitute these pieces and regenerate the mosaic."
                ),
                "unsourceable_items": unsourceable,
            },
        )

    existing = await checkout_store.load(job_id)
    if existing and existing.get("checkout_id") == body.checkout_id:
        raise HTTPException(status_code=409, detail="This checkout has already been confirmed")

    allocation = AllocationResult(**cached["allocation"])
    max_retries = int(os.environ.get("OPTIMIZER_MAX_STOCKOUT_RETRIES", "2"))

    await checkout_store.save(job_id, {
        "checkout_id": body.checkout_id,
        "shipping_country": cached["shipping_country"],
        "shipping_zip": cached["shipping_zip"],
        "customer_email": cached["customer_email"],
        "saga_status": "pending",
        "brickowl_order_ids": [],
        "lego_order_id": None,
        "stripe_payment_intent_id": None,
        "total_charged_cents": None,
        "error": None,
        "completed_at": None,
    })

    asyncio.create_task(saga_module.execute_checkout_saga(
        job_id=job_id,
        checkout_id=body.checkout_id,
        allocation=allocation,
        payment_method_id=body.stripe_payment_method_id,
        max_stockout_retries=max_retries,
    ))

    return ConfirmResponse(
        checkout_id=body.checkout_id,
        saga_status="initiated",
        poll_url=f"/jobs/{job_id}/checkout/{body.checkout_id}/status",
    )


@checkout_router.get(
    "/{job_id}/checkout/{checkout_id}/status",
    response_model=CheckoutStatusResponse,
)
async def get_checkout_status(job_id: str, checkout_id: str):
    """Poll Saga progress. Call every 2–5 seconds until saga_status is terminal."""
    state = await checkout_store.load(job_id)
    if state is None or state.get("checkout_id") != checkout_id:
        raise HTTPException(status_code=404, detail="Checkout not found")

    return CheckoutStatusResponse(
        checkout_id=checkout_id,
        saga_status=state["saga_status"],
        brickowl_order_ids=state.get("brickowl_order_ids", []),
        lego_order_id=state.get("lego_order_id"),
        stripe_payment_intent_id=state.get("stripe_payment_intent_id"),
        total_charged_cents=state.get("total_charged_cents"),
        error=state.get("error"),
        completed_at=state.get("completed_at"),
    )
