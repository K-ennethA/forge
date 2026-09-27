"""Vampire Warrior -- the HERO build (first humanoid Conquest unit; no source sculpt), one headless run.

    blender --background --factory-startup --python improve/vampwarrior_build.py -- \
        [--preview <out.blend>]          (body + outfit + hair + sword + regions + palette only: no bake, no rig)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-09-26 "Magmoo eye defect + NEW UNIT: Vampire Warrior" (the transcription of the artist's
three-view sheet; the image is not on disk). Tall female vampire warrior, intimidating presence; pale skin, red eyes,
fangs, very long bone-white hair (centre part, over the shoulders in front, cascade to the waist behind); black
high-collar armoured bodice with silver seam trim + red throat gem; long torn black cape with dark-red lining, ragged
ankle hem; long black gloves; black thigh-high heeled boots with armoured knees; skirt fauld with the bare upper-thigh
gap; a sword nearly her height, point-down: leaf / flame blade (bone-pale, serrated, dark centre vein), simple handle,
red tassel at the guard. HERO tier (30-50k tris).

BASE-MESH DECISION: MPFB2 human base (the house humanoid tool, docs/lane-conventions "parity bar") for the BODY -- its
proportions, face, hands and the game_engine skeleton + weights are best-in-class and free; hand-rolling a human body
from SDF would be slower and worse. Everything the sheet adds (boots' toe caps / soles / heels, knee cops, fauld plates,
belt, collar, gem, cape, hair, fangs, eyes, sword) is procedural geometry on top. Tight garments (bodice, gloves, boot
shafts) are PAINTED regions of the body mesh with iso-cut edges + raised cuffs: form-fitting by design, and they deform
exactly with the skin (zero cloth-vs-skin intersection in any clip).

UNITS: metres (MPFB native), floor z = 0 at the boot soles, front -Y, left +X. Natural scale; cell fit report-only.

Pipeline:
  1. MPFB2: female macros + stylising targets, game_engine rig with weights; the HEEL POSE is applied through the MPFB
     rig (foot pitched toes-down about the ankle, toes counter-rotated flat) so the rest mesh stands in heeled boots.
  2. body faces extracted (helpers masked out), scaled to BODY_H, lifted onto the soles; landmarks from the MPFB joints.
  3. regions on the body: iso-cut fields (boot top, glove top, armholes, neckline, leg line, seam trims, lips, liner).
  4. parts (vampwarrior_parts.py): eyes, fangs, toe caps + soles + heels, knee cops, boot / glove cuffs, fauld plates +
     belt, collar + throat gem, cape (solidified torn sheet), hair (scalp cap + lens-section locks), sword (+ tassel).
  5. palette, UVs, bake (normal + AO from the subdivided MPFB high + parts), rig (MPFB game_engine skeleton + hair /
     cape / sword / tassel chains), clips idle + walk, glb, skins.

v2 (design/review-log.md 2026-09-26 "Vampwarrior v2 feedback"): lips re-anchored on the measured mouth slit (front-surface
profile; v1 sat ~1 cm low), eye liner = a fine band measured from the lid edge (LINER_W), hair rebuilt as unified masses
(HAIR_STYLE A / B / C; A default), CEL treatment (AO collapsed into flat tone bands in Col, rough, no specular, maps
unwired), inverted-hull outline shells (own meshes, skinned, glTF cull-back), clips de-stiffened (eased idle sway,
chest / head overlap, hair + cape follow-through as damped springs in periodic steady state), toe-dip fix.
    [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)

v3 (design/review-log.md 2026-09-26 "Vampwarrior v2 feedback" + "QUEUED" + "Vampwarrior v3 spec expanded"): the cel
treatment is UNDONE (no outline shells, no AO tone bands; the v1 material returns: Col x baked AO, baked normal map) on
the v2 base; the freed shell triangles go back into the cape / collar grids. Eye liner = a thin contour ON the lid edge
(the lid edge measured by front-view ray casts against the skin and the eyeball mesh, not a vertex heuristic), fangs
rooted inside / under the upper lip, the DRAWN stylized pass (authored shadow-shape regions, face dials toward the
sheet's graphic read, painted angled brows, a faint thin lip paint), armour overlays (breastplate + pauldrons,
silver-trimmed), and the front hair reworked into long separate solid strands over the shoulders onto the chest.
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
import vampwarrior_parts as VP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; metres; front -Y, left +X, floor z = 0 at the soles)
UNIT = "vampwarrior"
CHAR_ID = "vampwarrior"               # WORKING NAME -- the artist may want a proper one
TRI_BUDGET = [30000, 50000]           # declared tier: HERO (quality-tier law: hero = 30-50k)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- body (MPFB2)
MACRO = {"gender": 0.0, "age": 0.5, "muscle": 0.58, "weight": 0.36, "proportions": 1.0, "height": 0.75,
         "cupsize": 0.55, "firmness": 0.8}
RACE = {"caucasian": 0.9, "asian": 0.05, "african": 0.05}
TARGETS_V2 = {                        # v2's stylising dials (kept only to quote the v3 change in the report)
    "legs/upperlegs-height-incr": 0.35, "legs/lowerlegs-height-incr": 0.25, "torso/measure-waist-circ-decr": 0.45,
    "neck/measure-neck-height-incr": 0.35, "head/head-oval": 0.45, "cheek/l-cheek-bones-incr": 0.45,
    "cheek/r-cheek-bones-incr": 0.45, "cheek/l-cheek-volume-decr": 0.35, "cheek/r-cheek-volume-decr": 0.35,
    "eyebrows/eyebrows-angle-down": 0.55, "eyes/l-eye-height2-decr": 0.25, "eyes/r-eye-height2-decr": 0.25,
    "chin/chin-prominent-incr": 0.25, "nose/nose-point-width-decr": 0.3, "mouth/mouth-angles-down": 0.2}
TARGETS = {**TARGETS_V2,              # "face dials" (v3, toward the sheet's graphic read): SHARPER CHIN + jaw, larger eyes
           "chin/chin-prominent-incr": 0.40,     # chin projects further (v2 0.25)
           "chin/chin-width-decr": 0.55,         # narrower chin (new)
           "chin/chin-triangle": 0.60,           # pointed chin (new)
           "head/head-invertedtriangular": 0.35,  # V-line jaw: jaw narrows toward the chin (new)
           "cheek/l-cheek-volume-decr": 0.50, "cheek/r-cheek-volume-decr": 0.50,   # leaner cheeks (v2 0.35)
           "eyes/l-eye-scale-incr": 0.45, "eyes/r-eye-scale-incr": 0.45}          # slightly larger eyes (new)
BODY_H = 1.78                         # "height" barefoot (m); the heels add HEEL lift on top
HEEL_DEG = 24.0                       # "heel pitch": the foot pitched toes-down by this at rest (heeled boots)
SOLE_T = 0.010                        # boot sole thickness under the forefoot
# ---- painted garment lines (fractions along the bone they sit on)
BOOT_T = 0.46                         # "boot height": boot top this far up the thigh (knee 0 .. hip 1)
BOOT_PEAK = 0.035                     # the boot top rises this much at the front of the thigh (a pointed top)
GLOVE_T = 0.42                        # "glove height": glove top this far down the upper arm (shoulder 0 .. elbow 1)
ARMHOLE_IN = 0.035                    # the bodice armhole plane sits this far inside the shoulder joint
ARMHOLE_TILT = 0.25                   # armhole plane tilt (the line runs inward over the shoulder top)
NECKLINE_DZ = 0.07                    # bodice neckline (inside the collar): this far below the head joint (front) ...
NECKLINE_TILT = 0.45                  # ... rising toward the back by this x the depth
LEGLINE_DZ, LEGLINE_SLOPE = 0.0, 0.9  # leg opening: crotch + dz, rising with |x| (high cut; under the fauld)
TRIM_W = 0.009                        # "seam trim" width
SEAM_X = (0.052, 0.078)               # front princess seams: |x| at the underbust .. at the waist
UNDERBUST_DZ = -0.02                  # underbust trim: this far below the bust apex height
LIP = (0.022, 0.0060, 0.0070)         # "lipstick size" (v3 fainter + thinner): half width / upper height above the slit /
                                      #   lower depth below (v2 0.026 / 0.0105 / 0.0135)
LIP_DZ = 0.0                          # "lipstick up/down": extra shift of the painted lips off the measured mouth slit (m)
LIP_V2 = (0.026, 0.0105, 0.0135)      # v2's lip ellipse, kept only to quote the before-area
# eye liner (v3): a thin line ON the lid edge contour. The lid edge = per 5-deg ray fan from the front, the first radius
# out from the eye centre at which the skin is hit in front of the eyeball mesh (what a front view sees).
LINER_W = (0.0011, 0.0006)            # "eye liner width": upper lid / lower lid line width out from the lid edge (m)
LINER_WING = (0.0010, 22.0, 12.0)     # "liner wing": extra width at the outer corner (m), angular half-width (deg),
                                      #   centre angle above the outer corner (deg)
# brows (v3, painted): an angled stroke above each eye, inner end low (the sheet's intimidating scowl), tapering outward.
# control points (x = lateral from the eye centre in units of the eye's front half-width, z = height above the upper
# lid edge in m), stroke width at the inner end -> tail
BROW_PTS = ((-1.10, 0.0030), (-0.35, 0.0062), (0.55, 0.0098), (1.30, 0.0082))
BROW_W = (0.0030, 0.0007)             # "brow thickness": inner end / tail (m)
# ---- eyes + fangs
EYE_IRIS_DEG, EYE_PUPIL_DEG = 36.0, 13.0  # iris / pupil cone half-angles on the eyeball (seen from the front)
EYE_SEG = 20                          # eyeball segments round the view axis (v2 16)
FANG_LEN, FANG_R = 0.0055, 0.0019     # "fangs": visible length below the mouth slit, radius where they leave the lip
FANG_X = 0.0105                       # fang offset from the midline
FANG_TUCK = (0.0055, 0.0030)          # the root is tucked this far ABOVE the slit / this far BEHIND the upper lip's front
FANG_PROUD = 0.0006                   # the tip stands this far in front of the lower lip's surface
MOUTH_IN_D = 0.0025                   # lip-zone skin this far behind the front surface = inside the mouth (dark line)
# ---- v3 authored shadow shapes (anime-style: few, crisp iso-cut edges, a slightly darker tone; NOT derived from AO)
JAW_LIGHT_DEG = 42.0                  # "neck shadow length": the stylised key light's elevation above the horizon, from
                                      #   the front; the chin's cast shadow on the neck = skin that cannot see it
JAW_SMOOTH = 3                        # neighbour-averaging passes on the cast-shadow indicator before the cut
JAW_GATE = (0.008, 0.6, 0.012)        # never above the chin bottom + a, rising a slope b per metre of |x| beyond c (face)
FRINGE_D = (0.0065, 0.0105)           # "fringe shadow depth": band under the hairline at the centre / at the temples (m)
FRINGE_SCALLOP = (0.0040, 3.5)        # pointed lower edge (the strand ends): point depth (m), points per side
CHEST_SHADOW = (0.030, 0.045, 0.010)  # "chest shadow": depth under each breast (m), spread (m), depth at the centre (m)
# ---- v3 armour overlays (the sheet's armoured bodice; silver-trimmed plates)
BREASTPLATE = {"phi": 62.0, "nu": 17, "nv": 9, "clear": 0.0045, "t": 0.0035, "top_dz": -0.028, "side_drop": 0.055,
               "bot_dz": 0.002, "bridge": 0.6, "keel": 0.003}   # half-angle from the front (deg), grid, clearance, plate
                                      #   thickness, top edge below the collar, top edge drop at the sides, bottom edge
                                      #   above the underbust, cleavage bridging (0..1), centre keel height
PAULDRON = {"axis": (0.72, -0.12, 0.68), "psi": (68.0, 44.0), "nu": 16, "nv": 6, "clear": 0.007, "bulge": 0.013,
            "t": 0.0035, "lame": (0.84, 1.30, 80.0), "lame_clear": 0.004}   # outward axis (x per side), cap half-angle
                                      #   down the arm / elsewhere, grid, clearance, dome bulge, thickness, lower lame:
                                      #   polar span (x psi) + azimuth half-range round the arm (deg), its clearance
ARMOR_HIDE_MARGIN = 0.012             # bodice skin fully under the breastplate (this far inside its edge) is removed
# ---- boots
TOE_EXT = 0.035                       # "pointed toe": the toe cap reaches this far past the toes
TOE_MARGIN = 0.006                    # toe-cap clearance over the toes
HEEL_TIP = (0.011, 0.011)             # heel tip half sizes (x, y); heel top = the sole under the heel
KNEE_COP = (0.058, 0.080, 0.028, 0.006)   # "armoured knee": half width, half height, dome height, thickness
CUFF = (0.012, 0.006)                 # boot / glove cuff band: height, thickness
# ---- fauld (skirt plates) + belt
FAULD_TOP_DZ, FAULD_LEN = 0.035, 0.175    # plate tops this far below the waist line, plate length
FAULD_PLATES = [(0.0, 34.0, 1.12), (42.0, 30.0, 1.0), (-42.0, 30.0, 1.0), (82.0, 30.0, 0.95), (-82.0, 30.0, 0.95),
                (130.0, 36.0, 0.9), (-130.0, 36.0, 0.9), (180.0, 36.0, 0.9)]   # (azimuth from front deg, width deg, len k)
FAULD_CLEAR, FAULD_FLARE, FAULD_T = 0.012, 0.045, 0.0035
BELT_H, BELT_T = 0.032, 0.008
# ---- collar + gem
COLLAR_H = (0.045, 0.085)             # "high collar" height at the front / back
COLLAR_GAP, COLLAR_FLARE, COLLAR_T = 0.008, 0.028, 0.004
COLLAR_OPEN_DEG = 34.0                # the collar is open this wide at the front (the gem sits in the gap)
COLLAR_NU = 44                        # collar columns round the neck (v1 44; v2 34 funded the outline shell; v3 restored)
GEM_R = 0.0115
# ---- cape
CAPE_PHI = 78.0                       # "cape wrap": half-angle from the back centre at the shoulders (deg)
CAPE_PHI_HEM = 98.0                   # ... at the hem (it flares round the legs)
CAPE_TOP_DZ = (0.03, -0.04)           # top edge: at the back of the neck base (+ up) / at the shoulder tips
CAPE_HEM_Z = 0.13                     # "cape length": hem height (ankle)
CAPE_CLEAR, CAPE_FLARE = 0.028, 0.20  # clearance off the back, extra flare at the hem
CAPE_NU, CAPE_NV = 44, 26             # cape grid (v1 44 x 26; v2 36 x 20 funded the outline shell; v3 restored)
CAPE_TEAR = (0.20, 0.07)              # "torn hem": longest tear, shortest tear (m) -- hash-alternated per column
CAPE_SLITS = [(0.22, 0.55), (0.5, 0.42), (0.77, 0.6)]   # (column fraction, slit height as a fraction of the drop)
CAPE_T = 0.0045
CAPE_FOLD, CAPE_FOLDS = 0.022, 6.5    # "cape folds": depth at the hem, folds across the width
# ---- hair
HAIR_CAP_T = 0.011                    # "hair volume" on the scalp
PART_W, PART_DEPTH = 0.012, 0.005     # centre part groove
HAIRLINE = (0.055, -0.035)            # hairline above the brow (front) / at the nape relative to the head joint
LOCK_T = 0.016                        # lock half-thickness (a clump)
LOCK_RINGS = 36                       # "hair lock detail": cross-sections per lock
HAIR_TIP_Z = 1.05                     # "hair length": the back cascade ends at the waist
HAIR_SPREAD = (0.8, 0.95)             # the back cascade's fan across the back at the shoulders / lower (x its head angle)
# v2 hair: unified masses (clumps = closed solids that overlap into one mass, carved grooves between / down them,
# pointed clump tips that separate toward the ends). Artist picks A / B / C from the options sheet.
HAIR_STYLE = "A"                      # "hair option": A broad smooth masses / B chunkier ribbon locks / C v1 locks merged
# v3 (artist: "long strands in front ... even if there isnt many individual strands and its a solid piece"): style A's
# FRONT is rebuilt as HAIR_FRONT_N long solid strands per side that leave the face-framing mass below the jaw, cross in
# front of the shoulder and fall down the chest (not the v2 side curtains outside the shoulders); the back cascade is v2's.
HAIR_FRONT_N = 3                      # "front strands" per side
HAIR_FRONT = {"split": 0.42, "free_w": 0.82, "overlap": 0.25, "sub": 2, "sub_depth": 0.18, "T": 0.022, "tip_w": 0.08,
              "cols": 7}            # strands separate this far up (fraction of the length from the tip), free width (x own
                                      #   share), overlap on the scalp, strand grooves, thickness, tip width, columns
HAIR_FRONT_X = ((0.040, 0.160), (0.038, 0.150))   # the front strands' span |x| across the chest: at the bust top / bust
HAIR_FRONT_TIPS = (1.14, 1.04, 1.10)  # "front strand length": tip heights, inner / middle / outer strand (m)
HAIR_OPTS = {
    "A": {"kind": "mass", "front_clumps": 2, "back_clumps": 5, "cols": 7, "rows": 18, "T": 0.022, "edge_t": 0.35,
          "sub": 2, "sub_depth": 0.22, "overlap": 0.30, "split": 0.25, "tip_w": 0.10, "off": 0.003, "front": "strands"},
    "B": {"kind": "mass", "front_clumps": 3, "back_clumps": 7, "cols": 5, "rows": 18, "T": 0.024, "edge_t": 0.30,
          "sub": 1, "sub_depth": 0.0, "overlap": 0.10, "split": 0.60, "tip_w": 0.10, "off": 0.003},
    "C": {"kind": "locks", "w_k": 1.7, "t_k": 1.25},
}
HAIR_FRONT_TIP_Z = (1.12, 1.08, 1.15)  # front masses' tip heights across (inner / middle / outer edge)
HAIR_BACK_PSI = (108.0, 252.0)        # the back mass's span round the head (deg from the front; v1 locks 116 .. 244)
# ---- sword (in the sword's own frame; see vampwarrior_parts.sword)
SWORD = {"blade_len": 1.24, "blade_w": 0.072, "blade_w_at": 0.42, "base_w": 0.028, "tip_pow": 0.8, "thick": 0.0085,
         "edge_t": 0.0, "serr_n": 15, "serr_depth": 0.16, "serr_pow": 0.75, "vein_w": 0.16, "n_st": 40,
         "guard": (0.055, 0.016, 0.012), "grip_len": 0.24, "grip_r": 0.0165, "pommel_r": 0.022, "grip_wraps": 6}
TASSEL = {"cord_len": 0.05, "cord_r": 0.0035, "knot_r": 0.009, "strands": 7, "len": 0.11, "spread": 0.012, "strand_w": 0.006}
SWORD_REST = (-0.46, -0.08)           # rest pose: the sword stands point-down here (x, y), tip on the floor
# ---- bake
BAKE_RES = (2048, 1024)               # normal, AO texture sizes (hero tier)
CUT_SNAP = 0.12
SLIVER_AREA = 5e-8                    # body triangles under this area (m^2) after the cuts are collapsed (shortest edge)
# ---- material (v3: the v2 cel treatment is UNDONE -- artist: "the cel shading didnt work undo it")
SHADE_JITTER = False                  # v1's +-4 % per-face value jitter (realism noise): off -- the drawn regions stay clean
AO_FLOOR = {"default": 0.40, "skin": 0.62, "hair": 0.82}   # "AO strength": the baked AO never darkens below this
                                      #   (per region class) -- depth without black grime in hair overlaps / face cavities
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow"], "hair": ["hair", "hair_shade", "hair_part"]}
HAIR_BONES, CAPE_BONES, CAPE_CHAINS = 4, 4, 5
GRIP_CURL = (52.0, 68.0, 46.0)        # "grip": finger curl round the handle, per joint (deg)
GRIP_THUMB = (18.0, 26.0)             # thumb wrap (deg)
RELAX_CURL = (10.0, 16.0, 10.0)       # the free (left) hand's relaxed curl
# ---- idle (confident guard: sword planted point-down at her right, hand on the grip)
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH_DEG = 1.3                 # chest breath (pitch), one breath per loop
IDLE_SWAY = 0.007                     # pelvis side sway (m)
IDLE_WEIGHT = (-0.016, 2.6)           # contrapposto: pelvis shift toward the sword side (m) / hip roll (deg)
IDLE_REACH = 0.972                    # soft knees: legs at this x their full reach
IDLE_FEET = {"L": (0.035, -0.07, 9.0), "R": (-0.01, 0.03, 12.0)}   # foot offset from rest (x, y) + toes-out yaw (deg)
IDLE_HEAD = (5.0, 3.0)                # "glare": chin tucked (deg) / slow look yaw amplitude (deg)
IDLE_LARM = (36.0, -3.0, -24.0)       # free arm: lowered (deg), forward (deg), elbow bend (deg; < 0 straightens the
                                      #   MPFB rest's 39 deg bend)
SWORD_IDLE_TIP = (-0.50, -0.26)       # the planted sword's tip (x, y) on the floor
SWORD_IDLE_LEAN = (3.0, -3.0)         # its lean (deg): top toward +X (her) / toward -Y (front; negative = back to her)
SWORD_IDLE_SWAY = 0.6                 # breath sway of the planted sword about its tip (deg)
IDLE_HAIR_DEG = (0.6, 2.0)            # hair drift (root, tip deg)
IDLE_CAPE_DEG = (0.4, 1.8)            # cape drift
# ---- walk (assertive stride, in place; sword trailing in the right hand)
WALK_N = 30                           # "walk cycle": 1.25 s per stride (2 steps) = 96 steps/min
WALK_STANCE = 0.6                     # stance fraction of the cycle
WALK_STEP_A = 0.29                    # "stride length": half the stance travel (m)
WALK_LIFT = 0.075                     # swing foot lift (m)
WALK_TOEOFF = (0.13, 26.0)            # toe-off: last fraction of the cycle in stance / max heel-up roll (deg)
WALK_FOOT_X = 0.72                    # feet land this x their rest width (a narrow, assertive line)
WALK_REACH = 0.982
WALK_PELVIS = (6.0, 4.0, 0.018)       # pelvis yaw (deg), roll (deg), side sway (m)
WALK_CHEST = (5.0, 2.5)               # chest counter-yaw (deg), chest up / lean back (deg)
WALK_LARM = (34.0, 15.0, -20.0)       # free arm: lowered (deg), swing (deg), elbow bend (deg)
SWORD_WALK = {"out_x": -0.30, "down": 34.0, "out": 32.0, "swing": 3.0, "fore": 0.03}   # sword carry: the hanging arm's
                                      #   outward lean, the blade's angle below horizontal / out from straight back (deg)
WALK_ARM_REACH = 0.965                # the sword arm hangs at this x its full length
WALK_CAPE = (11.0, 1.4, 4.5)          # cape trail (deg), flutter root / tip (deg)
WALK_HAIR = (4.0, 1.2, 3.5)           # back-hair trail (deg), bounce root / tip (deg)
# ---- v2 de-stiffening (artist standing note: "the stiffness we see in the biped models")
IDLE_EASE = 1.8                       # "idle sway ease": weight shifts settle and hold (tanh-shaped wave; 0 = pure sine)
IDLE_OVERLAP = (3.0, 6.0)             # chest / head follow the pelvis sway this many frames late (overlap)
WALK_OVERLAP = (2.0, 4.0)             # walk: chest counter-yaw / head lag behind the pelvis by this many frames
WALK_NOD = (1.4, 0.9)                 # head nod / chest bob with the step (deg, at 2 x the stride), lagged as above
# follow-through: each hair / cape bone's world rotation is the periodic steady state of a damped spring driven by
# its driver bone's rotation + the chain root's acceleration (a pendulum's drag); bones cascade down the chain
FT = {"hair_front": {"driver": "head", "hz": 3.0, "zeta": 0.5, "drag": 0.8, "gain": 1.0},
      "hair_back": {"driver": "head", "hz": 2.6, "zeta": 0.45, "drag": 1.0, "gain": 1.0},
      "cape": {"driver": "spine_03", "hz": 2.4, "zeta": 0.5, "drag": 1.3, "gain": 1.0}}
FT_INTO_BODY_DEG = 1.5                # soft limit on a chain's swing back INTO the body (deg)
FT_DRIFT_K = 0.6                    # the v1 air-drift waves kept at this share on top of the follow-through

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
SCRATCH = argv[argv.index("--scratch") + 1] if "--scratch" in argv else None   # exploration: every output goes here
OUT_IMPROVED = os.path.join(SCRATCH or os.path.join(ROOT, "improved"), UNIT + ".blend")
OUT_RIGGED = os.path.join(SCRATCH or os.path.join(ROOT, "rigged"), UNIT + ".blend")
OUT_GLB = os.path.join(SCRATCH or os.path.join(ROOT, "rigged"), UNIT + ".glb")
TEX_DIR = os.path.join(SCRATCH or os.path.join(ROOT, "improved"), "textures")
TAG = "twin" if DIGEST_ONLY else ("scratch" if SCRATCH else "main")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "name_status": "WORKING NAME (artist may rename)",
          "source": "none: from the artist's three-view sheet (transcribed in design/review-log.md 2026-09-26)",
          "tier": "hero", "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


def mesh_arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


# =========================================================================== 1. MPFB2 body
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
assert addon_utils.enable("bl_ext.user_default.mpfb", default_set=True, handle_error=None) is not None
from bl_ext.user_default.mpfb.services.humanservice import HumanService      # noqa: E402
from bl_ext.user_default.mpfb.services.targetservice import TargetService    # noqa: E402
from bl_ext.user_default.mpfb.services.locationservice import LocationService  # noqa: E402
t_ = time.time()
macro = TargetService.get_default_macro_info_dict()
macro.update(MACRO)
macro["race"] = dict(RACE)
hb = HumanService.create_human(macro_detail_dict=macro)
tdir = LocationService.get_mpfb_data("targets")
for rel, w in TARGETS.items():
    p_ = os.path.join(tdir, rel + ".target.gz")
    assert os.path.exists(p_), p_
    TargetService.load_target(hb, p_, weight=w)
rig0 = HumanService.add_builtin_rig(hb, "game_engine", import_weights=True)
for m in hb.modifiers:
    if m.type == "MASK":
        m.show_viewport = False; m.show_render = False
bpy.context.view_layer.update()


def eval_hb():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = hb.evaluated_get(dg)
    me_ = ev.to_mesh()
    co = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


V_rest0 = eval_hb()
me0 = hb.data
gi_body = hb.vertex_groups["body"].index
NV0 = len(me0.vertices)
GRP = {g.index: g.name for g in hb.vertex_groups}
MB = [b.name for b in rig0.data.bones]            # MPFB bone order
MBI = {n: i for i, n in enumerate(MB)}
W0 = np.zeros((NV0, len(MB)))
in_body = np.zeros(NV0, bool)
helper = {}
for v in me0.vertices:
    for g in v.groups:
        nm = GRP[g.group]
        if nm in MBI:
            W0[v.index, MBI[nm]] = g.weight
        elif g.group == gi_body and g.weight > 0.5:
            in_body[v.index] = True
        elif nm in ("helper-l-eye", "helper-r-eye", "helper-upper-teeth", "joint-mouth") and g.weight > 0.5:
            helper.setdefault(nm, []).append(v.index)
Hbody = float(V_rest0[in_body, 2].max() - V_rest0[in_body, 2].min())
SCALE = BODY_H / Hbody
# ---- heel pose through the MPFB rig: foot pitched toes-down about the ankle; the toes counter-rotated flat
for side in ("l", "r"):
    b = rig0.data.bones["foot_" + side]
    M3 = np.array(b.matrix_local)[:3, :3]
    f = np.array(b.tail_local) - np.array(b.head_local)
    fh = unit([f[0], f[1], 0.0])
    Rh = K._rot(np.cross([0.0, 0.0, 1.0], fh), math.radians(HEEL_DEG))
    pb = rig0.pose.bones["foot_" + side]
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = Matrix((M3.T @ Rh @ M3).tolist()).to_quaternion()
    bb = rig0.data.bones["ball_" + side]
    Mb = np.array(bb.matrix_local)[:3, :3]
    pbb = rig0.pose.bones["ball_" + side]
    pbb.rotation_mode = "QUATERNION"
    pbb.rotation_quaternion = Matrix((Mb.T @ Rh.T @ Mb).tolist()).to_quaternion()
bpy.context.view_layer.update()
V_all = eval_hb() * SCALE
BREST = {}
for pb in rig0.pose.bones:
    Mp = np.array(pb.matrix)
    BREST[pb.name] = {"head": np.array(pb.head) * SCALE, "tail": np.array(pb.tail) * SCALE, "z": Mp[:3, 2].copy()}
body_idx = np.nonzero(in_body)[0]
LIFT = SOLE_T - float(V_all[body_idx, 2].min())
V_all[:, 2] += LIFT
for b in BREST.values():
    b["head"][2] += LIFT; b["tail"][2] += LIFT
report["mpfb"] = {"macro": {k: v for k, v in macro.items()}, "targets": TARGETS,
                  "face_dials_v2_to_v3": {k: [TARGETS_V2.get(k, 0.0), TARGETS.get(k, 0.0)]
                                          for k in sorted(set(TARGETS) | set(TARGETS_V2))
                                          if TARGETS_V2.get(k, 0.0) != TARGETS.get(k, 0.0)}, "rig": "game_engine",
                  "mpfb_bones": len(MB), "scale_to_body_h": round(SCALE, 5), "body_h_barefoot": BODY_H,
                  "heel_deg": HEEL_DEG, "heel_lift": round(LIFT - SOLE_T, 4), "seconds": round(time.time() - t_, 1)}
# landmarks
EYE = {}
for side, nm in (("L", "helper-l-eye"), ("R", "helper-r-eye")):
    P_ = V_all[helper[nm]]
    c_ = P_.mean(0)
    EYE[side] = {"c": c_, "r": float(np.linalg.norm(P_ - c_, axis=1).mean())}
MOUTH = V_all[helper["joint-mouth"]].mean(0)
TEETH = V_all[helper["helper-upper-teeth"]]
# body mesh (faces fully inside the 'body' group)
remap = -np.ones(NV0, dtype=np.int64); remap[body_idx] = np.arange(len(body_idx))
BF = []
for p in me0.polygons:
    vs = list(p.vertices)
    if all(in_body[vs]):
        BF.append([int(remap[i]) for i in vs])
BV = V_all[body_idx].copy()
BW = W0[body_idx].copy()
del_objs = [hb, rig0]
for o in del_objs:
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
DOM = np.argmax(BW, 1)
DOMN = np.array([MB[j] for j in DOM], dtype=object)


def bone_set(prefixes, side=None):
    out = []
    for n in MB:
        base = n
        if side is not None and not (n.endswith("_" + side)):
            continue
        if any(base.startswith(p) for p in prefixes):
            out.append(n)
    return out


def dom_in(names):
    return np.isin(DOMN, list(names))


def P(n, key="head"):
    return BREST[n][key].copy()


HIP = {s: P("thigh_" + s.lower()) for s in "LR"}
KNEE = {s: P("calf_" + s.lower()) for s in "LR"}
ANKLE = {s: P("foot_" + s.lower()) for s in "LR"}
BALL = {s: P("ball_" + s.lower()) for s in "LR"}
TOE = {s: P("ball_" + s.lower(), "tail") for s in "LR"}
SHO = {s: P("upperarm_" + s.lower()) for s in "LR"}
ELB = {s: P("lowerarm_" + s.lower()) for s in "LR"}
WRI = {s: P("hand_" + s.lower()) for s in "LR"}
NECK0 = P("neck_01"); HEADJ = P("head")
PELVIS = P("pelvis")
ARM_B = {s: dom_in([n for n in MB if n.endswith("_" + s.lower()) and not n.startswith(("clavicle", "thigh", "calf", "foot", "ball"))])
         for s in "LR"}
LEG_B = {s: dom_in(["thigh_" + s.lower(), "calf_" + s.lower(), "foot_" + s.lower(), "ball_" + s.lower()]) for s in "LR"}
HEAD_B = dom_in(["head"])
Z_TOP = float(BV[:, 2].max())
face_front_y = float(BV[HEAD_B, 1].min())
report["landmarks"] = {"height_total": round(Z_TOP, 4), "hip_z": round(float(HIP["L"][2]), 4),
                       "knee_z": round(float(KNEE["L"][2]), 4), "shoulder_z": round(float(SHO["L"][2]), 4),
                       "neck_z": round(float(NECK0[2]), 4), "head_joint_z": round(float(HEADJ[2]), 4),
                       "eye_L": EYE["L"]["c"].round(4).tolist(), "eye_r": round(EYE["L"]["r"], 4),
                       "mouth": MOUTH.round(4).tolist(), "ankle_z": round(float(ANKLE["L"][2]), 4)}
print("BODY", json.dumps({"verts": len(BV), "tris": tri_count_F(BF), **report["landmarks"]}))

# =========================================================================== 2. body BVHs + profiles
F_all = BF
fcen = np.array([BV[f].mean(0) for f in BF])
fdom = np.array([np.bincount(DOM[f], minlength=len(MB)).argmax() for f in BF])
fdomn = np.array([MB[j] for j in fdom], dtype=object)
BVH_BODY = BVHTree.FromPolygons(BV.tolist(), BF)
is_arm_f = np.array([n.startswith(("upperarm", "lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")) for n in fdomn])
near_sho = np.zeros(len(BF), bool)
for s in "LR":
    near_sho |= np.linalg.norm(fcen - SHO[s], axis=1) < 0.085
TORSO_F = [f for f, a, ns, n in zip(BF, is_arm_f, near_sho, fdomn) if (not a or ns) and n not in ("head",)]
BVH_TORSO = BVHTree.FromPolygons(BV.tolist(), TORSO_F)


def radial_profile(bvh, ax, ay, phi_deg, zs, rmax=0.9):
    """outermost surface radius from the vertical axis (ax, ay) along azimuth phi (0 = +Y back, +90 = +X left)."""
    ph = math.radians(phi_deg)
    d = np.array([math.sin(ph), math.cos(ph), 0.0])
    out = np.zeros(len(zs))
    for k, z in enumerate(zs):
        o = Vector((ax + d[0] * rmax, ay + d[1] * rmax, z))
        hit, _, _, dist = bvh.ray_cast(o, Vector((-d[0], -d[1], 0.0)), rmax)
        out[k] = rmax - dist if hit is not None else 0.0
    return out


AX_Y = float(PELVIS[1])                          # the torso's vertical axis (x = 0, y = AX_Y)
# the triangle BVH used for barycentric weight transfer (the body's MPFB weights -> any point on / near the skin)
TRI, TRI_F = [], []
for fi, f in enumerate(BF):
    for k in range(1, len(f) - 1):
        TRI.append([f[0], f[k], f[k + 1]]); TRI_F.append(fi)
TRI = np.array(TRI)
BVH_TRI = BVHTree.FromPolygons(BV.tolist(), TRI.tolist())


def transfer(Ps, W=None, V=None):
    """MPFB weights at the nearest skin point (barycentric on the nearest triangle) for every point in Ps."""
    W = BW if W is None else W
    V = BV if V is None else V
    Ps = np.asarray(Ps, float)
    loc = np.empty_like(Ps); ti = np.empty(len(Ps), dtype=np.int64)
    for k, p in enumerate(Ps):
        l_, _, i_, _ = BVH_TRI.find_nearest(Vector(p))
        loc[k] = l_; ti[k] = i_
    a, b, c = V[TRI[ti, 0]], V[TRI[ti, 1]], V[TRI[ti, 2]]
    v0, v1, v2 = b - a, c - a, loc - a
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-20)
    bv = (d11 * d20 - d01 * d21) / den; bw = (d00 * d21 - d01 * d20) / den
    bu = 1.0 - bv - bw
    B = np.clip(np.stack([bu, bv, bw], 1), 0, 1); B /= B.sum(1, keepdims=True)
    return B[:, 0:1] * W[TRI[ti, 0]] + B[:, 1:2] * W[TRI[ti, 1]] + B[:, 2:3] * W[TRI[ti, 2]]


# ---- derived landmarks
def slab(mask, zlo, zhi):
    return mask & (BV[:, 2] >= zlo) & (BV[:, 2] <= zhi)


torso_v = ~(ARM_B["L"] | ARM_B["R"]) & ~HEAD_B
bust_m = slab(torso_v, SHO["L"][2] - 0.26, SHO["L"][2] - 0.05) & (np.abs(BV[:, 0]) > 0.03) & (np.abs(BV[:, 0]) < 0.14)
Z_BUST = float(BV[bust_m][np.argmin(BV[bust_m, 1]), 2])
Y_BUST = float(BV[bust_m, 1].min())
_zs = np.linspace(HIP["L"][2] + 0.02, Z_BUST - 0.03, 40)
_wd = [np.abs(BV[slab(torso_v, z - 0.006, z + 0.006), 0]).max() for z in _zs]
Z_WAIST = float(_zs[int(np.argmin(_wd))])
crotch_m = slab(torso_v, KNEE["L"][2], HIP["L"][2] + 0.05) & (np.abs(BV[:, 0]) < 0.012)
Z_CROTCH = float(BV[crotch_m, 2].min())
Z_UB = Z_BUST + UNDERBUST_DZ
# mouth slit (v2 fix): v1 took the most recessed midline VERTEX in the band, which was an inner-mouth vertex ~1 cm under
# the real slit (the lipstick sat on the lower lip + chin). v2 reads the FRONT SURFACE only: a +Y ray per 0.25 mm down the
# midline; the two most forward bulges in the band are the upper and lower lips, the slit is the most recessed point
# between them.
_zt = TEETH[:, 2].min()
_zs_l = np.arange(_zt - 0.020, _zt + 0.022, 0.00025)
_yp = []
for z_ in _zs_l:
    h_ = BVH_BODY.ray_cast(Vector((0.0, -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0)
    _yp.append(h_[0][1] if h_[0] is not None else np.nan)
_yp = np.array(_yp)
_ypf = np.convolve(np.nan_to_num(_yp, nan=np.nanmax(_yp)), np.ones(5) / 5.0, mode="same")
_mins = [i for i in range(3, len(_ypf) - 3) if _ypf[i] <= _ypf[i - 1] and _ypf[i] <= _ypf[i + 1]]
_mins = sorted(sorted(_mins, key=lambda i: _ypf[i])[:2])
_i_slit = _mins[0] + int(np.argmax(_ypf[_mins[0]:_mins[1] + 1]))
Z_SLIT = float(_zs_l[_i_slit])
Z_LIP = Z_SLIT + LIP_DZ
Y_LIP = float(np.nanmin(_yp[np.abs(_zs_l - Z_SLIT) < 0.003]))
report["lip_fix"] = {"v1_rule_z": None, "slit_z": round(Z_SLIT, 4), "lip_dz": LIP_DZ,
                     "lower_lip_bulge_z": round(float(_zs_l[_mins[0]]), 4), "upper_lip_bulge_z": round(float(_zs_l[_mins[1]]), 4),
                     "teeth_edge_z": round(float(_zt), 4)}
mid = (np.abs(BV[:, 0]) < 0.004) & HEAD_B & (BV[:, 2] > MOUTH[2] - 0.045) & (BV[:, 2] < MOUTH[2] + 0.02) & (BV[:, 1] < face_front_y + 0.045)
_mz = BV[mid]
_band = _mz[(_mz[:, 2] > TEETH[:, 2].min() - 0.012) & (_mz[:, 2] < TEETH[:, 2].min() + 0.014)]
report["lip_fix"]["v1_rule_z"] = round(float(_band[np.argmax(_band[:, 1]), 2]), 4)
report["lip_fix"]["shift_up_m"] = round(Z_LIP - report["lip_fix"]["v1_rule_z"], 4)
# chin bottom (v3): a +Y ray per 0.25 mm down the midline from the slit; the chin bottom is the last sample before the
# ray passes under the jaw (the hit jumps back > 8 mm onto the neck)
_zc_s = np.arange(Z_SLIT, Z_SLIT - 0.09, -0.00025)
_yc_s = np.array([(lambda h: h[0][1] if h[0] is not None else np.nan)(BVH_BODY.ray_cast(Vector((0.0, -0.6, float(z_))),
                                                                                         Vector((0.0, 1.0, 0.0)), 1.0)) for z_ in _zc_s])
_jump = np.nonzero(np.diff(np.nan_to_num(_yc_s, nan=9.0)) > 0.008)[0]
Z_CHIN = float(_zc_s[_jump[0]]) if len(_jump) else float(_zc_s[-1])
Y_CHIN = float(_yc_s[_jump[0]]) if len(_jump) else float(np.nanmin(_yc_s))
_hf = HEAD_B & (BV[:, 1] < float(BV[HEAD_B, 1].mean()))
report["chin"] = {"chin_bottom_z": round(Z_CHIN, 4), "chin_front_y": round(Y_CHIN, 4),
                  "slit_to_chin_m": round(Z_SLIT - Z_CHIN, 4),
                  "jaw_half_width_m": {"chin+%dmm" % d: round(float(np.abs(BV[_hf & (np.abs(BV[:, 2] - Z_CHIN - d / 1000.0) < 0.0015), 0]).max()), 4)
                                       for d in (5, 10, 20, 35) if (_hf & (np.abs(BV[:, 2] - Z_CHIN - d / 1000.0) < 0.0015)).any()},
                  "rule": "chin bottom = last midline front-ray hit before the ray passes under the jaw; jaw half-width = "
                          "max |x| of front-half head-dominant skin in a 3 mm slab at that height (the front silhouette)"}
_scalp = HEAD_B & (BV[:, 2] > EYE["L"]["c"][2])
HC = np.array([0.0, 0.5 * (BV[_scalp, 1].min() + BV[_scalp, 1].max()), EYE["L"]["c"][2]])
HR = np.array([np.abs(BV[_scalp, 0]).max(), 0.5 * (BV[_scalp, 1].max() - BV[_scalp, 1].min()), Z_TOP - HC[2]])
report["landmarks"].update({"bust_z": round(Z_BUST, 4), "waist_z": round(Z_WAIST, 4), "crotch_z": round(Z_CROTCH, 4),
                            "underbust_z": round(Z_UB, 4), "lip_line_z": round(Z_LIP, 4), "lip_front_y": round(Y_LIP, 4),
                            "head_centre": HC.round(4).tolist(), "head_radii": HR.round(4).tolist()})
print("LANDMARKS", json.dumps(report["landmarks"]))

# =========================================================================== 3. body regions (iso-cut fields)
FIELDS = {}
X, Y, Z = BV[:, 0], BV[:, 1], BV[:, 2]
for s, sg in (("L", 1.0), ("R", -1.0)):
    # boot top: a plane across the thigh at BOOT_T, peaking at the front
    zb = KNEE[s][2] + BOOT_T * (HIP[s][2] - KNEE[s][2])
    ta = unit(HIP[s] - KNEE[s])
    tt = np.clip(((BV - KNEE[s]) @ ta) / np.linalg.norm(HIP[s] - KNEE[s]), 0, 1)
    axis_p = KNEE[s][None] + np.outer(tt, HIP[s] - KNEE[s])
    dd = BV - axis_p
    front = np.clip(-dd[:, 1] / np.maximum(np.linalg.norm(dd[:, :2], axis=1), 1e-9), 0, 1)
    FIELDS["boot_" + s] = Z - (zb + BOOT_PEAK * front ** 2)
    # glove top: across the upper arm
    ua = ELB[s] - SHO[s]
    FIELDS["glove_" + s] = (BV - SHO[s]) @ unit(ua) - GLOVE_T * np.linalg.norm(ua)
    # armhole plane (normal out along the arm root, tilted up: the line runs inward over the shoulder top)
    an = unit([sg, 0.0, ARMHOLE_TILT])
    FIELDS["arm_" + s] = (BV - (SHO[s] - an * ARMHOLE_IN)) @ an
    FIELDS["legm_" + s] = LEG_B[s].astype(float)
    FIELDS["armm_" + s] = ARM_B[s].astype(float)
    FIELDS["side_" + s] = (sg * X > 0).astype(float)
FIELDS["neck"] = Z - (HEADJ[2] - NECKLINE_DZ + NECKLINE_TILT * (Y - HEADJ[1]))
FIELDS["headm"] = HEAD_B.astype(float)
FIELDS["leg"] = Z - (Z_CROTCH + LEGLINE_DZ + LEGLINE_SLOPE * np.abs(X))
_sx = np.interp(Z, [Z_WAIST - 0.10, Z_UB], [SEAM_X[1], SEAM_X[0]])
FIELDS["seam"] = np.abs(X) - _sx
FIELDS["bseam"] = np.abs(X) - 0.048
FIELDS["z"] = Z.copy()
FIELDS["y"] = Y.copy()
FIELDS["lip"] = (X / LIP[0]) ** 2 + ((Z - Z_LIP) / np.where(Z > Z_LIP, LIP[1], LIP[2])) ** 2 - 1.0
# eye liner (v3): a thin line ON the lid-edge contour. v2 took the lid edge as the innermost 'visible' skin vertex per
# 10-deg wedge (visible = on / outside the eyeball SPHERE with 0.5 mm tolerance); where the lower lid sits inside the
# sphere that estimate jumped outward and the band became a vertical dark wedge under the eye. v3 measures what a front
# view actually sees: per 5-deg ray fan round each eye, +Y rays at 0.1 mm radius steps; the lid edge at that angle is
# the first radius at which the SKIN is hit in front of the EYEBALL MESH (the faceted lathe that is rendered). The liner
# is the band [edge, edge + width(angle)] outward of it (+ everything inside it, which the eyeball hides).
BN = VP.vertex_normals(BV, BF)
if float(np.mean(np.einsum("ij,ij->i", BN, BV - BV.mean(0)))) < 0:
    BN = -BN
EYE_MESH, EYE_AP = {}, {}
AP_STEP, AP_N = 0.0001, 72
for s in "LR":
    e = EYE[s]
    angs = [0.0, EYE_PUPIL_DEG, EYE_PUPIL_DEG + 4, EYE_IRIS_DEG, 60.0, 90.0, 125.0, 155.0, 180.0]
    prof = [(e["r"] * math.sin(math.radians(a)), e["r"] * math.cos(math.radians(a))) for a in angs]
    regs = ["eye_pupil", "eye_iris", "eye_iris", "eye_sclera", "eye_sclera", "eye_sclera", "eye_sclera", "eye_sclera"]
    EYE_MESH[s] = VP.lathe(prof, regs, EYE_SEG, e["c"], (0.0, -1.0, 0.0), up_hint=(0, 0, 1))


def eye_polar(P, s):
    """front-projection polar coords round the eye centre: radius, angle (0 = the outer corner, 90 = up, 180 = inner)."""
    c_ = EYE[s]["c"]
    d_ = P[:, [0, 2]] - c_[[0, 2]]
    rad_ = np.hypot(d_[:, 0], d_[:, 1])
    ang_ = np.degrees(np.arctan2(d_[:, 1], d_[:, 0] * (1.0 if s == "L" else -1.0))) % 360.0
    return rad_, ang_


def front_first_hit(s, bvh_skin, bvh_eye, x, z):
    """+Y ray at (x, z): 'skin' / 'eye' / None -- which surface a front view sees first."""
    hs = bvh_skin.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    he = bvh_eye.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    if hs[0] is None and he[0] is None:
        return None, hs
    if he[0] is None or (hs[0] is not None and hs[3] < he[3] - 1e-6):
        return "skin", hs
    return "eye", he


BVH_EYE = {s: BVHTree.FromPolygons(EYE_MESH[s][0].tolist(), EYE_MESH[s][1]) for s in "LR"}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    ap_ = np.zeros(AP_N)
    for k in range(AP_N):
        th = math.radians(360.0 * k / AP_N)
        for i in range(1, 300):
            rho = i * AP_STEP
            who, _ = front_first_hit(s, BVH_BODY, BVH_EYE[s], c_[0] + sg_ * rho * math.cos(th), c_[2] + rho * math.sin(th))
            if who == "skin":
                ap_[k] = rho
                break
    EYE_AP[s] = ap_


def aperture(ang_, s):
    return np.interp(ang_, 360.0 * np.arange(AP_N) / AP_N, EYE_AP[s], period=360.0)


def liner_w(ang_):
    """liner width per angle: the upper-lid width over the top half, the lower-lid width under it (smooth at the corners),
    + the outer-corner wing."""
    ang_ = np.asarray(ang_, float)
    up_ = smoothstep(-0.25, 0.25, np.sin(np.radians(ang_)))
    dw_ = np.minimum(np.abs(ang_ - LINER_WING[2]) % 360.0, 360.0 - np.abs(ang_ - LINER_WING[2]) % 360.0)
    return LINER_W[1] + (LINER_W[0] - LINER_W[1]) * up_ + LINER_WING[0] * np.exp(-(dw_ / LINER_WING[1]) ** 2)


for s in "LR":
    rad_, ang_ = eye_polar(BV, s)
    gate_ = (np.linalg.norm(BV - EYE[s]["c"], axis=1) < EYE[s]["r"] + 0.012) & (Y < EYE[s]["c"][1] + 0.002) & HEAD_B
    FIELDS["eye_" + s] = np.where(gate_, rad_ - aperture(ang_, s) - liner_w(ang_), 1.0)
# brows (v3): a painted stroke per eye, the distance field of a Catmull-Rom curve through BROW_PTS (front projection,
# mirrored per side) minus the tapering half width
BROW_CURVE = {}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    half_w = 0.5 * (aperture(0.0, s) + aperture(180.0, s))
    top = float(aperture(90.0, s))
    pts = np.array([[c_[0] + sg_ * px * half_w, 0.0, c_[2] + top + pz] for px, pz in BROW_PTS])
    cur = VP.resample(VP.catmull(pts, 16), 80)[0]
    BROW_CURVE[s] = cur


def brow_field(P, s):
    cur = BROW_CURVE[s][:, [0, 2]]
    q = P[:, [0, 2]]
    best = np.full(len(P), 1e9); tb = np.zeros(len(P))
    for i in range(len(cur) - 1):
        a_, b_ = cur[i], cur[i + 1]
        ab = b_ - a_
        t_ = np.clip(((q - a_) @ ab) / float(ab @ ab), 0, 1)
        d_ = np.linalg.norm(q - (a_ + t_[:, None] * ab), axis=1)
        m_ = d_ < best
        best[m_] = d_[m_]; tb[m_] = (i + t_[m_]) / (len(cur) - 1)
    hw = 0.5 * (BROW_W[0] + (BROW_W[1] - BROW_W[0]) * tb ** 0.8)
    return best - hw


for s in "LR":
    gate_ = HEAD_B & (Y < EYE[s]["c"][1] + 0.03) & (np.abs(X - EYE[s]["c"][0]) < 0.04) & (Z > EYE[s]["c"][2]) & \
        (Z < EYE[s]["c"][2] + 0.035) & (BN[:, 1] < -0.2)
    FIELDS["brow_" + s] = np.where(gate_, brow_field(BV, s), 1.0)
# authored shadow shapes (v3)
# (1) jaw / neck: the chin's CAST shadow under a stylised key light (JAW_LIGHT_DEG above the horizon, from the front):
# skin whose ray toward the light hits the head. The 0/1 indicator is neighbour-smoothed and cut at 0.5 (a crisp,
# smooth edge); gated off the face (never above the chin bottom + JAW_GATE) and off the back of the neck.
_el = math.radians(JAW_LIGHT_DEG)
L_JAW = np.array([0.0, -math.cos(_el), math.sin(_el)])
BVH_HEADONLY = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n == "head"])
_jg = (Z < Z_CHIN + JAW_GATE[0] + JAW_GATE[1] * np.maximum(np.abs(X) - JAW_GATE[2], 0.0)) & (Z > NECK0[2] - 0.06) & \
    (Y < NECK0[1] + 0.02) & ~(ARM_B["L"] | ARM_B["R"])
_cast = np.zeros(len(BV))
for i in np.nonzero(_jg)[0]:
    o_ = Vector(BV[i] + BN[i] * 0.001)
    if BVH_HEADONLY.ray_cast(o_, Vector(L_JAW), 0.3)[0] is not None:
        _cast[i] = 1.0
_bedges = set()
for f in BF:
    for k in range(len(f)):
        a_, b_ = f[k], f[(k + 1) % len(f)]
        _bedges.add((min(a_, b_), max(a_, b_)))
_bedges = np.array(sorted(_bedges))
_bdeg = np.bincount(_bedges.ravel(), minlength=len(BV)).astype(float)
for _ in range(JAW_SMOOTH):
    acc_ = np.zeros(len(BV))
    np.add.at(acc_, _bedges[:, 0], _cast[_bedges[:, 1]]); np.add.at(acc_, _bedges[:, 1], _cast[_bedges[:, 0]])
    _cast = 0.5 * _cast + 0.5 * acc_ / np.maximum(_bdeg, 1.0)
FIELDS["jawsh"] = np.where(_jg, 0.5 - _cast, 1.0)          # < 0 = in the chin's shadow
FIELDS["jawgate"] = _jg.astype(float)
# (2) fringe: a band under the hairline over the forehead / temples, its lower edge scalloped like strand ends
_fx = np.abs(X) / 0.07
_fd = FRINGE_D[0] + (FRINGE_D[1] - FRINGE_D[0]) * np.clip(_fx, 0, 1) ** 1.5 + \
    FRINGE_SCALLOP[0] * (1.0 - np.abs(2.0 * ((FRINGE_SCALLOP[1] * np.clip(_fx, 0, 1.2) + 0.5) % 1.0) - 1.0))  # pointed
# (3) chest: under the bust onto the bodice, two crescents meeting at the centre
_bx = float(np.abs(BV[bust_m][np.argmin(BV[bust_m, 1]), 0]))
_cd = (CHEST_SHADOW[2] + (CHEST_SHADOW[0] - CHEST_SHADOW[2]) * np.exp(-((np.abs(X) - _bx) / CHEST_SHADOW[1]) ** 2)) * \
    (1.0 - smoothstep(_bx + 0.5 * CHEST_SHADOW[1], _bx + 1.4 * CHEST_SHADOW[1], np.abs(X)))   # closes before the flank
FIELDS["chestsh"] = Z - (Z_UB - TRIM_W * 0.5 - _cd)
# hairline: front above the brows, sides above the ears, nape at the back
_ce = (HC[1] - Y) / np.maximum(np.hypot(X, Y - HC[1]), 1e-9)          # 1 = front, -1 = back
_hl = np.where(_ce >= 0, np.interp(_ce, [0.0, 0.55, 1.0], [EYE["L"]["c"][2] + 0.018, EYE["L"]["c"][2] + 0.040,
                                                           EYE["L"]["c"][2] + HAIRLINE[0]]),
               np.interp(_ce, [-1.0, -0.35, 0.0], [HEADJ[2] + HAIRLINE[1], HEADJ[2] + HAIRLINE[1] + 0.015,
                                                   EYE["L"]["c"][2] + 0.018]))
FIELDS["hair"] = Z - _hl
FIELDS["fringe"] = Z - (_hl - _fd)                  # > 0 (and hair < 0) = the fringe shadow band under the hairline
FIELDS["ce"] = _ce
FIELDS["absx"] = np.abs(X)

bm = bmesh.new()
for p in BV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in BF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None, snap=CUT_SNAP):
    L = LAY[key]
    eps = 1e-6 * max(1.0, abs(tau))
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
        try:
            bmesh.ops.connect_verts(bm, verts=cv)
        except Exception:
            pass
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts), "face_connects": len(pairs)}


def g_and(*conds):
    return lambda a, b: all(c(a) and c(b) for c in conds)


def gv(key, lo=-1e9, hi=1e9):
    L = LAY[key]
    return lambda v: lo <= v[L] <= hi


CUTS = []
for s in "LR":
    CUTS.append(("boot_" + s, 0.0, g_and(gv("legm_" + s, 0.5))))
    CUTS.append(("glove_" + s, 0.0, g_and(gv("armm_" + s, 0.5))))
    for tau in (0.0, -TRIM_W):
        CUTS.append(("arm_" + s, tau, g_and(gv("side_" + s, 0.5), gv("z", SHO[s][2] - 0.22, SHO[s][2] + 0.12), gv("headm", -1, 0.5))))
CUTS.append(("neck", 0.0, g_and(gv("headm", -1, 0.5), gv("z", NECK0[2] - 0.05, HEADJ[2] + 0.06))))
CUTS.append(("leg", 0.0, g_and(gv("z", Z_CROTCH - 0.04, HIP["L"][2] + 0.25))))
for tau in (-TRIM_W / 2, TRIM_W / 2):
    CUTS.append(("seam", tau, g_and(gv("y", -1, AX_Y), gv("z", Z_WAIST - 0.10, Z_UB + 0.004), gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5))))
    CUTS.append(("bseam", tau, g_and(gv("y", AX_Y, 1), gv("z", Z_WAIST - 0.10, Z_UB + 0.06), gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5))))
    CUTS.append(("z", Z_UB + tau, g_and(gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5), gv("headm", -1, 0.5))))
CUTS.append(("lip", 0.0, g_and(gv("headm", 0.5), gv("y", -1, Y_LIP + 0.03), gv("z", Z_LIP - 0.03, Z_LIP + 0.03))))
for s in "LR":
    CUTS.append(("eye_" + s, 0.0, g_and(gv("headm", 0.5), gv("eye_" + s, -1, 0.03))))
    CUTS.append(("brow_" + s, 0.0, g_and(gv("headm", 0.5), gv("brow_" + s, -1, 0.03))))
CUTS.append(("jawsh", 0.0, g_and(gv("jawgate", 0.5))))
CUTS.append(("fringe", 0.0, g_and(gv("headm", 0.5), gv("ce", 0.1), gv("hair", -0.04, 0.002))))
CUTS.append(("chestsh", 0.0, g_and(gv("y", -1, AX_Y), gv("z", Z_UB - 0.08, Z_UB), gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5))))
CUTS.append(("hair", 0.0, g_and(gv("headm", 0.5))))
for tau in (PART_W * 0.5,):
    CUTS.append(("absx", tau, g_and(gv("hair", 0.0), gv("y", -1, HC[1] + 0.02))))
def refine_thin(key, exact, passes=2, samples=11):
    """a stroke thinner than the mesh edges can pass BETWEEN vertices (no sign change on any edge -> no iso cut). Split
    every gated edge whose interior dips below 0 while both ends are outside, at the dip, with the EXACT field value at
    the new vertex (other fields interpolated), so the stroke gets inside vertices and the cut closes round it."""
    L = LAY[key]
    n_split = 0
    for _ in range(passes):
        todo = []
        for e in bm.edges:
            a, b = e.verts
            if not (0.0 <= a[L] <= 0.03 and 0.0 <= b[L] <= 0.03):
                continue
            pa, pb = np.array(a.co), np.array(b.co)
            ts = np.linspace(0.05, 0.95, samples)
            vals = exact(pa[None] + (pb - pa)[None] * ts[:, None])
            k = int(np.argmin(vals))
            if vals[k] < 0:
                todo.append((e, a, b, float(ts[k])))
        for e, a, b, tt in todo:
            vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
            _, nv = bmesh.utils.edge_split(e, a, tt)
            for k in LAY:
                nv[LAY[k]] = vals[k]
            nv[L] = float(exact(np.array(nv.co)[None])[0])
        n_split += len(todo)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        if not todo:
            break
    return n_split


t_ = time.time()
REFINE = {s: refine_thin("brow_" + s, lambda P_, s=s: brow_field(P_, s)) for s in "LR"}
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
# v3: two cuts crossing near a vertex (mouth corners, lid corners) leave sub-0.1 mm slivers whose flat-shaded normals
# are garbage (black specks in the render): dissolve them
_ndeg = len(bm.faces)
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
for _ in range(3):
    _sl = {}
    for f in bm.faces:
        if f.calc_area() < SLIVER_AREA:
            e_ = min(f.edges, key=lambda e: e.calc_length())
            _sl[e_.index if e_.is_valid else id(e_)] = e_
    bm.edges.index_update()
    if not _sl:
        break
    _seen, _es = set(), []
    for e_ in _sl.values():                          # one collapse per vertex per pass (no chained merges)
        if e_.is_valid and not (set(e_.verts) & _seen):
            _es.append(e_); _seen |= set(e_.verts)
    bmesh.ops.collapse(bm, edges=_es, uvs=False)
_ndeg -= len(bm.faces)
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FV = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
nF = len(CF)
reg = np.array(["skin"] * nF, dtype=object)
_head = FV["headm"] > 0.5
_armL, _armR = FV["armm_L"] > 0.5, FV["armm_R"] > 0.5
_arm = _armL | _armR
_legL, _legR = FV["legm_L"] > 0.5, FV["legm_R"] > 0.5
_armhole_out = ((FV["arm_L"] > 0) & (FV["side_L"] > 0.5)) | ((FV["arm_R"] > 0) & (FV["side_R"] > 0.5))
bodice = (~_head) & (FV["neck"] < 0) & ~_armhole_out & (FV["leg"] > 0)
reg[bodice] = "bodice"
trim = bodice & (((FV["arm_L"] > -TRIM_W) & (FV["side_L"] > 0.5) & (FV["z"] > SHO["L"][2] - 0.22)) |
                 ((FV["arm_R"] > -TRIM_W) & (FV["side_R"] > 0.5) & (FV["z"] > SHO["R"][2] - 0.22)) |
                 ((np.abs(FV["seam"]) < TRIM_W / 2) & (FV["y"] < AX_Y) & (FV["z"] > Z_WAIST - 0.10) & (FV["z"] < Z_UB)) |
                 ((np.abs(FV["bseam"]) < TRIM_W / 2) & (FV["y"] > AX_Y) & (FV["z"] > Z_WAIST - 0.10) & (FV["z"] < Z_UB + 0.06)) |
                 ((np.abs(FV["z"] - Z_UB) < TRIM_W / 2)))
reg[trim & ~_arm] = "trim"
glove = (_armL & (FV["glove_L"] > 0)) | (_armR & (FV["glove_R"] > 0))
reg[glove] = "gloves"
boot = (_legL & (FV["boot_L"] < 0)) | (_legR & (FV["boot_R"] < 0))
reg[boot] = "boots"
# v3 authored shadow shapes (skin / bodice only; everything painted later wins over them)
SH_MASK = {"jaw_neck": (reg == "skin") & (FV["jawgate"] > 0.5) & (FV["jawsh"] < 0),
           "fringe": (reg == "skin") & _head & (FV["hair"] < 0) & (FV["fringe"] > 0) & (FV["ce"] > 0.1) &
                     (FV["z"] > EYE["L"]["c"][2] + 0.016),
           "chest": (reg == "bodice") & (FV["y"] < AX_Y) & (FV["chestsh"] > 0) & (FV["z"] < Z_UB - TRIM_W * 0.5) & ~_arm}
for k_, m_ in SH_MASK.items():
    reg[m_] = "bodice_shadow" if k_ == "chest" else "skin_shadow"
reg[_head & (FV["lip"] < 0) & (FV["y"] < Y_LIP + 0.03) & (np.abs(FV["z"] - Z_LIP) < 0.03)] = "lips"
# v3: inside the mouth (a lip face whose centre sits > MOUTH_IN_D behind the lips' most forward surface at its x, from
# the front profile) reads as the dark mouth line between the faint lips, not as more lip paint
_mx = np.arange(-LIP[0] - 0.002, LIP[0] + 0.0021, 0.0005)
_mfront = []
for x_ in _mx:
    ys_ = [BVH_BODY.ray_cast(Vector((float(x_), -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0)[0]
           for z_ in np.arange(Z_SLIT - 0.008, Z_SLIT + 0.008, 0.0005)]
    _mfront.append(min(h[1] for h in ys_ if h is not None))
_mfront = np.array(_mfront)
for fi in np.nonzero(reg == "lips")[0]:
    c_ = CV[CF[fi]].mean(0)
    if c_[1] > float(np.interp(c_[0], _mx, _mfront)) + MOUTH_IN_D:
        reg[fi] = "mouth"
reg[_head & ((FV["eye_L"] < 0) | (FV["eye_R"] < 0))] = "liner"
reg[_head & ((FV["brow_L"] < 0) | (FV["brow_R"] < 0)) & (FV["hair"] < 0)] = "brow"
reg[_head & (FV["hair"] > 0)] = "hair"
reg[_head & (FV["hair"] > 0) & (FV["absx"] < PART_W * 0.5) & (FV["y"] < HC[1] + 0.02)] = "hair_part"
report["iso_cuts"] = {"cuts": len(cut_log), "edge_splits": int(sum(c["edge_splits"] for c in cut_log)),
                      "thin_stroke_refine_splits": REFINE, "degenerate_faces_dissolved": int(_ndeg),
                      "seconds": round(time.time() - t_, 1), "body_tris_after_cuts": nF}
print("CUTS", json.dumps(report["iso_cuts"]))
CW = transfer(CV)                                   # body weights on the cut mesh (exact on the split edges)
# liner geometry proof (v3): the PAINTED cut mesh seen from the front. Per 5-deg ray fan round each eye, +Y rays every
# 0.05 mm out to 20 mm: the visible liner = the rays whose first hit is a 'liner' face (not the eyeball, not other skin).
# A contour line = liner visible at (nearly) every angle, starting right at the lid edge, a thin width everywhere.
BVH_CUT = BVHTree.FromPolygons(CV.tolist(), CF)
_lp = {}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    rows_ = []
    for k in range(AP_N):
        th = math.radians(360.0 * k / AP_N)
        seen, edge_, first_ = [], None, None
        for i in range(1, 400):
            rho = i * 0.00005
            who, h_ = front_first_hit(s, BVH_CUT, BVH_EYE[s], c_[0] + sg_ * rho * math.cos(th), c_[2] + rho * math.sin(th))
            if who == "skin":
                r_ = reg[h_[2]]
                if edge_ is None:
                    edge_, first_ = rho, r_
                seen.append(r_ == "liner")
            elif edge_ is not None:
                seen.append(False)
        seen = np.array(seen, bool)
        rows_.append({"deg": 360.0 * k / AP_N, "width_mm": 0.05 * float(seen.sum()),
                      "starts_at_lid_edge": first_ == "liner",
                      "run_mm": 0.05 * float(max((len(x) for x in "".join("1" if v else "0" for v in seen).split("0")), default=0))})
    _lp[s] = rows_
_all = [r for s in "LR" for r in _lp[s]]
_up = [r["width_mm"] for r in _all if 15.0 <= r["deg"] <= 165.0]
_lo = [r["width_mm"] for r in _all if 195.0 <= r["deg"] <= 345.0]
report["liner_proof"] = {
    "rule": "front view, per 5-deg ray fan round each eye, +Y rays every 0.05 mm: visible liner width = rays whose first hit "
            "is a liner face; the lid edge = the first ray that sees skin in front of the eyeball mesh",
    "widths_mm_target": {"upper": LINER_W[0] * 1000, "lower": LINER_W[1] * 1000, "wing": LINER_WING[0] * 1000},
    "upper_lid_width_mm": {"median": round(float(np.median(_up)), 2), "max": round(float(np.max(_up)), 2)},
    "lower_lid_width_mm": {"median": round(float(np.median(_lo)), 2), "max": round(float(np.max(_lo)), 2)},
    "max_visible_width_any_angle_mm": round(float(max(r["width_mm"] for r in _all)), 2),
    "contour_closed_pct": round(100.0 * float(np.mean([r["width_mm"] >= 0.2 for r in _all])), 1),
    "starts_at_lid_edge_pct": round(100.0 * float(np.mean([r["starts_at_lid_edge"] for r in _all])), 1),
    "aperture_mm": {s: {"outer": round(1000 * float(aperture(0.0, s)), 2), "up": round(1000 * float(aperture(90.0, s)), 2),
                        "inner": round(1000 * float(aperture(180.0, s)), 2), "down": round(1000 * float(aperture(270.0, s)), 2)}
                    for s in "LR"},
    "per_angle_L": [(round(r["deg"]), round(r["width_mm"], 2)) for r in _lp["L"]]}
# brow placement (front projection, relative to the eye centre; L eye, mirrored for R)
_bm = np.array([reg_ == "brow" for reg_ in reg])
_bc = np.array([CV[f].mean(0) for f in CF])
_bl = _bm & (_bc[:, 0] > 0)
report["brow"] = {"points_rule": "BROW_PTS: x in eye front half-widths from the eye centre (lateral +), z in m above the "
                                 "upper lid edge; Catmull-Rom stroke, width BROW_W inner -> tail",
                  "BROW_PTS": [list(p) for p in BROW_PTS], "BROW_W_mm": [w * 1000 for w in BROW_W],
                  "span_x_mm_from_eye_centre": [round(1000 * float(_bc[_bl, 0].min() - EYE["L"]["c"][0]), 1),
                                                round(1000 * float(_bc[_bl, 0].max() - EYE["L"]["c"][0]), 1)],
                  "span_z_mm_above_eye_centre": [round(1000 * float(_bc[_bl, 2].min() - EYE["L"]["c"][2]), 1),
                                                 round(1000 * float(_bc[_bl, 2].max() - EYE["L"]["c"][2]), 1)],
                  "inner_end_z_mm": round(1000 * float(BROW_CURVE["L"][0, 2] - EYE["L"]["c"][2]), 1),
                  "tail_z_mm": round(1000 * float(BROW_CURVE["L"][-1, 2] - EYE["L"]["c"][2]), 1),
                  "slant_deg_inner_to_peak": round(math.degrees(math.atan2(
                      float(BROW_CURVE["L"][50, 2] - BROW_CURVE["L"][0, 2]), float(BROW_CURVE["L"][50, 0] - BROW_CURVE["L"][0, 0]))), 1),
                  "stroke_centre_above_upper_lid_mm_at_eye_centre": round(1000 * float(
                      np.interp(EYE["L"]["c"][0], BROW_CURVE["L"][:, 0], BROW_CURVE["L"][:, 2]) - EYE["L"]["c"][2]
                      - aperture(90.0, "L")), 1),
                  "area_cm2_per_brow": round(1e4 * 0.5 * float(sum(
                      0.5 * np.linalg.norm(np.cross(CV[f[1]] - CV[f[0]], CV[f[2]] - CV[f[0]])) for f, m in zip(CF, _bm) if m)), 3)}
print("LINER", json.dumps({k: v for k, v in report["liner_proof"].items() if k != "per_angle_L"}), "LIP", json.dumps(report["lip_fix"]))
print("BROW", json.dumps(report["brow"]))
_fa_cut = np.array([0.5 * np.linalg.norm(np.cross(CV[f[1]] - CV[f[0]], CV[f[2]] - CV[f[0]])) for f in CF])
report["shadow_shapes"] = {
    "rule": "authored, not AO: palette regions with crisp iso-cut edges in a darker tone (skin_shadow / bodice_shadow)",
    "shapes": {k: {"region": "bodice_shadow" if k == "chest" else "skin_shadow",
                   "area_cm2": round(1e4 * float(_fa_cut[m & np.isin(reg, ["skin_shadow", "bodice_shadow"])].sum()), 2),
                   "faces": int((m & np.isin(reg, ["skin_shadow", "bodice_shadow"])).sum())} for k, m in SH_MASK.items()},
    "jaw_neck": {"light_elevation_deg": JAW_LIGHT_DEG, "light_dir": L_JAW.round(4).tolist(), "smooth_passes": JAW_SMOOTH,
                 "rule": "the chin's cast shadow: skin whose ray toward the stylised key light hits the head, "
                         "neighbour-smoothed, cut at 0.5; gated below the chin bottom (+ the jaw rise) and off the nape"},
    "fringe": {"depth_m": list(FRINGE_D), "scallop": list(FRINGE_SCALLOP),
               "rule": "a band under the hairline on the forehead / temples, lower edge scalloped"},
    "chest": {"depth_m": list(CHEST_SHADOW), "bust_apex_x": round(_bx, 4),
              "rule": "under the underbust trim onto the bodice: two crescents under the breasts meeting at the centre"}}
print("SHADOWS", json.dumps(report["shadow_shapes"]["shapes"]))

# =========================================================================== 4. parts
PARTS = []          # dicts: name, V, F, R, w ('transfer' | 'rigid:<bone>' | 'rigid_transfer' | callable), + chain info


def add_part(name, V, F, R, w="transfer", obj="main", **kw):
    d = {"name": name, "V": np.asarray(V, float), "F": [list(map(int, f)) for f in F], "R": list(R), "w": w, "obj": obj}
    d.update(kw)
    assert len(d["F"]) == len(d["R"]), name
    PARTS.append(d)
    return d


# ---- eyes: spheres looking forward (-Y), pupil + iris cones around the view axis
for s in "LR":
    V_, F_, R_ = EYE_MESH[s]                    # built in section 3 (the liner's lid edge is measured against it)
    add_part("eye." + s, V_, F_, R_, w="rigid:head")
# ---- fangs (v3): rooted INSIDE the mouth, tucked up behind the upper lip; they emerge from the slit and hang in front of
# the lower lip (v2's cones started on the upper lip's surface and lay over both lips). Per fang, the front-surface
# profile at the fang's x (+Y rays every 0.25 mm) gives the local slit (the most recessed point between the lips).
FANG_INFO = {}
for s, sg in (("L", 1.0), ("R", -1.0)):
    xf = sg * FANG_X
    zs_ = np.arange(Z_SLIT - 0.012, Z_SLIT + 0.012, 0.00025)
    yp_ = np.array([(lambda h: h[0][1] if h[0] is not None else np.nan)(BVH_BODY.ray_cast(Vector((xf, -0.6, float(z_))),
                                                                                         Vector((0.0, 1.0, 0.0)), 1.0)) for z_ in zs_])
    ypf_ = np.convolve(np.nan_to_num(yp_, nan=np.nanmax(yp_)), np.ones(5) / 5.0, mode="same")
    band_ = np.abs(zs_ - Z_SLIT) < 0.004
    zsl = float(zs_[band_][np.argmax(ypf_[band_])])
    ysl = float(np.interp(zsl, zs_, yp_))
    z_root = zsl + FANG_TUCK[0]
    root = np.array([xf, float(np.interp(z_root, zs_, yp_)) + FANG_TUCK[1], z_root])
    z_tip = zsl - FANG_LEN
    tip = np.array([xf * 0.97, float(np.interp(z_tip, zs_, yp_)) - FANG_PROUD, z_tip])
    ax = unit(root - tip)
    Lf = float(np.linalg.norm(root - tip))
    k_sl = (zsl - z_tip) / max(root[2] - z_tip, 1e-9)            # the slit's fraction along the fang (tip 0 .. root 1)
    prof_ = [(0.0, Lf), (FANG_R * 0.9, Lf * 0.92), (FANG_R, Lf * k_sl), (FANG_R * 0.62, Lf * k_sl * 0.45), (0.0, 0.0)]
    V_, F_, R_ = VP.lathe(prof_, ["fang"] * 4, 6, tip, ax)
    add_part("fang." + s, V_, F_, R_, w="rigid:head")
    # proof: the fang's front-most generator line vs the lip surface -- hidden above the slit, proud below it
    zz_ = np.linspace(z_tip + 0.0003, root[2] - 0.0003, 40)
    tt_ = (zz_ - z_tip) / (root[2] - z_tip)
    rr_ = np.interp(tt_ * Lf, [0.0, Lf * k_sl * 0.45, Lf * k_sl, Lf * 0.92, Lf], [0.0, FANG_R * 0.62, FANG_R, FANG_R * 0.9, 0.0])
    yfront_ = tip[1] + tt_ * (root[1] - tip[1]) - rr_ / max(math.sqrt(ax[1] ** 2 + ax[2] ** 2), 1e-9) * abs(ax[2])
    ysurf_ = np.interp(zz_, zs_, yp_)
    # the upper lip's lower edge at this x: down from the upper-lip bulge, the first sample where the front ray falls
    # > 3 mm back (into the parted mouth); the fang must be hidden above it and seen below it
    _iu = int(np.nanargmin(np.where(zs_ > zsl, yp_, np.nan)))
    _z_ul = zsl
    for i_ in range(_iu, 0, -1):
        if not np.isnan(yp_[i_ - 1]) and yp_[i_ - 1] > yp_[_iu] + 0.003:
            _z_ul = float(zs_[i_]); break
    above_, below_ = zz_ > _z_ul + 0.0003, zz_ < _z_ul - 0.0003
    FANG_INFO[s] = {"slit_z": round(zsl, 4), "slit_front_y": round(ysl, 4), "root": root.round(4).tolist(), "tip": tip.round(4).tolist(),
                    "upper_lip_edge_z": round(_z_ul, 4), "root_above_lip_edge_mm": round(1000 * (root[2] - _z_ul), 2),
                    "visible_len_mm": round(1000 * (_z_ul - z_tip), 2),
                    "hidden_above_lip_edge_pct": round(100.0 * float(np.mean(yfront_[above_] > ysurf_[above_])), 1),
                    "in_front_below_lip_edge_pct": round(100.0 * float(np.mean(yfront_[below_] < ysurf_[below_])), 1)}
report["fangs"] = {"rule": "root tucked FANG_TUCK above the slit / behind the upper lip's front surface; tip FANG_LEN below "
                           "the slit, FANG_PROUD in front of the lower lip; proof along the fang's front-most line vs the "
                           "lip surface profile at the fang's x", **FANG_INFO}
print("FANGS", json.dumps(report["fangs"]))

# ---- boots: pointed toe cap + sole (loft along the foot) + stiletto heel
BOOT_INFO = {}
for s in "LR":
    fm = LEG_B[s] & np.isin(DOMN, ["foot_" + s.lower(), "ball_" + s.lower()])
    Pf = BV[fm]
    fd = unit([TOE[s][0] - ANKLE[s][0], TOE[s][1] - ANKLE[s][1], 0.0])       # foot direction (horizontal)
    lat = unit(np.cross([0.0, 0.0, 1.0], fd))                                   # +lat = toward the foot's left
    sp = (Pf - ANKLE[s]) @ fd
    s0, s1 = float(sp.min()), float(sp.max())
    st = np.linspace(s0, s1 + TOE_EXT, 16)
    rows, sole_rows = [], []
    prof = []
    for k, sk in enumerate(st):
        sl = Pf[np.abs(sp - min(sk, s1 - 0.004)) < 0.012]
        lc = (sl - ANKLE[s]) @ lat
        c_lat = 0.5 * (lc.min() + lc.max())
        hw = 0.5 * (lc.max() - lc.min())
        zb, zt = float(sl[:, 2].min()), float(sl[:, 2].max())
        prof.append((sk, c_lat, hw, zb, zt))
    Vt, Ft, Rt = [], [], []
    toe_rings = []
    ball_s = float((BALL[s] - ANKLE[s]) @ fd)
    for (sk, c_lat, hw, zb, zt) in prof:
        if sk < ball_s - 0.035:
            continue
        over = max(0.0, sk - s1)
        tpt = 1.0 - (over / TOE_EXT) ** 0.9 if over > 0 else 1.0                # the toe point
        w_ = (hw + TOE_MARGIN) * max(tpt, 0.06) * (0.9 if over > 0 else 1.0)
        top = (zt + TOE_MARGIN) if over == 0 else (zb + (zt - zb) * 0.45 * max(tpt, 0.1) + TOE_MARGIN * 0.5)
        bot = 0.0 if sk > ball_s - 0.01 else zb - SOLE_T
        cen = ANKLE[s] + fd * sk + lat * c_lat
        ring = []
        for a in np.linspace(0, 2 * math.pi, 12, endpoint=False):
            ca, sa = math.cos(a), math.sin(a)
            zz = (0.5 * (top + bot) + 0.5 * (top - bot) * sa) if sa > 0 else (0.5 * (top + bot) + 0.5 * (top - bot) * sa ** 3)
            ring.append([cen[0] + lat[0] * w_ * ca, cen[1] + lat[1] * w_ * ca, max(zz, 0.0)])
        toe_rings.append(np.array(ring))
    V_, F_, R_ = VP.loft(toe_rings, "flat", "pole", reg="boots")
    C0b = np.array([BALL[s][0], BALL[s][1], 0.0])
    add_part("toecap." + s, V_, F_, R_, w="toe", side=s, fd=fd, c0=C0b)
    # sole: a slab under the whole foot, forefoot on the floor, rising to the heel
    sole = []
    heel_s = s0 + 0.012
    for (sk, c_lat, hw, zb, zt) in prof:
        if sk > s1 + TOE_EXT * 0.6:
            continue
        zt_ = zb + 0.004
        zb_ = 0.0 if sk > ball_s - 0.01 else max(0.0, zb - SOLE_T)
        w_ = hw + TOE_MARGIN * 0.6
        if sk > s1:
            w_ *= max(0.15, 1.0 - ((sk - s1) / TOE_EXT) ** 1.6)
        cen = ANKLE[s] + fd * sk + lat * c_lat
        ring = [cen + lat * w_ * x + np.array([0, 0, 1.0]) * 0 for x in ()]
        ring = np.array([[cen[0] + lat[0] * w_ * x, cen[1] + lat[1] * w_ * x, z] for x, z in
                         ((-1.0, zb_), (1.0, zb_), (1.0, zt_), (-1.0, zt_))])
        sole.append(ring)
    V_, F_, R_ = VP.loft(sole, "flat", "flat", reg="boot_sole")
    add_part("sole." + s, V_, F_, R_, w="toe", side=s, fd=fd, c0=C0b)
    # stiletto heel: from the sole under the heel to the floor
    hk = prof[0]
    hc = ANKLE[s] + fd * (s0 + 0.018) + lat * hk[1]
    ztop = hk[3] - SOLE_T * 0.5
    hx, hy = HEEL_TIP
    rings = []
    for zz, k in ((ztop, 2.1), (ztop * 0.55, 1.35), (0.0, 1.0)):
        rings.append(np.array([[hc[0] + lat[0] * hx * k * x + fd[0] * hy * k * y, hc[1] + lat[1] * hx * k * x + fd[1] * hy * k * y, zz]
                               for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]))
    V_, F_, R_ = VP.loft(rings[::-1], "flat", "flat", reg="heel")
    add_part("heel." + s, V_, F_, R_, w="rigid:foot_" + s.lower())
    BOOT_INFO[s] = {"heel_top_z": round(float(ztop), 4), "foot_len": round(s1 - s0, 4), "ball_s": round(ball_s, 4)}
# ---- knee cops (domed plates over the knee front) + boot / glove cuffs
for s, sg in (("L", 1.0), ("R", -1.0)):
    la = unit(HIP[s] - ANKLE[s])
    kf = unit(np.cross(la, [1.0, 0.0, 0.0]))
    kf = -kf if kf[1] > 0 else kf                                               # knee front (-Y)
    hit = BVH_BODY.ray_cast(Vector(KNEE[s] + kf * 0.3), Vector(-kf), 0.3)
    kp = np.array(hit[0]) if hit[0] is not None else KNEE[s] + kf * 0.05
    hw, hh, dh, th = KNEE_COP
    prof = [(0.0, dh), (0.45, dh * 0.86), (0.8, dh * 0.5), (1.0, 0.0), (1.0, -th), (0.8, dh * 0.5 - th), (0.45, dh * 0.86 - th), (0.0, dh - th)]
    prof = [(r * hw, h) for r, h in prof]
    V_, F_, R_ = VP.lathe(prof, ["knee_cop", "knee_cop", "knee_cop", "cuff", "knee_cop", "knee_cop", "knee_cop"], 14,
                          kp - kf * (dh * 0.55), kf, up_hint=la, su=hh / hw, sv=1.0)
    add_part("kneecop." + s, V_, F_, R_, w="rigid_transfer")


def ring_band(centre, axis, pts_fn, n, height, thick, reg_out, reg_rim):
    """closed band: a strip round an axis (radius per angle from pts_fn(angle, h)), solidified."""
    a, u, v = VP.frame_from_axis(axis, (0.0, 1.0, 0.0) if abs(axis[1]) < 0.9 else (1.0, 0.0, 0.0))
    V_ = []
    for h in (-0.5 * height, 0.5 * height):
        for k in range(n):
            t = 2 * math.pi * k / n
            d = math.cos(t) * u + math.sin(t) * v
            V_.append(np.asarray(centre) + a * h + d * pts_fn(d, h))
    F_ = VP.grid_faces(n, 2, closed_u=True)
    V_ = np.array(V_)
    F_ = [f if np.dot(np.cross(V_[f[1]] - V_[f[0]], V_[f[2]] - V_[f[0]]), V_[f].mean(0) - centre - a * np.dot(V_[f].mean(0) - centre, a)) > 0
          else f[::-1] for f in F_]
    return VP.solidify(V_, F_, thick, 0.0015, reg_out, reg_out, reg_rim)


def skin_radius(centre, axis, bvh):
    def fn(d, h):
        o = np.asarray(centre) + np.asarray(axis) * h
        hit = bvh.ray_cast(Vector(o + d * 0.2), Vector(-d), 0.2)
        return (0.2 - hit[3]) if hit[0] is not None else 0.05
    return fn


LIMB_BVH = {}
for s in "LR":
    lo_ = s.lower()
    LIMB_BVH["leg" + s] = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n in ("thigh_" + lo_, "calf_" + lo_)])
    LIMB_BVH["arm" + s] = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n in ("upperarm_" + lo_, "lowerarm_" + lo_)])
for s in "LR":
    zb = KNEE[s][2] + BOOT_T * (HIP[s][2] - KNEE[s][2])
    t_b = (zb - KNEE[s][2]) / (HIP[s][2] - KNEE[s][2])
    cb = KNEE[s] + t_b * (HIP[s] - KNEE[s])
    ab = unit(HIP[s] - KNEE[s])
    V_, F_, R_ = ring_band(cb + ab * (CUFF[0] * 0.5 - 0.002), ab, skin_radius(cb, ab, LIMB_BVH["leg" + s]), 24, CUFF[0],
                           CUFF[1], "cuff", "cuff")
    add_part("bootcuff." + s, V_, F_, R_, w="transfer")
    ua = unit(ELB[s] - SHO[s])
    cg = SHO[s] + GLOVE_T * (ELB[s] - SHO[s])
    V_, F_, R_ = ring_band(cg - ua * (CUFF[0] * 0.5 - 0.002), ua, skin_radius(cg, ua, LIMB_BVH["arm" + s]), 20, CUFF[0],
                           CUFF[1] * 0.8, "cuff", "cuff")
    add_part("glovecuff." + s, V_, F_, R_, w="transfer")

# ---- fauld (hanging skirt plates) + belt
Z_BELT = HIP["L"][2] + 0.045
_zf = np.linspace(Z_BELT + 0.02, Z_CROTCH - 0.12, 40)
_rf = {}


def r_hang_fauld(phi_front, z):
    key = round(phi_front, 3)
    if key not in _rf:
        prof = radial_profile(BVH_TORSO, 0.0, AX_Y, phi_front + 180.0, _zf)
        _rf[key] = np.maximum.accumulate(prof)
    return float(np.interp(-z, -_zf, _rf[key]))


FAULD_INFO = []
for k, (pc, pw, lk) in enumerate(FAULD_PLATES):
    nu, nv = 7, 6
    ztop = Z_BELT - FAULD_TOP_DZ * 0.2
    Lp = FAULD_LEN * lk
    Vg = []
    for j in range(nv):
        v = j / (nv - 1)
        for i in range(nu):
            u = i / (nu - 1)
            ph = pc + pw * (u - 0.5)
            point = 0.035 * (1.0 - abs(2 * u - 1)) if k == 0 else 0.012 * (1.0 - abs(2 * u - 1))
            z = ztop - (Lp + point) * v
            r = r_hang_fauld(ph, z) + FAULD_CLEAR + FAULD_FLARE * v ** 1.2 + 0.004 * (k % 2)
            a = math.radians(ph + 180.0)
            Vg.append([r * math.sin(a), AX_Y + r * math.cos(a), z])
    Vg = np.array(Vg)
    Fg = VP.grid_faces(nu, nv)
    c0 = Vg.mean(0)
    Fg = [f if np.dot(np.cross(Vg[f[1]] - Vg[f[0]], Vg[f[2]] - Vg[f[0]]), np.array([Vg[f].mean(0)[0], Vg[f].mean(0)[1] - AX_Y, 0.0])) > 0
          else f[::-1] for f in Fg]
    Rg = ["fauld_trim" if (fi // (nu - 1)) == nv - 2 else "fauld" for fi in range(len(Fg))]
    V_, F_, R_ = VP.solidify(Vg, Fg, FAULD_T, FAULD_T, Rg, "fauld", "fauld_trim")
    vrow = np.tile(np.repeat(np.linspace(0, 1, nv), nu), 2)
    add_part("fauld.%d" % k, V_, F_, R_, w="fauld", v_param=vrow, phi=pc)
    FAULD_INFO.append({"azimuth": pc, "width_deg": pw, "length": round(Lp, 3)})
# belt: a band round the hips over the plate tops
nb_ = 40
Vb = []
for h in (Z_BELT - BELT_H * 0.5, Z_BELT + BELT_H * 0.5):
    prof_ = [radial_profile(BVH_TORSO, 0.0, AX_Y, 360.0 * i / nb_, [h - 0.01, h, h + 0.01]).max() for i in range(nb_)]
    for i in range(nb_):
        a = math.radians(360.0 * i / nb_)
        r = prof_[i] + 0.005
        Vb.append([r * math.sin(a), AX_Y + r * math.cos(a), h])
Vb = np.array(Vb)
Fb = VP.grid_faces(nb_, 2, closed_u=True)
Fb = [f if np.dot(np.cross(Vb[f[1]] - Vb[f[0]], Vb[f[2]] - Vb[f[0]]), np.array([Vb[f].mean(0)[0], Vb[f].mean(0)[1] - AX_Y, 0])) > 0
      else f[::-1] for f in Fb]
V_, F_, R_ = VP.solidify(Vb, Fb, BELT_T, 0.002, "belt", "belt", "trim")
add_part("belt", V_, F_, R_, w="transfer")
# buckle plate at the front
V_, F_, R_ = VP.gem(np.array([0.0, float(Vb[:, 1].min()) - BELT_T - 0.002, Z_BELT]), (0, -1, 0), 0.022, 0.006, n=6, up_hint=(0, 0, 1))
add_part("buckle", V_, F_, ["trim"] * len(F_), w="rigid_transfer")

# ---- collar (standing, open at the front) + throat gem
NECK_F = [f for f, n in zip(BF, fdomn) if n in ("neck_01", "spine_03", "head", "clavicle_l", "clavicle_r")]
BVH_NECK = BVHTree.FromPolygons(BV.tolist(), NECK_F)
Z_C0 = NECK0[2] - 0.012
nu_c, nv_c = COLLAR_NU, 6
_zc = np.linspace(Z_C0, Z_C0 + COLLAR_H[1] + 0.01, 24)
Vc = []
for j in range(nv_c):
    v = j / (nv_c - 1)
    for i in range(nu_c):
        u = i / (nu_c - 1)
        phf = COLLAR_OPEN_DEG * 0.5 + (360.0 - COLLAR_OPEN_DEG) * u        # from the front, round the back
        back = 0.5 - 0.5 * math.cos(math.radians(phf))                          # 0 at the front .. 1 at the back
        hgt = COLLAR_H[0] + (COLLAR_H[1] - COLLAR_H[0]) * back ** 0.8
        z = Z_C0 + hgt * v
        prof = radial_profile(BVH_NECK, 0.0, NECK0[1], phf + 180.0, _zc)
        rmax = float(np.max(prof[_zc <= z + 1e-6])) if (_zc <= z + 1e-6).any() else float(prof[0])
        r = rmax + COLLAR_GAP + COLLAR_FLARE * v ** 1.6 * (0.4 + 0.6 * back)
        a = math.radians(phf + 180.0)
        Vc.append([r * math.sin(a), NECK0[1] + r * math.cos(a), z])
Vc = np.array(Vc)
Fc = VP.grid_faces(nu_c, nv_c)
Fc = [f if np.dot(np.cross(Vc[f[1]] - Vc[f[0]], Vc[f[2]] - Vc[f[0]]), np.array([Vc[f].mean(0)[0], Vc[f].mean(0)[1] - NECK0[1], 0])) > 0
      else f[::-1] for f in Fc]
Rc = ["collar_trim" if (fi // (nu_c - 1)) == nv_c - 2 else "collar" for fi in range(len(Fc))]
V_, F_, R_ = VP.solidify(Vc, Fc, COLLAR_T, COLLAR_T * 0.5, Rc, "collar", "collar_trim")
add_part("collar", V_, F_, R_, w="collar", v_param=np.tile(np.repeat(np.linspace(0, 1, nv_c), nu_c), 2))
_gh = BVH_NECK.ray_cast(Vector((0.0, NECK0[1] - 0.3, Z_C0 + 0.018)), Vector((0, 1, 0)), 0.3)
GEM_C = np.array(_gh[0]) + np.array([0.0, -0.004, 0.0])
V_, F_, R_ = VP.gem(GEM_C, (0, -1, 0), GEM_R, GEM_R * 0.55, n=8, up_hint=(0, 0, 1))
add_part("gem", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.torus(GEM_R * 1.12, GEM_R * 0.22, 16, 5, GEM_C + np.array([0, 0.001, 0]), (0, -1, 0), region="gem_setting")
add_part("gem_setting", V_, F_, R_, w="rigid_transfer")

# ---- v3 armour (artist: "maybe if we can do armor to match the clothes in the drawing that might help"): thin solid
# overlays that CONFORM to the body (sampled off the skin by rays, offset, solidified), silver-trimmed edges, skinned
# with the skin weights under them (they ride every clip like the cuffs). Breastplate: over the chest from under the
# collar to the underbust, between the armholes (the cleavage bridged, a low centre keel). Pauldrons: a cap over each
# shoulder + one lower lame down the arm.
TRUNK_F = [f for f, a, n in zip(BF, is_arm_f, fdomn) if not a and n != "head"]
BVH_TRUNK = BVHTree.FromPolygons(BV.tolist(), TRUNK_F)
BP = BREASTPLATE
Z_BP_TOP = Z_C0 + BP["top_dz"]
Z_BP_BOT = Z_UB + BP["bot_dz"]
_ub = np.concatenate([[-1.0, -0.94], np.linspace(-0.88, 0.88, BP["nu"] - 4), [0.94, 1.0]])
_vb = np.concatenate([[0.0, 0.07], np.linspace(0.14, 0.90, BP["nv"] - 4), [0.95, 1.0]])


def bp_top(u):
    return Z_BP_TOP - BP["side_drop"] * u * u


Rbp = np.zeros((len(_vb), len(_ub)))
for j, v in enumerate(_vb):
    for i, u in enumerate(_ub):
        z = Z_BP_BOT + (bp_top(u) - Z_BP_BOT) * v
        Rbp[j, i] = radial_profile(BVH_TRUNK, 0.0, AX_Y, 180.0 + BP["phi"] * u, [z])[0]
for j in range(len(_vb)):                            # bridge the cleavage: never below a smoothed row (only raises)
    rs_ = Rbp[j].copy()
    for _ in range(3):
        rs_ = np.convolve(np.pad(rs_, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    Rbp[j] = np.maximum(Rbp[j], Rbp[j] + BP["bridge"] * (rs_ - Rbp[j]))
Vbp = []
for j, v in enumerate(_vb):
    for i, u in enumerate(_ub):
        z = Z_BP_BOT + (bp_top(u) - Z_BP_BOT) * v
        r = Rbp[j, i] + BP["clear"] + BP["keel"] * max(0.0, 1.0 - abs(u) / 0.14)
        a = math.radians(180.0 + BP["phi"] * u)
        Vbp.append([r * math.sin(a), AX_Y + r * math.cos(a), z])
Vbp = np.array(Vbp)
nub_ = len(_ub)
Fbp = VP.grid_faces(nub_, len(_vb))
Fbp = [f if np.dot(np.cross(Vbp[f[1]] - Vbp[f[0]], Vbp[f[2]] - Vbp[f[0]]), np.array([Vbp[f].mean(0)[0], Vbp[f].mean(0)[1] - AX_Y, 0])) > 0
       else f[::-1] for f in Fbp]
Rg_bp = []
for fi in range(len(Fbp)):
    i_, j_ = fi % (nub_ - 1), fi // (nub_ - 1)
    Rg_bp.append("armor_trim" if (i_ in (0, nub_ - 2) or j_ in (0, len(_vb) - 2)) else "armor")
V_, F_, R_ = VP.solidify(Vbp, Fbp, BP["t"], 0.0, Rg_bp, "armor", "armor_trim")
add_part("breastplate", V_, F_, R_, w="transfer")


def under_breastplate(P, margin):
    """points (bodice skin) at least 'margin' inside the breastplate's outline (in its own phi / z parameters)."""
    P = np.asarray(P, float)
    phf = np.degrees(np.arctan2(-P[:, 0], -(P[:, 1] - AX_Y)))
    rr_ = np.hypot(P[:, 0], P[:, 1] - AX_Y)
    dphi = np.degrees(margin / np.maximum(rr_, 0.05))
    u = phf / BP["phi"]
    return (np.abs(phf) < BP["phi"] - dphi) & (P[:, 2] > Z_BP_BOT + margin) & (P[:, 2] < bp_top(np.clip(u, -1, 1)) - margin) & \
        (P[:, 1] < AX_Y)


