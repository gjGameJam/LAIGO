from collections import defaultdict
import numpy as np
from pathlib import Path

# D-019: relative import. The bare `from Util import ...` only resolved because
# picToMosiac.py appends scripts/ to sys.path at load time — fragile and breaks
# any standalone import (tests, REPL). Dead imports (os, math.ceil, log_debug,
# and — after D-013/D-008 — shutil, GetOutputPathDir, log_error) dropped too.
from .Util import (
    GetPaletteDict,
    SaveDictAsJsonsOptimized,
    log_info,
)
from .mosaic_types import STUDS_PER_BLOCK  # D-035: single source of truth for the 16-stud block

PALETTE_DICT = GetPaletteDict()
# D-036: palette index -> element_id, in LEGO_PALETTE_RGB order (= dict insertion
# order, since GetPaletteRGBArray uses the same dict keys). The pipeline hands us
# palette INDICES, so we count them with np.bincount and map straight to element
# IDs — no RGB round-trip, no np.unique(axis=0) sort, and no off-palette KeyError
# (every index is a valid palette slot by construction).
PALETTE_ELEMENT_IDS = list(PALETTE_DICT.values())
_N_PALETTE = len(PALETTE_ELEMENT_IDS)


def _accumulate_index_counts(order, idx_flat):
    """Add per-element-id pixel counts from a 1-D array of palette indices into
    `order` (a defaultdict(int)), via a single O(N) np.bincount."""
    if idx_flat.size == 0:
        return
    counts = np.bincount(idx_flat, minlength=_N_PALETTE)
    if counts.size > _N_PALETTE:
        # An index >= palette size means a pipeline invariant was violated. Fail
        # loud rather than silently shipping a kit missing bricks — this is the
        # index-path successor to the RGB path's D-008 guard.
        bad = int(np.flatnonzero(counts[_N_PALETTE:]).min() + _N_PALETTE)
        raise RuntimeError(
            f"Mosaic palette index {bad} is out of range (palette size "
            f"{_N_PALETTE}). Refusing to ship a kit missing bricks."
        )
    for i in range(_N_PALETTE):
        c = int(counts[i])
        if c:
            order[PALETTE_ELEMENT_IDS[i]] += c

# D-013: get_order_lists_file_path / empty_order_list_folder were the legacy CLI
# path (output_dir is None -> write to the project-root scratch dir). Removed;
# output_dir is now required and the API worker is the only caller.

def GenerateOrderList(fg_idx, fg_visible_mask, bg_idx, want_frame, output_dir):
    """Build the consolidated LEGO piece order from the palette-INDEX arrays the
    pipeline already computed (D-036), instead of re-deriving palette identity from
    RGB pixels (an np.unique(axis=0) sort + RGB->dict lookup that also created the
    off-palette KeyError surface — now impossible, since every index is a valid
    palette slot).

    bg_idx          : (H, W) int palette indices, counted in full (the 2D mosaic, or
                      the simplified 3D background).
    fg_idx          : (H, W) int palette indices for the 3D foreground, or None (2D).
    fg_visible_mask : bool (H, W) — count fg_idx only where True (the opaque studs);
                      ignored when fg_idx is None.
    """
    # D-013: output_dir is required (CLI fallback removed).
    if output_dir is None:
        raise ValueError("output_dir is required")

    log_info("creating order list...")

    # 1. Baseplates (structural). The mosaic is at stud resolution, so the index
    # array dims ARE the stud width/height GetBaseplatesForSize expects.
    height, width = bg_idx.shape
    order = defaultdict(int, GetBaseplatesForSize(width, height))

    # 2. Background / 2D layer: every pixel is a brick.
    _accumulate_index_counts(order, bg_idx.reshape(-1))

    # 3. Foreground layer (3D only): only the opaque studs.
    if fg_idx is not None:
        _accumulate_index_counts(order, fg_idx[fg_visible_mask])

    # 4. Frame.
    if want_frame:
        frame_parts = GetFrameForSize(width, height)
        for pid, qty in frame_parts.items():
            order[pid] += qty

    # 5. Save.
    output_json_path = Path(output_dir) / "OrderLists" / "order_list.json"
    SaveDictAsJsonsOptimized(order, output_json_path)
    log_info(f"Sum of all pieces: {sum(order.values())}")
    return order

def GetBaseplatesForSize(width, height):
    """Calculates structural baseplate components."""
    # Floor division (//) is faster than float division + int() cast
    blockWidth = width // STUDS_PER_BLOCK
    blockHeight = height // STUDS_PER_BLOCK

    numOfBlocks = blockWidth * blockHeight
    numOfGreenConnectors = (2 * (blockWidth - 1)) * blockHeight
    numOfGreenPlates = (blockWidth - 1) * blockHeight
    numOfRedConnectors = (2 * (blockHeight - 1)) * blockWidth
    numOfRedPlates = (blockHeight - 1) * blockWidth
    twoxtwoPlates = numOfBlocks * 5
    nailHooks = min(numOfBlocks, 2)
    nailHookConnectors = nailHooks * 2

    # Return standard dict; GenerateOrderList will upgrade it to defaultdict
    return {
        6302092: int(numOfBlocks),
        6302094: int(nailHooks),
        6279875: int(nailHookConnectors),
        6526672: int(numOfGreenConnectors),
        6347789: int(numOfRedConnectors),
        4621548: int(numOfGreenPlates),
        379521:  int(numOfRedPlates),
        4211094: int(twoxtwoPlates)
    }

def GetFrameForSize(width, height):
    """Calculates frame components based on mosaic dimensions."""
    blockWidth = width // STUDS_PER_BLOCK
    blockHeight = height // STUDS_PER_BLOCK
    num_of_corners = 4

    cornerBlocks = num_of_corners
    cornerPlates = num_of_corners
    onexoneBricks = num_of_corners * 2
    twoxoneBricksWithAxleHole = (blockWidth * 4) + (blockHeight * 4)
    axlePegs = twoxoneBricksWithAxleHole
    eightxoneBricks = twoxoneBricksWithAxleHole // 2
    fourxoneBricks = ((blockWidth - 1) * 2) + ((blockHeight - 1) * 2)
    tenxtwoPlates = eightxoneBricks
    sixxtwoPlates = fourxoneBricks
    sixteenxoneBricks = (blockWidth * 2) + (blockHeight * 2)
    onexoneBricks += num_of_corners
    thinCornerPlates = num_of_corners
    flatonexfourPlates = 8 * (blockWidth + blockHeight) - 4

    return {
        235726:  int(cornerBlocks),
        6483102: int(cornerPlates),
        300526:  int(onexoneBricks),
        6178922: int(twoxoneBricksWithAxleHole),
        4109810: int(axlePegs),
        300826:  int(eightxoneBricks),
        301026:  int(fourxoneBricks),
        383226:  int(tenxtwoPlates),
        379526:  int(sixxtwoPlates),
        246526:  int(sixteenxoneBricks),
        6439175: int(thinCornerPlates),
        243126:  int(flatonexfourPlates)
    }