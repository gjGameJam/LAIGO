from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import random
import math
from .Util import GetOutputPathDir, log_debug, log_info, log_error
from . import piece_specs as ps
# -----------------------------
# CONSTANTS
# -----------------------------
PLATE_WIDTH = 30          # width of top face
PLATE_HEIGHT = 15         # vertical drop of front face
PLATE_HALF_WIDTH = PLATE_WIDTH / 2
PLATE_HALF_HEIGHT = PLATE_HEIGHT / 2
STARTING_X = 50
STARTING_Y = 400
STUD_WIDTH = PLATE_WIDTH * 0.35
STUD_HEIGHT = PLATE_HEIGHT * 0.30
STUD_OFFSET_X = (PLATE_WIDTH - STUD_WIDTH) / 2   # centers the stud
STUD_OFFSET_Y = 0
STUD_NECK_HEIGHT = PLATE_HEIGHT * 0.3
STUD_NECK_WIDTH  = STUD_WIDTH * 0.75
STUD_NECK_OFFSET = (STUD_WIDTH - STUD_NECK_WIDTH) / 2
STUD_NECK_RISE = STUD_HEIGHT * 0.35   # how high into the stud the neck starts

def get_file_name(step_num, output_dir=None):
    if output_dir is not None:
        folder = Path(f"{output_dir}/Instructions")
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{step_num}.png"
    else:
        return f"{GetOutputPathDir()}/Instructions/{step_num}.png"

# Bundled in the repo (scripts/fonts/) so instruction text renders identically on
# local (Windows) and the deployed Linux service. Liberation Sans is a free,
# metric-compatible Arial clone, so the layout matches the previous arial.ttf look.
_BUNDLED_FONT = Path(__file__).parent / "fonts" / "LiberationSans-Regular.ttf"

# helper function to get the instruction font at a given size
def get_font(size=32):
    try:
        # Load the repo-bundled TTF by absolute path -> same on every platform.
        return ImageFont.truetype(str(_BUNDLED_FONT), size)
    except OSError:
        # Last resort if the bundled file is ever missing: Pillow's built-in
        # TrueType, which (unlike the old load_default()) still honors `size`.
        try:
            return ImageFont.load_default(size)   # Pillow >= 10.1
        except TypeError:
            return ImageFont.load_default()        # very old Pillow: bitmap fallback

def save_img_and_increment_step(img, step, output_dir=None, copy=True):
    save_target = img.copy() if copy else img
    width, height = save_target.size
    draw = ImageDraw.Draw(save_target)
    font = get_font(32)
    x_middle = width / 2
    x_offset = -len(str(step)) * 5
    offset = 50
    draw.text((x_middle + x_offset, height - offset), str(step), fill="black", font=font)
    saveName = get_file_name(step, output_dir)
    save_target.convert("RGB").save(saveName, format="PNG", compress_level=1)
    return (step + 1)


def to_rgb(color):
    return tuple(int(c * 255) for c in color)


def to_pillow(x, y):
    return (x, 792 - y) #silly little me was using reportwhateveritwas canvas before and y is flipped


def get_img_and_draw(step, wantClear, output_dir=None):
    if (step == 1 or wantClear): #if on first step or user wants clear img, then make empty IMG
        # create image with specified size and background color
        bg_color = (255, 255, 255, 255) # white background
        image_size = (612, 792)
        img = Image.new("RGBA", image_size, bg_color)
    else: #already has previous step that user wants to use so just open previous step
        fileName = get_file_name(step - 1, output_dir)
        img = Image.open(fileName).convert("RGBA")
    return img, ImageDraw.Draw(img) #return imagedraw to allow user to modify current step before saving


def block_center_xy(bx, by, xOffset, yOffset):
    return get_block_xy(bx + xOffset + 0.5, by + yOffset + 0.5)


def _draw_baseplate_connector_pin(draw, xSpot, ySpot, color, *, mirror=False):
    """Flat 'back of connector' pin for the baseplate-setup step: the original
    4-point parallelogram plus a wider flat collar band over 47.5%-75% of the pin
    length (0% = oval-hole end, 100% = page-middle end). Same flat fill `color`
    and black (10,10,10) outline as the rest of the pin, drawn as ONE polygon so
    the silhouette outline stays clean. mirror=False -> green; mirror=True -> red
    (x deltas negated), which reproduces the old redPin when collar_offset == 0."""
    P0, P1, P2, P3 = (-3.0, 9.0), (-30.0, 25.0), (-22.0, 29.0), (5.0, 13.0)
    collar_offset = 2.5  # extra half-width (px) the collar bulges out each side

    lx, ly = P0[0] - P1[0], P0[1] - P1[1]      # long axis = (27, -16)
    L = math.hypot(lx, ly)
    nx, ny = -ly / L, lx / L                   # top->bottom unit normal
    ox, oy = nx * collar_offset, ny * collar_offset

    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    top_lo, top_hi = lerp(P1, P0, 0.475), lerp(P1, P0, 0.75)  # hole edge extended 10% longer
    bot_lo, bot_hi = lerp(P2, P3, 0.475), lerp(P2, P3, 0.75)

    pts = [
        P1,
        top_lo, (top_lo[0] - ox, top_lo[1] - oy),
        (top_hi[0] - ox, top_hi[1] - oy), top_hi,
        P0, P3,
        bot_hi, (bot_hi[0] + ox, bot_hi[1] + oy),
        (bot_lo[0] + ox, bot_lo[1] + oy), bot_lo,
        P2,
    ]
    if mirror:
        pts = [(-dx, dy) for dx, dy in pts]
    draw.polygon([(xSpot + dx, ySpot + dy) for dx, dy in pts], color, outline=(10, 10, 10))


