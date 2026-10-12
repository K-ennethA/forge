"""Closed parametric parts for the proxy finish (pure numpy, deterministic): the pieces that REPLACE what Pixal3D melts
(thin crystal blades, crowns, horns, wing fans, chest starbursts) and the small sheet features a decimated shell cannot
carry (eyes, buttons). Generators follow gemwing_dragon_parts.py's conventions (copied ideas, not imported): every
generator returns V (n,3), F (list of index lists), R (per-face region names) and every piece is CLOSED (0 open edges).

    blade       faceted hexagonal crystal prism, flattened across its fan plane, length bands root -> tip (one region per
                band, alternating facet tones within a band), an off-centre pyramid tip, the base pole buried in the body
    fan         several blades from one root toward a list of tips (a wing, a crown, a fin): the roots bunch so the fan
                reads as ONE grown piece
    starburst   blades radiating flat from a centre in the plane across 'normal' (a chest gem burst)
    decal       a closed slab conforming to the shell: an ellipse outline projected onto the shell along -normal, the top
                skin domed, the bottom sunk (eyes, buttons)
    open_edges  edge-use count check (0 = closed)
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


def tris(F):
    return int(sum(len(f) - 2 for f in F))


def face_normal(V, f):
    q = V[f]
    n = np.zeros(3)
    for i in range(1, len(f) - 1):
        n = n + np.cross(q[i] - q[0], q[i + 1] - q[0])
    return n


def orient_outward(V, F, inside_pt):
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def frame_from_axis(axis, up_hint):
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(u) < 1e-6:
            u = np.cross(a, [0.0, 1.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


def triangulate(F):
    out = []
    for f in F:
        for i in range(1, len(f) - 1):
            out.append([f[0], f[i], f[i + 1]])
    return out


class Part:
    def __init__(self, name, V, F, R, bone=None, kind="part"):
        self.name = name
        self.V = np.asarray(V, float)
        Ft, Rt = [], []
        for f, r in zip(F, R):
            for t in triangulate([f]):
                Ft.append([int(i) for i in t]); Rt.append(r)
        self.F, self.R = Ft, Rt
        self.bone = bone
        self.kind = kind

    def tris(self):
        return len(self.F)


# --------------------------------------------------------------------------------------------------------------------
def blade(base, tip, radius, squash=0.45, tip_frac=0.28, bury=0.08, fan_n=(1.0, 0.0, 0.0), spin_deg=0.0,
          tip_off=0.25, bands=(0.4,), band_regions=(("crystal",),), tip_region=None, seed=0):
    """hex prism base -> (1 - tip_frac) L, then a pyramid apex at tip (offset tip_off x radius across the fan). bands =
    the body's length band split fractions; band_regions[b] = tones of band b (facets alternate through the list,
    deterministic); tip_region = the apex facets' tone (default: the last band's)."""
    base = np.asarray(base, float); tip = np.asarray(tip, float)
    a, u, v = frame_from_axis(tip - base, fan_n)
    L = float(np.linalg.norm(tip - base))
    sp = math.radians(spin_deg)
    sec = [math.cos(sp + k * math.pi / 3) * squash * u + math.sin(sp + k * math.pi / 3) * v for k in range(6)]
    Lb = L * (1.0 - tip_frac)
    levels = [(-bury, 0.85)] + [(b * Lb, 1.0) for b in bands] + [(Lb, 0.94)]
    V = [base + a * s + radius * sc * p for s, sc in levels for p in sec]
    pole = len(V); V.append(base - a * (bury + 0.3 * radius))
    apex = len(V); V.append(tip + radius * tip_off * v)
    F, R = [], []
    nb = len(levels) - 1

    def tone(band, k):
        regs = band_regions[min(band, len(band_regions) - 1)]
        return regs[(k + seed) % len(regs)]
    for li in range(nb):
        for k in range(6):
            k1 = (k + 1) % 6
            F.append([li * 6 + k, li * 6 + k1, (li + 1) * 6 + k1, (li + 1) * 6 + k]); R.append(tone(li, k))
    top = nb * 6
    for k in range(6):
        k1 = (k + 1) % 6
        F.append([pole, k1, k]); R.append(tone(0, k))
        F.append([top + k, top + k1, apex]); R.append(tip_region if tip_region else tone(nb, k + 1))
    V = np.array(V)
    F = orient_outward(V, F, base + a * (0.5 * Lb))
    return V, F, R


def merge(pieces):
    V, F, R, off = [], [], [], 0
    for v, f, r in pieces:
        V.append(v); F += [[i + off for i in ff] for ff in f]; R += list(r); off += len(v)
    return np.vstack(V), F, R


def fan(root, tips, radius, root_spread=0.10, fan_n=(1.0, 0.0, 0.0), **kw):
    """blades from root toward each tip; radius scalar or per blade; the bases fan out root_spread along each blade's
    direction from the shared root (one grown piece)."""
    root = np.asarray(root, float)
    rs = radius if isinstance(radius, (list, tuple)) else [radius] * len(tips)
    pieces = []
    for i, (t, r) in enumerate(zip(tips, rs)):
        t = np.asarray(t, float)
        b = root + unit(t - root) * root_spread
        pieces.append(blade(b, t, r, fan_n=fan_n, seed=i, **kw))
    return merge(pieces)


def starburst(centre, normal, count, length, radius, up=(0.0, 0.0, 1.0), lift=0.02, length_mod=(1.0,), **kw):
    """count blades radiating in the plane across 'normal' (rotating from 'up'), lifted 'lift' off the centre along
    normal, lengths cycled from length_mod (x length): a flat gem burst lying on the surface."""
    c = np.asarray(centre, float); n = unit(normal)
    e1 = unit(np.asarray(up, float) - n * float(np.dot(up, n))); e2 = np.cross(n, e1)
    dirs = [math.cos(2 * math.pi * i / count) * e1 + math.sin(2 * math.pi * i / count) * e2 for i in range(count)]
    pieces = {}
    for i, d in enumerate(dirs):                 # +X (and midline) blades first; a -X blade = its partner mirrored in X
        if d[0] < -1e-9:
            continue
        Li = length * length_mod[i % len(length_mod)]
        kw_i = dict(kw)
        if abs(d[0]) < 1e-9:
            kw_i["tip_off"] = 0.0           # a midline blade's apex offset would point along X: keep it on the midline
        pieces[i] = blade(c + n * lift, c + n * lift + d * Li, radius, fan_n=n, seed=i, **kw_i)
    for i, d in enumerate(dirs):
        if i in pieces:
            continue
        dm = d * np.array([-1.0, 1.0, 1.0])
        j = int(np.argmin([np.linalg.norm(dm - e) for e in dirs]))
        V, F, R = pieces[j]
        pieces[i] = (V * np.array([-1.0, 1.0, 1.0]), [f[::-1] for f in F], list(R))
    return merge([pieces[i] for i in range(count)])


def decal(anchor, normal, a, b, project, t_up=0.012, t_dn=0.03, dome=0.01, segs=20, rings=3, region="decal",
          up=(0.0, 0.0, 1.0)):
    """closed slab on the shell: an ellipse (half sizes a across, b along 'up') around anchor, projected onto the shell
    with project(pts, n) -> surface points (rays along -n); top = hit + n (t_up + dome (1 - rho^2)), bottom = hit - n t_dn"""
    n = unit(normal)
    e1 = unit(np.asarray(up, float) - n * float(np.dot(up, n))); e2 = np.cross(e1, n)
    anc = np.asarray(anchor, float)
    pts, rho = [anc.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            t = 2 * math.pi * m / segs
            pts.append(anc + r_ * (a * math.cos(t) * e2 + b * math.sin(t) * e1)); rho.append(r_)
    pts = np.array(pts); rho = np.array(rho)
    hit = project(pts, n)
    top = hit + n[None] * (t_up + dome * (1.0 - rho ** 2))[:, None]
    bot = hit - n[None] * t_dn
    nt = len(pts)
    V = np.vstack([top, bot])
    F, ref = [], []

    def ring(k):
        return [1 + (k - 1) * segs + m for m in range(segs)]
    for off, sg in ((0, 1.0), (nt, -1.0)):
        r1 = ring(1)
        for m in range(segs):
            F.append([off, off + r1[m], off + r1[(m + 1) % segs]]); ref.append(sg * n)
        for k in range(1, rings):
            A, B = ring(k), ring(k + 1)
            for m in range(segs):
                F.append([off + A[m], off + B[m], off + B[(m + 1) % segs], off + A[(m + 1) % segs]]); ref.append(sg * n)
    rim = ring(rings)
    for m in range(segs):
        m1 = (m + 1) % segs
        F.append([rim[m], rim[m1], nt + rim[m1], nt + rim[m]])
        ref.append(unit(0.5 * (pts[rim[m]] + pts[rim[m1]]) - anc))
    F = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, F, [region] * len(F)
