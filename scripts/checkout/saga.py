"""
Saga orchestrator for the checkout pipeline.

State machine:
  initiated → stripe_held → orders_placed → payment_captured
  Any step can transition to → failed (with compensation attempted)

Checkpoints are written to outputs/{job_id}/checkout_state.json after each step
so status polling always reflects the latest progress. If the server crashes
mid-saga, the operator can read the state file to determine what compensation
is needed (see docs/ORDER_OPTIMIZER.md → Crash Recovery).

Routing logic:
  seller_id="lego_official"  → lego_client.order_from_lego (Playwright)
  seller_id="brickowl_*"     → brickowl_client.create_order (pending API access)
  seller_id="bricklink_*"    → bricklink_client (pending setup)

LOCK NOTE: checkout_store.update() holds an asyncio.Lock internally. Never call
checkout_store.load() or checkout_store.update() while already inside an
update() call — asyncio.Lock is NOT reentrant and will deadlock.
"""

import asyncio
import logging
from datetime import datetime, timezone

from .models import AllocationResult, SagaStatus, StockoutError
from . import checkout_store, stripe_client
from .clients import lego_client, brickowl_client
from .cache import cache_delete

logger = logging.getLogger("laigo")

_LEGO_SELLER_ID = lego_client.SELLER_ID


async def _compensate(job_id: str) -> None:
    """
    Cancel all placed BrickOwl orders and release the Stripe hold.
    LEGO.com orders cannot be cancelled via API — logged for manual action.
    Best-effort: logs individual failures but continues through the list.
    """
    state = await checkout_store.load(job_id) or {}

    if state.get("lego_order_id"):
        logger.warning(
            f"[saga] [{job_id}] LEGO.com order {state['lego_order_id']} was placed and "
            "cannot be cancelled via API. Cancel manually at lego.com/profile/orders."
        )

    for order_id in reversed(state.get("brickowl_order_ids", [])):
        await brickowl_client.cancel_order(order_id)

    intent_id = state.get("stripe_payment_intent_id")
    if intent_id:
        try:
            await stripe_client.cancel_payment_hold(intent_id)
        except NotImplementedError:
            pass  # Stripe not yet enabled — no hold to cancel
        except Exception as exc:
            logger.error(f"[saga] [{job_id}] Stripe cancel failed: {exc}")

    await checkout_store.update(job_id, {"saga_status": SagaStatus.COMPENSATED})


