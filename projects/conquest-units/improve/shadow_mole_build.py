"""Shadow Mole -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/shadow_mole_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNITS x2: Pangolin Knight, Shadow Mole (artist sheets)" (section SHADOW MOLE)
+ the sheet design/reference/shadow_mole-character-sheet.png (intake: design/reference/shadow_mole/intake.json).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. Brief: improve/shadow_mole_v1_brief.md (v1 DRAFT).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet, measured off the two TRUE orthos (intake ortho_frame.views =
back, side; the sheet's FRONT is a 3/4 drawing, used as a check only):
  BACK view (seen from +Y; image right = -X = the unit's RIGHT, R):
        X = -(x_px - 638) / 100 ; z = (643 - y_px) / 100
  SIDE view (faces image-left = the unit's front, -Y; seen from +X):
        Y = (x_px - 991.5) / 100 ; z = (643 - y_px) / 100
The intake's back axis is x 632 (the bbox centre); the drawn body dome is centred at x 638 (body rows z 1.0-1.9, e.g.
y 504 spans x 574-701) and the two short stalks sit symmetric about it (roots x 537 / 742 -> X +1.01 / -1.04), so the
mirror plane X = 0 sits at x 638 and conquest_focus.axis_xy puts the sheet's x 632 axis 0.06 u to the model's +X.
The ONE tall stalk (back-left, X +0.72 at its root) is the unit's only asymmetric part.

Pipeline:
  1. SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the SHADOW BURROW (a low fill disc + a ring of puffy
     bulges + an outer ring of small scallops: the cloud-edged rim; flat on the floor), the mole's TRUNK (a tapered egg
     tilted forward) + HEAD (a tilted ellipsoid) + the short up-pointing SNOUT the petal crown sits on, the two DIGGING
     CLAWS (rounded mitten paws with three pointed toes, smooth-unioned onto the chest, toes hovering over the burrow
     rim) and the three BURROW-STALKS (one tall + two short tubes along catmull spines, their roots melting into the
     burrow). Marching tets = the HIGH (bake source); collapse decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: part ownership (burrow / body / claw / stalk), the burrow's dark
     HOLE (radius from the burrow centre), the stalks' arc-length colour bands (accent root -> bright pink -> the
     paler transition under the tuft).
  3. PARTS (shadow_mole_parts.py, closed): the flower-like petal TUFTS on each stalk tip (7 / 6 / 6 pointed petals,
     pale-pink midbands), the 7-petal CROWN on the snout, the small oval EYES (dark oval + white centre, decals).
  4. palette (palettes.py regions -> Col / Glow; no glow), Smart UV, bake normal + AO from the SDF high + parts at
     seed 0, AO floored per region class.
  5. RIG: root > burrow (static) ; root > body > head > crown ; body > claw.{L,R} ; root > stalk.{tall,L,R}.{0,1,2}
     (vine chains). Clips (PLACEHOLDER, movement not yet given -- OPEN-QUESTIONS N3): idle = de-synced stalk sway +
     body breathing + a head turn (4 s); walk = in-place burrow-slide (body bobs / leans, claws dig, stalks trail; the
     burrow stays on the floor; 2 s). glb export.
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
import shadow_mole_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "shadow_mole"
CHAR_ID = "shadow_mole"               # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit (brief)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the shell (the bake high)
SDF_BAND = 0.36                       # must exceed the largest smooth-union k (BURROW_K 0.30) + 2 steps (kit hazard)
BODY_TRIS = 10000                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
SHEET_AXIS_X = 0.06                   # the intake's back axis (x 632) sits this far toward +X of the mirror plane (x 638)
# ---- SHADOW BURROW: back view the burrow spans x 453-809 (X +1.85..-1.71 -> half width 1.78 about the mirror), side
#   view x 834-1197 (Y -1.57..+2.06: centre +0.25, half 1.82). The sheet draws the disc seen from slightly above (its
#   drawn ellipse is ~0.95 u tall); the model is a LOW cloud-edged solid (brief): its rim puffs top out at ~0.4.
BURROW_C = (0.0, 0.22)                # "burrow position" (X, Y) of the disc centre
BURROW_FILL = (1.50, 0.15, -0.03)     # "burrow floor": inner disc radius / half height / centre z (top at z 0.12)
PUFF_RING = (1.36, 12, 0.48, 0.15, 0.04)    # "burrow rim puffs": ring radius, count, puff radius, puff half height, z
                                      #   (v1.1 gate: low + broad + deeply blended -- a shadow pool, not a ring of stones)
PUFF_OUTER = (1.60, 14, 0.24, 0.09, 0.0)    # "rim scallops": the outer ring of low bulges that scallop the cloud edge
PUFF_JIT = 0.14                       # "cloud irregularity": puff size / spacing jitter share
BURROW_K = 0.30                       # puffs melt into each other over this (v1 0.10 left individual lumps)
HOLE_R = 1.18                         # "burrow hole": the near-black opening (sheet 8,8,8) inside this radius
# ---- mole TRUNK: a tapered egg, tilted forward. BACK view half widths (body mask rows): z 1.87 0.20, 1.63 0.50,
#   1.39 0.635, 1.15 0.75, 0.91 0.90; apex y 452 (z 1.91). SIDE view back edge z 1.87 Y 0.01, 1.63 0.34, 1.39 0.45,
#   1.15 0.62, 0.91 0.79; front edge z 0.91 -0.70 (fit: hw(z) = Rx sqrt(1 - t^2)(1 - TAPER t), t = (z - zc) / Rz)
TRUNK_C = (0.0, 0.07, 0.40)           # "body position"
TRUNK_R = (0.93, 0.80, 1.52)          # "body size": half width / half depth / half height
TRUNK_TAPER = 0.15                    # "dome shape": the body narrows this share toward its top
TRUNK_TILT = 10.0                     # "body lean" (deg, top toward the front)
TRUNK_K = 0.10                        # the body melts into the burrow over this
# ---- HEAD + SNOUT: SIDE view the head top z 1.97 (y 446) over Y -0.90..-0.40, the face front Y -1.08 at z 1.63
#   (y 480), chin Y -0.84 at z 1.39; the snout points up-forward to the crown centre (side view crown hub ~ x 886,
#   y 426 -> Y -1.05, z 2.17)
HEAD_C = (0.0, -0.50, 1.60)           # "head position"
HEAD_R = (0.46, 0.56, 0.38)           # "head size": half width / half length (forward) / half height
HEAD_TILT = 15.0                      # "head tilt" (deg, snout end up)
HEAD_K = 0.22
SNOUT_A, SNOUT_B = (0.0, -0.78, 1.78), (0.0, -0.98, 2.04)   # "snout": root / tip centres
SNOUT_R = (0.20, 0.12)                # snout root / tip radius
SNOUT_K = 0.10
# ---- EYE: SIDE view the drawn eye centre x 980.9, y 494.3 (Y -0.106, z 1.487); dark oval 13 x 21 px, white centre
#   6 x 12 px; cast onto the head from the side (both sides; mirror-symmetric)
EYE_YZ = (-0.106, 1.487)              # "eye placement"
EYE_R = (0.065, 0.105)                # "eye size": dark oval half width / half height
EYE_RINGS = (2, 4)                    # white centre = the inner 2 of 4 rings (white 0.5 of the dark oval)
EYE_UP, EYE_DOME, EYE_SINK = 0.006, 0.006, 0.02
# ---- DIGGING CLAWS: SIDE view the paws span ~0.6 u across and z 0.23-1.00 (two drawn at Y -0.85 / 0.0: the sheet
#   spreads them 3/4-wise); FRONT (3/4) view x 140-205 / 285-345. Model: one pair at X +-0.52 in front of the chest,
#   resting on the burrow rim, toes pointing forward-down over it (claw panel: a round mitten, three pointed toes)
CLAW_C = (0.50, -0.66, 0.72)          # "claw position" (L; R = mirror): the paw's back melts into the chest, the claw
                                      #   tips hover over the (lower, v1.1) rim so they stay free to dig
CLAW_R = (0.26, 0.12, 0.21)           # "paw size": half width / half thickness / half height of the rounded paw
CLAW_YAW, CLAW_PITCH = 15.0, 15.0     # paw turned outward / top leaning back (deg)
CLAW_K = 0.08                         # the paw melts into the chest over this
# the three CLAW TIPS (v1.1 gate: SDF toes read as drips): closed parametric curved pointed cones (shadow_mole_parts.petal
#   with a near-round section), roots buried in the paw's lower front, tips sharp (a true pole, not grid-limited)
TOE_SPREAD = 0.15                     # "claw spacing" (centre to centre)
TOE_ROOT_DOWN = 0.08                  # claws start this far below the paw centre
TOE_LEN = 0.40                        # "claw length" (visible + TOE_SINK buried)
TOE_HW, TOE_THICK = 0.08, 0.05        # claw root half width (across the paw) / half thickness
TOE_CURL = 22.0                       # "claw curve": the tips hook forward by this (deg)
TOE_SINK = 0.10                       # claw roots sink this far into the paw
TOE_FWD, TOE_FAN = 0.15, 0.20         # claws reach forward / fan outward (direction mix)
# ---- BURROW-STALKS: spines measured per view (BACK -> X, SIDE -> Y at the same heights; rows every 10 px):
#   tall: back x 566 @ z 1.31 .. x 612 @ z 3.01 (X 0.72 -> 0.26); side x 1074 @ z 1.02 .. x 1040 @ z 3.02 (Y 0.83 ->
#   0.49); its tuft tops out at y 262 (back, z 3.81) / y 291 (side, z 3.52) -- the views disagree by 0.29; the model
#   splits them (~3.7). short: back roots x 537 / 742 (X +1.01 / -1.04 @ z 0.76), stalk x 524 / 750 @ z 1.36, tuft
#   tips y 397 / 385 (z 2.46 / 2.58); side x 1137 @ z 0.72 .. 1150 @ z 1.82 (Y 1.46 -> 1.59), tip y 401 (z 2.42)
#   v1.1 gate (the orchestrator, against the STALK panel): thick, S-curved, flexible stalks that widen toward the tuft --
#   the spines keep the measured roots / tops and add an S-bend; radius ~1.7x v1 (the sheet orthos draw 0.22-0.37 u
#   wide stalks; the gate asked for roughly double)
STALK_TALL = [(0.82, 0.98, 0.05), (0.88, 1.08, 0.70), (0.74, 1.00, 1.35), (0.52, 0.74, 1.95), (0.42, 0.56, 2.40),
              (0.38, 0.60, 2.84)]                              # "tall stalk path" (an S in the side plane)
STALK_SHORT = [(1.00, 1.40, 0.05), (1.12, 1.48, 0.55), (1.04, 1.62, 1.05), (1.10, 1.62, 1.50), (1.20, 1.56, 1.82)]
                                      # "short stalk path" (L; R = mirror)
STALK_R_TALL = [(0.0, 0.28), (0.15, 0.24), (0.45, 0.22), (0.75, 0.23), (0.92, 0.26), (1.0, 0.28)]    # "stalk width"
STALK_R_SHORT = [(0.0, 0.23), (0.15, 0.19), (0.50, 0.18), (0.80, 0.19), (0.93, 0.21), (1.0, 0.22)]   # (radius by arc)
STALK_K = 0.12                        # stalk roots melt into the burrow over this
SF_ROOT, SF_TIP = 0.30, 0.86          # "stalk gradient": accent root band below / paler band above (arc fraction from
                                      #   the burrow; sheet: short-stalk rows z 0.83-1.03 sample 181,30,101 accent,
                                      #   z 1.43+ 231,47,132 primary)
# ---- PETAL TUFTS (flower-like tips): back view tall tuft 0.70 u wide (y 302, x 582-651), short tufts 0.44-0.60 wide
#   v1.1 gate: FEATHERY, FLAME-LIKE open fans (STALK panel: long pointed tongues growing out of the widening stalk,
#   fanning up + out, all swept one way), not closed cups: ring petals root at ring_frac of the stalk-tip radius,
#   alternate inner / outer spread, curl outward, and the whole fan leans by 'lean'
TUFTS = {"tall": {"n": 8, "len": 1.00, "hw": 0.16, "thick": 0.035, "spread": (10.0, 26.0), "curl": 26.0, "up": 0.60,
                  "lean": (0.0, 0.12), "ring_frac": 0.85},
         "short": {"n": 7, "len": 0.75, "hw": 0.125, "thick": 0.032, "spread": (12.0, 28.0), "curl": 28.0, "up": 0.40,
                   "lean": (0.15, 0.14), "ring_frac": 0.85}}   # petals, length, half width, half thickness, (inner,
                                      #   outer) spread (deg), outward curl (deg), share of vertical in the tuft axis,
                                      #   (X, Y) lean, ring radius / stalk-tip radius
PETAL_LEN_JIT = 0.18                  # ring petals are this much shorter at most (varied)
PETAL_SINK = 0.05                     # petal roots sink this far into their socket
PETAL_STATIONS, PETAL_RING = 10, 12   # petal mesh density (stations along x vertices round)
TUFT_WIDEST = 0.30                    # tuft petals are widest this share up (flame tongues); crown petals 0.35
TUFT_BASE = 0.80                      # tuft petal root width share (broad roots merge into one flame body)
TUFT_DROP = 0.14                      # ring petals root this far below the stalk tip on its side (a calyx hiding the cut end)
PALE_S = 0.85                         # "pale streak length": the pale midband runs this share up each petal
# ---- PETAL CROWN on the snout tip: SIDE view the crown spans x 841-954, y 361-492 (Y -1.50..-0.38, z 1.51..2.82):
#   petals up (to z 2.82), back (to Y -0.41), forward-down (to Y -1.50, z 1.82); FRONT view +-0.57 u sideways
CROWN_PETALS = [((0.0, 0.05, 1.0), 0.62), ((0.0, 0.95, 0.30), 0.58), ((0.0, -0.75, -0.65), 0.55),
                ((0.85, 0.10, 0.50), 0.56), ((0.75, -0.60, -0.25), 0.50)]   # "crown petals": (direction, length); the
                                      #   last two are mirrored to the right side -> 7 petals
CROWN_HW, CROWN_THICK, CROWN_CURL = 0.11, 0.03, 12.0
MAT_SPECULAR = 0.15                    # "sheen": Principled specular level (0.5 = Blender default); matte like the sheet
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_FLOOR = {"body": 0.50, "burrow": 0.45, "burrow_hole": 0.40, "claw": 0.55, "stalk_root": 0.50, "stalk": 0.55,
            "stalk_tip": 0.55, "petal": 0.60, "petal_pale": 0.60, "eye_dark": 0.70, "eye_white": 0.70}
                                      # "AO floor" per region (house style: nothing bakes to black grime)
# ---- rig
OWN_TAU = 0.08                        # soft part ownership for weights
WEIGHT_BODY_Z0 = 0.15                 # below this height the shell belongs to the (static) burrow
WEIGHT_CUT_SLOPE = 8.0                # how sharply (distance per unit below WEIGHT_BODY_Z0; run 2 with slope 1 left 15 % body weight)
STALK_BONES = 3
# ---- clips (PLACEHOLDER: movement not yet given -- OPEN-QUESTIONS N3). 24 fps; integer harmonics per loop.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH = 0.008                   # body rise x H (2 breaths per loop)
IDLE_ROLL = 1.0                       # body roll deg
IDLE_HEAD_TURN, IDLE_HEAD_NOD = 8.0, 2.0   # "head turn" yaw deg (1 cyc) / nod deg (2 cyc)
STALK_IDLE = {"tall": ((2.0, 6.0), (1.5, 5.0)), "short": ((2.5, 7.0), (2.0, 6.0))}   # "stalk sway": (root, tip) deg
                                      #   about X (1 cyc) / about Y (2 cyc)
STALK_LAG = 0.8                       # sway travels root -> tip (rad per bone)
CROWN_WOBBLE = (4.0, 3.0)             # crown wobble deg (X 2 cyc, Y 3 cyc)
WALK_N = 48                           # "slide loop": 2 s
WALK_BOB = 0.015                      # body rise x H (2 per loop)
WALK_LEAN = 3.0                       # forward lean deg (+ a 1.5 deg pitch bob)
WALK_ROLL = 2.0                       # body roll deg
WALK_DIG = 14.0                       # "claw dig": alternate paw lift-and-scoop deg
WALK_TRAIL = (3.0, 9.0)               # "stalk trail": stalks sweep back (root, tip deg)
WALK_WAVE = (1.5, 5.0)                # trailing ripple (root, tip deg), 2 cyc
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
          "design/reference/shadow_mole-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (back + side); floor z = 0 under the "
                   "burrow", "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


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


def mirX(V):
    V = np.array(V, float)
    V[:, 0] *= -1.0
    return V


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. shell SDF
t_sdf = time.time()
SIDES = (("L", 1.0), ("R", -1.0))
ZUP = np.array([0.0, 0.0, 1.0]); XAX = np.array([1.0, 0.0, 0.0]); YAX = np.array([0.0, 1.0, 0.0])

# ---- burrow: fill disc + rim puffs + outer scallops (deterministic jitter)
BC = np.array([BURROW_C[0], BURROW_C[1], 0.0])
PUFFS = []
for ring, (rr, nn, pr, ph, pz) in enumerate((PUFF_RING, PUFF_OUTER)):
    for i in range(nn):
        j1, j2, j3 = (FP.hash01(i + 31 * ring, s_) - 0.5 for s_ in (1.0, 2.0, 3.0))
        az = 2.0 * math.pi * (i + 0.5 * ring + 0.35 * j1) / nn
        r_ = rr * (1.0 + 0.25 * PUFF_JIT * j2)
        sc = 1.0 + PUFF_JIT * j3
        c = BC + np.array([r_ * math.sin(az), -r_ * math.cos(az), pz])
        PUFFS.append((c, np.array([pr * sc, pr * sc, ph * sc])))


def sd_fill(P):
    return SD.sd_ellipsoid(P, BC + np.array([0.0, 0.0, BURROW_FILL[2]]), (BURROW_FILL[0], BURROW_FILL[0], BURROW_FILL[1]))


def sd_burrow(P):
    d = sd_fill(P)
    for c, r in PUFFS:
        d = SD.smin(d, SD.sd_ellipsoid(P, c, r), BURROW_K)
    return d


# ---- trunk / head / snout
_tt = math.radians(TRUNK_TILT)
TR_U = np.array([0.0, -math.sin(_tt), math.cos(_tt)])         # trunk up axis (top toward the front)
TR_V = np.array([0.0, math.cos(_tt), math.sin(_tt)])          # trunk depth axis
TR_C = np.array(TRUNK_C, float)


def sd_trunk(P):
    Q = P - TR_C
    s = Q @ TR_U; v = Q @ TR_V
    f = 1.0 - TRUNK_TAPER * np.clip(s / TRUNK_R[2], -1.0, 1.0)
    L = np.stack([Q[:, 0] / f, v / f, s], 1)
    return SD.sd_ellipsoid(L, (0.0, 0.0, 0.0), TRUNK_R) * f


_ht = math.radians(HEAD_TILT)
HEAD_FRAME = np.array([[1.0, 0.0, 0.0], [0.0, -math.cos(_ht), math.sin(_ht)], [0.0, math.sin(_ht), math.cos(_ht)]])


def sd_head_only(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R, HEAD_FRAME)


def sd_snout(P):
    return SD.sd_round_cone(P, SNOUT_A, SNOUT_B, SNOUT_R[0], SNOUT_R[1])


def sd_head(P):
    return SD.smin(sd_head_only(P), sd_snout(P), SNOUT_K)


def sd_core(P):
    """trunk + head + snout in the grid's order (the anchor surface for the eyes / crown / facing landmark)"""
    return SD.smin(SD.smin(sd_trunk(P), sd_head_only(P), HEAD_K), sd_snout(P), SNOUT_K)


