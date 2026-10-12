"""Ember Stump -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/ember_stump_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNIT: Ember Stump" + the sheet design/reference/ember_stump-character-sheet.png
(intake: design/reference/ember_stump/intake.json, --cuts 460,860, orthos BACK + SIDE). Contract:
improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet; z = (804 - y_px) / 100 (floor line y 804 in all three views).
World X / Y come from the two TRUE orthos: BACK view X = -(x_px - 659.5) / 100 (the back camera sits on +Y, so image
right = -X = the unit's right), SIDE view Y = (x_px - 1024) / 100 (the side view faces image-LEFT: image left = -Y =
the front). Front = -Y, +X = the unit's LEFT. The FRONT view is slightly turned and its trunk lean is drawn MIRRORED
against the back view (front: top image-left / base image-right; back: the same image layout, which a true back view
would flip) -- the build follows the BACK view (an ortho) for every X position; the front view is used only for the
face details (eye / tooth spacing, measured relative to the mask).
ARTIST v1 VERDICT (2026-10-10, verbatim "here he should be symmetical and not leaning to one side"): the build is now
MIRROR-SYMMETRIC about X = 0 -- the trunk centreline runs straight up X = 0 (the back view's S-curve dropped; the side
view's forward arch kept), centred mask / crest / roots, mirrored arms, and the fire breath aimed straight forward
(orchestrator reading of "symmetical"; v1 aimed it 49 deg to the unit's right as the front + side views draw it).

Pipeline:
  1. TRUNK = ONE SDF: an arched generalised cylinder (elliptic sections on a centreline, knots measured per 50 px row
     off the back + side views), root lobes flaring at the floor with the floor cut raised between them (jagged root
     splits), gentle vertical bark RIDGES on the HIGH only. Marching tets = the HIGH; collapse decimation = the LOW.
  2. MASK = one SDF (own grid, closed islands): the upper jaw slab (forehead, four down-pointing teeth) + the lower jaw
     slab (three up-pointing teeth, ragged splintered bottom), a leaning front plane with shallow vertical cracks, the
     back filling to the trunk. The mouth is the real gap between the jaws (open through the full width, as the side
     view's notched profile draws it).
  3. ARMS = one SDF per arm (own grid, closed; the -X arm is the +X arm mirrored): a thin two-segment branch into a big
     upright split slab (polygon prism, two jagged prongs, ragged bottom, ridged faces), yawed 45 deg (the only yaw that
     shows ~0.85 u of slab in BOTH the back and the side view) and leaning back 10 deg (side view).
  4. Regions iso-cut into each low: vertical char streaks in the grooves + ash-pale streaks on the ridges (long windows,
     never dots), the pale tooth edges, the reddish split wood on the slab tops.
  5. CLOSED PARTS (ember_stump_parts.py): the soot crest (a dark core + 22 tapered feather blades, lengths from an
     envelope fitted to the sheet's plume, varied, rolled so no blade is edge-on to the front/side cameras), the two
     angular glowing eyes on dark sockets, the dark mouth back, the FLAME (8 layered closed tongues: 5 red outer licks
     fanned vertically + 3 orange inner tongues) leaving the mouth forward and to the unit's right.
  6. palette -> Col / Glow, flat shaded, Smart UV, bake normal + AO from the three SDF highs + every closed part except
     the flame (emissive, never occludes).
  7. RIG: root > trunk.001 > trunk.002 > trunk.003 > head (mask, eyes) > crest / flame; trunk.003 > arm_{L,R}.0 (branch)
     > arm_{L,R}.1 (slab). Clips PLACEHOLDER (movement not given): idle = slow trunk sway + crest flutter + flame flicker
     (scale pulse); walk = root-shuffle (base yaw on the floor) + lean. glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K               # noqa: E402  (read-only use)
import palettes as PAL           # noqa: E402  (read-only use)
import magmoo_sdf as SD          # noqa: E402  (read-only use: the SDF kit)
import ember_stump_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "ember_stump"
CHAR_ID = "ember_stump"               # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
DEGEN_DIST = 1e-4                     # cut slivers shorter than this (0.1 mm in sheet units x 100) are collapsed
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- TRUNK: (z, centre x, centre y, half-width x, half-depth y). x / half-width from the BACK view rows (edges px),
#   y / half-depth from the SIDE view rows (the back edge is drawn everywhere; the front edge is hidden behind the near
#   arm slab at y 505-770, so there the depth ~ the back-view width -- a round section):
#   y 804 floor: back 605-825 (roots incl.), side 962-1150 | y 750: back 640-800, side back 1140 | y 700: back 667-776,
#   side back 1145 | y 650: back 665-756, side back 1145 | y 600: back 640-742, side back 1140 | y 550: back 612-725,
#   side back 1127 | y 500: back ~600-700, side 1000-1112 | y 450: back 600-697, side back 1090 | y 385 top: back
#   595-692, side back 1055 (the mask in front)
TRUNK_KNOTS = [(0.00, 0.0, 0.30, 0.92, 0.80), (0.54, 0.0, 0.42, 0.72, 0.66), (1.04, 0.0, 0.58, 0.545, 0.60),
               (1.54, 0.0, 0.65, 0.47, 0.56), (2.04, 0.0, 0.56, 0.51, 0.60), (2.54, 0.0, 0.42, 0.565, 0.61),
               (3.04, 0.0, 0.32, 0.50, 0.56), (3.54, 0.0, 0.17, 0.485, 0.49), (4.19, 0.0, -0.11, 0.485, 0.42)]
TRUNK_X_V1 = [-0.555, -0.60, -0.615, -0.505, -0.315, -0.09, 0.08, 0.11, 0.16]   # v1's back-view S-curve centre x per knot
                                      #   (artist v1 verdict 2026-10-10 "here he should be symmetical and not leaning to
                                      #   one side": the centre x is now 0 at every knot; the side-view arch (y) is kept)
TOP_Z = 4.19                          # "stump top": flat cut under the crest (all views y 385)
ROOTS = [(-90.0, 0.90), (-35.0, 1.00), (-145.0, 1.00), (30.0, 0.80), (150.0, 0.80), (90.0, 0.85)]
                                      # "root splits": (angle deg in the trunk section, 0 = +X, 90 = +Y; relative size);
                                      #   mirror pairs about X = 0 (theta <-> 180 - theta) + the front / back roots
ROOT_A, ROOT_Z, ROOT_POW = 0.24, 0.95, 6.0   # root flare: extra radius share at the floor, height it fades out by, sharpness
NOTCH_H = 0.11                        # "jagged base": the bottom edge lifts this far between the roots
RIDGE_N, RIDGE_A = 13, 0.030          # "bark ridges" (HIGH only -> normal map): groove count round the trunk, depth share
RIDGE_WARP = (0.22, 1.1)              # irregular groove spacing: phi + a sin(3 phi) (phi from +Y; odd = mirror-symmetric; [1] unused since v1 verdict)
CHAR_TAU, ASH_TAU = 0.62, 0.78        # streak widths: char where cos(groove) > tau, ash where -cos > tau (ridge crests)
STREAK_TAPER = 3.0                    # streak ends taper over ~1 / this (u)
SDF_STEP, SDF_BAND = 0.020, 0.08      # trunk grid
BODY_TRIS = 6300                      # "trunk mesh detail" (before the colour cuts)
# ---- MASK (front view, relative to the mask centre x 232.5; FACE DETAIL panel for the teeth): width 188-277 px = 0.89;
#   top y 385 (z 4.19); upper teeth tips (panel, scaled) u -0.371 / -0.138 / 0.138 / 0.39 at z 3.53, valleys
#   -0.261 / -0.003 / 0.261 at z 3.72; lower teeth tips under the valleys at z 3.27 (front y 478-480), lower valleys
#   z 3.08 (y 495); ragged bottom y 585-612 (z 1.92-2.19). Side view: front face x 947 (y 375) -> 980 (y 575).
#   Back view: the mask's +X edge shows as a sliver at x 587-597 (X +0.62..+0.72) -> the mask centre X +0.26.
MASK_CX = 0.0                         # "mask placement" (X of its centre): centred (v1 verdict; v1 had 0.26)
MASK_HW = 0.445                       # "mask width" (half)
MASK_FRONT_TOP, MASK_LEAN = -0.77, 0.175   # front face y at the top; it leans back this much per unit down (side view)
MASK_MIN_T = 0.26                     # slab thickness where no trunk is behind it (the back-view sliver)
MASK_SINK = 0.10                      # elsewhere its back runs this far into the trunk
UPPER_JAW = [(-0.445, 3.64), (-0.371, 3.53), (-0.261, 3.72), (-0.138, 3.535), (0.0, 3.72), (0.138, 3.535),
             (0.261, 3.72), (0.371, 3.53), (0.445, 3.64), (0.445, 4.19), (-0.445, 4.19)]
_BOT = [(0.445, 2.20), (0.36, 2.03), (0.27, 2.16), (0.18, 1.98), (0.09, 2.13)]          # ragged bottom, mirrored
LOWER_JAW = ([(-0.445, 3.16), (-0.371, 3.08), (-0.261, 3.27), (-0.138, 3.08), (0.0, 3.27), (0.138, 3.08), (0.261, 3.27),
              (0.371, 3.08), (0.445, 3.16)] + _BOT + [(0.0, 2.00)] + [(-u, z) for u, z in _BOT[::-1]])
MASK_CRACK = (0.17, 0.012)            # "mask cracks": spacing, depth (HIGH ridges; the char cracks are windows on them)
MASK_STEP, MASK_BAND = 0.012, 0.05
MASK_TRIS = 1500
MASK_MAX_EDGE = 0.12                  # flat-face refinement before the crack cuts (see cut_island)
# ---- EYES (front view: centres x 208 / 253 -> u -0.245 / +0.205, y 405 -> z 3.98; 0.23-0.27 x 0.10, angry slant)
EYE_U, EYE_Z = (-0.225, 0.225), 3.98   # mirrored (v1 front-view -0.245 / +0.205)
EYE_OUTLINE = [(-0.135, -0.035), (-0.06, -0.058), (0.05, -0.045), (0.14, 0.038), (0.04, 0.052), (-0.07, 0.026)]
                                      # "eye shape" for the +u eye (inner tip low on the -u side); the -u eye mirrors it
EYE_SOCKET = 1.35                     # dark rim: the outline scaled by this
EYE_PROUD, SOCKET_PROUD = 0.022, 0.010
# ---- ARMS (+X arm; the -X arm mirrors it). Back view: slab 484-561 px (+X) / 752-840 (-X) -> centre |X| 1.37, 0.85
#   shown; inner prong top y 435-440 (z 3.66), outer prong top y 475-478 (z 3.27), V notch y 555-560 (z 2.46), bottom
#   y 720-755 (z ~0.6). Side view: the near slab 972-1090 px wide, top leaning back (top centre 1050, bottom 1000).
SLAB_C = (1.37, 0.0, 2.14)            # "slab placement": centre (the lean pivot)
SLAB_YAW, SLAB_LEAN = 45.0, 10.0      # broad face toward the front-outside; top leans back (deg)
SLAB_T = 0.24                         # "slab thickness"
SLAB_POLY = [(-0.475, 0.70), (-0.40, 0.58), (-0.31, 0.68), (-0.22, 0.56), (-0.12, 0.66), (-0.03, 0.55), (0.07, 0.67),
             (0.16, 0.57), (0.26, 0.69), (0.35, 0.58), (0.42, 0.66), (0.475, 0.60), (0.475, 3.15), (0.43, 3.29),
             (0.37, 3.18), (0.31, 3.24), (0.24, 3.12), (0.17, 3.20), (0.13, 2.85), (0.07, 2.47), (0.00, 2.80),
             (-0.03, 3.25), (-0.06, 3.45), (-0.10, 3.38), (-0.16, 3.52), (-0.24, 3.66), (-0.30, 3.47), (-0.36, 3.55),
             (-0.43, 3.36), (-0.475, 3.20)]   # "slab outline" (u inner -> outer, z): ragged bottom, two split prongs
SLAB_RIDGE = (0.22, 0.0)            # slab face ridges: spacing, depth -- depth 0 (v1 run 1: 0.016 read as corrugated planks in the normal map)
SPLINTER_Z = 2.40                     # split-wood (reddish) walls above this
BRANCH = [((0.36, 0.28, 3.16), 0.105), ((0.78, 0.16, 2.92), 0.085), ((1.13, 0.06, 2.64), 0.07)]
                                      # "branch": (point, radius) root (buried in the trunk) -> elbow -> into the slab
                                      #   (back view 600,500 -> 555,545 px; front 190,520 -> 140,560)
ARM_STEP, ARM_BAND, ARM_K = 0.016, 0.09, 0.05
ARM_TRIS = 1400                       # per arm
ARM_MAX_EDGE = 0.20                   # flat-face refinement before the streak cuts (see cut_island)
SLAB_TAU = (0.62, 0.66)               # flat-wood streak widths (char, ash): wider than the trunk's (a flat face shows them whole)
# ---- SOOT CREST (back view x 542-760 -> X +1.17..-1.0, top y 177; side x 892-1111 -> Y -1.32..+0.87, top y 172;
#   widest at y ~300-340 (z 4.6-5.0); lowest drooping feathers y 380-395 (z ~4.1-4.2))
CREST_CORE_C, CREST_CORE_R = (0.0, -0.13, 4.36), (0.60, 0.55, 0.40)   # dark core (covers the stump top)
CREST_ENV_C, CREST_ENV_R = (0.0, -0.225, 4.85), (1.09, 1.10, 1.45)    # the plume envelope the feather tips reach
CREST_N = 30                          # "feather count" (15 on the +X side, each mirrored to -X)
CREST_EL = (-16.0, 80.0)              # feather elevation range (deg); front-sector feathers stay >= CREST_FRONT_EL
CREST_FRONT_EL = 14.0                 #   (they must not hang over the eyes)
CREST_LEN_VAR = (0.86, 1.05)          # tips reach this share of the envelope (varied: no ribbed fan)
CREST_W = (0.20, 0.28)                # feather half-width range
CREST_T = 0.035                       # midrib half-thickness
CREST_DROOP = 0.12                    # outward feathers droop (bend) by this share of their length
CREST_JIT = (9.0, 14.0)               # azimuth / elevation jitter (deg)
CREST_SEED = 3.0
# ---- FIRE (front view: from the mouth (x ~245-270, y 450-485) to the tip x 135 (y 455-470), fanning to y 430-530;
#   side view: from the mouth (x ~1000) to x 877 (y 470), fanning y 425-520) -> forward 0.84-0.9 and to the unit's
#   right 0.97-1.1: azimuth ~49 deg from the front toward -X, level
FLAME_ROOT = (0.0, 0.20, 3.40)      # inside the mouth: (u from the mask centre, depth behind the face, z)
FLAME_AZ, FLAME_EL = 0.0, -2.0        # "fire direction": deg from the front toward the unit's right; elevation. v1 49
                                      #   (the sheet's front + side views); 0 = straight forward on the centre line
                                      #   (orchestrator reading of the artist's "symmetical", v1 verdict 2026-10-10)
FLAME_TONGUES = [("flame_edge", 24.0, 0.0, 1.12, 0.20, 0.10, 0.4), ("flame_edge", 11.0, 0.0, 1.45, 0.23, -0.08, 1.9),
                 ("flame_edge", -2.0, 0.0, 1.58, 0.24, 0.07, 3.1), ("flame_edge", -15.0, 0.0, 1.38, 0.23, -0.09, 4.4),
                 ("flame_edge", -29.0, 0.0, 1.08, 0.19, 0.11, 5.6), ("flame_core", 8.0, 0.0, 1.02, 0.28, 0.06, 0.9),
                 ("flame_core", -4.0, 0.0, 1.18, 0.30, -0.05, 2.6), ("flame_core", -14.0, 0.0, 0.92, 0.26, 0.07, 3.8)]
                                      # "flame tongues": (layer, elevation offset deg, azimuth offset deg, length, radius,
                                      #   curl, phase): red outer licks fanned vertically, orange inner tongues
FLAME_FLAT = 0.72                     # tongue sections squashed sideways (the fan reads in both views)
FLAME_NS, FLAME_SEG = 10, 10
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
GLOW_CAP = 2.0                        # emissive ceiling: max channel / 255 x emission_scale x emission_strength
# ---- rig
TRUNK_JOINTS = (1.45, 2.85, 4.00)     # trunk.001 | .002 | .003 | head split heights
JOINT_BLEND = (0.20, 0.20, 0.15)      # weight blend half-widths at each joint
# ---- clips (PLACEHOLDER: the artist has not given Ember Stump's movement). 24 fps; integer harmonics per loop ->
#   frame N+1 == frame 1. The base (trunk.001) never pitches or rolls: it stays flat on the floor.
IDLE_N = 120                          # "idle loop": 5 s (v2 note: room for a clear fire-off stretch)
FIRE_CYCLE = (24, 8, 32, 10)          # "fire on/off" (artist 2026-10-10 "the fire out of his mouth should be an animation
                                      #   that has it on and off"), idle frames: off until 24, GROW 8 (0.33 s, ease-out),
                                      #   HOLD 32 (1.33 s, with the flicker), DIE 10 (0.42 s, ease-in), then off to the end
FIRE_OFF_SCALE = 0.001                # the off flame: scaled to this (not 0: no degenerate normals) onto its root,
                                      #   which sits 0.20 behind the mask face inside the mouth
JAW_ACCENT = (0.07, 4.0)              # "mouth opens with the fire": lower-jaw drop (u) + hinge open (deg) at full fire
JAW_SPLIT_Z = 3.40                    # mask vertices below this are the lower jaw (its teeth tips z 3.27; upper 3.53)
IDLE_SWAY = ((1.2, 1.0), (0.9, 0.8))  # "trunk sway" deg: trunk.002 (pitch, roll), trunk.003 (pitch, roll), 1 cycle
IDLE_HEAD = 0.6                       # head nod deg (2 cycles)
CREST_FLUTTER = (2.0, 1.6, 0.025)     # "crest flutter": pitch deg (3 cyc), roll deg (2 cyc), scale +- (3 cyc)
FLAME_FLICK = {"idle": (0.08, (7, 11, 17), 0.0), "walk": (0.10, (4, 6, 9), 0.04)}   # "flame flicker": length +-,
                                      #   harmonics per loop, mean stretch; the width narrows by FLAME_WIDTH_K x stretch
FLAME_WIDTH_K = 0.5
FLAME_WOBBLE = 1.5                    # flame wobble deg
ARM_IDLE = (1.2, 1.8)                 # arm sway deg: branch, slab (lagging)
WALK_N = 48                           # "shuffle loop": 2 s
WALK_YAW = 4.0                        # "root shuffle": base yaw deg (on the floor)
WALK_LEAN = (3.0, 1.5, 3.0)           # trunk.002: forward lean deg, its 2-cycle bob, side roll deg
WALK_COUNTER = (1.0, 1.2)             # trunk.003 counter pitch / roll
WALK_ARM = (5.0, 4.0)                 # arm swing deg: branch, slab (L / R opposite)

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
          "design/reference/ember_stump-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; floor z = 0 at the sheet floor line (y 804); X from the back view, "
                   "Y from the side view", "overrides": OVERRIDES}
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


RIDGE_ON = [1.0]                     # 1 while the HIGH grids are filled (ridges / cracks), 0 for the smooth LOW source


def sdf_island(name, build, tris_target, min_frac=0.02, floor=False):
    """HIGH = polygonise(build() with the ridges) (the bake source); LOW = collapse decimation of polygonise(build()
    WITHOUT the ridges) (ridges at low-poly density read as corrugated planks: they live in the normal map only);
    keep islands; gate the low against the high (hull-fold).
    floor: collapse decimation places merged vertices at their quadric optimum, which can sit a hair under a flat
    floor cut -- those are snapped back onto z = 0 (counted + the worst dip reported)."""
    t_ = time.time()
    RIDGE_ON[0] = 1.0
    grid = build()
    HV, HF = SD.polygonise(grid)
    HF = [list(map(int, f)) for f in HF]
    HV, HF, crumbs = keep_islands(HV, HF, min_frac)
    RIDGE_ON[0] = 0.0
    g0 = build()
    SV, SF = SD.polygonise(g0)
    SV, SF, _ = keep_islands(SV, [list(map(int, f)) for f in SF], min_frac)
    RIDGE_ON[0] = 1.0
    del g0
    tmp = new_obj("dec_src", SV, SF)
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
    rep_ = {"low_source": "the same SDF without ridges (smooth), %d tris" % len(SF), "step": grid.step, "band": grid.band, "grid": grid.n.tolist(), "high_tris": len(HF), "crumbs": crumbs,
            "decimated_tris": tri_count_F(LF), "specks": specks,
            "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                            "max": round(float(dev.max()), 5)},
            "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
            "bbox_low_over_high": (bb_l / bb_h).round(5).tolist(), "floor_snap": snap, "seconds": round(time.time() - t_, 1)}
    assert np.all(bb_l / bb_h > 0.97), (name, rep_)           # the hull-fold hazard (BUILD_CONTRACT): gate it
    print("SDF", name, json.dumps(rep_))
    return HV, HF, LV, LF, rep_


def cut_island(V, F, field_fn, cuts, max_edge=None, face_pred=None):
    """firefly's iso-cutter on one island: every cut = (field, tau, gate) with gate = None or [(field, '>'/'<', v)]
    that both edge ends must satisfy. max_edge: first split edges longer than this (flat slabs decimate to big
    triangles, and a streak narrower than a triangle never crosses an edge, so it would vanish); the fields are then
    evaluated on the refined vertices (field_fn(V) -> {name: values}). -> cut V, F, per-face field means, normals, log"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    refine = 0
    if max_edge:
        for _ in range(2):                                   # 1-to-4 splits of the big faces facing the camera side
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
    # cut slivers: a crossing snapped onto an existing vertex can leave a zero-area face (v1 run 2: 2 zero-area faces +
    # 1 flipped UV failed uv_health) -- collapse them (closed topology kept), re-triangulate, then gate
    n0 = len(bm.faces)
    bmesh.ops.dissolve_degenerate(bm, dist=DEGEN_DIST, edges=bm.edges[:])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    flips = 0
    for _ in range(6):                                       # collinear slivers (a vertex ON the opposite edge, left by
        flat = [f for f in bm.faces if f.calc_area() <= DEGEN_DIST ** 2]   # the refinement splits): flip the long edge
        if not flat:
            break
        eds = list({max(f.edges, key=lambda e: e.calc_length()) for f in flat})
        res = bmesh.ops.rotate_edges(bm, edges=eds, use_ccw=False)
        flips += len(res["edges"])
    min_area = min(f.calc_area() for f in bm.faces)
    log.append({"degenerate_dissolved_faces": n0 - len(bm.faces), "sliver_edge_flips": flips, "min_face_area": float(min_area)})
    assert min_area > DEGEN_DIST ** 2, log[-1]
    bm.verts.index_update(); bm.faces.index_update(); bm.normal_update()
    CV = np.array([v.co[:] for v in bm.verts])
    CF = [[v.index for v in f.verts] for f in bm.faces]
    # region fields at the face CENTROIDS (true field values): a streak narrower than its triangles is cut into a strip
    # whose corners all sit ON the iso-line (value 0), so corner means cannot classify it (v1 verdict pass: 0 ash faces).
    # For the CUT fields a face also needs every corner >= the line: an uncut face straddling it (gated edges) must not
    # join by its centroid (sawtooth teeth)
    FVAL = field_fn(np.array([np.mean([v.co[:] for v in f.verts], 0) for f in bm.faces]))
    for key in {c_[0] for c_ in cuts}:
        cmin = np.array([min(v[LAY[key]] for v in f.verts) for f in bm.faces])
        FVAL[key] = np.where(cmin >= -1e-6, FVAL[key], np.minimum(FVAL[key], cmin))
    FN = np.array([f.normal[:] for f in bm.faces])
    bm.free()
    return CV, CF, FVAL, FN, log


