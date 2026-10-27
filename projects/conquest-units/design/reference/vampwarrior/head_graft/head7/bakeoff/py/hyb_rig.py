"""hyb_rig.py -- <fit.npz> <build_dir> : rig the hybrid head (opened from <build_dir>/hyb.blend).
Armature (f-frame = Blender world; exported glTF +Y up == the head7 glb frame): head, jaw (child of head), eye.L, eye.R.
Weights: head mesh head / jaw (lower lip + chin + lower mouth bag on the jaw, a smooth fade over the cheeks, the jaw
back and the throat; the upper lip never on the jaw); eyes rigid; upper teeth + fangs on the head; lower teeth + tongue
on the jaw.
Shape keys (head; the lower teeth / tongue carry matching jaw keys so keys alone are consistent): blink_L, blink_R
(the upper lid rotated about the eyeball centre down to 70 % of the opening, the lower lid up 30 %, falloff over the lid
rings, so the lid rides the ball), jaw_open (18 deg), viseme_A (11 deg + upper lip lift), viseme_O (7 deg + pucker),
viseme_M (lips pressed shut), brow_raise.
Tests per pose: edge stretch (p99 / max) in the face, flipped triangles, self-intersecting triangle pairs (BVH overlap,
neighbours excluded), lid vertices inside the eyeball. Writes rig_report.json, hybrid_head.glb, hyb_rigged.blend."""
import sys, os, json, math, numpy as np, bpy
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); OUT = a[1]
bpy.ops.wm.open_mainfile(filepath=os.path.join(OUT, "hyb.blend"))
sc = bpy.context.scene
head = bpy.data.objects["head"]
me = head.data
V0 = np.array([v.co for v in me.vertices])
nv = len(V0)
# rest: no skin vertex inside an eyeball (the lids ride on the ball)
_pk = np.zeros(nv, bool); _pk[Z["pocket_l"]] = True; _pk[Z["pocket_r"]] = True
_pushed = 0
for _k in range(2):
    _c = Z["ball_c"][_k]; _R = float(Z["ball_r"][_k])
    _d = np.linalg.norm(V0 - _c, axis=1)
    _m = (_d < _R + 0.0008) & ~_pk
    V0[_m] = _c + (V0[_m] - _c) / _d[_m, None] * (_R + 0.0008); _pushed += int(_m.sum())
me.vertices.foreach_set("co", V0.astype(np.float32).ravel()); me.update()
Fq = np.array([list(p.vertices) for p in me.polygons])
rep = {}
rep["rest_lid_verts_pushed_out_of_ball"] = 0


def ss(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t)


nbr = [set() for _ in range(nv)]
for f in Fq:
    for k in range(len(f)):
        nbr[f[k]].add(f[(k + 1) % len(f)]); nbr[f[(k + 1) % len(f)]].add(f[k])
rows = np.concatenate([[i] * len(x) for i, x in enumerate(nbr)]).astype(np.int64)
cols = np.concatenate([list(x) for x in nbr]).astype(np.int64)
deg = np.array([len(x) for x in nbr])
lu = Z["lip_up_line"]; ll = Z["lip_lo_line"]
bag = Z["bag"]; up_f = Z["up_f"]; lo_f = Z["lo_f"]
upv = np.unique(Fq[up_f]); lov = np.unique(Fq[lo_f])
LU = V0[lu][np.argsort(V0[lu, 0])]; LL = V0[ll][np.argsort(V0[ll, 0])]
xc0, xc1 = LU[0, 0], LU[-1, 0]


def zline(P, L):
    return np.interp(np.clip(P[:, 0], xc0, xc1), L[:, 0], L[:, 2])


