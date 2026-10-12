"""Sheet proxy: a Pixal3D 3-view mesh of the character sheet, used as a 3D REFERENCE ONLY (option ii, 2026-10-11).

    python -P sheet_proxy.py <unit> [--intake <intake.json>] [--out-dir <dir>] [--seed 56] [--views front,back,side]
        [--via auto|service|direct] [--min-free-gb 8.6] [--glb <existing.glb>] [--no-render] [--no-write-intake]

The procedural build stays the deliverable (regions, rig, symmetric rest pose, digest determinism). The proxy gives the
build lane what a flat sheet cannot: depth. It is generated ONCE, frozen by sha, normalised into the sheet's own frame
and scored against the sheet, and its numbers go into intake.json's `proxy` block:

    1. cut   front / back / side out of the sheet on ONE shared frame: one px scale, every view's floor line on the
             same row (the intake's view_masks.png silhouettes, holes filled, background pockets the subject encloses
             carved back out); RGBA cut-outs, so meshgen uses them as the matte. The side goes in as meshgen 'left'
             when the head points image-left (intake side_facing), 'right' otherwise. A front / back the intake
             flagged 3/4 is left out (a clean back mirrored stands in for a 3/4 front: same ortho silhouette).
    2. gen   meshgen Pixal3D multi-view, seed 56, options.shared_frame (one scale + vertical centre for every view -
             the trial's fix for long quadrupeds coming out short). Needs ~8.6 GB of free VRAM: below that it exits 3
             with a message and generates nothing. --via service uses the running meshgen (when it advertises
             shared_frame); --via direct runs meshgen's backend in this process against the running ComfyUI (when
             the service is idle) - auto picks the first that works.
    3. freeze the glb -> <out>/<unit>_proxy.glb + its sha256 (the build cites the sha, never regenerates).
    4. normalise into the sheet frame: Z up, front -Y, floor at z 0, bbox centred, ONE uniform scale so the front
             silhouette height equals the sheet's front height (the orientation is verified against the 24 proper
             rotations; no mirrors, so a left/right swap cannot hide).
    5. score per view against the sheet silhouettes: IoU (best translation, no per-view scale), shape IoU, row
             width error, bbox; side length / front height and front width / height, mesh vs sheet.
             GATE: mean IoU >= 0.75 AND side length within 5 % -> 'reliable'; else 'proxy unreliable, work from
             the sheet' (the block is still written, so the lane sees why).
    6. sections at 8 height bands (exact plane slices: x / y extents, centre, in sheet units) + landmarks (top, the
             front-most and rear-most points, the widest row, depth at the intake's half-width rows), and a render:
             <out>/<unit>_proxy_vs_sheet.png (sheet | proxy, front / side / back, with each view's IoU).

The proxy copies the sheet's POSE (a swung tail, staggered legs). The house rest pose is symmetric: cite the proxy for
torso / head / limb depths and cross-sections, never for an asymmetric pose. The SHEET IS THE SPEC: where the proxy and
the sheet disagree, the sheet wins.

The metric functions (view_masks, register, view_metrics) are imported by the scaffolded <unit>_compose.py for the
numeric sheet gate, so the build and the proxy are scored the same way.
"""
import hashlib, itertools, json, os, shutil, subprocess, sys, time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPO = os.path.normpath(os.path.join(ROOT, "..", ".."))
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
VPY = os.path.join(REPO, "service", ".venv", "Scripts", "python.exe")
MESHGEN = "http://127.0.0.1:8902"
PPU = 100.0                       # sheet px per sheet unit
SEED = 56
MIN_FREE_GB = 8.6                 # Pixal3D 3-view peaks ~11.2 of 11.9 GB with ~2.6 GB of desktop already resident
GATE_IOU = 0.75
GATE_SIDE_LEN = 0.05
SECTION_BANDS = (0.06, 0.18, 0.30, 0.42, 0.54, 0.66, 0.78, 0.90)
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ======================================================================================================================
# sheet silhouettes (shared with the compose gate)
# ======================================================================================================================
def load_intake(path):
    intake = json.load(open(path))
    return intake, os.path.dirname(os.path.abspath(path))


def sheet_path(intake):
    return os.path.join(ROOT, intake["sheet"])


def _fill_holes(m):
    from scipy import ndimage
    return ndimage.binary_fill_holes(m)


def view_masks(intake, intake_dir, sheet_rgb=None):
    """{view: full-sheet bool silhouette}. Smart intakes ship view_masks.png (the exact silhouettes they measured);
    an older intake falls back to the threshold rule inside each view's bbox. Holes are filled, then background
    pockets the subject encloses (a horn ring) are carved back out (meshgen.framing.enclosed_background)."""
    from PIL import Image
    if sheet_rgb is None:
        sheet_rgb = np.asarray(Image.open(sheet_path(intake)).convert("RGB"))
    H, W, _ = sheet_rgb.shape
    vm = intake.get("view_masks")
    out = {}
    if vm and os.path.exists(os.path.join(intake_dir, vm["path"])):
        lab = np.asarray(Image.open(os.path.join(intake_dir, vm["path"])))
        for name, k in vm["labels"].items():
            out[name] = lab == k
    else:
        bg = np.array(intake["background_rgb"], float)
        fg = np.linalg.norm(sheet_rgb.astype(float) - bg, axis=2) > float(intake["bg_tol"])
        for name, v in intake["views"].items():
            x0, y0, x1, y1 = v["bbox_px"]
            m = np.zeros((H, W), bool)
            m[y0:y1, x0:x1] = fg[y0:y1, x0:x1]
            out[name] = m
    sys.path.insert(0, REPO)
    try:
        from meshgen import framing
    except Exception:
        framing = None
    bg = np.array(intake["background_rgb"], float)
    for name, m in out.items():
        if not m.any():
            continue
        f = _fill_holes(m)
        if framing is not None:
            rep = framing.enclosed_background(sheet_rgb, f, bg=bg)
            f = framing.carve_enclosed(f, rep)
        out[name] = f
    return out


