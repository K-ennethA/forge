"""Mystic Snow Spirit -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/mystic_snow_spirit_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-11 "NEW UNITS x6" (section MYSTIC SNOW SPIRIT) + the sheet
design/reference/mystic_snow_spirit-character-sheet.png (intake: design/reference/mystic_snow_spirit/intake.json, --cuts
485,880 --region 0,0,1195,941, axes RE-PINNED by this lane: --axis front=248,back=671). Contract:
improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. FRONT + SIDE are the true orthos:
  FRONT view (the unit's left on image right):   X = (x_px - 248) / 100   ; z = (821 - y_px) / 100
  SIDE view (the face on image LEFT = -Y):        Y = (x_px - 1032) / 100  ; z = (822 - y_px) / 100
Axis re-pin: the intake's base rule put the FRONT axis at 244 (the lumpy snow-pile foot); the head (widest row y 410:
164-331 -> 247.5), the middle ball (y 490-570 row centres 249-249.5) and the skirt rows (y 600-760 centres 245-249) put the
body axis at 248. The BACK axis 686 -> 671 (back head widest row y 400: 590-752). The SIDE axis 1032 (the skirt base
centre) is kept: the side view draws the snowman leaning FORWARD (head centre 976.5 = Y -0.555, middle ball -0.46,
skirt top -0.19, skirt foot ~0.0) and that lean is built as drawn (mirror symmetry is about X only).
X +ve = the unit's LEFT (L), front = -Y, floor z = 0.

HOUSE RULE (artist 2026-10-10): the rest pose is MIRROR-SYMMETRIC about X = 0. The sheet places the five floating snowballs
asymmetrically (front view: upper pair at X -1.56 / +1.73, lower pair -1.755 / +1.875); they are built 2 + 2 + 1 (top),
each pair at its mean |X| and the sheet's heights. The face sits ~0.045 right of the head axis on the sheet: built centred.

Pipeline:
  1. BODY = ONE SDF: a round snow HEAD (ellipsoid) + a smaller MIDDLE ball + the conical SNOW-MOUND SKIRT (elliptic sections
     measured per row off the front + side views, its centre drifting back down the cone as drawn) flaring into a chunky
     lumpy RIM (14 rounded lumps, X-mirrored) and 10 FOOT lumps resting on the floor (the floor cut flattens them; arches
     between them show the blue under-glow). Gentle large-scale snow lumps live on the HIGH only (the normal map); the LOW
     is collapse-decimated from the smooth twin, then MIRROR-HALVED (X >= 0 half + its mirror, welded: exact symmetry).
  2. REGIONS iso-cut on the low: snow / blue snow_shade (down-facing: under the head, the middle ball and the rim lumps) /
     faint snow_glow (down-facing and below GLOW_Z: the pile foot).
  3. CLOSED PARTS (mystic_snow_spirit_parts.py): conforming oval EYES (rim / pale-blue iris / dark pupil rings on one smooth
     dome) + a highlight dot, the "o" MOUTH, the pointed snow NOSE cone, two forked TWIG HORNS (shaft + two twigs + snow
     clumps), two long BRANCH ARMS (knotted branch + three twig fingers + snow clumps on the upper surfaces), FIVE FLOATING
     SNOWBALLS, each consumed in a static layered FLAME (4 violet outer licks, 4 cyan licks, a tall violet central tongue,
     a fat pale core) on its own material (opaque, emissive).
  4. palette -> Col / Glow, flat shaded, Smart UV, bake normal + AO from the SDF high + every closed part except the flames.
  5. RIG: root > base > middle > head > horn_L / horn_R; middle > arm_{L,R}.0 > arm_{L,R}.1; base > orb.001-.005 >
     flame.001-.005. Clips PLACEHOLDER (movement not given): idle = body sway + snowballs bobbing on offset phases + flame
     flicker (scale pulse); walk = in-place sliding glide (base flat on the floor), snowballs + flames trailing. glb.
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
import mystic_snow_spirit_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "mystic_snow_spirit"
CHAR_ID = "mystic_snow_spirit"        # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
DEGEN_DIST = 1e-4                     # cut slivers shorter than this are collapsed
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.16                       # >= the largest smooth-union k (RIM_K 0.10) + 2 steps (contract hazard note)
BODY_TRIS = 6000                      # "body mesh detail": the shell's decimation target (before the colour cuts)
# ---- HEAD (FRONT rows: widest y 400-410 164-332 -> half 0.835 on X 247.5; half 66.5 @ y 360 / 64.5 @ y 450 -> an ellipse
#   centred y 405, half-height 0.73 (top y 331). SIDE: widest y 400 901-1052 -> half-depth 0.755, centre x 976.5)
HEAD_C = (0.0, -0.555, 4.16)          # "head centre"
HEAD_R = (0.835, 0.755, 0.73)         # "head size" (half width / depth / height)
# ---- MIDDLE BALL (FRONT rows y 490 191-307, y 500 187-312, y 560 189-308, y 570 192-305 -> half 0.68 x 0.73, centre y 529;
#   SIDE front edge x 925 @ y 520 -> depth half 0.61 (the head's depth / width ratio), centre Y -0.46)
MID_C = (0.0, -0.46, 2.92)            # "middle ball centre"
MID_R = (0.68, 0.61, 0.73)            # "middle ball size"
NECK_K = 0.06                         # head / middle ball blend (the drawn neck pinch: y 470 half 0.445)
WAIST_K = 0.08                        # middle ball / skirt blend (the drawn crease y ~585)
# ---- SKIRT: (z, half-width X (FRONT), half-depth Y (SIDE), centre Y (SIDE)) per measured row; the top knot is buried in
#   the middle ball. FRONT (axis 248): y 590 175-323 | 620 159-342 | 650 145-350 | 680 134-361 | 720 118-378 | 760 94-396.
#   SIDE (axis 1032): y 590 951-1075 | 620 937-1092 | 650 928-1107 | 680 921-1121 | 720 912-1139 | 760 898-1159.
#   Below z ~0.45 the drawn bulge (y 780-790: 66-418 / 879-1182, half 1.755 / 1.515) is the RIM lumps' reach.
SKIRT = [(2.62, 0.50, 0.42, -0.25), (2.31, 0.74, 0.62, -0.19), (2.01, 0.915, 0.775, -0.175), (1.71, 1.025, 0.895, -0.145),
         (1.41, 1.135, 1.00, -0.11), (1.01, 1.30, 1.135, -0.065), (0.61, 1.47, 1.29, -0.03), (0.40, 1.53, 1.32, 0.0),
         (0.22, 1.50, 1.30, 0.0)]
SKIRT_BOT = 0.22                      # the cone's flat underside (inside the ring of rim + foot lumps)
RIM_Z, RIM_R, RIM_IN = 0.40, 0.25, 0.93   # "snow-pile rim": lump centre height, base radius, placement (share of the section)
RIM_AZ = [0.0, 22.0, 47.0, 78.0, 104.0, 131.0, 157.0, 180.0]   # deg from the front toward +X; mirrored (0 / 180 on the plane)
RIM_K = 0.10
FOOT_Z, FOOT_R, FOOT_IN = 0.14, 0.27, 0.88   # "pile foot lumps" resting on the floor (front row y 810: lumps at X -1.4..-1.1,
FOOT_AZ = [14.0, 50.0, 88.0, 128.0, 166.0]   #   -0.34..+0.19, +0.95..+1.24, +1.4..+1.57): mirrored -> 10
FOOT_K = 0.07
LUMP_SEED = 5.0
SNOW_LUMP = (0.010, 9.0)              # "soft snow texture": HIGH-only gentle lumps (amplitude, wavenumber ~0.7 u wavelength)
SHADE_NZ = 0.45                       # "blue snow shadow": surfaces facing down steeper than this take snow_shade
GLOW_Z = 0.20                         # "base under-glow": shaded surfaces below this height glow faint blue
# ---- FACE (FRONT; the face sits 0.045 right of the head axis on the sheet -> built centred)
EYE_X, EYE_Z = 0.27, 4.33             # "eye placement": centres x 225.5 / 279.5 (|dX| 0.225 / 0.315 -> mean 0.27), y 388
EYE_HALF = (0.12, 0.165)              # "eye size": drawn 23 x 32 px (+ the head's sideways foreshortening at X 0.27)
EYE_RINGS = [(0.024, 0.050, "pupil"), (0.044, 0.090, "pupil"), (0.072, 0.118, "eye_iris"), (0.100, 0.147, "eye_iris"),
             (0.12, 0.165, "eye_rim")]   # rings (half u, half v, region inside): pupil 0.36 x 0.56 of the eye (FACE DETAIL
                                      #   panel 9 x 20 of 25 x 36 px), pale-blue iris fill, thin blue outline
EYE_DEPTH = 0.035                     # eye dome height
EYE_HI = (0.014, 0.045, 0.017)        # highlight dot: offset toward +X, up; radius (panel: up-right of the pupil)
MOUTH_Z, MOUTH_HALF = 3.74, (0.05, 0.045)   # "o" mouth (front y 447, 10 x 9 px)
NOSE_Z = 4.06                         # "snow nose": root centre (front y 402-420 top, tip y 433; side tip x 884 y 440)
NOSE_DIR = (0.0, -0.82, -0.57)        # forward-down (side view)
NOSE_LEN, NOSE_R, NOSE_SINK = 0.30, 0.13, 0.10
# ---- HORNS (+X horn; the -X horn mirrors it). FRONT: base (283, 342) / (215, 342) -> |X| 0.34 z 4.79, tip (323, 245) /
#   (173, 247) -> |X| 0.75 z 5.74; upper twig (305,288)->(337,268); lower stub (300,313)->(317,317) / (200,312)->(182,306).
#   SIDE: base x 965 (Y -0.67), tip x 972 (Y -0.60): near vertical.
HORN = [(0.26, -0.69, 4.62), (0.34, -0.67, 4.79), (0.55, -0.64, 5.27), (0.75, -0.60, 5.74)]
HORN_R = [0.07, 0.065, 0.05, 0.032]
TWIG_A = [(0.56, -0.635, 5.29), (0.73, -0.62, 5.44), (0.90, -0.58, 5.55)]
TWIG_A_R = [0.04, 0.033, 0.022]
TWIG_B = [(0.45, -0.66, 5.05), (0.56, -0.69, 5.10), (0.675, -0.72, 5.10)]
TWIG_B_R = [0.034, 0.028, 0.02]
HORN_SNOW = [("shaft", 0.18, 0.16, 0.05), ("shaft", 0.60, 0.15, 0.05), ("shaft", 0.92, 0.17, 0.055), ("twig_a", 0.80, 0.15, 0.05)]
# ---- ARMS (+X arm). FRONT centreline (brown rows): y 505 x 318 | 545 352 | 585 377 | 625 390 | 665 414; fingers @ y 705:
#   x 400 / 423 / 457, tips (402,725) (432,752) (460,722). SIDE centreline: y 505 x 995 | 545 1014 | 585 1032 | 625 1040 |
#   665 1055 | 705 1065; finger tips Y +0.07 / +0.43 / +0.74 (the side view draws the hand ~0.3 lower: z from the FRONT)
ARM = [(0.40, -0.45, 3.26), (0.70, -0.37, 3.16), (1.04, -0.18, 2.76), (1.29, 0.0, 2.36), (1.42, 0.08, 1.96),
       (1.62, 0.21, 1.60), (1.68, 0.26, 1.44)]
ARM_R = [0.14, 0.145, 0.15, 0.14, 0.14, 0.15, 0.145]   # "arm thickness" (front upper arm ~22 px, forearm ~25 px)
ARM_KNOT = [(0.30, 0.15, 0.05)]       # the drawn knot near the elbow (front (365, 550)): (arc share, bulge, width)
FINGERS = [[(1.63, 0.22, 1.56), (1.54, 0.13, 1.20), (1.55, 0.06, 0.95)],
           [(1.66, 0.25, 1.54), (1.75, 0.35, 1.18), (1.83, 0.43, 0.72)],
           [(1.68, 0.26, 1.56), (2.00, 0.53, 1.22), (2.12, 0.72, 0.99)]]
FINGER_R = [[0.095, 0.08, 0.055], [0.095, 0.085, 0.055], [0.09, 0.075, 0.052]]
ARM_SNOW = [(0.07, 0.36, 0.11), (0.31, 0.30, 0.095), (0.60, 0.34, 0.10), (0.88, 0.26, 0.09)]   # (arc share, length, height)
FINGER_SNOW = (0.93, 0.13, 0.05)     # small snow on every finger tip (the sheet's white tips)
# ---- FLOATING SNOWBALLS (white-blob fits: diameters 58-67 px; FRONT centres top (247.5, 232), up (92 / 421, 342),
#   low (72.5 / 435.5, 504); SIDE centres top x 1037, up 1114.5, low 1092)
ORB_R = 0.33                          # "snowball size"
ORBS = {"top": (0.0, 0.05, 5.89), "up": (1.645, 0.825, 4.79), "low": (1.815, 0.60, 3.18)}   # pairs: +X, mirrored
ORB_LEAN = {"top": (0.0, 0.0), "up": (22.0, 8.0), "low": (14.0, 8.0)}   # flame lean deg (outward, back): front-view tips
                                      #   lean out (up pair +-0.44 over 1.07, low +-0.26 over 1.0), side tips lean back
ORB_FLAME_K = {"top": 0.95, "up": 0.92, "low": 0.86}    # flame size (tip 1.14 / 1.07 / 1.0 above the ball centre)
# "flame petals": (region, azimuth deg, root elevation deg, tilt deg from the flame axis, length, radius, curl up,
#   pull in, wide_s). Azimuth 0 = OUTWARD (+X for the L snowballs and the top), 90 = back, -90 = front; a non-zero,
#   non-180 azimuth gets a twin at -az. Each petal leaves the ball tilted out, bellies, then CURLS UP and is PULLED IN
#   toward the flame axis, so the petals converge into one teardrop flame silhouette (v1 previews: diverging licks read as
#   a crown, an opaque lathe mass as a hat). Front view (the sheet's read): violet petals at the left / right edges and the
#   tall violet tip, cyan petals facing front / back (the middle of the silhouette), the pale hot body glimpsed between
#   them. The TOP flame keeps only the +X half (|az| <= 90) and mirrors it (X-symmetric).
FLAME_LICKS = [("flame_outer", 90.0, 90.0, 0.0, 1.02, 0.17, 0.0, 0.0, 0.40),      # tall central tongue (violet tip)
               ("flame_outer", 0.0, 10.0, 42.0, 0.94, 0.22, 0.55, 0.72, 0.32),    # outward edge petal
               ("flame_outer", 180.0, 10.0, 42.0, 0.88, 0.21, 0.55, 0.72, 0.32),  # inward edge petal
               ("flame_outer", 40.0, 12.0, 38.0, 0.82, 0.20, 0.55, 0.66, 0.32),   # corner petals (violet)
               ("flame_outer", 140.0, 12.0, 38.0, 0.78, 0.20, 0.55, 0.66, 0.32),
               ("flame_mid", 90.0, 16.0, 34.0, 0.86, 0.23, 0.55, 0.62, 0.34)]    # front / back petals (cyan)
# "flame heart": the pale hot body inside the petals (glimpsed between them): a lobed lathe (h, r) in ball radii along the
#   flame axis, its bottom ring tucked on the ball cap above h 0.3 (no brim round the ball)
FLAME_BODY = [(0.20, 0.0), (0.32, 0.97), (0.60, 0.92), (1.00, 0.72), (1.45, 0.46), (1.90, 0.20), (2.15, 0.0)]
FLAME_CORE_H = 9.0                    # body bands below this height take flame_core (all of it), above flame_mid
FLAME_LOBES = (6, 0.18)               # lobes round the heart (even count: the top flame stays X-symmetric), depth
FLAME_BODY_SEG = 12
FLAME_ROOT_IN = 0.70                  # petals root this far out from the ball centre (share of ORB_R: buried in the snow)
FLAME_FLAT = 0.70                     # each petal is flattened across its outward direction (a tongue, not a spike)
FLAME_NS, FLAME_SEG = 7, 6
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
GLOW_CAP = 2.0                        # emissive ceiling: max channel / 255 x emission_scale x emission_strength
# ---- rig
Z_BASE_MID, Z_MID_HEAD = 2.36, 3.55   # skirt / middle ball / head weight splits (the drawn creases)
JOINT_BLEND = (0.10, 0.08)
# ---- clips (PLACEHOLDER: the artist has not given the Mystic Snow Spirit's movement). 24 fps; integer harmonics per
#   loop -> frame N+1 == frame 1. The base never pitches or rolls: the pile stays flat on the floor.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_SWAY = (1.2, 0.8)                # "body sway" deg: middle roll (1 cyc), pitch (1 cyc, offset)
IDLE_HEAD = (1.0, 0.8)                # head roll (1 cyc, lagging), nod (2 cyc)
IDLE_ARM = (1.5, 2.0)                 # arm sway deg: branch, forearm (lagging)
ORB_BOB = (0.06, 0.02)                # "snowball bob" (u): 1 cyc + a 2 cyc overtone, phases 2 pi k / 5
FLAME_FLICK = {"idle": (0.08, (3, 5, 7)), "walk": (0.10, (2, 4, 6))}   # "flame flicker": length +-, harmonics per loop
FLAME_WIDTH_K = 0.5                   # the flame narrows by this share of its stretch
FLAME_WOBBLE = 2.0                    # flame tip wobble deg
WALK_N = 48                           # "glide loop": 2 s
WALK_YAW = 1.5                        # base yaw deg (on the floor)
WALK_LEAN = (4.0, 1.0, 1.5)           # middle: forward lean deg, its 2-cycle bob, side roll deg
WALK_HEAD = -2.0                      # head counter-pitch deg
WALK_ARM = (6.0, 3.0)                 # arms trail back deg + swing
ORB_TRAIL = (0.12, 0.04)              # snowballs trail back (u) + bob
FLAME_TRAIL = 10.0                    # flames stream back deg

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
          "design/reference/mystic_snow_spirit-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; floor z = 0 at the sheet floor line (front y 821); X from the front view "
                   "(axis 248), Y from the side view (axis 1032)", "overrides": OVERRIDES,
          "axes": {"front": 248.0, "side": 1032.0, "back": 671.0, "rule": "re-pinned (intake --axis front=248,back=671): "
                   "front/back = the head + middle-ball axis; side kept at the skirt base centre"}}
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

# =========================================================================== 1. BODY SDF
LUMP_ON = [1.0]                      # 1 while the HIGH grid is filled (gentle snow lumps), 0 for the smooth LOW source
_sk = np.array(SKIRT, float)
_skd = SD.catmull(_sk, 12)
_skd = _skd[np.argsort(_skd[:, 0], kind="stable")]
SKZ = _skd[:, 0]


def skirt_at(z):
    z = np.clip(z, SKZ[0], SKZ[-1])
    return [np.interp(z, SKZ, _skd[:, j]) for j in (1, 2, 3)]          # a, b, cy


def snow_lumps(P):
    """gentle large-scale lumps, even in X (mirror-symmetric); positive = inward"""
    A, k = SNOW_LUMP
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    return LUMP_ON[0] * A * (np.cos(k * x) * np.sin(0.83 * k * y + 0.7) * np.sin(0.91 * k * z + 1.3)
                             + 0.5 * np.cos(1.37 * k * x) * np.cos(1.19 * k * y + 2.1 + 0.6 * k * z))


def sd_head(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R) + snow_lumps(P)


def sd_mid(P):
    return SD.sd_ellipsoid(P, MID_C, MID_R) + snow_lumps(P)


def sd_cone(P):
    z = P[:, 2]
    a, b, cy = skirt_at(z)
    q = np.hypot(P[:, 0] / a, (P[:, 1] - cy) / b)
    d = (q - 1.0) * np.minimum(a, b) * 0.92                           # 0.92 ~ the cone's mean slope cosine
    d = np.maximum(d, np.maximum(SKIRT_BOT - z, z - SKZ[-1]))
    return d + snow_lumps(P)


def ring_lumps(az_list, z0, r0, k_in, seed, flat):
    """mirror pairs on the skirt section at z0 (0 / 180 on the plane): [(centre, radii)]"""
    a, b, cy = (float(v[0]) for v in skirt_at(np.array([z0])))
    out = []
    for i, az in enumerate(az_list):
        h = FP.hash01(i, seed)
        r = r0 * (0.72 + 0.56 * h)                                    # chunky, irregular (never a bead ring)
        zz = z0 + 0.12 * (FP.hash01(i, seed + 1.7) - 0.5)
        kk = k_in * (0.96 + 0.08 * FP.hash01(i, seed + 3.1))
        ar = math.radians(az)
        for sg in ((1.0,) if az in (0.0, 180.0) else (1.0, -1.0)):
            c = np.array([sg * a * kk * math.sin(ar), cy - b * kk * math.cos(ar), zz])
            out.append((c, np.array([r, r, r * flat])))
    return out


RIMS = ring_lumps(RIM_AZ, RIM_Z, RIM_R, RIM_IN, LUMP_SEED, 0.80)
FEET = ring_lumps(FOOT_AZ, FOOT_Z, FOOT_R, FOOT_IN, LUMP_SEED + 11.0, 0.75)


def sd_lump(c, r):
    return lambda P: SD.sd_ellipsoid(P, c, r) + snow_lumps(P)


# grid: X bounds a multiple of the step (nodes mirror about X = 0)
_GX = math.ceil(1.90 / SDF_STEP) * SDF_STEP
GLO = np.array([-_GX, -1.72, -0.04]); GHI = np.array([_GX, 1.72, 4.96])
HC = np.array(HEAD_C); HR = np.array(HEAD_R); MC = np.array(MID_C); MR = np.array(MID_R)


def build_body():
    g = SD.Grid(GLO, GHI, SDF_STEP, SDF_BAND)
    zc = np.arange(SKIRT_BOT - 0.02, SKZ[-1] + 0.02, 0.6)
    for z0 in zc:                                                     # z chunks (memory); min-union is idempotent
        g.apply(sd_cone, np.array([-1.62, -1.45, z0]), np.array([1.62, 1.40, min(z0 + 0.6, SKZ[-1] + 0.02)]), 0.0)
    for c, r in RIMS:
        g.apply(sd_lump(c, r), c - r, c + r, RIM_K)
    for c, r in FEET:
        g.apply(sd_lump(c, r), c - r, c + r, FOOT_K)
    g.apply(sd_mid, MC - MR, MC + MR, WAIST_K)
    g.apply(sd_head, HC - HR, HC + HR, NECK_K)
    g.floor(0.0)
    return g


def sd_body(P):
    """the grid's composition, analytic (region fields only): same op order, LUMP_ON as set by the caller"""
    d = sd_cone(P)
    for c, r in RIMS:
        d = SD.smin(d, sd_lump(c, r)(P), RIM_K)
    for c, r in FEET:
        d = SD.smin(d, sd_lump(c, r)(P), FOOT_K)
    d = SD.smin(d, sd_mid(P), WAIST_K)
    d = SD.smin(d, sd_head(P), NECK_K)
    return np.maximum(d, -P[:, 2])


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


