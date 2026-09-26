"""Supaoctto preview stills, the SURVEY's lighting/camera rules (vampito_render.py copied, not imported, so this lane never
touches another lane's file). In-memory only; never saves.

    blender --background <blend> --factory-startup --python supaoctto_render.py -- <out_prefix> [views] [--yaw <deg>]
            [--pose <clip>:<frame>] [--skin <palette skin>]

views: comma list of front, threequarter, tactical, side, back, cape (the cape + water-web close-up: from behind, above),
       front_yaw (front camera; the facing-candidate rows use it with --yaw).
--yaw: rotate every root object about Z in memory (the facing-candidate renders of the raw sculpt use 0 / 90 / 180 / 270).
--pose: evaluate a rigged blend at one frame of a clip (default: the rest pose).
--skin: repaint the stored colour regions from palettes/supaoctto/<skin>.json in memory (the skin-swap proof).
The floor sits at z = 0 (the contract floor), or under the lowest vertex for a raw sculpt below it.
Cameras: front = on -Y looking +Y (the Conquest front), side = on +X, back = on +Y.
"""
import bpy, sys, os, math
from mathutils import Vector, Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402  (read-only use)

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",") if len(argv) > 1 and not argv[1].startswith("--") else ["front", "threequarter", "tactical"]
scene = bpy.context.scene
yaw = math.radians(float(argv[argv.index("--yaw") + 1])) if "--yaw" in argv else 0.0
if yaw:
    for o in scene.objects:
        if o.parent is None:
            o.matrix_world = Matrix.Rotation(yaw, 4, "Z") @ o.matrix_world
if "--skin" in argv:
    import palettes as PAL  # noqa: E402  (read-only use)
    pal, done = PAL.repaint_scene("supaoctto", argv[argv.index("--skin") + 1])
    print("SKIN", pal["skin"], done)
pose = argv[argv.index("--pose") + 1] if "--pose" in argv else None
for o in scene.objects:
    if o.type == "ARMATURE":
        if pose:
            clip, fr = pose.split(":")
            K.assign_action(o, bpy.data.actions[clip])
            o.data.pose_position = "POSE"
            scene.frame_set(int(fr))
        else:
            o.data.pose_position = "REST"
for o in list(scene.objects):
    if o.type in ("CAMERA", "LIGHT"):
        bpy.data.objects.remove(o, do_unlink=True)
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render and o.visible_get()]
for o in list(scene.objects):
    if o.type == "MESH" and o not in meshes:
        bpy.data.objects.remove(o, do_unlink=True)
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    for v in me.vertices:
        w = o.matrix_world @ v.co
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    ev.to_mesh_clear()
size = hi - lo
floor_z = 0.0 if lo.z > -1e-3 else lo.z

scene.render.engine = "BLENDER_EEVEE"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 32
except Exception:
    pass
world = bpy.data.worlds.new("survey_world"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
rig = bpy.data.objects.new("survey_rig", None); scene.collection.objects.link(rig)


def light(name, energy, rot_deg, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot_deg]
    o.parent = rig


light("s_key", 3.2, (50, 0, 150)); light("s_fill", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)); light("s_rim", 2.0, (60, 0, 10))
rig.rotation_euler = (0, 0, math.radians(180))
centre = (lo + hi) / 2
radius = max(size.length / 2, 1e-3)
fm_me = bpy.data.meshes.new("s_floor"); R = radius * 6
fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
floor.location = (centre.x, centre.y, floor_z)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)
cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)


def aim(angle_deg, elev_deg, fill=1.08, target=None):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    tgt = centre if target is None else target
    dist = radius / math.sin(half_fov) * fill
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = tgt + d * dist
    cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()


cape_target = Vector((centre.x, centre.y + 0.15 * size.y, lo.z + 0.62 * size.z))
table = {"front": (0.0, 5.0, 1024, 1.0, None), "threequarter": (40.0, 15.0, 1024, 1.0, None), "tactical": (40.0, 55.0, 256, 1.6, None),
         "side": (90.0, 5.0, 1024, 1.0, None), "back": (180.0, 5.0, 1024, 1.0, None),
         "cape": (152.0, 24.0, 1024, 0.62, cape_target), "front_yaw": (0.0, 5.0, 768, 1.0, None)}
os.makedirs(os.path.dirname(PREFIX), exist_ok=True)
for tag in VIEWS:
    if tag not in table:
        print("SKIP", tag); continue
    ang, elev, res, fill, tgt = table[tag]
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    aim(ang, elev, fill, tgt)
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
sys.stdout.flush()
os._exit(0)
