"""
Two-pass greedy optimizer for LEGO piece allocation across multiple sourcing clients.

Pass 1: Assign each piece to the cheapest available seller. Existing sellers
        (those already chosen for prior pieces) are preferred to avoid adding
        extra shipping costs.

Pass 2: Consolidation — for each seller B, check if all of B's pieces can be
        moved to another seller A such that the extra unit-price cost is less
        than B's flat shipping fee. If so, merge B into A and save B's shipping.

This is a pure function — no I/O, no side effects, fully unit-testable.
"""

import os
from collections import defaultdict
from .models import SellerListing, AllocationEntry, AllocationResult


def merge_listings(
    *sources: dict[str, list[SellerListing]],
) -> dict[str, list[SellerListing]]:
    """
    Combine per-element listing dicts from multiple sourcing clients.
    Each source is {element_id: [SellerListing, ...]}. Listings for the same
    element are concatenated; the optimizer then chooses the cheapest across all.
    """
    merged: dict[str, list[SellerListing]] = {}
    for source in sources:
        for eid, listings in source.items():
            merged.setdefault(eid, []).extend(listings)
    return merged


def compute_laigo_fee(grand_total_cents: int) -> int:
    min_fee = int(os.environ.get("LAIGO_FEE_MIN_CENTS", "300"))
    pct = float(os.environ.get("LAIGO_FEE_PERCENT", "5")) / 100
    return max(min_fee, int(grand_total_cents * pct))


def optimize(
    order_items: list[dict],                      # [{elementId, quantity}, ...]
    listings: dict[str, list[SellerListing]],     # elementId → [SellerListing, ...]
) -> AllocationResult:
    """
    Allocate order_items across BrickOwl sellers to minimize total cost
    (piece prices + per-seller flat shipping).

    Items with no BrickOwl listings are routed to lego_fallback_items.
    Partial fulfillment (seller runs out mid-fill) also falls back to LEGO.com.
    """

    # ── Separate unavailable items ────────────────────────────────────────────
    lego_fallback_items: list[dict] = []
    available_items: list[dict] = []

    for item in order_items:
        eid = item["elementId"]
        if eid not in listings or not listings[eid]:
            lego_fallback_items.append({"elementId": eid, "quantity": item["quantity"]})
        else:
            available_items.append({"elementId": eid, "quantity": item["quantity"]})

    # ── Pass 1: Greedy allocation ─────────────────────────────────────────────
    # Sort by descending (quantity × cheapest_unit_price) so high-value pieces
    # are committed first, giving the consolidation pass the most savings potential.
    def _piece_value(item: dict) -> int:
        return item["quantity"] * min(l.price_per_cent for l in listings[item["elementId"]])

    available_items.sort(key=_piece_value, reverse=True)

    # allocation[seller_id][element_id] = quantity assigned to this seller
    allocation: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seller_piece_cost: dict[str, int] = defaultdict(int)   # cumulative piece cost per seller
    seller_meta: dict[str, SellerListing] = {}             # representative listing per seller

    for item in available_items:
        eid = item["elementId"]
        needed = item["quantity"]

        # Prefer already-chosen sellers (tuple key: (0, price) sorts before (1, price))
        # so we consolidate shipping by default in Pass 1 as well.
        seller_lots = sorted(
            listings[eid],
            key=lambda l: (0 if l.seller_id in allocation else 1, l.price_per_cent),
        )

        for listing in seller_lots:
            if needed <= 0:
                break
            if listing.available_qty <= 0:
                continue
            sid = listing.seller_id
            # Use .get() so reading doesn't create ghost entries in the defaultdict
            # (which would corrupt the seller-preference sort for subsequent items).
            already_allocated = allocation.get(sid, {}).get(eid, 0)
            remaining_stock = listing.available_qty - already_allocated
            if remaining_stock <= 0:
                continue
            can_take = min(remaining_stock, needed)

            allocation[sid][eid] += can_take
            seller_piece_cost[sid] += can_take * listing.price_per_cent
            if sid not in seller_meta:
                seller_meta[sid] = listing
            needed -= can_take

        if needed > 0:
            lego_fallback_items.append({"elementId": eid, "quantity": needed})

    # ── Pass 2: Consolidation ─────────────────────────────────────────────────
    # For each seller B with non-zero shipping: can we move all of B's pieces to
    # another seller A for less than B's shipping cost? If so, merge B into A.
    changed = True
    while changed:
        changed = False
        for b_sid in list(allocation.keys()):
            b_shipping = seller_meta[b_sid].shipping_cost_cents
            if b_shipping == 0:
                continue  # free shipping — no gain from eliminating this seller

            best_extra_cost = None
            best_a_sid = None

            for a_sid in list(allocation.keys()):
                if a_sid == b_sid:
                    continue

                extra_cost = 0
                feasible = True

                for eid, qty in allocation[b_sid].items():
                    a_listing = next(
                        (l for l in listings[eid] if l.seller_id == a_sid),
                        None,
                    )
                    if a_listing is None:
                        feasible = False
                        break

                    # Check that A has enough remaining stock to absorb B's qty
                    a_already = allocation[a_sid][eid]
                    a_remaining = a_listing.available_qty - a_already
                    if a_remaining < qty:
                        feasible = False
                        break

                    b_listing = next(l for l in listings[eid] if l.seller_id == b_sid)
                    extra_cost += qty * (a_listing.price_per_cent - b_listing.price_per_cent)

                if feasible and extra_cost < b_shipping:
                    if best_extra_cost is None or extra_cost < best_extra_cost:
                        best_extra_cost = extra_cost
                        best_a_sid = a_sid

            if best_a_sid is not None:
                # Merge B into A: move B's pieces to A at A's unit prices
                for eid, qty in allocation[b_sid].items():
                    a_listing = next(l for l in listings[eid] if l.seller_id == best_a_sid)
                    allocation[best_a_sid][eid] += qty
                    seller_piece_cost[best_a_sid] += qty * a_listing.price_per_cent

                del allocation[b_sid]
                del seller_piece_cost[b_sid]
                changed = True
                break  # restart — dict changed

    # ── Build result ──────────────────────────────────────────────────────────
    seller_allocations: list[AllocationEntry] = []
    total_piece_cost = 0
    total_shipping = 0

    for sid, items_dict in allocation.items():
        meta = seller_meta[sid]
        piece_cost = seller_piece_cost[sid]
        shipping = meta.shipping_cost_cents
        total_piece_cost += piece_cost
        total_shipping += shipping
        seller_allocations.append(AllocationEntry(
            seller_id=sid,
            seller_name=meta.seller_name,
            items=dict(items_dict),
            piece_cost_cents=piece_cost,
            shipping_cost_cents=shipping,
            subtotal_cents=piece_cost + shipping,
        ))

    grand_total = total_piece_cost + total_shipping
    laigo_fee = compute_laigo_fee(grand_total)

    return AllocationResult(
        seller_allocations=seller_allocations,
        lego_fallback_items=lego_fallback_items,
        lego_fallback_cost_cents=0,  # vestigial; LEGO.com pricing lives in seller_allocations
        total_piece_cost_cents=total_piece_cost,
        total_shipping_cents=total_shipping,
        grand_total_cents=grand_total,
        laigo_fee_cents=laigo_fee,
        customer_total_cents=grand_total + laigo_fee,
    )


