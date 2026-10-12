"""Petal Cobra -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/petal_cobra_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x3: Petal Cobra, Crystal Canine, Leaf Bird" (section PETAL COBRA) + the
sheet design/reference/petal_cobra-character-sheet.png (intake: design/reference/petal_cobra/intake.json). Contract:
improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. All three views are TRUE orthos:
  FRONT view (the unit's left on image right):   X = (x_px - 251) / 100   ; z = (789 - y_px) / 100
  SIDE view (the face on image LEFT):            Y = (x_px - 1000) / 100  ; z = (789 - y_px) / 100
  BACK view:                                      X = -(x_px - 684) / 100  ; z = (788 - y_px) / 100
X +ve = the unit's LEFT (L), front = -Y, floor z = 0 at the resting tail tip. The FRONT symmetry line is x 251 (head
193.5-308.5 -> 251, torso y 420 167-336 -> 251.5, eyes 225.5 / 276.8 -> 251.2, petal outer edges 31 / 469 -> 250), not the
pinned intake axis 240 (11 px left of it; the back view's line is 684 vs the pinned 673). The build is centred on the
drawn body; conquest_focus.axis_xy puts the ortho camera on the pinned 240 so the overlay lines up (deviation, reported).

Pipeline:
  1. BODY = ONE SDF: cranium + snout (the rounded head), a neck, the upright TORSO (a flattish super-elliptic panel whose
     top edge dips behind the neck and rises into the two pointed shoulder corners flanking the head), the two lower-corner
     NUBS, and the long smooth TAPERING TAIL (a dense round-cone sweep through measured knots: it curves to the unit's
     RIGHT and forward and rests its tip on the floor). Marching tets = the HIGH; collapse decimation = the LOW shell.
  2. FACE + MARKINGS on the low shell, every boundary iso-cut: two tilted almond EYES, the thin SMILE stroke, three pairs
     of oval amber MARKINGS (front-projected exactly as the front view draws them; the zones are densified first).
  3. PARTS (petal_cobra_parts.py, closed): the two HOOD PETALS, closed pillow sheets lofted between a measured inner
     edge (rooted in the upper back) and the measured outer rim (up, over the top and curling down into the top hook),
     cupped back-inward; inner face five green bands + vein strokes, back face green + veins, cream rim + top curl.
  4. palette (palettes.py regions -> Col / Glow, no glow), Smart UV, bake normal + AO from the SDF high + petals.
  5. RIG: root > tail.001 (at the planted tip) > ... > tail.005 > spine > neck > head; spine > petal_L / petal_R.
     Clips PLACEHOLDER (movement not given by the artist): idle = slow sway + hood flex; walk = in-place slither wave
     down the tail chain (yaw only, tail tip planted). glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K              # noqa: E402  (read-only use)
import palettes as PAL          # noqa: E402  (read-only use)
import magmoo_sdf as SD         # noqa: E402  (read-only use: the SDF kit)
import petal_cobra_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "petal_cobra"
CHAR_ID = "petal_cobra"               # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.20                       # BAND >= max smooth-union k (TAIL_K 0.15) + 2 steps (contract hazard note)
BODY_TRIS = 7600                      # "body mesh detail": the shell's decimation target (before densify + colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
FRONT_AXIS_PX, PINNED_AXIS_PX = 251.0, 240.0   # drawn front symmetry line vs the intake's pinned axis (see the header)
# ---- HEAD (rounded): cranium ellipsoid + a snout ellipsoid smooth-unioned. FRONT: widest |X| 0.555 at y 345 (z 4.44),
#   top y 273 (z 5.16), chin half-width 0.31 at y 390, chin y 402 (z 3.87). SIDE: top y 283 (z 5.06) at x 962, snout tip
#   x 893 (Y -1.07) at y 372 (z 4.17), chin underside y 395-400 (z 3.9) from x 905 to 965, back of the cranium ~x 1000.
#   One egg: an ellipsoid SHEARED forward-down (Y += HEAD_SHEAR (z - zc)), so its crown sits back (side y 290:
#   x 936-989) and its front low (the snout point falls out at z 4.19, Y -1.10). The head top differs between the
#   views (front 5.16 / side 5.06): the build uses 5.13 (deviation, reported).
HEAD_C = (0.0, -0.55, 4.47)           # "head" centre
HEAD_R = (0.555, 0.50, 0.66)          # "head size": half width / depth / height
HEAD_SHEAR = 0.35                     # "head lean": forward shift of the lower head per unit drop (the snake snout)
NECK = ((0.0, -0.12, 3.65), 0.32, (0.0, -0.38, 4.30), 0.30)   # "neck": round cone (a, ra, b, rb), hidden behind the chin
NECK_K = 0.12
# ---- TORSO: a super-elliptic section swept up the body. FRONT half-widths (both sides averaged, X from x 251): z 4.3
#   0.865 / 3.9 0.86 / 3.6 0.845 / 3.4 0.815 / 3.1 0.765 / 2.9 0.71 / 2.72 0.65 / 2.62 0.60; SIDE front / back edges
#   (Y): z 3.89 -0.41/0.28, 3.59 -0.46/0.35, 3.39 -0.42/0.41, 3.19 -0.34/0.48, 2.99 -0.24/0.57, 2.79 -0.09/0.61,
#   2.39 0.18/0.96. The top edge (front view): inner edge descends along the head x 190/312 @ y 360, 203/297 @ y 390;
#   the pointed shoulder corners' tips at x 170 / 322, y 308 (|X| 0.76, z 4.81); side view corner x 1004-1024 (Y 0-0.24).
TORSO_HW = [(2.35, 0.47), (2.50, 0.52), (2.62, 0.60), (2.72, 0.65), (2.90, 0.71), (3.10, 0.765), (3.40, 0.815),
            (3.60, 0.845), (3.90, 0.86), (4.30, 0.865), (4.50, 0.86), (4.62, 0.85), (4.72, 0.82), (4.80, 0.78),
            (4.86, 0.72), (4.95, 0.50)]   # "torso width" (z, half-width)
TORSO_YC = [(2.35, 0.56), (2.45, 0.50), (2.60, 0.40), (2.80, 0.26), (3.00, 0.165), (3.20, 0.07), (3.40, -0.005),
            (3.60, -0.055), (4.10, -0.04), (4.50, 0.04), (4.85, 0.12), (4.95, 0.13)]   # "torso lean" (z, centre Y)
TORSO_HD = [(2.35, 0.385), (2.60, 0.37), (2.80, 0.36), (3.00, 0.40), (3.20, 0.41), (3.40, 0.415), (3.60, 0.405),
            (3.90, 0.36), (4.10, 0.30), (4.50, 0.18), (4.85, 0.11), (4.95, 0.10)]   # "torso depth" (z, half-depth)
TORSO_TOP = [(0.0, 3.95), (0.40, 3.96), (0.47, 4.02), (0.53, 4.15), (0.61, 4.30), (0.66, 4.50), (0.70, 4.67),
             (0.76, 4.82), (0.90, 5.60)]  # "shoulder corners": the torso's top edge (|X|, z)
TORSO_P = 2.4                         # "flattish front panel": section super-ellipse exponent (2 = ellipse)
TORSO_TOP_K = 0.07                    # top-edge rounding (small: the sheet's corners are pointed)
TORSO_BOT_K = 0.15                    # the panel's underside rounds into the tail (a hard cut read as a skirt ledge)
TORSO_K = 0.10                        # torso / neck blend
NUB_C, NUB_R, NUB_K = (0.49, 0.39, 2.63), (0.15, 0.17, 0.14), 0.05   # "lower-corner nubs" (L side, mirrored): front
                                      #   nubs x 193-227 / 295-319 @ y 520, bottoms y ~535 (z 2.5)
# ---- TAIL: centreline knots (X from the FRONT row centres, Y from the SIDE row centres, z) + radius (the mean of the
#   front half-width and the side half-depth; the last knots sit on the floor). FRONT: y 560 212-306, 600 216-301,
#   650 212-287, 700 197-261, 750 150-216, 780 100-166, tip x 85 y 787. SIDE: y 560 1018-1097, 600 1023-1094,
#   650 1015-1081, 700 994-1054, 750 943-1005, 780 887-945, tip x 865 y 789.
TAIL_KNOTS = [((0.03, 0.45, 2.60), 0.50), ((0.08, 0.575, 2.29), 0.47), ((0.075, 0.59, 1.90), 0.445),
              ((0.03, 0.54, 1.60), 0.42), ((-0.02, 0.47, 1.35), 0.40), ((-0.10, 0.37, 1.12), 0.375),
              ((-0.21, 0.25, 0.90), 0.34), ((-0.36, 0.08, 0.67), 0.29), ((-0.57, -0.15, 0.45), 0.235),
              ((-0.84, -0.47, 0.25), 0.17), ((-1.18, -0.86, 0.11), 0.10), ((-1.58, -1.30, 0.035), 0.03)]
                                      #   radius = the FRONT half-width; the SIDE depth is TAIL_SQ of it
TAIL_SQ = 0.82                        # "tail section": side half-depth / front half-width (z 2.29 0.84, 1.9 0.82, 1.4 0.86)
TAIL_SUB = 4                          # "tail smoothness": round cones per knot span (Catmull-Rom resampled); the sweep is
                                      #   an EXACT union (smooth unions of overlapping cones inflate it ~0.08: v0 overlay)
TAIL_K = 0.15                         # tail / torso blend (only the spans above TAIL_K_Z)
TAIL_K_Z = 2.0
# ---- FACE (FRONT view amber blobs measured with PIL, + ~1 px of the ink): eyes centres (225.5, 365.0) / (276.8, 364.1)
#   -> X -0.255 / +0.258, z 4.245; amber 27 x 14 px; long axis tilted 29-37 deg (outer corners up). Smile: an ink stroke
#   x 243-262, ends y 393, lowest y 395.5 at x 251 (face panel: a small gentle smile under the eyes).
EYE_X, EYE_Z = 0.257, 4.245           # "eye placement" (mirrored)
EYE_AB = (0.145, 0.080)               # "eye size": half length / half height (narrow almond)
EYE_TILT = 33.0                       # "eye slant": outer corner up (deg)
SMILE = [(-0.085, 3.962), (-0.045, 3.942), (0.0, 3.934), (0.045, 3.942), (0.085, 3.962)]   # "smile" stroke (X, z)
SMILE_HW = 0.012                      # "smile line" half-width
# ---- MARKINGS (FRONT view amber blobs): pairs at z 3.84 / 3.465 / 3.08, |X| 0.60 / 0.555 / 0.48 (both sides averaged:
#   the drawn pairs sit 0.03-0.07 off mirror), amber 30x9 / 25x8.5 / 21x7.5 px; tilt outer end up 13 / 4, down 15 deg.
MARKS = [(0.600, 3.840, 0.160, 0.052, 13.0), (0.555, 3.465, 0.138, 0.050, 4.0), (0.480, 3.080, 0.118, 0.045, -15.0)]
FACE_SUBDIV = 2                       # "face / marking outline detail": local subdivision rounds round eyes + markings
SMILE_SUBDIV = 3                      # ... round the thin smile stroke
FACE_ZONE = 0.05                      # the densified zone reaches this far outside each feature
# ---- HOOD PETALS (L side; R mirrored). A closed pillow lofted between the INNER edge (from the bottom tip B up the
#   back, rooted in the torso, past the shoulder corner to the top hook H) and the OUTER rim (B, out and up the outer
#   edge, over the top and curling down into H). FRONT outer edge (X): y 230 1.53 / 260 1.77 / 300 1.98 / 350 2.14 /
#   400 2.18 / 450 2.15 / 500 2.02 / 530 1.87 / 550 1.74; top y 200 x 333-349; hook x 310-316 @ y 210-230. SIDE rim
#   (Y): y 210 0.64 / 250 1.03 / 300 1.33 / 350 1.52 / 400 1.62 / 450 1.61 / 500 1.54 / 540 1.39; hook x 990-1000 @
#   y 220; green front edge 0.27 @ y 260-300. BACK: inner edges x 670 / 699 (|X| 0.14) from y 390 to 480, reaching
#   the head at y 377 (z 4.41) but clear of it from y 357 up (gap X 0.47-0.67 @ z 4.66, matching the front's gap
#   between the head and the shoulder corner): the inner edge swings out between z 4.42 and 4.62. Built
#   SYMMETRIC from the taller petal (front image-right / back image-right / side): the sheet draws the two petals at
#   different heights per side, swapped between the front and back views (deviation, reported).
PETAL_INNER = [(1.50, 1.20, 2.31), (1.18, 1.00, 2.40), (0.70, 0.78, 2.62), (0.36, 0.58, 2.92), (0.16, 0.42, 3.30),
               (0.14, 0.32, 3.75), (0.20, 0.25, 4.15), (0.42, 0.22, 4.42), (0.70, 0.25, 4.62), (0.86, 0.28, 4.85),
               (0.90, 0.27, 5.10), (0.86, 0.20, 5.38), (0.64, -0.04, 5.62)]
PETAL_OUTER = [(1.50, 1.20, 2.31), (1.78, 1.40, 2.42), (2.02, 1.54, 2.89), (2.15, 1.61, 3.39), (2.18, 1.62, 3.89),
               (2.13, 1.52, 4.39), (1.98, 1.33, 4.89), (1.72, 1.05, 5.35), (1.40, 0.72, 5.68), (1.10, 0.45, 5.84),
               (0.88, 0.25, 5.89), (0.72, 0.08, 5.80), (0.64, -0.04, 5.62)]
PETAL_CUP_DIR = (-0.35, 0.94, 0.0)    # the cup bulges this way (back-inward): the concave inner face looks front-out
PETAL_CUP = 0.20                      # "petal cup depth"
PETAL_T = 0.020                       # "petal thickness" (half, mid-sheet)
RIM_BEAD = 0.030                      # "rolled rim": extra half-thickness along the cream rim
CURL_THICK = 1.2                      # the top curl thickens by this share (a rolled lip)
PETAL_NS = 36                         # "petal detail": rows from the bottom tip to the top hook
U_KNOTS = [0.0, 0.0833, 0.1667, 0.25, 0.294, 0.306, 0.3667, 0.4333, 0.5, 0.544, 0.556, 0.6167, 0.6833, 0.75, 0.774,
           0.786, 0.8333, 0.9167, 1.0]   # green-span columns (inner edge -> rim); the narrow ones are the three VEINS
VEIN_COLS = (4, 9, 14)                # the narrow vein columns (index of the cell)
V_KNOTS = [0.0, 0.4, 0.75, 1.0]       # rim-span columns
RIM_FRAC = [(0.0, 0.25), (0.06, 0.14), (0.25, 0.08), (0.45, 0.05), (0.60, 0.08), (0.72, 0.16), (0.82, 0.30),
            (0.88, 0.45), (1.0, 0.60)]   # "cream rim width" along the petal (s, share of the row): thin mid-edge, wide
                                      #   toward the top (sheet: 3-11 px at the outer middle, 20-40 px near the curl)
S_CURL = 0.885                        # rows above this are the cream top curl (both faces)
S_BOT = 0.04                          # rows below this are the cream bottom tip
S_VEIN = (0.14, 0.80)                 # the veins run between these rows
INNER_BANDS = [(3.0, "petal_light"), (3.4, "petal_light2"), (4.0, "petal_mid"), (5.0, "petal_mid2"),
               (99.0, "petal_dark")]  # "inner face shading": row z (green-span midpoint) upper limits -> band
BACK_SHADE_Z = 3.0                    # back face rows below this take the back shade
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig
TAIL_BONES = 5
OWN_TAU = 0.04                        # soft SDF ownership between head / neck / torso / tail (weights)
NECK_BAND, HEAD_BAND = (3.70, 3.95), (4.10, 4.35)   # spine -> neck -> head weight bands (neck group)
PETAL_W = (-0.02, 0.25)               # petal weight: spine inside the torso -> petal bone this far outside it
# ---- clips (PLACEHOLDER: the artist has not given the movement; brief: idle = slow sway + hood flex, walk = a slither
#   wave down the tail chain with the tail tip planted). 24 fps; integer harmonics per loop -> frame N+1 == frame 1.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_TAIL_YAW = (0.0, 0.8, 1.0, 1.2, 1.2)   # "sway": per tail bone (tip -> body) yaw deg, 1 cyc, lag below
IDLE_LAG = 0.5                        # rad per bone (the sway travels body -> tip)
IDLE_ROLL, IDLE_PITCH = 2.0, 1.0      # spine roll (1 cyc) / pitch (2 cyc) deg
IDLE_NOD = 1.5                        # head nod deg (2 cyc)
HOOD_FLEX = (4.0, 2)                  # "hood flex": petal open / close deg about its root hinge, cycles per loop
WALK_N = 48                           # "slither loop": 2 s
WALK_TAIL_YAW = (0.0, 4.0, 5.0, 5.0, 4.0)   # "slither": per tail bone yaw deg (tail.001 = the planted tip = 0)
WALK_LAG = 0.9                        # rad per bone: the wave travels down the chain (body -> tip)
WALK_COUNTER = 0.85                   # the spine undoes this share of the summed tail yaw (the head keeps facing ahead)
WALK_ROLL = 1.5                       # spine roll deg (1 cyc)
WALK_HOOD = (2.0, 2)                  # hood flex during the slither

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
          "design/reference/petal_cobra-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; three true orthos; floor z = 0 at the resting tail tip",
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


def mir(p, sg):
    return np.asarray(p, float) * np.array([sg, 1.0, 1.0])


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body SDF
t_sdf = time.time()
def _tab(T):
    """a measured (key, value) table -> a dense SMOOTH profile (Catmull-Rom through the knots): linear interpolation
    between knots leaves a crease at every knot, which flat shading shows as horizontal stripes (v0 render)"""
    d = SD.catmull(np.array(T, float), 12)
    o = np.argsort(d[:, 0], kind="stable")
    return d[o, 0], d[o, 1]


HW_Z, HW_V = _tab(TORSO_HW)
YC_Z, YC_V = _tab(TORSO_YC)
HD_Z, HD_V = _tab(TORSO_HD)
TOP_X, TOP_Z = _tab(TORSO_TOP)


def torso_yc(z):
    return np.interp(z, YC_Z, YC_V)


def sd_head(P):
    Q = P.copy()
    Q[:, 1] = P[:, 1] - HEAD_SHEAR * (P[:, 2] - HEAD_C[2])
    return SD.sd_ellipsoid(Q, HEAD_C, HEAD_R) / math.sqrt(1.0 + HEAD_SHEAR ** 2)


def sd_neck(P):
    return SD.sd_round_cone(P, NECK[0], NECK[2], NECK[1], NECK[3])


def sd_torso_core(P):
    """super-elliptic section (half-width hw(z), half-depth hd(z), centre yc(z)), first-order distance f / |grad f|,
    capped by the top edge z_top(|X|) (the shoulder corners) and a bottom plane (hidden inside the tail blend)"""
    z = P[:, 2]
    zc = np.clip(z, HW_Z[0], HW_Z[-1])
    hw = np.interp(zc, HW_Z, HW_V); hd = np.interp(zc, HD_Z, HD_V); yc = np.interp(zc, YC_Z, YC_V)
    qx = np.abs(P[:, 0]) / hw; qy = np.abs(P[:, 1] - yc) / hd
    p = TORSO_P
    r = np.maximum((qx ** p + qy ** p) ** (1.0 / p), 1e-9)
    gx = qx ** (p - 1) / hw; gy = qy ** (p - 1) / hd
    g = np.maximum(r ** (1.0 - p) * np.sqrt(gx * gx + gy * gy), 1.0 / np.maximum(hw, hd))
    d_side = (r - 1.0) / g
    ax = np.abs(P[:, 0])
    zt = np.interp(ax, TOP_X, TOP_Z)
    sl = (np.interp(ax + 1e-3, TOP_X, TOP_Z) - np.interp(np.maximum(ax - 1e-3, 0.0), TOP_X, TOP_Z)) / 2e-3
    d_top = (z - zt) / np.sqrt(1.0 + sl * sl)
    d = SD.smax(d_side, d_top, TORSO_TOP_K)
    return SD.smax(d, HW_Z[0] - z, TORSO_BOT_K)


def sd_nubs(P):
    return np.minimum(*[SD.sd_ellipsoid(P, mir(NUB_C, sg), NUB_R) for _, sg in SIDES])


def sd_torso(P):
    return SD.smin(sd_torso_core(P), sd_nubs(P), NUB_K)


_tk = np.array([k for k, _ in TAIL_KNOTS]); _tr = np.array([r for _, r in TAIL_KNOTS])
_td = SD.catmull(np.hstack([_tk, _tr[:, None]]), TAIL_SUB)          # smooth centreline + radius together
TAIL_PTS, TAIL_RAD = _td[:, :3], np.maximum(_td[:, 3], 0.012)
TAIL_SEGS = list(zip(TAIL_PTS[:-1], TAIL_PTS[1:], TAIL_RAD[:-1], TAIL_RAD[1:]))
_SQ = np.array([1.0, 1.0 / TAIL_SQ, 1.0])


def sd_tail_seg(P, a, b, ra, rb):
    """a round cone squashed in Y about its own midpoint (elliptic section: X ra..rb, Y TAIL_SQ of it); consecutive
    spans share their end ellipsoids, so the sweep stays seamless"""
    m = 0.5 * (a + b)
    return SD.sd_round_cone(m + (P - m) * _SQ, m + (a - m) * _SQ, m + (b - m) * _SQ, ra, rb) * TAIL_SQ


def sd_tail(P):
    d = np.full(len(P), 1e9)
    for a, b, ra, rb in TAIL_SEGS:
        d = np.minimum(d, sd_tail_seg(P, a, b, ra, rb))
    return d


PART_SDF = {"head": sd_head, "neck": sd_neck, "torso": sd_torso, "tail": sd_tail}
GROUPS = list(PART_SDF)
_lo = np.array([-1.72, -1.45, -0.03]); _hi = np.array([0.98, 1.05, 5.20])
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
g.apply(sd_torso_core, (-0.90, -0.50, 2.30), (0.90, 1.00, 4.97), 0.0)
g.apply(sd_nubs, mir(NUB_C, -1) - NUB_R, mir(NUB_C, 1) + NUB_R, NUB_K)
g.apply(sd_neck, (-0.33, -0.70, 3.30), (0.33, 0.21, 4.62), TORSO_K)
_hc = np.array(HEAD_C); _hr = np.array(HEAD_R) + np.array([0.0, HEAD_SHEAR * HEAD_R[2], 0.0])
g.apply(sd_head, _hc - _hr, _hc + _hr, NECK_K)
# the tail: the spans reaching above TAIL_K_Z are ONE exact union smooth-blended into the torso once (per-span smooth
# unions inflate the overlaps: a ring at the k / no-k boundary in the v0 render); the rest join by exact min (k 0)
_up = [sg_ for sg_ in TAIL_SEGS if max(sg_[0][2], sg_[1][2]) > TAIL_K_Z]
_dn = [sg_ for sg_ in TAIL_SEGS if max(sg_[0][2], sg_[1][2]) <= TAIL_K_Z]


def sd_tail_up(P):
    d = np.full(len(P), 1e9)
    for a, b, ra, rb in _up:
        d = np.minimum(d, sd_tail_seg(P, a, b, ra, rb))
    return d


_ul = np.min([np.minimum(a, b) - max(ra, rb) for a, b, ra, rb in _up], 0)
_uh = np.max([np.maximum(a, b) + max(ra, rb) for a, b, ra, rb in _up], 0)
g.apply(sd_tail_up, _ul, _uh, TAIL_K)
for a, b, ra, rb in _dn:
    g.apply(lambda P, a=a, b=b, ra=ra, rb=rb: sd_tail_seg(P, a, b, ra, rb),
            np.minimum(a, b) - max(ra, rb), np.maximum(a, b) + max(ra, rb), 0.0)
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


# ---- feature fields (front-projected, gated to the front surface they are drawn on)
def eye_field(P, sg, grow=0.0):
    f = FP.front_field(P, sg * EYE_X, EYE_Z, EYE_AB[0], EYE_AB[1], sg * EYE_TILT, sharp=1.0, grow=grow)
    return np.where(P[:, 1] < HEAD_C[1], f, -1.0)


def mark_field(P, k, sg, grow=0.0):
    x, z, a, b, tilt = MARKS[k]
    f = FP.front_field(P, sg * x, z, a, b, sg * tilt, grow=grow)
    return np.where(P[:, 1] < torso_yc(P[:, 2]) - 0.05, f, -1.0)


def smile_field(P, grow=0.0):
    f = FP.curve_field(P, SMILE, SMILE_HW, grow=grow)
    return np.where(P[:, 1] < -0.6, f, -1.0)


FEATURES = {}
for s, sg in SIDES:
    FEATURES["eye." + s] = (lambda P, grow=0.0, sg=sg: eye_field(P, sg, grow), "eye")
for k in range(len(MARKS)):
    for s, sg in SIDES:
        FEATURES["mark%d.%s" % (k + 1, s)] = (lambda P, grow=0.0, k=k, sg=sg: mark_field(P, k, sg, grow), "marking")
FEATURES["smile"] = (smile_field, "mouth")

# ---- feature densify: the decimated shell's ~0.07 edges would cut a 0.09-tall marking into a diamond. Subdivide the
#      faces round each feature (FACE_SUBDIV rounds; SMILE_SUBDIV round the thin smile), then pull every new vertex
#      onto the HIGH.
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
for rnd in range(max(FACE_SUBDIV, SMILE_SUBDIV)):
    names = [n for n in FEATURES if (rnd < SMILE_SUBDIV if n == "smile" else rnd < FACE_SUBDIV)]
    co = np.array([v.co[:] for v in bm.verts])
    inz = np.zeros(len(co), bool)
    for n in names:
        inz |= FEATURES[n][0](co, grow=FACE_ZONE) > 0.0
    bm.verts.index_update()
    zf = [f for f in bm.faces if any(inz[v.index] for v in f.verts)]
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
report["retopo"]["feature_densify"] = {"rounds": FACE_SUBDIV, "smile_rounds": SMILE_SUBDIV, "zone": FACE_ZONE,
                                       "new_verts": len(LV) - _nv0, "tris_after": tri_count_F(LF)}
print("RETOPO", json.dumps(report["retopo"]))

# =========================================================================== 3. region fields + iso cuts on the shell
FIELDS = {n: fn(LV) for n, (fn, _) in FEATURES.items()}
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k.replace(".", "_")) for k in FIELDS}
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


t = time.time()
cut_log = [iso_cut(k_, 0.0) for k_ in FEATURES]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
shell_reg = np.full(len(CF), "body", dtype=object)
feat_faces = {}
for n, (_, reg) in FEATURES.items():
    m_ = FVAL[n] > 0.0                       # each feature paints ONLY where its OWN field is inside (no borrowing)
    shell_reg[m_] = reg
    feat_faces[n] = int(m_.sum())
empty = sorted(n for n, c in feat_faces.items() if c == 0)
assert not empty, "a feature region is empty (it would borrow a neighbour's paint): %s" % empty
for reg in ("body", "eye", "marking", "mouth"):
    assert np.any(shell_reg == reg), "shell region %s is empty" % reg
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF),
                      "feature_faces": feat_faces}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 4. parts: the two hood petals (closed pillows)
NCUP = unit(PETAL_CUP_DIR)


def resample_by_arc(C, n):
    al = SD.arclen(C)
    tt = np.linspace(0.0, al[-1], n)
    return np.stack([np.interp(tt, al, C[:, k]) for k in range(3)], 1)


_rf_s = np.array([a for a, _ in RIM_FRAC]); _rf_v = np.array([b for _, b in RIM_FRAC])
JR = len(U_KNOTS) - 1                  # first rim column


def build_petal():
    """the L petal (+X): loft rows from the inner edge I(s) to the outer rim O(s), s by arc length from the bottom tip B
    to the top hook H; columns: the green span [0, 1 - rf(s)] at U_KNOTS (three narrow vein columns), the rim span
    [1 - rf(s), 1] at V_KNOTS -- so the cream rim boundary and the veins are exact grid lines (clean, never stair-
    stepped). Cup: the interior bulges back-inward (NCUP); thickness along the grid's own normals."""
    I = resample_by_arc(SD.catmull(np.array(PETAL_INNER), 12), PETAL_NS + 1)
    O = resample_by_arc(SD.catmull(np.array(PETAL_OUTER), 12), PETAL_NS + 1)
    ss = np.linspace(0.0, 1.0, PETAL_NS + 1)
    rf = np.interp(ss, _rf_s, _rf_v)
    U = np.array(U_KNOTS); Vk = np.array(V_KNOTS)
    T = np.hstack([U[None, :] * (1.0 - rf)[:, None], (1.0 - rf)[:, None] + rf[:, None] * Vk[None, 1:]])
    P = I[:, None, :] * (1.0 - T)[..., None] + O[:, None, :] * T[..., None]
    env = np.sin(math.pi * ss) ** 0.7
    P = P + NCUP[None, None, :] * (PETAL_CUP * np.sin(math.pi * T) * env[:, None])[..., None]
    N = FP.grid_normals(P, NCUP)
    tc = 1.0 - rf / 2.0
    bead = RIM_BEAD * np.exp(-((T - tc[:, None]) / (rf[:, None] / 2.0 + 1e-3)) ** 2)
    curl = 1.0 + CURL_THICK * smoothstep(S_CURL - 0.12, 1.0, ss)
    Hh = (PETAL_T + bead) * curl[:, None] * np.sin(math.pi * T) ** 0.5 * (np.sin(math.pi * ss) ** 0.3)[:, None]
    zrow = np.array([0.25 * (P[i, 0, 2] + P[i, JR, 2] + P[i + 1, 0, 2] + P[i + 1, JR, 2]) for i in range(PETAL_NS)])
    srow = 0.5 * (ss[:-1] + ss[1:])

    def region_fn(i, j, top):
        if j >= JR or srow[i] > S_CURL or srow[i] < S_BOT:
            return "petal_rim"
        vein = j in VEIN_COLS and S_VEIN[0] < srow[i] < S_VEIN[1]
        if top:                                          # +N = the back face (back-inward, convex)
            if vein:
                return "petal_back_vein"
            return "petal_back_shade" if zrow[i] < BACK_SHADE_Z else "petal_back"
        band = next(nm for zl, nm in INNER_BANDS if zrow[i] < zl)
        if vein and band != "petal_dark":
            return "petal_vein"
        return band
    V, F, R, IJ = FP.pillow(P, N, Hh, region_fn)
    return V, F, R, IJ, {"inner_len": float(SD.arclen(I)[-1]), "rim_len": float(SD.arclen(O)[-1]),
                         "row_z": zrow.round(3).tolist(), "s_rows": srow.round(3).tolist()}


