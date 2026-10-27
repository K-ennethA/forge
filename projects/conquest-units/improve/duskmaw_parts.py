"""duskmaw closed procedural parts for the light finish (light.parts). Stage-1 frame, sheet units (x right, -y front,
z up from the floor).

v6 (2026-10-25, the full re-run from the clean closed v6 proxy 92da035a09ce): the open / closed torso MOUTH is built from
parts on top of a SHALLOW maw cavity carved into the closed proxy (light.cuts capsule), all generated OFFLINE in the
service venv by `python -P improve/duskmaw_parts.py gen <out.json>` (scipy Delaunay; deterministic) and FROZEN into the
config as vertex / face / region arrays. In Blender the build only calls `frozen` (pure numpy):

    frozen(args) -> V (n,3), F (index lists), R (region per face), info
      args: V, F, R (run-length [[region, count], ...]), eps + pivot (the part is built x eps about the pivot = its bone
            head: hidden inside the torso at rest, the clip keys scale the bone by 1 / eps), info

The generated parts (gen_v6):
    teeth_up / teeth_lo  the upper / lower tooth rows TRACED from the OPEN sheet's front view (design/reference/
                         duskmaw-mouth-open-sheet.webp, crop x 120..380 y 330..590): red (all warm) / orange / yellow
                         layers (per-tooth red -> orange -> yellow), each a closed slab conforming to the torso front
                         (the proxy's front-most surface as a height map), scaled into the v6 torso
    lid                  the CLOSED state: a body-tone PLUG that refills the carved cavity (its front = the proxy torso
                         surface, its back behind the cavity wall) + the closed sheet's TEAR traced (design/reference/
                         duskmaw-character-sheet.webp, rim red / core orange / hot yellow) on it
    lining               the cavity's dark-red interior (deep -> lighter toward the rim, by depth), a closed slab just
                         in front of the carved wall; static (the plug covers it when closed)
v5.3 eyes (decals) kept below for reference; v6 uses the proxy's own eyes.
"""
import math
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import proxy_finish_parts as PFP     # Blender side (sys.path has improve/)
except ImportError:                      # pragma: no cover
    PFP = None


# =====================================================================================================================
# Blender side: the frozen parts
# =====================================================================================================================
def frozen(args):
    V = np.asarray(args["V"], float)
    F = [list(map(int, f)) for f in args["F"]]
    R = []
    for reg, n in args["R"]:
        R += [reg] * int(n)
    assert len(R) == len(F), "frozen part: %d regions for %d faces" % (len(R), len(F))
    eps = float(args.get("eps", 1.0))
    piv = np.asarray(args.get("pivot", [0.0, 0.0, 0.0]), float)
    V = piv + eps * (V - piv)
    info = dict(args.get("info") or {})
    info.update({"eps": eps, "pivot": piv.tolist(), "faces": len(F),
                 "rule": "frozen closed mesh generated offline (duskmaw_parts.py gen), built x eps about the pivot"})
    return V, F, R, info


# =====================================================================================================================
# v5.3 eyes (decals on the face plane) - not used by v6 (the v6 proxy's own eyes read)
# =====================================================================================================================
def _plane_project(y0, gx, gz, cx, cz):
    def project(pts, n):
        pts = np.asarray(pts, float); d = -np.asarray(n, float)
        out = []
        for p in pts:
            f0 = p[1] - (y0 + gx * (p[0] - cx) + gz * (p[2] - cz))
            df = d[1] - (gx * d[0] + gz * d[2])
            s = -f0 / df if abs(df) > 1e-9 else 0.0
            out.append(p + s * d)
        return np.array(out)
    return project


def eyes(args):
    cx, cz = float(args["cx"]), float(args["cz"])
    y0, gx, gz = float(args["y0"]), float(args["gx"]), float(args["gz"])
    a, b = float(args["a"]), float(args["b"])
    ang = math.radians(float(args.get("slant_deg", 35.0)))
    region = args.get("region", "eye_decal")
    pieces = []
    for sgn in (1.0, -1.0):
        ex = sgn * cx
        gxs = sgn * gx
        n = np.array([gxs, -1.0, gz]); n /= np.linalg.norm(n)
        anchor = np.array([ex, y0, cz])
        long_ = np.array([sgn * math.cos(ang), 0.0, math.sin(ang)])
        up = np.cross(n, long_); up = up if up[2] > 0 else -up
        V, F, R = PFP.decal(anchor, n, a, b, _plane_project(y0, gxs, gz, ex, cz),
                            t_up=float(args.get("t_up", 0.03)), t_dn=float(args.get("t_dn", 0.06)),
                            dome=float(args.get("dome", 0.004)), segs=int(args.get("segs", 28)),
                            rings=int(args.get("rings", 3)), region=region, up=tuple(up))
        pieces.append((V, F, R))
    V, F, R = PFP.merge(pieces)
    info = {"eyes": 2, "a": a, "b": b, "region": region}
    return V, [list(map(int, f)) for f in F], R, info


