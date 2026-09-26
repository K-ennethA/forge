"""Duskmaw v4 (hero, Conquest character_id 'monster'): the shadow-lord rework, one headless run.

    blender --background source-copies/hero-monster.blend --factory-startup --python improve/duskmaw_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette only: no bake, no rig -- fast look loop)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)
        [--out-root <dir>]               (determinism twin: writes improved/ + rigged/ under <dir>)

Artist spec (design/review-log.md, verbatim there). Still in force from v2/v3: an idle CHOMP, a GLIDE, a crisp shadow figure
(Aku / Father (KND) / Darkrai), the eyes are the face (no mouth under them), a RED lip frame + teeth round the body maw with
a YELLOW-ORANGE inside (hotter throat), the mouth CLOSED from behind, the mid-arm/elbow thickness (v3).
v4 (2026-09-26 "Duskmaw v4 feedback" + design/reference/duskmaw-v4-maw-curve-annotation.png + duskmaw-v4-shadowlord-
reference.webp): "his center is now too box like ... like we just put a box on him"
  (1) NO BELLY BOX: the maw is carved into a curving elliptical trunk -- the waist curves in above and below the bite line
      (the blue curves) and the mouth's sides follow it (the lips' outer edge sits at MAW_WRAP x the trunk half-width, so
      the mouth wraps ~57 deg round each side); red frame, yellow-orange cavity, hot throat, closed back bowl kept;
  (2) the top edge of the red lip line is JAGGED (irregular notches, the blue zigzag); the lower teeth are v3's exactly;
  (3) a LONG SLENDER trunk (the reference's tapering shadow-lord trunk): TORSO_STRETCH units of extra trunk under the
      chest, the trunk section designed slim (TRUNK_* tables) instead of v3's box;
  (4) FLOOR TENDRILS instead of v3's up-spikes: tapered, flattened tubes radiating OUT from the hem along the floor,
      curling at the tips (sideways spirals; three curl up off the floor like the reference's fiddleheads); the core
      floor ring is v3's; three of them curl slowly in the idle;
  (5) ARMS toward the reference: procedural hanging arms from the chest side -- the thick upper arm (v3's elbow
      thickness), a long forearm slimming to the wrist, four hooked claw fingers, flame-like wisps trailing off the
      forearm.

Reads (never writes): source-copies/hero-monster.blend (the 553k-tri sculpt, opened).
Outputs:
    improved/duskmaw.blend + .json          hero-tier low, UVs, baked normal/AO, region palette (no rig)
    improved/textures/duskmaw_{normal,ao}.png
    rigged/duskmaw.blend + .json            + shadow-lord rig (18 deform bones + contract root), clips idle (chomp) + walk (glide)
    rigged/duskmaw.glb                      identity-scale export (natural scale)
v3's outputs are kept beside v4 as improved/duskmaw_v3.* and rigged/duskmaw_v3.* (before column), v2's as *_v2.*.

Pipeline:
  1. sculpt main shell -> origin (XY bbox centre, floor z = 0). Front = -Y. The v3 floor ring is read off the sculpt's
     skirt; then the sculpt moves up TORSO_STRETCH (the "new frame"): only its part above Z_KEEP (chest, shoulders with
     the raised arm spikes, neck, head, hat) is kept.
  2. SDF REBUILD (Blender geometry-node SDF grids, voxel VOX): (sculpt - everything below Z_KEEP) U procedural TRUNK (a
     radial solid: rounded floor hem -> bell flare -> elliptical trunk (TRUNK_* tables, PCHIP) -> blended into the
     sculpt's chest section) U the two ARM tubes; minus the MAW (the opening = the hole prism minus the teeth prisms,
     + the cavity = cavity outline x the skin inset by T_TOOTH x a back BOWL). Masked SDF mean (face, base flare, chest
     junction, arm roots; never the maw). Then the CRISP parts -- floor TENDRILS (floor-cut), CLAWS, WISPS -- joined by a
     smooth-min fillet (K_BLEND) so they grow out of the body without being eroded.
  3. LOW: collapse decimation (deterministic) -> main shell -> re-centre; the eye patch is refined (edges <= EYE_EDGE).
  4. region FIELDS on the low + ISO-CONTOUR CUTS: eyes, the maw frame (jagged top) / teeth / skin depth, the tendrils,
     ember spike tips. Then the space map T (height + neck) on the low and the high.
  5. regions -> palettes.store_regions; paint from palettes/duskmaw/default.json; Smart UV; bake normal + AO.
  6. RIG: base (ground ring, never keyed) -> sway -> jaw | spine -> chest -> head -> crown, raised arm spikes arm+blade x2,
     hanging arms limb+fore x2, three tendril-curl bones + contract 'root'. Analytic weights (pre-T coordinates, bones
     mapped through T). Clips keyed every frame from closed-form curves (seam 0 by construction): idle = float + one
     CHOMP + three tendril curls; walk = GLIDE (lean, bob, arms/crown trailing; the ground ring never moves).
  7. identity-scale glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, heapq
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402  (read-only use)
import palettes as PAL  # noqa: E402  (read-only use)

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun). Lengths are in units (the natural
# scale, no refit). "sculpt frame" = heights on the original sculpt (moved up TORSO_STRETCH at load, see below); every
# other height is in the v4 "new frame" (the final figure before the HEIGHT_K / neck map T). Angles: degrees from the
# FRONT (-Y), both sides.
UNIT = "duskmaw"
CHAR_ID = "monster"
TRI_BUDGET = [25000, 35000]           # declared hero-tier window (contract tri_budget)
LOW_TRIS = 25000                      # "mesh detail" (decimation target before the colour cuts + eye refinement)
VOX = 0.07                            # "surface resolution": SDF voxel size of the rebuilt high surface
SDF_BAND = 12                         # SDF narrow band (voxels) -- wide enough for the masked smoothing to move surfaces
# ---- v4 PROPORTIONS ("make his torso long and more slender he seems such a box now")
TORSO_STRETCH = 6.5                   # "torso length": extra trunk height under the chest (the kept sculpt moves up this)
Z_KEEP_SCULPT = 13.0                  # the sculpt is kept above this sculpt-frame height (the chest's lower part); below it
                                      # (legs, skirt spikes, old ring maw, throat, v3's hanging arms + hands) all is procedural
HEIGHT_K = 1.10                       # "height": z scale about the floor (v3's +10 %)
NECK_K = 0.85                         # "neck thickness": radial scale of the neck column (v3)
NECK_Z = (15.9, 16.9, 19.9, 20.7)     # ... full inside the middle heights, ramped at the ends (sculpt frame)
NECK_R = (2.7, 4.2)                   # ... full inside the first radius, off beyond the second
NECK_STRETCH = 0.5                    # "neck length": extra height between the shoulders and the eyes (v3) ...
NECK_STRETCH_Z = (16.2, 18.2)         # ... over this band (sculpt frame)
# ---- v4 TRUNK: an elliptical section about the waist axis, PCHIP through these heights (new frame)
TRUNK_Z = (5.2, 7.2, 11.9, 16.1)      # "trunk heights": flare top | maw bottom | WAIST | maw top
TRUNK_W = (5.1, 4.8, 3.95, 5.2)       # "torso half-width" at those heights (the waist curve: in at the bite, out below)
TRUNK_F = (4.3, 4.1, 3.9, 4.0)        # "torso front depth"
TRUNK_B = (4.6, 4.2, 3.7, 4.0)        # "torso back depth" (the closed back wall behind the mouth)
Z_CHEST = (16.1, 20.1)                # the trunk blends into the sculpt's chest section over these heights
BODY_OVERLAP = 1.4                    # the procedural trunk runs this far up into the kept chest (junction smoothed)
CH_INSET = 0.35                       # ... sitting this far under the chest skin
# ---- shadow base (v2: "the bottom should almost be uniform like a shadow coming out of the ground") -- v3's floor ring
Z_CUT, Z_BT, BT_INSET = 4.0, 4.6, 0.3 # the sculpt skirt section the floor ring is read from (sculpt frame, unmoved)
HEM_R = 0.45                          # "hem roundness": rounded floor edge radius
FLARE = 0.45                          # "base flare": how far the shadow spreads at the floor (x the skirt radius)
FLARE_POW = 2.0                       # "flare curve": higher = the spread hugs the floor (a bell from TRUNK_Z[0] down)
BASE_ROUND = 0.7                      # "base uniformity": 0 = floor ring follows the skirt lobes, 1 = one round ring
CORE_W = 16                           # back smoothness of the skirt section (x 2.5 deg bins)
THETA_C, D_BLEND = 100.0, 15.0        # ... the smoothed back of the skirt section starts here (v3 closed-back angles)
# ---- v4 MAW (the mouth wraps the curving trunk; x from the waist axis, z new frame, front-projected)
MAW_WRAP = 0.84                       # "mouth wrap": the lips' outer edge at this x the trunk half-width (sin 57 deg;
                                      # 0.883 = 62 deg let the opening outgrow the trunk's front chord at the waist: 2.35 %
                                      # of the side rays crossed the mouth between the jaws -- the chord 2.5 in front of
                                      # the axis must stay wider than the opening)
HOLE_Z = (7.75, 15.5)                 # "maw bottom / top": the opening (inside the lips)
LIP_T = (0.55, 0.6, 0.5)              # "lip width" bottom / top / sides (red frame band round the opening)
HOLE_CR, HOLE_CR_TOP = 0.8, 0.35      # opening corner radii bottom / top
MAW_CR = 1.2                          # frame bottom corner radius
JAG_N = 9                             # "upper lip jaggedness": notches along the top edge of the red lip line (blue zigzag)
JAG_AMP = (0.3, 1.0)                  # ... notch height range (irregular: a golden-ratio sequence, deterministic)
JAG_VALLEY = (0.0, 0.18)              # ... valley height range
PITCH = 2.25                          # tooth pitch (v3's: "the bottom teeth are good" -- they are v3's exactly)
UP_MID_LEN = 3.5                      # "upper fangs": the middle pair (bases 0..+-PITCH) length ...
UP_OUT_LEN, UP_OUT_TIP_X = 2.0, 3.0   # ... the outer pair (base +-PITCH .. the mouth corner): length, tip x (corner fangs)
LO_X = (-1.0, 0.0, 1.0)               # "lower teeth" centres in pitches (in the upper valleys: interlock) -- v3
LO_LEN = (1.8, 2.0)                   # ... length sides / centre -- v3
LO_BASE = 0.8                         # ... base width (x PITCH) -- v3
T_TOOTH = 0.6                         # "tooth thickness": the lips/teeth are plates this thick over the cavity
CAV_EXP = 0.35                        # the cavity reaches this far under the lip plates (beyond the opening outline)
BOWL_C = (0.0, -2.4, 11.6)            # cavity back BOWL: ellipsoid centre (x/y relative to the waist axis, z) ...
BOWL_R = (6.6, 3.2, 6.5)              # ... semi-axes: the back wall sits 0.8 behind the axis (closed back, no see-through)
OPEN_BACK = -1.2                      # the opening prism runs from the front to this y (rel. the axis): inside the cavity
THROAT_Y = -1.0                       # cavity faces behind this y (rel. the axis) are the glowing throat (back wall)
# ---- v4 FLOOR TENDRILS ("lets have them more like tendrils reaching out on the floor")
TEND_N = 10                           # "tendril count" round the hem
TEND_PHASE = 18.0                     # ... first tendril angle (deg from the front; none dead-centre under the maw)
TEND_REACH = (6.0, 9.5)               # "tendril reach": length beyond the floor ring, short .. long (varied)
TEND_ROOT_IN = 2.4                    # ... they start this far inside the floor ring (rooted in the bell)
TEND_R0 = 1.45                        # "tendril thickness": root radius (x 0.85..1.15 varied)
TEND_TIP_R = 0.06                     # ... tip radius
TEND_TAPER = 0.85                     # ... taper exponent (lower = thicker for longer)
TEND_FLAT = 0.72                      # section height / width: they lie flat on the floor
TEND_CURL = (160.0, 270.0)            # "tendril curl": total sideways turn over the tip part (deg, varied)
TEND_CURL_START = 0.42                # ... the curl starts this far along
TEND_DIRS = (1, -1, -1, 1, -1, 1, 1, -1, 1, -1)   # curl direction per tendril (+ = clockwise seen from above)
TEND_UP = (1, 8, 5)                   # "fiddleheads": these tendrils curl UP off the floor at the tip ...
TEND_UP_DEG, TEND_UP_TURN = 215.0, 45.0   # ... vertical curl (deg) and their (smaller) sideways turn
K_BLEND = 0.5                         # smooth-min fillet where tendrils / claws / wisps meet the body
# ---- v4 ARMS (toward the reference: "wispy, tapering, clawed"), L side (x > 0) in the new frame; R mirrors
ARM_PTS = ((4.6, 0.6, 20.0), (7.0, 0.5, 18.9), (9.6, 1.2, 14.2), (9.4, -2.6, 7.6))   # root (in the chest) | "shoulder"
                                      # (leaves the body) | "elbow" | "wrist" -- x/y relative to the waist axis
ARM_RAD = (1.3, 1.15, 1.05, 0.4)      # radius there: the upper arm keeps v3's elbow thickness, the forearm slims to the wrist
PALM_R = 0.62                         # "hand": palm bulb radius
CLAW_N = 4                            # "claw fingers"
CLAW_LEN = (3.0, 3.9, 3.7, 2.8)       # ... lengths (fanned)
CLAW_R = 0.33                         # ... base radius (to a needle tip)
CLAW_SPREAD = 22.0                    # ... fan angle between neighbours (deg), across the outward-forward diagonal
CLAW_HOOK = 0.55                      # ... hook: the tip curls this x the length toward the palm side
WISP_AT = (0.05, 0.4, 0.72)           # "flame wisps": attachment points along the forearm (elbow 0 .. wrist 1)
WISP_LEN = (4.0, 5.4, 4.5)            # ... hanging lengths
WISP_W, WISP_T = 1.7, 0.3             # ... width (along the arm) / thickness at the root, tapering to a point
# ---- masked smoothing (SDF mean, width SM_W voxels x SM_IT iterations, blended by the masks below)
SM_W, SM_IT = 3, 14
FACE_Z = (14.6, 15.4, 20.0, 20.8)     # "face smoothing" height ramps (sculpt frame)
FACE_RAD = (4.2, 5.2)                 # ... radius ramp from the head axis
FACE_AX_Y = 0.0
LOW_Z = (0.3, 1.2, 4.6, 5.4)          # "base smoothing" height ramps (the bell flare)
LOW_RAD = (8.2, 9.2)                  # ... radius ramp (never the claws)
JUNC_Z = (18.1, 18.7, 21.4, 22.0)     # "chest junction smoothing": the trunk meets the kept chest (new frame)
ARMROOT_X = (4.2, 4.8, 7.8, 8.6)      # "arm root smoothing": |x| ramps ...
ARMROOT_Z = (17.1, 17.9, 21.1, 21.9)  # ... height ramps (the shoulder junction, never the forearm)
# ---- eyes (Aku / Father / Darkrai: the eyes ARE the face) -- (x, z) on the front of the face, mirrored, SCULPT frame
EYE_IN = (0.42, 18.72)                # "eye inner corner"
EYE_OUT = (2.05, 19.42)               # "eye outer corner" (higher = angrier slant)
EYE_TOP = 0.10                        # "brow arch": top edge bulge (units)
EYE_BOT = 0.46                        # "eye height": bottom edge depth (units)
EYE_POW = (0.9, 0.65)                 # top / bottom edge shape exponents (lower = fuller toward the corners)
EYE_EDGE = 0.1                        # the eye patch is refined to edges no longer than this before the cut
EYE_BOX = (2.7, 16.6, 20.4)           # refinement box: |x| < 2.7, z in [16.6, 20.4] (sculpt frame), front-facing ...
EYE_NEAR = 0.12                       # ... and only faces within this of an eye outline (or inside)
# ---- recolour
RED_DEPTH = 0.8                       # skin within RED_DEPTH x T_TOOTH of the surface inside the frame = red lip/teeth
BASE_Z = 3.4                          # "shadow base line": the floor darkening ramps up to this height
BASE_DARK = 0.55                      # ... shade multiplier at the floor
TEND_CUT = 0.12                       # a face this far outside the base skin (and on a tendril) is the dark 'base' region
SPIKE_TIPS = True                     # ember tips on the claws / shoulder spikes / hat spikes (dim; the palette 'tips')
SEED_Z = 13.9                         # tip field seed ring height (sculpt frame; v3's 0.45 H)
HAT_APEX_Z = 27.83                    # the hat apex above this is an ember tip (sculpt frame; v3's 0.9 H)
TIP_LEN, TIP_ABS, MIN_SPIKE, SPIKE_RMIN = 0.5, (0.8, 4.0), 1.0, 0.15   # tip burn fraction / clamp / min spike / min radius (x H)
CUT_SNAP = 0.18                       # iso-cut snap (no slivers)
BAKE_CAGE = 0.15
BAKE_RES = (2048, 1024)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest hero ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- rig (heights: new frame unless marked sculpt frame)
BASE_BONE = (0.62, 3.09)              # ground ring bone z range -- never keyed: the shadow stays planted
SWAY_Z = (1.4, 3.6)                   # lean pivot bone
JAW_Z = (5.0, 9.0)                    # lower jaw bone (the trunk below the bite)
CHEST_Z, HEAD_Z, CROWN_Z = (16.08, 21.02), (21.02, 25.35), (25.35, 30.61)   # (sculpt frame; v3's 0.52/0.68/0.82/0.99 H)
RAISED_SH, RAISED_EL, RAISED_TIP = (2.90, 18.55), (7.97, 19.48), (14.20, 17.93)  # raised shoulder spikes (x, z sculpt)
RAISED_X, RAISED_Z = (4.64, 7.53), (15.15, 20.41)  # ... their weight band |x| / z (sculpt frame; v3's ARM_IN/OUT/ZMIN/ZMAX)
W_BAND = 1.0                          # joint softness (upper chain)
BASE_W = (0.2, 3.2)                   # ground ring -> sway blend heights
JAW_W = (5.4, 6.9)                    # sway -> jaw blend heights (below the lower lip frame: lip + lower teeth ride the jaw)
U_W = (0.55, 2.2)                     # upper-jaw split half-width about MAW_MID: across the tooth plates / cheeks + back
ELBOW_BLEND = 0.8                     # hanging arms: limb -> fore blend half-width at the elbow (arc length)
# ---- clips (idle chomp + tendril curls; walk = glide)
IDLE_N = 49                           # idle frames 1..49 (48-frame loop, 2.0 s)
CHOMP = {"open_f": (6, 16), "shut_f": (16, 19), "hold_f": (19, 23), "rest_f": (23, 36)}   # beat (frames)
CHOMP_OPEN = 0.9                      # "chomp wind-up": extra gap at the open peak (units)
CHOMP_SHUT_GAP = -0.45                # tightest upper/lower neighbour pair's tip-to-tip gap when shut (negative = interlock)
CHOMP_UPPER = 0.6                     # share of the travel done by the upper jaw (chest drops) vs the lower jaw (rises)
CHOMP_ARMS = 9.0                      # raised shoulder spikes flare on the bite (deg)
HANG_FLARE, CLAW_FLEX = 5.0, 8.0      # hanging arms flare out / the claws snap forward on the bite (deg)
HANG_SWAY = 2.0                       # hanging arms idle sway (deg)
CURL_IDX = (2, 4, 7)                  # "tendril curl": these tendrils curl slowly in the idle ...
CURL_DEG = 10.0                       # ... by +- this (deg about the vertical, progressive toward the tip)
CURL_PIVOT = (0.38, 0.88)             # ... the bend ramps in between these fractions of the tendril
IDLE_BOB = 0.18                       # idle float bob of the upper body (units)
WALK_N = 33                           # glide frames 1..33 (32-frame loop, 1.33 s)
LEAN_DEG, LEAN_OSC = 7.0, 1.5         # "glide lean" forward + its oscillation (deg)
GLIDE_ROLL = 2.0                      # side drift (deg)
GLIDE_BOB = 0.25                      # float bob (units, 2 per loop)
ARM_TRAIL, ARM_FLUTTER = 14.0, 5.0    # raised spikes trail back + flutter (deg)
HANG_TRAIL, HANG_FLUTTER = 16.0, 4.0  # hanging arms trail back + flutter (deg)
CROWN_TRAIL = 6.0                     # crown/hat trails back like a flame (deg)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
# ---- sculpt-frame landmarks -> the new frame (the kept sculpt moved up TORSO_STRETCH); derived after the overrides
S_UP = TORSO_STRETCH


def up(z):
    return z + S_UP


NECK_Z = tuple(up(z) for z in NECK_Z)
NECK_STRETCH_Z = tuple(up(z) for z in NECK_STRETCH_Z)
FACE_Z = tuple(up(z) for z in FACE_Z)
EYE_IN, EYE_OUT = (EYE_IN[0], up(EYE_IN[1])), (EYE_OUT[0], up(EYE_OUT[1]))
EYE_BOX = (EYE_BOX[0], up(EYE_BOX[1]), up(EYE_BOX[2]))
CHEST_Z, HEAD_Z, CROWN_Z = (tuple(up(z) for z in t_) for t_ in (CHEST_Z, HEAD_Z, CROWN_Z))
RAISED_SH, RAISED_EL, RAISED_TIP = ((x, up(z)) for x, z in (RAISED_SH, RAISED_EL, RAISED_TIP))
RAISED_Z = tuple(up(z) for z in RAISED_Z)
SEED_Z, HAT_APEX_Z = up(SEED_Z), up(HAT_APEX_Z)
NECK_MEAS_Z = (up(17.2), up(18.4))
Z_KEEP = up(Z_KEEP_SCULPT)
Z_BODY_TOP = Z_KEEP + BODY_OVERLAP

# --out-root <dir>: a determinism TWIN build writes the same tree (improved/, rigged/) under <dir> instead of the project
OUT_ROOT = argv[argv.index("--out-root") + 1] if "--out-root" in argv else ROOT
for d_ in ("improved", "rigged"):
    os.makedirs(os.path.join(OUT_ROOT, d_), exist_ok=True)
OUT_IMPROVED = os.path.join(OUT_ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(OUT_ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(OUT_ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(OUT_ROOT, "improved", "textures")
# the normal-bake buffer for duskmaw_bake_diff.py (temp dir, never the project): main build 'main', twins by folder name
NORMAL_NPY = os.path.join(__import__("tempfile").gettempdir(), "duskmaw_normal_%s.npy" %
                          ("main" if OUT_ROOT == ROOT else os.path.basename(os.path.normpath(OUT_ROOT))))
report = {"unit": UNIT, "version": "v4 shadow lord (curving maw / long slender trunk / floor tendrils / clawed arms)",
          "conquest_character_id": CHAR_ID, "source": bpy.data.filepath,
          "tier": "hero", "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0, "overrides": OVERRIDES}
scene = bpy.context.scene


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ss1(e0, e1, x):
    return float(smoothstep(e0, e1, x))


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V, float).tolist(), [], F)
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


def keep_main_island(V, F, min_frac):
    """Drop connected pieces smaller than min_frac of the vertices (sculpt crumbs / SDF specks)."""
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
    lab, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_lab = cnt >= min_frac * n
    keep_v = keep_lab[inv]
    remap = -np.ones(n, dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    order_ = np.argsort(-cnt, kind="stable")
    pieces_ = [{"verts": int(cnt[k]), "kept": bool(keep_lab[k]), "bbox": [V[inv == k].min(0).round(2).tolist(), V[inv == k].max(0).round(2).tolist()]}
               for k in order_[:6]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum()),
                           "largest_dropped": int(cnt[~keep_lab].max()) if (~keep_lab).any() else 0, "largest_pieces": pieces_}


def circ_smooth(a, w):
    if w <= 0:
        return a.copy()
    k = np.ones(2 * w + 1) / (2 * w + 1)
    return np.convolve(np.concatenate([a[-w:], a, a[:w]]), k, mode="valid")


def pchip(xk, yk):
    """monotone cubic (Fritsch-Carlson) through the knots; zero slope at both ends (the bell flare below and the chest
    blend above both meet it flat). Returns a scalar function (clamped to the knot range)."""
    xk = np.asarray(xk, float); yk = np.asarray(yk, float)
    h = np.diff(xk); d = np.diff(yk) / h
    m = np.zeros_like(yk)
    for k in range(1, len(xk) - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])

    def f(z):
        z = min(max(float(z), xk[0]), xk[-1])
        k = min(int(np.searchsorted(xk, z, side="right")) - 1, len(xk) - 2)
        t = (z - xk[k]) / h[k]
        h00, h10, h01, h11 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t, -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
        return float(h00 * yk[k] + h10 * h[k] * m[k] + h01 * yk[k + 1] + h11 * h[k] * m[k + 1])
    return f


PW, PF, PB = pchip(TRUNK_Z, TRUNK_W), pchip(TRUNK_Z, TRUNK_F), pchip(TRUNK_Z, TRUNK_B)


# =========================================================================== v4 maw outlines (front projection)
# x relative to the waist axis, z new frame. The opening's sides follow the trunk: hole half-width = MAW_WRAP x W(z) -
# the side lip. OPEN = the hole prism minus the teeth prisms (upper fangs hang from the top edge, lower teeth rise in the
# upper valleys: interlock); the red frame = the hole grown by the lip widths, its top edge jagged.
HZ0, HZ1 = HOLE_Z
FZ0, FZ1 = HZ0 - LIP_T[0], HZ1 + LIP_T[1]


def frame_hw(z):
    return MAW_WRAP * PW(z)


def hole_hw(z):
    return frame_hw(z) - LIP_T[2]


def arc(cx, cz, rad, a0, a1, n=6):
    return [(cx + rad * math.cos(a), cz + rad * math.sin(a)) for a in np.linspace(a0, a1, n)]


def outline(hw_fun, zb, zt, cr_b, cr_t, top=None, n_side=48):
    """CCW (x right, z up, seen from the front) outline: rounded bottom corners, sides x = +-hw_fun(z) (the waist curve),
    rounded top corners -- or `top`, a right-to-left point list replacing the top edge (the jagged lip line)"""
    xb = hw_fun(zb + cr_b)
    P = arc(-xb + cr_b, zb + cr_b, cr_b, math.pi, 1.5 * math.pi, 8) + arc(xb - cr_b, zb + cr_b, cr_b, 1.5 * math.pi, 2 * math.pi, 8)
    zs_side = np.linspace(zb + cr_b, zt - cr_t, n_side)[1:-1]
    P += [(hw_fun(z), z) for z in zs_side]
    if top is None:
        xt = hw_fun(zt - cr_t)
        P += arc(xt - cr_t, zt - cr_t, cr_t, 0.0, 0.5 * math.pi, 6) + arc(-xt + cr_t, zt - cr_t, cr_t, 0.5 * math.pi, math.pi, 6)
    else:
        P += top
    P += [(-hw_fun(z), z) for z in zs_side[::-1]]
    return np.array(P, float)


def jag_line():
    """the red lip's top edge, right -> left: JAG_N irregular notches (peak lean + height from golden-ratio sequences)"""
    xr = frame_hw(FZ1)
    edges = np.linspace(xr, -xr, JAG_N + 1)
    pts = [(xr, FZ1)]
    for k in range(JAG_N):
        a, b = edges[k], edges[k + 1]
        g1, g2, g3 = (k * 0.618034 + 0.17) % 1.0, (k * 0.414214 + 0.61) % 1.0, (k * 0.732051 + 0.29) % 1.0
        pts.append((a + (b - a) * (0.28 + 0.44 * g1), FZ1 + JAG_AMP[0] + (JAG_AMP[1] - JAG_AMP[0]) * g2))
        if k < JAG_N - 1:
            pts.append((b, FZ1 + JAG_VALLEY[0] + (JAG_VALLEY[1] - JAG_VALLEY[0]) * g3))
    pts.append((-xr, FZ1))
    return pts


