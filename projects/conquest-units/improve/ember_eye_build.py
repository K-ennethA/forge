"""Ember Eye -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/ember_eye_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x2: Eye Totem, Ember Eye" (section EMBER EYE) + the sheet
design/reference/ember_eye-character-sheet.png (intake: design/reference/ember_eye/intake.json). Contract:
improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. FRONT + BACK are true orthos; the SIDE is drawn at a 3/4 slant:
  FRONT view (the unit's left on image right):   X = (x_px - 236.5) / 100 ; z = (811 - y_px) / 100
  BACK view (mirrored into world X):             X = -(x_px - 670) / 100  ; z = (811 - y_px) / 100
  SIDE view (3/4; front on image left):          image-right = 0.36 X + 0.934 Y (the slant: the eye disc is drawn 0.27 wide
                                                 vs the front's 0.76 -> cos 0.36, 69 deg off the front); z = (810 - y_px) / 100
Floor z = 0 at the STEM TIP (it floats: the sheet's shadow sits well below). X +ve = the unit's LEFT (L), front = -Y.
Edges are 10-row scans of the view masks (|rgb - background 43,43,42| > 26). Depth (Y) is read off the 3/4 side view by
removing the X share (0.36 X) of each measured side-view centre / width -- upper bounds where noted.

THE TWO ORTHOS DISAGREE on the upper flame: the BACK view is the front drawing NOT mirrored (its S-wave bulges to
image-left at z 5.1 in both views, i.e. to opposite world sides) and is drawn taller (tip y 68 = z 7.43 vs the front's
y 114 = z 6.97; the 3/4 side also reaches y 68). The build follows the FRONT ortho for the flame's X wave, widths and
height (FLAME_TIP_Z = 6.97, the face view: the eye, the side flames and the ribbon agree with it); the back's taller tip is
one knob away (--set FLAME_TIP_Z=7.43 stretches the flame above the orb top; 7.20 = the two-ortho mean). Reported.

Pipeline:
  1. BODY = ONE SDF: the ORB (a rounded PUCK: the 3/4 side view shows a thick coin, not a ball -- see ORB_D) smooth-union
     the STEM (an elliptic lathe of the measured profile, leaning back as the side view draws it), minus the shallow BACK
     INSET disc. Marching tets = the HIGH (bake source); collapse decimation = the LOW shell; the colour cut curves are
     densified locally and pulled back onto the high.
  2. REGIONS on the shell, every boundary iso-cut: the EYE (white disc, red flame-shaped teardrop pupil, its lighter inner
     flame; front-projected on the flat front face), the maroon RIM band (the puck's rounded edge, front + back), the back
     inset disc, the stem.
  3. PARTS (ember_eye_parts.py, closed pillows): the UPPER FLAME (a thick wavy tongue: the front S-wave in X, the side's
     wave in Y, elliptic section), the two SIDE FLAMES (detached, swept back), the three RIBBON strips (thin sheets with
     tattered lower edges). Highlight streaks iso-cut into the flames (front-projected, both faces).
  4. palette (palettes.py regions -> Col / Glow, NO glow: the sheet draws none beyond the bright colours), Smart UV, bake
     normal + AO from the SDF high + the parts.
  5. RIG: root > body (orb) > flame_top.001 > .002 > .003; body > flame_L, flame_R; body > ribbon.001 (centre), ribbon.002
     (L), ribbon.003 (R); body > stem. Clips PLACEHOLDER (movement not given): idle = hover bob + flame sway; walk = an
     in-place hover-drift lean with the flames, ribbons and stem trailing. glb export.
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
import ember_eye_parts as FP    # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "ember_eye"
CHAR_ID = "ember_eye"                 # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.14                       # BAND >= max smooth-union k (STEM_K 0.06) + 2 steps (contract hazard note)
BODY_TRIS = 3200                      # "body mesh detail": the shell's decimation target (before densify + cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- ORB ("dark-red orb with a deep-maroon rim"): FRONT widest |X| 0.685 at z 2.81-2.91 (y 530-520), chord at y 460
#   (z 3.51) [-0.23, 0.23] -> centre z 3.51 - sqrt(0.69^2 - 0.23^2) = 2.86; the back agrees (widest 0.69 at z 2.86).
#   DEPTH from the 3/4 SIDE view: the eye disc centre sits 0.28 left of the orb's centre (side x 992.5 vs 1020.5) and the
#   chords at z 2.4 / 3.4 are 0.81-0.83 wide; a ball of radius 0.69 would draw them 1.3 wide, a coin (cylinder) of half-depth
#   D draws them 2 (0.52 * 0.36 + D * 0.934) -> D 0.23, and the eye offset gives D 0.30 -> 0.26 (a thick puck).
ORB_C = (0.0, 0.0, 2.86)              # "orb centre"
ORB_R = 0.69                          # "orb radius"
ORB_D = 0.26                          # "orb thickness" (half; the side view's coin)
ORB_RR = 0.12                         # "orb edge roundness" (the side view's rounded coin edge)
RIM_W = 0.035                         # "rim band width" as seen from the front / back: the drawn dark rim outline r 67-70 px
                                      #   of the 69 px orb (FRONT ring median 70,8,7 there vs the face's 117,32,31)
BACK_DISC_R = 0.41                    # "back inset disc": BACK view darker disc r < 41 px (median 99,28,27 vs 116,32,31)
BACK_DISC_CUT = 0.425                 # ... its colour edge (just outside the inset's wall: the drawn darker groove r 38-43)
BACK_RECESS = 0.04                    # "back inset depth" (a shallow dish; the sheet only shades it)
BACK_K = 0.02                         # ... its edge rounding (smooth subtraction k)
# ---- STEM ("tapered dark-red lower stem / tail with a pointed tip"): rows (z, right X edge, left X edge, half-depth).
#   X edges = FRONT and mirrored BACK averaged (front y 700-780 [-0.17, 0.18], back [-0.18, 0.18]; the chisel tip z 0.31
#   -> 0: front 0.21 [-0.14, 0.14] / 0.11 [-0.07, 0.10], back mirrored [-0.13, 0.14] / [-0.08, 0.09]); half-depth: the 3/4
#   SIDE's stem is 0.37-0.39 wide -> sqrt(0.19^2 - (0.175 * 0.36)^2) / 0.934 = 0.19 (upper bound) -> 0.16.
STEM_ROWS = [(0.00, 0.015, 0.015, 0.0), (0.11, -0.075, 0.095, 0.075), (0.21, -0.135, 0.135, 0.125),
             (0.31, -0.175, 0.18, 0.16), (2.30, -0.175, 0.18, 0.16)]
STEM_TOP_Z = 1.30                     # the stem leaves the ribbon skirt here (front: the strips' hem y ~680); vertical above
STEM_LEAN_Y = 0.29                    # "stem lean": the 3/4 SIDE draws the visible stem slanting back: centre x 1037.5 at
                                      #   y 710 (z 1.0) -> 1052 at y 780 (z 0.3): 0.207 / u projected / 0.934 = 0.22 / u in Y
                                      #   (12.5 deg) -> 0.29 behind (+Y) at the tip; the orthos draw it vertical in X
STEM_K = 0.06                         # "stem / orb blend"
# ---- EYE (FRONT view, PIL): white disc filled bbox x 199-275 / y 489-566 -> centre (0.006, 2.832), half 0.38 x 0.385.
#   Red flame pupil (R > 170, G < 110 inside the disc): x 218-255 / y 499-554 -> X -0.185..0.185, z 2.57..3.12, widest
#   at z 2.78-2.84; the top narrows to a point (z 3.11: x 233-240). Built as a teardrop: a circle r 0.19 at z 2.765 + the
#   tangents to the tip z 3.125 (check: half-width at z 3.02 0.067 vs drawn 0.07). Lighter inner flame (R > 200, G > 120,
#   median 245,184,182): x 231-244 / y 515-542 -> X -0.055..0.075, z 2.69..2.96 -> circle r 0.065 at (0.005, 2.755) + tip
#   z 2.965 (the EYE DETAIL panel: "red flame-shaped pupil", the lighter inner flame).
EYE_C, EYE_R = (0.005, 2.833), 0.383                   # "eye size"
PUPIL_TD = (0.0, 2.765, 0.19, 3.125)                   # "flame pupil": (centre X, round-bottom centre z, radius, tip z)
CORE_TD = (0.005, 2.755, 0.065, 2.965)                 # "inner flame" (the same teardrop, smaller)
EYE_DENSE = ((0.02, 0), (0.02, 0), (0.02, 0), (0.02, 0), (0.02, 0))   # extra uniform 1-to-4 densify (zone u, rounds): eye / pupil / core /
                                                                        #   back disc / rim -- all 0: the longest-edge eye pass (EYE_FACE_EDGE) covers it
# ---- UPPER FLAME ("large, tall, wavy flame-like petal ... tapered tip"): FRONT rows (z, left X, right X), 10-row scan;
#   the tip y 114 (z 6.97) at x 207 (X -0.29). Rows below z 3.6 continue the neck straight down into the orb (base z 3.30).
FLAME_ROWS = [(3.30, -0.20, 0.17), (3.61, -0.20, 0.17), (3.71, -0.20, 0.17), (3.81, -0.26, 0.20), (3.91, -0.32, 0.23),
              (4.01, -0.34, 0.29), (4.11, -0.33, 0.34), (4.21, -0.33, 0.39), (4.31, -0.34, 0.41), (4.41, -0.38, 0.43),
              (4.51, -0.42, 0.43), (4.61, -0.49, 0.42), (4.71, -0.56, 0.41), (4.81, -0.60, 0.36), (4.91, -0.65, 0.32),
              (5.01, -0.67, 0.27), (5.11, -0.68, 0.21), (5.21, -0.67, 0.20), (5.31, -0.64, 0.17), (5.41, -0.59, 0.17),
              (5.51, -0.56, 0.18), (5.61, -0.51, 0.20), (5.71, -0.47, 0.20), (5.81, -0.41, 0.18), (5.91, -0.40, 0.20),
              (6.01, -0.41, 0.18), (6.11, -0.40, 0.15), (6.21, -0.40, 0.12), (6.31, -0.41, 0.09), (6.41, -0.43, 0.04),
              (6.51, -0.43, -0.04), (6.61, -0.41, -0.12), (6.71, -0.41, -0.15), (6.81, -0.41, -0.18), (6.91, -0.36, -0.21),
              (6.97, -0.29, -0.29)]
FLAME_ORB_TOP = 3.55                  # where the flame leaves the orb (front: the orb's top y 456)
FLAME_TIP_Z = 6.97                    # "upper flame height": the FRONT tip (7.43 = the back / 3/4 side tip; 7.20 = the mean)
# FLAME DEPTH WAVE (Y): the 3/4 SIDE centre (x) per row minus the X share, at the same height fraction s = (z - 3.55) /
#   (tip - 3.55) (side tip z 7.42): side centre - 1020.5 (the orb's side centre) = 0.36 X + 0.934 Y -> Y(s):
FLAME_Y = [(0.0, 0.055), (0.04, 0.075), (0.09, 0.105), (0.14, 0.17), (0.19, 0.20), (0.245, 0.19), (0.30, 0.13),
           (0.35, 0.056), (0.40, -0.01), (0.45, -0.056), (0.48, -0.07), (0.53, -0.048), (0.58, 0.005), (0.63, 0.094),
           (0.685, 0.152), (0.735, 0.186), (0.79, 0.164), (0.84, 0.125), (0.89, 0.154), (0.94, 0.24), (1.0, 0.35)]
FLAME_DEPTH = 0.75                    # "upper flame thickness": half-depth / half-width of the elliptic section (the 3/4 side's
                                      #   widths give 0.83-0.98, an upper bound: its slant adds the Y-wave's slope)
PROFILE_SMOOTH = 2                    # "outline smoothing": 1-2-1 passes over the measured flame rows (scan noise ~1 px)
FLAME_NS, FLAME_NT = 56, 16           # "upper flame detail": rows up the flame, columns across (cosine-spaced)
FLAME_HI_ROWS = [(4.41, -0.235, -0.235), (4.46, -0.24, -0.23), (4.54, -0.29, -0.23), (4.62, -0.34, -0.24),
                 (4.70, -0.40, -0.24), (4.78, -0.45, -0.24), (4.86, -0.48, -0.23), (4.94, -0.52, -0.27),
                 (5.02, -0.54, -0.32), (5.10, -0.55, -0.34), (5.18, -0.54, -0.35), (5.26, -0.52, -0.35),
                 (5.34, -0.48, -0.34), (5.42, -0.45, -0.33), (5.50, -0.41, -0.32), (5.58, -0.35, -0.30),
                 (5.64, -0.33, -0.33)]   # "flame highlight streak": FRONT R > 200, 50 < G < 140 rows (median 248,77,79)
# ---- SIDE FLAMES ("two smaller curved side flame-petals"): FRONT rows (z, inner |X|, outer |X|), the two sides averaged
#   (image-left x 30-160 / image-right x 300-420: they agree within 0.05-0.13; built symmetric). Bottom tip z 2.33 |X| 0.80,
#   top tip y 406 (z 4.05) |X| 1.43. They float DETACHED from the orb (a 0.1-0.4 gap in both orthos).
SIDE_ROWS = [(2.33, 0.80, 0.80), (2.41, 0.79, 0.92), (2.51, 0.80, 1.03), (2.61, 0.825, 1.135), (2.71, 0.84, 1.275),
             (2.81, 0.835, 1.48), (2.91, 0.83, 1.615), (3.01, 0.915, 1.70), (3.11, 1.015, 1.77), (3.21, 1.095, 1.785),
             (3.31, 1.155, 1.775), (3.41, 1.185, 1.74), (3.51, 1.23, 1.71), (3.61, 1.28, 1.68), (3.71, 1.33, 1.63),
             (3.81, 1.335, 1.565), (3.91, 1.365, 1.51), (4.05, 1.43, 1.43)]
SIDE_SWEEP = (0.43, 0.80)             # "side flame sweep": Y = 0.43 (|X| - 0.80): the 3/4 SIDE draws the visible flame's
                                      #   inner edge at side x 1072 (z 3.4) and its outer edge at 1118 (z 3.2) -> Y 0.17 / 0.43
SIDE_T, SIDE_TW = 0.06, 0.30          # "side flame thickness": half-thickness cap (u) and its share of the local half-width
SIDE_NS, SIDE_NT = 30, 14
SIDE_HI_ROWS = [(2.57, 0.935, 0.935), (2.62, 0.93, 0.98), (2.68, 0.94, 1.05), (2.74, 0.94, 1.11), (2.80, 0.96, 1.18),
                (2.86, 0.98, 1.19), (2.92, 1.00, 1.21), (2.98, 1.09, 1.23), (3.04, 1.17, 1.25), (3.10, 1.22, 1.26),
                (3.15, 1.25, 1.25)]   # "side flame highlight": FRONT +X flame R > 200, G > 140 rows (median 241,185,183);
                                      #   the -X flame's streak is drawn shorter (z 2.50-2.95): built mirrored from +X
# ---- RIBBON ("three hanging petal strips, tattered, cloth-like edges"): FRONT lowest / topmost red per column.
RIB_TOP_Z = 2.25                      # strip tops (inside the orb's bottom, z 2.17 at the axis)
RIB_C_TOP = (-0.15, 0.18)             # centre strip top X edges (front zoom: x 222 / 255 at y 592)
RIB_C_BOT = [(-0.515, 1.13), (-0.46, 1.12), (-0.41, 1.20), (-0.34, 1.25), (-0.29, 1.24), (-0.26, 1.22), (-0.21, 1.27),
             (-0.14, 1.31), (-0.01, 1.33), (0.07, 1.39), (0.14, 1.40), (0.23, 1.37), (0.30, 1.41), (0.42, 1.40),
             (0.47, 1.31), (0.53, 1.26)]   # centre strip's tattered lower edge (X, z), front lowest-red per column
RIB_S_UPPER = [(0.15, 2.25), (0.27, 2.17), (0.51, 2.02), (0.82, 1.76), (1.06, 1.61), (1.21, 1.47)]
                                      # side strip (L; R mirrored) upper edge to the outer tip (front + back topmost red, avg)
RIB_S_LOWER = [(0.15, 2.25), (-0.04, 2.00), (-0.04, 1.55), (0.30, 1.47), (0.57, 1.445), (0.66, 1.40), (0.72, 1.32), (0.79, 1.23),
               (0.85, 1.17), (0.88, 1.30), (0.90, 1.46), (0.97, 1.49), (1.04, 1.55), (1.10, 1.53), (1.16, 1.49),
               (1.21, 1.47)]          # ... its hidden inner edge (behind the centre strip + stem) then the tattered lower edge with
                                      #   the drawn drip at |X| 0.79-0.90 (front +X 1.21 / -X 1.14) out to the tip
RIB_C_Y0 = -0.20                      # "ribbon skirt": the centre strip hangs in front of the stem (Y -0.20) ...
RIB_KICK, RIB_CUP = 0.15, 0.05        # ... its hem swings forward (-Y) / bulges (the 3/4 side's forward strip tip)
RIB_S_Y0, RIB_SWEEP = 0.20, 0.22      # ... the side strips hang BEHIND the stem, Y = 0.20 + 0.22 X (L; R mirrored), their
                                      #   hidden inner edges meeting behind it: front AND back orthos draw the ribbon over the
                                      #   stem's top, so the skirt wraps it (the 3/4 side's front-to-back spread)
RIB_T, RIB_TW = 0.022, 0.30           # "ribbon thickness": half-thickness cap + share of the local half-width
RIB_C_NS, RIB_C_NT = 12, 32
RIB_S_NS, RIB_S_NT = 48, 8
# ---- bake / material
BAKE_RES = (1024, 512)
HIGH_DENSITY = 3                      # the parts' bake high: rows + columns x this (smooth normal map, no ribbing)
SPECULAR = 0.30                       # "sheen": the sheet paints glossy highlights (regions); a little real specular
# ---- clips (PLACEHOLDER: the artist has not given Ember Eye's movement or hover height). 24 fps; integer harmonics.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_LIFT = (0.04, 0.30)              # "hover bob": body lift range (u), IDLE_BOB_CYC per loop (rest pose = the floor)
IDLE_BOB_CYC = 2
IDLE_PITCH, IDLE_ROLL = 1.2, 1.5      # "slight wander" deg
FLAME_IDLE = (1.5, 5.0, 0.7)          # "flame sway": root deg, tip deg, lag per bone (1 cyc, X + Y)
SIDE_IDLE = 4.0                       # side flames flutter deg (2 cyc)
RIB_IDLE = (3.0, 2.0)                 # ribbons pendulum deg (fore-aft, sideways)
STEM_IDLE = (2.0, 1.5)                # stem pendulum deg
WALK_N = 48                           # "drift loop": 2 s
WALK_LIFT, WALK_BOB = 0.18, 0.03
WALK_PITCH, WALK_PITCH_OSC, WALK_ROLL = 8.0, 1.0, 1.5
FLAME_WALK_TRAIL = (-2.0, -3.0, -4.0)  # flame bones trail back (deg)
HANG_WALK_TRAIL = 6.0                 # ribbons + stem trail back (deg)
SIDE_WALK_TRAIL = 5.0                 # side flames sweep back (deg)

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
          "design/reference/ember_eye-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; front + back true orthos, side a 3/4 slant; floor z = 0 at the "
                   "stem tip (it floats)",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


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

# =========================================================================== 1. body SDF: orb (puck) + stem - back inset
t_sdf = time.time()
OC = np.array(ORB_C, float)
STEM = FP.Profile(STEM_ROWS)


def stem_yc(z):
    return STEM_LEAN_Y * np.clip((STEM_TOP_Z - np.asarray(z, float)) / STEM_TOP_Z, 0.0, 1.0)


def sd_orb(P):
    return FP.sd_puck(P, OC, ORB_R, ORB_D, ORB_RR)


def sd_stem(P):
    P = np.atleast_2d(P)
    Q = P.copy(); Q[:, 1] = Q[:, 1] - stem_yc(P[:, 2])
    return STEM.sd(Q)


def sd_recess(P):
    """the solid removed from the back face: a cylinder (axis Y) r BACK_DISC_R from BACK_RECESS below the back face out"""
    P = np.atleast_2d(P)
    q = np.sqrt((P[:, 0] - OC[0]) ** 2 + (P[:, 2] - OC[2]) ** 2)
    return np.maximum(q - BACK_DISC_R, (OC[1] + ORB_D - BACK_RECESS) - P[:, 1])


def sd_shape(P):
    return SD.smax(SD.smin(sd_orb(P), sd_stem(P), STEM_K), -sd_recess(P), BACK_K)


_lo = np.array([OC[0] - ORB_R, OC[1] - ORB_D, -0.02]) - 0.06
_hi = np.array([OC[0] + ORB_R, STEM_LEAN_Y + 0.2, OC[2] + ORB_R]) + 0.06
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
g.apply(sd_orb, OC - np.array([ORB_R, ORB_D, ORB_R]), OC + np.array([ORB_R, ORB_D, ORB_R]), 0.0)
g.apply(sd_stem, np.array([-0.2, -0.2, 0.0]), np.array([0.2, STEM_LEAN_Y + 0.2, max(r[0] for r in STEM_ROWS)]), STEM_K)
g.apply(sd_recess, np.array([OC[0] - BACK_DISC_R, OC[1] + ORB_D - BACK_RECESS, OC[2] - BACK_DISC_R]),
        np.array([OC[0] + BACK_DISC_R, OC[1] + ORB_D + 0.05, OC[2] + BACK_DISC_R]), BACK_K, mode="subtract")
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

# ---- shell colour fields (each ~ a distance near its boundary, > 0 inside)
PUPIL_POLY = FP.teardrop(*PUPIL_TD)
CORE_POLY = FP.teardrop(*CORE_TD)


def shell_fields(P):
    P = np.atleast_2d(P)
    q = np.sqrt((P[:, 0] - OC[0]) ** 2 + (P[:, 2] - OC[2]) ** 2)
    front = P[:, 1] < OC[1]
    back = P[:, 1] > OC[1]
    eye = EYE_R - np.hypot(P[:, 0] - EYE_C[0], P[:, 2] - EYE_C[1])
    return {"stem_own": sd_orb(P) - sd_stem(P),
            "rim_q": q - (ORB_R - RIM_W),
            "rim_y": np.abs(P[:, 1] - OC[1]) - (ORB_D - ORB_RR),
            "back": np.where(back, BACK_DISC_CUT - q, -1.0),
            "eye": np.where(front, eye, -1.0),
            "pupil": np.where(front, FP.poly_field(P, PUPIL_POLY), -1.0),
            "core": np.where(front, FP.poly_field(P, CORE_POLY), -1.0)}


def curve_dist(P):
    """approx. unsigned distance (u) to each densified cut curve: eye / pupil / core / back disc / rim (inf off-face)"""
    f = shell_fields(P)
    orb_side = f["stem_own"] < 0.0
    return [np.abs(f["eye"]), np.abs(f["pupil"]), np.abs(f["core"]), np.abs(f["back"]),
            np.where(orb_side & (f["rim_y"] > -0.05), np.abs(f["rim_q"]), np.inf)]


# ---- densify: the decimated shell's ~0.06 edges would cut the pupil's pointed tip and the thin inner flame to
#      polygons. Subdivide faces within EYE_DENSE zone of each curve (or straddling it), pull new vertices onto the HIGH.
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
_dense_log = []
_keys = ("eye", "pupil", "core", "back", "rim_q")


def _face_arrays():
    bm.verts.ensure_lookup_table(); bm.verts.index_update(); bm.faces.ensure_lookup_table()
    V_ = np.array([v.co[:] for v in bm.verts])
    F_ = np.array([[v.index for v in f.verts] for f in bm.faces])
    return V_, F_


def _subdivide(mask):
    fs = [bm.faces[i] for i in np.nonzero(mask)[0]]
    ze = list({e for f in fs for e in f.edges})
    if ze:
        bmesh.ops.subdivide_edges(bm, edges=ze, cuts=1, use_grid_fill=True)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    return len(fs)


# (a) the flat eye face: decimation leaves it a few big triangles with NO vertex inside the eye, so a vertex-sampled
#     field never sees the eye. Split every front-face triangle near the eye until its longest edge <= EYE_FACE_EDGE.
#     Longest-edge bisection (each flagged face splits only its longest edge, both neighbours split midpoint -> apex;
#     BEAUTY re-triangulation hung on the collinear splits) keeps the triangles well shaped -- uniform 1-to-4 splits of
#     the decimated face's long fan triangles breed slivers. Target: EYE_FACE_EDGE over the eye face, EYE_CURVE_EDGE
#     where a face touches (within EYE_CURVE_ZONE) or straddles the white / pupil / inner-flame outline.
EYE_FACE_EDGE = 0.10
EYE_CURVE_EDGE, EYE_CURVE_ZONE = 0.045, 0.03
for rnd in range(40):
    V_, F_ = _face_arrays()
    T_ = V_[F_]
    el = np.linalg.norm(T_ - np.roll(T_, 1, axis=1), axis=2)          # edge k = (vert k-1, vert k)
    emax = el.max(1)
    C_ = T_.mean(1)
    near = np.hypot(C_[:, 0] - EYE_C[0], C_[:, 2] - EYE_C[1]) - np.max(np.linalg.norm(T_ - C_[:, None], axis=2), 1) < EYE_R + 0.06
    ff_ = shell_fields(V_)
    on_curve = np.zeros(len(F_), bool)
    for key_ in ("eye", "pupil", "core"):
        fF = ff_[key_][F_]
        on_curve |= np.any(np.abs(fF) < EYE_CURVE_ZONE, 1) | (np.any(fF > 0, 1) & np.any(fF <= 0, 1))
    target = np.where(on_curve, EYE_CURVE_EDGE, EYE_FACE_EDGE)
    m_ = (T_[:, :, 1].max(1) < OC[1] - ORB_D + 0.02) & near & (emax > target)
    if not m_.any():
        break
    ks = np.argmax(el, 1)
    split = set()
    for fi in np.nonzero(m_)[0]:
        a_i, b_i = int(F_[fi][ks[fi] - 1]), int(F_[fi][ks[fi]])
        e_ = bm.edges.get((bm.verts[a_i], bm.verts[b_i]))
        if e_ is not None:
            split.add(e_)
    bm.edges.index_update()
    for e_ in sorted(split, key=lambda e: e.index):          # deterministic order
        if not e_.is_valid:
            continue
        v0_, v1_ = e_.verts
        fs_ = list(e_.link_faces)
        _, m_v = bmesh.utils.edge_split(e_, v0_, 0.5)
        for f_ in fs_:                                         # each neighbour (now a quad) splits m -> its apex
            opp = [v for v in f_.verts if v not in (v0_, v1_, m_v)]
            if f_.is_valid and len(f_.verts) == 4 and len(opp) == 1:
                bmesh.utils.face_split(f_, m_v, opp[0])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    _dense_log.append({"eye_face_round": rnd + 1, "edges_split": len(split), "tris_after": len(bm.faces)})
# (b) the cut curves: faces within EYE_DENSE zone of a curve, or straddling it
for rnd in range(max(r for _, r in EYE_DENSE)):
    V_, F_ = _face_arrays()
    dd = curve_dist(V_)
    ff = shell_fields(V_)
    m_ = np.zeros(len(F_), bool)
    for j, ((zw, rr), key) in enumerate(zip(EYE_DENSE, _keys)):
        if rnd >= rr:
            continue
        dF = dd[j][F_]; fF = ff[key][F_]
        m_ |= np.any(dF < zw, 1) | (np.any(fF > 0, 1) & np.any(fF <= 0, 1) & np.all(np.isfinite(dF), 1))
    _dense_log.append({"round": rnd + 1, "faces": _subdivide(m_), "tris_after": len(bm.faces)})
bm.verts.ensure_lookup_table()
for v in bm.verts[_nv0:]:
    v.co = bvh_hi.find_nearest(v.co)[0]
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["retopo"]["densify"] = {"zones": EYE_DENSE, "rounds": _dense_log, "new_verts": len(LV) - _nv0, "tris_after": tri_count_F(LF)}
print("T densify", round(time.time() - T0, 1))
print("RETOPO", json.dumps(report["retopo"]))


# =========================================================================== 3. iso cuts (shell + parts)
def iso_cut(bm, LAY, key, tau, gate=None):
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


def cut_mesh(V, F, fields, cuts_fn):
    """-> (V', F', per-face mean of every field, cut log); cuts_fn(LAY) -> [(key, tau, gate)]"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {k: bm.verts.layers.float.new(k) for k in fields}
    for v in bm.verts:
        for k, arr in fields.items():
            v[LAY[k]] = float(arr[v.index])
    log = [iso_cut(bm, LAY, k_, tau_, gate_) for k_, tau_, gate_ in cuts_fn(LAY)]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    CV = np.array([v.co[:] for v in bm.verts])
    CF = [[v.index for v in f.verts] for f in bm.faces]
    FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
    bm.free()
    return CV, CF, FVAL, log