def bbox(m):
    ys, xs = np.nonzero(m)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def iou(a, b):
    u = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b)) / u if u else 0.0


def register(mesh_m, ref_m):
    """Both masks on one canvas at the translation that maximises their overlap (FFT cross-correlation). Scale is NOT
    searched: cross-view scale errors and proportion errors still count; only where each drawing sits is removed."""
    from scipy.signal import fftconvolve
    ax0, ay0, ax1, ay1 = bbox(mesh_m); bx0, by0, bx1, by1 = bbox(ref_m)
    a = mesh_m[ay0:ay1, ax0:ax1].astype(np.float32); b = ref_m[by0:by1, bx0:bx1].astype(np.float32)
    corr = fftconvolve(b, a[::-1, ::-1], mode="full")
    r, c = np.unravel_index(np.argmax(corr), corr.shape)
    dy, dx = r - (a.shape[0] - 1), c - (a.shape[1] - 1)
    oy, ox = min(0, dy), min(0, dx)
    Hc = max(b.shape[0], dy + a.shape[0]) - oy; Wc = max(b.shape[1], dx + a.shape[1]) - ox
    A = np.zeros((Hc, Wc), bool); B = np.zeros((Hc, Wc), bool)
    B[-oy:-oy + b.shape[0], -ox:-ox + b.shape[1]] = b > 0
    A[dy - oy:dy - oy + a.shape[0], dx - ox:dx - ox + a.shape[1]] = a > 0
    return A, B


def _norm_grid(m, grid=256):
    x0, y0, x1, y1 = bbox(m)
    c = m[y0:y1, x0:x1]
    h, w = c.shape
    if w >= h:
        ow, oh = grid, max(1, round(grid * h / w))
    else:
        oh, ow = grid, max(1, round(grid * w / h))
    ri = np.clip((np.arange(oh) * (h / oh)).astype(int), 0, h - 1)
    ci = np.clip((np.arange(ow) * (w / ow)).astype(int), 0, w - 1)
    out = np.zeros((grid, grid), bool)
    r0, c0 = (grid - oh) // 2, (grid - ow) // 2
    out[r0:r0 + oh, c0:c0 + ow] = c[ri][:, ci]
    return out, w / h


def row_error(A, B):
    """Per-row silhouette width (filled px / 100 = sheet units) vs the sheet, mean abs error over the rows either
    covers; returned in units and as % of the sheet's widest row."""
    rows = np.nonzero(A.any(1) | B.any(1))[0]
    wa = A[rows].sum(1) / PPU; wb = B[rows].sum(1) / PPU
    mae = float(np.abs(wa - wb).mean())
    return mae, 100.0 * mae / max(B.sum(1).max() / PPU, 1e-9)


def view_metrics(mesh_m, ref_m, in_frame=False):
    """IoU (best translation), shape IoU (both cropped + fitted to a 256 grid), row width error, bbox sizes.
    in_frame=True also reports the IoU exactly as placed (masks already on one canvas: the build's ortho renders)."""
    A, B = register(mesh_m, ref_m)
    ga, aa = _norm_grid(mesh_m); gb, ab = _norm_grid(ref_m)
    mae, pct = row_error(A, B)
    x0, y0, x1, y1 = bbox(mesh_m); rx0, ry0, rx1, ry1 = bbox(ref_m)
    out = dict(iou=round(iou(A, B), 4), iou_shape=round(iou(ga, gb), 4), aspect_rel=round(abs(aa - ab) / max(aa, ab), 4),
               row_err_u=round(mae, 3), row_err_pct=round(pct, 1),
               w_u=round((x1 - x0) / PPU, 3), h_u=round((y1 - y0) / PPU, 3),
               ref_w_u=round((rx1 - rx0) / PPU, 3), ref_h_u=round((ry1 - ry0) / PPU, 3))
    if in_frame and mesh_m.shape == ref_m.shape:
        out["iou_in_frame"] = round(iou(mesh_m, ref_m), 4)
        mae2, pct2 = row_error(mesh_m, ref_m)
        out["row_err_pct_in_frame"] = round(pct2, 1)
    return out, A, B


# ======================================================================================================================
# 1. cut the views on one shared frame
# ======================================================================================================================
def clip_below_floor(intake, masks, tol=0.02):
    """A standing creature does not extend below the ground: whatever a view draws more than 2 % of its height under
    its floor line (a tail drawn hanging toward the viewer, a curl lying on the ground in perspective - gemwing's back
    view 129 px, its side 108 px) is a perspective artefact. Left in, it stretches the shared frame's vertical span,
    so the proxy is generated TALLER than the front view and every proportion against the front comes out short.
    Clipped in place (the same clipped masks are what the proxy is scored against). Returns {view: px clipped}."""
    out = {}
    for v, m in masks.items():
        if v not in intake["views"] or not m.any():
            continue
        fl = int(intake["views"][v]["floor_y_px"])
        h = int(intake["views"][v].get("height_px") or 0)
        cut = fl + 1 + int(tol * h)
        n = int(m[cut:].sum())
        if n:
            m[cut:] = False
            out[v] = n
    return out


