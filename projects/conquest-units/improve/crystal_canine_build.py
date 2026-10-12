"""Crystal Canine -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/crystal_canine_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x3: Petal Cobra, Crystal Canine, Leaf Bird (artist sheets)" (section
CRYSTAL CANINE) + the sheet design/reference/crystal_canine-character-sheet.png (intake:
design/reference/crystal_canine/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The SIDE view is the true ortho (faces image-left = the unit's
front, -Y); the FRONT view is near-frontal (the head pitched slightly down, the tail swung to one side) and gives only
what the side cannot show (widths, X spacing):
  SIDE view:   Y = (x_px - 1005.5) / 100 ; z = (772 - y_px) / 100
  FRONT view:  X = (x_px - 205) / 100    ; z = (776 - y_px) / 100     (the unit's left, +X, on image right)
  BACK view:   X = -(x_px - 565) / 100   ; z = (786 - y_px) / 100     (the unit's right on image right)
X +ve = the unit's LEFT (L). Floor z = 0 at the soles.

A STANDING FOUR-LEGGED CANINE, built SYMMETRIC in rest pose (artist house rule 2026-10-10: the sheet's pose is art;
posing comes later in the rig / clips): four legs mirror-symmetric about X = 0, no stagger, paws flat on the floor.

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the slim TORSO (round cone, pelvis -> chest), two
     HAUNCHES, four slim LEGS (round-cone chains) with PAWS (toe creases carved), the NECK, the CHEST RUFF (an ellipsoid
     ringed with blunt fur tufts + a pointed bottom tuft + a fine fuzz noise at the HIGH only), the HEAD + MUZZLE + two
     CHEEK tufts, two LARGE POINTED EARS (flattened round-cone chains, broad convex profile), the PLUME TAIL (a round-cone
     chain flattened across X with plume lobes, one inner wisp). Marching tets = the HIGH (bake source); collapse
     decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: the blush INNER EAR (an inset triangle on each ear's front face).
  3. PARTS (crystal_canine_parts.py, closed): EYE whites + IRISES + PUPILS + the NOSE (closed conforming decals), the
     faceted FOREHEAD GEM (9-vertex faceted solid, tone per facet by the light), the CRYSTAL MANE (MANE_N faceted
     hexagonal prisms per side, tone per facet by the light).
  4. palette (palettes.py regions -> Col / Glow, no glow), Smart UV, bake normal + AO from the SDF high + parts, seed 0.
  5. RIG: root > spine.001 > spine.002 > spine.003 > neck > head > ear_L / ear_R; spine.003 > foreleg_{L,R}.upper > .lower
     > .paw; spine.001 > hindleg_{L,R}.upper > .lower > .paw; spine.001 > tail.001 > .002 > .003 > .004. Clips
     (PLACEHOLDER, movement intent open): idle (breathing + an ear twitch + a tail sway, paws planted by a planar 2-bone
     IK) + walk (a 4-beat walk in place: stance paws planted on the floor and carried back, swing paws lifted). glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                # noqa: E402  (read-only use)
import palettes as PAL            # noqa: E402  (read-only use)
import magmoo_sdf as SD           # noqa: E402  (read-only use: the SDF kit)
import crystal_canine_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "crystal_canine"
CHAR_ID = "crystal_canine"            # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.20                       # >= the largest smooth-union k (0.14) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 15000                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- HEAD. SIDE view: crown under the ears ~y 218-232 (z 5.40-5.54), back of the skull x ~950 (Y -0.55), chin / jaw
#   line y 332-335 @ x 820-850 (z 4.40), forehead stop x 815-830 @ y 260-280 (Y -1.9..-1.75). FRONT view: crown between
#   the ears y 220-230 (z 5.46-5.56), face half width at the eyes x 135-281 (+-0.73 with the cheek fur)
HEAD_C, HEAD_R, HEAD_K = (0.0, -1.10, 4.98), (0.68, 0.68, 0.58), 0.10   # "head size" (half width / depth / height)
# ---- MUZZLE: SIDE view nose tip x 786-790 @ y 298-312 (Y -2.18, z 4.67), muzzle top line to the stop (830, 268), jaw
#   (805-850, 330-335); FRONT view nose-level width x 180-232 (+-0.26)
MUZZLE = ((0.0, -1.62, 4.72), (0.0, -2.06, 4.66), 0.30, 0.13)   # (a, b, r_a, r_b)
MUZZLE_SX, MUZZLE_K = 1.25, 0.10      # "muzzle width" (X stretch)
# ---- CHEEK fur: FRONT view tufts at x 127 / 285 (+-0.79) @ y 290-330 (z 4.46-4.86); SIDE view tuft (950, 285-300)
CHEEK = ((0.40, -1.00, 4.66), (0.66, -0.88, 4.44), 0.22, 0.14)  # L side (a, b, r_a, r_b): blunt
CHEEK_K = 0.08
# ---- NECK (hidden under the ruff + mane; the SIDE view's background gap x 830-850 @ y 333-345 bounds its front at Y -1.55)
NECK = ((0.0, -0.80, 3.30), (0.0, -1.15, 4.45), 0.52, 0.46)
NECK_K = 0.12
# ---- TORSO: SIDE view back line y 455-465 @ x 1090-1110 (z 3.07-3.17), rump x 1120-1130 (Y 1.15-1.25), belly y 523-540
#   @ x 940-970 (z 2.32-2.49), chest under the ruff; BACK view haunch half width 0.56 @ z 2.96
TORSO = ((0.0, 0.82, 2.74), (0.0, -0.95, 2.95), 0.42, 0.55)   # (pelvis centre, chest centre, radii)
TORSO_SX = 1.05                       # "body width": the torso's X stretch (slim)
# ---- CHEST RUFF: SIDE view front edge x 800-810 @ y 380-490 (Y -1.98, z 2.8-3.9), bottom (850-870, 542-574) (z 2.0-2.3);
#   FRONT view white ruff +-0.85 with tufts, its V bottom point (203, 533) (z 2.43)
#   HEART-SHAPED V BIB: FRONT view widest at the top (x 120-290 @ y 376: +-0.85, z 4.0), narrowing (+-0.7 @ z 3.0) to the
#   V point (203, 533) (z 2.43); the V edges carry soft tufts pointing down + in
RUFF_C, RUFF_R, RUFF_K = (0.0, -1.42, 3.88), (0.84, 0.54, 0.62), 0.12   # the bib's broad top
RUFF_V = ((0.0, -1.52, 3.55), (0.0, -1.60, 2.15), 0.46, 0.09)   # the bib's V: a tapering cone, extended DOWN between
                                      #   the front legs (artist v1 markup; SIDE view chest fur bottom (850-870, 574-603)
                                      #   = z 1.98-1.69)
CHEST_LOCKS = [(0.0, 4.00, 0.38, 0.0), (0.30, 4.02, 0.36, 0.15), (0.58, 3.95, 0.34, 0.35),
               (0.15, 3.58, 0.42, 0.05), (0.45, 3.55, 0.40, 0.20), (0.70, 3.55, 0.34, 0.45),
               (0.0, 3.12, 0.42, 0.0), (0.30, 3.10, 0.42, -0.10), (0.56, 3.08, 0.38, 0.20),
               (0.15, 2.66, 0.40, -0.25), (0.38, 2.68, 0.36, -0.35),
               (0.0, 2.26, 0.46, 0.0), (0.16, 2.30, 0.36, -0.40)]   # "chest fur": pointed flame locks layered DOWN in
                                      #   rows (x, z on the bib front, length, sideways lean + out / - in); x > 0 mirrored
CHEST_LOCK_R = (0.16, 0.012)          # lock root / tip radius (pointed, the sheet's fur locks)
CHEST_LOCK_FLAT = 0.40                # lock thickness share (flattened against the chest)
CHEST_LOCK_K = 0.06                   # blend: small, so the locks stay distinct layered flames (no round lumps)
RUFF_FUZZ = (0.0, 0.08)               # "fuzz": noise amplitude / wavelength -- OFF (v1 gate: the baked fuzz read as dark
                                      #   slivers over the bib; the sheet's ruff is clean cream with soft lobes)
# ---- EARS: LARGE broad triangles. FRONT view tips (122-132, 160) / (292-298, 160) (X -0.78 / +0.90, z 6.16), widths
#   0.62 @ z 5.56, 0.52 @ z 5.76, 0.34 @ z 5.96, 0.22 @ z 6.06 (the outer edge near-vertical at X -0.86); BACK view tips
#   X -0.65 / +0.82. SIDE view near ear tip (930, 152) (Y -0.76, z 6.20), base x 885-965; far ear tip (858, 161)
EAR_BASE = (0.50, -0.92, 5.22)        # the base centre (L side), on the head surface
EAR_TIP = (0.84, -0.80, 6.18)         # "ear tip" (L side)
EAR_R = (0.36, 0.02)                  # "ear width": base / tip half width
EAR_CONVEX = 0.65                     # "ear fullness": width ~ (1 - t)^this along the ear (< 1 = broad up high)
EAR_FLAT = 0.85                       # "ear thickness": front-back flattening of the cone
EAR_YAW = 25.0                        # the ear face turns this far from the front toward its side
EAR_K = 0.06
EAR_CUP = (0.70, 0.55, 0.12, 0.80)   # "ear cup": the inner ear goes IN -- a concave hollow on the front face (radius x
                                      #   the ear's, forward offset x the local radius, start / end share of the length);
                                      #   the opening is ~0.54 x the local radius wide, a rim ~0.46 x it each side
EAR_INNER_TAU = 0.012                 # the blush inner ear = the hollow's surface (deeper than this inside the solid ear)
# ---- FORELEGS (L; R mirrored). FRONT view legs x 165-195 / 218-246 @ y 650 (X +-0.26, 0.30 wide), paws x 157-200 /
#   213-254 @ y 750 (0.41-0.43 wide); SIDE view near leg front edge x 870-880 @ y 600-700 (Y -1.35..-1.25), paw x 832-900
#   @ y 740-772 (toe front Y -1.73)
FORE_KNOTS = [(0.27, -1.00, 2.70), (0.26, -1.18, 1.75), (0.25, -1.12, 0.45)]   # shoulder, elbow, wrist
#   leg WIDTHS (v1 gate: spindly): SIDE view near foreleg depth x 878-921 @ y 640 (z 1.32: 0.43), x 881-924 @ y 680
#   (z 0.92: 0.43), x 870-923 @ y 600 (z 1.72: 0.53 incl. the far leg); FRONT view width x 156-191 / 221-258 @ y 600
#   (0.35-0.37), x 165-195 / 218-246 @ y 650 (0.28-0.30), x 169-197 / 214-242 @ y 700 (0.28) -> an oval section
FORE_R = (0.26, 0.22, 0.17)           # "foreleg thickness" (depth half-widths) at shoulder / elbow / wrist
FORE_SX = 0.72                        # "leg flatness": the X half-width is this share of the depth (front 0.29 / side 0.41)
FORE_PAW_C, FORE_PAW_R = (0.25, -1.38, 0.14), (0.20, 0.32, 0.16)   # "paw size" (floor-cut)
FORE_TOE = (0.25, -1.68, 0.10)        # the toe front (chain end)
LEG_K, PAW_K = 0.10, 0.07
# ---- HINDLEGS (L; R mirrored -- SYMMETRIC, house rule): the SIDE view's near (true-ortho) hind leg: paw x 1085-1140 @
#   y 740-772 (Y 0.80..1.35), the lower leg x 1090-1140 near-vertical; X from the BACK view's hind paws x 503-551 / 590-615
#   @ y 750 (X +0.38 / -0.38). The sheet's stride (the far hind leg drawn forward) is pose, not model.
HIND_L = {"hip": (0.30, 0.72, 2.35), "stifle": (0.34, 0.92, 1.40), "hock": (0.37, 1.20, 0.58),
          "paw": (0.38, 1.05, 0.14), "toe": (0.38, 0.78, 0.10), "haunch": (0.28, 0.82, 2.20)}
HIND = {"L": HIND_L, "R": {k_: (-v_[0], v_[1], v_[2]) for k_, v_ in HIND_L.items()}}
#   SIDE view near hind leg x 1090-1140 @ y 650 (z 1.22: 0.50), x 1092-1140 @ y 700 (z 0.72: 0.48), thigh x 1060-1140 @
#   y 600 (z 1.72: 0.80, the haunch); FRONT view x 122-153 @ y 650 (0.31), x 118-146 @ y 700 (0.28)
HIND_R = (0.30, 0.25, 0.20)           # "hindleg thickness" (depth half-widths) at hip / stifle / hock
HIND_SX = 0.62                        # X half-width share (front 0.30 / side 0.49)
HIND_PAW_R = (0.19, 0.28, 0.15)
HAUNCH_R, HAUNCH_K = (0.24, 0.44, 0.68), 0.14   # "haunch size" (SIDE view thigh lobe Y 0.40-1.25, z 1.3-3.0)
# ---- LEG FUR: pointed flame tufts (geometry, flattened across X so they read in the side silhouette). SIDE view: the
#   fur tuft behind the near elbow (905-930, 560-600), the jagged back of the thigh (1120-1140, 590-620), a small tuft
#   behind the wrist (905-915, 700-720)
FORE_TUFTS = [((0.26, -1.00, 2.00), (0.27, -0.78, 1.62), 0.12), ((0.30, -1.02, 2.25), (0.35, -0.80, 1.92), 0.10),
              ((0.25, -1.00, 0.72), (0.25, -0.86, 0.52), 0.07)]   # (root, tip, root radius) L side, mirrored
HIND_TUFTS = [((0.36, 1.08, 1.82), (0.38, 1.32, 1.48), 0.12), ((0.42, 0.95, 1.98), (0.48, 1.14, 1.66), 0.10)]
TUFT_FLAT, TUFT_TIP_R, TUFT_K = 0.45, 0.012, 0.05   # tuft thickness share, tip radius (pointed), blend
CREASES = (-0.07, 0.07)               # "toe creases": 2 grooves across each paw front (3 toes, the sheet's ink lines)
CREASE_R, CREASE_K = 0.022, 0.015
# ---- TAIL: a long flowing PLUME. SIDE view outer edge (1130,481) (1150,512) (1170,545) (1190,582) (1197,640) (1195,700)
#   (1180,731), tip (1150-1160, 748) (Y 1.5, z 0.24); the root at the rump (1110-1130, 465-480); lower part ~0.6-0.9 wide.
#   The FRONT and BACK views swing it to opposite sides (front: the unit's left, back: the unit's right) -> built on the
#   midline as the side view draws it
#   v1 gate: a stiff tube -> a FLOWING plume: thin root, wide body, tapered soft tip (centre = the drawn outer edge minus
#   the local radius), plus layered LOCKS ending in their own soft points (the sheet's tail panel + side view wisps)
TAIL_KNOTS = [(0.0, 1.05, 2.98), (0.0, 1.28, 2.78), (0.0, 1.42, 2.30), (0.0, 1.56, 1.62), (0.0, 1.58, 1.00),
              (0.0, 1.56, 0.52), (0.0, 1.44, 0.26)]
TAIL_RAD = [0.09, 0.15, 0.24, 0.33, 0.34, 0.22, 0.04]   # "tail fullness" along the knots
TAIL_LOCKS = [  # "tail locks": (knots, radii) -- back-outer, left, right, inner-front; tips >= z 0.34 (off the floor)
    ([(0.0, 1.30, 2.70), (0.0, 1.62, 1.90), (0.0, 1.74, 1.00), (0.0, 1.62, 0.34)], [0.10, 0.18, 0.18, 0.03]),
    ([(0.08, 1.40, 2.40), (0.12, 1.60, 1.50), (0.07, 1.56, 0.75), (0.04, 1.38, 0.38)], [0.12, 0.20, 0.15, 0.03]),
    ([(-0.08, 1.40, 2.40), (-0.12, 1.60, 1.50), (-0.07, 1.56, 0.75), (-0.04, 1.38, 0.38)], [0.12, 0.20, 0.15, 0.03]),
    ([(0.0, 1.35, 2.10), (0.0, 1.38, 1.30), (0.0, 1.36, 0.70), (0.0, 1.24, 0.42)], [0.12, 0.18, 0.14, 0.03])]
TAIL_SX = 0.78                        # the plume is flattened across X (side-view broad, clear of the hind legs)
TAIL_K = 0.08
TAIL_LOBES = ()   # "plume lobes": (arc
                                      #   fraction, size x the local radius, side of the back edge they lean to)
TAIL_WISP = []                        # the inner wisp the SIDE view draws (a lobe from under the rump to a tip at
TAIL_WISP_R = []                      #   (1020-1045, 720-740)): OFF in v1 -- from the front / three-quarter it reads as a
                                      #   thin spike hanging between the legs. Knots to restore it: [(0, 1.15, 2.62),
                                      #   (0, 0.95, 1.80), (0, 0.55, 0.95), (0, 0.22, 0.36)], radii [0.12, 0.15, 0.10, 0.03]
# ---- FACE parts. FRONT view eyes x 157-190 / 228-257 @ y 283-312 (centres X +-0.34, 0.32 x 0.25), iris x 170-186
#   (shifted to the nose side), pupil ~(180, 298); SIDE view eye (845, 282) (Y -1.6, z 4.90) -> z 4.86 (the front view
#   draws the face pitched down: eye z 4.79). Nose: FRONT x 199-217 @ y 324-335 (0.18 x 0.11); SIDE tip z 4.67
EYE_X, EYE_Z = 0.34, 4.86             # "eye placement" (mirrored); cast from the front onto the head
EYE_A, EYE_B = 0.165, 0.12            # "eye size": almond half width / half height
EYE_SHARP = 0.6                       # almond corner sharpness (0 = oval)
EYE_TILT = 8.0                        # "eye slant": the outer corner up (deg)
EYE_FACE = 0.45                       # the eye faces this share further toward the front than the head normal
IRIS_RC = 0.125                       # "iris size": circle radius, CLIPPED to the almond (v1 gate: googly; the sheet's iris
                                      #   fills the eye's height, front view iris x 168-186 of the eye x 157-190)
IRIS_IN = 0.035                       # the iris sits this far toward the nose (front view: the white shows on the outer side)
PUPIL_R = (0.040, 0.062)              # "pupil size"
NOSE_Z, NOSE_R = 4.68, (0.095, 0.062)   # "nose" (cast from the front on the midline)
DECAL_UP, DECAL_DN, DECAL_DOME = 0.008, 0.03, 0.014   # decal top offset / sink / dome height
# ---- FOREHEAD GEM: FRONT view kite top (205, 217) (z 5.59), corners (182, 238) / (230, 237) (X +-0.24, z 5.38), bottom
#   (207, 288) (z 4.88), a vertical front ridge; SIDE view front face x 808-835 @ y 228-282 (Y -1.97, z 4.90-5.44), the
#   crown ridge back to (880, 218) (Y -1.25, z 5.54); BACK view the ridge shows over the crown x 553-593 @ y 220-235
GEM_KITE = {"T": (0.0, 5.52), "Lc": (0.24, 5.32), "B": (0.0, 4.92), "Rc": (-0.24, 5.32), "F": (0.0, 5.24)}   # (X, z)
                                      #   of the kite rim + its centre ridge point, cast onto the forehead from the front
GEM_PROUD = (0.030, 0.050)            # "gem height": rim / centre stand this far proud of the forehead (a flush inlay)
GEM_SINK = 0.05                       # the inlay's buried back
GEM_LIGHT = (0.35, -0.55, 0.75)       # facet tone: facets toward this light take gem_lit (front view: the right facet lit)
# ---- CRYSTAL MANE: per side (L; R mirrored) layered faceted prisms from the neck / shoulders radiating out + back.
#   Tips: FRONT view (X, z) image-right (346,258) (369,302) (388,356) (378,425) (347,346) (305,452) (303,490), image-left
#   (63,255) (43,285) (47,322) (23,352) (21,405) (73,413) (110,455) (110,500) (99,280); SIDE view (Y, z) (1047,268)
#   (1085,318) (1120,395-440: the big one) (1050,415) (1000,340) (955,295) (960,440); chest-front shards (790,380) (815,345)
MANE = [  # (root (L side), tip, radius) -- 12 per side
    ((0.38, -0.80, 4.12), (1.42, 0.42, 5.12), 0.12),    # 1 top: sweeps up + back
    ((0.42, -0.70, 3.95), (1.63, 0.80, 4.67), 0.13),    # 2
    ((0.45, -0.92, 3.85), (1.83, 0.00, 4.22), 0.13),    # 3 long, out
    ((0.45, -0.55, 3.55), (1.78, 1.15, 3.64), 0.19),    # 4 the big near-horizontal one sweeping back over the shoulder
    ((0.50, -0.75, 3.40), (1.32, 0.45, 3.60), 0.12),    # 5
    ((0.40, -1.00, 4.20), (1.06, -0.50, 4.88), 0.09),   # 6 short upper inner
    ((0.45, -0.98, 3.95), (1.45, -0.40, 4.40), 0.10),   # 7 short mid
    ((0.55, -1.00, 3.35), (0.98, -0.45, 3.22), 0.10),   # 8 low, out + down
    ((0.55, -1.12, 3.10), (0.96, -0.90, 2.80), 0.08),   # 9 lowest
    ((0.62, -1.15, 3.70), (1.30, -1.55, 4.05), 0.09),   # 10 front-layer shard (out + slightly forward)
    ((0.58, -1.10, 4.00), (1.15, -1.45, 4.55), 0.08),   # 11 front-layer shard (out + up)
    ((0.55, -1.20, 3.50), (1.25, -1.30, 3.85), 0.10)]   # 12 mid-front
MANE_RSCALE = 1.75                    # "crystal width": x the table radius
MANE_SQUASH = 0.60                    # "crystal thickness": the section's flattening across its fan (1 = round hex)
MANE_TIP = 0.24                       # "crystal points": the pyramid tip is this share of each prism's length
MANE_BURY = 0.22                      # the prisms grow from this deep inside the fur
MANE_BULGE = 1.10                     # mid-length swell of the section
MANE_TIP_OFF = 0.35                   # the apex sits off-centre (x radius): angular, natural tips
CRYSTAL_LIGHT = (0.0, -0.60, 0.80)    # facet tone light (symmetric, from above-front): toward -> crystal_lit, away -> shade
CRYSTAL_TONE_T = (0.30, -0.45)      # lit above / shade below these light dots
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands)
OWN_TAU = 0.06                        # soft part ownership for weights
NECK_BAND = (3.40, 3.80)              # spine.003 -> neck blend (z) on the neck
HEAD_BAND = (4.25, 4.55)              # neck -> head blend (z)
RUFF_BAND = (3.70, 4.20)              # ruff: spine.003 -> neck (z)
EAR_BLEND = (0.12, 0.35)              # head -> ear blend along the ear axis (fraction)
LEG_BLEND = (0.12, 0.08)              # leg joint blend half width (hip/knee), the paw blend (above the wrist / hock only)
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent not yet given
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.9                          # "breathing": chest rise (spine.002/.003 pitch deg, 2 breaths per loop)
IDLE_HEAD = (1.0, 2.5)                # head nod (2 cyc) / slow look (1 cyc), deg
EAR_TWITCH = (14.0, 0.30, 0.70, 30)   # "ear twitch": (deg, phase L, phase R, sharpness: (0.5 + 0.5 cos)^n pulse)
TAIL_SWAY = (4.0, 7.0, 10.0, 13.0, 1, 0.6)   # "tail sway": per bone yaw deg .001-.004, cyc, lag per bone
WALK_N = 48                           # "walk loop": 2 s
STRIDE, LIFT, DUTY = 0.40, 0.14, 0.75   # "stride" (paw travel), "paw lift", stance share of the cycle
WALK_PHASE = {"hindleg_L": 0.0, "foreleg_L": 0.25, "hindleg_R": 0.5, "foreleg_R": 0.75}   # 4-beat lateral sequence
PAW_CURL = 14.0                       # swing paws curl (deg)
WALK_BOB = 0.02                       # body bob (2 per loop)
WALK_TAIL = (3.0, 5.0, 7.0, 9.0, 1, 0.6)
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
          "design/reference/crystal_canine-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the true side ortho + the front view's widths; floor z = 0",
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


def sd_xstretch_cone(P, a, b, r1, r2, sx):
    Q = P.copy(); Q[:, 0] = Q[:, 0] / sx              # X-stretched (sx >= 1) / squashed (sx < 1): scaled by min(1, sx)
    A = np.array(a, float); B = np.array(b, float)
    A[0] /= sx; B[0] /= sx
    return SD.sd_round_cone(Q, A, B, r1, r2) * min(1.0, sx)


def sd_torso(P):
    return sd_xstretch_cone(P, *TORSO, TORSO_SX)


def sd_neck(P):
    return SD.sd_round_cone(P, *NECK)


def sd_head(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R)


def sd_muzzle(P):
    return sd_xstretch_cone(P, *MUZZLE, MUZZLE_SX)


def sd_cheek(P, sg):
    return SD.sd_round_cone(P, mir(CHEEK[0], sg), mir(CHEEK[1], sg), CHEEK[2], CHEEK[3])


def chain_sdf(P, pts, rads, sx=1.0):
    """round-cone chain; sx < 1 squashes its X section (an oval leg), scaled to a valid lower bound"""
    Q = P.copy(); Q[:, 0] = Q[:, 0] / sx
    d = np.full(len(P), 1e9)
    for (a, b), (ra, rb) in zip(zip(pts[:-1], pts[1:]), zip(rads[:-1], rads[1:])):
        a = np.array(a, float); b = np.array(b, float); a[0] /= sx; b[0] /= sx
        d = np.minimum(d, SD.sd_round_cone(Q, a, b, ra, rb))
    return d * min(1.0, sx)


def sd_flame(P, root, tip, r0, r1, nrm, flat):
    """a pointed flame lock / fur tuft: a round cone root -> tip, flattened along nrm (made perpendicular to its axis)"""
    root = np.asarray(root, float); tip = np.asarray(tip, float)
    a = unit(tip - root)
    n = unit(np.asarray(nrm, float) - a * float(np.dot(nrm, a)))
    w = np.cross(a, n)
    Q = P - root
    L = float(np.linalg.norm(tip - root))
    q = np.stack([Q @ w, Q @ a, (Q @ n) / flat], 1)
    return SD.sd_round_cone(q, (0.0, 0.0, 0.0), (0.0, L, 0.0), r0, r1) * flat


def tufts_sdf(P, tufts, sg):
    d = np.full(len(P), 1e9)
    for r_, t_, rr in tufts:
        d = SD.smin(d, sd_flame(P, mir(r_, sg), mir(t_, sg), rr, TUFT_TIP_R, (1.0, 0.0, 0.0), TUFT_FLAT), TUFT_K)
    return d


def fore_pts(sg):
    return [mir(p, sg) for p in FORE_KNOTS]


def sd_foreleg(P, sg):
    pts = fore_pts(sg) + [mir(FORE_PAW_C, sg) + np.array([0.0, 0.10, 0.04])]
    d = np.minimum(chain_sdf(P, pts, list(FORE_R) + [0.15], FORE_SX), SD.sd_ellipsoid(P, mir(FORE_PAW_C, sg), FORE_PAW_R))
    return SD.smin(d, tufts_sdf(P, FORE_TUFTS, sg), TUFT_K)


def hind_pts(s):
    h = HIND[s]
    return [np.array(h[k], float) for k in ("hip", "stifle", "hock")]


def sd_hindleg(P, s):
    h = HIND[s]
    pts = hind_pts(s) + [np.array(h["paw"], float) + np.array([0.0, 0.10, 0.04])]
    d = np.minimum(chain_sdf(P, pts, list(HIND_R) + [0.16], HIND_SX), SD.sd_ellipsoid(P, h["paw"], HIND_PAW_R))
    return SD.smin(d, tufts_sdf(P, HIND_TUFTS, 1.0 if s == "L" else -1.0), TUFT_K)


def sd_haunch(P, s):
    return SD.sd_ellipsoid(P, HIND[s]["haunch"], HAUNCH_R)


# ---- chest ruff: ellipsoid + blunt tuft ring + bottom tuft (+ fuzz)
RC = np.array(RUFF_C, float); RR_ = np.array(RUFF_R, float)
def sd_ruff_base(P):
    return SD.smin(SD.sd_ellipsoid(P, RC, RR_), SD.sd_round_cone(P, *RUFF_V), 0.15)


_ys = np.arange(-2.6, -0.4, 0.005)
RUFF_TUFT_LIST = []
for j, (x0, z0, L_, lean) in enumerate(CHEST_LOCKS):
    for sg in ((1.0, -1.0) if x0 > 1e-6 else (1.0,)):
        x = sg * x0
        dd = sd_ruff_base(np.stack([np.full_like(_ys, x), _ys, np.full_like(_ys, z0)], 1))
        yf = float(_ys[np.argmax(dd < 0)]) if np.any(dd < 0) else float(RC[1])   # the bib's front surface at (x, z)
        root = np.array([x, yf + 0.06, z0])
        d = unit(np.array([sg * lean, -0.25, -1.0]))
        nrm = unit(np.array([0.35 * sg * x0 / RR_[0], -1.0, 0.0]))          # the chest normal the lock lies against
        RUFF_TUFT_LIST.append((root, root + d * L_ * (0.92 + 0.16 * FP.hash01(j, 2.1)), nrm))


def fuzz(P):
    k = 2 * math.pi / RUFF_FUZZ[1]
    return RUFF_FUZZ[0] * (np.sin(k * P[:, 0] + 1.3) * np.sin(k * P[:, 1] + 0.7) * np.sin(k * P[:, 2] + 2.1)
                           + 0.5 * np.sin(1.7 * k * P[:, 0] + 0.2 * k * P[:, 2]) * np.sin(1.9 * k * P[:, 1] + 0.4))


def sd_ruff(P, with_fuzz=True):
    d = sd_ruff_base(P)
    for a, b, nrm in RUFF_TUFT_LIST:
        d = SD.smin(d, sd_flame(P, a, b, CHEST_LOCK_R[0], CHEST_LOCK_R[1], nrm, CHEST_LOCK_FLAT), CHEST_LOCK_K)
    return d - fuzz(P) if with_fuzz else d


# ---- ears: flattened round-cone chains in each ear's own frame (copied from winged_cat)
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


EAR_KNOTS = (0.0, 0.25, 0.5, 0.7, 0.85, 0.95, 1.0)


def sd_ear_solid(P, s):
    e = EAR[s]
    u, v, w = ear_local(P, s)
    Q = np.stack([u, v, w / EAR_FLAT], 1)
    d = np.full(len(P), 1e9)
    for t0, t1 in zip(EAR_KNOTS[:-1], EAR_KNOTS[1:]):
        d = np.minimum(d, SD.sd_round_cone(Q, (0, t0 * e["len"], 0), (0, t1 * e["len"], 0), float(ear_r(t0)), float(ear_r(t1))))
    return d * EAR_FLAT


def sd_ear(P, s):
    """the solid ear minus the front hollow (a narrower copy pushed forward through the front face): concave inner ear"""
    e = EAR[s]
    u, v, w = ear_local(P, s)
    Q = np.stack([u, v, w / EAR_FLAT], 1)
    cs, co, c0, c1 = EAR_CUP
    ks = [c0] + [k_ for k_ in EAR_KNOTS if c0 < k_ < c1] + [c1]
    h = np.full(len(P), 1e9)
    for t0, t1 in zip(ks[:-1], ks[1:]):
        h = np.minimum(h, SD.sd_round_cone(Q, (0, t0 * e["len"], co * float(ear_r(t0))), (0, t1 * e["len"], co * float(ear_r(t1))),
                                           cs * float(ear_r(t0)), cs * float(ear_r(t1))))
    return np.maximum(sd_ear_solid(P, s), -h * EAR_FLAT)


def ear_inner_field(P, s):
    """> 0 on the blush inner ear: the hollow's surface = shell points INSIDE the solid (uncarved) ear; the rim stays fur"""
    return -sd_ear_solid(P, s) - EAR_INNER_TAU


