"""Aquaoct parametric parts + tentacle sweep machinery (pure numpy, deterministic).

Closed parts (0 open edges: surfaces of revolution with poles on the axis) that ride on the SDF body shell:

    lathe          profile (r, h) path revolved about an axis; ends on the axis become poles (eye domes, highlights,
                   the strap-button lens, the beak) -- copied from firefly_parts.py
    eye_dome       the big round EYE: a domed disc (dark pupil centre, teal iris ring) whose skirt is buried under the
                   head surface inside the goggle-mask ring
    lens_dome      a small closed lens (eye highlight, strap-button lens)
    beak           a short closed cone (the small beak-like mouth under the mask)
    dome_decal     a round closed decal conforming to the shell (the head SPOTS; from shadowbun_parts.py)

Tentacle sweep (used by the build's SDF and its region / weight fields):

    Tentacle       one leaf-like tentacle: a dense spine (catmull through (r, z) control points in its radial plane at
                   azimuth az, optional tangential bow), per-station frames (T tangent, N leaf-top normal = the
                   radial-plane normal pointing up / out, rolled by 'twist' (root -> tip deg), W = T x N) and per-station half-width
                   (left / right edge, soft waves) + half-thickness profiles.
                   .eval(P) -> nearest spine station data + the leaf SDF: an elliptical cross-section (w / hw, n' / ht)
                   whose edges droop toward -N (n' = n + droop hw (w / hw)^2: an arched leaf, aqua top, cream underside
                   visible below the edges), with a rounded end cap (tangent overshoot / ht); distance by IQ's
                   ellipsoid bound approximation (k0 (k0 - 1) / k1), so the smooth unions blend by true distance.
    spine_param    nearest-polyline arc parameter (copied from shadowbun_parts.py)
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
        self.chain = None
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
    """Flip the whole face list if the signed volume about inside_pt is negative (the path ran the other way)."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0):
    """profile: [(r, h), ...] path; an end with r == 0 becomes a pole. seg_regions: region per profile segment."""
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    th = 2 * math.pi * np.arange(n) / n
    V, F, R, rings = [], [], [], []
    for r, h in profile:
        if r <= 1e-9:
            rings.append([len(V)]); V.append(c + a * h)
            continue
        idx = []
        for t in th:
            idx.append(len(V))
            V.append(c + a * h + r * (math.cos(t) * su * u + math.sin(t) * sv * v))
        rings.append(idx)
    for k in range(len(profile) - 1):
        A, B = rings[k], rings[k + 1]
        reg = seg_regions[k]
        if len(A) == 1 and len(B) == 1:
            continue
        if len(A) == 1:
            for i in range(n):
                F.append([A[0], B[(i + 1) % n], B[i]]); R.append(reg)
        elif len(B) == 1:
            for i in range(n):
                F.append([A[i], A[(i + 1) % n], B[0]]); R.append(reg)
        else:
            for i in range(n):
                F.append([A[i], A[(i + 1) % n], B[(i + 1) % n], B[i]]); R.append(reg)
    V = np.array(V)
    F = orient_outward(V, F, c + a * np.mean([p[1] for p in profile]))
    return V, F, R


def eye_dome(centre, axis, radius, pupil_r, h_top, dome, sink, segs=28):
    """The big round eye: a domed disc on 'axis' through 'centre' (the head-surface point at the ring centre). Top at
    h_top (above the surface), domed by 'dome'; pupil (dark) inside pupil_r, iris ring out to radius; the straight skirt
    runs down to -sink (buried in the head)."""
    prof = [(0.0, h_top), (pupil_r * 0.55, h_top - dome * 0.10), (pupil_r, h_top - dome * 0.32),
            (radius * 0.82, h_top - dome * 0.66), (radius, h_top - dome), (radius, -sink), (0.0, -sink)]
    regs = ["eye_pupil", "eye_pupil", "eye_iris", "eye_iris", "eye_iris", "eye_iris"]
    return lathe(prof, regs, segs, centre, axis)


def lens_dome(centre, axis, radius, height, sink, region, segs=16):
    """small closed lens: a dome of 'height' over a disc of 'radius', skirt down to -sink."""
    prof = [(0.0, height), (radius * 0.6, height * 0.72), (radius, 0.0), (radius, -sink), (0.0, -sink)]
    return lathe(prof, [region] * 4, segs, centre, axis)


