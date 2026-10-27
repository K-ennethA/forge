"""extract_low.py -- <out.npz> [object] : the rigged blend's body mesh (rest) -> head crop in the f-frame, with per-corner UVs
(glTF v-down convention, like dump_glb) + Vbody"""
import sys, os, json, numpy as np, bpy
a = sys.argv[sys.argv.index("--") + 1:]
ob = bpy.data.objects[a[1] if len(a) > 1 and a[1] else "vampwarrior"]
me = ob.data; me.calc_loop_triangles()
V = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
T = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("vertices", T); T = T.reshape(-1, 3)
L = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("loops", L); L = L.reshape(-1, 3)
uvl = me.uv_layers.active; UV = np.empty(len(me.loops) * 2); uvl.data.foreach_get("uv", UV); UV = UV.reshape(-1, 2)
FUV = UV[L]; FUV[..., 1] = 1.0 - FUV[..., 1]
sm = np.empty(len(me.loop_triangles), bool); me.loop_triangles.foreach_get("use_smooth", sm) if hasattr(me.loop_triangles[0], "use_smooth") else None
SH = np.array(json.loads(a[2]) if len(a) > 2 else [0, 0, 0]); V = V + SH
C = V[T].mean(1)
keep = (C[:, 2] > 7.13) & (np.abs(C[:, 0]) < 0.47) & (C[:, 1] > -0.75) & (C[:, 1] < 0.03) & (C[:, 2] < 8.06)
T = T[keep]; FUV = FUV[keep]
used = np.unique(T); rm = -np.ones(len(V), np.int64); rm[used] = np.arange(len(used))
s, mz, ty, tz = 1.07, 0.0035, -0.345, 7.515
Vb = V[used]; Vf = (Vb - np.array([-s * mz, ty, tz])) / s
np.savez_compressed(a[0], V=Vf, F=rm[T], FUV=FUV, Vbody=Vb)
print("EXTRACT tris", len(T), "of", len(me.loop_triangles))
