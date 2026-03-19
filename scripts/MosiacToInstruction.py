from .VisualMaker import draw_final_view, generate_baseplate_setup, draw_plate_column, get_img_and_draw, save_img_and_increment_step, draw_frame_instructions, draw_grid_setup_instruction
from PIL import Image, ImageDraw
import numpy as np
import shutil
from pathlib import Path
from .Util import GetOutputPathDir, log_info, log_debug, log_error

def empty_instructions_folder():
    folder = Path(f"{GetOutputPathDir()}/Instructions")
    shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)

def count_colors(img_rgba):
    arr = np.asarray(img_rgba, dtype=np.uint8)
    assert arr.shape[2] == 4  # RGBA
    rgb = arr[:, :, :3].reshape(-1, 3)
    return len(np.unique(rgb, axis=0))

#function to generate instructions for a RGBA image mosiac (pixel-perfect)
def GenerateInstructions(fg_rgba, bg_rgba, composite, want_frame, output_dir, progress_callback=None):
    #progress is 35 when entering this function, we will get to 99 by the end of this function
    def report(pct):
        if progress_callback:
            progress_callback(pct)
    assert isinstance(bg_rgba, Image.Image)
    assert bg_rgba.mode == "RGBA"
    #set up by clearing previous instructions and starting from step 1
    if output_dir is None: #only clear local folder if output_dir not provided
        empty_instructions_folder()
    step = 1
    bg_w, bg_h = bg_rgba.size
    blockWidth = (int)(bg_w / 16)
    blockHeight = (int)(bg_h / 16)
    #divide pixel width and height by 16 (length of backplate block) to determine row and column max
    log_debug(f"creating instructions for width {blockWidth} and height {blockHeight}")
    #convert fg and bg rgba into arrays for each block [width][height][16][16]
    if not fg_rgba is None:
        fg = np.asarray(fg_rgba, dtype=np.uint8)
    bg = np.asarray(bg_rgba, dtype=np.uint8)

    if not fg_rgba is None:
        fg = fg.astype(np.float32) / 255.0
    bg = bg.astype(np.float32) / 255.0


    # Extract RGB only, ignore alpha
    if not fg_rgba is None:
        fg_colors = set(map(tuple, fg[:, :, :3].reshape(-1, 3)))
        log_debug(f"FG unique RGB colors ({len(fg_colors)}):")
        log_info(fg_colors)

    bg_colors = set(map(tuple, bg[:, :, :3].reshape(-1, 3)))

    log_debug(f"\nBG unique RGB colors ({len(bg_colors)}):")
    log_info(bg_colors)

    H, W, C = bg.shape
    log_debug(f"height: {H}, width: {W}")
    assert C == 4
    assert W == bg_w and H == bg_h
    assert W % 16 == 0 and H % 16 == 0

    bg_color_count = count_colors(bg_rgba)
    log_info(f"BG unique RGB colors: {bg_color_count}")

    if not fg_rgba is None:
        fg_color_count = count_colors(fg_rgba)
        log_info(f"FG unique RGB colors: {fg_color_count}")

    report(40)
    total_blocks = blockWidth * blockHeight
    factor = 59 / total_blocks  # 69 progress points allocated for block processing
    block_count = 0
    #for each baseplate in the mosiac:
    for blockW in range(0, blockWidth):
        for blockH in range(0, blockHeight):
            #layer 0: baseplate
            # grid of interlocking 16x16s to ensure solid foundation
            step = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step, output_dir)
            #layer 1: background
            #here we loop over each row and column to get the color (should be contained in order list)
            #only place maximum of 16 pieces per instruction step to avoid overwhelming user
            #16x16x4 blocks
            # Slice out a 16x16 block
            y0, y1 = blockH*16, (blockH+1)*16
            x0, x1 = blockW*16, (blockW+1)*16
            bg_block = bg[y0:y1, x0:x1, :]   # shape: (16, 16, 4)
            img, draw = get_img_and_draw(step, False, output_dir) #false because we want to pick off where we left off
            for col in range(0, len(bg_block[0])):
                #loop over columns of 16x16 block (each column is 16 plates)
                to_reuse = img.copy()
                draw2 = ImageDraw.Draw(to_reuse)
                # take the column and convert to a list of 3-element tuples (R,G,B)
                column_rgb = [tuple(bg_block[15 - y, col]) for y in range(16)]
                draw_plate_column(draw, col, 0, column_rgb, False) #zero height with highlight
                draw_plate_column(draw2, col, 0, column_rgb, True) #zero height with highlight
                step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step

            
            #layer 2: foreground
            #same thing as background but ignore alpha channel to allow background to show through
            if not fg_rgba is None:
                fg_block = fg[y0:y1, x0:x1, :]   # shape: (16, 16, 4)
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
                    step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step

            block_count += 1
            report(40 + (int)(block_count * factor))
    
    #add frame instruction steps
    step = draw_grid_setup_instruction(step, output_dir)
    if want_frame:
        #show final view with frame (include frame building instructions)
        step = draw_frame_instructions(bg_rgba.width, bg_rgba.height, step, output_dir)
        step = draw_final_view(step, composite, True, output_dir)
    else:
        #show final view without frame
        step = draw_final_view(step, composite, False, output_dir)


#helper test function
def sample_column(img_np, blockW, blockH, col):
    return [
        tuple(img_np[blockH*16 + row, blockW*16 + col][:3])
        for row in range(16)
    ]


def GenerateBasePlateInstructions(blockRow, rowMax, blockCol, colMax, step, output_dir=None):
    
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

    #log_debug(f"creating baseplate instructions for width {blockCol} and height {blockRow} for max width {colMax} and max height {rowMax} case {case}")
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
    return generate_baseplate_setup(step, case, output_dir)

def block_column_to_rgb_tuples(block, col_idx):
    return [tuple(block[y, col_idx, :3]) for y in range(block.shape[0])]
