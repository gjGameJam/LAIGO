from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color

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

# -----------------------------
# Helper to draw polygons
# -----------------------------
def draw_polygon(c, points, fill_color):
    c.setFillColor(fill_color)
    path = c.beginPath()
    path.moveTo(points[0][0], points[0][1])
    for x, y in points[1:]:
        path.lineTo(x, y)
    path.close()
    c.drawPath(path, stroke=1, fill=1)

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

# -----------------------------
# Draw a 1x1 LEGO plate
# -----------------------------
def draw_plate(c, blockX, blockY, color):

    x, y = get_block_xy(blockX, blockY)

    # Shading
    top_color = color
    right_color = Color(color.red * 0.85, color.green * 0.85, color.blue * 0.85)
    front_color = Color(color.red * 0.70, color.green * 0.70, color.blue * 0.70)

    # --- Plate faces ---
    top = [
        (x + 0,               y + 0),
        (x + PLATE_HALF_WIDTH, y + PLATE_HALF_HEIGHT),
        (x + PLATE_WIDTH,      y + 0),
        (x + PLATE_HALF_WIDTH, y - PLATE_HALF_HEIGHT)
    ]

    right = [
        (x + PLATE_WIDTH,      y + 0),
        (x + PLATE_HALF_WIDTH, y - PLATE_HALF_HEIGHT),
        (x + PLATE_HALF_WIDTH, y - PLATE_HEIGHT),
        (x + PLATE_WIDTH,      y - PLATE_HALF_HEIGHT)
    ]

    front = [
        (x + 0,               y + 0),
        (x + PLATE_HALF_WIDTH, y - PLATE_HALF_HEIGHT),
        (x + PLATE_HALF_WIDTH, y - PLATE_HEIGHT),
        (x + 0,               y - PLATE_HALF_HEIGHT)
    ]

    draw_polygon(c, top, top_color)
    draw_polygon(c, right, right_color)
    draw_polygon(c, front, front_color)

    # --- Stud front wall ---
    stud_left = x + STUD_OFFSET_X
    stud_right = stud_left + STUD_WIDTH
    stud_top = y + 2.5
    stud_bottom = y - 2.5

    front_wall = [
        (stud_left,  stud_top),
        (stud_right, stud_top),
        (stud_right, stud_bottom),
        (stud_left,  stud_bottom)
    ]
    draw_polygon(c, front_wall, front_color)

    # --- Stud top (ellipse) ---
    c.setFillColor(top_color)
    c.ellipse(
        stud_left,
        y + STUD_OFFSET_Y,
        stud_right,
        y + STUD_OFFSET_Y + STUD_HEIGHT,
        fill=1
    )

#pass in 16 colors to draw a column of plates on a specific column
def draw_plate_column(c, start_blockX, colors):
    blockY = 0
    for color in colors:
        draw_plate(c, start_blockX, blockY, color)
        blockY += 1

#this function draws a baseplate that is face up
#TODO: add case 1-4 for baseplate connections and render accordingly
def draw_baseplate_top(c, size=16, color=Color(0.2, 0.2, 0.2)):
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
    top_color = color
    right_color = Color(color.red * 0.85, color.green * 0.85, color.blue * 0.85)
    front_color = Color(color.red * 0.70, color.green * 0.70, color.blue * 0.70)

    # -----------------
    # Baseplate faces
    # -----------------

    top = [
        (x,           y),
        (x + hw,      y + hh),
        (x + w,       y),
        (x + hw,      y - hh),
    ]

    right = [
        (x + w,       y),
        (x + hw,      y - hh),
        (x + hw,      y - hh - PLATE_HEIGHT),
        (x + w,       y - PLATE_HALF_HEIGHT),
    ]

    front = [
        (x,           y),
        (x + hw,      y - hh),
        (x + hw,      y - hh - PLATE_HEIGHT),
        (x,           y - PLATE_HALF_HEIGHT),
    ]

    draw_polygon(c, top, top_color)
    draw_polygon(c, right, right_color)
    draw_polygon(c, front, front_color)

    # -----------------
    # Studs (top only)
    # -----------------

    for sy in range(size):
        for sx in range(size):
            sx_iso, sy_iso = get_block_xy(sx + xOffset, sy + yOffset)

            stud_left = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH

            c.setFillColor(top_color)
            c.ellipse(
                stud_left,
                sy_iso + STUD_OFFSET_Y,
                stud_right,
                sy_iso + STUD_OFFSET_Y + STUD_HEIGHT,
                fill=1
            )


#this function draws a baseplate that is face up
def draw_baseplate_bottom(c, size=16, color=Color(0.2, 0.2, 0.2)):
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
    top_color = color
    right_color = Color(color.red * 0.85, color.green * 0.85, color.blue * 0.85)
    front_color = Color(color.red * 0.70, color.green * 0.70, color.blue * 0.70)

    # -----------------
    # Baseplate faces
    # -----------------

    top = [
        (x,           y),
        (x + hw,      y + hh),
        (x + w,       y),
        (x + hw,      y - hh),
    ]

    right = [
        (x + w,       y),
        (x + hw,      y - hh),
        (x + hw,      y - hh - PLATE_HEIGHT),
        (x + w,       y - PLATE_HALF_HEIGHT),
    ]

    front = [
        (x,           y),
        (x + hw,      y - hh),
        (x + hw,      y - hh - PLATE_HEIGHT),
        (x,           y - PLATE_HALF_HEIGHT),
    ]

    draw_polygon(c, top, top_color)
    draw_polygon(c, right, right_color)
    draw_polygon(c, front, front_color)

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


    c.setFillColor(Color(0.1, 0.1, 0.1))

    for sx, sy in studs:
        sx_iso, sy_iso = get_block_xy(
            sx + xOffset,
            sy + yOffset
        )

        stud_left = sx_iso + STUD_OFFSET_X
        stud_right = stud_left + STUD_WIDTH

        c.ellipse(
            stud_left,
            sy_iso + STUD_OFFSET_Y,
            stud_right,
            sy_iso + STUD_OFFSET_Y + STUD_HEIGHT,
            fill=1
        )

    #TODO: add ellipses for the 12 holes on the sides of the baseplate

    #TODO: add case 1-4 for baseplate connections and render accordingly


# -----------------------------
# Example usage
# -----------------------------
c = canvas.Canvas("lego_plates.pdf")

draw_baseplate_top(c)
draw_plate(c, 0, 1, Color(1, 0.8, 0.2))
draw_plate(c, 0, 0, Color(1, 0.2, 0.2))
draw_plate(c, 1, 1, Color(0.2, 0.6, 1))
draw_plate(c, 15, 0, Color(0.2, 0.8, .2))
draw_plate(c, 0, 15, Color(0.2, 0.8, .2))
draw_plate(c, 15, 15, Color(0.2, 0.8, .2))

c.save()

b = canvas.Canvas("backplateTest.pdf")
draw_baseplate_bottom(b)
b.save()
