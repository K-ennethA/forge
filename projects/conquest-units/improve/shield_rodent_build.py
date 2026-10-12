"""Shield Rodent -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/shield_rodent_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x3: Shield Rodent, Storm Vessel, Volcano Turtle" (section SHIELD RODENT)
+ the sheet design/reference/shield_rodent-character-sheet.png (intake: design/reference/shield_rodent/intake.json).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. X +ve = the unit's LEFT, front = -Y, floor z = 0 at the soles.
  SIDE view (the height + length ortho; faces image-left):  Y = (x_px - 957.5) / 100 ; z_m = (655 - y_px) / 100
  FRONT view (widths): X = (x_px - 174) / 100  (174 = the HEAD axis: eye centres 134.5 / 210.5, ear tips 70 / 283, snout
               ball 170, front feet 86 / 272 -- the drawn shell is centred 11 px right of it at 185.5: built symmetric
               about the head axis)
ELEVATION CORRECTION (every view is drawn from slightly above): elevation e = 19 deg, measured off the SIDE view's boss
  collar ring (98 px wide x 27 px tall: asin(27/98) = 16 deg) and the dome-top ring (73 x 27: 22 deg). The side view's floor
  line (y 655) is the NEAR feet (X ~ +0.9); a midline point (X = 0) is drawn 0.9 sin e = 0.30 higher than its true height
  and vertical extents are scaled by cos e: z_true = (z_m - 0.30) / 0.95 for midline points, z_true = z_m / 0.95 for the
  near-side points. Every height constant below states which.

THE BODY PLAN as drawn: a flat round SHIELD-SHELL (hard surface) carried low over four tiny feet; a small round steel
rodent head poking out under the shell's front with LARGE rounded ears; a long pointed faceted metal TAIL rising back
and up out of the plate behind the boss.

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the BELLY under the shell, the NECK, the HEAD (an
     egg tapering forward-down to the snout), two LARGE ROUNDED EARS (flattened ellipsoids with a carved front cup), four
     short LEGS + tiny rounded FEET (floor-cut soles, toe creases carved). Marching tets = the HIGH; collapse decimation
     = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: the warm-taupe INNER EAR (the cup), the head's centre SEAM line.
  3. PARTS (shield_rodent_parts.py, closed): the SHIELD-SHELL -- dark brushed top PLATE (slightly domed lens; sheen wedges
     as whole angular segments), the light-steel RIM band (bevelled ring), RIM_RIVETS small domes, the raised centre BOSS
     (stepped collar + dome), the SKIRT pan underneath -- the SNOUT ball, two TEETH, the EYES + highlight dots (closed
     conforming decals), the faceted TAIL (a 4-facet root block + a 4-facet spike, tone per facet).
  4. palette (palettes.py regions -> Col / Glow, no glow), Smart UV, bake normal + AO from the SDF high + the parts, where
     the plate and rim bake sources carry the brushed grooves + light scratches (normal-map lines only), seed 0.
  5. RIG: root > body > head > ear_L / ear_R; body > {fore,hind}leg_{L,R}.leg > .foot (2 bones each); body > tail.001 >
     tail.002. Clips (PLACEHOLDER, movement + height open): idle (breathing + an ear twitch + a tail tick, feet planted)
     + walk (a 4-beat scurry in place, stance feet planted on the floor and carried back, swing feet lifted). glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                # noqa: E402  (read-only use)
import palettes as PAL            # noqa: E402  (read-only use)
import magmoo_sdf as SD           # noqa: E402  (read-only use: the SDF kit)
import shield_rodent_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "shield_rodent"
CHAR_ID = "shield_rodent"             # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.20                       # >= the largest smooth-union k (0.14) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 10000                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- SHIELD-SHELL. Disc: FRONT view rim x 28-343 (3.15 incl. the rivets), BACK view 388-687 (2.99), SIDE view rim
#   x 822-1107 (Y -1.36..+1.50: 2.86 long, centre Y +0.07) -> one ROUND disc, radius the mean 1.48 (the sheet: "round").
#   LEVEL: the SIDE view's rim ellipse looks tilted 14 deg (front end y 562-597, back end y 490-530), but that is the
#   drawing's yaw + elevation: the near rim's bottom edge sits at y 608 over the front leg (x 885) and y 600 over the hind
#   leg (x 1020) -- 3.4 deg, less than the drawn ground line's own 5.1 deg (near soles y 652 @ x 875 / 636 @ x 1055)
SHELL_C = (0.0, 0.07, 0.32)           # "shell height": centre of the rim's BOTTOM plane = just over the feet tops (0.27).
                                      #   The feet sit right under the rim band (r ~1.28 from the shell centre) and only PEEK
                                      #   OUT: FRONT view rim bottom edge y 590 over the left foot (x 60-110), the foot runs
                                      #   y 592 -> sole 630, no leg shows; SIDE view rim bottom y 608 over the near front
                                      #   foot (top y 620, sole 652) and y 600 over the near hind foot (top y 604, sole 636):
                                      #   gaps of 0.02-0.12 -> rim bottom = feet top + 0.05 (v1 0.55 read as stilts, gate)
SHELL_PITCH = 0.0                     # "shell tilt": back-up pitch, deg (0: level, see above)
RIM_R = (1.20, 1.48)                  # "rim band": inner / outer radius (FRONT view band width at the sides ~0.27)
RIM_H = 0.34                          # rim band height at its inner edge (SIDE view band y 562-597 at the front end = 0.35
                                      #   incl. the plate lip)
RIM_SLOPE = 0.06                      # "rim slope": the band's top falls this much from its inner to its outer edge (the
                                      #   sheet's shield rim: a sloped light band, never a vertical wall)
RIM_BEVEL = 0.03
PLATE_EDGE_H, PLATE_RISE = 0.32, 0.42  # "plate dome": the plate meets the rim 0.02 under its top and rises this much to
                                      #   the centre: a FULLER dome so the low shell reads as the drawn shallow dome over the
                                      #   body (plate centre z 1.04; SIDE view collar base corrected z 1.14)
PLATE_RINGS = 7                       # plate dome profile steps (low)
SHELL_SEG = 96                        # "shell roundness": angular segments of the rim / plate / skirt
PLATE_SHEEN = ((-35.0, 30.0), (145.0, 30.0))   # "brushed sheen": lighter wedges (azimuth from the front toward +X, half
                                      #   width), a bow-tie across the front-left / back-right diagonal (SIDE view: plate
                                      #   front 106,109,115 vs back 70,72,77; BACK view left 115,117,122 vs right 65,68,73)
BOSS = [(0.0, 0.50), (0.50, 0.50), (0.50, 0.77), (0.48, 0.79), (0.38, 0.79)]   # "centre boss": ONE collar step (r, h
                                      #   above the rim bottom): SIDE view collar 0.98 wide (x 902-1000), BACK view 1.05
                                      #   (x 485-590); v1's two steps read as a wedding cake (gate)
BOSS_DOME = (0.38, 0.33)              # "boss dome": base radius / height (SIDE view dome x 912-985 = 0.73 wide)
BOSS_SEG, BOSS_DOME_STEPS = 80, 12    # boss smoothness (the bake source is a dense smooth copy: the facets bake smooth)
RIVETS = 18                           # "rivets": count round the rim (SIDE view 8 on the near half, BACK view 10 on the
                                      #   visible arc -> 16-20 all round)
RIVET_R, RIVET_H = 0.055, 0.045       # rivet dome radius / height (SIDE view rivet ~11 px)
RIVET_RADIUS = 1.34                   # rivets sit on the rim top, mid band
SKIRT = [(0.0, -0.10), (0.80, -0.10), (1.02, -0.03), (1.22, 0.0), (1.22, 0.04), (0.0, 0.04)]   # "skirt": the short pan
                                      #   under the rim (SIDE view under-rim band y 600-612 near the front leg)
BRUSH = (0, 0.55, 1.18, 0.0012, 0.016, 7.0)    # "brushed lines" (normal map only): grooves (count, r range, depth,
                                      #   half width, seed) on the plate's bake source -- OFF (count 0): even shallow grooves read as rings (gate)
SCRATCH_PLATE, SCRATCH_RIM = 9, 7     # "light scratches" (normal map only): short straight grooves on plate / rim top
SCRATCH = (0.0025, 0.012, 0.18, 0.42)  # scratch depth, half width, length range
# ---- HEAD (one SDF): SIDE view head centre (790, 548) z_m 1.07, top y 480 (z_m 1.75), chin y 617 (z_m 0.38), front edge
#   x 732 (Y -2.26); FRONT view width x 98-251 (1.53), chin y 627. The two views disagree on the chin by 0.30 (side
#   corrected 0.08, front corrected (628 - 627 + 35) / 95 = 0.38: the head is the nearest thing to the front camera) ->
#   their mean: the side-corrected head is RAISED 0.15
HEAD_C, HEAD_R = (0.0, -1.62, 0.81), (0.75, 0.62, 0.68)   # "head size" (half width / depth / height)
SNOUT_CONE = ((0.0, -1.70, 0.75), (0.0, -2.04, 0.55), 0.52, 0.17)   # the egg's forward-down taper to the snout
HEAD_K = 0.12
NECK = ((0.0, -0.70, 0.36), (0.0, -1.40, 0.70), 0.30, 0.40)
NECK_K = 0.12
BELLY_C, BELLY_R = (0.0, 0.05, 0.30), (0.95, 1.00, 0.08)   # the body under the shell (mostly hidden by the skirt)
SEAM_W = 0.016                        # "centre seam": half width of the seam line (FRONT view x 172-174, crown -> forehead)
SEAM_Z0 = 1.03                        # the seam runs over the crown down to this height (FRONT view: stops above the eyes)
# ---- SNOUT + TEETH: SIDE view ball centre (735, 580) r ~13 px, FRONT view ball (170, 593) r ~17 px -> r 0.15; teeth
#   SIDE x 728-737 from y 592 to 635 (0.43 long), FRONT x 162-180 (two, 0.085 wide each)
SNOUT_C, SNOUT_R = (0.0, -2.17, 0.51), 0.15     # "snout ball"
TOOTH_X, TOOTH_W, TOOTH_D = 0.048, 0.042, 0.032  # "teeth": centre offset, half width, half depth
TOOTH_TOP, TOOTH_TIP = 0.47, 0.05     # tooth top (buried in the ball) / tip height (tips kept 0.05 off the floor)
TOOTH_Y = -2.20
# ---- EYES: FRONT view dark ovals x 124-145 / 199-222, y 535-568 (centres X -0.395 / +0.365 -> +-0.38, 0.22 x 0.33);
#   SIDE view eye (775, 550) z_m 1.05 -> 0.79 + 0.15 (head raise) = 0.94; highlight peak at (131, 542) / (206, 542):
#   (-0.04, +0.095) from each centre (BOTH eyes: the drawn light is from the image-left)
EYE_X, EYE_Z = 0.38, 0.81             # "eye placement" (mirrored); cast from the front onto the head
EYE_A, EYE_B = 0.11, 0.165            # "eye size": oval half width / half height
EYE_FACE = 0.35                       # the eye faces this share further toward the front than the head normal
GLINT_OFF, GLINT_R = (-0.04, 0.085), 0.034   # "eye highlight": offset (X, z) from the eye centre, radius
DECAL_UP, DECAL_DN, DECAL_DOME = 0.008, 0.03, 0.016   # decal top offset / sink / dome height
# ---- EARS: LARGE broad rounded ovals, inner warm taupe. FRONT view tips (70, 370) / (283, 370) (X -1.04 / +1.09), the
#   ear ~0.62 wide face-on-ish; SIDE view near ear an oval x 812-907 x y 395-535 (0.95 x 1.40), tip z_m 2.60 (near side:
#   2.60 / 0.95 = 2.74; the head top + the drawn 0.87 rise of both views = 2.55)
EAR_C = (0.70, -1.12, 1.75)           # "ear placement": the oval's centre (L side)
EAR_AXIS = (0.38, 0.40, 0.83)         # the oval's long axis (up, out, back)
EAR_SIZE = (0.44, 0.70, 0.09)        # "ear size": half width / half length / half thickness
EAR_YAW = 45.0                        # the ear face turns this far from the front toward its side (cup seen in BOTH views)
EAR_CUP = (0.82, 0.86, 0.10, 0.06)    # "ear cup": in-plane scale (width, length), depth radius (its centre sits 0.02
                                      #   in front of the ear face), length shift toward the tip
EAR_INNER = 0.80                      # "inner ear": the taupe fills this share of the ear outline
EAR_K = 0.05
# ---- LEGS + FEET (L; R mirrored). FRONT view feet x 62-110 / 248-297 (X -0.88 / +0.98 -> +-0.93, 0.48 wide); SIDE view
#   near front foot x 845-905 (Y -1.12..-0.53) y 620-652, toes FORWARD; near hind foot x 1025-1080 (Y +0.68..+1.22) y
#   604-636, toes BACKWARD (its toe lines sit at the rear end in the SIDE and BACK views: built as drawn)
FORE_LEG = ((0.80, -0.70, 0.30), (0.91, -0.80, 0.17), 0.20, 0.19)   # hip, ankle, radii: stubby, hidden under the rim
HIND_LEG = ((0.80, 0.78, 0.30), (0.92, 0.90, 0.17), 0.20, 0.19)    #   (the feet only peek out)
FORE_FOOT = ((0.93, -0.84, 0.13), (0.22, 0.30, 0.14), -1.0)   # "foot size": centre, radii (z top 0.27), toe direction (Y)
HIND_FOOT = ((0.94, 0.96, 0.13), (0.21, 0.28, 0.14), 1.0)
LEG_K, FOOT_K = 0.10, 0.10
CREASES = (-0.065, 0.065)             # "toe lines": 2 grooves across each foot's toe end (3 toes, the sheet's ink lines)
CREASE_R, CREASE_K = 0.016, 0.010
TOE_LINE_W = 0.018                    # "toe line" half width: the drawn ink toe lines as a colour region along each crease
# ---- TAIL: a LONG POINTED faceted spike. SIDE view: the root block x 1022-1050 (Y +0.65..+0.93) rising out of the plate
#   behind the boss, the line (1040, 445) -> tip (1212, 306) = 39 deg in the image (40 deg after the cos e correction);
#   BACK view root 0.38 wide (x 517-555), a centre ridge (diamond section), a joint ~0.55 along
TAIL_ROOT_R = 1.30                    # "tail root": distance behind the shell centre (artist 2026-10-10: moved back/down onto the rim band, shield_rodent-v1-tail-annotation.png; was 0.65 on the plate)
TAIL_ANGLE = 40.0                     # "tail angle": rise from the horizontal, deg
TAIL_LEN = 2.42                       # "tail length" root -> tip (the tip lands at the drawn tip's Y +2.55)
TAIL_BLOCK = (0.42, 0.19, 0.17)       # "tail root block": length, half width, half height (diamond)
TAIL_SPIKE0 = (0.15, 0.135)           # the spike's start half width / half height (the step at the joint)
TAIL_SINK = 0.16                      # the root is buried this far into the plate
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands)
OWN_TAU = 0.05                        # soft part ownership for weights
NECK_BLEND = (0.90, 1.25)             # body -> head blend along -Y on the neck
EAR_BLEND = (0.20, 0.45)              # head -> ear blend along the ear axis (fraction from the base end)
LEG_BLEND = (0.07, 0.05)              # leg top blend half width, foot blend above the ankle
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent + height open
IDLE_N = 96                           # "idle loop": 4 s
BREATH = (0.012, 0.5)                 # "breathing": body rise (units) / pitch (deg), 2 breaths per loop
IDLE_HEAD = (1.2, 2.0)                # head nod (2 cyc) / slow look (1 cyc), deg
EAR_TWITCH = (16.0, 0.30, 0.62, 30)   # "ear twitch": (deg, phase L, phase R, sharpness of the (0.5 + 0.5 cos)^n pulse)
TAIL_TICK = (6.0, 0.18, 0.68, 40)     # "tail tick": (deg yaw, phase 1 (left), phase 2 (right), sharpness)
WALK_N = 24                           # "scurry loop": 1 s
STRIDE, LIFT, DUTY = 0.20, 0.05, 0.75  # "stride" (foot travel), "foot lift", stance share of the cycle
WALK_PHASE = {"hindleg_L": 0.0, "foreleg_L": 0.25, "hindleg_R": 0.5, "foreleg_R": 0.75}   # 4-beat lateral sequence
FOOT_CURL = 10.0                      # swing feet curl (deg)
WALK_BOB = 0.012                      # body bob (4 per loop: one per footfall)
WALK_ROLL = 1.2                       # body roll (deg, 1 cyc)
WALK_TAIL = (3.0, 1.5)                # tail sway yaw (deg) on tail.001 / tail.002, 1 cyc
FLOOR_EPS = 1e-4

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
          "design/reference/shield_rodent-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; heights off the side view corrected for the 19 deg drawing elevation, "
                   "widths off the front view; floor z = 0 at the soles",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


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

# =========================================================================== 1. body shell SDF
t_sdf = time.time()
SIDES = (("L", 1.0), ("R", -1.0))


def mir(p, sg):
    return np.array([sg * p[0], p[1], p[2]], float)


def sd_belly(P):
    return SD.sd_ellipsoid(P, BELLY_C, BELLY_R)


def sd_neck(P):
    return SD.sd_round_cone(P, *NECK)


def sd_head(P):
    return SD.smin(SD.sd_ellipsoid(P, HEAD_C, HEAD_R), SD.sd_round_cone(P, *SNOUT_CONE), 0.10)


# ---- ears: a flattened ellipsoid in each ear's own frame with the front cup carved out
EAR = {}
for s, sg in SIDES:
    c_ = mir(EAR_C, sg)
    ax = unit(mir(EAR_AXIS, sg))
    yaw = math.radians(EAR_YAW)
    nh = np.array([sg * math.sin(yaw), -math.cos(yaw), 0.0])
    n_ = unit(nh - ax * float(nh @ ax))                     # the ear's front-face normal (the cup side)
    w_ = np.cross(ax, n_)
    EAR[s] = {"c": c_, "axis": ax, "n": n_, "w": w_, "base": c_ - ax * EAR_SIZE[1], "tip": c_ + ax * EAR_SIZE[1]}


def ear_local(P, s):
    e = EAR[s]
    Q = P - e["c"]
    return Q @ e["w"], Q @ e["axis"], Q @ e["n"]


def sd_ear(P, s):
    u, v, w = ear_local(P, s)
    Q = np.stack([u, v, w], 1)
    hw, hl, ht = EAR_SIZE
    d = SD.sd_ellipsoid(Q, (0.0, 0.0, 0.0), (hw, hl, ht))
    cw, cl, cd, csh = EAR_CUP
    cup = SD.sd_ellipsoid(Q, (0.0, csh, ht + 0.02), (cw * hw, cl * hl, cd))   # floor 0.08 under the front face
    return np.maximum(d, -cup)


def ear_inner_field(P, s):
    """> 0 on the taupe inner ear: inside a scaled copy of the ear outline AND on the front (cup) side."""
    u, v, w = ear_local(P, s)
    hw, hl, ht = EAR_SIZE
    csh = EAR_CUP[3]
    rho = np.sqrt((u / (EAR_INNER * hw)) ** 2 + ((v - csh) / (EAR_INNER * hl)) ** 2)
    return np.minimum(1.0 - rho, (w + 0.4 * ht) * 4.0)


LEGS = {}
for s, sg in SIDES:
    for kind, LEGD, FOOT in (("foreleg", FORE_LEG, FORE_FOOT), ("hindleg", HIND_LEG, HIND_FOOT)):
        fc, fr, tdir = FOOT
        LEGS["%s_%s" % (kind, s)] = {"hip": mir(LEGD[0], sg), "ankle": mir(LEGD[1], sg), "r": (LEGD[2], LEGD[3]),
                                     "foot_c": mir(fc, sg), "foot_r": np.array(fr, float), "toe_dir": tdir,
                                     "toe": mir(fc, sg) + np.array([0.0, tdir * fr[1], -0.03])}


def sd_leg(P, lg):
    L_ = LEGS[lg]
    return SD.sd_round_cone(P, L_["hip"], L_["ankle"], *L_["r"])


def sd_foot(P, lg):
    L_ = LEGS[lg]
    return SD.sd_ellipsoid(P, L_["foot_c"], L_["foot_r"])


GROUPS = ["belly", "neck", "head", "ear.L", "ear.R"] + ["leg." + lg for lg in LEGS] + ["foot." + lg for lg in LEGS]


def group_sdf(P):
    cols = []
    for g_ in GROUPS:
        if g_ == "belly":
            d = sd_belly(P)
        elif g_ == "neck":
            d = sd_neck(P)
        elif g_ == "head":
            d = sd_head(P)
        elif g_.startswith("ear."):
            d = sd_ear(P, g_[4:])
        elif g_.startswith("leg."):
            d = sd_leg(P, g_[4:])
        else:
            d = sd_foot(P, g_[5:])
        cols.append(d)
    return np.stack(cols, 1)


def ell_box(c, r, pad=0.0):
    c = np.asarray(c, float); r = np.asarray(r, float)
    return c - r - pad, c + r + pad


def pts_box(pts, R, pad=0.0):
    A = np.array(pts, float)
    return A.min(0) - R - pad, A.max(0) + R + pad


GRID_LO, GRID_HI = (-1.65, -2.55, -0.05), (1.65, 1.45, 2.85)
g = SD.Grid(GRID_LO, GRID_HI, SDF_STEP, SDF_BAND)
g.apply(sd_belly, *ell_box(BELLY_C, BELLY_R), 0.0)
for lg, L_ in LEGS.items():
    g.apply(lambda P, lg=lg: sd_leg(P, lg), *pts_box([L_["hip"], L_["ankle"]], L_["r"][0]), LEG_K)
    g.apply(lambda P, lg=lg: sd_foot(P, lg), *ell_box(L_["foot_c"], L_["foot_r"]), FOOT_K)
g.apply(sd_neck, *pts_box([NECK[0], NECK[1]], NECK[3]), NECK_K)
g.apply(sd_head, *pts_box([HEAD_C, SNOUT_CONE[1]], max(HEAD_R)), HEAD_K)
for s, sg in SIDES:
    e = EAR[s]
    g.apply(lambda P, s=s: sd_ear(P, s), np.minimum(e["base"], e["tip"]) - EAR_SIZE[0], np.maximum(e["base"], e["tip"]) + EAR_SIZE[0],
            EAR_K)
TOE_CREASES = []
for lg, L_ in LEGS.items():                            # toe lines across each foot's toe end
    c_, r_, td = L_["foot_c"], L_["foot_r"], L_["toe_dir"]
    for dx in CREASES:
        x_ = c_[0] + dx * r_[0] / 0.21
        c0 = np.array([x_, c_[1] + td * 0.35 * r_[1], c_[2] + 0.95 * r_[2]])
        c1 = np.array([x_, c_[1] + td * 1.05 * r_[1], 0.0])
        TOE_CREASES.append((c0, c1))
        g.apply(lambda P, c0=c0, c1=c1: SD.sd_round_cone(P, c0, c1, CREASE_R, CREASE_R),
                np.minimum(c0, c1) - CREASE_R, np.maximum(c0, c1) + CREASE_R, CREASE_K, mode="subtract")
g.floor(0.0)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
assert np.all(HV.min(0) > np.array(GRID_LO) + SDF_STEP) and np.all(HV.max(0) < np.array(GRID_HI) - SDF_STEP), \
    ("the shell touches the SDF grid box (an open cut)", HV.min(0), HV.max(0))
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "toe_creases": len(TOE_CREASES), "crumbs": crumbs,
                 "seconds": round(time.time() - t_sdf, 1)}
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
_fk = [tuple(sorted(f)) for f in LF]
_fc = {}
for k_ in _fk:
    _fc[k_] = _fc.get(k_, 0) + 1
LF = [f for f, k_ in zip(LF, _fk) if _fc[k_] == 1]            # collapse leftovers: doubled faces (a zero-volume fold)
DOUBLED = int(sum(c for c in _fc.values() if c > 1))
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(),
                    "seconds": round(time.time() - t, 1)}
print("RETOPO", json.dumps(report["retopo"]))
assert np.all(_bb_l / _bb_h > 0.99), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))


# =========================================================================== 3. region fields + iso cuts on the shell
def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


DGR = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["inner"] = np.maximum(ear_inner_field(LV, "L"), ear_inner_field(LV, "R"))
FIELDS["x"] = LV[:, 0].copy()


def toe_field(P):
    """> 0 within TOE_LINE_W of a toe crease's centre line (the drawn toe lines)"""
    out = np.full(len(P), -1.0)
    for c0, c1 in TOE_CREASES:
        ab = c1 - c0
        t_ = np.clip((P - c0) @ ab / float(ab @ ab), 0.0, 1.0)
        out = np.maximum(out, TOE_LINE_W - np.linalg.norm(P - (c0 + np.outer(t_, ab)), axis=1))
    return out