# ---- tail: plume chain (X-flattened) + lobes + inner wisp
TK = np.array(TAIL_KNOTS, float)
TAIL_CURVE = SD.catmull(TK, 8)
_tal = SD.arclen(TAIL_CURVE)
TAIL_LEN = float(_tal[-1])
_kal = np.array([_tal[np.argmin(np.linalg.norm(TAIL_CURVE - k, axis=1))] for k in TK])


def tail_r(s_arc):
    return np.interp(s_arc, _kal, TAIL_RAD)


TAIL_LOBE_LIST = []
for fr, sz, side in TAIL_LOBES:
    sa = fr * TAIL_LEN
    p = np.array([np.interp(sa, _tal, TAIL_CURVE[:, k]) for k in range(3)])
    i_ = min(int(np.searchsorted(_tal, sa)), len(TAIL_CURVE) - 1)
    tng = unit(TAIL_CURVE[min(i_ + 1, len(TAIL_CURVE) - 1)] - TAIL_CURVE[max(i_ - 1, 0)])
    out = unit(np.cross(tng, [1.0, 0.0, 0.0]))                # in the YZ plane, perpendicular to the curve
    if out[1] < 0:
        out = -out                                            # toward the back (+Y): the plume's outer edge
    r_ = float(tail_r(sa))
    c = p + out * 0.45 * r_ + np.array([side * 0.25 * r_ * TAIL_SX, 0.0, 0.0])
    TAIL_LOBE_LIST.append((c, np.array([sz * r_ * TAIL_SX, sz * r_ * 0.9, sz * r_ * 1.3]), tng))


