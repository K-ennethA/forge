"""Magmoo animated previews (duskmaw_clips.py copied, not imported: multi-mesh unit + the material glow slot).
v2: --prefix names the outputs (magmoo_v2 keeps the v1 previews beside them). v3: + the 'ball' clip; the walk is the
flying S-arc (its top sheet is where the horizontal S reads). --clips idle,walk,ball picks clips.
In-memory only; never saves the blend.

    blender --background <rigged/magmoo.blend> --factory-startup --python magmoo_clips.py -- <out_dir> [res] [sheet] \
        [--prefix P] [--clips idle,walk,ball]

Writes <out_dir>/<P>_{idle,walk,ball}.mp4 (H.264, 24 fps) and, with 'sheet', <P>_<clip>_sheet.png (8 frames of
one cycle, 4x2) plus <P>_walk_top_sheet.png (the flight seen from above: the travelling S reads best there).
Lighting = the survey rules (key/fill/rim suns, grey world, Standard view transform, dark floor at z = 0). Loops play by
a CYCLES modifier on every fcurve (in memory); the material's glow-pulse slot is bound with the rig slot. The camera
is fit to the union of the clip's posed extents (projected), and never moves during a clip.
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
SHEET = "sheet" in argv
os.makedirs(OUT, exist_ok=True)
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == "ARMATURE")
meshes = [o for o in scene.objects if o.type == "MESH" and o.parent is rig and not o.hide_render]
UNIT = argv[argv.index("--prefix") + 1] if "--prefix" in argv else "magmoo"
mat = meshes[0].data.materials[0]
nt = mat.node_tree

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
cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)
R = 80.0
fm_me = bpy.data.meshes.new("s_floor")
fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)


def set_format(video):
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        fmt.media_type = "VIDEO" if video else "IMAGE"
    if video:
        fmt.file_format = "FFMPEG"; fmt.color_mode = "RGB"
        scene.render.ffmpeg.format = "MPEG4"; scene.render.ffmpeg.codec = "H264"
        scene.render.ffmpeg.constant_rate_factor = "HIGH"; scene.render.ffmpeg.audio_codec = "NONE"
    else:
        fmt.file_format = "PNG"


def posed_points(n_frames, step=3):
    pts = []
    dg = None
    for f in range(1, n_frames + 1, step):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for o in meshes:
            m_ = o.evaluated_get(dg).to_mesh()
            co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
            o.evaluated_get(dg).to_mesh_clear()
            pts.append(co.reshape(-1, 3)[::11])
    return np.vstack(pts)


def fit_camera(P, angle_deg, elev_deg, fill=1.06):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    dn = np.array([math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)])
    right = np.cross([0, 0, 1.0], dn); right /= np.linalg.norm(right); up = np.cross(dn, right)
    pr, pu = P @ right, P @ up
    c = right * (pr.min() + pr.max()) / 2 + up * (pu.min() + pu.max()) / 2 + dn * float(np.mean(P @ dn))
    rad = max(pr.max() - pr.min(), pu.max() - pu.min()) / 2 * 1.04
    dist = rad / math.tan(half_fov) * fill + float((P @ dn).max() - np.mean(P @ dn))
    cam.location = Vector(c + dn * dist)
    cam.rotation_euler = (Vector(c) - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_d.clip_start = dist * 0.01; cam_d.clip_end = dist * 20
    t = math.tan(half_fov)
    for _ in range(6):   # perspective re-fit: re-centre the projected points, depth so the larger half-extent = 1 / fill
        R = np.array(cam.rotation_euler.to_matrix())
        q = (P - np.array(cam.location)) @ R
        zc = -q[:, 2]
        x, y = q[:, 0] / zc / t, q[:, 1] / zc / t
        cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        ext = max(x.max() - x.min(), y.max() - y.min()) / 2
        zm = float(np.median(zc))
        cam.location = Vector(np.array(cam.location) + R[:, 0] * cx * zm * t + R[:, 1] * cy * zm * t + R[:, 2] * zm * (ext * fill - 1.0))


def write_sheet(tag, N):
    set_format(False)
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
    rows = [np.concatenate(tiles[i * 4:(i + 1) * 4], axis=1) for i in (1, 0)]
    sheet = np.concatenate(rows, axis=0)
    im = bpy.data.images.new("sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    im.pixels.foreach_set(sheet.ravel())
    im.filepath_raw = os.path.join(OUT, "%s_%s_sheet.png" % (UNIT, tag)); im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    scene.render.resolution_x = scene.render.resolution_y = RES
    print("WROTE sheet", tag)


VIEW = {"idle": (38.0, 16.0), "walk": (34.0, 22.0), "ball": (32.0, 14.0)}
CLIPS = argv[argv.index("--clips") + 1].split(",") if "--clips" in argv else ["idle", "walk", "ball"]
for clip in CLIPS:
    act = bpy.data.actions.get(clip)
    if act is None:
        print("NO CLIP", clip); continue
    for fc in K.action_fcurves(act):
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")
    K.assign_action(rig, act)
    K.assign_action(nt, act if any("nodes[" in fc.data_path for fc in K.action_fcurves(act)) else None)
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    loops = max(2, math.ceil(96 / N))
    P = posed_points(N)
    fit_camera(P, *VIEW[clip])
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    set_format(True)
    scene.frame_start, scene.frame_end = 1, N * loops
    prefix = os.path.join(OUT, "%s_%s_" % (UNIT, clip))
    for old in glob.glob(prefix + "*.mp4"):
        os.remove(old)
    scene.render.filepath = prefix
    bpy.ops.render.render(animation=True)
    made = sorted(glob.glob(prefix + "*.mp4"), key=os.path.getmtime)
    final = os.path.join(OUT, "%s_%s.mp4" % (UNIT, clip))
    if made:
        if os.path.exists(final):
            os.remove(final)
        os.replace(made[-1], final)
    print("WROTE", final, "frames", N * loops, "loops", loops, "cycle", N)
    if SHEET:
        write_sheet(clip, N)
        if clip == "walk":
            fit_camera(P, 0.0, 89.0, fill=1.15)   # v3: the droplet trail + tail tip stay in frame
            write_sheet("walk_top", N)