# ---- claws
CLAWS = {}
for s, sg in SIDES:
    p_, y_ = math.radians(CLAW_PITCH), math.radians(sg * CLAW_YAW)
    Rz = np.array([[math.cos(y_), -math.sin(y_), 0.0], [math.sin(y_), math.cos(y_), 0.0], [0.0, 0.0, 1.0]])
    Wd = Rz @ np.array([1.0, 0.0, 0.0])
    Nn = Rz @ np.array([0.0, -math.cos(p_), math.sin(p_)])
    Up = Rz @ np.array([0.0, math.sin(p_), math.cos(p_)])
    c = np.array([sg * CLAW_C[0], CLAW_C[1], CLAW_C[2]])
    toes = []
    for j in (-1, 0, 1):
        a = c + Wd * (j * TOE_SPREAD) - Up * TOE_ROOT_DOWN + Nn * (0.4 * CLAW_R[1])
        d = unit(-Up + Nn * TOE_FWD + Wd * (j * TOE_FAN))
        toes.append((a, a + d * TOE_LEN, d))
    CLAWS[s] = {"c": c, "frame": np.array([Wd, Nn, Up]), "toes": toes}


def sd_paw(P, s):
    return SD.sd_ellipsoid(P, CLAWS[s]["c"], CLAW_R, CLAWS[s]["frame"])