t_sdf = time.time()
LUMP_ON[0] = 1.0
g = build_body()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
grid_n = g.n.tolist()
del g
LUMP_ON[0] = 0.0
g0 = build_body()
SV, SF = SD.polygonise(g0)
SV, SF, _ = keep_islands(SV, [list(map(int, f)) for f in SF], 0.02)
del g0
tmp = new_obj("dec_src", SV, SF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(SF))
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
                 "low_source": "the same SDF without the snow lumps (smooth), %d tris" % len(SF),
                 "rim_lumps": len(RIMS), "foot_lumps": len(FEET), "seconds": round(time.time() - t_sdf, 1)}
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic) -> mirror-halved"
                              % SDF_STEP, "decimated_tris": tri_count_F(LF), "specks": specks, "floor_snap": floor_snap,
                    "mirror_halved": {"seam_verts": SEAM_N, "rule": "X >= 0 half + its mirror (symmetric rest mesh)"},
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_low_over_high": (bb_l / bb_h).round(5).tolist()}
print("SDF", json.dumps(report["sdf"]), json.dumps(report["retopo"]))
assert np.all(bb_l / bb_h > 0.97), report["retopo"]                    # the hull-fold hazard (BUILD_CONTRACT): gated
assert HV[:, 2].min() > -1e-6, "body high dips below the floor"
LUMP_ON[0] = 1.0


