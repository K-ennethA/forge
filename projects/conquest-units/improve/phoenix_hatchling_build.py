"""Phoenix Hatchling -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/phoenix_hatchling_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x4: Soot Puff, Winged Cat, Phoenix Hatchling, Chest Monster" (section
PHOENIX HATCHLING) + the sheet design/reference/phoenix_hatchling-character-sheet.png (intake:
design/reference/phoenix_hatchling/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft
speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. All three views are TRUE orthos:
  FRONT view (the unit's left on image right):   X = (x_px - 205) / 100     ; z = (831 - y_px) / 100
  SIDE view (the beak on image LEFT = -Y):        Y = (x_px - 979.5) / 100   ; z = (830 - y_px) / 100
  BACK view:                                      X = -(x_px - 603) / 100    ; z = (819 - y_px) / 100
Floor z = 0 at the soles. X +ve = the unit's LEFT (L). The views disagree in places (a painterly sheet): the side view
draws the eye at Y -0.70 where the front view's eye spacing (X +-0.98) puts it on the head at Y -0.94; the FACE follows
the front view (each constant says which view it was measured on).

Pipeline:
  1. SHELL = ONE SDF grid, groups smooth-unioned: HEAD (two-half-height ellipsoid) + CREST (three feather stems ending in
     swirl curls: torus + core) + the hooked BEAK (scaled round-cone chain) / the pear BODY (two-half-height ellipsoid + a
     hidden neck) + two WING PANELS (a shell ellipsoid cut by an inner plane and a scalloped hem) + the TAIL tuft (three swept
     feathers) / the smoke COLLAR (oval torus + an inner fill ring that meets the head's underside + seeded rounded puffs, two
     sizes; the small ones live mostly in the normal map). Marching tets = the HIGH; collapse decimation = the LOW.
  2. REGIONS on the low shell by group ownership; iso-cut: the collar boundary, the beak, the glowing RING band (the head
     surface within RING_W of the collar) and the collar's dark-grey wisp crowns.
  3. CLOSED PARTS: eyes (proud inset ellipsoid domes, glossy material) + highlight dots, the chest EMBER JEWEL (diamond +
     core), the legs (tapered tubes + ring segments), the feet (pad + three front toes + back toe), static EMBER flecks by the
     crest and ASH flecks round the collar (a static stand-in for the sheet's particles). 0 open edges everywhere.
  4. palette (palettes.py regions -> Col / Glow; glow = ring, jewel, ember only), Smart UV, bake normal + AO from the high
     (SDF shell + every closed part).
  5. RIG: root > body > neck > head > crest.001 > crest.002; body > wing_L / wing_R; root > thigh > shin > foot (L / R;
     the legs hang off root so the placeholder waddle never drags a planted foot). Clips PLACEHOLDER (movement not given):
     idle = breathing bob (neck) + crest sway; walk = in-place waddle step. glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                    # noqa: E402  (read-only use)
import palettes as PAL                # noqa: E402  (read-only use)
import magmoo_sdf as SD               # noqa: E402  (read-only use: the SDF kit)
import phoenix_hatchling_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "phoenix_hatchling"
CHAR_ID = "phoenix_hatchling"         # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.20                       # BAND >= max smooth-union k (NECK_K 0.15) + 2 steps (contract hazard note)
BODY_TRIS = 12500                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- HEAD: FRONT outline (head pixels, row scan) widest y 400-420 x 61..351 -> half 1.45 at z 4.2; y 300 half 0.735,
#   y 350 half 1.285 -> ellipse centre z 4.23, top half-height 1.25 (top z 5.48, under the crest); bottom y 515 (z 3.16) with
#   y 490 half 1.02 / y 470 half 1.22 -> a rounder bottom (half-height 1.11). SIDE: widest y 400 x 846..1119 -> Y -1.335..
#   +1.395 (centre +0.05, half 1.365 incl. the 2-3 px ink line); y 300 half 0.665, y 350 1.205 -> centre z 4.27.
HEAD_C = (0.0, 0.05, 4.24)            # head centre
HEAD_RXY = (1.45, 1.35)               # "head width / depth" half-sizes
HEAD_RZ = (1.24, 1.11)                # "head height": top / bottom half-heights (top 5.48, bottom 3.13)
# ---- CREST: three feather stems (round-cone chains) each ending in a SWIRL CURL (torus + core sphere). FRONT: crest base
#   y 290 x 164..261, stem y 250 x 169..229, top curl centre (232, 202) -> X 0.27 z 6.29, curls (200, 222) X -0.05 z 6.09 and
#   (175, 252) X -0.30 z 5.79, top y 183 (z 6.48). SIDE: base y 290 x 954..1038 (Y -0.25..+0.59), top curl centre (1027, 195)
#   -> Y +0.48 z 6.35 (spans x 1010..1044), front curls (977, 240) Y -0.03 z 5.90 / (962, 255) Y -0.17 z 5.75, top y 175.
CREST_MAIN = [((0.00, 0.12, 5.25), 0.37), ((-0.03, 0.15, 5.68), 0.28), ((0.06, 0.27, 6.03), 0.19), ((0.16, 0.37, 6.20), 0.12)]
CURL_MAIN = ((0.25, 0.45, 6.31), 0.13, 0.08, 0.10)   # "top swirl curl": centre, ring radius, tube radius, core radius
                                      #   (core ~ ring radius: a rolled disc with a groove = the spiral, not an eyelet)
CREST_MID = [((-0.04, 0.05, 5.40), 0.25), ((-0.05, -0.02, 5.84), 0.13)]
CURL_MID = ((-0.06, -0.05, 5.98), 0.10, 0.06, 0.075)  # "middle curl"
CREST_LOW = [((-0.12, 0.02, 5.30), 0.23), ((-0.24, -0.10, 5.64), 0.12)]
CURL_LOW = ((-0.30, -0.15, 5.77), 0.09, 0.055, 0.068)  # "front-low curl"
CURL_AXIS = (0.71, -0.71, 0.0)        # swirl plane normal: 45 deg between the front and side views (both views draw a spiral)
CREST_K, CURL_K = 0.12, 0.03          # stem -> head blend / curl -> stem blend
# ---- BODY: FRONT taupe y 640 x 60..349 (half 1.45 incl. the wings), belly bottom y ~735 (z 0.96); SIDE y 600 x 902..1109
#   (Y -0.775..+1.295), y 640 front -0.865, y 700 -0.625..+1.485 (wing / tail), bottom y 735.
BODY_C = (0.0, 0.30, 1.70)            # body centre (the widest row; a PEAR: narrow top under the collar, full bottom)
BODY_RXY = (1.36, 1.08)               # "body width / depth" (the wing panels add WING_T)
BODY_RZ = (1.93, 0.74)                # "body height" top / bottom half-heights (z 0.96 .. ; v1.2: the top lifted so the
                                      #   shoulders meet the thinner collar's underside -- the dome itself hides in the collar)
NECK = ((0.0, 0.18, 2.50), 0.62, (0.0, 0.08, 3.35), 0.60)   # hidden neck under the collar (fills the collar's hole)
NECK_K = 0.15
# ---- WINGS: FRONT outer edge |X| 1.30 (z 2.3) / 1.47 (1.9) / 1.43 (1.3) / 1.32 (1.1), shoulder y 575 (z 2.56), hem y 725-735
#   (z 0.96-1.06) with ~4 scallops across the front half of each wing, inner edge lines at x 172 / 238 (|X| 0.33) from y 640
#   down. SIDE: the wing panel covers the body side, front Y -0.865 (z 1.9) .. back +1.50, hem z ~1.0. BACK: two panels with the
#   body between them, scalloped hems.
#   v1.1 rebuild (gate follow-up): each wing is a thin PANEL hugging the body side = the body ellipsoid grown by WING_T
#   sideways / front-back, sunk WING_TOP_SINK at the top (so the panel fades into the shoulder under the collar) and dropped
#   WING_DROP at the bottom (so it hangs past the belly), cut by the plane sg * X = WING_X0 (its inner edge stands proud of
#   the belly: the drawn wing edge lines) and by a SCALLOPED HEM (rounded lobes hanging down, WING_HEM[2] per half-turn).
WING_T = 0.08                         # "wing panel thickness" over the body
WING_TOP_SINK = 0.06                  # the panel's top sinks into the body (no ledge at the shoulder)
WING_DROP = 0.50                      # "wing hang": extra bottom half-height (the hem cut sets the real bottom)
WING_X0 = 0.36                        # "wing inner edge" |X| (front: inner edge lines at |X| 0.33)
WING_HEM = (0.98, 0.15, 10, 0.0)      # "hem scallops": lowest feather-tip z, scallop depth, lobes per half-turn, phase
WING_CUT_K = 0.04                     # rounding of the panel's cut edges
WING_K = 0.02                         # wing -> body blend (small: the panel edge reads as a line)
# ---- TAIL tuft: SIDE top edge (1100, 625) -> tip (1168, 660) Y +1.885 z 1.70, bottom edge back to (1110, 720); BACK view the
#   pointed fan x 580..650 (|X| <= 0.35), apex y 650 (z 1.69), base ~y 700 (z 1.2). Three swept feathers from one root.
TAIL_FEATHERS = [((0.0, 1.05, 1.62), 0.30, (0.0, 1.88, 1.73), 0.03),
                 ((0.15, 1.05, 1.50), 0.24, (0.30, 1.64, 1.33), 0.04),
                 ((-0.15, 1.05, 1.50), 0.24, (-0.30, 1.64, 1.33), 0.04)]   # "tail": root / radius, tip / radius per feather
TAIL_K = 0.10
# ---- SMOKE COLLAR: FRONT dark rows y 495..575 (z 3.36..2.56), x 58..346 at y 530 (|X| ~1.44); SIDE x 849..1106 at y 530
#   (Y -1.305..+1.265), y 505..575; BACK x 470..749 (|X| 1.40).
COLLAR_C = (0.0, 0.10, 3.28)          # collar ring centre (v1.2: raised to the head base -- the head does not move)
COLLAR_R0 = 1.06                      # "collar radius" (X); Y = R0 * COLLAR_SY
COLLAR_SY = 0.84
COLLAR_AB = (0.30, 0.21)              # "collar thickness": tube radial / vertical half-size AT THE SIDES (artist v1 verdict:
                                      #   "half that height": v1 0.42 -> 0.21)
COLLAR_CENTRE_T = 0.55                # "thinner in the centre": front / back centre-line thickness as a share of the sides
                                      #   (vertical half-size b * (T + (1 - T) * cos^2 azimuth); 1 = uniform)
COLLAR_K = 0.04
COLLAR_FILL = (0.85, 0.25, 0.14, 3.30)   # inner fill ring (R0 X, radial, vertical half-sizes, z): the collar rises
                                      #   to meet the head's underside, so the head sits INTO the collar (no gap)
PUFF_SEED = 11                        # "collar fluff pattern" seed (deterministic)
PUFF_BIG = (90, (0.08, 0.13), (0.025, 0.05))     # "big puffs": count, radius range, protrusion range (the silhouette;
                                      #   v1.2 halved with the collar, then x the local taper)
PUFF_SMALL = (200, (0.035, 0.055), (0.006, 0.014))  # "small puffs": the smoky texture (mostly normal map: fuzz at HIGH)
PUFF_PH = (-115.0, 115.0)             # tube angles that carry puffs (deg; 0 = outward, 90 = up): the inner side is skipped (a puff
                                      #   under the tube sat on the body as a dark spot)
PUFF_K = 0.05
WISP_P = 0.15                         # "light wisps": share of upward big puffs whose crown takes the smoke_light tone
WISP_NZ = 0.15                        # ... only puffs whose normal points up this much (the sheet's strokes sit on top)
WISP_TAU = 0.022                      # ... and only their crown (protrusion above this; scaled down with the puffs in v1.2)
# ---- GLOWING RING at the head base (FRONT: the orange band hugs the head's bottom contour, 6-8 px tall: y 490-497 at
#   |X| 0.85, y 510-516 at the centre). v1.1: an iso-cut band ON the head shell = the head surface within RING_W of the
#   collar (it follows the head base where it sits into the collar; no separate torus).
RING_W = 0.10                         # "ring width" (distance from the collar base surface)
# ---- EYES (FRONT blobs, max channel < 20): centres (101.2, 401.8) / (297.7, 402.6) -> X -1.04 / +0.93, z 4.29; each 0.46-0.51
#   x 0.55 drawn (foreshortened ~0.71 on the head's 45 deg flank). SIDE: (909.9, 407.8) -> Y -0.70 z 4.22 (see UNITS note).
EYE_X, EYE_Z = 0.98, 4.29             # "eye placement" (mirrored; front view)
EYE_HALF_W, EYE_HALF_H = 0.35, 0.29   # "eye size" (true half-sizes on the head surface)
EYE_DEPTH, EYE_SINK = 0.10, 0.06      # dome depth / how far it sinks (proud = depth - sink)
EYE_ROUGHNESS = 0.12                  # "glossy eyes": the eye material's roughness
HL_OFF = (0.10, 0.115)                # "highlight dot" offset on the eye: up, toward +X (front: both dots up-right of centre,
                                      #   (+0.08, +0.10) drawn; side: Y +0.09)
HL_R, HL_DEPTH, HL_SINK = 0.045, 0.022, 0.010    # highlight dot radius (drawn 0.07-0.08 wide) / depth / sink
# ---- BEAK (FRONT orange blob x 156..235 y 411..461, centroid X -0.14 z 3.89 -- built on the centreline; SIDE x 822..869,
#   tip (822, 452) -> Y -1.575 z 3.78, root z 3.71..4.21)
#   v1.1: part of the SDF shell (smooth via the baked normal map, crisp iso-cut edge) = a HOOKED chain of round cones: convex
#   top curving down to the point, near-straight underside, cross-section wider than tall (BEAK_SX).
BEAK_CHAIN = [((0.0, -1.05, 3.98), 0.25), ((0.0, -1.35, 3.97), 0.19), ((0.0, -1.50, 3.88), 0.11), ((0.0, -1.585, 3.76), 0.015)]
BEAK_TIP = BEAK_CHAIN[-1][0]          # "beak tip"
BEAK_SX = 1.45                        # "beak width": cross-section width / height
BEAK_K = 0.04                         # beak -> head blend (small: a crisp crease)
# ---- EMBER JEWEL (FRONT orange blob centroid (204.0, 615.7) -> X -0.01 z 2.15, 0.13 x 0.18 + its ink outline; SIDE Y -0.77)
JEWEL_Z = 2.15
JEWEL_HALF = (0.13, 0.085)            # "jewel size": half-height, half-width
JEWEL_CORE = (0.065, 0.042)           # inner red diamond
# ---- LEGS (FRONT pink centres X +-0.47 at z 0.95 -> +-0.62 at z 0.37, width 0.18-0.20; ring lines y 751 / 773; SIDE Y
#   +0.20 at z 0.9 -> +0.14 at z 0.36, width 0.19-0.22)
HIP = (0.47, 0.17, 1.30)              # leg top (hidden in the body; L, mirrored)
ANKLE = (0.62, 0.14, 0.22)            # "ankle"
LEG_R = 0.10                          # "leg thickness" (radius)
LEG_RINGS_Z = (0.79, 0.58)            # "ring segments" heights
LEG_RING_r = 0.02
# ---- FEET (FRONT foot x -1.18..-0.29 / +0.30..+1.18 at z 0.13-0.19; SIDE toes forward to Y -0.58, back toe to +0.61)
PAD = (0.14, 0.13, 0.12)              # "foot pad": half Y, half X, half Z (centre at the ankle, z = half Z)
TOE_R = 0.10                          # "toe thickness" at the root
TOES = [("outer", (0.80, -0.60), 0.70), ("mid", (0.35, -0.94), 0.72), ("inner", (-0.40, -0.92), 0.60),
        ("back", (0.10, 1.0), 0.45)]  # "toes": direction (L; X mirrored), length
TOE_TIP_Z = 0.012
# ---- PARTICLES (static stand-in): EMBERS = orange blobs FRONT (204,180)(196,189)(178,206)(163,230) + SIDE (990,174)(982,186)
#   (958,216)(946,246); ASH = dark blobs FRONT |X| 1.43-1.59 z 2.64-3.65, SIDE Y -1.25..-0.99 / +1.39..+1.52, BACK |X| 1.5
EMBERS = [((-0.01, 0.11, 6.53), 0.05, 0.020), ((-0.09, 0.02, 6.43), 0.09, 0.034), ((-0.27, -0.21, 6.20), 0.09, 0.034),
          ((-0.42, -0.34, 5.92), 0.11, 0.040)]   # centre, length, half-width
EMBER_AXIS = (0.35, 0.25, 0.90)
ASH = [((-1.53, 1.45, 3.62), (0.05, 0.05, 0.065)), ((1.43, 1.39, 3.50), (0.075, 0.07, 0.09)),
       ((1.52, 1.50, 3.71), (0.03, 0.03, 0.035)), ((1.57, 1.52, 3.15), (0.065, 0.06, 0.095)),
       ((1.50, 1.46, 2.70), (0.085, 0.08, 0.09)), ((-1.59, -1.30, 3.35), (0.065, 0.06, 0.105)),
       ((-1.51, -1.25, 2.80), (0.085, 0.08, 0.10))]  # centre, half-sizes
# ---- GLOW (palette tiers; the sheet draws glow on the ring, jewel and embers only)
GLOW_REGIONS = {"ring", "jewel", "jewel_core", "ember"}
GLOW_CAP = 2.0                        # emissive palette ceiling (phone cap)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- clips (PLACEHOLDER: the artist has not given the movement). 24 fps; integer harmonics per loop -> frame N+1 == 1.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH = (0.035, 2)              # "breathing bob": neck lift (u), cycles per loop
IDLE_HEAD_NOD = 1.5                   # head nod deg (lags the breath)
IDLE_WING = 2.5                       # wings lift with the breath (deg)
CREST_SWAY = (4.0, 8.0, 1, 0.9)       # "crest sway": root deg, tip deg, cycles, lag (side-to-side)
CREST_NOD = (2.0, 5.0, 2, 0.9)        # crest fore-aft sway
WALK_N = 32                           # "waddle loop": 1.33 s, two steps
WALK_ROLL = 7.0                       # "waddle": body roll toward the planted foot (deg)
WALK_BOB = 0.05                       # body bob (u, twice per loop)
WALK_YAW = 4.0                        # body yaw (deg)
WALK_LIFT = 0.16                      # "step height": foot lift (u)
WALK_KNEE = 22.0                      # shin fold during the lift (deg)
WALK_TOE = 18.0                       # toes droop while lifted (deg)
WALK_WING = 6.0                       # wings flap out a little with the roll (deg)

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
          "design/reference/phoenix_hatchling-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; three true orthos; floor z = 0 at the soles", "overrides": OVERRIDES}
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


def mir(p, sg):
    return np.array([p[0] * sg, p[1], p[2]], float)


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

# =========================================================================== 1. shell SDF: head + crest / body + wings + tail / collar
t_sdf = time.time()


def chain_segments(chain):
    return [(np.array(chain[i][0], float), chain[i][1], np.array(chain[i + 1][0], float), chain[i + 1][1])
            for i in range(len(chain) - 1)]


CREST_SEGS = [s for ch in (CREST_MAIN, CREST_MID, CREST_LOW) for s in chain_segments(ch)]
CURLS = [CURL_MAIN, CURL_MID, CURL_LOW]
BEAK_SEGS = chain_segments(BEAK_CHAIN)
TAIL_SEGS = [(np.array(a, float), ra, np.array(b, float), rb) for a, ra, b, rb in TAIL_FEATHERS]

def sd_head(P):
    return FP.sd_ell2(P, HEAD_C, HEAD_RXY, HEAD_RZ[0], HEAD_RZ[1])


def sd_curl(P, cu):
    c, R0, r, rc = cu
    return np.minimum(FP.sd_torus(P, c, CURL_AXIS, R0, r), SD.sd_sphere(np.atleast_2d(P), c, rc))


def sd_crest(P):
    P = np.atleast_2d(P)
    d = np.full(len(P), 1e9)
    for a, ra, b, rb in CREST_SEGS:
        d = np.minimum(d, SD.sd_round_cone(P, a, b, ra, rb))
    for cu in CURLS:
        d = np.minimum(d, sd_curl(P, cu))
    return d


def sd_body(P):
    P = np.atleast_2d(P)
    return np.minimum(FP.sd_ell2(P, BODY_C, BODY_RXY, BODY_RZ[0], BODY_RZ[1]),
                      SD.sd_round_cone(P, NECK[0], NECK[2], NECK[1], NECK[3]))


def sd_beak(P):
    Q = np.atleast_2d(P).copy()
    Q[:, 0] /= BEAK_SX                                   # wider than tall (conservative: the scaled distance underestimates)
    d = np.full(len(Q), 1e9)
    for a, ra, b, rb in BEAK_SEGS:
        d = np.minimum(d, SD.sd_round_cone(Q, a, b, ra, rb))
    return d


def sd_tail(P):
    P = np.atleast_2d(P)
    d = np.full(len(P), 1e9)
    for a, ra, b, rb in TAIL_SEGS:
        d = np.minimum(d, SD.sd_round_cone(P, a, b, ra, rb))
    return d


def wing_hem_z(P, sg):
    """scalloped hem: feather lobes hanging down (pointed tips, rounded notches), WING_HEM[2] per half-turn round the body
    axis (front -> back)"""
    z0, amp, lobes, ph = WING_HEM
    th = np.arctan2(P[:, 1] - BODY_C[1], np.maximum(sg * P[:, 0], 1e-6))     # -pi/2 front .. +pi/2 back
    f = (lobes * (th / math.pi + 0.5) + ph) % 1.0
    return z0 + amp * (1.0 - (1.0 - np.abs(2.0 * f - 1.0)) ** 2)


def sd_wing(P, s):
    """a wing PANEL hugging the body side: the shell ellipsoid cut by the inner plane sg * X = WING_X0 and by the hem"""
    sg = 1.0 if s == "L" else -1.0
    P = np.atleast_2d(P)
    d = FP.sd_ell2(P, BODY_C, (BODY_RXY[0] + WING_T, BODY_RXY[1] + WING_T), BODY_RZ[0] - WING_TOP_SINK, BODY_RZ[1] + WING_DROP)
    d = SD.smax(d, WING_X0 - sg * P[:, 0], WING_CUT_K)
    return SD.smax(d, wing_hem_z(P, sg) - P[:, 2], WING_CUT_K)


def sd_collar_base(P):
    """the collar tube + its inner fill ring (rises to meet the head's underside: the head sits into the collar)"""
    return np.minimum(FP.sd_oval_torus(P, COLLAR_C, COLLAR_R0, COLLAR_SY, COLLAR_AB[0], COLLAR_AB[1], COLLAR_CENTRE_T),
                      FP.sd_oval_torus(P, (COLLAR_C[0], COLLAR_C[1], COLLAR_FILL[3]), COLLAR_FILL[0], COLLAR_SY,
                                       COLLAR_FILL[1], COLLAR_FILL[2]))

PUFFS = {"big": FP.torus_puffs(COLLAR_C, COLLAR_R0, COLLAR_SY, COLLAR_AB[0], COLLAR_AB[1], PUFF_BIG[0], PUFF_BIG[1],
                               PUFF_BIG[2], PUFF_PH, PUFF_SEED, COLLAR_CENTRE_T),
         "small": FP.torus_puffs(COLLAR_C, COLLAR_R0, COLLAR_SY, COLLAR_AB[0], COLLAR_AB[1], PUFF_SMALL[0], PUFF_SMALL[1],
                                 PUFF_SMALL[2], PUFF_PH, PUFF_SEED + 101, COLLAR_CENTRE_T)}
_wr = np.random.default_rng(PUFF_SEED + 401).uniform(0.0, 1.0, len(PUFFS["big"]["R"]))
WISP_MASK = (PUFFS["big"]["N"][:, 2] > WISP_NZ) & (_wr < WISP_P)
PC = np.vstack([PUFFS[k]["C"] for k in PUFFS])
PR = np.concatenate([PUFFS[k]["R"] for k in PUFFS])


def sd_collar(P):
    return np.minimum(sd_collar_base(P), FP.sd_spheres_min(np.atleast_2d(P), PC, PR))


GROUP_SDF = {"head": sd_head, "crest": sd_crest, "beak": sd_beak, "collar": sd_collar, "body": sd_body, "tail": sd_tail,
             "wing_L": lambda P: sd_wing(P, "L"), "wing_R": lambda P: sd_wing(P, "R")}
GROUP_REGION = {"head": "head", "crest": "crest", "beak": "beak", "collar": "collar", "body": "body", "tail": "tail",
                "wing_L": "wing", "wing_R": "wing"}
GROUPS = list(GROUP_SDF)
report["collar_fluff"] = {"seed": PUFF_SEED, "puffs": {k: int(len(v["R"])) for k, v in PUFFS.items()},
                          "wisp_puffs": int(WISP_MASK.sum()), "k": PUFF_K}

GLO = np.array([-1.75, -1.75, 0.85]); GHI = np.array([1.75, 2.00, 6.70])
g = SD.Grid(GLO, GHI, SDF_STEP, SDF_BAND)
_hc = np.array(HEAD_C); _hr = np.array([HEAD_RXY[0], HEAD_RXY[1], max(HEAD_RZ)])
g.apply(sd_head, _hc - _hr, _hc + _hr, 0.0)
for a, ra, b, rb in CREST_SEGS:
    g.apply(lambda P, a=a, b=b, ra=ra, rb=rb: SD.sd_round_cone(P, a, b, ra, rb), np.minimum(a, b) - ra, np.maximum(a, b) + ra,
            CREST_K)
for cu in CURLS:
    c_ = np.array(cu[0]); e_ = cu[1] + cu[2]
    g.apply(lambda P, cu=cu: sd_curl(P, cu), c_ - e_, c_ + e_, CURL_K)
_bk = np.array([p for p, _ in BEAK_CHAIN])
g.apply(sd_beak, _bk.min(0) - 0.45, _bk.max(0) + 0.45, BEAK_K)
_bc = np.array(BODY_C); _br = np.array([BODY_RXY[0], BODY_RXY[1], max(BODY_RZ)])
g.apply(lambda P: FP.sd_ell2(P, BODY_C, BODY_RXY, BODY_RZ[0], BODY_RZ[1]), _bc - _br, _bc + _br, 0.0)
_na, _nb = np.array(NECK[0]), np.array(NECK[2])
g.apply(lambda P: SD.sd_round_cone(P, _na, _nb, NECK[1], NECK[3]), np.minimum(_na, _nb) - NECK[1], np.maximum(_na, _nb) + NECK[1],
        NECK_K)
_wc = np.array(BODY_C)
_wr3 = np.array([BODY_RXY[0] + WING_T, BODY_RXY[1] + WING_T, max(BODY_RZ[0], BODY_RZ[1] + WING_DROP)])
for s, sg in SIDES:
    _wlo = _wc - _wr3; _whi = _wc + _wr3
    if sg > 0:
        _wlo = _wlo.copy(); _wlo[0] = WING_X0 - 0.1
    else:
        _whi = _whi.copy(); _whi[0] = -WING_X0 + 0.1
    g.apply(lambda P, s=s: sd_wing(P, s), _wlo, _whi, WING_K)
_tp = np.array([p for a, ra, b, rb in TAIL_SEGS for p in (a, b)])
g.apply(sd_tail, _tp.min(0) - 0.36, _tp.max(0) + 0.36, TAIL_K)
_cc = np.array(COLLAR_C); _ce = np.array([COLLAR_R0 + COLLAR_AB[0], COLLAR_R0 * COLLAR_SY + COLLAR_AB[0], COLLAR_AB[1]])
g.apply(sd_collar_base, _cc - _ce, _cc + _ce + np.array([0, 0, max(0.0, COLLAR_FILL[3] + COLLAR_FILL[2] - COLLAR_C[2] - COLLAR_AB[1])]), COLLAR_K)
for c_, r_ in zip(PC, PR):
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
print("RETOPO", json.dumps(report["retopo"]))

# =========================================================================== 3. region fields + iso cuts on the shell
DGR = np.stack([GROUP_SDF[gn](LV) for gn in GROUPS], 1)


def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


_wC = PUFFS["big"]["C"][WISP_MASK]; _wR = PUFFS["big"]["R"][WISP_MASK]
FIELDS = {"f_" + gn: own_field(DGR, j) for j, gn in enumerate(GROUPS)}
FIELDS["ringd"] = RING_W - sd_collar_base(LV)     # > 0 within RING_W of the collar BASE (smooth: the puffs would make it wavy)
FIELDS["wisp"] = sd_collar_base(LV) - (FP.sd_spheres_min(LV, _wC, _wR) if len(_wR) else np.full(len(LV), -1.0))
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
in_collar = lambda a, b: a[LAY["f_collar"]] > -TOL and b[LAY["f_collar"]] > -TOL
on_head = lambda a, b: all(v[LAY["f_collar"]] < TOL and v[LAY["f_head"]] > -0.05 for v in (a, b))
# only the colour boundaries are cut: collar | everything (smoke vs maroon / taupe), the beak, the glowing ring band on the
# head base, and the collar's light wisp crowns. head | crest and body | wing | tail share a tone: their ownership only
# drives the region names + the weights.
CUTS = [("f_collar", 0.0, None), ("f_beak", 0.0, None), ("ringd", 0.0, on_head), ("wisp", WISP_TAU, in_collar)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gn] for gn in GROUPS], 1), 1)
shell_reg = np.array([GROUP_REGION[GROUPS[o]] for o in owner], dtype=object)
is_collar = FVAL["f_collar"] > 0.0
shell_reg[is_collar] = "collar"
_nc = [j for j, gn in enumerate(GROUPS) if gn != "collar"]
_own2 = np.array(_nc)[np.argmax(np.stack([FVAL["f_" + GROUPS[j]] for j in _nc], 1), 1)]
_fix = ~is_collar & (shell_reg == "collar")                       # a face the collar no longer owns after the cut
shell_reg[_fix] = [GROUP_REGION[GROUPS[j]] for j in _own2[_fix]]
shell_reg[~is_collar & (shell_reg == "head") & (FVAL["ringd"] > 0.0)] = "ring"
shell_reg[is_collar & (FVAL["wisp"] > WISP_TAU)] = "smoke_light"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 4. closed parts
PARTS = []
# ---- eyes + highlight dots (front-view placement, on the head surface)
EYES = {}
for s, sg in SIDES:
    p_, n_ = FP.ell2_at(HEAD_C, HEAD_RXY, HEAD_RZ[0], HEAD_RZ[1], sg * EYE_X, EYE_Z, front=True)
    c_ = p_ - n_ * EYE_SINK
    V, F, R = FP.ellipsoid_dome(c_, n_, EYE_HALF_H, EYE_HALF_W, EYE_DEPTH, n=24, rings=8, region="eye")
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    a_, u_, v_ = FP.frame_from_axis(n_, (0, 0, 1))
    oh, ow = HL_OFF[0], -HL_OFF[1] * np.sign(v_[0])          # toward +X in world for both eyes (the drawn light)
    rho2 = (oh / EYE_HALF_H) ** 2 + (ow / EYE_HALF_W) ** 2
    hgt = EYE_DEPTH * math.sqrt(max(1.0 - rho2, 0.0))
    hp = c_ + u_ * oh + v_ * ow + a_ * hgt
    hn = unit(u_ * oh / EYE_HALF_H ** 2 + v_ * ow / EYE_HALF_W ** 2 + a_ * hgt / EYE_DEPTH ** 2)
    V, F, R = FP.ellipsoid_dome(hp - hn * HL_SINK, hn, HL_R, HL_R, HL_DEPTH, n=12, rings=4, region="eye_hi")
    PARTS.append(FP.Part("eye_hi." + s, V, F, R, bone="head"))
    EYES[s] = {"surface": p_, "normal": n_, "highlight": hp}
