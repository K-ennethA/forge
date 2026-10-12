"""Eclipse Wolf -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/eclipse_wolf_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-11 "NEW UNITS x6: Lava Komodo, Spectormn, Halo Sentinel, Eclipse Wolf, Gemwing Dragon,
Mystic Snow Spirit (artist sheets)" (section ECLIPSE WOLF) + the sheet design/reference/eclipse_wolf-character-sheet.png
(intake: design/reference/eclipse_wolf/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT.
Structure copied from crystal_canine_build.py (the same-contract quadruped canine) + soot_puff_build.py (seeded puffs).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet (intake.json transforms):
  SIDE view:   Y = (x_px - 1010) / 100 ; z = (790 - y_px) / 100     (faces image-left = the unit's front, -Y)
  FRONT view:  X = (x_px - 218) / 100  ; z = (800 - y_px) / 100     (the unit's left, +X, on image right)
  BACK view:   X = -(x_px - 652) / 100 ; z = (790 - y_px) / 100     (the unit's right on image right)
X +ve = the unit's LEFT (L). Floor z = 0 at the paws.

A STANDING FOUR-LEGGED WOLF, built SYMMETRIC in rest pose (artist house rule 2026-10-10): four legs mirror-symmetric about
X = 0 (the side view's stride is pose), the FLEECE TAIL straight back and up (the front / back views swing it to opposite
sides), the side view's height kept.

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the deep-chested TORSO (round cone, pelvis -> chest),
     two HAUNCHES, four slim LEGS (oval round-cone chains, thick upper -> slim lower) with PAWS (toe creases carved), the
     NECK, the HEAD + MUZZLE + JAW + two CHEEK tufts per side, two POINTED EARS (cupped: a hollow on the front face), and
     the WOOLLY FLEECE MANTLE: three base volumes (the chest RUFF + its V bib, the BACK band, the TAIL ball + root) covered
     in SEEDED ROUND CLUMPS (two sizes, mirrored X >= 0 -> X < 0, each smooth-unioned in: soot_puff's language).
     Marching tets = the HIGH (bake source); collapse decimation = the LOW shell, mirror-halved (X >= 0 + its mirror).
  2. REGIONS on the low shell, every boundary iso-cut: FLEECE (mantle ownership) with lighter clump TOPS (fleece_lit: the
     mantle field's gradient points up), the dark MOUTH (the front view's grin outline, front-facing head only), the light
     INNER EAR (the cup's hollow).
  3. PARTS (eclipse_wolf_parts.py, closed): EYE whites + PUPILS + NOSE (conforming decals), the TEETH (15 triangular
     decals cast from the front, interlocking: the zigzag gaps show the dark mouth), two CRESCENT HORNS (bevelled hexagon
     sweeps, the inner-edge bevels glow), the dark ORB (UV sphere) + its glowing RING (torus), four glowing LEG BANDS (tori).
  4. palette (palettes.py regions -> Col / Glow; glow = horn_glow, orb_ring, band only), Smart UV, bake normal + AO from
     the SDF high + parts, seed 0.
  5. RIG: root > spine.001 > spine.002 > spine.003 > neck > head > ear_L / ear_R / horn_L / horn_R / orb; spine.003 >
     foreleg_{L,R}.upper > .lower > .paw; spine.001 > hindleg_{L,R}.upper > .lower > .paw; spine.001 > tail.001 > .002 >
     .003. Clips (PLACEHOLDER, movement intent open): idle (breathing + an ear twitch + a tail sway + an orb bob, paws planted
     by a planar 2-bone IK) + walk (a 4-beat walk in place: stance paws planted on the floor and carried back). glb.
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
import eclipse_wolf_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "eclipse_wolf"
CHAR_ID = "eclipse_wolf"              # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.18                       # >= the largest smooth-union k (0.12) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 14500                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
GRID_LO, GRID_HI = (-1.80, -2.00, -0.05), (1.80, 3.10, 5.70)   # the SDF grid box (asserted clear of the shell)
# ---- HEAD. SIDE view: crown (960-980, 290) (Y -0.4, z 5.0), back of the skull x 1025-1035 @ y 330-360 (Y +0.2), chin /
#   jaw line y 420-430 @ x 900-950 (z 3.6-3.7). FRONT view: crown between the ears y 305 (z 4.95), face outline at the eyes
#   x 145-290 (+-0.73), at the grin corners y 400 x 150-285 (+-0.67: the face is wide low down)
HEAD_C, HEAD_R, HEAD_K = (0.0, -0.50, 4.30), (0.72, 0.62, 0.60), 0.10   # "head size" (half width / depth / height)
# ---- MUZZLE: SIDE view nose tip (865, 395) (Y -1.45, z 3.95), the stop (905, 350) (Y -1.05, z 4.40)
MUZZLE = ((0.0, -0.92, 4.05), (0.0, -1.33, 3.97), 0.27, 0.10)   # (a, b, r_a, r_b)
MUZZLE_SX, MUZZLE_K = 1.45, 0.10      # "muzzle width" (X stretch)
# ---- JAW: FRONT view the dark chin V under the grin to (218, 450) (z 3.50); SIDE view jaw bottom y 420-430 (z 3.6-3.7)
JAW = ((0.0, -0.62, 3.88), (0.0, -0.92, 3.68), 0.30, 0.15)
JAW_SX, JAW_K = 1.30, 0.10
# ---- CHEEK fur: FRONT view two spiky tufts per side at x 125-150 / 285-312 @ y 365-405 (X +-0.93, z 3.95-4.35)
CHEEKS = [((0.45, -0.62, 4.30), (0.90, -0.52, 4.22), 0.17, 0.04),
          ((0.45, -0.62, 4.10), (0.86, -0.55, 3.96), 0.16, 0.04)]   # L side (a, b, r_a, r_b): blunt points, mirrored
CHEEK_K = 0.07
# ---- NECK (hidden under the ruff + mantle)
NECK = ((0.0, -0.58, 2.90), (0.0, -0.48, 3.95), 0.50, 0.40)
NECK_K = 0.12
# ---- TORSO: a DEEP CHEST. SIDE view belly between the legs y 600-650 @ x 1010-1100 (z 1.4-1.9), the back under the
#   mantle, rump below the mantle / tail valley (1100, 520) (z 2.7); FRONT view forelegs leave the chest at x 150-212
TORSO = ((0.0, 1.30, 2.05), (0.0, -0.55, 2.35), 0.55, 0.80)   # (pelvis centre, chest centre, radii)
TORSO_SX = 0.82                       # "body width": the torso's X stretch
TORSO_K = 0.10
# ---- FORELEGS (L; R mirrored). FRONT view legs x 145-200 / 240-295 @ y 700 (X -+0.48, 0.45-0.55 wide), x 150-212 @ y
#   600 (0.60: thicker up top); paws x 140-205 @ y 770-800 (0.65 wide); SIDE view bands' mean x 977 (Y -0.33), the near paw
#   x 925-1000 (toe front Y -0.85), lower leg depth ~0.38 @ y 700
FORE_KNOTS = [(0.42, -0.40, 2.40), (0.46, -0.30, 1.40), (0.48, -0.36, 0.35)]   # shoulder, elbow, wrist
FORE_R = (0.33, 0.26, 0.17)           # "foreleg thickness" (depth half-widths) at shoulder / elbow / wrist: thick -> slim
FORE_SX = 1.10                        # X half-width share of the depth (front 0.47 / side 0.38 at y 700)
FORE_PAW_C, FORE_PAW_R = (0.48, -0.55, 0.13), (0.26, 0.30, 0.15)   # "paw size" (floor-cut)
FORE_TOE = (0.48, -0.84, 0.09)        # the toe front (chain end)
LEG_K, PAW_K = 0.10, 0.07
# ---- HINDLEGS (L; R mirrored -- SYMMETRIC, house rule): X from the BACK view's hind legs x 555-625 / 690-750 @ y 700
#   (centres X +-0.57 / 0.68 -> 0.62); Y from the SIDE view's hind bands x 1100-1150 / 1175-1230 (mean Y +1.52); the
#   FRONT view draws them at +-0.93 (perspective splay: not used)
HIND_L = {"hip": (0.48, 1.20, 2.05), "stifle": (0.56, 1.22, 1.25), "hock": (0.62, 1.62, 0.60),
          "paw": (0.62, 1.48, 0.13), "toe": (0.62, 1.20, 0.09), "haunch": (0.50, 1.28, 1.95)}
HIND = {"L": HIND_L, "R": {k_: (-v_[0], v_[1], v_[2]) for k_, v_ in HIND_L.items()}}
HIND_R = (0.33, 0.25, 0.17)           # "hindleg thickness" (depth half-widths) at hip / stifle / hock
HIND_SX = 1.05
HIND_PAW_R = (0.24, 0.29, 0.15)
HAUNCH_R, HAUNCH_K = (0.28, 0.45, 0.55), 0.12   # "haunch size" (BACK view thigh x 565-625 @ y 600: X 0.27-0.87)
CREASES = (-0.08, 0.08)               # "toe creases": 2 grooves across each paw front (3 toes, the sheet's ink lines)
CREASE_R, CREASE_K = 0.022, 0.015
# ---- FLEECE MANTLE base volumes (the clumps stand FLEECE_* h proud of these). SIDE view: chest front x 858 @ y 500-560
#   (Y -1.52), ruff top y 430 @ x 900-1000 (z 3.6), front-bottom lobes y 610-655 @ x 890-960 (z 1.35-1.8); mantle top y
#   440-470 @ x 1000-1080 (z 3.2-3.5) dipping to (1100, 520) (z 2.7), its lower edge y 600-640 (z 1.5-1.9); FRONT view
#   ruff half width 1.43 @ y 500 (z 3.0), 1.12 @ y 440 (z 3.6), the heart V's bottom lobes y 650-690 @ x 200-260 (z 1.1-1.5)
RUFF_C, RUFF_R = (0.0, -0.95, 2.70), (1.12, 0.40, 0.75)   # "chest ruff" (the bib's broad top)
RUFF_LOBE = ((0.72, -0.85, 3.05), (0.52, 0.42, 0.48))   # "ruff shoulders": the heart's two upper lobes (L, mirrored)
COLLAR = ((0.0, -0.20, 3.70), (0.78, 0.50, 0.55))   # "neck collar": the fleece wrapping the neck (SIDE view clumps
                                      #   behind the head (1000-1060, 330-440) = Y 0..0.5, z 3.5-4.6; FRONT view clumps
                                      #   flanking the jaw x 115-160 / 280-330 @ y 395-440 = X +-0.6-1.0, z 3.6-4.05)
RUFF_V = ((0.0, -0.85, 2.45), (0.0, -0.90, 1.55), 0.50, 0.25)   # "ruff point": the heart V hanging between the forelegs
BACK = ((0.0, -0.40, 2.30), (0.0, 1.00, 2.05), 0.95, 0.65)    # "back mantle": shoulders -> loin (BACK view shawl +-1.15)
BACK_SX, BACK_CUT_Z, BACK_CUT_K = 1.05, 1.75, 0.12   # its X stretch, its lower edge (z) and edge rounding
# ---- TAIL: a big fluffy wool ball, straight back + up (house rule). SIDE view top y 455 @ x 1170-1230 (z 3.35), back edge
#   x 1290 @ y 560-600 (Y +2.8), bottom y 690 @ x 1180-1240 (z 1.0); FRONT view width x 330-490 (1.6: X +-0.8)
TAIL_C, TAIL_R = (0.0, 2.05, 2.22), (0.66, 0.58, 0.98)
TAIL_ROOT = ((0.0, 1.25, 2.45), (0.0, 1.80, 2.45), 0.32, 0.55)
MANTLE_K = 0.12                       # smooth union of the three base volumes into the body
# ---- FLEECE CLUMPS ("soft, woolly fleece texture; rounded, fluffy curl clumps"): seeded spheres on the base surfaces
FLEECE_SEED = 11                      # "clump pattern" seed (any integer re-rolls the clumps; deterministic)
FLEECE_DRAWS = 9000                   # seeded candidate points on the base surface (area-weighted)
FLEECE_SPACING = 0.20                 # "clump density": min spacing of the big clump centres (dart throwing)
FLEECE_BIG = (0.12, 0.18, 0.45, 0.60)   # "clump size": radius range, protrusion range (x r)
LOBE_N = 3                            # "curl lobes": sub-lobes per big clump (the sheet's cauliflower clumps)
LOBE = (math.radians(50.0), 0.55, 0.50, 0.62)   # lobe tilt off the clump normal, offset (x r), radius range (x r)
PUFF_K = 0.10                         # "clump blend": smooth-union k of every clump (soft, never a pile of balls)
LIT_NZ = 0.30                         # "lit tops": fleece whose surface faces up more than this takes the lighter tone
# ---- EARS: pointed, cupped. FRONT view tips (140, 258) / (298, 258) (X -+0.79, z 5.42), base inner (200, 310) (X 0.18,
#   z 4.90), base outer (150, 345) (X 0.68, z 4.55); SIDE view tip (995, 250) (Y -0.15..-0.25), base x 960-1020
EAR_BASE = (0.40, -0.40, 4.70)        # the base centre (L side), inside the head
EAR_TIP = (0.80, -0.22, 5.44)         # "ear tip" (L side)
EAR_R = (0.30, 0.02)                  # "ear width": base / tip half width
EAR_CONVEX = 0.75                     # "ear fullness": width ~ (1 - t)^this along the ear
EAR_FLAT = 0.80                       # "ear thickness": front-back flattening of the cone
EAR_YAW = 22.0                        # the ear face turns this far from the front toward its side
EAR_K = 0.06
EAR_CUP = (0.68, 0.55, 0.14, 0.80)    # "ear cup": the concave hollow on the front face (crystal_canine v2)
EAR_INNER_TAU = 0.012                 # the light inner ear = the hollow's surface
# ---- FACE parts. FRONT view eyes x 168-205 / 233-270 @ y 355-395 (centres X -+0.33, z 4.25): NARROW + angry, the top
#   edge slanting down to the nose (outer top y 356 = z 4.44, inner top y 372 = z 4.28, bottom y 396 = z 4.04); pupils
#   black vertical ovals at the inner side (202, 378) / (236, 378) (0.16 toward the nose, ~0.11 x 0.24)
EYE_X, EYE_Z = 0.33, 4.22             # "eye placement" (mirrored); cast from the front onto the head
EYE_A, EYE_B = 0.19, 0.19             # "eye size": the oval half width / half height
EYE_BROW = (0.10, 0.45)               # "eye slant": the top edge y = c + s x (x outward): inner top +0.06, outer +0.20
EYE_FACE = 0.45                       # the eye faces this share further toward the front than the head normal
PUPIL_IN, PUPIL_DZ = 0.12, -0.01      # "pupil placement": toward the nose / down (clipped to the eye)
PUPIL_R = (0.055, 0.115)              # "pupil size"
NOSE_Z, NOSE_R = 4.01, (0.085, 0.055)   # "nose": FRONT (208-228, 390-405); cast on the midline
DECAL_UP, DECAL_DN, DECAL_DOME = 0.008, 0.03, 0.014   # decal top offset / sink / dome height
EYE_DOME = 0.005                      # the eye white's dome: low, so the flat-shaded eye face reads smooth
# ---- GRIN (front-view outline, relative to MOUTH_Z0): corners (155, 395) / (283, 395) (X -+0.63, z 4.05), the top edge
#   under the nose y 410 (z 3.90), the bottom V (218, 440) (z 3.60). SIDE view: the grin runs back from the nose to
#   (955, 385) (Y -0.55): the front outline wraps round the muzzle
MOUTH_Z0, MOUTH_A = 3.84, 0.62
MOUTH_TOP = (0.06, 0.16)              # top edge y = a + b (x / A)^2
MOUTH_BOT = (-0.22, 0.44, 1.6)        # bottom edge y = a + b |x / A|^p
MOUTH_YMAX = -0.30                    # the grin cavity only forward of this Y (never through the back of the head)
MOUTH_DEPTH = 0.16                    # "mouth depth": the grin is CARVED this far into the head (artist 2026-10-11:
                                      #   "push the mouth in and have the teeth carved out")
MOUTH_FRONT = 0.25                    # the cavity only where the nearest face surface faces front (n . -Y > this)
MOUTH_ROUND = 0.04                    # "lip softness": smooth-subtract k of the cavity (soft rounded inner walls)
TEETH = (8, 7)                        # "teeth": upper (hanging) / lower (rising) count: they interlock in a zigzag
TOOTH_FILL, TOOTH_INSET = 0.72, 0.011  # tooth height share of the local band, the gap (half width)
TOOTH_BASE = 0.92                     # tooth base width share of the tooth spacing: with TOOTH_FILL < 1 the rows
                                      #   no longer meet, so the purple inner mouth (artist 2026-10-11) shows between them
TOOTH_SET, TOOTH_T = 0.03, 0.07       # "teeth set": front face this far inside the lip line, tooth thickness (+Y)
TOOTH_ROOT = 0.035                    # the root edge sinks this far into the lip wall (no floating teeth)
# ---- HORNS (L; R mirrored): LARGE CRESCENTS rising behind the head, bulging OUT (front view) and BACK (side view).
#   FRONT view: tip (185, 163) (X 0.33, z 6.37), outer edge x 34 @ y 330 (X 1.84, z 4.7), inner glow edge x 100 @ y 350
#   (1.18), (150, 215) (0.68, z 5.85), bottom ends hidden behind the ruff (100-140, 420-445). SIDE view: tip (1000, 168)
#   (Y -0.1, z 6.2), outer edge x 1160 @ y 330 (Y 1.5), inner glow edge x 1095 @ y 300 (Y 0.85), lower end tucked behind
#   the neck (1000-1040, 430-450) (z 3.4). Centre line = mid outer / inner; the base buried in the shoulder fleece
HORN_KNOTS = [(0.62, 0.15, 3.00), (0.95, 0.50, 3.45), (1.28, 0.88, 3.90), (1.50, 1.17, 4.75), (1.38, 1.02, 5.45),
              (1.02, 0.62, 5.95), (0.62, 0.20, 6.22), (0.34, -0.10, 6.33)]
HORN_W = ((0.0, 0.52), (0.25, 0.68), (0.50, 0.66), (0.72, 0.46), (0.90, 0.18), (1.0, 0.0))   # "horn width" (in-plane)
HORN_D = ((0.0, 0.40), (0.50, 0.44), (0.80, 0.30), (1.0, 0.0))   # "horn thickness" (slab depth)
HORN_N = 36                           # sections along the horn
HORN_SEC = 10                         # "horn roundness": points round the elliptical section
HORN_GLOW = 0.17                      # "glow stripe": the inner-edge glow facet's width share of the horn width
HORN_CENTRE = (0.30, 0.20, 4.90)      # the crescent's concave side faces this (the inner glow edge)
# ---- ORB: FRONT view ring x 172-266 @ y 170-262 (centre (218, 216): z 5.84, outer r 0.47, inner r 0.37); SIDE view
#   centre (1017, 215) (Y +0.07, z 5.75)
ORB_C, ORB_R = (0.0, 0.07, 5.80), 0.35   # "orb" centre / radius
ORB_RING = (0.42, 0.05)               # "orb ring": major / tube radius (axis = the front)
# ---- LEG BANDS: FRONT view bands at y 745-755 (z 0.45-0.55), outer width 0.60-0.68; SIDE / BACK y 745-750 (z 0.42-0.45)
BAND_Z, BAND_R0, BAND_RT = 0.47, 0.29, 0.035   # "band height", ring radius, tube radius
BAND_SEGS = (20, 5)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_FLOOR_FLEECE = 0.70                # "fleece crevice darkness": the fleece texels' AO floor (plush, light creases)
AO_FLOOR = 0.55                       # "crevice darkness": the baked AO never goes below this (no black grime between
                                      #   the clumps: the sheet's fleece is soft, outlined, never sooty)
# ---- rig (weight bands)
OWN_TAU = 0.06                        # soft part ownership for weights
NECK_BAND = (3.30, 3.70)              # spine.003 -> neck blend (z) on the neck
HEAD_BAND = (3.80, 4.10)              # neck -> head blend (z)
RUFF_BAND = (3.10, 3.60)              # ruff: spine.003 -> neck (z)
EAR_BLEND = (0.12, 0.35)              # head -> ear blend along the ear axis (fraction)
LEG_BLEND = (0.12, 0.08)              # leg joint blend half width (hip/knee), the paw blend (above the wrist / hock only)
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent not yet given
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.9                          # "breathing": chest rise (spine.002/.003 pitch deg, 2 breaths per loop)
IDLE_HEAD = (0.8, 1.5)                # head nod (2 cyc) / slow look (1 cyc), deg (small: the horns ride the head)
EAR_TWITCH = (14.0, 0.30, 0.70, 30)   # "ear twitch": (deg, phase L, phase R, sharpness: (0.5 + 0.5 cos)^n pulse)
TAIL_SWAY = (4.0, 7.0, 10.0, 1, 0.6)  # "tail sway": per bone yaw deg .001-.003, cyc, lag per bone
ORB_BOB = (0.06, 2)                   # "orb bob": amplitude (sheet units), cycles per loop
WALK_N = 48                           # "walk loop": 2 s
STRIDE, LIFT, DUTY = 0.40, 0.14, 0.75   # "stride" (paw travel), "paw lift", stance share of the cycle
WALK_PHASE = {"hindleg_L": 0.0, "foreleg_L": 0.25, "hindleg_R": 0.5, "foreleg_R": 0.75}   # 4-beat lateral sequence
PAW_CURL = 14.0                       # swing paws curl (deg)
WALK_BOB = 0.02                       # body bob (2 per loop)
WALK_TAIL = (3.0, 5.0, 7.0, 1, 0.6)
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
          "design/reference/eclipse_wolf-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; heights / depths off the side view, widths off the front + back views; floor z = 0",
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


def vertex_normals(V, F):
    Fa = np.array(F, dtype=np.int64)
    fn = np.cross(V[Fa[:, 1]] - V[Fa[:, 0]], V[Fa[:, 2]] - V[Fa[:, 0]])     # area-weighted
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, Fa[:, k], fn)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

def mouth2d(x, z):
    u = np.clip(np.abs(x) / MOUTH_A, 0.0, 1.5)
    y = z - MOUTH_Z0
    top = MOUTH_TOP[0] + MOUTH_TOP[1] * u ** 2
    bot = MOUTH_BOT[0] + MOUTH_BOT[1] * u ** MOUTH_BOT[2]
    return np.minimum(np.minimum(top - y, y - bot), MOUTH_A - np.abs(x))


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


def sd_jaw(P):
    return sd_xstretch_cone(P, *JAW, JAW_SX)


def sd_cheek(P, sg):
    d = np.full(len(P), 1e9)
    for a, b, ra, rb in CHEEKS:
        d = SD.smin(d, SD.sd_round_cone(P, mir(a, sg), mir(b, sg), ra, rb), 0.04)
    return d


def chain_sdf(P, pts, rads, sx=1.0):
    """round-cone chain; sx != 1 stretches its X section (an oval leg), scaled to a valid lower bound"""
    Q = P.copy(); Q[:, 0] = Q[:, 0] / sx
    d = np.full(len(P), 1e9)
    for (a, b), (ra, rb) in zip(zip(pts[:-1], pts[1:]), zip(rads[:-1], rads[1:])):
        a = np.array(a, float); b = np.array(b, float); a[0] /= sx; b[0] /= sx
        d = np.minimum(d, SD.sd_round_cone(Q, a, b, ra, rb))
    return d * min(1.0, sx)


def fore_pts(sg):
    return [mir(p, sg) for p in FORE_KNOTS]


def fore_chain(sg):
    return fore_pts(sg) + [mir(FORE_PAW_C, sg) + np.array([0.0, 0.10, 0.04])], list(FORE_R) + [0.15]


def sd_foreleg(P, sg):
    pts, rr = fore_chain(sg)
    return SD.smin(chain_sdf(P, pts, rr, FORE_SX), SD.sd_ellipsoid(P, mir(FORE_PAW_C, sg), FORE_PAW_R), PAW_K)


def hind_pts(s):
    h = HIND[s]
    return [np.array(h[k], float) for k in ("hip", "stifle", "hock")]


def hind_chain(s):
    return hind_pts(s) + [np.array(HIND[s]["paw"], float) + np.array([0.0, 0.10, 0.04])], list(HIND_R) + [0.16]


def sd_hindleg(P, s):
    pts, rr = hind_chain(s)
    return SD.smin(chain_sdf(P, pts, rr, HIND_SX), SD.sd_ellipsoid(P, HIND[s]["paw"], HIND_PAW_R), PAW_K)


def sd_haunch(P, s):
    return SD.sd_ellipsoid(P, HIND[s]["haunch"], HAUNCH_R)


# ---- fleece mantle: three base volumes + seeded clumps
RC = np.array(RUFF_C, float); RR_ = np.array(RUFF_R, float)


def sd_ruff_base(P):
    d = SD.smin(SD.sd_ellipsoid(P, RC, RR_), SD.sd_round_cone(P, *RUFF_V), 0.15)
    for sg in (1.0, -1.0):
        d = SD.smin(d, SD.sd_ellipsoid(P, mir(RUFF_LOBE[0], sg), RUFF_LOBE[1]), 0.15)
    return SD.smin(d, SD.sd_ellipsoid(P, COLLAR[0], COLLAR[1]), 0.15)


def sd_back_base(P):
    d = sd_xstretch_cone(P, *BACK, BACK_SX)
    return SD.smax(d, BACK_CUT_Z - P[:, 2], BACK_CUT_K)


def sd_tail_base(P):
    return SD.smin(SD.sd_ellipsoid(P, TAIL_C, TAIL_R), SD.sd_round_cone(P, *TAIL_ROOT), 0.12)


BASES = {"ruff": sd_ruff_base, "mantle": sd_back_base, "tail": sd_tail_base}
BASE_C = {"ruff": np.array([0.0, -0.70, 2.90]), "mantle": 0.5 * (np.array(BACK[0]) + np.array(BACK[1])),
          "tail": np.array(TAIL_C, float)}


def sd_body_core(P):
    """the fur body the clumps must not grow out of (torso, neck, head group, legs)"""
    d = np.minimum(sd_torso(P), sd_neck(P))
    d = np.minimum(d, np.minimum(sd_head(P), np.minimum(sd_muzzle(P), sd_jaw(P))))
    for s, sg in SIDES:
        d = np.minimum(d, np.minimum(sd_foreleg(P, sg), sd_hindleg(P, s)))
    return d


def grad(fn, P, h=0.004):
    g = np.zeros_like(P)
    for k in range(3):
        e = np.zeros(3); e[k] = h
        g[:, k] = (fn(P + e) - fn(P - e)) / (2 * h)
    return g


def project_to(fn, S, iters=10):
    for _ in range(iters):
        d = fn(S)
        g = grad(fn, S)
        S = S - (d / np.maximum((g * g).sum(1), 1e-9))[:, None] * g
    return S, fn(S), g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-9)


def sd_mantle_base(P):
    return np.minimum.reduce([fn(P) for fn in BASES.values()])


def scatter():
    """EVEN seeded clumps over the mantle's base surface: the three base volumes are polygonised on a coarse grid, points
    drawn area-weighted (seeded), kept only on the OUTER surface (not inside the fur body, not on the back band's
    underside, no clump centre in the head), X >= 0, then thinned by dart-throwing (min spacing FLEECE_SPACING); each
    survivor is a big clump (radius + protrusion seeded) owned by its nearest base volume, grown LOBE_N seeded sub-lobes
    round its outer hemisphere (the sheet's cauliflower curls). Everything with |X| >= 0.25 r is then mirrored to -X
    (symmetric rest mesh + symmetric bake high), the rest snapped onto X = 0."""
    gc = SD.Grid(GRID_LO, GRID_HI, 0.05, 0.2)
    for nm_, fn in BASES.items():
        gc.apply(fn, np.array(GRID_LO), np.array(GRID_HI), 0.0)
    V_, F_ = SD.polygonise(gc)
    A_ = 0.5 * np.linalg.norm(np.cross(V_[F_[:, 1]] - V_[F_[:, 0]], V_[F_[:, 2]] - V_[F_[:, 0]]), axis=1)
    rng = np.random.default_rng(FLEECE_SEED)
    n = FLEECE_DRAWS
    fi = rng.choice(len(F_), n, p=A_ / A_.sum())
    uv = rng.uniform(0.0, 1.0, (n, 2))
    fl = uv.sum(1) > 1.0
    uv[fl] = 1.0 - uv[fl]
    S = V_[F_[fi, 0]] + uv[:, :1] * (V_[F_[fi, 1]] - V_[F_[fi, 0]]) + uv[:, 1:] * (V_[F_[fi, 2]] - V_[F_[fi, 0]])
    S, dd, N = project_to(sd_mantle_base, S, 4)
    own = np.argmin(np.stack([fn(S) for fn in BASES.values()], 1), 1)
    keep = (np.abs(dd) < 0.01) & (S[:, 0] >= 0.0) & (sd_body_core(S) > -0.02) & (sd_head(S) > 0.02)
    mb = own == list(BASES).index("mantle")
    keep &= ~mb | ((N[:, 2] > -0.75) & (S[:, 2] > BACK_CUT_Z - 0.03))   # clumps ride the lower rim too (scalloped edge)
    acc = []
    for i in np.nonzero(keep)[0]:                       # dart throwing in the seeded draw order
        if not acc or np.min(np.linalg.norm(S[acc] - S[i], axis=1)) >= FLEECE_SPACING:
            acc.append(i)
    acc = np.array(acc)
    S, N, own = S[acc], N[acc], own[acc]
    m = len(acc)
    R = rng.uniform(FLEECE_BIG[0], FLEECE_BIG[1], m)
    H = rng.uniform(FLEECE_BIG[2], FLEECE_BIG[3], m) * R
    C = S + N * (H - R)[:, None]
    ok = sd_head(C) > 0.0
    C, R, N, own = C[ok], R[ok], N[ok], own[ok]
    Cl, Rl, Ol = [C], [R], [own]
    lr = np.random.default_rng(FLEECE_SEED + 1000)
    for i in range(len(C)):
        T0 = lr.normal(0.0, 1.0, 3)
        T0 -= N[i] * float(T0 @ N[i]); T0 /= max(np.linalg.norm(T0), 1e-9)
        T2 = np.cross(N[i], T0)
        ang = 2 * math.pi * (np.arange(LOBE_N) + lr.uniform(0.0, 1.0)) / LOBE_N      # spread round the clump
        dirs = (np.outer(np.cos(ang), T0) + np.outer(np.sin(ang), T2)) * math.sin(LOBE[0]) + N[i] * math.cos(LOBE[0])
        Cl.append(C[i] + dirs * R[i] * LOBE[1])
        Rl.append(R[i] * lr.uniform(LOBE[2], LOBE[3], LOBE_N))
        Ol.append(np.full(LOBE_N, own[i]))
    C, R, O = np.vstack(Cl), np.concatenate(Rl), np.concatenate(Ol)
    mid = np.abs(C[:, 0]) < 0.25 * R
    C[mid, 0] = 0.0
    side = ~mid
    Cm = C[side].copy(); Cm[:, 0] *= -1.0
    C, R, O = np.vstack([C, Cm]), np.concatenate([R, R[side]]), np.concatenate([O, O[side]])
    return C, R, O, {"draws": n, "on_outer_surface_half": int(keep.sum()), "clumps_half": int(len(acc)),
                     "spheres_total": int(len(R)), "coarse_base_tris": int(len(F_))}


_C, _R, _O, _scat = scatter()
PUFFS = {nm_: (_C[_O == j_], _R[_O == j_]) for j_, nm_ in enumerate(BASES)}
_scat["per_base_spheres"] = {nm_: int(len(PUFFS[nm_][1])) for nm_ in PUFFS}


def sd_fleece_group(P, name, smooth=False):
    base = BASES[name](P)
    C, R = PUFFS[name]
    if smooth:
        return SD.smin(base, FP.sd_spheres_smin(P, C, R, PUFF_K), PUFF_K)
    return np.minimum(base, FP.sd_spheres_min(P, C, R))


def sd_fleece_smooth(P):
    d = np.full(len(P), 1e9)
    for nm_ in BASES:
        d = SD.smin(d, sd_fleece_group(P, nm_, True), MANTLE_K)
    return d


# ---- ears: flattened round-cone chains in each ear's own frame (crystal_canine v2: cupped)
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
    """> 0 on the light inner ear: the hollow's surface = shell points INSIDE the solid (uncarved) ear; the rim stays fur"""
    return -sd_ear_solid(P, s) - EAR_INNER_TAU


GROUPS = ["torso", "ruff", "mantle", "tail", "neck", "head", "muzzle", "jaw", "cheek.L", "cheek.R", "ear.L", "ear.R",
          "haunch.L", "haunch.R", "foreleg.L", "foreleg.R", "hindleg.L", "hindleg.R"]
FLEECE_G = ("ruff", "mantle", "tail")
HEAD_G = ("head", "muzzle", "jaw", "cheek.L", "cheek.R")


def group_sdf(P):
    cols = []
    for g_ in GROUPS:
        sd_side = g_[-1] if "." in g_ else None
        sg = 1.0 if sd_side == "L" else -1.0
        if g_ == "torso":
            d = sd_torso(P)
        elif g_ in FLEECE_G:
            d = sd_fleece_group(P, g_)
        elif g_ == "neck":
            d = sd_neck(P)
        elif g_ == "head":
            d = sd_head(P)
        elif g_ == "muzzle":
            d = sd_muzzle(P)
        elif g_ == "jaw":
            d = sd_jaw(P)
        elif g_.startswith("cheek."):
            d = sd_cheek(P, sg)
        elif g_.startswith("ear."):
            d = sd_ear(P, sd_side)
        elif g_.startswith("haunch."):
            d = sd_haunch(P, sd_side)
        elif g_.startswith("foreleg."):
            d = sd_foreleg(P, sg)
        else:
            d = sd_hindleg(P, sd_side)
        cols.append(d)
    return np.stack(cols, 1)


def ell_box(c, r, pad=0.0):
    c = np.asarray(c, float); r = np.asarray(r, float)
    return c - r - pad, c + r + pad


def pts_box(pts, R, pad=0.0):
    A = np.array(pts, float)
    return A.min(0) - R - pad, A.max(0) + R + pad


g = SD.Grid(GRID_LO, GRID_HI, SDF_STEP, SDF_BAND)
g.apply(sd_torso, *pts_box([TORSO[0], TORSO[1]], max(TORSO[2], TORSO[3])), 0.0)
for s, sg in SIDES:
    g.apply(lambda P, s=s: sd_haunch(P, s), *ell_box(HIND[s]["haunch"], HAUNCH_R), HAUNCH_K)
    h = HIND[s]
    g.apply(lambda P, s=s: sd_hindleg(P, s), *pts_box([h["hip"], h["stifle"], h["hock"], h["paw"]], max(HIND_PAW_R) + 0.05),
            LEG_K)
    g.apply(lambda P, sg=sg: sd_foreleg(P, sg), *pts_box(fore_pts(sg) + [mir(FORE_PAW_C, sg)], max(FORE_PAW_R) + 0.05), LEG_K)
g.apply(sd_neck, *pts_box([NECK[0], NECK[1]], NECK[2]), NECK_K)
g.apply(sd_back_base, *pts_box([BACK[0], BACK[1]], BACK[2] * BACK_SX), MANTLE_K)
g.apply(sd_ruff_base, np.array([-1.45, -1.55, 1.2]), np.array([1.45, 0.40, 4.35]), MANTLE_K)
g.apply(lambda P: SD.sd_round_cone(P, *RUFF_V), *pts_box([RUFF_V[0], RUFF_V[1]], RUFF_V[2]), MANTLE_K)
g.apply(sd_tail_base, *pts_box([TAIL_ROOT[0], TAIL_C], max(TAIL_R) + 0.05), MANTLE_K)
for nm_ in BASES:
    for c_, r_ in zip(*PUFFS[nm_]):
        g.apply(lambda P, c_=c_, r_=r_: SD.sd_sphere(P, c_, r_), c_ - r_, c_ + r_, PUFF_K)
g.apply(sd_head, *ell_box(HEAD_C, HEAD_R), HEAD_K)
g.apply(sd_muzzle, *pts_box([MUZZLE[0], MUZZLE[1]], MUZZLE[2] * MUZZLE_SX), MUZZLE_K)
g.apply(sd_jaw, *pts_box([JAW[0], JAW[1]], JAW[2] * JAW_SX), JAW_K)
for s, sg in SIDES:
    g.apply(lambda P, sg=sg: sd_cheek(P, sg), *pts_box([mir(c_[k_], sg) for c_ in CHEEKS for k_ in (0, 1)], 0.2), CHEEK_K)
    e = EAR[s]
    g.apply(lambda P, s=s: sd_ear(P, s), np.minimum(e["base"], e["tip"]) - EAR_R[0], np.maximum(e["base"], e["tip"]) + EAR_R[0],
            EAR_K)
def sd_face(P):
    """the UNCARVED head group (head, muzzle, jaw, cheeks): the face surface the grin is carved from"""
    return np.minimum.reduce([sd_head(P), sd_muzzle(P), sd_jaw(P), sd_cheek(P, 1.0), sd_cheek(P, -1.0)])


def sd_cavity(P):
    """the grin CAVITY: inside the front-view outline, forward of MOUTH_YMAX, and no deeper than MOUTH_DEPTH below the
    uncarved face surface (so its floor / back follow the face's curve), and only in the face's skin (sd_face < 0.06:
    never the collar / neck volumes that sit in the outline's prism under the jaw), and only under FRONT-facing face
    (never a channel along the underside of the jaw)"""
    sf = sd_face(P)
    gf = grad(sd_face, P, 0.01)
    nf = -gf[:, 1] / np.maximum(np.linalg.norm(gf, axis=1), 1e-9)       # the nearest face surface faces the front
    return np.maximum.reduce([-mouth2d(P[:, 0], P[:, 2]), -(sf + MOUTH_DEPTH), sf - 0.06, P[:, 1] - MOUTH_YMAX,
                              MOUTH_FRONT - nf])


g.apply(sd_cavity, np.array([-MOUTH_A - 0.05, -1.75, MOUTH_Z0 - 0.30]), np.array([MOUTH_A + 0.05, MOUTH_YMAX, MOUTH_Z0 + 0.30]),
        MOUTH_ROUND, mode="subtract")
TOE_CREASES = []
for s, sg in SIDES:                                    # toe creases across each paw front
    for c_, r_ in ((mir(FORE_PAW_C, sg), np.array(FORE_PAW_R)), (np.array(HIND[s]["paw"], float), np.array(HIND_PAW_R))):
        for dx in CREASES:
            x_ = c_[0] + dx * r_[0] / 0.24
            c0 = np.array([x_, c_[1] - 0.35 * r_[1], c_[2] + 0.95 * r_[2]])
            c1 = np.array([x_, c_[1] - 1.05 * r_[1], 0.0])
            TOE_CREASES.append((c0, c1))
            g.apply(lambda P, c0=c0, c1=c1: SD.sd_round_cone(P, c0, c1, CREASE_R, CREASE_R),
                    np.minimum(c0, c1) - CREASE_R, np.maximum(c0, c1) + CREASE_R, CREASE_K, mode="subtract")
g.floor(0.0)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
assert np.all(HV.min(0) > np.array(GRID_LO) + SDF_STEP) and np.all(HV.max(0) < np.array(GRID_HI) - SDF_STEP), \
    ("the shell touches the SDF grid box (an open cut)", HV.min(0), HV.max(0))
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "toe_creases": len(TOE_CREASES), "crumbs": crumbs,
                 "seconds": round(time.time() - t_sdf, 1)}
