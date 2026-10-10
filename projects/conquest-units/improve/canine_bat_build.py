"""Canine Bat Hybrid -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/canine_bat_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)"
(section CANINE BAT HYBRID) + the sheet design/reference/canine_bat-character-sheet.png (intake:
design/reference/canine_bat/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. Lane brief:
improve/canine_bat_v1_brief.md (v1 DRAFT).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The sheet's FRONT is drawn three-quarter, so every constant is
measured off the two TRUE orthos (intake ortho_frame.views = back, side); the front is a 3/4 check only:
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 935.5) / 100 ; z = (796 - y_px) / 100
  BACK view (the unit's right on image right):          X = -(x_px - 547.5) / 100 ; z = (794 - y_px) / 100
(the intake axes / floor lines; X +ve = the unit's LEFT (L)). The side view is drawn with the camera turned ~20 deg
toward the front-left: near-side (+X) features sit shifted toward +Y on the page, far-side ones toward -Y (near / far
ear tips x 1020 / 935 at the same height, near / far front paws x 870 / 797, hind paws x 1095 / 1025). Constants take
the near / far midpoint (the true position) unless a comment says otherwise.

A QUADRUPED (the first four-legged forge creature): body along -Y (head at the front), four short sturdy legs.

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the TORSO (a (Y, z_lo, z_hi, half-width) profile
     along Y), the NECK, the big round HEAD, the pig / bat SNOUT (a flat-faced capsule), four LEGS (two round-cone
     segments + a smooth rounded PAW with three carved TOE CREASES, floor-cut), the small hanging TAIL, the fluffy NECK
     RUFF (rounded collar puffs + a few broad overlapping PETAL lobes pointing out / slightly down with short soft tips +
     a full chest bib). Fur language (gate 2026-10-09): SIMPLE, CLEAN forms -- no scattered tufts or nubs anywhere; the
     soft-fur read lives in the silhouette (the ruff petals, a low blended fringe on the back / sides of the head) and in
     the bake. Marching tets = the HIGH (bake source); collapse decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: part ownership (snout, ruff) + the LIGHT field (lighter fur on
     the lower legs / paws, higher on the inner thighs and the back of the hind legs, a soft scalloped edge; plus the
     belly between the legs).
  3. PARTS (canine_bat_parts.py, closed): two OVERSIZED upright EARS (thin cupped membranes: mauve inner, dark rim,
     faint vein strips, fur back); closed conforming DECALS on the low shell: two big EYES (pale rim + dark iris +
     highlight), two NOSTRILS on the snout face; two small closed FANG cones.
  4. palette (palettes.py regions -> Col / Glow, no glow), Smart UV, bake normal + AO from the SDF high + parts, seed 0.
  5. RIG: root > hips > spine > chest > neck > head > ear.{L,R}.{0,1} (jaw-free head); hips > leg.{FL,FR,BL,BR}.{0,1,2}
     (2 = the paw, never scaled); hips > tail.{0,1}. Clips (PLACEHOLDER, artist N1 open): idle (breathing + ear
     twitch + tail wag, paws planted) + walk (in-place 4-beat trot: diagonal pairs a beat apart, root still). glb.
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
import canine_bat_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "canine_bat"
CHAR_ID = "canine_bat"                # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.24                       # >= the largest smooth-union k (LEG_K 0.16) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 12000                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- TORSO along Y. Rows (Y, z bottom, z top, half-width X): top / bottom from the SIDE view outline (body top y 504-509
#   @ x 935-1035 (z 2.92-2.87 incl. fur), y 513 @ x 1090 (2.83), y 542 @ x 1110 (2.54), y 567 @ x 1130 (2.29), rump end
#   x 1145 (Y 2.10); belly y 648-653 @ x 950-990 (z 1.43-1.48)); half-width from the BACK view (x 472-622 @ y 550 (0.75),
#   x 457-635 @ y 580 (0.90); the wider rows below z 2 are the hind haunches)
TORSO_PROFILE = [(-1.40, 2.20, 2.20, 0.00), (-1.28, 1.88, 2.66, 0.52), (-1.05, 1.66, 2.82, 0.68), (-0.55, 1.52, 2.88, 0.75),
                 (0.00, 1.47, 2.88, 0.78), (0.50, 1.46, 2.90, 0.82), (1.00, 1.45, 2.88, 0.87), (1.45, 1.48, 2.78, 0.88),
                 (1.78, 1.58, 2.52, 0.78), (2.00, 1.78, 2.24, 0.52), (2.10, 2.02, 2.02, 0.00)]
# ---- neck (hidden under the ruff) + HEAD (big, round) + SNOUT. SIDE view: chin y 492 (z 3.04) @ x 760-875, crown y 330-345
#   (z 4.5-4.65), forehead x 740 @ y 376 (Y -1.96, z 4.20); BACK view: head x 437-662 @ y 400-470 (half 1.05-1.12 incl. the
#   cheek fur), bottom y 490 (z 3.04), crown tuft y 340 (z 4.54)
NECK = ((0.0, -0.92, 2.50), (0.0, -1.00, 3.30), 0.55, 0.58)   # (a, b, r_a, r_b)
NECK_K = 0.14
HEAD_C, HEAD_R, HEAD_K = (0.0, -1.08, 3.84), (1.10, 0.95, 0.80), 0.14   # "head size" (half width / depth / height)
#   snout: SIDE view front face x 707-712 (Y -2.26), z 3.28-3.79 (y 418-469); a flat-faced capsule along -Y, tipped up
SNOUT_A, SNOUT_B, SNOUT_R = (0.0, -1.70, 3.53), (0.0, -2.06, 3.56), 0.30   # "snout size" (capsule ends, radius)
SNOUT_FACE_Y = -2.27                  # the flat front face of the snout (plane)
SNOUT_K, SNOUT_EDGE = 0.07, 0.04      # blend into the head / roundness of the face rim
# ---- legs (short, sturdy): per leg (L side) joint knots (X, Y, z) + radii; mirrored to R. SIDE view near / far front
#   paws x 830-905 / 760-835 (mid Y -1.0); hind paws x 1050-1140 / 1000-1050 (mid Y 1.25-1.35); BACK view hind legs
#   x 470-530 (X 0.48-0.78 @ z 0.64, centre 0.61, half 0.35), haunch outer x 433 (X 1.14 @ z 1.4-1.5), paws to X 1.05
LEG_FRONT = [(0.50, -0.86, 2.15), (0.52, -0.90, 1.20), (0.52, -0.93, 0.42)]
LEG_FRONT_R = [0.36, 0.29, 0.25]      # "front leg thickness" (shoulder, elbow, wrist)
LEG_HIND = [(0.52, 1.22, 1.85), (0.60, 1.40, 1.05), (0.62, 1.48, 0.45)]
LEG_HIND_R = [0.56, 0.40, 0.31]       # "hind leg / haunch thickness" (hip, stifle, hock)
LEG_K = 0.16                          # legs melt into the torso over this
PAW_FRONT = ((0.52, -1.02, 0.19), (0.30, 0.38, 0.21))   # "paw size" (centre, radii): rounded paws, floor-cut
PAW_HIND = ((0.62, 1.32, 0.19), (0.33, 0.42, 0.21))
PAW_K = 0.10
TOE_DY = -0.24                        # the paw front (toe tips) sits this far ahead of the paw centre (bone tail)
CREASES = (-0.11, 0.0, 0.11)          # "toe creases": 3 grooves across the paw front (4 toes), X offset x the paw width / 0.3
CREASE_R, CREASE_K = 0.032, 0.02      # groove half-width / softness (carved: SDF subtraction)
# ---- tail (small, hanging): BACK view a dark wedge x 518-578 @ y 640 (X +-0.30, z 1.54) down to a tip ~y 700 (z 0.94);
#   SIDE view rear contour x 1140-1145 @ y 590-700 (Y ~2.05)
TAIL_PTS = [(0.0, 1.78, 2.15), (0.0, 1.90, 1.50), (0.0, 1.96, 0.92)]
TAIL_RAD = [0.30, 0.23, 0.07]         # "tail thickness" root -> tip (blunt tip)
TAIL_K = 0.09
# ---- neck RUFF (fluffy, pale, scalloped). SIDE view: ruff x 800-950 / y 455-610 (front edge Y -1.36, back Y 0.15, z 1.9
#   -3.1); BACK view collar points x 450-650 @ y 495-575 (X +-0.96, z 2.2-3.0); FRONT (3/4 check) bib down to y ~640
RUFF_C = (0.0, -0.62, 2.70)           # collar centre
RUFF_AX = (0.66, 0.70)                # collar half extents (X, Y)
RUFF_Z = (2.60, 2.92)                 # collar height at the front / back (side view: the ruff starts right under the chin, y 505)
RUFF_PUFFS, RUFF_PUFF_R = 14, (0.20, 0.26)   # "ruff fullness": rounded collar puffs round the neck, radius range
RUFF_BIB = [((0.0, -1.42, 2.62), 0.30), ((0.0, -1.32, 2.24), 0.32), ((0.26, -1.24, 2.10), 0.27), ((-0.26, -1.24, 2.10), 0.27),
            ((0.0, -1.32, 1.98), 0.28)]   # chest bib puffs (a full, unbroken pale bib)
RUFF_K = 0.10
# the collar PETALS (gate 2026-10-09: "a soft fluffy collar of a few broad rounded overlapping petal-lobes, pointing out and
#   slightly down, short soft tips"; BACK view collar points reach x 451 / 643 = X +-0.96, z 2.2-3.0)
RUFF_PETALS = 12                      # "ruff petals": broad lobes round the collar (one overlapping row)
RUFF_PETAL_LEN = 0.42                 # how far each petal reaches past the collar ring (jittered +-12 %)
RUFF_PETAL_R = (0.23, 0.09)           # petal root (broad) / soft tip radius
RUFF_PETAL_DROP = 0.35                # how much the petals point down (0 = straight out, 1 = straight down)
RUFF_PETAL_K = 0.09                   # petals melt into the collar puffs over this
RUFF_BIB_PETALS = [((0.0, -1.33, 2.28), (0.0, -1.42, 1.80)), ((0.25, -1.24, 2.16), (0.37, -1.30, 1.84)),
                   ((-0.25, -1.24, 2.16), (-0.37, -1.30, 1.84))]   # chest bib petals (root, tip) hanging down the chest
# ---- soft-fur read: SILHOUETTE + BAKE only (gate 2026-10-09: no scattered tufts / nubs anywhere). The one fringe left: a
#   few LOW broad blended bulges on the back / sides of the head under the ears (BACK view fur at the head outline x 413-440
#   / 655-680 @ y 340-430: X +-1.1-1.36, z 3.6-4.5)
HEAD_FRINGE = [(100.0, 0.05), (122.0, 0.28), (140.0, -0.02), (160.0, 0.22)]   # (azimuth deg from the front, rise) per side
HEAD_FRINGE_R, HEAD_FRINGE_OUT, HEAD_FRINGE_K = 0.20, 0.07, 0.12   # bulge radius / how far it stands proud / blend
# ---- LIGHT fur regions (lower legs / paws / inner thighs / belly): height of the light edge per leg = OUT on the outer
#   face rising to IN toward the INNER direction. BACK view: hind legs light from y 690 (z 1.04) on the inner side, y 740
#   (z 0.54) outside, the side view's hind strip runs up the back of the haunch to y 640 (z 1.56); front legs light from
#   ~z 0.45 outside / ~1.0 inside (front 3/4 check)
LIGHT_FRONT = (0.42, 1.00, 0.30)      # (outer edge z, inner edge z, backward share of the inner direction)
LIGHT_HIND = (0.58, 1.30, 0.85)
LIGHT_SCALLOPS, LIGHT_SCALLOP_A = 7, 0.06    # "soft fur edge": rounded bumps round each leg, bump height (z)
BELLY_Z, BELLY_HW, BELLY_SLOPE = 1.62, 0.42, 2.0   # belly patch: top z, half-width before it falls off, fall-off
# ---- ears (OVERSIZED, upright, pointed, thin membrane). BACK view tips x 440 / 652 @ y 190 (X +-1.05, z 6.04); ear spans
#   x 413-517 @ y 325 (X 0.30..1.36, z 4.7), x 413-505 @ y 295; SIDE view near / far tips x 1020 / 935 @ y 197 (mid Y 0.42)
EAR_ROOT = (0.50, -0.68, 4.20)        # the buried root of the L ear (inside the head)
EAR_TIP = (1.05, 0.35, 6.04)          # "ear tip" (L side)
EAR_BOW = (0.14, 0.02, 0.0)           # outward bow of the ear spine (L side; the convex outer edge)
EAR_W = 0.70                          # "ear width" (half, widest low on the ear)
EAR_WIDE_AT = 0.45                    # width profile exponent: sin(pi sf^a) is widest at sf = 0.5^(1/a) (0.21)
EAR_T = 0.035                         # "membrane thickness" (half, mid-ear)
EAR_YAW = 44.0                        # the ear face turns this far from the front toward its side
EAR_CUP = 0.45                        # "ear cup": the edges curl toward the front by this share of the half-width
EAR_RIM_C = 0.90                      # ring |cos| above this = the dark rim (both faces)
EAR_BASE_SF = 0.10                    # below this spine fraction the ear is fur (buried / at the head)
EAR_VEINS = [(0.0, 0.14, 0.80), (0.5, 0.20, 0.55), (-0.5, 0.24, 0.62)]   # "veins": (ring cos, sf from, sf to)
EAR_FRINGE = (4, (0.03, 0.07), (0.03, 0.06))   # soft fur notches on the lower ear edges (count, depth, width)
EAR_ST, EAR_RING = 40, 24
# ---- face decals (closed, conforming): positions cast horizontally onto the shell from the head axis at (azimuth from
#   the front, z). SIDE view eye: pale rim x 773-824 / y 381-452 (centre Y -1.37 page, z 3.80), foreshortened; FRONT
#   (3/4) eye rim 0.60 wide x 0.71 tall. Azimuth from the page position corrected for the ~20 deg camera turn.
HEAD_AXIS_Y = HEAD_C[1]
EYE_AZ, EYE_Z = 48.0, 3.84            # "eye placement" (mirrored)
EYE_RIM_R, EYE_IRIS_R, EYE_HI_R = 0.32, 0.255, 0.06   # "eye size": pale rim / dark iris / highlight radii
EYE_SQUASH = (1.12, 1.0)              # eyes a touch taller than wide (front 3/4 check 0.71 / 0.60)
EYE_IRIS_OFF = (-3.0, 0.015)          # iris centre offset from the rim centre (az deg, z): the pale rim shows behind
EYE_HI_OFF = (-9.0, 0.11)             # highlight offset (upper front)
NOSTRIL_X, NOSTRIL_Z, NOSTRIL_R = 0.11, 3.58, 0.062   # the two nostrils on the snout face
NOSTRIL_SQUASH, NOSTRIL_TILT = (1.45, 1.0), 0.30      # tall ovals, tops splayed outward
FANG_X, FANG_Y = 0.20, -2.02          # "fangs": under the snout's back edge (side view fang x 718-735 / y 470-500)
FANG_LEN, FANG_R = 0.24, 0.062        # fang length / root radius (blunt tip)
DECAL_UP, DECAL_DN, DECAL_DOME = 0.010, 0.03, 0.022   # decal top offset / sink / dome height
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands)
HIPS_BAND = (0.55, 1.15)              # spine -> hips blend along Y on the torso
CHEST_BAND = (0.15, -0.55)            # spine -> chest blend along Y
NECK_BAND = (2.55, 3.05)              # chest -> neck blend (z) on the neck + ruff
HEAD_BAND = (3.05, 3.40)              # neck -> head blend (z)
PAW_BAND = (0.30, 0.52)               # paw -> lower leg blend (z)
KNEE_BAND = {"F": (1.05, 1.35), "B": (0.90, 1.20)}   # lower -> upper leg blend (z)
LEG_TOP = {"F": (1.85, 2.20), "B": (1.75, 2.25)}     # upper leg -> body blend at the leg tops (z)
OWN_TAU = 0.06                        # soft part ownership for weights
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent not yet given (N1)
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.018                        # "breathing": chest scale +- (2 breaths per loop)
IDLE_HEAD = (1.5, 3.0)                # head nod (2 cyc) / slow look (1 cyc), deg
EAR_IDLE = (1.5, 3.0, 1, 0.7)         # ear sway (root deg, tip deg, cyc, lag)
EAR_TWITCH = {"L": (0.30, 10.0), "R": (0.72, 9.0)}   # "ear twitch": de-synced per ear (loop phase, deg)
TWITCH_W, TWITCH_HZ = 0.035, 12       # twitch pulse width (loop fraction), flutter harmonic (3 Hz)
TAIL_WAG = (7.0, 12.0, 2, 0.8)        # "tail wag" (root, tip deg, cyc, lag)
WALK_N = 24                           # "trot loop": 1 s = one full stride
TROT_PHASE = {"BL": 0.0, "FR": 0.10, "BR": 0.50, "FL": 0.60}   # 4 beats: the diagonal pairs a tenth of a loop apart
TROT_SWING = (13.0, 15.0)             # fore-aft leg swing, deg (front, hind)
TROT_LIFT = (0.16, 0.14)              # paw lift at mid-swing (wrist / hock height gain), sheet units (front, hind)
TROT_CURL = 10.0                      # the lifted paw curls its toes down (x lift^3), deg
TROT_BOB = 0.035                      # the body (spine) bobs up twice per stride (the hips + legs stay: paws planted)
TROT_ROLL, TROT_PITCH = 2.0, 1.5      # body roll (1 cyc) / pitch (2 cyc), deg
EAR_TRAIL = (5.0, 0.08)               # ears dip back over each bob, deg, lag per bone (loop fraction)
TAIL_TROT = (6.0, 9.0)                # tail sway root / tip deg (1 cyc)
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
          "design/reference/canine_bat-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
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
_tp = SD.catmull(np.array(TORSO_PROFILE, float), 10)
_tp = _tp[np.argsort(_tp[:, 0], kind="stable")]
TY = _tp[:, 0]
TZC = 0.5 * (_tp[:, 1] + _tp[:, 2])
TRZ = np.clip(0.5 * (_tp[:, 2] - _tp[:, 1]), 0.0, None)
TRX = np.clip(_tp[:, 3], 0.0, None)
TRX[0] = TRX[-1] = TRZ[0] = TRZ[-1] = 0.0


def torso_at(y):
    return (np.interp(y, TY, TZC), np.interp(y, TY, TRZ, left=0.0, right=0.0), np.interp(y, TY, TRX, left=0.0, right=0.0))


def sd_torso(P):
    zc, rz, rx = torso_at(P[:, 1])
    q = np.hypot(P[:, 0] / np.maximum(rx, 1e-3), (P[:, 2] - zc) / np.maximum(rz, 1e-3))
    d = (q - 1.0) * np.maximum(0.5 * (rx + rz), 1e-3)
    return np.maximum(d, np.maximum(TY[0] - P[:, 1], P[:, 1] - TY[-1]))


def sd_neck(P):
    return SD.sd_round_cone(P, *NECK)


def sd_head(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R)


def sd_snout(P):
    d = SD.sd_round_cone(P, SNOUT_A, SNOUT_B, SNOUT_R, SNOUT_R)
    return SD.smax(d, SNOUT_FACE_Y - P[:, 1], SNOUT_EDGE)


def mir(p, sg):
    return np.array([sg * p[0], p[1], p[2]], float)


def cones_sdf(P, cones):
    d = np.full(len(P), 1e9)
    for a, b, r1, r2 in cones:
        d = np.minimum(d, SD.sd_round_cone(P, a, b, r1, r2))
    return d


def cones_box(cones, pad=0.0):
    A = np.array([c[0] for c in cones] + [c[1] for c in cones])
    R = max(max(c[2], c[3]) for c in cones)
    return A.min(0) - R - pad, A.max(0) + R + pad


SIDES = (("L", 1.0), ("R", -1.0))
LEGS = {}                                         # "FL" / "FR" / "BL" / "BR"
for fb, knots, rads, paw in (("F", LEG_FRONT, LEG_FRONT_R, PAW_FRONT), ("B", LEG_HIND, LEG_HIND_R, PAW_HIND)):
    for s, sg in SIDES:
        K_ = [mir(p, sg) for p in knots]
        pc = mir(paw[0], sg)
        LEGS[fb + s] = {"fb": fb, "side": s, "sg": sg, "knots": K_, "r": list(rads),
                        "cones": [(K_[i], K_[i + 1], rads[i], rads[i + 1]) for i in range(len(K_) - 1)],
                        "paw": pc, "paw_r": np.array(paw[1], float), "toes": []}
LEG_NAMES = ["FL", "FR", "BL", "BR"]


def sd_leg(P, n, parts=("cones", "paw", "toes")):
    L_ = LEGS[n]
    d = np.full(len(P), 1e9)
    if "cones" in parts:
        d = np.minimum(d, cones_sdf(P, L_["cones"]))
    if "paw" in parts:
        d = np.minimum(d, SD.sd_ellipsoid(P, L_["paw"], L_["paw_r"]))
    if "toes" in parts:
        for c, r in L_["toes"]:
            d = np.minimum(d, SD.sd_sphere(P, c, r))
    return d


TAIL_P = [np.array(p, float) for p in TAIL_PTS]
TAIL_CONES = [(TAIL_P[i], TAIL_P[i + 1], TAIL_RAD[i], TAIL_RAD[i + 1]) for i in range(len(TAIL_P) - 1)]


def sd_tail(P):
    return cones_sdf(P, TAIL_CONES)


# ---- the RUFF: collar puffs round the neck (an ellipse, lower at the front) + the chest bib
RC = np.array(RUFF_C, float)


def ruff_ring(th):
    """collar point + outward radial at azimuth th (0 = the front, + toward +X)"""
    zt = RUFF_Z[0] + (RUFF_Z[1] - RUFF_Z[0]) * 0.5 * (1.0 - math.cos(th))
    p = np.array([RC[0] + RUFF_AX[0] * math.sin(th), RC[1] - RUFF_AX[1] * math.cos(th), zt])
    return p, unit([math.sin(th) / RUFF_AX[0], -math.cos(th) / RUFF_AX[1], 0.0])


RUFF_BLOBS = []                                   # (centre, radius)
for i in range(RUFF_PUFFS):
    th = 2 * math.pi * (i + 0.5 * FP.hash01(i, 2.2)) / RUFF_PUFFS
    p, _ = ruff_ring(th)
    RUFF_BLOBS.append((p, RUFF_PUFF_R[0] + (RUFF_PUFF_R[1] - RUFF_PUFF_R[0]) * FP.hash01(i, 3.1)))
RUFF_BLOBS += [(np.array(c, float), r) for c, r in RUFF_BIB]


def sd_ruff_core(P):
    d = np.full(len(P), 1e9)
    for c, r in RUFF_BLOBS:
        d = np.minimum(d, SD.sd_sphere(P, c, r))
    return d


# ---- soft lobes (deterministic): (group, root a, tip b, r_root, r_tip, k). Gate 2026-10-09: the fur reads from the
#      silhouette + the bake only -- no scattered tufts; the ruff is a few broad petals, the head a low blended fringe.
TUFTS = []


def head_point(d):
    """point on the head ellipsoid along direction d from its centre + its outward normal"""
    d = unit(d)
    R = np.array(HEAD_R)
    s = 1.0 / np.linalg.norm(d / R)
    p = np.array(HEAD_C) + d * s
    return p, unit((p - np.array(HEAD_C)) / R ** 2)


for i in range(RUFF_PETALS):                          # collar petals: broad, overlapping, out + slightly down
    th = 2 * math.pi * (i + 0.5 + 0.2 * (FP.hash01(i, 6.6) - 0.5)) / RUFF_PETALS
    p, rad = ruff_ring(th)
    back = 0.5 * (1.0 - math.cos(th))                 # behind the head the collar lies along the back: petals flow back
    d_ = unit(rad * (1.0 - RUFF_PETAL_DROP) + np.array([0.0, 0.35 * back, -RUFF_PETAL_DROP]))
    L_ = RUFF_PETAL_LEN * (0.88 + 0.24 * FP.hash01(i, 7.1))
    TUFTS.append(("ruff", p, p + d_ * L_, RUFF_PETAL_R[0], RUFF_PETAL_R[1], RUFF_PETAL_K))
for a_, b_ in RUFF_BIB_PETALS:
    TUFTS.append(("ruff", np.array(a_, float), np.array(b_, float), RUFF_PETAL_R[0] * 1.1, RUFF_PETAL_R[1] * 1.1,
                  RUFF_PETAL_K))
for s, sg in SIDES:                                   # the low blended head fringe (silhouette only)
    for az_, rise in HEAD_FRINGE:
        az = math.radians(az_)
        S_, n_ = head_point([sg * math.sin(az), -math.cos(az), rise])
        TUFTS.append(("head", S_ - n_ * (HEAD_FRINGE_R - HEAD_FRINGE_OUT), S_ - n_ * (HEAD_FRINGE_R - HEAD_FRINGE_OUT - 0.04),
                      HEAD_FRINGE_R, HEAD_FRINGE_R * 0.9, HEAD_FRINGE_K))
TUFT_GROUPS = sorted({t[0] for t in TUFTS})


def tufts_sdf(P, group):
    return cones_sdf(P, [(a, b, r1, r2) for g_, a, b, r1, r2, _ in TUFTS if g_ == group]) if any(
        t[0] == group for t in TUFTS) else np.full(len(P), 1e9)


GROUPS = ["torso", "neck", "head", "snout", "ruff"] + ["leg." + n for n in LEG_NAMES] + ["tail"]
GROUP_REGION = {g_: "fur" for g_ in GROUPS}
GROUP_REGION.update({"snout": "snout", "ruff": "ruff"})


def group_sdf(P, tufts=True):
    cols = []
    for g_ in GROUPS:
        if g_ == "torso":
            d = sd_torso(P)
        elif g_ == "neck":
            d = sd_neck(P)
        elif g_ == "head":
            d = sd_head(P)
        elif g_ == "snout":
            d = sd_snout(P)
        elif g_ == "ruff":
            d = sd_ruff_core(P)
        elif g_.startswith("leg."):
            d = sd_leg(P, g_[4:])
        else:
            d = sd_tail(P)
        if tufts:
            d = np.minimum(d, tufts_sdf(P, g_))
        cols.append(d)
    return np.stack(cols, 1)


g = SD.Grid((-1.60, -2.45, -0.05), (1.60, 2.45, 5.00), SDF_STEP, SDF_BAND)
g.apply(sd_torso, (-1.0, TY[0], 1.3), (1.0, TY[-1], 3.0), 0.0)
g.apply(sd_neck, *cones_box([NECK]), NECK_K)
g.apply(sd_head, np.array(HEAD_C) - HEAD_R, np.array(HEAD_C) + HEAD_R, HEAD_K)
g.apply(sd_snout, np.array(SNOUT_B) - SNOUT_R - 0.05, np.array(SNOUT_A) + SNOUT_R + 0.05, SNOUT_K)
for n in LEG_NAMES:
    L_ = LEGS[n]
    for cone in L_["cones"]:
        g.apply(lambda P, cone=cone: SD.sd_round_cone(P, *cone), *cones_box([cone]), LEG_K)
    g.apply(lambda P, n=n: SD.sd_ellipsoid(P, LEGS[n]["paw"], LEGS[n]["paw_r"]), L_["paw"] - L_["paw_r"],
            L_["paw"] + L_["paw_r"], PAW_K)
for cone in TAIL_CONES:
    g.apply(lambda P, cone=cone: SD.sd_round_cone(P, *cone), *cones_box([cone]), TAIL_K)
for c, r in RUFF_BLOBS:
    g.apply(lambda P, c=c, r=r: SD.sd_sphere(P, c, r), c - r, c + r, RUFF_K)
for g_, a, b, r1, r2, k_t in TUFTS:
    g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2),
            np.minimum(a, b) - r1, np.maximum(a, b) + r1, k_t)
for n in LEG_NAMES:                                   # 3 toe creases carved across each paw front
    L_ = LEGS[n]; pc, pr = L_["paw"], L_["paw_r"]
    for dx in CREASES:
        x_ = pc[0] + dx * pr[0] / 0.30
        c0 = np.array([x_, pc[1] - 0.45 * pr[1], pc[2] + 0.85 * pr[2]])
        c1 = np.array([x_, pc[1] - 1.05 * pr[1], 0.0])
        g.apply(lambda P, c0=c0, c1=c1: SD.sd_round_cone(P, c0, c1, CREASE_R, CREASE_R),
                np.minimum(c0, c1) - CREASE_R, np.maximum(c0, c1) + CREASE_R, CREASE_K, mode="subtract")
g.floor(0.0)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "tufts": {gg: sum(1 for t in TUFTS if t[0] == gg) for gg in TUFT_GROUPS},
                 "ruff_puffs": len(RUFF_BLOBS), "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
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


LEG_XY = np.array([LEGS[n]["paw"][:2] for n in LEG_NAMES])


def light_field(P):
    """> 0 where the fur is LIGHT: per leg (the nearest paw column) the edge height runs from the outer face (OUT) up to
    the inner / back direction (IN), with soft rounded scallops round the leg; max'ed with the belly patch."""
    d2 = ((P[:, None, :2] - LEG_XY[None]) ** 2).sum(2)
    near = np.argmin(d2, 1)
    out = np.full(len(P), -1e3)
    for j, n in enumerate(LEG_NAMES):
        m = near == j
        if not m.any():
            continue
        L_ = LEGS[n]; sg = L_["sg"]
        zo, zi, bk = LIGHT_FRONT if L_["fb"] == "F" else LIGHT_HIND
        ax = np.array(L_["knots"][-1][:2])
        dxy = P[m, :2] - ax
        az = np.arctan2(dxy[:, 1], dxy[:, 0])
        inner = unit([-sg, bk])
        cosv = (dxy @ inner) / np.maximum(np.linalg.norm(dxy, axis=1), 1e-6)
        w = smoothstep(-0.2, 1.0, cosv)
        fr = ((az + math.pi) / (2 * math.pi) * LIGHT_SCALLOPS + 0.37 * j) % 1.0   # integer count: continuous at +-pi
        H = zo + (zi - zo) * w + LIGHT_SCALLOP_A * np.sqrt(np.clip(1.0 - (2.0 * fr - 1.0) ** 2, 0.0, 1.0))
        out[m] = H - P[m, 2]
    yf, yh = LEG_FRONT[0][1], LEG_HIND[0][1]
    belly = (BELLY_Z - P[:, 2] - BELLY_SLOPE * np.maximum(np.abs(P[:, 0]) - BELLY_HW, 0.0)
             - BELLY_SLOPE * np.maximum(np.maximum(yf - P[:, 1], P[:, 1] - yh), 0.0))
    return np.maximum(out, belly)