def streak_windows(n, seed, z_lo, z_hi, len_rng, prob, two_prob):
    """per groove: a list of (z0, z1) windows (long streaks, deterministic)"""
    out = []
    for k in range(n):
        w = []
        if FP.hash01(k, seed) < prob:
            L_ = len_rng[0] + (len_rng[1] - len_rng[0]) * FP.hash01(k, seed + 1.3)
            z0 = z_lo + (z_hi - z_lo - L_) * FP.hash01(k, seed + 2.7)
            w.append((z0, z0 + L_))
            if FP.hash01(k, seed + 4.1) < two_prob and z0 + L_ + 0.5 < z_hi:
                L2 = 0.5 * L_
                w.append((z0 + L_ + 0.35, min(z_hi, z0 + L_ + 0.35 + L2)))
        out.append(w)
    return out


def sym_key(k, off):
    """mirror-pair key: groove k and its mirror -k (off 0), ridge m and its mirror -m-1 (off 1) share windows"""
    return np.minimum(np.abs(k), np.abs(-k - off))


def window_term(k, z, wins):
    """0 inside the groove's window(s), falling linearly (STREAK_TAPER per unit) outside; -9 for no window"""
    out = np.full(len(z), -9.0)
    for kk in np.unique(k):
        sel = k == kk
        for z0, z1 in wins[int(kk)]:
            out[sel] = np.maximum(out[sel], np.minimum(0.0, np.minimum((z[sel] - z0), (z1 - z[sel])) * STREAK_TAPER))
    return out


