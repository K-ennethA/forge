"""Winged Cat -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/winged_cat_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x4: Soot Puff, Winged Cat, Phoenix Hatchling, Chest Monster (artist
sheets)" (section WINGED CAT) + the sheet design/reference/winged_cat-character-sheet.png (intake:
design/reference/winged_cat/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The sheet's FRONT is drawn three-quarter, so every constant is
measured off the two TRUE orthos (intake ortho_frame.views = back, side); the front + the face-detail panel are used
only for what the orthos cannot show (eye spacing, mouth shape):
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 972.5) / 100 ; z = (697 - y_px) / 100
  BACK view (the unit's right on image right):          X = -(x_px - 619.5) / 100 ; z = (699 - y_px) / 100
(the intake floor lines; the side axis is the intake's 972.5; the back view's CHARACTER centre is x 619.5 (head
517-722 @ y 440, haunches 515-728 @ y 630) -- 2.5 px left of the intake's bbox axis 622, see BACK_AXIS_OFF).
X +ve = the unit's LEFT (L).

A SITTING CAT: the rest pose IS the sheet's sit (haunches + hind feet on the floor, two upright front legs).

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the TORSO (an X-stretched round cone, chest up front,
     pelvis low at the back), two HAUNCHES + flat HIND FEET, two FRONT LEGS with round PAWS (toe creases carved), the
     NECK, the big round HEAD with a carved open-MOUTH pocket, two pointed EARS (flattened round cones), the TAIL ROOT
     stub and the fluffy COLLAR (a lumpy elliptical torus round the neck with a fine fuzz noise -- the fuzz reaches the
     HIGH and the bake; the decimated LOW keeps the lumps). Marching tets = the HIGH (bake source); collapse decimation
     = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: collar ownership, the cream INNER EAR (an inset triangle on each
     ear's front face), the MOUTH interior (the carved pocket's floor).
  3. PARTS (winged_cat_parts.py, closed): almond EYES + vertical slit PUPILS (closed conforming decals), two FANG cones,
     two leaf / bat WINGS (closed pillow sheets with a scalloped trailing edge, dark-green root + vein strips), the
     broom / fan TAIL (TAIL_N closed tapered pillow blades fanning back and down from the rump, layered, jagged ends,
     a darker line strip on each blade's overlapped edge).
  4. palette (palettes.py regions -> Col / Glow, no glow), Smart UV, bake normal + AO from the SDF high + parts, seed 0.
  5. RIG: root > spine > neck > head > ear_L / ear_R; spine > wing_L.001 > wing_L.002 (+ R); spine > tail.001 >
     tail.002 > tail.003; spine > foreleg_L / foreleg_R. Clips (PLACEHOLDER, movement intent open): idle (breathing +
     wing flutter + tail swish) + walk (sitting gentle weight-shift). glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K             # noqa: E402  (read-only use)
import palettes as PAL         # noqa: E402  (read-only use)
import magmoo_sdf as SD        # noqa: E402  (read-only use: the SDF kit)
import winged_cat_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "winged_cat"
CHAR_ID = "winged_cat"                # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.22                       # >= the largest smooth-union k (HAUNCH_K 0.14) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 9000                      # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
BACK_AXIS_OFF = -0.025                # the intake's back axis (x 622) sits 2.5 px right of the character centre (x 619.5)
# ---- HEAD (big, round). SIDE view: face front x 808-812 @ y 455-470 (Y -1.63), crown y ~360 @ x 900-940 (z 3.37), back
#   of the head x ~1000 @ y 425-460 (Y 0.27), chin under the collar ~y 540 (z 1.57). BACK view: widest x 511-723 @ y
#   440-450 (outline-incl. half 1.06, line centre ~1.03), crown y 347 @ x 617 (z 3.52) -> the crown is taken between the
#   two views (3.45)
HEAD_C, HEAD_R, HEAD_K = (0.0, -0.69, 2.50), (1.03, 0.95, 0.95), 0.10   # "head size" (half width / depth / height)
NECK = ((0.0, -0.30, 1.30), (0.0, -0.55, 1.95), 0.42, 0.40)   # (a, b, r_a, r_b): hidden under the head + collar
NECK_K = 0.12
# ---- TORSO: an X-stretched round cone (pelvis low / back -> chest high / front). SIDE view chest front x 880-890 @ y 600
#   (Y -0.9), back line (1000,542) -> (1040,573) -> (1060,602) -> rump x 1068 @ y 640 (Y 0.96, incl. the haunch); BACK
#   view under the collar x 537-703 @ y 560-580 (half 0.82, z 1.2-1.4)
TORSO = ((0.0, 0.15, 0.62), (0.0, -0.30, 1.35), 0.62, 0.52)   # (pelvis centre, chest centre, radii)
TORSO_SX = 1.50                       # "body width": the torso's X stretch (back view shoulders +-0.78 at z 1.35)
# ---- HAUNCHES + HIND FEET. BACK view widest x 515-728 @ y 620-640 (half 1.06, z 0.6-0.8), x 522-722 @ y 600 (z 1.0);
#   SIDE view thigh arc centre ~(1020, 640) r ~0.45, rump x 1068; hind foot x 980-1050 @ y 670-697 (Y 0.08-0.78), BACK view
#   hind feet x 525-560 / 680-712 at the floor (X +-0.8)
HAUNCH_C, HAUNCH_R, HAUNCH_K = (0.55, 0.42, 0.62), (0.50, 0.55, 0.60), 0.14
HINDFOOT_C, HINDFOOT_R, FOOT_K = (0.74, 0.43, 0.11), (0.21, 0.36, 0.13), 0.08
# ---- FRONT LEGS + PAWS. SIDE view leg front edge x 878-888 @ y 600-690 (Y -0.88), paws y 660-697 with the toe front at
#   x ~873; FRONT (3/4) two legs side by side, each ~0.5 wide -> X +-0.38
LEG_KNOTS = [(0.36, -0.55, 1.15), (0.38, -0.68, 0.25)]   # shoulder -> wrist (L side)
LEG_R = (0.24, 0.19)                  # "front leg thickness" (shoulder, wrist)
LEG_K = 0.12
PAW_C, PAW_R, PAW_K = (0.38, -0.74, 0.12), (0.21, 0.26, 0.14), 0.07   # "paw size": round paws, floor-cut
CREASES = (-0.07, 0.07)               # "toe creases": 2 grooves across each paw / hind-foot front (3 toes, sheet ink lines)
CREASE_R, CREASE_K = 0.022, 0.015
# ---- TAIL ROOT stub on the rump (the blades grow from TAIL_ROOT). SIDE view the fan leaves the body at x 1065-1072,
#   y 580-640 (Y 0.93-1.0, z 0.57-1.17); BACK view apex y 545-572 (z 1.3-1.5) -> root z 0.98 (between the two views)
TAILSTUB = ((0.0, 0.62, 0.90), (0.0, 0.85, 0.95), 0.17, 0.13)
TAILSTUB_K = 0.08
# ---- COLLAR: a lumpy elliptical torus round the neck, front lower. SIDE view red x 864-995 (Y -1.08..+0.23), y 505-590
#   (z 1.07-1.92), tube ~0.43 tall (y 544-589 @ x 880; 525-568 @ x 960); FRONT (3/4) width x 105-267 (1.62 -> outer +-0.81);
#   BACK view a band at z 1.70-2.0 between the wings
COLLAR_C = (0.0, -0.40, 1.50)         # ring centre
COLLAR_TILT = 32.0                    # "collar tilt": the front sits lower (deg)
COLLAR_RX, COLLAR_RY = 0.60, 0.50     # ring radii (X / Y)
COLLAR_TUBE = 0.21                    # "collar thickness" (tube radius)
COLLAR_LUMPS = ((9, 0.14, 0.4), (14, 0.08, 1.7))   # "fluffy lumps": (count round the ring, depth x tube, phase)
COLLAR_FUZZ = (0.016, 0.085)          # "fuzz": noise amplitude / wavelength (reaches the HIGH + the bake only)
COLLAR_K = 0.06
# ---- EARS: broad triangular cat ears = a flattened cone with a CONVEX width profile (wide up high, a sharp tip).
#   BACK view (fg runs, outline-incl.): L ear tip y 276 @ x 528-532 (X 0.89, z 4.23), R tip @ x 689-701 (X 0.76) -> +-0.83;
#   L ear x 517-568 @ y 308 (X 0.52..1.03, z 3.91: 0.51 wide 0.32 under the tip), x 513-588 @ y 340 (X 0.32..1.07, z 3.59:
#   0.75 wide), R ear x 640-715 @ y 340 (X 0.21..0.96); the outer edge is near-vertical (x 513-520 from y 308 to 380) and
#   the ear merges into the head below y ~345. SIDE view: near tip x 935-943 @ y 278 (Y -0.33, z 4.19), far tip x 910-916
#   @ y 286 (Y -0.60) -> -0.46; near ear base x ~898-975 @ y ~365 (Y -0.75..0.0: 0.68 wide at z 3.32), the cream inner
#   triangle x 916-939 @ y 310-370
EAR_BASE = (0.58, -0.36, 3.30)        # the base centre (L side), on the head surface (head z 3.2 here)
EAR_TIP = (0.84, -0.46, 4.22)         # "ear tip" (L side)
EAR_R = (0.46, 0.02)                  # "ear width": base / tip half width (back view 0.75 wide at z 3.59 after the yaw)
EAR_CONVEX = 0.55                     # "ear fullness": width ~ (1 - t)^this along the ear (1 = straight sides; < 1 keeps
                                      #   the ear broad up high: back view 0.51 wide only 0.32 under the tip)
EAR_FLAT = 0.34                       # "ear thickness": front-back flattening of the cone
EAR_YAW = 42.0                        # the ear face turns this far from the front toward its side (broad in BOTH orthos:
                                      #   back 0.75 / side 0.68 at the base)
EAR_K = 0.06
EAR_INNER = (0.10, 0.88, 0.70, 0.10)  # "inner ear": (start, end along the ear, width x the ear's, min depth into the face)
# ---- MOUTH: an open pocket carved into the lower face. FACE panel opening x 1298-1342 (0.53 wide at the panel's ~82 px/u
#   head scale), top under the 'w' line y 162, rounded bottom y 193 (0.38 tall); SIDE view white y 486-520 (z 1.77-2.11),
#   x 833-854
MOUTH_C, MOUTH_R = (0.0, -1.60, 1.93), (0.27, 0.22, 0.22)   # pocket ellipsoid (centre, radii): a shallow bite (~0.1 deep)
MOUTH_TOP = 2.13                      # flat top of the opening (the upper lip line)
MOUTH_K = 0.03
MOUTH_TAU = 0.012                     # the mouth region = shell points this far inside the uncarved body
# ---- EYES (almond, white) + PUPILS (black vertical slits). FACE panel eye centres x 1283 / 1364 (+-0.49 at ~82 px/u),
#   44 x 35 px (0.53 x 0.42); FRONT (3/4) eyes x 110-160 / 205-255 (0.50 wide, centre spacing 0.95); SIDE view white
#   y 415-452 (centre z 2.63)
EYE_X, EYE_Z = 0.48, 2.63             # "eye placement" (mirrored); cast from the front onto the head
EYE_A, EYE_B = 0.27, 0.21             # "eye size": almond half width / half height
EYE_SHARP = 1.2                       # almond corner sharpness (0 = oval)
EYE_TILT = 12.0                       # "eye slant": the outer corner up (deg)
EYE_FACE = 0.40                       # the eye faces this share further toward the front than the head normal
PUPIL_A, PUPIL_B = 0.045, 0.175       # "pupil size": slit half width / half height (panel ~8 x 35 px)
PUPIL_IN = 0.05                       # pupils sit this far toward the midline (panel: both pupils inside their eyes' centres)
DECAL_UP, DECAL_DN, DECAL_DOME = 0.010, 0.03, 0.020   # decal top offset / sink / dome height
# ---- FANGS: two small cones hanging from the top corners of the mouth (FACE panel fangs x ~1302 / 1340, ~12 px long)
FANG_X, FANG_LEN, FANG_R = 0.17, 0.15, 0.045
# ---- WINGS (L side; R mirrored): leaf / bat shape, closed pillow sheet. BACK view tip x 466-471 @ y 455-460 (X 1.50,
#   z 2.42), leading edge through (556, 486) (X 0.64, z 2.13), trailing points (493,520) (518,549) (545,556), root under
#   the collar (588-595, 549-553); SIDE view near wing tip (1144, 437) (Y 1.72, z 2.60), far wing tip (1093, 387) (Y 1.21,
#   z 3.10), leading edge through (1051, 466), trailing points (1114,506) (1090,536) (1066,551), root (1001-1023, 540-551)
WING_ROOT = ((0.22, 0.20, 1.68), (0.30, 0.44, 1.46))   # root: leading-edge end / trailing-edge end (buried in the back)
WING_LEAD_MID = (0.68, 0.82, 2.24)    # leading edge passes here (the convex top of the leaf)
WING_TIP = (1.50, 1.48, 2.62)         # "wing tip"
WING_TRAIL = [(0.74, 0.94, 1.45), (1.00, 1.18, 1.60), (1.25, 1.40, 1.86)]   # trailing-edge points, root -> tip (3 points)
WING_SCALLOP = 0.17                   # "scallop depth": each trailing-edge arc bows toward the leading edge (x its chord)
WING_T = 0.030                        # "wing thickness" (half, mid-wing)
WING_CAMBER = 0.05                    # the leaf cups this far (toward its back face) at mid-chord
WING_NS, WING_NT = 30, 12             # "wing detail": stations along the span / across the chord
WING_ROOT_S = 0.15                    # the dark-green root (sheet: dark green at the wing roots, side (1015,535) back (590,540))
WING_VEIN_W = 0.022                   # dark-green vein strips: root -> each trailing point (half width)
# ---- TAIL: the broom / fan ("broom/fan-shaped"). The side and back views each draw it face-on, so it is built as ONE
#   BROOM: TAIL_N blades from a single root at the rump, laid on a cone opening backward and spread both vertically and
#   laterally. Every tip lies on the outer (scalloped) edge, placed so that:
#   - from BEHIND the tips trace the back view's kite: the outer tips at X +-0.98 (fan x 541-704 @ y 640 = +-0.82 + the
#     drawn tip flare), z 0.60, the scalloped bottom edge z 0.47 (back view bottom edge y 645-690, the side view's 0.47
#     where the two agree), the top tip z 2.02 (the side view's top tip; the back view's apex is only z 1.5 -- departure);
#   - from the SIDE every tip lands on the drawn outer edge line (1160,495) -> (1215,650) = (Y 1.88, z 2.02) -> (2.43, 0.47),
#     so the upper edge runs root -> (1.88, 2.02) (~46 deg, drawn 44) and the bottom edge stops at z 0.47.
#   The blades lie tangent to the cone (their width runs round it), so none is edge-on except the top / bottom blade from
#   the side, which ARE the fan's drawn edges. No blade reaches the floor.
TAIL_ROOT = (0.0, 0.85, 0.95)         # single root at the rump (side view the fan leaves the body at x 1065-1072, y 580-640)
TAIL_N = 12                           # "tail blades": one per 360 / TAIL_N deg round the back view (0 = straight up)
TAIL_KITE = ((0.0, 2.02), (0.45, 1.80), (0.98, 0.60), (0.0, 0.47))   # back-view tip outline (X, z): top, shoulder, outer
                                      #   corner (mirrored), bottom; the shoulder lifts the blades beside the top one so the
                                      #   edge-on top blade never stands alone as a spike in the side view
TAIL_EDGE = ((1.88, 2.02), (2.43, 0.47))   # side-view outer edge line (Y, z): each tip's Y comes from its z on this line
TAIL_W = (0.06, 0.55)                 # "blade width" at the root / the tip (full width; neighbours overlap = no gaps)
TAIL_T = 0.020                        # blade half-thickness
TAIL_LAYER = 0.03                     # "layering": every other blade stands this proud of its neighbours (outward)
TAIL_JAG = (3, 0.08)                  # "frayed ends": teeth per blade end, depth (x length)
TAIL_LINE = 0.18                      # the darker line strip along each blade's overlapped upper edge (share of the width)
TAIL_NS, TAIL_NT = 14, 6
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands)
OWN_TAU = 0.06                        # soft part ownership for weights
NECK_BAND = (1.30, 1.60)              # spine -> neck blend (z) on the neck
HEAD_BAND = (1.80, 2.10)              # neck -> head blend (z)
LEG_TOP = (0.95, 1.25)                # foreleg -> spine blend at the shoulder (z)
EAR_BLEND = (0.12, 0.35)              # head -> ear blend along the ear axis (fraction)
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent not yet given
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.8                          # "breathing": chest rise (spine pitch deg, 2 breaths per loop; the neck counters)
IDLE_HEAD = (1.2, 2.5)                # head nod (2 cyc) / slow look (1 cyc), deg
EAR_IDLE = (2.0, 1, 0.9)              # ear sway (deg, cyc, phase offset R)
FLUTTER = (6.0, 12.0, 4, 0.7)         # "wing flutter": (root deg, tip deg, cyc, lag) -- small fast beats (1 Hz)
TAIL_SWISH = (6.0, 10.0, 14.0, 1, 0.6)   # "tail swish": per bone deg (yaw) .001/.002/.003, cyc, lag per bone
WALK_N = 48                           # "weight-shift loop": 2 s
SHIFT_ROLL, SHIFT_PITCH = 2.0, 0.8    # "weight shift": spine roll (1 cyc) / pitch (2 cyc), deg
WING_SHIFT = (3.0, 5.0, 2, 0.6)       # wings settle with the shift
TAIL_SHIFT = (5.0, 8.0, 11.0, 1, 0.5)
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
          "design/reference/winged_cat-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (back + side); floor z = 0 at the soles",
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


def sd_torso(P):
    Q = P.copy(); Q[:, 0] = Q[:, 0] / TORSO_SX                # X-stretched: Lipschitz <= 1, a valid lower bound
    return SD.sd_round_cone(Q, *TORSO)


def sd_neck(P):
    return SD.sd_round_cone(P, *NECK)


def sd_head(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R)


def sd_haunch(P, sg):
    return np.minimum(SD.sd_ellipsoid(P, mir(HAUNCH_C, sg), HAUNCH_R), SD.sd_ellipsoid(P, mir(HINDFOOT_C, sg), HINDFOOT_R))


def sd_leg(P, sg):
    a, b = mir(LEG_KNOTS[0], sg), mir(LEG_KNOTS[1], sg)
    return np.minimum(SD.sd_round_cone(P, a, b, LEG_R[0], LEG_R[1]), SD.sd_ellipsoid(P, mir(PAW_C, sg), PAW_R))


def sd_tailstub(P):
    return SD.sd_round_cone(P, *TAILSTUB)


# ---- collar: lumpy elliptical torus (+ fuzz)
_ct = math.radians(COLLAR_TILT)
COL_AX = np.array([0.0, -math.sin(_ct), math.cos(_ct)])          # ring axis (tilted: the front of the ring sits lower)
COL_E2 = np.array([0.0, math.cos(_ct), math.sin(_ct)])            # in-plane 'back' direction
COL_E1 = np.array([1.0, 0.0, 0.0])
CC = np.array(COLLAR_C, float)


def collar_lumps(th):
    m = np.ones_like(th)
    for n_, a_, ph in COLLAR_LUMPS:
        m = m + a_ * np.sin(n_ * th + ph)
    return m


def fuzz(P):
    k = 2 * math.pi / COLLAR_FUZZ[1]
    return COLLAR_FUZZ[0] * (np.sin(k * P[:, 0] + 1.3) * np.sin(k * P[:, 1] + 0.7) * np.sin(k * P[:, 2] + 2.1)
                             + 0.5 * np.sin(1.7 * k * P[:, 0] + 0.2 * k * P[:, 2]) * np.sin(1.9 * k * P[:, 1] + 0.4))


def sd_collar(P, with_fuzz=True):
    Q = P - CC
    x, y, h = Q @ COL_E1, Q @ COL_E2, Q @ COL_AX
    th = np.arctan2(y / COLLAR_RY, x / COLLAR_RX)
    Rth = np.hypot(COLLAR_RX * np.cos(th), COLLAR_RY * np.sin(th))
    q = np.hypot(x / COLLAR_RX, y / COLLAR_RY)
    d = np.hypot((q - 1.0) * Rth, h) - COLLAR_TUBE * collar_lumps(th)
    return d - fuzz(P) if with_fuzz else d


# ---- ears: flattened round cones in each ear's own frame
EAR = {}
for s, sg in SIDES:
    b, t_ = mir(EAR_BASE, sg), mir(EAR_TIP, sg)
    ax = unit(t_ - b)
    yaw = math.radians(EAR_YAW)
    nh = np.array([sg * math.sin(yaw), -math.cos(yaw), 0.0])
    n_ = unit(nh - ax * float(nh @ ax))                     # the ear's front-face normal
    w_ = np.cross(ax, n_)
    EAR[s] = {"base": b, "tip": t_, "axis": ax, "n": n_, "w": w_, "len": float(np.linalg.norm(t_ - b))}


def ear_local(P, s):
    e = EAR[s]
    Q = P - e["base"]
    return Q @ e["w"], Q @ e["axis"], Q @ e["n"]


def ear_r(t):
    t = np.clip(np.asarray(t, float), 0.0, 1.0)
    return EAR_R[0] * (1.0 - t) ** EAR_CONVEX + EAR_R[1] * t


EAR_KNOTS = (0.0, 0.25, 0.5, 0.7, 0.85, 0.95, 1.0)   # the convex profile as a chain of round cones


def sd_ear(P, s):
    e = EAR[s]
    u, v, w = ear_local(P, s)
    Q = np.stack([u, v, w / EAR_FLAT], 1)
    d = np.full(len(P), 1e9)
    for t0, t1 in zip(EAR_KNOTS[:-1], EAR_KNOTS[1:]):
        d = np.minimum(d, SD.sd_round_cone(Q, (0, t0 * e["len"], 0), (0, t1 * e["len"], 0), float(ear_r(t0)), float(ear_r(t1))))
    return d * EAR_FLAT


def ear_inner_field(P, s):
    """> 0 on the cream inner ear: inside a narrower, shorter copy of the ear's outline (the plane distance ignores the
    thickness) AND on the front half of the flattened cone."""
    e = EAR[s]
    u, v, w = ear_local(P, s)
    a0, a1, ws, _ = EAR_INNER
    L = e["len"]
    v0, v1 = a0 * L, a1 * L
    tt = np.clip((v - v0) / (v1 - v0), 0.0, 1.0)
    rr = ws * ear_r(v / L) * (1.0 - tt ** 4)                # the inset follows the ear's own convex outline
    d2 = np.where((v >= v0) & (v <= v1), np.abs(u) - rr, np.maximum(v0 - v, v - v1) + np.abs(u))
    return np.minimum(-d2, w * 2.0)


def sd_mouth(P):
    d = SD.sd_ellipsoid(P, MOUTH_C, MOUTH_R)
    return SD.smax(d, P[:, 2] - MOUTH_TOP, 0.02)


GROUPS = ["torso", "neck", "head", "ear.L", "ear.R", "haunch.L", "haunch.R", "leg.L", "leg.R", "tailroot", "collar"]
GROUP_REGION = {g_: "fur" for g_ in GROUPS}
GROUP_REGION["collar"] = "collar"


def group_sdf(P, with_fuzz=True):
    cols = []
    for g_ in GROUPS:
        if g_ == "torso":
            d = sd_torso(P)
        elif g_ == "neck":
            d = sd_neck(P)
        elif g_ == "head":
            d = sd_head(P)
        elif g_.startswith("ear."):
            d = sd_ear(P, g_[4:])
        elif g_.startswith("haunch."):
            d = sd_haunch(P, 1.0 if g_.endswith("L") else -1.0)
        elif g_.startswith("leg."):
            d = sd_leg(P, 1.0 if g_.endswith("L") else -1.0)
        elif g_ == "tailroot":
            d = sd_tailstub(P)
        else:
            d = sd_collar(P, with_fuzz)
        cols.append(d)
    return np.stack(cols, 1)


def ell_box(c, r, pad=0.0):
    c = np.asarray(c, float); r = np.asarray(r, float)
    return c - r - pad, c + r + pad


def cone_box(a, b, r1, r2, pad=0.0):
    A = np.array([a, b], float); R = max(r1, r2)
    return A.min(0) - R - pad, A.max(0) + R + pad


g = SD.Grid((-1.20, -1.80, -0.05), (1.20, 1.15, 4.35), SDF_STEP, SDF_BAND)
g.apply(sd_torso, *cone_box(np.array(TORSO[0]) * [TORSO_SX, 1, 1], np.array(TORSO[1]) * [TORSO_SX, 1, 1],
                            TORSO[2] * TORSO_SX, TORSO[3] * TORSO_SX), 0.0)
for s, sg in SIDES:
    g.apply(lambda P, sg=sg: SD.sd_ellipsoid(P, mir(HAUNCH_C, sg), HAUNCH_R), *ell_box(mir(HAUNCH_C, sg), HAUNCH_R), HAUNCH_K)
    g.apply(lambda P, sg=sg: SD.sd_ellipsoid(P, mir(HINDFOOT_C, sg), HINDFOOT_R), *ell_box(mir(HINDFOOT_C, sg), HINDFOOT_R),
            FOOT_K)
    a_, b_ = mir(LEG_KNOTS[0], sg), mir(LEG_KNOTS[1], sg)
    g.apply(lambda P, a_=a_, b_=b_: SD.sd_round_cone(P, a_, b_, LEG_R[0], LEG_R[1]), *cone_box(a_, b_, *LEG_R), LEG_K)
    g.apply(lambda P, sg=sg: SD.sd_ellipsoid(P, mir(PAW_C, sg), PAW_R), *ell_box(mir(PAW_C, sg), PAW_R), PAW_K)
g.apply(sd_tailstub, *cone_box(*TAILSTUB), TAILSTUB_K)
g.apply(sd_neck, *cone_box(*NECK), NECK_K)
g.apply(sd_head, *ell_box(HEAD_C, HEAD_R), HEAD_K)
for s, _ in SIDES:
    e = EAR[s]
    g.apply(lambda P, s=s: sd_ear(P, s), np.minimum(e["base"], e["tip"]) - EAR_R[0], np.maximum(e["base"], e["tip"]) + EAR_R[0],
            EAR_K)
_cr = max(COLLAR_RX, COLLAR_RY) + COLLAR_TUBE * 1.3 + COLLAR_FUZZ[0] * 2
g.apply(sd_collar, CC - _cr, CC + _cr, COLLAR_K)
g.apply(sd_mouth, *ell_box(MOUTH_C, MOUTH_R), MOUTH_K, mode="subtract")
TOE_CREASES = []
for s, sg in SIDES:                                    # toe creases across each front paw + each hind-foot front
    for c_, r_ in ((mir(PAW_C, sg), np.array(PAW_R)), (mir(HINDFOOT_C, sg), np.array(HINDFOOT_R))):
        for dx in CREASES:
            x_ = c_[0] + dx * r_[0] / 0.21
            c0 = np.array([x_, c_[1] - 0.35 * r_[1], c_[2] + 0.95 * r_[2]])
            c1 = np.array([x_, c_[1] - 1.05 * r_[1], 0.0])
            TOE_CREASES.append((c0, c1))
            g.apply(lambda P, c0=c0, c1=c1: SD.sd_round_cone(P, c0, c1, CREASE_R, CREASE_R),
                    np.minimum(c0, c1) - CREASE_R, np.maximum(c0, c1) + CREASE_R, CREASE_K, mode="subtract")
g.floor(0.0)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
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
FIELDS["mouth"] = -DGR.min(1) - MOUTH_TAU                  # inside the uncarved body = the carved pocket's floor
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
on_head = lambda a, b: all(v[LAY["f_head"]] > -TOL for v in (a, b))
CUTS = [("f_collar", 0.0, None), ("inner", 0.0, on_ear), ("mouth", 0.0, on_head)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array([GROUP_REGION[GROUPS[o]] for o in owner], dtype=object)
is_ear = np.isin(owner, [GROUPS.index("ear.L"), GROUPS.index("ear.R")])
shell_reg[is_ear & (FVAL["inner"] > 0)] = "ear_inner"
shell_reg[(owner == GROUPS.index("head")) & (FVAL["mouth"] > 0)] = "mouth"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: eyes, pupils, fangs, wings, tail
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
    S, _ = cast((x, -3.0, z), (0, 1, 0))
    ns = [cast((x + dx, -3.0, z + dz), (0, 1, 0))[1] for dx, dz in ((0, 0), (0.04, 0), (-0.04, 0), (0, 0.04), (0, -0.04))]
    return S, unit(np.mean(ns, 0))


PARTS = []
FACE = {}
for s, sg in SIDES:
    S, n = front_anchor(sg * EYE_X, EYE_Z)
    ne = unit(n + EYE_FACE * np.array([0.0, -1.0, 0.0]))
    tilt = math.radians(EYE_TILT) * sg                       # outer corner up: the eye's x axis rises toward the side
    up = (-math.sin(tilt), 0.0, math.cos(tilt))
    O = FP.lens_curve(EYE_A, EYE_B, 28, EYE_SHARP)
    V, F, R = FP.polar_decal(S, ne, O, project, DECAL_UP, DECAL_DN, DECAL_DOME, 4, "eye", up_hint=up)
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    Sp, _ = front_anchor(sg * (EYE_X - PUPIL_IN), EYE_Z)
    Op = FP.lens_curve(PUPIL_B, PUPIL_A, 20, 0.5)[:, ::-1]    # the slit: long axis vertical
    V, F, R = FP.polar_decal(Sp, ne, Op, project, DECAL_UP + DECAL_DOME + 0.006, DECAL_DN, 0.006, 3, "pupil", up_hint=up)
    PARTS.append(FP.Part("pupil." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": ne.round(4).tolist()}
    Sf, _ = cast((sg * FANG_X, -3.0, MOUTH_TOP + 0.02), (0, 1, 0))
    root = Sf + np.array([0.0, 0.045, 0.03])                # the root sits just inside the upper lip
    tip = root + np.array([0.0, -0.02, -FANG_LEN - 0.03])
    ax_ = unit(root - tip)
    Lf = float(np.linalg.norm(root - tip))
    prof = [(0.0, 0.02), (FANG_R, 0.0), (FANG_R * 0.85, -0.25 * Lf), (FANG_R * 0.45, -0.7 * Lf), (FANG_R * 0.15, -0.95 * Lf),
            (0.0, -Lf)]
    V, F, R = FP.lathe(prof, "fang", 10, root, ax_, up_hint=(0, 1, 0))
    PARTS.append(FP.Part("fang." + s, V, F, R, bone="head"))
    FACE["fang." + s] = {"root": root.round(4).tolist(), "tip": tip.round(4).tolist()}


# ---- wings: closed pillow leaves (built on the L side, mirrored)
def catmull_pts(pts, n_per):
    return SD.catmull(np.asarray(pts, float), n_per)


def resample_by_arc(C, n):
    al = SD.arclen(C)
    tt = np.linspace(0.0, al[-1], n)
    return np.stack([np.interp(tt, al, C[:, k]) for k in range(3)], 1)


def build_wing(sg):
    rl, rt = mir(WING_ROOT[0], sg), mir(WING_ROOT[1], sg)
    tip = mir(WING_TIP, sg)
    lead = catmull_pts([rl, mir(WING_LEAD_MID, sg), tip], 24)
    tp = [rt] + [mir(p, sg) for p in WING_TRAIL] + [tip]
    nrm = unit(np.cross(tip - rl, mir(WING_TRAIL[1], sg) - rl))
    if nrm[1] < 0:                                         # the leaf's back face points back (+Y)
        nrm = -nrm
    trail = [rt]
    for A, B in zip(tp[:-1], tp[1:]):                       # scallops: each arc bows toward the leading edge
        ch = B - A
        inw = unit(np.cross(nrm, ch)) * sg
        mid_lead = lead[np.argmin(np.linalg.norm(lead - 0.5 * (A + B), axis=1))]
        if np.dot(inw, mid_lead - 0.5 * (A + B)) < 0:
            inw = -inw
        for u in np.linspace(0.0, 1.0, 13)[1:]:
            trail.append(A + ch * u + inw * WING_SCALLOP * float(np.linalg.norm(ch)) * math.sin(math.pi * u))
    trail = np.array(trail)
    Ls = resample_by_arc(lead, WING_NS + 1)
    Ts = resample_by_arc(trail, WING_NS + 1)
    tt = np.linspace(0.0, 1.0, WING_NT + 1)
    P = Ls[:, None, :] * (1 - tt)[None, :, None] + Ts[:, None, :] * tt[None, :, None]
    P = P + nrm[None, None, :] * (WING_CAMBER * np.sin(math.pi * tt)[None, :, None]
                                  * np.sin(math.pi * np.linspace(0.0, 1.0, WING_NS + 1) ** 0.8)[:, None, None])
    ss = np.linspace(0.0, 1.0, WING_NS + 1)
    Hh = WING_T * (np.sin(math.pi * tt)[None, :] ** 0.5) * (1.0 - 0.6 * ss[:, None])
    N = np.broadcast_to(nrm, P.shape)
    # vein lines (plane coords): root mid -> each trailing point
    root_mid = 0.5 * (rl + rt)
    veins = [(root_mid, mir(p, sg)) for p in WING_TRAIL]

    def seg_dist(p, a, b):
        ab = b - a
        u = np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0.0, 1.0)
        return float(np.linalg.norm(p - a - ab * u)), u

    def region_fn(i, j, top_):
        if (i + 0.5) / WING_NS < WING_ROOT_S:
            return "wing_root"
        c = 0.25 * (P[i, j] + P[i + 1, j] + P[i + 1, j + 1] + P[i, j + 1])
        for a, b in veins:
            d, u = seg_dist(c, a, b)
            if d < WING_VEIN_W and 0.05 < u < 0.97:
                return "wing_vein"
        return "wing"
    V, F, R, IJ = FP.pillow(P, N, Hh, region_fn)
    s_v = IJ[:, 0] / WING_NS
    span = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(0.5 * (Ls + Ts), axis=0), axis=1))])
    arc_v = span[IJ[:, 0]]
    return V, F, R, arc_v, 0.5 * (Ls + Ts), nrm


WING = {}
for s, sg in SIDES:
    V, F, R, arc_v, spine, nrm = build_wing(sg)
    p = FP.Part("wing." + s, V, F, R)
    al = SD.arclen(spine)
    p.chain = ("wing_%s" % s, arc_v, float(al[-1]), "spine", 2, 0.0)
    PARTS.append(p)
    WING[s] = {"spine": spine, "length": float(al[-1]), "normal": nrm}

# ---- tail: TAIL_N closed tapered pillow blades fanning from the rump
TR = np.array(TAIL_ROOT, float)


def kite_tip(beta):
    """back-view ray from the root at angle beta (rad, 0 = straight up, + toward +X) -> its hit on the kite outline
    (top, outer corner, bottom, mirrored corner) as (X, z)"""
    top, sho, cor, bot = (np.array(p, float) for p in TAIL_KITE)
    m_ = np.array([-1.0, 1.0])
    poly = [top, sho, cor, bot, cor * m_, sho * m_, top]
    o = np.array([TR[0], TR[2]])
    r = np.array([math.sin(beta), math.cos(beta)])
    best = None
    for A, B in zip(poly[:-1], poly[1:]):
        M = np.array([r, A - B]).T
        if abs(np.linalg.det(M)) < 1e-12:
            continue
        lam, mu = np.linalg.solve(M, A - o)
        if lam > 0 and -1e-9 <= mu <= 1 + 1e-9 and (best is None or lam < best):
            best = lam
    return o + r * best


def edge_y(z):
    (y0, z0), (y1, z1) = TAIL_EDGE
    return y0 + (z0 - z) / (z0 - z1) * (y1 - y0)


TAIL_TIPS = []
for k in range(TAIL_N):
    xz = kite_tip(2 * math.pi * k / TAIL_N)
    TAIL_TIPS.append(np.array([xz[0], edge_y(xz[1]), xz[1]]))
TAIL_AXIS = unit(np.mean([unit(t - TR) for t in TAIL_TIPS], 0))


def blade_frame(k):
    """blade k round the broom: direction d, width direction W (tangent round the cone: toward the next tip), face
    normal (outward from the cone axis), length, tip width"""
    tip = TAIL_TIPS[k]
    d = unit(tip - TR)
    w_ = TAIL_TIPS[(k + 1) % TAIL_N] - TAIL_TIPS[(k - 1) % TAIL_N]
    W = unit(w_ - d * float(w_ @ d))
    Nb = unit(np.cross(d, W))
    if float(Nb @ (d - TAIL_AXIS * float(d @ TAIL_AXIS))) < 0:
        Nb = -Nb
    return k / max(TAIL_N - 1, 1), d, W, Nb, float(np.linalg.norm(tip - TR)), TAIL_W[1]


TAIL_INFO = []
TAIL_CHAIN_MAX = float(max(np.linalg.norm(t - TR) for t in TAIL_TIPS))
for bi in range(TAIL_N):
    phi, d, W, Nb, Lb, wtip = blade_frame(bi)
    layer = (bi % 2) * TAIL_LAYER                           # odd blades lap over their even neighbours (a ring has no first)
    ss = np.linspace(0.0, 1.0, TAIL_NS + 1)
    tt = np.linspace(0.0, 1.0, TAIL_NT + 1)
    tooth = np.abs(((tt * TAIL_JAG[0] + 0.5 * FP.hash01(bi, 1.7)) % 1.0) - 0.5) * 2.0   # 1 at the tooth tips
    Lt = Lb * (1.0 - TAIL_JAG[1] * (1.0 - tooth) * (0.7 + 0.6 * FP.hash01(bi, 3.3)))
    wid = TAIL_W[0] + (wtip - TAIL_W[0]) * ss ** 0.8
    P = np.zeros((TAIL_NS + 1, TAIL_NT + 1, 3))
    for i, s_ in enumerate(ss):
        for j, t_ in enumerate(tt):
            P[i, j] = TR + d * (s_ * Lt[j]) + W * ((t_ - 0.5) * wid[i]) + Nb * layer * min(1.0, s_ / 0.25)
    Hh = TAIL_T * (np.sin(math.pi * tt)[None, :] ** 0.5) * (np.sin(math.pi * np.clip(ss, 0, 1) ** 0.6)[:, None] ** 0.3)
    N = np.broadcast_to(Nb, P.shape)

    def region_fn(i, j, top_):
        return "tail_line" if (j + 0.5) / TAIL_NT > 1.0 - TAIL_LINE else "tail"
    V, F, R, IJ = FP.pillow(P, N, Hh, region_fn)
    p = FP.Part("tail_blade.%02d" % bi, V, F, R)
    arc = np.array([ss[i] * Lt[j] for i, j in IJ])
    p.chain = ("tail", arc, TAIL_CHAIN_MAX, "spine", 3, 0.0)
    PARTS.append(p)
    TAIL_INFO.append({"blade": bi, "dir": d.round(4).tolist(), "length": round(Lb, 4),
                      "tip_centre": (TR + d * Lb).round(4).tolist()})
TAIL_CHAIN_LEN = float(max(blade_frame(k)[4] for k in range(TAIL_N)))


# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
assert lo0[2] >= -1e-3, "a part dips below the floor (z %.4f, %s): the soles would float" % (lo0[2], [i[0] for i in ISL if i[1][:, 2].min() < -1e-3])   # floor = the SDF floor cut (+ the kit NODE_EPS nudge)
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
REG = ["fur", "ear_inner", "collar", "mouth", "eye", "pupil", "fang", "wing", "wing_root", "wing_vein", "tail", "tail_line"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)


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

# facing landmark: the head centre -> the face front on the midline at the mouth-top height
HEAD_CTR = np.array(HEAD_C, float)
FACE_FRONT, _ = cast((0.0, -3.0, MOUTH_TOP + 0.08), (0, 1, 0))
anchor = HEAD_CTR - SHIFT
landmark = FACE_FRONT - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the face front on the midline just above the mouth",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["tail_blades"] = TAIL_INFO

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
    key = {"eye": "eyes", "pupil": "pupils", "fang": "fangs", "wing": "wings", "tail_blade": "tail"}[p.name.split(".")[0]]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The shell carries the round head, the pointed "
                            "ears, the haunches, the paws and the lumpy collar; the two leaf wings and the ten-blade fan "
                            "tail are the next biggest shares; the bake carries the collar fuzz." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, decals, fangs, wing + blade pillows)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"back_view_height_px": 430, "back_view_width_px": 306, "side_view_height_px": 424,
                               "side_view_length_px": 407},
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
low["conquest_source"] = "from scratch: design/reference/winged_cat-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_wing_pts = VALL[RANGE["wing.L"][0]:RANGE["wing.L"][1]]
_tail_pts = np.vstack([p.V for p in PARTS if p.name.startswith("tail_blade")]) - SHIFT
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-1.05, -1.75, 1.55]) - SHIFT).tolist(), (np.array([1.05, -0.8, 3.45]) - SHIFT).tolist()],
    "wing": [_wing_pts.min(0).tolist(), _wing_pts.max(0).tolist()],
    "tail": [_tail_pts.min(0).tolist(), _tail_pts.max(0).tolist()],
    "collar": [(np.array([-0.95, -1.15, 0.9]) - SHIFT).tolist(), (np.array([0.95, 0.35, 2.1]) - SHIFT).tolist()],
    "views": {"face": [-12.0, 5.0], "wing": [135.0, 15.0], "tail": [110.0, 10.0], "collar": [-25.0, 8.0]},
    "axis_xy": [float(-SHIFT[0] + BACK_AXIS_OFF), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "face")}, default=str))
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
body_faces = np.ones(nf, bool)
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets, the collar fuzz at full density) + every closed part",
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


SPINE_PTS = {"spine": ((0.0, 0.15, 0.55), (0.0, -0.32, 1.38)), "neck": ((0.0, -0.32, 1.38), (0.0, -0.55, 1.98)),
             "head": ((0.0, -0.55, 1.98), (0.0, -0.69, 3.40))}
BONES = [("spine", P_(SPINE_PTS["spine"][0]), P_(SPINE_PTS["spine"][1]), "root"),
         ("neck", P_(SPINE_PTS["neck"][0]), P_(SPINE_PTS["neck"][1]), "spine"),
         ("head", P_(SPINE_PTS["head"][0]), P_(SPINE_PTS["head"][1]), "neck")]
for s, sg in SIDES:
    BONES.append(("ear_" + s, P_(EAR[s]["base"] + EAR[s]["axis"] * 0.15), P_(EAR[s]["tip"]), "head"))
for s, sg in SIDES:
    sp = WING[s]["spine"]; al = SD.arclen(sp)
    pts = [np.array([np.interp(x, al, sp[:, k]) for k in range(3)]) for x in np.linspace(0, al[-1], 3)]
    BONES += [("wing_%s.001" % s, P_(pts[0]), P_(pts[1]), "spine"), ("wing_%s.002" % s, P_(pts[1]), P_(pts[2]), "wing_%s.001" % s)]
TAIL_PTS = [TR + TAIL_AXIS * TAIL_CHAIN_LEN * k / 3.0 for k in range(4)]
BONES += [("tail.001", P_(TAIL_PTS[0]), P_(TAIL_PTS[1]), "spine"), ("tail.002", P_(TAIL_PTS[1]), P_(TAIL_PTS[2]), "tail.001"),
          ("tail.003", P_(TAIL_PTS[2]), P_(TAIL_PTS[3]), "tail.002")]
for s, sg in SIDES:
    BONES.append(("foreleg_" + s, P_(mir(LEG_KNOTS[0], sg)), P_(mir(PAW_C, sg) + np.array([0.0, -0.10, 0.0])), "spine"))
CONNECT = {"neck", "head", "wing_L.002", "wing_R.002", "tail.002", "tail.003"}
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
GD = group_sdf(Pw, with_fuzz=False)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
zz = Pw[:, 2]
Ws = np.zeros((b_ - a_, len(DEFORM)))
for gname in ("torso", "haunch.L", "haunch.R"):
    Ws[:, J["spine"]] += Wg[:, GROUPS.index(gname)]
wn = smoothstep(NECK_BAND[0], NECK_BAND[1], zz)
whd = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
gi = GROUPS.index("neck")
Ws[:, J["spine"]] += Wg[:, gi] * (1 - wn)
Ws[:, J["neck"]] += Wg[:, gi] * wn * (1 - whd)
Ws[:, J["head"]] += Wg[:, gi] * wn * whd
Ws[:, J["neck"]] += Wg[:, GROUPS.index("collar")]
Ws[:, J["head"]] += Wg[:, GROUPS.index("head")]
for s, sg in SIDES:
    gi = GROUPS.index("ear." + s)
    _, v_, _ = ear_local(Pw, s)
    we = smoothstep(EAR_BLEND[0], EAR_BLEND[1], v_ / EAR[s]["len"])
    Ws[:, J["ear_" + s]] += Wg[:, gi] * we
    Ws[:, J["head"]] += Wg[:, gi] * (1 - we)
    gi = GROUPS.index("leg." + s)
    wt_ = smoothstep(LEG_TOP[0], LEG_TOP[1], zz)
    Ws[:, J["foreleg_" + s]] += Wg[:, gi] * (1 - wt_)
    Ws[:, J["spine"]] += Wg[:, gi] * wt_
gi = GROUPS.index("tailroot")
_, s_arc, _ = FP.spine_param(Pw, np.array(TAIL_PTS))
Wv = K.vine_weights(np.clip(s_arc, 0, TAIL_CHAIN_LEN), TAIL_CHAIN_LEN, 3)
Ws[:, J["spine"]] += Wg[:, gi] * Wv[:, 0]
for k in range(3):
    Ws[:, J["tail.%03d" % (k + 1)]] += Wg[:, gi] * Wv[:, k + 1]
Wt[a_:b_] = Ws
CHAIN_BONES = {"wing_L": ["wing_L.001", "wing_L.002"], "wing_R": ["wing_R.001", "wing_R.002"],
               "tail": ["tail.001", "tail.002", "tail.003"]}
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if p.chain:
        chain, s_arc, L_, parent, nb, s0 = p.chain
        Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, nb, parent_s0=s0)
        Wt[a_:b_, J[parent]] += Wv[:, 0]
        for k in range(nb):
            Wt[a_:b_, J[CHAIN_BONES[chain][k]]] += Wv[:, k + 1]
    else:
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between torso, haunches, neck, collar, head, ears, front "
                          "legs, tail root; neck spine -> neck -> head by z bands; collar rides the neck; ears blend from "
                          "the head along the ear axis; front legs blend into the spine at the shoulder; wings + tail "
                          "blades: rigkit.vine_weights along their chains (parent spine); eyes, pupils, fangs rigid on "
                          "the head" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame with no action bound (pass 1, floor
#      guard lifting the spine if any vertex dips below z 0), keyed from the stored channels (pass 2, frame N+1 = frame 1),
#      measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM


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


# wing flap axis: the root chord line (leading-edge root -> trailing-edge root) -- the wing beats about its hinge
WING_HINGE = {s: unit(mir(WING_ROOT[1], sg) - mir(WING_ROOT[0], sg)) * sg for s, sg in SIDES}


def wings_pose(P, u, spec, phase0):
    a0, a1, cyc, lag = spec
    for s, sg in SIDES:
        for k in range(2):
            ang = K.vine_wave(u, k, 2, a0, a1, lag, cyc, phase0)
            P["wing_%s.%03d" % (s, k + 1)] = (None, [(tuple(WING_HINGE[s]), ang)], None)


def tail_pose(P, u, spec, phase0):
    amps, cyc, lag = spec[:3], spec[3], spec[4]
    for k in range(3):
        ang = amps[k] * math.sin(TAU * cyc * u + phase0 - k * lag)
        P["tail.%03d" % (k + 1)] = (None, [("Z", ang)], None)


def idle_pose(u):
    P = {}
    br = math.sin(TAU * 2 * u)
    P["spine"] = (None, [("X", -BREATH * br)], None)              # the chest rises (pitch back about the pelvis)
    P["neck"] = (None, [("X", BREATH * br)], None)                # the head stays level
    P["head"] = (None, [("X", IDLE_HEAD[0] * math.sin(TAU * 2 * u - 0.9)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u + 0.3))], None)
    for s, sg in SIDES:
        ph = 0.0 if s == "L" else EAR_IDLE[2]
        P["ear_" + s] = (None, [("Y", sg * EAR_IDLE[0] * math.sin(TAU * EAR_IDLE[1] * u + ph))], None)
    wings_pose(P, u, FLUTTER, 0.0)
    tail_pose(P, u, TAIL_SWISH, 0.4)
    return P, {"lift": 0.0}


def walk_pose(u):
    """PLACEHOLDER sitting weight-shift: the upper body rolls side to side over the haunches (1 cyc) with a small pitch
    (2 cyc); the front legs counter-roll so the paws stay planted; the head steadies; wings + tail follow."""
    P = {}
    roll = SHIFT_ROLL * math.sin(TAU * u)
    pitch = SHIFT_PITCH * math.sin(TAU * 2 * u + 0.5)
    P["spine"] = (None, [("Y", roll), ("X", pitch)], None)
    P["neck"] = (None, [("Y", -0.6 * roll)], None)
    P["head"] = (None, [("Y", -0.3 * roll), ("X", -pitch)], None)
    for s, sg in SIDES:
        P["foreleg_" + s] = (None, [("X", -pitch), ("Y", -roll)], None)
        P["ear_" + s] = (None, [("Y", sg * 1.5 * math.sin(TAU * u - 0.6))], None)
    wings_pose(P, u, WING_SHIFT, 0.8)
    tail_pose(P, u, TAIL_SHIFT, 1.0)
    return P, {"lift": 0.0, "roll": roll}


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
        if bn == "spine":
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
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "spine_z": float(pbs["spine"].head.z),
                     "wing_tip_L": list(pbs["wing_L.002"].tail), "wing_tip_R": list(pbs["wing_R.002"].tail),
                     "tail_tip": list(pbs["tail.003"].tail), "head_tail": list(pbs["head"].tail),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("spine_z") - REST_H["spine"][2]
    wl = np.array([r["wing_tip_L"] for r in R_]); wr = np.array([r["wing_tip_R"] for r in R_])
    tt_ = np.array([r["tail_tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "spine_lift": [round(float(bz.min()), 5), round(float(bz.max()), 5)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "wing_tip_L_p2p_xyz": (wl.max(0) - wl.min(0)).round(4).tolist(),
                    "wing_tip_R_p2p_xyz": (wr.max(0) - wr.min(0)).round(4).tolist(),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist(),
                    "head_top_p2p_xyz": (hd.max(0) - hd.min(0)).round(4).tolist()}
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
rig["conquest_rig"] = ("winged_cat v1: root > spine > neck > head > ear_L / ear_R; spine > wing_{L,R}.001 > .002; "
                       "spine > tail.001 > tail.002 > tail.003; spine > foreleg_L / foreleg_R")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s: breathing (chest "
                               "rise, head level), small head nod / look, ear sway, wing flutter (4 small beats about the "
                               "wing hinge), tail swish (travelling yaw wave). walk = 2 s SITTING gentle weight-shift: the "
                               "upper body rolls over the haunches, the front paws stay planted, wings + tail follow. "
                               "Paws never below z 0 (floor guard), root still.")
low["conquest_locomotion"] = "PLACEHOLDER: sitting weight-shift in place (movement intent open; the game glides the unit)"
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
              "structure": "armature identity + skinned 'winged_cat' (one opaque body material); natural scale; "
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
