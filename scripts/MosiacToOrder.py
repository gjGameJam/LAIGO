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
    baseplates = GetBaseplatesForSize(desiredSize[0], desiredSize[1])
    for piece_id, qty in baseplates.items():
        order[piece_id] += qty
        print("baseplate piece:", piece_id, "qty:", qty)

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
    

plate_id_by_size = {
    (32, 32): 6139364,
    (16, 32): 4269651,
    (16, 16): 6004927,
    (8, 16): 4654613,
    (8, 8): 4210802,
    (6, 16): 4226358,
    (6, 14): 4210720,
    (6, 12): 4256149,
    (6, 10): 4211114,
    (6, 8): 4210794,
    (6, 6): 4211134,
    (4, 12): 4210706,
    (4, 10): 4211122,
    (4, 8): 4211061,
    (4, 6): 4211115,
    (4, 4): 4243831,
    (3, 3): 6039176,
    (2, 16): 4210796,
    (2, 14): 6000970,
    (2, 12): 4211067,
    (2, 10): 4210678,
    (2, 8): 4210997,
    (2, 4): 4211065,
    (2, 3): 4211043,
    (2, 2): 4211094,
    (1, 12): 6133611,
    (1, 10): 4257526,
    (1, 8): 4210998,
    (1, 6): 4211056,
    (1, 5): 6413109,
    (1, 4): 4211001,
    (1, 3): 4211133,
    (1, 2): 4211063,
    (1, 1): 4210719,
}


# this function has input of width and height in studs
# 
def GetBaseplatesForSize(width, height):
    """
    Given width and height in studs, return:
        dict {baseplate_piece_id: quantity}
    """

    # Sort plate sizes largest → smallest by area
    plates = sorted(
        plate_id_by_size.keys(),
        key=lambda s: s[0] * s[1],
        reverse=True
    )

    # remaining untiled rectangles
    rects = [(width, height)]

    # counts by (w, h)
    size_counts = defaultdict(int)

    for pw, ph in plates:
        new_rects = []

        for rw, rh in rects:
            nx = rw // pw
            ny = rh // ph
            count = nx * ny

            if count:
                size_counts[(pw, ph)] += count

                # right strip
                rem_w = rw - nx * pw
                if rem_w > 0:
                    new_rects.append((rem_w, ny * ph))

                # bottom strip
                rem_h = rh - ny * ph
                if rem_h > 0:
                    new_rects.append((rw, rem_h))
            else:
                new_rects.append((rw, rh))

        rects = new_rects

    # convert size counts → piece ID counts
    baseplates = defaultdict(int)

    for (w, h), qty in size_counts.items():
        # normalize orientation if needed
        key = (w, h)
        if key not in plate_id_by_size:
            key = (h, w)

        piece_id = plate_id_by_size[key]
        baseplates[piece_id] += qty * 2  # two layers to ensure solid base

    return dict(baseplates)