# =========================================================================== 1. TRUNK SDF
t_sdf = time.time()
_kn = np.array(TRUNK_KNOTS, float)
_dense = SD.catmull(_kn, 16)
_dense = _dense[np.argsort(_dense[:, 0], kind="stable")]
TZ = _dense[:, 0]


def trunk_at(z):
    z = np.clip(z, TZ[0], TZ[-1])
    return [np.interp(z, TZ, _dense[:, j]) for j in range(1, 5)]       # cx, cy, a, b


_ROOT_TH = np.radians([r[0] for r in ROOTS]); _ROOT_S = np.array([r[1] for r in ROOTS])


def wob(z, ph):
    return 0.35 * np.sin(1.3 * z + ph) + 0.18 * np.sin(2.9 * z + 1.7 * ph)


def trunk_local(P):
    z = P[:, 2]
    cx, cy, a, b = trunk_at(z)
    qx = (P[:, 0] - cx) / a; qy = (P[:, 1] - cy) / b
    th = np.arctan2(qy, qx)
    phi = np.arctan2(qx, qy)                             # from +Y (the back): mirror X -> -X maps phi -> -phi
    thw = phi + RIDGE_WARP[0] * np.sin(3.0 * phi) + 0.08 * wob(z, 0.7) * np.sin(phi)   # odd in phi: mirror-symmetric
    return z, cx, cy, a, b, qx, qy, th, thw


