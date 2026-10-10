"""Pangolin Knight -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/pangolin_knight_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNITS x2: Pangolin Knight, Shadow Mole (artist sheets)" (section PANGOLIN
KNIGHT) + the sheet design/reference/pangolin_knight-character-sheet.webp (intake: design/reference/pangolin_knight/
intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. Brief: improve/pangolin_knight_v1_brief.md (v1
DRAFT). An upright bipedal pangolin: cream face / chest / belly with a soft fur edge, a short snout + pink nose, a simple
black eye; HELMET-like overlapping head scales with a crest; layered rounded-pointed brown leaf SCALES in rows over the
back, shoulders, flanks and thighs ("simple layered scales, smooth rounded shapes, not too many scales"); a LONG
SEGMENTED tapering TAIL curving down to the floor behind; forelimbs held forward in armour-band GAUNTLETS with three dark
claws; short legs, clawed dark feet. Unarmed (artist question P1). Clips PLACEHOLDER.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The sheet's FRONT is drawn three-quarter, so every constant is
measured off the two TRUE orthos (intake ortho_frame.views = back, side); the front is a 3/4 check only:
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 990) / 100 ; z = (790 - y_px) / 100
  BACK view (the unit's right on image right):          X = -(x_px - 548) / 100 ; z = (782 - y_px) / 100
X +ve = the unit's LEFT (L). FLOOR: z = 0 is the SOLE line (side y 790 = the foot soles x 860-970; back y 782 = the
visible foot x 440-500; front y 783-791), NOT the intake floor_y_px (side 818 / back 811 = the drawn TAIL TIP, which the
sheet draws 28 / 29 px below the soles). The ortho renders put z = 0 on the intake floor line, so every BUILD outline in
sheet_vs_model.png sits 28 px (side) / 29 px (back) LOW against the drawing by construction (reported; the fix is the
intake's floor line, outside this lane's allowlist). X = 0 = the back view's mirror plane x 548 (silhouette midpoints
z 2.2-5.0: x 547-549); the intake axis x 554 is therefore model X -0.06 (conquest_focus.axis_xy carries it).

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the EGG torso (z, front, back, half-width profile),
     the big round HEAD (smooth union = the neck), the short SNOUT (round cone, the pink nose tip a region), two ARMS
     (upper arm + forearm round cones, held forward), two LEGS (thigh + shin + a rounded FOOT); clamped flat on the floor.
     Marching tets = the HIGH (bake source); collapse decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: part ownership (torso / arms / legs), the CREAM field (front of
     the torso by azimuth, its edge a soft scalloped wave with a tan fur band; the face below the helmet line), the pink
     NOSE, the head cap under the helmet, the feet + foot bands.
  3. PARTS (pangolin_knight_parts.py, all closed): the SCALES -- one closed thin plate each (outer skin + inner skin +
     side walls), a rounded-pointed leaf outline, root buried in the body, tip lifted out to the sheet-measured scale
     ENVELOPE, rows stacked so each row overlaps the next (brick-staggered columns, alternate columns lifted), three brown
     tones + a darker outline band; the HELMET (radial head scales: visor, crown crest, hood); the segmented TAIL (one
     closed loft whose radius steps at every band edge, light / mid bands + a cream underside); two GAUNTLETS (lathes:
     cuff band, dark gauntlet, front rim band); 6 hand CLAWS + 6 foot CLAWS (closed lofts); the EYES (closed conforming
     decals cast along the side view) + their glints.
  4. palette (palettes.py regions -> Col / Glow; no glow), Smart UV, bake normal + AO from the SDF high + parts, AO floor
     per region class.
  5. RIG: root > hips > spine > chest > head (helmet, eyes rigid); chest > arm.{L,R}.{0,1,2} (1 = forearm + gauntlet,
     2 = the claws); hips > leg.{L,R}.{0,1,2} (2 = the foot, planted); hips > tail.0..3 (vine chain). Clips idle + walk =
     PLACEHOLDER. glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                 # noqa: E402  (read-only use)
import palettes as PAL             # noqa: E402  (read-only use)
import magmoo_sdf as SD            # noqa: E402  (read-only use: the SDF kit)
import pangolin_knight_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0 at the
# soles)
UNIT = "pangolin_knight"
CHAR_ID = "pangolin_knight"           # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.30                       # BAND >= max smooth-union k (HEAD_K 0.22) + 2 steps (BUILD_CONTRACT kit hazard)
BODY_TRIS = 5000                      # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- the EGG torso. Rows (z, front Y, back Y, half-width X). Front (cream chest / belly) from the SIDE view outline:
#   z 1.1-1.3 -> x 886-887 (-1.03), 1.5 -> x 875 (-1.15), 1.7 -> 867 (-1.23), 2.0 -> 862 (-1.28), 2.2 -> 863 (-1.27),
#   3.2 -> 859 (-1.31), 3.5 -> 858 (-1.32); the belly bottom bulge ends z 1.1 (the legs below). Back Y + half-width are
#   hidden under the scales on every view: the BACK view's narrowest scale silhouette (x 462-634 @ y 542, +-0.86 @ z 2.4,
#   where the scales tuck behind the arms) bounds the half-width; the back follows the scale stack's inner edge.
EGG_PROFILE = [(0.92, -0.45, 0.10, 0.00), (1.00, -0.80, 0.30, 0.50), (1.15, -1.00, 0.40, 0.70), (1.45, -1.13, 0.45, 0.78),
               (1.80, -1.24, 0.45, 0.80), (2.10, -1.27, 0.42, 0.79), (2.50, -1.26, 0.33, 0.75), (2.90, -1.28, 0.18, 0.71),
               (3.30, -1.30, 0.00, 0.64), (3.60, -1.28, -0.20, 0.54), (3.90, -1.20, -0.38, 0.42), (4.15, -1.10, -0.50, 0.00)]
# ---- head (big, round; one mass with the body). SIDE view: face front x 785 @ y 370 (Y -2.05, z 4.2), x 794 @ y 390
#   (-1.96, 4.0); jaw under the snout y 430 (z 3.6) @ x 802-826; BACK view: head (inside the helmet) x 474-622 (+-0.74);
#   the helmet scales sit on its upper half
HEAD_C = (0.0, -1.30, 4.42)           # "head position"
HEAD_R = (0.74, 0.80, 0.86)           # "head size" (half width / depth / height)
HEAD_K = 0.22                         # "neck": smooth union head <-> body
# ---- snout + nose. SIDE view (4x crop): nose tip x 751 @ y 402-415 (Y -2.39, z 3.88-3.75), snout top x 780 @ y 382
#   (-2.10, 4.08), snout underside y 420 (z 3.70) @ x 770
SNOUT = ((0.0, -1.86, 4.06), (0.0, -2.24, 3.85), 0.30, 0.15)   # "snout" (base, tip, base radius, tip radius)
SNOUT_K = 0.10
NOSE_C, NOSE_R = (0.0, -2.36, 3.84), 0.12  # "pink nose": the snout tip inside this sphere is the nose
# ---- arms (held forward). SIDE view: the forearm leaves the chest at x 885-890 (Y -1.05..-1.0, z 2.6-2.9 under the
#   shoulder scale); BACK view: the gauntlets x 411-458 / 638-685 (|X| 0.90-1.37) @ y 512-552 (z 2.3-2.7)
SHOULDER = (0.60, -0.70, 3.05)
ELBOW = (0.96, -0.92, 2.72)
WRIST = (1.05, -1.20, 2.62)
ARM_R = (0.22, 0.20, 0.19)            # "arm thickness" (shoulder, elbow, wrist)
ARM_K = 0.12
# ---- gauntlets (armour bands). SIDE view (4x crop): cuff band x 862-885 (Y -1.28..-1.05), dark gauntlet x 830-862
#   (-1.60..-1.28), front rim band x 812-830 (-1.78..-1.60); height y 502-562 (z 2.88-2.28: radius 0.30); BACK view
#   half-width ~0.24 -> an oval section
GAUNT_AXIS = ((1.06, -1.04, 2.65), (1.10, -1.76, 2.52))   # back end -> front end (L side)
GAUNT_PROF = [(0.00, -0.04), (0.20, -0.04), (0.255, 0.00), (0.270, 0.04), (0.270, 0.20), (0.250, 0.225), (0.262, 0.25),
              (0.290, 0.36), (0.290, 0.52), (0.272, 0.58), (0.268, 0.60), (0.268, 0.68), (0.240, 0.705), (0.18, 0.735),
              (0.00, 0.75)]           # "gauntlet shape": (radius, distance along the axis)
GAUNT_REG = ["gauntlet", "gauntlet_band", "gauntlet_band", "gauntlet_band", "gauntlet_band", "gauntlet", "gauntlet",
             "gauntlet", "gauntlet", "gauntlet_band", "gauntlet_band", "gauntlet_band", "gauntlet", "gauntlet"]
GAUNT_OVAL = (1.0, 0.84)              # vertical / horizontal radius scale (SIDE r 0.30 vs BACK r ~0.24)
GAUNT_SEG = 20
# ---- hand claws: three per gauntlet. SIDE view (4x crop): tips x 772 @ y 552 (Y -2.18, z 2.38) / x 785 @ y 565
#   (-2.05, 2.25); roots in the gauntlet front x 820 (Y -1.70)
HCLAW_ROOT_H = 0.62                   # claw roots sit this far along the gauntlet axis (inside the front cap)
HCLAW_SPREAD = [(-0.13, -0.02), (0.0, 0.05), (0.13, -0.02)]   # (lateral, up) offset of each claw root
HCLAW_LEN, HCLAW_DROP = 0.52, 0.20    # "claw length" forward / how far each claw curves down to its tip
HCLAW_W, HCLAW_H = 0.11, 0.12         # claw half-width / half-height at the root
# ---- legs (short) + feet. BACK view: shin x 461-511 (X 0.37-0.87) @ y 712-752 (z 0.3-0.7); SIDE view: shin + foot
#   x 863-990 (Y -1.27..0) @ y 700-790, foot top y 750 (z 0.40), heel x 990 (Y 0.0); thighs under the scale skirt
HIP = (0.55, -0.30, 1.30)
KNEE = (0.62, -0.36, 0.74)
ANKLE = (0.66, -0.32, 0.32)
LEG_R = (0.34, 0.26, 0.23)            # "leg thickness" (hip, knee, ankle)
LEG_K = 0.15
FOOT = ((0.66, 0.02, 0.19), (0.69, -0.66, 0.18), 0.19, 0.18)   # "foot" heel -> toe ball (centres, radii)
FOOT_K = 0.08
FOOT_TOP = 0.40                       # below this the leg is the (brown) foot
FOOT_BAND_Y = (-0.72, -0.58)          # "foot band": the light band across the foot behind the claws (SIDE x 912-945)
# ---- foot claws: three big dark hoof-claws per foot. SIDE view: x 855-912 (Y -1.35..-0.78) @ y 747-790
FCLAW_YAW = (-22.0, 0.0, 22.0)        # spread of the 3 toes (deg about the foot direction) + FOOT_YAW toe-out
FOOT_YAW = 8.0
FCLAW_LEN, FCLAW_W, FCLAW_H = 0.58, 0.13, 0.15   # "foot claw" length / half-width / half-height at the root
# ---- SCALES (body). Carrier = a vertical cylinder round the egg's centre line A(z) = (0, yc(z)); a scale runs from its
#   ROOT (buried in the body at z_root) DOWN to its TIP (lifted out to the scale envelope at z_tip) along azimuth theta
#   (deg from the front toward each side; 180 = the back midline). ENVELOPE = the sheet silhouette of the scale tips:
#   SIDE view back outline (theta 180: Y at z) and BACK view half-width (theta 90: X at z), elliptical between.
ENV_BACK_Y = [(4.40, 0.05), (4.00, 0.15), (3.60, 0.12), (3.20, 0.32), (2.80, 0.55), (2.40, 0.76), (2.00, 0.97),
              (1.60, 1.10), (1.20, 1.25), (0.80, 1.25)]   # SIDE view x 995 @ y 350 ... x 1110 @ y 630, x 1115 @ y 670
ENV_SIDE_X = [(4.30, 1.10), (4.00, 1.00), (3.60, 0.95), (3.20, 1.02), (2.80, 1.00), (2.40, 0.90), (2.00, 1.02),
              (1.60, 1.10), (1.20, 1.22), (0.80, 1.18)]   # BACK view x 438 @ y 352 ... x 426 @ y 662 (tip maxima)
FRONT_FLARE = 0.14                    # toward the front (theta < 90) the envelope = the body front + this
MIN_FLARE = 0.10                      # every tip stands at least this far off the body
SCALE_ROWS = [  # (z root, z tip, [theta columns], half-width) -- brick rows: A rows on 180/144/108/72, B rows on 162/126/90
    (4.30, 3.30, [180.0, 144.0, 108.0, 72.0], 0.44),
    (3.88, 2.88, [162.0, 126.0, 90.0], 0.44),
    (3.46, 2.46, [180.0, 144.0, 108.0, 72.0], 0.46),
    (3.04, 2.04, [162.0, 126.0, 90.0], 0.46),
    (2.62, 1.62, [180.0, 144.0, 108.0, 72.0], 0.46),
    (2.20, 1.25, [162.0, 126.0, 90.0], 0.44),
    (1.78, 0.95, [144.0, 108.0, 72.0], 0.42),
    (1.40, 0.80, [108.0, 72.0], 0.40)]   # the last row = the thigh skirt (SIDE view: scales end z 0.9 over the thighs)
SCALE_U = [0.0, 0.22, 0.45, 0.66, 0.84, 1.0]   # "scale detail": stations root -> tip
SCALE_V = [-1.0, -0.82, -0.40, 0.0, 0.40, 0.82, 1.0]   # stations across (+-0.82..1 = the darker outline band)
LEAF = (0.55, 0.42, 1.6, 0.08)        # "leaf shape": root width share, widest point, tip pointedness (1 = straight
                                      #   sides, 2 = round), tip width share (rounded-pointed, never a needle)
SCALE_EASE = 2.0                      # how fast a scale rises off the body (1 - (1 - u)^EASE)
TIP_CURL = 0.05                       # "tip lift": extra outward lift at each tip (x u^3)
SCALE_CROWN = 0.10                    # "scale dome": the centre line stands this share of the half-width above the edges
SCALE_THICK = (0.040, 0.020)          # plate thickness root -> tip
SCALE_BURY = 0.04                     # roots sink this far into the body (hidden under the row above)
SCALE_CLEAR = 0.012                   # the plate's inner skin keeps this clear of the body
COL_LIFT = 0.028                      # alternate columns of a row ride this much higher (neighbours overlap cleanly)
TONE_DARK_SHARE = 0.18                # this share of scales (hash) runs one tone darker ("slight colour variation")
# ---- HELMET: a COHESIVE CAP of broad rounded scales seated on the head (gate 2026-10-09: "a cohesive cap, 2-3
#   overlapping rows that hug the skull with no gaps, a smooth visor, ONE pointed crest leaf on top at the back"). Sphere
#   carrier round HELM_C; each scale runs root direction (az, el) -> tip direction (az, el) (deg; az 0 = front, 90 = the
#   unit's left; el from the horizontal) and its radius is SKULL-RELATIVE all along: the head surface under it + an
#   offset rising from off_root (buried) to off_tip, so the plate follows the skull instead of flaring off it. Rows: BRIM
#   (6, the visor its front scale), MID (6, staggered 30 deg, tips over the brim roots), CROWN (3, tips over the mid
#   roots), CREST (1, back midline pointing up, riding on top). SIDE view: brow overhang x 739-760 @ y 325-340 (Y
#   -2.51..-2.30, z 4.5-4.65); helmet top y 228-235 (z 5.6); BACK view: crest leaf x 525-575 @ y 220-300 (z 5.6-4.8,
#   tip up), hood sides x 432-436 @ y 342-352 (X 1.12-1.16, z 4.3-4.4)
HELM_C = (0.0, -1.25, 4.55)
HELM_LEAF = (0.62, 0.45, 1.9, 0.12)   # "helmet scale shape": rounder than the body leaves (smooth rounded shapes)
CREST_LEAF = (0.50, 0.40, 1.05, 0.06)  # the crest leaf: pointed (back view); tip exponent 1.35 -> 1.05 (1 = straight point: the sharp almond)
HELM_SCALES = [  # (root (az, el), tip (az, el), half-width, off_root, off_tip, leaf, tone)
    ((0.0, 40.0), (0.0, 0.0), 0.62, -0.04, 0.43, "helm", "scale_light"),     # BRIM: the VISOR (smooth brow overhang;
                                                                         #   artist "more aligned" 2026-10-09: off_tip 0.34 -> 0.43 toward the drawn reach Y -2.51)
    ((60.0, 38.0), (64.0, -6.0), 0.58, -0.04, 0.20, "helm", "scale"),        #   front-sides (tips stay above the eye)
    ((120.0, 38.0), (122.0, -18.0), 0.58, -0.04, 0.24, "helm", "scale_dark"),  # back-sides (the hood)
    ((180.0, 40.0), (180.0, -24.0), 0.58, -0.04, 0.28, "helm", "scale"),     #   back (over the neck)
    ((30.0, 72.0), (34.0, 26.0), 0.54, -0.02, 0.24, "helm", "scale"),        # MID row (staggered 30 deg)
    ((90.0, 70.0), (92.0, 22.0), 0.54, -0.02, 0.26, "helm", "scale_light"),
    ((150.0, 70.0), (152.0, 20.0), 0.54, -0.02, 0.28, "helm", "scale"),
    ((0.0, 89.0), (0.0, 54.0), 0.52, 0.02, 0.18, "helm", "scale_light"),     # CROWN row (roots at the top; off_tip
                                                                         #   0.26/0.27 -> 0.18/0.19: a smoother top edge, no ruffle)
    ((120.0, 89.0), (120.0, 54.0), 0.52, 0.02, 0.19, "helm", "scale"),
    ((180.0, 36.0), (180.0, 86.0), 0.50, 0.22, 0.36, "crest", "scale_light")]  # the CREST leaf (on top, pointing up;
                                                                         #   off_tip 0.30 -> 0.36 + CREST_LEAF tip 1.35 -> 1.05: the sheet's sharp almond)
HELM_U = [0.0, 0.22, 0.45, 0.66, 0.84, 1.0]
HELM_TIP_CURL = 0.0                   # helmet tips lift only this much (no edge-on tips sticking up)
# ---- TAIL: one closed loft along a spine, its radius stepping at every band edge. SIDE view centre line: z 1.0 -> Y 0.78
#   (x 1011-1125), z 0.6 -> 1.05, z 0.3 -> 1.28, tip x 1185 (Y 1.89); BACK view: root width x 493-603 (+-0.55) @ y 632,
#   curving to the unit's RIGHT (image right): z 0.0 -> x 579-688 (X -0.31..-1.40)
TAIL_SPINE = [(0.0, 0.10, 1.85), (0.0, 0.50, 1.50), (-0.05, 0.80, 1.02), (-0.25, 1.08, 0.60), (-0.60, 1.38, 0.30),
              (-1.00, 1.64, 0.14), (-1.40, 1.88, 0.06)]
TAIL_R = [(0.0, 0.45), (0.15, 0.52), (0.35, 0.47), (0.55, 0.38), (0.75, 0.27), (0.90, 0.15), (1.0, 0.0)]  # "tail
                                      #   thickness": radius by arc fraction (BACK view widths)
TAIL_B0, TAIL_BANDS = 0.16, 6         # "tail segments": the bands start this far along (the root is under the scales)
TAIL_SWELL = 0.10                     # each band swells this much toward its back edge, then steps down to the next
TAIL_RING = 16
TAIL_UNDER = -0.45                    # the underside (sin of the ring angle below this) is the lighter belly tone
TAIL_CLEAR = 0.012                    # the tail's lowest point stays this far above the floor (walk floor guard)
# ---- eye (side view decal, cast along X). SIDE view (4x crop): eye x 812-830 @ y 356-394 (Y -1.78..-1.60,
#   z 4.34..3.96); glint x 815-820 @ y 362-372
EYE_C, EYE_AB = (-1.69, 4.15), (0.095, 0.19)   # "eye position" (Y, z) / "eye size" (half width, half height)
GLINT_C, GLINT_R = (-1.72, 4.25), 0.035
DECAL = {"eye": (0.012, 0.022), "glint": (0.036, 0.008)}   # (lift off the shell, dome height)
DECAL_DN = 0.03
# ---- cream field (face / chest / belly): the front of the torso by azimuth round the egg centre (deg), with a soft
#   scalloped fur edge; the head is cream below the helmet line
CREAM_ANG = 66.0                      # "cream width": cream within this azimuth of the front
CREAM_WAVE = (5.0, 0.22)              # "fur edge": scallop depth (deg) / spacing (z)
FUR_W = 0.10                          # the tan fur band just outside the cream (field units: 30 deg = 1)
CAP_LINE = (4.40, -0.85)              # "helmet line" on the head: cap above z = a + b (Y + 1.3) (front high, back low)
CAP_BLEND = (3.55, 3.85)              # torso azimuth rule -> head rule blend (z)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_FLOOR = {"scales": 0.50, "fur": 0.60, "hard": 0.65}   # "AO floor" per region class (house style: no black grime;
                                      #   the stacked scales would otherwise bake their gaps black)
AO_CLASS = {"scales": ["scale_light", "scale", "scale_dark", "scale_edge", "underskin", "tail_band", "tail_band_light",
                       "tail_under"],
            "fur": ["cream", "fur_tan", "nose", "foot", "foot_band"],
            "hard": ["gauntlet", "gauntlet_band", "claw", "eye", "glint"]}
AO_HIDDEN_DIST = 0.10                 # a face whose normal ray hits the mesh this close is hidden (not floored data)
# ---- rig (weight bands, z)
HIP_BAND = (1.45, 1.90)               # hips -> spine
SPINE_BAND = (2.55, 3.00)             # spine -> chest
HEAD_BAND = (3.70, 4.05)              # chest -> head
FOOT_BAND = (0.28, 0.40)              # foot -> shin
KNEE_BAND = (0.66, 0.82)              # shin -> thigh
LEG_TOP = (1.05, 1.25)                # thigh -> hips
SHOULDER_BLEND = 0.18                 # the first share of the upper arm blends into the chest
OWN_TAU = 0.05                        # soft part ownership for weights
TAIL_BONES = 4
# ---- clips (PLACEHOLDER: the artist has not given the movement intent; 24 fps; integer harmonics per loop)
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.014                        # "breathing": chest scale +- (2 breaths per loop)
HEAD_BOB = (2.2, 1.2)                 # "head bob": nod deg (2 cyc) / tilt deg (1 cyc)
TAIL_SWAY = (2.0, 6.0, 0.6)           # "tail sway": root deg, tip deg (1 cyc), lag per bone (rad)
ARM_IDLE = 1.5                        # arms rise with the breath (deg)
WALK_N = 48                           # "waddle loop": 2 s = two steps
HOP = 0.05                            # the hips lift this much over each step (never below rest)
WADDLE_ROLL = 5.0                     # "waddle": the body rolls over the stance foot (deg)
WADDLE_YAW = 3.0                      # hips twist toward the swing leg (deg)
STEP_SWING = 18.0                     # the swing thigh reaches forward (deg)
STEP_LIFT = 24.0                      # the swing shin folds back to lift the foot (deg)
STEP_BACK = 5.0                       # the stance thigh pushes back (deg)
ARM_SWAY = 4.0                        # arms sway opposite the roll (deg)
TAIL_WAG = (3.0, 8.0, 0.10)           # tail counter-wag root / tip deg + lag per bone (loop fraction)
TAIL_LIFT = 3.0                       # the tail root pitches up this much over each step (its tip clears the floor)
ROLL_PIVOT_PAD = 0.002                # extra hips lift over the analytic roll compensation (units at full step)
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
          "design/reference/pangolin_knight-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (back + side); floor z = 0 at the soles "
                   "(side y 790 / back y 782; the intake floor lines 818 / 811 are the drawn tail tip, 28 / 29 px lower)",
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
SN = (np.array(SNOUT[0], float), np.array(SNOUT[1], float), SNOUT[2], SNOUT[3])


def sd_head(P):
    return SD.sd_ellipsoid(P, HC, HR)


def sd_snout(P):
    return SD.sd_round_cone(P, *SN)


def sd_torso(P):
    return np.minimum(sd_egg(P), np.minimum(sd_head(P), sd_snout(P)))


def mir(p, sg):
    return np.array([sg * p[0], p[1], p[2]], float)


SIDES = (("L", 1.0), ("R", -1.0))
ARM = {s: {"upper": (mir(SHOULDER, sg), mir(ELBOW, sg), ARM_R[0], ARM_R[1]),
           "fore": (mir(ELBOW, sg), mir(WRIST, sg), ARM_R[1], ARM_R[2])} for s, sg in SIDES}
LEG = {s: {"thigh": (mir(HIP, sg), mir(KNEE, sg), LEG_R[0], LEG_R[1]),
           "shin": (mir(KNEE, sg), mir(ANKLE, sg), LEG_R[1], LEG_R[2]),
           "foot": (mir(FOOT[0], sg), mir(FOOT[1], sg), FOOT[2], FOOT[3])} for s, sg in SIDES}


def sd_arm(P, s, fore=True):
    d = SD.sd_round_cone(P, *ARM[s]["upper"])
    return np.minimum(d, SD.sd_round_cone(P, *ARM[s]["fore"])) if fore else d


def sd_leg(P, s):
    L_ = LEG[s]
    d = np.minimum(SD.sd_round_cone(P, *L_["thigh"]), SD.sd_round_cone(P, *L_["shin"]))
    return np.minimum(d, SD.sd_round_cone(P, *L_["foot"]))


GROUPS = ["torso", "arm.L", "arm.R", "leg.L", "leg.R"]
GROUP_REGION = {"torso": "cream", "arm.L": "fur_tan", "arm.R": "fur_tan", "leg.L": "fur_tan", "leg.R": "fur_tan"}


def group_sdf(P):
    return np.stack([sd_torso(P), sd_arm(P, "L"), sd_arm(P, "R"), sd_leg(P, "L"), sd_leg(P, "R")], 1)


def cone_box(a, b, r):
    return np.minimum(a, b) - r, np.maximum(a, b) + r


g = SD.Grid((-1.32, -2.50, -0.05), (1.32, 0.70, 5.32), SDF_STEP, SDF_BAND)
g.apply(sd_egg, (-0.85, -1.35, 0.90), (0.85, 0.50, 4.17), 0.0)
g.apply(sd_head, HC - HR, HC + HR, HEAD_K)
g.apply(sd_snout, *cone_box(SN[0], SN[1], SN[2]), SNOUT_K)
for s, _ in SIDES:
    for nm_, k_ in (("upper", ARM_K), ("fore", 0.08)):
        a, b, r1, r2 = ARM[s][nm_]
        g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), *cone_box(a, b, max(r1, r2)), k_)
    for nm_, k_ in (("thigh", LEG_K), ("shin", 0.10), ("foot", FOOT_K)):
        a, b, r1, r2 = LEG[s][nm_]
        g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), *cone_box(a, b, max(r1, r2)), k_)
g.floor(0.0)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "max_k": max(HEAD_K, ARM_K, LEG_K, SNOUT_K, FOOT_K), "grid": g.n.tolist(),
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


def yc_axis(z):
    """the scale carrier's centre line Y (the egg centre, held constant above / below the egg's useful range)"""
    return np.interp(np.clip(z, 1.05, 4.10), EZ, EYC)


def cream_field(P):
    """> 0 on the cream face / chest / belly. Torso: within CREAM_ANG of the front by azimuth in the egg's normalised
    section, the edge a soft scallop wave; head: below the helmet line (CAP_LINE); blended over CAP_BLEND."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    yc, ry, rx = egg_at(np.clip(z, EZ[0] + 0.02, EZ[-1] - 0.02))
    az = np.degrees(np.arctan2(np.abs(x) / np.maximum(rx, 0.05), -(y - yc) / np.maximum(ry, 0.05)))
    wave = CREAM_WAVE[0] * np.sin(2.0 * math.pi * z / CREAM_WAVE[1])
    f_body = (CREAM_ANG + wave - az) / 30.0
    f_head = (CAP_LINE[0] + CAP_LINE[1] * (y + 1.3) - z) / 0.3
    w = smoothstep(CAP_BLEND[0], CAP_BLEND[1], z)
    return (1.0 - w) * f_body + w * f_head


