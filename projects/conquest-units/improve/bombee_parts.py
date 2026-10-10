"""Bombee parametric parts (pure numpy, deterministic): the pieces that ride on the SDF body ball.

Every generator returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every SOLID is
closed (0 open edges): stinger + spikes are surfaces of revolution with poles on the axis, the face features are closed
conforming decals (a domed / flat top skin + a sunk bottom skin + a side wall), the wing veins are closed flat ribbons
straddling the membrane, the wing rims closed tubes round the leaf outline. The ONLY open pieces are the wing
MEMBRANES: single-surface leaf sheets drawn by the double-sided alphaMode-BLEND wing material (the firefly smoke path).

    lathe          profile (r, h) path revolved about an axis; ends on the axis become poles (firefly_parts copy)
    dome_decal     round closed decal conforming to a surface (eye, highlight, blush), optional oval squash
                   (shadowbun_parts copy)
    ribbon         closed flat ribbon along a station list (centre C, surface normal N, in-surface side Q): four
                   rails C +- Q hw +- N t, quads between stations, flat end caps (smile line, wing veins)
    tube_loop      closed tube round a closed polyline (wing rim outline)
    Leaf           one leaf / petal WING: pointed root, rounded tip, cupped toward +N; maps leaf coords (x along the
                   axis, b across) to 3D and builds the membrane grid, the diamond vein lattice and the rim loop
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


class Part:
    def __init__(self, name, V, F, R, bone=None, wing=False):
        self.name = name
        self.V = np.asarray(V, float)
        self.F = [list(map(int, f)) for f in F]
        self.R = list(R)
        self.bone = bone
        self.wing = wing                 # True = membrane (wing material: double-sided BLEND)
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
    n = np.cross(q[1] - q[0], q[2] - q[0])
    if len(f) == 4:
        n = n + np.cross(q[2] - q[0], q[3] - q[0])
    return n


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


def orient_outward(V, F, inside_pt):
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


# --------------------------------------------------------------------------- lathe (firefly_parts)
def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1), phase=0.0):
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    th = phase + 2 * math.pi * np.arange(n) / n
    V, F, R, rings = [], [], [], []
    for r, h in profile:
        if r <= 1e-9:
            rings.append([len(V)]); V.append(c + a * h)
            continue
        idx = []
        for t in th:
            idx.append(len(V)); V.append(c + a * h + r * (math.cos(t) * u + math.sin(t) * v))
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


# --------------------------------------------------------------------------- conforming decal (shadowbun_parts)
def dome_decal(anchor, normal, radius, project, t_up, t_dn, dome, rings, segs, region, squash=(1.0, 1.0),
               up_hint=(0.0, 0.0, 1.0), offset=(0.0, 0.0)):
    """Closed round decal on a surface. anchor: a surface point; normal: the decal axis (outward). project(pts, n) ->
    surface points hit by rays cast along -n through pts. squash = (along up_hint, across) radius scale; offset = the
    decal centre shifted (along up_hint, across) in the anchor's tangent plane. Top skin = hit + n (t_up + dome
    (1 - rho^2)); bottom skin = hit - n t_dn; a side wall joins the rims."""
    n = unit(normal)
    e1 = np.asarray(up_hint, float) - n * float(np.dot(up_hint, n))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n, [1.0, 0.0, 0.0])
    e1 = unit(e1); e2 = np.cross(n, e1)
    a = np.asarray(anchor, float) + offset[0] * e1 + offset[1] * e2
    pts, rho = [a.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            t = 2 * math.pi * m / segs
            pts.append(a + radius * r_ * (math.cos(t) * squash[0] * e1 + math.sin(t) * squash[1] * e2)); rho.append(r_)
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
    Fo = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, [region] * len(Fo)


# --------------------------------------------------------------------------- ribbon + tube
def ribbon(C, N, Q, hw, t_up, t_dn, region):
    """Closed flat ribbon. Per station i: rails C +- Q hw_i (side) and +N t_up / -N t_dn (faces). 4 vertices per
    station [top+, top-, bot-, bot+], quads between stations, flat end caps; every face points away from the
    ribbon's own centre line."""
    C = np.asarray(C, float); N = np.asarray(N, float); Q = np.asarray(Q, float)
    hw = np.broadcast_to(np.asarray(hw, float), (len(C),))
    tu = np.broadcast_to(np.asarray(t_up, float), (len(C),))
    td = np.broadcast_to(np.asarray(t_dn, float), (len(C),))
    V = []
    for c, n, q, w, u_, d_ in zip(C, N, Q, hw, tu, td):
        V += [c + q * w + n * u_, c - q * w + n * u_, c - q * w - n * d_, c + q * w - n * d_]
    V = np.array(V)
    Cl = C + N * ((tu - td) / 2.0)[:, None]
    m = len(C)
    F, ref = [], []
    for i in range(m - 1):
        for k in range(4):
            k1 = (k + 1) % 4
            F.append([i * 4 + k, i * 4 + k1, (i + 1) * 4 + k1, (i + 1) * 4 + k]); ref.append((Cl[i] + Cl[i + 1]) / 2)
    F.append([0, 1, 2, 3]); ref.append(Cl[0] + (Cl[0] - Cl[1]))
    F.append([(m - 1) * 4 + k for k in range(4)]); ref.append(Cl[m - 1] + (Cl[m - 1] - Cl[m - 2]))
    Fo = []
    for f, rp in zip(F, ref):
        q = V[f]
        Fo.append(f if np.dot(face_normal(V, f), q.mean(0) - rp) >= 0 else f[::-1])
    # end caps: their reference sits OUTSIDE the ribbon end, so flip them to point away from the interior
    for j in (len(Fo) - 2, len(Fo) - 1):
        Fo[j] = Fo[j][::-1]
    return V, Fo, [region] * len(Fo)


