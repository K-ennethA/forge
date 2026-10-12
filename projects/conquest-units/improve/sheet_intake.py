"""Character-sheet intake (plain Python + PIL, no Blender): the FIRST step of a new from-scratch unit
(.claude/skills/conquest-new-unit). Measures the sheet so the build lane starts from numbers, not eyeballing.

    python -P sheet_intake.py <sheet image> <unit> [--views front,back,side] [--bg-tol 26] [--base-frac 0.06]
        [--axis front=212,side=1001] [--ortho-views front,side] [--region x0,y0,x1,y1] [--assign x1,x2] [--cuts x1,x2]
        [--floor view=y_px,...] [--side-facing left|right] [--out-dir <dir>] [--no-smart] [--no-matte]

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

SMART INTAKE (default since 2026-10-11; --no-smart = the exact pre-smart behaviour, byte for byte). It automates the
flags the orchestrator used to find by hand, by running the same measurement twice: pass 1 with the caller's flags,
an analysis of what came out, then pass 2 with AUTO flags filled in wherever the caller gave none. A flag the caller
passes ALWAYS wins. intake.json then also carries:
    auto_flags      the flags smart intake chose (paste them to reproduce, or override one by passing it)
    side_facing     'left' / 'right' (which way the side view's head points on the image) + a confidence;
                    image-left = the creature's LEFT flank = meshgen's 'left' camera
    view_checks     per view: mirror-symmetry IoU, tone asymmetry, 3/4 suspicion, floor candidates (raw lowest row,
                    shadow-free, thick-support contact), floating parts, mass axis vs feet axis, dark-subject test
    height_check    per-view heights and the worst pairwise disagreement in %
    palette_fallback  when no chip column/row is found: relaxed (gradient) chips, else k-means colours of the views
    view_masks      design/reference/<unit>/view_masks.png: one label per view (1..n in --views order) at sheet px,
                    the silhouettes compose's numeric gate and sheet_proxy.py score against
    intake_notes    short human-readable lines for the lane brief (what was auto-chosen, what to check)
What it automates (each recorded in intake_notes):
    - titles, rule lines and framed detail panels removed before the split (was --region)
    - views fused by touching parts: cut at the thinnest column between them (was --cuts)
    - the floor = the lowest THICK body contact: drop shadows, thin tail curls / lava-pool rims and floating parts
      ignored (was --floor); every candidate is reported
    - the axis from the body mass: mirror-symmetry axis on front / back, eroded-mass centroid on the side, so props,
      tails, orbs and chains do not pull it (was --axis)
    - 3/4 / perspective front or back views (mirror asymmetry, front-vs-back width mismatch) left out of
      ortho_views (was --ortho-views)
    - a dark subject on a dark sheet: BiRefNet matte of that view (meshgen's ComfyUI, when it is up; --no-matte to
      skip), else a note to lower --bg-tol
    - front-vs-side height disagreement as a number; a chip-row palette fallback
"""
import json, os, sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPO = os.path.normpath(os.path.join(ROOT, "..", ".."))

MIN_GUTTER = 12                      # px: an empty column run at least this wide separates two views
MIN_VIEW_W = 60                      # px: narrower foreground runs are labels / specks, not views


# ======================================================================================================================
# arguments
# ======================================================================================================================
def parse_args(argv):
    args = list(argv)
    o = {"sheet": args[0], "unit": args[1]}

    def val(flag):
        return args[args.index(flag) + 1] if flag in args else None

    def kv(flag, cast):
        out = {}
        if flag in args:
            for p in args[args.index(flag) + 1].split(","):
                k_, v_ = p.split("=")
                out[k_] = cast(v_)
        return out
    o["views"] = val("--views").split(",") if "--views" in args else ["front", "back", "side"]
    o["bg_tol"] = float(val("--bg-tol")) if "--bg-tol" in args else 26.0
    o["base_frac"] = float(val("--base-frac")) if "--base-frac" in args else 0.06
    # the axis = centre of the bottom BASE_FRAC of the silhouette (feet / pot base). 0.15 was fooled by shadowbun's
    #   tail (it hangs into the bottom 14 %); 6 % keeps only the feet
    o["ortho_views"] = val("--ortho-views").split(",") if "--ortho-views" in args else None
    # views drawn as TRUE orthographic projections (a sheet "front" drawn three-quarter, like shadowbun's, is
    #   excluded: the sheet-vs-model compare uses only these). None = default (front,side; smart: auto-picked)
    o["region"] = [int(v) for v in val("--region").split(",")] if "--region" in args else None
    # --region x0,y0,x1,y1 : only this box is searched (keeps a big title / the detail panels out; raven_rogue
    #   2026-10-09 merged its title into the front view)
    o["assign"] = [float(v) for v in val("--assign").split(",")] if "--assign" in args else None
    # --assign x1,x2 : view boundaries for COMPONENT assignment: every big blob goes to the view whose span holds its
    #   centroid x, so views that overlap in x (a side-view beak reaching over the back view's feathers) stay apart
    o["cuts"] = [int(v) for v in val("--cuts").split(",")] if "--cuts" in args else None
    # --cuts x1,x2 : hard column boundaries for views that TOUCH (one blob: shadow_mole's burrows join front + back)
    o["floor"] = kv("--floor", int)
    # --floor side=790,back=782 : explicit floor line (the soles) per view, when the drawing hangs something below the
    #   feet (pangolin_knight's tail tip, 28 px)
    o["axis"] = kv("--axis", float)  # --axis front=212,side=1001 : explicit axis px per view (overrides the base rule)
    o["side_facing"] = val("--side-facing")
    o["out_dir"] = val("--out-dir")
    o["smart"] = "--no-smart" not in args
    o["matte"] = "--no-matte" not in args
    o["user_flags"] = {k for k in ("bg_tol", "region", "cuts", "floor", "axis", "ortho_views", "assign")
                       if "--" + k.replace("_", "-") in args}
    return o


# ======================================================================================================================
# the measurement (pass logic unchanged from the pre-smart script: --no-smart runs exactly this, once)
# ======================================================================================================================
def label_blobs(mask):
    """4-connected components (iterative flood over run-length rows; no scipy)."""
    Hh, Ww = mask.shape
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


