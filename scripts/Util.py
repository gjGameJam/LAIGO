import numpy as np


# LEGO palette in rgb
# got color codes from https://brickset.com/colours/family-Green
# got pieces from https://www.lego.com/en-us/pick-and-build/pick-a-brick?query=3024&selectedElement=6099189
LEGO_PALETTE_RGB_DICT = {
    (180, 0, 0): 302421,     # Bright Red
    (202, 76, 11): 6469084,   #  Reddish Orange
    (187, 128, 90): 6330584, # Nuegat
    (145, 80, 28): 6186012, # Dark Orange
    (255, 201, 149): 6357797, # Light Nougat
    (95, 49, 9): 4221744, # Reddish Brown
    (170, 125, 85): 6215606, #medium nuegat
    (214, 121, 35): 4524929, #bright orange
    (55, 33, 0): 6194729, # dark brown
    (252, 172, 0): 6073040, #flam yellowish orange
    (137, 125, 98): 4549436, #sand yellow
    (176, 160, 111): 4159553, #brick yellow
    (170, 127, 46): 6069887, #warm gold
    (250, 200, 10): 302424, #bright yellow
    (255, 236, 108): 6058014, #cool yellow
    (119, 119, 78): 6058245, #olive green
    (165, 202, 24): 4621557, #bright yellowish green
    (226, 249, 154): 6566896, #spring yellowish green
    (88, 171, 65): 6401817, #bright green
    (0, 133, 43): 302428, #dark green
    (0, 69, 26): 6055169, #earth green
    (112, 142, 124): 6099189, #sand green
    (211, 242, 234): 6058016, #aqua green
    (6, 157, 159): 6213778, #bright bluish green
    (104, 195, 226): 6097493, #medium azure
    (70, 155, 195): 6151664, #dark azure
    (27, 42, 52): 302426, #black
    (30, 90, 168): 302423, #bright blue
    (157, 195, 247): 6184484, #light royal blue
    (115, 150, 200): 4179826, #medium blue
    (112, 129, 154): 6257079, #sand blue
    (25, 50, 90): 4184108, #earth blue
    (68, 26, 145): 6231376, #medium lilac
    (160, 110, 185): 4619521, #medium lavender
    (205, 164, 222): 6099363, #lavender
    (138, 18, 168): 6096942, #bright reddish violet
    (211, 53, 157): 6217797, #bright purple
    (114, 0, 18): 4539114, #new dark red
    (244, 132, 124): 6258091, #vibrant coral
    (244, 244, 244): 302401, #white
    (150, 150, 150): 4211399, #medium stone grey
    (100, 100, 100): 4210719 #dark stone grey
}

#this website has the 1x1 lego plates:
# https://www.bricklink.com/v2/catalog/catalogitem.page?P=3024&name=Plate%201%20x%201&category=%5BPlate%5D#T=S&O={%22iconly%22:0}

def GetPaletteDict():
    return LEGO_PALETTE_RGB_DICT

def GetPaletteRGBArray():
    return np.array(list(LEGO_PALETTE_RGB_DICT.keys()), dtype=np.uint8)