def nose_field(P):
    return NOSE_R - np.linalg.norm(P - np.array(NOSE_C), axis=1)


DGR = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["cream"] = cream_field(LV)
FIELDS["nose"] = nose_field(LV)
FIELDS["z"] = LV[:, 2].copy()
FIELDS["y"] = LV[:, 1].copy()
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


TOL = 0.01
in_torso = lambda a, b: a[LAY["f_torso"]] > -TOL and b[LAY["f_torso"]] > -TOL
in_legs = lambda a, b: (a[LAY["f_leg.L"]] > -TOL or a[LAY["f_leg.R"]] > -TOL) and (b[LAY["f_leg.L"]] > -TOL or b[LAY["f_leg.R"]] > -TOL)
in_feet = lambda a, b: in_legs(a, b) and a[LAY["z"]] < FOOT_TOP + 0.05 and b[LAY["z"]] < FOOT_TOP + 0.05
CUTS = [("f_" + gname, 0.0, None, None) for gname in GROUPS if gname != "torso"]
CUTS += [("cream", 0.0, in_torso, None), ("cream", -FUR_W, in_torso, None), ("nose", 0.0, in_torso, None),
         ("z", FOOT_TOP, in_legs, None), ("y", FOOT_BAND_Y[0], in_feet, None), ("y", FOOT_BAND_Y[1], in_feet, None)]
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
is_leg = (owner == GROUPS.index("leg.L")) | (owner == GROUPS.index("leg.R"))
shell_reg[is_torso & (FVAL["cream"] < 0)] = "fur_tan"
shell_reg[is_torso & (FVAL["cream"] < -FUR_W)] = "underskin"
shell_reg[is_torso & (FVAL["nose"] > 0)] = "nose"
shell_reg[is_leg & (FVAL["z"] > 1.0)] = "underskin"            # thighs under the scale skirt
shell_reg[is_leg & (FVAL["z"] < FOOT_TOP)] = "foot"
shell_reg[is_leg & (FVAL["z"] < FOOT_TOP) & (FVAL["y"] > FOOT_BAND_Y[0]) & (FVAL["y"] < FOOT_BAND_Y[1])] = "foot_band"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def torso_normal(P, h=0.004):
    P = np.atleast_2d(np.asarray(P, float))
    gr = np.stack([(sd_torso(P + e) - sd_torso(P - e)) / (2 * h) for e in np.eye(3) * h], 1)
    return gr / np.maximum(np.linalg.norm(gr, axis=1, keepdims=True), 1e-12)


