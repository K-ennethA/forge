"""Halo Sentinel parametric parts + SDF helpers (pure numpy, deterministic). Copied / adapted from firefly_parts.py (Part,
open_edges, orient_outward, frame_from_axis, lathe) and lava_komodo_parts.py (mirror_part); the unit's own generators:

    halo_ring      closed torus (the floating halo), angles symmetric about the front (-Y) so the ring is mirror-exact
    tube           closed tube along a path, pole-capped at both ends (the horn stems)
    lathe          profile revolved about an axis, ends on the axis = poles (the horn arrowheads: closed cones)
    sd_ellipsoid_axis   an ellipsoid along an arbitrary axis a -> b (the wing-like arm paddles)
    sd_rows_pillar      the jelly pillar: per-height elliptic cross-sections interpolated from measured rows
    almond_front / oval_front   front-projected eye fields (> 0 inside) for the iso-cuts
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


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
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def signed_volume(V, F):
    vol = 0.0
    for f in F:
        for i in range(1, len(f) - 1):
            vol += np.dot(V[f[0]], np.cross(V[f[i]], V[f[i + 1]]))
    return vol / 6.0


def mirror_part(p, name):
    """the X = 0 mirror image of a part (faces reversed so the winding stays outward)"""
    V = p.V.copy()
    V[:, 0] *= -1.0
    q = Part(name, V, [f[::-1] for f in p.F], p.R, bone=p.bone)
    for k, v in p.__dict__.items():
        if k not in q.__dict__:
            setattr(q, k, v)
    return q


def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1)):
    """profile: [(r, h), ...]; an end with r == 0 becomes a pole (firefly_parts.lathe, no oval / rmod)."""
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
            V.append(c + a * h + r * (math.cos(t) * u + math.sin(t) * v))
        rings.append(idx)
    for k in range(len(profile) - 1):
        A, B = rings[k], rings[k + 1]
        reg = seg_regions[k]
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


def halo_ring(centre, normal, R0, r, n_major, n_minor, region="halo"):
    """closed torus about 'normal'. Major angle 0 = the ring's FRONT point (toward -Y, tilted with the ring); the angles
    are symmetric about it (n_major even) so the ring is exactly mirror-symmetric about X = 0."""
    nrm = unit(normal)
    f0 = np.array([0.0, -1.0, 0.0])
    u = unit(f0 - nrm * float(f0 @ nrm))            # in-plane front direction
    v = np.cross(nrm, u)                             # in-plane side direction (+-X)
    c = np.asarray(centre, float)
    V, F = [], []
    for i in range(n_major):
        t = 2 * math.pi * i / n_major
        radial = math.cos(t) * u + math.sin(t) * v
        rc = c + R0 * radial
        for j in range(n_minor):
            p = 2 * math.pi * j / n_minor
            V.append(rc + r * (math.cos(p) * radial + math.sin(p) * nrm))
    for i in range(n_major):
        for j in range(n_minor):
            i1, j1 = (i + 1) % n_major, (j + 1) % n_minor
            F.append([i * n_minor + j, i1 * n_minor + j, i1 * n_minor + j1, i * n_minor + j1])
    V = np.array(V)
    if signed_volume(V, F) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


def tube(path, radii, sides, region, up=(0.0, 0.0, 1.0)):
    """closed tube along path (n, 3), radius per point, both ends capped by a pole (a fan to the path end point).
    Parallel-transport frames."""
    C = np.asarray(path, float)
    r = np.asarray(radii, float)
    n = len(C)
    T = np.gradient(C, axis=0)
    T = T / np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
    u0 = np.asarray(up, float) - T[0] * float(np.asarray(up, float) @ T[0])
    if np.linalg.norm(u0) < 1e-6:
        u0 = np.cross(T[0], (1.0, 0.0, 0.0))
    U = [unit(u0)]
    for i in range(1, n):
        U.append(unit(U[-1] - T[i] * float(U[-1] @ T[i])))
    U = np.array(U)
    W = np.cross(T, U)
    ph = 2 * math.pi * np.arange(sides) / sides
    V = [C[0]]
    for i in range(n):
        for p in ph:
            V.append(C[i] + r[i] * (math.cos(p) * W[i] + math.sin(p) * U[i]))
    V.append(C[-1])
    V = np.array(V)
    F = []
    ring = lambda i, j: 1 + i * sides + (j % sides)
    for j in range(sides):
        F.append([0, ring(0, j + 1), ring(0, j)])
    for i in range(n - 1):
        for j in range(sides):
            F.append([ring(i, j), ring(i, j + 1), ring(i + 1, j + 1), ring(i + 1, j)])
    last = len(V) - 1
    for j in range(sides):
        F.append([last, ring(n - 1, j), ring(n - 1, j + 1)])
    if signed_volume(V, F) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- SDF helpers
def sd_ellipsoid_axis(P, a, b, r_side, r_depth, depth_dir=(0.0, 1.0, 0.0)):
    """ellipsoid whose long axis runs a -> b (semi-length |b - a| / 2); 'r_side' across the axis in the plane of the
    axis and depth_dir's normal, 'r_depth' along the depth direction made perpendicular (IQ's ellipsoid bound)."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    c = 0.5 * (a + b)
    ax = unit(b - a)
    dd = np.asarray(depth_dir, float) - ax * float(np.asarray(depth_dir, float) @ ax)
    dd = unit(dd)
    sd_ = np.cross(dd, ax)
    Q = P - c
    q = np.stack([Q @ ax, Q @ sd_, Q @ dd], 1)
    r = np.array([0.5 * np.linalg.norm(b - a), r_side, r_depth])
    k0 = np.linalg.norm(q / r, axis=1)
    k1 = np.linalg.norm(q / (r * r), axis=1)
    return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -r.min())


