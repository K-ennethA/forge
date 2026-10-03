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
+ a beard / mustache built from the same ribbon locks; s6 props + glasses + bake (the hair proxy split scalp / beard);
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
# ---- the under-beard paint + the beard / mustache (ribbon locks, the hair stack)
BEARD_ZONE = {"sideburn_z": -0.008, "cheek": ((0.026, -0.064), (0.042, -0.062), (0.058, -0.052), (0.072, -0.036)),
              "lip_band": (0.0045, 0.0060, 0.0050), "neck_drop": 0.040}   # "beard coverage" on the skin: the top edge
                                      #   (|x| m from the midline, z m below the eye centres) from the sideburn down the
                                      #   cheek to the mustache; the lip band kept skin (m past the seam ends, above / below
                                      #   the seam); under the jaw down this far below the chin
BEARD_LEN = 0.075                     # "beard length": the point falls this far below the chin (m)
BEARD_TIP_W = 0.050                   # "beard width at the bottom": the outer locks' tips at most this far off the midline
BEARD_POINT = 0.78                    # "groomed point": how strongly the tips converge (0 = straight down, 1 = one point)
BEARD_FWD = 0.012                     # the tips stand this far in front of the chest / cravat (m)
BEARD_CORE_W = 0.058                  # "beard core": the dark inner mass's half width at the jaw line (m), narrowing to the point
BEARD_CORE_OFF = 0.0045               # the core stands this far outside the throat / cravat envelope (m; under the locks)
BEARD_LOCKS = (                       # v2 (artist: "match the style we decided on for wren"): GROOMED MASSES of narrow ribbon
                                      #   locks with real cross-section in THREE LAYERED LENGTHS, bottom -> top (the layer
                                      #   resolve's stack): (layer kind, tier, root azimuth deg from the front (+ his left),
                                      #   root height = 'eye' / 'nose' / 'slit' / 'chin' landmark + offset m, or 'edge' = the
                                      #   under-beard zone's top edge + offset m (roots cover the painted zone: no grey mask),
                                      #   length share of BEARD_LEN)
    ("beard_in", "M", 0.0, ("chin", 0.006), 0.62), ("beard_in", "M", 17.0, ("slit", -0.016), 0.60),
    ("beard_in", "M", -17.0, ("slit", -0.016), 0.60), ("beard_in", "M", 34.0, ("slit", -0.010), 0.56),
    ("beard_in", "M", -34.0, ("slit", -0.010), 0.56), ("beard_in", "M", 52.0, ("edge", -0.016), 0.50),
    ("beard_in", "M", -52.0, ("edge", -0.016), 0.50),
    ("beard", "L", 8.0, ("slit", -0.012), 0.86), ("beard", "L", -8.0, ("slit", -0.012), 0.86),
    ("beard", "L", 25.0, ("slit", -0.004), 0.84), ("beard", "L", -25.0, ("slit", -0.004), 0.84),
    ("beard", "L", 42.0, ("edge", -0.004), 0.80), ("beard", "L", -42.0, ("edge", -0.004), 0.80),
    ("beard", "L", 58.0, ("edge", -0.003), 0.74), ("beard", "L", -58.0, ("edge", -0.003), 0.74),
    ("beard", "M", 76.0, ("edge", -0.003), 0.62), ("beard", "M", -76.0, ("edge", -0.003), 0.62),
    ("beard_top", "M", 0.0, ("chin", 0.014), 1.04), ("beard_top", "M", 15.0, ("slit", -0.007), 1.00),
    ("beard_top", "M", -15.0, ("slit", -0.007), 1.00), ("beard_top", "M", 36.0, ("edge", -0.001), 0.92),
    ("beard_top", "M", -36.0, ("edge", -0.001), 0.92),
    ("beard_chin", "S", 0.0, ("slit", -0.0065), 0.80), ("beard_chin", "S", 9.0, ("slit", -0.0070), 0.78),   # v3: the chin
    ("beard_chin", "S", -9.0, ("slit", -0.0070), 0.78),                   #   under the lip (artist arrow 4)
    ("beard_cheek", "S", 25.0, ("slit", 0.0055), 0.82), ("beard_cheek", "S", -25.0, ("slit", 0.0055), 0.82),   # v3: beside
                                                                          #   the mustache ends (arrows 2, 3) ("beard_top", "S", 84.0, ("edge", -0.012), 0.50),
    ("beard_top", "S", -84.0, ("edge", -0.012), 0.50))
