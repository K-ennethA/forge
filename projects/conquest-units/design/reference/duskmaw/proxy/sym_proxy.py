"""duskmaw base: symmetric-head 4-view proxy. front/back head rows mirrored about the sheet axis (front: image-left half,
back: image-right half - the half that carries the side spike), side as `right`, side mirrored as `left`."""
import os, sys, json, numpy as np
ROOT = r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
sys.path.insert(0, os.path.join(ROOT, "improve"))
import sheet_proxy as SP
from PIL import Image
UNIT = sys.argv[2]; FY, BY = int(sys.argv[3]), int(sys.argv[4])
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
intake, idir = SP.load_intake(os.path.join(ROOT, "design/reference/" + UNIT + "/intake.json"))
rgb = np.asarray(Image.open(SP.sheet_path(intake)).convert("RGB")).copy()
masks = SP.view_masks(intake, idir, rgb)
SP.clip_below_floor(intake, masks)
def sym(view, y_end, keep, half=150):
    ax = int(round(intake["views"][view]["axis_x_px"]))
    m = masks[view]
    for y in range(0, y_end):
        for dx in range(1, half):
            src, dst = (ax - dx, ax + dx) if keep == "left" else (ax + dx, ax - dx)
            rgb[y, dst] = rgb[y, src]; m[y, dst] = m[y, src]
sym("front", FY, "left"); sym("back", BY, "right")
Image.fromarray(rgb).save(os.path.join(out, "sheet_symhead.png"))
cuts, frame = SP.cut_views(intake, masks, rgb, ["front", "back", "side"], out)
mv = {"front": cuts["front"], "back": cuts["back"], "right": cuts["side"],
      "left": SP.mirror_png(cuts["side"], os.path.join(out, "view_side_mirrored.png"))}
glb = os.path.join(out, UNIT + "_symhead.glb")
res = SP.generate(mv, glb, 56, "direct")
print("GENERATED", glb, {k: res.get(k) for k in ("via", "seed", "duration_ms")})
