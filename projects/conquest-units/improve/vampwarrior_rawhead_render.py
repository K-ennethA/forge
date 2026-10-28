"""vampwarrior_rawhead_render.py -- <spec.json> : renders of head meshes in the f-frame (front -y, up +z, glb units), same
cameras / lights as head_graft/head7/hybrid/scripts/render.py (ortho, sun key 3.0 + fill 1.0, grey world 0.6).
spec: {"out": dir, "res": 768, "items": [{"name", "npz" (V, F, FUV glTF v-down), "tex"?, "nrm"?, "parts"?: [same]}],
       "shots": [{"tag", "angle", "mode": clay|flat|tex|holes; item keys tex / nrm / mr (glTF metallic-roughness png, G = roughness), "frame": head|face|eye|eyeR|mouth|neck, "elev"?}]}
mode holes: emission white on front faces, magenta on back faces (a hole shows magenta), needs outward winding."""
import sys, os, json, math, numpy as np, bpy
from mathutils import Vector, Euler
SP = json.load(open(sys.argv[sys.argv.index("--") + 1]))
os.makedirs(SP["out"], exist_ok=True)
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
sc = bpy.context.scene
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.type = "ORTHO"
res = int(SP.get("res", 768)); sc.render.resolution_x = sc.render.resolution_y = res
FR = {"head": ((0.0, 0.0, 0.0), 1.02), "face": ((0.0, -0.15, -0.02), 0.52), "eye": ((0.12, -0.17, 0.09), 0.22),
      "eyeR": ((-0.12, -0.17, 0.09), 0.22), "mouth": ((0.0, -0.25, -0.13), 0.24), "neck": ((0.0, 0.0, -0.36), 0.40)}
FR.update({k: (tuple(v[0]), v[1]) for k, v in SP.get("frames", {}).items()})
ld = bpy.data.lights.new("key", "SUN"); ld.energy = 3.0; L1 = bpy.data.objects.new("key", ld); sc.collection.objects.link(L1)
L1.rotation_euler = Euler((math.radians(50), 0, math.radians(-30)))
lf = bpy.data.lights.new("fill", "SUN"); lf.energy = 1.0; L2 = bpy.data.objects.new("fill", lf); sc.collection.objects.link(L2)
L2.rotation_euler = Euler((math.radians(70), 0, math.radians(140)))
sc.world = bpy.data.worlds.new("w"); sc.world.use_nodes = True
bg = sc.world.node_tree.nodes["Background"]; bg.inputs[0].default_value = (0.35, 0.35, 0.37, 1); bg.inputs[1].default_value = 0.6


def mk(name, Z, tex=None, nrm=None, mr=None):
    me = bpy.data.meshes.new(name)
    V = np.asarray(Z["V"], np.float64); F = np.asarray(Z["F"], np.int64)
    me.vertices.add(len(V)); me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(F.size); me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
    me.polygons.add(len(F)); me.polygons.foreach_set("loop_start", (np.arange(len(F)) * 3).astype(np.int32))
    me.polygons.foreach_set("loop_total", np.full(len(F), 3, np.int32))
    me.update(); me.validate()
    if "FUV" in Z.files:
        uvl = me.uv_layers.new(name="UV"); uv = np.asarray(Z["FUV"]).reshape(-1, 2).copy(); uv[:, 1] = 1.0 - uv[:, 1]
        uvl.data.foreach_set("uv", uv.astype(np.float32).ravel())
    ob = bpy.data.objects.new(name, me); sc.collection.objects.link(ob)
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool)); me.update()
    mat = bpy.data.materials.new(name + "_m"); mat.use_nodes = True
    nt = mat.node_tree; pb = nt.nodes["Principled BSDF"]; pb.inputs["Roughness"].default_value = 0.6
    if tex:
        ti = nt.nodes.new("ShaderNodeTexImage"); ti.image = bpy.data.images.load(tex); nt.links.new(ti.outputs[0], pb.inputs["Base Color"])
    if mr:
        tm = nt.nodes.new("ShaderNodeTexImage"); tm.image = bpy.data.images.load(mr); tm.image.colorspace_settings.name = "Non-Color"
        sp = nt.nodes.new("ShaderNodeSeparateColor"); nt.links.new(tm.outputs[0], sp.inputs[0]); nt.links.new(sp.outputs["Green"], pb.inputs["Roughness"])
    if nrm:
        tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = bpy.data.images.load(nrm); tn.image.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap"); nm.uv_map = "UV"
        nt.links.new(tn.outputs[0], nm.inputs["Color"]); nt.links.new(nm.outputs[0], pb.inputs["Normal"])
    me.materials.append(mat)
    # holes material (second slot, switched in per shot)
    mh = bpy.data.materials.new(name + "_h"); mh.use_nodes = True; nh = mh.node_tree
    for n in list(nh.nodes):
        nh.nodes.remove(n)
    geo = nh.nodes.new("ShaderNodeNewGeometry"); mix = nh.nodes.new("ShaderNodeMixShader")
    e1 = nh.nodes.new("ShaderNodeEmission"); e1.inputs[0].default_value = (0.8, 0.8, 0.8, 1)
    e2 = nh.nodes.new("ShaderNodeEmission"); e2.inputs[0].default_value = (1, 0, 1, 1); e2.inputs[1].default_value = 3.0
    out = nh.nodes.new("ShaderNodeOutputMaterial")
    nh.links.new(geo.outputs["Backfacing"], mix.inputs[0]); nh.links.new(e1.outputs[0], mix.inputs[1]); nh.links.new(e2.outputs[0], mix.inputs[2])
    nh.links.new(mix.outputs[0], out.inputs["Surface"])
    ob["mats"] = [mat.name, mh.name]
    return ob