def draw_baseplate_bottom(draw, size=16, color=(0.2, 0.2, 0.2), case=0):
    """
    Draw a single monolithic NxN LEGO baseplate (height = 1 plate)
    with NxN studs.
    """
    xOffset = .5
    yOffset = -.5
    x, y = get_block_xy(xOffset, yOffset)
    w = PLATE_WIDTH * size
    hw = PLATE_HALF_WIDTH * size
    hh = PLATE_HALF_HEIGHT * size
    # Shading
    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))
    green_color = to_rgb((.647, .792, .09))
    red_color = to_rgb((0.8, 0.1, 0.1))
    # Shared 3D thickness for the center block AND the back-corner pads/rails
    # below. The corner squares are already in PIL space (y grows down), so a
    # face that "drops down" on screen adds +depth to a point's y.
    depth = PLATE_HEIGHT * 0.8  # slightly thinner than a plate

    # -----------------
    # Baseplate faces
    # -----------------
    top = [
        to_pillow(x,           y),
        to_pillow(x + hw,      y + hh),
        to_pillow(x + w,       y),
        to_pillow(x + hw,      y - hh),
    ]
    right = [
        to_pillow(x + w,       y),                     # top-right
        to_pillow(x + hw,      y - hh),                # top-left
        to_pillow(x + hw,      y - hh - PLATE_HEIGHT), # bottom-left
        to_pillow(x + w,       y - PLATE_HEIGHT),      # bottom-right
    ]
    front = [
        to_pillow(x,           y),                     # top-left
        to_pillow(x + hw,      y - hh),                # top-right
        to_pillow(x + hw,      y - hh - PLATE_HEIGHT), # bottom-right
        to_pillow(x,           y - PLATE_HEIGHT),      # bottom-left
    ]
   
    draw.polygon(top, top_color, outline=(10,10,10))
    draw.polygon(right, right_color, outline=(10,10,10))
    draw.polygon(front, front_color, outline=(10,10,10))


    islands = [
        (.5, .5),     # bottom-left
        (.5, 14.5),    # top-left
        (14.5, 14.5),   # top-right
        (14.5, .5),    # bottom-right
    ]
    # c.setStrokeColor(Color(0.05, 0.05, 0.05))
    # c.setLineWidth(1.25)


    iso_points = [block_center_xy(bx, by, xOffset, yOffset) for bx, by in islands]

    for i in range(len(iso_points)):
        x1, y1 = iso_points[i]
        x2, y2 = iso_points[(i + 1) % len(iso_points)]
        px1, py1 = to_pillow(x1, y1)
        px2, py2 = to_pillow(x2, y2)
        draw.line([(px1, py1), (px2, py2)], fill=(10,10,10), width=1)


    bottom_left_square = [
        to_pillow(x + 0,               y + 0),
        to_pillow(x + PLATE_WIDTH, y + PLATE_HALF_WIDTH),
        to_pillow(x + PLATE_WIDTH * 2,      y + 0),
        to_pillow(x + PLATE_WIDTH, y - PLATE_HALF_WIDTH)
    ]
    draw.polygon(bottom_left_square, top_color, outline=(10,10,10))

    dx = -PLATE_WIDTH * 7 #- PLATE_HALF_WIDTH
    dy = -PLATE_WIDTH * 4 + PLATE_HALF_WIDTH
    top_left_square = [(x - dx, y - dy) for x, y in bottom_left_square]
    draw.polygon(top_left_square, top_color, outline=(10,10,10))

    dx = -PLATE_WIDTH * 14 #- PLATE_HALF_WIDTH
    dy = 0
    top_right_square = [(x - dx, y - dy) for x, y in bottom_left_square]
    draw.polygon(top_right_square, top_color, outline=(10,10,10))


    dx = -PLATE_WIDTH * 7 #- PLATE_HALF_WIDTH
    dy = PLATE_WIDTH * 4 - PLATE_HALF_WIDTH
    bottom_right_square = [(x - dx, y - dy) for x, y in bottom_left_square]
    draw.polygon(bottom_right_square, top_color, outline=(10,10,10))

    # -----------------------------------------------------------------
    # 3D thickness for the back half of the corner border.
    # Each flat corner diamond above is [left, top, right, bottom] in PIL
    # points; a "depth wall" on a lower edge drops that edge down by `depth`
    # (mirrors the center block's big_front / big_right). The front (bottom)
    # corner and the two front rails are intentionally left flat -- their
    # walls would fall off the baseplate's front edge.
    # -----------------------------------------------------------------
    def _drop(pt, d=depth):
        return (pt[0], pt[1] + d)


    bc = bottom_right_square           # back corner       [left, top, right, bottom]
    bl = bottom_left_square            # left side corner  [left, top, right, bottom]
    tr = top_right_square              # right side corner [left, top, right, bottom]

    # Depth walls are drawn first; the pad tops are re-drawn afterwards so each
    # wall's inner half is occluded by its own pad. That occlusion is what turns
    # the side-corner walls into the visible "triangle behind the row".
    # Shading follows the center block's "light from the right" convention:
    # left-half walls use the darker front_color, right-half walls right_color.

    # Back rows: one depth wall per back edge, along the plate edge from each
    # side corner's top vertex to the back corner's near vertex (renders the
    # rim's visible face). Back-left face points SE (right_color), back-right SW
    # (front_color).
    draw.polygon([bl[1], bc[0], _drop(bc[0]), _drop(bl[1])], right_color, outline=(10, 10, 10))
    draw.polygon([tr[1], bc[2], _drop(bc[2]), _drop(tr[1])], front_color, outline=(10, 10, 10))

    # Back corner: both lower edges (they meet at its center-facing bottom vertex).
    draw.polygon([bc[0], bc[3], _drop(bc[3]), _drop(bc[0])], front_color, outline=(10, 10, 10))
    draw.polygon([bc[2], bc[3], _drop(bc[3]), _drop(bc[2])], right_color, outline=(10, 10, 10))

    # Side corners: a single triangle behind each rail (left: NE edge bl1-bl2;
    # right: NW edge tr1-tr0). The inner vertex drops straight down only as far
    # as the pad's bottom vertex -- which sits on the plate's front edge -- so
    # the triangle ends exactly where it meets the front row (corner raised to
    # the same level as the front rows, no gap or overshoot). The top re-draw
    # clips the inner half, leaving the triangle flush against its rail.
    left_tip = (bl[2][0], bl[3][1])    # inner vertex bl2 dropped to bottom-vertex level
    right_tip = (tr[0][0], tr[3][1])   # inner vertex tr0 dropped to bottom-vertex level
    draw.polygon([bl[1], bl[2], left_tip, _drop(bl[1])], front_color, outline=(10, 10, 10))
    draw.polygon([tr[1], tr[0], right_tip, _drop(tr[1])], right_color, outline=(10, 10, 10))

    # Re-draw the three back-half pad tops crisp over the walls.
    draw.polygon(bc, top_color, outline=(10, 10, 10))
    draw.polygon(bl, top_color, outline=(10, 10, 10))
    draw.polygon(tr, top_color, outline=(10, 10, 10))


    # -----------------
    # Big middle 3D square
    # -----------------


    middle = x + PLATE_WIDTH * 6
    top_hh = PLATE_HALF_WIDTH * 2
    # `depth` is defined once near the top of this function (shared with the
    # back-corner pads/rails).


    # --- Top face ---
    big_top = [
        to_pillow(middle + 0,               y + 0),
        to_pillow(middle + PLATE_WIDTH * 2, y + top_hh),
        to_pillow(middle + PLATE_WIDTH * 4, y + 0),
        to_pillow(middle + PLATE_WIDTH * 2, y - top_hh),
    ]


    # --- Right face ---
    big_right = [
        to_pillow(middle + PLATE_WIDTH * 4, y + 0),
        to_pillow(middle + PLATE_WIDTH * 2, y - top_hh),
        to_pillow(middle + PLATE_WIDTH * 2, y - top_hh - depth),
        to_pillow(middle + PLATE_WIDTH * 4, y - depth),
    ]




    # --- Front face ---
    big_front = [
        to_pillow(middle + 0,               y + 0),
        to_pillow(middle + PLATE_WIDTH * 2, y - top_hh),
        to_pillow(middle + PLATE_WIDTH * 2, y - top_hh - depth),
        to_pillow(middle + 0,               y - depth),
    ]




    draw.polygon(big_top,   top_color, outline=(10,10,10))
    draw.polygon(big_right, right_color, outline=(10,10,10))
    draw.polygon(big_front, front_color, outline=(10,10,10))


    # -----------------
    # Studs (top only)
    # -----------------
    studs = [
        (0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (3, 0), (4,0), (5,0), (6,0), (7,0), (8,0), (9,0), (10,0), (11,0), (12,0), (13,0), (14,0), (15,0),
        (14, 1), (15, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (0, 7), (0, 8), (0, 9), (0, 10), (0, 11), (0, 12), (0, 13), (0, 14), (0, 15), (15, 1),
        (15, 2), (15, 3), (15, 4), (15, 5), (15, 6), (15, 7), (15, 8), (15, 9), (15, 10), (15, 11), (15, 12), (15, 13), (15, 14), (15, 15),
        (1, 15), (2, 15), (3, 15), (4, 15), (5, 15), (6, 15), (7, 15), (8, 15), (9, 15), (10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (1, 14), (14, 14),
        (6, 6), (6, 7), (6, 8), (6, 9), (7, 6), (7, 7), (7, 8), (7, 9), (8, 6), (8, 7), (8, 8), (8, 9), (9, 6), (9, 7), (9, 8), (9, 9)
    ]
    #offset stud tops to look like holes
    dx = 0.2
    dy = -0.2
    studs = [(x - dx, y - dy) for x, y in studs]


    for sx, sy in studs:
        sx_iso, sy_iso = get_block_xy(
            sx + xOffset,
            sy + yOffset
        )
        stud_left = sx_iso + STUD_OFFSET_X
        stud_right = stud_left + STUD_WIDTH
        draw.ellipse(
            [stud_left, sy_iso + STUD_OFFSET_Y, stud_right, sy_iso + STUD_OFFSET_Y + STUD_HEIGHT],
            fill=(25,25,25),
            outline=(10,10,10)
        )
 
    #add ellipses for the 12 holes on the front and right of the baseplate
    side_holes = [
        (-1.3, 2.9), (-1.3, 3.9), (-1.3, 4.9), (-1.3, 5.9), (-1.3, 6.9), (-1.3, 7.9), (-1.3, 8.9), (-1.3, 9.9), (-1.3, 10.9), (-1.3, 11.9), (-1.3, 12.9), (-1.3, 13.9),
 
        (1.15, 16.35), (2.15, 16.35), (3.15, 16.35), (4.15, 16.35), (5.15, 16.35), (6.15, 16.35), (7.15, 16.35), (8.15, 16.35), (9.15, 16.35), (10.15, 16.35), (11.15, 16.35), (12.15, 16.35)
    ]


    green_holes = [
        (-1.3, 3.9), (-1.3, 12.9)
    ]


    red_holes = [
        (2.15, 16.35), (11.15, 16.35)
    ]
 
    for sx, sy in side_holes:
        sx_iso, sy_iso = get_block_xy(
            sx + xOffset,
            sy + yOffset
        )
        stud_left = sx_iso + STUD_OFFSET_X
        stud_right = stud_left + STUD_WIDTH
        VERTICAL_WIDTH  = STUD_WIDTH * 0.4
        VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
 
        cx = stud_left + STUD_WIDTH / 2
        cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
        draw.ellipse(
            [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
            fill=(25,25,25),
            outline=(10,10,10)
        )


    #case 0-3 for baseplate connections
    if (case == 0):
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(draw, 0, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 0, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 7, 7, 2, 2, (0.44, 0.44, 0.44))
        #red and green plates upside down
        draw_plate_sized_upside_down(draw, -1, 6, 2, 4, (.647, .792, .09))
        draw_plate_sized_upside_down(draw, 6, 15, 4, 2, (0.8, 0.1, 0.1))
        #red and green connectors
        #red connectors
        for sx, sy in red_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=red_color,
                outline=(10,10,10)
            )




        # draw back of green connectors
        _draw_baseplate_connector_pin(draw, x + 100, y + 1, green_color)


        _draw_baseplate_connector_pin(draw, x + 235, y + 69, green_color)
        # draw back of red connectors
        _draw_baseplate_connector_pin(draw, x + 245, y + 69, red_color, mirror=True)
        _draw_baseplate_connector_pin(draw, x + 380, y + 1, red_color, mirror=True)
        #then do green holes
        for sx, sy in green_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=green_color,
                outline=(10,10,10)
            )
        return
    if (case == 1):
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(draw, 0, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 0, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 7, 7, 2, 2, (0.44, 0.44, 0.44))
        #red plates upside down
        draw_plate_sized_upside_down(draw, 6, 15, 4, 2, (0.8, 0.1, 0.1))
        #red and green connectors
        #red connectors
        for sx, sy in red_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=red_color,
                outline=(10,10,10)
            )
        # draw back of red connectors
        _draw_baseplate_connector_pin(draw, x + 245, y + 69, red_color, mirror=True)
        _draw_baseplate_connector_pin(draw, x + 380, y + 1, red_color, mirror=True)
        return
    if (case == 2):
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(draw, 0, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 0, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 7, 7, 2, 2, (0.44, 0.44, 0.44))
        #green plates upside down
        draw_plate_sized_upside_down(draw, -1, 6, 2, 4, (.647, .792, .09))
        #green connectors no red
        for sx, sy in green_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=green_color,
                outline=(10,10,10)
            )
        # draw back of green connectors
        _draw_baseplate_connector_pin(draw, x + 100, y + 1, green_color)
        _draw_baseplate_connector_pin(draw, x + 235, y + 69, green_color)
        return
    if (case == 3):
        #no connectors (already handled)
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(draw, 0, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 0, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 14, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 0, 14, 2, 2, (0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(draw, 7, 7, 2, 2, (0.44, 0.44, 0.44))
        return


def draw_baseplate_top(draw, size=16, color=(0.2, 0.2, 0.2), case=0):
    """
    Draw a single monolithic NxN LEGO baseplate (height = 1 plate)
    with NxN studs.
    """


    if (case == 0):
        #red and green connectors
        draw_plate_sized(draw, 16.5, 4.5, 2, 4, 1, (.647, .792, .09))   # 2x4
        draw_plate_sized(draw, 7.5, -2.5, 4, 2, 1, (0.8, 0.1, 0.1))   # 4x2      
    if (case == 1):
        #red connectors no green
        draw_plate_sized(draw, 7.5, -2.5, 4, 2, 1, (0.8, 0.1, 0.1))   # 4x2
    if (case == 2):
        #green connectors no red
        draw_plate_sized(draw, 16.5, 4, 2, 4, 1, (.647, .792, .09))   # 2x4
    if (case == 3):
        #no connectors (already handled)
        pass




    #2x2 grey plates on bottom of plate
    draw_plate_sized(draw, 1.5, -1.5, 2, 2, 1, (0.44, 0.44, 0.44))
    draw_plate_sized(draw, 15.5, -1.5, 2, 2, 1, (0.44, 0.44, 0.44))
    draw_plate_sized(draw, 15.5, 12.5, 2, 2, 1, (0.44, 0.44, 0.44))
   
    xOffset = .5
    yOffset = -.5
    x, y = get_block_xy(xOffset, yOffset)
    w = PLATE_WIDTH * size
    hw = PLATE_HALF_WIDTH * size
    hh = PLATE_HALF_HEIGHT * size
    # Shading
    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))
    green_color = to_rgb((.647, .792, .09))
    red_color = to_rgb((0.8, 0.1, 0.1))
    # -----------------
    # Baseplate faces
    # -----------------
    top = [
        to_pillow(x,           y),
        to_pillow(x + hw,      y + hh),
        to_pillow(x + w,       y),
        to_pillow(x + hw,      y - hh),
    ]
    right = [
        to_pillow(x + w,       y),                     # top-right
        to_pillow(x + hw,      y - hh),                # top-left
        to_pillow(x + hw,      y - hh - PLATE_HEIGHT), # bottom-left
        to_pillow(x + w,       y - PLATE_HEIGHT),      # bottom-right
    ]
    front = [
        to_pillow(x,           y),                     # top-left
        to_pillow(x + hw,      y - hh),                # top-right
        to_pillow(x + hw,      y - hh - PLATE_HEIGHT), # bottom-right
        to_pillow(x,           y - PLATE_HEIGHT),      # bottom-left
    ]
    draw.polygon(top, top_color, outline=(10,10,10))
    draw.polygon(right, right_color, outline=(10,10,10))
    draw.polygon(front, front_color, outline=(10,10,10))
     
   
    # -----------------
    # Studs (top only)
    # -----------------
   
    for sy in range(size):
        for sx in range(size):
            sx_iso, sy_iso = get_block_xy(sx + xOffset, sy + yOffset)
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            # --- Stud front wall (same as draw_plate) ---
            wall_top = sy_iso + 2.5
            wall_bottom = sy_iso - 2.5
            front_wall = [
                (stud_left, wall_top + 5), (stud_right, wall_top + 5), (stud_right, wall_bottom + 5), (stud_left, wall_bottom + 5)
            ]
            #c.setFillColor(Color(0.2, 0.2, 0.2))
            draw.polygon(front_wall, front_color, outline=(10,10,10))
            top_stud_color = to_rgb((0.25, 0.25, 0.25))
            draw.ellipse(
                [stud_left, sy_iso + STUD_OFFSET_Y, stud_right, sy_iso + STUD_OFFSET_Y + STUD_HEIGHT],
                fill=top_stud_color,
                outline=(10,10,10)
            )
 
    #add ellipses for the 12 holes on the front and right of the baseplate
    hole_color = to_rgb((0.1, 0.1, 0.1))
    side_holes = [
        (-1.3, 2.9), (-1.3, 3.9), (-1.3, 4.9), (-1.3, 5.9), (-1.3, 6.9), (-1.3, 7.9), (-1.3, 8.9), (-1.3, 9.9), (-1.3, 10.9), (-1.3, 11.9), (-1.3, 12.9), (-1.3, 13.9),
 
        (1.15, 16.35), (2.15, 16.35), (3.15, 16.35), (4.15, 16.35), (5.15, 16.35), (6.15, 16.35), (7.15, 16.35), (8.15, 16.35), (9.15, 16.35), (10.15, 16.35), (11.15, 16.35), (12.15, 16.35)
    ]
 
    for sx, sy in side_holes:
        sx_iso, sy_iso = get_block_xy(
            sx + xOffset,
            sy + yOffset
        )
        stud_left = sx_iso + STUD_OFFSET_X
        stud_right = stud_left + STUD_WIDTH
        VERTICAL_WIDTH  = STUD_WIDTH * 0.4
        VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
 
        cx = stud_left + STUD_WIDTH / 2
        cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
 
        draw.ellipse(
            [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
            fill=hole_color,
            outline=(10,10,10)
        )
   
    green_holes = [
        (2.15, 16.35), (11.15, 16.35)
    ]




    red_holes = [
        (-1.3, 3.9), (-1.3, 12.9)
    ]


    #case 0-3 for baseplate connections
    if (case == 0):
        #red and green connectors
        #red connectors
        for sx, sy in red_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=red_color,
                outline=(10,10,10)
            )


        #then do green holes
        for sx, sy in green_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=green_color,
                outline=(10,10,10)
            )
        return
    if (case == 1):
        #red connectors
        for sx, sy in red_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=red_color,
                outline=(10,10,10)
            )
        return
    if (case == 2):
        #green connectors no red
        for sx, sy in green_holes:
            sx_iso, sy_iso = get_block_xy(
                sx + xOffset,
                sy + yOffset
            )
            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            VERTICAL_WIDTH  = STUD_WIDTH * 0.4
            VERTICAL_HEIGHT = STUD_HEIGHT * 2.0
   
            cx = stud_left + STUD_WIDTH / 2
            cy = sy_iso + STUD_OFFSET_Y + STUD_HEIGHT / 2
   
            draw.ellipse(
                [cx - VERTICAL_WIDTH/2, cy - VERTICAL_HEIGHT/2, cx + VERTICAL_WIDTH/2, cy + VERTICAL_HEIGHT/2],
                fill=green_color,
                outline=(10,10,10)
            )
    if (case == 3):
        #no connectors (already handled)
        return