def cut_views(intake, masks, sheet_rgb, names, out_dir, pad=20):
    """RGBA cut-outs at sheet px, every view's floor line on the same row, same height. Returns {view: path}, frame."""
    from PIL import Image
    ext = {}
    for v in names:
        x0, y0, x1, y1 = bbox(masks[v])
        floor = intake["views"][v]["floor_y_px"]
        ext[v] = dict(x0=x0, x1=x1, y0=y0, y1=y1, floor=floor)
    above = max(e["floor"] - e["y0"] for e in ext.values())
    below = max(0, max(e["y1"] - 1 - e["floor"] for e in ext.values()))
    Hc = above + below + 1 + 2 * pad
    paths, frame = {}, {"above_floor_px": int(above), "below_floor_px": int(below), "pad_px": pad, "height_px": int(Hc),
                        "views": {}}
    H, W, _ = sheet_rgb.shape
    for v in names:
        e = ext[v]
        xa, xb = e["x0"] - pad, e["x1"] + pad
        ya = e["floor"] - above - pad
        rgba = np.zeros((Hc, xb - xa, 4), np.uint8)
        sy0, sy1 = max(ya, 0), min(ya + Hc, H)
        sx0, sx1 = max(xa, 0), min(xb, W)
        rgba[sy0 - ya:sy1 - ya, sx0 - xa:sx1 - xa, :3] = sheet_rgb[sy0:sy1, sx0:sx1]
        rgba[sy0 - ya:sy1 - ya, sx0 - xa:sx1 - xa, 3] = masks[v][sy0:sy1, sx0:sx1] * 255
        p = os.path.join(out_dir, "view_%s.png" % v)
        Image.fromarray(rgba).save(p)
        paths[v] = p
        frame["views"][v] = {"crop_origin_px": [int(xa), int(ya)], "floor_row": int(above + pad),
                             "mask_bbox_px": [e["x0"], e["y0"], e["x1"], e["y1"]]}
    return paths, frame


def mirror_png(src, dst):
    from PIL import Image
    Image.open(src).transpose(Image.FLIP_LEFT_RIGHT).save(dst)
    return dst


# ======================================================================================================================
# 2. generate
# ======================================================================================================================
def free_vram_gb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20).stdout
        used, total = [float(x) for x in out.strip().splitlines()[0].split(",")]
        return (total - used) / 1024.0, total / 1024.0
    except Exception:
        return None, None


def _http(method, url, body=None, timeout=30):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def service_state():
    """('ok' | 'busy' | 'down', supports_shared_frame)"""
    try:
        caps = _http("GET", MESHGEN + "/capabilities", timeout=5)
    except Exception:
        return "down", False
    supports = "shared_frame" in ((caps.get("backends") or {}).get("pixal3d") or {}).get("options", [])
    try:
        jobs = _http("GET", MESHGEN + "/jobs", timeout=5).get("jobs", [])
        busy = any(j.get("state") in ("queued", "running") for j in jobs)
    except Exception:
        busy = False
    return ("busy" if busy else "ok"), supports


def generate(views, out_glb, seed, via):
    """meshgen Pixal3D multi-view with shared framing. Returns the result payload."""
    options = {"seed": int(seed), "shared_frame": True}
    state, supports = service_state()
    if via == "auto":
        via = "service" if (state == "ok" and supports) else "direct"
    if via == "service":
        if not supports:
            raise SystemExit("PROXY: the running meshgen does not advertise options.shared_frame (it predates this "
                             "change) - restart meshgen, or use --via direct")
        job = _http("POST", MESHGEN + "/generate3d", {"backend": "pixal3d", "views": views, "options": options,
                                                     "output": out_glb})
        while True:
            time.sleep(3)
            st = _http("GET", "%s/job/%s" % (MESHGEN, job["job_id"]))
            if st["state"] in ("done", "error", "cancelled"):
                break
        if st["state"] != "done":
            raise SystemExit("PROXY: meshgen job %s: %s" % (st["state"], st.get("error")))
        res = st.get("result") or {}
        res["via"] = "service"
        return res
    # direct: meshgen's own backend in this process, against the running ComfyUI
    if state == "busy":
        raise SystemExit("PROXY: meshgen has a job queued / running - wait for it (direct mode would share the GPU)")
    sys.path.insert(0, REPO)
    from meshgen import config as mconfig
    from meshgen.comfyui_client import ComfyUIClient
    from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
    from meshgen.backends import multiview
    cfg = mconfig.load()
    client = ComfyUIClient(cfg, log=lambda *_a: None)
    if not client.is_running():
        raise SystemExit("PROXY: ComfyUI is not running on %s - start meshgen (any generation starts it) or use "
                         "--via service" % cfg.comfyui_port)
    be = Pixal3DBackend(cfg, client)
    opts = dict(options)
    opts["views"] = multiview.normalise_views(views)
    res = be.generate(None, opts, out_glb)
    res["via"] = "direct"
    return res