FIELDS["toe"] = toe_field(LV)
FIELDS["z"] = LV[:, 2].copy()
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
on_ear = lambda a, b: all(max(v[LAY["f_ear.L"]], v[LAY["f_ear.R"]]) > -TOL for v in (a, b))
on_seam_zone = lambda a, b: all(v[LAY["f_head"]] > -TOL and v[LAY["z"]] > SEAM_Z0 - 0.06 for v in (a, b))
on_seam_end = lambda a, b: all(v[LAY["f_head"]] > -TOL and abs(v[LAY["x"]]) < SEAM_W + 0.05 for v in (a, b))
_FOOT_KEYS = ["f_foot." + lg for lg in LEGS]
on_foot = lambda a, b: all(max(v[LAY[k]] for k in _FOOT_KEYS) > -TOL for v in (a, b))
CUTS = [("inner", 0.0, on_ear), ("x", SEAM_W, on_seam_zone), ("x", -SEAM_W, on_seam_zone), ("z", SEAM_Z0, on_seam_end)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array(["body"] * len(CF), dtype=object)
is_ear = np.isin(owner, [GROUPS.index("ear.L"), GROUPS.index("ear.R")])
shell_reg[is_ear & (FVAL["inner"] > 0)] = "ear_inner"
is_foot = np.isin(owner, [GROUPS.index("foot." + lg) for lg in LEGS])
# (no toe_line colour: the 0.036-wide strips fragmented into dark specks on the decimated feet; the carved creases +
#  the bake carry the toe lines)
is_head = owner == GROUPS.index("head")
shell_reg[is_head & (np.abs(FVAL["x"]) < SEAM_W) & (FVAL["z"] > SEAM_Z0)] = "seam"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=6.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


def project(pts, n):
    return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])


