from PIL import Image, ImageDraw
import random
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




def get_file_name(step_num):
    return f"instructions/{step_num}.png"








def save_img_and_increment_step(img, step):
    saveName = get_file_name(step)
    img.save(saveName)
    #print("Saved " + saveName)
    return step + 1








def to_rgb(color):
    return tuple(int(c * 255) for c in color)








def to_pillow(x, y):
    return (x, 792 - y) #silly little me was using reportwhateveritwas canvas before and y is flipped








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








def block_center_xy(bx, by, xOffset, yOffset):
    return get_block_xy(bx + xOffset + 0.5, by + yOffset + 0.5)








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




    # -----------------
    # Big middle 3D square
    # -----------------




    middle = x + PLATE_WIDTH * 6
    top_hh = PLATE_HALF_WIDTH * 2
    depth = PLATE_HEIGHT * 0.8  # slightly thinner than a plate




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
        xSpot = x + 100
        ySpot = y + 1
        greenPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(greenPin, green_color, outline=(10,10,10))




        xSpot = x + 235
        ySpot = y + 69
        greenPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(greenPin, green_color, outline=(10,10,10))
        # draw back of red connectors
        xSpot = x + 245
        ySpot = y + 69
        redPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(redPin, red_color, outline=(10,10,10))
        xSpot = x + 380
        ySpot = y + 1
        redPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(redPin, red_color, outline=(10,10,10))
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
        xSpot = x + 245
        ySpot = y + 69
        redPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(redPin, red_color, outline=(10,10,10))
        xSpot = x + 380
        ySpot = y + 1
        redPin = [
            (xSpot + 3,           ySpot + 9),            # top-left
            (xSpot + 30,      ySpot + 25),       # top-right
            (xSpot + 22, ySpot + 29),  # bottom-right
            (xSpot + -5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(redPin, red_color, outline=(10,10,10))
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
        xSpot = x + 100
        ySpot = y + 1
        greenPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(greenPin, green_color, outline=(10,10,10))
        xSpot = x + 235
        ySpot = y + 69
        greenPin = [
            (xSpot - 3,           ySpot + 9),            # top-left
            (xSpot - 30,      ySpot + 25),       # top-right
            (xSpot - 22, ySpot + 29),  # bottom-right
            (xSpot + 5,      ySpot + 13),       # bottom-left
        ]
        draw.polygon(greenPin, green_color, outline=(10,10,10))
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
        draw_plate_sized(draw, 16.5, 4.5, 2, 4, (.647, .792, .09))   # 2x4
        draw_plate_sized(draw, 7.5, -2.5, 4, 2, (0.8, 0.1, 0.1))   # 4x2      
    if (case == 1):
        #red connectors no green
        draw_plate_sized(draw, 7.5, -2.5, 4, 2, (0.8, 0.1, 0.1))   # 4x2
    if (case == 2):
        #green connectors no red
        draw_plate_sized(draw, 16.5, 4, 2, 4, (.647, .792, .09))   # 2x4
    if (case == 3):
        #no connectors (already handled)
        pass








    #2x2 grey plates on bottom of plate
    draw_plate_sized(draw, 1.5, -1.5, 2, 2, (0.44, 0.44, 0.44))
    draw_plate_sized(draw, 15.5, -1.5, 2, 2, (0.44, 0.44, 0.44))
    draw_plate_sized(draw, 15.5, 12.5, 2, 2, (0.44, 0.44, 0.44))
   
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








def draw_plate_sized(draw, blockX, blockY, width, height, color):
    PLATE_Z = PLATE_HEIGHT * 0.33




    top_color = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))




    # --- Top corners ---
    p0 = get_block_xy(blockX, blockY)                   # front-left
    p1 = get_block_xy(blockX + width, blockY)           # front-right
    p2 = get_block_xy(blockX + width, blockY + height)  # back-right
    p3 = get_block_xy(blockX, blockY + height)          # back-left




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
    for sy in range(height):
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