DGR = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["light"] = light_field(LV)
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
fur_side = lambda a, b: all(v[LAY["f_ruff"]] < TOL and v[LAY["f_snout"]] < TOL for v in (a, b))
CUTS = [("f_snout", 0.0, None), ("f_ruff", 0.0, None), ("light", 0.0, fur_side)]
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
shell_reg[(shell_reg == "fur") & (FVAL["light"] > 0)] = "fur_light"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: ears, eyes, nostrils, fangs
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=6.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


def project(pts, n):
    return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])


def face_anchor(az_deg, z):
    """horizontal ray from outside toward the head axis at azimuth az (0 = front, + toward +X) -> surface point + a
    smoothed normal (mean of a small ring of hits)."""
    az = math.radians(az_deg)
    dvec = np.array([math.sin(az), -math.cos(az), 0.0])
    ax = np.array([0.0, HEAD_AXIS_Y, z])
    S, _ = cast(ax + dvec * 3.0, -dvec)
    ns = []
    for da, dz in ((0, 0), (4, 0), (-4, 0), (0, 0.04), (0, -0.04)):
        a2 = math.radians(az_deg + da)
        d2 = np.array([math.sin(a2), -math.cos(a2), 0.0])
        ns.append(cast(np.array([0.0, HEAD_AXIS_Y, z + dz]) + d2 * 3.0, -d2)[1])
    return S, unit(np.mean(ns, 0))