def front_anchor(x, z):
    """ray from the front (-Y) along +Y at (x, z) -> surface point + a smoothed normal (mean of a small cross of hits)"""
    S, _ = cast((x, -4.0, z), (0, 1, 0))
    ns = [cast((x + dx, -4.0, z + dz), (0, 1, 0))[1] for dx, dz in ((0, 0), (0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03))]
    return S, unit(np.mean(ns, 0))


PARTS = []
HIGH_SWAP = {}                      # part name -> (V, F) of its bake source (the plate / rim with brushed lines + scratches)
# ---- the shield-shell frame (pitched back-up)
_p = math.radians(SHELL_PITCH)
SH_C = np.array(SHELL_C, float)
SH_AX = np.array([0.0, -math.sin(_p), math.cos(_p)])
SH_UP = (0.0, 1.0, 0.0)                                  # lathe u -> the shell's back direction
_a, _u, _v = FP.frame_from_axis(SH_AX, SH_UP)


def shell_pt(x_back, y_side, h):
    return SH_C + _u * x_back + _v * y_side + _a * h


def az_of(th):
    """lathe angle -> world azimuth from the front (-Y) toward +X, degrees"""
    d = math.cos(th) * _u + math.sin(th) * _v
    return math.degrees(math.atan2(d[0], -d[1]))


