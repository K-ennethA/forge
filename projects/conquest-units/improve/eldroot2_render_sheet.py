"""Eldroot standing v2 approval sheet: the seated ORIGINAL vs the v2 fold-back SIT pose vs the v2 STANDING rest.

    blender --background rigged/eldroot_standing2.blend --factory-startup --python eldroot2_render_sheet.py --
            <out_dir> <seated_original.blend>

Writes to <out_dir>:
  standing_front.png / standing_threequarter.png / standing_side.png / standing_tactical.png -- the standing
      rest alone (render_improved.py views + lighting).
  compare_front.png / compare_threequarter.png / compare_side.png / compare_tactical.png -- left to right:
      seated ORIGINAL (rigged/eldroot.blend rest, untouched), v2 SIT pose (sitting_idle frame 1, the fold-back),
      v2 STANDING rest; same floor, same scale, same camera. Each carries a floating label with its measured
      height; the standing label carries the standing/seated ratio.
In-memory only; never saves.
"""
import bpy, sys, os, math
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
OUT, SEATED = argv[0], argv[1]
os.makedirs(OUT, exist_ok=True)
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


# ---- standing rest alone
if rig.animation_data:
    rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
bpy.context.view_layer.update()
lo, hi = bbox([stand])
H_STAND = hi.z - lo.z
c, r = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
for tag, ang, elev, res, fill in (("front", 0.0, 5.0, 1024, 1.0), ("threequarter", 40.0, 15.0, 1024, 1.0),
                                  ("side", 90.0, 5.0, 1024, 1.0), ("tactical", 40.0, 55.0, 256, 1.6)):
    aim(c, r, ang, elev, fill)
    shoot(os.path.join(OUT, "standing_%s.png" % tag), res)

# ---- v2 SIT pose snapshot (sitting_idle frame 1, evaluated, frozen into its own object)
import sys as _s
_s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rigkit as K  # noqa: E402
K.assign_action(rig, bpy.data.actions["sitting_idle"])
scene.frame_set(1)
dg = bpy.context.evaluated_depsgraph_get()
sit_me = bpy.data.meshes.new_from_object(stand.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
sit = bpy.data.objects.new("eldroot2_sit_snapshot", sit_me); scene.collection.objects.link(sit)
sit.matrix_world = stand.matrix_world.copy()
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
bpy.context.view_layer.update()

# ---- seated ORIGINAL
with bpy.data.libraries.load(SEATED) as (src, dst):
    dst.objects = ["eldroot", "eldroot_rig"]
seated, seated_rig = dst.objects
scene.collection.objects.link(seated); scene.collection.objects.link(seated_rig)
if seated_rig.animation_data:
    seated_rig.animation_data.action = None
seated_rig.hide_render = True
bpy.context.view_layer.update()
lo_s, hi_s = bbox([seated]); H_SEAT = hi_s.z - lo_s.z
lo_p, hi_p = bbox([sit]); H_SIT = hi_p.z - lo_p.z
print("HEIGHTS seated %.4f sit_pose %.4f standing %.4f ratio %.4f" % (H_SEAT, H_SIT, H_STAND, H_STAND / H_SEAT))

# ---- labels (emissive text, billboarded to the camera)
tm = bpy.data.materials.new("s_text"); tm.use_nodes = True
nt = tm.node_tree; nt.nodes.clear()
em = nt.nodes.new("ShaderNodeEmission"); em.inputs[0].default_value = (0.95, 0.9, 0.75, 1); em.inputs[1].default_value = 1.5
mo = nt.nodes.new("ShaderNodeOutputMaterial"); nt.links.new(em.outputs[0], mo.inputs[0])
labels = []


def label(text):
    cu = bpy.data.curves.new("lbl", "FONT"); cu.body = text; cu.size = 0.30; cu.align_x = "CENTER"; cu.align_y = "BOTTOM"
    cu.materials.append(tm)
    o = bpy.data.objects.new("lbl", cu); scene.collection.objects.link(o)
    o.visible_shadow = False
    con = o.constraints.new("TRACK_TO"); con.target = cam; con.track_axis = "TRACK_Z"; con.up_axis = "UP_Y"
    labels.append(o)
    return o


L_SEAT = label("seated original  %.2f m" % H_SEAT)
L_SIT = label("v2 sit (fold-back)  %.2f m" % H_SIT)
L_STAND = label("v2 standing  %.2f m  =  %.2fx" % (H_STAND, H_STAND / H_SEAT))
foot = max(hi.x - lo.x, hi.y - lo.y, hi_s.x - lo_s.x, hi_s.y - lo_s.y, hi_p.x - lo_p.x, hi_p.y - lo_p.y)
GAP = foot + 0.6
objs = [(seated_rig, seated, L_SEAT, hi_s.z), (sit, sit, L_SIT, hi_p.z), (rig, stand, L_STAND, hi.z)]


def place(axis):
    for k, (mover, mesh, lab, top) in enumerate(objs):
        off = (k - 1) * GAP
        v = Vector((off, 0, 0)) if axis == "x" else Vector((0, off, 0))   # side cam on +X: image-right = +Y
        mover.location = v
        lab.location = v + Vector((0, 0, top + 0.25))
    bpy.context.view_layer.update()


for tag, axis, ang, elev, res, fill in (("front", "x", 0.0, 5.0, 1600, 1.0), ("threequarter", "x", 40.0, 15.0, 1600, 1.0),
                                       ("side", "y", 90.0, 5.0, 1600, 1.0), ("tactical", "x", 40.0, 55.0, 512, 1.5)):
    place(axis)
    lo_a, hi_a = bbox([stand, seated, sit])
    hi_a.z += 0.7
    c, r = (lo_a + hi_a) / 2, max((hi_a - lo_a).length / 2, 1e-3)
    for lb in labels:
        lb.hide_render = tag == "tactical"
    aim(c, r, ang, elev, fill)
    shoot(os.path.join(OUT, "compare_%s.png" % tag), res)
rig.location = (0, 0, 0)
sys.stdout.flush()