def measure(A, BG, fg, fl):
    """One intake pass. ``fl``: views, bg_tol, base_frac, ortho_views, region, assign, cuts, floor, axis.
    ``fg`` = the foreground mask (BG distance > bg_tol, or a smart replacement). Returns the intake fields plus the
    per-view masks (internal, under key '_masks')."""
    Hh, Ww, _ = A.shape
    VIEWS, BASE_FRAC = fl["views"], fl["base_frac"]
    ORTHO_VIEWS = fl["ortho_views"] if fl["ortho_views"] is not None else ["front", "side"]
    REGION, ASSIGN, CUTS, FLOOR_OVR, AXIS_OVR = fl["region"], fl["assign"], fl["cuts"], fl["floor"], fl["axis"]
    BG_TOL = fl["bg_tol"]
    fg_full = fg.copy()                  # the swatch search always sees the whole sheet (--region masks only the views)
    fg = fg.copy()
    if REGION:
        keep_ = np.zeros_like(fg); keep_[REGION[1]:REGION[3], REGION[0]:REGION[2]] = True; fg &= keep_
    if fl.get("fg_full") is not None:    # smart: the chips are searched on the plain threshold mask
        fg_full = fl["fg_full"]

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
    segs = []
    occ_x = np.nonzero(colocc)[0]
    cuts = [r for r in interior if r[1] - r[0] >= MIN_GUTTER]
    edges = [occ_x[0]] + [c for r in cuts for c in r] + [occ_x[-1] + 1]
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a >= MIN_VIEW_W:
            segs.append((int(a), int(b)))
    if CUTS:                                        # --cuts x1,x2 : hard column boundaries for views that TOUCH (one
        occ_ = np.nonzero(big.any(0))[0]            #   blob: shadow_mole's burrows join front + back); --assign cannot
        edges_ = [int(occ_[0])] + CUTS + [int(occ_[-1]) + 1]   # split a blob
        segs = [(a_, b_) for a_, b_ in zip(edges_[:-1], edges_[1:])]
    VIEW_MASK = {}
    if fl.get("view_masks") and not ASSIGN and not CUTS:   # smart: fused views split by a watershed (split_fused)
        VIEW_MASK = {k: v & big for k, v in fl["view_masks"].items()}
        segs = []
        for k in VIEWS:
            xs_ = np.nonzero(VIEW_MASK[k].any(0))[0]
            segs.append((int(xs_.min()), int(xs_.max()) + 1))
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
    views, masks = {}, {}
    for name, (a, b) in zip(VIEWS, segs):
        m = (VIEW_MASK[name] if name in VIEW_MASK else big)[:, a:b]
        ys, xs = np.nonzero(m)
        y0, y1 = int(ys.min()), FLOOR_OVR.get(name, int(ys.max()))
        base = (ys >= y1 - BASE_FRAC * (y1 - y0)) & (ys <= y1)
        axis = AXIS_OVR.get(name, float(a + 0.5 * (xs[base].min() + xs[base].max())))
        rows = {}
        for frac in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9):               # half-width at heights (fraction of the height)
            yy = int(round(y1 - frac * (y1 - y0)))
            xr = np.nonzero(m[yy])[0]
            if len(xr):
                rows["%.2f" % frac] = {"y_px": yy, "half_w_units": round(float(max(a + xr.max() - axis, axis - a - xr.min()) / 100), 3)}
        views[name] = {"bbox_px": [int(a + xs.min()), y0, int(a + xs.max()) + 1, y1 + 1], "axis_x_px": round(axis, 1),
                       "floor_y_px": y1, "height_px": y1 - y0 + 1, "height_units": round((y1 - y0 + 1) / 100, 3),
                       "width_units": round((xs.max() - xs.min() + 1) / 100, 3), "half_width_profile": rows,
                       "transform": "z = (%d - y_px) / 100 ; x = (x_px - %.1f) / 100" % (y1, axis)}
        full = np.zeros((Hh, Ww), bool)
        full[:, a:b] = m
        masks[name] = full
    extra = segs[len(VIEWS):]
    # ortho frame: one box size for every view (the widest / tallest view + margins), placed per view on its own axis
    # and floor line -- the renderer frames the build's ortho cameras with the SAME box, so sheet crop and render
    # compare 1:1
    MARGIN_X, ABOVE, BELOW = 20, 40, 20
    fw = int(10 * np.ceil((2 * max(max(v["axis_x_px"] - v["bbox_px"][0], v["bbox_px"][2] - v["axis_x_px"])  # the box
                                    for k, v in views.items() if k in ORTHO_VIEWS or not ORTHO_VIEWS) + 2 * MARGIN_X) / 10))
    # is centred on each axis, so it must cover the widest reach FROM the axis (an off-centre tail), not the bbox
    #   width (shadowbun's side crop lost its tail)
    fh = int(10 * np.ceil((max(v["height_px"] for v in views.values()) + ABOVE + BELOW) / 10))
    ortho = {"views": [v for v in ORTHO_VIEWS if v in views], "width_px": fw, "height_px": fh, "below_floor_px": BELOW,
             "crops": {k: [int(round(v["axis_x_px"] - fw / 2)), v["floor_y_px"] + BELOW - fh,
                           int(round(v["axis_x_px"] - fw / 2)) + fw, v["floor_y_px"] + BELOW] for k, v in views.items()}}

    sw, dropped = find_swatches(A, BG, fg_full, segs, VIEWS, BG_TOL)
    return {"views": views, "ortho_frame": ortho, "unnamed_extra_segments_px": extra, "swatches": sw,
            "swatch_rejects_off_column": dropped, "_masks": masks, "_segs": segs, "_big": big}


def find_swatches(A, BG, fg_full, segs, VIEWS, BG_TOL, max_std=12):
    """Palette chips: compact near-circular uniform blobs right of the turnaround area. ``max_std`` 12 = the
    pre-smart rule (flat chips); the smart fallback reruns it at a higher value for gradient-shaded chips."""
    Hh, Ww, _ = A.shape
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
            r = 0.35 * d_                                             # inner disc (full-res px), clear of the edge
            yy, xx = np.mgrid[int(cy - r):int(cy + r) + 1, int(cx - r):int(cx + r) + 1]
            disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
            px = A[yy[disc], xx[disc]]
            if px.std(0).max() > max_std:                              # not uniform: a detail-panel speck, not a chip
                continue
            sw.append({"centre_px": [round(float(cx), 1), round(float(cy), 1)],
                       "rgb": [int(v) for v in np.median(px, 0)], "diameter_px": int(2 * d_)})
    sw.sort(key=lambda s: (s["centre_px"][1], s["centre_px"][0]))
    cand = sw
    sw, best = [], []
    ALIGN = None
    for c in cand:          # the largest same-size group ALIGNED in x (a chip column) or in y (a chip row) = the palette
        for al in (0, 1):   #   (seedant / shadowbun draw a column; raven_rogue a row)
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
        gap_slots = [g0 + k * step for g0, g1 in zip(cs_[:-1], cs_[1:]) if g1 - g0 > 1.5 * step
                     for k in range(1, int(round((g1 - g0) / step)))]
        # a GAP inside the row/column (bombee's dark chip sits between found chips): probe the missing slots too
        starts = [(-1, cs_[0]), (1, cs_[-1])] + [(2, t) for t in gap_slots]
        for direction, t_ in starts:                                   # walk outward one step at a time (2 = one gap
            while True:                                                #   probe)
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
                bg_like = np.linalg.norm(np.median(px, 0) - BG) < 4.0   # a recovered 'chip' that IS the background
                # (the walk hit a panel border: raven_rogue x 1236); shadowbun's near-black chip sits ~9 from its bg
                if px.std(0).max() > max_std or not has_ring or bg_like:
                    break
                sw.append({"centre_px": [round(float(cx_), 1), round(float(cy_), 1)], "rgb": [int(v) for v in np.median(px, 0)],
                           "diameter_px": dia, "inferred_by_spacing": True})
                if direction == 2:
                    break
        sw.sort(key=lambda d: d["centre_px"][st])
    dropped = [c for c in cand if c not in sw]
    return sw, dropped


# ======================================================================================================================
# smart analysis
# ======================================================================================================================
def _dilate(m, k=1):
    out = m.copy()
    for _ in range(k):
        cur = out.copy()
        out[1:] |= cur[:-1]; out[:-1] |= cur[1:]; out[:, 1:] |= cur[:, :-1]; out[:, :-1] |= cur[:, 1:]
    return out


def _erode(m, k=1):
    return ~_dilate(~m, k)


def _ndi():
    try:
        from scipy import ndimage
        return ndimage
    except ImportError:
        return None


def _label(m):
    nd = _ndi()
    if nd is not None:
        lab, n = nd.label(m)
        return lab, int(n)
    return label_blobs(m)


def _erode_r(m, r):
    """Erosion by a disc of radius r (scipy when present; else r 4-steps)."""
    nd = _ndi()
    if nd is not None and r > 0:
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        return nd.binary_erosion(m, structure=(yy * yy + xx * xx) <= r * r)
    return _erode(m, r)


def _fill_holes(m):
    nd = _ndi()
    if nd is not None:
        return nd.binary_fill_holes(m)
    outside = np.zeros_like(m)
    outside[0, :] = ~m[0, :]; outside[-1, :] = ~m[-1, :]; outside[:, 0] = ~m[:, 0]; outside[:, -1] = ~m[:, -1]
    prev = None
    while prev is None or (outside != prev).any():
        prev = outside
        outside = _dilate(outside, 1) & ~m
    return ~outside


def _runs(line):
    """(start, length) of every True run in a 1-D bool array."""
    d = np.diff(np.concatenate([[0], line.astype(np.int8), [0]]))
    s = np.nonzero(d == 1)[0]
    e = np.nonzero(d == -1)[0]
    return s, e - s


def frame_lines(fg, min_frac=0.12, max_thick=5):
    """Thin straight rules: horizontal runs >= min_frac of the sheet width whose band is <= max_thick px thick, and
    vertical runs likewise. Title underlines, panel frames and separators are these; drawn creatures are not (a
    long solid row of a body is thick)."""
    Hh, Ww = fg.shape
    hl = np.zeros_like(fg)
    for y in range(Hh):
        s, L = _runs(fg[y])
        for s_, L_ in zip(s, L):
            if L_ >= min_frac * Ww:
                hl[y, s_:s_ + L_] = True
    vl = np.zeros_like(fg)
    for x in range(Ww):
        s, L = _runs(fg[:, x])
        for s_, L_ in zip(s, L):
            if L_ >= min_frac * Hh:
                vl[s_:s_ + L_, x] = True
    # thickness test: a rule's band is thin across its run
    thick_h = np.zeros_like(fg)
    for x in range(Ww):
        s, L = _runs(fg[:, x])
        for s_, L_ in zip(s, L):
            if L_ > max_thick:
                thick_h[s_:s_ + L_, x] = True
    thick_v = np.zeros_like(fg)
    for y in range(Hh):
        s, L = _runs(fg[y])
        for s_, L_ in zip(s, L):
            if L_ > max_thick:
                thick_v[y, s_:s_ + L_] = True
    return (hl & ~thick_h) | (vl & ~thick_v)


