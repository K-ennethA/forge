# rawlook_parts.py: blender -b --python rawlook_parts.py -- <out_prefix> <glb>[@<4x4 json matrix file>] ...
# rawlook_fsb.py's exact cameras / lighting (workbench STUDIO, lens 70, 1000 px, bbox-framed) + a 3/4 view (az 45); raw meshes, no cleanup.
import bpy, sys, math, mathutils, json
args = sys.argv[sys.argv.index("--") + 1:]; out = args[0]
bpy.ops.wm.read_factory_settings(use_empty=True)
for spec in args[1:]:
    glb, _, mfile = spec.partition("@")
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=glb)
    if mfile:
        M = mathutils.Matrix(json.load(open(mfile))["matrix"])
        for o in set(bpy.context.scene.objects) - before:
            if o.parent is None: o.matrix_world = M @ o.matrix_world
bpy.context.view_layer.update()
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
    for name, az in (("front", 0), ("side", 90), ("back", 180), ("q34", 45)):
        a = math.radians(az); d = mathutils.Vector((math.sin(a), -math.cos(a), 0.05))
        cam.location = ctr + d * R * 2.05
        cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = "%s_%s_%s.png" % (out, mode.lower(), name)
        bpy.ops.render.render(write_still=True)