PARTS = []
FACE = {}


def cast_side(sg):
    """side-view (Y, z) points -> shell hits along -sg X (from the unit's sg side) + smooth SDF normals"""
    def cast(pts2):
        H = []
        for y, z in np.asarray(pts2, float):
            loc, nrm, idx, dd = bvh_c.ray_cast(Vector((sg * 3.0, y, z)), Vector((-sg, 0.0, 0.0)), 6.0)
            if loc is None:
                loc = bvh_c.find_nearest(Vector((sg * 0.6, y, z)))[0]
            H.append(np.array(loc))
        H = np.array(H)
        return H, torso_normal(H)
    return cast


for s, sg in SIDES:
    V, F, R = FP.front_decal(EYE_C, EYE_AB[0], EYE_AB[1], 0.0, cast_side(sg), DECAL["eye"][0], DECAL_DN, DECAL["eye"][1],
                             3, 24, "eye")
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    V, F, R = FP.front_decal(GLINT_C, GLINT_R, GLINT_R, 0.0, cast_side(sg), DECAL["glint"][0], DECAL_DN,
                             DECAL["glint"][1], 1, 10, "glint")
    PARTS.append(FP.Part("glint." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"centre_side_yz": list(EYE_C), "half_axes": list(EYE_AB)}


# ---- scale carriers: the outermost body surface along a ray (last inside point of the carrier SDF)
TAIL_SP = SD.resample(SD.catmull(np.array(TAIL_SPINE, float), 8), 60)
_tal = SD.arclen(TAIL_SP); TAIL_SF = _tal / _tal[-1]
_tr = np.array(TAIL_R, float)


def tail_r(sf):
    return np.interp(sf, _tr[:, 0], _tr[:, 1])


def sd_tail_proxy(P):
    """round cones along the first 55 % of the tail (scale clearance only; never polygonised)"""
    d = np.full(len(P), 1e9)
    idx = np.nonzero(TAIL_SF <= 0.55)[0]
    for i in idx[::6]:
        j = min(i + 6, len(TAIL_SP) - 1)
        d = np.minimum(d, SD.sd_round_cone(P, TAIL_SP[i], TAIL_SP[j], tail_r(TAIL_SF[i]) * 1.1, tail_r(TAIL_SF[j]) * 1.1))
    return d


def sd_carrier(P):
    """what a body scale must clear: the torso + head, the UPPER arms (the shoulder scales ride over them), the
    thighs, the tail root"""
    d = np.minimum(sd_torso(P), np.minimum(sd_arm(P, "L", False), sd_arm(P, "R", False)))
    d = np.minimum(d, np.minimum(SD.sd_round_cone(P, *LEG["L"]["thigh"]), SD.sd_round_cone(P, *LEG["R"]["thigh"])))
    return np.minimum(d, sd_tail_proxy(P))


def ray_last_inside(O, D, sdf, rmax=2.4, step=0.02):
    """per ray (origins O (n,3), unit dirs D (n,3)): distance to the LAST point inside sdf within rmax (0 = none)"""
    O = np.atleast_2d(O); D = np.atleast_2d(D)
    ts = np.arange(0.0, rmax + 1e-9, step)
    P = O[:, None, :] + D[:, None, :] * ts[None, :, None]
    ins = (sdf(P.reshape(-1, 3)) < 0).reshape(len(O), len(ts))
    last = np.where(ins.any(1), len(ts) - 1 - np.argmax(ins[:, ::-1], 1), -1)
    lo = np.where(last >= 0, ts[np.maximum(last, 0)], 0.0)
    hi = lo + step
    for _ in range(6):
        mid = 0.5 * (lo + hi)
        inside = sdf(O + D * mid[:, None]) < 0
        lo = np.where(inside, mid, lo); hi = np.where(inside, hi, mid)
    return np.where(last >= 0, lo, 0.0)


def hdir(th_deg, sg):
    t_ = np.radians(th_deg)
    return np.stack([sg * np.sin(t_), -np.cos(t_), np.zeros_like(t_)], -1)


def latdir(th_deg, sg):
    t_ = np.radians(th_deg)
    return np.stack([sg * np.cos(t_), np.sin(t_), np.zeros_like(t_)], -1)


_eb = np.array(ENV_BACK_Y, float)[::-1]
_es = np.array(ENV_SIDE_X, float)[::-1]


def r_env(th_deg, z):
    """scale-tip envelope radius from the carrier axis: elliptical between the BACK-view half-width (theta 90) and the
    SIDE-view back (theta 180) / the body front + FRONT_FLARE (theta 0)"""
    r90 = np.interp(z, _es[:, 0], _es[:, 1])
    yc = yc_axis(z)
    r180 = np.interp(z, _eb[:, 0], _eb[:, 1]) - yc
    r0 = yc - np.interp(np.clip(z, EZ[0], EZ[-1]), EZ, EF) + FRONT_FLARE
    t_ = math.radians(th_deg)
    ry = r180 if th_deg >= 90.0 else r0
    return 1.0 / math.sqrt((math.sin(t_) / r90) ** 2 + (math.cos(t_) / max(ry, 1e-3)) ** 2)


def leaf_cells(nu, nv, tone, edge=True):
    regs = []
    for i in range(nu - 1):
        row = []
        for j in range(nv - 1):
            row.append("scale_edge" if edge and i >= 1 and (j == 0 or j == nv - 2) else tone)
        regs.append(row)
    return regs


TONES = ["scale_dark", "scale", "scale_light"]


def body_tone(th, seed):
    lev = 2 if th >= 160.0 else (1 if th >= 100.0 else 1)
    if FP.hash01(seed, 3.7) < TONE_DARK_SHARE:
        lev -= 1
    return TONES[max(lev, 0)]


SCALE_INFO = []
_su = np.array(SCALE_U); _sv = np.array(SCALE_V)
k_scale = 0
for ri, (z_r, z_t, cols, W) in enumerate(SCALE_ROWS):
    for ci, th in enumerate(cols):
        for s, sg in (SIDES if th < 179.0 else (("C", 1.0),)):
            zs = z_r + (z_t - z_r) * _su
            A = np.stack([np.zeros_like(zs), yc_axis(zs), zs], 1)
            h0 = hdir(np.array([th]), sg)[0]; e0 = latdir(np.array([th]), sg)[0]
            rb_r = ray_last_inside(A[:1], h0[None], sd_carrier)[0]
            rb_t = ray_last_inside(A[-1:], h0[None], sd_carrier)[0]
            lift = COL_LIFT * (ci % 2)
            R_root = rb_r - SCALE_BURY
            R_tip = max(r_env(th, z_t), rb_t + MIN_FLARE) + lift
            Rm = R_root + (R_tip - R_root) * (1.0 - (1.0 - _su) ** SCALE_EASE) + TIP_CURL * _su ** 3
            w = W * FP.leaf_hw(_su, *LEAF)
            thick = SCALE_THICK[0] + (SCALE_THICK[1] - SCALE_THICK[0]) * _su
            a_ = (_sv[None, :] * w[:, None]) / np.maximum(Rm[:, None], 0.2)
            D = h0[None, None, :] + a_[..., None] * e0[None, None, :]
            D /= np.linalg.norm(D, axis=-1, keepdims=True)
            Rg = Rm[:, None] + SCALE_CROWN * W * (1.0 - _sv[None, :] ** 2) * np.sin(math.pi * np.clip(_su, 0, 1) ** 0.7)[:, None]
            Ag = np.repeat(A[:, None, :], len(_sv), 1)
            rb = ray_last_inside(Ag.reshape(-1, 3), D.reshape(-1, 3), sd_carrier).reshape(Rg.shape)
            floor_r = rb + SCALE_CLEAR + thick[:, None]
            Rg = np.where(_su[:, None] >= 0.2, np.maximum(Rg, floor_r), Rg)
            O = Ag + D * Rg[..., None]
            tone = body_tone(th, k_scale)
            V, F, R = FP.slab(O, thick, D, leaf_cells(len(_su), len(_sv), tone))
            p = FP.Part("scale.%d.%d.%s" % (ri, ci, s), V, F, R)
            p.kind = "body_scale"
            PARTS.append(p)
            SCALE_INFO.append({"name": p.name, "theta": th, "z": [z_r, z_t], "R_root_tip": [round(float(R_root), 3),
                               round(float(R_tip), 3)], "tone": tone, "tris": p.tris()})
            k_scale += 1


# ---- HELMET scales (sphere carrier round HELM_C)
HELM = np.array(HELM_C, float)


def sph(az, el, sg):
    a, e = math.radians(az), math.radians(el)
    return np.array([sg * math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)])


