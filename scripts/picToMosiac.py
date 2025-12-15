import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

import absl.logging
absl.logging.set_verbosity(absl.logging.ERROR)

from PIL import Image, ImageFilter
import numpy as np
from skimage import color
import sys
from pathlib import Path
import cv2
import mediapipe as mp

# LEGO palette in rgb
# got color codes from https://rebrickable.com/colors/
LEGO_PALETTE_RGB = np.array([
    [114, 20, 15],     # Dark Red
    [255, 105, 143],   # Coral
    [228, 173, 200],   # Bright Pink
    [200, 112, 160],   # Dark Pink
    [146, 57, 120],    # Magenta
    [172, 120, 186],   # Medium Lavender
    [225, 213, 237],   # Lavender
    [63, 54, 145],     # Dark Purple / Medium Lilac
    [96, 116, 161],    # Sand Blue
    [0, 85, 191],      # Blue
    [90, 147, 219],    # Medium Blue
    [10, 52, 99],      # Dark Blue / Earth Blue
    [159, 195, 233],   # Bright Light Blue
    [7, 139, 201],     # Dark Azure
    [54, 174, 191],    # Medium Azure
    [0, 143, 155],     # Dark Turquoise
    [173, 195, 192],   # Light Aqua
    [179, 215, 209],   # Aqua
    [24, 70, 50],      # Dark Green / Earth Green
    [160, 188, 172],   # Sand Green
    [35, 120, 65],     # Green
    [75, 159, 74],     # Bright Green
    [187, 233, 11],    # Lime
    [223, 238, 165],   # Yellowish Green
    [155, 154, 90],    # Olive Green
    [235, 216, 0],     # Vibrant Yellow
    [255, 240, 58],    # Bright Light Yellow
    [248, 187, 61],    # Bright Light Orange
    [244, 205, 47],    # Bright Yellow
    [228, 205, 158],   # Tan
    [149, 138, 115],   # Dark Tan
    [205, 163, 115],   # Warm Tan / Medium Tan
    [165, 125, 85],    # Medium Nougat
    [117, 89, 69],     # Medium Brown
    [88, 42, 18],      # Reddish Brown
    [94, 92, 51],      # Olive Green (duplicate variant)
    [145, 76, 12],     # Reddish Orange
    [94, 63, 51],      # Umber Brown
    [201, 26, 9],      # Red
    [248, 138, 24],    # Orange
    [169, 85, 0],      # Dark Orange
    [214, 158, 71],    # Ochre Yellow
    [246, 215, 179],   # Light Nougat
    [208, 145, 104],   # Nougat
    [145, 92, 60],     # Sienna Brown
    [53, 33, 0],       # Dark Brown
    [160, 165, 169],   # Light Bluish Gray
    [108, 110, 104],   # Dark Bluish Gray
    [5, 19, 29],       # Black
    [255, 255, 255],   # White
])

def rgb_list_to_lab(arr_rgb):
    arr = np.array(arr_rgb, dtype=np.uint8).reshape((-1,1,3))/255.0
    lab = color.rgb2lab(arr).reshape((-1,3))
    return lab

PALETTE_LAB = rgb_list_to_lab(LEGO_PALETTE_RGB)

def nearest_palette_index_lab(pixel_lab, palette_lab):
    # Use perceptually accurate CIEDE2000 distance
    pixel_lab_reshaped = pixel_lab.reshape((1, 1, 3))
    palette_lab_reshaped = palette_lab.reshape((-1, 1, 3))
    d = color.deltaE_ciede2000(palette_lab_reshaped, pixel_lab_reshaped)
    return int(np.argmin(d))

def image_to_lego_mosaic(img, studs_w):
    # Apply Unsharp Mask
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=350, threshold=3))

    # Maintain aspect ratio — width fixed, height derived
    orig_w, orig_h = img.size
    aspect = orig_h / orig_w
    studs_h = int(round(studs_w * aspect))

    img_small = img.resize((studs_w, studs_h), resample=Image.LANCZOS)

    rgb = np.asarray(img_small)/255.0  # float [0,1]
    lab = color.rgb2lab(rgb)

    out_idx = np.zeros((studs_h, studs_w), dtype=np.int32)
    err = np.zeros_like(lab)

    # Floyd–Steinberg kernel (distributes error to neighbors)
    for y in range(studs_h):
        for x in range(studs_w):
            current = lab[y, x] + err[y, x]
            idx = nearest_palette_index_lab(current, PALETTE_LAB)
            out_idx[y, x] = idx
            quant = PALETTE_LAB[idx]
            e = current - quant
            # distribute error (in Lab), clamped to prevent overflow
            if x+1 < studs_w: err[y, x+1] = np.clip(err[y, x+1] + e * 7/16, -128, 128)
            if y+1 < studs_h:
                if x-1 >= 0: err[y+1, x-1] = np.clip(err[y+1, x-1] + e * 3/16, -128, 128)
                err[y+1, x] = np.clip(err[y+1, x] + e * 5/16, -128, 128)
                if x+1 < studs_w: err[y+1, x+1] = np.clip(err[y+1, x+1] + e * 1/16, -128, 128)

    # produce RGB output image of studs
    out_rgb = np.array([LEGO_PALETTE_RGB[i] for i in out_idx.flatten()]).reshape((studs_h, studs_w, 3)).astype(np.uint8)
    out_img = Image.fromarray(out_rgb).resize((studs_w * 10, studs_h * 10), Image.NEAREST)
    return out_img, out_idx


