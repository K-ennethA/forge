"""Chest Monster parametric parts (pure numpy, deterministic): the hard-surface pieces of the box creature.

Every generator returns V (n,3) float, F list of vertex-index lists (tris / quads), R list of per-face region names; every
part here is a CLOSED solid (0 open edges): the box and the lid are rounded boxes (a cube grid projected onto a box with
rounded edges), the eye emblem / iris / glint are chamfered plates with fan caps, the crystals are faceted hexagonal
prisms with a pyramid tip and a buried base cap, the magic smoke is a low-poly core + bipyramid rays, the spark flecks
are octahedra. The only SDF piece of the unit is the dangling leg (chest_monster_build.py).

    Part / open_edges / orient_outward / frame_from_axis    the firefly_parts idioms
    rounded_box     tapered box with rounded (bevelled) edges: the BOX body and the LID band
    vesica          almond / vesica-piscis outline (two circle arcs): the eye emblem and the iris
    plate           closed plate from a convex 2D outline in the XZ plane: back face buried, front face chamfered
    crystal_prism   faceted hexagonal prism with a pointed pyramid tip; per hex face a tone region, optional white
                    facet-streak cells (the streak is its own polygons, never paint across a face)
    ellipsoid       low-poly ellipsoid (lathe with poles): the smoke core, the leg dots
    spike           closed 4-sided bipyramid along a direction: one smoke-burst ray
    octa            octahedron: one static spark fleck
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    """-> (a, u, v): a = axis, u = the up_hint made perpendicular, v = a x u."""
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


class Part:
    def __init__(self, name, V, F, R, bone=None):
        self.name = name
        self.V = np.asarray(V, float)
        self.F = [list(map(int, f)) for f in F]
        self.R = list(R)
        self.bone = bone
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


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def orient_convex(V, F, centre):
    """Per face: point away from centre (valid for a convex solid; used by the rounded boxes)."""
    out = []
    for f in F:
        q = V[f]
        n = np.zeros(3)
        for i in range(1, len(f) - 1):
            n += np.cross(q[i] - q[0], q[i + 1] - q[0])
        out.append(f if np.dot(n, q.mean(0) - centre) >= 0 else f[::-1])
    return out


# --------------------------------------------------------------------------- rounded box
def _axis_nodes(h, r, n_mid, n_bev):
    """nodes on [-h, h]: n_bev tan-spaced nodes in each bevel band (uniform bevel angles after projection), n_mid
    interior segments between the bands"""
    bev = [(h - r) + r * math.tan(math.radians(45.0) * k / n_bev) for k in range(1, n_bev + 1)]
    mid = np.linspace(-(h - r), h - r, n_mid + 1).tolist()
    return np.array([-b for b in bev[::-1]] + mid + bev)


def rounded_box(half_xy_top, z0, z1, r, n_mid, n_bev, base_scale=1.0):
    """Closed box with rounded edges (bevel radius r, 2*n_bev segments per 90 deg), centred on X/Y, z0..z1.
    half_xy_top = (hx, hy) at z1; the X/Y half sizes grow linearly to base_scale x at z0 (a TAPER: wider at the base).
    n_mid = (nx, ny, nz) interior segments per axis. Returns V, F (quads), plus the per-face outward normal."""
    hx, hy = half_xy_top
    hz = 0.5 * (z1 - z0)
    zc = 0.5 * (z0 + z1)
    xs, ys, zs = (_axis_nodes(h, r, n, n_bev) for h, n in ((hx, n_mid[0]), (hy, n_mid[1]), (hz, n_mid[2])))
    NX, NY, NZ = len(xs) - 1, len(ys) - 1, len(zs) - 1
    key, V = {}, []

    def vid(i, j, k):
        kk = (i, j, k)
        if kk not in key:
            q = np.array([xs[i], ys[j], zs[k]])
            hh = np.array([hx, hy, hz])
            inner = np.clip(q, -(hh - r), hh - r)
            d = q - inner
            p = inner + r * unit(d) if np.linalg.norm(d) > 1e-12 else q
            key[kk] = len(V); V.append(p)
        return key[kk]
    F = []
    for i in range(NX):
        for j in range(NY):
            for k in (0, NZ):
                F.append([vid(i, j, k), vid(i + 1, j, k), vid(i + 1, j + 1, k), vid(i, j + 1, k)])
    for i in range(NX):
        for k in range(NZ):
            for j in (0, NY):
                F.append([vid(i, j, k), vid(i + 1, j, k), vid(i + 1, j, k + 1), vid(i, j, k + 1)])
    for j in range(NY):
        for k in range(NZ):
            for i in (0, NX):
                F.append([vid(i, j, k), vid(i, j + 1, k), vid(i, j + 1, k + 1), vid(i, j, k + 1)])
    V = np.array(V)
    s = 1.0 + (base_scale - 1.0) * (hz - V[:, 2]) / (2.0 * hz)       # 1 at the top, base_scale at the bottom
    V[:, 0] *= s; V[:, 1] *= s
    V[:, 2] += zc
    F = orient_convex(V, F, np.array([0.0, 0.0, zc]))
    N = []
    for f in F:
        q = V[f]
        N.append(unit(np.cross(q[1] - q[0], q[2] - q[0]) + np.cross(q[2] - q[0], q[3] - q[0])))
    return V, F, np.array(N)


# --------------------------------------------------------------------------- eye emblem
def vesica(a, b, n):
    """Almond (vesica piscis) outline, half-width a, half-height b, centred on 0: 2n points CCW in (x, z), the tips at
    (0, +-b). Two circle arcs of radius R = (a^2 + b^2) / 2a centred at x = -+(R - a)."""
    R = (a * a + b * b) / (2.0 * a)
    cx = R - a
    th = math.asin(b / R)
    pts = []
    for k in range(n):                                  # right arc: bottom tip -> top tip (centre at x = -cx)
        t = -th + 2.0 * th * k / n
        pts.append((-cx + R * math.cos(t), R * math.sin(t)))
    for k in range(n):                                  # left arc: top tip -> bottom tip (centre at x = +cx)
        t = math.pi - th + 2.0 * th * k / n
        pts.append((cx + R * math.cos(t), R * math.sin(t)))
    return np.array(pts)


def plate(outline, centre_xz, y_front, y_back, chamfer, region, side_region=None, inset=None):
    """Closed plate from a convex 2D outline (x, z offsets about centre_xz) facing -Y: the back face at y_back (buried),
    a straight side wall to y_front + chamfer, then a 45-deg chamfer to the front face at y_front inset by 'inset'.
    Caps are fans to a centre pole (convex outline). Regions: front + chamfer = region, side wall = side_region."""
    cxz = np.asarray(centre_xz, float)
    n = len(outline)
    c2 = outline.mean(0)
    ins = chamfer if inset is None else inset
    sz = np.linalg.norm(outline - c2, axis=1)
    shrink = np.clip(1.0 - ins / np.maximum(sz, 1e-9), 0.0, 1.0)
    inner = c2 + (outline - c2) * shrink[:, None]
    rings = [(outline, y_back), (outline, y_front + chamfer), (inner, y_front)]
    V = []
    for pts, y in rings:
        for x, z in pts:
            V.append((cxz[0] + x, y, cxz[1] + z))
    pb = len(V); V.append((cxz[0] + c2[0], y_back, cxz[1] + c2[1]))
    pf = len(V); V.append((cxz[0] + c2[0], y_front, cxz[1] + c2[1]))
    F, R = [], []
    sreg = side_region or region
    for r_ in range(2):
        for i in range(n):
            i1 = (i + 1) % n
            F.append([r_ * n + i, r_ * n + i1, (r_ + 1) * n + i1, (r_ + 1) * n + i]); R.append(sreg if r_ == 0 else region)
    for i in range(n):
        i1 = (i + 1) % n
        F.append([pb, i1, i]); R.append(sreg)
        F.append([pf, 2 * n + i, 2 * n + i1]); R.append(region)
    V = np.array(V, float)
    F = orient_outward(V, F, np.array([cxz[0] + c2[0], 0.5 * (y_front + y_back), cxz[1] + c2[1]]))
    return V, F, R


# --------------------------------------------------------------------------- crystals
HEX_COLS = (0.0, 0.40, 0.60)         # per hex face: the column split (the middle column carries the facet streak)


def crystal_prism(base, axis, length, radius, tip_frac, spin_deg, bury, tone_of, streak_faces, streak_s=(0.30, 0.68)):
    """Hexagonal prism from base (buried 'bury' below it) along axis: the straight body to (1 - tip_frac) x length,
    then a pyramid tip to the apex. tone_of(face_normal) -> region per hex face (the facet tones); streak_faces = the
    hex-face indices that carry a white streak cell (middle column, streak_s fraction of the length). Closed."""
    a, u, v = frame_from_axis(axis, (0.0, 0.0, 1.0) if abs(unit(axis)[2]) < 0.95 else (1.0, 0.0, 0.0))
    base = np.asarray(base, float)
    sp = math.radians(spin_deg)
    corners = [math.cos(sp + k * math.pi / 3) * u + math.sin(sp + k * math.pi / 3) * v for k in range(6)]
    sec = []                                             # section points (unit radius), 3 per hex face
    for k in range(6):
        c0, c1 = corners[k], corners[(k + 1) % 6]
        for f in HEX_COLS:
            sec.append(c0 * (1 - f) + c1 * f)
    ns = len(sec)
    Lb = length * (1.0 - tip_frac)
    levels = [-bury, streak_s[0] * length, streak_s[1] * length, Lb]
    assert levels[2] < Lb, "streak runs into the tip"
    V = []
    for s in levels:
        for p in sec:
            V.append(base + a * s + radius * p)
    pole_b = len(V); V.append(base - a * bury)
    apex = len(V); V.append(base + a * length)
    F, R = [], []
    face_n = [unit(corners[k] + corners[(k + 1) % 6]) for k in range(6)]
    tone = [tone_of(n_) for n_ in face_n]
    for li in range(len(levels) - 1):
        for i in range(ns):
            i1 = (i + 1) % ns
            k = i // 3
            col = i % 3
            reg = tone[k]
            if li == 1 and col == 1 and k in streak_faces:
                reg = "crystal_streak"
            F.append([li * ns + i, li * ns + i1, (li + 1) * ns + i1, (li + 1) * ns + i]); R.append(reg)
    top = (len(levels) - 1) * ns
    for i in range(ns):
        i1 = (i + 1) % ns
        F.append([pole_b, i1, i]); R.append(tone[i // 3])
        F.append([top + i, top + i1, apex]); R.append(tone[i // 3])
    V = np.array(V)
    F = orient_outward(V, F, base + a * (0.5 * Lb))
    return V, F, R, face_n


# --------------------------------------------------------------------------- smoke / sparks / dots
def ellipsoid(c, radii, n_seg, n_ring, region, frame=None):
    """Low-poly ellipsoid (poles on local z). frame = (ex, ey, ez) columns, default world axes."""
    c = np.asarray(c, float)
    ex, ey, ez = (np.eye(3) if frame is None else np.asarray(frame, float))
    V = [c - ez * radii[2]]
    for j in range(1, n_ring):
        ph = -math.pi / 2 + math.pi * j / n_ring
        for i in range(n_seg):
            t = 2 * math.pi * i / n_seg
            V.append(c + ex * radii[0] * math.cos(ph) * math.cos(t) + ey * radii[1] * math.cos(ph) * math.sin(t)
                     + ez * radii[2] * math.sin(ph))
    V.append(c + ez * radii[2])
    V = np.array(V)
    F = []
    for i in range(n_seg):
        F.append([0, 1 + (i + 1) % n_seg, 1 + i])
    for j in range(n_ring - 2):
        a0, b0 = 1 + j * n_seg, 1 + (j + 1) * n_seg
        for i in range(n_seg):
            i1 = (i + 1) % n_seg
            F.append([a0 + i, a0 + i1, b0 + i1, b0 + i])
    top = len(V) - 1; a0 = 1 + (n_ring - 2) * n_seg
    for i in range(n_seg):
        F.append([a0 + i, a0 + (i + 1) % n_seg, top])
    F = orient_outward(V, F, c)
    return V, F, [region] * len(F)


def spike(root, direction, length, w_mid, t_mid, s_mid, back, region, up_hint=(0.0, 0.0, 1.0)):
    """Closed 4-sided bipyramid: a back point 'back' behind root, a diamond section (half-widths w_mid across, t_mid
    thin) at s_mid x length, the sharp tip at length. One smoke-burst ray."""
    a, u, v = frame_from_axis(direction, up_hint)
    r = np.asarray(root, float)
    m = r + a * (s_mid * length)
    V = np.array([r - a * back, m + v * w_mid, m + u * t_mid, m - v * w_mid, m - u * t_mid, r + a * length])
    F = [[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4], [5, 1, 2], [5, 2, 3], [5, 3, 4], [5, 4, 1]]
    F = orient_outward(V, F, m)
    return V, F, [region] * len(F)


def octa(c, r, region, spin=0.0):
    c = np.asarray(c, float)
    cs, sn = math.cos(spin), math.sin(spin)
    V = np.array([c + [r * cs, r * sn, 0], c + [-r * sn, r * cs, 0], c - [r * cs, r * sn, 0], c - [-r * sn, r * cs, 0],
                  c + [0, 0, r * 1.3], c - [0, 0, r * 1.3]])
    F = [[4, 0, 1], [4, 1, 2], [4, 2, 3], [4, 3, 0], [5, 1, 0], [5, 2, 1], [5, 3, 2], [5, 0, 3]]
    F = orient_outward(V, F, c)
    return V, F, [region] * len(F)
