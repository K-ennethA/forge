"""See-through proof for the Duskmaw maw (read-only; opens blends, never saves).

    blender --background --factory-startup --python duskmaw_seethrough.py -- <out.json> <label>=<blend> [<label>=<blend> ...]

Per blend: rays through the WAIST CORE -- |u| <= 2.5 units either side of the waist axis, heights = the mouth band (v3 blends
carry it as conquest_maw_band = the maw's hole heights; v1/v2 blends: 6.0-9.2, between the chest's underside and the
skirt's peaks), step 0.1 -- from 24 yaws round the figure (0 = the front camera,
180 = straight behind). Value = % of those rays that pass through the model without hitting anything (v1 = the open
ring maw; v2 must be 0 from every side, including the front: there the rays enter the mouth and stop on the throat).
Rigged blends are also measured on every 4th frame of every clip (the chomp's open/shut and the glide's lean).
Where light gets through is reported too: per yaw, the missed rays' closest-approach y relative to the waist axis
(negative = in front of it). The back hemisphere (yaw 105-255) is the artist's complaint ("from the back side").
Waist axis: v2 carries it (conquest_front_anchor x/y = the waist axis); otherwise the throat column's centre
(vertices at z 6.2-8.9 within 2 units of the origin), the rule the build uses on the sculpt.
"""
import bpy, sys, json, math
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
JOBS = [a.split("=", 1) for a in argv[1:]]
Z_BAND, HALF_W, STEP = (6.0, 9.2), 2.5, 0.1


def rays(V, F, axis, where=None, Z_BAND=Z_BAND):
    """% of rays through per yaw; if `where` is a dict, it also collects per yaw the missed rays' closest-approach
    points to the waist axis (y relative to the axis: negative = in FRONT of it) -- where the light gets through."""
    bvh = BVHTree.FromPolygons(V.tolist(), F)
    rows = {}
    for deg_ in range(0, 360, 15):
        a = math.radians(deg_)
        d = Vector((-math.sin(a), math.cos(a), 0.0))
        e1 = Vector((math.cos(a), math.sin(a), 0.0))
        start = Vector((axis[0], axis[1], 0.0)) - d * 60.0
        n = miss = 0
        ys = []
        for u in np.arange(-HALF_W, HALF_W + 1e-9, STEP):
            for z in np.arange(Z_BAND[0], Z_BAND[1] + 1e-9, STEP):
                hit = bvh.ray_cast(start + e1 * float(u) + Vector((0, 0, float(z))), d, 120.0)
                n += 1
                if hit[0] is None:
                    miss += 1
                    ys.append(float(u) * e1.y)
        rows[deg_] = round(100.0 * miss / n, 2)
        if where is not None and ys:
            w = where.setdefault(deg_, [1e9, -1e9])
            w[0] = min(w[0], min(ys)); w[1] = max(w[1], max(ys))
    return rows


def mesh_now(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m = ev.to_mesh()
    V = np.empty(len(m.vertices) * 3); m.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
    M = np.array(ob.matrix_world); V = V @ M[:3, :3].T + M[:3, 3]
    F = [list(p.vertices) for p in m.polygons]
    ev.to_mesh_clear()
    return V, F


res = {"rule": __doc__.strip().split("\n\n")[2], "blends": {}}
for label, path in JOBS:
    bpy.ops.wm.open_mainfile(filepath=path)
    sc = bpy.context.scene
    meshes = [o for o in sc.objects if o.type == "MESH" and not o.hide_render]
    ob = max(meshes, key=lambda o: len(o.data.polygons))
    rig = next((o for o in sc.objects if o.type == "ARMATURE"), None)
    if rig is not None:
        rig.data.pose_position = "REST"
    bpy.context.view_layer.update()
    V, F = mesh_now(ob)
    if "conquest_version" in ob.keys():
        axis = list(ob["conquest_front_anchor"])[:2]
    else:
        col = (V[:, 2] > 6.2) & (V[:, 2] < 8.9) & (np.hypot(V[:, 0], V[:, 1]) < 2.0)
        axis = V[col, :2].mean(0).tolist()
    wh = {}
    band = tuple(ob["conquest_maw_band"]) if "conquest_maw_band" in ob.keys() else Z_BAND
    row = {"file": path, "axis_xy": [round(float(a), 4) for a in axis], "z_band": [round(float(b), 3) for b in band],
           "rest": rays(V, F, axis, wh, band)}
    row["rest_max_pct"] = max(row["rest"].values())
    row["rest_front_pct"], row["rest_back_pct"] = row["rest"][0], row["rest"][180]
    row["rest_back_hemisphere_max_pct"] = max(v for k, v in row["rest"].items() if 105 <= k <= 255)
    row["rest_miss_y_range_by_yaw"] = {k: [round(a_, 3), round(b_, 3)] for k, (a_, b_) in sorted(wh.items())}
    if rig is not None:
        rig.data.pose_position = "POSE"
        worst, worst_yaw, posed_wh = {}, {}, {}
        for act in bpy.data.actions:
            rig.animation_data_create(); rig.animation_data.action = act
            if hasattr(rig.animation_data, "action_slot") and rig.animation_data.action_slot is None and len(act.slots):
                rig.animation_data.action_slot = act.slots[0]
            f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
            per_yaw = {}
            wh_c = {}
            for f in range(f0, f1 + 1, 4):
                sc.frame_set(f)
                Vp, Fp = mesh_now(ob)
                for k, v in rays(Vp, Fp, axis, wh_c, band).items():
                    per_yaw[k] = max(per_yaw.get(k, 0.0), v)
            worst[act.name] = max(per_yaw.values())
            worst_yaw[act.name] = per_yaw
            posed_wh[act.name] = {k: [round(a_, 3), round(b_, 3)] for k, (a_, b_) in sorted(wh_c.items())}
        row["posed_max_pct_by_clip"] = worst
        row["posed_back_hemisphere_max_pct_by_clip"] = {c: max(v for k, v in py.items() if 105 <= k <= 255) for c, py in worst_yaw.items()}
        row["posed_max_pct_by_clip_and_yaw"] = worst_yaw
        row["posed_miss_y_range_by_clip_and_yaw"] = posed_wh
    res["blends"][label] = row
    print("SEETHROUGH", label, json.dumps(row))
json.dump(res, open(OUT, "w"), indent=1)
print("DONE")