HOLE_POLY = outline(hole_hw, HZ0, HZ1, HOLE_CR, HOLE_CR_TOP)
FRAME_POLY = outline(frame_hw, FZ0, FZ1, MAW_CR, 0.0, top=jag_line())
CAV_POLY = outline(lambda z: hole_hw(z) + CAV_EXP, HZ0 - CAV_EXP, HZ1 + CAV_EXP, HOLE_CR + CAV_EXP, HOLE_CR_TOP + CAV_EXP)
X_EDGE = hole_hw(HZ1) + 0.8                     # the outer fangs' bases run past the mouth corner (no sliver)
UP_TEETH = [(-PITCH, 0.0, UP_MID_LEN, -0.5 * PITCH), (0.0, PITCH, UP_MID_LEN, 0.5 * PITCH),   # (x_a, x_b, length, tip x)
            (PITCH, X_EDGE, UP_OUT_LEN, UP_OUT_TIP_X), (-X_EDGE, -PITCH, UP_OUT_LEN, -UP_OUT_TIP_X)]
LO_TEETH = [(k * PITCH, LO_LEN[1] if abs(k) < 1e-9 else LO_LEN[0]) for k in LO_X]
UP_TIPS = [(xt, HZ1 - l) for _, _, l, xt in UP_TEETH]
LO_TIPS = [(x, HZ0 + l) for x, l in LO_TEETH]
MAW_MID = 0.5 * (min(z for _, z in UP_TIPS) + max(z for _, z in LO_TIPS))   # the jaw split line (rig)
SPINE_Z0 = MAW_MID + 0.3                                                   # upper jaw / spine bone head
TOOTH_POLYS = [np.array([(xa, HZ1 + 1.0), (xa, HZ1), (xt, HZ1 - l), (xb, HZ1), (xb, HZ1 + 1.0)]) for xa, xb, l, xt in UP_TEETH] + \
              [np.array([(x - 0.5 * LO_BASE * PITCH, HZ0 - 1.0), (x + 0.5 * LO_BASE * PITCH, HZ0 - 1.0), (x + 0.5 * LO_BASE * PITCH, HZ0),
                         (x, HZ0 + l), (x - 0.5 * LO_BASE * PITCH, HZ0)]) for x, l in LO_TEETH]


