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


def apply_add(F, lo, step, add):
    """smooth UNION of a capsule / sphere / ellipsoid into the SDF grid (light.adds): F = smin(F, d, k); never mirrored.
    "ellipsoid": [centre, [rx, ry, rz]] uses the usual bound k0 (k0 - 1) / k1 (exact on the axes; duskmaw v6 torso)"""
    k = float(add.get("k", 0.04))
    if "capsule" in add:
        a, b, r = add["capsule"]; pts = np.array([a, b], float); rad = float(r)
        fn = lambda P: sd_capsule(P, a, b, r)
    elif "sphere" in add:
        c, r = add["sphere"]; pts = np.array([c], float); rad = float(r)
        fn = lambda P: np.linalg.norm(P - np.asarray(c, float), axis=1) - r
    elif "ellipsoid" in add:
        c, rr = add["ellipsoid"]; c = np.asarray(c, float); rr = np.asarray(rr, float)
        pts = np.array([c - rr, c + rr]); rad = 0.0

        def fn(P):
            q = P - c
            k0 = np.linalg.norm(q / rr, axis=1); k1 = np.linalg.norm(q / (rr * rr), axis=1)
            return k0 * (k0 - 1.0) / np.maximum(k1, 1e-12)
    else:
        raise ValueError(add)
    g = rad + k + 3 * step
    i0 = np.clip(np.floor((pts.min(0) - g - lo) / step).astype(int), 0, np.array(F.shape) - 1)
    i1 = np.clip(np.ceil((pts.max(0) + g - lo) / step).astype(int) + 1, 0, np.array(F.shape))
    axes = [lo[d] + step * np.arange(i0[d], i1[d]) for d in range(3)]
    X, Y, Z = np.meshgrid(*axes, indexing="ij")
    d = fn(np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)).reshape(X.shape)
    sl = tuple(slice(a_, b_) for a_, b_ in zip(i0, i1))
    cur = F[sl]
    h = np.clip(0.5 + 0.5 * (d - cur) / k, 0.0, 1.0)
    F[sl] = d * (1 - h) + cur * h - k * h * (1 - h)
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
    mx = cfg.get("midline_x")
    if mx is not None:              # the body's mirror plane to x 0 (sheet_proxy centres the BBOX; a swung tail skews it)
        V[:, 0] -= float(mx)
        report["proxy"]["midline_x"] = {"shift_x_u": round(-float(mx), 4),
                                        "rule": "the body midline (paired paws / head, after refloor) moved to x 0, so the "
                                                "repose mirror and the mirror test use the body's own plane"}
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

# ======================================================================================================================
# light mode: targeted FACE paint (an explicit artist ask, 2026-10-12: an extra nose; the eyes off / not lined up)
# ======================================================================================================================
def _ellipse_alpha(X, Z, c, r, feather):
    """1 inside the ellipse (centre c, radii r), smoothstep to 0 over `feather` (units) outside"""
    d = np.sqrt(((X - c[0]) / r[0]) ** 2 + ((Z - c[1]) / r[1]) ** 2)
    t = np.clip((d - 1.0) * min(r) / max(feather, 1e-9), 0.0, 1.0)
    return 1.0 - t * t * (3 - 2 * t)


def _inpaint(img, hole):
    """fill `hole` (bool) from its surroundings: normalised Gaussian convolution at growing radii (diffusion-like)"""
    from scipy import ndimage
    out = img.astype(float).copy()
    known = ~hole
    todo = hole.copy()
    for sig in (2, 4, 8, 16, 32, 64):
        if not todo.any():
            break
        w = ndimage.gaussian_filter(known.astype(float), sig)
        num = np.stack([ndimage.gaussian_filter(out[..., k] * known, sig) for k in range(3)], -1)
        ok = todo & (w > 1e-3)
        out[ok] = num[ok] / w[ok, None]
        known = known | ok
        todo = todo & ~ok
    return out


