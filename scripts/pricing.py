"""Static piece-price table for GET /jobs/{job_id}/stats.

Prices live in scripts/piece_prices.json (committed), keyed by LEGO element ID
as a string, values in integer US cents (null = price unknown). The table is
deliberately static: PWYW makes the cost a shipping-free *estimate* with no
financial exposure, the SKU universe is ~63 closed element IDs, and LEGO's
price endpoint sits behind Cloudflare — see the cron-rejection rationale in
the stats-endpoint plan. Refreshing prices = edit the file + bump "as_of".

Leaf module: stdlib only, safe to import anywhere (including the worker tree).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

PRICE_TABLE_PATH = Path(__file__).resolve().parent / "piece_prices.json"

_table_cache: Optional[dict] = None
_table_mtime_ns: Optional[int] = None


def load_price_table(refresh: bool = False) -> dict:
    """Load the price table, cached against the file's mtime.

    The mtime check (one os.stat per call) is what makes "edit
    piece_prices.json, no restart" work: uvicorn --reload only watches .py
    files, so without it a running server would serve the stale table forever.

    Returns {"currency": str, "as_of": str, "prices_cents": {element_id_str: int|None}}.
    Raises on missing/malformed file — callers that treat pricing as optional
    (the stats route) catch and degrade to a null estimate.
    """
    global _table_cache, _table_mtime_ns
    mtime_ns = PRICE_TABLE_PATH.stat().st_mtime_ns
    if _table_cache is None or refresh or mtime_ns != _table_mtime_ns:
        with open(PRICE_TABLE_PATH, "r", encoding="utf-8") as f:
            table = json.load(f)
        if not isinstance(table.get("prices_cents"), dict):
            raise ValueError("piece_prices.json missing 'prices_cents' object")
        _table_cache = table
        _table_mtime_ns = mtime_ns
    return _table_cache


def estimate_cost_cents(piece_counts: dict, table: Optional[dict] = None) -> Optional[int]:
    """Full-build cost estimate: sum(qty * unit price) over piece_counts.

    Returns None unless EVERY element in piece_counts has a non-null price —
    a partial sum would understate the build cost and mislead worse than no
    number at all.
    """
    if table is None:
        table = load_price_table()
    prices = table["prices_cents"]
    total = 0
    for element_id, qty in piece_counts.items():
        price = prices.get(str(element_id))
        if price is None:
            return None
        total += int(qty) * int(price)
    return total
