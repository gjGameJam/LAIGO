import numpy as np
from pathlib import Path 
from collections import defaultdict
import json
from math import ceil
from dotenv import load_dotenv
import os
from logger import logger

#only print if environment variable DEBUG is set to true, otherwise be silent (for cleaner multiprocessing logs)
DEBUG = bool(os.getenv("DEBUG"))

def load_project_env():
    """
    Load .env file from project root.
    Assumes this file is in scripts/ and .env is in project root.
    """
    # Project root is parent of scripts/
    project_root = Path(__file__).parent.parent.resolve()
    env_path = project_root / ".env"

    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        log_info(f".env loaded from {env_path}")
    else:
        log_error(f"No .env found at {env_path}, using defaults or system environment")

    # Optional: return project_root for convenience
    return project_root

def log_info(message):
    logger.info(message)

def log_debug(message):
    logger.debug(message)

def log_error(message):
    logger.error(message)

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

# def SaveDictAsJson(data: dict, output_path: Path):
#     if not isinstance(data, dict):
#         raise TypeError(f"SaveDictAsJson expected dict, got {type(data)}")

#     if not output_path.parent.exists():
#         raise FileNotFoundError(f"Output directory does not exist: {output_path.parent}")

#     try:
#         order_json = DictToJson(data)
#     except Exception as e:
#         raise RuntimeError("Failed to serialize dictionary to JSON") from e

#     try:
#         with open(output_path, "w", encoding="utf-8") as f:
#             f.write(order_json)
#     except Exception as e:
#         raise IOError(f"Failed to write JSON to {output_path}") from e


#input data will be dictionary of int elementId (piece number) and int quantity (key and value)
#this function should return output of json string with following format
def SaveDictAsJsonsOptimized(order_dict, output_path: Path, max_per_item: int = 999):
    """
    Split order_dict values into chunks <= max_per_item and write multiple JSONs.
    
    Optimizations:
    - Pre-calculates max_parts to avoid building intermediate chunk lists.
    - Implements a "Fast Path" for the common single-file case.
    - Uses a single pass per file to build the JSON structure.
    """
    if not isinstance(order_dict, (dict, defaultdict)):
        raise TypeError("order_dict must be a dict or defaultdict")

    if not isinstance(output_path, Path):
        output_path = Path(output_path)

    if max_per_item <= 0:
        raise ValueError("max_per_item must be positive")

    # Step 0: Filter out non-positive quantities immediately to save CPU later
    # This prevents 'empty' entries in the JSON.
    active_items = {str(k): v for k, v in order_dict.items() if v > 0}

    if not active_items:
        log_info("No items to write.")
        return

    # Step 1: Determine if we need more than one file
    max_qty = max(active_items.values())
    max_parts = ceil(max_qty / max_per_item)

    # Ensure destination exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Case A: Fast Path (Single File) ---
    if max_parts <= 1:
        file_items = [{"elementId": k, "quantity": v} for k, v in active_items.items()]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(file_items, f, indent=4)
        log_debug(f"Saved {len(file_items)} items to {output_path}")
        return

    # --- Case B: Multi-file Math Split ---
    for file_index in range(max_parts):
        file_items = []
        offset = file_index * max_per_item
        
        for element_id, total_qty in active_items.items():
            # Calculate how much of this specific element belongs in this file
            # Logic: Subtract the offset (already sent) and cap at max_per_item
            qty_for_this_file = min(max(total_qty - offset, 0), max_per_item)
            
            if qty_for_this_file > 0:
                file_items.append({
                    "elementId": element_id, 
                    "quantity": qty_for_this_file
                })

        # Determine path (file.json, file_1.json, file_2.json, etc.)
        if file_index == 0:
            out_path = output_path
        else:
            out_path = output_path.with_name(f"{output_path.stem}_{file_index}{output_path.suffix}")

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(file_items, f, indent=4)

        log_debug(f"Saved {len(file_items)} items to {out_path}")

def GetOutputPathDir():
    return Path(__file__).resolve().parent.parent / "outputs"


