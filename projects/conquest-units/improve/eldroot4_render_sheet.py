"""Eldroot standing v4 approval sheet (tasset plates + proportion rebalance): v3 STANDING vs v4 STANDING vs v4
MID-FOLD (stand_up, the plates riding the thighs) vs v4 SIT, plus tasset closeups and the traced-W overlay. Same
lighting / camera rules as eldroot3_render_sheet.py.

    blender --background rigged/eldroot_standing4.blend --factory-startup --python eldroot4_render_sheet.py --
            <out_dir> <v3_standing.blend> <v4_receipt.json>

Writes to <out_dir>:
  standing_front / _threequarter / _side / _back / _tactical .png -- the v4 standing rest alone.
  compare_front / _threequarter / _side / _tactical .png -- left to right: v3 STANDING (rigged/eldroot_standing3.blend,
      untouched), v4 STANDING, v4 MID-FOLD (stand_up frame where the plates swing ~70 deg with the thighs), v4 SIT
      (sitting_idle frame 1); same floor, scale and camera; labels carry measured heights.
  fold_threequarter / fold_front / fold_side .png -- the mid-fold snapshot alone (tassets riding the fold).
  sit_threequarter / sit_front / sit_side .png -- the v4 sit pose alone.
  closeup_tassets_front.png (standing) / closeup_tassets_fold.png (mid-fold) / closeup_knee_collars.png /
  closeup_ankle_collars.png.
  annotation_overlay.png -- the v4 standing rest through the artist's annotation camera (the v2 front camera riding
      with the trunk, scaled with the cell refit) with the traced W stroke painted red on top.
In-memory only; never saves.
"""
import bpy, sys, os, math, json
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
OUT, V3, RECEIPT = argv[0], argv[1], argv[2]
os.makedirs(OUT, exist_ok=True)
HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402

REP = json.load(open(RECEIPT))
scene = bpy.context.scene
stand = bpy.data.objects["eldroot"]
rig = bpy.data.objects["eldroot_rig"]

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

fm_me = bpy.data.meshes.new("s_floor"); R_ = 80.0
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


def bbox(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((1e9, 1e9, 1e9)); hi = -lo
    for o in objs:
        ev = o.evaluated_get(dg)
        m = ev.to_mesh()
        for v in m.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
        ev.to_mesh_clear()
    return lo, hi


def aim(centre, radius, angle_deg, elev_deg, fill=1.08):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    dist = radius / math.sin(half_fov) * fill
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = centre + d * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100


def shoot(path, res):
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print("WROTE", path)


def rest_pose(r):
    if r.animation_data:
        r.animation_data.action = None
    for pb in r.pose.bones:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)


