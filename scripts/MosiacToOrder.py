from collections import defaultdict
import numpy as np
from Util import GetPaletteDict
PALETTE_DICT = GetPaletteDict()

# return list of all lego pieces needed for both layers
def GenerateOrderList(fg_out_rgba, bg_rgba):
    """
    fg_out_rgba : PIL Image, mode RGBA
    bg_rgba     : PIL Image, mode RGBA (or RGB, alpha ignored)
    returns     : dict {lego_piece_id: count}
    """

    print("creating order list...")
    # -----------------
    # Layer 0: Base Layer (to place all plates on)
    # -----------------
    desiredSize = fg_out_rgba.size
    #order = GetBaseplatesForSize(desiredSize[0], desiredSize[1])
    order = defaultdict(int, GetBaseplatesForSize(desiredSize[0], desiredSize[1]))


    # -----------------
    # Layer 1: Background
    # -----------------
    bg_pixels = bg_rgba.load()
    for y in range(bg_rgba.height):
        for x in range(bg_rgba.width):
            rgb = bg_pixels[x, y][:3]
            piece_id = GetLegoPieceFromColor(rgb, PALETTE_DICT)
            order[piece_id] += 1

    # -----------------
    # Layer 2: Foreground
    # -----------------
    fg_pixels = fg_out_rgba.load()
    for y in range(fg_out_rgba.height):
        for x in range(fg_out_rgba.width):
            r, g, b, a = fg_pixels[x, y]
            if a == 0:
                continue
            piece_id = GetLegoPieceFromColor((r, g, b), PALETTE_DICT)
            order[piece_id] += 1

    print("returning order list...")
    return dict(order)


#helper function for determining piece from color
#all plates (1x1) are piece number 3024
#returns lego piece number for given rgb color via dictionary lookup
def GetLegoPieceFromColor(rgb, palette_dict=PALETTE_DICT):

    rgb = tuple(int(c) for c in rgb)

    try:
        return palette_dict[rgb]
    except KeyError:
        raise ValueError(f"RGB color {rgb} not found in LEGO palette")
    

# plate_id_by_size = {
#     (32, 32): 6139364,
#     (16, 32): 4269651,
#     (16, 16): 6004927,
#     (8, 16): 4654613,
#     (8, 8): 4210802,
#     (6, 16): 4226358,
#     (6, 14): 4210720,
#     (6, 12): 4256149,
#     (6, 10): 4211114,
#     (6, 8): 4210794,
#     (6, 6): 4211134,
#     (4, 12): 4210706,
#     (4, 10): 4211122,
#     (4, 8): 4211061,
#     (4, 6): 4211115,
#     (4, 4): 4243831,
#     (3, 3): 6039176,
#     (2, 16): 4210796,
#     (2, 14): 6000970,
#     (2, 12): 4211067,
#     (2, 10): 4210678,
#     (2, 8): 4210997,
#     (2, 4): 4211065,
#     (2, 3): 4211043,
#     (2, 2): 4211094,
#     (1, 12): 6133611,
#     (1, 10): 4257526,
#     (1, 8): 4210998,
#     (1, 6): 4211056,
#     (1, 5): 6413109,
#     (1, 4): 4211001,
#     (1, 3): 4211133,
#     (1, 2): 4211063,
#     (1, 1): 4210719,
# }


# this function has input of width and height in studs
# 
def GetBaseplatesForSize(width, height):
    """
    Given width and height in studs, return:
        dict {baseplate_piece_id: quantity}
    """

    blockWidth = width / 16
    blockHeight = height / 16

    numOfBlocks = int(blockWidth * blockHeight)
    numOfGreenConnectors = (int)((2 * (blockWidth - 1)) * blockHeight)
    numOfGreenPlates = (int)((blockWidth - 1) * blockHeight)
    numOfRedConnectors = (int)((2 * (blockHeight - 1)) * blockWidth)
    numOfRedPlates = (int)((blockHeight - 1) * blockWidth)
    twoxtwoPlates = numOfBlocks * 5
    nailHooks = min(numOfBlocks, 2)
    nailHookConnectors = nailHooks * 2

    blockID = 63202092
    nailHookID = 6302094
    nailHookConnectorID = 6279875
    greenConnectorID = 6526672
    redConnectorID = 6347789
    greenPlateID = 4621548
    redPlateID = 379521
    twoxtwoPlateID = 4211094
    # convert size counts → piece ID counts
    baseplates = defaultdict(int)
    baseplates[blockID] = numOfBlocks
    baseplates[nailHookID] = nailHooks
    baseplates[nailHookConnectorID] = nailHookConnectors
    baseplates[greenConnectorID] = numOfGreenConnectors
    baseplates[redConnectorID] = numOfRedConnectors
    baseplates[greenPlateID] = numOfGreenPlates
    baseplates[redPlateID] = numOfRedPlates
    baseplates[twoxtwoPlateID] = twoxtwoPlates


    return dict(baseplates)