PARTS = []
_pV, _pF, _pR, _pIJ, PETAL_INFO = build_petal()
for s, sg in SIDES:
    if sg > 0:
        V, F = _pV, _pF
    else:
        V, F = _pV * np.array([-1.0, 1.0, 1.0]), [f[::-1] for f in _pF]   # mirror (winding flipped: still outward)
    p = FP.Part("petal." + s, V, F, list(_pR), bone="petal_" + s)
    PARTS.append(p)
report["petals"] = {"rows": PETAL_NS, "cols": len(U_KNOTS) - 1 + len(V_KNOTS) - 1, "tris_each": PARTS[0].tris(),
                    "inner_edge_len": round(PETAL_INFO["inner_len"], 3), "rim_len": round(PETAL_INFO["rim_len"], 3),
                    "row_z": PETAL_INFO["row_z"], "rule": "closed pillow (knife-thin perimeter, welded tips), L built, "
                    "R mirrored (symmetric by the brief)"}
_petal_reg = {r: int(sum(1 for x in _pR if x == r)) for r in sorted(set(_pR))}
for r in ("petal_light", "petal_light2", "petal_mid", "petal_mid2", "petal_dark", "petal_vein", "petal_back",
          "petal_back_shade", "petal_back_vein", "petal_rim"):
    assert _petal_reg.get(r, 0) > 0, "petal region %s is empty: %s" % (r, _petal_reg)