def root_lobe(th):
    lob = np.zeros(len(th))
    for t0, s0 in zip(_ROOT_TH, _ROOT_S):
        lob = np.maximum(lob, s0 * np.maximum(np.cos(th - t0), 0.0) ** ROOT_POW)
    return lob


def groove_phase(thw, z):
    return RIDGE_N * thw


def sd_trunk(P, ridges=True):
    z, cx, cy, a, b, qx, qy, th, thw = trunk_local(P)
    rr = np.hypot(qx, qy)
    lob = root_lobe(th)
    flare = 1.0 + ROOT_A * lob * smoothstep(ROOT_Z, 0.0, z) ** 1.3
    ridge = 1.0 - RIDGE_ON[0] * RIDGE_A * (0.5 + 0.5 * np.cos(groove_phase(thw, z))) ** 3 if ridges else 1.0
    d = (rr - flare * ridge) * np.minimum(a, b)
    d = np.maximum(d, NOTCH_H * (1.0 - np.clip(lob / _ROOT_S.max(), 0, 1)) - z)
    return np.maximum(d, z - TOP_Z)


_tz = np.linspace(0, TOP_Z, 60)
_tc = np.array(trunk_at(_tz)).T
_rx = math.ceil(((_tc[:, 2] * (1 + ROOT_A)).max() + 0.06) / SDF_STEP) * SDF_STEP; _ry = (_tc[:, 3] * (1 + ROOT_A)).max() + 0.06
def build_trunk():
    g = SD.Grid((_tc[:, 0].min() - _rx, _tc[:, 1].min() - _ry, -0.02), (_tc[:, 0].max() + _rx, _tc[:, 1].max() + _ry,
                TOP_Z + 0.02), SDF_STEP, SDF_BAND)
    _ghi = g.lo + g.step * (g.n - 1)
    for _z0 in np.arange(g.lo[2], _ghi[2], 0.75):             # z chunks (memory); overlapping margins min-union idempotently
        g.apply(sd_trunk, np.array([g.lo[0], g.lo[1], _z0]), np.array([_ghi[0], _ghi[1], min(_z0 + 0.75, _ghi[2])]), 0.0)
    g.floor(0.0)
    return g


HV_T, HF_T, LV_T, LF_T, rep_t = sdf_island("trunk", build_trunk, BODY_TRIS, floor=True)
report["sdf"] = {"trunk": rep_t}
assert HV_T[:, 2].min() > -1e-6, "trunk high dips below the floor"

# trunk streak fields
CHAR_WIN = streak_windows(RIDGE_N, 1.0, 0.15, TOP_Z - 0.2, (1.0, 2.6), 0.85, 0.45)
ASH_WIN = streak_windows(RIDGE_N, 7.0, 0.10, 2.7, (0.7, 1.6), 0.60, 0.3)


def trunk_fields(P):
    z, cx, cy, a, b, qx, qy, th, thw = trunk_local(P)
    ph = groove_phase(thw, z)
    k_g = sym_key(np.round(ph / TAU).astype(int), 0)
    k_r = sym_key(np.round((ph - math.pi) / TAU).astype(int), 1)
    fchar = np.cos(ph) - CHAR_TAU + window_term(k_g, z, CHAR_WIN)
    fash = -np.cos(ph) - ASH_TAU + window_term(k_r, z, ASH_WIN)
    return {"char": fchar, "ash": fash}


t = time.time()
CV_T, CF_T, FV_T, FN_T, log_t = cut_island(LV_T, LF_T, trunk_fields, [("char", 0.0, None), ("ash", 0.0, None)])
reg_t = np.full(len(CF_T), "bark", dtype=object)
reg_t[FV_T["ash"] > 0] = "bark_ash"
reg_t[FV_T["char"] > 0] = "bark_char"
report["iso_cuts"] = {"trunk": {"cuts": log_t, "tris_after": len(CF_T), "seconds": round(time.time() - t, 1)}}

# =========================================================================== 2. MASK SDF (upper + lower jaw)
_UJ = np.array(UPPER_JAW); _LJ = np.array(LOWER_JAW)
M_EN0 = np.array([0.0, -1.0, -MASK_LEAN]) / math.sqrt(1.0 + MASK_LEAN ** 2)   # the mask face's outward normal
_lean_n = math.sqrt(1.0 + MASK_LEAN ** 2)


def mask_front_y(z):
    return MASK_FRONT_TOP + (TOP_Z - z) * MASK_LEAN


def mask_back_y(P):
    z = P[:, 2]
    cx, cy, a, b = trunk_at(z)
    q = (P[:, 0] - cx) / a
    tf = cy - b * np.sqrt(np.clip(1.0 - q * q, 0.0, 1.0)) - 5.0 * np.maximum(np.abs(q) - 1.0, 0.0)
    return np.minimum(np.maximum(mask_front_y(z) + MASK_MIN_T, tf + MASK_SINK), 0.40)   # 0.40: inside the grid


def mask_phase(u, z):
    return TAU * u / MASK_CRACK[0] + wob(z, 2.3) * 2.0 * np.sin(math.pi * u / (2.0 * MASK_HW))   # odd in u: symmetric


def mask_groove(u, z):
    return np.cos(mask_phase(u, z))


def sd_mask(P):
    u = P[:, 0] - MASK_CX
    z = P[:, 2]
    Q = np.stack([u, z], 1)
    d2 = np.minimum(FP.sd_polygon(Q, _UJ), FP.sd_polygon(Q, _LJ))
    yf = mask_front_y(z) + RIDGE_ON[0] * MASK_CRACK[1] * (0.5 + 0.5 * mask_groove(u, z)) ** 3
    d_front = (yf - P[:, 1]) / _lean_n
    d_back = P[:, 1] - mask_back_y(P)
    return np.maximum(np.maximum(d2, d_front), d_back)