PARTS = []
FACE = {}
for s, sg in SIDES:
    S, n = face_anchor(sg * EYE_AZ, EYE_Z)
    V, F, R = FP.dome_decal(S, n, EYE_RIM_R, project, DECAL_UP, DECAL_DN, DECAL_DOME, 4, 24, "eye_rim", squash=EYE_SQUASH)
    PARTS.append(FP.Part("eye_rim." + s, V, F, R, bone="head"))
    S2, n2 = face_anchor(sg * (EYE_AZ + EYE_IRIS_OFF[0]), EYE_Z + EYE_IRIS_OFF[1])
    V, F, R = FP.dome_decal(S2, n2, EYE_IRIS_R, project, DECAL_UP + 0.022, DECAL_DN, DECAL_DOME * 1.6, 4, 24, "eye",
                            squash=EYE_SQUASH)
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    S3, n3 = face_anchor(sg * (EYE_AZ + EYE_HI_OFF[0]), EYE_Z + EYE_HI_OFF[1])
    V, F, R = FP.dome_decal(S3, n3, EYE_HI_R, project, DECAL_UP + 0.022 + DECAL_DOME * 1.6 + 0.008, DECAL_DN, 0.008, 2, 12,
                            "eye_hi")
    PARTS.append(FP.Part("eye_hi." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": n.round(4).tolist()}
    Sn, nn = cast((sg * NOSTRIL_X, SNOUT_FACE_Y - 1.0, NOSTRIL_Z), (0, 1, 0), 2.0)
    V, F, R = FP.dome_decal(Sn, unit(nn + np.array([0, -1.0, 0])), NOSTRIL_R, project, 0.006, DECAL_DN, 0.004, 2, 14,
                            "nostril", squash=NOSTRIL_SQUASH, up_hint=(sg * NOSTRIL_TILT, 0.0, 1.0))
    PARTS.append(FP.Part("nostril." + s, V, F, R, bone="head"))
    FACE["nostril." + s] = {"surface": Sn.round(4).tolist()}
    Sf, _ = cast((sg * FANG_X, FANG_Y, 2.6), (0, 0, 1), 1.5)
    root = Sf + np.array([0.0, 0.0, 0.05])
    tip = Sf + np.array([0.0, -0.03, -FANG_LEN])
    ax_ = unit(root - tip)
    Lf = float(np.linalg.norm(root - tip))
    prof = [(0.0, 0.03), (FANG_R, 0.0), (FANG_R * 0.85, -0.25 * Lf), (FANG_R * 0.45, -0.7 * Lf), (FANG_R * 0.18, -0.95 * Lf),
            (0.0, -Lf)]
    V, F, R = FP.lathe(prof, "fang", 10, root, ax_, up_hint=(0, 1, 0))   # h < 0 runs from the root toward the tip
    PARTS.append(FP.Part("fang." + s, V, F, R, bone="head"))
    FACE["fang." + s] = {"root": root.round(4).tolist(), "tip": tip.round(4).tolist()}


def ear_region(c, s_, sf):
    if sf < EAR_BASE_SF:
        return "ear_back"
    if abs(c) > EAR_RIM_C or sf > 0.97:
        return "ear_rim"
    if s_ < 0:
        return "ear_back"
    for vc, s0, s1 in EAR_VEINS:
        if abs(c - vc) < 0.05 and s0 <= sf <= s1:
            return "ear_vein"
    return "ear_inner"


EAR = {}
for s, sg in SIDES:
    root = mir(EAR_ROOT, sg)
    tip = mir(EAR_TIP, sg)
    mid = 0.5 * (root + tip) + mir(EAR_BOW, sg)
    tt = np.linspace(0, 1, EAR_ST)[:, None]
    spine = (1 - tt) ** 2 * root + 2 * (1 - tt) * tt * mid + tt ** 2 * tip
    al = SD.arclen(spine); sf = al / al[-1]
    shape = np.sin(math.pi * np.clip(sf, 0, 1) ** EAR_WIDE_AT)
    shape[0] = shape[-1] = 0.0
    hw_l = EAR_W * shape * FP.soft_fringe(sf, 0.10, 0.40, EAR_FRINGE[0], EAR_FRINGE[1], EAR_FRINGE[2], 11.0 + sg)
    hw_r = EAR_W * shape * FP.soft_fringe(sf, 0.10, 0.40, EAR_FRINGE[0], EAR_FRINGE[1], EAR_FRINGE[2], 23.0 + sg)
    ht = EAR_T * np.sin(math.pi * np.clip(sf, 0, 1) ** 0.6) ** 0.6
    yaw = math.radians(EAR_YAW)
    V, F, R, S_, C_ = FP.membrane(spine, (sg * math.sin(yaw), -math.cos(yaw), 0.0), hw_l, hw_r, ht, EAR_RING, EAR_CUP,
                                  ear_region)
    p = FP.Part("ear." + s, V, F, R)
    Sb, _ = cast(root + np.array([0, 0, 2.0]), (0, 0, -1), 3.0)
    s_surf = float(np.clip(SD.arclen(spine)[np.argmin(np.linalg.norm(spine - Sb, axis=1))], 0.0, al[-1] * 0.5))
    p.chain = ("ear." + s, S_ * al[-1], al[-1], "head", 2, s_surf)
    PARTS.append(p)
    EAR[s] = {"spine": C_, "length": float(al[-1]), "root": root, "tip": tip}

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
REG = ["fur", "fur_light", "ruff", "snout", "nostril", "ear_back", "ear_inner", "ear_rim", "ear_vein", "eye_rim", "eye",
       "eye_hi", "fang"]
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
report["glow"] = {"skin": "default", "glowing_regions": _lit, "rule": "no glow (brief): every region writes zero Glow"}
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

# facing landmark: the head centre -> the snout face centre (midline)
HEAD_CTR = np.array(HEAD_C, float)
SNOUT_TIP = np.array([0.0, SNOUT_FACE_Y, 0.5 * (SNOUT_A[2] + SNOUT_B[2])])
anchor = HEAD_CTR - SHIFT
landmark = SNOUT_TIP - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the snout face centre on the midline",
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
    key = {"eye_rim": "eyes", "eye": "eyes", "eye_hi": "eyes", "ear": "ears", "nostril": "nostrils",
           "fang": "fangs"}[p.name.split(".")[0]]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The shell carries the fur silhouette (ruff "
                            "fringe, cheeks, ear-base fur, belly fringe, haunches, lower-leg fluff) and the bake carries "
                            "the tufts at full density; the two oversized membrane ears are the next biggest share."
                            % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, ears, decals, fangs)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"back_view_height_px": 605, "back_view_width_px": 266, "side_view_length_px": 446},
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
low["conquest_source"] = "from scratch: design/reference/canine_bat-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_ear_pts = VALL[RANGE["ear.L"][0]:RANGE["ear.L"][1]]
_paw = LEGS["BL"]["paw"]
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-1.05, -2.35, 2.85]) - SHIFT).tolist(), (np.array([1.05, -1.2, 4.55]) - SHIFT).tolist()],
    "ear": [_ear_pts.min(0).tolist(), _ear_pts.max(0).tolist()],
    "ruff": [(np.array([-1.0, -1.6, 1.6]) - SHIFT).tolist(), (np.array([1.0, 0.3, 3.2]) - SHIFT).tolist()],
    "paw_tail": [(np.array([-0.2, 0.6, 0.0]) - SHIFT).tolist(), (np.array([_paw[0] + 0.4, 2.3, 2.3]) - SHIFT).tolist()],
    "views": {"face": [-20.0, 5.0], "ear": [55.0, 10.0], "ruff": [-35.0, 5.0], "paw_tail": [130.0, 10.0]},
    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

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
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets, every fur tuft at full density) + every closed part",
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