def slerp(a, b, t):
    om = math.acos(float(np.clip(a @ b, -1.0, 1.0)))
    if om < 1e-6:
        return np.outer(1 - t, a) + np.outer(t, b)
    return (np.outer(np.sin((1 - t) * om), a) + np.outer(np.sin(t * om), b)) / math.sin(om)


_hu = np.array(HELM_U)
_ease = 1.0 - (1.0 - _hu) ** SCALE_EASE
for hi_, ((az_r, el_r), (az_t, el_t), W, off_r, off_t, leaf, tone) in enumerate(HELM_SCALES):
    mid = az_r in (0.0, 180.0) and az_t in (0.0, 180.0)
    for s, sg in ((("C", 1.0),) if mid else SIDES):
        d_r, d_t = sph(az_r, el_r, sg), sph(az_t, el_t, sg)
        nrm_pl = np.cross(d_r, d_t); nrm_pl = nrm_pl / np.linalg.norm(nrm_pl)
        Dm = slerp(d_r, d_t, _hu)
        rk_u = ray_last_inside(np.repeat(HELM[None], len(_hu), 0), Dm, sd_torso)     # the skull under the midline
        Rm = rk_u + off_r + (off_t - off_r) * _ease + HELM_TIP_CURL * _hu ** 3
        w = W * FP.leaf_hw(_hu, *(HELM_LEAF if leaf == "helm" else CREST_LEAF))
        thick = SCALE_THICK[0] + (SCALE_THICK[1] - SCALE_THICK[0]) * _hu
        a_ = (_sv[None, :] * w[:, None]) / np.maximum(Rm[:, None], 0.2)
        D = Dm[:, None, :] + a_[..., None] * nrm_pl[None, None, :]
        D /= np.linalg.norm(D, axis=-1, keepdims=True)
        rk = ray_last_inside(np.repeat(HELM[None], D.shape[0] * D.shape[1], 0), D.reshape(-1, 3), sd_torso).reshape(D.shape[:2])
        # skull-relative across the width too: each grid point keeps the midline's offset over ITS OWN skull point
        Rg = rk + (Rm - rk_u)[:, None] + SCALE_CROWN * W * (1.0 - _sv[None, :] ** 2) * np.sin(math.pi * _hu ** 0.7)[:, None]
        Rg = np.where(_hu[:, None] >= 0.2, np.maximum(Rg, rk + SCALE_CLEAR + thick[:, None]), Rg)
        O = HELM[None, None, :] + D * Rg[..., None]
        V, F, R = FP.slab(O, thick, D, leaf_cells(len(_hu), len(_sv), tone))
        p = FP.Part("helmet.%d.%s" % (hi_, s), V, F, R, bone="head")
        p.kind = "helmet"
        PARTS.append(p)
        SCALE_INFO.append({"name": p.name, "root": [az_r, el_r], "tip": [az_t, el_t], "offset_root_tip": [off_r, off_t],
                           "skull_r_root_tip": [round(float(rk_u[0]), 3), round(float(rk_u[-1]), 3)], "tone": tone,
                           "tris": p.tris()})
