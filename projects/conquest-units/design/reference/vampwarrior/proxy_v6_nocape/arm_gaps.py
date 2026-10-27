# blender --background --python arm_gaps.py -- <glb> [step]
# Arm-to-body clearance on a raw proxy (glb units; glb y = height). Horizontal cross-sections every <step>: closed loops
# per slice; on each side the OUTERMOST loop is the arm when another loop (torso / leg) lies between it and the centre and
# it is narrower than 0.07. Arm loops vs every other loop: 2D gap per slice, and the 3D minimum (KD tree over all body
# loop points within +-0.01 in height). "Fused" heights = below the armpit, no separate arm loop on that side although
# the arm continues lower. Mesh vertices are labelled arm / body by their nearest slice point; the closest arm-vertex to
# body-vertex distance below the armpit is the fusion test (< 0.002 = fused).
import bpy, sys, bmesh, json, math
from mathutils import Vector, kdtree
a = sys.argv[sys.argv.index("--") + 1:]; glb = a[0]; step = float(a[1]) if len(a) > 1 else 0.002
bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.gltf(filepath=glb)
o = [x for x in bpy.context.scene.objects if x.type == "MESH"][0]
bm = bmesh.new(); bm.from_mesh(o.data); bm.transform(o.matrix_world); bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5); bm.edges.index_update()
E = list(bm.edges); V = [v.co.copy() for v in bm.verts]
import bisect
EZ = sorted(((min(e.verts[0].co.z, e.verts[1].co.z), max(e.verts[0].co.z, e.verts[1].co.z), e) for e in E), key=lambda t: t[0])
EZ_lo = [t[0] for t in EZ]; MAXLEN = max(t[1] - t[0] for t in EZ)
zmin = min(v.z for v in V); zmax = max(v.z for v in V); H = zmax - zmin
def loops_at(z):
    pts = []
    i0 = bisect.bisect_left(EZ_lo, z - MAXLEN); i1 = bisect.bisect_right(EZ_lo, z)
    for _lo, _hi, e in EZ[i0:i1]:
        if _hi <= z: continue
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
    out = []
    for c in C.values():
        if len(c) < 8: continue
        xs = [p.x for p in c]; out.append({"pts": c, "x0": min(xs), "x1": max(xs), "cx": (min(xs) + max(xs)) / 2, "w": max(xs) - min(xs)})
    return out
# top-level loops only (nested inner walls folded into their part).
# scan from 0.80 H (above the shoulders) down to 0.35 H (below the fingertips)
ARM_CX, ARM_X0 = 0.085, 0.04
CREASE = 0.015      # the armpit crease itself (the first 0.015 below the armpit) is not counted as a gap
z = zmin + 0.80 * H; rows = []
while z > zmin + 0.35 * H:
    L = loops_at(z); L = [l for l in L if l["w"] > 0.004]
    # nested walls (the shell is two-walled): a loop whose bbox lies inside another's belongs to that part; keep only the
    # top-level (outer-surface) loops for classification and distances, but remember the nested ones for vertex labels
    for l in L:
        ys = [p.y for p in l["pts"]]; l["y0"] = min(ys); l["y1"] = max(ys); l["kids"] = []
    top = []
    for l in L:
        par = [m for m in L if m is not l and m["x0"] <= l["x0"] and m["x1"] >= l["x1"] and m["y0"] <= l["y0"] and m["y1"] >= l["y1"]]
        if par: min(par, key=lambda m: m["w"])["kids"].append(l)
        else: top.append(l)
    for l in top:
        kids = list(l["kids"])
        while kids:
            k = kids.pop(); l.setdefault("all_kids", []).append(k); kids += k["kids"]
    L = top
    row = {"z": z, "arm": {}, "body": []}
    for side, sgn in (("right_x+", 1), ("left_x-", -1)):
        # arm = every top-level loop clearly out at the side (centre beyond ARM_CX, inner edge beyond ARM_X0) while some
        # other top-level loop (torso / leg) lies nearer the centre; several pieces (fingers) are merged into one arm set
        arms = [l for l in L if sgn * l["cx"] > ARM_CX and sgn * (l["x0"] if sgn > 0 else l["x1"]) > ARM_X0]
        inner = [l for l in L if all(l is not m for m in arms) and abs(l["cx"]) < ARM_CX]
        if arms and inner:
            pts = [p for l in arms for p in l["pts"]]; kids = [k for l in arms for k in ([l] + l.get("all_kids", []))]
            row["arm"][side] = {"pts": pts, "all_kids": kids[len(arms):] + [], "members": arms}
    used = [m for a_ in row["arm"].values() for m in a_["members"]]
    row["body"] = [l for l in L if all(l is not x for x in used)]
    rows.append(row); z -= step
