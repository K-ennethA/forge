"""LYRA (student researcher, Professor Elias' student) -- hero tier (Conquest roster), v1 DRAFT (lane-conventions "Two
speeds": one clean headless build + probes-as-sanity + one comparison strip; no gate wall, no determinism twin, NO clips).

    blender --background --factory-startup --python improve/lyra_build.py -- \
        [--preview <out.blend>]          (body + outfit + hair + props + regions + palette only: no bake, no rig)
        [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)
        [--until <section>] [--debug-blend <out.blend>]   (exploration: stop after a section, optionally save a painted blend)
        [--glb]                          (also export rigged/lyra.glb through the shared _GLOW path)

Spec (binding): design/review-log.md 2026-10-06 "NEW UNIT: Lyra" + design/OPEN-QUESTIONS.md L1-L5 (the live defaults built
here). SHAPE AUTHORITY: design/reference/lyra/lyra_sheet.webp (front / side / back figures + head, torso, shoulder, belt,
bag, cloth-pattern panels) -- silhouette-matched to the VIEWS, never a text transcription. Female, 17, FULL house-style face
(Wren reference stack), dark-brown HIGH PONYTAIL built mass-first (swept-back scalp into the tie, loose face-framing strands,
the ribbon-lock tail), blue ribbon + gold hairpiece + tassel at the tie; white collared shirt with rolled puffed sleeves,
blue necktie ribbon + gold diamond pin, navy gold-trimmed shoulder capelet with the compass-star emblem (large on the back),
brown cross-chest strap, dark corset + two belts (buckles, hanging vials + compass medallions), cream gold-trimmed skirt
panels with navy panels / diamond motifs over black trousers, leather satchel + scroll case, brown strapped boots; a stack
of BOOKS cradled in her RIGHT arm (the sheet's FRONT view; L1), the hold BAKED INTO THE BIND POSE (clipless law).
House style: design/character-style-guide.md. Reference implementation: improve/elias_* (read-only; itself Wren's stack):
s1 verbatim (the MPFB body + face ops), s2 re-cut (shirt / trousers, no beard / age lines), s3 the Lyra outfit, s4 the
capelet / strap / satchel / scroll case, s5 the ribbon-lock stack with the Lyra mass table + the ponytail + tie pieces,
s6 the book stack + assembly + bake, s7 rig + chains + the baked book hold + save + glb.
UNITS: metres, floor z = 0 at the boot soles, front -Y, left +X ("her left" = +X).
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
import lyra_parts as VP        # noqa: E402

# =========================================================================== TUNABLE CONSTANTS (artist-facing names)
UNIT = "lyra"
CHAR_ID = "lyra"
TRI_BUDGET = [30000, 50000]           # declared tier: HERO / named (quality-tier law: hero = 30-50k)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- body (MPFB2). MakeHuman age macro: 0.1875 = 11 yr, 0.5 = 25 yr -> 17 yr = 0.1875 + 6/14 x 0.3125 = 0.321
HEIGHT = 1.70                         # "height" (L2 default, a step under Wren): boot sole to the crown of the head (m)
SOLE_T = 0.024                        # "boot sole": the foot sits this far above the floor inside the boot
BODY_H = HEIGHT - SOLE_T              # (derived: the barefoot MPFB body height)
MACRO = {"gender": 0.0, "age": 0.321, "muscle": 0.45, "weight": 0.42, "proportions": 0.80, "height": 0.5,
         "cupsize": 0.42, "firmness": 0.60}                                 # "age 17, female": slim, active student build
RACE = {"caucasian": 0.70, "asian": 0.22, "african": 0.08}
TARGETS = {                           # "face dials": the house-style anime base (Wren's young dials) + a girl's soft face
    "eyes/l-eye-scale-incr": 1.00, "eyes/r-eye-scale-incr": 1.00,          # "eye size": the dial's max (then EYE_SCALE)
    "eyes/l-eye-height2-incr": 0.40, "eyes/r-eye-height2-incr": 0.40,      # "eye opening"
    "eyes/l-eye-bag-decr": 1.00, "eyes/r-eye-bag-decr": 1.00,              # no bags (the flat under-eye law)
    "head/head-oval": 0.45,                                                # smooth oval head
    "chin/chin-bones-decr": 1.00, "chin/chin-width-decr": 1.00, "chin/chin-triangle": 0.70,   # "jaw": soft, pointed (v1.1: was
    "chin/chin-height-decr": 1.00,                                         #   0.60 / 0.45 / 0.40 + no height dial) -- "CHIN LENGTH":
                                                                           #   v1.1 the artist's red line (contour seam->chin 61.1 -> 52.7 mm)
    "cheek/l-cheek-volume-decr": 0.15, "cheek/r-cheek-volume-decr": 0.15,  # "cheeks": youthful, not gaunt
    "nose/nose-scale-horiz-decr": 0.45, "nose/nose-volume-decr": 0.50, "nose/nose-point-width-decr": 0.60,   # "nose size"
    "nose/nose-flaring-decr": 0.50, "nose/nose-nostrils-width-decr": 0.50, "nose/nose-scale-vert-decr": 0.20,
    "mouth/mouth-scale-horiz-decr": 0.55,                                  # "MOUTH WIDTH": seam -> ~0.70 x eye spacing
    "mouth/mouth-trans-up": 1.00,                                          # "MOUTH HEIGHT": toward the Ashe v_ratio 0.29 (v1.1: 0.75 ->
                                                                           #   1.0 re-centres the mouth on the SHORTER chin)
    "mouth/mouth-lowerlip-volume-incr": 0.50, "mouth/mouth-upperlip-volume-decr": 0.40,
    "mouth/mouth-angles-up": 0.50,                                         # "mouth corners level"
    "eyebrows/eyebrows-angle-down": 0.12,                                  # "determined brows" (the sheet's calm focus)
    "neck/neck-scale-horiz-decr": 0.30,                                    # slender neck
    "expression/units/caucasian/eye-left-slit": 0.12, "expression/units/caucasian/eye-right-slit": 0.12,   # "calm lids"
    "legs/upperlegs-height-incr": 0.0, "legs/lowerlegs-height-incr": 0.0,    # leg length (0: the head-to-body read needs the height in the head, house rule 5.5-6)
}
TARGETS_EDIT = None                   # (s1 probe hook kept from Wren; None = TARGETS as listed)
HEAD_SCALE = (1.00, 0.55, 0.60)       # "head size": the MPFB head-scale dials (vert, horiz, depth incr; 0..1) -- the anime head-to-body
                                      #   read (house rule 5.5-6 heads tall)
REST_ARM_DOWN = 22.0                  # "rest arm drop": the MPFB A-pose arms lowered this much (deg) in the bind pose (left arm;
                                      #   the right arm is then IK-solved onto the book stack in s7)
REST_ELBOW_OPEN = 24.0                # the MPFB rest elbow opened this much (deg)
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
EYE_IRIS_DEG, EYE_PUPIL_DEG = 27.5, 10.0          # "iris size" (~58-62 % of the opening; house 55-65 %)
EYE_SEG = 36
EYE_BACK = 110.0
IRIS_SHADE = 0.30
EYE_HILITE = (32.0, 0.34, 3.9)        # "eye highlight": the round dot (angle, place, size)
EYE_HILITE_RING = 2.0
LINER_W = (0.0013, 0.0003)            # lower liner 0.3 mm
LINER_WING = (0.0007, 20.0, 10.0)
LASH_PROFILE = ((0.0, 2.3), (30.0, 2.7), (90.0, 2.2), (150.0, 1.1), (180.0, 0.5))   # "upper lash band" (Wren's + a girl's lash)
LASH_WING = (2.6, 11.0, 2.0)          # "lash wing": the tapered flick (mm extra, deg half width, centre deg)
BROW_PTS = ((-1.15, 0.0064), (-0.40, 0.0084), (0.50, 0.0098), (1.28, 0.0080))   # "brows": a soft arc (a girl's brow, higher mid)
BROW_W = (0.0072, 0.0028)             # "bold brows": inner / tail width (m) -- bold but finer than Wren's 8.6 / 4.0 (a girl's brow)
BROW_TAPER = 1.2
LIP = (0.0180, 0.0035, 0.0065)        # paler lip tint zone
LIP_FORMS = {"lower": (0.0012, 0.0070, 0.80, 0.80, 0.40), "upper": (0.0004, 0.0050, 0.70, 0.90, 0.40)}
LIP_DZ = 0.0
MOUTH_LINE = (0.0012, 0.45)
MOUTH_LINE_EXT = 0.0010
MOUTH_LINE_REFINE = {"passes": 3, "samples": 41}
MOUTH_LINE_CENTROID = 0.00015
MOUTH_LINE_SNAP = 0.02
MOUTH_LEN = None
MOUTH_SMIRK = None                    # "smirk": none (calm, kind); an artist question
MOUTH_SMIRK_GEO = None
MOUTH_SMOOTH = (4.0, 30.0, 12.0, 28.0, 6.0, 2.0, 2.0, 5.0)
MOUTH_PROXY = (4.0, 30.0, 12.0, 28.0, 6.0, 2.0)
FACE_UV_SCALE = 4.5
FACE_NORMAL_REF = "flat"
MOUTH_IN_D = 0.0032
JAW_LIGHT_DEG = 42.0
JAW_GATE = (0.008, 0.6, 0.012)
JAW_EDGE = (4.0, 10.0)                # "neck shadow edge" (v1.1): the chin's cast shadow edged by a DRAWN smooth curve -- azimuth bin,
                                      #   Gaussian sigma round the neck (deg); larger sigma = a calmer, rounder edge (s2 authored shadow)
MOUTH_HIDDEN = (0.034, 0.014, 0.030, 0.00005, 0.06, 0.003)
EYE_RIM_HIGH_OUT = (0.5, 2.0, 100.0, 50.0)
ENCLOSED_DIRS = ((0, -1, 0), (0.7, -0.7, 0), (-0.7, -0.7, 0), (0, -0.7, 0.7), (0, -0.7, -0.7), (0.5, -0.5, 0.5),
                 (-0.5, -0.5, 0.5), (0.5, -0.5, -0.5), (-0.5, -0.5, -0.5), (0, -0.57, 0.82), (0.95, -0.3, 0), (-0.95, -0.3, 0))
FACE_LINES = None                     # "age lines": NONE (age 17 -- the youthful face, no painted lines)
# ---- scalp hair: the HIGH PONYTAIL, mass-first (review-log 2026-10-06; silhouette-matched to the saved sheet's front / side /
# back views + head panel): the hair SWEPT BACK from the hairline (front, temples, nape) into the tie high at the back of the
# crown; loose face-framing strands at both temples (longer on her right, to the collarbone); the ponytail rising from the
# tie, arcing back and falling down the back to the belt line, drifting to her right (the back view)
HAIR_INTERIOR_R = 0.70
HAIRLINE = (0.060, -0.040)            # "hairline": above the eye centres at the front / at the nape vs the head joint (m)
HAIRLINE_SIDE = (0.012, -0.004, -0.004)   # "hairline over the ears" (v1.1, review-log "Lyra v1 verdicts" (1): the hair extends
                                      #   DOWN into the bare band above / behind the ear): above the eye centres (m) -- the ARCH over
                                      #   the ear (its top ~5 mm above the eye centres, measured: 7 mm clear), the SIDEBURN dip in
                                      #   front of the ear, the band BEHIND it (v1: one flat 0.026 arch = 21 mm of bare skin over the ear)
HAIRLINE_EAR_CE = (-0.30, 0.13)       # the ear's span in the hairline's front/back coordinate ce (measured: skin standing > 95 mm
                                      #   out at y -0.055 .. -0.015 = ce 0.11 .. -0.28); the arch holds over it, falls outside
HAIR_CAP_T = 0.0040                   # "hair volume" on the scalp (the inner volume under the swept masses; Elias 12 mm)
HAIR_CAP_INNER = False                # the cap's scalp-facing shell harvested (provably hidden)
HAIR_CAP_SINK = 0.0015                # the cap's inner shell / rim sinks this far UNDER the scalp (m): the hairline rim seals into the skin
HAIR_ROOT_K = 0.30
HAIRLINE_FEATHER = (0.045, 0.0015)    # the cap feathers over 24 mm above the hairline (a thick cap edge reads as a helmet)
HAIR_UV_STRIP = 0.16                  # hair faces packed into the right 16 % of the UV square
LOCK_OFF = 0.0020
TIE_DIR = (180.0, 58.0)               # "ponytail tie": the tie's place on the head (psi deg from the front, elevation deg about
                                      #   the head centre): the sheet's HIGH ponytail -- the side view's tie at the crown's back
TIE_OFF = 0.012                       # the tie centre stands this far off the cap surface (m)
TIE_R = 0.016                         # "tie size": the gathered hair's radius at the tie (m)
SCALP_HUG = True                      # "scalp hugs the head": the swept / part locks stay thin scalp petals until they enter the tie
                                      #   (their free point = the tie): no stacked lock ledges along the hairline (the v1 helmet / bowl read)
SWEEP_LIFT_RAMP = (0.25, 0.70, 1.0, 0.75)   # the swept locks' volume lift along the sweep (share of the mass lift at the
                                      #   control points after the root; the last points sink into the tie)
TAIL_FRAME_UP = 0.55                  # the tail's first direction: the tie's outward normal leaned this much toward straight up
TAIL_PATH = ((0.0, 0.0, 0.0), (-0.004, 0.028, 0.044), (-0.012, 0.074, 0.052), (-0.026, 0.118, 0.004), (-0.050, 0.140, -0.110),
             (-0.080, 0.140, -0.270), (-0.100, 0.128, -0.430), (-0.112, 0.118, -0.600))   # "ponytail line": the tail's mean
                                      #   path from the tie (x m her left, y m back, z m up): rising up / back out of the tie
                                      #   (the side view's fountain), over and down the back to the belt line, drifting to her
                                      #   right (the back view); the spine clearance keeps it off the capelet / back
HAIR_PART = ((-12.0, 26.0), (-9.0, 48.0), (-5.0, 72.0))   # "soft part": the part line (psi deg from the front, - = her right;
                                      #   elevation deg about the head centre) from the front hairline back over the top -- the
                                      #   sheet's front view: a soft part just off centre to her right, the fringe sweeping off it
                                      #   to both sides (more hair to her left)
HAIR_MASSES = {                       # "primary masses" (6: nape, sides, front, fringel, fringer, tail): per mass the
    # follow-through chain (None = rigid on the head), stack offset (m), volume lift off the cap (m: down the sides / over the
    # top), free point (control index where the lock leaves the head), and its LOCKS listed bottom -> top. Root forms:
    # ("hl", psi deg, mm above the hairline) | ("part", t along HAIR_PART 0 front .. 1 back, vias) with vias = control points
    # (("side", psi, z mm above the eye centres, standoff m) | ("head", psi, elevation deg, standoff m)); tip = "tie" (swept
    # into the tie ring) | ("face", x mm, z mm) | ("side", psi, z mm, flick m); tail locks: (tier, width, (spread x mm,
    # spread y mm at the ends), length share of TAIL_PATH, root angle round the tie deg). psi + = her left. Width spread
    # >= 3:1 inside every mass.
    "nape":  {"chain": None, "layer": 0.0, "lift": (0.006, 0.006), "free_k": 3, "locks": (
        ("L", 66.0, ("hl", 180.0, 6.0), "tie"),                     # the nape swept UP into the tie (the back view)
        ("L", 62.0, ("hl", 152.0, 6.0), "tie"),
        ("L", 62.0, ("hl", -152.0, 6.0), "tie"),
        ("M", 46.0, ("hl", 128.0, 4.0), "tie"),
        ("M", 44.0, ("hl", -128.0, 4.0), "tie"),
        ("S", 20.0, ("hl", 166.0, 2.0), "tie"))},
    "sides": {"chain": None, "layer": 0.0010, "lift": (0.011, 0.010), "free_k": 3, "locks": (
        ("M", 48.0, ("hl", 120.0, 6.0), "tie"),                     # behind / over the ears swept back into the tie
        ("M", 48.0, ("hl", -120.0, 6.0), "tie"),
        ("L", 64.0, ("hl", 96.0, 4.0), "tie"),
        ("L", 64.0, ("hl", -96.0, 4.0), "tie"),
        ("S", 20.0, ("hl", 106.0, 2.0), "tie"),
        ("S", 20.0, ("hl", -106.0, 2.0), "tie"))},
    "front": {"chain": None, "layer": 0.0030, "lift": (0.008, 0.012), "free_k": 3, "locks": (
        ("L", 62.0, ("hl", 4.0, 3.0), "tie"),                       # the front hairline swept back over the top into the tie,
        ("L", 60.0, ("hl", 20.0, 3.0), "tie"),                      #   split at the soft part (HAIR_PART, psi -12: the gap
        ("M", 50.0, ("hl", 36.0, 3.0), "tie"),                      #   between the roots at +4 and -28 reads as the part line;
        ("M", 42.0, ("hl", 52.0, 3.0), "tie"),                      #   more hair to her left, the sheet's front view)
        ("L", 58.0, ("hl", -28.0, 3.0), "tie"),
        ("M", 46.0, ("hl", -44.0, 3.0), "tie"),
        ("S", 20.0, ("hl", -58.0, 2.0), "tie"),
        ("S", 16.0, ("hl", 66.0, 2.0), "tie"))},
    "fringel": {"chain": "hair_side.L", "layer": 0.0042, "lift": (0.004, 0.004), "free_k": 2, "locks": (
        ("M", 26.0, ("hl", 44.0, -1.0), ("side", 70.0, -96.0, 0.012)),   # her left: the long strand down the cheek to the jaw
        ("S", 12.0, ("hl", 52.0, -1.0), ("side", 80.0, -62.0, 0.010)),
        ("M", 24.0, ("part", 0.40, (("side", 26.0, 50.0, 0.006),)), ("side", 62.0, -6.0, 0.010)),   # the FRINGE sweeping off the
        ("S", 12.0, ("part", 0.56, (("side", 38.0, 50.0, 0.006),)), ("side", 72.0, -34.0, 0.010)),  #   part down to her left temple
        ("S", 8.0, ("hl", 18.0, 2.0), ("face", 30.0, 26.0)))},           #   + a wisp onto the forehead (head panel)
    "fringer": {"chain": "hair_side.R", "layer": 0.0045, "lift": (0.004, 0.004), "free_k": 2, "locks": (
        ("M", 24.0, ("hl", -44.0, -1.0), ("side", -74.0, -112.0, 0.014)),  # her right: the longer strands to the collarbone
        ("S", 10.0, ("hl", -52.0, -1.0), ("side", -84.0, -84.0, 0.012)),
        ("M", 22.0, ("part", 0.34, (("side", -36.0, 50.0, 0.006),)), ("side", -66.0, -8.0, 0.010)),   # the shorter fringe side
        ("S", 8.0, ("hl", -30.0, 2.0), ("face", -38.0, 22.0)))},
    "tail":  {"chain": "hair_tail", "layer": 0.0050, "lift": (0.0, 0.0), "free_k": 1, "locks": (
        ("L", 76.0, (0.0, 36.0), 1.00, 180.0),                      # the PONYTAIL (bottom -> top layer): full near the tie,
        ("L", 70.0, (-36.0, 24.0), 0.93, 225.0),                    #   fanning out, wavy (TAIL_WAVE)
        ("L", 70.0, (32.0, 26.0), 0.88, 135.0),
        ("L", 60.0, (-18.0, 46.0), 0.85, 160.0),
        ("M", 48.0, (22.0, 42.0), 0.82, 200.0),
        ("L", 58.0, (-54.0, 0.0), 0.80, 270.0),
        ("M", 50.0, (48.0, 2.0), 0.76, 90.0),
        ("S", 24.0, (-62.0, 16.0), 0.68, 315.0),
        ("S", 22.0, (58.0, 18.0), 0.64, 45.0),
        ("M", 40.0, (0.0, -32.0), 0.72, 340.0),
        ("L", 58.0, (-6.0, -20.0), 0.96, 0.0),
        ("M", 46.0, (24.0, -22.0), 0.86, 30.0))}}
CLUMP_ROOT = {m_: 0.0 for m_ in HAIR_MASSES}
HAIR_KIND_W = {m_: 1.0 for m_ in HAIR_MASSES}   # (widths are set per lock in HAIR_MASSES)
HAIR_LAYER = {m_: v_["layer"] for m_, v_ in HAIR_MASSES.items()}   # stack order offset (m)
HAIR_LIFT = {m_: v_["lift"][0] for m_, v_ in HAIR_MASSES.items()}   # "hair volume" lift (m)
HAIR_LIFT_RAMP = (0.55, 1.0, 1.0)     # (loose locks) the lift per control point after the root (share of the mass lift)
HAIR_BONES = 3                        # follow-through bones per chain (Wren's)
TAIL_BONES = 4                        # follow-through bones on the ponytail (the long hanging mass)
TAIL_RADIAL = 0.85                    # "round ponytail": the tail locks' section frames lean this much toward the radial from the
                                      #   tail's own line (they wrap round it as a tube; 0 = they lie flat on the back like the scalp locks)
TAIL_THICK = 2.6                      # "ponytail body": the tail locks' section thickness x their tier's (rounder, fuller strands)
TAIL_ROOT_K = 0.75                    # "full at the tie": the tail locks' root width share (narrow 0.35 read as a limp rope)
TAIL_WAVE = (0.020, 1.6)              # "wavy tail": lateral wave amplitude (m, grows in down the tail) and waves along the tail; each
                                      #   lock's phase follows its ring angle (the sheet's side / back views: a full wavy tail)
CLUMP_STACK = 0.0004
CLUMP_W_VARY = 0.14
CLUMP_WRAP = 1.0
CLUMP_TOP_THIN = (55.0, 80.0, 0.30)
CLUMP_ROOT_GROW = 0.40
CLUMP_SWAY = 0.12                     # "S-curve sway": sideways sway amplitude (x the lock width)
CLUMP_SCALP_T = 0.62
CLUMP_S_TIP_K = {"L": 1.0, "M": 1.0, "S": 0.9}
RIBBON_STATIONS_KIND = {"nape": 13, "sides": 13, "front": 14, "tail": 15}   # "lock segments" per mass
RIBBON_STATIONS = {"L": 15, "M": 14, "S": 13}   # the style guide's 13-19 sections
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
RIBBON_RADIAL_AXIS = False
RIBBON_TIER = {"L": (0.058, 0.0036, 0.40), "M": (0.044, 0.0032, 0.40), "S": (0.024, 0.0026, 0.70)}
LAYER_GAP = 0.0006
LAYER_ROOT = 0.10
LAYER_NEAR = 0.005
LAYER_WINDOW = 0.030
LAYER_LIFT_MAX = 0.009
LAYER_SMOOTH = 1.3
LAYER_ITERS = 4
RIBBON_RING_JITTER = 0.6
RIBBON_TUCK = (0.6, 3, 0.012)
HAIR_TIERS = (0.08, 0.90)             # "painted hair tiers": root below / tip above these arc fractions
ANGEL_RING = (48.0, 52.0)             # "angel ring" elevations (deg) on the head's ellipsoid (the swept crown's highlight)
HAIR_CREVICE_MAX_EL = 50.0
HAIR_PROXY = (48, 24, 80, 0.003)      # "one-volume hair shading" proxy -- built per GROUP: the scalp + tie, and the ponytail
HAIR_LOCK_NORMAL_MIX = 0.45
HAIR_SHELL_NORMAL_MIX = 0.85
HAIR_NORMAL_CARRIER = "vertex"        # "hair normal carrier" (house standard): custom split vertex normals on the hair faces
HAIR_CLEAR = (0.0025, 0.0005)
HAIR_BAKE_CAGE = (0.0004, 0.0012)
NEAREST_TIE = 1e-6
# ---- the tie pieces (L4: the ribbon tails + tassel follow-through)
RIBBON_BAND = (0.0060, 0.0026)        # "ribbon wrap": the blue band round the tie (half width m, thickness m)
RIBBON_BOW = ((0.034, 0.016, 35.0), (0.030, 0.014, -40.0))   # "bow loops": per loop (length m, half width m, angle deg off the
                                      #   tail axis about the tie's out direction; + = her left)
RIBBON_TAILS = (((-0.022, 0.012), 0.24, 0.0090, "ribbon"), ((0.016, 0.014), 0.20, 0.0085, "ribbon"),
                ((-0.008, 0.020), 0.15, 0.0050, "ribbon_gold"), ((0.006, 0.022), 0.13, 0.0045, "ribbon_gold"))
                                      # "ribbon tails": per tail (x / y m offset from the tail line at the tie, length m, half
                                      #   width m, region): the blue ribbon's two tails + the two gold tails (head panel)
RIBBON_TAIL_T = 0.0010                # ribbon tail half thickness (m)
HAIRPIECE = {"size": (0.016, 0.020), "t": 0.0030, "gem_r": 0.0045, "tassel": (0.040, 0.0045)}   # "gold hairpiece": the diamond
                                      #   plate on the back of the tie (half width, half height m), thickness, the round gold
                                      #   boss, the dangling tassel (length, radius m)
TAIL_RING = (0.45, 0.0040)            # "second tie": the gold band round the ponytail at this share of its length (back view)
# ---- boots (Elias's boot stack + shadow-assassin's straps; knee-high brown lace-up)
BOOT_MARGIN = (0.009, 0.011)
TOE_EXT = 0.020
BOOT_CLEAR = (0.009, 0.014)           # shaft clearance at the ankle / at the top (m)
BOOT_TOP = 0.36                       # "boot height": the shaft reaches this far down the shin from the knee (knee 0 .. ankle 1)
CUFF = (0.034, 0.010, 0.012, 0.0040)  # "boot cuff": height, clearance, flare, thickness (m)
BOOT_STRAPS = (0.22, 0.48, 0.74)      # "boot straps": strap centres down the shaft (shaft top 0 .. ankle 1)
BOOT_STRAP = (0.015, 0.0028, (0.010, 0.0035, 0.008))   # strap height, thickness (m), gold buckle half sizes
# ---- shirt (painted on the body) + puffed rolled sleeves + collar + necktie
SLEEVE_T = 0.32                       # "rolled sleeve": the shirt sleeve ends this far down the forearm (elbow 0 .. wrist 1)
SLEEVE_ROLL = (0.046, 0.014, 0.006)   # "rolled cuff": height, roll thickness, clearance (m)
SLEEVE_PUFF = {"t": (0.40, 1.0), "clear": (0.004, 0.030), "fore": 0.85, "nu": 16, "nv": 7, "t_cloth": 0.0025}   # "puffed
                                      #   sleeve": over the upper arm from this share of shoulder -> elbow to the elbow, clearance
                                      #   growing to the max at the elbow (the sheet's billowing sleeves), continuing down the
                                      #   forearm to the cuff at "fore" x the max; grid; cloth thickness
VNECK = (0.018, 0.30)                 # shirt neckline: depth below the neck base (m), rise per m of |x| (a closed collar)
NECK_DZ = 0.010
COLLAR = {"z": 0.014, "band_r": 0.0055, "band_flat": 2.2, "flap": (0.030, 0.020, 0.0018, 28.0)}   # "shirt collar": band height
                                      #   above the neck base, tube radius, flattening; the front points (length, width, thickness
                                      #   m, splay deg)
NECKTIE = {"knot": (0.014, 0.009, 0.012), "tails": ((-0.010, 0.105, 0.0085, -8.0), (0.010, 0.092, 0.0080, 10.0)),
           "t": 0.0012, "pin": (0.0065, 0.0028)}   # "necktie ribbon": knot half sizes (m), tails (x off m, length m, half
                                      #   width m, splay deg), half thickness, gold diamond pin (radius, height m)
# ---- corset + belts + hanging vials / compass medallions + pouch
CORSET = {"z": (-0.045, 0.085), "clear": 0.004, "t": 0.0045}   # "corset": span vs the waist line (m), clearance, thickness
BELTS = ((0.010, 0.024, 0.006, 0.0), (-0.042, 0.022, 0.008, -0.030))   # "belts": per belt (centre z vs the waist m, height
                                      #   m, clearance over the corset m, slope: z drop per unit x toward her right)
BELT_T = 0.0045
BUCKLES = ((0, 6.0, (0.016, 0.018, 0.004)), (1, -22.0, (0.015, 0.017, 0.004)))   # "gold buckles": (belt, azimuth deg from
                                      #   the front + her left, half sizes across / height / depth)
VIALS = ((28.0, 0.060, 0.0075), (38.0, 0.070, 0.0080), (47.0, 0.055, 0.0070))   # "hanging vials": (azimuth deg, length m,
                                      #   radius m) hanging from the lower belt (her left front: the belt panel / front view)
MEDALLIONS = ((66.0, 0.060, 0.020), (84.0, 0.085, 0.017))   # "compass medallions": (azimuth deg, drop below the lower belt m,
                                      #   radius m) at her left hip (the side view's two gold compasses)
POUCH = (58.0, (0.040, 0.020, 0.044)) # "hip bag": her left hip (front view): azimuth, half sizes across / depth / height
# ---- skirt panels (trunk-hung): cream with navy panels, gold trims, diamond motifs; open at the front over the trousers
SKIRT = {"open": 26.0, "len": (0.70, 0.76), "clear": 0.010, "flare": 0.080, "t": 0.0032, "nu": 46, "nv": 12,
         "points": (0.070, 9), "tear": (0.030, 0.012)}   # "skirt": front opening half angle (deg from the front), length below
                                      #   the waist line front / back (m), clearance, hem flare, thickness, grid, the pointed hem
                                      #   (point depth m, points round the hem), the hem's small tatter (long, typical m)
SKIRT_NAVY = ((26.0, 46.0), (-46.0, -26.0), (168.0, 192.0), (84.0, 102.0), (-102.0, -84.0))   # "navy panels": azimuth bands
                                      #   (deg from the front, + her left) painted navy: the front edges, the back centre strip,
                                      #   the side strips (front / back views)
SKIRT_TRIM = 0.006                    # gold trim band along every navy panel edge and the hem (m)
SKIRT_BAND = (0.050, 0.030)           # "hem band": the patterned band on the cream panels -- height above the hem, its width (m)
SKIRT_MOTIFS = 10                     # gold diamond motifs on the hem band
FRONT_TAB = {"w": 0.070, "len": 0.30, "trim": 0.006, "t": 0.0030, "nv": 8}   # "front tab": the navy panel hanging at the centre
                                      #   front from the belts (width, length below the lower belt, trim, thickness, rows)
SKIRT_EMBLEM = (36.0, 0.52, 0.090)    # "panel emblem": the compass star on her left navy panel (azimuth deg, centre z m, height m)
# ---- capelet (shoulder-hung) + emblem + brooch + strap + satchel + scroll case
CAPELET = {"front": 152.0, "drop": ((0.0, 0.340), (40.0, 0.290), (90.0, 0.270), (150.0, 0.190)), "clear": 0.012, "flare": 0.030,
           "nu": 48, "nv": 10, "t": 0.0040, "trim": 0.014, "v_sharp": 2.2}   # "capelet": front edge azimuth from the BACK (deg),
                                      #   the drop below the neck line per azimuth from the back (deg, m: the back's long V point,
                                      #   short over the arms, shorter at the front), clearance, flare, grid, thickness, gold
                                      #   trim band (m), V-point sharpness
CAPE_EMBLEM = (0.155, 0.105, 0.0042)  # "back emblem": the compass star's height, its centre below the neck base, stroke half width (m)
SHOULDER_EMBLEM = (0.060, 0.0024)     # the small compass on each shoulder (the side view / shoulder panel): height, stroke half width
BROOCH = {"r": 0.019, "drop": 0.105, "x": 0.088, "crest_h": 0.026}   # "compass brooch": her left chest (front view): radius,
                                      #   drop below the neck base, x (m, + her left), crest size
STRAP = {"top": (0.55, 0.10), "low": (-0.95, 0.02), "w": 0.026, "t": 0.0028}   # "cross strap": her left shoulder (x share of the
                                      #   shoulder joint, dz above it m) -> her right hip (x share of the hip joint, dz vs the hip m)
SATCHEL = {"phi": -102.0, "z_off": -0.020, "size": (0.110, 0.040, 0.092), "flap": 0.64}   # "satchel": azimuth from the FRONT
                                      #   (her right side), height vs the strap's low end, half sizes, flap share
SCROLL_CASE = {"phi": -138.0, "tilt": 24.0, "len": 0.36, "r": 0.020, "z": -0.10}   # "scroll case": azimuth, tilt deg, length,
                                      #   radius, top vs the lower belt (the back view's diagonal tube behind the satchel)
SCROLL_ROLL = {"phi": 150.0, "len": 0.17, "r": 0.016, "z": -0.050}   # "scroll": the paper roll at her left back hip (side view)
BRACELET = (0.035, 0.0030, 0.0040)   # "bracelets": the dark band on both wrists -- this far up the forearm from the wrist joint
                                      #   (m), tube radius (m), clearance off the skin (m)
# ---- the BOOK STACK (own object + bone 'books', child of hand_r) and the baked hold (L1: the RIGHT arm, the sheet's front view)
HAS_BOOKS = True                      # "book stack": False = empty-handed (the right arm keeps the rest drop, no prop / bone)
BOOKS = (((0.180, 0.250, 0.040), "book_cover"), ((0.172, 0.240, 0.034), "book_cover2"), ((0.165, 0.228, 0.038), "book_cover"))
                                      # "books": per book (width spine -> fore-edge, height, thickness m), cover region; stacked
                                      #   along the thickness axis, inner -> outer
BOOK_COVER_T = 0.0035
BOOK_HOLD = {"x": -0.135, "fwd": -0.035, "drop": 0.140, "yaw": 38.0, "tilt": 6.0, "clear": 0.004}   # "book place": the
                                      #   stack centre vs the right shoulder joint z (drop m) at x (m, - = her right) and this far
                                      #   in front of the chest front (fwd m, - = forward); yaw = the stack's thickness axis turned
                                      #   from straight out (her right) toward the front (deg: the front view shows the covers
                                      #   facing front-outward); tilt = the top leaning back toward her (deg); the stack then
                                      #   slides out until this clearance off the body / outfit
BOOK_GRIP = {"u": 0.70, "v": 0.24, "palm": 0.024}   # "hand on the books": the palm centre on the OUTER cover at u (share from the
                                      #   spine at the front toward the fore-edge) / v (share up from the bottom), palm standoff (m)
HOLD_ELBOW_POLES = ((-0.30, 0.90, -0.30), (-0.60, 0.70, -0.40), (-0.85, 0.40, -0.35), (-0.20, 0.95, 0.20), (-0.90, 0.10, -0.40))
                                      # the elbow directions tried (out, back, down) with the hand-roll search
HOLD_TWIST_SHARE = 0.5                # "forearm twist share"
HOLD_CURL = ((40.0, 55.0, 35.0), "wrap")   # "cradle fingers": the start curl per joint (deg); per-finger curl solve then fits
                                      #   each finger to the stack (shadow-assassin precedent), the thumb solved onto the cover
FINGER_PAD = 0.0012                   # the solved fingers' closest approach to the books (m)
BOOK_CAPELET_CLEAR = 0.003            # the capelet's front panel (shoulder-hung: it rides the raised arm) is laid BEHIND the
                                      #   stack in the bind pose by this clearance (m) where the swing carried it into the books
HAND_DECIMATE = {"L": 0.36, "R": 0.52}   # "hand detail" (Elias v7.1 budget pattern)
# ---- bake + material
BAKE_RES = (2048, 1024)
CUT_SNAP = 0.12
SLIVER_AREA = 5e-8
AO_SAMPLES = 48
AO_FLOOR = {"default": 0.45, "skin": 0.62, "hair": 0.82, "cloth": 0.52}
AO_FACE_LIFT = None
AO_FACE_FLOOR = (1.0, 3.0, (0.0, 26.0, 100.0), (0.031, 0.013, 0.028))
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow"],
                    "hair": ["hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice"],
                    "cloth": ["shirt", "shirt_shade", "shirt_roll", "collar", "capelet", "capelet_lining", "skirt", "skirt_shade",
                              "skirt_navy", "skirt_navy_inner", "skirt_band", "trousers", "tie", "tie_shade"]}

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
BODY_H = HEIGHT - SOLE_T
TARGETS = {**TARGETS, "head/head-scale-vert-incr": HEAD_SCALE[0], "head/head-scale-horiz-incr": HEAD_SCALE[1],
           "head/head-scale-depth-incr": HEAD_SCALE[2]}
SCRATCH = argv[argv.index("--scratch") + 1] if "--scratch" in argv else None
_OUT_ROOT = SCRATCH or ROOT
OUT_IMPROVED = os.path.join(_OUT_ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(_OUT_ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(_OUT_ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(_OUT_ROOT, "improved", "textures")
TAG = "scratch" if SCRATCH else "main"
report = {"unit": UNIT, "conquest_character_id": CHAR_ID,
          "version": "v1.1 draft (house-style face, mass-first high ponytail, academy outfit, book stack held in the bind pose; "
                     "v1.1 = the artist's v1 verdicts: side hairline lowered round the ear, continuous jaw cast-shadow field, shorter chin)",
          "name_status": "named by the sheet (LYRA - STUDENT RESEARCHER, PROFESSOR ELIAS' STUDENT)",
          "source": "design/reference/lyra/lyra_sheet.webp (review-log 2026-10-06)", "tier": "hero",
          "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}
SECTIONS = ["lyra_s1_body.py", "lyra_s2_regions.py", "lyra_s3_outfit.py", "lyra_s4_capelet.py", "lyra_s5_hair.py",
            "lyra_s6_assemble.py", "lyra_s7_rig.py"]
UNTIL = argv[argv.index("--until") + 1] if "--until" in argv else None   # (exploration: stop after the named section)
for sec_ in SECTIONS:
    _p = os.path.join(HERE, sec_)
    print("SECTION", sec_, round(time.time() - T0, 1)); sys.stdout.flush()
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
    if UNTIL and UNTIL in sec_:
        if "--debug-blend" in argv and "PARTS" in globals():   # (exploration: body + parts, palette-painted, for quick stills)
            _pal = PAL.load(UNIT, "default")
            _isl = [{"name": "body", "V": CV, "F": CF, "R": list(reg)}] + PARTS
            _V = np.vstack([p_["V"] for p_ in _isl]); _F, _R, _o = [], [], 0
            for p_ in _isl:
                _F += [[i + _o for i in f] for f in p_["F"]]; _R += list(p_["R"]); _o += len(p_["V"])
            _names = sorted(set(_R))
            _me = bpy.data.meshes.new(UNIT); _me.from_pydata(_V.tolist(), [], _F); _me.update()
            _ob = bpy.data.objects.new(UNIT, _me); scene.collection.objects.link(_ob)
            PAL.store_regions(_me, _names, [_names.index(r_) for r_ in _R], np.ones(len(_R)))
            PAL.paint(_me, _pal)
            _mt = bpy.data.materials.new("dbg"); _mt.use_nodes = True
            _vc = _mt.node_tree.nodes.new("ShaderNodeVertexColor"); _vc.layer_name = "Col"
            _mt.node_tree.links.new(_vc.outputs["Color"], _mt.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
            _me.materials.append(_mt); _me.shade_flat()
            _ob["conquest_focus"] = json.dumps({"head": [(HC - HR * 1.3 - np.array([0, 0, 0.05])).tolist(), (HC + HR * 1.3 + np.array([0, 0.12, 0])).tolist()]})
            bpy.ops.wm.save_as_mainfile(filepath=argv[argv.index("--debug-blend") + 1], copy=True, compress=True)
            print("DEBUG_BLEND", len(_F), "faces", tri_count_F(_F), "tris")
        print("UNTIL", sec_, round(time.time() - T0, 1)); sys.stdout.flush(); os._exit(0)