report["petals"]["faces_per_region"] = _petal_reg

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
_shell_lo = float(CV[:, 2].min())
_below = [i[0] for i in ISL if i[1][:, 2].min() < _shell_lo - 1e-3]
assert not _below, "a part dips below the floor (the tail tip's rest z %.4f): %s" % (_shell_lo, _below)
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
assert float(VALL[:, 2].min()) >= -1e-9, "floor"
HV_s = HV - SHIFT
REG = ["body", "eye", "marking", "mouth", "petal_light", "petal_light2", "petal_mid", "petal_mid2", "petal_dark",
       "petal_vein", "petal_back", "petal_back_shade", "petal_back_vein", "petal_rim"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "declared palette regions with no faces: %s" % _empty


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
    mat.use_backface_culling = True                  # opaque closed pieces: single-sided (glTF doubleSided false)
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
report["glow"] = {"skin": "default", "glowing_regions": _lit, "rule": "no glow (the sheet draws none): zero Glow"}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = jitter(VALL, FALL)
for r in ("eye", "marking", "mouth"):
    shade[rid == REG.index(r)] = 1.0                 # the face + markings stay clean (no value jitter)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=6.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


# facing landmark: the head centre -> the snout's front point on the midline
SNOUT_FRONT, _ = cast((0.0, -3.0, 4.19), (0, 1, 0))
anchor = np.array(HEAD_C) - SHIFT
landmark = SNOUT_FRONT - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the snout front on the midline (the face looks -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = {"eyes": {"x": EYE_X, "z": EYE_Z, "half_ab": list(EYE_AB), "tilt_deg": EYE_TILT,
                           "rule": "front-projected tilted almond iso-cut into the head (closed shell, no decal)"},
                  "smile": {"stroke": SMILE, "half_width": SMILE_HW,
                            "rule": "front-projected thin stroke iso-cut into the snout front (ink tone)"},
                  "markings": [{"x": m_[0], "z": m_[1], "half_ab": [m_[2], m_[3]], "tilt_deg": m_[4]} for m_ in MARKS]}

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
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF),
                  "petals": sum(p.tris() for p in PARTS),
                  "per_part": {"shell (head + neck + torso + nubs + tail, one SDF; face + markings cut in)": tri_count_F(CF),
                               **{p.name: p.tris() for p in PARTS}},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The one SDF shell carries the head, the torso "
                            "panel with its shoulder corners + nubs and the long tail; the face + marking outlines are "
                            "densified locally; the two cupped hood petals (closed pillows with clean rim + vein grid "
                            "lines) are the other big share." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (SDF shell; petal pillows); eyes / smile / markings are "
                                "iso-cut regions of the shell"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"front": "H 5.90 W 4.39 (x 32-471)", "side": "H 5.90 D 3.02 (x 862-1164)",
                               "back": "H 5.77 W 3.66"},
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
low["conquest_source"] = "from scratch: design/reference/petal_cobra-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_pl = VALL[RANGE["petal.L"][0]:RANGE["petal.L"][1]]
_tl = TAIL_PTS - SHIFT
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.70, -1.15, 3.75]) - SHIFT).tolist(), (np.array([0.70, 0.0, 5.20]) - SHIFT).tolist()],
    "hood_petal": [_pl.min(0).tolist(), _pl.max(0).tolist()],
    "torso": [(np.array([-0.95, -0.6, 2.40]) - SHIFT).tolist(), (np.array([0.95, 0.3, 4.15]) - SHIFT).tolist()],
    "tail": [(_tl.min(0) - 0.4).tolist(), (_tl.max(0) + 0.4).tolist()],
    "views": {"face": [0.0, 4.0], "hood_petal": [25.0, 8.0], "torso": [0.0, 3.0], "tail": [-20.0, 10.0]},
    "axis_xy": [float((PINNED_AXIS_PX - FRONT_AXIS_PX) / 100.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow")}, default=str))
    print("PETALS", json.dumps({k: v for k, v in report["petals"].items()}))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + petals)
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
body_faces = np.ones(nf, bool)
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + both closed petal pillows",
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


# tail chain from the planted TIP up to the torso: tail.001 starts at the tip
_tail_up = TAIL_PTS[::-1]                                   # tip -> top
_tail_al = SD.arclen(_tail_up)
_top_i = int(np.argmin(np.linalg.norm(_tail_up - np.array(TAIL_KNOTS[1][0]), axis=1)))   # the tail's top (z 2.29)
TAIL_L = float(_tail_al[_top_i])


def tail_point(s_):
    return np.array([np.interp(s_, _tail_al, _tail_up[:, k]) for k in range(3)])


TAIL_BPTS = [tail_point(TAIL_L * k / TAIL_BONES) for k in range(TAIL_BONES + 1)]
SPINE_TOP = np.array([0.0, -0.05, 3.85])
NECK_TOP = np.array([0.0, -0.30, 4.30])
HEAD_TOP = np.array([0.0, -0.42, 5.12])
BONES = []
for k in range(TAIL_BONES):
    BONES.append(("tail.%03d" % (k + 1), P_(TAIL_BPTS[k]), P_(TAIL_BPTS[k + 1]), "root" if k == 0 else "tail.%03d" % k))
BONES += [("spine", P_(TAIL_BPTS[-1]), P_(SPINE_TOP), "tail.%03d" % TAIL_BONES),
          ("neck", P_(SPINE_TOP), P_(NECK_TOP), "spine"), ("head", P_(NECK_TOP), P_(HEAD_TOP), "neck")]
PETAL_ROOT = np.array([0.18, 0.33, 3.75]); PETAL_TIPB = np.array([1.55, 1.15, 4.15])
for s, sg in SIDES:
    BONES.append(("petal_" + s, P_(mir(PETAL_ROOT, sg)), P_(mir(PETAL_TIPB, sg)), "spine"))
CONNECT = {"tail.%03d" % (k + 1) for k in range(1, TAIL_BONES)} | {"spine", "neck", "head"}
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


def chain_hat(s_, mids):
    """arc-length hat weights over a chain with bone midpoints 'mids': below the first midpoint = bone 0, above the
    last = the last bone, linear between neighbours"""
    n = len(mids)
    W = np.zeros((len(s_), n))
    k = np.clip(np.searchsorted(mids, s_) - 1, 0, n - 2)
    u = np.clip((s_ - np.asarray(mids)[k]) / (np.asarray(mids)[k + 1] - np.asarray(mids)[k]), 0.0, 1.0)
    W[np.arange(len(s_)), k] = 1.0 - u
    W[np.arange(len(s_)), k + 1] += u
    return W


# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
GD = np.stack([PART_SDF[gn](Pw) for gn in GROUPS], 1)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
zz = Pw[:, 2]
Ws = np.zeros((b_ - a_, len(DEFORM)))
Ws[:, J["head"]] += Wg[:, GROUPS.index("head")]
wn = smoothstep(NECK_BAND[0], NECK_BAND[1], zz); whd = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
gi = GROUPS.index("neck")
Ws[:, J["spine"]] += Wg[:, gi] * (1 - wn)
Ws[:, J["neck"]] += Wg[:, gi] * wn * (1 - whd)
Ws[:, J["head"]] += Wg[:, gi] * wn * whd
Ws[:, J["spine"]] += Wg[:, GROUPS.index("torso")]
_, s_arc, _ = FP.spine_param(Pw, _tail_up)
TAIL_MIDS = [TAIL_L * (k + 0.5) / TAIL_BONES for k in range(TAIL_BONES)] + [TAIL_L + 0.5 * float(np.linalg.norm(SPINE_TOP - TAIL_BPTS[-1]))]
Wv = chain_hat(s_arc, TAIL_MIDS)
gi = GROUPS.index("tail")
for k in range(TAIL_BONES):
    Ws[:, J["tail.%03d" % (k + 1)]] += Wg[:, gi] * Wv[:, k]
Ws[:, J["spine"]] += Wg[:, gi] * Wv[:, TAIL_BONES]
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
    Pp = VALL[a_:b_] + SHIFT
    wp = smoothstep(PETAL_W[0], PETAL_W[1], sd_torso(Pp))
    Wt[a_:b_, J[p.bone]] = wp
    Wt[a_:b_, J["spine"]] += 1.0 - wp
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between head, neck, torso (+ nubs), tail; neck spine -> "
                          "neck -> head by z bands; torso = spine; tail = arc-length hats from the planted tip "
                          "(tail.001) up to the spine; petals: spine inside the torso -> petal bone by smoothstep%s of "
                          "the torso SDF" % (OWN_TAU, str(PETAL_W))}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame, keyed from the stored channels