res = {"glb": glb, "height": H, "zmin": zmin, "step": step, "sides": {}}
body_pts_all = [(p, r["z"]) for r in rows for l in r["body"] for p in l["pts"]]
kd = kdtree.KDTree(len(body_pts_all))
for i, (p, zz) in enumerate(body_pts_all): kd.insert(p, i)
kd.balance()
for side in ("right_x+", "left_x-"):
    zs = [r["z"] for r in rows if side in r["arm"]]
    if not zs: res["sides"][side] = None; continue
    z_top, z_bot = max(zs), min(zs)
    fused = [round(r["z"], 4) for r in rows if z_bot < r["z"] < z_top and side not in r["arm"]]
    best = (9, None, None); per = []
    bsel = [(p, zz) for (p, zz) in body_pts_all if zz <= z_top - CREASE]          # body below the armpit crease only
    kd = kdtree.KDTree(len(bsel))
    for i, (p, zz) in enumerate(bsel): kd.insert(p, i)
    kd.balance()
    for r in rows:
        if side not in r["arm"]: continue
        if r["z"] > z_top - CREASE: continue
        arm = r["arm"][side]; g2 = 9
        bp = [p for l in r["body"] for p in l["pts"]]
        ks = kdtree.KDTree(len(bp))
        for j, p in enumerate(bp): ks.insert(p, j)
        ks.balance()
        for p in arm["pts"]:
            if bp:
                q, j, d = ks.find(p)
                if d < g2: g2 = d
            q, i, d = kd.find(p)
            if d < best[0]: best = (d, (round(p.x, 4), round(p.y, 4), round(p.z, 4)), (round(q.x, 4), round(q.y, 4), round(q.z, 4)))
        per.append((round(r["z"], 4), round(g2, 4)))
    # crotch = highest height where the body is two separate leg loops
    crotch = max((r["z"] for r in rows if len([l for l in r["body"] if abs(l["cx"]) > 0.015 and l["w"] < 0.09]) >= 2 and not any(abs(l["cx"]) < 0.015 for l in r["body"])), default=None)
    def part(zz):
        if crotch is None: return "?"
        return "thigh" if zz < crotch else ("hips" if zz < crotch + 0.09 else "torso")
    mz = min(per, key=lambda t: t[1])
    res.setdefault("crotch_z", round(crotch, 4) if crotch is not None else None)
    hand_lo = z_bot; hand_hi = z_bot + 0.09 * H
    hand = min((g for zz, g in per if hand_lo <= zz <= hand_hi), default=None)
    res["sides"][side] = {"armpit_z": round(z_top, 4), "arm_bottom_z": round(z_bot, 4), "fused_heights_below_armpit": fused[:200],
                          "n_fused_slices": len(fused), "min_gap_3d": round(best[0], 4), "min_gap_arm_point": best[1], "min_gap_body_point": best[2],
                          "min_gap_slice_2d": mz, "min_gap_slice_part": part(mz[0]), "min_gap_3d_part": part(best[1][2]) if best[1] else None,
                          "min_by_part": {pp: min([g for zz, g in per if part(zz) == pp], default=None) for pp in ("torso", "hips", "thigh")}, "hand_zone_z": [round(hand_lo, 4), round(hand_hi, 4)],
                          "hand_to_body_min_2d": hand, "gap_profile_every_10th": per[::10]}
# vertex-level fusion test
slice_pts = []
for r in rows:
    for side, l in r["arm"].items():
        for ll in l["members"] + [k for m in l["members"] for k in m.get("all_kids", [])]:
            for p in ll["pts"]: slice_pts.append((p, "arm:" + side))
    for l in r["body"]:
        for ll in [l] + l.get("all_kids", []):
            for p in ll["pts"]: slice_pts.append((p, "body"))
kd2 = kdtree.KDTree(len(slice_pts))
for i, (p, lab) in enumerate(slice_pts): kd2.insert(p, i)
kd2.balance()
lab_v = {}
for vi, p in enumerate(V):
    q, i, d = kd2.find(p)
    if i is not None and d < 0.003: lab_v[vi] = slice_pts[i][1]
for side in ("right_x+", "left_x-"):
    s = res["sides"].get(side)
    if not s: continue
    armv = [V[i] for i, l in lab_v.items() if l == "arm:" + side and V[i].z < s["armpit_z"] - 0.01]
    bodyv = [(i, V[i]) for i, l in lab_v.items() if l == "body" and s["arm_bottom_z"] - 0.01 < V[i].z < s["armpit_z"] - 0.01]
    kb = kdtree.KDTree(len(bodyv))
    for j, (i, p) in enumerate(bodyv): kb.insert(p, j)
    kb.balance()
    md = 9; at = None; n_close = 0
    for p in armv:
        q, j, d = kb.find(p)
        if j is None: continue
        if d < 0.002: n_close += 1
        if d < md: md = d; at = (round(p.x, 4), round(p.y, 4), round(p.z, 4))
    s["vertex_min_arm_to_body"] = round(md, 4); s["vertex_min_at"] = at; s["n_arm_vertices_within_0.002"] = n_close; s["n_arm_vertices"] = len(armv)
print("ARMGAPS " + json.dumps(res))