# ---------------------------------------------------------------- jaw weights
hinge = np.array([0.0, 0.06, -0.03])
zl = zline(V0, LL)
wv = ss(zl + 0.004, zl - 0.014, V0[:, 2])
wb = ss(0.12, 0.0, V0[:, 1])
wn = np.where(V0[:, 1] > -0.16, ss(-0.43, -0.28, V0[:, 2]), 1.0)
wj = wv * wb * wn
forced = np.zeros(nv, bool)
wj[lov] = 1.0; forced[lov] = True
wj[upv] = 0.0; forced[upv] = True
wj[ll] = 1.0; wj[lu] = 0.0
corners = [int(lu[np.argmin(V0[lu, 0])]), int(lu[np.argmax(V0[lu, 0])])]
wj[corners] = 0.5
forced[ll] = True; forced[lu] = True; forced[corners] = True
bagz = V0[bag, 2] < 0.5 * (zline(V0[bag], LU) + zline(V0[bag], LL))
wj[bag] = np.where(bagz, 1.0, 0.0)
for _ in range(6):
    a_ = np.zeros(nv); np.add.at(a_, rows, wj[cols]); a_ /= deg
    wj[~forced] = 0.5 * wj[~forced] + 0.5 * a_[~forced]
wj = np.clip(wj, 0, 1)
rep["jaw_weights"] = {"verts_gt_0.5": int((wj > 0.5).sum()), "verts_gt_0.01": int((wj > 0.01).sum())}

# ---------------------------------------------------------------- armature
arm_d = bpy.data.armatures.new("head_rig"); arm = bpy.data.objects.new("head_rig", arm_d); sc.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
for o in bpy.data.objects: o.select_set(o is arm)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_d.edit_bones
bh = eb.new("head"); bh.head = (0, 0.03, -0.40); bh.tail = (0, 0.03, 0.30)
bj = eb.new("jaw"); bj.head = tuple(hinge); bj.tail = (0, -0.21, -0.24); bj.parent = bh
ECn = Z["ball_c"]
for c in ECn:
    nm = "eye.L" if c[0] > 0 else "eye.R"
    be = eb.new(nm); be.head = tuple(c); be.tail = tuple(c + np.array([0, -0.06, 0])); be.parent = bh
bpy.ops.object.mode_set(mode="OBJECT")


def bind(ob, groups):
    for g, w in groups.items():
        vg = ob.vertex_groups.new(name=g)
        w = np.broadcast_to(np.asarray(w, float), (len(ob.data.vertices),))
        for i, wi in enumerate(w):
            if wi > 1e-4:
                vg.add([i], float(wi), "REPLACE")
    m = ob.modifiers.new("Armature", "ARMATURE"); m.object = arm
    ob.parent = arm


bind(head, {"head": 1.0 - wj, "jaw": wj})
for nm in ("eye.L", "eye.R"):
    bind(bpy.data.objects[nm], {nm: 1.0})
for nm in ("teeth_upper", "fang.L", "fang.R"):
    bind(bpy.data.objects[nm], {"head": 1.0})
for nm in ("teeth_lower", "tongue"):
    bind(bpy.data.objects[nm], {"jaw": 1.0})


# ---------------------------------------------------------------- shape keys
def rot_about(P, piv, axis, ang):
    axis = axis / np.linalg.norm(axis)
    R = np.array(Matrix.Rotation(float(ang), 3, Vector(axis)))
    return piv + (P - piv) @ R.T


def jaw_pose(P, w, deg_):
    Pr = rot_about(P, hinge, np.array([1.0, 0.0, 0.0]), math.radians(deg_))
    return P + np.asarray(w)[:, None] * (Pr - P)


