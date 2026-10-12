"""ember_stump stills (scaffolded from firefly_render.py: the survey lighting/camera rules, copied not imported). In-memory
only; never saves.

    blender --background <blend> --factory-startup --python ember_stump_render.py -- <out_prefix> <views> [--pose <clip>:<frame>]

views (comma list):
  front, threequarter, side, back, tactical   whole model, perspective (the survey table), floor at z = 0
  focus                                        every close-up the build declares in the main mesh's 'conquest_focus' prop
                                               ({name: [lo, hi] (world boxes), optional "views": {name: [angle, elev]},
                                               "axis_xy": [x, y]}); a single declared name also works as a view
  ortho_front, ortho_back, ortho_side         ORTHOGRAPHIC at the reference sheet's own scale (100 px per unit), framed by
                                               design/reference/<unit>/intake.json 'ortho_frame' (the same box the compose
                                               crops out of the sheet), floor at the sheet's floor line, transparent film
--pose: evaluate at one frame of a clip (default: the rest pose; a missing clip falls back to rest and says so).
Cameras: front = on -Y looking +Y (the Conquest front), side = on +X (the unit's left; its front faces image-left like
the house sheets' side view), back = on +Y.
"""
import bpy, sys, os, math, json
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402  (read-only use)

UNIT = "ember_stump"
argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",")
scene = bpy.context.scene
pose = argv[argv.index("--pose") + 1] if "--pose" in argv else None
for o in scene.objects:
    if o.type == "ARMATURE":
        clip, fr = pose.split(":") if pose else (None, None)
        if clip and clip in bpy.data.actions:
            K.assign_action(o, bpy.data.actions[clip])
            o.data.pose_position = "POSE"
            scene.frame_set(int(fr))
        else:
            if clip:
                print("NO CLIP", clip, "- rendering the rest pose")
            o.data.pose_position = "REST"
for o in list(scene.objects):
    if o.type in ("CAMERA", "LIGHT"):
        bpy.data.objects.remove(o, do_unlink=True)
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
main = max(meshes, key=lambda o: len(o.data.polygons))
FOCUS = json.loads(main["conquest_focus"]) if "conquest_focus" in main.keys() else {}
FOCUS_VIEWS = FOCUS.pop("views", {})
AXIS = FOCUS.pop("axis_xy", [0.0, 0.0])
INTAKE = os.path.join(ROOT, "design", "reference", UNIT, "intake.json")
OF = json.load(open(INTAKE))["ortho_frame"] if os.path.exists(INTAKE) else None
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
views = []
for v in VIEWS:
    views += sorted(FOCUS) if v == "focus" else [v]
os.makedirs(os.path.dirname(PREFIX), exist_ok=True)
for tag in views:
    cam_d.type = "PERSP"
    scene.render.film_transparent = False
    floor.hide_render = False
    scene.render.resolution_percentage = 100
    if tag in table:
        ang, elev, res, fill = table[tag]
        scene.render.resolution_x = scene.render.resolution_y = res
        aim(centre, radius, ang, elev, fill)
    elif tag in FOCUS:
        b0, b1 = (Vector(v) for v in FOCUS[tag])
        ang, elev = FOCUS_VIEWS.get(tag, (28.0, 8.0))
        scene.render.resolution_x = scene.render.resolution_y = 1024
        aim((b0 + b1) / 2, max((b1 - b0).length / 2, 1e-3), ang, elev, 1.0)
    elif tag in ("ortho_front", "ortho_back", "ortho_side"):
        if OF is None:
            print("SKIP", tag, "(no intake.json ortho_frame)"); continue
        w_px, h_px, below = OF["width_px"], OF["height_px"], OF["below_floor_px"]
        ax, ay = AXIS
        cam_d.type = "ORTHO"; cam_d.ortho_scale = max(w_px, h_px) / 100.0
        cam_d.clip_start = 0.01; cam_d.clip_end = 100
        scene.render.resolution_x, scene.render.resolution_y = w_px, h_px
        scene.render.film_transparent = True
        floor.hide_render = True
        zc = (h_px / 2.0 - below) / 100.0          # image bottom = 'below_floor_px' under the floor line, like the sheet crop
        if tag == "ortho_front":
            cam.location = (ax, ay - 20.0, zc); cam.rotation_euler = (math.radians(90), 0, 0)
        elif tag == "ortho_back":                  # on +Y looking -Y: the unit's right on image right, as sheets draw it
            cam.location = (ax, ay + 20.0, zc); cam.rotation_euler = (math.radians(90), 0, math.radians(180))
        else:
            cam.location = (ax + 20.0, ay, zc); cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    else:
        print("SKIP", tag); continue
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
sys.stdout.flush()
os._exit(0)
