"""Wren v7 hair diagnosis (review-log 2026-09-29 "Wren v7 hair feedback": "ours look chopped up"): mesh-level metrics of
the delivered hair, topology-agnostic so the v6 and v7 rigs are measured by the same rule. Read-only, in memory.

    blender --background <rigged or improved wren.blend> --factory-startup --python wren_hair_diag.py -- <out.json>

Per lock (the 'conquest_islands' ranges of the main mesh, rest / bind shape):
  interpenetration   triangle pairs where two DIFFERENT locks cut through each other (BVHTree.overlap) + lock pairs involved;
                     the same against the hair cap (a lock sunk into the cap)
  paint patches      connected components (shared edge) of every paint region on the lock: the patchwork count; region
                     boundary edges
  facet kinks        the dihedral between neighbouring TOP faces (not hair_shade) of one lock: what a flat render shows
  tiny / sliver      faces under 0.25 mm2; aspect (longest edge^2 / 2 area) over 20
"""
import bpy, sys, json, math
import numpy as np
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
main = max((o for o in bpy.context.scene.objects if o.type == "MESH" and "conquest_islands" in o.data.keys()),
           key=lambda o: len(o.data.polygons))
me = main.data
isl = json.loads(me["conquest_islands"])
V = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
F = [lv[a:a + n] for a, n in zip(ls, lt)]
names_reg = list(me["conquest_regions"])
rid = np.empty(len(me.polygons), dtype=np.int32)
me.attributes["region_id"].data.foreach_get("value", rid)
if names_reg is None:
    names_reg = [str(i) for i in range(int(rid.max()) + 1)]
owner = np.full(len(V), "", dtype=object)
for n_, (a_, b_) in isl.items():
    owner[a_:b_] = n_
fown = np.array([owner[f[0]] for f in F], dtype=object)
locks = sorted(n_ for n_ in isl if n_.startswith("lock."))
rep = {"blend": bpy.data.filepath, "locks": len(locks)}


def tris_of(fids):
    return [[int(F[i][0]), int(F[i][k]), int(F[i][k + 1])] for i in fids for k in range(1, len(F[i]) - 1)]


LF = {n_: np.nonzero(fown == n_)[0] for n_ in locks + ["hair_cap"]}
BV_ = {n_: BVHTree.FromPolygons(V.tolist(), tris_of(LF[n_])) for n_ in LF}
rep["tris"] = {"locks": int(sum(len(tris_of(LF[n_])) for n_ in locks)), "cap": len(tris_of(LF["hair_cap"]))}
# interpenetration (shared vertices: none -- every lock is its own closed solid)
pairs, tri_pairs, per_lock, cap_pairs = [], 0, {}, 0
for i, a in enumerate(locks):
    for b in locks[i + 1:]:
        ov = BV_[a].overlap(BV_[b])
        if ov:
            pairs.append((a, b, len(ov))); tri_pairs += len(ov)
            per_lock[a] = per_lock.get(a, 0) + len(ov); per_lock[b] = per_lock.get(b, 0) + len(ov)
    cap_pairs += len(BV_[a].overlap(BV_["hair_cap"]))
rep["interpenetration"] = {"lock_pairs": len(pairs), "tri_pairs": tri_pairs, "lock_cap_tri_pairs": cap_pairs,
                           "locks_involved": len(per_lock),
                           "worst_pairs": [list(p) for p in sorted(pairs, key=lambda p: -p[2])[:8]]}
# the TOP sheets only (every face but the hair_shade undersides): the crossings the eye reads
_shade = list(me["conquest_regions"]).index("hair_shade") if "hair_shade" in list(me["conquest_regions"]) else -1
_rid0 = np.empty(len(me.polygons), dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", _rid0)
BT_ = {n_: BVHTree.FromPolygons(V.tolist(), tris_of([i for i in LF[n_] if _rid0[i] != _shade])) for n_ in locks}
tp_, lp_ = 0, 0
for i, a in enumerate(locks):
    for b in locks[i + 1:]:
        ov = BT_[a].overlap(BT_[b])
        if ov:
            lp_ += 1; tp_ += len(ov)
rep["interpenetration"]["top_sheets"] = {"lock_pairs": lp_, "tri_pairs": tp_}
# paint patches + facet kinks + tiny faces
shade_id = names_reg.index("hair_shade") if "hair_shade" in names_reg else -1
fn = np.array([np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]) for f in F])
fa = np.array([0.5 * np.linalg.norm(sum(np.cross(V[f[k]] - V[f[0]], V[f[k + 1]] - V[f[0]]) for k in range(1, len(f) - 1)))
               for f in F])
fnu = fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-18)[:, None]
patches, bedges, dih, tiny, sliver, area = 0, 0, [], 0, 0, 0.0
crev_id = names_reg.index("hair_crevice") if "hair_crevice" in names_reg else -2
ring_id = names_reg.index("hair_ring") if "hair_ring" in names_reg else -2
crev_patches = ring_patches = 0
for n_ in locks:
    fids = LF[n_]
    E = {}
    for i in fids:
        f = F[i]
        for k in range(len(f)):
            e = (min(f[k], f[(k + 1) % len(f)]), max(f[k], f[(k + 1) % len(f)]))
            E.setdefault(e, []).append(i)
    par = {int(i): int(i) for i in fids}

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for e, fs in E.items():
        if len(fs) == 2:
            a, b = fs
            if rid[a] == rid[b]:
                par[find(a)] = find(b)
            else:
                bedges += 1
            if rid[a] != shade_id and rid[b] != shade_id:
                dih.append(math.degrees(math.acos(float(np.clip(fnu[a] @ fnu[b], -1, 1)))))
    roots = {}
    for i in fids:
        roots.setdefault(find(int(i)), rid[i])
    patches += len(roots)
    crev_patches += sum(1 for r in roots.values() if r == crev_id)
    ring_patches += sum(1 for r in roots.values() if r == ring_id)
    for i in fids:
        f = F[i]
        L2 = max(float(np.sum((V[f[k]] - V[f[(k + 1) % len(f)]]) ** 2)) for k in range(len(f)))
        tiny += int(fa[i] < 2.5e-7)
        sliver += int(fa[i] > 0 and L2 / (2.0 * fa[i]) > 20.0)
        area += fa[i]
dih = np.array(dih)
rep["paint"] = {"patches_total": patches, "patches_per_lock": round(patches / max(len(locks), 1), 2),
                "crevice_patches": crev_patches, "ring_patches": ring_patches, "region_boundary_edges": bedges}
rep["facet_kinks_top_deg"] = {"p50": round(float(np.percentile(dih, 50)), 2), "p90": round(float(np.percentile(dih, 90)), 2),
                              "max": round(float(dih.max()), 2), "share_over_20deg_pct": round(100.0 * float((dih > 20).mean()), 1),
                              "edges": int(len(dih))}
rep["faces"] = {"lock_faces": int(sum(len(LF[n_]) for n_ in locks)), "tiny_under_0.25mm2": tiny, "sliver_aspect_over_20": sliver,
                "lock_area_cm2": round(1e4 * area, 1)}
print("HAIRDIAG", json.dumps(rep))
json.dump(rep, open(OUT, "w"), indent=1)
sys.stdout.flush()
import os
os._exit(0)