report["fleece"] = {"seed": FLEECE_SEED, "clumps": _scat, "k": PUFF_K,
                    "rule": "area-weighted seeded draws on the coarse-polygonised base surface -> outer surface only -> "
                            "dart-throwing (spacing %.2f) -> big clump + %d sub-lobes; X >= 0 mirrored" % (FLEECE_SPACING, LOBE_N)}
print("SDF", json.dumps(report["sdf"]), "FLEECE", json.dumps(report["fleece"]))

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


t = time.time()
DGR = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
_fl = [GROUPS.index(g_) for g_ in FLEECE_G]
_nf = [j for j in range(len(GROUPS)) if j not in _fl]
FIELDS["fleece"] = DGR[:, _nf].min(1) - DGR[:, _fl].min(1)
# lit clump tops: the smooth mantle field's gradient points up (evaluated where the mantle is near)
_lit = np.full(len(LV), -1.0)
_near = FIELDS["fleece"] > -0.15
if np.any(_near):
    _gz = grad(sd_fleece_smooth, LV[_near], 0.006)
    _lit[_near] = _gz[:, 2] / np.maximum(np.linalg.norm(_gz, axis=1), 1e-9) - LIT_NZ
FIELDS["lit"] = _lit
# grin: the front-view outline, on front-facing head-group surface forward of MOUTH_YMAX
_hg = [GROUPS.index(g_) for g_ in HEAD_G]
_nh = [j for j in range(len(GROUPS)) if j not in _hg]
_own_head = DGR[:, _nh].min(1) - DGR[:, _hg].min(1)
VN = vertex_normals(LV, LF)