def draw_plate_sized(draw, blockX, blockY, width, length, height, color):
    PLATE_Z = PLATE_HEIGHT * 0.33 * height


    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))


    # --- Top corners ---
    p0 = get_block_xy(blockX, blockY)                   # front-left
    p1 = get_block_xy(blockX + width, blockY)           # front-right
    p2 = get_block_xy(blockX + width, blockY + length)  # back-right
    p3 = get_block_xy(blockX, blockY + length)          # back-left


    # --- Top ---
    draw.polygon([to_pillow(p0[0], p0[1]), to_pillow(p1[0], p1[1]), to_pillow(p2[0], p2[1]), to_pillow(p3[0], p3[1])], top_color, outline=(10,10,10))
    # --- FRONT FACE (correct: +X edge) ---
    front = [
        to_pillow(p0[0], p0[1]),
        to_pillow(p1[0], p1[1]),
        to_pillow(p1[0], p1[1] - PLATE_Z),
        to_pillow(p0[0], p0[1] - PLATE_Z),
    ]
    draw.polygon(front, front_color, outline=(10,10,10))
    # --- RIGHT FACE (diagonal depth) ---
    right = [
        to_pillow(p1[0], p1[1]),
        to_pillow(p2[0], p2[1]),
        to_pillow(p2[0], p2[1] - PLATE_Z),
        to_pillow(p1[0], p1[1] - PLATE_Z),
    ]
    draw.polygon(right, right_color, outline=(10,10,10))


    # --- Studs ---
    for sy in range(length):
        for sx in range(width):
            sx_iso, sy_iso = get_block_xy(blockX + sx, blockY + sy)


            cx = sx_iso + PLATE_HALF_WIDTH
            cy = sy_iso + STUD_OFFSET_Y
            draw_stud_with_neck(draw, cx, cy, top_color, front_color, right_color)


def draw_plate_sized_upside_down(draw, blockX, blockY, width, height, color):
    PLATE_Z = PLATE_HEIGHT * 0.33


    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))


    blockX = blockX + .31
    blockY = blockY - .31
    # --- Top corners ---
    p0 = get_block_xy(blockX,           blockY + height)
    p1 = get_block_xy(blockX + width,   blockY + height)
    p2 = get_block_xy(blockX + width,   blockY)
    p3 = get_block_xy(blockX,           blockY)


    # --- Top ---
    draw.polygon([p0, p1, p2, p3], top_color, outline=(10,10,10))
    # --- FRONT FACE (correct: +X edge) ---
    front = [
        p0,
        p1,
        (p1[0], p1[1] + PLATE_Z),
        (p0[0], p0[1] + PLATE_Z),
    ]
    draw.polygon(front, front_color, outline=(10,10,10))
    # --- RIGHT FACE (diagonal depth) ---
    right = [
        p3,                               # top-left of the face
        p0,                               # bottom-left of the face
        (p0[0], p0[1] + PLATE_Z),         # bottom-left dropped
        (p3[0], p3[1] + PLATE_Z),         # top-left dropped
    ]
    draw.polygon(right, right_color, outline=(10,10,10))


    # =====================
    # HOLES (stud-sized)
    # =====================
    dx = 0.1
    dy = -0.1
    hole_color = ((int)(front_color[0] * 0.50), (int)(front_color[1] * 0.50), (int)(front_color[2] * 0.50))
    for sy in range(height):
        for sx in range(width):
            sx_hole = blockX + sx - dx
            sy_hole = blockY + sy - dy
            sx_iso, sy_iso = get_block_xy(sx_hole, sy_hole)
            stud_left  = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH
            draw.ellipse(
                [stud_left, sy_iso + STUD_OFFSET_Y - PLATE_Z, stud_right, sy_iso + STUD_OFFSET_Y + STUD_HEIGHT - PLATE_Z],
                fill=hole_color,
                outline=(10,10,10)
            )


def draw_stud_with_neck(draw, cx, cy, top_color, front_color, right_color):
    # --- Stud top ---
    left  = cx - STUD_WIDTH / 2
    right = cx + STUD_WIDTH / 2
    # --- Neck geometry (spawn higher) ---
    neck_left  = cx - STUD_WIDTH / 2
    neck_right = cx + STUD_WIDTH / 2
    neck_top = cy + STUD_NECK_RISE
    neck_bot = neck_top - STUD_NECK_HEIGHT
    # Front face
    front = [
        to_pillow(neck_left,  neck_top),
        to_pillow(neck_right, neck_top),
        to_pillow(neck_right, neck_bot),
        to_pillow(neck_left,  neck_bot),
    ]
    draw.polygon(front, front_color, outline=(10,10,10))
    flipped_top = 2 * 400 - (cy + STUD_HEIGHT * 3)
    flipped_bottom = 2 * 400 - (cy + STUD_HEIGHT * 2)
    draw.ellipse(
        [left, flipped_top, right, flipped_bottom],
        fill=top_color,
        outline=(10,10,10)
    )


def draw_stud(draw, cx, cy, blockZ, color):
    # Convert stud center from stud coords -> pixel coords

    if blockZ != 0:
        cy += PLATE_HALF_HEIGHT * blockZ

    top_color   = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))

    #create updated stud and neck sizing
    neck_left  = cx + STUD_WIDTH * 1.5
    neck_right = cx + STUD_WIDTH * 4.5
    neck_top = cy + STUD_NECK_HEIGHT * 5
    neck_bot = neck_top - STUD_NECK_HEIGHT * 3
    front = [
        to_pillow(neck_left,  neck_top),
        to_pillow(neck_right, neck_top),
        to_pillow(neck_right, neck_bot),
        to_pillow(neck_left,  neck_bot),
    ]
    draw.polygon(front, front_color, outline=(10,10,10))

    draw.ellipse(
        [neck_left, 786 - neck_top, neck_right, 786 - neck_bot],
        fill=top_color,
        outline=(10,10,10)
    )


# Per-process cache of rendered baseplate-setup canvases. Keys:
#   "stepA"          — blank baseplate piece (case-independent)
#   "stepB:{case}"   — positioned baseplate with connectors
#   "stepC:{case}"   — top-of-baseplate view (also returned as foundation canvas)
# Values are pristine PIL Images (no step number stamp), keyed only by baseplate
# geometry — job-independent templates, so reuse across jobs is always safe
# (callers draw on img.copy() and never mutate the cached original). On 3.12+ the
# worker respawns per job (max_tasks_per_child=1) so the cache is rebuilt fresh;
# on ≤3.11 the worker persists and the cache is reused across jobs — still no
# cross-job leakage, since the templates carry no per-job state. See D-030.
# Memory cost: up to 9 entries × ~2 MB each = ~18 MB.
_baseplate_setup_cache = {}


def _render_baseplate_step_a():
    img, _ = get_img_and_draw(step=1, wantClear=True)
    draw = ImageDraw.Draw(img)
    draw_baseplate_bottom(draw, 16, (0.2, 0.2, 0.2), -1)
    return img


def _render_baseplate_step_b(case):
    img = _get_or_build_baseplate("stepA", _render_baseplate_step_a).copy()
    draw = ImageDraw.Draw(img)
    draw_baseplate_bottom(draw, 16, (0.2, 0.2, 0.2), case)
    return img


def _render_baseplate_step_c(case):
    img, _ = get_img_and_draw(step=1, wantClear=True)
    draw = ImageDraw.Draw(img)
    draw_baseplate_top(draw, 16, (0.2, 0.2, 0.2), case)
    return img


def _get_or_build_baseplate(key, builder):
    if key not in _baseplate_setup_cache:
        _baseplate_setup_cache[key] = builder()
    return _baseplate_setup_cache[key]


def _baseplate_step_b_parts(case):
    """Pieces introduced when attaching the baseplate to its neighbors (the step
    after the bare baseplate): always 5x 2x2 reinforcement plates, plus the
    connectors/bridge plates this block needs for its position. `case` encodes
    neighbors — 0 = right+bottom, 1 = bottom only (last column), 2 = right only
    (last row), 3 = none (corner). Green = horizontal/right seam, red =
    vertical/bottom seam, matching draw_baseplate_bottom and the per-block counts
    in MosiacToOrder.GetBaseplatesForSize."""
    parts = [(ps.SPEC_BY_ELEMENT[4211094], 5)]            # 2x2 plates, every block
    if case in (0, 2):                                    # has a right neighbor -> green
        parts.append((ps.SPEC_BY_ELEMENT[6526672], 2))   # green connectors
        parts.append((ps.SPEC_BY_ELEMENT[4621548], 1))   # green bridge plate
    if case in (0, 1):                                    # has a bottom neighbor -> red
        parts.append((ps.SPEC_BY_ELEMENT[6347789], 2))   # red connectors
        parts.append((ps.SPEC_BY_ELEMENT[379521], 1))    # red bridge plate
    return parts


#function for gnerating instructions for baseplate setup and returns step after incrementing parameter for each step
def generate_baseplate_setup(step, case, output_dir=None, minimap=None):
    # minimap (optional): (n_w, n_h, cur_w, cur_h) "you are here" block grid.
    # Drawn onto each .copy() below (never the cached template); since img_c is
    # returned as the column loop's foundation, the minimap rides along onto every
    # column page for free — no per-column minimap call needed.
    # Step 1: blank canvas, show the standalone baseplate piece (case-independent)
    img_a = _get_or_build_baseplate("stepA", _render_baseplate_step_a).copy()
    draw_a = ImageDraw.Draw(img_a)
    if minimap is not None:
        draw_block_minimap(draw_a, *minimap)
    # parts legend: this step places the 16x16 baseplate
    draw_step_parts_legend(draw_a, [(ps.SPEC_BY_ELEMENT[6302092], 1)])
    step = save_img_and_increment_step(img_a, step, output_dir)
    # Step 2: positioned baseplate with connectors (varies by case)
    img_b = _get_or_build_baseplate(f"stepB:{case}", lambda: _render_baseplate_step_b(case)).copy()
    draw_b = ImageDraw.Draw(img_b)
    if minimap is not None:
        draw_block_minimap(draw_b, *minimap)
    # parts legend: 5x 2x2 plates + any connectors this block's position needs
    draw_step_parts_legend(draw_b, _baseplate_step_b_parts(case))
    step = save_img_and_increment_step(img_b, step, output_dir)
    # Step 3: top-of-baseplate view (no new piece introduced — stages the stud
    # surface for column placement); returned canvas is mutated by caller for
    # column drawing, so we .copy() the cached pristine version.
    img_c = _get_or_build_baseplate(f"stepC:{case}", lambda: _render_baseplate_step_c(case)).copy()
    if minimap is not None:
        draw_block_minimap(ImageDraw.Draw(img_c), *minimap)
    # Flip arrow lives ONLY on the saved step-3 PNG. Draw it on a throwaway copy
    # so it never bleeds onto img_c, which the caller reuses as the foundation
    # for every column page. Copy AFTER the minimap so the minimap still rides
    # onto both the saved PNG and the returned canvas.
    arrow_img = img_c.copy()
    arrow_draw = ImageDraw.Draw(arrow_img)
    draw_curved_arrow(
        arrow_draw,
        box=(150, 120, 430, 360),
        start_angle=180,
        end_angle=360,
        width=6,
        head_len=22,
        head_wid=26,
        fill="black",
    )
    step = save_img_and_increment_step(arrow_img, step, output_dir)
    return step, img_c  # clean canvas (no arrow) returned to caller

# -----------------------------
# Convert block grid to isometric XY
# -----------------------------
def get_block_xy(blockX, blockY):
    baseX = STARTING_X
    baseY = STARTING_Y
    # moving "up" one block
    baseX += blockY * PLATE_HALF_WIDTH
    baseY += blockY * PLATE_HALF_HEIGHT
    # moving "right" one block
    baseX += blockX * PLATE_HALF_WIDTH
    baseY -= blockX * PLATE_HALF_HEIGHT
    return [baseX, baseY]


