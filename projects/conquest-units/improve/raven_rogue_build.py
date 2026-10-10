"""Raven Rogue -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/raven_rogue_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)"
(section RAVEN ROGUE) + the sheet design/reference/raven_rogue-character-sheet.png (intake:
design/reference/raven_rogue/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. A chunky upright raven:
dark purple-navy plumage, cream chest bib, a LARGE curved dark-grey beak, heavy-lidded pale-gold eyes, a red slash scar,
a small fang, a swept crest, layered CAPE-like wings with ragged violet tips, short banded legs, clawed grey feet, a
short ragged tail. Unarmed (artist question R1 open). Clips PLACEHOLDER (movement intent not given: question N1).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. TRUE orthos FRONT + SIDE (intake ortho_frame.views):
  FRONT view (the unit's left = +X on image right):     X = (x_px - 214) / 100 ; z = (739 - y_px) / 100
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 995.5) / 100 ; z = (738 - y_px) / 100
(the intake axes / floor lines). Every constant below cites the view + px it was read from (PIL row scans of the
outline mask |rgb - bg| > 26 at 0.1 u steps; 4x crops for the face).

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the EGG body (z, front, back, half-width profile),
     the round HEAD ellipsoid smooth-unioned on top (one mass, a slight neck), two BROW ridges (the heavy lids), the
     swept CREST (4 blunt short round-cone clumps, deep smooth unions: fur language), a blunt dark FRINGE under the bib,
     two LEGS (thigh + shin round cones, two raised leg BANDS) + FEET (ankle knob, 3 front toes + a hind toe); clamped
     flat on the floor. Marching tets = the HIGH (bake source); collapse decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: part ownership (torso / legs), the cream BIB (a front-view
     outline in (x, z) restricted to the front half, its edge FRINGED by a rounded zig-zag), the leg bands (z cuts).
  3. PARTS (raven_rogue_parts.py, all closed): the BEAK (a loft along the side-view culmen / gonys curves, upper bill
     + darker lower mandible split on an edge loop), front-view DECALS cast onto the shell (eye rim, pale-gold eye
     clipped by the slanted LID line (the frown), pupil, lid band, the red SCAR slash), the FANG, 8 CLAWS, 3 TAIL
     feathers (ragged edges, violet tips), and the CAPE: per side three layered closed drape SLABS (mantle / middle /
     low) hanging from the shoulders, flaring out and back over a measured envelope, ragged rounded feather tips,
     overlapping feather steps, front edges tucked to the body, the colour grading dark -> mid -> violet on row loops.
  4. palette (palettes.py regions -> Col / Glow; no glow), Smart UV, bake normal + AO from the SDF high + parts.
  5. RIG: root > hips > body > head (beak, eyes, scar, fang rigid on head); body > wing.{L,R}.{0,1,2} (cape chains);
     hips > leg.{L,R}.{0,1,2} (2 = the foot, planted); hips > tail.{0,1}. Clips idle + walk = PLACEHOLDER. glb export.
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
import raven_rogue_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "raven_rogue"
CHAR_ID = "raven_rogue"               # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.32                       # BAND >= max smooth-union k (HEAD_K 0.25) + 2 steps (BUILD_CONTRACT kit hazard)
BODY_TRIS = 5600                      # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- the EGG body. Rows (z, front Y, back Y, half-width X). Front (bib) Y from the SIDE view outline: z 1.0 -> x 942
#   (-0.53), 1.4 -> 894 (-1.01), 1.8 -> 876 (-1.19), 2.2 -> 871 (-1.24), 2.6 -> 877 (-1.18), 3.0 -> 895 (-1.00); bottom
#   z 0.9 (side y 648: Y -0.41..+0.69). Back Y and half-width are hidden under the cape on every view: the FRONT view's
#   dark gap between the bib (x 152 / 276 @ y 539, -0.62..0.62) and the cape inner edge (x 142 / 286, +-0.72) bounds
#   the width from below; 0.97 keeps the cape off it.
EGG_PROFILE = [(0.80, -0.10, 0.30, 0.00), (0.86, -0.38, 0.58, 0.42), (0.98, -0.60, 0.76, 0.68), (1.20, -0.84, 0.86, 0.86),
               (1.50, -1.03, 0.90, 0.95), (1.80, -1.18, 0.90, 0.98), (2.10, -1.24, 0.88, 0.97), (2.40, -1.22, 0.84, 0.93),
               (2.70, -1.13, 0.78, 0.86), (3.00, -0.98, 0.70, 0.76), (3.25, -0.80, 0.60, 0.62), (3.40, -0.55, 0.40, 0.00)]
# ---- head (round, one mass with the body). FRONT view half-width: z 3.6 -> 1.05, 3.8 -> 1.09, 4.4 -> 0.92,
#   4.6 -> 0.80, 4.8 -> 0.58 (x 162/277 @ y 259), top y 246 (z 4.93); SIDE view: back of head x 1064 (Y +0.69) @ z 3.9-4.1,
#   forehead x 853 (Y -1.42) @ z 4.0
HEAD_C = (0.0, -0.35, 3.85)           # "head position"
HEAD_R = (1.08, 1.02, 1.08)           # "head size" (half width / depth / height)
HEAD_K = 0.25                         # "neck": smooth union head <-> body (a slight neck, one mass)
# ---- heavy lids: a soft brow ridge along each lid line (front-view lid line, see EYE_LID)
BROW_R = (0.28, 0.07, 0.06)           # "brow ridge" half-length along the lid / depth / height
BROW_LIFT, BROW_K = 0.05, 0.07       # ridge centre sits this far above the lid line; blend
# ---- crest: blunt soft clumps on the top-back of the head, swept back. FRONT view tips: x 222-229 @ y 209 (X 0.08-0.15,
#   z 5.3), x 191 / 253 @ y 229 (X -0.23 / +0.39, z 5.1); SIDE view tips: x 998-1009 @ y 210 (Y 0.03-0.14, z 5.28),
#   x 1018-1034 @ y 225 (Y 0.22-0.39, z 5.13)
CREST = [((0.00, -0.20, 4.70), (0.09, 0.02, 5.25), 0.19, 0.075),
         ((-0.22, -0.10, 4.66), (-0.27, 0.10, 5.03), 0.15, 0.065),
         ((0.24, -0.08, 4.66), (0.34, 0.14, 5.06), 0.15, 0.065),
         ((0.02, 0.20, 4.60), (0.05, 0.40, 5.00), 0.14, 0.060)]   # (root, BLUNT tip, root radius, tip radius)
CREST_K = 0.10                        # crest clumps melt deep into the head (soft, never spikes)
# ---- dark fringe under the bib (FRONT view: dark V between the legs, y 650-690 -> z 0.9-0.5 @ x +-0.3)
FRINGE = [(-0.24, 0.66), (0.0, 0.60), (0.24, 0.66)]   # (x, tip z): broad blunt clumps
FRINGE_Y, FRINGE_Z0, FRINGE_R, FRINGE_K = -0.40, 0.98, (0.17, 0.075), 0.10
# ---- legs (short, banded) + feet. FRONT view: shin x 255-281 @ y 699 (X 0.41..0.67, z 0.4); feet y 719-739 x 120-163
#   (X -0.94..-0.51) / 268-309; claws reach the floor at X +-0.53..+-0.96. SIDE view: legs drawn staggered (x 963-976 /
#   1034-1057, Y -0.3 / +0.5: the drawing's split stance) -> both legs at the mean Y 0.10
HIP = (0.50, 0.10, 1.10)              # thigh top (L side)
KNEE = (0.52, 0.10, 0.70)
ANKLE = (0.54, 0.08, 0.26)
LEG_R = (0.17, 0.125, 0.105)          # "leg thickness" (hip, knee, ankle)
LEG_K = 0.10
BANDS_Z, BAND_W, BAND_PROUD = (0.64, 0.46), 0.030, 0.016   # "leg bands": centre z (FRONT view lines y 675 / 693), half
                                                            #   width, how far they stand off the shin
FOOT_YAW = 22.0                       # "toe-out": the foot turns this far outward (FRONT: toes centred X +-0.74 vs shin 0.54)
TOE_SPREAD = (-32.0, 0.0, 32.0)       # 3 front toes, deg about the foot direction
TOE_LEN, TOE_R = 0.40, (0.066, 0.048)  # "toe length", radius (root, tip)
HIND_TOE = 0.14                       # hind toe length
TOE_K = 0.05
CLAW_LEN, CLAW_R = 0.13, 0.042        # "claw length" / base radius (dark grey closed cones curving to the floor)
# ---- beak: SIDE view culmen (top) + gonys (bottom) curves from the root (buried in the head) to the hooked tip
#   (4x crop): top x 902 @ y 354 (Y -0.93 buried), 873 @ 354 (-1.21, z 3.84), 838 @ 362 (-1.58, 3.755), 812 @ 385 (-1.83,
#   3.53), 797 @ 415 (-1.98, 3.23), 793 @ 445 (-2.03, 2.93); tip 795 @ 486 (-2.005, 2.52); bottom 885 @ 440 (-1.10, 2.98)
#   -> 855 @ 450 (-1.40, 2.85), 817 @ 463 (-1.78, 2.63). FRONT view: widest x 180-250 @ y 382 (+-0.34, z 3.57)
BEAK_TOP = [(-0.85, 3.86), (-1.21, 3.84), (-1.58, 3.755), (-1.83, 3.53), (-1.98, 3.23), (-2.03, 2.93), (-2.005, 2.52)]
BEAK_BOT = [(-0.85, 3.00), (-1.10, 2.90), (-1.40, 2.80), (-1.78, 2.66), (-1.95, 2.56), (-2.005, 2.52)]
BEAK_W = [(0.0, 0.33), (0.18, 0.34), (0.40, 0.30), (0.62, 0.22), (0.82, 0.12), (0.95, 0.05), (1.0, 0.0)]   # "beak width"
                                                                          #   (arc fraction, half-width)
BEAK_ST, BEAK_RING = 22, 16
BEAK_LOW_SIN = -0.30                  # the lower mandible (darker) = the section below this (sin of the ring angle)
# ---- face decals, laid out in FRONT-view coords (x, z) and cast along +Y onto the shell (L side; R mirrored)
#   4x crop of the front face: eye white x 236-280 @ y 320-367 (X 0.22..0.66, z 4.19..3.72); lid line from x 225 @ y 341
#   (X 0.11, z 3.98, inner, LOW: the frown) to x 285 @ y 317 (X 0.71, z 4.22); pupil x 240-251 @ y 344-361
EYE_C, EYE_AB = (0.43, 3.94), (0.22, 0.235)   # "eye position" / "eye size" (half width, half height)
EYE_LID = ((0.10, 3.975), (0.74, 4.225))      # "lid line" (inner end, outer end): the eye is clipped below it
PUPIL_C, PUPIL_AB = (0.305, 3.875), (0.058, 0.088)   # "pupil": near the inner corner, partly under the lid
RIM_GROW = 0.032                      # dark rim round the eye
LID_W = 0.030                         # "lid line" thickness (half)
LID_TRIM = 0.03                       # the lid line stops this short of the lid ends (no tail past the head curve)
SCAR = ((0.27, 4.62), (0.66, 3.48), 0.05)    # "scar": slash ends (x, z) + half width (FRONT x 241 @ y 277 -> x 280 @
                                              #   y 391, 7-9 px wide), on the unit's LEFT (+X)
FANG = ((0.52, 3.67), 0.115, 0.036)   # "fang": root (x, z) at the beak corner (FRONT x 264-268 @ y 372-382), length,
                                      #   base radius
DECAL = {"rim": (0.010, 0.008), "eye": (0.016, 0.020), "pupil": (0.031, 0.010), "lid": (0.040, 0.006),
         "scar": (0.004, 0.003)}       # per decal (lift off the shell, dome height): stacked so each sits on the last
DECAL_DN = 0.03                       # decal skins sink this far under the shell
# ---- bib (cream, front-view outline): half-width per z. FRONT view: bottom y 648 (z 0.91, x +-0.38), z 1.2 -> +-0.80,
#   1.6 -> 0.71, 2.0 -> 0.62, 2.5 -> 0.73, 2.8 -> 0.48 (beak covers the centre), 3.2 -> 0.40, cheeks 3.5 -> 0.74,
#   3.6 -> 0.78, top under the eyes z 3.72
BIB_W = [(0.90, 0.30), (0.95, 0.46), (1.05, 0.64), (1.20, 0.80), (1.40, 0.78), (1.60, 0.73), (2.00, 0.66), (2.50, 0.72),
         (2.75, 0.56), (3.00, 0.48), (3.20, 0.46), (3.38, 0.64), (3.55, 0.76), (3.66, 0.74), (3.74, 0.60)]
BIB_Z = (0.91, 3.74)                  # bib bottom / top edge
BIB_FRINGE = (15, 0.05)               # "bib fringe": soft rounded scallops round the edge (count, depth)
BIB_SOFT = 0.08                       # the bib outline's corners round off over this (smooth minimum)
BIB_REFINE = (0.035, 1)               # "bib edge finish": shell faces within this of the bib edge are split (passes)
                                      #   and the new vertices laid back on the SDF high, so the cut follows the scallops
BIB_SNAP = 0.06                       # the bib cut's own snap (v1 used CUT_SNAP 0.18: crossings jumped to vertices ->
                                      #   the stair steps the v1 gate saw)
# ---- CAPE (layered wings). Envelope = the outer surface radius r from the cape axis (0, CAPE_AY) per azimuth theta
#   (deg from the front toward each side) and z. theta 90 = the FRONT-view silhouette (x 8/420 @ y 559, +-2.06 @ z 1.8;
#   +-1.62 @ z 2.6; +-1.35 @ z 3.0; +-1.79 @ z 1.0); theta 150-180 = the SIDE-view back silhouette (x 1218 @ y 448,
#   Y 2.23 @ z 2.9; Y 2.0 @ z 1.8; 1.75 @ z 1.4) via Y = CAPE_AY - r cos(theta), X = r sin(theta)
CAPE_AY = -0.15
CAPE_ENV = {60.0: [(0.3, 1.0), (1.0, 1.0), (1.4, 1.1), (1.8, 1.18), (2.2, 1.2), (2.6, 1.18), (3.0, 1.12), (3.3, 1.08), (3.6, 1.05)],
            90.0: [(0.3, 1.45), (0.6, 1.48), (1.0, 1.52), (1.4, 1.58), (1.8, 1.62), (2.2, 1.52), (2.6, 1.38), (3.0, 1.20),
                   (3.25, 1.13), (3.6, 1.08)],
            120.0: [(0.3, 1.58), (0.6, 1.64), (1.0, 1.70), (1.4, 1.76), (1.8, 1.80), (2.2, 1.72), (2.6, 1.55), (2.9, 1.38),
                    (3.2, 1.24), (3.6, 1.05)],
            150.0: [(0.3, 1.68), (0.6, 1.78), (1.0, 1.92), (1.4, 2.05), (1.8, 2.2), (2.2, 2.35), (2.5, 2.45), (2.9, 2.42),
                    (3.1, 1.96), (3.3, 1.21), (3.6, 0.95)],
            180.0: [(0.3, 1.40), (0.6, 1.50), (1.2, 1.75), (1.8, 1.92), (2.4, 2.02), (2.9, 1.92), (3.1, 1.5), (3.3, 1.0),
                    (3.6, 0.85)]}
# per layer: top z, bottom z per theta (before the ragged tips), front-edge theta (top row, bottom row), radial inset,
# ragged tips (count, extra drop), seed, colour grade rows, flare share (of CAPE_FLARE). FRONT-view tips: z 2.6 / 2.2 (mantle), 1.8 / 1.3 (middle), 1.0 / 0.6 / 0.4 (low)
CAPE_LAYERS = {"A": {"top": 3.55, "bot": {60.0: 2.55, 95.0: 2.85, 130.0: 2.70, 180.0: 2.55}, "th": (60.0, 64.0),
                     "off": 0.0, "tips": 5, "rag": 0.80, "seed": 1.0, "grade": (0.50, 0.78), "flare": 0.45},
               "B": {"top": 3.15, "bot": {60.0: 1.55, 100.0: 2.05, 140.0: 1.80, 180.0: 1.70}, "th": (68.0, 84.0),
                     "off": 0.07, "tips": 6, "rag": 0.85, "seed": 2.0, "grade": (0.32, 0.58), "flare": 0.85},
               "C": {"top": 2.60, "bot": {70.0: 0.80, 105.0: 0.98, 140.0: 1.25, 180.0: 1.35}, "th": (80.0, 96.0),
                     "off": 0.14, "tips": 5, "rag": 0.85, "seed": 3.0, "grade": (0.22, 0.46), "flare": 0.6}}
CAPE_COLS_PER_TIP = 6                 # "cape detail": columns per ragged feather tip
CAPE_ROWS = [0.0, 0.08, 0.20, 0.33, 0.45, 0.56, 0.67, 0.78, 0.89, 1.0]   # row loops down each layer; 'grade' per
                                      #   layer = (dark -> mid, mid -> violet) row fractions (the 'violet grading')
CAPE_FLARE = (0.12, 0.42, 100.0, 45.0)   # "cape flare": each layer fans outward toward its tips by (base + side bump
                                      #   at theta centre / half-width deg) x v^2: the side feathers splay out most
CAPE_DARK_FEATHERS = 0.12             # "slight colour variation": this share of feathers runs one tone darker (whole feathers)
CAPE_BACK_DARK = 160.0                # feathers centred behind this azimuth (the back centre) run one tone darker
CAPE_TIP_VAR = 0.45                   # "torn": the shortest tip is this share of the longest (lengths vary per feather)
CAPE_SIDE_TIPS = (110.0, 40.0, 0.35)  # the longest tips hang at the sides (theta centre, half-width deg); tips elsewhere
                                      #   are shortened by up to this share (FRONT view: the low tips at X +-1.7, z 0.4-0.6)
CAPE_TIP_POINT = 1.25                 # "tip shape": > 1 = pointed feather tips with a softly rounded apex
CAPE_THICK = (0.045, 0.024)           # slab thickness (top, tips)
CAPE_CLEAR = 0.05                     # the cape's inner skin keeps this clear of the body
CAPE_TUCK = 0.12                      # "front-edge tuck": the first share of each layer curls in to the body
CAPE_OVERLAP = 0.035                  # "feather overlap": each feather's back edge stands this proud of the next
CAPE_TORN = 0.35                      # "torn tips": depth of the small notch in every other tip (share of its drop)
CAPE_TOP_BURY = 0.03                  # the mantle's top row is buried this far into the neck
CAPE_RB_ZMIN = 1.0                    # below this height the cape clears the body as it does at this height
# ---- tail (short, ragged): SIDE view: from under the cape at x 1100 @ y 625 (Y 1.0, z 1.1) to tips x 1212-1236 @
#   y 638-678 (Y 2.17-2.40, z 1.0-0.6); 3 feathers fanned
TAIL_ROOT, TAIL_MID, TAIL_TIP = (0.0, 0.55, 1.18), (0.0, 1.50, 1.00), (0.0, 2.42, 0.74)
TAIL_FAN = [(-6.0, 0.92, 4.0), (5.0, 1.0, 5.0), (15.0, 0.82, 6.0)]   # (pitch deg: + = tip up, length scale, seed);
                                      #   the fan spreads in the vertical plane (the side view's ragged fan)
TAIL_HW, TAIL_T = 0.20, 0.035         # "tail feather width" (half) / thickness (half)
TAIL_TIP_S = 0.62                     # violet from this fraction of the feather
TAIL_ST, TAIL_RING = 16, 8
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_FLOOR = {"plumage": 0.55, "bib_face": 0.60, "decal_beak": 0.70}   # "AO floor" per region class (house style: no
                                      #   black grime; v1.1 gate: v1 visible AO mean 0.41, p05 0.0)
AO_CLASS = {"plumage": ["plumage", "cape", "cape_mid", "cape_tip", "leg", "leg_band", "claw"],
            "bib_face": ["bib"],
            "decal_beak": ["beak", "beak_low", "eye", "pupil", "lid", "scar", "fang"]}
AO_HIDDEN_DIST = 0.12                 # a face whose normal ray hits the mesh this close is hidden (not floored data)
# ---- rig (weight bands, z)
HIP_BAND = (1.05, 1.50)               # hips -> body blend on the egg
HEAD_BAND = (2.95, 3.40)              # body -> head blend
FOOT_BAND = (0.20, 0.32)              # foot -> shin blend on the legs
KNEE_BAND = (0.62, 0.78)              # shin -> thigh
LEG_TOP = (0.88, 1.08)                # thigh -> hips
OWN_TAU = 0.05                        # soft part ownership for weights
WING_BONE_TH = 125.0                  # the cape chain runs down this azimuth
WING_Z = (3.50, 2.50, 1.55, 0.60)     # cape chain joint heights
CAPE_ZTOP = 3.55                      # cape weight arc = drop below this
# ---- clips (PLACEHOLDER: the artist has not given the movement intent, question N1; 24 fps; integer harmonics)
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.012                        # "breathing": body scale +- (2 breaths per loop)
HEAD_TILT = (3.0, 1.2)                # "head tilt": roll deg (1 cyc) / nod deg (2 cyc)
CAPE_IDLE = {"Y": (0.8, 3.5, 2, 0.6), "X": (0.6, 2.5, 3, 0.7)}   # "cape ruffle" (root deg, tip deg, cyc, lag per bone)
TAIL_IDLE = (2.0, 4.0, 2, 0.8)        # tail flick (root, tip deg, cyc, lag)
WALK_N = 48                           # "strut loop": 2 s = two hops
HOP = 0.09                            # "hop height": the hips lift this much over each step (never below rest)
STRUT_SWING = 16.0                    # the swing leg reaches forward (deg at the thigh)
STRUT_LIFT = 26.0                     # the swing shin folds back to lift the foot (deg)
STRUT_BACK = 5.0                      # the stance thigh pushes back (deg)
STRUT_LEAN = 5.0                      # forward lean of the body (deg)
STRUT_ROLL = 3.0                      # body roll over the stance foot (deg)
HEAD_PUMP = (0.05, 3.0)               # "head pump": forward thrust (units) + nod (deg) per step (the bird strut)
CAPE_BOUNCE = (2.0, 6.0, 0.06)        # cape lag on the hop: root, tip deg, lag per bone (loop fraction)
TAIL_BOB = 5.0                        # tail counter-bob deg
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
          "design/reference/raven_rogue-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (front + side); floor z = 0 at the soles",
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
_eg = SD.catmull(np.array(EGG_PROFILE, float), 10)
_eg = _eg[np.argsort(_eg[:, 0], kind="stable")]
EZ = _eg[:, 0]
EF, EB = _eg[:, 1], np.maximum(_eg[:, 2], _eg[:, 1])
EYC = 0.5 * (EF + EB)
ERY = np.clip(0.5 * (EB - EF), 0.0, None)
ERX = np.clip(_eg[:, 3], 0.0, None)
ERX[0] = ERX[-1] = ERY[0] = ERY[-1] = 0.0


def egg_at(z):
    return (np.interp(z, EZ, EYC), np.interp(z, EZ, ERY, left=0.0, right=0.0), np.interp(z, EZ, ERX, left=0.0, right=0.0))


def sd_egg(P):
    yc, ry, rx = egg_at(P[:, 2])
    q = np.hypot(P[:, 0] / np.maximum(rx, 1e-3), (P[:, 1] - yc) / np.maximum(ry, 1e-3))
    d = (q - 1.0) * np.maximum(0.5 * (rx + ry), 1e-3)
    return np.maximum(d, np.maximum(EZ[0] - P[:, 2], P[:, 2] - EZ[-1]))


HC, HR = np.array(HEAD_C, float), np.array(HEAD_R, float)


def sd_head(P):
    return SD.sd_ellipsoid(P, HC, HR)


def head_front_y(x, z):
    """the head ellipsoid's front surface Y at front-view (x, z)"""
    q = 1.0 - (x / HR[0]) ** 2 - ((z - HC[2]) / HR[2]) ** 2
    return HC[1] - HR[1] * math.sqrt(max(q, 0.0))


