"""Proxy FINISH stage: a frozen Pixal3D sheet proxy as the BASE mesh of a NEW unit, with the house rules on top
(prototype 2026-10-11, after the artist's Pixal3D trial verdict: "all of the work it did looks better than what we did,
except for the called out issues of model interpertation etc"). Plain Python (service venv, run with -P) for the
solid / colour / gate / panel stages; the Blender half is proxy_finish_build.py, driven by the unit's stub build.

    python -P proxy_finish.py init  <unit> [--force]     the stub improve/<unit>_build.py (the runner's build), the
                                                         per-unit config improve/<unit>_finish.json (template) and
                                                         palettes/<unit>/default.json (one region per sheet chip)
    python -P proxy_finish.py solid <unit> --out <npz>   STAGE 1 (the build calls it; standalone = debug): the clean,
                                                         symmetric, watertight SDF grid + colour sources + resolved config
    python -P proxy_finish.py gate  <unit> [--hand <glb>]   the finish gate: finished vs sheet AND vs the raw proxy,
                                                         mirror test, open edges, tris (+ an optional hand build scored
                                                         the same way); renders/<unit>/<unit>_finish_gate.json
    python -P proxy_finish.py panel <unit> [--hand <blend>]  sheet | raw proxy | finished | hand build, front / side /
                                                         threequarter, with the IoU numbers: renders/<unit>/<unit>_finish_panel.png

STAGE 1 (deterministic numpy + scipy; the digest twins each run it, so the grid sha is in the build digest):
  1. load   the frozen proxy glb (sha checked against intake.json proxy.sha256) into the sheet frame with the intake's
            proxy.transform (Z up, front -Y, floor z 0, sheet units); its base-colour texture sampled per surface point
  2. solid  surface samples -> shell nodes on a grid; exterior = flood fill from the border of the CLOSED shell (closing
            radius `close_voxels`: soup cracks and specks seal), so the 14-64 non-manifold pieces become one solid;
            near-band signed distance = the exact distance to the proxy surface, signed by the flood fill (and by the
            nearest face normal on shell nodes that touch the exterior), offset by `min_half_thickness` so blades thinner
            than a voxel survive; light Gaussian smoothing; hard floor at z 0
  3. straighten (config `straighten`): a lateral shear that puts a swung tail / neck back on the midline: per slab along
            the chain axis the occupied centroid's x is measured, smoothed, ramped in from `from`, and removed
  4. symmetrise: the symmetry plane is found (mirror IoU of the occupancy over sub-voxel shifts), then
            F_sym = (F + F mirrored in X) / 2 - an exact average, never a bisect: the left and right halves both vote
  5. cuts   (config `cuts`): capsules / spheres / boxes subtracted (smooth max) - where a procedural part replaces what
            Pixal3D melted (a crystal crown, wing blades, a chest starburst)
  6. register the solid's ortho silhouettes onto the sheet views (FFT best translation, the gate's own metric), so the
            config can cite SHEET PIXELS for every point (cuts, parts, joints) and the sheet colours project exactly
  7. colour sources: per sheet view the chip label of every pixel (nearest palette chip in CIELab, the inked outline
            eroded away); the proxy texture samples, their drift corrected by an affine colour fit against the sheet
            where both see the surface, snapped to chips; all saved for the Blender stage's face vote
  1b. optional frame fixes: `fit_side_length` (stretch Y to the sheet's side length when the proxy gate says it is off)
            and `refloor` (the floor at the FOOT contact in a Y window, then a uniform rescale to the sheet's front
            height: sheet_proxy floors at the lowest point, which can be a tail curl lying on the ground)
  5b. `silhouette_fan` parts are expanded here into explicit blades that follow the SHEET's side-view silhouette (rays
            from the root, a blade per reach peak plus fill) with each tip's X from the front-view silhouette
  8. melt report: the parts thinner than `thin_report_u` (a morphological opening of the solid) as components with
            their sheet-px boxes - the candidates Pixal3D fuses into shard noise (blades, crowns, horns)
"""
import hashlib, json, math, os, subprocess, sys, time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPO = os.path.normpath(os.path.join(ROOT, "..", ".."))
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PPU = 100.0

DEFAULTS = {
    "tier": "regular", "tri_budget": [8000, 22000], "body_tris": 14000, "char_id": None,
    "cell_max": [1.8, 1.9],
    "step": None,                 # grid step (sheet units); None = height / steps_per_height
    "steps_per_height": 240,
    "close_voxels": 1,            # closing radius of the proxy shell before the flood fill (seals soup cracks)
    "min_half_thickness": 0.5,    # x step: every sheet gets at least this half thickness (thin blades survive)
    "smooth_sigma": 0.6,          # Gaussian sigma (voxels) on the near-band SDF
    "band_voxels": 4,
    "symmetrise": True,
    "sym_search_voxels": 8,
    "fit_side_length": False,     # stretch Y so the proxy's side length / front height equals the sheet's (proxy gate)
    "colour_fit_max_residual": 35.0,   # the texture drift fit is applied only when its median residual (RGB) is below
    "straighten": [],             # [{"axis": "y"|"z", "from": a, "to": b, "ramp": r, "zmin": .., "zmax": ..}]
    "cuts": [],                   # [{"capsule": [p, q, r]} | {"sphere": [c, r]} | {"box": [lo, hi]}, "k": 0.02}
    "thin_report_u": 0.07,
    "colour": {"chip_names": None, "merge": {}, "outline_erode_px": 3, "facing_min": 0.15, "proxy_weight": 0.12,
               "min_island_area": 0.012, "keep_small": [], "keep_small_area": 0.002, "majority_iters": 2,
               "boundary_smooth_iters": 3, "sheet_views": ["front", "back", "side"]},
    "parts": [],
    "rig": {},
    "facing": {},
    "focus": {},
    "clips": {},
    "bake_res": [1024, 512],
    # "light": keep the proxy's geometry + texture as generated (stage_light / proxy_finish_light.py); "full" = above
    "mode": "full",
    "light": {"steps_per_height": 300,  # the watertight remesh grid (finer than full mode: sub-px, then decimated)
              "close_voxels": 1, "smooth_sigma": 0.0, "min_half_thickness": 0.5, "band_voxels": 4,
              "speck_frac": 0.002,      # closed pieces under this share of the high's vertices are dropped (specks)
              "floor_clip": False,      # never clip at the floor: a posed tail below the feet is reposed, not cut
              "dense": [],              # [{"box": [lo, hi], "density": 2.5}]: decimation keeps these (head / face) denser
              "pre_tris": 300000,       # uniform first decimation (calibration of the dense weighting runs on it)
              "albedo_res": 2048,       # the proxy texture baked onto the low
              "colour_fit": False,      # the sheet drift fit on the proxy texture (off: the artist picked the raw colours)
              "region": "proxy_albedo"},   # the one palette region (white): Col tints the baked albedo
    "repose": None,               # {"targets": [...]} (proxy_finish_repose.py): straighten posed chains, apply as rest
}


# ======================================================================================================================
# small helpers
# ======================================================================================================================
def sha_arr(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def paths(unit):
    ref = os.path.join(ROOT, "design", "reference", unit)
    return {"intake": os.path.join(ref, "intake.json"), "ref": ref,
            "cfg": os.path.join(HERE, unit + "_finish.json"), "stub": os.path.join(HERE, unit + "_build.py"),
            "palette": os.path.join(ROOT, "palettes", unit, "default.json"),
            "renders": os.path.join(ROOT, "renders", unit), "rigged_glb": os.path.join(ROOT, "rigged", unit + ".glb"),
            "rigged_blend": os.path.join(ROOT, "rigged", unit + ".blend")}


def load_cfg(unit):
    p = paths(unit)["cfg"]
    raw = json.load(open(p, encoding="utf-8-sig")) if os.path.exists(p) else {}   # utf-8-sig: a Windows editor BOM
    return deep_merge(DEFAULTS, raw), raw


def srgb_to_lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])], -1)


def chips_of(intake):
    """the sheet's palette chips: intake swatches, else the smart intake's palette_fallback chips"""
    sw = intake.get("swatches") or []
    src = "swatches"
    if not sw:
        sw = (intake.get("palette_fallback") or {}).get("chips") or []
        src = "palette_fallback:" + str((intake.get("palette_fallback") or {}).get("source"))
    return [list(map(int, s["rgb"])) for s in sw], src


def chip_names(cfg, chips):
    names = (cfg["colour"].get("chip_names") or [])
    return [names[i] if i < len(names) and names[i] else "chip_%d" % i for i in range(len(chips))]


def nearest_chip(rgb, chips_lab):
    lab = srgb_to_lab(rgb)
    d = np.linalg.norm(lab[..., None, :] - chips_lab, axis=-1)
    return np.argmin(d, -1).astype(np.int16), np.min(d, -1)


# ======================================================================================================================
# 1. load the frozen proxy (geometry + uv + base-colour texture)
# ======================================================================================================================
def read_proxy(glb_path):
    sys.path.insert(0, REPO)
    from meshgen import glb as G
    from meshgen.tools.mesh_metrics import _accessor, _node_matrices
    from PIL import Image
    import io
    doc, blob = G.read_chunks(glb_path)
    mats = _node_matrices(doc)
    V, F, UV, TEXI = [], [], [], []
    off = 0
    images = {}
    for mi, mesh in enumerate(doc.get("meshes") or []):
        W = mats.get(mi, np.eye(4))
        for prim in mesh.get("primitives") or []:
            a = prim["attributes"]
            pos = _accessor(doc, blob, a["POSITION"]) @ W[:3, :3].T + W[:3, 3]
            idx = _accessor(doc, blob, prim["indices"]).reshape(-1, 3) if "indices" in prim else \
                np.arange(len(pos)).reshape(-1, 3)
            uv = _accessor(doc, blob, a["TEXCOORD_0"]) if "TEXCOORD_0" in a else np.zeros((len(pos), 2))
            tex = -1
            m = (doc.get("materials") or [{}])[prim["material"]] if "material" in prim else {}
            bct = (m.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if bct is not None:
                src = doc["textures"][bct["index"]]["source"]
                if src not in images:
                    im = doc["images"][src]
                    bv = doc["bufferViews"][im["bufferView"]]
                    raw = bytes(blob[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]])
                    images[src] = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
                tex = src
            V.append(pos); F.append(idx + off); UV.append(uv); TEXI.append(np.full(len(idx), tex))
            off += len(pos)
    return np.concatenate(V), np.concatenate(F), np.concatenate(UV), np.concatenate(TEXI), images


