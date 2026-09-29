"""Wren -- the Oakvale farm-boy HERO build (second humanoid Conquest unit, the vampwarrior lessons applied from the start).

    blender --background --factory-startup --python improve/wren_build.py -- \
        [--preview <out.blend>]          (body + outfit + hair + pitchfork + regions + palette only: no bake, no rig)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-09-28 "NEW UNIT: Wren" + design/reference/wren-character-sheet.webp (three views + head /
necklace / bracer / cloak / cloth-patch / staff detail panels + palette chips). Age 16, village farm boy, HERO role.
v2 (review-log 2026-09-28 "Wren v2 face feedback"): the FACE round -- bigger eyes, bigger brows, the lips re-paired (the
v1 mouth-compression expression rolled the upper lip in behind a protruding lower lip = the mismatch + the pinch), face
smoothing (a face-zone normal bake referenced to the flat facets at a raised face texel density: the face shades smooth
while every face stays flat-shaded per the contract). Everything below the neck is v1.

LESSONS APPLIED FROM THE START (vampwarrior v1 -> v4.2, review-log 2026-09-26 entries):
  - MPFB2 base tuned STYLISED immediately (age macro at 16 years, anime face dials: larger eyes, soft jaw, small nose),
    warm skin SAMPLED from the sheet (never Blender-default grey);
  - hair = shaped pointed masses on a feathered scalp cap, own UV strip (flat normal / white AO: no bake blotches), low
    crown, a fringe with pointed tips (no hood read);
  - NO cel bands, NO outline shells: Col x baked AO + baked normal map, roughness 0.62, per-region AO floors;
  - painted tight garments with iso-cut edges (shirt, vest, trousers, sock wraps + lacing), solid closed overlays for loose
    pieces (boots, trouser blouse, tunic tails, rope sash, pouch, sleeve rolls, bracer, necklace, cloak + hood + cowl);
  - authored shadow shapes (jaw / neck cast shadow, the fringe's zigzag shadow on the forehead), drawn brows + liner;
  - clips with damped-spring follow-through (periodic steady state), the swing-foot lift proof (zero toe dips), exact loops;
  - the pitchfork on its own bone (child of hand_r), the grip transform recorded, the roll auto-picked (least wrist bend);
  - bake + twin digest tolerance gate.

The build is split into section files executed in ONE namespace (wren_s1_body.py ... wren_s8_clips.py); every tunable
constant lives HERE (artist-facing names in the comments). UNITS: metres, floor z = 0 at the boot soles, front -Y, left +X.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile, addon_utils
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K             # noqa: E402  (read-only use)
import palettes as PAL         # noqa: E402  (read-only use)
import wren_parts as VP        # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
UNIT = "wren"
CHAR_ID = "wren"                      # the sheet names him
TRI_BUDGET = [30000, 50000]           # declared tier: HERO (quality-tier law: hero = 30-50k)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- body (MPFB2). MakeHuman age macro: 0.0 = 1 yr, 0.1875 = 11 yr, 0.5 = 25 yr -> 16 yr = 0.1875 + 5/14 x 0.3125
MACRO = {"gender": 1.0, "age": 0.30, "muscle": 0.52, "weight": 0.40, "proportions": 0.85, "height": 0.5,
         "cupsize": 0.5, "firmness": 0.5}
RACE = {"caucasian": 0.72, "asian": 0.23, "african": 0.05}
TARGETS = {                           # "face dials" (anime read: larger eyes, soft jaw, small nose, bold level brows)
    "eyes/l-eye-scale-incr": 1.00, "eyes/r-eye-scale-incr": 1.00,          # "eye size" (v1 0.40; v2 bigger eyes: the dial's max)
    "eyes/l-eye-height2-incr": 0.35, "eyes/r-eye-height2-incr": 0.35,      # "eye opening": a more open lid (v1 0.25)
    "eyes/l-eye-bag-decr": 0.60, "eyes/r-eye-bag-decr": 0.60,              # v2: no under-eye bags (anime-smooth lower lid)
    "head/head-oval": 0.45,                                                # smooth oval head
    "chin/chin-bones-decr": 0.35,                                          # soft jaw
    "chin/chin-width-decr": 0.25, "chin/chin-triangle": 0.30,              # narrow, gently pointed chin (anime V)
    "cheek/l-cheek-volume-decr": 0.30, "cheek/r-cheek-volume-decr": 0.30,  # lean cheeks (not the child's round face)
    "nose/nose-scale-horiz-decr": 0.40, "nose/nose-volume-decr": 0.45, "nose/nose-point-width-decr": 0.60,  # small nose
    "nose/nose-flaring-decr": 0.50, "nose/nose-nostrils-width-decr": 0.50, "nose/nose-scale-vert-decr": 0.20,  # (anime: no nostril read)
    "head/head-scale-vert-incr": 0.18, "cheek/l-cheek-bones-incr": 0.20, "cheek/r-cheek-bones-incr": 0.20,  # longer teen face
    "mouth/mouth-scale-horiz-decr": 0.20,                                  # small mouth
    "eyebrows/eyebrows-angle-down": 0.25,                                  # determined brow set
    "neck/neck-scale-horiz-decr": 0.20,                                    # slender teen neck
    "mouth/mouth-lowerlip-volume-incr": 0.50, "mouth/mouth-upperlip-volume-decr": 0.40,   # v2 "lip pairing": matched upper / lower lip
    "mouth/mouth-angles-down": 0.15,                                       # "mouth corners": a relaxed neutral set (v1 0.45 + the compression = the pinch)
    "expression/units/caucasian/eye-left-slit": 0.20, "expression/units/caucasian/eye-right-slit": 0.20,   # the sheet's determined lids (v1 0.30)
    "legs/upperlegs-height-incr": 0.20, "legs/lowerlegs-height-incr": 0.15,  # leggy stylised proportions
}                                     # (v1's "expression/units/caucasian/mouth-compression": 1.0 is GONE: it rolled the upper lip in)
LIP_SEAL = (0.009, 0.0003, 0.0025)    # "lip seal" (v2): the relaxed lips closed geometrically: falloff over the lip height (m),
                                      #   overlap past the seam (m), gap = front rays this far behind the lip front (m); None = off
BODY_H = 1.68                         # "height" barefoot (m); the boot soles add SOLE_T
REST_ARM_DOWN = 22.0                  # "rest arm drop": the MPFB A-pose arms lowered this much (deg) in the bind pose, so
                                      #   the cloak drapes over arms that already hang near the clip poses
REST_ELBOW_OPEN = 24.0                # the MPFB rest elbow (bent ~39 deg forward) opened this much in the bind pose
SOLE_T = 0.022                        # "boot sole": the foot sits this far above the floor inside the boot
# ---- painted garment lines
SLEEVE_T = 0.10                       # "rolled sleeve": the sleeve ends this far down the forearm (elbow 0 .. wrist 1)
VNECK = (0.082, 0.85)                 # "shirt V-neck": depth below the neck base at the midline (m), rise per m of |x|
NECK_DZ = 0.012                       # round neckline elsewhere: this far below the neck base (rising 0.3 x toward the back)
VEST_X = (0.056, 0.074)               # "vest opening": |x| of the vest's front edges at the neck base / at the waist
ARMHOLE_IN, ARMHOLE_TILT = 0.030, 0.25   # vest armhole plane: inside the shoulder joint, tilt
WRAP_T = (0.20, 0.64)                 # "sock wraps": along the shin (knee 0 .. ankle 1): wrap top (under the trouser
                                      #   blouse) / boot top
LACE = (0.072, 0.0022, 36.0)          # "cross lacing": an X every this many m up the shin front, line half width (m), line
                                      #   angle from horizontal (deg)
LACE_SPAN = 95.0                      # the lacing covers the shin within this angle either side of the front centre
WRAP_BANDS = 0.024                    # cloth band spacing of the wraps (a faint tone band every this many m)
# ---- face
EYE_IRIS_DEG, EYE_PUPIL_DEG = 40.0, 15.0  # iris / pupil cone half-angles on the eyeball (larger anime iris)
EYE_SEG = 16
LINER_W = (0.0013, 0.0003)            # "eye liner": upper / lower lid line width (m) -- a bold anime upper lid
LINER_WING = (0.0007, 20.0, 10.0)     # outer-corner flick: extra width (m), angular half-width, centre angle (deg)
BROW_PTS = ((-1.15, 0.0058), (-0.40, 0.0074), (0.50, 0.0090), (1.28, 0.0080))   # "brows": (x in eye half-widths from the
                                      #   eye centre, height above the upper lid m) -- inner end low: the earnest set
BROW_W = (0.0086, 0.0040)             # "brow thickness": inner end / tail (m) -- v2 bigger (v1 6.4 / 2.8 mm)
BROW_TAPER = 1.2
LIP = (0.017, 0.0026, 0.0026)         # subtle lip tint: half width / upper height / lower depth (m) around the seam (v2: matched; v1 2.4 / 3.2 mm)
LIP_DZ = 0.0
MOUTH_LINE = (0.0011, 0.30)           # v2 "mouth line": painted width on the sealed seam (m) at the centre, x this at the corners
FACE_UV_SCALE = 4.5                   # v2 "face smoothing": the face zone's texel density x this (linear) vs the rest of the
                                      #   UV layout, so its normal bake resolves the smooth high per facet
FACE_NORMAL_REF = "flat"              # v2 "face smoothing": the face zone's normal map is baked against its FLAT facets (it
                                      #   then shades smooth although every face stays flat-shaded, the contract); "smooth" = v1
MOUTH_IN_D = 0.0032                   # (v1 rule, LIP_SEAL = None only) lip-zone skin this far behind the front surface = the mouth
JAW_LIGHT_DEG = 42.0                  # "neck shadow length" (the chin's cast shadow under a stylised key light)
JAW_SMOOTH = 3
JAW_GATE = (0.008, 0.6, 0.012)
# fringe: the pointed tips of the front locks, front projection (x mm from the midline, +x = his left; z mm above the eye
# centres). The fringe clumps are BUILT to these tips and the forehead shadow band follows the same zigzag.
FRINGE_TIPS = ((-60.0, 20.0), (-40.0, 27.0), (-20.0, 19.0), (4.0, -6.0), (22.0, 25.0), (41.0, 18.0), (60.0, 22.0))
                                      # (tips at eye + 18..27 mm leave the bold brows showing; one narrow lock falls between
                                      #   the eyes, as on the sheet's head panel)
FRINGE_NOTCH = 17.0                   # the edge between two tips rises this far (mm) above the higher tip
FRINGE_D = (0.0045, 0.0065)           # "fringe shadow depth": band under the fringe edge at the tips / at the notches (m)
FRINGE_UNDER = 0.006                  # the band also runs this far ABOVE the edge (skin glimpsed between the locks)
HAIRLINE = (0.060, -0.050)            # scalp hairline above the eye centres (front) / at the nape relative to the head joint
HAIR_CAP_T = 0.0055                   # "hair volume" on the scalp
HAIR_ROOT_K = 0.30                    # every lock's thickness at its root (x its T): roots stacked on the crown made it tall (vampwarrior v4.1 lesson)
HAIRLINE_FEATHER = (0.022, 0.0022)    # the cap thins to the 2nd value over the first above the hairline (no hood rim)
HAIR_UV_STRIP = 0.14                  # hair faces packed into the right 14 % of the UV square (flat normal / white AO)
LOCK_RINGS = 12                       # "hair lock detail": cross-sections per lock
LOCK_OFF = 0.0020                     # locks ride this far off the cap (their control points; the path keeps 1.2 mm + half the lock thickness)
# hair locks: (name, chain, width m, thickness m, control points) -- see wren_s5_hair.py for the paths
HAIR_W = {"fringe": 0.042, "side": 0.036, "back": 0.042, "crown": 0.040, "ahoge": 0.009, "tail": 0.020}
HAIR_T = {"fringe": 0.0075, "side": 0.009, "back": 0.010, "crown": 0.0065, "ahoge": 0.004, "tail": 0.010}
HAIR_VOL = {"side": 0.009, "back": 0.010, "back_flick": 0.030}
OUTER_LOCKS = ((66.0, 8.0, 0.040), (92.0, -6.0, 0.046), (118.0, 10.0, 0.044), (146.0, 22.0, 0.040), (170.0, 16.0, 0.036))
                                      # "messy outline": big outer locks per side (psi deg from the front, tip elevation deg
                                      #   about the head centre, tip flick off the cap m) -- the spiky silhouette over the
                                      #   ears and round the back (the sheet), rooted on the crown so the top stays low
OUTER_W, OUTER_T, OUTER_MID = 0.040, 0.010, 0.014   # their width, thickness, mid-way lift off the cap (m)   # "hair volume": side / back locks ride this much further off the cap mid-way
SIDE_TIPS = ((86.0, -12.0, 0.022), (98.0, -36.0, 0.020), (112.0, -16.0, 0.036), (126.0, 4.0, 0.040))   # per side: tip (psi deg, z mm above
                                      #   the eye centres, outward flick m) -- over the ears to the cheekbone / jaw
BACK_PSI = (140.0, 157.0, 173.0, 189.0, 205.0, 221.0)   # back locks round the head (deg from the front)
BACK_TIP_EL = (-42.0, -52.0, -58.0, -57.0, -51.0, -41.0)   # their tips' elevation about the head centre (deg)
CROWN_PSI = (150.0, 210.0, 75.0, -75.0)   # crown flicks (the messy top)
TAIL = (0.060, 0.010)                 # "nape tail": length (m), tie radius (m)
AHOGE_H = 0.045                       # "cowlick" height above the crown
CROWN_LIFT = 0.008                    # "crown flick lift": the messy top flicks stand this far off the cap (low crown lesson)
# ---- boots (solid)
BOOT_MARGIN = (0.010, 0.012)          # foot shell clearance: sides / top
TOE_EXT = 0.022                       # the rounded toe reaches this far past the toes
BOOT_CLEAR = (0.011, 0.016)           # shaft clearance off the calf: at the ankle / at the top
CUFF = (0.058, 0.014, 0.020, 0.0045)  # "boot cuff": fold height, clearance over the shaft at the top / flare at its lower
                                      #   edge, thickness
STRAPS = ((-0.035, 10.0), (0.060, -18.0))   # buckled straps: (station m ahead of the ankle along the foot, slant deg)
# ---- trousers blouse (solid overlay on the painted trousers)
PUFF_TOP = 0.42                       # the blouse starts this far down the thigh (hip 0 .. knee 1)
PUFF_CLEAR = (0.006, 0.030, 0.042, 0.006)   # "trouser bagginess": clearance at the top / mid-thigh / the bloused
                                      #   overhang just above the tuck / at the tuck into the wraps (m)
PUFF_RIPPLE = (0.08, 5.0)             # fabric folds: relative ripple, folds round the leg
PUFF_NU, PUFF_NV = 14, 12
# ---- tunic tails (shirt below the sash) + vest panels
SKIRT_LEN = (0.175, 0.20)             # "tunic length" below the sash: front / back (m)
SKIRT_CLEAR, SKIRT_FLARE, SKIRT_T = 0.010, 0.040, 0.0035
SKIRT_SHIRT_DEG = 26.0                # the shirt shows this far either side of the front centre; the vest panels outside it
SKIRT_NU, SKIRT_NV = 36, 6
# ---- rope sash + ties + pouch
ROPE_R = 0.0085                       # "rope thickness"
ROPE_DROP = (0.0, 0.016)              # the two wraps: height offsets (m)
KNOT_X = 0.045                        # the knot sits this far to his left of the front centre
TIES = ((0.26, 0.010, 0.0075), (0.15, -0.012, 0.0065))   # hanging ties: (length m, x offset from the knot, radius)
POUCH = {"phi": 58.0, "size": (0.042, 0.022, 0.052), "drop": 0.012}   # belt pouch on his left hip: azimuth from the front
                                      #   (deg, + = his left), half sizes (across, depth, height), drop below the sash
# ---- sleeves, bracer, necklace, vest rivets
SLEEVE_ROLL = (0.034, 0.011, 0.006)   # rolled cuff: height, thickness, clearance
BRACER = {"t": (0.03, 0.55), "clear": 0.0045, "t_leather": 0.0035, "nu": 20, "nv": 8, "straps": 3,
          "strap_w": 0.0085, "gem": (0.011, 0.019, 0.006), "gem_az": 40.0}   # left forearm (the sheet): span along the
                                      #   forearm (wrist 0 .. elbow 1), clearance, leather thickness, grid, crossed straps,
                                      #   diamond crystal (half width, half length, depth), its azimuth (front 0 .. lateral 90)
PENDANT = (0.0062, 0.0065, 0.019, 0.125)   # crystal pendant: radius, point up, point down, drop below the neck base (m)
CORD_R = 0.0016
RIVETS = 4                            # brass rivets per vest front edge
# ---- cloak
CLOAK_PHI_L = ((0.0, 150.0), (0.22, 104.0), (1.0, 108.0))   # "cloak wrap" his LEFT side: (v down the cloak, edge azimuth
                                      #   from the back centre deg) -- over the shoulder to the clasp, then falling BEHIND the
                                      #   arm (the sheet: the bracer arm hangs in front of the cloak)
CLOAK_PHI_R = ((0.0, 138.0), (0.22, 97.0), (1.0, 100.0))   # his RIGHT (pitchfork) side: thrown back behind the arm
CLOAK_TOP = (0.000, 0.052, -0.050)    # top edge: at the back of the neck base (+ up), over the shoulder tops (above the
                                      #   shoulder joint), at the front ends (below the neck base)
CLOAK_HEM_Z = 0.50                    # "cloak length": hem height (knee)
CLOAK_TEAR = (0.13, 0.035)            # "ragged hem": longest point, typical tooth (m)
CLOAK_CLEAR, CLOAK_FLARE = 0.022, 0.085
CLOAK_NU, CLOAK_NV = 40, 22
CLOAK_T = 0.0045
CLOAK_FOLD, CLOAK_FOLDS = 0.016, 7.5  # "cloak folds": depth at the hem, folds across the width
CLOAK_SLITS = [(0.30, 0.30), (0.62, 0.36), (0.83, 0.28)]   # (column fraction, slit height as a fraction of the drop)
CLOAK_WORN = (0.74, 0.50)
CLOAK_ARM_FOLLOW = (0.97, 0.05, 0.15)   # "cloak rides the arms": where the cloak lies over a shoulder / arm it takes up to
                                      #   this share of that arm's skin weights, fully within the 2nd value (m) of it, none
                                      #   beyond the 3rd             # worn / sun-faded mottle: noise threshold, feature scale (m)
# patches: (a m across from the cloak's right front edge along the row, b m down from the top, half w, half h, rot deg,
# kind) -- the sheet's back view (right shoulder blade, mid left, lower left + right, the hem) + the front-left panel
PATCHES = ((0.42, 0.20, 0.050, 0.042, 12.0, "a"), (0.86, 0.34, 0.046, 0.058, -8.0, "b"), (0.56, 0.58, 0.058, 0.044, 20.0, "c"),
           (1.00, 0.70, 0.050, 0.050, -14.0, "a"), (0.30, 0.66, 0.042, 0.052, 6.0, "b"), (1.50, 0.55, 0.050, 0.040, -22.0, "c"),
           (1.62, 0.26, 0.036, 0.046, 10.0, "a"))
STITCH = (0.033, 0.013, 0.0024, 0.0014)   # cross stitches: spacing along the patch edge, length across it, width, height
HOOD = {"w": 0.15, "len": 0.30, "top_dz": -0.012, "clear": 0.010, "bulge": 0.030, "nu": 11, "nv": 9}   # the hood lying down the back
COWL = {"fold": ((0.0, 0.0), (0.004, 0.017), (0.013, 0.029), (0.027, 0.026), (0.041, 0.010), (0.051, -0.010)),
        "gap_deg": 56.0, "clear": 0.003, "cols": 30, "t": 0.0045, "lump": 0.22}   # the hood's bunched rim as a FOLDED
                                      #   collar: row profile (outward, up m) at the back, rising from the cloak's top edge,
                                      #   rolling over and down onto the shoulders; front opening (deg), clearance, columns,
                                      #   fabric thickness, lumpiness (a tube read as a life-ring)
CLASP_R = 0.019
# ---- pitchfork (own object; its frame: +Z up the shaft, origin = the butt)
FORK = {"len": 1.74, "shaft_r": (0.0160, 0.0142), "yoke_at": 0.79, "butt": (0.0, 0.050), "collar": (0.755, 0.79),
        "grip": (0.50, 0.745), "grip_rings": 24, "wrap_pitch": 0.021, "wrap_t": 0.0026, "ferrule_t": 0.0022,
        "tine_len": 0.35, "tine_spread": 0.068, "tine_r": (0.0105, 0.0040), "grip_at": 0.64}   # "pitchfork": length
                                      #   (sheet: 1.03 x his height), the hand grips at grip_at x len (the sheet's chest height)
FORK_REST = (-0.44, -0.02)            # rest pose: the fork stands upright here (x, y), butt on the floor
FORK_ELBOW_POLE = (-0.55, -0.28, -0.80)   # the fork arm's elbow direction (IK pole): out, forward, down
# ---- bake + material
BAKE_RES = (2048, 1024)
CUT_SNAP = 0.12
SLIVER_AREA = 5e-8
ENCLOSED_DIRS = ((0, -1, 0), (0.7, -0.7, 0), (-0.7, -0.7, 0), (0, -0.7, 0.7), (0, -0.7, -0.7), (0.5, -0.5, 0.5),
                 (-0.5, -0.5, 0.5), (0.5, -0.5, -0.5), (-0.5, -0.5, -0.5), (0, -0.57, 0.82), (0.95, -0.3, 0), (-0.95, -0.3, 0))
                                      # v2 hidden-skin harvest: a face vertex is enclosed when rays toward ALL of these (front /
                                      #   three-quarter / side / below / the tactical view from above) hit the head or an eyeball
V1_FACE = {                           # v1 baseline (commit 96478b3), MEASURED by this build run in the v1 configuration
                                      #   (--set TARGETS=<v1> LIP_SEAL=None BROW_W=(0.0064,0.0028) LIP=(0.017,0.0024,0.0032)
                                      #   FACE_UV_SCALE=1.0): the v2 report quotes it next to the live v2 numbers
    "eye_dial": 0.40, "aperture_mm_L": {"outer": 13.7, "up": 2.6, "inner": 8.1, "down": 6.7}, "open_w_mm": 21.8, "open_h_mm": 9.3,
    "brow_W_mm": [6.4, 2.8], "brow_width_mm_measured": {"t0.05": 5.95, "t0.25": 5.7, "t0.50": 4.75, "t0.75": 3.65, "t0.95": 2.4},
    "mouth_compression": 1.0, "lip_pairing_x0": {"upper_fwd_mm": 4.24, "lower_fwd_mm": 0.53, "lower_lead_mm": -3.71,
                                                  "upper_proud_h_mm": 7.5, "lower_proud_h_mm": 2.9},
    "face_zone_visible": {"tris": 6596, "area_cm2": 1070.9, "tris_per_cm2": 6.16, "dihedral_deg_p50_p90": [6.99, 26.35],
                          "texels_per_mm": 0.494, "normal_ref": "smooth low, rendered flat (the facets show)"},
    "face_zone_incl_hidden_mouth_interior": {"tris": 8376}, "total_tris": 49662}
AO_SAMPLES = 48                       # AO bake samples (v1 16: the v2 face texel density resolved the 16-sample noise as speckle in the lip crease)
AO_FLOOR = {"default": 0.42, "skin": 0.62, "hair": 0.82, "cloth": 0.50}
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow"], "hair": ["hair", "hair_shade", "hair_tie"],
                    "cloth": ["cloak", "cloak_worn", "patch_a", "patch_b", "patch_c", "shirt", "vest", "trousers"]}
HAIR_BONES, CAPE_BONES, CAPE_CHAINS = 3, 4, 5
GRIP_CURL = (58.0, 72.0, 50.0)        # "grip": finger curl round the shaft, per joint (deg)
GRIP_THUMB = (22.0, 30.0)
RELAX_CURL = (12.0, 18.0, 12.0)
# ---- idle: earnest stance, leaning a little on the planted pitchfork at his right
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH_DEG = 1.4
IDLE_SWAY = 0.008                     # pelvis side sway (m)
IDLE_WEIGHT = (-0.014, 2.2)           # weight toward the pitchfork: pelvis shift (m) / hip roll (deg)
IDLE_REACH = 0.975
IDLE_FEET = {"L": (0.020, -0.03, 9.0), "R": (-0.015, 0.02, 11.0)}   # foot offset from rest (x, y) + toes-out yaw (deg)
IDLE_HEAD = (-3.0, 3.5)               # "earnest": chin slightly UP (deg, < 0) / slow look yaw amplitude (deg)
IDLE_LARM = (10.0, 3.0, 10.0)         # free (left) arm: lowered further (deg), forward (deg), elbow bend (deg)
FORK_IDLE_BUTT = (-0.40, -0.30)       # the planted fork's butt on the floor (x, y)
FORK_IDLE_LEAN = (5.0, 4.0)           # its lean (deg): top toward +X (him) / toward +Y (back, toward him)
FORK_IDLE_SWAY = 0.7                  # breath sway of the planted fork about its butt (deg)
IDLE_EASE = 1.8
IDLE_OVERLAP = (3.0, 6.0)
IDLE_HAIR_DEG = (0.5, 1.6)
IDLE_CAPE_DEG = (0.4, 1.6)
# ---- walk: boyish lighter stride, quicker than the vampire's 96 steps/min, a little bounce, the fork carried upright
WALK_N = 26                           # "walk cycle": 26 frames per stride (2 steps) = 110.8 steps/min (vampwarrior 30 = 96)
WALK_STANCE = 0.58
WALK_STEP_A = 0.235                   # "stride length": half the stance travel (m)
WALK_LIFT = 0.080                     # swing foot lift (m)
WALK_TOEOFF = (0.13, 24.0)            # toe-off: last fraction of the cycle in stance / max heel-up roll (deg)
WALK_HEELSTRIKE = 9.0                 # toes-up at the end of the swing (deg; lifted clear of the floor by the proof)
WALK_FOOT_X = 0.86                    # feet land this x their rest width
WALK_REACH = 0.975
WALK_PELVIS = (4.0, 2.5, 0.010)       # pelvis yaw (deg), roll (deg), side sway (m) -- less hip than the vampire
WALK_BOUNCE = 0.014                   # "bounce": extra pelvis dip at each foot contact (m)
WALK_CHEST = (6.0, -2.0)              # chest counter-yaw (deg), chest lean (deg; < 0 = a slight forward eagerness)
WALK_LARM = (4.0, 16.0, 14.0)         # free arm: lowered (deg), swing (deg), elbow bend (deg)
FORK_WALK = {"grip": (-0.08, -0.24, -0.06), "tilt_fwd": 7.0, "tilt_out": -3.0, "swing": 2.5, "bob": 0.010}
                                      # fork carry: the grip in the chest frame relative to the right shoulder joint (m),
                                      #   shaft tilt top-forward / top-outward (deg; < 0 = the top toward him, the butt clear
                                      #   of his legs), pendulum swing (deg), grip bob (m)
WALK_CAPE = (8.0, 1.2, 3.0)           # cloak trail (deg), flutter root / tip (deg)
WALK_HAIR = (3.0, 1.0, 3.0)
WALK_OVERLAP = (2.0, 4.0)
WALK_NOD = (1.6, 1.0)
FT = {"hair_fringe": {"driver": "head", "hz": 3.4, "zeta": 0.42, "drag": 0.9, "gain": 1.0},
      "hair_side": {"driver": "head", "hz": 3.2, "zeta": 0.45, "drag": 0.8, "gain": 0.8},
      "hair_tail": {"driver": "head", "hz": 2.6, "zeta": 0.40, "drag": 1.2, "gain": 1.0},
      "tie": {"driver": "pelvis", "hz": 2.4, "zeta": 0.35, "drag": 1.4, "gain": 1.0},
      "cape": {"driver": "spine_03", "hz": 2.3, "zeta": 0.6, "drag": 1.1, "gain": 1.0, "axes": (1.0, 0.3, 0.3)}}   # cloak: full
                                      #   trailing (world X) lag, the sideways / twisting (Y / Z) lag damped to 0.3 (the side
                                      #   panels swung into the legs and arms at 1.0: poke-through gate)
FT_INTO_BODY_DEG = 1.5
FT_DRIFT_K = 0.6

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
SCRATCH = argv[argv.index("--scratch") + 1] if "--scratch" in argv else None
OUT_IMPROVED = os.path.join(SCRATCH or os.path.join(ROOT, "improved"), UNIT + ".blend")
OUT_RIGGED = os.path.join(SCRATCH or os.path.join(ROOT, "rigged"), UNIT + ".blend")
OUT_GLB = os.path.join(SCRATCH or os.path.join(ROOT, "rigged"), UNIT + ".glb")
TEX_DIR = os.path.join(SCRATCH or os.path.join(ROOT, "improved"), "textures")
TAG = "twin" if DIGEST_ONLY else ("scratch" if SCRATCH else "main")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "name_status": "named by the sheet (WREN, Oakvale Village)",
          "source": "none: the artist's three-view sheet design/reference/wren-character-sheet.webp", "tier": "hero",
          "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}
SECTIONS = ["wren_s1_body.py", "wren_s2_regions.py", "wren_s3_outfit.py", "wren_s4_cloak.py", "wren_s5_hair.py",
            "wren_s6_assemble.py", "wren_s7_rig.py", "wren_s8_clips.py"]
for sec_ in SECTIONS:
    _p = os.path.join(HERE, sec_)
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