def clutter_mask(fg, Hh, Ww):
    """What is NOT a turnaround view: the title text, rule lines, and the framed detail-panel / palette boxes
    (everything inside a rectangle of thin frame lines). Returns (mask to remove, report)."""
    lines = frame_lines(fg)
    lab, n = _label(_dilate(lines, 2))
    remove = lines.copy()
    panels, rules = [], []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        if (x1 - x0) >= 0.08 * Ww and (y1 - y0) >= 0.08 * Hh:
            panels.append([x0, y0, x1, y1])
            remove[y0:y1, x0:x1] = True             # the box and everything drawn inside it
        else:
            rules.append([x0, y0, x1, y1])
    # the panel COLUMN: when two or more framed panels stack in one x range, a panel whose frame was not found (a
    #   dim or broken border: gemwing's neck panel) still sits in that column - drop every component that lies
    #   mostly inside it (component-wise, so a view reaching into the column with its tail keeps it)
    column = None
    if len(panels) >= 2:
        x0s = sorted(p_[0] for p_ in panels)
        col_x0 = x0s[len(x0s) // 2] if len(x0s) > 2 else x0s[0]
        stacked = [p_ for p_ in panels if p_[0] <= col_x0 + 0.05 * Ww and p_[2] >= col_x0]
        if len(stacked) >= 2:
            column = [int(min(p_[0] for p_ in stacked)), int(min(p_[1] for p_ in stacked)), Ww,
                      int(max(p_[3] for p_ in stacked))]
            labc, nc = _label(fg & ~remove)
            if nc:
                xs_all = np.nonzero(labc)[1]
                ids = labc[labc > 0]
                tot = np.bincount(ids, minlength=nc + 1)
                inside = np.bincount(ids[xs_all >= column[0]], minlength=nc + 1)
                drop = np.nonzero((tot > 0) & (inside >= 0.6 * np.maximum(tot, 1)))[0]
                drop = drop[drop > 0]
                if len(drop):
                    remove |= np.isin(labc, drop)
    # title: big-letter blobs in the top band that are short (a text line), not a tall view reaching up there
    lab2, n2 = _label(fg & ~remove)
    title = []
    if n2:
        objs = _ndi().find_objects(lab2) if _ndi() is not None else None
        for i in range(1, n2 + 1):
            if objs is not None:
                sl = objs[i - 1]
                y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
            else:
                ys, xs = np.nonzero(lab2 == i)
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            if y1 <= 0.2 * Hh and (y1 - y0) <= 0.12 * Hh:
                remove[y0:y1, x0:x1] |= lab2[y0:y1, x0:x1] == i
                title.append([int(x0), int(y0), int(x1), int(y1)])
    return remove, {"panels": panels, "panel_column": column, "rules": len(rules), "title_blobs": len(title)}


def clutter_segments(big, segs, fg_text=None, text_only=False):
    """Segments that are not turnaround views. A view is mostly ONE blob (a detail-panel column is several images +
    text: largest blob < 40 %), carries little text (>= 100 letter-sized blobs = a text column), is TALL (>= 45 % of
    the tallest view-like segment) and stands in the common floor band (its bottom within 20 % of that height of the
    view-like segments' median bottom). Returns the (a, b) column spans to drop."""
    info = []
    letters = None
    if fg_text is not None:
        lab_t, n_t = _label(fg_text)
        if n_t:
            sz_t = np.bincount(lab_t.ravel(), minlength=n_t + 1)
            nd = _ndi()
            objs = nd.find_objects(lab_t) if nd is not None else None
            if objs is not None:
                letters = np.array([0.5 * (o_[1].start + o_[1].stop) for i, o_ in enumerate(objs)
                                    if o_ is not None and 15 <= sz_t[i + 1] <= 600 and o_[0].stop - o_[0].start <= 40])
    for a, b in segs:
        strip = big[:, a:b]
        rows = np.nonzero(strip.any(1))[0]
        lab, n = _label(strip)
        sz = np.bincount(lab.ravel(), minlength=n + 1); sz[0] = 0
        info.append({"ab": (a, b), "area": int(strip.sum()), "h": int(rows.max() - rows.min() + 1),
                     "bottom": int(rows.max()), "one": float(sz.max() / max(1, sz.sum())),
                     "letters": int(((letters >= a) & (letters < b)).sum()) if letters is not None and len(letters) else 0})
    if not info:
        return []
    if text_only:
        return [i["ab"] for i in info if i["letters"] >= 100]
    viewish = [i for i in info if i["one"] >= 0.4 and i["letters"] < 100] or info
    amax = max(i["area"] for i in viewish)
    hmax = max(i["h"] for i in viewish)
    tall = [i for i in viewish if i["h"] >= 0.45 * hmax]
    ref = float(np.median([i["bottom"] for i in tall])) if tall else max(i["bottom"] for i in info)
    drop = []
    for i in info:
        if (i["one"] < 0.4 or i["letters"] >= 100 or i["area"] < 0.15 * amax or i["h"] < 0.45 * hmax
                or i["bottom"] < ref - 0.2 * hmax):
            drop.append(i["ab"])
    return drop


def suggest_cuts(big, segs, n_views):
    """Views fused by touching parts: the thinnest column (fewest foreground px, smoothed) of the widest segment(s),
    away from the segment ends, until there are n_views segments. Returns (nominal cut x list, detail)."""
    occ = big.sum(0).astype(float)
    occ_s = np.convolve(occ, np.ones(9) / 9, mode="same")
    segs = list(segs)
    cuts = []
    while len(segs) < n_views and segs:
        i = int(np.argmax([b - a for a, b in segs]))
        a, b = segs[i]
        lo, hi = a + int(0.2 * (b - a)), b - int(0.2 * (b - a))
        if hi - lo < 4:
            break
        x = int(lo + np.argmin(occ_s[lo:hi]))
        cuts.append({"x_px": x, "column_px": int(occ[x]), "segment": [int(a), int(b)]})
        segs[i:i + 1] = [(a, x), (x, b)]
    return sorted(c["x_px"] for c in cuts), cuts


def split_fused(blob, x_c, A, BG):
    """Split ONE blob holding two touching views at nominal column x_c, along the WEAKEST CONTACT, not a column.
    1. the thinnest contact first: open the blob by 2, 3, 4, 6 px - the first radius that leaves a big piece on each
       side of x_c (each piece goes to the side holding most of it) marks the two views, so a tail tip resting on the
       neighbour's paw stays with its own view;
    2. else the heavily eroded bodies (contacts, tails and claws vanish under the erosion) are the seeds;
    then every unassigned pixel joins the seed it reaches by the shorter path through the drawing (gemwing: the
    back view's tail tip lies under the side view's snout)."""
    ys, xs = np.nonzero(blob)
    H = ys.max() - ys.min() + 1
    total = float(blob.sum())
    cols = np.arange(blob.shape[1])[None, :]
    markers = np.zeros(blob.shape, np.int16)
    for r_open in (2, 3, 4, 6):
        opened = _dilate(_erode_r(blob, r_open), r_open) & blob
        lab, n = _label(opened)
        if n < 2:
            continue
        sizes = np.bincount(lab.ravel(), minlength=n + 1); sizes[0] = 0
        big = [i for i in range(1, n + 1) if sizes[i] >= 0.05 * total]
        side_of = {}
        for i in big:
            side_of[i] = 1 if float((np.nonzero(lab == i)[1] < x_c).mean()) > 0.5 else 2
        if 1 in side_of.values() and 2 in side_of.values():
            for i, k in side_of.items():
                markers[lab == i] = k
            break
    if not (markers == 1).any() or not (markers == 2).any():
        markers[:] = 0
        r = max(4, int(0.035 * H))
        core = _erode_r(blob, r)
        lab, n = _label(core)
        if n:
            sizes = np.bincount(lab.ravel(), minlength=n + 1); sizes[0] = 0
            for i in np.argsort(-sizes):
                if i == 0 or sizes[i] < 0.01 * total:
                    continue
                sel = lab == i
                left_frac = float((np.nonzero(sel)[1] < x_c).mean())
                if 0.2 <= left_frac <= 0.8:     # one core across both views (wide skirts touching): cut it at x_c
                    markers[sel & (cols < x_c)] = 1
                    markers[sel & (cols >= x_c)] = 2
                else:
                    markers[sel] = 1 if left_frac > 0.5 else 2
    if not (markers == 1).any() or not (markers == 2).any():
        left = blob.copy(); left[:, x_c:] = False
        return left, blob & ~left, "column"
    # geodesic growth inside the blob at half resolution: each pixel joins the core it can reach by the shorter path
    #   through the drawing (a priority watershed is NOT this: on a bright subject every path is cheap and the first
    #   seed floods everything)
    b2 = blob[::2, ::2]
    l2 = markers[::2, ::2].copy()
    l2[~b2] = 0
    todo = b2 & (l2 == 0)
    while todo.any():
        g1 = _dilate(l2 == 1, 1) & todo
        g2 = _dilate(l2 == 2, 1) & todo & ~g1
        if not (g1.any() or g2.any()):
            break
        l2[g1] = 1; l2[g2] = 2
        todo &= ~(g1 | g2)
    full = np.kron(l2, np.ones((2, 2), np.int16))[:blob.shape[0], :blob.shape[1]]
    left = blob & (full == 1)
    right = blob & (full == 2)
    rest = blob & ~left & ~right                      # odd edge pixels: by side of the cut
    left |= rest & (np.arange(blob.shape[1])[None, :] < x_c)
    right |= rest & ~left
    return left, right, "geodesic"


def mirror_axis(m, lo, hi):
    """x (half-px steps) maximising IoU(m, m mirrored about x), searched in [lo, hi]."""
    ys, xs = np.nonzero(m)
    if not len(xs):
        return None, 0.0
    best = (None, -1.0)
    rows = ys
    for x2 in range(int(2 * lo), int(2 * hi) + 1):           # mirror: x' = x2 - x
        xm = x2 - xs
        ok = (xm >= 0) & (xm < m.shape[1])
        hit = np.zeros(len(xs), bool)
        hit[ok] = m[rows[ok], xm[ok]]
        inter = hit.sum()
        iou = inter / (2 * len(xs) - inter)
        if iou > best[1]:
            best = (x2 / 2.0, float(iou))
    return best


def analyse_view(name, m, A, BG, bg_tol, H_sheet):
    """Everything smart intake knows about one view's silhouette ``m`` (full-sheet bool mask)."""
    ys, xs = np.nonzero(m)
    out = {}
    if not len(ys):
        return {"empty": True}
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    H = y1 - y0 + 1
    W = x1 - x0 + 1
    # -- floating parts: components not touching the main body (orbs, halos, drawn shadows)
    lab, n = _label(m)
    sizes = np.bincount(lab.ravel(), minlength=n + 1); sizes[0] = 0
    main_id = int(np.argmax(sizes))
    body = lab == main_id
    body_bottom = int(np.nonzero(body.any(1))[0].max())
    floating = []
    for i in range(1, n + 1):
        if i == main_id or sizes[i] < 0.002 * sizes[main_id]:
            continue
        yy, xx = np.nonzero(lab == i)
        floating.append({"bbox_px": [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1],
                         "area_px": int(sizes[i]), "below_body": bool(yy.min() > body_bottom)})
    out["floating_parts"] = floating
    # the floor counts EVERY part of the drawing (phoenix_hatchling's thin legs are a separate blob from its body;
    #   a floating creature's droplet tip is its lowest point) except drop-shadow-like blobs: flat (>= 2.5x as wide as
    #   tall, <= 8 % of the height) and below the main body
    whole = m.copy()
    for f in floating:
        fx0, fy0, fx1, fy1 = f["bbox_px"]
        if f["below_body"] and (fx1 - fx0) >= 2.5 * (fy1 - fy0) and (fy1 - fy0) <= 0.08 * H:
            whole[fy0:fy1, fx0:fx1] &= ~(lab[fy0:fy1, fx0:fx1] > 0) | (lab[fy0:fy1, fx0:fx1] == main_id)
            f["shadow_like"] = True
    # -- drop shadow pixels (darker than the sheet, low chroma, in the bottom 20 % of the view)
    px = A.astype(np.float64)
    lum = px.mean(2)
    chroma = np.linalg.norm((px - lum[..., None]) - (BG - BG.mean()), axis=2)
    band = np.zeros_like(m); band[y1 - int(0.12 * H):y1 + 1] = True
    dark_neutral = whole & band & (lum < BG.mean() - 3) & (chroma < 10)
    # a drop shadow is a FLAT, WIDE patch at the very bottom; near-black feet / fur of the same tone are not (they
    #   are tall, or merge with the legs): keep only components at least 3x as wide as tall and <= 6 % of the height
    shadow = np.zeros_like(m)
    if dark_neutral.any():
        lab_s, n_s = _label(dark_neutral)
        for i in range(1, n_s + 1):
            yy, xx = np.nonzero(lab_s == i)
            hh, ww = yy.max() - yy.min() + 1, xx.max() - xx.min() + 1
            if ww >= 3 * hh and hh <= 0.06 * H and len(yy) >= 30:
                shadow[yy, xx] = True
    body_ns = whole & ~shadow
    by, bx = np.nonzero(body_ns)
    # -- the mass: the eroded body (drops chains, thin tails, wisps, claws)
    r_er = max(2, int(0.03 * H))
    core = _erode_r(body_ns, r_er)
    if core.sum() < 0.1 * body_ns.sum():
        core = body_ns
    cy_, cx_ = np.nonzero(core)
    out["mass_centroid_px"] = round(float(cx_.mean()), 1)
    out["core_width_px"] = int(cx_.max() - cx_.min() + 1)
    ax = None
    if name in ("front", "back"):
        ax, iou_core = mirror_axis(core, x0 + 0.3 * W, x1 - 0.3 * W)
        _ax2, iou_full = mirror_axis(m, ax - 0.5, ax + 0.5) if ax is not None else (None, 0.0)
        out["mass_axis_px"] = ax
        out["mirror_iou"] = round(iou_full, 3)
        out["mirror_iou_core"] = round(iou_core, 3)
        if ax is not None:   # tone asymmetry: luminance vs its mirror inside both silhouettes (a 3/4 view shows a cheek)
            xm = (2 * ax - xs).round().astype(int)
            ok = (xm >= 0) & (xm < m.shape[1])
            ok[ok] &= m[ys[ok], xm[ok]]
            L = lum[ys[ok], xs[ok]]; Lm = lum[ys[ok], xm[ok]]
            out["tone_asym"] = round(float(np.abs(L - Lm).mean() / max(L.mean(), 1.0)), 3)
            out["half_area_ratio"] = round(float(min((xs < ax).sum(), (xs > ax).sum())
                                                 / max((xs < ax).sum(), (xs > ax).sum(), 1)), 3)
    else:
        out["mass_axis_px"] = round(float(cx_.mean()), 1)
    # -- floor: the lowest CONTACT. A contact column has a long unbroken run up from its bottom pixel (a leg, a body
    #   resting on the floor), not a thin curl or a pool rim. On a front / back whose body core is near-symmetric (eroded mirror IoU >= 0.6) feet come
    #   in pairs about the axis: an UNPAIRED lowest part that is NARROW (< 20 % of the width: gemwing's back-view tail
    #   tip hanging toward the viewer) and > 8 % of the height below the lowest paired contact is skipped; a WIDE one
    #   (petal_cobra's tail lying along the floor) is a real contact and stays. A narrow contact at the END of the view beyond the body mass (a tail tip touching down)
    #   is skipped when a real contact sits within 25 % of the height above it.
    def lowest(mask_):
        r = np.nonzero(mask_.any(1))[0]
        return int(r.max()) if len(r) else None
    raw = lowest(m)
    body_low = lowest(body_ns)
    T = max(10, int(0.10 * H))
    solid = _fill_holes(_erode(_dilate(body_ns, 3), 3)) & _dilate(body_ns, 3)   # crystal / fur gaps closed (3 px)
    bottom = np.full(m.shape[1], -1)
    for x in range(x0, x1 + 1):
        col = solid[:, x]
        r = np.nonzero(col)[0]
        if not len(r):
            continue
        b = r.max()
        k = b
        while k > 0 and col[k - 1]:
            k -= 1
        if b - k + 1 >= T:
            bottom[x] = b
    paired_skip = None
    if ax is not None and out.get("mirror_iou_core", 0) >= 0.6:
        mir = np.full_like(bottom, -1)
        for x in range(x0, x1 + 1):
            xm = int(round(2 * ax - x))
            if 0 <= xm < len(bottom) and bottom[x] >= 0:
                cand = bottom[max(0, xm - 3):xm + 4]
                cand = cand[cand >= 0]
                if len(cand):
                    mir[x] = min(bottom[x], int(cand.max()))
        if (mir >= 0).any():
            lo_pair, lo_all = int(mir.max()), int(bottom.max())
            low_w = int((bottom >= lo_all - 6).sum())          # how wide the unpaired lowest part is
            if lo_all - lo_pair > 0.08 * H and low_w < 0.2 * W:
                paired_skip = {"y_px": lo_all, "paired_y_px": lo_pair, "why": "unpaired part hanging below the feet"}
                bottom = mir
    core_x = (int(cx_.min()), int(cx_.max()))
    skipped = []
    floor = None
    levels = sorted(set(int(b) for b in bottom if b >= 0), reverse=True)
    while levels:
        lvl = levels[0]
        sel = np.nonzero(bottom >= lvl - 6)[0]
        width = len(sel)
        centre = float(sel.mean())
        at_end = centre < core_x[0] or centre > core_x[1] or centre < x0 + 0.15 * W or centre > x1 - 0.15 * W
        rest = [l_ for l_ in levels if l_ < lvl - 6]
        if width < 0.08 * W and at_end and rest and rest[0] >= lvl - 0.25 * H:
            skipped.append({"y_px": lvl, "x_px": [int(sel.min()), int(sel.max())], "why": "narrow contact at the end"})
            bottom[sel] = -1
            levels = rest
            continue
        floor = lvl
        # the contact SPAN: every supported column within 3 % of the height of the floor (front AND hind feet)
        span = np.nonzero(bottom >= lvl - max(8, int(0.03 * H)))[0]
        out["contact_x_px"] = [int(span.min()), int(span.max())]
        break
    if floor is None:
        floor = body_low
    out["floor_candidates"] = {"raw_lowest_px": raw, "body_lowest_px": body_low, "contact_px": floor,
                               "shadow_px": int(shadow.sum()), "support_run_min_px": T,
                               "skipped": skipped + ([paired_skip] if paired_skip else [])}
    out["floor_px"] = floor
    base = by >= (floor - 0.06 * H)
    out["feet_axis_px"] = round(float(0.5 * (bx[base].min() + bx[base].max())) if base.any() else float(bx.mean()), 1)
    if name not in ("front", "back"):
        # side: the axis is where the creature STANDS - the centre of its contact span (tail tips skipped above), not
        #   the mass centroid a fat tail or a wing pulls back
        cx_c = out.get("contact_x_px")
        out["mass_axis_px"] = round(0.5 * (cx_c[0] + cx_c[1]), 1) if cx_c else out["feet_axis_px"]
    # -- dark subject: how much of the filled silhouette the threshold lost (holes / missing fur)
    filled = _fill_holes(_dilate(body, 3)) & ~_dilate(~_fill_holes(_dilate(body, 3)), 3)
    lost = float((filled & ~m).sum()) / max(float(filled.sum()), 1.0)
    near = float((np.linalg.norm(px[m] - BG, axis=1) < 2.0 * bg_tol).mean())
    out["threshold_holes_frac"] = round(lost, 3)
    out["near_bg_frac"] = round(near, 3)
    out["dark_subject"] = bool(lost > 0.12 or near > 0.45)
    return out


FACING_RIGHT_MIN = 0.14     # colour score needed to call image-RIGHT (calibrated on 30 house sheets, 2026-10-11:
                            #   the two right-facing sheets score +0.15 / +0.31; left-facing ones -0.30..+0.12 except
                            #   mystic_snow_spirit +0.23, the one miss); below it the house default (left) stands
FACING_WEAK = 0.05          # |score| under this: no evidence either way


def _top_band(m, frac):
    ys = np.nonzero(m.any(1))[0]
    out = m.copy()
    out[int(ys.min() + frac * (ys.max() - ys.min())):] = False
    return out


def side_facing_colour(A, mf, mb, ms, bins=8):
    """Which way the side view's head points, from COLOUR: the face (eyes, mouth, snout, chest) is what the front
    view shows and the back view does not. Colours over-represented in the top half of the front view against the
    top half of the back view are 'face' colours; the side view's top 70 % is scanned for where they sit. Score > 0 =
    face toward image-right. Returns (score, separation of the two histograms 0..1)."""
    q = (A // (256 // bins)).astype(np.int32)
    code = q[..., 0] * bins * bins + q[..., 1] * bins + q[..., 2]
    mf, mb, ms = _top_band(mf, 0.5), _top_band(mb, 0.5), _top_band(ms, 0.7)
    hf = np.bincount(code[mf], minlength=bins ** 3).astype(float); hf /= max(hf.sum(), 1)
    hb = np.bincount(code[mb], minlength=bins ** 3).astype(float); hb /= max(hb.sum(), 1)
    w = (hf - hb) / (hf + hb + 1e-3)
    ys, xs = np.nonzero(ms)
    wi = w[code[ys, xs]]
    cx = 0.5 * (xs.min() + xs.max()); half = 0.5 * (xs.max() - xs.min() + 1)
    return float((wi * (xs - cx)).sum() / max(np.abs(wi).sum(), 1e-9) / half), float(0.5 * np.abs(hf - hb).sum())


def side_facing_geometry(m):
    """Fallback without front + back views: the head end's OUTER 25 % stands higher than the tail end's (weak: a
    raised tail or a dive fools it). Score > 0 = head toward image-right."""
    ys, xs = np.nonzero(m)
    x0, x1, y1 = xs.min(), xs.max(), ys.max()
    H = y1 - ys.min() + 1
    tops = np.full(x1 - x0 + 1, np.nan)
    for x in range(x0, x1 + 1):
        c = np.nonzero(m[:, x])[0]
        if len(c):
            tops[x - x0] = (y1 - c.min()) / H
    q = max(1, int(0.25 * len(tops)))
    return float(np.nanmean(tops[-q:]) - np.nanmean(tops[:q]))


def palette_fallback(A, BG, masks, k=6):
    """No chip column/row: the dominant colours of the views (k-means on a pixel sample, background excluded)."""
    px = np.concatenate([A[m] for m in masks.values() if m.any()]).astype(np.float64)
    px = px[np.linalg.norm(px - BG, axis=1) > 30]
    if len(px) < k:
        return []
    rng = np.random.default_rng(0)
    px = px[rng.choice(len(px), min(len(px), 40000), replace=False)]
    cent = px[rng.choice(len(px), k, replace=False)]
    for _ in range(25):
        d = ((px[:, None, :] - cent[None]) ** 2).sum(2)
        lab = d.argmin(1)
        cent = np.array([px[lab == i].mean(0) if (lab == i).any() else cent[i] for i in range(k)])
    share = np.bincount(lab, minlength=k) / len(lab)
    order = np.argsort(-share)
    return [{"rgb": [int(round(v)) for v in cent[i]], "share": round(float(share[i]), 3)} for i in order]


def birefnet_strip_mattes(sheet_path, strips, A_shape, margin=12):
    """BiRefNet mattes of column strips [(a, b), ...] of the sheet, through meshgen's ComfyUI (when it is running).
    Returns a list of full-sheet bool masks, or None when ComfyUI is down / meshgen is not importable."""
    try:
        sys.path.insert(0, REPO)
        from meshgen import framing
        from meshgen import config as mconfig
        from meshgen.comfyui_client import ComfyUIClient
    except Exception:
        return None
    cfg = mconfig.load()
    client = ComfyUIClient(cfg, log=lambda *_a: None)
    try:
        if not client.is_running():
            return None
    except Exception:
        return None
    import tempfile
    Hh, Ww = A_shape[:2]
    im = Image.open(sheet_path).convert("RGB")
    out = []
    with tempfile.TemporaryDirectory(prefix="intake_matte_") as td:
        paths = []
        for i, (a, b) in enumerate(strips):
            x0, x1 = max(0, a - margin), min(Ww, b + margin)
            p = os.path.join(td, "strip%d.png" % i)
            im.crop((x0, 0, x1, Hh)).save(p)
            paths.append((p, x0, x1))
        try:
            masks = framing.birefnet_mattes([p for p, _a, _b in paths], config=cfg, client=client)
        except Exception:
            return None
        for (_p, x0, x1), mk in zip(paths, masks):
            full = np.zeros((Hh, Ww), bool)
            full[:, x0:x1] = mk
            out.append(full)
    return out


def dark_strip(big_strip, A, BG, bg_tol):
    """Is the subject in this strip dark on a dark sheet? The threshold then loses fur / armour into the background:
    the filled silhouette has big holes, or most subject pixels sit near the background colour."""
    if not big_strip.any():
        return False, 0.0, 0.0
    filled = _fill_holes(_dilate(big_strip, 3))
    filled &= ~_dilate(~filled, 3)
    lost = float((filled & ~big_strip).sum()) / max(float(filled.sum()), 1.0)
    near = float((np.linalg.norm(A[big_strip].astype(np.float64) - BG, axis=1) < 2.0 * bg_tol).mean())
    return bool(lost > 0.12 or near > 0.45), round(lost, 3), round(near, 3)


def smart_pass(o, A, BG, fg0, sheet_path):
    """Pass 1 analysis -> (flags for pass 2, auto flags, extra intake fields, notes, the pass-2 foreground)."""
    Hh, Ww, _ = A.shape
    notes, auto = [], {}
    flags = dict(views=o["views"], bg_tol=o["bg_tol"], base_frac=o["base_frac"], ortho_views=o["ortho_views"],
                 region=o["region"], assign=o["assign"], cuts=o["cuts"], floor=dict(o["floor"]), axis=dict(o["axis"]))
    fg = fg0.copy()
    # 1. clutter (title, rules, framed panels) out of the view search
    clutter, remove = None, np.zeros_like(fg0)
    if o["region"] is None:
        remove, clutter = clutter_mask(fg0, Hh, Ww)
        fg = fg0 & ~remove
        if clutter["panels"] or clutter["title_blobs"]:
            notes.append("removed %d framed panel(s), %d title blob(s) and %d rule line(s) before the split (was "
                         "--region)" % (len(clutter["panels"]), clutter["title_blobs"], clutter["rules"]))
    flags["fg_full"] = fg0
    p1 = measure(A, BG, fg, flags)
    if o["cuts"] is None and o["assign"] is None:      # text columns (unframed detail panels) out before anything else
        texty = clutter_segments(p1["_big"], p1["_segs"], fg_text=fg0, text_only=True)
        if texty and len(texty) < len(p1["_segs"]):
            for a, b in texty:
                remove[:, a:b] |= fg[:, a:b]
            fg = fg0 & ~remove
            notes.append("dropped %d text column(s) (detail panels without frames): x %s"
                         % (len(texty), ", ".join("%d-%d" % ab for ab in texty)))
            p1 = measure(A, BG, fg, flags)
    # 2. dark subject on a dark sheet: BiRefNet matte of that strip, UNION the threshold (glows and pale parts the
    #   matte may soften stay), background pockets the matte filled (a horn ring) carved back out
    matte_used, dark_info = [], []
    for a, b in p1["_segs"]:
        strip = np.zeros_like(fg); strip[:, a:b] = fg[:, a:b]          # every threshold px: ink outlines count
        is_dark, lost, near = dark_strip(strip, A, BG, o["bg_tol"])
        if is_dark:
            dark_info.append({"x_px": [int(a), int(b)], "threshold_holes_frac": lost, "near_bg_frac": near})
    if dark_info:
        mattes = (birefnet_strip_mattes(sheet_path, [tuple(d["x_px"]) for d in dark_info], A.shape)
                  if o["matte"] else None)
        if mattes:
            try:
                sys.path.insert(0, REPO)
                from meshgen import framing as _fr
            except Exception:
                _fr = None
            fg = fg.copy()
            for d, mk in zip(dark_info, mattes):
                a, b = d["x_px"]
                keep = np.zeros_like(fg); keep[:, a:b] = True
                mk = mk & keep & ~remove
                if _fr is not None:
                    rep = _fr.enclosed_background(A[:, a:b].astype(np.uint8), mk[:, a:b], bg=BG)
                    d["carved_px"] = rep["area_px"]
                    mk[:, a:b] = _fr.carve_enclosed(mk[:, a:b], rep)
                fg = fg | mk
                matte_used.append(d["x_px"])
            notes.append("dark subject on a dark sheet at x %s (the threshold lost %s of the filled silhouette): "
                         "BiRefNet matte merged in, enclosed background carved (%s px)"
                         % ("; ".join("%d-%d" % tuple(d["x_px"]) for d in dark_info),
                            ", ".join("%d%%" % (100 * d["threshold_holes_frac"]) for d in dark_info),
                            ", ".join(str(d.get("carved_px", 0)) for d in dark_info)))
            p1 = measure(A, BG, fg, flags)
        else:
            notes.append("dark subject on a dark sheet at x %s (threshold holes %s): no BiRefNet matte (%s); the "
                         "floor and outline may sit inside the drawing - check the overlay, or rerun with --bg-tol 12"
                         % ("; ".join("%d-%d" % tuple(d["x_px"]) for d in dark_info),
                            ", ".join("%d%%" % (100 * d["threshold_holes_frac"]) for d in dark_info),
                            "--no-matte" if not o["matte"] else "meshgen's ComfyUI is not running"))
    # 3. segments that are not views (a panel column without frames, a label) -> dropped
    if o["cuts"] is None and o["assign"] is None:
        small = clutter_segments(p1["_big"], p1["_segs"], fg_text=fg0)
        if small and len(small) < len(p1["_segs"]):
            for a, b in small:
                remove[:, a:b] |= fg[:, a:b]
            fg = fg & ~remove
            notes.append("dropped %d non-view segment(s) (detail panels / labels: short, or standing above the "
                         "views' floor band): x %s" % (len(small), ", ".join("%d-%d" % ab for ab in small)))
            p1 = measure(A, BG, fg, flags)
    # 4. fused views -> a geodesic split
    if o["cuts"] is None and o["assign"] is None and len(p1["_segs"]) < len(o["views"]):
        cuts, detail = suggest_cuts(p1["_big"], p1["_segs"], len(o["views"]))
        if cuts:
            big_ = p1["_big"]
            pieces = []
            for a, b in p1["_segs"]:
                inside = [c for c in cuts if a < c < b]
                strip = np.zeros_like(big_); strip[:, a:b] = big_[:, a:b]
                for c in inside:                   # split each fused strip at its nominal cut(s), left to right
                    left, right, how = split_fused(strip, c, A, BG)
                    pieces.append(left)
                    strip = right
                pieces.append(strip)
            vm = {name: pc for name, pc in zip(o["views"], pieces)}
            if len(vm) == len(o["views"]):
                flags["view_masks"] = vm
                gut = [int((p1["_segs"][i][1] + p1["_segs"][i + 1][0]) // 2) for i in range(len(p1["_segs"]) - 1)]
                auto["cuts_nominal"] = sorted(gut + cuts)
                auto["split"] = [{"x_px": d["x_px"], "segment": d["segment"], "column_px": d["column_px"]}
                                 for d in detail]
                notes.append("views fused by touching parts: split at x %s by a watershed along the weakest "
                             "contact (the plain-column equivalent is --cuts %s; --assign / --cuts override)"
                             % (",".join(map(str, cuts)), ",".join(map(str, auto["cuts_nominal"]))))
                p1 = measure(A, BG, fg, flags)
    elif len(p1["_segs"]) > len(o["views"]):
        notes.append("%d extra foreground segment(s) right of the views (unnamed_extra_segments_px): check the "
                     "overlay; a view split in two needs --cuts" % (len(p1["_segs"]) - len(o["views"])))
    masks = p1["_masks"]
    checks = {name: analyse_view(name, m, A, BG, o["bg_tol"], Hh) for name, m in masks.items()}
    # 5. floor + axis per view
    for name, c in checks.items():
        if c.get("empty"):
            continue
        fc = c["floor_candidates"]
        if name not in o["floor"] and fc["contact_px"] is not None and fc["contact_px"] != fc["raw_lowest_px"]:
            flags["floor"][name] = int(fc["contact_px"])
            auto.setdefault("floor", {})[name] = int(fc["contact_px"])
            why = []
            if fc["shadow_px"]:
                why.append("a drop shadow (%d px)" % fc["shadow_px"])
            if fc["body_lowest_px"] is not None and fc["body_lowest_px"] > fc["contact_px"]:
                why.append("a part with no leg-like support hanging %d px lower (tail curl / pool / unpaired tail)"
                           % (fc["body_lowest_px"] - fc["contact_px"]))
            if fc["skipped"]:
                why.append("%d narrow end contact(s) (tail tip)" % len(fc["skipped"]))
            if any(p["below_body"] for p in c["floating_parts"]):
                why.append("a floating part below the body")
            notes.append("%s floor %d px = the lowest body contact; ignored %s (raw lowest row %d)"
                         % (name, fc["contact_px"], ", ".join(why) or "thin bottom rows", fc["raw_lowest_px"]))
        if name not in o["axis"] and c.get("mass_axis_px") is not None:
            ax = float(c["mass_axis_px"])
            flags["axis"][name] = ax
            auto.setdefault("axis", {})[name] = ax
            if abs(ax - c["feet_axis_px"]) > 6:
                notes.append("%s axis %.1f px (%s); the feet-base rule says %.1f"
                             % (name, ax, "mirror-symmetry of the body mass" if name in ("front", "back")
                                else "centre of the floor contact span", c["feet_axis_px"]))
    # 6. 3/4 views -> ortho views
    sus = {}
    if "front" in checks and "back" in checks and not checks["front"].get("empty") and not checks["back"].get("empty"):
        wf = checks["front"]["core_width_px"]; wb = checks["back"]["core_width_px"]   # eroded: tails / claws out
        ratio = float(min(wf, wb) / max(wf, wb))
    else:
        ratio = 1.0
    # A 3/4 view is asymmetric in OUTLINE and in TONE (one cheek / flank shows, the far one hides). An asymmetric
    #   POSE (a swung tail, a raised arm) breaks only the outline, so both are required. Calibrated on the 30 house
    #   sheets (2026-10-11): the 8 fronts the orchestrator rejected as 3/4 score mirror IoU 0.39-0.71 with tone
    #   asymmetry 0.30-0.68; the accepted ortho fronts/backs fall outside that box except ember_stump's back.
    for name in ("front", "back"):
        c = checks.get(name)
        if not c or c.get("empty"):
            continue
        why = []
        if c["mirror_iou"] < 0.75 and c.get("tone_asym", 0) >= 0.30:
            why = ["mirror IoU %.2f" % c["mirror_iou"], "tone asymmetry %.2f" % c["tone_asym"]]
        c["three_quarter_suspect"] = bool(why)
        c["three_quarter_why"] = why
        if why:
            sus[name] = why
    if ratio < 0.85:
        notes.append("front and back body widths disagree (%.2f, eroded silhouettes): one of them may be drawn 3/4 or "
                     "in perspective" % ratio)
    if o["ortho_views"] is None:
        cand = [v for v in ("front", "back") if v in checks and v not in sus and not checks[v].get("empty")]
        if cand:
            pick = "front" if "front" in cand else cand[0]
        else:       # both look 3/4: the less asymmetric one, flagged
            pick = min((v for v in ("front", "back") if v in checks), key=lambda v: checks[v].get("tone_asym", 1))
        ov = [pick] + (["side"] if "side" in checks else [])
        auto["ortho_views"] = ov
        flags["ortho_views"] = ov
        if sus:
            notes.append("probable 3/4 / perspective view(s): %s -> ortho_views %s%s (a 3/4 SIDE is not "
                         "auto-detected: check it)" % ("; ".join("%s (%s)" % (k, ", ".join(v)) for k, v in sus.items()),
                                                       ",".join(ov), "" if cand else " - BOTH look 3/4, the "
                                                       "overlay is indicative only"))
    # 7. side facing
    facing = None
    if "side" in masks and masks["side"].any():
        if "front" in masks and "back" in masks and masks["front"].any() and masks["back"].any():
            score, sep = side_facing_colour(A, masks["front"], masks["back"], masks["side"])
            cue = "colour"
        else:
            score, sep = side_facing_geometry(masks["side"]), None
            cue = "geometry"
        guess = "right" if score >= FACING_RIGHT_MIN else "left"
        conf = round(min(1.0, abs(score) / 0.3), 2)
        weak = abs(score) < FACING_WEAK or (0 < score < FACING_RIGHT_MIN and score >= FACING_WEAK)
        if o["side_facing"]:
            facing = {"facing": o["side_facing"], "source": "flag"}
        else:
            facing = {"facing": guess, "source": "auto" if not weak else "default"}
        facing.update({"guess": guess, "score": round(score, 3), "confidence": conf, "cue": cue,
                       "front_back_separation": None if sep is None else round(sep, 3),
                       "rule": "score > 0 = face toward image-right; 'right' needs >= %.2f, else the house default "
                               "(left)" % FACING_RIGHT_MIN})
        facing["meshgen_camera"] = facing["facing"]   # image-left head = the creature's LEFT flank = meshgen 'left'
        notes.append("side view faces image-%s (%s, %s score %+.2f) -> meshgen view '%s'%s"
                     % (facing["facing"], facing["source"], cue, score, facing["meshgen_camera"],
                        "; WEAK evidence - LOOK at the head before the proxy runs (--side-facing to set it)"
                        if weak and not o["side_facing"] else ""))
    return flags, auto, {"view_checks": checks, "side_facing": facing, "clutter": clutter, "matte_strips": matte_used,
                         "dark_strips": dark_info,
                         "front_back_width_ratio": round(ratio, 3)}, notes, fg


def smart_fields(o, A, BG, res_m, flags, auto, extra, notes, out_dir):
    """The smart-only keys of intake.json (+ view_masks.png)."""
    views = res_m["views"]
    masks = res_m["_masks"]
    checks = extra["view_checks"]
    # heights: per view and the worst pairwise disagreement
    hs = {k: v["height_units"] for k, v in views.items()}
    pairs = []
    ks = list(hs)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            a, b = hs[ks[i]], hs[ks[j]]
            pairs.append({"views": [ks[i], ks[j]], "diff_pct": round(100 * abs(a - b) / max(a, b), 1)})
    worst = max(pairs, key=lambda p: p["diff_pct"]) if pairs else None
    if "front" in hs and "side" in hs:
        fs = round(100 * (hs["side"] - hs["front"]) / hs["front"], 1)
        notes.append("front-vs-side height: front %.2f u, side %.2f u (%+.1f%%)%s"
                     % (hs["front"], hs["side"], fs, " - over 3 %: reconcile them and say how" if abs(fs) > 3 else ""))
    # palette fallback
    pal = None
    if not res_m["swatches"]:
        relaxed, _drop = find_swatches(A, BG, flags.get("fg_full", np.linalg.norm(A - BG, axis=2) > o["bg_tol"]),
                                       res_m["_segs"], o["views"], o["bg_tol"], max_std=45)
        if relaxed:
            pal = {"source": "relaxed_chips", "note": "gradient-shaded chips (uniformity 45 instead of 12); rgb = "
                                                        "the median of each chip's inner disc", "chips": relaxed}
            notes.append("no flat palette chips: %d gradient-shaded chips found with a relaxed search "
                         "(palette_fallback)" % len(relaxed))
        else:
            pal = {"source": "kmeans_views", "note": "no chips on the sheet: the dominant colours of the views - "
                                                       "sample the drawn surfaces before citing these",
                   "colours": palette_fallback(A, BG, masks)}
            notes.append("no palette chips at all: palette_fallback = k-means colours of the views (sample the "
                         "surfaces before citing them)")
    # view masks (labels 1..n in --views order)
    lab = np.zeros(A.shape[:2], np.uint8)
    for i, name in enumerate(o["views"]):
        if name in masks:
            lab[masks[name] & (lab == 0)] = i + 1
    Image.fromarray(lab).save(os.path.join(out_dir, "view_masks.png"))
    vc = {}
    for name, c in checks.items():
        vc[name] = {k: v for k, v in c.items() if not k.startswith("_")}
    return {"smart": True, "auto_flags": auto,
            "auto_flags_cli": auto_cli(auto),
            "side_facing": extra["side_facing"], "view_checks": vc,
            "front_back_width_ratio": extra["front_back_width_ratio"],
            "height_check": {"heights_units": hs, "pairs": pairs, "worst": worst},
            "clutter_removed": extra["clutter"], "matte_strips_px": extra["matte_strips"],
            "dark_strips": extra["dark_strips"],
            "palette_fallback": pal,
            "view_masks": {"path": "view_masks.png", "labels": {n: i + 1 for i, n in enumerate(o["views"])},
                           "rule": "label image at sheet px: the silhouette each view was measured on"},
            "intake_notes": notes}


def auto_cli(auto):
    parts = []
    if "cuts" in auto:
        parts.append("--cuts " + ",".join(map(str, auto["cuts"])))
    if "floor" in auto:
        parts.append("--floor " + ",".join("%s=%d" % kv for kv in auto["floor"].items()))
    if "axis" in auto:
        parts.append("--axis " + ",".join("%s=%g" % kv for kv in auto["axis"].items()))
    if "ortho_views" in auto:
        parts.append("--ortho-views " + ",".join(auto["ortho_views"]))
    return " ".join(parts)


def draw_overlay(im, res, out_dir):
    ov = im.copy()
    d = ImageDraw.Draw(ov)
    for name, v in res["views"].items():
        x0, y0, x1, y1 = v["bbox_px"]
        d.rectangle([x0, y0, x1, y1], outline=(0, 230, 255), width=2)
        d.line([v["axis_x_px"], y0 - 10, v["axis_x_px"], y1 + 10], fill=(255, 0, 255), width=1)
        d.line([x0 - 10, v["floor_y_px"], x1 + 10, v["floor_y_px"]], fill=(255, 255, 0), width=1)
        tag = "%s  H %.2f u" % (name, v["height_units"])
        if res.get("smart"):
            c = res["view_checks"].get(name, {})
            if c.get("three_quarter_suspect"):
                tag += "  3/4?"
            if name in res["ortho_frame"]["views"]:
                tag += "  ORTHO"
            fc = c.get("floor_candidates") or {}
            for key, col in (("raw_lowest_px", (255, 120, 0)), ("body_lowest_px", (255, 200, 0))):
                y = fc.get(key)
                if y is not None and y != v["floor_y_px"]:
                    d.line([x0, y, x1, y], fill=col, width=1)
        d.text((x0, y0 - 14), tag, fill=(0, 230, 255))
    for s in res["swatches"]:
        cx, cy = s["centre_px"]
        d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], outline=(0, 230, 255), width=2)
        d.text((cx + 20, cy - 6), str(s["rgb"]), fill=(235, 235, 235))
    pal = res.get("palette_fallback") or {}
    for s in pal.get("chips", []):
        cx, cy = s["centre_px"]
        d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], outline=(255, 160, 0), width=2)
    if res.get("smart"):
        sf = res.get("side_facing") or {}
        if sf and "side" in res["views"]:
            x0, y0, x1, _y1 = res["views"]["side"]["bbox_px"]
            d.text((x0, y0 - 28), "head -> image-%s (%s %.2f)" % (sf["facing"], sf["source"], sf["confidence"]),
                   fill=(255, 160, 0))
        y = 8
        for n_ in res["intake_notes"][:12]:
            d.text((8, im.height - 14 * (len(res["intake_notes"][:12]) + 1) + y), "- " + n_[:180], fill=(255, 220, 120))
            y += 14
    ov.save(os.path.join(out_dir, "intake_overlay.png"))