class PillarRows:
    """rows (z, x_in, x_out, y_front, y_back) -> per-height elliptic sections (centre cx, cy; half-axes rx, ry)"""

    def __init__(self, rows):
        R = np.array(sorted(rows, key=lambda r: r[0]), float)
        self.z = R[:, 0]
        self.cx = 0.5 * (R[:, 1] + R[:, 2]); self.rx = 0.5 * (R[:, 2] - R[:, 1])
        self.cy = 0.5 * (R[:, 3] + R[:, 4]); self.ry = 0.5 * (R[:, 4] - R[:, 3])

    def at(self, z):
        return tuple(np.interp(z, self.z, arr) for arr in (self.cx, self.cy, self.rx, self.ry))

    def sd(self, P):
        """per-height 2D ellipse distance (IQ bound) scaled by the flare (cos of the side slope), capped top + bottom"""
        cx, cy, rx, ry = self.at(P[:, 2])
        qx = (P[:, 0] - cx); qy = (P[:, 1] - cy)
        k0 = np.hypot(qx / rx, qy / ry)
        k1 = np.hypot(qx / rx ** 2, qy / ry ** 2)
        d = np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -np.minimum(rx, ry))
        return d * 0.92                                   # flare slope ~0.5 -> distance shrinks by <= cos 27 deg

    def theta(self, P):
        """angle round the section centre (0 = front -Y, +90 deg = +X), in degrees"""
        cx, cy, rx, ry = self.at(P[:, 2])
        return np.degrees(np.arctan2((P[:, 0] - cx) / rx, -(P[:, 1] - cy) / ry))


def almond_front(P, cx, cz, a, b, p=0.75):
    """front-projected vertical almond (> 0 inside): half-width(z) = a (1 - (dz / b)^2)^p (p 0.5 = ellipse, 1 = lens)"""
    t = np.clip((P[:, 2] - cz) / b, -1.0, 1.0)
    hw = a * np.maximum(1.0 - t * t, 0.0) ** p
    inside_z = 1.0 - np.abs((P[:, 2] - cz) / b)
    return np.minimum(hw - np.abs(P[:, 0] - cx), inside_z * a)


def oval_front(P, cx, cz, a, b):
    """front-projected ellipse (> 0 inside), scaled ~ distance in u near the edge"""
    return (1.0 - np.hypot((P[:, 0] - cx) / a, (P[:, 2] - cz) / b)) * min(a, b)