#pass in array of 16 length to draw a column of plates on a specific column (if element is empty then no plate is needed)
def draw_plate_column(draw, start_blockX, height, colors, highlight):
    if len(colors) != 16:
        log_error("need 16 color indexes")
        return
    for i in range(15, -1, -1): #go from 15 to 0
        #paint back to front to adhere to painters algorithm
        #print("DRAWING color:", colors[i])
        if (colors[i][3] != 0):
            draw_plate(draw, start_blockX, i, height, colors[i], highlight)


def draw_plate(draw, blockX, blockY, blockZ, color, highlight):
    x, y = get_block_xy(blockX, blockY)
    if blockZ == 1:
        y += PLATE_HALF_HEIGHT
    # Shading
    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))
    # --- Plate faces ---
    top = [
        to_pillow(x + 0,               y + 0),
        to_pillow(x + PLATE_HALF_WIDTH, y + PLATE_HALF_HEIGHT),
        to_pillow(x + PLATE_WIDTH,      y + 0),
        to_pillow(x + PLATE_HALF_WIDTH, y - PLATE_HALF_HEIGHT)
    ]
    right = [
        to_pillow(x + PLATE_WIDTH,      y + 0),
        to_pillow(x + PLATE_HALF_WIDTH, y - PLATE_HALF_HEIGHT),
        to_pillow(x + PLATE_HALF_WIDTH, y - PLATE_HEIGHT),
        to_pillow(x + PLATE_WIDTH,      y - PLATE_HALF_HEIGHT)
    ]
    front = [
        to_pillow(x + 0,               y + 0),
        to_pillow(x + PLATE_HALF_WIDTH, y - PLATE_HALF_HEIGHT),
        to_pillow(x + PLATE_HALF_WIDTH, y - PLATE_HEIGHT),
        to_pillow(x + 0,               y - PLATE_HALF_HEIGHT)
    ]
    draw.polygon(top, top_color, outline=(10,10,10))
    draw.polygon(right, right_color, outline=(10,10,10))
    draw.polygon(front, front_color, outline=(10,10,10))



    #perform highlight if desired
    if highlight:
        yellow = (255, 255, 0)
        outline = [
            to_pillow(x + 0,               y + 0),
            to_pillow(x + 0,               y - PLATE_HALF_HEIGHT + 1),
            to_pillow(x + PLATE_HALF_WIDTH, y - PLATE_HEIGHT + 1),
            to_pillow(x + PLATE_WIDTH,      y - PLATE_HALF_HEIGHT + 1),
            to_pillow(x + PLATE_WIDTH,      y + 0),
            to_pillow(x + PLATE_HALF_WIDTH, y + PLATE_HALF_HEIGHT),
            to_pillow(x + 0,               y + 0)
        ]
        draw.line(outline, fill=yellow, width=2)




    # --- Stud front wall ---
    stud_left = x + STUD_OFFSET_X
    stud_right = stud_left + STUD_WIDTH
    stud_top = y + 2.5
    stud_bottom = y - 2.5
    front_wall = [
        to_pillow(stud_left,  stud_top),
        to_pillow(stud_right, stud_top),
        to_pillow(stud_right, stud_bottom),
        to_pillow(stud_left,  stud_bottom)
    ]
    draw.polygon(front_wall, front_color, outline=(10,10,10))
    # --- Stud top (ellipse) ---
    flipped_top = 2 * 400 - (y + STUD_HEIGHT * 3)
    flipped_bottom = 2 * 400 - (y + STUD_HEIGHT * 2)
    draw.ellipse(
        [stud_left, flipped_top, stud_right, flipped_bottom],
        fill=top_color,
        outline=(10,10,10)
    )


# -----------------------------
# Instruction-page overlays (per-step piece legend + "you are here" minimap)
# These use plain top-left page coordinates (no to_pillow / get_block_xy) and
# live in the empty top strip of the 612x792 page, clear of the isometric build
# (mid-band) and the bottom-center step number stamped by save_img_and_increment_step.
# -----------------------------

def _draw_mini_stud(draw, cx, top_cy, srx, sry, rise, color):
    """Draw one raised LEGO stud as a short cylinder: base ellipse + side wall +
    top ellipse, so it reads with a neck rather than a flat disc. (cx, top_cy)
    centers the TOP ellipse; the base sits `rise` px lower (toward the viewer).
    Shared by draw_mini_plate (1x1) and _draw_mini_rect (2x2/2x4/4x2/...) so every
    legend stud looks the same. `color` is the normalized (r,g,b) plate color."""
    edge = (10, 10, 10)
    top_color = to_rgb(color)
    side_color = to_rgb((color[0] * 0.78, color[1] * 0.78, color[2] * 0.78))
    base_cy = top_cy + rise
    # rounded base; the side wall (drawn next, no outline) hides its upper half
    draw.ellipse([cx - srx, base_cy - sry, cx + srx, base_cy + sry], fill=side_color, outline=edge)
    draw.rectangle([cx - srx, top_cy, cx + srx, base_cy], fill=side_color)
    draw.line([(cx - srx, top_cy), (cx - srx, base_cy)], fill=edge)
    draw.line([(cx + srx, top_cy), (cx + srx, base_cy)], fill=edge)
    draw.ellipse([cx - srx, top_cy - sry, cx + srx, top_cy + sry], fill=top_color, outline=edge)


def draw_mini_plate(draw, cx, cy, unit, color):
    """Draw a small isometric 1x1 LEGO plate icon. `cx` is the horizontal center
    and `cy` the vertical center of the top diamond; `unit` is the diamond
    half-width in pixels. `color` is a normalized (r,g,b) tuple. Plain top-left
    page coords (y grows downward) — independent of the get_block_xy/to_pillow
    isometric system used by draw_plate."""
    hw = unit             # horizontal half-width of the top diamond
    hh = unit * 0.5       # vertical half-height (2:1 isometric)
    depth = unit * 0.55   # drop of the side faces

    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))
    edge = (10, 10, 10)

    left_v = (cx - hw, cy)
    top_v = (cx, cy - hh)
    right_v = (cx + hw, cy)
    bot_v = (cx, cy + hh)

    # side faces first so the top diamond paints over their shared edges
    left_face = [left_v, bot_v, (cx, cy + hh + depth), (cx - hw, cy + depth)]
    right_face = [right_v, bot_v, (cx, cy + hh + depth), (cx + hw, cy + depth)]
    draw.polygon(left_face, front_color, outline=edge)
    draw.polygon(right_face, right_color, outline=edge)

    # top diamond
    draw.polygon([left_v, top_v, right_v, bot_v], top_color, outline=edge)

    # stud on top — a short cylinder (base ellipse + side wall + top ellipse) so
    # it reads as a raised LEGO stud with a neck, not a flat disc.
    srx = hw * 0.42           # stud ellipse half-width
    sry = hh * 0.42           # stud ellipse half-height (same 2:1 foreshortening)
    rise = unit * 0.28        # how tall the stud stands in px
    base_cy = cy - hh * 0.05  # base ellipse sits ~centered on the plate top face
    top_cy = base_cy - rise
    _draw_mini_stud(draw, cx, top_cy, srx, sry, rise, color)


def draw_step_piece_legend(draw, stud_colors, *, x=25, y=18, max_width=440):
    """Draw a compact legend of the pieces placed in this step: one mini-plate
    icon per distinct color with a small 'xN' count. `stud_colors` is a list of
    normalized (r,g,b) tuples (opaque studs only). Collapses same-color runs,
    preserves first-seen order, wraps to a new row past `max_width`."""
    if not stud_colors:
        return
    counts = {}
    order = []
    for c in stud_colors:
        key = tuple(round(float(v), 4) for v in c[:3])
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1

    font = get_font(16)
    unit = 9 * 1.32           # mini-plate half-width (icons ~32% larger than base)
    cell_w = 2 * unit + 34    # icon (~2*unit wide) + room for the 'xN' label
    row_h = 40                # row pitch: icon height + label + gap
    icon_cy = unit + 6        # vertical center of the icon within its row

    cur_x = x
    cur_y = y
    for key in order:
        if cur_x + cell_w > x + max_width:
            cur_x = x
            cur_y += row_h
        cx = cur_x + unit
        cy = cur_y + icon_cy
        draw_mini_plate(draw, cx, cy, unit, key)
        draw.text((cur_x + 2 * unit + 4, cy - 8), f"x{counts[key]}", fill="black", font=font)
        cur_x += cell_w


# -----------------------------
# Generalized per-step parts legend — proportional mini icons for ANY piece
# (baseplate, connectors, frame bricks/plates), not just the 1x1 plates that
# draw_step_piece_legend handles. Plain top-left page coords, same band as
# draw_mini_plate / draw_step_piece_legend (clear of the top-right minimap and the
# isometric build mid-band). Specs come from piece_specs.PieceSpec.
# -----------------------------

# Pixel deltas of the original flat connector-pin parallelogram (red = mirrored
# across x). Now an INDEPENDENT constant used ONLY by the GREY legend connectors
# (axle peg / hooks), which keep the plain flat-parallelogram icon. The baseplate
# green/red pins have DIVERGED: they now carry a flat collar band (see
# _draw_baseplate_connector_pin), and the green/red legend icons use
# _draw_connector_rod. Do NOT sync this back to draw_baseplate_bottom.
_CONNECTOR_PIN_DELTAS = [(-3, 9), (-30, 25), (-22, 29), (5, 13)]


def _connector_pin_points(ax, ay, *, mirror=False, scale=1.0):
    """The 4-point parallelogram of the green/red baseplate connector pin.
    (ax, ay) is the anchor; `mirror` flips x (green->red); `scale` shrinks it for
    legend icons. At offset 0, scale 1, mirror False this reproduces the
    baseplate's greenPin exactly."""
    deltas = [(-dx, dy) for dx, dy in _CONNECTOR_PIN_DELTAS] if mirror else _CONNECTOR_PIN_DELTAS
    return [(ax + dx * scale, ay + dy * scale) for dx, dy in deltas]


def _mini_shades(color):
    """(top, right, front, edge) colors for a mini iso piece — same shading
    ratios as draw_mini_plate."""
    top = to_rgb(color)
    right = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))
    return top, right, front, (10, 10, 10)


def _mini_per_stud(width, length, unit, *, max_span=58.0):
    """Per-stud screen size so a (width x length) piece fits a legend cell:
    nominal `unit` for small pieces, shrunk for long/large ones (a 16x16 baseplate
    collapses to a small box; a 10x2 stays a long thin bar)."""
    span_studs = max(width + length, 2)
    return max(min(unit, max_span / span_studs), 2.0)


def _draw_mini_rect(draw, cx, cy, s, width, length, height, color, *, axle=False):
    """Proportional isometric W x L plate/brick centered at (cx, cy). `s` is the
    per-stud screen size; `height` (plate layers) sets the body depth. Studs are
    drawn only when they'd be legible."""
    ewx, ewy = s, -s * 0.5            # +1 stud along width
    elx, ely = -s, -s * 0.5           # +1 stud along length
    depth = s * (0.55 if height <= 1 else 0.95)
    # Center the top-face bounding box (plus the downward body) on (cx, cy).
    px = cx - (width - length) * s / 2.0
    py = cy - depth / 2.0 + (width + length) * s / 4.0
    top_c, right_c, front_c, edge = _mini_shades(color)

    def corner(i, j):
        return (px + i * ewx + j * elx, py + i * ewy + j * ely)

    front = corner(0, 0)
    right = corner(width, 0)
    back = corner(width, length)
    left = corner(0, length)
    # Two visible side walls (front-left + front-right edges extruded down).
    draw.polygon([front, left, (left[0], left[1] + depth), (front[0], front[1] + depth)], front_c, outline=edge)
    draw.polygon([front, right, (right[0], right[1] + depth), (front[0], front[1] + depth)], right_c, outline=edge)
    # Top face.
    draw.polygon([front, right, back, left], top_c, outline=edge)
    # Axle-hole marker on the front wall.
    if axle:
        mx = (front[0] + right[0]) / 2.0
        my = (front[1] + right[1]) / 2.0 + depth / 2.0
        arm = max(s * 0.35, 2)
        draw.line([(mx - arm, my), (mx + arm, my)], fill=(0, 0, 0), width=2)
        draw.line([(mx, my - arm), (mx, my + arm)], fill=(0, 0, 0), width=2)
    # Studs — skip when too small or too many (keeps big/long pieces readable).
    # Drawn as full cylinders (base + neck + top), same as draw_mini_plate's 1x1
    # stud, so 2x2/2x4/4x2 pieces read with necks instead of flat discs.
    # Threshold 4.5 (not 5) so the long thin 10x2 plate (s~4.8) still gets studs;
    # the 16x16 baseplate stays a flat box via the width*length<=24 count gate.
    if s >= 4.5 and width * length <= 24:
        srx, sry = s * 0.30, s * 0.18
        rise = s * 0.28
        cells = sorted(
            ((i, j) for i in range(width) for j in range(length)),
            key=lambda c: c[0] + c[1], reverse=True,   # back-to-front (painter's)
        )
        for i, j in cells:
            scx = px + (i + 0.5) * ewx + (j + 0.5) * elx
            scy = py + (i + 0.5) * ewy + (j + 0.5) * ely - rise
            _draw_mini_stud(draw, scx, scy, srx, sry, rise, color)