FIELDS["mouth"] = np.minimum.reduce([mouth2d(LV[:, 0], LV[:, 2]) + 0.01, MOUTH_YMAX - LV[:, 1],   # (no group gate: the
                                     #   neck volume reaches into the cavity and would steal its back wall)
                                     -sd_face(LV) - 0.012])   # the carved cavity surface (inside the uncarved face)
FIELDS["inner"] = np.maximum(ear_inner_field(LV, "L"), ear_inner_field(LV, "R"))
report["field_seconds"] = round(time.time() - t, 1)
_own = np.argmin(DGR, 1)
_tv = (_own == GROUPS.index("tail")) & (LV[:, 2] < 1.6)          # the tail ball's lower half
_legs = [GROUPS.index(g_) for g_ in ("hindleg.L", "hindleg.R")]
TAIL_GAP = float(DGR[_tv][:, _legs].min()) if np.any(_tv) else 9.0
report["tail_leg_clearance"] = {"min_gap": round(TAIL_GAP, 4), "need": "> PUFF_K / 2 = %.3f (no smooth-union bridge)" % (PUFF_K / 2),
                                "rule": "tail-owned shell points below z 1.6 -> nearest hind leg"}
print("TAIL_GAP", round(TAIL_GAP, 4))
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
on_fleece = lambda a, b: all(v[LAY["fleece"]] > -TOL for v in (a, b))
CUTS = [("fleece", 0.0, None), ("lit", 0.0, on_fleece), ("mouth", 0.0, None), ("inner", 0.0, on_ear)]
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
is_fleece = FVAL["fleece"] > 0
shell_reg[is_fleece] = "fleece"
shell_reg[is_fleece & (FVAL["lit"] > 0)] = "fleece_lit"
shell_reg[~is_fleece & (FVAL["mouth"] > 0)] = "mouth"
is_ear = np.isin(owner, [GROUPS.index("ear.L"), GROUPS.index("ear.R")])
shell_reg[~is_fleece & is_ear & (FVAL["inner"] > 0)] = "ear_inner"
_mv = np.unique(np.array([v for f_, r_ in zip(CF, shell_reg) if r_ == "mouth" for v in f_]))
_md = -sd_face(CV[_mv]) if len(_mv) else np.zeros(1)
_FC = np.array([CV[f_].mean(0) for f_ in CF])
_carved = (-sd_face(_FC) > 0.03) & (mouth2d(_FC[:, 0], _FC[:, 2]) > 0) & (_FC[:, 1] < MOUTH_YMAX)
_cnp = _carved & (shell_reg != "mouth")
print("CARVED", int(_carved.sum()), "not purple", int(_cnp.sum()), sorted(set(shell_reg[_cnp])), np.round(_FC[_cnp][:12], 3).tolist())
report["mouth"] = {"depth_max": round(float(_md.max()), 4), "depth_p90": round(float(np.percentile(_md, 90)), 4),
                   "target": MOUTH_DEPTH, "faces": int(np.sum(shell_reg == "mouth")),
                   "rule": "mouth-region shell vertices -> depth below the UNCARVED face surface (-sd_face)"}