# =====================================================================================================================
# OFFLINE generator (service venv: numpy + scipy + PIL). python -P improve/duskmaw_parts.py gen <out.json>
# =====================================================================================================================
ROOT = os.path.normpath(os.path.join(HERE, ".."))
OPEN_SHEET = os.path.join(ROOT, "design", "reference", "duskmaw-mouth-open-sheet.webp")
CLOSED_SHEET = os.path.join(ROOT, "design", "reference", "duskmaw-character-sheet.webp")

GEN = {
    # v6.1 (artist on v6: "the mouth is more close to the ref on that 2nd model" = v5.4's wide open maw): the torso is
    # widened through the maw height (light.adds before_cuts ellipsoid), the cavity carved at the open sheet's size and
    # the rows traced at FULL sheet scale (1.0x) about the sheet maw centre column cx_px; the sheet row top top_py lands
    # at top_z (the sheet interior centre py ~450 on the cavity centre z 4.05)
    "teeth": {"crop": [120, 330, 380, 590], "cx_px": 240.5, "top_py": 358, "top_z": 4.97, "sx": 1.0, "sz": 1.0,
              "orange": [200, 80], "yellow": [225, 150],
              "split_py": 452, "dp_px": 3.0, "spacing": 0.09,
              "layers": [["red", "teeth_red", -0.022], ["orange", "teeth_orange", -0.027], ["yellow", "teeth_yellow", -0.032]],
              "thick": 0.04},
    # the carved cavity: the union of these light.cuts capsules [p, q, r] (the same numbers go into the config cuts)
    "cavity": {"capsules": [[[-0.06, -0.755, 3.73], [-0.06, -0.755, 4.38], 0.34], [[0.06, -0.755, 3.73], [0.06, -0.755, 4.38], 0.34]]},
    # the closed state's tear (closed sheet front, crop x 165..330 y 400..670) at sheet scale on the plug
    "tear": {"crop": [165, 330, 400, 670], "axis_px": 246.0, "sx": 0.86, "sz": 1.0, "cz": 4.05, "dp_px": 2.4,
             "spacing": 0.07, "layers": [["rim", "tear_rim", -0.020], ["core", "tear_core", -0.025], ["hot", "tear_hot", -0.030]],
             "thick": 0.03},
    "plug": {"margin": 0.035, "front": -0.013, "behind_wall": 0.008, "min_thick": 0.015, "spacing": 0.12, "rays": 40},
    "lining": {"inset": 0.006, "front_of_wall": 0.009, "thick": 0.012, "spacing": 0.08, "rays": 48,
               "bands": [[0.0, "maw_rim"], [0.08, "maw_mid"], [0.19, "maw_deep"]]},
    "hm": {"x": [-1.2, 1.2], "z": [2.4, 5.3], "cell": 0.01, "torso_y_max": -0.25},
}


