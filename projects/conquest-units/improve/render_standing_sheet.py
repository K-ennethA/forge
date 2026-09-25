"""Eldroot standing silhouette sheet (the artist's approval point for the standing rest).

    blender --background rigged/eldroot_standing.blend --factory-startup --python render_standing_sheet.py --
            <out_dir> <seated_original.blend>

Writes to <out_dir>:
  standing_front.png / standing_threequarter.png / standing_tactical.png  -- the standing REST,
      render_improved.py's exact views + lighting (front 0/5 deg 1024 px, threequarter 40/15,
      tactical 40/55 256 px fill 1.6).
  compare_front.png / compare_threequarter.png / compare_side.png / compare_tactical.png  -- the
      seated ORIGINAL (appended from rigged/eldroot.blend, untouched, rest pose) beside the
      standing rest, same floor, same scale, same camera (left = seated, right = standing).
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
if rig.animation_data:
    rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)

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


def bbox(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((1e9, 1e9, 1e9)); hi = -lo
    for o in objs:
        for c in o.evaluated_get(dg).bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
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


# ---- standing alone (render_improved.py views)
lo, hi = bbox([stand])
c, r = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
for tag, ang, elev, res, fill in (("front", 0.0, 5.0, 1024, 1.0), ("threequarter", 40.0, 15.0, 1024, 1.0), ("tactical", 40.0, 55.0, 256, 1.6)):
    aim(c, r, ang, elev, fill)
    shoot(os.path.join(OUT, "standing_%s.png" % tag), res)

# ---- side by side with the seated ORIGINAL
with bpy.data.libraries.load(SEATED) as (src, dst):
    dst.objects = ["eldroot", "eldroot_rig"]
seated, seated_rig = dst.objects
scene.collection.objects.link(seated); scene.collection.objects.link(seated_rig)
if seated_rig.animation_data:
    seated_rig.animation_data.action = None
seated_rig.hide_render = True
GAP = 4.3


def place(axis):
    off = Vector((GAP, 0, 0)) if axis == "x" else Vector((0, GAP, 0))    # side cam on +X: image-right = +Y
    seated_rig.location = -off / 2
    rig.location = off / 2
    bpy.context.view_layer.update()


for tag, axis, ang, elev, res, fill in (("front", "x", 0.0, 5.0, 1400, 1.0), ("threequarter", "x", 40.0, 15.0, 1400, 1.0),
                                       ("side", "y", 90.0, 5.0, 1400, 1.0), ("tactical", "x", 40.0, 55.0, 384, 1.5)):
    place(axis)
    lo, hi = bbox([stand, seated])
    c, r = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
    aim(c, r, ang, elev, fill)
    shoot(os.path.join(OUT, "compare_%s.png" % tag), res)
rig.location = (0, 0, 0)
sys.stdout.flush()