BEARD_T = {"beard_in": 0.0050, "beard": 0.0048, "beard_top": 0.0044, "beard_chin": 0.0036, "beard_cheek": 0.0036}   # "beard lock thickness": half-thickness (m) -- a
                                      #   real cross-section (v1 3.2-3.6 mm on 44-55 mm widths read as flat strips)
BEARD_ROOT_K = 0.80                   # v3 "beard root width" (x the lock width): roots along a LINE (the zone edge / lip) start
                                      #   near full width -- Wren's whorl root_k 0.40 left the painted zone showing between
                                      #   narrow roots (artist arrows 2-4); Wren's own along-a-line roots used 0.60
BEARD_EDGE_LIFT = 0.004               # v3: 'edge' roots sit this far ABOVE the zone's top edge (on skin), so the lock body hides the
                                      #   painted edge instead of starting on it (a 2-4 mm grey crescent showed above the cheek roots)
BEARD_CHAIN_PSI = 12.0                # beard locks beyond this azimuth ride the beard.L / beard.R chains, the middle beard.C
MUSTACHE = ((0.003, 0.037, 0.52, 0.0120), (0.007, 0.033, 0.36, 0.0095), (0.012, 0.028, 0.18, 0.0070))   # "mustache" per side, bottom -> top layer:
                                      #   (root |x| m at the lip centre, tip |x| m, tip drop below the mouth line as a share of
                                      #   the seam-to-chin height, width m = the lip height it covers); each runs ALONG the
                                      #   upper lip and droops past the corner (a walrus-style droop is an artist question)
MUSTACHE_T = 0.0026                   # "mustache thickness": half-thickness (m) -- thinner than a beard lock, lies on the lip
MUSTACHE_LIP_CLEAR = 0.0018           # the mustache's lower edge stays this far above the mouth line at the centre (m)
# ---- scalp hair (ribbon locks, the house style; tousled grey, swept back from a receding front hairline)
HAIR_INTERIOR_R = 0.70
HAIRLINE = (0.074, -0.050)            # "hairline": above the eye centres at the front (receding) / at the nape vs the head joint
HAIR_CAP_T = 0.0050                   # "hair volume" on the scalp
HAIR_CAP_INNER = False                # the cap's scalp-facing shell harvested (provably hidden)
HAIR_ROOT_K = 0.30
HAIRLINE_FEATHER = (0.022, 0.0022)
HAIR_UV_STRIP = 0.16                  # hair + beard faces packed into the right 16 % of the UV square
LOCK_OFF = 0.0020
HAIR_WHORL = (174.0, 62.0)            # v2 "crown whorl" (psi deg from the front toward his left, elevation deg): every scalp
                                      #   lock radiates from it along its great circle (Wren's flow rule; the sheet gives no
                                      #   part line, so the tousled hair falls from one slightly off-centre whorl)
HAIR_LOCKS = (                        # v2 (Wren's clump stack): (kind, tier, tip (psi, elevation deg, flick m), mirrored L / R?,
                                      #   follow-through chain or None) -- listed bottom -> top within a kind
    ("back", "L", (180.0, -52.0, 0.020), False, "hair_back"),
    ("back", "M", (158.0, -44.0, 0.032), True, "hair_back"),
    ("back", "L", (180.0, -36.0, 0.030), False, "hair_back"),
    ("outer", "M", (136.0, -24.0, 0.034), True, "hair_side"),
    ("outer", "L", (112.0, -16.0, 0.030), True, "hair_side"),
    ("side", "L", (88.0, -6.0, 0.024), True, "hair_side"),        # over the ear top, flicking out behind it
    ("side", "M", (66.0, 14.0, 0.020), True, "hair_side"),
    ("front", "M", (52.0, 24.0, 0.006), True, "hair_front"),      # the front locks run forward from the whorl over the top and
    ("front", "M", (37.0, 26.0, 0.005), True, "hair_front"),      #   their tips fall just PAST the receding hairline (el ~38 deg)
    ("front", "L", (24.0, 28.0, 0.004), True, "hair_front"),      #   onto the forehead, lifting a little (tousled): overlapping
    ("front", "L", (11.0, 29.0, 0.004), True, "hair_front"),      #   neighbours, so the hairline is covered by lock bodies, never
    ("front", "L", (0.0, 30.0, 0.004), False, "hair_front"),      #   a cap rim (the helmet rule)
    ("top", "L", (-12.0, 52.0, 0.006), False, None),              # the top layer over the crown (the angel-ring band)
    ("top", "L", (14.0, 54.0, 0.006), False, None),
    ("tousle", "S", (130.0, 72.0, 0.020), False, None),           # "tousled": short flicks lifting off the crown
    ("tousle", "S", (210.0, 70.0, 0.022), False, None),
    ("tousle", "S", (32.0, 30.0, 0.010), False, None))            # the one forelock toward his left brow
