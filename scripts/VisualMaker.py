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
def draw_baseplate_top(c, size=16, color=Color(0.2, 0.2, 0.2), case=0):
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
        (x + w,       y),                     # top-right
        (x + hw,      y - hh),                # top-left
        (x + hw,      y - hh - PLATE_HEIGHT), # bottom-left
        (x + w,       y - PLATE_HEIGHT),      # bottom-right
    ]
    front = [
        (x,           y),                     # top-left
        (x + hw,      y - hh),                # top-right
        (x + hw,      y - hh - PLATE_HEIGHT), # bottom-right
        (x,           y - PLATE_HEIGHT),      # bottom-left
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
            # --- Stud front wall (same as draw_plate) ---
            wall_top = sy_iso + 2.5
            wall_bottom = sy_iso - 2.5
            front_wall = [
                (stud_left, wall_top), (stud_right, wall_top), (stud_right, wall_bottom), (stud_left, wall_bottom)
            ]
            c.setFillColor(Color(0.2, 0.2, 0.2))
            draw_polygon(c, front_wall, front_color)
            c.setFillColor(Color(0.25, 0.25, 0.25))
            c.ellipse(
                stud_left,
                sy_iso + STUD_OFFSET_Y,
                stud_right,
                sy_iso + STUD_OFFSET_Y + STUD_HEIGHT,
                fill=1
            )
 
    #add ellipses for the 12 holes on the front and right of the baseplate
    c.setFillColor(Color(0.1, 0.1, 0.1))
    side_holes = [
        (2.7, -1.15), (3.7, -1.15), (4.7, -1.15), (5.7, -1.15), (6.7, -1.15), (7.7, -1.15), (8.7, -1.15), (9.7, -1.15), (10.7, -1.15), (11.7, -1.15), (12.7, -1.15), (13.7, -1.15),
 
        (16.15, 1.35), (16.15, 2.35), (16.15, 3.35), (16.15, 4.35), (16.15, 5.35), (16.15, 6.35), (16.15, 7.35), (16.15, 8.35), (16.15, 9.35), (16.15, 10.35), (16.15, 11.35), (16.15, 12.35)
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
 
        c.ellipse(
            cx - VERTICAL_WIDTH/2,
            cy - VERTICAL_HEIGHT/2,
            cx + VERTICAL_WIDTH/2,
            cy + VERTICAL_HEIGHT/2,
            fill=1
        )


    #TODO: add case 0-3 for baseplate connections and render accordingly
    if (case == 0):
        #red and green connectors
        pass
    if (case == 1):
        #red connectors no green
        pass
    if (case == 2):
        #green connectors no red
        pass
    if (case == 3):
        #no connectors
        pass
 
#this function draws a baseplate that is face up
def draw_baseplate_bottom(c, size=16, color=Color(0.2, 0.2, 0.2), case=0):
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
        (x + w,       y),                     # top-right
        (x + hw,      y - hh),                # top-left
        (x + hw,      y - hh - PLATE_HEIGHT), # bottom-left
        (x + w,       y - PLATE_HEIGHT),      # bottom-right
    ]
    front = [
        (x,           y),                     # top-left
        (x + hw,      y - hh),                # top-right
        (x + hw,      y - hh - PLATE_HEIGHT), # bottom-right
        (x,           y - PLATE_HEIGHT),      # bottom-left
    ]
 
    draw_polygon(c, top, top_color)
    draw_polygon(c, right, right_color)
    draw_polygon(c, front, front_color)

    islands = [
        (.5, .5),     # bottom-left
        (.5, 14.5),    # top-left
        (14.5, 14.5),   # top-right
        (14.5, .5),    # bottom-right
    ]
    c.setStrokeColor(Color(0.05, 0.05, 0.05))
    c.setLineWidth(1.25)

    iso_points = [block_center_xy(bx, by, xOffset, yOffset) for bx, by in islands]


    for i in range(len(iso_points)):
        x1, y1 = iso_points[i]
        x2, y2 = iso_points[(i + 1) % len(iso_points)]
        c.line(x1, y1, x2, y2)

    bottom_left_square = [
        (x + 0,               y + 0),
        (x + PLATE_WIDTH, y + PLATE_HALF_WIDTH),
        (x + PLATE_WIDTH * 2,      y + 0),
        (x + PLATE_WIDTH * .95, y - PLATE_HALF_WIDTH * .95)
    ]
    draw_polygon(c, bottom_left_square, top_color)

    dx = -PLATE_WIDTH * 7 #- PLATE_HALF_WIDTH
    dy = -PLATE_WIDTH * 4 + PLATE_HALF_WIDTH
    top_left_square = [(x - dx, y - dy) for x, y in bottom_left_square]
    draw_polygon(c, top_left_square, top_color)

    dx = -PLATE_WIDTH * 14 #- PLATE_HALF_WIDTH
    dy = 0
    top_right_square = [(x - dx, y - dy) for x, y in bottom_left_square]
    draw_polygon(c, top_right_square, top_color)

    dx = -PLATE_WIDTH * 7 #- PLATE_HALF_WIDTH
    dy = PLATE_WIDTH * 4 - PLATE_HALF_WIDTH
    bottom_right_square = [(x - dx, y - dy) for x, y in bottom_left_square]
    draw_polygon(c, bottom_right_square, top_color)

    # -----------------
    # Big middle 3D square
    # -----------------

    middle = x + PLATE_WIDTH * 6
    top_hh = PLATE_HALF_WIDTH * 2
    depth = PLATE_HEIGHT * 0.8  # slightly thinner than a plate

    # --- Top face ---
    big_top = [
        (middle + 0,               y + 0),
        (middle + PLATE_WIDTH * 2, y + top_hh),
        (middle + PLATE_WIDTH * 4, y + 0),
        (middle + PLATE_WIDTH * 2, y - top_hh),
    ]

    # --- Right face ---
    big_right = [
        (middle + PLATE_WIDTH * 4, y + 0),
        (middle + PLATE_WIDTH * 2, y - top_hh),
        (middle + PLATE_WIDTH * 2, y - top_hh - depth),
        (middle + PLATE_WIDTH * 4, y - depth),
    ]

    # --- Front face ---
    big_front = [
        (middle + 0,               y + 0),
        (middle + PLATE_WIDTH * 2, y - top_hh),
        (middle + PLATE_WIDTH * 2, y - top_hh - depth),
        (middle + 0,               y - depth),
    ]

    draw_polygon(c, big_top,   top_color)
    draw_polygon(c, big_right, right_color)
    draw_polygon(c, big_front, front_color)



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
    dx = -0.1
    dy = 0.1
    studs = [(x - dx, y - dy) for x, y in studs]

 
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
 
    #add ellipses for the 12 holes on the front and right of the baseplate
    side_holes = [
        (2.7, -1.15), (3.7, -1.15), (4.7, -1.15), (5.7, -1.15), (6.7, -1.15), (7.7, -1.15), (8.7, -1.15), (9.7, -1.15), (10.7, -1.15), (11.7, -1.15), (12.7, -1.15), (13.7, -1.15),
 
        (16.15, 1.35), (16.15, 2.35), (16.15, 3.35), (16.15, 4.35), (16.15, 5.35), (16.15, 6.35), (16.15, 7.35), (16.15, 8.35), (16.15, 9.35), (16.15, 10.35), (16.15, 11.35), (16.15, 12.35)
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
        c.ellipse(
            cx - VERTICAL_WIDTH/2,
            cy - VERTICAL_HEIGHT/2,
            cx + VERTICAL_WIDTH/2,
            cy + VERTICAL_HEIGHT/2,
            fill=1
        )

    #TODO: add case 0-3 for baseplate connections and render accordingly
    if (case == 0):
        #red and green connectors
        pass
    if (case == 1):
        #red connectors no green
        pass
    if (case == 2):
        #green connectors no red
        pass
    if (case == 3):
        #no connectors
        pass

    

def block_center_xy(bx, by, xOffset, yOffset):
    return get_block_xy(bx + xOffset + 0.5, by + yOffset + 0.5)

 
# -----------------------------
# Example usage
# -----------------------------
c = canvas.Canvas("lego_plates.pdf")
#case 0: red and green connectors
#case 1: red connectors no green
#case 2: green connectors no red
#case 3: no connectors
draw_baseplate_top(c, 16, Color(0.2, 0.2, 0.2), 0)
draw_plate(c, 0, 1, Color(1, 0.8, 0.2))
draw_plate(c, 0, 0, Color(1, 0.2, 0.2))
draw_plate(c, 1, 1, Color(0.2, 0.6, 1))
draw_plate(c, 15, 0, Color(0.2, 0.8, .2))
draw_plate(c, 0, 15, Color(0.2, 0.8, .2))
draw_plate(c, 15, 15, Color(0.2, 0.8, .2))
c.save()
b = canvas.Canvas("backplateTest.pdf")
draw_baseplate_bottom(b, 16, Color(0.2, 0.2, 0.2), 0)
b.save()

print("finished visuals!")