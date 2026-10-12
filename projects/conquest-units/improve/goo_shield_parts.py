"""Goo Shield parametric parts (pure numpy, deterministic): the closed pieces that are not the goo SDF shell.

Every generator returns V (n,3) float, F list of vertex-index lists, R list of per-face region names; every part here is
a CLOSED solid (0 open edges). The two ROCK pieces (head armour, body armour) are SDF solids built in goo_shield_build.py
(an envelope intersected with seeded tangent-plane cuts = flat rock facets); this module supplies their facet planes.

    Part / open_edges / orient_outward / frame_from_axis    the firefly_parts / chest_monster_parts idioms
    fib_dirs        deterministic, seeded-jittered Fibonacci sphere directions (the rock facet plane directions)
    facet_planes    tangent planes of a degree-1 homogeneous envelope at seeded directions, pushed in by a seeded depth:
                    intersecting the envelope with them chops a flat facet cap per plane (the faceted rock look)
    oval_decal      a closed, surface-conforming oval decal (concentric oval rings projected onto a surface by a
                    caller-supplied projector; a buried back cap + side wall close it): the eyes
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
    """Flip the whole face list if the signed volume about inside_pt is negative (faces must be consistently wound)."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


# --------------------------------------------------------------------------- rock facets
def fib_dirs(n, seed, jitter=0.35):
    """n unit directions on a Fibonacci sphere, each nudged by a seeded tangent jitter (fraction of the mean spacing):
    evenly spread but irregular, so the facets do not tile in visible rows."""
    rng = np.random.default_rng(seed)
    k = np.arange(n) + 0.5
    z = 1.0 - 2.0 * k / n
    th = math.pi * (1.0 + 5.0 ** 0.5) * k
    r = np.sqrt(np.maximum(1.0 - z * z, 0.0))
    D = np.stack([r * np.cos(th), r * np.sin(th), z], 1)
    sp = math.sqrt(4.0 * math.pi / n)
    D = D + rng.normal(0.0, jitter * sp, D.shape)
    return D / np.linalg.norm(D, axis=1)[:, None]


def facet_planes(f_env, centre, frame, dirs, depth_range, seed, keep=None, h=1e-4, tilt=0.0):
    """Tangent planes of the envelope f_env(Q) (Q in the envelope's LOCAL frame, f = 1 on the surface, homogeneous of
    degree 1 so the surface point along a direction d is d / f(d)) at each direction, pushed inward by a seeded depth,
    each normal nudged by a seeded tilt (std, rad) so flat envelope faces break into facets too.
    frame: 3x3, rows = the local axes in world. keep(S_world, n_world) -> bool filters planes. Returns (N (m,3) world
    normals, O (m,) offsets): a world point P is inside every facet when P @ N.T - O <= 0."""
    rng = np.random.default_rng(seed)
    M = np.asarray(frame, float)
    c = np.asarray(centre, float)
    Ns, Os = [], []
    for d in dirs:
        dep = rng.uniform(depth_range[0], depth_range[1])
        tj = rng.normal(0.0, tilt, 3) if tilt > 0 else np.zeros(3)
        Sl = d / float(f_env(d[None])[0])
        g = np.array([(f_env((Sl + h * e)[None])[0] - f_env((Sl - h * e)[None])[0]) / (2 * h) for e in np.eye(3)])
        nl = unit(unit(g) + tj)
        S = c + Sl @ M
        n = unit(nl @ M)
        if keep is not None and not keep(S, n):
            continue
        Ns.append(n); Os.append(float(n @ S) - dep)
    return np.array(Ns), np.array(Os)


# --------------------------------------------------------------------------- eye decal
def oval_decal(centre_xz, rings, ring_regions, n_seg, project, proud, depth, back_region):
    """Closed oval decal facing -Y. rings = [(a, b), ...] growing half-axes (x, z) of concentric ovals about centre_xz;
    ring_regions[k] = the region of the band INSIDE ring k (k = 0: the centre fan). project(x, z) -> (surface point,
    outward unit normal) on the carrier surface. Front vertices sit 'proud' off the surface, the back cap (outer ring
    + a pole) 'depth' inside it; a side wall joins the outer front ring to the back ring. Consistently wound, closed."""
    cx, cz = centre_xz
    ang = 2.0 * math.pi * np.arange(n_seg) / n_seg
    V, F, R = [], [], []

    def put(x, z, off):
        S, n = project(x, z)
        V.append(np.asarray(S, float) + np.asarray(n, float) * off)
        return len(V) - 1
    pole = put(cx, cz, proud)
    ring_ids = []
    for a, b in rings:
        ring_ids.append([put(cx + a * math.cos(t), cz + b * math.sin(t), proud) for t in ang])
    a_o, b_o = rings[-1]
    back = [put(cx + a_o * math.cos(t), cz + b_o * math.sin(t), -depth) for t in ang]
    bpole = put(cx, cz, -depth)
    r0 = ring_ids[0]
    for i in range(n_seg):
        i1 = (i + 1) % n_seg
        F.append([pole, r0[i1], r0[i]]); R.append(ring_regions[0])
    for k in range(len(rings) - 1):
        ra, rb = ring_ids[k], ring_ids[k + 1]
        for i in range(n_seg):
            i1 = (i + 1) % n_seg
            F.append([ra[i], ra[i1], rb[i1], rb[i]]); R.append(ring_regions[k + 1])
    ro = ring_ids[-1]
    for i in range(n_seg):
        i1 = (i + 1) % n_seg
        F.append([ro[i], ro[i1], back[i1], back[i]]); R.append(back_region)
        F.append([bpole, back[i], back[i1]]); R.append(back_region)
    V = np.array(V)
    front_pt = V[pole]
    inside = 0.5 * (front_pt + V[bpole])
    F2 = orient_outward(V, F, inside)
    return V, F2, R
