"""Regression tests for D-008 — GenerateOrderList must fail loud on off-palette
pixels rather than silently dropping pieces (which would ship a kit missing
bricks).

NOTE: importing scripts.picToMosiac first is a temporary shim. Util.py still
uses a bare `from logger import logger` (D-020, fixed in Wave 6); picToMosiac's
sys.path.append makes it resolve. After Wave 6 this import can be dropped.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_order_list
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

import scripts.picToMosiac  # noqa: F401  (sys.path shim — see module docstring)
from scripts.MosiacToOrder import GenerateOrderList
from scripts.Util import LEGO_PALETTE_RGB_DICT


def _rgba(color, size=16):
    """Solid RGBA image of `color` (an (r,g,b) tuple), opaque."""
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :, :3] = color
    arr[:, :, 3] = 255
    return Image.fromarray(arr)  # 4-channel uint8 -> RGBA inferred


def test_offpalette_background_raises():
    off = (1, 2, 3)
    assert off not in LEGO_PALETTE_RGB_DICT
    bg = _rgba(off)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "OrderLists").mkdir()
        try:
            GenerateOrderList(None, bg, False, Path(tmp))
        except RuntimeError as e:
            assert "background" in str(e) and "(1, 2, 3)" in str(e)
            print("OK: test_offpalette_background_raises")
        else:
            raise AssertionError("expected RuntimeError on off-palette background")


def test_offpalette_foreground_raises():
    palette_color = next(iter(LEGO_PALETTE_RGB_DICT))  # a valid background color
    off = (1, 2, 3)
    bg = _rgba(palette_color)
    fg = _rgba(off)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "OrderLists").mkdir()
        try:
            GenerateOrderList(fg, bg, False, Path(tmp))
        except RuntimeError as e:
            assert "foreground" in str(e) and "(1, 2, 3)" in str(e)
            print("OK: test_offpalette_foreground_raises")
        else:
            raise AssertionError("expected RuntimeError on off-palette foreground")


def test_valid_palette_succeeds():
    color = next(iter(LEGO_PALETTE_RGB_DICT))
    bg = _rgba(color)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "OrderLists").mkdir()
        order = GenerateOrderList(None, bg, False, Path(tmp))
        assert sum(order.values()) > 0
        assert (Path(tmp) / "OrderLists" / "order_list.json").exists()
    print("OK: test_valid_palette_succeeds")


if __name__ == "__main__":
    test_offpalette_background_raises()
    test_offpalette_foreground_raises()
    test_valid_palette_succeeds()
    print("\nAll order-list tests passed.")