ringarr = Z["ring"]
blink = {}
for k, side in enumerate(("l", "r")):
    c = Z["ball_c"][k]; R = float(Z["ball_r"][k])
    up = Z["eye_up_" + side]; lo = Z["eye_lo_" + side]; cn = Z["eye_corners"][k]
    axis = V0[cn[1]] - V0[cn[0]]; axis /= np.linalg.norm(axis)
    zc = lambda P: V0[cn[0], 2] + (P[:, 0] - V0[cn[0], 0]) / (V0[cn[1], 0] - V0[cn[0], 0]) * (V0[cn[1], 2] - V0[cn[0], 2])
    Lo = V0[lo][np.argsort(V0[lo, 0])]; Up = V0[up][np.argsort(V0[up, 0])]

    def angle_to(p, t):
        u_ = p - c; v_ = t - c
        u_ = u_ - (u_ @ axis) * axis; v_ = v_ - (v_ @ axis) * axis
        return math.atan2(float(np.cross(u_, v_) @ axis), float(u_ @ v_))
    # per margin column: the closing target 30 % up from the lower lid
    th_up = []
    for p in Up:
        lo_p = np.array([p[0], np.interp(p[0], Lo[:, 0], Lo[:, 1]), np.interp(p[0], Lo[:, 0], Lo[:, 2])])
        t_ = lo_p + 0.3 * (p - lo_p)
        th_up.append(angle_to(p, t_))
    th_lo = []
    for p in Lo:
        up_p = np.array([p[0], np.interp(p[0], Up[:, 0], Up[:, 1]), np.interp(p[0], Up[:, 0], Up[:, 2])])
        t_ = p + 0.3 * (up_p - p)
        th_lo.append(angle_to(p, t_))
    th_up = np.array(th_up); th_lo = np.array(th_lo)
    fall_u = {1: 1.0, 2: 1.0, 3: 0.92, 4: 0.72, 5: 0.45, 6: 0.2, 7: 0.06}
    fall_l = {1: 1.0, 2: 0.85, 3: 0.5, 4: 0.2, 5: 0.05}
    D = np.zeros_like(V0)
    rk = ringarr[k]
    sel = np.nonzero((rk >= 1) & (rk <= 7))[0]
    above = V0[sel, 2] >= zc(V0[sel])
    for i, ab in zip(sel, above):
        r = int(rk[i])
        if ab:
            f_ = fall_u.get(r, 0.0); th = float(np.interp(V0[i, 0], Up[:, 0], th_up)) * f_
        else:
            f_ = fall_l.get(r, 0.0); th = float(np.interp(V0[i, 0], Lo[:, 0], th_lo)) * f_
        if f_ <= 0:
            continue
        # fade toward the corners (the corners stay)
        tx = (V0[i, 0] - V0[cn[0], 0]) / (V0[cn[1], 0] - V0[cn[0], 0])
        th *= float(ss(0.0, 0.12, tx) * ss(1.0, 0.88, tx)) if r == 1 else 1.0
        D[i] = rot_about(V0[i][None], c, axis, th)[0] - V0[i]
    blink[side] = D
    rep["blink_" + side] = {"verts": int((np.linalg.norm(D, axis=1) > 1e-6).sum()),
                            "max_angle_deg": round(float(np.degrees(np.abs(th_up).max())), 1)}

lipreg = np.zeros(nv, bool); lipreg[upv] = True; lipreg[lov] = True; lipreg[lu] = True; lipreg[ll] = True
wl = lipreg.astype(float)
for _ in range(3):
    a_ = np.zeros(nv); np.add.at(a_, rows, wl[cols]); a_ /= deg
    wl = np.maximum(wl, 0.8 * a_)
mouth_c = 0.5 * (LU.mean(0) + LL.mean(0))
keys = {}
keys["blink_L"] = V0 + blink["l"]; keys["blink_R"] = V0 + blink["r"]
keys["jaw_open"] = jaw_pose(V0, wj, 18.0)
P = jaw_pose(V0, wj, 11.0)
wup = np.zeros(nv); wup[upv] = 1.0
for _ in range(2):
    a_ = np.zeros(nv); np.add.at(a_, rows, wup[cols]); a_ /= deg; wup = np.maximum(wup, 0.7 * a_)