print("MOUTH", json.dumps(report["mouth"]))
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: face, teeth, horns, orb, bands
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=6.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


def project(pts, n):
    return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])


def front_cast(x, z):
    return cast((x, -4.0, z), (0, 1, 0))


def front_anchor(x, z):
    """ray from the front (-Y) along +Y at (x, z) -> surface point + a smoothed normal (mean of a small cross of hits)"""
    S, _ = front_cast(x, z)
    ns = [front_cast(x + dx, z + dz)[1] for dx, dz in ((0, 0), (0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03))]
    return S, unit(np.mean(ns, 0))


def mirror_part(p_, name):
    V_ = p_.V.copy(); V_[:, 0] *= -1.0
    return FP.Part(name, V_, [f_[::-1] for f_ in p_.F], p_.R, bone=p_.bone)


def in_eye(x, y):
    """the narrow eye (decal coords, x outward): an oval cut by the slanted brow line"""
    return (x / EYE_A) ** 2 + (y / EYE_B) ** 2 < 1.0 and y < EYE_BROW[0] + EYE_BROW[1] * x


PARTS = []
FACE = {}
for s, sg in SIDES[:1]:                                      # L only; R = the mirror (symmetric rest mesh)
    S, n = front_anchor(sg * EYE_X, EYE_Z)
    ne = unit(n + EYE_FACE * np.array([0.0, -1.0, 0.0]))
    O = FP.clip_outline(in_eye, EYE_A * 1.5, 28)
    V, F, R = FP.polar_decal(S, ne, O, project, DECAL_UP, DECAL_DN, EYE_DOME, 3, "eye_white", up_hint=(0, 0, 1))
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    pc = (-PUPIL_IN, PUPIL_DZ)                               # decal x points outward: the nose side is -x
    in_pupil = lambda x, y: (((x + PUPIL_IN) / PUPIL_R[0]) ** 2 + ((y - PUPIL_DZ) / PUPIL_R[1]) ** 2 < 1.0
                             and in_eye(x / 0.96, y / 0.96))
    Op = FP.clip_outline(lambda x, y: in_pupil(x + pc[0], y + pc[1]), 0.3, 20)
    Sp = S + (pc[0] * np.cross(unit(np.array([0, 0, 1.0]) - ne * ne[2]), ne)) + pc[1] * unit(np.array([0, 0, 1.0]) - ne * ne[2])
    V, F, R = FP.polar_decal(Sp, ne, Op, project, DECAL_UP + EYE_DOME + 0.004, DECAL_DN, 0.003, 2, "pupil", up_hint=(0, 0, 1))
    PARTS.append(FP.Part("pupil." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": ne.round(4).tolist(), "pupil": Sp.round(4).tolist()}
for p_ in [p_ for p_ in PARTS if p_.name.endswith(".L")]:
    PARTS.append(mirror_part(p_, p_.name[:-2] + ".R"))
Sn, nn = front_anchor(0.0, NOSE_Z)
nn = unit(nn + 0.5 * np.array([0.0, -1.0, 0.0]))
V, F, R = FP.polar_decal(Sn, nn, FP.lens_curve(NOSE_R[0], NOSE_R[1], 20, 0.15), project, DECAL_UP, DECAL_DN, 0.03, 3,
                         "nose", up_hint=(0, 0, 1))
PARTS.append(FP.Part("nose", V, F, R, bone="head"))
FACE["nose"] = {"surface": Sn.round(4).tolist(), "normal": nn.round(4).tolist()}


# ---- teeth: SOLID carved wedges set inside the cavity, upper row hanging from the top lip, lower row rising from the
#      bottom lip, interlocking (the gaps show the purple cavity behind)
def m_top(x):
    return MOUTH_Z0 + MOUTH_TOP[0] + MOUTH_TOP[1] * (abs(x) / MOUTH_A) ** 2


def m_bot(x):
    return MOUTH_Z0 + MOUTH_BOT[0] + MOUTH_BOT[1] * (abs(x) / MOUTH_A) ** MOUTH_BOT[2]


def inset2d(tri):
    C2 = np.asarray(tri, float); g2 = C2.mean(0)
    return g2 + (C2 - g2) * np.maximum(0.0, 1.0 - TOOTH_INSET / np.linalg.norm(C2 - g2, axis=1))[:, None]


def face_y(x, z):
    """Y of the UNCARVED face surface at front-view (x, z): march + bisect sd_face along +Y"""
    ys = np.arange(-2.2, MOUTH_YMAX + 0.3, 0.01)
    d = sd_face(np.stack([np.full_like(ys, x), ys, np.full_like(ys, z)], 1))
    if not np.any(d < 0):                                 # at the face silhouette's edge: the closest approach
        return float(ys[int(np.argmin(d))])
    k = int(np.argmax(d < 0))
    lo_, hi_ = ys[k - 1], ys[k]
    for _ in range(30):
        m_ = 0.5 * (lo_ + hi_)
        if sd_face(np.array([[x, m_, z]]))[0] < 0:
            hi_ = m_
        else:
            lo_ = m_
    return 0.5 * (lo_ + hi_)


_tw = 2 * MOUTH_A / TEETH[0]
TEETH_INFO = []
for row, cnt in (("upper", TEETH[0]), ("lower", TEETH[1])):
    cs_ = [(-MOUTH_A + _tw * (i + 0.5)) for i in range(cnt)] if row == "upper" else [_tw * (i - (cnt - 1) / 2) for i in range(cnt)]
    for i, c_ in enumerate(cs_):
        x0, x1 = c_ - TOOTH_BASE * _tw / 2, c_ + TOOTH_BASE * _tw / 2
        h_ = TOOTH_FILL * (m_top(c_) - m_bot(c_))
        if row == "upper":
            tri = [(x0, m_top(x0)), (x1, m_top(x1)), (c_, m_top(c_) - h_)]
            root = (0.0, TOOTH_ROOT)
        else:
            tri = [(x0, m_bot(x0)), (x1, m_bot(x1)), (c_, m_bot(c_) + h_)]
            root = (0.0, -TOOTH_ROOT)
        t2 = inset2d(tri)
        t2[:2] += np.array(root)                          # the root edge sinks into the lip wall
        front = np.array([[x_, face_y(x_, z_) + TOOTH_SET, z_] for x_, z_ in t2])
        assert np.all(front[:, 1] > -1.6) and np.all(front[:, 1] < MOUTH_YMAX), ("tooth off the face", nm_ := row, i, front)
        back = front + np.array([0.0, TOOTH_T, 0.0])
        back[2] = front[2] + np.array([0.0, 0.35 * TOOTH_T, 0.0])   # tapered point
        V, F, R = FP.tooth_wedge(front, back, "teeth")
        nm = "tooth.%s.%02d" % (row, i + 1)
        PARTS.append(FP.Part(nm, V, F, R, bone="head"))
        TEETH_INFO.append({"name": nm, "front_xz": np.round(tri, 3).tolist()})


# ---- horns (L; R mirrored)
def tab(table):
    xs = [a for a, _ in table]; ys = [b for _, b in table]
    return lambda s_: float(np.interp(s_, xs, ys))


_hk = np.array(HORN_KNOTS, float)
_hc = _hk - _hk.mean(0)
HORN_PLANE_N = np.linalg.svd(_hc)[2][2]
HORN_PATH = SD.resample(SD.catmull(_hk, 10), HORN_N)
V, F, R = FP.horn_sweep(HORN_PATH, tab(HORN_W), tab(HORN_D), HORN_PLANE_N, HORN_CENTRE, "horn_glow", "horn", HORN_SEC, HORN_GLOW)
PARTS.append(FP.Part("horn.L", V, F, R, bone="horn_L"))
PARTS.append(mirror_part(PARTS[-1], "horn.R")); PARTS[-1].bone = "horn_R"
_hplanar = float(np.abs(_hc @ HORN_PLANE_N).max())

# ---- orb + ring
V, F, R = FP.uv_sphere(ORB_C, ORB_R, 14, 22, "orb")
PARTS.append(FP.Part("orb", V, F, R, bone="orb"))
V, F, R = FP.torus(ORB_RING[0], ORB_RING[1], 32, 5, ORB_C, (0.0, -1.0, 0.0), up_hint=(0, 0, 1), region="orb_ring")
PARTS.append(FP.Part("orb_ring", V, F, R, bone="orb"))


# ---- leg bands: a torus round each lower leg at BAND_Z, its axis along the leg segment there
def band_on(chain_pts, rads, sx, bone, name):
    for a, b, ra, rb in zip(chain_pts[:-1], chain_pts[1:], rads[:-1], rads[1:]):
        if min(a[2], b[2]) <= BAND_Z <= max(a[2], b[2]):
            t_ = (a[2] - BAND_Z) / (a[2] - b[2])
            c = a + (b - a) * t_
            r_leg = (ra + (rb - ra) * t_) * max(1.0, sx)
            V, F, R = FP.torus(BAND_R0, BAND_RT, BAND_SEGS[0], BAND_SEGS[1], c, b - a, up_hint=(0, -1, 0), region="band")
            PARTS.append(FP.Part(name, V, F, R, bone=bone))
            return {"name": name, "centre": c.round(4).tolist(), "leg_radius": round(float(r_leg), 4),
                    "clearance": round(float(BAND_R0 - BAND_RT - r_leg), 4)}
    raise AssertionError("no leg segment spans BAND_Z for " + name)


_fp, _fr = fore_chain(1.0)
_hp, _hr = hind_chain("L")
BAND_INFO = [band_on(_fp, _fr, FORE_SX, "foreleg_L.lower", "band.fore.L"),
             band_on(_hp, _hr, HIND_SX, "hindleg_L.paw", "band.hind.L")]
for nm_ in ("band.fore.L", "band.hind.L"):
    p_ = [q for q in PARTS if q.name == nm_][0]
    PARTS.append(mirror_part(p_, nm_[:-2] + ".R")); PARTS[-1].bone = p_.bone.replace("_L.", "_R.")
assert all(b_["clearance"] > 0.0 for b_ in BAND_INFO), BAND_INFO

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
REG = ["fur", "ear_inner", "fleece", "fleece_lit", "mouth", "teeth", "eye_white", "pupil", "nose", "horn", "horn_glow",
       "orb", "orb_ring", "band"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (must never borrow a neighbour's field): %s" % _empty


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


GLOW_ALLOWED = {"horn_glow", "orb_ring", "band"}
GLOW_CAP = 2.0


def glow_tiers(pal):
    """the sheet draws glow on the horn inner edges, the orb ring and the leg bands only; every tier (and tier x
    emission_strength) under the 2.0 cap"""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    es = float(pal["material"].get("emission_strength", 1.0))
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "emission_strength": es,
            "only_drawn_glow": sorted(tier) == sorted(GLOW_ALLOWED), "cap": GLOW_CAP,
            "cap_pass": all(v <= GLOW_CAP and v * es <= GLOW_CAP for v in tier.values()),
            "max_effective": round(max(tier.values()) * es, 3)}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)   # palette <-> regions 1:1
report["glow"] = glow_tiers(pal_default)
assert report["glow"]["only_drawn_glow"] and report["glow"]["cap_pass"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, np.ones(len(FALL)))   # no value jitter: clean fleece, no dark noise
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
_gx, _gz = np.meshgrid(np.linspace(-MOUTH_A, MOUTH_A, 600), np.linspace(MOUTH_Z0 - 0.3, MOUTH_Z0 + 0.3, 300))
_gx, _gz = _gx.ravel(), _gz.ravel()
_inm = mouth2d(_gx, _gz) > 0
_cov = np.zeros(len(_gx), bool)
for ti in TEETH_INFO:
    a2, b2, c2 = inset2d(ti["front_xz"])
    def _ed(u_, v_):
        return (v_[0] - u_[0]) * (_gz - u_[1]) - (v_[1] - u_[1]) * (_gx - u_[0])
    d1, d2, d3 = _ed(a2, b2), _ed(b2, c2), _ed(c2, a2)
    _cov |= ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
MOUTH_SHOW = float((_inm & ~_cov).sum() / max(_inm.sum(), 1))
report["teeth"] = {"upper": TEETH[0], "lower": TEETH[1], "fill": TOOTH_FILL, "base": TOOTH_BASE,
                   "mouth_purple_share_front": round(MOUTH_SHOW, 4),
                   "rule": "front-view (x, z): share of the mouth outline not covered by an (inset) tooth triangle",
                   "teeth": TEETH_INFO}
print("MOUTH_SHOW", round(MOUTH_SHOW, 4))
report["horns"] = {"plane_normal_L": HORN_PLANE_N.round(4).tolist(), "knots_off_plane_max": round(_hplanar, 4),
                   "path_len": round(float(SD.arclen(HORN_PATH)[-1]), 4), "sections": HORN_N}
report["bands"] = BAND_INFO

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
    key = {"eye": "eye_whites", "pupil": "pupils", "nose": "nose", "tooth": "teeth", "horn": "horns", "orb": "orb",
           "orb_ring": "orb_ring", "band": "leg_bands"}[p.name.split(".")[0]]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The shell carries the head, the ears, four "
                            "slim legs + paws and the clumped fleece mantle + tail (the big clumps in the silhouette, "
                            "the small curls in the normal map); the horns, orb and bands are low closed sweeps / tori."
                            % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, decals, teeth, horns, orb, ring, bands)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"front_view_height_px": 639, "front_view_width_px": 436, "side_view_height_px": 635,
                               "side_view_length_px": 430},
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
low["conquest_source"] = "from scratch: design/reference/eclipse_wolf-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = ("Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base "
                                    "Color (glow: horn inner edges, orb ring, leg bands)")