PD = PAULDRON
PAUL_INFO = {}
for s, sg in (("L", 1.0), ("R", -1.0)):
    lo_ = s.lower()
    shf = [f for f, n, c in zip(BF, fdomn, fcen) if n in ("clavicle_" + lo_, "upperarm_" + lo_, "spine_03") and sg * c[0] > 0.04
           and np.linalg.norm(c - SHO[s]) < 0.2]
    bvh_sh = BVHTree.FromPolygons(BV.tolist(), shf)
    a_ = unit([sg * PD["axis"][0], PD["axis"][1], PD["axis"][2]])
    e1 = unit((ELB[s] - SHO[s]) - a_ * float((ELB[s] - SHO[s]) @ a_))
    e2 = np.cross(a_, e1)
    O_ = SHO[s]

    def shell_pt(az, psi, off):
        d = math.cos(psi) * a_ + math.sin(psi) * (math.cos(az) * e1 + math.sin(az) * e2)
        h = bvh_sh.ray_cast(Vector(O_ + d * 0.35), Vector(-d), 0.35)
        base = np.array(h[0]) if h[0] is not None else O_ + d * 0.06
        return base + d * off

    def psi_max(az):
        pa, pb = math.radians(PD["psi"][0]), math.radians(PD["psi"][1])
        return 1.0 / math.sqrt((math.cos(az) / pa) ** 2 + (math.sin(az) / pb) ** 2)

    # cap: apex + nv-1 rings
    nu_p, nv_p = PD["nu"], PD["nv"]
    Vp_ = [shell_pt(0.0, 0.0, PD["clear"] + PD["bulge"])]
    for j in range(1, nv_p):
        for k in range(nu_p):
            az = 2 * math.pi * k / nu_p
            pm = psi_max(az)
            f_ = j / (nv_p - 1)
            Vp_.append(shell_pt(az, pm * f_, PD["clear"] + PD["bulge"] * (1.0 - f_ ** 2)))
    Vp_ = np.array(Vp_)
    Fp_, Rp_ = [], []
    for k in range(nu_p):
        k1 = (k + 1) % nu_p
        Fp_.append([0, 1 + k, 1 + k1]); Rp_.append("armor")
    for j in range(1, nv_p - 1):
        for k in range(nu_p):
            k1 = (k + 1) % nu_p
            Fp_.append([1 + (j - 1) * nu_p + k, 1 + j * nu_p + k, 1 + j * nu_p + k1, 1 + (j - 1) * nu_p + k1])
            Rp_.append("armor_trim" if j == nv_p - 2 else "armor")
    cdir = Vp_.mean(0) - O_
    Fp_ = [f if np.dot(np.cross(Vp_[f[1]] - Vp_[f[0]], Vp_[f[2]] - Vp_[f[0]]), Vp_[f].mean(0) - O_) > 0 else f[::-1] for f in Fp_]
    V_, F_, R_ = VP.solidify(Vp_, Fp_, PD["t"], 0.0, Rp_, "armor", "armor_trim")
    add_part("pauldron." + s, V_, F_, R_, w="transfer")
    # lower lame: a band round the arm side, under the cap's lower edge, reaching further down the arm
    nl_u, nl_v = 9, 4
    Vl_ = []
    for j in range(nl_v):
        for k in range(nl_u):
            az = math.radians(-PD["lame"][2] + 2 * PD["lame"][2] * k / (nl_u - 1))
            pm = psi_max(az)
            psi = pm * (PD["lame"][0] + (PD["lame"][1] - PD["lame"][0]) * j / (nl_v - 1))
            Vl_.append(shell_pt(az, psi, PD["lame_clear"]))
    Vl_ = np.array(Vl_)
    Fl_ = VP.grid_faces(nl_u, nl_v)
    Fl_ = [f if np.dot(np.cross(Vl_[f[1]] - Vl_[f[0]], Vl_[f[2]] - Vl_[f[0]]), Vl_[f].mean(0) - O_) > 0 else f[::-1] for f in Fl_]
    Rl_ = ["armor_trim" if fi // (nl_u - 1) == nl_v - 2 else "armor" for fi in range(len(Fl_))]
    V_, F_, R_ = VP.solidify(Vl_, Fl_, PD["t"], 0.0, Rl_, "armor", "armor_trim")
    add_part("pauldronlame." + s, V_, F_, R_, w="transfer")
    PAUL_INFO[s] = {"cap_apex": Vp_[0].round(4).tolist(), "cap_rim_max_offset_m": round(float(np.max([
        BVH_BODY.find_nearest(Vector(p))[3] for p in Vp_[1 + (nv_p - 2) * nu_p:]])), 4),
        "cap_apex_offset_m": round(float(BVH_BODY.find_nearest(Vector(Vp_[0]))[3]), 4)}
ARMOR_INFO = {"breastplate": {"top_z_centre": round(Z_BP_TOP, 4), "bottom_z": round(Z_BP_BOT, 4), "half_angle_deg": BP["phi"],
                              "min_clear_to_skin_m": round(float(min(BVH_BODY.find_nearest(Vector(p))[3] for p in Vbp)), 4)},
              "pauldrons": PAUL_INFO}

# ---- cape: a torn sheet hung from the back of the neck base / shoulders, solidified (black outside, red lining inside)
Z_CT_BACK = NECK0[2] + CAPE_TOP_DZ[0]
Z_CT_SIDE = SHO["L"][2] + CAPE_TOP_DZ[1]
_zg = np.linspace(Z_CT_BACK + 0.02, 0.04, 100)
_phg = np.arange(-CAPE_PHI_HEM - 12.0, CAPE_PHI_HEM + 12.01, 2.0)
_Vc_, _Fc_, _o_ = [BV], [list(f) for f in TORSO_F], len(BV)   # v3: the cape hangs over the pauldrons too
for p_ in PARTS:
    if p_["name"].split(".")[0] in ("pauldron", "pauldronlame", "breastplate"):
        _Vc_.append(p_["V"]); _Fc_ += [[i + _o_ for i in f] for f in p_["F"]]; _o_ += len(p_["V"])
BVH_CAPEP = BVHTree.FromPolygons(np.vstack(_Vc_).tolist(), _Fc_)
_PROF = np.stack([np.maximum.accumulate(radial_profile(BVH_CAPEP, 0.0, AX_Y, ph, _zg)) for ph in _phg])   # hang from above


def cape_hang(phi_back, z):
    col = np.array([np.interp(phi_back, _phg, _PROF[:, k]) for k in range(len(_zg))])
    return float(np.interp(-z, -_zg, col))


cols = []
slit_at = {int(round(uf * (CAPE_NU - 1))): hf for uf, hf in CAPE_SLITS}
for c in range(CAPE_NU):
    u = c / (CAPE_NU - 1)
    if c in slit_at:                                 # v3: the pair sits a sliver apart (v2's coincident pair left
        du_ = 0.12 / (CAPE_NU - 1)                   #   zero-width quads above the slit -> degenerate / flipped UVs)
        cols.append({"u": u - du_, "c": c, "gap": -1, "slit": slit_at[c]})
        cols.append({"u": u + du_, "c": c, "gap": 1, "slit": slit_at[c]})
    else:
        cols.append({"u": u, "c": c, "gap": 0, "slit": None})
NUc = len(cols)
vrows = np.linspace(0.0, 1.0, CAPE_NV) ** 1.12
Vcape, UVc = [], []
for j, v in enumerate(vrows):
    for cl in cols:
        u, c = cl["u"], cl["c"]
        # torn hem: an irregular jag (a small alternating tooth + hash) with occasional deep rips
        tear = 0.028 * hash01(c, 5.0) + (0.025 if c % 2 else 0.0)
        if hash01(c, 3.0) > 0.52:
            tear = CAPE_TEAR[1] + (CAPE_TEAR[0] - CAPE_TEAR[1]) * hash01(c, 7.0)
        if cl["gap"]:
            tear += 0.03 * (1 if cl["gap"] > 0 else 0.4)
        ztop = Z_CT_SIDE + (Z_CT_BACK - Z_CT_SIDE) * (1.0 - abs(2 * u - 1) ** 1.7)
        hem = CAPE_HEM_Z + tear
        z = ztop + (hem - ztop) * v
        span = CAPE_PHI + (CAPE_PHI_HEM - CAPE_PHI) * v ** 1.2
        ph = (2 * u - 1) * span
        if cl["gap"] and v > 1.0 - cl["slit"]:
            ph += cl["gap"] * 2.2 * ((v - (1.0 - cl["slit"])) / cl["slit"]) ** 1.3
        r = cape_hang(ph, z) + CAPE_CLEAR + CAPE_FLARE * v ** 1.5 + CAPE_FOLD * v ** 1.2 * math.sin(2 * math.pi * CAPE_FOLDS * u + 1.3 * v)
        a = math.radians(ph)
        Vcape.append([r * math.sin(a), AX_Y + r * math.cos(a), z])
        UVc.append([u, v])
Vcape = np.array(Vcape); UVc = np.array(UVc)
pairs_ = {i: cols[i]["slit"] for i in range(NUc - 1) if cols[i]["gap"] == -1}
Fcape = VP.grid_faces(NUc, CAPE_NV, skip=lambda i, j: i in pairs_ and vrows[j] >= 1.0 - pairs_[i] - 1e-9)
Fcape = [f if np.dot(np.cross(Vcape[f[1]] - Vcape[f[0]], Vcape[f[2]] - Vcape[f[0]]),
                     np.array([Vcape[f].mean(0)[0], Vcape[f].mean(0)[1] - AX_Y, 0.0])) > 0 else f[::-1] for f in Fcape]
V_, F_, R_ = VP.solidify(Vcape, Fcape, CAPE_T * 0.5, CAPE_T * 0.5, "cape_outer", "cape_lining", "cape_outer")
add_part("cape", V_, F_, R_, w="cape", uv=np.vstack([UVc, UVc]))
CAPE_INFO = {"columns": NUc, "rows": CAPE_NV, "slits": len(pairs_), "top_z_back": round(Z_CT_BACK, 4),
             "top_z_side": round(Z_CT_SIDE, 4), "hem_z": CAPE_HEM_Z, "tear_m": list(CAPE_TEAR)}

# ---- hair: scalp cap (the body's hair faces after the cuts, offset) + lens-section locks
cap_faces = [k for k, r in enumerate(reg) if r in ("hair", "hair_part")]
used = sorted(set(i for k in cap_faces for i in CF[k]))
mp_ = {v: k for k, v in enumerate(used)}
Vcap = CV[used]
Fcap = [[mp_[i] for i in CF[k]] for k in cap_faces]
tpart = np.clip(1.0 - np.abs(Vcap[:, 0]) / PART_W, 0, 1) * (Vcap[:, 1] < HC[1] + 0.02)
V_, F_, R_ = VP.solidify(Vcap, Fcap, HAIR_CAP_T - PART_DEPTH * tpart ** 1.5, -0.0015,
                         ["hair_part" if reg[k] == "hair_part" else "hair" for k in cap_faces], "hair_shade", "hair")
CAP_V, CAP_F = V_, F_
add_part("hair_cap", V_, F_, R_, w="rigid:head")


def comb_bvh(names):
    Vs, Fs, o = [CV], [list(f) for f in CF], len(CV)
    for p in PARTS:
        if p["name"].split(".")[0] in names:
            Vs.append(p["V"]); Fs += [[i + o for i in f] for f in p["F"]]; o += len(p["V"])
    return BVHTree.FromPolygons(np.vstack(Vs).tolist(), Fs)


BVH_HAIR = comb_bvh({"hair_cap", "collar", "cape", "belt", "fauld", "gem", "gem_setting", "breastplate", "pauldron",
                     "pauldronlame"})
BVH_CAP = BVHTree.FromPolygons(CAP_V.tolist(), CAP_F)


def on_head(psi, el, off):
    """point on the hair cap at azimuth psi (deg from the FRONT toward +X) / elevation el from the head centre, + off."""
    d = np.array([math.sin(math.radians(psi)) * math.cos(math.radians(el)), -math.cos(math.radians(psi)) * math.cos(math.radians(el)),
                  math.sin(math.radians(el))])
    o = HC + d * 0.4
    hit = BVH_CAP.ray_cast(Vector(o), Vector(-d), 0.4)
    if hit[0] is None:
        hit = BVH_BODY.ray_cast(Vector(o), Vector(-d), 0.4)
    return np.array(hit[0]) + d * off


def support(phi_back, z, off):
    """outermost body / cape / collar surface at azimuth phi_back (0 = back, +90 = +X) and height z, + off."""
    r = radial_profile(BVH_HAIR, 0.0, AX_Y, phi_back, [z])[0]
    a = math.radians(phi_back)
    return np.array([(r + off) * math.sin(a), AX_Y + (r + off) * math.cos(a), z])


def front_support(x, z, off):
    hit = BVH_HAIR.ray_cast(Vector((x, -0.8, z)), Vector((0, 1, 0)), 1.2)
    y = hit[0][1] if hit[0] is not None else Y_BUST
    return np.array([x, y - off, z])


HO = HAIR_OPTS[HAIR_STYLE]
LT = LOCK_T * HO.get("t_k", 1.0)       # style C: thicker v1 locks
LOCKS = []
if HO["kind"] == "locks":             # style C: v1's 3 + 3 front / 9 back lens locks, wider + thicker so they merge
    for sg, s in ((1.0, "L"), (-1.0, "R")):
        for k, (dpsi, dx, tipz, W) in enumerate([(3.0, 0.012, 1.12, 0.019), (12.0, 0.04, 1.06, 0.022), (22.0, 0.07, 1.16, 0.021)]):
            ctl = [on_head(sg * dpsi, 60.0 - 4 * k, LT * 0.6), on_head(sg * (40.0 + 6 * k), 36.0 - 2 * k, LT),
                   on_head(sg * (78.0 + 4 * k), 10.0, LT), on_head(sg * (96.0 + 5 * k), -16.0, LT),
                   np.array([sg * (0.10 + dx * 0.8), NECK0[1] - 0.045 + 0.01 * k, SHO[s][2] + 0.05 - 0.01 * k]),
                   front_support(sg * (0.085 + dx), Z_BUST + 0.06, LT + 0.008),
                   front_support(sg * (0.085 + dx), Z_BUST, LT + 0.01)]
            tip = ctl[-1].copy(); tip[2] = tipz; tip[1] -= 0.012
            ctl.append(tip)
            LOCKS.append({"name": "lock.front.%s.%d" % (s, k), "ctl": np.array(ctl), "W": W * HO["w_k"], "chain": "hair_front." + s})
    # back cascade: psi measured from the FRONT toward +X (180 = back centre); the torso azimuth from the back is 180 - psi
    for k, psi in enumerate(np.linspace(116.0, 244.0, 9)):
        pb_ = 180.0 - psi
        ctl = [on_head(psi, 76.0, LT * 0.6), on_head(psi, 32.0, LT), on_head(psi, -12.0, LT),
               support(pb_, HEADJ[2] - 0.035, LT + 0.004)]
        for zz, spread in ((SHO["L"][2] - 0.03, HAIR_SPREAD[0]), (1.32, HAIR_SPREAD[1]), (1.2, HAIR_SPREAD[1])):
            ctl.append(support(pb_ * spread, zz, LT + 0.006))
        tipz = HAIR_TIP_Z + 0.05 * math.cos(2.3 * k) + 0.02 * abs(k - 4) / 4
        ctl.append(support(pb_ * HAIR_SPREAD[1], tipz, LT + 0.008))
        ch = "hair_back.L" if psi < 165 else ("hair_back.R" if psi > 195 else "hair_back.C")
        LOCKS.append({"name": "lock.back.%d" % k, "ctl": np.array(ctl), "W": 0.033 * HO["w_k"], "chain": ch})


def col_front(sg, s, q, off):
    """a front-mass column at q (0 = the part edge .. 1 = the outer edge): from the centre part over the temple and the
    ear, over the shoulder, down the front to the bust / waist."""
    ctl = [on_head(sg * (3.0 + 34.0 * q), 62.0 - 12.0 * q, off), on_head(sg * (42.0 + 28.0 * q), 34.0 - 8.0 * q, off),
           on_head(sg * (78.0 + 30.0 * q), 8.0, off), on_head(sg * (96.0 + 26.0 * q), -16.0, off),
           np.array([sg * (0.088 + 0.085 * q), NECK0[1] - 0.042 + 0.035 * q, SHO[s][2] + 0.045 - 0.012 * q]),
           front_support(sg * (0.072 + 0.095 * q), Z_BUST + 0.06, off + 0.003),
           front_support(sg * (0.078 + 0.095 * q), Z_BUST, off + 0.004)]
    tip = ctl[-1].copy(); tip[2] = float(np.interp(q, [0.0, 0.5, 1.0], HAIR_FRONT_TIP_Z)); tip[1] -= 0.006
    ctl.append(tip)
    return np.array(ctl)


def col_back(q, off, tipz):
    """a back-mass column at q (0 = her left side of the head .. 1 = her right): crown, down the back of the head, over
    the collar / cape, fanning across the back to the waist."""
    psi = HAIR_BACK_PSI[0] + (HAIR_BACK_PSI[1] - HAIR_BACK_PSI[0]) * q
    pb_ = 180.0 - psi
    ctl = [on_head(psi, 76.0, off), on_head(psi, 32.0, off), on_head(psi, -12.0, off), support(pb_, HEADJ[2] - 0.035, off + 0.002)]
    for zz, spread in ((SHO["L"][2] - 0.03, HAIR_SPREAD[0]), (1.32, HAIR_SPREAD[1]), (1.2, HAIR_SPREAD[1])):
        ctl.append(support(pb_ * spread, zz, off + 0.003))
    ctl.append(support(pb_ * HAIR_SPREAD[1], tipz, off + 0.004))
    return np.array(ctl)


def relax_path(C, margin, iters=6, fix=1):
    C = C.copy()
    for _ in range(iters):
        Cn = C.copy()
        Cn[fix:-1] = C[fix:-1] + 0.35 * (0.5 * (C[fix - 1:-2] + C[fix + 1:]) - C[fix:-1])
        C = Cn
        for i in range(fix, len(C)):
            q, n_, _, d = BVH_HAIR.find_nearest(Vector(C[i]))
            if q is None:
                continue
            q = np.array(q); n_ = np.array(n_)
            sd = float((C[i] - q) @ n_)
            if sd < margin:
                C[i] = q + n_ * margin
    return C


LOCK_INFO = {}


def hair_clump(name, colfn, q0, q1, chain, P=None):
    """one clump of a hair mass: a sheet of P['cols'] columns x P['rows'] rows spanning [q0, q1] widened by the overlap
    (neighbouring clumps interpenetrate into one mass), thick in the middle / thin at its edges (the carved groove between
    clumps) with P['sub'] shallow strand grooves down it; below 1 - split it narrows to P['free_w'] x its own width (the
    clumps come apart) and over the last 20 % to a point. Solidified (closed); weights like a lock (head, then its chain).
    P defaults to the style's params (HO); v3's front strands pass their own."""
    P = HO if P is None else P
    m, nr, ov = P["cols"], P["rows"], P["overlap"]
    w = q1 - q0
    qs = np.linspace(max(0.0, q0 - ov * w), min(1.0, q1 + ov * w), m)
    off = P["off"]
    cols_, sarcs = [], []
    for q in qs:
        C = VP.resample(VP.catmull(colfn(q), 10), 48)[0]
        C = relax_path(C, off + 0.0015)
        seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
        sa = np.concatenate([[0], np.cumsum(seg)])
        srow = sa[-1] * np.linspace(0.0, 1.0, nr) ** 1.35                 # denser rows over the scalp
        cols_.append(np.stack([np.interp(srow, sa, C[:, k]) for k in range(3)], 1))
        sarcs.append(srow)
    P_ = np.array(cols_)                                                   # (m, nr, 3)
    S_ = np.array(sarcs)
    c = (m - 1) // 2
    v = np.linspace(0.0, 1.0, nr)
    kfree = P.get("free_w", 0.9) * (q1 - q0) / max(qs[-1] - qs[0], 1e-9)   # own width (no overlap), a hair narrower
    wf = (1.0 + (kfree - 1.0) * smoothstep(1.0 - P["split"], 1.0 - P["split"] + 0.15, v)) * \
        (1.0 + (P["tip_w"] - 1.0) * smoothstep(0.8, 1.0, v) ** 0.8)
    P_ = P_[c][None] + (P_ - P_[c][None]) * wf[None, :, None]
    V = P_.transpose(1, 0, 2).reshape(-1, 3)                               # vertex j * m + i
    F = VP.grid_faces(m, nr)
    sgn = 0.0
    for f in F[::3]:
        q_ = V[f]; cc = q_.mean(0)
        nn = np.cross(q_[1] - q_[0], q_[2] - q_[0])
        h_ = BVH_HAIR.find_nearest(Vector(cc))
        sgn += float(np.dot(nn, cc - np.array(h_[0])))
    if sgn < 0:
        F = [f[::-1] for f in F]
    fcol = np.tile(np.arange(m) / (m - 1.0), nr)
    vrow = np.repeat(v, m)
    across = P["edge_t"] + (1.0 - P["edge_t"]) * np.sqrt(np.maximum(0.0, 1.0 - (2.0 * fcol - 1.0) ** 2))
    if P["sub"] > 1:
        across *= 1.0 - P["sub_depth"] * 0.5 * (1.0 + np.cos(2.0 * math.pi * P["sub"] * fcol))
    along = (0.35 + 0.65 * smoothstep(0.0, 0.2, vrow)) * (1.0 - 0.8 * smoothstep(0.7, 1.0, vrow))
    Vs, Fs, Rs = VP.solidify(V, F, P["T"] * across * along, 0.0012, "hair", "hair_shade", "hair")
    svert = np.tile(S_.T.reshape(-1), 2)
    Cc = P_[c]
    dcap = np.array([BVH_CAP.find_nearest(Vector(p))[3] for p in Cc])
    i_leave = int(np.argmax(dcap > 0.02)) if (dcap > 0.02).any() else nr // 3
    Lc = float(S_[c][-1])
    add_part(name, Vs, Fs, Rs, w="lock", s=svert, s_leave=float(S_[c][i_leave]), L=Lc, chain=chain, path=Cc[i_leave:])
    LOCK_INFO[name] = {"length": round(Lc, 3), "tip_z": round(float(Cc[-1][2]), 3), "q": [round(float(qs[0]), 3), round(float(qs[-1]), 3)],
                       "leaves_head_at_m": round(float(S_[c][i_leave]), 3),
                       "min_clear": round(float(min(BVH_HAIR.find_nearest(Vector(p))[3] for p in P_.reshape(-1, 3))), 4)}


def col_front_strand(sg, s, q, off, j):
    """v3 front strand column at q (0 = the part edge .. 1 = the outer edge) of strand j: from the centre part over the
    temple and past the ear (the face-framing mass), then FORWARD beside the neck in front of the shoulder and down the
    chest (over the breastplate), hanging free below the bust to the strand's own tip height."""
    xb0 = HAIR_FRONT_X[0][0] + (HAIR_FRONT_X[0][1] - HAIR_FRONT_X[0][0]) * q
    xb1 = HAIR_FRONT_X[1][0] + (HAIR_FRONT_X[1][1] - HAIR_FRONT_X[1][0]) * q
    ctl = [on_head(sg * (3.0 + 30.0 * q), 62.0 - 12.0 * q, off), on_head(sg * (40.0 + 26.0 * q), 34.0 - 6.0 * q, off),
           on_head(sg * (70.0 + 22.0 * q), 8.0, off), on_head(sg * (82.0 + 16.0 * q), -14.0, off),
           np.array([sg * (0.072 + 0.055 * q), NECK0[1] - 0.058 + 0.012 * q, SHO[s][2] + 0.055]),
           front_support(sg * xb0, Z_BUST + 0.07, off + 0.003),
           front_support(sg * xb1, Z_BUST - 0.01, off + 0.004)]
    tip = ctl[-1].copy(); tip[2] = HAIR_FRONT_TIPS[j]; tip[1] -= 0.004
    ctl.append(tip)
    return np.array(ctl)


if HO["kind"] == "mass":
    for sg, s in ((1.0, "L"), (-1.0, "R")):
        if HO.get("front") == "strands":
            HOF = {**HO, **HAIR_FRONT}
            n_ = HAIR_FRONT_N
            for j in range(n_):
                hair_clump("lock.front.%s.%d" % (s, j), lambda q, sg=sg, s=s, j=j: col_front_strand(sg, s, q, HOF["off"], j),
                           j / n_, (j + 1) / n_, "hair_front." + s, HOF)
            continue
        n_ = HO["front_clumps"]
        for j in range(n_):
            hair_clump("lock.front.%s.%d" % (s, j), lambda q, sg=sg, s=s: col_front(sg, s, q, HO["off"]), j / n_, (j + 1) / n_,
                       "hair_front." + s)
    n_ = HO["back_clumps"]
    for j in range(n_):
        tz = HAIR_TIP_Z + 0.045 * math.cos(2.3 * j + 0.4) + 0.03 * abs(j - (n_ - 1) / 2.0) / ((n_ - 1) / 2.0)
        psi_c = HAIR_BACK_PSI[0] + (HAIR_BACK_PSI[1] - HAIR_BACK_PSI[0]) * (j + 0.5) / n_
        ch = "hair_back.L" if psi_c < 165 else ("hair_back.R" if psi_c > 195 else "hair_back.C")
        hair_clump("lock.back.%d" % j, lambda q, tz=tz: col_back(q, HO["off"], tz), j / n_, (j + 1) / n_, ch)
for L_ in LOCKS:
    C = VP.catmull(L_["ctl"], 10)
    C, Ltot = VP.resample(C, LOCK_RINGS)
    C = relax_path(C, LT + 0.003)
    seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
    sarc = np.concatenate([[0], np.cumsum(seg)])
    Ltot = float(sarc[-1])
    dcap = np.array([BVH_CAP.find_nearest(Vector(p))[3] for p in C])
    i_leave = int(np.argmax(dcap > 0.02)) if (dcap > 0.02).any() else len(C) // 3      # where the lock leaves the head
    rings, sv = [], []
    T_ = np.gradient(C, axis=0)
    for i, p in enumerate(C):
        sfr = sarc[i] / Ltot
        q, n_, _, _ = BVH_HAIR.find_nearest(Vector(p))
        nout = unit(p - np.array(q)) if np.linalg.norm(p - np.array(q)) > 1e-5 else unit([p[0], p[1] - AX_Y, 0.0])
        wdir = np.cross(nout, unit(T_[i]))
        hw = L_["W"] * (0.62 + 0.38 * math.sin(0.5 * math.pi * min(sfr / 0.45, 1.0))) * (1.0 - smoothstep(0.62, 1.0, sfr) * 0.96)
        ht = LT * (0.75 + 0.25 * math.sin(math.pi * min(sfr / 0.5, 1.0))) * (1.0 - smoothstep(0.7, 1.0, sfr) * 0.85)
        if i == 0:
            hw *= 0.5; ht *= 0.5
        rings.append(VP.lens_ring(p, T_[i], wdir, max(hw, 0.0012), max(ht, 0.0008), 6))
        sv.append(sarc[i])
    V_, F_, _ = VP.loft(rings, "pole", "pole", reg="hair", pole1=C[-1] + unit(T_[-1]) * 0.01)
    svert = np.concatenate([np.repeat(sv, 6), [0.0, Ltot]])
    R_ = []
    for f in F_:
        q = V_[f]
        nn = np.cross(q[1] - q[0], q[2] - q[0])
        c_ = q.mean(0)
        qq, n2, _, _ = BVH_HAIR.find_nearest(Vector(c_))
        R_.append("hair_shade" if np.dot(unit(nn), unit(c_ - np.array(qq))) < -0.25 else "hair")
    add_part(L_["name"], V_, F_, R_, w="lock", s=svert, s_leave=float(sarc[i_leave]), L=Ltot, chain=L_["chain"],
             path=C[i_leave:])
    LOCK_INFO[L_["name"]] = {"length": round(Ltot, 3), "tip_z": round(float(C[-1][2]), 3), "leaves_head_at_m": round(float(sarc[i_leave]), 3),
                             "min_clear": round(float(min(BVH_HAIR.find_nearest(Vector(p))[3] for p in C[i_leave:])), 4)}

# ---- sword (own object) + tassel: rest pose = standing point-down beside the right hand, tip on the floor
Vs_, Fs_, Rs_, SWL = VP.sword(SWORD)
SW_T = np.array([SWORD_REST[0], SWORD_REST[1], SWORD["blade_len"]])
add_part("sword", Vs_ + SW_T, Fs_, Rs_, w="rigid:sword", obj="sword")
gx, gy, gz = SWORD["guard"]
TAS_TOP = SW_T + np.array([gx * 0.62, 0.0, -gz])
Vt_, Ft_, Rt_, St_ = VP.tassel(TAS_TOP, TASSEL)
add_part("tassel", Vt_, Ft_, Rt_, w="tassel", obj="sword", s=St_)
SWORD_INFO = {"total_len": round(float(SWL["total_len"]), 4), "blade_len": SWORD["blade_len"],
              "x_height": round(float(SWL["total_len"]) / Z_TOP, 3), "rest_tip": (SW_T + SWL["tip"]).round(4).tolist(),
              "rest_grip": (SW_T + SWL["grip_centre"]).round(4).tolist()}

# =========================================================================== 5. assemble + centre
REG = ["skin", "lips", "liner", "eye_sclera", "eye_iris", "eye_pupil", "fang", "hair", "hair_shade", "hair_part",
       "bodice", "trim", "gloves", "boots", "boot_sole", "heel", "knee_cop", "cuff", "fauld", "fauld_trim", "belt",
       "collar", "collar_trim", "gem", "gem_setting", "cape_outer", "cape_lining",
       "skin_shadow", "bodice_shadow", "brow", "armor", "armor_trim", "mouth"]
REG_S = ["blade", "blade_vein", "guard", "handle", "pommel", "tassel", "tassel_cord"]
# hidden skin removed: the scalp under the hair cap and the toes inside the toe caps (the cap / toe-cap solids are closed)
_cdom = np.array([MB[j] for j in np.argmax(CW, 1)], dtype=object)
_toe = np.array([all(_cdom[i].startswith("ball_") for i in f) for f in CF])
# v3: and the bodice skin fully under the breastplate (every corner ARMOR_HIDE_MARGIN inside its outline)
_ubp = under_breastplate(CV, ARMOR_HIDE_MARGIN)
_plate = np.array([bool(_ubp[f].all()) for f in CF]) & np.isin(reg, ["bodice", "trim", "bodice_shadow"])
_keep = ~np.isin(reg, ["hair", "hair_part"]) & ~_toe & ~_plate
_usedv = np.unique(np.concatenate([np.array(f) for f, k in zip(CF, _keep) if k]))
_rm = -np.ones(len(CV), dtype=np.int64); _rm[_usedv] = np.arange(len(_usedv))
report["hidden_skin_removed"] = {"scalp_faces": int(np.isin(reg, ["hair", "hair_part"]).sum()), "toe_faces": int(_toe.sum()),
                                 "under_breastplate_faces": int(_plate.sum()),
                                 "tris_removed": int(sum(len(f) - 2 for f, k in zip(CF, _keep) if not k))}
CV = CV[_usedv]; CW = CW[_usedv]
CF = [[int(_rm[i]) for i in f] for f, k in zip(CF, _keep) if k]
reg = reg[_keep]
ISL = [{"name": "body", "V": CV, "F": CF, "R": list(reg), "w": "body", "obj": "main"}] + PARTS
allV = np.vstack([p["V"] for p in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, 0.0])
report["centre_shift"] = SHIFT.round(6).tolist()
report["min_z_before_shift"] = round(float(lo0[2]), 6)
OBJ = {"main": {"V": [], "F": [], "R": [], "RANGE": {}, "FRANGE": {}, "n": 0},
       "sword": {"V": [], "F": [], "R": [], "RANGE": {}, "FRANGE": {}, "n": 0}}
for p in ISL:
    o = OBJ[p["obj"]]
    o["RANGE"][p["name"]] = (o["n"], o["n"] + len(p["V"]))
    o["FRANGE"][p["name"]] = (len(o["F"]), len(o["F"]) + len(p["F"]))
    o["V"].append(p["V"] - SHIFT)
    o["F"] += [[i + o["n"] for i in f] for f in p["F"]]
    o["R"] += list(p["R"])
    o["n"] += len(p["V"])
for o in OBJ.values():
    o["V"] = np.vstack(o["V"])
assert set(OBJ["main"]["R"]) <= set(REG), sorted(set(OBJ["main"]["R"]) - set(REG))
assert set(OBJ["sword"]["R"]) <= set(REG_S), sorted(set(OBJ["sword"]["R"]) - set(REG_S))


def jitter(V, F):
    if not SHADE_JITTER:
        return np.ones(len(F))
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
    mat.use_backface_culling = True
    return mat


def new_obj(name, V, F):
    me_ = bpy.data.meshes.new(name)
    me_.from_pydata(np.asarray(V).tolist(), [], [list(map(int, f)) for f in F])
    me_.update()
    ob_ = bpy.data.objects.new(name, me_)
    scene.collection.objects.link(ob_)
    return ob_


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


def glow_tiers(pal):
    """red-eye glow gate: emission tiers eye_iris > gem > 0 (the only lit regions), and every glow is RED (hue within
    20 deg of 0): the eyes read as the one lit accent, the gem as a faint second."""
    import colorsys
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    hues = {n: round(colorsys.rgb_to_hsv(*[c / 255.0 for c in v["emission"]])[0] * 360.0, 1)
            for n, v in pal["regions"].items() if "emission" in v}
    ok_grade = set(tier) == {"eye_iris", "gem"} and tier["eye_iris"] > tier["gem"] > 0
    ok_hue = all(min(h, 360 - h) <= 20.0 for h in hues.values())
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "grade_pass": ok_grade, "accent_hue_deg": hues, "hue_pass": ok_hue}


