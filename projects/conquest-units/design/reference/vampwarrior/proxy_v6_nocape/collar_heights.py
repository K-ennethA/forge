# blender --background --python collar_heights.py -- <glb> z_lo z_hi step
# Per height: the neck loops (centred, < 0.06 wide) and the collar (every other loop around the neck). Reports the highest
# height where the collar has material in front of / behind / left / right of the neck, the chin bottom (where the face loop's
# depth drops to the neck's), and the neck outer/inner widths.
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
    for i, (e, p) in enumerate(pts):
        for fc in e.link_faces:
            for e2 in fc.edges:
                j = idx.get(e2.index)
                if j is not None: par[f(i)] = f(j)
    C = {}
    for i, (e, p) in enumerate(pts): C.setdefault(f(i), []).append(p)
    return [c for c in C.values() if len(c) >= 6]
top = {"front": None, "back": None, "left": None, "right": None}
n = int(round((z_hi - z_lo) / step))
for k in range(n + 1):
    z = z_hi - k * step
    L = loops_at(z); neck = []; coll = []
    for c in L:
        xs = [p.x for p in c]; ys = [p.y for p in c]; cx = (min(xs) + max(xs)) / 2; w = max(xs) - min(xs)
        if abs(cx) > 0.12 or w > 0.30: continue
        (neck if (abs(cx) < 0.01 and w < 0.06) else coll).append(c)
    if not neck: continue
    nb = [(min(p.x for p in c), max(p.x for p in c), min(p.y for p in c), max(p.y for p in c)) for c in neck]
    big = max(nb, key=lambda b: (b[1] - b[0]) * (b[3] - b[2])); ncy = (big[2] + big[3]) / 2
    P_ = [p for c in coll for p in c]
    hit = {"front": any(abs(p.x) < 0.008 and p.y < big[2] - 0.002 for p in P_), "back": any(abs(p.x) < 0.008 and p.y > big[3] + 0.002 for p in P_),
           "left": any(p.x < big[0] - 0.004 and abs(p.y - ncy) < 0.03 for p in P_), "right": any(p.x > big[1] + 0.004 and abs(p.y - ncy) < 0.03 for p in P_)}
    for d, h in hit.items():
        if h and top[d] is None: top[d] = round(z, 4)
    print("z %.4f neck loops %s | collar loops %d | collar at %s" % (z, " ".join("w%.3f d%.3f" % (b[1] - b[0], b[3] - b[2]) for b in sorted(nb, key=lambda b: -(b[1] - b[0]))), len(coll), ",".join(d for d, h in hit.items() if h)))
print("COLLAR TOP", top)
