import os
from PIL import Image, ImageFilter
import numpy as np
from skimage import color
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import cv2
import mediapipe as mp
from MosiacToOrder import GenerateOrderList
from MosiacToInstruction import GenerateInstructions
from Util import GetPalette



def rgb_list_to_lab(arr_rgb):
    arr = np.array(arr_rgb, dtype=np.uint8).reshape((-1,1,3))/255.0
    lab = color.rgb2lab(arr).reshape((-1,3))
    return lab

LEGO_PALETTE_RGB = GetPalette()
PALETTE_LAB = rgb_list_to_lab(LEGO_PALETTE_RGB)

def nearest_palette_index_lab(pixel_lab, palette_lab):
    # Use perceptually accurate CIEDE2000 distance
    pixel_lab_reshaped = pixel_lab.reshape((1, 1, 3))
    palette_lab_reshaped = palette_lab.reshape((-1, 1, 3))
    d = color.deltaE_ciede2000(palette_lab_reshaped, pixel_lab_reshaped)
    return int(np.argmin(d))

def simplify_background_lego(bg_idx, palette_lab, k=5):
    """
    Simplify background to top-k LEGO colors (index-space clustering).
    bg_idx: (H,W) array of LEGO palette indices
    """

    print(f"simplifying background to top {k} lego colors...")

    # Count frequency of LEGO colors
    unique, counts = np.unique(bg_idx, return_counts=True)
    freq = dict(zip(unique, counts))

    # Select top-k most frequent colors
    top_colors = sorted(freq, key=freq.get, reverse=True)[:k]
    top_colors = np.array(top_colors, dtype=np.int32)

    print("background dominant LEGO colors:", top_colors.tolist())

    # LAB values of retained colors
    top_lab = palette_lab[top_colors]

    # Reassign each pixel to nearest retained LEGO color
    simplified_idx = np.zeros_like(bg_idx)

    for y in range(bg_idx.shape[0]):
        for x in range(bg_idx.shape[1]):
            lab = palette_lab[bg_idx[y, x]]
            d = color.deltaE_ciede2000(
                top_lab.reshape(-1, 1, 3),
                lab.reshape(1, 1, 3)
            )
            simplified_idx[y, x] = top_colors[np.argmin(d)]

    return simplified_idx


def image_to_lego_mosaic(img, studs_w):
    # Apply Unsharp Mask
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=350, threshold=3))

    # Maintain aspect ratio — width fixed, height derived
    orig_w, orig_h = img.size
    aspect = orig_h / orig_w
    studs_h = int(round(studs_w * aspect))

    img_small = img.resize((studs_w, studs_h), Image.NEAREST)

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
    out_img = Image.fromarray(out_rgb).resize((studs_w, studs_h), Image.NEAREST)
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

def adjust_lightness_lab(img_pil, delta_L):
    """
    delta_L: [-100, 100] perceptual lightness shift
    """
    print(f"adjusting lightness by {delta_L} in Lab space...")

    rgb = np.asarray(img_pil).astype(np.float32) / 255.0
    lab = color.rgb2lab(rgb)

    lab[..., 0] = np.clip(lab[..., 0] + delta_L, 0, 100)

    rgb_out = np.clip(color.lab2rgb(lab) * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb_out)


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