def remove_background(pil_img):
    mp_selfie = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)

    img = np.array(pil_img)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    results = mp_selfie.process(img_rgb)
    mask = results.segmentation_mask

    # Foreground mask (True = subject)
    fg_mask = mask > 0.51
    bg_mask = ~fg_mask

    # --- Foreground only (background becomes white) ---
    foreground = np.where(fg_mask[..., None], img, 255)

    # --- Background only (foreground becomes white or transparent) ---
    background = np.where(bg_mask[..., None], img, 255)

    # Convert back to PIL
    fg_pil = Image.fromarray(foreground.astype(np.uint8))
    bg_pil = Image.fromarray(background.astype(np.uint8))

    return fg_pil, bg_pil, fg_mask

#todo: get this function returning a transparent foreground (front most layer of lego mosiac)
def make_difference_transparent(orig, new):
    # Compare fg PIL to original image, making all pixels that are different transparent
    orig = np.array(orig.convert("RGBA"))
    fg = np.array(new.convert("RGBA"))

    # Mask of pixels that changed after background removal
    diff = np.any(fg[..., :3] != orig[..., :3], axis=-1)

    # Apply transparency
    result = fg.copy()
    result[diff, 3] = 0

    return Image.fromarray(result)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python picToMosiac.py width")
        sys.exit(1)

    studs_width = int(sys.argv[1])
    script_dir = Path(__file__).resolve().parent
    image_folder = script_dir.parent / "images"
    image_path = image_folder / "stella1.jpg"

    img = Image.open(image_path).convert('RGB')

    print("processing image with background-foreground separation...")
    # Remove background FIRST (foreground = layer 2, background = layer 1)
    fg_pil, bg_pil, fg_mask = remove_background(img)

    #downsize foreground and background to get correct alpha mask

    # Compare fg PIL to original image → make background transparent
    fg_alpha_pil = make_difference_transparent(img, fg_pil)  # RGBA

    # -----------------------------
    # FOREGROUND: preserve alpha
    # -----------------------------
    fg_rgba = fg_alpha_pil.convert("RGBA")
    fg_rgb  = fg_rgba.convert("RGB")             # RGB for OpenCV + mosaic
    fg_a    = fg_rgba.getchannel("A")            # store alpha separately

    # Convert RGB → OpenCV BGR
    fg_bgr = cv2.cvtColor(np.array(fg_rgb), cv2.COLOR_RGB2BGR)

    # Apply filters on RGB only
    fg_bilateral_bgr = cv2.bilateralFilter(fg_bgr, 15, 150, 150)
    fg_gaus_blur_bgr = cv2.medianBlur(fg_bilateral_bgr, 25)

    # Back to PIL RGB
    fg_filtered_rgb = cv2.cvtColor(fg_gaus_blur_bgr, cv2.COLOR_BGR2RGB)
    fg_filtered_image = Image.fromarray(fg_filtered_rgb)

    # -----------------------------
    # BACKGROUND (no alpha)
    # -----------------------------
    bg_np = np.array(bg_pil)
    bg_bgr = cv2.cvtColor(bg_np, cv2.COLOR_RGB2BGR)

    bg_bilateral_bgr = cv2.bilateralFilter(bg_bgr, 15, 150, 150)
    bg_gaus_blur_bgr = cv2.medianBlur(bg_bilateral_bgr, 25)

    bg_filtered_rgb = cv2.cvtColor(bg_gaus_blur_bgr, cv2.COLOR_BGR2RGB)
    bg_filtered_image = Image.fromarray(bg_filtered_rgb)

    # -----------------------------
    # Produce LEGO mosaics
    # -----------------------------
    print("converting processed image to lego mosiac...")

    # ---- Foreground mosaic (RGB only) ----
    fg_out_img, idx = image_to_lego_mosaic(fg_filtered_image, studs_width)

    # Resize alpha to match mosaic output
    fg_alpha_resized = fg_a.resize(fg_out_img.size, Image.NEAREST)

    # Recombine RGB mosaic + alpha
    fg_out_rgba = fg_out_img.convert("RGBA")
    fg_out_rgba.putalpha(fg_alpha_resized)

    print("showing foreground and background (layer 2 and 1)")
    fg_out_rgba.show()  # now shows with transparency preserved

    # ---- Background mosaic ----
    bg_out_img, idx = image_to_lego_mosaic(bg_filtered_image, studs_width)
    bg_out_img.show()

    #TODO: send background and foreground to instruction set generation script

    # Ensure BG is RGBA to match
    bg_rgba = bg_out_img.convert("RGBA")

    # Composite: foreground on top
    composite = Image.alpha_composite(bg_rgba, fg_out_rgba)

    print("showing final output")
    # Show result
    composite.show()
    


    

    #out_img.save(image_folder / "mosaic_preview.png")
    #os.startfile(image_folder / "mosaic_preview.png")     # Windows only
    output_path = image_folder / f"{image_path.stem}_lego.png"
    fg_out_img.save(output_path)
    print(f"Saved mosaic to {output_path}")
