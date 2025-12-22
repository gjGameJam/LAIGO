import numpy as np


# LEGO palette in rgb
# got color codes from https://rebrickable.com/colors/
#TODO: convert this to dictionary with color rgb and LegoBrickID
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

#this website has the 1x1 lego plates: https://www.bricklink.com/v2/catalog/catalogitem.page?P=3024&name=Plate%201%20x%201&category=%5BPlate%5D#T=S&O={%22iconly%22:0}

def GetPalette():
    return LEGO_PALETTE_RGB