class GridHeightMap:
    """v6.1: the FRONT surface of the stage-1 solid itself (adds applied, no cuts: `proxy_finish.py solid` on the
    config without cuts / parts) on the same (x, z) grid as HeightMap: the first + -> - crossing along +y (the fine
    grids where they cover the point, else the coarse grid), returned + `shift` (0.006 = the remesh surface sits
    min_half_thickness x the fine step outside the proxy, so the HeightMap offsets keep their meaning)"""
    def __init__(self, npz, spec, shift=0.006):
        import json
        from scipy import ndimage
        Z = np.load(npz)
        meta = json.loads(bytes(Z["meta"]).decode())
        grids = [(Z["F"].astype(np.float32), Z["lo"].astype(float), float(Z["step"][0]))]
        for i_, fm in enumerate(meta.get("fine") or []):
            grids.append((Z["fine_%d" % i_].astype(np.float32), np.asarray(fm["lo"], float), float(fm["step"])))
        c = float(spec["cell"]); x0, x1 = spec["x"]; z0, z1 = spec["z"]
        nx = int(round((x1 - x0) / c)) + 1; nz = int(round((z1 - z0) / c)) + 1
        X, Zz = np.meshgrid(x0 + c * np.arange(nx), z0 + c * np.arange(nz), indexing="ij")
        ys = np.arange(-1.4, 0.4, 0.005)
        G = np.full((nx, nz), np.nan)
        prev = None
        for k, y in enumerate(ys):
            P = np.stack([X.ravel(), np.full(X.size, y), Zz.ravel()], 1)
            f = np.full(len(P), np.inf)
            for (F, lo, st) in grids:          # coarse first, fine overrides inside its box
                u = (P - lo) / st
                ins = np.all((u >= 0) & (u <= np.array(F.shape) - 1.001), 1)
                if ins.any():
                    f[ins] = ndimage.map_coordinates(F, u[ins].T, order=1)
            f = f.reshape(nx, nz)
            if prev is not None:
                hit = np.isnan(G) & (prev > 0) & (f <= 0)
                t = prev[hit] / np.maximum(prev[hit] - f[hit], 1e-12)
                G[hit] = ys[k - 1] + t * (ys[k] - ys[k - 1])
            prev = f
        valid = ~np.isnan(G) & (G < float(spec["torso_y_max"]))
        # the front-most surface within +-0.04 u (a min filter on y): conforming slabs clear small ridges (the pecs)
        Gf = ndimage.minimum_filter(np.where(valid, G, 9.0), size=9, mode="nearest")
        G = np.where(valid, np.minimum(G, Gf), G)
        d, (ii, jj) = ndimage.distance_transform_edt(~valid, return_indices=True)
        Gv = np.where(valid, G, np.nan)
        self.G = np.where(valid, Gv, Gv[ii, jj] + 0.6 * d * c) + shift
        self.valid = valid
        self.x0, self.z0, self.c, self.nx, self.nz = x0, z0, c, nx, nz

    def y(self, x, z):
        return HeightMap.y(self, x, z)

    def inside(self, x, z):
        return HeightMap.inside(self, x, z)


def _load_proxy_points():
    import sys, json
    sys.path.insert(0, HERE)
    import proxy_finish as PF
    intake = json.load(open(os.path.join(ROOT, "design", "reference", "duskmaw", "intake.json")))
    pr = intake["proxy"]
    Vgl, Fp, UV, TEXI, images = PF.read_proxy(os.path.join(ROOT, "design", "reference", "duskmaw", pr["glb"]))
    V = PF.to_sheet(Vgl, pr["transform"])
    P, FI, C = PF.sample_surface(V, Fp, UV, TEXI, {}, 0.008)
    return P, pr["sha256"]


class HeightMap:
    """the proxy torso's FRONT-most surface y(x, z) on a grid (cells without torso extrapolated: nearest valid + a 0.6
    slope back), bilinear lookup"""
    def __init__(self, P, spec):
        from scipy import ndimage
        c = float(spec["cell"]); x0, x1 = spec["x"]; z0, z1 = spec["z"]
        nx = int(round((x1 - x0) / c)) + 1; nz = int(round((z1 - z0) / c)) + 1
        s = (P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 2] >= z0) & (P[:, 2] <= z1)
        Q = P[s]
        ix = np.rint((Q[:, 0] - x0) / c).astype(int); iz = np.rint((Q[:, 2] - z0) / c).astype(int)
        G = np.full((nx, nz), np.inf)
        np.minimum.at(G, (ix, iz), Q[:, 1])
        valid = G < float(spec["torso_y_max"])
        Gm = np.where(valid, G, np.nan)
        # 3x3 median of the valid cells (sampling noise)
        pad = np.pad(Gm, 1, mode="edge")
        stack = np.stack([pad[1 + dx:1 + dx + nx, 1 + dz:1 + dz + nz] for dx in (-1, 0, 1) for dz in (-1, 0, 1)])
        med = np.nanmedian(stack, 0)
        Gm = np.where(valid, med, np.nan)
        d, (ii, jj) = ndimage.distance_transform_edt(~valid, return_indices=True)
        self.G = np.where(valid, Gm, Gm[ii, jj] + 0.6 * d * c)
        self.valid = valid
        self.x0, self.z0, self.c, self.nx, self.nz = x0, z0, c, nx, nz

    def y(self, x, z):
        x = np.asarray(x, float); z = np.asarray(z, float)
        fx = np.clip((x - self.x0) / self.c, 0, self.nx - 1.0001); fz = np.clip((z - self.z0) / self.c, 0, self.nz - 1.0001)
        i = np.floor(fx).astype(int); j = np.floor(fz).astype(int); u = fx - i; v = fz - j
        G = self.G
        return (G[i, j] * (1 - u) * (1 - v) + G[i + 1, j] * u * (1 - v) + G[i, j + 1] * (1 - u) * v + G[i + 1, j + 1] * u * v)

    def inside(self, x, z):
        x = np.asarray(x, float); z = np.asarray(z, float)
        i = np.clip(np.rint((x - self.x0) / self.c).astype(int), 0, self.nx - 1)
        j = np.clip(np.rint((z - self.z0) / self.c).astype(int), 0, self.nz - 1)
        return self.valid[i, j]


