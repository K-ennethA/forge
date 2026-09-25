"""Preview renders of an improved unit with the SURVEY's exact lighting/camera (before/after compare).

    blender --background <improved/X.blend> --factory-startup --python render_improved.py -- <out_prefix> [closeup]

Views (identical to survey/survey.py): front (camera on -Y = the TRUE front after the facing
fix, 5 deg up, 1024 px), threequarter (40 deg, 15 deg up, 1024 px), tactical (40 deg, 55 deg
down, 256 px, fill 1.6 -- unit ~100 px tall, docs/BLENDER_RIGGING.md gameplay zoom).
Optional 'closeup': Eldroot head bake-detail view (1024 px, head band framed).
Lights: key/fill/rim suns + grey world, Standard view transform (copied from survey.py,
which copied projects/werewolf/export/game-drop/render_look.py). In-memory only; never saves.
"""
import bpy, sys, math
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
CLOSEUP = len(argv) > 1 and argv[1] == "closeup"
scene = bpy.context.scene
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    for c in o.evaluated_get(dg).bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
size = hi - lo

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
rig = bpy.data.objects.new("survey_rig", None); scene.collection.objects.link(rig)


def light(name, energy, rot_deg, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot_deg]
    o.parent = rig


light("s_key", 3.2, (50, 0, 150)); light("s_fill", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)); light("s_rim", 2.0, (60, 0, 10))
rig.rotation_euler = (0, 0, math.radians(180))

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
cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)


def aim(angle_deg, elev_deg, fill=1.08, c=None, rad=None):
    c = centre if c is None else c
    rad = radius if rad is None else rad
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    dist = rad / math.sin(half_fov) * fill
    d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = c + d * dist
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()


views = [("front", 0.0, 5.0, 1024, 1.0, None, None), ("threequarter", 40.0, 15.0, 1024, 1.0, None, None),
         ("tactical", 40.0, 55.0, 256, 1.6, None, None)]
if CLOSEUP:
    # head band (top 45%) of the boss, seen 25 deg off the front so eye hollows + side bark both show
    hc = Vector((centre.x, centre.y, lo.z + size.z * 0.78))
    views.append(("closeup", 25.0, 8.0, 1024, 1.0, hc, size.z * 0.30))
for tag, ang, elev, res, fill, c, rad in views:
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    aim(ang, elev, fill, c, rad)
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