def _draw_mini_corner(draw, cx, cy, s, n, height, color):
    """Proportional L-shaped corner piece centered at (cx, cy): an n x n footprint
    with the front (n//2) x (n//2) quadrant removed, so n=4 -> 4x4-minus-2x2
    (12 studs, the corner PLATE) and n=2 -> 2x2-minus-1x1 (3 studs, the corner
    BRICK) — matching the build-art draw_corner_plate / draw_corner_brick. `s` is
    the per-stud screen size; `height` sets the body depth (flat plate vs thick
    brick). Studs are drawn as necked cylinders, same as the other mini icons."""
    c = n // 2                           # removed front quadrant is c x c
    ewx, ewy = s, -s * 0.5
    elx, ely = -s, -s * 0.5
    depth = s * (0.55 if height <= 1 else 0.95)
    px = cx                              # square n x n footprint is symmetric in (w - l)
    py = cy - depth / 2.0 + (n + n) * s / 4.0
    top_c, right_c, front_c, edge = _mini_shades(color)

    def corner(i, j):
        return (px + i * ewx + j * elx, py + i * ewy + j * ely)

    # Top-face L outline (full n x n with the front c x c quadrant, i<c & j<c,
    # removed). Indices: 0=(c,0) 1=(n,0) 2=(n,n) 3=(0,n) 4=(0,c) 5=(c,c).
    face = [corner(c, 0), corner(n, 0), corner(n, n), corner(0, n), corner(0, c), corner(c, c)]
    # Filled side walls connect the top face down to the base so the icon reads as
    # ONE solid piece (not two stacked plates). Only the lower-silhouette edges are
    # visible: the two outer front edges (0->1, 3->4) plus the two inner notch
    # edges (4->5, 5->0). +i edges take the right shade, -j edges the front shade
    # (same convention as _draw_mini_rect). Drawn before the top face covers them.
    for (a, b), shade in (((0, 1), right_c), ((4, 5), right_c),
                          ((3, 4), front_c), ((5, 0), front_c)):
        ax, ay = face[a]
        bx, by = face[b]
        draw.polygon([(ax, ay), (bx, by), (bx, by + depth), (ax, ay + depth)], shade, outline=edge)
    # Top face over the wall tops.
    draw.polygon(face, top_c, outline=edge)
    # Necked studs on the n x n grid minus the notch, back-to-front (painter's).
    if s >= 4.5:
        srx, sry = s * 0.30, s * 0.18
        rise = s * 0.28
        cells = sorted(
            ((i, j) for i in range(n) for j in range(n) if not (i < c and j < c)),
            key=lambda ij: ij[0] + ij[1], reverse=True,
        )
        for i, j in cells:
            scx = px + (i + 0.5) * ewx + (j + 0.5) * elx
            scy = py + (i + 0.5) * ewy + (j + 0.5) * ely - rise
            _draw_mini_stud(draw, scx, scy, srx, sry, rise, color)


def _draw_connector_pin(draw, cx, cy, s, color, *, mirror):
    """Green/red LEGO-Art connector pin (baseplate shape) centered at (cx, cy)."""
    scale = max(s / 12.0, 0.45)          # ~unit-sized icon
    pts = _connector_pin_points(0.0, 0.0, mirror=mirror, scale=scale)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bx = (min(xs) + max(xs)) / 2.0
    by = (min(ys) + max(ys)) / 2.0
    pts = [(x - bx + cx, y - by + cy) for x, y in pts]
    draw.polygon(pts, to_rgb(color), outline=(10, 10, 10))


def _draw_connector_rod(draw, cx, cy, s, color):
    """Green/red LEGO Technic connector pin (design 32054/65304, "Pin 3L with
    Friction Ridges and Stop Bush") as a round isometric cylinder along the length
    (el) axis with one wider, round stop-bush collar offset toward the short end —
    the pin passes THROUGH the collar. Built from circular cross-sections (sheared
    to ellipses in iso) swept far->near with a varying radius (thin pin, wide
    collar). The black border is drawn in depth order so it never paints over
    what's in front: the collar's front flange ring is stroked BEFORE the near pin
    is filled (the pin then overpaints the part that lies in front of it); the
    collar's back rim and the pin's far end are only their far-side silhouette arcs
    (no full circle / "hole"); only the near pin tip is a full circle. Green and
    red share this shape/orientation (no mirror). Centered at (cx, cy)."""
    s *= 1.21                         # a touch bigger than the other mini icons
    top_c, side_c, _shadow, edge = _mini_shades(color)
    ewx, ewy = s, -s * 0.5            # +1 unit along width (up-right, cross-section axis)
    elx, ely = -s, -s * 0.5           # +1 unit along length (up-left, the pin axis)

    rod_len, rod_r = 2.4, 0.17                       # round pin: length, radius
    col_r, col_len, col_at = 0.31, 0.5, 0.25         # round collar: radius, length, start frac
    j0 = col_at * rod_len                            # collar near edge along length
    j1 = j0 + col_len                                # collar far edge
    two_pi = 2.0 * math.pi
    back0, back1 = 3.0 * math.pi / 4.0, 7.0 * math.pi / 4.0   # far-side silhouette half-arc

    def ring(jj, radius, a0=0.0, a1=two_pi, up=0.0, n=24):
        # Cross-section circle perpendicular to the axis at length jj (a full ring,
        # or the partial arc [a0, a1]); optionally shifted up. Sheared to an ellipse.
        return [(jj * elx + radius * math.cos(a) * ewx,
                 jj * ely - up + radius * math.cos(a) * ewy + radius * math.sin(a) * s)
                for a in (a0 + (a1 - a0) * k / n for k in range(n + 1))]

    def gpt(jj, radius, theta):       # one silhouette-generator point
        return (jj * elx + radius * math.cos(theta) * ewx,
                jj * ely + radius * math.cos(theta) * ewy + radius * math.sin(theta) * s)

    span = 1.118 * s
    nseg = lambda dj: max(6, int(dj * span / 1.1))

    def tube(ja, jb, radius):
        # filled body discs + top-highlight discs along [ja, jb]
        out = []
        m = nseg(abs(jb - ja))
        for k in range(m + 1):
            jj = ja + (jb - ja) * k / m
            out.append((ring(jj, radius), side_c))
            out.append((ring(jj, radius * 0.5, up=radius * s * 0.42), top_c))
        return out

    far_t = tube(rod_len, j1, rod_r)                 # far pin (behind collar)
    col_t = tube(j1, j0, col_r)                      # collar body
    near_t = tube(j0, 0.0, rod_r)                    # near pin (in front of collar)
    flange = (ring(j0, col_r), top_c)                # collar front face
    tip = (ring(0.0, rod_r), top_c)                  # pin front face

    # Center on (cx, cy) using all fill points.
    pts = [p for grp in (far_t, col_t, near_t) for poly, _ in grp for p in poly]
    pts += flange[0] + tip[0]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    dx = cx - (min(xs) + max(xs)) / 2.0
    dy = cy - (min(ys) + max(ys)) / 2.0
    sh = lambda q: [(x + dx, y + dy) for x, y in q]
    bw = max(1, int(round(s * 0.05)))

    def fill(pf):
        draw.polygon(sh(pf[0]), pf[1])

    def stroke(q, *, closed=False):
        q = sh(q)
        draw.line(q + ([q[0]] if closed else []), fill=edge, width=bw, joint="curve")

    # Back-to-front, interleaving borders so none paints over what's in front of it.
    for pf in far_t:
        fill(pf)
    for pf in col_t:
        fill(pf)
    stroke(ring(j1, col_r, back0, back1))            # collar BACK rim (far-side arc only)
    fill(flange)
    stroke(ring(j0, col_r), closed=True)             # collar FRONT flange ring (pin overpaints occluded part next)
    for pf in near_t:
        fill(pf)
    fill(tip)
    # Outer silhouette long edges (top + bottom generators, stepping at the collar).
    for theta in (-math.pi / 4.0, 3.0 * math.pi / 4.0):
        stroke([gpt(0.0, rod_r, theta), gpt(j0, rod_r, theta), gpt(j0, col_r, theta),
                gpt(j1, col_r, theta), gpt(j1, rod_r, theta), gpt(rod_len, rod_r, theta)])
    stroke(ring(rod_len, rod_r, back0, back1))       # pin FAR end: rounded arc, no "hole"
    stroke(ring(0.0, rod_r), closed=True)            # pin FRONT tip: the single circle


def draw_mini_piece(draw, cx, cy, unit, spec):
    """Draw a small proportional isometric icon of `spec` (piece_specs.PieceSpec)
    centered at (cx, cy). `unit` is the nominal 1-stud half-width (matches
    draw_mini_plate). Plain top-left page coords."""
    if spec.shape == ps.SHAPE_CONNECTOR:
        # Green (6526672) / red (6347789) are both design 32054 — same part, drawn
        # the same way (no mirror) as a flat-faced 3D rod. Grey connectors (axle
        # peg / hooks) keep the legacy flat parallelogram icon.
        if max(spec.color) - min(spec.color) > 0.25:   # chromatic => green/red
            _draw_connector_rod(draw, cx, cy, unit, spec.color)
        else:
            mirror = spec.color[0] >= spec.color[1]
            _draw_connector_pin(draw, cx, cy, unit, spec.color, mirror=mirror)
        return
    if spec.shape == ps.SHAPE_CORNER:
        n = max(spec.width, spec.length)          # 4 = corner plate, 2 = corner brick
        s = _mini_per_stud(n, n, unit)
        _draw_mini_corner(draw, cx, cy, s, n, spec.height, spec.color)
        return
    w, l = max(spec.width, 1), max(spec.length, 1)
    s = _mini_per_stud(w, l, unit)
    _draw_mini_rect(draw, cx, cy, s, w, l, spec.height, spec.color, axle=spec.axle)


def _text_width(draw, text, font):
    try:
        return draw.textlength(text, font=font)
    except AttributeError:                # very old Pillow without textlength
        return len(text) * 7


def draw_step_parts_legend(draw, parts, *, x=25, y=18, max_width=440, max_rows=2):
    """Per-step legend of the pieces used: a proportional mini icon per entry with
    a two-line 'WxH' / 'xN' label. `parts` is a list of (PieceSpec, qty). Wraps to
    a new row past `max_width`; stops after `max_rows` so it can't spill into the
    isometric build area below. Top-left strip (clear of the top-right minimap)."""
    if not parts:
        return
    font = get_font(15)
    unit = 11.0                          # nominal per-stud size (~matches draw_mini_plate)
    icon_cy = y + 22                     # icon vertical center within a row
    row_h = 58
    pad = 16
    cur_x = x
    row = 0
    for spec, qty in parts:
        w, l = max(spec.width, 1), max(spec.length, 1)
        s = _mini_per_stud(w, l, unit)
        icon_w = 30 if spec.shape == ps.SHAPE_CONNECTOR else (w + l) * s
        if spec.show_dims:
            label = f"{spec.width}x{spec.length}"
        elif spec.shape == ps.SHAPE_CONNECTOR:
            label = ""              # connectors: pin icon + qty only (no "green conn"/"red conn")
        else:
            label = spec.name
        qty_label = f"x{qty}"
        cell_w = max(icon_w, _text_width(draw, label, font), 2 * unit) + pad
        if cur_x + cell_w > x + max_width and cur_x > x:
            row += 1
            if row >= max_rows:
                break
            cur_x = x
        cxc = cur_x + cell_w / 2.0
        cyc = icon_cy + row * row_h
        draw_mini_piece(draw, cxc, cyc, unit, spec)
        # Label line (skipped for nameless connectors) + qty line, centered.
        # The qty stays on the second line so it lines up with the other cells.
        qw = _text_width(draw, qty_label, font)
        if label:
            lw = _text_width(draw, label, font)
            draw.text((cxc - lw / 2.0, cyc + 20), label, fill="black", font=font)
        draw.text((cxc - qw / 2.0, cyc + 36), qty_label, fill="black", font=font)
        cur_x += cell_w


def draw_big_quantity(draw, n, x, y):
    """Large 'xN' multiplier drawn next to an assembled frame piece — tells the
    builder how many of that fully-made piece to produce. Placed in the build
    area, clear of the top-left parts legend and the bottom-center step number.
    Plain top-left text (no anchor=) so the bitmap-font fallback still works."""
    draw.text((x, y), f"x{n}", fill="black", font=get_font(72))


