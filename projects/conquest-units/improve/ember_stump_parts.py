"""Ember Stump parametric parts (pure numpy, deterministic): the closed pieces and the 2D SDF helper of the tree-stump unit.

Every mesh generator returns V (n,3) float, F list of vertex-index lists (tris / quads), R list of per-face region names;
every part here is a CLOSED solid (0 open edges). The organic pieces (trunk, mask, branch arms) are SDFs in
ember_stump_build.py; this module carries what they need (sd_polygon) plus the hard pieces:

    Part / open_edges / orient_outward / frame_from_axis / hash01    the firefly_parts idioms
    sd_polygon      signed distance to a 2D polygon (any simple polygon, vectorised): the mask jaws + the arm slab outline
    ellipsoid       low-poly ellipsoid (lathe with poles): the soot-crest core
    plate_frame     closed plate from a star-shaped 2D outline in an arbitrary (u, v, n) frame: the eyes, eye sockets,
                    the dark mouth back
    feather_blade   closed tapered feather blade (diamond section, midrib ridge, pointed both ends) along a bent spine:
                    one soot-crest feather; one upper facet strip is the highlight region
    tube_tongue     closed lathe along a curved spine (poles at both ends, optional lobing): one flame tongue
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


# --------------------------------------------------------------------------- 2D polygon SDF
def sd_polygon(Q, poly):
    """Signed distance (negative inside) from 2D points Q (n,2) to the simple polygon poly (m,2) (IQ's sdPolygon:
    nearest-edge distance, sign by the crossing rule; any winding)."""
    Q = np.asarray(Q, float)
    v = np.asarray(poly, float)
    d = np.sum((Q - v[0]) ** 2, 1)
    s = np.ones(len(Q))
    j = len(v) - 1
    for i in range(len(v)):
        e = v[j] - v[i]
        w = Q - v[i]
        t = np.clip((w @ e) / float(e @ e), 0.0, 1.0)
        b = w - np.outer(t, e)
        d = np.minimum(d, np.sum(b * b, 1))
        c1 = Q[:, 1] >= v[i, 1]
        c2 = Q[:, 1] < v[j, 1]
        c3 = e[0] * w[:, 1] > e[1] * w[:, 0]
        flip = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
        s = np.where(flip, -s, s)
        j = i
    return s * np.sqrt(d)


# --------------------------------------------------------------------------- crest core
def ellipsoid(c, radii, n_seg, n_ring, region, frame=None):
    """Low-poly ellipsoid (poles on local z). frame = (ex, ey, ez) rows, default world axes."""
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


# --------------------------------------------------------------------------- plates (eyes, sockets, mouth back)
def plate_frame(outline, origin, e_u, e_v, e_n, front, back, chamfer, region, side_region=None):
    """Closed plate from a star-shaped 2D outline (u, v offsets about origin) in the frame (e_u, e_v, e_n), e_n = the
    outward face normal: the back face at -back along e_n (buried), a straight side wall up to (front - chamfer), then a
    chamfer to the front face at +front inset by 'chamfer'. Caps are fans to the outline centroid."""
    o = np.asarray(origin, float)
    e_u, e_v, e_n = (np.asarray(x, float) for x in (e_u, e_v, e_n))
    outline = np.asarray(outline, float)
    n = len(outline)
    c2 = outline.mean(0)
    sz = np.linalg.norm(outline - c2, axis=1)
    shrink = np.clip(1.0 - chamfer / np.maximum(sz, 1e-9), 0.0, 1.0)
    inner = c2 + (outline - c2) * shrink[:, None]
    rings = [(outline, -back), (outline, front - chamfer), (inner, front)]
    V = []
    for pts, h in rings:
        for u, v in pts:
            V.append(o + e_u * u + e_v * v + e_n * h)
    pb = len(V); V.append(o + e_u * c2[0] + e_v * c2[1] - e_n * back)
    pf = len(V); V.append(o + e_u * c2[0] + e_v * c2[1] + e_n * front)
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
    F = orient_outward(V, F, o + e_u * c2[0] + e_v * c2[1] + e_n * (0.5 * (front - back)))
    return V, F, R


# --------------------------------------------------------------------------- soot-crest feather
def feather_blade(root, axis, face_n, length, half_w, half_t, bend, ns, hi_side, hi_s=(0.22, 0.96),
                  region="crest", hi_region="crest_hi"):
    """Closed tapered feather blade from root along axis (unit), the broad face facing face_n (made perpendicular to
    the axis). Spine c(s) = root + length (s axis + s^2 bend) (bend = a vector, the droop / curl). Section = a diamond:
    edge +w, midrib ridge +t, edge -w, ridge -t; half-width half_w sin(pi s^0.65)^0.6 (full leaf, pointed both ends), ridge
    half_t x the same taper. Poles at the root and the tip. The upper facet on side hi_side (+1: between edge +w and
    ridge +t, -1: ridge +t and edge -w) is the highlight region for s in hi_s. -> V, F, R, spine (ns+1, 3)."""
    root = np.asarray(root, float)
    a = unit(axis)
    bend = np.asarray(bend, float)
    s_ = np.linspace(0.0, 1.0, ns + 1)
    C = root[None] + length * (s_[:, None] * a[None] + (s_ ** 2)[:, None] * bend[None])
    T = np.gradient(C, axis=0)
    T = T / np.linalg.norm(T, axis=1)[:, None]
    V = [C[0]]
    for k in range(1, ns):
        t_ = T[k]
        nt = np.asarray(face_n, float) - t_ * float(np.dot(face_n, t_))
        nt = unit(nt)
        nw = unit(np.cross(nt, t_))
        taper = math.sin(math.pi * s_[k] ** 0.65) ** 0.6
        w = half_w * taper
        th = half_t * taper
        V += [C[k] + nw * w, C[k] + nt * th, C[k] - nw * w, C[k] - nt * th]
    V.append(C[-1])
    V = np.array(V)
    tip = len(V) - 1
    F, R = [], []
    ring = lambda k, j: 1 + (k - 1) * 4 + (j % 4)          # k = 1..ns-1
    for j in range(4):
        F.append([0, ring(1, j + 1), ring(1, j)]); R.append(region)
    for k in range(1, ns - 1):
        sm = 0.5 * (s_[k] + s_[k + 1])
        for j in range(4):
            reg = region
            if hi_s[0] <= sm <= hi_s[1] and ((hi_side > 0 and j == 0) or (hi_side < 0 and j == 1)):
                reg = hi_region
            F.append([ring(k, j), ring(k, j + 1), ring(k + 1, j + 1), ring(k + 1, j)]); R.append(reg)
    for j in range(4):
        F.append([ring(ns - 1, j), ring(ns - 1, j + 1), tip]); R.append(region)
    F = orient_outward(V, F, C[ns // 2])
    return V, F, R, C


# --------------------------------------------------------------------------- flame tongue
def tube_tongue(root, axis, up, length, radius, ns, nseg, wave, phase, region, flat=1.0, wide_s=0.35, tip_pow=1.35,
                lobes=0, lobe_a=0.0):
    """Closed tongue: a lathe along the spine c(s) = root + length (s axis + wave sin(pi (1.4 s) + phase) s up_perp),
    radius r(s) = radius (s / wide_s)^0.7 below wide_s, radius ((1 - s) / (1 - wide_s))^tip_pow above (poles at both
    ends: a pointed root buried in the mouth and a sharp licking tip). 'flat' squashes the section across the 'up'
    direction (1 = round); optional lobes ripple the section. -> V, F, R, spine."""
    root = np.asarray(root, float)
    a, u0, v0 = frame_from_axis(axis, up)
    s_ = np.linspace(0.0, 1.0, ns + 1)
    C = root[None] + length * (s_[:, None] * a[None]
                               + (wave * np.sin(math.pi * 1.4 * s_ + phase) * s_)[:, None] * u0[None])
    T = np.gradient(C, axis=0)
    T = T / np.linalg.norm(T, axis=1)[:, None]
    V = [C[0]]
    for k in range(1, ns):
        t_ = T[k]
        uu = unit(u0 - t_ * float(np.dot(u0, t_)))
        vv = np.cross(t_, uu)
        s = s_[k]
        r = radius * ((s / wide_s) ** 0.7 if s <= wide_s else ((1.0 - s) / (1.0 - wide_s)) ** tip_pow)
        for i in range(nseg):
            th = 2.0 * math.pi * i / nseg
            rm = 1.0 + (lobe_a * s * math.cos(lobes * th) * math.cos(phase + 3.0 * s) if lobes else 0.0)   # even in th: a tongue in the YZ plane stays mirror-symmetric
            V.append(C[k] + r * rm * (uu * math.cos(th) + vv * math.sin(th) * flat))
    V.append(C[-1])
    V = np.array(V)
    tip = len(V) - 1
    ring = lambda k, i: 1 + (k - 1) * nseg + (i % nseg)
    F = []
    for i in range(nseg):
        F.append([0, ring(1, i + 1), ring(1, i)])
    for k in range(1, ns - 1):
        for i in range(nseg):
            F.append([ring(k, i), ring(k, i + 1), ring(k + 1, i + 1), ring(k + 1, i)])
    for i in range(nseg):
        F.append([ring(ns - 1, i), ring(ns - 1, i + 1), tip])
    F = orient_outward(V, F, C[ns // 3])
    return V, F, [region] * len(F), C
