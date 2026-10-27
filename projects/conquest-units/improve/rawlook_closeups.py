# rawlook_closeups.py: blender --background --python rawlook_closeups.py -- <glb> <out_prefix>  -> full-length front / side / back + head close-ups
import bpy, sys, math, mathutils
# r2.py <glb> <out_prefix>: full-length front / side / back (ortho) + head close-ups front / 35 / 90 (untextured + textured)
glb, out = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
pts = [o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)]); hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
ctr = (lo + hi) / 2; H = hi.z - lo.z
sc = bpy.context.scene; sc.render.engine = "BLENDER_WORKBENCH"
sh = sc.display.shading; sh.light = "STUDIO"; sh.show_cavity = False
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam


def shot(name, az, target, ortho_scale=None, dist=None, res=(700, 1000)):
    sc.render.resolution_x, sc.render.resolution_y = res
    a = math.radians(az); d = mathutils.Vector((math.sin(a), -math.cos(a), 0.0))
    if ortho_scale:
        cam.data.type = "ORTHO"; cam.data.ortho_scale = ortho_scale; cam.location = target + d * H * 3
    else:
        cam.data.type = "PERSP"; cam.data.lens = 85; cam.location = target + d * dist
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    for mode in ("SINGLE", "TEXTURE"):
        sh.color_type = mode
        if mode == "SINGLE": sh.single_color = (0.7, 0.7, 0.7)
        sc.render.filepath = "%s_%s_%s.png" % (out, name, mode.lower())
        bpy.ops.render.render(write_still=True)


for name, az in (("full_front", 0), ("full_side", 90), ("full_back", 180)):
    shot(name, az, ctr, ortho_scale=H * 1.05)
head = mathutils.Vector((ctr.x, ctr.y, hi.z - 0.075 * H))
for name, az in (("head_front", 0), ("head_q35", 35), ("head_side", 90)):
    shot(name, az, head, dist=H * 0.75, res=(800, 800))