def sd_claw(P, s):
    return sd_paw(P, s)          # the three pointed claw tips are closed parts (section 4)


def claw_box(s):
    pts = [CLAWS[s]["c"]] + [p for t in CLAWS[s]["toes"] for p in t[:2]]
    A = np.array(pts)
    return A.min(0) - 0.26, A.max(0) + 0.26


# ---- stalks
STALKS = [FP.Tube("stalk.tall", STALK_TALL, STALK_R_TALL),
          FP.Tube("stalk.L", STALK_SHORT, STALK_R_SHORT),
          FP.Tube("stalk.R", mirX(STALK_SHORT), STALK_R_SHORT)]
STALK_KIND = {"stalk.tall": "tall", "stalk.L": "short", "stalk.R": "short"}
for t_ in STALKS:
    v_ = sd_burrow(t_.C)
    i0 = int(np.argmax(v_ > 0))                                   # first spine station out of the burrow
    t_.s0 = float(t_.arc[i0])

GROUPS = ["burrow", "trunk", "head", "claw.L", "claw.R", "stalk.tall", "stalk.L", "stalk.R"]
CGROUPS = {"burrow": ["burrow"], "body": ["trunk", "head"], "claw": ["claw.L", "claw.R"],
           "stalk": ["stalk.tall", "stalk.L", "stalk.R"]}