def in_sheen(az):
    for c_, hw in PLATE_SHEEN:
        if abs((az - c_ + 180.0) % 360.0 - 180.0) <= hw:
            return True
    return False


_dth = math.pi / SHELL_SEG
_th = 2 * math.pi * np.arange(SHELL_SEG) / SHELL_SEG
SHEEN_SEG = [in_sheen(az_of(t_ + _dth)) for t_ in _th]


def plate_profile(rings):
    r_in = RIM_R[0] + 0.04
    prof = [(0.0, 0.10), (r_in, 0.10), (r_in, PLATE_EDGE_H)]
    for k in range(1, rings + 1):
        r_ = r_in * (1.0 - k / rings)
        prof.append((r_, PLATE_EDGE_H + PLATE_RISE * (1.0 - (r_ / r_in) ** 2)))
    return prof


def plate_h(r):
    r_in = RIM_R[0] + 0.04
    return PLATE_EDGE_H + PLATE_RISE * (1.0 - (min(r, r_in) / r_in) ** 2)


_pp = plate_profile(PLATE_RINGS)
_ptop = len(_pp) - 1                                     # segments >= 2 are the dome top
V, F, R, PL_FR = FP.lathe(_pp, lambda k, i: ("plate_sheen" if (k >= 2 and SHEEN_SEG[i]) else "plate"), SHELL_SEG,
                          SH_C, SH_AX, SH_UP)
PARTS.append(FP.Part("plate", V, F, R, bone="body"))
# rim band: bevelled rectangle loop (r, h)
r0, r1 = RIM_R
b_ = RIM_BEVEL
_ho = RIM_H - RIM_SLOPE
RIM_LOOP = [(r0, b_), (r0, RIM_H - b_), (r0 + b_, RIM_H), (r1 - b_, _ho), (r1, _ho - b_), (r1, b_), (r1 - b_, 0.0),
            (r0 + b_, 0.0)]


def rim_top_h(r):
    return RIM_H + (_ho - RIM_H) * (r - (r0 + b_)) / ((r1 - b_) - (r0 + b_))
