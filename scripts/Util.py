import numpy as np


# LEGO palette in rgb
# got color codes from https://rebrickable.com/colors/
#TODO: convert this to dictionary with color rgb and LegoBrickID
LEGO_PALETTE_RGB_DICT = {
    (114, 20, 15): 0,     # Dark Red
    (255, 105, 143): 1,   # Coral
    (228, 173, 200): 2,   # Bright Pink
    (200, 112, 160): 3,   # Dark Pink
    (146, 57, 120): 4,    # Magenta
    (172, 120, 186): 5,   # Medium Lavender
    (225, 213, 237): 6,   # Lavender
    (63, 54, 145): 7,     # Dark Purple / Medium Lilac
    (96, 116, 161): 8,    # Sand Blue
    (0, 85, 191): 9,      # Blue
    (90, 147, 219): 10,   # Medium Blue
    (10, 52, 99): 11,     # Dark Blue / Earth Blue
    (159, 195, 233): 12,  # Bright Light Blue
    (7, 139, 201): 13,    # Dark Azure
    (54, 174, 191): 14,   # Medium Azure
    (0, 143, 155): 15,    # Dark Turquoise
    (173, 195, 192): 16,  # Light Aqua
    (179, 215, 209): 17,  # Aqua
    (24, 70, 50): 18,     # Dark Green / Earth Green
    (160, 188, 172): 19,  # Sand Green
    (35, 120, 65): 20,    # Green
    (75, 159, 74): 21,    # Bright Green
    (187, 233, 11): 22,   # Lime
    (223, 238, 165): 23,  # Yellowish Green
    (155, 154, 90): 24,   # Olive Green
    (235, 216, 0): 25,    # Vibrant Yellow
    (255, 240, 58): 26,   # Bright Light Yellow
    (248, 187, 61): 27,   # Bright Light Orange
    (244, 205, 47): 28,   # Bright Yellow
    (228, 205, 158): 29,  # Tan
    (149, 138, 115): 30,  # Dark Tan
    (205, 163, 115): 31,  # Warm Tan / Medium Tan
    (165, 125, 85): 32,   # Medium Nougat
    (117, 89, 69): 33,    # Medium Brown
    (88, 42, 18): 34,     # Reddish Brown
    (94, 92, 51): 35,     # Olive Green (duplicate variant)
    (145, 76, 12): 36,    # Reddish Orange
    (94, 63, 51): 37,     # Umber Brown
    (201, 26, 9): 38,     # Red
    (248, 138, 24): 39,   # Orange
    (169, 85, 0): 40,     # Dark Orange
    (214, 158, 71): 41,   # Ochre Yellow
    (246, 215, 179): 42,  # Light Nougat
    (208, 145, 104): 43,  # Nougat
    (145, 92, 60): 44,    # Sienna Brown
    (53, 33, 0): 45,      # Dark Brown
    (160, 165, 169): 46,  # Light Bluish Gray
    (108, 110, 104): 47,  # Dark Bluish Gray
    (5, 19, 29): 48,      # Black
    (255, 255, 255): 49,  # White
}

#this website has the 1x1 lego plates:
# https://www.bricklink.com/v2/catalog/catalogitem.page?P=3024&name=Plate%201%20x%201&category=%5BPlate%5D#T=S&O={%22iconly%22:0}

def GetPaletteDict():
    return LEGO_PALETTE_RGB_DICT

def GetPaletteRGBArray():
    return np.array(list(LEGO_PALETTE_RGB_DICT.keys()), dtype=np.uint8)