STUD_NECK_HEIGHT = PLATE_HEIGHT * 0.3
STUD_NECK_WIDTH  = STUD_WIDTH * 0.75
STUD_NECK_OFFSET = (STUD_WIDTH - STUD_NECK_WIDTH) / 2
STUD_NECK_RISE = STUD_HEIGHT * 0.35   # how high into the stud the neck starts




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


    #TODO: create updated stud and neck sizing
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






#function for gnerating instructions for baseplate setup and returns step after incrementing parameter for each step
def generate_baseplate_setup(step, case):
    #draw empty plate to help user see next step requirements
    img, draw = get_img_and_draw(step, True) # get blank image to start instructions
    draw_baseplate_bottom(draw, 16, (0.2, 0.2, 0.2), -1) #-1 case is where the baseplate piece is by itself
    step = save_img_and_increment_step(img, step) # Save current step
    #draw bottom of baseplate with current case
    img, draw = get_img_and_draw(step, False) # get previous image (blank if no previous) and draw object to draw on it
    draw_baseplate_bottom(draw, 16, (0.2, 0.2, 0.2), case)
    step = save_img_and_increment_step(img, step) # Save current step
    #draw top of baseplate with current case
    img, draw = get_img_and_draw(step, True) # use blank image to start top instructions on
    draw_baseplate_top(draw, 16, (0.2, 0.2, 0.2), case)
    step = save_img_and_increment_step(img, step) # Save current step
    print("drew baseplate instruction")
    return step




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
        print("need 16 color indexes")
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




def draw_corner_plate(draw, baseX, baseY, baseZ, color, highlight=False):
    # Convert base stud to pixel origin
    x0, y0 = get_block_xy(baseX, baseY)


    # Shading
    top_color   = to_rgb(color)
    right_color = to_rgb((color[0] * 0.85, color[1] * 0.85, color[2] * 0.85))
    front_color = to_rgb((color[0] * 0.70, color[1] * 0.70, color[2] * 0.70))


    half_width = 8
    full_width = 16
    plate_height = 2


    # -----------------------------
    # TOP FACE (4x4 with 2x2 missing bottom-left)
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


    # -----------------------------
    # LEFT FACE (vertical wall on left side)
    # This is the right wall of the 8x8 plate.
    # -----------------------------
    left = [
        iso(0, half_width - plate_height, x0, y0),   # drop plate height
        iso(half_width, half_width - plate_height, x0, y0),
        iso(half_width, half_width, x0, y0),
        iso(0, half_width, x0, y0),
    ]
    draw.polygon(left, front_color, outline=(10,10,10))


    # -----------------------------
    # RIGHT FACE (vertical wall on right side)
    # This is the front wall of the 4x8 section.
    # -----------------------------
    right = [
        iso(half_width, 0, x0, y0),
        iso(full_width, 0, x0, y0),
        iso(full_width, 0 - plate_height, x0, y0),
        iso(half_width, 0 - plate_height, x0, y0)
    ]
    draw.polygon(right, front_color, outline=(10,10,10))


    # --- Studs (only where plate exists) ---
    for dx in range(4):
        for dy in range(4):
            if dx < 2 and dy < 2:
                continue  # missing quadrant
            xOffset = dx * PLATE_WIDTH * 2
            yOffset = dy * PLATE_HEIGHT * 2
            draw_stud(draw, x0 + xOffset, y0 + yOffset, baseZ, color)


    # --- Optional highlight ---
    if highlight:
        draw.line([top[1], top[2], top[3], left[0], left[1], right[3], right[2], right[1]], fill=(255,255,0), width=2)








def draw_ortho_plate(draw, baseX, baseY, isAxle, width, depth, height, color, highlight=False):
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




    # --- Studs (only where plate exists) ---
    for dx in range(width):
        for dy in range(depth):
            xOffset = dx * PLATE_WIDTH * 2
            yOffset = dy * PLATE_HEIGHT * 2
            draw_stud(draw, x0 + xOffset, y0 + yOffset, 0, color)


    # --- Optional highlight ---
    if highlight:
        draw.line([top[2], front[0], front[1], top[1], top[2]], fill=(255,255,0), width=2)








