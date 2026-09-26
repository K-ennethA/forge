"""Supaoctto animated previews (vampito_clips.py copied, not imported): idle + walk + float mp4s and 8-frame contact sheets, the
survey lighting (key/fill/rim suns + grey world, Standard view transform, dark floor AT z = 0 -- the contract floor).
In-memory only; never saves the blend.

    blender --background <rigged/supaoctto.blend> --factory-startup --python supaoctto_clips.py -- <out_dir> [res] [prefix] [tags]

Renders (camera angle measured from the -Y front, like supaoctto_render.py):
    supaoctto_idle.mp4 + _idle_sheet.png           three-quarter front (40 deg), 2 loops
    supaoctto_idle_back.mp4 + _idle_back_sheet.png three-quarter BACK (150 deg): the cape sway + water-web ripple, 1 loop
    supaoctto_walk.mp4 + _walk_sheet.png           three-quarter front, >= 3 s of loops
    supaoctto_walk_side_sheet.png                  side (90 deg): the stepping, one stride
    supaoctto_walk_back.mp4                        three-quarter back: the trailing cape, >= 3 s
    supaoctto_float.mp4 + _float_sheet.png         three-quarter front (30 deg): rise, arms cross, hover, land (1 loop)
    supaoctto_float_back.mp4 + _float_back_sheet   three-quarter BACK (150 deg): the cape flare + web ripple (1 loop)
    supaoctto_float_side_sheet.png                 side (90 deg)
    supaoctto_float_face.mp4 + _float_face_sheet   v3: a FACE camera that follows the head (translation only, 20 deg), 1
                                                   loop: the smirk opening over the arms-crossed hold
v3: each clip's action also carries the 'smirk' morph weight (a KEY slot): it is bound with the rig for every job.
[prefix] replaces the 'supaoctto' file prefix (v2 renders use v2_supaoctto so the v1 files stay for the before/after).
[tags] (v4) a comma list limiting the jobs to those tags (e.g. float_face for the floating-mask variant's face cam).
Loops play by adding a CYCLES modifier to every fcurve in memory (the clip's last frame == its first). The camera frames
the union of the clip's extents and never moves. Contact sheets: 8 evenly spaced frames of one loop, 4x2.
"""
import bpy, sys, os, math, glob, json
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
RES = int(argv[1]) if len(argv) > 1 else 768
PREFIX = argv[2] if len(argv) > 2 else None
ONLY = set(argv[3].split(",")) if len(argv) > 3 else None
os.makedirs(OUT, exist_ok=True)
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == "ARMATURE")
mesh = next(o for o in scene.objects if o.type == "MESH" and o.parent is rig)
UNIT = PREFIX or mesh.name

scene.render.engine = "BLENDER_EEVEE"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 16
except Exception:
    pass
world = bpy.data.worlds.new("survey_world"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
lrig = bpy.data.objects.new("survey_rig", None); scene.collection.objects.link(lrig)


def light(name, energy, rot_deg, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot_deg]
    o.parent = lrig


light("s_key", 3.2, (50, 0, 150)); light("s_fill", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)); light("s_rim", 2.0, (60, 0, 10))
lrig.rotation_euler = (0, 0, math.radians(180))
scene.render.fps = K.FPS
scene.render.fps_base = 1.0


def video_mode():
    f = scene.render.image_settings
    if hasattr(f, "media_type"):
        f.media_type = "VIDEO"
    f.file_format = "FFMPEG"
    f.color_mode = "RGB"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.ffmpeg.audio_codec = "NONE"


cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)
fm_me = bpy.data.meshes.new("s_floor")
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)
floor_built = False


def extents(n_frames):
    lo = Vector((1e9, 1e9, 1e9)); hi = -lo
    for f in range(1, n_frames + 1):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for c in mesh.evaluated_get(dg).bound_box:
            w = mesh.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    return lo, hi


KEYS = mesh.data.shape_keys


def bind(act):
    K.assign_action(rig, act)
    if KEYS is not None:
        K.assign_action(KEYS, act if any(s_.target_id_type == "KEY" for s_ in act.slots) else None)


def place_camera(clip, angle, elev):
    global floor_built
    act = bpy.data.actions[clip]
    bind(act)
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    lo, hi = extents(N)
    lo.z = min(lo.z, 0.0)
    size = hi - lo
    centre = (lo + hi) / 2
    radius = max(size.length / 2, 1e-3)
    if not floor_built:
        R = radius * 6
        fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
        floor.location = (centre.x, centre.y, 0.0)
        floor_built = True
    cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100
    a, e = math.radians(angle), math.radians(elev)
    dist = radius / math.sin(half_fov) * 1.05
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = centre + d * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    return N


