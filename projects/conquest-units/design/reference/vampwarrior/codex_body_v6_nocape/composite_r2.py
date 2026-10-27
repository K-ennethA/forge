# composite_r2.py: review-round-1 fixes.
#  * all views: never take the edit where the BASE pixel is skin (skin was never under the cape) -> removes the front thigh
#    smears; the base skin is restored.
#  * side_left / back: Codex round-2 edits (edited2_*.png, made from the round-1 composites) pasted only inside the round-1
#    CAPE mask (+6 px) and, on the side, the burgundy lining slivers the round-1 mask missed.
#  * the saved cape masks / outlines gain those burgundy slivers (they are visible cape lining).
#  * reports the final diff vs the base outside the union of all masks used.
import json, os, sys
sys.setrecursionlimit(100000)
import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage as ndi

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import importlib.util
spec = importlib.util.spec_from_file_location("cm_fns", os.path.join(here, "cape_masks_fns.py")); cm = importlib.util.module_from_spec(spec); spec.loader.exec_module(cm)
OUT = {"front": "body_front.png", "side_left": "aligned_body_side_left.png", "back": "aligned_body_back.png"}
COLLAR = json.load(open(os.path.join(here, "collar_protect.json")))
EXTRA = json.load(open(os.path.join(here, "cape_extra.json")))
R1 = os.path.join(here, "round1")
L = lambda p: np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
report = json.load(open(os.path.join(R1, "edit_diff.json"))); outlines = json.load(open(os.path.join(R1, "cape_outlines.json")))
for v, outname in OUT.items():
    base = L(os.path.join(here, "base_%s.png" % v)); r1 = L(os.path.join(R1, outname))
    prot = Image.new("L", (base.shape[1], base.shape[0])); ImageDraw.Draw(prot).polygon([tuple(p) for p in COLLAR[v]], fill=255); prot = np.asarray(prot) > 0
    cape = np.asarray(Image.open(os.path.join(R1, "cape_mask_%s.png" % v))) > 0
    w1 = np.asarray(Image.open(os.path.join(R1, "composite_mask_%s.png" % v))).astype(np.float32) / 255
    skin = (base[..., 0] > 140) & (base.mean(2) > 135) & (base[..., 0] - base[..., 2] > 4)
    skin = ndi.binary_opening(skin, iterations=1) & ~cape
    cur = r1.copy(); wu = w1.copy()
    if v in ("side_left", "back"):
        e2 = L(os.path.join(here, "edited2_%s.png" % v))
        R = ndi.binary_dilation(cape, iterations=6)
        if v == "side_left":
            burg = (base[..., 0] > 70) & (base[..., 0] - base[..., 1] > 25) & (base[..., 0] - base[..., 2] > 15)
            burg &= ~prot; burg[EXTRA[v]["clip_below"]:] = False
            burg = ndi.binary_opening(burg, iterations=1)
            lab, n = ndi.label(burg); sz = ndi.sum(burg, lab, range(1, n + 1)); burg = np.isin(lab, 1 + np.nonzero(sz >= 60)[0])
            cape = cape | burg
            R |= ndi.binary_dilation(burg, iterations=4)
        R &= ~prot & ~skin
        w = ndi.gaussian_filter(R.astype(np.float32), 1.5); w[ndi.binary_erosion(R, iterations=2)] = 1; w[prot | skin] = 0
        cur = e2 * w[..., None] + r1 * (1 - w[..., None]); wu = np.maximum(wu, w)
    if v == "side_left":
        # leftover burgundy lining (dark red) in the composite where the round-2 edit is not burgundy -> take the edit
        def dred(x): return (x[..., 0] < 140) & (x[..., 0] > 45) & (x[..., 0] - x[..., 1] > 20) & (x[..., 0] - x[..., 2] > 12)
        left = dred(cur) & ~dred(e2) & ~prot; left[:300] = False; left[EXTRA[v]["clip_below"]:] = False
        left = ndi.binary_dilation(left, iterations=2) & ~prot & ~skin
        cur[left] = e2[left]; wu[left] = 1.0; cape |= left
        print("side leftover burgundy px replaced", int(left.sum()))
    # skin rule (all views): restore the base where the base is skin
    s = ndi.gaussian_filter(skin.astype(np.float32), 0.8); s[skin] = 1
    cur = base * s[..., None] + cur * (1 - s[..., None])
    Image.fromarray(np.clip(cur + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(here, outname))
    Image.fromarray((np.clip(wu, 0, 1) * 255).astype(np.uint8)).save(os.path.join(here, "composite_mask_%s.png" % v))
    lab, n = ndi.label(cape, structure=np.ones((3, 3))); sz = ndi.sum(cape, lab, range(1, n + 1)); cape = np.isin(lab, 1 + np.nonzero(sz >= 300)[0])
    Image.fromarray((cape * 255).astype(np.uint8)).save(os.path.join(here, "cape_mask_%s.png" % v))
    fin = L(os.path.join(here, outname)); dc = np.abs(fin - base).mean(2); outside = wu < 0.01
    report[v].update({"r2_final_MAD_outside_mask": round(float(dc[outside].mean()), 4), "r2_final_max_outside_mask": float(dc[outside].max()),
                      "r2_mask_pct": round(100 * float((~outside).mean()), 2), "r2_cape_mask_pct": round(100 * float(cape.mean()), 2),
                      "r2_final_whole_image_MAD": round(float(dc.mean()), 2)})
    comps = []
    lab, n = ndi.label(cape, structure=np.ones((3, 3)))
    for i in range(1, n + 1):
        c = lab == i; ys, xs = np.nonzero(c); holes = []
        hl, hn = ndi.label(ndi.binary_fill_holes(c) & ~c)
        for j in range(1, hn + 1):
            if (hl == j).sum() >= 30: holes.append(cm.dp_closed(cm.moore_trace(hl == j), 1.0))
        comps.append({"area_px": int(c.sum()), "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                      "outer": cm.dp_closed(cm.moore_trace(c), 1.0), "holes": holes})
    comps.sort(key=lambda c: -c["area_px"]); outlines[v]["components"] = comps
    print(v, {k: report[v][k] for k in report[v] if k.startswith("r2_")}, [(c["area_px"], len(c["outer"])) for c in comps])
ImageOps.mirror(Image.open(os.path.join(here, "aligned_body_side_left.png")).convert("RGB")).save(os.path.join(here, "aligned_body_side_right.png"))
json.dump(report, open(os.path.join(here, "edit_diff.json"), "w"), indent=1)
json.dump(outlines, open(os.path.join(here, "cape_outlines.json"), "w"))