def poly_sdf2(P, poly):
    """signed distance (> 0 inside) of 2D points P (n,2) to a closed polygon"""
    a = poly; b = np.roll(poly, -1, 0)
    d = np.full(len(P), np.inf); inside = np.zeros(len(P), bool)
    for (ax_, az_), (bx_, bz_) in zip(a, b):
        ex, ez = bx_ - ax_, bz_ - az_
        wx, wz = P[:, 0] - ax_, P[:, 1] - az_
        t_ = np.clip((wx * ex + wz * ez) / max(ex * ex + ez * ez, 1e-12), 0.0, 1.0)
        d = np.minimum(d, np.hypot(wx - ex * t_, wz - ez * t_))
        c = ((az_ > P[:, 1]) != (bz_ > P[:, 1])) & (P[:, 0] < ax_ + (P[:, 1] - az_) * ex / (ez if abs(ez) > 1e-12 else 1e-12))
        inside ^= c
    return np.where(inside, d, -d)


# =========================================================================== 1. sculpt -> origin
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
src = bpy.data.objects["Sphere"]
SV, SF = mesh_arrays(src.data, src.matrix_world)
report["tris_source"] = tri_count(src.data)
SV, SF, crumbs = keep_main_island(SV, SF, 0.01)
report["source_cleanup"] = crumbs
lo, hi = SV.min(0), SV.max(0)
SHIFT = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
SV = SV - SHIFT
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
# the waist axis: centre of the throat column between the skirt and the chest (sculpt)
_rs = np.hypot(SV[:, 0], SV[:, 1])
_col = (SV[:, 2] > 6.2) & (SV[:, 2] < 8.9) & (_rs < 2.0)
AX = SV[_col, :2].mean(0)
report["waist_axis_sculpt_frame"] = AX.round(4).tolist()
_nk = (SV[:, 2] > 16.8) & (SV[:, 2] < 18.6) & (np.abs(SV[:, 0] - AX[0]) < 3.0)
NAX = np.array([0.5 * (SV[_nk, 0].min() + SV[_nk, 0].max()), 0.5 * (SV[_nk, 1].min() + SV[_nk, 1].max())])
report["neck_axis_sculpt_frame"] = NAX.round(4).tolist()

# =========================================================================== 2. SDF rebuild
t_sdf = time.time()
NT = 144
TH = np.linspace(-math.pi, math.pi, NT, endpoint=False)
SIN_T, COS_T = np.sin(TH), np.cos(TH)
dS = SV[:, :2] - AX
rS = np.hypot(dS[:, 0], dS[:, 1]); thS = np.arctan2(dS[:, 0], -dS[:, 1])
HANDS_S = (rS > 7.4) & (np.abs(dS[:, 1]) < 3.2) & (SV[:, 2] > 3.8)   # hanging hands + raised arms: out of the sections


def section_r(z0, z1, rmax):
    m = (SV[:, 2] >= z0) & (SV[:, 2] < z1) & (rS < rmax) & ~HANDS_S
    b = ((thS[m] + math.pi) / (2 * math.pi) * NT).astype(int) % NT
    out = np.zeros(NT)
    np.maximum.at(out, b, rS[m])
    return out


# the v3 floor ring, read off the (unmoved) sculpt skirt: identical ring, identical contact
BACK_W = smoothstep(math.radians(THETA_C - D_BLEND), math.radians(THETA_C + D_BLEND), np.abs(TH))
_raw = section_r(Z_CUT, Z_BT, 10.0)
R_bt = (circ_smooth(_raw, 1) - BT_INSET) * (1 - BACK_W) + (circ_smooth(_raw, CORE_W) - BT_INSET) * BACK_W
R_gnd = (1 - BASE_ROUND) * R_bt * (1 + FLARE) + BASE_ROUND * R_bt.mean() * (1 + FLARE)
# v4: the kept sculpt moves up TORSO_STRETCH (below Z_KEEP it is cut away: the procedural trunk replaces it)
SV[:, 2] += S_UP
ZS_SEC = (Z_KEEP - 0.4) + 0.2 * np.arange(int(round((Z_BODY_TOP + 0.6 - (Z_KEEP - 0.4)) / 0.2)) + 1)
SEC_TAB = np.array([circ_smooth(section_r(z_ - 0.3, z_ + 0.3, 9.3), 3) for z_ in ZS_SEC])


def R_sec(z):
    f_ = float(np.clip((z - ZS_SEC[0]) / 0.2, 0.0, len(ZS_SEC) - 1.000001))
    k_ = int(f_)
    return SEC_TAB[k_] * (1 - (f_ - k_)) + SEC_TAB[k_ + 1] * (f_ - k_)


def ell_r(W, F, B):
    """per angle bin: the radius of the elliptical trunk section (half-width W, front depth F, back depth B)"""
    D = np.where(COS_T >= 0.0, F, B)
    return 1.0 / np.sqrt((SIN_T / W) ** 2 + (COS_T / D) ** 2)


def trunk_R(z):
    R = ell_r(PW(z), PF(z), PB(z))
    if z > Z_CHEST[0]:
        s_ = ss1(Z_CHEST[0], Z_CHEST[1], z)
        R = R * (1 - s_) + (R_sec(max(z, Z_KEEP - 0.3)) - CH_INSET) * s_
    return R


R_FT = trunk_R(TRUNK_Z[0])


def R_body(z):
    """procedural lower body radius per angle bin at height z (new frame): rounded hem -> bell flare -> the trunk"""
    if z <= TRUNK_Z[0]:
        zc = max(z, HEM_R)
        w = ((TRUNK_Z[0] - zc) / (TRUNK_Z[0] - HEM_R)) ** FLARE_POW
        R = R_FT + (R_gnd - R_FT) * w
        if z < HEM_R:                                   # rounded hem: a quarter circle at the floor edge
            R = R - HEM_R + math.sqrt(max(HEM_R ** 2 - (HEM_R - max(z, 0.0)) ** 2, 0.0))
        return R
    return trunk_R(z)


def R_hem(z):
    """the base skin WITHOUT the rounded hem (the tendril region's reference surface)"""
    return R_body(max(z, HEM_R))


def radial_solid(Rfun, zs):
    rings = []
    for z in zs:
        R = Rfun(z)
        rings.append(np.stack([AX[0] + R * SIN_T, AX[1] - R * COS_T, np.full(NT, z)], 1))
    V = np.concatenate(rings + [np.array([[AX[0], AX[1], zs[0]], [AX[0], AX[1], zs[-1]]])])
    F = []
    nr = len(zs)
    for k in range(nr - 1):
        for i in range(NT):
            a, b = k * NT + i, k * NT + (i + 1) % NT
            F.append([a, b, b + NT, a + NT])
    cb, ct = nr * NT, nr * NT + 1
    for i in range(NT):
        F.append([cb, (i + 1) % NT, i])
        F.append([ct, (nr - 1) * NT + i, (nr - 1) * NT + (i + 1) % NT])
    return V, F


def ellipsoid(c, ab, nu=64, nv=40):
    V, F = [], []
    for j in range(1, nv):
        ph = math.pi * j / nv
        for i in range(nu):
            t = 2 * math.pi * i / nu
            V.append((c[0] + ab[0] * math.sin(ph) * math.cos(t), c[1] + ab[1] * math.sin(ph) * math.sin(t), c[2] + ab[2] * math.cos(ph)))
    V += [(c[0], c[1], c[2] + ab[2]), (c[0], c[1], c[2] - ab[2])]
    top, bot = len(V) - 2, len(V) - 1
    for j in range(nv - 2):
        for i in range(nu):
            a, b = j * nu + i, j * nu + (i + 1) % nu
            F.append([a, a + nu, b + nu, b])
    for i in range(nu):
        F.append([top, i, (i + 1) % nu])
        F.append([bot, (nv - 2) * nu + (i + 1) % nu, (nv - 2) * nu + i])
    return V, F


def cylinder(c, rad, z0, z1, nu=64):
    V = [(c[0] + rad * math.cos(2 * math.pi * i / nu), c[1] + rad * math.sin(2 * math.pi * i / nu), z) for z in (z0, z1) for i in range(nu)]
    V += [(c[0], c[1], z0), (c[0], c[1], z1)]
    F = [[i, (i + 1) % nu, nu + (i + 1) % nu, nu + i] for i in range(nu)]
    F += [[2 * nu, (i + 1) % nu, i] for i in range(nu)] + [[2 * nu + 1, nu + i, nu + (i + 1) % nu] for i in range(nu)]
    return V, F


def prism_y(poly, y0, y1, xoff):
    """a 2D (x, z) CCW polygon extruded along y from y0 (front) to y1 (back), x offset by xoff -- outward normals"""
    n = len(poly)
    V = [(xoff + x, y0, z) for x, z in poly] + [(xoff + x, y1, z) for x, z in poly]
    F = [list(range(n)), list(range(2 * n - 1, n - 1, -1))]
    F += [[i, n + i, n + (i + 1) % n, (i + 1) % n] for i in range(n)]
    return V, F


def orient_out(V, F):
    """flip a closed mesh's faces if its signed volume is negative (outward normals for the SDF conversion)"""
    Va = np.asarray(V, float)
    vol = 0.0
    for f in F:
        a = Va[f[0]]
        for k in range(1, len(f) - 1):
            vol += float(np.dot(a, np.cross(Va[f[k]], Va[f[k + 1]])))
    return (V, [f[::-1] for f in F]) if vol < 0 else (V, F)


def merge(parts):
    """one mesh from several closed pieces (each oriented outward)"""
    V_, F_ = [], []
    for Vp, Fp in parts:
        Vp, Fp = orient_out(Vp, Fp)
        F_ += [[j + len(V_) for j in f] for f in Fp]; V_ += [tuple(map(float, v)) for v in Vp]
    return V_, F_


def sweep(C, ra, rb=None, nu=16, n1_0=None):
    """closed tube along the polyline C (n,3): elliptical section ra (along N1) x rb (along N2 = N1 x T), parallel-
    transport frames from n1_0 (default: the horizontal side vector), fan caps at both ends"""
    C = np.asarray(C, float)
    ra = np.broadcast_to(np.asarray(ra, float), (len(C),))
    rb = ra if rb is None else np.broadcast_to(np.asarray(rb, float), (len(C),))
    Tn = np.gradient(C, axis=0); Tn /= np.linalg.norm(Tn, axis=1)[:, None]
    v = np.cross(Tn[0], [0.0, 0.0, 1.0]) if n1_0 is None else np.asarray(n1_0, float)
    if np.linalg.norm(v) < 1e-6:
        v = np.cross(Tn[0], [0.0, 1.0, 0.0])
    N1 = np.zeros_like(C)
    for k in range(len(C)):
        v = v - np.dot(v, Tn[k]) * Tn[k]; v = v / np.linalg.norm(v); N1[k] = v
    N2 = np.cross(N1, Tn)
    V, F = [], []
    for k in range(len(C)):
        for i in range(nu):
            a = 2 * math.pi * i / nu
            V.append(tuple(C[k] + max(ra[k], 0.008) * math.cos(a) * N1[k] + max(rb[k], 0.008) * math.sin(a) * N2[k]))
    for k in range(len(C) - 1):
        for i in range(nu):
            F.append([k * nu + i, k * nu + (i + 1) % nu, (k + 1) * nu + (i + 1) % nu, (k + 1) * nu + i])
    V += [tuple(C[0]), tuple(C[-1])]
    c0, c1 = len(V) - 2, len(V) - 1
    last = (len(C) - 1) * nu
    for i in range(nu):
        F.append([c0, (i + 1) % nu, i]); F.append([c1, last + i, last + (i + 1) % nu])
    return V, F, Tn


def catmull(P, n_seg=20):
    """uniform Catmull-Rom through the points (ends duplicated): dense polyline"""
    P = np.asarray(P, float)
    Q = np.concatenate([P[:1] * 2 - P[1:2], P, P[-1:] * 2 - P[-2:-1]])
    out = []
    for k in range(1, len(Q) - 2):
        p0, p1, p2, p3 = Q[k - 1], Q[k], Q[k + 1], Q[k + 2]
        for t in np.linspace(0.0, 1.0, n_seg, endpoint=(k == len(Q) - 3)):
            out.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return np.array(out)


def bezier(P0, P1, P2, n=16):
    t = np.linspace(0.0, 1.0, n)[:, None]
    return (1 - t) ** 2 * P0 + 2 * (1 - t) * t * P1 + t * t * P2


def arclen(C):
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])


# ---- v4 ARMS: tube root -> shoulder -> elbow -> wrist, palm bulb, CLAW_N hooked claws, flame wisps off the forearm.
# Skeleton samples (x, y, z, radius, arc length s, side, part) drive the weights and the region masks.
def arm_parts(sgn):
    P = np.array([(AX[0] + sgn * x, AX[1] + y, z) for x, y, z in ARM_PTS])
    C = catmull(P, 24)
    s = arclen(C)
    s_knots = [float(s[np.argmin(np.linalg.norm(C - p, axis=1))]) for p in P]
    rad = np.interp(s, s_knots, ARM_RAD)
    tube_V, tube_F, Tn = sweep(C, rad, nu=20)
    D = Tn[-1]
    palm_c = C[-1] + D * 0.35
    palm = ellipsoid(tuple(palm_c), (PALM_R, PALM_R, PALM_R), 20, 12)
    v_ = np.array([sgn * 0.7, -0.7, 0.0])
    A_f = v_ - np.dot(v_, D) * D; A_f /= np.linalg.norm(A_f)
    Hk = -sgn * np.cross(A_f, D); Hk /= np.linalg.norm(Hk)          # toward the palm side (inward-forward)
    claws, samples = [], []
    for k in range(CLAW_N):
        a_ = math.radians((k - 0.5 * (CLAW_N - 1)) * CLAW_SPREAD)
        d_ = D * math.cos(a_) + A_f * math.sin(a_)
        L_ = CLAW_LEN[k % len(CLAW_LEN)]
        B_ = palm_c + d_ * PALM_R * 0.55
        Cc = bezier(B_, B_ + d_ * 0.62 * L_, B_ + d_ * 0.72 * L_ + Hk * CLAW_HOOK * L_, 18)
        u_ = np.linspace(0.0, 1.0, len(Cc))
        rc = CLAW_R * (1 - u_) ** 0.9 + 0.012
        Vc, Fc, _ = sweep(Cc, rc, nu=10)
        claws.append((Vc, Fc))
        samples += [(*p, r_, s[-1] + 0.35 + PALM_R + L_ * u, sgn, 2) for p, r_, u in zip(Cc, rc, u_)]
    wisps = []
    i_el, i_wr = int(np.argmin(np.abs(s - s_knots[2]))), len(C) - 1
    for k, (u_at, L_) in enumerate(zip(WISP_AT, WISP_LEN)):
        i_ = int(round(i_el + u_at * (i_wr - i_el)))
        c_, t_, r_ = C[i_], Tn[i_], rad[i_]
        o_ = np.array([sgn * 0.9, 0.35, 0.0]); o_ = o_ - np.dot(o_, t_) * t_; o_ /= np.linalg.norm(o_)   # outward (+ a bit back)
        P0 = c_ + o_ * r_ * 0.2
        b_ = np.cross(o_, t_); b_ /= np.linalg.norm(b_)
        flick = b_ * (0.22 * L_ * (1 if k % 2 == 0 else -1))                   # the flame tip flicks sideways
        Cw = bezier(P0, P0 + o_ * 0.8 + t_ * 0.3 * L_,
                    P0 + o_ * (0.9 + 0.3 * L_) + t_ * 0.85 * L_ + flick, 20)      # trails off the forearm, flaring out
        u_ = np.linspace(0.0, 1.0, len(Cw))
        ww = 0.5 * WISP_W * (1 - u_) ** 0.8 + 0.01
        wt = 0.5 * WISP_T * (1 - 0.55 * u_) + 0.012
        Vw, Fw, _ = sweep(Cw, ww, wt, nu=14, n1_0=o_)     # the broad side faces front (width in the arm's front plane)
        wisps.append((Vw, Fw))
        samples += [(*p, max(a, b), s[i_], sgn, 3) for p, a, b in zip(Cw, ww, wt)]
    samples = [(*p, r_, s_, sgn, 1) for p, r_, s_ in zip(C, rad, s)] + [(*palm_c, PALM_R, s[-1] + 0.35, sgn, 1)] + samples
    info = {"length_root_to_wrist": round(float(s[-1]), 3), "s_shoulder": round(s_knots[1], 3), "s_elbow": round(s_knots[2], 3),
            "radius_shoulder_elbow_wrist": [round(float(v), 3) for v in ARM_RAD[1:]], "claws": CLAW_N,
            "claw_tips": [np.round(c[0][-1], 3).tolist() for c in claws], "wisps": len(wisps)}
    return (tube_V, tube_F), palm, claws, wisps, samples, {"P": P, "s_knots": s_knots, "palm": palm_c, "D": D}, info