# ---- ember jewel on the chest
JP, JN = FP.ell2_at(BODY_C, BODY_RXY, BODY_RZ[0], BODY_RZ[1], 0.0, JEWEL_Z, front=True)
V, F, R = FP.bipyramid(JP - JN * 0.005, JN, JEWEL_HALF[0], JEWEL_HALF[1], 0.045, 0.05, region="jewel")
PARTS.append(FP.Part("jewel", V, F, R, bone="body"))
V, F, R = FP.bipyramid(JP - JN * 0.005, JN, JEWEL_CORE[0], JEWEL_CORE[1], 0.062, 0.02, region="jewel_core")
PARTS.append(FP.Part("jewel_core", V, F, R, bone="body"))
# ---- legs, ring segments, feet
LEGS = {}
for s, sg in SIDES:
    h_, a_ = mir(HIP, sg), mir(ANKLE, sg)
    V, F, R = FP.taper(h_, a_, LEG_R, [(0.0, 1.0), (0.5, 0.98), (1.0, 0.95)], n=10, region="leg", root_cap=LEG_R)
    p = FP.Part("leg." + s, V, F, R); p.leg = s; PARTS.append(p)
    for k, zr in enumerate(LEG_RINGS_Z):
        f_ = (h_[2] - zr) / (h_[2] - a_[2])
        V, F, R = FP.torus(LEG_R * (1.0 - 0.05 * f_) + 0.004, LEG_RING_r, 20, 6, h_ + (a_ - h_) * f_, a_ - h_, region="leg_ring")
        PARTS.append(FP.Part("leg_ring.%s.%d" % (s, k), V, F, R, bone="shin_" + s))
    pc = np.array([a_[0], a_[1], PAD[2]])
    V, F, R = FP.ellipsoid_dome(pc, (0, 0, 1), PAD[0], PAD[1], PAD[2], n=12, rings=6, region="leg")
    PARTS.append(FP.Part("pad." + s, V, F, R, bone="foot_" + s))
    root_ = np.array([a_[0], a_[1], TOE_R])
    tips = {}
    for nm, (dx, dy), ln in TOES:
        d_ = unit([dx * sg, dy, 0.0])
        tip = root_ + d_ * ln; tip[2] = TOE_TIP_Z
        V, F, R = FP.taper(root_, tip, TOE_R, [(0.0, 1.0), (0.35, 0.68), (0.7, 0.38), (1.0, 0.0)], n=8, region="leg",
                           root_cap=0.03)
        PARTS.append(FP.Part("toe.%s.%s" % (s, nm), V, F, R, bone="foot_" + s))
        tips[nm] = tip
    LEGS[s] = {"hip": h_, "ankle": a_, "toe_tips": tips}