def tube_loop(P, Nref, r, n_minor, region):
    """Closed tube (torus topology) round the closed polyline P (no repeated end point); Nref (per point) fixes the
    tube frame's first radial axis (the wing normal), so the ring never twists."""
    P = np.asarray(P, float); Nref = np.asarray(Nref, float)
    m = len(P)
    T = np.array([unit(P[(i + 1) % m] - P[i - 1]) for i in range(m)])
    V = []
    for p, t, nr in zip(P, T, Nref):
        a = unit(nr - t * float(nr @ t))
        b = np.cross(t, a)
        for j in range(n_minor):
            ph = 2 * math.pi * j / n_minor
            V.append(p + r * (math.cos(ph) * a + math.sin(ph) * b))
    V = np.array(V)
    F = []
    for i in range(m):
        i1 = (i + 1) % m
        for j in range(n_minor):
            j1 = (j + 1) % n_minor
            F.append([i * n_minor + j, i1 * n_minor + j, i1 * n_minor + j1, i * n_minor + j1])
    Fo = []
    for i, f in enumerate(F):
        c = 0.5 * (P[f[0] // n_minor] + P[f[1] // n_minor])
        Fo.append(f if np.dot(face_normal(V, f), V[f].mean(0) - c) >= 0 else f[::-1])
    return V, Fo, [region] * len(Fo)


# --------------------------------------------------------------------------- leaf wing
class Leaf:
    """One leaf / petal wing. Leaf coords: x in [0, L] along the axis A from the pointed root, b across along W.
    3D point = root + A x + W b + N cup b^2 / hw(x)   (the edges curl toward +N by cup x the local half-width).
    hw(x) = HW f(x / L) / max f, f(s) = s^p (1 - s^2)^0.5: a POINTED root (p < 1) and a ROUNDED tip."""

    def __init__(self, root, axis, width_dir, length, hw_max, cup, point_pow=0.8):
        self.root = np.asarray(root, float)
        self.A = unit(axis)
        w = np.asarray(width_dir, float)
        self.W = unit(w - self.A * float(w @ self.A))
        self.N = np.cross(self.A, self.W)
        self.L, self.HW, self.cup, self.p = float(length), float(hw_max), float(cup), float(point_pow)
        ss = np.linspace(0.0, 1.0, 2001)
        self._fmax = float(self._f(ss).max())

    def _f(self, s):
        s = np.clip(np.asarray(s, float), 0.0, 1.0)
        return s ** self.p * np.sqrt(np.clip(1.0 - s * s, 0.0, None))

    def hw(self, s):
        return self.HW * self._f(s) / self._fmax

    def point(self, x, b):
        s = np.clip(x / self.L, 0.0, 1.0)
        h = np.maximum(self.hw(s), 1e-6)
        x = np.asarray(x, float); b = np.asarray(b, float)
        curl = self.cup * b * b / h
        return (self.root[None] + np.outer(x, self.A) + np.outer(b, self.W) + np.outer(curl, self.N)) if np.ndim(x) \
            else self.root + self.A * x + self.W * b + self.N * curl

    def normal(self, x, b, e=1e-4):
        px = self.point(np.atleast_1d(x + e), np.atleast_1d(b)) - self.point(np.atleast_1d(x - e), np.atleast_1d(b))
        pb = self.point(np.atleast_1d(x), np.atleast_1d(b + e)) - self.point(np.atleast_1d(x), np.atleast_1d(b - e))
        n = np.cross(px, pb)
        return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)

    def membrane(self, s0, ns, nt, region):
        """grid of ns stations (s0 .. 1; the tip a single vertex; s0 == 0 -> the root a single vertex too) x (nt + 1)
        across. Returns V, F, R, s per vertex."""
        ss = s0 + (1.0 - s0) * np.linspace(0.0, 1.0, ns + 1) ** 0.9
        V, S, rows = [], [], []
        for k, s in enumerate(ss):
            h = float(self.hw(s))
            if h < 1e-6:
                rows.append([len(V)]); V.append(self.point(np.array([s * self.L]), np.array([0.0]))[0]); S.append(s)
                continue
            tt = np.linspace(-1.0, 1.0, nt + 1)
            P = self.point(np.full(nt + 1, s * self.L), tt * h * 0.999)
            rows.append(list(range(len(V), len(V) + nt + 1))); V += list(P); S += [s] * (nt + 1)
        F = []
        for A_, B_ in zip(rows[:-1], rows[1:]):
            if len(A_) == 1 and len(B_) == 1:
                continue
            if len(A_) == 1:
                for j in range(nt):
                    F.append([A_[0], B_[j], B_[j + 1]])
            elif len(B_) == 1:
                for j in range(nt):
                    F.append([A_[j], B_[0], A_[j + 1]])
            else:
                for j in range(nt):
                    F.append([A_[j], B_[j], B_[j + 1], A_[j + 1]])
        V = np.array(V)
        Fo = [f if np.dot(face_normal(V, f), self.N) >= 0 else f[::-1] for f in F]   # one consistent side (+N)
        return V, Fo, [region] * len(Fo), np.array(S)

    def outline(self, s0, n):
        """closed outline polyline (right edge s0 -> tip, left edge back) + per-point surface normals"""
        ss = s0 + (1.0 - s0) * (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, n)))   # crowd stations at both ends
        right = [(s, 1.0) for s in ss]
        left = [(s, -1.0) for s in ss[::-1][1:-1]] if s0 <= 1e-9 else [(s, -1.0) for s in ss[::-1][1:]]
        pts = right + left
        x = np.array([s * self.L for s, _ in pts]); b = np.array([sg * float(self.hw(s)) for s, sg in pts])
        P = self.point(x, b)
        N = self.normal(x, b)
        return P, N, np.array([s for s, _ in pts])

    def veins(self, s0, gap, ang_deg, phase, margin, n_st, hw_v, ht_v, region, min_len=0.12):
        """diamond lattice: two families of straight lines at +-ang to the leaf axis, spaced 'gap' apart (leaf coords),
        clipped to the leaf inset by 'margin' (they end under the rim tube) and to s >= s0. Each kept run -> one closed
        ribbon of n_st stations conforming to the cupped membrane. Returns V, F, R, per-vertex s, count."""
        V, F, R, S, cnt = [], [], [], [], 0
        a = math.radians(ang_deg)
        span = self.L + 2 * self.HW
        for sgn in (1.0, -1.0):
            D = np.array([math.cos(a), sgn * math.sin(a)])
            Nn = np.array([-sgn * math.sin(a), math.cos(a)])
            cmax = span
            for c in np.arange(-cmax + phase * gap, cmax, gap):
                lam = np.linspace(-span, span, 1601)
                q = c * Nn[None] + lam[:, None] * D[None]
                s = q[:, 0] / self.L
                inside = (s > s0 + 0.01) & (s < 0.995) & (np.abs(q[:, 1]) < self.hw(s) - margin)
                if not inside.any():
                    continue
                idx = np.nonzero(inside)[0]
                brk = np.nonzero(np.diff(idx) > 1)[0]
                runs = np.split(idx, brk + 1)
                run = max(runs, key=len)
                l0, l1 = lam[run[0]], lam[run[-1]]
                if l1 - l0 < min_len:
                    continue
                ll = np.linspace(l0, l1, n_st)
                qq = c * Nn[None] + ll[:, None] * D[None]
                x, b = qq[:, 0], qq[:, 1]
                C = self.point(x, b)
                Nv = self.normal(x, b)
                T = np.gradient(C, axis=0)
                Q = np.array([unit(np.cross(n_, t_)) for n_, t_ in zip(Nv, T)])
                v_, f_, r_ = ribbon(C, Nv, Q, hw_v, ht_v, ht_v, region)
                o = len(V)
                V += list(v_); F += [[i + o for i in f] for f in f_]; R += r_
                S += list(np.repeat(x / self.L, 4))
                cnt += 1
        return np.array(V), F, R, np.array(S), cnt
