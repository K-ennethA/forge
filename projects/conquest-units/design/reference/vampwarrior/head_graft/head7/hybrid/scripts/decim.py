"""decim.py -- <in.npz> <out.npz> <target_tris> [keep_uv 0|1] : Blender COLLAPSE (quadric) to target tris (triangulated)"""
import sys, os, numpy as np, bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); tgt = int(a[2]); kuv = len(a) > 3 and a[3] == "1"
U.clear()
ob = U.obj("m", Z["V"], Z["F"])
if kuv and "FUV" in Z.files:
    uvl = ob.data.uv_layers.new(name="UV"); uv = Z["FUV"].reshape(-1, 2).copy(); uvl.data.foreach_set("uv", uv.astype(np.float32).ravel())
bpy.context.view_layer.objects.active = ob
m = ob.modifiers.new("d", "DECIMATE"); m.decimate_type = "COLLAPSE"; m.ratio = tgt / len(Z["F"]); m.use_collapse_triangulate = True
bpy.ops.object.modifier_apply(modifier="d")
me = ob.data; me.calc_loop_triangles()
V, T = U.arrays(ob)
out = {"V": V, "F": T}
if kuv and "FUV" in Z.files:
    L = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("loops", L)
    UV = np.empty(len(me.loops) * 2); me.uv_layers[0].data.foreach_get("uv", UV); out["FUV"] = UV.reshape(-1, 2)[L.reshape(-1, 3)]
np.savez_compressed(a[1], **out)
print("DECIM", len(Z["F"]), "->", len(T))