# ---- v4 FLOOR TENDRILS: from inside the bell, radiating out along the floor; curvature ramps in past TEND_CURL_START
# (a sideways spiral), the TEND_UP ones curl up and over instead (fiddleheads). Flattened sections, dipped below the
# floor and cut flat by the floor slab (flat contact).
def tendril(i):
    g1, g2, g3 = (i * 0.618034 + 0.30) % 1.0, (i * 0.381966 + 0.70) % 1.0, (i * 0.754878 + 0.11) % 1.0
    phi = math.radians(TEND_PHASE + 360.0 * i / TEND_N)
    Rg = float(np.interp(phi, TH, R_gnd, period=2 * math.pi))
    L = TEND_REACH[0] + (TEND_REACH[1] - TEND_REACH[0]) * g1 + TEND_ROOT_IN
    r0 = TEND_R0 * (0.85 + 0.3 * g3)
    dirn = TEND_DIRS[i % len(TEND_DIRS)]
    upc = i in TEND_UP
    turn = math.radians(TEND_UP_TURN if upc else TEND_CURL[0] + (TEND_CURL[1] - TEND_CURL[0]) * g2) * dirn
    elev = math.radians(TEND_UP_DEG) if upc else math.radians(18.0)
    n = 90
    ds = L / (n - 1)
    uc = TEND_CURL_START
    kk = turn / (L * (1 - uc) / 2.3)                         # sideways curvature scale (integral = turn)
    ke = elev / (L * (1 - uc) / 2.5)                         # vertical curl scale (integral = elev)
    be = -dirn * math.radians(14.0) / (2 * uc * L / math.pi) # early counter-bend (an S before the curl)
    psi, e_ = phi, 0.0
    p = np.array([AX[0] + (Rg - TEND_ROOT_IN) * math.sin(phi), AX[1] - (Rg - TEND_ROOT_IN) * math.cos(phi), 0.0])
    C, rr = [], []
    zlift = 0.0
    for k in range(n):
        u = k / (n - 1)
        r_ = TEND_TIP_R + (r0 - TEND_TIP_R) * (1 - u) ** TEND_TAPER
        C.append([p[0], p[1], r_ * TEND_FLAT * 0.8 + zlift]); rr.append(r_)
        q = max(0.0, (u - uc) / (1 - uc))
        psi += (kk * q ** 1.3 + (be * math.sin(math.pi * u / uc) if u < uc else 0.0)) * ds
        e_ += ke * q ** 1.5 * ds
        p = p + np.array([math.cos(e_) * math.sin(psi), -math.cos(e_) * math.cos(psi), 0.0]) * ds
        zlift += math.sin(e_) * ds
    C = np.array(C); rr = np.array(rr)
    V, F, _ = sweep(C, rr, rr * TEND_FLAT, nu=16)
    reach = float(np.max(np.hypot(C[:, 0] - AX[0], C[:, 1] - AX[1]) + rr))
    u_s = np.linspace(0.0, 1.0, n)
    samples = [(*c, r_, u, i) for c, r_, u in zip(C, rr, u_s)]
    return (V, F), samples, {"angle_deg": round(math.degrees(phi), 1), "length": round(L, 3), "root_radius": round(r0, 3),
                             "reach_radius": round(reach, 3), "reach_beyond_ring": round(reach - Rg, 3),
                             "tip_height": round(float(C[-1, 2]), 3), "max_height": round(float((C[:, 2] + rr * TEND_FLAT).max()), 3),
                             "curl_deg": round(math.degrees(turn), 1), "curls_up": bool(upc), "curl_bone": i in CURL_IDX,
                             "pivot": C[int(CURL_PIVOT[0] * (n - 1))].round(4).tolist(), "tip": C[-1].round(4).tolist()}


def obj_tmp(name, V, F):
    V, F = orient_out(V, F)
    m = bpy.data.meshes.new(name); m.from_pydata([tuple(map(float, v)) for v in V], [], F); m.update(); m.validate()
    o = bpy.data.objects.new(name, m); scene.collection.objects.link(o); o.hide_render = True
    return o


zs_body = np.concatenate([[-0.3 * VOX], np.linspace(0.0, HEM_R, 8)[1:], np.linspace(HEM_R, TRUNK_Z[0], 44)[1:],
                          np.linspace(TRUNK_Z[0], Z_BODY_TOP, 130)[1:]])
zs_ins = np.linspace(HZ0 - CAV_EXP - 0.6, HZ1 + CAV_EXP + 0.6, 64)
ARMS = {s_: arm_parts(g_) for s_, g_ in (("L", 1), ("R", -1))}
TENDS = [tendril(i) for i in range(TEND_N)]
tend_mesh = merge([t_[0] for t_ in TENDS])
claw_mesh = merge([c for s_ in ARMS for c in ARMS[s_][2]])
wisp_mesh = merge([w for s_ in ARMS for w in ARMS[s_][3]])
arm_mesh = {s_: merge([ARMS[s_][0], ARMS[s_][1]]) for s_ in ARMS}
teeth_mesh = merge([prism_y(tp, AX[1] - 14.5, AX[1] + OPEN_BACK + 0.5, AX[0]) for tp in TOOTH_POLYS])
slab = merge([cylinder(AX, 30.0, -6.0, -0.3 * VOX)])
tmp_objs = [obj_tmp("sdf_sculpt", SV, SF), obj_tmp("sdf_body", *radial_solid(R_body, zs_body)),
            obj_tmp("sdf_keepcut", *cylinder(AX, 16.0, -2.0, Z_KEEP)),
            obj_tmp("sdf_armL", *arm_mesh["L"]), obj_tmp("sdf_armR", *arm_mesh["R"]),
            obj_tmp("sdf_hole", *prism_y(HOLE_POLY, AX[1] - 14.0, AX[1] + OPEN_BACK, AX[0])),
            obj_tmp("sdf_teeth", *teeth_mesh),
            obj_tmp("sdf_cavprism", *prism_y(CAV_POLY, AX[1] - 14.0, AX[1] + 3.0, AX[0])),
            obj_tmp("sdf_inset", *radial_solid(lambda z_: R_body(z_) - T_TOOTH, zs_ins)),
            obj_tmp("sdf_bowl", *ellipsoid((AX[0] + BOWL_C[0], AX[1] + BOWL_C[1], BOWL_C[2]), BOWL_R)),
            obj_tmp("sdf_tend", *tend_mesh), obj_tmp("sdf_floor", *slab),
            obj_tmp("sdf_claws", *claw_mesh), obj_tmp("sdf_wisps", *wisp_mesh)]
o_s, o_b, o_x, o_al, o_ar, o_h, o_th, o_cp, o_in, o_bw, o_t, o_fl, o_cl, o_ws = tmp_objs
ng = bpy.data.node_groups.new("duskmaw_sdf", "GeometryNodeTree")
ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
NN = ng.nodes
g_out = NN.new("NodeGroupOutput")


def sdf(o):
    oi = NN.new("GeometryNodeObjectInfo"); oi.inputs["Object"].default_value = o; oi.transform_space = "ORIGINAL"
    ms = NN.new("GeometryNodeMeshToSDFGrid"); ms.inputs["Voxel Size"].default_value = VOX; ms.inputs["Band Width"].default_value = SDF_BAND
    ng.links.new(oi.outputs["Geometry"], ms.inputs["Mesh"])
    return ms.outputs["SDF Grid"]


def sdf_bool(op, g1, g2s):
    b = NN.new("GeometryNodeSDFGridBoolean"); b.operation = op
    multi = [i for i in b.inputs if i.is_multi_input and i.enabled][0]
    ng.links.new(g1, b.inputs["Grid 1"] if op == "DIFFERENCE" else multi)
    for g in g2s:
        ng.links.new(g, multi)
    return b.outputs["Grid"]


def nmath(op, x, y=None):
    m = NN.new("ShaderNodeMath"); m.operation = op
    if isinstance(x, (int, float)):
        m.inputs[0].default_value = float(x)
    else:
        ng.links.new(x, m.inputs[0])
    if y is not None:
        if isinstance(y, (int, float)):
            m.inputs[1].default_value = float(y)
        else:
            ng.links.new(y, m.inputs[1])
    return m.outputs[0]


def nband(src_, a, b, c, d):
    """smoothstep up over [a, b], down over [c, d] (field)"""
    def mr(lo_, hi_, inv):
        m = NN.new("ShaderNodeMapRange"); m.interpolation_type = "SMOOTHSTEP"; m.clamp = True
        ng.links.new(src_, m.inputs["Value"])
        m.inputs["From Min"].default_value = lo_; m.inputs["From Max"].default_value = hi_
        m.inputs["To Min"].default_value = 1.0 if inv else 0.0; m.inputs["To Max"].default_value = 0.0 if inv else 1.0
        return m.outputs["Result"]
    return nmath("MULTIPLY", mr(a, b, False), mr(c, d, True))


A_ = sdf_bool("DIFFERENCE", sdf(o_s), [sdf(o_x)])                           # the kept sculpt (above Z_KEEP)
CAVG = sdf_bool("INTERSECT", sdf(o_cp), [sdf(o_in), sdf(o_bw)])             # cavity: outline x skin inset x bowl
OPEN = sdf_bool("DIFFERENCE", sdf(o_h), [sdf(o_th)])                        # opening: hole minus the teeth
U0 = sdf_bool("UNION", A_, [sdf(o_b), sdf(o_al), sdf(o_ar)])                 # + procedural trunk + arm tubes
U_ = sdf_bool("DIFFERENCE", U0, [OPEN, CAVG])                                # - the maw
TEND = sdf_bool("DIFFERENCE", sdf(o_t), [sdf(o_fl)])                         # tendrils, cut flat at the floor
CRISP = sdf_bool("UNION", TEND, [sdf(o_cl), sdf(o_ws)])                      # + claws + wisps (never smoothed)
mean_ = NN.new("GeometryNodeSDFGridMean"); mean_.inputs["Width"].default_value = SM_W; mean_.inputs["Iterations"].default_value = SM_IT
ng.links.new(U_, mean_.inputs["Grid"])
pos = NN.new("GeometryNodeInputPosition")
sep = NN.new("ShaderNodeSeparateXYZ"); ng.links.new(pos.outputs[0], sep.inputs[0])
X_, Y_, Z_ = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
yf = nmath("SUBTRACT", Y_, float(FACE_AX_Y))
rad_f = nmath("SQRT", nmath("ADD", nmath("MULTIPLY", X_, X_), nmath("MULTIPLY", yf, yf)))
xa, ya = nmath("SUBTRACT", X_, float(AX[0])), nmath("SUBTRACT", Y_, float(AX[1]))
rad_a = nmath("SQRT", nmath("ADD", nmath("MULTIPLY", xa, xa), nmath("MULTIPLY", ya, ya)))
m_face = nmath("MULTIPLY", nband(Z_, *FACE_Z), nband(rad_f, -1.0, -0.5, *FACE_RAD))
m_low = nmath("MULTIPLY", nband(Z_, *LOW_Z), nband(rad_a, -1.0, -0.5, *LOW_RAD))
m_j = nmath("MULTIPLY", nband(Z_, *JUNC_Z), nband(rad_a, -1.0, -0.5, 7.8, 8.6))            # trunk -> kept chest
m_arm = nmath("MULTIPLY", nband(nmath("ABSOLUTE", xa), *ARMROOT_X), nband(Z_, *ARMROOT_Z))  # arm roots melt into the chest
m_arm = nmath("MULTIPLY", m_arm, nband(ya, -4.0, -3.4, 3.4, 4.0))
FHW_MAX = max(frame_hw(z) for z in np.linspace(FZ0, FZ1, 40))
m_mouth = nmath("MULTIPLY", nband(xa, -(FHW_MAX + 1.2), -(FHW_MAX + 0.6), FHW_MAX + 0.6, FHW_MAX + 1.2),
                nband(Z_, FZ0 - 1.0, FZ0 - 0.5, FZ1 + JAG_AMP[1] + 0.3, FZ1 + JAG_AMP[1] + 0.7))
m_mouth = nmath("MULTIPLY", m_mouth, nband(ya, -40.0, -39.0, 1.0, 2.0))
keep_ = nmath("MULTIPLY", nmath("SUBTRACT", m_mouth, 1.0), -1.0)                # 1 - m_mouth
mask = nmath("MULTIPLY", nmath("MAXIMUM", nmath("MAXIMUM", nmath("MAXIMUM", m_face, m_low), m_j), m_arm), keep_)
s1 = NN.new("GeometryNodeSampleGrid"); ng.links.new(U_, s1.inputs["Grid"]); ng.links.new(pos.outputs[0], s1.inputs["Position"])
s2 = NN.new("GeometryNodeSampleGrid"); ng.links.new(mean_.outputs["Grid"], s2.inputs["Grid"]); ng.links.new(pos.outputs[0], s2.inputs["Position"])
s3 = NN.new("GeometryNodeSampleGrid"); ng.links.new(CRISP, s3.inputs["Grid"]); ng.links.new(pos.outputs[0], s3.inputs["Position"])
mix = NN.new("ShaderNodeMix"); mix.data_type = "FLOAT"; mix.clamp_factor = True
ng.links.new(mask, mix.inputs["Factor"]); ng.links.new(s1.outputs["Value"], mix.inputs[2]); ng.links.new(s2.outputs["Value"], mix.inputs[3])
fa_, fb_ = mix.outputs[0], s3.outputs["Value"]
# polynomial smooth min: min(a, b) - h^2 K / 4, h = max(K - |a - b|, 0) / K  (fillet where the crisp parts meet the body)
h_ = nmath("DIVIDE", nmath("MAXIMUM", nmath("SUBTRACT", float(K_BLEND), nmath("ABSOLUTE", nmath("SUBTRACT", fa_, fb_))), 0.0), float(K_BLEND))
final_f = nmath("SUBTRACT", nmath("MINIMUM", fa_, fb_), nmath("MULTIPLY", nmath("MULTIPLY", h_, h_), 0.25 * K_BLEND))
# the fillet also acts where the tendrils' floor cut meets the body's floor (both at -0.3 VOX): it would sink the floor
# K/4 -- so the floor slab is cut again from the final field (flat, common floor contact)
s4 = NN.new("GeometryNodeSampleGrid"); ng.links.new(sdf(o_fl), s4.inputs["Grid"]); ng.links.new(pos.outputs[0], s4.inputs["Position"])
final_f = nmath("MAXIMUM", final_f, nmath("MULTIPLY", s4.outputs["Value"], -1.0))
ftg = NN.new("GeometryNodeFieldToGrid"); ftg.grid_items.new("FLOAT", "sdf")
ng.links.new(sdf_bool("UNION", U_, [mean_.outputs["Grid"], CRISP]), ftg.inputs["Topology"]); ng.links.new(final_f, ftg.inputs["sdf"])
g2m = NN.new("GeometryNodeGridToMesh"); g2m.inputs["Threshold"].default_value = 0.0; g2m.inputs["Adaptivity"].default_value = 0.0
ng.links.new(ftg.outputs["sdf"], g2m.inputs["Grid"])
ng.links.new(g2m.outputs["Mesh"], g_out.inputs[0])
host = obj_tmp("sdf_host", [], [])
md_ = host.modifiers.new("sdf", "NODES"); md_.node_group = ng
HV, HF = evaluated_arrays(host)
for o in tmp_objs + [host]:
    me_ = o.data
    bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.meshes.remove(me_)
