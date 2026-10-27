# final_fix.py <out_dir>: final-review fixes on top of final_composite.py's images, without new Codex rounds.
#  * front, thigh zone (rows 560-770, cols 380-650): the edit's thighs taken whole - the base thigh edges were the cape
#    panels' edges, so base-inside / edit-outside left doubled thigh contours.
#  * back (rows >= 700) and side_left (rows >= 800): the round-2 edit's lower legs taken whole - the base boots below the old
#    hem and the edit's boots above it did not join (hem ghosts, calf fragments). The edit's feet sit on the base rows
#    (soles 1476 / 1468, silhouettes within 1-2 px).
#  * then: edit background in those zones -> continuation of the base background; dark-red lining remnants -> inpainted;
#    isolated dark specks in the background (< 60 px) -> background.
#  The collar zone and the head are never touched. The mask union for the diff report is written as composite_mask_*.png.
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage as ndi

here = os.path.dirname(os.path.abspath(__file__)); out_dir = sys.argv[1] if len(sys.argv) > 1 else here
os.makedirs(out_dir, exist_ok=True)
VIEWS = {"front": ("body_front.png", "edited_front.png", [(560, 770, 380, 650)]),
         "side_left": ("aligned_body_side_left.png", "edited2_side_left.png", [(800, 1536, 0, 1024)]),
         "back": ("aligned_body_back.png", "edited2_back.png", [(700, 1536, 0, 1024)])}
COLLAR = json.load(open(os.path.join(here, "collar_protect.json")))
L = lambda p: np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
rep = json.load(open(os.path.join(here, "edit_diff.json"))); rng = np.random.default_rng(2)
def dred(x): return (x[..., 0] < 150) & (x[..., 0] > 45) & (x[..., 0] - x[..., 1] > 18) & (x[..., 0] - x[..., 2] > 10)
for v, (fname, ename, zones) in VIEWS.items():
    base = L(os.path.join(here, "base_%s.png" % v)); cur = L(os.path.join(here, fname)); E = L(os.path.join(here, ename))
    wprev = np.asarray(Image.open(os.path.join(here, "composite_mask_%s.png" % v))).astype(np.float32) / 255
    prot = Image.new("L", (base.shape[1], base.shape[0])); ImageDraw.Draw(prot).polygon([tuple(p) for p in COLLAR[v]], fill=255); prot = np.asarray(prot) > 0
    Z = np.zeros(prot.shape, bool)
    for y0, y1, x0, x1 in zones: Z[y0:y1, x0:x1] = True
    Z &= ~prot
    w = ndi.gaussian_filter(Z.astype(np.float32), 3.0); w[ndi.binary_erosion(Z, iterations=6)] = 1.0; w[prot] = 0
    cur = E * w[..., None] + cur * (1 - w[..., None])
    bg0 = np.median(base[:20, :20].reshape(-1, 3), 0); bgE = np.median(E[:20, :20].reshape(-1, 3), 0)
    M = (w > 0.01) | (wprev > 0.01)
    known = (np.abs(base - bg0).max(2) < 12) & ~ndi.binary_dilation(M, iterations=2)
    fillpx = (w > 0.01) & ~ndi.binary_dilation(np.abs(E - bgE).max(2) >= 22, iterations=2)
    fill = np.zeros_like(base); done = np.zeros(fillpx.shape, bool)
    for s in (2, 4, 8, 16, 32, 64, 128):
        num = np.stack([ndi.gaussian_filter(base[..., c] * known, s) for c in range(3)], -1); den = ndi.gaussian_filter(known.astype(np.float32), s)
        ok = fillpx & ~done & (den > 0.15); fill[ok] = num[ok] / den[ok][:, None]; done |= ok
    fill[fillpx & ~done] = bg0
    cur[fillpx] = fill[fillpx] + rng.normal(0, 1.1, (int(fillpx.sum()), 1))
    # dark-red remnants (not the collar zone, not the bright gem) -> inpaint from non-red neighbours
    rem = dred(cur) & ~prot; rem[:300] = False
    rem = ndi.binary_dilation(ndi.binary_opening(rem, iterations=1), iterations=2) & ~prot
    good = (~rem).astype(np.float32); inp = rem.copy()
    for s in (2, 4, 8):
        num = np.stack([ndi.gaussian_filter(cur[..., c] * good, s) for c in range(3)], -1); den = ndi.gaussian_filter(good, s)
        ok = inp & (den > 0.3); cur[ok] = num[ok] / den[ok][:, None]; inp &= ~ok
    # isolated dark specks in the background
    fg = np.abs(cur - bg0).max(2) > 14
    lab, n = ndi.label(fg); sz = ndi.sum(fg, lab, range(1, n + 1)); speck = np.isin(lab, 1 + np.nonzero(sz < 60)[0]) & ~prot
    speck = ndi.binary_dilation(speck, iterations=2) & ~prot
    num = np.stack([ndi.gaussian_filter(cur[..., c] * (~fg), 6) for c in range(3)], -1); den = ndi.gaussian_filter((~fg).astype(np.float32), 6)
    ok = speck & (den > 0.2); cur[ok] = num[ok] / den[ok][:, None]
    wu = np.maximum(np.maximum(wprev, w), (rem | speck).astype(np.float32))
    Image.fromarray(np.clip(cur + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(out_dir, fname))
    Image.fromarray((wu * 255).astype(np.uint8)).save(os.path.join(out_dir, "composite_mask_%s.png" % v))
    fin = L(os.path.join(out_dir, fname)); dc = np.abs(fin - base).mean(2); outside = wu < 0.01
    rep[v].update({"FIX_mask_pct": round(100 * float((~outside).mean()), 2), "FIX_MAD_outside_mask": round(float(dc[outside].mean()), 4),
                   "FIX_max_outside_mask": float(dc[outside].max()), "FIX_whole_image_MAD": round(float(dc.mean()), 2),
                   "FIX_red_px": int(rem.sum()), "FIX_speck_px": int(speck.sum())})
    print(v, {k: rep[v][k] for k in rep[v] if k.startswith("FIX")})
ImageOps.mirror(Image.open(os.path.join(out_dir, "aligned_body_side_left.png")).convert("RGB")).save(os.path.join(out_dir, "aligned_body_side_right.png"))
json.dump(rep, open(os.path.join(out_dir, "edit_diff.json"), "w"), indent=1)