def sd_tail(P):
    Q = P.copy(); Q[:, 0] = Q[:, 0] / TAIL_SX
    d = np.full(len(P), 1e9)
    for i in range(len(TK) - 1):
        a, b = TK[i].copy(), TK[i + 1].copy()
        d = SD.smin(d, SD.sd_round_cone(Q, a, b, TAIL_RAD[i], TAIL_RAD[i + 1]), 0.04)
    for kn, rr in ([(TAIL_WISP, TAIL_WISP_R)] if TAIL_WISP else []) + list(TAIL_LOCKS):
        kq = [np.array([k_[0] / TAIL_SX, k_[1], k_[2]]) for k_ in kn]
        for i in range(len(kq) - 1):
            d = SD.smin(d, SD.sd_round_cone(Q, kq[i], kq[i + 1], rr[i], rr[i + 1]), 0.06)
    d = d * TAIL_SX
    for c, r3, tng in TAIL_LOBE_LIST:
        e3 = unit(np.cross([1.0, 0.0, 0.0], tng))
        fr_ = np.array([[1.0, 0.0, 0.0], e3, tng])
        d = SD.smin(d, SD.sd_ellipsoid(P, c, r3, frame=fr_), 0.06)
    return d


GROUPS = ["torso", "ruff", "neck", "head", "muzzle", "cheek.L", "cheek.R", "ear.L", "ear.R", "haunch.L", "haunch.R",
          "foreleg.L", "foreleg.R", "hindleg.L", "hindleg.R", "tail"]


