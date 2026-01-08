from VisualMaker import generate_baseplate_setup, draw_plate_column, get_img_and_draw, save_img_and_increment_step
from PIL import Image
import numpy as np
#take in ord list from MosiacToOrder to get pieces and counts
#take in foreground and background RGBA images (ignore alpha channel in foreground because it allows background to show through)
#build background first then foreground on top

#function to generate instructions for creating the mosiac frame (stretch)

#function to generate instrucctions for a RGBA image mosiac (pixel-perfect)

def GenerateInstructions(fg_rgba, bg_rgba, orderList):
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

    H, W, C = fg.shape
    assert C == 4
    assert W == fg_w and H == fg_h
    assert W % 16 == 0 and H % 16 == 0

    #Split height into blockHeight chunks of 16 rows
    #Split width into blockWidth chunks of 16 columns
    #with four at end for rgba
    fg_blocks = (
        fg
        .reshape(blockHeight, 16, blockWidth, 16, 4)
        .transpose(2, 0, 3, 1, 4)
    )

    bg_blocks = (
        bg
        .reshape(blockHeight, 16, blockWidth, 16, 4)
        .transpose(2, 0, 3, 1, 4)
    )
    #(blockWidth, blockHeight, 16, 16, 4)


    #for each baseplate in the mosiac:
    for blockW in range(0, blockWidth):
        for blockH in reversed(range(0, blockHeight)):
            #layer 0: baseplate
            # grid of interlocking 16x16s to ensure solid foundation
            step = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step)
            #TODO: use copies of canvas from last baseplate setup instruction as starting point for 1x1 placement instructions
            #layer 1: background
            #here we loop over each row and column to get the color (should be contained in order list)
            #only place maximum of 16 pieces per instruction step to avoid overwhelming user
            #16x16x4 blocks
            fg_block = fg_blocks[blockW, blockH]
            #print(f"fg size {len(fg_block)} x {len(fg_block[0])}")
            for col in range(0, len(fg_block[0])):
                #loop over columns of 16x16 block (each column is 16 plates)
                #draw_plate_column()
                img, draw = get_img_and_draw(step, False) #false because we want to pick off where we left off
                # take the column and convert to a list of 3-element tuples (R,G,B)
                column_rgb = [tuple(fg_block[y, col, :3]) for y in range(16)]
                draw_plate_column(draw, col, column_rgb)
                step = save_img_and_increment_step(img, step) # Save current step


            #TODO: use copies of canvas from 1x1 background instruction as starting point for 1x1 foreground placement instructions
            #layer 2: foreground
            #same thing as background but ignore alpha channel to allow background to show through
            bg_block = bg_blocks[blockW, blockH]


    return 1

def GenerateBasePlateInstructions(blockRow, rowMax, blockCol, colMax, step):
    
    #case 0: red and green connectors 
    #case 1: red connectors no green (right most column)
    #case 2: green connectors no red (bottom row)
    #case 3: no connectors (bottom right corner only)
    case = 0
    if 0 == blockRow and colMax - 1 == blockCol:
        case = 3
    elif colMax - 1 == blockCol:
        case = 1
    elif 0 == blockRow:
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