# ---- particles (static stand-in for the sheet's ember + ash particles)
for k, (c_, ln, wd) in enumerate(EMBERS):
    V, F, R = FP.teardrop(np.array(c_, float), EMBER_AXIS, ln, wd, n=6, region="ember")
    PARTS.append(FP.Part("ember.%d" % k, V, F, R, bone="head"))
for k, (c_, sz) in enumerate(ASH):
    V, F, R = FP.blob(np.array(c_, float), sz, seed=k + 1, n=6, region="ash")
    PARTS.append(FP.Part("ash.%d" % k, V, F, R, bone="body"))

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
REG = ["head", "crest", "collar", "smoke_light", "body", "wing", "tail", "eye", "eye_hi", "beak", "ring", "jewel",
       "jewel_core", "leg", "leg_ring", "ember", "ash"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
EYE_REGIONS = ("eye", "eye_hi")
MATI = np.isin(rid, [REG.index(r) for r in EYE_REGIONS]).astype(np.int32)


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
    mat.use_backface_culling = True                      # opaque closed solids: single-sided (glTF doubleSided false)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    MAT_EYE.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = EYE_ROUGHNESS
    return out


def glow_check(pal):
    """the sheet draws glow on the collar ring, the chest jewel and the embers: exactly those regions emit, under the cap"""
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    peak = max([max(c / 255.0 for c in pal["regions"][n]["emission"]) * pal["regions"][n].get("emission_scale", 1.0)
                for n in lit] or [0.0])
    tiers = {n: pal["regions"][n].get("emission_scale", 1.0) for n in lit}
    return {"skin": pal["skin"], "glowing_regions": lit, "emission_scale_tiers": tiers, "peak_srgb_x_scale": round(peak, 4),
            "peak_x_strength": round(peak * pal["material"].get("emission_strength", 1.0), 4), "cap": GLOW_CAP,
            "ok": set(lit) == GLOW_REGIONS and peak * pal["material"].get("emission_strength", 1.0) <= GLOW_CAP}


MAT_BODY = make_mat(UNIT + "_body")
MAT_EYE = make_mat(UNIT + "_eye")
pal_default = PAL.load(UNIT, "default")
report["glow"] = glow_check(pal_default)
assert report["glow"]["ok"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_EYE)
me.polygons.foreach_set("material_index", MATI)
shade = jitter(VALL, FALL)
_clean = np.isin(rid, [REG.index(r) for r in ("eye", "eye_hi", "beak", "ring", "jewel", "jewel_core", "ember", "ash",
                                                "leg_ring")])
shade[_clean] = 1.0
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the beak tip on the midline
anchor = np.array(HEAD_C) - SHIFT
landmark = np.array(BEAK_TIP) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre -> beak tip on the midline (the beak points forward, -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = {"eyes": {s: {"surface": (v["surface"] - SHIFT).round(4).tolist(), "normal": v["normal"].round(4).tolist(),
                               "half_wh": [EYE_HALF_W, EYE_HALF_H], "proud": round(EYE_DEPTH - EYE_SINK, 4)}
                           for s, v in EYES.items()},
                  "beak": {"chain": [list(p) for p, _ in BEAK_CHAIN], "sx": BEAK_SX, "rule": "SDF shell, iso-cut"},
                  "ring": {"width": RING_W, "rule": "head surface within RING_W of the collar, iso-cut, glowing"},
                  "jewel": {"surface": (JP - SHIFT).round(4).tolist()}}

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
_pp = {}
for p in PARTS:
    key = p.name.split(".")[0]
    _pp[key] = _pp.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF),
                  "per_region": _reg_tris,
                  "per_part": {"shell (head + crest + body + wings + tail + collar, one SDF)": tri_count_F(CF), **_pp}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The one SDF shell carries the head, crest curls, "
                            "body, wings, tail and the lumpy collar (~13k tris keeps the collar puffs and crest curls in "
                            "the LOW silhouette); the closed parts (eyes, beak, ring, jewel, legs, feet, particles) add "
                            "the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (SDF shell, lathes with poles, tori)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "sheet": {"front": "H 6.52 W 2.95", "side": "H 6.63 D 3.51", "back": "H 6.40 W 3.17"}},
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
low["conquest_source"] = "from scratch: design/reference/phoenix_hatchling-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = ("Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base "
                                    "Color (glow: collar ring, chest jewel, ember flecks)")