def to_sheet(Vgl, tf):
    Vz = np.stack([Vgl[:, 0], -Vgl[:, 2], Vgl[:, 1]], 1)
    R = np.array(tf["rotation"], float); off = np.array(tf["offset"], float)
    return tf["scale"] * (Vz @ R.T - off)


def sample_surface(V, F, UV, TEXI, images, spacing):
    """barycentric lattice samples (adaptive per triangle) -> points, face index, base colour (sRGB 0-255)"""
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import _bary_grid
    T = V[F]
    span = np.max(np.linalg.norm(T - np.roll(T, 1, axis=1), axis=2), 1)
    level = np.clip(np.ceil(span / spacing).astype(int), 1, 16)
    P, FI, C = [], [], []
    for L in np.unique(level):
        sel = np.nonzero(level == L)[0]
        B = _bary_grid(int(L))                                   # (k, 3)
        pts = np.einsum("kj,fjd->fkd", B, T[sel]).reshape(-1, 3)
        fi = np.repeat(sel, len(B))
        uvs = np.einsum("kj,fjd->fkd", B, UV[F[sel]]).reshape(-1, 2)
        col = np.zeros((len(pts), 3))
        ti = np.repeat(TEXI[sel], len(B))
        for t, im in images.items():
            m = ti == t
            if m.any():
                h, w, _ = im.shape
                u = np.clip((uvs[m, 0] % 1.0) * (w - 1), 0, w - 1).round().astype(int)
                v = np.clip((uvs[m, 1] % 1.0) * (h - 1), 0, h - 1).round().astype(int)   # glTF uv: v down = image row
                col[m] = im[v, u]
        P.append(pts); FI.append(fi); C.append(col)
    return np.concatenate(P), np.concatenate(FI), np.concatenate(C)


# ======================================================================================================================
# 2-5. the clean solid
# ======================================================================================================================
def ball(r):
    k = int(math.ceil(r))
    g = np.mgrid[-k:k + 1, -k:k + 1, -k:k + 1]
    return (g ** 2).sum(0) <= r * r + 1e-9


def shift_x(F, dx_nodes):
    """F resampled at x + dx (dx per (y, z) column, in nodes; linear; edge-clamped)"""
    nx = F.shape[0]
    dx = np.broadcast_to(np.asarray(dx_nodes, float), F.shape[1:])
    xi = np.arange(nx)[:, None, None] + dx[None]
    x0 = np.clip(np.floor(xi).astype(np.int64), 0, nx - 2)
    w = np.clip(xi - x0, 0.0, 1.0)
    a = np.take_along_axis(F, x0, 0); b = np.take_along_axis(F, x0 + 1, 0)
    return a * (1 - w) + b * w


def straighten_offsets(occ, lo, step, spec):
    """per-slab x centroid of the occupied nodes along spec['axis'] from spec['from'] to spec['to'], smoothed and ramped
    in -> lateral offset (units) per (y, z) column"""
    from scipy import ndimage
    ax = {"y": 1, "z": 2}[spec.get("axis", "y")]
    n = occ.shape
    coords = [lo[k] + step * np.arange(n[k]) for k in range(3)]
    xs = coords[0]
    sel = occ.copy()
    for k, key in ((2, "zmin"), (2, "zmax"), (1, "ymin"), (1, "ymax")):
        if key in spec:
            c = coords[k]
            m = (c >= spec[key]) if key.endswith("min") else (c <= spec[key])
            shape = [1, 1, 1]; shape[k] = -1
            sel &= m.reshape(shape)
    a0, a1 = float(spec["from"]), float(spec["to"])
    t = coords[ax]
    cnt = sel.sum(axis=tuple(i for i in range(3) if i != ax)).astype(float)
    sx = (sel * xs[:, None, None]).sum(axis=tuple(i for i in range(3) if i != ax))
    cen = np.where(cnt > 0, sx / np.maximum(cnt, 1), np.nan)
    inside = (t >= min(a0, a1)) & (t <= max(a0, a1)) & np.isfinite(cen)
    c = np.where(inside, cen, 0.0)
    # fill gaps by interpolation, smooth, ramp
    idx = np.nonzero(inside)[0]
    if len(idx) == 0:
        return np.zeros(n[1:]), {"slabs": 0}
    c = np.interp(np.arange(len(t)), idx, c[idx])
    c = ndimage.gaussian_filter1d(c, max(1.0, spec.get("smooth", 0.15) / step))
    ramp = float(spec.get("ramp", 0.3))
    d = np.abs(t - a0) if a1 >= a0 else np.abs(a0 - t)
    g = np.clip(d / max(ramp, 1e-6), 0, 1); g = g * g * (3 - 2 * g)
    within = (t - a0) * np.sign(a1 - a0 if a1 != a0 else 1) >= 0
    off = np.where(within, c * g, 0.0)
    if ax == 1:
        out = np.broadcast_to(off[:, None], n[1:]).copy()
    else:
        out = np.broadcast_to(off[None, :], n[1:]).copy()
    return out, {"axis": spec.get("axis", "y"), "from": a0, "to": a1, "max_offset_u": round(float(np.abs(off).max()), 4)}


def symmetry_plane(occ, search):
    """best integer half-node shift s: occupancy vs its X mirror shifted by s nodes (plane at s/2 nodes from centre)"""
    best = (-1.0, 0)
    flip = occ[::-1]
    for s in range(-2 * search, 2 * search + 1):
        a = np.roll(flip, s, axis=0)
        inter = np.count_nonzero(a & occ); uni = np.count_nonzero(a | occ)
        v = inter / max(uni, 1)
        if v > best[0]:
            best = (v, s)
    return best[1], best[0]


def sd_capsule(P, a, b, r):
    a = np.asarray(a, float); b = np.asarray(b, float)
    ba = b - a; pa = P - a
    h = np.clip((pa @ ba) / max(float(ba @ ba), 1e-12), 0, 1)
    return np.linalg.norm(pa - h[:, None] * ba, axis=1) - r


def sd_box(P, lo, hi):
    c = (np.asarray(lo, float) + np.asarray(hi, float)) / 2; e = (np.asarray(hi, float) - np.asarray(lo, float)) / 2
    q = np.abs(P - c) - e
    return np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(1), 0)


def apply_cut(F, lo, step, cut, mirror=True):
    k = float(cut.get("k", 0.02))
    if "capsule" in cut:
        a, b, r = cut["capsule"]; pts = np.array([a, b]); rad = r
        fn = lambda P: sd_capsule(P, a, b, r)
    elif "sphere" in cut:
        c, r = cut["sphere"]; pts = np.array([c]); rad = r
        fn = lambda P: np.linalg.norm(P - np.asarray(c, float), axis=1) - r
    elif "box" in cut:
        blo, bhi = cut["box"]; pts = np.array([blo, bhi]); rad = 0.0
        fn = lambda P: sd_box(P, blo, bhi)
    else:
        raise ValueError(cut)
    fns = [fn]
    if mirror and cut.get("mirror", True):
        fns.append(lambda P: fn(P * np.array([-1.0, 1.0, 1.0])))
        pts = np.vstack([pts, pts * [-1, 1, 1]])
    g = rad + k + 3 * step
    i0 = np.clip(np.floor((pts.min(0) - g - lo) / step).astype(int), 0, np.array(F.shape) - 1)
    i1 = np.clip(np.ceil((pts.max(0) + g - lo) / step).astype(int) + 1, 0, np.array(F.shape))
    axes = [lo[d] + step * np.arange(i0[d], i1[d]) for d in range(3)]
    X, Y, Z = np.meshgrid(*axes, indexing="ij")
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
    d = np.min([f(P) for f in fns], axis=0).reshape(X.shape)
    sl = tuple(slice(a_, b_) for a_, b_ in zip(i0, i1))
    cur = F[sl]
    # smooth max(cur, -d) (subtract)
    h = np.clip(0.5 - 0.5 * (-d - cur) / k, 0.0, 1.0)
    F[sl] = (-d) * (1 - h) + cur * h + k * h * (1 - h)
    return int(np.count_nonzero(d < 0))


