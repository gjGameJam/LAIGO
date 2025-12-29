#take in ord list from MosiacToOrder to get pieces and counts
#take in foreground and background RGBA images (ignore alpha channel in foreground because it allows background to show through)
#build background first then foreground on top

#function to generate instructions for creating the mosiac frame (stretch)

#function to generate instrucctions for a RGBA image mosiac (pixel-perfect)

def GenerateInstructions(fg_out_rgba, bg_rgba, orderList):
    print("creating instructions...")
    #layer 0: baseplate
    # two layers of baseplates to ensure solid foundation
    
    #layer 1: background
    #here we loop over each row and column to get the color (should be contained in order list)
    #only place small number of pieces per instruction step to avoid overwhelming user

    #layer 2: foreground
    #same thing as background but ignore alpha channel to allow background to show through


    return 1