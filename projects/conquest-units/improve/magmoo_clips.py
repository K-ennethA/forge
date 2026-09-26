"""Magmoo animated previews (v4). In-memory only; never saves the blend.

    blender --background <rigged/magmoo.blend> --factory-startup --python magmoo_clips.py -- <out_dir> [res] [sheet] \
        [--prefix P] [--clips idle,walk,ball] [--no-mp4] [--transitions <rigged/magmoo.json>]

Writes <out_dir>/<P>_{idle,walk,ball}.mp4 (H.264, 24 fps) and, with 'sheet', <P>_<clip>_sheet.png (8 frames of one cycle,
4x2) plus <P>_walk_side_sheet.png (the leap seen square from the side, 12 frames: the launch, the vertical S the body
rides through the air and the landing read here; the camera does not follow -- the unit travels in place).
--transitions: frame-by-frame contact sheets of one MERGE (a join's surfaces meeting: strands reach, contact, bulge) and
one SEPARATION (strands neck, snap, recoil) taken from the rig report's transition events (rigged/magmoo.json), close
up on the join -> <P>_transition_merge.png / <P>_transition_separation.png (frame numbers burnt into the tile order,
listed in the log).
v4: the unit is one mesh; each clip binds the rig slot, the Key's shape-key slot (the goo deformation) and the material
glow slot of the same action. Lighting = the survey rules. The camera is fit to the union of the clip's posed extents.
"""
import bpy, sys, os, math, glob, json
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
RES = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 768
SHEET = "sheet" in argv
os.makedirs(OUT, exist_ok=True)
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == "ARMATURE")
meshes = [o for o in scene.objects if o.type == "MESH" and o.parent is rig and not o.hide_render]
UNIT = argv[argv.index("--prefix") + 1] if "--prefix" in argv else "magmoo"
mat = meshes[0].data.materials[0]
nt = mat.node_tree
KEY = meshes[0].data.shape_keys

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
R = 120.0
fm_me = bpy.data.meshes.new("s_floor")
fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)
ISL = json.loads(meshes[0].data["conquest_islands"]) if "conquest_islands" in meshes[0].data else {}


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


def posed(f):
    scene.frame_set(f)
    dg = bpy.context.evaluated_depsgraph_get()
    o = meshes[0]
    m_ = o.evaluated_get(dg).to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    o.evaluated_get(dg).to_mesh_clear()
    return co.reshape(-1, 3)


def live_points(co, every=11):
    keep = np.ones(len(co), bool)
    for name, (a, b) in ISL.items():
        if float(np.ptp(co[a:b], axis=0).max()) < 0.2:
            keep[a:b] = False
    return co[keep][::every]


def posed_points(n_frames, step=3):
    return np.vstack([live_points(posed(f)) for f in range(1, n_frames + 1, step)])


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
    for _ in range(6):
        Rm = np.array(cam.rotation_euler.to_matrix())
        q = (P - np.array(cam.location)) @ Rm
        zc = -q[:, 2]
        x, y = q[:, 0] / zc / t, q[:, 1] / zc / t
        cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        ext = max(x.max() - x.min(), y.max() - y.min()) / 2
        zm = float(np.median(zc))
        cam.location = Vector(np.array(cam.location) + Rm[:, 0] * cx * zm * t + Rm[:, 1] * cy * zm * t + Rm[:, 2] * zm * (ext * fill - 1.0))


def render_tile(f, r):
    scene.frame_set(f)
    p = os.path.join(OUT, "_tile_%s.png" % UNIT)
    scene.render.filepath = p
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(p, check_existing=False)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], 4)
    bpy.data.images.remove(img)
    os.remove(p)
    return px