def face_paint(V, F, UV, TEXI, tex, fp, report):
    """Paint edits made in FRONT-VIEW space (x right, z up, stage-1 frame) back into the proxy texture: every texel of a
    front-facing, front-visible triangle in the window takes the edited colour at its own front projection, weighted by
    the edit mask x a facing ramp. Edits, in order: `erase` ellipses (inpainted from the surrounding fur: a duplicate
    nose), then `eyes`: the `src` eye (an ellipse) copied to `dst` and MIRRORED about `mirror_x` to the other side (one
    eye, so the pair is level and mirror-matched), after both old eyes (`old`) are inpainted away. Geometry is never
    touched; the bake then carries the painted texture onto the low."""
    from scipy import ndimage
    x0, x1, z0, z1 = [float(v) for v in fp["window"]]
    ppu = float(fp.get("ppu", 400.0))
    ymax = float(fp.get("y_max", 0.0))
    W, H = int(round((x1 - x0) * ppu)), int(round((z1 - z0) * ppu))
    T = V[F]
    cen = T.mean(1)
    N = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    N /= np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]
    inwin = (cen[:, 0] > x0 - 0.05) & (cen[:, 0] < x1 + 0.05) & (cen[:, 2] > z0 - 0.05) & (cen[:, 2] < z1 + 0.05) & \
        (cen[:, 1] < ymax)
    # 1. z-buffer + colour of the front view: dense barycentric samples (finer than a pixel, no level cap) of every
    #    triangle in the window, two passes (depth, then the front-most colour), nearest-texel colour
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import _bary_grid
    sel_w = np.nonzero(inwin)[0]
    Tw = V[F[sel_w]]
    span_w = np.max(np.linalg.norm(Tw - np.roll(Tw, 1, axis=1), axis=2), 1)
    lev_w = np.clip(np.ceil(span_w * ppu * 1.6).astype(int), 1, 256)
    zb = np.full((H, W), np.inf)
    R = np.zeros((H, W, 3))

    def _groups():
        for L in np.unique(lev_w):
            ss = sel_w[lev_w == L]
            Bg = _bary_grid(int(L))
            for ch in np.array_split(ss, max(1, len(ss) * len(Bg) // 2000000)):
                yield ch, Bg
    for pass_ in (0, 1):
        for ch, Bg in _groups():
            pos = np.einsum("kj,fjd->fkd", Bg, V[F[ch]]).reshape(-1, 3)
            jx = np.floor((pos[:, 0] - x0) * ppu).astype(int); jz = np.floor((z1 - pos[:, 2]) * ppu).astype(int)
            ok = (jx >= 0) & (jx < W) & (jz >= 0) & (jz < H)
            if pass_ == 0:
                np.minimum.at(zb, (jz[ok], jx[ok]), pos[ok, 1])
                continue
            fr = ok.copy()
            fr[ok] = pos[ok, 1] <= zb[jz[ok], jx[ok]] + 1e-9
            if not fr.any():
                continue
            uvs = np.einsum("kj,fjd->fkd", Bg, UV[F[ch]]).reshape(-1, 2)[fr]
            ti_ = np.repeat(TEXI[ch], len(Bg))[fr]
            col = np.zeros((int(fr.sum()), 3))
            for t_, im in tex.items():
                m_ = ti_ == t_
                h_, w_ = im.shape[:2]
                u = np.clip((uvs[m_, 0] % 1.0) * (w_ - 1), 0, w_ - 1).round().astype(int)
                v = np.clip((uvs[m_, 1] % 1.0) * (h_ - 1), 0, h_ - 1).round().astype(int)
                col[m_] = im[v, u]
            R[jz[fr], jx[fr]] = col
    have = np.isfinite(zb)
    R = _inpaint(R, ~have)                      # only the background around the head
    zb_f = ndimage.grey_closing(np.where(have, zb, 1e9), size=3)
    X, Z = np.meshgrid(x0 + (np.arange(W) + 0.5) / ppu, z1 - (np.arange(H) + 0.5) / ppu)
    E = R.copy()
    M = np.zeros((H, W))
    rep = {"window": [x0, x1, z0, z1], "ppu": ppu, "ops": []}
    for er in fp.get("erase") or []:
        a = _ellipse_alpha(X, Z, er["c"], er["r"], float(er.get("feather", 0.03)))
        hole = a > 0.02
        E = E * (1 - a[..., None]) + _inpaint(E, hole) * a[..., None]
        M = np.maximum(M, a)
        rep["ops"].append({"erase": er["c"], "r": er["r"], "px": int(hole.sum()), "why": er.get("why")})
    ey = fp.get("eyes")
    if ey:
        r = ey["r"]
        fe = float(ey.get("feather", 0.04))
        hole = np.zeros((H, W), bool)
        aold = np.zeros((H, W))
        for c in ey["old"]:
            a = _ellipse_alpha(X, Z, c, [r[0] * 1.12, r[1] * 1.15], fe)
            aold = np.maximum(aold, a)
            hole |= a > 0.02
        clean = _inpaint(E, hole)
        src = np.array(ey["src"], float)
        mx = float(ey.get("mirror_x", 0.0))
        dst = np.array(ey["dst"], float)
        out = clean.copy()
        apaste = np.zeros((H, W))
        for sgn in (1, -1):                     # the copy, then its mirror about mirror_x
            d = dst if sgn == 1 else np.array([2 * mx - dst[0], dst[1]])
            sx = src[0] + sgn * (X - d[0])      # pull: each output pixel samples the source eye (x flipped for the mirror)
            sz = src[1] + (Z - d[1])
            u = (sx - x0) * ppu - 0.5
            v = (z1 - sz) * ppu - 0.5
            patch = np.stack([ndimage.map_coordinates(R[..., k], [v, u], order=1, mode="nearest") for k in range(3)], -1)
            a = _ellipse_alpha(X, Z, d, r, fe)
            out = out * (1 - a[..., None]) + patch * a[..., None]
            apaste = np.maximum(apaste, a)
        E = E * (1 - aold[..., None]) + clean * aold[..., None]
        E = E * (1 - apaste[..., None]) + out * apaste[..., None]
        M = np.maximum(M, np.maximum(aold, apaste))
        rep["ops"].append({"eyes": {"src": ey["src"], "dst": [dst.tolist(), [2 * mx - dst[0], dst[1]]], "old": ey["old"],
                                    "r": r, "raise_u": round(float(dst[1] - src[1]), 4)}, "why": ey.get("why")})
    # 2. texels of the window's front-facing triangles take E where M > 0. Dense barycentric samples (finer than a
    #    texel); each texel takes the edit at its sample NEAREST the texel centre (point sampling: the write is lossless,
    #    an identity edit leaves the texture as it was), so no speckle of old and new and no footprint blur
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import _bary_grid
    f0, f1 = [float(v) for v in fp.get("facing_ramp", [0.1, 0.3])]
    cand = np.nonzero(inwin & (-N[:, 1] > f0))[0]
    spacing = float(fp.get("sample_u", 0.0012))
    written = 0
    for t_, im in tex.items():
        h_, w_ = im.shape[:2]
        sel = cand[TEXI[cand] == t_]
        if not len(sel):
            continue
        Tq = V[F[sel]]
        span = np.max(np.linalg.norm(Tq - np.roll(Tq, 1, axis=1), axis=2), 1)
        level = np.clip(np.ceil(span / spacing).astype(int), 1, 64)
        best = np.full(h_ * w_, np.inf); bw = np.zeros(h_ * w_); bc = np.zeros((h_ * w_, 3))
        for L in np.unique(level):
            ss = sel[level == L]
            Bg = _bary_grid(int(L))
            for ch in np.array_split(ss, max(1, len(ss) * len(Bg) // 2000000)):
                pos = np.einsum("kj,fjd->fkd", Bg, V[F[ch]]).reshape(-1, 3)
                uvs = np.einsum("kj,fjd->fkd", Bg, UV[F[ch]]).reshape(-1, 2)
                fw = np.clip((-N[np.repeat(ch, len(Bg)), 1] - f0) / max(f1 - f0, 1e-9), 0, 1)
                jx = np.floor((pos[:, 0] - x0) * ppu).astype(int); jz = np.floor((z1 - pos[:, 2]) * ppu).astype(int)
                inside = (jx >= 0) & (jx < W) & (jz >= 0) & (jz < H)
                jx = np.clip(jx, 0, W - 1); jz = np.clip(jz, 0, H - 1)
                vis = pos[:, 1] <= zb_f[jz, jx] + float(fp.get("depth_tol", 0.02))
                wgt = np.where(inside & vis, M[jz, jx] * fw * fw * (3 - 2 * fw), 0.0)
                uf = (uvs[:, 0] % 1.0) * (w_ - 1); vf = (uvs[:, 1] % 1.0) * (h_ - 1)
                u = np.clip(uf, 0, w_ - 1).round().astype(int); v = np.clip(vf, 0, h_ - 1).round().astype(int)
                ti = v * w_ + u
                d2 = (uf - u) ** 2 + (vf - v) ** 2
                o = np.lexsort((d2, ti))                     # per texel: its sample nearest the texel centre
                first = np.ones(len(o), bool); first[1:] = ti[o][1:] != ti[o][:-1]
                k_ = o[first]
                better = d2[k_] < best[ti[k_]]
                k_ = k_[better]
                best[ti[k_]] = d2[k_]; bw[ti[k_]] = wgt[k_]; bc[ti[k_]] = E[jz[k_], jx[k_]]
        hit = bw > 1e-3
        flat = im.reshape(-1, 3)
        old = flat[hit].astype(float)
        wv = bw[hit][:, None]
        flat[hit] = np.clip(np.rint(old * (1 - wv) + bc[hit] * wv), 0, 255).astype(np.uint8)
        written += int(hit.sum())
    rep["texels_written"] = written
    rep["rule"] = ("edits in front-view space, written into the proxy texture on front-facing (-n_y > %.2f), front-visible "
                   "texels; geometry untouched" % f0)
    report["face_paint"] = rep
    return rep, (R, E, M)


def apply_pulls(V, pulls):
    """soft-selection grabs, in place: each pull moves the vertices by `move` x w, w = smoothstep(1 - d) over the
    ellipsoidal distance d = |(p - centre) / radius|, times an optional `y_ramp` [y0, y1] (0 at y0, 1 at y1: the stretch
    zone - behind y0 nothing moves, so the eyes / stop stay; between, a smooth stretch; beyond y1 a full move). Pulls
    sum; deterministic numpy. -> report"""
    rep = []
    D = np.zeros_like(V)
    for pl in pulls:
        c = np.asarray(pl["centre"], float); r = np.asarray(pl["radius"], float); mv = np.asarray(pl["move"], float)
        d = np.linalg.norm((V - c) / r, axis=1)
        t = np.clip(1.0 - d, 0.0, 1.0)
        w = t * t * (3 - 2 * t)
        if pl.get("y_ramp"):
            y0, y1 = [float(v) for v in pl["y_ramp"]]
            u = np.clip((V[:, 1] - y0) / (y1 - y0), 0.0, 1.0)
            w = w * u * u * (3 - 2 * u)
        D += w[:, None] * mv[None, :]
        rep.append({"centre": c.tolist(), "radius": r.tolist(), "move": mv.tolist(), "y_ramp": pl.get("y_ramp"),
                    "verts_moved": int((w > 1e-4).sum()), "max_move_u": round(float(w.max() * np.linalg.norm(mv)), 4),
                    "why": pl.get("why")})
    V += D
    return rep


def erase3d(V, F, UV, TEXI, tex, spheres, report):
    """opt-in light.erase3d (texture only): every texel whose surface point lies inside a sphere takes the median colour
    of the surface in the shell r..r * ring (the surrounding fur), blended by a smoothstep over the last `feather` of the
    radius; seen from any side (no projection: for marks on side-facing faces a front-view edit cannot reach)"""
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import _bary_grid
    cen = V[F].mean(1)
    rep = []
    for sp in spheres:
        c = np.asarray(sp["c"], float); r = float(sp["r"]); ring = float(sp.get("ring", 1.6))
        fe = float(sp.get("feather", 0.3)) * r
        sel = np.nonzero(np.linalg.norm(cen - c, axis=1) < r * ring + 0.05)[0]
        if not len(sel):
            rep.append({"c": c.tolist(), "r": r, "texels": 0}); continue
        for t_, im in tex.items():
            st = sel[TEXI[sel] == t_]
            if not len(st):
                continue
            h_, w_ = im.shape[:2]
            Bg = _bary_grid(48)
            pos = np.einsum("kj,fjd->fkd", Bg, V[F[st]]).reshape(-1, 3)
            uvs = np.einsum("kj,fjd->fkd", Bg, UV[F[st]]).reshape(-1, 2)
            u = np.clip((uvs[:, 0] % 1.0) * (w_ - 1), 0, w_ - 1).round().astype(int)
            v = np.clip((uvs[:, 1] % 1.0) * (h_ - 1), 0, h_ - 1).round().astype(int)
            dist = np.linalg.norm(pos - c, axis=1)
            shell = (dist > r) & (dist < r * ring)
            if shell.sum() < 10:
                continue
            fill = np.median(im[v[shell], u[shell]].astype(float), 0)
            inside = dist < r
            t = np.clip((r - dist[inside]) / max(fe, 1e-9), 0, 1)
            wv = t * t * (3 - 2 * t)
            ti = v[inside] * w_ + u[inside]
            best = {}
            for k, (tt, ww) in enumerate(zip(ti.tolist(), wv.tolist())):
                if ww > best.get(tt, -1.0):
                    best[tt] = ww
            idx = np.array(list(best.keys()), np.int64); wts = np.array(list(best.values()))[:, None]
            flat = im.reshape(-1, 3)
            flat[idx] = np.clip(np.rint(flat[idx].astype(float) * (1 - wts) + fill * wts), 0, 255).astype(np.uint8)
            rep.append({"c": c.tolist(), "r": r, "texels": int(len(idx)), "fill_rgb": np.round(fill).tolist(),
                        "why": sp.get("why")})
    report["erase3d"] = rep
    return rep


# ======================================================================================================================
# light mode: 3D-masked texture recolours + a hue clamp (opt-in; artist 2026-10-12, Vulture Creature: "the black wings
# and more skull like shape, but without the green aura"; head = bone skull helmet, glow = NONE)
# ======================================================================================================================
def lab_to_srgb(lab):
    """inverse of srgb_to_lab (D65): (..., 3) Lab -> sRGB 0..255 floats (clipped)"""
    lab = np.asarray(lab, float)
    fy = (lab[..., 0] + 16.0) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f > 0.206893, f ** 3, (f - 16.0 / 116.0) / 7.787) * np.array([0.95047, 1.0, 1.08883])
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    c = np.clip(xyz @ np.linalg.inv(M).T, 0.0, 1.0)
    c = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return np.clip(c * 255.0, 0.0, 255.0)


def texel_points(V, F, UV, TEXI, tex, spacing_px=0.7):
    """every texel a triangle covers -> its 3D surface point (dense barycentric samples, finer than a texel, at most
    `spacing_px` texels apart; each texel takes its sample NEAREST the texel centre, the same convention as
    face_paint). -> {texture key: (pos (h*w, 3) float32, covered (h*w,) bool)}"""
    sys.path.insert(0, REPO)
    from meshgen.tools.mesh_metrics import _bary_grid
    out = {}
    for t_, im in tex.items():
        h_, w_ = im.shape[:2]
        sel = np.nonzero(TEXI == t_)[0]
        pos = np.zeros((h_ * w_, 3), np.float32)
        best = np.full(h_ * w_, np.inf)
        if len(sel):
            Tuv = UV[F[sel]] * np.array([w_ - 1, h_ - 1], float)
            span = np.max(np.linalg.norm(Tuv - np.roll(Tuv, 1, axis=1), axis=2), 1)
            level = np.clip(np.ceil(span / spacing_px).astype(int), 1, 128)
            for L in np.unique(level):
                ss = sel[level == L]
                Bg = _bary_grid(int(L))
                for ch in np.array_split(ss, max(1, len(ss) * len(Bg) // 2000000)):
                    p3 = np.einsum("kj,fjd->fkd", Bg, V[F[ch]]).reshape(-1, 3)
                    uvs = np.einsum("kj,fjd->fkd", Bg, UV[F[ch]]).reshape(-1, 2)
                    uf = (uvs[:, 0] % 1.0) * (w_ - 1); vf = (uvs[:, 1] % 1.0) * (h_ - 1)
                    u = np.clip(uf, 0, w_ - 1).round().astype(int); v = np.clip(vf, 0, h_ - 1).round().astype(int)
                    ti = v * w_ + u
                    d2 = (uf - u) ** 2 + (vf - v) ** 2
                    o = np.lexsort((d2, ti))
                    first = np.ones(len(o), bool); first[1:] = ti[o][1:] != ti[o][:-1]
                    k_ = o[first]
                    better = d2[k_] < best[ti[k_]]
                    k_ = k_[better]
                    best[ti[k_]] = d2[k_]; pos[ti[k_]] = p3[k_]
        out[t_] = (pos, np.isfinite(best))
    return out


def _region_w(X, reg):
    """soft 3D region weight in [0, 1]: max over `boxes` [[lo, hi], ...] / `spheres` [[c, r], ...] (feather `feather` u,
    smoothstep), times (1 - the same for `exclude` boxes)"""
    fe = float(reg.get("feather", 0.03))

    def sstep(d):                       # d = signed distance (negative inside) -> 1 inside, 0 beyond feather
        t = np.clip(-d / max(fe, 1e-9) + 1.0, 0.0, 1.0) if fe > 0 else (d <= 0).astype(float)
        return t * t * (3 - 2 * t)
    w = np.zeros(len(X))
    for b in reg.get("boxes") or []:
        w = np.maximum(w, sstep(sd_box(X, np.asarray(b[0], float), np.asarray(b[1], float))))
    for c, r in reg.get("spheres") or []:
        w = np.maximum(w, sstep(np.linalg.norm(X - np.asarray(c, float), axis=1) - float(r)))
    for b in reg.get("exclude") or []:
        w = w * (1.0 - sstep(sd_box(X, np.asarray(b[0], float), np.asarray(b[1], float))))
    return w


def _lab_select_w(Lb, sel):
    """soft colour selection in CIELab: product over the given ranges {"L": [lo, hi], "a": [..], "b": [..]}, each edge
    a smoothstep `soft` Lab units wide"""
    s = float(sel.get("soft", 3.0))
    w = np.ones(len(Lb))
    for k, j in (("L", 0), ("a", 1), ("b", 2)):
        if k in sel:
            lo, hi = [float(v) for v in sel[k]]
            t0 = np.clip((Lb[:, j] - (lo - s)) / max(s, 1e-9), 0, 1); t1 = np.clip(((hi + s) - Lb[:, j]) / max(s, 1e-9), 0, 1)
            w = w * t0 * t0 * (3 - 2 * t0) * t1 * t1 * (3 - 2 * t1)
    return w


def recolour3d(V, F, UV, TEXI, tex, specs, report, tp=None):
    """opt-in light.recolour3d (texture only, geometry untouched): per spec, the texels whose 3D surface point lies in
    the spec's region (_region_w) AND whose colour matches `select` (_lab_select_w) are repainted to `target_rgb` with
    the proxy's own detail kept as a luminance ratio: new = target x clip((Y / Y_median)^detail_gamma, detail_clip),
    Y_median over the selected texels (weight > 0.5). Blend weight = region x select x `strength`. -> report rows"""
    tp = tp or texel_points(V, F, UV, TEXI, tex)
    rows = []
    for sp in specs:
        tgt = np.asarray(sp["target_rgb"], float)
        gam = float(sp.get("detail_gamma", 0.8)); dc = sp.get("detail_clip", [0.55, 1.35])
        row = {"name": sp.get("name"), "target_rgb": tgt.tolist(), "why": sp.get("why")}
        for t_, im in tex.items():
            pos, cov = tp[t_]
            idx = np.nonzero(cov)[0]
            wr = _region_w(pos[idx].astype(float), sp)
            idx, wr = idx[wr > 1e-3], wr[wr > 1e-3]
            if not len(idx):
                continue
            flat = im.reshape(-1, 3)
            src = flat[idx].astype(float)
            Lb = srgb_to_lab(src)
            w = wr * _lab_select_w(Lb, sp.get("select") or {}) * float(sp.get("strength", 1.0))
            keep = w > 1e-3
            idx, w, src = idx[keep], w[keep], src[keep]
            if not len(idx):
                continue
            Y = src @ np.array([0.2126, 0.7152, 0.0722])
            Ym = float(np.median(Y[w > 0.5])) if (w > 0.5).any() else float(np.median(Y))
            det = np.clip((np.maximum(Y, 1.0) / max(Ym, 1.0)) ** gam, float(dc[0]), float(dc[1]))
            new = np.clip(tgt[None, :] * det[:, None], 0, 255)
            flat[idx] = np.clip(np.rint(src * (1 - w[:, None]) + new * w[:, None]), 0, 255).astype(np.uint8)
            row.update({"texels_touched": int(len(idx)), "texels_ge_half": int((w > 0.5).sum()),
                        "median_src_rgb": np.round(np.median(src[w > 0.5], 0)).tolist() if (w > 0.5).any() else None,
                        "median_new_rgb": np.round(np.median(flat[idx[w > 0.5]].astype(float), 0)).tolist()
                        if (w > 0.5).any() else None})
        rows.append(row)
    report["recolour3d"] = rows
    return rows


def tint_clamp(V, F, UV, TEXI, tex, specs, report, tp=None):
    """opt-in light.tint_clamp (texture only): a HUE clamp in CIELab that keeps L* (the brightness) exactly: a* is raised
    to `a_min` (green -> neutral) and, when given, b* to `b_min` (blue -> neutral / warm), on every texel (no region) or
    on the texels whose 3D point lies in the spec's region (`boxes` / `spheres` / `exclude`, soft). Specs apply in
    order. Report: texels per texture with a* < -3 / -6 before and after."""
    rows = []
    for sp in specs:
        row = {"a_min": sp.get("a_min"), "b_min": sp.get("b_min"), "why": sp.get("why")}
        for t_, im in tex.items():
            flat = im.reshape(-1, 3)
            if sp.get("boxes") or sp.get("spheres"):
                tp = tp or texel_points(V, F, UV, TEXI, tex)
                pos, cov = tp[t_]
                idx = np.nonzero(cov)[0]
                wr = _region_w(pos[idx].astype(float), sp)
                idx, wr = idx[wr > 1e-3], wr[wr > 1e-3]
            else:
                idx = np.arange(len(flat)); wr = np.ones(len(flat))
            Lb = srgb_to_lab(flat[idx].astype(float))
            row["a_lt_-3_before"] = int((Lb[:, 1] < -3).sum()); row["a_lt_-6_before"] = int((Lb[:, 1] < -6).sum())
            L2 = Lb.copy()
            if sp.get("a_min") is not None:
                L2[:, 1] = np.maximum(L2[:, 1], float(sp["a_min"]))
            if sp.get("b_min") is not None:
                L2[:, 2] = np.maximum(L2[:, 2], float(sp["b_min"]))
            ch = np.any(np.abs(L2 - Lb) > 1e-9, 1)
            new = lab_to_srgb(L2[ch])
            src = flat[idx[ch]].astype(float)
            flat[idx[ch]] = np.clip(np.rint(src * (1 - wr[ch, None]) + new * wr[ch, None]), 0, 255).astype(np.uint8)
            La = srgb_to_lab(flat[idx].astype(float))
            row.update({"texels_region": int(len(idx)), "texels_changed": int(ch.sum()),
                        "a_lt_-3_after": int((La[:, 1] < -3).sum()), "a_lt_-6_after": int((La[:, 1] < -6).sum()),
                        "L_max_abs_change": round(float(np.abs(La[:, 0] - Lb[:, 0]).max()), 3) if len(idx) else 0.0})
        rows.append(row)
    report["tint_clamp"] = rows
    return rows


def floor_lift(V, spec):
    """opt-in light.floor_lift (geometry, before the remesh): a hem that hangs BELOW the refloored feet (a cloak-wing tip
    drawn past the floor line) is lifted onto the floor by a monotone vertical squash of the bottom `band` u, weighted
    by smooth ramps (`ramps`: [[axis, a, b], ...], axis 'x' / 'absx' / 'y' / 'z': 0 at a, 1 at b), so the feet (weight
    0) never move: z' = z + w * d * clip(1 - (z - zmin) / (band - zmin), 0, 1), d = -zmin (zmin = the lowest z with
    w > 0.5). The texture rides along (UVs unchanged). -> report"""
    w = np.ones(len(V))
    for ax, a, b in spec.get("ramps") or []:
        x = np.abs(V[:, 0]) if ax == "absx" else V[:, "xyz".index(ax)]
        t = np.clip((x - float(a)) / (float(b) - float(a)), 0.0, 1.0)
        w = w * t * t * (3 - 2 * t)
    band = float(spec.get("band", 0.4))
    hm = w > 0.5
    zmin = float(V[hm, 2].min()) if hm.any() else 0.0
    if zmin >= 0.0:
        return {"lifted": False, "zmin_u": round(zmin, 4)}
    d = -zmin
    t = np.clip(1.0 - (V[:, 2] - zmin) / (band - zmin), 0.0, 1.0)
    dz = w * d * t
    moved = dz > 1e-6
    w_lo = w[V[:, 2] < band]
    V[:, 2] += dz
    return {"lifted": True, "zmin_before_u": round(zmin, 4), "band_u": band, "verts_moved": int(moved.sum()),
            "max_lift_u": round(float(dz.max()), 4), "min_z_after_u": round(float(V[:, 2].min()), 5),
            "weight_below_band_max_outside_hem": round(float(w_lo[w_lo <= 0.5].max()) if (w_lo <= 0.5).any() else 0.0, 4),
            "ramps": spec.get("ramps"), "why": spec.get("why")}


def light_cuts(lc):
    """light.cuts + every light.parts entry's `replace_cuts` (a procedural part removes the proxy part it replaces);
    no parts = light.cuts unchanged"""
    return list(lc.get("cuts") or []) + [c for p_ in lc.get("parts") or [] for c in p_.get("replace_cuts") or []]


def build_fine(V, Fp, UV, TEXI, Fg, lo, step, lc, report):
    """opt-in light.fine (vampwarrior / duskmaw v5.4, artist 2026-10-25 "clumpy, like poorly sculpted", "destroyed on
    parts"; diagnosis: the stage-1 grid (0.025-0.027 u) is as coarse as the strands / eyelids / teeth, so the closing and
    the min_half_thickness offset fuse them): inside each box the SAME watertight rule as build_solid (shell nodes,
    closing `close_voxels`, exterior flood fill, exact near-band distance to the surface samples, the sign from the
    nearest sample's face normal, offset min_half_thickness x the FINE step) on a grid `div` (2 or 3) times finer.
    The box is snapped OUTWARD to coarse nodes [i0, i1] so every div-th fine node IS a coarse node. Exterior seed: any
    flood component holding a fine node that coincides with a coarse node the coarse solid calls clearly outside
    (> 1.5 coarse steps); the box cuts the body, so its border cannot seed the fill. The Blender half stitches each
    fine grid into the coarse remesh (proxy_finish_light.polygonise_fine). Absent key = no change.
    -> [{"name", "div", "i0", "i1", "lo", "step", "F"}] (+ report["solid"]["fine"])"""
    from scipy import ndimage
    from scipy.spatial import cKDTree
    n = np.array(Fg.shape)
    k = int(lc["close_voxels"]); band = int(lc["band_voxels"]); mht = float(lc["min_half_thickness"])
    cen = V[Fp].mean(1)
    tri = V[Fp]
    fnr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fnr /= np.maximum(np.linalg.norm(fnr, axis=1, keepdims=True), 1e-18)
    out, rows = [], []
    for bi_, b in enumerate(lc["fine"]):
        t0_ = time.time()
        div = int(b.get("div", 2))
        assert div in (2, 3), "light.fine div must be 2 or 3 (got %r)" % div
        blend = float(b.get("blend_voxels", 2.0))
        blo, bhi = np.asarray(b["box"][0], float), np.asarray(b["box"][1], float)
        i0 = np.clip(np.floor((blo - lo) / step).astype(int), 1, n - 2)
        i1 = np.clip(np.ceil((bhi - lo) / step).astype(int), 1, n - 2)
        assert np.all(i1 - i0 >= 2 * math.ceil(blend) + 2), "light.fine box %r is too small for its blend band" % b.get("name")
        for o_ in out:                   # boxes may not overlap (one fine grid per coarse cell)
            assert np.any((i1 <= o_["i0"]) | (i0 >= o_["i1"])), "light.fine boxes %r and %r overlap" % (b.get("name"), o_["name"])
        st = step / div
        flo = lo + step * i0
        fn = (i1 - i0) * div + 1
        fhi = flo + st * (fn - 1)
        pad = (band + 3 + k) * st + step
        selF = np.nonzero(np.all((cen >= flo - pad) & (cen <= fhi + pad), 1))[0]
        P, FI, _ = sample_surface(V, Fp[selF], UV, TEXI, {}, 0.5 * st)
        FI = selF[FI]                    # sample -> proxy face index
        ij = np.rint((P - flo) / st).astype(np.int64)
        inb = np.all((ij >= 0) & (ij < fn), 1)
        shell = np.zeros(fn, bool); shell[ij[inb, 0], ij[inb, 1], ij[inb, 2]] = True
        bl = ball(k) if k > 0 else None
        shell_d = ndimage.binary_dilation(shell, structure=bl) if k > 0 else shell
        lab, nl = ndimage.label(~shell_d)
        del shell_d
        cs = Fg[i0[0]:i1[0] + 1, i0[1]:i1[1] + 1, i0[2]:i1[2] + 1]
        ls = lab[::div, ::div, ::div]
        seed = np.unique(ls[(cs > 1.5 * step) & (ls > 0)])
        ext0 = np.isin(lab, seed)
        del lab
        ext = (ndimage.binary_dilation(ext0, structure=bl) & ~shell) if k > 0 else ext0
        del ext0
        near_ext = ndimage.binary_dilation(ext, iterations=1)
        bandm = ndimage.binary_dilation(shell, iterations=band)
        tree = cKDTree(P)
        bix = np.nonzero(bandm)
        del bandm
        Q = flo + st * np.stack(bix, 1)
        dist, nn = tree.query(Q, workers=-1)
        sgn = np.einsum("ij,ij->i", Q - P[nn], fnr[FI[nn]]) >= 0
        is_ext = ext[bix]; is_sh = (shell & near_ext)[bix]
        t = mht * st
        val = np.where(is_ext, dist - t, np.where(is_sh, np.where(sgn, dist, -dist) - t, -(dist + t)))
        Ff = np.where(ext, band * st, -band * st).astype(np.float64)
        Ff[bix] = np.clip(val, -band * st, band * st)
        del Q, dist, nn, sgn, is_ext, is_sh, val, ext, near_ext, tree
        if float(lc["smooth_sigma"]) > 0:
            Ff = ndimage.gaussian_filter(Ff, float(lc["smooth_sigma"]))
        out.append({"name": b.get("name", "fine%d" % bi_), "div": div, "i0": i0, "i1": i1, "lo": flo, "step": st, "F": Ff})
        rows.append({"name": out[-1]["name"], "div": div, "step_u": round(st, 6), "grid": fn.tolist(),
                     "nodes": int(np.prod(fn)), "box_snapped": [flo.round(5).tolist(), fhi.round(5).tolist()],
                     "samples": int(len(P)), "shell_nodes": int(shell.sum()), "seed_components": int(len(seed)),
                     "components": int(nl), "min_half_thickness_u": round(t, 5), "blend_voxels": blend,
                     "seconds": round(time.time() - t0_, 1)})
    report["solid"]["fine"] = rows
    return out


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
    if lc.get("pulls"):               # opt-in, an explicit artist ask: a smooth LOCAL reshape of the proxy (a muzzle drawn
        V = V.copy()                  # longer on the sheet); geometry + texture move together (the UVs ride along)
        report["proxy"]["pulls"] = apply_pulls(V, lc["pulls"])
    if lc.get("floor_lift"):          # opt-in: a hem drawn below the floor line lifted onto it (the feet never move)
        V = V.copy()
        report["proxy"]["floor_lift"] = floor_lift(V, lc["floor_lift"])
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
    FINE = build_fine(V, Fp, UV, TEXI, Fg, lo, step, lc, report) if lc.get("fine") else []   # opt-in light.fine
    report["solid"]["pieces"] = occupancy_pieces(Fg < 0)
    report["solid"]["rule"] = ("LIGHT: watertight remesh only (no smoothing / straighten / symmetrise / cuts); the surface "
                               "sits min_half_thickness x step = %.4f u outside the proxy" % (lc["min_half_thickness"] * step))
    adds_pre = [c for c in lc.get("adds") or [] if c.get("before_cuts")]    # opt-in per add (duskmaw v6: the torso
    adds_post = [c for c in lc.get("adds") or [] if not c.get("before_cuts")]   # widened, THEN the maw carved into it)
    if adds_pre:
        report["solid"]["light_adds_pre"] = [{"shape": [k for k in ("capsule", "sphere", "ellipsoid") if k in c][0],
                                              "nodes_inside": apply_add(Fg, lo, step, c), "why": c.get("why")}
                                             for c in adds_pre]
        for f_ in FINE:
            for c in adds_pre:
                apply_add(f_["F"], f_["lo"], f_["step"], c)
    if light_cuts(lc):                # opt-in: a duplicate PART Pixal3D invented (a forked tail), removed, never mirrored
        report["solid"]["light_cuts"] = [{"shape": [k for k in ("capsule", "sphere", "box") if k in c][0],
                                          "nodes_inside": apply_cut(Fg, lo, step, c, mirror=False), "why": c.get("why")}
                                         for c in light_cuts(lc)]
        for f_ in FINE:                 # light.fine: the same cuts in every fine grid
            for c in light_cuts(lc):
                apply_cut(f_["F"], f_["lo"], f_["step"], c, mirror=False)
    if adds_post:                     # opt-in, after the cuts: a smooth UNION that re-joins what a cut left disjoint (a
        report["solid"]["light_adds"] = [{"shape": [k for k in ("capsule", "sphere", "ellipsoid") if k in c][0],   # kept
                                          "nodes_inside": apply_add(Fg, lo, step, c), "why": c.get("why")}   # limb's stalk)
                                         for c in adds_post]
    if adds_post and FINE:            # light.fine: the same adds in every fine grid
        for f_ in FINE:
            for c in adds_post:
                apply_add(f_["F"], f_["lo"], f_["step"], c)
    if lc.get("bake_drop_interior"):            # opt-in: proxy faces buried inside the watertight solid (inner shells the
        thr = float(lc["bake_drop_interior"])   # flood fill sealed) never show; dropped from the bake source so the bake
        T_ = V[Fp]                              # cage (3 x p99 proxy-to-low) stays tight and rays hit the outer skin
        cen_ = T_.mean(1)
        n_ = np.cross(T_[:, 1] - T_[:, 0], T_[:, 2] - T_[:, 0])
        n_ /= np.maximum(np.linalg.norm(n_, axis=1), 1e-12)[:, None]

        def _f(P_):
            ij_ = np.clip(np.rint((P_ - lo) / step).astype(np.int64), 0, np.array(Fg.shape) - 1)
            return Fg[ij_[:, 0], ij_[:, 1], ij_[:, 2]]
        deep = (_f(cen_ + thr * n_) < 0) & (_f(cen_ - thr * n_) < 0)    # solid on BOTH sides: an inner shell
        Fp, TEXI = Fp[~deep], TEXI[~deep]
        used = np.unique(Fp)
        remap = np.full(len(V), -1, np.int64); remap[used] = np.arange(len(used))
        V, UV, Fp = V[used], UV[used], remap[Fp]
        report["solid"]["bake_interior_tris_dropped"] = int(deep.sum())
    if lc["floor_clip"]:
        z = lo[2] + step * np.arange(Fg.shape[2])
        Fg = np.maximum(Fg, (0.0 - z)[None, None, :])
        for f_ in FINE:
            zf = f_["lo"][2] + f_["step"] * np.arange(f_["F"].shape[2])
            f_["F"] = np.maximum(f_["F"], (0.0 - zf)[None, None, :])
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
    if lc.get("face_paint") and tex:    # opt-in, an explicit artist ask: a targeted face repaint (texture only)
        _fr, (R_, E_, M_) = face_paint(V, Fp, UV, TEXI, tex, lc["face_paint"], report)
        dbg = os.environ.get("PF_FACE_DEBUG")
        if dbg:
            os.makedirs(dbg, exist_ok=True)
            Image.fromarray(np.clip(R_, 0, 255).astype(np.uint8)).save(os.path.join(dbg, "face_front_before.png"))
            Image.fromarray(np.clip(E_, 0, 255).astype(np.uint8)).save(os.path.join(dbg, "face_front_edited.png"))
            Image.fromarray((255 * M_).astype(np.uint8)).save(os.path.join(dbg, "face_front_mask.png"))
        if not quiet:
            print("FACE_PAINT", json.dumps({k: _fr[k] for k in ("ops", "texels_written")}))
    if lc.get("erase3d") and tex:       # opt-in: marks on side-facing faces, refilled from the surrounding surface
        erase3d(V, Fp, UV, TEXI, tex, lc["erase3d"], report)
    if (lc.get("recolour3d") or lc.get("tint_clamp")) and tex:   # opt-in: 3D-masked recolours, then the hue clamp
        tp_ = texel_points(V, Fp, UV, TEXI, tex)
        rc_all = lc.get("recolour3d") or []
        rc_pre = [s_ for s_ in rc_all if not s_.get("after_clamp")]
        rc_post = [s_ for s_ in rc_all if s_.get("after_clamp")]   # opt-in per spec: a deliberate hue (a green glow
        rows_ = []                                                  # core) painted AFTER the hue clamp, so it survives
        if rc_pre:
            rows_ += recolour3d(V, Fp, UV, TEXI, tex, rc_pre, report, tp_)
        if lc.get("tint_clamp"):
            tint_clamp(V, Fp, UV, TEXI, tex, lc["tint_clamp"], report, tp_)
        if rc_post:
            rows_ += [dict(r_, after_clamp=True) for r_ in recolour3d(V, Fp, UV, TEXI, tex, rc_post, report, tp_)]
        if rc_all:
            report["recolour3d"] = rows_
        if not quiet:
            print("RECOLOUR3D", json.dumps(report.get("recolour3d")))
            print("TINT_CLAMP", json.dumps(report.get("tint_clamp")))
    meta ={"report": report, "regs": regs, "resolved": rcfg, "cfg": cfg, "facing": facing, "mode": "light"}
    if FINE:                          # light.fine: the fine grids ride in the npz (fine_<i>), their frame in meta
        meta["fine"] = [{"name": f_["name"], "div": f_["div"], "i0": f_["i0"].tolist(), "i1": f_["i1"].tolist(),
                         "lo": f_["lo"].tolist(), "step": f_["step"], "spec": lc["fine"][i_]} for i_, f_ in enumerate(FINE)]
    Fg = Fg.astype(np.float32)
    report["grid_sha"] = sha_arr(Fg[::2, ::2, ::2])
    report["proxy_sha"] = sha_arr(V)
    arrays = {"F": Fg, "lo": lo, "step": np.array([step]), "prox_V": V.astype(np.float64), "prox_F": Fp.astype(np.int64),
              "prox_UV": UV.astype(np.float64), "prox_texi": TEXI.astype(np.int64),
              "meta": np.frombuffer(json.dumps(meta).encode(), np.uint8)}
    for k_, t_ in tex.items():
        arrays["tex_%d" % int(k_)] = t_
    for i_, f_ in enumerate(FINE):
        arrays["fine_%d" % i_] = f_["F"].astype(np.float32)
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
        c_proxy[0] += float(cfg.get("midline_x") or 0.0) / rf["rescale"]     # the stage frame's midline shift, undone
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
