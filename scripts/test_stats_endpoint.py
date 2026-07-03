"""Unit + endpoint tests for the job-stats stack.

Covers scripts/pricing.py, MosiacToOrder.BuildStatsPayload, and the
GET /jobs/{job_id}/stats route.

Pure-function unit tests:
    1. estimate_cost_cents — all elements priced -> exact sum
    2. estimate_cost_cents — element absent from table -> None (no partial sum)
    3. estimate_cost_cents — null price -> None
    4. estimate_cost_cents — int keys in piece_counts tolerated
    5. piece_prices.json covers every element ID the pipeline can emit
    6. BuildStatsPayload — >999 quantities preserved (order_list split guard),
       str keys, non-positive quantities dropped, total correct

Endpoint handler tests (drive the async function directly, no TestClient):
    7.  404 + STATS_NOT_AVAILABLE when stats.json missing
    8.  200 + full payload when every element is priced
    9.  200 + null estimate when an element is unpriced
    10. 200 + null estimate when the price table itself is broken
    11. 500 + STATS_CORRUPTED when stats.json is not valid JSON
    12. 404 on path-traversal job_id

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_stats_endpoint
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from scripts.MosiacToOrder import (
    BuildStatsPayload,
    GetBaseplatesForSize,
    GetFrameForSize,
)
from scripts.Util import LEGO_PALETTE_RGB_DICT
from scripts.pricing import estimate_cost_cents, load_price_table


def _table(prices: dict) -> dict:
    return {"currency": "USD", "as_of": "2026-07-03", "prices_cents": prices}


# ---------------------------------------------------------------------------
# 1-4. estimate_cost_cents
# ---------------------------------------------------------------------------
def test_estimate_all_priced():
    table = _table({"111": 6, "222": 10})
    assert estimate_cost_cents({"111": 100, "222": 3}, table) == 630
    print("OK: test_estimate_all_priced")


def test_estimate_missing_element_returns_none():
    table = _table({"111": 6})
    assert estimate_cost_cents({"111": 100, "999": 1}, table) is None
    print("OK: test_estimate_missing_element_returns_none")


def test_estimate_null_price_returns_none():
    table = _table({"111": 6, "222": None})
    assert estimate_cost_cents({"111": 100, "222": 3}, table) is None
    print("OK: test_estimate_null_price_returns_none")


def test_estimate_accepts_int_keys():
    table = _table({"111": 6})
    assert estimate_cost_cents({111: 2}, table) == 12
    print("OK: test_estimate_accepts_int_keys")


# ---------------------------------------------------------------------------
# 5. Committed price table covers the whole pipeline SKU universe
# ---------------------------------------------------------------------------
def test_price_table_covers_all_pipeline_elements():
    table = load_price_table(refresh=True)
    keys = set(table["prices_cents"])
    emitted = (
        set(LEGO_PALETTE_RGB_DICT.values())
        | set(GetBaseplatesForSize(32, 32))
        | set(GetFrameForSize(32, 32))
    )
    missing = {e for e in emitted if str(e) not in keys}
    assert not missing, (
        f"piece_prices.json is missing element IDs {sorted(missing)} — "
        "a palette/structural-part edit must be mirrored there or full-build "
        "pricing silently goes null"
    )
    print("OK: test_price_table_covers_all_pipeline_elements")


def test_price_table_reloads_on_file_change():
    """Editing piece_prices.json must take effect in a running server without a
    restart: uvicorn --reload only watches .py files, so load_price_table
    invalidates its cache on the file's mtime instead."""
    import os
    from scripts import pricing

    with tempfile.TemporaryDirectory() as tmp:
        table_path = Path(tmp) / "piece_prices.json"
        original_path = pricing.PRICE_TABLE_PATH
        original_cache = pricing._table_cache
        original_mtime = pricing._table_mtime_ns
        pricing.PRICE_TABLE_PATH = table_path
        pricing._table_cache = None
        pricing._table_mtime_ns = None
        try:
            table_path.write_text(json.dumps(_table({"111": None})))
            assert pricing.estimate_cost_cents({"111": 2}) is None

            table_path.write_text(json.dumps(_table({"111": 6})))
            # Force a distinct mtime — same-second writes can share a timestamp.
            os.utime(table_path, ns=(0, table_path.stat().st_mtime_ns + 1_000_000))

            assert pricing.estimate_cost_cents({"111": 2}) == 12
        finally:
            pricing.PRICE_TABLE_PATH = original_path
            pricing._table_cache = original_cache
            pricing._table_mtime_ns = original_mtime
    print("OK: test_price_table_reloads_on_file_change")


# ---------------------------------------------------------------------------
# 6. BuildStatsPayload
# ---------------------------------------------------------------------------
def test_build_stats_payload():
    # 1500 > the 999-per-file order_list split cap: stats must keep the full
    # quantity (the stable order_list.json would report only 999).
    payload = BuildStatsPayload({111: 1500, 222: 3, 333: 0})
    assert payload["piece_counts"] == {"111": 1500, "222": 3}
    assert payload["total_pieces"] == 1503
    json.dumps(payload)  # no leaked non-JSON types
    print("OK: test_build_stats_payload")


# ---------------------------------------------------------------------------
# 7-12. Endpoint handler tests
# ---------------------------------------------------------------------------
def _import_handler():
    from scripts import Main
    return Main


def _write_stats(output_dir: Path, job_id: str, body: bytes) -> None:
    job_dir = output_dir / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "stats.json").write_bytes(body)


