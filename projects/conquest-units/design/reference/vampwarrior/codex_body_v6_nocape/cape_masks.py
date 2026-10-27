# cape_masks.py: (1) the CAPE MASK per view = the base pixels the Codex edit removed as the cape (strong change, opened,
# small blobs dropped, closed, holes filled), minus the protected collar zone; (2) the composite: the edit pasted back only
# inside the cape mask (+ a few px), the base everywhere else; (3) the diff report; (4) Moore-traced, Douglas-Peucker (1 px)
# outline polygons of the cape mask in image px -> cape_outlines.json.
import json, os, sys
sys.setrecursionlimit(100000)
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

here = os.path.dirname(os.path.abspath(__file__))
OUT = {"front": "body_front.png", "side_left": "aligned_body_side_left.png", "back": "aligned_body_back.png"}
# protected collar zone per view (image px, base canvas): polygon around the standing collar + neck + head; never changed
COLLAR = json.load(open(os.path.join(here, "collar_protect.json")))
EXTRA = json.load(open(os.path.join(here, "cape_extra.json")))

def moore_trace(m):
    """Outer boundary of a single 8-connected blob (bool array), Moore-neighbour tracing, Jacob's stopping criterion.
    Returns [(x, y), ...] pixel centres, clockwise in image coordinates."""
    H, W = m.shape; p = np.zeros((H + 2, W + 2), bool); p[1:-1, 1:-1] = m
    ys, xs = np.nonzero(p); i = np.lexsort((xs, ys))[0]; start = (int(ys[i]), int(xs[i]))
    nb = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]      # N, NE, E, SE, S, SW, W, NW
    cur = start; back = 6                                                            # came from the west
    out = [cur]; first_dir = None
    for _ in range(4 * p.size):
        for k in range(8):
            d = (back + 1 + k) % 8; y, x = cur[0] + nb[d][0], cur[1] + nb[d][1]
            if p[y, x]:
                prev = cur; cur = (y, x); back = (d + 4) % 8
                break
        else:
            break                                                                    # isolated pixel
        if first_dir is None: first_dir = cur
        elif prev == start and cur == first_dir: out.pop(); break
        out.append(cur)
    return [(x - 1, y - 1) for y, x in out]

def dp(pts, eps):
    pts = np.asarray(pts, float)
    if len(pts) < 3: return pts.tolist()
    a, b = pts[0], pts[-1]; ab = b - a; L = np.hypot(*ab)
    q = pts - a; d = np.abs(ab[0] * q[:, 1] - ab[1] * q[:, 0]) / L if L > 0 else np.hypot(q[:, 0], q[:, 1])
    i = int(np.argmax(d))
    if d[i] > eps: return dp(pts[:i + 1], eps)[:-1] + dp(pts[i:], eps)
    return [a.tolist(), b.tolist()]

def dp_closed(ring, eps):
    r = np.asarray(ring, float); far = int(np.argmax(np.hypot(*(r - r[0]).T)))
    A = dp(r[:far + 1], eps); B = dp(np.vstack([r[far:], r[:1]]), eps)
    return [[round(x, 1), round(y, 1)] for x, y in (A[:-1] + B[:-1])]