TOL = 0.01
t = time.time()
SHF = shell_fields(LV)


def shell_cuts(LAY):
    off_stem = lambda a, b: a[LAY["stem_own"]] < TOL and b[LAY["stem_own"]] < TOL
    in_rimq = lambda a, b: off_stem(a, b) and a[LAY["rim_q"]] > -TOL and b[LAY["rim_q"]] > -TOL
    in_eye = lambda a, b: a[LAY["eye"]] > -TOL and b[LAY["eye"]] > -TOL
    in_pup = lambda a, b: a[LAY["pupil"]] > -TOL and b[LAY["pupil"]] > -TOL
    return [("stem_own", 0.0, None), ("rim_q", 0.0, off_stem), ("rim_y", 0.0, in_rimq), ("back", 0.0, None),
            ("eye", 0.0, None), ("pupil", 0.0, in_eye), ("core", 0.0, in_pup)]


CV, CF, FV, shell_log = cut_mesh(LV, LF, SHF, shell_cuts)
shell_reg = np.full(len(CF), "body", dtype=object)
shell_reg[FV["stem_own"] > 0.0] = "stem"
shell_reg[(FV["rim_q"] > 0.0) & (FV["rim_y"] > 0.0) & (FV["stem_own"] <= 0.0)] = "rim"
shell_reg[FV["back"] > 0.0] = "back_disc"
shell_reg[FV["eye"] > 0.0] = "eye_white"
shell_reg[(FV["eye"] > 0.0) & (FV["pupil"] > 0.0)] = "pupil"
shell_reg[(FV["eye"] > 0.0) & (FV["pupil"] > 0.0) & (FV["core"] > 0.0)] = "pupil_core"
SHELL_OWN = {"stem": ["stem_own"], "rim": ["rim_q", "rim_y"], "back_disc": ["back"], "eye_white": ["eye"],
             "pupil": ["eye", "pupil"], "pupil_core": ["pupil", "core"]}
