"""Gemwing Dragon -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/gemwing_dragon_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-11 "NEW UNITS x6" (section GEMWING DRAGON) + "Gemwing Dragon pre-build direction"
(artist verbatim: "for gemwing dragon tone the crystalness of the body and save the most color for the wings and spikes
around it") + the sheet design/reference/gemwing_dragon-character-sheet.png (intake: design/reference/gemwing_dragon/
intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The SIDE view is the master profile (the brief: "standing as the
side view draws it"; it faces image-left = the unit's front, -Y). It is drawn as a slight three-quarter, so the near paws
sit ABOVE the intake's side floor line (772 px is the bottom of the tail curl): near front paw sole y 712, near hind paw
sole y 735 -> the side floor is taken at y 725 (the drawn paws), NOT 772 (reported). The FRONT view gives the widths:
  SIDE view:   Y = (x_px - 900) / 100 ; z = (725 - y_px) / 100
  FRONT view:  X = (x_px - 174) / 100 ; z = (764 - y_px) / 100     (the unit's left, +X, on image right)
X +ve = the unit's LEFT (L). Floor z = 0 at the soles.

A STANDING FOUR-LEGGED CRYSTAL DRAGON, built MIRROR-SYMMETRIC in rest pose (artist house rule 2026-10-10): four legs
mirror-symmetric about X = 0 (no stagger), the tail straight back on the midline curving gently down to the floor (the
sheet swings / curls it).

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the deep CHEST, the BODY (round cone chest -> pelvis),
     two HAUNCHES, four legs (thick upper legs tapering to the feet, a shoulder lobe on each foreleg, flat-soled FEET), the
     S-curved NECK, the angular reptilian HEAD (cranium + boxy snout + lower jaw, BROW ridges, a carved MOUTH line), the
     long TAIL. Marching tets = the HIGH (bake source); collapse decimation + mirror-weld = the LOW shell.
  2. BODY FACETS (the toned-down crystal): a symmetric farthest-point Voronoi of the shell (per body part), every cell
     border cut into the mesh along the exact bisector planes; each cell takes ONE low-contrast tone by its mean normal vs
     the light (body_lit / body / body_shade) or, sparsely, a muted accent; the HIGH is flattened toward each cell's
     plane, so the baked normal map carries subtle planar facets.
  3. PARTS (gemwing_dragon_parts.py, closed): the reptilian IRIS + SLIT PUPIL decals; the CROWN, NECK, BACK, TAIL and LEG
     SPIKES (faceted hex prisms, swept back, saturated prismatic tones by light + length band); the two CRYSTAL WINGS
     (11 layered faceted blades per side fanning up and back from the shoulders, the most saturated tones + white streak
     cells); the crystal CLAWS (3 per foot).
  4. palette (palettes.py regions -> Col / Glow, no glow, opaque), Smart UV, bake normal + AO from the faceted high + parts.
  5. RIG: root > hips > spine.001 > .002 > .003 > neck.001 > neck.002 > head; spine.003 > foreleg_{L,R}.upper > .lower >
     .foot; hips > hindleg_{L,R}.upper > .lower > .foot; spine.002 > wing_{L,R}.001 > .002 > .003; hips > tail.001 >
     .002 > .003 > .004. Clips (PLACEHOLDER, movement intent open): idle (breathing + a wing flex + a tail sway, feet
     planted by a planar 2-bone IK) + walk (a 4-beat walk in place, stance feet planted). glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile, colorsys
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                # noqa: E402  (read-only use)
import palettes as PAL            # noqa: E402  (read-only use)
import magmoo_sdf as SD           # noqa: E402  (read-only use: the SDF kit)
import gemwing_dragon_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "gemwing_dragon"
CHAR_ID = "gemwing_dragon"            # roster id: not yet in Conquest
TRI_BUDGET = [20000, 30000]           # declared tier: BOSS unit (artist approved 2026-10-11, v5 verdict "yes do it")
CELL_MAX_H, CELL_MAX_FP = 3.4, 3.8    # Conquest 4-cell boss ceilings (assets.conf) -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.25                       # >= the largest smooth-union k (0.20, the belly) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 6500                      # "body mesh detail": the shell's collapse-decimation target (v6 boss: finer facets)
EDGE_MAX = 0.19                       # the decimated shell's edges longer than this are split (midpoint projected onto
                                      #   the high, then a beautify pass): collapse decimation leaves slivers up to 2.3
                                      #   long on the body / tail / leg cylinders, which skipped whole facet cells (v1 look
                                      #   loop: flank stripes). Must stay < the facet size (FACET_R) so every cell holds a
                                      #   vertex
CUT_SNAP = 0.25                       # facet cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- HEAD. SIDE view: snout tip (665, 328) (Y -2.35, z 3.97); nose top (680, 312) (z 4.13); brow (745, 290) (Y -1.55,
#   z 4.35); skull top / back (770-795, 290-300) (Y -1.05..-1.3, z 4.3); jaw underside (700-750, 360-370) (z 3.55-3.65);
#   mouth line (680, 340) -> (760, 345) (z 3.85-3.80). FRONT view: head half width ~0.40 at the eyes (x 130-215, the head
#   drawn turned and raised -- see the report)
HEAD_C, HEAD_R, HEAD_K = (0.0, -1.28, 4.02), (0.40, 0.40, 0.30), 0.08   # "head size": a FLAT cranium
SNOUT = ((0.0, -1.50, 3.99), (0.0, -2.12, 3.92), 0.28, 0.13)            # "snout": a LONG wedge, deep blunt tip (v6, artist v5 verdict 'its snout and mouth are so small': sized from the sheet's head panel)
SNOUT_SX, SNOUT_SZ, SNOUT_K = 1.00, 0.80, 0.10                          # "snout width" / "snout flatness"
JAW = ((0.0, -1.10, 3.75), (0.0, -2.10, 3.74), 0.22, 0.10)              # "lower jaw": long, angular, under the snout
JAW_SX, JAW_K = 1.00, 0.06
BROW = ((0.22, -1.57, 4.22), (0.14, 0.24, 0.09), 0.04)                  # "brow ridge": strong, overhanging the eye
MOUTH = ((0.0, -1.83, 3.83), (0.48, 0.42, 0.022), 0.075, 0.012)         # "mouth line" (tip -> under the eye): (centre, radii,
                                      #   blend) -- a thin disc subtracted only within 'depth' of the head's outer surface
                                      #   (never an internal void); ends at Y -1.53 (the sheet's mouth corner x 760)
# ---- NECK: SIDE view throat (770, 380) (Y -1.3, z 3.45), front of neck (755, 420) (Y -1.45, z 3.05), neck back behind the
#   spikes (840, 380) -> (870, 420) (Y -0.6..-0.3); FRONT view neck x 140-215 @ y 350-400 (+-0.38). A gentle S.
NECK_KNOTS = [(0.0, -1.12, 3.95), (0.0, -0.98, 3.60), (0.0, -0.85, 3.20), (0.0, -0.50, 2.80)]   # (v4, artist v3 verdict 'looks like a horse rather than a dragon': re-proportioned on the SIDE view): sheet neck lean 28.8 deg fwd
NECK_RAD = [0.23, 0.27, 0.34, 0.48]   # "neck thickness" along the knots (thin at the head -> the chest)
NECK_K = 0.12
# ---- TRUNK. SIDE view chest front (745, 460) (Y -1.55, z 2.65), chest / belly bottom (830-950, 540-545) (z 1.8-1.85),
#   back line under the wings (950-1050, 430-440) (z 2.85-2.95), rump (1120-1140, 450) (Y 2.2-2.4, z 2.75); FRONT view
#   chest half width ~0.80 at z 2.6 (+ the plates the direction tones down)
CHEST_C, CHEST_R, CHEST_K = (0.0, -0.85, 2.35), (0.78, 0.62, 0.66), 0.14   # "chest depth" (v4: lower)
CHEST_V = ((0.0, -0.95, 2.38), (0.0, -0.70, 1.50), 0.54, 0.30)            # "chest keel": the deep chest hanging down
                                      #   between the forelegs (v2, artist v1 verdict "wider on the bottom" + design/
                                      #   reference/gemwing_dragon-v1-belly-annotation.png: the red underline's low point
                                      #   (120, 243) = Y -0.65, z 1.18 at 34 px / unit, feet y 283)
CHEST_V_SX = 1.35
BELLY = ((0.0, 0.15, 1.95), (0.78, 1.30, 0.66))   # "belly": a full rounded underline chest -> hips (markup (160, 232)
                                      #   Y 0.5 z 1.5, (200, 222) Y 1.65 z 1.8)
BELLY_K = 0.20
BODY = ((0.0, -0.50, 2.30), (0.0, 1.85, 2.30), 0.58, 0.50)                 # "body" round cone chest -> pelvis (v4: lower, longer)
BODY_SX = 1.15                        # "body width": the body's X stretch
# ---- HAUNCHES: SIDE view near thigh x 1040-1150 @ y 480-620 (Y 1.4-2.5, z 1.05-2.45)
HAUNCH_C, HAUNCH_R, HAUNCH_K = (0.56, 1.85, 1.85), (0.34, 0.58, 0.64), 0.14   # L (mirrored): the big thigh
# ---- FORELEGS (L; R mirrored). SIDE view near foreleg: shoulder (850, 500) (Y -0.5, z 2.25), elbow (845, 590)
#   (Y -0.55, z 1.35), wrist (790, 670) (Y -1.1, z 0.55), paw (750-820, 690-712), toes / claws to x 720 (Y -1.8);
#   FRONT view front paws x 80-150 / 195-265 (X +-0.57), upper legs x 100-160 @ y 600 (0.60 wide)
FORE_KNOTS = [(0.50, -0.60, 2.30), (0.66, -0.25, 1.40), (0.68, -1.00, 0.50)]   # shoulder, elbow (BENT back), wrist (v4, artist v3 verdict 'looks like a horse rather than a dragon': re-proportioned on the SIDE view)
FORE_R = (0.36, 0.27, 0.19)           # "foreleg thickness": heavy forearm
FORE_SX = 0.90                        # the leg's X half width share of its depth
FORE_SHOULDER = ((0.52, -0.55, 2.05), (0.28, 0.36, 0.42))   # the shoulder lobe (centre, radii)
FORE_FOOT_C, FORE_FOOT_R = (0.70, -1.25, 0.13), (0.27, 0.34, 0.15)   # "foot size": splayed, wider stance
FORE_TOE = (0.70, -1.60, 0.08)        # the toe front (bone end)
LEG_K, FOOT_K = 0.10, 0.07
# ---- HINDLEGS (L; R mirrored -- SYMMETRIC, house rule): the SIDE view's NEAR hind leg (the lower, larger one): thigh in
#   the haunch, lower leg x 1120-1170 @ y 620-690, paw x 1115-1185 @ y 690-735 (Y 2.15-2.85); X from the BACK view's near
#   (hind) paws x 400-450 / 555-605 (X +-0.78). The sheet's far hind leg (stepping forward, x 985-1060) is pose.
HIND_KNOTS = [(0.52, 1.85, 2.20), (0.66, 1.50, 1.30), (0.80, 2.45, 0.78)]      # hip, knee (FORWARD), hock (reverse angle) (v4, artist v3 verdict 'looks like a horse rather than a dragon': re-proportioned on the SIDE view)
HIND_R = (0.36, 0.26, 0.19)           # "hindleg thickness"
HIND_SX = 0.88
HIND_FOOT_C, HIND_FOOT_R = (0.84, 2.32, 0.13), (0.27, 0.34, 0.15)
HIND_TOE = (0.84, 1.98, 0.08)
# ---- TAIL: built straight back on the midline, curving gently DOWN to the floor (house rule; the sheet curls it under
#   and forward to a tip at (1000, 805)). Root at the rump (1110-1140, 440-480) (Y 2.1-2.4, z 2.45-2.85); the sheet's
#   tail is ~0.7 thick at the root, ~0.3 at mid length.
TAIL_KNOTS = [(0.0, 2.05, 2.40), (0.0, 2.95, 2.02), (0.0, 3.75, 1.42), (0.0, 4.45, 0.74), (0.0, 5.15, 0.30),
              (0.0, 5.75, 0.17)]
TAIL_RAD = [0.42, 0.32, 0.22, 0.15, 0.11, 0.07]    # "tail thickness" (v4: a heavier base tapering)
TAIL_K = 0.10
# ---- BODY FACETS (the toned-down crystal, artist direction 2026-10-11)
FACET_R = 0.18                        # "body facet size": farthest-point seed spacing (v6: cells ~0.25 across = the
                                      #   20-40 px)
FACET_LIGHT = (0.0, -0.45, 0.89)      # facet tone light (symmetric, above-front)
FACET_TONE_T = (0.52, -0.05)          # cell mean-normal . light: lit above / shade below (low-contrast 3-tone)
FACET_JIT = 0.35                      # per-cell tone jitter (mosaic variety, symmetric hash)
ACCENT_FRAC = (0.045, 0.035)            # "body colour accents": share of cells taking the muted cool / warm accent (sparse)
FACET_MAX_MOVE = 0.12                 # v3 (artist v2 verdict "the whole body should also look more geometric like a body
                                      #   made of crystals"): every LOW shell vertex moves onto its facet cell's plane (a
                                      #   border vertex onto the planes' intersection, regularised least squares) -> real
                                      #   planar facets with crisp creases; a move is capped at this length
FACET_LS_EPS = 0.02                   # least-squares regularisation for near-parallel neighbouring facet planes
SEAM_SNAP = 0.06                      # seeds this close to X = 0 sit ON the midline (single, symmetric)
# ---- EYES: SIDE view eye (737, 307) (Y -1.63, z 4.18), almond ~0.20 x 0.10, slanting up toward the back; violet iris,
#   dark plum slit (face panel 1292-1322 x 138-155)
EYE_Y, EYE_Z = -1.50, 4.06            # "eye placement": set back under the brow (L; cast from the side-front)
EYE_A, EYE_B, EYE_SHARP = 0.105, 0.055, 0.7   # "eye size": almond half length / half height / corner sharpness
EYE_TILT = 12.0                       # "eye slant": the back corner up (deg)
PUPIL_AB = (0.018, 0.048)             # "slit pupil": half width / half height
DECAL_UP, DECAL_DN, DECAL_DOME = 0.008, 0.03, 0.010
NOSTRIL = ((0.08, -2.17, 3.95), 0.032)   # "nostrils" (L centre, radius; mirrored), carved near the snout tip (head panel (1240, 165))
# ---- WINGS (v3, artist v2 verdict 2026-10-11 "same for the wings, more one piece like the original reference"): per
#   side ONE fused crystal wing -- a big fan of blades sweeping up-back from the shoulder, fused along their length and at
#   their bases (one SDF). The v2 swept, scalloped silhouette (design/reference/gemwing_dragon-v1-wing-annotation.png) is
#   kept, pulled back toward the SHEET's side view (floor y 725): tallest tip (1078, 72) (Y 1.78, z 6.53), top-back peach
#   (1200, 92) (Y 3.0, z 6.33), feather points (1200, 238) (3.0, 4.87), (1198, 348) (2.98, 3.77), (1125, 432)
#   (2.25, 2.93); roots at the shoulder (900-950, 420-450). X: the front view's tips at +-1.3..1.5.
WING_ARM = [(0.45, 0.00, 2.95), (0.80, 0.65, 4.55), (1.10, 1.45, 5.70), (1.40, 2.60, 6.45)]   # shoulder, elbow, wrist, tip
WING_ROOT_BURY = (0.32, -0.05, 2.65)  # the wing arm starts this deep inside the shoulder (one attached solid)
WING_ARM_R = (0.16, 0.14, 0.11, 0.07, 0.02)   # the arm (leading-edge) radius at the bury point + the four arm knots
WING_BLADES = [  # (base (L), tip (L), radius, family, layer: 0 trailing feathers, 1 primaries / leading edge, 2 coverts)
    ((0.52, 0.25, 3.25), (1.05, 1.50, 3.00), 0.20, "violet", 0),   # inner secondary (meets the body)
    ((0.62, 0.40, 3.60), (1.30, 2.25, 2.95), 0.22, "peach", 0),    # secondary: sheet point (1125, 432)
    ((0.72, 0.60, 4.05), (1.35, 2.55, 3.30), 0.22, "violet", 0),   # secondary
    ((0.82, 0.80, 4.45), (1.45, 3.00, 3.80), 0.25, "peach", 0),    # secondary: sheet point (1198, 348)
    ((0.90, 0.95, 4.75), (1.40, 2.80, 4.30), 0.24, "violet", 0),   # secondary: the notch above it
    ((0.95, 1.10, 4.95), (1.45, 3.10, 4.80), 0.26, "peach", 1),    # primary: sheet point (1200, 238)
    ((1.00, 1.30, 5.15), (1.45, 3.15, 5.35), 0.25, "violet", 1),   # primary
    ((1.05, 1.45, 5.35), (1.42, 3.05, 6.15), 0.27, "peach", 1),    # primary: sheet top-back peach (1200, 92)
    ((1.05, 1.30, 5.40), (1.40, 2.40, 6.50), 0.25, "blue", 1),     # leading edge: the wingtip (sheet (1078, 72))
    ((0.78, 0.55, 4.30), (1.20, 1.70, 5.95), 0.24, "blue", 1),     # leading edge: elbow -> wrist
    ((0.50, 0.15, 3.15), (0.95, 0.75, 4.95), 0.20, "blue", 1),     # leading edge: shoulder -> elbow
    ((0.70, 0.50, 4.10), (1.05, 1.45, 4.80), 0.18, "blue", 2),     # covert over the forearm
    ((0.95, 1.05, 4.90), (1.25, 2.10, 5.50), 0.18, "violet", 2)]   # covert over the wrist
WING_LAYER_OFF = 0.045                # layers sit this far apart along the wing plane's outward normal (layered)
WING_RSCALE = 1.00                    # "blade width": x the table radius (v6: blades lie side by side, no lumpy overlap)
WING_FLAT = 0.40                      # "blade thickness": a blade's section flattening across the wing plane
WING_FUSE_K = 0.012                   # "blade fusion": a near-hard union (v6: crisp blades, fused only where they touch)
WING_SQUASH = 0.40                    # "blade thickness": the hex section's flattening across the wing plane
WING_TIPF = 0.28                      # "blade points": the pyramid tip share of each blade
WING_RAMPS = {"blue": ("gem_indigo", "gem_blue", "gem_sky", "gem_lilac"), "violet": ("gem_indigo", "gem_violet", "gem_lilac", "gem_pink"),
              "peach": ("gem_indigo", "gem_violet", "gem_pink", "gem_peach")}   # ONE root -> tip gradient per blade (4 steps)
WING_STEP, WING_TRIS = 0.022, 1500     # the wing SDF step / its decimation target (facets ARE the look)
# ---- SPIKES (closed faceted prisms, saturated). CROWN (v2, artist v1 verdict: "the head spikes more in the shape of a
#   crown" + design/reference/gemwing_dragon-v1-crown-annotation.png): a ring of UPRIGHT crystal points on the head top
#   around a taller centre point, stepping down toward the sides and back (not swept back like a mohawk)
CROWN_C = (0.0, -1.24)                # ring centre (Y) on the head top (X = 0)
CROWN_RING = (0.30, 0.30)             # ring half axes (X, Y)
CROWN_CENTRE = (1.00, 0.110)          # "crown centre point": length, radius
CROWN_POINTS = [(0.0, 0.72, 0.090), (45.0, 0.64, 0.085), (90.0, 0.54, 0.080), (135.0, 0.45, 0.075),
                (180.0, 0.40, 0.075)]   # (angle from the front, length, radius); 45 / 90 / 135 mirrored to the R side
CROWN_SPLAY = 0.30                    # the ring points lean out of the ring this much (an open crown), no back sweep
CROWN_BAND = (0.04, 0.18, 0.075, 16)  # v3 ("one merged piece" + the v2 crown markup's base band): two stacked loops at
                                      #   these lifts over the head top, tube radius, ring points; the points rise from
                                      #   the band's top loop
CROWN_K = 0.05                        # the smooth-union blend fusing the points into the band
CROWN_STEP, CROWN_TRIS = 0.015, 700   # the crown SDF step / its decimation target
# ---- CHEST ARMOUR (v4, artist v3 verdict "the chest should be a piece like this" + design/reference/gemwing_dragon-v3-
#   chest-annotation.png): ONE starburst piece on the upper chest -- a faceted centre gem with ~10 pointed rays round its
#   rim (the longest at the sides) and a pointed bottom tip, conformed to the chest (rays laid along the surface), one SDF
CHEST_STAR_C = 2.38                   # the star centre height on the chest front (z)
CHEST_STAR_GEM = (0.26, 0.30, 0.09)   # the centre gem half axes (across, up, out of the chest)
CHEST_STAR_RAYS = [(0.0, 0.42), (35.0, 0.48), (70.0, 0.60), (100.0, 0.66), (135.0, 0.50), (180.0, 0.62)]   # (angle from
                                      #   up, length); 35 / 70 / 100 / 135 mirrored: 10 points
CHEST_STAR_R = (0.11, 0.02)           # ray root / tip radius
CHEST_STAR_LIFT = (0.08, 0.035)       # the centre / ray tips stand this far off the chest
CHEST_STAR_K, CHEST_STAR_STEP, CHEST_STAR_TRIS = 0.05, 0.015, 700
# ---- CROWN RIDGE (v4, artist v3 verdict "the crown should extend like this" + design/reference/gemwing_dragon-v3-snout-
#   annotation.png): the crown continues forward as one crystal ridge plate along the head top and the snout top to the
#   nose, ending in a pointed, slightly hooked tip above the nostrils (part of the SAME crown SDF)
CROWN_RIDGE_Y = (-1.54, -1.70, -1.86, -2.02, -2.14)   # ridge stations along the head / snout top (cast down + lift)
CROWN_RIDGE_R = (0.065, 0.040)        # ridge half height at the crown / at the nose (v5 gate: a LOW plate)
CROWN_RIDGE_SX = 1.9                  # the ridge is a plate: this much wider than tall
CROWN_RIDGE_LIFT = 0.012             # hugging the snout surface (half buried)
CROWN_HOOK = (0.06, 0.03, 0.018)      # the nose tip (no horn): projects this far beyond the nose front (Y), rises this
                                      #   much over the last ridge station, tip radius
SPIKE_ROWS = {   # "spike rows": curve -> (midline (arc fraction, length), side (arc fraction, length), side angle deg)
    "neck": ([(0.12, 0.42), (0.30, 0.52), (0.48, 0.60), (0.66, 0.58), (0.84, 0.50)],
             [(0.21, 0.36), (0.39, 0.44), (0.57, 0.48), (0.75, 0.44)], 58.0),
    "back": ([(0.30, 0.50), (0.48, 0.56), (0.66, 0.52), (0.84, 0.46)],
             [(0.39, 0.40), (0.57, 0.42), (0.75, 0.38)], 52.0),
    "tail": ([(0.05, 0.58), (0.17, 0.60), (0.29, 0.56), (0.41, 0.52), (0.53, 0.46), (0.65, 0.40), (0.77, 0.34),
              (0.88, 0.28)],
             [(0.11, 0.50), (0.23, 0.48), (0.35, 0.44), (0.47, 0.40), (0.59, 0.36), (0.71, 0.30), (0.82, 0.25)], 64.0)}
BACK_LINE = [(0.0, -0.40, 2.70), (0.0, 0.60, 2.62), (0.0, 1.50, 2.56), (0.0, 2.05, 2.50)]   # spine inside the back
SPIKE_SWEEP = 58.0                    # "spike sweep": the spikes lean this far back from the surface normal (deg)
SPIKE_RK = 0.19                       # "spike width": radius = this x length
SPIKE_SQUASH, SPIKE_TIP, SPIKE_BURY = 0.72, 0.38, 0.14
TAIL_TIP_SPIKE = 0.55                 # the tail ends in one long crystal along its tangent (the sheet's tail tip blade)
LEG_SPIKES = [  # (leg, root (L, inside the leg), direction, length): SIDE view forearm shards (830-880, 580-650) up-back,
                #   the hock shards (1150-1180, 630-680) up-back; FRONT view elbow spikes out (90, 555) / (300, 550)
    ("foreleg", (0.67, -0.62, 0.95), (0.35, 0.80, 0.50), 0.42, ".lower"),
    ("foreleg", (0.66, -0.28, 1.45), (0.80, 0.55, 0.20), 0.40, ".upper"),   # the elbow point
    ("hindleg", (0.80, 2.50, 0.85), (0.30, 0.85, 0.45), 0.38, ".lower"),   # the hock point
    ("hindleg", (0.70, 2.10, 1.75), (0.75, 0.60, 0.25), 0.40, ".upper"),
    ("foreleg", (0.68, -0.85, 0.70), (0.75, 0.45, 0.50), 0.32, ".lower"),
    ("hindleg", (0.86, 1.70, 1.80), (0.80, 0.55, 0.35), 0.40, ".upper")]
GEM_LIGHT = (0.45, -0.40, 0.80)       # prism facet light for L-side parts (mirrored for R; X = 0 on midline parts)
GEM_TONE_T = (0.35, -0.25)            # facet . light: +1 lighter above / -1 darker below
GEM_RAMPS = {"blue": ("gem_indigo", "gem_blue", "gem_sky"), "violet": ("gem_indigo", "gem_violet", "gem_lilac"),
             "peach": ("gem_violet", "gem_pink", "gem_peach")}   # dark -> light per family (the prismatic gradients)
SPIKE_FAMILIES = ("violet", "blue", "peach", "violet", "peach", "blue")   # spike family cycle (hashed per spike)
# ---- CLAWS: FRONT view 3 claws per front paw (x 160-190 / 222-250 @ y 725-762), SIDE view claws to x 720 (Y -1.8)
CLAW_X = (-0.12, 0.0, 0.12)           # "claw spread" across the foot
CLAW_LEN, CLAW_W, CLAW_H = 0.30, 0.050, 0.065   # "claw size"
CLAW_DROP = 0.10                      # the claw curls down this much to its tip (tip on the floor)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands)
OWN_TAU = 0.06                        # soft part ownership for weights
LEG_BLEND = (0.12, 0.08)              # leg joint blend half width (hip/knee), the foot blend (above the ankle only)
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent not yet given
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 1.0                          # "breathing": chest rise (spine.002/.003 pitch deg, 2 breaths per loop)
IDLE_HEAD = (1.2, 2.5)                # head nod (2 cyc) / slow look (1 cyc), deg
WING_FLEX = (3.5, 3.0, 4.0, 0.6)      # "wing flex": shoulder spread (deg about Y, outward), elbow fold, wrist fold (deg
                                      #   about X), phase lag per joint (rad)
TAIL_SWAY = (3.0, 5.0, 7.0, 9.0, 1, 0.6)   # "tail sway": per bone yaw deg .001-.004, cyc, lag per bone
WALK_N = 48                           # "walk loop": 2 s
STRIDE, LIFT, DUTY = 0.50, 0.16, 0.75   # "stride" (foot travel), "foot lift", stance share of the cycle
WALK_PHASE = {"hindleg_L": 0.0, "foreleg_L": 0.25, "hindleg_R": 0.5, "foreleg_R": 0.75}   # 4-beat lateral sequence
FOOT_CURL = 14.0                      # swing feet curl (deg)
WALK_BOB = 0.02                       # body bob (2 per loop)
WALK_TAIL = (2.0, 4.0, 6.0, 8.0, 1, 0.6)
WALK_WING = 1.5                       # wings settle slightly with each step (deg)
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
          "design/reference/gemwing_dragon-character-sheet.png)", "tier": "boss", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; the side view (floor at the drawn paws, y 725) is the master profile, "
                   "the front view gives the widths; floor z = 0",
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


def sd_scaled_cone(P, a, b, r1, r2, sx=1.0, sz=1.0):
    """round cone with its X / z axes stretched by sx / sz (scaled by min(1, sx, sz): a valid lower bound)"""
    Q = P.copy(); Q[:, 0] /= sx; Q[:, 2] /= sz
    A = np.array(a, float); B = np.array(b, float)
    A[0] /= sx; B[0] /= sx; A[2] /= sz; B[2] /= sz
    return SD.sd_round_cone(Q, A, B, r1, r2) * min(1.0, sx, sz)


def chain_sdf(P, pts, rads, sx=1.0, k=0.0):
    """round-cone chain; sx < 1 squashes its X section (an oval leg), scaled to a valid lower bound"""
    Q = P.copy(); Q[:, 0] = Q[:, 0] / sx
    d = np.full(len(P), 1e9)
    for (a, b), (ra, rb) in zip(zip(pts[:-1], pts[1:]), zip(rads[:-1], rads[1:])):
        a = np.array(a, float); b = np.array(b, float); a[0] /= sx; b[0] /= sx
        d = SD.smin(d, SD.sd_round_cone(Q, a, b, ra, rb), k) if k > 0 else np.minimum(d, SD.sd_round_cone(Q, a, b, ra, rb))
    return d * min(1.0, sx)


def sd_chest(P):
    return SD.smin(SD.sd_ellipsoid(P, CHEST_C, CHEST_R), sd_scaled_cone(P, *CHEST_V, sx=CHEST_V_SX), 0.12)


def sd_body(P):
    return SD.smin(sd_scaled_cone(P, *BODY, sx=BODY_SX), SD.sd_ellipsoid(P, BELLY[0], BELLY[1]), BELLY_K)


def sd_neck(P):
    return chain_sdf(P, NECK_KNOTS, NECK_RAD, 1.0, 0.04)


def sd_head(P):
    """cranium + two brow ridges (one group: the head)"""
    d = SD.sd_ellipsoid(P, HEAD_C, HEAD_R)
    for s, sg in SIDES:
        d = SD.smin(d, SD.sd_ellipsoid(P, mir(BROW[0], sg), BROW[1]), BROW[2])
    return d


def sd_mouth_cut(P):
    """the mouth line: a thin disc kept only within MOUTH depth of the snout / jaw surface (never splits the head)"""
    c, r, depth, _ = MOUTH
    disc = SD.sd_ellipsoid(P, c, r)
    inner = np.minimum(np.minimum(sd_snout_raw(P), sd_jaw_raw(P)), SD.sd_ellipsoid(P, HEAD_C, HEAD_R)) + depth
    return np.maximum(disc, -inner)


def sd_snout_raw(P):
    return sd_scaled_cone(P, *SNOUT, sx=SNOUT_SX, sz=SNOUT_SZ)


def sd_jaw_raw(P):
    return sd_scaled_cone(P, *JAW, sx=JAW_SX)


def sd_snout(P):
    return np.maximum(sd_snout_raw(P), -sd_mouth_cut(P))


def sd_jaw(P):
    return np.maximum(sd_jaw_raw(P), -sd_mouth_cut(P))


def sd_haunch(P, sg):
    return SD.sd_ellipsoid(P, mir(HAUNCH_C, sg), HAUNCH_R)


def fore_pts(sg):
    return [mir(p, sg) for p in FORE_KNOTS]


def hind_pts(sg):
    return [mir(p, sg) for p in HIND_KNOTS]


def sd_foreleg(P, sg):
    pts = fore_pts(sg) + [mir(FORE_FOOT_C, sg) + np.array([0.0, 0.08, 0.04])]
    d = chain_sdf(P, pts, list(FORE_R) + [0.14], FORE_SX)
    d = SD.smin(d, SD.sd_ellipsoid(P, mir(FORE_SHOULDER[0], sg), FORE_SHOULDER[1]), LEG_K)
    return SD.smin(d, SD.sd_ellipsoid(P, mir(FORE_FOOT_C, sg), FORE_FOOT_R), FOOT_K)


def sd_hindleg(P, sg):
    pts = hind_pts(sg) + [mir(HIND_FOOT_C, sg) + np.array([0.0, 0.08, 0.04])]
    d = chain_sdf(P, pts, list(HIND_R) + [0.15], HIND_SX)
    return SD.smin(d, SD.sd_ellipsoid(P, mir(HIND_FOOT_C, sg), HIND_FOOT_R), FOOT_K)


TK = np.array(TAIL_KNOTS, float)
TAIL_CURVE = SD.catmull(TK, 8)
_tal = SD.arclen(TAIL_CURVE)
TAIL_LEN = float(_tal[-1])


def sd_tail(P):
    return chain_sdf(P, TAIL_KNOTS, TAIL_RAD, 1.0, 0.05)


GROUPS = ["chest", "body", "neck", "head", "snout", "jaw", "haunch.L", "haunch.R", "foreleg.L", "foreleg.R",
          "hindleg.L", "hindleg.R", "tail"]


def group_sdf(P):
    cols = []
    for g_ in GROUPS:
        sg = 1.0 if g_.endswith(".L") else -1.0
        if g_ == "chest":
            d = sd_chest(P)
        elif g_ == "body":
            d = sd_body(P)
        elif g_ == "neck":
            d = sd_neck(P)
        elif g_ == "head":
            d = sd_head(P)
        elif g_ == "snout":
            d = sd_snout(P)
        elif g_ == "jaw":
            d = sd_jaw(P)
        elif g_.startswith("haunch."):
            d = sd_haunch(P, sg)
        elif g_.startswith("foreleg."):
            d = sd_foreleg(P, sg)
        elif g_.startswith("hindleg."):
            d = sd_hindleg(P, sg)
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


GRID_LO, GRID_HI = (-1.20, -2.55, -0.05), (1.20, 6.00, 4.75)
g = SD.Grid(GRID_LO, GRID_HI, SDF_STEP, SDF_BAND)
g.apply(sd_body, *pts_box([BODY[0], BODY[1], BELLY[0]], max(max(BODY[2], BODY[3]) * BODY_SX, max(BELLY[1]))), 0.0)
g.apply(sd_chest, *pts_box([CHEST_C, CHEST_V[0], CHEST_V[1]], max(CHEST_R) * 1.25), CHEST_K)
for s, sg in SIDES:
    g.apply(lambda P, sg=sg: sd_haunch(P, sg), *ell_box(mir(HAUNCH_C, sg), HAUNCH_R), HAUNCH_K)
    g.apply(lambda P, sg=sg: sd_hindleg(P, sg), *pts_box(hind_pts(sg) + [mir(HIND_FOOT_C, sg)], max(HIND_FOOT_R) + 0.32),
            LEG_K)
    g.apply(lambda P, sg=sg: sd_foreleg(P, sg), *pts_box(fore_pts(sg) + [mir(FORE_FOOT_C, sg), mir(FORE_SHOULDER[0], sg)],
                                                          max(FORE_SHOULDER[1]) + 0.05), LEG_K)
g.apply(sd_tail, *pts_box(TAIL_KNOTS, max(TAIL_RAD)), TAIL_K)
g.apply(sd_neck, *pts_box(NECK_KNOTS, max(NECK_RAD)), NECK_K)
g.apply(sd_head, *pts_box([HEAD_C, mir(BROW[0], 1.0), mir(BROW[0], -1.0)], max(HEAD_R) + 0.2), HEAD_K)
g.apply(sd_snout, *pts_box([SNOUT[0], SNOUT[1]], SNOUT[2] * SNOUT_SX), SNOUT_K)
g.apply(sd_jaw, *pts_box([JAW[0], JAW[1]], JAW[2] * JAW_SX), JAW_K)
g.apply(sd_mouth_cut, *ell_box(MOUTH[0], MOUTH[1]), MOUTH[3], mode="subtract")
for s_, sg_ in SIDES:                                         # v6: nostrils near the snout tip
    _nc = mir(NOSTRIL[0], sg_)
    g.apply(lambda P, c=_nc: SD.sd_sphere(P, c, NOSTRIL[1]), _nc - NOSTRIL[1], _nc + NOSTRIL[1], 0.01, mode="subtract")
g.floor(0.0)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
assert np.all(HV.min(0) > np.array(GRID_LO) + SDF_STEP) and np.all(HV.max(0) < np.array(GRID_HI) - SDF_STEP), \
    ("the shell touches the SDF grid box (an open cut)", HV.min(0), HV.max(0))
UNDER_Y = (-1.2, -0.9, -0.6, -0.3, 0.0, 0.5, 1.0, 1.5)   # the belly underline probe stations (Y)


def underline():
    """lowest z of the trunk (chest + body, hard union) on the midline X = 0 at each UNDER_Y station"""
    zs = np.arange(0.0, 3.0, 0.005)
    out = {}
    for y_ in UNDER_Y:
        P = np.stack([np.zeros_like(zs), np.full_like(zs, y_), zs], 1)
        d = np.minimum(sd_chest(P), sd_body(P))
        out["%.1f" % y_] = round(float(zs[np.argmax(d < 0)]), 3) if np.any(d < 0) else None
    return out


report["belly_underline_z"] = underline()


def head_ratio(V):
    D = group_sdf(V)
    own = np.isin(np.argmin(D, 1), [GROUPS.index(g_) for g_ in ("head", "snout", "jaw")])
    H_ = V[own]
    back = float(HEAD_C[1] + HEAD_R[1])                       # the cranium's back (the neck joins there)
    L_ = back - float(H_[:, 1].min()); Hh = float(H_[:, 2].max() - H_[:, 2].min())
    return {"length": round(L_, 3), "height": round(Hh, 3), "length_over_height": round(L_ / Hh, 3),
            "sheet": {"length": 1.25, "height": 0.85, "length_over_height": 1.47},
            "rule": "shell points owned by head / snout / jaw: snout front -> the cranium back (HEAD_C + HEAD_R) over the "
                    "head's vertical extent (brow top -> jaw bottom)"}
print("UNDERLINE", json.dumps(report["belly_underline_z"]))
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation + long-edge split + mirror weld
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
DEC_TRIS = tri_count_F(LF)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
bm = bmesh.new()
_vs = [bm.verts.new(p) for p in LV]
for f in LF:
    bm.faces.new([_vs[i] for i in f])
SPLIT_ROUNDS = []
for it in range(8):
    long_e = [e for e in bm.edges if e.calc_length() > EDGE_MAX]
    if not long_e:
        break
    SPLIT_ROUNDS.append(len(long_e))
    res_ = bmesh.ops.subdivide_edges(bm, edges=long_e, cuts=1, use_grid_fill=False)
    for v in res_["geom_split"]:
        if isinstance(v, bmesh.types.BMVert):
            v.co = bvh_hi.find_nearest(v.co)[0]                 # the new midpoint back onto the high surface
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bmesh.ops.beautify_fill(bm, faces=bm.faces[:], edges=bm.edges[:])   # flip sliver diagonals (no cascade)
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()


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
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic) -> long-edge "
                              "split (> %.2f, midpoints onto the high) + beautify" % (SDF_STEP, EDGE_MAX),
                    "decimated_tris": DEC_TRIS, "split_rounds": SPLIT_ROUNDS, "low_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "mirror_halved": {"seam_verts": SEAM_N, "rule": "X >= 0 half + its mirror (symmetric rest mesh)"},
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(),
                    "seconds": round(time.time() - t, 1)}
print("RETOPO", json.dumps(report["retopo"]))
report["head_proportion"] = head_ratio(LV)
print("HEAD", json.dumps(report["head_proportion"]))
assert np.all(_bb_l / _bb_h > 0.99), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it

# =========================================================================== 3. body facets: symmetric Voronoi cut + tones
t = time.time()
FACET_GROUP = {"chest": "trunk", "body": "trunk", "haunch.L": "trunk", "haunch.R": "trunk", "neck": "neck", "head": "head",
               "snout": "head", "jaw": "head", "foreleg.L": "foreleg", "foreleg.R": "foreleg", "hindleg.L": "hindleg",
               "hindleg.R": "hindleg", "tail": "tail"}
FG_NAMES = sorted(set(FACET_GROUP.values()))
_DL = group_sdf(LV)
LV_G = np.array([FG_NAMES.index(FACET_GROUP[GROUPS[j]]) for j in np.argmin(_DL, 1)])


def vertex_normals(V, F):
    N = np.zeros_like(V)
    for f in F:
        n = np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]])
        for i in f:
            N[i] += n
    return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


# farthest-point seeds on the X >= 0 half, per facet group; deterministic start = the group's highest vertex
SEEDS, SEED_G = [], []
for gi, gname in enumerate(FG_NAMES):
    idx = np.nonzero((LV_G == gi) & (LV[:, 0] >= -1e-9))[0]
    if len(idx) == 0:
        continue
    P_ = LV[idx]
    first = int(np.lexsort((P_[:, 1], -P_[:, 2]))[0])
    chosen = [first]
    dmin = np.linalg.norm(P_ - P_[first], axis=1)
    while True:
        j = int(np.argmax(dmin))
        if dmin[j] < FACET_R:
            break
        chosen.append(j)
        dmin = np.minimum(dmin, np.linalg.norm(P_ - P_[j], axis=1))
    for j in chosen:
        p = P_[j].copy()
        if p[0] < SEAM_SNAP:
            p[0] = 0.0
            SEEDS.append(p); SEED_G.append(gi)
        else:
            SEEDS.append(p); SEED_G.append(gi)
            SEEDS.append(np.array([-p[0], p[1], p[2]])); SEED_G.append(gi)
SEEDS = np.array(SEEDS); SEED_G = np.array(SEED_G)
KD_G = {}
for gi in range(len(FG_NAMES)):
    ii = np.nonzero(SEED_G == gi)[0]
    kd = KDTree(len(ii))
    for k_, i_ in enumerate(ii):
        kd.insert(Vector(SEEDS[i_]), int(i_))
    kd.balance()
    KD_G[gi] = kd


def nearest_seed(p, gi):
    co, idx, d = KD_G[gi].find(Vector(p))
    return idx


bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_el = np.array([e.calc_length() for e in bm.edges])
EDGE_STATS = {k_: round(float(np.percentile(_el, q_)), 4) for k_, q_ in (("p50", 50), ("p90", 90), ("p99", 99), ("max", 100))}
print("EDGES", json.dumps(EDGE_STATS), "n>edge_max", int((_el > EDGE_MAX).sum()), "of", len(_el))
n_sub = []
for it in range(4):                                         # split long edges at their exact midpoints (mirror-exact)
    long_e = [e for e in bm.edges if e.calc_length() > EDGE_MAX]
    if not long_e:
        break
    n_sub.append(len(long_e))
    bmesh.ops.subdivide_edges(bm, edges=long_e, cuts=1, use_grid_fill=False)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.verts.ensure_lookup_table()
SUB_TRIS = len(bm.faces)
_SV = np.array([v.co[:] for v in bm.verts])
_SG = np.array([FG_NAMES.index(FACET_GROUP[GROUPS[j]]) for j in np.argmin(group_sdf(_SV), 1)])
LABEL = np.array([nearest_seed(_SV[i], _SG[i]) for i in range(len(_SV))])
LAB = bm.verts.layers.int.new("cell")
for v in bm.verts:
    v[LAB] = int(LABEL[v.index])
# 3a. edge splits on the exact bisector planes (|p - S_b|^2 - |p - S_a|^2 = 0 is linear along an edge)
snapped, splits = set(), []
for e in bm.edges:
    a, b = e.verts
    la, lb = a[LAB], b[LAB]
    if la == lb:
        continue
    pa, pb = np.array(a.co), np.array(b.co)
    Sa, Sb = SEEDS[la].copy(), SEEDS[lb].copy()
    if abs(pa[0]) < 1e-9 and pb[0] * Sa[0] < 0:                 # a seam vertex's seed is a mirror-pair tie: use the
        Sa[0] = -Sa[0]                                           #   twin on the far end's side (mirror-exact cuts)
    if abs(pb[0]) < 1e-9 and pa[0] * Sb[0] < 0:
        Sb[0] = -Sb[0]
    if np.allclose(Sa, Sb):
        continue
    ga = float((pa - Sb) @ (pa - Sb) - (pa - Sa) @ (pa - Sa))
    gb = float((pb - Sb) @ (pb - Sb) - (pb - Sa) @ (pb - Sa))
    tt = ga / (ga - gb) if ga - gb != 0 else 0.5
    if not (0.0 <= tt <= 1.0):
        tt = 0.5                                             # a cross-group edge: no shared bisector
    if tt < CUT_SNAP:
        snapped.add(a.index)
    elif tt > 1 - CUT_SNAP:
        snapped.add(b.index)
    else:
        splits.append((e, a, tt))
for v in bm.verts:
    if v.index in snapped:
        v[LAB] = -1
for e, a, tt in splits:
    _, nv = bmesh.utils.edge_split(e, a, tt)
    nv[LAB] = -1
# 3b. connect the border vertices inside every face that holds two or more cells
pairs = []
for f in bm.faces:
    vs = list(f.verts)
    labs = {v[LAB] for v in vs if v[LAB] >= 0}
    bv = [v for v in vs if v[LAB] < 0]
    if len(labs) < 2 or len(bv) < 2:
        continue
    pairs.append(bv)
n_connect = 0
for bv in pairs:
    if len(bv) == 2:
        if bv[1] in [e_.other_vert(bv[0]) for e_ in bv[0].link_edges]:
            continue
    try:
        bmesh.ops.connect_verts(bm, verts=bv)
        n_connect += 1
    except Exception:
        pass
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FCELL = []
for f in bm.faces:
    labs = [v[LAB] for v in f.verts if v[LAB] >= 0]
    if labs:
        vals, cnt = np.unique(labs, return_counts=True)
        FCELL.append(int(vals[np.argmax(cnt)]))
    else:
        c_ = np.mean([v.co[:] for v in f.verts], 0)
        gi = int(LV_G[LF[bvh_lo.find_nearest(Vector(c_))[2]][0]])     # the part of the nearest uncut low face
        FCELL.append(nearest_seed(c_, gi))
bm.free()
FCELL = np.array(FCELL)
# 3c. one tone per cell: area-weighted mean normal vs the light, a symmetric jitter, sparse muted accents
FN = np.array([np.cross(CV[f[1]] - CV[f[0]], CV[f[2]] - CV[f[0]]) for f in CF])
FA = np.linalg.norm(FN, axis=1) * 0.5
FC = np.array([CV[f].mean(0) for f in CF])
CELL_N = np.zeros((len(SEEDS), 3)); CELL_A = np.zeros(len(SEEDS)); CELL_C = np.zeros((len(SEEDS), 3))
np.add.at(CELL_N, FCELL, FN)
np.add.at(CELL_A, FCELL, FA)
np.add.at(CELL_C, FCELL, FC * FA[:, None])
CELL_C = CELL_C / np.maximum(CELL_A, 1e-12)[:, None]
CELL_N = CELL_N / np.maximum(np.linalg.norm(CELL_N, axis=1), 1e-12)[:, None]
_fl = unit(FACET_LIGHT)


def seed_hash(p, salt):
    return (math.sin(abs(p[0]) * 91.17 + p[1] * 47.31 + p[2] * 13.73 + salt * 7.77) * 43758.5453) % 1.0


CELL_TONE = []
for c_ in range(len(SEEDS)):
    d = float(CELL_N[c_] @ _fl) + FACET_JIT * (seed_hash(SEEDS[c_], 1.0) - 0.5)
    h = seed_hash(SEEDS[c_], 2.0)
    if h < ACCENT_FRAC[0]:
        CELL_TONE.append("body_accent_cool")
    elif h < ACCENT_FRAC[0] + ACCENT_FRAC[1]:
        CELL_TONE.append("body_accent_warm")
    else:
        CELL_TONE.append("body_lit" if d > FACET_TONE_T[0] else ("body_shade" if d < FACET_TONE_T[1] else "body"))
shell_reg = np.array([CELL_TONE[c_] for c_ in FCELL], dtype=object)
CELL_SHADE = np.array([0.975 + 0.05 * seed_hash(SEEDS[c_], 3.0) for c_ in range(len(SEEDS))])
_used = np.unique(FCELL)
report["facets"] = {"seeds": int(len(SEEDS)), "cells_used": int(len(_used)), "facet_r": FACET_R,
                    "groups": {gname: int((SEED_G == gi).sum()) for gi, gname in enumerate(FG_NAMES)},
                    "edge_splits": len(splits), "snapped_verts": len(snapped), "face_connects": n_connect,
                    "long_edge_splits": n_sub, "edge_max": EDGE_MAX, "low_edge_lengths": EDGE_STATS,
                    "shell_tris_before": tri_count_F(LF), "shell_tris_after_long_edge_split": SUB_TRIS,
                    "shell_tris_after": tri_count_F(CF),
                    "tone_cells": {k_: int(sum(1 for c_ in _used if CELL_TONE[c_] == k_)) for k_ in sorted(set(CELL_TONE))},
                    "seconds": round(time.time() - t, 1),
                    "rule": "farthest-point seeds per body part on the X >= 0 half (mirrored; within %.2f of X = 0 on the "
                            "midline), Euclidean nearest seed within the part, every cell border split on the exact "
                            "bisector plane; one tone per cell (mean normal . light + jitter), accents by a symmetric hash"
                            % SEAM_SNAP}
print("FACETS", json.dumps(report["facets"]))
# 3d. REAL facets: every shell vertex onto its cell's plane; a border vertex onto the intersection of its cells' planes
#     (regularised least squares: order-free, so mirror pairs stay mirror-exact); soles stay on the floor
t = time.time()
VCELLS = [set() for _ in range(len(CV))]
for fi, f in enumerate(CF):
    for v in f:
        VCELLS[v].add(int(FCELL[fi]))
CV0 = CV.copy()
_mv = np.zeros(len(CV))
for i in range(len(CV)):
    cs = sorted(VCELLS[i])
    Nm = CELL_N[cs]
    rr = np.einsum("ij,ij->i", Nm, CV0[i][None] - CELL_C[cs])
    lam = np.linalg.solve(Nm @ Nm.T + FACET_LS_EPS * np.eye(len(cs)), rr)
    dv = -(Nm.T @ lam)
    m_ = float(np.linalg.norm(dv))
    if m_ > FACET_MAX_MOVE:
        dv *= FACET_MAX_MOVE / m_
    CV[i] = CV0[i] + dv
    _mv[i] = min(m_, FACET_MAX_MOVE)
_kdm = KDTree(len(CV0))                                       # exact mirror pairs of the (symmetric) cut shell
for i in range(len(CV0)):
    _kdm.insert(Vector(CV0[i]), i)
_kdm.balance()
_mj = np.array([_kdm.find(Vector((-CV0[i][0], CV0[i][1], CV0[i][2])))[1] for i in range(len(CV0))])
_mjd = np.array([_kdm.find(Vector((-CV0[i][0], CV0[i][1], CV0[i][2])))[2] for i in range(len(CV0))])
assert float(_mjd.max()) < 1e-6, ('the cut shell lost its mirror pairs', float(_mjd.max()))
_CVm = CV[_mj].copy(); _CVm[:, 0] *= -1.0
CV = 0.5 * (CV + _CVm)                                        # the facet moves made mirror-exact (the cut's triangulation
                                                              #   is not mirror-ordered, so cell planes differ by ~1e-2)
CV[np.abs(CV0[:, 0]) < 1e-9, 0] = 0.0
CV[CV0[:, 2] < 1e-6, 2] = 0.0
CV[:, 2] = np.maximum(CV[:, 2], 0.0)
_pl = []                                                      # planarity: per-face normal vs its cell plane (deg)
for fi, f in enumerate(CF):
    n_ = np.cross(CV[f[1]] - CV[f[0]], CV[f[2]] - CV[f[0]])
    if np.linalg.norm(n_) > 1e-12:
        _pl.append(math.degrees(math.acos(min(1.0, abs(float(unit(n_) @ CELL_N[FCELL[fi]]))))))
report["facet_geometry"] = {"move_p50": round(float(np.percentile(_mv, 50)), 4), "move_p99": round(float(np.percentile(_mv, 99)), 4),
                            "move_max": round(float(_mv.max()), 4), "capped": int((_mv >= FACET_MAX_MOVE - 1e-9).sum()),
                            "face_vs_cell_plane_deg_p50": round(float(np.percentile(_pl, 50)), 3),
                            "face_vs_cell_plane_deg_p95": round(float(np.percentile(_pl, 95)), 3),
                            "seconds": round(time.time() - t, 1),
                            "rule": "LOW shell vertices onto their facet planes (border vertices onto the planes' "
                                    "intersection, regularised LS eps %.2f), moves capped at %.2f" % (FACET_LS_EPS, FACET_MAX_MOVE)}
print("FACET_GEOM", json.dumps(report["facet_geometry"]))

# =========================================================================== 4. parts: eyes, crown, spikes, wings, claws
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=8.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


def project(pts, n):
    return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])


def mirror_part(p_, name):
    V_ = p_.V.copy(); V_[:, 0] *= -1.0
    q_ = FP.Part(name, V_, [f_[::-1] for f_ in p_.F], p_.R, bone=p_.bone.replace("_L", "_R") if p_.bone else None)
    q_.kind = p_.kind
    if getattr(p_, "W", None) is not None:
        q_.W = p_.W.copy(); q_.wbones = [b_.replace("_L", "_R") for b_ in p_.wbones]
    return q_


PARTS = []
FACE = {}
# ---- eyes (L; R = the mirror)
_ed = unit((-1.0, 0.30, 0.0))
S, n = cast(np.array([0.0, EYE_Y, EYE_Z]) - _ed * 3.0, _ed)
ns_ = [cast(np.array([0.0, EYE_Y + dy, EYE_Z + dz]) - _ed * 3.0, _ed)[1] for dy, dz in ((0, 0), (0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03))]
ne = unit(np.mean(ns_, 0))
O = FP.rotate2(FP.lens_curve(EYE_A, EYE_B, 20, EYE_SHARP), EYE_TILT)     # decal x = e2 = up x n: +Y (back) on the L eye
V, F, R = FP.polar_decal(S, ne, O, project, DECAL_UP, DECAL_DN, DECAL_DOME, 2, "iris", up_hint=(0, 0, 1))
p_ = FP.Part("iris.L", V, F, R, bone="head"); p_.kind = "irises"; PARTS.append(p_)
Op = FP.rotate2(FP.lens_curve(PUPIL_AB[0], PUPIL_AB[1], 16, 0.6), EYE_TILT * 0.5)
V, F, R = FP.polar_decal(S, ne, Op, project, DECAL_UP + DECAL_DOME + 0.006, DECAL_DN, 0.004, 2, "pupil", up_hint=(0, 0, 1))
p_ = FP.Part("pupil.L", V, F, R, bone="head"); p_.kind = "pupils"; PARTS.append(p_)
FACE["eye.L"] = {"surface": S.round(4).tolist(), "normal": ne.round(4).tolist()}
for p_ in [p_ for p_ in PARTS if p_.name.endswith(".L")]:
    PARTS.append(mirror_part(p_, p_.name[:-2] + ".R"))


# ---- prism tone (light by side; the length band picks the gradient step)
def gem_toner(family, sg):
    ramp = GEM_RAMPS[family]
    lt = unit((GEM_LIGHT[0] * sg, GEM_LIGHT[1], GEM_LIGHT[2])) if sg != 0 else unit((0.0, GEM_LIGHT[1], GEM_LIGHT[2]))
    nb = None

    def tone(n_, band):
        d = float(np.dot(n_, lt))
        sh = 1 if d > GEM_TONE_T[0] else (-1 if d < GEM_TONE_T[1] else 0)
        base = {0: 0, 1: 1, 2: 1}.get(band, 2)
        return ramp[int(np.clip(base + sh, 0, 2))]
    return tone


SPIKE_INFO = []


def add_spike(name, base, tip, rad, fam, bone, kind, sg):
    """sg = +1 L-side part (mirrored afterwards), 0 midline (X-symmetric by construction: spin 0, flattened across X)"""
    base = np.asarray(base, float); tip = np.asarray(tip, float)
    V, F, R = FP.gem_blade(base, tip, rad, SPIKE_SQUASH, SPIKE_TIP, SPIKE_BURY + 0.4 * rad, 0.0, 0.0 if sg == 0 else 0.25,
                           1.08, (1.0, 0.0, 0.0), gem_toner(fam, sg), bands=(0.45,))
    p_ = FP.Part(name, V, F, R, bone=bone); p_.kind = kind
    PARTS.append(p_)
    SPIKE_INFO.append({"name": name, "kind": kind, "family": fam, "length": round(float(np.linalg.norm(tip - base)), 3),
                       "tip": tip.round(3).tolist(), "bone": bone})
    return p_


def fam_of(i, salt):
    return SPIKE_FAMILIES[int(FP.hash01(i, salt) * len(SPIKE_FAMILIES)) % len(SPIKE_FAMILIES)]


# crown (v3, artist v2 verdict "can the crown component be one merged piece"): ONE closed faceted crystal solid -- a
# raised crystal BAND wrapping the head top (two stacked loops, design/reference/gemwing_dragon-v2-crown-annotation.png)
# with the upright points growing out of it, smooth-unioned in one SDF, polygonised, decimated, mirror-welded
def sdf_part(name, fn, lo, hi, step, target_tris, weld=True):
    """one closed faceted part from an SDF: polygonise (kit) -> collapse decimation to target_tris -> (mirror weld about
    X = 0 for a midline part). Returns V, F, report; asserts ONE connected piece"""
    lo = np.asarray(lo, float); hi = np.asarray(hi, float)
    g_ = SD.Grid(lo, hi, step, max(3 * step, 0.08))
    g_.apply(fn, lo, hi, 0.0)
    V_, F_ = SD.polygonise(g_)
    F_ = [list(map(int, f)) for f in F_]
    V_, F_, isl = keep_islands(V_, F_, 0.01)                   # drop sub-1% tip crumbs (under-sampled blade points)
    _, _, isl1 = keep_islands(V_, F_, 0.0)
    assert isl1["pieces"] == 1, ("%s is not one piece" % name, isl, isl1)
    ob_ = new_obj(name + "_dec", V_, F_)
    dm_ = ob_.modifiers.new("dec", "DECIMATE"); dm_.decimate_type = "COLLAPSE"; dm_.use_collapse_triangulate = True
    dm_.ratio = min(1.0, target_tris / len(F_))
    V2, F2 = evaluated_arrays(ob_)
    bpy.data.objects.remove(ob_, do_unlink=True)
    nh = len(F_)
    if weld:
        V2, F2, _ = mirror_halve(V2, F2)
    return np.asarray(V2), F2, {"high_tris": nh, "tris": tri_count_F(F2), "pieces_after_crumb_drop": isl1["pieces"], "crumbs": isl}


def ramp_tone(fam, light, n_, band):
    ramp = GEM_RAMPS[fam]
    d = float(np.dot(n_, light))
    sh = 1 if d > GEM_TONE_T[0] else (-1 if d < GEM_TONE_T[1] else 0)
    return ramp[int(np.clip(band + sh, 0, 2))]


CROWN_SEGS = []                                               # (base, tip, base radius, family) incl. the mirrored side
_cb, _ = cast((0.0, CROWN_C[1], 7.0), (0.0, 0.0, -1.0))
CROWN_SEGS.append((_cb + np.array([0.0, 0.0, CROWN_BAND[1]]), _cb + np.array([0.0, 0.0, CROWN_CENTRE[0]]),
                   CROWN_CENTRE[1], "blue"))
_CROWN_FAM = ("violet", "peach", "blue", "violet", "peach")
for i, (th, L_, r_) in enumerate(CROWN_POINTS):
    for sgn in ((1.0,) if th in (0.0, 180.0) else (1.0, -1.0)):
        t_ = math.radians(th)
        x_, y_ = sgn * CROWN_RING[0] * math.sin(t_), CROWN_C[1] - CROWN_RING[1] * math.cos(t_)
        b_, _ = cast((x_, y_, 7.0), (0.0, 0.0, -1.0))
        b_ = b_ + np.array([0.0, 0.0, CROWN_BAND[1]])
        rad_ = unit(np.array([x_, y_ - CROWN_C[1], 0.0]))
        ax = unit(np.array([0.0, 0.0, 1.0]) + CROWN_SPLAY * rad_)
        CROWN_SEGS.append((b_, b_ + ax * L_, r_, _CROWN_FAM[i]))
_ring = []
for k in range(CROWN_BAND[3]):                                # the band loops follow the head top
    t_ = 2 * math.pi * k / CROWN_BAND[3]
    x_, y_ = CROWN_RING[0] * math.sin(t_), CROWN_C[1] - CROWN_RING[1] * math.cos(t_)
    _ring.append(cast((x_, y_, 7.0), (0.0, 0.0, -1.0))[0])
_ring = np.array(_ring)
_RIDGE = [cast((0.0, y_, 7.0), (0.0, 0.0, -1.0))[0] + np.array([0.0, 0.0, CROWN_RIDGE_LIFT]) for y_ in CROWN_RIDGE_Y]
_RIDGE[0] = _RIDGE[0] + np.array([0.0, 0.0, CROWN_BAND[1]])  # starts on the band's front
_NOSE, _ = cast((0.0, -4.0, SNOUT[1][2]), (0.0, 1.0, 0.0))   # the nose front on the midline
_HOOK = np.array([0.0, _NOSE[1] - CROWN_HOOK[0], _RIDGE[-1][2] + CROWN_HOOK[1]])
_RIDGE_R = np.linspace(CROWN_RIDGE_R[0], CROWN_RIDGE_R[1], len(_RIDGE))


def sd_ridge(P):
    Q = P.copy(); Q[:, 0] /= CROWN_RIDGE_SX                   # a plate: wider than tall (a valid bound, scaled by 1)
    d = np.full(len(P), 1e9)
    for k in range(len(_RIDGE) - 1):
        d = np.minimum(d, SD.sd_round_cone(Q, _RIDGE[k], _RIDGE[k + 1], _RIDGE_R[k], _RIDGE_R[k + 1]))
    return SD.smin(d, SD.sd_round_cone(P, _RIDGE[-1], _HOOK, _RIDGE_R[-1], CROWN_HOOK[2]), 0.03)


_cap_c = np.array([0.0, CROWN_C[1], _cb[2] + 0.5 * (CROWN_BAND[0] + CROWN_BAND[1])])
_cap_r = (CROWN_RING[0] + 0.02, CROWN_RING[1] + 0.02, 0.12)


def sd_crown(P):
    d = np.full(len(P), 1e9)
    for lift in (CROWN_BAND[0], CROWN_BAND[1]):               # two stacked loops = the band
        R_ = _ring + np.array([0.0, 0.0, lift])
        for k in range(len(R_)):
            d = SD.smin(d, SD.sd_round_cone(P, R_[k], R_[(k + 1) % len(R_)], CROWN_BAND[2], CROWN_BAND[2]), 0.03)
    d = SD.smin(d, SD.sd_ellipsoid(P, _cap_c, _cap_r), 0.04)   # the base plate filling the band (one solid)
    d = SD.smin(d, sd_ridge(P), 0.05)                         # v4: the ridge forward to the hooked nose tip
    for b_, t_, r_, _ in CROWN_SEGS:
        d = SD.smin(d, SD.sd_round_cone(P, b_ - unit(t_ - b_) * 0.05, t_, r_, 0.012), CROWN_K)
    return d


_cp = np.vstack([np.array([b_, t_]) for b_, t_, _, _ in CROWN_SEGS] + [_ring, np.array(_RIDGE), _HOOK[None]])
V, F, _crep = sdf_part("crown", sd_crown, _cp.min(0) - 0.15, _cp.max(0) + 0.15, CROWN_STEP, CROWN_TRIS)
_cl0 = unit((0.0, GEM_LIGHT[1], GEM_LIGHT[2]))
_zb, _zt = _ring[:, 2].min(), max(t_[2] for _, t_, _, _ in CROWN_SEGS)
R = []
for f in F:
    c_ = V[f].mean(0); n_ = unit(FP.face_normal(V, f))
    dseg = [float(FP.spine_param(c_[None], np.array([b_, t_]))[2][0]) for b_, t_, _, _ in CROWN_SEGS]
    j = int(np.argmin(dseg))
    fam = CROWN_SEGS[j][3] if dseg[j] < 0.10 else "violet"
    if c_[1] < CROWN_C[1] - CROWN_RING[1] - 0.10:             # the ridge: blue -> sky toward the nose hook
        R.append(ramp_tone("blue", _cl0, n_, 1 if c_[1] > CROWN_RIDGE_Y[-1] else 2))
        continue
    hb = (c_[2] - _zb) / max(_zt - _zb, 1e-6)
    R.append(ramp_tone(fam, _cl0, n_, 0 if hb < 0.18 else (1 if hb < 0.55 else 2)))
p_ = FP.Part("crown", V, F, R, bone="head"); p_.kind = "crown"
PARTS.append(p_)
CROWN_INFO = {"points": len(CROWN_SEGS), "tips": [t_.round(3).tolist() for _, t_, _, _ in CROWN_SEGS], **_crep,
              "ridge": [r_.round(3).tolist() for r_ in _RIDGE], "hook_tip": _HOOK.round(3).tolist(),
              "hook_projection_beyond_nose": round(float(_NOSE[1] - _HOOK[1]), 4),
              "hook_rise_over_ridge_end": round(float(_HOOK[2] - _RIDGE[-1][2]), 4),
              "rule": "one SDF: a raised band (two loops at +%.2f / +%.2f over the head top, r %.2f) + %d upright points "
                      "+ the ridge plate forward over the snout to a hooked nose tip, smooth-unioned (k %.2f) -> one "
                      "closed faceted solid, mirror-welded"
                      % (CROWN_BAND[0], CROWN_BAND[1], CROWN_BAND[2], len(CROWN_SEGS), CROWN_K)}
# rows along curves: root = the shell hit of a ray cast from outside toward the curve point
NECK_CURVE = SD.catmull(np.array(NECK_KNOTS, float), 8)
BACK_CURVE = SD.catmull(np.array(BACK_LINE, float), 8)
CURVES = {"neck": NECK_CURVE, "back": BACK_CURVE, "tail": TAIL_CURVE}


def curve_at(C, frac):
    al = SD.arclen(C)
    s_ = frac * al[-1]
    p = np.array([np.interp(s_, al, C[:, k]) for k in range(3)])
    i_ = min(int(np.searchsorted(al, s_)), len(C) - 1)
    tng = unit(C[min(i_ + 1, len(C) - 1)] - C[max(i_ - 1, 0)])
    return p, tng


def row_bone(kind, p):
    if kind == "neck":
        return "neck.002" if p[2] > 3.40 else "neck.001"
    if kind == "back":
        return "spine.001" if p[1] > 0.85 else "spine.002"
    _, s_a, _ = FP.spine_param(p[None], TAIL_BONE_PTS_M)
    return "tail.%03d" % (min(int(s_a[0] / (TAIL_LEN / 4.0)), 3) + 1)


TAIL_BONE_PTS_M = np.array([np.array([np.interp(x, _tal, TAIL_CURVE[:, k]) for k in range(3)]) for x in np.linspace(0, TAIL_LEN, 5)])
_sw = math.radians(SPIKE_SWEEP)
for kind, (mids, sides, phi_deg) in SPIKE_ROWS.items():
    C = CURVES[kind]
    for j, (fr, L_) in enumerate(mids):
        c_, tng = curve_at(C, fr)
        dors = unit(np.cross([1.0, 0.0, 0.0], tng))
        root, _ = cast(c_ + dors * 2.5, -dors)
        ax = unit(math.cos(_sw) * dors + math.sin(_sw) * tng)
        add_spike("%s.M.%02d" % (kind, j + 1), root, root + ax * L_, max(0.05, SPIKE_RK * L_), fam_of(j, len(kind) + 0.3),
                  row_bone(kind, c_), kind + "_spikes", 0)
    phi = math.radians(phi_deg)
    for j, (fr, L_) in enumerate(sides):
        c_, tng = curve_at(C, fr)
        dors = unit(np.cross([1.0, 0.0, 0.0], tng))
        lat = unit(np.cross(tng, dors))                         # +X side for a +Y tangent
        if lat[0] < 0:
            lat = -lat
        out = unit(math.cos(phi) * dors + math.sin(phi) * lat)
        root, _ = cast(c_ + out * 2.5, -out)
        ax = unit(math.cos(_sw) * out + math.sin(_sw) * tng)
        add_spike("%s.L.%02d" % (kind, j + 1), root, root + ax * L_, max(0.05, SPIKE_RK * L_), fam_of(j, len(kind) + 0.7),
                  row_bone(kind, c_), kind + "_spikes", 1)
# the tail-tip crystal: continues the tail along its final tangent
_tp, _tt = curve_at(TAIL_CURVE, 0.985)
_tt = unit(np.array([0.0, _tt[1], max(_tt[2], -0.05)]))
add_spike("tail.M.tip", _tp - _tt * 0.10, _tp + _tt * TAIL_TIP_SPIKE, 0.065, "violet", "tail.004", "tail_spikes", 0)
# leg spikes (L)
for j, (lg, root, dr, L_, bsfx) in enumerate(LEG_SPIKES):
    root = np.asarray(root, float)
    out = unit(np.array([dr[0], dr[1] * 0.25, 0.0]))
    hit, _ = cast(root + out * 1.5, -out)
    ax = unit(dr)
    add_spike("legspike.L.%02d" % (j + 1), hit, hit + ax * L_, max(0.06, SPIKE_RK * L_), fam_of(j, 9.1),
              lg + "_L" + bsfx, "leg_spikes", 1)
# ---- wings (v3, artist v2 verdict "same for the wings, more one piece like the original reference"): per side ONE
#      closed faceted crystal wing -- the layered blades fused (smooth union) along their length and at their bases on
#      the wing arm, one SDF, polygonised + decimated (L; R = the mirror). Faces take the prismatic tone of the blade
#      they belong to (family ramp by the position along the blade + the light) and streak cells
_WA = np.array(WING_ARM, float)
_wn = unit(np.cross(_WA[3] - _WA[0], np.array(WING_BLADES[3][1]) - _WA[0]))
if _wn[0] < 0:
    _wn = -_wn                                                # outward (+X on the L wing)
WB = []
_ARMD = SD.resample(SD.catmull(_WA, 8), 200)
for i, (b_, t_, r_, fam, layer) in enumerate(WING_BLADES):
    b_ = np.asarray(b_, float)
    b_ = _ARMD[int(np.argmin(np.linalg.norm(_ARMD - b_, axis=1)))]   # every blade grows FROM the wing arm (fused base)
    WB.append((b_ + _wn * WING_LAYER_OFF * layer, np.asarray(t_, float) + _wn * WING_LAYER_OFF * layer,
               r_ * WING_RSCALE, fam))


def sd_flat_cone(P, root, tip, r0, r1, nrm, flat):
    """a round cone root -> tip flattened along nrm (made perpendicular to its axis): one wing blade"""
    a = unit(tip - root)
    n = unit(np.asarray(nrm, float) - a * float(np.dot(nrm, a)))
    w = np.cross(a, n)
    Q = P - root
    L = float(np.linalg.norm(tip - root))
    q = np.stack([Q @ w, Q @ a, (Q @ n) / flat], 1)
    return SD.sd_round_cone(q, (0.0, 0.0, 0.0), (0.0, L, 0.0), r0, r1) * flat


_HEXN = []                                                    # the squashed hexagon's 6 edge normals + offsets (unit radius)
_hc_ = [np.array([WING_SQUASH * math.cos(k * math.pi / 3), math.sin(k * math.pi / 3)]) for k in range(6)]
for k in range(6):
    e_ = _hc_[(k + 1) % 6] - _hc_[k]
    n2 = np.array([e_[1], -e_[0]]); n2 = n2 / np.linalg.norm(n2)
    if n2 @ (_hc_[k] + _hc_[(k + 1) % 6]) < 0:
        n2 = -n2
    _HEXN.append((n2, float(n2 @ _hc_[k])))


def sd_hex_blade(P, base, tip, r):
    """a long straight faceted crystal blade: a hexagonal prism (squashed across the wing plane) with a pyramid tip --
    flat faces, sharp edges (a bound-correct-enough field for the polygoniser)"""
    a = unit(tip - base)
    u = unit(_wn - a * float(_wn @ a)); v = np.cross(a, u)
    Q = P - base
    t = Q @ a; x = Q @ u; y = Q @ v
    L = float(np.linalg.norm(tip - base)); Lb = L * (1.0 - WING_TIPF)
    sc = np.clip((L - t) / (L - Lb), 0.0, 1.0)
    d = np.full(len(P), -1e9)
    for n2, h_ in _HEXN:
        d = np.maximum(d, x * n2[0] + y * n2[1] - h_ * r * sc)
    return np.maximum(d, np.maximum(-t - 0.10, t - L))


def blade_fields(P):
    return np.stack([sd_hex_blade(P, b_, t_, r_) for b_, t_, r_, _ in WB], 1)


_ARM = np.vstack([np.array(WING_ROOT_BURY)[None], _WA])


def sd_wing(P):
    d = np.full(len(P), 1e9)
    for k in range(len(_ARM) - 1):                            # the wing arm (leading-edge bone), buried at the shoulder
        d = SD.smin(d, SD.sd_round_cone(P, _ARM[k], _ARM[k + 1], WING_ARM_R[k], WING_ARM_R[k + 1]), 0.04)
    D = blade_fields(P)
    for j in range(D.shape[1]):
        d = SD.smin(d, D[:, j], WING_FUSE_K)
    return d


_wp = np.vstack([np.array([b_, t_]) for b_, t_, _, _ in WB] + [_ARM])
V, F, _wrep = sdf_part("wing", sd_wing, _wp.min(0) - 0.35, _wp.max(0) + 0.35, WING_STEP, WING_TRIS, weld=False)
FCw = np.array([V[f].mean(0) for f in F])
_bj = np.argmin(blade_fields(FCw), 1)
R = []
for fi, f in enumerate(F):                                    # ONE gradient per blade: the ramp step by the position
    b_, t_, r_, fam = WB[_bj[fi]]                             #   along it (root -> tip), no light shift, no streaks
    tt = float((FCw[fi] - b_) @ unit(t_ - b_)) / float(np.linalg.norm(t_ - b_))
    R.append(WING_RAMPS[fam][int(np.clip(tt * 4.0, 0, 3))])
p_ = FP.Part("wing.L", V, F, R, bone="wing_L.001"); p_.kind = "wings"
_, s_w, _ = FP.spine_param(V, _WA)                            # weights along the ONE wing bone chain (parent spine.002)
p_.wbones = ["spine.002", "wing_L.001", "wing_L.002", "wing_L.003"]
p_.W = K.vine_weights(np.clip(s_w, 0, None), float(SD.arclen(_WA)[-1]), 3, parent_s0=0.0)
PARTS.append(p_)
WING_INFO = {**_wrep, "blades_fused": len(WB), "plane_normal_L": _wn.round(4).tolist(), "arm": WING_ARM,
             "tips": [t_.round(3).tolist() for _, t_, _, _ in WB],
             "rule": "one SDF per wing: the wing arm (round cones, buried at the shoulder) + %d long straight hex-prism "
                     "blades (flat faces, pyramid tips) in near-hard union (k %.3f) -> one closed faceted solid; one "
                     "root -> tip gradient per blade (4 ramp steps, no streaks); weights: rigkit.vine_weights along "
                     "wing_X.001-.003 (parent spine.002)" % (len(WB), WING_FUSE_K)}
# ---- claws (L): 3 per foot, from the foot front curling down to the floor
_cl_light = unit((0.45, -0.40, 0.80))


def claw_tone(n_):
    return "gem_violet" if float(n_ @ _cl_light) > 0.55 else "claw"


CLAW_INFO = []
for lg, fc, fr in (("foreleg", FORE_FOOT_C, FORE_FOOT_R), ("hindleg", HIND_FOOT_C, HIND_FOOT_R)):
    fc = np.asarray(fc, float); fr = np.asarray(fr, float)
    for j, dx in enumerate(CLAW_X):
        x_ = fc[0] + dx * (1.0 + 0.6 * abs(dx))
        base = np.array([x_, fc[1] - 0.70 * fr[1], 0.11])
        spread = np.array([dx * 0.35, 0.0, 0.0])
        tip = np.array([x_ + dx * 0.25, fc[1] - 0.70 * fr[1] - CLAW_LEN * (1.0 - 0.25 * abs(dx) / 0.12), 0.006])
        ctrl = np.array([0.5 * (x_ + tip[0]), 0.5 * (base[1] + tip[1]) - 0.04, base[2] + 0.02]) + spread * 0.2
        V, F, R = FP.claw(base, ctrl, tip, CLAW_W, CLAW_H, 4, claw_tone, side_hint=(1.0, 0.0, 0.0))
        p_ = FP.Part("claw.L.%s.%d" % (lg, j + 1), V, F, R, bone=lg + "_L.foot"); p_.kind = "claws"
        PARTS.append(p_)
        CLAW_INFO.append({"name": p_.name, "tip": tip.round(3).tolist()})
# ---- chest armour (v4): ONE starburst piece on the upper chest -- a faceted centre gem + pointed rays laid along the
#      chest surface (each ray root -> mid -> tip projected onto the shell and lifted), one SDF, decimated, mirror-welded
_S0, _ = cast((0.0, -4.0, CHEST_STAR_C), (0.0, 1.0, 0.0))
_n0 = np.array([0.0, -1.0, 0.0])
_e1 = np.array([0.0, 0.0, 1.0]); _e2 = np.array([1.0, 0.0, 0.0])
_SC = _S0 + _n0 * CHEST_STAR_LIFT[0]
STAR_RAYS = []                                                # (root, mid, tip, family)
_SFAM = ("blue", "peach", "violet", "peach", "blue", "violet")
for i, (th, L_) in enumerate(CHEST_STAR_RAYS):
    for sgn in ((1.0,) if th in (0.0, 180.0) else (1.0, -1.0)):
        t_ = math.radians(th)
        dr = sgn * math.sin(t_) * _e2 + math.cos(t_) * _e1
        pts_ = []
        for fr in (0.5, 1.0):
            q_ = _S0 + dr * L_ * fr
            hit, nh = cast(q_ + np.array([0.0, -3.0, 0.0]), np.array([0.0, 1.0, 0.0]))   # laid onto the chest from the front
            pts_.append(hit + unit(nh) * CHEST_STAR_LIFT[1] * (2.0 if fr < 1.0 else 1.0))
        STAR_RAYS.append((_SC, pts_[0], pts_[1], _SFAM[i]))


def sd_star(P):
    Q = P - _SC
    d = SD.sd_ellipsoid(Q @ np.eye(3), np.zeros(3), (CHEST_STAR_GEM[0], CHEST_STAR_GEM[2], CHEST_STAR_GEM[1]))
    for a_, m_, t_, _ in STAR_RAYS:
        d = SD.smin(d, SD.smin(SD.sd_round_cone(P, a_, m_, CHEST_STAR_R[0], 0.6 * CHEST_STAR_R[0] + 0.4 * CHEST_STAR_R[1]),
                               SD.sd_round_cone(P, m_, t_, 0.6 * CHEST_STAR_R[0] + 0.4 * CHEST_STAR_R[1], CHEST_STAR_R[1]),
                               0.02), CHEST_STAR_K)
    return d


_spts = np.vstack([np.array([a_, m_, t_]) for a_, m_, t_, _ in STAR_RAYS])
V, F, _srep = sdf_part("chest_star", sd_star, _spts.min(0) - 0.2, _spts.max(0) + 0.2, CHEST_STAR_STEP, CHEST_STAR_TRIS)
R = []
for f in F:
    c_ = V[f].mean(0); n_ = unit(FP.face_normal(V, f))
    dr_ = [float(FP.spine_param(c_[None], np.array([a_, m_, t_]))[2][0]) for a_, m_, t_, _ in STAR_RAYS]
    j = int(np.argmin(dr_))
    if np.linalg.norm(c_ - _SC) < 0.24:                       # the centre gem: violet, lit facets lilac
        R.append(ramp_tone("violet", _cl0, n_, 1))
    else:
        L_ = float(np.linalg.norm(STAR_RAYS[j][2] - _SC))
        R.append(ramp_tone(STAR_RAYS[j][3], _cl0, n_, 1 if np.linalg.norm(c_ - _SC) < 0.6 * L_ else 2))
p_ = FP.Part("chest_star", V, F, R, bone="spine.003"); p_.kind = "chest_armour"
PARTS.append(p_)
ARMOUR_INFO = {**_srep, "points": len(STAR_RAYS), "centre": _SC.round(3).tolist(),
               "tips": [t_.round(3).tolist() for _, _, t_, _ in STAR_RAYS],
               "rule": "one SDF: a centre gem (half axes %s) + %d rays laid along the chest (root -> mid -> tip, projected "
                       "+ lifted), smooth-unioned (k %.2f) -> one closed faceted solid, mirror-welded"
                       % (list(CHEST_STAR_GEM), len(STAR_RAYS), CHEST_STAR_K)}
for p_ in [p_ for p_ in PARTS if (".L." in p_.name or p_.name == "wing.L") and p_.kind in (
        "neck_spikes", "back_spikes", "tail_spikes", "leg_spikes", "wings", "claws", "chest_armour")]:
    PARTS.append(mirror_part(p_, p_.name.replace(".L.", ".R.") if ".L." in p_.name else "wing.R"))

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
REG = ["body_lit", "body", "body_shade", "body_accent_cool", "body_accent_warm", "iris", "pupil", "claw",
       "gem_indigo", "gem_blue", "gem_sky", "gem_violet", "gem_lilac", "gem_pink", "gem_peach"]
BODY_REG = {"body_lit", "body", "body_shade", "body_accent_cool", "body_accent_warm"}
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (must never borrow a neighbour's field): %s" % _empty
assert not (set(RNAME[:len(CF)]) - BODY_REG), "the shell carries only the toned body regions"


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
report["glow"] = {"skin": "default", "glowing_regions": _lit, "rule": "no glow (the sheet's sheen is colour, not light): "
                  "zero Glow"}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
_shade = jitter(VALL, FALL)
_shade[:len(CF)] = CELL_SHADE[FCELL]                          # a facet is ONE flat tone (no per-triangle speckle)
PAL.store_regions(me, REG, rid, _shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})


# the colour split (artist direction): area-weighted HSV saturation of the body vs the wings + spikes
def sat_of(name):
    return colorsys.rgb_to_hsv(*[c / 255.0 for c in pal_default["regions"][name]["rgb"]])[1]


_sat = np.array([sat_of(r) for r in REG])[rid]
_kind = np.array(["shell"] * len(CF) + [k_ for p in PARTS for k_ in [p.kind] * len(p.F)], dtype=object)
_split = {}
for nm_, mask in (("body_shell", _kind == "shell"), ("wings", _kind == "wings"), ("crown", _kind == "crown"),
                  ("chest_armour", _kind == "chest_armour"),
                  ("spikes_all", np.isin(_kind, ["neck_spikes", "back_spikes", "tail_spikes", "leg_spikes"])),
                  ("claws", _kind == "claws")):
    w_ = fa[mask]
    _split[nm_] = {"area_weighted_mean_sat": round(float((_sat[mask] * w_).sum() / w_.sum()), 4),
                   "share_sat_gt_0.4": round(float(w_[_sat[mask] > 0.4].sum() / w_.sum()), 4), "area": round(float(w_.sum()), 3)}
report["colour_split"] = {**_split, "rule": "HSV saturation of each face's palette sRGB, area-weighted (artist direction: "
                          "body toned down, the most colour on the wings + spikes)"}
print("COLOUR_SPLIT", json.dumps(report["colour_split"]))

# facing landmark: the head centre -> the snout tip on the midline
HEAD_CTR = np.array(HEAD_C, float)
Sn, _ = cast((0.0, -4.0, SNOUT[1][2]), (0, 1, 0))
anchor = HEAD_CTR - SHIFT
landmark = Sn - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the snout tip (front cast on the midline at the snout tip height)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["wings"] = WING_INFO
report["crown"] = CROWN_INFO
report["chest_armour"] = ARMOUR_INFO
report["spikes"] = {"count": len(SPIKE_INFO) + sum(1 for s_ in SPIKE_INFO if ".L." in s_["name"]), "built_L_and_midline": SPIKE_INFO}
report["claws"] = {"per_foot": len(CLAW_X), "total": 4 * len(CLAW_X), "L": CLAW_INFO}

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
report["mirror_test"] = {"max_abs_asymmetry": round(float(_mir.max()), 6), "p99": round(float(np.percentile(_mir, 99)), 6),
                         "per_piece_max": {k_: round(float(_mir[v_[0]:v_[1]].max()), 4) for k_, v_ in RANGE.items()
                                           if float(_mir[v_[0]:v_[1]].max()) > 0.01},
                         "rule": "every rest vertex mirrored across X = 0 (after the centring shift) -> distance to the "
                                 "nearest point of the mesh"}
print("MIRROR", json.dumps(report["mirror_test"]))
tris_main = tri_count_F(FALL)
_part_tris = {}
for p in PARTS:
    _part_tris[p.kind] = _part_tris.get(p.kind, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_facet_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("BOSS tier (artist approved 2026-10-11: v5 verdict 'why does the dragon look so low poly' -> 'yes do it'): window [%d, %d], the extra budget spent on the body shell (finer crystal facets). The shell carries the head, neck, deep chest, four legs + feet "
                            "and the tail, cut into ~%d planar toned facets; each wing and the crown are ONE fused faceted crystal "
                            "solid, the chest armour faceted gem plates, the neck / back / tail / leg spikes closed hex "
                            "prisms, the claws faceted points (low-poly by design: facets ARE the look)." % (TRI_BUDGET[0], TRI_BUDGET[1], len(_used)))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, decals, spikes, blades, claws)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"front_view_height_px": 621, "front_view_width_px": 320,
                               "side_view_height_px_to_drawn_paws": 653, "side_view_length_px": 660},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "boss"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch: design/reference/gemwing_dragon-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_wing_pts = np.vstack([p.V for p in PARTS if p.name == "wing.L"]) - SHIFT
_crown_pts = np.vstack([p.V for p in PARTS if p.kind == "crown"] + [np.array([[0.0, -2.40, 3.55]])]) - SHIFT
low["conquest_focus"] = json.dumps({
    "head": [(_crown_pts.min(0) - 0.10).tolist(), (_crown_pts.max(0) + 0.10).tolist()],
    "wing": [_wing_pts.min(0).tolist(), _wing_pts.max(0).tolist()],
    "neck": [(np.array([-0.9, -1.8, 2.2]) - SHIFT).tolist(), (np.array([0.9, 0.2, 4.3]) - SHIFT).tolist()],
    "tail": [(np.array([-1.0, 1.0, 0.0]) - SHIFT).tolist(), (np.array([1.0, 6.4, 3.3]) - SHIFT).tolist()],
    "views": {"head": [62.0, 10.0], "wing": [100.0, 12.0], "neck": [55.0, 8.0], "tail": [118.0, 14.0]},
    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "facets", "tris", "measure", "facing",
                                                            "open_edges", "mirror_test", "regions_area_share", "glow",
                                                            "colour_split", "face")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = faceted SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
# v3: the facets are GEOMETRY now, so the bake high's shell IS the faceted low shell (the normal map stays flat on the
# facets and carries only the parts' contact; the AO carries the occlusion)
BAKE_CAGE = 0.02
HV_s = CV - SHIFT
HF = [list(f) for f in CF]
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
me.shade_flat()                                               # v3: the facets are geometry and the high IS the faceted shell:
                                                              #   bake against the FLAT low (a smooth-shaded bake re-encoded
                                                              #   every facet tilt, then the flat render applied it twice:
                                                              #   grey mottled body)
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
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled faceted-high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "the faceted LOW shell itself (facets are geometry) + every "
                                         "closed part",
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
                     "provenance": "pixel-sampled from the sheet's chip row (y 852) + drawn surfaces (see the palette notes)"}
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


SPINE_KNOTS = [(0.0, 2.20, 2.35), (0.0, 1.30, 2.35), (0.0, 0.40, 2.40), (0.0, -0.35, 2.45), (0.0, -0.55, 2.80)]
NECK_BONE = [(0.0, -0.55, 2.80), (0.0, -0.90, 3.38), (0.0, -1.12, 3.95)]
HEAD_PTS = ((0.0, -1.12, 3.95), (0.0, -1.40, 4.35))
BONES = [("hips", P_(SPINE_KNOTS[1]), P_(SPINE_KNOTS[0]), "root"),
         ("spine.001", P_(SPINE_KNOTS[1]), P_(SPINE_KNOTS[2]), "hips"),
         ("spine.002", P_(SPINE_KNOTS[2]), P_(SPINE_KNOTS[3]), "spine.001"),
         ("spine.003", P_(SPINE_KNOTS[3]), P_(SPINE_KNOTS[4]), "spine.002"),
         ("neck.001", P_(NECK_BONE[0]), P_(NECK_BONE[1]), "spine.003"),
         ("neck.002", P_(NECK_BONE[1]), P_(NECK_BONE[2]), "neck.001"),
         ("head", P_(HEAD_PTS[0]), P_(HEAD_PTS[1]), "neck.002")]
for s, sg in SIDES:
    for k in range(3):                                        # the wing arm: shoulder -> elbow -> wrist -> tip
        BONES.append(("wing_%s.%03d" % (s, k + 1), P_(mir(WING_ARM[k], sg)), P_(mir(WING_ARM[k + 1], sg)),
                      "spine.002" if k == 0 else "wing_%s.%03d" % (s, k)))
LEGS = {}                                             # leg -> {"pts": [top, knee, ankle, toe] (model space), "parent"}
for s, sg in SIDES:
    LEGS["foreleg_" + s] = {"pts": fore_pts(sg) + [mir(FORE_TOE, sg)], "parent": "spine.003", "group": "foreleg." + s,
                            "haunch": None}
    LEGS["hindleg_" + s] = {"pts": hind_pts(sg) + [mir(HIND_TOE, sg)], "parent": "hips", "group": "hindleg." + s,
                            "haunch": "haunch." + s}
for lg, L_ in LEGS.items():
    pts = L_["pts"]
    BONES += [(lg + ".upper", P_(pts[0]), P_(pts[1]), L_["parent"]), (lg + ".lower", P_(pts[1]), P_(pts[2]), lg + ".upper"),
              (lg + ".foot", P_(pts[2]), P_(pts[3]), lg + ".lower")]
TAIL_BONE_PTS = list(TAIL_BONE_PTS_M)
for k in range(4):
    BONES.append(("tail.%03d" % (k + 1), P_(TAIL_BONE_PTS[k]), P_(TAIL_BONE_PTS[k + 1]), "hips" if k == 0 else "tail.%03d" % k))
CONNECT = {"spine.002", "spine.003", "neck.001", "neck.002", "head", "tail.002", "tail.003", "tail.004",
           "wing_L.002", "wing_L.003", "wing_R.002", "wing_R.003"} | \
          {lg + sfx for lg in LEGS for sfx in (".lower", ".foot")}
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
# trunk: hips / spine.001-.003 hat weights along the spine polyline (rump -> chest)
_, s_sp, _ = FP.spine_param(Pw, np.array(SPINE_KNOTS))
L_sp = float(SD.arclen(np.array(SPINE_KNOTS))[-1])
Wv = K.vine_weights(np.clip(s_sp, 0, L_sp), L_sp, 4)
for gname in ("chest", "body"):
    gi = GROUPS.index(gname)
    Ws[:, J["hips"]] += Wg[:, gi] * (Wv[:, 0] + Wv[:, 1])
    Ws[:, J["spine.001"]] += Wg[:, gi] * Wv[:, 2]
    Ws[:, J["spine.002"]] += Wg[:, gi] * Wv[:, 3]
    Ws[:, J["spine.003"]] += Wg[:, gi] * Wv[:, 4]
# neck: spine.003 -> neck.001 -> neck.002 -> head along the neck chain
_, s_nk, _ = FP.spine_param(Pw, np.array(NECK_BONE + [HEAD_PTS[1]]))
L_nk = float(SD.arclen(np.array(NECK_BONE + [HEAD_PTS[1]]))[-1])
Wn = K.vine_weights(np.clip(s_nk, 0, L_nk), L_nk, 3, parent_s0=0.0)
gi = GROUPS.index("neck")
Ws[:, J["spine.003"]] += Wg[:, gi] * Wn[:, 0]
Ws[:, J["neck.001"]] += Wg[:, gi] * Wn[:, 1]
Ws[:, J["neck.002"]] += Wg[:, gi] * Wn[:, 2]
Ws[:, J["head"]] += Wg[:, gi] * Wn[:, 3]
for gname in ("head", "snout", "jaw"):
    Ws[:, J["head"]] += Wg[:, GROUPS.index(gname)]


def leg_weights(P, lg):
    """-> (n, 4) weights [parent, upper, lower, foot] by the arc parameter on the leg polyline extended 0.5 above its top:
    joint blends of half width LEG_BLEND[0] at the top / knee, the foot blend only ABOVE the ankle (the whole foot rides
    the foot bone rigidly: planted feet keep their soles on the floor)"""
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
        for k, sfx in enumerate((".upper", ".lower", ".foot")):
            Ws[:, J[lg + sfx]] += Wg[:, gi] * WL[:, k + 1]
gi = GROUPS.index("tail")
_, s_arc, _ = FP.spine_param(Pw, np.array(TAIL_BONE_PTS))
Wv = K.vine_weights(np.clip(s_arc, 0, TAIL_LEN), TAIL_LEN, 4)
Ws[:, J["hips"]] += Wg[:, gi] * Wv[:, 0]
for k in range(4):
    Ws[:, J["tail.%03d" % (k + 1)]] += Wg[:, gi] * Wv[:, k + 1]
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "W", None) is not None:                      # the fused wings: weighted along their bone chain
        for k_, bn_ in enumerate(p.wbones):
            Wt[a_:b_, J[bn_]] += p.W[:, k_]
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between chest, body, neck, head, snout, jaw, haunches, "
                          "four legs, tail; trunk hips / spine.001-.003 hat weights along the spine; neck spine.003 -> "
                          "neck.001 -> neck.002 -> head along the neck chain; legs + haunches: parent -> upper -> lower -> "
                          "foot by the leg-chain arc (the foot rigid below the ankle); tail: rigkit.vine_weights along "
                          "tail.001-.004 (parent hips); irises, pupils, crown spikes rigid on the head; neck / back / "
                          "tail / leg spikes rigid on the bone at their root; wing blades rigid on the wing-arm bone at their base "
                          "(wing_X.001 / .002 / .003); claws rigid on the foot" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics). Per frame: the trunk / head / wings / tail are posed,
#      then every leg is solved by a planar (YZ) 2-bone IK to its foot target (rest + the clip's foot offset), the foot
#      bone keeping its world pitch; a floor guard lifts the whole body + targets if any vertex dips below z 0 (pass 1);
#      keyed from the stored channels (pass 2, frame N+1 = frame 1); measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
REST_T = {b.name: np.array(b.tail_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM
LEG_BONES = {lg + sfx for lg in LEGS for sfx in (".upper", ".lower", ".foot")}


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
    """legs: {leg: (dy, lift, foot_pitch_deg)} -> sets the three leg bones of every leg (planar 2-bone IK). Returns the
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
        rig.pose.bones[lg + ".foot"].rotation_quaternion = local_quat(lg + ".foot", [("X", math.degrees(t3 - t2))])
    return short