RIM_REG = ["rim", "rim", "rim", "rim", "rim_wall", "rim_wall", "skirt", "skirt"]
V, F, R, RIM_FR = FP.ring_lathe(RIM_LOOP, RIM_REG, SHELL_SEG, SH_C, SH_AX, SH_UP)
PARTS.append(FP.Part("rim", V, F, R, bone="body"))
V, F, R, _ = FP.lathe(SKIRT, "skirt", SHELL_SEG, SH_C, SH_AX, SH_UP)
PARTS.append(FP.Part("skirt", V, F, R, bone="body"))
# boss: stepped collar + dome (one lathe from the buried bottom pole to the dome pole)
def boss_profile(steps):
    bd = [(BOSS_DOME[0] * math.cos(t_), BOSS[-1][1] + BOSS_DOME[1] * math.sin(t_))
          for t_ in np.linspace(0, math.pi / 2, steps + 1)[1:]]
    bd[-1] = (0.0, BOSS[-1][1] + BOSS_DOME[1])
    return BOSS + bd, ["boss_collar"] * (len(BOSS) - 1) + ["boss_dome"] * len(bd)


BOSS_PROF, BOSS_REG = boss_profile(BOSS_DOME_STEPS)
V, F, R, _ = FP.lathe(BOSS_PROF, BOSS_REG, BOSS_SEG, SH_C, SH_AX, SH_UP)
PARTS.append(FP.Part("boss", V, F, R, bone="body"))
_bp_hi, _br_hi = boss_profile(64)
Vh, Fh, _, _ = FP.lathe(_bp_hi, _br_hi, 320, SH_C, SH_AX, SH_UP)
HIGH_SWAP["boss"] = (Vh, Fh)                             # smooth bake source: the flat facets bake to a smooth dome
# rivets: small domes on the rim top
RIV_PROF = [(0.0, -0.035), (RIVET_R, -0.035)] + [(RIVET_R * math.cos(t_), RIVET_H * math.sin(t_))
                                                for t_ in np.linspace(0, math.pi / 2, 5)[:-1]] + [(0.0, RIVET_H)]
_slope = (_ho - RIM_H) / ((r1 - b_) - (r0 + b_))
for k in range(RIVETS):
    th = 2 * math.pi * (k + 0.5) / RIVETS
    c_ = shell_pt(RIVET_RADIUS * math.cos(th), RIVET_RADIUS * math.sin(th), rim_top_h(RIVET_RADIUS))
    radial = math.cos(th) * _u + math.sin(th) * _v
    V, F, R, _ = FP.lathe(RIV_PROF, "rivet", 8, c_, unit(_a - _slope * radial), SH_UP)   # normal to the sloped rim top
    PARTS.append(FP.Part("rivet.%02d" % k, V, F, R, bone="body"))
# ---- bake sources: the plate + rim with brushed grooves and light scratches (normal map only)
rng_g = [(BRUSH[1] + (BRUSH[2] - BRUSH[1]) * FP.hash01(j, BRUSH[5]), BRUSH[3] * (0.5 + 0.5 * FP.hash01(j, BRUSH[5] + 1.0)))
         for j in range(BRUSH[0])]


def scratches(n_, seed, r_lo, r_hi):
    out = []
    for j in range(n_):
        rr = r_lo + (r_hi - r_lo) * FP.hash01(j, seed)
        th = 2 * math.pi * FP.hash01(j, seed + 1.0)
        L_ = SCRATCH[2] + (SCRATCH[3] - SCRATCH[2]) * FP.hash01(j, seed + 2.0)
        ang = th + math.pi / 2 + (FP.hash01(j, seed + 3.0) - 0.5) * 1.2     # roughly tangential
        c_ = np.array([rr * math.cos(th), rr * math.sin(th)])
        d_ = np.array([math.cos(ang), math.sin(ang)])
        out.append((c_ - d_ * L_ / 2, c_ + d_ * L_ / 2))
    return out


SCR_PLATE = scratches(SCRATCH_PLATE, 11.0, 0.62, 1.12)
SCR_RIM = scratches(SCRATCH_RIM, 23.0, RIM_R[0] + 0.05, RIM_R[1] - 0.05)


def scratch_field(x, y, segs):
    out = np.zeros_like(x)
    for a2, b2 in segs:
        ab = b2 - a2
        L2 = float(ab @ ab)
        t_ = np.clip(((x - a2[0]) * ab[0] + (y - a2[1]) * ab[1]) / L2, 0.0, 1.0)
        dx_, dy_ = x - (a2[0] + t_ * ab[0]), y - (a2[1] + t_ * ab[1])
        taper = np.sin(np.pi * t_) ** 0.5
        out = np.maximum(out, SCRATCH[0] * taper * np.exp(-(dx_ ** 2 + dy_ ** 2) / SCRATCH[1] ** 2))
    return out


def plate_detail(x, y):
    r = np.hypot(x, y)
    gsum = np.zeros_like(r)
    for rj, dj in rng_g:
        gsum += dj * np.exp(-((r - rj) / BRUSH[4]) ** 2)
    return -(gsum + scratch_field(x, y, SCR_PLATE))


_pp_hi = [(0.0, 0.10), (RIM_R[0] + 0.04, 0.10)] + [(r_, plate_h(r_)) for r_ in np.linspace(RIM_R[0] + 0.04, 0.0, 240)]
_pp_hi[2] = (RIM_R[0] + 0.04, PLATE_EDGE_H)
_pp_hi.insert(2, (RIM_R[0] + 0.04, PLATE_EDGE_H - 0.01))
Vh, Fh, _, frh = FP.lathe(_pp_hi, "plate", 360, SH_C, SH_AX, SH_UP)
_hh = (Vh - SH_C) @ _a
_rr = np.hypot((Vh - SH_C) @ _u, (Vh - SH_C) @ _v)
_top = (_hh > PLATE_EDGE_H - 0.005) & (_rr < RIM_R[0] + 0.035) & (_rr > 0.50)
Vh = FP.displace_axial(Vh, SH_C, frh, _top, plate_detail)
HIGH_SWAP["plate"] = (Vh, Fh)
_rl_hi, _rl_top = [], []
for k, ((ra, ha), (rb, hb)) in enumerate(zip(RIM_LOOP, RIM_LOOP[1:] + RIM_LOOP[:1])):   # densify the rim's top face
    nseg = 24 if k == 2 else 1
    for j in range(nseg):
        _rl_hi.append((ra + (rb - ra) * j / nseg, ha + (hb - ha) * j / nseg)); _rl_top.append(k == 2 or (k == 3 and j == 0))
Vh, Fh, _, frh = FP.ring_lathe(_rl_hi, "rim", 480, SH_C, SH_AX, SH_UP)
Vh = FP.displace_axial(Vh, SH_C, frh, np.repeat(np.array(_rl_top), 480), lambda x, y: -scratch_field(x, y, SCR_RIM))
HIGH_SWAP["rim"] = (Vh, Fh)
# ---- snout ball + teeth (head)
SN_PROF = [(0.0, -SNOUT_R)] + [(SNOUT_R * math.cos(t_), SNOUT_R * math.sin(t_)) for t_ in np.linspace(-math.pi / 2, math.pi / 2, 11)[1:-1]] \
    + [(0.0, SNOUT_R)]
V, F, R, _ = FP.lathe(SN_PROF, "snout", 24, SNOUT_C, (0, 0, 1), (0, 1, 0))
PARTS.append(FP.Part("snout", V, F, R, bone="head"))
_tl = TOOTH_TOP - TOOTH_TIP
TOOTH_PROF = [(0.0, 0.0), (TOOTH_W, 0.01), (TOOTH_W, _tl - 0.10), (TOOTH_W * 0.85, _tl - 0.045), (TOOTH_W * 0.45, _tl - 0.012),
              (0.0, _tl)]
for s, sg in SIDES:
    V, F, R, _ = FP.lathe(TOOTH_PROF, "tooth", 10, (sg * TOOTH_X, TOOTH_Y, TOOTH_TOP), (0, 0, -1), (0, 1, 0),
                          su=TOOTH_D / TOOTH_W, sv=1.0)
    PARTS.append(FP.Part("tooth." + s, V, F, R, bone="head"))