_mhx = math.ceil((MASK_HW + 0.03) / MASK_STEP) * MASK_STEP           # grid nodes mirror about X = 0
_mlo = np.array([MASK_CX - _mhx, MASK_FRONT_TOP - 0.05, 1.94])
_mhi = np.array([MASK_CX + _mhx, 0.45, TOP_Z + 0.03])
def build_mask():
    g = SD.Grid(_mlo, _mhi, MASK_STEP, MASK_BAND)
    g.apply(sd_mask, _mlo, _mhi, 0.0)
    return g


HV_M, HF_M, LV_M, LF_M, rep_m = sdf_island("mask", build_mask, MASK_TRIS, min_frac=0.05)
report["sdf"]["mask"] = rep_m
assert rep_m["crumbs"]["kept"] == 2 and rep_m["specks"]["kept"] == 2, "mask must be exactly the two jaw slabs"

MCHAR_WIN = streak_windows(9, 11.0, 2.05, TOP_Z - 0.05, (0.35, 0.9), 0.6, 0.3)


def mask_fields(P):
    u = P[:, 0] - MASK_CX
    z = P[:, 2]
    ph = mask_phase(u, z)
    k = sym_key(np.round(ph / TAU).astype(int), 0)
    return {"front": P[:, 1] - mask_front_y(z), "mchar": np.cos(ph) - SLAB_TAU[0] + window_term(k, z, MCHAR_WIN)}


t = time.time()
CV_M, CF_M, FV_M, FN_M, log_m = cut_island(LV_M, LF_M, mask_fields, [("mchar", 0.0, [("front", "<", 0.04)])], max_edge=MASK_MAX_EDGE,
                                       face_pred=lambda n_, c_: float(n_ @ M_EN0) > 0.8)
reg_m = np.full(len(CF_M), "mask", dtype=object)
_mfront = FV_M["front"] < 0.03
reg_m[_mfront & (FV_M["mchar"] > 0)] = "mask_char"
_fz = np.array([np.mean(CV_M[f, 2]) for f in CF_M])
reg_m[(~_mfront) & (np.abs(FN_M[:, 2]) > 0.30) & (_fz > 3.0) & (_fz < 3.80)] = "tooth_edge"
report["iso_cuts"]["mask"] = {"cuts": log_m, "tris_after": len(CF_M), "seconds": round(time.time() - t, 1)}

# =========================================================================== 3. ARM SDF (+X arm; -X = mirror)
_yaw, _lean = math.radians(SLAB_YAW), math.radians(SLAB_LEAN)
_Rx = np.array([[1, 0, 0], [0, math.cos(-_lean), -math.sin(-_lean)], [0, math.sin(-_lean), math.cos(-_lean)]])
E_U = _Rx @ np.array([math.cos(_yaw), math.sin(_yaw), 0.0])          # width axis: inner/front -> outer/back
E_N = _Rx @ np.array([math.sin(_yaw), -math.cos(_yaw), 0.0])         # broad-face normal: outward-front
E_V = _Rx @ np.array([0.0, 0.0, 1.0])                                # up (leaning back)
SLAB_O = np.array(SLAB_C, float)
_SP = np.array(SLAB_POLY)


def slab_local(P):
    d = P - SLAB_O
    return d @ E_U, d @ E_V + SLAB_O[2], d @ E_N


def slab_half(u, v):
    return 0.5 * SLAB_T - RIDGE_ON[0] * SLAB_RIDGE[1] * (0.5 + 0.5 * np.cos(TAU * u / SLAB_RIDGE[0] + wob(v, 4.1) * 0.6)) ** 3


def sd_slab(P):
    u, v, w = slab_local(P)
    d2 = FP.sd_polygon(np.stack([u, v], 1), _SP)
    dw = np.abs(w) - slab_half(u, v)
    return np.minimum(np.maximum(d2, dw), 0.0) + np.hypot(np.maximum(d2, 0.0), np.maximum(dw, 0.0))


def sd_branch(P):
    d = np.full(len(P), 1e9)
    for (a, ra), (b, rb) in zip(BRANCH[:-1], BRANCH[1:]):
        d = np.minimum(d, SD.sd_round_cone(P, a, b, ra, rb))
    return d


_corners = np.array([SLAB_O + E_U * u + E_V * (v - SLAB_O[2]) for u, v in SLAB_POLY])
_alo = np.minimum(_corners.min(0), np.array([p for p, _ in BRANCH]).min(0)) - 0.2
_ahi = np.maximum(_corners.max(0), np.array([p for p, _ in BRANCH]).max(0)) + 0.2
_bp = np.array([p for p, _ in BRANCH])


def build_arm():
    g = SD.Grid(_alo, _ahi, ARM_STEP, ARM_BAND)
    g.apply(sd_slab, _corners.min(0) - 0.15, _corners.max(0) + 0.15, 0.0)
    g.apply(sd_branch, _bp.min(0) - 0.12, _bp.max(0) + 0.12, ARM_K)
    return g


HV_A, HF_A, LV_A, LF_A, rep_a = sdf_island("arm", build_arm, ARM_TRIS)
report["sdf"]["arm"] = rep_a
SCHAR_WIN = streak_windows(9, 21.0, 0.6, 3.4, (0.9, 2.2), 0.60, 0.3)
SASH_WIN = streak_windows(9, 27.0, 0.6, 3.0, (0.6, 1.6), 0.75, 0.5)


def arm_fields(P):
    u, v, w = slab_local(P)
    ph = TAU * u / SLAB_RIDGE[0] + wob(v, 4.1) * 0.6
    k_g = np.mod(np.round(ph / TAU).astype(int), 9)
    k_r = np.mod(np.round((ph - math.pi) / TAU).astype(int), 9)
    ds, db = sd_slab(P), sd_branch(P)
    return {"own_slab": db - ds, "schar": np.cos(ph) - SLAB_TAU[0] + window_term(k_g, v, SCHAR_WIN),
            "sash": -np.cos(ph) - SLAB_TAU[1] + window_term(k_r, v, SASH_WIN), "wf": np.abs(w) - slab_half(u, v), "v": v}


t = time.time()
_slab_only = [("own_slab", ">", 0.0)]
CV_A, CF_A, FV_A, FN_A, log_a = cut_island(LV_A, LF_A, arm_fields, [("own_slab", 0.0, None), ("schar", 0.0, _slab_only),
                                                                     ("sash", 0.0, _slab_only)], max_edge=ARM_MAX_EDGE,
                                       face_pred=lambda n_, c_: abs(float(n_ @ E_N)) > 0.8)
reg_a = np.full(len(CF_A), "bark", dtype=object)
_slab = FV_A["own_slab"] > 0
reg_a[_slab & (FV_A["sash"] > 0)] = "bark_ash"
reg_a[_slab & (FV_A["schar"] > 0)] = "bark_char"
reg_a[_slab & (FV_A["v"] > SPLINTER_Z) & (np.abs(FN_A @ E_N) < 0.5)] = "splinter"   # the split walls, not the faces
report["iso_cuts"]["arm"] = {"cuts": log_a, "tris_after_per_arm": len(CF_A), "seconds": round(time.time() - t, 1)}
_MIR = np.array([-1.0, 1.0, 1.0])

# =========================================================================== 4. closed parts
PARTS = []
PARTS.append(FP.Part("trunk", CV_T, CF_T, list(reg_t), bone=None))
PARTS[-1].trunk = True
PARTS.append(FP.Part("mask", CV_M, CF_M, list(reg_m), bone="head"))
for side, mir in (("L", False), ("R", True)):
    V_ = CV_A * _MIR if mir else CV_A
    F_ = [f[::-1] for f in CF_A] if mir else CF_A
    p = FP.Part("arm." + side, V_, F_, list(reg_a))
    p.arm = side
    p.own_slab = np.zeros(len(V_))
    PARTS.append(p)
_own_slab_v = arm_fields(CV_A)["own_slab"]
for p in PARTS:
    if getattr(p, "arm", None):
        p.own_slab = _own_slab_v