#      (frame N+1 = frame 1), measured as the checker plays them
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM


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


# hood hinge: the petal's inner (root) edge direction, lower root -> upper root (L); R = the mirror (axis x negated,
# angle negated: a mirror-symmetric open / close)
_hinge = unit(np.array(PETAL_INNER[6]) - np.array(PETAL_INNER[4]))
HINGE = {"L": (tuple(_hinge), 1.0), "R": (tuple(_hinge * np.array([-1.0, 1.0, 1.0])), -1.0)}


def hood(P, u, spec):
    amp, cyc = spec
    ang = amp * math.sin(TAU * cyc * u)
    for s, _ in SIDES:
        ax, sgn = HINGE[s]
        P["petal_" + s] = (None, [(ax, sgn * ang)], None)


def idle_pose(u):
    P = {}
    yaws = []
    for k in range(TAIL_BONES):                             # the sway travels from the body (k high) down to the tip
        a = IDLE_TAIL_YAW[k] * math.sin(TAU * u - (TAIL_BONES - 1 - k) * IDLE_LAG)
        yaws.append(a)
        P["tail.%03d" % (k + 1)] = (None, [("Z", a)], None)
    roll = IDLE_ROLL * math.sin(TAU * u + 0.3)
    pitch = IDLE_PITCH * math.sin(TAU * 2 * u)
    P["spine"] = (None, [("Z", -0.5 * sum(yaws)), ("Y", roll), ("X", pitch)], None)
    P["neck"] = (None, [("Y", -0.5 * roll), ("X", -0.5 * pitch)], None)
    P["head"] = (None, [("Y", -0.3 * roll), ("X", IDLE_NOD * math.sin(TAU * 2 * u - 0.9))], None)
    hood(P, u, HOOD_FLEX)
    return P, {"roll": roll}