# =========================================================================== 2. regions on the body low (iso cuts)
def body_fields(P):
    LUMP_ON[0] = 0.0
    e = 0.01
    gz = (sd_body(P + np.array([0, 0, e])) - sd_body(P - np.array([0, 0, e])))
    gx = (sd_body(P + np.array([e, 0, 0])) - sd_body(P - np.array([e, 0, 0])))
    gy = (sd_body(P + np.array([0, e, 0])) - sd_body(P - np.array([0, e, 0])))
    LUMP_ON[0] = 1.0
    nz = gz / np.maximum(np.sqrt(gx * gx + gy * gy + gz * gz), 1e-9)
    shade = -nz - SHADE_NZ
    return {"shade": shade, "glow": GLOW_Z - P[:, 2]}


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
    CV = np.array([v.co[:] for v in bm.verts])
    CF = [[v.index for v in f.verts] for f in bm.faces]
    FVAL = field_fn(np.array([np.mean([v.co[:] for v in f.verts], 0) for f in bm.faces]))
    for key in {c_[0] for c_ in cuts}:
        cmin = np.array([min(v[LAY[key]] for v in f.verts) for f in bm.faces])
        FVAL[key] = np.where(cmin >= -1e-6, FVAL[key], np.minimum(FVAL[key], cmin))
    FN = np.array([f.normal[:] for f in bm.faces])
    bm.free()
    return CV, CF, FVAL, FN, log