def mir(p, sg):
    return np.array([sg * p[0], p[1], p[2]], float)


SIDES = (("L", 1.0), ("R", -1.0))
BROWS = []
for s, sg in SIDES:
    l0 = np.array(EYE_LID[0]); l1 = np.array(EYE_LID[1])
    mid = 0.5 * (l0 + l1)
    d2 = unit(l1 - l0)
    nz = np.array([-d2[1], d2[0]])                                  # up off the lid line (front plane)
    cx, cz = mid + nz * BROW_LIFT
    c = np.array([sg * cx, head_front_y(cx, cz) + 0.02, cz])
    ax = unit([sg * d2[0], 0.0, d2[1]])
    ay = np.array([0.0, 1.0, 0.0])
    az = unit(np.cross(ax, ay))
    BROWS.append((c, np.array([ax, ay, az])))


def sd_brows(P):
    return np.minimum(*[SD.sd_ellipsoid(P, c, BROW_R, frame=fr) for c, fr in BROWS])


def cones_sdf(P, cones):
    d = np.full(len(P), 1e9)
    for a, b, r1, r2 in cones:
        d = np.minimum(d, SD.sd_round_cone(P, a, b, r1, r2))
    return d


CREST_CONES = [(np.array(a, float), np.array(b, float), r1, r2) for a, b, r1, r2 in CREST]
FRINGE_CONES = [(np.array([x, FRINGE_Y, FRINGE_Z0]), np.array([1.1 * x, FRINGE_Y - 0.06, zt]), FRINGE_R[0], FRINGE_R[1])
                for x, zt in FRINGE]


