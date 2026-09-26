"""Duskmaw preview renders, the SURVEY's lighting/camera rules (render_improved.py copied, not imported, so this
lane never touches the shared file). In-memory only; never saves.

    blender --background <blend> --factory-startup --python duskmaw_render.py -- <out_prefix> [views] [--glb <path> --yaw <deg>]

views: comma list of front, threequarter, tactical, maw, back, side, backthreequarter, mawback, lowfront, lowback, head,
    headthreequarter (default front,threequarter,tactical,maw).
--skin <unit>:<skin>: repaint in memory from palettes/<unit>/<skin>.json before rendering (palette variants).
--glb: clear the scene, import the shipped glb and yaw it (the shipped monster.glb imports facing -Y: yaw 0 shows
its TRUE front on the -Y camera; the survey game tiles used the roster yaw 180 and rendered its back).
maw closeup: front camera (0 deg, 4 deg up) framed on the chest-to-skirt band (mouth illusion) -- the 14-40% height band at
    the model centre for BOTH the rework and the shipped glb, so both sides frame the same anatomy.
"""
import bpy, sys, math
import numpy as np
from mathutils import Vector, Matrix

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
VIEWS = argv[1].split(",") if len(argv) > 1 and not argv[1].startswith("--") else ["front", "threequarter", "tactical", "maw"]
scene = bpy.context.scene
if "--glb" in argv:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=argv[argv.index("--glb") + 1])
    yaw = math.radians(float(argv[argv.index("--yaw") + 1])) if "--yaw" in argv else 0.0
    for o in scene.objects:
        if o.parent is None:
            o.matrix_world = Matrix.Rotation(yaw, 4, "Z") @ o.matrix_world
for o in scene.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
if any(o.type == "ARMATURE" for o in scene.objects):
    # the glTF importer adds an unparented, unskinned 2 m 'Icosphere' (bone display shape): not part of the model
    for o in list(scene.objects):
        if o.type == "MESH" and o.parent is None and len(o.vertex_groups) == 0 and o.name.startswith("Icosphere"):
            bpy.data.objects.remove(o, do_unlink=True)
if "--skin" in argv:                      # in-memory palette swap (palettes.py, read-only use); never saved
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    import palettes as PAL
    PAL.repaint_scene(argv[argv.index("--skin") + 1].split(":")[0], argv[argv.index("--skin") + 1].split(":")[1])
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
dg = bpy.context.evaluated_depsgraph_get()
lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    for c in o.evaluated_get(dg).bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
size = hi - lo


def region_bounds(pred):
    for o in meshes:
        me = o.data
        if "region_id" not in me.attributes:
            continue
        names = list(me["conquest_regions"])
        rid = np.empty(len(me.polygons), dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", rid)
        fc = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("center", fc); fc = fc.reshape(-1, 3)
        m = np.array([pred(names[r]) for r in rid])
        if m.any():
            P = fc[m] @ np.array(o.matrix_world)[:3, :3].T + np.array(o.matrix_world)[:3, 3]
            return Vector(P.min(0)), Vector(P.max(0))
    return None


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


table = {"front": (0.0, 5.0, 1024, 1.0, None, None), "threequarter": (40.0, 15.0, 1024, 1.0, None, None),
         "tactical": (40.0, 55.0, 256, 1.6, None, None),
         "back": (180.0, 5.0, 1024, 1.0, None, None), "side": (90.0, 5.0, 1024, 1.0, None, None),
         "backthreequarter": (140.0, 15.0, 1024, 1.0, None, None)}
mb = (Vector((lo.x, lo.y, lo.z + 0.14 * size.z)), Vector((hi.x, hi.y, lo.z + 0.40 * size.z)))
mc = (mb[0] + mb[1]) / 2
mr = 0.30 * size.x
table["maw"] = (0.0, 4.0, 1024, 1.0, Vector((0.0, mc.y, mc.z)), mr)
table["mawback"] = (180.0, 4.0, 1024, 1.0, Vector((0.0, mc.y, mc.z)), mr)
# low-angle front / back: the camera sits below the mouth band looking UP through it (background through the maw = see-through)
table["lowfront"] = (0.0, -6.0, 1024, 1.0, Vector((0.0, mc.y, lo.z + 0.30 * size.z)), 0.36 * size.x)
table["lowback"] = (180.0, -6.0, 1024, 1.0, Vector((0.0, mc.y, lo.z + 0.30 * size.z)), 0.36 * size.x)
# head closeup: the head/hat band (58-100 % of the height) on the front camera
table["head"] = (0.0, 4.0, 1024, 1.0, Vector((0.0, mc.y, lo.z + 0.645 * size.z)), 0.19 * size.z)
table["headthreequarter"] = (30.0, 6.0, 1024, 1.0, Vector((0.0, mc.y, lo.z + 0.645 * size.z)), 0.19 * size.z)
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