bpy.data.node_groups.remove(ng)
n_raw = len(HV)
HV, HF, hspecks = keep_main_island(HV, HF, 0.5)         # the outer shell only (see report: inner shells + specks dropped)


def signed_volume(V, F):
    vol = 0.0
    for f in F:
        a = V[f[0]]
        for k in range(1, len(f) - 1):
            vol += float(np.dot(a, np.cross(V[f[k]], V[f[k + 1]]))) / 6.0
    return vol


vol_h = signed_volume(HV, HF)
if vol_h < 0:                                             # grid-to-mesh winds the shell inward: flip to outward normals
    HF = [f[::-1] for f in HF]


def _prof(z_):
    R_ = R_body(z_)
    return {str(d_): round(float(np.interp(math.radians(d_), TH, R_, period=2 * math.pi)), 3) for d_ in (0, 30, 60, 90, 120, 180)}


report["sdf_rebuild"] = {"method": "geometry-node SDF grids: ((sculpt above Z_KEEP) U procedural trunk U arm tubes) - maw ((hole "
                                   "prism - teeth prisms) U cavity = outline x skin inset T_TOOTH x back bowl); masked SDF mean (face "
                                   "/ bell flare / chest junction / arm roots, never the maw) mixed in by Field-to-Grid; smooth-min "
                                   "(K_BLEND) with the crisp parts (floor tendrils cut flat at the floor, claws, wisps); grid -> mesh",
                         "voxel": VOX, "band_voxels": SDF_BAND, "raw_verts": n_raw,
                         "inner_shells_note": "Field-to-Grid writes only the band voxels, so thick parts get an inverted inner "
                                              "shell ~SDF_BAND voxels inside; it never touches the outer surface and is dropped "
                                              "with the specks (only the largest shell is kept)", "high_faces": len(HF), "specks_dropped": hspecks,
                         "seconds": round(time.time() - t_sdf, 1), "winding_flipped": bool(vol_h < 0), "enclosed_volume": round(abs(vol_h), 3),
                         "trunk_new_frame_pre_K": {"torso_stretch": S_UP, "z_keep": Z_KEEP, "body_top": Z_BODY_TOP,
                                                   "heights": list(TRUNK_Z), "half_width": list(TRUNK_W), "front": list(TRUNK_F),
                                                   "back": list(TRUNK_B), "waist_half_width_min": round(min(PW(z) for z in np.linspace(TRUNK_Z[0], TRUNK_Z[-1], 200)), 3),
                                                   "R_ground_min_max": [round(float(R_gnd.min()), 3), round(float(R_gnd.max()), 3)],
                                                   "R_body_by_theta_deg": {"z%.1f" % z_: _prof(z_) for z_ in (2.0, 5.0, 6.35, 8.5, 10.8, 13.0, 14.8, 16.5, 18.5)}},
                         "maw": {"wrap": MAW_WRAP, "hole_z": list(HOLE_Z), "frame_z": [FZ0, FZ1], "pitch": PITCH,
                                 "hole_half_width_bottom_waist_top": [round(hole_hw(HZ0 + HOLE_CR), 3), round(hole_hw(TRUNK_Z[2]), 3), round(hole_hw(HZ1), 3)],
                                 "frame_half_width_bottom_waist_top": [round(frame_hw(FZ0 + MAW_CR), 3), round(frame_hw(TRUNK_Z[2]), 3), round(frame_hw(FZ1), 3)],
                                 "jag_top_line": [[round(x, 3), round(z, 3)] for x, z in jag_line()],
                                 "upper_tips_xz": [[round(x, 3), round(z, 3)] for x, z in UP_TIPS],
                                 "lower_tips_xz": [[round(x, 3), round(z, 3)] for x, z in LO_TIPS], "mid_line_z": round(MAW_MID, 3),
                                 "lower_teeth_final_frame": {"base_width": round(LO_BASE * PITCH, 3), "lengths_sides_centre": [round(v * HEIGHT_K, 3) for v in LO_LEN],
                                                             "note": "identical to v3 (same LO_* constants, same HEIGHT_K)"}},
                         "arms": {s_: ARMS[s_][6] for s_ in ARMS},
                         "tendrils": [t_[2] for t_ in TENDS]}
print("SDF", json.dumps({k: v for k, v in report["sdf_rebuild"].items() if k not in ("tendrils", "arms")}))

# =========================================================================== 3. low: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, LOW_TRIS / tri_count(tmp.data))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_main_island(LV, LF, 0.01)
lo2, hi2 = LV.min(0), LV.max(0)
S2 = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
LV = LV - S2; HV = HV - S2; SV = SV - S2
AX = AX - S2[:2]
NAX = NAX - S2[:2]
ZOFF = -S2[2]                                    # new-frame heights -> final (pre-T) frame: z + ZOFF
SHIFT = SHIFT + S2
# the arm / tendril skeleton samples follow the re-centre
ARM_S = np.array([s_ for a_ in ARMS for s_ in ARMS[a_][4]], float)        # x y z r s side part
ARM_S[:, :3] -= S2
TEN_S = np.array([s_ for t_ in TENDS for s_ in t_[1]], float)             # x y z r u idx
TEN_S[:, :3] -= S2
ARM_GEO = {a_: {k: (np.asarray(v) - S2 if k in ("P", "palm") else v) for k, v in ARMS[a_][5].items()} for a_ in ARMS}
TEN_INFO = [t_[2] for t_ in TENDS]
HIGH = new_obj(UNIT + "_high", HV, HF)
size = LV.max(0) - LV.min(0)
H = float(size[2])
report["retopo"] = {"method": "collapse decimation of the SDF surface (deterministic) -> main shell",
                    "decimated_tris": int(sum(len(f) - 2 for f in LF)), "specks_dropped": specks, "seconds": round(time.time() - t, 1)}
bvh_high = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_high.find_nearest(Vector(p))[3] for p in LV])
report["retopo"]["low_to_high_distance"] = {"mean": round(float(dev.mean()), 4), "p99": round(float(np.percentile(dev, 99)), 4),
                                            "max": round(float(dev.max()), 4), "pct_of_height_p99": round(100 * float(np.percentile(dev, 99)) / H, 3)}

# ---- eye patch refinement (crisp eye outlines on the smooth face)
t = time.time()


def bm_from(V, F):
    """bmesh from arrays; exact duplicate faces (decimation leftovers) are skipped and counted"""
    b_ = bmesh.new()
    for p in V:
        b_.verts.new(p)
    b_.verts.ensure_lookup_table()
    dup = 0
    for f in F:
        try:
            b_.faces.new([b_.verts[i] for i in f])
        except ValueError:
            dup += 1
    b_.verts.index_update()
    return b_, dup


bm, dup0 = bm_from(LV, LF)
bm.normal_update()


def eye_field(x, zsc):
    """front-projected eye lens field (> 0 inside): |x| mirrored about the waist axis, zsc in the new frame"""
    x = np.abs(np.asarray(x, float) - AX[0]); zsc = np.asarray(zsc, float)
    ux = np.array(EYE_OUT) - np.array(EYE_IN); Le = float(np.linalg.norm(ux)); ux = ux / Le
    vx = np.array([-ux[1], ux[0]])
    vx = -vx if vx[1] < 0 else vx
    pe_ = np.stack([x - EYE_IN[0], zsc - EYE_IN[1]], -1)
    u = pe_ @ ux; v = pe_ @ vx
    se = np.clip(u / Le, 0.0, 1.0)
    f_ = np.minimum(EYE_TOP * np.sin(math.pi * se) ** EYE_POW[0] - v, v + EYE_BOT * np.sin(math.pi * se) ** EYE_POW[1])
    return np.where((u > 0) & (u < Le), f_, -np.minimum(np.abs(u), np.abs(u - Le)) - 0.01)


def in_eye_box(f):
    c = f.calc_center_median()
    if not (abs(c.x - AX[0]) < EYE_BOX[0] and EYE_BOX[1] + ZOFF < c.z < EYE_BOX[2] + ZOFF and f.normal.y < -0.1 and c.y < AX[1] - 1.0):
        return False
    return float(eye_field(c.x, c.z - ZOFF)) > -EYE_NEAR


n_before = len(bm.faces)
for it in range(6):
    faces = [f for f in bm.faces if in_eye_box(f)]
    edges = sorted({e for f in faces for e in f.edges if e.calc_length() > EYE_EDGE}, key=lambda e: e.index)
    if not edges:
        break
    bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=False)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.edges.index_update(); bm.faces.index_update()
    bm.normal_update()
moved = 0
for v in bm.verts:
    if v.index >= len(LV):
        loc = bvh_high.find_nearest(v.co)[0]
        if loc is not None:
            v.co = loc; moved += 1
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["eye_refinement"] = {"duplicate_faces_skipped": dup0, "iterations": it, "faces_added": len(LF) - n_before, "verts_projected": moved,
                            "edge_max": EYE_EDGE, "seconds": round(time.time() - t, 1)}


# ---- arm / tendril membership of points (pre-T frame): nearest skeleton sample, falloff past its radius
def _kd(P):
    kd = KDTree(len(P))
    for i, p in enumerate(P):
        kd.insert(Vector(p), i)
    kd.balance()
    return kd


KD_ARM, KD_TEN = _kd(ARM_S[:, :3]), _kd(TEN_S[:, :3])
R_HEM_Z = np.arange(0.0, 6.01, 0.05)
R_HEM_TAB = np.array([R_hem(float(z_)) for z_ in R_HEM_Z])


def membership(P):
    """per point: arm membership a (side, arc length s, part), tendril membership t (index, fraction u) -- 0..1"""
    a = np.zeros(len(P)); t = np.zeros(len(P))
    a_i = np.zeros(len(P), dtype=np.int64); t_i = np.zeros(len(P), dtype=np.int64)
    for k, p in enumerate(P):
        _, j, d = KD_ARM.find(Vector(p)); a_i[k] = j
        a[k] = 1.0 - ss1(ARM_S[j, 3] + 0.1, ARM_S[j, 3] + 0.45, d)
        _, j2, d2 = KD_TEN.find(Vector(p)); t_i[k] = j2
        t[k] = 1.0 - ss1(TEN_S[j2, 3] + 0.1, TEN_S[j2, 3] + 0.5, d2)
    # tendrils: only outside the base skin (the rooted part is the bell); arms: fade in past the shoulder (the root is chest)
    zq = np.clip(P[:, 2] - ZOFF, 0.0, 6.0)
    kq = np.clip((zq / 0.05).astype(int), 0, len(R_HEM_Z) - 1)
    d_ = P[:, :2] - AX
    thq = np.arctan2(d_[:, 0], -d_[:, 1])
    bq = ((thq + math.pi) / (2 * math.pi) * NT).astype(int) % NT
    rin = np.hypot(d_[:, 0], d_[:, 1]) - R_HEM_TAB[kq, bq]
    t *= smoothstep(-0.3, 0.4, rin) * (P[:, 2] - ZOFF < 7.0)
    s_sh = np.array([ARM_GEO["L" if ARM_S[j, 5] > 0 else "R"]["s_knots"][1] for j in a_i])
    a *= smoothstep(s_sh - 0.9, s_sh + 0.5, ARM_S[a_i, 4])
    return a, a_i, t, t_i


# =========================================================================== 4. region fields (per low vertex)
low0 = new_obj("fields_tmp", LV, LF)
M0 = K.MeshData(low0)
NV = np.empty(len(LV) * 3); low0.data.vertex_normals.foreach_get("vector", NV); NV = NV.reshape(-1, 3)
bpy.data.objects.remove(low0, do_unlink=True)
dxy = LV[:, :2] - AX
r = np.hypot(dxy[:, 0], dxy[:, 1])
th_deg = np.degrees(np.abs(np.arctan2(dxy[:, 0], -dxy[:, 1])))
zl = LV[:, 2]
zs_ = zl - ZOFF                                               # heights in the new frame (the constants' frame)
elen = np.linalg.norm(LV[M0.ev[:, 0]] - LV[M0.ev[:, 1]], axis=1)
adj = [[] for _ in range(M0.n)]
for (a, b), l_ in zip(M0.ev, elen):
    adj[a].append((b, l_)); adj[b].append((a, l_))
t = time.time()
amem_v, _, tmem_v, _ = membership(LV)
report["membership_seconds"] = round(time.time() - t, 1)


def dijkstra(seeds, limit=np.inf):
    g_ = np.full(M0.n, np.inf)
    pq = [(0.0, int(v)) for v in seeds]
    for _, v in pq:
        g_[v] = 0.0
    heapq.heapify(pq)
    while pq:
        d_, v = heapq.heappop(pq)
        if d_ > g_[v] or d_ > limit:
            continue
        for u, l_ in adj[v]:
            nd = d_ + l_
            if nd < g_[u]:
                g_[u] = nd; heapq.heappush(pq, (nd, u))
    return g_