report = {}; outlines = {}
for v, outname in OUT.items():
    a = np.asarray(Image.open(os.path.join(here, "base_%s.png" % v)).convert("RGB")).astype(np.float32)
    b = np.asarray(Image.open(os.path.join(here, "edited_%s.png" % v)).convert("RGB")).astype(np.float32)
    d = np.abs(a - b).mean(2)
    prot = Image.new("L", (a.shape[1], a.shape[0])); ImageDraw.Draw(prot).polygon([tuple(p) for p in COLLAR[v]], fill=255)
    prot = np.asarray(prot) > 0
    m = ndi.binary_opening(d > 25, structure=np.ones((5, 5)))
    lab, n = ndi.label(m); sizes = ndi.sum(m, lab, range(1, n + 1))
    m = np.isin(lab, 1 + np.nonzero(sizes >= 800)[0])
    m = ndi.binary_closing(m, structure=np.ones((7, 7)), iterations=2)
    ex = Image.new("L", (a.shape[1], a.shape[0]))
    for poly in EXTRA[v]["polygons"]: ImageDraw.Draw(ex).polygon([tuple(p) for p in poly], fill=255)
    m = ndi.binary_fill_holes(m | (np.asarray(ex) > 0) | prot) & ~prot
    m[EXTRA[v]["clip_below"]:] = False
    m = ndi.binary_opening(m, structure=np.ones((3, 3)))
    lab, n = ndi.label(m); sizes = ndi.sum(m, lab, range(1, n + 1))
    cape = np.isin(lab, 1 + np.nonzero(sizes >= 800)[0]); m0 = cape.copy()
    # thin hem tips and red-lining slivers next to the cape: any strongly changed or burgundy base pixel within 25 px of it
    near = ndi.binary_dilation(cape, iterations=25)
    burgundy = (a[..., 0] > 70) & (a[..., 0] - a[..., 1] > 25) & (a[..., 0] - a[..., 2] > 15) & (d > 15)
    cape |= near & ((d > 20) | burgundy) & ~prot
    cape = ndi.binary_fill_holes(ndi.binary_closing(cape, structure=np.ones((3, 3)), iterations=2)) & ~prot
    cape[EXTRA[v]["clip_below"]:] = False
    # the composite uses everything found (cape_all: it also catches the thigh / glove / boot edges Codex redrew next to the
    # cape). The SAVED cape mask is the CAPE ONLY: (cape pixels that became background in the edit) + (the strict, opened diff
    # region + the hand-traced back polygon, i.e. cape that became body), never skin, never the collar zone, no specks < 300 px.
    cape_all = cape.copy()
    bgb = np.median(a[:20, :20].reshape(-1, 3), 0); bge = np.median(b[:20, :20].reshape(-1, 3), 0)
    base_fg = np.abs(a - bgb).max(2) > 18; edit_bg = np.abs(b - bge).max(2) < 14
    skin = (a[..., 0] > 140) & (a.mean(2) > 135) & (a[..., 0] - a[..., 2] > 4)
    strict = ndi.binary_fill_holes(m0 | (np.asarray(ex) > 0) | prot) & ~prot
    cape = ((base_fg & edit_bg & cape_all) | strict) & ~skin & ~prot
    cape = ndi.binary_opening(ndi.binary_closing(cape, iterations=2), iterations=1)
    cape[EXTRA[v]["clip_below"]:] = False
    lab, n = ndi.label(cape, structure=np.ones((3, 3))); sizes = ndi.sum(cape, lab, range(1, n + 1))
    cape = np.isin(lab, 1 + np.nonzero(sizes >= 300)[0])
    Image.fromarray((cape * 255).astype(np.uint8)).save(os.path.join(here, "cape_mask_%s.png" % v))
    # composite mask: cape + 6 px, never the collar zone
    keep = ndi.binary_dilation(cape_all, iterations=6) & ~prot
    w = ndi.gaussian_filter(keep.astype(np.float32), 2.0); w[ndi.binary_erosion(keep, iterations=3)] = 1.0; w[prot] = 0.0
    # re-tone the edit's background to the base's local background
    bg0 = np.median(a[:20, :20].reshape(-1, 3), 0); bg1 = np.median(b[:20, :20].reshape(-1, 3), 0)
    obg = (np.abs(a - bg0).max(2) < 14).astype(np.float32)
    num = np.stack([ndi.gaussian_filter(a[..., c] * obg, 30) for c in range(3)], -1)
    den = ndi.gaussian_filter(obg, 30)[..., None]
    local_bg = np.where(den > 0.05, num / np.maximum(den, 1e-6), bg0)      # far from any base background: the global grey
    # edit-background pixels inside the mask -> the base's smooth local background + faint noise (the edit leaves a faint
    # ghost of the removed cape in its own background, so its tone is not kept)
    ebg = np.abs(b - bg1).max(2) < 16; b2 = b.copy()
    rng = np.random.default_rng(0); b2[ebg] = local_bg[ebg] + rng.normal(0, 1.2, (int(ebg.sum()), 1))
    out = b2 * w[..., None] + a * (1 - w[..., None])
    Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(here, outname))
    Image.fromarray((w * 255).astype(np.uint8)).save(os.path.join(here, "composite_mask_%s.png" % v))
    fin = np.asarray(Image.open(os.path.join(here, outname)).convert("RGB")).astype(np.float32)
    outside = w < 0.01; dc = np.abs(fin - a).mean(2)
    report[v] = {"cape_mask_pct": round(100 * float(cape.mean()), 2), "composite_mask_pct": round(100 * float((~outside).mean()), 2),
                 "edit_MAD_outside_mask": round(float(d[outside].mean()), 3), "edit_pct_px_gt10_outside": round(100 * float((d[outside] > 10).mean()), 3),
                 "final_MAD_outside_mask": round(float(dc[outside].mean()), 4), "final_max_outside_mask": float(dc[outside].max()),
                 "final_whole_image_MAD": round(float(dc.mean()), 2)}
    # outlines: every component's outer ring + every hole's ring (holes = areas enclosed by the cape, e.g. an arm in front of it)
    comps = []
    lab, n = ndi.label(cape, structure=np.ones((3, 3)))
    for i in range(1, n + 1):
        c = lab == i; ring = dp_closed(moore_trace(c), 1.0)
        holes = []
        hl, hn = ndi.label(ndi.binary_fill_holes(c) & ~c)
        for j in range(1, hn + 1):
            h = hl == j
            if h.sum() >= 30: holes.append(dp_closed(moore_trace(h), 1.0))
        ys, xs = np.nonzero(c)
        comps.append({"area_px": int(c.sum()), "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())], "outer": ring, "holes": holes})
    comps.sort(key=lambda c: -c["area_px"])
    outlines[v] = {"image": "base_%s.png" % v, "size": [int(a.shape[1]), int(a.shape[0])], "components": comps}
    print(v, report[v], "components", [(c["area_px"], len(c["outer"]), len(c["holes"])) for c in comps])
json.dump(report, open(os.path.join(here, "edit_diff.json"), "w"), indent=1)
json.dump({"_note": "cape silhouettes per view, image px (x right, y down) on the 1024x1536 reference canvas; outer ring + hole rings per component; Moore-traced pixel centres simplified with Douglas-Peucker eps 1 px. The masks are cape_mask_<view>.png. side_left is the aligned left profile (the right profile = mirror x -> 1023 - x).",
           **outlines}, open(os.path.join(here, "cape_outlines.json"), "w"))