report["iso_cuts"] = {"shell": shell_log, "shell_tris_after_cuts": len(CF)}
print("T shell_cuts", round(time.time() - T0, 1))
print("CUTS shell", json.dumps(shell_log))

# =========================================================================== 4. parts (closed pillows)
FZ0 = FLAME_ORB_TOP
_FTIP0 = max(r[0] for r in FLAME_ROWS)


def flame_z(z):
    """the front's flame rows stretched above the orb top so the tip lands at FLAME_TIP_Z (identity at 6.97)"""
    z = np.asarray(z, float)
    return np.where(z > FZ0, FZ0 + (z - FZ0) * (FLAME_TIP_Z - FZ0) / (_FTIP0 - FZ0), z)


def smooth_rows(rows, passes=PROFILE_SMOOTH, n_per=8):
    """measured rows (z, a, b) -> a dense C1 profile: PROFILE_SMOOTH passes of a 1-2-1 average over the interior rows
    (the 1-px scan noise), then Catmull-Rom. Piecewise-linear rows put a crease every 0.1 u that the flat-shaded low AND
    the bake high both showed as horizontal ribs up the flame (v1 run 3)."""
    R = np.array(sorted(rows), float)
    for _ in range(passes):
        R[1:-1, 1:] = 0.25 * R[:-2, 1:] + 0.5 * R[1:-1, 1:] + 0.25 * R[2:, 1:]
    D = SD.catmull(R, n_per)
    D = D[np.concatenate([[True], np.diff(D[:, 0]) > 1e-9])]       # keep z strictly increasing
    return D


