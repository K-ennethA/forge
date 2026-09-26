"""Shared helpers for the Conquest unit rig + clip wave (rig_unit.py, the contract checker,
render_animated.py). Pure measurement / math; nothing here saves a file.

MeshData      world-space verts, edges, adjacency, islands of the unit mesh.
trace_limb    deterministic limb skeleton from the mesh: start at a contact tip, grow slab by
              slab along an axis ('z' upward, or 'r' radially inward) through mesh-connected
              vertices, stop when the slab set jumps in size (the limb has merged into the body).
              (The probe that produced the landmark numbers in rig_unit.py's SPEC table.)
seg_dist      point -> segment / polyline distances (vectorised).
VineCurve / vine_weights / vine_wave
              the vine-chain archetype (flexible multi-bone appendages; see its section).
action_fcurves  Blender 5.0 slotted-action fcurve walk.
"""
import math
import numpy as np

CLIP_NAMES = ("idle", "walk")                     # Conquest UnitAnimator CLIP_IDLE / CLIP_WALK
ALLOWED_CLIP_NAMES = ("idle", "walk", "idle-loop", "walk-loop")
FPS = 24


class MeshData:
    def __init__(self, ob, coords=None):
        me = ob.data
        n = len(me.vertices)
        if coords is None:
            co = np.empty(n * 3); me.vertices.foreach_get("co", co)
            M = np.array(ob.matrix_world)
            self.W = co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
        else:
            self.W = coords
        ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev)
        self.ev = ev.reshape(-1, 2)
        self.n = n
        src = np.concatenate([self.ev[:, 0], self.ev[:, 1]])
        order = np.argsort(src, kind="stable")
        self.nbr = np.concatenate([self.ev[:, 1], self.ev[:, 0]])[order]
        cnt = np.bincount(src, minlength=n)
        self.ptr = np.concatenate([[0], np.cumsum(cnt)])
        lab = -np.ones(n, dtype=np.int64)
        k = 0
        for s in range(n):
            if lab[s] >= 0:
                continue
            stack = [s]; lab[s] = k
            while stack:
                v = stack.pop()
                for u in self.nbr[self.ptr[v]:self.ptr[v + 1]]:
                    if lab[u] < 0:
                        lab[u] = k; stack.append(u)
            k += 1
        self.island = lab
        sizes = np.bincount(lab)
        self.main = lab == int(np.argmax(sizes))
        self.island_sizes = sizes
        lo, hi = self.W.min(0), self.W.max(0)
        self.lo, self.hi = lo, hi
        self.maxdim = float((hi - lo).max())
        self.H = float(hi[2] - lo[2])

    def neighbours(self, v):
        return self.nbr[self.ptr[v]:self.ptr[v + 1]]

    def neighbour_mean(self, X):
        deg = np.bincount(self.ev.ravel(), minlength=self.n).astype(float)
        s = np.zeros_like(X)
        for k in range(X.shape[1]):
            s[:, k] = np.bincount(self.ev[:, 0], X[self.ev[:, 1], k], minlength=self.n) + \
                np.bincount(self.ev[:, 1], X[self.ev[:, 0], k], minlength=self.n)
        return s / np.maximum(deg, 1)[:, None]


def seg_dist(P, a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    ab = b - a
    t = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-12), 0.0, 1.0)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


def poly_dist(P, pts):
    return np.min(np.stack([seg_dist(P, pts[i], pts[i + 1]) for i in range(len(pts) - 1)]), axis=0)


def trace_limb(M, axis, xy, seed_z=None, ds=None, rs=None, merge=2.6, max_slabs=40, centre=(0.0, 0.0)):
    """Trace a limb from its tip. axis 'z': tip = lowest verts near xy, grow upward.
    axis 'r': tip = outermost verts near xy (and seed_z if given), grow radially inward.
    Returns {"poly": [{"c", "n", "a"}], "stop": reason, "verts": [indices]}."""
    W = M.W
    ds = ds or 0.03 * M.maxdim
    rs = rs or 0.10 * M.maxdim
    c = np.array(centre)
    r = np.hypot(W[:, 0] - c[0], W[:, 1] - c[1])
    a = W[:, 2].copy() if axis == "z" else -r
    dxy = np.hypot(W[:, 0] - xy[0], W[:, 1] - xy[1])
    near = M.main & (dxy < rs)
    if seed_z is not None:
        near &= np.abs(W[:, 2] - seed_z) < rs
    if not near.any():
        return {"poly": [], "stop": "no seed", "verts": []}
    a0 = a[near].min()
    cur = set(np.nonzero(near & (a <= a0 + ds))[0].tolist())
    ext = int(np.nonzero(near)[0][np.argmin(a[near])])
    cur = _flood(M, {ext}, lambda v: v in cur)
    poly = [{"c": W[list(cur)].mean(0).tolist(), "n": len(cur), "a": float(a0)}]
    limb = set(cur)
    hi_a = a0 + ds
    stop = "max_slabs"
    for _ in range(max_slabs):
        lo_a, hi2 = hi_a, hi_a + ds
        inslab = lambda v, lo_a=lo_a, hi2=hi2: lo_a < a[v] <= hi2
        seeds = set()
        for v in cur:
            for u in M.neighbours(v):
                if inslab(u):
                    seeds.add(int(u))
        if not seeds:
            stop = "end"
            break
        nxt = _flood(M, seeds, inslab)
        med = float(np.median([p["n"] for p in poly][-3:]))
        if len(poly) >= 2 and len(nxt) > merge * med:
            stop = "merged(n %d > %.1f x %.0f)" % (len(nxt), merge, med)
            break
        poly.append({"c": W[list(nxt)].mean(0).tolist(), "n": len(nxt), "a": float(hi2)})
        limb |= nxt
        cur = nxt
        hi_a = hi2
    return {"poly": poly, "stop": stop, "verts": sorted(limb)}


