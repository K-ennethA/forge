# outlines.py: cape_mask_<view>.png -> cape_outlines.json (Moore-traced outer ring + hole rings per component, Douglas-Peucker 1 px)
import json, os, sys
sys.setrecursionlimit(100000)
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
import importlib.util
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cm_fns", os.path.join(here, "cape_masks_fns.py")); cm = importlib.util.module_from_spec(spec); spec.loader.exec_module(cm)
out = {"_note": "CAPE silhouettes per view in image px (x right, y down) on the 1024x1536 reference canvas = the pixels removed as the cape "
        "(cape_mask_<view>.png; the collar is not included). Per component: outer ring + hole rings, Moore-traced pixel centres "
        "simplified with Douglas-Peucker eps 1 px. side_left is the aligned left profile (right profile = mirror x -> 1023 - x). "
        "Front/back show where the cape is visible, not its full extent behind the body."}
for v in ("front", "side_left", "back"):
    m = np.asarray(Image.open(os.path.join(here, "cape_mask_%s.png" % v))) > 0
    lab, n = ndi.label(m, structure=np.ones((3, 3))); sz = ndi.sum(m, lab, range(1, n + 1)); m = np.isin(lab, 1 + np.nonzero(sz >= 100)[0])
    Image.fromarray((m * 255).astype(np.uint8)).save(os.path.join(here, "cape_mask_%s.png" % v))
    comps = []
    lab, n = ndi.label(m, structure=np.ones((3, 3)))
    for i in range(1, n + 1):
        c = lab == i; ys, xs = np.nonzero(c); holes = []
        hl, hn = ndi.label(ndi.binary_fill_holes(c) & ~c)
        for j in range(1, hn + 1):
            if (hl == j).sum() >= 30: holes.append(cm.dp_closed(cm.moore_trace(hl == j), 1.0))
        comps.append({"area_px": int(c.sum()), "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                      "outer": cm.dp_closed(cm.moore_trace(c), 1.0), "holes": holes})
    comps.sort(key=lambda c: -c["area_px"])
    out[v] = {"mask": "cape_mask_%s.png" % v, "size": [m.shape[1], m.shape[0]], "area_px": int(m.sum()), "components": comps}
    print(v, int(m.sum()), [(c["area_px"], len(c["outer"]), len(c["holes"])) for c in comps])
json.dump(out, open(os.path.join(here, "cape_outlines.json"), "w"))