#function for drawing frame around mosiac, saving each step until frame is complete
def draw_frame_for_mosiac(width, height, step):
    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)
    black = (.3, .3, .3)
   
    #draw corner plate
    img, draw = get_img_and_draw(step, True) #want clean state for this
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)


    # draw stuff (highlight on draw2 and no highlight on draw)
    draw_corner_plate(draw, 4, 4, 0, black, False)
    draw_corner_plate(draw2, 4, 4, 0, black, True)
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


    #draw corner brick
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw_corner_brick(draw, 1, 15, 0, black, False)
    draw_corner_brick(draw2, 1, 15, 0, black, True)
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


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
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


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
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


    #8x1 brick: 1
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw_ortho_plate(draw, 0, 7, False, 8, 1, 3, black, False)
    draw_ortho_plate(draw2, 0, 7, False, 8, 1, 3, black, True)
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


    #2x1 axle bricks: 2
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    #left axle brick
    draw_ortho_plate(draw, -4, 3, True, 2, 1, 3, black, False)
    draw_ortho_plate(draw2, -4, 3, True, 2, 1, 3, black, True)
    #right axle brick
    draw_ortho_plate(draw, 16, 23, True, 2, 1, 3, black, False)
    draw_ortho_plate(draw2, 16, 23, True, 2, 1, 3, black, True)
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


    #each block that is connected to another block should have a 4x1 brick and 6x2 plate
    #6x2 plate
    img, draw = get_img_and_draw(step, True) #get clean slate for this step
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw_ortho_plate(draw, 4, 0, False, 6, 2, 1, black, False)
    draw_ortho_plate(draw2, 4, 0, False, 6, 2, 1, black, True)
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


    #4x1 brick
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    draw_ortho_plate(draw, 1, 7, False, 4, 1, 3, black, False)
    draw_ortho_plate(draw2, 1, 7, False, 4, 1, 3, black, True)
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)


    #TODO: draw axle pins going into axle blocks


    #display plate and brick layer (those layers should be complete now)
    #TODO: add the 16x1 blocks on each exposed side


    #TODO: add top layer of thin corner plates and flat 4x1 plates


    return step



def draw_finished_view(composite, step):
    pass


# step = generate_baseplate_setup(6, 0)
#step = 2


#step = draw_frame_for_mosiac(64, 48, step)
#print("finished")




# #img never has highlighting and to_reuse will be the same as img except the most recent step is highlighted
# img, draw = get_img_and_draw(step, False) #use previous step to add onto for this step
# to_reuse = img.copy()
# draw2 = ImageDraw.Draw(to_reuse)
# test_array = [(random.random(), random.random(), random.random(), 255) for _ in range(16)]
# #draw twice, once for highlight and once regularly, using the regular one for the next step's start and saving highlighted one as current step
# #draw highlight on the step on reuse
# #reuse gets set to image each step. image is the previous without highlighting
# draw_plate_column(draw, 0, 0, test_array, False) #draw step without highlight
# draw_plate_column(draw2, 0, 0, test_array, True) #draw highlight
# step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)








# to_reuse = img.copy()
# draw2 = ImageDraw.Draw(to_reuse)
# test_array = [(random.random(), random.random(), random.random(), 255) for _ in range(16)]
# draw_plate_column(draw, 0, 1, test_array, False)
# draw_plate_column(draw2, 0, 1, test_array, True)
# step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)








# to_reuse = img.copy()
# draw2 = ImageDraw.Draw(to_reuse)
# test_array = [(random.random(), random.random(), random.random(), 255) for _ in range(16)]
# draw_plate_column(draw, 4, 0, test_array, False)
# draw_plate_column(draw2, 4, 0, test_array, True)
# step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)