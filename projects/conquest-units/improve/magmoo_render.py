"""Magmoo stills (v5), the survey's lighting/camera rules. In-memory only; never saves.
v5: the eye pickers take the socket ('eye') + the goo eye ('eye_goo', islands eye_orb.* / ball_eye.*); + view
'eyeclose' (one goo eye filling the frame, off its axis leaned toward the front). The rest pose = the v5 paused rest.

    blender --background <blend> --factory-startup --python magmoo_render.py -- <out_prefix> [views] \
        [--pose <clip>:<frame>] [--res N]

views (comma list; default front,threequarter,side,tactical,head):
  front / threequarter / side / tactical / top   fit to the unit's projected extent (tactical = 256 px, 55 deg down)
  head       the head closeup: frames the eye sockets (+ margin) from 30 deg off the front, 12 deg up
  headside   the head from its side, level (the dragon eye placement reads here)
  eyeprofile looks across the head tangent to the +X socket's goo: a socket shows as a notch in the outline
--pose idle:66 poses the rig (bones + the Key's shape-key slot + the material glow slot of that clip's action) at that
frame before rendering; otherwise the REST pose (v4: the segmented rest = the bind pose).
v4: the unit is ONE merged mesh; its islands (mesh custom prop 'conquest_islands') are measured posed, and COLLAPSED
islands (evaluated extent < 0.2: the goo ball riding tiny inside the mound, hidden bridges / shed drips, pieces absorbed
into the ball) are left out of the framing and the region pickers. The floor sits at z = 0.
"""
import bpy, sys, os, math, json
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",") if len(argv) > 1 and not argv[1].startswith("--") else \
    ["front", "threequarter", "side", "tactical", "head"]
RES = int(argv[argv.index("--res") + 1]) if "--res" in argv else 1024
POSE = argv[argv.index("--pose") + 1] if "--pose" in argv else None
scene = bpy.context.scene
rig = next((o for o in scene.objects if o.type == "ARMATURE"), None)
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
mat = meshes[0].data.materials[0] if meshes and meshes[0].data.materials else None
if rig is not None:
    if POSE:
        clip, fr = POSE.split(":")
        act = bpy.data.actions[clip]
        K.assign_action(rig, act)
        for o in meshes:
            if o.data.shape_keys is not None and any(s.target_id_type == "KEY" for s in act.slots):
                K.assign_action(o.data.shape_keys, act)
        if mat is not None and any("nodes[" in fc.data_path for fc in K.action_fcurves(act)):
            K.assign_action(mat.node_tree, act)
        rig.data.pose_position = "POSE"
        scene.frame_set(int(fr))
    else:
        rig.data.pose_position = "REST"
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()


def eval_mesh(o):
    """-> (world verts, face centres, face normals, face region names, face island names, live-vertex mask)"""
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    fc = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("center", fc); fc = fc.reshape(-1, 3)
    fn = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("normal", fn); fn = fn.reshape(-1, 3)
    fv = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", fv)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    fv0 = lv[fv]
    reg = None
    if "region_id" in me.attributes and "conquest_regions" in o.data:
        names = list(o.data["conquest_regions"])
        rid = np.empty(len(me.polygons), dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", rid)
        reg = np.array(names)[rid]
    ev.to_mesh_clear()
    M = np.array(o.matrix_world)
    co = co @ M[:3, :3].T + M[:3, 3]
    live = np.ones(len(co), bool)
    isl = np.full(len(co), "", dtype=object)
    if "conquest_islands" in o.data:
        for name, (a, b) in json.loads(o.data["conquest_islands"]).items():
            isl[a:b] = name
            if float(np.ptp(co[a:b], axis=0).max()) < 0.2:
                live[a:b] = False
    return co, fc @ M[:3, :3].T + M[:3, 3], fn @ M[:3, :3].T, reg, isl[fv0], live, live[fv0]


EV = {o.name: eval_mesh(o) for o in meshes}
ALLP = np.vstack([v[0][v[5]][::5] for v in EV.values()])
lo, hi = ALLP.min(0), ALLP.max(0)
size = Vector(hi - lo)

scene.render.engine = "BLENDER_EEVEE"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 32
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
centre = Vector((lo + hi) / 2)
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
cam_d.clip_start = radius * 0.005; cam_d.clip_end = radius * 100
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)