MAT_BODY = make_mat(UNIT + "_body")
MAT_SWORD = make_mat(UNIT + "_sword")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["grade_pass"] and report["glow_tiers"]["default"]["hue_pass"], report["glow_tiers"]
low = new_obj(UNIT, OBJ["main"]["V"], OBJ["main"]["F"])
low.data.materials.append(MAT_BODY)
rid = np.array([REG.index(r) for r in OBJ["main"]["R"]], dtype=np.int32)
PAL.store_regions(low.data, REG, rid, jitter(OBJ["main"]["V"], OBJ["main"]["F"]))
swo = new_obj(UNIT + "_sword", OBJ["sword"]["V"], OBJ["sword"]["F"])
swo.data.materials.append(MAT_SWORD)
rid_s = np.array([REG_S.index(r) for r in OBJ["sword"]["R"]], dtype=np.int32)
PAL.store_regions(swo.data, REG_S, rid_s, jitter(OBJ["sword"]["V"], OBJ["sword"]["F"]))
report["regions_faces"] = repaint([low, swo], pal_default)
me = low.data
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in OBJ["main"]["RANGE"].items()})
# facing landmark: the head centre (between the eyes, at the skull's mid depth) -> the nose tip (most forward midline point)
_nose = BV[HEAD_B & (np.abs(BV[:, 0]) < 0.004)]
NOSE = _nose[np.argmin(_nose[:, 1])]
anchor = np.array([0.5 * (EYE["L"]["c"][0] + EYE["R"]["c"][0]), HC[1], EYE["L"]["c"][2]]) - SHIFT
landmark = NOSE - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (between the eyes, at the skull's mid depth) -> nose tip (midline)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
for ob_ in (low, swo):
    ob_.data.shade_flat()
    bpy.context.view_layer.objects.active = ob_
    for o in scene.objects:
        o.select_set(o is ob_)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.002, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=0.002)
    bpy.ops.object.mode_set(mode="OBJECT")