def sd_torso(P, extras=True):
    d = np.minimum(sd_egg(P), sd_head(P))
    if extras:
        d = np.minimum(d, np.minimum(sd_brows(P), np.minimum(cones_sdf(P, CREST_CONES), cones_sdf(P, FRINGE_CONES))))
    return d


LEG = {}
for s, sg in SIDES:
    toes = []
    base = mir(ANKLE, sg) + np.array([0.0, 0.0, -0.12])
    for a_ in TOE_SPREAD:
        a = math.radians(FOOT_YAW + a_)
        d = np.array([sg * math.sin(a), -math.cos(a), 0.0])
        tip = base + d * TOE_LEN
        tip[2] = TOE_R[1]
        toes.append((base, tip, TOE_R[0], TOE_R[1], d))
    a = math.radians(FOOT_YAW)
    d = -np.array([sg * math.sin(a), -math.cos(a), 0.0])
    tip = base + d * HIND_TOE; tip[2] = TOE_R[1]
    toes.append((base, tip, TOE_R[0] * 0.9, TOE_R[1] * 0.95, d))
    bands = []
    for zc in BANDS_Z:
        t_ = (KNEE[2] - zc) / (KNEE[2] - ANKLE[2])
        c = mir(KNEE, sg) + (mir(ANKLE, sg) - mir(KNEE, sg)) * t_
        r = LEG_R[1] + (LEG_R[2] - LEG_R[1]) * t_ + BAND_PROUD
        bands.append((c + np.array([0, 0, BAND_W]), c - np.array([0, 0, BAND_W]), r, r))
    LEG[s] = {"thigh": (mir(HIP, sg), mir(KNEE, sg), LEG_R[0], LEG_R[1]),
              "shin": (mir(KNEE, sg), mir(ANKLE, sg), LEG_R[1], LEG_R[2]),
              "ankle": (mir(ANKLE, sg) + np.array([0, 0, -0.06]), LEG_R[2] * 1.05),
              "toes": toes, "bands": bands}


