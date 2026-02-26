import os
from PIL import Image, ImageFilter
import numpy as np
from skimage import color
import sys
from pathlib import Path
import cv2
import mediapipe as mp
import copy
from enum import Enum
import traceback
sys.path.append(str(Path(__file__).resolve().parent)) #look in same folder for scripts
from .MosiacToOrder import GenerateOrderList
from .MosiacToInstruction import GenerateInstructions
from .Util import GetPaletteRGBArray, load_project_env
load_project_env() #a little jenky way to avoid circular imports
from .Util import log_debug, log_info, log_error


#converts a list of RGB colors into a NumPy array of Lab colors
def rgb_list_to_lab(arr_rgb):
    arr = np.array(arr_rgb, dtype=np.uint8).reshape((-1,1,3))/255.0
    lab = color.rgb2lab(arr).reshape((-1,3))
    return lab


load_project_env()
LEGO_PALETTE_RGB = GetPaletteRGBArray()
PALETTE_LAB = rgb_list_to_lab(LEGO_PALETTE_RGB)
PALETTE_LAB_RESHAPED = PALETTE_LAB.reshape((-1, 1, 3))
MAX_BLOCK_WIDTH = int(os.getenv("MAX_MOSAIC_BLOCK_WIDTH"))
MIN_BLOCK_WIDTH = 1
STUDS_PER_BLOCK = int(os.getenv("STUD_WIDTH_OF_BLOCK"))


#gets the index of the closest color to the pixel from the palette by visual distance
def nearest_palette_index_lab(pixel_lab):
    pixel_lab_reshaped = pixel_lab.reshape((1, 1, 3)) #reshape in order to compare
    d = color.deltaE_ciede2000(PALETTE_LAB_RESHAPED, pixel_lab_reshaped)
    return int(np.argmin(d)) #return closest color index


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
    simplified_idx = np.copy(bg_idx)
    H, W = bg_idx.shape
    for y in range(H):
        for x in range(W):
            if alpha_mask is not None and alpha_mask[y, x] == 0:
                continue
            lab = palette_lab[bg_idx[y, x]]
            d = color.deltaE_ciede2000(top_lab.reshape(-1,1,3), lab.reshape(1,1,3))
            simplified_idx[y, x] = top_colors[np.argmin(d)]
    return simplified_idx


#takes an image and lego stud width and returns lego image and array of lego image pixel colors
def image_to_lego_mosaic(img, studs_w, alpha_mask=None):
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=350, threshold=3))
    #calculate stud height calculation such that it is always divisible by 16
    orig_w, orig_h = img.size
    aspect = orig_h / orig_w
    # Width is already LEGO-valid → convert to block count
    blocks_w = studs_w // STUDS_PER_BLOCK   # exact integer

    # Solve height in the SAME coordinate system
    blocks_h = max(1, round(blocks_w * aspect))

    # Convert back to studs (guaranteed divisible by 16)
    studs_h = blocks_h * STUDS_PER_BLOCK

    img_small = img.resize((studs_w, studs_h), Image.NEAREST)
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
    out_rgb = np.array([LEGO_PALETTE_RGB[i] for i in out_idx.flatten()]).reshape((studs_h, studs_w, 3)).astype(np.uint8)
    out_img = Image.fromarray(out_rgb).resize((studs_w, studs_h), Image.NEAREST)
    return out_img, out_idx


#separates the background of an image, returning the foreground and background
def remove_background(pil_img):
    mp_selfie = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
    img = np.array(pil_img)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    results = mp_selfie.process(img_rgb)
    if results.segmentation_mask is None:
        raise RuntimeError("Selfie segmentation failed")
    mask = results.segmentation_mask
    fg_mask = mask > 0.51
    background = np.where(~fg_mask[...,None], img, 255)
    foreground = np.where(fg_mask[...,None], img, 255)
    fg_pil = Image.fromarray(foreground.astype(np.uint8))
    bg_pil = Image.fromarray(background.astype(np.uint8))
    return fg_pil, bg_pil, fg_mask


#takes and returns PIL.Image.Image with delta lightness change
def adjust_lightness_lab(img_pil, delta_L):
    #log_debug(f"adjusting lightness by {delta_L} in Lab space...")
    rgb = np.asarray(img_pil).astype(np.float32)/255.0
    lab = color.rgb2lab(rgb)
    lab[...,0]=np.clip(lab[...,0]+delta_L,0,100)
    rgb_out = np.clip(color.lab2rgb(lab)*255,0,255).astype(np.uint8)
    return Image.fromarray(rgb_out)


#makes the pixels that match on the original to the new one to be transparent
def make_difference_transparent(orig, new):
    orig = np.array(orig.convert("RGBA"))
    fg = np.array(new.convert("RGBA"))
    diff = np.any(fg[...,:3] != orig[...,:3], axis=-1)
    result = fg.copy()
    result[diff,3] = 0
    return Image.fromarray(result)


#helper enum for input handling
class MosaicType(str, Enum):
    TWO_D = "2d"
    THREE_D = "3d"


