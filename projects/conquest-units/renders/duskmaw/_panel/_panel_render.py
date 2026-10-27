
import bpy, json, math, sys
from mathutils import Matrix, Vector
argv = sys.argv[sys.argv.index("--") + 1:]
src, tfp, outp = argv[0], argv[1], argv[2]
if src.endswith(".glb"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
    tf = json.load(open(tfp)) if tfp != "-" else None
    if tf:
        R = Matrix(tf["rotation"]); off = Vector(tf["offset"]); s = tf["scale"]
        M = Matrix.Scale(s, 4) @ Matrix.Translation(-off) @ R.to_4x4()
        root = bpy.data.objects.new("norm", None); bpy.context.scene.collection.objects.link(root)
        for o in list(bpy.context.scene.objects):
            if o is not root and o.parent is None:
                o.parent = root; o.matrix_parent_inverse = Matrix.Identity(4)
        root.matrix_world = M
else:
    bpy.ops.wm.open_mainfile(filepath=src)
scene = bpy.context.scene
for o in scene.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
for o in list(scene.objects):
    if o.type in ("CAMERA", "LIGHT"):
        bpy.data.objects.remove(o, do_unlink=True)
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    for v in me.vertices:
        w = o.matrix_world @ v.co
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    ev.to_mesh_clear()
scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
rig = bpy.data.objects.new("lr", None); scene.collection.objects.link(rig)
def light(name, energy, rot, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot]; o.parent = rig
light("k", 3.2, (50, 0, 150)); light("f", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)); light("r", 2.0, (60, 0, 10))
rig.rotation_euler = (0, 0, math.radians(180))
cam_d = bpy.data.cameras.new("c"); cam_d.lens = 50
cam = bpy.data.objects.new("c", cam_d); scene.collection.objects.link(cam); scene.camera = cam
half = math.atan(18.0 / 50.0)
H = float(argv[3]) if len(argv) > 3 else (hi - lo).length / 2
c = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, H * 0.5))
rad = H * 0.72
scene.render.resolution_x = scene.render.resolution_y = 640
scene.render.film_transparent = True
for tag, ang, el in (("front", 0.0, 5.0), ("side", 90.0, 5.0), ("threequarter", 40.0, 15.0)):
    a, e = math.radians(ang), math.radians(el)
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = c + d * (rad / math.sin(half) * 1.05)
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_d.clip_start = rad * 0.01; cam_d.clip_end = rad * 100
    scene.render.filepath = outp + "_" + tag + ".png"
    bpy.ops.render.render(write_still=True)
if len(argv) > 4 and argv[4] != "-":
    cu = json.load(open(argv[4]))
    for tag, spec in cu.items():
        a, e = math.radians(spec["yaw"]), math.radians(spec.get("elev", 4.0))
        cc = Vector(spec["c"]); rr = float(spec["r"])
        d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
        cam.location = cc + d * (rr / math.sin(half))
        cam.rotation_euler = (cc - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam_d.clip_start = rr * 0.01; cam_d.clip_end = rr * 100
        scene.render.filepath = outp + "_" + tag + ".png"
        bpy.ops.render.render(write_still=True)
print("PANEL_RENDERED", outp)
