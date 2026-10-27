"""hyb_export_npz.py -- <blend> <out.npz> [key] : the assembled hybrid (all visible parts, rest or one shape key) as one
triangle mesh + per-face part id (0 head skin, 1 eyes, 2 fangs, 3 teeth/tongue, 4 mouth interior / pocket)"""
import sys, numpy as np, bpy
a = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.open_mainfile(filepath=a[0])
key = a[2] if len(a) > 2 else None
V, F, P = [], [], []; off = 0
dg = bpy.context.evaluated_depsgraph_get()
for o in bpy.data.objects:
    if o.type != "MESH":
        continue
    if o.data.shape_keys:
        for kb in o.data.shape_keys.key_blocks:
            kb.value = 1.0 if kb.name == key else 0.0
dg.update()
for o in bpy.data.objects:
    if o.type != "MESH":
        continue
    oe = o.evaluated_get(dg); me = oe.to_mesh(); me.calc_loop_triangles()
    Vo = np.array([o.matrix_world @ v.co for v in me.vertices])
    T = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("vertices", T); T = T.reshape(-1, 3)
    mi = np.empty(len(me.loop_triangles), np.int64); me.loop_triangles.foreach_get("material_index", mi)
    if o.name == "head":
        pid = np.where(mi == 0, 0, 4)
    elif o.name.startswith("eye"):
        pid = np.full(len(T), 1)
    elif o.name.startswith("fang"):
        pid = np.full(len(T), 2)
    else:
        pid = np.full(len(T), 3)
    V.append(Vo); F.append(T + off); P.append(pid); off += len(Vo)
    oe.to_mesh_clear()
np.savez_compressed(a[1], V=np.vstack(V), F=np.vstack(F), part=np.concatenate(P))
print("EXPORT", off, len(np.vstack(F)))