def persistence(fv, mask, min_pers):
    """0-dim persistence of the maxima of fv over the mesh graph restricted to mask (v1 build). Returns per vertex the
    owning LIVE tip (-1 outside mask) and that tip's persistence; basins below min_pers fold into their merge target."""
    idx = np.nonzero(mask)[0]
    order = idx[np.argsort(-fv[idx], kind="stable")]
    par = -np.ones(M0.n, dtype=np.int64)
    bas_max, own, merged = {}, -np.ones(M0.n, dtype=np.int64), {}

    def uf(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for v in order:
        roots_ = {uf(u) for u, _ in adj[v] if par[u] >= 0}
        par[v] = v
        if not roots_:
            bas_max[v] = v; own[v] = v
            continue
        rl = sorted(roots_, key=lambda q: -fv[bas_max[q]])
        keep = rl[0]
        own[v] = bas_max[keep]
        for q in rl[1:]:
            merged[bas_max[q]] = (bas_max[keep], float(fv[v]))
            par[q] = keep
        par[v] = keep
    pers_ = {tp: float(fv[tp]) - sg for tp, (_, sg) in merged.items()}
    for tp in set(bas_max.values()) - set(merged):
        pers_[tp] = float(fv[tp] - fv[idx].min())

    def live_tip(tp):
        while pers_[tp] < min_pers and tp in merged:
            tp = merged[tp][0]
        return tp
    cache = {}
    tipv = -np.ones(M0.n, dtype=np.int64)
    for v in idx:
        o = int(own[v])
        if o not in cache:
            cache[o] = live_tip(o)
        tipv[v] = cache[o]
    plen = np.array([pers_[int(tp)] if tp >= 0 else 0.0 for tp in tipv])
    return tipv, plen, pers_


def burn_field(fv, tipv, plen, frac, clamp_, min_pers, valid=None):
    ok = (tipv >= 0) & (plen >= min_pers)
    if valid is not None:
        ok &= valid
    burn_ = np.clip(frac * plen, clamp_[0], clamp_[1])
    f_ = np.where(ok, fv - (fv[np.maximum(tipv, 0)] - burn_), -1.0)
    return f_, np.where(ok, plen, 0.0), sorted({int(tp) for tp in tipv[ok]})


def tip_rows(tl, plen_d):
    return [[round(float(v), 2) for v in LV[i]] + [round(plen_d[i], 2)] for i in sorted(tl, key=lambda q: -plen_d[q])]


t = time.time()
# ---- spike tips (v1 rule: geodesic distance from a torso ring at the chest; persistence per spike). The floor tendrils
# are spikes of this field too: excluded (they are the dark base), so the embers stay on claws / wisps / shoulder + hat spikes
H_PRE0 = H
ring = (np.abs(zs_ - SEED_Z) < 0.35) & (r < 0.25 * H)
g = dijkstra(np.nonzero(ring)[0])
g[~np.isfinite(g)] = 0.0
spk, spk_plen, spk_pers = persistence(g, np.ones(M0.n, bool), MIN_SPIKE)
tipf, spk_len, tips = burn_field(g, spk, spk_plen, TIP_LEN, TIP_ABS, MIN_SPIKE,
                                 valid=(r[np.maximum(spk, 0)] > SPIKE_RMIN * H) & (tmem_v[np.maximum(spk, 0)] < 0.5))
if not SPIKE_TIPS:
    tipf[:] = -1.0; spk_len[:] = 0.0; tips = []
# ---- v4 maw fields (front projection x from the waist axis, z new frame) + skin depth + tendril region
RB_Z = np.arange(-0.2, Z_BODY_TOP + 0.2 + 1e-9, 0.05)
RB_TAB = np.array([R_body(float(z_)) for z_ in RB_Z])       # the procedural skin radius per height x angle bin
RH_TAB = np.array([R_hem(float(z_)) for z_ in RB_Z])        # ... without the rounded hem (the tendrils' base skin)


def tab_at(TAB, P):
    """per point: the table radius at its height (new frame) and angle, bilinear"""
    d_ = P[:, :2] - AX; zq = P[:, 2] - ZOFF
    f_ = np.clip((zq - RB_Z[0]) / 0.05, 0.0, len(RB_Z) - 1.000001); k_ = f_.astype(int); a_ = f_ - k_
    g_ = (np.arctan2(d_[:, 0], -d_[:, 1]) + math.pi) / (2 * math.pi) * NT; j_ = np.floor(g_).astype(int); b_ = g_ - j_
    j0, j1 = j_ % NT, (j_ + 1) % NT
    R0 = TAB[k_, j0] * (1 - b_) + TAB[k_, j1] * b_
    R1 = TAB[k_ + 1, j0] * (1 - b_) + TAB[k_ + 1, j1] * b_
    return R0 * (1 - a_) + R1 * a_


P2 = np.stack([LV[:, 0] - AX[0], zs_], 1)
front_v = (LV[:, 1] < AX[1]) & (th_deg < 85.0)
dsk = tab_at(RB_TAB, LV) - r                                  # depth behind the procedural skin (> 0 inside)
framef = np.where(front_v, np.maximum(poly_sdf2(P2, FRAME_POLY), -0.99), -1.0)
hrectf = np.where(front_v, np.maximum(poly_sdf2(P2, HOLE_POLY), -0.99), -1.0)
redf = RED_DEPTH * T_TOOTH - dsk                              # > 0: within the lip / teeth plate
# inside the mouth (any depth): within the cavity outline (+ margin), in front of the back bowl, inside the skin
cavf = np.minimum(poly_sdf2(P2, CAV_POLY) + 0.15, AX[1] + BOWL_C[1] + BOWL_R[1] + 0.3 - LV[:, 1])
cavf = np.where((dsk > 0.05) & (zs_ < Z_CHEST[1]), np.maximum(cavf, -0.99), -1.0)
tendf = np.where((zs_ < 6.0) & (amem_v < 0.3), np.clip(np.maximum(r - tab_at(RH_TAB, LV) - TEND_CUT, -0.99), -0.99, 5.0), -1.0)
tendf = np.where(tmem_v > 0.5, np.maximum(tendf, 0.05), tendf)   # a whole tendril is 'base' (the lifted curls too)
# ---- eyes (front-projected (x, z) field on the face)
face_front = (NV[:, 1] < -0.1) & (LV[:, 1] < AX[1] - 1.0) & (zs_ > EYE_BOX[1]) & (zs_ < EYE_BOX[2])
eyef = np.where(face_front, np.maximum(eye_field(LV[:, 0], zs_), -0.99), -1.0)
report["fields_seconds"] = round(time.time() - t, 1)

# =========================================================================== iso-contour cuts
bm, dup1 = bm_from(LV, LF)
FIELDS = {"z": zl, "r": r, "th": th_deg, "tip": tipf, "gtip": spk_len, "front": front_v.astype(float), "dsk": dsk,
          "frame": framef, "hrect": hrectf, "red": redf, "cav": cavf, "yb": LV[:, 1] - AX[1] - THROAT_Y, "tend": tendf, "eye": eyef,
          "amem": amem_v, "tmem": tmem_v}
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
    nsplit = 0
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
        nsplit += 1
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
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": nsplit, "face_connects": len(pairs)}


pos_ = lambda k: (lambda a, b: a[LAY[k]] > -0.999 and b[LAY[k]] > -0.999)
spike = lambda a, b: a[LAY["gtip"]] > MIN_SPIKE and b[LAY["gtip"]] > MIN_SPIKE
skin_f = lambda a, b: all(v[LAY["front"]] > 0.5 and v[LAY["dsk"]] < 0.35 and v[LAY["frame"]] > -0.999 for v in (a, b))
skin_in = lambda a, b: skin_f(a, b) and a[LAY["frame"]] > 0.0 and b[LAY["frame"]] > 0.0
in_frame = lambda a, b: all(v[LAY["front"]] > 0.5 and v[LAY["frame"]] > -1e-4 for v in (a, b))
interior = lambda a, b: all(v[LAY["cav"]] > 0.0 and v[LAY["red"]] < 0.0 for v in (a, b))
CUTS = [("z", HAT_APEX_Z + ZOFF, None), ("tip", 0.0, spike), ("frame", 0.0, skin_f), ("hrect", 0.05, skin_in), ("red", 0.0, in_frame),
        ("yb", 0.0, interior), ("tend", 0.0, pos_("tend")), ("eye", 0.0, pos_("eye"))]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.faces.index_update()
nf = len(bm.faces)
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1),
                      "rule": "edge split at the field's iso-value (snap within CUT_SNAP of a vertex) + face connects (v1 machinery)"}
low_me = bpy.data.meshes.new(UNIT)
bm.to_mesh(low_me)
bm.free()
low = bpy.data.objects.new(UNIT, low_me)
scene.collection.objects.link(low)
me = low.data
for nm in list(me.attributes.keys()):
    if nm in LAY:
        me.attributes.remove(me.attributes[nm])
report["tris_final"] = tri_count(me)
assert report["tris_final"] == nf


# =========================================================================== space map T (height + neck; v3's, moved up)
def T_apply(P):
    """smooth and monotone: (1) the neck column scaled radially by NECK_K about the neck axis (full inside NECK_R[0], off
    beyond NECK_R[1]; ramped over NECK_Z), (2) NECK_STRETCH extra height over NECK_STRETCH_Z (smoothstep displacement),
    (3) the whole figure scaled HEIGHT_K in z about the floor"""
    P = np.array(P, float, copy=True)
    zs0 = P[:, 2] - ZOFF
    d_ = P[:, :2] - NAX
    rr_ = np.hypot(d_[:, 0], d_[:, 1])
    kz = 1.0 - (1.0 - NECK_K) * smoothstep(NECK_Z[0], NECK_Z[1], zs0) * (1.0 - smoothstep(NECK_Z[2], NECK_Z[3], zs0))
    sc = 1.0 - (1.0 - kz) * (1.0 - smoothstep(NECK_R[0], NECK_R[1], rr_))
    P[:, :2] = NAX + d_ * sc[:, None]
    P[:, 2] = (P[:, 2] + NECK_STRETCH * smoothstep(NECK_STRETCH_Z[0], NECK_STRETCH_Z[1], zs0)) * HEIGHT_K
    return P


def Tz(z):
    return float(T_apply(np.array([[AX[0] + 10.0, AX[1], z + ZOFF]]))[0, 2])   # a height on the trunk (off the neck column)


LV_PRE = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", LV_PRE); LV_PRE = LV_PRE.reshape(-1, 3)
me.vertices.foreach_set("co", T_apply(LV_PRE).ravel()); me.update()
HV = T_apply(HV)
HIGH.data.vertices.foreach_set("co", HV.ravel()); HIGH.data.update()
H_PRE = H
LVT = T_apply(LV_PRE)
size = LVT.max(0) - LVT.min(0)
H = float(size[2])
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
_nk0 = (LV_PRE[:, 2] - ZOFF > NECK_MEAS_Z[0]) & (LV_PRE[:, 2] - ZOFF < NECK_MEAS_Z[1]) & (np.abs(LV_PRE[:, 0] - NAX[0]) < 3.0)
report["proportions"] = {"height_k": HEIGHT_K, "neck_k": NECK_K, "neck_stretch": NECK_STRETCH, "torso_stretch": S_UP, "neck_axis": NAX.round(4).tolist(),
                         "height_before_T": round(H_PRE, 4), "height_after_T": round(H, 4), "height_gain_pct": round(100 * (H / H_PRE - 1), 2),
                         "neck_width_before_after_T": [round(float(np.ptp(LV_PRE[_nk0, 0])), 3), round(float(np.ptp(LVT[_nk0, 0])), 3)],
                         "trunk_final_frame": {"floor_to_chest_junction": round(Tz(Z_KEEP), 3), "maw_frame_z": [round(Tz(FZ0), 3), round(Tz(FZ1), 3)],
                                               "waist_z": round(Tz(TRUNK_Z[2]), 3),
                                               "v3_floor_to_chest_junction": round(13.8 * 1.10, 3),
                                               "widths_front_view_by_design": {"maw_top": round(2 * PW(TRUNK_Z[3]), 3), "waist": round(2 * PW(TRUNK_Z[2]), 3),
                                                                               "maw_bottom": round(2 * PW(TRUNK_Z[1]), 3), "flare_top": round(2 * PW(TRUNK_Z[0]), 3)},
                                               "v3_width_at_the_maw_band_by_design": "2 x BOX_W = 11.0 (the rounded box)"},
                         "rule": "T applied to the low (after the region cuts) and the bake high; every constant above is in the pre-T frame"}
report["natural"] = {"height": round(H, 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "footprint": round(fp, 4), "units": "sculpt units (natural proportions, no refit; v4 = the stretched sculpt x T)",
                     "origin_shift_sculpt_units": SHIFT.round(5).tolist(),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint",
                                                     "ceilings": [CELL_MAX_H, CELL_MAX_FP]}}

# =========================================================================== regions
REG = ["body", "base", "tips", "lip", "maw_inner", "maw_throat", "teeth", "eyes"]
R_ = {n: i for i, n in enumerate(REG)}
zc, rc = FVAL["z"], FVAL["r"]
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
rid = np.full(nf, R_["body"], dtype=np.int32)
arm_c = FVAL["amem"] > 0.5
rid[(FVAL["tend"] > 0.0) & ~arm_c] = R_["base"]
rid[((FVAL["tip"] > 0.0) & (FVAL["gtip"] > MIN_SPIKE)) | (zc > HAT_APEX_Z + ZOFF)] = R_["tips"]
maw_f = (FVAL["front"] > 0.5) & (FVAL["frame"] > 0.0)
red_f = maw_f & (FVAL["red"] > 0.0)
inner_f = (FVAL["cav"] > 0.0) & (FVAL["red"] <= 0.0)
rid[red_f & (FVAL["hrect"] <= 0.05)] = R_["lip"]
rid[red_f & (FVAL["hrect"] > 0.05)] = R_["teeth"]
rid[inner_f] = R_["maw_inner"]
rid[inner_f & (FVAL["yb"] > 0.0)] = R_["maw_throat"]
rid[FVAL["eye"] > 0.0] = R_["eyes"]
report["region_rule"] = {
    "base": "the floor tendrils + the hem skin outside the base surface: faces TEND_CUT or more outside the base skin (iso-cut) or "
            "on a tendril (membership > 0.5) -- the dark shadow spreading on the ground; the floor darkening (BASE_DARK) ramps "
            "over every non-arm face below BASE_Z",
    "tips": "outer TIP_LEN of every persistent spike (v1 geodesic-persistence rule; the floor tendrils excluded) + the hat apex -- "
            "dim embers on the claws, wisp ends, shoulder spikes and hat",
    "lip": "front skin inside the maw FRAME outline (the waist-curved sides, the JAGGED top line; iso-cut) and outside the opening, "
           "within RED_DEPTH x T_TOOTH of the skin -- the red frame",
    "teeth": "the same red plate inside the opening outline: the fangs (upper, hanging) and v3's lower teeth + their side walls -- red",
    "maw_inner": "inside the cavity outline and deeper than the plate: cavity walls under the lips, plate backs -- yellow-orange",
    "maw_throat": "the same, behind THROAT_Y (the back bowl): the hot yellow throat",
    "eyes": "front-projected lens field on the smoothed face (EYE_IN -> EYE_OUT, EYE_TOP / EYE_BOT), iso-cut -- the face's only feature",
    "waist_axis": AX.round(4).tolist()}
report["tip_field"] = {"spikes_found": len(tips), "spikes_xyz_len": tip_rows(tips, spk_pers)[:24]}

# ---- cavity shade from the rebuilt high + deterministic per-face jitter + the base's floor darkening
t = time.time()
kd_h = KDTree(len(HV))
for i, p in enumerate(HV):
    kd_h.insert(p, i)
kd_h.balance()
hme = HIGH.data
ev_h = np.empty(len(hme.edges) * 2, dtype=np.int64); hme.edges.foreach_get("vertices", ev_h); ev_h = ev_h.reshape(-1, 2)
nv_h = np.empty(len(hme.vertices) * 3); hme.vertex_normals.foreach_get("vector", nv_h); nv_h = nv_h.reshape(-1, 3)
deg = np.bincount(ev_h.ravel(), minlength=len(HV)).astype(float)


