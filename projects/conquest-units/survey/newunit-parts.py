"""New-unit parts probe (read only, never saves).

    blender --background <source-copies/newunit-X.blend> --factory-startup --python newunit-parts.py -- <out.json>

Per mesh object: visibility flags, world bbox + centre, direction of the centre from the whole-unit
centre (angle from -Y: 0 = -Y, +90 = +X, 180 = +Y), world-space vertex digest (sha1 of float32
coords, evaluated at rest) so two files can be compared part-by-part, negative-scale flag, and
normals orientation (mean signed volume sign: a closed mesh with outward normals has volume > 0).
Armatures: bones with head/tail (world), deform flag, parent, per-mesh vertex-group coverage
(fraction of verts with any weight > 0 on a deform bone). Pose position forced to REST.
"""
import bpy, sys, json, math, hashlib
import numpy as np
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1]
vl = set(o.name for o in bpy.context.view_layer.objects)
for o in bpy.data.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()


def ang(dx, dy):
    return round(math.degrees(math.atan2(dx, -dy)), 1)


parts, allW = [], []
for o in bpy.data.objects:
    if o.type != "MESH":
        continue
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    M = np.array(o.matrix_world); W = co @ M[:3, :3].T + M[:3, 3]
    # signed volume (world): sum over triangles of det/6
    me.calc_loop_triangles()
    tv = np.empty(len(me.loop_triangles) * 3, dtype=np.int64); me.loop_triangles.foreach_get("vertices", tv)
    T = W[tv.reshape(-1, 3)]
    vol = float(np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum() / 6.0)
    visible = o.name in vl and not o.hide_render and o.visible_get()
    lo, hi = W.min(0), W.max(0)
    parts.append({"name": o.name, "render_visible": visible, "hide_render": o.hide_render,
                  "hide_viewport": o.hide_get() if o.name in vl else None, "parent": o.parent.name if o.parent else None,
                  "verts": n, "bbox_min": lo.round(3).tolist(), "bbox_max": hi.round(3).tolist(),
                  "centre": ((lo + hi) / 2).round(3).tolist(), "negative_scale": bool(np.linalg.det(M[:3, :3]) < 0),
                  "signed_volume": round(vol, 4), "normals": "outward" if vol > 0 else "INVERTED",
                  "vert_digest": hashlib.sha1(W.astype(np.float32).tobytes()).hexdigest()[:16],
                  "modifiers": [m.type for m in o.modifiers],
                  "vertex_groups": [g.name for g in o.vertex_groups]})
    if visible:
        allW.append(W)
    ev.to_mesh_clear()
A = np.vstack(allW) if allW else np.zeros((1, 3))
ctr = A.mean(0)
for p in parts:
    c = np.array(p["centre"]); d = c - ctr
    p["dir_from_unit_centroid_deg"] = ang(d[0], d[1]); p["horiz_offset"] = round(float(math.hypot(d[0], d[1])), 3)
rep = {"source": bpy.data.filepath, "unit_centroid": ctr.round(3).tolist(),
       "unit_bbox_min": A.min(0).round(3).tolist(), "unit_bbox_max": A.max(0).round(3).tolist(), "parts": parts, "armatures": []}
for o in bpy.data.objects:
    if o.type != "ARMATURE":
        continue
    M = o.matrix_world
    bones = [{"name": b.name, "deform": b.use_deform, "parent": b.parent.name if b.parent else None,
              "head": list(map(lambda v: round(v, 3), M @ b.head_local)), "tail": list(map(lambda v: round(v, 3), M @ b.tail_local))}
             for b in o.data.bones]
    deform = {b.name for b in o.data.bones if b.use_deform}
    cover = {}
    for m in bpy.data.objects:
        if m.type != "MESH":
            continue
        bound = any(md.type == "ARMATURE" and md.object == o for md in m.modifiers)
        if not m.vertex_groups:
            cover[m.name] = {"bound": bound, "weighted_frac": 0.0}
            continue
        gi = {g.index for g in m.vertex_groups if g.name in deform}
        w = sum(1 for v in m.data.vertices if any(g.group in gi and g.weight > 0 for g in v.groups))
        cover[m.name] = {"bound": bound, "weighted_frac": round(w / max(len(m.data.vertices), 1), 4)}
    ad = o.animation_data
    rep["armatures"].append({"object": o.name, "data": o.data.name, "scale": [round(s, 3) for s in o.scale],
                             "location": [round(s, 3) for s in o.location], "bones": bones, "coverage": cover,
                             "action": ad.action.name if ad and ad.action else None,
                             "nla": [t.name for t in ad.nla_tracks] if ad else []})
rep["actions"] = [a.name for a in bpy.data.actions]
json.dump(rep, open(OUT, "w"), indent=1)
print("PARTS_JSON", OUT)