def build_solid(V, F, P, FI, cfg, report):
    from scipy import ndimage
    from scipy.spatial import cKDTree
    H = float(V[:, 2].max() - V[:, 2].min())
    step = float(cfg["step"] or round(H / cfg["steps_per_height"], 4))
    band = cfg["band_voxels"]
    lo_v, hi_v = V.min(0), V.max(0)
    pad = (band + 3 + cfg["close_voxels"]) * step
    hx = max(abs(lo_v[0]), abs(hi_v[0])) + pad + cfg["sym_search_voxels"] * step
    m = int(math.ceil(hx / step))
    lo = np.array([-m * step, lo_v[1] - pad, lo_v[2] - pad])
    n = np.array([2 * m + 1, int(math.ceil((hi_v[1] + pad - lo[1]) / step)) + 1,
                  int(math.ceil((hi_v[2] + pad - lo[2]) / step)) + 1])
    # shell nodes (nearest node of every surface sample)
    ij = np.clip(np.rint((P - lo) / step).astype(np.int64), 0, n - 1)
    shell = np.zeros(n, bool); shell[ij[:, 0], ij[:, 1], ij[:, 2]] = True
    k = int(cfg["close_voxels"])
    shell_d = ndimage.binary_dilation(shell, structure=ball(k)) if k > 0 else shell
    lab, nl = ndimage.label(~shell_d)
    border = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),
                                           lab[:, :, 0].ravel(), lab[:, :, -1].ravel()])).tolist()) - {0}
    ext0 = np.isin(lab, list(border))
    ext = (ndimage.binary_dilation(ext0, structure=ball(k)) & ~shell) if k > 0 else ext0
    interior_cavities = int(nl - len(border))
    near_ext = ndimage.binary_dilation(ext, iterations=1)
    bandm = ndimage.binary_dilation(shell, iterations=band)
    # exact distance to the proxy surface on the band; normal sign on shell nodes touching the exterior
    tri = V[F]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-18)
    tree = cKDTree(P)
    bi = np.nonzero(bandm)
    Q = lo + step * np.stack(bi, 1)
    dist, nn = tree.query(Q, workers=-1)
    sgn = np.einsum("ij,ij->i", Q - P[nn], fn[FI[nn]]) >= 0
    is_ext = ext[bi]; is_sh = (shell & near_ext)[bi]
    t = cfg["min_half_thickness"] * step
    val = np.where(is_ext, dist - t, np.where(is_sh, np.where(sgn, dist, -dist) - t, -(dist + t)))
    Fg = np.where(ext, band * step, -band * step).astype(np.float64)
    Fg[bi] = np.clip(val, -band * step, band * step)
    sh_frac = float(np.mean(sgn[is_sh])) if is_sh.any() else None
    if cfg["smooth_sigma"] > 0:
        Fg = ndimage.gaussian_filter(Fg, cfg["smooth_sigma"])
    report["solid"] = {"step": step, "grid": n.tolist(), "shell_nodes": int(shell.sum()), "close_voxels": k,
                       "sealed_cavities": interior_cavities, "min_half_thickness_u": round(t, 4),
                       "shell_nodes_signed_outside_frac": None if sh_frac is None else round(sh_frac, 3),
                       "smooth_sigma_vox": cfg["smooth_sigma"]}
    return Fg, lo, step


def occupancy_pieces(occ):
    from scipy import ndimage
    lab, nl = ndimage.label(occ)
    if nl == 0:
        return 0, []
    sizes = np.bincount(lab.ravel())[1:]
    return int(nl), sorted(sizes.tolist(), reverse=True)[:6]


# ======================================================================================================================
# 6. registration onto the sheet views
# ======================================================================================================================
VIEW_AXES = {"front": (0, 1, +1), "back": (0, 1, -1)}   # (horizontal axis index, projection axis index, horizontal sign)


def view_axes(view, facing):
    if view == "front":
        return 0, 1, +1.0
    if view == "back":
        return 0, 1, -1.0
    return 1, 0, (+1.0 if facing == "left" else -1.0)    # side: camera on +X (left flank) when the head faces image-left


def ortho_mask(occ, lo, step, view, facing):
    """silhouette of the occupancy at PPU px/u: (mask, h0 (units at px column 0), ztop (units at px row 0))"""
    from PIL import Image
    ha, pa, sg = view_axes(view, facing)
    proj = occ.any(axis=pa)                                  # (n_h, n_z) when pa removes one of x / y
    if ha == 1 and pa == 0:
        proj = proj                                          # (ny, nz)
    hcoord = lo[ha] + step * np.arange(occ.shape[ha])
    if sg < 0:
        proj = proj[::-1]; hcoord = -hcoord[::-1]
    img = proj.T[::-1]                                       # rows = z descending
    ztop = lo[2] + step * (occ.shape[2] - 1)
    s = step * PPU
    W = int(round(img.shape[1] * s)); Hh = int(round(img.shape[0] * s))
    im = Image.fromarray((img * 255).astype(np.uint8)).resize((W, Hh), Image.NEAREST)
    return np.asarray(im) > 127, hcoord[0] - 0.5 * step, ztop + 0.5 * step


def fft_offset(a, b):
    """translation (dy, dx) placing a's bbox crop inside b's bbox crop at maximum overlap (sheet_proxy.register's rule)"""
    from scipy.signal import fftconvolve
    def bb(m):
        ys, xs = np.nonzero(m); return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    ax0, ay0, ax1, ay1 = bb(a); bx0, by0, bx1, by1 = bb(b)
    A = a[ay0:ay1, ax0:ax1].astype(np.float32); B = b[by0:by1, bx0:bx1].astype(np.float32)
    corr = fftconvolve(B, A[::-1, ::-1], mode="full")
    r, c = np.unravel_index(np.argmax(corr), corr.shape)
    return (r - (A.shape[0] - 1), c - (A.shape[1] - 1)), (ax0, ay0), (bx0, by0)


def register_views(occ, lo, step, masks, facing, views):
    out = {}
    for v in views:
        if v not in masks or not masks[v].any():
            continue
        mm, h0, ztop = ortho_mask(occ, lo, step, v, facing)
        (dy, dx), (ax0, ay0), (bx0, by0) = fft_offset(mm, masks[v])
        # model px (pm_x, pm_y) -> sheet px: u = bx0 - ax0 + dx + pm_x ; pm_x = (h - h0) * PPU
        ox = bx0 - ax0 + dx - h0 * PPU
        oy = by0 - ay0 + dy + ztop * PPU
        ha, pa, sg = view_axes(v, facing)
        out[v] = {"ox": float(ox), "oy": float(oy), "h_axis": int(ha), "h_sign": float(sg),
                  "rule": "u = ox + 100 * sign * coord[h_axis]; v = oy - 100 * z (sheet px)"}
    return out


def model_to_px(reg, p):
    p = np.atleast_2d(np.asarray(p, float))
    u = reg["ox"] + PPU * reg["h_sign"] * p[:, reg["h_axis"]]
    v = reg["oy"] - PPU * p[:, 2]
    return np.stack([u, v], 1)


def resolve_point(spec, regs):
    """[x, y, z] in units, or {"side": [u, v], "front": [u, v], "back": [u, v], "x": .., "y": .., "z": ..} in SHEET PX
    (z from front, else back, else side; front/back give x, side gives y; explicit x / y / z override; x defaults 0)"""
    if isinstance(spec, (list, tuple)):
        return [float(c) for c in spec]
    p = {"x": None, "y": None, "z": None}
    for v in ("front", "back", "side"):          # z from the front first: the proxy is scaled to the front height
        if v in spec and v in regs:
            u, vv = spec[v]
            r = regs[v]
            h = (u - r["ox"]) / (PPU * r["h_sign"])
            z = (r["oy"] - vv) / PPU
            p["xyz"[r["h_axis"]]] = p["xyz"[r["h_axis"]]] if p["xyz"[r["h_axis"]]] is not None else h
            p["z"] = p["z"] if p["z"] is not None else z
    for k in ("x", "y", "z"):
        if k in spec:
            p[k] = float(spec[k])
    if p["x"] is None:
        p["x"] = 0.0
    if p["y"] is None or p["z"] is None:
        raise ValueError("point %r needs a side px (or explicit y) and a z" % (spec,))
    return [p["x"], p["y"], p["z"]]


def resolve_tree(obj, regs):
    """every dict carrying a view-px key or 'pt' -> resolved; lists of points inside known keys are resolved too"""
    PT_KEYS = {"base", "tip", "a", "b", "centre", "c", "lo", "hi", "at", "to", "anchor", "landmark", "root", "head",
               "tail"}
    LIST_KEYS = {"points", "spine", "tips"}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in LIST_KEYS and isinstance(v, list):
                out[k] = [resolve_point(p, regs) for p in v]
            elif k in PT_KEYS:
                is_pt = isinstance(v, dict) or (isinstance(v, list) and len(v) == 3 and
                                                all(isinstance(c, (int, float)) for c in v))
                out[k] = resolve_point(v, regs) if is_pt else v
            elif k in ("capsule",):
                out[k] = [resolve_point(v[0], regs), resolve_point(v[1], regs), float(v[2])]
            elif k in ("sphere",):
                out[k] = [resolve_point(v[0], regs), float(v[1])]
            elif k in ("box",):
                out[k] = [resolve_point(v[0], regs), resolve_point(v[1], regs)]
            else:
                out[k] = resolve_tree(v, regs)
        return out
    if isinstance(obj, list):
        return [resolve_tree(v, regs) for v in obj]
    return obj


