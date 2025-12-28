from collections import defaultdict
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
    order = defaultdict(int)
    # -----------------
    # Layer 0: Base Layer (to place all plates on)
    # -----------------
    desiredSize = fg_out_rgba.size
    GetBaseplatesForSize(desiredSize[0], desiredSize[1])

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
# helper function for determining piece from color
# all plates (1x1) are piece number 3024
#returns lego piece number for given rgb color via dictionary lookup
def GetLegoPieceFromColor(rgb, palette_dict=PALETTE_DICT):

    rgb = tuple(int(c) for c in rgb)

    try:
        return palette_dict[rgb]
    except KeyError:
        raise ValueError(f"RGB color {rgb} not found in LEGO palette")

# this function has input of width and height in studs
# 
def GetBaseplatesForSize(width, height):
    """
    Given width and height in studs, return the appropriate baseplate piece ID.
    Common sizes:
        32x32 -> 6139364
        16x32 -> 4269651
        16x16 -> 6004927
        8x16 -> 4654613
        8x8 -> 4210802
        6x16 -> 4226358
        6x14 -> 4210720
        6x12 -> 4256149
        6x10 -> 4211114
        6x8 -> 4210794
        6x6 -> 4211134
        4x12 -> 4210706
        4x10 -> 4211122
        4x8 -> 4211061
        4x6 -> 4211115
        4x4 -> 4243831
        3x3 -> 6039176
        2x16 -> 4210796
        2x14 -> 6000970
        2x12 -> 4211067
        2x10 -> 4210678
        2x8 -> 4210997
        2x4 -> 4211065
        2x3 -> 4211043
        2x2 -> 4211094
        1x12 -> 6133611
        1x10 -> 4257526
        1x8 -> 4210998
        1x6 -> 4211056
        1x5 -> 6413109
        1x4 -> 4211001
        1x3 -> 4211133
        1x2 -> 4211063
        1x1 -> 4210719

    """
    if width <= 32 and height <= 32:
        return 91405  # 32x32 baseplate
    elif width <= 48 and height <= 48:
        return 91406  # 48x48 baseplate
    elif width <= 64 and height <= 64:
        return 91407  # 64x64 baseplate
    else:
        raise ValueError(f"No standard baseplate for size {width}x{height} studs")