report["scales"] = {"count_body": sum(1 for p in PARTS if p.kind == "body_scale"),
                    "count_helmet": sum(1 for p in PARTS if p.kind == "helmet"), "rows": SCALE_INFO}

# ---- TAIL: one closed loft, the radius stepping at every band edge
_bl = (1.0 - TAIL_B0) / TAIL_BANDS
st = [0.0, 0.04, 0.09, TAIL_B0 - 0.02]
for kb in range(TAIL_BANDS):
    b0 = TAIL_B0 + kb * _bl
    st += [b0 + 0.004, b0 + 0.35 * _bl, b0 + 0.72 * _bl, b0 + 0.95 * _bl]
st = np.unique(np.round(np.clip(np.array(st + [1.0]), 0.0, 1.0), 6))
band_of = lambda sf: np.clip(np.floor((sf - TAIL_B0) / _bl), -1, TAIL_BANDS - 1)
loc_t = lambda sf: np.clip((sf - TAIL_B0) / _bl - np.floor((sf - TAIL_B0) / _bl), 0.0, 1.0)
TR_ = tail_r(st) * np.where(st >= TAIL_B0, 1.0 + TAIL_SWELL * loc_t(st) ** 1.5, 1.0)
TR_[0] = 0.0; TR_[-1] = 0.0
TSP = np.stack([np.interp(st * _tal[-1], _tal, TAIL_SP[:, k]) for k in range(3)], 1)
TSP[:, 2] = np.maximum(TSP[:, 2], TR_ + TAIL_CLEAR)
TSP[-1, 2] = max(TSP[-1, 2], TAIL_CLEAR + 0.01)


