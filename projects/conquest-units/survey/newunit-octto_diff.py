"""supaoctto vs supaoctto_rig: per-part base-mesh diff + stored pose (read only, never saves).

    blender --background <newunit-supaoctto_rig.blend> --factory-startup --python newunit-octto_diff.py -- <newunit-supaoctto.blend> <out.json>

Links the unrigged copy's meshes in memory (bpy.data.libraries.load, never written) and compares each
same-named object's LOCAL vertex coords, object matrix_world, and reports the rig's stored pose
(max pose-bone deviation from rest) so 'which file is live' rests on measurements.
"""
import bpy, sys, json
import numpy as np

a = sys.argv[sys.argv.index("--") + 1:]
OTHER, OUT = a[0], a[1]
live = {o.name: o for o in bpy.data.objects if o.type == "MESH"}
with bpy.data.libraries.load(OTHER, link=False) as (src, dst):
    dst.objects = [n for n in src.objects]
rep = {"rig_file": bpy.data.filepath, "unrigged_file": OTHER, "parts": []}


def co(me):
    c = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", c); return c.reshape(-1, 3)


for o in dst.objects:
    if o is None or o.type != "MESH":
        continue
    base = o.name.split(".")[0] if o.name not in live else o.name
    # imported names collide with the open file's names and get a .00N suffix; map back by data name order
    rep["parts"].append({"unrigged_obj": o.name})
# match by stripping the collision suffix Blender appended on load
imported = [o for o in dst.objects if o and o.type == "MESH"]
rows = []
for o in imported:
    cand = o.name
    for suf in (".001", ".002", ".003"):
        if cand.endswith(suf) and cand[:-len(suf)] in live:
            cand = cand[:-len(suf)]; break
    L = live.get(cand)
    if L is None or L is o:
        rows.append({"part": o.name, "match": None}); continue
    A, B = co(L.data), co(o.data)
    r = {"part": cand, "verts_rig": len(A), "verts_unrigged": len(B)}
    if len(A) == len(B):
        r["max_local_vert_diff"] = round(float(np.abs(A - B).max()), 6)
        r["moved_verts"] = int((np.abs(A - B).max(1) > 1e-5).sum())
    Mr, Mu = np.array(L.matrix_world), np.array(o.matrix_world)
    r["max_matrix_world_diff"] = round(float(np.abs(Mr - Mu).max()), 6)
    r["parent_rig"] = L.parent.name if L.parent else None
    r["parent_unrigged"] = o.parent.name if o.parent else None
    rows.append(r)
rep["parts"] = rows
for o in bpy.data.objects:
    if o.type == "ARMATURE" and o.pose:
        dev = 0.0
        for pb in o.pose.bones:
            m = np.array(pb.matrix_basis)
            dev = max(dev, float(np.abs(m - np.eye(4)).max()))
        rep["stored_pose_max_basis_dev"] = round(dev, 6)
        rep["pose_position"] = o.data.pose_position
json.dump(rep, open(OUT, "w"), indent=1)
print("OCTTO_DIFF", json.dumps(rep))
