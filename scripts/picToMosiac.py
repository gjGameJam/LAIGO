import os
from PIL import Image, ImageFilter
import numpy as np
from skimage import color
from pathlib import Path
import mediapipe as mp
import traceback
# D-019/D-020: the sys.path.append hack that let MosiacToOrder/Util use bare
# imports is gone — every intra-package import is now relative. Dead imports
# (sys, copy, enum.Enum) removed; MosaicType moved to the leaf mosaic_types module.
from .mosaic_types import MosaicType
from .MosiacToOrder import GenerateOrderList
from .MosiacToInstruction import GenerateInstructions
from .preview_builder import build_preview_payload, write_preview_atomic
from .Util import GetPaletteRGBArray, load_project_env
# Only load .env when running locally
if os.getenv("RENDER") is None:
    load_project_env()
from .Util import log_debug, log_info, log_error


#converts a list of RGB colors into a NumPy array of Lab colors
def rgb_list_to_lab(arr_rgb):
    arr = np.array(arr_rgb, dtype=np.uint8).reshape((-1,1,3))/255.0
    lab = color.rgb2lab(arr).reshape((-1,3))
    return lab


LEGO_PALETTE_RGB = GetPaletteRGBArray()
PALETTE_LAB = rgb_list_to_lab(LEGO_PALETTE_RGB)
PALETTE_LAB_RESHAPED = PALETTE_LAB.reshape((-1, 1, 3))
MAX_BLOCK_WIDTH = int(os.getenv("MAX_MOSAIC_BLOCK_WIDTH", 40))
MIN_BLOCK_WIDTH = 1
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK", 16))


#gets the index of the closest color to the pixel from the palette by visual distance
def nearest_palette_index_lab(pixel_lab):
    pixel_lab_reshaped = pixel_lab.reshape((1, 1, 3))
    d = color.deltaE_ciede2000(PALETTE_LAB_RESHAPED, pixel_lab_reshaped)
    return int(np.argmin(d))


#optimizes the background to have a maximum number of k colors and returns array of indexes of colors
def simplify_background_lego(bg_idx, palette_lab, k=5, alpha_mask=None):
    log_info(f"simplifying background to top {k} lego colors...")
    if alpha_mask is not None:
        valid_pixels = bg_idx[alpha_mask == 255]
    else:
        valid_pixels = bg_idx.flatten()
    unique, counts = np.unique(valid_pixels, return_counts=True)
    if len(unique) == 0:
        raise ValueError("No valid background pixels to simplify")
    freq = dict(zip(unique, counts))
    top_colors = sorted(freq, key=freq.get, reverse=True)[:k]
    top_colors = np.array(top_colors, dtype=np.int32)
    log_info(f"background dominant LEGO colors: {top_colors.tolist()}")
    top_lab = palette_lab[top_colors]

    # Compute nearest top-color for each unique palette index that appears in
    # valid pixels (at most 43 iterations) then apply the remap in one vectorized op.
    index_remap = np.arange(len(palette_lab), dtype=np.int32)
    for idx in unique:
        lab = palette_lab[idx]
        d = color.deltaE_ciede2000(top_lab.reshape(-1, 1, 3), lab.reshape(1, 1, 3))
        index_remap[idx] = top_colors[int(np.argmin(d))]

    simplified_idx = index_remap[bg_idx]
    if alpha_mask is not None:
        simplified_idx[alpha_mask == 0] = bg_idx[alpha_mask == 0]
    return simplified_idx


def background_color_budget(bg_idx, fg_mask_np, background_color_percent):
    """How many distinct LEGO colors to keep in the simplified background.

    Scales the user's ``background_color_percent`` slider over the number of
    distinct colors in the REAL background region (``fg_mask_np == 0``).

    D-001: this previously counted ``bg_idx[fg_mask_np == 255]`` — the
    foreground silhouette — where ``remove_background`` white-fills every pixel,
    so the unique count was always 1 and the slider collapsed every background
    to a single color. ``fg_mask_np == 0`` is the visible background and mirrors
    the ``alpha_mask=(255 - fg_mask_np)`` passed to ``simplify_background_lego``.
    """
    background_indices = bg_idx[fg_mask_np == 0]
    unique_count = len(np.unique(background_indices))
    return max(1, int((background_color_percent / 100) * unique_count))