def tail_region(th, sm):
    if math.sin(th) < TAIL_UNDER:
        return "tail_under"
    b = int(band_of(sm))
    return "underskin" if b < 0 else ("tail_band_light" if b % 2 == 0 else "tail_band")


V, F, R, S_ = FP.loft(TSP, (0.0, 0.0, 1.0), TR_, TR_, TR_, TAIL_RING, regions=tail_region)
p = FP.Part("tail", V, F, R)
p.kind = "tail"
PARTS.append(p)
TAIL_BONE_SP = TSP.copy()
report["tail"] = {"stations": len(st), "tris": p.tris(), "length": round(float(SD.arclen(TSP)[-1]), 3),
                  "bands": TAIL_BANDS, "tip": TSP[-1].round(3).tolist(), "min_z": round(float(V[:, 2].min()), 4)}

# ---- GAUNTLETS + hand claws
GAUNT = {}
for s, sg in SIDES:
    g0, g1 = mir(GAUNT_AXIS[0], sg), mir(GAUNT_AXIS[1], sg)
    ax = unit(g1 - g0)
    V, F, R = FP.lathe(GAUNT_PROF, GAUNT_REG, GAUNT_SEG, g0, ax, up_hint=(0, 0, 1), su=GAUNT_OVAL[0], sv=GAUNT_OVAL[1])
    PARTS.append(FP.Part("gauntlet." + s, V, F, R, bone="arm.%s.1" % s))
    lat = unit(np.cross(ax, (0.0, 0.0, 1.0))) * (-sg)       # toward the unit's outside... (sign only orders the claws)
    up = unit(np.cross(lat, ax)) if np.cross(lat, ax)[2] > 0 else -unit(np.cross(lat, ax))
    tips = []
    for k, (dl, du) in enumerate(HCLAW_SPREAD):
        r0 = g0 + ax * HCLAW_ROOT_H + lat * dl + up * du
        sp = np.array([r0 - ax * 0.06, r0 + ax * 0.12 * HCLAW_LEN / 0.5, r0 + ax * 0.26 * HCLAW_LEN / 0.5 - up * 0.03,
                       r0 + ax * 0.38 * HCLAW_LEN / 0.5 - up * HCLAW_DROP * 0.45, r0 + ax * 0.46 * HCLAW_LEN / 0.5 - up * HCLAW_DROP * 0.8,
                       r0 + ax * HCLAW_LEN - up * HCLAW_DROP])
        cw = np.array([0.0, HCLAW_W, HCLAW_W * 0.9, HCLAW_W * 0.68, HCLAW_W * 0.4, 0.0])
        ch = np.array([0.0, HCLAW_H, HCLAW_H * 0.85, HCLAW_H * 0.6, HCLAW_H * 0.35, 0.0])
        V, F, R, _ = FP.loft(sp, up, cw, cw, ch, 8, regions=lambda th, s_: "claw")
        PARTS.append(FP.Part("hclaw.%s.%d" % (s, k), V, F, R, bone="arm.%s.2" % s))
        tips.append(sp[-1])
    GAUNT[s] = {"g0": g0, "g1": g1, "front": g0 + ax * GAUNT_PROF[-1][1], "tips": np.array(tips)}