async def execute_checkout_saga(
    job_id: str,
    checkout_id: str,
    allocation: AllocationResult,
    payment_method_id: str,
    max_stockout_retries: int = 2,
) -> None:
    """
    Full checkout Saga. Runs as a background asyncio task.
    Checkpoints state after each major step.
    """
    import os
    from .optimizer import optimize

    await checkout_store.update(job_id, {
        "checkout_id": checkout_id,
        "saga_status": SagaStatus.INITIATED,
        "brickowl_order_ids": [],
        "lego_order_id": None,
        "stripe_payment_intent_id": None,
        "total_charged_cents": None,
        "error": None,
        "completed_at": None,
    })

    # ── Step 1: Stripe payment hold ───────────────────────────────────────────
    intent_id: str | None = None
    try:
        intent_id = await stripe_client.create_payment_hold(
            amount_cents=allocation.customer_total_cents,
            currency=os.environ.get("STRIPE_CURRENCY", "usd"),
            payment_method_id=payment_method_id,
            idempotency_key=f"hold-{checkout_id}",
        )
        await checkout_store.update(job_id, {
            "stripe_payment_intent_id": intent_id,
            "saga_status": SagaStatus.STRIPE_HELD,
        })
    except NotImplementedError:
        logger.warning(f"[saga] [{checkout_id}] Stripe not configured — skipping payment hold")
        await checkout_store.update(job_id, {"saga_status": SagaStatus.STRIPE_HELD})
    except Exception as exc:
        await checkout_store.update(job_id, {
            "saga_status": SagaStatus.FAILED,
            "error": f"Stripe hold failed: {exc}",
        })
        return

    # ── Step 2: Place orders per seller ──────────────────────────────────────
    current_allocation = allocation
    retries_left = max_stockout_retries

    while True:
        placed_brickowl_ids: list[str] = []
        lego_order_id: str | None = None
        stockout_eid: str | None = None
        order_failed = False

        # ── BrickOwl sellers ─────────────────────────────────────────────────
        brickowl_entries = [
            e for e in current_allocation.seller_allocations
            if e.seller_id.startswith(brickowl_client.SELLER_ID_PREFIX)
        ]
        for entry in brickowl_entries:
            try:
                order_id = await brickowl_client.create_order(
                    seller_id=entry.seller_id,
                    items=entry.items,
                )
                placed_brickowl_ids.append(order_id)
                await checkout_store.update(job_id, {"brickowl_order_ids": list(placed_brickowl_ids)})
            except StockoutError as e:
                stockout_eid = e.element_id
                break
            except Exception as exc:
                await checkout_store.update(job_id, {"brickowl_order_ids": list(placed_brickowl_ids)})
                await _compensate(job_id)
                await checkout_store.update(job_id, {
                    "saga_status": SagaStatus.FAILED,
                    "error": f"BrickOwl order failed: {exc}",
                })
                return

        if stockout_eid:
            if retries_left == 0:
                await _compensate(job_id)
                await checkout_store.update(job_id, {
                    "saga_status": SagaStatus.FAILED,
                    "error": f"Stockout on '{stockout_eid}' after {max_stockout_retries} retries",
                })
                return

            for oid in reversed(placed_brickowl_ids):
                await brickowl_client.cancel_order(oid)
            await checkout_store.update(job_id, {"brickowl_order_ids": []})
            await cache_delete(f"brickowl_listings:{stockout_eid}")

            state = await checkout_store.load(job_id) or {}
            order_items = checkout_store.read_order_list(job_id)

            from .clients import bricklink_client
            from .optimizer import merge_listings, apply_free_shipping_thresholds
            country = state.get("shipping_country", "US")
            zipp = state.get("shipping_zip", "")
            bo_listings, lego_listings, bl_listings = await asyncio.gather(
                brickowl_client.get_all_listings(order_items, country, zipp),
                lego_client.get_all_listings(order_items, country, zipp),
                bricklink_client.get_all_listings(order_items, country, zipp),
            )
            current_allocation = apply_free_shipping_thresholds(
                optimize(order_items, merge_listings(lego_listings, bo_listings, bl_listings))
            )
            retries_left -= 1
            logger.info(
                f"[saga] [{checkout_id}] stockout on '{stockout_eid}', "
                f"retrying ({retries_left} retries left)"
            )
            continue  # retry the while loop

        # ── LEGO.com primary order ────────────────────────────────────────────
        lego_entries = [
            e for e in current_allocation.seller_allocations
            if e.seller_id == _LEGO_SELLER_ID
        ]
        if lego_entries:
            lego_items: dict[str, int] = {}
            for entry in lego_entries:
                for eid, qty in entry.items.items():
                    lego_items[eid] = lego_items.get(eid, 0) + qty

            try:
                lego_order_id = await lego_client.order_from_lego(
                    items=[{"elementId": eid, "quantity": qty} for eid, qty in lego_items.items()],
                    job_id=job_id,
                )
                await checkout_store.update(job_id, {"lego_order_id": lego_order_id})
            except Exception as exc:
                await _compensate(job_id)
                await checkout_store.update(job_id, {
                    "saga_status": SagaStatus.FAILED,
                    "error": f"LEGO.com order failed: {exc}",
                })
                return

        break  # all orders placed

    await checkout_store.update(job_id, {"saga_status": SagaStatus.ORDERS_PLACED})

    # ── Step 3: Capture payment ───────────────────────────────────────────────
    if intent_id:
        try:
            await stripe_client.capture_payment(intent_id)
        except NotImplementedError:
            pass  # dev mode
        except Exception as exc:
            # Orders already placed — do NOT compensate; flag for manual review
            logger.error(
                f"[saga] [{checkout_id}] Stripe capture failed AFTER orders placed: {exc}. "
                "Manual intervention required."
            )
            await checkout_store.update(job_id, {
                "saga_status": SagaStatus.FAILED,
                "error": (
                    f"Stripe capture failed after orders were placed: {exc}. "
                    "Orders ARE placed. Manual review required."
                ),
            })
            return

    await checkout_store.update(job_id, {
        "saga_status": SagaStatus.PAYMENT_CAPTURED,
        "total_charged_cents": current_allocation.customer_total_cents,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info(
        f"[saga] [{checkout_id}] complete — "
        f"charged {current_allocation.customer_total_cents} cents"
    )