def group_sdf(P, with_fuzz=True):
    cols = []
    for g_ in GROUPS:
        sd_side = g_[-1] if "." in g_ else None
        sg = 1.0 if sd_side == "L" else -1.0
        if g_ == "torso":
            d = sd_torso(P)
        elif g_ == "ruff":
            d = sd_ruff(P, with_fuzz)
        elif g_ == "neck":
            d = sd_neck(P)
        elif g_ == "head":
            d = sd_head(P)
        elif g_ == "muzzle":
            d = sd_muzzle(P)
        elif g_.startswith("cheek."):
            d = sd_cheek(P, sg)
        elif g_.startswith("ear."):
            d = sd_ear(P, sd_side)
        elif g_.startswith("haunch."):
            d = sd_haunch(P, sd_side)
        elif g_.startswith("foreleg."):
            d = sd_foreleg(P, sg)
        elif g_.startswith("hindleg."):
            d = sd_hindleg(P, sd_side)
        else:
            d = sd_tail(P)
        cols.append(d)
    return np.stack(cols, 1)


def ell_box(c, r, pad=0.0):
    c = np.asarray(c, float); r = np.asarray(r, float)
    return c - r - pad, c + r + pad


def pts_box(pts, R, pad=0.0):
    A = np.array(pts, float)
    return A.min(0) - R - pad, A.max(0) + R + pad


