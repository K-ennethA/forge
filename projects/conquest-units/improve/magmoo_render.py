"""Magmoo stills, the survey's lighting/camera rules (geode_render.py copied, not imported). In-memory only; never saves.

    blender --background <blend> --factory-startup --python magmoo_render.py -- <out_prefix> [views]

views (comma list; default all): front, threequarter, side, tactical, head, gap_upper, gap_tail.
  head      the head closeup: frames the eye + maw regions (+ margin) from 30 deg off the front, 12 deg up
  gap_*     the molten gap closeups: frames that gap's bridge strands + the core (molten end) faces round them,
            from the side (70 deg) and 18 deg up, so the disjointed ends and the lava strands read together
Framing: every view is fit to the render-visible meshes (rest pose), the floor sits at z = 0.
"""
import bpy, sys, math
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",") if len(argv) > 1 and not argv[1].startswith("--") else \
    ["front", "threequarter", "side", "tactical", "head", "gap_upper", "gap_tail"]
RES = int(argv[argv.index("--res") + 1]) if "--res" in argv else 1024
scene = bpy.context.scene
for o in scene.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    for c in o.evaluated_get(dg).bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
size = hi - lo


def region_points(pred, obj_pred=lambda o: True):
    out = []
    for o in meshes:
        me = o.data
        if "region_id" not in me.attributes or not obj_pred(o):
            continue
        names = list(me["conquest_regions"])
        rid = np.empty(len(me.polygons), dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", rid)
        fc = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("center", fc); fc = fc.reshape(-1, 3)
        m = np.array([pred(names[r]) for r in rid])
        if m.any():
            out.append(fc[m] @ np.array(o.matrix_world)[:3, :3].T + np.array(o.matrix_world)[:3, 3])
    return np.vstack(out) if out else None


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
centre = (lo + hi) / 2
radius = max(size.length / 2, 1e-3)
fm_me = bpy.data.meshes.new("s_floor"); R = radius * 6
fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
floor.location = (centre.x, centre.y, lo.z)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)
cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam_d.clip_start = radius * 0.005; cam_d.clip_end = radius * 100
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)


def world_verts(o):
    co = np.empty(len(o.data.vertices) * 3); o.data.vertices.foreach_get("co", co)
    M = np.array(o.matrix_world)
    return co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]


ALLP = np.vstack([world_verts(o)[::7] for o in meshes])


def aim(angle_deg, elev_deg, fill=1.08, c=None, rad=None):
    """c/rad given: frame that sphere. Otherwise fit the model's PROJECTED extent for this view direction (a long
    serpent seen end-on would be a speck inside its bounding sphere)."""
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    fitted = c is None
    if fitted:
        dn = np.array(d); right = np.cross([0, 0, 1.0], dn); right /= np.linalg.norm(right); up = np.cross(dn, right)
        pr, pu = ALLP @ right, ALLP @ up
        c = Vector(right * (pr.min() + pr.max()) / 2 + up * (pu.min() + pu.max()) / 2 + dn * float(np.mean(ALLP @ dn)))
        rad = max(pr.max() - pr.min(), pu.max() - pu.min()) / 2 * 1.06
        dist = rad / math.tan(half_fov) * fill + float((ALLP @ dn).max() - np.mean(ALLP @ dn))
    else:
        dist = rad / math.sin(half_fov) * fill
    cam.location = c + d * dist
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    if fitted:
        perspective_refine(ALLP, fill * 1.06)


def perspective_refine(P, fill, iters=6):
    """perspective fit (the orthographic estimate over-sizes the far end): re-centre the projected points and set the
    depth so the larger half-extent lands at 1 / fill of the half-frame; the view direction never changes."""
    t = math.tan(half_fov)
    for _ in range(iters):
        R = np.array(cam.rotation_euler.to_matrix())
        q = (P - np.array(cam.location)) @ R
        zc = -q[:, 2]
        x, y = q[:, 0] / zc / t, q[:, 1] / zc / t
        cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        ext = max(x.max() - x.min(), y.max() - y.min()) / 2
        zm = float(np.median(zc))
        loc = np.array(cam.location) + R[:, 0] * cx * zm * t + R[:, 1] * cy * zm * t + R[:, 2] * zm * (ext * fill - 1.0)
        cam.location = Vector(loc)


def frame_pts(P, grow, minr):
    a_, b_ = Vector(P.min(0)), Vector(P.max(0))
    return (a_ + b_) / 2, max((b_ - a_).length / 2 * grow, minr)


table = {"front": (0.0, 5.0, RES, 1.0, None, None), "threequarter": (40.0, 15.0, RES, 1.0, None, None),
         "side": (90.0, 8.0, RES, 1.0, None, None), "tactical": (40.0, 55.0, 256, 1.6, None, None)}
E = region_points(lambda n: n in ("eye", "maw"))
if E is not None:
    c_, r_ = frame_pts(E, 1.9, 0.9)
    table["head"] = (30.0, 12.0, RES, 1.0, c_, r_)
body = next((o for o in meshes if o.get("conquest_segment") == "body"), None)
if body is not None:
    by = np.array([(body.matrix_world @ Vector(c)).y for c in body.bound_box])
    ymid = float((by.min() + by.max()) / 2)
    for tag, pred in (("gap_upper", lambda y: y < ymid), ("gap_tail", lambda y: y > ymid)):
        C = region_points(lambda n: n == "core")
        if C is None:
            continue
        C = C[np.array([pred(y) for y in C[:, 1]])]
        B = region_points(lambda n: True, lambda o: o.get("conquest_segment") == "bridges")
        if B is not None:
            B = B[np.array([pred(y) for y in B[:, 1]])]
            C = np.vstack([C, B])
        c_, r_ = frame_pts(C, 2.4, 1.4)
        table[tag] = (70.0, 18.0, RES, 1.0, c_, r_)
for tag in VIEWS:
    if tag not in table:
        print("SKIP", tag); continue
    ang, elev, res, fill, c, rad = table[tag]
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    aim(ang, elev, fill, c, rad)
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