HAIRLINE_LOCKS = (                    # v3 "hairline row" (artist arrow 1: the dark cap band under the front tips): short locks
                                      #   lying flat on the cap just above the receding hairline, interleaved between the front
                                      #   tips and layered UNDER them: (tier, root (psi, el), tip (psi, el, flick)), mirrored
    ("S", (7.0, 47.0), (7.0, 31.0, 0.002)), ("S", (21.0, 46.0), (21.0, 30.0, 0.002)), ("S", (40.0, 42.0), (42.0, 26.0, 0.003)))
CLUMP_ROOT = {"front": 0.06, "top": 0.04, "side": 0.10, "outer": 0.12, "back": 0.16, "tousle": 0.03}   # root position along
                                      #   whorl -> tip (Wren's: roots never stack on the whorl point -- the low-crown lesson)
HAIR_KIND_W = {"back": 1.05, "outer": 1.10, "side": 1.15, "front": 1.30, "top": 1.0, "tousle": 0.80,
               "beard_in": 0.50, "beard": 0.48, "beard_top": 0.44, "beard_chin": 0.70, "beard_cheek": 0.75, "must": 1.0,
               "hairline": 1.55}   # "lock width by kind" (x the tier width;
                                      #   Wren's scalp values; the beard narrow -> 25-32 mm locks with real thickness)
HAIR_LAYER = {"back": 0.0, "outer": 0.0009, "side": 0.0018, "front": 0.0027, "top": 0.0032, "tousle": 0.0036,
              "beard_in": 0.0, "beard": 0.0010, "beard_top": 0.0020, "beard_chin": 0.0026, "beard_cheek": 0.0026,
              "must": 0.0012, "hairline": 0.0021}   # stack order offset (m)
HAIR_LIFT = {"back": 0.007, "outer": 0.012, "side": 0.016, "front": 0.003, "top": 0.006, "hairline": 0.0015, "tousle": 0.010}   # "hair volume" lift (m)
HAIR_BONES = 3                        # follow-through bones per chain (Wren's)
CLUMP_STACK = 0.0004
CLUMP_W_VARY = 0.14
CLUMP_WRAP = 1.0
CLUMP_TOP_THIN = (55.0, 80.0, 0.30)
CLUMP_ROOT_GROW = 0.40
CLUMP_SWAY = 0.16                     # "tousled S-curve": sideways sway amplitude (x the lock width); Wren 0.14
CLUMP_SCALP_T = 0.62
CLUMP_S_TIP_K = {"L": 1.0, "M": 1.0, "S": 0.9}
RIBBON_STATIONS_KIND = {"back": 13, "outer": 13, "side": 14, "beard_in": 13}   # v3: the half-hidden layers at the style
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
         "ecliptic_deg": 23.5, "finial": 0.018, "finial_r": 0.0085, "orb_r": 0.038, "grip_at": 0.62}   # "staff": length
                                      #   (sheet: taller than him), shaft radii, the head (cup + armillary + finial) height,
                                      #   ferrule span, collar, the armillary radius / ring thickness, the teal orb radius,
                                      #   the hand grips at grip_at x len
STAFF_REST_OFF = (-0.060, 0.010)      # rest pose: the staff stands upright this far (x, y m) from the right hand's grip point
STAFF_ELBOW_POLES = ((-0.55, 0.15, -0.80), (-0.30, 0.55, -0.80), (-0.75, -0.15, -0.65), (-0.15, 0.85, -0.50))   # the
                                      #   staff-hold evaluation's elbow directions tried (out, back, down) with the roll search
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
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow", "face_line"],
                    "hair": ["hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice", "beard_inner"],
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
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "version": "v1 draft",
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
