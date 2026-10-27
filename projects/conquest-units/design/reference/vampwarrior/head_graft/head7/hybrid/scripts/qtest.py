import bpy, bmesh
def mk():
    bpy.ops.mesh.primitive_monkey_add(); ob = bpy.context.active_object
    ob.data.remesh_voxel_size = 0.02; bpy.ops.object.voxel_remesh(); return ob
for fix in ("merge", "tri", "none_smooth", "decim"):
    for o in list(bpy.data.objects): bpy.data.objects.remove(o)
    ob = mk()
    bm = bmesh.new(); bm.from_mesh(ob.data)
    if fix == "merge": bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    if fix == "tri": bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bad = sum(1 for e in bm.edges if len(e.link_faces) != 2); badv = sum(1 for v in bm.verts if not v.is_manifold)
    bm.to_mesh(ob.data); bm.free()
    if fix == "decim":
        m = ob.modifiers.new("d", "DECIMATE"); m.ratio = 0.5; bpy.ops.object.modifier_apply(modifier="d")
    r = bpy.ops.object.quadriflow_remesh(target_faces=2000, mode="FACES")
    print("QTEST", fix, bad, badv, r, len(ob.data.polygons))
