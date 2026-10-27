# blender --background --python neck_slices.py -- <glb> [z_lo z_hi]
# Horizontal cross-sections of the mesh (glb y = blender z). For each height: the loops (connected cut edges),
# the loop that encloses the neck axis = the neck; other loops near it (collar, cape) are reported by bbox.
import bpy, sys, bmesh, mathutils, math
args = sys.argv[sys.argv.index("--") + 1:]; glb = args[0]
z_lo, z_hi = (float(args[1]), float(args[2])) if len(args) > 2 else (0.30, 0.42)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
o = [x for x in bpy.context.scene.objects if x.type == "MESH"][0]
me = o.data; mw = o.matrix_world
bm0 = bmesh.new(); bm0.from_mesh(me); bm0.transform(mw); bmesh.ops.remove_doubles(bm0, verts=bm0.verts, dist=1e-5); bm0.edges.index_update()
# neck axis: centroid of verts in a thin slab just under the chin is unknown; use head centroid x/y from verts with z in (0.40, 0.44)
hv = [v.co for v in bm0.verts if 0.40 < v.co.z < 0.44 and abs(v.co.x) < 0.05]
ax = sum(p.x for p in hv) / len(hv); ay = sum(p.y for p in hv) / len(hv)
print("axis x %.4f y %.4f" % (ax, ay))
def loops_at(z):
    pts = []
    for e in bm0.edges:
        a, b = e.verts[0].co, e.verts[1].co
        if (a.z - z) * (b.z - z) < 0:
            t = (z - a.z) / (b.z - a.z); p = a.lerp(b, t)
            pts.append((e, p))
    # connect cut points through shared faces
    idx = {e.index: i for i, (e, p) in enumerate(pts)}
    parent = list(range(len(pts)))
    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, (e, p) in enumerate(pts):
        for fc in e.link_faces:
            for e2 in fc.edges:
                j = idx.get(e2.index)
                if j is not None and j != i: parent[f(i)] = f(j)
    comps = {}
    for i, (e, p) in enumerate(pts): comps.setdefault(f(i), []).append(p)
    return list(comps.values())
def inside(poly, x, y):
    # crude: point in bbox and angular winding using sorted angle coverage
    angs = sorted(math.atan2(p.y - y, p.x - x) for p in poly)
    gaps = [b - a for a, b in zip(angs, angs[1:])] + [angs[0] + 2 * math.pi - angs[-1]]
    return max(gaps) < 1.0
n = int(round((z_hi - z_lo) / 0.0025))
for k in range(n + 1):
    z = z_hi - k * 0.0025
    L = loops_at(z); rep = []
    neck = None
    for c in L:
        xs = [p.x for p in c]; ys = [p.y for p in c]
        if min(xs) - 0.12 > ax or max(xs) + 0.12 < ax: continue
        bb = (min(xs), max(xs), min(ys), max(ys))
        enc = inside(c, ax, ay)
        rep.append((enc, bb, len(c)))
    encl = sorted([r for r in rep if r[0]], key=lambda r: (r[1][1] - r[1][0]))
    s = "z %.4f  loops %d" % (z, len(rep))
    if encl:
        bb = encl[0][1]; s += "  innermost-enclosing w %.4f d %.4f (x %.4f..%.4f, y %.4f..%.4f)" % (bb[1] - bb[0], bb[3] - bb[2], *bb)
        others = [r for r in rep if r is not encl[0]]
        if others: s += "  | others: " + "; ".join("%s w%.3f d%.3f" % ("E" if r[0] else "-", r[1][1] - r[1][0], r[1][3] - r[1][2]) for r in others[:5])
    print(s)
