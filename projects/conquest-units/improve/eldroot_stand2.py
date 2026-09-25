"""Eldroot standing v2: FULL leg extension (artist 2026-09-25 v2 spec: "the two legs in front should be
fully extended, meaning he should double in height, hes just sitting in the air now" + "we may need model
changes to make the feet wider as well to sell it better"). v1 (rigged/eldroot_standing.blend, a 0.45 m
harmonic lift) is the REJECTED baseline and stays on disk untouched.

    blender --background rigged/eldroot.blend --factory-startup --python eldroot_stand2.py --
            <out.blend> <out.json> [--ratio 2.0] [--geo-only]

Opens the SEATED rig (rigged/eldroot.blend), never saves over it (save_as_mainfile copy=True). Reads the
high-poly master from source-copies/ancient_tree.blend (byte copy) and rebuilds it into game units with
improve_unit.py's exact steps (world transform, eldroot Taubin fix, cell-budget scale) + a measured
translation fit to the approved low.

WHY A MODEL CHANGE (measured, see report "measure"): the sculpted front legs are 1.2 m knee-up columns
whose upper half (z 0.47-1.16) is FUSED to the trunk front along a 2.9 m junction loop (0.35 m2 per leg),
and they stand 1.0 m IN FRONT of the trunk's centre of mass. Unfolded as sculpted they give <1.5x height,
and no deformation of the fused surface can put a vertical column under the body without passing through
it (v1 measured the folds). So:
  A. LEG COLUMN PROFILE: star-ray sampling of the high-poly column (180 azimuths x 6 mm rings + a 30-ring
     hemispherical cap from the knee-cap centre); rays whose hit lands on trunk-owned surface (nearest low
     vertex not leg-dominant) are the fused back -> filled by periodic interpolation + the opposite side's
     high-frequency detail. Result: the sculpted column, closed.
  B. TRUNK: the low's leg faces (+ junction faces + the trunk-weighted inner knee lobes in front of the belly)
     removed; each front hole closed by a constrained-Delaunay membrane (bi-Laplacian lift, boundary 1-ring
     fixed; closure audited: every edge shared once per direction). Trunk + arms move RIGIDLY (stretch 1.000,
     0 flips).
  C. LEGS (new, rigid shells, one bone each): thigh (hip -> knee), shin (knee -> ankle, the sculpted column
     with its mossy knee cap on top), foot (the column's pad, WIDENED + flattened: flat sole, root-toe lobes).
     Leg lengths are SOLVED so that (i) standing height = RATIO x seated height with straight, vertical-plane
     columns under the trunk's centre of mass and (ii) sitting (trunk back on its sculpted base, feet planted)
     keeps the shins vertical in front where the sculpt has them.
  D. LOOK: fresh Smart-UV (improve_unit params) + normal/AO re-bake of the WHOLE standing low from a standing
     high master (trunk high minus legs + membrane high + shell highs); trunk corner colours copied verbatim,
     new faces coloured by improve_unit's eldroot region rules (cavity from the shell highs).
  E. RIG: 14 bones (seated trunk/arm bones moved rigidly + thigh/shin/foot per leg); trunk weights verbatim
     (seated thigh/shin shares -> pelvis), membrane weights from its boundary, shells rigid (weight 1).
  F. CLIPS, 24 fps, in place, every frame keyed: sitting_idle, stand_up, idle, sit_down, walk.
"""
import bpy, bmesh, sys, os, json, math, time, hashlib
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils import geometry as mgeo

T0 = time.time()
argv = sys.argv[sys.argv.index("--") + 1:]
OUT_BLEND, OUT_JSON = argv[0], argv[1]


def opt(name, default, cast=float):
    return cast(argv[argv.index(name) + 1]) if name in argv else default


HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "addon")))
import rigkit as K  # noqa: E402

SRC_HIGH = os.path.join(PRJ, "source-copies", "ancient_tree.blend")
TEX_DIR = os.path.join(PRJ, "improved", "textures")
FPS = K.FPS
TAU = 2 * math.pi
RATIO = opt("--ratio", 2.0)          # standing height / seated height (artist: "double in height")
X_HIP = 0.45                        # hip joints (trunk coords): inside the trunk base, 0.35 above it
Z_HIP = 0.35
Z_ANK = 0.15                        # ankle joint height (inside the 0.30 m foot shell)
LEG_SX, LEG_SY = 1.25, 1.45         # leg thickening vs the sculpted column (x, y): a 5.4 m body on the sculpted
                                    # 0.48 x 0.32 m column read as stilts on the first draft render
LEG_CUT = 0.05                      # trunk faces with ANY seated leg weight > this go with the legs (the crease band)
FOOT_W = 1.42                       # foot pad radius factor at the sole, vs the thickened leg column (artist: "make the feet
                                    # wider"): ~1.8x the sculpted column's width, feet 0.2 m apart at the inner edges
FOOT_COLLAR = 1.1                   # foot top collar radius factor (encloses the shin through the walk tilt)
FOOT_FWD = 0.15                     # sole centre ahead of the ankle (toes forward) -> pads centred under the COM
TOE_A = 0.28                        # root-toe lobe amplitude (5 lobes, one straight ahead)
THIGH_TAPER = (0.85, 1.15)          # thigh radius factor knee -> hip (narrower at the knee: the cap lip shows)
NT_HI, NT_LO = 180, 28              # azimuth samples: high master / low game mesh
DZ_HI = 0.006                       # high ring spacing (m)
report = {"unit": "eldroot", "stage": "standing v2 - full leg extension (model change)", "source": bpy.data.filepath,
          "fps": FPS, "ratio_target": RATIO,
          "rejected_baseline": "rigged/eldroot_standing.blend (v1, 0.45 m harmonic lift, height 3.16 m = 1.17x)"}

scene = bpy.context.scene
scene.render.fps = FPS
scene.render.fps_base = 1.0
low = bpy.data.objects["eldroot"]
old_rig = bpy.data.objects["eldroot_rig"]
report["seated_actions_removed"] = [a.name for a in bpy.data.actions]
for a in list(bpy.data.actions):
    bpy.data.actions.remove(a)
assert np.abs(np.array(low.matrix_world) - np.eye(4)).max() < 1e-9
me0 = low.data
n0 = len(me0.vertices)


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


W0 = np.empty(n0 * 3); me0.vertices.foreach_get("co", W0); W0 = W0.reshape(-1, 3)
F0 = np.empty(len(me0.polygons) * 3, dtype=np.int64); me0.polygons.foreach_get("vertices", F0); F0 = F0.reshape(-1, 3)
H_SEAT = float(W0[:, 2].max() - W0[:, 2].min())
old_names = [g.name for g in low.vertex_groups]
Wold = np.zeros((n0, len(old_names)))
for v in me0.vertices:
    for g in v.groups:
        Wold[v.index, g.group] = g.weight
col_ = {nm: i for i, nm in enumerate(old_names)}
legw = {s: Wold[:, col_["thigh." + s]] + Wold[:, col_["shin." + s]] for s in "LR"}
leg_any = legw["L"] + legw["R"]
legdom = leg_any >= 0.5
report["seated"] = {"verts": n0, "tris": len(F0), "height_m": round(H_SEAT, 4), "coords_sha": sha(W0),
                    "bbox": [W0.min(0).round(4).tolist(), W0.max(0).round(4).tolist()]}
old_bones = {b.name: (np.array(b.head_local), np.array(b.tail_local), b.parent.name if b.parent else None)
             for b in old_rig.data.bones}
report["seated_leg_bone_chain_m"] = {s: {"thigh": round(float(np.linalg.norm(old_bones["thigh." + s][1] - old_bones["thigh." + s][0])), 4),
                                         "shin": round(float(np.linalg.norm(old_bones["shin." + s][1] - old_bones["shin." + s][0])), 4)}
                                     for s in "LR"}

# ====================================================================== 1. high-poly master (improve_unit steps)
t1 = time.time()
with bpy.data.libraries.load(SRC_HIGH) as (src_, dst_):
    dst_.objects = list(src_.objects)
tmpc = bpy.data.collections.new("hi_src"); scene.collection.children.link(tmpc)
for o in dst_.objects:
    if o is not None:
        tmpc.objects.link(o)
bpy.context.view_layer.update()
srco = max([o for o in dst_.objects if o is not None and o.type == "MESH"], key=lambda o: len(o.data.vertices))
me_hi = srco.data.copy(); me_hi.name = "eldroot2_high_src"
me_hi.transform(srco.matrix_world)
hi_smooth_frac = float(np.mean([p.use_smooth for p in me_hi.polygons]))
for o in list(tmpc.objects):
    bpy.data.objects.remove(o, do_unlink=True)
bpy.data.collections.remove(tmpc)


def read_mesh(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co)
    ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev)
    return co.reshape(-1, 3), ev.reshape(-1, 2)


def neighbour_mean(W, ev, deg):
    s = np.zeros_like(W)
    for k in range(W.shape[1]):
        s[:, k] = np.bincount(ev[:, 0], W[ev[:, 1], k], minlength=len(W)) + np.bincount(ev[:, 1], W[ev[:, 0], k], minlength=len(W))
    return s / np.maximum(deg, 1)[:, None]


def taubin(W, ev, w, iters, lam=0.5, mu=-0.53):
    deg = np.bincount(ev.ravel(), minlength=len(W)).astype(float)
    W = W.copy(); ww = w[:, None]
    for _ in range(iters):
        W += lam * ww * (neighbour_mean(W, ev, deg) - W)
        W += mu * ww * (neighbour_mean(W, ev, deg) - W)
    return W


