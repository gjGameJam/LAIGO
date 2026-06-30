from .VisualMaker import draw_final_view, generate_baseplate_setup, draw_plate_column, save_img_and_increment_step, draw_frame_instructions, draw_grid_setup_instruction, draw_hook_assembly_instruction, draw_backhook_instruction, draw_step_piece_legend
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas as rl_canvas
import numpy as np
import re
from pathlib import Path
from typing import List
from .Util import log_info, log_debug
from .mosaic_types import STUDS_PER_BLOCK  # D-035: single source of truth for the 16-stud block

# D-013: the legacy CLI path (empty_instructions_folder + the
# `if output_dir is None:` branches that fell back to GetOutputPathDir()) has
# been removed. output_dir is now a required argument; the API worker is the
# only caller. Dropped dead imports (GetOutputPathDir, shutil, log_error) too.


def count_colors(img_rgba):
    arr = np.asarray(img_rgba, dtype=np.uint8)
    if arr.shape[2] != 4:  # D-023: explicit raise, not assert (survives python -O)
        raise ValueError(f"count_colors expects RGBA, got {arr.shape[2]} channels")
    # D-043: pack each RGB into one uint32 and run a 1-D np.unique instead of the
    # O(N log N) lexicographic sort np.unique(axis=0) does over ~400k pixel rows
    # (twice per job, only to log "BG/FG unique RGB colors: N"). 8 bits/channel is
    # a bijection, so the distinct count is identical.
    rgb = arr[:, :, :3].reshape(-1, 3).astype(np.uint32)
    keys = (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]
    return len(np.unique(keys))



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
    # D-023: explicit raises (not assert — assert vanishes under python -O).
    if not isinstance(bg_rgba, Image.Image):
        raise TypeError(f"bg_rgba must be PIL.Image.Image, got {type(bg_rgba)}")
    if bg_rgba.mode != "RGBA":
        raise ValueError(f"bg_rgba must be RGBA, got {bg_rgba.mode!r}")
    # D-013: output_dir is required (CLI fallback removed).
    if output_dir is None:
        raise ValueError("output_dir is required")
    step = 1
    bg_w, bg_h = bg_rgba.size
    blockWidth = bg_w // STUDS_PER_BLOCK
    blockHeight = bg_h // STUDS_PER_BLOCK
    #divide pixel width and height by the block edge (studs) to determine row and column max
    log_debug(f"creating instructions for width {blockWidth} and height {blockHeight}")
    #convert fg and bg rgba into arrays for each block [width][height][16][16]
    if not fg_rgba is None:
        fg = np.asarray(fg_rgba, dtype=np.uint8)
    bg = np.asarray(bg_rgba, dtype=np.uint8)

    # D-004/D-005: removed the set(map(tuple, ...)) materializations and their
    # log_info(<full set>) dumps. They recomputed the unique-color sets a second
    # way purely for log lines and flooded prod logs with RGB-tuple reprs. The
    # cheap count_colors(...) summaries below are the single source of this info.

    H, W, C = bg.shape
    log_debug(f"height: {H}, width: {W}")
    # D-023: explicit raises (not assert).
    if C != 4:
        raise ValueError(f"bg must have 4 channels (RGBA), got {C}")
    if W != bg_w or H != bg_h:
        raise ValueError(f"shape mismatch: numpy ({W},{H}) vs PIL ({bg_w},{bg_h})")
    if W % STUDS_PER_BLOCK or H % STUDS_PER_BLOCK:
        raise ValueError(f"mosaic dims must be divisible by {STUDS_PER_BLOCK}, got ({W},{H})")

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
            #layer 0: baseplate — returns canvas so we skip the disk read below
            step, img = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step, output_dir)
            draw = ImageDraw.Draw(img)
            #layer 1: background
            y0, y1 = blockH*STUDS_PER_BLOCK, (blockH+1)*STUDS_PER_BLOCK
            x0, x1 = blockW*STUDS_PER_BLOCK, (blockW+1)*STUDS_PER_BLOCK
            bg_block = bg[y0:y1, x0:x1, :]   # shape: (STUDS_PER_BLOCK, STUDS_PER_BLOCK, 4)
            for col in range(0, len(bg_block[0])):
                #loop over columns of the block (each column is STUDS_PER_BLOCK plates)
                to_reuse = img.copy()
                draw2 = ImageDraw.Draw(to_reuse)
                # take the column and convert to a list of 3-element tuples (R,G,B)
                column_rgb = [tuple(c / 255.0 for c in bg_block[STUDS_PER_BLOCK - 1 - y, col]) for y in range(STUDS_PER_BLOCK)]
                draw_plate_column(draw, col, 0, column_rgb, False) #zero height no highlight
                draw_plate_column(draw2, col, 0, column_rgb, True) #zero height with highlight
                # legend of the pieces placed this step (opaque studs only)
                studs = [c[:3] for c in column_rgb if c[3] != 0]
                draw_step_piece_legend(draw2, studs)
                step = save_img_and_increment_step(to_reuse, step, output_dir, copy=False)

            #layer 2: foreground
            if not fg_rgba is None:
                fg_block = fg[y0:y1, x0:x1, :]   # shape: (STUDS_PER_BLOCK, STUDS_PER_BLOCK, 4)
                for col in range(0, len(fg_block[0])):
                    to_reuse = img.copy()
                    draw2 = ImageDraw.Draw(to_reuse)
                    # take the column and convert to a list of 3-element tuples (R,G,B,A)
                    column_rgba = [tuple(c / 255.0 for c in fg_block[STUDS_PER_BLOCK - 1 - y, col]) for y in range(STUDS_PER_BLOCK)]
                    if all(pixel[3] == 0 for pixel in column_rgba):
                        # Entire column is transparent, skip
                        continue
                    draw_plate_column(draw, col, 1, column_rgba, False) #one height no highlight
                    draw_plate_column(draw2, col, 1, column_rgba, True) #one height with highlight
                    # legend of the pieces placed this step (opaque studs only)
                    studs = [c[:3] for c in column_rgba if c[3] != 0]
                    draw_step_piece_legend(draw2, studs)
                    step = save_img_and_increment_step(to_reuse, step, output_dir, copy=False)

            block_count += 1
            report(40 + (int)(block_count * factor))

    #add frame instruction steps
    step = draw_grid_setup_instruction(step, output_dir)
    if want_frame:
        #include frame building + assembly instructions before the back hooks
        step = draw_frame_instructions(bg_rgba.width, bg_rgba.height, step, output_dir)
    #hook hardware (every kit ships it regardless of frame): first sub-assemble each
    #hook by pushing its 2 black pins in, then attach the hook(s) to the back, then
    #the final "admire your artwork" view
    step = draw_hook_assembly_instruction(bg_rgba.width, bg_rgba.height, step, output_dir)
    step = draw_backhook_instruction(bg_rgba.width, bg_rgba.height, step, output_dir)
    step = draw_final_view(step, composite, want_frame, output_dir)

    # Save PDF
    instructions_dir = Path(output_dir) / "Instructions"
    pdf_path = instructions_dir / "instructions.pdf"

    # D-021: order is irrelevant here — this list only drives the post-PDF
    # deletion loop. The PDF itself is assembled in step order by images_to_pdf
    # via _get_ordered_pngs. The old underscore-keyed sort was dead (filenames
    # are "1.png".."N.png", no underscores -> every key collapsed to 0).
    png_files = list(instructions_dir.glob("*.png"))

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
        tuple(img_np[blockH*STUDS_PER_BLOCK + row, blockW*STUDS_PER_BLOCK + col][:3])
        for row in range(STUDS_PER_BLOCK)
    ]


def GenerateBasePlateInstructions(blockRow, rowMax, blockCol, colMax, step, output_dir=None):
    case = 0
    if rowMax - 1 == blockRow and colMax - 1 == blockCol:
        case = 3
    elif colMax - 1 == blockCol:
        case = 1
    elif rowMax - 1 == blockRow:
        case = 2
    minimap = (colMax, rowMax, blockCol, blockRow)  # (n_w, n_h, cur_w, cur_h)
    return generate_baseplate_setup(step, case, output_dir, minimap=minimap)


def block_column_to_rgb_tuples(block, col_idx):
    return [tuple(block[y, col_idx, :3]) for y in range(block.shape[0])]