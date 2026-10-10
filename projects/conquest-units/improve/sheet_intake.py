"""Character-sheet intake (plain Python + PIL, no Blender): the FIRST step of a new from-scratch unit
(.claude/skills/conquest-new-unit). Measures the sheet so the build lane starts from numbers, not eyeballing.

    python -P sheet_intake.py <sheet image> <unit> [--views front,back,side] [--bg-tol 26] [--base-frac 0.06]
        [--axis front=212,side=1001] [--ortho-views front,side] [--region x0,y0,x1,y1] [--assign x1,x2] [--cuts x1,x2] [--floor view=y_px,...]

Finds the turnaround views automatically: the background tone is the sheet's median border colour; foreground = pixels
further than --bg-tol from it. Small text / label blobs are dropped; the remaining foreground in the LEFT turnaround area
(everything left of the detail-panel column, found as the widest empty vertical gutter) is split into views at empty
vertical gutters, left to right, named by --views (default front,back,side: the house sheet order).
Per view: bbox px, the character axis x (centre of the bottom 15 % of the silhouette = the base / feet), the floor line
y (lowest foreground row), height px, and the sheet-unit transform (1 unit = 100 px; z = (floor_y - y) / 100,
x = (x - axis_x) / 100). Palette swatches = round blobs of uniform colour in the panel column; each is pixel-sampled
(median of its inner disc). Writes:
    design/reference/<unit>/intake.json        every number above (the build lane cites it)
    design/reference/<unit>/intake_overlay.png the sheet with view boxes, axes, floor lines and swatch samples drawn
                                               (the orchestrator LOOKS at it before dispatching: a wrong split is
                                               caught here, not in the lane)
"""
import json, os, sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

args = sys.argv[1:]
SHEET, UNIT = args[0], args[1]
VIEWS = args[args.index("--views") + 1].split(",") if "--views" in args else ["front", "back", "side"]
BG_TOL = float(args[args.index("--bg-tol") + 1]) if "--bg-tol" in args else 26.0
MIN_GUTTER = 12                      # px: an empty column run at least this wide separates two views
MIN_VIEW_W = 60                      # px: narrower foreground runs are labels / specks, not views
BASE_FRAC = float(args[args.index("--base-frac") + 1]) if "--base-frac" in args else 0.06   # the axis = centre of the
                                     #   bottom BASE_FRAC of the silhouette (feet / pot base). 0.15 was fooled by
                                     #   shadowbun's tail (it hangs into the bottom 14 %); 6 % keeps only the feet
ORTHO_VIEWS = args[args.index("--ortho-views") + 1].split(",") if "--ortho-views" in args else ["front", "side"]
                                     # views drawn as TRUE orthographic projections (a sheet "front" drawn three-
                                     #   quarter, like shadowbun's, is excluded: the sheet-vs-model compare uses only these)
REGION = [int(v) for v in args[args.index("--region") + 1].split(",")] if "--region" in args else None
                                     # --region x0,y0,x1,y1 : only this box is searched (keeps a big title / the detail
                                     #   panels out; raven_rogue 2026-10-09 merged its title into the front view)
ASSIGN = [float(v) for v in args[args.index("--assign") + 1].split(",")] if "--assign" in args else None
                                     # --assign x1,x2 : view boundaries for COMPONENT assignment: every big blob goes to the
                                     #   view whose span holds its centroid x, so views that overlap in x (a side-view beak
                                     #   reaching over the back view's feathers) stay apart; the column-gutter split cannot
FLOOR_OVR = {}                       # --floor side=790,back=782 : explicit floor line (the soles) per view, when the
if "--floor" in args:                #   drawing hangs something below the feet (pangolin_knight's tail tip, 28 px)
    for kv in args[args.index("--floor") + 1].split(","):
        k_, v_ = kv.split("="); FLOOR_OVR[k_] = int(v_)
AXIS_OVR = {}                        # --axis front=212,side=1001 : explicit axis px per view (overrides the base rule)
if "--axis" in args:
    for kv in args[args.index("--axis") + 1].split(","):
        k_, v_ = kv.split("="); AXIS_OVR[k_] = float(v_)

im = Image.open(SHEET).convert("RGB")
A = np.asarray(im).astype(np.int32)
Hh, Ww, _ = A.shape
border = np.concatenate([A[:8].reshape(-1, 3), A[-8:].reshape(-1, 3), A[:, :8].reshape(-1, 3), A[:, -8:].reshape(-1, 3)])
BG = np.median(border, 0)
fg = np.linalg.norm(A - BG, axis=2) > BG_TOL
fg_full = fg.copy()                  # the swatch search always sees the whole sheet (--region masks only the views)
if REGION:
    keep_ = np.zeros_like(fg); keep_[REGION[1]:REGION[3], REGION[0]:REGION[2]] = True; fg &= keep_