def expand_silhouette_fans(parts, regs, masks, report):
    """'silhouette_fan' parts -> explicit 'blade' parts that follow the SHEET's wing / crest silhouette:
    rays from the root (in the side view) every step_deg over 'angles' (0 = toward the tail, 90 = up) march through the
    side mask inside side_box (gaps <= gap_px bridged); the reach per angle, smoothed, has local maxima = blade tips
    (>= min_sep_deg apart, >= min_len units); each tip's lateral x = the outermost front-mask px of the +X side at the
    tip's row (inside front_box), less an inset; blade radius from the angular spacing, clamped to 'radius'."""
    out, rep = [], []
    for spec in parts:
        if spec.get("type") != "silhouette_fan":
            out.append(spec)
            continue
        rs, rf = regs["side"], regs["front"]
        sm, fm = masks["side"], masks["front"]
        root = np.asarray(spec["root"], float)
        ru, rv = model_to_px(rs, root)[0]
        u0, v0, u1, v1 = spec["side_box"]
        a0, a1 = spec.get("angles", [10, 120])
        stp = float(spec.get("step_deg", 2.0))
        gap = int(spec.get("gap_px", 6))
        ang = np.arange(a0, a1 + 1e-9, stp)
        reach = np.zeros(len(ang))
        tips = []
        for i, a in enumerate(ang):
            d = np.array([math.cos(math.radians(a)) * rs["h_sign"], -math.sin(math.radians(a))])
            last, miss = 0.0, 0
            for t in range(1, 1200):
                u = int(round(ru + d[0] * t)); v = int(round(rv + d[1] * t))
                if not (u0 <= u < u1 and v0 <= v < v1) or v < 0 or u < 0 or v >= sm.shape[0] or u >= sm.shape[1]:
                    break
                if sm[v, u]:
                    last, miss = t, 0
                else:
                    miss += 1
                    if miss > gap:
                        break
            reach[i] = last
            tips.append((ru + d[0] * last, rv + d[1] * last))
        rsm = np.convolve(np.pad(reach, 2, mode="edge"), np.ones(5) / 5.0, mode="valid")
        sep = max(1, int(round(spec.get("min_sep_deg", 9.0) / stp)))
        cand = [i for i in range(len(ang)) if rsm[i] >= rsm[max(0, i - 1)] and rsm[i] >= rsm[min(len(ang) - 1, i + 1)]
                and rsm[i] / PPU >= spec.get("min_len", 0.5)]
        cand.sort(key=lambda i: -rsm[i])
        pick = []
        for i in cand:
            if all(abs(i - j) >= sep for j in pick):
                pick.append(i)
        if spec.get("fill", True):          # a blade every sep where no peak is near: the fan FILLS the silhouette
            for i in range(0, len(ang), sep):
                if rsm[i] / PPU >= spec.get("min_len", 0.5) and all(abs(i - j) >= (sep + 1) // 2 for j in pick):
                    pick.append(i)
        pick.sort()
        fx0, fy0, fx1, fy1 = spec["front_box"]
        rmin, rmax = spec.get("radius", [0.08, 0.3])
        tones = spec.get("tones") or [{}]
        for k, i in enumerate(pick):
            tu, tv = tips[i]
            y = (tu - rs["ox"]) / (PPU * rs["h_sign"]); z = (rs["oy"] - tv) / PPU
            frow = int(round(rf["oy"] - z * PPU))
            rows = fm[max(frow - 8, fy0):min(frow + 9, fy1), fx0:fx1]
            cols = np.nonzero(rows.any(0))[0]
            L = rsm[i] / PPU
            r = float(np.clip(L * math.tan(math.radians(0.5 * sep * stp)) * spec.get("width_k", 0.9), rmin, rmax))
            x = ((fx0 + cols.max()) - rf["ox"]) / (PPU * rf["h_sign"]) - r * spec.get("x_inset_k", 0.6) if len(cols) \
                else float(root[0]) + 0.5
            tip = [round(float(x), 3), round(float(y), 3), round(float(z), 3)]
            dvec = np.asarray(tip) - root
            base = (root + dvec / max(np.linalg.norm(dvec), 1e-9) * spec.get("root_spread", 0.12)).round(3).tolist()
            shape = dict(spec.get("shape", {})); shape.update(tones[k % len(tones)])
            out.append({"type": "blade", "name": "%s_%02d" % (spec["name"], k), "base": base, "tip": tip,
                        "radius": round(r, 3), "bone": spec.get("bone"), "mirror": spec.get("mirror", True),
                        "shape": shape, "why": "silhouette fan '%s': side ray %.0f deg reach %.0f px -> tip px (%.0f, %.0f)"
                                               % (spec["name"], ang[i], rsm[i], tu, tv)})
        rep.append({"name": spec["name"], "root_px": [round(float(ru), 1), round(float(rv), 1)], "blades": len(pick),
                    "angles_deg": [float(ang[i]) for i in pick], "reach_px": [round(float(rsm[i]), 1) for i in pick]})
    report["silhouette_fans"] = rep
    return out


# ======================================================================================================================
# 7. colour sources
# ======================================================================================================================
def sheet_labels(sheet_rgb, masks, views, chips_lab, erode_px):
    from scipy import ndimage
    out = {}
    for v in views:
        if v not in masks:
            continue
        m = ndimage.binary_erosion(masks[v], iterations=erode_px)
        ys, xs = np.nonzero(masks[v])
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        lab = np.full((y1 - y0, x1 - x0), -1, np.int16)
        sub = m[y0:y1, x0:x1]
        idx, _ = nearest_chip(sheet_rgb[y0:y1, x0:x1][sub], chips_lab)
        lab[sub] = idx
        out[v] = {"origin": [int(x0), int(y0)], "labels": lab}
    return out


def visible_mask(P, occ, lo, step, view, facing, tol_vox=1.6):
    """samples on the first occupied surface along the view direction (no mesh: the occupancy's depth map)"""
    ha, pa, sg = view_axes(view, facing)
    ij = np.clip(np.rint((P - lo) / step).astype(np.int64), 0, np.array(occ.shape) - 1)
    # depth along pa toward the camera: front camera at -Y (first occupied from low y); back at +Y; side +X or -X
    cam_low = {"front": True, "back": False, "side": facing != "left"}[view]
    ax = pa
    n = occ.shape[ax]
    idx = np.arange(n).reshape([-1 if d == ax else 1 for d in range(3)])
    big = np.where(occ, idx, n if cam_low else -1)
    first = big.min(axis=ax) if cam_low else big.max(axis=ax)        # (other two axes)
    if ax == 0:
        f = first[ij[:, 1], ij[:, 2]]
    else:
        f = first[ij[:, 0], ij[:, 2]]
    d = ij[:, ax] - f
    return (d <= tol_vox) if cam_low else (d >= -tol_vox)


def colour_fit(proxy_rgb, sheet_rgb):
    """affine colour transform sheet ~ [proxy, 1] @ A, two trimming rounds (drop the worst 25 %)"""
    X = np.hstack([proxy_rgb, np.ones((len(proxy_rgb), 1))])
    keep = np.ones(len(X), bool)
    A = None
    for _ in range(3):
        A, *_ = np.linalg.lstsq(X[keep], sheet_rgb[keep], rcond=None)
        r = np.linalg.norm(X @ A - sheet_rgb, axis=1)
        keep = r <= np.percentile(r[keep], 75)
    return A, float(np.median(np.linalg.norm(X @ A - sheet_rgb, axis=1)))


# ======================================================================================================================
# stage 1 driver
# ======================================================================================================================
def stage_solid(unit, out_npz, quiet=False):
    from PIL import Image
    from scipy import ndimage
    t0 = time.time()
    sys.path.insert(0, HERE)
    import sheet_proxy as SP
    pp = paths(unit)
    cfg, _raw = load_cfg(unit)
    intake = json.load(open(pp["intake"]))
    proxy = intake.get("proxy")
    assert proxy, "intake.json has no proxy block: run sheet_proxy.py %s first" % unit
    glb = os.path.join(pp["ref"], proxy["glb"])
    digest = sha_file(glb)
    assert digest == proxy["sha256"], "the frozen proxy changed: %s != %s" % (digest[:12], proxy["sha256"][:12])
    report = {"unit": unit, "proxy": {"glb": proxy["glb"], "sha256": digest, "gate": (proxy.get("gate") or {}).get("verdict"),
                                      "mean_iou": proxy.get("mean_iou")}}
    Vgl, Fp, UV, TEXI, images = read_proxy(glb)
    V = to_sheet(Vgl, proxy["transform"])
    sl = proxy.get("side_len_over_front_h") or {}
    if cfg["fit_side_length"] and sl.get("rel_err"):
        ky = 1.0 / (1.0 + float(sl["rel_err"]))
        V[:, 1] *= ky
        report["proxy"]["side_length_fit"] = {"scale_y": round(ky, 4), "proxy_rel_err": sl["rel_err"],
                                              "rule": "the sheet is the spec: Y stretched to the sheet's side length"}
    rf = cfg.get("refloor")
    if rf:                          # the floor at the FOOT contact (not a tail curl on the ground), then the front height
        wins = rf.get("y_windows") or [rf["y"]]          # several windows (front + hind feet): the floor at the lowest
        xmin = float(rf.get("x_abs_min", 0.0))           # |x| >= this: the legs, not a midline belly / tail
        zs = []
        for w in wins:
            sel = (V[:, 1] >= w[0]) & (V[:, 1] <= w[1]) & (np.abs(V[:, 0]) >= xmin)
            zs.append(float(np.percentile(V[sel, 2], rf.get("pct", 0.2))))
        z0 = min(zs)
        V[:, 2] -= z0
        k = 1.0
        if rf.get("rescale_to_front_height", True):
            hf = float(intake["views"]["front"]["height_units"])
            k = hf / float(V[:, 2].max())
            V = V * k
        report["proxy"]["refloor"] = {"foot_contact_z_u": round(z0, 4), "rescale": round(k, 4), "y_windows": wins,
                                      "window_z_u": [round(z, 4) for z in zs], "x_abs_min": xmin,
                                      "rule": "sheet_proxy put the floor at the lowest point (a tail curl): the feet hung "
                                              "z0 above it; floor moved to the feet, then scaled to the sheet front height"}
    if cfg["mode"] == "light":
        return stage_light(unit, out_npz, cfg, intake, pp, report, V, Fp, UV, TEXI, images, quiet, t0)
    H = float(V[:, 2].max() - V[:, 2].min())
    step0 =float(cfg["step"] or round(H / cfg["steps_per_height"], 4))
    P, FI, Cs = sample_surface(V, Fp, UV, TEXI, images, 0.5 * step0)
    report["proxy"].update({"tris": int(len(Fp)), "verts": int(len(V)), "height_u": round(H, 4), "samples": int(len(P)),
                            "textured": bool(images)})
    Fg, lo, step = build_solid(V, Fp, P, FI, cfg, report)
    occ0 = Fg < 0
    report["solid"]["pieces_before"] = occupancy_pieces(occ0)
    # 3. straighten (lateral shear), 4. symmetrise
    xshift = np.zeros(Fg.shape[1:])
    st_rep = []
    for spec in cfg["straighten"]:
        off, r_ = straighten_offsets(Fg < 0, lo, step, spec)
        xshift = xshift + off
        Fg = shift_x(Fg, off / step)
        st_rep.append(r_)
    plane_u = 0.0
    sym = {"enabled": bool(cfg["symmetrise"])}
    if cfg["symmetrise"]:
        occ = Fg < 0
        s, iou_m = symmetry_plane(occ, cfg["sym_search_voxels"])
        plane_nodes = -s / 2.0          # roll(flip, s) matches occ -> the mirror plane sits s/2 nodes off the centre
        plane_u = -plane_nodes * step
        Fg = shift_x(Fg, np.full(Fg.shape[1:], -plane_nodes))
        mirror_iou_before = iou_m
        Fg = 0.5 * (Fg + Fg[::-1])
        sym.update({"plane_x_u": round(float(plane_u), 4), "mirror_iou_before": round(float(mirror_iou_before), 4),
                    "rule": "F_sym = (F + F mirrored in X) / 2 (an average, not a bisect)"})
        xshift = xshift + plane_u
    report["straighten"] = st_rep
    report["symmetrise"] = sym
    # hard floor at z = 0 (the proxy's floor)
    z = lo[2] + step * np.arange(Fg.shape[2])
    Fg = np.maximum(Fg, (0.0 - z)[None, None, :])
    # 6. registration (before the cuts: the cuts are cited in sheet px on this frame)
    sheet_rgb = np.asarray(Image.open(SP.sheet_path(intake)).convert("RGB"))
    masks = SP.view_masks(intake, pp["ref"], sheet_rgb)
    SP.clip_below_floor(intake, masks)
    facing = ((intake.get("side_facing") or {}).get("facing")) or "left"
    views = [v for v in ("front", "back", "side") if v in masks]
    regs = register_views(Fg < 0, lo, step, masks, facing, views)
    rcfg = resolve_tree({k: cfg[k] for k in ("cuts", "parts", "rig", "facing", "focus")}, regs)
    rcfg["parts"] = expand_silhouette_fans(rcfg["parts"], regs, masks, report)
    # 5. cuts
    cut_rep = []
    for c in rcfg["cuts"]:
        nin = apply_cut(Fg, lo, step, c)
        cut_rep.append({"shape": [k for k in ("capsule", "sphere", "box") if k in c][0], "nodes_inside": nin,
                        "why": c.get("why")})
    report["cuts"] = cut_rep
    occ = Fg < 0
    report["solid"]["pieces_after"] = occupancy_pieces(occ)
    # 8. melt report: what a ball of radius thin_report_u cannot reach
    r_vox = cfg["thin_report_u"] / step
    opened = ndimage.binary_opening(occ, structure=ball(r_vox))
    thin = occ & ~opened
    lab, nl = ndimage.label(thin)
    comps = []
    if nl:
        sizes = np.bincount(lab.ravel())
        for ci in np.argsort(-sizes[1:])[:12] + 1:
            if sizes[ci] * step ** 3 < 0.002:
                continue
            ijk = np.argwhere(lab == ci)
            plo = lo + step * ijk.min(0); phi = lo + step * ijk.max(0)
            row = {"volume_u3": round(float(sizes[ci] * step ** 3), 4), "lo_u": plo.round(3).tolist(),
                   "hi_u": phi.round(3).tolist()}
            for v in ("front", "side"):
                if v in regs:
                    q = model_to_px(regs[v], np.array([plo, phi]))
                    row[v + "_px"] = [int(q[:, 0].min()), int(q[:, 1].min()), int(q[:, 0].max()), int(q[:, 1].max())]
            comps.append(row)
    report["melt_candidates"] = {"rule": "solid parts thinner than 2 x %.3f u (a ball that radius cannot enter): "
                                         "the blades / crowns / horns / wings Pixal3D fuses; replace them via cuts + "
                                         "parts in the finish config" % cfg["thin_report_u"],
                                 "thin_volume_frac": round(float(thin.sum() / max(occ.sum(), 1)), 4),
                                 "components": comps}
    # 7. colour sources
    chips, chip_src = chips_of(intake)
    assert chips, "no palette chips in intake.json (swatches / palette_fallback)"
    chips_lab = srgb_to_lab(np.array(chips, float))
    names = chip_names(cfg, chips)
    ccfg = cfg["colour"]
    sv = [v for v in ccfg["sheet_views"] if v in regs]
    labs = sheet_labels(sheet_rgb, masks, sv, chips_lab, ccfg["outline_erode_px"])
    # proxy samples into the finished frame (straighten + plane shift), drift-corrected, snapped
    ij = np.clip(np.rint((P - lo) / step).astype(np.int64), 0, np.array(Fg.shape) - 1)
    Pf = P.copy()
    Pf[:, 0] = P[:, 0] - xshift[ij[:, 1], ij[:, 2]]
    vis = visible_mask(Pf, occ, lo, step, "front", facing) if "front" in regs else np.zeros(len(P), bool)
    fit = None
    Cc = Cs.copy()
    if images and vis.sum() > 500 and "front" in regs:
        q = model_to_px(regs["front"], Pf[vis]).round().astype(int)
        mfront = masks["front"]
        ok = (q[:, 1] >= 0) & (q[:, 1] < mfront.shape[0]) & (q[:, 0] >= 0) & (q[:, 0] < mfront.shape[1])
        ok[ok] = ndimage.binary_erosion(mfront, iterations=4)[q[ok, 1], q[ok, 0]]
        if ok.sum() > 500:
            A, med = colour_fit(Cs[vis][ok], sheet_rgb[q[ok, 1], q[ok, 0]].astype(float))
            used = med <= cfg["colour_fit_max_residual"]
            if used:
                Cc = np.clip(np.hstack([Cs, np.ones((len(Cs), 1))]) @ A, 0, 255)
            fit = {"pairs": int(ok.sum()), "median_residual_rgb": round(med, 2), "A": np.round(A, 4).tolist(),
                   "applied": bool(used),
                   "rule": "sheet_rgb ~ [proxy_rgb, 1] @ A, front-visible samples, trimmed lstsq; applied only when "
                           "the median residual <= %.0f (a prismatic / multi-hue texture is not affine)"
                           % cfg["colour_fit_max_residual"]}
    pl, _pd = nearest_chip(Cc, chips_lab)
    raw_l, _ = nearest_chip(Cs, chips_lab)
    rng = np.random.default_rng(0)
    keep = np.sort(rng.choice(len(Pf), min(len(Pf), 250000), replace=False))
    report["colour"] = {"chips": dict(zip(names, chips)), "chip_source": chip_src, "drift_fit": fit,
                        "proxy_chip_share_raw": {names[i]: round(float((raw_l == i).mean()), 4) for i in range(len(chips))},
                        "proxy_chip_share_fitted": {names[i]: round(float((pl == i).mean()), 4) for i in range(len(chips))},
                        "sheet_views": sv}
    # write
    meta = {"report": report, "regs": regs, "resolved": rcfg, "cfg": cfg, "chip_names": names, "chips": chips,
            "facing": facing, "sheet_label_views": {v: {"origin": d["origin"]} for v, d in labs.items()}}
    Fg = Fg.astype(np.float32)
    report["grid_sha"] = sha_arr(Fg[::2, ::2, ::2])
    arrays = {"F": Fg, "lo": lo, "step": np.array([step]), "samp_p": Pf[keep].astype(np.float32),
              "samp_chip": pl[keep].astype(np.int16), "meta": np.frombuffer(json.dumps(meta).encode(), np.uint8)}
    for v, d in labs.items():
        arrays["lab_" + v] = d["labels"]
    os.makedirs(os.path.dirname(os.path.abspath(out_npz)), exist_ok=True)
    np.savez(out_npz, **arrays)
    report["seconds"] = round(time.time() - t0, 1)
    if not quiet:
        print("SOLID", json.dumps({"grid_sha": report["grid_sha"], "solid": report["solid"], "symmetrise": sym,
                                   "straighten": st_rep, "cuts": cut_rep, "seconds": report["seconds"]}))
        print("MELT", json.dumps(report["melt_candidates"]))
        print("FANS", json.dumps(report.get("silhouette_fans")))
        print("COLOUR", json.dumps(report["colour"]))
        print("REGS", json.dumps(regs))
    return report


# ======================================================================================================================
# stage 1, LIGHT mode: the proxy as generated, only made shippable
# ======================================================================================================================
def stage_light(unit, out_npz, cfg, intake, pp, report, V, Fp, UV, TEXI, images, quiet, t0):
    """LIGHT finish (artist 2026-10-11: the raw proxy's look is the pick; "in trying to fix the symmetry and make changes
    we broke some things, like the mouth"). Stage 1 only makes the proxy WATERTIGHT: the same shell / flood-fill /
    exact-distance SDF as the full finish, on a finer grid (light.steps_per_height), no smoothing, no straighten, no
    symmetrise, no cuts, no parts, no colour snapping. The proxy mesh itself (frame-fixed by refloor) and its texture go
    into the npz untouched: the Blender half bakes colour / normal / AO from THEM, not from the remesh."""
    from PIL import Image
    sys.path.insert(0, HERE)
    import sheet_proxy as SP
    lc = cfg["light"]
    H = float(V[:, 2].max() - V[:, 2].min())
    step = float(lc.get("step") or round(H / lc["steps_per_height"], 4))
    P, FI, Cs = sample_surface(V, Fp, UV, TEXI, images, 0.5 * step)
    report["proxy"].update({"tris": int(len(Fp)), "verts": int(len(V)), "height_u": round(H, 4), "samples": int(len(P)),
                            "textured": bool(images), "z_range_u": [round(float(V[:, 2].min()), 4),
                                                                    round(float(V[:, 2].max()), 4)]})
    scfg = dict(cfg)
    scfg.update({"step": step, "close_voxels": int(lc["close_voxels"]), "smooth_sigma": float(lc["smooth_sigma"]),
                 "min_half_thickness": float(lc["min_half_thickness"]), "band_voxels": int(lc["band_voxels"]),
                 "sym_search_voxels": 0})
    Fg, lo, step = build_solid(V, Fp, P, FI, scfg, report)
    report["solid"]["pieces"] = occupancy_pieces(Fg < 0)
    report["solid"]["rule"] = ("LIGHT: watertight remesh only (no smoothing / straighten / symmetrise / cuts); the surface "
                               "sits min_half_thickness x step = %.4f u outside the proxy" % (lc["min_half_thickness"] * step))
    if lc.get("cuts"):                # opt-in: a duplicate PART Pixal3D invented (a forked tail), removed, never mirrored
        report["solid"]["light_cuts"] = [{"shape": [k for k in ("capsule", "sphere", "box") if k in c][0],
                                          "nodes_inside": apply_cut(Fg, lo, step, c, mirror=False), "why": c.get("why")}
                                         for c in lc["cuts"]]
    if lc["floor_clip"]:
        z = lo[2] + step * np.arange(Fg.shape[2])
        Fg = np.maximum(Fg, (0.0 - z)[None, None, :])
    sheet_rgb = np.asarray(Image.open(SP.sheet_path(intake)).convert("RGB"))
    masks = SP.view_masks(intake, pp["ref"], sheet_rgb)
    SP.clip_below_floor(intake, masks)
    facing = ((intake.get("side_facing") or {}).get("facing")) or "left"
    views = [v for v in ("front", "back", "side") if v in masks]
    regs = register_views(Fg < 0, lo, step, masks, facing, views)
    rcfg = resolve_tree({k: cfg[k] for k in ("rig", "facing", "focus")}, regs)
    rcfg["repose"] = resolve_tree(cfg.get("repose") or {}, regs)
    rcfg["dense"] = resolve_tree(lc.get("dense") or [], regs)
    # optional sheet drift fit, applied to the texture pixels (off by default: the artist picked the raw colours)
    fit = None
    tex = {k: v.copy() for k, v in images.items()}
    if lc["colour_fit"] and images and "front" in regs:
        vis = visible_mask(P, Fg < 0, lo, step, "front", facing)
        q = model_to_px(regs["front"], P[vis]).round().astype(int)
        mf = masks["front"]
        ok = (q[:, 1] >= 0) & (q[:, 1] < mf.shape[0]) & (q[:, 0] >= 0) & (q[:, 0] < mf.shape[1])
        from scipy import ndimage
        ok[ok] = ndimage.binary_erosion(mf, iterations=4)[q[ok, 1], q[ok, 0]]
        if ok.sum() > 500:
            A, med = colour_fit(Cs[vis][ok], sheet_rgb[q[ok, 1], q[ok, 0]].astype(float))
            used = med <= cfg["colour_fit_max_residual"]
            if used:
                for k_ in tex:
                    t_ = tex[k_].reshape(-1, 3).astype(float)
                    tex[k_] = np.clip(np.hstack([t_, np.ones((len(t_), 1))]) @ A, 0, 255).round().astype(np.uint8) \
                        .reshape(tex[k_].shape)
            fit = {"pairs": int(ok.sum()), "median_residual_rgb": round(med, 2), "applied": bool(used)}
    report["colour"] = {"mode": "light: the proxy texture as generated" + (" + drift fit" if fit and fit["applied"] else ""),
                        "drift_fit": fit, "textures": {str(k): list(v.shape) for k, v in tex.items()}}
    meta = {"report": report, "regs": regs, "resolved": rcfg, "cfg": cfg, "facing": facing, "mode": "light"}
    Fg = Fg.astype(np.float32)
    report["grid_sha"] = sha_arr(Fg[::2, ::2, ::2])
    report["proxy_sha"] = sha_arr(V)
    arrays = {"F": Fg, "lo": lo, "step": np.array([step]), "prox_V": V.astype(np.float64), "prox_F": Fp.astype(np.int64),
              "prox_UV": UV.astype(np.float64), "prox_texi": TEXI.astype(np.int64),
              "meta": np.frombuffer(json.dumps(meta).encode(), np.uint8)}
    for k_, t_ in tex.items():
        arrays["tex_%d" % int(k_)] = t_
    os.makedirs(os.path.dirname(os.path.abspath(out_npz)), exist_ok=True)
    np.savez(out_npz, **arrays)
    report["seconds"] = round(time.time() - t0, 1)
    if not quiet:
        print("LIGHT_SOLID", json.dumps({"grid_sha": report["grid_sha"], "solid": report["solid"],
                                         "refloor": report["proxy"].get("refloor"), "seconds": report["seconds"]}))
        print("COLOUR", json.dumps(report["colour"]))
        print("REGS", json.dumps(regs))
    return report


# ======================================================================================================================
# init: stub build + config template + chip palette
# ======================================================================================================================
STUB = '''"""{unit} -- the BUILD (proxy finish): a frozen Pixal3D sheet proxy finished into a contract unit by
proxy_finish_build.py (config: improve/{unit}_finish.json). Generated by `proxy_finish.py init {unit}`; edit the config,
not this file. Same argv as every house build: --preview <blend>, --digest-only <json>.

    blender --background --factory-startup --python improve/{unit}_build.py -- [--digest-only <json>] [--preview <blend>]
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proxy_finish_build as PFB  # noqa: E402

PFB.run("{unit}")
'''


def stage_init(unit, force=False):
    pp = paths(unit)
    intake = json.load(open(pp["intake"]))
    done = []
    if force or not os.path.exists(pp["stub"]):
        open(pp["stub"], "w", encoding="utf-8").write(STUB.format(unit=unit))
        done.append(("stub", pp["stub"]))
    if force or not os.path.exists(pp["cfg"]):
        tmpl = {"unit": unit, "char_id": unit, "tier": "regular", "tri_budget": [8000, 22000], "body_tris": 14000,
                "colour": {"chip_names": None, "merge": {}},
                "straighten": [], "cuts": [], "parts": [],
                "rig": {"spine": None, "chains": []},
                "_doc": "points: [x, y, z] sheet units, or {'side': [u, v], 'front': [u, v]} SHEET PX (see proxy_finish.py)"}
        json.dump(tmpl, open(pp["cfg"], "w", encoding="utf-8"), indent=1)
        done.append(("config", pp["cfg"]))
    cfg, _ = load_cfg(unit)
    chips, src = chips_of(intake)
    names = chip_names(cfg, chips)
    if force or not os.path.exists(pp["palette"]):
        os.makedirs(os.path.dirname(pp["palette"]), exist_ok=True)
        regions = {}
        merge = cfg["colour"].get("merge") or {}
        if cfg["mode"] == "light":      # light: the colour is the baked proxy texture; one white region tints it
            regions[cfg["light"]["region"]] = {"rgb": [255, 255, 255], "note": "tint over the baked proxy albedo "
                                                                               "(light mode; white = as generated)"}
            names, chips = [], []
        for nm, rgb in zip(names, chips):
            if nm in merge:
                continue
            regions[nm] = {"rgb": rgb, "note": "sheet chip (intake %s), pixel-sampled by sheet_intake.py" % src}
        for part in cfg.get("parts") or []:
            for r in part.get("regions_extra", []):
                regions.setdefault(r["name"], {"rgb": r["rgb"], "note": r.get("note", "part tone")})
        pal = {"unit": unit, "skin": "default",
               "description": "%s default look: one region per sheet palette chip (proxy_finish.py init; chips from "
                              "intake.json %s)" % (unit, src),
               "regions": regions, "material": {"roughness": 0.85, "emission_strength": 1.0, "alpha": 1.0}}
        json.dump(pal, open(pp["palette"], "w", encoding="utf-8"), indent=1)
        done.append(("palette", pp["palette"]))
    for k, p in done:
        print("INIT %-8s %s" % (k, p))
    if not done:
        print("INIT nothing written (files exist; --force to overwrite)")


# ======================================================================================================================
# gate: finished vs sheet AND vs the raw proxy (+ hand build), mirror test, open edges
# ======================================================================================================================
def glb_mesh_rest(glb):
    """triangles of every mesh primitive of a glb (bind pose), in the sheet frame Z up (glTF Y up -> Z up)"""
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import load_glb
    m = load_glb(glb)
    v = m["verts"].astype(np.float64)
    return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1), m["faces"].astype(np.int64)