tris_main = tri_count_F(OBJ["main"]["F"]); tris_sw = tri_count_F(OBJ["sword"]["F"])
report["tris"] = {"total": tris_main + tris_sw, "main": tris_main, "sword": tris_sw, "body": tri_count_F(CF),
                  "hair_locks": sum(tri_count_F(p["F"]) for p in PARTS if p["name"].startswith("lock")),
                  **{p["name"]: tri_count_F(p["F"]) for p in PARTS if not p["name"].startswith("lock")}}
report["tier_rationale"] = ("HERO role (the artist's first humanoid hero): window [%d, %d]. The MPFB body at native "
                            "resolution (face, hands, fingers for the grip) + a real hair mass + a torn two-colour cape + "
                            "armour + a serrated sword lands mid-window." % tuple(TRI_BUDGET))
report["open_edges"] = {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}
report["open_edges_rule"] = "every part is a closed solid (listed here only if not); the body is the MPFB skin"
allV2 = np.vstack([OBJ["main"]["V"], OBJ["sword"]["V"]])
lo_a, hi_a = allV2.min(0), allV2.max(0)
Hh = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / Hh, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(Hh, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(Hh * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4)}}
report["parts"] = {"boots": BOOT_INFO, "fauld": FAULD_INFO, "cape": CAPE_INFO, "sword": SWORD_INFO, "locks": LOCK_INFO,
                   "armor": ARMOR_INFO}