def write_sheet(clip, N, tag):
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        fmt.media_type = "IMAGE"
    fmt.file_format = "PNG"
    r = RES // 2
    scene.render.resolution_x = scene.render.resolution_y = r
    tiles = []
    for k in range(8):
        f = 1 + int(round(k * N / 8))
        scene.frame_set(f)
        p = os.path.join(OUT, "_tile_%s.png" % UNIT)
        scene.render.filepath = p
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(p, check_existing=False)
        px = np.array(img.pixels[:], dtype=np.float32).reshape(r, r, 4)
        bpy.data.images.remove(img)
        tiles.append(px)
    os.remove(os.path.join(OUT, "_tile_%s.png" % UNIT))
    rows = [np.concatenate(tiles[i * 4:(i + 1) * 4], axis=1) for i in (1, 0)]   # pixel rows are bottom-up
    sheet = np.concatenate(rows, axis=0)
    im = bpy.data.images.new("sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    im.pixels.foreach_set(sheet.ravel())
    im.filepath_raw = os.path.join(OUT, "%s_%s_sheet.png" % (UNIT, tag)); im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    print("WROTE sheet", tag)


def write_mp4(clip, N, tag, loops):
    scene.render.resolution_x = scene.render.resolution_y = RES
    video_mode()
    scene.frame_start, scene.frame_end = 1, N * loops
    prefix = os.path.join(OUT, "%s_%s_" % (UNIT, tag))
    for old in glob.glob(prefix + "*.mp4"):
        os.remove(old)
    scene.render.filepath = prefix
    bpy.ops.render.render(animation=True)
    made = sorted(glob.glob(prefix + "*.mp4"), key=os.path.getmtime)
    final = os.path.join(OUT, "%s_%s.mp4" % (UNIT, tag))
    if made:
        if os.path.exists(final):
            os.remove(final)
        os.replace(made[-1], final)
    print("WROTE", final, "frames", N * loops, "loops", loops, "cycle", N)


CLIPS = [c for c in ("idle", "walk", "float") if bpy.data.actions.get(c)]
for clip in CLIPS:
    act = bpy.data.actions.get(clip)
    for fc in K.action_fcurves(act):
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")
JOBS = [("idle", "idle", 40.0, 15.0, 2, True), ("idle", "idle_back", 150.0, 18.0, 1, True),
        ("walk", "walk", 40.0, 15.0, None, True), ("walk", "walk_side", 90.0, 8.0, 0, True),
        ("walk", "walk_back", 150.0, 18.0, None, False),
        ("float", "float", 30.0, 12.0, 1, True), ("float", "float_back", 150.0, 18.0, 1, True),
        ("float", "float_side", 90.0, 6.0, 0, True)]
for clip, tag, ang, elev, loops, sheet in JOBS:
    if clip not in CLIPS or (ONLY is not None and tag not in ONLY):
        continue
    N = place_camera(clip, ang, elev)
    if loops is None:
        loops = max(3, math.ceil(72 / N))
    if loops:
        write_mp4(clip, N, tag, loops)
    if sheet:
        write_sheet(clip, N, tag)


def write_face(clip, tag, angle, elev, fill):
    """v3: the camera follows the face (the head bone carries the rest-pose face landmark), orientation fixed."""
    LMv = {}
    for key_ in ("v3_landmarks", "v2_landmarks"):
        if key_ in mesh.keys() and not LMv:
            LMv = json.loads(mesh[key_])
    if not LMv or "mouth" not in LMv:
        print("SKIP face cam: no v3 landmarks"); return
    act = bpy.data.actions[clip]
    bind(act)
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    face_rest = (Vector(LMv["visor"]) * 0.45 + Vector(LMv["mouth"]) * 0.55)
    pbh = rig.pose.bones["head"]; bh = rig.data.bones["head"]
    a, e = math.radians(angle), math.radians(elev)
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    scene.frame_set(1)
    lo_, hi_ = extents(1)
    dist = max((hi_ - lo_).length / 2, 1e-3) / math.sin(half_fov) * fill
    cam.animation_data_clear()
    for f in range(1, N + 2):
        scene.frame_set(f)
        M = rig.matrix_world @ pbh.matrix @ bh.matrix_local.inverted()
        tgt = M @ face_rest
        cam.location = tgt + d * dist
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
    write_mp4(clip, N, tag, 1)
    write_sheet(clip, N, tag)
    cam.animation_data_clear()


if "float" in CLIPS and (ONLY is None or "float_face" in ONLY):
    write_face("float", "float_face", 20.0, 4.0, 0.26)
sys.stdout.flush()
os._exit(0)