t = time.time()
CV_B, CF_B, FV_B, FN_B, log_b = cut_island(LV, LF, body_fields, [("shade", 0.0, None), ("glow", 0.0, [("shade", ">", -1e-4)])])
CV_B[:, 2] = np.maximum(CV_B[:, 2], 0.0)
reg_b = np.full(len(CF_B), "snow", dtype=object)
reg_b[FV_B["shade"] > 0] = "snow_shade"
reg_b[(FV_B["shade"] > 0) & (FV_B["glow"] > 0)] = "snow_glow"
report["iso_cuts"] = {"body": {"cuts": log_b, "tris_after": len(CF_B), "seconds": round(time.time() - t, 1)}}

# =========================================================================== 3. closed parts
PARTS = []


def mirror_part(p_, name, bone):
    V_ = p_.V.copy(); V_[:, 0] *= -1.0
    q = FP.Part(name, V_, [f_[::-1] for f_ in p_.F], p_.R, bone=bone)
    for k_ in ("flame", "arm", "arm_s", "finger", "dense"):
        if hasattr(p_, k_):
            setattr(q, k_, getattr(p_, k_))
    if hasattr(q, "arm"):
        q.arm = "R"
    return q


body = FP.Part("body", CV_B, CF_B, list(reg_b), bone=None)
body.body = True
PARTS.append(body)


