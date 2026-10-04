"""Wren -- the Oakvale farm-boy HERO build (second humanoid Conquest unit, the vampwarrior lessons applied from the start).

    blender --background --factory-startup --python improve/wren_build.py -- \
        [--preview <out.blend>]          (body + outfit + hair [+ pitchfork] + regions + palette only: no bake, no rig)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--hair-digest-only <out.json>]  (v5.1: sections 1-5 only, saves NOTHING but the exact hair digest: the hair-face
                                          decoupling probe -- run with a face --set, it must equal the build's hair_geometry)
        [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-09-28 "NEW UNIT: Wren" + design/reference/wren-character-sheet.webp (three views + head /
necklace / bracer / cloak / cloth-patch / staff detail panels + palette chips). Age 16, village farm boy, HERO role.
v2 (review-log 2026-09-28 "Wren v2 face feedback"): the FACE round -- bigger eyes, bigger brows, the lips re-paired (the
v1 mouth-compression expression rolled the upper lip in behind a protruding lower lip = the mismatch + the pinch), face
smoothing (a face-zone normal bake referenced to the flat facets at a raised face texel density: the face shades smooth
while every face stays flat-shaded per the contract). Everything below the neck is v1.
v3 (review-log 2026-09-29 "Wren v3 feedback": the Fire Emblem / anime round): GEOMETRIC eye enlargement (socket + eyeball
scaled together about the cornea, blended over the orbit), the anime eye treatment (a large iris with a lid-shadow band,
a bold tapered upper-lash band, a highlight dot, the pupil kept), and the hair rebuilt to the clump stack (fewer bolder
clumps in size tiers radiating from a crown whorl with S-curve rhythm, a dark inner cap, a SMOOTH-PROXY normal bake into
the hair UV strip -- the whole hairdo shades as one soft volume while every face stays flat-shaded -- and painted shading
tiers: roots / underside / angel ring / tips). Everything below the neck is v1.
v4 (review-log 2026-09-29 "Wren v4 feedback"): the x1.30 socket kept (approved "perfect"), the IRIS shrunk into it
(EYE_IRIS_DEG / EYE_PUPIL_DEG); the eye BAGS flattened away (EYE_BAG_FLAT: a per-column no-bag profile fit, + the baked
AO floored there, AO_FACE_LIFT); the orbit-blend LEAK cleaned (EYE_ORBIT quadrant ellipses, EYE_ORBIT_DEPTH, and past the
socket core the move SLIDES along the v2 surface: EYE_SLIDE); the 2D-anime MOUTH (LIP_FLAT relief to 15 %, no lip tint,
a drawn smirk line MOUTH_LEN / MOUTH_SMIRK on the kept seam); the HAIR re-flowed from a SIDE PART on his left (HAIR_PART,
FRINGE_PART_T / FRINGE_SWEEP, the sweep clumps over the crown) with LAYER SHADOWS (HAIR_CREVICE: every clump's outline
traced as a crevice band onto the hair beneath it), per-lock volume in the shading normal (HAIR_LOCK_NORMAL_MIX), a
consistent clump stack (CLUMP_STACK), a broken angel ring (ANGEL_RING_JITTER) and a clearance pass (HAIR_CLEAR).
Everything below the neck is v1.
v5 (review-log 2026-09-29 "Wren v5 feedback + FE reference set": the under-eye and mouth purge, per the FE style references
design/reference/fe-style/): EYE BAGS gone -- under each lower lid the skin becomes the front convex hull of the lash line
and the cheek (EYE_BAG_FILL: the margin roll, the bag bulge and the trough under it all land on one straight run of skin,
continued past the lid corners), the lid edge measured against the RENDERED lathe ball, the lower lids' steep rim faces left
out of the bake high (EYE_RIM_HIGH_OUT) and the under-eye AO floored to 1.0 (AO_FACE_FLOOR); LIPS fully 2D -- per column
the front convex hull of the nose base and the chin (LIP_PROFILE "bridge", LIP_BRIDGE: no lip volume, no lower-lip rim, no
lip-chin notch; the rims folded flat against it, LIP_HIDDEN_K, LIP_RIM_STEP 0.1 mm), the mouth zone's hidden layers left out
of the bake high and the folded rims harvested (MOUTH_HIDDEN), its AO floored to 1.0. The drawn smirk line + the seal kept;
the eyes, lashes, brows, hair and everything below the neck are v4.
v6 (review-log 2026-09-29 "Wren v6 mouth feedback" + "addendum": ONE mouth). Diagnosed on the v5.1 rig (wren_mouth_probe.py:
orthographic renders with the line repainted skin): TWO features -- the drawn line (29 mm) and the sealed seam's crease
(46 mm, running 0.5-1.4 mm under the line and curving down to 3 mm below its level past its ends: the MPFB mouth corners
5.3 mm down) -- plus the bridge zone's edge kinks as a lip-lens outline. v6: the MOUTH itself re-placed and re-sized by the
MPFB dials (MOUTH HEIGHT / MOUTH WIDTH / mouth corners level: the seam 39 mm = 0.64 x the eye spacing, at 0.29 of the way
from the nose bottom to the chin -- the Ashe portrait's ratios, REF_MOUTH), the smirk built into the seam (MOUTH_SMIRK_GEO),
the line painted over the WHOLE seam (MOUTH_LEN None, MOUTH_LINE_EXT; MOUTH_LINE_REFINE / _SNAP / _CENTROID: no gap at the
risen corner), and nose-to-chin smooth skin: the mouth zone's front skin moved onto its Gaussian-smoothed heightfield
(MOUTH_SMOOTH) and its normal texels written from that smooth proxy (MOUTH_PROXY). Everything else is v5.1.
v6.1 (review-log 2026-09-29 "Wren v6.1 mouth feedback" + addendum + "a bit larger"; target the FE archer figure): simple
lips ON the smoothed skin -- a soft lower-lip volume and a very small upper-lip plane (LIP_FORMS; the normal proxy smooths
without them and adds them back analytically, so they shade as volumes), a paler-than-skin lip tone (LIP + the palette's
'lips'), the upper rim's overhang below the seam tucked behind the lower sheet (LIP_RIM_TUCK: it read as lit slivers), the
mouth widened (MOUTH WIDTH 0.30: the line ~46 mm) and the line 1.3 mm. Everything else is v7.
v7 (review-log 2026-09-29 "Wren v7 hair feedback": "more segmented and cleared, ours look chopped up"; the FE figure refs).
Diagnosed on the v6 rig (improve/wren_hair_diag.py + lock-id renders): every clump cut through its neighbours (103 lock
pairs, 9397 triangle pairs, 3245 on the visible top sheets), 13.5 paint patches per clump (the crevice / ring cuts), 12
lens sections with spine turns p90 35 deg and silhouette-edge turns p90 67 deg. v7 = RIBBON locks (RIBBON_*): faired
spines, rotation-minimising frames, a bend limit, 13-19 stations crowded into the bends and the taper, a 6-vertex section
with sharp corners tapering to a point, whole-segment paint (no cuts); the LAYER RESOLVE (LAYER_*) puts the top sheets in
one layer order and lifts / tucks them apart; the layer shadow becomes the TUCK SHADE (RIBBON_TUCK); the mouth-interior
faces are no longer scalp (HAIR_INTERIOR_R; HAIRSTABLE gains a mouth-dial probe via TARGETS_EDIT). Everything else is v6.

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
    2026-10-03: HAS_FORK = False (the artist dropped the fork: no prop until a weapon is designed) -- the machinery stays;
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
    "eyes/l-eye-bag-decr": 1.00, "eyes/r-eye-bag-decr": 1.00,              # v2 0.60 -> v3 1.00: the 1.3x orbit scale enlarged the under-eye crease
    "head/head-oval": 0.45,                                                # smooth oval head
    "chin/chin-bones-decr": 0.35,                                          # soft jaw
    "chin/chin-width-decr": 0.25, "chin/chin-triangle": 0.30,              # narrow, gently pointed chin (anime V)
    "cheek/l-cheek-volume-decr": 0.30, "cheek/r-cheek-volume-decr": 0.30,  # lean cheeks (not the child's round face)
    "nose/nose-scale-horiz-decr": 0.40, "nose/nose-volume-decr": 0.45, "nose/nose-point-width-decr": 0.60,  # small nose
    "nose/nose-flaring-decr": 0.50, "nose/nose-nostrils-width-decr": 0.50, "nose/nose-scale-vert-decr": 0.20,  # (anime: no nostril read)
    "head/head-scale-vert-incr": 0.18, "cheek/l-cheek-bones-incr": 0.20, "cheek/r-cheek-bones-incr": 0.20,  # longer teen face
    "mouth/mouth-scale-horiz-decr": 0.30,                                  # v6.1 "MOUTH WIDTH" ("a bit larger"): the seam 44.5 mm,
                                      #   the line 46.5 mm = 0.36 x the face width at the line (the Ashe 3/4 portrait's 0.39 is the
                                      #   ceiling: kept under it, boyish); v6 0.80: the seam
                                      #   39 mm = 0.64 x the eye spacing, the FE Ashe portrait's mouth : eye-spacing ratio
                                      #   (measured 64.3 / 100.3 px); v1-v5 0.20 = a 46 mm seam under a 29 mm line (v6 feedback)
    "mouth/mouth-trans-up": 0.85,                                          # v6 "MOUTH HEIGHT": the mouth moved up 6 mm, to 0.29 of
                                      #   the way from the nose bottom to the chin (the Ashe portrait: 0.29, measured along
                                      #   the tilted face axis); v1-v5 0 = 0.36
    "eyebrows/eyebrows-angle-down": 0.25,                                  # determined brow set
    "neck/neck-scale-horiz-decr": 0.20,                                    # slender teen neck
    "mouth/mouth-lowerlip-volume-incr": 0.50, "mouth/mouth-upperlip-volume-decr": 0.40,   # v2 "lip pairing": matched upper / lower lip
    "mouth/mouth-angles-up": 0.50,                                         # v6 "mouth corners": the seam LEVEL to its ends (within
                                      #   +-0.7 mm; v1-v5 "mouth-angles-down" 0.15 = the corners 5.3 mm down: the drawn line
                                      #   stopped short of a seam that curved down past it = the second feature)
    "expression/units/caucasian/eye-left-slit": 0.20, "expression/units/caucasian/eye-right-slit": 0.20,   # the sheet's determined lids (v1 0.30)
    "legs/upperlegs-height-incr": 0.20, "legs/lowerlegs-height-incr": 0.15,  # leggy stylised proportions
}                                     # (v1's "expression/units/caucasian/mouth-compression": 1.0 is GONE: it rolled the upper lip in)
TARGETS_EDIT = None                   # v7 probe hook: {dial: value} applied over TARGETS (the HAIRSTABLE mouth-dial probe runs
                                      #   with a mouth edit here); None = TARGETS as listed (the build)
LIP_SEAL = (0.009, 0.0003, 0.0025)    # "lip seal" (v2): the relaxed lips closed geometrically: falloff over the lip height (m),
                                      #   overlap past the seam (m), gap = front rays this far behind the lip front (m); None = off
LIP_FLAT = (0.026, 0.0088, 0.011, 0.005, 0.0, 0.0012)   # "2D-anime lips": the lip zone (half width, height above /
                                      #   below the seam, border fade, m) keeps this fraction of its offset from the no-lip
                                      #   profile (0.15 = thin, barely-there lips + a shallow seam; 1.0 = the v2/v3 paired
                                      #   volumes); the profile = per column a fit through anchor bands this wide (m) just
                                      #   above / below the zone; None = off (v3). v4 (0.022, 0.009, 0.010, 0.003, 0.15,
                                      #   0.003); v5 "lips fully 2D": K = 0 (flat skin), the zone widened past the mouth
                                      #   corners (the crease past the line ends), 11 mm below (the lower-lip rim)
LIP_PROFILE = "bridge"                # v5 "no-lip profile": per column the front convex hull of two anchor bands just
                                      #   outside the LIP_BRIDGE zone (the nose base above, the chin below) = one straight run
                                      #   of skin from the nose base to the chin, tangent to the chin (no lip volume, no lower-
                                      #   lip rim, no lip-chin notch: measured on v4 the lower lip's rim dropped 3.7 mm over
                                      #   2 mm at 13-14 mm under the seam = the "lower-lip hint"); "line" = v4 (one line
                                      #   through LIP_FLAT's bands, K of the relief kept). (Tried and dropped: a C1 cubic
                                      #   Hermite between the bands -- the steep chin / nose-base slopes bowed it into a 2-3 mm
                                      #   muzzle mound, rendered.)
LIP_BRIDGE = (12.0, 26.0, 13.0, 26.0, 4.0, 5.0, 2.0, 4.0, 2.0)   # v5 "mouth zone": its top / bottom edge above / below
                                      #   the seam (mm: the nose-base crease / the chin's front) over |x| <= the 3rd mm,
                                      #   narrowing linearly to the 5th / 6th mm at the 4th mm (the mouth corners: only the
                                      #   seam crease there), anchor bands the 7th mm wide, faded out over the 8th mm past
                                      #   the corners and in over the 9th mm inside the top / bottom edges
LIP_HIDDEN_K = (0.0, 0.15, 0.002, 0.0001)   # v5: a mouth-zone vertex b behind the front skin lands K x b behind the
                                      #   bridge, but never closer than the 4th m: the 1st K within the 3rd m of the seam (the
                                      #   rims rolled into it: 0 = folded flat against the bridge, no steep fold faces at the
                                      #   seam past the drawn line), the 2nd elsewhere (the lips' inner faces: v4's K, the
                                      #   layers keep their order)
LIP_RIM_STEP = 0.0001                # after the flatten the upper lip's rim stays this far (m) in front of the lower's
                                      #   along the seam (0 = let them interleave into a sawtooth); v4 0.0004 = a visible
                                      #   ledge; v5 0.1 mm = just the draw order of the two sealed rims
LIP_RIM_TUCK = (0.0002, 0.0010, 0.85) # v6.1 "rim tuck": the upper sheet's vertices more than the 1st m below the seam land the
                                      #   2nd m behind the lower sheet, within the 3rd x the seam half-width (v4-v6 None: the
                                      #   upper rim hung 1.2-1.5 mm below the seam in front of the lower sheet = lit slivers +
                                      #   a shadow under them; 0.05 mm behind left the straddling faces crossing 0.5 mm down)
LIP_RIM_TUCK_SIDES = (1.0,)          # v6.1: which sheets tuck (+1 the upper below the seam, -1 the lower above it: tried,
                                      #   it opened dark notches into the mouth along the line, rendered)
LIP_FRONT_TOL = 0.003                 # v4: lip-zone vertices up to this far behind the front surface are flattened too (the
                                      #   rims tucked in the seam crease; the mouth interior sits >= 4.4 mm behind, measured)
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
EYE_SCALE = 1.30                      # v3 "eye size (geometric)": the socket + eyeball scaled together by this about each
                                      #   eye's cornea apex (the lids keep their fit on the ball; the dial was exhausted)
EYE_SCALE_ZONE = (18.0, 30.0, 1.0, 2.2)   # v3 orbit blend (used only when EYE_ORBIT = None): full scale within the 1st mm
                                      #   of the eye centre (face plane), none beyond the 2nd; in depth full to the 3rd x
                                      #   eyeball radius behind the centre, none beyond the 4th -- it LEAKED onto the nose
                                      #   bridge and the side of the face (v4 feedback)
EYE_ORBIT = {"outer": (19.0, 29.0), "up": (11.0, 21.0), "inner": (11.0, 18.5), "down": (11.5, 21.0)}   # v4 "orbit blend":
                                      #   per direction from the eye centre (face plane, mm, before the scale): full scale
                                      #   within the 1st value, none beyond the 2nd (quadrant ellipses between) -- the lids
                                      #   (the approved socket) sit inside the 1st everywhere; the nose bridge / temple outside
EYE_SLIDE = (1.0, 3.0)                # v4 "orbit slide": from the 1st mm inside the full-scale core to the 2nd mm outside
                                      #   it, the scale's move turns into a slide ALONG the original (v2) surface: the skin
                                      #   redistributes to make room, the orbit / nose / cheek / temple shape stays; None = off
EYE_ORBIT_DEPTH = (0.4, 1.1, 0.0, 0.6)   # v4: in depth (x eyeball radius behind the ball's centre) the whole scale is full
                                      #   to the 1st, none beyond the 2nd; its DEPTH component (the push back behind the
                                      #   cornea that sank the side of the face) full to the 3rd, none beyond the 4th
EYE_BAG_FILL = (0.25, 12.0, 24.0, 4.0, 2.5, 2)   # v5 "eye bags: GONE, flat skin" (review-log 2026-09-29 "Wren v5
                                      #   feedback + FE reference set": the FE face has NO under-eye geometry): per column
                                      #   under each lower lid the skin becomes the FRONT CONVEX HULL of the lash-line point
                                      #   (the lid edge + the 1st mm) and the cheek (from the 2nd to the 3rd mm under the lid
                                      #   edge) = one straight run of skin from the lash line, tangent to the cheek: the lid-
                                      #   margin roll (the thin lower-lid line), the bag bulge and the trough under it all
                                      #   land on it. Full over the lower lid, continued past each lid corner at the corner's
                                      #   height and faded out over the 4th mm (the orbit hollow under the outer corner is part
                                      #   of the bag: 2.4-2.9 mm deep there with the fade inside the lid run); vertices up
                                      #   to the 5th mm behind the front surface move with it (hidden lid / sleeve layers keep
                                      #   their order); 6th = passes. (Tried: leaving out only the 1.5 mm margin band -- the
                                      #   bag's forward bulge stayed ON the hull and set a raised shelf 1.3-2.8 mm in front of
                                      #   the lid edge, measured.) None = the v4 EYE_BAG_FLAT fit
EYE_BAG_FLAT = None                   # v5: replaced by EYE_BAG_FILL. v4 value (1.0, 12.0, 2.0, 55.0, 0.0, (0.0, 0.7, 14.5, 19.5)):
                                      #   "eye bags: gone": under each lower lid, from
                                      #   the 1st to the 2nd mm below the lid edge (fading out over the 3rd mm), within the
                                      #   4th deg either side of straight down, the surface keeps the 5th x its offset from
                                      #   the no-bag profile (0 = flat) -- per column a fit through two anchor bands (mm below
                                      #   the lid edge: the lid margin 6th[0..1], the cheek 6th[2..3]); None = off (v3). (The
                                      #   zone starts 1 mm under the lid edge: started at 2 mm, the untouched lower-lid bulge
                                      #   above it left a crease line under each eye, rendered)
EYE_IRIS_DEG, EYE_PUPIL_DEG = 27.0, 10.0  # "iris size" / pupil: cone half-angles on the eyeball (v3 40 / 15 = 83 % of the
                                      #   opening, "the eyeball is too big"; v4 27 / 10 fits the approved socket, FE-typical
                                      #   ~60 %; the pupil keeps v3's pupil : iris ratio)
EYE_SEG = 36                          # v3: 36 segments (the bigger iris read polygonal at 16; the dot needs the density)
EYE_BACK = 110.0                      # v3: the ball's hidden back closes as a cone to a pole this far round (deg); None = v2 sphere
IRIS_SHADE = 0.30                     # v3 "iris lid shadow": the iris above this fraction of its radius over its centre is
                                      #   the darker lid-shadow tone (anime iris: dark top, lit bottom); None = off
EYE_HILITE = (32.0, 0.34, 3.9)        # v3 "eye highlight dot": direction from the iris centre (deg up from the OUTER side),
                                      #   distance (x the iris cone angle), dot radius (deg on the eyeball); None = off
EYE_HILITE_RING = 2.0                 # extra eyeball rings this many deg apart through the dot's band (a round dot, not a triangle)
LINER_W = (0.0013, 0.0003)            # "eye liner": upper / lower lid line width (m) (v2; v3 upper = LASH_PROFILE)
LINER_WING = (0.0007, 20.0, 10.0)     # outer-corner flick: extra width (m), angular half-width, centre angle (deg)
LASH_PROFILE = ((0.0, 2.2), (30.0, 2.6), (90.0, 2.1), (150.0, 1.1), (180.0, 0.5))   # v3 "upper lash band": width (mm) round
                                      #   the upper lid, (deg from the outer corner, 90 = top, 180 = inner corner); None = v2
LASH_WING = (2.4, 11.0, 2.0)          # v3 "lash wing": extra width (mm), angular half width, centre (deg): the tapered flick
                                      #   past the outer corner
BROW_PTS = ((-1.15, 0.0058), (-0.40, 0.0074), (0.50, 0.0090), (1.28, 0.0080))   # "brows": (x in eye half-widths from the
                                      #   eye centre, height above the upper lid m) -- inner end low: the earnest set
BROW_W = (0.0086, 0.0040)             # "brow thickness": inner end / tail (m) -- v2 bigger (v1 6.4 / 2.8 mm)
BROW_TAPER = 1.2
LIP = (0.0190, 0.0035, 0.0065)        # lip tint: half width / upper height / lower depth (m) around the seam (v2/v3 (0.017,
                                      #   0.0026, 0.0026)); v4-v6 None = no tint; v6.1 "lips slightly less colored than the base":
                                      #   the zone over both LIP_FORMS takes the palette's 'lips' tone (paler, less saturated)
LIP_FORMS = {"lower": (0.0012, 0.0070, 0.80, 0.80, 0.40),   # v6.1 "simple lip dimension" (the archer figure): forward
             "upper": (0.0004, 0.0050, 0.70, 0.90, 0.40)}   #   bumps ON the smoothed mouth skin -- (amplitude m, height
                                      #   m from the seam, peak shape p: the profile sin^2(pi t^p) peaks at t = 0.5^(1/p) of the
                                      #   height, lateral reach x the seam half-width, the outer fraction of that reach faded
                                      #   out). LOWER = one soft simple volume under the line; UPPER = a very small plane
                                      #   above it (a third of the lower). Both start from zero WITH zero slope at the seam: the
                                      #   seal's overlap band moves by ~0, the line stays in the crease between; None = off (v6)
LIP_DZ = 0.0
MOUTH_LINE = (0.0013, 0.45)           # v2 "mouth line": painted width on the sealed seam (m) at the centre, x this at the corners
                                      #   (v6.1 1.3 mm: the archer figure's line is bolder, and it covers the sealed rims'
                                      #   +-0.6 mm zigzag; v2-v6 1.1)
                                      #   (v2-v5 0.30: the 0.33 mm ends fell between the mesh's vertices and the painted line
                                      #   stopped 1.7 mm short of each seam end; v6 0.45 = 0.5 mm ends)
MOUTH_LINE_EXT = 0.0010               # v6: with MOUTH_LEN None the line runs this far (m) past each seam end, over the corner
                                      #   folds (the seam's last millimetre was the "second feature" at the corners)
MOUTH_LINE_REFINE = {"passes": 3, "samples": 41}   # v6: the thin-stroke edge refinement of the line (v2-v5 the default 2 /
                                      #   11: an edge crossing the rising smirk corner missed the 0.8 mm stroke = a gap in
                                      #   the line, rendered)
MOUTH_LINE_CENTROID = 0.00015         # v6: a face is also painted when its centroid lies this far (m) inside the stroke by the
                                      #   exact field (the rim faces the interpolated field missed: a gap at the smirk
                                      #   corner); 0 = every straddling face too (spiky edges, rendered)
MOUTH_LINE_SNAP = 0.02                # v6: the line's iso-cut snaps a crossing onto a vertex only within this edge fraction
                                      #   (the default CUT_SNAP 0.12 stepped the rising smirk corner by up to 0.3 mm)
MOUTH_LEN = None                      # v4 "mouth line length": the line covers |x| <= this (m) of the seam; None = the whole
                                      #   seam. v4/v5 0.0165 (a 29 mm line on a 46 mm seam: "the line doesn't fill the area");
                                      #   v6 None: the seam is narrowed to the line's width instead (MOUTH WIDTH), ONE mouth
MOUTH_SMIRK = (0.0011, 1.0, 0.45)     # v4 "smirk": the corner on his left (+1; -1 = his right) rises this much (m) over the
                                      #   outer 3rd-value fraction of that half; None = level
MOUTH_SMIRK_GEO = 0.006               # v6: the smirk is built INTO the seam (the lips near it lifted by the smirk profile,
                                      #   full on the seam, fading to none this far (m) above / below it and 5 mm past its
                                      #   end), so the line sits exactly on the opening at the risen corner too (v4/v5: painted
                                      #   only, 1.1 mm above the seam there); None = painted only
MOUTH_SMOOTH = (4.0, 30.0, 12.0, 28.0, 6.0, 2.0, 2.0, 5.0)   # v6 "smooth skin, nose to chin" (GEOMETRY): the mouth
                                      #   zone's front skin moves onto its own front heightfield Gaussian-smoothed (sigma,
                                      #   mm) -- full within |x| <= the 2nd mm, the 3rd mm above / 4th mm below the seam,
                                      #   faded out over the 5th mm; samples above (nose bottom - the 6th mm) left out; hidden
                                      #   layers up to the 7th mm behind the front move with it, none past the 8th. The seam's
                                      #   5-deg rim strip, the bridge edge kinks and the corner folds become one smooth
                                      #   surface, and the line's cut slivers lie in their parent facets' planes; None = off
MOUTH_PROXY = (4.0, 30.0, 12.0, 28.0, 6.0, 2.0)   # v6 "smooth skin, nose to chin": the mouth zone's normal texels are
                                      #   WRITTEN from a SMOOTH PROXY (the hair's one-volume trick, face side): the bake
                                      #   high's front heightfield Gaussian-smoothed (sigma, mm; normalised over the valid
                                      #   samples), its normal encoded per texel in the flat facet's tangent frame -- full
                                      #   within |x| <= the 2nd mm, the 3rd mm above / 4th mm below the seam, blended out to the
                                      #   ray bake over the 5th mm; samples above (nose bottom - the 6th mm) left out (the
                                      #   nose's own underside never bleeds in). The seam's rim sheets, the bridge zone's edge
                                      #   kinks and the flat facets then shade as one smooth surface: nothing but the drawn
                                      #   line reads (v5: the seam baked as a jagged crease 3 mm below the line's ends); None
                                      #   = off (v5)
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
FRINGE_TIPS = ((-62.0, 13.0), (-43.0, 21.0), (-23.0, 15.0), (-3.0, -8.0), (17.0, 23.0), (37.0, 19.0), (57.0, 25.0))
                                      # v4 side part: the tips on the sweep side (his right, x < FRINGE_SPLIT_X) reach lower
                                      #   and further across; the two past the part (his left) are the short side. One
                                      #   narrow lock (FRINGE_NARROW) still falls between the eyes, as on the sheet's head
                                      #   panel (v3: a symmetric zigzag from the centre)
FRINGE_TIP_OFF = 0.006                # v4: fringe tips stand this far (m) + LOCK_OFF in front of the forehead (v3 4.5 mm: the
                                      #   swept outer locks swung into the brow in the walk -- hair gate)
FRINGE_NARROW = 3                     # the FRINGE_TIPS index of the narrow lock between the eyes
FRINGE_SPLIT_X = 28.0                 # v4: fringe tips left of this x (mm, his left = +) fall to the short side of the part
FRINGE_PART_T = (0.28, 0.20, 0.12, 0.05, 0.0, 0.0, 0.02)   # v4 "sweep": where on the part (0 = front end .. 1 = whorl)
                                      #   each fringe lock starts -- the further across the forehead, the further back
FRINGE_SWEEP = 9.0                    # v4: each fringe lock aims this far (deg) toward the part above its tip, so its last
                                      #   stretch crosses the forehead diagonally (the sweep) instead of hanging straight
FRINGE_NOTCH = 17.0                   # the edge between two tips rises this far (mm) above the higher tip
FRINGE_D = (0.0045, 0.0065)           # "fringe shadow depth": band under the fringe edge at the tips / at the notches (m)
FRINGE_UNDER = 0.006                  # the band also runs this far ABOVE the edge (skin glimpsed between the locks)
HAIR_INTERIOR_R = 0.70                # v7: a hairline face whose centroid lies under this fraction of the head ellipsoid (the
                                      #   mouth interior's back wall, measured 0.42-0.62; the scalp >= 0.75) is never hair;
                                      #   None = the v6 rule (42 mouth-interior faces in the cap: the hair digest followed the
                                      #   mouth dials)
HAIRLINE = (0.060, -0.050)            # scalp hairline above the eye centres (front) / at the nape relative to the head joint
HAIR_CAP_T = 0.0055                   # "hair volume" on the scalp
HAIR_ROOT_K = 0.30                    # every lock's thickness at its root (x its T): roots stacked on the crown made it tall (vampwarrior v4.1 lesson)
HAIRLINE_FEATHER = (0.022, 0.0022)    # the cap thins to the 2nd value over the first above the hairline (no hood rim)
HAIR_UV_STRIP = 0.14                  # hair faces packed into the right 14 % of the UV square (v3: proxy-baked normals, white AO)
LOCK_OFF = 0.0020                     # clumps ride this far off the cap (their control points; the path keeps 1.2 mm + half the clump thickness)
# ---- v3 hair: the CLUMP STACK (review-log 2026-09-29). Every clump radiates from the crown whorl: its root sits on the
# great circle whorl -> tip, CLUMP_ROOT[kind] of the way out (roots never stack on one point: the low-crown lesson), and
# its path follows that great circle, lifted off the cap, with an S-curve sway. Tiers: L = the big mass clumps that make
# the silhouette, M = overlapping fillers, S = accent tips / flyaways.
HAIR_WHORL = (150.0, 60.0)            # "crown whorl": (psi deg from the front toward his left, elevation deg about the head
                                      #   centre) -- v4: at the back end of the side part (v3 176 / 60, centre flow)
HAIR_PART = ((30.0, 44.0), (52.0, 62.0), (110.0, 68.0))   # v4 "side part": the part line (psi, el deg) from the front
                                      #   hairline back to the whorl, on his LEFT (the sheet's fringe sweeps toward his
                                      #   right, image-left on the head panel); None = the v3 centre flow
PART_ROOT_K = 0.60                    # v4: the root width (x width) of the clumps that start ON the part (sweep / side /
                                      #   outer): the tiers' 0.30 -- made for roots converging on one whorl -- left the crown
                                      #   either side of the part as dark cap between narrow roots
CLUMP_STACK = 0.0004                  # v4 "layer stack": each later clump of a kind rides this much (m) further off the cap,
                                      #   so every overlap has a clear upper and lower layer (the layer shadows need it)
HAIR_CREVICE = (5.0, 2.0, 0.5, 14.0, 50.0, 0.5)   # v4 "layer shadows": the band each clump's outline traces onto the
                                      #   hair beneath it: width at the root / at the tip (mm, tapering), the outline may sit
                                      #   this far BELOW the receiving point (mm: the wrap tucks clump edges down) and at most
                                      #   this far above (mm); receivers only below this elevation (deg: the lit crown keeps
                                      #   the angel ring clean) and not above the edge by more than this (mm: the shadow falls
                                      #   downhill); None = off (v3: the dome with lines)
HAIR_CREVICE_SNAP = 0.25              # the band cut snaps a crossing within this edge fraction onto the nearer vertex (no
                                      #   sliver splits: fewer tris for the same band shape)
ANGEL_RING_JITTER = 2.0               # v4: every clump's angel-ring segment shifts by up to this (deg, a fixed per-clump
                                      #   hash): broken per-lock highlight strokes, not one stripe round a dome
CLUMP_TIER = {"L": (0.080, 0.0085, 0.30), "M": (0.058, 0.0072, 0.30), "S": (0.026, 0.0050, 0.70)}   # "clump size tiers":
                                      #   full width (m), half-thickness (m), root width (x width) (v2's locks: 72-84 mm wide);
                                      #   narrow roots at the whorl leave dark wedges between the clumps (the whorl's radiating read)
CLUMP_ROOT_GROW = 0.40                # a clump reaches full width this far along it (arc fraction)
CLUMP_W_VARY = 0.14                   # "varied widths": every clump's width x (1 +- this), a fixed per-clump hash
CLUMP_WRAP = 1.0                      # "clump wrap": the cross-section bends with the head's curvature across its width
                                      #   (x the local head radius parabola; 0 = flat plates standing off the skull)
CLUMP_TOP_THIN = (55.0, 80.0, 0.30)   # "low crown": a clump thins by up to the 3rd value where it crosses the top of the head
                                      #   (elevation between the 1st and 2nd deg) -- the bold read is the width; stacked
                                      #   thick roots made the crown tall (vampwarrior v4.1) -- v3 0.45; v4 0.30: thicker
                                      #   over the top = deeper visible gaps between the top clumps
CLUMP_ROOT = {"fringe": 0.04, "sweep": 0.03, "side": 0.04, "outer": 0.05, "back": 0.16, "crown": 0.05}   # root position
                                      #   along origin -> tip (the origin: the whorl, or v4 the part point; v3 fringe 0.14 /
                                      #   side 0.20 / outer 0.22 -- measured at 0.08-0.16 the roots left a 15-25 mm dark swath
                                      #   along the part: v4 roots start just off it, the part reads as a narrow dark line;
                                      #   roots along a LINE never stack, the low-crown lesson was about one whorl point)
CLUMP_S = (0.0, 0.16, 0.24, 0.33, 0.42, 0.51, 0.60, 0.69, 0.77, 0.84, 0.92, 1.0)
                                      # cross-sections along a clump (arc fractions); the root / tip paint tiers cut on these
                                      #   (v4 12, v3 15: the dropped sections pay for the layer-shadow cuts, tris <= 50k)
CLUMP_SWAY = 0.14                     # "S-curve rhythm": sideways sway amplitude (x the clump width), one S per clump,
                                      #   alternating hand clump to clump, faded in toward where the clump leaves the scalp
CLUMP_SCALP_T = 0.62                  # "petal thickness": on the scalp a clump is this x its thickness (thin layered petals
                                      #   from the whorl); it thickens to full where it leaves the scalp
CLUMP_LAYER = {"back": 0.0, "outer": 0.0009, "side": 0.0028, "sweep": 0.0018, "crown": 0.0027, "fringe": 0.0027}   # "clump layering": extra
                                      #   offset off the cap (m) per kind, so the petals stack (back under sides under fringe)
CLUMP_LIFT = {"fringe": 0.005, "side": 0.016, "outer": 0.012, "sweep": 0.012, "back": 0.007, "crown": 0.006}   # "hair volume": lift off
                                      #   the cap (m) down the sides; none over the top (elevation > 70 deg), full below 35 deg
# the clumps: (kind, tier, tip, chain). Tips: fringe = FRINGE_TIPS index (front projection); side = (psi deg, z mm above
# the eye centres, outward flick m); outer / back / crown = (psi deg, tip elevation deg about the head centre, flick m).
# Side / outer clumps are mirrored L / R (psi > 0 = his left; R = -psi).
HAIR_CLUMPS = (                       # (v4: listed bottom -> top within a kind: CLUMP_STACK; the fringe from the lock that
                                      #   starts furthest back on the part up to the between-eyes lock on top)
    ("fringe", "M", 0, "hair_fringe.R"), ("fringe", "L", 1, "hair_fringe.R"), ("fringe", "L", 2, "hair_fringe.R"),
    ("fringe", "M", 6, "hair_fringe.L"), ("fringe", "M", 5, "hair_fringe.L"), ("fringe", "L", 4, "hair_fringe.C"),
    ("fringe", "S", 3, "hair_fringe.C"),      # (v4: three fringe chains -- the sweep's far locks, the centre, the short
                                              #   side -- each lagging along its own mean path; one chain over the whole
                                              #   fanned sweep swung its outer locks into the brow: hair gate)
    ("sweep", "M", (-138.0, 26.0, 0.032), None), ("sweep", "L", (-92.0, 28.0, 0.036), None),
    ("sweep", "L", (-48.0, 30.0, 0.030), None), ("sweep", "L", (-12.0, 50.0, 0.010), None),   # v4: the combed-over
                                      #   top on his right (from the part, not mirrored; bottom -> top): covers the crown
                                      #   right of the part, the front one lies flat over the top toward the fringe
    ("side", "M", (70.0, -4.0, 0.014), "hair_side"),
    ("side", "L", (88.0, -14.0, 0.022), "hair_side"), ("side", "M", (108.0, -30.0, 0.048), "hair_side"),
    ("outer", "S", (74.0, 22.0, 0.034), "hair_side"), ("outer", "L", (120.0, -6.0, 0.046), None),
    ("outer", "M", (148.0, -2.0, 0.048), None),
    ("back", "L", (180.0, -60.0, 0.022), None),                                      # the nape under-layer
    ("back", "M", (164.0, -50.0, 0.046), None), ("back", "M", (196.0, -50.0, 0.046), None),
    ("back", "L", (146.0, -34.0, 0.056), None), ("back", "L", (214.0, -34.0, 0.056), None),
    ("back", "M", (180.0, -42.0, 0.050), None),                                      # the flared points over it
    ("crown", "S", (130.0, 52.0, 0.012), None), ("crown", "S", (232.0, 50.0, 0.012), None))
CLUMP_S_TIP_K = {"L": 1.0, "M": 1.0, "S": 0.9}
# ---- v7 RIBBON LOCKS (review-log 2026-09-29 "Wren v7 hair feedback": "more segmented and cleared, ours look chopped up";
# the figure refs design/reference/fe-style/fe-archer-figure-hair.webp + fe-byleth-figure-hair.png are the bar). Every
# lock = ONE smooth ribbon: a faired spine (Catmull -> arc-length resample -> Taubin fairing -> smooth clearance push),
# rotation-minimising frames, a constant-topology section tapering to a sharp tip, painted by whole segments (no cuts),
# and the LAYER RESOLVE: the locks' top sheets put in one layer order and lifted / tucked until they stop cutting through
# each other.
RIBBON = True                         # False = the v6 lens clumps (CLUMP_S sections, ring + crevice cuts)
RIBBON_STATIONS = {"L": 16, "M": 15, "S": 12}   # "lock segments": cross-sections per lock (root .. the last one before
                                      #   the tip point); v6 12 for every tier (spine turn p90 35 deg, edge turn p90 67 deg)
RIBBON_DENSE = 64                     # the spine is faired as this many arc-length points
RIBBON_FAIR = (10, 0.50, -0.53)       # Taubin fairing (iterations, lambda, mu): smooths without shrinking the S-curve
RIBBON_PUSH_SMOOTH = 3.0              # the spine's clearance push is dilated + Gaussian-smoothed over this many spine points
RIBBON_ROOT_RAMP = 0.12               # the spine's clearance margin ramps in over this arc fraction from the root
RIBBON_BEND = (0.45, 60)              # "no folded edges": the spine's sideways curvature x the ribbon's half width stays under
                                      #   the 1st value (1 = the inner edge folds back on itself), by local fairing (at most the
                                      #   2nd passes)
RIBBON_TAIL_TUCK = 0.40               # the nape tail's clearance ramps in over this arc fraction (its root is tucked into the
                                      #   nape by design)
RIBBON_CURV_K = 0.35                  # station spacing: this share follows the spine's turning (denser through the S
                                      #   bends), the rest plain arc length
RIBBON_TIP_DENSE = 1.35               # stations crowd toward the tip (1 = even): the taper is where the silhouette turns
RIBBON_TOP = (0.55, 0.80)             # "lock section": the top shoulder vertices at +-this x the half width, this x the
                                      #   thickness (the centre ridge = 1, the corners = 0: the sharp defined edges)
RIBBON_UNDER = 0.45                   # the underside: one centre vertex this x the thickness below (a shallow V)
RIBBON_TAPER = (1.15, 0.03)           # "tip taper": exponent on the free-part fraction, width left at the last section
                                      #   (x the width) before the tip point
RIBBON_BELLY = 0.05                   # the free part swells this much just past leaving the scalp (v6 CLUMP_BELLY 0.12:
                                      #   the lens bellies read as blobs)
RIBBON_FRAME_SMOOTH = 3.0             # the section frames (outward normal) Gaussian-smoothed over this many spine points
RIBBON_RADIAL = 0.35                  # the outward normal leans this share toward the head-centre radial (stable frames
                                      #   where the nearest surface jumps: cap -> ear -> cloak)
RIBBON_TIER = {"L": (0.058, 0.0036, 0.40), "M": (0.044, 0.0032, 0.40), "S": (0.024, 0.0026, 0.70)}   # v7 "lock size
                                      #   tiers": full width (m), half-thickness (m), root width (x width) -- narrower and
                                      #   thinner than v6 CLUMP_TIER (80 / 58 / 26 mm, 8.5 / 7.2 / 5.0 mm): the fringe's 7
                                      #   locks spanned ~3x the forehead and cut through each other (9397 triangle pairs)
RIBBON_KIND_W = {"fringe": 0.70, "sweep": 1.0, "side": 1.15, "outer": 1.10, "back": 1.05, "crown": 1.0}   # "lock width by
                                      #   kind" (x the tier width): the fringe's 7 tips sit ~20 mm apart (FRINGE_TIPS), so its
                                      #   locks overlap their neighbours partly (feathers), not 3-deep; the sides / back cover
                                      #   the head (the dark cap between them reads as the grooves)
RIBBON_NARROW_W = 0.024               # the narrow lock between the eyes (FRINGE_NARROW) full width (v6 0.032)
LAYER_GAP = 0.0006                    # "layer stack" (v7 resolve): a lock's top sheet rides at least this (m) over the top
                                      #   sheet of every lock layered beneath it (> the 0.4 mm hair-bake cage)
LAYER_ROOT = 0.10                     # a lock's root (below this arc fraction) is left out of the resolve (roots cross at
                                      #   the part line / whorl by nature)
LAYER_NEAR = 0.005                    # only top sheets within this (m) of a vertex along its normal count (crossings / near
                                      #   contacts; a lock passing well above or below is no conflict)
LAYER_WINDOW = 0.030                  # a lock vertex is tested along its section's outward normal within this (m)
LAYER_LIFT_MAX = 0.009                # a section's total resolve lift / drop is clipped to this (m): the lock keeps its
                                      #   authored place (a stuck conflict stays, counted in "after")
LAYER_SMOOTH = 1.3                    # the lift profile is Gaussian-smoothed over this many stations (a gentle ramp, no kink)
LAYER_ITERS = 4                       # resolve rounds over every lock (test, move, rebuild), bottom layer first
RIBBON_RING_JITTER = 0.6              # v7: every ribbon's angel-ring stroke shifts by up to this (deg; v4 ANGEL_RING_JITTER 2.0:
                                      #   on whole-segment strokes the 4 deg spread read as zebra stripes over the crown)
RIBBON_TUCK = (0.6, 3, 0.012)         # v7 "layer shadows" (TUCK SHADE, replaces the v4 HAIR_CREVICE cut bands on ribbons): a
                                      #   ribbon segment takes the crevice tone on its whole top when at least the 1st share of
                                      #   its sample points (the 2nd per top facet) lie under a lock layered above it, within
                                      #   the 3rd (m) along the section normal; None = off
CLUMP_BELLY = 0.12                    # "clump belly": the free part swells this much just past where it leaves the scalp
TAIL = (0.070, 0.010, 0.0045)         # "nape tail": length (m), tie radius (m), layer (m: it rides over the back clumps)
AHOGE_H = 0.045                       # "cowlick" height above the crown
HAIR_CAP_INNER = False                # v3 "dark inner cap": keep the cap's inner (scalp-facing) shell; False = harvest it
                                      #   (provably hidden: it faces into the closed head)
HAIR_TIERS = (0.16, 0.90)             # "painted hair tiers": clump arc fraction below which the top faces are the dark ROOT
                                      #   tone / above which the lighter TIP tone
ANGEL_RING = (43.0, 50.0)             # "angel ring": the highlight band on the clumps' top faces between these elevations
                                      #   (deg, about the head centre on the head's own ellipsoid): one consistent height
HAIR_PROXY = (48, 24, 80, 0.003)      # "one-volume hair shading": the smooth proxy = a (lon x lat) sphere grid about the
                                      #   hairdo's centre, the smoothed upper envelope of the hair (iterations), padded (m);
                                      #   its normals are baked into the hair UV strip against the flat facets; None = flat (v2)
HAIR_LOCK_NORMAL_MIX = 0.45           # v4 "per-lock volume": the hair shading normal = the proxy's (one soft volume) leaned
                                      #   this far toward each clump's own smooth normal (0 = v3's pure proxy: "a dome with
                                      #   lines"; 1 = every lock a separate tube)
HAIR_CLEAR = (0.0025, 0.0005)         # v4 clearance pass: clump vertices pushed out to this far off the head / neck skin /
                                      #   off the cap surface (m)
HAIR_BAKE_CAGE = (0.0004, 0.0012)     # the hair normal bake: cage extrusion / max ray distance (m) (the high is the hair's
                                      #   own surface carrying the proxy normals) -- used only by HAIR_NORMAL_CARRIER "map"
HAIR_NORMAL_CARRIER = "vertex"        # "hair normal carrier" (research H5, 2026-10-03): how the proxy-leaned hair normals
                                      #   reach the renderer. "vertex" = CUSTOM SPLIT NORMALS on the hair faces only (smooth,
                                      #   exported as glTF NORMAL; the hair UV strip of the normal map stays flat); "map" = the
                                      #   v3-v6.1 tangent-space bake onto flat facets (measured: 22.6 % of samples
                                      #   unrepresentable, decode p90 40.8 deg -- the checkered facets)
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
CLOAK_FOREARM = {"l": (1.0, 0.2, 0.5), "r": (1.0, 0.35, 0.75)}   # (s7 weight-source fix 2026-10-03) per side (his
                                      #   left = the free arm, right = the fork arm): where the cloak's nearest skin is the
                                      #   FOREARM / HAND its arm coupling (CLOAK_ARM_FOLLOW) fades down the forearm to the 1st
                                      #   value's share at the wrist -- full up to the 2nd value of the elbow -> wrist span,
                                      #   the wrist share from the 3rd on (clavicle / upper arm keep the full share). The posed
                                      #   hand comes in onto the hip: a cloak glued to it is dragged through the sash / pouch;
                                      #   with no forearm coupling the elbow passes through it. (1.0, *, *) = the v6.1 weights
CLOAK_HIP = {"l": (0.0, 0.10, 0.05, 55.0, 125.0, 15.0), "r": (0.0, 0.10, 0.05, 55.0, 125.0, 15.0)}   # (s7 2026-10-03)
                                      #   per side: the cloak's side panels at the sash line ride the hip -- this share of
                                      #   their chain-hung weights moves to the trunk's skin weights (no leg share), full
                                      #   within the 2nd value (m) of the sash line, none past + the 3rd; side azimuths (deg
                                      #   from the back centre) 4th .. 5th, fading over the 6th. 0.0 = the v6.1 weights
# ---- s7 weight sources (2026-10-03 fix: the sash's nearest-skin copy grabbed the hand resting at the hip -- 12 verts per
# wrap at ~1.0 on lowerarm_l / _r, 24 torso crossings in the run; the pouch rode thumb_02_l at 0.93)
TORSO_HUNG = ()                       # garment parts hanging from the torso ("sash", "knot", "pouch", ...): their skin
                                      #   weight copy sees the trunk faces only (no arm-chain face is a source) and any
                                      #   arm-chain share left on a trunk vertex is dropped (renormalised)
# STATUS 2026-10-03: all three knobs ship OFF (= the v6.1 weights, weights digest bd94f0edfe3d2c38 reproduced). Measured
#   with them ON (TORSO_HUNG = the five parts above, CLOAK_FOREARM l (0.0, 0.2, 0.5) / r (0.0, 0.35, 0.75), CLOAK_HIP 0.5
#   both sides): every cloak crossing 0 in idle / walk / run -- but s3 BUILT the sash and the pouch around the REST-pose
#   hands (BVH_SASH's radial profile sees the A-pose hands: one ring per wrap at 344-380 mm vs ~140-180 mm, the pouch at
#   x 0.32-0.40 = on the hand), so trunk weights leave a rope spike and a floating pouch out at the rest hands. Needs the
#   s3 geometry fix first (outside the s7 lane); retune CLOAK_FOREARM / CLOAK_HIP after it.
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
# ---- hand-held prop (house props law: own mesh / node, own bone under hand_r, a recorded grip)
HAS_FORK = False                      # "carry the pitchfork" (review-log 2026-10-03 "can we drop the pitchfork from wren":
                                      #   the hero carries NOTHING until a weapon is designed). False = no fork mesh, no
                                      #   'pitchfork' bone / node / grip transform, no fork gates; the right ARM keeps the
                                      #   approved carry path (the FORK / FORK_* / GRIP numbers below still define where the
                                      #   empty hand goes, so the silhouette and timing stay as approved) and the right
                                      #   FINGERS take the left hand's pose per clip. True = the v6.1 build exactly (the
                                      #   machinery a future weapon re-uses).
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
NEAREST_TIE = 1e-6                    # v5.1 hair-face decoupling (s5 nearest()): faces within this distance (m) of the
                                      #   nearest one form the tie set resolved canonically (not by BVH traversal order):
                                      #   the BVH's float32 coordinates at head height (z ~ 1.7 m) carry a 1.2e-7 m ULP, so
                                      #   1 um (~8 ULP) covers two faces rounding one shared edge point; 3 orders under the
                                      #   1.2 mm clump path margin, so no genuinely farther face ever joins
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
V2_FACE = {                           # v2 baseline (commit f2b0703), from its committed report improved/wren.json + the iris
                                      #   coverage MEASURED by this build in the v2 eye configuration (--set EYE_SCALE=1.0
                                      #   EYE_SEG=16 IRIS_SHADE=None EYE_HILITE=None LASH_PROFILE=None EYE_BACK=None + the v2
                                      #   eye-bag dial 0.60; that run reproduces v2's aperture 15.6 / 3.8 / 9.7 / 7.6 mm and
                                      #   upper liner 1.2 mm exactly)
    "aperture_mm_L": {"outer": 15.6, "up": 3.8, "inner": 9.7, "down": 7.6}, "open_w_mm": 25.3, "open_h_mm": 11.4,
    "eyeball_r_mm": 16.92, "iris_deg": 40.0, "pupil_deg": 15.0, "iris_coverage_pct": 82.4, "visible_opening_mm2": 243.7,
    "eyeball_poke_verts": 0, "liner_upper_mm": 1.2,
    "hair": {"locks": 37, "tris_locks": 5328, "tris_cap": 3196, "tris_tie": 80, "tris_total": 8604,
             "crown_above_scalp_mm": 14.4, "normal_strip": "flat (0.5, 0.5, 1) everywhere"},
    "total_tris": 48137}
V3_FACE = {                           # v3 baseline (commit 63078cf): its committed report improved/wren.json + the numbers
                                      #   MEASURED by this build in the v3 configuration (--set EYE_ORBIT=None
                                      #   EYE_BAG_FLAT=None LIP_FLAT=None, EYE_IRIS_DEG / EYE_PUPIL_DEG 40 / 15): the v4 report
                                      #   quotes it next to the live v4 numbers
    "iris_deg": 40.0, "pupil_deg": 15.0, "iris_coverage_pct": {"L": 83.2, "R": 83.3}, "visible_opening_mm2": {"L": 411.8, "R": 411.4},
    "aperture_mm_L": {"outer": 20.3, "up": 4.9, "inner": 12.6, "down": 9.9},
    "shape_dev_mm_vs_v2_surface": {"nose": {"max": 4.17, "p95": 3.606}, "nose_bridge": {"max": 2.857, "p95": 2.817},
                                   "side": {"max": 1.932, "p95": 0.622}, "temple": {"max": 0.985, "p95": 0.006},
                                   "cheek": {"max": 0.874, "p95": 0.465}, "forehead": {"max": 0.074, "p95": 0.0},
                                   "outside_socket_core": {"max": 4.054, "p95": 1.432}},
    "undereye_crease_mm_L": {"dx-8": 1.678, "dx-4": 1.481, "dx+0": 1.338, "dx+4": 1.865, "dx+8": 2.853, "max": 2.853},
    "lip_pairing_x0": {"upper_fwd_mm": 3.25, "lower_fwd_mm": 0.96, "upper_proud_h_mm": 7.6, "lower_proud_h_mm": 5.8},
    "lip_tint_mm": [17.0, 2.6, 2.6], "mouth_line": "1.1 mm over the whole 46 mm seam, level",
    "hair": {"clumps": 29, "flow": "centre: every clump radiating from the whorl (176, 60)", "crown_above_scalp_mm": 17.1,
             "layer_shadows": "none", "tris_locks": 5776},
    "total_tris": 48766}
V4_FACE = {                           # v4 baseline (commit 39f29a6): its committed report improved/wren.json + the delivered
                                      #   v4 mesh MEASURED by improve/wren_face_probe.py (renders/wren/wren_v4_face_probe.json):
                                      #   the v5 report quotes it next to the live v5 numbers
    "undereye": {"build_crease_mm_L_max": 0.606, "probe_crease_mm_5col_max": {"L": 0.683, "R": 0.684},
                 "probe_crease_mm_dense_max": {"L": 2.799, "R": 2.865}, "probe_flatness_mm_max": {"L": 1.239, "R": 1.269},
                 "probe_lid_line_mm_max": {"L": 1.038, "R": 1.067}, "ao_floor": "0.93, 0.6-14 mm under the lid, +-55 deg"},
    "mouth": {"lip_pairing_x0": {"upper_fwd_mm": 1.83, "lower_fwd_mm": -0.01, "seam_z_offset_mm": -1.4},
              "relief_vs_fit_mm_after": 0.465, "K": 0.15, "rim_step_mm": 0.4,
              "probe_relief_mm_max": 0.912, "probe_past_line_ends_mm": 0.912, "probe_lower_lip_band_mm": 0.891,
              "probe_upper_proud_detrended_mm": 0.718, "probe_seam_recess_mm": 0.612, "ao_floor": "0.93 within 2.5 mm of the seam"},
    "iris_coverage_pct": {"L": 58.0, "R": 58.1}, "total_tris": 48428}
V51_MOUTH = {                         # v5.1 baseline (commit f69ec03), MEASURED by improve/wren_mouth_probe.py on the committed
                                      #   v5.1 rig (renders/wren/wren_v5_mprobe.json, rendered once before the v6 build
                                      #   replaced it) + the v5.1 seam off its report: the v6 report quotes it next to v6
    "line": {"x_mm": [-14.52, 14.78], "width_mm": 29.3, "mm_below_eyes": 75.24},
    "seam": {"width_mm": 46.0, "corner_drop_mm": 5.3},
    "second_feature_seam_trace_noline": {"columns": 44, "x_mm": [-23.0, 23.0], "dz_mm_below_line_max": 3.0,
                                          "contrast_max": 0.126, "under_the_line_dz_mm": [-1.4, -0.5]},
    "placement": {"nose_bottom_mm_below_eyes": 49.9, "chin_mm_below_eyes": 119.9, "v_ratio": 0.362,
                  "w_eyes": 0.482, "w_face": 0.237, "face_w_at_line_mm": 123.6}}
REF_MOUTH = {                         # v6: the reference proportions MEASURED off the pictures (pixel picks on 3-6x zooms,
                                      #   design/reference/): Ashe (fe-ashe-portrait.png, 512 px, three-quarter, head tilted
                                      #   16.8 deg -- distances along the tilted face axis / the eye line): eyes (240, 246) /
                                      #   (336, 275), nose-mark bottom (298.3, 336.3), mouth ends (241.3, 348.7) / (304.7,
                                      #   359.5), chin point (269.2, 415), face outline along the eye line through the mouth
                                      #   (166.7, 321.7) / (323.3, 370.3). Alicia (alicia_face_front.png, 1024 px, front):
                                      #   eyes (410, 535) / (615, 535), nose dot (512.5, 602.5), mouth 484 .. 540 at 677.5,
                                      #   face 292 px wide at the mouth
    "ashe": {"v_ratio": 0.29, "w_eyes": 0.64, "w_face_projected": 0.39, "mouth_px": 64.3, "eye_spacing_px": 100.3,
             "face_px_at_mouth": 164.0, "nose_to_mouth_px": 24.3, "nose_to_chin_px": 83.7},
    "alicia": {"w_eyes": 0.27, "w_face": 0.19, "mouth_px": 56.0, "eye_spacing_px": 205.0, "face_px_at_mouth": 292.0,
               "note": "chibi proportions (tiny lower face): the Ashe portrait is the binding target (spec)"}}
MOUTH_HIDDEN = (0.034, 0.014, 0.030, 0.00005, 0.06, 0.003)   # v5 "flat mouth bakes flat": in the mouth zone (|x| <
                                      #   the 1st m, the 2nd m above / the 3rd m below the seam, up to the 5th m behind the
                                      #   lips' front) every face facing backward or lying more than the 4th m behind the front
                                      #   skin is left out of the normal-bake high; of those, the ones up to the 6th m behind
                                      #   (the rims folded against the bridge) are harvested from the low too. None = off (v4)
EYE_RIM_HIGH_OUT = (0.5, 2.0, 100.0, 50.0)   # v5 "no lower-lid line": the normal-bake high leaves out the lower lids'
                                      #   rim faces -- from the 1st mm inside to the 2nd mm outside the lid edge (front
                                      #   projection), within the 3rd deg of straight down, tilted more than the 4th deg from
                                      #   the view axis (the lid edge's drop to the flat skin); None = kept (v4)
AO_SAMPLES = 48                   # AO bake samples (v1 16: the v2 face texel density resolved the 16-sample noise as speckle in the lip crease)
AO_FLOOR = {"default": 0.42, "skin": 0.62, "hair": 0.82, "cloth": 0.50}
AO_FACE_LIFT = (0.93, 0.0025, 0.6, 14.0)   # v4: the baked AO is floored at the 1st value on the skin in the MOUTH band
                                      #   (within the 2nd m of the seam: the crease baked in as a jagged dark streak past the
                                      #   short drawn line) and UNDER THE EYES (the 3rd .. 4th mm below the lid edge, the
                                      #   EYE_BAG_FLAT angle: no baked bag shadow); None = off. (Used only when AO_FACE_FLOOR
                                      #   is None.)
AO_FACE_FLOOR = (1.0, 3.0, (0.0, 26.0, 100.0), (0.031, 0.013, 0.028))   # v5 "AO fully floored": the skin's baked AO
                                      #   is lifted to the 1st value (1.0 = no occlusion tone at all) under each eye (from the
                                      #   lid edge to the 3rd[1] mm below it, within 3rd[2] deg either side of straight down:
                                      #   the whole lower lid to both corners) and over the whole mouth zone (|x| <= 4th[0],
                                      #   4th[1] above / 4th[2] below the seam, m), feathered to the skin floor over the 2nd
                                      #   mm at the border (no hard-edged light patch); None = the v4 AO_FACE_LIFT
AO_FLOOR_REGIONS = {"skin": ["skin", "skin_shadow", "lips", "brow"],
                    "hair": ["hair", "hair_shade", "hair_tie", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice"],
                    "cloth": ["cloak", "cloak_worn", "patch_a", "patch_b", "patch_c", "shirt", "vest", "trousers"]}
HAIR_BONES, CAPE_BONES, CAPE_CHAINS = 3, 4, 5
GRIP_CURL = (58.0, 72.0, 50.0)        # "grip": finger curl round the shaft, per joint (deg)
GRIP_THUMB = (22.0, 30.0)
RELAX_CURL = (12.0, 18.0, 12.0)       # (HAS_FORK False: both hands take the clip's left-hand curl -- RELAX_CURL + thumb
                                      #   (4, 8) in idle / walk, RUN_HAND in the run)
RUN_HAND = ((34.0, 52.0, 38.0), (18.0, 24.0))   # the run's free hand: a loose fist (curl per joint, thumb; deg)
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
# ---- run (review-log 2026-10-03 "young hero sprint": an eager, youthful FULL sprint, not a soldier's measured stride)
RUN_N = 12                            # "run cycle": 12 frames per stride (2 steps) = 0.5 s at 24 fps = 240 steps/min.
                                      #   Derivation: the game's run pace (StoryRuleset.run_step_seconds 0.12 s/cell) x 24 fps
                                      #   = 2.88 frames/cell; one step = 6 frames = 0.25 s = 2.08 cells (4 % under 2 cells),
                                      #   the stride = 4.2 cells; an EVEN integer cycle keeps L / R exact half-cycle mirrors on
                                      #   keyed frames and the seam exact. 240/min reads as a full sprint (recreational sprint
                                      #   180-210, elite top speed ~260-280); 14 (206/min) read as a fast jog, 10 as a scramble
RUN_STANCE = 0.30                     # "ground time": fraction of the cycle each foot is down (duty < 0.5 = a flight phase):
                                      #   1.8 of 6 frames per step airborne -> frames 4-5 and 10-11 have both feet off the floor
RUN_STEP = (0.24, 0.46)               # "stride reach": the ball joint lands this far AHEAD of / leaves this far BEHIND its rest
                                      #   spot (m); stance travel 0.70 m in 0.15 s = the ground speed (4.67 m/s)
RUN_ROLL = (8.0, 3.0, 50.0)           # stance heel lift (deg): forefoot touchdown / mid-stance / toe-off push
RUN_SWING = ((0.30, 0.30, 0.36, 100.0), (0.60, -0.20, 0.42, -6.0), (0.82, -0.36, 0.13, -10.0))
                                      # swing keys (q = swing fraction, ball joint dy from rest (m, < 0 = ahead), ball height
                                      #   above its rest (m), heel lift (deg)): heel recovery (folded under the seat), KNEE
                                      #   DRIVE (thigh ~horizontal, toes cocked), reach; it lands pawing back at ground speed
RUN_FOOT_X = 0.62                     # feet land this x their rest half-width (a runner's narrow track)
RUN_REACH = 0.985                     # stance leg reach (x the leg length) at its longest (touchdown / toe-off)
RUN_BOUNCE = 0.022                    # "bounce": pelvis rise / fall about its mean (m), high in flight, low at mid-stance
RUN_PELVIS = (7.0, 3.0, 0.006)        # pelvis yaw (deg), drop (deg), side shift (m)
RUN_LEAN = (7.0, 12.0, 0.5)           # "forward lean": pelvis tilt (deg), spine total (deg), push-off lean pulse (deg)
RUN_CHEST = 9.0                       # chest counter-yaw against the pelvis (deg; the shoulders drive the arms)
RUN_HEAD = (4.0, 0.2)                 # head (stabilised, eyes locked ahead): world forward pitch (deg), share of the chest's
                                      #   yaw it keeps (a head bobbing with the trunk drove the fringe into the brow)
RUN_LARM = (12.0, -12.0, 28.0, 50.0, 10.0)   # free (left) arm pump: lowered (deg), swing centre (deg, < 0 = forward),
                                      #   swing amplitude (deg), elbow bend (deg), bend pulse (deg; closes in front)
FORK_RUN = {"grip": (-0.08, -0.24, -0.04), "pump": (0.02, 0.005), "tilt_fwd": 9.0, "tilt_out": 0.0, "swing": 3.0}
                                      # fork carry at a sprint = the walk's convention (carried upright at his right side,
                                      #   the right hand at chest height) leaned into the run: the grip in the chest frame
                                      #   relative to the right shoulder joint (m), its pump fore-aft / up (m), the shaft
                                      #   tilted top-forward and top-inward (deg; < 0 = the butt out, clear of the knee drive
                                      #   and the heel kick), the pump's shaft rock (deg). (A 30-deg "charging" tilt was
                                      #   tried: its butt trailed into the back leg and the floor, the wrist bent 112 deg)
RUN_CAPE = (34.0, 2.0, 6.0)           # cloak trail relative to the leaning chest (deg, root .. tip x0.55 .. 1), flutter
RUN_CAPE_SPREAD = (0.0, 0.75, 1.0, 0.75, 0.35)   # the trail per cloak chain (his right front edge .. his left front
                                      #   edge): the front-wrapping edges trail less (at 55 deg x 1 the left wrap rose
                                      #   into the face and cut the torso)
RUN_CAPE_ARM = (0.0, 0.0, 0.0, 0.25, 0.4)   # each cloak chain follows the LEFT upper arm's fore-aft swing by this x
                                      #   (the cloak drapes over that arm: a full pump under a still cloak pokes through)
RUN_CAPE_ARM_FWD = 1.0                # ... x this on the FORWARD swing (the arm leaves under the front edge; a wrap
                                      #   dragged fully forward hung as a curtain over the legs)
RUN_HAIR = (6.0, 1.0, 2.5, 0.0)      # nape-tail trail (deg), flutter root / tip (deg), fringe lift off the brow (deg)
RUN_FT_DRAG = 0.5                     # the follow-through's pendulum drag (anchor acceleration) x this in the run only
RUN_TIE_LIFT = 0.85                  # the sash ties ride the left thigh's forward swing (x its angle past vertical)
RUN_OVERLAP = 1.0                     # chest lag behind the pelvis (frames)
FT = {"hair_fringe": {"driver": "head", "hz": 3.0, "zeta": 0.45, "drag": 1.0, "gain": 0.9},   # v3 re-tune (v2 3.4 / 0.42 /
      #   0.9 / 1.0): the bolder, heavier clumps swing slower and a touch less far (a 60-80 mm clump whipping at the thin
      #   lock's rate read as paper)
      "hair_side": {"driver": "head", "hz": 2.8, "zeta": 0.48, "drag": 0.9, "gain": 0.65},   # v2 3.2 / 0.45 / 0.8 / 0.8
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
_OUT_ROOT = SCRATCH or ROOT            # (v6: a scratch build mirrors improved/ + rigged/, so its reports never overwrite
                                      #   each other and the probes run on it unchanged)
OUT_IMPROVED = os.path.join(_OUT_ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(_OUT_ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(_OUT_ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(_OUT_ROOT, "improved", "textures")
TAG = "twin" if DIGEST_ONLY else ("scratch" if SCRATCH else "main")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "name_status": "named by the sheet (WREN, Oakvale Village)",
          "source": "none: the artist's three-view sheet design/reference/wren-character-sheet.webp", "tier": "hero",
          "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}
SECTIONS = ["wren_s1_body.py", "wren_s2_regions.py", "wren_s3_outfit.py", "wren_s4_cloak.py", "wren_s5_hair.py",
            "wren_s6_assemble.py", "wren_s7_rig.py", "wren_s8_clips.py"]
HAIR_DIGEST_ONLY = argv[argv.index("--hair-digest-only") + 1] if "--hair-digest-only" in argv else None
for sec_ in SECTIONS:
    if HAIR_DIGEST_ONLY and sec_ == "wren_s6_assemble.py":
        # v5.1 hair-stability probe (wren_run.ps1): sections 1-5 only (nothing saved), the exact hair digest -> json
        json.dump({"hair_geometry": DIG["hair_geometry"], "overrides": OVERRIDES, "seconds": round(time.time() - T0, 1)},
                  open(HAIR_DIGEST_ONLY, "w"), indent=1)
        print("HAIRDIGEST", DIG["hair_geometry"], json.dumps(OVERRIDES))
        sys.stdout.flush(); os._exit(0)
    _p = os.path.join(HERE, sec_)
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
