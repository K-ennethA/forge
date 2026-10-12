"""Gemwing Dragon parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Copied from crystal_canine_parts.py / chest_monster_parts.py (copied, not imported) + the unit's own generators. Every
generator returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every piece is a CLOSED
solid (0 open edges):

    polar_decal   a closed DECAL conforming to the shell whose outline is any star-shaped 2D curve (the reptilian IRIS
                  almond, the SLIT PUPIL): a polar grid projected onto the shell along the anchor normal, the top skin
                  domed, the bottom skin sunk under it, a side wall joining the rims
    lens_curve    the pointed almond / slit outline (x = a cos t, y = b sin t |sin t|^p)
    gem_blade     faceted hexagonal crystal prism (flattened across its fan plane) with an off-centre pyramid tip and a
                  buried base pole, in length BANDS (the prismatic gradient root -> tip) and optional streak columns
                  (the white streak cell on the face(s) toward the viewer): one WING blade / CROWN / NECK / BACK / TAIL /
                  LEG spike. tone_of(face normal, band) -> region per facet
    claw          a curved, faceted (diamond-section) crystal CLAW tapering to a point, its base buried in the paw
    gem_inlay     a CHEST ARMOUR plate: a closed faceted gem slab conformed to the shell (raised centre, buried back)
    spine_param   nearest-polyline arc parameter (chain weights)
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


class Part:
    def __init__(self, name, V, F, R, bone=None):
        self.name = name
        self.V = np.asarray(V, float)
        self.F = [list(map(int, f)) for f in F]
        self.R = list(R)
        self.bone = bone
        self.kind = None
        assert len(self.F) == len(self.R), name

    def tris(self):
        return int(sum(len(f) - 2 for f in self.F))


def open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


def face_normal(V, f):
    q = V[f]
    n = np.zeros(3)
    for i in range(1, len(f) - 1):
        n = n + np.cross(q[i] - q[0], q[i + 1] - q[0])
    return n


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    """-> (a, u, v): a = axis, u = the up_hint made perpendicular, v = a x u."""
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


def spine_param(P, pts):
    """-> (t in [0,1] by arc length at the nearest polyline point, arc length s, distance)."""
    pts = np.asarray(pts, float)
    seg = np.diff(pts, axis=0)
    L = np.linalg.norm(seg, axis=1)
    acc = np.concatenate([[0.0], np.cumsum(L)])
    best_d = np.full(len(P), 1e18); best_s = np.zeros(len(P))
    for i in range(len(seg)):
        w = P - pts[i]
        u = np.clip((w @ seg[i]) / max(float(seg[i] @ seg[i]), 1e-18), 0.0, 1.0)
        d = np.linalg.norm(w - np.outer(u, seg[i]), axis=1)
        m = d < best_d
        best_d[m] = d[m]; best_s[m] = acc[i] + u[m] * L[i]
    return best_s / acc[-1], best_s, best_d


# --------------------------------------------------------------------------- conforming decals (iris, slit pupil)
def lens_curve(a, b, segs, p=1.0):
    """pointed almond outline: x = a cos t, y = b sign(sin t) |sin t|^(1 + p) -> (segs, 2). p = 0 is an ellipse."""
    t = 2 * math.pi * np.arange(segs) / segs
    s = np.sin(t)
    return np.stack([a * np.cos(t), b * np.sign(s) * np.abs(s) ** (1.0 + p)], 1)


def rotate2(O, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    O = np.asarray(O, float)
    return np.stack([c * O[:, 0] - s * O[:, 1], s * O[:, 0] + c * O[:, 1]], 1)


def polar_decal(anchor, normal, outline, project, t_up, t_dn, dome, rings, region, up_hint=(0.0, 0.0, 1.0)):
    """Closed decal on the shell whose outline is a star-shaped 2D curve about the anchor (outline (segs, 2) in the
    decal frame: x along e2 = e1 x n, y along e1 = up_hint made perpendicular to n). project(pts, n) -> surface points
    hit by rays cast along -n through pts (the low shell). Top skin = hit + n (t_up + dome (1 - rho^2)); bottom skin =
    hit - n t_dn; a side wall joins the rims."""
    n = unit(normal)
    e1 = np.asarray(up_hint, float) - n * float(np.dot(up_hint, n))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n, [1.0, 0.0, 0.0])
    e1 = unit(e1); e2 = np.cross(e1, n)
    a = np.asarray(anchor, float)
    O = np.asarray(outline, float)
    segs = len(O)
    pts, rho = [a.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            pts.append(a + r_ * (O[m, 0] * e2 + O[m, 1] * e1)); rho.append(r_)
    pts = np.array(pts); rho = np.array(rho)
    hit = project(pts, n)
    top = hit + n[None] * (t_up + dome * (1.0 - rho ** 2))[:, None]
    bot = hit - n[None] * t_dn
    nt = len(pts)
    V = np.vstack([top, bot])
    F, ref = [], []

    def ring_idx(k):
        return [1 + (k - 1) * segs + m for m in range(segs)]
    for off, sgn in ((0, 1.0), (nt, -1.0)):             # top (+n) and bottom (-n) skins
        r1 = ring_idx(1)
        for m in range(segs):
            F.append([off, off + r1[m], off + r1[(m + 1) % segs]]); ref.append(sgn * n)
        for k in range(1, rings):
            A, B = ring_idx(k), ring_idx(k + 1)
            for m in range(segs):
                F.append([off + A[m], off + B[m], off + B[(m + 1) % segs], off + A[(m + 1) % segs]]); ref.append(sgn * n)
    rim = ring_idx(rings)
    for m in range(segs):                                # side wall
        m1 = (m + 1) % segs
        F.append([rim[m], rim[m1], nt + rim[m1], nt + rim[m]])
        ref.append(unit(0.5 * (pts[rim[m]] + pts[rim[m1]]) - a))
    Fo = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, [region] * len(Fo)


# --------------------------------------------------------------------------- crystal blades / spikes
HEX_COLS = (0.0, 0.40, 0.60)         # per hex face: the column split (the middle column carries the streak cell)


def gem_blade(base, tip, radius, squash, tip_frac, bury, spin_deg, tip_off, bulge, fan_n, tone_of, bands=(0.35, 0.70),
              streak_faces=(), streak_band=1):
    """Hexagonal crystal prism from base (its base pole buried 'bury' behind base) to the apex 'tip': the straight body to
    (1 - tip_frac) x length, split in length BANDS at the fractions 'bands' of the body (the root -> tip prismatic
    gradient), a slight swell at the first band line ('bulge'), then an off-centre pyramid tip. The section is flattened by
    'squash' along fan_n (the blade lies in its fan plane: broad face-on, never an edge-on pencil).
    tone_of(face_normal, band) -> region per facet (band 0 = the root band ... len(bands) = the body's last band,
    len(bands) + 1 = the pyramid tip). streak_faces: hex-face indices whose MIDDLE column carries 'gem_streak' in the
    band 'streak_band' (wing blades only; the other blades skip the columns: fewer tris). Closed. -> V, F, R"""
    base = np.asarray(base, float); tip = np.asarray(tip, float)
    a, u, v = frame_from_axis(tip - base, fan_n)        # u ~ the fan normal (the thin direction), v across the fan
    L = float(np.linalg.norm(tip - base))
    sp = math.radians(spin_deg)
    corners = [math.cos(sp + k * math.pi / 3) * squash * u + math.sin(sp + k * math.pi / 3) * v for k in range(6)]
    sec, sec_face, sec_col = [], [], []                 # streak faces get the 3-column split, the rest one column
    for k in range(6):
        c0, c1 = corners[k], corners[(k + 1) % 6]
        for ci, f in enumerate(HEX_COLS if k in streak_faces else (0.0,)):
            sec.append(c0 * (1 - f) + c1 * f); sec_face.append(k); sec_col.append(ci)
    ns = len(sec)
    Lb = L * (1.0 - tip_frac)
    levels = [(-bury, 0.80)] + [(b * Lb, bulge if i == 0 else 1.0 + (bulge - 1.0) * 0.5) for i, b in enumerate(bands)] + [(Lb, 0.92)]
    V = []
    for s, sc in levels:
        for p in sec:
            V.append(base + a * s + radius * sc * p)
    pole_b = len(V); V.append(base - a * (bury + 0.3 * radius))
    apex = len(V); V.append(tip + radius * tip_off * v)
    face_n = [unit(corners[k] + corners[(k + 1) % 6]) for k in range(6)]
    F, R = [], []
    for li in range(len(levels) - 1):
        for i in range(ns):
            i1 = (i + 1) % ns
            k = sec_face[i]
            reg = tone_of(face_n[k], li)
            if li == streak_band and sec_col[i] == 1 and k in streak_faces:
                reg = "gem_streak"
            F.append([li * ns + i, li * ns + i1, (li + 1) * ns + i1, (li + 1) * ns + i]); R.append(reg)
    top = (len(levels) - 1) * ns
    for i in range(ns):
        i1 = (i + 1) % ns
        k = sec_face[i]
        F.append([pole_b, i1, i]); R.append(tone_of(face_n[k], 0))
        F.append([top + i, top + i1, apex]); R.append(tone_of(face_n[k], len(levels) - 1))
    V = np.array(V)
    F = orient_outward(V, F, base + a * (0.5 * Lb))
    return V, F, R


def facing_faces(base, tip, spin_deg, fan_n, view, n):
    """indices of the n hex faces of gem_blade(base, tip, ..., spin_deg, ..., fan_n) whose normals face 'view' most"""
    a, u, v = frame_from_axis(np.asarray(tip, float) - np.asarray(base, float), fan_n)
    sp = math.radians(spin_deg)
    fn = [unit(math.cos(sp + (k + 0.5) * math.pi / 3) * u + math.sin(sp + (k + 0.5) * math.pi / 3) * v) for k in range(6)]
    order = np.argsort([-float(np.dot(f_, unit(view))) for f_ in fn], kind="stable")
    return set(int(k) for k in order[:n])


# --------------------------------------------------------------------------- claws
def claw(base, ctrl, tip, w0, h0, n_sec, tone_of, side_hint=(1.0, 0.0, 0.0)):
    """Curved faceted claw: diamond sections (half width w, half height h, tapering linearly to 0 at the tip) along the
    quadratic bezier base -> ctrl -> tip; a base pole closes the buried end, the tip is an apex. side_hint fixes the
    section's width direction (the claw's flat sides face +-side_hint). tone_of(face normal) -> region. Closed."""
    base, ctrl, tip = (np.asarray(p, float) for p in (base, ctrl, tip))
    ts = np.linspace(0.0, 1.0, n_sec + 1)[:-1]
    V = []
    for t in ts:
        p = (1 - t) ** 2 * base + 2 * (1 - t) * t * ctrl + t * t * tip
        d = unit(2 * (1 - t) * (ctrl - base) + 2 * t * (tip - ctrl))
        w_ = unit(np.asarray(side_hint, float) - d * float(np.dot(side_hint, d)))
        h_ = np.cross(d, w_)
        sc = 1.0 - t
        for c in (w_ * w0, h_ * h0, -w_ * w0, -h_ * h0):
            V.append(p + sc * c)
    d0 = unit(ctrl - base)
    pole = len(V); V.append(base - d0 * 0.4 * max(w0, h0))
    apex = len(V); V.append(tip)
    F = []
    for li in range(n_sec - 1):
        for k in range(4):
            k1 = (k + 1) % 4
            F.append([li * 4 + k, li * 4 + k1, (li + 1) * 4 + k1, (li + 1) * 4 + k])
    top = (n_sec - 1) * 4
    for k in range(4):
        k1 = (k + 1) % 4
        F.append([pole, k1, k])
        F.append([top + k, top + k1, apex])
    V = np.array(V)
    F = orient_outward(V, F, (base + ctrl) * 0.5)
    R = [tone_of(unit(face_normal(V, f))) for f in F]
    return V, F, R


# --------------------------------------------------------------------------- chest armour plates (copied from crystal_canine_parts.gem_inlay)
def gem_inlay(rim, centre, proud, sink, tone_of):
    """rim: [(surface point, normal)] x N round the plate outline (in order); centre: (point, normal). Top skin: the rim
    at proud[0], the centre at proud[1] above the surface (N facets round the raised centre: a faceted gem plate);
    bottom skin sunk 'sink'; side walls. tone_of(face normal) -> region. Closed."""
    N = len(rim)
    top = [np.asarray(p_, float) + unit(n_) * proud[0] for p_, n_ in rim]
    bot = [np.asarray(p_, float) - unit(n_) * sink for p_, n_ in rim]
    c_t = np.asarray(centre[0], float) + unit(centre[1]) * proud[1]
    c_b = np.asarray(centre[0], float) - unit(centre[1]) * sink
    V = np.array(top + bot + [c_t, c_b])
    it, ib = 2 * N, 2 * N + 1
    F = []
    for i in range(N):
        j = (i + 1) % N
        F.append([i, j, it])
        F.append([N + j, N + i, ib])
        F.append([j, i, N + i, N + j])
    F = orient_outward(V, F, 0.5 * (c_t + c_b))
    R = [tone_of(unit(face_normal(V, f))) for f in F]
    return V, F, R
