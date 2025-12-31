#take in ord list from MosiacToOrder to get pieces and counts
#take in foreground and background RGBA images (ignore alpha channel in foreground because it allows background to show through)
#build background first then foreground on top

#function to generate instructions for creating the mosiac frame (stretch)

#function to generate instrucctions for a RGBA image mosiac (pixel-perfect)

def GenerateInstructions(fg_out_rgba, bg_rgba, orderList):
    print("creating instructions...")
    #layer 0: baseplate
    # grid of interlocking 16x16s to ensure solid foundation
    
    #layer 1: background
    #here we loop over each row and column to get the color (should be contained in order list)
    #only place small number of pieces per instruction step to avoid overwhelming user

    #layer 2: foreground
    #same thing as background but ignore alpha channel to allow background to show through


    return 1

def GenerateBasePlateInstructions(fg_out_rgba, bg_rgba, orderList):
    print("creating baseplate instructions...")
    #first step is to get 16x16 baseplate out
    #add green connectors on right side and red connectors along bottom side
    #add green plates on right side and red plates along bottom side
    #add 2x2 plates on middle and corners for extra stability
    #add nail hooks on top edges (or direct middle) for hanging
    #add nail hook connectors to nail hooks to allow connection to frame

    #3 cases: 
    #case 1: in middle of mosiac(all connections)
    #case 2: top/left side of mosiac (no top/left connections)
    #case 3: bottom/right mosiac (no bottom/right connections)

    return 1