GROUPS = []
for it in SP["items"]:
    if "glb" in it:          # the glb as imported (its own vertex normals / UVs), material replaced by the standard one
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=it["glb"])
        gobs = [o for o in bpy.data.objects if o not in before]
        for o in gobs:
            if o.type != "MESH":
                o.hide_render = True
        g = [o for o in gobs if o.type == "MESH"][0]
        g.parent = None; g.matrix_world = __import__("mathutils").Matrix.Identity(4); g.name = it["name"]
        mat = bpy.data.materials.new(it["name"] + "_m"); mat.use_nodes = True
        nt = mat.node_tree; pb = nt.nodes["Principled BSDF"]; pb.inputs["Roughness"].default_value = 0.6
        ti = nt.nodes.new("ShaderNodeTexImage"); ti.image = bpy.data.images.load(it["tex"]); nt.links.new(ti.outputs[0], pb.inputs["Base Color"])
        uvn = nt.nodes.new("ShaderNodeUVMap"); uvn.uv_map = g.data.uv_layers[0].name; nt.links.new(uvn.outputs[0], ti.inputs[0])
        g.data.materials.clear(); g.data.materials.append(mat)
        mh = bpy.data.materials.new(it["name"] + "_h")
        g["mats"] = [mat.name, mh.name]; g["keep_normals"] = 1
        GROUPS.append((it, [g])); continue
    obs = [mk(it["name"], np.load(it["npz"]), it.get("tex"), it.get("nrm"), it.get("mr"))]
    for j, p in enumerate(it.get("parts", [])):
        obs.append(mk(it["name"] + "_p%d" % j, np.load(p["npz"]), p.get("tex"), p.get("nrm")))
    GROUPS.append((it, obs))

for it, obs in GROUPS:
    for it2, obs2 in GROUPS:
        for o in obs2:
            o.hide_render = it2 is not it
    for sh in SP["shots"]:
        c, scale = FR[sh.get("frame", "head")]
        a = math.radians(sh["angle"]); el = math.radians(sh.get("elev", 0.0))
        d = Vector((math.sin(a) * math.cos(el), -math.cos(a) * math.cos(el), math.sin(el)))
        cam.location = Vector(c) + 3.0 * d
        cam.rotation_euler = (math.radians(90) - el, 0, a)
        cam.data.ortho_scale = scale
        mode = sh["mode"]
        for o in obs:
            o.data.materials.clear(); o.data.materials.append(bpy.data.materials[o["mats"][1 if mode == "holes" else 0]])
            if not o.get("keep_normals"):
                o.data.polygons.foreach_set("use_smooth", np.full(len(o.data.polygons), mode != "flat", bool)); o.data.update()
        if mode in ("clay", "flat"):
            sc.render.engine = "BLENDER_WORKBENCH"
            s_ = sc.display.shading; s_.light = "STUDIO"; s_.color_type = "SINGLE"; s_.single_color = (0.75, 0.75, 0.75)
            s_.show_cavity = False; s_.show_backface_culling = False; s_.background_type = "VIEWPORT"; s_.background_color = (0.08, 0.08, 0.09)
        else:
            sc.render.engine = "BLENDER_EEVEE"
        sc.render.filepath = os.path.join(SP["out"], "%s_%s.png" % (it["name"], sh["tag"]))
        bpy.ops.render.render(write_still=True)
        print("RENDERED", sc.render.filepath, flush=True)
