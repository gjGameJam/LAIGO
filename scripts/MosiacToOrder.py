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

PALETTE_DICT = GetPaletteDict()

# D-013: get_order_lists_file_path / empty_order_list_folder were the legacy CLI
# path (output_dir is None -> write to the project-root scratch dir). Removed;
# output_dir is now required and the API worker is the only caller.

def GenerateOrderList(fg_out_rgba, bg_rgba, want_frame, output_dir):
    """
    Analyzes images to produce a consolidated LEGO piece order.
    Optimized for CPU efficiency and minimal memory allocation.
    """
    # D-013: output_dir is required (CLI fallback removed).
    if output_dir is None:
        raise ValueError("output_dir is required")

    log_info("creating order list...")

    # 1. Initialize with Baseplates
    # We wrap the result in a defaultdict(int) to allow safe += operations later
    width, height = bg_rgba.size
    order = defaultdict(int, GetBaseplatesForSize(width, height))

    # 2. Layer: Background
    # Reshape (H, W, 3) -> (N, 3) for fast unique counting
    bg_arr = np.asarray(bg_rgba)[:, :, :3].reshape(-1, 3)
    unique_rgb, counts = np.unique(bg_arr, axis=0, return_counts=True)
    
    for rgb_row, count in zip(unique_rgb, counts):
        color_tuple = tuple(int(c) for c in rgb_row)
        try:
            piece_id = PALETTE_DICT[color_tuple]
            order[piece_id] += int(count)
        except KeyError:
            # D-008: fail loud rather than silently dropping pieces. Every mosaic
            # pixel is built FROM the palette, so an off-palette color is a
            # pipeline-invariant violation. Dropping it would ship a kit missing
            # bricks; raising fails the job into manifest_failed.json (no charge,
            # since checkout reads order_list.json only after job completion).
            raise RuntimeError(
                f"Mosaic pixel color {color_tuple} (layer=background) is not in "
                "LEGO_PALETTE_RGB_DICT. Refusing to ship a kit missing bricks."
            ) from None

    # 3. Layer: Foreground
    if fg_out_rgba is not None:
        fg_arr = np.asarray(fg_out_rgba)
        # Process only pixels with Alpha > 0
        visible_mask = fg_arr[:, :, 3] > 0
        fg_rgb = fg_arr[visible_mask, :3]
        
        unique_fg, fg_counts = np.unique(fg_rgb, axis=0, return_counts=True)
        for rgb_row, count in zip(unique_fg, fg_counts):
            color_tuple = tuple(int(c) for c in rgb_row)
            try:
                piece_id = PALETTE_DICT[color_tuple]
                order[piece_id] += int(count)
            except KeyError:
                # D-008: see background layer above — fail loud, never drop.
                raise RuntimeError(
                    f"Mosaic pixel color {color_tuple} (layer=foreground) is not "
                    "in LEGO_PALETTE_RGB_DICT. Refusing to ship a kit missing bricks."
                ) from None

    # 4. Layer: Frame
    if want_frame:
        frame_parts = GetFrameForSize(width, height)
        for pid, qty in frame_parts.items():
            order[pid] += qty

    # 5. Save Output
    output_json_path = Path(output_dir) / "OrderLists" / "order_list.json"

    # Pass the defaultdict directly to the utility
    SaveDictAsJsonsOptimized(order, output_json_path)
    
    log_info(f"Sum of all pieces: {sum(order.values())}")
    return order

def GetBaseplatesForSize(width, height):
    """Calculates structural baseplate components."""
    # Floor division (//) is faster than float division + int() cast
    blockWidth = width // 16
    blockHeight = height // 16

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
    blockWidth = width // 16
    blockHeight = height // 16
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