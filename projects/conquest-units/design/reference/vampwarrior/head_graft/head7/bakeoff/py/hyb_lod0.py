"""hyb_lod0.py -- <bakeoff dir> <fit.npz> <build_dir> : the showcase LOD0 head = the rigged LOD1 head subdivided once
(Catmull-Clark, linear UVs so the atlas stays valid), open skin re-projected onto the source (lid / lip skirts, nostrils,
mouth bag and eye pockets keep the subdivided shape), every shape key carried through the same subdivision as a delta,
weights interpolated by the subdivision; the normal map re-baked for LOD0 (same source prep + cage as LOD1).
Writes <build_dir>/hyb_rigged_lod0.blend, hybrid_head_lod0.glb, hybrid_normal_lod0.png, lod0_report.json."""
import sys, os, json, numpy as np, bpy, bmesh
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

a = sys.argv[sys.argv.index("--") + 1:]
BD, FIT, OUT = a[0], a[1], a[2]
Z = np.load(FIT); Zs = np.load(os.path.join(BD, "s2", "edit.npz"))
bpy.ops.wm.open_mainfile(filepath=os.path.join(OUT, "hyb_rigged.blend"))
sc = bpy.context.scene
head = bpy.data.objects["head"]
arm = bpy.data.objects["head_rig"]
rep = {}
keys = [kb.name for kb in head.data.shape_keys.key_blocks]
V1 = np.array([v.co for v in head.data.vertices])
nv1 = len(V1)
# masks on LOD1 (same rules as the fit)
Fq1 = np.array([list(p.vertices) for p in head.data.polygons])
nbr = [set() for _ in range(nv1)]
for f in Fq1:
    for k in range(4):
        nbr[f[k]].add(f[(k + 1) % 4]); nbr[f[(k + 1) % 4]].add(f[k])
keepshape = np.zeros(nv1, bool)
for k in range(2):
    rk = Z["ring"][k]; keepshape |= (rk >= 0) & (rk <= 9)
fr = set(Z["lip_up_line"].tolist()) | set(Z["lip_lo_line"].tolist()); seen = set(fr)
for r in range(4):
    nf = set()
    for i in fr:
        for j in nbr[i]:
            if j not in seen:
                nf.add(j); seen.add(j)
    fr = nf
keepshape[list(seen)] = True
keepshape[Z["bag"]] = True
keepshape |= (np.abs(V1[:, 0]) < 0.075) & (V1[:, 2] > -0.07) & (V1[:, 2] < 0.04) & (V1[:, 1] < -0.24)
keepshape |= np.abs(V1[:, 0]) > 0.27                    # ears: keep (their fit is rough; projection crumples them)
# evaluate every key through a subdivision (armature off)
arm_mod = head.modifiers.get("Armature"); arm_mod.show_viewport = False
sub = head.modifiers.new("sub", "SUBSURF"); sub.levels = 1; sub.render_levels = 1; sub.uv_smooth = "NONE"
sub.boundary_smooth = "ALL"
dg = bpy.context.evaluated_depsgraph_get()
P = {}
for kn in keys:
    for kb in head.data.shape_keys.key_blocks:
        kb.value = 1.0 if (kb.name == kn and kn != "Basis") else 0.0
    dg.update()
    oe = head.evaluated_get(dg); me = oe.to_mesh()
    P[kn] = np.array([v.co for v in me.vertices]); oe.to_mesh_clear()
for kb in head.data.shape_keys.key_blocks:
    kb.value = 0.0
# the LOD0 object: copy, drop keys, apply the subdivision
h0 = head.copy(); h0.data = head.data.copy(); h0.name = "head_lod0"; sc.collection.objects.link(h0)
h0.shape_key_clear()
for m in list(h0.modifiers):
    if m.type == "ARMATURE":
        h0.modifiers.remove(m)
bpy.context.view_layer.objects.active = h0
for o in bpy.data.objects: o.select_set(o is h0)
bpy.ops.object.modifier_apply(modifier="sub")
head.modifiers.remove(sub); arm_mod.show_viewport = True
V0 = np.array([v.co for v in h0.data.vertices])
assert len(V0) == len(P["Basis"]), (len(V0), len(P["Basis"]))
rep["lod0_verts"] = len(V0); rep["lod0_quads"] = len(h0.data.polygons)
# re-project the open skin
kd = KDTree(nv1)
for i, p in enumerate(V1):
    kd.insert(p.tolist(), i)
kd.balance()
ks0 = np.array([keepshape[kd.find(p.tolist())[1]] for p in V0])
Fs = Zs["F"][Zs["flab"] == 0]
tS = BVHTree.FromPolygons(Zs["V"].tolist(), Fs.tolist())
Vr = V0.copy(); moved = 0
for i in np.nonzero(~ks0)[0]:
    h = tS.find_nearest(Vector(V0[i]), 0.02)
    if h[0] is not None:
        Vr[i] = np.array(h[0]); moved += 1