# focus boxes for the close-ups (world, after the centre shift) = the sheet's detail panels
_horn_pts = np.vstack([p.V for p in PARTS if p.name.startswith("horn.") or p.name.startswith("orb")]) - SHIFT
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.95, -1.60, 3.45]) - SHIFT).tolist(), (np.array([0.95, -0.20, 5.50]) - SHIFT).tolist()],
    "halo": [_horn_pts.min(0).tolist(), _horn_pts.max(0).tolist()],
    "chest": [(np.array([-1.45, -1.65, 1.20]) - SHIFT).tolist(), (np.array([1.45, -0.40, 3.70]) - SHIFT).tolist()],
    "bands": [(np.array([-0.85, -0.90, 0.0]) - SHIFT).tolist(), (np.array([0.85, -0.10, 1.0]) - SHIFT).tolist()],
    "tail": [(np.array([-0.95, 1.20, 0.90]) - SHIFT).tolist(), (np.array([0.95, 2.90, 3.50]) - SHIFT).tolist()],
    "views": {"face": [-15.0, 6.0], "halo": [0.0, 10.0], "chest": [-25.0, 8.0], "bands": [-10.0, 10.0],
              "tail": [130.0, 10.0]},
    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "fleece", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "mirror_test", "regions_area_share", "glow", "face", "horns",
                                                            "bands", "tail_leg_clearance", "iso_cuts")}, default=str))
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
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets, every fleece curl at full density) + every closed part",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0
pa[:, :3] = np.maximum(pa[:, :3], AO_FLOOR)
_fl_tex = texels_of(np.isin(rid, [REG.index("fleece"), REG.index("fleece_lit")]), RA)
pa[_fl_tex, :3] = np.maximum(pa[_fl_tex, :3], AO_FLOOR_FLEECE)
bstats["ao_floor"] = {"all": AO_FLOOR, "fleece": AO_FLOOR_FLEECE, "fleece_texels": int(_fl_tex.sum())}
img_ao.pixels.foreach_set(pa.ravel())
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


