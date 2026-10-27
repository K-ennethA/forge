# blender --background --python measure_neck.py -- <glb> <out_prefix>
# Orthographic front + side renders of the head/neck/collar band with known scale (pixel row -> glb Y), plus slice widths.
import bpy, sys, math, mathutils, json
import bmesh
glb, out = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
dg = bpy.context.evaluated_depsgraph_get()
V = []
for o in objs:
    m = o.evaluated_get(dg).to_mesh(); mw = o.matrix_world
    V += [mw @ v.co for v in m.vertices]
lo = mathutils.Vector([min(p[i] for p in V) for i in range(3)]); hi = mathutils.Vector([max(p[i] for p in V) for i in range(3)])
print("BBOX blender lo", tuple(round(x, 4) for x in lo), "hi", tuple(round(x, 4) for x in hi))
# glTF Y-up -> Blender Z-up (glb y = blender z)
H = hi.z - lo.z; z0 = hi.z - 0.24 * H; z1 = hi.z + 0.01 * H      # top 24% = head, neck, collar, shoulders
cx = (lo.x + hi.x) / 2
# slice table: for each z, x-extent and y-extent of vertices within the central column (|x-cx| < 0.12*H)
rows = []
N = 120
for i in range(N + 1):
    z = z0 + (z1 - z0) * i / N; dz = (z1 - z0) / N / 2
    pts = [p for p in V if abs(p.z - z) <= dz]
    if not pts: continue
    xs = [p.x for p in pts]; ys = [p.y for p in pts]
    c = [p for p in pts if abs(p.x - cx) < 0.04 * H]
    rows.append({"y_glb": round(z, 4), "n": len(pts), "xmin": round(min(xs), 4), "xmax": round(max(xs), 4),
                 "front_y": round(min(ys), 4), "back_y": round(max(ys), 4),
                 "centre_front": round(min(p.y for p in c), 4) if c else None, "centre_back": round(max(p.y for p in c), 4) if c else None})
json.dump({"lo": list(lo), "hi": list(hi), "rows": rows}, open(out + "_slices.json", "w"), indent=1)
sc = bpy.context.scene; sc.render.engine = "BLENDER_WORKBENCH"
sh = sc.display.shading; sh.light = "STUDIO"; sh.color_type = "TEXTURE"
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.type = "ORTHO"; span = z1 - z0; cam.data.ortho_scale = span
sc.render.resolution_x = sc.render.resolution_y = 1000
zc = (z0 + z1) / 2; cy = (lo.y + hi.y) / 2
for name, loc, rot in (("front", (cx, lo.y - 3, zc), (math.pi / 2, 0, 0)), ("side", (lo.x - 3, cy, zc), (math.pi / 2, 0, -math.pi / 2)), ("back", (cx, hi.y + 3, zc), (math.pi / 2, 0, math.pi))):
    cam.location = loc; cam.rotation_euler = rot
    sc.render.filepath = "%s_ortho_%s.png" % (out, name); bpy.ops.render.render(write_still=True)
print("ORTHO row r -> y_glb = %.5f - r * %.6f  (1000 px over %.4f)" % (z1, span / 1000, span))
json.dump({"z_top": z1, "per_px": span / 1000}, open(out + "_ortho_map.json", "w"))