# a light relax on the projected verts' boundary band so the keep / project seam does not step
rep["reprojected_verts"] = moved
h0.data.vertices.foreach_set("co", Vr.astype(np.float32).ravel()); h0.data.update()
h0.shape_key_add(name="Basis")
for kn in keys:
    if kn == "Basis":
        continue
    kb = h0.shape_key_add(name=kn)
    kb.data.foreach_set("co", (Vr + (P[kn] - P["Basis"])).astype(np.float32).ravel()); kb.value = 0.0
m = h0.modifiers.new("Armature", "ARMATURE"); m.object = arm; h0.parent = arm
# ---------------------------------------------------------------- re-bake the normal map for LOD0
sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 1
ecov = np.load(os.path.join(BD, "s2", "edit_eyecover.npy"))
keep = Zs["flab"] == 0
keep &= ~ecov
FS = Zs["F"][keep]
me = bpy.data.meshes.new("src"); me.from_pydata(Zs["V"].tolist(), [], FS.tolist()); me.update()
src = bpy.data.objects.new("src", me); sc.collection.objects.link(src)
th_ = BVHTree.FromPolygons(Vr.tolist(), [list(p.vertices) for p in h0.data.polygons])
flip = []
for p_ in src.data.polygons:
    h = th_.find_nearest(p_.center)
    if h[0] is not None and p_.normal.dot(h[1]) < 0:
        flip.append(p_.index)
bm = bmesh.new(); bm.from_mesh(src.data); bm.faces.ensure_lookup_table()
bmesh.ops.reverse_faces(bm, faces=[bm.faces[i_] for i_ in flip]); bm.to_mesh(src.data); bm.free()
src.data.polygons.foreach_set("use_smooth", np.ones(len(FS), bool))
h0.data.polygons.foreach_set("use_smooth", np.ones(len(h0.data.polygons), bool))
skin = h0.data.materials[0].copy(); skin.name = "skin_lod0"; h0.data.materials[0] = skin
nt = skin.node_tree
nimg = bpy.data.images.new("hybrid_normal_lod0", 2048, 2048, alpha=False); nimg.colorspace_settings.name = "Non-Color"
for n_ in nt.nodes:
    if n_.type == "TEX_IMAGE" and n_.image and "normal" in n_.image.name:
        n_.image = nimg
    n_.select = False
target = [n_ for n_ in nt.nodes if n_.type == "TEX_IMAGE" and n_.image is nimg][0]
target.select = True; nt.nodes.active = target
for o in bpy.data.objects: o.select_set(o in (h0, src))
bpy.context.view_layer.objects.active = h0
# bake on the rest shape without the armature
h0.modifiers["Armature"].show_render = False; h0.modifiers["Armature"].show_viewport = False
r = bpy.ops.object.bake(type="NORMAL", use_selected_to_active=True, cage_extrusion=0.008, max_ray_distance=0.02, margin=12,
                        use_clear=True)
h0.modifiers["Armature"].show_render = True; h0.modifiers["Armature"].show_viewport = True
RES = 2048
px_n = np.array(nimg.pixels[:]).reshape(RES, RES, 4); nn = px_n[..., :3] * 2 - 1
xy = nn[..., :2]; L_ = np.linalg.norm(xy, axis=-1); cap = 0.38
xy = xy * np.where(L_ > cap, cap / np.maximum(L_, 1e-6), 1.0)[..., None]; z_ = np.sqrt(np.clip(1 - (xy ** 2).sum(-1), 0, 1))
valid = px_n[..., :3].sum(-1) > 0.05
px_n[..., :3] = np.where(valid[..., None], np.concatenate([xy, z_[..., None]], -1), np.array([0.0, 0.0, 1.0])) * 0.5 + 0.5
nimg.pixels.foreach_set(px_n.astype(np.float32).ravel())
nimg.filepath_raw = os.path.join(OUT, "hybrid_normal_lod0.png"); nimg.file_format = "PNG"; nimg.save()
bpy.data.objects.remove(src, do_unlink=True)
rep["bake"] = list(r)
# LOD0 scene: hide / remove the LOD1 head for the export
lod1_name = head.name
bpy.data.objects.remove(head, do_unlink=True)
h0.name = "head"
for o in bpy.data.objects:
    o.select_set(o.type in ("MESH", "ARMATURE"))
bpy.context.view_layer.objects.active = arm
bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, "hybrid_head_lod0.glb"), export_format="GLB", use_selection=True,
                          export_yup=True, export_apply=False, export_morph=True, export_skins=True, export_animations=False,
                          export_tangents=True, export_materials="EXPORT")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "hyb_rigged_lod0.blend"))
rep["tris"] = {o.name: int(sum(len(p.vertices) - 2 for p in o.data.polygons)) for o in bpy.data.objects if o.type == "MESH"}
rep["tris_total"] = int(sum(rep["tris"].values()))
json.dump(rep, open(os.path.join(OUT, "lod0_report.json"), "w"), indent=1)
print("LOD0", json.dumps(rep))