# ---- 2D polygon utilities ---------------------------------------------------------------------------------------
def _moore(mm):
    p = np.pad(mm, 1)
    ys, xs = np.nonzero(p)
    k = np.lexsort((xs, ys))[0]
    start = (ys[k], xs[k])
    nb = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    cur, back = start, 6
    out = [start]
    for _ in range(400000):
        found = False
        for i in range(8):
            dd = (back + 1 + i) % 8
            ny, nx = cur[0] + nb[dd][0], cur[1] + nb[dd][1]
            if p[ny, nx]:
                back = (dd + 4) % 8; cur = (ny, nx); found = True
                break
        if not found or cur == start:
            break
        out.append(cur)
    P = np.array(out, float) - 1.0
    return np.stack([P[:, 1], P[:, 0]], 1)          # (x px, y px)


def _dp(P, eps):
    if len(P) < 3:
        return P
    a, b = P[0], P[-1]; ab = b - a; L = np.linalg.norm(ab)
    d = np.abs(ab[0] * (P[:, 1] - a[1]) - ab[1] * (P[:, 0] - a[0])) / L if L > 1e-9 else np.linalg.norm(P - a, axis=1)
    i = int(np.argmax(d))
    if d[i] > eps:
        return np.vstack([_dp(P[:i + 1], eps)[:-1], _dp(P[i:], eps)])
    return np.vstack([a, b])


def _closed_dp(P, eps):
    i = int(np.argmax(np.linalg.norm(P - P[0], axis=1)))
    A = _dp(P[:i + 1], eps); Bq = _dp(np.vstack([P[i:], P[:1]]), eps)
    return np.vstack([A[:-1], Bq[:-1]])


def _area(P):
    return 0.5 * float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))


def _ccw(P):
    return P if _area(P) > 0 else P[::-1]


def _inside_poly(Q, P):
    """even-odd point in polygon, Q (m,2), P (n,2)"""
    x, y = Q[:, 0][:, None], Q[:, 1][:, None]
    x0, y0 = P[:, 0][None], P[:, 1][None]
    x1, y1 = np.roll(P[:, 0], -1)[None], np.roll(P[:, 1], -1)[None]
    cond = ((y0 > y) != (y1 > y))
    xin = x0 + (y - y0) * (x1 - x0) / np.where(np.abs(y1 - y0) < 1e-15, 1e-15, (y1 - y0))
    return (np.sum(cond & (x < xin), 1) % 2) == 1