def tail_pose(P, u, spec, phase0):
    amps, cyc, lag = spec[:4], spec[4], spec[5]
    for k in range(4):
        ang = amps[k] * math.sin(TAU * cyc * u + phase0 - k * lag)
        P["tail.%03d" % (k + 1)] = (None, [("Z", ang)], None)


def wing_pose(P, a1, a2, a3):
    """the wing arm: .001 spreads (about +Y, the L wing out to +X; R mirrored), .002 / .003 fold + unfold (about X:
    mirror-symmetric) with a lag"""
    for s, sg in SIDES:
        P["wing_%s.001" % s] = (None, [("Y", sg * a1)], None)
        P["wing_%s.002" % s] = (None, [("X", a2)], None)
        P["wing_%s.003" % s] = (None, [("X", a3)], None)


def idle_pose(u):
    P = {}
    br = math.sin(TAU * 2 * u)
    P["spine.002"] = (None, [("X", -0.5 * BREATH * br)], None)   # the chest rises (pitch about the mid back)
    P["spine.003"] = (None, [("X", -0.5 * BREATH * br)], None)
    P["neck.001"] = (None, [("X", 0.5 * BREATH * br)], None)     # the head stays level
    P["neck.002"] = (None, [("X", 0.5 * BREATH * br)], None)
    P["head"] = (None, [("X", IDLE_HEAD[0] * math.sin(TAU * 2 * u - 0.9)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u + 0.3))], None)
    wing_pose(P, WING_FLEX[0] * math.sin(TAU * u), WING_FLEX[1] * math.sin(TAU * u - WING_FLEX[3]),
              WING_FLEX[2] * math.sin(TAU * u - 2 * WING_FLEX[3]))
    tail_pose(P, u, TAIL_SWAY, 0.4)
    return P, {}, {"lift": 0.0}