def beak(base, axis, length, radius, sink, segs=14):
    """short closed cone from 'base' along 'axis' (tip at length), its foot buried by 'sink'."""
    prof = [(0.0, length), (radius * 0.45, length * 0.55), (radius, 0.0), (radius, -sink), (0.0, -sink)]
    return lathe(prof, ["beak"] * 4, segs, base, axis)


def dome_decal(anchor, normal, radius, project, t_up, t_dn, dome, rings, segs, region, up_hint=(0.0, 0.0, 1.0)):
    """Closed round DECAL conforming to the shell (copied from shadowbun_parts.py): a polar grid projected onto the shell
    along the anchor normal (project(pts, n) -> surface hits), top skin = hit + n (t_up + dome (1 - rho^2)), bottom skin
    = hit - n t_dn, a side wall joins the rims. Used for the round head SPOTS (a true circle at any mesh density)."""
    n = unit(normal)
    e1 = np.asarray(up_hint, float) - n * float(np.dot(up_hint, n))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n, [1.0, 0.0, 0.0])
    e1 = unit(e1); e2 = np.cross(n, e1)
    a = np.asarray(anchor, float)
    pts, rho = [a.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            t = 2 * math.pi * m / segs
            pts.append(a + radius * r_ * (math.cos(t) * e1 + math.sin(t) * e2)); rho.append(r_)
    pts = np.array(pts); rho = np.array(rho)
    hit = project(pts, n)
    top = hit + n[None] * (t_up + dome * (1.0 - rho ** 2))[:, None]
    bot = hit - n[None] * t_dn
    nt = len(pts)
    V = np.vstack([top, bot])
    F, ref = [], []

    def ring_idx(k):
        return [1 + (k - 1) * segs + m for m in range(segs)]
    for off, sgn in ((0, 1.0), (nt, -1.0)):
        r1 = ring_idx(1)
        for m in range(segs):
            F.append([off, off + r1[m], off + r1[(m + 1) % segs]]); ref.append(sgn * n)
        for k in range(1, rings):
            A, B = ring_idx(k), ring_idx(k + 1)
            for m in range(segs):
                F.append([off + A[m], off + B[m], off + B[(m + 1) % segs], off + A[(m + 1) % segs]]); ref.append(sgn * n)
    rim = ring_idx(rings)
    for m in range(segs):
        m1 = (m + 1) % segs
        F.append([rim[m], rim[m1], nt + rim[m1], nt + rim[m]])
        ref.append(unit(0.5 * (pts[rim[m]] + pts[rim[m1]]) - a))

    def fn(V_, f):
        q = V_[f]
        nn = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            nn = nn + np.cross(q[2] - q[0], q[3] - q[0])
        return nn
    Fo = [f if np.dot(fn(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, [region] * len(Fo)


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


# --------------------------------------------------------------------------- tentacle sweep
def _catmull2(points, n_per):
    P = np.asarray(points, float)
    P = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0.0, 1.0, n_per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-2])
    return np.array(out)


class Tentacle:
    """One leaf-like tentacle (see the module doc). ctrl: [(r, z)] in the radial plane at azimuth az_deg (0 = front -Y,
    + toward +X) about the vertical axis through (0, y_axis). hw_knots / ht_knots: [(s, value)] profiles over the arc
    fraction s. waves: (count, amplitude, seed) soft edge waves (left / right edges de-phased). mirror_of: when this
    is the -X twin of a +X tentacle, its left / right edge waves swap so the pair is mirror-symmetric."""

    def __init__(self, name, az_deg, ctrl, y_axis, hw_knots, ht_knots, hw_scale, ht_scale, droop, twist_deg, bow,
                 waves, ns=56, swap_edges=False):
        self.name = name
        self.az = math.radians(az_deg)
        R2 = np.array([math.sin(self.az), -math.cos(self.az), 0.0])           # radial (outward) horizontal
        TG = np.array([math.cos(self.az), math.sin(self.az), 0.0])            # tangential horizontal (= z x R2)
        self.R2, self.TG = R2, TG
        dense = _catmull2(np.array(ctrl, float), 12)
        al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))])
        t = np.linspace(0.0, al[-1], ns)
        rz = np.stack([np.interp(t, al, dense[:, 0]), np.interp(t, al, dense[:, 1])], 1)
        sf = t / al[-1]
        C = (np.array([0.0, y_axis, 0.0])[None] + rz[:, :1] * R2[None] + rz[:, 1:2] * np.array([0.0, 0.0, 1.0])[None]
             + (bow * np.sin(math.pi * sf))[:, None] * TG[None])
        T = np.gradient(C, axis=0)
        T = T / np.linalg.norm(T, axis=1, keepdims=True)
        tr, tz = T @ R2, T[:, 2]
        N = -tz[:, None] * R2[None] + tr[:, None] * np.array([0.0, 0.0, 1.0])[None]
        N = N - T * np.einsum("ij,ij->i", N, T)[:, None]
        N = N / np.linalg.norm(N, axis=1, keepdims=True)
        W = np.cross(T, N)
        tw = np.radians(twist_deg[0] + (twist_deg[1] - twist_deg[0]) * sf)    # roll the leaf about its spine (root, tip)
        N2 = N * np.cos(tw)[:, None] + W * np.sin(tw)[:, None]
        W2 = np.cross(T, N2)
        self.C, self.T, self.N, self.W = C, T, N2, W2
        self.sf = sf
        self.arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
        self.L = float(self.arc[-1])
        hk = np.array(hw_knots, float); tk = np.array(ht_knots, float)
        hw = np.interp(sf, hk[:, 0], hk[:, 1]) * hw_scale
        self.ht = np.interp(sf, tk[:, 0], tk[:, 1]) * ht_scale
        n_w, a_w, seed = waves
        env = np.clip((sf - 0.22) / 0.18, 0, 1) * np.clip((1.0 - sf) / 0.12, 0, 1)
        e1 = 1.0 + a_w * env * np.sin(2 * math.pi * n_w * sf + 6.283 * hash01(1, seed))
        e2 = 1.0 + a_w * env * np.sin(2 * math.pi * (n_w + 0.5) * sf + 6.283 * hash01(2, seed))
        self.hw_r, self.hw_l = (hw * e2, hw * e1) if swap_edges else (hw * e1, hw * e2)
        self.droop = droop
        self.wsign = -1.0 if swap_edges else 1.0        # the mirrored twin's W axis is the mirror of -W

    def bbox(self, pad):
        m = float(max(self.hw_r.max(), self.hw_l.max(), self.ht.max())) + pad
        return self.C.min(0) - m, self.C.max(0) + m

    def eval(self, P):
        """-> dict(d leaf SDF, s arc length, sf arc fraction, w, nn (droop-corrected normal coord), tside = nn / ht)."""
        P = np.asarray(P, float)
        C = self.C
        best = np.full(len(P), 1e18); bi = np.zeros(len(P), np.int64); bu = np.zeros(len(P))
        for i in range(len(C) - 1):
            a = C[i]; ab = C[i + 1] - a
            u = np.clip(((P - a) @ ab) / float(ab @ ab), 0.0, 1.0)
            q = a + u[:, None] * ab
            d2 = np.einsum("ij,ij->i", P - q, P - q)
            m = d2 < best
            best[m] = d2[m]; bi[m] = i; bu[m] = u[m]
        u = bu[:, None]
        lerp = lambda A: A[bi] * (1 - u) + A[bi + 1] * u
        q = lerp(C)
        T = lerp(self.T); T /= np.linalg.norm(T, axis=1, keepdims=True)
        N = lerp(self.N); N = N - T * np.einsum("ij,ij->i", N, T)[:, None]; N /= np.linalg.norm(N, axis=1, keepdims=True)
        W = np.cross(T, N)
        D = P - q
        w = np.einsum("ij,ij->i", D, W); nn = np.einsum("ij,ij->i", D, N); tt = np.einsum("ij,ij->i", D, T)
        s = self.arc[bi] + bu * (self.arc[bi + 1] - self.arc[bi])
        sf = s / self.arc[-1]
        hw = np.where(w >= 0, np.interp(sf, self.sf, self.hw_r), np.interp(sf, self.sf, self.hw_l))
        ht = np.interp(sf, self.sf, self.ht)
        n2 = nn + self.droop * hw * (w / hw) ** 2
        ends = ((bi == 0) & (bu <= 0.0)) | ((bi == len(C) - 2) & (bu >= 1.0))
        tt = np.where(ends, tt, 0.0)
        # IQ's bound-correct ellipsoid approximation in the local frame (radii hw, ht, ht): k0 (k0 - 1) / k1
        k0 = np.sqrt((w / hw) ** 2 + (n2 / ht) ** 2 + (tt / ht) ** 2)
        k1 = np.sqrt((w / hw ** 2) ** 2 + (n2 / ht ** 2) ** 2 + (tt / ht ** 2) ** 2)
        d = np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -ht)
        return {"d": d, "s": s, "sf": sf, "w": w, "n2": n2, "tside": n2 / ht, "wfrac": self.wsign * w / hw}