SPINE_PTS = {"hips": ((0.0, 1.55, 2.22), (0.0, 0.85, 2.26)), "spine": ((0.0, 0.85, 2.26), (0.0, -0.15, 2.30)),
             "chest": ((0.0, -0.15, 2.30), (0.0, -0.90, 2.52)), "neck": ((0.0, -0.90, 2.52), (0.0, -1.00, 3.30)),
             "head": ((0.0, -1.00, 3.30), (0.0, -1.08, 4.60))}
BONES = [("hips", P_(SPINE_PTS["hips"][0]), P_(SPINE_PTS["hips"][1]), "root"),
         ("spine", P_(SPINE_PTS["spine"][0]), P_(SPINE_PTS["spine"][1]), "hips"),
         ("chest", P_(SPINE_PTS["chest"][0]), P_(SPINE_PTS["chest"][1]), "spine"),
         ("neck", P_(SPINE_PTS["neck"][0]), P_(SPINE_PTS["neck"][1]), "chest"),
         ("head", P_(SPINE_PTS["head"][0]), P_(SPINE_PTS["head"][1]), "neck")]
for n in LEG_NAMES:
    L_ = LEGS[n]
    k0, k1, k2 = L_["knots"]
    toe = np.array([L_["paw"][0], L_["paw"][1] + TOE_DY - 0.06, 0.14])
    BONES += [("leg.%s.0" % n, P_(k0), P_(k1), "hips"), ("leg.%s.1" % n, P_(k1), P_(k2), "leg.%s.0" % n),
              ("leg.%s.2" % n, P_(k2), P_(toe), "leg.%s.1" % n)]