def sd_leg(P, s):
    L_ = LEG[s]
    d = np.minimum(SD.sd_round_cone(P, *L_["thigh"]), SD.sd_round_cone(P, *L_["shin"]))
    d = np.minimum(d, SD.sd_sphere(P, *L_["ankle"]))
    d = np.minimum(d, cones_sdf(P, [t[:4] for t in L_["toes"]]))
    d = np.minimum(d, cones_sdf(P, L_["bands"]))
    return d


GROUPS = ["torso", "leg.L", "leg.R"]
GROUP_REGION = {"torso": "plumage", "leg.L": "leg", "leg.R": "leg"}


def group_sdf(P):
    return np.stack([sd_torso(P), sd_leg(P, "L"), sd_leg(P, "R")], 1)


def cone_box(a, b, r):
    return np.minimum(a, b) - r, np.maximum(a, b) + r


g = SD.Grid((-1.25, -1.55, -0.05), (1.25, 1.05, 5.40), SDF_STEP, SDF_BAND)
g.apply(sd_egg, (-1.0, -1.3, 0.78), (1.0, 0.95, 3.42), 0.0)
g.apply(sd_head, HC - HR, HC + HR, HEAD_K)
for c, fr in BROWS:
    g.apply(lambda P, c=c, fr=fr: SD.sd_ellipsoid(P, c, BROW_R, frame=fr), c - max(BROW_R), c + max(BROW_R), BROW_K)
for a, b, r1, r2 in CREST_CONES:
    g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), *cone_box(a, b, r1), CREST_K)
for a, b, r1, r2 in FRINGE_CONES:
    g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), *cone_box(a, b, r1), FRINGE_K)
for s, _ in SIDES:
    L_ = LEG[s]
    g.apply(lambda P, s=s: SD.sd_round_cone(P, *LEG[s]["thigh"]), *cone_box(L_["thigh"][0], L_["thigh"][1], LEG_R[0]), LEG_K)
    g.apply(lambda P, s=s: SD.sd_round_cone(P, *LEG[s]["shin"]), *cone_box(L_["shin"][0], L_["shin"][1], LEG_R[1]), LEG_K)
    for a, b, r1, r2 in L_["bands"]:
        g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), *cone_box(a, b, r1), 0.012)
    c_, r_ = L_["ankle"]
    g.apply(lambda P, c_=c_, r_=r_: SD.sd_sphere(P, c_, r_), c_ - r_, c_ + r_, TOE_K)
    for a, b, r1, r2, _d in L_["toes"]:
        g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), *cone_box(a, b, r1), TOE_K)
g.floor(0.0)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "max_k": max(HEAD_K, CREST_K, LEG_K, BROW_K), "grid": g.n.tolist(),
                 "ops": len(g.ops), "high_tris": len(HF), "high_verts": len(HV), "crumbs": crumbs,
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
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_low": [LV.min(0).round(4).tolist(), LV.max(0).round(4).tolist()],
                    "bbox_high": [HV.min(0).round(4).tolist(), HV.max(0).round(4).tolist()],
                    "seconds": round(time.time() - t, 1)}
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))


# =========================================================================== 3. region fields + iso cuts on the shell
def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


_bw = np.array(BIB_W, float)