# ======================================================================================================================
# main
# ======================================================================================================================
def main(argv):
    o = parse_args(argv)
    SHEET, UNIT = o["sheet"], o["unit"]
    im = Image.open(SHEET).convert("RGB")
    A = np.asarray(im).astype(np.int32)
    Hh, Ww, _ = A.shape
    border = np.concatenate([A[:8].reshape(-1, 3), A[-8:].reshape(-1, 3), A[:, :8].reshape(-1, 3), A[:, -8:].reshape(-1, 3)])
    BG = np.median(border, 0)
    fg = np.linalg.norm(A - BG, axis=2) > o["bg_tol"]
    flags = dict(views=o["views"], bg_tol=o["bg_tol"], base_frac=o["base_frac"], ortho_views=o["ortho_views"],
                 region=o["region"], assign=o["assign"], cuts=o["cuts"], floor=o["floor"], axis=o["axis"])
    if o["smart"]:
        flags, auto, extra, notes, fg = smart_pass(o, A, BG, fg, SHEET)
    res_m = measure(A, BG, fg, flags)
    views = res_m["views"]

    out_dir = o["out_dir"] or os.path.join(ROOT, "design", "reference", UNIT)
    os.makedirs(out_dir, exist_ok=True)
    res = {"unit": UNIT, "sheet": os.path.relpath(os.path.abspath(SHEET), ROOT).replace("\\", "/"), "size_px": [Ww, Hh],
           "background_rgb": [int(v) for v in BG], "bg_tol": o["bg_tol"], "views": views,
           "ortho_frame": res_m["ortho_frame"], "unnamed_extra_segments_px": res_m["unnamed_extra_segments_px"],
           "swatches": res_m["swatches"], "swatch_rejects_off_column": res_m["swatch_rejects_off_column"],
           "base_frac": o["base_frac"], "axis_overrides": o["axis"],
           "units_rule": "1 sheet unit = 100 px; per view z = (floor_y - y) / 100, x = (x - axis_x) / 100",
           "caveat": "automatic split: VERIFY intake_overlay.png before dispatch (overlapping views or a floor shadow "
                     "fuse / extend a view; rerun with --views or --bg-tol). A DARK subject on a dark sheet (shadowbun) "
                     "loses its outline into the background: the floor line can sit high by ~15 px - check the soles"}
    if o["smart"]:
        res.update(smart_fields(o, A, BG, res_m, flags, auto, extra, notes, out_dir))
    json.dump(res, open(os.path.join(out_dir, "intake.json"), "w"), indent=1)
    draw_overlay(im, res, out_dir)
    print("INTAKE", json.dumps({"views": {k: {kk: v[kk] for kk in ("bbox_px", "axis_x_px", "floor_y_px", "height_units")}
                                          for k, v in views.items()}, "swatches": [s["rgb"] for s in res["swatches"]],
                                "extra_segments": res["unnamed_extra_segments_px"]}))
    if o["smart"]:
        print("AUTO_FLAGS", res["auto_flags_cli"] or "(none)")
        for n_ in res["intake_notes"]:
            print("NOTE", n_)