def head_surface_front(x, z):
    """the head ellipsoid's front (-Y) surface point at (x, z) + its outward normal"""
    q = 1.0 - (x / HR[0]) ** 2 - ((z - HC[2]) / HR[2]) ** 2
    y = HC[1] - HR[1] * math.sqrt(max(q, 0.0))
    S = np.array([x, y, z])
    return S, unit((S - HC) / (HR * HR))


def tangent_frame(n):
    e_v = unit(np.array([0.0, 0.0, 1.0]) - n * n[2])
    e_u = np.cross(e_v, n)
    return e_u, e_v


# ---- eyes (the +X = L eye; R mirrors), highlight dots, mouth, nose
FACE = {}
S_e, n_e = head_surface_front(EYE_X, EYE_Z)
eu, ev = tangent_frame(n_e)
V, F, R = FP.conform_dome(HC, HR, S_e, eu, ev, EYE_RINGS, EYE_DEPTH, 24)
PARTS.append(FP.Part("eye.L", V, F, R, bone="head"))
PARTS.append(mirror_part(PARTS[-1], "eye.R", "head"))
_hp = S_e + eu * EYE_HI[0] + ev * EYE_HI[1]
_hS, _hn = FP.ell_point(HC, HR, _hp - HC)
_rho = 0.5 * (EYE_HI[0] / EYE_HALF[0] + EYE_HI[1] / EYE_HALF[1])
_hc = _hS + _hn * (0.004 + EYE_DEPTH * math.sqrt(max(0.0, 1.0 - _rho * _rho)))
V, F, R = FP.ellipsoid(_hc, (EYE_HI[2], EYE_HI[2], EYE_HI[2] * 0.55), 10, 5, "eye_hi", frame=(eu, ev, _hn))
PARTS.append(FP.Part("eye_hi.L", V, F, R, bone="head"))
PARTS.append(mirror_part(PARTS[-1], "eye_hi.R", "head"))
FACE["eye_L"] = {"surface": S_e.round(4).tolist(), "normal": n_e.round(4).tolist(), "half_uv": list(EYE_HALF)}
S_m, n_m = head_surface_front(0.0, MOUTH_Z)
mu, mv = tangent_frame(n_m)
V, F, R = FP.conform_dome(HC, HR, S_m, mu, mv, [(MOUTH_HALF[0] * 0.6, MOUTH_HALF[1] * 0.6, "mouth"),
                                                 (MOUTH_HALF[0], MOUTH_HALF[1], "mouth")], 0.012, 16)
PARTS.append(FP.Part("mouth", V, F, R, bone="head"))
S_n, n_n = head_surface_front(0.0, NOSE_Z)
NOSE_ROOT = S_n - n_n * NOSE_SINK
V, F, R, nose_sp = FP.lick(NOSE_ROOT, NOSE_DIR, (0.0, 0.0, -0.06), NOSE_LEN + NOSE_SINK, NOSE_R, 9, 10, "nose",
                           wide_s=0.30, tip_pow=1.0, root_pow=0.35, flat_axis=(0.0, 0.0, 1.0))
PARTS.append(FP.Part("nose", V, F, R, bone="head"))
FACE["nose_tip"] = nose_sp[-1].round(4).tolist()
FACE["mouth"] = S_m.round(4).tolist()

# ---- twig horns (+X = L)
HORN_PARTS = []
V, F, R, C_h, s_h = FP.branch(HORN, HORN_R, 12, 7, "wood", seed=1.0, wobble=0.05)
HORN_PARTS.append(FP.Part("horn.L", V, F, R, bone="horn_L"))
V, F, R, C_ta, s_ta = FP.branch(TWIG_A, TWIG_A_R, 7, 6, "wood", seed=2.0, wobble=0.04)
HORN_PARTS.append(FP.Part("horn_twig_a.L", V, F, R, bone="horn_L"))
V, F, R, C_tb, s_tb = FP.branch(TWIG_B, TWIG_B_R, 6, 6, "wood", seed=3.0, wobble=0.04)
HORN_PARTS.append(FP.Part("horn_twig_b.L", V, F, R, bone="horn_L"))
_hsp = {"shaft": (C_h, s_h, HORN_R), "twig_a": (C_ta, s_ta, TWIG_A_R)}
for j, (which, s_at, ln, ht) in enumerate(HORN_SNOW):
    C_, s_, r_ = _hsp[which]
    rb = float(np.interp(s_at, np.linspace(0, 1, len(r_)), r_))
    for k, (V, F, R) in enumerate(FP.snow_clump(C_, s_, s_at * s_[-1], rb, ln, ht, seed=10.0 + j, n_seg=8, n_ring=4)):
        HORN_PARTS.append(FP.Part("horn_snow.%d%d.L" % (j, k), V, F, R, bone="horn_L"))
for p in HORN_PARTS:
    PARTS.append(p)
    PARTS.append(mirror_part(p, p.name[:-2] + ".R", "horn_R"))

# ---- arms (+X = L)
ARM_PARTS = []
V, F, R, C_a, s_a = FP.branch(ARM, ARM_R, 16, 8, "wood", knots=ARM_KNOT, seed=4.0, wobble=0.06)
p = FP.Part("arm.L", V, F, R); p.arm = "L"; p.arm_s = None
ARM_PARTS.append(p)
ARM_SPINE, ARM_ARC = C_a, s_a
for j, (pts, rr) in enumerate(zip(FINGERS, FINGER_R)):
    V, F, R, C_f, s_f = FP.branch(pts, rr, 7, 6, "wood", seed=5.0 + j, wobble=0.05)
    p = FP.Part("finger.%d.L" % j, V, F, R); p.arm = "L"; p.finger = True
    ARM_PARTS.append(p)
    for k, (V, F, R) in enumerate(FP.snow_clump(C_f, s_f, FINGER_SNOW[0] * s_f[-1], rr[-1] * 1.1, FINGER_SNOW[1],
                                                FINGER_SNOW[2], seed=30.0 + j, n_seg=8, n_ring=4)):
        q = FP.Part("finger_snow.%d%d.L" % (j, k), V, F, R); q.arm = "L"; q.finger = True
        ARM_PARTS.append(q)
for j, (s_at, ln, ht) in enumerate(ARM_SNOW):
    rb = float(np.interp(s_at, np.linspace(0, 1, len(ARM_R)), ARM_R))
    for k, (V, F, R) in enumerate(FP.snow_clump(C_a, s_a, s_at * s_a[-1], rb, ln, ht, seed=20.0 + j, n_seg=8, n_ring=4)):
        q = FP.Part("arm_snow.%d%d.L" % (j, k), V, F, R); q.arm = "L"
        ARM_PARTS.append(q)