def draw_block_minimap(draw, n_w, n_h, cur_w, cur_h, *, anchor_right=587, top=18, box=120):
    """Draw a schematic block-grid minimap (n_w x n_h cells) anchored to the
    top-right, highlighting the current baseplate (cur_w, cur_h). `cur_w` is the
    column index (left->right), `cur_h` the row index (top->down), matching the
    image block order (blockH=0 is the top row, blockW=0 the left column)."""
    if n_w < 1 or n_h < 1:
        return
    cell = min(18, box // max(n_w, n_h))
    cell = max(cell, 3)       # keep cells visible for very large grids
    grid_w = cell * n_w
    left = anchor_right - grid_w
    grey = (210, 210, 210)
    highlight = (255, 210, 0)
    edge = (60, 60, 60)
    for r in range(n_h):
        for c in range(n_w):
            x0 = left + c * cell
            y0 = top + r * cell
            x1 = x0 + cell
            y1 = y0 + cell
            if c == cur_w and r == cur_h:
                draw.rectangle([x0, y0, x1, y1], fill=highlight, outline=(0, 0, 0), width=2)
            else:
                draw.rectangle([x0, y0, x1, y1], fill=grey, outline=edge)


# Helper: convert stud offsets to pixel coords
def iso(dx, dy, x0, y0):
    return to_pillow(
        x0 + dx * PLATE_HALF_WIDTH,
        y0 + dy * PLATE_HALF_HEIGHT
    )

def draw_corner_brick(draw, baseX, baseY, baseZ, color, highlight=False):
    # Convert base stud to pixel origin
    x0, y0 = get_block_xy(baseX, baseY)

    # Shading
    top_color   = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))

    half_width = 4
    full_width = 8
    plate_height = 2

    # -----------------------------
    # TOP FACE (2x2 with 1x1 missing bottom-left)
    # -----------------------------
    top = [
        iso(half_width, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, full_width, x0, y0),
        iso(0, full_width, x0, y0),
        iso(0, half_width, x0, y0),
        iso(half_width, half_width, x0, y0)
    ]
    draw.polygon(top, top_color, outline=(10,10,10))

    left = [
        iso(half_width, 0 - plate_height, x0, y0),
        iso(0, 0 - plate_height, x0, y0),
        iso(0, half_width, x0, y0),
        iso(half_width, half_width, x0, y0)
    ]
    draw.polygon(left, front_color, outline=(10,10,10))

    
    right = [
        iso(half_width, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, -half_width - plate_height, x0, y0),
        iso(half_width, -half_width - plate_height, x0, y0)
    ]
    draw.polygon(right, front_color, outline=(10,10,10))

    # --- Studs (only where plate exists) ---
    for dx in range(2):
        for dy in range(2):
            if dx < 1 and dy < 1:
                continue  # missing quadrant
            xOffset = dx * PLATE_WIDTH * 2
            yOffset = dy * PLATE_HEIGHT * 2
            draw_stud(draw, x0 + xOffset, y0 + yOffset, baseZ, color)

    # --- Optional highlight ---
    if highlight:
        draw.line([top[1], top[2], top[3], left[1], left[0], right[3], right[2], right[1]], fill=(255,255,0), width=2)


def draw_brick(draw, baseX, baseY, baseZ, color, highlight=False):
    # Convert base stud to pixel origin
    x0, y0 = get_block_xy(baseX, baseY)

    # Shading
    top_color   = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))

    half_width = 2
    full_width = 4
    plate_height = 6
    
    top = [
        iso(0, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, full_width, x0, y0),
        iso(0, full_width, x0, y0),
    ]
    draw.polygon(top, top_color, outline=(10,10,10))

    front = [
        iso(0, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, - plate_height, x0, y0),
        iso(0, - plate_height, x0, y0)
    ]
    draw.polygon(front, front_color, outline=(10,10,10))

    draw_stud(draw, x0, y0, baseZ, color)

    # --- Optional highlight ---
    if highlight:
        draw.line([top[1], top[2], top[3], front[3], front[2], top[2]], fill=(255,255,0), width=2)


def draw_corner_plate(draw, baseX, baseY, baseZ, length, width, color, cutoffX, cutoffY, highlight=False):
    # Convert base stud to pixel origin
    x0, y0 = get_block_xy(baseX, baseY)

    # Shading
    top_color   = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))

    
    full_width = length * 4
    half_width = width * 4
    plate_height = 2

    # -----------------------------
    # TOP FACE (4x4 with 2x2 missing bottom-left)
    # -----------------------------
    top = [
        iso(full_width - half_width, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, full_width, x0, y0),
        iso(0, full_width, x0, y0),
        iso(0, full_width - half_width, x0, y0),
        iso(full_width - half_width, full_width - half_width, x0, y0)
    ]
    draw.polygon(top, top_color, outline=(10,10,10))

    # -----------------------------
    # LEFT FACE (vertical wall on left side)
    # This is the right wall of the 8x8 plate.
    # -----------------------------
    left = [
        iso(0, full_width - half_width - plate_height, x0, y0),   # drop plate height
        iso(full_width - half_width, full_width - half_width - plate_height, x0, y0),
        iso(full_width - half_width, full_width - half_width, x0, y0),
        iso(0, full_width - half_width, x0, y0),
    ]
    draw.polygon(left, front_color, outline=(10,10,10))

    # -----------------------------
    # RIGHT FACE (vertical wall on right side)
    # This is the front wall of the 4x8 section.
    # -----------------------------
    right = [
        iso(full_width - half_width, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, 0 - plate_height, x0, y0),
        iso(full_width - half_width, 0 - plate_height, x0, y0)
    ]
    draw.polygon(right, front_color, outline=(10,10,10))

    # --- Studs (only where plate exists) ---
    for dx in range(length):
        for dy in range(length):
            if dx < cutoffX and dy < cutoffY:
                continue  # missing quadrant
            xOffset = dx * PLATE_WIDTH * 2
            yOffset = dy * PLATE_HEIGHT * 2
            draw_stud(draw, x0 + xOffset, y0 + yOffset, baseZ, color)

    # --- Optional highlight ---
    if highlight:
        draw.line([top[1], top[2], top[3], left[0], left[1], right[3], right[2], right[1]], fill=(255,255,0), width=2)


def draw_ortho_plate(draw, baseX, baseY, isAxle, width, depth, height, color, highlight=False, flat=False):
    # Convert base stud to pixel origin
    x0, y0 = get_block_xy(baseX, baseY)

    # Shading
    top_color   = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))

    full_width = width * 4
    half_width = depth * 4
    plate_height = height * 2

    top = [
        iso(full_width, 0, x0, y0),
        iso(full_width, half_width, x0, y0),
        iso(0, half_width, x0, y0),
        iso(0, 0, x0, y0)
    ]
    draw.polygon(top, top_color, outline=(10,10,10))

    # -----------------------------
    # front FACE (vertical wall on left side)
    # This is the front wall
    # -----------------------------
    front = [
        iso(0, -plate_height, x0, y0),   # drop plate height
        iso(full_width, -plate_height, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(0, 0, x0, y0)
    ]
    draw.polygon(front, front_color, outline=(10,10,10))

    if isAxle:
        cx, cy = iso(full_width / 2, -plate_height / 2, x0, y0)

        vertical_size = 9  # pixel length of each arm of the plus
        horizontal_size = 12
        line_width = 4

        # horizontal line
        draw.line([(cx - horizontal_size, cy), (cx + horizontal_size, cy)], fill=(0,0,0), width=line_width)

        # vertical line
        draw.line([(cx, cy - vertical_size), (cx, cy + vertical_size)], fill=(0,0,0), width=line_width)

    # --- Optional highlight ---
    if highlight:
        draw.line([top[2], front[0], front[1], top[1], top[2]], fill=(255,255,0), width=2)

    #return early if flat (no need to draw studs)
    if flat:
        return
    # --- Studs (only where plate exists) ---
    for dx in range(width):
        for dy in range(depth):
            xOffset = dx * PLATE_WIDTH * 2
            yOffset = dy * PLATE_HEIGHT * 2
            draw_stud(draw, x0 + xOffset, y0 + yOffset, 0, color)


def draw_grid_setup_instruction(step, output_dir=None):
    img, draw = get_img_and_draw(step, True, output_dir)
    width, height = img.size
    draw = ImageDraw.Draw(img)

    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)
    
    if blockHeight > 1:
        #instruct user to connect columns via the red connectors going down
        font = get_font(20)
        margin = 25
        placement = (margin, margin)
        text = "connect baseplates via red connectors/plates to form columns"
        draw.text(placement, text, fill="black", font=font)

        #draw first cube
        black = to_rgb((.2, .2, .2))
        red = to_rgb((.8, .1, .1))
        x_offset = 100
        y_offset = 90
        length = 100
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #draw second cube
        y_offset = 260
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))
        #draw arrow showing step
        draw_arrow(draw, 275, 250, 100, 50, 50, 120)

        #draw connectors
        x_offset = 125
        y_offset = 90 + length
        length = 50
        triangle = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length / 2, y_offset + length),
        ]
        draw.polygon(triangle, red, outline=(10,10,10))

        #show completed step
        x_offset = 475
        y_offset = 150
        length = 100
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))
        #draw second cube so they look connected
        y_offset = y_offset + length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

    if blockWidth > 1:
        #instruct user to connect columns via the green connectors going right
        font = get_font(20)
        margin = 25
        half_height = height / 2
        placement = (margin, half_height)
        text = "connect columns via green connectors/plates to form the grid"
        draw.text(placement, text, fill="black", font=font)

        #draw column
        black = to_rgb((.2, .2, .2))
        green = to_rgb((.1, .8, .1))
        x_offset = margin
        y_offset = half_height + 120
        length = 80
        #first column fist block
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))
        #first column second block
        y_offset = y_offset + length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #second column first block
        x_offset = x_offset + length * 2
        y_offset -= length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #second column second block
        y_offset = y_offset + length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #third column first block
        x_offset = 410
        y_offset -= length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #third column second block
        y_offset += length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #fourth column first block
        x_offset += length
        y_offset -= length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))

        #fourth column second block
        y_offset += length
        cube = [
            (x_offset, y_offset),
            (x_offset + length, y_offset),
            (x_offset + length, y_offset + length),
            (x_offset, y_offset + length),
        ]
        draw.polygon(cube, black, outline=(10,10,10))
        
        #draw connectors
        x_offset = margin + length
        y_offset = half_height + 100 + (length / 2)
        length = 50
        triangle = [
            (x_offset, y_offset),
            (x_offset, y_offset + length),
            (x_offset + length, y_offset + length / 2),
        ]
        draw.polygon(triangle, green, outline=(10,10,10))

        y_offset += length + (length * .5)
        triangle = [
            (x_offset, y_offset),
            (x_offset, y_offset + length),
            (x_offset + length, y_offset + length / 2),
        ]
        draw.polygon(triangle, green, outline=(10,10,10))


        #draw arrow showing step
        draw_arrow(draw, 300, half_height + 200, 50, 25, 25, 60)

    step = save_img_and_increment_step(img, step, output_dir) # Save current step (with current step pieces highlighted)
    return step


def draw_arrow(draw, x, y, s_len, s_thick, h_len, h_wid, fill="black"):
    """
    Draws a right-pointing arrow using named geometry variables.

    draw    : ImageDraw.Draw object
    x, y    : starting point (left-middle of the arrow)
    s_len   : shaft length
    s_thick : shaft thickness
    h_len   : arrowhead length
    h_wid   : arrowhead width
    fill    : color
    """

    # Shaft geometry
    shaft_top    = y - s_thick / 2
    shaft_bottom = y + s_thick / 2

    # Arrowhead geometry
    head_base_x = x + s_len
    head_tip_x  = x + s_len + h_len
    head_top    = y - h_wid / 2
    head_bottom = y + h_wid / 2

    # Polygon points (clockwise)
    arrow = [
        (x, shaft_top),           # left-top of shaft
        (head_base_x, shaft_top),
        (head_base_x, head_top),  # start of arrowhead
        (head_tip_x, y),          # tip
        (head_base_x, head_bottom),
        (head_base_x, shaft_bottom),
        (x, shaft_bottom)         # left-bottom of shaft
    ]

    draw.polygon(arrow, fill=fill)