def stalk_cols(P):
    return [t_.eval(P) for t_ in STALKS]


def group_sdf(P):
    ST = stalk_cols(P)
    cols = [sd_burrow(P), sd_trunk(P), sd_head(P), sd_claw(P, "L"), sd_claw(P, "R")] + [e["d"] for e in ST]
    return np.stack(cols, 1), ST


boxes = [(BC - np.array([1.95, 1.95, 0.1]), BC + np.array([1.95, 1.95, 0.5])),
         (TR_C - np.array(TRUNK_R) * 1.1, TR_C + np.array(TRUNK_R) * 1.1)]
for t_ in STALKS:
    boxes.append(t_.bbox(0.05))
GLO = np.min([b[0] for b in boxes], 0) - 0.05
GHI = np.max([b[1] for b in boxes], 0) + 0.05
GLO[2] = -0.06
g = SD.Grid(GLO, GHI, SDF_STEP, SDF_BAND)
g.apply(sd_fill, BC - np.array([BURROW_FILL[0], BURROW_FILL[0], 0.3]), BC + np.array([BURROW_FILL[0], BURROW_FILL[0], 0.25]), 0.0)
for c, r in PUFFS:
    g.apply(lambda P, c=c, r=r: SD.sd_ellipsoid(P, c, r), c - r, c + r, BURROW_K)
g.apply(sd_trunk, TR_C - np.array(TRUNK_R) * 1.05, TR_C + np.array(TRUNK_R) * 1.05, TRUNK_K)
g.apply(sd_head_only, np.array(HEAD_C) - max(HEAD_R), np.array(HEAD_C) + max(HEAD_R), HEAD_K)
g.apply(sd_snout, np.minimum(SNOUT_A, SNOUT_B) - SNOUT_R[0], np.maximum(SNOUT_A, SNOUT_B) + SNOUT_R[0], SNOUT_K)
for s, _ in SIDES:
    c = CLAWS[s]["c"]
    g.apply(lambda P, s=s: sd_paw(P, s), c - max(CLAW_R), c + max(CLAW_R), CLAW_K)
for t_ in STALKS:
    lo_, hi_ = t_.bbox(0.0)
    g.apply(lambda P, t_=t_: t_.eval(P)["d"], lo_, hi_, STALK_K)
g.floor(0.0)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "puffs": len(PUFFS),
                 "stalks": {t_.name: {"length": round(t_.L, 4), "exit_arc": round(t_.s0, 4)} for t_ in STALKS},
                 "high_bbox": [HV.min(0).round(4).tolist(), HV.max(0).round(4).tolist()],
                 "seconds": round(time.time() - t_sdf, 1)}