# ... [imports and LEGO palette remain unchanged] ...

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python picToMosiac.py width PercentOfBackgroundColors")
        sys.exit(1)

    studs_width = int(sys.argv[1])
    background_color_percent = int(sys.argv[2])
    script_dir = Path(__file__).resolve().parent
    image_folder = script_dir.parent / "images"
    image_path = image_folder / "stella1.jpg"

    img = Image.open(image_path).convert('RGB')

    orig_w, orig_h = img.size
    aspect = orig_h / orig_w
    studs_h = int(round(studs_width * aspect))

    print("processing image with background-foreground separation...")
    fg_pil, bg_pil, fg_mask = remove_background(img)

    fg_alpha_pil = make_difference_transparent(img, fg_pil)  # RGBA

    # -----------------------------
    # FOREGROUND: preserve alpha
    # -----------------------------
    fg_rgba = fg_alpha_pil.convert("RGBA")
    fg_rgb  = fg_rgba.convert("RGB")
    fg_a    = fg_rgba.getchannel("A")

    # Convert RGB → OpenCV BGR
    fg_bgr = cv2.cvtColor(np.array(fg_rgb), cv2.COLOR_RGB2BGR)

    # -----------------------------
    # FILTERING COMMENTED OUT FOR PIXEL-PERFECT
    # -----------------------------
    # fg_bilateral_bgr = cv2.bilateralFilter(fg_bgr, 15, 150, 150)
    # fg_gaus_blur_bgr = cv2.medianBlur(fg_bilateral_bgr, 25)
    # fg_filtered_rgb = cv2.cvtColor(fg_gaus_blur_bgr, cv2.COLOR_BGR2RGB)
    # fg_filtered_image = Image.fromarray(fg_filtered_rgb)
    fg_filtered_image = Image.fromarray(cv2.cvtColor(fg_bgr, cv2.COLOR_BGR2RGB))

    # -----------------------------
    # BACKGROUND (no alpha)
    # -----------------------------
    bg_np = np.array(bg_pil)
    bg_bgr = cv2.cvtColor(bg_np, cv2.COLOR_RGB2BGR)

    

    # -----------------------------
    # FILTERING COMMENTED OUT FOR PIXEL-PERFECT
    # -----------------------------
    # bg_bilateral_bgr = cv2.bilateralFilter(bg_bgr, 15, 150, 150)
    # bg_gaus_blur_bgr = cv2.medianBlur(bg_bilateral_bgr, 25)
    # bg_filtered_rgb = cv2.cvtColor(bg_gaus_blur_bgr, cv2.COLOR_BGR2RGB)
    # bg_filtered_image = Image.fromarray(bg_filtered_rgb)
    bg_filtered_image = Image.fromarray(cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2RGB))

    # Darken background slightly
    #bg_filtered_image = adjust_lightness_lab(bg_filtered_image, delta_L=-3)

    # Brighten foreground slightly
    fg_filtered_image = adjust_lightness_lab(fg_filtered_image, delta_L=+8)


    # -----------------------------
    # Produce LEGO mosaics
    # -----------------------------
    print("converting processed image to lego mosiac...")

    fg_out_img, idx = image_to_lego_mosaic(fg_filtered_image, studs_width)

    # -----------------------------
    # BACKGROUND SIMPLIFICATION
    # -----------------------------
    bg_out_img, bg_idx = image_to_lego_mosaic(bg_filtered_image, studs_width)

    #get number of colors from the background image
    unique, counts = np.unique(bg_idx, return_counts=True)
    #print(unique.size)
    numOfDesiredColors =  (int)((background_color_percent / 100) * unique.size)
    numOfDesiredColors = max(1, numOfDesiredColors)  #at least one color
    #print(background_color_percent)
    #print(numOfDesiredColors)
    #perform color quantization on background only to minimize color details
    bg_idx_simplified = simplify_background_lego(
        bg_idx,
        PALETTE_LAB,
        k=numOfDesiredColors   # adjust number of background colors here
    )

    # Rebuild simplified background image (pixel-perfect)
    bg_rgb_simplified = LEGO_PALETTE_RGB[bg_idx_simplified]
    bg_out_img = Image.fromarray(bg_rgb_simplified.astype(np.uint8))


    fg_alpha_resized = fg_a.resize((studs_width, studs_h), Image.NEAREST)

    fg_out_rgba = fg_out_img.convert("RGBA").resize((studs_width, studs_h), Image.NEAREST)
    fg_out_rgba.putalpha(fg_alpha_resized)

    bg_rgba = bg_out_img.convert("RGBA").resize(fg_out_rgba.size, Image.NEAREST)
    fg_out_rgba.show()
    bg_rgba.show()

    print("generating order list...")
    #TODO: send background and foregrount to instruction + order generation
    orderList = GenerateOrderList(fg_out_rgba, bg_rgba)
    #use json template from this to create order list: https://www.lego.com/en-us/pick-and-build/pick-a-brick?query=3024
    instructionSet = GenerateInstructions(fg_out_rgba, bg_rgba, orderList)
    print("finished order list!")

    print("Foreground RGBA:", fg_out_rgba.size, fg_out_rgba.mode)
    print("Background RGBA:", bg_rgba.size, bg_rgba.mode)
    composite = Image.alpha_composite(bg_rgba, fg_out_rgba)

    print("showing final output")
    composite.show()

    output_path = image_folder / f"{image_path.stem}_lego.png"
    fg_out_img.save(output_path)
    print(f"Saved mosaic to {output_path}")

