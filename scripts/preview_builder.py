"""3D-preview payload builder for the LAIGO mosaic pipeline.

Produces a small JSON blob describing the finished mosaic (per-stud color grid,
frame metadata, palette) that the frontend renders in Three.js. Consumed by
GET /jobs/{job_id}/preview in Main.py.

Design notes:
- Pure function — no I/O in build_preview_payload. The worker writes the result
  to disk via write_preview_atomic so a half-written file is never visible to a
  reader racing the writer.
- Palette index 0 is reserved for the perimeter frame (LEGO black, element_id
  null). When the mosaic itself contains LEGO black studs, a SEPARATE palette
  entry is emitted with element_id=302426 — same hex, distinct semantics
  (orderable vs. frame-hardcoded).
- Grid cells are local palette indices (0..N-1), not global LEGO_PALETTE_RGB
  indices. Bumping schema_version is required for any breaking change.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

# Util.py uses a bare `from logger import logger` import (CLAUDE.md "Antipatterns"
# §"Bare imports alongside relative imports"). picToMosiac.py compensates by
# prepending scripts/ to sys.path at module load — when preview_builder is
# imported standalone (e.g., by test_preview.py without first importing
# picToMosiac), we mirror that workaround so Util's logger import resolves.
sys.path.append(str(Path(__file__).resolve().parent))

from .Util import LEGO_PALETTE_RGB_DICT

SCHEMA_VERSION = 1
FRAME_HEX = "#1B2A34"
FRAME_THICKNESS_STUDS = 1
FRAME_HEIGHT_PLATES = 6
FOREGROUND_LIFT_PLATES_3D = 1
FOREGROUND_EMPTY_SENTINEL = -1

_GLOBAL_RGB_BY_INDEX: list[tuple[int, int, int]] = list(LEGO_PALETTE_RGB_DICT.keys())


def _hex_for_rgb(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def _build_local_palette(used_global_indices: set[int]) -> tuple[list[dict], dict[int, int]]:
    """Return (palette_list, global_to_local). Index 0 is always the frame slot."""
    palette: list[dict] = [{"hex": FRAME_HEX, "element_id": None}]
    global_to_local: dict[int, int] = {}
    for gidx in sorted(used_global_indices):
        rgb = _GLOBAL_RGB_BY_INDEX[gidx]
        element_id = LEGO_PALETTE_RGB_DICT[rgb]
        palette.append({"hex": _hex_for_rgb(rgb), "element_id": int(element_id)})
        global_to_local[gidx] = len(palette) - 1
    return palette, global_to_local


def _grid_to_python_ints(remapped: np.ndarray) -> list[list[int]]:
    """numpy 2D array -> list-of-lists of pure Python ints (json.dumps-safe)."""
    return [[int(v) for v in row] for row in remapped]


def build_preview_payload(
    *,
    job_id: str,
    mosaic_type: str,
    block_width: int,
    block_height: int,
    studs_width: int,
    studs_height: int,
    to_frame: bool,
    bg_idx: np.ndarray,
    fg_idx: Optional[np.ndarray] = None,
    fg_mask: Optional[np.ndarray] = None,
) -> dict:
    """Build the JSON-serializable preview payload.

    bg_idx: (H, W) int array of global LEGO palette indices.
    fg_idx: (H, W) int array or None (None for 2D mosaics).
    fg_mask: (H, W) uint8 array with 0/255 or None. Required when fg_idx is set.
    """
    if mosaic_type not in ("2d", "3d"):
        raise ValueError(f"mosaic_type must be '2d' or '3d', got {mosaic_type!r}")
    if bg_idx.shape != (studs_height, studs_width):
        raise ValueError(
            f"bg_idx shape {bg_idx.shape} does not match ({studs_height}, {studs_width})"
        )
    is_3d = mosaic_type == "3d"
    if is_3d:
        if fg_idx is None or fg_mask is None:
            raise ValueError("3D mosaic requires both fg_idx and fg_mask")
        if fg_idx.shape != bg_idx.shape or fg_mask.shape != bg_idx.shape:
            raise ValueError("fg_idx and fg_mask must match bg_idx shape")

    used: set[int] = {int(v) for v in np.unique(bg_idx)}
    if is_3d:
        fg_visible_indices = fg_idx[fg_mask == 255]
        if fg_visible_indices.size > 0:
            used.update(int(v) for v in np.unique(fg_visible_indices))

    palette, global_to_local = _build_local_palette(used)

    remap = np.zeros(len(_GLOBAL_RGB_BY_INDEX), dtype=np.int32)
    for gidx, lidx in global_to_local.items():
        remap[gidx] = lidx
    bg_local = remap[bg_idx]
    background_grid = _grid_to_python_ints(bg_local)

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "mosaic_type": mosaic_type,
        "width_studs": int(studs_width),
        "height_studs": int(studs_height),
        "block_width": int(block_width),
        "block_height": int(block_height),
        "has_frame": bool(to_frame),
        "foreground_lift_plates": FOREGROUND_LIFT_PLATES_3D if is_3d else 0,
        "frame": {
            "thickness_studs": FRAME_THICKNESS_STUDS,
            "height_plates": FRAME_HEIGHT_PLATES,
            "palette_index": 0,
        },
        "palette": palette,
        "background_grid": background_grid,
    }

    if is_3d:
        fg_local = remap[fg_idx]
        fg_local_masked = np.where(fg_mask == 255, fg_local, FOREGROUND_EMPTY_SENTINEL)
        payload["foreground_grid"] = _grid_to_python_ints(fg_local_masked)

    return payload


def write_preview_atomic(payload: dict, dst: Path) -> None:
    """Write payload to dst as JSON atomically (.tmp + os.replace)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, dst)