def write_grid(tiles, cols, path):
    rows_ = [np.concatenate(tiles[i * cols:(i + 1) * cols], axis=1) for i in range(len(tiles) // cols)]
    sheet = np.concatenate(rows_[::-1], axis=0)                 # image rows are bottom-up: first row on top
    im = bpy.data.images.new("sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    im.pixels.foreach_set(sheet.ravel())
    im.filepath_raw = path; im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    print("WROTE sheet", path)


def write_sheet(tag, frames, cols=4, rx=None, ry=None):
    set_format(False)
    rx = rx or RES // 2; ry = ry or RES // 2
    scene.render.resolution_x, scene.render.resolution_y = rx, ry
    tiles = [render_tile(f, rx) for f in frames]
    write_grid(tiles, cols, os.path.join(OUT, "%s_%s_sheet.png" % (UNIT, tag)))
    scene.render.resolution_x = scene.render.resolution_y = RES


def bind(act):
    for fc in K.action_fcurves(act):
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")
    K.assign_action(rig, act)
    if KEY is not None:
        K.assign_action(KEY, act if any(s.target_id_type == "KEY" for s in act.slots) else None)
    K.assign_action(nt, act if any("nodes[" in fc.data_path for fc in K.action_fcurves(act)) else None)


VIEW = {"idle": (38.0, 16.0), "walk": (34.0, 20.0), "ball": (32.0, 14.0)}
CLIPS = argv[argv.index("--clips") + 1].split(",") if "--clips" in argv else ["idle", "walk", "ball"]
for clip in CLIPS:
    act = bpy.data.actions.get(clip)
    if act is None:
        print("NO CLIP", clip); continue
    bind(act)
    N = int(round(act.frame_range[1] - act.frame_range[0]))
    loops = max(2, math.ceil(96 / N))
    P = posed_points(N)
    fit_camera(P, *VIEW[clip])
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    if "--no-mp4" not in argv:
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
        write_sheet(clip, [1 + int(round(k * N / 8)) for k in range(8)])
        if clip == "walk":
            fit_camera(P, 90.0, 4.0, fill=1.04)                # square side view: the vertical S + launch / land
            fr = [1 + int(round(k * N / 12)) for k in range(12)]
            write_sheet("walk_side", fr, cols=4, rx=RES // 2, ry=int(RES * 0.3))
            print("SIDE_FRAMES", fr)
if "--transitions" in argv:
    rep = json.load(open(argv[argv.index("--transitions") + 1]))
    ev = rep["transition_events"]
    gt = rep["gap_tracks"]
    best = {}
    for clip in ("walk", "idle", "ball"):
        for e in ev.get(clip, []):
            kind = "merge" if e["event"] == "contact" else "separation"
            tr = gt[clip][e["join"]]
            n_ = len(tr) - 1
            # merge: prefer the contact with the longest approach inside strand reach before it; separation: the
            # snap with the longest stretch (touching -> snapped) before it -- the most frames of visible goo bridge
            span, f = 0, e["frame"] - 2
            while span < 12 and (0.0 < tr[f % n_] < 0.85 if kind == "merge" else tr[f % n_] < 0.9):
                span += 1; f -= 1
            if kind not in best or span > best[kind][0]:
                best[kind] = (span, clip, e)
    picks = {k: (v[1], v[2]) for k, v in best.items()}
    for kind, (clip, e) in picks.items():
        act = bpy.data.actions[clip]
        bind(act)
        N = int(round(act.frame_range[1] - act.frame_range[0]))
        f0 = e["frame"]
        frames = [((f0 - 1 + k) % N) + 1 for k in (range(-9, 3) if kind == "merge" else range(-6, 6))]
        # close-up on the join: the two pieces' points round the join at the event frame
        co = posed(f0)
        a_, b_ = ("mid", "tail") if e["join"] == "crown" else ("mid", "head")
        A = co[ISL[a_][0]:ISL[a_][1]]; B = co[ISL[b_][0]:ISL[b_][1]]
        ca, cb = A.mean(0), B.mean(0)
        mid_pt = 0.5 * (A[np.argmin(np.linalg.norm(A - cb, axis=1))] + B[np.argmin(np.linalg.norm(B - ca, axis=1))])
        allp = np.vstack([posed(f)[np.linalg.norm(posed(f) - mid_pt, axis=1) < 2.6][::3] for f in frames[::3]])
        fit_camera(allp, 70.0, 18.0, fill=1.05)
        set_format(False)
        scene.render.resolution_x = scene.render.resolution_y = 384
        tiles = [render_tile(f, 384) for f in frames]
        write_grid(tiles, 6, os.path.join(OUT, "%s_transition_%s.png" % (UNIT, kind)))
        print("TRANSITION", kind, clip, e, "frames", frames)
