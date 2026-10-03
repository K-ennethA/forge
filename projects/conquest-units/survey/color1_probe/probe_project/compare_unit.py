"""Vertex-by-vertex check of Godot's CUSTOM0 against the glb's glow (_GLOW float when present, else COLOR_1) (skinned-unit gate, 2026-10-02).

    python compare_unit.py <unit.glb> <dump_unit.json>

Godot surface s of mesh node N <-> glb primitive s of the mesh on glb node N. Each Godot vertex is matched to the glb
vertices of that primitive at the same position (tolerance = Godot's attribute-compression step x 4), same normal
(dot > 0.98) and same UV (2e-3); MATCH = some such glb vertex has COLOR_1 within 2e-3 of CUSTOM0 on all 4 channels,
MISMATCH = candidates exist but none agrees, UNMATCHED = no candidate (vertex not traceable).
"""
import json
import struct
import sys
from collections import defaultdict

import numpy as np


def read_glb(path):
    d = open(path, "rb").read()
    jl = struct.unpack_from("<I", d, 12)[0]
    return d, json.loads(d[20:20 + jl]), 20 + jl + 8


def acc(d, js, bo, ai):
    a = js["accessors"][ai]
    bv = js["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    dt = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[a["componentType"]]
    sz = np.dtype(dt).itemsize
    stride = bv.get("byteStride", n * sz)
    off = bo + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    raw = np.frombuffer(d, dtype=np.uint8, count=stride * (a["count"] - 1) + n * sz, offset=off)
    out = np.stack([np.frombuffer(raw[i * stride:i * stride + n * sz].tobytes(), dtype=dt) for i in range(a["count"])])
    out = out.astype(np.float64)
    if a.get("normalized"):
        out /= {"<u2": 65535.0, "u1": 255.0}[dt]
    return out


def check_surface(gs, prim, d, js, bo):
    P = acc(d, js, bo, prim["attributes"]["POSITION"])
    N = acc(d, js, bo, prim["attributes"]["NORMAL"])
    U = acc(d, js, bo, prim["attributes"]["TEXCOORD_0"])
    ref = "_GLOW" if "_GLOW" in prim["attributes"] else "COLOR_1"   # the extension's own source choice
    C = acc(d, js, bo, prim["attributes"][ref])
    if C.shape[1] == 3:
        C = np.hstack([C, np.ones((len(C), 1))])
    gp = np.array(gs["pos"]).reshape(-1, 3)
    gn = np.array(gs["nrm"]).reshape(-1, 3)
    gu = np.array(gs["uv"]).reshape(-1, 2)
    gc = np.array(gs["custom0"]).reshape(-1, 4) if gs["custom0"] else None
    ext = (P.max(0) - P.min(0)).max()
    tol = max(1e-5, 4 * ext / 65535)
    grid = defaultdict(list)
    for i, p in enumerate(P):
        grid[tuple(np.floor(p / tol).astype(int))].append(i)
    res = {"ref_attr": ref, "ref_max": round(float(C[:, :3].max()), 4),
           "godot_verts": len(gp), "glb_verts": len(P), "pos_tol_m": round(tol, 6),
           "custom0_present": gc is not None, "match": 0, "mismatch": 0, "unmatched": 0,
           "glb_colour_distinct": len(np.unique(np.round(C, 4), axis=0))}
    if gc is None:
        return res
    res["custom0_distinct"] = len(np.unique(np.round(gc, 4), axis=0))
    res["custom0_max"] = round(float(gc[:, :3].max()), 4)
    res["custom0_verts_above_1"] = int((gc[:, :3] > 1 + 1e-6).any(axis=1).sum())
    res["same_index_order_matches"] = int((np.abs(gc - C).max(1) < 2e-3).sum()) if len(gc) == len(C) else None
    bad = []
    for i in range(len(gp)):
        k = np.floor(gp[i] / tol).astype(int)
        cand = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                for j in grid.get((k[0] + dx, k[1] + dy, k[2] + dz), ())]
        cand = [j for j in cand if np.abs(P[j] - gp[i]).max() <= tol and N[j] @ gn[i] > 0.98
                and np.abs(U[j] - gu[i]).max() < 2e-3]
        if not cand:
            res["unmatched"] += 1
        elif any(np.abs(C[j] - gc[i]).max() < 2e-3 for j in cand):
            res["match"] += 1
        else:
            res["mismatch"] += 1
            if len(bad) < 5:
                bad.append({"v": i, "custom0": gc[i].round(4).tolist(), "glb": [C[j].round(4).tolist() for j in cand[:3]]})
    res["mismatch_examples"] = bad
    return res


def main():
    glb_path, dump_path = sys.argv[1], sys.argv[2]
    d, js, bo = read_glb(glb_path)
    dump = json.load(open(dump_path))
    node_mesh = {n["name"]: n["mesh"] for n in js["nodes"] if "mesh" in n}
    out = {"unit": dump["unit"], "godot": dump["godot"]}
    for path in ("imported", "runtime"):
        rows = []
        for m in dump[path]["meshes"]:
            mi = node_mesh.get(m["node"])
            if mi is None:
                rows.append({"node": m["node"], "error": "no glb node of that name"})
                continue
            prims = js["meshes"][mi]["primitives"]
            row = {"node": m["node"], "skin": m["skin"], "skeleton_ok": m["skeleton_ok"],
                   "godot_surfaces": len(m["surfaces"]), "glb_primitives": len(prims), "surfaces": []}
            for s in m["surfaces"]:
                r = {k: s[k] for k in ("surface", "has_custom0", "custom0_type", "has_color", "has_bones",
                                       "has_weights", "compressed", "blend_shapes")}
                if s["surface"] < len(prims):
                    r.update(check_surface(s, prims[s["surface"]], d, js, bo))
                row["surfaces"].append(r)
            rows.append(row)
        out[path] = {"meshes": rows, "animations": dump[path].get("animations"),
                     "skeleton_bones": dump[path].get("skeleton_bones"),
                     "append_err": dump[path].get("append_err"), "error": dump[path].get("error")}
        tot = [s for r in rows for s in r.get("surfaces", [])]
        out[path]["totals"] = {k: sum(s.get(k, 0) for s in tot) for k in ("godot_verts", "match", "mismatch", "unmatched")}
        out[path]["totals"]["surfaces_with_custom0"] = "%d/%d" % (sum(s["has_custom0"] for s in tot), len(tot))
    print("COMPARE " + json.dumps(out))


if __name__ == "__main__":
    main()
