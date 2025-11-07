# ============================================
# LEGO Mosaic Generator (KMeans)
# ============================================
# - KMeans compresses the color space
# - Snaps colors to LEGO palette for final mosaic
# ============================================

from PIL import Image
import numpy as np
from sklearn.cluster import KMeans
from scipy.spatial import distance
from pathlib import Path
import random
import sys

# -------------------------------
# STEP 0: CLI Argument Handling
# -------------------------------
# Usage:
#   python picToMosiac.py [mosaic_size] [num_colors]
# Example:
#   python picToMosiac.py 64 12

if len(sys.argv) < 2:
    print("Usage: python picToMosiac.py [mosaic_size] [num_colors]")
    sys.exit(1)

mosaic_size = int(sys.argv[1]) if len(sys.argv) > 1 else 64
K = int(sys.argv[2]) if len(sys.argv) > 2 else 12

print("getting paths")
# Paths
script_dir = Path(__file__).resolve().parent
print(script_dir)
image_path = script_dir.parent / "images" / "labrador.jpg"

# -------------------------------
# STEP 1: Load and Resize Image
# -------------------------------
print(f"Loading image: {image_path}")
img = Image.open(image_path).convert("RGB")
target_size = (mosaic_size, mosaic_size)
img = img.resize(target_size, Image.Resampling.LANCZOS)

pixels = np.array(img).reshape(-1, 3)
n_pixels = len(pixels)
print(f"Image resized to {target_size}, total pixels = {n_pixels}")

# -------------------------------
# STEP 2: KMeans Color Compression
# -------------------------------
print(f"Running KMeans with K={K} to compress color space...")
kmeans = KMeans(n_clusters=K, random_state=0, n_init="auto")
kmeans.fit(pixels)

centroids = kmeans.cluster_centers_
labels = kmeans.labels_
print("KMeans complete. Approximate colors identified.")

# -------------------------------
# STEP 3: Snap to LEGO Palette
# -------------------------------
# got color codes from https://rebrickable.com/colors/
lego_colors = np.array([
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


# Vectorized nearest-color snapping
lego_distances = distance.cdist(centroids, lego_colors)
nearest_indices = np.argmin(lego_distances, axis=1)
snapped_colors = lego_colors[nearest_indices]

# -------------------------------
# STEP 4: Reconstruct Full Mosaic
# -------------------------------
# Assign each pixel to its cluster’s snapped LEGO color
lego_pixels = np.zeros_like(pixels)
for i in range(K):
    lego_pixels[labels == i] = snapped_colors[i]

# Reshape to mosaic dimensions
lego_image = lego_pixels.reshape(mosaic_size, mosaic_size, 3).astype(np.uint8)
lego_mosaic = Image.fromarray(lego_image)

# Optional upscale for viewing
lego_mosaic_upscaled = lego_mosaic.resize(img.size, Image.NEAREST)

output_path = image_path.parent / "lego_mosaic_kmeans_output.png"
lego_mosaic_upscaled.save(output_path)
lego_mosaic_upscaled.show()

print(f"✅ LEGO mosaic generated and saved as {output_path}")
