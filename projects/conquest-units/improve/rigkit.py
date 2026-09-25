"""Shared helpers for the Conquest unit rig + clip wave (rig_unit.py, the contract checker,
render_animated.py). Pure measurement / math; nothing here saves a file.

MeshData      world-space verts, edges, adjacency, islands of the unit mesh.
trace_limb    deterministic limb skeleton from the mesh: start at a contact tip, grow slab by
              slab along an axis ('z' upward, or 'r' radially inward) through mesh-connected
              vertices, stop when the slab set jumps in size (the limb has merged into the body).
              (The probe that produced the landmark numbers in rig_unit.py's SPEC table.)
seg_dist      point -> segment / polyline distances (vectorised).
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
