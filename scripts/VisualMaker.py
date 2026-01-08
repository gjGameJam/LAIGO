from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color
from pathlib import Path
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
 
#this function draws a baseplate that is face up
def draw_baseplate_top(c, size=16, color=Color(0.2, 0.2, 0.2), case=0):
    """
    Draw a single monolithic NxN LEGO baseplate (height = 1 plate)
    with NxN studs.
    """
    green_color = Color(.647, .792, .09)
    if (case == 0):
        #red and green connectors
        draw_plate_sized(c, 16.5, 4.5, 2, 4, green_color)   # 2x4
        draw_plate_sized(c, 7.5, -2.5, 4, 2, Color(0.8, 0.1, 0.1))   # 4x2       
    if (case == 1):
        #red connectors no green
        draw_plate_sized(c, 7.5, -2.5, 4, 2, Color(0.8, 0.1, 0.1))   # 4x2
    if (case == 2):
        #green connectors no red
        draw_plate_sized(c, 16.5, 4, 2, 4, green_color)   # 2x4
    if (case == 3):
        #no connectors (already handled)
        pass

    #2x2 grey plates on bottom of plate
    draw_plate_sized(c, 1.5, -1.5, 2, 2, Color(0.44, 0.44, 0.44))
    draw_plate_sized(c, 15.5, -1.5, 2, 2, Color(0.44, 0.44, 0.44))
    draw_plate_sized(c, 15.5, 12.5, 2, 2, Color(0.44, 0.44, 0.44))
    
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
    
    red_holes = [
        (3.7, -1.15), (12.7, -1.15)
    ]

    green_holes = [
        (16.15, 2.35), (16.15, 11.35)
    ]


    #case 0-3 for baseplate connections
    if (case == 0):
        #red and green connectors
        #red connectors
        c.setFillColor(Color(0.8, 0.1, 0.1))
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )

        

        #then do green holes
        c.setFillColor(green_color)
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )
        return
    if (case == 1):
        #red connectors
        c.setFillColor(Color(0.8, 0.1, 0.1))
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )
        return
    if (case == 2):
        #green connectors no red
        c.setFillColor(green_color)
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )
        return
    if (case == 3):
        #no connectors (already handled)
        return
 
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
    green_color = Color(.647, .792, .09)
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

    green_holes = [
        (3.7, -1.15), (12.7, -1.15)
    ]

    red_holes = [
        (16.15, 2.35), (16.15, 11.35)
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

    #case 0-3 for baseplate connections
    if (case == 0):
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(c, 0, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 0, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 7, 7, 2, 2, Color(0.44, 0.44, 0.44))
        #red and green plates upside down
        draw_plate_sized_upside_down(c, 6, -1, 4, 2, green_color)
        draw_plate_sized_upside_down(c, 15, 6, 2, 4, Color(0.8, 0.1, 0.1))
        #red and green connectors
        #red connectors
        c.setFillColor(Color(0.8, 0.1, 0.1))
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )

        # draw back of green connectors
        xSpot = x + 72
        ySpot = y - 31
        greenPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, greenPin, green_color)

        xSpot = x + 210
        ySpot = y - 100
        greenPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, greenPin, green_color)

        # draw back of red connectors
        xSpot = x + 270
        ySpot = y - 100
        redPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, redPin, Color(0.8, 0.1, 0.1))

        xSpot = x + 405
        ySpot = y - 33
        redPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, redPin, Color(0.8, 0.1, 0.1))

        #then do green holes
        c.setFillColor(green_color)
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )
        return
    if (case == 1):
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(c, 0, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 0, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 7, 7, 2, 2, Color(0.44, 0.44, 0.44))
        #red plate upside
        draw_plate_sized_upside_down(c, 15, 6, 2, 4, Color(0.8, 0.1, 0.1))
        #red connectors
        c.setFillColor(Color(0.8, 0.1, 0.1))
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )
        # draw back of red connectors
        xSpot = x + 270
        ySpot = y - 100
        redPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, redPin, Color(0.8, 0.1, 0.1))

        xSpot = x + 405
        ySpot = y - 33
        redPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, redPin, Color(0.8, 0.1, 0.1))
        return
    if (case == 2):
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(c, 0, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 0, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 7, 7, 2, 2, Color(0.44, 0.44, 0.44))
        #green plate upside down
        draw_plate_sized_upside_down(c, 6, -1, 4, 2, green_color)
        #green connectors no red
        c.setFillColor(green_color)
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
    
            c.ellipse(
                cx - VERTICAL_WIDTH/2,
                cy - VERTICAL_HEIGHT/2,
                cx + VERTICAL_WIDTH/2,
                cy + VERTICAL_HEIGHT/2,
                fill=1
            )
        # draw back of green connectors
        xSpot = x + 72
        ySpot = y - 31
        greenPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, greenPin, green_color)

        xSpot = x + 210
        ySpot = y - 100
        greenPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]

        draw_polygon(c, greenPin, green_color)
        return
    if (case == 3):
        #no connectors (already handled)
        #draw upside down pieces
        #2x2s
        draw_plate_sized_upside_down(c, 0, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 0, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 14, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 0, 14, 2, 2, Color(0.44, 0.44, 0.44))
        draw_plate_sized_upside_down(c, 7, 7, 2, 2, Color(0.44, 0.44, 0.44))
        return
    