# ---- eyes + highlight dots (closed conforming decals)
FACE = {}
for s, sg in SIDES:
    S, n = front_anchor(sg * EYE_X, EYE_Z)
    ne = unit(n + EYE_FACE * np.array([0.0, -1.0, 0.0]))
    V, F, R = FP.polar_decal(S, ne, FP.lens_curve(EYE_A, EYE_B, 24, 0.0), project, DECAL_UP, DECAL_DN, DECAL_DOME, 3, "eye",
                             up_hint=(0, 0, 1))
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    Sg, _ = front_anchor(sg * EYE_X + GLINT_OFF[0], EYE_Z + GLINT_OFF[1])
    V, F, R = FP.polar_decal(Sg, ne, FP.lens_curve(GLINT_R, GLINT_R, 12, 0.0), project, DECAL_UP + DECAL_DOME + 0.006,
                             DECAL_DN, 0.004, 2, "eye_glint", up_hint=(0, 0, 1))
    PARTS.append(FP.Part("glint." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": ne.round(4).tolist(), "glint": Sg.round(4).tolist()}
# ---- tail: root block + spike, diamond sections, tone per facet (upper facets lit, lower facets shade)
TAIL_ROOT = shell_pt(TAIL_ROOT_R, 0.0, plate_h(TAIL_ROOT_R))
_ta = math.radians(TAIL_ANGLE)
TAIL_DIR = np.array([0.0, math.cos(_ta), math.sin(_ta)])


def tail_tone(n_):
    return "tail" if float(n_[2]) > 0.0 else "tail_shade"


bl, bw, bh = TAIL_BLOCK
V, F, R = FP.diamond_spike(TAIL_ROOT, TAIL_DIR, [(-TAIL_SINK, bw, bh), (bl, bw * 0.95, bh * 0.95)], tail_tone)
PARTS.append(FP.Part("tail_block", V, F, R, bone="tail.001"))
V, F, R = FP.diamond_spike(TAIL_ROOT, TAIL_DIR, [(bl - 0.04, TAIL_SPIKE0[0], TAIL_SPIKE0[1]),
                                                 (bl + 0.35 * (TAIL_LEN - bl), TAIL_SPIKE0[0] * 0.70, TAIL_SPIKE0[1] * 0.70),
                                                 (TAIL_LEN, 0.0, 0.0)], tail_tone)
PARTS.append(FP.Part("tail_spike", V, F, R, bone="tail.002"))
TAIL_TIP = TAIL_ROOT + TAIL_DIR * TAIL_LEN
TAIL_JOINT = TAIL_ROOT + TAIL_DIR * bl

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
_below = [i[0] for i in ISL if i[1][:, 2].min() < -1e-3]
assert lo0[2] >= -1e-3 and not _below, "a part dips below the floor (z %.4f, %s)" % (lo0[2], _below)   # floor = SDF cut
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
REG = ["body", "ear_inner", "seam", "eye", "eye_glint", "snout", "tooth", "plate", "plate_sheen", "rim", "rim_wall", "rivet",
       "boss_collar", "boss_dome", "skirt", "tail", "tail_shade"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (must never borrow a neighbour's field): %s" % _empty


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.98 + 0.04 * j                       # +-2 %: brushed metal, never speckle


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                  # opaque closed body: single-sided (glTF doubleSided false)
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
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)
_lit = sorted(n for n, v in pal_default["regions"].items() if "emission" in v)
report["glow"] = {"skin": "default", "glowing_regions": _lit, "rule": "no glow (the sheet draws none): zero Glow"}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, jitter(VALL, FALL))
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the snout ball on the midline
anchor = np.array(HEAD_C, float) - SHIFT
landmark = np.array(SNOUT_C, float) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the snout ball centre (SNOUT_C), both on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["shell"] = {"centre": SH_C.round(4).tolist(), "axis": SH_AX.round(4).tolist(), "pitch_deg": SHELL_PITCH,
                   "rim_r": list(RIM_R), "rim_h": RIM_H, "plate_centre_z": round(float(shell_pt(0, 0, plate_h(0.0))[2]), 4),
                   "boss_top_z": round(float(shell_pt(0, 0, BOSS[-1][1] + BOSS_DOME[1])[2]), 4), "rivets": RIVETS,
                   "sheen_segments": int(sum(SHEEN_SEG)), "brush_grooves": BRUSH[0],
                   "scratches": {"plate": SCRATCH_PLATE, "rim": SCRATCH_RIM}}
report["tail"] = {"root": TAIL_ROOT.round(4).tolist(), "joint": TAIL_JOINT.round(4).tolist(), "tip": TAIL_TIP.round(4).tolist(),
                  "angle_deg": TAIL_ANGLE, "length": TAIL_LEN}

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
_part_tris = {}
for p in PARTS:
    key = p.name.split(".")[0]
    key = {"rivet": "rivets", "tooth": "teeth", "eye": "eyes", "glint": "eye_glints"}.get(key, key)
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The SDF shell carries the head, the two large "
                            "ears, the belly, four short legs + feet; the shield-shell is hard-surface lathes (%d segments "
                            "for a round silhouette) + %d rivets; the brushed lines + scratches live in the normal map."
                            % (TRI_BUDGET[0], TRI_BUDGET[1], SHELL_SEG, RIVETS))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, lathes, rim ring, decals, tail facets)"}
assert not report["open_edges"]["open_parts"], report["open_edges"]["open_parts"]
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"front_view_height_px": 291, "front_view_width_px": 317, "side_view_height_px": 350,
                               "side_view_length_px": 492, "note": "the sheet's heights include the drawing elevation"},
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
low["conquest_source"] = "from scratch: design/reference/shield_rodent-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_sh_pts = np.vstack([p.V for p in PARTS if p.name in ("rim", "boss")])
_tail_pts = np.vstack([p.V for p in PARTS if p.name.startswith("tail")])
_fl = LEGS["foreleg_L"]; _hl = LEGS["hindleg_L"]
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-1.05, -2.45, 0.05]) - SHIFT).tolist(), (np.array([1.05, -0.95, 2.20]) - SHIFT).tolist()],
    "shell": [(_sh_pts.min(0) - SHIFT).tolist(), (_sh_pts.max(0) - SHIFT).tolist()],
    "feet": [(np.array([0.55, -1.30, 0.0]) - SHIFT).tolist(), (np.array([1.35, 1.30, 0.75]) - SHIFT).tolist()],
    "tail": [(_tail_pts.min(0) - SHIFT).tolist(), (_tail_pts.max(0) - SHIFT).tolist()],
    "views": {"face": [-22.0, 10.0], "shell": [35.0, 38.0], "feet": [70.0, 8.0], "tail": [125.0, 18.0]},
    "axis_xy": [float(0.155 - SHIFT[0]), float(-SHIFT[1])]})   # the sheet's front axis 189.5 px = X +0.155 (head axis 174)

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "face", "shell", "tail")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
for name, V, F, R, p in ISL[1:]:
    if name in HIGH_SWAP:
        V, F = HIGH_SWAP[name]                                # the brushed / scratched bake source
    PH_V.append(V - SHIFT); PH_F += [[i + o_ for i in f] for f in F]; o_ += len(V)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
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
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part; the plate + rim bake sources carry "
                                         "the brushed grooves + light scratches",
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
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k not in ("NORMAL", "AO")}))
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
bsdf = nt_.nodes["Principled BSDF"]
mul = nt_.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt_.links.new(nt_.nodes["col"].outputs["Color"], ia); nt_.links.new(ta.outputs["Color"], ib)
nt_.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt_.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt_.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt_.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
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
                     "provenance": "pixel-sampled from the sheet's chips + drawn surfaces (see the palette notes)"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 7. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


BONES = [("body", P_(shell_pt(0, 0, -0.10)), P_(shell_pt(0, 0, 0.60)), "root"),
         ("head", P_((0.0, -1.05, 0.57)), P_((0.0, -1.72, 1.40)), "body")]