for p in ARM_PARTS:
    PARTS.append(p)
    PARTS.append(mirror_part(p, p.name[:-2] + ".R", None))

# ---- floating snowballs + flames
ORB_NAMES = {("top", "C"): "001", ("up", "L"): "002", ("up", "R"): "003", ("low", "L"): "004", ("low", "R"): "005"}
ORB_INFO = {}


def flame_frame(kind):
    lo_, lb_ = (math.radians(v) for v in ORB_LEAN[kind])
    a = unit([math.sin(lo_), math.sin(lb_), 1.0])
    e1 = unit(np.array([1.0, 0.0, 0.0]) - a * a[0])                  # azimuth 0 = outward (+X); 90 = back
    e2 = np.cross(a, e1)
    return a, e1, e2


def build_flame(kind, c):
    """the licks of one snowball's flame -> [(V, F, R, az, spine, mirror_me)]. The TOP flame keeps the +X half
    (|az| <= 90; the on-plane azimuths +-90 and the el-90 tongues are X-symmetric by construction) and flags the
    |az| < 90 licks for mirroring, so it is exactly X-symmetric."""
    a, e1, e2 = flame_frame(kind)
    ks = ORB_FLAME_K[kind]
    out = []
    for i, (reg, az, el, tilt, L_, r_, curl, pull, ws) in enumerate(FLAME_LICKS):
        twins = (1.0,) if (az in (0.0, 180.0) or el >= 80.0) else (1.0, -1.0)
        if i == 0:                                                 # the engulfing body first (one per flame)
            prof = [(h * ORB_R * (1.0 if h < 0.4 else ks), r_b * ORB_R * (1.0 if h < 0.4 else ks)) for h, r_b in FLAME_BODY]
            regs = ["flame_core" if 0.5 * (h_a + h_b) < FLAME_CORE_H else "flame_mid"
                    for (h_a, _), (h_b, _) in zip(FLAME_BODY[:-1], FLAME_BODY[1:])]
            Vb, Fb, Rb = FP.flame_body(c, a, e1, prof, regs, FLAME_BODY_SEG, FLAME_LOBES[0], FLAME_LOBES[1])
            out.append((Vb, Fb, Rb, 0.0, np.array([c, c + a * FLAME_BODY[-1][0] * ORB_R * ks]), False))
        for sg in twins:
            azs = sg * az
            if kind == "top" and abs(azs) > 90.0 and el < 80.0:
                continue                                           # produced by mirroring the +X half
            ar, er, tr = math.radians(azs), math.radians(el), math.radians(tilt)
            hdir = math.cos(ar) * e1 + math.sin(ar) * e2
            root = c + ORB_R * FLAME_ROOT_IN * (math.cos(er) * hdir + math.sin(er) * a)
            d = math.cos(tr) * a + math.sin(tr) * hdir
            bend = curl * (a - d) - pull * math.sin(tr) * hdir
            V, F, R, sp = FP.lick(root, d, bend, L_ * ks, r_ * ks, FLAME_NS, FLAME_SEG, reg, wide_s=ws, tip_pow=1.4,
                                  flat=FLAME_FLAT if el < 80.0 else 1.0, flat_axis=hdir if el < 80.0 else e2)
            out.append((V, F, R, azs, sp, kind == "top" and el < 80.0 and abs(azs) < 90.0))
    return out


for kind, c0 in ORBS.items():
    c0 = np.array(c0, float)
    sides = (("C", 1.0),) if kind == "top" else (("L", 1.0),)
    for side, _ in sides:
        nm = ORB_NAMES[(kind, side)]
        V, F, R = FP.ellipsoid(c0, (ORB_R, ORB_R, ORB_R), 22, 11, "orb")
        po = FP.Part("orb.%s" % nm, V, F, R, bone="orb.%s" % nm); po.dense = c0
        PARTS.append(po)
        fl = build_flame(kind, c0)
        fparts = []
        for i, (V, F, R, az, sp, mir) in enumerate(fl):
            pf = FP.Part("flame.%s.%02d" % (nm, i), V, F, R, bone="flame.%s" % nm); pf.flame = True
            fparts.append(pf)
            if mir:
                fparts.append(mirror_part(pf, "flame.%s.%02dm" % (nm, i), "flame.%s" % nm))
        PARTS.extend(fparts)
        a_, _, _ = flame_frame(kind)
        tip = max((x_[4][-1] for x_ in fl), key=lambda q: float((q - c0) @ a_))
        ORB_INFO[nm] = {"kind": kind, "side": side, "centre": c0, "axis": a_, "tip": tip, "licks": len(fparts)}
        if kind != "top":
            nm2 = ORB_NAMES[(kind, "R")]
            PARTS.append(mirror_part(po, "orb.%s" % nm2, "orb.%s" % nm2))
            for pf in fparts:
                PARTS.append(mirror_part(pf, pf.name.replace(".%s." % nm, ".%s." % nm2), "flame.%s" % nm2))
            ORB_INFO[nm2] = {"kind": kind, "side": "R", "centre": c0 * MIRX, "axis": a_ * MIRX, "tip": tip * MIRX,
                             "licks": len(fparts)}