def _seg_dist(Q, P):
    a = P; b = np.roll(P, -1, 0); ab = b - a
    t = np.clip(((Q[:, None, :] - a[None]) * ab[None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-15)[None], 0, 1)
    proj = a[None] + t[..., None] * ab[None]
    return np.linalg.norm(Q[:, None, :] - proj, axis=2).min(1)


def _resample(P, ds):
    out = []
    n = len(P)
    for i in range(n):
        a, b = P[i], P[(i + 1) % n]
        k = max(1, int(math.ceil(np.linalg.norm(b - a) / ds)))
        for t in range(k):
            out.append(a + (b - a) * t / k)
    return np.array(out)


def tri2d(polys, spacing):
    """triangulate polygons (list of (n,2) CCW loops in (x, z), even-odd: holes allowed) with interior points at
    `spacing` (Delaunay; triangles kept by centroid inside) -> pts (m,2), tris (k,3) CCW"""
    from scipy.spatial import Delaunay
    B = [_resample(_ccw(np.asarray(P, float)), spacing) for P in polys]
    allB = np.vstack(B)
    lo = allB.min(0); hi = allB.max(0)
    gx = np.arange(lo[0] + 0.5 * spacing, hi[0], spacing); gz = np.arange(lo[1] + 0.5 * spacing, hi[1], spacing * 0.866)
    G = []
    for k, z in enumerate(gz):
        xs = gx + (0.5 * spacing if k % 2 else 0.0)
        G.append(np.stack([xs, np.full_like(xs, z)], 1))
    G = np.vstack(G) if G else np.zeros((0, 2))

    def inside(Q):
        m = np.zeros(len(Q), bool)
        for P in polys:
            m ^= _inside_poly(Q, np.asarray(P, float))
        return m
    if len(G):
        G = G[inside(G)]
        if len(G):
            dmin = np.min([_seg_dist(G, np.asarray(P, float)) for P in polys], 0)
            G = G[dmin > 0.45 * spacing]
    pts = np.vstack([allB, G])
    pts = np.unique(np.round(pts, 7), axis=0)
    T = Delaunay(pts).simplices
    cen = pts[T].mean(1)
    T = T[inside(cen)]
    a, b, c = pts[T[:, 0]], pts[T[:, 1]], pts[T[:, 2]]
    cr = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    T = np.where((cr < 0)[:, None], T[:, [0, 2, 1]], T)
    T = T[np.abs(cr) > 1e-12]
    used = np.unique(T)
    remap = np.full(len(pts), -1); remap[used] = np.arange(len(used))
    return pts[used], remap[T]


def slab(pts, T, yfront, yback):
    """a CLOSED slab from a 2D triangulation (x, z): front sheet at y = yfront(x, z) (normal -y), back at yback, walls
    along the boundary edges -> V (2m,3), F (triangles)"""
    m = len(pts)
    yf = yfront(pts[:, 0], pts[:, 1]); yb = yback(pts[:, 0], pts[:, 1])
    yb = np.maximum(yb, yf + 1e-4)
    V = np.vstack([np.stack([pts[:, 0], yf, pts[:, 1]], 1), np.stack([pts[:, 0], yb, pts[:, 1]], 1)])
    F = [list(t) for t in T] + [[m + t[0], m + t[2], m + t[1]] for t in T]
    cnt = {}
    for t in T:
        for i in range(3):
            a, b = int(t[i]), int(t[(i + 1) % 3])
            cnt[(a, b)] = cnt.get((a, b), 0) + 1
    for (a, b), _n in cnt.items():
        if (b, a) not in cnt:            # boundary edge a -> b (interior on the left, CCW)
            F.append([a, m + a, m + b]); F.append([a, m + b, b])
    return V, F


def _mask_polys(mask, x0, y0, dp_px, min_area_px=60):
    """outer loops of every component (holes filled) of a pixel mask -> list of (n,2) polygons in sheet px"""
    from scipy import ndimage
    lab, n = ndimage.label(mask)
    out = []
    for k in range(1, n + 1):
        m = lab == k
        if m.sum() < min_area_px:
            continue
        m = ndimage.binary_fill_holes(m)
        P = _closed_dp(_moore(m), dp_px) + np.array([x0, y0])
        if len(P) >= 3 and abs(_area(P)) > 4:
            out.append(P)
    return out


def _sheet(path, crop):
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)
    x0, y0, x1, y1 = crop
    C = im[y0:y1, x0:x1]
    return C[..., 0], C[..., 1], C[..., 2]


def trace_teeth(spec):
    from scipy import ndimage
    x0, y0, x1, y1 = spec["crop"]
    R, G, B = _sheet(OPEN_SHEET, spec["crop"])
    warm = (R > 120) & (R - B > 55)
    warm = ndimage.binary_opening(ndimage.binary_closing(warm, iterations=1), iterations=1)
    lab, n = ndimage.label(warm)
    sz = ndimage.sum(warm, lab, range(1, n + 1))
    keep = [k + 1 for k in np.argsort(-sz)[:2]]               # the two rows (upper 166..315 x 358..445, lower 147..333)
    rows = {}
    for k in keep:
        m = lab == k
        ys, _ = np.nonzero(m)
        nm = "up" if (ys.mean() + y0) < spec["split_py"] else "lo"
        mo = m & (R > spec["orange"][0]) & (G > spec["orange"][1])
        my = m & (R > spec["yellow"][0]) & (G > spec["yellow"][1])
        rows[nm] = {"red": _mask_polys(m, x0, y0, spec["dp_px"], 200),
                    "orange": _mask_polys(ndimage.binary_opening(mo), x0, y0, spec["dp_px"], 12),
                    "yellow": _mask_polys(ndimage.binary_opening(my), x0, y0, spec["dp_px"], 8)}
    return rows


def trace_tear(spec):
    from scipy import ndimage
    x0, y0, x1, y1 = spec["crop"]
    R, G, B = _sheet(CLOSED_SHEET, spec["crop"])
    warm = (R > 120) & (R - B > 55)
    core = (R > 200) & (G > 95)
    hot = (R > 225) & (G > 175)

    def largest(mm):
        lab, n = ndimage.label(mm)
        if n == 0:
            return mm
        s = ndimage.sum(mm, lab, range(1, n + 1)); return lab == (1 + int(np.argmax(s)))
    rim = largest(ndimage.binary_fill_holes(ndimage.binary_closing(warm, iterations=1)))
    ys, _ = np.nonzero(rim)
    cy = 0.5 * (ys.min() + ys.max()) + y0
    masks = {"rim": rim, "core": largest(ndimage.binary_fill_holes(core & rim)), "hot": largest(ndimage.binary_fill_holes(hot & rim))}
    return {k: _mask_polys(m, x0, y0, spec["dp_px"], 10)[:1] for k, m in masks.items()}, cy


