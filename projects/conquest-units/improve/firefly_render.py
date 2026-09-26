"""Firefly stills (vampito_render.py's survey lighting/camera rules, copied not imported). In-memory only; never saves.

    blender --background <blend> --factory-startup --python firefly_render.py -- <out_prefix> <views> [--pose <clip>:<frame>]

views (comma list):
  front, threequarter, side, back, tactical   whole model, perspective (the survey table), floor at z = 0
  head, port, flame                            close-ups framed on the build's 'conquest_focus' boxes
  ortho_front, ortho_side                      ORTHOGRAPHIC at the reference sheet's own scale (100 px per unit, 400 x 820,
                                               floor at the image's y 800 = the sheet's flame-tip line y 845 in its crop),
                                               centred on the torso axis, transparent film -- the sheet-vs-model panels
--pose: evaluate at one frame of a clip (default: the rest pose).
Cameras: front = on -Y looking +Y (the Conquest front), side = on +X (the unit's left; its front faces image-left like the
sheet's side view), back = on +Y.
"""
import bpy, sys, os, math, json
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402  (read-only use)

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",")
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


table = {"front": (0.0, 5.0, 1024, 1.0), "threequarter": (40.0, 15.0, 1024, 1.0), "tactical": (40.0, 55.0, 256, 1.6),
         "side": (90.0, 5.0, 1024, 1.0), "back": (180.0, 5.0, 1024, 1.0)}
close = {"head": (28.0, 8.0, 0.62), "port": (78.0, 10.0, 0.60), "flame": (25.0, 2.0, 0.62)}
os.makedirs(os.path.dirname(PREFIX), exist_ok=True)
for tag in VIEWS:
    cam_d.type = "PERSP"
    scene.render.film_transparent = False
    floor.hide_render = False
    scene.render.resolution_percentage = 100
    if tag in table:
        ang, elev, res, fill = table[tag]
        scene.render.resolution_x = scene.render.resolution_y = res
        aim(centre, radius, ang, elev, fill)
    elif tag in close:
        b0, b1 = (Vector(v) for v in FOCUS[tag])
        ang, elev, fill = close[tag]
        scene.render.resolution_x = scene.render.resolution_y = 1024
        aim((b0 + b1) / 2, max((b1 - b0).length / 2, 1e-3), ang, elev, fill / 0.62)
    elif tag in ("ortho_front", "ortho_side"):
        ax, ay = FOCUS["axis_xy"]
        cam_d.type = "ORTHO"; cam_d.ortho_scale = 8.2
        cam_d.clip_start = 0.01; cam_d.clip_end = 100
        scene.render.resolution_x, scene.render.resolution_y = 400, 820
        scene.render.film_transparent = True
        floor.hide_render = True
        zc = 8.2 / 2 - 0.20                     # image bottom = z -0.20 (sheet crop y 865), top = z 8.00 (y 45)
        if tag == "ortho_front":
            cam.location = (ax, ay - 20.0, zc); cam.rotation_euler = (math.radians(90), 0, 0)
        else:
            cam.location = (ax + 20.0, ay, zc); cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    else:
        print("SKIP", tag); continue
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
sys.stdout.flush()
os._exit(0)