def apply_free_shipping_thresholds(allocation: AllocationResult) -> AllocationResult:
    """
    Zero out shipping for any seller whose allocation meets their client's
    free-shipping threshold.

    Currently handles LEGO.com only: if the lego_official piece cost total
    reaches LEGO_FREE_SHIPPING_THRESHOLD_CENTS (default $35.00), shipping is
    set to $0 and all downstream totals are recalculated.

    The LAIGO fee is recalculated on the reduced grand_total so the customer
    gets the full benefit of free shipping.
    """
    from .clients.lego_client import SELLER_ID as _LEGO_ID

    threshold = int(os.environ.get("LEGO_FREE_SHIPPING_THRESHOLD_CENTS", "3500"))

    new_entries = list(allocation.seller_allocations)
    shipping_saved = 0

    for i, entry in enumerate(new_entries):
        if (entry.seller_id == _LEGO_ID
                and entry.shipping_cost_cents > 0
                and entry.piece_cost_cents >= threshold):
            shipping_saved += entry.shipping_cost_cents
            new_entries[i] = AllocationEntry(
                seller_id=entry.seller_id,
                seller_name=entry.seller_name,
                items=entry.items,
                piece_cost_cents=entry.piece_cost_cents,
                shipping_cost_cents=0,
                subtotal_cents=entry.piece_cost_cents,
            )

    if shipping_saved == 0:
        return allocation

    new_total_shipping = allocation.total_shipping_cents - shipping_saved
    new_grand_total = allocation.grand_total_cents - shipping_saved
    new_laigo_fee = compute_laigo_fee(new_grand_total)

    return allocation.model_copy(update={
        "seller_allocations": new_entries,
        "total_shipping_cents": new_total_shipping,
        "grand_total_cents": new_grand_total,
        "laigo_fee_cents": new_laigo_fee,
        "customer_total_cents": new_grand_total + new_laigo_fee,
    })