def walk_pose(u):
    """PLACEHOLDER slither: a yaw wave travels down the tail chain from the body to the planted tip (tail.001 = 0, so
    the tip never slides); the spine undoes most of the summed yaw so the head keeps facing ahead; small roll."""
    P = {}
    yaws = []
    for k in range(TAIL_BONES):
        a = WALK_TAIL_YAW[k] * math.sin(TAU * u - (TAIL_BONES - 1 - k) * WALK_LAG)
        yaws.append(a)
        P["tail.%03d" % (k + 1)] = (None, [("Z", a)], None)
    roll = WALK_ROLL * math.sin(TAU * u + 0.6)
    P["spine"] = (None, [("Z", -WALK_COUNTER * sum(yaws)), ("Y", roll)], None)
    P["neck"] = (None, [("Y", -0.6 * roll)], None)
    P["head"] = (None, [("Y", -0.3 * roll)], None)
    hood(P, u, WALK_HOOD)
    return P, {"roll": roll}


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def apply_pose(P):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        loc, rots, sc = P.get(bn, (None, [], None))
        pb.location = Vector(REST[bn].T @ (np.zeros(3) if loc is None else np.asarray(loc, float)))
        pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
        pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)


def bake_clip(name, N, pose_fn):
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames = []
    for f in range(N):
        P, _ = pose_fn(f / N)
        apply_pose(P)
        frames.append({bn: (rig.pose.bones[bn].location.copy(), rig.pose.bones[bn].rotation_quaternion.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
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
    return act, rows


CLIPS = {"idle": (IDLE_N, idle_pose), "walk": (WALK_N, walk_pose)}
NEW_ACTS, key_rows = {}, []
t_clip = time.time()
for cn, (N, fn) in CLIPS.items():
    NEW_ACTS[cn], rows = bake_clip(cn, N, fn)
    key_rows += rows
DIG["keys"] = sha(np.array(key_rows))
TIPV = np.nonzero(Wt[:, J["tail.001"]] > 0.5)[0]
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
    rest_tip = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "tip": C[TIPV].mean(0).tolist(),
                     "head_tail": list(pbs["head"].tail), "petal_L": list(pbs["petal_L"].tail),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    tip = np.array([r["tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_])
    pl = np.array([r["petal_L"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "tail_tip_p2p_xyz": (tip.max(0) - tip.min(0)).round(5).tolist(),
                    "head_top_p2p_xyz": (hd.max(0) - hd.min(0)).round(4).tolist(),
                    "petal_L_tip_p2p_xyz": (pl.max(0) - pl.min(0)).round(4).tolist()}
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
rig["conquest_rig"] = ("petal_cobra v1: root > tail.001 (planted tip) > ... > tail.%03d > spine > neck > head; "
                       "spine > petal_L / petal_R" % TAIL_BONES)
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s slow sway (a yaw "
                               "wave down the tail, spine roll %.1f deg, head nod) + hood flex (+-%.0f deg about each "
                               "petal's root hinge, 2 cyc). walk = 2 s in-place slither: a yaw wave travels down the tail "
                               "chain to the planted tip (tail.001 still), the spine counters it so the head faces ahead. "
                               "Root still." % (IDLE_ROLL, HOOD_FLEX[0]))
low["conquest_locomotion"] = "PLACEHOLDER: in-place slither, tail tip planted (movement intent open; the game glides the unit)"
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
              "structure": "armature identity + skinned 'petal_cobra' (one opaque body material); natural scale; "
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