def label_blobs(mask):
    """4-connected components (iterative flood over run-length rows; no scipy)."""
    lab = np.zeros(mask.shape, np.int32)
    n = 0
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if lab[y0, x0]:
            continue
        n += 1
        stack = [(y0, x0)]
        lab[y0, x0] = n
        while stack:
            y, x = stack.pop()
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < Hh and 0 <= xx < Ww and mask[yy, xx] and not lab[yy, xx]:
                    lab[yy, xx] = n
                    stack.append((yy, xx))
    return lab, n


# work at half resolution for the component pass (labels / specks), then map back
small = fg[::2, ::2]
lab, n = label_blobs(small)
sizes = np.bincount(lab.ravel(), minlength=n + 1)
big = np.zeros_like(small)
for i in range(1, n + 1):
    if sizes[i] >= 1500:             # >= ~6000 full-res px: a character view (labels and swatches are far smaller)
        big |= lab == i
big = np.kron(big, np.ones((2, 2), bool))[:Hh, :Ww] & fg

# the turnaround area = columns left of the widest interior empty gutter
colocc = big.any(0)
runs, x = [], 0
while x < Ww:
    if not colocc[x]:
        x0 = x
        while x < Ww and not colocc[x]:
            x += 1
        runs.append((x0, x))
    else:
        x += 1
interior = [r for r in runs if r[0] > 0 and r[1] < Ww]
segs, x = [], 0
occ_x = np.nonzero(colocc)[0]
cuts = [r for r in interior if r[1] - r[0] >= MIN_GUTTER]
edges = [occ_x[0]] + [c for r in cuts for c in r] + [occ_x[-1] + 1]
for a, b in zip(edges[::2], edges[1::2]):
    if b - a >= MIN_VIEW_W:
        segs.append((int(a), int(b)))
CUTS = [int(v) for v in args[args.index("--cuts") + 1].split(",")] if "--cuts" in args else None
if CUTS:                                        # --cuts x1,x2 : hard column boundaries for views that TOUCH (one blob:
    occ_ = np.nonzero(big.any(0))[0]            #   shadow_mole's burrows join front + back); --assign cannot split a blob
    edges_ = [int(occ_[0])] + CUTS + [int(occ_[-1]) + 1]
    segs = [(a_, b_) for a_, b_ in zip(edges_[:-1], edges_[1:])]
VIEW_MASK = {}
if ASSIGN:                                      # component assignment (see --assign)
    labb, nb = label_blobs(big[::2, ::2])
    bounds = [-1e9] + [v / 2 for v in ASSIGN] + [1e9]
    for i in range(1, nb + 1):
        yy_, xx_ = np.nonzero(labb == i)
        if len(yy_) < 200:
            continue
        k = int(np.searchsorted(bounds, xx_.mean()) - 1)
        if 0 <= k < len(VIEWS):
            VIEW_MASK.setdefault(VIEWS[k], np.zeros_like(labb, bool))
            VIEW_MASK[VIEWS[k]] |= labb == i
    VIEW_MASK = {k: np.kron(v, np.ones((2, 2), bool))[:Hh, :Ww] & big for k, v in VIEW_MASK.items()}
    segs = []
    for k in VIEWS:
        xs_ = np.nonzero(VIEW_MASK[k].any(0))[0]
        segs.append((int(xs_.min()), int(xs_.max()) + 1))
views = {}
for name, (a, b) in zip(VIEWS, segs):
    m = (VIEW_MASK[name] if name in VIEW_MASK else big)[:, a:b]
    ys, xs = np.nonzero(m)
    y0, y1 = int(ys.min()), FLOOR_OVR.get(name, int(ys.max()))
    base = (ys >= y1 - BASE_FRAC * (y1 - y0)) & (ys <= y1)
    axis = AXIS_OVR.get(name, float(a + 0.5 * (xs[base].min() + xs[base].max())))
    rows = {}
    for frac in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9):               # half-width at heights (fraction of the height from the floor)
        yy = int(round(y1 - frac * (y1 - y0)))
        xr = np.nonzero(m[yy])[0]
        if len(xr):
            rows["%.2f" % frac] = {"y_px": yy, "half_w_units": round(float(max(a + xr.max() - axis, axis - a - xr.min()) / 100), 3)}
    views[name] = {"bbox_px": [int(a + xs.min()), y0, int(a + xs.max()) + 1, y1 + 1], "axis_x_px": round(axis, 1),
                   "floor_y_px": y1, "height_px": y1 - y0 + 1, "height_units": round((y1 - y0 + 1) / 100, 3),
                   "width_units": round((xs.max() - xs.min() + 1) / 100, 3), "half_width_profile": rows,
                   "transform": "z = (%d - y_px) / 100 ; x = (x_px - %.1f) / 100" % (y1, axis)}
