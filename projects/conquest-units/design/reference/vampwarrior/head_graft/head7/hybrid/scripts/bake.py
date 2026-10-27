"""bake.py -- <src.npz> <src_tex.png> <low.npz> <out_prefix> <res> <uvmode: keep|smart|pack> [cage_u]
Bakes a tangent NORMAL (low smooth-shaded = smooth reference) + DIFFUSE colour from the source (eyeball shells dropped) onto
the low with a per-part cage = clip(2.5 x p99 low->src, 0.004, 0.03) unless given. Writes <out>_normal.png, <out>_albedo.png,
<out>_low.npz (V, F, FUV glTF convention) and <out>_bake.json"""
import sys, os, json, numpy as np, bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
Zs = np.load(a[0]); texp = a[1]; Zl = np.load(a[2]); out = a[3]; res = int(a[4]); uvmode = a[5]
U.clear()
sc = bpy.context.scene; sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 1
F = Zs["F"][Zs["flab"] == 0]; FUV = Zs["FUV"][Zs["flab"] == 0]
src = U.obj("src", Zs["V"], F)
uvl = src.data.uv_layers.new(name="UV"); uv = FUV.reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]; uvl.data.foreach_set("uv", uv.astype(np.float32).ravel())
ms = bpy.data.materials.new("src_m"); ms.use_nodes = True; nt = ms.node_tree
ti = nt.nodes.new("ShaderNodeTexImage"); ti.image = bpy.data.images.load(texp); nt.links.new(ti.outputs[0], nt.nodes["Principled BSDF"].inputs["Base Color"])
src.data.materials.append(ms)
src.data.polygons.foreach_set("use_smooth", np.ones(len(src.data.polygons), bool))
low = U.obj("low", Zl["V"], Zl["F"])
low.data.polygons.foreach_set("use_smooth", np.ones(len(low.data.polygons), bool)); low.data.update()
lu = low.data.uv_layers.new(name="UV")
bpy.context.view_layer.objects.active = low
for o in bpy.data.objects: o.select_set(o is low)
if uvmode == "keep":
    uv = Zl["FUV"].reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]; lu.data.foreach_set("uv", uv.astype(np.float32).ravel())
elif uvmode == "pack":
    uv = Zl["FUV"].reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]; lu.data.foreach_set("uv", uv.astype(np.float32).ravel())
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(margin=0.004, rotate=False); bpy.ops.object.mode_set(mode="OBJECT")
else:
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.003); bpy.ops.object.mode_set(mode="OBJECT")
# cage from the low -> source distance
tS = U.bvh(Zs["V"], F)
d = U.dists(tS, Zl["V"])
cage = float(a[6]) if len(a) > 6 else float(np.clip(2.5 * np.percentile(d, 99), 0.004, 0.03))
ml = bpy.data.materials.new("low_m"); ml.use_nodes = True; nl = ml.node_tree
low.data.materials.append(ml)
rep = {"cage_u": round(cage, 5), "low_to_src_p50_p99": [round(float(np.percentile(d, 50)), 5), round(float(np.percentile(d, 99)), 5)],
       "uvmode": uvmode, "res": res, "tris": int(len(Zl["F"]))}
for typ, name in (("NORMAL", "normal"), ("DIFFUSE", "albedo")):
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=False)
    if typ == "NORMAL":
        img.colorspace_settings.name = "Non-Color"
    n = nl.nodes.new("ShaderNodeTexImage"); n.image = img
    for x in nl.nodes: x.select = False
    n.select = True; nl.nodes.active = n
    for o in bpy.data.objects: o.select_set(o in (low, src))
    bpy.context.view_layer.objects.active = low
    kw = dict(type=typ, use_selected_to_active=True, cage_extrusion=cage, max_ray_distance=2.0 * cage, margin=8, use_clear=True)
    if typ == "DIFFUSE":
        sc.render.bake.use_pass_direct = False; sc.render.bake.use_pass_indirect = False; sc.render.bake.use_pass_color = True
    r = bpy.ops.object.bake(**kw)
    img.filepath_raw = out + "_%s.png" % name; img.file_format = "PNG"; img.save()
    px = np.array(img.pixels[:]).reshape(res, res, 4)
    if typ == "NORMAL":
        rep["normal_detail_frac_dev_gt_0.05"] = round(float((np.linalg.norm(px[..., :3] - [0.5, 0.5, 1.0], axis=-1) > 0.05).mean()), 4)
    rep[typ] = list(r)
me = low.data; me.calc_loop_triangles()
L = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("loops", L)
lu = me.uv_layers["UV"]; UVo = np.empty(len(me.loops) * 2); lu.data.foreach_get("uv", UVo); UVo = UVo.reshape(-1, 2)[L.reshape(-1, 3)]; UVo[..., 1] = 1 - UVo[..., 1]
T = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("vertices", T)
np.savez_compressed(out + "_low.npz", V=Zl["V"], F=T.reshape(-1, 3), FUV=UVo)
json.dump(rep, open(out + "_bake.json", "w"), indent=1)
print("BAKE", json.dumps(rep))