_fr = smooth_rows(FLAME_ROWS)
FR_Z = flame_z(_fr[:, 0]); FR_L = _fr[:, 1]; FR_R = _fr[:, 2]
_fy = np.array(FLAME_Y)
_fy_s = np.convolve(np.pad(_fy[:, 1], 1, mode="edge"), np.ones(3) / 3.0, mode="valid")   # light 3-tap smoothing
_fy_s[-1] = _fy[-1, 1]
_fyd = SD.catmull(np.stack([_fy[:, 0], _fy_s], 1), 8)       # C1 depth wave (no creases)


def flame_y(z):
    s = np.clip((np.asarray(z, float) - FZ0) / (FLAME_TIP_Z - FZ0), 0.0, 1.0)
    return np.interp(s, _fyd[:, 0], _fyd[:, 1])


def build_flame(ns=None, nt=None):
    ns = ns or FLAME_NS; nt = nt or FLAME_NT
    zs = np.linspace(FR_Z[0], FR_Z[-1], ns + 1)
    T, phi = FP.cos_cols(nt)
    L = np.interp(zs, FR_Z, FR_L); R = np.interp(zs, FR_Z, FR_R)
    P = np.zeros((ns + 1, nt + 1, 3))
    P[..., 0] = L[:, None] + (R - L)[:, None] * T[None, :]
    P[..., 1] = flame_y(zs)[:, None]
    P[..., 2] = zs[:, None]
    N = FP.grid_normals(P, (0.0, -1.0, 0.0))
    H = (FLAME_DEPTH * 0.5 * (R - L))[:, None] * np.sin(phi)[None, :]
    V, F, Rg, IJ, SK = FP.pillow(P, N, H, lambda i, j, top: "flame")
    centre = np.stack([0.5 * (L + R), flame_y(zs), zs], 1)
    return V, F, Rg, {"rows": ns, "cols": nt, "z": [round(float(zs[0]), 3), round(float(zs[-1]), 3)],
                      "centre": centre}