print("SDF", json.dumps(report["sdf"]))
assert crumbs["dropped_verts"] < 0.002 * (len(HV) + crumbs["dropped_verts"]), ("a body part fell off the shell", crumbs)

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
_hb = HV.max(0) - HV.min(0); _lb = LV.max(0) - LV.min(0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_low_over_high": (_lb / _hb).round(4).tolist(),
                    "seconds": round(time.time() - t, 1)}
assert min(_lb / _hb) > 0.97, ("decimation folded the shell", report["retopo"])
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))
print("RETOPO", json.dumps(report["retopo"]))


# =========================================================================== 3. region fields + iso cuts on the shell
def cgroup_fields(D):
    """colour-group ownership: f_g = min(other groups) - min(group g's columns) (> 0 where g owns the surface)"""
    out = {}
    for gname, members in CGROUPS.items():
        own_ = [GROUPS.index(m) for m in members]
        oth = [j for j in range(len(GROUPS)) if j not in own_]
        out["f_" + gname] = D[:, oth].min(1) - D[:, own_].min(1)
    return out


def sfrac_field(D, ST):
    js = np.argmin(D[:, 5:], 1)
    out = np.zeros(len(D))
    for k, t_ in enumerate(STALKS):
        m = js == k
        out[m] = (ST[k]["s"][m] - t_.s0) / max(t_.L - t_.s0, 1e-9)
    return out


DGR, STL = group_sdf(LV)
FIELDS = cgroup_fields(DGR)
FIELDS["sfrac"] = sfrac_field(DGR, STL)
FIELDS["rb"] = np.hypot(LV[:, 0] - BC[0], LV[:, 1] - BC[1])
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
gate_of = lambda gname: (lambda a, b: a[LAY["f_" + gname]] > -TOL and b[LAY["f_" + gname]] > -TOL)
CUTS = [("f_burrow", 0.0, None), ("f_claw", 0.0, None), ("f_stalk", 0.0, None),
        ("rb", HOLE_R, gate_of("burrow")), ("sfrac", SF_ROOT, gate_of("stalk")), ("sfrac", SF_TIP, gate_of("stalk"))]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
CNAMES = list(CGROUPS)
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in CNAMES], 1), 1)
shell_reg = np.array([CNAMES[o] for o in owner], dtype=object)
shell_reg[(shell_reg == "burrow") & (FVAL["rb"] < HOLE_R)] = "burrow_hole"
_st = shell_reg == "stalk"
shell_reg[_st & (FVAL["sfrac"] < SF_ROOT)] = "stalk_root"
shell_reg[_st & (FVAL["sfrac"] > SF_TIP)] = "stalk_tip"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: eyes, petal tufts, petal crown
PARTS = []
FACE = {}
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def project(pts, n):
    out = []
    for p in pts:
        loc, nrm, idx, dd = bvh_c.ray_cast(Vector(p + n * 0.4), Vector(-n), 1.2)
        if loc is None:
            loc = bvh_c.find_nearest(Vector(p))[0]
        out.append(np.array(loc))
    return np.array(out)


def ray_hit(fn, o, d, t_max=8.0, dt=0.005):
    """first entry of the ray o + t d into fn < 0 (bisected to 1e-7) -> (point, outward unit normal)"""
    o = np.asarray(o, float); d = unit(d)
    ts = np.arange(0.0, t_max, dt)
    v = fn(o[None] + ts[:, None] * d[None])
    i = int(np.argmax(v < 0))
    assert v[i] < 0, "ray missed the surface"
    a, b = ts[max(i - 1, 0)], ts[i]
    for _ in range(40):
        m = 0.5 * (a + b)
        if fn((o + m * d)[None])[0] < 0:
            b = m
        else:
            a = m
    p = o + 0.5 * (a + b) * d
    e = 1e-4
    gr = np.array([fn((p + e * ax)[None])[0] - fn((p - e * ax)[None])[0] for ax in np.eye(3)])
    return p, unit(gr)


# ---- eyes (cast from the side onto the head: the side view's drawn eye)
EYE_REGS = ["eye_white"] * EYE_RINGS[0] + ["eye_dark"] * (EYE_RINGS[1] - EYE_RINGS[0])
for s, sg in SIDES:
    S, n_ = ray_hit(sd_core, (sg * 4.0, EYE_YZ[0], EYE_YZ[1]), (-sg, 0.0, 0.0))
    V, F, R = FP.oval_decal(S, n_, EYE_R, project, EYE_UP, EYE_SINK, EYE_DOME, EYE_RINGS[1], 18, EYE_REGS, "eye_dark")
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": n_.round(4).tolist()}


# ---- the claws' three pointed tips (closed curved cones on each claw bone)
for s, sg in SIDES:
    Nn = CLAWS[s]["frame"][1]
    for j, (a, b, d) in enumerate(CLAWS[s]["toes"]):
        V, F, R = FP.petal(a, d, Nn, TOE_LEN - TOE_SINK, TOE_HW, TOE_THICK, TOE_CURL, TOE_SINK, 8, 8, widest=0.05,
                           base_frac=1.0, pale_s=-1.0, regions=("claw", "claw"))
        PARTS.append(FP.Part("clawtip.%s.%d" % (s, j), V, F, R, bone="claw." + s))


# ---- petal tufts on the stalk tips (the R short stalk's tuft = the mirror of the L one)
def tuft_parts(t_, spec, sg_lean=1.0):
    E, T = t_.C[-1], t_.T[-1]
    ring_r = spec["ring_frac"] * float(t_.r[-1])
    A = unit((1.0 - spec["up"]) * T + spec["up"] * ZUP + np.array([sg_lean * spec["lean"][0], spec["lean"][1], 0.0]))
    a_, u_, v_ = FP.frame_from_axis(A, (0.0, -1.0, 0.0))
    out = []
    V, F, R = FP.petal(E, A, u_, spec["len"], spec["hw"], spec["thick"], 6.0, PETAL_SINK, PETAL_STATIONS, PETAL_RING,
                       pale_s=PALE_S, widest=TUFT_WIDEST, base_frac=TUFT_BASE)
    out.append((V, F, R))
    nr = spec["n"] - 1
    for m in range(nr):
        h1, h2, h3 = (FP.hash01(m, s_) for s_ in (11.0, 12.0, 13.0))
        ph = 2.0 * math.pi * (m + 0.25 * (h1 - 0.5)) / nr + 0.3
        rad = math.cos(ph) * u_ + math.sin(ph) * v_
        beta = math.radians(spec["spread"][m % 2] * (0.85 + 0.3 * h2))      # alternate inner / outer: an open fan
        d = unit(A * math.cos(beta) + rad * math.sin(beta))
        L = spec["len"] * (1.0 - PETAL_LEN_JIT * h3)
        V, F, R = FP.petal(E - A * TUFT_DROP + rad * ring_r, d, rad, L, spec["hw"], spec["thick"], spec["curl"] * (0.8 + 0.4 * h2),
                           PETAL_SINK + 0.03, PETAL_STATIONS, PETAL_RING, pale_s=PALE_S, twist_deg=10.0 * (h1 - 0.5),
                           widest=TUFT_WIDEST, base_frac=TUFT_BASE)
        out.append((V, F, R))
    return out, A


