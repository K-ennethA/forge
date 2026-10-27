"""qflow.py -- <src.npz> <out.npz> <target_faces> [main_only 0|1]: Blender QuadriFlow remesh (triangulated output)"""
import sys, os, time, numpy as np, bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); tgt = int(a[2]); main = len(a) > 3 and a[3] == "1"
F = Z["F"]
if main:
    F = F[Z["flab"] == 0]
U.clear()
ob = U.obj("m", Z["V"], F)
bpy.context.view_layer.objects.active = ob; ob.select_set(True)
import bmesh
if len(a) > 4 and a[4] == "1":      # manifold cleanup loop
    bm = bmesh.new(); bm.from_mesh(ob.data)
    def largest():
        seen = set(); comps = []
        for f in bm.faces:
            if f in seen: continue
            st = [f]; c = []; seen.add(f)
            while st:
                g = st.pop(); c.append(g)
                for e in g.edges:
                    for h in e.link_faces:
                        if h not in seen:
                            seen.add(h); st.append(h)
            comps.append(c)
        comps.sort(key=len)
        bmesh.ops.delete(bm, geom=[f for c in comps[:-1] for f in c], context="FACES")
        return len(comps)
    for it in range(10):
        bad = set()
        for e in bm.edges:
            if len(e.link_faces) > 2: bad.update(e.link_faces)
        for v in bm.verts:
            if v.link_faces and not v.is_manifold and not v.is_boundary: bad.update(v.link_faces)
        bmesh.ops.delete(bm, geom=list(bad), context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        np_ = largest()
        # boundary verts touching >1 boundary loop -> drop their faces too
        bv = [v for v in bm.verts if v.is_boundary and sum(1 for e in v.link_edges if e.is_boundary) > 2]
        bmesh.ops.delete(bm, geom=list({f for v in bv for f in v.link_faces}), context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        nb = sum(1 for e in bm.edges if e.is_boundary); nm = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        nv = sum(1 for v in bm.verts if not v.is_manifold)
        print("CLEAN it", it, "faces", len(bm.faces), "boundary", nb, ">2", nm, "nonmanifold verts", nv, "pieces", np_)
        if nb == 0 and nm == 0 and nv == 0:
            break
    bm.to_mesh(ob.data); bm.free(); ob.data.update()
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_non_manifold(); bpy.ops.mesh.fill_holes(sides=0)
    bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.mesh.select_non_manifold()
    try:
        bpy.ops.mesh.fill(use_beauty=True)
    except Exception as e:
        print("fill failed", e)
    bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.mesh.select_non_manifold()
    bpy.ops.object.mode_set(mode="OBJECT")
    print("AFTER fill_holes non-manifold verts selected", sum(1 for v in ob.data.vertices if v.select))
    for e in [e for e in ob.data.edges if e.select][:5]:
        print("  sel edge", [tuple(round(c, 3) for c in ob.data.vertices[i].co) for i in e.vertices])
if len(a) > 5 and float(a[5]) > 0:   # watertight pre-pass: Blender voxel remesh at this voxel size
    ob.data.remesh_voxel_size = float(a[5]); ob.data.use_remesh_fix_poles = True
    bpy.ops.object.voxel_remesh()
    print("VOXEL", a[5], "faces", len(ob.data.polygons))
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.mesh.select_non_manifold()
    bpy.ops.object.mode_set(mode="OBJECT")
    nsel = sum(1 for v in ob.data.vertices if v.select)
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.delete(type="VERT"); bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.fill_holes(sides=0)
    bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.mesh.select_non_manifold()
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts = sorted([o for o in bpy.data.objects if o.type == "MESH"], key=lambda o: len(o.data.polygons))
    print("VOXEL shells", [len(o.data.polygons) for o in parts][-5:], len(parts))
    for o in parts[:-1]:
        bpy.data.objects.remove(o, do_unlink=True)
    ob = parts[-1]; bpy.context.view_layer.objects.active = ob; ob.select_set(True)
    print("VOXEL nonmanifold verts", nsel, "after delete+fill", sum(1 for v in ob.data.vertices if v.select))
t = time.time()
try:
    r = bpy.ops.object.quadriflow_remesh(target_faces=tgt, use_mesh_symmetry=False, use_preserve_sharp=True, use_preserve_boundary=True, preserve_attributes=False, seed=0, mode="FACES")
    print("QFLOW result", r, "faces", len(ob.data.polygons), "s", round(time.time() - t, 1))
except Exception as e:
    print("QFLOW failed", repr(e)[:300])
V, T = U.arrays(ob)
q = sum(1 for p in ob.data.polygons if p.loop_total == 4)
np.savez_compressed(a[1], V=V, F=T)
print("QFLOW out polys", len(ob.data.polygons), "quads", q, "tris", len(T))