for ob_ in (low, swo):
    ob_["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "hero"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch on an MPFB2 base: the artist's sheet (transcribed, design/review-log.md 2026-09-26)"
low["conquest_scale_policy"] = "natural proportions in metres; game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
swo["conquest_toggle"] = "the sword is its own node + bone 'sword': hide or swap it"


def box(pts, pad):
    pts = np.asarray(pts)
    return [(pts.min(0) - pad).tolist(), (pts.max(0) + pad).tolist()]


_hem = Vcape[Vcape[:, 2] < CAPE_HEM_Z + 0.35] - SHIFT
FOCUS = {"face": box([EYE["L"]["c"] - SHIFT, EYE["R"]["c"] - SHIFT, np.array([0, Y_LIP, Z_LIP - 0.03]) - SHIFT], 0.045),
         "sword": box([SW_T - SHIFT + np.array([0, 0, -0.35]), SW_T - SHIFT + np.array([0, 0, 0.32])], 0.06),
         "hem": box(_hem[_hem[:, 1] > AX_Y - SHIFT[1]], 0.02),
         "boots": box([ANKLE["L"] - SHIFT, ANKLE["R"] - SHIFT, KNEE["L"] - SHIFT, TOE["R"] - SHIFT, np.array([0, 0, 0.0])], 0.06),
         "torso": box([SHO["L"] - SHIFT, SHO["R"] - SHIFT, np.array([0, 0, Z_BELT - 0.12]) - SHIFT], 0.05),
         "hand": box([WRI["R"] - SHIFT], 0.12),
         "head": box([HC - SHIFT + np.array([0, 0, HR[2]]), HC - SHIFT - np.array([0, HR[1], 0]), HC - SHIFT + np.array([0, HR[1], 0]),
                      SHO["L"] - SHIFT, SHO["R"] - SHIFT, np.array([0.0, Y_BUST, Z_BUST - 0.12]) - SHIFT], 0.05),
         "portrait": box([np.array([-HR[0], Y_CHIN, HC[2] + HR[2] * 0.75]) - SHIFT, np.array([HR[0], Y_CHIN, Z_C0 - 0.01]) - SHIFT], 0.02),
         "eyes": box([EYE["L"]["c"] - SHIFT + np.array([0.022, 0, 0.016]), EYE["R"]["c"] - SHIFT - np.array([0.022, 0, 0.012])], 0.004)}
low["conquest_focus"] = json.dumps(FOCUS)

if PREVIEW:
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris", "measure", "facing", "open_edges", "regions_area_share",
                                                            "glow_tiers", "centre_shift", "parts", "chin", "mpfb",
                                                            "hidden_skin_removed")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = subdivided MPFB body + parts)