def _flood(M, seeds, ok):
    out = set(seeds)
    stack = list(seeds)
    while stack:
        v = stack.pop()
        for u in M.neighbours(v):
            u = int(u)
            if u not in out and ok(u):
                out.add(u); stack.append(u)
    return out


# =========================================================================== vine-chain archetype
# A VINE is a flexible appendage (Vineweave's arms, tendrils, tentacle capes): a multi-bone
# chain whose rest shape is a CURVE, not a posed rigid limb. Three pieces, all pure numpy:
#   VineCurve     rest-straight vine (origin + direction + length) -> posed curve, defined by a
#                 rotation field R(s) integrated along arc length from bend / twist ops. The
#                 same field re-poses the high sculpt (deform) and places the chain's bones
#                 (bones), so mesh and rig agree by construction.
#   vine_weights  arc-length hat weights: every vertex blends the two bones whose midpoints
#                 bracket its s (<= 2 chain influences), the root blends into a parent bone.
#   vine_wave     per-bone idle angle: a travelling wave root -> tip, amplitude growing to the
#                 tip, integer harmonics of the loop (seam-closed by construction).
def _rot(axis, ang):
    a = np.asarray(axis, float); a = a / max(np.linalg.norm(a), 1e-12)
    c, s = math.cos(ang), math.sin(ang)
    x, y, z = a
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def _ramp_weight(s, s0, s1, ramp):
    """Curvature density over [s0, s1]: smooth ramp-in/out of width ramp*(s1-s0), flat between."""
    if s < s0 or s > s1:
        return 0.0
    w = max(ramp * (s1 - s0), 1e-9)
    a = min((s - s0) / w, 1.0); b = min((s1 - s) / w, 1.0)
    return (a * a * (3 - 2 * a)) * (b * b * (3 - 2 * b))