def draw_curved_arrow(draw, box, start_angle, end_angle, width=6,
                      head_len=22, head_wid=26, fill="black"):
    """
    Draws a curved (arc) arrow: a shaft riding on the ellipse defined by `box`
    plus a triangular arrowhead at `end_angle` pointing along the arc tangent.
    Reads as a rotational / "flip it over" motion.

    draw        : ImageDraw.Draw object
    box         : (x0, y0, x1, y1) bounding box of the ellipse the arc rides on
    start_angle : arc start in degrees. PIL convention: 0=right, 90=bottom,
                  180=left, 270=top, sweeping clockwise in screen space.
    end_angle   : arc end in degrees (the arrowhead is drawn here)
    width       : thickness of the curved shaft
    head_len    : arrowhead length (tip distance beyond the arc end point)
    head_wid    : arrowhead base width
    fill        : color
    """
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    rx = (x1 - x0) / 2.0
    ry = (y1 - y0) / 2.0

    # Curved shaft
    draw.arc(box, start=start_angle, end=end_angle, fill=fill, width=width)

    # End point of the arc (where the head attaches)
    a = math.radians(end_angle)
    ex = cx + rx * math.cos(a)
    ey = cy + ry * math.sin(a)

    # Forward tangent at the arc end (clockwise sweep): d/da (cos, sin) = (-sin, cos)
    tx = -math.sin(a)
    ty = math.cos(a)
    tlen = math.hypot(tx, ty) or 1.0
    tx, ty = tx / tlen, ty / tlen
    # Perpendicular, for the head base width
    px, py = -ty, tx

    tip   = (ex + tx * head_len, ey + ty * head_len)
    baseL = (ex + px * head_wid / 2.0, ey + py * head_wid / 2.0)
    baseR = (ex - px * head_wid / 2.0, ey - py * head_wid / 2.0)
    draw.polygon([tip, baseL, baseR], fill=fill)


def draw_directional_arrow(draw, tip_x, tip_y, dir_x, dir_y,
                           shaft_len, shaft_thick, head_len, head_wid, fill="black"):
    """
    Draws a straight arrow whose TIP (the point of the arrowhead) sits at
    (tip_x, tip_y) and points along the unit of (dir_x, dir_y); the shaft trails
    backwards, opposite the direction. Generalizes draw_arrow (which only points
    right) using the same tangent/perpendicular head math as draw_curved_arrow,
    so a single call can aim up / down / left / right.

    draw                 : ImageDraw.Draw object
    tip_x, tip_y         : where the arrowhead point lands
    dir_x, dir_y         : direction the arrow points (need not be unit length)
    shaft_len            : length of the shaft behind the arrowhead
    shaft_thick          : shaft thickness
    head_len, head_wid   : arrowhead length / base width
    fill                 : color
    """
    mag = math.hypot(dir_x, dir_y) or 1.0
    ux, uy = dir_x / mag, dir_y / mag      # forward unit vector
    px, py = -uy, ux                       # perpendicular unit vector

    # Arrowhead: tip at (tip_x, tip_y), base head_len behind it.
    base_x = tip_x - ux * head_len
    base_y = tip_y - uy * head_len
    head_left  = (base_x + px * head_wid / 2.0, base_y + py * head_wid / 2.0)
    head_right = (base_x - px * head_wid / 2.0, base_y - py * head_wid / 2.0)

    # Shaft: a rectangle running shaft_len further back from the head base.
    end_x = base_x - ux * shaft_len
    end_y = base_y - uy * shaft_len
    shaft = [
        (base_x + px * shaft_thick / 2.0, base_y + py * shaft_thick / 2.0),
        (base_x - px * shaft_thick / 2.0, base_y - py * shaft_thick / 2.0),
        (end_x  - px * shaft_thick / 2.0, end_y  - py * shaft_thick / 2.0),
        (end_x  + px * shaft_thick / 2.0, end_y  + py * shaft_thick / 2.0),
    ]
    draw.polygon(shaft, fill=fill)
    draw.polygon([(tip_x, tip_y), head_left, head_right], fill=fill)


def draw_axle_pin_map(draw, blockWidth, blockHeight, center_x, band_top, band_bottom):
    """
    Top-down schematic for the axle-connection step: the mosaic baseplate grid
    (grey squares) wrapped by the assembled frame (dark border), with one
    inward-pointing arrow at every axle-pin hole. Each long frame edge carries
    two axle bricks, so every baseplate edge along the perimeter gets two pins,
    placed at the ~25% and ~75% marks -> 2 * perimeter arrows total. Auto-fits
    and centers within the [band_top, band_bottom] vertical band.

    draw                 : ImageDraw.Draw to render onto
    blockWidth/Height    : mosaic size in 16x16 baseplate blocks
    center_x             : page x to center the grid on
    band_top/band_bottom : vertical pixel band the whole diagram must fit within
    """
    light_grey = to_rgb((.8, .8, .8))
    frame_grey = to_rgb((.3, .3, .3))
    outline = (10, 10, 10)

    # Fixed arrow / mark geometry (independent of grid size for legibility).
    cross = 6                 # half-length of each "+" hole arm
    a_shaft_len = 16
    a_shaft_thick = 5
    a_head_len = 11
    a_head_wid = 14
    # how far an arrow sticks out past a grid edge (tip sits cross+2 inside it)
    arrow_out = a_head_len + a_shaft_len - (cross + 2)   # ~19px
    edge_pad = arrow_out + 6      # clearance reserved beyond each grid edge
    caption_block = 34            # space under the grid for the caption

    band_h = band_bottom - band_top
    max_w = 430
    max_h = band_h - 2 * edge_pad - caption_block
    CAP = 90                      # don't oversize a tiny (e.g. 1x1) mosaic
    length = min(max_w / blockWidth, max_h / blockHeight, CAP)

    grid_w = length * blockWidth
    grid_h = length * blockHeight

    # Center the whole composition (top arrows + grid + bottom arrows + caption).
    total_h = edge_pad + grid_h + edge_pad + caption_block
    grid_left = center_x - grid_w / 2.0
    grid_top = band_top + (band_h - total_h) / 2.0 + edge_pad
    grid_right = grid_left + grid_w
    grid_bottom = grid_top + grid_h

    # Assembled frame border the pins lock into (box is the outer edge).
    fb = max(6, int(length * 0.16))
    draw.rectangle(
        [grid_left - fb, grid_top - fb, grid_right + fb, grid_bottom + fb],
        outline=frame_grey, width=fb,
    )

    # Mosaic baseplate squares.
    for w in range(blockWidth):
        for h in range(blockHeight):
            x = grid_left + length * w
            y = grid_top + length * h
            draw.rectangle([x, y, x + length, y + length],
                           fill=light_grey, outline=outline)

    # A "+" cross-hole mark on the border plus an inward arrow, per axle pin.
    def pin(px, py, dx, dy):
        draw.line([(px - cross, py), (px + cross, py)], fill=(0, 0, 0), width=3)
        draw.line([(px, py - cross), (px, py + cross)], fill=(0, 0, 0), width=3)
        tip_x = px + dx * (cross + 2)   # tip just inside the grid edge
        tip_y = py + dy * (cross + 2)
        draw_directional_arrow(draw, tip_x, tip_y, dx, dy,
                               a_shaft_len, a_shaft_thick, a_head_len, a_head_wid)

    # Two pins per baseplate edge, arrows pointing inward toward the mosaic.
    for w in range(blockWidth):
        left = grid_left + length * w
        pin(left + 0.25 * length, grid_top, 0, 1)       # top edge -> down
        pin(left + 0.75 * length, grid_top, 0, 1)
        pin(left + 0.25 * length, grid_bottom, 0, -1)   # bottom edge -> up
        pin(left + 0.75 * length, grid_bottom, 0, -1)
    for h in range(blockHeight):
        top = grid_top + length * h
        pin(grid_left, top + 0.25 * length, 1, 0)       # left edge -> right
        pin(grid_left, top + 0.75 * length, 1, 0)
        pin(grid_right, top + 0.25 * length, -1, 0)      # right edge -> left
        pin(grid_right, top + 0.75 * length, -1, 0)

    # Caption under the grid.
    caption_font = get_font(20)
    caption = "Each arrow marks one axle pin"
    cap_w = _text_width(draw, caption, caption_font)
    draw.text((center_x - cap_w / 2.0, grid_bottom + fb + 10),
              caption, fill="black", font=caption_font)