# ======================================================================================================================
# 4. normalise into the sheet frame
# ======================================================================================================================
def load_mesh(glb):
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import load_glb
    m = load_glb(glb)
    v = m["verts"].astype(np.float64)
    v = np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1)          # glTF Y-up -> Z-up (front stays -Y)
    return v, m["faces"].astype(np.int64)


def project(verts, view):
    if view == "front":   # camera on -Y looking +Y: +X on image right
        return verts[:, 0], verts[:, 2]
    if view == "back":    # camera on +Y looking -Y: -X on image right
        return -verts[:, 0], verts[:, 2]
    if view == "side":    # camera on +X (the LEFT flank): +Y on image right, so the head (-Y) is on image-left
        return verts[:, 1], verts[:, 2]
    raise ValueError(view)


def splat(verts, faces, view, ppu=PPU, max_level=24):
    """Orthographic coverage mask of a mesh (adaptive barycentric splatting, meshgen's mesh_metrics lattice)."""
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import _bary_grid
    from scipy import ndimage
    u, v = project(verts, view)
    u0, u1, v0, v1 = u.min(), u.max(), v.min(), v.max()
    W = int(np.ceil((u1 - u0) * ppu)) + 3
    H = int(np.ceil((v1 - v0) * ppu)) + 3
    mask = np.zeros((H, W), bool)
    tu, tv = u[faces] - u0, v1 - v[faces]
    span = np.maximum(tu.max(1) - tu.min(1), tv.max(1) - tv.min(1)) * ppu
    level = np.clip(np.ceil(span * 2.0).astype(int), 1, max_level)
    for L in np.unique(level):
        sel = level == L
        B = _bary_grid(int(L))
        pu = (B @ tu[sel].T).T.reshape(-1)
        pv = (B @ tv[sel].T).T.reshape(-1)
        mask[(pv * ppu).astype(int) + 1, (pu * ppu).astype(int) + 1] = True
    return ndimage.binary_fill_holes(ndimage.binary_closing(mask, iterations=1))


def proper_rotations():
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            M = np.zeros((3, 3))
            for i, (p, s) in enumerate(zip(perm, signs)):
                M[i, p] = s
            if round(np.linalg.det(M)) == 1:
                out.append(M)
    return out


def normalise(verts, faces, refs):
    """R (verified orientation), offset, scale: v_sheet = scale * (R @ v - offset); floor z 0, bbox centred on X/Y,
    front silhouette height = the sheet's front silhouette height."""
    rng = np.random.default_rng(0)
    fsub = faces[rng.choice(len(faces), min(len(faces), 40000), replace=False)]

    def centre(V):
        return V - [(V[:, 0].min() + V[:, 0].max()) / 2, (V[:, 1].min() + V[:, 1].max()) / 2, V[:, 2].min()]
    probe_views = [v for v in ("front", "side") if v in refs] or list(refs)
    scores = []
    for R in proper_rotations():
        V = centre(verts @ R.T)
        V = V / max(V[:, 2].max() / 2.0, 1e-9)
        sc = np.mean([iou(_norm_grid(splat(V, fsub, v, ppu=60))[0], _norm_grid(refs[v])[0]) for v in probe_views])
        scores.append((sc, R))
    ident = [s for s, R in scores if np.allclose(R, np.eye(3))][0]
    best_sc, best_R = max(scores, key=lambda t: t[0])
    R = best_R if best_sc > ident + 0.05 else np.eye(3)       # Pixal3D MV comes out front -Y; only a clear win moves it
    VR = verts @ R.T
    off = np.array([(VR[:, 0].min() + VR[:, 0].max()) / 2, (VR[:, 1].min() + VR[:, 1].max()) / 2, VR[:, 2].min()])
    V = VR - off
    ref_front = refs.get("front", refs.get("back"))
    fm = splat(V, faces, "front")
    _, fy0, _, fy1 = bbox(fm); _, ry0, _, ry1 = bbox(ref_front)
    s = (ry1 - ry0) / max(fy1 - fy0, 1)
    return {"rotation": R.tolist(), "offset": off.tolist(), "scale": float(s),
            "rule": "v_sheet = scale * (R @ v_zup - offset); glTF (x, y, z) -> z-up (x, -z, y)",
            "orientation_identity_iou": round(float(ident), 4), "orientation_best_iou": round(float(best_sc), 4)}, \
        s * V


