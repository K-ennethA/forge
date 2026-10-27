# blender --background --python neck_loops.py -- <glb> z_lo z_hi step
# Every cross-section loop near the body's centre column (|x centre| < 0.06): centre, width (x), depth (y), and the number of
# loops it encloses / is enclosed by (bbox nesting) -> shows nested walls (double shells) of the neck and collar;
# OPENn = n open ends (a cut through an open sheet / boundary), closed = a closed loop.
import bpy, sys, bmesh
a = sys.argv[sys.argv.index("--") + 1:]; glb = a[0]; z_lo, z_hi, step = map(float, a[1:4])
bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.gltf(filepath=glb)
o = [x for x in bpy.context.scene.objects if x.type == "MESH"][0]
bm = bmesh.new(); bm.from_mesh(o.data); bm.transform(o.matrix_world); bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5); bm.edges.index_update()
E = list(bm.edges)
def loops_at(z):
    pts = []
    for e in E:
        p, q = e.verts[0].co, e.verts[1].co
        if (p.z - z) * (q.z - z) < 0:
            t = (z - p.z) / (q.z - p.z); pts.append((e, p.lerp(q, t)))
    idx = {e.index: i for i, (e, p) in enumerate(pts)}; par = list(range(len(pts)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    deg = [0] * len(pts)
    for i, (e, p) in enumerate(pts):
        nb = set()
        for fc in e.link_faces:
            for e2 in fc.edges:
                j = idx.get(e2.index)
                if j is not None and j != i: par[f(i)] = f(j); nb.add(j)
        deg[i] = len(nb)
    C = {}; O = {}
    for i, (e, p) in enumerate(pts): C.setdefault(f(i), []).append(p); O[f(i)] = O.get(f(i), 0) + (deg[i] < 2)
    out = []
    for r, c in C.items():
        if len(c) < 6: continue
        xs = [p.x for p in c]; ys = [p.y for p in c]
        out.append((min(xs), max(xs), min(ys), max(ys), O[r]))
    return out
n = int(round((z_hi - z_lo) / step))
for k in range(n + 1):
    z = z_hi - k * step
    L = [b for b in loops_at(z) if abs((b[0] + b[1]) / 2) < 0.06 and (b[1] - b[0]) < 0.30]
    L.sort(key=lambda b: (b[1] - b[0]) * (b[3] - b[2]))
    desc = []
    for b in L:
        inside = sum(1 for c in L if c is not b and c[0] <= b[0] and c[1] >= b[1] and c[2] <= b[2] and c[3] >= b[3])
        desc.append("w%.3f d%.3f c(%.3f,%.3f) nest%d %s" % (b[1] - b[0], b[3] - b[2], (b[0] + b[1]) / 2, (b[2] + b[3]) / 2, inside, "OPEN%d" % b[4] if b[4] else "closed"))
    print("z %.4f | " % z + " ; ".join(desc[:7]))
