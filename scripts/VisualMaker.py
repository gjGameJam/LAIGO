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
# Draw a 1Ã1 LEGO plate
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

# -----------------------------
# Example usage
# -----------------------------
c = canvas.Canvas("lego_plates.pdf")

draw_plate(c, 0, 1, Color(1, 0.8, 0.2))
draw_plate(c, 0, 0, Color(1, 0.2, 0.2))
draw_plate(c, 1, 1, Color(0.2, 0.6, 1))
draw_plate(c, 16, 0, Color(0.2, 0.8, .2))
draw_plate(c, 0, 16, Color(0.2, 0.8, .2))
draw_plate(c, 16, 16, Color(0.2, 0.8, .2))

c.save()