def block_center_xy(bx, by, xOffset, yOffset):
    return get_block_xy(bx + xOffset + 0.5, by + yOffset + 0.5)

def draw_plate_sized(c, blockX, blockY, width, height, color):
    PLATE_Z = PLATE_HEIGHT * 0.33

    top_color = color
    right_color = Color(color.red * 0.85, color.green * 0.85, color.blue * 0.85)
    front_color = Color(color.red * 0.70, color.green * 0.70, color.blue * 0.70)

    # --- Top corners ---
    p0 = get_block_xy(blockX, blockY)                   # front-left
    p1 = get_block_xy(blockX + width, blockY)           # front-right
    p2 = get_block_xy(blockX + width, blockY + height)  # back-right
    p3 = get_block_xy(blockX, blockY + height)          # back-left

    # --- Top ---
    draw_polygon(c, [p0, p1, p2, p3], top_color)

    # --- FRONT FACE (correct: +X edge) ---
    front = [
        p0,
        p1,
        (p1[0], p1[1] - PLATE_Z),
        (p0[0], p0[1] - PLATE_Z),
    ]
    draw_polygon(c, front, front_color)

    # --- RIGHT FACE (diagonal depth) ---
    right = [
        p1,
        p2,
        (p2[0], p2[1] - PLATE_Z),
        (p1[0], p1[1] - PLATE_Z),
    ]
    draw_polygon(c, right, right_color)

    # --- Studs ---
    c.setFillColor(top_color)
    for sy in range(height):
        for sx in range(width):
            sx_iso, sy_iso = get_block_xy(blockX + sx, blockY + sy)

            cx = sx_iso + PLATE_HALF_WIDTH
            cy = sy_iso + STUD_OFFSET_Y

            draw_stud_with_neck(
                c,
                cx,
                cy,
                top_color,
                front_color,
                right_color
            )

STUD_NECK_HEIGHT = PLATE_HEIGHT * 0.3
STUD_NECK_WIDTH  = STUD_WIDTH * 0.75
STUD_NECK_OFFSET = (STUD_WIDTH - STUD_NECK_WIDTH) / 2
STUD_NECK_RISE = STUD_HEIGHT * 0.35   # how high into the stud the neck starts


def draw_stud_with_neck(c, cx, cy, top_color, front_color, right_color):
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
        (neck_left,  neck_top),
        (neck_right, neck_top),
        (neck_right, neck_bot),
        (neck_left,  neck_bot),
    ]
    draw_polygon(c, front, front_color)
    c.setFillColor(top_color)
    c.ellipse(
        left,
        cy,
        right,
        cy + STUD_HEIGHT,
        fill=1
    )


def draw_plate_sized_upside_down(c, blockX, blockY, width, height, color):
    PLATE_Z = PLATE_HEIGHT * 0.33

    top_color = color
    right_color = Color(color.red * 0.85, color.green * 0.85, color.blue * 0.85)
    front_color = Color(color.red * 0.70, color.green * 0.70, color.blue * 0.70)

    blockX = blockX + .15
    blockY = blockY - .15
    # --- Top corners ---
    p0 = get_block_xy(blockX, blockY)                   # front-left
    p1 = get_block_xy(blockX + width, blockY)           # front-right
    p2 = get_block_xy(blockX + width, blockY + height)  # back-right
    p3 = get_block_xy(blockX, blockY + height)          # back-left

    # --- Top ---
    draw_polygon(c, [p0, p1, p2, p3], top_color)

    # --- FRONT FACE (correct: +X edge) ---
    front = [
        p0,
        p1,
        (p1[0], p1[1] - PLATE_Z),
        (p0[0], p0[1] - PLATE_Z),
    ]
    draw_polygon(c, front, front_color)

    # --- RIGHT FACE (diagonal depth) ---
    right = [
        p1,
        p2,
        (p2[0], p2[1] - PLATE_Z),
        (p1[0], p1[1] - PLATE_Z),
    ]
    draw_polygon(c, right, right_color)

    # =====================
    # HOLES (stud-sized)
    # =====================

    dx = 0.1
    dy = -0.1
    
    c.setFillColor(Color(front_color.red * 0.50, front_color.green * 0.50, front_color.blue * 0.50))

    for sy in range(height):
        for sx in range(width):
            sx_hole = blockX + sx - dx
            sy_hole = blockY + sy - dy

            sx_iso, sy_iso = get_block_xy(sx_hole, sy_hole)

            stud_left  = sx_iso + STUD_OFFSET_X
            stud_right = stud_left + STUD_WIDTH

            c.ellipse(
                stud_left,
                sy_iso + STUD_OFFSET_Y - PLATE_Z,
                stud_right,
                sy_iso + STUD_OFFSET_Y + STUD_HEIGHT - PLATE_Z,
                fill=1,
                stroke=0
            )