for s, sg in SIDES:
    BONES.append(("ear_" + s, P_(EAR[s]["base"] + EAR[s]["axis"] * 0.30), P_(EAR[s]["tip"]), "head"))
for lg, L_ in LEGS.items():
    BONES += [(lg + ".leg", P_(L_["hip"]), P_(L_["ankle"]), "body"), (lg + ".foot", P_(L_["ankle"]), P_(L_["toe"]), lg + ".leg")]
BONES += [("tail.001", P_(TAIL_ROOT), P_(TAIL_JOINT), "body"), ("tail.002", P_(TAIL_JOINT), P_(TAIL_TIP), "tail.001")]
CONNECT = {"tail.002"}                                # the foot bones stay unconnected: their location absorbs the IK
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
    e.use_connect = nm in CONNECT
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
GD = group_sdf(Pw)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
Ws = np.zeros((b_ - a_, len(DEFORM)))
Ws[:, J["body"]] += Wg[:, GROUPS.index("belly")]
wnk = smoothstep(NECK_BLEND[0], NECK_BLEND[1], -Pw[:, 1])
Ws[:, J["body"]] += Wg[:, GROUPS.index("neck")] * (1 - wnk)
Ws[:, J["head"]] += Wg[:, GROUPS.index("neck")] * wnk
Ws[:, J["head"]] += Wg[:, GROUPS.index("head")]
for s, sg in SIDES:
    gi = GROUPS.index("ear." + s)
    _, v_, _ = ear_local(Pw, s)
    we = smoothstep(EAR_BLEND[0], EAR_BLEND[1], (v_ + EAR_SIZE[1]) / (2 * EAR_SIZE[1]))
    Ws[:, J["ear_" + s]] += Wg[:, gi] * we
    Ws[:, J["head"]] += Wg[:, gi] * (1 - we)


def leg_weights(P, lg):
    """-> (n, 3) [body, leg, foot] by the arc parameter on [hip + 0.3 up, hip, ankle, toe]: the leg blends in from the
    body at the hip, the foot ONLY below / at the ankle (the whole foot rides the foot bone rigidly: planted soles)"""
    L_ = LEGS[lg]
    poly = np.array([L_["hip"] + np.array([0.0, 0.0, 0.3]), L_["hip"], L_["ankle"], L_["toe"]])
    _, s_, _ = FP.spine_param(P, poly)
    al = SD.arclen(poly)
    b, b2 = LEG_BLEND
    w_lg = smoothstep(al[1] - b, al[1] + b, s_)
    w_ft = smoothstep(al[2] - b2, al[2], s_)
    return np.stack([1 - w_lg, w_lg - w_ft, w_ft], 1)


for lg in LEGS:
    WL = leg_weights(Pw, lg)
    gi = GROUPS.index("leg." + lg)
    Ws[:, J["body"]] += Wg[:, gi] * WL[:, 0]
    Ws[:, J[lg + ".leg"]] += Wg[:, gi] * WL[:, 1]
    Ws[:, J[lg + ".foot"]] += Wg[:, gi] * WL[:, 2]
    Ws[:, J[lg + ".foot"]] += Wg[:, GROUPS.index("foot." + lg)]
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
    Wt[a_:b_, J[p.bone]] = 1.0
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx_ = np.argsort(-Wt, 1, kind="stable")[:, 4:]
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between belly, neck, head, ears, 4 legs, 4 feet; neck "
                          "body -> head along -Y; ears blend from the head along the ear axis; legs body -> leg -> foot "
                          "by the leg arc (each foot rigid below the ankle); the shield-shell parts (plate, rim, skirt, "
                          "boss, rivets) rigid on body; snout, teeth, eyes, highlights rigid on head; the tail root block "
                          "rigid on tail.001, the spike rigid on tail.002" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics). Per frame: body / head / ears / tail posed, then every
