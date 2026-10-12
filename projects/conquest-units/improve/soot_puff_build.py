"""Soot Puff -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/soot_puff_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x4: Soot Puff, Winged Cat, Phoenix Hatchling, Chest Monster" (section
SOOT PUFF) + the sheet design/reference/soot_puff-character-sheet.webp (intake: design/reference/soot_puff/intake.json).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. All three views are TRUE orthos (axes pinned to the body centre):
  FRONT view (the unit's left on image right):   X = (x_px - 229) / 100    ; z = (662 - y_px) / 100
  SIDE view (the face on image RIGHT):           -Y = (x_px - 1009.5) / 100 ; z = (667 - y_px) / 100
  BACK view:                                      X = -(x_px - 639) / 100   ; z = (659 - y_px) / 100
Floor z = 0 at the ARM TIPS (the sheet's floor line; it FLOATS: the drawn shadow sits ~0.6 under them). X +ve = the
unit's LEFT (L). The views disagree in places (a painterly sheet): the side view draws the whole body ~0.05 higher than
the front (top 4.11 vs 4.06) and draws the eyes / mouth ~0.3-0.45 INSIDE its silhouette (it would need them at lateral
|X| 1.35 / 0.93, the front puts them at 0.41 / 0.0): the FACE follows the front view; each constant says which view.

Pipeline:
  1. BODY = ONE SDF: the base ellipsoid ball + two dangling round-cone ARMS (smooth union, a soft fillet under the ball)
     + the FUZZ: seeded overlapping rounded puffs scattered on the ball (two sizes) and the arms, each smooth-unioned in
     (the sheet's irregular, scalloped, painterly rim). Marching tets = the HIGH (bake source: the small puffs live
     mostly in the normal map); collapse decimation = the LOW shell (keeps the big puffs: a lumpy, not smooth, outline).
  2. REGIONS on the low shell, every boundary iso-cut: the FACE (two eye ovals + the mouth oval, front-projected exactly
     as the front view draws them; the puffs keep clear of them so they sit on smooth ground), the FUZZ crowns (puff
     crests standing > FUZZ_TAU proud of the base), the DUST highlights (the crowns of a seeded few upper puffs).
  3. no separate parts: every piece is the one closed shell (0 open edges).
  4. palette (palettes.py regions -> Col / Glow, no glow), matte material, Smart UV, bake normal + AO from the SDF high.
  5. RIG: root > body > arm_L.001 > arm_L.002, body > arm_R.001 > arm_R.002 (rigkit.vine_weights along each arm, the
     fillet blends from the body). Clips PLACEHOLDER (movement not given by the artist): idle = slow hover bob
     (+0..IDLE_LIFT) + arm sway; walk = in-place hover-drift lean. Rest pose: arm tips on the floor (z 0). glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K            # noqa: E402  (read-only use)
import palettes as PAL        # noqa: E402  (read-only use)
import magmoo_sdf as SD       # noqa: E402  (read-only use: the SDF kit)
import soot_puff_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "soot_puff"
CHAR_ID = "soot_puff"                 # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high; the small puffs are ~3-5 steps)
SDF_BAND = 0.16                       # BAND >= max smooth-union k (ARM_K 0.12) + 2 steps (contract hazard note)
BODY_TRIS = 10500                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- the BALL (base surface; the fuzz puffs stand FUZZ h proud of it, so the drawn outline = base + ~0.06).
#   FRONT outline half-widths (row-scan, both sides averaged): z 3.88 0.63 / 3.52 1.25 / 3.10 1.67 / 2.50 1.85 /
#   2.10 1.93 (widest, y 454) / 1.42 1.78 / 1.12 1.59 / 0.46 0.82; top y 256 (z 4.06, one tuft; the cap is z ~4.0),
#   bottom y 640 (z 0.22). SIDE outline: widest +-1.53 at y 448 (z 2.19); top 4.11, bottom ~0.40 (drawn 0.05 higher).
BALL_C = (0.0, 0.0, 2.15)             # ball centre (front view widest row z 2.10 / side 2.19)
BALL_RXY = (1.845, 1.455)              # "body width / depth" (base half-widths: front 1.93 - puff, side 1.53 - puff)
BALL_RZ = (1.815, 1.845)              # "body height": top / bottom base half-heights (top cap ~4.0, bottom ~0.23)
# ---- ARMS: round cones hanging under the ball, smooth-unioned into it. FRONT view: arm centres |X| 1.22 at z 0.6
#   (x 90-133 / 319-362 px... averaged L/R: the drawing sits ~0.1 left), 1.10 at z 0.1, tips z 0 at |X| ~1.08; arm width
#   0.43-0.47 (z 0.3-0.6), 0.31-0.35 at z 0.1. SIDE view: the one drawn arm centre -Y 0.89 at z 0.57 (x 1080-1116),
#   0.85 at z 0.33, 0.78 at z 0.15, tip 0.765 (FORWARD of the body centre; both arms overlap there, see deviation b).
ARM_TOP = (1.30, -0.98, 1.30)         # "arm root" (inside the ball: the fillet hides it) -- L side, mirrored
ARM_TIP = (1.09, -0.76, 0.20)         # "arm tip" (centre of the rounded tip; its lowest point is the floor)
ARM_R = (0.190, 0.160)                # "arm thickness": root / tip radius ("tapering slightly")
ARM_K = 0.12                          # "arm fillet": smooth-union k where the arm leaves the ball
# ---- FUZZ (the sheet's "soft, fuzzy, irregular, painterly edge ... slightly airy"): seeded rounded puffs. The rim
#   scallops in the sheet's FUZZY EDGE panel are 10-25 px wide (0.10-0.25) and stand 3-8 px (0.03-0.08) proud.
PUFF_SEED = 7                         # "fuzz pattern" seed (any integer re-rolls the puffs; deterministic)
PUFF_BIG = (480, (0.12, 0.22), (0.05, 0.10))   # "big puffs": count, radius range, protrusion range (the silhouette)
PUFF_SMALL = (1300, (0.06, 0.11), (0.015, 0.035))  # "small puffs": the painterly texture (mostly normal map)
PUFF_ARM = (60, (0.07, 0.12), (0.03, 0.05))       # "arm fuzz" per arm
PUFF_K = 0.06                         # "puff blend": smooth-union k of every puff (soft, never a pinecone of spheres)
FACE_MARGIN = 0.05                    # puffs keep this clear of the eye / mouth ovals (the face sits on smooth ground)
# ---- FACE (FRONT view, white blobs measured with PIL): eyes centres (182.2, 418.5) / (264.4, 414.9) px -> X -0.468 /
#   +0.354, z 2.435 / 2.471, each 0.33 x 0.44; mouth (233.5, 472.7) -> X +0.045, z 1.893, 0.24 x 0.28. Built SYMMETRIC
#   about the ball axis (eyes +-0.411 = half their spacing; the mouth on the centreline = deviation a: drawn 0.10 right
#   of the eye midline -0.057). Side view: eye z 2.525, mouth 1.975 (the side is drawn ~0.05-0.08 higher).
EYE_X, EYE_Z = 0.411, 2.453           # "eye placement" (mirrored)
EYE_AB = (0.165, 0.220)               # "eye size": half-width, half-height (front-projected oval)
MOUTH_Z = 1.893                       # "mouth placement" (centreline)
MOUTH_AB = (0.120, 0.140)             # "mouth size": half-width, half-height
FACE_SUBDIV = 2                       # "face outline detail": local subdivision rounds round the eyes + mouth
FACE_ZONE = 0.10                      # the densified zone reaches this far outside each oval
# ---- REGIONS: palette chips body 0,0,0 / fuzz 15,15,15 / dusty 113,113,115 / face 255,255,255
FUZZ_TAU = 0.012                      # "fuzz crowns": a puff standing this far proud of the base takes the fuzz tone; the
                                      #   creases between puffs stay body black (the FUZZY EDGE panel's outlined puffs; 0.03
                                      #   left isolated crowns that read as spots)
DUST_P = 0.0                          # "dust": this share of the big puffs on the upper half carry a dusty crown
DUST_NZ = 0.30                        # ... only puffs whose normal points up at least this much (sheet: lit from above)
DUST_TAU = 0.055                      # ... and only their top crown (protrusion above this)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
SPECULAR = 0.0                        # "sheen": Principled specular level (0 = the sheet's matte, dusty, no-sheen soot)
# ---- clips (PLACEHOLDER: the artist has not given Soot Puff's movement; brief: idle = slow hover bob + arm sway,
#   walk = hover-drift lean). 24 fps; integer harmonics per loop -> frame N+1 == frame 1. The rest pose stays on the
#   floor (contract feet_origin, arm tips z 0); the clips lift.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_LIFT = (0.03, 0.30)              # "hover bob": the body rises between these (u) and back, IDLE_BOB_CYC times per loop
                                      #   (brief: +0..0.3; the 0.03 floor keeps the swaying arm tips off the floor)
IDLE_BOB_CYC = 2                      #   (0.5 Hz: slow)
IDLE_PITCH, IDLE_ROLL = 1.5, 1.2      # body wander deg (1 cyc)
ARM_IDLE_X = (3.0, 6.0, 2, 0.8)       # "arm sway" fore-aft (root deg, tip deg, cyc, lag): lags the bob (drag)
ARM_IDLE_Y = (2.0, 4.0, 1, 0.7)       # arm sway sideways (1 cyc), L / R a third of a cycle apart
WALK_N = 48                           # "drift loop": 2 s
WALK_LIFT, WALK_BOB = 0.15, 0.03      # mean lift + bob (2 cyc)
WALK_PITCH, WALK_PITCH_OSC, WALK_ROLL = 8.0, 1.0, 1.5    # "lean into the drift": nose-down deg, wobble, roll
ARM_WALK_TRAIL = (10.0, 6.0)          # "arms trail back" deg (root, tip; sums down the chain)
ARM_WALK_FLUTTER = (2.0, 4.0, 4, 0.9)  # trailing flutter (root deg, tip deg, cyc, lag)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": "none: from-scratch unit (sheet "
          "design/reference/soot_puff-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; three true orthos; floor z = 0 at the arm tips (it floats)",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
SIDES = (("L", 1.0), ("R", -1.0))


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def mesh_arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], [list(map(int, f)) for f in F])
    me.update()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    return ob


def evaluated_arrays(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me_l = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    V, F = mesh_arrays(me_l)
    bpy.data.meshes.remove(me_l)
    return V, F


def keep_islands(V, F, min_frac):
    n = len(V)
    par = np.arange(n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for f in F:
        r0 = find(f[0])
        for v in f[1:]:
            r1 = find(v)
            if r1 != r0:
                par[r1] = r0
    roots = np.array([find(i) for i in range(n)])
    _, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_v = (cnt >= min_frac * n)[inv]
    remap = -np.ones(n, dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "dropped_verts": int((~keep_v).sum())}


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body SDF: ball + arms + fuzz puffs
t_sdf = time.time()
BC = np.array(BALL_C, float)
ARMS = {}
for s, sg in SIDES:
    a = np.array(ARM_TOP, float) * np.array([sg, 1.0, 1.0])
    b = np.array(ARM_TIP, float) * np.array([sg, 1.0, 1.0])
    ARMS[s] = {"a": a, "b": b, "ax": unit(b - a), "L": float(np.linalg.norm(b - a))}


def sd_ball(P):
    """IQ's bound-correct ellipsoid with a top / bottom half-height (bombee's ball)"""
    Q = np.atleast_2d(P) - BC
    r = np.stack([np.full(len(Q), BALL_RXY[0]), np.full(len(Q), BALL_RXY[1]),
                  np.where(Q[:, 2] > 0.0, BALL_RZ[0], BALL_RZ[1])], 1)
    k0 = np.linalg.norm(Q / r, axis=1)
    k1 = np.linalg.norm(Q / (r * r), axis=1)
    return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -min(BALL_RXY + BALL_RZ))


def sd_arm(P, s):
    A = ARMS[s]
    return SD.sd_round_cone(np.atleast_2d(P), A["a"], A["b"], ARM_R[0], ARM_R[1])


def sd_arms(P):
    return np.minimum(sd_arm(P, "L"), sd_arm(P, "R"))


def sd_base(P):
    """the un-fuzzed body: ball smooth-union arms (the surface the puffs stand proud of)"""
    return SD.smin(sd_ball(P), sd_arms(P), ARM_K)


# ---- fuzz puffs (seeded): two sizes on the ball, one on each arm; none on the face ovals
def face_ovals():
    return [("eye.L", EYE_X, EYE_Z) + tuple(EYE_AB), ("eye.R", -EYE_X, EYE_Z) + tuple(EYE_AB),
            ("mouth", 0.0, MOUTH_Z) + tuple(MOUTH_AB)]


def clear_of_face(pf):
    """keep a puff unless its footprint (cap radius a = sqrt(2 r h)) reaches a face oval grown by FACE_MARGIN; only
    front-facing puffs can (the ovals are front-projected)"""
    a_cap = np.sqrt(2.0 * pf["R"] * pf["H"])
    keep = np.ones(len(pf["R"]), bool)
    front = pf["N"][:, 1] < -0.3
    for _, cx, cz, ea, eb in face_ovals():
        q = ((pf["S"][:, 0] - cx) / (ea + a_cap + FACE_MARGIN)) ** 2 + ((pf["S"][:, 2] - cz) / (eb + a_cap + FACE_MARGIN)) ** 2
        keep &= ~(front & (q < 1.0))
    return keep


def not_under_arm(pf):
    """ball puffs whose centre falls inside an arm would only thicken the arm: drop them"""
    return sd_arms(pf["C"]) > 0.0


PUFFS = {}
_pb = FP.ball_puffs(BC, BALL_RXY, BALL_RZ[0], BALL_RZ[1], PUFF_BIG[0], PUFF_BIG[1], PUFF_BIG[2], PUFF_SEED)
_ps = FP.ball_puffs(BC, BALL_RXY, BALL_RZ[0], BALL_RZ[1], PUFF_SMALL[0], PUFF_SMALL[1], PUFF_SMALL[2], PUFF_SEED + 101)
for nm, pf in (("big", _pb), ("small", _ps)):
    k_ = clear_of_face(pf) & not_under_arm(pf)
    PUFFS[nm] = {key: v[k_] for key, v in pf.items()}
for s, sg in SIDES:
    A = ARMS[s]
    # the arm's visible length starts where it leaves the ball: puffs from 45 % down the axis to the rounded tip
    pf = FP.arm_puffs(A["a"], A["b"], ARM_R[0], ARM_R[1], PUFF_ARM[0], PUFF_ARM[1], PUFF_ARM[2],
                      PUFF_SEED + (201 if s == "L" else 301), s_rng=(0.45, 1.10))
    PUFFS["arm." + s] = pf
# dust: a seeded share of the big puffs on the upper half (their crowns only)
_dr = np.random.default_rng(PUFF_SEED + 401).uniform(0.0, 1.0, len(PUFFS["big"]["R"]))
DUST_MASK = (PUFFS["big"]["N"][:, 2] > DUST_NZ) & (_dr < DUST_P)
PC = np.vstack([PUFFS[k]["C"] for k in PUFFS])
PR = np.concatenate([PUFFS[k]["R"] for k in PUFFS])
report["fuzz"] = {"seed": PUFF_SEED, "puffs": {k: int(len(v["R"])) for k, v in PUFFS.items()},
                  "dropped_face_or_arm": {"big": int(PUFF_BIG[0] - len(PUFFS["big"]["R"])),
                                          "small": int(PUFF_SMALL[0] - len(PUFFS["small"]["R"]))},
                  "dust_puffs": int(DUST_MASK.sum()), "k": PUFF_K}

_lo = BC - np.array([BALL_RXY[0], BALL_RXY[1], BALL_RZ[1]]) - 0.12
_hi = BC + np.array([BALL_RXY[0], BALL_RXY[1], BALL_RZ[0]]) + 0.12
_alo = np.min([np.minimum(A["a"], A["b"]) for A in ARMS.values()], 0) - ARM_R[0] - 0.06
_lo = np.minimum(_lo, _alo)
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
g.apply(sd_ball, _lo, _hi, 0.0)
for s in ("L", "R"):
    A = ARMS[s]
    g.apply(lambda P, s=s: sd_arm(P, s), np.minimum(A["a"], A["b"]) - ARM_R[0], np.maximum(A["a"], A["b"]) + ARM_R[0], ARM_K)
for k_ in PUFFS:
    for c_, r_ in zip(PUFFS[k_]["C"], PUFFS[k_]["R"]):
        g.apply(lambda P, c_=c_, r_=r_: SD.sd_sphere(P, c_, r_), c_ - r_, c_ + r_, PUFF_K)
t_poly = time.time()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "seconds_fields": round(t_poly - t_sdf, 1),
                 "seconds": round(time.time() - t_sdf, 1)}
del g
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(),
                    "seconds": round(time.time() - t, 1)}
assert np.all(_bb_l / _bb_h > 0.99), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))

# ---- face densify: the decimated shell's ~0.08 edges would cut the 0.24-wide mouth into a pentagon. Subdivide the
#      faces round each face oval FACE_SUBDIV times, then pull every new vertex onto the HIGH (the face zone is the
#      puff-free smooth ball, so the ovals land on smooth ground with ~4x finer outlines).
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
for _ in range(FACE_SUBDIV):
    def _in_zone(co):
        return co[1] < 0.0 and any(FP.ellipse_front(np.array([co]), cx, cz, ea + FACE_ZONE, eb + FACE_ZONE)[0] > 0.0
                                   for _, cx, cz, ea, eb in face_ovals())
    zf = [f for f in bm.faces if any(_in_zone(v.co) for v in f.verts)]
    ze = list({e for f in zf for e in f.edges})
    bmesh.ops.subdivide_edges(bm, edges=ze, cuts=1, use_grid_fill=True)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.ensure_lookup_table()
for v in bm.verts[_nv0:]:
    v.co = bvh_hi.find_nearest(v.co)[0]
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["retopo"]["face_densify"] = {"rounds": FACE_SUBDIV, "zone": FACE_ZONE, "new_verts": len(LV) - _nv0,
                                    "tris_after": tri_count_F(LF)}
print("RETOPO", json.dumps(report["retopo"]))

# =========================================================================== 3. region fields + iso cuts on the shell
_dust_C = PUFFS["big"]["C"][DUST_MASK]; _dust_R = PUFFS["big"]["R"][DUST_MASK]


def gated_oval(P, cx, cz, ea, eb):
    f = FP.ellipse_front(P, cx, cz, ea, eb)
    return np.where(P[:, 1] < 0.0, f, -1.0)          # front surface only (the ovals are front-projected)


_db = sd_base(LV)
FIELDS = {"fuzz": _db - FP.sd_spheres_min(LV, PC, PR),
          # no dust puffs (DUST_P 0) = no vertex is ever on a dust crown: the field is a constant -1 (outside), never
          # _db minus a sentinel (that read _db + 1 > DUST_TAU and painted every fuzz crown dusty)
          "dust": (_db - FP.sd_spheres_min(LV, _dust_C, _dust_R)) if len(_dust_R) else np.full(len(LV), -1.0)}
for nm, cx, cz, ea, eb in face_ovals():
    FIELDS[nm] = gated_oval(LV, cx, cz, ea, eb)
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None):
    L = LAY[key]
    eps = 1e-5 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < CUT_SNAP:
                a[L] = tau
            elif tt > 1 - CUT_SNAP:
                b[L] = tau
    cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
    pairs = []
    for f in bm.faces:
        vs = list(f.verts)
        sides = [side(v) for v in vs]
        if not (1 in sides and -1 in sides):
            continue
        cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
        if len(cv) == 2:
            pairs.append(cv)
    for cv in pairs:
        bmesh.ops.connect_verts(bm, verts=cv)
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts), "face_connects": len(pairs)}


TOL = 0.01
off_face = lambda a, b: all(a[LAY[nm]] < TOL and b[LAY[nm]] < TOL for nm, *_ in face_ovals())
in_fuzz = lambda a, b: a[LAY["fuzz"]] > FUZZ_TAU - TOL and b[LAY["fuzz"]] > FUZZ_TAU - TOL
CUTS = [(nm, 0.0, None) for nm, *_ in face_ovals()] + [("fuzz", FUZZ_TAU, off_face), ("dust", DUST_TAU, in_fuzz)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
shell_reg = np.full(len(CF), "body", dtype=object)
shell_reg[FVAL["fuzz"] > FUZZ_TAU] = "fuzz"
shell_reg[(FVAL["fuzz"] > FUZZ_TAU) & (FVAL["dust"] > DUST_TAU)] = "dust"
for nm, *_ in face_ovals():
    shell_reg[FVAL[nm] > 0.0] = "face"
assert len(_dust_R) > 0 or not np.any(shell_reg == "dust"), "dust faces without dust puffs"
assert np.any(shell_reg == "fuzz") and np.any(shell_reg == "body") and np.any(shell_reg == "face"), "an expected region is empty"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 4. assemble + centre (one closed shell)
ISL = [("shell", CV, CF, list(shell_reg), None)]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, RANGE = [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    nv_ += len(V)
VALL = np.vstack(VALL)
HV_s = HV - SHIFT
REG = ["body", "fuzz", "dust", "face"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.97 + 0.06 * j


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                      # opaque closed shell: single-sided (glTF doubleSided false)
    bsdf.inputs["Specular IOR Level"].default_value = SPECULAR   # "matte, dusty, no sheen" (sheet MATERIAL panel)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
_lit = sorted(n for n, v in pal_default["regions"].items() if "emission" in v)
report["glow"] = {"glowing_regions": _lit, "rule": "no glow: the sheet draws none (Glow attribute all zero)"}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = jitter(VALL, FALL)
shade[rid == REG.index("face")] = 1.0                # the face ovals stay clean (no value jitter)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the ball centre -> the mouth's front point on the midline
_mouth_front = np.array([0.0, -1.0, 0.0])
_mp, _ = FP.ellipsoid_hit(BC, BALL_RXY, BALL_RZ[0], BALL_RZ[1], np.array([[0.0, -1.0, (MOUTH_Z - BC[2]) / BALL_RXY[1]]]))
anchor = BC - SHIFT
landmark = np.array([0.0, _mp[0, 1], BC[2]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "ball centre -> the face's front point on the midline (eyes + mouth face -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = {nm: {"centre_xz": [cx, cz], "half_wh": [ea, eb], "rule": "front-projected oval iso-cut into the shell"}
                  for nm, cx, cz, ea, eb in face_ovals()}
report["arms"] = {s: {"top": (A["a"] - SHIFT).round(4).tolist(), "tip_centre": (A["b"] - SHIFT).round(4).tolist(),
                      "radius": list(ARM_R), "length": round(A["L"], 4)} for s, A in ARMS.items()}

# ---- flat + UV
low.data.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

tris_main = tri_count_F(FALL)
_reg_tris = {n: int((rid == j).sum()) for j, n in enumerate(REG)}
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF),
                  "per_region": _reg_tris, "per_part": {"shell (ball + arms + fuzz, one SDF)": tri_count_F(CF)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. One shell carries everything: the big fuzz "
                            "puffs need ~12k tris to stay lumpy in the LOW silhouette; the small puffs ride the normal "
                            "map; the colour cuts (face ovals, fuzz crowns, dust) add the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "one closed SDF shell (ball + arms + puffs); the face is iso-cut into it (no decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "sheet": {"front": "H 4.09 W 3.90", "side": "H 4.14 D 3.11", "back": "H 4.04 W 3.74"}},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "regular"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch: design/reference/soot_puff-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's detail panels (face / fuzzy edge / arms)
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.95, -1.9, 1.45]) - SHIFT).tolist(), (np.array([0.95, -0.9, 2.95]) - SHIFT).tolist()],
    "fuzz_edge": [(np.array([-1.95, -0.6, 2.6]) - SHIFT).tolist(), (np.array([-0.3, 0.6, 4.1]) - SHIFT).tolist()],
    "arms": [(np.array([-1.6, -1.3, -0.05]) - SHIFT).tolist(), (np.array([1.6, -0.3, 1.25]) - SHIFT).tolist()],
    "views": {"face": [0.0, 4.0], "fuzz_edge": [-20.0, 10.0], "arms": [0.0, 2.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "fuzz", "arms")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake normal + AO (high = the SDF shell)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
HIGH = new_obj(UNIT + "_high", HV_s, HF)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.cycles.seed = 0
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
nt_ = MAT_BODY.node_tree
tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
nf = len(me.polygons)
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
ls_ = np.concatenate([[0], np.cumsum(lt_)[:-1]])
UVl = UVn.reshape(-1, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for fi in np.nonzero(mask_faces)[0]:
        poly = UVl[ls_[fi]:ls_[fi] + lt_[fi]] * res
        for j in range(1, len(poly) - 1):
            p = np.array([poly[0], poly[j], poly[j + 1]])
            x0_, y0_ = np.floor(p.min(0)).astype(int); x1_, y1_ = np.ceil(p.max(0)).astype(int)
            xs, ys = np.meshgrid(np.arange(max(x0_, 0), min(x1_, res)), np.arange(max(y0_, 0), min(y1_, res)))
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


tb = time.time()
for o in scene.objects:
    o.select_set(o is HIGH or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt_.nodes.active = node
    scene.cycles.samples = samples
    t = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
me.shade_flat()
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
body_faces = np.ones(nf, bool)
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(HF), "high_rule": "the SDF shell (marching tets: ball + arms + every puff)",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_normal_twin.npy" if DIGEST_ONLY else "_normal_main.npy")), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_ao_twin.npy" if DIGEST_ONLY else "_ao_main.npy")), pa[:, :1])
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k != "pixel_sha"}))
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
# wire the textures into the body material
nt = MAT_BODY.node_tree
bsdf = nt.nodes["Principled BSDF"]
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(nt.nodes["col"].outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGH.data
bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


def geometry_digest(obs):
    h = hashlib.sha256()
    for o in obs:
        me_ = o.data
        co = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", co)
        lv = np.empty(len(me_.loops), dtype=np.int64); me_.loops.foreach_get("vertex_index", lv)
        h.update(np.round(co, 6).astype(np.float32).tobytes()); h.update(lv.tobytes())
        for nm in ("Col", "Glow"):
            cd = np.empty(len(me_.loops) * 4, dtype=np.float32); me_.color_attributes[nm].data.foreach_get("color", cd)
            h.update(np.round(cd, 5).tobytes())
        uv = np.empty(len(me_.loops) * 2); me_.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


DIG["geometry_colour_uv"] = geometry_digest([low])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "pixel-sampled from the sheet's four palette chips (intake swatches) + drawn-surface "
                                   "samples (see the palette notes)"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


def arm_exit(A):
    """where the arm axis leaves the base ball (bisection along a -> b on sd_ball)"""
    lo_, hi_ = 0.0, A["L"]
    for _ in range(60):
        mid = 0.5 * (lo_ + hi_)
        if sd_ball(A["a"] + A["ax"] * mid)[0] < 0.0:
            lo_ = mid
        else:
            hi_ = mid
    return 0.5 * (lo_ + hi_)


ARM_BONES = 2
BONES = [("body", P_(BC), P_(BC + np.array([0.0, 0.0, 1.0])), "root")]
for s, _ in SIDES:
    A = ARMS[s]
    A["s_exit"] = arm_exit(A)
    A["s_end"] = A["L"] + ARM_R[1]                      # the rounded tip's lowest point along the axis
    pts = [A["a"] + A["ax"] * x for x in np.linspace(A["s_exit"], A["s_end"], ARM_BONES + 1)]
    for k in range(ARM_BONES):
        BONES.append(("arm_%s.%03d" % (s, k + 1), P_(pts[k]), P_(pts[k + 1]),
                      "body" if k == 0 else "arm_%s.%03d" % (s, k)))
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.4); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_); e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = nm.startswith("arm") and not nm.endswith(".001")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 3 influences): arm ownership = smoothstep over (ball distance - arm distance) at the rest shell
#      (the fillet blends), times rigkit.vine_weights arc-length hats along the arm from where it leaves the ball
ARM_OWN_W = 0.10                      # half-width of the ownership blend across the fillet
ARM_PARENT_S0 = -0.15                 # the body -> arm.001 blend starts this far above the exit point (along the axis)
Wt = np.zeros((len(VALL), len(DEFORM)))
Pw = VALL + SHIFT
d_ball_ = sd_ball(Pw)
Wt[:, J["body"]] = 1.0
for s, _ in SIDES:
    A = ARMS[s]
    own = smoothstep(-ARM_OWN_W, ARM_OWN_W, d_ball_ - sd_arm(Pw, s))
    own[sd_arm(Pw, s) > 0.6] = 0.0                     # far from this arm: never
    s_arc = (Pw - A["a"]) @ A["ax"] - A["s_exit"]
    Lc = A["s_end"] - A["s_exit"]
    Wv = K.vine_weights(np.clip(s_arc, ARM_PARENT_S0 - 1.0, Lc), Lc, ARM_BONES, parent_s0=ARM_PARENT_S0)
    Wt[:, J["body"]] -= own * (1.0 - Wv[:, 0])
    for k in range(ARM_BONES):
        Wt[:, J["arm_%s.%03d" % (s, k + 1)]] += own * Wv[:, k + 1]
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx_ = np.argsort(-Wt, 1)[:, 4:]
    np.put_along_axis(Wt, idx_, 0.0, 1)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "rule": "ball + face + fuzz 1.0 body; each arm owned by smoothstep(+-%.2f) of (ball distance - arm "
                          "distance), split along the arm by rigkit.vine_weights from its exit point out of the ball "
                          "(body -> arm.001 blend starts %.2f above it)" % (ARM_OWN_W, -ARM_PARENT_S0)}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def wave(u, k, spec, phase):
    a0, a1, cyc, lag = spec
    return K.vine_wave(u, k, ARM_BONES, a0, a1, lag, cyc, phase)


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
ARM_PHASE = {"L": 0.0, "R": 2.0 * math.pi / 3.0}     # L / R a third of a cycle apart (never a mirrored flap)


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        ph_b = TAU * IDLE_BOB_CYC * u
        w_ = 0.5 * (1.0 - math.cos(ph_b))                      # 0 at the bob bottom .. 1 at the top
        z = IDLE_LIFT[0] + (IDLE_LIFT[1] - IDLE_LIFT[0]) * w_
        pitch = IDLE_PITCH * math.sin(TAU * u + 0.5) * w_      # the wander fades out at the bottom (tips stay clear)
        roll = IDLE_ROLL * math.sin(TAU * u + 1.9) * w_
        P["body"] = ((0.0, 0.0, z), [("X", pitch), ("Y", roll)], None)
        for s, sg in SIDES:
            ph = ARM_PHASE[s]
            for k in range(ARM_BONES):
                bn = "arm_%s.%03d" % (s, k + 1)
                # drag: the fore-aft sway lags the bob by a quarter cycle; the sideways sway is slow, L / R offset
                rx = wave(u, k, ARM_IDLE_X, -math.pi / 2 + 0.3 * ph)
                ry = sg * wave(u, k, ARM_IDLE_Y, ph)
                P[bn] = (None, [("X", rx), ("Y", ry)], None)
        info = {"lift": z, "pitch": pitch, "roll": roll}
    else:
        z = WALK_LIFT + WALK_BOB * math.sin(TAU * 2 * u)
        pitch = WALK_PITCH + WALK_PITCH_OSC * math.sin(TAU * 2 * u - 1.0)
        roll = WALK_ROLL * math.sin(TAU * u + 0.6)
        P["body"] = ((0.0, 0.0, z), [("X", pitch), ("Y", roll)], None)
        for s, sg in SIDES:
            ph = ARM_PHASE[s]
            for k in range(ARM_BONES):
                bn = "arm_%s.%03d" % (s, k + 1)
                P[bn] = (None, [("X", ARM_WALK_TRAIL[k] + wave(u, k, ARM_WALK_FLUTTER, ph)),
                                ("Y", sg * 1.0 * math.sin(TAU * u + ph))], None)
        info = {"lift": z, "pitch": pitch, "roll": roll}
    return P, info


NEW_ACTS = {}
key_rows = []
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1
        P, _ = pose_at(cn, f % N)
        for bn in DEFORM:
            loc, rots, sc = P.get(bn, (None, [], None))
            pb = rig.pose.bones[bn]
            pb.location = local_loc(bn, loc) if loc is not None else Vector((0.0, 0.0, 0.0))
            pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
            pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)
            for ch in ("location", "rotation_quaternion", "scale"):
                pb.keyframe_insert(ch, frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion) + list(pb.scale))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["loop_seconds"] = N / K.FPS
    NEW_ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


BODY_REST = Matrix(arm_data.bones["body"].matrix_local)
ARMV = {s: np.nonzero(Wt[:, J["arm_%s.%03d" % (s, ARM_BONES)]] > 0.5)[0] for s, _ in SIDES}   # the lower arm verts
BALLV = np.nonzero(Wt[:, J["body"]] > 0.999)[0]
samples, clip_rep = [], {}
for cn, N in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    first = last = None
    rows = []
    arm_pen = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        by = pbs["body"].matrix.col[1].xyz
        Mb = np.array(BODY_REST @ pbs["body"].matrix.inverted())          # posed -> body rest frame
        if (f - 1) % 4 == 0:
            for s, _ in SIDES:                                               # lower arm entering the ball?
                Cw = C[ARMV[s]] @ Mb[:3, :3].T + Mb[:3, 3] + SHIFT
                arm_pen.append(float(sd_ball(Cw).min()))
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "pitch": math.degrees(math.atan2(-by.y, by.z)), "roll": math.degrees(math.atan2(by.x, by.z)),
                     "root": float((rig.matrix_world @ pbs["root"].head).length),
                     **{"tip_" + s: list(pbs["arm_%s.%03d" % (s, ARM_BONES)].tail) for s, _ in SIDES}})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - arm_data.bones["body"].head_local.z
    tips = {s: np.array([r["tip_" + s] for r in R_]) for s, _ in SIDES}
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 4),
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "pitch_deg": [round(float(g_("pitch").min()), 3), round(float(g_("pitch").max()), 3)],
                    "roll_deg": [round(float(g_("roll").min()), 3), round(float(g_("roll").max()), 3)],
                    "arm_tip_p2p_xyz": {s: (v.max(0) - v.min(0)).round(4).tolist() for s, v in tips.items()},
                    "arm_tips_z_LR_corr": round(float(np.corrcoef(tips["L"][:, 2], tips["R"][:, 2])[0, 1]), 4),
                    "lower_arm_min_sd_to_ball": round(min(arm_pen), 4)}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hover"] = {"rest_height_units": HH, "idle_lift_units": list(IDLE_LIFT), "walk_lift_units": WALK_LIFT,
                "sheet_shadow_gap_units": 0.6,
                "rule": "rest pose: arm tips on the floor (contract feet_origin, z 0); idle keys body.location z = "
                        "IDLE_LIFT[0] .. [1] (slow bob), walk WALK_LIFT + bob; the contract root never moves. The sheet's floor "
                        "shadow sits ~0.6 under the tips: the in-game hover height is the artist's open question"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "soot_puff v1: root > body > arm_L.001 > arm_L.002, body > arm_R.001 > arm_R.002 (vine weights)"
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Soot Puff's movement; open question): idle = "
                               "4 s slow hover bob (body +%.2f..%.2f u, 0.5 Hz) + lagging arm sway; walk = 2 s in-place "
                               "hover-drift (%.0f deg nose-down lean, arms trailing back)" % (IDLE_LIFT[0], IDLE_LIFT[1], WALK_PITCH))
low["conquest_locomotion"] = "hover (floats): rest pose arm tips z 0 per contract; the clips lift the body"
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris": report["tris"]["total"], "seconds": round(time.time() - T0, 1)},
              open(DIGEST_ONLY, "w"), indent=1)
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 7. glb
for o in scene.objects:
    o.select_set(o in (rig, low))
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, NEW_ACTS["idle"])
t = time.time()
import export_glb as _EG; _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False, export_attributes=True)
rig.animation_data.action = None


def glb_carries(path):
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    prims = [(m.get("name"), p) for m in js.get("meshes", []) for p in m["primitives"]]
    return {"materials": [{"name": m.get("name"), "alphaMode": m.get("alphaMode", "OPAQUE"),
                           "doubleSided": m.get("doubleSided", False),
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "attributes": sorted(p["attributes"])} for n, p in prims],
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'soot_puff' (one opaque body primitive); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default)},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("HOVER", json.dumps(rep["hover"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