def snapshot(action, frame, name):
    K.assign_action(rig, bpy.data.actions[action])
    scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    me_ = bpy.data.meshes.new_from_object(stand.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    ob_ = bpy.data.objects.new(name, me_); scene.collection.objects.link(ob_)
    ob_.matrix_world = stand.matrix_world.copy()
    rest_pose(rig); scene.frame_set(1); bpy.context.view_layer.update()
    return ob_


# ---- mid-fold frame: the stand_up frame whose plate swing (tasset.L.0 vs pelvis) is closest to 70 deg while unfolding
K.assign_action(rig, bpy.data.actions["stand_up"])
act = bpy.data.actions["stand_up"]
best_f, best_d, swing_at = 1, 1e9, {}
for f in range(int(act.frame_range[0]), int(act.frame_range[1]) + 1):
    scene.frame_set(f)
    pbT, pbP = rig.pose.bones["tasset.L.0"], rig.pose.bones["pelvis"]
    GT = pbT.matrix @ pbT.bone.matrix_local.inverted(); GP = pbP.matrix @ pbP.bone.matrix_local.inverted()
    ang = math.degrees((GP.inverted() @ GT).to_quaternion().angle)
    swing_at[f] = ang
    if abs(ang - 70.0) < best_d:
        best_d, best_f = abs(ang - 70.0), f
F_MID = best_f
print("MIDFOLD stand_up frame %d swing %.1f deg" % (F_MID, swing_at[F_MID]))
rest_pose(rig); scene.frame_set(1); bpy.context.view_layer.update()

# ---- v4 standing rest alone
lo, hi = bbox([stand])
H_STAND = hi.z - lo.z
c, r = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
for tag, ang, elev, res, fill in (("front", 0.0, 5.0, 1024, 1.0), ("threequarter", 40.0, 15.0, 1024, 1.0),
                                  ("side", 90.0, 5.0, 1024, 1.0), ("back", 180.0, 8.0, 1024, 1.0),
                                  ("tactical", 40.0, 55.0, 256, 1.6)):
    aim(c, r, ang, elev, fill)
    shoot(os.path.join(OUT, "standing_%s.png" % tag), res)
ev_ = {b.name: (rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local) for b in rig.data.bones}
knee_c = (ev_["shin.L"][0] + ev_["shin.R"][0]) / 2 + Vector((0, 0, 0.12))
aim(knee_c, 1.15, 25.0, 12.0, 1.0); shoot(os.path.join(OUT, "closeup_knee_collars.png"), 1024)
ank_c = (ev_["foot.L"][0] + ev_["foot.R"][0]) / 2 + Vector((0, 0, 0.25))
aim(ank_c, 1.15, 25.0, 18.0, 1.0); shoot(os.path.join(OUT, "closeup_ankle_collars.png"), 1024)
hip_c = (ev_["thigh.L"][0] + ev_["thigh.R"][0]) / 2
sk_c = Vector((0.0, hip_c.y - 0.45, hip_c.z - 0.35))
aim(sk_c, 1.35, 15.0, 6.0, 1.0); shoot(os.path.join(OUT, "closeup_tassets_front.png"), 1024)

# ---- annotation overlay: the artist's camera (v2 front) riding with the trunk, x the cell refit scale
sk = REP["skirt"]; ac = sk["annotation_camera"]; S_FIT = REP["cell_refit"]["scale"]
CL2, CC2 = Vector(ac["loc_v2"]), Vector(ac["centre_v2"])
DT = Vector(ac["trunk_offset_m"])
RM = (CC2 - CL2).to_track_quat("-Z", "Y").to_matrix()
cam.location = (CL2 + DT) * S_FIT
cam.rotation_euler = RM.to_euler()
cam_d.clip_start = 0.05; cam_d.clip_end = 500
shoot(os.path.join(OUT, "_ann_raw.png"), 1024)
TRACE = json.load(open(os.path.join(PRJ, "design", "refs", "eldroot-v3-trace.json")))
T_ = math.tan(half_fov)
st_px = []
for x, z in TRACE["eldroot-v3-skirt-silhouette.png"]["world_xz_on_plane_y-0.90"]:
    v = RM.transposed() @ (Vector((x, -0.90, z)) - CL2)
    st_px.append((((v.x / -v.z) / T_ + 1) / 2 * 1024, (1 - (v.y / -v.z) / T_) / 2 * 1024))
img = bpy.data.images.load(os.path.join(OUT, "_ann_raw.png"))
px = np.array(img.pixels[:], dtype=np.float32).reshape(1024, 1024, 4)
for u, v in st_px:
    ui, vi = int(round(u)), int(round(v))
    for du in (-1, 0, 1):
        for dv in (-1, 0, 1):
            uu, vv = ui + du, vi + dv
            if 0 <= uu < 1024 and 0 <= vv < 1024:
                px[1023 - vv, uu, :3] = (1.0, 0.05, 0.05)
out_im = bpy.data.images.new("ann", 1024, 1024, alpha=True)
out_im.pixels.foreach_set(px.ravel())
out_im.filepath_raw = os.path.join(OUT, "annotation_overlay.png"); out_im.file_format = "PNG"; out_im.save()
bpy.data.images.remove(img); bpy.data.images.remove(out_im)
os.remove(os.path.join(OUT, "_ann_raw.png"))
print("WROTE", os.path.join(OUT, "annotation_overlay.png"), "W-check", json.dumps(sk.get("w_hem_recheck_split_plates", {}).get("row_error_px")))

# ---- snapshots: mid-fold + sit
fold = snapshot("stand_up", F_MID, "eldroot4_fold_snapshot")
sit = snapshot("sitting_idle", 1, "eldroot4_sit_snapshot")
lo_f, hi_f = bbox([fold]); lo_p, hi_p = bbox([sit]); H_SIT = hi_p.z - lo_p.z; H_FOLD = hi_f.z - lo_f.z
stand.hide_render = True
for ob_, tag_, lo_x, hi_x in ((sit, "sit", lo_p, hi_p), (fold, "fold", lo_f, hi_f)):
    others = [o for o in (sit, fold) if o is not ob_]
    for o in others:
        o.hide_render = True
    ob_.hide_render = False
    cs_, rs_ = (lo_x + hi_x) / 2, max((hi_x - lo_x).length / 2, 1e-3)
    for tag, ang, elev in (("threequarter", 40.0, 15.0), ("front", 0.0, 8.0), ("side", 90.0, 8.0)):
        aim(cs_, rs_, ang, elev, 1.0); shoot(os.path.join(OUT, "%s_%s.png" % (tag_, tag)), 1024)
fold.hide_render = False
fb_c = (lo_f + hi_f) / 2
fold_hip = Vector((0.0, fb_c.y - 0.2, lo_f.z + 0.45 * (hi_f.z - lo_f.z)))
sit.hide_render = True
aim(fold_hip, 1.6, 35.0, 12.0, 1.0); shoot(os.path.join(OUT, "closeup_tassets_fold.png"), 1024)
sit.hide_render = False
stand.hide_render = False

# ---- v3 STANDING (untouched blend)
with bpy.data.libraries.load(V3) as (src, dst):
    dst.objects = ["eldroot", "eldroot_rig"]
v3, v3_rig = dst.objects
scene.collection.objects.link(v3); scene.collection.objects.link(v3_rig)
rest_pose(v3_rig)
v3_rig.hide_render = True
bpy.context.view_layer.update()
lo_3, hi_3 = bbox([v3]); H_V3 = hi_3.z - lo_3.z
H_SEAT = REP["heights"]["seated_m"]
print("HEIGHTS v3 %.4f v4 %.4f fold %.4f sit %.4f ratio_v4 %.4f" % (H_V3, H_STAND, H_FOLD, H_SIT, H_STAND / H_SIT))

tm = bpy.data.materials.new("s_text"); tm.use_nodes = True
nt = tm.node_tree; nt.nodes.clear()
em = nt.nodes.new("ShaderNodeEmission"); em.inputs[0].default_value = (0.95, 0.9, 0.75, 1); em.inputs[1].default_value = 1.5
mo = nt.nodes.new("ShaderNodeOutputMaterial"); nt.links.new(em.outputs[0], mo.inputs[0])
labels = []


def label(text):
    cu = bpy.data.curves.new("lbl", "FONT"); cu.body = text; cu.size = 0.28; cu.align_x = "CENTER"; cu.align_y = "BOTTOM"
    cu.materials.append(tm)
    o = bpy.data.objects.new("lbl", cu); scene.collection.objects.link(o)
    o.visible_shadow = False
    con = o.constraints.new("TRACK_TO"); con.target = cam; con.track_axis = "TRACK_Z"; con.up_axis = "UP_Y"
    labels.append(o)
    return o


L_V3 = label("v3 standing  %.2f m" % H_V3)
L_V4 = label("v4 standing  %.2f m  =  %.2fx sit" % (H_STAND, H_STAND / H_SIT))
L_FD = label("v4 rising (plates ride the thighs)")
L_SIT = label("v4 sit  %.2f m" % H_SIT)
foot = max(hi.x - lo.x, hi.y - lo.y, hi_3.x - lo_3.x, hi_3.y - lo_3.y, hi_p.x - lo_p.x, hi_p.y - lo_p.y, hi_f.x - lo_f.x, hi_f.y - lo_f.y)
GAP = foot + 0.5
objs = [(v3_rig, v3, L_V3, hi_3.z), (rig, stand, L_V4, hi.z), (fold, fold, L_FD, hi_f.z), (sit, sit, L_SIT, hi_p.z)]


def place(axis):
    for k, (mover, mesh, lab, top) in enumerate(objs):
        off = (k - 1.5) * GAP
        v = Vector((off, 0, 0)) if axis == "x" else Vector((0, off, 0))
        mover.location = v
        lab.location = v + Vector((0, 0, top + 0.25))
    bpy.context.view_layer.update()


for tag, axis, ang, elev, res, fill in (("front", "x", 0.0, 5.0, 1800, 1.0), ("threequarter", "x", 40.0, 15.0, 1800, 1.0),
                                       ("side", "y", 90.0, 5.0, 1800, 1.0), ("tactical", "x", 40.0, 55.0, 640, 1.5)):
    place(axis)
    lo_a, hi_a = bbox([stand, v3, sit, fold])
    hi_a.z += 0.7
    c, r = (lo_a + hi_a) / 2, max((hi_a - lo_a).length / 2, 1e-3)
    for lb in labels:
        lb.hide_render = tag == "tactical"
    aim(c, r, ang, elev, fill)
    shoot(os.path.join(OUT, "compare_%s.png" % tag), res)
rig.location = (0, 0, 0)
sys.stdout.flush()
