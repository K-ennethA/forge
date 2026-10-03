# Blender headless: build a cube with TWO color attributes and export a glb.
# COLOR_0 ("Col0") = all red (1,0,0,1)
# COLOR_1 ("Glow")  = per-vertex: R = vertex index / 7, G = 0.25, B = 0.75, A = 1
import bpy, bmesh, os, sys

out_dir = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(out_dir, "probe_project", "probe.glb")
os.makedirs(os.path.dirname(out), exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
me = bpy.data.meshes.new("ProbeMesh")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
bm.to_mesh(me)
bm.free()
ob = bpy.data.objects.new("Probe", me)
bpy.context.scene.collection.objects.link(ob)

# POINT-domain FLOAT_COLOR (linear, no sRGB conversion ambiguity)
c0 = me.color_attributes.new("Col0", 'FLOAT_COLOR', 'POINT')
c1 = me.color_attributes.new("Glow", 'FLOAT_COLOR', 'POINT')
for v in me.vertices:
    c0.data[v.index].color = (1.0, 0.0, 0.0, 1.0)
    c1.data[v.index].color = (v.index / 7.0, 0.25, 0.75, 1.0)
me.color_attributes.active_color = c0
me.color_attributes.render_color_index = 0
print("VERTS", [(v.index, tuple(round(x, 3) for x in v.co)) for v in me.vertices])

bpy.ops.object.select_all(action='DESELECT')
ob.select_set(True)
bpy.context.view_layer.objects.active = ob

kw = dict(filepath=out, export_format='GLB', use_selection=True)
# Blender 4.2+/5.0 color export knobs; try the "all color attributes" mode.
import inspect
props = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
print("COLOR PROPS", [p for p in props if 'color' in p.lower() or 'vertex' in p.lower() or 'attribute' in p.lower()])
if 'export_vertex_color' in props:
    kw['export_vertex_color'] = 'ACTIVE'
if 'export_all_vertex_colors' in props:
    kw['export_all_vertex_colors'] = True
if 'export_active_vertex_color_when_no_material' in props:
    kw['export_active_vertex_color_when_no_material'] = True
if 'export_attributes' in props:
    kw['export_attributes'] = True
print("EXPORT KW", kw)
bpy.ops.export_scene.gltf(**kw)
print("WROTE", out)