GRID_LO, GRID_HI = (-1.35, -2.40, -0.05), (1.35, 2.15, 6.40)
g = SD.Grid(GRID_LO, GRID_HI, SDF_STEP, SDF_BAND)
g.apply(sd_torso, *pts_box([TORSO[0], TORSO[1]], max(TORSO[2], TORSO[3])), 0.0)
for s, sg in SIDES:
    g.apply(lambda P, s=s: sd_haunch(P, s), *ell_box(HIND[s]["haunch"], HAUNCH_R), HAUNCH_K)
    h = HIND[s]
    g.apply(lambda P, s=s: sd_hindleg(P, s), *pts_box([h["hip"], h["stifle"], h["hock"], h["paw"]], max(HIND_PAW_R) + 0.05),
            LEG_K)
    g.apply(lambda P, sg=sg: sd_foreleg(P, sg), *pts_box(fore_pts(sg) + [mir(FORE_PAW_C, sg)], max(FORE_PAW_R) + 0.05), LEG_K)
g.apply(sd_tail, *pts_box(list(TAIL_KNOTS) + list(TAIL_WISP) + [k_ for kn, _ in TAIL_LOCKS for k_ in kn], max(TAIL_RAD) * 1.6),
        TAIL_K)
g.apply(sd_neck, *pts_box([NECK[0], NECK[1]], NECK[2]), NECK_K)
_rf_hi = 1.9
g.apply(sd_ruff, RC - _rf_hi, RC + _rf_hi, RUFF_K)
g.apply(sd_head, *ell_box(HEAD_C, HEAD_R), HEAD_K)
g.apply(sd_muzzle, *pts_box([MUZZLE[0], MUZZLE[1]], MUZZLE[2] * MUZZLE_SX), MUZZLE_K)
for s, sg in SIDES:
    g.apply(lambda P, sg=sg: sd_cheek(P, sg), *pts_box([mir(CHEEK[0], sg), mir(CHEEK[1], sg)], CHEEK[2]), CHEEK_K)
    e = EAR[s]
    g.apply(lambda P, s=s: sd_ear(P, s), np.minimum(e["base"], e["tip"]) - EAR_R[0], np.maximum(e["base"], e["tip"]) + EAR_R[0],
            EAR_K)
TOE_CREASES = []
for s, sg in SIDES:                                    # toe creases across each paw front
    for c_, r_ in ((mir(FORE_PAW_C, sg), np.array(FORE_PAW_R)), (np.array(HIND[s]["paw"], float), np.array(HIND_PAW_R))):
        for dx in CREASES:
            x_ = c_[0] + dx * r_[0] / 0.19
            c0 = np.array([x_, c_[1] - 0.35 * r_[1], c_[2] + 0.95 * r_[2]])
            c1 = np.array([x_, c_[1] - 1.05 * r_[1], 0.0])
            TOE_CREASES.append((c0, c1))
            g.apply(lambda P, c0=c0, c1=c1: SD.sd_round_cone(P, c0, c1, CREASE_R, CREASE_R),
                    np.minimum(c0, c1) - CREASE_R, np.maximum(c0, c1) + CREASE_R, CREASE_K, mode="subtract")
g.floor(0.0)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
assert np.all(HV.min(0) > np.array(GRID_LO) + SDF_STEP) and np.all(HV.max(0)[:2] < np.array(GRID_HI)[:2] - SDF_STEP),     ("the shell touches the SDF grid box (an open cut)", HV.min(0), HV.max(0))
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "toe_creases": len(TOE_CREASES), "ruff_tufts": len(RUFF_TUFT_LIST),
                 "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
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


def mirror_halve(V, F):
    """SYMMETRY (artist house rule): marching tets (Kuhn diagonal) + collapse decimation are not mirror-exact, so the low
    shell keeps its X >= 0 half (bisected at X = 0) and gets the mirrored copy, welded on the seam. Closed by construction."""
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


LV, LF, SEAM_N = mirror_halve(LV, LF)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "mirror_halved": {"seam_verts": SEAM_N, "rule": "X >= 0 half + its mirror (symmetric rest mesh)"},
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
_own = np.argmin(DGR, 1)
_ruff_v = LV[_own == GROUPS.index("ruff")]
_front = _ruff_v[np.abs(_ruff_v[:, 0]) < 0.25]
report["chest_fur"] = {"bottom_z": round(float(_front[:, 2].min()), 4), "v1_bottom_z": 2.23, "locks": len(RUFF_TUFT_LIST),
                       "rule": "lowest ruff-owned shell point within |X| < 0.25 (between the front legs)"}
_tv = (_own == GROUPS.index("tail")) & (LV[:, 2] < 2.0)          # below the rump: the root itself joins the body
_legs = [GROUPS.index(g_) for g_ in ("hindleg.L", "hindleg.R", "haunch.L", "haunch.R")]
_tg = DGR[_tv][:, _legs]
TAIL_GAP = float(_tg.min())
_ti = np.unravel_index(np.argmin(_tg), _tg.shape)
TAIL_GAP_AT = {"point": LV[_tv][_ti[0]].round(3).tolist(), "group": GROUPS[_legs[_ti[1]]]}
report["tail_leg_clearance"] = {"min_gap": round(TAIL_GAP, 4), "at": TAIL_GAP_AT, "need": "> TAIL_K / 2 = %.3f (no smooth-union bridge)" % (TAIL_K / 2),
                                "rule": "tail-owned shell points below z 2.0 (under the rump) -> nearest hind leg / haunch"}
