from VisualMaker import generate_baseplate_setup
#take in ord list from MosiacToOrder to get pieces and counts
#take in foreground and background RGBA images (ignore alpha channel in foreground because it allows background to show through)
#build background first then foreground on top

#function to generate instructions for creating the mosiac frame (stretch)

#function to generate instrucctions for a RGBA image mosiac (pixel-perfect)

def GenerateInstructions(fg_out_rgba, bg_rgba, orderList):
    step = 1
    fg_w, fg_h = fg_out_rgba.size
    blockWidth = (int)(fg_w / 16)
    blockHeight = (int)(fg_h / 16)
    #divide pixel width and height by 16 (length of backplate block) to determine row and column max
    print("creating instructions for width " + str(blockWidth) + " and height " + str(blockHeight))
    #for each baseplate in the mosiac:
    for blockW in range(0, blockWidth):
        for blockH in reversed(range(0, blockHeight)):
            #print("step " + str(step))
            #layer 0: baseplate
            # grid of interlocking 16x16s to ensure solid foundation
            step = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step)
            #layer 1: background
            #here we loop over each row and column to get the color (should be contained in order list)
            #only place small number of pieces per instruction step to avoid overwhelming user

            #layer 2: foreground
            #same thing as background but ignore alpha channel to allow background to show through


    return 1

def GenerateBasePlateInstructions(blockRow, rowMax, blockCol, colMax, step):
    
    #case 0: red and green connectors 
    #case 1: red connectors no green (right most column)
    #case 2: green connectors no red (bottom row)
    #case 3: no connectors (bottom right corner only)
    case = 0
    if 0 == blockRow and colMax - 1 == blockCol:
        case = 3
    elif colMax - 1 == blockCol:
        case = 1
    elif 0 == blockRow:
        case = 2

    print("creating baseplate instructions for width " + str(blockCol) + " and height " + str(blockRow) + " for max width " + str(colMax) + " and max height " + str(rowMax) + " case " + str(case))
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
    return generate_baseplate_setup(step, case)