# focus boxes for the close-ups (world, after the centre shift) = the sheet's detail panels (face / crest / smoke collar /
# body + feet)
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-1.35, -1.7, 3.45]) - SHIFT).tolist(), (np.array([1.35, -0.4, 4.95]) - SHIFT).tolist()],
    "crest": [(np.array([-0.6, -0.6, 5.1]) - SHIFT).tolist(), (np.array([0.6, 0.8, 6.7]) - SHIFT).tolist()],
    "collar": [(np.array([-1.6, -1.4, 2.4]) - SHIFT).tolist(), (np.array([1.6, 0.2, 3.7]) - SHIFT).tolist()],
    "body_feet": [(np.array([-1.5, -1.0, -0.05]) - SHIFT).tolist(), (np.array([1.5, 1.9, 2.8]) - SHIFT).tolist()],
    "views": {"face": [0.0, 4.0], "crest": [90.0, 8.0], "collar": [0.0, 6.0], "body_feet": [35.0, 10.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "collar_fluff", "glow")}, default=str))
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
BAKE_NODES = {}
for mat_ in (MAT_BODY, MAT_EYE):
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
    for mat_ in (MAT_BODY, MAT_EYE):
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
body_faces = MATI == 0
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "the SDF shell (marching tets) + every closed part",
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
# wire the textures into the BODY material only (the glossy eyes keep flat colour)
nt = MAT_BODY.node_tree
tn, ta = BAKE_NODES[MAT_BODY.name]
for n_ in BAKE_NODES[MAT_EYE.name]:
    MAT_EYE.node_tree.nodes.remove(n_)
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
                     "provenance": "pixel-sampled from the sheet's five palette chips (intake swatches) + drawn-surface "
                                   "samples (see the palette notes)"}
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


