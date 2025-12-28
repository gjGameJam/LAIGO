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