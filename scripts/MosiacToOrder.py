from collections import defaultdict
import numpy as np
from Util import GetPaletteDict
from collections import Counter
from pathlib import Path
import shutil
from Util import GetOutputPathDir, SaveDictAsJsonsOptimized

PALETTE_DICT = GetPaletteDict()

def get_order_lists_file_path():
    return GetOutputPathDir() / "OrderLists"

def empty_order_list_folder():
    folder = Path(get_order_lists_file_path())
    shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)

# saves list of all lego pieces needed for both layers
def GenerateOrderList(fg_out_rgba, bg_rgba):
    """
    fg_out_rgba : PIL Image, mode RGBA
    bg_rgba     : PIL Image, mode RGBA (or RGB, alpha ignored)
    """
    print("clearing previous order lists...")
    empty_order_list_folder()
    print("creating order list...")
    # -----------------
    # Layer 0: Base Layer (to place all plates on)
    # -----------------
    desiredSize = fg_out_rgba.size
    #order = GetBaseplatesForSize(desiredSize[0], desiredSize[1])
    order = defaultdict(int, GetBaseplatesForSize(desiredSize[0], desiredSize[1]))


    # -----------------
    # Layer 1: Background
    # -----------------
    bg_pixels = bg_rgba.load()
    for y in range(bg_rgba.height):
        for x in range(bg_rgba.width):
            rgb = bg_pixels[x, y][:3]
            piece_id = GetLegoPieceFromColor(rgb, PALETTE_DICT)
            order[piece_id] += 1


    # -----------------
    # Layer 2: Foreground
    # -----------------
    fg_pixels = fg_out_rgba.load()
    for y in range(fg_out_rgba.height):
        for x in range(fg_out_rgba.width):
            r, g, b, a = fg_pixels[x, y]
            if a == 0:
                continue
            piece_id = GetLegoPieceFromColor((r, g, b), PALETTE_DICT)
            order[piece_id] += 1


    print("returning order list...")
    frameList = GetFrameForSize(fg_out_rgba.width, fg_out_rgba.height)
    finalList = dict(Counter(order) + Counter(frameList))

    print("finished order list!:", finalList)
    #save json of order list
    output_json_path = get_order_lists_file_path() / f"order_list.json"
    SaveDictAsJsonsOptimized(finalList, output_json_path)
    print("Sum of all pieces:", sum(finalList.values()))



#helper function for determining piece from color
#all plates (1x1) are piece number 3024
#returns lego piece number for given rgb color via dictionary lookup
def GetLegoPieceFromColor(rgb, palette_dict=PALETTE_DICT):

    rgb = tuple(int(c) for c in rgb)

    try:
        return palette_dict[rgb]
    except KeyError:
        raise ValueError(f"RGB color {rgb} not found in LEGO palette")
   

# plate_id_by_size = {
#     (32, 32): 6139364,
#     (16, 32): 4269651,
#     (16, 16): 6004927,
#     (8, 16): 4654613,
#     (8, 8): 4210802,
#     (6, 16): 4226358,
#     (6, 14): 4210720,
#     (6, 12): 4256149,
#     (6, 10): 4211114,
#     (6, 8): 4210794,
#     (6, 6): 4211134,
#     (4, 12): 4210706,
#     (4, 10): 4211122,
#     (4, 8): 4211061,
#     (4, 6): 4211115,
#     (4, 4): 4243831,
#     (3, 3): 6039176,
#     (2, 16): 4210796,
#     (2, 14): 6000970,
#     (2, 12): 4211067,
#     (2, 10): 4210678,
#     (2, 8): 4210997,
#     (2, 4): 4211065,
#     (2, 3): 4211043,
#     (2, 2): 4211094,
#     (1, 12): 6133611,
#     (1, 10): 4257526,
#     (1, 8): 4210998,
#     (1, 6): 4211056,
#     (1, 5): 6413109,
#     (1, 4): 4211001,
#     (1, 3): 4211133,
#     (1, 2): 4211063,
#     (1, 1): 4210719,
# }