BONES += [("tail.0", P_(TAIL_P[0]), P_(TAIL_P[1]), "hips"), ("tail.1", P_(TAIL_P[1]), P_(TAIL_P[2]), "tail.0")]
for s, _ in SIDES:
    C_ = EAR[s]["spine"]; al = SD.arclen(C_)
    pts = [np.array([np.interp(x, al, C_[:, k]) for k in range(3)]) for x in np.linspace(0, al[-1], 3)]
    BONES += [("ear.%s.0" % s, P_(pts[0]), P_(pts[1]), "head"), ("ear.%s.1" % s, P_(pts[1]), P_(pts[2]), "ear.%s.0" % s)]
CONNECT = {"chest", "neck", "head", "tail.1", "ear.L.1", "ear.R.1"} | {"leg.%s.%d" % (n, k) for n in LEG_NAMES for k in (1, 2)}
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
    e.use_connect = nm in CONNECT                  # spine stays unconnected: it carries the trot's body bob
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
yy, zz = Pw[:, 1], Pw[:, 2]
Ws = np.zeros((b_ - a_, len(DEFORM)))
gi = GROUPS.index("torso")
wh = smoothstep(HIPS_BAND[0], HIPS_BAND[1], yy)
wc = smoothstep(CHEST_BAND[0], CHEST_BAND[1], yy)
Ws[:, J["hips"]] += Wg[:, gi] * wh
Ws[:, J["chest"]] += Wg[:, gi] * wc
Ws[:, J["spine"]] += Wg[:, gi] * np.clip(1.0 - wh - wc, 0, None)
wn = smoothstep(NECK_BAND[0], NECK_BAND[1], zz)
whd = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
for gname in ("neck", "ruff"):
    gi = GROUPS.index(gname)
    Ws[:, J["chest"]] += Wg[:, gi] * (1 - wn)
    Ws[:, J["neck"]] += Wg[:, gi] * wn * (1 - whd)
    Ws[:, J["head"]] += Wg[:, gi] * wn * whd