try:
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
t_bake = time.time()
HIGHB = new_obj(UNIT + "_highbody", BV - SHIFT, BF)
HIGHB.data.shade_smooth()
sm_ = HIGHB.modifiers.new("subd", "SUBSURF"); sm_.levels = 1; sm_.render_levels = 1
HV_, HF_ = [], []
_o = 0
for p in PARTS:
    HV_.append(p["V"] - SHIFT); HF_ += [[i + _o for i in f] for f in p["F"]]; _o += len(p["V"])
HIGHP = new_obj(UNIT + "_highparts", np.vstack(HV_), HF_)
dg = bpy.context.evaluated_depsgraph_get()
_hb_me = bpy.data.meshes.new_from_object(HIGHB.evaluated_get(dg))
high_tris = sum(len(p_.vertices) - 2 for p_ in _hb_me.polygons) + tri_count_F(HF_)
_hV, _hF = mesh_arrays(_hb_me)
bpy.data.meshes.remove(_hb_me)
_bvh_h = BVHTree.FromPolygons(_hV.tolist(), _hF)
_shrink = np.array([_bvh_h.find_nearest(Vector(p))[3] for p in (CV - SHIFT)])
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.cycles.seed = 0
BAKE_CAGE = 0.012
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
nt = MAT_BODY.node_tree
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
swo.hide_render = True
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
for o in scene.objects:
    o.select_set(o in (HIGHB, HIGHP, low))
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt.nodes.active = node
    scene.cycles.samples = samples
    t_ = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, max_ray_distance=BAKE_CAGE * 2.5,
                             margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t_, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
me.shade_flat()
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
bstats.update({"cage_extrusion": BAKE_CAGE, "cage_rule": "12 mm: > 3 x the measured low-skin -> subdivided-high distance "
               "(p99 / max below) so every low texel finds its high surface",
               "low_to_high_skin_m": {"p99": round(float(np.percentile(_shrink, 99)), 5), "max": round(float(_shrink.max()), 5)},
               "resolution": {"normal": RN, "ao": RA},
               "high_tris": high_tris, "high_rule": "MPFB body at subdivision 1 (smooth) + every part",
               "normal_baked_texels_pct": round(100 * float(cov_n.mean()), 2),
               "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n] > 0.05).mean()), 4),
               "ao_baked_texels_pct": round(100 * float(cov_a.mean()), 2),
               "ao_mean": round(float(pa[cov_a, 0].mean()), 4), "ao_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), "vampwarrior_normal_%s.npy" % TAG), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), "vampwarrior_ao_%s.npy" % TAG), pa[:, :1])
bstats["seconds"] = round(time.time() - t_bake, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
# v3 AO lift (the digests / tolerance gate above are on the RAW bake): the shaded look keeps the AO's depth but not its
# grime -- where hair clumps interpenetrate and in the eye / nostril cavities the raw AO reaches 0 and read as black
# blotches. Per texel AO' = floor + (1 - floor) AO, the floor per region class (AO_FLOOR), rasterised from the UV
# triangles and dilated over the bake margin.
me.calc_loop_triangles()
_nt_ = len(me.loop_triangles)
_lt_l = np.empty(_nt_ * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", _lt_l); _lt_l = _lt_l.reshape(-1, 3)
_lt_p = np.empty(_nt_, dtype=np.int64); me.loop_triangles.foreach_get("polygon_index", _lt_p)
_uvl = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", _uvl); _uvl = _uvl.reshape(-1, 2)
_ridf = np.array([REG.index(r) for r in OBJ["main"]["R"]])
_flr = np.full((RA, RA), AO_FLOOR["default"])
for cls_, regs_ in AO_FLOOR_REGIONS.items():
    ids_ = [REG.index(r) for r in regs_]
    for tri_ in np.nonzero(np.isin(_ridf[_lt_p], ids_))[0]:
        P3 = _uvl[_lt_l[tri_]] * RA - 0.5
        x0, y0 = np.floor(P3.min(0)).astype(int); x1, y1 = np.ceil(P3.max(0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, RA - 1), min(y1, RA - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx_, gy_ = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        a_, b_, c_ = P3
        den_ = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den_) < 1e-12:
            continue
        w0 = ((b_[1] - c_[1]) * (gx_ - c_[0]) + (c_[0] - b_[0]) * (gy_ - c_[1])) / den_
        w1 = ((c_[1] - a_[1]) * (gx_ - c_[0]) + (a_[0] - c_[0]) * (gy_ - c_[1])) / den_
        inside_ = (w0 >= -0.02) & (w1 >= -0.02) & (1 - w0 - w1 >= -0.02)
        _flr[gy_[inside_], gx_[inside_]] = np.maximum(_flr[gy_[inside_], gx_[inside_]], AO_FLOOR[cls_])
for _ in range(6):                                   # dilate the higher floors over the bake margin
    f_ = _flr.copy()
    f_[1:] = np.maximum(f_[1:], _flr[:-1]); f_[:-1] = np.maximum(f_[:-1], _flr[1:])
    f_[:, 1:] = np.maximum(f_[:, 1:], _flr[:, :-1]); f_[:, :-1] = np.maximum(f_[:, :-1], _flr[:, 1:])
    _flr = f_
_ao_raw = pa[:, 0].copy()
pa[:, :3] = (_flr.reshape(-1) + (1.0 - _flr.reshape(-1)) * _ao_raw)[:, None]
img_ao.pixels.foreach_set(pa.ravel())
bstats["ao_lift"] = {"floors": AO_FLOOR, "regions": AO_FLOOR_REGIONS,
                     "raw_p05": round(float(np.percentile(_ao_raw[cov_a], 5)), 4),
                     "lifted_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4),
                     "lifted_mean": round(float(pa[cov_a, 0].mean()), 4)}
DIG["ao_lifted"] = sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
bsdf = nt.nodes["Principled BSDF"]
# v3: the v1 material returns (the v2 cel bands are undone): Base Color = Col x the baked AO, Normal = the baked normal map
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(nt.nodes["col"].outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
report["material"] = {"base_color": "Col x AO bake (multiply)", "normal": "baked normal map", "jitter": SHADE_JITTER,
                      "roughness": pal_default["material"].get("roughness"), "cel_bands": "none (v2 treatment undone)",
                      "outline_shells": "none (v2 treatment undone)"}
scene.render.engine = "BLENDER_EEVEE"
for ob_ in (HIGHB, HIGHP):
    m_ = ob_.data
    bpy.data.objects.remove(ob_, do_unlink=True); bpy.data.meshes.remove(m_)
swo.hide_render = False
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k != "pixel_sha"}))


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


DIG["geometry_colour_uv"] = geometry_digest([low, swo])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "authored from the sheet's palette chips (bone-white, pale grey, near-black, dark red, deep maroon, black)"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 7. rig: MPFB game_engine skeleton + chains
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def rot(axis, deg):
    return K._rot(axis, math.radians(deg))


def Rx(d):
    return rot((1, 0, 0), d)


def Ry(d):
    return rot((0, 1, 0), d)


def Rz(d):
    return rot((0, 0, 1), d)


def Tr(h, R):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = np.asarray(h) - R @ np.asarray(h)
    return M


def Tt(v):
    M = np.eye(4); M[:3, 3] = v
    return M


def TRT(pos, R, pivot):
    """rest point 'pivot' -> 'pos', rotated by R (a world rigid map)."""
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = np.asarray(pos) - R @ np.asarray(pivot)
    return M


def xf(M, p):
    return M[:3, :3] @ np.asarray(p) + M[:3, 3]


def frame_to(xa, za):
    """rotation whose columns are (X', Y', Z') with X' = xa (made perpendicular to za), Z' = za."""
    za = unit(za); xa = unit(np.asarray(xa) - za * float(np.dot(xa, za)))
    return np.stack([xa, np.cross(za, xa), za], 1)


def S_(p):
    return np.asarray(p, float) - SHIFT


MPFB_PARENT = {}
_arm_rest = {}
BONES = []          # (name, head, tail, parent, zaxis or None)
for n in MB:
    if n == "Root":
        continue
    b = BREST[n]
    BONES.append((n, S_(b["head"]), S_(b["tail"]), None, b["z"]))
# MPFB parents (from the probe order: every MPFB bone's parent precedes it)
_MP = {"pelvis": "root", "spine_01": "pelvis", "spine_02": "spine_01", "spine_03": "spine_02", "neck_01": "spine_03",
       "head": "neck_01", "thigh_l": "pelvis", "thigh_r": "pelvis"}
for n in MB:
    if n in _MP or n == "Root":
        continue
    if n.startswith("clavicle"):
        _MP[n] = "spine_03"
    elif n.startswith("upperarm"):
        _MP[n] = "clavicle_" + n[-1]
    elif n.startswith("lowerarm"):
        _MP[n] = "upperarm_" + n[-1]
    elif n.startswith("hand"):
        _MP[n] = "lowerarm_" + n[-1]
    elif n.startswith("calf"):
        _MP[n] = "thigh_" + n[-1]
    elif n.startswith("foot"):
        _MP[n] = "calf_" + n[-1]
    elif n.startswith("ball"):
        _MP[n] = "foot_" + n[-1]
    else:                                             # fingers: <finger>_0k_<side>
        f_, k_, sd_ = n.split("_")
        _MP[n] = "hand_" + sd_ if k_ == "01" else "%s_%02d_%s" % (f_, int(k_) - 1, sd_)
BONES = [(n, h, t, _MP[n], z) for (n, h, t, _, z) in BONES]
# hair chains: the mean path of each chain's locks (from where they leave the head)
CHAIN_PTS = {}
for ch in sorted(set(p["chain"] for p in PARTS if p["w"] == "lock")):
    paths = [VP.resample(p["path"], 40)[0] for p in PARTS if p["w"] == "lock" and p["chain"] == ch]
    mp_path = np.mean(paths, 0)
    rs, Lc = VP.resample(mp_path, HAIR_BONES + 1)
    CHAIN_PTS[ch] = (rs, Lc, "spine_03")
# cape chains: grid columns
_base_cols = [i for i, cl in enumerate(cols) if cl["gap"] <= 0]
CAPE_U = [(k + 0.5) / CAPE_CHAINS for k in range(CAPE_CHAINS)]
for k, uc in enumerate(CAPE_U):
    ci = min(_base_cols, key=lambda i: abs(cols[i]["u"] - uc))
    colp = np.array([Vcape[j * NUc + ci] for j in range(CAPE_NV)])
    rs, Lc = VP.resample(colp, CAPE_BONES + 1)
    CHAIN_PTS["cape.%d" % k] = (rs, Lc, "spine_03")
TAS_LEN = TASSEL["cord_len"] + TASSEL["len"]
CHAIN_PTS["tassel"] = (np.array([TAS_TOP, TAS_TOP + np.array([0, 0, -TASSEL["cord_len"] - 0.01]), TAS_TOP + np.array([0, 0, -TAS_LEN])]),
                       TAS_LEN, "sword")
SW_GRIP0 = SW_T + SWL["grip_centre"]
BONES.append(("sword", S_(SW_GRIP0), S_(SW_GRIP0 + np.array([0, 0, -0.30])), "hand_r", None))
for ch, (pts, Lc, par) in sorted(CHAIN_PTS.items(), key=lambda kv: (kv[0] == "tassel", kv[0])):
    for k in range(len(pts) - 1):
        BONES.append(("%s.%d" % (ch, k), S_(pts[k]), S_(pts[k + 1]), par if k == 0 else "%s.%d" % (ch, k - 1), None))
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0.2, 0); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p, z) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_)
    if z is not None:
        e.align_roll(Vector(z))
    else:
        e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = False
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
PARENT = {b[0]: b[3] for b in BONES}
HEADP = {b[0]: np.array(b[1], float) for b in BONES}
TAILP = {b[0]: np.array(b[2], float) for b in BONES}
HEADP["root"] = np.zeros(3)
REST4 = {b.name: np.array(b.matrix_local) for b in arm_data.bones}


# ---- weights (<= 4 influences)
def mpfb_to_rig(Wm):
    out = np.zeros((len(Wm), len(DEFORM)))
    for j, n in enumerate(MB):
        out[:, J["pelvis" if n == "Root" else n]] += Wm[:, j]
    return out


def onehot(n, bone):
    W = np.zeros((n, len(DEFORM))); W[:, J[bone]] = 1.0
    return W


def chain_w(s, L, ch, nb, parent_bone, s0=0.0):
    Wv = K.vine_weights(np.clip(s, 0, L), L, nb, parent_s0=s0)
    W = np.zeros((len(s), len(DEFORM)))
    W[:, J[parent_bone]] += Wv[:, 0]
    for k in range(nb):
        W[:, J["%s.%d" % (ch, k)]] += Wv[:, k + 1]
    return W


def part_weights(p):
    V, w = p["V"], p["w"]
    n = len(V)
    if w == "body":
        return mpfb_to_rig(CW)
    if w == "transfer":
        return mpfb_to_rig(transfer(V))
    if w.startswith("rigid:"):
        return onehot(n, w.split(":")[1])
    if w == "rigid_transfer":
        return np.repeat(mpfb_to_rig(transfer(V.mean(0)[None])), n, 0)
    if w == "toe":                                    # ahead of the ball contact line: the toes bone (stays flat at toe-off)
        wb = smoothstep(-0.022, 0.0, (V - p["c0"]) @ p["fd"])[:, None]
        return (1 - wb) * mpfb_to_rig(transfer(V)) + wb * onehot(n, "ball_" + p["side"].lower())
    if w == "fauld":
        b_ = 0.55 * smoothstep(0.25, 1.0, p["v_param"])[:, None]
        return (1 - b_) * onehot(n, "pelvis") + b_ * mpfb_to_rig(transfer(V))
    if w == "collar":
        W = mpfb_to_rig(transfer(V))
        W[:, J["neck_01"]] += W[:, J["head"]]; W[:, J["head"]] = 0.0
        return W
    if w == "cape":
        uu, vv = p["uv"][:, 0], p["uv"][:, 1]
        W = np.zeros((n, len(DEFORM)))
        cu = np.array(CAPE_U)
        k1 = np.clip(np.searchsorted(cu, uu), 1, CAPE_CHAINS - 1)
        k0 = k1 - 1
        tt = np.clip((uu - cu[k0]) / (cu[k1] - cu[k0]), 0, 1)
        for kk, ww in ((k0, 1 - tt), (k1, tt)):
            for c in range(CAPE_CHAINS):
                m = kk == c
                if not m.any():
                    continue
                Lc = CHAIN_PTS["cape.%d" % c][1]
                W[m] += ww[m][:, None] * chain_w(vv[m] * Lc, Lc, "cape.%d" % c, CAPE_BONES, "spine_03")
        return W
    if w == "lock":
        s = p["s"]
        Lc = CHAIN_PTS[p["chain"]][1]
        sc = (s - p["s_leave"]) / max(p["L"] - p["s_leave"], 1e-6) * Lc
        W = chain_w(sc, Lc, p["chain"], HAIR_BONES, "head", s0=-0.04)
        W[s <= p["s_leave"] - 0.04] = onehot(1, "head")[0]
        return W
    if w == "tassel":
        return chain_w(p["s"] * TAS_LEN, TAS_LEN, "tassel", 2, "sword", s0=0.0)
    raise ValueError(w)