CREST_Z = (5.25, 5.92, 6.32)          # crest.001 from the head top to the curl fork, crest.002 into the top curl
BONES = [("body", P_((0.0, 0.26, 1.30)), P_((0.0, 0.20, 2.60)), "root"),
         ("neck", P_((0.0, 0.15, 2.60)), P_((0.0, 0.08, 3.35)), "body"),
         ("head", P_((0.0, 0.06, 3.35)), P_((0.0, 0.10, CREST_Z[0])), "neck"),
         ("crest.001", P_((0.0, 0.12, CREST_Z[0])), P_((-0.02, 0.18, CREST_Z[1])), "head"),
         ("crest.002", P_((-0.02, 0.18, CREST_Z[1])), P_(CURL_MAIN[0]), "crest.001")]
for s, sg in SIDES:
    BONES.append(("wing_" + s, P_(mir((0.80, 0.40, 2.45), sg)), P_(mir((1.05, 0.45, 1.05), sg)), "body"))
for s, sg in SIDES:
    Lg = LEGS[s]
    knee = mir((0.49, 0.17, 1.00), sg)
    BONES.append(("thigh_" + s, P_(Lg["hip"] + np.array([0, 0, 0.25])), P_(knee), "root"))
    BONES.append(("shin_" + s, P_(knee), P_(Lg["ankle"]), "thigh_" + s))
    BONES.append(("foot_" + s, P_(Lg["ankle"]), P_(Lg["toe_tips"]["mid"] + np.array([0, 0, 0.05])), "shin_" + s))
