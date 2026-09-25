"""Preview mp4s (+ 8-frame contact sheets) for Eldroot standing v3 (tree-skirt + trunk collars): the brief asks
for refreshed stand_up and walk previews (eldroot3_* stems; the v2 eldroot2_* previews stay untouched). A copy of
eldroot2_render_clips.py restricted to those two clips; the camera framing still takes the union of ALL five clips so
it matches the v2 previews shot for shot. Same lighting as render_improved.py. In-memory only; never saves the blend.

    blender --background rigged/eldroot_standing3.blend --factory-startup --python eldroot3_render_clips.py --
            <out_dir> [res]

ONE fixed camera for every clip (framing = the union of all clips' extents), so the seated and
standing heights read against each other. Loops play per CLIPS; one-shots hold their first pose 0.5 s
and their last 1 s (a shifted copy of the action whose constant extrapolation holds the ends).
stand_up, sit_down and walk are also rendered from the side (90 deg, *_side.mp4).
Writes <out_dir>/eldroot3_<clip>.mp4 (+ _side.mp4) and eldroot3_<clip>_sheet.png.
"""
import bpy, sys, os, math, glob
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
RES = int(argv[1]) if len(argv) > 1 else 768
os.makedirs(OUT, exist_ok=True)
scene = bpy.context.scene
rig = bpy.data.objects["eldroot_rig"]
mesh = bpy.data.objects["eldroot"]

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
fm_me = bpy.data.meshes.new("s_floor"); R_ = 60.0
fm_me.from_pydata([(-R_, -R_, 0), (R_, -R_, 0), (R_, R_, 0), (-R_, R_, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)
cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)

CLIPS = [("sitting_idle", "loop", 1, "eldroot3_sitting_idle"), ("stand_up", "oneshot", 1, "eldroot3_stand_up"),
         ("idle", "loop", 2, "eldroot3_idle"), ("sit_down", "oneshot", 1, "eldroot3_sit_down"),
         ("walk", "loop", 3, "eldroot3_walk")]
RENDER = ("stand_up", "walk")
HOLD_IN, HOLD_OUT = 12, 24

# union extents over every clip (every 2nd frame) -> one camera for all
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for name, kind, loops, stem in CLIPS:
    act = bpy.data.actions[name]
    K.assign_action(rig, act)
    f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
    for f in range(f0, f1 + 1, 2):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for c in mesh.evaluated_get(dg).bound_box:
            w = mesh.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
centre = (lo + hi) / 2
radius = max((hi - lo).length / 2, 1e-3)
print("UNION EXTENTS", tuple(round(v, 3) for v in lo), tuple(round(v, 3) for v in hi))


def aim(angle_deg, elev_deg):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    dist = radius / math.sin(half_fov) * 1.08
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = centre + d * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100


def set_video():
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        fmt.media_type = "VIDEO"
    fmt.file_format = "FFMPEG"
    fmt.color_mode = "RGB"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.ffmpeg.audio_codec = "NONE"


def render_video(stem, f_start, f_end):
    set_video()
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    scene.frame_start, scene.frame_end = f_start, f_end
    prefix = os.path.join(OUT, stem + "_")
    for old in glob.glob(prefix + "[0-9]*.mp4"):
        os.remove(old)
    scene.render.filepath = prefix
    bpy.ops.render.render(animation=True)
    made = sorted(glob.glob(prefix + "[0-9]*.mp4"), key=os.path.getmtime)
    final = os.path.join(OUT, stem + ".mp4")
    if made:
        if os.path.exists(final):
            os.remove(final)
        os.replace(made[-1], final)
    print("WROTE", final, "frames", f_end - f_start + 1)


def write_sheet(stem, frames):
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        fmt.media_type = "IMAGE"
    fmt.file_format = "PNG"
    r = RES // 2
    scene.render.resolution_x = scene.render.resolution_y = r
    tiles = []
    tmp = os.path.join(OUT, "_tile_eldroot3.png")
    for f in frames:
        scene.frame_set(f)
        scene.render.filepath = tmp
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(tmp, check_existing=False)
        tiles.append(np.array(img.pixels[:], dtype=np.float32).reshape(r, r, 4))
        bpy.data.images.remove(img)
    os.remove(tmp)
    rows = [np.concatenate(tiles[i * 4:(i + 1) * 4], axis=1) for i in (1, 0)]
    sheet = np.concatenate(rows, axis=0)
    im = bpy.data.images.new("sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    im.pixels.foreach_set(sheet.ravel())
    im.filepath_raw = os.path.join(OUT, stem + "_sheet.png"); im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    print("WROTE sheet", stem)


for name, kind, loops, stem in CLIPS:
    if name not in RENDER:
        continue
    act = bpy.data.actions[name]
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    if kind == "loop":
        play = act.copy()
        for fc in K.action_fcurves(play):
            if not any(m.type == "CYCLES" for m in fc.modifiers):
                fc.modifiers.new("CYCLES")
        K.assign_action(rig, play)
        for ang, elev, st in [(40.0, 15.0, stem)] + ([(90.0, 8.0, stem + "_side")] if name == "walk" else []):
            aim(ang, elev)
            render_video(st, 1, N * loops)
        aim(40.0, 15.0)
        write_sheet(stem, [1 + int(round(k * N / 8)) for k in range(8)])
    else:
        play = act.copy()                      # shifted copy: ends hold via constant extrapolation
        play.use_frame_range = False
        for fc in K.action_fcurves(play):
            for kp in fc.keyframe_points:
                kp.co.x += HOLD_IN; kp.handle_left.x += HOLD_IN; kp.handle_right.x += HOLD_IN
            fc.extrapolation = "CONSTANT"
        K.assign_action(rig, play)
        views = [(40.0, 15.0, stem)] + ([(90.0, 8.0, stem + "_side")] if name in ("stand_up", "sit_down") else [])
        for ang, elev, st in views:
            aim(ang, elev)
            render_video(st, 1, HOLD_IN + N + 1 + HOLD_OUT)
        aim(40.0, 15.0)
        write_sheet(stem, [1 + HOLD_IN + int(round(k * N / 7)) for k in range(8)])
    rig.animation_data.action = None
sys.stdout.flush()