def nmean(X):
    s_ = np.zeros_like(X)
    for k in range(X.shape[1]):
        s_[:, k] = np.bincount(ev_h[:, 0], X[ev_h[:, 1], k], minlength=len(HV)) + np.bincount(ev_h[:, 1], X[ev_h[:, 0], k], minlength=len(HV))
    return s_ / np.maximum(deg, 1)[:, None]


el = np.linalg.norm(HV[ev_h[:, 0]] - HV[ev_h[:, 1]], axis=1).mean()
cav = ((nmean(HV) - HV) * nv_h).sum(1) / el
for _ in range(6):
    cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
cav = np.clip(cav / (np.percentile(np.abs(cav), 95) + 1e-9), -1, 1)
fcav = np.array([np.mean([cav[j] for (_, j, _) in kd_h.find_n(p, 8)]) for p in FC])
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
bmask = (zc - ZOFF < BASE_Z) & ~arm_c                         # the shadow darkens toward the floor (tendrils + bell)
shade[bmask] *= BASE_DARK + (1 - BASE_DARK) * smoothstep(0.0, BASE_Z, zc[bmask] - ZOFF)
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k
report["cavity_seconds"] = round(time.time() - t, 1)
fa = np.empty(nf); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == R_[n]].sum() / fa.sum()), 4) for n in REG}

# facing landmark (the mouth is the identity): waist axis at mouth height -> area centroid of the red lip framing the
# opening; the eyes' centroid is reported beside it
lipm = rid == R_["lip"]
anchor = np.array([AX[0], AX[1], float(np.average(FC[lipm, 2], weights=fa[lipm]))])
landmark = np.average(FC[lipm], axis=0, weights=fa[lipm])
dvec = landmark - anchor
eyem = rid == R_["eyes"]
eye_c = np.average(FC[eyem], axis=0, weights=fa[eyem]) if eyem.any() else None
report["facing"] = {"rule": "waist axis (at the lip's mean height) -> area centroid of the red lip region that frames the mouth opening",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2),
                    "eyes_centroid": eye_c.round(4).tolist() if eye_c is not None else None}

# =========================================================================== flat + UV
me.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

# =========================================================================== material
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
PAL.apply_material(mat, pal_default)
me.materials.append(mat)
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "hero"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"
low["conquest_version"] = "duskmaw v4 (shadow lord: curving maw / long slender trunk / floor tendrils / clawed arms)"
MAW_BAND = [Tz(z_) for z_ in HOLE_Z]
low["conquest_maw_band"] = MAW_BAND            # the maw's hole height band (final frame): the see-through proof's ray band
# measurement handles (final frame) for duskmaw_base_measure.py / duskmaw_render.py
_armP = {s_: T_apply(np.asarray(ARM_GEO[s_]["P"]) + np.array([0.0, 0.0, ZOFF])) for s_ in ARM_GEO}
low["conquest_arm_L"] = _armP["L"].ravel().tolist()     # root, shoulder, elbow, wrist (x y z each)
low["conquest_arm_R"] = _armP["R"].ravel().tolist()
low["conquest_arm_radius"] = list(ARM_RAD)
low["conquest_trunk_band"] = [Tz(TRUNK_Z[0]), Tz(Z_KEEP)]

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris_final", "retopo", "eye_refinement", "regions_faces",
                                                             "regions_area_share", "facing", "tip_field", "proportions", "natural")}))
    print("TENDRILS", json.dumps(report["sdf_rebuild"]["tendrils"]))
    print("ARMS", json.dumps(report["sdf_rebuild"]["arms"]))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake (normal + AO from the rebuilt high)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
# the NORMAL bake is not byte-deterministic (per-process Cycles tie-break, the vampito finding): duskmaw_run.ps1
# -Determinism gates it with duskmaw_bake_diff.py (every other digest must be identical)
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
assert (lt_ == 3).all(), "low must be all triangles"
UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for tri in UVn[mask_faces]:
        p = tri * res
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
    nt.nodes.active = node
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
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "normal_baked_pct_of_uv_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_dev_mean": round(float(devn[cov_n & alltex_n].mean()), 4),
    "ao_baked_pct_of_uv_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


bstats["pixel_sha"] = {"normal": sha(px[:, :3]), "ao": sha(pa[:, :1])}
np.save(NORMAL_NPY, px[:, :3])
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["high"] = "the SDF-rebuilt surface (%d faces)" % len(HF)
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGH.data
bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True

uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV after the iso cuts (66 deg, margin 0.004), repacked", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()), "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"], "skins": PAL.list_skins(UNIT)}


# ---- see-through proof (ray casts through the waist core, every 15 degrees round the figure)
def see_through(V, F, axis_xy, z_band=(6.0, 9.2), half_w=2.5, step=0.1):
    bvh = BVHTree.FromPolygons(np.asarray(V).tolist(), F)
    rows = {}
    for deg_ in range(0, 360, 15):
        a = math.radians(deg_)
        d = Vector((-math.sin(a), math.cos(a), 0.0))                  # the ray travels from the camera at yaw deg_ inward
        e1 = Vector((math.cos(a), math.sin(a), 0.0))
        start = Vector((axis_xy[0], axis_xy[1], 0.0)) - d * 60.0
        n_ = miss = 0
        for u in np.arange(-half_w, half_w + 1e-9, step):
            for z in np.arange(z_band[0], z_band[1] + 1e-9, step):
                o = start + e1 * float(u) + Vector((0, 0, float(z)))
                hit = bvh.ray_cast(o, d, 120.0)
                n_ += 1
                miss += hit[0] is None
        rows[deg_] = round(100.0 * miss / n_, 2)
    return rows


LFf = [list(p.vertices) for p in me.polygons]
st = see_through(LVf, LFf, AX, z_band=tuple(MAW_BAND))
report["see_through"] = {"rule": "rays through the waist core (|u| <= 2.5 units about the waist axis, z = the maw's hole band "
                                 "MAW_BAND %.2f-%.2f) " % tuple(MAW_BAND) +
                                 "from 24 yaws (0 = the front camera, 180 = behind); value = % of rays that pass straight through",
                         "pct_rays_through_by_yaw": st, "max_pct": max(st.values())}
print("SEE_THROUGH", json.dumps(report["see_through"]))
bpy.context.preferences.filepaths.save_version = 0
set_tex_paths("//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig (shadow lord)
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS, "v1_note": "v1's carried shipped clips (attack/hit/death) are dropped "
       "since v2 (artist scope: idle + locomotion); they remain in git history with the v1 build"}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
Mw = K.MeshData(low)
W_ = Mw.W                                                 # final (post-T) coordinates
Wp = LV_PRE                                               # the same vertices before T: the weights' frame (constants' frame)
lo_n, hi_n = W_.min(0), W_.max(0)
H_new = float(hi_n[2] - lo_n[2])
cx, cy = float(AX[0]), float(AX[1])                       # the rig stands on the waist axis
zo = lambda z: z + ZOFF
BONES_PRE = [("base", (cx, cy, zo(BASE_BONE[0])), (cx, cy, zo(BASE_BONE[1])), "root", False),
             ("sway", (cx, cy, zo(SWAY_Z[0])), (cx, cy, zo(SWAY_Z[1])), "base", False),
             ("jaw", (cx, cy, zo(JAW_Z[0])), (cx, cy, zo(JAW_Z[1])), "sway", False),
             ("spine", (cx, cy, zo(SPINE_Z0)), (cx, cy, zo(CHEST_Z[0])), "sway", False),
             ("chest", (cx, cy, zo(CHEST_Z[0])), (cx, cy, zo(CHEST_Z[1])), "spine", True),
             ("head", (cx, cy, zo(HEAD_Z[0])), (cx, cy, zo(HEAD_Z[1])), "chest", True),
             ("crown", (cx, cy, zo(CROWN_Z[0])), (cx, cy, zo(CROWN_Z[1])), "head", True)]
for sgn, s in ((1, "L"), (-1, "R")):
    BONES_PRE.append(("arm." + s, (cx + sgn * RAISED_SH[0], cy, zo(RAISED_SH[1])), (cx + sgn * RAISED_EL[0], cy, zo(RAISED_EL[1])), "chest", False))
    BONES_PRE.append(("blade." + s, (cx + sgn * RAISED_EL[0], cy, zo(RAISED_EL[1])), (cx + sgn * RAISED_TIP[0], cy, zo(RAISED_TIP[1])), "arm." + s, True))
    Pa = ARM_GEO[s]["P"] + np.array([0.0, 0.0, ZOFF])
    hand_end = ARM_GEO[s]["palm"] + np.array([0.0, 0.0, ZOFF]) + ARM_GEO[s]["D"] * 1.2
    BONES_PRE.append(("limb." + s, tuple(Pa[1]), tuple(Pa[2]), "chest", False))
    BONES_PRE.append(("fore." + s, tuple(Pa[2]), tuple(hand_end), "limb." + s, True))
for i in CURL_IDX:
    tpiv = np.array(TEN_INFO[i]["pivot"]) - S2 + np.array([0.0, 0.0, ZOFF])
    ttip = np.array(TEN_INFO[i]["tip"]) - S2 + np.array([0.0, 0.0, ZOFF])     # pivot -> tip: >= 0.5 x the tendril apart
    BONES_PRE.append(("tend.%d" % i, tuple(tpiv), tuple(ttip), "base", False))
BH_PRE = {b[0]: (np.array(b[1]), np.array(b[2])) for b in BONES_PRE}
BONES = [(nm, tuple(T_apply(np.array([h]))[0]), tuple(T_apply(np.array([t_]))[0]), p_, c_) for (nm, h, t_, p_, c_) in BONES_PRE]
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.06 * H_new); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p, c) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_)
    e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = c
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
BH = {b[0]: (np.array(b[1]), np.array(b[2])) for b in BONES}

# ---- analytic weights (<= 4 influences), on the pre-T coordinates (Wp) with the pre-T bones (BH_PRE)
x_, y_, z_ = Wp[:, 0] - cx, Wp[:, 1] - cy, Wp[:, 2]
zsk = z_ - ZOFF
rr = np.hypot(x_, y_)
# upper-jaw field: split at the maw's mid line MAW_MID; tight (U_W[0]) across the tooth plates so every tooth rides its
# jaw rigidly, wide (U_W[1]) through the cheeks, the cavity walls and the back so the bite squashes smoothly there
dsk_w = tab_at(RB_TAB, Wp) - rr
hw_v = np.array([hole_hw(float(z)) for z in np.clip(zsk, HZ0, HZ1)])
tight = (1 - smoothstep(hw_v - 0.2, hw_v + 1.0, np.abs(x_))) * (1 - smoothstep(0.9 * T_TOOTH, 1.6 * T_TOOTH, dsk_w)) * (y_ < 0)
uw = U_W[1] + (U_W[0] - U_W[1]) * tight
u_up = smoothstep(MAW_MID - uw, MAW_MID + uw, zsk)
# lower chain: base (ground ring) -> sway -> jaw
w_base = 1.0 - smoothstep(BASE_W[0], BASE_W[1], zsk)
w_jaw = (1.0 - w_base) * smoothstep(JAW_W[0], JAW_W[1], zsk)
w_sway = 1.0 - w_base - w_jaw
# upper chain: spine -> chest -> head -> crown by height
joints = [BH_PRE["chest"][0][2], BH_PRE["head"][0][2], BH_PRE["crown"][0][2]]
names_up = ["spine", "chest", "head", "crown"]
up_w = np.zeros((Mw.n, 4))
prev = np.ones(Mw.n)
for k_, zj in enumerate(joints):
    s_ = smoothstep(zj - W_BAND, zj + W_BAND, z_)
    up_w[:, k_] = prev * (1 - s_)
    prev = prev * s_
up_w[:, 3] = prev
# raised shoulder spikes (v1 rule, absolute bands)
side_ = np.sign(x_)
ax_ = np.abs(x_)
armness = smoothstep(RAISED_X[0], RAISED_X[1], ax_) * smoothstep(zo(RAISED_Z[0]) - W_BAND, zo(RAISED_Z[0]) + W_BAND, z_) * \
    (1 - smoothstep(zo(RAISED_Z[1]) - W_BAND, zo(RAISED_Z[1]) + W_BAND, z_))
bladeness = smoothstep(RAISED_EL[0] - 1.2 * W_BAND, RAISED_EL[0] + 1.2 * W_BAND, ax_)
Wb = np.zeros((Mw.n, len(DEFORM)))
for k_, nm in enumerate(names_up):
    Wb[:, J[nm]] += up_w[:, k_] * u_up * (1 - armness)
Wb[:, J["base"]] += w_base * (1 - u_up) * (1 - armness)
Wb[:, J["sway"]] += w_sway * (1 - u_up) * (1 - armness)
Wb[:, J["jaw"]] += w_jaw * (1 - u_up) * (1 - armness)
L_ = side_ > 0
for sgn_m, s in ((L_, "L"), (~L_, "R")):
    Wb[sgn_m, J["arm." + s]] += (armness * (1 - bladeness))[sgn_m]
    Wb[sgn_m, J["blade." + s]] += (armness * bladeness)[sgn_m]
# hanging arms (limb / fore by arc length across the elbow; claws + wisps ride the fore) and floor tendrils (the ground:
# base, the curl tendrils ramp into their bone toward the tip)
t = time.time()
amem, a_i, tmem, t_i = membership(Wp)
Wa = np.zeros_like(Wb)
for s, sgn in (("L", 1), ("R", -1)):
    m_ = ARM_S[a_i, 5] == sgn
    s_el = ARM_GEO[s]["s_knots"][2]
    fore = smoothstep(s_el - ELBOW_BLEND, s_el + ELBOW_BLEND, ARM_S[a_i, 4])
    fore = np.where(ARM_S[a_i, 6] >= 2, 1.0, fore)
    Wa[m_, J["limb." + s]] = (1 - fore)[m_]
    Wa[m_, J["fore." + s]] = fore[m_]
Wtd = np.zeros_like(Wb)
tidx = TEN_S[t_i, 5].astype(int)
tu = TEN_S[t_i, 4]
c_ = np.zeros(Mw.n)
for i in CURL_IDX:
    mi = tidx == i
    c_[mi] = smoothstep(CURL_PIVOT[0], CURL_PIVOT[1], tu[mi])
    Wtd[mi, J["tend.%d" % i]] = c_[mi]
Wtd[:, J["base"]] = 1.0 - c_
amem = np.minimum(amem, 1.0 - tmem)
Wt = Wb * (1.0 - amem - tmem)[:, None] + Wa * amem[:, None] + Wtd * tmem[:, None]
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx = np.argsort(-Wt, 1, kind="stable")[:, 4:]
    np.put_along_axis(Wt, idx, 0.0, 1)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
