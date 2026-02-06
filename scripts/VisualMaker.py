from PIL import Image, ImageDraw, ImageFont
import random
from Util import GetOutputPathDir
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

def get_file_name(step_num):
    return f"{GetOutputPathDir()}/Instructions/{step_num}.png"


def save_img_and_increment_step(img, step):
    #draw step number on bottom middle
    width, height = img.size
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 32)
    
    #draw step number in center bottom
    x_middle = width / 2
    x_offset = -len(str(step)) * 5
    offset = 50
    draw.text((x_middle + x_offset, height - offset), str(step), fill="black", font=font)
    #save and increment/return step
    saveName = get_file_name(step)
    img.save(saveName)
    return (step + 1)


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


def draw_grid_setup_instruction(step):
    img, draw = get_img_and_draw(step, True)
    width, height = img.size
    draw = ImageDraw.Draw(img)

    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)
    
    if blockHeight > 1:
        #instruct user to connect columns via the red connectors going down
        font = ImageFont.truetype("arial.ttf", 20)
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
        font = ImageFont.truetype("arial.ttf", 20)
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

    step = save_img_and_increment_step(img, step) # Save current step (with current step pieces highlighted)
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


def draw_frame_setup_instruction(width, height, step):
    img, draw = get_img_and_draw(step, True)
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)
    im_w, im_h = img.size
    middle_x_of_image = (int)(im_w / 2)
    middle_y_of_image = (int)(im_h / 2)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 28)
    small_font = ImageFont.truetype("arial.ttf", 22)
    
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
    step = save_img_and_increment_step(to_reuse, step) # Save current step

    
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
    step = save_img_and_increment_step(to_reuse, step) # Save current step
    
    #instruct user to place 4x1 on baseplate seams if numOfConnectors is not 0
    numOfConnectors =  perimeter - 4
    if numOfConnectors != 0:
        to_reuse = img.copy()
        draw2 = ImageDraw.Draw(to_reuse)
        for seam in seams:
            draw2.ellipse(seam, fill=blue, outline=(10,10,10))

        draw2.text((30, 100), "Put short frame parts below baseplate seams", fill="black", font=font)
        step = save_img_and_increment_step(to_reuse, step) # Save current step

    #draw axle pins going into axle blocks
    img, draw = get_img_and_draw(step, True)
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)

    draw2.text((30, 100), "Push axle pins into holes all the way", fill="black", font=font)
    draw2.text((30, middle_y_of_image), "The frame and mosaic should be connected", fill="black", font=font)
    draw_ortho_plate(draw2, -12, 16, True, 2, 1, 3, (.3, .3, .3), False)
    #draw axle pin below the axle pin text
    axle_pin = Image.open("axle_pin.jpg").convert("RGBA") #TODO: update file path if needed
    axle_pin = axle_pin.resize((100, 100))
    to_reuse.paste(axle_pin, (300, 150), axle_pin)
    step = save_img_and_increment_step(to_reuse, step) # Save current step

    #display plate and brick layer (those layers should be complete now)
    draw3.text((30, 100), "Place 16x1 bricks here to complete the layer", fill="black", font=font)
    draw_plate_sized(draw3, -3, 10, 1, 16, 4, (.3, .3, .3))
    step = save_img_and_increment_step(baseplate_middles, step) # Save current step

    #add top layer of thin corner plates and flat 4x1 plates
    img, draw = get_img_and_draw(step, True)
    draw.text((30, 100), "Place corner plates on corners", fill="black", font=font)
    draw_corner_plate(draw, 0, 8, 0, 3, 1, (.3, .3, .3), 2, 2, False) #3x3 corner
    draw_ortho_plate(draw, 16, -8, False, 4, 1, 1, (.3, .3, .3), False, True) 
    draw.text((30, middle_y_of_image), "Place all flat plates to fill the sides", fill="black", font=font)

    step = save_img_and_increment_step(img, step) # Save current step

    #step = save_img_and_increment_step(img, step) # Save current step (with current step pieces highlighted)
    return step


#function for drawing frame around mosiac, saving each step until frame is complete
def draw_frame_for_mosiac(width, height, step):
    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)
    black = (.3, .3, .3)
    font = ImageFont.truetype("arial.ttf", 60)
    perimeter = (blockWidth * 2) + (blockHeight * 2)
    
    #draw corner plate
    img, draw = get_img_and_draw(step, True) #want clean state for this
    to_reuse = img.copy()
    draw2 = ImageDraw.Draw(to_reuse)

    # draw stuff (highlight on draw2 and no highlight on draw)
    draw_corner_plate(draw, 4, 4, 0, 4, 2, black, 2, 2, False)
    draw_corner_plate(draw2, 4, 4, 0, 4, 2, black, 2, 2, True)
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
    draw2.text((50, 75), "4X", fill="black", font=font) #draw quantity number so users know how many pieces to make
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
    quantity = str(perimeter) + "X"
    draw2.text((50, 75), quantity, fill="black", font=font) #draw quantity number so users know how many pieces to make
    step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)

    #each block that is connected to another block should have a 4x1 brick and 6x2 plate
    numOfConnectors = perimeter - 4
    if numOfConnectors != 0:
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
        quantity = str(numOfConnectors) + "X"
        draw2.text((50, 75), quantity, fill="black", font=font) #draw quantity number so users know how many pieces to make
        step = save_img_and_increment_step(to_reuse, step) # Save current step (with current step pieces highlighted)

    #return step once all actions are taken
    return step

#high level function to create instructions for the grid and frame setup that calls medium level functions                                                                                                                                                                                           
def draw_frame_instructions(width, height, step):
    #shows the connection of grid cells into columns and column into grid (now handled externally)
    #step = draw_grid_setup_instruction(step)
    #shows steps for making the pieces of the frame
    step = draw_frame_for_mosiac(width, height, step)
    #show steps for putting together frame pieces
    step = draw_frame_setup_instruction(width, height, step)
    return step #return step for any future use


def draw_final_view(step, composite, want_frame):
    img, draw = get_img_and_draw(step, True)
    print("drawing final view...")
    black = to_rgb((.2, .2, .2))
    #aspect ratio of image
    composite_aspect_ratio = composite.height / composite.width
    #desired width of composite
    desired_comp_width = 400
    comp_h = int(round(desired_comp_width * (composite_aspect_ratio)))
    #scale such that the composite/frame always take up certain portion of canvas
    composite_resized = composite.resize((desired_comp_width, comp_h), Image.Resampling.LANCZOS)
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


    font = ImageFont.truetype("arial.ttf", 20)
    margin = 50
    placement = (margin, margin)
    text = "Admire your artwork (add frame hooks to back is desired)"
    draw.text(placement, text, fill="black", font=font)


    print("finished drawing final view!")
    step = save_img_and_increment_step(img, step)
    return step





# draw_frame_instructions(64, 48, 2)
# print("finished")


