"""Holly Snowman -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/holly_snowman_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md "2026-10-11 -- NEW UNIT: Holly Snowman" + the sheet design/reference/holly_snowman-character-
sheet.png (intake: design/reference/holly_snowman/intake.json, --cuts 470,880; axes front 250 / back 668 / side 1010).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render,
report). Style: design/character-style-guide.md.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. FRONT + SIDE are the true orthos:
  FRONT view (the unit's left on image right):   X = (x_px - 250) / 100   ; z = (759 - y_px) / 100
  SIDE view (the face on image LEFT = -Y):        Y = (x_px - 1010) / 100  ; z = (762 - y_px) / 100
X +ve = the unit's LEFT (L), front = -Y, floor z = 0. The SIDE view is drawn a little HIGHER than the front through the
torso (the arm root z 3.12 vs 2.91, the buttons 2.93 / 2.61 / 2.27 vs 2.77 / 2.37 / 1.98): heights follow the FRONT view,
depths (Y) the SIDE view; where a part needs both (the frond tips, the berry cluster) the two views' z are averaged and
the constant says so. The side axis 1010 is the FEET centre: the head (centre Y +0.19) and the belly (+0.27) sit behind
the feet as drawn.

HOUSE RULE (artist 2026-10-10): the rest pose is MIRROR-SYMMETRIC about X = 0. Every +X part is mirrored; the shell is
mirror-halved (its X >= 0 half + the mirror, welded); the puffs are placed on the X >= 0 half and mirrored.

Pipeline:
  1. BODY = ONE SDF: a round fluffy HEAD (a superellipsoid: the drawn head has a fuller lower edge than an ellipsoid) over
     a pear-shaped BODY (elliptic sections measured per row off the front + side views: narrow neck, wide round belly),
     two small rounded snow FEET; then the SCALLOPS: seeded soft rounded puffs (Poisson-spaced on the base surface,
     X-mirrored, three size zones head / body / feet) smooth-unioned in with a gentle blend -> the cloud-like outline.
     v1.2 (gate follow-up): the scallops are a VIEW-INDEPENDENT GIRTH FRINGE -- puffs live where the base normal is
     near-horizontal (|normal Z| small: the rims every horizontal view sees in silhouette) plus the head's lower collar;
     the top cap and the under-surfaces stay smooth, and the face + button column stay clear. Marching tets = the HIGH; collapse decimation = the LOW (smooth-rounded with a soft scalloped edge),
     mirror-halved.
  2. SNOW TONES as a smooth per-VERTEX colour gradient on the shell (one 'snow' region; corner colours blended, no
     region edges): snow_hi on up-facing surface fading through snow to snow_shade underneath (head underside, belly,
     feet), and a feathered blue BLUSH glow on the lower face. The grade weights live on the mesh ('snow_grade' point
     attribute) and repaint() re-blends them from any palette.
  3. CLOSED PARTS (holly_snowman_parts.py): two simple oval EYES (domes conforming to the head base field), two LEAF EARS
     (closed curved pillow leaves: two-tone halves split at the midrib + a light rim strip), the PLANT ARMS (a short stalk
     + six layered pointed frond leaves each), the BERRY CLUSTERS (8 spheres each, a round highlight dot on every berry),
     three red berry BUTTONS (flattened spheres pressed into the chest).
  4. palette -> Col (no glow), flat shaded, Smart UV, bake normal + AO (AO floor: no dark creases) from the SDF high +
     every closed part.
  5. RIG: root > body > head > ear_L / ear_R; body > arm_L / arm_R; root > foot_L / foot_R. Clips PLACEHOLDER (movement
     not given): idle = a soft breathing squash + ear / frond sway; walk = an in-place waddle hop (body roll + bob, the
     feet alternately lift and come back down to the floor; nothing slides below it). glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                     # noqa: E402  (read-only use)
import palettes as PAL                 # noqa: E402  (read-only use)
import magmoo_sdf as SD                # noqa: E402  (read-only use: the SDF kit)
import holly_snowman_parts as FP       # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "holly_snowman"
CHAR_ID = "holly_snowman"             # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
DEGEN_DIST = 1e-4                     # cut slivers shorter than this are collapsed
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.16                       # >= the largest smooth-union k (NECK_K 0.10) + 2 steps (contract hazard note)
BODY_TRIS = 12500                     # "body mesh detail": the shell's decimation target (no colour cuts: v1.1 grades)
# ---- HEAD (base surface = the drawn outline minus the scallop height ~0.07). FRONT rows (half-width incl. scallops):
#   z 4.49 1.05 | 4.19 1.15 | 4.09 1.16 | 3.79 1.17 (widest) | 3.49 1.02 | 3.39 1.00 | 3.29 0.90 | 3.19 0.73; top at the
#   centre y 257 (z 5.02, between the ears). SIDE rows: z 3.79 Y -0.84..+1.21 (centre +0.19, half-depth 1.03).
#   A plain ellipsoid under-fills the drawn lower edge (z 3.29: 0.76 vs 0.90): the head is a superellipsoid with
#   vertical exponents top 2.5 / bottom 3.0 (z 3.29 -> 0.95, 3.19 -> 0.79, 4.49 -> 1.05, 4.59 -> 0.98 incl. scallops)
HEAD_C = (0.0, 0.19, 3.95)            # "head centre"
HEAD_R = (1.09, 0.97)                 # "head size": half width X / half depth Y
HEAD_H = (0.96, 0.85)                 # "head height": top / bottom half-heights (top z 4.91 + scallops, bottom 3.10)
HEAD_P = (2.5, 3.0)                   # "head fullness": vertical exponents top / bottom (2 = an ellipsoid)
# ---- BODY: (z, half-width X (FRONT), half-depth Y (SIDE), centre Y (SIDE)) per measured row, scallops (0.06) removed.
#   FRONT half-widths: z 3.09 0.62 | 2.89 0.63 | 2.69 0.62 | 2.49 0.72 | 2.29 0.73 | 2.09 0.96 | 1.89 1.17 | 1.69 1.30 |
#   1.49 1.39 | 1.39 1.41 | 1.09 1.41 | 0.89 1.30 | 0.69 1.25 | 0.59 1.10. SIDE (z - 0.03 to the front floor) front / back
#   edges: 3.06 -0.47/0.86 | 2.66 -0.64/1.14 | 2.36 -0.75/1.32 | 2.06 -0.82/1.45 | 1.86 -1.01/1.62 | 1.56 -1.17/1.69 |
#   1.16 -1.16/1.68 | 0.96 -1.05/1.62 | 0.76 -0.99/1.56 | 0.56 -0.80/1.37 | 0.46 -0.61/1.31 | 0.36 -0.56/1.14. The belly
#   bottom between the feet: the front crotch apex y ~725 (z 0.34).
BODY = [(0.38, 0.72, 0.78, 0.31), (0.50, 1.00, 0.96, 0.31), (0.65, 1.17, 1.13, 0.28), (0.85, 1.24, 1.26, 0.28),
        (1.05, 1.34, 1.32, 0.27), (1.25, 1.34, 1.37, 0.26), (1.45, 1.33, 1.38, 0.27), (1.65, 1.25, 1.33, 0.27),
        (1.85, 1.11, 1.25, 0.30), (2.05, 0.95, 1.10, 0.33), (2.25, 0.72, 0.98, 0.32), (2.45, 0.66, 0.92, 0.28),
        (2.65, 0.58, 0.82, 0.25), (2.85, 0.57, 0.68, 0.23), (3.05, 0.56, 0.61, 0.20), (3.45, 0.45, 0.50, 0.20)]
BODY_BOT = 0.32                       # the belly's underside (inside the feet + the crotch blend)
NECK_K = 0.10                         # head / body blend (the drawn neck crease z ~3.1)
# ---- FEET (FRONT: x 141-235 / 270-366 at y 750 -> |X| 0.25..1.09 incl. scallops, top crease y ~715 (z 0.44); SIDE:
#   x 946-1077 -> Y -0.64..+0.67, centre +0.01)
FOOT_C = (0.67, 0.0, 0.15)            # "foot placement" (+X foot; mirrored)
FOOT_R = (0.40, 0.62, 0.30)           # "foot size" (the floor cut flattens the sole)
FOOT_K = 0.06                         # foot / belly blend (the drawn crease stays visible)
# ---- SCALLOPS ("fluffy, rounded shape"; the outline bumps measure 0.10-0.25 wide on the head, 0.15-0.35 on the belly,
#   standing 0.04-0.08 proud): seeded soft spheres on the base surface, gentle blend
PUFF_SEED = 3                         # "fluff pattern" (any integer re-rolls the scallops; deterministic)
PUFF_ZONES = {"head": ((0.14, 0.20), 0.34), "body": ((0.18, 0.26), 0.32), "feet": ((0.10, 0.14), 0.30)}
                                      # "scallop size" per zone: radius range, protrusion as a share of the radius
PUFF_SPACING = 1.3                    # scallop spacing (x the mean radius of the pair): the caps overlap (no polka dots)
PUFF_K = 0.06                         # "scallop softness": smooth-union k (fills the creases: soft, never cauliflower)
FRINGE = (0.12, 0.32)                 # "scallop girth": puff depth fades from full to 0 as |base normal Z| goes 0.12 -> 0.32
                                      #   (the sheet scallops each mass's outline in front, back and side alike: the
                                      #   near-horizontal girth is that outline from every horizontal angle); < 0.15 -> none
COLLAR = (-0.92, -0.78, -0.55, -0.42)  # "head collar": head-zone puffs also where normal Z is in this soft band (rise
                                      #   -0.92 -> -0.78, fall -0.55 -> -0.42): the drawn scalloped lower edge of the head
CAND_STEP = 0.04                      # candidate sampling step (a coarse polygonisation of the base surface)
FACE_CLEAR = (3.92, 0.70, 0.55, 0.35)  # puff-free FACE: front-projected ellipse (centre z, half X, half z) on front-facing
                                      #   surface (normal Y < -0.35): the eyes + blush sit on smooth ground
BUTTON_CLEAR = 0.24                   # puff-free disc round each button (front-projected radius)
# ---- SNOW TONES: a smooth per-vertex gradient from the base normal (v1.1: no region edges). The sheet paints a
#   view-centred blue with lighter rims in every view (a painterly effect no vertex colour can follow); the brief's reading
#   is built: highlight on the upper lit areas fading to shadow under the head / belly / feet
GRADE_HI = (0.25, 0.95)               # "snow highlight": weight 0 -> 1 as the base normal Z goes 0.25 -> 0.95
GRADE_SHADE = (0.0, 0.85)             # "snow shadow": weight 0 -> 1 as -normal Z goes 0.0 -> 0.85
GRADE_SHADE_MAX = 0.85                # shadow weight ceiling (the drawn under-head / lower belly reach ~143,175,252)
BLUSH = (3.86, 0.58, 0.46, 0.90)      # "blush glow": front-projected ellipse (centre z, half X, half z), peak weight; the
                                      #   drawn blue (face centre 144,176,252) spans x ~200-300, y ~330-425
BLUSH_CORE = 0.30                     # full strength inside this share of the ellipse radius, smooth fade to 0 at its rim
                                      #   (feather ~0.70 x 0.58 = 0.41 u across, ~0.32 u vertically)
FRONT_GATE = (0.30, 0.65)             # the blush fades in over -normal Y 0.30 -> 0.65 (front-facing only)
GRADE_TONES = ("snow_hi", "snow_shade", "blush")   # palette tones the gradient blends (no faces carry them)
# ---- FACE (FRONT eye blobs: centres (221.4, 352.5) / (279.2, 352.5), each 12 x 34 px)
EYE_X, EYE_Z = 0.289, 4.065           # "eye placement" (mirrored); the side view draws the eye at Y -0.43, inside its
                                      #   silhouette (deviation: the front placement is built)
EYE_HALF = (0.062, 0.17)              # "eye size": half width, half height
EYE_DEPTH, EYE_LIFT = 0.016, 0.006    # eye dome height, lift off the base surface
# ---- BUTTONS (FRONT blobs: centres x 250.4, y 481.7 / 521.8 / 561.0, 19-20 px; SIDE x 956 / 941 / 934 = they protrude
#   from the chest)
BUTTON_Z = (2.773, 2.372, 1.980)      # "button heights"
BUTTON_R, BUTTON_FLAT, BUTTON_SINK = 0.10, 0.80, 0.0   # radius; squash along the chest normal; pressed-in depth
LIGHT = (0.0, -0.55, 0.83)            # berry / button highlight direction (up-front: X-symmetric)
BERRY_SEG = (10, 7)                   # berry sphere segments, rings (highlight dot = the top ring cap, 0.38 r)
# ---- LEAF EARS (+X ear; mirrored). FRONT: tip (361, 170) -> X 1.11 z 5.89; base hidden in the head at x ~322 (X 0.72).
#   SIDE: tip (1095, 165) -> Y 0.85 z 5.97, base x ~1050 (Y 0.40); drawn width ~0.85 (front) / 0.90 (side): the leaf
#   faces diagonally (front-outward) so both views see its face; rim along the inner / front margin, the lighter half
#   on the rim side of the midrib (ear boxes: inner half 21,58,38 vs outer 14,42,29)
EAR_BASE = (0.72, 0.40, 4.58)         # "ear root" (buried in the head top)
EAR_TIP = (1.11, 0.85, 5.92)          # "ear tip" (front z 5.89 / side 5.97 averaged)
EAR_FACE = (0.5, -0.866, 0.0)        # the ear leaf's face direction
EAR_RIM_SIDE = (-0.7, -0.7, 0.3)      # the rim margin (inner + front)
EAR_HALF_W = 0.55                     # "ear width" (half; teardrop widest at 28 % of the length)
EAR_SHAPE = (0.6, 1.2)                # leaf outline exponents (round base, pointed tip)
EAR_THICK = 0.040                     # leaf half-thickness
EAR_BOW = (0.06, 0.07)                # spine bow: toward the face, toward the rim margin ("slight curve")
EAR_CUP = 0.30                        # gentle cup across the leaf
EAR_RIM = (0.30, 1.0)                 # the rim strip runs this share of the length (to the tip)
LEAF_T = (0.0, 0.035, 0.14, 0.28, 0.42, 0.5, 0.58, 0.72, 0.86, 0.965, 1.0)   # across-leaf grid (narrow rim cell)
# ---- PLANT ARMS (+X arm; mirrored). The stalk FRONT (300, 455) -> (318, 470); SIDE root (1020, 450) -> Y 0.10.
STALK = ((0.40, 0.12, 3.08), (0.74, 0.15, 2.90), 0.045, 0.032)   # "arm stalk": root (in the body), end, radii
#   Frond tips: FRONT (X, z) A (428,450) 1.78/3.09 | B (455,480) 2.05/2.79 | C (460,560) 2.10/1.99 (under the berries) |
#   D (418,615) 1.68/1.44 | E (372,585) 1.22/1.74; SIDE (Y, z) a (1148,500) 1.38/2.62 | b (1145,535) 1.35/2.27 |
#   c (1150,620) 1.40/1.42 | d (1068,640) 0.58/1.22 | e (1015,575) 0.05/1.87 | f (1043,545) 0.33/2.17 (hidden in the
#   front: its X is interpolated between D and E). Paired in order; tip z = the two views averaged; E pushed out to X 1.40
#   (at the drawn 1.22 it would sit inside the belly scallops)
FROND_FACE = (0.707, -0.707, 0.0)     # the fronds' face direction (front-outward, both views see them broad)
FRONDS = [  # name, base, tip, half width, bow (toward face, toward the upper margin), rim strip?
    ("a", (0.72, 0.15, 2.93), (1.78, 1.38, 2.86), 0.20, (0.03, 0.12), True),
    ("b", (0.74, 0.18, 2.88), (2.05, 1.35, 2.53), 0.21, (0.03, 0.10), True),
    ("c", (0.86, 0.30, 2.74), (2.10, 1.40, 1.71), 0.20, (0.04, 0.05), False),
    ("d", (0.82, 0.25, 2.76), (1.68, 0.58, 1.33), 0.22, (0.05, 0.04), False),
    ("e", (0.76, 0.14, 2.82), (1.40, 0.05, 1.80), 0.20, (0.05, 0.03), False),
    ("f", (0.80, 0.20, 2.79), (1.50, 0.33, 2.02), 0.19, (0.04, 0.04), False)]
FROND_SHAPE = (0.8, 1.25)             # lance outline exponents (narrow base, pointed tip)
FROND_THICK = 0.024
FROND_CUP = 0.22
FROND_RIM = (0.25, 0.95)
FROND_NS = 10
FROND_T = (0.0, 0.06, 0.28, 0.5, 0.72, 1.0)   # across-frond grid (narrow rim cell)
# ---- BERRY CLUSTER (+X; mirrored). FRONT berry centres (416,526) (437,524) (430,541) (449,540) (427,569) (442,580)
#   (407,522 small); SIDE (1100,559) (1125,559) (1115,579) (1139,582) (1092,592) (1101,608) (1117,622) (1090,546 small);
#   radii 7-9 px. 8 berries: X from the front, Y from the side, z averaged (front centre 2.14, side 1.82)
BERRIES = [(1.66, 0.80, 2.25, 0.065), (1.87, 0.92, 2.20, 0.09), (1.80, 1.15, 2.12, 0.09), (1.99, 1.05, 2.03, 0.09),
           (1.92, 1.29, 1.95, 0.09), (1.77, 0.84, 1.83, 0.07), (1.82, 0.93, 1.68, 0.08), (1.92, 1.07, 1.52, 0.08)]
BERRY_SPREAD = 0.80 
BERRY_SCALE = 1.10                    # berry radius scale (drawn diameters 16-20 px)                  # "cluster tightness": offsets from the cluster centre scaled (the drawn berries touch)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_FLOOR = 0.62                       # "crease darkness": the baked AO never goes below this (soft snow, no dark creases)
# ---- rig
Z_NECK = 3.15                         # head / body weight split (the drawn neck crease)
NECK_BLEND = 0.12
FOOT_SPLIT = (0.30, 0.46, 0.10, 0.28)  # shell -> foot weights: z fades out over [z0, z1], |X| fades in over [x0, x1]
# ---- clips (PLACEHOLDER: the artist has not given the Holly Snowman's movement). 24 fps; integer harmonics per loop ->
#   frame N+1 == frame 1. The body bone pivots ON the floor, so the squash / roll never pushes the shell below it.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH = (0.025, 2)              # "breathing squash": body length +-, cycles per loop (width counter-scales 0.5x)
IDLE_HEAD = (1.2, 1.0)                # head roll / nod deg (1 cyc, lagging)
IDLE_EAR = (5.0, 2)                   # "ear sway" deg, cycles
IDLE_FROND = (3.0, 1)                 # "frond sway" deg, cycles
WALK_N = 48                           # "waddle loop": 2 s (one step each foot)
WALK_ROLL = 5.0                       # "waddle" body roll deg (toward the planted foot)
WALK_HOP = 0.08                       # "hop" body lift (u), twice per loop (each step)
WALK_FOOT_LIFT = 0.07                 # the stepping foot lifts this high, then lands back on the floor
WALK_YAW = 3.0                        # body yaw deg
WALK_EAR, WALK_FROND = 7.0, 6.0       # ear / frond bounce deg

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
          "design/reference/holly_snowman-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; floor z = 0 at the sheet floor line (front y 759); X from the front view "
                   "(axis 250), Y from the side view (axis 1010)", "overrides": OVERRIDES,
          "axes": {"front": 250.0, "side": 1010.0, "back": 668.0, "rule": "intake axes kept (the side axis = the feet "
                   "centre; the head and belly sit behind it as drawn)"}}
scene = bpy.context.scene
DIG = {}
TAU = 2.0 * math.pi
MIRX = np.array([-1.0, 1.0, 1.0])


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

# =========================================================================== 1. BODY SDF (base) + scallop puffs
_bd = SD.catmull(np.array(BODY, float), 12)
_bd = _bd[np.argsort(_bd[:, 0], kind="stable")]
BZ = _bd[:, 0]
HC = np.array(HEAD_C, float)


def body_at(z):
    z = np.clip(z, BZ[0], BZ[-1])
    return [np.interp(z, BZ, _bd[:, j]) for j in (1, 2, 3)]          # a, b, cy


def sd_torso(P):
    z = P[:, 2]
    a, b, cy = body_at(z)
    q = np.hypot(P[:, 0] / a, (P[:, 1] - cy) / b)
    d = (q - 1.0) * np.minimum(a, b) * 0.92
    return np.maximum(d, np.maximum(BODY_BOT - z, z - BZ[-1]))


def sd_head(P):
    """superellipsoid: elliptic sections (HEAD_R), vertical exponent HEAD_P (top / bottom), half-heights HEAD_H"""
    Q = P - HC
    up = Q[:, 2] >= 0.0
    c = np.where(up, HEAD_H[0], HEAD_H[1])
    p = np.where(up, HEAD_P[0], HEAD_P[1])
    rxy = np.hypot(Q[:, 0] / HEAD_R[0], Q[:, 1] / HEAD_R[1])
    rz = np.abs(Q[:, 2]) / c
    F = (rxy ** p + rz ** p) ** (1.0 / p)
    return (F - 1.0) * min(HEAD_R[0], HEAD_R[1], HEAD_H[1]) * 0.95


def sd_foot(P, sg):
    return SD.sd_ellipsoid(P, (sg * FOOT_C[0], FOOT_C[1], FOOT_C[2]), FOOT_R)


def sd_base(P):
    """the un-scalloped body (the grid's base composition, analytic): torso, head (NECK_K), feet (FOOT_K), floor"""
    d = SD.smin(sd_torso(P), sd_head(P), NECK_K)
    d = SD.smin(d, np.minimum(sd_foot(P, 1.0), sd_foot(P, -1.0)), FOOT_K)
    return np.maximum(d, -P[:, 2])


def grad(fn, P, h=0.004):
    P = np.atleast_2d(np.asarray(P, float))
    g = np.zeros_like(P)
    for k in range(3):
        e = np.zeros(3); e[k] = h
        g[:, k] = (fn(P + e) - fn(P - e)) / (2 * h)
    return g


def unit_rows(g):
    return g / np.maximum(np.linalg.norm(g, axis=1), 1e-12)[:, None]


def project_to(fn, P, iters=12):
    P = np.atleast_2d(np.asarray(P, float)).copy()
    for _ in range(iters):
        d = fn(P)
        g = grad(fn, P)
        P -= g * (d / np.maximum((g * g).sum(1), 1e-12))[:, None]
    return P, unit_rows(grad(fn, P))


def sd_base_nofloor(P):
    d = SD.smin(sd_torso(P), sd_head(P), NECK_K)
    return SD.smin(d, np.minimum(sd_foot(P, 1.0), sd_foot(P, -1.0)), FOOT_K)


# grid: X bounds a multiple of the step (nodes mirror about X = 0)
_GX = math.ceil(1.72 / SDF_STEP) * SDF_STEP
GLO = np.array([-_GX, -1.45, -0.04]); GHI = np.array([_GX, 1.95, 5.06])


def base_grid(step):
    gx = math.ceil(1.72 / step) * step
    g = SD.Grid(np.array([-gx, GLO[1], GLO[2]]), np.array([gx, GHI[1], GHI[2]]), step, SDF_BAND)
    zc = np.arange(BODY_BOT - 0.04, BZ[-1] + 0.04, 0.6)
    for z0 in zc:                                                     # z chunks (memory); min-union is idempotent
        g.apply(sd_torso, np.array([-1.5, -1.3, z0]), np.array([1.5, 1.8, min(z0 + 0.6, BZ[-1] + 0.04)]), 0.0)
    hr = np.array([HEAD_R[0], HEAD_R[1], max(HEAD_H)])
    g.apply(sd_head, HC - hr, HC + hr, NECK_K)
    for sg in (1.0, -1.0):
        fc = np.array([sg * FOOT_C[0], FOOT_C[1], FOOT_C[2]])
        g.apply(lambda P, sg=sg: sd_foot(P, sg), fc - np.array(FOOT_R), fc + np.array(FOOT_R), FOOT_K)
    return g


# ---- scallop placement: candidates = a coarse polygonisation of the base surface (X >= 0 half), Poisson-thinned
t_sdf = time.time()
gc = base_grid(CAND_STEP)
gc.floor(0.0)
CV, _ = SD.polygonise(gc)
del gc
CV = CV[(CV[:, 0] >= -1e-9) & (CV[:, 2] > 0.02)]
CN = unit_rows(grad(sd_base_nofloor, CV))
_dz = {"head": sd_head(CV), "body": sd_torso(CV), "feet": sd_foot(CV, 1.0)}
ZN = list(PUFF_ZONES)
zone = np.argmin(np.stack([_dz[z] for z in ZN], 1), 1)
order = np.random.default_rng(PUFF_SEED).permutation(len(CV))


def in_face(S, n):
    zc_, ax_, az_, ny_ = FACE_CLEAR
    return n[1] < -ny_ and (S[0] / ax_) ** 2 + ((S[2] - zc_) / az_) ** 2 < 1.0


def btn_surface(zb):
    """the base surface point on the centreline at height zb (bisection from the front) + its outward normal"""
    lo_, hi_ = -2.0, HEAD_C[1]
    for _ in range(60):
        m_ = 0.5 * (lo_ + hi_)
        if sd_base(np.array([[0.0, m_, zb]]))[0] > 0.0:
            lo_ = m_
        else:
            hi_ = m_
    S = np.array([0.0, 0.5 * (lo_ + hi_), zb])
    return S, unit_rows(grad(sd_base, S))[0]


BTN = [btn_surface(z_) for z_ in BUTTON_Z]


def in_buttons(S, n):
    return n[1] < -0.3 and any(S[0] ** 2 + (S[2] - zb) ** 2 < BUTTON_CLEAR ** 2 for zb in BUTTON_Z)


SPMAX = PUFF_SPACING * max(v[0][1] for v in PUFF_ZONES.values())
cells = {}
PUFFS = []                                         # (centre, radius, zone, on_plane)
skipped = {"face": 0, "buttons": 0, "fringe": 0, "spacing": 0, "own_mirror": 0}
for rank, i in enumerate(order):
    S = CV[i].copy(); n = CN[i]; zn = ZN[zone[i]]
    (r0, r1), hf = PUFF_ZONES[zn]
    r = r0 + (r1 - r0) * FP.hash01(int(i), PUFF_SEED + 0.37)
    if in_face(S, n):
        skipped["face"] += 1; continue
    if in_buttons(S, n):
        skipped["buttons"] += 1; continue
    fw = 1.0 - float(smoothstep(FRINGE[0], FRINGE[1], abs(n[2])))
    if zn == "head":
        fw = max(fw, float(smoothstep(COLLAR[0], COLLAR[1], n[2]) * (1.0 - smoothstep(COLLAR[2], COLLAR[3], n[2]))))
    if fw < 0.15:
        skipped["fringe"] += 1; continue
    on_plane = S[0] < 0.02
    if on_plane:
        S[0] = 0.0
    elif 2.0 * S[0] < PUFF_SPACING * r:
        skipped["own_mirror"] += 1; continue
    key = tuple((S // SPMAX).astype(int))
    ok = True
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dzz in (-1, 0, 1):
                for (q, rq) in cells.get((key[0] + dx, key[1] + dy, key[2] + dzz), ()):
                    lim = PUFF_SPACING * 0.5 * (r + rq)
                    if np.sum((S - q) ** 2) < lim * lim or np.sum((S - q * MIRX) ** 2) < lim * lim:
                        ok = False; break
                if not ok:
                    break
            if not ok:
                break
        if not ok:
            break
    if not ok:
        skipped["spacing"] += 1; continue
    cells.setdefault(key, []).append((S, r))
    h = r * hf * fw
    PUFFS.append((S - n * (r - h), r, zn, on_plane))
PUFF_ALL = []
for c_, r_, zn, onp in PUFFS:
    PUFF_ALL.append((c_, r_, zn))
    if not onp:
        PUFF_ALL.append((c_ * MIRX, r_, zn))
report["scallops"] = {"seed": PUFF_SEED, "candidates_half": int(len(CV)), "placed_half": len(PUFFS),
                      "total_mirrored": len(PUFF_ALL),
                      "per_zone": {z: sum(1 for p in PUFF_ALL if p[2] == z) for z in ZN}, "skipped": skipped,
                      "k": PUFF_K, "spacing": PUFF_SPACING}
print("PUFFS", json.dumps(report["scallops"]))


def build_body(with_puffs=True):
    g = base_grid(SDF_STEP)
    if with_puffs:
        for c_, r_, _ in PUFF_ALL:
            g.apply(lambda P, c_=c_, r_=r_: SD.sd_sphere(P, c_, r_), c_ - r_, c_ + r_, PUFF_K)
    g.floor(0.0)
    return g


def mirror_halve(V, F):
    """SYMMETRY (house rule; eclipse_wolf's): marching tets (Kuhn diagonal) + collapse decimation are not mirror-exact,
    so the low keeps its X >= 0 half (bisected at X = 0) and gets the mirrored copy, welded on the seam."""
    bm_ = bmesh.new()
    vs = [bm_.verts.new(p_) for p_ in V]
    for f_ in F:
        bm_.faces.new([vs[i] for i in f_])
    bmesh.ops.bisect_plane(bm_, geom=bm_.verts[:] + bm_.edges[:] + bm_.faces[:], dist=1e-7, plane_co=(0.0, 0.0, 0.0),
                           plane_no=(1.0, 0.0, 0.0), clear_inner=True)
    bmesh.ops.triangulate(bm_, faces=bm_.faces[:])
    bm_.verts.index_update()
    Vh = np.array([v_.co[:] for v_ in bm_.verts])
    Fh = [[v_.index for v_ in f_.verts] for f_ in bm_.faces]
    bm_.free()
    seam = np.abs(Vh[:, 0]) < 1e-6
    Vh[seam, 0] = 0.0
    n_ = len(Vh)
    mid = np.where(seam, np.arange(n_), n_ + np.arange(n_))
    V2 = Vh.copy(); V2[:, 0] *= -1.0
    Vm = np.vstack([Vh, V2])
    Fm = Fh + [[int(mid[i]) for i in f_[::-1]] for f_ in Fh]
    used = np.zeros(len(Vm), bool)
    for f_ in Fm:
        used[f_] = True
    remap = -np.ones(len(Vm), dtype=np.int64); remap[used] = np.arange(int(used.sum()))
    return Vm[used], [[int(remap[i]) for i in f_] for f_ in Fm], int(seam.sum())


g = build_body(True)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
grid_n = g.n.tolist()
del g
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_islands(LV, LF, 0.01)
below = LV[:, 2] < 0.0
floor_snap = {"verts_snapped_to_floor": int(below.sum()), "worst_dip": round(float(-LV[:, 2].min()), 6) if below.any() else 0.0}
LV[:, 2] = np.maximum(LV[:, 2], 0.0)
LV, LF, SEAM_N = mirror_halve(LV, LF)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 30000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
bb_h = np.ptp(HV, 0); bb_l = np.ptp(LV, 0)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": grid_n, "high_tris": len(HF), "crumbs": crumbs,
                 "seconds": round(time.time() - t_sdf, 1)}
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation of the scalloped high "
                              "(deterministic) -> mirror-halved" % SDF_STEP, "decimated_tris": tri_count_F(LF),
                    "specks": specks, "floor_snap": floor_snap,
                    "mirror_halved": {"seam_verts": SEAM_N, "rule": "X >= 0 half + its mirror (symmetric rest mesh)"},
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_low_over_high": (bb_l / bb_h).round(5).tolist()}
print("SDF", json.dumps(report["sdf"]), json.dumps(report["retopo"]))
assert np.all(bb_l / bb_h > 0.97), report["retopo"]                    # the hull-fold hazard (BUILD_CONTRACT): gated
assert HV[:, 2].min() > -1e-6, "body high dips below the floor"


# =========================================================================== 2. regions on the body low (iso cuts)
def body_fields(P):
    """smooth snow-grade weights from the base normal (+ the front-projected blush ellipse): w_hi, w_shade, w_blush"""
    n = unit_rows(grad(sd_base_nofloor, P, 0.01))
    nz, ny = n[:, 2], n[:, 1]
    w_hi = smoothstep(GRADE_HI[0], GRADE_HI[1], nz)
    w_sh = GRADE_SHADE_MAX * smoothstep(GRADE_SHADE[0], GRADE_SHADE[1], -nz)
    rho = np.sqrt((P[:, 0] / BLUSH[1]) ** 2 + ((P[:, 2] - BLUSH[0]) / BLUSH[2]) ** 2)
    w_b = BLUSH[3] * (1.0 - smoothstep(BLUSH_CORE, 1.0, rho)) * smoothstep(FRONT_GATE[0], FRONT_GATE[1], -ny)
    return {"hi": w_hi, "shade": w_sh, "blush": w_b}


def cut_island(V, F, field_fn, cuts):
    """ember_stump's iso-cutter: every cut = (field, tau, gate) with gate None or [(field, '>'/'<', v)] that both edge
    ends must satisfy -> cut V, F, per-face field values, normals, log"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
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

    log = [iso_cut(k_, tau_, g_) for k_, tau_, g_ in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    n0 = len(bm.faces)
    bmesh.ops.dissolve_degenerate(bm, dist=DEGEN_DIST, edges=bm.edges[:])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    flips = 0
    for _ in range(6):
        flat = [f for f in bm.faces if f.calc_area() <= DEGEN_DIST ** 2]
        if not flat:
            break
        eds = list({max(f.edges, key=lambda e: e.calc_length()) for f in flat})
        res = bmesh.ops.rotate_edges(bm, edges=eds, use_ccw=False)
        flips += len(res["edges"])
    min_area = min(f.calc_area() for f in bm.faces)
    log.append({"degenerate_dissolved_faces": n0 - len(bm.faces), "sliver_edge_flips": flips, "min_face_area": float(min_area)})
    assert min_area > DEGEN_DIST ** 2, log[-1]
    bm.verts.index_update(); bm.faces.index_update(); bm.normal_update()
    CV_ = np.array([v.co[:] for v in bm.verts])
    CF_ = [[v.index for v in f.verts] for f in bm.faces]
    FVAL = field_fn(np.array([np.mean([v.co[:] for v in f.verts], 0) for f in bm.faces]))
    for key in {c_[0] for c_ in cuts}:
        cmin = np.array([min(v[LAY[key]] for v in f.verts) for f in bm.faces])
        FVAL[key] = np.where(cmin >= -1e-6, FVAL[key], np.minimum(FVAL[key], cmin))
    FN = np.array([f.normal[:] for f in bm.faces])
    bm.free()
    return CV_, CF_, FVAL, FN, log


t = time.time()
CV_B, CF_B, FV_B, FN_B, log_b = cut_island(LV, LF, body_fields, [])     # no iso cuts (v1.1): degenerate clean-up only
CV_B[:, 2] = np.maximum(CV_B[:, 2], 0.0)
reg_b = np.full(len(CF_B), "snow", dtype=object)
_gf = body_fields(CV_B)
GRADE_B = np.stack([_gf["hi"], _gf["shade"], _gf["blush"]], 1)          # per body vertex (the shell's vertices come first)
report["snow_grade"] = {"rule": "per-vertex weights from the smooth base normal; corner colours = snow -> snow_hi (w_hi), "
                                "-> snow_shade (w_shade), -> blush (w_blush), blended in linear RGB",
                        "w_hi": {"max": round(float(GRADE_B[:, 0].max()), 3), "share_gt_0.5": round(float((GRADE_B[:, 0] > 0.5).mean()), 4)},
                        "w_shade": {"max": round(float(GRADE_B[:, 1].max()), 3), "share_gt_0.5": round(float((GRADE_B[:, 1] > 0.5).mean()), 4)},
                        "w_blush": {"max": round(float(GRADE_B[:, 2].max()), 3), "verts_gt_0.05": int((GRADE_B[:, 2] > 0.05).sum()),
                                    "verts_gt_0.5": int((GRADE_B[:, 2] > 0.5).sum())}}
report["iso_cuts"] = {"body": {"cuts": log_b, "tris_after": len(CF_B), "seconds": round(time.time() - t, 1)}}

# =========================================================================== 3. closed parts
PARTS = []


def mirror_part(p_, name, bone):
    V_ = p_.V.copy(); V_[:, 0] *= -1.0
    q = FP.Part(name, V_, [f_[::-1] for f_ in p_.F], p_.R, bone=bone)
    return q


body = FP.Part("body", CV_B, CF_B, list(reg_b), bone=None)
body.body = True
PARTS.append(body)


def head_surf(T):
    """project onto the base surface (the eyes sit on the puff-free face) -> point, outward normal"""
    P_, n_ = project_to(sd_base_nofloor, np.asarray(T, float)[None, :])
    return P_[0], n_[0]


def tangent_frame(n):
    e_v = unit(np.array([0.0, 0.0, 1.0]) - n * n[2])
    e_u = np.cross(e_v, n)
    return e_u, e_v


# ---- eyes (+X = L; R mirrors)
FACE = {}
S_e, n_e = head_surf(np.array([EYE_X, HEAD_C[1] - HEAD_R[1], EYE_Z]))
eu, ev = tangent_frame(n_e)
V, F, R = FP.conform_dome(head_surf, S_e, eu, ev, [(EYE_HALF[0] * 0.55, EYE_HALF[1] * 0.55, "eye"),
                                                    (EYE_HALF[0], EYE_HALF[1], "eye")], EYE_DEPTH, 20, lift=EYE_LIFT)
PARTS.append(FP.Part("eye.L", V, F, R, bone="head"))
PARTS.append(mirror_part(PARTS[-1], "eye.R", "head"))
FACE["eye_L"] = {"surface": S_e.round(4).tolist(), "normal": n_e.round(4).tolist(), "half_uv": list(EYE_HALF)}

# ---- buttons (centreline)
for k, (S_b, n_b) in enumerate(BTN):
    c_b = S_b - n_b * BUTTON_SINK
    V, F, R = FP.berry_ball(c_b, BUTTON_R, LIGHT, BERRY_SEG[0], BERRY_SEG[1], flat=BUTTON_FLAT, axis=n_b)
    PARTS.append(FP.Part("button.%d" % (k + 1), V, F, R, bone="body"))
FACE["buttons"] = [S_b.round(4).tolist() for S_b, _ in BTN]

# ---- leaf ears (+X = L)
V, F, R, ear_info = FP.leaf(EAR_BASE, EAR_TIP, EAR_FACE, EAR_RIM_SIDE, EAR_HALF_W, EAR_THICK, 16, LEAF_T, shape=EAR_SHAPE,
                            bow_n=EAR_BOW[0], bow_u=EAR_BOW[1], cup=EAR_CUP, rim_s=EAR_RIM)
PARTS.append(FP.Part("ear.L", V, F, R, bone="ear_L"))
PARTS.append(mirror_part(PARTS[-1], "ear.R", "ear_R"))

# ---- plant arms (+X = L): stalk + fronds + berries
ARM_PARTS = []
V, F, R = FP.tube(STALK[0], STALK[1], STALK[2], STALK[3], 8, 2, "leaf")
ARM_PARTS.append(FP.Part("stalk.L", V, F, R, bone="arm_L"))
FROND_INFO = {}
for nm, b_, t_, hw, bow, rim in FRONDS:
    V, F, R, inf = FP.leaf(b_, t_, FROND_FACE, (0.0, 0.0, 1.0), hw, FROND_THICK, FROND_NS, FROND_T, shape=FROND_SHAPE,
                           bow_n=bow[0], bow_u=bow[1], cup=FROND_CUP, rim_s=FROND_RIM if rim else None)
    ARM_PARTS.append(FP.Part("frond_%s.L" % nm, V, F, R, bone="arm_L"))
    FROND_INFO[nm] = {"length": round(inf["length"], 3)}
_bc = np.array([b[:3] for b in BERRIES]).mean(0)
BERRY_C = _bc
for k, (x_, y_, z_, r_) in enumerate(BERRIES):
    c_ = _bc + (np.array([x_, y_, z_]) - _bc) * BERRY_SPREAD
    V, F, R = FP.berry_ball(c_, r_ * BERRY_SCALE, LIGHT, BERRY_SEG[0], BERRY_SEG[1])
    ARM_PARTS.append(FP.Part("berry.%d.L" % (k + 1), V, F, R, bone="arm_L"))
for p in ARM_PARTS:
    PARTS.append(p)
    PARTS.append(mirror_part(p, p.name[:-2] + ".R", "arm_R"))

# frond / ear clearance vs the base body (report): leaf vertices deeper than 0.02 inside the base, past the buried root
clear = {}
for p in PARTS:
    if p.name.startswith(("frond_", "berry.")) and p.name.endswith(".L"):
        d_ = sd_base(p.V)
        clear[p.name[:-2]] = {"min_sd": round(float(d_.min()), 4), "verts_inside": int((d_ < -0.02).sum())}
report["part_clearance_vs_base_body"] = clear

# =========================================================================== 4. assemble + centre
ISL = [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
assert lo0[2] > -1e-6, "something dips below the floor: min z %.6f" % lo0[2]
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
assert VALL[:, 2].min() > -1e-9 and abs(VALL[RANGE["body"][0]:RANGE["body"][1], 2].min()) < 1e-9, "the feet must sit on the floor"
# mirror test (house rule): every vertex mirrored X -> -X, distance to the nearest point of the mesh
_bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mir_d = np.array([_bvh_all.find_nearest(Vector((-p_[0], p_[1], p_[2])))[3] for p_ in VALL])
report["mirror_test"] = {"rule": "every rest vertex mirrored across X = 0 -> distance to the nearest point of the mesh",
                         "max_abs_asymmetry": round(float(_mir_d.max()), 6), "p99": round(float(np.percentile(_mir_d, 99)), 6),
                         "mean": round(float(_mir_d.mean()), 7), "shift_x": round(float(SHIFT[0]), 7),
                         "bbox_x": [round(float(VALL[:, 0].min()), 5), round(float(VALL[:, 0].max()), 5)]}
print("MIRROR", json.dumps(report["mirror_test"]))
REG = ["snow", "eye", "leaf", "leaf_light", "leaf_rim", "berry", "berry_hi"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    FCc[:, 0] = np.abs(FCc[:, 0])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.985 + 0.03 * j                                  # |X|-based: mirror-symmetric; small (no speckle)


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


def paint_grade(me_, pal):
    """v1.1: overwrite the 'snow' faces' corner colours with the smooth per-vertex grade (palette tones blended in linear
    RGB from the stored 'snow_grade' weights); every other region keeps PAL.paint's flat colour"""
    missing = [t_ for t_ in ("snow",) + GRADE_TONES if t_ not in pal["regions"]]
    assert not missing, "palette lacks the gradient tones %s" % missing
    tone = {k_: PAL.srgb_to_linear(pal["regions"][k_]["rgb"]) for k_ in ("snow",) + GRADE_TONES}
    nv = len(me_.vertices)
    gw = np.empty(nv * 3, dtype=np.float32); me_.attributes["snow_grade"].data.foreach_get("vector", gw); gw = gw.reshape(-1, 3)
    col = np.tile(tone["snow"], (nv, 1))
    col = col + gw[:, :1] * (tone["snow_hi"] - col)
    col = col + gw[:, 1:2] * (tone["snow_shade"] - col)
    col = col + gw[:, 2:3] * (tone["blush"] - col)
    names_, rid_, _ = PAL.read_regions(me_)
    lt = np.empty(len(me_.polygons), dtype=np.int64); me_.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me_.loops), dtype=np.int64); me_.loops.foreach_get("vertex_index", lv)
    snow_loop = np.repeat(rid_ == names_.index("snow"), lt)
    ca = me_.color_attributes["Col"]
    cd = np.empty(len(me_.loops) * 4, dtype=np.float32); ca.data.foreach_get("color", cd); cd = cd.reshape(-1, 4)
    cd[snow_loop, :3] = np.clip(col[lv[snow_loop]], 0.0, 1.0)
    ca.data.foreach_set("color", cd.ravel())
    return int(snow_loop.sum())


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        out[o.name + ":graded_snow_corners"] = paint_grade(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
_emit = [n for n, v in pal_default["regions"].items() if "emission" in v]
report["glow"] = {"default": {"emitting_regions": _emit, "rule": "the sheet draws no glow (brief: no glow)"}}
assert not _emit, report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = jitter(VALL, FALL)
shade[rid == REG.index("snow")] = 1.0                  # the snow is graded per vertex (no per-face speckle)
PAL.store_regions(me, REG, rid, shade)
_gall = np.zeros((len(VALL), 3), np.float32); _gall[:len(CV_B)] = GRADE_B
me.attributes.new("snow_grade", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", _gall.ravel())
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre at eye height -> the face between the eyes (straight -Y)
anchor = np.array([0.0, HEAD_C[1], EYE_Z]) - SHIFT
landmark = head_surf(np.array([0.0, HEAD_C[1] - HEAD_R[1], EYE_Z]))[0] - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre at eye height -> the head's front surface between the eyes (the face looks -Y)",
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
report["tris"] = {"total": tris_main, "window": TRI_BUDGET, "per_part_family": _pp,
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The scalloped SDF snow shell (head, pear body, "
                            "feet; iso-cut grading) carries the largest share; two leaf ears, two plant arms of six "
                            "frond leaves + 8 berries each, three buttons and the eyes the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]), "parts": len(open_e),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every part is closed: the SDF shell (mirror-welded), leaf pillows, stalk tubes, berry / "
                                "button spheres, eye domes"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_height_units": {"front": 5.9, "back": 5.89, "side": 6.01},
                     "sheet_width_units_front": 4.32,
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
low["conquest_source"] = "from scratch: design/reference/holly_snowman-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color (all black: no glow)"
low["conquest_alpha_channel"] = "none: every material opaque"
_hr = np.array([HEAD_R[0], HEAD_R[1], HEAD_H[0]])
_face = np.array([[-HEAD_R[0] - 0.12, HEAD_C[1] - HEAD_R[1] - 0.6, HEAD_C[2] - HEAD_H[1] - 0.05],
                  [HEAD_R[0] + 0.12, HEAD_C[1] + 0.2, HEAD_C[2] + HEAD_H[0] + 0.1]])
_earv = np.vstack([p.V for p in PARTS if p.name == "ear.L"])
_armv = np.vstack([p.V for p in PARTS if p.name.endswith(".L") and p.name.startswith(("frond_", "berry.", "stalk"))])
_low = np.array([[-1.5, -1.4, 0.0], [1.5, 1.9, 1.7]])
low["conquest_focus"] = json.dumps({
    "face": [(_face[0] - SHIFT).tolist(), (_face[1] - SHIFT).tolist()],
    "leaf_ear": [(_earv.min(0) - SHIFT - 0.08).tolist(), (_earv.max(0) - SHIFT + 0.08).tolist()],
    "plant_arm": [(_armv.min(0) - SHIFT - 0.08).tolist(), (_armv.max(0) - SHIFT + 0.08).tolist()],
    "body_feet": [(_low[0] - SHIFT).tolist(), (_low[1] - SHIFT).tolist()],
    "views": {"face": [0.0, 4.0], "leaf_ear": [25.0, 10.0], "plant_arm": [40.0, 8.0], "body_feet": [20.0, 6.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("scallops", "sdf", "retopo", "tris", "measure", "facing",
                                                            "open_edges", "regions_area_share", "mirror_test",
                                                            "part_clearance_vs_base_body")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake normal + AO (high = SDF high + closed parts)
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


add_high(HV, HF)
for p in PARTS:
    if getattr(p, "body", False):
        continue                                     # the SDF low is replaced by its high
    add_high(p.V, p.F)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
BAKE_CAGE = max(0.02, round(3.0 * report["retopo"]["high_to_low_sampled"]["p99"], 4))
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
body_faces = np.zeros(nf, bool); body_faces[np.arange(nf) < len(CF_B)] = True     # the shell's faces come first
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the body's sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "the scalloped body SDF high + every closed part",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "normal_dev_p99": round(float(np.percentile(devn[cov_n & alltex_n], 99)), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean_raw": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05_raw": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0
pa[:, :3] = np.maximum(pa[:, :3], AO_FLOOR)
bstats["ao_floor"] = AO_FLOOR
img_ao.pixels.foreach_set(pa.ravel())
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
                     "provenance": "pixel-sampled from the sheet's palette chips (intake swatches) + drawn-surface samples "
                                   "(see the palette notes)"}
report["face"] = FACE
report["fronds"] = FROND_INFO
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


_cyB = float(body_at(np.array([1.2]))[2][0])
BONES = [("body", P_((0.0, _cyB, 0.0)), P_((0.0, _cyB, Z_NECK)), "root"),
         ("head", P_((0.0, HEAD_C[1], Z_NECK)), P_((0.0, HEAD_C[1], HEAD_C[2] + HEAD_H[0])), "body")]
for side, m_ in (("L", np.ones(3)), ("R", MIRX)):
    BONES.append(("ear_%s" % side, P_(np.array(EAR_BASE) * m_), P_(np.array(EAR_TIP) * m_), "head"))
    BONES.append(("arm_%s" % side, P_(np.array(STALK[0]) * m_), P_(BERRY_C * m_), "body"))
    BONES.append(("foot_%s" % side, P_(np.array([FOOT_C[0], FOOT_C[1], 0.0]) * m_),
                  P_(np.array([FOOT_C[0], FOOT_C[1], 0.45]) * m_), "root"))
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
    e.use_connect = False
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences): the shell by height (neck blend) + the feet zones (low and outboard); every closed part
#      rigid on its bone (eyes head, buttons body, ears ear_*, stalk / fronds / berries arm_*)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["body"]
Pw = VALL[a_:b_] + SHIFT
w_head = smoothstep(Z_NECK - NECK_BLEND, Z_NECK + NECK_BLEND, Pw[:, 2])
z0_, z1_, x0_, x1_ = FOOT_SPLIT
w_low = 1.0 - smoothstep(z0_, z1_, Pw[:, 2])
w_fL = w_low * smoothstep(x0_, x1_, Pw[:, 0])
w_fR = w_low * smoothstep(x0_, x1_, -Pw[:, 0])
Wt[a_:b_, J["head"]] = w_head
Wt[a_:b_, J["foot_L"]] = w_fL
Wt[a_:b_, J["foot_R"]] = w_fR
Wt[a_:b_, J["body"]] = 1.0 - w_head - w_fL - w_fR
for p in PARTS:
    if getattr(p, "body", False):
        continue
    a_, b_ = RANGE[p.name]
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
                  "rule": "shell: head smoothstep at z %.2f +- %.2f; feet = (1 - smoothstep z %.2f..%.2f) x smoothstep "
                          "|X| %.2f..%.2f on each side; the rest body. Eyes 1.0 head; buttons 1.0 body; ears 1.0 ear_*; "
                          "stalk / fronds / berries 1.0 arm_*" % ((Z_NECK, NECK_BLEND) + tuple(FOOT_SPLIT))}
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
C_ = math.cos


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        amp, cyc = IDLE_BREATH
        br = S(TAU * cyc * u)
        Lf = 1.0 + amp * br
        Wf = 1.0 - 0.5 * amp * br
        P["body"] = (None, [], (Wf, Lf, Wf))                      # the body bone's local Y = world Z (pivot on the floor)
        P["head"] = (None, [("Y", IDLE_HEAD[0] * S(TAU * u - 0.8)), ("X", IDLE_HEAD[1] * S(TAU * u + 0.6))], None)
        for side, sg, ph in (("L", 1.0, 0.0), ("R", -1.0, 0.9)):
            P["ear_%s" % side] = (None, [("Y", sg * IDLE_EAR[0] * S(TAU * IDLE_EAR[1] * u + ph))], None)
            P["arm_%s" % side] = (None, [("X", IDLE_FROND[0] * S(TAU * IDLE_FROND[1] * u + 0.5 + ph)),
                                         ("Y", sg * 0.6 * IDLE_FROND[0] * S(TAU * IDLE_FROND[1] * u + 1.6 + ph))], None)
    else:
        rl = S(TAU * u)                                            # + = lean toward +X (the L foot planted)
        hop = WALK_HOP * (0.5 - 0.5 * C_(TAU * 2 * u))
        P["body"] = ((0.0, 0.0, hop), [("Y", WALK_ROLL * rl), ("Z", WALK_YAW * S(TAU * u + 0.5))], None)
        P["head"] = (None, [("Y", -0.4 * WALK_ROLL * S(TAU * u - 0.5)), ("X", 1.5 * S(TAU * 2 * u - 1.0))], None)
        for side, sg in (("L", 1.0), ("R", -1.0)):
            lift = WALK_FOOT_LIFT * max(0.0, -sg * rl) ** 2           # the unloaded foot lifts, lands back at 0
            P["foot_%s" % side] = ((0.0, 0.0, lift), [], None)
            P["ear_%s" % side] = (None, [("Y", sg * WALK_EAR * S(TAU * 2 * u - 1.2))], None)
            P["arm_%s" % side] = (None, [("X", WALK_FROND * S(TAU * 2 * u - 0.9)), ("Y", sg * 0.5 * WALK_FROND * rl)], None)
    return P


NEW_ACTS = {}
key_rows = []
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1
        P = pose_at(cn, f % N)
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


BRV = np.arange(*RANGE["body"])
sole_v = BRV[VALL[BRV, 2] < 0.02]
samples, clip_rep = [], {}
for cn, N in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    first = last = None
    rows = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        Cc = eval_coords(low)
        samples.append(Cc[::7])
        if f == 1:
            first = Cc
        if f == N + 1:
            last = Cc
        pbs = rig.pose.bones
        rows.append({"minz": float(Cc[:, 2].min()), "sole_minz": float(Cc[sole_v, 2].min()),
                     "sole_slide": float(np.abs(Cc[sole_v, :2] - VALL[sole_v, :2]).max()),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "soles": {"min_z": round(float(g_("sole_minz").min()), 6), "slide_xy_max": round(float(g_("sole_slide").max()), 5),
                              "sole_verts": int(len(sole_v))}}
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
rig["conquest_rig"] = "holly_snowman v1: root > body > head > ear_L / ear_R; body > arm_L / arm_R; root > foot_L / foot_R"
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("idle = PLACEHOLDER (the artist has not given the Holly Snowman's movement; open question): "
                               "4 s soft breathing squash (body length +-%.3f, pivot on the floor) + ear / frond sway; "
                               "walk = PLACEHOLDER 2 s in-place waddle hop (body roll %.1f deg + hop %.2f u twice per "
                               "loop; the unloaded foot lifts %.2f u and lands back on the floor; no forward travel)"
                               % (IDLE_BREATH[0], WALK_ROLL, WALK_HOP, WALK_FOOT_LIFT))
low["conquest_locomotion"] = "in-place waddle placeholder: rest pose soles z 0 per contract; the feet never go below it"
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
if "snow_grade" in low.data.attributes:                # the grade weights are build-side data (the glb carries Col)
    low.data.attributes.remove(low.data.attributes["snow_grade"])
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
              "structure": "armature identity + skinned 'holly_snowman' (one opaque primitive); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default)}, "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
