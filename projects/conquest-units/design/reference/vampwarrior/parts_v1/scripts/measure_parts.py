# measure_parts.py: blender -b --python measure_parts.py -- <out.json> <body.glb> [<cape.glb>@<matrix.json>]
# raw meshes (merged by distance 1e-5 only to undo glTF seam splits): islands, boundary (open) edges, non-manifold;
# arms vs torso: per height band between armpit and wrist, the X gap between the torso cluster and each arm cluster (front projection) + 3D min distance;
# cape vs body: min / percentile distance of cape verts to the body surface, verts inside the body, face-face intersections (BVH overlap).
import bpy, bmesh, sys, json, math, mathutils
import numpy as np
from mathutils.bvhtree import BVHTree
args = sys.argv[sys.argv.index("--") + 1:]; out = args[0]
bpy.ops.wm.read_factory_settings(use_empty=True)

def load(spec):
    glb, _, mfile = spec.partition("@")
    before = set(bpy.context.scene.objects); bpy.ops.import_scene.gltf(filepath=glb)
    new = [o for o in set(bpy.context.scene.objects) - before]
    M = mathutils.Matrix(json.load(open(mfile))["matrix"]) if mfile else mathutils.Matrix.Identity(4)
    bpy.context.view_layer.update()
    bm = bmesh.new()
    for o in new:
        if o.type != "MESH": continue
        t = bmesh.new(); t.from_mesh(o.data); t.transform(M @ o.matrix_world)
        me = bpy.data.meshes.new("tmp"); t.to_mesh(me); bm.from_mesh(me); t.free()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    return bm

def stats(bm):
    seen = set(); isl = []
    for v in bm.verts:
        if v.index in seen: continue
        st = [v]; seen.add(v.index); n = 0
        while st:
            x = st.pop(); n += 1
            for e in x.link_edges:
                w = e.other_vert(x)
                if w.index not in seen: seen.add(w.index); st.append(w)
        isl.append(n)
    isl.sort(reverse=True)
    bnd = sum(1 for e in bm.edges if e.is_boundary); nm = sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary)
    co = np.array([v.co[:] for v in bm.verts])
    return dict(verts=len(bm.verts), faces=len(bm.faces), islands=len(isl), island_sizes_top=isl[:8], boundary_edges=bnd,
                nonmanifold_edges=nm, bbox_min=co.min(0).round(4).tolist(), bbox_max=co.max(0).round(4).tolist()), co

res = {}
body = load(args[1]); res["body"], bco = stats(body)
H = bco[:, 2].max() - bco[:, 2].min(); z0 = bco[:, 2].min()
res["body"]["height"] = float(H)
# arms vs torso: bands from 0.55 H to 0.80 H (wrist..armpit region of a ~8-head figure), front projection on X
bands = []
for f in np.arange(0.50, 0.82, 0.02):
    sel = bco[(bco[:, 2] > z0 + f * H) & (bco[:, 2] < z0 + (f + 0.02) * H)]
    if len(sel) < 20: continue
    xs = np.sort(sel[:, 0]); gaps = np.diff(xs); cx = np.median(bco[:, 0])
    # gaps wider than 0.4 % H, left and right of centre
    big = [(float(xs[i]), float(xs[i + 1]), float(gaps[i])) for i in np.nonzero(gaps > 0.004 * H)[0]]
    L = [g for g in big if g[1] <= cx]; R = [g for g in big if g[0] >= cx]
    bands.append(dict(h_frac=round(float(f), 2), gap_left=round(max([g[2] for g in L], default=0) / H, 4), gap_right=round(max([g[2] for g in R], default=0) / H, 4)))
res["arm_torso_bands"] = bands
if len(args) > 2:
    cape = load(args[2]); res["cape"], cco = stats(cape)
    bt = BVHTree.FromBMesh(body); ct = BVHTree.FromBMesh(cape)
    inter = bt.overlap(ct); res["cape"]["intersecting_face_pairs"] = len(inter)
    d = []; inside = 0
    for v in cape.verts:
        loc, nrm, idx, dist = bt.find_nearest(v.co)
        if loc is None: continue
        s = (v.co - loc).dot(nrm); d.append(dist if s >= 0 else -dist); inside += s < 0
    d = np.array(d)
    res["cape"]["dist_to_body_over_H"] = dict(min_signed=float(d.min() / H), p1=float(np.percentile(d, 1) / H), p5=float(np.percentile(d, 5) / H),
                                              median=float(np.median(d) / H), verts_inside=int(inside), verts_within_0p5pctH=int((np.abs(d) < 0.005 * H).sum()), n=len(d))
    # cape vs arms: cape verts near the arm bands (outside |x| > shoulder-ish) closest distance
    ic = np.array(sorted(set(i for p in inter for i in [p[0]]))) if inter else np.array([], int)
    if len(ic):
        fz = np.array([body.faces[i].calc_center_median()[:] for i in ic])
        res["cape"]["intersections_body_face_z_frac"] = np.histogram((fz[:, 2] - z0) / H, bins=10, range=(0, 1))[0].tolist()
        res["cape"]["intersections_body_face_absx_over_H_p50_p90"] = [float(np.percentile(np.abs(fz[:, 0] - np.median(bco[:, 0])) / H, q)) for q in (50, 90)]
json.dump(res, open(out, "w"), indent=1); print(json.dumps(res)[:3000])