# ---- foot claws: three big hoof-claws per foot, tips on the floor
FCLAW_TIPS = {}
for s, sg in SIDES:
    fb = mir(FOOT[1], sg)
    tips = []
    for k, yaw in enumerate(FCLAW_YAW):
        a = math.radians(FOOT_YAW + yaw)
        d = np.array([sg * math.sin(a), -math.cos(a), 0.0])
        lat = np.array([-d[1], d[0], 0.0])
        r0 = fb + d * 0.02 + lat * (0.0 if k == 1 else 0.0) + np.array([0.0, 0.0, 0.02])
        L_ = FCLAW_LEN * (1.0 if k == 1 else 0.88)
        prof = [(-0.08, 0.0, 0.0, 0.0), (0.04, 0.03, 0.9, 0.9), (0.20, 0.06, 1.0, 1.0), (0.45, 0.02, 0.85, 0.75), (0.70, -0.06, 0.62, 0.5),
                (0.88, -0.12, 0.35, 0.28), (1.0, -0.16, 0.0, 0.0)]          # (s, dz, width share, height share)
        sp, cw, ch = [], [], []
        for s_, dz, wf, hf in prof:
            q = r0 + d * (s_ * L_) + np.array([0.0, 0.0, dz])
            cw.append(FCLAW_W * wf); ch.append(FCLAW_H * hf)
            q[2] = max(q[2], FCLAW_H * hf + 0.006)                       # never below the floor
            sp.append(q)
        V, F, R, _ = FP.loft(np.array(sp), (0.0, 0.0, 1.0), np.array(cw), np.array(cw), np.array(ch), 8,
                             regions=lambda th, s__: "claw")
        PARTS.append(FP.Part("fclaw.%s.%d" % (s, k), V, F, R, bone="leg.%s.2" % s))
        tips.append(sp[-1])
    FCLAW_TIPS[s] = np.array(tips)

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
REG = ["cream", "fur_tan", "underskin", "nose", "foot", "foot_band", "scale_light", "scale", "scale_dark", "scale_edge",
       "tail_band", "tail_band_light", "tail_under", "gauntlet", "gauntlet_band", "claw", "eye", "glint"]
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