print("CHEST", json.dumps(report["chest_fur"]), "TAIL_GAP", round(TAIL_GAP, 4))
assert TAIL_GAP > TAIL_K / 2, ("the tail would weld to a hind leg", TAIL_GAP, TAIL_GAP_AT)
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
CUTS = [("inner", 0.0, on_ear)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array(["fur"] * len(CF), dtype=object)
is_ear = np.isin(owner, [GROUPS.index("ear.L"), GROUPS.index("ear.R")])
shell_reg[is_ear & (FVAL["inner"] > 0)] = "ear_inner"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: eyes, irises, pupils, nose, gem, mane
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
    S, _ = cast((x, -4.0, z), (0, 1, 0))
    ns = [cast((x + dx, -4.0, z + dz), (0, 1, 0))[1] for dx, dz in ((0, 0), (0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03))]
    return S, unit(np.mean(ns, 0))


def iris_outline(cx, segs=28):
    """the iris = the almond eye opening INTERSECTED with a circle (radius IRIS_RC, centre (cx, 0)): convex, so star-shaped
    about the eye anchor; sampled by bisection along rays from the anchor"""
    def inside(x, y):
        if abs(x) >= EYE_A:
            return False
        ymax = EYE_B * (1.0 - (x / EYE_A) ** 2) ** ((1.0 + EYE_SHARP) / 2.0)
        return abs(y) <= ymax and (x - cx) ** 2 + y * y <= IRIS_RC ** 2
    out = []
    for k in range(segs):
        t = 2 * math.pi * k / segs
        lo_, hi_ = 0.0, EYE_A + IRIS_RC
        for _ in range(30):
            m_ = 0.5 * (lo_ + hi_)
            lo_, hi_ = (m_, hi_) if inside(m_ * math.cos(t), m_ * math.sin(t)) else (lo_, m_)
        out.append((0.985 * lo_ * math.cos(t), 0.985 * lo_ * math.sin(t)))   # a hair inside the white's rim
    return np.array(out)


def mirror_part(p_, name):
    V_ = p_.V.copy(); V_[:, 0] *= -1.0
    q_ = FP.Part(name, V_, [f_[::-1] for f_ in p_.F], p_.R, bone=p_.bone)
    return q_


PARTS = []
FACE = {}
for s, sg in SIDES[:1]:                                      # L only; R = the mirror (symmetric rest mesh)
    S, n = front_anchor(sg * EYE_X, EYE_Z)
    ne = unit(n + EYE_FACE * np.array([0.0, -1.0, 0.0]))
    tilt = math.radians(EYE_TILT) * sg                       # outer corner up
    up = (-math.sin(tilt), 0.0, math.cos(tilt))
    O = FP.lens_curve(EYE_A, EYE_B, 24, EYE_SHARP)
    V, F, R = FP.polar_decal(S, ne, O, project, DECAL_UP, DECAL_DN, DECAL_DOME, 3, "eye_white", up_hint=up)
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    Si, _ = front_anchor(sg * (EYE_X - IRIS_IN), EYE_Z)
    Oi = iris_outline(-sg * IRIS_IN)                         # decal x (e2) points outward for both eyes: the nose is -sg
    V, F, R = FP.polar_decal(S, ne, Oi, project, DECAL_UP + DECAL_DOME + 0.004, DECAL_DN, 0.008, 3, "iris", up_hint=up)
    PARTS.append(FP.Part("iris." + s, V, F, R, bone="head"))
    Op = FP.lens_curve(PUPIL_R[0], PUPIL_R[1], 16, 0.0)
    V, F, R = FP.polar_decal(Si, ne, Op, project, DECAL_UP + DECAL_DOME + 0.016, DECAL_DN, 0.006, 2, "pupil", up_hint=up)
    PARTS.append(FP.Part("pupil." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": ne.round(4).tolist(), "iris": Si.round(4).tolist()}
for p_ in [p_ for p_ in PARTS if p_.name.endswith(".L")]:
    PARTS.append(mirror_part(p_, p_.name[:-2] + ".R"))
Sn, nn = front_anchor(0.0, NOSE_Z)
nn = unit(nn + 0.5 * np.array([0.0, -1.0, 0.0]))
V, F, R = FP.polar_decal(Sn, nn, FP.lens_curve(NOSE_R[0], NOSE_R[1], 20, 0.15), project, DECAL_UP, DECAL_DN, 0.03, 3,
                         "nose", up_hint=(0, 0, 1))
PARTS.append(FP.Part("nose", V, F, R, bone="head"))
FACE["nose"] = {"surface": Sn.round(4).tolist(), "normal": nn.round(4).tolist()}

# ---- forehead gem
_gl = unit(GEM_LIGHT)


def gem_tone(n_):
    """facets turned toward the light (vs the inlay's own centre normal) are lit: the sheet's kite, one side lit"""
    return "gem_lit" if float(n_ @ _gl) > float(GEM_PTS["F"][1] @ _gl) else "gem_shade"


GEM_PTS = {}
for k_, (gx, gz) in GEM_KITE.items():
    GEM_PTS[k_] = front_anchor(gx, gz)                       # (surface point, smoothed normal) on the forehead
_rim = []
for k0, k1 in (("T", "Lc"), ("Lc", "B"), ("B", "Rc"), ("Rc", "T")):  # 8-point rim: the kite corners + edge midpoints, all
    _rim.append(GEM_PTS[k0])                                 #   cast onto the forehead (the facets follow its curve)
    _rim.append(front_anchor(0.5 * (GEM_KITE[k0][0] + GEM_KITE[k1][0]), 0.5 * (GEM_KITE[k0][1] + GEM_KITE[k1][1])))
V, F, R = FP.gem_inlay(_rim, GEM_PTS["F"], GEM_PROUD, GEM_SINK, gem_tone)
PARTS.append(FP.Part("gem", V, F, R, bone="head"))
FACE["gem"] = {k_: v_[0].round(4).tolist() for k_, v_ in GEM_PTS.items()}

# ---- crystal mane
_cl = unit(CRYSTAL_LIGHT)


def crystal_tone(n_):
    d = float(n_ @ _cl)
    return "crystal_lit" if d > CRYSTAL_TONE_T[0] else ("crystal_shade" if d < CRYSTAL_TONE_T[1] else "crystal")


MANE_INFO = []
for s, sg in SIDES[:1]:                                      # L only; R = the mirror
    for i, (root, tip, rad) in enumerate(MANE):
        b_, t_ = mir(root, sg), mir(tip, sg)
        ax = unit(t_ - b_)
        fan_n = np.cross(ax, [0.0, 0.0, 1.0])
        if np.linalg.norm(fan_n) < 1e-3:
            fan_n = np.array([0.0, 1.0, 0.0])
        spin = 0.0
        V, F, R, _ = FP.crystal_prism(b_, t_, rad * MANE_RSCALE, MANE_SQUASH, MANE_TIP, MANE_BURY, spin, MANE_TIP_OFF * sg, MANE_BULGE,
                                      crystal_tone, unit(fan_n))
        PARTS.append(FP.Part("crystal.%s.%02d" % (s, i + 1), V, F, R, bone="spine.003"))
        MANE_INFO.append({"name": "crystal.%s.%02d" % (s, i + 1), "root": b_.round(3).tolist(), "tip": t_.round(3).tolist(),
                          "length": round(float(np.linalg.norm(t_ - b_)), 3), "radius": round(rad * MANE_RSCALE, 4)})
for p_ in [p_ for p_ in PARTS if p_.name.startswith("crystal.L.")]:
    PARTS.append(mirror_part(p_, p_.name.replace("crystal.L.", "crystal.R.")))

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
_below = [i[0] for i in ISL if i[1][:, 2].min() < -1e-3]
assert lo0[2] >= -1e-3 and not _below, "a part dips below the floor (z %.4f, %s)" % (lo0[2], _below)   # floor = SDF cut
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
REG = ["fur", "ear_inner", "eye_white", "iris", "pupil", "nose", "gem_lit", "gem_shade", "crystal_lit", "crystal",
       "crystal_shade"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (must never borrow a neighbour's field): %s" % _empty


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
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)
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

# facing landmark: the head centre -> the nose decal on the midline
HEAD_CTR = np.array(HEAD_C, float)
anchor = HEAD_CTR - SHIFT
landmark = Sn - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the nose (front cast on the midline at NOSE_Z)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["mane"] = {"per_side": len(MANE), "total": 2 * len(MANE), "crystals": MANE_INFO}

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

_bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mir = np.array([_bvh_all.find_nearest(Vector((-p[0], p[1], p[2])))[3] for p in VALL])
report["mirror_test"] = {"max_abs_asymmetry": round(float(_mir.max()), 5), "p99": round(float(np.percentile(_mir, 99)), 5),
                         "per_piece_max": {k_: round(float(_mir[v_[0]:v_[1]].max()), 4) for k_, v_ in RANGE.items()
                                           if float(_mir[v_[0]:v_[1]].max()) > 0.01},
                         "rule": "every rest vertex mirrored across X = 0 -> distance to the nearest point of the mesh"}
print("MIRROR", json.dumps(report["mirror_test"]))
tris_main = tri_count_F(FALL)
_part_tris = {}
for p in PARTS:
    key = {"eye": "eye_whites", "iris": "irises", "pupil": "pupils", "nose": "nose", "gem": "gem",
           "crystal": "mane_crystals"}[p.name.split(".")[0]]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The shell carries the head, the large ears, the "
                            "ruff tufts, four slim legs + paws and the plume tail; the crystal mane is 24 faceted prisms "
                            "(low-poly by design: facets ARE the look); the bake carries the ruff fuzz." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, decals, gem, crystal prisms)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"front_view_height_px": 623, "front_view_width_px": 398, "side_view_height_px": 621,
                               "side_view_length_px": 415},
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
low["conquest_source"] = "from scratch: design/reference/crystal_canine-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_mane_pts = np.vstack([p.V for p in PARTS if p.name.startswith("crystal.L")]) - SHIFT
_gem_pts = PARTS[[p.name for p in PARTS].index("gem")].V - SHIFT
_tail_lo = np.array([-0.6, 0.3, 0.0]) - SHIFT; _tail_hi = np.array([0.6, 2.0, 3.2]) - SHIFT
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.85, -2.25, 4.30]) - SHIFT).tolist(), (np.array([0.85, -0.70, 5.70]) - SHIFT).tolist()],
    "gem": [(_gem_pts.min(0) - 0.15).tolist(), (_gem_pts.max(0) + 0.15).tolist()],
    "mane": [_mane_pts.min(0).tolist(), _mane_pts.max(0).tolist()],
    "tail": [_tail_lo.tolist(), _tail_hi.tolist()],
    "views": {"face": [-18.0, 6.0], "gem": [-25.0, 22.0], "mane": [60.0, 12.0], "tail": [125.0, 8.0]},
    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges", "mirror_test",
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
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets, the ruff fuzz at full density) + every closed part",
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


SPINE_KNOTS = [(0.0, 0.95, 2.80), (0.0, 0.20, 2.85), (0.0, -0.50, 2.95), (0.0, -1.05, 3.30)]   # spine.001 -> .003
NECK_PTS = ((0.0, -1.05, 3.30), (0.0, -1.12, 4.45))
HEAD_PTS = ((0.0, -1.12, 4.45), (0.0, -1.20, 5.55))
BONES = [("spine.001", P_(SPINE_KNOTS[0]), P_(SPINE_KNOTS[1]), "root"),
         ("spine.002", P_(SPINE_KNOTS[1]), P_(SPINE_KNOTS[2]), "spine.001"),
         ("spine.003", P_(SPINE_KNOTS[2]), P_(SPINE_KNOTS[3]), "spine.002"),
         ("neck", P_(NECK_PTS[0]), P_(NECK_PTS[1]), "spine.003"),
         ("head", P_(HEAD_PTS[0]), P_(HEAD_PTS[1]), "neck")]
