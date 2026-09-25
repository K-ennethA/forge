"""Facing landmarks for ambiguous sculpts (read only, never saves).

    blender --background <copy.blend> --factory-startup --python newunit-landmarks.py -- <out.json>

Over all viewport+render-visible meshes (evaluated, world): the crown (top 4% of height) centroid and
its direction from the unit's plan centre; the ground-contact set (lowest 4%) centroid; and the
principal horizontal axis (PCA of XY) with, for each end (outer 15% along it), the vertex count, mean
height and Laplacian roughness -- a spiky / carved end vs a smooth rounded end. Angles from -Y
(0 = -Y, +90 = +X, 180 = +Y).
"""
import bpy, sys, json, math
import numpy as np

OUT = sys.argv[sys.argv.index("--") + 1]
vl = set(o.name for o in bpy.context.view_layer.objects)
dg = bpy.context.evaluated_depsgraph_get()
Ws, Es, off = [], [], 0
for o in bpy.data.objects:
    if o.type != "MESH" or o.name not in vl or o.hide_render or not o.visible_get():
        continue
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    c = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", c); c = c.reshape(-1, 3)
    M = np.array(o.matrix_world); Ws.append(c @ M[:3, :3].T + M[:3, 3])
    e = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", e); Es.append(e.reshape(-1, 2) + off)
    off += len(c); ev.to_mesh_clear()
W = np.vstack(Ws); E = np.vstack(Es); n = len(W)
deg = np.bincount(E.ravel(), minlength=n).astype(float)
ns = np.zeros((n, 3)); np.add.at(ns, E[:, 0], W[E[:, 1]]); np.add.at(ns, E[:, 1], W[E[:, 0]])
lap = np.linalg.norm(W - ns / np.maximum(deg, 1)[:, None], axis=1)
lo, hi = W.min(0), W.max(0); H = hi[2] - lo[2]
pc = (lo[:2] + hi[:2]) / 2


def ang(d):
    return round(math.degrees(math.atan2(d[0], -d[1])), 1)


crown = W[W[:, 2] >= hi[2] - 0.04 * H]; base = W[W[:, 2] <= lo[2] + 0.04 * H]
rep = {"plan_centre": pc.round(3).tolist(), "height": round(float(H), 3),
       "crown": {"n": len(crown), "centroid": crown.mean(0).round(3).tolist(), "dir_deg": ang(crown.mean(0)[:2] - pc),
                 "offset": round(float(np.linalg.norm(crown.mean(0)[:2] - pc)), 3)},
       "ground": {"n": len(base), "centroid": base.mean(0).round(3).tolist(), "dir_deg": ang(base.mean(0)[:2] - pc)}}
xy = W[:, :2] - W[:, :2].mean(0)
w, v = np.linalg.eigh(np.cov(xy.T)); ax = v[:, -1]
t = xy @ ax; tl, th = np.percentile(t, 15), np.percentile(t, 85)
ends = {}
for tag, m in (("end_pos", t >= th), ("end_neg", t <= tl)):
    P = W[m]; d = P[:, :2].mean(0) - pc
    ends[tag] = {"n": int(m.sum()), "dir_deg": ang(d), "mean_z": round(float(P[:, 2].mean()), 3),
                 "lap_mean": round(float(lap[m].mean()), 5), "lap_p95": round(float(np.percentile(lap[m], 95)), 5)}
rep["principal_axis_xy"] = ax.round(3).tolist(); rep["axis_ratio"] = round(float(math.sqrt(w[-1] / max(w[0], 1e-9))), 2)
rep["ends"] = ends
json.dump(rep, open(OUT, "w"), indent=1)
print("LANDMARKS", json.dumps(rep))
