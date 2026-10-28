# register_mesh.py: blender -b --python register_mesh.py -- <body.glb> <cape.glb> <register_px.json> <out_matrix.json>
# body mesh <-> body px and cape mesh <-> cape px by bbox (uniform scale from the height; front image x = +X, side_left image x = +Y, rows = -Z),
# then cape px -> body px (register_px cape_to_body_px) -> body mesh. Output: the cape's 4x4 world matrix (uniform scale + translation), body untouched.
import bpy, sys, json, mathutils
import numpy as np
b_glb, c_glb, pxf, outf = sys.argv[sys.argv.index("--") + 1:]
R = json.load(open(pxf)); T = R["cape_to_body_px"]
bpy.ops.wm.read_factory_settings(use_empty=True)
def verts(glb):
    before = set(bpy.context.scene.objects); bpy.ops.import_scene.gltf(filepath=glb); bpy.context.view_layer.update()
    P = []
    for o in set(bpy.context.scene.objects) - before:
        if o.type == "MESH":
            M = np.array(o.matrix_world); co = np.array([v.co[:] for v in o.data.vertices]); P.append(co @ M[:3, :3].T + M[:3, 3])
    return np.concatenate(P)
def frame(P, px):
    lo, hi = P.min(0), P.max(0); f, s = px["front"], px["side_left"]
    sc = (f["bot"] - f["top"]) / (hi[2] - lo[2])
    return dict(lo=lo, hi=hi, c=(lo + hi) / 2, s=sc, fcx=(f["x0"] + f["x1"]) / 2, fcy=(f["top"] + f["bot"]) / 2, scx=(s["x0"] + s["x1"]) / 2,
                ratio_x=float((hi[0] - lo[0]) * sc / (f["x1"] - f["x0"])), ratio_y=float((hi[1] - lo[1]) * sc / (s["x1"] - s["x0"])))
B = frame(verts(b_glb), R["body"]); C = frame(verts(c_glb), R["cape"])
k = T["k"]
pf = k * C["fcx"] + T["tx"]; pr = k * C["fcy"] + T["ty"]; ps = k * C["scx"] + T["tsx"]
target = np.array([B["c"][0] + (pf - B["fcx"]) / B["s"], B["c"][1] + (ps - B["scx"]) / B["s"], B["c"][2] - (pr - B["fcy"]) / B["s"]])
S = k * C["s"] / B["s"]
M = mathutils.Matrix.Translation(mathutils.Vector(target.tolist())) @ mathutils.Matrix.Scale(S, 4) @ mathutils.Matrix.Translation(mathutils.Vector((-C["c"]).tolist()))
res = dict(matrix=[list(r) for r in M], scale=S, target_centre=target.tolist(),
           bbox_vs_silhouette=dict(body_x=B["ratio_x"], body_y=B["ratio_y"], cape_x=C["ratio_x"], cape_y=C["ratio_y"]),
           body_bbox=[B["lo"].tolist(), B["hi"].tolist()], cape_bbox=[C["lo"].tolist(), C["hi"].tolist()])
json.dump(res, open(outf, "w"), indent=1); print("REG", json.dumps(res))