class VineCurve:
    """ops: [{"kind": "bend", "s0", "s1", "deg", "axis": world xyz, "ramp": 0..0.5},
             {"kind": "twist", "s0", "s1", "deg", "ramp"}]   (twist = about the current tangent).
    Angles are distributed over [s0, s1] by the ramp density so the total equals deg."""

    def __init__(self, origin, rest_dir, length, ops, ds=0.002):
        self.o = np.asarray(origin, float)
        self.d0 = np.asarray(rest_dir, float) / np.linalg.norm(rest_dir)
        self.L = float(length)
        self.ops = ops
        n = int(math.ceil(self.L / ds)) + 1
        self.s = np.linspace(0.0, self.L, n)
        ds = self.s[1] - self.s[0]
        dens = []
        for op in ops:
            w = np.array([_ramp_weight(v, op["s0"], op["s1"], op.get("ramp", 0.25)) for v in self.s])
            tot = w.sum() * ds
            dens.append(w / tot * math.radians(op["deg"]) if tot > 0 else w)
        R = np.eye(3)
        C = self.o.copy()
        self.R = np.empty((n, 3, 3)); self.C = np.empty((n, 3))
        for i in range(n):
            self.R[i] = R; self.C[i] = C
            for op, dn in zip(ops, dens):
                ang = dn[i] * ds
                if ang == 0.0:
                    continue
                ax = op["axis"] if op["kind"] == "bend" else R @ self.d0
                R = _rot(ax, ang) @ R
            C = C + (R @ self.d0) * ds

    def _idx(self, s):
        f = np.clip(np.asarray(s, float), 0.0, self.L) / self.L * (len(self.s) - 1)
        return np.clip(np.round(f).astype(int), 0, len(self.s) - 1)

    def frame(self, s):
        i = self._idx(s)
        return self.C[i], self.R[i]

    def tangent(self, s):
        return self.frame(s)[1] @ self.d0

    def deform(self, P, s):
        """P (n,3) world points with arc parameter s (n,); s <= 0 -> unchanged (root side)."""
        P = np.asarray(P, float); s = np.asarray(s, float)
        out = P.copy()
        m = s > 0
        if m.any():
            sc = np.clip(s[m], 0, self.L)
            f = sc / self.L * (len(self.s) - 1)
            i0 = np.clip(np.floor(f).astype(int), 0, len(self.s) - 2)
            u = (f - i0)[:, None]
            Ci = self.C[i0] * (1 - u) + self.C[i0 + 1] * u          # linear between the 2 mm nodes:
            Ri = self.R[i0] * (1 - u[:, :, None]) + self.R[i0 + 1] * u[:, :, None]   # no per-node steps
            off = P[m] - (self.o + np.outer(sc, self.d0))
            out[m] = Ci + np.einsum("nij,nj->ni", Ri, off)
        return out

    def arc_stretch_min(self, P, s, eps=0.01, where=False):
        """Fold check: min |d p'/ds| over points (1 = rigid, -> 0 = pinched, a fold makes the
        mapped strand reverse; measured as the ratio of mapped to rest step along s).
        where=True -> (min, s at the min, fraction of points below 0.15)."""
        m = (np.asarray(s) > eps) & (np.asarray(s) < self.L - eps)
        if not m.any():
            return (1.0, None, 0.0) if where else 1.0
        Pm, sm = np.asarray(P, float)[m], np.asarray(s, float)[m]
        P0 = self.deform(Pm - self.d0 * eps, sm - eps)
        P1 = self.deform(Pm + self.d0 * eps, sm + eps)
        T = np.einsum("nij,j->ni", self.R[self._idx(sm)], self.d0)
        f = np.einsum("ni,ni->n", P1 - P0, T) / (2 * eps)
        if where:
            return float(f.min()), float(sm[int(np.argmin(f))]), float((f < 0.15).mean())
        return float(f.min())

    def bones(self, n):
        """n bones evenly in arc length: [(head, tail, s_head, s_tail)]."""
        ks = np.linspace(0.0, self.L, n + 1)
        H = [self.frame(k)[0] for k in ks]
        return [(H[k], H[k + 1], ks[k], ks[k + 1]) for k in range(n)]


def vine_weights(s, L, n, parent_s0=0.0):
    """Arc-length hat weights -> (len(s), n + 1): column 0 = the parent bone, 1..n = chain.
    Bone k's midpoint m_k = (k + .5) L / n; a vertex between m_k and m_k+1 blends those two
    linearly; below m_0 it blends parent -> bone 0 from s = parent_s0; above m_n-1 bone n-1."""
    s = np.asarray(s, float)
    W = np.zeros((len(s), n + 1))
    m = (np.arange(n) + 0.5) * L / n
    lo = s <= m[0]
    t = np.clip((s[lo] - parent_s0) / max(m[0] - parent_s0, 1e-9), 0, 1)
    t = t * t * (3 - 2 * t)
    W[lo, 0] = 1 - t; W[lo, 1] = t
    hi = s >= m[-1]
    W[hi, n] = 1.0
    mid = ~lo & ~hi
    k = np.clip(np.searchsorted(m, s[mid]) - 1, 0, n - 2)
    u = (s[mid] - m[k]) / (m[k + 1] - m[k])
    idx = np.nonzero(mid)[0]
    W[idx, k + 1] = 1 - u; W[idx, k + 2] = u
    return W


def vine_wave(t, k, n, amp_root, amp_tip, lag, harmonic=1, phase=0.0):
    """Angle (deg) of chain bone k (0 = root) at loop phase t in [0,1): a wave travelling
    root -> tip (phase lag 'lag' rad per bone), amplitude root -> tip linear. Integer
    'harmonic' keeps t=0 and t=1 identical (seam-closed)."""
    a = amp_root + (amp_tip - amp_root) * (k / max(n - 1, 1))
    return a * math.sin(2 * math.pi * harmonic * t + phase - k * lag)


def action_fcurves(act):
    """Every fcurve of a (Blender 5.0 slotted) action, across layers/strips/channelbags."""
    out = []
    if hasattr(act, "layers"):
        for layer in act.layers:
            for strip in layer.strips:
                for cb in getattr(strip, "channelbags", []):
                    out.extend(cb.fcurves)
    if not out and hasattr(act, "fcurves"):
        out = list(act.fcurves)
    return out


def assign_action(idb, action):
    """Assign an action, picking a slot for this ID type when Blender wants one."""
    if idb.animation_data is None:
        idb.animation_data_create()
    ad = idb.animation_data
    ad.action = action
    if action is None or not hasattr(ad, "action_slot"):
        return
    if ad.action_slot is not None:
        return
    for slot in list(getattr(ad, "action_suitable_slots", ())) or list(action.slots):
        try:
            ad.action_slot = slot
            return
        except (TypeError, ValueError, RuntimeError):
            continue
