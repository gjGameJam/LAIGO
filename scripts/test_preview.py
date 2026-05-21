"""Unit + integration tests for the 3D-preview endpoint stack.

Covers scripts/preview_builder.py and the GET /jobs/{job_id}/preview route.

Pure-function unit tests:
    1. 2D mosaic, no frame — basic payload shape
    2. 2D mosaic, with frame — frame block present, palette index 0 reserved
    3. 3D mosaic, no frame — foreground_grid present, -1 sentinel where fg_mask==0
    4. 3D mosaic, with frame — combined
    5. Palette compaction — only used colors emitted
    6. LEGO black distinct from frame black (same hex, different element_id)
    7. Frame slot always present at palette[0] even when has_frame=False
    8. Output is JSON-serializable (no leaked numpy types)
    9. Dimensions consistency (width_studs == block_width*16, etc.)

Endpoint handler tests (drive the async function directly, no TestClient):
    10. 404 + PREVIEW_NOT_AVAILABLE when preview.json missing
    11. 200 + bytes-match when file present
    12. 500 + PREVIEW_CORRUPTED when read_bytes raises

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_preview
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import numpy as np

from scripts.preview_builder import (
    FOREGROUND_EMPTY_SENTINEL,
    FRAME_HEX,
    SCHEMA_VERSION,
    build_preview_payload,
    write_preview_atomic,
)
from scripts.Util import LEGO_PALETTE_RGB_DICT

_GLOBAL_RGB_BY_INDEX = list(LEGO_PALETTE_RGB_DICT.keys())


def _global_idx_for_rgb(rgb: tuple[int, int, int]) -> int:
    return _GLOBAL_RGB_BY_INDEX.index(rgb)


def _fill(value: int, h: int, w: int) -> np.ndarray:
    return np.full((h, w), value, dtype=np.int32)


# ---------------------------------------------------------------------------
# 1. 2D no-frame basic shape
# ---------------------------------------------------------------------------
def test_2d_no_frame_basic():
    red = _global_idx_for_rgb((180, 0, 0))
    white = _global_idx_for_rgb((244, 244, 244))
    bg = _fill(red, 16, 32)
    bg[0:8, :] = white

    payload = build_preview_payload(
        job_id="job-2d",
        mosaic_type="2d",
        block_width=2,
        block_height=1,
        studs_width=32,
        studs_height=16,
        to_frame=False,
        bg_idx=bg,
    )

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["mosaic_type"] == "2d"
    assert payload["has_frame"] is False
    assert payload["foreground_lift_plates"] == 0
    assert "foreground_grid" not in payload
    assert len(payload["background_grid"]) == 16
    assert len(payload["background_grid"][0]) == 32
    assert len(payload["palette"]) == 3
    assert payload["palette"][0] == {"hex": FRAME_HEX, "element_id": None}
    print("OK: test_2d_no_frame_basic")


# ---------------------------------------------------------------------------
# 2. 2D with frame
# ---------------------------------------------------------------------------
def test_2d_with_frame():
    red = _global_idx_for_rgb((180, 0, 0))
    bg = _fill(red, 16, 16)

    payload = build_preview_payload(
        job_id="job-2d-frame",
        mosaic_type="2d",
        block_width=1,
        block_height=1,
        studs_width=16,
        studs_height=16,
        to_frame=True,
        bg_idx=bg,
    )

    assert payload["has_frame"] is True
    assert payload["frame"]["thickness_studs"] == 1
    assert payload["frame"]["height_plates"] == 6
    assert payload["frame"]["palette_index"] == 0
    grid_values = {v for row in payload["background_grid"] for v in row}
    assert 0 not in grid_values, "frame slot should not appear in grid for mosaic without black studs"
    print("OK: test_2d_with_frame")


# ---------------------------------------------------------------------------
# 3. 3D no-frame
# ---------------------------------------------------------------------------
def test_3d_no_frame():
    bg_color = _global_idx_for_rgb((0, 133, 43))
    fg_color = _global_idx_for_rgb((30, 90, 168))
    bg = _fill(bg_color, 16, 16)
    fg = _fill(fg_color, 16, 16)
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[4:12, 4:12] = 255

    payload = build_preview_payload(
        job_id="job-3d",
        mosaic_type="3d",
        block_width=1,
        block_height=1,
        studs_width=16,
        studs_height=16,
        to_frame=False,
        bg_idx=bg,
        fg_idx=fg,
        fg_mask=mask,
    )

    assert payload["foreground_lift_plates"] == 1
    fgrid = payload["foreground_grid"]
    assert len(fgrid) == 16 and len(fgrid[0]) == 16
    for y in range(16):
        for x in range(16):
            if 4 <= y < 12 and 4 <= x < 12:
                assert fgrid[y][x] != FOREGROUND_EMPTY_SENTINEL
            else:
                assert fgrid[y][x] == FOREGROUND_EMPTY_SENTINEL
    palette_hex = {entry["hex"] for entry in payload["palette"]}
    assert "#00852B" in palette_hex
    assert "#1E5AA8" in palette_hex
    print("OK: test_3d_no_frame")


# ---------------------------------------------------------------------------
# 4. 3D with frame
# ---------------------------------------------------------------------------
def test_3d_with_frame():
    bg_color = _global_idx_for_rgb((244, 244, 244))
    fg_color = _global_idx_for_rgb((180, 0, 0))
    bg = _fill(bg_color, 16, 16)
    fg = _fill(fg_color, 16, 16)
    mask = np.full((16, 16), 255, dtype=np.uint8)

    payload = build_preview_payload(
        job_id="job-3d-frame",
        mosaic_type="3d",
        block_width=1,
        block_height=1,
        studs_width=16,
        studs_height=16,
        to_frame=True,
        bg_idx=bg,
        fg_idx=fg,
        fg_mask=mask,
    )

    assert payload["has_frame"] is True
    assert payload["foreground_lift_plates"] == 1
    assert "foreground_grid" in payload
    fgrid = payload["foreground_grid"]
    assert all(v != FOREGROUND_EMPTY_SENTINEL for row in fgrid for v in row)
    print("OK: test_3d_with_frame")


# ---------------------------------------------------------------------------
# 5. Palette compaction
# ---------------------------------------------------------------------------
def test_palette_compaction():
    colors = [
        _global_idx_for_rgb((180, 0, 0)),
        _global_idx_for_rgb((244, 244, 244)),
        _global_idx_for_rgb((0, 133, 43)),
        _global_idx_for_rgb((30, 90, 168)),
    ]
    bg = np.zeros((4, 4), dtype=np.int32)
    bg[0, :] = colors[0]
    bg[1, :] = colors[1]
    bg[2, :] = colors[2]
    bg[3, :] = colors[3]

    payload = build_preview_payload(
        job_id="job-compact",
        mosaic_type="2d",
        block_width=0,
        block_height=0,
        studs_width=4,
        studs_height=4,
        to_frame=False,
        bg_idx=bg,
    )

    assert len(payload["palette"]) == 5, (
        f"expected 4 unique colors + 1 frame slot, got {len(payload['palette'])}"
    )
    print("OK: test_palette_compaction")


# ---------------------------------------------------------------------------
# 6. LEGO black distinct from frame black
# ---------------------------------------------------------------------------
def test_palette_dedup_lego_black_distinct_from_frame_black():
    black = _global_idx_for_rgb((27, 42, 52))
    bg = _fill(black, 8, 8)

    payload = build_preview_payload(
        job_id="job-black",
        mosaic_type="2d",
        block_width=0,
        block_height=0,
        studs_width=8,
        studs_height=8,
        to_frame=True,
        bg_idx=bg,
    )

    black_entries = [e for e in payload["palette"] if e["hex"] == FRAME_HEX]
    assert len(black_entries) == 2, f"expected two entries with hex {FRAME_HEX}"
    assert black_entries[0] == {"hex": FRAME_HEX, "element_id": None}
    assert black_entries[1]["element_id"] == 302426
    mosaic_black_local = payload["palette"].index(black_entries[1])
    assert mosaic_black_local != 0
    assert all(
        v == mosaic_black_local for row in payload["background_grid"] for v in row
    ), "mosaic black cells should reference the non-zero local index"
    print("OK: test_palette_dedup_lego_black_distinct_from_frame_black")


# ---------------------------------------------------------------------------
# 7. Frame slot always at palette[0] even without frame
# ---------------------------------------------------------------------------
def test_frame_slot_always_present_even_with_no_frame():
    bg = _fill(_global_idx_for_rgb((180, 0, 0)), 4, 4)

    payload = build_preview_payload(
        job_id="job-noframe",
        mosaic_type="2d",
        block_width=0,
        block_height=0,
        studs_width=4,
        studs_height=4,
        to_frame=False,
        bg_idx=bg,
    )

    assert payload["palette"][0] == {"hex": FRAME_HEX, "element_id": None}
    print("OK: test_frame_slot_always_present_even_with_no_frame")


# ---------------------------------------------------------------------------
# 8. JSON-serialization safety (no leaked numpy types)
# ---------------------------------------------------------------------------
def test_serialization_no_numpy_types():
    bg = _fill(_global_idx_for_rgb((180, 0, 0)), 16, 16)
    fg = _fill(_global_idx_for_rgb((30, 90, 168)), 16, 16)
    mask = np.full((16, 16), 255, dtype=np.uint8)

    payload = build_preview_payload(
        job_id="job-serial",
        mosaic_type="3d",
        block_width=1,
        block_height=1,
        studs_width=16,
        studs_height=16,
        to_frame=True,
        bg_idx=bg,
        fg_idx=fg,
        fg_mask=mask,
    )

    json.dumps(payload)
    print("OK: test_serialization_no_numpy_types")


# ---------------------------------------------------------------------------
# 9. Dimensions consistency
# ---------------------------------------------------------------------------
def test_dimensions_consistency():
    bg = _fill(_global_idx_for_rgb((180, 0, 0)), 32, 48)

    payload = build_preview_payload(
        job_id="job-dims",
        mosaic_type="2d",
        block_width=3,
        block_height=2,
        studs_width=48,
        studs_height=32,
        to_frame=False,
        bg_idx=bg,
    )

    assert payload["width_studs"] == payload["block_width"] * 16
    assert payload["height_studs"] == payload["block_height"] * 16
    assert len(payload["background_grid"]) == payload["height_studs"]
    assert len(payload["background_grid"][0]) == payload["width_studs"]
    print("OK: test_dimensions_consistency")


# ---------------------------------------------------------------------------
# 10-12. Endpoint handler tests
# ---------------------------------------------------------------------------
def _import_handler():
    from scripts import Main
    return Main


def test_endpoint_404_when_no_file():
    Main = _import_handler()
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as tmp:
        original = Main.OUTPUT_DIR
        Main.OUTPUT_DIR = Path(tmp)
        try:
            try:
                asyncio.run(Main.get_job_preview("missing-job"))
            except HTTPException as exc:
                assert exc.status_code == 404
                assert exc.detail["code"] == "PREVIEW_NOT_AVAILABLE"
            else:
                raise AssertionError("Expected HTTPException 404")
        finally:
            Main.OUTPUT_DIR = original
    print("OK: test_endpoint_404_when_no_file")


def test_endpoint_200_when_file_present():
    Main = _import_handler()

    with tempfile.TemporaryDirectory() as tmp:
        original = Main.OUTPUT_DIR
        Main.OUTPUT_DIR = Path(tmp)
        try:
            job_dir = Main.OUTPUT_DIR / "job-200"
            job_dir.mkdir(parents=True)
            body = b'{"hello":"world"}'
            (job_dir / "preview.json").write_bytes(body)

            response = asyncio.run(Main.get_job_preview("job-200"))
            assert response.status_code == 200
            assert response.body == body
            assert response.media_type == "application/json"
        finally:
            Main.OUTPUT_DIR = original
    print("OK: test_endpoint_200_when_file_present")


def test_endpoint_500_on_corruption():
    Main = _import_handler()
    from fastapi import HTTPException

    with tempfile.TemporaryDirectory() as tmp:
        original = Main.OUTPUT_DIR
        Main.OUTPUT_DIR = Path(tmp)
        try:
            job_dir = Main.OUTPUT_DIR / "job-500"
            job_dir.mkdir(parents=True)
            (job_dir / "preview.json").mkdir()

            try:
                asyncio.run(Main.get_job_preview("job-500"))
            except HTTPException as exc:
                assert exc.status_code == 500
                assert exc.detail["code"] == "PREVIEW_CORRUPTED"
            else:
                raise AssertionError("Expected HTTPException 500")
        finally:
            Main.OUTPUT_DIR = original
    print("OK: test_endpoint_500_on_corruption")


# ---------------------------------------------------------------------------
# Bonus: write_preview_atomic produces a readable JSON file
# ---------------------------------------------------------------------------
def test_write_preview_atomic_roundtrip():
    bg = _fill(_global_idx_for_rgb((180, 0, 0)), 4, 4)
    payload = build_preview_payload(
        job_id="job-rt",
        mosaic_type="2d",
        block_width=0,
        block_height=0,
        studs_width=4,
        studs_height=4,
        to_frame=False,
        bg_idx=bg,
    )

    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "preview.json"
        write_preview_atomic(payload, dst)
        assert dst.exists()
        loaded = json.loads(dst.read_text())
        assert loaded["job_id"] == "job-rt"
        assert loaded["schema_version"] == SCHEMA_VERSION
    print("OK: test_write_preview_atomic_roundtrip")


if __name__ == "__main__":
    test_2d_no_frame_basic()
    test_2d_with_frame()
    test_3d_no_frame()
    test_3d_with_frame()
    test_palette_compaction()
    test_palette_dedup_lego_black_distinct_from_frame_black()
    test_frame_slot_always_present_even_with_no_frame()
    test_serialization_no_numpy_types()
    test_dimensions_consistency()
    test_write_preview_atomic_roundtrip()
    test_endpoint_404_when_no_file()
    test_endpoint_200_when_file_present()
    test_endpoint_500_on_corruption()
    print("\nAll preview tests passed.")
