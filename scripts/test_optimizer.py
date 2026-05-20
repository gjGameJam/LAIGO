"""Unit tests for scripts/checkout/optimizer.py.

Covers PRE_RELEASE_PAYMENT_CHECKLIST / CHECKOUT_AUDIT §6.10 items 1-7:
    1. optimize() happy-path single seller
    2. optimize() shipping consolidation (Pass 2 collapse)
    3. optimize() threshold jump (LEGO.com free-shipping promotion)
    4. optimize() stockout fall-through to lego_fallback_items
    5. optimize() 2000-piece x 15-seller perf bound (<2s)
    6. merge_listings() cross-source ordering invariants
    7. compute_laigo_fee() boundary: $3 floor at $59.99, 5% at $60.01

Pure functions — no DB, no network. Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_optimizer
"""
import os
import sys
import time


def _make_listing(seller_id, seller_name, price_per_cent, available_qty,
                  shipping_cost_cents, lot_id=""):
    from scripts.checkout.models import SellerListing
    return SellerListing(
        seller_id=seller_id,
        seller_name=seller_name,
        price_per_cent=price_per_cent,
        available_qty=available_qty,
        shipping_cost_cents=shipping_cost_cents,
        lot_id=lot_id,
    )


def test_happy_path_single_seller():
    from scripts.checkout.optimizer import optimize

    listings = {
        "3001": [_make_listing("brickowl_solo", "Solo", 10, 100, 500)],
        "3002": [_make_listing("brickowl_solo", "Solo", 20, 100, 500)],
    }
    order = [{"elementId": "3001", "quantity": 5}, {"elementId": "3002", "quantity": 3}]

    result = optimize(order, listings)

    assert len(result.seller_allocations) == 1
    entry = result.seller_allocations[0]
    assert entry.seller_id == "brickowl_solo"
    assert entry.items == {"3001": 5, "3002": 3}
    assert entry.piece_cost_cents == 5 * 10 + 3 * 20
    assert entry.shipping_cost_cents == 500
    assert entry.subtotal_cents == 110 + 500
    assert result.total_piece_cost_cents == 110
    assert result.total_shipping_cents == 500
    assert result.grand_total_cents == 610
    assert result.lego_fallback_items == []
    print("OK: happy_path_single_seller")


def test_shipping_consolidation():
    from scripts.checkout.optimizer import optimize

    listings = {
        "3001": [
            _make_listing("brickowl_a", "A", 100, 100, 500),
            _make_listing("brickowl_b", "B", 100, 100, 800),
        ],
        "3002": [
            _make_listing("brickowl_a", "A", 100, 100, 500),
            _make_listing("brickowl_b", "B", 90, 1, 800),
        ],
    }
    order = [{"elementId": "3001", "quantity": 5}, {"elementId": "3002", "quantity": 1}]

    result = optimize(order, listings)

    assert len(result.seller_allocations) == 1, (
        f"expected Pass 2 to collapse into 1 seller, got {len(result.seller_allocations)}"
    )
    only = result.seller_allocations[0]
    assert only.seller_id == "brickowl_a"
    assert only.items == {"3001": 5, "3002": 1}
    assert only.shipping_cost_cents == 500
    print("OK: shipping_consolidation (Pass 2 collapsed B into A)")


def test_threshold_jump():
    from scripts.checkout.optimizer import optimize, apply_free_shipping_thresholds
    from scripts.checkout.clients.lego_client import SELLER_ID as LEGO_ID

    os.environ["LEGO_FREE_SHIPPING_THRESHOLD_CENTS"] = "3500"

    listings = {
        "3001": [_make_listing(LEGO_ID, "LEGO.com", 400, 100, 599)],
    }

    # Below threshold: shipping retained
    below = optimize([{"elementId": "3001", "quantity": 8}], listings)  # 3200 cents
    below_after = apply_free_shipping_thresholds(below)
    assert below_after.seller_allocations[0].shipping_cost_cents == 599
    assert below_after.total_shipping_cents == 599

    # Above threshold: shipping zeroed
    above = optimize([{"elementId": "3001", "quantity": 9}], listings)  # 3600 cents
    above_after = apply_free_shipping_thresholds(above)
    assert above_after.seller_allocations[0].shipping_cost_cents == 0, (
        "free-shipping promotion should have fired at piece_cost_cents >= 3500"
    )
    assert above_after.total_shipping_cents == 0
    # Grand total should drop by 599 and laigo_fee should be recomputed.
    assert above_after.grand_total_cents == above.grand_total_cents - 599
    assert above_after.laigo_fee_cents <= above.laigo_fee_cents, (
        "laigo_fee must be recomputed on the reduced grand_total"
    )
    print("OK: threshold_jump (free-shipping promotion fires + fee recomputes)")


def test_stockout_fall_through():
    from scripts.checkout.optimizer import optimize

    listings = {
        "3001": [_make_listing("brickowl_a", "A", 10, 100, 500)],
        "3002": [],  # no listings — must route to fallback
        # 3003 not in listings dict at all — must also route to fallback
    }
    order = [
        {"elementId": "3001", "quantity": 5},
        {"elementId": "3002", "quantity": 7},
        {"elementId": "3003", "quantity": 2},
    ]

    result = optimize(order, listings)

    fallback_by_eid = {item["elementId"]: item["quantity"] for item in result.lego_fallback_items}
    assert fallback_by_eid == {"3002": 7, "3003": 2}
    assert len(result.seller_allocations) == 1
    assert result.seller_allocations[0].items == {"3001": 5}
    print("OK: stockout_fall_through (missing + empty listings both routed)")


