"""Portrait-fix gate (read-only): the world AABB Conquest's PortraitCache measures vs the model's REAL (skinned) extent.

    blender --background --factory-startup --python duskmaw_aabb_check.py -- <out.json> <label>=<glb>[@<model_scale>] ...

PortraitCache (Conquest game/ui/PortraitCache.gd) frames its camera on the union of every VisualInstance3D's
get_aabb() transformed by its global_transform. Godot's glTF importer reparents a SKINNED mesh under its Skeleton3D
with an identity transform, and the Skeleton3D sits where the skeleton's root joint's parent node is -- so the
measured box is (root joint's parent world matrix) x (mesh POSITION bounds). The mesh is RENDERED where glTF skinning
puts it: sum_j w_j * (joint_world_j @ inverse_bind_j) @ v. When the export parents the skeleton under a scaled node
(the shipped monster.glb: MonsterRig scale 0.0585, vertices already at game size), the two disagree by that scale.

This script parses the glb directly (no importer in the loop), computes both boxes after PortraitCache's own model
transform (yaw = roster model_yaw_deg + 180 + 15, uniform model_scale), then replays _frame_camera() on the MEASURED
box and reports where the camera lands relative to the RENDERED model.
"""
import sys, json, struct, math
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
JOBS = []
for a in argv[1:]:
    lab, rest = a.split("=", 1)
    path, _, sc = rest.partition("@")
    yaw = 0.0
    if "#" in path:
        path, yaw_s = path.split("#", 1); yaw = float(yaw_s)
    if sc == "fit":                   # the report-only cell fit the roster would carry (improved/duskmaw.json)
        import os
        rep_ = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "improved", "duskmaw.json")))
        sc = rep_["natural"]["export_cell_fit_report_only"]["scale"]
    JOBS.append((lab, path, float(sc) if sc else 1.0, yaw))

CT = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8, 5122: np.int16, 5120: np.int8}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
# PortraitCache constants
HEAD_REGION_FRACTION, FRAME_FILL_FRACTION, CAPTURE_FOV_DEG, CAPTURE_YAW_DEG = 0.35, 0.80, 30.0, 15.0


def load(path):
    raw = open(path, "rb").read()
    jl = struct.unpack("<I", raw[12:16])[0]
    gj = json.loads(raw[20:20 + jl])
    off = 20 + jl
    bl = struct.unpack("<I", raw[off:off + 4])[0]
    return gj, raw[off + 8:off + 8 + bl]


def accessor(gj, binc, i):
    a = gj["accessors"][i]
    bv = gj["bufferViews"][a["bufferView"]]
    dt = np.dtype(CT[a["componentType"]])
    nc = NC[a["type"]]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", 0) or dt.itemsize * nc
    buf = np.frombuffer(binc, dtype=np.uint8, count=stride * (a["count"] - 1) + dt.itemsize * nc, offset=start)
    out = np.lib.stride_tricks.as_strided(buf, shape=(a["count"], dt.itemsize * nc), strides=(stride, 1)).copy()
    arr = out.view(dt).reshape(a["count"], nc).astype(np.float64)
    if a.get("normalized"):
        arr /= float(np.iinfo(dt).max)
    return arr


def local_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], float).reshape(4, 4).T
    t = np.array(n.get("translation", [0, 0, 0]), float)
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    s = np.array(n.get("scale", [1, 1, 1]), float)
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4); M[:3, :3] = R * s[None, :]; M[:3, 3] = t
    return M


def aabb(P):
    return P.min(0), P.max(0)


def box_corners(lo, hi):
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def xf(M, P):
    return P @ M[:3, :3].T + M[:3, 3]


def frame_camera(lo, hi):
    size = hi - lo
    top_y = lo[1] + size[1]
    head_h = max(size[1] * HEAD_REGION_FRACTION, 0.05)
    target_y = top_y - head_h * 0.5
    cx, cz = lo[0] + size[0] * 0.5, lo[2] + size[2] * 0.5
    frame_h = head_h / FRAME_FILL_FRACTION
    dist = max((frame_h * 0.5) / math.tan(math.radians(CAPTURE_FOV_DEG) * 0.5), 0.3)
    return np.array([cx, target_y, cz]), np.array([cx, target_y, cz + dist]), frame_h, dist


