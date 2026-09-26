"""Fire Sprite PLACEHOLDER idle preview (firefly_clips.py copied, not imported): idle mp4 + an 8-frame contact sheet over the
loop, three-quarter view, the survey lighting (key/fill/rim suns + grey world, Standard view transform, dark floor AT z = 0
-- the contract floor). In-memory only; never saves the blend.

    blender --background <rigged/firesprite.blend> --factory-startup --python firesprite_clips.py -- <out_dir> [res]

The camera frames the union of the clip's extents over every mesh on the rig (the body AND the wand node) and never moves.
Loops play by adding a CYCLES modifier to every fcurve in memory (the clip's last frame == its first).
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
rig = next(o for o in scene.objects if o.type == "ARMATURE")
meshes = [o for o in scene.objects if o.type == "MESH" and o.parent is rig]
UNIT = max(meshes, key=lambda o: len(o.data.polygons)).name

scene.render.engine = "BLENDER_EEVEE"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 24
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
scene.render.resolution_x = scene.render.resolution_y = RES
scene.render.resolution_percentage = 100


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


def extents(n_frames):
    lo = Vector((1e9, 1e9, 1e9)); hi = -lo
    for f in range(1, n_frames + 1):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for m in meshes:
            ev = m.evaluated_get(dg)
            me = ev.to_mesh()
            co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
            ev.to_mesh_clear()
            co = co.reshape(-1, 3)
            lo = Vector(np.minimum(np.array(lo), co.min(0))); hi = Vector(np.maximum(np.array(hi), co.max(0)))
    return lo, hi


def write_sheet(clip, N):
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
    im.filepath_raw = os.path.join(OUT, "%s_%s_sheet.png" % (UNIT, clip)); im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    scene.render.resolution_x = scene.render.resolution_y = RES
    print("WROTE sheet", clip)


for clip in ("idle",):
    act = bpy.data.actions.get(clip)
    if act is None:
        print("NO CLIP", clip); continue
    for fc in K.action_fcurves(act):
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")
    K.assign_action(rig, act)
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    loops = 3
    lo, hi = extents(N)
    lo.z = min(lo.z, 0.0)
    size = hi - lo
    centre = (lo + hi) / 2
    radius = max(size.length / 2, 1e-3)
    R = radius * 6
    fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
    floor.location = (centre.x, centre.y, 0.0)
    cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100
    a, e = math.radians(-40.0), math.radians(15.0)   # the wand side
    dist = radius / math.sin(half_fov) * 1.08
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = centre + d * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    video_mode()
    scene.frame_start, scene.frame_end = 1, N * loops
    prefix = os.path.join(OUT, "%s_%s_placeholder_" % (UNIT, clip))
    for old in glob.glob(prefix + "*.mp4"):
        os.remove(old)
    scene.render.filepath = prefix
    bpy.ops.render.render(animation=True)
    made = sorted(glob.glob(prefix + "*.mp4"), key=os.path.getmtime)
    final = os.path.join(OUT, "%s_%s_placeholder.mp4" % (UNIT, clip))
    if made:
        if os.path.exists(final):
            os.remove(final)
        os.replace(made[-1], final)
    print("WROTE", final, "frames", N * loops, "loops", loops, "cycle", N)
    write_sheet(clip, N)
sys.stdout.flush()
os._exit(0)
