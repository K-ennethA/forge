import bpy, sys, bmesh
a = sys.argv[sys.argv.index("--") + 1:]; glb = a[0]; zs = [float(x) for x in a[1:]]
bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.gltf(filepath=glb)
o = [x for x in bpy.context.scene.objects if x.type == "MESH"][0]
bm = bmesh.new(); bm.from_mesh(o.data); bm.transform(o.matrix_world); bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5); bm.edges.index_update()
for z in zs:
    pts = []
    for e in bm.edges:
        p, q = e.verts[0].co, e.verts[1].co
        if (p.z - z) * (q.z - z) < 0: pts.append((e, p.lerp(q, (z - p.z) / (q.z - p.z))))
    idx = {e.index: i for i, (e, p) in enumerate(pts)}; par = list(range(len(pts)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i, (e, p) in enumerate(pts):
        for fc in e.link_faces:
            for e2 in fc.edges:
                j = idx.get(e2.index)
                if j is not None: par[f(i)] = f(j)
    C = {}
    for i, (e, p) in enumerate(pts): C.setdefault(f(i), []).append(p)
    L = sorted([(min(p.x for p in c), max(p.x for p in c), min(p.y for p in c), max(p.y for p in c), len(c)) for c in C.values() if len(c) >= 8])
    print("Z %.3f: " % z + " ; ".join("x[%.3f,%.3f] y[%.3f,%.3f]" % l[:4] for l in L))