def open_edge_count(F, V=None):
    if V is not None:               # weld first: a glb splits vertices on UV seams
        _u, inv = np.unique(np.round(V, 6), axis=0, return_inverse=True)
        F = inv.reshape(-1)[F]
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    _, c = np.unique(e, axis=0, return_counts=True)
    return int((c == 1).sum()), int((c > 2).sum())


def mirror_test(V):
    from scipy.spatial import cKDTree
    Vm = V * np.array([-1.0, 1.0, 1.0])
    cx = (V[:, 0].min() + V[:, 0].max()) / 2
    Vm[:, 0] += 2 * cx
    d, _ = cKDTree(V).query(Vm, workers=-1)
    return {"max_u": round(float(d.max()), 5), "p99_u": round(float(np.percentile(d, 99)), 5),
            "mean_u": round(float(d.mean()), 6)}


def score_mesh(V, F, refs, facing):
    sys.path.insert(0, HERE)
    import sheet_proxy as SP
    out = {}
    for v, ref in refs.items():
        Vv = V.copy()
        if v == "side" and facing == "right":
            pass
        met, _A, _B = SP.view_metrics(SP.splat(Vv, F, v), ref)
        out[v] = {"iou": met["iou"], "row_err_pct": met["row_err_pct"], "w_u": met["w_u"], "h_u": met["h_u"]}
    return out


