"""Regression tests for GenerateOrderList (D-036 — index-based counting).

GenerateOrderList consumes the palette-INDEX arrays the pipeline already computed
and counts them with np.bincount, mapping index -> element_id. This replaces the
old RGB round-trip (np.unique(axis=0) + RGB->dict lookup) and its D-008 off-palette
guard: an off-palette colour is now structurally impossible (every index is a valid
palette slot), so the remaining failure mode is an out-of-range INDEX, which must
still fail loud rather than ship a kit missing bricks.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_order_list
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from scripts.MosiacToOrder import GenerateOrderList, GetFrameForSize
from scripts.Util import LEGO_PALETTE_RGB_DICT

PALETTE_EIDS = list(LEGO_PALETTE_RGB_DICT.values())  # index -> element_id
N = len(PALETTE_EIDS)


def _tmp():
    return Path(tempfile.mkdtemp())


def test_background_counts_match_bincount():
    # Top half index 0, bottom half index 5 -> 128 studs each.
    bg_idx = np.zeros((16, 16), dtype=np.int32)
    bg_idx[8:, :] = 5
    order = GenerateOrderList(None, None, bg_idx, False, _tmp())
    assert order[PALETTE_EIDS[0]] == 128, order[PALETTE_EIDS[0]]
    assert order[PALETTE_EIDS[5]] == 128, order[PALETTE_EIDS[5]]
    print("OK: test_background_counts_match_bincount")


def test_foreground_only_counts_visible():
    bg_idx = np.zeros((16, 16), dtype=np.int32)        # all index 0
    fg_idx = np.full((16, 16), 5, dtype=np.int32)      # all index 5
    visible = np.zeros((16, 16), dtype=bool)
    visible[:8, :] = True                               # only top half opaque
    order = GenerateOrderList(fg_idx, visible, bg_idx, False, _tmp())
    assert order[PALETTE_EIDS[0]] == 256, "full background counted"
    assert order[PALETTE_EIDS[5]] == 128, "only the 128 visible fg studs counted"
    print("OK: test_foreground_only_counts_visible")


def test_frame_toggle_adds_frame_parts():
    bg_idx = np.zeros((16, 16), dtype=np.int32)
    no_frame = GenerateOrderList(None, None, bg_idx, False, _tmp())
    with_frame = GenerateOrderList(None, None, bg_idx, True, _tmp())
    frame = GetFrameForSize(16, 16)
    assert frame, "expected some frame parts for a 16x16 mosaic"
    for pid, qty in frame.items():
        assert with_frame[pid] - no_frame.get(pid, 0) == qty
    print("OK: test_frame_toggle_adds_frame_parts")


def test_out_of_range_index_raises():
    # An index == palette size is invalid; must fail loud (successor to D-008).
    bg_idx = np.full((16, 16), N, dtype=np.int32)
    try:
        GenerateOrderList(None, None, bg_idx, False, _tmp())
    except RuntimeError as e:
        assert "out of range" in str(e), str(e)
        print("OK: test_out_of_range_index_raises")
    else:
        raise AssertionError("expected RuntimeError on out-of-range palette index")


def test_writes_order_json():
    bg_idx = np.zeros((16, 16), dtype=np.int32)
    out = _tmp()
    GenerateOrderList(None, None, bg_idx, False, out)
    assert (out / "OrderLists" / "order_list.json").exists()
    print("OK: test_writes_order_json")


if __name__ == "__main__":
    test_background_counts_match_bincount()
    test_foreground_only_counts_visible()
    test_frame_toggle_adds_frame_parts()
    test_out_of_range_index_raises()
    test_writes_order_json()
    print("\nAll order-list tests passed.")