def bib_field(P):
    """> 0 on the cream bib: inside the FRONT-view outline (half-width BIB_W(z), BIB_Z bottom / top) on the front half
    of the body / head, the edge a soft row of rounded SCALLOPS (v1.1 gate: the v1 zig-zag teeth cut into pixel
    steps). The outline's corners are rounded by smooth minima (BIB_SOFT) so the iso line has no kinks."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    w = np.interp(z, _bw[:, 0], _bw[:, 1])
    f = SD.smin(w - np.abs(x), SD.smin((z - BIB_Z[0]) * 0.8, (BIB_Z[1] - z) * 0.8, BIB_SOFT), BIB_SOFT)
    yc = np.where(z < 3.0, egg_at(z)[0], HC[1])
    f = SD.smin(f, (yc - y) * 1.5, BIB_SOFT)
    phi = np.arctan2(z - 2.3, np.abs(x) + 1e-9)                    # angle round the bib centre: scallop spacing
    fr = (phi / math.pi * BIB_FRINGE[0]) % 1.0
    scal = np.sqrt(np.clip(1.0 - (2.0 * fr - 1.0) ** 2, 0.0, 1.0)) - (math.pi / 4.0)   # round bumps, zero mean
    return f + BIB_FRINGE[1] * scal


DGR = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["bib"] = bib_field(LV)
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


def iso_cut(key, tau, gate=None, snap=None):
    L = LAY[key]
    snap = CUT_SNAP if snap is None else snap
    eps = 1e-5 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < snap:
                a[L] = tau
            elif tt > 1 - snap:
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


def refine_band(key, w, passes):
    """split every torso face the field `key` crosses within +-w (edge midpoints), lay the new vertices on the SDF high
    (nearest point) and recompute every field from the new positions"""
    added = 0
    for _ in range(passes):
        n0 = len(bm.verts)
        bm.edges.index_update()
        ed = set()
        for f in bm.faces:
            vals = [v[LAY[key]] for v in f.verts]
            if min(vals) < w and max(vals) > -w and all(v[LAY["f_torso"]] > -TOL for v in f.verts):
                ed.update(f.edges)
        if not ed:
            break
        bmesh.ops.subdivide_edges(bm, edges=sorted(ed, key=lambda e: e.index), cuts=1, use_grid_fill=True)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bm.verts.ensure_lookup_table(); bm.verts.index_update(); bm.edges.index_update(); bm.faces.index_update()
        for v in bm.verts[n0:]:
            v.co = bvh_hi.find_nearest(v.co)[0]
        P = np.array([v.co[:] for v in bm.verts])
        D = group_sdf(P)
        vals = {"f_" + gname: own_field(D, j) for j, gname in enumerate(GROUPS)}
        vals["bib"] = bib_field(P); vals["z"] = P[:, 2]
        for v in bm.verts:
            for k_, arr in vals.items():
                v[LAY[k_]] = float(arr[v.index])
        added += len(bm.verts) - n0
    return added


TOL = 0.01
REFINED = refine_band("bib", BIB_REFINE[0], BIB_REFINE[1])
in_torso = lambda a, b: a[LAY["f_torso"]] > -TOL and b[LAY["f_torso"]] > -TOL
in_legs = lambda a, b: a[LAY["f_torso"]] < TOL and b[LAY["f_torso"]] < TOL
CUTS = [("f_leg.L", 0.0, None, None), ("f_leg.R", 0.0, None, None), ("bib", 0.0, in_torso, BIB_SNAP)]
for zc in BANDS_Z:
    CUTS += [("z", zc - BAND_W, in_legs, None), ("z", zc + BAND_W, in_legs, None)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_, sn_) for k_, tau_, gate_, sn_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array([GROUP_REGION[GROUPS[o]] for o in owner], dtype=object)
is_torso = owner == GROUPS.index("torso")
shell_reg[is_torso & (FVAL["bib"] > 0)] = "bib"
in_band = np.zeros(len(CF), bool)
for zc in BANDS_Z:
    in_band |= np.abs(FVAL["z"] - zc) < BAND_W
shell_reg[~is_torso & in_band] = "leg_band"
report["iso_cuts"] = {"bib_refine_new_verts": REFINED, "cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def torso_normal(P, h=0.004):
    P = np.atleast_2d(np.asarray(P, float))
    gr = np.stack([(sd_torso(P + e) - sd_torso(P - e)) / (2 * h) for e in np.eye(3) * h], 1)
    return gr / np.maximum(np.linalg.norm(gr, axis=1, keepdims=True), 1e-12)


def cast_front(pts2):
    """front-view (x, z) points -> shell hits along +Y + smooth SDF normals"""
    H, N = [], []
    for x, z in np.asarray(pts2, float):
        loc, nrm, idx, dd = bvh_c.ray_cast(Vector((x, -6.0, z)), Vector((0.0, 1.0, 0.0)), 12.0)
        if loc is None:
            loc, nrm, idx, dd = bvh_c.find_nearest(Vector((x, head_front_y(x, z), z)))
        H.append(np.array(loc))
    H = np.array(H)
    return H, torso_normal(H)


PARTS = []
FACE = {}


def mirror2(p, sg):
    return (sg * p[0], p[1])


for s, sg in SIDES:
    lid = (*mirror2(EYE_LID[0], sg), *mirror2(EYE_LID[1], sg))
    ec = mirror2(EYE_C, sg)
    V, F, R = FP.front_decal(ec, EYE_AB[0] + RIM_GROW, EYE_AB[1] + RIM_GROW, 0.0, cast_front, *DECAL["rim"][:1], DECAL_DN,
                             DECAL["rim"][1], 3, 28, "lid", clip=(lid[0], lid[1] + RIM_GROW * 0.5, lid[2], lid[3] + RIM_GROW * 0.5))
    PARTS.append(FP.Part("eye_rim." + s, V, F, R, bone="head"))
    V, F, R = FP.front_decal(ec, EYE_AB[0], EYE_AB[1], 0.0, cast_front, DECAL["eye"][0], DECAL_DN, DECAL["eye"][1], 3, 28,
                             "eye", clip=lid)
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    V, F, R = FP.front_decal(mirror2(PUPIL_C, sg), PUPIL_AB[0], PUPIL_AB[1], 0.0, cast_front, DECAL["pupil"][0], DECAL_DN,
                             DECAL["pupil"][1], 2, 16, "pupil", clip=lid)
    PARTS.append(FP.Part("pupil." + s, V, F, R, bone="head"))
    l0, l1 = np.array(lid[:2]), np.array(lid[2:])
    ang = math.degrees(math.atan2(l1[1] - l0[1], l1[0] - l0[0]))
    V, F, R = FP.front_decal(tuple(0.5 * (l0 + l1)), 0.5 * np.linalg.norm(l1 - l0) - LID_TRIM, LID_W, ang, cast_front,
                             DECAL["lid"][0], DECAL_DN, DECAL["lid"][1], 2, 24, "lid", p=1.6)
    PARTS.append(FP.Part("lid." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"centre_xz": list(ec), "lid": list(lid)}
s0, s1, sw = np.array(SCAR[0]), np.array(SCAR[1]), SCAR[2]
V, F, R = FP.front_decal(tuple(0.5 * (s0 + s1)), 0.5 * np.linalg.norm(s1 - s0), sw,
                         math.degrees(math.atan2(s1[1] - s0[1], s1[0] - s0[0])), cast_front, DECAL["scar"][0], DECAL_DN,
                         DECAL["scar"][1], 8, 28, "scar", p=1.3)     # 8 rings: the long slash must not chord under the shell
PARTS.append(FP.Part("scar", V, F, R, bone="head"))
FACE["scar"] = {"ends_xz": [list(SCAR[0]), list(SCAR[1])], "side": "L (+X)"}
# fang: a small closed cone hanging from the cheek at the beak corner
fh, fn = cast_front([FANG[0]])
fh, fn = fh[0], fn[0]
f_root = fh - fn * 0.03
f_tip = fh + np.array([0.0, -0.025, -FANG[1]]) + fn * 0.01
f_sp = np.array([f_root + (f_tip - f_root) * t_ for t_ in (0.0, 0.2, 0.55, 1.0)])
fw = np.array([0.0, FANG[2], FANG[2] * 0.62, 0.0])
V, F, R, _ = FP.loft(f_sp, (0.0, -1.0, 0.0), fw, fw, fw * 0.8, 8, regions=lambda th, s: "fang")
PARTS.append(FP.Part("fang", V, F, R, bone="head"))
FACE["fang"] = {"root": fh.round(4).tolist()}
# beak: loft between the culmen (top) and gonys (bottom) curves of the side view
_bt = SD.resample(np.array([[0.0, y, z] for y, z in BEAK_TOP]), BEAK_ST)
_bb = SD.resample(np.array([[0.0, y, z] for y, z in BEAK_BOT]), BEAK_ST)
B_SP = 0.5 * (_bt + _bb)
B_UP = _bt - _bb
B_HT = 0.5 * np.linalg.norm(B_UP, axis=1)
_bs = SD.arclen(B_SP); _bs = _bs / _bs[-1]
_bwt = np.array(BEAK_W, float)
B_HW = np.interp(_bs, _bwt[:, 0], _bwt[:, 1])
B_HW[0] = B_HW[-1] = 0.0
B_UP[-1] = B_UP[-2]
V, F, R, _ = FP.loft(B_SP, B_UP, B_HW, B_HW, np.maximum(B_HT, 1e-3), BEAK_RING,
                     regions=lambda th, s: "beak_low" if math.sin(th) < BEAK_LOW_SIN else "beak")
PARTS.append(FP.Part("beak", V, F, R, bone="head"))
BEAK_TIP = B_SP[-1].copy()
# claws: one per toe, from inside the toe tip curving forward-down to the floor
for s, sg in SIDES:
    for k, (a, b, r1, r2, d) in enumerate(LEG[s]["toes"]):
        ln = CLAW_LEN * (0.7 if k == 3 else 1.0)
        sp = np.array([b - d * 0.03, b + d * 0.03 * ln / CLAW_LEN, b + d * 0.08 * ln / CLAW_LEN + np.array([0, 0, -0.012]),
                       b + d * ln + np.array([0, 0, -(r2 - 0.006)])])
        cw = np.array([0.0, CLAW_R, CLAW_R * 0.7, 0.0]) * (0.85 if k == 3 else 1.0)
        V, F, R, _ = FP.loft(sp, (0.0, 0.0, 1.0), cw, cw, cw, 6, regions=lambda th, s_: "claw")
        PARTS.append(FP.Part("claw.%s.%d" % (s, k), V, F, R, bone="leg.%s.2" % s))
# tail feathers: ragged, violet tips, fanned about the root
_tr, _tm, _tt = (np.array(v, float) for v in (TAIL_ROOT, TAIL_MID, TAIL_TIP))
TAIL_SPINE = np.array([_tr, _tm, _tt])
for k, (yaw, ls, seed) in enumerate(TAIL_FAN):
    Rz = np.array(Matrix.Rotation(math.radians(yaw), 3, "X"))
    tt_ = np.linspace(0, 1, TAIL_ST)[:, None]
    sp = (1 - tt_) ** 2 * _tr + 2 * (1 - tt_) * tt_ * _tm + tt_ ** 2 * _tt
    sp = _tr + ((sp - _tr) * ls) @ Rz.T
    al = SD.arclen(sp); sf = al / al[-1]
    shape = np.sin(math.pi * np.clip(0.15 + 0.85 * sf, 0, 1) ** 0.9)
    shape[0] = 0.0; shape[-1] = 0.0
    hl = TAIL_HW * shape * FP.soft_fringe(sf, 0.45, 0.98, 3, (0.15, 0.30), (0.03, 0.06), 11.0 + seed)
    hr = TAIL_HW * shape * FP.soft_fringe(sf, 0.45, 0.98, 3, (0.15, 0.30), (0.03, 0.06), 23.0 + seed)
    V, F, R, S_ = FP.loft(sp, (1.0, 0.0, 0.0), hl, hr, TAIL_T * np.sqrt(np.clip(shape, 0, 1)), TAIL_RING,
                          regions=lambda th, s_: "cape_tip" if s_ > TAIL_TIP_S else "cape")
    p = FP.Part("tail.%d" % k, V, F, R)
    p.chain = ("tail", S_ * al[-1] * (SD.arclen(TAIL_SPINE)[-1] / al[-1]), float(SD.arclen(TAIL_SPINE)[-1]), "hips", 2, 0.0)
    PARTS.append(p)


# ---- CAPE: per side three layered closed drape slabs
def interp_theta(knots, th):
    ks = sorted(knots)
    th = np.clip(np.asarray(th, float), ks[0], ks[-1])
    out = np.zeros_like(th)
    for a, b in zip(ks[:-1], ks[1:]):
        m = (th >= a) & (th <= b)
        w = 0.5 - 0.5 * np.cos(math.pi * (th[m] - a) / (b - a))       # smooth (cosine) blend between knots
        out[m] = (1 - w) * knots[a] + w * knots[b]
    return out


def r_env(TH, Z):
    vals = {k: np.interp(Z, [p[0] for p in v], [p[1] for p in v]) for k, v in CAPE_ENV.items()}
    ks = sorted(vals)
    th = np.clip(TH, ks[0], ks[-1])
    out = np.zeros_like(th)
    for a, b in zip(ks[:-1], ks[1:]):
        m = (th >= a) & (th <= b)
        w = 0.5 - 0.5 * np.cos(math.pi * (th[m] - a) / (b - a))
        out[m] = (1 - w) * vals[a][m] + w * vals[b][m]
    return out


def body_r(TH, Z, sg):
    """distance from the cape axis to the torso surface (egg + head, no extras) along azimuth TH at height Z (0 where
    the axis point is outside the body)"""
    th = np.radians(TH.ravel()); z = Z.ravel()
    d = np.stack([sg * np.sin(th), -np.cos(th), np.zeros_like(th)], 1)
    o = np.stack([np.zeros_like(z), np.full_like(z, CAPE_AY), z], 1)
    inside0 = sd_torso(o, extras=False) < 0
    lo, hi = np.zeros(len(z)), np.full(len(z), 3.5)
    for _ in range(32):
        mid = 0.5 * (lo + hi)
        ins = sd_torso(o + d * mid[:, None], extras=False) < 0
        lo = np.where(ins, mid, lo); hi = np.where(ins, hi, mid)
    return np.where(inside0, lo, 0.0).reshape(TH.shape)


def tip_extra(u, n, rag, seed, th_tip):
    ex = np.zeros_like(u)
    for k in range(n):
        c = (k + 0.5) / n
        w = 0.5 / n
        t_ = (u - c) / w
        side = math.exp(-((float(np.interp(c, u, th_tip)) - CAPE_SIDE_TIPS[0]) / CAPE_SIDE_TIPS[1]) ** 2)
        h = rag * (CAPE_TIP_VAR + (1.0 - CAPE_TIP_VAR) * FP.hash01(k, seed)) * (1.0 - CAPE_SIDE_TIPS[2] * (1.0 - side))
        b = np.where(np.abs(t_) < 1, 1.0 - np.abs(t_) ** CAPE_TIP_POINT, 0.0)
        if FP.hash01(k, seed + 0.7) > 0.5:                              # a torn notch in this tip
            off = (FP.hash01(k, seed + 1.9) - 0.5) * 0.8 * w
            b = b * (1.0 - CAPE_TORN * np.exp(-((u - c - off) / (0.22 * w)) ** 2))
        ex = ex + h * b
    return ex


CAPE_INFO = {}
for s, sg in SIDES:
    for nm, L_ in CAPE_LAYERS.items():
        nt = L_["tips"]
        NU = nt * CAPE_COLS_PER_TIP + 1
        u = np.linspace(0.0, 1.0, NU)
        v = np.unique(np.round(np.concatenate([CAPE_ROWS, L_["grade"]]), 6))
        th0 = L_["th"][0] + (L_["th"][1] - L_["th"][0]) * v
        TH = th0[:, None] + u[None, :] * (180.0 - th0[:, None])
        zb = interp_theta(L_["bot"], TH[-1])
        D = L_["top"] - zb + tip_extra(u, nt, L_["rag"], L_["seed"], TH[-1])
        Z = L_["top"] - v[:, None] * D[None, :]
        thick = CAPE_THICK[0] + (CAPE_THICK[1] - CAPE_THICK[0]) * v[:, None] * np.ones_like(u)[None, :]
        rb = body_r(TH, np.maximum(Z, CAPE_RB_ZMIN), sg)        # below the egg the clearance follows its base
        rmin = rb + CAPE_CLEAR + thick
        fl = L_["flare"] * (CAPE_FLARE[0] + CAPE_FLARE[1] * np.exp(-((TH - CAPE_FLARE[2]) / CAPE_FLARE[3]) ** 2))
        Rr = r_env(TH, Z) - L_["off"] + CAPE_OVERLAP * ((u * nt) % 1.0)[None, :] + fl * (v ** 2)[:, None]
        tuck = smoothstep(0.0, CAPE_TUCK, u)[None, :]
        Rr = rmin + np.maximum(Rr - rmin, 0.0) * tuck
        if nm == "A":
            Rr[0] = rb[0] - CAPE_TOP_BURY
        thr = np.radians(TH)
        O = np.stack([sg * Rr * np.sin(thr), CAPE_AY - Rr * np.cos(thr), Z], -1)
        dU = np.gradient(O, axis=1); dV = np.gradient(O, axis=0)
        nrm = np.cross(dV, dU)
        radial = np.stack([sg * np.sin(thr), -np.cos(thr), np.zeros_like(thr)], -1)
        flip = np.einsum("ijk,ijk->ij", nrm, radial) < 0
        nrm[flip] *= -1
        nrm /= np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-12)
        I = O - nrm * thick[..., None]
        bands = 0.5 * (v[:-1] + v[1:])
        gr = L_["grade"]
        tones = ["cape", "cape_mid", "cape_tip"]
        regs = []
        for b in bands:
            lev = 0 if b < gr[0] else (1 if b < gr[1] else 2)
            row = []
            for j in range(NU - 1):
                k = j // CAPE_COLS_PER_TIP
                th_c = float(TH[-1, min(k * CAPE_COLS_PER_TIP + CAPE_COLS_PER_TIP // 2, NU - 1)])
                dk = int(FP.hash01(k, L_["seed"] + 5.3) < CAPE_DARK_FEATHERS) + int(th_c > CAPE_BACK_DARK)
                row.append(tones[max(lev - dk, 0)])
            regs.append(row)
        V, F, R = FP.drape(O, I, regs)
        p = FP.Part("cape.%s.%s" % (nm, s), V, F, R)
        p.chain = ("wing." + s, None, None, "body", 3, 0.25)
        PARTS.append(p)
        CAPE_INFO["%s.%s" % (nm, s)] = {"cols": NU, "rows": len(v), "tris": p.tris(),
                                        "z_range": [round(float(Z.min()), 3), round(float(Z.max()), 3)],
                                        "r_range": [round(float(Rr.min()), 3), round(float(Rr.max()), 3)]}
report["cape"] = CAPE_INFO

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
REG = ["plumage", "bib", "leg", "leg_band", "claw", "beak", "beak_low", "eye", "pupil", "lid", "scar", "fang",
       "cape", "cape_mid", "cape_tip"]
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
    mat.use_backface_culling = True                  # opaque closed pieces: single-sided (glTF doubleSided false)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


def glow_check(pal):
    """brief: no glow -- every region zero Glow"""
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    return {"skin": pal["skin"], "glowing_regions": lit, "no_glow": not lit}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
report["glow"] = glow_check(pal_default)
assert report["glow"]["no_glow"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, jitter(VALL, FALL))
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
report["zero_area_faces"] = int((fa < 1e-9).sum())
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the beak tip (midline)
anchor = HC - SHIFT
landmark = BEAK_TIP - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C, midline) -> beak tip on the midline",
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
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.003)
bpy.ops.object.mode_set(mode="OBJECT")

tris_main = tri_count_F(FALL)
_part_tris = {}
for p in PARTS:
    key = p.name.split(".")[0]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The cape is where the budget goes: six closed "
                            "layered slabs carry the ragged silhouette; the shell carries the body, head, crest, legs and "
                            "feet; the bake carries the SDF high's detail into the normal map." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, beak, decals, fang, claws, tail, cape slabs)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet": {"front_height_u": 5.38, "front_width_u": 4.15, "side_height_u": 5.31, "side_width_u": 4.5},
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
low["conquest_source"] = "from scratch: design/reference/raven_rogue-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_cape_pts = np.vstack([VALL[RANGE["cape.%s.L" % n][0]:RANGE["cape.%s.L" % n][1]] for n in CAPE_LAYERS])
_tail_pts = np.vstack([VALL[RANGE["tail.%d" % k][0]:RANGE["tail.%d" % k][1]] for k in range(len(TAIL_FAN))])
_foot = np.array([0.95, 0.4, 0.75]) - SHIFT
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.2, -1.5, 3.3]) - SHIFT).tolist(), (np.array([1.05, -0.4, 4.75]) - SHIFT).tolist()],
    "beak": [(np.array([-0.4, -2.1, 2.45]) - SHIFT).tolist(), (np.array([0.4, -0.9, 3.95]) - SHIFT).tolist()],
    "wing": [(_cape_pts.min(0) * np.array([1, 1, 1])).tolist(), _cape_pts.max(0).tolist()],
    "feet_tail": [(np.array([-0.2, -0.6, -0.02]) - SHIFT).tolist(), np.maximum(_foot, _tail_pts.max(0)).tolist()],
    "views": {"face": [28.0, 4.0], "beak": [75.0, 4.0], "wing": [105.0, 8.0], "feet_tail": [70.0, 8.0]},
    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "zero_area_faces", "cape")},
                                default=str))
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


def texel_faces(res):
    """-> (res*res,) int: the face whose UV triangle covers each texel centre (-1 = none)"""
    m = np.full((res, res), -1, dtype=np.int64)
    for fi in range(nf):
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
            m[ys.ravel()[ins], xs.ravel()[ins]] = fi
    return m.ravel()


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
TFA = texel_faces(RA)
alltex_a = TFA >= 0
# ---- AO floors per region class (v1.1 gate; house style: "AO floors per region class so nothing bakes to black grime").
#      Visible vs hidden faces: a ray from the face centre along its normal that hits the mesh within AO_HIDDEN_DIST
#      marks the face hidden (buried decal undersides, the mantle's buried top row, cape inner skins against the body).
_FC = np.array([VALL[f].mean(0) for f in FALL])
_FN = np.array([np.cross(VALL[f[1]] - VALL[f[0]], VALL[f[2]] - VALL[f[0]]) for f in FALL])
_FN /= np.maximum(np.linalg.norm(_FN, axis=1, keepdims=True), 1e-12)
_bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
face_vis = np.array([_bvh_all.ray_cast(Vector(c + n * 1e-3), Vector(n), AO_HIDDEN_DIST)[0] is None
                     for c, n in zip(_FC, _FN)])
_cls = {r: c for c, rs in AO_CLASS.items() for r in rs}
face_floor = np.array([AO_FLOOR[_cls[REG[r]]] for r in rid])
vis_tex = alltex_a & cov_a
vis_tex[alltex_a] &= face_vis[TFA[alltex_a]]


def ao_stats(arr):
    out = {"visible_all": {"mean": round(float(arr[vis_tex].mean()), 4), "p05": round(float(np.percentile(arr[vis_tex], 5)), 4)}}
    for c, rs in AO_CLASS.items():
        m_ = vis_tex.copy()
        m_[alltex_a] &= np.isin(rid[TFA[alltex_a]], [REG.index(r) for r in rs])
        if m_.any():
            out[c] = {"mean": round(float(arr[m_].mean()), 4), "p05": round(float(np.percentile(arr[m_], 5)), 4),
                      "texels": int(m_.sum())}
    return out


ao_before = ao_stats(pa[:, 0])
floor_tex = np.full(len(pa), min(AO_FLOOR.values()))       # margin / unmapped baked texels: the lowest class floor
floor_tex[alltex_a] = face_floor[TFA[alltex_a]]
pa[cov_a, :3] = np.maximum(pa[cov_a, :3], floor_tex[cov_a, None])
ao_after = ao_stats(pa[:, 0])
bstats["ao_floor"] = {"floors": AO_FLOOR, "classes": AO_CLASS, "hidden_rule": "normal ray hits within %.2f" % AO_HIDDEN_DIST,
                      "faces_visible": int(face_vis.sum()), "faces_hidden": int((~face_vis).sum()),
                      "visible_texels": int(vis_tex.sum()), "before": ao_before, "after": ao_after}
print("AO_FLOOR", json.dumps(bstats["ao_floor"]))
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part",
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


_yc = lambda z: float(egg_at(np.array([z]))[0][0])
BONES = [("hips", P_((0, _yc(0.9), 0.9)), P_((0, _yc(1.4), 1.4)), "root"),
         ("body", P_((0, _yc(1.4), 1.4)), P_((0, _yc(3.0), 3.0)), "hips"),
         ("head", P_((0, _yc(3.0), 3.0)), P_((0, HC[1], 4.9)), "body")]
WING_PTS = {}
for s, sg in SIDES:
    th_ = np.full(len(WING_Z), WING_BONE_TH)
    rr = r_env(th_, np.array(WING_Z)) - 0.10
    a_ = math.radians(WING_BONE_TH)
    pts = [np.array([sg * r_ * math.sin(a_), CAPE_AY - r_ * math.cos(a_), z_]) for r_, z_ in zip(rr, WING_Z)]
    WING_PTS[s] = pts
    for k in range(3):
        BONES.append(("wing.%s.%d" % (s, k), P_(pts[k]), P_(pts[k + 1]), "body" if k == 0 else "wing.%s.%d" % (s, k - 1)))
    th_l = LEG[s]
    toe_mid = th_l["toes"][1][1]
    BONES += [("leg.%s.0" % s, P_(mir(HIP, sg)), P_(mir(KNEE, sg)), "hips"),
              ("leg.%s.1" % s, P_(mir(KNEE, sg)), P_(mir(ANKLE, sg)), "leg.%s.0" % s),
              ("leg.%s.2" % s, P_(mir(ANKLE, sg)), P_(toe_mid), "leg.%s.1" % s)]
BONES += [("tail.0", P_(TAIL_SPINE[0]), P_(TAIL_SPINE[1]), "hips"), ("tail.1", P_(TAIL_SPINE[1]), P_(TAIL_SPINE[2]), "tail.0")]
CONNECT = {"body", "head", "leg.L.1", "leg.L.2", "leg.R.1", "leg.R.2", "tail.1", "wing.L.1", "wing.L.2", "wing.R.1", "wing.R.2"}
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
wh = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
wl = smoothstep(HIP_BAND[1], HIP_BAND[0], zz)
gi = GROUPS.index("torso")
Ws[:, J["head"]] += Wg[:, gi] * wh
Ws[:, J["hips"]] += Wg[:, gi] * wl
Ws[:, J["body"]] += Wg[:, gi] * np.clip(1.0 - wh - wl, 0, None)
for s, _ in SIDES:
    gi = GROUPS.index("leg." + s)
    wf = smoothstep(FOOT_BAND[1], FOOT_BAND[0], zz)
    wk = smoothstep(KNEE_BAND[0], KNEE_BAND[1], zz)
    wt = smoothstep(LEG_TOP[0], LEG_TOP[1], zz)
    Ws[:, J["leg.%s.2" % s]] += Wg[:, gi] * wf
    Ws[:, J["leg.%s.1" % s]] += Wg[:, gi] * (1 - wf) * (1 - wk)
    Ws[:, J["leg.%s.0" % s]] += Wg[:, gi] * (1 - wf) * wk * (1 - wt)
    Ws[:, J["hips"]] += Wg[:, gi] * (1 - wf) * wk * wt
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if p.chain and p.name.startswith("cape"):
        chain, _s, _L, parent, nb, s0 = p.chain
        drop = np.clip(CAPE_ZTOP - (VALL[a_:b_, 2] + SHIFT[2]), 0.0, None)
        L_ = CAPE_ZTOP - WING_Z[-1]
        Wv = K.vine_weights(np.clip(drop, 0, L_), L_, nb, parent_s0=s0)
        Wt[a_:b_, J[parent]] += Wv[:, 0]
        for k in range(nb):
            Wt[a_:b_, J["%s.%d" % (chain, k)]] += Wv[:, k + 1]
    elif p.chain:
        chain, s_arc, L_, parent, nb, s0 = p.chain
        _, s_arc, _ = FP.spine_param(VALL[a_:b_] + SHIFT, TAIL_SPINE)
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between the torso and the legs; the torso runs hips "
                          "(below HIP_BAND) -> body -> head (above HEAD_BAND) by wide smoothsteps; legs: foot below "
                          "FOOT_BAND, shin, thigh above KNEE_BAND, the top blending into hips; cape slabs: "
                          "rigkit.vine_weights on the drop below CAPE_ZTOP (body -> wing.s.0 -> .1 -> .2); tail: "
                          "vine_weights along its spine from hips; beak, eye decals, scar, fang rigid on head; claws "
                          "rigid on their foot" % OWN_TAU}
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


def hang(s):
    """one step's envelope in [0, 1]: zero value AND zero slope at both ends"""
    g_ = math.sin(math.pi * (s % 1.0)) ** 2
    return 1.0 - (1.0 - g_) ** 2