def stage_gate(unit, hand_glb=None, ref_glb=None):
    sys.path.insert(0, HERE)
    import sheet_proxy as SP
    from PIL import Image
    pp = paths(unit)
    intake = json.load(open(pp["intake"]))
    proxy = intake["proxy"]
    facing = ((intake.get("side_facing") or {}).get("facing")) or "left"
    sheet_rgb = np.asarray(Image.open(SP.sheet_path(intake)).convert("RGB"))
    masks = SP.view_masks(intake, pp["ref"], sheet_rgb)
    SP.clip_below_floor(intake, masks)
    refs = {}
    for v in ("front", "back", "side"):
        if v in masks and masks[v].any():
            x0, y0, x1, y1 = SP.bbox(masks[v])
            m = masks[v][y0:y1, x0:x1]
            refs[v] = m[:, ::-1] if (v == "side" and facing == "right") else m
    Vf, Ff = glb_mesh_rest(pp["rigged_glb"])
    Vp, Fp = SP.load_mesh(os.path.join(pp["ref"], proxy["glb"]))
    tf = proxy["transform"]
    Vp = tf["scale"] * (Vp @ np.array(tf["rotation"]).T - np.array(tf["offset"]))
    res = {"unit": unit, "views_scored": list(refs), "facing": facing}
    res["finished"] = score_mesh(Vf, Ff, refs, facing)
    res["proxy_raw"] = score_mesh(Vp, Fp, refs, facing)
    # finished vs the raw proxy, silhouette to silhouette (how much the finish changed the proxy's shape)
    res["finished_vs_proxy"] = {}
    for v in refs:
        met, _A, _B = SP.view_metrics(SP.splat(Vf, Ff, v), SP.splat(Vp, Fp, v))
        res["finished_vs_proxy"][v] = {"iou": met["iou"], "row_err_pct": met["row_err_pct"]}
    if hand_glb and os.path.exists(hand_glb):
        Vh, Fh = glb_mesh_rest(hand_glb)
        res["hand_build"] = score_mesh(Vh, Fh, refs, facing)
        res["hand_build"]["_glb"] = hand_glb
    # the artist-PICKED reference (a previous finish's glb; default: the raw proxy) and the light finish before its repose
    if ref_glb and os.path.exists(ref_glb):
        Vr, Fr = glb_mesh_rest(ref_glb)
        res["picked_ref"] = score_mesh(Vr, Fr, refs, facing)
        res["picked_ref"]["_glb"] = ref_glb
    else:
        Vr, Fr = Vp, Fp
        ij = os.path.join(ROOT, "improved", unit + ".json")
        rf = (json.load(open(ij))["stage1"]["proxy"].get("refloor") or {}) if os.path.exists(ij) else {}
        if rf:                      # the raw proxy in the finished frame (feet floored, front height): shape vs shape
            Vr = (Vp - np.array([0.0, 0.0, rf["foot_contact_z_u"]])) * rf["rescale"]
            res["picked_frame"] = "raw proxy refloored (z - %.4f) and scaled x%.4f like the finish" % (
                rf["foot_contact_z_u"], rf["rescale"])
    res["finished_vs_picked"] = {}
    for v in refs:
        met, _A, _B = SP.view_metrics(SP.splat(Vf, Ff, v), SP.splat(Vr, Fr, v))
        res["finished_vs_picked"][v] = {"iou": met["iou"], "row_err_pct": met["row_err_pct"]}
    pre = os.path.join(pp["renders"], "_light", "prerepose_low.npz")
    if os.path.exists(pre) and os.path.getmtime(pre) >= os.path.getmtime(pp["rigged_glb"]) - 3600:
        Zp = np.load(pre)
        res["prerepose"] = score_mesh(Zp["V"], Zp["F"], refs, facing)
        res["prerepose_vs_picked"] = {}
        for v in refs:
            met, _A, _B = SP.view_metrics(SP.splat(Zp["V"], Zp["F"], v), SP.splat(Vr, Fr, v))
            res["prerepose_vs_picked"][v] = {"iou": met["iou"], "row_err_pct": met["row_err_pct"]}
    for k in ("finished", "proxy_raw", "hand_build", "picked_ref", "prerepose", "finished_vs_picked",
              "prerepose_vs_picked"):
        if k in res:
            ious = [res[k][v]["iou"] for v in refs if v in res[k]]
            res[k]["_mean_iou"] = round(float(np.mean(ious)), 4)
    rj = json.load(open(pp["rigged_blend"][:-6] + ".json")) if os.path.exists(pp["rigged_blend"][:-6] + ".json") else {}
    oe, nm = open_edge_count(Ff, Vf)
    res["mesh"] = {"glb_tris": int(len(Ff)), "glb_open_edges_welded": oe, "glb_nonmanifold_edges_welded": nm,
                   "build_open_edges": (rj.get("finish") or {}).get("open_edges"),
                   "mirror": mirror_test(Vf), "build_mirror": (rj.get("finish") or {}).get("mirror")}
    for v in refs:
        fv = res["finished"][v]; pv = res["proxy_raw"][v]
        hv = (res.get("hand_build") or {}).get(v)
        rv = (res.get("picked_ref") or {}).get(v)
        pr = (res.get("prerepose") or {}).get(v)
        print("FINISH_GATE view=%-5s finished iou=%.3f row_err=%.1f%% | proxy iou=%.3f | finished-vs-proxy iou=%.3f"
              " | finished-vs-picked iou=%.3f%s%s%s"
              % (v, fv["iou"], fv["row_err_pct"], pv["iou"], res["finished_vs_proxy"][v]["iou"],
                 res["finished_vs_picked"][v]["iou"], " | picked iou=%.3f" % rv["iou"] if rv else "",
                 " | pre-repose iou=%.3f vs-picked %.3f" % (pr["iou"], res["prerepose_vs_picked"][v]["iou"]) if pr else "",
                 " | hand iou=%.3f" % hv["iou"] if hv else ""))
    print("FINISH_GATE mean finished=%.3f proxy=%.3f finished-vs-picked=%.3f%s%s%s"
          % (res["finished"]["_mean_iou"], res["proxy_raw"]["_mean_iou"], res["finished_vs_picked"]["_mean_iou"],
             " picked=%.3f" % res["picked_ref"]["_mean_iou"] if "picked_ref" in res else "",
             " pre-repose=%.3f pre-repose-vs-picked=%.3f" % (res["prerepose"]["_mean_iou"],
                                                            res["prerepose_vs_picked"]["_mean_iou"])
             if "prerepose" in res else "",
             " hand=%.3f" % res["hand_build"]["_mean_iou"] if "hand_build" in res else ""))
    print("FINISH_GATE mesh", json.dumps(res["mesh"]))
    os.makedirs(pp["renders"], exist_ok=True)
    json.dump(res, open(os.path.join(pp["renders"], unit + "_finish_gate.json"), "w"), indent=1)
    return res


