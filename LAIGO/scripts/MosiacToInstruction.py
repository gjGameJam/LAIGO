from .VisualMaker import draw_final_view, generate_baseplate_setup, draw_plate_column, get_img_and_draw, save_img_and_increment_step, draw_frame_instructions, draw_grid_setup_instruction
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas as rl_canvas
import numpy as np
import shutil
import re
from pathlib import Path
from typing import List
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


def erase_step_number(img):
    """White out the region where save_img_and_increment_step burns the step number.
    save_img_and_increment_step draws at (width/2 + x_offset, height - 50) with font size 32.
    We cover a generous rectangle around that area to ensure the old number is fully cleared."""
    draw = ImageDraw.Draw(img)
    width, height = img.size
    draw.rectangle(
        [(width // 2 - 80, height - 70), (width // 2 + 80, height - 20)],
        fill=(255, 255, 255, 255)
    )


# -------------------------------------------------------
# PDF helpers
# -------------------------------------------------------

def _extract_step_num(path: Path) -> int:
    match = re.search(r'(\d+)', path.stem)
    if not match:
        raise ValueError(f"Invalid filename (no step number): {path.name}")
    return int(match.group(1).zfill(4))


def _get_ordered_pngs(input_dir: Path) -> List[Path]:
    files = [f for f in input_dir.iterdir() if f.suffix.lower() == ".png"]
    if not files:
        raise ValueError(f"No PNG files found in: {input_dir}")
    files.sort(key=_extract_step_num)
    return files


def _validate_sequence(files: List[Path]) -> None:
    steps = [_extract_step_num(f) for f in files]
    expected = list(range(min(steps), max(steps) + 1))
    if steps != expected:
        raise ValueError("Missing or duplicate step numbers detected")


def images_to_pdf(input_folder: str, output_pdf: str) -> None:
    input_dir = Path(input_folder)
    output_path = Path(output_pdf)
    if not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    files = _get_ordered_pngs(input_dir)
    if not files:
        raise ValueError("No valid images to write")
    _validate_sequence(files)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    c = rl_canvas.Canvas(str(output_path), pagesize=(612, 792))
    for path in files:
        c.drawImage(str(path), 0, 0, width=612, height=792)
        c.showPage()
    c.save()
    log_info(f"Created PDF with {len(files)} pages at: {output_path}")


# -------------------------------------------------------
# Main instruction generator
# -------------------------------------------------------

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
    factor = 55 / total_blocks  # 55 progress points allocated for block processing
    block_count = 0
    #for each baseplate in the mosiac:
    for blockW in range(0, blockWidth):
        for blockH in range(0, blockHeight):
            #layer 0: baseplate
            step = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step, output_dir)
            #layer 1: background
            y0, y1 = blockH*16, (blockH+1)*16
            x0, x1 = blockW*16, (blockW+1)*16
            bg_block = bg[y0:y1, x0:x1, :]   # shape: (16, 16, 4)
            img, draw = get_img_and_draw(step, False, output_dir) #false because we want to pick off where we left off
            for col in range(0, len(bg_block[0])):
                #loop over columns of 16x16 block (each column is 16 plates)
                to_reuse = img.copy()
                draw2 = ImageDraw.Draw(to_reuse)
                # take the column and convert to a list of 3-element tuples (R,G,B)
                column_rgb = [tuple(c / 255.0 for c in bg_block[15 - y, col]) for y in range(16)]
                draw_plate_column(draw, col, 0, column_rgb, False) #zero height no highlight
                draw_plate_column(draw2, col, 0, column_rgb, True) #zero height with highlight
                erase_step_number(to_reuse)  # clear burned-in number from previous step
                step = save_img_and_increment_step(to_reuse, step, output_dir) # Save current step

            #layer 2: foreground
            if not fg_rgba is None:
                fg_block = fg[y0:y1, x0:x1, :]   # shape: (16, 16, 4)
                for col in range(0, len(fg_block[0])):
                    to_reuse = img.copy()
                    draw2 = ImageDraw.Draw(to_reuse)
                    # take the column and convert to a list of 3-element tuples (R,G,B,A)
                    column_rgba = [tuple(c / 255.0 for c in fg_block[15 - y, col]) for y in range(16)]
                    if all(pixel[3] == 0 for pixel in column_rgba):
                        # Entire column is transparent, skip
                        continue
                    draw_plate_column(draw, col, 1, column_rgba, False) #one height no highlight
                    draw_plate_column(draw2, col, 1, column_rgba, True) #one height with highlight
                    erase_step_number(to_reuse)  # clear burned-in number from previous step
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

    # Save PDF
    instructions_dir = Path(output_dir) / "Instructions" if output_dir else Path(GetOutputPathDir()) / "Instructions"
    pdf_path = instructions_dir / "instructions.pdf"

    png_files = sorted(
        instructions_dir.glob("*.png"),
        key=lambda p: int(p.stem.split("_")[-1]) if "_" in p.stem else 0
    )

    if not png_files:
        raise RuntimeError("No instruction PNGs found — aborting PDF generation.")

    images_to_pdf(str(instructions_dir), str(pdf_path))

    report(99)

    # verify PDF was actually created and non-empty
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF generation failed — not deleting source images.")

    # delete after success
    for png_file in png_files:
        png_file.unlink()

#helper test function
def sample_column(img_np, blockW, blockH, col):
    return [
        tuple(img_np[blockH*16 + row, blockW*16 + col][:3])
        for row in range(16)
    ]


def GenerateBasePlateInstructions(blockRow, rowMax, blockCol, colMax, step, output_dir=None):
    case = 0
    if rowMax - 1 == blockRow and colMax - 1 == blockCol:
        case = 3
    elif colMax - 1 == blockCol:
        case = 1
    elif rowMax - 1 == blockRow:
        case = 2
    return generate_baseplate_setup(step, case, output_dir)


def block_column_to_rgb_tuples(block, col_idx):
    return [tuple(block[y, col_idx, :3]) for y in range(block.shape[0])]