# facing landmark: the head centre -> the nose tip (midline)
anchor = HC - SHIFT
landmark = np.array(NOSE_C) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C, midline) -> nose tip (NOSE_C) on the midline",
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
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The scales are where the budget goes: %d closed "
                            "plates (6 x 7 grid each) carry the layered-scale identity; the shell carries the body, head, "
                            "snout, arms and legs; the bake carries the SDF high's detail into the normal map."
                            % (TRI_BUDGET[0], TRI_BUDGET[1], report["scales"]["count_body"] + report["scales"]["count_helmet"]))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, scales, helmet, tail, gauntlets, claws, eye decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet": {"side_height_from_soles_u": 5.62, "back_height_from_soles_u": 5.64,
                               "intake_heights_u": {"back": 5.94, "side": 5.91}, "side_width_u": 4.47, "back_width_u": 3.07},
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
low["conquest_source"] = "from scratch: design/reference/pangolin_knight-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_tail_pts = VALL[RANGE["tail"][0]:RANGE["tail"][1]]
_gl = VALL[RANGE["gauntlet.L"][0]:RANGE["gauntlet.L"][1]]
_hc = np.vstack([VALL[RANGE[p.name][0]:RANGE[p.name][1]] for p in PARTS if p.kind == "helmet"])
low["conquest_focus"] = json.dumps({
    "face": [(np.array([0.0, -2.6, 3.5]) - SHIFT).tolist(), (np.array([1.0, -0.8, 5.0]) - SHIFT).tolist()],
    "head_crest": [_hc.min(0).tolist(), _hc.max(0).tolist()],
    "forelimb": [(_gl.min(0) - np.array([0.1, 0.5, 0.25])).tolist(), (_gl.max(0) + np.array([0.1, 0.1, 0.1])).tolist()],
    "tail": [_tail_pts.min(0).tolist(), _tail_pts.max(0).tolist()],
    "views": {"face": [62.0, 4.0], "head_crest": [140.0, 28.0], "forelimb": [72.0, 6.0], "tail": [130.0, 12.0]},
    "axis_xy": [float(-0.06 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "zero_area_faces", "tail")},
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
TFN = texel_faces(RN)
alltex_n = TFN >= 0
TFA = texel_faces(RA)
alltex_a = TFA >= 0
# ---- AO floors per region class (house style: "AO floors per region class so nothing bakes to black grime"). Visible
#      vs hidden faces: a ray from the face centre along its normal that hits the mesh within AO_HIDDEN_DIST marks the
#      face hidden (buried scale roots, inner skins against the body, decal undersides).
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


_yc = lambda z: float(yc_axis(np.array([z]))[0])
TB = SD.resample(TAIL_BONE_SP, TAIL_BONES + 1)
BONES = [("hips", P_((0, _yc(1.0), 1.0)), P_((0, _yc(1.85), 1.85)), "root"),
         ("spine", P_((0, _yc(1.85), 1.85)), P_((0, _yc(2.75), 2.75)), "hips"),
         ("chest", P_((0, _yc(2.75), 2.75)), P_((0, _yc(3.65), 3.65)), "spine"),
         ("head", P_((0, _yc(3.65), 3.65)), P_((0, HC[1], HC[2] + HR[2])), "chest")]
for s, sg in SIDES:
    G_ = GAUNT[s]
    BONES += [("arm.%s.0" % s, P_(mir(SHOULDER, sg)), P_(mir(ELBOW, sg)), "chest"),
              ("arm.%s.1" % s, P_(mir(ELBOW, sg)), P_(G_["front"]), "arm.%s.0" % s),
              ("arm.%s.2" % s, P_(G_["front"]), P_(G_["tips"].mean(0)), "arm.%s.1" % s)]
    toe = mir(FOOT[1], sg) + np.array([0.0, -0.2, 0.0])
    BONES += [("leg.%s.0" % s, P_(mir(HIP, sg)), P_(mir(KNEE, sg)), "hips"),
              ("leg.%s.1" % s, P_(mir(KNEE, sg)), P_(mir(ANKLE, sg)), "leg.%s.0" % s),
              ("leg.%s.2" % s, P_(mir(ANKLE, sg)), P_(toe), "leg.%s.1" % s)]
for k in range(TAIL_BONES):
    BONES.append(("tail.%d" % k, P_(TB[k]), P_(TB[k + 1]), "hips" if k == 0 else "tail.%d" % (k - 1)))
CONNECT = {"spine", "chest", "head", "arm.L.1", "arm.L.2", "arm.R.1", "arm.R.2", "leg.L.1", "leg.L.2", "leg.R.1", "leg.R.2"} | \
          {"tail.%d" % k for k in range(1, TAIL_BONES)}
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


def torso_bands(zz):
    """(n, 4) weights hips / spine / chest / head by height (the shell torso + every body scale)"""
    wh = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
    wc = smoothstep(SPINE_BAND[0], SPINE_BAND[1], zz)
    ws = smoothstep(HIP_BAND[0], HIP_BAND[1], zz)
    return np.stack([1 - ws, ws * (1 - wc), wc * (1 - wh), wh], 1)


# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
GD = group_sdf(Pw)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
zz = Pw[:, 2]
Ws = np.zeros((b_ - a_, len(DEFORM)))
TBW = torso_bands(zz)
gi = GROUPS.index("torso")
for c_, bn in enumerate(("hips", "spine", "chest", "head")):
    Ws[:, J[bn]] += Wg[:, gi] * TBW[:, c_]
for s, sg in SIDES:
    gi = GROUPS.index("arm." + s)
    _, sa, _ = FP.spine_param(Pw, np.array([mir(SHOULDER, sg), mir(ELBOW, sg), mir(WRIST, sg)]))
    Lu = float(np.linalg.norm(mir(ELBOW, sg) - mir(SHOULDER, sg)))
    w_sh = smoothstep(0.0, SHOULDER_BLEND * Lu, sa)
    w_el = smoothstep(Lu - 0.06, Lu + 0.06, sa)
    Ws[:, J["chest"]] += Wg[:, gi] * (1 - w_sh)
    Ws[:, J["arm.%s.0" % s]] += Wg[:, gi] * w_sh * (1 - w_el)
    Ws[:, J["arm.%s.1" % s]] += Wg[:, gi] * w_sh * w_el
    gi = GROUPS.index("leg." + s)
    wf = smoothstep(FOOT_BAND[1], FOOT_BAND[0], zz)
    wk = smoothstep(KNEE_BAND[0], KNEE_BAND[1], zz)
    wt = smoothstep(LEG_TOP[0], LEG_TOP[1], zz)
    Ws[:, J["leg.%s.2" % s]] += Wg[:, gi] * wf
    Ws[:, J["leg.%s.1" % s]] += Wg[:, gi] * (1 - wf) * (1 - wk)
    Ws[:, J["leg.%s.0" % s]] += Wg[:, gi] * (1 - wf) * wk * (1 - wt)
    Ws[:, J["hips"]] += Wg[:, gi] * (1 - wf) * wk * wt
Wt[a_:b_] = Ws
TAIL_L = float(SD.arclen(TAIL_BONE_SP)[-1])
for p in PARTS:
    a_, b_ = RANGE[p.name]
    Pv = VALL[a_:b_] + SHIFT
    if p.kind == "body_scale":
        TBW = torso_bands(Pv[:, 2])
        for c_, bn in enumerate(("hips", "spine", "chest", "head")):
            Wt[a_:b_, J[bn]] += TBW[:, c_]
    elif p.kind == "tail":
        _, s_arc, _ = FP.spine_param(Pv, TAIL_BONE_SP)
        Wv = K.vine_weights(np.clip(s_arc, 0, TAIL_L), TAIL_L, TAIL_BONES, parent_s0=0.15 * TAIL_L)
        Wt[a_:b_, J["hips"]] += Wv[:, 0]
        for k in range(TAIL_BONES):
            Wt[a_:b_, J["tail.%d" % k]] += Wv[:, k + 1]
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
                  "rule": "shell: soft SDF ownership (tau %.2f) between torso / arms / legs; the torso runs hips -> spine "
                          "-> chest -> head by height (HIP / SPINE / HEAD_BAND smoothsteps); arms: chest -> arm.0 (first "
                          "%.2f of the upper arm) -> arm.1 past the elbow; legs: foot below FOOT_BAND, shin, thigh above "
                          "KNEE_BAND, the top blending into hips; body scales: the torso height bands per vertex (they "
                          "ride the shell under them); helmet, eyes rigid on head; gauntlets rigid on arm.1, hand claws on "
                          "arm.2, foot claws on leg.2; tail: rigkit.vine_weights along its spine from hips"
                          % (OWN_TAU, SHOULDER_BLEND)}
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
    """PLACEHOLDER idle (4 s): breathing (the chest swells twice), a head bob (nod 2 cyc + a slow tilt), the tail swaying
    side to side as a wave down the chain (horizontal: the tail stays off the floor), the arms rising a little with each
    breath; feet planted (hips and legs still)."""
    P = {}
    br = math.sin(TAU * 2 * u)
    P["chest"] = (None, [], (1 + BREATH * br, 1 + 0.6 * BREATH * br, 1 + BREATH * br))
    P["head"] = (None, [("X", HEAD_BOB[0] * math.sin(TAU * 2 * u - 0.7)), ("Y", HEAD_BOB[1] * math.sin(TAU * u + 0.4))], None)
    for k in range(TAIL_BONES):
        P["tail.%d" % k] = (None, [("Z", K.vine_wave(u, k, TAIL_BONES, TAIL_SWAY[0], TAIL_SWAY[1], TAIL_SWAY[2], 1, 0.3))], None)
    for s, sg in SIDES:
        P["arm.%s.0" % s] = (None, [("X", ARM_IDLE * math.sin(TAU * 2 * u - 0.5))], None)
    return P, {"lift": 0.0}


def walk_pose(u):
    """PLACEHOLDER walk (2 s): an in-place WADDLE -- two steps per loop; each step the hips lift a little (never below
    rest), roll over the stance foot and twist toward the swing leg; the swing thigh reaches forward with the shin
    folded to lift the foot, the stance thigh pushes back; spine + head counter-roll (gaze level); the arms sway; the
    tail counter-wags (and undoes the roll so its tip stays off the floor)."""
    k, s, sg = step_of(u)
    h = hang(s)
    a = math.radians(WADDLE_ROLL * h)
    comp = SOLE_XMAX * math.sin(a) - HIPS_Z * (1.0 - math.cos(a)) + ROLL_PIVOT_PAD * h   # the roll pivots on the stance
    P = {"hips": ((0.0, 0.0, HOP * h + comp), [("Y", sg * WADDLE_ROLL * h), ("Z", -sg * WADDLE_YAW * h)], None)}
    P["spine"] = (None, [("Y", -sg * 0.45 * WADDLE_ROLL * h)], None)
    P["chest"] = (None, [("Y", -sg * 0.2 * WADDLE_ROLL * h), ("Z", sg * 0.5 * WADDLE_YAW * h)], None)
    P["head"] = (None, [("Y", -sg * 0.3 * WADDLE_ROLL * h), ("X", 1.5 * math.sin(TAU * s))], None)
    swing = "R" if k == 0 else "L"
    for s_, sg_ in SIDES:
        if s_ == swing:
            P["leg.%s.0" % s_] = (None, [("X", -STEP_SWING * h)], None)
            P["leg.%s.1" % s_] = (None, [("X", STEP_LIFT * h)], None)
            P["leg.%s.2" % s_] = (None, [("X", -0.6 * STEP_LIFT * h)], None)
        else:
            P["leg.%s.0" % s_] = (None, [("X", STEP_BACK * h)], None)
            P["leg.%s.1" % s_] = (None, [], None)
            P["leg.%s.2" % s_] = (None, [("X", -STEP_BACK * h)], None)
        P["arm.%s.0" % s_] = (None, [("X", sg * sg_ * ARM_SWAY * h)], None)
    for kb in range(TAIL_BONES):
        lagh = hang(s - TAIL_WAG[2] * (kb + 1))
        amp = TAIL_WAG[0] + (TAIL_WAG[1] - TAIL_WAG[0]) * kb / max(TAIL_BONES - 1, 1)
        rots = [("Z", sg * amp * lagh)]
        if kb == 0:
            rots = [("Y", -sg * WADDLE_ROLL * h), ("X", TAIL_LIFT * h)] + rots
        P["tail.%d" % kb] = (None, rots, None)
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


# the stance sole sits SOLE_XMAX outboard of the hips head: a roll a drops it by SOLE_XMAX sin a - HIPS_Z (1 - cos a);
# the walk lifts the hips by exactly that (+ a pad) so the floor guard never has to
_sole_pts = VALL[(VALL[:, 2] < 0.01) & np.isin(np.argmax(Wt, 1), [J["leg.L.2"], J["leg.R.2"]])]
HIPS_Z = float(REST_H["hips"][2])
SOLE_XMAX = float(np.abs(_sole_pts[:, 0] - REST_H["hips"][0]).max())
rep["walk_roll_pivot"] = {"sole_xmax": round(SOLE_XMAX, 4), "hips_z": round(HIPS_Z, 4), "sole_verts": int(len(_sole_pts))}
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
                     "tail_tip": list(pbs["tail.%d" % (TAIL_BONES - 1)].tail),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    hz = g_("hips_z") - REST_H["hips"][2]
    tt_ = np.array([r["tail_tip"] for r in R_]); ht_ = np.array([r["head_tail"] for r in R_])
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
rig["conquest_rig"] = ("pangolin_knight v1: root > hips > spine > chest > head (helmet scales, eyes rigid); chest > "
                       "arm.{L,R}.{0,1,2} (1 = forearm + gauntlet, 2 = claws); hips > leg.{L,R}.{0,1,2} (2 = the foot); "
                       "hips > tail.0..3 (vine chain)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER: the artist has not given the movement intent. idle = breathing + head bob + "
                               "tail sway (4 s: chest swells twice, nod + slow tilt, a horizontal wave down the tail, arms "
                               "rise with the breath, feet planted); walk = an in-place waddle-walk (2 s: two steps, hips "
                               "lift + roll over the stance foot, swing leg reach + foot lift, counter-rolled spine / head, "
                               "arm sway, tail counter-wag; feet never below z 0, root still)")
low["conquest_locomotion"] = "walker: waddle in place (the game glides the unit)"
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
              "structure": "armature identity + skinned 'pangolin_knight' (one opaque body material); natural scale; "
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
