# ============================================
# Color Quantization (Mean Color)
# ============================================
# - Keeps original image size
# - Uses KMeans color quantization
# - Assigns each cluster its mean color
# ============================================

from PIL import Image
import numpy as np
from sklearn.cluster import KMeans
from pathlib import Path
import sys

# -------------------------------
# STEP 0: CLI Argument Handling
# -------------------------------
# Usage:
#   python colorQuant.py [num_colors]
# Example:
#   python colorQuant.py 12

if len(sys.argv) < 2:
    print("Usage: python colorQuant.py [num_colors]")
    sys.exit(1)

K = int(sys.argv[1])  # number of clusters/colors

# -------------------------------
# STEP 1: Load Image (no resizing)
# -------------------------------
script_dir = Path(__file__).resolve().parent
image_path = script_dir.parent / "images" / "labrador.jpg"

img = Image.open(image_path).convert("RGB")
width, height = img.size
print(f"Loaded image: {image_path} ({width}x{height})")

pixels = np.array(img).reshape(-1, 3)
print(f"Total pixels: {len(pixels)}")

# -------------------------------
# STEP 2: KMeans Color Compression
# -------------------------------
print(f"Running KMeans (K={K})...")
kmeans = KMeans(n_clusters=K, random_state=0, n_init="auto")
kmeans.fit(pixels)

labels = kmeans.labels_
print("KMeans complete. Clusters assigned.")

# -------------------------------
# STEP 3: Compute Mean Color per Cluster
# -------------------------------
mean_colors = np.zeros((K, 3))
for i in range(K):
    cluster_pixels = pixels[labels == i]
    if len(cluster_pixels) > 0:
        mean_colors[i] = cluster_pixels.mean(axis=0)
    else:
        mean_colors[i] = [0, 0, 0]  # fallback

# -------------------------------
# STEP 4: Rebuild Quantized Image
# -------------------------------
quantized_pixels = np.zeros_like(pixels)
for i in range(K):
    quantized_pixels[labels == i] = mean_colors[i]

quantized_image = quantized_pixels.reshape(height, width, 3).astype(np.uint8)
output_image = Image.fromarray(quantized_image)

# -------------------------------
# STEP 5: Save Result
# -------------------------------
output_path = image_path.parent / f"color_quantized_K{K}.png"
output_image.save(output_path)
output_image.show()

print(f"✅ Color-quantized image saved to {output_path}")