def build_side(ns=None, nt=None):
    """the L side flame (+X): rows up z from the bottom tip to the top tip, columns inner -> outer edge; the plane swept
    back Y = SWEEP (X - pivot)"""
    sr = np.array(SIDE_ROWS)
    ns = ns or SIDE_NS; nt = nt or SIDE_NT
    zs = np.linspace(sr[0, 0], sr[-1, 0], ns + 1)
    sd_ = smooth_rows(SIDE_ROWS)
    I = np.interp(zs, sd_[:, 0], sd_[:, 1]); O = np.interp(zs, sd_[:, 0], sd_[:, 2])
    T, phi = FP.cos_cols(nt)
    P = np.zeros((ns + 1, nt + 1, 3))
    P[..., 0] = I[:, None] + (O - I)[:, None] * T[None, :]
    P[..., 1] = SIDE_SWEEP[0] * (P[..., 0] - SIDE_SWEEP[1])
    P[..., 2] = zs[:, None]
    N = FP.grid_normals(P, (0.0, -1.0, 0.0))
    H = np.minimum(SIDE_T, SIDE_TW * 0.5 * (O - I))[:, None] * np.sin(phi)[None, :]
    V, F, Rg, IJ, SK = FP.pillow(P, N, H, lambda i, j, top: "side_flame")
    return V, F, Rg