P[:, 2] += 0.003 * wup * (1 - wj)
P[:, 0] += (P[:, 0] - mouth_c[0]) * 0.04 * wl
keys["viseme_A"] = P
P = jaw_pose(V0, wj, 7.0)
P[:, 0] -= (P[:, 0] - mouth_c[0]) * 0.28 * wl
P[:, 1] -= 0.006 * wl
keys["viseme_O"] = P
P = V0.copy()
gap = zline(V0, LU) - zline(V0, LL)
wlow = np.zeros(nv); wlow[lov] = 1.0; wlow[ll] = 1.0
for _ in range(3):
    a_ = np.zeros(nv); np.add.at(a_, rows, wlow[cols]); a_ /= deg; wlow = np.maximum(wlow, 0.75 * a_)
wlow[upv] = 0; wlow[lu] = 0
P[:, 2] += gap * wlow * 0.85
P[:, 1] += 0.002 * wl
keys["viseme_M"] = P
P = V0.copy()
wbz = ss(0.105, 0.15, V0[:, 2]) * ss(0.34, 0.2, V0[:, 2]) * ss(0.32, 0.24, np.abs(V0[:, 0])) * (V0[:, 1] < -0.02)
P[:, 2] += 0.012 * wbz
keys["brow_raise"] = P

head.shape_key_add(name="Basis")
for nm, P in keys.items():
    kb = head.shape_key_add(name=nm); kb.data.foreach_set("co", P.astype(np.float32).ravel())
jaw_deg = {"jaw_open": 18.0, "viseme_A": 11.0, "viseme_O": 7.0}
for nm in ("teeth_lower", "tongue"):
    ob = bpy.data.objects[nm]; Pp = np.array([v.co for v in ob.data.vertices])
    ob.shape_key_add(name="Basis")
    for kn in keys:
        kb = ob.shape_key_add(name=kn)
        Pk = jaw_pose(Pp, np.ones(len(Pp)), jaw_deg[kn]) if kn in jaw_deg else Pp
        kb.data.foreach_set("co", Pk.astype(np.float32).ravel())

# ---------------------------------------------------------------- tests
tri = []
for f in Fq:
    for t in range(1, len(f) - 1):
        tri.append([f[0], f[t], f[t + 1]])
tri = np.array(tri)
E = np.unique(np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), 1), axis=0)
skin_v = np.ones(nv, bool); skin_v[bag] = False
for side in ("l", "r"):
    skin_v[Z["pocket_" + side]] = False
face_box = (V0[:, 1] < -0.05) & (V0[:, 2] > -0.3) & (V0[:, 2] < 0.3)


SXR = {}
def selfx(P):
    t = BVHTree.FromPolygons(P.tolist(), tri.tolist())
    pairs = t.overlap(t)
    n = 0; where = {"eyes": 0, "mouth": 0, "ears": 0, "nose": 0, "other": 0}
    for i, j in pairs:
        if i < j and not (set(tri[i]) & set(tri[j])):
            if skin_v[tri[i]].all() and skin_v[tri[j]].all():
                n += 1
                cc = P[tri[i]].mean(0)
                if abs(abs(cc[0]) - 0.12) < 0.1 and abs(cc[2] - 0.092) < 0.07 and cc[1] < -0.08: where["eyes"] += 1
                elif abs(cc[0]) < 0.12 and -0.2 < cc[2] < -0.06 and cc[1] < -0.1: where["mouth"] += 1
                elif abs(cc[0]) > 0.25: where["ears"] += 1
                elif abs(cc[0]) < 0.08 and -0.07 < cc[2] < 0.05: where["nose"] += 1
                else: where["other"] += 1
    SXR["last"] = where
    return n


