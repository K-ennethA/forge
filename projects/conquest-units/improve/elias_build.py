"""Professor Elias -- ROYAL RESEARCHER, hero tier (Conquest roster), v1 DRAFT (lane-conventions "Two speeds": one clean
headless build + probes-as-sanity + one comparison sheet; no gate wall, no determinism twin, NO clips this pass).

    blender --background --factory-startup --python improve/elias_build.py -- \
        [--preview <out.blend>]          (body + outfit + hair + props + regions + palette only: no bake, no rig)
        [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)
        [--glb]                          (also export rigged/elias.glb; off by default this pass)

Spec (binding): design/review-log.md 2026-10-02 "NEW UNIT: PROFESSOR ELIAS" (inline sheet; the transcription is binding):
60+ royal researcher, tousled grey hair, round wire glasses, full grey beard + mustache, lined kind face, heavy brows,
blue cravat, navy gold-trimmed scholar mantle with capelet + trident brooch (teal gem), the trident on the mantle back and
the book, cream under-robe with big rolled sleeves, dark bracers, wide brown belt with pouches + scroll tubes, brown
cross-body satchel on the back (buckled flap, scrolls), dark brown trousers, brown gold-trimmed boots; STAFF topped with
a brass armillary sphere holding a teal orb (brass ferrule); BOOK = dark tome, trident crest + clasp. Palette chips:
navy, dark brown, taupe, cream, gold, teal.
House style: design/character-style-guide.md. Reference implementation: improve/wren_* (read-only): the stages below are
copied from it and adapted (s1 verbatim; s2 garments re-cut; s3/s4 the Elias outfit + mantle; s5 the ribbon-lock stack
+ (v4) the long beard / mustache as conforming shells; s6 props + glasses + bake (the hair proxy split scalp / beard);
s7 rig + prop bones + grip records + roll search + save).
UNITS: metres, floor z = 0 at the boot soles, front -Y, left +X ("his left" = +X).
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
import elias_parts as VP       # noqa: E402

# =========================================================================== TUNABLE CONSTANTS (artist-facing names)
UNIT = "elias"
CHAR_ID = "elias"
TRI_BUDGET = [30000, 50000]           # declared tier: HERO (quality-tier law: hero = 30-50k)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- body (MPFB2). MakeHuman age macro: 0.5 = 25 yr, 1.0 = 90 yr -> 64 yr = 0.5 + 39/65 x 0.5 = 0.80
MACRO = {"gender": 1.0, "age": 0.80, "muscle": 0.40, "weight": 0.50, "proportions": 0.70, "height": 0.5,
         "cupsize": 0.5, "firmness": 0.5}                                  # "age 60+", calm scholar build
RACE = {"caucasian": 0.82, "asian": 0.12, "african": 0.06}
TARGETS = {                           # "face dials": the house-style anime base (Wren's eye / mouth dials) + elderly / kind
    "eyes/l-eye-scale-incr": 1.00, "eyes/r-eye-scale-incr": 1.00,          # "eye size": the dial's max (then EYE_SCALE)
    "eyes/l-eye-height2-incr": 0.35, "eyes/r-eye-height2-incr": 0.35,      # "eye opening" (Wren's; the age macro droops the lids)
    "head/head-scale-vert-incr": 0.50, "head/head-scale-horiz-incr": 0.40, "head/head-scale-depth-incr": 0.40,   # "head size":
                                      #   the anime head-to-body read (the age / proportion macros shrink the head vs Wren)
    "eyes/l-eye-bag-decr": 1.00, "eyes/r-eye-bag-decr": 1.00,              # no bags (the flat under-eye law)
    "head/head-oval": 0.35,                                                # "head shape"
    "chin/chin-bones-decr": 0.20, "chin/chin-width-decr": 0.10,            # "jaw": softer than a square old jaw (the beard sits on it)
    "cheek/l-cheek-volume-decr": 0.15, "cheek/r-cheek-volume-decr": 0.15,  # "cheeks"
    "nose/nose-scale-horiz-decr": 0.25, "nose/nose-volume-decr": 0.25, "nose/nose-point-width-decr": 0.40,   # "nose size"
    "nose/nose-flaring-decr": 0.40, "nose/nose-nostrils-width-decr": 0.40,
    "mouth/mouth-scale-horiz-decr": 0.90,                                  # "MOUTH WIDTH": seam -> ~0.70 x eye spacing (house 0.65-0.73)
    "mouth/mouth-trans-up": 0.48,                                          # "MOUTH HEIGHT": toward the Ashe v_ratio 0.29
    "mouth/mouth-lowerlip-volume-incr": 0.50, "mouth/mouth-upperlip-volume-decr": 0.40,
    "mouth/mouth-angles-up": 0.50,                                         # "mouth corners level"
    "eyebrows/eyebrows-angle-up": 0.20,                                    # "kind brows": inner ends a touch up
    "expression/units/caucasian/eye-left-slit": 0.15, "expression/units/caucasian/eye-right-slit": 0.15,   # "calm lids"
}
TARGETS_EDIT = None                   # (s1 probe hook kept from Wren; None = TARGETS as listed)
BODY_H = 1.72                         # "height" barefoot (m); the boot soles add SOLE_T
REST_ARM_DOWN = 22.0                  # "rest arm drop": the MPFB A-pose arms lowered this much (deg) in the bind pose
REST_ELBOW_OPEN = 24.0                # the MPFB rest elbow opened this much (deg) in the bind pose
SOLE_T = 0.024                        # "boot sole": the foot sits this far above the floor inside the boot
# ---- face (house style; values = Wren's settled ones unless noted)
LIP_SEAL = (0.009, 0.0003, 0.0025)
LIP_FLAT = (0.026, 0.0088, 0.011, 0.005, 0.0, 0.0012)
LIP_PROFILE = "bridge"
LIP_BRIDGE = (12.0, 26.0, 13.0, 26.0, 4.0, 5.0, 2.0, 4.0, 2.0)
LIP_HIDDEN_K = (0.0, 0.15, 0.002, 0.0001)
LIP_RIM_STEP = 0.0001
LIP_RIM_TUCK = (0.0002, 0.0010, 0.85)
LIP_RIM_TUCK_SIDES = (1.0,)
LIP_FRONT_TOL = 0.003
EYE_SCALE = 1.30                      # "eye size (geometric)": socket + eyeball x1.30 (Wren: "perfect")
EYE_SCALE_ZONE = (18.0, 30.0, 1.0, 2.2)
EYE_ORBIT = {"outer": (19.0, 29.0), "up": (11.0, 21.0), "inner": (11.0, 18.5), "down": (11.5, 21.0)}
EYE_SLIDE = (1.0, 3.0)
EYE_ORBIT_DEPTH = (0.4, 1.1, 0.0, 0.6)
EYE_BAG_FILL = (0.25, 12.0, 24.0, 4.0, 2.5, 2)   # "eye bags: GONE" (flat under-eye, the style law)
EYE_BAG_FLAT = None
EYE_IRIS_DEG, EYE_PUPIL_DEG = 27.0, 10.0          # "iris size" (~58 % of the opening on Wren)
EYE_SEG = 36
EYE_BACK = 110.0
IRIS_SHADE = 0.30
EYE_HILITE = (32.0, 0.34, 3.9)
EYE_HILITE_RING = 2.0
LINER_W = (0.0013, 0.0003)            # lower liner 0.3 mm
LINER_WING = (0.0007, 20.0, 10.0)
LASH_PROFILE = ((0.0, 2.0), (30.0, 2.3), (90.0, 1.9), (150.0, 1.0), (180.0, 0.5))   # "upper lash band" (a touch lighter than Wren's)
LASH_WING = (1.6, 11.0, 2.0)          # "lash wing": shorter flick (an old man's eye)
BROW_PTS = ((-1.20, 0.0078), (-0.45, 0.0088), (0.50, 0.0092), (1.30, 0.0070))   # "brows": kind arc, tail dropping
BROW_W = (0.0125, 0.0075)             # "HEAVY brows" (sheet): inner / tail width (m); Wren 8.6 / 4.0
BROW_TAPER = 1.0
LIP = (0.0190, 0.0035, 0.0065)        # paler lip tint zone
LIP_FORMS = {"lower": (0.0012, 0.0070, 0.80, 0.80, 0.40), "upper": (0.0004, 0.0050, 0.70, 0.90, 0.40)}
LIP_DZ = 0.0
MOUTH_LINE = (0.0013, 0.45)
MOUTH_LINE_EXT = 0.0010
MOUTH_LINE_REFINE = {"passes": 3, "samples": 41}
MOUTH_LINE_CENTROID = 0.00015
MOUTH_LINE_SNAP = 0.02
MOUTH_LEN = None
MOUTH_SMIRK = None                    # "smirk": none (calm); an artist question
MOUTH_SMIRK_GEO = None
MOUTH_SMOOTH = (4.0, 30.0, 12.0, 28.0, 6.0, 2.0, 2.0, 5.0)
MOUTH_PROXY = (4.0, 30.0, 12.0, 28.0, 6.0, 2.0)
FACE_UV_SCALE = 4.5
FACE_NORMAL_REF = "flat"
MOUTH_IN_D = 0.0032
JAW_LIGHT_DEG = 42.0
JAW_SMOOTH = 3
JAW_GATE = (0.008, 0.6, 0.012)
MOUTH_HIDDEN = (0.034, 0.014, 0.030, 0.00005, 0.06, 0.003)
EYE_RIM_HIGH_OUT = (0.5, 2.0, 100.0, 50.0)
ENCLOSED_DIRS = ((0, -1, 0), (0.7, -0.7, 0), (-0.7, -0.7, 0), (0, -0.7, 0.7), (0, -0.7, -0.7), (0.5, -0.5, 0.5),
                 (-0.5, -0.5, 0.5), (0.5, -0.5, -0.5), (-0.5, -0.5, -0.5), (0, -0.57, 0.82), (0.95, -0.3, 0), (-0.95, -0.3, 0))
# ---- face: the elderly read (paint, not geometry: the style guide's "features are paint on simple smooth geometry")
FACE_LINES = {"crow": (3, 0.0042, 0.0007, 0.0035), "brow_furrow": (2, 0.020, 0.0007, 0.0)}   # "lined kind face": painted
                                      #   lines -- crow's feet (count, length m, width m, gap past the outer corner m) and
                                      #   forehead lines (count, half length m, width m); None = none
FOREHEAD_LINES_Z = (0.030, 0.038)     # the forehead lines' heights above the eye centres (m)
# ---- the under-beard paint + (v6) the BEARD as MASS-FIRST CLUMPED STRANDS over a thin core shell (review-log 2026-10-03
# "Elias v5 hair APPROVED; beard same treatment": the v4 / v5 long smooth lobed shell -- one conforming volume, 75 mm below
# the chin, 3,432 tris with the mustache -- replaced; judged against design/reference/elias/elias_sheet.webp front / side /
# head-detail panel: a cropped cheek beard following the jaw, a chin mass of clumped strands tapering to a soft rounded point
# ~30 mm below the chin, neck skin showing under the jaw at the sides, the mustache two soft sweeps joining the beard)
BEARD_ZONE = {"sideburn_z": -0.008, "cheek": ((0.026, -0.064), (0.042, -0.062), (0.058, -0.052), (0.072, -0.036), (0.080, -0.012)),
              "lip_band": (0.0045, 0.0060, 0.0050), "neck_drop": 0.020,
              "neck_rise": (0.034, 0.018, 0.050)}   # "beard coverage" on the skin: the top edge
                                      #   (|x| m from the midline, z m below the eye centres) from the sideburn down the
                                      #   cheek to the mustache; the lip band kept skin (m past the seam ends, above / below
                                      #   the seam); under the jaw down this far below the chin at the front, the lower edge
                                      #   rising (m, over |x| m -> m) toward the jaw corners (v6: the sheet's neck shows skin
                                      #   under the jaw -- v4's 40 mm drop / 44 mm rise served the long bib; measured: the zone
                                      #   now ends 14 mm above the chin at |x| >= 50 mm, the jaw underside there sits 1-10 mm
                                      #   above the chin, so the core's side columns close onto the jaw; v6 cheek point
                                      #   (0.080, -0.012): the top edge ramps up to the sideburn instead of stepping 28 mm at
                                      #   |x| 72 mm -- the step left a zone sliver above each column's lowest run, visible from
                                      #   the front since v4 (zone probe: 7-18 mm deep at psi -57 deg))
BEARD_ENV_EAR_Y = 0.005               # the beard envelope uses head skin in front of (head centre y - this) only: no ears
BEARD_CORE = {"offset": (0.0025, 0.0048),    # v6 CORE SHELL "beard core thickness": over the cheeks / at the chin front (m;
                                             #   v4's 4.5 / 9.5: the volume now lives in the clumps over it)
              "offset_psi": (12.0, 50.0),    #   the chin -> cheek blend over this azimuth span (deg)
              "sink": 0.0006,                # the boundary rows sit this far UNDER the skin: no visible edge, the fade takes over
              "ramp": 0.0050,                # the thickness ramps in over this distance (m, down from the top edge; v6: 8 mm left the
                                             #   zone 3-5 mm deep visible under the nose between the 6 deg columns)
              "hang_off": 0.0040,            # below the chin the core stands at least this far in front of the cravat / chest (m)
              "drop_x": 0.10,                #   (the drop envelope's window: |x| < this, so no shoulder / arm)
              "taper": (0.45, 0.55),         # below the jaw the core slopes inward this much per metre down: front / sides
              "col_deg": 6.0,                # column step (deg; v4 2.5 -- no lobes to carry, the clumps carry the edge)
              "rows": (9, 3),                # outer rows (top edge -> hem) / underside rows (hem -> neck)
              "under_clear": 0.0015,         # the underside keeps this far off the neck skin / cravat / chest (m)
              "root_f": 0.07,                # paint: the root tier (into the stubble fade) above this row fraction
              "inner_dz": 0.004}             # paint: below (chin - this) the core is the DARK INNER beard (hair_beard_crevice:
                                             #   the gaps between the hanging clumps read as shadowed strands -- the scalp's
                                             #   dark inner cap rule), above it hair_beard (the cropped cheek beard)
BEARD_CORE_LEN = 0.026                # "core length": the core's hem this far below the chin at the front (m; the clumps hang
                                      #   past it to the silhouette's point)
BEARD_CORE_HEM = ((0.0, 1.00), (14.0, 0.85), (26.0, 0.55), (40.0, 0.15), (55.0, -0.20), (90.0, -0.45))   # the core hem's drop
                                      #   per azimuth (deg from the front, share of BEARD_CORE_LEN; < 0 = above the chin: the
                                      #   side columns end on the jaw); never above the zone's lower edge (- 2 mm)
BEARD_FADE = (0.0045, 3, 0.0022, 0.0060, 0.03)   # "fade band width": the stubble fade on the skin outside the zone edge --
                                      #   width (m of the zone field), painted steps, edge serration amplitude (m), serration
                                      #   tooth width (m), lip-band steepness (Varden v4's rule; grey stubble on warm skin;
                                      #   measured: 7 mm of field read as a grey film over the cheeks)
BEARD_CHAIN_PSI = 12.0                # the core's hanging mass rides beard.C within this azimuth, beard.L / .R beyond (+-4 deg)
BEARD_CHAIN_LEAVE = 0.010             # the chains take over this far ABOVE the chin (m): above it everything is rigid on the head
BEARD_MASSES = {                      # v6 "beard masses" (research H1, the v5 scalp's mass-first rule): per mass the chain (None
    # = rigid on the head), stack offset (m), volume lift off the core (m), free point (control index where the lock leaves
    # the face), and its LOCKS bottom -> top: (tier, width mm, root (psi deg, z mm vs the chin bottom), tip (psi, z mm, standoff
    # mm off the core envelope)); psi + = his left. Every mass spreads >= 3:1 in width; the tips vary in length so the hem
    # breaks into clumps of a rounded outline (the sheet), never a comb
    "beardsL": {"chain": None, "layer": 0.0, "lift": 0.0012, "free_k": 3, "locks": (    # his left cheek / sideburn: short
        ("L", 22.0, (79.0, 104.0), (75.0, 60.0, 0.0)),                                   #   strands down the cheek, cropped
        ("M", 14.0, (70.0, 82.0), (63.0, 44.0, 0.0)),
        ("S", 7.0, (76.0, 96.0), (79.0, 50.0, 0.5)))},   # (roots >= 5 deg inside the
                                                                                     #   core span: past it the sunk end column)
    "beardsR": {"chain": None, "layer": 0.0, "lift": 0.0012, "free_k": 3, "locks": (
        ("L", 23.0, (-79.0, 102.0), (-74.0, 58.0, 0.0)),
        ("M", 15.0, (-69.0, 80.0), (-62.0, 42.0, 0.0)),
        ("S", 7.0, (-76.0, 94.0), (-80.0, 50.0, 0.5)))},
    "beardL": {"chain": "beard.L", "layer": 0.0005, "lift": 0.0015, "free_k": 2, "locks": (   # his left jaw: from the cheek
        ("L", 30.0, (56.0, 64.0), (30.0, -10.0, 2.0)),                                   #   sweeping forward-down along the
        ("M", 20.0, (68.0, 54.0), (46.0, -2.0, 1.5)),                                    #   jaw to under the chin's side
        ("M", 15.0, (44.0, 44.0), (22.0, -16.0, 2.0)),
        ("S", 9.0, (76.0, 44.0), (62.0, 6.0, 1.0)))},
    "beardR": {"chain": "beard.R", "layer": 0.0006, "lift": 0.0015, "free_k": 2, "locks": (
        ("L", 31.0, (-55.0, 62.0), (-29.0, -11.0, 2.0)),
        ("M", 19.0, (-67.0, 52.0), (-45.0, -1.0, 1.5)),
        ("M", 15.0, (-43.0, 42.0), (-21.0, -15.0, 2.0)),
        ("S", 9.0, (-75.0, 42.0), (-63.0, 7.0, 1.0)))},
    "beardC": {"chain": "beard.C", "layer": 0.0010, "lift": 0.0020, "free_k": 2, "locks": (   # the CHIN mass: wide clumps
        ("L", 40.0, (0.0, 20.0), (0.0, -28.0, 2.5)),                                     #   converging on the soft point,
        ("L", 34.0, (-17.0, 24.0), (-4.0, -25.0, 2.5)),                                  #   the outer ones shorter and
        ("L", 34.0, (17.0, 24.0), (4.0, -24.0, 2.5)),                                    #   aimed in (the rounded outline;
        ("M", 24.0, (-28.0, 14.0), (-9.0, -17.0, 2.5)),                                  #   v6 draft: tips at +-12 deg curled
        ("M", 24.0, (28.0, 14.0), (8.0, -18.0, 2.5)),                                    #   out like claws)
        ("M", 16.0, (-5.0, 30.0), (-1.5, -33.0, 3.0)),                                   #   the longest, on top: the point
        ("S", 12.0, (9.0, 28.0), (2.5, -30.0, 3.0)),
        ("S", 10.0, (-21.0, 18.0), (-7.0, -22.0, 2.5)))}}
MUSTACHE_MASSES = {                   # v6 "mustache": two soft SWEEPS (the head-detail panel), each layered ribbon locks lying
    # on the upper lip / core and drooping past the mouth corners into the beard: (tier, width mm, control points (x mm off
    # the midline, z mm vs the mouth line), face-projected; the last = the tip); rigid on the head
    "mustL": {"layer": 0.0016, "lift": 0.0008, "free_k": 3, "locks": (
        ("L", 16.0, ((1.5, 12.5), (11.0, 11.0), (20.0, 7.0), (28.0, -1.0), (34.0, -14.0))),
        ("M", 10.0, ((5.0, 14.5), (14.0, 13.0), (23.0, 9.0), (31.0, 0.0), (38.0, -18.0))),
        ("S", 5.0, ((9.0, 15.5), (17.0, 14.0), (25.0, 10.0), (31.0, 4.0), (36.0, -6.0))))},
    "mustR": {"layer": 0.0019, "lift": 0.0008, "free_k": 3, "locks": (
        ("L", 16.0, ((-1.5, 12.5), (-11.0, 11.0), (-20.0, 7.0), (-28.0, -1.5), (-33.0, -15.0))),
        ("M", 10.0, ((-5.0, 14.5), (-14.0, 13.0), (-23.0, 9.0), (-31.0, 0.0), (-37.0, -17.0))),
        ("S", 5.0, ((-9.0, 15.5), (-17.0, 14.0), (-25.0, 10.0), (-31.0, 4.0), (-35.0, -7.0))))}}
MUSTACHE_ROOT_K = 0.70                # "mustache root width" (x width): the sweeps start wide under the nose so the two roots cover
                                      #   the upper-lip zone strip between the nose and the lip band (measured on the v6 draft at
                                      #   the tier's 0.40: the zone 3-5 mm deep visible between the roots)
MUSTACHE_LIP_CLEAR = 0.0018           # the mustache's lower edge stays this far above the mouth line at |x| < the seam's
                                      #   half width x 0.6 (m; measured, reported)
BEARD_TUCK = False                    # "beard tuck shade": the scalp's whole-segment tuck stroke on the beard locks (off: horizontal
                                      #   bars across the hanging clumps, measured on the v6 draft)
BEARD_LOCK_T = 1.0                    # "clump thickness": the beard locks' thickness x the scalp tier's (v6 draft 1.25: tubes)
BEARD_RIBBON = {"taper": (1.6, 0.14), "belly": 0.14, "tip_k": {"L": 0.40, "M": 0.40, "S": 0.50}, "sway": 0.06, "radial": 0.70}   # "soft rounded taper":
                                      #   the beard locks' width taper (exponent, last-section share; scalp (1.15, 0.03)), swell,
                                      #   tip pole length share (scalp 1.0): rounded clump ends, not needles; the S-curve sway x
                                      #   width (scalp 0.16: the hanging clumps wriggled like fingers); the frame lean toward the
                                      #   HORIZONTAL radial (scalp 0.35 toward the head centre: the hanging clumps' sections
                                      #   twisted sideways under the chin -- crescent tips)
BEARD_STATIONS = {"beardC": 9, "beardL": 8, "beardR": 8, "beardsL": 8, "beardsR": 8, "must": 8}   # "lock segments" (the
                                      #   short beard locks; the tri budget: 50k hero ceiling)
# ---- scalp hair (ribbon locks, the house style; tousled grey, swept back from a receding front hairline)
HAIR_INTERIOR_R = 0.70
HAIRLINE = (0.074, -0.050)            # "hairline": above the eye centres at the front (receding) / at the nape vs the head joint
HAIR_CAP_T = 0.0120                   # "hair volume" on the scalp (v5: the inner volume under the masses -- v4 5 mm; the
                                      #   locks rest on it instead of standing off a thin cap with open cavities under them)
HAIR_CAP_INNER = False                # the cap's scalp-facing shell harvested (provably hidden)
HAIR_ROOT_K = 0.30
HAIRLINE_FEATHER = (0.030, 0.0022)     # (v5: the thicker cap feathers over 30 mm above the hairline; v4 22 mm)
HAIR_UV_STRIP = 0.16                  # hair + beard faces packed into the right 16 % of the UV square
LOCK_OFF = 0.0020
# ---- v5 SCALP (review-log 2026-10-03 "Elias SHEET SAVED + scalp hair rejected"; silhouette-matched to
# design/reference/elias/elias_sheet.webp front / side / back + head panel): a SIDE PART on his left, LONG BANGS sweeping from
# it across the forehead to his right temple, the short side falling from it down his left temple, flowing tousled side waves
# flaring out over both ears, a tousled crown and a layered back -- built MASS-FIRST (research H1: 4-7 primary masses, lock
# width spread >= 3:1), every mass made of Wren-grade ribbon locks of hand-set widths; flow from the part (the whorl only at
# its back end, for the back mass and the crown flicks)
HAIR_PART = ((30.0, 40.0), (44.0, 56.0), (82.0, 68.0))   # "side part": the part line (psi deg from the front toward his left,
                                      #   elevation deg about the head centre) from the front hairline over his left brow back
                                      #   over the top to the whorl (the sheet's head panel: the hair splits over his left eye)
HAIR_WHORL = (150.0, 62.0)            # "crown whorl": the part's back end (the back mass and the crown flicks start here)
PART_ROOT_K = 0.60                    # root width (x width) of the locks that start ON the part (Wren v4's measured value: the
                                      #   tiers' 0.30-0.40, made for roots converging on one whorl, left dark cap beside the part)
BANG_TIP_OFF = 0.006                  # "bang tips off the forehead": face-projected tips stand this far (m) + LOCK_OFF off the skin
BANG_AIM_EL = 8.0                     # the bangs aim this far (deg) above their own tip, so each arcs over the forehead from the
                                      #   part straight down-across to it (a front-top "brim" stretch read as a helmet edge)
GLASSES_HAIR_CLEAR = 0.0025           # "hair over the glasses": the locks' spines keep this much more (m) off the rims / temple arms
BANG_SWEEP = 10.0                     # "bang sweep": each bang aims this far (deg) toward the part above its tip, so its last
                                      #   stretch crosses the forehead diagonally (the sweep) instead of hanging straight
HAIR_MASSES = {                       # "primary masses": per mass the follow-through chain, stack offset (m), volume lift off the
    # cap (m: down the sides / over the top), root position along origin -> tip, free point (control index where the lock
    # leaves the head), and its LOCKS listed bottom -> top: (tier, full width mm, origin = part parameter 0 (front end) .. 1
    # (whorl) or "whorl", tip[, lift x]) with tip = ("face", x mm, z mm above the eye centres) | ("side", psi deg, z mm,
    # outward flick m) | ("head", psi deg, elevation deg, flick m); psi + = his left; lift x scales the mass lift (the
    # under-layers lie closer to the cap and fill under the flared outer locks)
    "back":  {"chain": "hair_back", "layer": 0.0, "lift": (0.010, 0.005), "root": 0.12, "free_k": 3, "locks": (
        ("M", 36.0, "whorl", ("head", 180.0, -50.0, 0.010), 0.40),   # the nape under-layer (the sheet's shadowed nape)
        ("S", 16.0, "whorl", ("head", -158.0, -50.0, 0.012), 0.40),
        ("S", 15.0, "whorl", ("head", 158.0, -48.0, 0.012), 0.40),
        ("L", 58.0, "whorl", ("head", -146.0, -26.0, 0.040)),        # the big back waves, flaring out over the nape
        ("L", 56.0, "whorl", ("head", 146.0, -24.0, 0.040)),
        ("L", 62.0, "whorl", ("head", 178.0, -30.0, 0.036)),
        ("M", 38.0, "whorl", ("head", -170.0, -4.0, 0.032)),         # the upper back layer
        ("M", 36.0, "whorl", ("head", 165.0, -6.0, 0.032)),
        ("M", 34.0, "whorl", ("head", -128.0, -40.0, 0.010), 0.40),  # under-layers behind the ears (the back <-> side seam)
        ("M", 34.0, "whorl", ("head", 128.0, -40.0, 0.010), 0.40),
        ("L", 52.0, "whorl", ("head", -128.0, -16.0, 0.040)),        # the back waves bridging into the side waves
        ("L", 52.0, "whorl", ("head", 128.0, -14.0, 0.040)))},
    "sidel": {"chain": "hair_side.L", "layer": 0.0009, "lift": (0.015, 0.006), "root": 0.0, "free_k": 3, "locks": (
        ("M", 30.0, 0.18, ("side", 78.0, 10.0, 0.008), 0.30),        # under-layer over the temple / ear front (covers the cap edge)
        ("L", 44.0, 0.50, ("side", 108.0, -36.0, 0.012), 0.35),      # under-layer under the flare
        ("M", 36.0, 0.70, ("side", 124.0, -22.0, 0.006), 0.30),      # under-layer behind the ear (the cap edge there)
        ("L", 56.0, 0.40, ("side", 98.0, -24.0, 0.044)),             # his left side wave: swept back over / behind the ear,
        ("L", 54.0, 0.62, ("side", 120.0, -32.0, 0.050)),            #   flaring out (the front view's wings)
        ("M", 36.0, 0.82, ("side", 142.0, -38.0, 0.040)),
        ("S", 17.0, 0.52, ("side", 110.0, -48.0, 0.058)))},          #   the long thin wisp under the jaw line
    "sider": {"chain": "hair_side.R", "layer": 0.0012, "lift": (0.015, 0.006), "root": 0.0, "free_k": 3, "locks": (
        ("M", 32.0, 0.22, ("side", -78.0, 10.0, 0.008), 0.30),       # under-layer over the temple / ear front (covers the cap edge)
        ("L", 46.0, 0.55, ("side", -108.0, -36.0, 0.012), 0.35),     # under-layer under the flare
        ("M", 36.0, 0.72, ("side", -124.0, -22.0, 0.006), 0.30),     # under-layer behind the ear (the cap edge there)
        ("L", 60.0, 0.40, ("side", -94.0, -22.0, 0.052)),            # his right side wave (the sweep side: fuller)
        ("L", 58.0, 0.62, ("side", -120.0, -32.0, 0.052)),
        ("M", 40.0, 0.82, ("side", -144.0, -40.0, 0.040)),
        ("S", 16.0, 0.50, ("side", -108.0, -50.0, 0.060)),
        ("M", 36.0, 0.28, ("side", -80.0, -8.0, 0.030)))},           # over the temple / the ear's front top
    "crown": {"chain": None, "layer": 0.0024, "lift": (0.012, 0.016), "root": 0.0, "free_k": 2, "locks": (
        ("L", 58.0, 0.12, ("head", -34.0, 34.0, 0.014)),             # the front top (under the bangs: no seam between them)
        ("L", 64.0, 0.30, ("head", -66.0, 32.0, 0.024)),             # the top sweep: from the part over the top toward his
        ("L", 60.0, 0.58, ("head", -118.0, 38.0, 0.026)),            #   right / back (the sheet's big swept volume)
        ("M", 42.0, 0.86, ("head", -155.0, 40.0, 0.026)),
        ("M", 40.0, 0.38, ("head", 76.0, 46.0, 0.022)),              # the short side's top, toward his left
        ("S", 22.0, "whorl", ("head", 175.0, 76.0, 0.026)),          # "tousled": flicks lifting off the crown (back view)
        ("S", 19.0, "whorl", ("head", 125.0, 72.0, 0.024)),
        ("S", 21.0, "whorl", ("head", -150.0, 70.0, 0.026)),
        ("S", 18.0, 0.50, ("head", -95.0, 68.0, 0.024)),
        ("S", 18.0, 0.06, ("head", 14.0, 60.0, 0.026)))},            # the strand rising off the part's front (head panel)
    "fall":  {"chain": "hair_side.L", "layer": 0.0030, "lift": (0.006, 0.004), "root": 0.0, "free_k": 2, "locks": (
        ("L", 48.0, 0.14, ("side", 86.0, 18.0, 0.020)),              # the short side: the wing over his left temple
        ("M", 32.0, 0.00, ("side", 58.0, 8.0, 0.010)),               #   strands falling down his left temple past the outer rim
        ("S", 15.0, 0.03, ("side", 68.0, -2.0, 0.010)),              #   (head panel) -- the thin one in front of the ear (side view)
        ("S", 16.0, 0.00, ("face", 42.0, 24.0)),
        ("M", 30.0, 0.04, ("face", 28.0, 32.0)))},                   #   the strand at the part falling onto his left forehead
    "bang":  {"chain": "hair_front", "layer": 0.0036, "lift": (0.004, 0.006), "root": 0.0, "free_k": 2, "locks": (
        ("L", 56.0, 0.34, ("side", -66.0, 14.0, 0.014)),             # LONG BANGS: the outer sweep down to his right temple
        ("L", 54.0, 0.24, ("face", -46.0, 26.0)),                    #   across the forehead toward his right brow
        ("L", 46.0, 0.14, ("face", -28.0, 22.0)),
        ("M", 34.0, 0.06, ("face", -10.0, 18.0)),
        ("M", 28.0, 0.02, ("face", 6.0, 26.0)),
        ("S", 16.0, 0.10, ("face", -4.0, 15.0)))}}                   #   the long thin wisp falling between the brows (on top)
CLUMP_ROOT = {m_: v_["root"] for m_, v_ in HAIR_MASSES.items()}
HAIR_KIND_W = {m_: 1.0 for m_ in HAIR_MASSES}   # (widths are set per lock in HAIR_MASSES)
HAIR_LAYER = {m_: v_["layer"] for m_, v_ in HAIR_MASSES.items()}   # stack order offset (m)
HAIR_LIFT = {m_: v_["lift"][0] for m_, v_ in HAIR_MASSES.items()}   # "hair volume" lift (m)
HAIR_LIFT_RAMP = (0.55, 1.0, 1.0)     # the lift per control point after the root (share of the mass lift): the volume
                                      #   swells in off the part instead of stepping up at the first point
HAIR_BONES = 3                        # follow-through bones per chain (Wren's)
CLUMP_STACK = 0.0004
CLUMP_W_VARY = 0.14
CLUMP_WRAP = 1.0
CLUMP_TOP_THIN = (55.0, 80.0, 0.30)
CLUMP_ROOT_GROW = 0.40
CLUMP_SWAY = 0.16                     # "tousled S-curve": sideways sway amplitude (x the lock width); Wren 0.14
CLUMP_SCALP_T = 0.62
CLUMP_S_TIP_K = {"L": 1.0, "M": 1.0, "S": 0.9}
RIBBON_STATIONS_KIND = {"back": 14, "sidel": 14, "sider": 14}   # v5 (v3 rule): the half-hidden layers near the style
                                      #   guide's floor (pays for the coverage locks)
RIBBON_STATIONS = {"L": 16, "M": 15, "S": 13}   # "lock segments": the style guide's 13-19 sections (v1 11-15: under it)
RIBBON_DENSE = 64
RIBBON_FAIR = (10, 0.50, -0.53)
RIBBON_PUSH_SMOOTH = 3.0
RIBBON_ROOT_RAMP = 0.12
RIBBON_BEND = (0.45, 60)
RIBBON_CURV_K = 0.35
RIBBON_TIP_DENSE = 1.35
RIBBON_TOP = (0.55, 0.80)
RIBBON_UNDER = 0.45
RIBBON_TAPER = (1.15, 0.03)
RIBBON_BELLY = 0.05
RIBBON_FRAME_SMOOTH = 3.0
RIBBON_RADIAL = 0.35
RIBBON_RADIAL_AXIS = False            # (v6: the section frames lean toward the head-centre radial; True = the horizontal radial
                                      #   about the vertical axis -- the beard's hanging clumps only)
RIBBON_TIER = {"L": (0.058, 0.0036, 0.40), "M": (0.044, 0.0032, 0.40), "S": (0.024, 0.0026, 0.70)}
LAYER_GAP = 0.0006
LAYER_ROOT = 0.10
LAYER_NEAR = 0.005
LAYER_WINDOW = 0.030
LAYER_LIFT_MAX = 0.009
LAYER_SMOOTH = 1.3
LAYER_ITERS = 4                       # (Wren's rounds)
RIBBON_RING_JITTER = 0.6
RIBBON_TUCK = (0.6, 3, 0.012)
HAIR_TIERS = (0.16, 0.90)             # "painted hair tiers": root below / tip above these arc fractions
ANGEL_RING = (43.0, 50.0)             # "angel ring" elevations (deg) on the head's ellipsoid
HAIR_CREVICE_MAX_EL = 50.0            # tuck shade only below this elevation (the lit crown keeps the ring)
HAIR_PROXY = (48, 24, 80, 0.003)      # "one-volume hair shading" proxy (lon x lat, smoothing passes, pad m) -- built per
                                      #   GROUP: the scalp hairdo and the beard + mustache each get their own smooth egg
HAIR_LOCK_NORMAL_MIX = 0.45
HAIR_SHELL_NORMAL_MIX = 0.85          # "beard shell normals": the beard / mustache shells' share of their OWN smooth normal (vs
                                      #   the per-group proxy egg; Varden v4's measured value); the scalp locks keep HAIR_LOCK_NORMAL_MIX
HAIR_NORMAL_CARRIER = "vertex"        # "hair normal carrier" (v4; research H5, the Wren pilot f1dcc74 = house standard): the
                                      #   proxy-leaned hair normals ship as CUSTOM SPLIT VERTEX NORMALS on the hair faces (glTF
                                      #   NORMAL; contract: conquest_smooth_regions = the hair family) -- "map" = the v3 tangent-map bake
HAIR_CLEAR = (0.0025, 0.0005)
HAIR_BAKE_CAGE = (0.0004, 0.0012)
NEAREST_TIE = 1e-6
# ---- glasses (thin geometry, rigid on the head)
GLASSES = {"rim_margin": 0.0040, "rim_min_r": 0.0175, "wire_r": 0.0010, "front_off": 0.0105, "tilt_deg": 7.0,
           "rim_seg": 28, "bridge_rise": 0.0045, "temple_clear": 0.0030, "temple_back": 0.105, "temple_drop": 0.012,
           "lens": None}              # "glasses": rim = the eye opening + margin (min radius), wire radius, rims stand this
                                      #   far in front of the cornea apex, pantoscopic tilt, the temples run back over the
                                      #   ears (clearance, length behind the rim, drop at the ear); "lens": None = open rims
                                      #   (no glass: an artist question)
# ---- boots (solid, brown, gold trim)
BOOT_MARGIN = (0.010, 0.012)
TOE_EXT = 0.022
BOOT_CLEAR = (0.011, 0.016)
BOOT_TOP = 0.22                       # "boot height": the shaft reaches this far down the shin from the knee (knee 0 .. ankle 1)
CUFF = (0.050, 0.014, 0.018, 0.0045)  # "boot cuff" (gold-trimmed fold): height, clearance, flare, thickness
BOOT_TRIM = 0.010                     # "gold trim": the band at the cuff's top edge (m)
# ---- painted body garments
SLEEVE_T = 0.40                       # "rolled sleeve": the robe sleeve ends this far down the forearm (elbow 0 .. wrist 1)
SLEEVE_ROLL = (0.060, 0.022, 0.008)   # "BIG rolled sleeves": height, roll thickness, clearance (m)
VNECK = (0.060, 0.90)                 # robe neckline: V depth below the neck base (m), rise per m of |x|
NECK_DZ = 0.010
ARMHOLE_IN, ARMHOLE_TILT = 0.030, 0.25
# ---- robe skirt (cream, below the belt) + belt + pouches + scroll tubes
ROBE_LEN = (0.46, 0.50)               # "under-robe length" below the belt: front / back (m)
ROBE_CLEAR, ROBE_FLARE, ROBE_T = 0.010, 0.060, 0.0035
ROBE_NU, ROBE_NV = 30, 7
BELT = {"h": 0.064, "clear": 0.005, "t": 0.006, "dz": -0.010}   # "wide belt": height, clearance over the robe, thickness,
                                      #   offset of its centre from the waist line (m)
BUCKLE = (0.030, 0.036, 0.006)        # gold buckle: half width / half height / depth (m)
POUCHES = ((34.0, (0.032, 0.020, 0.038)), (-30.0, (0.026, 0.017, 0.032)))   # belt pouches: (azimuth from the front deg,
                                      #   + his left; half sizes across / depth / height)
SCROLLS = ((-46.0, 16.0, 0.25, 0.0130), (-56.0, -6.0, 0.21, 0.0112), (-40.0, 30.0, 0.19, 0.0102))   # "scroll tubes"
                                      #   tucked in the belt: (azimuth deg, tilt deg, length m, radius m)
# ---- cravat + brooch
CRAVAT = {"z": 0.012, "band_r": 0.0060, "band_flat": 1.8, "knot": (0.017, 0.011, 0.014), "fall": (0.019, 0.008, 0.026),
          "fall_drop": 0.034}         # "blue cravat": the band round the neck (radius, flattening), the knot (half sizes),
                                      #   the puffed fall under it (half sizes) and how far below the knot it hangs
BROOCH = {"r": 0.020, "drop": 0.115, "gem": (0.0075, 0.0060), "crest_h": 0.030}   # "trident brooch": plate radius, drop
                                      #   below the neck base, teal gem (radius, height), crest size (m)
# ---- mantle (navy, sleeveless, open front) + capelet + trim + crest
MANTLE_FRONT = ((0.0, 150.0), (0.30, 128.0), (1.0, 117.0))   # "mantle opening": (v down the mantle, front-edge azimuth
                                      #   from the back centre deg) -- close at the chest, falling open at the sides below
                                      #   (the robe, belt, pouches and scroll tubes show in front)
MANTLE_TOP = (0.006, 0.040, -0.075)   # top edge vs the neck base (back) / above the shoulder joints / at the front ends (m)
MANTLE_HEM_Z = 0.300                  # "mantle length": hem height (m; mid-calf)
MANTLE_TEAR = (0.045, 0.016)          # "ragged-ish hem": longest point, typical tooth (m)
MANTLE_CLEAR, MANTLE_FLARE = 0.014, 0.075
MANTLE_NU, MANTLE_NV = 32, 18
MANTLE_T = 0.0050
MANTLE_FOLD, MANTLE_FOLDS = 0.012, 8.0
MANTLE_TRIM = (0.034, 0.028)          # "gold trim": band height at the hem / width along the front edges (m)
MANTLE_MOTIFS = (14, 0.018, 0.060)    # "gold diamond motifs": count round the hem, half size (m), height above the hem (m)
CAPELET = {"front": 166.0, "drop": 0.215, "clear": 0.012, "flare": 0.035, "nu": 40, "nv": 8, "t": 0.0045,
           "scallop": (0.016, 13.0), "trim": 0.024}   # "capelet": front edge azimuth, length below the shoulder top,
                                      #   clearance, flare, grid, thickness, scalloped hem (depth m, scallops), gold band (m)
CREST = {"z_below_capelet": 0.135, "h": 0.190, "w": 0.0052}   # the trident on the mantle back: centre below the capelet
                                      #   hem, height, stroke half width (m)
# ---- satchel (worn on the back) + strap
SATCHEL = {"phi": 34.0, "z_off": -0.030, "size": (0.120, 0.042, 0.100), "strap_w": 0.024, "strap_t": 0.0028,
           "flap": 0.62, "scrolls": ((-0.05, 0.11, 0.0105, 10.0), (-0.015, 0.13, 0.0095, -6.0))}   # "satchel": azimuth from the
                                      #   BACK centre toward his left (deg), height vs the hip joint, half sizes, strap width /
                                      #   thickness, flap share of the height, scrolls poking out (x off, length, radius, tilt)
# ---- bracers (both forearms, dark)
BRACER = {"t": (0.04, 0.52), "clear": 0.0045, "t_leather": 0.0038, "nu": 14, "nv": 6, "trim_w": 0.006}
# ---- staff (own object + bone 'staff', child of hand_r)
STAFF = {"len": 1.86, "shaft_r": (0.0165, 0.0135), "head_h": 0.235, "ferrule": (0.0, 0.065), "collar": 0.045,
         "brass_t": 0.0022, "rings": 16, "cup_h": 0.045, "sphere_r": 0.082, "ring_r": 0.0034, "ring_seg": (24, 4),
         "ecliptic_deg": 23.5, "finial": 0.018, "finial_r": 0.0085, "orb_r": 0.038, "grip_at": 0.70}   # "staff": length
                                      #   (sheet: taller than him), shaft radii, the head (cup + armillary + finial) height,
                                      #   ferrule span, collar, the armillary radius / ring thickness, the teal orb radius,
                                      #   the hand grips at grip_at x len (v7 0.62 -> 0.70: the sheet's fist sits high, near
                                      #   the shoulder; 1.30 m keeps the elbow bend skinnable)
STAFF_REST_OFF = (-0.060, 0.010)      # rest pose: the staff stands upright this far (x, y m) from the right hand's grip point
STAFF_ELBOW_POLES = ((-0.55, 0.15, -0.80), (-0.30, 0.55, -0.80), (-0.75, -0.15, -0.65), (-0.15, 0.85, -0.50))   # the
                                      #   staff-hold elbow directions tried (out, back, down) with the roll search
# ---- v7 "STAFF HOLD" (review-log 2026-10-04 "he is not holding his staff"): the sheet's front-view hold BAKED INTO THE BIND
# POSE (no clips: the glb's default pose is what the game shows) -- the staff planted upright beside his right foot, the
# right arm reaching it by the analytic two-bone IK, the fist closed round the shaft
STAFF_HOLD = {"out": -0.150, "fwd": -0.120}   # "staff place": the planted staff's axis vs the right shoulder joint (x m, -
                                      #   = out to his right; y m, - = forward)
HOLD_TWIST_SHARE = 0.5                # "forearm twist share": the forearm takes this share of the grip's twist about its own
                                      #   axis (the rest stays at the wrist; 0 = all at the wrist)
HOLD_CURL = ((58.0, 72.0, 50.0), "wrap")   # "grip fingers": finger curl per joint (knuckle, middle, tip; deg; Wren's
                                      #   HAND_POSES "grip") + the thumb: "wrap" = solved in s7 (closed over the fingers round
                                      #   the shaft), or Wren's 2- / 5-value thumb tuple (Wren's "grip" (22, 30) left the thumb
                                      #   standing 53 mm off the shaft)
HAND_DECIMATE = {"L": 0.40, "R": 0.60}   # "hand detail" (v7.1 budget, Varden's glove pattern): each hand's interior vertices
                                      #   collapse-decimated to this share of that hand's tris (the open left hand takes more;
                                      #   the gripping right keeps its knuckle / finger silhouette); None = off (v7)
# ---- book (own object + bone 'book', child of hand_l)
BOOK = {"size": (0.165, 0.230, 0.048), "cover_t": 0.0045, "overhang": 0.003, "spine_bulge": 0.009, "crest_h": 0.120,
        "crest_w": 0.0028, "strap_w": 0.020}   # "tome": width / height / thickness, board thickness, overhang, spine bulge,
                                      #   crest size + stroke half width, clasp strap width (m)
BOOK_HOLD = (0.020, 0.040, 0.004)     # rest: the spine edge sits this far past the knuckles toward the fingertips, the book
                                      #   hangs this far below them, and stands this far off the palm (m)
# ---- bake + material
BAKE_RES = (2048, 1024)
CUT_SNAP = 0.12
SLIVER_AREA = 5e-8
AO_SAMPLES = 48
AO_FLOOR = {"default": 0.42, "skin": 0.62, "hair": 0.82, "cloth": 0.50}
AO_FACE_LIFT = None
AO_FACE_FLOOR = (1.0, 3.0, (0.0, 26.0, 100.0), (0.031, 0.013, 0.028))
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow", "face_line", "beard_fade1", "beard_fade2", "beard_fade3"],
                    "hair": ["hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice", "beard_inner",
                             "hair_beard", "hair_beard_shade", "hair_beard_root", "hair_beard_tip", "hair_beard_crevice"],
                    "cloth": ["mantle", "mantle_lining", "capelet", "robe", "robe_shade", "trousers", "cravat"]}

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = None
WANT_GLB = "--glb" in argv
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
SCRATCH = argv[argv.index("--scratch") + 1] if "--scratch" in argv else None
_OUT_ROOT = SCRATCH or ROOT
OUT_IMPROVED = os.path.join(_OUT_ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(_OUT_ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(_OUT_ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(_OUT_ROOT, "improved", "textures")
TAG = "scratch" if SCRATCH else "main"
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "version": "v7 (defect fix: the staff HELD in the bind pose; belt / mantle no longer tented over the hands; trunk-only weight sources; v6 beard + v5 scalp unchanged)",
          "name_status": "named by the sheet (PROFESSOR ELIAS - ROYAL RESEARCHER)",
          "source": "none: the artist's inline three-view sheet, transcribed in design/review-log.md 2026-10-02", "tier": "hero",
          "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}
SECTIONS = ["elias_s1_body.py", "elias_s2_regions.py", "elias_s3_outfit.py", "elias_s4_mantle.py", "elias_s5_hair.py",
            "elias_s6_assemble.py", "elias_s7_rig.py"]
for sec_ in SECTIONS:
    _p = os.path.join(HERE, sec_)
    print("SECTION", sec_, round(time.time() - T0, 1)); sys.stdout.flush()
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