def test_partial_fulfillment_falls_through():
    """Seller runs out of stock mid-fill: the remainder must route to fallback."""
    from scripts.checkout.optimizer import optimize

    listings = {
        "3001": [_make_listing("brickowl_a", "A", 10, 3, 500)],  # only 3 in stock
    }
    order = [{"elementId": "3001", "quantity": 10}]

    result = optimize(order, listings)

    # 3 should be allocated to A, 7 should fall through
    assert len(result.seller_allocations) == 1
    assert result.seller_allocations[0].items == {"3001": 3}
    assert result.lego_fallback_items == [{"elementId": "3001", "quantity": 7}]
    print("OK: partial_fulfillment_falls_through")


def test_perf_2000_pieces_15_sellers():
    """§6.10 perf bound: 2000 pieces × 15 sellers must complete in <2s."""
    from scripts.checkout.optimizer import optimize

    # 200 unique elements, each with 15 seller listings, 10 qty per element = 2000 pieces.
    listings = {}
    order = []
    for ei in range(200):
        eid = f"e{ei:04d}"
        listings[eid] = [
            _make_listing(
                seller_id=f"brickowl_s{si:02d}",
                seller_name=f"S{si}",
                price_per_cent=10 + (ei + si) % 7,   # mild variation
                available_qty=100,
                shipping_cost_cents=300 + (si * 47) % 500,
            )
            for si in range(15)
        ]
        order.append({"elementId": eid, "quantity": 10})

    t0 = time.perf_counter()
    result = optimize(order, listings)
    elapsed = time.perf_counter() - t0

    # Allocation should be valid (all pieces accounted for)
    total_allocated = sum(
        qty for entry in result.seller_allocations for qty in entry.items.values()
    )
    total_fallback = sum(item["quantity"] for item in result.lego_fallback_items)
    assert total_allocated + total_fallback == 2000, (
        f"piece accounting broke: {total_allocated} allocated + {total_fallback} fallback != 2000"
    )

    assert elapsed < 2.0, f"perf budget exceeded: {elapsed:.2f}s > 2.0s"
    print(f"OK: perf_2000_pieces_15_sellers ({elapsed*1000:.0f}ms)")


def test_merge_listings_cross_source_ordering():
    from scripts.checkout.optimizer import merge_listings

    src_a = {
        "3001": [_make_listing("brickowl_a1", "A1", 10, 50, 500)],
        "3002": [_make_listing("brickowl_a2", "A2", 20, 50, 500)],
    }
    src_b = {
        "3001": [_make_listing("brickowl_b1", "B1", 12, 50, 600)],
        "3003": [_make_listing("brickowl_b2", "B2", 30, 50, 600)],
    }
    src_c = {
        "3001": [_make_listing("brickowl_c1", "C1", 8, 50, 700)],
    }

    merged = merge_listings(src_a, src_b, src_c)

    # All elementIds preserved
    assert set(merged.keys()) == {"3001", "3002", "3003"}
    # Per-element: listings concatenated in source order (a, b, c)
    sellers_3001 = [l.seller_id for l in merged["3001"]]
    assert sellers_3001 == ["brickowl_a1", "brickowl_b1", "brickowl_c1"], (
        f"expected concat in source order, got {sellers_3001}"
    )
    # Single-source elements untouched
    assert len(merged["3002"]) == 1 and merged["3002"][0].seller_id == "brickowl_a2"
    assert len(merged["3003"]) == 1 and merged["3003"][0].seller_id == "brickowl_b2"
    # Source dicts not mutated
    assert len(src_a["3001"]) == 1
    print("OK: merge_listings_cross_source_ordering")


def test_merge_listings_empty():
    from scripts.checkout.optimizer import merge_listings
    assert merge_listings() == {}
    assert merge_listings({}, {}) == {}
    print("OK: merge_listings_empty")


def test_compute_laigo_fee_boundary():
    from scripts.checkout.optimizer import compute_laigo_fee

    # Defaults: min=300 (=$3), pct=5%
    os.environ.pop("LAIGO_FEE_MIN_CENTS", None)
    os.environ.pop("LAIGO_FEE_PERCENT", None)

    # $59.99 grand total -> 5% = 299.95 cents -> int() = 299 -> floor wins -> 300
    assert compute_laigo_fee(5999) == 300, (
        f"$59.99 should hit $3 floor, got {compute_laigo_fee(5999)} cents"
    )
    # $60.00 grand total -> 5% = 300 cents -> ties to floor
    assert compute_laigo_fee(6000) == 300
    # $60.01 -> 5% = 300.05 -> int() = 300 -> ties to floor
    assert compute_laigo_fee(6001) == 300
    # $60.20 -> 5% = 301 -> percentage wins
    assert compute_laigo_fee(6020) == 301, (
        f"$60.20 should yield 5% (301 cents), got {compute_laigo_fee(6020)}"
    )
    # $100 -> 5% = 500 cents
    assert compute_laigo_fee(10000) == 500
    # $0 -> floor
    assert compute_laigo_fee(0) == 300
    print("OK: compute_laigo_fee_boundary ($3 floor vs 5% transition)")


def main() -> int:
    test_happy_path_single_seller()
    test_shipping_consolidation()
    test_threshold_jump()
    test_stockout_fall_through()
    test_partial_fulfillment_falls_through()
    test_perf_2000_pieces_15_sellers()
    test_merge_listings_cross_source_ordering()
    test_merge_listings_empty()
    test_compute_laigo_fee_boundary()
    print()
    print("All optimizer tests PASSED (§6.10 items 1-7).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