# ======================================================================================================================
# self-test (no repo files touched): python -P sheet_intake.py --selftest
# ======================================================================================================================
def _synthetic_sheet(path):
    """A house-style sheet in miniature: title, three views (the back's tail touching the side's paw = fused views),
    a drop shadow under the front, a framed detail panel with a row of five palette chips; the side faces LEFT and the
    face colour (yellow eyes) shows on the front and on the side's left end, never on the back."""
    W, H = 1300, 700
    BGc = (36, 36, 38)
    im = Image.new("RGB", (W, H), BGc)
    d = ImageDraw.Draw(im)
    for i in range(9):                                       # title letters
        d.rectangle([30 + 46 * i, 20, 62 + 46 * i, 70], fill=(220, 220, 220))
    body, leg, eye = (90, 140, 210), (60, 100, 170), (250, 220, 40)
    d.ellipse([60, 560, 300, 585], fill=(22, 22, 24))        # drop shadow under the front
    for cx in (180, 480):                                    # front, back: body + two legs, symmetric
        d.ellipse([cx - 90, 200, cx + 90, 470], fill=body)
        d.rectangle([cx - 60, 440, cx - 25, 570], fill=leg)
        d.rectangle([cx + 25, 440, cx + 60, 570], fill=leg)
        d.ellipse([cx - 50, 140, cx + 50, 240], fill=body)   # head
    for ex in (155, 205):
        d.ellipse([ex - 10, 175, ex + 10, 195], fill=eye)    # the face shows on the front only
    d.polygon([(450, 380), (560, 430), (690, 568), (692, 575), (600, 520), (430, 420)], fill=body)  # back tail, rooted in the body, its tip on the side paw
    # side view: body, head on the LEFT (with the eye), legs, tail trailing right and low
    d.ellipse([700, 260, 1000, 470], fill=body)
    d.ellipse([650, 170, 780, 280], fill=body)
    d.ellipse([672, 205, 692, 225], fill=eye)
    for lx in (730, 790, 900, 950):
        d.rectangle([lx, 440, lx + 30, 570], fill=leg)
    d.polygon([(990, 380), (1080, 470), (1100, 520), (1060, 500), (980, 420)], fill=body)
    d.rectangle([686, 560, 732, 572], fill=leg)              # the paw the back's tail touches
    d.rectangle([1120, 40, 1290, 680], outline=(140, 140, 140), width=2)       # framed panel
    d.ellipse([1150, 80, 1260, 260], fill=(200, 120, 90))   # panel image
    for i, c in enumerate([(240, 240, 240), (90, 140, 210), (60, 100, 170), (250, 220, 40), (200, 120, 90)]):
        d.ellipse([1140 + 30 * i, 620, 1162 + 30 * i, 642], fill=c)
    im.save(path)