TEND_V = tmem > 0.5
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "ground_ring_base_weight_min": round(float(Wt[(z_ < lo_n[2] + 0.05) & ~(tmem > 0.05), J["base"]].min()), 4),
                  "arm_vertices": int((amem > 0.5).sum()), "tendril_vertices": int(TEND_V.sum()), "seconds": round(time.time() - t, 1),
                  "rule": "on the pre-T coordinates: body = upper-jaw field u (split at MAW_MID, tight on the tooth plates) x "
                          "(spine/chest/head/crown by height) + (1-u) x (base -> sway -> jaw by height) + the raised spikes "
                          "(arm/blade, v1 bands); hanging arms (membership: nearest arm skeleton sample, falloff past its radius, "
                          "faded in past the shoulder) = limb -> fore across the elbow (claws, wisps = fore); floor tendrils "
                          "(membership outside the base skin) = base, the CURL_IDX ones ramping into their tend.* bone toward the tip"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig

# ---- clips: closed-form curves keyed on every frame (the last frame equals the first: seam 0 by construction)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST3 = {b.name: Matrix([list(r_[:3]) for r_ in b.matrix_local[:3]]) for b in arm_data.bones}


def world_rot(bone, axis, deg_):
    """pose quaternion (bone local) for a rotation of deg_ about a WORLD axis at the bone head"""
    B = REST3[bone]
    Rw = Matrix.Rotation(math.radians(deg_), 3, Vector(axis))
    return (B.inverted() @ Rw @ B).to_quaternion()


def world_loc(bone, v):
    """pose location (bone local) for a WORLD translation v"""
    return REST3[bone].inverted() @ Vector(v)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def chomp_curve(f):
    """jaw gap offset (units, + = wider than rest) for idle frame f: wind-up -> snap shut -> hold -> ease back"""
    a0, a1 = CHOMP["open_f"]; s0, s1 = CHOMP["shut_f"]; h0, h1 = CHOMP["hold_f"]; r0, r1 = CHOMP["rest_f"]
    shut = -(GAP_REST - CHOMP_SHUT_GAP)
    if f <= a0:
        return 0.0
    if f <= a1:
        return CHOMP_OPEN * ease((f - a0) / (a1 - a0))
    if f <= s1:
        u = (f - s0) / (s1 - s0)
        return CHOMP_OPEN + (shut - CHOMP_OPEN) * (u ** 2)          # accelerating snap
    if f <= h1:
        return shut
    if f <= r1:
        return shut * (1 - ease((f - r0) / (r1 - r0)))
    return 0.0


# the maw's tooth tips on the final mesh: per designed tooth, the lowest (upper jaw) / highest (lower jaw) vertex of the
# 'teeth' region within its column; pairs = neighbouring upper/lower teeth (they interlock)
tv = np.zeros(len(W_), bool)
for f_, rg in zip(me.polygons, rid):
    if rg == R_["teeth"]:
        tv[list(f_.vertices)] = True
tip_rows_ = []
for kind, TT in (("upper", UP_TIPS), ("lower", LO_TIPS)):
    for x_t, z_t in TT:
        m_ = tv & (np.abs(Wp[:, 0] - cx - x_t) < 0.3 * PITCH) & (np.abs(Wp[:, 2] - ZOFF - z_t) < 1.2) & (Wp[:, 1] < cy)
        if not m_.any():
            continue
        c_i = np.nonzero(m_)[0]
        i_ = int(c_i[np.argmin(Wp[c_i, 2])] if kind == "upper" else c_i[np.argmax(Wp[c_i, 2])])
        tip_rows_.append((kind, x_t, i_))
pairs = []
for ku, xu, iu in tip_rows_:
    for kl, xl, il in tip_rows_:
        if ku == "upper" and kl == "lower" and abs(xu - xl) < 0.6 * PITCH:
            pairs.append((iu, il, float(W_[iu, 2] - W_[il, 2]), xu, xl))
pairs.sort(key=lambda q: q[2])
GAP_REST = pairs[0][2] if pairs else 1.0
rep["teeth_tips"] = [{"jaw": k_, "design_x": round(x_t, 3), "vertex": i_, "xyz": W_[i_].round(3).tolist()} for k_, x_t, i_ in tip_rows_]
rep["teeth_pairs"] = [{"upper_x": round(q[3], 3), "lower_x": round(q[4], 3), "gap_rest": round(q[2], 3)} for q in pairs]


def key_clip(name, n_frames, pose_fn):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(1, n_frames + 1):
        scene.frame_set(f)
        for pb in rig.pose.bones:
            pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
        pose = pose_fn(f, (f - 1) / (n_frames - 1))
        for bn, (loc, q) in pose.items():
            pb = rig.pose.bones[bn]
            if loc is not None:
                pb.location = loc
            if q is not None:
                pb.rotation_quaternion = q
        for bn in DEFORM:
            if bn == "base":
                continue                       # the ground ring never moves
            pb = rig.pose.bones[bn]
            pb.keyframe_insert("location", frame=f, group=bn)
            pb.keyframe_insert("rotation_quaternion", frame=f, group=bn)
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, n_frames
    act.use_cyclic = True
    return act


def qmul(*qs):
    out = Quaternion()
    for q in qs:
        out = out @ q
    return out


def idle_pose(f, t):
    g = chomp_curve(f)                                   # + = open wider
    up_ = CHOMP_UPPER * g; dn = (1 - CHOMP_UPPER) * g
    bob = IDLE_BOB * math.sin(2 * math.pi * t)
    bite = max(0.0, -g) / max(GAP_REST - CHOMP_SHUT_GAP, 1e-6)          # 0..1 on the bite
    windup = max(0.0, g) / max(CHOMP_OPEN, 1e-6)
    P = {"spine": (world_loc("spine", (0, 0, up_ + bob)), None),
         "jaw": (world_loc("jaw", (0, 0, -dn)), None),
         "head": (None, world_rot("head", (1, 0, 0), -3.0 * windup + 2.0 * bite)),
         "crown": (None, world_rot("crown", (0, 1, 0), 2.0 * math.sin(2 * math.pi * t)))}
    for s, sg in (("L", 1), ("R", -1)):
        flare = CHOMP_ARMS * (bite - 0.5 * windup)
        P["arm." + s] = (None, world_rot("arm." + s, (0, 1, 0), -sg * (flare + 2.0 * math.sin(2 * math.pi * t))))
        P["blade." + s] = (None, world_rot("blade." + s, (0, 1, 0), -sg * 0.6 * flare))
        hang = HANG_FLARE * (bite - 0.5 * windup) + HANG_SWAY * math.sin(2 * math.pi * t + 0.9)
        P["limb." + s] = (None, world_rot("limb." + s, (0, 1, 0), -sg * hang))
        P["fore." + s] = (None, world_rot("fore." + s, (1, 0, 0), -CLAW_FLEX * bite + 1.5 * math.sin(2 * math.pi * t + 1.6)))
    for k_, i in enumerate(CURL_IDX):
        sgn_c = TEND_DIRS[i % len(TEND_DIRS)]
        P["tend.%d" % i] = (None, world_rot("tend.%d" % i, (0, 0, 1), -sgn_c * CURL_DEG * math.sin(2 * math.pi * (t + k_ / len(CURL_IDX)))))
    return P


def walk_pose(f, t):
    w = 2 * math.pi * t
    lean = LEAN_DEG + LEAN_OSC * math.sin(w)
    P = {"sway": (None, qmul(world_rot("sway", (0, 1, 0), GLIDE_ROLL * math.sin(w)), world_rot("sway", (1, 0, 0), lean))),
         "spine": (world_loc("spine", (0, 0, GLIDE_BOB * math.sin(2 * w))), None),
         "head": (None, world_rot("head", (1, 0, 0), -0.5 * lean)),
         "crown": (None, world_rot("crown", (1, 0, 0), -CROWN_TRAIL - 2.0 * math.sin(w - 0.8)))}
    for s, sg in (("L", 1), ("R", -1)):
        P["arm." + s] = (None, qmul(world_rot("arm." + s, (0, 0, 1), sg * (ARM_TRAIL + ARM_FLUTTER * math.sin(w - 0.6))),
                                    world_rot("arm." + s, (0, 1, 0), -sg * 3.0 * math.sin(2 * w - 1.0))))
        P["blade." + s] = (None, world_rot("blade." + s, (0, 0, 1), sg * (0.5 * ARM_TRAIL + ARM_FLUTTER * math.sin(w - 1.4))))
        P["limb." + s] = (None, world_rot("limb." + s, (1, 0, 0), HANG_TRAIL + HANG_FLUTTER * math.sin(w - 0.7)))
        P["fore." + s] = (None, world_rot("fore." + s, (1, 0, 0), 0.6 * HANG_TRAIL + HANG_FLUTTER * math.sin(w - 1.5)))
    return P


NEW_ACTS = {"idle": key_clip("idle", IDLE_N, idle_pose), "walk": key_clip("walk", WALK_N, walk_pose)}
rig.animation_data.action = None


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


pair_v = [(q[0], q[1]) for q in pairs]
ground = (W_[:, 2] < lo_n[2] + 0.05) & (tmem < 0.05)        # the core floor ring (the tendrils are measured apart)
tfloor = (W_[:, 2] < lo_n[2] + 0.05) & TEND_V
curl_v = np.isin(tidx, list(CURL_IDX)) & TEND_V & (c_ > 0.5)
wrist_v = {s: int(np.argmin(np.linalg.norm(Wp - (ARM_GEO[s]["P"][3] + np.array([0.0, 0.0, ZOFF])), axis=1))) for s in ("L", "R")}
clip_rep = {}
for cn, act in NEW_ACTS.items():
    K.assign_action(rig, act)
    f0, f1 = 1, int(round(act.frame_range[1]))
    first = last = None
    gaps, gslide, gz, rootoff, spine_dz, jaw_dz, sway_deg, pair_gaps = [], 0.0, 0.0, 0.0, [], [], [], []
    tf_lift, curl_travel, wrist_travel = 0.0, 0.0, 0.0
    for f in range(f0, f1 + 1):
        scene.frame_set(f)
        C = eval_coords(low)
        if f == f0:
            first = C
        if f == f1:
            last = C
        if pair_v:
            gaps.append(min(float(C[a, 2] - C[b, 2]) for a, b in pair_v))
            pair_gaps.append([float(C[a, 2] - C[b, 2]) for a, b in pair_v])
        gslide = max(gslide, float(np.linalg.norm((C - W_)[ground, :2], axis=1).max()))
        gz = max(gz, float(np.abs(C[ground, 2] - W_[ground, 2]).max()))
        tf_lift = max(tf_lift, float(np.abs(C[tfloor, 2] - W_[tfloor, 2]).max()) if tfloor.any() else 0.0)
        curl_travel = max(curl_travel, float(np.linalg.norm((C - W_)[curl_v], axis=1).max()) if curl_v.any() else 0.0)
        wrist_travel = max(wrist_travel, max(float(np.linalg.norm(C[v] - W_[v])) for v in wrist_v.values()))
        rootoff = max(rootoff, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        spine_dz.append(float((rig.pose.bones["spine"].head - arm_data.bones["spine"].head_local)[2]))
        jaw_dz.append(float((rig.pose.bones["jaw"].head - arm_data.bones["jaw"].head_local)[2]))
        ys_ = rig.pose.bones["spine"].matrix.to_3x3() @ Vector((0, 1, 0))
        sway_deg.append(math.degrees(math.atan2(-ys_[1], ys_[2])))
    seam = float(np.linalg.norm(first - last, axis=1).max())
    row = {"frames": [f0, f1], "loop_frames": f1 - f0, "seconds": round((f1 - f0) / K.FPS, 3), "cyclic": True,
           "seam_units_x1000": round(seam * 1000, 4), "ground_ring_slide_max": round(gslide, 6), "ground_ring_lift_max": round(gz, 6),
           "tendril_floor_lift_max": round(tf_lift, 6), "curl_tendril_tip_travel_max": round(curl_travel, 4),
           "wrist_travel_max": round(wrist_travel, 4), "root_offset_max": round(rootoff, 8),
           "spine_dz_range": [round(min(spine_dz), 4), round(max(spine_dz), 4)], "jaw_dz_range": [round(min(jaw_dz), 4), round(max(jaw_dz), 4)],
           "upper_body_forward_lean_deg_range": [round(min(sway_deg), 2), round(max(sway_deg), 2)]}
    if gaps:
        row["front_teeth_gap"] = {"rest": round(gaps[0], 4), "max_open": round(max(gaps), 4), "min_shut": round(min(gaps), 4),
                                  "frame_open": int(np.argmax(gaps)) + f0, "frame_shut": int(np.argmin(gaps)) + f0,
                                  "travel": round(max(gaps) - min(gaps), 4), "travel_pct_H": round(100 * (max(gaps) - min(gaps)) / H_new, 3),
                                  "every_pair_at_shut": [round(v, 4) for v in pair_gaps[int(np.argmin(gaps))]],
                                  "pairs_interlocked_at_shut": int(sum(v < 0 for v in pair_gaps[int(np.argmin(gaps))])), "pairs": len(pair_v)}
    clip_rep[cn] = row
    print("CLIP", cn, json.dumps(row))
rep["clips"] = clip_rep
rep["clip_rules"] = {
    "idle": "float bob (IDLE_BOB, 1 per loop) + ONE chomp per loop: wind-up open CHOMP_OPEN over open_f, accelerating snap to a "
            "tip-to-tip gap CHOMP_SHUT_GAP over shut_f, hold, ease back over rest_f; the upper jaw (spine) does CHOMP_UPPER of "
            "the travel, the lower jaw (jaw bone) the rest; raised spikes flare CHOMP_ARMS, hanging arms flare HANG_FLARE and the "
            "claws snap CLAW_FLEX on the bite; the CURL_IDX tendrils curl +-CURL_DEG about the vertical (a third of a loop apart)",
    "walk": "GLIDE: no stepping; sway leans the figure forward LEAN_DEG (+-LEAN_OSC) with GLIDE_ROLL side drift, float bob "
            "GLIDE_BOB (2 per loop), raised spikes trail ARM_TRAIL, hanging arms trail HANG_TRAIL + flutter, crown trails "
            "CROWN_TRAIL; the ground ring (base) and the tendrils are never keyed -- in place, the game moves the unit",
    "keys": "every frame, LINEAR, closed-form curves (integer harmonics / beats that end at rest): last frame == first frame"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "duskmaw v4: shadow lord (base/sway/jaw | spine/chest/head/crown, raised spikes arm+blade x2, hanging " \
                      "arms limb+fore x2, tendril curls x3) + contract root"
low["conquest_clips"] = list(NEW_ACTS)
low["conquest_clip_status"] = "v4: idle (float + chomp on the curving maw + tendril curls) and walk (glide), authored"
for a in list(bpy.data.actions):
    if a.name not in NEW_ACTS:
        bpy.data.actions.remove(a)
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 7. identity-scale glb
for o in scene.objects:
    o.select_set(o is rig or o is low)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, NEW_ACTS["idle"])
t = time.time()
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False)
rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "structure": "armature object identity (no scale), mesh child identity, natural scale; game model_scale "
                           "%.5f reaches the 1.8 m cell (report-only)" % k_fit}
rig.animation_data.action = None
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
rep["digest_rig"] = hashlib.sha256(np.round(Wt, 6).astype(np.float32).tobytes() +
                                   np.array([[v for b in rep["bones"] for v in b["head"] + b["tail"]]], np.float32).tobytes()).hexdigest()[:16]
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "seconds")}))
sys.stdout.flush()
os._exit(0)