def prune(W):
    W = np.where(W > 1e-4, W, 0.0)
    if (W > 0).sum(1).max() > 4:
        idx_ = np.argsort(-W, 1, kind="stable")[:, 4:]
        np.put_along_axis(W, idx_, 0.0, 1)
    return W / np.maximum(W.sum(1), 1e-30)[:, None]


t_ = time.time()
WM = np.zeros((len(OBJ["main"]["V"]), len(DEFORM)))
WS = np.zeros((len(OBJ["sword"]["V"]), len(DEFORM)))
for p in ISL:
    a_, b_ = OBJ[p["obj"]]["RANGE"][p["name"]]
    (WM if p["obj"] == "main" else WS)[a_:b_] = part_weights(p)
WM, WS = prune(WM), prune(WS)
for ob_, W in ((low, WM), (swo, WS)):
    ob_.vertex_groups.clear()
    for j, n in enumerate(DEFORM):
        nz = np.nonzero(W[:, j] > 0)[0]
        if len(nz) == 0:
            continue
        vg = ob_.vertex_groups.new(name=n)
        for wv in np.unique(np.round(W[nz, j], 6)):
            ids = nz[np.round(W[nz, j], 6) == wv]
            vg.add(ids.tolist(), float(wv), "REPLACE")
    ob_.parent = rig
    ob_.matrix_parent_inverse = Matrix.Identity(4)
    am = ob_.modifiers.new("Armature", "ARMATURE"); am.object = rig
DIG["weights"] = sha(np.vstack([WM, WS]))
infl = (WM > 0).sum(1)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(WM.sum(1) - 1.0).max()), "seconds": round(time.time() - t_, 1),
                  "sword_object": "sword verts 1.0 on 'sword'; tassel on tassel.0/1 (vine weights)",
                  "rule": "body: MPFB game_engine weights (Root -> pelvis); cuffs / belt / toe caps / soles / collar / v3 "
                          "breastplate + pauldrons: the MPFB "
                          "weights of the nearest skin point (barycentric); knee cops / gem / buckle: the skin weights at their "
                          "centroid (rigid ride); fauld: pelvis at the top blending to the skin under the hem (<= 55 %); "
                          "heels foot, eyes / fangs / hair cap head; hair locks head until they leave the head then "
                          "rigkit.vine_weights along their chain; cape: across-hat between the 5 chains x along-hat down "
                          "each; <= 4 influences, normalised"}
print("WEIGHTS", json.dumps(rep["weights"]))

# =========================================================================== 8. clips (closed-form poses -> keyed FK; legs + sword arm analytic IK)
TAU = 2 * math.pi


def ss5(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)


def fk(D, n, R=None):
    D[n] = D[PARENT[n]] @ Tr(HEADP[n], np.eye(3) if R is None else R)


def two_bone(b1, b2, b3, H, T, pole):
    """analytic IK: bones b1 (head H0 -> K0), b2 (K0 -> T0 = b3 head). Hip at H (posed), target T for b3's head, knee
    toward 'pole'. Returns D[b1], D[b2], the reached target, the unreachable remainder (m)."""
    H0, K0, A0 = HEADP[b1], HEADP[b2], HEADP[b3]
    a, b = float(np.linalg.norm(K0 - H0)), float(np.linalg.norm(A0 - K0))
    d0 = unit(A0 - H0)
    k0 = unit((K0 - H0) - float((K0 - H0) @ d0) * d0)
    Dn = float(np.linalg.norm(T - H))
    Dc = float(np.clip(Dn, abs(a - b) + 1e-6, (a + b) * (1 - 1e-5)))
    d = unit(T - H)
    k = unit(pole - float(pole @ d) * d)
    al = math.acos(float(np.clip((a * a + Dc * Dc - b * b) / (2 * a * Dc), -1, 1)))
    al0 = math.atan2(float((K0 - H0) @ k0), float((K0 - H0) @ d0))
    F0 = np.stack([d0, k0, np.cross(d0, k0)], 1); F1 = np.stack([d, k, np.cross(d, k)], 1)
    Ral = F1 @ F0.T
    m = np.cross(d, k)
    R1 = K._rot(m, al - al0) @ Ral
    Kp = H + R1 @ (K0 - H0)
    At = H + d * Dc
    c1 = R1 @ (A0 - K0); c2 = At - Kp
    ang = math.atan2(float(np.dot(np.cross(c1, c2), m)), float(np.dot(c1, c2)))
    R2 = K._rot(m, ang) @ R1
    return TRT(H, R1, H0), TRT(Kp, R2, K0), At, Dn - Dc


LEGS = {}
for s in "LR":
    lo_ = s.lower()
    H0, A0 = HEADP["thigh_" + lo_], HEADP["foot_" + lo_]
    Kn = HEADP["calf_" + lo_]
    LEGS[s] = {"len": float(np.linalg.norm(Kn - H0) + np.linalg.norm(A0 - Kn)),
               "C0": np.array([HEADP["ball_" + lo_][0], HEADP["ball_" + lo_][1], 0.0]),
               "knee_dir": unit((Kn - H0) - float((Kn - H0) @ unit(A0 - H0)) * unit(A0 - H0))}
# hands: knuckle line, palm normal, grip frame
HANDS = {}
for s in "LR":
    lo_ = s.lower()
    wr = HEADP["hand_" + lo_]
    kn = np.mean([HEADP["%s_01_%s" % (f, lo_)] for f in ("index", "middle", "ring", "pinky")], 0)
    a_h = unit(HEADP["pinky_01_" + lo_] - HEADP["index_01_" + lo_])
    e_h = unit((HEADP["middle_01_" + lo_] - wr) - float((HEADP["middle_01_" + lo_] - wr) @ a_h) * a_h)
    n_p = unit(np.cross(e_h, a_h))
    if float((HEADP["thumb_02_" + lo_] - wr) @ n_p) < 0:
        n_p = -n_p
    HANDS[s] = {"a": a_h, "e": e_h, "n": n_p, "grip": 0.45 * wr + 0.55 * kn + n_p * 0.021}
ARM_LEN = {s: float(np.linalg.norm(HEADP["lowerarm_" + s.lower()] - HEADP["upperarm_" + s.lower()]) +
                    np.linalg.norm(HEADP["hand_" + s.lower()] - HEADP["lowerarm_" + s.lower()])) for s in "LR"}
TOE_REACH = max(float(np.max((p["V"] - SHIFT - LEGS[p["side"]]["C0"]) @ p["fd"])) for p in PARTS if p["name"].startswith("toecap"))
TOE_PTS = {}
for s in "LR":
    _tp = [p for p in PARTS if p["name"] in ("toecap." + s, "sole." + s)]
    _V = np.vstack([p["V"] for p in _tp])
    _wb = np.concatenate([smoothstep(-0.022, 0.0, (p["V"] - p["c0"]) @ p["fd"]) for p in _tp])
    TOE_PTS[s] = (_V - SHIFT, _wb)
ROLL = {"idle": 180.0, "walk": 0.0}   # the sword's roll about its own axis per clip (picked below: least wrist bend)
# the grip transform G: the (rest) sword -> held in the (rest) right hand, hammer grip, blade out of the pinky side
R_G = frame_to(HANDS["R"]["e"], -HANDS["R"]["a"])
G = TRT(HANDS["R"]["grip"], R_G, S_(SW_GRIP0))
G_inv = np.linalg.inv(G)


def curl_axis(side, finger):
    """per-finger curl axis (rest world): the knuckle line, signed so +angle moves the fingertip toward the palm side."""
    lo_ = side.lower()
    a = HANDS[side]["a"]
    b1 = "%s_01_%s" % (finger, lo_)
    tip = TAILP["%s_03_%s" % (finger, lo_)] - HEADP[b1]
    moved = K._rot(a, 0.3) @ tip
    return a if float((moved - tip) @ HANDS[side]["n"]) > 0 else -a


CURL_AX = {(s, f): curl_axis(s, f) for s in "LR" for f in ("index", "middle", "ring", "pinky")}


def fingers(D, side, curl, thumb=(0.0, 0.0)):
    lo_ = side.lower()
    for f in ("index", "middle", "ring", "pinky"):
        ax = CURL_AX[(side, f)]
        for k in range(3):
            fk(D, "%s_%02d_%s" % (f, k + 1, lo_), K._rot(ax, math.radians(curl[k] * (0.92 if f == "index" else 1.0))))
    tax = unit(np.cross(HANDS[side]["n"], unit(TAILP["thumb_01_" + lo_] - HEADP["thumb_01_" + lo_])))
    tip0 = TAILP["thumb_03_" + lo_] - HEADP["thumb_01_" + lo_]
    if float((K._rot(tax, 0.3) @ tip0 - tip0) @ HANDS[side]["n"]) < 0:
        tax = -tax
    fk(D, "thumb_01_" + lo_, K._rot(tax, math.radians(thumb[0])))
    fk(D, "thumb_02_" + lo_, K._rot(tax, math.radians(thumb[1])))
    fk(D, "thumb_03_" + lo_, K._rot(tax, math.radians(thumb[1] * 0.6)))


ELB_AX = {}
for s in "LR":
    lo_ = s.lower()
    ua = unit(TAILP["upperarm_" + lo_] - HEADP["upperarm_" + lo_]); la = unit(TAILP["lowerarm_" + lo_] - HEADP["lowerarm_" + lo_])
    ax = unit(np.cross(ua, la))
    ELB_AX[s] = ax if float(ua @ (K._rot(ax, 0.2) @ la)) < float(ua @ la) else -ax      # +angle = more flexion


def free_arm(D, side, lower, fwd, bend, lag_bend=0.0):
    lo_ = side.lower()
    sg = 1.0 if side == "L" else -1.0
    fk(D, "clavicle_" + lo_)
    fk(D, "upperarm_" + lo_, Rx(fwd) @ Ry(sg * lower))
    fk(D, "lowerarm_" + lo_, K._rot(ELB_AX[side], math.radians(bend + lag_bend)))
    fk(D, "hand_" + lo_)


def sword_arm(D, Dsw):
    """the right hand follows the sword (D_hand = D_sword G^-1); the arm reaches the wrist by analytic IK."""
    Dh = Dsw @ G_inv
    fk(D, "clavicle_r")
    Hs = xf(D["clavicle_r"], HEADP["upperarm_r"])
    wrist = xf(Dh, HEADP["hand_r"])
    pole = unit(np.array([-0.35, 0.55, -0.75]))
    D["upperarm_r"], D["lowerarm_r"], reached, err = two_bone("upperarm_r", "lowerarm_r", "hand_r", Hs, wrist, pole)
    D["hand_r"] = Dh
    D["sword"] = Dsw
    return err, float(np.linalg.norm(reached - wrist))


def tassel(D, t, amp, ph=0.0):
    top = xf(D["sword"], S_(TAS_TOP))
    R0 = Ry(amp * math.sin(TAU * t + ph)) @ Rx(0.6 * amp * math.sin(TAU * t + ph + 1.1))
    D["tassel.0"] = TRT(top, R0, S_(TAS_TOP))
    fk(D, "tassel.1", Ry(1.4 * amp * math.sin(TAU * t + ph - 0.8)))


def plant_legs(D, feet, reach_k):
    """feet: {side: (contact point C (world), R_foot, R_ball)}; the pelvis is lowered until every leg reaches at reach_k."""
    info = {}
    for s in "LR":
        lo_ = s.lower()
        C, Rf, Rb = feet[s]
        A = C + Rf @ (HEADP["foot_" + lo_] - LEGS[s]["C0"])
        H = xf(D["pelvis"], HEADP["thigh_" + lo_])
        pole = Rf @ LEGS[s]["knee_dir"]
        D["thigh_" + lo_], D["calf_" + lo_], At, err = two_bone("thigh_" + lo_, "calf_" + lo_, "foot_" + lo_, H, A, pole)
        D["foot_" + lo_] = TRT(At, Rf, HEADP["foot_" + lo_])
        D["ball_" + lo_] = TRT(xf(D["foot_" + lo_], HEADP["ball_" + lo_]), Rb, HEADP["ball_" + lo_])
        info[s] = err
    return info


def pelvis_height(Rp, off_xy, feet, reach_k):
    """highest pelvis z offset at which every foot's ankle target is within reach_k x the leg length."""
    best = math.inf
    for s in "LR":
        lo_ = s.lower()
        C, Rf, _ = feet[s]
        A = C + Rf @ (HEADP["foot_" + lo_] - LEGS[s]["C0"])
        h0 = HEADP["pelvis"] + Rp @ (HEADP["thigh_" + lo_] - HEADP["pelvis"]) + np.array([off_xy[0], off_xy[1], 0.0])
        r = reach_k * LEGS[s]["len"]
        dxy2 = float((A[0] - h0[0]) ** 2 + (A[1] - h0[1]) ** 2)
        best = min(best, A[2] + math.sqrt(max(r * r - dxy2, 1e-9)) - h0[2])
    return best


# ---- idle: the planted sword (tip on the floor, leaning), the hand on its grip
_tip_rest = S_(SW_T + SWL["tip"])
IDLE_TIP = np.array([SWORD_IDLE_TIP[0] - SHIFT[0], SWORD_IDLE_TIP[1] - SHIFT[1], 0.0])
IDLE_FEET_C = {}
for s in "LR":
    dx, dy, yaw = IDLE_FEET[s]
    sg = 1.0 if s == "L" else -1.0
    IDLE_FEET_C[s] = (LEGS[s]["C0"] + np.array([sg * dx, dy, 0.0]), Rz(-sg * yaw))


def ease_wave(x):
    """a periodic weight-shift wave that eases in, settles and holds (tanh-shaped sine; IDLE_EASE 0 = pure sine)."""
    return math.sin(x) if IDLE_EASE <= 0 else math.tanh(IDLE_EASE * math.sin(x)) / math.tanh(IDLE_EASE)


def pose_idle(t):
    D = {"root": np.eye(4)}
    info = {}
    br = math.sin(TAU * t)                                   # one breath per loop
    sway = ease_wave(TAU * t + 0.7)
    sway_c = ease_wave(TAU * (t - IDLE_OVERLAP[0] / IDLE_N) + 0.7)      # the chest follows the pelvis late (overlap)
    sway_h = ease_wave(TAU * (t - IDLE_OVERLAP[1] / IDLE_N) + 0.7)      # ... the head / free arm later still
    feet = {s: (IDLE_FEET_C[s][0], IDLE_FEET_C[s][1], IDLE_FEET_C[s][1]) for s in "LR"}
    Rp = Ry(IDLE_WEIGHT[1] + 0.4 * sway) @ Rz(-2.0)
    off = np.array([IDLE_WEIGHT[0] + IDLE_SWAY * sway, 0.004, 0.0])
    dz = min(0.0, pelvis_height(Rp, off[:2], feet, IDLE_REACH))
    D["pelvis"] = Tt(off + np.array([0, 0, dz - 0.003 * (0.5 + 0.5 * br)])) @ Tr(HEADP["pelvis"], Rp)
    fk(D, "spine_01", Ry(-0.5 * IDLE_WEIGHT[1]) @ Rx(-0.3 * IDLE_BREATH_DEG * br))
    fk(D, "spine_02", Ry(-0.4 * IDLE_WEIGHT[1] - 0.3 * sway_c) @ Rx(-0.5 * IDLE_BREATH_DEG * br))
    fk(D, "spine_03", Rz(3.0) @ Ry(-0.25 * sway_c) @ Rx(-IDLE_BREATH_DEG * br - 1.5))
    fk(D, "neck_01", Rx(0.5 * IDLE_HEAD[0]))
    fk(D, "head", Rz(IDLE_HEAD[1] * math.sin(TAU * t + 1.9)) @ Rx(0.5 * IDLE_HEAD[0] + 0.6 * math.sin(TAU * t + 0.4))
       @ Ry(-1.5 - 0.5 * sway_h))
    info.update(plant_legs(D, feet, IDLE_REACH))
    # the planted sword, leaning, with a breath sway about its tip
    Rl = Rx(SWORD_IDLE_LEAN[1] + SWORD_IDLE_SWAY * br) @ Ry(SWORD_IDLE_LEAN[0] + 0.4 * SWORD_IDLE_SWAY * sway) @ Rz(ROLL["idle"])
    Dsw = TRT(IDLE_TIP, Rl, _tip_rest)
    info["arm_R"], info["wrist_R"] = sword_arm(D, Dsw)
    free_arm(D, "L", IDLE_LARM[0] + 1.0 * br, IDLE_LARM[1] + 1.5 * sway_h, IDLE_LARM[2])
    fingers(D, "R", GRIP_CURL, GRIP_THUMB)
    fingers(D, "L", RELAX_CURL, (4.0, 8.0))
    tassel(D, t, 2.0)
    return D, info


def drape_local(cn, ch, k, t):
    """the static drape + the (scaled) v1 air-drift waves of chain bone k, as a local rotation; the follow-through lag
    is composed on top of this in apply_chains."""
    dk = FT_DRIFT_K
    if cn == "idle":
        if ch.startswith("hair"):
            return Rx((0.8 if "back" in ch else -0.4) * dk * K.vine_wave(t, k, HAIR_BONES, IDLE_HAIR_DEG[0], IDLE_HAIR_DEG[1], 0.7, 1, 0.9 * len(ch))) \
                @ Ry(0.6 * dk * K.vine_wave(t, k, HAIR_BONES, IDLE_HAIR_DEG[0], IDLE_HAIR_DEG[1], 0.7, 1, 2.0 + len(ch)))
        return Rx(0.6 + dk * K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], 0.8, 1, 1.1 * int(ch[-1]))) \
            @ Ry(0.5 * dk * K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], 0.8, 1, 2.3 + int(ch[-1])))
    if ch.startswith("hair"):
        a = (WALK_HAIR[0] * (k + 1) / HAIR_BONES + dk * K.vine_wave(t, k, HAIR_BONES, WALK_HAIR[1], WALK_HAIR[2], 0.8, 2, 0.7 * len(ch))) \
            if "back" in ch else -1.0 + 0.5 * dk * K.vine_wave(t, k, HAIR_BONES, WALK_HAIR[1], WALK_HAIR[2], 0.8, 2, 0.4)
        return Rx(a) @ Ry(0.5 * dk * K.vine_wave(t, k, HAIR_BONES, WALK_HAIR[1], WALK_HAIR[2], 0.8, 1, 1.3 + len(ch)))
    return Rx(WALK_CAPE[0] * (0.55 + 0.45 * k / (CAPE_BONES - 1)) + dk * K.vine_wave(t, k, CAPE_BONES, WALK_CAPE[1], WALK_CAPE[2], 0.9, 2, 0.8 * int(ch[-1]))) \
        @ Ry(0.5 * dk * K.vine_wave(t, k, CAPE_BONES, WALK_CAPE[1], WALK_CAPE[2], 0.9, 1, 1.7 + int(ch[-1])))


# ---- walk: in-place stride, planted feet slide back at ground speed; the sword trails in the right hand
WALK_SPEED = 2 * WALK_STEP_A / (WALK_STANCE * WALK_N / K.FPS)          # m/s implied by the stance travel
BALL_H = {s: float(HEADP["ball_" + s.lower()][2]) for s in "LR"}


def foot_walk(s, t):
    t0 = 0.0 if s == "L" else 0.5
    u = (t - t0) % 1.0
    sg = 1.0 if s == "L" else -1.0
    C0 = LEGS[s]["C0"]
    x = C0[0] * WALK_FOOT_X
    Yc = C0[1]
    beta = WALK_STANCE
    if u < beta:
        p = u / beta
        y = Yc - WALK_STEP_A + 2 * WALK_STEP_A * p
        z = 0.0
        roll = WALK_TOEOFF[1] * ss5(beta - WALK_TOEOFF[0], beta, u) if u > beta - WALK_TOEOFF[0] else 0.0
        stance = True
    else:
        q = (u - beta) / (1 - beta)
        y = Yc + WALK_STEP_A - 2 * WALK_STEP_A * (q - math.sin(TAU * q) / TAU)
        z = WALK_LIFT * math.sin(math.pi * q) ** 1.2
        roll = WALK_TOEOFF[1] * (1.0 - ss5(0.0, 0.6, q))
        stance = False
    yaw = Rz(-sg * 4.0)
    Rf = yaw @ Rx(roll)
    # rolling about the floor contact drops the ball joint (BALL_H above it) by BALL_H (1 - cos roll): lift that back so
    # the flat toes never go under the floor
    z += BALL_H[s] * (1.0 - math.cos(math.radians(roll)))
    # the toes stay flat on the floor in stance; in swing they point down only as far as the lift clears the toe tip
    # (x 0.3: the pivot sits BALL_H above the floor, so the tip drops more than TOE_REACH sin(angle); measured toe-tip
    #  floor dips -2.1 mm at x 1.0, -1.3 mm at x 0.6)
    toe = 0.0 if stance else min(0.35 * roll, 0.3 * math.degrees(math.asin(min(1.0, z / TOE_REACH))))
    Rb = yaw @ Rx(toe)
    if not stance:
        # v2 toe-dip fix: v1 dipped the toe-cap tip 0.63 mm under the floor at swing start. Pose the toe cap + sole rigidly
        # (foot / ball blend as skinned) and lift the swing foot by whatever still reaches below z = 0.
        lo_ = s.lower()
        C_ = np.array([x, y, z])
        pf = C_ + (TOE_PTS[s][0] - C0) @ Rf.T
        hb = C_ + Rf @ (HEADP["ball_" + lo_] - C0)
        pb = hb + (TOE_PTS[s][0] - HEADP["ball_" + lo_]) @ Rb.T
        zmin = float(((1.0 - TOE_PTS[s][1]) * pf[:, 2] + TOE_PTS[s][1] * pb[:, 2]).min())
        z += max(0.0, -zmin)
    return np.array([x, y, z]), Rf, Rb, stance


def pose_walk(t):
    D = {"root": np.eye(4)}
    info = {}
    c1 = math.cos(TAU * t)
    s1 = math.sin(TAU * (t - 0.05))
    feet, st = {}, {}
    for s in "LR":
        C, Rf, Rb, stc = foot_walk(s, t)
        feet[s] = (C, Rf, Rb); st[s] = stc
    yaw = -WALK_PELVIS[0] * c1
    roll = -WALK_PELVIS[1] * s1
    Rp = Rz(yaw) @ Ry(roll)
    off = np.array([WALK_PELVIS[2] * s1, 0.0, 0.0])
    dz = pelvis_height(Rp, off[:2], feet, WALK_REACH)
    D["pelvis"] = Tt(off + np.array([0, 0, dz])) @ Tr(HEADP["pelvis"], Rp)
    # overlap: the chest's counter-yaw and bob come WALK_OVERLAP[0] frames after the pelvis, the head's WALK_OVERLAP[1]
    tc, th = t - WALK_OVERLAP[0] / WALK_N, t - WALK_OVERLAP[1] / WALK_N
    c1c, c1h = math.cos(TAU * tc), math.cos(TAU * th)
    s1h = math.sin(TAU * (th - 0.05))
    fk(D, "spine_01", Rz(-0.35 * yaw) @ Ry(-0.4 * roll))
    fk(D, "spine_02", Rz(-0.45 * yaw + 0.5 * WALK_CHEST[0] * c1c) @ Ry(-0.4 * roll) @ Rx(-0.5 * WALK_CHEST[1]))
    fk(D, "spine_03", Rz(-0.3 * yaw + 0.5 * WALK_CHEST[0] * c1c) @ Ry(-0.2 * roll) @ Rx(-0.5 * WALK_CHEST[1] + WALK_NOD[1] * math.sin(2 * TAU * tc)))
    fk(D, "neck_01", Rz(-0.5 * WALK_CHEST[0] * c1h) @ Rx(0.5 * WALK_CHEST[1]))
    fk(D, "head", Rz(-0.4 * WALK_CHEST[0] * c1h) @ Rx(0.5 * WALK_CHEST[1] + 2.0 - WALK_NOD[0] * math.sin(2 * TAU * th))
       @ Ry(-0.5 * WALK_PELVIS[1] * s1h))
    info.update(plant_legs(D, feet, WALK_REACH))
    # the sword: grip beside the right hip, blade trailing back-down-out, a small pendulum with the stride
    sw = SWORD_WALK
    dn, ou = math.radians(sw["down"]), math.radians(sw["out"])
    sdir = np.array([-math.cos(dn) * math.sin(ou), math.cos(dn) * math.cos(ou), -math.sin(dn)])
    Rsw = K._rot(sdir, math.radians(ROLL["walk"])) @ frame_to(np.array([0.0, -0.15, -1.0]), -sdir)
    Rsw = rot((1, 0, 0), sw["swing"] * c1) @ Rsw
    # the wrist hangs at WALK_ARM_REACH of the arm's length from the shoulder (always reachable); the grip follows
    fk(D, "clavicle_r")
    Hs = xf(D["clavicle_r"], HEADP["upperarm_r"])
    dh = unit(np.array([sw["out_x"], -0.05 - 2.0 * sw["fore"] * c1, -1.0]))
    W_ = Hs + WALK_ARM_REACH * ARM_LEN["R"] * dh
    P = W_ - (Rsw @ R_G.T) @ (HEADP["hand_r"] - HANDS["R"]["grip"])
    Dsw = TRT(P, Rsw, S_(SW_GRIP0))
    info["arm_R"], info["wrist_R"] = sword_arm(D, Dsw)
    free_arm(D, "L", WALK_LARM[0], WALK_LARM[1] * c1, WALK_LARM[2], lag_bend=6.0 * math.cos(TAU * t - 0.6) + 6.0)
    fingers(D, "R", GRIP_CURL, GRIP_THUMB)
    fingers(D, "L", RELAX_CURL, (4.0, 8.0))
    tassel(D, t, 7.0, 0.5)
    info["stance"] = st
    info["feet"] = feet
    info["pelvis_dz"] = dz
    return D, info