def perspective_refine(P, fill, iters=8):
    """v5: centre + dolly with a MULTIPLICATIVE distance update (median depth x projected extent) that never lets the
    nearest point come closer than 25 % of the median depth. The v4 additive step diverged on the v5 front view (the
    raised head sits far in front of the long body: the camera ended up past it and the still came out empty)."""
    t = math.tan(half_fov)
    for _ in range(iters):
        R_ = np.array(cam.rotation_euler.to_matrix())
        q = (P - np.array(cam.location)) @ R_
        zc = -q[:, 2]
        zm = float(np.median(zc))
        if zc.min() < 0.25 * zm:                        # too close to the nearest point: back off first
            cam.location = Vector(np.array(cam.location) + R_[:, 2] * (0.25 * zm - float(zc.min())) * 2.0)
            continue
        x, y = q[:, 0] / zc / t, q[:, 1] / zc / t
        cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        ext = max(x.max() - x.min(), y.max() - y.min()) / 2
        new_zm = max(zm * ext * fill, zm - 0.75 * float(zc.min()))
        cam.location = Vector(np.array(cam.location) + R_[:, 0] * cx * zm * t + R_[:, 1] * cy * zm * t +
                              R_[:, 2] * (new_zm - zm))


def aim_dir(dn, fill=1.08, P=None):
    P = ALLP if P is None else P
    dn = np.asarray(dn, float) / np.linalg.norm(dn)
    right = np.cross([0, 0, 1.0], dn) if abs(dn[2]) < 0.999 else np.array([1.0, 0, 0])
    right /= np.linalg.norm(right); up = np.cross(dn, right)
    pr, pu = P @ right, P @ up
    c = right * (pr.min() + pr.max()) / 2 + up * (pu.min() + pu.max()) / 2 + dn * float(np.mean(P @ dn))
    rad = max(pr.max() - pr.min(), pu.max() - pu.min()) / 2 * 1.06
    dist = rad / math.tan(half_fov) * fill + float((P @ dn).max() - np.mean(P @ dn))
    cam.location = Vector(c + dn * dist)
    cam.rotation_euler = (Vector(c) - cam.location).to_track_quat("-Z", "Y" if abs(dn[2]) < 0.999 else "X").to_euler()
    exact_fit(P, fill * 1.06)


def exact_fit(P, fill):
    """v5: exact perspective framing at a fixed camera rotation, by nested bisection: for a camera depth D, pan along
    the camera right / up until the projected bounds are centred (each monotone in the pan), then pick the D whose
    half-extent x fill = the frame edge (monotone in D). Replaces the v4 iterative refine, which diverged on the v5
    front view (the raised head far in front of the long body)."""
    t = math.tan(half_fov)
    R_ = np.array(cam.rotation_euler.to_matrix())
    rt, upv, bk = R_[:, 0], R_[:, 1], R_[:, 2]
    pr, pu, pb = P @ rt, P @ upv, P @ bk

    def centred(D):
        zc = D - pb                                          # camera at depth D along bk
        a, b = [], []
        for comp in (pr, pu):
            lo_, hi_ = comp.min(), comp.max()
            for _ in range(40):
                m = 0.5 * (lo_ + hi_)
                x = (comp - m) / (zc * t)
                if x.max() + x.min() > 0:
                    lo_ = m
                else:
                    hi_ = m
            a.append(0.5 * (lo_ + hi_))
        x = (pr - a[0]) / (zc * t); y = (pu - a[1]) / (zc * t)
        return a, max(x.max() - x.min(), y.max() - y.min()) / 2
    lo_d = float(pb.max()) + 1e-3 * float(np.ptp(pb) + 1.0)
    hi_d = lo_d + 50.0 * float(max(np.ptp(pr), np.ptp(pu), np.ptp(pb)) + 1.0)
    for _ in range(50):
        D = 0.5 * (lo_d + hi_d)
        if centred(D)[1] * fill > 1.0:
            lo_d = D
        else:
            hi_d = D
    (a0, a1), _ = centred(hi_d)
    cam.location = Vector(rt * a0 + upv * a1 + bk * hi_d)
    cam_d.clip_start = max(1e-3, float((hi_d - pb).min()) * 0.2)