for s, sg in SIDES:
    BONES.append(("ear_" + s, P_(EAR[s]["base"] + EAR[s]["axis"] * 0.15), P_(EAR[s]["tip"]), "head"))
LEGS = {}                                             # leg -> {"pts": [top, knee, ankle, toe] (model space), "parent"}
for s, sg in SIDES:
    LEGS["foreleg_" + s] = {"pts": fore_pts(sg) + [mir(FORE_TOE, sg)], "parent": "spine.003", "group": "foreleg." + s,
                            "haunch": None}
    h = HIND[s]
    LEGS["hindleg_" + s] = {"pts": hind_pts(s) + [np.array(h["toe"], float)], "parent": "spine.001", "group": "hindleg." + s,
                            "haunch": "haunch." + s}
for lg, L_ in LEGS.items():
    pts = L_["pts"]
    BONES += [(lg + ".upper", P_(pts[0]), P_(pts[1]), L_["parent"]), (lg + ".lower", P_(pts[1]), P_(pts[2]), lg + ".upper"),
              (lg + ".paw", P_(pts[2]), P_(pts[3]), lg + ".lower")]
TAIL_BONE_PTS = [np.array([np.interp(x, _tal, TAIL_CURVE[:, k]) for k in range(3)]) for x in np.linspace(0, TAIL_LEN, 5)]
for k in range(4):
    BONES.append(("tail.%03d" % (k + 1), P_(TAIL_BONE_PTS[k]), P_(TAIL_BONE_PTS[k + 1]),
                  "spine.001" if k == 0 else "tail.%03d" % k))
CONNECT = {"spine.002", "spine.003", "neck", "head", "tail.002", "tail.003", "tail.004"} | \
          {lg + sfx for lg in LEGS for sfx in (".lower", ".paw")}
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
# torso: spine.001-.003 hat weights along the spine polyline
_, s_sp, _ = FP.spine_param(Pw, np.array(SPINE_KNOTS))
L_sp = float(SD.arclen(np.array(SPINE_KNOTS))[-1])
Wv = K.vine_weights(np.clip(s_sp, 0, L_sp), L_sp, 3)
gi = GROUPS.index("torso")
Ws[:, J["spine.001"]] += Wg[:, gi] * (Wv[:, 0] + Wv[:, 1])
Ws[:, J["spine.002"]] += Wg[:, gi] * Wv[:, 2]
Ws[:, J["spine.003"]] += Wg[:, gi] * Wv[:, 3]
# neck: spine.003 -> neck -> head by z bands; ruff: spine.003 -> neck
wn = smoothstep(NECK_BAND[0], NECK_BAND[1], zz)
whd = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
gi = GROUPS.index("neck")
Ws[:, J["spine.003"]] += Wg[:, gi] * (1 - wn)
Ws[:, J["neck"]] += Wg[:, gi] * wn * (1 - whd)
Ws[:, J["head"]] += Wg[:, gi] * wn * whd
wr = smoothstep(RUFF_BAND[0], RUFF_BAND[1], zz)
gi = GROUPS.index("ruff")
Ws[:, J["spine.003"]] += Wg[:, gi] * (1 - wr)
Ws[:, J["neck"]] += Wg[:, gi] * wr
for gname in ("head", "muzzle", "cheek.L", "cheek.R"):
    Ws[:, J["head"]] += Wg[:, GROUPS.index(gname)]
for s, sg in SIDES:
    gi = GROUPS.index("ear." + s)
    _, v_, _ = ear_local(Pw, s)
    we = smoothstep(EAR_BLEND[0], EAR_BLEND[1], v_ / EAR[s]["len"])
    Ws[:, J["ear_" + s]] += Wg[:, gi] * we
    Ws[:, J["head"]] += Wg[:, gi] * (1 - we)


def leg_weights(P, lg):
    """-> (n, 4) weights [parent, upper, lower, paw] by the arc parameter on the leg polyline extended 0.5 above its top:
    joint blends of half width LEG_BLEND[0] at the top / knee, the paw blend only ABOVE the ankle (the whole paw rides
    the paw bone rigidly: planted paws keep their soles on the floor)"""
    pts = [np.asarray(p, float) for p in LEGS[lg]["pts"]]
    poly = np.array([pts[0] + np.array([0.0, 0.0, 0.5])] + pts)
    _, s_, _ = FP.spine_param(P, poly)
    al = SD.arclen(poly)
    s0, s1, s2 = al[1], al[2], al[3]
    b, b2 = LEG_BLEND
    w_up = smoothstep(s0 - b, s0 + b, s_)
    w_lo = smoothstep(s1 - b, s1 + b, s_)
    w_pw = smoothstep(s2 - b2, s2, s_)
    return np.stack([1 - w_up, w_up - w_lo, w_lo - w_pw, w_pw], 1)


for lg, L_ in LEGS.items():
    WL = leg_weights(Pw, lg)
    for gname in [L_["group"]] + ([L_["haunch"]] if L_["haunch"] else []):
        gi = GROUPS.index(gname)
        Ws[:, J[L_["parent"]]] += Wg[:, gi] * WL[:, 0]
        for k, sfx in enumerate((".upper", ".lower", ".paw")):
            Ws[:, J[lg + sfx]] += Wg[:, gi] * WL[:, k + 1]
gi = GROUPS.index("tail")
_, s_arc, _ = FP.spine_param(Pw, np.array(TAIL_BONE_PTS))
Wv = K.vine_weights(np.clip(s_arc, 0, TAIL_LEN), TAIL_LEN, 4)
Ws[:, J["spine.001"]] += Wg[:, gi] * Wv[:, 0]
for k in range(4):
    Ws[:, J["tail.%03d" % (k + 1)]] += Wg[:, gi] * Wv[:, k + 1]
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between torso, ruff, neck, head, muzzle, cheeks, ears, "
                          "haunches, four legs, tail; torso spine.001-.003 hat weights along the spine; neck spine.003 -> "
                          "neck -> head by z bands; ruff spine.003 -> neck; ears blend from the head along the ear axis; "
                          "legs + haunches: parent -> upper -> lower -> paw by the leg-chain arc (the paw rigid below "
                          "the ankle); tail: rigkit.vine_weights along tail.001-.004 (parent spine.001); eyes, irises, "
                          "pupils, nose, gem rigid on the head; the 24 mane crystals rigid on spine.003" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics). Per frame: the trunk / head / ears / tail are posed,
