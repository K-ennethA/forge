"""render.py -- <spec.json> : workbench clay (flat / smooth) and EEVEE textured renders of head meshes (f-frame).
spec: {"out": dir, "res": 640, "items": [{"name", "npz" | "blend"+"object", "tex"?, "nrm"?, "smooth_all"?}],
       "shots": [{"tag", "angle", "mode": flat|smooth|tex|texflat, "frame": head|face}]}"""
import sys, os, json, math, numpy as np, bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
from mathutils import Vector, Euler
SP = json.load(open(sys.argv[sys.argv.index("--") + 1]))
os.makedirs(SP["out"], exist_ok=True)
U.clear()
sc = bpy.context.scene
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.type = "ORTHO"
res = int(SP.get("res", 640)); sc.render.resolution_x = sc.render.resolution_y = res
sc.render.film_transparent = False
FR = {"head": ((0.0, 0.0, 0.0), 1.02), "face": ((0.0, -0.15, -0.02), 0.52), "eye": ((0.12, -0.17, 0.09), 0.22),
      "mouth": ((0.0, -0.25, -0.13), 0.24)}
FR.update({k: (tuple(v[0]), v[1]) for k, v in SP.get("frames", {}).items()})
# lights for eevee
ld = bpy.data.lights.new("key", "SUN"); ld.energy = 3.0; L1 = bpy.data.objects.new("key", ld); sc.collection.objects.link(L1)
L1.rotation_euler = Euler((math.radians(50), 0, math.radians(-30)))
lf = bpy.data.lights.new("fill", "SUN"); lf.energy = 1.0; L2 = bpy.data.objects.new("fill", lf); sc.collection.objects.link(L2)
L2.rotation_euler = Euler((math.radians(70), 0, math.radians(140)))
sc.world = bpy.data.worlds.new("w"); sc.world.use_nodes = True
bg = sc.world.node_tree.nodes["Background"]; bg.inputs[0].default_value = (0.35, 0.35, 0.37, 1); bg.inputs[1].default_value = 0.6


class Multi:
    """several objects treated as one render item"""
    def __init__(self, obs):
        self.obs = obs
        self.data = obs[0].data

    @property
    def hide_render(self):
        return self.obs[0].hide_render

    @hide_render.setter
    def hide_render(self, v):
        for o in self.obs:
            o.hide_render = v


