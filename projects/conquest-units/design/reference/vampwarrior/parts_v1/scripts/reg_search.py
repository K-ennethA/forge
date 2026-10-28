# reg_search.py: blender -b --python reg_search.py -- <body.glb> <cape.glb> <matrix.json> <out.json>
# rigid refinement of the bbox registration: grid over cape translation (dy back, dz up) and uniform scale about the cape's top centre;
# score = cape verts inside the body (signed nearest-surface distance < 0), tie-break: small mean shift. Writes the best matrix (+ the grid).
import bpy, bmesh, sys, json, mathutils, numpy as np
from mathutils.bvhtree import BVHTree
bg, cg, mf, out = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
def load(glb, M):
    before = set(bpy.context.scene.objects); bpy.ops.import_scene.gltf(filepath=glb); bpy.context.view_layer.update()
    bm = bmesh.new()
    for o in set(bpy.context.scene.objects) - before:
        if o.type == "MESH":
            t = bmesh.new(); t.from_mesh(o.data); t.transform(M @ o.matrix_world); me = bpy.data.meshes.new("t"); t.to_mesh(me); bm.from_mesh(me); t.free()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5); return bm
body = load(bg, mathutils.Matrix.Identity(4)); bt = BVHTree.FromBMesh(body)
M0 = mathutils.Matrix(json.load(open(mf))["matrix"]); cape = load(cg, M0)
co = np.array([v.co[:] for v in cape.verts]); sub = co[::4]
H = 0.986
top = np.array([0.0, np.median(co[:, 1]), co[:, 2].max()])
def score(dy, dz, s):
    P = (sub - top) * s + top + np.array([0, dy, dz]); inside = 0; dmin = 9
    for p in P:
        loc, n, i, d = bt.find_nearest(mathutils.Vector(p))
        sd = (mathutils.Vector(p) - loc).dot(n); inside += sd < 0; dmin = min(dmin, sd)
    return int(inside), float(dmin)
grid = []
for s in (1.0, 1.03, 1.06):
    for dy in (0.0, 0.01, 0.02, 0.03):
        for dz in (-0.01, 0.0, 0.01):
            i, d = score(dy, dz, s); grid.append(dict(s=s, dy=dy, dz=dz, inside_quarter_sample=i, min_signed_over_H=d / H)); print(grid[-1], flush=True)
best = min(grid, key=lambda g: (g["inside_quarter_sample"], abs(g["dy"]) + abs(g["dz"]) + abs(g["s"] - 1)))
T = mathutils.Matrix.Translation(mathutils.Vector((0, best["dy"], best["dz"]))) @ mathutils.Matrix.Translation(mathutils.Vector(top.tolist())) @ mathutils.Matrix.Scale(best["s"], 4) @ mathutils.Matrix.Translation(mathutils.Vector((-top).tolist()))
json.dump(dict(matrix=[list(r) for r in (T @ M0)], best=best, grid=grid, base=mf), open(out, "w"), indent=1); print("BEST", best)