# =========================================================================== 4. assemble + centre
ISL = [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
assert lo0[2] > -1e-6, "something dips below the floor: min z %.6f" % lo0[2]
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
assert VALL[:, 2].min() > -1e-9 and abs(VALL[RANGE["body"][0]:RANGE["body"][1], 2].min()) < 1e-9, "the pile must sit on the floor"
# mirror test (house rule): every vertex mirrored X -> -X, distance to the nearest point of the mesh
_bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mir_d = np.array([_bvh_all.find_nearest(Vector((-p_[0], p_[1], p_[2])))[3] for p_ in VALL])
report["mirror_test"] = {"rule": "every rest vertex mirrored across X = 0 -> distance to the nearest point of the mesh",
                         "max_abs_asymmetry": round(float(_mir_d.max()), 6), "p99": round(float(np.percentile(_mir_d, 99)), 6),
                         "mean": round(float(_mir_d.mean()), 7), "shift_x": round(float(SHIFT[0]), 7),
                         "bbox_x": [round(float(VALL[:, 0].min()), 5), round(float(VALL[:, 0].max()), 5)]}
print("MIRROR", json.dumps(report["mirror_test"]))
REG = ["snow", "snow_shade", "snow_glow", "nose", "eye_rim", "eye_iris", "pupil", "eye_hi", "mouth", "wood", "orb",
       "flame_outer", "flame_mid", "flame_core"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
FLAME_REG = {"flame_outer", "flame_mid", "flame_core"}
assert all((RNAME[i] in FLAME_REG) == (MATI[i] == 1) for i in range(len(RNAME))), "flame region off the flame material"


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    FCc[:, 0] = np.abs(FCc[:, 0])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.97 + 0.06 * j                                   # |X|-based: the per-face shade jitter is mirror-symmetric too


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


GLOW_REGIONS = {"snow_glow", "flame_outer", "flame_mid", "flame_core"}


def glow_check(pal):
    """the sheet draws glow on the flames + a faint blue under-glow at the pile foot: tiers base glow < outer < mid < core;
    peak under the cap"""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    peak = {n: max(pal["regions"][n]["emission"]) / 255.0 * tier[n] * pal["material"].get("emission_strength", 1.0)
            for n in tier}
    ok_set = set(tier) == GLOW_REGIONS
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "peak_x_strength": {k: round(v, 4) for k, v in peak.items()},
            "cap": GLOW_CAP, "only_drawn_glow": ok_set,
            "grade_pass": (tier["snow_glow"] < tier["flame_outer"] < tier["flame_mid"] < tier["flame_core"]) if ok_set else False,
            "faint_base_glow": (peak.get("snow_glow", 9) <= 0.5),
            "cap_pass": all(v <= GLOW_CAP for v in peak.values())}


MAT_BODY = make_mat(UNIT + "_body")
MAT_FLAME = make_mat(UNIT + "_flame")
pal_default = PAL.load(UNIT, "default")
report["glow"] = {"default": glow_check(pal_default)}
_gc = report["glow"]["default"]
assert _gc["only_drawn_glow"] and _gc["grade_pass"] and _gc["cap_pass"] and _gc["faint_base_glow"], _gc

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

# facing landmark: the head centre -> the face between the eyes (straight -Y)
anchor = np.array([0.0, HEAD_C[1], EYE_Z]) - SHIFT
landmark = head_surface_front(0.0, EYE_Z)[0] - SHIFT
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
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The SDF snowman shell (head, middle ball, skirt + "
                            "lumpy rim, iso-cut) carries the largest share; five snowballs with 10-lick flames, two "
                            "branch arms with fingers, two forked horns, the face parts the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]), "parts": len(open_e),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every part is closed: the SDF shell (mirror-welded), branches, snow clumps, domes, "
                                "ellipsoids, flame licks"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_height_units": {"front": 7.04, "back": 7.08, "side": 7.03},
                     "sheet_width_units_front": 4.67,
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
low["conquest_source"] = "from scratch: design/reference/mystic_snow_spirit-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = "none: every material opaque (the flames are opaque emissive meshes on their own material)"
_face = np.array([[-HR[0] - 0.15, HC[1] - HR[1] - 0.45, HC[2] - HR[2] - 0.1], [HR[0] + 0.15, HC[1] + 0.2, HC[2] + HR[2] + 0.15]])
_hornv = np.vstack([p.V for p in PARTS if p.name.startswith("horn") and p.name.endswith(".L")])
_o2 = np.vstack([p.V for p in PARTS if p.name in ("orb.002",) or p.name.startswith("flame.002.")])
_armv = np.vstack([p.V for p in PARTS if getattr(p, "arm", None) == "L"])
_lowarm = np.array([[_armv[:, 0].min() - 0.6, _armv[:, 1].min() - 0.5, 0.0], [_armv[:, 0].max() + 0.1, _armv[:, 1].max() + 0.1, 2.9]])
low["conquest_focus"] = json.dumps({
    "face": [(_face[0] - SHIFT).tolist(), (_face[1] - SHIFT).tolist()],
    "horn": [(_hornv.min(0) - SHIFT - 0.08).tolist(), (_hornv.max(0) - SHIFT + 0.08).tolist()],
    "snowball": [(_o2.min(0) - SHIFT - 0.06).tolist(), (_o2.max(0) - SHIFT + 0.06).tolist()],
    "lower_body": [(_lowarm[0] - SHIFT).tolist(), (_lowarm[1] - SHIFT).tolist()],
    "views": {"face": [0.0, 4.0], "horn": [20.0, 10.0], "snowball": [20.0, 6.0], "lower_body": [35.0, 8.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "mirror_test")}, default=str))
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
    if getattr(p, "body", False) or getattr(p, "flame", False):
        continue                                     # the SDF low is replaced by its high; the flames never occlude
    if getattr(p, "dense", None) is not None:        # snowballs: a dense sphere in the high (the normal map reads smooth)
        Vd, Fd, _ = FP.ellipsoid(p.dense * (MIRX if p.V[:, 0].mean() < -1e-6 else 1.0), (ORB_R, ORB_R, ORB_R), 64, 32, "orb")
        add_high(Vd, Fd)
        continue
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
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the body's sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "the body SDF high (with the gentle snow lumps) + every closed part except the flames",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "normal_dev_p99": round(float(np.percentile(devn[cov_n & alltex_n], 99)), 4),
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
                     "provenance": "pixel-sampled from the sheet's palette chips (intake swatches) + drawn-surface samples "
                                   "(see the palette notes)"}
report["face"] = FACE
report["orbs"] = {k: {kk: (vv.round(4).tolist() if hasattr(vv, "round") else vv) for kk, vv in v.items()} for k, v in ORB_INFO.items()}
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


def skirt_c(z):
    return float(skirt_at(np.array([z]))[2][0])


ARM_ELBOW_S = float(FP.arclen(np.array(ARM[:4]))[-1] / FP.arclen(np.array(ARM))[-1])   # the elbow = the 4th control point
_e_i = int(np.argmin(np.abs(ARM_ARC / ARM_ARC[-1] - ARM_ELBOW_S)))
ELBOW = ARM_SPINE[_e_i]
BONES = [("base", P_((0.0, skirt_c(0.3), 0.0)), P_((0.0, skirt_c(2.2), Z_BASE_MID)), "root"),
         ("middle", P_((0.0, skirt_c(2.2), Z_BASE_MID)), P_((0.0, -0.5, Z_MID_HEAD)), "base"),
         ("head", P_((0.0, -0.5, Z_MID_HEAD)), P_((0.0, HEAD_C[1], HEAD_C[2] + HEAD_R[2])), "middle")]
for side, m_ in (("L", np.ones(3)), ("R", MIRX)):
    BONES.append(("horn_%s" % side, P_(np.array(HORN[1]) * m_), P_(np.array(HORN[-1]) * m_), "head"))
    BONES.append(("arm_%s.0" % side, P_(ARM_SPINE[0] * m_), P_(ELBOW * m_), "middle"))
    BONES.append(("arm_%s.1" % side, P_(ELBOW * m_), P_(ARM_SPINE[-1] * m_), "arm_%s.0" % side))
for nm in sorted(ORB_INFO):
    oi = ORB_INFO[nm]
    BONES.append(("orb.%s" % nm, P_(oi["centre"]), P_(oi["centre"] + oi["axis"] * 0.45), "base"))
    BONES.append(("flame.%s" % nm, P_(oi["centre"] + oi["axis"] * ORB_R * 0.3), P_(oi["tip"]), "orb.%s" % nm))
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
    e.use_connect = nm in ("middle", "head") or nm.endswith(".1")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences): the shell along z (two crease blends); face parts on head; horns rigid on horn_*;
