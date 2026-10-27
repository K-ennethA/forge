# rawlook_views.py: blender --background --python rawlook_views.py -- <glb> <out_prefix>  -> untextured + textured front / 3/4 / side
import bpy, sys, math, mathutils
glb, out = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
pts = [o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)]); hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
ctr = (lo + hi) / 2; R = (hi - lo).length
sc = bpy.context.scene; sc.render.engine = "BLENDER_WORKBENCH"; sc.render.resolution_x = sc.render.resolution_y = 1000
sh = sc.display.shading; sh.light = "STUDIO"; sh.show_cavity = False
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.lens = 70
for mode in ("SINGLE", "TEXTURE"):
    sh.color_type = mode
    if mode == "SINGLE": sh.single_color = (0.7, 0.7, 0.7)
    for name, az in (("front", 0), ("q34", 35), ("side", 90)):
        a = math.radians(az); d = mathutils.Vector((math.sin(a), -math.cos(a), 0.05))
        cam.location = ctr + d * R * 1.55
        cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = "%s_%s_%s.png" % (out, mode.lower(), name)
        bpy.ops.render.render(write_still=True)