for gname in ("head", "snout"):
    Ws[:, J["head"]] += Wg[:, GROUPS.index(gname)]
LIMB_W = {}
for n in LEG_NAMES:
    gi = GROUPS.index("leg." + n)
    fb = LEGS[n]["fb"]
    wp = smoothstep(PAW_BAND[1], PAW_BAND[0], zz)
    wk = smoothstep(KNEE_BAND[fb][0], KNEE_BAND[fb][1], zz)
    wt = smoothstep(LEG_TOP[fb][0], LEG_TOP[fb][1], zz)
    body = "chest" if fb == "F" else "hips"
    Ws[:, J["leg.%s.2" % n]] += Wg[:, gi] * wp
    Ws[:, J["leg.%s.1" % n]] += Wg[:, gi] * (1 - wp) * (1 - wk)
    Ws[:, J["leg.%s.0" % n]] += Wg[:, gi] * (1 - wp) * wk * (1 - wt)
    Ws[:, J[body]] += Wg[:, gi] * (1 - wp) * wk * wt
    LIMB_W["leg." + n] = {"dominant_verts": int((np.argmax(Wg, 1) == gi).sum())}
gi = GROUPS.index("tail")
_, s_arc, _ = FP.spine_param(Pw, np.array(TAIL_P))
L_ = float(SD.arclen(np.array(TAIL_P))[-1])
Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, 2)
Ws[:, J["hips"]] += Wg[:, gi] * Wv[:, 0]
Ws[:, J["tail.0"]] += Wg[:, gi] * Wv[:, 1]
Ws[:, J["tail.1"]] += Wg[:, gi] * Wv[:, 2]
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if p.chain:
        chain, s_arc, L_, parent, nb, s0 = p.chain
        Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, nb, parent_s0=s0)
        Wt[a_:b_, J[parent]] += Wv[:, 0]
        for k in range(nb):
            Wt[a_:b_, J["%s.%d" % (chain, k)]] += Wv[:, k + 1]
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
                  "limbs": LIMB_W,
                  "rule": "shell: soft SDF ownership (tau %.2f) between torso, neck, head, snout, ruff, 4 legs, tail; the "
                          "torso runs hips (Y > HIPS_BAND) -> spine -> chest (Y < CHEST_BAND); neck + ruff chest -> neck -> "
                          "head by z bands; legs: paw below PAW_BAND, lower, upper, top blending into chest (front) / hips "
                          "(hind); tail: rigkit.vine_weights; ears: vine_weights from the head surface; eyes, nostrils, "
                          "fangs rigid on head" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame with no action bound (pass 1, floor
#      guard lifting the hips if any vertex dips below z 0), keyed from the stored channels (pass 2, frame N+1 = frame 1),
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
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
    return Rw


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ world_rot(rots) @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def wrapd(u, u0):
    d = (u - u0) % 1.0
    return d - 1.0 if d > 0.5 else d


def ears_idle(P, u):
    for s, sg in SIDES:
        ph = 0.0 if s == "L" else 2.1
        u0, amp = EAR_TWITCH[s]
        pulse = math.exp(-(wrapd(u, u0) / TWITCH_W) ** 2)
        for k in range(2):
            bn = "ear.%s.%d" % (s, k)
            sway = K.vine_wave(u, k, 2, EAR_IDLE[0], EAR_IDLE[1], EAR_IDLE[3], EAR_IDLE[2], ph)
            tw = amp * (0.6 + 0.4 * k) * pulse * math.sin(TAU * TWITCH_HZ * u - 0.8 * k)
            P[bn] = (None, [("X", sway + tw), ("Z", sg * (0.6 * sway + 0.8 * tw))], None)


def idle_pose(u):
    P = {}
    br = math.sin(TAU * 2 * u)
    sc = (1 + BREATH * br, 1 + 0.4 * BREATH * br, 1 + BREATH * br)
    P["chest"] = (None, [], sc)
    P["neck"] = (None, [], (1 / sc[0], 1 / sc[1], 1 / sc[2]))      # the head does not breathe
    P["head"] = (None, [("X", IDLE_HEAD[0] * math.sin(TAU * 2 * u - 0.9)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u + 0.3))], None)
    ears_idle(P, u)
    for k in range(2):
        bn = "tail.%d" % k
        wag = K.vine_wave(u, k, 2, TAIL_WAG[0], TAIL_WAG[1], TAIL_WAG[3], TAIL_WAG[2], 0.3)
        P[bn] = (None, [("Y", wag)], None)            # the hanging tail swings side to side about the Y axis
    return P, {"lift": 0.0}


