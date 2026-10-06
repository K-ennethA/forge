"""Lyra stills: shadow_assassin_render.py's survey lighting / camera rules (= elias_render.py's; other units' files are
read-only) + elias_render's in-memory palette override + the Lyra close-ups. In-memory only (the opened blend is never saved).

    blender --background <blend> --factory-startup --python lyra_render.py -- <out_prefix> <views>
            [--res N] [--bg sheet|survey] [--palette-override "name=r,g,b;..."] [--suffix _x] [--hide-books]

views (comma list):
  front, threequarter, side, back, back_threequarter      whole model, perspective (the survey table), floor at z = 0
  head_front, head_tq, head_side, head_back               the head + the ponytail's tie / rise (build focus box 'head')
  tail_side, tail_back                                    the whole ponytail (focus 'tail')
  hold, hold_tq, hold_side, hold_top                      the book stack + the right hand (focus 'hold')
  belt, satchel, back_emblem, chest, boots                corset / belts / vials / medallions; satchel + scroll case; the
                                                          capelet back emblem; collar / necktie / brooch; the boots
  ortho_front, ortho_side, ortho_back                     orthographic, transparent film (the sheet comparison)
--suffix: appended to every file name (the one-tone hair still uses --palette-override + --suffix _onetone).
Cameras: front = on -Y looking +Y (the Conquest front), side = on +X (her left; she faces image-left, as on the sheet),
back = on +Y.
"""
import bpy, sys, os, math, json
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",")
RES = int(argv[argv.index("--res") + 1]) if "--res" in argv else 1024
BG = argv[argv.index("--bg") + 1] if "--bg" in argv else "sheet"
SUFFIX = argv[argv.index("--suffix") + 1] if "--suffix" in argv else ""
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
scene = bpy.context.scene
for o in scene.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
for o in list(scene.objects):
    if o.type in ("CAMERA", "LIGHT"):
        bpy.data.objects.remove(o, do_unlink=True)
if "--palette-override" in argv:
    import palettes as PAL  # noqa: E402  (read-only use)
    for o in scene.objects:
        if o.type == "MESH" and "region_id" in o.data.attributes:
            pal = PAL.load(o.get("conquest_unit", "lyra"), o.data.get("conquest_skin", "default"))
            names_ = set(o.data["conquest_regions"])
            for item in argv[argv.index("--palette-override") + 1].split(";"):
                nm, val = item.split("=")
                if nm not in pal["regions"]:
                    continue
                pal["regions"][nm] = dict(pal["regions"][nm], rgb=[int(x) for x in val.split(",")])
            print("OVERRIDE", o.name, PAL.paint(o.data, pal) is not None)
if "--hide-books" in argv:
    for o in scene.objects:
        if o.type == "MESH" and o.name.endswith("_books"):
            o.hide_render = True
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
main = max(meshes, key=lambda o: len(o.data.polygons))
FOCUS = json.loads(main["conquest_focus"]) if "conquest_focus" in main.keys() else {}
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    M = np.array(o.matrix_world); co = co @ M[:3, :3].T + M[:3, 3]
    lo = Vector(np.minimum(np.array(lo), co.min(0))); hi = Vector(np.maximum(np.array(hi), co.max(0)))
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
_bgc = (0.62, 0.58, 0.45, 1) if BG == "sheet" else (0.18, 0.18, 0.19, 1)     # sheet paper (linear)
world.node_tree.nodes["Background"].inputs[0].default_value = _bgc
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6 if BG != "sheet" else 1.0
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
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.45, 0.42, 0.33, 1) if BG == "sheet" else (0.1, 0.1, 0.11, 1)
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


table = {"front": (0.0, 5.0, 1.0), "threequarter": (-35.0, 12.0, 1.0), "side": (90.0, 5.0, 1.0), "back": (180.0, 5.0, 1.0),
         "back_threequarter": (145.0, 10.0, 1.0)}
close = {"head_front": (0.0, 3.0, 0.80), "head_tq": (-38.0, 6.0, 0.80), "head_side": (90.0, 3.0, 0.80), "head_back": (180.0, 6.0, 0.80),
         "tail_side": (90.0, 4.0, 0.95), "tail_back": (180.0, 4.0, 0.95),
         "hold": (0.0, 6.0, 1.0), "hold_tq": (-45.0, 10.0, 1.0), "hold_side": (-90.0, 4.0, 1.0), "hold_top": (-20.0, 55.0, 1.0),
         "belt": (25.0, 6.0, 1.0), "satchel": (-120.0, 6.0, 1.0), "back_emblem": (180.0, 8.0, 1.0), "chest": (8.0, 6.0, 1.0),
         "boots": (20.0, 10.0, 1.0)}
CLOSE_KEY = {"head_front": "head", "head_tq": "head", "head_side": "head", "head_back": "head", "tail_side": "tail",
             "tail_back": "tail", "hold_tq": "hold", "hold_side": "hold", "hold_top": "hold", "back_emblem": "capelet"}
os.makedirs(os.path.dirname(PREFIX), exist_ok=True)
for tag in VIEWS:
    cam_d.type = "PERSP"
    scene.render.film_transparent = False
    floor.hide_render = False
    scene.render.resolution_percentage = 100
    scene.render.resolution_x = scene.render.resolution_y = RES
    if tag in table:
        ang, elev, fill = table[tag]
        aim(centre, radius, ang, elev, fill)
    elif tag in close:
        key = CLOSE_KEY.get(tag, tag)
        if key not in FOCUS:
            print("SKIP (no focus box)", tag); continue
        b0, b1 = (Vector(v) for v in FOCUS[key])
        ang, elev, fill = close[tag]
        aim((b0 + b1) / 2, max((b1 - b0).length / 2, 1e-3), ang, elev, fill)
    elif tag in ("ortho_front", "ortho_side", "ortho_back"):
        cam_d.type = "ORTHO"
        cam_d.clip_start = 0.01; cam_d.clip_end = 100
        scene.render.resolution_x, scene.render.resolution_y = RES // 2, RES
        wide = size.y if tag == "ortho_side" else size.x
        cam_d.ortho_scale = max(size.z, 2.0 * wide) * 1.04
        scene.render.film_transparent = True
        floor.hide_render = True
        zc = size.z * 0.5
        if tag == "ortho_front":
            cam.location = (centre.x, -20.0, zc); cam.rotation_euler = (math.radians(90), 0, 0)
        elif tag == "ortho_back":
            cam.location = (centre.x, 20.0, zc); cam.rotation_euler = (math.radians(90), 0, math.radians(180))
        else:
            cam.location = (20.0, centre.y, zc); cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    else:
        print("SKIP", tag); continue
    scene.render.filepath = "%s_%s%s.png" % (PREFIX, tag, SUFFIX)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
sys.stdout.flush()
os._exit(0)
