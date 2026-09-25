"""Facing audit: direction-free landmark measurements on a source copy (read only, never saves).

    blender --background <copy.blend> --factory-startup --python facing_audit.py -- <out.json>

Prints/records, in world space (authored object scale applied):
  * bbox, vertex centroid
  * height-band centroid offsets (where the mass leans in X/Y per 10% band)
  * low band (feet/legs) centroid, top band centroid
  * "feature" centroid of the top 30%: vertices weighted by Laplacian magnitude
    (|v - mean(neighbours)|, a curvature proxy) -- carved faces, eye hollows and
    mouths concentrate curvature on the side they are carved into.
  * extreme points per horizontal direction for the upper band
No assumption about which way is front goes into any number.
"""
import bpy, sys, json, math
import numpy as np

out = sys.argv[sys.argv.index("--") + 1:][0]
obs = [o for o in bpy.data.objects if o.type == "MESH"]
ob = max(obs, key=lambda o: len(o.data.vertices))
me = ob.data
n = len(me.vertices)
co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
M = np.array(ob.matrix_world)
W = co @ M[:3, :3].T + M[:3, 3]
ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev); ev = ev.reshape(-1, 2)
deg = np.bincount(ev.ravel(), minlength=n).astype(float)
nsum = np.zeros((n, 3))
np.add.at(nsum, ev[:, 0], W[ev[:, 1]]); np.add.at(nsum, ev[:, 1], W[ev[:, 0]])
lap = np.linalg.norm(W - nsum / np.maximum(deg, 1)[:, None], axis=1)

lo, hi = W.min(0), W.max(0)
H = hi[2] - lo[2]
ctr_xy = (lo[:2] + hi[:2]) / 2
rep = {"object": ob.name, "matrix_scale": [round(v, 3) for v in ob.scale],
       "bbox_min": lo.round(3).tolist(), "bbox_max": hi.round(3).tolist(),
       "centroid": W.mean(0).round(3).tolist(), "bbox_centre_xy": ctr_xy.round(3).tolist()}
bands = []
for i in range(10):
    z0, z1 = lo[2] + H * i / 10, lo[2] + H * (i + 1) / 10
    m = (W[:, 2] >= z0) & (W[:, 2] < z1)
    if m.sum():
        c = W[m].mean(0)
        bands.append({"band": i, "n": int(m.sum()), "cx": round(c[0] - ctr_xy[0], 3), "cy": round(c[1] - ctr_xy[1], 3),
                      "ymin": round(W[m, 1].min(), 3), "ymax": round(W[m, 1].max(), 3),
                      "xmin": round(W[m, 0].min(), 3), "xmax": round(W[m, 0].max(), 3)})
rep["bands"] = bands
top = W[:, 2] > lo[2] + 0.7 * H
wt = lap[top] ** 2
tc = W[top].mean(0)
fc = (W[top] * wt[:, None]).sum(0) / wt.sum()
rep["top30_centroid"] = tc.round(3).tolist()
rep["top30_feature_centroid_offset_xy"] = (fc[:2] - tc[:2]).round(4).tolist()
rep["top30_feature_offset_angle_deg_from_minusY"] = round(math.degrees(math.atan2(fc[0] - tc[0], -(fc[1] - tc[1]))), 1)
# per-side curvature energy in the top 30%: which half (by y and by x) carries the carved detail
yt, xt = W[top, 1] - tc[1], W[top, 0] - tc[0]
rep["top30_lap_energy"] = {"minusY": round(float(wt[yt < 0].sum()), 4), "plusY": round(float(wt[yt > 0].sum()), 4),
                           "minusX": round(float(wt[xt < 0].sum()), 4), "plusX": round(float(wt[xt > 0].sum()), 4)}
low = W[:, 2] < lo[2] + 0.12 * H
rep["low12_centroid_offset_xy"] = (W[low].mean(0)[:2] - ctr_xy).round(3).tolist()
rep["low12_n"] = int(low.sum())
# highest point and its horizontal offset from the bbox centre
ti = int(np.argmax(W[:, 2]))
rep["apex"] = W[ti].round(3).tolist()
print("AUDIT", json.dumps(rep))
json.dump(rep, open(out, "w"), indent=1)