# ======================================================================================================================
# 6. sections + landmarks
# ======================================================================================================================
def slice_points(V, F, h):
    """Exact intersection points of the mesh surface with the plane z = h."""
    z = V[F, 2]                                         # (n, 3)
    above = z > h
    cross = above.any(1) & ~above.all(1)
    pts = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        za, zb = z[cross, a], z[cross, b]
        sel = (za > h) != (zb > h)
        if not sel.any():
            continue
        pa, pb = V[F[cross][sel, a]], V[F[cross][sel, b]]
        t = ((h - za[sel]) / (zb[sel] - za[sel]))[:, None]
        pts.append(pa + t * (pb - pa))
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def sections_and_landmarks(V, F, intake, front_view="front"):
    H = float(V[:, 2].max())
    secs = []
    for f in SECTION_BANDS:
        p = slice_points(V, F, f * H)
        if len(p) < 6:
            continue
        lo, hi = np.percentile(p[:, :2], 1, 0), np.percentile(p[:, :2], 99, 0)
        secs.append({"z_frac": f, "z_u": round(f * H, 3), "x_min_u": round(lo[0], 3), "x_max_u": round(hi[0], 3),
                     "width_x_u": round(hi[0] - lo[0], 3), "y_front_u": round(lo[1], 3), "y_back_u": round(hi[1], 3),
                     "depth_y_u": round(hi[1] - lo[1], 3), "y_centre_u": round((hi[1] + lo[1]) / 2, 3),
                     "points": int(len(p))})
    i_top = int(np.argmax(V[:, 2])); i_fr = int(np.argmin(V[:, 1])); i_bk = int(np.argmax(V[:, 1]))
    lm = {"height_u": round(H, 3),
          "top": {"z_u": round(H, 3), "x_u": round(V[i_top, 0], 3), "y_u": round(V[i_top, 1], 3)},
          "front_most": {"y_u": round(V[i_fr, 1], 3), "z_u": round(V[i_fr, 2], 3),
                         "note": "the most -Y point (snout / chest / beak tip)"},
          "rear_most": {"y_u": round(V[i_bk, 1], 3), "z_u": round(V[i_bk, 2], 3), "note": "the most +Y point (tail)"},
          "length_y_u": round(float(V[:, 1].max() - V[:, 1].min()), 3),
          "width_x_u": round(float(V[:, 0].max() - V[:, 0].min()), 3)}
    widest = max(secs, key=lambda s: s["width_x_u"]) if secs else None
    if widest:
        lm["widest_band"] = {"z_u": widest["z_u"], "width_x_u": widest["width_x_u"]}
    # depth at the intake's half-width rows (the front view's landmark heights, as fractions of its height)
    hw = (intake["views"].get(front_view) or {}).get("half_width_profile") or {}
    rows = {}
    for frac in sorted(hw, key=float):
        h = min(max(float(frac) * H, 0.02 * H), 0.98 * H)        # a slice AT the floor is degenerate
        p = slice_points(V, F, h)
        if len(p) >= 6:
            lo, hi = np.percentile(p[:, :2], 1, 0), np.percentile(p[:, :2], 99, 0)
            rows[frac] = {"z_u": round(h, 3), "depth_y_u": round(hi[1] - lo[1], 3),
                          "half_w_x_u": round(max(abs(lo[0]), abs(hi[0])), 3),
                          "sheet_half_w_u": hw[frac]["half_w_units"]}
    lm["depth_at_intake_rows"] = rows
    return secs, lm


# ======================================================================================================================
# 7. render (Blender, headless) + the sheet-vs-proxy panel
# ======================================================================================================================
BLENDER_SCRIPT = r'''
import bpy, json, math, sys
from mathutils import Matrix, Vector
argv = sys.argv[sys.argv.index("--") + 1:]
glb, tf, outp = argv[0], json.load(open(argv[1])), argv[2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
R = Matrix(tf["rotation"]); off = Vector(tf["offset"]); s = tf["scale"]
# glTF import already turned Y-up into Z-up; v_sheet = s * (R @ v - off)
M = Matrix.Scale(s, 4) @ Matrix.Translation(-off) @ R.to_4x4()
root = bpy.data.objects.new("norm", None); bpy.context.scene.collection.objects.link(root)
for o in list(bpy.context.scene.objects):
    if o is not root and o.parent is None:
        o.parent = root; o.matrix_parent_inverse = Matrix.Identity(4)
root.matrix_world = M
bpy.context.view_layer.update()
pts = []
dg = bpy.context.evaluated_depsgraph_get()
for o in bpy.context.scene.objects:
    if o.type == "MESH":
        me = o.evaluated_get(dg).to_mesh()
        pts += [o.matrix_world @ v.co for v in me.vertices]
        o.evaluated_get(dg).to_mesh_clear()
lo = Vector([min(p[i] for p in pts) for i in range(3)]); hi = Vector([max(p[i] for p in pts) for i in range(3)])
sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sh = sc.display.shading
sh.light = "STUDIO"; sh.color_type = "TEXTURE"; sh.show_cavity = False
sc.render.film_transparent = True
sc.view_settings.view_transform = "Standard"
cam = bpy.data.cameras.new("c"); cam.type = "ORTHO"
co = bpy.data.objects.new("c", cam); sc.collection.objects.link(co); sc.camera = co
c = (lo + hi) / 2
for view in ("front", "side", "back"):
    if view == "front":
        w, h = hi.x - lo.x, hi.z - lo.z; co.location = (c.x, lo.y - 20, c.z); co.rotation_euler = (math.pi / 2, 0, 0)
    elif view == "back":
        w, h = hi.x - lo.x, hi.z - lo.z; co.location = (c.x, hi.y + 20, c.z); co.rotation_euler = (math.pi / 2, 0, math.pi)
    else:
        w, h = hi.y - lo.y, hi.z - lo.z; co.location = (hi.x + 20, c.y, c.z); co.rotation_euler = (math.pi / 2, 0, math.pi / 2)
    W, H = int(round(w * 100)) + 4, int(round(h * 100)) + 4
    sc.render.resolution_x, sc.render.resolution_y = W, H
    cam.ortho_scale = max(W, H) / 100.0
    cam.clip_end = 200
    sc.render.filepath = outp + "_" + view + ".png"
    bpy.ops.render.render(write_still=True)
print("RENDERED", outp)
'''


