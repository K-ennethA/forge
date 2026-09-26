"""Geode motion v2 preview mp4s + contact sheets: 'walk' (several loops) and 'idle' (two loops), three-quarter view, the
SAME lighting / camera rules as render_animated.py (key/fill/rim suns + grey world, Standard view transform, dark
floor), which v1's geode_idle.mp4 used. A geode-owned copy (not an import) because Geode's clips carry two extra slots
in the same action that the shared renderer does not bind: the material node tree (glow pulse) AND the mesh's shape
Key (the electricity-arc flicker). In-memory only; never saves the blend.

    blender --background rigged/geode.blend --factory-startup --python geode_anim_render.py -- <out_dir> [res]

Writes <out_dir>/geode_walk.mp4, geode_idle_v2.mp4, geode_walk_sheet.png, geode_idle_v2_sheet.png (H.264, 24 fps).
Loops play by adding a CYCLES modifier to every fcurve in memory (each clip's last frame == its first).
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
NAMES = {"walk": "geode_walk", "idle": "geode_idle_v2"}
os.makedirs(OUT, exist_ok=True)
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == "ARMATURE")
mesh = next(o for o in scene.objects if o.type == "MESH" and o.parent is rig)
nt = mesh.data.materials[0].node_tree if mesh.data.materials else None
key = mesh.data.shape_keys

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
scene.render.resolution_x = scene.render.resolution_y = RES
scene.render.resolution_percentage = 100


def video_mode():
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        fmt.media_type = "VIDEO"
    fmt.file_format = "FFMPEG"
    fmt.color_mode = "RGB"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.ffmpeg.audio_codec = "NONE"


video_mode()
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
        for c in mesh.evaluated_get(dg).bound_box:
            w = mesh.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    return lo, hi


def bind(idb, act, prefix):
    """bind idb to the action's slot of its ID type (identifier prefix: OB / NT / KE); None when the action has none."""
    if idb is None:
        return False
    slot = next((s for s in act.slots if s.identifier.startswith(prefix)), None)
    if slot is None:
        if idb.animation_data:
            idb.animation_data.action = None
        return False
    if idb.animation_data is None:
        idb.animation_data_create()
    idb.animation_data.action = act
    idb.animation_data.action_slot = slot
    return True


def write_sheet(stem, N):
    """8 evenly spaced frames of one cycle, 4x2, same camera (PNG)."""
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        fmt.media_type = "IMAGE"
    fmt.file_format = "PNG"
    r = RES // 2
    scene.render.resolution_x = scene.render.resolution_y = r
    tiles = []
    tmp = os.path.join(OUT, "_tile_%s.png" % stem)
    for k in range(8):
        f = 1 + int(round(k * N / 8))
        scene.frame_set(f)
        scene.render.filepath = tmp
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(tmp, check_existing=False)
        tiles.append(np.array(img.pixels[:], dtype=np.float32).reshape(r, r, 4))
        bpy.data.images.remove(img)
    os.remove(tmp)
    rows = [np.concatenate(tiles[i * 4:(i + 1) * 4], axis=1) for i in (1, 0)]   # pixel rows are bottom-up
    sheet = np.concatenate(rows, axis=0)
    im = bpy.data.images.new("sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    im.pixels.foreach_set(sheet.ravel())
    im.filepath_raw = os.path.join(OUT, "%s_sheet.png" % stem); im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    scene.render.resolution_x = scene.render.resolution_y = RES
    video_mode()
    print("WROTE sheet", stem)


floor_built = False
for clip in ("walk", "idle"):
    act = bpy.data.actions.get(clip)
    if act is None:
        print("NO CLIP", clip); continue
    for fc in K.action_fcurves(act):
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")
    bound = {"rig": bind(rig, act, "OB"), "glow": bind(nt, act, "NT"), "arcs": bind(key, act, "KE")}
    print("BOUND", clip, bound)
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    loops = max(3, math.ceil(72 / N)) if clip == "walk" else 2
    lo, hi = extents(N)
    print("EXTENTS", clip, tuple(round(v, 3) for v in lo), tuple(round(v, 3) for v in hi))
    size = hi - lo
    centre = (lo + hi) / 2
    radius = max(size.length / 2, 1e-3)
    if not floor_built:
        R = radius * 6
        fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
        floor.location = (centre.x, centre.y, 0.0)
        floor_built = True
    cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100
    a, e = math.radians(40.0), math.radians(15.0)
    dist = radius / math.sin(half_fov) * 1.08
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = centre + d * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.frame_start, scene.frame_end = 1, N * loops
    stem = NAMES[clip]
    prefix = os.path.join(OUT, stem + "_")
    for old in glob.glob(prefix + "*.mp4"):
        os.remove(old)
    scene.render.filepath = prefix
    bpy.ops.render.render(animation=True)
    made = sorted(glob.glob(prefix + "*.mp4"), key=os.path.getmtime)
    final = os.path.join(OUT, stem + ".mp4")
    if made:
        if os.path.exists(final):
            os.remove(final)
        os.replace(made[-1], final)
    print("WROTE", final, "frames", N * loops, "loops", loops, "cycle", N)
    write_sheet(stem, N)
sys.stdout.flush()
os._exit(0)