def wrist_bend(D):
    Rw = D["lowerarm_r"][:3, :3].T @ D["hand_r"][:3, :3]
    return math.degrees(math.acos(float(np.clip((np.trace(Rw) - 1) / 2, -1, 1))))


# the sword's roll about its own axis: the angle (10 deg steps) that leaves the gripping wrist least bent at t = 0
ROLL_SEARCH = {}
for cn_, fn_ in (("idle", pose_idle), ("walk", pose_walk)):
    best_ = None
    for r_ in range(0, 360, 10):
        ROLL[cn_] = float(r_)
        D_, info_ = fn_(0.0)
        b_ = wrist_bend(D_) + 1000.0 * info_["arm_R"]
        if best_ is None or b_ < best_[0] - 1e-9:
            best_ = (b_, r_)
    ROLL[cn_] = float(best_[1])
    ROLL_SEARCH[cn_] = {"roll_deg": best_[1], "wrist_bend_deg_at_t0": round(best_[0], 2)}
print("ROLL", json.dumps(ROLL_SEARCH))
CLIP_N = {"idle": IDLE_N, "walk": WALK_N}
POSE = {"idle": pose_idle, "walk": pose_walk}
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
ORDER = ["root"] + DEFORM


def basis(D, n):
    return np.linalg.inv(REST4[n]) @ np.linalg.inv(D[PARENT[n]]) @ D[n] @ REST4[n]


PARENT["root"] = None


# ---- v2 follow-through (secondary motion) for the hair + cape chains
def rotvec(R):
    q = Matrix(np.asarray(R).tolist()).to_quaternion()
    if q.w < 0:
        q.negate()
    ax_, ang_ = q.to_axis_angle()
    return np.array(ax_) * ang_


def exp_rv(v):
    a = float(np.linalg.norm(v))
    return np.eye(3) if a < 1e-12 else K._rot(v / a, a)


FT_CHAINS = sorted(c for c in CHAIN_PTS if c != "tassel")
INTO_SIGN = {"hair_front": 1.0, "hair_back": -1.0, "cape": -1.0}   # the X-rotation sign that swings a chain INTO the body


def ft_kind(ch):
    return "cape" if ch.startswith("cape") else ch.split(".")[0]


def ft_nb(ch):
    return CAPE_BONES if ch.startswith("cape") else HAIR_BONES


def secondary(cn, N):
    """per chain bone k: world rotation y_k = H_k * y_(k-1), y_(-1) = the driver's rotation + the pendulum drag of the
    chain root's acceleration, H = a damped spring (FT hz / zeta) applied as the PERIODIC steady state in the frequency
    domain (so the loop stays exact). The keyed lag is y_k - (the chain parent's own rotation), soft-limited into the body."""
    Ds = [POSE[cn](f / N)[0] for f in range(N)]
    w = 2 * math.pi * np.fft.fftfreq(N, d=1.0 / K.FPS)
    out, lag = {}, {}
    lim = math.radians(FT_INTO_BODY_DEG)
    for ch in FT_CHAINS:
        P_ = FT[ft_kind(ch)]
        par = CHAIN_PTS[ch][2]
        rd = np.array([rotvec(D[P_["driver"]][:3, :3] @ Ds[0][P_["driver"]][:3, :3].T) for D in Ds])
        rp = np.array([rotvec(D[par][:3, :3] @ Ds[0][par][:3, :3].T) for D in Ds])
        anc = np.array([xf(D[par], HEADP[ch + ".0"]) for D in Ds])
        acc = np.real(np.fft.ifft(np.fft.fft(anc, axis=0) * (-(w ** 2))[:, None], axis=0))
        u = rd + P_["drag"] * np.stack([-acc[:, 1], acc[:, 0], np.zeros(N)], 1) / 9.81
        w0 = 2 * math.pi * P_["hz"]
        H = w0 ** 2 / (w0 ** 2 - w ** 2 + 2j * P_["zeta"] * w0 * w)
        y, rows = u, []
        sg_ = INTO_SIGN[ft_kind(ch)]
        for k in range(ft_nb(ch)):
            y = np.real(np.fft.ifft(np.fft.fft(y, axis=0) * H[:, None], axis=0))
            d = P_["gain"] * (y - rp)
            x_ = d[:, 0] * sg_
            d[:, 0] = sg_ * np.where(x_ > 0, lim * np.tanh(x_ / lim), x_)
            rows.append(d)
        out[ch] = [[exp_rv(rows[k][f]) for k in range(len(rows))] for f in range(N)]
        uu = u - u.mean(0)
        ax_ = int(np.argmax(uu.var(0)))
        tip = y[:, ax_] - y[:, ax_].mean()
        cc = [float(np.dot(np.roll(uu[:, ax_], s_), tip)) for s_ in range(N)]
        L_ = int(np.argmax(cc))
        L_ = L_ - N if L_ > N // 2 else L_
        lag[ch] = {"tip_lag_frames": L_, "axis": "xyz"[ax_], "driver_ptp_deg": round(math.degrees(float(np.ptp(uu[:, ax_]))), 2),
                   "tip_follow_through_ptp_deg": round(math.degrees(float(np.ptp(rows[-1][:, ax_]))), 2)}
    return out, lag


def apply_chains(D, cn, t, f):
    for ch in FT_CHAINS:
        Lp = np.eye(3)
        for k in range(ft_nb(ch)):
            n = "%s.%d" % (ch, k)
            Rpv = D[PARENT[n]][:3, :3]
            Lk = SEC[cn][ch][f][k]
            fk(D, n, Rpv.T @ Lk @ Lp.T @ Rpv @ drape_local(cn, ch, k, t))
            Lp = Lk


t_ = time.time()
SEC, FT_LAG = {}, {}
for cn, N in CLIP_N.items():
    SEC[cn], FT_LAG[cn] = secondary(cn, N)
rep["follow_through"] = {"params": FT, "into_body_limit_deg": FT_INTO_BODY_DEG, "drift_k": FT_DRIFT_K, "per_clip": FT_LAG,
                         "seconds": round(time.time() - t_, 1)}
print("FOLLOW", json.dumps(FT_LAG))
t_ = time.time()
ACTS, key_rows, ik_worst = {}, [], {}
for cn, N in CLIP_N.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    for f in range(N + 1):                       # frames 1..N+1; frame N+1 == frame 1 (closed-form cycles -> exact seam)
        D, info = POSE[cn]((f % N) / N)
        apply_chains(D, cn, (f % N) / N, f % N)
        for k_, v_ in info.items():
            if isinstance(v_, float):
                ik_worst[(cn, k_)] = max(ik_worst.get((cn, k_), 0.0), abs(v_))
        for n in ORDER:
            Bm = np.eye(4) if n == "root" else basis(D, n)
            q = Matrix(Bm[:3, :3].tolist()).to_quaternion(); q.normalize()
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = rig.pose.bones[n]
            pb.location = Vector(Bm[:3, 3]); pb.rotation_quaternion = q
            pb.keyframe_insert("location", frame=f + 1)
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))
print("KEYED", round(time.time() - t_, 1), json.dumps({"%s.%s" % k: round(v, 5) for k, v in ik_worst.items()}))


# ---- clip measurement (evaluated meshes, every frame)
def eval_coords(ob):
    dg_ = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg_)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


from mathutils.kdtree import KDTree  # noqa: E402

RM = OBJ["main"]["RANGE"]
RS = OBJ["sword"]["RANGE"]


def rng(name, R_=RM):
    return np.arange(*R_[name])


domM = np.array([DEFORM[j] for j in np.argmax(WM, 1)], dtype=object)
body_ids = rng("body")
LEG_IDS = np.concatenate([body_ids[np.isin(domM[body_ids], ["thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r", "ball_l", "ball_r"])]] +
                         [rng(n) for n in RM if n.split(".")[0] in ("toecap", "sole", "heel", "kneecop", "bootcuff")])
LARM_IDS = body_ids[np.array([d.endswith("_l") and d.split("_")[0] in ("upperarm", "lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")
                              for d in domM[body_ids]])]
RARM_IDS = body_ids[np.array([d.endswith("_r") and d.split("_")[0] in ("upperarm", "lowerarm", "hand")
                              for d in domM[body_ids]])]
TORSO_IDS = body_ids[np.isin(domM[body_ids], ["pelvis", "spine_01", "spine_02", "spine_03", "thigh_l", "thigh_r"])]
CAPE_IDS = rng("cape")
BLADE_IDS = np.arange(RS["sword"][0], RS["sword"][1])
V0m = OBJ["main"]["V"]; V0s = OBJ["sword"]["V"]
CONTACT = {}
for s in "LR":
    so = rng("sole." + s)
    bot = so[np.abs(V0m[so, 2]) < 1e-6]
    C0s = LEGS[s]["C0"]
    CONTACT[s] = int(bot[np.argmin(np.linalg.norm(V0m[bot, :2] - C0s[:2], axis=1))])
    he = rng("heel." + s)
    CONTACT["heel" + s] = he[np.abs(V0m[he, 2]) < 1e-6]
tip_id = int(BLADE_IDS[np.argmin(V0s[BLADE_IDS, 2])])
# v3: the long front strands vs the chest (signed: < 0 = inside the body / breastplate) and the pauldrons vs the cape
FHAIR_IDS = np.concatenate([rng(n) for n in RM if n.startswith("lock.front")])
FHAIR_IDS = FHAIR_IDS[V0m[FHAIR_IDS, 2] < Z_BP_TOP]   # the chest part (above it the scalp skin is removed under the cap)
PAUL_IDS = np.concatenate([rng(n) for n in RM if n.split(".")[0] in ("pauldron", "pauldronlame")])
_bpf0 = OBJ["main"]["FRANGE"]["breastplate"][0]      # the plate's OUTER skin only (its inner skin / rim flip the sign)
CHEST_TRIS = [list(f) for f in OBJ["main"]["F"][slice(*OBJ["main"]["FRANGE"]["body"])]] + \
    [list(f) for f in OBJ["main"]["F"][_bpf0:_bpf0 + len(Fbp)]]
CHEST_TRIS = [[f[0], f[k], f[k + 1]] for f in CHEST_TRIS for k in range(1, len(f) - 1)]


def kd(P):
    t_k = KDTree(len(P))
    for i, p in enumerate(P):
        t_k.insert(p, i)
    t_k.balance()
    return t_k


def mind(tree, P):
    return float(min(tree.find(p)[2] for p in P))


samples = []
clip_rep = {}
grip_rel = np.linalg.inv(REST4["hand_r"]) @ G @ REST4["sword"]
for cn, N in CLIP_N.items():
    K.assign_action(rig, ACTS[cn])
    first = last = firsts = lasts = None
    minz, minz_s, root_off, grip_dev, wrist_max = 1e9, 1e9, 0.0, 0.0, 0.0
    tips, heels = {s: [] for s in "LR"}, {s: [] for s in "LR"}
    gaps = {"cape_to_legs": 1e9, "cape_to_left_arm": 1e9, "cape_to_right_arm": 1e9, "blade_to_cape": 1e9,
            "blade_to_legs": 1e9, "blade_to_torso": 1e9, "cape_to_pauldrons": 1e9, "front_hair_to_chest_signed": 1e9}
    tipz = 1e9
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low); Sw = eval_coords(swo)
        samples.append(C[::9]); samples.append(Sw[::5])
        if f == 1:
            first, firsts = C, Sw
        if f == N + 1:
            last, lasts = C, Sw
        minz = min(minz, float(C[:, 2].min())); minz_s = min(minz_s, float(Sw[:, 2].min()))
        tipz = min(tipz, float(Sw[tip_id, 2]))
        root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        Mh = np.array(rig.pose.bones["hand_r"].matrix); Ms = np.array(rig.pose.bones["sword"].matrix)
        grip_dev = max(grip_dev, float(np.abs(np.linalg.inv(Mh) @ Ms - grip_rel).max()))
        Ml = np.array(rig.pose.bones["lowerarm_r"].matrix)
        Rw = (np.linalg.inv(Ml) @ Mh)[:3, :3] @ np.linalg.inv((np.linalg.inv(REST4["lowerarm_r"]) @ REST4["hand_r"])[:3, :3])
        wrist_max = max(wrist_max, math.degrees(math.acos(float(np.clip((np.trace(Rw) - 1) / 2, -1, 1)))))
        for s in "LR":
            tips[s].append(C[CONTACT[s]].copy()); heels[s].append(C[CONTACT["heel" + s]].mean(0))
        if f % 2 == 1 and f <= N:
            kc = kd(C[CAPE_IDS[::2]])
            gaps["cape_to_legs"] = min(gaps["cape_to_legs"], mind(kc, C[LEG_IDS[::3]]))
            gaps["cape_to_left_arm"] = min(gaps["cape_to_left_arm"], mind(kc, C[LARM_IDS[::2]]))
            gaps["cape_to_right_arm"] = min(gaps["cape_to_right_arm"], mind(kc, C[RARM_IDS[::2]]))
            gaps["blade_to_cape"] = min(gaps["blade_to_cape"], mind(kc, Sw[BLADE_IDS[::3]]))
            kb = kd(Sw[BLADE_IDS])
            gaps["blade_to_legs"] = min(gaps["blade_to_legs"], mind(kb, C[LEG_IDS[::3]]))
            gaps["blade_to_torso"] = min(gaps["blade_to_torso"], mind(kb, C[TORSO_IDS[::3]]))
            gaps["cape_to_pauldrons"] = min(gaps["cape_to_pauldrons"], mind(kc, C[PAUL_IDS]))
            bc_ = BVHTree.FromPolygons(C.tolist(), CHEST_TRIS)
            for p_ in C[FHAIR_IDS[::2]]:
                q_, n_, _, d_ = bc_.find_nearest(Vector(p_))
                if q_ is not None and d_ < 0.05:
                    gaps["front_hair_to_chest_signed"] = min(gaps["front_hair_to_chest_signed"],
                                                             d_ if float((p_ - np.array(q_)) @ np.array(n_)) >= 0 else -d_)
    row = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 4), "cyclic": True,
           "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
           "seam_sword_mm": round(float(np.linalg.norm(firsts - lasts, axis=1).max()) * 1000, 6),
           "min_z_main": round(minz, 5), "min_z_sword": round(minz_s, 5),
           "sword_tip_min_z": round(tipz, 4),
           "root_offset_max": round(root_off, 8), "grip_relation_max_dev": round(grip_dev, 8),
           "right_wrist_bend_max_deg": round(wrist_max, 2),
           "ik_unreachable_max_m": {k[1]: round(v, 5) for k, v in ik_worst.items() if k[0] == cn},
           "clearance_min_m": {k: round(v, 4) for k, v in gaps.items()}}
    tips = {s: np.array(v) for s, v in tips.items()}; heels = {s: np.array(v) for s, v in heels.items()}
    if cn == "walk":
        gait = {}
        for s in "LR":
            fl = []
            for f in range(N):
                _, _, _, stc = foot_walk(s, f / N)
                t0 = 0.0 if s == "L" else 0.5
                u = (f / N - t0) % 1.0
                if stc and u <= WALK_STANCE - WALK_TOEOFF[0] + 1e-9:
                    fl.append(f)
            P_ = tips[s][fl]
            H_ = heels[s][fl]
            dy = P_[:, 1] - P_[0, 1]
            ideal = WALK_SPEED * (np.array(fl) - fl[0]) / K.FPS
            gait[s] = {"flat_stance_frames": len(fl), "ball_contact_z_range": [round(float(P_[:, 2].min()), 5), round(float(P_[:, 2].max()), 5)],
                       "heel_tip_z_range": [round(float(H_[:, 2].min()), 5), round(float(H_[:, 2].max()), 5)],
                       "lateral_x_drift": round(float(np.ptp(P_[:, 0])), 6),
                       "slip_vs_ground_m": round(float(np.abs(dy - ideal).max()), 6),
                       "swing_contact_z_max": round(float(tips[s][:, 2].max()), 4)}
        step = 2 * WALK_STEP_A / WALK_STANCE * 0.5
        row.update({"gait": gait, "cadence_steps_per_min": round(2 * 60.0 * K.FPS / N, 2),
                    "stance_travel_m": round(2 * WALK_STEP_A, 4), "step_length_m": round(step, 4),
                    "stride_length_m": round(2 * step, 4), "ground_speed_m_per_s": round(WALK_SPEED, 4),
                    "ground_speed_body_heights_per_s": round(WALK_SPEED / Z_TOP, 4),
                    "stance_fraction": WALK_STANCE, "foot_lift_m": WALK_LIFT, "toe_off_roll_deg": WALK_TOEOFF[1],
                    "rule": "zero-slip: in flat stance (before toe-off) the ball-contact sole vertex moves back at exactly the "
                            "ground speed (slip = max deviation from that line), with no lift and no lateral drift"})
    else:
        row["feet_planted"] = {s: {"ball_contact_xy_drift": round(float(np.linalg.norm(tips[s][:, :2] - tips[s][0, :2], axis=1).max()), 6),
                                   "ball_contact_z_range": [round(float(tips[s][:, 2].min()), 5), round(float(tips[s][:, 2].max()), 5)]}
                               for s in "LR"}
    clip_rep[cn] = row
    print("CLIP", cn, json.dumps(row))
    sys.stdout.flush()
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
# render focus boxes at the idle's first frame (the beauty renders use --pose idle:1)
K.assign_action(rig, ACTS["idle"]); scene.frame_set(1)
C1 = eval_coords(low); S1 = eval_coords(swo)
guard1 = S1[BLADE_IDS][np.argmax(S1[BLADE_IDS, 2])]
FOCUS["sword"] = box([guard1 + np.array([0, 0, -0.45]), guard1 + np.array([0, 0, 0.30])], 0.07)
FOCUS["sword_full"] = box(S1, 0.03)
_eyes1 = C1[np.concatenate([rng("eye.L"), rng("eye.R")])]
FOCUS["eyes"] = [(_eyes1.min(0) - np.array([0.012, 0.0, 0.004])).tolist(), (_eyes1.max(0) + np.array([0.012, 0.0, 0.016])).tolist()]
FOCUS["hand"] = box([xf(np.array(rig.pose.bones["hand_r"].matrix) @ np.linalg.inv(REST4["hand_r"]), HEADP["hand_r"])], 0.10)
low["conquest_focus"] = json.dumps(FOCUS)
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rep["grip"] = {"rule": "hammer grip: the handle runs along the right hand's knuckle line (index -> pinky), blade out of the "
                       "pinky side, the edge along the knuckles; the sword bone is a child of hand_r and every clip keys it "
                       "at the SAME hand-relative transform (grip_relation_max_dev); the REST pose stands the sword point-down "
                       "beside the right hand (not in it: in-hand at the A-pose would push the bbox off-centre)",
               "G_hand_to_sword_rest4": grip_rel.round(6).tolist(),
               "sword_roll_search": ROLL_SEARCH}
rep["tris"] = {"model": report["tris"]["total"], "outline_shells": 0, "budget": TRI_BUDGET}
print("TRIS", json.dumps(rep["tris"]))
rig["conquest_rig"] = ("vampwarrior v3: root (contract) > MPFB2 game_engine skeleton (pelvis, spine_01-03, neck_01, head, "
                       "clavicle / upperarm / lowerarm / hand + 15 finger bones per side, thigh / calf / foot / ball) + "
                       "hair_front.L/R.0-3, hair_back.L/C/R.0-3, cape.0-4.0-3 (chest children) + sword (hand_r child) > tassel.0-1")
low["conquest_clips"] = list(CLIP_N)
low["conquest_clip_status"] = ("idle (planted-sword guard, breath, eased weight shift) + walk (in-place stride, sword trailing, "
                               "chest / head overlap); hair + cape follow-through (damped-spring lag); no attack/hit/death")
low["conquest_look"] = ("v3: v1 shaded material (Col x baked AO, baked normal map); no outline shells, no cel bands; the "
                        "stylisation is DRAWN into the palette regions (shadow shapes, brows, liner contour)")
low["conquest_locomotion"] = "biped in heeled boots: rest pose on the floor per contract (soles z 0), clips in place"
low["conquest_sword_grip"] = json.dumps(grip_rel.round(6).tolist())
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for a in list(bpy.data.actions):
    if a.name not in CLIP_N:
        bpy.data.actions.remove(a)
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

# =========================================================================== 9. glb + skins
for o in scene.objects:
    o.select_set(o in (rig, low, swo))
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, ACTS["idle"])
t_ = time.time()
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False)
rig.animation_data.action = None


def glb_carries(path):
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    prims = [(m.get("name"), p) for m in js.get("meshes", []) for p in m["primitives"]]
    return {"materials": [{"name": m.get("name"), "alphaMode": m.get("alphaMode", "OPAQUE"), "doubleSided": m.get("doubleSided", False),
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "colour_sets": sorted(k for k in p["attributes"] if k.startswith("COLOR"))} for n, p in prims],
            "nodes": len(js.get("nodes", [])), "mesh_nodes": sorted(n.get("name") for n in js.get("nodes", []) if "mesh" in n),
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": [len(s_["joints"]) for s_ in js.get("skins", [])], "extensionsUsed": js.get("extensionsUsed", [])}


def glb_winding(path, mesh_name):
    """signed volume of a glb mesh (positions + indices read straight from the binary chunk): < 0 = inward faces."""
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    b0 = 20 + L + 8
    ctype = {5123: np.uint16, 5125: np.uint32, 5126: np.float32}

    def acc(i):
        a = js["accessors"][i]; bv = js["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC3": 3}[a["type"]]
        off = b0 + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.frombuffer(data, dtype=ctype[a["componentType"]], count=a["count"] * n, offset=off).reshape(-1, n)
    vol, tris, doubles = 0.0, 0, []
    for m in js["meshes"]:
        if m.get("name") != mesh_name:
            continue
        for p in m["primitives"]:
            P = acc(p["attributes"]["POSITION"]).astype(float)
            I = acc(p["indices"]).reshape(-1, 3).astype(np.int64)
            c = P.mean(0)
            vol += float(np.einsum("ij,ij->i", P[I[:, 0]] - c, np.cross(P[I[:, 1]] - c, P[I[:, 2]] - c)).sum() / 6.0)
            tris += len(I)
            doubles.append(js["materials"][p["material"]].get("doubleSided", False))
    return {"signed_volume_m3": round(vol, 6), "inward": vol < 0, "tris": tris, "double_sided": doubles}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t_, 1), "carries": glb_carries(OUT_GLB),
              "body_winding_ref": glb_winding(OUT_GLB, low.data.name),
              "structure": "armature + skinned 'vampwarrior' (body, opaque) + skinned 'vampwarrior_sword' (own node, bone "
                           "'sword'); material Col x AO + normal map (v1 wiring); natural scale (metres); report-only "
                           "cell fit %.5f" % k_fit}
print("GLB_WINDING", json.dumps(rep["glb"]["body_winding_ref"]))
ALLM = [low, swo]
geo0 = geometry_digest(ALLM)
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]}}
pal_d = PAL.load(UNIT, "dawn")
gt_d = glow_tiers(pal_d)
assert gt_d["grade_pass"] and gt_d["hue_pass"], gt_d
counts_d = repaint(ALLM, pal_d)
out_d = OUT_RIGGED[:-6] + "__dawn.blend"
bpy.ops.wm.save_as_mainfile(filepath=out_d, copy=True, compress=True, relative_remap=False)
rep["skins"]["dawn"] = {"file": out_d, "palette": PAL.table(pal_d), "palette_files": pal_d["files"], "glow_tiers": gt_d,
                        "region_faces": counts_d, "geometry_colour_uv_digest": geometry_digest(ALLM)}
repaint(ALLM, pal_default)
rep["skins"]["repaint_proof"] = {"rule": "a skin is a pure palette swap: same regions / faces / vertices; default restored",
                                 "default_digest_before": geo0, "default_digest_after_restore": geometry_digest(ALLM)}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