def build_rib_centre(ns=None, nt=None):
    ns = ns or RIB_C_NS; nt = nt or RIB_C_NT
    T, phi = FP.cos_cols(nt)
    top = np.stack([RIB_C_TOP[0] + (RIB_C_TOP[1] - RIB_C_TOP[0]) * T, np.full(len(T), RIB_TOP_Z)], 1)
    bot = FP.resample_by_arc(np.array(RIB_C_BOT), nt + 1)
    ss = np.linspace(0.0, 1.0, ns + 1)
    XZ = top[None, :, :] * (1 - ss)[:, None, None] + bot[None, :, :] * ss[:, None, None]
    P = np.zeros((ns + 1, nt + 1, 3))
    P[..., 0] = XZ[..., 0]; P[..., 2] = XZ[..., 1]
    P[..., 1] = RIB_C_Y0 - (RIB_KICK * ss[:, None] + RIB_CUP * np.sin(np.pi * ss)[:, None] * np.sin(phi)[None, :])
    N = FP.grid_normals(P, (0.0, -1.0, 0.0))
    wid = np.linalg.norm(bot[-1] - bot[0]) * (ss) + (RIB_C_TOP[1] - RIB_C_TOP[0]) * (1 - ss)
    H = np.minimum(RIB_T, RIB_TW * 0.5 * wid)[:, None] * np.sin(phi)[None, :]
    V, F, Rg, IJ, SK = FP.pillow(P, N, H, lambda i, j, top_: "ribbon")
    return V, F, Rg


def build_rib_side(ns=None, nt=None):
    """the L side strip (+X): rows along the upper edge U and the lower path L (both by arc length, apex -> outer tip,
    the two ends welded), columns U -> L; hung behind the stem, Y = RIB_S_Y0 + SWEEP X"""
    ns = ns or RIB_S_NS; nt = nt or RIB_S_NT
    U = FP.resample_by_arc(np.array(RIB_S_UPPER), ns + 1)
    Lw = FP.resample_by_arc(np.array(RIB_S_LOWER), ns + 1)
    T, phi = FP.cos_cols(nt)
    XZ = U[:, None, :] * (1 - T)[None, :, None] + Lw[:, None, :] * T[None, :, None]
    P = np.zeros((ns + 1, nt + 1, 3))
    P[..., 0] = XZ[..., 0]; P[..., 2] = XZ[..., 1]
    P[..., 1] = RIB_S_Y0 + RIB_SWEEP * P[..., 0]
    N = FP.grid_normals(P, (0.0, -1.0, 0.0))
    wid = np.linalg.norm(U - Lw, axis=1)
    H = np.minimum(RIB_T, RIB_TW * 0.5 * wid)[:, None] * np.sin(phi)[None, :]
    V, F, Rg, IJ, SK = FP.pillow(P, N, H, lambda i, j, top_: "ribbon")
    return V, F, Rg


MIRX = np.array([-1.0, 1.0, 1.0])
FLAME_HI_POLY = FP.stroke_poly([(float(flame_z(z)), a, b) for z, a, b in FLAME_HI_ROWS])
SIDE_HI_POLY = FP.stroke_poly(SIDE_HI_ROWS)
PARTS = []
_fv, _ff, _fr_, FLAME_INFO = build_flame()
PARTS.append(FP.Part("flame_top", _fv, _ff, _fr_, bone="flame_top"))
_sv, _sf, _sr = build_side()
PARTS.append(FP.Part("flame.L", _sv, _sf, _sr, bone="flame_L"))
PARTS.append(FP.Part("flame.R", _sv * MIRX, [f[::-1] for f in _sf], _sr, bone="flame_R"))
_cv, _cf, _cr = build_rib_centre()
PARTS.append(FP.Part("ribbon.C", _cv, _cf, _cr, bone="ribbon.001"))
_rv, _rf, _rr = build_rib_side()
PARTS.append(FP.Part("ribbon.L", _rv, _rf, _rr, bone="ribbon.002"))
PARTS.append(FP.Part("ribbon.R", _rv * MIRX, [f[::-1] for f in _rf], _rr, bone="ribbon.003"))
print("T parts_built", round(time.time() - T0, 1))
report["parts_built"] = {p.name: {"tris_before_cuts": p.tris(), "open_edges": FP.open_edges(p.F)} for p in PARTS}

# highlight streak cuts on the flames (front-projected, through both faces of the thin pieces)
part_log = {}
for p in PARTS:
    if p.name == "flame_top":
        fld = {"hi": FP.poly_field(p.V, FLAME_HI_POLY)}
    elif p.name.startswith("flame."):
        Vm = p.V * MIRX if p.name.endswith(".R") else p.V
        fld = {"hi": FP.poly_field(Vm, SIDE_HI_POLY)}
    else:
        continue
    V2, F2, FV2, lg = cut_mesh(p.V, p.F, fld, lambda LAY: [("hi", 0.0, None)])
    base = p.R[0]
    R2 = np.where(FV2["hi"] > 0.0, "flame_hi" if base == "flame" else "side_hi", base).tolist()
    assert np.any(FV2["hi"] > 0.0), "highlight field never positive on %s" % p.name
    p.V, p.F, p.R = V2, F2, R2
    p.info["hi_faces"] = int(np.sum(FV2["hi"] > 0.0))
    part_log[p.name] = lg
report["iso_cuts"]["parts"] = part_log
print("T part_cuts", round(time.time() - T0, 1))
print("CUTS parts", json.dumps(part_log))

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
_shell_lo = float(CV[:, 2].min())
_below = [i[0] for i in ISL if i[1][:, 2].min() < _shell_lo - 1e-6]
assert not _below, "a part dips below the floor (the stem tip's rest z %.4f): %s" % (_shell_lo, _below)
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
assert abs(float(VALL[:, 2].min())) < 1e-9, "rest pose: the stem tip is the floor (min z 0)"
HV_s = HV - SHIFT
REG = ["body", "rim", "back_disc", "eye_white", "pupil", "pupil_core", "stem", "flame", "flame_hi", "side_flame",
       "side_hi", "ribbon"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "declared palette regions with no faces: %s" % _empty
# no region borrows a field: every shell region owns only faces its own field(s) claim; every field paints somewhere
for r_, fs_ in SHELL_OWN.items():
    m_ = shell_reg == r_
    for f_ in fs_:
        assert np.all(FV[f_][m_] > 0.0), "region %s has faces outside its field %s" % (r_, f_)
        assert np.any(SHF[f_] > 0.0), "field %s never positive" % f_
assert not np.any((shell_reg == "rim") & (FV["eye"] > 0.0)), "rim on the eye"


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
    bsdf.inputs["Specular IOR Level"].default_value = SPECULAR
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
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)   # palette <-> regions 1:1
_lit = sorted(n for n, v in pal_default["regions"].items() if "emission" in v)
report["glow"] = {"skin": "default", "glowing_regions": _lit,
                  "rule": "NO glow: the sheet draws none beyond the bright colours ('bright red with soft highlight'); "
                          "zero Glow. The artist can ask for an emissive flame tier."}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = jitter(VALL, FALL)
shade[np.isin(rid, [REG.index(r) for r in ("eye_white", "pupil", "pupil_core")])] = 1.0
shade[len(CF):] = 1.0                               # the pillow parts stay clean: their long row faces turned the +-3% value
                                                 #   jitter into horizontal bands up the flame (v1 run 2)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the orb centre -> the eye's front point (the flat front face at the eye centre)