# eyes + sockets on the mask front plane (frame: e_u = +X, e_v up the leaning face, e_n outward)
M_EU = np.array([1.0, 0.0, 0.0])
M_EV = unit([0.0, -MASK_LEAN, 1.0])
M_EN = unit([0.0, -1.0, -MASK_LEAN])
EYE_INFO = {}
for side, u0 in zip(("R", "L"), EYE_U):                   # -u eye = the unit's right (image left in the front view)
    ol = np.array(EYE_OUTLINE) * (np.array([-1.0, 1.0]) if u0 < 0 else 1.0)
    if u0 < 0:
        ol = ol[::-1]
    org = np.array([MASK_CX + u0, mask_front_y(EYE_Z), EYE_Z])
    c2 = ol.mean(0)
    V, F, R = FP.plate_frame(c2 + (ol - c2) * EYE_SOCKET, org, M_EU, M_EV, M_EN, SOCKET_PROUD, 0.04, 0.004, "eye_socket")
    PARTS.append(FP.Part("eye_socket." + side, V, F, R, bone="head"))
    V, F, R = FP.plate_frame(ol, org, M_EU, M_EV, M_EN, EYE_PROUD, 0.03, 0.006, "eye")
    PARTS.append(FP.Part("eye." + side, V, F, R, bone="head"))
    EYE_INFO[side] = {"centre": org.round(4).tolist(), "size": (np.ptp(ol, 0)).round(3).tolist()}
# dark mouth back: a plate recessed in the mouth gap, against the trunk
_mz0, _mz1 = 3.04, 3.76
_mo = np.array([MASK_CX, mask_front_y(0.5 * (_mz0 + _mz1)), 0.5 * (_mz0 + _mz1)])
_mo_c = _mo - M_EN * 0.30
_mh = 0.5 * (_mz1 - _mz0) / float(M_EV[2])
V, F, R = FP.plate_frame(np.array([(-MASK_HW + 0.10, -_mh), (MASK_HW - 0.10, -_mh), (MASK_HW - 0.10, _mh),
                                   (-MASK_HW + 0.10, _mh)]), _mo_c, M_EU, M_EV, M_EN, 0.02, 0.14, 0.008, "mouth")
PARTS.append(FP.Part("mouth_back", V, F, R, bone="head"))
# soot crest: core + feather blades
V, F, R = FP.ellipsoid(CREST_CORE_C, CREST_CORE_R, 14, 7, "crest")
PARTS.append(FP.Part("crest_core", V, F, R, bone="crest"))
_core_c = np.array(CREST_CORE_C); _core_r = np.array(CREST_CORE_R)
_env_c = np.array(CREST_ENV_C); _env_r = np.array(CREST_ENV_R)
GOLD = math.pi * (3.0 - math.sqrt(5.0))
CREST_VIEWS = [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.643, -0.766, 0.0]),
               np.array([-0.643, -0.766, 0.0])]
CREST_INFO = []
_NH = CREST_N // 2
for i in range(_NH):                                     # the +X half; each feather is mirrored to -X below
    h1, h2, h3, h4, h5 = (FP.hash01(i, CREST_SEED + k) for k in (0.0, 1.1, 2.3, 3.7, 5.3))
    tt = (i + 0.5) / _NH
    el = CREST_EL[1] + (CREST_EL[0] - CREST_EL[1]) * tt ** 0.85 + CREST_JIT[1] * (h1 - 0.5)   # high first; even in angle
    az = 8.0 + 164.0 * ((i * 0.6180339887) % 1.0) + CREST_JIT[0] * (h2 - 0.5)   # 0 = front, 180 = back, +X side
    azw = (az + 180.0) % 360.0 - 180.0
    if abs(azw) < 60.0:
        el = max(el, CREST_FRONT_EL + 6.0 * h3)
    el = min(el, 89.0)
    a_r, e_r = math.radians(az), math.radians(el)
    d = np.array([math.sin(a_r) * math.cos(e_r), -math.cos(a_r) * math.cos(e_r), math.sin(e_r)])
    root = _core_c + d * (1.0 / np.linalg.norm(d / _core_r)) * 0.55
    oc = (root - _env_c) / _env_r; dd = d / _env_r                       # ray -> envelope ellipsoid
    A_, B_, C_ = float(dd @ dd), float(2 * oc @ dd), float(oc @ oc - 1.0)
    L_env = (-B_ + math.sqrt(max(B_ * B_ - 4 * A_ * C_, 0.0))) / (2 * A_)
    L_ = L_env * (CREST_LEN_VAR[0] + (CREST_LEN_VAR[1] - CREST_LEN_VAR[0]) * h4)
    # roll: the broad face as visible as possible from the front, side and both front 3/4 cameras (no edge-on spike), + jitter
    a_, u_, v_ = FP.frame_from_axis(d)
    best, bn = -1.0, None
    for k in range(24):
        r_ = math.pi * k / 24
        n_ = math.cos(r_) * u_ + math.sin(r_) * v_
        vis = sorted(abs(float(n_ @ w_)) for w_ in CREST_VIEWS)
        sc = vis[0] + 0.3 * sum(vis) / len(vis)
        if sc > best + 1e-9:
            best, bn = sc, r_
    rj = bn + math.radians(24.0 * (h5 - 0.5))
    face_n = math.cos(rj) * u_ + math.sin(rj) * v_
    down = np.array([0.0, 0.0, -1.0]) - d * (-d[2])
    bend = unit(down) * CREST_DROOP * math.cos(e_r) if np.linalg.norm(down) > 1e-6 else np.zeros(3)
    bend = bend + np.cross(d, face_n) * 0.06 * (h3 - 0.5)
    hw = (CREST_W[0] + (CREST_W[1] - CREST_W[0]) * h1) * min(1.15, (L_ / 1.1) ** 0.5)
    V, F, R, spine = FP.feather_blade(root, d, face_n, L_, hw, CREST_T, bend, 8, 1 if h2 > 0.5 else -1)
    PARTS.append(FP.Part("crest.%02d" % i, V, F, R, bone="crest"))
    PARTS.append(FP.Part("crest.%02d" % (i + _NH), V * np.array([-1.0, 1.0, 1.0]), [f[::-1] for f in F], R, bone="crest"))
    CREST_INFO.append({"az": round(az % 360.0, 1), "el": round(el, 1), "length": round(L_, 3), "half_w": round(hw, 3),
                       "visibility_min_4views": round(min(abs(float(face_n @ w_)) for w_ in CREST_VIEWS), 3)})
# flame tongues (the fire breath)
FL_ROOT = np.array([MASK_CX + FLAME_ROOT[0], mask_front_y(FLAME_ROOT[2]) + FLAME_ROOT[1], FLAME_ROOT[2]])
_fa, _fe = math.radians(FLAME_AZ), math.radians(FLAME_EL)
FL_DIR = np.array([-math.sin(_fa) * math.cos(_fe), -math.cos(_fa) * math.cos(_fe), math.sin(_fe)])
FL_LEN = max(t_[3] for t_ in FLAME_TONGUES)
FLAME_INFO = []
for i, (reg, de, da, L_, r_, wave, ph) in enumerate(FLAME_TONGUES):
    a_r, e_r = math.radians(FLAME_AZ + da), math.radians(FLAME_EL + de)
    d = np.array([-math.sin(a_r) * math.cos(e_r), -math.cos(a_r) * math.cos(e_r), math.sin(e_r)])
    V, F, R, sp = FP.tube_tongue(FL_ROOT, d, (0.0, 0.0, 1.0), L_, r_, FLAME_NS, FLAME_SEG, wave, ph, reg, flat=FLAME_FLAT,
                                 lobes=3, lobe_a=0.12)
    p = FP.Part("flame.%d" % i, V, F, R, bone="flame")
    p.flame = True
    PARTS.append(p)
    FLAME_INFO.append({"layer": reg, "tip": (sp[-1]).round(3).tolist(), "length": L_, "radius": r_})

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
assert abs(VALL[:, 2].min()) < 1e-9 and VALL[RANGE["trunk"][0]:RANGE["trunk"][1], 2].min() < 1e-9, "the base must sit on the floor"
# mirror test (artist v1 verdict: symmetric): every vertex mirrored X -> -X, distance to the nearest point of the mesh
_bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mir_d = np.array([_bvh_all.find_nearest(Vector((-p_[0], p_[1], p_[2])))[3] for p_ in VALL])
report["symmetry"] = {"rule": "mirror each vertex X -> -X; distance to the nearest point on the mesh (0 = mirror-symmetric)",
                      "max": round(float(_mir_d.max()), 5), "p99": round(float(np.percentile(_mir_d, 99)), 5),
                      "mean": round(float(_mir_d.mean()), 5), "bbox_x": [round(float(VALL[:, 0].min()), 5), round(float(VALL[:, 0].max()), 5)],
                      "per_part_max": {k: round(float(_mir_d[a:b].max()), 5) for k, (a, b) in RANGE.items()
                                       if not k.startswith(("crest.", "flame.", "eye"))},
                      "trunk_knot_x_v1": TRUNK_X_V1, "trunk_knot_x_now": [k[1] for k in TRUNK_KNOTS]}
