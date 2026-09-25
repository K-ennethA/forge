"""Hero facing audit (read only, never saves) -- facing_audit.py's measurements over ALL visible meshes.

    blender --background <source-copies/hero-X.blend> --factory-startup --python hero_facing.py -- blend <out.json>
    blender --background --factory-startup --python hero_facing.py -- glb <in.glb> <out.json>

Heroes are multi-object sculpts (facing_audit.py takes only the biggest mesh), so this joins every
render-visible mesh, evaluated (modifiers applied, armature at rest), in world space. Measures, with
no front assumption in any number:
  * top-30% curvature-feature centroid offset + angle from -Y (facing_audit.py's method: carved
    eyes / mouths / visors concentrate Laplacian energy on the side they are carved into)
  * per-side Laplacian energy of the top 30% and of the 35-75% band (chest -- Duskmaw's mouth)
  * per-material face-centroid direction from the body centroid (a painted mouth / face / visor
    material points at the front), angle from -Y
Angles: 0 = -Y (Conquest sculpt convention), +90 = +X, 180 = +Y.
"""
import bpy, sys, json, math
import numpy as np

a = sys.argv[sys.argv.index("--") + 1:]
if a[0] == "glb":
    OUT = a[2]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=a[1])
else:
    OUT = a[1]
vl = set(o.name for o in bpy.context.view_layer.objects)
for o in bpy.data.objects:          # armature at rest so a stored pose can't skew the audit
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
Ws, faces_c, faces_m, E = [], [], [], []
off = 0
mat_names = []
for o in bpy.data.objects:
    if o.type != "MESH" or o.name not in vl or o.hide_render or not o.visible_get() or o.name.startswith("Icosphere"):
        continue
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    M = np.array(o.matrix_world); W = co @ M[:3, :3].T + M[:3, 3]
    ed = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ed)
    fc = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("center", fc); fc = fc.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
    mi = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("material_index", mi)
    names = [s.material.name if s.material else "<none>" for s in o.material_slots] or ["<none>"]
    Ws.append(W); E.append(ed.reshape(-1, 2) + off); faces_c.append(fc)
    faces_m.extend(names[min(i, len(names) - 1)] for i in mi)
    off += n
    mat_names.append(o.name)
    ev.to_mesh_clear()
W = np.vstack(Ws); ev = np.vstack(E); FC = np.vstack(faces_c); FM = np.array(faces_m)
n = len(W)
deg = np.bincount(ev.ravel(), minlength=n).astype(float)
nsum = np.zeros((n, 3))
np.add.at(nsum, ev[:, 0], W[ev[:, 1]]); np.add.at(nsum, ev[:, 1], W[ev[:, 0]])
lap = np.linalg.norm(W - nsum / np.maximum(deg, 1)[:, None], axis=1)
lo, hi = W.min(0), W.max(0); H = hi[2] - lo[2]
ctr = W.mean(0)


def ang(dx, dy):
    return round(math.degrees(math.atan2(dx, -dy)), 1)


def band(z0, z1):
    m = (W[:, 2] >= lo[2] + z0 * H) & (W[:, 2] <= lo[2] + z1 * H)
    wt = lap[m] ** 2
    tc = W[m].mean(0)
    fcn = (W[m] * wt[:, None]).sum(0) / wt.sum()
    y, x = W[m, 1] - tc[1], W[m, 0] - tc[0]
    return {"n": int(m.sum()), "feature_offset_xy": (fcn[:2] - tc[:2]).round(4).tolist(),
            "feature_angle_from_minusY_deg": ang(fcn[0] - tc[0], fcn[1] - tc[1]),
            "lap_energy": {"minusY": round(float(wt[y < 0].sum()), 5), "plusY": round(float(wt[y > 0].sum()), 5),
                           "minusX": round(float(wt[x < 0].sum()), 5), "plusX": round(float(wt[x > 0].sum()), 5)},
            "extent_y": [round(float(W[m, 1].min()), 3), round(float(W[m, 1].max()), 3)]}


rep = {"source": bpy.data.filepath or a[1], "meshes": mat_names, "verts": n,
       "bbox_min": lo.round(3).tolist(), "bbox_max": hi.round(3).tolist(), "centroid": ctr.round(3).tolist(),
       "top30": band(0.70, 1.0), "head_top15": band(0.85, 1.0), "chest_35_75": band(0.35, 0.75)}
mats = {}
for name in sorted(set(FM.tolist())):
    m = FM == name
    c = FC[m].mean(0); d = c - ctr
    mats[name] = {"faces": int(m.sum()), "centroid": c.round(3).tolist(),
                  "dir_angle_from_minusY_deg": ang(d[0], d[1]), "horiz_offset": round(float(math.hypot(d[0], d[1])), 4)}
rep["materials"] = mats
json.dump(rep, open(OUT, "w"), indent=1)
print("HERO_FACING", json.dumps({k: rep[k] for k in ("top30", "head_top15", "chest_35_75")}))