anchor = OC - SHIFT
landmark = np.array([EYE_C[0], OC[1] - ORB_D, EYE_C[1]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "orb centre -> the eye's front point (the eye faces -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["eye"] = {"white_disc": {"centre_xz": list(EYE_C), "radius": EYE_R},
                 "pupil_teardrop": {"cx_zb_r_tip": list(PUPIL_TD)}, "inner_flame": {"cx_zb_r_tip": list(CORE_TD)},
                 "rule": "front-projected fields iso-cut into the orb's FLAT front face (smooth, no grain, densified)"}

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
_shell_tris = tri_count_F(CF)
_sh_FC = np.array([np.mean(CV[f], 0) for f in CF])
_stem_t = int(np.sum(sd_stem(_sh_FC) < sd_orb(_sh_FC)))
report["tris"] = {"total": tris_main, "window": TRI_BUDGET, "shell": _shell_tris,
                  "shell_before_densify_cuts": report["retopo"]["decimated_tris"],
                  "per_part": {"orb (+ eye, rim, back disc)": _shell_tris - _stem_t, "stem": _stem_t,
                               **{p.name: p.tris() for p in PARTS}},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)},
                  "per_part_rule": "shell faces counted to orb / stem by the lower SDF at the face centre"}
report["tier_rationale"] = ("REGULAR role: window [%d, %d]. The orb + stem shell is light (~%d decimated tris; its eye "
                            "and rim cut curves densified); the upper flame's thick wavy tongue, the two side flames and "
                            "the three tattered ribbon strips (closed pillows) carry the rest." % (TRI_BUDGET[0], TRI_BUDGET[1], BODY_TRIS))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"per_piece": open_e, "closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (SDF shell; pillows with welded tips); the eye, rim, back "
                                "disc and highlight streaks are iso-cut regions (no decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1)},
                     "sheet": {"front": "H 6.98 W 3.58", "back": "H 7.44 W 3.60", "side (3/4)": "H 7.43 W 1.61"},
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
low["conquest_source"] = "from scratch: design/reference/ember_eye-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
_box = lambda name: [VALL[RANGE[name][0]:RANGE[name][1]].min(0).tolist(), VALL[RANGE[name][0]:RANGE[name][1]].max(0).tolist()]
_rb = np.vstack([VALL[RANGE[n][0]:RANGE[n][1]] for n in ("ribbon.C", "ribbon.L", "ribbon.R")])
low["conquest_focus"] = json.dumps({
    "eye_detail": [(np.array([-0.75, -0.4, 2.12]) - SHIFT).tolist(), (np.array([0.75, 0.3, 3.60]) - SHIFT).tolist()],
    "upper_flame": _box("flame_top"),
    "side_flame": _box("flame.L"),
    "lower_ribbon_stem": [np.minimum(_rb.min(0), (np.array([-0.2, -0.2, 0.0]) - SHIFT)).tolist(), _rb.max(0).tolist()],
    "views": {"eye_detail": [0.0, 3.0], "upper_flame": [20.0, 5.0], "side_flame": [25.0, 5.0], "lower_ribbon_stem": [0.0, 5.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "centre_shift")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
_hd = HIGH_DENSITY
_hiparts = [build_flame(FLAME_NS * _hd, FLAME_NT * _hd)[:2], build_side(SIDE_NS * _hd, SIDE_NT * _hd)[:2]]
_hiparts.append((_hiparts[1][0] * MIRX, [f[::-1] for f in _hiparts[1][1]]))
_hiparts.append(build_rib_centre(RIB_C_NS * _hd, RIB_C_NT * _hd)[:2])
_rs = build_rib_side(RIB_S_NS * _hd, RIB_S_NT * _hd)[:2]
_hiparts += [_rs, (_rs[0] * MIRX, [f[::-1] for f in _rs[1]])]
for V, F in _hiparts:
    PH_V.append(np.asarray(V) - SHIFT); PH_F += [[i + o_ for i in f] for f in F]; o_ += len(V)
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
me.shade_flat()                       # bake AGAINST THE FLAT low the game shows (flat_shaded rule): the map then
                                      #   carries the high's smooth curvature onto every facet (baked against smooth normals,
                                      #   the coarse flame rows rendered as horizontal ribs: v1 run 1)
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
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets: orb + stem - back inset) + every pillow part re-generated at %dx rows + columns (the smooth curvature the flat low faces bake their normals from)" % HIGH_DENSITY,
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
                     "provenance": "pixel-sampled from the drawn surfaces; the sheet's five chips cited per region "
                                   "(see the palette notes)"}
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


FL_C = FLAME_INFO["centre"]                           # the flame centreline (x, y, z) rows
FL_BASE_Z = FLAME_ORB_TOP - 0.10                      # flame_top.001 starts just inside the orb top
_fl_al = SD.arclen(FL_C)


def fl_point(z):
    return np.array([np.interp(z, FL_C[:, 2], FL_C[:, k]) for k in range(3)])


FL_TIP_Z = float(FL_C[-1, 2])
FL_KZ = [FL_BASE_Z + (FL_TIP_Z - FL_BASE_Z) * k / 3.0 for k in range(4)]
SIDE_ROOT = np.array([0.80, 0.0, 2.33]); SIDE_TIPB = np.array([1.43, SIDE_SWEEP[0] * (1.43 - SIDE_SWEEP[1]), 4.05])
RIB_APEX = np.array([0.0, 0.0, 2.20])
RIB_C_END = np.array([0.0, RIB_C_Y0 - RIB_KICK, 1.30])
RIB_S_END = np.array([1.05, RIB_S_Y0 + RIB_SWEEP * 1.05, 1.45])
STEM_HEAD = np.array([0.0, 0.0, 2.20]); STEM_TAIL = np.array([0.015, float(stem_yc(0.0)), 0.0])
BONES = [("body", P_(OC), P_(OC + np.array([0.0, 0.0, 0.6])), "root")]
for k in range(3):
    BONES.append(("flame_top.%03d" % (k + 1), P_(fl_point(FL_KZ[k])), P_(fl_point(FL_KZ[k + 1])),
                  "body" if k == 0 else "flame_top.%03d" % k))
BONES += [("flame_L", P_(SIDE_ROOT), P_(SIDE_TIPB), "body"), ("flame_R", P_(SIDE_ROOT * MIRX), P_(SIDE_TIPB * MIRX), "body"),
          ("ribbon.001", P_(RIB_APEX), P_(RIB_C_END), "body"),
          ("ribbon.002", P_(RIB_APEX + np.array([0.15, 0.0, 0.0])), P_(RIB_S_END), "body"),
          ("ribbon.003", P_(RIB_APEX - np.array([0.15, 0.0, 0.0])), P_(RIB_S_END * MIRX), "body"),
          ("stem", P_(STEM_HEAD), P_(STEM_TAIL), "body")]
CONNECT = {"flame_top.002", "flame_top.003"}
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
OWN_W = 0.08                          # stem ownership blend half-width across the orb / stem fillet
FLAME_PARENT_S0 = -0.15               # the body -> flame_top.001 blend starts this far below the flame base (along z)
RIB_W = (0.10, 0.40)                  # ribbon strips: body at the apex -> strip bone by this distance from the apex
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
ws = smoothstep(-OWN_W, OWN_W, sd_orb(Pw) - sd_stem(Pw))
Wt[a_:b_, J["stem"]] = ws
Wt[a_:b_, J["body"]] = 1.0 - ws
a_, b_ = RANGE["flame_top"]
Pf = VALL[a_:b_] + SHIFT
Lf = FL_TIP_Z - FL_BASE_Z
Wv = K.vine_weights(np.clip(Pf[:, 2] - FL_BASE_Z, FLAME_PARENT_S0 - 1.0, Lf), Lf, 3, parent_s0=FLAME_PARENT_S0)
Wt[a_:b_, J["body"]] = Wv[:, 0]
for k in range(3):
    Wt[a_:b_, J["flame_top.%03d" % (k + 1)]] = Wv[:, k + 1]
for nm, bn in (("flame.L", "flame_L"), ("flame.R", "flame_R")):
    a_, b_ = RANGE[nm]
    Wt[a_:b_, J[bn]] = 1.0
for nm, bn, apex in (("ribbon.C", "ribbon.001", RIB_APEX), ("ribbon.L", "ribbon.002", RIB_APEX + np.array([0.15, 0, 0])),
                     ("ribbon.R", "ribbon.003", RIB_APEX - np.array([0.15, 0, 0]))):
    a_, b_ = RANGE[nm]
    Pr = VALL[a_:b_] + SHIFT
    wr = smoothstep(RIB_W[0], RIB_W[1], np.linalg.norm(Pr - apex, axis=1))
    Wt[a_:b_, J[bn]] = wr
    Wt[a_:b_, J["body"]] = 1.0 - wr
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
                  "rule": "shell: orb = body, stem by smoothstep(+-%.2f) of (orb distance - stem distance); upper flame: "
                          "rigkit.vine_weights along z from %.2f (body -> flame_top.001 from %.2f below); side flames rigid "
                          "on flame_L / flame_R; ribbons: body at the apex -> strip bone by smoothstep%s of the distance "
                          "from the apex" % (OWN_W, FL_BASE_Z, -FLAME_PARENT_S0, str(RIB_W))}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (every deform bone in BOTH clips); integer cycles per loop
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
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
        ph_b = TAU * IDLE_BOB_CYC * u
        w_ = 0.5 * (1.0 - math.cos(ph_b))
        z = IDLE_LIFT[0] + (IDLE_LIFT[1] - IDLE_LIFT[0]) * w_
        pitch = IDLE_PITCH * math.sin(TAU * u + 0.5)
        roll = IDLE_ROLL * math.sin(TAU * u + 1.9)
        P["body"] = ((0.0, 0.0, z), [("X", pitch), ("Y", roll)], None)
        for k in range(3):
            rx = K.vine_wave(u, k, 3, FLAME_IDLE[0], FLAME_IDLE[1], FLAME_IDLE[2], 1, -0.6)
            ry = K.vine_wave(u, k, 3, FLAME_IDLE[0], FLAME_IDLE[1], FLAME_IDLE[2], 1, 1.3)
            P["flame_top.%03d" % (k + 1)] = (None, [("X", rx), ("Y", ry)], None)
        sf = SIDE_IDLE * math.sin(TAU * 2 * u + 0.8)
        P["flame_L"] = (None, [("Y", sf)], None)
        P["flame_R"] = (None, [("Y", -sf)], None)
        for k, (bn, ph) in enumerate((("ribbon.001", 0.0), ("ribbon.002", 0.7), ("ribbon.003", 1.4))):
            P[bn] = (None, [("X", RIB_IDLE[0] * math.sin(ph_b - math.pi / 2 - 0.4 * k)),
                            ("Y", RIB_IDLE[1] * math.sin(TAU * u + ph))], None)
        P["stem"] = (None, [("X", STEM_IDLE[0] * math.sin(ph_b - math.pi / 2 - 0.3)),
                            ("Y", STEM_IDLE[1] * math.sin(TAU * u + 0.4))], None)
    else:
        z = WALK_LIFT + WALK_BOB * math.sin(TAU * 2 * u)
        pitch = WALK_PITCH + WALK_PITCH_OSC * math.sin(TAU * 2 * u - 1.0)
        roll = WALK_ROLL * math.sin(TAU * u + 0.6)
        P["body"] = ((0.0, 0.0, z), [("X", pitch), ("Y", roll)], None)
        for k in range(3):
            P["flame_top.%03d" % (k + 1)] = (None, [("X", FLAME_WALK_TRAIL[k] + 0.8 * math.sin(TAU * 2 * u - 1.6 - 0.5 * k)),
                                                    ("Y", 1.0 * math.sin(TAU * u + 1.0 - 0.5 * k))], None)
        P["flame_L"] = (None, [("Z", SIDE_WALK_TRAIL + 1.0 * math.sin(TAU * 2 * u - 1.2))], None)
        P["flame_R"] = (None, [("Z", -SIDE_WALK_TRAIL - 1.0 * math.sin(TAU * 2 * u - 1.2))], None)
        for k, bn in enumerate(("ribbon.001", "ribbon.002", "ribbon.003")):
            P[bn] = (None, [("X", HANG_WALK_TRAIL + 1.5 * math.sin(TAU * 2 * u - 2.2 - 0.3 * k)),
                            ("Y", 1.5 * math.sin(TAU * u + 1.6 + 0.5 * k))], None)
        P["stem"] = (None, [("X", HANG_WALK_TRAIL + 1.5 * math.sin(TAU * 2 * u - 2.6)),
                            ("Y", 1.5 * math.sin(TAU * u + 1.6))], None)
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
        by = pbs["body"].matrix.col[1].xyz
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "pitch": math.degrees(math.atan2(-by.y, by.z)), "roll": math.degrees(math.atan2(by.x, by.z)),
                     "root": float((rig.matrix_world @ pbs["root"].head).length),
                     "flame_tip": list(pbs["flame_top.003"].tail), "stem_tip": list(pbs["stem"].tail)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - arm_data.bones["body"].head_local.z
    ft = np.array([r["flame_tip"] for r in R_]); stt = np.array([r["stem_tip"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "pitch_deg": [round(float(g_("pitch").min()), 3), round(float(g_("pitch").max()), 3)],
                    "roll_deg": [round(float(g_("roll").min()), 3), round(float(g_("roll").max()), 3)],
                    "flame_tip_p2p_xyz": (ft.max(0) - ft.min(0)).round(4).tolist(),
                    "stem_tip_p2p_xyz": (stt.max(0) - stt.min(0)).round(4).tolist()}
    # nothing dips below the floor in any clip frame (it floats: the rest pose touches z 0, the clips only lift)
    assert clip_rep[cn]["clearance_min_z"] >= -1e-4, ("below the floor", cn, clip_rep[cn]["clearance_min_z"])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hover"] = {"rest_height_units": HH, "idle_lift_units": list(IDLE_LIFT), "walk_lift_units": WALK_LIFT,
                "rule": "rest pose: stem tip on the floor (contract feet_origin, z 0); idle keys body.location z = "
                        "IDLE_LIFT[0] .. [1] (slow bob), walk WALK_LIFT + bob; the contract root never moves. The sheet's "
                        "floor shadow sits well under the tip: the in-game hover height is the artist's open question"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("ember_eye v1: root > body > flame_top.001 > .002 > .003; body > flame_L, flame_R; body > "
                       "ribbon.001 (centre), ribbon.002 (L), ribbon.003 (R); body > stem")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Ember Eye's movement or hover height; open "
                               "question): idle = 4 s hover bob (body +%.2f..%.2f u, 0.5 Hz) + flame sway (a wave up the "
                               "flame chain), side-flame flutter, ribbon + stem pendulum; walk = 2 s in-place hover-drift "
                               "(%.0f deg forward lean; flames, ribbons and stem trailing)" % (IDLE_LIFT[0], IDLE_LIFT[1], WALK_PITCH))
low["conquest_locomotion"] = "hover (floats): rest pose stem tip z 0 per contract; the clips lift the body"
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
              "structure": "armature identity + skinned 'ember_eye' (one opaque body primitive); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("HOVER", json.dumps(rep["hover"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