#      arms by the arc parameter of the nearest point of the main branch spine (the buried root on middle, blend into arm.0,
#      the elbow blend into arm.1; fingers + their snow fully arm.1); snowballs on orb.*, flames on flame.*
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["body"]
zz = VALL[a_:b_, 2] + SHIFT[2]
s0 = smoothstep(Z_BASE_MID - JOINT_BLEND[0], Z_BASE_MID + JOINT_BLEND[0], zz)
s1 = smoothstep(Z_MID_HEAD - JOINT_BLEND[1], Z_MID_HEAD + JOINT_BLEND[1], zz)
Wt[a_:b_, J["base"]] = 1.0 - s0
Wt[a_:b_, J["middle"]] = s0 - s1
Wt[a_:b_, J["head"]] = s1
_La = ARM_ARC[-1]
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "body", False):
        continue
    side = getattr(p, "arm", None)
    if side:
        Vw = (VALL[a_:b_] + SHIFT) * (MIRX if side == "R" else 1.0)
        if getattr(p, "finger", False):
            s_v = np.full(len(Vw), _La)
        else:
            dd = np.linalg.norm(Vw[:, None, :] - ARM_SPINE[None, :, :], axis=2)
            s_v = ARM_ARC[np.argmin(dd, 1)]
        w_root = smoothstep(0.03 * _La, 0.14 * _La, s_v)
        w_elb = smoothstep(ARM_ELBOW_S * _La - 0.07 * _La, ARM_ELBOW_S * _La + 0.07 * _La, s_v)
        Wt[a_:b_, J["middle"]] = 1.0 - w_root
        Wt[a_:b_, J["arm_%s.0" % side]] = w_root - w_elb
        Wt[a_:b_, J["arm_%s.1" % side]] = w_elb
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
                  "rule": "shell: smoothstep blends at z %.2f / %.2f (half-widths %s); eyes, mouth, nose 1.0 head; horns "
                          "1.0 horn_*; arms by spine arc (middle -> arm.0 over 3-14%%, arm.0 -> arm.1 at the elbow +- 7%%; "
                          "fingers 1.0 arm.1); snowballs 1.0 orb.*, flames 1.0 flame.*" % (Z_BASE_MID, Z_MID_HEAD, list(JOINT_BLEND))}
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
ORB_ORDER = sorted(ORB_INFO)


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        P["middle"] = (None, [("Y", IDLE_SWAY[0] * S(TAU * u)), ("X", IDLE_SWAY[1] * S(TAU * u + 1.4))], None)
        P["head"] = (None, [("Y", IDLE_HEAD[0] * S(TAU * u - 0.8)), ("X", IDLE_HEAD[1] * S(TAU * 2 * u - 1.2))], None)
        for side, ph in (("L", 0.0), ("R", 1.3)):
            P["arm_%s.0" % side] = (None, [("X", IDLE_ARM[0] * S(TAU * u + 0.5 + ph))], None)
            P["arm_%s.1" % side] = (None, [("X", IDLE_ARM[1] * S(TAU * u - 0.3 + ph))], None)
        for k, nm in enumerate(ORB_ORDER):
            ph = TAU * k / len(ORB_ORDER)
            dz = ORB_BOB[0] * S(TAU * u + ph) + ORB_BOB[1] * S(TAU * 2 * u + 1.7 * ph)
            P["orb.%s" % nm] = ((0.0, 0.0, dz), [], None)
    else:
        P["base"] = (None, [("Z", WALK_YAW * S(TAU * u))], None)
        P["middle"] = (None, [("X", WALK_LEAN[0] + WALK_LEAN[1] * S(TAU * 2 * u - 0.5)), ("Y", WALK_LEAN[2] * S(TAU * u + 0.4))], None)
        P["head"] = (None, [("X", WALK_HEAD + 0.8 * S(TAU * 2 * u - 1.6))], None)
        for side, sg in (("L", 1.0), ("R", -1.0)):
            P["arm_%s.0" % side] = (None, [("X", WALK_ARM[0] + sg * WALK_ARM[1] * S(TAU * u))], None)
            P["arm_%s.1" % side] = (None, [("X", 2.0 + sg * WALK_ARM[1] * S(TAU * u - 0.6))], None)
        for k, nm in enumerate(ORB_ORDER):
            ph = TAU * k / len(ORB_ORDER)
            P["orb.%s" % nm] = ((0.0, ORB_TRAIL[0], ORB_TRAIL[1] * S(TAU * 2 * u + ph)), [], None)
    amp, (h1, h2, h3) = FLAME_FLICK[clip]
    for k, nm in enumerate(ORB_ORDER):
        ph = 1.3 * k
        flick = 0.5 * S(TAU * h1 * u + ph) + 0.3 * S(TAU * h2 * u + 1.1 + ph) + 0.2 * S(TAU * h3 * u + 2.0 + ph)
        Lf = 1.0 + amp * flick
        Wf = 1.0 - FLAME_WIDTH_K * (Lf - 1.0)
        rots = [("X", (-FLAME_TRAIL if clip == "walk" else 0.0) + FLAME_WOBBLE * S(TAU * (h1 + 1) * u + 0.4 + ph)),
                ("Y", FLAME_WOBBLE * S(TAU * (h2 + 1) * u + 1.7 + ph))]
        P["flame.%s" % nm] = (None, rots, (Wf, Lf, Wf))
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
base_v = BRV[VALL[BRV, 2] < 0.02]
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
                     "base_minz": float(C[base_v, 2].min()), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "base_on_floor": {"min_z": round(float(g_("base_minz").min()), 6),
                                      "z_spread_max": round(float(g_("base_z_range").max()), 6), "base_verts": int(len(base_v))}}
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
rep["orb_bones"] = {("orb." + k): "%s %s" % (v["kind"], v["side"]) for k, v in ORB_INFO.items()}
rig["conquest_rig"] = ("mystic_snow_spirit v1: root > base > middle > head > horn_L / horn_R; middle > arm_{L,R}.0 > "
                       "arm_{L,R}.1; base > orb.001-.005 (top, up L, up R, low L, low R) > flame.001-.005")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("idle = PLACEHOLDER (the artist has not given the Mystic Snow Spirit's movement; open "
                               "question): 4 s body sway + snowballs bobbing on offset phases + flame flicker (scale "
                               "pulse); walk = PLACEHOLDER 2 s in-place sliding glide (the base flat on the floor, yaw "
                               "%.1f deg; the body leans in, the snowballs trail %.2f back, the flames stream back)"
                               % (WALK_YAW, ORB_TRAIL[0]))
low["conquest_locomotion"] = "sliding-glide placeholder: rest pose base z 0 per contract; the base never leaves the floor"
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
              "structure": "armature identity + skinned 'mystic_snow_spirit' (opaque body primitive + opaque emissive "
                           "flame primitive); natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
