from VisualMaker import generate_baseplate_setup, draw_plate_column, get_img_and_draw, save_img_and_increment_step, draw_frame_instructions
from PIL import Image, ImageDraw
import numpy as np
#take in ord list from MosiacToOrder to get pieces and counts
#take in foreground and background RGBA images (ignore alpha channel in foreground because it allows background to show through)
#build background first then foreground on top

#function to generate instructions for creating the mosiac frame (stretch)


def count_colors(img_rgba):
    arr = np.asarray(img_rgba, dtype=np.uint8)
    assert arr.shape[2] == 4  # RGBA
    rgb = arr[:, :, :3].reshape(-1, 3)
    return len(np.unique(rgb, axis=0))

#function to generate instrucctions for a RGBA image mosiac (pixel-perfect)

def GenerateInstructions(fg_rgba, bg_rgba, composite):
    assert isinstance(fg_rgba, Image.Image)
    assert fg_rgba.mode == "RGBA"
    step = 1
    fg_w, fg_h = fg_rgba.size
    blockWidth = (int)(fg_w / 16)
    blockHeight = (int)(fg_h / 16)
    #divide pixel width and height by 16 (length of backplate block) to determine row and column max
    print("creating instructions for width " + str(blockWidth) + " and height " + str(blockHeight))
    #convert fg and bg rgba into arrays for each block [width][height][16][16]

    fg = np.asarray(fg_rgba, dtype=np.uint8)
    bg = np.asarray(bg_rgba, dtype=np.uint8)

    fg = fg.astype(np.float32) / 255.0
    bg = bg.astype(np.float32) / 255.0


    # Extract RGB only, ignore alpha
    fg_colors = set(map(tuple, fg[:, :, :3].reshape(-1, 3)))
    bg_colors = set(map(tuple, bg[:, :, :3].reshape(-1, 3)))

    print(f"FG unique RGB colors ({len(fg_colors)}):")
    print(fg_colors)

    print(f"\nBG unique RGB colors ({len(bg_colors)}):")
    print(bg_colors)

    H, W, C = fg.shape
    assert C == 4
    assert W == fg_w and H == fg_h
    assert W % 16 == 0 and H % 16 == 0

    fg_color_count = count_colors(fg_rgba)
    bg_color_count = count_colors(bg_rgba)

    print("FG unique RGB colors:", fg_color_count)
    print("BG unique RGB colors:", bg_color_count)

    #Split height into blockHeight chunks of 16 rows
    #Split width into blockWidth chunks of 16 columns
    #with four at end for rgba
    # reshape with (blockH, blockW, 16 rows, 16 cols, 4)
    # bg_blocks = bg.reshape(blockHeight, 16, blockWidth, 16, 4).transpose(0, 2, 1, 3, 4)
    # fg_blocks = fg.reshape(blockHeight, 16, blockWidth, 16, 4).transpose(0, 2, 1, 3, 4)

    #(blockWidth, blockHeight, 16, 16, 4)


    #for each baseplate in the mosiac:
    for blockW in range(0, blockWidth):
        for blockH in range(0, blockHeight):
            #layer 0: baseplate
            # grid of interlocking 16x16s to ensure solid foundation
            step = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step)
            #layer 1: background
            #here we loop over each row and column to get the color (should be contained in order list)
            #only place maximum of 16 pieces per instruction step to avoid overwhelming user
            #16x16x4 blocks
            # Slice out a 16x16 block
            y0, y1 = blockH*16, (blockH+1)*16
            x0, x1 = blockW*16, (blockW+1)*16
            bg_block = bg[y0:y1, x0:x1, :]   # shape: (16, 16, 4)
            fg_block = fg[y0:y1, x0:x1, :]   # shape: (16, 16, 4)
            img, draw = get_img_and_draw(step, False) #false because we want to pick off where we left off
            #print(f"bg size {len(bg_block)} x {len(bg_block[0])}")
            for col in range(0, len(bg_block[0])):
                #loop over columns of 16x16 block (each column is 16 plates)
                to_reuse = img.copy()
                draw2 = ImageDraw.Draw(to_reuse)
                # take the column and convert to a list of 3-element tuples (R,G,B)
                column_rgb = [tuple(bg_block[15 - y, col]) for y in range(16)]
                #print("RAW column colors:", len(set(column_rgb)), set(column_rgb))
                #print(set(column_rgb))
                draw_plate_column(draw, col, 0, column_rgb, False) #zero height
                draw_plate_column(draw2, col, 0, column_rgb, True) #zero height
                step = save_img_and_increment_step(to_reuse, step) # Save current step

            
            #layer 2: foreground
            #same thing as background but ignore alpha channel to allow background to show through
            # #print(f"fg size {len(fg_block)} x {len(fg_block[0])}")
            for col in range(0, len(fg_block[0])):
                #img, draw = get_img_and_draw(step, False) #false because we want to pick off where we left off
                to_reuse = img.copy()
                draw2 = ImageDraw.Draw(to_reuse)
                # take the column and convert to a list of 3-element tuples (R,G,B,A)
                column_rgba = [tuple(fg_block[15 - y, col]) for y in range(16)]
                if all(pixel[3] == 0 for pixel in column_rgba):
                    # Entire column is transparent, skip
                    continue
                draw_plate_column(draw, col, 1, column_rgba, False) #one height
                draw_plate_column(draw2, col, 1, column_rgba, True) #one height
                step = save_img_and_increment_step(to_reuse, step) # Save current step
    
    #add frame instruction steps
    draw_frame_instructions(fg_rgba.width, fg_rgba.height, step)

    #add frame around entire mosiac view
    #img, draw = get_img_and_draw(step, True) #True because we want a fresh image for frame
    #draw_finished_view(composite, draw)
    #save_img_and_increment_step(img, step) # Save current step


def sample_column(img_np, blockW, blockH, col):
    return [
        tuple(img_np[blockH*16 + row, blockW*16 + col][:3])
        for row in range(16)
    ]


def GenerateBasePlateInstructions(blockRow, rowMax, blockCol, colMax, step):
    
    #case 0: red and green connectors 
    #case 1: red connectors no green (right most column)
    #case 2: green connectors no red (bottom row)
    #case 3: no connectors (bottom right corner only)
    case = 0
    if rowMax - 1 == blockRow and colMax - 1 == blockCol:
        case = 3
    elif colMax - 1 == blockCol:
        case = 1
    elif rowMax - 1 == blockRow:
        case = 2

    print("creating baseplate instructions for width " + str(blockCol) + " and height " + str(blockRow) + " for max width " + str(colMax) + " and max height " + str(rowMax) + " case " + str(case))
    #first step is to get 16x16 baseplate out
    #add green connectors on right side and red connectors along bottom side
    #add green plates on right side and red plates along bottom side
    #add 2x2 plates on middle and corners for extra stability
    #add nail hooks on top edges (or direct middle) for hanging
    #add nail hook connectors to nail hooks to allow connection to frame
    #3 cases: 
    #case 1: in middle of mosiac(all connections)
    #case 2: top/left side of mosiac (no top/left connections)
    #case 3: bottom/right mosiac (no bottom/right connections)
    return generate_baseplate_setup(step, case)

def block_column_to_rgb_tuples(block, col_idx):
    return [tuple(block[y, col_idx, :3]) for y in range(block.shape[0])]