CONNECTED = {"crest.002", "shin_L", "shin_R", "foot_L", "foot_R"}
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
    e.use_connect = nm in CONNECTED
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences). Shell: a soft ownership over the SDF groups at the rest shell (exp(-(d - d_min) / TAU_W):
#      seams blend across ~0.1), group -> bone: head -> head, crest -> head / crest.001 / crest.002 along z (rigkit vine
#      weights), collar -> neck, body + tail -> body, wing_s -> wing_s. Parts: rigid on their bone; legs split thigh /
#      shin / foot along z; pads + toes foot.
TAU_W = 0.035
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
Dg = np.stack([GROUP_SDF[gn](Pw) for gn in GROUPS], 1)
Sg = np.exp(-(Dg - Dg.min(1, keepdims=True)) / TAU_W)
Sg /= Sg.sum(1, keepdims=True)
GB = {"head": "head", "beak": "head", "collar": "neck", "body": "body", "tail": "body", "wing_L": "wing_L", "wing_R": "wing_R"}
for j, gn in enumerate(GROUPS):
    if gn == "crest":
        Wv = K.vine_weights(Pw[:, 2] - CREST_Z[0], CREST_Z[2] - CREST_Z[0], 2, parent_s0=-0.10)
        Wt[a_:b_, J["head"]] += Sg[:, j] * Wv[:, 0]
        Wt[a_:b_, J["crest.001"]] += Sg[:, j] * Wv[:, 1]
        Wt[a_:b_, J["crest.002"]] += Sg[:, j] * Wv[:, 2]
    else:
        Wt[a_:b_, J[GB[gn]]] += Sg[:, j]
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "leg", None):
        s = p.leg
        z_ = p.V[:, 2]
        w_thigh = smoothstep(0.95, 1.12, z_)
        w_foot = smoothstep(0.30, 0.16, z_)
        Wt[a_:b_, J["thigh_" + s]] = w_thigh
        Wt[a_:b_, J["foot_" + s]] = w_foot
        Wt[a_:b_, J["shin_" + s]] = np.clip(1.0 - w_thigh - w_foot, 0.0, None)
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
                  "rule": "shell: soft SDF-group ownership exp(-(d - d_min) / %.3f) -> head / crest chain (vine weights "
                          "along z) / neck (collar) / body (+ tail) / wing_L / wing_R; parts rigid (eyes, beak, ring, "
                          "embers: head; jewel, ash: body); legs thigh / shin / foot along z, pads + toes foot" % TAU_W}
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