# ======================================================================================================================
# panel: sheet | raw proxy | finished | hand build
# ======================================================================================================================
PANEL_SCRIPT = r'''
import bpy, json, math, sys
from mathutils import Matrix, Vector
argv = sys.argv[sys.argv.index("--") + 1:]
src, tfp, outp = argv[0], argv[1], argv[2]
if src.endswith(".glb"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
    tf = json.load(open(tfp)) if tfp != "-" else None
    if tf:
        R = Matrix(tf["rotation"]); off = Vector(tf["offset"]); s = tf["scale"]
        M = Matrix.Scale(s, 4) @ Matrix.Translation(-off) @ R.to_4x4()
        root = bpy.data.objects.new("norm", None); bpy.context.scene.collection.objects.link(root)
        for o in list(bpy.context.scene.objects):
            if o is not root and o.parent is None:
                o.parent = root; o.matrix_parent_inverse = Matrix.Identity(4)
        root.matrix_world = M
else:
    bpy.ops.wm.open_mainfile(filepath=src)
scene = bpy.context.scene
for o in scene.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
for o in list(scene.objects):
    if o.type in ("CAMERA", "LIGHT"):
        bpy.data.objects.remove(o, do_unlink=True)
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    for v in me.vertices:
        w = o.matrix_world @ v.co
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    ev.to_mesh_clear()
scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
rig = bpy.data.objects.new("lr", None); scene.collection.objects.link(rig)
def light(name, energy, rot, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot]; o.parent = rig
light("k", 3.2, (50, 0, 150)); light("f", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)); light("r", 2.0, (60, 0, 10))
rig.rotation_euler = (0, 0, math.radians(180))
cam_d = bpy.data.cameras.new("c"); cam_d.lens = 50
cam = bpy.data.objects.new("c", cam_d); scene.collection.objects.link(cam); scene.camera = cam
half = math.atan(18.0 / 50.0)
H = float(argv[3]) if len(argv) > 3 else (hi - lo).length / 2
c = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, H * 0.5))
rad = H * 0.72
scene.render.resolution_x = scene.render.resolution_y = 640
scene.render.film_transparent = True
for tag, ang, el in (("front", 0.0, 5.0), ("side", 90.0, 5.0), ("threequarter", 40.0, 15.0)):
    a, e = math.radians(ang), math.radians(el)
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = c + d * (rad / math.sin(half) * 1.05)
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_d.clip_start = rad * 0.01; cam_d.clip_end = rad * 100
    scene.render.filepath = outp + "_" + tag + ".png"
    bpy.ops.render.render(write_still=True)
if len(argv) > 4 and argv[4] != "-":
    cu = json.load(open(argv[4]))
    for tag, spec in cu.items():
        a, e = math.radians(spec["yaw"]), math.radians(spec.get("elev", 4.0))
        cc = Vector(spec["c"]); rr = float(spec["r"])
        d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
        cam.location = cc + d * (rr / math.sin(half))
        cam.rotation_euler = (cc - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam_d.clip_start = rr * 0.01; cam_d.clip_end = rr * 100
        scene.render.filepath = outp + "_" + tag + ".png"
        bpy.ops.render.render(write_still=True)
print("PANEL_RENDERED", outp)
'''