rows = {}
for lab, path, model_scale, yaw in JOBS:
    gj, binc = load(path)
    nodes = gj["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    world = {}

    def wm(i):
        if i not in world:
            world[i] = (wm(parent[i]) if i in parent else np.eye(4)) @ local_matrix(nodes[i])
        return world[i]
    scene_roots = gj["scenes"][gj.get("scene", 0)]["nodes"]
    # PortraitCache's model transform: rotation about Y by model_yaw + 180 + 15, uniform model_scale
    th = math.radians(yaw + 180.0 + CAPTURE_YAW_DEG)
    Mroot = np.eye(4)
    Mroot[:3, :3] = np.array([[math.cos(th), 0, math.sin(th)], [0, 1, 0], [-math.sin(th), 0, math.cos(th)]]) * model_scale
    meas_pts, rend_pts, per_mesh = [], [], []
    for i, n in enumerate(nodes):
        if "mesh" not in n:
            continue
        mesh = gj["meshes"][n["mesh"]]
        for prim in mesh["primitives"]:
            P = accessor(gj, binc, prim["attributes"]["POSITION"])
            lo, hi = aabb(P)
            if "skin" in n:
                skin = gj["skins"][n["skin"]]
                joints = skin["joints"]
                jset = set(joints)
                roots_ = [j for j in joints if parent.get(j) not in jset]
                skel_parent = parent.get(roots_[0])
                Mskel = wm(skel_parent) if skel_parent is not None else np.eye(4)
                meas = xf(Mskel, box_corners(lo, hi))
                IBM = accessor(gj, binc, skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
                JM = np.stack([wm(j) @ IBM[k] for k, j in enumerate(joints)])
                Jt = accessor(gj, binc, prim["attributes"]["JOINTS_0"]).astype(np.int64)
                Wt = accessor(gj, binc, prim["attributes"]["WEIGHTS_0"])
                Ph = np.hstack([P, np.ones((len(P), 1))])
                R_ = np.zeros((len(P), 4))
                for k in range(4):
                    R_ += Wt[:, k:k + 1] * np.einsum("nij,nj->ni", JM[Jt[:, k]], Ph)
                rend = R_[:, :3]
                kind = "skinned (Godot: MeshInstance3D under Skeleton3D, identity; skeleton under node %s '%s')" % (
                    skel_parent, nodes[skel_parent].get("name") if skel_parent is not None else None)
                sk_scale = float(np.cbrt(abs(np.linalg.det(Mskel[:3, :3]))))
            else:
                meas = xf(wm(i), box_corners(lo, hi))
                rend = xf(wm(i), P)
                kind = "static"
                sk_scale = float(np.cbrt(abs(np.linalg.det(wm(i)[:3, :3]))))
            meas_w = xf(Mroot, meas)
            rend_w = xf(Mroot, rend)
            meas_pts.append(box_corners(*aabb(meas_w))); rend_pts.append(rend_w)
            ml, mh = aabb(meas_w); rl, rh = aabb(rend_w)
            per_mesh.append({"node": n.get("name"), "kind": kind, "vertices": int(len(P)),
                             "placement_scale": round(sk_scale, 6),
                             "measured_size": (mh - ml).round(4).tolist(), "rendered_size": (rh - rl).round(4).tolist()})
    ML, MH = aabb(np.vstack(meas_pts)); RL, RH = aabb(np.vstack(rend_pts))
    target, cam, frame_h, dist = frame_camera(ML, MH)
    rsize = RH - RL; msize = MH - ML
    inside = bool(np.all(cam >= RL) and np.all(cam <= RH))
    # what the camera frames of the REAL model: frame band vs the rendered head band
    band = [float(target[1] - frame_h / 2), float(target[1] + frame_h / 2)]
    rt, rcam, rframe_h, rdist = frame_camera(RL, RH)
    rows[lab] = {
        "glb": path, "model_scale": model_scale, "model_yaw_deg": yaw,
        "top_nodes": [nodes[r].get("name") for r in scene_roots],
        "meshes": per_mesh,
        "measured_aabb_size": msize.round(4).tolist(), "rendered_aabb_size": rsize.round(4).tolist(),
        "rendered_over_measured_height": round(float(rsize[1] / max(msize[1], 1e-9)), 4),
        "portrait_camera": {"target": target.round(4).tolist(), "position": cam.round(4).tolist(),
                            "distance": round(dist, 4), "distance_clamped_to_min_0.3": bool(dist <= 0.3 + 1e-9),
                            "frame_height": round(frame_h, 4), "frame_y_band": [round(v, 4) for v in band],
                            "inside_rendered_model_aabb": inside,
                            "rendered_model_y": [round(float(RL[1]), 4), round(float(RH[1]), 4)],
                            "rendered_model_z_front": round(float(RH[2]), 4)},
        "portrait_camera_if_measured_right": {"target": rt.round(4).tolist(), "position": rcam.round(4).tolist(),
                                              "distance": round(rdist, 4)},
        "pass": bool(abs(rsize[1] / max(msize[1], 1e-9) - 1.0) < 0.01 and not inside)}
    print("AABB", lab, json.dumps({k: rows[lab][k] for k in ("measured_aabb_size", "rendered_aabb_size",
                                                               "rendered_over_measured_height", "pass")}))
json.dump(rows, open(OUT, "w"), indent=1, default=float)
sys.stdout.flush()