TUFT_AXES = {}
for t_ in STALKS:
    if t_.name == "stalk.R":
        continue
    spec = TUFTS[STALK_KIND[t_.name]]
    pp, A = tuft_parts(t_, spec)
    TUFT_AXES[t_.name] = A
    for k, (V, F, R) in enumerate(pp):
        PARTS.append(FP.Part("petal.%s.%02d" % (t_.name.split(".")[1], k), V, F, R, bone=t_.name + ".%d" % (STALK_BONES - 1)))
        if t_.name == "stalk.L":                               # the mirror tuft on the R short stalk
            PARTS.append(FP.Part("petal.R.%02d" % k, mirX(V), [f[::-1] for f in F], R, bone="stalk.R.%d" % (STALK_BONES - 1)))
TUFT_AXES["stalk.R"] = TUFT_AXES["stalk.L"] * np.array([-1.0, 1.0, 1.0])

# ---- petal crown on the snout tip
SN_A = unit(np.array(SNOUT_B) - np.array(SNOUT_A))
HUB = np.array(SNOUT_B, float) + SN_A * (SNOUT_R[1] * 0.55)
_cp = []
for d, L in CROWN_PETALS:
    _cp.append((unit(d), L))
    if abs(d[0]) > 1e-6:
        _cp.append((unit(np.array(d) * np.array([-1.0, 1.0, 1.0])), L))
for k, (d, L) in enumerate(_cp):
    fn_ = SN_A - d * float(np.dot(SN_A, d))
    if np.linalg.norm(fn_) < 0.2:
        fn_ = XAX.copy()
    V, F, R = FP.petal(HUB, d, fn_, L, CROWN_HW, CROWN_THICK, CROWN_CURL, PETAL_SINK, PETAL_STATIONS, PETAL_RING,
                       pale_s=PALE_S)
    PARTS.append(FP.Part("petal.crown.%02d" % k, V, F, R, bone="crown"))
FACE["crown_hub"] = HUB.round(4).tolist()

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
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
REG = ["body", "burrow", "burrow_hole", "claw", "stalk_root", "stalk", "stalk_tip", "petal", "petal_pale",
       "eye_dark", "eye_white"]
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
    mat.use_backface_culling = True                  # opaque closed body: single-sided (glTF doubleSided false)
    bsdf.inputs["Specular IOR Level"].default_value = MAT_SPECULAR   # matte (v1.1 gate: the burrow read as grey stone)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


GLOW_REGIONS = {"stalk_root", "stalk", "stalk_tip", "petal", "petal_pale", "claw"}   # artist 2026-10-09 (M1): "yes glow"
GLOW_CAP = 2.0                        # emissive palette ceiling (phone cap, import notes s7)


def glow_check(pal):
    """M1 answered 2026-10-09 ("yes glow"): exactly the pink regions glow, faintly, under the phone cap"""
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    peak = max([max(c / 255.0 for c in pal["regions"][n]["emission"]) * pal["regions"][n].get("emission_scale", 1.0)
                for n in lit] or [0.0])
    return {"skin": pal["skin"], "glowing_regions": lit, "peak_srgb_x_scale": round(peak, 4),
            "ok": set(lit) == GLOW_REGIONS and peak * pal["material"].get("emission_strength", 1.0) <= GLOW_CAP}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
report["glow"] = glow_check(pal_default)
assert report["glow"]["ok"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, jitter(VALL, FALL))
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the body axis at the face height -> the face front on the midline
FRONT_S, FRONT_N = ray_hit(sd_core, (0.0, -4.0, 1.62), (0, 1, 0))
anchor = np.array([0.0, TRUNK_C[1], 1.62]) - SHIFT
landmark = FRONT_S - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "body axis (0, TRUNK_C.y) at the face height (z 1.62) -> the face front on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE

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
    key = "eyes" if p.name.startswith("eye.") else ("crown_petals" if p.name.startswith("petal.crown") else
                                                     ("claw_tips" if p.name.startswith("clawtip.") else "tuft_petals"))
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
_shell_reg_tris = {}
for r_ in sorted(set(shell_reg)):
    _shell_reg_tris[r_] = int((shell_reg == r_).sum())
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "shell_by_region": _shell_reg_tris, "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The SDF shell (burrow + mole + claws + stalks) "
                            "carries ~2/3 of the budget; the 26 petal blades (tufts + crown) the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, eye decals, petals, claw tips)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1),
                                             "sheet_back": {"height_px": 382, "width_px": 370},
                                             "sheet_side": {"height_px": 352, "width_px": 367}},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
_body_v = (np.argmax(np.stack([FIELDS["f_" + c_] for c_ in CNAMES], 1), 1) == CNAMES.index("body"))
report["measure"]["body_top_z_rest"] = round(float(LV[_body_v, 2].max()), 4)   # sheet back-view dome apex z 1.91
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
low["conquest_source"] = "from scratch: design/reference/shadow_mole-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (pink stalks / petals / claws glow faintly, palette tiers 0.15-0.50)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_tall = STALKS[0]
_tip = _tall.C[-1]
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.75, -1.65, 1.15]) - SHIFT).tolist(), (np.array([0.75, 0.15, 2.85]) - SHIFT).tolist()],
    "stalk_tip": [(_tip - np.array([0.55, 0.55, 0.25]) - SHIFT).tolist(), (_tip + np.array([0.55, 0.55, 0.85]) - SHIFT).tolist()],
    "claw": [(CLAWS["L"]["c"] - np.array([0.40, 0.40, 0.55]) - SHIFT).tolist(), (CLAWS["L"]["c"] + np.array([0.40, 0.40, 0.35]) - SHIFT).tolist()],
    "burrow": [(BC - np.array([1.9, 1.9, 0.0]) - SHIFT).tolist(), (BC + np.array([1.9, 1.9, 0.6]) - SHIFT).tolist()],
    "views": {"face": [75.0, 6.0], "stalk_tip": [160.0, 8.0], "claw": [20.0, 12.0], "burrow": [30.0, 32.0]},
    "axis_xy": [float(SHEET_AXIS_X - SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "face", "iso_cuts")}, default=str))
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
allf = np.ones(nf, bool)
alltex_n = texels_of(allf, RN)
reg_tex_a = {j: texels_of(rid == j, RA) for j in range(len(REG))}
alltex_a = np.zeros(RA * RA, bool)
for m_ in reg_tex_a.values():
    alltex_a |= m_