def step_of(u):
    k = int(2.0 * (u % 1.0)) % 2
    return k, (2.0 * (u % 1.0)) % 1.0, (1.0 if k == 0 else -1.0)


def idle_pose(u):
    """PLACEHOLDER idle (4 s): breathing, a slow head tilt + nod, a cape ruffle travelling down each chain (L / R
    de-synced), a small tail flick; feet planted (hips and legs still)."""
    P = {}
    br = math.sin(TAU * 2 * u)
    P["body"] = (None, [], (1 + BREATH * br, 1 + 0.5 * BREATH * br, 1 + BREATH * br))
    P["head"] = (None, [("Y", HEAD_TILT[0] * math.sin(TAU * u + 0.3)), ("X", HEAD_TILT[1] * math.sin(TAU * 2 * u - 0.9))], None)
    for s, sg in SIDES:
        ph = 0.0 if s == "L" else 1.9
        for k in range(3):
            ry = K.vine_wave(u, k, 3, CAPE_IDLE["Y"][0], CAPE_IDLE["Y"][1], CAPE_IDLE["Y"][3], CAPE_IDLE["Y"][2], ph)
            rx = K.vine_wave(u, k, 3, CAPE_IDLE["X"][0], CAPE_IDLE["X"][1], CAPE_IDLE["X"][3], CAPE_IDLE["X"][2], ph + 0.8)
            P["wing.%s.%d" % (s, k)] = (None, [("Y", sg * ry), ("X", rx)], None)
    for k in range(2):
        P["tail.%d" % k] = (None, [("Z", K.vine_wave(u, k, 2, TAIL_IDLE[0], TAIL_IDLE[1], TAIL_IDLE[3], TAIL_IDLE[2], 0.4)),
                                   ("X", 0.5 * K.vine_wave(u, k, 2, 1.0, 2.0, 0.7, 1, 1.0))], None)
    return P, {"lift": 0.0}