def walk_foot(u, ph):
    """foot offset (dy, lift, pitch) at loop phase u for a leg of phase ph: stance (DUTY of the cycle) carries the planted
    foot back from -STRIDE/2 to +STRIDE/2 (forward = -Y), the swing returns it with a sine lift + toe curl"""
    p = (u - ph) % 1.0
    if p < DUTY:
        return (STRIDE * (p / DUTY - 0.5), 0.0, 0.0)
    q = (p - DUTY) / (1.0 - DUTY)
    sq = q * q * (3 - 2 * q)
    return (STRIDE * (0.5 - sq), LIFT * math.sin(math.pi * q), -FOOT_CURL * math.sin(math.pi * q))


def walk_pose(u):
    """PLACEHOLDER 4-beat walk in place (the game glides the unit): lateral sequence LH, LF, RH, RF; stance feet planted
    on the floor and carried back at the stride speed, swing feet lifted; a small body bob, head counter-nod, wings
    settling, tail sway."""
    P = {}
    bob = WALK_BOB * math.sin(TAU * 2 * u)
    P["hips"] = ((0.0, 0.0, bob), [], None)
    P["head"] = (None, [("X", -1.0 * math.sin(TAU * 2 * u + 0.6))], None)
    wa = WALK_WING * math.sin(TAU * 2 * u - 0.8)
    wing_pose(P, wa, 0.6 * wa, 0.4 * wa)
    tail_pose(P, u, WALK_TAIL, 1.0)
    legs = {lg: walk_foot(u, WALK_PHASE[lg]) for lg in LEGS}
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
        if bn == "hips":
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
# sole vertices: dominant deform weight on a foot bone, below z 0.05 at rest (the planted-foot probe)
_foot_j = [J[lg + ".foot"] for lg in LEGS]
SOLE = np.nonzero(np.isin(np.argmax(Wt, 1), _foot_j) & (VALL[:, 2] < 0.05))[0]
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
        rows.append({"minz": float(C[:, 2].min()), "hips_z": float(pbs["hips"].head.z),
                     "tail_tip": list(pbs["tail.004"].tail), "head_tail": list(pbs["head"].tail),
                     "wing_L": list(pbs["wing_L.003"].tail), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("hips_z") - REST_H["hips"][2]
    tt_ = np.array([r["tail_tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_]); wl = np.array([r["wing_L"] for r in R_])
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
                    "hips_lift": [round(float(bz.min()), 5), round(float(bz.max()), 5)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "ik_reach_shortfall_max": round(float(max(SHORT[cn])), 5),
                    "sole_verts": int(len(SOLE)),
                    "planted_sole_slide_max_vs_ground": round(max(slide) if slide else 0.0, 5),
                    "sole_min_z": round(float(S_[:, :, 2].min()), 5),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist(),
                    "head_top_p2p_xyz": (hd.max(0) - hd.min(0)).round(4).tolist(),
                    "wing_L_tip_p2p_xyz": (wl.max(0) - wl.min(0)).round(4).tolist()}
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
rig["conquest_rig"] = ("gemwing_dragon v1: root > hips > spine.001 > spine.002 > spine.003 > neck.001 > neck.002 > head; "
                       "spine.003 > foreleg_{L,R}.upper > .lower > .foot; hips > hindleg_{L,R}.upper > .lower > .foot; "
                       "spine.002 > wing_{L,R}.001 > .002 > .003; hips > tail.001 > .002 > .003 > .004")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s: breathing (chest "
                               "rise, head level), small head nod / look, a wing flex (the wing spreads %.1f deg at "
                               "the shoulder, elbow + wrist fold with a lag), tail sway (travelling yaw wave); feet held planted by a "
                               "planar 2-bone IK. walk = 2 s 4-beat walk in place (lateral sequence LH, LF, RH, RF; stance "
                               "75%%; stride %.2f, foot lift %.2f sheet units): stance feet planted on the floor and "
                               "carried back, swing feet lifted + curled; wings settle with each step. Feet never below "
                               "z 0, root still." % (WING_FLEX[0], STRIDE, LIFT))
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
              "structure": "armature identity + skinned 'gemwing_dragon' (one opaque body material); natural scale; "
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