def _rx(v, deg):
    a = math.radians(deg)
    return np.array([v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a)])


_KC = {}


def knee_comp(n, r0, dz):
    """the lower-leg rotation (deg) that puts the wrist / hock at its rest height + dz when the upper leg swings r0 about
    the shoulder / hip (in stance dz = 0: a slanted rest leg passing through vertical would otherwise dip its paw below
    the floor; in the swing dz > 0 lifts the paw). A fine deterministic scan in the leg's (Y, z) plane over the roots that
    keep the lower leg folded BACKWARD (never tipped forward past min(rest, vertical)); the smallest-|r1| root wins."""
    key = (n, round(r0, 6), round(dz, 6))
    if key not in _KC:
        k0, k1, k2 = [np.array(k)[1:] for k in LEGS[n]["knots"]]
        up, lo_ = k1 - k0, k2 - k1
        rest_ang = math.degrees(math.atan2(lo_[0], -lo_[1]))          # + = the paw behind the knee
        cand = np.linspace(-40.0, 80.0, 12001)
        ok = rest_ang + r0 + cand >= min(rest_ang, 0.0) - 1e-9
        z = np.array([(k0 + _rx(up, r0) + _rx(lo_, r0 + c))[1] for c in cand])
        err = np.abs(z - (k2[1] + dz)) + 1e-5 * np.abs(cand) + 1e3 * (~ok)
        _KC[key] = float(cand[int(np.argmin(err))])
    return _KC[key]