#      leg solved exactly: the leg bone aims at its foot target (rest ankle + the clip's foot offset), the unconnected
#      foot bone takes the target ankle position (its location absorbs the leg-length change) and keeps its world rest
#      orientation (+ the swing curl). A floor guard lifts the body + every target if any vertex dips below z 0 (pass 1);
#      keyed from the stored channels (pass 2, frame N+1 = frame 1); measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST4 = {b.name: Matrix(b.matrix_local) for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
REST_T = {b.name: np.array(b.tail_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM
LEG_BONES = {lg + sfx for lg in LEGS for sfx in (".leg", ".foot")}


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def world_rot(rots):
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        av = Vector(AXV[axis]) if isinstance(axis, str) else Vector(axis)
        Rw = Matrix.Rotation(math.radians(deg), 3, av) @ Rw
    return Rw


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ world_rot(rots) @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def solve_legs(legs, guard_z):
    """legs: {leg: (dy, lift, curl_deg)} -> poses the leg + foot bones exactly onto the targets. Returns the worst
    leg-length change (target hip->ankle distance minus the bone length) absorbed by the foot bone's location."""
    worst = 0.0
    for lg, L_ in LEGS.items():
        dy, lift, curl = legs.get(lg, (0.0, 0.0, 0.0))
        bl_, bf_ = lg + ".leg", lg + ".foot"
        Mp = rig.pose.bones["body"].matrix.copy()
        M0 = Mp @ REST4["body"].inverted() @ REST4[bl_]               # the leg bone posed with an identity basis
        Hh = np.array(M0.translation)
        A = REST_H[bf_] + np.array([0.0, dy, lift + guard_z])
        y0 = Vector(np.array(M0.to_3x3().col[1]))
        y1 = Vector(unit(A - Hh))
        Rw = y0.rotation_difference(y1).to_matrix()
        M03 = M0.to_3x3()
        q = (M03.inverted() @ Rw @ M03).to_quaternion(); q.normalize()
        if q.w < 0:
            q.negate()
        pbl = rig.pose.bones[bl_]
        pbl.location = (0, 0, 0); pbl.rotation_quaternion = q; pbl.scale = (1, 1, 1)
        ML = M0 @ q.to_matrix().to_4x4()
        Mf0 = ML @ REST4[bl_].inverted() @ REST4[bf_]                 # the foot posed with an identity basis
        Rf = Matrix.Rotation(math.radians(curl * L_["toe_dir"] * -1.0), 3, "X") @ REST4[bf_].to_3x3()
        Mt = Matrix.Translation(Vector(A)) @ Rf.to_4x4()
        Bf = Mf0.inverted() @ Mt
        loc, qf, _ = Bf.decompose()
        qf.normalize()
        if qf.w < 0:
            qf.negate()
        pbf = rig.pose.bones[bf_]
        pbf.location = loc; pbf.rotation_quaternion = qf; pbf.scale = (1, 1, 1)
        worst = max(worst, abs(float(np.linalg.norm(A - Hh)) - float(np.linalg.norm(REST_H[bf_] - REST_H[bl_]))))
    return worst


def pulse(u, u0, n):
    return (0.5 + 0.5 * math.cos(TAU * (u - u0))) ** n     # a narrow periodic bump (integer harmonics)


def idle_pose(u):
    P = {}
    br = math.sin(TAU * 2 * u)
    P["body"] = ((0.0, 0.0, BREATH[0] * (0.5 + 0.5 * br)), [("X", BREATH[1] * math.sin(TAU * 2 * u - 0.5))], None)
    P["head"] = (None, [("X", IDLE_HEAD[0] * math.sin(TAU * 2 * u - 0.9)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u + 0.3))], None)
    for s, sg in SIDES:
        u0 = EAR_TWITCH[1] if s == "L" else EAR_TWITCH[2]
        tw = EAR_TWITCH[0] * pulse(u, u0, EAR_TWITCH[3])
        P["ear_" + s] = (None, [(tuple(EAR[s]["axis"]), -sg * 0.4 * tw), (tuple(EAR[s]["w"]), tw)], None)   # flick
    tick = TAIL_TICK[0] * (pulse(u, TAIL_TICK[1], TAIL_TICK[3]) - pulse(u, TAIL_TICK[2], TAIL_TICK[3]))
    P["tail.001"] = (None, [("Z", tick), ("X", -0.25 * abs(tick))], None)
    P["tail.002"] = (None, [("Z", 0.5 * TAIL_TICK[0] * (pulse(u, TAIL_TICK[1] + 0.02, TAIL_TICK[3])
                                                        - pulse(u, TAIL_TICK[2] + 0.02, TAIL_TICK[3])))], None)
    return P, {}, {"tick": tick}


def walk_foot(u, ph):
    """foot offset (dy, lift, curl) at loop phase u for a leg of phase ph: stance (DUTY of the cycle) carries the planted
    foot back from -STRIDE/2 to +STRIDE/2 (forward = -Y), the swing returns it with a sine lift + curl"""
    p = (u - ph) % 1.0
    if p < DUTY:
        return (STRIDE * (p / DUTY - 0.5), 0.0, 0.0)
    q = (p - DUTY) / (1.0 - DUTY)
    sq = q * q * (3 - 2 * q)
    return (STRIDE * (0.5 - sq), LIFT * math.sin(math.pi * q), FOOT_CURL * math.sin(math.pi * q))


def walk_pose(u):
    """PLACEHOLDER 4-beat scurry in place (the game glides the unit): lateral sequence LH, LF, RH, RF; stance feet planted
    on the floor and carried back at the stride speed, swing feet lifted; a small body bob + roll, ears back, tail sway."""
    P = {}
    bob = WALK_BOB * (0.5 + 0.5 * math.sin(TAU * 4 * u))
    P["body"] = ((0.0, 0.0, bob), [("Y", WALK_ROLL * math.sin(TAU * u))], None)
    P["head"] = (None, [("X", -1.0 * math.sin(TAU * 4 * u + 0.6))], None)
    for s, sg in SIDES:
        P["ear_" + s] = (None, [(tuple(EAR[s]["w"]), -6.0 + 1.5 * math.sin(TAU * 4 * u - 0.6))], None)
    P["tail.001"] = (None, [("Z", WALK_TAIL[0] * math.sin(TAU * u + 0.4))], None)
    P["tail.002"] = (None, [("Z", WALK_TAIL[1] * math.sin(TAU * u - 0.2))], None)
    legs = {lg: walk_foot(u, WALK_PHASE[lg]) for lg in LEGS}
    return P, legs, {"bob": bob}


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def apply_pose(P, legs, guard_z):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        if bn in LEG_BONES:
            pb.location = (0, 0, 0); pb.rotation_quaternion = Quaternion(); pb.scale = (1, 1, 1)
            continue
        loc, rots, sc = P.get(bn, (None, [], None))
        dw = np.zeros(3) if loc is None else np.asarray(loc, float)
        if bn == "body":
            dw = dw + np.array([0.0, 0.0, guard_z])
        pb.location = local_loc(bn, dw)
        pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
        pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    short = solve_legs(legs, guard_z)
    bpy.context.view_layer.update()
    return short


def bake_clip(name, N, pose_fn):
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames, guard, info, shorts = [], [], [], []
    for f in range(N):
        P, legs, inf = pose_fn(f / N)
        gz = 0.0
        for it in range(6):
            sh_ = apply_pose(P, legs, gz)
            mz = float(eval_coords(low)[:, 2].min())
            if mz >= -1e-6:
                break
            gz += -mz + FLOOR_EPS
        frames.append({bn: (rig.pose.bones[bn].location.copy(), rig.pose.bones[bn].rotation_quaternion.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
        guard.append(gz); info.append(inf); shorts.append(sh_)
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    rows = []
    for f in range(N + 1):
        fr = frames[f % N]
        for bn in KEY_BONES:
            pb = rig.pose.bones[bn]
            pb.location, pb.rotation_quaternion, pb.scale = fr[bn]
            for ch in ("location", "rotation_quaternion", "scale"):
                pb.keyframe_insert(ch, frame=f + 1)
            rows.append(list(fr[bn][0]) + list(fr[bn][1]) + list(fr[bn][2]))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["loop_seconds"] = N / K.FPS
    rig.animation_data.action = None
    return act, rows, guard, info, shorts


CLIPS = {"idle": (IDLE_N, idle_pose), "walk": (WALK_N, walk_pose)}
NEW_ACTS, key_rows, GUARD, INFO, SHORT = {}, [], {}, {}, {}
t_clip = time.time()
for cn, (N, fn) in CLIPS.items():
    NEW_ACTS[cn], rows, GUARD[cn], INFO[cn], SHORT[cn] = bake_clip(cn, N, fn)
    key_rows += rows
DIG["keys"] = sha(np.array(key_rows))
# sole vertices: dominant deform weight on a foot bone, below z 0.03 at rest (the planted-foot probe)
_ft_j = [J[lg + ".foot"] for lg in LEGS]
SOLE = np.nonzero(np.isin(np.argmax(Wt, 1), _ft_j) & (VALL[:, 2] < 0.03))[0]
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
    soles = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        soles.append(C[SOLE])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "tail_tip": list(pbs["tail.002"].tail), "head_tail": list(pbs["head"].tail),
                     "ear_L": list(pbs["ear_L"].tail), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - REST_H["body"][2]
    tt_ = np.array([r["tail_tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_]); el = np.array([r["ear_L"] for r in R_])
    S_ = np.array(soles[:-1])                              # (frames, sole verts, 3)
    planted = S_[:, :, 2] < 0.005
    slide = []
    for fi in range(len(S_) - 1):                          # planted in both frames: ground speed = the stride speed
        both = planted[fi] & planted[fi + 1]
        if both.any():
            dyv = S_[fi + 1, both, 1] - S_[fi, both, 1]
            slide.append(float(np.abs(dyv - (STRIDE / (DUTY * N) if cn == "walk" else 0.0)).max()))
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "body_lift": [round(float(bz.min()), 5), round(float(bz.max()), 5)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "leg_length_change_max": round(float(max(SHORT[cn])), 5),
                    "sole_verts": int(len(SOLE)),
                    "planted_sole_slide_max_vs_ground": round(max(slide) if slide else 0.0, 5),
                    "sole_min_z": round(float(S_[:, :, 2].min()), 5),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist(),
                    "head_top_p2p_xyz": (hd.max(0) - hd.min(0)).round(4).tolist(),
                    "ear_L_tip_p2p_xyz": (el.max(0) - el.min(0)).round(4).tolist()}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["clip_seconds"] = round(time.time() - t_clip, 1)
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("shield_rodent v1: root > body > head > ear_L / ear_R; body > {fore,hind}leg_{L,R}.leg > .foot; "
                       "body > tail.001 > tail.002")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s: breathing (the "
                               "shell rises + pitches a touch, 2 breaths), small head nod / look, one ear twitch per ear "
                               "per loop, two tail ticks (left, right); feet held planted by the exact leg solve. walk = "
                               "1 s 4-beat scurry in place (lateral sequence LH, LF, RH, RF; stance 75%%; stride %.2f, "
                               "foot lift %.2f sheet units): stance feet planted on the floor and carried back, swing feet "
                               "lifted + curled, ears back, tail sway. Feet never below z 0, root still." % (STRIDE, LIFT))
low["conquest_locomotion"] = "PLACEHOLDER: 4-beat scurry in place (movement intent open; the game glides the unit)"
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

# =========================================================================== 8. glb
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
              "structure": "armature identity + skinned 'shield_rodent' (one opaque body material); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
