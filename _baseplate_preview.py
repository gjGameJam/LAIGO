"""Throwaway harness: render draw_baseplate_bottom in isolation for fast visual
iteration on the back-of-baseplate 3D corner/rail work. Delete when done.

Run from project root:
    .venv\\Scripts\\python.exe _baseplate_preview.py
"""
from PIL import Image, ImageDraw
from scripts.VisualMaker import draw_baseplate_bottom

for case in (-1, 0):
    img = Image.new("RGBA", (612, 792), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw_baseplate_bottom(draw, case=case)
    out = f"_baseplate_preview_case{case}.png"
    img.convert("RGB").save(out, format="PNG")
    crop = img.convert("RGB").crop((20, 250, 590, 560)).resize((1140, 620))
    crop.save(f"_baseplate_zoom_case{case}.png", format="PNG")
    if case == -1:
        img.convert("RGB").crop((30, 350, 210, 470)).resize((720, 480)).save(
            "_baseplate_left_corner.png", format="PNG")
        img.convert("RGB").crop((410, 350, 590, 470)).resize((720, 480)).save(
            "_baseplate_right_corner.png", format="PNG")
        # ultra-tight crop on the left corner -> front-left row junction
        img.convert("RGB").crop((70, 390, 170, 455)).resize((800, 520)).save(
            "_baseplate_junction.png", format="PNG")
        # reference overlay: plate edges + left-pad vertices, then zoom left corner
        ref = img.convert("RGB").copy()
        d2 = ImageDraw.Draw(ref)
        L=(50,399.5); FRONT=(290,519.5); RIGHT=(530,399.5); BACK=(290,279.5)
        d2.line([L, FRONT], fill=(255,0,0), width=1)      # front-left edge (red)
        d2.line([RIGHT, FRONT], fill=(255,128,0), width=1)# front-right edge (orange)
        for name,p in [("bl0",(50,399.5)),("bl1",(80,384.5)),("bl2",(110,399.5)),("bl3",(80,414.5))]:
            d2.ellipse([p[0]-2,p[1]-2,p[0]+2,p[1]+2], fill=(0,255,0))
        # blue = new triangle tip (x=bl2, y=bl3) -- should sit level with bl3 on the front edge
        tip=(110, 414.5)
        d2.ellipse([tip[0]-3,tip[1]-3,tip[0]+3,tip[1]+3], fill=(0,128,255))
        ref.crop((40, 380, 200, 470)).resize((800, 450)).save("_baseplate_ref.png", format="PNG")
    print("wrote", out)