def walk_pose(u):
    """PLACEHOLDER walk (2 s): an in-place HOP-STRUT -- two steps per loop; each step the hips hop up (never below rest),
    the swing leg reaches forward with the shin folded to lift the foot, the stance thigh pushes back, the body leans
    forward and rolls over the stance foot, the head pumps forward (bird strut), the cape and tail lag the hop."""
    k, s, sg = step_of(u)
    h = hang(s)
    P = {"hips": ((0.0, 0.0, HOP * h), [("Y", sg * STRUT_ROLL * h)], None)}
    P["body"] = (None, [("X", -STRUT_LEAN - 1.5 * h), ("Z", -sg * 2.0 * h)], None)
    pump = HEAD_PUMP[0] * math.sin(math.pi * s)
    P["head"] = ((0.0, -pump, 0.0), [("X", STRUT_LEAN * 0.8 + HEAD_PUMP[1] * math.sin(TAU * s))], None)
    swing = "R" if k == 0 else "L"
    for s_, sg_ in SIDES:
        if s_ == swing:
            P["leg.%s.0" % s_] = (None, [("X", -STRUT_SWING * h)], None)
            P["leg.%s.1" % s_] = (None, [("X", STRUT_LIFT * h)], None)
            P["leg.%s.2" % s_] = (None, [("X", -0.6 * STRUT_LIFT * h)], None)
        else:
            P["leg.%s.0" % s_] = (None, [("X", STRUT_BACK * h)], None)
            P["leg.%s.1" % s_] = (None, [], None)
            P["leg.%s.2" % s_] = (None, [("X", -STRUT_BACK * h)], None)
        for kb in range(3):
            lagh = hang(s - CAPE_BOUNCE[2] * (kb + 1))
            amp = CAPE_BOUNCE[0] + (CAPE_BOUNCE[1] - CAPE_BOUNCE[0]) * kb / 2.0
            P["wing.%s.%d" % (s_, kb)] = (None, [("X", amp * (lagh - 0.5)), ("Y", sg_ * 0.5 * amp * (lagh - 0.5))], None)
    for kb in range(2):
        P["tail.%d" % kb] = (None, [("X", TAIL_BOB * (hang(s - 0.08 * (kb + 1)) - 0.5) * (0.6 + 0.4 * kb)),
                                    ("Z", sg * 2.0 * h)], None)
    return P, {"lift": HOP * h}


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
SOLE = {s: np.nonzero(_dom == J["leg.%s.2" % s])[0] for s, _ in SIDES}
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
        rows.append({"minz": float(C[:, 2].min()), "sole_L": float(C[SOLE["L"], 2].min()), "sole_R": float(C[SOLE["R"], 2].min()),
                     "hips_z": float(pbs["hips"].head.z), "head_tail": list(pbs["head"].tail),
                     "wing_tip_L": list(pbs["wing.L.2"].tail), "tail_tip": list(pbs["tail.1"].tail),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    hz = g_("hips_z") - REST_H["hips"][2]
    wt_ = np.array([r["wing_tip_L"] for r in R_]); tt_ = np.array([r["tail_tip"] for r in R_])
    ht_ = np.array([r["head_tail"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "sole_min_z": {"L": [round(float(g_("sole_L").min()), 4), round(float(g_("sole_L").max()), 4)],
                                   "R": [round(float(g_("sole_R").min()), 4), round(float(g_("sole_R").max()), 4)]},
                    "hips_lift": [round(float(hz.min()), 4), round(float(hz.max()), 4)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "head_tip_p2p_xyz": (ht_.max(0) - ht_.min(0)).round(4).tolist(),
                    "wing_tip_L_p2p_xyz": (wt_.max(0) - wt_.min(0)).round(4).tolist(),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist()}
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
rig["conquest_rig"] = ("raven_rogue v1: root > hips > body > head (beak, eyes, scar, fang rigid); body > wing.{L,R}.{0,1,2} "
                       "(cape chains); hips > leg.{L,R}.{0,1,2} (2 = the foot); hips > tail.{0,1}")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER: the artist has not given the movement intent (OPEN-QUESTIONS N1). idle = a "
                               "cape-ruffle stance (4 s: breathing, slow head tilt, de-synced cape ruffle down each wing "
                               "chain, tail flick, feet planted); walk = an in-place hop-strut (2 s: two hops, swing leg "
                               "reach + foot lift, head pump, cape + tail lag; feet never below z 0, root still)")
low["conquest_locomotion"] = "walker: hop-strut in place (the game glides the unit)"
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
              "structure": "armature identity + skinned 'raven_rogue' (one opaque body material); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief: default skin only)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
