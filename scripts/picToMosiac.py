import os
from PIL import Image, ImageFilter
import numpy as np
from skimage import color
import sys
from pathlib import Path
import cv2
import mediapipe as mp
import copy

sys.path.append(str(Path(__file__).resolve().parent))
from MosiacToOrder import GenerateOrderList
from MosiacToInstruction import GenerateInstructions
from Util import GetPaletteRGBArray


# ------------------------------
# Helper functions
# ------------------------------
def rgb_list_to_lab(arr_rgb):
    arr = np.array(arr_rgb, dtype=np.uint8).reshape((-1,1,3))/255.0
    lab = color.rgb2lab(arr).reshape((-1,3))
    return lab

LEGO_PALETTE_RGB = GetPaletteRGBArray()
PALETTE_LAB = rgb_list_to_lab(LEGO_PALETTE_RGB)

def split_order_list(order: dict, max_quantity: int = 999) -> dict:
    """
    Takes a dict {elementId: quantity} and returns a new dict
    with quantities capped at max_quantity per entry.
    """
    split_order = {}
    for elementId, qty in order.items():
        while qty > 0:
            current_qty = min(qty, max_quantity)
            # Use unique key for split pieces (optional) or accumulate into list of dicts later
            split_order[elementId] = split_order.get(elementId, []) + [current_qty]
            qty -= current_qty
    return split_order

def nearest_palette_index_lab(pixel_lab, palette_lab):
    pixel_lab_reshaped = pixel_lab.reshape((1, 1, 3))
    palette_lab_reshaped = palette_lab.reshape((-1, 1, 3))
    d = color.deltaE_ciede2000(palette_lab_reshaped, pixel_lab_reshaped)
    return int(np.argmin(d))

def simplify_background_lego(bg_idx, palette_lab, k=5, alpha_mask=None):
    print(f"simplifying background to top {k} lego colors...")
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
    print("background dominant LEGO colors:", top_colors.tolist())
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

def image_to_lego_mosaic(img, studs_w, alpha_mask=None):
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=350, threshold=3))
    orig_w, orig_h = img.size
    aspect = orig_h / orig_w
    studs_h = int(round(studs_w * aspect))
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
            idx = nearest_palette_index_lab(current, PALETTE_LAB)
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

def adjust_lightness_lab(img_pil, delta_L):
    print(f"adjusting lightness by {delta_L} in Lab space...")
    rgb = np.asarray(img_pil).astype(np.float32)/255.0
    lab = color.rgb2lab(rgb)
    lab[...,0]=np.clip(lab[...,0]+delta_L,0,100)
    rgb_out = np.clip(color.lab2rgb(lab)*255,0,255).astype(np.uint8)
    return Image.fromarray(rgb_out)

def make_difference_transparent(orig, new):
    orig = np.array(orig.convert("RGBA"))
    fg = np.array(new.convert("RGBA"))
    diff = np.any(fg[...,:3] != orig[...,:3], axis=-1)
    result = fg.copy()
    result[diff,3] = 0
    return Image.fromarray(result)

# ------------------------------
# Main script with error handling
# ------------------------------
if __name__ == "__main__":
    
    try:
        print("starting picture to lego mosaic conversion...")
        if len(sys.argv) < 3:
            raise ValueError("Usage: python picToMosiac.py width PercentOfBackgroundColors")
        studs_width = int(sys.argv[1])
        background_color_percent = int(sys.argv[2])
        if studs_width <= 0: raise ValueError("Studs width must be positive")
        if not (0<background_color_percent<=100): raise ValueError("Background color percent must be 1-100")
        
        script_dir = Path(__file__).resolve().parent
        image_folder = script_dir.parent / "images"
        image_path = image_folder / "stella1.jpg"
        if not image_path.exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        
        img = Image.open(image_path).convert("RGB")
        fg_pil, bg_pil, fg_mask = remove_background(img)
        fg_alpha_pil = make_difference_transparent(img, fg_pil)
        fg_rgba = fg_alpha_pil.convert("RGBA")
        fg_a = fg_rgba.getchannel("A").point(lambda p:255 if p>0 else 0)
        fg_filtered_image = adjust_lightness_lab(fg_rgba.convert("RGB"), delta_L=5)
        bg_filtered_image = adjust_lightness_lab(bg_pil, delta_L=5)
        
        print("converting processed image to lego mosiac...")
        fg_out_img, fg_idx = image_to_lego_mosaic(fg_filtered_image, studs_width, alpha_mask=fg_a)
        bg_out_img, bg_idx = image_to_lego_mosaic(bg_filtered_image, studs_width)
        
        fg_mask_resized = fg_a.resize(bg_idx.shape[::-1], Image.NEAREST)
        fg_mask_np = np.array(fg_mask_resized)
        numOfDesiredColors = max(1,int((background_color_percent/100)*len(np.unique(bg_idx[fg_mask_np==255]))))
        bg_idx_simplified = simplify_background_lego(bg_idx, PALETTE_LAB, k=numOfDesiredColors, alpha_mask=(255-fg_mask_np))
        bg_rgb_simplified = LEGO_PALETTE_RGB[bg_idx_simplified]
        bg_out_img = Image.fromarray(bg_rgb_simplified.astype(np.uint8))
        
        fg_alpha_resized = fg_a.resize((studs_width, fg_idx.shape[0]), Image.NEAREST)
        fg_out_rgba = fg_out_img.convert("RGBA").resize((studs_width, fg_idx.shape[0]), Image.NEAREST)
        fg_out_rgba.putalpha(fg_alpha_resized)
        bg_rgba = bg_out_img.convert("RGBA").resize(fg_out_rgba.size, Image.NEAREST)
        
        print("size of foreground mosaic:", fg_out_rgba.size)
        fg_out_rgba.show()
        bg_rgba.show()

        composite = Image.alpha_composite(bg_rgba, fg_out_rgba)
        composite.show()
        
        img_output_path = image_folder / f"{image_path.stem}_lego.png"
        composite.save(img_output_path)
        print(f"Saved mosaic to {img_output_path}")

        print("generating order list...")
        GenerateOrderList(fg_out_rgba, bg_rgba)
        GenerateInstructions(fg_out_rgba, bg_rgba, composite)
        print("finished instructions!")
    
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        raise
