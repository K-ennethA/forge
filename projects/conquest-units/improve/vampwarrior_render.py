"""Vampire Warrior stills (firefly_render.py's survey lighting / camera rules, copied not imported). In-memory only.

    blender --background <blend> --factory-startup --python vampwarrior_render.py -- <out_prefix> <views>
            [--pose <clip>:<frame>] [--res N] [--focus x0,y0,z0,x1,y1,z1]

views (comma list):
  front, threequarter, side, back, tactical     whole model, perspective (the survey table), floor at z = 0
  face, face_side, sword, hem, boots, torso     close-ups framed on the build's 'conquest_focus' boxes (or --focus)
  ortho_front, ortho_side                       orthographic sheet-style views (transparent film)
--pose: evaluate at one frame of a clip (default: the rest pose).
Cameras: front = on -Y looking +Y (the Conquest front), side = on +X (her left), back = on +Y.
"""
import bpy, sys, os, math, json
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402  (read-only use)

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",")
RES = int(argv[argv.index("--res") + 1]) if "--res" in argv else 1024
FOC = [float(x) for x in argv[argv.index("--focus") + 1].split(",")] if "--focus" in argv else None
scene = bpy.context.scene
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
import vampwarrior_toon as VT  # noqa: E402
if "--no-outline" in argv:
    print("HIDDEN", VT.hide_outlines(bpy))
if "--toon" in argv:
    print("TOON", VT.toon_preview(bpy))
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
main = max(meshes, key=lambda o: len(o.data.polygons))
FOCUS = json.loads(main["conquest_focus"]) if "conquest_focus" in main.keys() else {}
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

scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 48
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
floor.location = (centre.x, centre.y, 0.0)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)
cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)


def aim(c, rad, angle_deg, elev_deg, fill=1.08):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    dist = rad / math.sin(half_fov) * fill
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = c + d * dist
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_d.clip_start = rad * 0.01; cam_d.clip_end = rad * 100


table = {"front": (0.0, 5.0, 1.0), "threequarter": (35.0, 12.0, 1.0), "tactical": (40.0, 55.0, 1.6),
         "tactical_small": (40.0, 55.0, 1.6), "side": (90.0, 5.0, 1.0), "back": (180.0, 5.0, 1.0),
         "back_threequarter": (145.0, 10.0, 1.0)}
close = {"face": (18.0, 4.0, 1.0), "face_side": (70.0, 4.0, 1.0), "face_front": (0.0, 2.0, 0.62), "sword": (20.0, 6.0, 1.0),
         "hem": (150.0, 10.0, 1.0), "boots": (30.0, 12.0, 1.0), "torso": (20.0, 6.0, 1.0), "hand": (35.0, 10.0, 1.0),
         "head": (25.0, 6.0, 1.0), "head_back": (160.0, 8.0, 1.0)}
os.makedirs(os.path.dirname(PREFIX), exist_ok=True)
for tag in VIEWS:
    cam_d.type = "PERSP"
    scene.render.film_transparent = False
    floor.hide_render = False
    scene.render.resolution_percentage = 100
    scene.render.resolution_x = scene.render.resolution_y = RES
    if tag in table:
        ang, elev, fill = table[tag]
        if tag == "tactical":
            scene.render.resolution_x = scene.render.resolution_y = 256
        if tag == "tactical_small":
            scene.render.resolution_x = scene.render.resolution_y = 128
        aim(centre, radius, ang, elev, fill)
    elif tag in close:
        if FOC is not None:
            b0, b1 = Vector(FOC[:3]), Vector(FOC[3:])
        else:
            key = tag if tag in FOCUS else tag.split("_")[0]
            b0, b1 = (Vector(v) for v in FOCUS[key])
        ang, elev, fill = close[tag]
        aim((b0 + b1) / 2, max((b1 - b0).length / 2, 1e-3), ang, elev, fill)
    elif tag in ("ortho_front", "ortho_side"):
        cam_d.type = "ORTHO"
        cam_d.clip_start = 0.01; cam_d.clip_end = 100
        scene.render.resolution_x, scene.render.resolution_y = RES // 2, RES
        wide = size.x if tag == "ortho_front" else size.y
        cam_d.ortho_scale = max(size.z, 2.0 * wide) * 1.06
        scene.render.film_transparent = True
        floor.hide_render = True
        zc = size.z * 0.5
        if tag == "ortho_front":
            cam.location = (centre.x, -20.0, zc); cam.rotation_euler = (math.radians(90), 0, 0)
        else:
            cam.location = (20.0, centre.y, zc); cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    else:
        print("SKIP", tag); continue
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
sys.stdout.flush()
os._exit(0)