def render_proxy(glb, transform, out_prefix, work_dir):
    if not os.path.exists(BLENDER):
        return None
    scr = os.path.join(work_dir, "_render_proxy.py")
    tfp = os.path.join(work_dir, "_transform.json")
    open(scr, "w").write(BLENDER_SCRIPT)
    json.dump(transform, open(tfp, "w"))
    r = subprocess.run([BLENDER, "--background", "--factory-startup", "--python", scr, "--", glb, tfp, out_prefix],
                       capture_output=True, text=True, creationflags=NO_WINDOW, timeout=600)
    os.remove(scr); os.remove(tfp)
    ok = all(os.path.exists("%s_%s.png" % (out_prefix, v)) for v in ("front", "side", "back"))
    return out_prefix if ok and r.returncode == 0 else None


def compose_panel(cuts, render_prefix, scores, facing, unit, out_png, gate):
    """sheet | proxy (rendered, or its silhouette), one row per view, both at sheet scale, bottom-aligned."""
    from PIL import Image, ImageDraw
    K = 0.5
    rows = []
    for v in ("front", "side", "back"):
        if v not in cuts:
            continue
        sheet_im = Image.open(cuts[v]).convert("RGBA")
        if v == "side" and facing == "right":
            sheet_im = sheet_im.transpose(Image.FLIP_LEFT_RIGHT)
        p = "%s_%s.png" % (render_prefix, v) if render_prefix else None
        prox = Image.open(p).convert("RGBA") if p and os.path.exists(p) else Image.new("RGBA", (40, 40))
        cells = [im.resize((max(1, int(im.width * K)), max(1, int(im.height * K))), Image.LANCZOS)
                 for im in (sheet_im, prox)]
        rows.append((v, cells, scores.get(v)))
    if not rows:
        return None
    cw = max(c.width for _v, cells, _m in rows for c in cells) + 20
    ch = [max(c.height for c in cells) + 20 for _v, cells, _m in rows]
    head, foot = 40, 30
    out = Image.new("RGB", (2 * cw, head + sum(h + foot for h in ch)), (34, 34, 38))
    d = ImageDraw.Draw(out)
    d.text((10, 8), "SHEET  %s" % unit, fill=(235, 235, 235))
    d.text((cw + 10, 8), "PIXAL3D PROXY (reference only)  gate: %s" % gate, fill=(235, 235, 235))
    y = head
    for (v, cells, m), h in zip(rows, ch):
        for i, im in enumerate(cells):
            out.paste(im, (i * cw + (cw - im.width) // 2, y + h - 10 - im.height), im)
        d.text((10, y + h + 4), v.upper() + ("  (head image-right, mirrored to compare)" if v == "side" and
                                              facing == "right" else ""), fill=(150, 200, 255))
        if m:
            d.text((cw + 10, y + h + 4), "IoU %.2f  row err %.0f%%  %.2f x %.2f u (sheet %.2f x %.2f)"
                   % (m["iou"], m["row_err_pct"], m["w_u"], m["h_u"], m["ref_w_u"], m["ref_h_u"]), fill=(200, 200, 120))
        y += h + foot
    out.save(out_png)
    return out_png


# ======================================================================================================================
# main
# ======================================================================================================================
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse(argv):
    a = list(argv)

    def val(flag, default=None):
        return a[a.index(flag) + 1] if flag in a else default
    unit = a[0]
    return {"unit": unit,
            "intake": val("--intake", os.path.join(ROOT, "design", "reference", unit, "intake.json")),
            "out_dir": val("--out-dir"), "seed": int(val("--seed", SEED)), "views": val("--views"),
            "via": val("--via", "auto"), "min_free_gb": float(val("--min-free-gb", MIN_FREE_GB)),
            "glb": val("--glb"), "render": "--no-render" not in a, "write_intake": "--no-write-intake" not in a,
            "keep_below_floor": "--keep-below-floor" in a}


def main(argv):
    from PIL import Image
    o = parse(argv)
    unit = o["unit"]
    intake, intake_dir = load_intake(o["intake"])
    out_dir = o["out_dir"] or os.path.join(intake_dir, "proxy")
    os.makedirs(out_dir, exist_ok=True)
    sheet_rgb = np.asarray(Image.open(sheet_path(intake)).convert("RGB"))
    masks = view_masks(intake, intake_dir, sheet_rgb)
    clipped = {} if o["keep_below_floor"] else clip_below_floor(intake, masks)
    facing = ((intake.get("side_facing") or {}).get("facing")) or "left"
    checks = intake.get("view_checks") or {}
    want = o["views"].split(",") if o["views"] else [v for v in ("front", "back", "side") if v in intake["views"]]
    notes = []
    if clipped:
        notes.append("clipped below the floor line (perspective tails / curls; --keep-below-floor to keep): %s"
                     % ", ".join("%s %d px" % kv for kv in clipped.items()))
    three_q = [v for v in ("front", "back") if (checks.get(v) or {}).get("three_quarter_suspect")]
    used = [v for v in want if v not in three_q]
    for v in three_q:
        if v in want:
            notes.append("%s left out: the intake flags it 3/4 (%s)" % (v, ", ".join(checks[v].get("three_quarter_why", []))))
    # 1. cut
    cuts, frame = cut_views(intake, masks, sheet_rgb, [v for v in want if v in masks], out_dir)
    mv = {}
    if "front" in used:
        mv["front"] = cuts["front"]
    elif "back" in used:
        mv["front"] = mirror_png(cuts["back"], os.path.join(out_dir, "view_front_from_back.png"))
        notes.append("front stood in by the mirrored back (same ortho silhouette; its colours are the back's - the "
                     "proxy is geometry only)")
    if "back" in used and "front" in used:
        mv["back"] = cuts["back"]
    if "side" in used:
        mv["left" if facing == "left" else "right"] = cuts["side"]
    if "front" not in mv or len(mv) < 2:
        raise SystemExit("PROXY: needs a clean front (or back) and one more view; have %s" % sorted(mv))
    # 2. generate (or reuse --glb)
    raw_glb = os.path.join(out_dir, "_raw.glb")
    gen = {}
    if o["glb"]:
        shutil.copy2(o["glb"], raw_glb)
        gen = {"via": "reused", "source": o["glb"]}
    else:
        free, total = free_vram_gb()
        if free is not None and free < o["min_free_gb"]:
            print("PROXY: GPU busy - %.1f GB free of %.1f, Pixal3D 3-view needs ~%.1f GB. Close GPU apps (games, "
                  "browsers) or wait for the running job, then rerun. Nothing was generated; the build works from the "
                  "sheet alone." % (free, total, o["min_free_gb"]))
            sys.exit(3)
        t0 = time.time()
        res = generate(mv, raw_glb, o["seed"], o["via"])
        gen = {"via": res.get("via"), "seed": res.get("seed"), "duration_ms": res.get("duration_ms"),
               "wall_s": round(time.time() - t0, 1), "vram": res.get("vram"), "stats": res.get("stats"),
               "framing": (res.get("multiview") or {}).get("framing"), "warnings": res.get("warnings"),
               "free_vram_gb_before": None if free is None else round(free, 2)}
    # 3. freeze
    digest = sha256(raw_glb)
    glb = os.path.join(out_dir, "%s_proxy.glb" % unit)
    shutil.move(raw_glb, glb)
    # 4. normalise
    refs = {}
    for v in ("front", "back", "side"):
        if v in masks and masks[v].any():
            x0, y0, x1, y1 = bbox(masks[v])
            m = masks[v][y0:y1, x0:x1]
            refs[v] = m[:, ::-1] if (v == "side" and facing == "right") else m
    verts, faces = load_mesh(glb)
    tf, V = normalise(verts, faces, refs)
    # 5. score
    scores = {}
    for v in refs:
        met, A, B = view_metrics(splat(V, faces, v), refs[v])
        met["scored"] = v in used
        scores[v] = met
        vis = np.zeros(A.shape + (3,), np.uint8)
        vis[B & ~A] = (230, 60, 60); vis[A & ~B] = (60, 120, 240); vis[A & B] = (235, 235, 235)
        Image.fromarray(vis).save(os.path.join(out_dir, "overlap_%s.png" % v))
    scored = [v for v in scores if scores[v]["scored"]]
    mean_iou = round(float(np.mean([scores[v]["iou"] for v in scored])), 4)
    side_len = None
    if "side" in scores and "front" in scores:
        mesh_r = scores["side"]["w_u"] / scores["front"]["h_u"]
        sheet_r = scores["side"]["ref_w_u"] / scores["front"]["ref_h_u"]
        side_len = {"mesh": round(mesh_r, 3), "sheet": round(sheet_r, 3), "rel_err": round(mesh_r / sheet_r - 1, 4)}
    fw = None
    if "front" in scores:
        fw = {"mesh": round(scores["front"]["w_u"] / scores["front"]["h_u"], 3),
              "sheet": round(scores["front"]["ref_w_u"] / scores["front"]["ref_h_u"], 3)}
    ok = mean_iou >= GATE_IOU and (side_len is None or abs(side_len["rel_err"]) <= GATE_SIDE_LEN)
    why = []
    if mean_iou < GATE_IOU:
        why.append("mean IoU %.2f < %.2f" % (mean_iou, GATE_IOU))
    if side_len and abs(side_len["rel_err"]) > GATE_SIDE_LEN:
        why.append("side length off by %+.1f%%" % (100 * side_len["rel_err"]))
    verdict = "reliable" if ok else "proxy unreliable, work from the sheet (%s)" % "; ".join(why)
    # 6. sections + landmarks
    secs, lm = sections_and_landmarks(V, faces, intake, "front" if "front" in intake["views"] else used[0])
    # 7. render + panel
    render_prefix = render_proxy(glb, tf, os.path.join(out_dir, "%s_proxy" % unit), out_dir) if o["render"] else None
    panel = compose_panel(cuts, render_prefix, scores, facing, unit, os.path.join(out_dir, "%s_proxy_vs_sheet.png" % unit),
                          "PASS" if ok else "UNRELIABLE")

    def rel(p):
        return os.path.relpath(p, intake_dir).replace("\\", "/") if p else None
    block = {"glb": rel(glb), "sha256": digest, "seed": o["seed"], "model": "Pixal3D multi-view (meshgen, shared_frame)",
             "views_sent": {k: rel(p) for k, p in mv.items()}, "side_facing": facing, "frame": frame,
             "generated": gen, "transform": tf, "clipped_below_floor_px": clipped,
             "scores": scores, "mean_iou": mean_iou,
             "side_len_over_front_h": side_len, "front_w_over_h": fw,
             "gate": {"pass": ok, "verdict": verdict, "rule": "mean IoU >= %.2f and side length within %d%%"
                      % (GATE_IOU, int(100 * GATE_SIDE_LEN))},
             "sections": secs, "landmarks": lm, "render": rel(panel), "notes": notes,
             "use": "3D REFERENCE ONLY: cite sections / landmark depths in the build's constants; the sheet is the "
                    "spec and the rest pose stays symmetric (the proxy copies the sheet's pose)",
             "units": "sheet units (1 u = 100 sheet px); z up from the floor, x = the creature's left/right (bbox "
                      "centred), y = depth, front = -y"}
    json.dump(block, open(os.path.join(out_dir, "proxy.json"), "w"), indent=1)
    if o["write_intake"]:
        intake["proxy"] = block
        json.dump(intake, open(o["intake"], "w"), indent=1)
    print("PROXY", json.dumps({"sha256": digest[:16], "mean_iou": mean_iou,
                               "iou": {v: scores[v]["iou"] for v in scores},
                               "row_err_pct": {v: scores[v]["row_err_pct"] for v in scores},
                               "side_len": side_len, "gate": verdict}))
    for n in notes:
        print("NOTE", n)
    return block


# ======================================================================================================================
# self-test (no GPU, no repo files touched): python -P sheet_proxy.py --selftest
# ======================================================================================================================
def _box_mesh(lx, ly, lz):
    V = np.array([[x, y, z] for x in (-lx / 2, lx / 2) for y in (-ly / 2, ly / 2) for z in (0, lz)], float)
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def selftest():
    import tempfile
    from PIL import Image
    fails = []

    def check(name, ok, detail=""):
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            fails.append(name)
    # sections: an exact slice of a 2 x 4 x 3 box
    V, F = _box_mesh(2.0, 4.0, 3.0)
    p = slice_points(V, F, 1.5)
    check("slice extents", np.allclose([p[:, 0].min(), p[:, 0].max(), p[:, 1].min(), p[:, 1].max()],
                                       [-1, 1, -2, 2], atol=1e-6), str(len(p)))
    secs, lm = sections_and_landmarks(V, F, {"views": {"front": {"half_width_profile": {
        "0.50": {"y_px": 0, "half_w_units": 1.0}}}}})
    check("sections depth", all(abs(s_["depth_y_u"] - 4.0) < 0.05 for s_ in secs), str([s_["depth_y_u"] for s_ in secs]))
    check("landmark depth row", abs(lm["depth_at_intake_rows"]["0.50"]["depth_y_u"] - 4.0) < 0.05)
    # projection + metrics: the box seen from the side is 4 wide x 3 tall at 100 px/u
    side = splat(V, F, "side")
    x0, y0, x1, y1 = bbox(side)
    check("splat side size", abs((x1 - x0) - 400) <= 3 and abs((y1 - y0) - 300) <= 3, "%dx%d" % (x1 - x0, y1 - y0))
    met, _a, _b = view_metrics(side, side)
    check("self IoU 1", met["iou"] == 1.0 and met["row_err_pct"] == 0.0, str(met["iou"]))
    shifted = np.zeros((side.shape[0] + 40, side.shape[1] + 40), bool)
    shifted[25:25 + side.shape[0], 13:13 + side.shape[1]] = side
    met2, _a, _b = view_metrics(shifted, np.pad(side, ((0, 40), (0, 40))), in_frame=True)
    check("registration removes placement", met2["iou"] == 1.0 and met2["iou_in_frame"] < 0.95,
          "%.3f / %.3f" % (met2["iou"], met2["iou_in_frame"]))
    # normalise: a scaled, offset box comes back at the reference height with identity orientation
    refs = {"front": splat(V, F, "front"), "side": side}
    tf, Vn = normalise(V * 0.37 + [0.2, -0.1, 0.05], F, refs)
    check("normalise scale + floor", abs(Vn[:, 2].max() - 3.0) < 0.03 and abs(Vn[:, 2].min()) < 1e-6
          and np.allclose(tf["rotation"], np.eye(3)), "h %.3f" % Vn[:, 2].max())
    # below-floor clip + shared-floor cut-outs
    intake = {"views": {"front": {"floor_y_px": 80, "height_px": 60}, "side": {"floor_y_px": 90, "height_px": 70}}}
    m = {"front": np.zeros((120, 200), bool), "side": np.zeros((120, 200), bool)}
    m["front"][20:81, 10:60] = True
    m["side"][20:91, 100:180] = True
    m["side"][91:110, 170:190] = True                      # a tail curl drawn under the floor
    clipped = clip_below_floor(intake, m)
    check("clip below floor", clipped.get("side", 0) > 0 and not m["side"][93:].any(), str(clipped))
    with tempfile.TemporaryDirectory(prefix="proxy_selftest_") as td:
        rgb = np.zeros((120, 200, 3), np.uint8) + 120
        paths, frame = cut_views(intake, m, rgb, ["front", "side"], td)
        a = np.asarray(Image.open(paths["front"])); b = np.asarray(Image.open(paths["side"]))
        fa = frame["views"]["front"]["floor_row"]; fb = frame["views"]["side"]["floor_row"]
        last_a = np.nonzero(a[:, :, 3].any(1))[0].max(); last_b = np.nonzero(b[:, :, 3].any(1))[0].max()
        check("cut-outs share height + floor row", a.shape[0] == b.shape[0] and fa == fb and last_a == fa
              and abs(last_b - fb) <= 2, "h %d/%d floor %d/%d" % (a.shape[0], b.shape[0], last_a, last_b))
    print("SELFTEST %s (%d failed)" % ("PASS" if not fails else "FAIL", len(fails)))
    return not fails


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main(sys.argv[1:])