#      then every leg is solved by a planar (YZ) 2-bone IK to its paw target (rest + the clip's paw offset), the paw bone
#      keeping its world pitch; a floor guard lifts the whole body + targets if any vertex dips below z 0 (pass 1);
#      keyed from the stored channels (pass 2, frame N+1 = frame 1); measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
REST_T = {b.name: np.array(b.tail_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM
LEG_BONES = {lg + sfx for lg in LEGS for sfx in (".upper", ".lower", ".paw")}


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


def ang2(v):
    return math.atan2(v[2], v[1])                  # pitch angle in the YZ plane (a +X rotation increases it)


def rot2(v, a):
    c, s_ = math.cos(a), math.sin(a)
    return np.array([0.0, v[1] * c - v[2] * s_, v[1] * s_ + v[2] * c])


LEG_REST = {}
for lg in LEGS:
    H0, K0, A0 = REST_H[lg + ".upper"], REST_T[lg + ".upper"], REST_T[lg + ".lower"]
    yz = lambda v: np.array([0.0, v[1], v[2]])
    l1 = float(np.linalg.norm(yz(K0 - H0))); l2 = float(np.linalg.norm(yz(A0 - K0)))
    cr = (A0 - H0)[1] * (K0 - H0)[2] - (A0 - H0)[2] * (K0 - H0)[1]
    LEG_REST[lg] = {"H0": H0, "K0": K0, "A0": A0, "l1": l1, "l2": l2, "bend": 1.0 if cr > 0 else -1.0}


def posed_pitch(bn):
    pb = rig.pose.bones[bn]
    Rd = np.array(pb.matrix)[:3, :3] @ REST[bn].T
    return math.atan2(Rd[2, 1], Rd[1, 1])


def solve_legs(legs, guard_z):
    """legs: {leg: (dy, lift, paw_pitch_deg)} -> sets the three leg bones of every leg (planar 2-bone IK). Returns the
    worst IK reach shortfall."""
    short = 0.0
    for lg, LR in LEG_REST.items():
        dy, lift, pp = legs.get(lg, (0.0, 0.0, 0.0))
        pbu = rig.pose.bones[lg + ".upper"]
        Hh = np.array(pbu.head)                             # armature space == world (rig at identity)
        par = posed_pitch(LEGS[lg]["parent"])
        A = LR["A0"] + np.array([0.0, dy, lift + guard_z])
        d_ = np.array([0.0, A[1] - Hh[1], A[2] - Hh[2]])
        dist = float(np.linalg.norm(d_))
        dmin, dmax = abs(LR["l1"] - LR["l2"]) + 1e-4, LR["l1"] + LR["l2"] - 1e-4
        short = max(short, dist - dmax, dmin - dist)
        dc = min(max(dist, dmin), dmax)
        e_ = d_ / max(dist, 1e-9)
        ca = (LR["l1"] ** 2 + dc ** 2 - LR["l2"] ** 2) / (2 * LR["l1"] * dc)
        al = math.acos(max(-1.0, min(1.0, ca)))
        Kn = Hh + LR["l1"] * rot2(e_, LR["bend"] * al)
        An = Hh + e_ * dc
        t1 = ang2(Kn - Hh) - ang2(LR["K0"] - LR["H0"])
        t2 = ang2(An - Kn) - ang2(LR["A0"] - LR["K0"])
        t3 = math.radians(pp)
        rig.pose.bones[lg + ".upper"].rotation_quaternion = local_quat(lg + ".upper", [("X", math.degrees(t1 - par))])
        rig.pose.bones[lg + ".lower"].rotation_quaternion = local_quat(lg + ".lower", [("X", math.degrees(t2 - t1))])
        rig.pose.bones[lg + ".paw"].rotation_quaternion = local_quat(lg + ".paw", [("X", math.degrees(t3 - t2))])
    return short


def tail_pose(P, u, spec, phase0):
    amps, cyc, lag = spec[:4], spec[4], spec[5]
    for k in range(4):
        ang = amps[k] * math.sin(TAU * cyc * u + phase0 - k * lag)
        P["tail.%03d" % (k + 1)] = (None, [("Z", ang)], None)


def pulse(u, u0, n):
    return (0.5 + 0.5 * math.cos(TAU * (u - u0))) ** n     # a narrow periodic bump (integer harmonics)


def idle_pose(u):
    P = {}
    br = math.sin(TAU * 2 * u)
    P["spine.002"] = (None, [("X", -0.5 * BREATH * br)], None)   # the chest rises (pitch about the mid back)
    P["spine.003"] = (None, [("X", -0.5 * BREATH * br)], None)
    P["neck"] = (None, [("X", BREATH * br)], None)                # the head stays level
    P["head"] = (None, [("X", IDLE_HEAD[0] * math.sin(TAU * 2 * u - 0.9)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u + 0.3))], None)
    for s, sg in SIDES:
        u0 = EAR_TWITCH[1] if s == "L" else EAR_TWITCH[2]
        tw = EAR_TWITCH[0] * pulse(u, u0, EAR_TWITCH[3])
        P["ear_" + s] = (None, [(tuple(EAR[s]["axis"]), -sg * 0.4 * tw), (tuple(EAR[s]["w"]), tw)], None)   # flick
    tail_pose(P, u, TAIL_SWAY, 0.4)
    return P, {}, {"lift": 0.0}


def walk_paw(u, ph):
    """paw offset (dy, lift, pitch) at loop phase u for a leg of phase ph: stance (DUTY of the cycle) carries the planted
    paw back from -STRIDE/2 to +STRIDE/2 (forward = -Y), the swing returns it with a sine lift + toe curl"""
    p = (u - ph) % 1.0
    if p < DUTY:
        return (STRIDE * (p / DUTY - 0.5), 0.0, 0.0)
    q = (p - DUTY) / (1.0 - DUTY)
    sq = q * q * (3 - 2 * q)
    return (STRIDE * (0.5 - sq), LIFT * math.sin(math.pi * q), -PAW_CURL * math.sin(math.pi * q))


def walk_pose(u):
    """PLACEHOLDER 4-beat walk in place (the game glides the unit): lateral sequence LH, LF, RH, RF; stance paws planted
    on the floor and carried back at the stride speed, swing paws lifted; a small body bob, head counter-nod, tail sway."""
    P = {}
    bob = WALK_BOB * math.sin(TAU * 2 * u)
    P["spine.001"] = ((0.0, 0.0, bob), [], None)
    P["head"] = (None, [("X", -1.0 * math.sin(TAU * 2 * u + 0.6))], None)
    for s, sg in SIDES:
        P["ear_" + s] = (None, [("X", -1.5 * math.sin(TAU * 2 * u - 0.6))], None)
    tail_pose(P, u, WALK_TAIL, 1.0)
    legs = {lg: walk_paw(u, WALK_PHASE[lg]) for lg in LEGS}
    return P, legs, {"bob": bob}


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def apply_pose(P, legs, guard_z):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        if bn in LEG_BONES:
            pb.location = (0, 0, 0); pb.rotation_quaternion = Quaternion(); pb.scale = (1, 1, 1)
            continue
        loc, rots, sc = P.get(bn, (None, [], None))
        dw = np.zeros(3) if loc is None else np.asarray(loc, float)
        if bn == "spine.001":
            dw = dw + np.array([0.0, 0.0, guard_z])
        pb.location = local_loc(bn, dw)
        pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
        pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    short = solve_legs(legs, guard_z)
    bpy.context.view_layer.update()
    return short


def bake_clip(name, N, pose_fn):
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames, guard, info, shorts = [], [], [], []
    for f in range(N):
        P, legs, inf = pose_fn(f / N)
        gz = 0.0
        for it in range(6):
            sh_ = apply_pose(P, legs, gz)
            mz = float(eval_coords(low)[:, 2].min())
            if mz >= -1e-6:
                break
            gz += -mz + FLOOR_EPS
        frames.append({bn: (rig.pose.bones[bn].location.copy(), rig.pose.bones[bn].rotation_quaternion.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
        guard.append(gz); info.append(inf); shorts.append(sh_)
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
    return act, rows, guard, info, shorts


CLIPS = {"idle": (IDLE_N, idle_pose), "walk": (WALK_N, walk_pose)}
NEW_ACTS, key_rows, GUARD, INFO, SHORT = {}, [], {}, {}, {}
t_clip = time.time()
for cn, (N, fn) in CLIPS.items():
    NEW_ACTS[cn], rows, GUARD[cn], INFO[cn], SHORT[cn] = bake_clip(cn, N, fn)
    key_rows += rows
DIG["keys"] = sha(np.array(key_rows))
# sole vertices: dominant deform weight on a paw bone, below z 0.05 at rest (the planted-paw probe)
_paw_j = [J[lg + ".paw"] for lg in LEGS]
SOLE = np.nonzero(np.isin(np.argmax(Wt, 1), _paw_j) & (VALL[:, 2] < 0.05))[0]
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
    soles = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        soles.append(C[SOLE])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "spine_z": float(pbs["spine.001"].head.z),
                     "tail_tip": list(pbs["tail.004"].tail), "head_tail": list(pbs["head"].tail),
                     "ear_L": list(pbs["ear_L"].tail), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("spine_z") - REST_H["spine.001"][2]
    tt_ = np.array([r["tail_tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_]); el = np.array([r["ear_L"] for r in R_])
    S_ = np.array(soles[:-1])                              # (frames, sole verts, 3)
    planted = S_[:, :, 2] < 0.01
    slide = []
    for fi in range(len(S_) - 1):                          # planted in both frames: ground speed = the stride speed
        both = planted[fi] & planted[fi + 1]
        if both.any():
            dyv = S_[fi + 1, both, 1] - S_[fi, both, 1]
            slide.append(float(np.abs(dyv - (STRIDE / (DUTY * N) if cn == "walk" else 0.0)).max()))
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "spine_lift": [round(float(bz.min()), 5), round(float(bz.max()), 5)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "ik_reach_shortfall_max": round(float(max(SHORT[cn])), 5),
                    "sole_verts": int(len(SOLE)),
                    "planted_sole_slide_max_vs_ground": round(max(slide) if slide else 0.0, 5),
                    "sole_min_z": round(float(S_[:, :, 2].min()), 5),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist(),
                    "head_top_p2p_xyz": (hd.max(0) - hd.min(0)).round(4).tolist(),
                    "ear_L_tip_p2p_xyz": (el.max(0) - el.min(0)).round(4).tolist()}
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
rig["conquest_rig"] = ("crystal_canine v1: root > spine.001 > spine.002 > spine.003 > neck > head > ear_L / ear_R; "
                       "spine.003 > foreleg_{L,R}.upper > .lower > .paw; spine.001 > hindleg_{L,R}.upper > .lower > .paw; "
                       "spine.001 > tail.001 > .002 > .003 > .004")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s: breathing (chest "
                               "rise, head level), small head nod / look, one ear twitch per ear per loop, tail sway "
                               "(travelling yaw wave); paws held planted by a planar 2-bone IK. walk = 2 s 4-beat walk in "
                               "place (lateral sequence LH, LF, RH, RF; stance 75%%; stride %.2f, paw lift %.2f sheet units): "
                               "stance paws planted on the floor and carried back, swing paws lifted + curled; each leg "
                               "cycles about its own rest paw (symmetric rest stance). Paws never "
                               "below z 0, root still." % (STRIDE, LIFT))
low["conquest_locomotion"] = "PLACEHOLDER: 4-beat walk in place (movement intent open; the game glides the unit)"
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
              "structure": "armature identity + skinned 'crystal_canine' (one opaque body material); natural scale; "
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
