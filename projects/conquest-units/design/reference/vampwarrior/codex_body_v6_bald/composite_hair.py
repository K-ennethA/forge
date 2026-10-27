# composite_hair.py: keep the Codex hair-removal edit ONLY inside the hair area; every other pixel = the v6 original.
# hair area = pixels the edit changed strongly (mean |diff| > 30), opened (drops 1-2 px line-art jitter), small blobs
# dropped, holes filled (the scalp), dilated 10 px, feathered 3 px.  Reports the edit's MAD inside / outside that mask.
import json, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull
from PIL import ImageDraw
# rows above which the whole hull of the edit is taken (just below the chin / top of the collar, per view)
HEAD_ROW = {"front": 215, "side_left": 215, "back": 215}

here = os.path.dirname(os.path.abspath(__file__))
names = {"front": "body_front.png", "side_left": "aligned_body_side_left.png", "back": "aligned_body_back.png"}
report = {}
for v, outname in names.items():
    a = np.asarray(Image.open(os.path.join(here, "orig_%s.png" % v)).convert("RGB")).astype(np.float32)
    b = np.asarray(Image.open(os.path.join(here, "edited_%s.png" % v)).convert("RGB")).astype(np.float32)
    d = np.abs(a - b).mean(2)
    m = d > 30
    m = ndi.binary_opening(m, structure=np.ones((3, 3)))
    lab, n = ndi.label(m)
    sizes = ndi.sum(m, lab, range(1, n + 1))
    keep = np.isin(lab, 1 + np.nonzero(sizes >= 150)[0])
    # stray wisps: pale hair-coloured original pixels that the edit changed
    pale = (a.min(2) > 140) & (a.max(2) - a.min(2) < 40) & (d > 15)
    keep |= ndi.binary_dilation(pale, iterations=2)
    # head: the whole region above the chin row inside the hull of the changed pixels (scalp + hairline; the face gets replaced anyway)
    ys, xs = np.nonzero(keep[:HEAD_ROW[v]])
    hull = ConvexHull(np.c_[xs, ys]); poly = [(float(xs[i]), float(ys[i])) for i in hull.vertices]
    hm = Image.new("L", (a.shape[1], a.shape[0])); ImageDraw.Draw(hm).polygon(poly, fill=255)
    keep |= (np.asarray(hm) > 0)
    keep = ndi.binary_closing(keep, structure=np.ones((9, 9)), iterations=2)
    keep = ndi.binary_fill_holes(keep)
    keep = ndi.binary_dilation(keep, structure=np.ones((3, 3)), iterations=8)
    w = ndi.gaussian_filter(keep.astype(np.float32), 3.0)
    w[keep & (ndi.binary_erosion(keep, iterations=4))] = 1.0
    # background inside the mask: the edit's grey differs slightly from the original's; re-tone edit-background pixels to the
    # original's local background (normalised gaussian of the original's own background pixels) so no halo shows
    bg0 = np.median(a[:20, :20].reshape(-1, 3), 0); bg1 = np.median(b[:20, :20].reshape(-1, 3), 0)
    obg = (np.abs(a - bg0).max(2) < 14).astype(np.float32)
    num = np.stack([ndi.gaussian_filter(a[..., c] * obg, 30) for c in range(3)], -1); den = ndi.gaussian_filter(obg, 30)[..., None]
    local_bg = num / np.maximum(den, 1e-3)
    ebg = np.abs(b - bg1).max(2) < 14
    b = b.copy(); b[ebg] = b[ebg] - bg1 + local_bg[ebg]
    out = b * w[..., None] + a * (1 - w[..., None])
    Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(here, outname))
    Image.fromarray((w * 255).astype(np.uint8)).save(os.path.join(here, "hairmask_%s.png" % v))
    outside = w < 0.01
    dc = np.abs(np.asarray(Image.open(os.path.join(here, outname)).convert("RGB")).astype(np.float32) - a).mean(2)
    report[v] = {"mask_pct": round(100 * float(keep.mean()), 2),
                 "edit_MAD_inside_mask": round(float(d[~outside].mean()), 2),
                 "edit_MAD_outside_mask": round(float(d[outside].mean()), 3),
                 "edit_pct_px_gt10_outside": round(100 * float((d[outside] > 10).mean()), 3),
                 "final_MAD_outside_mask": round(float(dc[outside].mean()), 4),
                 "final_max_outside_mask": float(dc[outside].max())}
    print(v, report[v])
json.dump(report, open(os.path.join(here, "edit_diff.json"), "w"), indent=1)
