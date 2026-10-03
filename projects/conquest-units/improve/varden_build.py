"""General Varden -- KINGDOM GENERAL, hero tier (Conquest roster), v1 DRAFT (lane-conventions "Two speeds": one clean
headless build + probes-as-sanity + one comparison sheet; no gate wall, no determinism twin, NO clips this pass).

    blender --background --factory-startup --python improve/varden_build.py -- \
        [--preview <out.blend>]          (body + outfit + hair + props + regions + palette only: no bake, no rig)
        [--stop-after <section>]         (exploration: save <scratch or renders/varden/logs>/varden_stage.blend after it)
        [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)
        [--no-glb]                       (skip rigged/varden.glb; the glb is ON by default, through export_glb._GLOW)

Spec (binding): design/review-log.md 2026-10-02 "NEW UNIT: General Varden"; sheet design/reference/varden/varden_sheet.webp
(every tone in palettes/varden/default.json is pixel-sampled from it). 48+ kingdom general: tousled grey-brown hair swept
back, SHORT-CROPPED full beard + mustache with grey streaks, heavy brow, lined stern scarred face; huge navy cloak with a
dark FUR MANTLE, gold hem trim + emblem motifs, the angular gold trident-crest emblem large on the back, round gold radial
BROOCH + chain at the collar; high-collar navy tunic (gold-trimmed collar), cream rolled under-sleeves, dark gloves with
gold-trimmed VAMBRACES, white tabard panel, wide brown BELT + cross-body sword strap (buckles, studs), dark trousers,
heavy brown buckled BOOTS with gold-edged cuffs; LONGSWORD at the left hip (gold cruciform guard + pommel, dark wrapped
grip, dark scabbard with gold chape). Palette chips: navy, dark brown, grey-taupe, cream, gold, steel teal.
House style: design/character-style-guide.md. Reference implementation: the ELIAS pipeline (improve/elias_*, read-only),
itself the Wren stack: s1 verbatim; s2 garments + scars re-cut; s3 the Varden outfit; s4 the cloak + FUR MANTLE (a new
garment class: fur clumps, not cloth) + brooch + strap; s5 the ribbon-lock stack with Varden's swept-back hair + short
beard; s6 the sword / scabbard objects + bake; s7 rig + prop bones + grip record + roll search + save + glb (_GLOW).
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
import varden_parts as VP      # noqa: E402

# =========================================================================== TUNABLE CONSTANTS (artist-facing names)
UNIT = "varden"
CHAR_ID = "varden"
TRI_BUDGET = [30000, 50000]           # declared tier: HERO (quality-tier law: hero = 30-50k)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- body (MPFB2). MakeHuman age macro: 0.5 = 25 yr, 1.0 = 90 yr -> 48 yr = 0.5 + 23/65 x 0.5 = 0.677
MACRO = {"gender": 1.0, "age": 0.677, "muscle": 0.72, "weight": 0.62, "proportions": 0.75, "height": 0.6,
         "cupsize": 0.5, "firmness": 0.5}                                  # "age 48+", a broad battle-hardened build
RACE = {"caucasian": 0.86, "asian": 0.06, "african": 0.08}
TARGETS = {                           # "face dials": the house-style anime base (Wren's eye / mouth dials) + HARD / SQUARE / stern
    "eyes/l-eye-scale-incr": 1.00, "eyes/r-eye-scale-incr": 1.00,          # "eye size": the dial's max (then EYE_SCALE)
    "eyes/l-eye-height2-incr": 0.20, "eyes/r-eye-height2-incr": 0.20,      # "eye opening": narrower than Elias (a hard stare)
    "head/head-scale-vert-incr": 0.90, "head/head-scale-horiz-incr": 0.80, "head/head-scale-depth-incr": 0.75,   # "head size":
                                      #   the house 5.5-6 heads read (0.50 measured 6.35 heads at 1.80 m)
    "eyes/l-eye-bag-decr": 1.00, "eyes/r-eye-bag-decr": 1.00,              # no bags (the flat under-eye law; 48+ is NOT sagging)
    "head/head-square": 0.45,                                              # "head shape": SQUARER than Elias (sheet: harder)
    "chin/chin-width-incr": 0.30, "chin/chin-bones-incr": 0.30,            # "jaw": a broad square jaw (the short beard shows it)
    "chin/chin-prominent-incr": 0.15,
    "cheek/l-cheek-bones-incr": 0.30, "cheek/r-cheek-bones-incr": 0.30,    # "cheekbones": hard planes
    "cheek/l-cheek-volume-decr": 0.30, "cheek/r-cheek-volume-decr": 0.30,  # "cheeks": lean, battle-worn
    "nose/nose-scale-horiz-decr": 0.15, "nose/nose-point-width-decr": 0.30,   # "nose size": a strong nose, still stylised
    "nose/nose-flaring-decr": 0.30, "nose/nose-nostrils-width-decr": 0.30,
    "eyebrows/eyebrows-trans-down": 0.35, "eyebrows/eyebrows-angle-down": 0.30,   # "HEAVY BROW": low, inner ends down (stern)
    "forehead/forehead-trans-forward": 0.25,                               # "brow ridge": the forehead over the eyes forward
    "neck/neck-scale-horiz-incr": 0.35, "neck/neck-scale-depth-incr": 0.25,   # "neck": a soldier's thick neck
    "mouth/mouth-scale-horiz-decr": 0.90,                                  # "MOUTH WIDTH": seam -> ~0.70 x eye spacing (house 0.65-0.73)
    "mouth/mouth-trans-up": 0.48,                                          # "MOUTH HEIGHT": toward the Ashe v_ratio 0.29
    "mouth/mouth-lowerlip-volume-incr": 0.40, "mouth/mouth-upperlip-volume-decr": 0.40,
    "mouth/mouth-angles-up": 0.30,                                         # "mouth corners": near level, a stern set (gruff)
    "expression/units/caucasian/eye-left-slit": 0.25, "expression/units/caucasian/eye-right-slit": 0.25,   # "hard lids"
}
FRAME_TARGETS = {                     # v2 "TALLER + a BROADER / WIDER FRAME, WIDER SHOULDERS" (artist, 2026-10-03): BODY-ONLY
                                      #   dials (torso / arms / legs / neck targets never touch the head mesh: the v1 face and
                                      #   hair are not in question); the macros stay v1's (they reshape the whole body, face too)
    "torso/measure-shoulder-dist-incr": 1.00,                              # "shoulder width": the clavicle / acromion span (v3:
                                                                           #   the dial's max -- weights above 1.0 clamp)
    "torso/torso-scale-horiz-incr": 0.80, "torso/torso-vshape-incr": 0.85,   # "broad chest / back", tapering to the waist (v3)
    "torso/torso-scale-depth-incr": 0.35, "torso/torso-muscle-dorsi-incr": 0.75, "torso/torso-muscle-pectoral-incr": 0.45,
    "torso/torso-scale-vert-incr": 0.25,                                   # "taller": a longer torso
    "arms/l-upperarm-shoulder-muscle-incr": 0.80, "arms/r-upperarm-shoulder-muscle-incr": 0.80,   # heavy deltoids (v3)
    "arms/l-upperarm-muscle-incr": 0.40, "arms/r-upperarm-muscle-incr": 0.40,
    "legs/upperlegs-height-incr": 0.55, "legs/lowerlegs-height-incr": 0.50,   # "taller": longer legs
    "legs/l-upperleg-muscle-incr": 0.30, "legs/r-upperleg-muscle-incr": 0.30,
    "neck/measure-neck-circ-incr": 0.55,                                   # the thicker neck to match the frame (v3)
}
SHOULDER_WIDEN = (0.015, 0.55)        # v3 "wider shoulders still": past the exhausted dial, each arm chain moved this far out (m
                                      #   per side) by its skin weight + this share of its clavicle weight (s1); (0, 0) = off
BODY_SCALE = 0.95203                  # v2: the MPFB -> metres scale FIXED at v1's value (the v1 head size kept); the frame dials
                                      #   above set the new height. None = scale to BODY_H (v1 behaviour)
TARGETS_EDIT = None                   # (s1 probe hook kept from Wren; None = TARGETS as listed)
BODY_H = 1.78                         # "height" barefoot (m); the boot soles add SOLE_T
REST_ARM_DOWN = 22.0                  # "rest arm drop": the MPFB A-pose arms lowered this much (deg) in the bind pose
REST_ELBOW_OPEN = 24.0                # the MPFB rest elbow opened this much (deg) in the bind pose
SOLE_T = 0.030                        # "boot sole": heavy boots -- the foot sits this far above the floor inside the boot
# ---- face (house style; values = Wren's / Elias's settled ones unless noted)
LIP_SEAL = (0.009, 0.0003, 0.0025)
LIP_FLAT = (0.026, 0.0088, 0.011, 0.005, 0.0, 0.0012)
LIP_PROFILE = "bridge"
LIP_BRIDGE = (12.0, 26.0, 13.0, 26.0, 4.0, 5.0, 2.0, 4.0, 2.0)
LIP_HIDDEN_K = (0.0, 0.15, 0.002, 0.0001)
LIP_RIM_STEP = 0.0001
LIP_RIM_TUCK = (0.0002, 0.0010, 0.85)
LIP_RIM_TUCK_SIDES = (1.0,)
LIP_FRONT_TOL = 0.003
EYE_SCALE = 1.22                      # "eye size (geometric)": socket + eyeball x1.22 (Wren 1.30: a harder, smaller eye)
EYE_SCALE_ZONE = (18.0, 30.0, 1.0, 2.2)
EYE_ORBIT = {"outer": (19.0, 29.0), "up": (11.0, 21.0), "inner": (11.0, 18.5), "down": (11.5, 21.0)}
EYE_SLIDE = (1.0, 3.0)
EYE_ORBIT_DEPTH = (0.4, 1.1, 0.0, 0.6)
EYE_BAG_FILL = (0.25, 12.0, 24.0, 4.0, 2.5, 2)   # "eye bags: GONE" (flat under-eye, the style law)
EYE_BAG_FLAT = None
EYE_IRIS_DEG, EYE_PUPIL_DEG = 28.0, 10.0          # "iris size" (house 55-65 % of the opening)
EYE_SEG = 30
EYE_BACK = 110.0
IRIS_SHADE = 0.34
EYE_HILITE = (32.0, 0.34, 3.6)
EYE_HILITE_RING = 2.0
LINER_W = (0.0013, 0.0003)            # lower liner 0.3 mm
LINER_WING = (0.0007, 20.0, 10.0)
LASH_PROFILE = ((0.0, 2.2), (30.0, 2.4), (90.0, 2.0), (150.0, 1.1), (180.0, 0.5))   # "upper lash band" (hard, heavy lid line)
LASH_WING = (1.4, 10.0, 2.0)          # "lash wing": short flick (a man's eye)
BROW_PTS = ((-1.25, 0.0042), (-0.50, 0.0064), (0.45, 0.0078), (1.30, 0.0066))   # "brows": STERN -- the inner ends low and
                                      #   pulled down toward the nose, a flat hard arc (x in eye half widths, z above the opening)
BROW_W = (0.0135, 0.0082)             # "HEAVY brows" (sheet): inner / tail width (m); Elias 12.5 / 7.5, Wren 8.6 / 4.0
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
MOUTH_SMIRK = None                    # "smirk": none (stern); an artist question
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
# ---- face: the 48+ battle-worn read (PAINT, not geometry: "features are paint on simple smooth geometry")
FACE_LINES = {"crow": (2, 0.0040, 0.0007, 0.0035), "brow_furrow": (2, 0.022, 0.0008, 0.0),
              "frown": (2, 0.0110, 0.0007, 0.0060)}   # "lined stern face": painted lines -- crow's feet (count, length m,
                                      #   width m, gap past the outer corner m), forehead lines (count, half length m, width
                                      #   m), the glabella FROWN lines between the brows (count, length m, width m, half
                                      #   spacing m); None = none. Lined but NOT sagging (no bags, no jowls).
FOREHEAD_LINES_Z = (0.034, 0.043)     # the forehead lines' heights above the eye centres (m)
FACE_SCARS = (                        # "SCARRED" (sheet head panel): authored scars as PAINTED shadow-shape work -- each a pale
                                      #   healed line (scar) with its shadow edge just below it (scar_shadow): (side 'L' = his
                                      #   left / 'R' = his right, polyline points (x m OUTWARD from the eye centre, z m from the
                                      #   eye centre), width m). Placement / count = an artist question.
    ("R", ((0.006, -0.016), (0.019, -0.030), (0.028, -0.043)), 0.0013),   # across his right cheek under the eye (the sheet's
    ("R", ((0.014, -0.012), (0.024, -0.021)), 0.0010),                    #   two diagonal cheek nicks) + a short parallel one
    ("L", ((-0.004, 0.024), (0.004, 0.012)), 0.0011),                     # a nick through his left brow (the sheet's brow cut)
)
SCAR_SHADOW = (0.0009, 0.6)           # the scar's shadow edge: offset below the scar line (m), width x the scar width
# ---- the under-beard paint + the SHORT-CROPPED beard / mustache (ribbon locks, the hair stack -- shorter layers)
BEARD_ZONE = {"sideburn_z": -0.022, "cheek": ((0.024, -0.054), (0.040, -0.049), (0.054, -0.040), (0.066, -0.026)),
              "lip_band": (0.0040, 0.0055, 0.0045), "neck_drop": 0.030}   # "beard coverage" on the skin: the top edge
                                      #   (|x| m from the midline, z m below the eye centres) from the sideburn (joins the hair)
                                      #   down the cheek line to the mustache -- HIGHER on the cheek than Elias (the sheet's
                                      #   full beard); the lip band kept skin; under the jaw down this far below the chin
BEARD_LEN = 0.020                     # "beard length": SHORT-CROPPED -- the trimmed edge falls this far below the chin (m)
BEARD_TIP_W = 0.046                   # "beard width at the bottom": the outer locks' tips at most this far off the midline
BEARD_POINT = 0.30                    # "beard shape": squared trimmed edge (0 = straight down, 1 = one point; Elias 0.78)
BEARD_FWD = 0.003                     # the tips stand this far in front of the jaw / throat (m): dense, close-cropped
BEARD_CORE_W = 0.056                  # "beard core": the dark inner mass's half width at the jaw line (m)
BEARD_CORE_OFF = 0.0035               # the core stands this far outside the throat envelope (m; under the locks)
BEARD_LOCKS = (                       # SHORT-CROPPED groomed masses of narrow ribbon locks with real cross-section, three short
                                      #   layered lengths, bottom -> top (the layer resolve's stack): (layer kind, tier, root
                                      #   azimuth deg from the front (+ his left), root height = 'eye' / 'nose' / 'slit' / 'chin'
                                      #   landmark + offset m, or 'edge' = the under-beard zone's top edge + offset m (roots
                                      #   cover the painted zone: no grey mask), length share of BEARD_LEN, past the jaw line)
    ("beard_in", "S", 0.0, ("chin", 0.006), 0.70), ("beard_in", "S", 18.0, ("slit", -0.016), 0.66),
    ("beard_in", "S", -18.0, ("slit", -0.016), 0.66), ("beard_in", "S", 36.0, ("slit", -0.010), 0.60),
    ("beard_in", "S", -36.0, ("slit", -0.010), 0.60), ("beard_in", "S", 54.0, ("edge", -0.014), 0.50),
    ("beard_in", "S", -54.0, ("edge", -0.014), 0.50),
    ("beard", "M", 8.0, ("slit", -0.012), 0.95), ("beard", "M", -8.0, ("slit", -0.012), 0.95),
    ("beard", "M", 25.0, ("slit", -0.004), 0.90), ("beard", "M", -25.0, ("slit", -0.004), 0.90),
    ("beard", "M", 42.0, ("edge", -0.004), 0.82), ("beard", "M", -42.0, ("edge", -0.004), 0.82),
    ("beard", "M", 58.0, ("edge", -0.003), 0.70), ("beard", "M", -58.0, ("edge", -0.003), 0.70),
    ("beard", "M", 72.0, ("edge", -0.003), 0.55), ("beard", "M", -72.0, ("edge", -0.003), 0.55),
    ("beard_top", "M", 0.0, ("chin", 0.014), 1.00), ("beard_top", "S", 15.0, ("slit", -0.007), 0.96),
    ("beard_top", "S", -15.0, ("slit", -0.007), 0.96), ("beard_top", "S", 34.0, ("edge", -0.001), 0.86),
    ("beard_top", "S", -34.0, ("edge", -0.001), 0.86),
    ("beard_chin", "S", 0.0, ("slit", -0.0065), 0.80), ("beard_chin", "S", 9.0, ("slit", -0.0070), 0.76),   # the chin under
    ("beard_chin", "S", -9.0, ("slit", -0.0070), 0.76),                   #   the lip (the Elias v3 coverage lesson)
    ("beard_cheek", "S", 25.0, ("slit", 0.0055), 0.80), ("beard_cheek", "S", -25.0, ("slit", 0.0055), 0.80))   # beside the
                                                                          #   mustache ends (no painted zone showing)
BEARD_T = {"beard_in": 0.0032, "beard": 0.0032, "beard_top": 0.0030, "beard_chin": 0.0026, "beard_cheek": 0.0026}   # "beard
                                      #   lock thickness": half-thickness (m) -- real clumps, not flat panels (Elias v2 lesson)
BEARD_ROOT_K = 0.80                   # "beard root width" (x the lock width): roots along a LINE start near full width (Elias v3)
BEARD_EDGE_LIFT = 0.004               # 'edge' roots sit this far ABOVE the zone's top edge (on skin): the lock body hides the
                                      #   painted edge, never starts on it (Elias v3)
BEARD_JAW_RISE = 0.040                # "trimmed jaw line": the cheek locks' tips rise this much (m) toward the ear (short crop)
BEARD_JAW_PSI = (28.0, 85.0)          #   ramping in over this root-azimuth span (deg from the front)
BEARD_ENV_EAR_Y = 0.012               # the beard envelope uses head skin in front of (head centre y - this) only: no ears
BEARD_TIP_TURN = 0.35                 # the tip azimuth = the root azimuth x (1 - this x share): the locks converge toward the chin
BEARD_CHAIN_PSI = 12.0                # beard locks beyond this azimuth ride the beard.L / beard.R chains, the middle beard.C
BEARD_GREY = {"locks": (("beard_top", 0), ("beard_chin", 0), ("beard", 0), ("beard", 1), ("beard_in", 0)),
              "tip_only": (("beard", 4), ("beard", 5), ("beard_top", 3))}   # "grey streaks" (sheet): whole locks painted the
                                      #   grey beard tones (the chin centre) + locks whose TIP tier only greys (the cheeks):
                                      #   (kind, index within the kind). The streak pattern is an artist question.
MUSTACHE = ((0.003, 0.035, 0.22, 0.0115), (0.007, 0.032, 0.15, 0.0092), (0.012, 0.029, 0.08, 0.0068))   # "mustache" per side,
                                      #   bottom -> top: (root |x| m at the lip centre, tip |x| m, tip drop below the mouth line
                                      #   as a share of the seam-to-chin height, width m); runs ALONG the upper lip and turns
                                      #   down into the beard at the corners (trimmed, no walrus droop)
MUSTACHE_T = 0.0024                   # "mustache thickness": half-thickness (m)
MUSTACHE_LIP_CLEAR = 0.0018           # the mustache's lower edge stays this far above the mouth line at the centre (m)
# ---- scalp hair (ribbon locks, the house style; tousled grey-brown SWEPT BACK from the front hairline)
HAIR_INTERIOR_R = 0.70
HAIRLINE = (0.068, -0.050)            # "hairline": above the eye centres at the front / at the nape vs the head joint
HAIR_CAP_T = 0.0050                   # "hair volume" on the scalp
HAIR_CAP_INNER = False                # the cap's scalp-facing shell harvested (provably hidden)
HAIR_ROOT_K = 0.30
HAIR_ROOT_FLAT = ("sweep", "side", "hairline")   # kinds rooted on the hairline: flat root edge (not a root point)
HAIRLINE_FEATHER = (0.022, 0.0022)
CAP_RIM_PAINT = 0.55                  # cap outer faces below this share of the feather (+ the rim strip) take the hair ROOT tone
HAIR_UV_STRIP = 0.16                  # hair + beard faces packed into the right 16 % of the UV square
LOCK_OFF = 0.0020
HAIR_WHORL = (176.0, 60.0)            # "crown whorl" (psi deg from the front toward his left, elevation deg): the BACK and
                                      #   side layers radiate from it (Wren's flow rule); the swept-back top layer is rooted on
                                      #   the front hairline instead (HAIR_SWEEP)
HAIR_LOCKS = (                        # the under layers (Wren's clump stack): (kind, tier, tip (psi, elevation deg, flick m),
                                      #   mirrored L / R?, follow-through chain or None) -- bottom -> top within a kind
    ("back", "L", (180.0, -50.0, 0.018), False, "hair_back"),
    ("back", "M", (156.0, -42.0, 0.026), True, "hair_back"),
    ("back", "L", (180.0, -34.0, 0.024), False, "hair_back"),
    ("outer", "M", (134.0, -24.0, 0.030), True, "hair_side"),
    ("outer", "M", (112.0, -12.0, 0.026), True, "hair_side"),
    ("tousle", "S", (130.0, 72.0, 0.022), False, None),           # "tousled": short flicks lifting off the crown
    ("tousle", "S", (216.0, 66.0, 0.024), False, None))
HAIR_SWEEP = (                        # "SWEPT BACK" (sheet): locks ROOTED along the front hairline / temples and swept back over
                                      #   the head, lifting a little (tousled), layered OVER the under layers: (kind, tier, root
                                      #   (psi, el), tip (psi, el, flick m), mirrored?, chain). Wide roots cover the hairline
                                      #   (no exposed cap band: the Elias v2 / v3 lesson); the tips fall past the crown.
    ("side", "M", (74.0, 8.0), (134.0, -8.0, 0.026), True, "hair_side"),      # temple / sideburn top -> back over the ear
    ("side", "M", (62.0, 24.0), (122.0, 14.0, 0.024), True, "hair_side"),
    ("sweep", "L", (46.0, 32.0), (138.0, 44.0, 0.020), True, "hair_front"),   # the swept top: front corner -> behind the crown
    ("sweep", "L", (30.0, 35.5), (150.0, 56.0, 0.018), True, "hair_front"),   #   (roots ON the hairline: el 32-38, the cap rim
    ("sweep", "L", (14.0, 37.0), (164.0, 62.0, 0.016), True, "hair_front"),   #   at ~38 deg at the front)
    ("sweep", "L", (0.0, 38.0), (180.0, 64.0, 0.016), False, "hair_front"),
    ("top", "M", (22.0, 46.0), (120.0, 72.0, 0.022), True, None),             # the top layer over the crown (angel-ring band)
    ("top", "M", (-6.0, 48.0), (190.0, 74.0, 0.022), False, None))
HAIR_LOOSE = (                        # "tousled loose strands" (sheet head panel: a few strands falling forward over the forehead
                                      #   toward his left): (tier, root (psi, el), tip (psi, el, flick m)), not mirrored
    ("S", (8.0, 47.0), (20.0, 26.0, 0.006)), ("S", (16.0, 46.0), (34.0, 24.0, 0.007)))
HAIRLINE_LOCKS = ()                   # "hairline row" (Elias v3): short flat locks under the swept roots. EMPTY on Varden: the
                                      #   swept locks are ROOTED on the hairline with flat wide roots (root_k 0.95, 14-16 deg
                                      #   apart, 75 mm wide: heavy overlap), so the row only poked its tips out between them as
                                      #   points (measured on the first build); format (tier, root (psi, el), tip (psi, el, flick))
CLUMP_ROOT = {"top": 0.04, "side": 0.10, "outer": 0.12, "back": 0.16, "tousle": 0.03}   # root position along whorl -> tip
                                      #   (Wren's: roots never stack on the whorl point -- the low-crown lesson)
HAIR_KIND_W = {"back": 1.05, "outer": 1.10, "side": 1.20, "sweep": 1.30, "front": 1.30, "top": 1.0, "tousle": 0.80,
               "loose": 0.55, "beard_in": 0.70, "beard": 0.66, "beard_top": 0.60, "beard_chin": 0.80, "beard_cheek": 0.85,
               "must": 1.0, "hairline": 1.55}   # "lock width by kind" (x the tier width)
HAIR_LAYER = {"back": 0.0, "outer": 0.0009, "side": 0.0018, "hairline": 0.0021, "sweep": 0.0027, "top": 0.0032,
              "tousle": 0.0036, "loose": 0.0040, "beard_in": 0.0, "beard": 0.0010, "beard_top": 0.0020, "beard_chin": 0.0026,
              "beard_cheek": 0.0026, "must": 0.0012}   # stack order offset (m)
HAIR_LIFT = {"back": 0.007, "outer": 0.011, "side": 0.010, "sweep": 0.006, "top": 0.006, "hairline": 0.0015, "tousle": 0.010,
             "loose": 0.003}          # "hair volume" lift (m)
HAIR_BONES = 3                        # follow-through bones per chain (Wren's)
CLUMP_STACK = 0.0004
CLUMP_W_VARY = 0.14
CLUMP_WRAP = 1.0
CLUMP_TOP_THIN = (55.0, 80.0, 0.30)
CLUMP_ROOT_GROW = 0.40
CLUMP_SWAY = 0.16                     # "tousled S-curve": sideways sway amplitude (x the lock width); Wren 0.14
CLUMP_SCALP_T = 0.62
CLUMP_S_TIP_K = {"L": 1.0, "M": 1.0, "S": 0.9}
RIBBON_STATIONS_KIND = {"back": 13, "outer": 13, "beard_in": 13}   # the half-hidden layers at the style guide's floor
RIBBON_STATIONS = {"L": 15, "M": 14, "S": 13}   # "lock segments": the style guide's 13-19 sections
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
ANGEL_RING = (62.0, 70.0)             # "angel ring" elevations (deg) on the head's ellipsoid
HAIR_CREVICE_MAX_EL = 60.0            # tuck shade only below this elevation (the lit crown keeps the ring)
HAIR_PROXY = (48, 24, 80, 0.003)      # "one-volume hair shading" proxy (lon x lat, smoothing passes, pad m) -- PER GROUP:
                                      #   the scalp hairdo and the beard + mustache each get their own smooth egg
HAIR_LOCK_NORMAL_MIX = 0.45
HAIR_CLEAR = (0.0025, 0.0005)
HAIR_BAKE_CAGE = (0.0004, 0.0012)
NEAREST_TIE = 1e-6
# ---- boots (heavy, brown, buckled straps, gold-edged cuff)
BOOT_MARGIN = (0.012, 0.014)
TOE_EXT = 0.026
BOOT_CLEAR = (0.013, 0.019)
BOOT_TOP = 0.10                       # "boot height": the shaft reaches this far down the shin from the knee (knee 0 .. ankle 1)
CUFF = (0.060, 0.016, 0.022, 0.0050)  # "boot cuff" (folded, gold-edged): height, clearance, flare, thickness
BOOT_TRIM = 0.008                     # "gold edge": the band at the cuff's top edge (m)
BOOT_STRAPS = ((0.30, 1.0), (0.55, -1.0), (0.80, 1.0))   # "buckled straps" round the shaft: (shin fraction knee 0 .. ankle 1,
                                      #   buckle side +1 outer / -1 inner); sheet: three per boot
BOOT_STRAP = (0.016, 0.0030, 0.0022)  # strap height, thickness, clearance over the shaft (m)
BOOT_BUCKLE = (0.011, 0.012, 0.004)   # buckle half width / half height / depth (m)
# ---- painted body garments
SLEEVE_T = 0.10                       # the cream under-sleeve's painted end down the forearm (elbow 0 .. wrist 1): under the vambrace
VNECK = (0.0, 0.0)                    # the tunic is HIGH-COLLARED: no V (neck cut at the neck base)
NECK_DZ = 0.004
TABARD_CHEST = (0.045, 0.10, 0.28, 0.45)   # the white tabard's painted V on the chest: half width at the top (m), top /
                                      #   bottom below the neck joint (m), width taper to the bottom (x)
GLOVE_DECIMATE = 0.40                 # "glove detail": interior glove vertices collapse-decimated to this ratio (budget; None = off)
GLOVE_T = 0.02                        # "dark gloves": the glove starts this far up the forearm from the wrist (x the forearm)
ARMHOLE_IN, ARMHOLE_TILT = 0.030, 0.25
# ---- tunic skirt (navy, below the belt) + white tabard panel + gold edges + star motifs
SKIRT_LEN = (0.58, 0.58)              # "tunic skirt length" below the belt: front / back (m): to the knee (sheet: ~0.6 m)
SKIRT_CLEAR, SKIRT_FLARE, SKIRT_T = 0.010, 0.070, 0.0035
SKIRT_NU, SKIRT_NV = 32, 7
TABARD = {"half_deg": 15.0, "trim_deg": 3.2, "len_extra": 0.035, "edge_trim": 3.0}   # "white tabard panel": half width in
                                      #   azimuth from the front (deg), gold edge strips (deg), how much longer than the skirt
                                      #   it hangs (m), the navy skirt's front opening edge trim (deg)
SKIRT_HEM_TRIM = 0.024                # gold band along the skirt hem (m)
SKIRT_STARS = ((24.0, 0.15, 0.052),)  # "kingdom-emblem stars" on the skirt front panels: (azimuth deg, height above the hem m,
                                      #   radius m), mirrored
BELT = {"h": 0.068, "clear": 0.005, "t": 0.006, "dz": -0.005}   # "wide belt": height, clearance over the tunic, thickness,
                                      #   offset of its centre from the waist line (m)
BUCKLE = (0.032, 0.038, 0.006)        # square gold buckle: half width / half height / depth (m)
BELT_STUDS = (14, 0.0042)             # "studs": count round the belt, radius (m)
SWORD_BELT = {"tilt_deg": 11.0, "drop": 0.075, "h": 0.044, "t": 0.0055, "clear": 0.004}   # the lower SWORD BELT round the hips,
                                      #   slanting down to his left hip (deg), centre drop below the belt (m), height, thickness
# ---- high collar + vambraces + under-sleeve rolls
COLLAR = {"below": 0.050, "h": 0.004, "back_rise": 0.030, "clear": 0.004, "t": 0.0040, "trim": 0.010, "flare": 0.012,
          "gap_deg": 22.0}            # "high collar": from this far below the neck joint (NECK0 is mid-neck) to this far above
                                      #   it at the front, rising this much more at the back; clearance, thickness, gold trim
                                      #   band at the top edge, flare, front opening (deg each side of the front) -- kept under
                                      #   the jaw (the first build's 52 mm collar reached the chin)
VAMBRACE = {"t": (0.03, 0.78), "clear": 0.0050, "t_leather": 0.0045, "nu": 16, "nv": 7, "trim_w": 0.008, "flare": 0.010}
                                      # "gold-trimmed vambraces": span wrist -> elbow (fractions), clearance, leather thickness,
                                      #   grid, gold rim width, flare at the elbow end (m)
SLEEVE_ROLL = (0.070, 0.026, 0.008)   # the cream under-sleeve bunched over the vambrace top: height, puff, clearance (m)
# ---- cloak (navy, open front, hangs behind the arms) + trim + hem motifs + the back emblem
CLOAK_FRONT = ((0.0, 152.0), (0.30, 132.0), (1.0, 120.0))   # "cloak opening": (v down the cloak, front-edge azimuth from the
                                      #   back centre deg) -- close under the fur at the chest, falling open at the sides
CLOAK_TOP = (0.010, 0.050, -0.060)    # top edge vs the neck base (back) / above the shoulder joints / at the front ends (m)
CLOAK_HEM_Z = 0.240                   # "cloak length": hem height (m; low shin -- sheet: hem ~0.25 m off the floor)
CLOAK_TEAR = (0.030, 0.012)           # hem unevenness: longest point, typical tooth (m) (the sheet's worn hem)
CLOAK_CLEAR, CLOAK_FLARE = 0.016, 0.200   # clearance over what it hangs on, extra flare at the hem (m): a HUGE cloak
CLOAK_NU, CLOAK_NV = 32, 15
CLOAK_T = 0.0055
CLOAK_FOLD, CLOAK_FOLDS = 0.018, 7.0  # "folds": amplitude (m), count across
CLOAK_TRIM = (0.040, 0.024)           # "gold trim": band height at the hem / width along the front edges (m)
CLOAK_MOTIFS = (16, 0.020, 0.066)     # "gold emblem motifs" above the hem band: count, half size, height above the hem (m)
CREST = {"z_below_fur": 0.090, "h": 0.520, "w": 0.0095}   # the trident-crest EMBLEM on the back (sheet: large): top below
                                      #   the fur hem (m), height (m), stroke half width (m)
# ---- the FUR MANTLE (NEW garment class: layered fur clumps / tufts -- creature-principles territory, not cloth)
FUR = {"drop": 0.220, "front": 150.0, "clear": 0.028, "nu": 36, "nv": 6, "base_t": 0.014, "rise": 0.012}   # the fur ROLL
                                      #   (dark under-fur base): length below the shoulder top, front edge azimuth from the back
                                      #   (deg), clearance over the cloak, grid, thickness, how far the collar part stands up
FUR_ROWS = ((0.08, 0.085, 24, 0.0), (0.40, 0.100, 24, 0.5), (0.70, 0.110, 22, 0.0), (0.97, 0.095, 22, 0.5))   # "fur
                                      #   volume": tuft rows from the collar (0) to the hem (1): (row position down the roll,
                                      #   tuft length m, tufts round, phase shift); the hem row makes the shaggy silhouette
FUR_TUFT = {"w": 0.058, "t": 0.0085, "w_vary": 0.30, "len_vary": 0.30, "lift": 0.26, "curl": 0.40, "stations": 5,
            "out": 0.15, "sway": 0.25}   # one tuft: width, half-thickness, width / length jitter, how much it stands off the roll
                                      #   (x length), downward curl, sections, outward lean, sideways sway (x width)
# ---- brooch + chain (his left collar)
BROOCH = {"r": 0.032, "phi_front": 38.0, "drop": 0.055, "ridges": 8, "studs": 10, "boss_h": 0.010, "pendant": 0.060}
                                      # "round gold radial brooch": radius, azimuth from the front (+ his left, deg), drop below
                                      #   the neck base (m), radial ridges, rim studs, centre boss height, pendant drop (m)
CHAIN = {"r": 0.0030, "sag": 0.050, "links": 18, "to_phi": -38.0}   # the chain from the brooch across the chest to the cloak's
                                      #   right corner: wire radius, sag (m), link count, anchor azimuth (deg)
# ---- cross-body sword strap (his right shoulder -> his left hip) + its buckle + studs
STRAP = {"w": 0.040, "t": 0.0032, "studs": 9, "stud_r": 0.0034, "buckle_t": 0.36}   # width, thickness, studs, stud radius,
                                      #   buckle position along the front run (0 shoulder .. 1 hip)
# ---- sword (own object + bone 'sword', sheathed in the scabbard at the LEFT hip) + scabbard (own object + bone 'scabbard')
SWORD = {"blade_len": 0.86, "blade_w": (0.050, 0.020), "blade_t": 0.0065, "ricasso": 0.03, "guard_w": 0.240,
         "guard_t": 0.022, "grip_len": 0.21, "grip_r": (0.0150, 0.0135), "pommel_r": 0.026, "wrap_pitch": 0.020}
                                      # "longsword": blade length / width at the guard -> near the tip / thickness, ricasso,
                                      #   crossguard span / thickness, grip length / radii, pommel radius, cord-wrap pitch (m)
SCABBARD = {"pad": 0.006, "t": 0.0045, "chape": 0.085, "throat": 0.045, "bands": (0.32, 0.62)}   # scabbard clearance over
                                      #   the blade, wall, gold chape length, gold throat length, band fractions down it
SWORD_HANG = {"phi_front": 34.0, "z_hilt": 0.0, "tilt_fwd": 7.0, "tilt_out": -6.0, "off": 0.010}   # "sword at the LEFT
                                      #   hip": azimuth from the front (+ his left, deg), guard drop below the sword belt (m),
                                      #   the hilt leaning forward / out from vertical (deg), stand-off from the skirt (m)
SWORD_TILTS = ((2.0, 5.0, 8.0, 12.0, 16.0), (-14.0, -10.0, -6.0, -2.0, 2.0))   # the hang search: forward / outward hilt tilts
                                      #   tried (deg; outward < 0 = the hilt leans in, the tip out -- the sheet's front view)
SWORD_CLEAR = 0.004                   # the scabbard keeps this much air off the leg / skirt / boots (m) beyond its half width
SWORD_GUARD_POLES = ((-0.55, 0.15, -0.80), (-0.30, 0.55, -0.80), (-0.75, -0.15, -0.65), (-0.15, 0.85, -0.50))   # the
                                      #   guard-hold evaluation's elbow directions tried (out, back, down) with the roll search
SWORD_GUARD = (0.12, -0.40, -0.22, 40.0)  # the evaluated "guard" hold (unkeyed): right-hand grip at (x, y, z) m from the right
                                      #   shoulder, the blade raised this many deg above horizontal, pointing forward
# ---- bake + material
BAKE_RES = (2048, 1024)
CUT_SNAP = 0.12
SLIVER_AREA = 5e-8
AO_SAMPLES = 48
AO_FLOOR = {"default": 0.42, "skin": 0.62, "hair": 0.82, "cloth": 0.50, "fur": 0.70}
AO_FACE_LIFT = None
AO_FACE_FLOOR = (1.0, 3.0, (0.0, 26.0, 100.0), (0.031, 0.013, 0.028))
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow", "face_line", "scar", "scar_shadow"],
                    "hair": ["hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice",
                             "beard_inner", "beard", "beard_shade", "beard_root", "beard_tip", "beard_crevice", "beard_grey",
                             "beard_grey_tip"],
                    "cloth": ["cloak", "cloak_lining", "tunic", "tunic_shade", "tabard", "sleeve", "sleeve_roll", "trousers"],
                    "fur": ["fur", "fur_shade", "fur_tip", "fur_inner"]}

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
STOP_AFTER = argv[argv.index("--stop-after") + 1] if "--stop-after" in argv else None
DIGEST_ONLY = None
WANT_GLB = "--no-glb" not in argv
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
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "version": "v3 draft (shoulders)",
          "name_status": "named by the sheet (GENERAL VARDEN - KINGDOM GENERAL)",
          "source": "design/reference/varden/varden_sheet.webp (saved sheet; transcription design/review-log.md 2026-10-02)",
          "tier": "hero", "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}


def STAGE_DUMP():
    """exploration (--stop-after): the body (cut + painted if s2 ran) + every part so far, flat palette colours per face
    (unknown regions magenta), saved as <scratch or renders/varden/logs>/varden_stage.blend for varden_render.py."""
    pal_ = json.load(open(os.path.join(ROOT, "palettes", UNIT, "default.json")))["regions"]
    srgb_ = lambda c: [((x / 255.0 + 0.055) / 1.055) ** 2.4 if x / 255.0 > 0.04045 else x / 255.0 / 12.92 for x in c]
    bv_, bf_, br_ = (CV, CF, list(reg)) if "reg" in globals() else (BV, BF, ["skin"] * len(BF))
    Vs_, Fs_, Rs_, o_ = [bv_], [list(f) for f in bf_], list(br_), len(bv_)
    for p_ in globals().get("PARTS", []):
        Vs_.append(p_["V"]); Fs_ += [[i + o_ for i in f] for f in p_["F"]]; Rs_ += list(p_["R"]); o_ += len(p_["V"])
    me_ = bpy.data.meshes.new(UNIT + "_stage")
    me_.from_pydata(np.vstack(Vs_).tolist(), [], Fs_)
    me_.update()
    ca_ = me_.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    lt_ = np.empty(len(me_.polygons), dtype=np.int64); me_.polygons.foreach_get("loop_total", lt_)
    cols_ = np.array([srgb_(pal_[r_]["rgb"]) + [1.0] if r_ in pal_ else [1.0, 0.0, 1.0, 1.0] for r_ in Rs_], dtype=np.float32)
    ca_.data.foreach_set("color", np.repeat(cols_, lt_, axis=0).ravel())
    ob_ = bpy.data.objects.new(UNIT + "_stage", me_)
    bpy.context.scene.collection.objects.link(ob_)
    mat_ = bpy.data.materials.new("stage"); mat_.use_nodes = True
    vc_ = mat_.node_tree.nodes.new("ShaderNodeVertexColor"); vc_.layer_name = "Col"
    mat_.node_tree.links.new(vc_.outputs["Color"], mat_.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    me_.materials.append(mat_)
    me_.shade_flat()
    for o in list(bpy.context.scene.objects):
        if o is not ob_:
            bpy.data.objects.remove(o, do_unlink=True)
    out_ = os.path.join(SCRATCH or os.path.join(ROOT, "renders", UNIT, "logs"), UNIT + "_stage.blend")
    os.makedirs(os.path.dirname(out_), exist_ok=True)
    unknown_ = sorted(set(r_ for r_ in Rs_ if r_ not in pal_))
    bpy.ops.wm.save_as_mainfile(filepath=out_, copy=True, compress=True)
    print("STAGE_SAVED", out_, "tris", sum(len(f) - 2 for f in Fs_), "unknown_regions", unknown_, round(time.time() - T0, 1))
    sys.stdout.flush(); os._exit(0)


SECTIONS = ["varden_s1_body.py", "varden_s2_regions.py", "varden_s3_outfit.py", "varden_s4_cloak.py", "varden_s5_hair.py",
            "varden_s6_assemble.py", "varden_s7_rig.py"]
for sec_ in SECTIONS:
    _p = os.path.join(HERE, sec_)
    print("SECTION", sec_, round(time.time() - T0, 1)); sys.stdout.flush()
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
    if STOP_AFTER and sec_.startswith("varden_" + STOP_AFTER):
        STAGE_DUMP()