print("SYMMETRY", json.dumps(report["symmetry"]))
REG = ["bark", "bark_char", "bark_ash", "splinter", "mask", "mask_char", "tooth_edge", "mouth", "eye_socket", "eye",
       "crest", "crest_hi", "flame_edge", "flame_core"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
FLAME_REG = {"flame_edge", "flame_core"}
assert all((RNAME[i] in FLAME_REG) == (MATI[i] == 1) for i in range(len(RNAME))), "flame region off the flame material"


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.96 + 0.08 * j


def make_mat(name, kind):
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


GLOW_REGIONS = {"eye", "flame_edge", "flame_core"}


def glow_check(pal):
    """the sheet draws glow on the eyes and the fire only; tiers eye < flame edge < flame core; peak under the cap"""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    peak = {n: max(pal["regions"][n]["emission"]) / 255.0 * tier[n] * pal["material"].get("emission_strength", 1.0)
            for n in tier}
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "peak_x_strength": {k: round(v, 4) for k, v in peak.items()},
            "cap": GLOW_CAP, "only_drawn_glow": set(tier) == GLOW_REGIONS,
            "grade_pass": tier.get("eye", 9) < tier.get("flame_edge", 0) < tier.get("flame_core", 0) if set(tier) == GLOW_REGIONS else False,
            "cap_pass": all(v <= GLOW_CAP for v in peak.values())}


MAT_BODY = make_mat(UNIT + "_body", "body")
MAT_FLAME = make_mat(UNIT + "_flame", "flame")
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

