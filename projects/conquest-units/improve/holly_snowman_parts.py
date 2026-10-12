"""Holly Snowman parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF snow shell.

Copied from mystic_snow_spirit_parts.py (Part / open_edges / orient_outward / hash01 / frame_from_axis / ell_point /
ellipsoid / conform_dome) and leaf_bird_parts.py (pillow); copied, not imported. Every generator returns V (n,3) float,
F list of vertex-index lists, R list of per-face region names; every piece is a CLOSED solid (0 open edges):

    ellipsoid     low-poly ellipsoid, poles on a local axis: the BERRIES and the three BUTTONS (berry_ball puts the top
                  pole on the light direction and paints its first ring cap 'berry_hi': a round highlight dot)
    conform_dome  closed dome whose rings conform to a surface (a projector callback: the head's base field): the two
                  simple oval EYES
    pillow        thin closed sheet over an (s, t) grid, perimeter shared by both skins (knife-thin leaf edge)
    leaf          a pillow leaf: curved spine (quadratic Bezier with an out-of-plane + in-plane bow), teardrop / lance
                  half-width profile, a gentle cup across, a light half (the rim side of the midrib) + a dark half, and
                  an optional narrow light RIM strip on one margin: the two LEAF EARS and the arm FRONDS
    tube          closed tapered tube with flat caps: the short frond STALK at each shoulder
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


def signed_volume(V, F):
    vol = 0.0
    c = np.asarray(V, float).mean(0)
    for f in F:
        p0 = V[f[0]] - c
        for i in range(1, len(f) - 1):
            vol += float(np.dot(p0, np.cross(V[f[i]] - c, V[f[i + 1]] - c)))
    return vol / 6.0


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def ell_point(c, radii, d):
    """The point of the axis-aligned ellipsoid (c, radii) on the ray from c along d, and the outward normal there."""
    c = np.asarray(c, float); r = np.asarray(radii, float); d = unit(d)
    t = 1.0 / np.linalg.norm(d / r)
    S = c + d * t
    return S, unit((S - c) / (r * r))


# --------------------------------------------------------------------------- ellipsoid (berries, buttons)
def ellipsoid(c, radii, n_seg, n_ring, region, frame=None):
    """Low-poly ellipsoid (poles on local z). frame = (ex, ey, ez) rows, default world axes. Face order: bottom fan,
    bands, TOP fan last (berry_ball relies on it)."""
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


def berry_ball(c, r, light, n_seg, n_ring, flat=1.0, axis=None):
    """A berry / button sphere whose top pole faces the light; the pole's first ring (cap radius cos(90 - 180/n_ring)
    of r) is the 'berry_hi' highlight dot, the rest 'berry'. flat < 1 squashes the ball along 'axis' (a button pressed
    into the snow), default no squash."""
    ez = unit(light)
    _, ex, ey = frame_from_axis(ez, (0.0, 0.0, 1.0) if abs(ez[2]) < 0.95 else (0.0, 1.0, 0.0))
    V, F, R = ellipsoid(c, (r, r, r), n_seg, n_ring, "berry", frame=(ex, ey, ez))
    R = list(R)
    for k in range(len(F) - n_seg, len(F)):
        R[k] = "berry_hi"
    if flat < 1.0 and axis is not None:
        a = unit(axis)
        q = V - c
        V = c + q - np.outer(q @ a, a) * (1.0 - flat)
    return V, F, R


# --------------------------------------------------------------------------- conform dome (eyes)
def conform_dome(surf, S, e_u, e_v, rings, depth, nseg, lift=0.004, sink=0.04):
    """Closed dome on a smooth surface, centred at its surface point S: e_u / e_v span the tangent plane. surf(T) ->
    (the surface point nearest T, its outward unit normal) (mystic's version took an ellipsoid; this one takes the
    projector, so the eyes conform to the head's real base field).
    rings = [(hu, hv, region_inside), ...] from the centre out (elliptic rings, any aspect; the last is the outline).
    Each ring point is projected onto the surface, then lifted along the surface normal by
    lift + depth sqrt(1 - rho^2) (rho = the ring's size relative to the outline): one smooth surface, exact region
    edges. The base (outline pushed 'sink' inward + a back pole) closes it inside the head. -> V, F, R."""
    S = np.asarray(S, float); e_u = unit(e_u); e_v = unit(e_v)
    HU, HV = rings[-1][0], rings[-1][1]
    th = 2.0 * math.pi * np.arange(nseg) / nseg

    def on_surf(T_, h):
        P_, n_ = surf(T_)
        return P_ + n_ * h

    S0, n0 = surf(S)
    V = [S0 + n0 * (lift + depth)]
    for hu, hv, _ in rings:
        rho = 0.5 * (hu / HU + hv / HV)
        h = lift + depth * math.sqrt(max(0.0, 1.0 - rho * rho))
        for t in th:
            V.append(on_surf(S0 + e_u * hu * math.cos(t) + e_v * hv * math.sin(t), h))
    nr = len(rings)
    for t in th:
        V.append(on_surf(S0 + e_u * HU * math.cos(t) + e_v * HV * math.sin(t), -sink))
    V.append(S0 - n0 * sink * 1.5)
    V = np.array(V)
    ring = lambda j, i: 1 + j * nseg + (i % nseg)
    F, R = [], []
    for i in range(nseg):
        F.append([0, ring(0, i), ring(0, i + 1)]); R.append(rings[0][2])
    for j in range(nr - 1):
        for i in range(nseg):
            F.append([ring(j, i), ring(j + 1, i), ring(j + 1, i + 1), ring(j, i + 1)]); R.append(rings[j + 1][2])
    for i in range(nseg):                                                    # outline -> buried base ring (side wall)
        F.append([ring(nr - 1, i), ring(nr, i), ring(nr, i + 1), ring(nr - 1, i + 1)]); R.append(rings[-1][2])
    bp = len(V) - 1
    for i in range(nseg):
        F.append([ring(nr, i), bp, ring(nr, i + 1)]); R.append(rings[-1][2])
    F = orient_outward(V, F, S0 - n0 * sink * 0.5)
    return V, F, R


# --------------------------------------------------------------------------- pillow (leaf sheets; leaf_bird_parts)
def face_normal(V, f):
    q = V[f]
    n = np.cross(q[1] - q[0], q[2] - q[0])
    if len(f) == 4:
        n = n + np.cross(q[2] - q[0], q[3] - q[0])
    return n


def pillow(P, N, H, region_fn, weld=1e-7):
    """P (ns+1, nt+1, 3) grid points, N (ns+1, nt+1, 3) unit offset directions, H (ns+1, nt+1) half-thickness (forced 0
    on the perimeter). Perimeter vertices are single (shared by both skins); interior vertices are doubled (top +N H,
    bottom -N H). Coincident perimeter points (a pointed tip) are welded; faces left with < 3 distinct vertices drop.
    region_fn(i, j, top) -> region of grid cell (i, j) on the top (+N) / bottom skin. Returns V, F, R, IJ."""
    P = np.asarray(P, float); N = np.asarray(N, float); H = np.asarray(H, float)
    ns, nt = P.shape[0] - 1, P.shape[1] - 1
    assert ns >= 2 and nt >= 2
    V, IJ = [], []
    top = -np.ones((ns + 1, nt + 1), int); bot = -np.ones((ns + 1, nt + 1), int)
    key = {}
    for i in range(ns + 1):
        for j in range(nt + 1):
            if i in (0, ns) or j in (0, nt):
                k = tuple(np.round(P[i, j] / weld).astype(np.int64))
                if k not in key:
                    key[k] = len(V); V.append(P[i, j]); IJ.append((i, j))
                top[i, j] = bot[i, j] = key[k]
            else:
                top[i, j] = len(V); V.append(P[i, j] + N[i, j] * H[i, j]); IJ.append((i, j))
                bot[i, j] = len(V); V.append(P[i, j] - N[i, j] * H[i, j]); IJ.append((i, j))
    V = np.array(V)
    F, R = [], []
    for i in range(ns):
        for j in range(nt):
            for idx, is_top in ((top, True), (bot, False)):
                f = [idx[i, j], idx[i + 1, j], idx[i + 1, j + 1], idx[i, j + 1]]
                if not is_top:
                    f = f[::-1]
                g = []
                for v in f:
                    if v not in g:
                        g.append(v)
                if len(g) < 3:
                    continue
                F.append(g); R.append(region_fn(i, j, is_top))
    probe = [k for k, f in enumerate(F) if all(0 < IJ[v][0] < ns and 0 < IJ[v][1] < nt for v in f)]
    if probe:
        f = F[probe[0]]
        i_, j_ = IJ[f[0]]
        sgn = 1.0 if f[0] == top[i_, j_] else -1.0
        if np.dot(face_normal(V, f), N[i_, j_]) * sgn < 0:
            F = [f[::-1] for f in F]
    return V, F, R, np.array(IJ)


def leaf(base, tip, normal_hint, rim_hint, half_w, thick, ns, t_grid, shape=(0.5, 1.3), bow_n=0.0, bow_u=0.0,
         cup=0.0, rim_s=None, s_pow=1.0):
    """A closed leaf pillow from base to tip.
    normal_hint: the leaf face's facing (made perpendicular to the base->tip axis); rim_hint: picks the +u margin (the
    t = 0 margin: the light half + the optional rim strip). half_w: max half-width; profile f(s) = s^a (1-s)^b
    normalised to 1 (a = 0.5 -> a round base, b > 1 -> a pointed, slightly concave tip). bow_n / bow_u: spine bow
    (share of the length) along the face normal / in-plane toward the +u margin. cup: margins lift toward +n by
    cup * w^2 / half_w (a gentle cup, concave on the face side). rim_s: (s0, s1) range of the t = 0 rim strip or None.
    t_grid: increasing t values 0..1 (a narrow first cell = a narrow rim). Returns V, F, R, info."""
    base = np.asarray(base, float); tip = np.asarray(tip, float)
    d = unit(tip - base); L = float(np.linalg.norm(tip - base))
    n0 = unit(np.asarray(normal_hint, float) - d * float(np.dot(normal_hint, d)))
    u0 = unit(np.cross(n0, d))
    if float(np.dot(u0, rim_hint)) < 0.0:
        u0 = -u0                               # u toward the rim side (the face normal n0 keeps the hint's side)
    ctrl = 0.5 * (base + tip) + n0 * bow_n * L + u0 * bow_u * L
    s = np.linspace(0.0, 1.0, ns + 1) ** s_pow
    C = ((1 - s) ** 2)[:, None] * base + (2 * s * (1 - s))[:, None] * ctrl + (s ** 2)[:, None] * tip
    T = 2 * (1 - s)[:, None] * (ctrl - base) + 2 * s[:, None] * (tip - ctrl)
    T = T / np.linalg.norm(T, axis=1)[:, None]
    a_, b_ = shape
    sm = a_ / (a_ + b_)
    fmax = sm ** a_ * (1 - sm) ** b_
    f = (np.clip(s, 0, 1) ** a_) * (np.clip(1 - s, 0, 1) ** b_) / fmax
    t = np.asarray(t_grid, float)
    nt = len(t) - 1
    P = np.zeros((ns + 1, nt + 1, 3)); N = np.zeros((ns + 1, nt + 1, 3)); H = np.zeros((ns + 1, nt + 1))
    for i in range(ns + 1):
        u_i = unit(u0 - T[i] * float(np.dot(u0, T[i])))
        n_i = unit(np.cross(u_i, T[i]))
        if float(np.dot(n_i, n0)) < 0:
            n_i = -n_i
        w = half_w * f[i]
        for j in range(nt + 1):
            q = 1.0 - 2.0 * t[j]                               # +1 on the rim margin, -1 on the other
            P[i, j] = C[i] + u_i * w * q + n_i * cup * (w * q) ** 2 / max(half_w, 1e-9)
            N[i, j] = n_i
            H[i, j] = thick * math.sqrt(max(f[i], 0.0)) * math.sqrt(max(0.0, 1.0 - q * q))

    def region(i, j, top):
        sm_ = 0.5 * (s[i] + s[i + 1])
        if rim_s is not None and j == 0 and rim_s[0] <= sm_ <= rim_s[1]:
            return "leaf_rim"
        return "leaf_light" if 0.5 * (t[j] + t[j + 1]) < 0.5 else "leaf"
    V, F, R, IJ = pillow(P, N, H, region)
    return V, F, R, {"length": L, "spine": C, "normal": n0, "u": u0}


# --------------------------------------------------------------------------- tube (frond stalk)
def tube(a, b, r0, r1, nseg, nst, region):
    """closed tapered tube a -> b, flat capped"""
    a = np.asarray(a, float); b = np.asarray(b, float)
    ax, e1, e2 = frame_from_axis(b - a)
    V = [a]
    for k in range(nst + 1):
        s = k / nst
        c = a + (b - a) * s
        r = r0 + (r1 - r0) * s
        for i in range(nseg):
            th = 2 * math.pi * i / nseg
            V.append(c + r * (math.cos(th) * e1 + math.sin(th) * e2))
    V.append(b)
    V = np.array(V)
    ring = lambda k, i: 1 + k * nseg + (i % nseg)
    F = [[0, ring(0, i + 1), ring(0, i)] for i in range(nseg)]
    for k in range(nst):
        for i in range(nseg):
            F.append([ring(k, i), ring(k, i + 1), ring(k + 1, i + 1), ring(k + 1, i)])
    e = len(V) - 1
    F += [[ring(nst, i), ring(nst, i + 1), e] for i in range(nseg)]
    F = orient_outward(V, F, 0.5 * (a + b))
    return V, F, [region] * len(F)