#processes system args and raises error if invalid
def handle_input(args):
    if len(args) != 5:
        raise ValueError("Usage: python picToMosiac.py Width Dimension PercentOfBackgroundColors WantFrame")

    width = int(args[1])
    dimension = args[2].upper()
    background_color_percent = int(args[3])

    frame_str = args[4].strip().lower() # Parse boolean explicitly
    if frame_str not in ("true", "false"):
        raise ValueError("WantFrame must be 'True' or 'False' (not case sensitive)")
    to_frame = (frame_str == "true")

    if not (MIN_BLOCK_WIDTH <= width <= MAX_BLOCK_WIDTH): #ensure desired width is within bounds
        raise ValueError("Width must be 1-40 blocks")

    if dimension not in ("2D", "3D"):
        raise ValueError("Dimension must be '2D' or '3D' (not case sensitive)")

    if not (1 <= background_color_percent <= 100):
        raise ValueError("PercentOfBackgroundColors must be 1-100 percent")
   
    studs_width = width * STUDS_PER_BLOCK #there are 16 studs per baseplate block side (this ensures width of mosiac = width of baseplate(s))

    if (dimension == "2D"): # return 2d for flat mosiac
        return MosaicType.TWO_D, studs_width, background_color_percent, to_frame
   
    return MosaicType.THREE_D, studs_width, background_color_percent, to_frame # return 3d for mosiac with foreground and background


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


def pic_to_mosaic(img_path, block_width, mosiac_type, background_color_percent, to_frame, output_dir=None, job_id=None):
    try:
        img = open_image(img_path) #gets RGB of image
        image_folder = Path(__file__).resolve().parent.parent / "images"
        #log_info("starting picture to lego mosaic conversion...")
        if mosiac_type == MosaicType.THREE_D: #handle 3d mosiac case with foreground and background layers
            log_debug("starting 3d mosaic process by differentiating between fg and bg...")
            fg_pil, bg_pil, fg_mask = remove_background(img) #separates foreground from background
            fg_alpha_pil = make_difference_transparent(img, fg_pil) #makes the background transparent on the foreground
            fg_rgba = fg_alpha_pil.convert("RGBA")
            fg_a = fg_rgba.getchannel("A").point(lambda p:255 if p>0 else 0) #get alpha from foreground to use later
            #brighten the foreground/background
            fg_filtered_image = adjust_lightness_lab(fg_rgba.convert("RGB"), delta_L=5)
            bg_filtered_image = adjust_lightness_lab(bg_pil, delta_L=5)
       
            log_debug("converting processed image to lego mosiac...")
            fg_out_img, fg_idx = image_to_lego_mosaic(fg_filtered_image, block_width, alpha_mask=fg_a)
            bg_out_img, bg_idx = image_to_lego_mosaic(bg_filtered_image, block_width)
       
            fg_mask_resized = fg_a.resize(bg_idx.shape[::-1], Image.NEAREST)
            fg_mask_np = np.array(fg_mask_resized)
            color_quant = max(1,int((background_color_percent/100)*len(np.unique(bg_idx[fg_mask_np==255]))))
            bg_idx_simplified = simplify_background_lego(bg_idx, PALETTE_LAB, k=color_quant, alpha_mask=(255-fg_mask_np))
            bg_rgb_simplified = LEGO_PALETTE_RGB[bg_idx_simplified]
            bg_out_img = Image.fromarray(bg_rgb_simplified.astype(np.uint8))
       
            fg_alpha_resized = fg_a.resize((block_width, fg_idx.shape[0]), Image.NEAREST)
            fg_out_rgba = fg_out_img.convert("RGBA").resize((block_width, fg_idx.shape[0]), Image.NEAREST)
            fg_out_rgba.putalpha(fg_alpha_resized)
            bg_rgba = bg_out_img.convert("RGBA").resize(fg_out_rgba.size, Image.NEAREST)
       
            #log_debug(f"size of foreground mosaic: {fg_out_rgba.size}")
            #log_debug(f"size of background mosaic: {bg_rgba.size}")
            #fg_out_rgba.show()
            #bg_rgba.show()

            #combine the background and foreground for the final 3d mosiac
            composite = Image.alpha_composite(bg_rgba, fg_out_rgba)
            #composite.show()
       
            # img_output_path = image_folder / f"{img_path.stem}_lego.png"
            # composite.save(img_output_path)
            #log_info(f"Saved mosaic to {img_output_path}")

            log_debug("generating order list...")
            GenerateOrderList(fg_out_rgba, bg_rgba, to_frame, output_dir)
            GenerateInstructions(fg_out_rgba, bg_rgba, composite, to_frame, output_dir)
            log_debug("finished mosiac generation!")

        else: #handle 2d mosiac case
            log_debug("starting 2d mosaic process by adjusting lightness...")
            #adjust lightness then convert to lego mosiac (no need to handle alpha stuff for one layer)
            filtered_image = adjust_lightness_lab(img, delta_L=5)
            out_img, img_idx = image_to_lego_mosaic(filtered_image, block_width)
            #show and save the image
            #out_img.show()
            # img_output_path = image_folder / f"{img_path.stem}_lego.png"
            # out_img.save(img_output_path)
            out_img_rgba = out_img.convert("RGBA")
            #generate order list and instructions to create mosaic
            log_debug("generating order list...")
            #GenerateOrderList and GenerateInstructions handle 2d mosaics (if second param is None) and/or no frame (if last param is False)
            GenerateOrderList(None, out_img_rgba, to_frame, output_dir)
            GenerateInstructions(None, out_img_rgba, out_img_rgba, to_frame, output_dir)
            log_debug("finished mosiac generation!")
   
    except Exception as e:
        give_exception_message(e)


if __name__ == "__main__":
    try:
        log_info("handling input...")
        mosiac_type, block_width, background_color_percent, to_frame = handle_input(sys.argv) #handle console args
        image_folder = Path(__file__).resolve().parent.parent / "images"
        image_name = "stella1.jpg"
        image_path = image_folder / image_name
        pic_to_mosaic(image_path, block_width, mosiac_type, background_color_percent, to_frame)
    except Exception as e:
        give_exception_message(e)