# facing landmark: the stump's centreline at eye height -> the mask's front face between the eyes (straight -Y)
_ecx, _ecy, _, _ = trunk_at(np.array([EYE_Z]))
anchor = np.array([MASK_CX, float(_ecy[0]), EYE_Z]) - SHIFT
landmark = np.array([MASK_CX, mask_front_y(EYE_Z), EYE_Z]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "trunk centreline at eye height -> the mask front face between the eyes (the face looks -Y)",
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
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The arched SDF trunk (root flare + streak cuts) "
                            "carries the largest share; the two split-slab arms, the mask jaws, 22 crest feathers and 8 "
                            "flame tongues the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]), "parts": len(open_e),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every part is closed: SDF islands (trunk, 2 mask jaws, 2 arms), plates, ellipsoid, "
                                "feather blades, flame tongues"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_height_units": {"front": 6.27, "back": 6.29, "side": 6.35},
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
low["conquest_source"] = "from scratch: design/reference/ember_stump-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = "none: every material opaque (the flame is an opaque emissive mesh on its own material)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's detail panels
_face = np.array([[MASK_CX - MASK_HW - 0.25, MASK_FRONT_TOP - 0.4, 2.9], [MASK_CX + MASK_HW + 0.25, -0.2, TOP_Z + 0.35]])
_crest = np.vstack([p.V for p in PARTS if p.name.startswith("crest")])
_armL = PARTS[[p.name for p in PARTS].index("arm.L")].V
_fl = np.vstack([p.V for p in PARTS if getattr(p, "flame", False)] + [_face[1][None] * np.array([1, 1, 0]) + np.array([0, 0, 3.0])])
low["conquest_focus"] = json.dumps({
    "face": [(_face[0] - SHIFT).tolist(), (_face[1] - SHIFT).tolist()],
    "crest": [(_crest.min(0) - SHIFT).tolist(), (_crest.max(0) - SHIFT).tolist()],
    "arm": [(_armL.min(0) - SHIFT - 0.05).tolist(), (_armL.max(0) - SHIFT + 0.05).tolist()],
    "fire": [(_fl.min(0) - SHIFT - 0.05).tolist(), (_fl.max(0) - SHIFT + 0.05).tolist()],
    "views": {"face": [0.0, 4.0], "crest": [25.0, 18.0], "arm": [45.0, 4.0], "fire": [-30.0, 6.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow")}, default=str))
    print("CREST", json.dumps(CREST_INFO))
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


add_high(HV_T, HF_T)
add_high(HV_M, HF_M)
add_high(HV_A, HF_A)
add_high(HV_A * _MIR, [f[::-1] for f in HF_A])
for p in PARTS:
    if p.name in ("trunk", "mask") or getattr(p, "arm", None) or getattr(p, "flame", False):
        continue                                     # SDF lows are replaced by their highs; the flame never occludes
    add_high(p.V, p.F)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
BAKE_CAGE = max(0.02, round(3.0 * max(r_["high_to_low_sampled"]["p99"] for r_ in report["sdf"].values()), 4))
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
    "high_tris": len(PH_F), "high_rule": "trunk + mask + 2 arm SDF highs + every closed part except the flame tongues",
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
# wire the textures into the BODY material only (the flame keeps flat colour + glow)
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
report["crest"] = CREST_INFO
report["flame"] = {"root": (FL_ROOT - SHIFT).round(4).tolist(), "dir": FL_DIR.round(4).tolist(), "tongues": FLAME_INFO}
report["eyes"] = EYE_INFO
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


def cl(z):
    cx, cy, _, _ = trunk_at(np.array([z]))
    return np.array([float(cx[0]), float(cy[0]), z])


JAW_HINGE = np.array([MASK_CX, mask_front_y(3.10) + 0.30, 3.10])   # back top of the lower jaw (buried in the trunk)
ARM_PTS = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    m_ = np.array([sg, 1.0, 1.0])
    root_ = np.array(BRANCH[0][0]) * m_
    end_ = np.array(BRANCH[-1][0]) * m_
    top_ = (SLAB_O + E_V * (3.3 - SLAB_O[2])) * m_
    ARM_PTS[side] = (root_, end_, top_)
J0, J1, J2 = TRUNK_JOINTS
BONES = [("trunk.001", P_(cl(0.0)), P_(cl(J0)), "root"),
         ("trunk.002", P_(cl(J0)), P_(cl(J1)), "trunk.001"),
         ("trunk.003", P_(cl(J1)), P_(cl(J2)), "trunk.002"),
         ("head", P_(cl(J2)), P_(cl(J2) + np.array([0.0, 0.0, 0.9])), "trunk.003"),
         ("crest", P_(CREST_CORE_C), P_(np.array(CREST_CORE_C) + np.array([0.0, 0.0, 1.2])), "head"),
         ("flame", P_(FL_ROOT), P_(FL_ROOT + FL_DIR * FL_LEN), "head"),
         ("jaw", P_(JAW_HINGE), P_(JAW_HINGE + np.array([0.0, -0.4, 0.0])), "head")]
for side in ("L", "R"):
    r0, e0, t0 = ARM_PTS[side]
    BONES.append(("arm_%s.0" % side, P_(r0), P_(e0), "trunk.003"))
    BONES.append(("arm_%s.1" % side, P_(e0), P_(t0), "arm_%s.0" % side))
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
    e.use_connect = nm in ("trunk.002", "trunk.003", "head") or nm.endswith(".1")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences): trunk shell along z (joint blends); mask / eyes / mouth on head; crest on crest; flame
#      on flame; arms: the branch blends trunk.003 -> arm.0 along its first third, the slab rigid on arm.1
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["trunk"]
zz = VALL[a_:b_, 2] + SHIFT[2]
s_ = [smoothstep(jz - hw, jz + hw, zz) for jz, hw in zip(TRUNK_JOINTS, JOINT_BLEND)]
Wt[a_:b_, J["trunk.001"]] = 1.0 - s_[0]
Wt[a_:b_, J["trunk.002"]] = s_[0] - s_[1]
Wt[a_:b_, J["trunk.003"]] = s_[1] - s_[2]
Wt[a_:b_, J["head"]] = s_[2]
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if p.name == "trunk":
        continue
    if getattr(p, "arm", None):
        side = p.arm
        r0, e0, _ = ARM_PTS[side]
        Vw = VALL[a_:b_] + SHIFT
        ax_ = e0 - r0; Lb = float(np.linalg.norm(ax_))
        sb = np.clip((Vw - r0) @ ax_ / Lb, 0.0, Lb)
        wb = smoothstep(0.15 * Lb, 0.40 * Lb, sb)
        is_slab = p.own_slab > 0.0
        Wt[a_:b_, J["trunk.003"]] = np.where(is_slab, 0.0, 1.0 - wb)
        Wt[a_:b_, J["arm_%s.0" % side]] = np.where(is_slab, 0.0, wb)
        Wt[a_:b_, J["arm_%s.1" % side]] = np.where(is_slab, 1.0, 0.0)
    elif p.name == "mask":                                   # the lower jaw rides the jaw bone (fire accent)
        lowj = (VALL[a_:b_, 2] + SHIFT[2]) < JAW_SPLIT_Z
        Wt[a_:b_, J["head"]] = np.where(lowj, 0.0, 1.0)
        Wt[a_:b_, J["jaw"]] = np.where(lowj, 1.0, 0.0)
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
                  "rule": "trunk shell: smoothstep blends at z %s (half-widths %s); mask, eyes, sockets, mouth back 1.0 "
                          "head; crest core + feathers 1.0 crest; flame tongues 1.0 flame; arms: branch trunk.003 -> "
                          "arm.0 over 15-40%% of its length, slab 1.0 arm.1" % (list(TRUNK_JOINTS), list(JOINT_BLEND))}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
HH = report["measure"]["height"]


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


def fire_env(clip, f):
    """fire on/off envelope (0 off .. 1 full) at frame f of the loop: idle = off / grow (ease-out) / hold / die (ease-in)
    / off; walk = off throughout. Frame 0 (== frame N) is off, so the loop seam is exact."""
    if clip != "idle":
        return 0.0
    t0, tg, th, td = FIRE_CYCLE
    if f < t0:
        return 0.0
    if f < t0 + tg:
        x = (f - t0) / tg
        return 1.0 - (1.0 - x) ** 3
    if f < t0 + tg + th:
        return 1.0
    if f < t0 + tg + th + td:
        x = (f - t0 - tg - th) / td
        return (1.0 - x) ** 2
    return 0.0


def flame_pose(clip, u, env=1.0):
    amp, (h1, h2, h3), mean = FLAME_FLICK[clip]
    flick = 0.5 * S(TAU * h1 * u + 0.3) + 0.3 * S(TAU * h2 * u + 1.1) + 0.2 * S(TAU * h3 * u + 2.0)
    Lf = 1.0 + mean + amp * flick
    Wf = 1.0 - FLAME_WIDTH_K * (Lf - 1.0)
    rots = [("X", FLAME_WOBBLE * S(TAU * (h1 + 2) * u + 0.4)), ("Z", FLAME_WOBBLE * S(TAU * (h2 + 2) * u + 1.7))]
    k = max(FIRE_OFF_SCALE, env)
    return rots, (Wf * k, Lf * k, Wf * k), Lf * k


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        (p2, r2), (p3, r3) = IDLE_SWAY
        P["trunk.002"] = (None, [("X", p2 * S(TAU * u)), ("Y", r2 * S(TAU * u + 1.6))], None)
        P["trunk.003"] = (None, [("X", p3 * S(TAU * u - 0.7)), ("Y", r3 * S(TAU * u + 0.9))], None)
        P["head"] = (None, [("X", IDLE_HEAD * S(TAU * 2 * u - 1.2))], None)
        cs = 1.0 + CREST_FLUTTER[2] * S(TAU * 3 * u + 1.0)
        P["crest"] = (None, [("X", CREST_FLUTTER[0] * S(TAU * 3 * u + 0.4)), ("Y", CREST_FLUTTER[1] * S(TAU * 2 * u + 1.9))],
                      (cs, cs, cs))
        for side, ph in (("L", 0.0), ("R", 1.3)):
            P["arm_%s.0" % side] = (None, [("X", ARM_IDLE[0] * S(TAU * u + 0.5 + ph))], None)
            P["arm_%s.1" % side] = (None, [("X", ARM_IDLE[1] * S(TAU * u - 0.3 + ph))], None)
    else:
        P["trunk.001"] = (None, [("Z", WALK_YAW * S(TAU * u))], None)
        P["trunk.002"] = (None, [("X", WALK_LEAN[0] + WALK_LEAN[1] * S(TAU * 2 * u - 0.5)),
                                 ("Y", WALK_LEAN[2] * S(TAU * u + 0.4))], None)
        P["trunk.003"] = (None, [("X", -WALK_COUNTER[0] + 1.0 * S(TAU * 2 * u - 1.2)),
                                 ("Y", -WALK_COUNTER[1] * S(TAU * u + 0.9))], None)
        P["head"] = (None, [("X", -0.8 + 0.8 * S(TAU * 2 * u - 1.8))], None)
        cs = 1.0 + 0.02 * S(TAU * 2 * u)
        P["crest"] = (None, [("X", -3.0 * S(TAU * 2 * u - 2.2)), ("Y", 2.5 * S(TAU * u + 1.0))], (cs, cs, cs))
        for side, sg in (("L", 1.0), ("R", -1.0)):
            P["arm_%s.0" % side] = (None, [("X", sg * WALK_ARM[0] * S(TAU * u))], None)
            P["arm_%s.1" % side] = (None, [("X", sg * WALK_ARM[1] * S(TAU * u - 0.6))], None)
    env = fire_env(clip, f)
    rots, sc, Lf = flame_pose(clip, u, env)
    P["flame"] = (None, rots, sc)
    P["jaw"] = ((0.0, 0.0, -JAW_ACCENT[0] * env), [("X", JAW_ACCENT[1] * env)], None)
    return P, {"flame_len": Lf, "fire": env}


def fire_timeline(cn, N):
    """the keyed on/off phases as 1-based frame ranges (frame N+1 == frame 1)"""
    if cn != "idle":
        return {"off": [1, N + 1], "rule": "fire off on every frame (flame scale %g)" % FIRE_OFF_SCALE}
    t0, tg, th, td = FIRE_CYCLE
    return {"off": [1, t0 + 1], "grow": [t0 + 1, t0 + tg + 1], "hold": [t0 + tg + 1, t0 + tg + th + 1],
            "die": [t0 + tg + th + 1, t0 + tg + th + td + 1], "off_again": [t0 + tg + th + td + 1, N + 1],
            "seconds": {"off_total": round((N - tg - th - td) / K.FPS, 3), "grow": round(tg / K.FPS, 3),
                        "hold": round(th / K.FPS, 3), "die": round(td / K.FPS, 3)}}


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


TRV = np.arange(*RANGE["trunk"])
base_v = TRV[VALL[TRV, 2] < 0.02]
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
        rows.append({"minz": float(C[:, 2].min()), "base_z_range": float(np.ptp(C[base_v, 2])),
                     "base_minz": float(C[base_v, 2].min()), "root": float((rig.matrix_world @ pbs["root"].head).length),
                     "flame_len": float(pbs["flame"].scale[1])})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "base_on_floor": {"min_z": round(float(g_("base_minz").min()), 6),
                                      "z_spread_max": round(float(g_("base_z_range").max()), 6), "base_verts": int(len(base_v))},
                    "flame_length_scale": [round(float(g_("flame_len").min()), 4), round(float(g_("flame_len").max()), 4)],
                    "fire_timeline_frames": fire_timeline(cn, N)}
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
rig["conquest_rig"] = ("ember_stump v3: root > trunk.001 > trunk.002 > trunk.003 > head > crest / flame / jaw; trunk.003 > "
                       "arm_{L,R}.0 (branch) > arm_{L,R}.1 (split slab)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("idle = PLACEHOLDER body motion (the artist has not given Ember Stump's movement; open "
                               "question): 5 s slow trunk sway + crest flutter; fire on/off cycle per artist 2026-10-10 "
                               "(off, grow 0.33 s, hold 1.33 s with flicker, die 0.42 s, off; the lower jaw drops with the "
                               "fire); walk = PLACEHOLDER 2 s root-shuffle (base yaw %.0f deg on the floor) + lean, fire off. "
                               "Rest pose: fire ON (review / static previews)" % WALK_YAW)
low["conquest_locomotion"] = "root-shuffle placeholder: rest pose base z 0 per contract; the base never leaves the floor"
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
              "structure": "armature identity + skinned 'ember_stump' (opaque body primitive + opaque emissive flame "
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