def make_item(it):
    if "blend_all" in it:
        with bpy.data.libraries.load(it["blend_all"]) as (src, dst):
            dst.objects = [n for n in src.objects if n not in ("src",)]
        obs = []
        for o in dst.objects:
            if o is None or o.type not in ("MESH",):
                continue
            sc.collection.objects.link(o); o.parent = None
            if o.data.shape_keys:
                for kb in o.data.shape_keys.key_blocks:
                    kb.value = 1.0 if kb.name == it.get("shape_key") else 0.0
                print("KEYS", it["name"], o.name, o.data.name, o.data.shape_keys.name, [(kb.name, kb.value) for kb in o.data.shape_keys.key_blocks if kb.value > 0])
            if it.get("no_normal"):
                for ms in o.material_slots:
                    if ms.material and ms.material.node_tree:
                        nt_ = ms.material.node_tree
                        for l_ in list(nt_.links):
                            if l_.to_socket.name == "Normal":
                                nt_.links.remove(l_)
            obs.append(o)
        it["_keep"] = True
        return Multi(obs)
    if "npz" in it:
        Z = np.load(it["npz"], allow_pickle=True)
        ob = U.obj(it["name"], Z["V"], Z["F"])
        if "FUV" in Z.files:
            uvl = ob.data.uv_layers.new(name="UV")
            uv = Z["FUV"].reshape(-1, 2).copy(); uv[:, 1] = 1.0 - uv[:, 1]
            uvl.data.foreach_set("uv", uv.astype(np.float32).ravel())
    else:
        with bpy.data.libraries.load(it["blend"]) as (src, dst):
            dst.objects = [it["object"]]
        ob = dst.objects[0]; sc.collection.objects.link(ob)
        ob.parent = None; ob.modifiers.clear()
        if it.get("to_f"):        # body -> f-frame similarity
            s, off = it["to_f"]["s"], it["to_f"]["off"]
            me = ob.data; V = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
            V = (V - np.array(off)) / s; me.vertices.foreach_set("co", V.astype(np.float32).ravel()); me.update()
        ob.matrix_world = __import__("mathutils").Matrix.Identity(4)
    if it.get("img_dir"):
        for slot in ob.material_slots:
            if slot.material and slot.material.node_tree:
                for nd in slot.material.node_tree.nodes:
                    if nd.type == "TEX_IMAGE" and nd.image and nd.image.name.startswith("vampwarrior_"):
                        base = nd.image.name.split(".")[0]
                        nd.image = bpy.data.images.load(os.path.join(it["img_dir"], base + ".png"))
                        if "normal" in base:
                            nd.image.colorspace_settings.name = "Non-Color"
                        print("IMG", it["name"], base, nd.image.filepath)
    if it.get("keep_material"):
        it["_keep"] = True
        return ob
    mat = bpy.data.materials.new(it["name"] + "_m"); mat.use_nodes = True
    nt = mat.node_tree; pb = nt.nodes["Principled BSDF"]; pb.inputs["Roughness"].default_value = 0.6
    if it.get("tex"):
        ti = nt.nodes.new("ShaderNodeTexImage"); ti.image = bpy.data.images.load(it["tex"]); nt.links.new(ti.outputs[0], pb.inputs["Base Color"])
    if it.get("nrm"):
        tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = bpy.data.images.load(it["nrm"]); tn.image.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap"); nt.links.new(tn.outputs[0], nm.inputs["Color"]); nt.links.new(nm.outputs[0], pb.inputs["Normal"])
    ob.data.materials.clear(); ob.data.materials.append(mat)
    return ob


OBS = [(it, make_item(it)) for it in SP["items"]]
for it, ob in OBS:
    if isinstance(ob, Multi):
        continue
    sm0 = np.zeros(len(ob.data.polygons), bool); ob.data.polygons.foreach_get("use_smooth", sm0); it["_smooth0"] = sm0


def set_shade(ob, smooth):
    ob.data.polygons.foreach_set("use_smooth", np.full(len(ob.data.polygons), smooth, bool)); ob.data.update()


for it, ob in OBS:
    for o2 in OBS:
        o2[1].hide_render = o2[1] is not ob
    for sh in SP["shots"]:
        c, scale = FR[sh.get("frame", "head")]
        a = math.radians(sh["angle"])
        d = Vector((math.sin(a), -math.cos(a), 0.0))
        cam.location = Vector(c) + 3.0 * d
        cam.rotation_euler = (math.radians(90), 0, a)
        cam.data.ortho_scale = scale
        mode = sh["mode"]
        targets = ob.obs if isinstance(ob, Multi) else [ob]
        for o_ in targets:
            if isinstance(ob, Multi):
                if mode in ("flat", "smooth"):
                    set_shade(o_, mode == "smooth")
                else:
                    set_shade(o_, True)
            elif not (it.get("_keep") and mode == "tex"):
                set_shade(o_, mode in ("smooth", "tex"))
            else:
                o_.data.polygons.foreach_set("use_smooth", it["_smooth0"]); o_.data.update()
        if mode in ("flat", "smooth"):
            sc.render.engine = "BLENDER_WORKBENCH"
            sh_ = sc.display.shading; sh_.light = "STUDIO"; sh_.color_type = "SINGLE"; sh_.single_color = (0.75, 0.75, 0.75)
            sh_.show_cavity = False; sh_.background_type = "VIEWPORT"; sh_.background_color = (0.08, 0.08, 0.09)
        else:
            sc.render.engine = "BLENDER_EEVEE"
        sc.render.filepath = os.path.join(SP["out"], "%s_%s.png" % (it["name"], sh["tag"]))
        bpy.ops.render.render(write_still=True)
        print("RENDERED", sc.render.filepath)