def test(P, name):
    em = face_box[E[:, 0]] & skin_v[E[:, 0]] & skin_v[E[:, 1]]
    l0 = np.linalg.norm(V0[E[em, 0]] - V0[E[em, 1]], axis=1); l1 = np.linalg.norm(P[E[em, 0]] - P[E[em, 1]], axis=1)
    r = l1 / np.maximum(l0, 1e-9)
    n0 = np.cross(V0[tri[:, 1]] - V0[tri[:, 0]], V0[tri[:, 2]] - V0[tri[:, 0]])
    n1 = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
    vis = skin_v[tri].all(1)
    flips = int((((n0 * n1).sum(1) < 0) & vis).sum())
    pen = 0
    for k in range(2):
        c = Z["ball_c"][k]; R = float(Z["ball_r"][k])
        d = np.linalg.norm(P - c, axis=1)
        pen += int(((d < R - 0.0003) & skin_v).sum())
    return {"stretch_p99": round(float(np.percentile(r, 99)), 3), "stretch_max": round(float(r.max()), 3),
            "compress_p1": round(float(np.percentile(r, 1)), 3), "edges_over_1.5x": int((r > 1.5).sum()),
            "flipped_tris": flips, "self_intersections": selfx(P), "self_x_where": dict(SXR["last"]),
            "lid_verts_inside_eyeball": pen}


tests = {"rest": test(V0, "rest")}
for nm, P in keys.items():
    tests[nm] = test(P, nm)
# the jaw through the BONE (linear blend skinning) = the same as the jaw_open key by construction
rep["tests"] = tests
print("TESTS", json.dumps(tests))
rep["rest_lid_verts_pushed_out_of_ball"] = _pushed
for ob_ in bpy.data.objects:
    if ob_.type == "MESH" and ob_.data.shape_keys:
        for kb in ob_.data.shape_keys.key_blocks:
            kb.value = 0.0
# ---------------------------------------------------------------- export
for o in bpy.data.objects:
    o.select_set(o.type in ("MESH", "ARMATURE"))
bpy.context.view_layer.objects.active = arm
glb = os.path.join(OUT, "hybrid_head.glb")
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_morph=True, export_skins=True, export_animations=False, export_texcoords=True,
                          export_normals=True, export_tangents=True, export_materials="EXPORT", export_image_format="AUTO")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "hyb_rigged.blend"))
info = {"frame": "glTF +Y up, the SAME frame as head7_s56_edit.glb (Blender build frame f = (x, -z_glb, y_glb)); "
                 "head height ~0.97 glb units; the kit's head_graft similarity (yaw 90, mid_z, scale 1.07, place) applies",
        "bones": {b.name: {"head": [round(c, 4) for c in b.head_local], "tail": [round(c, 4) for c in b.tail_local],
                           "parent": b.parent.name if b.parent else None} for b in arm_d.bones},
        "weights": {"head (mesh 'head')": "head = 1 - jaw; jaw = lower lip + chin + lower mouth bag, fade over cheeks / jaw "
                                          "back / throat; upper lip 0; mouth corners 0.5",
                    "eye.L / eye.R": "rigid, own bones (look-around)", "teeth_upper, fang.L, fang.R": "head",
                    "teeth_lower, tongue": "jaw"},
        "shape_keys": list(keys.keys()),
        "shape_key_note": "head + teeth_lower + tongue carry the keys; jaw_open / viseme_A / viseme_O include the jaw "
                          "rotation (18 / 11 / 7 deg), so use EITHER those keys OR the jaw bone, not both",
        "parts": [o.name for o in bpy.data.objects if o.type == "MESH"],
        "tris": {o.name: int(sum(len(p.vertices) - 2 for p in o.data.polygons)) for o in bpy.data.objects if o.type == "MESH"}}
info["tris_total"] = int(sum(info["tris"].values()))
rep["info"] = info
json.dump(rep, open(os.path.join(OUT, "rig_report.json"), "w"), indent=1)
json.dump(info, open(os.path.join(OUT, "hybrid_head_rig.json"), "w"), indent=1)
print("RIG", json.dumps({k: rep[k] for k in rep if k != "tests"})[:3000])