ok_a = cov_a & alltex_a
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean_before_floor": round(float(pa[ok_a, 0].mean()), 4),
    "ao_p05_before_floor": round(float(np.percentile(pa[ok_a, 0], 5)), 4)})
# AO floor per region class (house style: nothing bakes to black grime). Texels shared by two regions take the
# higher floor (the max is order-independent).
aocls = {"floor": dict(AO_FLOOR), "per_region": {}}
for j, n in enumerate(REG):
    m_ = reg_tex_a[j] & ok_a
    if m_.any():
        aocls["per_region"][n] = {"texels": int(m_.sum()), "p05_before": round(float(np.percentile(pa[m_, 0], 5)), 4),
                                  "le_0.02_before": int((pa[m_, 0] <= 0.02).sum())}
        pa[m_, 0:3] = np.maximum(pa[m_, 0:3], AO_FLOOR[n])
aocls["p05_after_floor"] = round(float(np.percentile(pa[ok_a, 0], 5)), 4)
aocls["min_after_floor"] = round(float(pa[ok_a, 0].min()), 4)
bstats["ao_classes"] = aocls
print("AO_CLASSES", json.dumps(aocls))
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_normal_twin.npy" if DIGEST_ONLY else "_normal_main.npy")), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_ao_twin.npy" if DIGEST_ONLY else "_ao_main.npy")), pa[:, :1])
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
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


BONES = [("burrow", P_((BC[0], BC[1], 0.0)), P_((BC[0], BC[1], 0.35)), "root"),
         ("body", P_((0.0, TRUNK_C[1], 0.30)), P_((0.0, -0.12, 1.40)), "root"),
         ("head", P_((0.0, -0.12, 1.40)), P_(SNOUT_B), "body"),
         ("crown", P_(HUB), P_(HUB + SN_A * 0.35), "head")]
for s, sg in SIDES:
    BONES.append(("claw." + s, P_((sg * 0.48, -0.50, 0.98)), P_(CLAWS[s]["c"]), "body"))
CHAIN = {}
for t_ in STALKS:
    ks = np.linspace(t_.s0, t_.L, STALK_BONES + 1)
    pts = [t_.at(x)[0] for x in ks]
    for k in range(STALK_BONES):
        BONES.append(("%s.%d" % (t_.name, k), P_(pts[k]), P_(pts[k + 1]), "root" if k == 0 else "%s.%d" % (t_.name, k - 1)))
    CHAIN[t_.name] = {"s_surf": t_.s0, "length": t_.L}
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
    e.use_connect = nm.startswith("stalk.") and nm[-1] in "12"
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
GD, STW = group_sdf(Pw)
# the trunk SDF reaches below the floor: without a cut the burrow's floor cap under the mole is body-owned and lifts /
# dips with every bob (v1 run 1: the floor guard fired on 27 / 39 frames). Body + stalk ownership stops at WEIGHT_BODY_Z0.
_zcut = (WEIGHT_BODY_Z0 - Pw[:, 2]) * WEIGHT_CUT_SLOPE        # steep: at z 0 the body / stalk weight is ~exp(-15)
GD[:, 1] = np.maximum(GD[:, 1], _zcut)
GD[:, 5:] = np.maximum(GD[:, 5:], _zcut[:, None])              # same for the stalk roots
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
Ws = np.zeros((b_ - a_, len(DEFORM)))
Ws[:, J["burrow"]] += Wg[:, 0]
Ws[:, J["body"]] += Wg[:, 1]
Ws[:, J["head"]] += Wg[:, 2]
Ws[:, J["claw.L"]] += Wg[:, 3]
Ws[:, J["claw.R"]] += Wg[:, 4]
STALK_W = {}
for k, t_ in enumerate(STALKS):
    col = Wg[:, 5 + k]
    s0 = t_.s0
    Lc = t_.L - s0
    Wv = K.vine_weights(np.clip(STW[k]["s"] - s0, -0.3, Lc), Lc, STALK_BONES, parent_s0=-0.15)
    Ws[:, J["burrow"]] += col * Wv[:, 0]
    for kb in range(STALK_BONES):
        Ws[:, J["%s.%d" % (t_.name, kb)]] += col * Wv[:, kb + 1]
    STALK_W[t_.name] = {"length": round(t_.L, 4), "root_arc": round(s0, 4), "dominant_verts": int((np.argmax(Wg, 1) == 5 + k).sum())}
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
                  "stalks": STALK_W,
                  "rule": "shell: soft SDF ownership (tau %.2f): burrow -> burrow (static), trunk -> body, head + snout "
                          "-> head, each paw + toes -> claw.<side>, each stalk -> rigkit.vine_weights along its own spine "
                          "from where it leaves the burrow (the root blends into the burrow); eyes rigid on head, crown "
                          "petals on crown, each tuft's petals on its stalk's last bone" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame with no action bound (pass 1, floor
#      guard lifting the body if any vertex dips below z 0), keyed from the stored channels (pass 2, frame N+1 = frame 1),
#      measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]
KEY_BONES = DEFORM
SPH = {t_.name: 2.39996 * i + 0.7 for i, t_ in enumerate(STALKS)}   # golden-angle phases: no two stalks in step


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def world_rot(rots):
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(axis)) @ Rw
    return Rw


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ world_rot(rots) @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def claw_sg(s):
    return 1.0 if s == "L" else -1.0