CLIPS = {"idle": IDLE_N, "walk": WALK_N}


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        amp, cyc = IDLE_BREATH
        br = 0.5 * (1.0 - math.cos(TAU * cyc * u))                    # 0 = exhaled .. 1 = inhaled
        P["neck"] = ((0.0, 0.0, amp * br), [], None)
        P["head"] = (None, [("X", -IDLE_HEAD_NOD * math.sin(TAU * cyc * u - 0.8))], None)
        for s, sg in SIDES:
            P["wing_" + s] = (None, [("Y", -sg * IDLE_WING * br)], None)
        for k in range(2):
            bn = "crest.%03d" % (k + 1)
            ry = K.vine_wave(u, k, 2, CREST_SWAY[0], CREST_SWAY[1], CREST_SWAY[3], CREST_SWAY[2], 0.0)
            rx = K.vine_wave(u, k, 2, CREST_NOD[0], CREST_NOD[1], CREST_NOD[3], CREST_NOD[2], -1.2)
            P[bn] = (None, [("Y", ry), ("X", rx)], None)
        info = {"breath": br}
    else:
        ph = TAU * u
        lift = {"L": max(0.0, math.sin(ph)), "R": max(0.0, -math.sin(ph))}   # L steps in the first half, R in the second
        roll = -WALK_ROLL * math.sin(ph)                                # lean over the planted foot (away from the lifted)
        bob = WALK_BOB * 0.5 * (1.0 - math.cos(2 * ph))
        P["body"] = ((0.0, 0.0, bob), [("Y", roll), ("Z", WALK_YAW * math.sin(ph))], None)
        P["neck"] = (None, [("Y", -0.5 * roll)], None)                # the head stays level-ish
        P["head"] = (None, [("X", 2.0 * math.sin(2 * ph - 0.6))], None)
        for k in range(2):
            bn = "crest.%03d" % (k + 1)
            P[bn] = (None, [("Y", K.vine_wave(u, k, 2, 5.0, 10.0, 0.9, 1, 1.6)),
                            ("X", K.vine_wave(u, k, 2, 2.0, 5.0, 0.9, 2, 0.4))], None)
        for s, sg in SIDES:
            l_ = lift[s] ** 1.5
            P["wing_" + s] = (None, [("Y", -sg * WALK_WING * (0.5 + 0.5 * math.sin(ph * sg)))], None)
            P["thigh_" + s] = ((0.0, 0.0, WALK_LIFT * l_), [("X", -6.0 * l_)], None)
            P["shin_" + s] = (None, [("X", WALK_KNEE * l_)], None)
            P["foot_" + s] = (None, [("X", -WALK_KNEE * l_ + WALK_TOE * l_)], None)
        info = {"roll": roll, "lift_L": lift["L"], "lift_R": lift["R"]}
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