extra = segs[len(VIEWS):]
# ortho frame: one box size for every view (the widest / tallest view + margins), placed per view on its own axis and
# floor line -- the renderer frames the build's ortho cameras with the SAME box, so sheet crop and render compare 1:1
MARGIN_X, ABOVE, BELOW = 20, 40, 20
fw = int(10 * np.ceil((2 * max(max(v["axis_x_px"] - v["bbox_px"][0], v["bbox_px"][2] - v["axis_x_px"])   # the box is
                                for k, v in views.items() if k in ORTHO_VIEWS or not ORTHO_VIEWS) + 2 * MARGIN_X) / 10))
                                     # centred on each axis, so it must cover the widest reach FROM the axis (an
                                     #   off-centre tail), not the bbox width (shadowbun's side crop lost its tail)
fh = int(10 * np.ceil((max(v["height_px"] for v in views.values()) + ABOVE + BELOW) / 10))
ortho = {"views": [v for v in ORTHO_VIEWS if v in views], "width_px": fw, "height_px": fh, "below_floor_px": BELOW,
         "crops": {k: [int(round(v["axis_x_px"] - fw / 2)), v["floor_y_px"] + BELOW - fh,
                       int(round(v["axis_x_px"] - fw / 2)) + fw, v["floor_y_px"] + BELOW] for k, v in views.items()}}

# swatches: compact near-circular uniform blobs right of the turnaround area
x_panel = segs[len(VIEWS) - 1][1] if len(segs) >= len(VIEWS) else Ww // 2
sw = []
lab2, n2 = label_blobs(fg_full[:, x_panel:][::2, ::2])
for i in range(1, n2 + 1):
    ys, xs = np.nonzero(lab2 == i)
    h_, w_ = int(np.ptp(ys)) + 1, int(np.ptp(xs)) + 1
    d_ = min(h_, w_)
    k_ = max(h_, w_) / max(d_, 1)
    # a chip is a disc; chips spaced closer than their antialiased edge fuse into a CHAIN of k discs along one axis
    kr = int(round(k_))
    if not (6 <= d_ <= 48) or kr < 1 or abs(k_ - kr) > 0.3 or len(ys) < 0.65 * 0.785 * 4 / 3.1416 * h_ * w_ * 0.9:
        continue
    vert = h_ >= w_
    for c in range(kr):
        if vert:
            cy = 2 * (ys.min() + (c + 0.5) * h_ / kr); cx = 2 * xs.mean() + x_panel
        else:
            cx = 2 * (xs.min() + (c + 0.5) * w_ / kr) + x_panel; cy = 2 * ys.mean()
        r = 0.35 * d_                                             # inner disc (full-res px), clear of the antialiased edge
        yy, xx = np.mgrid[int(cy - r):int(cy + r) + 1, int(cx - r):int(cx + r) + 1]
        disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
        px = A[yy[disc], xx[disc]]
        if px.std(0).max() > 12:                                   # not uniform: a detail-panel speck, not a chip
            continue
        sw.append({"centre_px": [round(float(cx), 1), round(float(cy), 1)],
                   "rgb": [int(v) for v in np.median(px, 0)], "diameter_px": int(2 * d_)})
sw.sort(key=lambda s: (s["centre_px"][1], s["centre_px"][0]))
cand = sw
sw, best = [], []
ALIGN = None
for c in cand:              # the largest same-size group ALIGNED in x (a chip column) or in y (a chip row) = the palette
    for al in (0, 1):       #   (seedant / shadowbun draw a column; raven_rogue a row)
        grp = [d for d in cand if abs(d["centre_px"][al] - c["centre_px"][al]) <= 6
               and abs(d["diameter_px"] - c["diameter_px"]) <= 0.25 * c["diameter_px"]]
        if len(grp) > len(best):
            best, ALIGN = grp, al