# this function has input of width and height in studs
# returns a dictionary of id and quantity for lego pieces required for the back plate grid
def GetBaseplatesForSize(width, height):


    blockWidth = width / 16
    blockHeight = height / 16


    #calculate quantities
    numOfBlocks = int(blockWidth * blockHeight)
    numOfGreenConnectors = (int)((2 * (blockWidth - 1)) * blockHeight)
    numOfGreenPlates = (int)((blockWidth - 1) * blockHeight)
    numOfRedConnectors = (int)((2 * (blockHeight - 1)) * blockWidth)
    numOfRedPlates = (int)((blockHeight - 1) * blockWidth)
    twoxtwoPlates = numOfBlocks * 5
    nailHooks = min(numOfBlocks, 2)
    nailHookConnectors = nailHooks * 2


    blockID = 6306097
    nailHookID = 6302094
    nailHookConnectorID = 6279875
    greenConnectorID = 6526672
    redConnectorID = 6347789
    greenPlateID = 4621548
    redPlateID = 379521
    twoxtwoPlateID = 4211094
    # convert size counts → piece ID counts
    baseplates = defaultdict(int)
    baseplates[blockID] = numOfBlocks
    baseplates[nailHookID] = nailHooks
    baseplates[nailHookConnectorID] = nailHookConnectors
    baseplates[greenConnectorID] = numOfGreenConnectors
    baseplates[redConnectorID] = numOfRedConnectors
    baseplates[greenPlateID] = numOfGreenPlates
    baseplates[redPlateID] = numOfRedPlates
    baseplates[twoxtwoPlateID] = twoxtwoPlates


    return dict(baseplates)




def GetFrameForSize(width, height):


    blockWidth = (int)(width / 16)
    blockHeight = (int)(height / 16)


    num_of_corners = 4
    #frame_width = width + 2 #will be two wider because it will be the border
    #frame_height = height + 2 #will be two taller because it will be the border


    #calculate quantities
    cornerBlocks = num_of_corners
    cornerPlates = num_of_corners
    onexoneBricks = num_of_corners * 2
    twoxoneBricksWithAxleHole = (int)(blockWidth * 4) + (int)(blockHeight * 4) #two sides and two per block = *4
    axlePegs = twoxoneBricksWithAxleHole #1:1 with the holes
    eightxoneBricks = (int)(twoxoneBricksWithAxleHole / 2) # these go inbetween two axle hole blocks
    fourxoneBricks = ((blockWidth - 1) * 2) + ((blockHeight - 1) * 2) #connect each non corner blocks
    tenxtwoPlates = eightxoneBricks #will always be below the 8x1
    sixxtwoPlates = fourxoneBricks #will always be below the 4x1
    #Above handles the bottom level
    sixteenxoneBricks = (blockWidth * 2) + (blockHeight * 2) #1:1 with perimeter in blocks
    onexoneBricks += num_of_corners #to handle corners (not handled by 16 length blocks)
    #above handles the middle level
    thinCornerPlates = num_of_corners

    flatonexfourPlates = 8 * (blockWidth + blockHeight) - 4 # -4 to handle corners only needing 1 instead of 2



    #each corner needs 1 corner plate, 2 1x1x1 blocks, 1 corner block
    #STRETCH: consider passing in desired frame color to select different piece IDs
    cornerBlockID = 235726
    cornerPlateID = 6483102
    onexoneBrickID = 300526
    twoxoneBrickWithAxleHoleID = 6178922
    axlePegID = 4109810
    eightxoneBrickID = 300826
    fourxoneBrickID = 301026
    tenxtwoPlateID = 383226
    sixxtwoPlateID = 379526
    sixteenxoneBrickID = 246526
    thinCornerPlateID = 6439175
    flatonexfourPlateID = 243126


    #initialize dictionary
    baseplates = defaultdict(int)
    baseplates[cornerBlockID] = cornerBlocks
    baseplates[cornerPlateID] = cornerPlates
    baseplates[onexoneBrickID] = onexoneBricks
    baseplates[twoxoneBrickWithAxleHoleID] = twoxoneBricksWithAxleHole
    baseplates[axlePegID] = axlePegs
    baseplates[eightxoneBrickID] = eightxoneBricks
    baseplates[fourxoneBrickID] = fourxoneBricks
    baseplates[tenxtwoPlateID] = tenxtwoPlates
    baseplates[sixxtwoPlateID] = sixxtwoPlates
    baseplates[sixteenxoneBrickID] = sixteenxoneBricks
    baseplates[thinCornerPlateID] = thinCornerPlates
    baseplates[flatonexfourPlateID] = flatonexfourPlates
   


    #return dictionary of piece id and quantity and keys and values
    return dict(baseplates)