def gen_v6(out_json, grid_npz=None):
    import json, time
    t0 = time.time()
    P, sha = _load_proxy_points()
    hm = GridHeightMap(grid_npz, GEN["hm"]) if grid_npz else HeightMap(P, GEN["hm"])
    CAPS = [(np.asarray(p_, float), np.asarray(q_, float), float(r_)) for p_, q_, r_ in GEN["cavity"]["capsules"]]
    cp = np.mean([c_[0] for c_ in CAPS], 0); cq = np.mean([c_[1] for c_ in CAPS], 0)

    def sd_cap(x, y, z):
        Q = np.stack([np.broadcast_to(x, np.shape(y)), y, np.broadcast_to(z, np.shape(y))], -1).reshape(-1, 3)
        out = np.full(len(Q), np.inf)
        for a_, b_, r_ in CAPS:
            ba = b_ - a_; pa = Q - a_
            h = np.clip((pa @ ba) / float(ba @ ba), 0, 1)
            out = np.minimum(out, np.linalg.norm(pa - h[:, None] * ba, axis=1) - r_)
        return out.reshape(np.shape(y))

    def cap_back(x, z):
        """the cavity's carved wall (the deepest capsule back half) at (x, z); nan where no capsule reaches"""
        x = np.asarray(x, float); z = np.asarray(z, float)
        out = np.full(np.broadcast(x, z).shape, np.nan)
        for a_, b_, r_ in CAPS:
            dz = np.maximum(0.0, np.maximum(z - b_[2], a_[2] - z))
            r2 = r_ ** 2 - (x - a_[0]) ** 2 - dz ** 2
            yb = np.where(r2 > 0, a_[1] + np.sqrt(np.maximum(r2, 0)), np.nan)
            out = np.fmax(out, yb)
        return out

    def opening_poly(off, n=72):
        """star-shaped outline {sd_capsule(x, hm(x, z), z) < off} about the capsule centre (bisection along rays)"""
        zc = 0.5 * (cp[2] + cq[2])
        pts = []
        for k in range(n):
            th = 2 * math.pi * k / n
            d = np.array([math.cos(th), math.sin(th)])
            lo_, hi_ = 0.0, 2.0
            for _ in range(40):
                mid = 0.5 * (lo_ + hi_)
                x, z = mid * d[0], zc + mid * d[1]
                if sd_cap(np.array([x]), hm.y(np.array([x]), np.array([z])), np.array([z]))[0] < off:
                    lo_ = mid
                else:
                    hi_ = mid
            pts.append([lo_ * d[0], zc + lo_ * d[1]])
        return np.array(pts)

    parts = {}
    report = {"proxy_sha": sha[:12], "gen": GEN, "height_map": "stage-1 grid " + os.path.basename(grid_npz) if grid_npz else "proxy samples"}

    # ---- the lining (static): just in front of the carved wall, inset from the rim
    lg = GEN["lining"]
    poly_l = opening_poly(-lg["inset"], lg["rays"])
    pts, T = tri2d([poly_l], lg["spacing"])
    yf_l = lambda x, z: np.minimum(np.nan_to_num(cap_back(x, z), nan=9.0) - lg["front_of_wall"], hm.y(x, z) + 0.003)
    V, F = slab(pts, T, yf_l, lambda x, z: yf_l(x, z) + lg["thick"])
    Vf = V[np.asarray(F)].mean(1) if len(F) else V
    depth = cap_back(Vf[:, 0], Vf[:, 2]) - hm.y(Vf[:, 0], Vf[:, 2])          # how deep the wall sits at the face
    depth = np.nan_to_num(depth, nan=0.0)
    bands = sorted(lg["bands"], key=lambda b: b[0])
    reg = [max([b for b in bands if d_ >= b[0]] or [bands[0]], key=lambda b: b[0])[1] for d_ in depth]
    parts["lining"] = _pack(V, F, reg)
    report["lining"] = {"outline_x": [round(float(poly_l[:, 0].min()), 3), round(float(poly_l[:, 0].max()), 3)],
                        "outline_z": [round(float(poly_l[:, 1].min()), 3), round(float(poly_l[:, 1].max()), 3)],
                        "max_depth_u": round(float(depth.max()), 3), "tris": len(F),
                        "bands": {b[1]: int(sum(1 for r in reg if r == b[1])) for b in bands}}

    # ---- the plug (lid): refills the cavity; front = the torso surface, back behind the wall
    pg = GEN["plug"]
    poly_p = opening_poly(pg["margin"], pg["rays"])
    pts, T = tri2d([poly_p], pg["spacing"])
    yf_p = lambda x, z: hm.y(x, z) + pg["front"]
    yb_p = lambda x, z: np.maximum(np.nan_to_num(cap_back(x, z) + pg["behind_wall"], nan=-9.0), yf_p(x, z) + pg["min_thick"])
    Vp, Fp_ = slab(pts, T, yf_p, yb_p)
    lid_V = [Vp]; lid_F = [Fp_]; lid_R = ["tear_lid"] * len(Fp_)
    report["plug"] = {"outline_x": [round(float(poly_p[:, 0].min()), 3), round(float(poly_p[:, 0].max()), 3)],
                      "outline_z": [round(float(poly_p[:, 1].min()), 3), round(float(poly_p[:, 1].max()), 3)], "tris": len(Fp_)}
    # ---- the tear on the plug (closed sheet, traced)
    tg = GEN["tear"]
    tp, cy = trace_tear(tg)
    off = len(Vp)
    tear_rep = {}
    for key, region, dy in tg["layers"]:
        for Pp in tp[key]:
            U = np.stack([(Pp[:, 0] - tg["axis_px"]) * tg["sx"] / 100.0, tg["cz"] + (cy - Pp[:, 1]) * tg["sz"] / 100.0], 1)
            pts, T = tri2d([U], tg["spacing"])
            V, F = slab(pts, T, (lambda dy_: (lambda x, z: hm.y(x, z) + dy_))(dy), lambda x, z: hm.y(x, z) + 0.012)
            lid_V.append(V); lid_F.append([[i + off for i in f] for f in F]); lid_R += [region] * len(F); off += len(V)
            tear_rep[key] = {"x": [round(float(U[:, 0].min()), 3), round(float(U[:, 0].max()), 3)],
                             "z": [round(float(U[:, 1].min()), 3), round(float(U[:, 1].max()), 3)], "tris": len(F)}
    parts["lid"] = _pack(np.vstack(lid_V), [f for Fl in lid_F for f in Fl], lid_R)
    report["tear"] = tear_rep

    # ---- the teeth rows (open sheet, traced)
    th = GEN["teeth"]
    rows = trace_teeth(th)
    teeth_rep = {}
    for nm in ("up", "lo"):
        Vs, Fs, Rs, off = [], [], [], 0
        lay_rep = {}
        for key, region, dy in th["layers"]:
            ntri = 0
            for Pp in rows[nm][key]:
                U = np.stack([(Pp[:, 0] - th["cx_px"]) * th["sx"] / 100.0, th["top_z"] - (Pp[:, 1] - th["top_py"]) * th["sz"] / 100.0], 1)
                try:
                    pts, T = tri2d([U], th["spacing"] if key == "red" else 0.6 * th["spacing"])
                except Exception:
                    continue
                if len(T) == 0:
                    continue
                V, F = slab(pts, T, (lambda dy_: (lambda x, z: hm.y(x, z) + dy_))(dy),
                            (lambda dy_: (lambda x, z: hm.y(x, z) + dy_ + th["thick"]))(dy))
                Vs.append(V); Fs += [[i + off for i in f] for f in F]; Rs += [region] * len(F); off += len(V); ntri += len(F)
            lay_rep[key] = {"polys": len(rows[nm][key]), "tris": ntri}
        Vall = np.vstack(Vs)
        parts["teeth_" + nm] = _pack(Vall, Fs, Rs)
        teeth_rep[nm] = {"layers": lay_rep, "x": [round(float(Vall[:, 0].min()), 3), round(float(Vall[:, 0].max()), 3)],
                         "z": [round(float(Vall[:, 2].min()), 3), round(float(Vall[:, 2].max()), 3)], "tris": len(Fs)}
    report["teeth"] = teeth_rep
    report["seconds"] = round(time.time() - t0, 1)
    for k, v in parts.items():
        v["open_edges"] = _open_edges(v["F"])
    json.dump({"parts": parts, "report": report}, open(out_json, "w"))
    print(json.dumps(report, default=str)[:4000])
    print("OPEN_EDGES", {k: v["open_edges"] for k, v in parts.items()})