def idle_pose(u):
    P = {}
    rise = HH * IDLE_BREATH * 0.5 * (1.0 - math.cos(TAU * 2 * u))
    P["burrow"] = (None, [], None)
    P["body"] = ((0.0, 0.0, rise), [(YAX, IDLE_ROLL * math.sin(TAU * u + 0.4))], None)
    P["head"] = (None, [(ZUP, IDLE_HEAD_TURN * math.sin(TAU * u)), (XAX, IDLE_HEAD_NOD * math.sin(TAU * 2 * u - 0.9))], None)
    P["crown"] = (None, [(XAX, CROWN_WOBBLE[0] * math.sin(TAU * 2 * u + 0.5)), (YAX, CROWN_WOBBLE[1] * math.sin(TAU * 3 * u))], None)
    for s, _ in SIDES:
        P["claw." + s] = (None, [(XAX, -2.0 * (0.5 + 0.5 * math.sin(TAU * 2 * u + (0.0 if s == "L" else math.pi))))], None)
    for t_ in STALKS:
        ph = SPH[t_.name]
        ax_, ay_ = STALK_IDLE[STALK_KIND[t_.name]]
        for k in range(STALK_BONES):
            rx = K.vine_wave(u, k, STALK_BONES, ax_[0], ax_[1], STALK_LAG, 1, ph)
            ry = K.vine_wave(u, k, STALK_BONES, ay_[0], ay_[1], STALK_LAG, 2, ph + 1.3)
            P["%s.%d" % (t_.name, k)] = (None, [(XAX, rx), (YAX, ry)], None)
    return P, {"lift": rise}


def walk_pose(u):
    """in-place burrow-slide: two bobs per loop. The body rises with each bob, leans into the slide and rolls; the
    paws dig alternately (lift forward-up, scoop back to rest); every stalk trails back with a travelling ripple; the
    burrow stays on the floor."""
    P = {}
    rise = HH * WALK_BOB * 0.5 * (1.0 - math.cos(TAU * 2 * u))
    P["burrow"] = (None, [], None)
    P["body"] = ((0.0, 0.0, rise), [(XAX, WALK_LEAN + 1.5 * math.sin(TAU * 2 * u - 0.6)),
                                    (YAX, WALK_ROLL * math.sin(TAU * u))], None)
    P["head"] = (None, [(XAX, -0.8 * WALK_LEAN + 1.0 * math.sin(TAU * 2 * u - 1.2)), (ZUP, 2.5 * math.sin(TAU * u))], None)
    P["crown"] = (None, [(XAX, -3.0 + 3.0 * math.sin(TAU * 2 * u - 1.8))], None)
    for s, _ in SIDES:
        st = 0.5 + 0.5 * math.sin(TAU * u + (0.0 if s == "L" else math.pi))
        P["claw." + s] = (None, [(XAX, -WALK_DIG * st)], None)
    for t_ in STALKS:
        ph = SPH[t_.name]
        for k in range(STALK_BONES):
            fk = k / max(STALK_BONES - 1, 1)
            trail = WALK_TRAIL[0] + (WALK_TRAIL[1] - WALK_TRAIL[0]) * fk
            wave = K.vine_wave(u, k, STALK_BONES, WALK_WAVE[0], WALK_WAVE[1], 0.7, 2, ph)
            sway = K.vine_wave(u, k, STALK_BONES, 1.0, 3.0, 0.7, 1, ph + 2.0)
            P["%s.%d" % (t_.name, k)] = (None, [(XAX, -(trail + wave)), (YAX, sway)], None)
    return P, {"lift": rise}


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def apply_pose(P, guard_z):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        loc, rots, sc = P.get(bn, (None, [], None))
        dw = np.zeros(3) if loc is None else np.asarray(loc, float)
        if bn == "body":
            dw = dw + np.array([0.0, 0.0, guard_z])
        pb.location = local_loc(bn, dw)
        pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
        pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)


def bake_clip(name, N, pose_fn):
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames, guard, info = [], [], []
    for f in range(N):
        P, inf = pose_fn(f / N)
        gz = 0.0
        for it in range(6):
            apply_pose(P, gz)
            bpy.context.view_layer.update()
            mz = float(eval_coords(low)[:, 2].min())
            if mz >= -1e-6:
                break
            gz += -mz + FLOOR_EPS
        frames.append({bn: (rig.pose.bones[bn].location.copy(), rig.pose.bones[bn].rotation_quaternion.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
        guard.append(gz); info.append(inf)
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
    return act, rows, guard, info


CLIPS = {"idle": (IDLE_N, idle_pose), "walk": (WALK_N, walk_pose)}
NEW_ACTS, key_rows, GUARD, INFO = {}, [], {}, {}
t_clip = time.time()
for cn, (N, fn) in CLIPS.items():
    NEW_ACTS[cn], rows, GUARD[cn], INFO[cn] = bake_clip(cn, N, fn)
    key_rows += rows
DIG["keys"] = sha(np.array(key_rows))
TIPB = {t_.name: "%s.%d" % (t_.name, STALK_BONES - 1) for t_ in STALKS}
_bur = np.arange(len(VALL))[(Wt[:, J["burrow"]] > 0.999)]
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
    bur_move = 0.0
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        bur_move = max(bur_move, float(np.abs(C[_bur] - VALL[_bur]).max()) if len(_bur) else 0.0)
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "tips": {n_: list(pbs[b_].tail) for n_, b_ in TIPB.items()},
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - REST_H["body"][2]
    tips = {n_: np.array([r["tips"][n_] for r in R_]) for n_ in TIPB}
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "lowest_point_range": [round(float(g_("minz").min()), 4), round(float(g_("minz").max()), 4)],
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "burrow_static_max_move": round(bur_move, 6),
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "tip_p2p": {n_: round(float(np.linalg.norm(v.max(0) - v.min(0))), 4) for n_, v in tips.items()}}
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
rig["conquest_rig"] = ("shadow_mole v1: root > burrow (static) ; root > body > head > crown ; body > claw.{L,R} ; "
                       "root > stalk.{tall,L,R}.{0,1,2} (3 vine chains x 3 bones)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (movement not yet given by the artist, OPEN-QUESTIONS N3): idle = stalk sway "
                               "(4 s: de-synced root->tip waves per stalk, body breathing rise + roll, a head turn + nod, "
                               "crown wobble); walk = in-place burrow-slide (2 s: body bobs / leans / rolls, the paws dig "
                               "alternately, stalks trail back with a ripple; the burrow stays on the floor; root still)")
low["conquest_locomotion"] = "burrower: in-place burrow-slide (the game glides the unit)"
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
              "structure": "armature identity + skinned 'shadow_mole' (one opaque body material); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief 2026-10-09)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
