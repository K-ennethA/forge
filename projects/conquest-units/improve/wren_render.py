"""Wren stills (vampwarrior_render.py's survey lighting / camera rules, copied not imported). In-memory only.

    blender --background <blend> --factory-startup --python wren_render.py -- <out_prefix> <views>
            [--pose <clip>:<frame>] [--res N] [--focus x0,y0,z0,x1,y1,z1]

views (comma list):
  front, threequarter, side, back, tactical     whole model, perspective (the survey table), floor at z = 0
  face, face_side, sword, hem, boots, torso     close-ups framed on the build's 'conquest_focus' boxes (or --focus)
  ortho_front, ortho_side, ortho_back           orthographic sheet-style views (transparent film)
--pose: evaluate at one frame of a clip (default: the rest pose).
Cameras: front = on -Y looking +Y (the Conquest front), side = on +X (her left), back = on +Y.
"""
import bpy, sys, os, math, json
import numpy as np
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
if "--palette-override" in argv:
    # in-memory repaint with some regions overridden: "name=r,g,b[:emission_scale];name=..." (debug tints, alternatives)
    import palettes as PAL  # noqa: E402  (read-only use)
    for o in scene.objects:
        if o.type == "MESH" and "region_id" in o.data.attributes:
            pal = PAL.load(o.get("conquest_unit", "wren"), o.data.get("conquest_skin", "default"))
            for item in argv[argv.index("--palette-override") + 1].split(";"):
                nm, val = item.split("=")
                rgb, _, esc = val.partition(":")
                pal["regions"][nm] = dict(pal["regions"][nm], rgb=[int(x) for x in rgb.split(",")])
                if esc:
                    pal["regions"][nm]["emission_scale"] = float(esc)
            print("OVERRIDE", o.name, PAL.paint(o.data, pal) is not None)
if "--hair-normal-flat" in argv:
    # v3 A/B proof: the hair UV strip of the normal map set back to flat (0.5, 0.5, 1) in memory = the v2 hair shading
    # (every facet lit by its own face normal) on the v3 geometry
    for o in scene.objects:
        if o.type == "MESH" and "conquest_hair_uv_strip" in o.keys():
            u0 = float(o["conquest_hair_uv_strip"]) - 0.006
            for img in bpy.data.images:
                if img.name.endswith("_normal") and img.size[0] > 0:
                    W_, H_ = img.size
                    px_ = np.empty(W_ * H_ * 4, dtype=np.float32); img.pixels.foreach_get(px_); px_ = px_.reshape(H_, W_, 4)
                    px_[:, int(u0 * W_):, :3] = (0.5, 0.5, 1.0)
                    img.pixels.foreach_set(px_.ravel()); img.update()
                    print("HAIR_NORMAL_FLAT", img.name, int(u0 * W_))
if "--hide-fork" in argv:
    # v4: the hair close-ups from his right / behind are taken without the pitchfork (its shaft crosses those views)
    for o in scene.objects:
        if o.type == "MESH" and o.name.endswith("_pitchfork"):
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
         "head": (25.0, 6.0, 1.0), "head_back": (160.0, 8.0, 1.0), "head_front": (0.0, 4.0, 1.0),
         "portrait": (0.0, 3.0, 0.62), "portrait_tq": (32.0, 6.0, 0.66), "portrait_low": (12.0, -14.0, 0.66),
         "eyes": (0.0, 1.0, 1.0), "torso_front": (0.0, 4.0, 1.0),
         # v4: brow close-up; the fang close-ups (the root must never show over the upper lip) at the portrait angle,
         # from below, three-quarter-below and above
         "brows": (0.0, 2.0, 1.0),
         # wren close-ups: hair from four sides, the cloak + patches from behind, bracer + necklace, the pitchfork
         "head_side": (90.0, 4.0, 1.0), "head_tq_back": (135.0, 10.0, 1.0), "head_top": (20.0, 45.0, 1.0),
         "cloak": (180.0, 8.0, 1.0), "cloak_tq": (140.0, 10.0, 1.0), "cloak_front": (0.0, 6.0, 1.0),
         "patches": (180.0, 10.0, 0.9), "necklace": (0.0, 6.0, 1.0), "bracer": (60.0, 8.0, 1.0),
         "bracer_front": (15.0, 6.0, 1.0), "fork_head": (10.0, 6.0, 1.0), "fork_full": (20.0, 4.0, 1.0),
         "boots_front": (0.0, 10.0, 1.0),
         # v2 face round: the mouth (lip pairing) front + three-quarter
         "mouth": (0.0, 2.0, 1.0), "mouth_tq": (30.0, 4.0, 1.0), "mouth_side": (90.0, 2.0, 1.0),
         # v3 FE round: the hair one-volume close-up (three-quarter from above the brow line) + the single-eye close-up
         "hair_close": (35.0, 16.0, 0.78), "eye_close": (0.0, 1.0, 0.55), "eyes_v2frame": (0.0, 1.0, 1.0),
         # v4 (review-log 2026-09-29 "Wren v4 feedback"): the orbit-blend cleanup views (nose bridge / nostrils front and
         # three-quarter, the side of the face in profile), all framed on the rest-pose 'face' box; the hair layer-shadow
         # close-ups (front-left over the part, right three-quarter over the sweep)
         "nose": (0.0, 1.0, 0.62), "nose_tq": (35.0, 2.0, 0.66), "face_side90": (90.0, 2.0, 1.0),
         "hair_part": (25.0, 38.0, 0.80), "hair_sweep": (-50.0, 12.0, 0.80), "hair_back_close": (160.0, 18.0, 0.80),
         # v5 (review-log 2026-09-29 "Wren v5 feedback + FE reference set"): the under-eye close-ups (the 'eyes' box
         # moved 16 mm down: the lower lids, the under-eye skin and the cheeks), front and three-quarter
         "undereye": (0.0, 1.0, 1.0), "undereye_tq": (30.0, 2.0, 1.0)}
CLOSE_KEY = {"hair_close": "head", "eye_close": "eye_L", "nose": "face", "nose_tq": "face", "hair_part": "head",
             "hair_sweep": "head", "hair_back_close": "head", "undereye": "eyes", "undereye_tq": "eyes"}
CLOSE_SHIFT = {"undereye": (0.0, 0.0, -0.016), "undereye_tq": (0.0, 0.0, -0.016)}   # the focus box moved (m)
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
            key = tag if tag in FOCUS else ("fork_full" if tag.startswith("fork") and tag not in FOCUS else
                                             CLOSE_KEY.get(tag, tag.split("_")[0]))
            b0, b1 = (Vector(v) for v in FOCUS[key])
            if tag in CLOSE_SHIFT:
                b0, b1 = b0 + Vector(CLOSE_SHIFT[tag]), b1 + Vector(CLOSE_SHIFT[tag])
        ang, elev, fill = close[tag]
        aim((b0 + b1) / 2, max((b1 - b0).length / 2, 1e-3), ang, elev, fill)
    elif tag in ("ortho_front", "ortho_side", "ortho_back"):
        cam_d.type = "ORTHO"
        cam_d.clip_start = 0.01; cam_d.clip_end = 100
        scene.render.resolution_x, scene.render.resolution_y = RES // 2, RES
        wide = size.y if tag == "ortho_side" else size.x
        cam_d.ortho_scale = max(size.z, 2.0 * wide) * 1.06
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
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
sys.stdout.flush()
os._exit(0)
