"""Where the SHIPPED necromancer.glb spent its triangles (the 'before' of Mortis's budget redistribution).
Read-only: opens the source copy, imports the shipped glb in memory, never saves.

    blender --background source-copies/hero-necromancer.blend --factory-startup --python mortis_glb_audit.py -- <glb> <out.json>

The glb is the source sculpt decimated, recentred and scaled to 1.8 m. Its verts are mapped back into the sculpt
frame (bbox fit, yaw 0 or 180 -- whichever lands closer to the sculpt), and every glb triangle takes the part of
its nearest sculpt vertex: slab / robe / head / book / hands (the twenty Sphere.003-.022 arms).
"""
import bpy, sys, json, math
import numpy as np
from mathutils.kdtree import KDTree

argv = sys.argv[sys.argv.index("--") + 1:]
GLB, OUT = argv[0], argv[1]
PART = {"Sphere": "slab", "Sphere.001": "robe", "Sphere.002": "head", "Sphere.023": "book"}
P, L = [], []
for o in bpy.data.objects:
    if o.type != "MESH":
        continue
    co = np.empty(len(o.data.vertices) * 3); o.data.vertices.foreach_get("co", co)
    M = np.array(o.matrix_world)
    P.append(co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3])
    L += [PART.get(o.name, "hands")] * len(o.data.vertices)
P = np.vstack(P); L = np.array(L)
src_lo, src_hi = P.min(0), P.max(0)
kd = KDTree(len(P))
for i, p in enumerate(P):
    kd.insert(p, i)
kd.balance()
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=GLB)
g = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
dg = bpy.context.evaluated_depsgraph_get()
V, T = [], []
off = 0
for o in g:
    me = o.evaluated_get(dg).to_mesh()
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
    M = np.array(o.matrix_world)
    V.append(co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3])
    t = np.empty(len(me.loop_triangles) * 3, dtype=np.int64); me.loop_triangles.foreach_get("vertices", t)
    T.append(t.reshape(-1, 3) + off); off += len(me.vertices)
V = np.vstack(V); T = np.vstack(T)
best = None
for yaw in (0.0, 180.0):
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    Vr = V @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T
    lo, hi = Vr.min(0), Vr.max(0)
    k = (src_hi[2] - src_lo[2]) / (hi[2] - lo[2])
    Vm = (Vr - (lo + hi) / 2 * np.array([1, 1, 0]) - np.array([0, 0, lo[2]])) * k + \
        np.array([(src_lo[0] + src_hi[0]) / 2, (src_lo[1] + src_hi[1]) / 2, src_lo[2]])
    d = np.array([kd.find(p)[2] for p in Vm[::7]])
    if best is None or d.mean() < best[1]:
        best = (yaw, float(d.mean()), Vm, k)
yaw, resid, Vm, k = best
C = Vm[T].mean(1)
lab = np.array([L[kd.find(p)[1]] for p in C])
tot = len(T)
res = {"glb": GLB, "tris": tot, "fit": {"yaw_deg": yaw, "scale": round(k, 4), "mean_nearest_dist_sculpt_units": round(resid, 4)},
       "tris_per_part": {n: int((lab == n).sum()) for n in ("slab", "robe", "head", "book", "hands")},
       "shares": {n: round(float((lab == n).mean()), 4) for n in ("slab", "robe", "head", "book", "hands")},
       "rule": "glb triangle centroid -> nearest source-sculpt vertex's part, after a bbox fit into the sculpt frame"}
json.dump(res, open(OUT, "w"), indent=1)
print("GLB_AUDIT", json.dumps(res))
import os
sys.stdout.flush(); os._exit(0)