def test_endpoint_404_when_no_file():
    Main = _import_handler()
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as tmp:
        original = Main.OUTPUT_DIR
        Main.OUTPUT_DIR = Path(tmp).resolve()
        try:
            try:
                asyncio.run(Main.get_job_stats("missing-job"))
            except HTTPException as exc:
                assert exc.status_code == 404
                assert exc.detail["code"] == "STATS_NOT_AVAILABLE"
            else:
                raise AssertionError("Expected HTTPException 404")
        finally:
            Main.OUTPUT_DIR = original
    print("OK: test_endpoint_404_when_no_file")


def test_endpoint_200_fully_priced():
    Main = _import_handler()

    with tempfile.TemporaryDirectory() as tmp:
        original_dir = Main.OUTPUT_DIR
        original_loader = Main.load_price_table
        Main.OUTPUT_DIR = Path(tmp).resolve()
        Main.load_price_table = lambda refresh=False: _table({"111": 6, "222": 10})
        try:
            stats = {"piece_counts": {"111": 100, "222": 3}, "total_pieces": 103}
            _write_stats(Main.OUTPUT_DIR, "job-priced", json.dumps(stats).encode())

            body = asyncio.run(Main.get_job_stats("job-priced"))
            assert body == {
                "piece_count": 103,
                "estimated_cost_cents": 630,
                "currency": "USD",
                "pricing_as_of": "2026-07-03",
            }
        finally:
            Main.OUTPUT_DIR = original_dir
            Main.load_price_table = original_loader
    print("OK: test_endpoint_200_fully_priced")


def test_endpoint_null_estimate_when_unpriced():
    Main = _import_handler()

    with tempfile.TemporaryDirectory() as tmp:
        original_dir = Main.OUTPUT_DIR
        original_loader = Main.load_price_table
        Main.OUTPUT_DIR = Path(tmp).resolve()
        Main.load_price_table = lambda refresh=False: _table({"111": 6, "222": None})
        try:
            stats = {"piece_counts": {"111": 100, "222": 3}, "total_pieces": 103}
            _write_stats(Main.OUTPUT_DIR, "job-unpriced", json.dumps(stats).encode())

            body = asyncio.run(Main.get_job_stats("job-unpriced"))
            assert body["piece_count"] == 103
            assert body["estimated_cost_cents"] is None
            # Table itself loaded fine, so its metadata still comes through.
            assert body["currency"] == "USD"
            assert body["pricing_as_of"] == "2026-07-03"
        finally:
            Main.OUTPUT_DIR = original_dir
            Main.load_price_table = original_loader
    print("OK: test_endpoint_null_estimate_when_unpriced")


def test_endpoint_degrades_when_price_table_broken():
    Main = _import_handler()

    def _boom(refresh=False):
        raise FileNotFoundError("piece_prices.json gone")

    with tempfile.TemporaryDirectory() as tmp:
        original_dir = Main.OUTPUT_DIR
        original_loader = Main.load_price_table
        Main.OUTPUT_DIR = Path(tmp).resolve()
        Main.load_price_table = _boom
        try:
            stats = {"piece_counts": {"111": 5}, "total_pieces": 5}
            _write_stats(Main.OUTPUT_DIR, "job-notable", json.dumps(stats).encode())

            body = asyncio.run(Main.get_job_stats("job-notable"))
            assert body == {
                "piece_count": 5,
                "estimated_cost_cents": None,
                "currency": None,
                "pricing_as_of": None,
            }
        finally:
            Main.OUTPUT_DIR = original_dir
            Main.load_price_table = original_loader
    print("OK: test_endpoint_degrades_when_price_table_broken")


def test_endpoint_500_on_corruption():
    Main = _import_handler()
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as tmp:
        original = Main.OUTPUT_DIR
        Main.OUTPUT_DIR = Path(tmp).resolve()
        try:
            _write_stats(Main.OUTPUT_DIR, "job-corrupt", b"not json{{")
            try:
                asyncio.run(Main.get_job_stats("job-corrupt"))
            except HTTPException as exc:
                assert exc.status_code == 500
                assert exc.detail["code"] == "STATS_CORRUPTED"
            else:
                raise AssertionError("Expected HTTPException 500")
        finally:
            Main.OUTPUT_DIR = original
    print("OK: test_endpoint_500_on_corruption")


def test_endpoint_traversal_404():
    Main = _import_handler()
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as tmp:
        original = Main.OUTPUT_DIR
        Main.OUTPUT_DIR = Path(tmp).resolve()
        try:
            try:
                asyncio.run(Main.get_job_stats("../outside"))
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("Expected HTTPException 404")
        finally:
            Main.OUTPUT_DIR = original
    print("OK: test_endpoint_traversal_404")


if __name__ == "__main__":
    test_estimate_all_priced()
    test_estimate_missing_element_returns_none()
    test_estimate_null_price_returns_none()
    test_estimate_accepts_int_keys()
    test_price_table_covers_all_pipeline_elements()
    test_price_table_reloads_on_file_change()
    test_build_stats_payload()
    test_endpoint_404_when_no_file()
    test_endpoint_200_fully_priced()
    test_endpoint_null_estimate_when_unpriced()
    test_endpoint_degrades_when_price_table_broken()
    test_endpoint_500_on_corruption()
    test_endpoint_traversal_404()
    print("\nAll stats-endpoint tests passed.")
