"""Volcano Turtle -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/volcano_turtle_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x3: Shield Rodent, Storm Vessel, Volcano Turtle" (section VOLCANO TURTLE)
+ the sheet design/reference/volcano_turtle-character-sheet.webp (intake: design/reference/volcano_turtle/intake.json, orthos
FRONT + BACK, the side view is 3/4, --cuts 460,925, floor pinned to the feet at y 808). Contract:
improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet; z = (808 - y_px) / 100 in every view (floor line y 808).
  FRONT view (X, heights):  X = (x_px - 230.5) / 100   (camera on -Y: image right = +X = the unit's LEFT)
  BACK view  (X, heights):  X = -(x_px - 685) / 100    (camera on +Y: image right = -X = the unit's right)
  SIDE view  (depth only):  Y = (x_px - 1060) / 100    (faces image-LEFT: image left = -Y = the front; 1060 = the body's
                            depth centre: belly front 942, shell back 1249). The side view is drawn 3/4, so its depths are
                            approximate (reported); every X and every height comes from the two true orthos.
Front = -Y, +X = the unit's LEFT, floor z = 0 under the soles.

THE BODY PLAN as drawn: an upright, bipedal, chunky turtle of dark volcanic rock: a barrel torso with a tan plastron belly
(three horizontal segment lines, a V tip between the legs), two thick stubby leg columns with two tan toe claws each, a
round head with a small snout, glowing angular eyes, a rocky brow (three dark angular blocks) and a VOLCANO CRATER on top
(glowing lava wall + lip, darker lava dripping down the head) from which a constant FLAME rises (orange outer tongues,
yellow inner core, four small flicks). Big angular rock shoulder plates over short upper arms and OVERSIZED fists (tan
curled fingers at the bottom-front), crossed by glowing LAVA CRACKS. A faceted TURTLE SHELL on the back (nine plates,
glowing lava cracks between them per the back view, a tan rim). A short, stubby, pointed tail (tan underside).

Pipeline:
  1. BODY = ONE SDF (magmoo_sdf banded grid + smooth unions): torso (elliptic sections on knots measured off the front /
     back / side rows), head + snout, the crater (capped cone, hollowed on top), two leg columns, four toe claws, the tail.
     Marching tets = the HIGH (bake source); collapse decimation = the LOW (deterministic).
  2. Regions iso-cut into the body low (refined in the plastron + crater zones first): the plastron outline + its three
     segment lines, the toes, the tail underside, the crater lava band and the lava DRIP line (a zig-zag of drip tongues).
  3. ARM = one SDF per arm (the -X arm = the +X arm mirrored): short upper arm, a superellipsoid fist, three finger lobes,
     two chamfered rock shoulder blocks. Lava cracks (thin glowing bands: a fist ring, an upper-arm ring, an outer fist crack,
     a diagonal shoulder crack) + the knuckle region iso-cut.
     SHELL = one SDF: nine inset tangent planes on an ellipsoid dome (faceted plates) + a tan rim ring; the glowing cracks are
     the plate seams the back view draws glowing (a continuous seam-distance field), iso-cut.
  4. CLOSED PARTS (volcano_turtle_parts.py): eyes + dark sockets, the three brow blocks, nostrils, the mouth line (a ribbon on
     the surface), the FLAME (8 layered closed tongues: 5 orange outer, 3 yellow inner) + 4 flame flicks.
  5. palette -> Col / Glow, flat shaded, Smart UV, bake normal + AO from the SDF highs + every closed part except the flame.
  6. RIG: root > hips > spine > chest > head > flame; chest > arm_{L,R}.upper > .fore > .fist; hips > leg_{L,R}.thigh >
     .shin > .foot; hips > tail. Clips PLACEHOLDER (movement + height open): idle = breathing + a fist flex + a flame
     flicker (scale pulse); walk = a heavy in-place biped stomp, stance feet planted on the floor. glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                   # noqa: E402  (read-only use)
import palettes as PAL               # noqa: E402  (read-only use)
import magmoo_sdf as SD              # noqa: E402  (read-only use: the SDF kit)
import volcano_turtle_parts as FP    # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "volcano_turtle"
CHAR_ID = "volcano_turtle"            # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
DEGEN_DIST = 1e-4                     # cut slivers shorter than this are collapsed (ember_stump uv_health lesson)
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- TORSO: (z, centre y, half-width x, half-depth y), smooth (Catmull) through the knots.
#   half-width: front view torso edges between the arms + back view (the shell rim x 552-824 at y 570-620 sits on it);
#   depth: side view belly front x 991 (y 450) / 965 (500) / 945 (550) / 942 (600) / 949 (650) / 961 (700), the back
#   under the shell rim ~x 1135 (Y +0.75); the bottom is the plastron V tip between the legs (front y 762 -> z 0.46)
TORSO_KNOTS = [(0.46, -0.08, 0.14, 0.14), (0.72, -0.12, 0.78, 0.70), (1.08, -0.15, 1.20, 0.86), (1.58, -0.18, 1.38, 0.93),
               (2.08, -0.21, 1.42, 0.97), (2.58, -0.20, 1.40, 0.95), (3.08, -0.12, 1.30, 0.83), (3.58, 0.00, 1.08, 0.69),
               (3.95, 0.04, 0.80, 0.56), (4.20, 0.04, 0.60, 0.45)]
# ---- HEAD (front view: widest +-0.9 at y 360-420, chin y 440 (z 3.68), the crater meets it at y 330 x 167-303; side view:
#   face front x 946 (Y -1.14) at y 400, the back of the head runs into the shell top at x ~1120)
HEAD_C, HEAD_R, HEAD_K = (0.0, -0.30, 4.22), (0.93, 0.80, 0.72), 0.22      # "head size"; neck blend
SNOUT_C, SNOUT_R, SNOUT_K = (0.0, -0.86, 3.92), (0.34, 0.22, 0.20), 0.10   # the small muzzle bump round the nostrils
# ---- CRATER (FACE DETAIL + front view: lip y 287-292 x 171-300 (r 0.65), the wall narrowing to r ~0.56 where it meets
#   the head at y ~330; side view centre x ~1045-1054 -> Y -0.15)
CRATER_XY = (0.0, -0.15)
CRATER_Z, CRATER_R, CRATER_K = (4.50, 5.21), (0.55, 0.66), 0.10   # "crater": bottom (buried) / lip height, radii, blend
CRATER_HOLLOW = (0.52, 5.04)          # the hollow on top: radius, floor height (the flame rises out of it)
LAVA_BAND_Z = 4.84                    # "crater lava": the crater wall above this is bright lava (+ the lip + the hollow)
DRIP_TOP = 4.80                       # the dark-lava zone under the bright band starts here (between the drips)
DRIP_N, DRIP_SEED = 12, 5.0           # "lava drips" round the crater (front FACE DETAIL: tongues to y ~337, z 4.71)
DRIP_DEPTH = (0.10, 0.24)             # drip tongue length below DRIP_TOP (range)
DRIP_HALF_DEG = (8.0, 13.0)           # drip tongue half-width (deg round the crater axis)
# ---- LEGS (front view: outer edge x 36-51 / 421-426, inner edge x 208-254 at y 700-790; back view 511-664 / 720-859;
#   side view x 961-1177 at y 700-780) -> columns centred X +-1.05, Y +0.03, radius (z, r) profile, depth x LEG_SY
LEG_X, LEG_Y, LEG_SY = 1.05, 0.03, 1.08
LEG_KNOTS = [(0.00, 0.76), (0.10, 0.83), (0.30, 0.88), (0.70, 0.90), (1.10, 0.86), (1.50, 0.74), (1.85, 0.56)]
LEG_K = 0.20                          # leg -> torso blend
# ---- TOES (front view: two tan claws per foot at the base: x 60-93 + 103-147 / 313-357 + 367-403, y 790-808)
TOE_AZ = (0.0, 36.0)                  # "toe claws": azimuth deg from the front toward each foot's outside
TOE_R = (0.19, 0.13, 0.12)            # claw half-size (across, out, up)
TOE_Z, TOE_IN, TOE_K = 0.07, 0.04, 0.04   # claw centre height, how far its centre sits inside the column, blend
# ---- TAIL (back view: y 700-768 x 660-718 -> +-0.29, tip z 0.40; side view: tip x 1247 y 750 -> Y +1.87, z 0.58)
TAIL_A, TAIL_B, TAIL_R, TAIL_K = (0.0, 0.85, 1.15), (0.0, 1.82, 0.50), (0.30, 0.03), 0.10
# ---- PLASTRON (front view: tan x 171-295 (half 0.62 about x 233) at y 520-640, top arc y 478 at the centre; narrowing
#   between the legs: +-0.21 at y 690, +-0.12 at y 750, V tip y 762; segment lines at y 572 / 632 / 683)
PL_TOP = (3.32, 0.42)                 # "plastron top arc": centre height, drop at the sides
PL_HW = [(0.46, 0.0), (0.62, 0.12), (1.15, 0.22), (1.40, 0.62), (4.5, 0.62)]   # (z, half-width)
PL_FRONT = 0.20                       # the plastron covers the torso in front of (centre y - this)
PL_LINES, PL_LINE_W = (2.36, 1.76, 1.25), 0.022   # "plastron segment lines": heights, half-width
SDF_STEP, SDF_BAND = 0.025, 0.28      # body grid (band > max smooth-union k 0.22 + 2 steps)
BODY_TRIS = 7000                      # "body mesh detail" (before the colour cuts)
BODY_MAX_EDGE = 0.075                 # refinement in the plastron + crater zones before the line / drip cuts
# ---- ARM (+X arm; the -X arm mirrors it). Front view: arm x 18-170 (X 0.63-2.12) at y 470-690, crack band y 580-590,
#   tan knuckles y 640-690; shoulder blocks x 80-165 y 395-445 and x 80-130 y 445-500. Side view: fist x 975-1130 (Y -0.85
#   ..+0.70) at y 487-690, upper arm x 1032-1130 under the shoulder block x 1032-1145 y 405-505, upper ring crack y ~465.
UPPER_A, UPPER_B, UPPER_R = (1.18, 0.10, 3.55), (1.30, 0.05, 2.95), 0.46    # "upper arm"
FIST_C, FIST_R, FIST_P = (1.38, -0.05, 2.22), (0.72, 0.78, 1.02), 2.7       # "fist": centre, half-size, boxiness
FINGER_C, FINGER_DX, FINGER_R, FINGER_K = (1.38, -0.56, 1.44), (0.0,), (0.58, 0.30, 0.25), 0.08   # the curled-finger roll
BLOCKS = [((1.02, 0.02, 3.88), (0.46, 0.60, 0.27), 25.0), ((1.48, 0.0, 3.36), (0.27, 0.56, 0.33), 12.0)]
                                      # "shoulder plates": (centre, half-size, tilt deg: the outer side lower)
BLOCK_CHAMFER, BLOCK_CORNER = 0.14, 0.26
UPPER_K = 0.12
CRACK_W = 0.032                       # "lava crack" half-width (sheet lines ~6-7 px)
CR_FIST_Z, CR_UPPER_Z = 2.24, 3.40    # fist ring (front y 584) / upper-arm ring (side y ~465)
CR_SIDE_Y, CR_SIDE_Z = 0.45, (1.45, 2.24)   # the outer fist crack running down from the ring (side view)
CR_BLOCK = 4.635                      # shoulder crack: X + z = this (front view (165,410) -> (100,475))
ARM_STEP, ARM_BAND = 0.022, 0.18      # band > UPPER_K 0.12 + 2 steps
ARM_TRIS = 2000                       # per arm
# ---- SHELL (back view: rim outer x 550-825 (X +-1.37), y 395-700 (z 4.13-1.08); the plates' seeds = the drawn plate
#   centres; side view: back x 1249 (Y +1.89) at y ~550, front edge under the shoulder ~x 1135-1150)
SHELL_C, SHELL_R = (0.0, 0.62, 2.60), (1.30, 1.26, 1.50)     # "shell dome" ellipsoid
SHELL_BASE_Y = 0.42                   # the dome is cut flat here (inside the torso)
SHELL_INSET = 0.16                    # "plate facets": each plate is a plane this far inside the dome at its seed
SHELL_SEEDS = {"C": (0.0, 2.76), "UL": (0.98, 3.05), "UR": (-0.98, 3.05), "LL": (0.90, 1.98), "LR": (-0.90, 1.98),
               "TL": (0.40, 3.62), "TR": (-0.40, 3.62), "BL": (0.40, 1.66), "BR": (-0.40, 1.66)}   # (X, z) back view
SHELL_GLOW = [("C", "UL"), ("C", "UR"), ("C", "LL"), ("C", "LR"), ("UL", "TL"), ("UR", "TR"), ("UL", "LL"),
              ("UR", "LR"), ("LL", "BL"), ("LR", "BR")]      # the seams the back view draws GLOWING (the rest are dark seams)
SHELL_CRACK_W = 0.034
SHELL_SEAM_TOL = 0.03                 # a seam's band runs where its two plates are within this of the top plane
RIM_Y, RIM_AB, RIM_T, RIM_K = 0.80, (1.27, 1.47), 0.11, 0.04   # "shell rim": plane, ring radii (x, z), tube, blend
SHELL_STEP, SHELL_BAND = 0.024, 0.10  # band > RIM_K 0.04 + 2 steps
SHELL_TRIS = 2400
# ---- FACE (FACE DETAIL panel + front view; centres relative to the head axis x 235)
EYE_X, EYE_Z = 0.39, 4.27             # eyes x 184-210 / 262-288, y 362-399
EYE_OUTLINE = [(-0.10, 0.14), (0.05, 0.175), (0.125, -0.04), (0.08, -0.16), (-0.09, -0.16), (-0.125, 0.0)]
                                      # "eye shape" for the +X eye (u outward, v up); the -X eye mirrors it
EYE_SOCKET, EYE_PROUD, SOCKET_PROUD = 1.22, 0.022, 0.011
BROW_C = (0.0, 4.54, [(-0.205, 0.18), (0.205, 0.18), (0.165, -0.18), (-0.165, -0.18)])   # centre block x 215-256 y 336-372
BROW_S = (0.51, 4.70, (0.145, 0.125), -21.0)  # side blocks x 165-199 / 269-300, y 322-354: (X, z, half-size, tilt deg)
BROW_PROUD, BROW_CHAMFER = 0.10, 0.035
NOSE_X, NOSE_Z, NOSE_R = 0.11, 3.935, 0.03    # two nostril dots x 225 / 247, y 414.5
MOUTH_X, MOUTH_Z = 0.27, (3.72, 0.06)         # smile x 210-265; z centre + rise at the ends (y 436 centre, 430 ends)
MOUTH_W = 0.014
# ---- FLAME (front view: tip y 132 (z 6.76), widest +-0.72 at y 250, tongue tips (165,225) / (305,197) / (207,185);
#   side view: the tip leans back to x ~1140; flicks at (197,175) (281,175) (157,220) + back view (633,175) (735,175))
FLAME_ROOT = (0.0, -0.15, 4.90)
FLAME_TONGUES = [("flame_outer", 14.0, 90.0, 1.86, 0.70, 0.50, 0.06, 0.3),
                 ("flame_outer", 30.0, 180.0, 0.98, 0.44, 0.90, 0.12, 1.4),
                 ("flame_outer", 26.0, 0.0, 1.22, 0.46, 0.90, -0.12, 2.2),
                 ("flame_outer", 15.0, 165.0, 1.40, 0.44, 0.80, 0.10, 3.1),
                 ("flame_outer", 28.0, 105.0, 1.08, 0.42, 0.90, 0.0, 4.0),
                 ("flame_inner", 7.0, 90.0, 1.12, 0.56, 1.0, 0.05, 0.7),
                 ("flame_inner", 22.0, 180.0, 0.70, 0.32, 1.0, 0.0, 1.9),
                 ("flame_inner", 22.0, 0.0, 0.76, 0.32, 1.0, 0.0, 2.8)]
                                      # "flame tongues": (layer, tilt deg from vertical, tilt azimuth deg (0 = +X, 90 = back,
                                      #   180 = -X), length, radius, flatness, curl, phase)
FLAME_WIDE_S = 0.22
FLAME_NS, FLAME_SEG = 10, 10
FLICKS = [(-0.34, -0.12, 6.24, 0.24, 0.055), (0.55, -0.08, 6.20, 0.26, 0.06), (-0.76, -0.02, 5.80, 0.22, 0.05),
          (0.80, 0.05, 5.74, 0.20, 0.05)]    # "flame flicks": (x, y, z of the base, length, radius)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
GLOW_CAP = 2.0                        # emissive ceiling: max channel / 255 x emission_scale x emission_strength
# ---- rig
TORSO_J = (1.70, 2.65)                # hips | spine | chest split heights
TORSO_JW = (0.25, 0.25)
LEG_J = (0.85, 0.25)                  # thigh | shin | foot split heights
LEG_JW = (0.15, 0.08)
ARM_J = (3.05, 2.58)                  # upper | fore | fist split heights
ARM_JW = (0.10, 0.12)
MEMBER_BLEND = 0.12                   # leg / tail / head membership blend (own-field half-width)
# ---- clips (PLACEHOLDER: the artist has not given the Volcano Turtle's movement or height). 24 fps; integer harmonics
#   per loop -> frame N+1 == frame 1. Hips never move in either clip: the stance feet stay exactly on the floor.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH = (0.022, 0.8)            # "breathing": chest rise (u) + spine pitch (deg), 2 cycles
IDLE_HEAD = 1.0                       # head nod deg (2 cycles, lagging)
FIST_FLEX = (5.0, 0.035, 2.0)         # "fist flex": fist curl deg, squeeze scale +-, forearm deg (1 cycle; R lags L)
TAIL_IDLE = 3.0                       # tail sway deg
FLAME_FLICK = {"idle": (0.08, (7, 11, 17), 0.0), "walk": (0.10, (4, 6, 9), 0.04)}   # "flame flicker": length +-,
                                      #   harmonics per loop, mean stretch; the width narrows by FLAME_WIDTH_K x stretch
FLAME_WIDTH_K = 0.5
FLAME_WOBBLE = 1.5                    # flame wobble deg
WALK_N = 48                           # "stomp loop": 2 s (one step per leg)
WALK_LIFT = 30.0                      # "stomp lift": thigh swing deg at the top of a step (shin folds 2x, foot stays level)
WALK_ROLL = 4.0                       # body roll over the stance leg (deg, spine)
WALK_BOB = 0.05                       # "stomp drop": the upper body sinks this far at each foot strike (u)
WALK_ARM = 7.0                        # arm swing deg (opposite the legs)
WALK_TAIL = 6.0                       # tail wag deg
WALK_FLAME_TRAIL = 6.0                # the flame leans back while walking (deg)

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
          "design/reference/volcano_turtle-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; floor z = 0 at the sheet floor line (y 808); X from the front + back "
                   "orthos, Y from the 3/4 side view", "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
TAU = 2.0 * math.pi


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
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(len(np.unique(roots[keep_v]))),
                           "dropped_verts": int((~keep_v).sum())}


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)


def sdf_island(name, build, tris_target, min_frac=0.02, floor=False):
    """HIGH = polygonise(build()) (the bake source); LOW = collapse decimation of the HIGH (deterministic); keep islands;
    gate the low against the high (the hull-fold hazard). floor: collapse decimation places merged vertices at their
    quadric optimum, which can sit a hair under a flat floor cut -- those are snapped back onto z = 0 (counted)."""
    t_ = time.time()
    grid = build()
    HV, HF = SD.polygonise(grid)
    HF = [list(map(int, f)) for f in HF]
    HV, HF, crumbs = keep_islands(HV, HF, min_frac)
    tmp = new_obj("dec_src", HV, HF)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, tris_target / len(HF))
    LV, LF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF, specks = keep_islands(LV, LF, 0.01)
    snap = None
    if floor:
        below = LV[:, 2] < 0.0
        snap = {"verts_snapped_to_floor": int(below.sum()), "worst_dip": round(float(-LV[:, 2].min()), 6) if below.any() else 0.0}
        LV[:, 2] = np.maximum(LV[:, 2], 0.0)
    bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
    dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
    bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
    _hs = np.arange(0, len(HV), max(1, len(HV) // 30000))
    dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
    bb_h = np.ptp(HV, 0); bb_l = np.ptp(LV, 0)
    rep_ = {"step": grid.step, "band": grid.band, "grid": grid.n.tolist(), "ops": grid.ops, "high_tris": len(HF),
            "crumbs": crumbs, "decimated_tris": tri_count_F(LF), "specks": specks,
            "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                            "max": round(float(dev.max()), 5)},
            "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
            "bbox_low_over_high": (bb_l / bb_h).round(5).tolist(), "floor_snap": snap, "seconds": round(time.time() - t_, 1)}
    assert np.all(bb_l / bb_h > 0.97), (name, rep_)           # the hull-fold hazard (BUILD_CONTRACT): gate it
    print("SDF", name, json.dumps(rep_))
    return HV, HF, LV, LF, rep_


def cut_island(V, F, field_fn, cuts, max_edge=None, face_pred=None, rounds=2):
    """ember_stump's iso-cutter on one island: every cut = (field, tau, gate) with gate = None or [(field, '>'/'<', v)]
    that both edge ends must satisfy. max_edge: first split edges longer than this on the faces face_pred(normal, centre)
    selects (a band narrower than a triangle never crosses an edge); the fields are then evaluated on the refined
    vertices (field_fn(V) -> {name: values}). Slivers dissolved + flipped, min face area gated.
    -> cut V, F, per-face field means, normals, log"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    refine = 0
    if max_edge:
        for _ in range(rounds):
            bm.normal_update()
            big = [f for f in bm.faces if max(e.calc_length() for e in f.edges) > max_edge
                   and (face_pred is None or face_pred(np.array(f.normal[:]), np.array(f.calc_center_median()[:])))]
            if not big:
                break
            eds = list({e for f in big for e in f.edges})
            refine += len(big)
            bmesh.ops.subdivide_edges(bm, edges=eds, cuts=1, use_grid_fill=True)
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    fields = field_fn(np.array([v.co[:] for v in bm.verts]))
    LAY = {k: bm.verts.layers.float.new(k) for k in fields}
    for v in bm.verts:
        for k, arr in fields.items():
            v[LAY[k]] = float(arr[v.index])

    def gate_ok(spec, a, b):
        if spec is None:
            return True
        for key, op, val in spec:
            for v in (a, b):
                x = v[LAY[key]]
                if (op == ">" and not x > val) or (op == "<" and not x < val):
                    return False
        return True

    def iso_cut(key, tau, spec):
        L = LAY[key]
        eps = 1e-5 * max(1.0, abs(tau))
        on = lambda v: abs(v[L] - tau) <= eps
        side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
        ok = lambda a, b: gate_ok(spec, a, b)
        for e in bm.edges:
            a, b = e.verts
            sa, sb = side(a), side(b)
            if sa * sb < 0 and ok(a, b):
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts_ = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
        for e in cuts_:
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
        return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts_), "face_connects": len(pairs)}

    log = [{"refined_faces": refine, "max_edge": max_edge}] + [iso_cut(k_, tau_, g_) for k_, tau_, g_ in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    n0 = len(bm.faces)
    bmesh.ops.dissolve_degenerate(bm, dist=DEGEN_DIST, edges=bm.edges[:])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    flips = collapses = 0
    for it in range(12):
        flat = [f for f in bm.faces if f.calc_area() <= DEGEN_DIST ** 2]
        if not flat:
            break
        if it < 6:
            eds = list({max(f.edges, key=lambda e: e.calc_length()) for f in flat})
            res = bmesh.ops.rotate_edges(bm, edges=eds, use_ccw=False)
            flips += len(res["edges"])
        else:                                                # flips stuck: merge each sliver's shortest edge
            f0 = flat[0]
            bmesh.ops.collapse(bm, edges=[min(f0.edges, key=lambda e: e.calc_length())], uvs=False)
            collapses += 1
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    min_area = min(f.calc_area() for f in bm.faces)
    log.append({"degenerate_dissolved_faces": n0 - len(bm.faces), "sliver_edge_flips": flips, "sliver_collapses": collapses,
                "min_face_area": float(min_area)})
    assert min_area > DEGEN_DIST ** 2, log[-1]
    bm.verts.index_update(); bm.faces.index_update(); bm.normal_update()
    CV = np.array([v.co[:] for v in bm.verts])
    CF = [[v.index for v in f.verts] for f in bm.faces]
    FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
    FN = np.array([f.normal[:] for f in bm.faces])
    bm.free()
    return CV, CF, FVAL, FN, log


def own_fields(D):
    """D: {group: distances} -> {group: min(others) - own} (> 0 where the group owns the surface)"""
    keys = list(D)
    M = np.stack([D[k] for k in keys], 1)
    return {k: np.delete(M, j, axis=1).min(1) - M[:, j] for j, k in enumerate(keys)}


# =========================================================================== 1. BODY SDF
t_sdf = time.time()
_tk = np.array(TORSO_KNOTS, float)
_td = SD.catmull(_tk, 16)
_td = _td[np.argsort(_td[:, 0], kind="stable")]
TZ = _td[:, 0]


def torso_at(z):
    z = np.clip(z, TZ[0], TZ[-1])
    return [np.interp(z, TZ, _td[:, j]) for j in (1, 2, 3)]       # cy, rx, ry


def sd_torso(P):
    z = P[:, 2]
    cy, rx, ry = torso_at(z)
    q = np.hypot(P[:, 0] / rx, (P[:, 1] - cy) / ry)
    d = (q - 1.0) * np.minimum(rx, ry)
    return np.maximum(d, np.maximum(TZ[0] - z, z - TZ[-1]))


def sd_head(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R)


def sd_snout(P):
    return SD.sd_ellipsoid(P, SNOUT_C, SNOUT_R)


def sd_crater(P):
    return FP.sd_capped_cone(P, CRATER_XY[0], CRATER_XY[1], CRATER_Z[0], CRATER_Z[1], CRATER_R[0], CRATER_R[1])


def sd_hollow(P):
    return np.maximum(np.hypot(P[:, 0] - CRATER_XY[0], P[:, 1] - CRATER_XY[1]) - CRATER_HOLLOW[0], CRATER_HOLLOW[1] - P[:, 2])


_lk = np.array(LEG_KNOTS, float)
_ld = SD.catmull(_lk, 16)
_ld = _ld[np.argsort(_ld[:, 0], kind="stable")]
LEG_TOP = float(_ld[-1, 0])


def sd_leg(P, sg):
    z = P[:, 2]
    r = np.interp(np.clip(z, _ld[0, 0], _ld[-1, 0]), _ld[:, 0], _ld[:, 1])
    d = np.hypot(P[:, 0] - sg * LEG_X, (P[:, 1] - LEG_Y) / LEG_SY) - r
    return np.maximum(d, z - LEG_TOP)


def leg_r(z):
    return float(np.interp(z, _ld[:, 0], _ld[:, 1]))


TOES = []                                  # (side sign, centre, frame)
for sg in (1.0, -1.0):
    for az in TOE_AZ:
        a_ = math.radians(az)
        out = np.array([sg * math.sin(a_), -math.cos(a_) * LEG_SY, 0.0])
        rad = leg_r(TOE_Z) * float(np.linalg.norm(out))
        dirn = unit(out)
        c_ = np.array([sg * LEG_X, LEG_Y, TOE_Z]) + dirn * (rad - TOE_IN)
        side = unit(np.cross([0.0, 0.0, 1.0], dirn))
        TOES.append((sg, c_, np.array([side, dirn, [0.0, 0.0, 1.0]])))


def sd_toes(P, sg=None):
    d = np.full(len(P), 1e9)
    for s_, c_, fr in TOES:
        if sg is None or s_ == sg:
            d = np.minimum(d, SD.sd_ellipsoid(P, c_, TOE_R, frame=fr))
    return d


def sd_tail(P):
    return SD.sd_round_cone(P, TAIL_A, TAIL_B, TAIL_R[0], TAIL_R[1])


BODY_OPS = [("torso", sd_torso, 0.0, "union"), ("head", sd_head, HEAD_K, "union"), ("snout", sd_snout, SNOUT_K, "union"),
            ("crater", sd_crater, CRATER_K, "union"), ("leg_L", lambda P: sd_leg(P, 1.0), LEG_K, "union"),
            ("leg_R", lambda P: sd_leg(P, -1.0), LEG_K, "union"), ("toe", sd_toes, TOE_K, "union"),
            ("tail", sd_tail, TAIL_K, "union"), ("hollow", sd_hollow, 0.03, "subtract")]


def sd_body(P):
    """the analytic twin of the body grid (same op order, no band clipping): surface queries for the decals"""
    d = None
    for nm, fn, k, mode in BODY_OPS:
        x = fn(P)
        if d is None:
            d = x
        elif mode == "union":
            d = SD.smin(d, x, k)
        else:
            d = SD.smax(d, -x, k)
    return np.maximum(d, -P[:, 2])


_cr = CRATER_R[1]
BODY_BOXES = {"torso": ((-1.5, -1.25, TZ[0]), (1.5, 1.05, TZ[-1])),
              "head": (np.array(HEAD_C) - HEAD_R, np.array(HEAD_C) + HEAD_R),
              "snout": (np.array(SNOUT_C) - SNOUT_R, np.array(SNOUT_C) + SNOUT_R),
              "crater": ((CRATER_XY[0] - _cr, CRATER_XY[1] - _cr, CRATER_Z[0]), (CRATER_XY[0] + _cr, CRATER_XY[1] + _cr, CRATER_Z[1])),
              "leg_L": ((LEG_X - 0.95, LEG_Y - 1.05, -0.05), (LEG_X + 0.95, LEG_Y + 1.05, LEG_TOP)),
              "leg_R": ((-LEG_X - 0.95, LEG_Y - 1.05, -0.05), (-LEG_X + 0.95, LEG_Y + 1.05, LEG_TOP)),
              "toe": (np.array([c_ for _, c_, _ in TOES]).min(0) - 0.22, np.array([c_ for _, c_, _ in TOES]).max(0) + 0.22),
              "tail": (np.minimum(TAIL_A, TAIL_B) - TAIL_R[0], np.maximum(TAIL_A, TAIL_B) + TAIL_R[0]),
              "hollow": ((CRATER_XY[0] - 0.6, CRATER_XY[1] - 0.6, CRATER_HOLLOW[1] - 0.05), (CRATER_XY[0] + 0.6, CRATER_XY[1] + 0.6, CRATER_Z[1] + 0.1))}


def build_body():
    lo = np.array([-2.05, -1.30, -0.04]); hi = np.array([2.05, 2.0, CRATER_Z[1] + 0.06])
    g = SD.Grid(lo, hi, SDF_STEP, SDF_BAND)
    for nm, fn, k, mode in BODY_OPS:
        b0, b1 = BODY_BOXES[nm]
        b0 = np.asarray(b0, float); b1 = np.asarray(b1, float)
        if nm == "torso":                             # z chunks (memory); overlapping margins min-union idempotently
            for z0 in np.arange(b0[2], b1[2], 1.0):
                g.apply(fn, np.array([b0[0], b0[1], z0]), np.array([b1[0], b1[1], min(z0 + 1.0, b1[2])]), k)
        else:
            g.apply(fn, b0, b1, k, mode=mode)
    g.floor(0.0)
    return g


HV_B, HF_B, LV_B, LF_B, rep_b = sdf_island("body", build_body, BODY_TRIS, floor=True)
report["sdf"] = {"body": rep_b}
assert HV_B[:, 2].min() > -1e-6, "body high dips below the floor"
assert rep_b["crumbs"]["kept"] == 1 and rep_b["specks"]["kept"] == 1, "the body must be one closed island"

# body region fields
_dr_az = []
for i in range(DRIP_N):
    _dr_az.append((360.0 * (i + 0.5 * FP.hash01(i, DRIP_SEED)) / DRIP_N,
                   DRIP_DEPTH[0] + (DRIP_DEPTH[1] - DRIP_DEPTH[0]) * FP.hash01(i, DRIP_SEED + 1.7),
                   DRIP_HALF_DEG[0] + (DRIP_HALF_DEG[1] - DRIP_HALF_DEG[0]) * FP.hash01(i, DRIP_SEED + 3.1)))


def drip_level(P):
    th = np.degrees(np.arctan2(P[:, 0] - CRATER_XY[0], -(P[:, 1] - CRATER_XY[1])))     # 0 = front, + toward +X
    dep = np.zeros(len(P))
    for az, D_, W_ in _dr_az:
        dth = np.abs((th - az + 180.0) % 360.0 - 180.0)
        dep = np.maximum(dep, D_ * np.clip(1.0 - dth / W_, 0.0, 1.0) ** 0.8)
    return DRIP_TOP - dep


_ph = np.array(PL_HW, float)


def plastron_field(P):
    X, Y, z = P[:, 0], P[:, 1], P[:, 2]
    cy, rx, ry = torso_at(z)
    hw = np.interp(z, _ph[:, 0], _ph[:, 1])
    top = PL_TOP[0] - PL_TOP[1] * (X / _ph[-1, 1]) ** 2
    return np.minimum(np.minimum(hw - np.abs(X), top - z), (cy - PL_FRONT) - Y)


_tdir = unit(np.subtract(TAIL_B, TAIL_A))
TAIL_N = unit(np.cross(_tdir, [1.0, 0.0, 0.0]))
if TAIL_N[2] < 0:
    TAIL_N = -TAIL_N


def body_groups(P):
    return {"torso": sd_torso(P), "head": np.minimum(sd_head(P), sd_snout(P)), "crater": sd_crater(P),
            "leg_L": np.minimum(sd_leg(P, 1.0), sd_toes(P, 1.0)), "leg_R": np.minimum(sd_leg(P, -1.0), sd_toes(P, -1.0)),
            "tail": sd_tail(P)}


def body_fields(P):
    D = body_groups(P)
    own = own_fields(D)
    toe_d = sd_toes(P)
    others = np.minimum.reduce([D["torso"], D["head"], D["crater"], sd_leg(P, 1.0), sd_leg(P, -1.0), D["tail"]])
    return {"own_torso": own["torso"], "own_tail": own["tail"], "own_toe": others - toe_d, "plastron": plastron_field(P),
            "drip": P[:, 2] - drip_level(P), "band": P[:, 2] - LAVA_BAND_Z,
            "tail_side": (P - np.asarray(TAIL_A)) @ TAIL_N, "z": P[:, 2].copy()}


def body_refine_pred(n_, c_):
    """only the crater / drip zone is refined (the drip tongues are narrower than the shell's triangles); every other cut
    is on a (piecewise) linear field, which an iso-cut follows exactly at any triangle size"""
    r_ = math.hypot(c_[0] - CRATER_XY[0], c_[1] - CRATER_XY[1])
    return DRIP_TOP - DRIP_DEPTH[1] - 0.06 < c_[2] < LAVA_BAND_Z + 0.04 and 0.40 < r_ < 0.85


TOL = 0.01
t = time.time()
CV_B, CF_B, FV_B, FN_B, log_b = cut_island(
    LV_B, LF_B, body_fields,
    [("own_toe", 0.0, None), ("own_tail", 0.0, None), ("tail_side", 0.0, [("own_tail", ">", -TOL)]),
     ("plastron", 0.0, [("own_torso", ">", -TOL)])]
    + [("z", zl + sg * PL_LINE_W, [("plastron", ">", -TOL)]) for zl in PL_LINES for sg in (-1.0, 1.0)]
    + [("band", 0.0, None), ("drip", 0.0, [("band", "<", TOL)])],
    max_edge=BODY_MAX_EDGE, face_pred=body_refine_pred, rounds=1)
reg_b = np.full(len(CF_B), "skin", dtype=object)
reg_b[FV_B["own_toe"] > 0] = "toe"
reg_b[(FV_B["own_tail"] > 0) & (FV_B["tail_side"] < 0)] = "tail_under"
_pl = (FV_B["own_torso"] > -TOL) & (FV_B["plastron"] > 0)
reg_b[_pl] = "plastron"
reg_b[_pl & (np.min(np.abs(FV_B["z"][:, None] - np.array(PL_LINES)[None]), 1) < PL_LINE_W)] = "plastron_line"
reg_b[(FV_B["drip"] > 0) & (FV_B["band"] < 0)] = "lava_drip"
reg_b[FV_B["band"] > 0] = "crater"
report["iso_cuts"] = {"body": {"cuts": log_b, "tris_after": len(CF_B), "seconds": round(time.time() - t, 1)}}

# =========================================================================== 2. ARM SDF (+X arm; -X = mirror)
_BLK = []
for c_, h_, tilt in BLOCKS:
    p_ = math.radians(tilt)
    fr = np.array([[math.cos(p_), 0.0, -math.sin(p_)], [0.0, 1.0, 0.0], [math.sin(p_), 0.0, math.cos(p_)]])
    _BLK.append((np.array(c_, float), fr, np.array(h_, float)))


def sd_upper(P):
    return SD.sd_round_cone(P, UPPER_A, UPPER_B, UPPER_R, UPPER_R)


def sd_fist(P):
    return FP.sd_superellipsoid(P, FIST_C, FIST_R, FIST_P)


def sd_fingers(P):
    d = np.full(len(P), 1e9)
    for dx in FINGER_DX:
        d = np.minimum(d, SD.sd_ellipsoid(P, np.add(FINGER_C, (dx, 0.0, 0.0)), FINGER_R))
    return d


def sd_block(P, i):
    c_, fr, h_ = _BLK[i]
    return FP.sd_chamfer_box(P, c_, fr, h_, BLOCK_CHAMFER, BLOCK_CORNER)


ARM_OPS = [("fist", sd_fist, 0.0), ("upper", sd_upper, UPPER_K), ("finger", sd_fingers, FINGER_K),
           ("blockA", lambda P: sd_block(P, 0), 0.0), ("blockB", lambda P: sd_block(P, 1), 0.0)]
_alo = np.array([0.45, -1.05, 1.08]); _ahi = np.array([2.20, 0.95, 4.25])


def build_arm():
    g = SD.Grid(_alo, _ahi, ARM_STEP, ARM_BAND)
    for nm, fn, k in ARM_OPS:
        g.apply(fn, _alo, _ahi, k)
    return g


HV_A, HF_A, LV_A, LF_A, rep_a = sdf_island("arm", build_arm, ARM_TRIS)
report["sdf"]["arm"] = rep_a
assert rep_a["crumbs"]["kept"] == 1, "the arm must be one closed island"


def arm_groups(P):
    return {nm: fn(P) for nm, fn, k in ARM_OPS}


def arm_fields(P):
    """linear crack fields (an iso-cut follows a linear field exactly) + the ownership windows that gate them"""
    own = own_fields(arm_groups(P))
    return {"own_finger": own["finger"], "w_fist": own["fist"] + 0.03, "w_upper": own["upper"], "w_block": own["blockA"],
            "x": P[:, 0].copy(), "y": P[:, 1].copy(), "z": P[:, 2].copy(),
            "blk": (P[:, 0] + P[:, 2] - CR_BLOCK) / math.sqrt(2.0)}


ARM_CRACKS = [("z", CR_FIST_Z, [("w_fist", ">", 0.0)]), ("z", CR_UPPER_Z, [("w_upper", ">", 0.0)]),
              ("y", CR_SIDE_Y, [("w_fist", ">", 0.0), ("z", ">", CR_SIDE_Z[0]), ("z", "<", CR_SIDE_Z[1] + CRACK_W),
                                ("x", ">", FIST_C[0])]),
              ("blk", 0.0, [("w_block", ">", 0.0)])]


def crack_faces(FV):
    hit = np.zeros(len(FV["z"]), bool)
    for key, c0, gate in ARM_CRACKS:
        m = np.abs(FV[key] - c0) < CRACK_W
        for gk, op, gv in gate:
            m &= (FV[gk] > gv) if op == ">" else (FV[gk] < gv)
        hit |= m
    return hit


t = time.time()
CV_A, CF_A, FV_A, FN_A, log_a = cut_island(LV_A, LF_A, arm_fields,
                                           [("own_finger", 0.0, None)]
                                           + [(k_, c0 + sg * CRACK_W, g_ + [("own_finger", "<", TOL)])
                                              for k_, c0, g_ in ARM_CRACKS for sg in (-1.0, 1.0)])
reg_a = np.full(len(CF_A), "arm_rock", dtype=object)
reg_a[FV_A["own_finger"] > 0] = "knuckle"
reg_a[(FV_A["own_finger"] < 0) & crack_faces(FV_A)] = "crack"
report["iso_cuts"]["arm"] = {"cuts": log_a, "tris_after_per_arm": len(CF_A), "seconds": round(time.time() - t, 1)}
_MIR = np.array([-1.0, 1.0, 1.0])

# =========================================================================== 3. SHELL SDF
SC = np.array(SHELL_C, float); SR = np.array(SHELL_R, float)
SEED_NAMES = list(SHELL_SEEDS)
_SP, _SN = [], []
for nm in SEED_NAMES:
    X_, z_ = SHELL_SEEDS[nm]
    w_ = 1.0 - (X_ / SR[0]) ** 2 - ((z_ - SC[2]) / SR[2]) ** 2
    assert w_ > 0.05, ("shell seed off the dome", nm)
    p_ = np.array([X_, SC[1] + SR[1] * math.sqrt(w_), z_])
    n_ = unit((p_ - SC) / SR ** 2)
    _SP.append(p_ - n_ * SHELL_INSET); _SN.append(n_)
_SP = np.array(_SP); _SN = np.array(_SN)
_GLOW_IJ = [(SEED_NAMES.index(a), SEED_NAMES.index(b)) for a, b in SHELL_GLOW]


def shell_planes(P):
    return P @ _SN.T - np.sum(_SP * _SN, 1)[None]


def sd_plates(P):
    D = shell_planes(P)
    d = np.maximum(D.max(1), SD.sd_ellipsoid(P, SC, SR))
    return np.maximum(d, SHELL_BASE_Y - P[:, 1])


def sd_rim(P):
    e = (np.hypot(P[:, 0] / RIM_AB[0], (P[:, 2] - SC[2]) / RIM_AB[1]) - 1.0) * min(RIM_AB)
    return np.hypot(e, P[:, 1] - RIM_Y) - RIM_T


_slo = np.array([-1.48, SHELL_BASE_Y - 0.04, 0.95]); _shi = np.array([1.48, SC[1] + SR[1] + 0.05, 4.28])


def build_shell():
    g = SD.Grid(_slo, _shi, SHELL_STEP, SHELL_BAND)
    g.apply(sd_plates, _slo, _shi, 0.0)
    g.apply(sd_rim, _slo, _shi, RIM_K)
    return g


HV_S, HF_S, LV_S, LF_S, rep_s = sdf_island("shell", build_shell, SHELL_TRIS)
report["sdf"]["shell"] = rep_s
assert rep_s["crumbs"]["kept"] == 1, "the shell must be one closed island"


def shell_fields(P):
    """per glowing seam (i, j): h = the signed distance to the bisector plane of plates i and j (LINEAR, so an iso-cut
    follows it exactly at any triangle size: flat plates decimate to big triangles whose corners all sit on seams) and
    t = how far plate i or j is from being the top plane (0 on both plates, the gate)"""
    D = shell_planes(P)
    d1 = D.max(1)
    out = {"own_rim": sd_plates(P) - sd_rim(P)}
    for k, (i, j) in enumerate(_GLOW_IJ):
        out["h%d" % k] = (D[:, i] - D[:, j]) / float(np.linalg.norm(_SN[i] - _SN[j]))
        out["t%d" % k] = d1 - np.maximum(D[:, i], D[:, j])
    return out


t = time.time()
CV_S, CF_S, FV_S, FN_S, log_s = cut_island(LV_S, LF_S, shell_fields,
                                           [("own_rim", 0.0, None)]
                                           + [("h%d" % k, sg * SHELL_CRACK_W, [("t%d" % k, "<", SHELL_SEAM_TOL), ("own_rim", "<", TOL)])
                                              for k in range(len(_GLOW_IJ)) for sg in (-1.0, 1.0)])
reg_s = np.full(len(CF_S), "shell_plate", dtype=object)
_sc = np.zeros(len(CF_S), bool)
for k in range(len(_GLOW_IJ)):
    _sc |= (np.abs(FV_S["h%d" % k]) < SHELL_CRACK_W) & (FV_S["t%d" % k] < SHELL_SEAM_TOL)
reg_s[(FV_S["own_rim"] < 0) & _sc] = "crack"
reg_s[(FV_S["own_rim"] > 0) | (FN_S[:, 1] < -0.5)] = "shell_rim"    # the rim ring + the flat underside the side view shows tan
report["iso_cuts"]["shell"] = {"cuts": log_s, "tris_after": len(CF_S), "seconds": round(time.time() - t, 1)}
report["sdf"]["seconds"] = round(time.time() - t_sdf, 1)

# =========================================================================== 4. closed parts
PARTS = []
PARTS.append(FP.Part("body", CV_B, CF_B, list(reg_b)))
PARTS[-1].body = True
for side, mir in (("L", False), ("R", True)):
    V_ = CV_A * _MIR if mir else CV_A
    F_ = [f[::-1] for f in CF_A] if mir else CF_A
    p = FP.Part("arm." + side, V_, F_, list(reg_a))
    p.arm = side
    PARTS.append(p)
PARTS.append(FP.Part("shell", CV_S, CF_S, list(reg_s)))
PARTS[-1].shell = True


def surf_hit(x, z, y0=-3.0, dy=1.0):
    """the first body-surface crossing along +Y at (x, z) -> (point, outward normal)"""
    ts = np.arange(0.0, 4.0, 0.005)
    P = np.stack([np.full_like(ts, x), y0 + dy * ts, np.full_like(ts, z)], 1)
    d = sd_body(P)
    k = int(np.nonzero(d < 0)[0][0])
    a, b = ts[k - 1], ts[k]
    for _ in range(40):
        m = 0.5 * (a + b)
        if sd_body(np.array([[x, y0 + dy * m, z]]))[0] < 0:
            b = m
        else:
            a = m
    p = np.array([x, y0 + dy * 0.5 * (a + b), z])
    e = 1e-4
    gr = np.array([sd_body(np.array([p + e * ax]))[0] - sd_body(np.array([p - e * ax]))[0] for ax in np.eye(3)])
    return p, unit(gr)


def face_frame(n_):
    e_u = unit(np.array([1.0, 0.0, 0.0]) - n_ * n_[0])
    e_v = unit(np.cross(n_, e_u))
    if e_v[2] < 0:
        e_v = -e_v
    return e_u, e_v


FACE_INFO = {}
for side, sg in (("L", 1.0), ("R", -1.0)):            # L = +X (image right in the front view)
    ol = np.array(EYE_OUTLINE) * np.array([sg, 1.0])
    if sg < 0:
        ol = ol[::-1]
    org, n_ = surf_hit(sg * EYE_X, EYE_Z)
    e_u, e_v = face_frame(n_)
    c2 = ol.mean(0)
    V, F, R = FP.plate_frame(c2 + (ol - c2) * EYE_SOCKET, org, e_u, e_v, n_, SOCKET_PROUD, 0.06, 0.004, "eye_socket")
    PARTS.append(FP.Part("eye_socket." + side, V, F, R, bone="head"))
    V, F, R = FP.plate_frame(ol, org, e_u, e_v, n_, EYE_PROUD, 0.05, 0.006, "eye")
    PARTS.append(FP.Part("eye." + side, V, F, R, bone="head"))
    FACE_INFO["eye." + side] = {"centre": org.round(4).tolist(), "normal": n_.round(3).tolist()}
# rocky brow: the centre trapezoid + two tilted side blocks
org, n_ = surf_hit(BROW_C[0], BROW_C[1])
e_u, e_v = face_frame(n_)
V, F, R = FP.plate_frame(np.array(BROW_C[2]), org, e_u, e_v, n_, BROW_PROUD, 0.10, BROW_CHAMFER, "brow")
PARTS.append(FP.Part("brow.C", V, F, R, bone="head"))
FACE_INFO["brow.C"] = {"centre": org.round(4).tolist()}
for side, sg in (("L", 1.0), ("R", -1.0)):
    hx, hz = BROW_S[2]
    a_ = math.radians(BROW_S[3]) * sg
    sq = np.array([(-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz)])
    sq = sq @ np.array([[math.cos(a_), math.sin(a_)], [-math.sin(a_), math.cos(a_)]])
    org, n_ = surf_hit(sg * BROW_S[0], BROW_S[1])
    e_u, e_v = face_frame(n_)
    V, F, R = FP.plate_frame(sq, org, e_u, e_v, n_, BROW_PROUD, 0.10, BROW_CHAMFER, "brow")
    PARTS.append(FP.Part("brow." + side, V, F, R, bone="head"))
    FACE_INFO["brow." + side] = {"centre": org.round(4).tolist()}
for side, sg in (("L", 1.0), ("R", -1.0)):
    org, n_ = surf_hit(sg * NOSE_X, NOSE_Z)
    e_u, e_v = face_frame(n_)
    V, F, R = FP.plate_frame(FP.circle(NOSE_R, 8), org, e_u, e_v, n_, 0.012, 0.04, 0.006, "face_line")
    PARTS.append(FP.Part("nostril." + side, V, F, R, bone="head"))
_mp, _mn = [], []
for x_ in np.linspace(-MOUTH_X, MOUTH_X, 15):
    p_, n_ = surf_hit(x_, MOUTH_Z[0] + MOUTH_Z[1] * (x_ / MOUTH_X) ** 2)
    _mp.append(p_); _mn.append(n_)
V, F, R = FP.ribbon(_mp, _mn, MOUTH_W, 0.008, 0.04, "face_line")
PARTS.append(FP.Part("mouth", V, F, R, bone="head"))
FACE_INFO["mouth"] = {"ends": [np.round(_mp[0], 4).tolist(), np.round(_mp[-1], 4).tolist()]}

# flame tongues + flicks (opaque emissive, own material)
FL_ROOT = np.array(FLAME_ROOT, float)
FLAME_INFO = []
for i, (reg, tilt, taz, L_, r_, flat, wave, ph) in enumerate(FLAME_TONGUES):
    t_r, a_r = math.radians(tilt), math.radians(taz)
    d = np.array([math.sin(t_r) * math.cos(a_r), math.sin(t_r) * math.sin(a_r), math.cos(t_r)])
    V, F, R, sp = FP.tube_tongue(FL_ROOT, d, (1.0, 0.0, 0.0), L_, r_, FLAME_NS, FLAME_SEG, wave, ph, reg, flat=flat,
                                 wide_s=FLAME_WIDE_S, lobes=3, lobe_a=0.10)
    p = FP.Part("flame.%d" % i, V, F, R, bone="flame")
    p.flame = True
    PARTS.append(p)
    FLAME_INFO.append({"layer": reg, "tip": sp[-1].round(3).tolist(), "length": L_, "radius": r_})
for i, (x_, y_, z_, L_, r_) in enumerate(FLICKS):
    d = unit([0.25 * np.sign(x_), 0.05, 1.0])
    V, F, R, sp = FP.tube_tongue((x_, y_, z_), d, (1.0, 0.0, 0.0), L_, r_, 6, 8, 0.0, 0.0, "flame_outer", wide_s=0.35)
    p = FP.Part("flick.%d" % i, V, F, R, bone="flame")
    p.flame = True
    PARTS.append(p)
    FLAME_INFO.append({"layer": "flick", "tip": sp[-1].round(3).tolist(), "length": L_, "radius": r_})

# =========================================================================== 5. assemble + centre
ISL = [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
assert lo0[2] > -1e-4, "something dips below the floor: min z %.5f" % lo0[2]
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, MATI, RANGE = [], [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    MATI += [1 if getattr(p, "flame", False) else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
assert abs(VALL[:, 2].min()) < 1e-9 and VALL[RANGE["body"][0]:RANGE["body"][1], 2].min() < 1e-9, "the soles must sit on the floor"
REG = ["skin", "plastron", "plastron_line", "toe", "tail_under", "arm_rock", "knuckle", "shell_plate", "shell_rim", "brow",
       "eye_socket", "face_line", "eye", "crack", "lava_drip", "crater", "flame_outer", "flame_inner"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
FLAME_REG = {"flame_outer", "flame_inner"}
assert all((RNAME[i] in FLAME_REG) == (MATI[i] == 1) for i in range(len(RNAME))), "flame region off the flame material"


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.96 + 0.08 * j


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                      # opaque closed solids: single-sided (glTF doubleSided false)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


GLOW_REGIONS = {"eye", "crack", "lava_drip", "crater", "flame_outer", "flame_inner"}
GLOW_ORDER = ["lava_drip", "crack", "eye", "crater", "flame_outer", "flame_inner"]


def glow_check(pal):
    """the sheet draws glow on the eyes, the lava cracks, the crater rim (+ its drips) and the flame only; tiers
    lava_drip < crack < eye < crater < flame_outer < flame_inner; peak under the cap"""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    peak = {n: max(pal["regions"][n]["emission"]) / 255.0 * tier[n] * pal["material"].get("emission_strength", 1.0)
            for n in tier}
    ok_set = set(tier) == GLOW_REGIONS
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "peak_x_strength": {k: round(v, 4) for k, v in peak.items()},
            "cap": GLOW_CAP, "only_drawn_glow": ok_set,
            "grade_pass": ok_set and all(tier[a] < tier[b] for a, b in zip(GLOW_ORDER[:-1], GLOW_ORDER[1:])),
            "cap_pass": all(v <= GLOW_CAP for v in peak.values())}


MAT_BODY = make_mat(UNIT + "_body")
MAT_FLAME = make_mat(UNIT + "_flame")
pal_default = PAL.load(UNIT, "default")
report["glow"] = {"default": glow_check(pal_default)}
_gc = report["glow"]["default"]
assert _gc["only_drawn_glow"] and _gc["grade_pass"] and _gc["cap_pass"], _gc

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_FLAME)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
shade = jitter(VALL, FALL)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the face surface between the eyes (straight -Y)
anchor = np.array([0.0, HEAD_C[1], EYE_Z]) - SHIFT
landmark = surf_hit(0.0, EYE_Z)[0] - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre at eye height -> the face surface between the eyes (the face looks -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

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
_pp = {}
for p in PARTS:
    key = p.name.split(".")[0]
    _pp[key] = _pp.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "per_part_family": _pp,
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The SDF body (plastron / drip refinement + cuts) "
                            "carries the largest share; the two arm islands, the faceted shell, 8 flame tongues + 4 flicks "
                            "and the face decals the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]), "parts": len(open_e),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every part is closed: SDF islands (body, 2 arms, shell), plates, ribbon, flame tongues"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_height_units": {"front": 6.77, "back": 6.85, "side": 6.89},
                     "sheet_width_units": {"front": 4.25, "back": 4.27, "side_3q": 3.16},
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
low["conquest_source"] = "from scratch: design/reference/volcano_turtle-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = "none: every material opaque (the flame is an opaque emissive mesh on its own material)"
_face = np.array([[-1.05, -1.45, 3.45], [1.05, 0.3, 5.35]])
_fl = np.vstack([p.V for p in PARTS if getattr(p, "flame", False)] + [np.array([[-0.7, -0.8, 4.6], [0.7, 0.5, 4.6]])])
_armL = PARTS[[p.name for p in PARTS].index("arm.L")].V
_sh = np.vstack([CV_S, np.array([TAIL_B])])
low["conquest_focus"] = json.dumps({
    "face": [(_face[0] - SHIFT).tolist(), (_face[1] - SHIFT).tolist()],
    "flame": [(_fl.min(0) - SHIFT - 0.05).tolist(), (_fl.max(0) - SHIFT + 0.05).tolist()],
    "arm": [(_armL.min(0) - SHIFT - 0.05).tolist(), (_armL.max(0) - SHIFT + 0.05).tolist()],
    "shell": [(_sh.min(0) - SHIFT - 0.05).tolist(), (_sh.max(0) - SHIFT + 0.05).tolist()],
    "views": {"face": [0.0, 6.0], "flame": [0.0, 10.0], "arm": [60.0, 4.0], "shell": [180.0, 8.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "iso_cuts", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow")}, default=str))
    print("FACE", json.dumps(FACE_INFO))
    print("FLAME", json.dumps(FLAME_INFO))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF highs + closed parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [], []
o_ = 0


def add_high(Vh, Fh):
    global o_
    PH_V.append(Vh - SHIFT); PH_F.extend([[i + o_ for i in f] for f in Fh]); o_ += len(Vh)


add_high(HV_B, HF_B)
add_high(HV_A, HF_A)
add_high(HV_A * _MIR, [f[::-1] for f in HF_A])
add_high(HV_S, HF_S)
for p in PARTS:
    if getattr(p, "body", False) or getattr(p, "arm", None) or getattr(p, "shell", False) or getattr(p, "flame", False):
        continue                                     # SDF lows are replaced by their highs; the flame never occludes
    add_high(p.V, p.F)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
BAKE_CAGE = max(0.02, round(3.0 * max(r_["high_to_low_sampled"]["p99"] for k_, r_ in report["sdf"].items()
                                      if isinstance(r_, dict)), 4))
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
BAKE_NODES = {}
for mat_ in (MAT_BODY, MAT_FLAME):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
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
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, 16)):
    for mat_ in (MAT_BODY, MAT_FLAME):
        mat_.node_tree.nodes.active = BAKE_NODES[mat_.name][idx]
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
body_faces = np.array(MATI) == 0
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the worst SDF island's sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "body + 2 arm + shell SDF highs + every closed part except the flame tongues / flicks",
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
nt = MAT_BODY.node_tree
tn, ta = BAKE_NODES[MAT_BODY.name]
for n_ in BAKE_NODES[MAT_FLAME.name]:
    MAT_FLAME.node_tree.nodes.remove(n_)
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
                     "provenance": "pixel-sampled from the sheet's six palette chips (intake swatches) + drawn-surface "
                                   "samples (see the palette notes)"}
report["flame"] = {"root": (FL_ROOT - SHIFT).round(4).tolist(), "tongues": FLAME_INFO}
report["face"] = FACE_INFO
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


def tc(z, dy=0.0):
    cy, _, _ = torso_at(np.array([z]))
    return np.array([0.0, float(cy[0]) + dy, z])


ARM_PTS = (np.array(UPPER_A), np.array(UPPER_B), np.array([FIST_C[0], FIST_C[1], ARM_J[1]]),
           np.array([FIST_C[0], FIST_C[1] - 0.10, FIST_C[2] - FIST_R[2] + 0.10]))
BONES = [("hips", P_(tc(1.00)), P_(tc(TORSO_J[0])), "root"),
         ("spine", P_(tc(TORSO_J[0])), P_(tc(TORSO_J[1])), "hips"),
         ("chest", P_(tc(TORSO_J[1])), P_(tc(3.75)), "spine"),
         ("head", P_(np.array([0.0, HEAD_C[1], 3.75])), P_(np.array([0.0, HEAD_C[1], 4.80])), "chest"),
         ("flame", P_(FL_ROOT), P_(FL_ROOT + np.array([0.0, 0.20, 1.75])), "head"),
         ("tail", P_(TAIL_A), P_(TAIL_B), "hips")]
for side, sg in (("L", 1.0), ("R", -1.0)):
    m_ = np.array([sg, 1.0, 1.0])
    a0, a1, a2, a3 = (p * m_ for p in ARM_PTS)
    BONES += [("arm_%s.upper" % side, P_(a0), P_(a1), "chest"), ("arm_%s.fore" % side, P_(a1), P_(a2), "arm_%s.upper" % side),
              ("arm_%s.fist" % side, P_(a2), P_(a3), "arm_%s.fore" % side)]
    hx = sg * LEG_X
    BONES += [("leg_%s.thigh" % side, P_((hx, LEG_Y, 1.50)), P_((hx, LEG_Y, LEG_J[0])), "hips"),
              ("leg_%s.shin" % side, P_((hx, LEG_Y, LEG_J[0])), P_((hx, LEG_Y, LEG_J[1])), "leg_%s.thigh" % side),
              ("leg_%s.foot" % side, P_((hx, LEG_Y, LEG_J[1])), P_((hx, LEG_Y - 0.80, LEG_J[1])), "leg_%s.shin" % side)]
CONNECT = {"spine", "chest", "arm_L.fore", "arm_R.fore", "arm_L.fist", "arm_R.fist", "leg_L.shin", "leg_R.shin",
           "leg_L.foot", "leg_R.foot"}
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


def torso_split(z):
    s1 = smoothstep(TORSO_J[0] - TORSO_JW[0], TORSO_J[0] + TORSO_JW[0], z)
    s2 = smoothstep(TORSO_J[1] - TORSO_JW[1], TORSO_J[1] + TORSO_JW[1], z)
    return {"hips": 1.0 - s1, "spine": s1 - s2, "chest": s2}


a_, b_ = RANGE["body"]
Vw = VALL[a_:b_] + SHIFT
zz = Vw[:, 2]
G = body_groups(Vw)
own = own_fields(G)
m_head = smoothstep(-MEMBER_BLEND, MEMBER_BLEND, np.minimum(G["torso"], np.minimum(G["leg_L"], G["leg_R"]))
                    - np.minimum(G["head"], G["crater"]))
m_tail = smoothstep(-MEMBER_BLEND, MEMBER_BLEND, own["tail"])
m_leg = {s: smoothstep(-MEMBER_BLEND, MEMBER_BLEND, own["leg_" + s]) for s in ("L", "R")}
m_rest = np.clip(1.0 - m_head - m_tail - m_leg["L"] - m_leg["R"], 0.0, 1.0)
for bn, w in torso_split(zz).items():
    Wt[a_:b_, J[bn]] += m_rest * w
Wt[a_:b_, J["head"]] += m_head
Wt[a_:b_, J["tail"]] += m_tail
l1 = smoothstep(LEG_J[0] - LEG_JW[0], LEG_J[0] + LEG_JW[0], zz)
l2 = smoothstep(LEG_J[1] - LEG_JW[1], LEG_J[1] + LEG_JW[1], zz)
for s in ("L", "R"):
    Wt[a_:b_, J["leg_%s.thigh" % s]] += m_leg[s] * l1
    Wt[a_:b_, J["leg_%s.shin" % s]] += m_leg[s] * (l2 - l1)
    Wt[a_:b_, J["leg_%s.foot" % s]] += m_leg[s] * (1.0 - l2)
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "body", False):
        continue
    Vw = VALL[a_:b_] + SHIFT
    if getattr(p, "arm", None):
        s = p.arm
        u1 = smoothstep(ARM_J[0] - ARM_JW[0], ARM_J[0] + ARM_JW[0], Vw[:, 2])
        u2 = smoothstep(ARM_J[1] - ARM_JW[1], ARM_J[1] + ARM_JW[1], Vw[:, 2])
        Wt[a_:b_, J["arm_%s.upper" % s]] = u1
        Wt[a_:b_, J["arm_%s.fore" % s]] = u2 - u1
        Wt[a_:b_, J["arm_%s.fist" % s]] = 1.0 - u2
    elif getattr(p, "shell", False):
        s2 = smoothstep(TORSO_J[1] - 0.30, TORSO_J[1] + 0.30, Vw[:, 2])
        Wt[a_:b_, J["spine"]] = 1.0 - s2
        Wt[a_:b_, J["chest"]] = s2
    else:
        Wt[a_:b_, J[p.bone]] = 1.0
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
                  "rule": "body: membership by SDF ownership (head / tail / legs, +-%.2f blend), the rest hips | spine | "
                          "chest by height %s; legs thigh | shin | foot by height %s; arms upper | fore | fist by height %s; "
                          "shell spine -> chest; face decals 1.0 head; flame tongues + flicks 1.0 flame"
                          % (MEMBER_BLEND, list(TORSO_J), list(LEG_J), list(ARM_J))}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """world-axis rotations (deg; in order, about the bone head, relative to the parent's posed frame) -> basis quat"""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
S = math.sin


def flame_pose(clip, u, extra=()):
    amp, (h1, h2, h3), mean = FLAME_FLICK[clip]
    flick = 0.5 * S(TAU * h1 * u + 0.3) + 0.3 * S(TAU * h2 * u + 1.1) + 0.2 * S(TAU * h3 * u + 2.0)
    Lf = 1.0 + mean + amp * flick
    Wf = 1.0 - FLAME_WIDTH_K * (Lf - 1.0)
    rots = list(extra) + [("X", FLAME_WOBBLE * S(TAU * (h1 + 2) * u + 0.4)), ("Y", FLAME_WOBBLE * S(TAU * (h2 + 2) * u + 1.7))]
    return rots, (Wf, Lf, Wf), Lf


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        br = S(TAU * 2 * u)
        P["spine"] = (None, [("X", -IDLE_BREATH[1] * br)], None)
        P["chest"] = ((0.0, 0.0, IDLE_BREATH[0] * (0.5 + 0.5 * br)), [], None)
        P["head"] = (None, [("X", IDLE_HEAD * S(TAU * 2 * u - 1.0))], None)
        for s, ph in (("L", 0.0), ("R", 1.4)):
            fx = S(TAU * u + ph)
            sc = 1.0 - FIST_FLEX[1] * max(fx, 0.0)
            P["arm_%s.fist" % s] = (None, [("X", -FIST_FLEX[0] * fx)], (sc, sc, sc))
            P["arm_%s.fore" % s] = (None, [("X", -FIST_FLEX[2] * S(TAU * u + ph - 0.6))], None)
        P["tail"] = (None, [("Z", TAIL_IDLE * S(TAU * u + 0.8))], None)
        rots, sc, Lf = flame_pose(clip, u)
    else:
        lift = {"L": max(0.0, S(TAU * u)) ** 1.3, "R": max(0.0, S(TAU * u + math.pi)) ** 1.3}
        for s in ("L", "R"):
            a = WALK_LIFT * lift[s]
            P["leg_%s.thigh" % s] = (None, [("X", -a)], None)
            P["leg_%s.shin" % s] = (None, [("X", 2.0 * a)], None)
            P["leg_%s.foot" % s] = (None, [("X", -a)], None)
        roll = -WALK_ROLL * S(TAU * u)                   # over the stance leg (L lifts first -> lean to -X)
        drop = -WALK_BOB * (0.5 + 0.5 * math.cos(TAU * 2 * u))
        P["spine"] = ((0.0, 0.0, drop), [("Y", roll), ("X", -1.0)], None)
        P["chest"] = (None, [("Z", 3.0 * S(TAU * u - 0.5)), ("Y", -0.3 * roll)], None)
        P["head"] = (None, [("Y", -0.5 * roll), ("X", 0.8 * S(TAU * 2 * u - 1.2))], None)
        for s, sg in (("L", 1.0), ("R", -1.0)):
            P["arm_%s.upper" % s] = (None, [("X", sg * WALK_ARM * S(TAU * u))], None)
            P["arm_%s.fore" % s] = (None, [("X", sg * 0.4 * WALK_ARM * S(TAU * u - 0.6))], None)
        P["tail"] = (None, [("Z", WALK_TAIL * S(TAU * u - 0.9))], None)
        rots, sc, Lf = flame_pose(clip, u, extra=[("X", -WALK_FLAME_TRAIL)])
    P["flame"] = (None, rots, sc)
    return P, {"flame_len": Lf}


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


BV = np.arange(*RANGE["body"])
dom = np.argmax(Wt, 1)
SOLE = {s: BV[(VALL[BV, 2] < 0.02) & (dom[BV] == J["leg_%s.foot" % s])] for s in ("L", "R")}
assert all(len(v) > 20 for v in SOLE.values()), {s: len(v) for s, v in SOLE.items()}
samples, clip_rep = [], {}
for cn, N in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    first = last = None
    rows = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        row = {"minz": float(C[:, 2].min()), "root": float((rig.matrix_world @ pbs["root"].head).length),
               "flame_len": float(pbs["flame"].scale[1])}
        for s in ("L", "R"):
            row["sole_" + s] = (float(C[SOLE[s], 2].min()), float(C[SOLE[s], 2].max()))
        rows.append(row)
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    soles = {}
    for s in ("L", "R"):
        lo_s = np.array([r["sole_" + s][0] for r in R_]); hi_s = np.array([r["sole_" + s][1] for r in R_])
        planted = lo_s < 1e-4
        soles[s] = {"min_z": round(float(lo_s.min()), 6), "lift_max": round(float(lo_s.max()), 4),
                    "planted_frames": int(planted.sum()), "frames": len(R_),
                    "planted_sole_top_z_range": [round(float(hi_s[planted].min()), 6), round(float(hi_s[planted].max()), 6)]
                    if planted.any() else None}
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5), "soles": soles,
                    "flame_length_scale": [round(float(g_("flame_len").min()), 4), round(float(g_("flame_len").max()), 4)]}
    assert clip_rep[cn]["clearance_min_z"] > -1e-3, ("dips below the floor", cn, clip_rep[cn])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("volcano_turtle v1: root > hips > spine > chest > head > flame; chest > arm_{L,R}.upper > .fore > "
                       ".fist; hips > leg_{L,R}.thigh > .shin > .foot; hips > tail")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given the Volcano Turtle's movement or height; open "
                               "question): idle = 4 s breathing + fist flex + flame flicker (scale pulse); walk = 2 s heavy "
                               "in-place biped stomp (thigh %.0f deg, shin folds, foot level; stance feet planted on the "
                               "floor, the body drops %.2f u at each strike)" % (WALK_LIFT, WALK_BOB))
low["conquest_locomotion"] = "in-place stomp placeholder: hips static, stance soles exactly on z 0, swing soles lifted"
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
              "structure": "armature identity + skinned 'volcano_turtle' (opaque body primitive + opaque emissive flame "
                           "primitive); natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