def aim(angle_deg, elev_deg, fill=1.08, P=None):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    aim_dir(np.array([math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)]), fill, P)


def region_sel(pred, isl_pred=lambda n: True):
    """-> (face centres, face normals) of the matching regions on the live islands"""
    C, N = [], []
    for n, (v, fc, fn, reg, fisl, live, flive) in EV.items():
        if reg is None:
            continue
        m = np.array([pred(r) for r in reg]) & flive & np.array([isl_pred(i) for i in fisl])
        if m.any():
            C.append(fc[m]); N.append(fn[m])
    return (np.vstack(C), np.vstack(N)) if C else (None, None)


table = {"front": (0.0, 5.0, RES, 1.0, None), "threequarter": (40.0, 15.0, RES, 1.0, None),
         "side": (90.0, 8.0, RES, 1.0, None), "tactical": (40.0, 55.0, 256, 1.6, None), "top": (0.0, 89.9, RES, 1.02, None),
         "back": (180.0, 12.0, RES, 1.0, None)}
EYE_ISL = lambda i: i in ("head", "ball") or i.startswith("eye_orb") or i.startswith("ball_eye")   # v5 goo eyes
E, EN = region_sel(lambda r: r in ("eye", "eye_goo"), EYE_ISL)
if E is not None:
    c_ = E.mean(0)
    rr = max(float(np.linalg.norm(E - c_, axis=1).max()) * 2.4, 1.1)
    box = np.vstack([c_ + np.array(o) * rr for o in [(1, 1, 1), (-1, -1, -1), (1, -1, 1), (-1, 1, -1)]])
    table["head"] = (30.0, 12.0, RES, 1.0, box)
    X = E - c_
    L = np.linalg.svd(X, full_matrices=False)[2][0]
    if L[0] < 0:
        L = -L
    side = X @ L > 0
    c1 = E[side].mean(0)
    Gc, Gn = region_sel(lambda r: r not in ("eye", "eye_goo"), lambda i: i in ("head", "ball"))
    ring = np.linalg.norm(Gc - c1, axis=1) < float(np.linalg.norm(E[side] - c1, axis=1).max()) * 1.6
    n1 = Gn[ring].mean(0); n1 /= np.linalg.norm(n1)
    c2 = E[~side].mean(0)
    ring2 = np.linalg.norm(Gc - c2, axis=1) < float(np.linalg.norm(E[~side] - c2, axis=1).max()) * 1.6
    n2 = Gn[ring2].mean(0); n2 /= np.linalg.norm(n2)
    f = np.cross(L, n1 + n2); f /= np.linalg.norm(f)
    dn = np.cross(n1, f); dn /= np.linalg.norm(dn)
    if dn[2] < 0:
        dn = -dn
    rr2 = float(np.linalg.norm(E[side] - c1, axis=1).max())
    box2 = np.vstack([c1 + f * rr2 * 6.0, c1 - f * rr2 * 6.0, c1 + n1 * rr2 * 3.0, c1 - n1 * rr2 * 3.0])
    table["eyeprofile"] = (dn, None, RES, 1.0, box2)
    # v5 eyeclose: one goo eye (the +X side) filling the frame, seen off its own axis leaned toward the front
    dn3 = n1 + np.array([0.0, -0.75, 0.15]); dn3 /= np.linalg.norm(dn3)
    box3 = np.vstack([c1 + np.array(o) * rr2 * 2.6 for o in [(1, 1, 1), (-1, -1, -1), (1, -1, 1), (-1, 1, -1)]])
    table["eyeclose"] = (dn3, None, RES, 1.0, box3)
    Hc, _ = region_sel(lambda r: True, lambda i: i == "head")
    if Hc is not None:
        table["headside"] = (75.0, 10.0, RES, 1.05, Hc)
        table["headtop"] = (20.0, 50.0, RES, 1.05, Hc)
for tag in VIEWS:
    if tag not in table:
        print("SKIP", tag); continue
    ang, elev, res, fill, P = table[tag]
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    if elev is None:
        aim_dir(ang, fill, P)
    else:
        aim(ang, elev, fill, P)
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