def stage_panel(unit, hand=None, ref=None, ref_title=None, closeup=False):
    """sheet | picked reference | finished | [hand build], front / side / threequarter (+ a face close-up row).
    ref: a .blend / .glb of the artist-picked version, or "proxy" (the raw proxy through its transform); without it the
    raw proxy column is shown as before. Light mode defaults to ref = "proxy" and, when the config pins a rigid face,
    a face row: each model framed head-on to ITS OWN face (the raw proxy's head is turned by the repose's head yaw;
    the finished one faces -Y), so the faces can be compared like for like."""
    from PIL import Image, ImageDraw
    pp = paths(unit)
    intake = json.load(open(pp["intake"]))
    proxy = intake["proxy"]
    cfg, _ = load_cfg(unit)
    light = cfg["mode"] == "light"
    rigid = (cfg.get("repose") or {}).get("rigid") or []
    if light and ref is None:
        ref = "proxy"
    if light and rigid:
        closeup = True
    closeup = closeup and bool(rigid)
    work = os.path.join(pp["renders"], "_panel")
    os.makedirs(work, exist_ok=True)
    for f in os.listdir(work):
        if f.endswith(".png"):
            os.remove(os.path.join(work, f))
    scr = os.path.join(work, "_panel_render.py")
    open(scr, "w").write(PANEL_SCRIPT)
    tfp = os.path.join(work, "_tf.json")
    json.dump(proxy["transform"], open(tfp, "w"))
    H = max(v["height_units"] for v in intake["views"].values() if "height_units" in v)
    cu = {}
    if closeup:
        imp = json.load(open(os.path.join(ROOT, "improved", unit + ".json")))
        lo_, hi_ = np.array(rigid[0]["box"][0], float), np.array(rigid[0]["box"][1], float)
        c_stage = (lo_ + hi_) / 2
        r_ = 0.36 * float(np.linalg.norm(hi_ - lo_))
        rf = imp["stage1"]["proxy"].get("refloor") or {"foot_contact_z_u": 0.0, "rescale": 1.0}
        c_proxy = c_stage / rf["rescale"] + np.array([0.0, 0.0, rf["foot_contact_z_u"]])
        head_yaw = 0.0
        for tg in (imp.get("repose") or {}).get("targets") or []:
            names = [b["bone"] for b in tg["bones"]]
            if rigid[0]["bone"] in names:
                head_yaw = -tg["bones"][names.index(rigid[0]["bone"])]["yaw_deg"]
        fin_c = np.array(imp.get("focus_face_centre") or (c_stage - np.array(imp["centre_shift"])), float)
        cu = {"finished": {"face": {"c": fin_c.tolist(), "r": r_, "yaw": 0.0},
                           "face34": {"c": fin_c.tolist(), "r": r_, "yaw": 55.0, "elev": 8.0}},
              "ref": {"face": {"c": c_proxy.tolist(), "r": r_ / rf["rescale"], "yaw": head_yaw},
                      "face34": {"c": c_proxy.tolist(), "r": r_ / rf["rescale"], "yaw": head_yaw + 55.0, "elev": 8.0}}}
    jobs = [("finished", pp["rigged_blend"], "-")]
    if ref == "proxy":
        jobs.append(("ref", os.path.join(pp["ref"], proxy["glb"]), tfp))
        ref_title = ref_title or "PICKED: RAW PIXAL3D PROXY"
    elif ref:
        jobs.append(("ref", ref, "-"))
        ref_title = ref_title or "PICKED REFERENCE"
    else:
        jobs.append(("proxy", os.path.join(pp["ref"], proxy["glb"]), tfp))
    if hand and os.path.exists(hand):
        jobs.append(("hand", hand, "-"))
    procs = []
    for tag, src, tf in jobs:
        out = os.path.join(work, tag)
        cuf = "-"
        if tag in cu:
            cuf = os.path.join(work, "_cu_%s.json" % tag)
            json.dump(cu[tag], open(cuf, "w"))
        procs.append((tag, subprocess.Popen([BLENDER, "--background", "--factory-startup", "--python", scr, "--", src, tf,
                                             out, "%.4f" % H, cuf], stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)))
    for tag, p_ in procs:
        p_.wait()
    gp = os.path.join(pp["renders"], unit + "_finish_gate.json")
    gate = json.load(open(gp)) if os.path.exists(gp) else {}
    sys.path.insert(0, HERE)
    import sheet_proxy as SP
    sheet = Image.open(SP.sheet_path(intake)).convert("RGBA")
    T = 360
    cols = ["sheet"] + (["ref"] if ref else ["proxy"]) + ["finished"] + (["hand"] if hand and os.path.exists(hand) else [])
    rp_on = bool((cfg.get("repose") or {}).get("targets"))
    title = {"sheet": "SHEET", "proxy": "RAW PIXAL3D PROXY", "ref": ref_title,
             "finished": "NEW SHIPPABLE (%s)" % (("light finish" + (" + repose" if rp_on else "")) if light
                                                 else "proxy finish"),
             "hand": "CURRENT HAND BUILD"}
    gkey = {"proxy": "proxy_raw", "finished": "finished", "hand": "hand_build",
            "ref": "picked_ref" if (ref and ref != "proxy") else "proxy_raw"}
    rows = ["front", "side", "threequarter"] + (["face", "face34"] if cu else [])
    out = Image.new("RGB", (len(cols) * (T + 8) + 8, 40 + len(rows) * (T + 34)), (34, 34, 38))
    d = ImageDraw.Draw(out)
    for ci, col in enumerate(cols):
        x = 8 + ci * (T + 8)
        d.text((x, 8), title[col], fill=(235, 235, 235))
        if col in gkey and gkey[col] in gate:
            extra = ""
            if col == "finished" and "finished_vs_picked" in gate:
                extra = " | vs picked %.3f" % gate["finished_vs_picked"]["_mean_iou"]
            d.text((x, 22), "mean IoU vs sheet %.3f%s" % (gate[gkey[col]].get("_mean_iou", float("nan")), extra),
                   fill=(200, 200, 120))
        for ri, rv in enumerate(rows):
            y = 40 + ri * (T + 34)
            if col == "sheet":
                fv = intake["views"]["front"]
                if rv in ("face", "face34"):
                    fb = rigid[0]["box"]
                    im = sheet.crop((int(fv["axis_x_px"] + 100 * fb[0][0]), int(fv["floor_y_px"] - 100 * fb[1][2]),
                                     int(fv["axis_x_px"] + 100 * fb[1][0]), int(fv["floor_y_px"] - 100 * fb[0][2])))
                    lab = "front view, head crop"
                else:
                    vb = intake["views"].get(rv if rv != "threequarter" else "back", fv)["bbox_px"]
                    im = sheet.crop(tuple(vb))
                    lab = rv if rv != "threequarter" else "back (sheet has no 3/4)"
            else:
                p = os.path.join(work, "%s_%s.png" % (col, rv))
                im = Image.open(p).convert("RGBA") if os.path.exists(p) else Image.new("RGBA", (10, 10))
                lab = {"face": "face, head-on to its own face",
                       "face34": "face, 55 deg off its own face (mouth line)"}.get(rv, rv)
                g = (gate.get(gkey[col]) or {}).get(rv)
                if g:
                    lab += "  IoU %.3f  row %.0f%%" % (g["iou"], g["row_err_pct"])
                if col == "finished" and rv in (gate.get("finished_vs_picked") or {}):
                    lab += "  | vs picked %.3f" % gate["finished_vs_picked"][rv]["iou"]
            bb = im.getbbox() or (0, 0, im.width, im.height)
            im = im.crop(bb)
            k = min(T / im.width, T / im.height)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
            bg = Image.new("RGBA", (T, T), (34, 34, 38, 255))
            bg.alpha_composite(im, ((T - im.width) // 2, (T - im.height) if rv not in ("face", "face34") else (T - im.height) // 2))
            out.paste(bg.convert("RGB"), (x, y))
            d.text((x, y + T + 4), lab, fill=(150, 200, 255))
    dst = os.path.join(pp["renders"], unit + "_finish_panel.png")
    out.save(dst)
    print("PANEL", dst)
    return dst


# ======================================================================================================================
if __name__ == "__main__":
    a = sys.argv[1:]

    def val(flag, default=None):
        return a[a.index(flag) + 1] if flag in a else default
    cmd, unit = a[0], a[1]
    if cmd == "init":
        stage_init(unit, "--force" in a)
    elif cmd == "solid":
        stage_solid(unit, val("--out", os.path.join(os.environ.get("TEMP", "."), unit + "_solid_debug.npz")))
    elif cmd == "gate":
        stage_gate(unit, val("--hand"), val("--ref"))
    elif cmd == "panel":
        stage_panel(unit, val("--hand"), val("--ref"), val("--ref-title"), "--closeup" in a)
    else:
        raise SystemExit("unknown command %r" % cmd)
