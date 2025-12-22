

#return list of all lego pieces needed for both layers
def GenerateOrderList(fg_out_rgba, bg_rgba):
    #need to get each non alpha pixel from foreground and background and return dictionary of pieces and counts
    print("creating order list...")
    #layer 1: background
    #here we loop over each pixel and get the color (should match lego palette) and add pieces to order list

    #layer 2: foreground
    #here we loop over each pixel and get the color (should match lego palette) and add pieces to order list

    # for i in range(2): #go over each layer
    #     #for each pixel in the layer
    #     for y in range(fg_out_rgba.height):
    #         for x in range(fg_out_rgba.width):
    #             #get pixel color
    #             #ignore alpha channel for foreground
    #             if (i == 1 and pixel[3] == 0):
    #                 continue
                
    #             #map pixel color to lego piece


    return 1

#helper function for determining piece from color
#all plates (1x1) are piece number 3024
def GetLegoPieceFromColor(rgb):
    #map rgb to lego piece id
    lego_piece_id = 3024  #1x1 plate
    return lego_piece_id