#takes an image and lego stud width and returns lego image and array of lego image pixel colors
def image_to_lego_mosaic(img, studs_w, alpha_mask=None):
    #calculate stud height calculation such that it is always divisible by 16
    orig_w, orig_h = img.size
    aspect = orig_h / orig_w
    # Width is already LEGO-valid → convert to block count
    blocks_w = studs_w // STUDS_PER_BLOCK   # exact integer

    # Solve height in the SAME coordinate system
    blocks_h = max(1, round(blocks_w * aspect))

    # Convert back to studs (guaranteed divisible by 16)
    studs_h = blocks_h * STUDS_PER_BLOCK

    img_small = img.resize((studs_w, studs_h), Image.LANCZOS)
    img_small = img_small.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
    rgb = np.asarray(img_small)/255.0
    lab = color.rgb2lab(rgb)
    out_idx = np.zeros((studs_h, studs_w), dtype=np.int32)
    err = np.zeros_like(lab)
    if alpha_mask is not None:
        alpha_small = alpha_mask.resize((studs_w, studs_h), Image.NEAREST)
        alpha_np = np.array(alpha_small)
    else:
        alpha_np = np.ones((studs_h, studs_w), dtype=np.uint8)*255
    err[alpha_np==0] = 0
    for y in range(studs_h):
        for x in range(studs_w):
            if alpha_np[y, x] == 0:
                continue
            current = lab[y, x] + err[y, x]
            idx = nearest_palette_index_lab(current)
            out_idx[y, x] = idx
            quant = PALETTE_LAB[idx]
            e = current - quant
            if x+1<studs_w and alpha_np[y, x+1]!=0: err[y,x+1] = np.clip(err[y,x+1]+e*7/16,-128,128)
            if y+1<studs_h:
                if x-1>=0 and alpha_np[y+1,x-1]!=0: err[y+1,x-1]=np.clip(err[y+1,x-1]+e*3/16,-128,128)
                if alpha_np[y+1,x]!=0: err[y+1,x]=np.clip(err[y+1,x]+e*5/16,-128,128)
                if x+1<studs_w and alpha_np[y+1,x+1]!=0: err[y+1,x+1]=np.clip(err[y+1,x+1]+e*1/16,-128,128)
        err[y, alpha_np[y]==0] = 0
    out_rgb = LEGO_PALETTE_RGB[out_idx].astype(np.uint8)
    out_img = Image.fromarray(out_rgb).resize((studs_w, studs_h), Image.NEAREST)
    return out_img, out_idx


# D-016: MediaPipe SelfieSegmentation's documented default threshold is 0.5.
# We use 0.51 — empirically biases slightly against borderline foreground pixels
# to reduce halo around low-contrast edges. Pinned as a named constant; worth
# re-tuning now that D-007 feeds MediaPipe the correct (RGB) colorspace.
_SEGMENTATION_FG_THRESHOLD = 0.51


#separates the background of an image, returning the foreground and background
def remove_background(pil_img):
    img = np.array(pil_img)  # PIL is already RGB — that's what MediaPipe expects.
    # D-011: context-manager form releases MediaPipe's native (C++) graph
    # resources on exit. Plain GC does not, so without this the graph leaks once
    # max_tasks_per_child rises above 1 (worker runs >1 job before exiting).
    with mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1) as mp_selfie:
        # D-007: feed RGB straight through. The old cv2.cvtColor(img, BGR2RGB)
        # swapped channels and handed MediaPipe a BGR-coded tensor, degrading
        # segmentation precision (the model is RGB-trained).
        results = mp_selfie.process(img)
        if results.segmentation_mask is None:
            raise RuntimeError("Selfie segmentation failed")
        # Derive fg_mask inside the `with` so it never reads through MediaPipe's
        # buffer after the graph closes.
        fg_mask = results.segmentation_mask > _SEGMENTATION_FG_THRESHOLD
    background = np.where(~fg_mask[...,None], img, 255)
    foreground = np.where(fg_mask[...,None], img, 255)
    fg_pil = Image.fromarray(foreground.astype(np.uint8))
    bg_pil = Image.fromarray(background.astype(np.uint8))
    return fg_pil, bg_pil, fg_mask