SOLE = {s: np.nonzero((Wt[:, J["foot_" + s]] > 0.5) & (VALL[:, 2] < 0.03))[0] for s, _ in SIDES}
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
        rows.append({"minz": float(C[:, 2].min()), "root": float((rig.matrix_world @ pbs["root"].head).length),
                     **{"sole_" + s: [float(C[SOLE[s], 2].min()), float(C[SOLE[s], 2].max())] for s, _ in SIDES},
                     "crest_tip": list(pbs["crest.002"].tail), "neck_z": float(pbs["neck"].head.z)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    ct = np.array([r["crest_tip"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 4),
                    "sole_min_z": {s: round(float(np.array([r["sole_" + s][0] for r in R_]).min()), 4) for s, _ in SIDES},
                    "sole_lift_max": {s: round(float(np.array([r["sole_" + s][0] for r in R_]).max()), 4) for s, _ in SIDES},
                    "crest_tip_p2p_xyz": (ct.max(0) - ct.min(0)).round(4).tolist(),
                    "neck_bob": round(float(g_("neck_z").max() - g_("neck_z").min()), 4)}
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
rig["conquest_rig"] = ("phoenix_hatchling v1: root > body > neck > head > crest.001 > crest.002; body > wing_L / wing_R; "
                       "root > thigh > shin > foot (L / R)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given the Phoenix Hatchling's movement; open question): "
                               "idle = 4 s breathing bob (neck +%.3f u, %d breaths) + crest sway; walk = %.2f s in-place "
                               "waddle step (body roll %.0f deg, foot lift %.2f u)" % (IDLE_BREATH[0], IDLE_BREATH[1],
                                                                                    WALK_N / K.FPS, WALK_ROLL, WALK_LIFT))
low["conquest_locomotion"] = "biped walker (feet flat on the floor at rest, z 0)"
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
              "structure": "armature identity + skinned 'phoenix_hatchling' (body primitive + glossy eye primitive); natural "
                           "scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default)}, "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