#function for gnerating instructions for baseplate setup and returns step after incrementing parameter for each step
def generate_baseplate_setup(step, case):
    #TODO: use pillow images to allow building on step by step
    #draw empty plate to help user see next step requirements
    canv = canvas.Canvas(get_output_path(step))
    step = step + 1
    draw_baseplate_bottom(canv, 16, Color(0.2, 0.2, 0.2), -1) #-1 case is where the baseplate piece is by itself
    canv.save()
    #draw bottom of baseplate with current case
    canv = canvas.Canvas(get_output_path(step))
    step = step + 1
    draw_baseplate_bottom(canv, 16, Color(0.2, 0.2, 0.2), case)
    canv.save()
    #draw top of baseplate with current case
    canv = canvas.Canvas(get_output_path(step))
    step = step + 1
    draw_baseplate_top(canv, 16, Color(0.2, 0.2, 0.2), case)
    canv.save()
    print("drew baseplate instruction")
    return step


folder = Path("instructions")

if not folder.is_dir():
    raise FileNotFoundError(f"Expected folder does not exist: {folder}")
def get_output_path(step):
    return str(folder / f"{step}.pdf")


#pass in 16 colors to draw a column of plates on a specific column
def draw_plate_column(c, start_blockX, colors):
    blockY = 0
    for color in colors:
        draw_plate(c, start_blockX, blockY, color)
        blockY += 1
 
# -----------------------------
# Example usage
# -----------------------------
#c = canvas.Canvas("lego_plates.pdf")
#case 0: red and green connectors (bottom right corner only)
#case 1: red connectors no green (right most column)
#case 2: green connectors no red (bottom row)
#case 3: no connectors

#backplate test top
# draw_baseplate_top(c, 16, Color(0.2, 0.2, 0.2), 0)
# draw_plate(c, 0, 1, Color(1, 0.8, 0.2))
# draw_plate(c, 0, 0, Color(1, 0.2, 0.2))
# draw_plate(c, 1, 1, Color(0.2, 0.6, 1))
# draw_plate(c, 15, 0, Color(0.2, 0.8, .2))
# draw_plate(c, 0, 15, Color(0.2, 0.8, .2))
# draw_plate(c, 15, 15, Color(0.2, 0.8, .2))
# c.save()

# #backplate test bottom
# b = canvas.Canvas("backplateTest.pdf")
# draw_baseplate_bottom(b, 16, Color(0.2, 0.2, 0.2), 0)
# b.save()

# #instruction generation test
# generate_baseplate_setup(2, 0)
# print("finished visuals!")


#TODO: convert report canvases functions to pillow images supporting
from PIL import Image, ImageDraw

def get_file_name(step_num):
    return f"instructions/{step_num}.png"

def save_img_and_increment_step(img, step):
    saveName = get_file_name(step)
    img.save(saveName)
    print("Saved " + saveName)
    return step + 1

def get_img_and_draw(step, wantClear):
    if (step == 1 or wantClear): #if on first step or user wants clear img, then make empty IMG
        # create image with specified size and background color
        bg_color = (255, 255, 255, 255) # white background
        image_size = (612, 792)
        img = Image.new("RGBA", image_size, bg_color)
    else: #already has previous step that user wants to use so just open previous step
        fileName = get_file_name(step - 1) 
        img = Image.open(fileName).convert("RGBA")
    return img, ImageDraw.Draw(img) #return imagedraw to allow user to modify current step before saving



# ----------------------------
# Step 0: Setup step number
# ----------------------------
step = 1

# -----------------------------------------------
# Step 1: Create base image and add rectangle
# -----------------------------------------------
img, draw = get_img_and_draw(step, False) # get previous image (blank if no previous) and draw object to draw on it
draw.rectangle((50, 50, 150, 150), fill=(200, 50, 50, 255)) # Draw stuff for current step (can be arbritrary amount of draws)
step = save_img_and_increment_step(img, step) # Save current step

# -----------------------------------------
# Step 2: Reopen the image and add more
# -----------------------------------------
img, draw = get_img_and_draw(step, False) # get previous image (blank if no previous) and draw object to draw on it
draw.ellipse((200, 50, 300, 150), fill=(50, 150, 255, 255)) # Draw stuff for current step (can be arbritrary amount of draws)
step = save_img_and_increment_step(img, step) # Save current step