def _open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


def _pack(V, F, R):
    rl = []
    for r in R:
        if rl and rl[-1][0] == r:
            rl[-1][1] += 1
        else:
            rl.append([r, 1])
    return {"V": np.round(np.asarray(V, float), 5).tolist(), "F": [list(map(int, f)) for f in F], "R": rl}


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "gen":
        gen_v6(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)


# =====================================================================================================================
# v6.2 (2026-10-25): the open maw is the v5.4 open proxy's own maw GRAFTED (light.head_graft, maw_graft/maw_v54.glb);
# CLOSED = the graft squashed by shell-weighted maw bones under this LID: a body-tone shield shaped like the closed
# sheet's tear (narrow, long, tapered, lower-reaching) + the traced tear on it, conforming to the v6 torso front.
# python -P improve/duskmaw_parts.py gen62 <out.json>
# =====================================================================================================================
GEN62 = {
    "shield": {"sx": 1.0, "sz": 1.40, "cz": 3.45, "dilate_u": 0.08, "front": -0.05, "thick": 0.07, "spacing": 0.13,
               "rays": 40},
    "tear": {"crop": [165, 330, 400, 670], "axis_px": 246.0, "sx": 0.80, "sz": 1.0, "cz": 3.30, "dp_px": 2.4,
             "spacing": 0.07, "layers": [["rim", "tear_rim", -0.056], ["core", "tear_core", -0.061], ["hot", "tear_hot", -0.066]],
             "thick": 0.03},
    "hm": {"x": [-1.2, 1.2], "z": [1.8, 5.3], "cell": 0.01, "torso_y_max": -0.30},
}