SPINE_KNOTS = [(0.0, 1.30, 2.30), (0.0, 0.45, 2.42), (0.0, -0.30, 2.50), (0.0, -0.58, 2.95)]   # spine.001 -> .003
NECK_PTS = ((0.0, -0.58, 2.95), (0.0, -0.50, 3.95))
HEAD_PTS = ((0.0, -0.50, 3.95), (0.0, -0.50, 4.95))
BONES = [("spine.001", P_(SPINE_KNOTS[0]), P_(SPINE_KNOTS[1]), "root"),
         ("spine.002", P_(SPINE_KNOTS[1]), P_(SPINE_KNOTS[2]), "spine.001"),
         ("spine.003", P_(SPINE_KNOTS[2]), P_(SPINE_KNOTS[3]), "spine.002"),
         ("neck", P_(NECK_PTS[0]), P_(NECK_PTS[1]), "spine.003"),
         ("head", P_(HEAD_PTS[0]), P_(HEAD_PTS[1]), "neck")]
for s, sg in SIDES:
    BONES.append(("ear_" + s, P_(EAR[s]["base"] + EAR[s]["axis"] * 0.15), P_(EAR[s]["tip"]), "head"))
for s, sg in SIDES:
    BONES.append(("horn_" + s, P_(mir(HORN_PATH[0], sg)), P_(mir(HORN_PATH[len(HORN_PATH) // 2], sg)), "head"))
BONES.append(("orb", P_(ORB_C), P_(np.array(ORB_C) + np.array([0.0, 0.0, 0.35])), "head"))
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
TAIL_BONE_PTS = [np.array(p, float) for p in ((0.0, 1.25, 2.60), (0.0, 1.75, 2.80), (0.0, 2.20, 2.50), (0.0, 2.45, 1.85))]
TAIL_LEN = float(SD.arclen(np.array(TAIL_BONE_PTS))[-1])
for k in range(3):
    BONES.append(("tail.%03d" % (k + 1), P_(TAIL_BONE_PTS[k]), P_(TAIL_BONE_PTS[k + 1]),
                  "spine.001" if k == 0 else "tail.%03d" % k))
CONNECT = {"spine.002", "spine.003", "neck", "head", "tail.002", "tail.003"} | \
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
GD = group_sdf(Pw)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
zz = Pw[:, 2]
Ws = np.zeros((b_ - a_, len(DEFORM)))
_, s_sp, _ = FP.spine_param(Pw, np.array(SPINE_KNOTS))
L_sp = float(SD.arclen(np.array(SPINE_KNOTS))[-1])
Wv = K.vine_weights(np.clip(s_sp, 0, L_sp), L_sp, 3)
for gname in ("torso", "mantle"):                     # torso + back mantle: spine.001-.003 hat weights along the spine
    gi = GROUPS.index(gname)
    Ws[:, J["spine.001"]] += Wg[:, gi] * (Wv[:, 0] + Wv[:, 1])
    Ws[:, J["spine.002"]] += Wg[:, gi] * Wv[:, 2]
    Ws[:, J["spine.003"]] += Wg[:, gi] * Wv[:, 3]
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
for gname in HEAD_G:
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
Wv = K.vine_weights(np.clip(s_arc, 0, TAIL_LEN), TAIL_LEN, 3)
Ws[:, J["spine.001"]] += Wg[:, gi] * Wv[:, 0]
for k in range(3):
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between torso, ruff, back mantle, tail, neck, head, muzzle, "
                          "jaw, cheeks, ears, haunches, four legs; torso + back mantle spine.001-.003 hat weights along the "
                          "spine; neck spine.003 -> neck -> head by z bands; ruff spine.003 -> neck; ears blend from the "
                          "head along the ear axis; legs + haunches: parent -> upper -> lower -> paw by the leg-chain arc "
                          "(the paw rigid below the ankle); tail: rigkit.vine_weights along tail.001-.003 (parent "
                          "spine.001); eyes, pupils, nose, teeth rigid on the head; horns rigid on horn_L / horn_R; orb + "
                          "ring rigid on orb; fore bands on .lower, hind bands on .paw (the segment they ring)" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics). Per frame: the trunk / head / ears / tail / orb are
#      posed, then every leg is solved by a planar (YZ) 2-bone IK to its paw target (rest + the clip's paw offset), the
#      paw bone keeping its world pitch; a floor guard lifts the whole body + targets if any vertex dips below z 0 (pass
#      1); keyed from the stored channels (pass 2, frame N+1 = frame 1); measured as the checker plays them (pass 3)
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
    amps, cyc, lag = spec[:3], spec[3], spec[4]
    for k in range(3):
        ang = amps[k] * math.sin(TAU * cyc * u + phase0 - k * lag)
        P["tail.%03d" % (k + 1)] = (None, [("Z", ang)], None)


def pulse(u, u0, n):
    return (0.5 + 0.5 * math.cos(TAU * (u - u0))) ** n     # a narrow periodic bump (integer harmonics)


def orb_pose(P, u, phase0):
    P["orb"] = ((0.0, 0.0, ORB_BOB[0] * math.sin(TAU * ORB_BOB[1] * u + phase0)), [], None)


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
    orb_pose(P, u, 0.0)
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
    on the floor and carried back at the stride speed, swing paws lifted; a small body bob, head counter-nod, tail sway,
    orb bob."""
    P = {}
    bob = WALK_BOB * math.sin(TAU * 2 * u)
    P["spine.001"] = ((0.0, 0.0, bob), [], None)
    P["head"] = (None, [("X", -1.0 * math.sin(TAU * 2 * u + 0.6))], None)
    for s, sg in SIDES:
        P["ear_" + s] = (None, [("X", -1.5 * math.sin(TAU * 2 * u - 0.6))], None)
    tail_pose(P, u, WALK_TAIL, 1.0)
    orb_pose(P, u, 1.2)
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
                     "tail_tip": list(pbs["tail.003"].tail), "head_tail": list(pbs["head"].tail),
                     "ear_L": list(pbs["ear_L"].tail), "orb": list(pbs["orb"].head),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("spine_z") - REST_H["spine.001"][2]
    tt_ = np.array([r["tail_tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_])
    el = np.array([r["ear_L"] for r in R_]); ob_ = np.array([r["orb"] for r in R_])
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
                    "ear_L_tip_p2p_xyz": (el.max(0) - el.min(0)).round(4).tolist(),
                    "orb_p2p_xyz": (ob_.max(0) - ob_.min(0)).round(4).tolist()}
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
rig["conquest_rig"] = ("eclipse_wolf v1: root > spine.001 > spine.002 > spine.003 > neck > head > ear_L / ear_R / horn_L / "
                       "horn_R / orb; spine.003 > foreleg_{L,R}.upper > .lower > .paw; spine.001 > hindleg_{L,R}.upper > "
                       ".lower > .paw; spine.001 > tail.001 > .002 > .003")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s: breathing (chest "
                               "rise, head level), small head nod / look, one ear twitch per ear per loop, tail sway "
                               "(travelling yaw wave), orb bob (%.2f sheet units, 2 per loop); paws held planted by a planar "
                               "2-bone IK. walk = 2 s 4-beat walk in place (lateral sequence LH, LF, RH, RF; stance 75%%; "
                               "stride %.2f, paw lift %.2f sheet units): stance paws planted on the floor and carried back, "
                               "swing paws lifted + curled; each leg cycles about its own rest paw (symmetric rest stance). "
                               "Paws never below z 0, root still." % (ORB_BOB[0], STRIDE, LIFT))
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
              "structure": "armature identity + skinned 'eclipse_wolf' (one opaque body material, Glow -> emission); "
                           "natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