if len(best) >= 3:
    st = 1 - ALIGN                                                # the axis the chips step along
    sw = sorted(best, key=lambda d: d["centre_px"][st])
    cs_ = np.array([d["centre_px"][st] for d in sw])
    step = float(np.median(np.diff(cs_)))
    c0 = float(np.median([d["centre_px"][ALIGN] for d in sw])); dia = int(np.median([d["diameter_px"] for d in sw]))
    lim = Hh if st == 1 else Ww
    starts = [(-1, cs_[0]), (1, cs_[-1])]
    for g0, g1 in zip(cs_[:-1], cs_[1:]):                          # a GAP inside the row/column (bombee's dark chip sits
        if g1 - g0 > 1.5 * step:                                   #   between found chips): probe the missing slots too
            starts.append((0, g0)); starts.append((0, g1))
    gap_slots = [g0 + k * step for g0, g1 in zip(cs_[:-1], cs_[1:]) if g1 - g0 > 1.5 * step
                 for k in range(1, int(round((g1 - g0) / step)))]
    starts = [(-1, cs_[0]), (1, cs_[-1])] + [(2, t) for t in gap_slots]
    for direction, t_ in starts:                                   # walk outward one step at a time (2 = one gap probe)
        while True:
            t_ = t_ + direction * step if direction != 2 else t_
            if not (0.6 * dia < t_ < lim - 0.6 * dia):
                break
            cx_, cy_ = (c0, t_) if st == 1 else (t_, c0)
            r = 0.30 * dia
            yy, xx = np.mgrid[int(cy_ - r):int(cy_ + r) + 1, int(cx_ - r):int(cx_ + r) + 1]
            disc = (yy - cy_) ** 2 + (xx - cx_) ** 2 <= r * r
            yy2, xx2 = np.mgrid[int(cy_ - 0.6 * dia):int(cy_ + 0.6 * dia) + 1, int(cx_ - 0.6 * dia):int(cx_ + 0.6 * dia) + 1]
            rr = np.hypot(yy2 - cy_, xx2 - cx_)
            ring = (rr > 0.42 * dia) & (rr < 0.56 * dia)
            edge = A[yy2[ring], xx2[ring]]
            px = A[yy[disc], xx[disc]]
            has_ring = np.linalg.norm(edge - BG, axis=1).mean() > 0.5 * BG_TOL
            bg_like = np.linalg.norm(np.median(px, 0) - BG) < 4.0   # a recovered 'chip' that IS the background (the walk
                                                                  #   hit a panel border: raven_rogue x 1236); shadowbun's
                                                                  #   near-black chip sits ~9 from its bg and still passes
            if px.std(0).max() > 12 or not has_ring or bg_like:
                break
            sw.append({"centre_px": [round(float(cx_), 1), round(float(cy_), 1)], "rgb": [int(v) for v in np.median(px, 0)],
                       "diameter_px": dia, "inferred_by_spacing": True})
            if direction == 2:
                break
    sw.sort(key=lambda d: d["centre_px"][st])
dropped = [c for c in cand if c not in sw]

out_dir = os.path.join(ROOT, "design", "reference", UNIT)
os.makedirs(out_dir, exist_ok=True)
res = {"unit": UNIT, "sheet": os.path.relpath(os.path.abspath(SHEET), ROOT).replace("\\", "/"), "size_px": [Ww, Hh],
       "background_rgb": [int(v) for v in BG], "bg_tol": BG_TOL, "views": views, "ortho_frame": ortho,
       "unnamed_extra_segments_px": extra, "swatches": sw, "swatch_rejects_off_column": dropped, "base_frac": BASE_FRAC,
       "axis_overrides": AXIS_OVR,
       "units_rule": "1 sheet unit = 100 px; per view z = (floor_y - y) / 100, x = (x - axis_x) / 100",
       "caveat": "automatic split: VERIFY intake_overlay.png before dispatch (overlapping views or a floor shadow "
                 "fuse / extend a view; rerun with --views or --bg-tol). A DARK subject on a dark sheet (shadowbun) "
                 "loses its outline into the background: the floor line can sit high by ~15 px - check the soles"}
json.dump(res, open(os.path.join(out_dir, "intake.json"), "w"), indent=1)

ov = im.copy()
d = ImageDraw.Draw(ov)
for name, v in views.items():
    x0, y0, x1, y1 = v["bbox_px"]
    d.rectangle([x0, y0, x1, y1], outline=(0, 230, 255), width=2)
    d.line([v["axis_x_px"], y0 - 10, v["axis_x_px"], y1 + 10], fill=(255, 0, 255), width=1)
    d.line([x0 - 10, v["floor_y_px"], x1 + 10, v["floor_y_px"]], fill=(255, 255, 0), width=1)
    d.text((x0, y0 - 14), "%s  H %.2f u" % (name, v["height_units"]), fill=(0, 230, 255))
for s in sw:
    cx, cy = s["centre_px"]
    d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], outline=(0, 230, 255), width=2)
    d.text((cx + 20, cy - 6), str(s["rgb"]), fill=(235, 235, 235))
ov.save(os.path.join(out_dir, "intake_overlay.png"))
print("INTAKE", json.dumps({"views": {k: {kk: v[kk] for kk in ("bbox_px", "axis_x_px", "floor_y_px", "height_units")}
                                      for k, v in views.items()}, "swatches": [s["rgb"] for s in sw],
                            "extra_segments": extra}))