def selftest():
    import tempfile
    fails = []

    def check(name, ok, detail=""):
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            fails.append(name)
    with tempfile.TemporaryDirectory(prefix="intake_selftest_") as td:
        sheet = os.path.join(td, "toy-character-sheet.png")
        _synthetic_sheet(sheet)
        out = os.path.join(td, "out")
        main([sheet, "toy", "--out-dir", out, "--no-matte"])
        r = json.load(open(os.path.join(out, "intake.json")))
        v = r["views"]
        check("three views", list(v) == ["front", "back", "side"], str({k: x["bbox_px"] for k, x in v.items()}))
        check("title + panel kept out", v["front"]["bbox_px"][1] >= 130 and v["side"]["bbox_px"][2] <= 1110,
              "front top %d, side right %d" % (v["front"]["bbox_px"][1], v["side"]["bbox_px"][2]))
        check("fused back/side split", 640 <= v["back"]["bbox_px"][2] <= 700 and v["side"]["bbox_px"][0] >= 640,
              "back %s side %s" % (v["back"]["bbox_px"], v["side"]["bbox_px"]))
        check("front floor at the feet, shadow ignored", abs(v["front"]["floor_y_px"] - 570) <= 3,
              "floor %d" % v["front"]["floor_y_px"])
        check("front axis by symmetry", abs(v["front"]["axis_x_px"] - 180) <= 2, "axis %.1f" % v["front"]["axis_x_px"])
        check("side faces image-left", r["side_facing"]["facing"] == "left", str(r["side_facing"]["score"]))
        check("five chips", len(r["swatches"]) == 5, str([s_["rgb"] for s_ in r["swatches"]]))
        check("view_masks written", os.path.exists(os.path.join(out, "view_masks.png")))
        check("notes for the brief", len(r["intake_notes"]) >= 3, str(len(r["intake_notes"])))
        legacy = os.path.join(td, "legacy")
        main([sheet, "toy", "--out-dir", legacy, "--no-smart", "--cuts", "320,640"])
        lj = json.load(open(os.path.join(legacy, "intake.json")))
        check("--no-smart writes no smart keys", "intake_notes" not in lj and "view_masks" not in lj
              and not os.path.exists(os.path.join(legacy, "view_masks.png")))
    print("SELFTEST %s (%d failed)" % ("PASS" if not fails else "FAIL", len(fails)))
    return not fails


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main(sys.argv[1:])