def gen_lid62(out_json):
    import json, time
    from scipy import ndimage
    t0 = time.time()
    P, sha = _load_proxy_points()
    hm = HeightMap(P, GEN62["hm"])
    sg, tg = GEN62["shield"], GEN62["tear"]
    tp, cy = trace_tear(tg)
    rim = tp["rim"][0]
    # the shield: the closed sheet's tear rim, scaled (sx, sz) about its centre, placed at cz, dilated dilate_u
    U = np.stack([(rim[:, 0] - tg["axis_px"]) * sg["sx"] / 100.0, sg["cz"] + (cy - rim[:, 1]) * sg["sz"] / 100.0], 1)
    c = float(sg["dilate_u"]); x0, z0 = U.min(0) - 2 * c - 0.05; x1, z1 = U.max(0) + 2 * c + 0.05
    g = 0.005
    X, Zz = np.meshgrid(np.arange(x0, x1, g), np.arange(z0, z1, g), indexing="ij")
    ins = _inside_poly(np.stack([X.ravel(), Zz.ravel()], 1), U).reshape(X.shape)
    d = ndimage.distance_transform_edt(~ins) * g
    m = d <= c
    m = ndimage.binary_opening(m, iterations=6)
    lab, n = ndimage.label(m)
    m = lab == (1 + int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))))
    Pm = _closed_dp(_moore(m.T), 4.0)             # mask (x rows) -> px loop (col = x index, row = z index)
    poly = np.stack([x0 + g * Pm[:, 0], z0 + g * Pm[:, 1]], 1)
    pts, T = tri2d([poly], sg["spacing"])
    V, F = slab(pts, T, lambda x, z: hm.y(x, z) + sg["front"], lambda x, z: hm.y(x, z) + sg["front"] + sg["thick"])
    Vs, Fs, Rs = [V], [list(f) for f in F], ["tear_lid"] * len(F)
    off = len(V)
    rep = {"shield": {"x": [round(float(poly[:, 0].min()), 3), round(float(poly[:, 0].max()), 3)],
                      "z": [round(float(poly[:, 1].min()), 3), round(float(poly[:, 1].max()), 3)], "tris": len(F)}}
    for key, region, dy in tg["layers"]:
        for Pp in tp[key]:
            Ut = np.stack([(Pp[:, 0] - tg["axis_px"]) * tg["sx"] / 100.0, tg["cz"] + (cy - Pp[:, 1]) * tg["sz"] / 100.0], 1)
            pts, T = tri2d([Ut], tg["spacing"])
            V, F = slab(pts, T, (lambda dy_: (lambda x, z: hm.y(x, z) + dy_))(dy), lambda x, z: hm.y(x, z) + sg["front"] + 0.01)
            Vs.append(V); Fs += [[i + off for i in f] for f in F]; Rs += [region] * len(F); off += len(V)
            rep[key] = {"x": [round(float(Ut[:, 0].min()), 3), round(float(Ut[:, 0].max()), 3)],
                        "z": [round(float(Ut[:, 1].min()), 3), round(float(Ut[:, 1].max()), 3)], "tris": len(F)}
    lid = _pack(np.vstack(Vs), Fs, Rs)
    lid["open_edges"] = _open_edges(lid["F"])
    rep.update({"proxy_sha": sha[:12], "gen": GEN62, "seconds": round(time.time() - t0, 1), "open_edges": lid["open_edges"]})
    json.dump({"parts": {"lid": lid}, "report": rep}, open(out_json, "w"))
    print(json.dumps(rep, default=str)[:3000])


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "gen62":
        gen_lid62(sys.argv[2])