def trot_leg(n, u):
    """one leg of the in-place trot at loop phase u: fore-aft swing about the hip / shoulder (forward = -Y), the lower
    leg folds during the forward swing (the paw lifts), the paw stays level in stance and curls in the air."""
    fb = LEGS[n]["fb"]
    A = TROT_SWING[0 if fb == "F" else 1]
    LF_ = TROT_LIFT[0 if fb == "F" else 1]
    p = TAU * (u - TROT_PHASE[n])
    fwd = A * math.sin(p)                              # + = the paw forward
    lift = max(0.0, math.cos(p)) ** 1.5                # forward swing = the paw in the air
    r0 = -fwd                                          # +X rotation swings the paw toward +Y (back)
    r1 = knee_comp(n, r0, LF_ * lift)                  # stance: wrist / hock held at rest height; swing: lifted
    r2 = -(r0 + r1) + TROT_CURL * lift ** 3            # level paw; the toes curl only high in the swing (no toe dip)
    return {"leg.%s.0" % n: (None, [("X", r0)], None), "leg.%s.1" % n: (None, [("X", r1)], None),
            "leg.%s.2" % n: (None, [("X", r2)], None)}, lift


def walk_pose(u):
    """PLACEHOLDER in-place 4-beat trot (diagonal pairs BL+FR, BR+FL, each pair's front paw a tenth of a loop behind its
    hind paw = four footfalls). Root + hips still (the paws stay planted in stance); the spine carries the body bob
    (twice per stride), a small roll / pitch; head steadies; ears dip back on each bob; tail sways."""
    P = {}
    lifts = {}
    for n in LEG_NAMES:
        d, lifts[n] = trot_leg(n, u)
        P.update(d)
    bob = TROT_BOB * math.sin(TAU * 2 * u + 0.6) ** 2
    P["spine"] = ((0.0, 0.0, bob), [("Y", TROT_ROLL * math.sin(TAU * u)), ("X", TROT_PITCH * math.sin(TAU * 2 * u))], None)
    P["chest"] = (None, [("Y", -0.5 * TROT_ROLL * math.sin(TAU * u - 0.3))], None)
    P["head"] = (None, [("X", -TROT_PITCH * math.sin(TAU * 2 * u - 0.4))], None)
    for s, sg in SIDES:
        for k in range(2):
            lag = EAR_TRAIL[1] * (k + 1)
            P["ear.%s.%d" % (s, k)] = (None, [("X", EAR_TRAIL[0] * (0.6 + 0.4 * k) * math.sin(TAU * 2 * (u - lag) + 0.6))], None)
    for k in range(2):
        P["tail.%d" % k] = (None, [("Y", TAIL_TROT[k] * math.sin(TAU * (u - 0.1 * (k + 1))))], None)
    return P, {"lift": bob, "paw_lift": lifts}


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
        if bn == "hips":
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
_dom = np.argmax(Wt, 1)
SOLE = {n: np.nonzero(_dom == J["leg.%s.2" % n])[0] for n in LEG_NAMES}
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
        rows.append({"minz": float(C[:, 2].min()), **{"sole_" + n: float(C[SOLE[n], 2].min()) for n in LEG_NAMES},
                     "spine_z": float(pbs["spine"].head.z), "ear_tip_L": list(pbs["ear.L.1"].tail),
                     "tail_tip": list(pbs["tail.1"].tail), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("spine_z") - REST_H["spine"][2]
    et = np.array([r["ear_tip_L"] for r in R_]); tt_ = np.array([r["tail_tip"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "sole_min_z": {n: [round(float(g_("sole_" + n).min()), 4), round(float(g_("sole_" + n).max()), 4)]
                                   for n in LEG_NAMES},
                    "body_bob": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "ear_tip_L_p2p_xyz": (et.max(0) - et.min(0)).round(4).tolist(),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist()}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["trot"] = {"phase": TROT_PHASE, "swing_deg": TROT_SWING, "paw_lift": TROT_LIFT, "bob": TROT_BOB,
               "rule": "diagonal pairs (BL+FR, BR+FL), each pair's front paw 0.10 loop after its hind paw = 4 footfall "
                       "beats; root + hips still, the spine carries the bob", "seconds": round(time.time() - t_clip, 1)}
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("canine_bat v1: root > hips > spine > chest > neck > head > ear.{L,R}.{0,1} (jaw-free head); "
                       "hips > leg.{FL,FR,BL,BR}.{0,1,2} (2 = paw, never scaled); hips > tail.{0,1}")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist N1: movement intent not yet given). idle = 4 s: breathing (chest), "
                               "de-synced ear twitches, tail wag, small head nod / look, paws planted. walk = 1 s in-place "
                               "4-beat trot: diagonal pairs a tenth of a loop apart, paws never below z 0, root + hips "
                               "still, body bob on the spine, ears + tail trailing.")
low["conquest_locomotion"] = "walker: quadruped trot in place (the game glides the unit)"
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
              "structure": "armature identity + skinned 'canine_bat' (one opaque body material); natural scale; "
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