def draw_frame_setup_instruction(width, height, step, output_dir):
    img, draw = get_img_and_draw(step, True, output_dir)
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    im_w, im_h = img.size
    middle_x_of_image = (int)(im_w / 2)
    middle_y_of_image = (int)(im_h / 2)
    draw = ImageDraw.Draw(img)
    font = get_font(28)
    small_font = get_font(22)
    
    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)
    perimeter = (blockWidth * 2) + (blockHeight * 2)

    black = to_rgb((.3, .3, .3))
    light_grey = to_rgb((.8, .8, .8))
    red = to_rgb((.8, .1, .1))
    blue = to_rgb((.1, .1, .8))
    green = to_rgb((.1, .8, .1))
    
    length = 60
    circle_radius = length / 4
    x_offset = middle_x_of_image - ((blockWidth / 2) * length) #center on middle of page
    y_offset = 350
    edges = []
    seams = []
    #draw grid/corners and calculate frame piece placement
    for w in range(0, blockWidth):
        for h in range(0, blockHeight):
            #draw light square to indicate mosiac
            x = x_offset + length * w
            y = y_offset + length * h
            cube = [
                (x, y),
                (x + length, y),
                (x + length, y + length),
                (x, y + length),
            ]
            draw.polygon(cube, light_grey, outline=(10,10,10))
            draw2.polygon(cube, light_grey, outline=(10,10,10))
            #corners
            if (w == 0 and h == 0) or (w == blockWidth - 1 and h == 0) or (w == 0 and h == blockHeight - 1) or (w == blockWidth - 1 and h == blockHeight - 1):
                new_x = x
                new_y = y
                if w == blockWidth - 1:
                    new_x += length
                if h == blockHeight - 1:
                    new_y += length
                circle = [
                    (new_x - circle_radius),
                    (new_y - circle_radius),
                    (new_x + circle_radius),
                    (new_y + circle_radius),
                ]
                draw2.ellipse(circle, fill=red, outline=(10,10,10))
                #handle one width
                if (blockWidth == 1 and h == 0):
                    circle = [
                        (x - circle_radius),
                        (y - circle_radius),
                        (x + circle_radius),
                        (y + circle_radius),
                    ]
                    draw2.ellipse(circle, fill=red, outline=(10,10,10))
                    circle = [
                        (x - circle_radius + length),
                        (y - circle_radius),
                        (x + circle_radius + length),
                        (y + circle_radius),
                    ]
                    draw2.ellipse(circle, fill=red, outline=(10,10,10))
                if (blockWidth == 1 and h == blockHeight - 1):
                    circle = [
                        (x - circle_radius),
                        (y - circle_radius + length),
                        (x + circle_radius),
                        (y + circle_radius + length),
                    ]
                    draw2.ellipse(circle, fill=red, outline=(10,10,10))
                    circle = [
                        (x - circle_radius + length),
                        (y - circle_radius + length),
                        (x + circle_radius + length),
                        (y + circle_radius + length),
                    ]
                    draw2.ellipse(circle, fill=red, outline=(10,10,10))
                #Handle one height
                if (blockHeight == 1 and w == 0):
                    circle = [
                        (x - circle_radius),
                        (y - circle_radius),
                        (x + circle_radius),
                        (y + circle_radius),
                    ]
                    draw2.ellipse(circle, fill=red, outline=(10,10,10))
                if (blockHeight == 1 and w == blockWidth - 1):
                    circle = [
                        (x - circle_radius + length),
                        (y - circle_radius),
                        (x + circle_radius + length),
                        (y + circle_radius),
                    ]
                    draw2.ellipse(circle, fill=red, outline=(10,10,10))

                #save two edges per corner (or if the width/heigh is 1) to the edges list
                if w == 0 and h == 0:
                    circle = [
                        (x - circle_radius + length / 2),
                        (y - circle_radius),
                        (x + circle_radius + length / 2),
                        (y + circle_radius),
                    ]
                    edges.append(circle)
                    circle = [
                        (x - circle_radius),
                        (y - circle_radius + length / 2),
                        (x + circle_radius),
                        (y + circle_radius + length / 2),
                    ]
                    edges.append(circle)
                if w == blockWidth - 1 and h == 0:
                    circle = [
                        (x - circle_radius + length / 2),
                        (y - circle_radius),
                        (x + circle_radius + length / 2),
                        (y + circle_radius),
                    ]
                    edges.append(circle)
                    circle = [
                        (x - circle_radius + length),
                        (y - circle_radius + length / 2),
                        (x + circle_radius + length),
                        (y + circle_radius + length / 2),
                    ]
                    edges.append(circle)
                if w == 0 and h == blockHeight - 1:
                    circle = [
                        (x - circle_radius + length / 2),
                        (y - circle_radius + length),
                        (x + circle_radius + length / 2),
                        (y + circle_radius + length),
                    ]
                    edges.append(circle)
                    circle = [
                        (x - circle_radius),
                        (y - circle_radius + length / 2),
                        (x + circle_radius),
                        (y + circle_radius + length / 2),
                    ]
                    edges.append(circle)
                if w == blockWidth - 1 and h == blockHeight - 1:
                    circle = [
                        (x - circle_radius + length / 2),
                        (y - circle_radius + length),
                        (x + circle_radius + length / 2),
                        (y + circle_radius + length),
                    ]
                    edges.append(circle)
                    circle = [
                        (x - circle_radius + length),
                        (y - circle_radius + length / 2),
                        (x + circle_radius + length),
                        (y + circle_radius + length / 2),
                    ]
                    edges.append(circle)
            #perimeter edges that are not corners
            if ((w == 0 or w == blockWidth - 1) and (h != 0 and h != blockHeight - 1)) or ((h == 0 or h == blockHeight - 1) and (w != 0 and w != blockWidth - 1)):
                new_x = x
                new_y = y
                if h != blockHeight - 1 and h != 0:
                    new_y += length / 2
                if w != blockWidth - 1 and w != 0:
                    new_x += length / 2
                if w == blockWidth - 1:
                    new_x += length
                if h == blockHeight - 1:
                    new_y += length
                circle = [
                    (new_x - circle_radius),
                    (new_y - circle_radius),
                    (new_x + circle_radius),
                    (new_y + circle_radius),
                   ]
                edges.append(circle)
                if (blockWidth == 1):
                    #draw edges on other side
                    circle = [
                        (new_x - circle_radius - length),
                        (new_y - circle_radius),
                        (new_x + circle_radius - length),
                        (new_y + circle_radius),
                    ]
                    edges.append(circle)
                if (blockHeight == 1):
                    #draw edges on other side
                    circle = [
                        (new_x - circle_radius),
                        (new_y - circle_radius - length),
                        (new_x + circle_radius),
                        (new_y + circle_radius - length),
                    ]
                    edges.append(circle)
    
    for w in range(0, blockWidth + 1):
        for h in range(0, blockHeight + 1):
            x = x_offset + length * w
            y = y_offset + length * h
            #perimeter edges including corners
            if (w == 0 or w == blockWidth or h == 0 or h == blockHeight):
                #dont draw on corners
                if (w == 0 and (h == 0 or h == blockHeight)) or (w == blockWidth and (h == 0 or h == blockHeight)):
                    continue
                circle = [
                    (x - circle_radius),
                    (y - circle_radius),
                    (x + circle_radius),
                    (y + circle_radius),
                ]
                seams.append(circle)


    #instruct user to place corners on corners
    draw2.text((30, 100), "Put frame corners below each corner", fill="black", font=font)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step

    
    #instruct user to place 8x1 on baseplate centers
    to_reuse = img.copy()
    baseplate_middles = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw3 = ImageDraw.Draw(baseplate_middles)
    #draw 8x1 on each edge face
    for edge in edges:
        draw2.ellipse(edge, fill=green, outline=(10,10,10))
        draw3.ellipse(edge, fill=green, outline=(10,10,10))

    draw2.text((30, 100), "Put long frame parts below baseplate centers", fill="black", font=font)
    draw2.text((30, 150), "(long frame pieces have axle bricks on each side)", fill="black", font=small_font)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step
    
    #instruct user to place 4x1 on baseplate seams if numOfConnectors is not 0
    numOfConnectors =  perimeter - 4
    if numOfConnectors != 0:
        to_reuse = img.copy()
        draw2 = ImageDraw.Draw(to_reuse)
        for seam in seams:
            draw2.ellipse(seam, fill=blue, outline=(10,10,10))

        draw2.text((30, 100), "Put short frame parts below baseplate seams", fill="black", font=font)
        step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step

    #draw axle pins going into axle blocks
    img, draw = get_img_and_draw(step, True, output_dir)
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)

    draw2.text((30, 100), "Push axle pins into holes all the way", fill="black", font=font)
    draw2.text((30, middle_y_of_image), "The frame and mosaic should be connected", fill="black", font=font)
    # axle BRICK (with the cross-shaped hole) now on the RIGHT side
    draw_ortho_plate(draw2, -4, 24, True, 2, 1, 3, (.3, .3, .3), False)
    # axle PIN photo now on the LEFT side
    BASE_DIR = Path(__file__).resolve().parent
    axle_pin = Image.open(BASE_DIR / "axle_pin.jpg").convert("RGBA") #TODO: ensure file path works on hosted service
    axle_pin = axle_pin.resize((100, 100))
    to_reuse.paste(axle_pin, (90, 150), axle_pin)
    # arrow pointing FROM the pin (left) TO the hole (right); both sit at y~200
    draw_arrow(draw2, 200, 200, 100, 22, 40, 44, fill="black")
    # lower-half map: where every axle pin locks the frame onto the mosaic
    draw_axle_pin_map(draw2, blockWidth, blockHeight, middle_x_of_image, 430, 740)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step

    #display plate and brick layer (those layers should be complete now)
    draw3.text((30, 100), "Place 16x1 bricks here to complete the layer", fill="black", font=font)
    draw_plate_sized(draw3, -3, 10, 1, 16, 4, (.3, .3, .3))
    step = save_img_and_increment_step(baseplate_middles, step, output_dir) # Save current step

    #add top layer of thin corner plates and flat 4x1 plates
    img, draw = get_img_and_draw(step, True, output_dir)
    draw.text((30, 100), "Place corner plates on corners", fill="black", font=font)
    draw_corner_plate(draw, 0, 8, 0, 3, 1, (.3, .3, .3), 2, 2, False) #3x3 corner
    draw_ortho_plate(draw, 16, -8, False, 4, 1, 1, (.3, .3, .3), False, True) 
    draw.text((30, middle_y_of_image), "Place all flat plates to fill the sides", fill="black", font=font)

    step = save_img_and_increment_step(img, step, output_dir) # Save current step
    return step


#function for drawing frame around mosiac, saving each step until frame is complete
def draw_frame_for_mosiac(width, height, step, output_dir):
    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)
    black = (.3, .3, .3)
    perimeter = (blockWidth * 2) + (blockHeight * 2)
    
    #draw corner plate
    img, draw = get_img_and_draw(step, True, output_dir) #want clean state for this
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)

    # draw stuff (highlight on draw2 and no highlight on draw)
    draw_corner_plate(draw, 4, 4, 0, 4, 2, black, 2, 2, False)
    draw_corner_plate(draw2, 4, 4, 0, 4, 2, black, 2, 2, True)
    draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[6483102], 1)])  # corner plate x1 (per assembled corner)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #draw corner brick
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw_corner_brick(draw, 1, 15, 0, black, False)
    draw_corner_brick(draw2, 1, 15, 0, black, True)
    draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[235726], 1)])  # corner brick x1 (per assembled corner)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #add 1x1 bricks (to side of corner brick and top edge of corner brick)
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    #piece 1
    draw_brick(draw, -3, 15, 0, black, False)
    draw_brick(draw2, -3, 15, 0, black, True)
    #piece 2
    draw_brick(draw, 5, 15, 0, black, False)
    draw_brick(draw2, 5, 15, 0, black, True)
    #piece 3
    draw_brick(draw, -2, 22, 0, black, False)
    draw_brick(draw2, -2, 22, 0, black, True)
    # legend: 3 one-by-one bricks per assembled corner (onexoneBricks in
    # GetFrameForSize). This is the corner's final sub-step, so the corner is now
    # complete — show how many corners to make (4, one per baseplate corner).
    draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[300526], 3)])
    draw_big_quantity(draw2, 4, 430, 330)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #set up wider background to capture entire piece
    bg_color = (255, 255, 255, 255) # white background
    image_size = (800, 792)
    img = Image.new("RGBA", image_size, bg_color)
    draw = ImageDraw.Draw(img)
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    #10x2 plate
    draw_ortho_plate(draw, 3, 0, False, 10, 2, 1, black, False)
    draw_ortho_plate(draw2, 3, 0, False, 10, 2, 1, black, True)
    draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[383226], 1)])  # 10x2 plate x1 (per assembled long edge)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #8x1 brick: 1
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw_ortho_plate(draw, 0, 7, False, 8, 1, 3, black, False)
    draw_ortho_plate(draw2, 0, 7, False, 8, 1, 3, black, True)
    draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[300826], 1)])  # 8x1 brick x1 (per assembled long edge)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #2x1 axle bricks: 2
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    #left axle brick
    draw_ortho_plate(draw, -4, 3, True, 2, 1, 3, black, False)
    draw_ortho_plate(draw2, -4, 3, True, 2, 1, 3, black, True)
    #right axle brick
    draw_ortho_plate(draw, 16, 23, True, 2, 1, 3, black, False)
    draw_ortho_plate(draw2, 16, 23, True, 2, 1, 3, black, True)
    # legend: 2 axle bricks per assembled long edge (twoxoneBricksWithAxleHole).
    # This is the long edge's final sub-step, so it's now complete — show how many
    # long edges to make: one per baseplate edge = perimeter = 2*(blockW+blockH).
    draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[6178922], 2)])
    draw_big_quantity(draw2, perimeter, 600, 210)
    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #each block that is connected to another block should have a 4x1 brick and 6x2 plate
    numOfConnectors = perimeter - 4
    if numOfConnectors != 0:
        #6x2 plate
        img, draw = get_img_and_draw(step, True, output_dir) #get clean slate for this step
        to_reuse = img.copy()
        draw2 = ImageDraw.Draw(to_reuse)
        draw_ortho_plate(draw, 4, 0, False, 6, 2, 1, black, False)
        draw_ortho_plate(draw2, 4, 0, False, 6, 2, 1, black, True)
        draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[379526], 1)])  # 6x2 plate x1 (per assembled connector)
        step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

        #4x1 brick
        to_reuse = img.copy()
        draw2 = ImageDraw.Draw(to_reuse)
        draw_ortho_plate(draw, 1, 7, False, 4, 1, 3, black, False)
        draw_ortho_plate(draw2, 1, 7, False, 4, 1, 3, black, True)
        # legend: 1 4x1 brick per assembled connector. This is the connector's
        # final sub-step, so it's now complete — show how many connectors to make:
        # one per baseplate seam = numOfConnectors.
        draw_step_parts_legend(draw2, [(ps.SPEC_BY_ELEMENT[301026], 1)])
        draw_big_quantity(draw2, numOfConnectors, 450, 330)
        step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step (with current step pieces highlighted)

    #return step once all actions are taken
    return step

#high level function to create instructions for the grid and frame setup that calls medium level functions                                                                                                                                                                                          
def draw_frame_instructions(width, height, step, output_dir=None):
    #shows the connection of grid cells into columns and column into grid (now handled externally)
    #step = draw_grid_setup_instruction(step)
    #shows steps for making the pieces of the frame
    step = draw_frame_for_mosiac(width, height, step, output_dir)
    #show steps for putting together frame pieces
    step = draw_frame_setup_instruction(width, height, step, output_dir)
    return step #return step for any future use

#draws final views of completed mosiac (all put together) with frame if applicable
def draw_final_view(step, composite, want_frame, output_dir=None):
    img, draw = get_img_and_draw(step, True, output_dir)
    log_info("drawing final view...")
    black = to_rgb((.2, .2, .2))
    #aspect ratio of image
    composite_aspect_ratio = composite.height / composite.width
    #desired width of composite
    desired_comp_width = 400
    comp_h = int(round(desired_comp_width * (composite_aspect_ratio)))
    #scale such that the composite/frame always take up certain portion of canvas
    composite_resized = composite.resize((desired_comp_width, comp_h), Image.Resampling.NEAREST)
    #half heights to center drawings
    half_comp_w = desired_comp_width / 2
    half_comp_h = composite_resized.height / 2
    frame_thickness = 15
    start_y = (int)(img.height / 2)
    start_x = (int)(img.width / 2)
    #draw box before composite to simulate frame
    if want_frame:
        y1 = start_y - half_comp_h - frame_thickness
        y2 = start_y + half_comp_h + frame_thickness
        x1 = start_x - frame_thickness - half_comp_w
        x2 = start_x + frame_thickness + half_comp_w
        frame = [
            (x1, y1), #top left
            (x2, y1), #top right
            (x2, y2), #bottom right
            (x1, y2) #bottom left
        ]
        draw.polygon(frame, black, outline=(10,10,10))
    #draw composite at start_x, start_y
    composite_x = (int)(start_x - half_comp_w)
    composite_y = (int)(start_y - half_comp_h)
    img.paste(composite_resized, (composite_x, composite_y))


    font = get_font(20)
    margin = 50
    placement = (margin, margin)
    text = "Admire your artwork (add frame hooks to back is desired)"
    draw.text(placement, text, fill="black", font=font)


    log_debug("finished drawing final view!")
    step = save_img_and_increment_step(img, step, output_dir)
    return step