WH, EVH = read_mesh(me_hi)
lo_, hi_ = WH.min(0), WH.max(0)
hh = (WH[:, 2] - lo_[2]) / (hi_[2] - lo_[2])
nv_ = np.empty(len(me_hi.vertices) * 3); me_hi.vertex_normals.foreach_get("vector", nv_); NRMH = nv_.reshape(-1, 3)
w_side = smoothstep(0.50, 0.58, hh) * smoothstep(0.30, 0.60, np.abs(NRMH[:, 0]))
WH = taubin(WH, EVH, w_side, 60)                                   # improve_unit step 2 (eldroot)
lo_, hi_ = WH.min(0), WH.max(0); size = hi_ - lo_
kf = min(3.4 / size[2], 3.8 / max(size[0], size[1]))              # improve_unit step 3 (cell budget)
WH = (WH - np.array([(lo_[0] + hi_[0]) / 2, (lo_[1] + hi_[1]) / 2, lo_[2]])) * kf
me_hi.calc_loop_triangles()
FH = np.empty(len(me_hi.loop_triangles) * 3, dtype=np.int64); me_hi.loop_triangles.foreach_get("vertices", FH); FH = FH.reshape(-1, 3)
# translation fit high -> approved low (improve_unit's feet shift came from the decimated low's bbox)
tfit = np.zeros(3)
sub = W0[:: max(1, n0 // 4000)]
bvh0 = BVHTree.FromPolygons(WH.tolist(), FH.tolist())
for it in range(4):
    res = np.array([np.array(p - tfit) - np.array(bvh0.find_nearest(Vector(p - tfit))[0]) for p in sub])
    tfit += res.mean(0)
res = np.array([np.linalg.norm(np.array(p - tfit) - np.array(bvh0.find_nearest(Vector(p - tfit))[0])) for p in sub])
WH = WH + tfit
bvhH = BVHTree.FromPolygons(WH.tolist(), FH.tolist())
report["high_master"] = {"source": SRC_HIGH, "tris": int(len(FH)), "verts": int(len(WH)),
                         "scale_factor": round(float(kf), 6), "scale_factor_improve_report": 0.056205,
                         "translation_fit_m": tfit.round(5).tolist(),
                         "low_to_high_residual_mm": {"mean": round(float(res.mean()) * 1000, 2), "p95": round(float(np.percentile(res, 95)) * 1000, 2),
                                                     "max": round(float(res.max()) * 1000, 2)},
                         "smooth_face_fraction": round(hi_smooth_frac, 3), "seconds": round(time.time() - t1, 1)}
# cavity field normalisation of the approved look (improve_unit: CAV / p95|CAV|)
degH = np.bincount(EVH.ravel(), minlength=len(WH)).astype(float)
me_hi.vertices.foreach_set("co", WH.ravel()); me_hi.update()
nv_ = np.empty(len(me_hi.vertices) * 3); me_hi.vertex_normals.foreach_get("vector", nv_); NRMH = nv_.reshape(-1, 3)
elH = np.linalg.norm(WH[EVH[:, 0]] - WH[EVH[:, 1]], axis=1).mean()
CAVH = ((neighbour_mean(WH, EVH, degH) - WH) * NRMH).sum(1) / elH
for _ in range(6):
    CAVH = neighbour_mean(CAVH[:, None], EVH, degH)[:, 0] * 0.5 + CAVH * 0.5
CAV_SCALE = float(np.percentile(np.abs(CAVH), 95) + 1e-9)

# ====================================================================== 2. MEASURE the sculpted legs
kd0 = KDTree(n0)
for i, p in enumerate(W0):
    kd0.insert(p, i)
kd0.balance()
measure = {}
AXIS = {}
for s in "LR":
    m = legw[s] >= 0.5
    P = W0[m]
    # centroid polyline of the leg column (5 cm slabs) -> arc length; free vs fused span from the junction loop
    zs = np.arange(0.0, P[:, 2].max() + 1e-9, 0.10)
    cents = [P[(P[:, 2] >= z) & (P[:, 2] < z + 0.10)].mean(0) for z in zs if ((P[:, 2] >= z) & (P[:, 2] < z + 0.10)).any()]
    cents = np.array(cents)
    cents[1:-1] = (cents[:-2] + 2 * cents[1:-1] + cents[2:]) / 4.0     # slab centroids jitter where the column fuses
    arc = float(np.linalg.norm(np.diff(cents, axis=0), axis=1).sum())
    free_rings = P[(P[:, 2] > 0.10) & (P[:, 2] < 0.45)]
    AXIS[s] = np.array([np.median(free_rings[:, 0]), np.median(free_rings[:, 1])])
    measure[s] = {"column_top_z_m": round(float(P[:, 2].max()), 4), "centroid_polyline_arc_m": round(arc, 4),
                  "leg_verts": int(m.sum())}
# junction loops (seated): boundary of the leg-dominant face set
from collections import defaultdict
for s in "LR":
    fm = (legw[s] >= 0.5)[F0].all(1)
    ec = defaultdict(int)
    for f in F0[fm]:
        for k in range(3):
            a_, b_ = sorted((int(f[k]), int(f[(k + 1) % 3]))); ec[(a_, b_)] += 1
    bd = np.array([e for e, c in ec.items() if c == 1])
    bv = np.unique(bd)
    measure[s].update({"junction_loop_verts": int(len(bv)), "junction_loop_length_m": round(float(np.linalg.norm(W0[bd[:, 0]] - W0[bd[:, 1]], axis=1).sum()), 3),
                       "fused_z_range_m": [round(float(W0[bv, 2].min()), 3), round(float(W0[bv, 2].max()), 3)],
                       "free_column_below_m": round(float(W0[bv, 2].min()), 3)})
measure["unfolded_as_sculpted"] = {
    "bone_chain_m": round(float(np.mean([sum(v.values()) for v in report["seated_leg_bone_chain_m"].values()])), 4),
    "geometry_arc_m": round(float(np.mean([measure[s]["centroid_polyline_arc_m"] for s in "LR"])), 4),
    "note": "the column is a knee-up shin; its thigh is not sculpted (fused into the trunk front). Standing on the "
            "sculpted column alone lifts the trunk base by at most its arc length"}
arc_mean = measure["unfolded_as_sculpted"]["geometry_arc_m"]
measure["unfolded_as_sculpted"]["max_height_m"] = round(H_SEAT + arc_mean, 3)
measure["unfolded_as_sculpted"]["max_ratio"] = round((H_SEAT + arc_mean) / H_SEAT, 3)
report["measure"] = measure

# ====================================================================== 3. column profile (star rays on the high)
Z0S, ZC = 0.04, 0.95
ZTOP = float(np.mean([measure[s]["column_top_z_m"] for s in "LR"]))
ZS = np.arange(Z0S, ZC + 1e-9, DZ_HI)
TH = np.arange(NT_HI) * TAU / NT_HI
PHI = np.radians(np.linspace(0.0, 88.0, 30))
COL = {}


def fill_rings(R, V):
    """Per ring: periodic interpolation across invalid azimuths + the opposite side's detail."""
    R = R.copy(); N = R.shape[1]
    th = np.arange(N)
    filled = ~V
    good = V.sum(1) >= 0.3 * N
    for i in range(R.shape[0]):
        if not good[i]:
            continue
        idx = np.nonzero(V[i])[0]
        if len(idx) == N:
            continue
        xp = np.concatenate([idx - N, idx, idx + N]); fp = np.tile(R[i, idx], 3)
        sm = np.interp(th, xp, fp)
        base = np.where(V[i], R[i], sm)
        k = 15
        pad = np.concatenate([base[-k:], base, base[:k]])
        avg = np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]
        det = base - avg
        opp = (th + N // 2) % N
        R[i] = np.where(V[i], R[i], sm + np.where(V[i, opp], det[opp], 0.0))
    for i in np.nonzero(~good)[0]:                    # whole ring unusable -> nearest usable rings
        gi = np.nonzero(good)[0]
        j = gi[np.argmin(np.abs(gi - i))]
        R[i] = R[j]; filled[i] = True
    for _ in range(2):                                # blend filled cells along the column
        Rs = R.copy()
        Rs[1:-1] = 0.25 * R[:-2] + 0.5 * R[1:-1] + 0.25 * R[2:]
        R = np.where(filled, Rs, R)
    return R, filled


for s in "LR":
    ax, ay = AXIS[s]
    own = legw[s] >= 0.5

    def ray(o, d):
        hit = bvhH.ray_cast(Vector(o), Vector(d), 0.9)
        if hit[0] is None:
            return 0.0, False
        p = hit[0]
        _, idx, _ = kd0.find(p)
        return float(hit[3]), bool(own[idx])
    RSd = np.zeros((len(ZS), NT_HI)); VS = np.zeros_like(RSd, dtype=bool)
    for i, z in enumerate(ZS):
        for j, t in enumerate(TH):
            RSd[i, j], VS[i, j] = ray((ax, ay, z), (math.cos(t), math.sin(t), 0.0))
    RCd = np.zeros((len(PHI), NT_HI)); VC = np.zeros_like(RCd, dtype=bool)
    for i, ph in enumerate(PHI):
        for j, t in enumerate(TH):
            RCd[i, j], VC[i, j] = ray((ax, ay, ZC), (math.cos(ph) * math.cos(t), math.cos(ph) * math.sin(t), math.sin(ph)))
    ref = np.nanmedian(np.where(VS[(ZS > 0.1) & (ZS < 0.45)], RSd[(ZS > 0.1) & (ZS < 0.45)], np.nan), axis=0)
    VS &= (RSd <= 1.35 * np.nan_to_num(ref, nan=0.3)[None, :]) & (RSd > 0.02)
    VC &= (RCd < 0.5) & (RCd > 0.01)
    RS, fS = fill_rings(RSd, VS)
    RCf, fC = fill_rings(RCd, VC)
    RCf[0] = RS[-1]                                   # phi = 0 ring IS the side ring at ZC (same rays)
    COL[s] = {"RS": RS, "RC": RCf, "axis": (ax, ay)}
    report.setdefault("column_profile", {})[s] = {
        "axis_xy": [round(ax, 4), round(ay, 4)], "rings_side": len(ZS), "rings_cap": len(PHI), "azimuths": NT_HI,
        "fused_or_missed_filled_pct_side": round(100 * float(fS.mean()), 1),
        "fused_filled_pct_above_z0.47": round(100 * float(fS[ZS > 0.47].mean()), 1),
        "fused_filled_pct_below_z0.45": round(100 * float(fS[ZS < 0.45].mean()), 1),
        "cap_filled_pct": round(100 * float(fC.mean()), 1),
        "pad_half_extent_m": {"x": round(float(max(RS[0] * np.abs(np.cos(TH)))), 4), "y": round(float(max(RS[0] * np.abs(np.sin(TH)))), 4)},
        "radius_median_m": round(float(np.median(RS)), 4)}


def r_side(s, z, lowpass=False):
    RS = COL[s]["RS_lp"] if lowpass else COL[s]["RS"]
    z = min(max(z, ZS[0]), ZS[-1])
    f = (z - ZS[0]) / DZ_HI
    i = min(int(f), len(ZS) - 2); w = f - i
    return RS[i] * (1 - w) + RS[i + 1] * w


def r_cap(s, k, lowpass=False):
    return (COL[s]["RC_lp"] if lowpass else COL[s]["RC"])[k]


def lp_theta(R, k):
    pad = np.concatenate([R[:, -k:], R, R[:, :k]], axis=1)
    ker = np.ones(2 * k + 1) / (2 * k + 1)
    return np.array([np.convolve(r, ker, mode="same")[k:-k] for r in pad])


for s in "LR":
    RS_lp = lp_theta(COL[s]["RS"], 3)
    ker = np.ones(7) / 7
    RS_lp = np.array([np.convolve(np.pad(RS_lp[:, j], 3, mode="edge"), ker, mode="valid") for j in range(NT_HI)]).T
    COL[s]["RS_lp"] = RS_lp
    COL[s]["RC_lp"] = lp_theta(COL[s]["RC"], 3)


def at_theta(r_hi, nt):
    """High ring (NT_HI azimuths) -> nt azimuths (bin mean, bins centred on the output azimuths)."""
    if nt == NT_HI:
        return r_hi
    step = NT_HI / nt
    out = np.empty(nt)
    for j in range(nt):
        c = j * step
        idx = (np.arange(int(round(c - step / 2)), int(round(c + step / 2))) % NT_HI)
        out[j] = r_hi[idx].mean()
    return out


# ====================================================================== 4. trunk: remove legs, close the holes
bm = bmesh.new(); bm.from_mesh(me0)
oi = bm.verts.layers.int.new("oi")
for v in bm.verts:
    v[oi] = v.index
bm.verts.ensure_lookup_table()
legcut = leg_any > LEG_CUT
# knee lobes: the sculpt's leg anatomy continues past the weighted column - an inner knee lobe (z 1.0-1.3,
# x 0.2-0.45) protrudes to y -0.90 and is weighted to the TRUNK (ray slices of the seated low, lane report).
# Left on, it reads as a pair of horns on the standing chest (first v2 render). Rule: trunk faces entirely in
# front of the trunk's front surface (y < LOBE_Y; the belly/chin front measures -0.42..-0.52 at |x| < 0.45)
# below the chin (z < LOBE_Z) belong to the legs.
LOBE_Y, LOBE_Z = -0.58, 1.45
lobe = (W0[:, 1] < LOBE_Y) & (W0[:, 2] < LOBE_Z) & (np.abs(W0[:, 0]) < 0.95)
legcut_all = legcut | lobe
kill = [f for f in bm.faces if any(legcut[v.index] for v in f.verts) or all(lobe[v.index] for v in f.verts)]
report["trunk_edit"] = {"leg_cut_weight": LEG_CUT,
                        "faces_removed_leg": int(sum(1 for f in kill if all(legdom[v.index] for v in f.verts))),
                        "faces_removed_junction_band": int(sum(1 for f in kill if any(legcut[v.index] for v in f.verts)
                                                               and not all(legdom[v.index] for v in f.verts))),
                        "faces_removed_knee_lobe": int(sum(1 for f in kill if not any(legcut[v.index] for v in f.verts))),
                        "knee_lobe_rule": "trunk faces with every vertex at y < %.2f, z < %.2f, |x| < 0.95 (seated coords)" % (LOBE_Y, LOBE_Z)}
bmesh.ops.delete(bm, geom=kill, context="FACES_ONLY")
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
# drop slivers the cut isolated (face islands < 2% of the trunk) and any non-manifold fans they leave
isl = []
seen_f = set()
for f0 in bm.faces:
    if f0.index in seen_f:
        continue
    comp, st = [], [f0]; seen_f.add(f0.index)
    while st:
        f = st.pop(); comp.append(f)
        for e in f.edges:
            for g in e.link_faces:
                if g.index not in seen_f:
                    seen_f.add(g.index); st.append(g)
    isl.append(comp)
small = [f for comp in isl if len(comp) < 0.02 * len(bm.faces) for f in comp]
report["trunk_edit"]["islands_after_cut"] = [len(c) for c in isl]
report["trunk_edit"]["sliver_faces_dropped"] = len(small)
if small:
    bmesh.ops.delete(bm, geom=small, context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
def unpinch():
    for _ in range(4):                                  # faces hanging by one edge into the hole (boundary pinches)
        bm.verts.ensure_lookup_table()
        pinch = [v for v in bm.verts if sum(1 for e in v.link_edges if len(e.link_faces) == 1) > 2]
        if not pinch:
            break
        drop = {f for v in pinch for f in v.link_faces if sum(1 for e in f.edges if len(e.link_faces) == 1) >= 1}
        report["trunk_edit"]["pinch_faces_dropped"] = report["trunk_edit"].get("pinch_faces_dropped", 0) + len(drop)
        bmesh.ops.delete(bm, geom=list(drop), context="FACES_ONLY")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")


def bm_hole_loops():
    nb_ = defaultdict(list)
    for e in bm.edges:
        if len(e.link_faces) == 1:
            a_, b_ = e.verts
            nb_[a_].append(b_); nb_[b_].append(a_)
    out, seen_ = [], set()
    for st in list(nb_):
        if st in seen_:
            continue
        lp, prev, cur = [st], None, st; seen_.add(st)
        while True:
            nx = [x for x in nb_[cur] if x is not prev and x not in seen_]
            if not nx:
                break
            prev, cur = cur, nx[0]; lp.append(cur); seen_.add(cur)
        out.append(lp)
    return out


def projects_simply(P3):
    """Does the loop project to a SIMPLE polygon on the best-fit plane or a blend toward the front (-Y) view?"""
    c = P3.mean(0)
    n_fit = np.linalg.svd(P3 - c)[2][2]
    if n_fit[1] > 0:
        n_fit = -n_fit
    m = len(P3)
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        nrm = (1 - w) * n_fit + w * np.array([0.0, -1.0, 0.0]); nrm /= np.linalg.norm(nrm)
        a = np.array([0.0, 0.0, 1.0]) if abs(nrm[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        e1 = a - nrm * (a @ nrm); e1 /= np.linalg.norm(e1); e2 = np.cross(nrm, e1)
        Q = np.stack([(P3 - c) @ e1, (P3 - c) @ e2], 1)
        ok = True
        for i in range(m):
            p1, p2 = Q[i], Q[(i + 1) % m]
            for j in range(i + 2, m):
                if (j + 1) % m == i:
                    continue
                q1, q2 = Q[j], Q[(j + 1) % m]
                d1 = np.cross(p2 - p1, q1 - p1); d2 = np.cross(p2 - p1, q2 - p1)
                d3 = np.cross(q2 - q1, p1 - q1); d4 = np.cross(q2 - q1, p2 - q1)
                if d1 * d2 < 0 and d3 * d4 < 0:
                    ok = False; break
            if not ok:
                break
        if ok:
            return True
    return False


unpinch()
# a folded hole boundary (no simple projection) fills as a fin; grow that hole ring by ring until it projects simply
grown = []
for _ in range(6):
    bad = [lp for lp in bm_hole_loops() if not projects_simply(np.array([v.co[:] for v in lp]))]
    if not bad:
        break
    for lp in bad:
        grown.append([round(float(np.mean([v.co.x for v in lp])), 3), len(lp)])
    drop = {f for lp in bad for v in lp for f in v.link_faces}
    bmesh.ops.delete(bm, geom=list(drop), context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    unpinch()
report["trunk_edit"]["hole_rings_grown_to_simple_projection"] = grown
bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
for i, v in enumerate(bm.verts):
    v.index = i
col_l = bm.loops.layers.float_color.get("Col"); glow_l = bm.loops.layers.float_color.get("Glow")
TV = np.array([v.co[:] for v in bm.verts])
TOI = np.array([v[oi] for v in bm.verts])
TF = np.array([[v.index for v in f.verts] for f in bm.faces])
TCOL = np.array([[l[col_l][:] for l in f.loops] for f in bm.faces])
TGLOW = np.array([[l[glow_l][:] for l in f.loops] for f in bm.faces])
bedges = [e for e in bm.edges if len(e.link_faces) == 1]
adj = defaultdict(list)
for e in bedges:
    a_, b_ = e.verts[0].index, e.verts[1].index
    adj[a_].append(b_); adj[b_].append(a_)
report["trunk_edit"]["hole_boundary_max_degree"] = int(max(len(v) for v in adj.values()))
loops_, seen = [], set()
for st in sorted(adj):
    if st in seen:
        continue
    lp = [st]; seen.add(st); prev = None; cur = st
    while True:
        nx = [x for x in adj[cur] if x != prev and x not in seen]
        if not nx:
            break
        prev, cur = cur, nx[0]; lp.append(cur); seen.add(cur)
    loops_.append(lp)
# direction the trunk faces traverse each boundary edge (the membrane must run the other way)
edge_dir = {}
for e in bedges:
    f = e.link_faces[0]
    vs = [v.index for v in f.verts]
    a_, b_ = e.verts[0].index, e.verts[1].index
    i_ = vs.index(a_)
    edge_dir[(a_, b_)] = vs[(i_ + 1) % len(vs)] == b_
    edge_dir[(b_, a_)] = not edge_dir[(a_, b_)]
bm.free()
report["trunk_edit"]["holes"] = [{"verts": len(lp), "centroid": TV[lp].mean(0).round(3).tolist()} for lp in loops_]
assert len(loops_) == 2, "expected one front hole per leg, got %d" % len(loops_)


def cdt_patch(P3, spacing, plane=None, Q2=None):
    """Constrained-Delaunay membrane over a closed 3D loop P3 (ordered). Returns (V3, tris, nb, new_pts,
    plane, V2) with the first nb vertices = the loop. Interior lifted harmonically (uniform Laplacian,
    boundary fixed); V2 = the plane coordinates (same order). Q2: explicit 2D loop coordinates (a disk
    parameterisation) instead of a planar projection."""
    if plane is None:
        c = P3.mean(0)
        U, Sv, Vt = np.linalg.svd(P3 - c)
        e1, e2 = Vt[0], Vt[1]
    else:
        c, e1, e2 = plane
    Q = np.stack([(P3 - c) @ e1, (P3 - c) @ e2], 1) if Q2 is None else np.asarray(Q2, float)
    nb = len(Q)
    path = [(Q[i], Q[(i + 1) % nb]) for i in range(nb)]

    def inside(pt):
        x, y = pt; ins = False
        for (a, b) in path:
            if (a[1] > y) != (b[1] > y):
                xi = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if xi > x:
                    ins = not ins
        return ins

    def segd(pt):
        best = 1e9
        for (a, b) in path:
            ab = b - a; t = np.clip(((pt - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
            best = min(best, np.linalg.norm(pt - (a + t * ab)))
        return best
    lo2, hi2 = Q.min(0), Q.max(0)
    xs = np.arange(lo2[0] + spacing / 2, hi2[0], spacing); ys = np.arange(lo2[1] + spacing / 2, hi2[1], spacing)
    inner = []
    for j, y in enumerate(ys):
        for x in xs:
            pt = np.array([x + (spacing / 2 if j % 2 else 0.0), y])
            if inside(pt) and segd(pt) > 0.55 * spacing:
                inner.append(pt)
    inner = np.array(inner) if inner else np.zeros((0, 2))
    allq = [Vector(q) for q in Q] + [Vector(q) for q in inner]
    out = mgeo.delaunay_2d_cdt(allq, [], [list(range(nb))], 1, 1e-9, True)
    ov, oe, of_, orig_v = out[0], out[1], out[2], out[3]
    new_pts = len(ov) - len(allq)
    tris = []
    for f in of_:
        for k in range(1, len(f) - 1):
            tris.append([f[0], f[k], f[k + 1]])
    tris = np.array(tris)
    # map output verts -> input order when possible
    mp = np.array([ov_[0] if ov_ else -1 for ov_ in orig_v])
    V2 = np.array([v[:] for v in ov])
    n_all = len(V2)
    X = np.zeros((n_all, 3))
    fixed = np.zeros(n_all, bool)
    for i in range(n_all):
        if 0 <= mp[i] < nb:
            X[i] = P3[mp[i]]; fixed[i] = True
        else:
            X[i] = c + V2[i, 0] * e1 + V2[i, 1] * e2
    # uniform Laplacian lift
    nbrs = defaultdict(set)
    for t in tris:
        for k in range(3):
            nbrs[t[k]].add(t[(k + 1) % 3]); nbrs[t[k]].add(t[(k + 2) % 3])
    free = np.nonzero(~fixed)[0]
    fidx = -np.ones(n_all, dtype=np.int64); fidx[free] = np.arange(len(free))
    if len(free):
        A = np.zeros((len(free), len(free))); B = np.zeros((len(free), 3))
        for i in free:
            r = fidx[i]; A[r, r] = len(nbrs[i])
            for j in nbrs[i]:
                if fixed[j]:
                    B[r] += X[j]
                else:
                    A[r, fidx[j]] -= 1.0
        X[free] = np.linalg.solve(A, B)
    # reorder: loop verts first (in loop order), then interior
    order = np.concatenate([np.array([int(np.nonzero(mp == i)[0][0]) for i in range(nb)]), free])
    inv = np.empty(n_all, dtype=np.int64); inv[order] = np.arange(n_all)
    return X[order], inv[tris], nb, new_pts, (c, e1, e2), V2[order]


ntv_ = len(TV)


def lift_bilap(Vp, Tp, nb, lp):
    """Least-squares bi-Laplacian lift: the interior minimises sum ||L x||^2 over interior + loop rows with the
    loop verts' trunk 1-ring fixed -> the membrane continues the trunk's surface tangentially."""
    ni = len(Vp) - nb
    gid = np.concatenate([np.array(lp), ntv_ + np.arange(ni)])
    nbrs = defaultdict(set)
    for t in Tp:
        g = gid[t]
        for k in range(3):
            nbrs[int(g[k])].add(int(g[(k + 1) % 3])); nbrs[int(g[k])].add(int(g[(k + 2) % 3]))
    for f in TF[np.isin(TF, lp).any(1)]:
        for k in range(3):
            nbrs[int(f[k])].add(int(f[(k + 1) % 3])); nbrs[int(f[k])].add(int(f[(k + 2) % 3]))
    Xall = {int(g): (TV[g] if g < ntv_ else Vp[nb + g - ntv_]) for g in nbrs}
    rows = [int(g) for g in gid]
    A = np.zeros((len(rows), ni)); B = np.zeros((len(rows), 3))
    for r, g in enumerate(rows):
        terms = [(g, float(len(nbrs[g])))] + [(j, -1.0) for j in nbrs[g]]
        for j, w in terms:
            if j >= ntv_:
                A[r, j - ntv_] += w
            else:
                B[r] -= w * TV[j]
    X, *_ = np.linalg.lstsq(A, B, rcond=None)
    out = Vp.copy(); out[nb:] = X
    return out


def bary_map(Q2, V2, T, X3):
    """2D points Q2 -> 3D on the piecewise-linear membrane (V2 plane coords, T tris, X3 lifted coords)."""
    a, b, c = V2[T[:, 0]], V2[T[:, 1]], V2[T[:, 2]]
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    fn = np.cross(X3[T[:, 1]] - X3[T[:, 0]], X3[T[:, 2]] - X3[T[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
    P = np.zeros((len(Q2), 3)); N = np.zeros((len(Q2), 3))
    for i, q in enumerate(Q2):
        l1 = ((b[:, 1] - c[:, 1]) * (q[0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (q[1] - c[:, 1])) / den
        l2 = ((c[:, 1] - a[:, 1]) * (q[0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (q[1] - c[:, 1])) / den
        l3 = 1 - l1 - l2
        k = int(np.argmax(np.minimum(np.minimum(l1, l2), l3)))
        P[i] = l1[k] * X3[T[k, 0]] + l2[k] * X3[T[k, 1]] + l3[k] * X3[T[k, 2]]
        N[i] = fn[k]
    return P, N


def bark_noise(P):
    """Deterministic bark-like relief (vertical furrows), metres."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    return (0.6 * np.sin(38.0 * x + 2.5 * np.sin(7.0 * z) + 11.0 * y) + 0.3 * np.sin(71.0 * x - 23.0 * y + 5.0 * z)
            + 0.25 * np.sin(17.0 * z + 29.0 * x)) * 0.004


def plane_of(P3, nrm):
    nrm = nrm / np.linalg.norm(nrm)
    a = np.array([0.0, 0.0, 1.0]) if abs(nrm[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = a - nrm * (a @ nrm); e1 /= np.linalg.norm(e1)
    return (P3.mean(0), e1, np.cross(nrm, e1))


def best_patch(P3, spacing):
    """The loop must project WITHOUT self-intersection (a CDT intersection point splits a boundary edge and the
    membrane no longer shares the trunk's edges). Candidates: the best-fit plane, then blends toward the front
    (-Y) projection; the first with zero intersection points wins."""
    c = P3.mean(0)
    n_fit = np.linalg.svd(P3 - c)[2][2]
    if n_fit[1] > 0:
        n_fit = -n_fit
    tried = []
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        nrm = (1 - w) * n_fit + w * np.array([0.0, -1.0, 0.0])
        pl = plane_of(P3, nrm)
        res = cdt_patch(P3, spacing, pl)
        tried.append((w, res[3]))
        if res[3] == 0:
            return res, tried, None
    # no planar projection is simple: arc-length disk parameterisation (a circle of the loop's perimeter - always
    # a simple polygon; the bi-Laplacian lift places the interior from connectivity alone)
    seg = np.linalg.norm(np.roll(P3, -1, 0) - P3, axis=1)
    per = float(seg.sum())
    th = TAU * np.concatenate([[0.0], np.cumsum(seg)[:-1]]) / per
    Qc = (per / TAU) * np.stack([np.cos(th), np.sin(th)], 1)
    pl = plane_of(P3, n_fit)
    Qp = np.stack([(P3 - pl[0]) @ pl[1], (P3 - pl[0]) @ pl[2]], 1)
    a_proj = 0.5 * abs(float(np.sum(Qp[:, 0] * np.roll(Qp[:, 1], -1) - np.roll(Qp[:, 0], -1) * Qp[:, 1])))
    a_disk = per * per / (2 * TAU)
    dscale = math.sqrt(a_disk / max(a_proj, 1e-9))
    res = cdt_patch(P3, spacing * dscale, pl, Q2=Qc)   # same interior density as a planar fill
    tried.append(("disk", res[3]))
    return res, tried, (Qc, dscale)


patches = []
for lp in loops_:
    P3 = TV[lp]
    (Vp, Tp, nb, newp, plane, V2p), tried, Qdisk = best_patch(P3, 0.035)
    report["trunk_edit"].setdefault("membrane_projection_tries", []).append([[w if isinstance(w, str) else round(w, 2), k] for w, k in tried])
    Vp = lift_bilap(Vp, Tp, nb, lp)
    # winding: the loop edge (lp[0] -> lp[1]) must run opposite to the trunk face
    a_, b_ = 0, 1
    t_has = None
    for t in Tp:
        for k in range(3):
            if t[k] == a_ and t[(k + 1) % 3] == b_:
                t_has = True
            if t[k] == b_ and t[(k + 1) % 3] == a_:
                t_has = False
    trunk_ab = edge_dir[(lp[0], lp[1])]
    if t_has is not None and t_has == trunk_ab:
        Tp = Tp[:, ::-1]
    Pd = np.concatenate([np.linspace(P3[i], P3[(i + 1) % len(P3)], 4, endpoint=False) for i in range(len(P3))])
    Qd, dsc = None, 1.0
    if Qdisk is not None:
        Qc_, dsc = Qdisk
        Qd = np.concatenate([np.linspace(Qc_[i], Qc_[(i + 1) % len(Qc_)], 4, endpoint=False) for i in range(len(Qc_))])
    _, Th, nbh, newh, _, V2h = cdt_patch(Pd, 0.012 * dsc, plane, Q2=Qd)
    Vh, Nh = bary_map(V2h, V2p, Tp, Vp)                   # the high membrane lies ON the low membrane ...
    dB = np.array([np.min(np.linalg.norm(V2p[:nb] - q, axis=1)) for q in V2h])
    Vh = Vh + Nh * (bark_noise(Vh) * smoothstep(0.0, 0.05, dB))[:, None]     # ... plus bark relief for the bake
    patches.append({"loop": lp, "V": Vp, "T": Tp, "nb": nb, "Vh": Vh, "Th": Th, "cdt_new_points": newp + newh,
                    "side": "L" if P3[:, 0].mean() > 0 else "R"})
report["trunk_edit"]["membranes"] = [{"side": p["side"], "low_verts_added": int(len(p["V"]) - p["nb"]), "low_tris": int(len(p["T"])),
                                      "high_tris": int(len(p["Th"])), "cdt_intersection_points": int(p["cdt_new_points"])}
                                     for p in patches]
report["trunk_edit"]["membrane_rule"] = ("constrained Delaunay in the loop's best-fit plane (3.5 cm spacing; blends toward the "
                                         "front projection, then an arc-length disk parameterisation, until the loop is simple), "
                                         "least-squares bi-Laplacian lift with the trunk 1-ring fixed (tangent-continuous); high = "
                                         "the same surface at 1.2 cm + 4 mm deterministic bark relief")

# centre of mass of the closed trunk (seated coords): divergence theorem over trunk + membranes
CV = [TV]; CF = [TF]; off_ = len(TV)
for p in patches:
    ids = np.concatenate([np.array(p["loop"]), off_ + np.arange(len(p["V"]) - p["nb"])])
    CV.append(p["V"][p["nb"]:]); CF.append(ids[p["T"]]); off_ += len(p["V"]) - p["nb"]
CVa = np.vstack(CV); CFa = np.vstack(CF)
a_, b_, c_ = CVa[CFa[:, 0]], CVa[CFa[:, 1]], CVa[CFa[:, 2]]
vol_t = np.einsum("ij,ij->i", a_, np.cross(b_, c_)) / 6.0
VOL = float(vol_t.sum())
COM = ((vol_t[:, None] * (a_ + b_ + c_) / 4.0).sum(0) / VOL)
report["trunk_mass"] = {"closed_volume_m3": round(VOL, 4), "com_trunk_coords": COM.round(4).tolist(),
                        "rule": "uniform density; divergence theorem over trunk + arms + membranes"}

# ====================================================================== 5. solve the legs
Y_ANK_SEAT = float(np.mean([AXIS[s][1] for s in "LR"]))
X_ANK = float(np.mean([abs(AXIS[s][0]) for s in "LR"]))
Y_HIP = float(COM[1] + FOOT_FWD)                       # pads (sole centre = ankle - FOOT_FWD) centred under the COM
Z_B = (RATIO - 1.0) * H_SEAT                           # trunk base rise
DXS = X_ANK - X_HIP
S_TOT = math.sqrt((Z_B + Z_HIP - Z_ANK) ** 2 + DXS ** 2)
cc = Z_ANK - Z_HIP
DYS = Y_ANK_SEAT - Y_HIP
L2 = (S_TOT ** 2 - DXS ** 2 - DYS ** 2 - cc ** 2) / (2 * (S_TOT + cc))
L1 = S_TOT - L2
J_OFF = 0.5 * (ZTOP - ZC)                              # knee joint sits mid-cap
H_B = L2 - J_OFF                                       # shin band end = cap centre
DY_SIT = Y_HIP - Y_ANK_SEAT                            # seated trunk displacement (rest frame): back by this, down by Z_B
report["leg_solve"] = {"hip_trunk_coords": [X_HIP, round(Y_HIP, 4), Z_HIP], "ankle_x": round(X_ANK, 4), "ankle_z": Z_ANK,
                       "seated_ankle_y_trunk_coords": round(Y_ANK_SEAT, 4), "trunk_rise_m": round(Z_B, 4),
                       "thigh_L1_m": round(L1, 4), "shin_L2_m": round(L2, 4), "leg_total_m": round(S_TOT, 4),
                       "seated_knee_joint_z_m": round(Z_ANK + L2, 4), "sculpt_knee_cap_top_z_m": round(ZTOP, 4),
                       "seated_knee_cap_top_z_m": round(Z_ANK + L2 + (ZTOP - ZC) - J_OFF, 4),
                       "seated_trunk_offset_from_stand_m": [0.0, round(DY_SIT, 4), round(-Z_B, 4)],
                       "rule": "standing: straight legs hip->ankle, height = RATIO x seated; seated: trunk on its sculpted base, "
                               "feet planted, shin vertical over the sculpted ankle -> L2 = (S^2-dx^2-dy^2-c^2)/(2(S+c)), L1 = S-L2"}
report["length_added_m"] = {"leg_total_new": round(S_TOT + 0.0, 4), "sculpted_arc": arc_mean,
                            "added": round(S_TOT - arc_mean, 4), "note": "hip->ankle; plus the 0.30 m foot shell below the ankle"}

# ====================================================================== 6. leg shells (rest frame, pre-recentre)
T_TRUNK = np.array([0.0, 0.0, Z_B])


def frame(u):
    ex = np.array([1.0, 0.0, 0.0]); ex = ex - u * (ex @ u); ex /= np.linalg.norm(ex)
    ey = np.cross(u, ex)
    return ex, ey


def loft(rings, pole_a=None, pole_b=None):
    """rings: list of (M,3) arrays bottom -> top, azimuth increasing CCW about the axis."""
    M = len(rings[0]); R = len(rings)
    V = np.vstack(rings)
    F = []
    for i in range(R - 1):
        for j in range(M):
            a = i * M + j; b = i * M + (j + 1) % M; c = (i + 1) * M + (j + 1) % M; d = (i + 1) * M + j
            F += [[a, b, c], [a, c, d]]
    extra = []
    if pole_a is not None:
        pa = len(V) + len(extra); extra.append(pole_a)
        F += [[pa, (j + 1) % M, j] for j in range(M)]
    if pole_b is not None:
        pb = len(V) + len(extra); extra.append(pole_b)
        top = (R - 1) * M
        F += [[pb, top + j, top + (j + 1) % M] for j in range(M)]
    if extra:
        V = np.vstack([V, np.array(extra)])
    return V, np.array(F)


def aniso(nt):
    """Leg thickening: the sculpted column profile scaled (LEG_SX, LEG_SY) about its axis (azimuth kept)."""
    th = np.arange(nt) * TAU / nt
    return np.sqrt((LEG_SX * np.cos(th)) ** 2 + (LEG_SY * np.sin(th)) ** 2)


def ring_pts(C, ex, ey, r, nt):
    th = np.arange(nt) * TAU / nt
    r = r * aniso(nt)
    return C[None, :] + r[:, None] * (np.cos(th)[:, None] * ex[None, :] + np.sin(th)[:, None] * ey[None, :])


def band_z(t, n):
    """t in [0,1] along a shell band -> source z, mirror-tiled n times over [0.25, ZC]."""
    zb0, zb1 = 0.25, ZC
    tt = min(max(t, 0.0), 1.0) * n
    k = min(int(tt), n - 1); f = tt - k
    return zb0 + f * (zb1 - zb0) if k % 2 == 0 else zb1 - f * (zb1 - zb0)


def shell_leg(s, hi_res):
    nt = NT_HI if hi_res else NT_LO
    lp = not hi_res
    sx = 1.0 if s == "L" else -1.0
    H = np.array([sx * X_HIP, Y_HIP, Z_B + Z_HIP]); A = np.array([sx * X_ANK, Y_HIP, Z_ANK])
    u = (H - A) / np.linalg.norm(H - A)
    ex, ey = frame(u)
    Kn = A + u * L2
    out = {}
    # ---- shin: dome (inside the foot) + band (stretched once over [0.25, ZC]) + sculpted cap
    rings = []
    rb = at_theta(r_side(s, 0.25, lp), nt)
    for hq in ((-0.03, 0.0) if not hi_res else np.linspace(-0.03, 0.0, 8)[:-1]):
        d = (0.08 - hq) / 0.12                          # shallow dome: stays above the sole through the shin tilt
        rings.append(ring_pts(A + u * hq, ex, ey, rb * math.sqrt(max(1 - d * d, 0.05)), nt))
    nband = max(2, int(round((H_B - 0.08) / (DZ_HI if hi_res else 0.075))))
    for i in range(nband + 1):
        t = i / nband
        hq = 0.08 + t * (H_B - 0.08)
        rings.append(ring_pts(A + u * hq, ex, ey, at_theta(r_side(s, band_z(t, 1), lp), nt), nt))
    Cc = A + u * H_B
    ks = range(1, len(PHI)) if hi_res else (6, 12, 18, 24)
    for k in ks:
        ph = PHI[k]; rr = at_theta(r_cap(s, k, lp), nt)
        th = np.arange(nt) * TAU / nt
        dirs = math.cos(ph) * aniso(nt)[:, None] * (np.cos(th)[:, None] * ex + np.sin(th)[:, None] * ey) + math.sin(ph) * u
        rings.append(Cc[None, :] + rr[:, None] * dirs)
    top = Cc + u * float(np.mean(r_cap(s, len(PHI) - 1)))
    bot = A + u * (-0.045)
    out["shin"] = loft(rings, bot, top)
    out["shin_stretch"] = (H_B - 0.08) / (ZC - 0.25)
    # ---- thigh: knee dome + band (mirror-tiled x2) + hip dome
    rings = []
    nt2 = 2
    tap = lambda t: THIGH_TAPER[0] + (THIGH_TAPER[1] - THIGH_TAPER[0]) * float(smoothstep(0.0, 1.0, t))
    r0 = at_theta(r_side(s, band_z(0.0, nt2), lp), nt) * tap(0.0)
    for hq in ((-0.12, -0.07) if not hi_res else np.linspace(-0.12, 0.0, 14)[:-1]):
        d = (0.0 - hq) / 0.15
        rings.append(ring_pts(Kn + u * hq, ex, ey, r0 * math.sqrt(max(1 - d * d, 0.05)), nt))
    nb2 = max(2, int(round(L1 / (DZ_HI if hi_res else 0.08))))
    for i in range(nb2 + 1):
        t = i / nb2
        rings.append(ring_pts(Kn + u * (t * L1), ex, ey, at_theta(r_side(s, band_z(t, nt2), lp), nt) * tap(t), nt))
    r1 = at_theta(r_side(s, band_z(1.0, nt2), lp), nt) * tap(1.0)
    for hq in ((0.08, 0.15) if not hi_res else np.linspace(0.0, 0.18, 16)[1:]):
        d = hq / 0.20
        rings.append(ring_pts(Kn + u * (L1 + hq), ex, ey, r1 * math.sqrt(max(1 - d * d, 0.05)), nt))
    out["thigh"] = loft(rings, Kn + u * (-0.15), Kn + u * (L1 + 0.20))
    out["thigh_detail_scale"] = L1 / (nt2 * (ZC - 0.25))
    # ---- foot: the sculpted pad band, widened at the sole, flat sole, root toes, collar + shoulder
    rings = []
    th = np.arange(nt) * TAU / nt
    zf = ([0.0, 0.015, 0.05, 0.10, 0.16, 0.23, 0.30] if not hi_res else list(np.arange(0.0, 0.30 + 1e-9, DZ_HI)))
    toe = np.maximum(0.0, np.cos(5 * (th + math.pi / 2))) ** 4          # lobe straight ahead (-Y) + 4 around
    for z in zf:
        # profile = the sculpted COLUMN band (z 0.25-0.53), not its tapering pad tip (half-extent 0.10 m, narrower
        # than the column: a 1.6x widening of the tip gave a sole no wider than the leg on the first v2 render)
        zsrc = 0.25 + z * (0.28 / 0.30)
        w = 1.0 - float(smoothstep(0.0, 0.26, z))
        fac = FOOT_COLLAR + (FOOT_W - FOOT_COLLAR) * w
        rr = at_theta(r_side(s, zsrc, lp), nt) * fac * (1.0 + TOE_A * toe * (1.0 - float(smoothstep(0.0, 0.14, z))))
        C = np.array([A[0], A[1] - FOOT_FWD * w, z])
        rings.append(ring_pts(C, np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), rr, nt))
    rtop = at_theta(r_side(s, 0.53, lp), nt) * FOOT_COLLAR
    for z, f_ in ((0.34, 0.85), (0.38, 0.55)) if not hi_res else [(0.30 + q, math.sqrt(max(1 - (q / 0.12) ** 2, 0.05))) for q in np.linspace(0.0, 0.11, 12)[1:]]:
        rings.append(ring_pts(np.array([A[0], A[1], z]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), rtop * f_, nt))
    out["foot"] = loft(rings, np.array([A[0], A[1] - FOOT_FWD, 0.0]), np.array([A[0], A[1], 0.42]))
    out["joints"] = {"hip": H, "knee": Kn, "ankle": A, "toe": A + np.array([0.0, -0.30, 0.0])}
    out["foot_extent"] = {"sole_half_x": float((rings[0][:, 0].max() - rings[0][:, 0].min()) / 2),
                          "sole_half_y": float((rings[0][:, 1].max() - rings[0][:, 1].min()) / 2)}
    return out


LEGS_LO = {s: shell_leg(s, False) for s in "LR"}
LEGS_HI = {s: shell_leg(s, True) for s in "LR"}


def signed_volume(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


shell_rep = {}
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        for LEGS in (LEGS_LO, LEGS_HI):
            V, F = LEGS[s][part]
            if signed_volume(V, F) < 0:
                LEGS[s][part] = (V, F[:, ::-1].copy())
        V, F = LEGS_LO[s][part]
        shell_rep["%s.%s" % (part, s)] = {"tris_low": int(len(F)), "tris_high": int(len(LEGS_HI[s][part][1])),
                                          "volume_m3": round(signed_volume(V, F), 4)}
fx0 = report["column_profile"]["L"]["pad_half_extent_m"]
_band = (ZS > 0.10) & (ZS < 0.45)
col_half = {s: {"x": float(np.median((COL[s]["RS"][_band] * np.abs(np.cos(TH))[None, :]).max(1))),
                "y": float(np.median((COL[s]["RS"][_band] * np.abs(np.sin(TH))[None, :]).max(1)))} for s in "LR"}
sole_half = {s: LEGS_HI[s]["foot_extent"] for s in "LR"}
report["foot_widening_factors"] = {
    s: {"sculpted_column_half_extent_m_z0.10-0.45": {k: round(v, 4) for k, v in col_half[s].items()},
        "sculpted_pad_tip_half_extent_m": report["column_profile"][s]["pad_half_extent_m"],
        "new_sole_half_extent_m": {"x": round(sole_half[s]["sole_half_x"], 4), "y": round(sole_half[s]["sole_half_y"], 4)},
        "width_x_vs_sculpted_column": round(sole_half[s]["sole_half_x"] / col_half[s]["x"], 3),
        "depth_y_vs_sculpted_column": round(sole_half[s]["sole_half_y"] / col_half[s]["y"], 3),
        "width_x_vs_sculpted_pad_tip": round(sole_half[s]["sole_half_x"] / report["column_profile"][s]["pad_half_extent_m"]["x"], 3),
        "sole_area_vs_sculpted_column_section": round(sole_half[s]["sole_half_x"] * sole_half[s]["sole_half_y"] / (col_half[s]["x"] * col_half[s]["y"]), 3)}
    for s in "LR"}
report["legs"] = {"shells": shell_rep,
                  "shin_detail_stretch": round(LEGS_LO["L"]["shin_stretch"], 3),
                  "thigh_detail_scale_mirror_tiled_x2": round(LEGS_LO["L"]["thigh_detail_scale"], 3),
                  "foot_widening": {"sole_radius_factor": FOOT_W, "collar_factor": FOOT_COLLAR, "toe_lobes": 5, "toe_amplitude": TOE_A,
                                    "sculpted_pad_half_extent_m": fx0,
                                    "new_sole_half_extent_m": {k: round(v, 4) for k, v in LEGS_HI["L"]["foot_extent"].items()},
                                    "blend": "radius factor smoothstep(z, 0, 0.26 m) from %.1fx at the sole to %.1fx at the ankle collar; sole flat at z = 0" % (FOOT_W, FOOT_COLLAR),
                                    "toes_forward_m": FOOT_FWD}}

# ====================================================================== 7. assemble the standing low + high
parts_V, parts_F, fkind, fside, vlabel = [], [], [], [], []
voff = 0
TVr = TV + T_TRUNK
parts_V.append(TVr); parts_F.append(TF); fkind += ["trunk"] * len(TF); fside += [""] * len(TF); vlabel += ["trunk"] * len(TVr)
voff = len(TVr)
patch_vert_ranges = []
for p in patches:
    ids = np.concatenate([np.array(p["loop"]), voff + np.arange(len(p["V"]) - p["nb"])])
    parts_V.append(p["V"][p["nb"]:] + T_TRUNK); parts_F.append(ids[p["T"]])
    fkind += ["membrane"] * len(p["T"]); fside += [p["side"]] * len(p["T"]); vlabel += ["membrane." + p["side"]] * (len(p["V"]) - p["nb"])
    patch_vert_ranges.append((voff, voff + len(p["V"]) - p["nb"], np.array(p["loop"])))
    voff += len(p["V"]) - p["nb"]
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_LO[s][part]
        parts_V.append(V); parts_F.append(F + voff)
        fkind += [part] * len(F); fside += [s] * len(F); vlabel += ["%s.%s" % (part, s)] * len(V)
        voff += len(V)
VL = np.vstack(parts_V); FL = np.vstack(parts_F)
fkind = np.array(fkind); fside = np.array(fside); vlabel = np.array(vlabel)
lo_b, hi_b = VL.min(0), VL.max(0)
S_RC = np.array([-(lo_b[0] + hi_b[0]) / 2, -(lo_b[1] + hi_b[1]) / 2, -lo_b[2]])
VL = VL + S_RC
report["recentre_shift_m"] = S_RC.round(5).tolist()
T_REST = T_TRUNK + S_RC                                  # trunk coords -> rest frame
# rigidity proof on the trunk: edge lengths + face normals vs the seated sculpt
e_old = np.linalg.norm(TV[TF[:, 0]] - TV[TF[:, 1]], axis=1); e_new = np.linalg.norm(VL[TF[:, 0]] - VL[TF[:, 1]], axis=1)
n_old = np.cross(TV[TF[:, 1]] - TV[TF[:, 0]], TV[TF[:, 2]] - TV[TF[:, 0]])
n_new = np.cross(VL[TF[:, 1]] - VL[TF[:, 0]], VL[TF[:, 2]] - VL[TF[:, 0]])
report["rest_rebuild"] = {"trunk_faces_kept": int(len(TF)), "trunk_edge_stretch_max_abs": float(np.abs(e_new / e_old - 1).max()),
                          "trunk_flipped_faces": int(((n_old * n_new).sum(1) < 0).sum()),
                          "trunk_transform": "rigid translation %s" % T_REST.round(4).tolist()}
# leg shells: inverted faces (normal against the outward direction from the shell axis)
inv_ = 0
for s in "LR":
    J = LEGS_LO[s]["joints"]
    for part, (a0, a1) in (("thigh", ("knee", "hip")), ("shin", ("ankle", "knee")), ("foot", ("ankle", "ankle"))):
        m = (vlabel[FL[:, 0]] == "%s.%s" % (part, s))
        Fp = FL[m]
        cen = VL[Fp].mean(1)
        nrm = np.cross(VL[Fp[:, 1]] - VL[Fp[:, 0]], VL[Fp[:, 2]] - VL[Fp[:, 0]])
        pa, pb = J[a0] + S_RC, J[a1] + S_RC
        if part == "foot":
            # the foot's rings are centred FOOT_FWD ahead of the ankle at the sole (toes forward): the outward
            # reference is that ring-centre line, clamped inside the shell (z 0.10-0.30)
            zc = np.clip(cen[:, 2], 0.10, 0.30)
            ctr = np.stack([np.full(len(cen), pa[0]), pa[1] - FOOT_FWD * (1.0 - smoothstep(0.0, 0.26, zc)), zc], 1)
            out_ = cen - ctr
        else:
            ab = pb - pa
            tpar = np.clip(((cen - pa) @ ab) / max(ab @ ab, 1e-12), 0, 1)
            out_ = cen - (pa + tpar[:, None] * ab)
        inv_ += int(((nrm * out_).sum(1) < -1e-12).sum())
report["rest_rebuild"]["leg_shell_inverted_faces"] = inv_
report["rest_rebuild"]["tris_total"] = int(len(FL))
# closure: every edge of trunk+membranes and of each shell used exactly twice, once per direction
def edge_audit(Fs):
    cnt = defaultdict(int)
    for f in Fs:
        for k in range(3):
            cnt[(int(f[k]), int(f[(k + 1) % 3]))] += 1
    bad_dir = sum(1 for e, c in cnt.items() if c > 1)
    open_ = sum(1 for e in cnt if (e[1], e[0]) not in cnt)
    return {"directed_edge_duplicates": bad_dir, "open_edges": open_}
report["rest_rebuild"]["trunk_plus_membranes_closure"] = edge_audit(FL[np.isin(fkind, ("trunk", "membrane"))])
report["rest_rebuild"]["shell_closure"] = {f"{p}.{s}": edge_audit(FL[vlabel[FL[:, 0]] == f"{p}.{s}"]) for s in "LR" for p in ("thigh", "shin", "foot")}

# high master (rest frame): trunk high minus the legs + membrane highs + shell highs
_, nnear = None, None
removed_low = np.ones(n0, bool); removed_low[TOI] = False      # every low vertex the cut (+ hole growth) took
hi_leg = np.array([removed_low[kd0.find(p)[1]] for p in WH])
keepF = ~hi_leg[FH].any(1)
HV = [WH + T_REST]; HF = [FH[keepF]]; hoff = len(WH)
for p in patches:
    HV.append(p["Vh"] + T_REST); HF.append(p["Th"] + hoff); hoff += len(p["Vh"])
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_HI[s][part]
        HV.append(V + S_RC); HF.append(F + hoff); hoff += len(V)
HVa = np.vstack(HV); HFa = np.vstack(HF)
report["high_master"]["standing_tris"] = int(len(HFa))
report["high_master"]["trunk_high_faces_removed_as_leg"] = int((~keepF).sum())

# ---- cavity per new low face (shell highs: grid Laplacian on the high shells, scaled like the approved look)
def shell_cavity(V, F):
    ev = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    ev = np.unique(np.sort(ev, 1), axis=0)
    deg = np.bincount(ev.ravel(), minlength=len(V)).astype(float)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    el = np.linalg.norm(V[ev[:, 0]] - V[ev[:, 1]], axis=1).mean()
    cav = ((neighbour_mean(V, ev, deg) - V) * vn).sum(1) / el
    for _ in range(6):
        cav = neighbour_mean(cav[:, None], ev, deg)[:, 0] * 0.5 + cav * 0.5
    return np.clip(cav / CAV_SCALE, -1, 1)


cav_pts, cav_val = [], []
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_HI[s][part]
        cav_pts.append(V + S_RC); cav_val.append(shell_cavity(V, F))
cav_pts = np.vstack(cav_pts); cav_val = np.concatenate(cav_val)
kdc = KDTree(len(cav_pts))
for i, p in enumerate(cav_pts):
    kdc.insert(p, i)
kdc.balance()


def srgb(r, g, b):
    def c(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return np.array([c(r), c(g), c(b)])


# ---- colours: trunk corners verbatim; new faces by improve_unit's eldroot region rules
nf = len(FL)
FC = VL[FL].mean(1)
FNr = np.cross(VL[FL[:, 1]] - VL[FL[:, 0]], VL[FL[:, 2]] - VL[FL[:, 0]])
FNr /= np.maximum(np.linalg.norm(FNr, axis=1), 1e-12)[:, None]
newf = fkind != "trunk"
# membranes are judged in trunk (seated) coordinates like their neighbours; legs in the rest frame
FCq = FC.copy()
FCq[np.isin(fkind, ("membrane", "trunk"))] -= T_REST
fh_ = FCq[:, 2] / H_SEAT
fcav = np.zeros(nf)
for i in np.nonzero(newf & (fkind != "membrane"))[0]:
    fcav[i] = float(np.mean([cav_val[j] for (_, j, _) in kdc.find_n(FC[i], 6)]))
colf = np.tile(srgb(94, 78, 64), (nf, 1))
t_ = 1 - smoothstep(0.22, 0.34, fh_); colf = colf * (1 - t_[:, None]) + srgb(66, 52, 44) * t_[:, None]
t_ = smoothstep(0.50, 0.75, FNr[:, 2]) * smoothstep(0.15, 0.30, fh_) * (fh_ < 0.93) * (fcav < 0.2) * ((fh_ < 0.55) | (FCq[:, 1] > 0))
colf = colf * (1 - t_[:, None]) + srgb(92, 118, 60) * t_[:, None]
t_ = np.where((fcav > 0.35) & (fh_ > 0.45), smoothstep(0.35, 0.7, fcav), 0.0)
colf = colf * (1 - t_[:, None]) + srgb(30, 22, 18) * t_[:, None]
shade = 1.0 - 0.45 * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FCq @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
colf = np.clip(colf * shade[:, None], 0, 1)
COLC = np.zeros((nf, 3, 4)); GLOWC = np.zeros((nf, 3, 4))
ntr = len(TF)
COLC[:ntr] = TCOL; GLOWC[:ntr] = TGLOW
COLC[ntr:, :, :3] = colf[ntr:, None, :]; COLC[ntr:, :, 3] = 1.0
GLOWC[ntr:, :, 3] = 1.0
# the leg/trunk crease no longer exists: trunk faces near the cut lose its cavity darkening (blend to the plain
# region rule over 5-15 cm from the cut), everything else keeps its approved colour verbatim
loopv = np.concatenate([np.array(lp) for lp in loops_])
Ftc = TV[TF].mean(1)
dband = np.min(np.stack([np.linalg.norm(Ftc - TV[v], axis=1) for v in loopv]), axis=0)
wb = 1 - smoothstep(0.05, 0.15, dband)
COLC[:ntr, :, :3] = COLC[:ntr, :, :3] * (1 - wb[:, None, None]) + colf[:ntr, None, :] * wb[:, None, None]
report["look"] = {"trunk_corner_colours": "verbatim (approved palette) except the old crease band",
                  "trunk_faces_recoloured_crease_band": {"any": int((wb > 1e-3).sum()), "full": int((wb > 0.999).sum())},
                  "new_faces": int(newf.sum()),
                  "new_faces_by_kind": {k: int((fkind == k).sum()) for k in ("membrane", "thigh", "shin", "foot")},
                  "rule": "improve_unit eldroot regions (old_bark / root_feet by height / moss up-facing / hollow_dark cavity) + cavity shade k 0.45 + jitter"}

# ---- build the mesh
me = bpy.data.meshes.new("eldroot2_mesh")
me.from_pydata(VL.tolist(), [], FL.tolist())
me.update()
me.shade_flat()
ca = me.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
ga = me.color_attributes.new("Glow", "FLOAT_COLOR", "CORNER")
ca.data.foreach_set("color", COLC.reshape(-1).astype(np.float32))
ga.data.foreach_set("color", GLOWC.reshape(-1).astype(np.float32))
me.color_attributes.active_color = ca
me.color_attributes.render_color_index = me.color_attributes.find("Col")
mat = me0.materials[0]
me.materials.append(mat)
low.modifiers.remove(low.modifiers["Armature"])
low.parent = None
bpy.data.objects.remove(old_rig, do_unlink=True)
low.data = me
low.vertex_groups.clear()
bpy.data.meshes.remove(me0)
bpy.data.meshes.remove(me_hi)

if "--geo-only" in argv:
    hm = bpy.data.meshes.new("eldroot2_high"); hm.from_pydata(HVa.tolist(), [], HFa.tolist()); hm.update()
    ho = bpy.data.objects.new("eldroot2_high", hm); scene.collection.objects.link(ho); ho.hide_render = True
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
    json.dump(report, open(OUT_JSON, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("GEO_DONE", json.dumps({k: report[k] for k in ("measure", "leg_solve", "trunk_mass")}, default=str))
    sys.stdout.flush(); os._exit(0)

# ====================================================================== 8. UVs + bake (whole standing low from the standing high)
t8 = time.time()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.object.mode_set(mode="OBJECT")
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn); UVn = UVn.reshape(-1, 3, 2)
uva = 0.5 * np.abs((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 1, 1] - UVn[:, 0, 1]) * (UVn[:, 2, 0] - UVn[:, 0, 0]))
a3 = 0.5 * np.linalg.norm(np.cross(VL[FL[:, 1]] - VL[FL[:, 0]], VL[FL[:, 2]] - VL[FL[:, 0]]), axis=1)
dens = np.sqrt(uva / np.maximum(a3, 1e-12))
legf = np.isin(fkind, ("thigh", "shin", "foot"))
report["uv"] = {"uv_sha": sha(UVn), "method": "fresh Smart UV (improve_unit params: 66 deg, margin 0.004) on the whole standing mesh",
                "linear_texel_density_uv_per_m": {"trunk_median": round(float(np.median(dens[fkind == 'trunk'])), 4),
                                                  "legs_median": round(float(np.median(dens[legf])), 4),
                                                  "legs_p05_p95": [round(float(np.percentile(dens[legf], 5)), 4), round(float(np.percentile(dens[legf], 95)), 4)],
                                                  "trunk_p05_p95": [round(float(np.percentile(dens[fkind == 'trunk'], 5)), 4), round(float(np.percentile(dens[fkind == 'trunk'], 95)), 4)]},
                "approved_seated_density_ref": round(math.sqrt(0.458 / 26.459), 4)}
hm = bpy.data.meshes.new("eldroot2_high"); hm.from_pydata(HVa.tolist(), [], HFa.tolist()); hm.update()
if hi_smooth_frac > 0.5:
    hm.shade_smooth()
ho = bpy.data.objects.new("eldroot2_high", hm); scene.collection.objects.link(ho)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
EXT = 0.076                                              # the approved bake's cage (0.02 x 3.8 m footprint)
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = EXT; bk.max_ray_distance = EXT * 2.0; bk.margin = 16; bk.use_clear = False
nt_ = mat.node_tree
img_nodes = [n for n in nt_.nodes if n.bl_idname == "ShaderNodeTexImage"]
old_imgs = [n.image for n in img_nodes if n.image]
img_n = bpy.data.images.new("eldroot2_normal", 2048, 2048, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new("eldroot2_ao", 1024, 1024, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = ta = None
for n_ in img_nodes:
    if n_.image and "normal" in n_.image.name:
        n_.image = img_n; tn = n_
    elif n_.image and "ao" in n_.image.name:
        n_.image = img_ao; ta = n_
for im in old_imgs:
    if im.users == 0:
        bpy.data.images.remove(im)
me.shade_smooth()
for o in scene.objects:
    o.select_set(o in (ho, low))
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt_.nodes.active = node
    scene.cycles.samples = samples
    t = time.time()
    r = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=EXT, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r), "seconds": round(time.time() - t, 1)}
me.shade_flat()
px = np.empty(2048 * 2048 * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(1024 * 1024 * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
dev = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02


def texels_of(mask_faces, res):
    """Texel mask (res x res) covered by the given faces' UV triangles (centre sampling)."""
    m = np.zeros((res, res), bool)
    for t in UVn[mask_faces]:
        p = t * res
        x0, y0 = np.floor(p.min(0)).astype(int); x1, y1 = np.ceil(p.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, res)), np.arange(max(y0, 0), min(y1, res)))
        if xs.size == 0:
            continue
        q = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
        a, b, c = p

        def ed(u_, v_):
            return (v_[0] - u_[0]) * (q[:, 1] - u_[1]) - (v_[1] - u_[1]) * (q[:, 0] - u_[0])
        d1, d2, d3 = ed(a, b), ed(b, c), ed(c, a)
        ins = ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
        m[ys.ravel()[ins], xs.ravel()[ins]] = True
    return m.ravel()


legtex = texels_of(legf, 2048)
trunktex = texels_of(fkind == "trunk", 2048)
membtex = texels_of(fkind == "membrane", 2048)
bstats.update({
    "normal_covered_texels": round(float(cov_n.mean()), 4),
    "normal_detail_fraction_dev_gt_0.05": round(float((dev[cov_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(dev[cov_n].mean()), 4),
    "legs": {"texels": int(legtex.sum()), "baked_pct": round(100 * float(cov_n[legtex].mean()), 2),
             "detail_fraction_dev_gt_0.05": round(float((dev[legtex & cov_n] > 0.05).mean()), 4), "normal_dev_mean": round(float(dev[legtex & cov_n].mean()), 4)},
    "trunk": {"texels": int(trunktex.sum()), "baked_pct": round(100 * float(cov_n[trunktex].mean()), 2),
              "detail_fraction_dev_gt_0.05": round(float((dev[trunktex & cov_n] > 0.05).mean()), 4), "normal_dev_mean": round(float(dev[trunktex & cov_n].mean()), 4)},
    "membranes": {"texels": int(membtex.sum()), "baked_pct": round(100 * float(cov_n[membtex].mean()), 2) if membtex.any() else None},
    "ao_covered_texels": round(float(cov_a.mean()), 4), "ao_mean": round(float(pa[cov_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4),
    "approved_seated_reference": {"normal_covered_texels": 0.7129, "normal_detail_fraction_dev_gt_0.05": 0.1992, "normal_dev_mean": 0.0369,
                                  "ao_covered_texels": 0.7864, "ao_mean": 0.7891}})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, "eldroot2_normal.png"), (img_ao, "eldroot2_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
    img.filepath = "//../improved/textures/" + nm
bstats["pixel_sha"] = {"normal": sha(px), "ao": sha(pa)}
bstats["cage_extrusion"] = EXT
bstats["seconds_uv_and_bake"] = round(time.time() - t8, 1)
report["bake"] = bstats
bpy.data.objects.remove(ho, do_unlink=True)
bpy.data.meshes.remove(hm)
scene.render.engine = "BLENDER_EEVEE"

# ====================================================================== 9. rig + weights
arm_data = bpy.data.armatures.new("eldroot_rig")
rig = bpy.data.objects.new("eldroot_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
spec = [("root", old_bones["root"][0], old_bones["root"][1], None)]
for nm in ("pelvis", "chest", "crown", "upperarm.L", "forearm.L", "upperarm.R", "forearm.R"):
    hd, tl, par = old_bones[nm]
    spec.append((nm, hd + T_REST, tl + T_REST, par))
for s in "LR":
    J = LEGS_LO[s]["joints"]
    spec += [("thigh." + s, J["hip"] + S_RC, J["knee"] + S_RC, "pelvis"), ("shin." + s, J["knee"] + S_RC, J["ankle"] + S_RC, "thigh." + s),
             ("foot." + s, J["ankle"] + S_RC, J["toe"] + S_RC, "shin." + s)]
for nm, hd, tl, par in spec:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(hd); e.tail = Vector(tl)
    if par:
        e.parent = arm_data.edit_bones[par]
    e.use_connect = False
    e.use_deform = nm != "root"
    e.roll = 0.0
bpy.ops.object.mode_set(mode="OBJECT")
BONES = [b.name for b in arm_data.bones]
DEFORM = [b for b in BONES if b != "root"]
REST = {b.name: b.matrix_local.copy() for b in arm_data.bones}
RH = {b.name: Vector(b.head_local) for b in arm_data.bones}
RT = {b.name: Vector(b.tail_local) for b in arm_data.bones}
nV = len(VL)
Wn = np.zeros((nV, len(DEFORM)))
dc = {nm: i for i, nm in enumerate(DEFORM)}
nonleg = [nm for nm in old_names if not nm.startswith(("thigh", "shin"))]
ntv = len(TV)
Wk = Wold[TOI]
folded = 0
for nm in nonleg:
    Wn[:ntv, dc[nm]] = Wk[:, col_[nm]]
legshare = Wk[:, [col_[x] for x in old_names if x.startswith(("thigh", "shin"))]].sum(1)
Wn[:ntv, dc["pelvis"]] += legshare
folded = int((legshare > 1e-6).sum())
for (a0, a1, loopids) in patch_vert_ranges:
    Pm = VL[a0:a1]; B = VL[loopids]
    d = np.linalg.norm(Pm[:, None, :] - B[None, :, :], axis=2)
    w = 1.0 / np.maximum(d, 1e-4) ** 2
    Wn[a0:a1] = (w @ Wn[loopids]) / w.sum(1)[:, None]
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        Wn[vlabel == "%s.%s" % (part, s), dc["%s.%s" % (part, s)]] = 1.0
# cap influences at 4 (glTF), renormalise
for i in np.nonzero((Wn > 1e-5).sum(1) > 4)[0]:
    keep = np.argsort(Wn[i])[-4:]
    row = np.zeros_like(Wn[i]); row[keep] = Wn[i, keep]; Wn[i] = row
Wn /= np.maximum(Wn.sum(1), 1e-30)[:, None]
for j, nm in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=nm)
    for i in np.nonzero(Wn[:, j] > 1e-5)[0]:
        vg.add([int(i)], float(Wn[i, j]), "REPLACE")
report["reweight"] = {"trunk_verts_verbatim": int(ntv - folded), "trunk_verts_seated_leg_share_folded_to_pelvis": folded,
                      "membrane_verts_inverse_distance_from_boundary": int(sum(a1 - a0 for a0, a1, _ in patch_vert_ranges)),
                      "leg_shell_verts_rigid": int(np.isin(vlabel, [f"{p}.{s}" for p in ("thigh", "shin", "foot") for s in "LR"]).sum()),
                      "max_influences": int((Wn > 1e-5).sum(1).max()),
                      "per_bone_dominant": {nm: int((np.argmax(Wn, 1) == j).sum()) for j, nm in enumerate(DEFORM)}}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
mod = low.modifiers.new("Armature", "ARMATURE")
mod.object = rig

# ====================================================================== 10. pose machinery
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
depth = {}
for b in arm_data.bones:
    d_, p = 0, b.parent
    while p:
        d_ += 1; p = p.parent
    depth[b.name] = d_
LEVELS = sorted(set(depth.values()))
LL1 = {s: (RT["thigh." + s] - RH["thigh." + s]).length for s in "LR"}
LL2 = {s: (RT["shin." + s] - RH["shin." + s]).length for s in "LR"}
A1 = {s: (RT["upperarm." + s] - RH["upperarm." + s]).length for s in "LR"}
A2 = {s: (RT["forearm." + s] - RH["forearm." + s]).length for s in "LR"}


def perp_pole(hd, kn, tl):
    dv = (tl - hd).normalized()
    p = (kn - hd) - dv * (kn - hd).dot(dv)
    return p.normalized()


POLE_ARM = {s: perp_pole(RH["upperarm." + s], RH["forearm." + s], RT["forearm." + s]) for s in "LR"}
DSIT = Vector((0.0, DY_SIT, -Z_B))
PIVOT = Vector((0.0, -0.35, 0.05)) + Vector(T_REST)     # trunk front-bottom edge (rest)
SEAT_PAD = {s: RT["forearm." + s] + DSIT for s in "LR"}
ANKLE = {s: RH["foot." + s].copy() for s in "LR"}


def Tm(v):
    return Matrix.Translation(Vector(v))


def G_about(head_new, R3, head_rest):
    return Tm(head_new) @ R3.to_4x4() @ Tm(-Vector(head_rest))


def eul(deg):
    return Euler([math.radians(x) for x in deg], "XYZ").to_matrix()


def rot_of(G):
    return G.to_3x3().normalized()


def ik2(H, T, l1, l2, pole):
    d = T - H
    D = d.length
    Dc = min(max(D, abs(l1 - l2) + 1e-5), (l1 + l2) * 0.9999)
    dv = d.normalized()
    pp = pole - dv * pole.dot(dv); pp.normalize()
    a_ = (l1 * l1 - l2 * l2 + Dc * Dc) / (2 * Dc)
    hh_ = math.sqrt(max(l1 * l1 - a_ * a_, 0.0))
    return H + dv * a_ + pp * hh_, H + dv * Dc, D / (l1 + l2)


def aim_R(Rcur, rest_dir, target_dir):
    cur = (Rcur @ rest_dir).normalized()
    q = cur.rotation_difference(target_dir.normalized())
    return q.to_matrix() @ Rcur


reach_log = {}


def solve(P):
    G = {"root": Matrix.Identity(4)}
    d = Vector((P.get("px", 0.0), P.get("py", 0.0), P.get("lift", 0.0)))
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    G["pelvis"] = Tm(PIVOT + d) @ Rp.to_4x4() @ Tm(-PIVOT)
    hc = G["pelvis"] @ RH["chest"]
    G["chest"] = G_about(hc, eul(P.get("chest", (0, 0, 0))) @ Rp, RH["chest"])
    hk = G["chest"] @ RH["crown"]
    G["crown"] = G_about(hk, eul(P.get("crown", (0, 0, 0))) @ rot_of(G["chest"]), RH["crown"])
    for s in "LR":
        H = G["pelvis"] @ RH["thigh." + s]
        A_ = Vector(P.get("feet", {}).get(s, ANKLE[s]))
        # knee pole: the in-sagittal-plane perpendicular of hip->ankle, bent the forward/up way: forward while
        # the leg hangs straight, UP when folded (seated: hip behind the ankle -> the knee rises over it).
        # Never parallel to the leg (the old forward->up blend went degenerate mid stand_up with the hip over
        # the ankle, and the knee flipped out sideways: a shin 99.5 mm under the floor at stand_up frame 65).
        pole = (A_ - H).normalized().cross(Rp @ Vector((1.0, 0.0, 0.0)))
        Kp, Tp, reach = ik2(H, A_, LL1[s], LL2[s], pole)
        reach_log.setdefault("leg." + s, []).append(reach)
        Rt = aim_R(Rp, RT["thigh." + s] - RH["thigh." + s], Kp - H)
        G["thigh." + s] = G_about(H, Rt, RH["thigh." + s])
        Rs = aim_R(Rt, RT["shin." + s] - RH["shin." + s], Tp - Kp)
        G["shin." + s] = G_about(Kp, Rs, RH["shin." + s])
        yaw = P.get("foot_yaw", {}).get(s, 0.0)
        G["foot." + s] = G_about(A_, eul((0, 0, yaw)), RH["foot." + s])
        Hs = G["pelvis"] @ RH["upperarm." + s]
        ar = P.get("arms", {}).get(s, {})
        Ru_fk = eul(ar.get("rot", (0, 0, 0))) @ Rp
        Gu_fk = G_about(Hs, Ru_fk, RH["upperarm." + s])
        he_fk = Gu_fk @ RH["forearm." + s]
        Rf_fk = eul(ar.get("fore", (0, 0, 0))) @ Ru_fk
        Gf_fk = G_about(he_fk, Rf_fk, RH["forearm." + s])
        plant = ar.get("plant", 0.0)
        if plant <= 0.0:
            G["upperarm." + s], G["forearm." + s] = Gu_fk, Gf_fk
            continue
        tip_fk = Gf_fk @ RT["forearm." + s]
        tgt = Vector(ar.get("target", SEAT_PAD[s]))
        tgt = tgt.lerp(tip_fk, ar.get("peel", 0.0))
        Ke, Te, rch = ik2(Hs, tgt, A1[s], A2[s], Rp @ POLE_ARM[s])
        if plant > 0.5:
            reach_log.setdefault("arm." + s, []).append(rch)
        Ru_ik = aim_R(Rp, RT["upperarm." + s] - RH["upperarm." + s], Ke - Hs)
        Rf_ik = aim_R(Ru_ik, RT["forearm." + s] - RH["forearm." + s], Te - Ke)
        qu = Ru_fk.to_quaternion().slerp(Ru_ik.to_quaternion(), plant)
        Gu = G_about(Hs, qu.to_matrix(), RH["upperarm." + s])
        he = Gu @ RH["forearm." + s]
        qf = Rf_fk.to_quaternion().slerp(Rf_ik.to_quaternion(), plant)
        G["upperarm." + s], G["forearm." + s] = Gu, G_about(he, qf.to_matrix(), RH["forearm." + s])
    return G


def apply(G):
    for pb in pose:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    for lv in LEVELS:
        for nm in BONES:
            if depth[nm] == lv and nm in G and nm != "root":
                pose[nm].matrix = G[nm] @ REST[nm]
        bpy.context.view_layer.update()


def key_all(frame, prevq):
    for pb in pose:
        if pb.name == "root":
            continue
        q = pb.rotation_quaternion.copy()
        if pb.name in prevq and prevq[pb.name].dot(q) < 0:
            q.negate(); pb.rotation_quaternion = q
        prevq[pb.name] = q.copy()
        pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
        pb.keyframe_insert("location", frame=frame, group=pb.name)


def mesh_coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


apply({})
Cr = mesh_coords()
report["rest_equals_standing_mesh_max_mm"] = round(float(np.linalg.norm(Cr - VL, axis=1).max()) * 1000, 4)


# ====================================================================== 11. motion
def sn(t, k=1.0, ph=0.0):
    return math.sin(TAU * k * t + ph)


def cs(t, k=1.0, ph=0.0):
    return math.cos(TAU * k * t + ph)


def s01(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (6 * x - 15) + 10)


def win(t, a, b):
    return s01((t - a) / (b - a))


def bump(t, c, w):
    d = abs(t - c)
    return math.cos(0.5 * math.pi * d / w) ** 2 if d < w else 0.0


def pulse(t, c, w=0.25):
    d = (t - c) % 1.0
    return math.sin(math.pi * d / w) if d < w else 0.0


def lerp(a, b, x):
    if isinstance(a, (tuple, list)):
        return tuple(lerp(p, q, x) for p, q in zip(a, b))
    return a + (b - a) * x


def v3add(a, b):
    return tuple(p + q for p, q in zip(a, b))


LTOT = min(LL1[s] + LL2[s] for s in "LR")
clamp_log = {"frames": 0}


def reach_clamp(P, cap=0.9985):
    """Keep every planted leg inside cap x full reach by lowering the hips (never lifts)."""
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    for _ in range(3):
        d = Vector((P.get("px", 0.0), P.get("py", 0.0), P.get("lift", 0.0)))
        worst = 0.0
        for s in "LR":
            H = Tm(PIVOT + d) @ Rp.to_4x4() @ Tm(-PIVOT) @ RH["thigh." + s]
            A_ = Vector(P.get("feet", {}).get(s, ANKLE[s]))
            D = (H - A_).length
            lim = cap * (LL1[s] + LL2[s])
            if D > lim:
                v = H - A_
                # lower along z until |v| = lim
                hz = math.sqrt(max(lim * lim - v.x * v.x - v.y * v.y, 0.0))
                worst = max(worst, v.z - hz)
        if worst <= 1e-7:
            return P
        P["lift"] = P.get("lift", 0.0) - worst - 1e-5
        clamp_log["frames"] += 1
    return P


def idle_P(t):
    """Standing idle: the seated wave's creak-sway ported; knees ~10 deg off straight (the columns stay columns)."""
    return {"lift": -0.026 + 0.005 * sn(t),
            "pelvis": (0.5 * sn(t, 2), 1.0 * sn(t), 0.5 * sn(t, 1, 0.9)),
            "chest": (1.0 * sn(t, 2, -0.5), 1.5 * sn(t, 1, -0.5), 1.0 * sn(t, 1, -0.3)),
            "crown": (1.5 * sn(t, 1, -1.4), 3.0 * sn(t, 1, -1.2), 2.0 * sn(t, 1, -0.4)),
            "arms": {"L": {"rot": (4.0 + 1.5 * sn(t, 1, -0.9), 0.0, 1.0 * sn(t, 1, -0.4)), "fore": (-6.0 + 2.0 * sn(t, 1, -1.5), 0, 0)},
                     "R": {"rot": (4.0 + 1.5 * sn(t, 1, -0.9), 0.0, -1.0 * sn(t, 1, -0.4)), "fore": (-6.0 + 2.0 * sn(t, 1, -1.5), 0, 0)}}}


def sit_P(t):
    """Sitting idle: dormant, rooted; the trunk rests exactly on its sculpted base, knuckles planted."""
    return {"lift": -Z_B, "py": DY_SIT,
            "chest": (0.6 * sn(t), 0.0, 0.3 * sn(t, 1, 1.1)),
            "crown": (0.9 * sn(t, 1, -0.7), 0.6 * sn(t, 1, -1.3), 0.0),
            "arms": {s: {"plant": 1.0} for s in "LR"}}


def blendP(Pa, Pb, x):
    out = {}
    for k in ("lift", "px", "py"):
        out[k] = lerp(Pa.get(k, 0.0), Pb.get(k, 0.0), x)
    for k in ("pelvis", "chest", "crown"):
        out[k] = lerp(Pa.get(k, (0, 0, 0)), Pb.get(k, (0, 0, 0)), x)
    out["arms"] = {}
    for s in "LR":
        a_, b_ = Pa.get("arms", {}).get(s, {}), Pb.get("arms", {}).get(s, {})
        out["arms"][s] = {"rot": lerp(a_.get("rot", (0, 0, 0)), b_.get("rot", (0, 0, 0)), x),
                          "fore": lerp(a_.get("fore", (0, 0, 0)), b_.get("fore", (0, 0, 0)), x),
                          "plant": lerp(a_.get("plant", 0.0), b_.get("plant", 0.0), x)}
    return out


I0, S0 = idle_P(0.0), sit_P(0.0)
STAND_S, SIT_S = 5.0, 4.5

# ---- the rise path (hips), shared by stand_up and sit_down. The first v2 clips moved the hips forward over the
# feet while still low: a 2.9 m leg compressed to ~1 m folds the knee 1.2 m forward and DOWN (shin 80 deg over,
# 195 mm under the floor). Rule: the path is driven by the KNEE - it stays over the ankle, tipping at most
# PSI_MAX forward mid-rise - and the thigh swings from its seated angle (back-down) to vertical around it. The hips
# rise behind the feet first (knuckles still planted), then arc forward over the pads.
PSI_MAX = math.radians(24.0)
A_M = (ANKLE["L"] + ANKLE["R"]) / 2
RHM = (RH["thigh.L"] + RH["thigh.R"]) / 2
L1M = (LL1["L"] + LL1["R"]) / 2
L2M = (LL2["L"] + LL2["R"]) / 2
H_SEAT_W = RHM + DSIT
H_STAND_W = PIVOT + eul(I0["pelvis"]) @ (RHM - PIVOT) + Vector((0.0, 0.0, I0["lift"]))   # idle frame 1 hips, exactly
K_SEAT_W = A_M + Vector((0.0, 0.0, L2M))
_v = H_SEAT_W - K_SEAT_W
A_SEAT = math.atan2(_v.z, _v.y)                         # thigh elevation in the sagittal plane, from rearward (+Y)
L1YZ = math.hypot(_v.y, _v.z)


def _path_raw(g):
    psi = PSI_MAX * math.sin(math.pi * g)
    a = A_SEAT + (math.pi / 2 - A_SEAT) * g
    K = A_M + L2M * Vector((0.0, -math.sin(psi), math.cos(psi)))
    return K + L1YZ * Vector((0.0, math.cos(a), math.sin(a)))


_E0 = H_SEAT_W - _path_raw(0.0)
_E1 = H_STAND_W - _path_raw(1.0)


def hip_path(g):
    return _path_raw(g) + _E0 * (1.0 - g) + _E1 * g


def hips_to(P, H):
    """Pelvis translation that puts the mid-hip at H under the pose's pelvis rotation (about PIVOT)."""
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    d = H - PIVOT - Rp @ (RHM - PIVOT)
    # floor guard: a forward pitch about the hips would sink the trunk front edge; while seated-low the trunk
    # instead rocks on that edge (PIVOT never below its seated height)
    d.z = max(d.z, DSIT.z)
    P["py"], P["lift"] = d.y, d.z
    return P


report["rise_path"] = {"knee_tip_max_deg": math.degrees(PSI_MAX), "thigh_seated_elevation_deg": round(math.degrees(A_SEAT), 2),
                       "endpoint_corrections_mm": {"seat": round(_E0.length * 1000, 2), "stand": round(_E1.length * 1000, 2)},
                       "rule": "knee = ankle + L2 tipped psi(g) = PSI_MAX sin(pi g) forward; hip = knee + L1 at elevation "
                               "lerp(seated, 90 deg, g); endpoint residuals blended out linearly"}


def stand_up_P(ts):
    """Seated 2.7 m -> standing 5.4 m, 5.0 s, feet planted the whole way.
    0.0-1.0 stir: crown lifts, chest swells, knuckles press.   0.7-2.6 load: the trunk pitches 16 deg onto the
    knuckles.   1.3-2.15 STRAIN: the sculpted base is rooted - it lifts 0.10 m with a 7 Hz tremble, then TEARS
    FREE (a 0.06 m jerk at 2.1 s).   1.6-4.3 the big rise along the knee-driven path: the hips climb behind the
    feet, then arc forward over the pads as the legs lock straight; knuckles push until 2.3 s, peel 2.1-2.7, swing
    to hang.   4.2-5.0 settle: the mass lands on the columns - knees give 5 cm and recover, the lean rocks back
    2 deg, crown nods - into idle frame 1."""
    g = s01((ts - 1.6) / 2.7)
    P = blendP(S0, I0, g)
    strain = win(ts, 1.3, 1.9) * (1 - g)
    trem = bump(ts, 1.75, 0.45)
    tear = win(ts, 2.02, 2.16) * (1 - g)
    lean = 16.0 * win(ts, 0.7, 2.0) * (1 - win(ts, 2.6, 4.0)) - 2.0 * bump(ts, 4.4, 0.4)
    P["pelvis"] = v3add(P["pelvis"], (lean, 1.2 * math.sin(TAU * 6.0 * ts) * trem, 0.0))
    hips_to(P, hip_path(g))
    P["lift"] += (0.10 * strain + 0.06 * tear) * (1 - g) + 0.008 * math.sin(TAU * 7.0 * ts) * trem - 0.05 * bump(ts, 4.45, 0.35)
    P["chest"] = v3add(P["chest"], (3.5 * bump(ts, 0.5, 0.5) + 5.0 * bump(ts, 1.8, 0.5) - 3.5 * bump(ts, 3.9, 0.5), 0.0, 0.0))
    P["crown"] = v3add(P["crown"], (-4.0 * bump(ts, 0.45, 0.45) - 7.0 * bump(ts, 4.2, 0.55) + 1.5 * math.sin(TAU * 6.5 * ts) * trem + 3.0 * bump(ts, 2.1, 0.12), 0.0, 0.0))
    peel = s01((ts - 2.1) / 0.6)
    for s in "LR":
        P["arms"][s]["plant"] = 1.0 - s01((ts - 2.3) / 0.5)
        P["arms"][s]["peel"] = peel
        P["arms"][s]["rot"] = lerp((0, 0, 0), I0["arms"][s]["rot"], s01((ts - 2.6) / 1.5))
        P["arms"][s]["fore"] = lerp((0, 0, 0), I0["arms"][s]["fore"], s01((ts - 2.6) / 1.5))
    return reach_clamp(P)


def sit_down_P(ts):
    """Standing -> seated, 4.5 s, feet planted. 0-0.7 the crown dips, weight settles. 0.5-3.5 the hips run the
    rise path backwards: they arc back off the pads and sink behind the feet (knees tip forward <= 24 deg); the
    trunk pitches 10 deg forward to counter the mass, upright again before touch-down. 2.6-3.5 knuckles reach down
    and plant. 3.5 touch-down: the base lands with a thud (chest + crown shudder), 3.7-4.5 settles into
    sitting_idle frame 1."""
    g = 1.0 - s01((ts - 0.5) / 3.0)
    P = blendP(I0, S0, 1.0 - g)
    lean = 10.0 * win(ts, 0.5, 1.4) * (1 - win(ts, 2.4, 3.3))
    P["pelvis"] = v3add(P["pelvis"], (lean, 0.0, 0.0))
    hips_to(P, hip_path(g))
    thud = bump(ts, 3.62, 0.22)
    P["lift"] += -0.008 * thud                        # the seated base clears the floor by 10.6 mm
    P["chest"] = v3add(P["chest"], (2.5 * bump(ts, 2.0, 0.7) - 2.5 * thud, 0.0, 0.0))
    P["crown"] = v3add(P["crown"], (3.0 * bump(ts, 0.4, 0.4) + 4.0 * thud + 2.0 * bump(ts, 4.0, 0.35), 0.0, 0.0))
    reach = s01((ts - 2.6) / 0.9)
    for s in "LR":
        P["arms"][s]["plant"] = s01((ts - 2.3) / 0.5)
        P["arms"][s]["peel"] = 1.0 - reach
        P["arms"][s]["rot"] = lerp(I0["arms"][s]["rot"], (0, 0, 0), s01((ts - 1.5) / 1.5))
        P["arms"][s]["fore"] = lerp(I0["arms"][s]["fore"], (0, 0, 0), s01((ts - 1.5) / 1.5))
    return reach_clamp(P)


WALK = dict(S=0.70, duty=0.75, lift=0.18, offsets={"L": 0.75, "R": 0.25}, arm_swing=9.0, v=0.14)
WALK["N"] = int(round(WALK["S"] * FPS / (WALK["duty"] * WALK["v"])))


def gait_target(F0_, phase, duty, S, lift):
    if phase < duty:
        u_ = phase / duty
        return (F0_[0], F0_[1] - S / 2 + S * u_, F0_[2])
    u_ = (phase - duty) / (1.0 - duty)
    m = S * (1.0 - duty) / duty
    h00, h10 = 2 * u_ ** 3 - 3 * u_ ** 2 + 1, u_ ** 3 - 2 * u_ ** 2 + u_
    h01, h11 = -2 * u_ ** 3 + 3 * u_ ** 2, u_ ** 3 - u_ ** 2
    y = h00 * (S / 2) + h10 * m + h01 * (-S / 2) + h11 * m
    return (F0_[0], F0_[1] + y, F0_[2] + lift * math.sin(math.pi * u_))


def walk_P(t):
    """Two-beat lumber at full height: L swing [0,.25), double support, R swing [.5,.75), double support.
    Hips ride 7 cm low (knees ~17 deg), dip as each foot lands, roll + shift over the stance foot; arms
    swing contralaterally, forearms lag."""
    def lean(tt):
        tt %= 1.0
        if tt < 0.25:
            return -1.0
        if tt < 0.5:
            return -1.0 + 2.0 * s01((tt - 0.25) / 0.25)
        if tt < 0.75:
            return 1.0
        return 1.0 - 2.0 * s01((tt - 0.75) / 0.25)
    dip = pulse(t, 0.25) + pulse(t, 0.75)
    effort = pulse(t, 0.0) + pulse(t, 0.5)
    lw = lean(t)
    P = {"lift": -0.07 - 0.03 * dip + 0.012 * effort, "px": 0.045 * lean(t - 0.03),
         "pelvis": (2.0 + 1.5 * dip, 2.2 * lw, 1.5 * lw),
         "chest": (1.5 * pulse(t, 0.29) + 1.5 * pulse(t, 0.79), 1.5 * lean(t - 0.06), -0.8 * lw),
         "crown": (2.0 * pulse(t, 0.35) + 2.0 * pulse(t, 0.85), 1.6 * lean(t - 0.12), 0.0),
         "feet": {}, "arms": {}}
    wk = WALK
    for s, off in wk["offsets"].items():
        P["feet"][s] = gait_target(tuple(ANKLE[s]), (t + off) % 1.0, wk["duty"], wk["S"], wk["lift"])
    A_ = wk["arm_swing"]
    P["arms"]["R"] = {"rot": (4.0 - A_ * cs(t, 1, -TAU * 0.125), 0.0, 0.0), "fore": (-6.0 - 0.45 * A_ * cs(t, 1, -TAU * 0.20), 0, 0)}
    P["arms"]["L"] = {"rot": (4.0 + A_ * cs(t, 1, -TAU * 0.125), 0.0, 0.0), "fore": (-6.0 + 0.45 * A_ * cs(t, 1, -TAU * 0.20), 0, 0)}
    return P


# proof: sitting = the sculpt's trunk exactly (rigid), measured on the evaluated mesh (no breathing)
apply(solve({"lift": -Z_B, "py": DY_SIT, "arms": {s: {"plant": 1.0} for s in "LR"}}))
Cs = mesh_coords()
trunk_orig_rest = W0[TOI] + np.array([0.0, DY_SIT, 0.0]) + S_RC
report["sit_pose"] = {"trunk_vs_sculpt_max_mm": round(float(np.linalg.norm(Cs[:ntv] - trunk_orig_rest, axis=1).max()) * 1000, 4)}
# legs: seated shells vs the sculpted columns (both directions, nearest-surface)
legF0 = F0[legdom[F0].all(1)]
sc_off = np.array([0.0, DY_SIT, 0.0]) + S_RC
bvh_orig_leg = BVHTree.FromPolygons((W0 + sc_off).tolist(), legF0.tolist())
shellv = np.isin(vlabel, [f"{p}.{s}" for p in ("thigh", "shin", "foot") for s in "LR"])
shellF = FL[legf]
bvh_new_leg = BVHTree.FromPolygons(Cs.tolist(), shellF.tolist())
d_on = np.array([(Vector(p) - bvh_new_leg.find_nearest(Vector(p))[0]).length for p in (W0[legdom] + sc_off)])
# only the shell surface OUTSIDE the trunk is visible; measure the new->sculpt direction on visible shell verts
bvh_trunk_sit = BVHTree.FromPolygons(Cs.tolist(), FL[~legf].tolist())
vis = []
for i in np.nonzero(shellv)[0]:
    p = Vector(Cs[i])
    hit = bvh_trunk_sit.ray_cast(p, Vector((0.0, 0.0, 1.0)), 10.0)
    cnt = 0; q = p
    while hit[0] is not None and cnt < 20:
        cnt += 1; q = hit[0] + Vector((0, 0, 1e-5)); hit = bvh_trunk_sit.ray_cast(q, Vector((0.0, 0.0, 1.0)), 10.0)
    vis.append(cnt % 2 == 0)
vis = np.array(vis)
sv_idx = np.nonzero(shellv)[0][vis]
d_no = np.array([(Vector(Cs[i]) - bvh_orig_leg.find_nearest(Vector(Cs[i]))[0]).length for i in sv_idx])
knee_top = {}
for s in "LR":
    ms = vlabel == "shin." + s
    knee_top[s] = round(float(Cs[ms, 2].max()), 4)
report["sit_pose"].update({
    "sculpt_leg_surface_to_new_legs_mm": {"mean": round(float(d_on.mean()) * 1000, 1), "p95": round(float(np.percentile(d_on, 95)) * 1000, 1), "max": round(float(d_on.max()) * 1000, 1)},
    "visible_new_leg_surface_to_sculpt_mm": {"verts": int(len(sv_idx)), "mean": round(float(d_no.mean()) * 1000, 1), "p95": round(float(np.percentile(d_no, 95)) * 1000, 1)},
    "knee_cap_top_z_m": knee_top, "sculpt_knee_cap_top_z_m": round(ZTOP, 4),
    "seated_height_m": round(float(Cs[:, 2].max() - Cs[:, 2].min()), 4)})

CLIPS = [
    ("sitting_idle", "loop", 144, lambda f, N: sit_P((f - 1) / N % 1.0)),
    ("stand_up", "oneshot", int(round(STAND_S * FPS)), lambda f, N: stand_up_P((f - 1) / FPS)),
    ("idle", "loop", 96, lambda f, N: idle_P((f - 1) / N % 1.0)),
    ("sit_down", "oneshot", int(round(SIT_S * FPS)), lambda f, N: sit_down_P((f - 1) / FPS)),
    ("walk", "loop", WALK["N"], lambda f, N: walk_P((f - 1) / N % 1.0)),
]
clips = {}
for name, kind, N, fn in CLIPS:
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    reach_log.clear(); clamp_log["frames"] = 0
    prevq = {}
    for f in range(1, N + 2):
        apply(solve(fn(f, N)))
        key_all(f, prevq)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = kind == "loop"
    clips[name] = {"action": name, "kind": kind, "frames": N + 1,
                   ("cycle_frames" if kind == "loop" else "duration_frames"): N, "seconds": round(N / FPS, 4),
                   "ik_reach_max": {k: round(max(v), 4) for k, v in reach_log.items()},
                   "reach_clamped_frames": clamp_log["frames"]}
    if name == "walk":
        v = WALK["S"] / (WALK["duty"] * N) * FPS
        clips[name].update({"stride_m": WALK["S"], "duty": WALK["duty"], "swing_lift_m": WALK["lift"], "speed_m_per_s": round(v, 4),
                            "belt_m_per_frame": WALK["S"] / (WALK["duty"] * N)})

# ====================================================================== 12. measure
sole = {s: (vlabel == "foot." + s) & (VL[:, 2] < 0.001) for s in "LR"}
report["pad_sole_verts"] = {s: int(sole[s].sum()) for s in "LR"}


def runs_cyclic(flags, cyclic):
    n_ = len(flags)
    if all(flags):
        return [list(range(n_))]
    if not cyclic:
        runs, cur = [], []
        for i in range(n_):
            if flags[i]:
                cur.append(i)
            elif cur:
                runs.append(cur); cur = []
        if cur:
            runs.append(cur)
        return runs
    start = next(i for i in range(n_) if not flags[i])
    runs, cur = [], []
    for k in range(1, n_ + 1):
        i = (start + k) % n_
        if flags[i]:
            cur.append(i)
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    return runs


frame_cache = {}
trunk_min_z = {}
for name, kind, N, fn in CLIPS:
    info = clips[name]
    K.assign_action(rig, bpy.data.actions[name])
    feet = {s: [] for s in "LR"}
    hands = {s: [] for s in "LR"}
    stretch, gap, root_dev = 0.0, 0.0, 0.0
    lo_, hi_ = np.full(3, 1e9), np.full(3, -1e9)
    sole_z = {s: [] for s in "LR"}
    tmin = 1e9
    last = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        for s in "LR":
            feet[s].append(np.array(pose["foot." + s].head))
            hands[s].append(np.array(pose["forearm." + s].tail))
            gap = max(gap, (pose["shin." + s].head - pose["thigh." + s].tail).length,
                      (pose["foot." + s].head - pose["shin." + s].tail).length)
        root_dev = max(root_dev, pose["root"].head.length)
        for pb in pose:
            stretch = max(stretch, abs(pb.length - pb.bone.length) / pb.bone.length)
        C = mesh_coords()
        if C[:, 2].min() < lo_[2]:
            iz = int(C[:, 2].argmin())
            info["lowest_vertex"] = {"z_mm": round(float(C[iz, 2]) * 1000, 2), "frame": f, "part": str(vlabel[iz]),
                                     "vertex": iz, "co": C[iz].round(4).tolist(), "rest_co": VL[iz].round(4).tolist(),
                                     "bone_matrix": [list(map(lambda x: round(x, 4), r)) for r in pose[str(vlabel[iz])].matrix]
                                     if str(vlabel[iz]) in pose else None}
        lo_ = np.minimum(lo_, C.min(0)); hi_ = np.maximum(hi_, C.max(0))
        tmin = min(tmin, float(C[:ntv, 2].min()))
        for s in "LR":
            sole_z[s].append(C[sole[s], 2])
        if f == 1:
            frame_cache[(name, "first")] = C
        last = C
    frame_cache[(name, "last")] = last
    if kind == "loop":
        info["seam_residual_mm"] = round(float(np.linalg.norm(frame_cache[(name, "first")] - last, axis=1).max()) * 1000, 4)
    info["root_max_offset_mm"] = round(root_dev * 1000, 4)
    info["bone_rigid_stretch_max_pct"] = round(stretch * 100, 5)
    info["joint_gap_max_mm"] = round(gap * 1000, 4)
    info["trunk_min_z_mm"] = round(tmin * 1000, 2)
    info["extent"] = {"footprint": round(float(max(hi_[0] - lo_[0], hi_[1] - lo_[1])), 4),
                      "width": round(float(hi_[0] - lo_[0]), 4), "depth": round(float(hi_[1] - lo_[1]), 4),
                      "height": round(float(hi_[2] - lo_[2]), 4), "top_z": round(float(hi_[2]), 4), "min_z": round(float(lo_[2]), 4)}
    belt = info.get("belt_m_per_frame", 0.0)
    slides = {}
    tracks = [("foot." + s, feet[s]) for s in "LR"]
    if name == "sitting_idle":
        tracks += [("forearm.%s:tail" % s, hands[s]) for s in "LR"]
    pad_contact = {}
    for lab_, Pt in tracks:
        Pt = np.array(Pt[:N] if kind == "loop" else Pt)
        zmin = Pt[:, 2].min()
        flags = list(Pt[:, 2] <= zmin + 0.0005)
        worst, nruns = 0.0, 0
        for run in runs_cyclic(flags, kind == "loop"):
            if len(run) < 2:
                continue
            nruns += 1
            q = [Pt[i, :2] - np.array([0.0, belt * k]) for k, i in enumerate(run)]
            worst = max(worst, max(np.linalg.norm(a_ - b_) for a_ in q for b_ in q))
        slides[lab_] = {"stance_runs": nruns, "stance_frames": int(sum(flags)), "worst_drift_mm": round(worst * 1000, 4),
                        "contact_z_mm": round(float(zmin) * 1000, 2)}
        if lab_.startswith("foot."):
            s = lab_[-1]
            zz = [sole_z[s][i] for i in range(len(flags)) if flags[i]]
            pad_contact[s] = {"sole_z_min_mm": round(float(min(z.min() for z in zz)) * 1000, 3),
                              "sole_z_max_mm": round(float(max(z.max() for z in zz)) * 1000, 3)}
    info["contact_slide"] = slides
    info["pad_sole_during_contact"] = pad_contact


def join(a_, b_):
    return round(float(np.linalg.norm(frame_cache[a_] - frame_cache[b_], axis=1).max()) * 1000, 4)


clips["stand_up"]["join_mm"] = {"first_vs_sitting_idle_f1": join(("stand_up", "first"), ("sitting_idle", "first")),
                                "last_vs_idle_f1": join(("stand_up", "last"), ("idle", "first"))}
clips["sit_down"]["join_mm"] = {"first_vs_idle_f1": join(("sit_down", "first"), ("idle", "first")),
                                "last_vs_sitting_idle_f1": join(("sit_down", "last"), ("sitting_idle", "first"))}

try:
    from forge.tools import rigcheck
    for name, kind, N, fn in CLIPS:
        feet_ = ["foot.L", "foot.R"]
        mode = "in_place" if name == "walk" else "planted"
        if name == "sitting_idle":
            feet_ += ["forearm.L:tail", "forearm.R:tail"]
        res = rigcheck.cmd_animation_check({"rig": rig.name, "action": name, "mode": mode, "feet": feet_})
        seam = res.get("loop_seam_closure") or {}
        clips[name]["forge_animation_check"] = {
            "mode": res.get("mode"), "gate": res.get("gate"), "deformation_gate": res.get("deformation_gate"),
            "feet": [{"bone": f_["bone"], "steps": f_["steps_measured"], "worst_drift_mm": f_["worst_drift_mm"],
                      "verdict": f_["verdict"]} for f_ in res.get("feet", [])],
            "loop_seam_closure": {k: seam.get(k) for k in ("verdict", "says") if k in seam},
            "says": res.get("says")}
except Exception:
    import traceback
    report["forge_animation_check_error"] = traceback.format_exc()[-1500:]

# ---- standing balance: COM (trunk + shells, rest) vs the support polygon of the two soles
def hull2(pts):
    pts = sorted(set(map(tuple, np.round(pts, 6))))
    if len(pts) < 3:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


vt = [(VOL, COM + T_REST)]
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_LO[s][part]
        a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        vv = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
        vt.append((float(vv.sum()), (vv[:, None] * (a + b + c) / 4.0).sum(0) / vv.sum() + S_RC))
Mtot = sum(v for v, _ in vt)
COM_S = sum(v * c for v, c in vt) / Mtot
soles = VL[sole["L"] | sole["R"]][:, :2]
hl = hull2(soles)                                        # CCW (monotone chain)
sd = []
for i in range(len(hl)):
    a, b = hl[i], hl[(i + 1) % len(hl)]
    e = b - a
    sd.append(float((e[0] * (COM_S[1] - a[1]) - e[1] * (COM_S[0] - a[0])) / np.linalg.norm(e)))   # >0 = inside side
inside = min(sd) >= 0
dmin = min(abs(x) for x in sd)
per_foot = {s: VL[sole[s]][:, :2].mean(0).round(4).tolist() for s in "LR"}
report["balance"] = {"com_rest_xyz": COM_S.round(4).tolist(), "sole_centres_xy": per_foot,
                     "com_y_minus_mean_sole_y_m": round(float(COM_S[1] - np.mean([per_foot[s][1] for s in "LR"])), 4),
                     "com_inside_support_polygon": bool(inside), "com_margin_to_support_edge_m": round(dmin, 4),
                     "support_polygon_extent_m": {"x": round(float(np.ptp(hl[:, 0])), 3), "y": round(float(np.ptp(hl[:, 1])), 3)},
                     "rule": "uniform density over trunk (closed) + leg shells, rest pose; support = convex hull of both flat soles"}

# ---- finish
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
bpy.context.view_layer.update()
Crest = mesh_coords()
H_STAND = float(Crest[:, 2].max() - Crest[:, 2].min())
report["heights"] = {"seated_m": round(H_SEAT, 4), "standing_m": round(H_STAND, 4), "ratio": round(H_STAND / H_SEAT, 4),
                     "sitting_pose_m": report["sit_pose"]["seated_height_m"],
                     "v1_rejected_standing_m": 3.1608, "v1_ratio": round(3.1608 / H_SEAT, 4)}
report["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                    "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]}
                   for b in arm_data.bones]
report["bone_count"] = len(arm_data.bones)
report["leg_chain_lengths_m"] = {s: {"thigh": round(LL1[s], 4), "shin": round(LL2[s], 4)} for s in "LR"}
report["clips"] = clips
rig["conquest_rig"] = "archetype-minimal v2 standing-rest, full leg extension (eldroot)"
low["conquest_clips"] = [c[0] for c in CLIPS]
low["conquest_state_clips"] = ["sitting_idle", "stand_up", "sit_down"]
low["conquest_rest_stance"] = "standing (full extension)"
low["conquest_max_height"] = 5.6
low["conquest_tri_budget"] = [20000, 30000]
for k in ("conquest_front_anchor", "conquest_front_landmark"):
    if k in low.keys():
        low[k] = (np.array(low[k]) + T_REST).tolist()
report["mesh_final"] = {"verts": int(len(VL)), "tris": int(len(FL)), "coords_sha": sha(VL), "faces_sha": sha(FL),
                        "bbox": [Crest.min(0).round(4).tolist(), Crest.max(0).round(4).tolist()]}
report["seconds"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_BLEND), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
json.dump(report, open(OUT_JSON, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("STAND2_DONE", json.dumps({"heights": report["heights"], "seconds": report["seconds"]}))
sys.stdout.flush()
