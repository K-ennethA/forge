# contact_where.py: blender -b --python contact_where.py -- <body.glb> <cape.glb@matrix.json> <out.json>
# cape verts inside the body / within 0.5 % H of it, classified by WHERE on the body (nearest point): head-collar (z > 0.86 H), shoulder (0.74-0.86 H),
# arm (below 0.74 H, beyond the torso cluster's x-extent at that height), torso (below 0.74 H, inside it). Heights as fractions of body height.
import bpy, bmesh, sys, json, mathutils, numpy as np
from mathutils.bvhtree import BVHTree
a = sys.argv[sys.argv.index("--") + 1:]; bg, cspec, out = a
bpy.ops.wm.read_factory_settings(use_empty=True)
def load(spec):
    glb, _, mf = spec.partition("@"); M = mathutils.Matrix(json.load(open(mf))["matrix"]) if mf else mathutils.Matrix.Identity(4)
    before = set(bpy.context.scene.objects); bpy.ops.import_scene.gltf(filepath=glb); bpy.context.view_layer.update(); bm = bmesh.new()
    for o in set(bpy.context.scene.objects) - before:
        if o.type == "MESH":
            t = bmesh.new(); t.from_mesh(o.data); t.transform(M @ o.matrix_world); me = bpy.data.meshes.new("t"); t.to_mesh(me); bm.from_mesh(me); t.free()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5); return bm
body = load(bg); cape = load(cspec); bt = BVHTree.FromBMesh(body)
B = np.array([v.co[:] for v in body.verts]); z0 = B[:, 2].min(); H = B[:, 2].max() - z0; cx = np.median(B[:, 0])
def torso_half(zf):
    sel = B[np.abs((B[:, 2] - z0) / H - zf) < 0.01][:, 0] - cx
    xs = np.sort(sel); g = np.diff(xs); i0 = np.searchsorted(xs, 0)
    r = i0
    while r < len(xs) - 1 and g[r] < 0.004 * H: r += 1
    l = i0
    while l > 0 and g[l - 1] < 0.004 * H: l -= 1
    return xs[l], xs[r]
cache = {}
cnt = {k: dict(inside=0, near=0) for k in ("head_collar", "shoulder", "arm", "torso")}
for v in cape.verts:
    loc, n, i, d = bt.find_nearest(v.co)
    sd = (v.co - loc).dot(n)
    if sd >= 0.005 * H: continue
    zf = (loc.z - z0) / H
    if zf > 0.86: k = "head_collar"
    elif zf > 0.74: k = "shoulder"
    else:
        key = round(zf, 2)
        if key not in cache: cache[key] = torso_half(key)
        lo, hi = cache[key]; k = "torso" if lo - 0.002 <= loc.x - cx <= hi + 0.002 else "arm"
    cnt[k]["inside" if sd < 0 else "near"] += 1
res = dict(H=float(H), counts=cnt, n_cape_verts=len(cape.verts)); json.dump(res, open(out, "w"), indent=1); print("WHERE", json.dumps(res))
