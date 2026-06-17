"""Regression tests for D-001 — background_color_percent slider in 3D mosaics.

Covers scripts/picToMosiac.background_color_budget, the pure helper that scales
the user's slider over the distinct LEGO colors in the REAL background region.
Before the fix the budget counted the foreground silhouette (always white-filled,
unique count 1), so the slider collapsed every background to a single color.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_background_budget
"""
from __future__ import annotations

import numpy as np

from scripts.picToMosiac import background_color_budget


def _bg_with_n_background_colors(n: int, h: int = 16, w: int = 16):
    """Build (bg_idx, fg_mask_np) where the background half has n distinct
    palette indices and the foreground half is white-filled (index 40)."""
    WHITE_IDX = 40  # any single index; mimics remove_background's white fill
    bg_idx = np.full((h, w), WHITE_IDX, dtype=np.int32)
    fg_mask_np = np.zeros((h, w), dtype=np.uint8)
    # Top half = foreground silhouette (opaque), white-filled in bg_idx.
    fg_mask_np[: h // 2, :] = 255
    # Bottom half = real background: spread n distinct indices across its rows.
    bg_rows = range(h // 2, h)
    for i, y in enumerate(bg_rows):
        bg_idx[y, :] = i % n
    return bg_idx, fg_mask_np


def test_budget_scales_with_percent():
    # 8 distinct background colors; budget should track the slider linearly.
    bg_idx, fg_mask_np = _bg_with_n_background_colors(8)
    assert background_color_budget(bg_idx, fg_mask_np, 100) == 8
    assert background_color_budget(bg_idx, fg_mask_np, 50) == 4
    assert background_color_budget(bg_idx, fg_mask_np, 25) == 2
    print("OK: test_budget_scales_with_percent")


def test_budget_floor_is_one():
    # Even at 0% (or a tiny %) we never drop below a single color.
    bg_idx, fg_mask_np = _bg_with_n_background_colors(8)
    assert background_color_budget(bg_idx, fg_mask_np, 0) == 1
    assert background_color_budget(bg_idx, fg_mask_np, 1) == 1
    print("OK: test_budget_floor_is_one")


def test_budget_ignores_foreground_fill():
    # The foreground silhouette is white-filled with a SINGLE index. The old
    # (buggy) code counted that region and always got 1. The fix counts the
    # background region instead, so the budget must reflect background variety,
    # not the foreground fill.
    bg_idx, fg_mask_np = _bg_with_n_background_colors(8)
    # Sanity: foreground region truly has only 1 unique index (the white fill).
    assert len(np.unique(bg_idx[fg_mask_np == 255])) == 1
    # Fixed behavior: 100% over 8 background colors -> 8, not 1.
    assert background_color_budget(bg_idx, fg_mask_np, 100) == 8
    print("OK: test_budget_ignores_foreground_fill")


def test_budget_monotonic_nondecreasing():
    bg_idx, fg_mask_np = _bg_with_n_background_colors(8)
    prev = 0
    for pct in range(0, 101, 5):
        cur = background_color_budget(bg_idx, fg_mask_np, pct)
        assert cur >= prev, f"budget went down at {pct}%: {cur} < {prev}"
        prev = cur
    print("OK: test_budget_monotonic_nondecreasing")


if __name__ == "__main__":
    test_budget_scales_with_percent()
    test_budget_floor_is_one()
    test_budget_ignores_foreground_fill()
    test_budget_monotonic_nondecreasing()
    print("\nAll background-budget tests passed.")