#takes and returns PIL.Image.Image with delta lightness change
def adjust_lightness_lab(img_pil, delta_L):
    if delta_L == 0:
        return img_pil

    arr = np.asarray(img_pil)

    # Handle RGBA by separating alpha
    if arr.shape[-1] == 4:
        rgb = arr[..., :3]
        alpha = arr[..., 3:]
    else:
        rgb = arr
        alpha = None

    rgb = rgb.astype(np.float32) / 255.0 # Normalize once
    lab = color.rgb2lab(rgb) # Convert to LAB
    # Adjust lightness in-place
    np.add(lab[..., 0], delta_L, out=lab[..., 0])
    np.clip(lab[..., 0], 0, 100, out=lab[..., 0])

    # Back to RGB
    rgb_out = color.lab2rgb(lab)
    np.clip(rgb_out, 0, 1, out=rgb_out)

    rgb_out = (rgb_out * 255).astype(np.uint8)

    # Reattach alpha if needed
    if alpha is not None:
        rgb_out = np.concatenate([rgb_out, alpha], axis=-1)

    return Image.fromarray(rgb_out)


#makes the pixels that match on the original to the new one to be transparent
def make_difference_transparent(orig, new):
    orig = np.array(orig.convert("RGBA"))
    fg = np.array(new.convert("RGBA"))
    diff = np.any(fg[...,:3] != orig[...,:3], axis=-1)
    fg[diff, 3] = 0   # modify fg in place
    return Image.fromarray(fg)


# MosaicType is imported from .mosaic_types (D-032). Re-exported here so existing
# `from .picToMosiac import MosaicType` call sites keep working.


#gives the error name, type, and line location
def give_exception_message(e):
    tb = traceback.extract_tb(e.__traceback__)[-1] #get last frame of stack
    log_error(f"[ERROR] {type(e).__name__}: {e}")
    log_error(f"File: {tb.filename}")
    log_error(f"Line: {tb.lineno}")
    log_error(f"Function: {tb.name}")
    log_error(f"Code: {tb.line}")
    raise


#takes path and returns image rgb
def open_image(image_path):
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")
   
    img = Image.open(image_path).convert("RGB")
    return img


def pic_to_mosaic(img_path, block_width, mosiac_type, background_color_percent, to_frame, output_dir=None, job_id=None, progress_callback=None):
    def report(pct):
        if progress_callback:
            progress_callback(pct)

    try:
        report(1)
        img = open_image(img_path)
        #image_folder = Path(__file__).resolve().parent.parent / "images"

        if mosiac_type == MosaicType.THREE_D:
            log_debug("starting 3d mosaic process by differentiating between fg and bg...")
            report(5)
            fg_pil, bg_pil, fg_mask = remove_background(img)

            report(10)
            fg_alpha_pil = make_difference_transparent(img, fg_pil)
            fg_rgba = fg_alpha_pil.convert("RGBA")
            fg_a = fg_rgba.getchannel("A").point(lambda p:255 if p>0 else 0)
    
            report(15)
            fg_filtered_image = adjust_lightness_lab(fg_rgba.convert("RGB"), delta_L=5)
            report(25)
            bg_filtered_image = adjust_lightness_lab(bg_pil, delta_L=5)

            report(30)
            log_debug("converting processed image to lego mosiac...")
            fg_out_img, fg_idx = image_to_lego_mosaic(fg_filtered_image, block_width, alpha_mask=fg_a)
            bg_out_img, bg_idx = image_to_lego_mosaic(bg_filtered_image, block_width)
            fg_mask_resized = fg_a.resize(bg_idx.shape[::-1], Image.NEAREST)
            fg_mask_np = np.array(fg_mask_resized)
            color_quant = background_color_budget(bg_idx, fg_mask_np, background_color_percent)
            bg_idx_simplified = simplify_background_lego(bg_idx, PALETTE_LAB, k=color_quant, alpha_mask=(255-fg_mask_np))
            bg_rgb_simplified = LEGO_PALETTE_RGB[bg_idx_simplified]
            bg_out_img = Image.fromarray(bg_rgb_simplified.astype(np.uint8))

            if output_dir is not None:
                try:
                    studs_h = fg_idx.shape[0]
                    preview_payload = build_preview_payload(
                        job_id=job_id or "",
                        mosaic_type="3d",
                        block_width=block_width // STUDS_PER_BLOCK,
                        block_height=studs_h // STUDS_PER_BLOCK,
                        studs_width=block_width,
                        studs_height=studs_h,
                        to_frame=to_frame,
                        bg_idx=bg_idx_simplified,
                        fg_idx=fg_idx,
                        fg_mask=fg_mask_np,
                    )
                    write_preview_atomic(preview_payload, Path(output_dir) / "preview.json")
                except Exception as e:
                    log_error(f"preview.json build/write failed (non-fatal): {e}")

            fg_alpha_resized = fg_a.resize((block_width, fg_idx.shape[0]), Image.NEAREST)
            fg_out_rgba = fg_out_img.convert("RGBA").resize((block_width, fg_idx.shape[0]), Image.NEAREST)
            fg_out_rgba.putalpha(fg_alpha_resized)
            bg_rgba = bg_out_img.convert("RGBA").resize(fg_out_rgba.size, Image.NEAREST)

            composite = Image.alpha_composite(bg_rgba, fg_out_rgba)

            report(35)
            log_debug("generating order list...")
            GenerateOrderList(fg_out_rgba, bg_rgba, to_frame, output_dir)

            GenerateInstructions(fg_out_rgba, bg_rgba, composite, to_frame, output_dir, progress_callback=report)
            log_debug("finished mosiac generation!")

        else:
            log_debug("starting 2d mosaic process by adjusting lightness...")
            report(5)
            filtered_image = adjust_lightness_lab(img, delta_L=5)

            report(15)
            out_img, img_idx = image_to_lego_mosaic(filtered_image, block_width)

            out_img_rgba = out_img.convert("RGBA")

            if output_dir is not None:
                try:
                    studs_h = img_idx.shape[0]
                    preview_payload = build_preview_payload(
                        job_id=job_id or "",
                        mosaic_type="2d",
                        block_width=block_width // STUDS_PER_BLOCK,
                        block_height=studs_h // STUDS_PER_BLOCK,
                        studs_width=block_width,
                        studs_height=studs_h,
                        to_frame=to_frame,
                        bg_idx=img_idx,
                        fg_idx=None,
                        fg_mask=None,
                    )
                    write_preview_atomic(preview_payload, Path(output_dir) / "preview.json")
                except Exception as e:
                    log_error(f"preview.json build/write failed (non-fatal): {e}")

            log_debug("generating order list...")
            report(30)
            GenerateOrderList(None, out_img_rgba, to_frame, output_dir)

            report(35)
            GenerateInstructions(None, out_img_rgba, out_img_rgba, to_frame, output_dir, progress_callback=report)
            log_debug("finished mosiac generation!")


    except Exception as e:
        give_exception_message(e)
# D-013: the __main__ CLI entry point (+ handle_input) was removed. It ran
# against a hardcoded image path (images/stella1.jpg) and the legacy
# output_dir=None scratch path; the API worker is the only production entry.