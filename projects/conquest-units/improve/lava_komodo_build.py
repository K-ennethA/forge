"""Lava Komodo -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/lava_komodo_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-11 "NEW UNITS x6" (section LAVA KOMODO) + the sheet
design/reference/lava_komodo-character-sheet.webp (intake: design/reference/lava_komodo/intake.json, orthos FRONT + SIDE,
the side faces image-LEFT, --cuts 305,612). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed:
one pass, render, report; docs/lane-conventions.md "Two speeds").

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet.
  FRONT view (X, heights):  X = (x_px - 153) / 100, z = (687 - y_px) / 100   (intake axis + floor: the pool's near edge)
  SIDE view  (Y, heights):  Y = (x_px - 914) / 100, z = (695 - y_px) / 100   (faces image-LEFT: image left = -Y = the
                            front; 914 = the intake axis. FLOOR PINNED at y 695 = the pool's near edge under the body
                            (x 760-1050: lava down to y 680-704), NOT the intake's 788, which is the tip of the tail curl
                            the sheet draws swung toward the viewer. With y 695 the crest tip is z 4.52 (side) vs 4.55
                            (front): the two views agree.)
  The sheet's views are drawn with a slight downward camera (the pool reads as a 60-70 px band, the face sits ~0.1 u higher
  in the side view than the front): X always from the front, Y from the side, heights averaged where they disagree (each
  constant cites its rows). Front = -Y, +X = the unit's LEFT, floor z = 0 under the pool.

THE BODY PLAN as drawn: a SITTING komodo lizard -- an upright chest and neck propped on two planted front legs, the body
sloping back over big haunches into a long tapered tail; broken volcanic ROCK PLATES separated by clean glowing MAGMA
CRACKS (a dorsal crack down the spine in the back view); a komodo head (snout, heavy brow plates, a pale stone snout,
an expressive glowing orange eye with a slit pupil, a glowing lower jaw); a FLAME CREST on the head top and nape; a
glowing molten chest / throat; a MELTING lower body (the limbs and belly merge into lava, drips) pooling into a glowing
LAVA POOL on the floor.
HOUSE RULE (artist 2026-10-10): the rest pose is MIRROR-SYMMETRIC about X = 0. The sheet curls the tail toward the viewer
(side) / to one side (back); it is built STRAIGHT BACK along the floor (reported).

Pipeline:
  1. BODY = ONE SDF (magmoo_sdf banded grid + smooth unions): torso sections + chest + neck + cranium + snout + jaw + brow
     ridges + cheek spikes + front legs + haunches + tail + teardrop drips + the lobed LAVA POOL slab (the big pool blend
     k IS the melt: the limbs, belly and tail flare into the pool). Marching tets = the HIGH; collapse decimation = the LOW;
     the LOW is mirror-halved (X >= 0 half + its mirror, crystal_canine's technique) so the rest mesh is exactly symmetric.
  2. ROCK PLATES = Voronoi cells of Poisson-disc seeds on the +X half (mirrored; the mirror plane is always a cell
     boundary = the dorsal crack). Per point h = the distance to the nearest bisector plane (linear per seed pair); the
     MAGMA CRACK band is h < CRACK_W, iso-cut into the half shell (refined where a face spans two cells first). Plate tone
     per cell (3 chips, hashed). The molten front / jaw / tail underside, the lava skirt (melting lower body), the drips,
     the pool, the snout + brow plates are iso-cut regions too; the cut half is mirrored + welded.
     Normal detail: the HIGH is grooved along the cracks (a subtle bevel) + a tiny per-plate relief before the bake.
  3. CLOSED PARTS (lava_komodo_parts.py): eye socket / iris / pupil / glint, nostrils, the mouth line ribbon, the FLAME
     CREST (layered closed tongues: orange outer + yellow inner; centre tongues lie on the mirror plane, side ones mirrored).
  4. palette -> Col / Glow, flat shaded, Smart UV, bake normal + AO from the grooved SDF high + every closed part except
     the crest.
  5. RIG: root > hips > spine.001 > .002 > .003 > neck > head > crest; spine.003 > leg_{L,R}.upper > .lower;
     hips > tail.001 > .002 > .003 > .004. The pool + the lowest body band are anchored to hips (never animated).
     Clips PLACEHOLDER (movement + height open): idle = breathing + a crest flicker (scale pulse) + a tail sway;
     walk = an in-place lizard crawl-shuffle (spine S-wave, alternating front-leg reaches, tail sway), base on the floor. glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                   # noqa: E402  (read-only use)
import palettes as PAL               # noqa: E402  (read-only use)
import magmoo_sdf as SD              # noqa: E402  (read-only use: the SDF kit)
import lava_komodo_parts as FP       # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "lava_komodo"
CHAR_ID = "lava_komodo"               # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
DEGEN_DIST = 1e-4                     # cut slivers shorter than this are collapsed (ember_stump uv_health lesson)
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- TORSO: (Y, centre z, half-width X, half-height z) elliptic XZ sections, smooth (Catmull) along Y. Top = the SIDE
#   view back line (z = cz + rz): x 860 y 419 (Y -0.54, z 2.76) / 920 458 (0.06, 2.37) / 1000 492 (0.86, 2.03) /
#   1060 531 (1.46, 1.64) / 1120 590 (2.06, 1.05) / 1180 634 (2.66, 0.61); half-width: FRONT view between the legs
#   (the body edge +-1.04 at y 485-515 includes the legs, the torso itself ~+-0.95); the bottom sinks into the pool.
TORSO_KNOTS = [(-1.75, 1.55, 0.78, 1.25), (-1.25, 1.35, 0.93, 1.50), (-0.60, 1.15, 0.98, 1.52), (0.10, 0.95, 0.98, 1.42),
               (0.80, 0.75, 0.94, 1.30), (1.50, 0.55, 0.85, 1.08), (2.10, 0.35, 0.70, 0.72), (2.70, 0.20, 0.52, 0.40)]
# ---- CHEST (SIDE view front line: x 717-720 at y 480-510 (Y -1.95, z 1.9-2.15), x 735-738 at y 525-555 (Y -1.78,
#   z 1.4-1.7); FRONT view: the upright glowing chest between the legs)
CHEST_C, CHEST_R, CHEST_K = (0.0, -1.17, 1.55), (0.85, 0.58, 1.15), 0.25
# ---- NECK (round cone, X squashed: FRONT view +-0.60 at y 405 (z 2.82), +-0.73 at y 425; SIDE view throat front x 680-686
#   at y 435-465 (Y -2.3, z 2.3-2.6), nape x 800 y 375 (Y -1.14, z 3.20))
NECK_A, NECK_B, NECK_R, NECK_SX, NECK_K = (0.0, -1.38, 2.10), (0.0, -1.78, 3.18), (0.74, 0.55), 0.92, 0.20
# ---- HEAD (FRONT view: head x 103-203 (+-0.50) at y 315-345, top y 318; SIDE view: snout tip x 598 y 330-365 (Y -3.16,
#   z 3.3-3.65), head top x 640 y 322 (z 3.73), back of the head ~x 760 (Y -1.54))
HEAD_C, HEAD_R, HEAD_K = (0.0, -2.00, 3.45), (0.50, 0.56, 0.35), 0.16      # "head size" (cranium)
SNOUT_C, SNOUT_R, SNOUT_K = (0.0, -2.64, 3.43), (0.32, 0.52, 0.24), 0.22   # "snout" (FRONT +-0.28 at y 335-375)
JAW_C, JAW_R, JAW_K = (0.0, -2.42, 3.14), (0.42, 0.60, 0.19), 0.12         # "lower jaw" (FRONT chin y 395 -> z 2.92)
BROW_C, BROW_R, BROW_TILT, BROW_K = (0.30, -2.28, 3.68), (0.15, 0.19, 0.06), 18.0, 0.08   # "brow plates" (+X; the angry
#   tilt: the outer end higher, FRONT brows x 108-140 / 166-198 at y 318-330)
SPIKE_A, SPIKE_B, SPIKE_R, SPIKE_K = (0.40, -1.85, 3.30), (0.64, -1.72, 3.26), (0.10, 0.025), 0.06   # "cheek spikes"
#   (FRONT view x 91 / 216 at y 355-365 -> X +-0.63, z 3.27)
# ---- FRONT LEGS (+X; mirrored). FRONT view: the dark rock legs x 50-115 / 190-258 at y 470-615 (X +-0.40..1.05);
#   SIDE view: the near leg from the shoulder x 790 y 460 (Y -1.24, z 2.35) down to x 720 y 600 (Y -1.94, z 0.95), the
#   foot sinks into the lava at x ~700 y 620 (Y -2.14, z 0.75)
LEG_S, LEG_E, LEG_F = (0.74, -1.22, 2.15), (0.80, -1.78, 0.98), (0.82, -1.98, 0.22)   # shoulder / elbow / foot
LEG_RAD, LEG_K = (0.36, 0.30, 0.34), 0.08   # v1.1: blend 0.16 -> 0.08 so the legs read as limbs, not a chest skirt
# ---- HAUNCHES (+X; mirrored). FRONT view: the outer bulge +-1.21 at y 555-595 (z 0.9-1.3); SIDE view: the hind thigh
#   mass x 950-1150 (Y 0.36-2.36) under the back line
HAUNCH_C, HAUNCH_R, HAUNCH_K = (0.78, 1.25, 0.78), (0.48, 0.95, 0.78), 0.25
# ---- TAIL: built STRAIGHT BACK along the floor (house rule; the sheet curls it toward the viewer). Length = the drawn
#   curl's centreline: SIDE view (1180,650) -> (1235,730) -> (1180,780) -> tip (1065,788) ~ 2.9 u; knots (Y, z, radius)
TAIL_KNOTS = [(2.35, 0.46, 0.50), (3.15, 0.36, 0.40), (3.95, 0.25, 0.28), (4.75, 0.15, 0.16), (5.50, 0.07, 0.06)]
TAIL_K = 0.20
# ---- DRIPS (smooth teardrop lobes hanging off the body): (cast axis, X or Y of the cast line, top z, length, r_top,
#   r_bulb). SIDE view: the throat drip x 686 y 420-470 (z 2.25-2.75), belly-edge drips x 837 / 868 y 600-630 (Y -0.77 /
#   -0.46, z 0.65-0.95); FRONT view: chest-bottom drip x 150-160 y 600-625, flank drips x 41 / 270 y 600-625 (+-1.12)
DRIPS = [("Y", 0.0, 2.66, 0.40, 0.05, 0.085), ("Y", 0.0, 0.95, 0.30, 0.05, 0.08), ("X", -0.75, 1.00, 0.32, 0.05, 0.08),
         ("X", -0.45, 0.94, 0.28, 0.045, 0.075), ("X", 0.95, 0.98, 0.30, 0.05, 0.08)]
DRIP_IN, DRIP_K = 0.04, 0.06          # how deep the drip top sits in the body; blend
# ---- LAVA POOL (flat slab on the floor; 2D lobes (cx, cy, ax, ay), the +X ones mirrored). FRONT view: the pool spans
#   x 5-304 at y 635-665 (+-1.5, cut by the view edge); SIDE view: from x 598 (Y -3.16) back under the body, pools round
#   the tail (back view y 700-790)
POOL_LOBES = [(0.0, -2.15, 1.55, 0.95), (0.95, -0.55, 0.80, 1.25), (0.90, 1.15, 0.72, 1.05), (0.0, 2.95, 0.72, 0.75)]
POOL_K2, POOL_TOP, POOL_EDGE = 0.40, 0.08, 0.035   # lobe blend, "pool thickness", rim rounding
POOL_K = 0.32                         # "melt": the body -> pool blend (the limbs / belly / tail flare into the lava)
SDF_STEP, SDF_BAND = 0.03, 0.40       # body grid (band > max smooth-union k 0.32 + 2 steps)
BODY_TRIS = 7000                      # "body mesh detail" (the full shell before halving + the colour cuts)
# ---- REGIONS
POOL_CUT = 0.11                       # "pool": the body below this is the glowing pool (top at POOL_TOP 0.08)
SKIRT_Z_BODY, SKIRT_Z_LEG, SKIRT_Z_HAUNCH = 1.00, 0.40, 0.45   # "melting lower body": lava below this height on the
#   body sides / the front legs / the haunches (SIDE view: the lower torso round the legs is lava from y ~590 (z 1.05) at
#   x 800-950, the near front leg is dark rock down to y ~650 (z 0.45), the haunch plates reach y ~650; FRONT view: lava
#   below y ~610-640 on the legs)
SKIRT_A, SKIRT_L = 0.13, 0.62         # + SKIRT_A cos(2 pi (Y + 0.8 |X|) / SKIRT_L): the lava tongues
MOLT_HW = [(0.0, 0.62), (1.0, 0.56), (1.9, 0.50), (2.3, 0.46), (2.6, 0.47), (3.0, 0.52), (3.4, 0.52)]   # "molten chest": (z, half-
#   width) FRONT view glow x 105-200 (+-0.48) at y 400-600, the throat full width under the jaw
MOLT_YF = [(0.0, -1.20), (1.9, -1.20), (2.4, -1.50), (2.9, -1.72), (3.4, -1.80)]          # (z, Y): the glow lies in
#   front of this (SIDE view: the orange throat x 640-760 at y 380-500 -> back edge Y -1.5 .. -1.75)
MOLT_TONGUE = (0.05, 0.42)            # "flame edge" of the molten panel: amplitude, wavelength (z)
MOLT_MID = [(0.55, -0.02), (0.95, 0.17), (2.0, 0.40), (2.7, 0.43), (3.2, 0.45)]   # (z, half-width) the hotter V inside
MOLT_CORE = [(0.95, -0.02), (1.35, 0.11), (2.1, 0.27), (2.7, 0.29), (3.2, 0.30)]  # the yellow core V (FRONT view: the yellow
#   V x 125-180 at y 450 (+-0.27) narrowing to a point at y ~590 (z 0.97); orange round it, between the legs)
MOUTH_LINE = [(-3.20, 3.28), (-2.60, 3.27), (-2.00, 3.22), (-1.70, 3.16)]   # (Y, z) the mouth seam: the jaw below is
#   molten (SIDE view mouth x 603 y 352 -> z 3.43 at the tip, FRONT view y 375 -> 3.12: averaged, reported)
MOUTH_Y1 = -1.70
TAIL_UNDER = 0.0                      # "tail underside": the tail below its centre line + this glows
# ---- ROCK PLATES + MAGMA CRACKS (LAVA BODY TEXTURE panel: big angular plates 0.5-0.9 u, cracks 8-12 px)
PLATE_S = 0.80                        # "plate size" on the body (Poisson-disc spacing; v1.1 0.62 -> 0.80: fewer, larger)
PLATE_S_NECK, PLATE_S_HEAD = 0.52, 0.34   # smaller plates on the neck / head ("rugged stone plates")
PLATE_S_TAIL = (0.62, 0.30)           # tail plates: root -> tip
PLATE_MINX = 0.40                     # seeds stay this x spacing off the mirror plane (no sliver plate on the dorsal crack)
PLATE_TONES = [("plate_a", 0.60), ("plate_b", 0.32), ("plate_c", 0.08)]   # "plate tone mix" (hashed per plate)
PLATE_DARK_BOX = ((-1.3, 0.6), 2.4)   # dark-stone plates only on the torso flank: Y window + below this height (none on the
#   face (v1.1 gate: a black patch round the eye) nor the haunch back / tail (v1: a dirty dark wedge))
PLATE_SEED = 3.7
CRACK_W = 0.025                       # "magma crack" half-width (v1.1 0.045 -> 0.025: the sheet's 0.04-0.06 wide gaps)
BIS_SNAP = 0.12                       # bisector split: an edge crossing nearer its end than this is left (that end is in
#   the crack band already)
PLATE_PROUD = 0.080                   # "plate height": a full-size slab stands this far out over the crack channel (scaled
#   down with the plate size: head / tail plates lower)
PLATE_DOME = 0.30                     # "plate dome": the slab rises over this fraction of its spacing from the crack edge
TAIL_SLAB_R = 0.45                    # the tail slabs flatten as the tail thins (x (radius / this)^2: no folds at the tip)
CRACK_CHANNEL = 0.012                 # the crack centre line sinks this far (the channel floor)
PLATE_RELIEF = 0.010                  # per-plate height jitter
EYE_MASK = ((0.39, -2.30, 3.56), 0.20)    # no slab displacement round the eye decals (centre, radius)
NOSE_MASK = ((0.11, -3.12, 3.48), 0.10)   # ... nor round the nostrils
# ---- FACE (+X; mirrored). Eye: FRONT x 118 / 188 y 337 (X +-0.35, z 3.50); SIDE x 677 y 333 (Y -2.37, z 3.62) -> z 3.56
EYE_Y, EYE_Z = -2.30, 3.56
EYE_SOCKET = [(-0.15, 0.00), (-0.10, 0.065), (0.00, 0.092), (0.10, 0.075), (0.155, 0.01), (0.11, -0.055), (0.00, -0.08),
              (-0.10, -0.055)]       # "eye shape" (u forward, v up) -- HEAD DETAIL almond, dark socket line art
IRIS_R, PUPIL_R, GLINT = 0.072, (0.020, 0.060), (0.022, 0.030, 0.017)   # iris radius, slit half-size, glint (u, v, r)
EYE_PROUD = (0.012, 0.022, 0.030, 0.036)   # socket / iris / pupil / glint height over the surface
NOSE_X, NOSE_Z, NOSE_R = 0.11, 3.48, 0.028  # nostrils (FRONT x 140 / 166 y 357)
MOUTH_W = 0.016
# ---- FLAME CREST (FRONT view: tip x 162 y 232 (z 4.55), side tongues (122,275) / (186,275) (X +-0.31, z 4.12), outer
#   (112,305) / (195,305) (+-0.41, z 3.82); SIDE view tips (675,268) (727,240) (755,285) (765,300) -> Y -2.39 / -1.87 /
#   -1.59 / -1.49 at z 4.27 / 4.55 / 4.10 / 3.95; the crest runs from the brow back to the nape x 665-780)
CREST = [("crest_outer", 0.00, -1.92, 3.66, 6.0, 90.0, 0.92, 0.34, 0.55, 0.24, 0.3, 0),
         ("crest_outer", 0.00, -2.34, 3.56, 12.0, 270.0, 0.68, 0.27, 0.55, -0.26, 1.4, 0),
         ("crest_outer", 0.00, -1.56, 3.56, 14.0, 90.0, 0.60, 0.27, 0.55, 0.26, 2.2, 0),
         ("crest_outer", 0.00, -1.36, 3.40, 16.0, 90.0, 0.50, 0.22, 0.55, 0.22, 3.1, 0),
         ("crest_inner", 0.00, -2.10, 3.66, 8.0, 270.0, 0.62, 0.21, 0.85, 0.16, 0.7, 0),
         ("crest_inner", 0.00, -1.66, 3.60, 10.0, 90.0, 0.40, 0.18, 0.85, 0.14, 1.9, 0),
         ("crest_outer", 0.20, -2.06, 3.60, 18.0, 20.0, 0.60, 0.24, 0.60, 0.22, 1.9, 3),
         ("crest_outer", 0.32, -1.80, 3.48, 30.0, 25.0, 0.46, 0.20, 0.60, 0.20, 2.6, 3),
         ("crest_inner", 0.16, -2.16, 3.62, 14.0, 15.0, 0.38, 0.15, 0.90, 0.10, 2.9, 0)]
#   "crest tongues": (layer, X root (0 = on the mirror plane; > 0 mirrored), Y, z (buried in the head), tilt deg from
#   vertical, tilt azimuth deg (0 = +X out, 90 = back, 270 = forward), length, radius, flatness across X, curl, phase, lobes)
CREST_NS, CREST_SEG = 10, 10
CREST_TIP_POW = 0.9                   # "flame fullness": < 1 = fuller licks (1.35 = the turtle's thin spikes)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
GLOW_CAP = 2.0                        # emissive ceiling: max channel / 255 x emission_scale x emission_strength
# ---- rig
SPINE_J = [(0.0, 1.95, 0.70), (0.0, 1.00, 1.00), (0.0, 0.00, 1.25), (0.0, -0.90, 1.55), (0.0, -1.42, 2.00)]
#   joints hips | spine.001 | spine.002 | spine.003 | neck (the chain from the pelvis forward-up to the neck base)
SPINE_SPLIT = (1.00, 0.00, -0.90)     # body Y splits hips | .001 | .002 | .003
SPINE_JW = 0.35
NECK_TOP = (0.0, -1.85, 3.15)
HEAD_TIP = (0.0, -2.95, 3.45)
LEG_JW = 0.15                         # leg upper | lower blend at the elbow height
MEMBER_BLEND = 0.10                   # head / neck / leg / tail / pool membership blend (own-field half-width)
ANCHOR_Z = (0.10, 0.55)               # the body below z 0.10 rides hips fully, fading out by 0.55 (the pool + melt base
#   never move: hips is never animated)
TAIL_J = [2.30, 3.05, 3.80, 4.60, 5.50]   # tail.001..004 joints (Y)
TAIL_JW = 0.30
# ---- clips (PLACEHOLDER: the artist has not given the Lava Komodo's movement or height). 24 fps; integer harmonics
#   per loop -> frame N+1 == frame 1. hips never move: the pool + the melting base stay exactly on the floor.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATH = (1.2, 0.025)            # "breathing": spine.002 pitch-up deg, spine.003 rise (u) -- up only, 2 cycles
IDLE_HEAD = 1.5                       # head nod deg (2 cycles, lagging)
TAIL_IDLE = 4.0                       # "tail sway" deg per tail bone (1 cycle, lag 0.5 rad per bone)
CREST_FLICK = {"idle": (0.08, (7, 11, 17), 0.0), "walk": (0.10, (4, 6, 9), 0.04)}   # "crest flicker": length +-,
#   harmonics per loop, mean stretch; the width narrows by CREST_WIDTH_K x stretch
CREST_WIDTH_K = 0.5
CREST_WOBBLE = 1.5                    # crest wobble deg
WALK_N = 48                           # "crawl loop": 2 s (one reach per front leg)
WALK_SPINE = 4.0                      # "spine S-wave" yaw deg (alternating along the chain)
WALK_REACH = 12.0                     # "front-leg reach": upper leg swings forward + up (deg), lower leg follows half
WALK_TAIL = 7.0                       # tail sway deg per bone
WALK_HEAD = 3.0                       # head counter-yaw deg (keeps the gaze steady)
WALK_CREST_TRAIL = 6.0                # the crest leans back while walking (deg)

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
          "design/reference/lava_komodo-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; floor z = 0 at the pool's near edge (front y 687, side y 695); X from the "
                   "front ortho, Y from the side ortho", "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
TAU = 2.0 * math.pi
MIRX = np.array([-1.0, 1.0, 1.0])


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
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(len(np.unique(roots[keep_v]))),
                           "dropped_verts": int((~keep_v).sum())}


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)


def deviation(HV, HF, LV, LF):
    bvh_hi = BVHTree.FromPolygons(HV.tolist(), [list(map(int, f)) for f in HF])
    dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
    bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
    _hs = np.arange(0, len(HV), max(1, len(HV) // 30000))
    dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
    bb_h = np.ptp(HV, 0); bb_l = np.ptp(LV, 0)
    return {"low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                            "max": round(float(dev.max()), 5)},
            "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
            "bbox_low_over_high": (bb_l / bb_h).round(5).tolist()}


def mirror_halve(V, F):
    """SYMMETRY (artist house rule; crystal_canine's technique): marching tets (Kuhn diagonal) + collapse decimation are not
    mirror-exact, so the low keeps its X >= 0 half (bisected at X = 0); the half is cut and coloured, then mirror_weld()
    adds its mirror image welded on the seam."""
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
    Vh[np.abs(Vh[:, 0]) < 1e-6, 0] = 0.0
    used = np.zeros(len(Vh), bool)
    for f_ in Fh:
        used[f_] = True
    remap = -np.ones(len(Vh), dtype=np.int64); remap[used] = np.arange(int(used.sum()))
    return Vh[used], [[int(remap[i]) for i in f_] for f_ in Fh]


def mirror_weld(V, F, seam_tol=1e-4):
    """half (X >= 0, open on the seam) -> the closed symmetric mesh: seam vertices (|X| < seam_tol, snapped to 0) shared,
    the mirror copy's faces reversed. -> V, F, seam vertex count (faces [0, n) = the half, [n, 2n) = their mirrors)"""
    V = V.copy()
    seam = np.abs(V[:, 0]) < seam_tol
    V[seam, 0] = 0.0
    assert V[:, 0].min() >= 0.0, "the half crosses the mirror plane: min X %.6f" % V[:, 0].min()
    n_ = len(V)
    mid = np.where(seam, np.arange(n_), n_ + np.arange(n_))
    Vm = np.vstack([V, V * MIRX])
    Fm = [list(f) for f in F] + [[int(mid[i]) for i in f[::-1]] for f in F]
    used = np.zeros(len(Vm), bool)
    for f_ in Fm:
        used[f_] = True
    remap = -np.ones(len(Vm), dtype=np.int64); remap[used] = np.arange(int(used.sum()))
    return Vm[used], [[int(remap[i]) for i in f_] for f_ in Fm], int(seam.sum())


def cut_island(V, F, field_fn, cuts, max_edge=None, face_pred=None, rounds=2, split_seeds=None):
    """ember_stump's iso-cutter (via volcano_turtle) on one island: every cut = (field, tau, gate) with gate = None or
    [(field, '>'/'<', v)] that both edge ends must satisfy. max_edge: first split edges longer than this on the faces
    face_pred(VF (nf, 3, 3) corner coords) selects (a band narrower than a triangle never crosses an edge); the fields are
    then evaluated on the refined vertices (field_fn(V) -> {name: values}). Slivers dissolved + flipped, min area gated.
    -> cut V, F, per-face field means, normals, log"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    refine = 0
    if max_edge:
        for _ in range(rounds):
            faces = [f for f in bm.faces]
            VF = np.array([[v.co[:] for v in f.verts] for f in faces])
            EL = np.linalg.norm(VF - np.roll(VF, 1, axis=1), axis=2).max(1)
            sel = (EL > max_edge) & face_pred(VF)
            big = [f for f, s_ in zip(faces, sel) if s_]
            if not big:
                break
            eds = list({e for f in big for e in f.edges})
            refine += len(big)
            bmesh.ops.subdivide_edges(bm, edges=eds, cuts=1, use_grid_fill=True)
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    nbis = 0
    if split_seeds is not None:
        # every Voronoi bisector crossing is split into the mesh exactly: along an edge from cell i to cell j the field
        # |p - si|^2 - |p - sj|^2 is LINEAR, so the split lands on the bisector plane (the crack's centre line); the crack
        # band edges (h = CRACK_W, cut below) then lie between a centre-line vertex and a plate vertex: clean, even width
        Ss = np.asarray(split_seeds, float)
        bm.verts.ensure_lookup_table(); bm.verts.index_update()
        vcell, _ = FP.plate_cells(np.array([v.co[:] for v in bm.verts]), Ss)
        vc = {v: int(vcell[v.index]) for v in bm.verts}
        newv = set()
        for e in list(bm.edges):
            a, b = e.verts
            ca, cb = vc[a], vc[b]
            if ca == cb or ca < 0 or cb < 0:
                continue
            pa, pb = np.array(a.co[:]), np.array(b.co[:])
            fa = float(((pa - Ss[ca]) ** 2).sum() - ((pa - Ss[cb]) ** 2).sum())
            fb = float(((pb - Ss[ca]) ** 2).sum() - ((pb - Ss[cb]) ** 2).sum())
            if fa * fb >= 0:
                continue
            tt = fa / (fa - fb)
            if tt < BIS_SNAP or tt > 1.0 - BIS_SNAP:
                continue
            _, nv = bmesh.utils.edge_split(e, a, tt)
            newv.add(nv); vc[nv] = -1
        pairs = []
        for f in bm.faces:
            cv = [v for v in f.verts if v in newv]
            if len(cv) == 2:
                pairs.append(cv)
        for cv in pairs:
            bmesh.ops.connect_verts(bm, verts=cv)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        nbis = len(newv)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    fields = field_fn(np.array([v.co[:] for v in bm.verts]))
    LAY = {k: bm.verts.layers.float.new(k) for k in fields}
    for v in bm.verts:
        for k, arr in fields.items():
            v[LAY[k]] = float(arr[v.index])

    def gate_ok(spec, a, b):
        if spec is None:
            return True
        for key, op, val in spec:
            for v in (a, b):
                x = v[LAY[key]]
                if (op == ">" and not x > val) or (op == "<" and not x < val):
                    return False
        return True

    def iso_cut(key, tau, spec):
        L = LAY[key]
        eps = 1e-5 * max(1.0, abs(tau))
        on = lambda v: abs(v[L] - tau) <= eps
        side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
        ok = lambda a, b: gate_ok(spec, a, b)
        for e in bm.edges:
            a, b = e.verts
            sa, sb = side(a), side(b)
            if sa * sb < 0 and ok(a, b):
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts_ = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
        for e in cuts_:
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
        return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts_), "face_connects": len(pairs)}

    log = [{"refined_faces": refine, "max_edge": max_edge, "bisector_splits": nbis}] + [iso_cut(k_, tau_, g_) for k_, tau_, g_ in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    n0 = len(bm.faces)
    bmesh.ops.dissolve_degenerate(bm, dist=DEGEN_DIST, edges=bm.edges[:])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    flips = collapses = 0
    for it in range(12):
        flat = [f for f in bm.faces if f.calc_area() <= DEGEN_DIST ** 2]
        if not flat:
            break
        if it < 6:
            eds = list({max(f.edges, key=lambda e: e.calc_length()) for f in flat})
            res = bmesh.ops.rotate_edges(bm, edges=eds, use_ccw=False)
            flips += len(res["edges"])
        else:                                                # flips stuck: merge each sliver's shortest edge
            f0 = flat[0]
            bmesh.ops.collapse(bm, edges=[min(f0.edges, key=lambda e: e.calc_length())], uvs=False)
            collapses += 1
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    min_area = min(f.calc_area() for f in bm.faces)
    log.append({"degenerate_dissolved_faces": n0 - len(bm.faces), "sliver_edge_flips": flips, "sliver_collapses": collapses,
                "min_face_area": float(min_area)})
    assert min_area > DEGEN_DIST ** 2, log[-1]
    bm.verts.index_update(); bm.faces.index_update(); bm.normal_update()
    CV = np.array([v.co[:] for v in bm.verts])
    CF = [[v.index for v in f.verts] for f in bm.faces]
    FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
    FN = np.array([f.normal[:] for f in bm.faces])
    bm.free()
    return CV, CF, FVAL, FN, log


def own_fields(D):
    """D: {group: distances} -> {group: min(others) - own} (> 0 where the group owns the surface)"""
    keys = list(D)
    M = np.stack([D[k] for k in keys], 1)
    return {k: np.delete(M, j, axis=1).min(1) - M[:, j] for j, k in enumerate(keys)}


# =========================================================================== 1. BODY SDF
t_sdf = time.time()
_tk = np.array(TORSO_KNOTS, float)
_td = SD.catmull(_tk, 16)
_td = _td[np.argsort(_td[:, 0], kind="stable")]
TY = _td[:, 0]


def sd_torso(P):
    Y = P[:, 1]
    Yc = np.clip(Y, TY[0], TY[-1])
    cz, rx, rz = (np.interp(Yc, TY, _td[:, j]) for j in (1, 2, 3))
    q = np.hypot(P[:, 0] / rx, (P[:, 2] - cz) / rz)
    d = (q - 1.0) * np.minimum(rx, rz)
    return np.maximum(d, np.maximum(TY[0] - Y, Y - TY[-1]))


def sd_chest(P):
    return SD.sd_ellipsoid(P, CHEST_C, CHEST_R)


def sd_neck(P):
    Q = P.copy()
    Q[:, 0] /= NECK_SX
    return SD.sd_round_cone(Q, NECK_A, NECK_B, NECK_R[0], NECK_R[1]) * NECK_SX


def sd_cranium(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R)


def sd_snout(P):
    return SD.sd_ellipsoid(P, SNOUT_C, SNOUT_R)


def sd_jaw(P):
    return SD.sd_ellipsoid(P, JAW_C, JAW_R)


_bt = math.radians(BROW_TILT)
BROW_FR = np.array([[math.cos(_bt), 0.0, math.sin(_bt)], [0.0, 1.0, 0.0], [-math.sin(_bt), 0.0, math.cos(_bt)]])
sd_brow = FP.sd_xfold(lambda P: SD.sd_ellipsoid(P, BROW_C, BROW_R, frame=BROW_FR))
sd_spike = FP.sd_xfold(lambda P: SD.sd_round_cone(P, SPIKE_A, SPIKE_B, SPIKE_R[0], SPIKE_R[1]))
sd_leg = FP.sd_xfold(lambda P: np.minimum(SD.sd_round_cone(P, LEG_S, LEG_E, LEG_RAD[0], LEG_RAD[1]),
                                          SD.sd_round_cone(P, LEG_E, LEG_F, LEG_RAD[1], LEG_RAD[2])))
sd_haunch = FP.sd_xfold(lambda P: SD.sd_ellipsoid(P, HAUNCH_C, HAUNCH_R))
_tl = np.array(TAIL_KNOTS, float)


def sd_tail(P):
    d = np.full(len(P), 1e9)
    for a, b in zip(_tl[:-1], _tl[1:]):
        d = np.minimum(d, SD.sd_round_cone(P, (0.0, a[0], a[1]), (0.0, b[0], b[1]), a[2], b[2]))
    return d


def tail_cz(Y):
    return np.interp(Y, _tl[:, 0], _tl[:, 1])


_POOL_FULL = []
for cx, cy, ax, ay in POOL_LOBES:
    _POOL_FULL.append((cx, cy, ax, ay))
    if cx > 0:
        _POOL_FULL.append((-cx, cy, ax, ay))


def sd_pool(P):
    return FP.sd_lobed_slab(P, _POOL_FULL, POOL_K2, POOL_TOP, POOL_EDGE)


CORE_OPS = [("torso", sd_torso, 0.0), ("chest", sd_chest, CHEST_K), ("neck", sd_neck, NECK_K),
            ("cranium", sd_cranium, HEAD_K), ("snout", sd_snout, SNOUT_K), ("jaw", sd_jaw, JAW_K),
            ("brow", sd_brow, BROW_K), ("spike", sd_spike, SPIKE_K), ("leg", sd_leg, LEG_K),
            ("haunch", sd_haunch, HAUNCH_K), ("tail", sd_tail, TAIL_K)]


def sd_eval(P, ops):
    d = None
    for nm, fn, k in ops:
        x = fn(P)
        d = x if d is None else SD.smin(d, x, k)
    return d


def sd_core(P):
    """the body without drips + pool (drip placement)"""
    return sd_eval(P, CORE_OPS)


def cast(fn, origin, direction, tmax=4.0, step=0.004):
    """the first crossing of fn < 0 from origin along direction -> (point, outward normal)"""
    o = np.asarray(origin, float); dvec = unit(direction)
    ts = np.arange(0.0, tmax, step)
    d = fn(o[None] + ts[:, None] * dvec[None])
    k = int(np.nonzero(d < 0)[0][0])
    a, b = ts[k - 1], ts[k]
    for _ in range(40):
        m = 0.5 * (a + b)
        if fn((o + m * dvec)[None])[0] < 0:
            b = m
        else:
            a = m
    p = o + 0.5 * (a + b) * dvec
    e = 1e-4
    gr = np.array([fn((p + e * ax)[None])[0] - fn((p - e * ax)[None])[0] for ax in np.eye(3)])
    return p, unit(gr)


DRIP_GEO = []                               # (top, bulb, r_top, r_bulb); the +X ones are mirrored by the fold below
for axis, c_, ztop, L_, rt, rb in DRIPS:
    if axis == "Y":
        p_, n_ = cast(sd_core, (c_, -4.5, ztop), (0.0, 1.0, 0.0))
    else:
        p_, n_ = cast(sd_core, (2.5, c_, ztop), (-1.0, 0.0, 0.0))
    nh = unit([n_[0], n_[1], 0.0])
    top = p_ - nh * DRIP_IN
    bulb = p_ + nh * (0.55 * rb) - np.array([0.0, 0.0, L_])
    DRIP_GEO.append((top, bulb, rt, rb))


def _sd_drips_pos(P):
    d = np.full(len(P), 1e9)
    for top, bulb, rt, rb in DRIP_GEO:
        d = np.minimum(d, FP.sd_teardrop(P, top, bulb, rt, rb, SD.sd_round_cone))
    return d


sd_drips = FP.sd_xfold(_sd_drips_pos)
BODY_OPS = CORE_OPS + [("drip", sd_drips, DRIP_K), ("pool", sd_pool, POOL_K)]


def sd_body(P):
    """the analytic twin of the body grid (same op order, no band clipping): surface queries for the decals"""
    return np.maximum(sd_eval(P, BODY_OPS), -P[:, 2])


def _box_ell(c, r, mirror=False):
    c = np.asarray(c, float); r = np.asarray(r, float)
    lo, hi = c - r, c + r
    if mirror:
        lo = np.minimum(lo, c * MIRX - r); hi = np.maximum(hi, c * MIRX + r)
    return lo, hi


def _box_cone(a, b, r, mirror=True):
    A = np.array([a, b], float)
    lo, hi = A.min(0) - r, A.max(0) + r
    if mirror:
        lo[0] = min(lo[0], -hi[0]); hi[0] = max(hi[0], -lo[0])
    return lo, hi


_dg = np.array([g[0] for g in DRIP_GEO] + [g[1] for g in DRIP_GEO])
_pl = np.array(_POOL_FULL)
BODY_BOXES = {"torso": ((-1.0, TY[0], -0.6), (1.0, TY[-1], 3.0)),
              "chest": _box_ell(CHEST_C, CHEST_R), "neck": _box_cone(NECK_A, NECK_B, NECK_R[0]),
              "cranium": _box_ell(HEAD_C, HEAD_R), "snout": _box_ell(SNOUT_C, SNOUT_R), "jaw": _box_ell(JAW_C, JAW_R),
              "brow": _box_ell(BROW_C, (0.24, 0.24, 0.24), True), "spike": _box_cone(SPIKE_A, SPIKE_B, SPIKE_R[0]),
              "leg": _box_cone(np.array([LEG_S, LEG_E, LEG_F]).min(0), np.array([LEG_S, LEG_E, LEG_F]).max(0), max(LEG_RAD)),
              "haunch": _box_ell(HAUNCH_C, HAUNCH_R, True),
              "tail": ((-0.5, _tl[0, 0] - 0.5, -0.5), (0.5, _tl[-1, 0] + 0.1, 1.0)),
              "drip": _box_cone(_dg.min(0), _dg.max(0), 0.1),
              "pool": ((float((_pl[:, 0] - _pl[:, 2]).min()), float((_pl[:, 1] - _pl[:, 3]).min()), -0.1),
                       (float((_pl[:, 0] + _pl[:, 2]).max()), float((_pl[:, 1] + _pl[:, 3]).max()), POOL_TOP + 0.02))}
GRID_LO = np.array([BODY_BOXES["pool"][0][0] - 0.06, BODY_BOXES["pool"][0][1] - 0.06, -0.04])
GRID_HI = np.array([BODY_BOXES["pool"][1][0] + 0.06, max(BODY_BOXES["pool"][1][1], _tl[-1, 0] + 0.1) + 0.06,
                    HEAD_C[2] + HEAD_R[2] + 0.25])


def build_body():
    g = SD.Grid(GRID_LO, GRID_HI, SDF_STEP, SDF_BAND)
    for nm, fn, k in BODY_OPS:
        b0, b1 = (np.asarray(b, float) for b in BODY_BOXES[nm])
        if nm == "torso":                             # Y chunks (memory); k = 0 min-unions overlapping margins idempotently
            for y0 in np.arange(b0[1], b1[1], 1.0):
                g.apply(fn, np.array([b0[0], y0, b0[2]]), np.array([b1[0], min(y0 + 1.0, b1[1]), b1[2]]), k)
        else:
            g.apply(fn, b0, b1, k)
    g.floor(0.0)
    return g


t_ = time.time()
grid_b = build_body()
HV_B, HF_B = SD.polygonise(grid_b)
HF_B = [list(map(int, f)) for f in HF_B]
HV_B, HF_B, crumbs_b = keep_islands(HV_B, HF_B, 0.02)
assert HV_B[:, 2].min() > -1e-6, "body high dips below the floor"
assert crumbs_b["kept"] == 1, ("the body must be one closed island", crumbs_b)
tmp = new_obj("dec_src", HV_B, HF_B)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF_B))
LV0, LF0 = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV0, LF0, specks_b = keep_islands(LV0, LF0, 0.01)
_below = LV0[:, 2] < 0.0
floor_snap = {"verts_snapped_to_floor": int(_below.sum()), "worst_dip": round(float(-LV0[:, 2].min()), 6) if _below.any() else 0.0}
LV0[:, 2] = np.maximum(LV0[:, 2], 0.0)
dev_raw = deviation(HV_B, HF_B, LV0, LF0)
assert np.all(np.array(dev_raw["bbox_low_over_high"]) > 0.97), dev_raw    # the hull-fold hazard (BUILD_CONTRACT)
LVh, LFh = mirror_halve(LV0, LF0)
report["sdf"] = {"body": {"step": grid_b.step, "band": grid_b.band, "grid": grid_b.n.tolist(), "ops": grid_b.ops,
                          "high_tris": len(HF_B), "crumbs": crumbs_b, "decimated_tris": tri_count_F(LF0), "specks": specks_b,
                          "floor_snap": floor_snap, "raw_low_vs_high": dev_raw, "half_tris": tri_count_F(LFh),
                          "seconds": round(time.time() - t_, 1)}}
print("SDF body", json.dumps(report["sdf"]["body"]))
del grid_b

# =========================================================================== 2. plates + regions on the +X half
t_ = time.time()


def seed_spacing(P):
    X, Y, z = P[:, 0], P[:, 1], P[:, 2]
    s = np.full(len(P), PLATE_S)
    s = np.where((Y < -1.0) & (z > 2.3), PLATE_S_NECK, s)
    s = np.where((Y < -1.65) & (z > 2.95), PLATE_S_HEAD, s)
    tl = Y > _tl[0, 0]
    s = np.where(tl, np.interp(Y, [_tl[0, 0], _tl[-1, 0]], list(PLATE_S_TAIL)), s)
    return s


_cand = LVh[LVh[:, 2] > 0.15]
SEEDS_H, SEED_SP = FP.poisson_seeds(_cand, seed_spacing(_cand), order_seed=PLATE_SEED, min_x_frac=PLATE_MINX)
SEEDS = np.vstack([SEEDS_H, SEEDS_H * MIRX])
NSEED = len(SEEDS_H)
_tone_cum = np.cumsum([w for _, w in PLATE_TONES])
SEED_TONE = [PLATE_TONES[int(np.searchsorted(_tone_cum, FP.hash01(i, 9.1) * _tone_cum[-1], side="right"))][0]
             for i in range(NSEED)]
_dark_ok = (SEEDS_H[:, 1] > PLATE_DARK_BOX[0][0]) & (SEEDS_H[:, 1] < PLATE_DARK_BOX[0][1]) & (SEEDS_H[:, 2] < PLATE_DARK_BOX[1])
SEED_TONE = [t if (t != "plate_c" or _dark_ok[i]) else "plate_a" for i, t in enumerate(SEED_TONE)]
if "plate_c" not in SEED_TONE:                               # keep the chip in use: the body seed with the highest hash
    _ic = max(np.nonzero(_dark_ok)[0], key=lambda i: FP.hash01(i, 9.1))
    SEED_TONE[_ic] = "plate_c"
_mhw = np.array(MOLT_HW, float); _myf = np.array(MOLT_YF, float); _mth = np.array(MOUTH_LINE, float)
_mmid = np.array(MOLT_MID, float); _mcore = np.array(MOLT_CORE, float)


def body_groups(P):
    head = np.minimum.reduce([sd_cranium(P), sd_snout(P), sd_jaw(P), sd_brow(P), sd_spike(P)])
    return {"torso": np.minimum(sd_torso(P), sd_chest(P)), "neck": sd_neck(P), "head": head, "leg": sd_leg(P),
            "haunch": sd_haunch(P), "tail": sd_tail(P), "drip": sd_drips(P), "pool": sd_pool(P)}


def skirt_top(P, own):
    """the melting line: high on the body sides (the lower torso is lava round the legs, SIDE view), low on the front legs
    + haunches (rock limbs standing in the lava), a cos wave of lava tongues"""
    m_leg = smoothstep(-0.05, 0.05, own["leg"]); m_h = smoothstep(-0.05, 0.05, own["haunch"])
    base = SKIRT_Z_BODY + (SKIRT_Z_LEG - SKIRT_Z_BODY) * m_leg + (SKIRT_Z_HAUNCH - SKIRT_Z_BODY) * m_h
    return base + SKIRT_A * np.cos(TAU * (P[:, 1] + 0.8 * np.abs(P[:, 0])) / SKIRT_L)


def body_fields(P):
    G = body_groups(P)
    own = own_fields(G)
    X, Y, z = P[:, 0], P[:, 1], P[:, 2]
    tongue = MOLT_TONGUE[0] * np.cos(TAU * z / MOLT_TONGUE[1])           # flame-edged panel (clean wavy edge)
    hw = np.interp(z, _mhw[:, 0], _mhw[:, 1]) + tongue
    yf = np.interp(z, _myf[:, 0], _myf[:, 1])
    hmid = np.interp(z, _mmid[:, 0], _mmid[:, 1], left=-1.0) + 0.6 * tongue
    hcore = np.interp(z, _mcore[:, 0], _mcore[:, 1], left=-1.0) + 0.4 * tongue
    head_parts = {"brow": sd_brow(P), "rest": np.minimum.reduce([sd_cranium(P), sd_snout(P), sd_jaw(P), sd_spike(P)])}
    own_h = own_fields(head_parts)
    cell, h = FP.plate_cells(P, SEEDS)
    return {"own_head": own["head"], "own_tail": own["tail"], "own_drip": own["drip"], "own_leg": own["leg"],
            "own_brow": np.minimum(own_h["brow"], own["head"] + 0.02),
            "front": np.minimum(hw - np.abs(X), yf - Y), "mid": hmid - np.abs(X), "core": hcore - np.abs(X),
            "mouth": np.interp(Y, _mth[:, 0], _mth[:, 1]) - z,
            "tailu": z - (tail_cz(Y) + TAIL_UNDER), "skirt": z - skirt_top(P, own), "pool": z - POOL_CUT,
            "crk": h - CRACK_W, "cell": cell.astype(float)}


TOL = 0.01
_rock_gate = [("skirt", ">", -TOL), ("own_drip", "<", TOL), ("own_brow", "<", TOL)]
_panel_gate = [("own_head", "<", TOL), ("own_leg", "<", TOL), ("own_tail", "<", TOL)]
CV_h, CF_h, FV_h, FN_h, log_b = cut_island(
    LVh, LFh, body_fields,
    [("pool", 0.0, None), ("skirt", 0.0, [("own_tail", "<", TOL)]), ("own_drip", 0.0, None),
     ("own_tail", 0.0, None), ("tailu", 0.0, [("own_tail", ">", -TOL)]),
     ("front", 0.0, _panel_gate), ("mid", 0.0, _panel_gate + [("front", ">", -TOL)]),
     ("core", 0.0, _panel_gate + [("front", ">", -TOL)]),
     ("mouth", 0.0, [("own_head", ">", -TOL)]), ("own_brow", 0.0, None),
     ("crk", 0.0, _rock_gate)],
    split_seeds=SEEDS)
NH = len(CF_h)


def rock_weight(f, P):
    """1 on the raised rock plates, 0 on every glowing / decal-carrying area (smooth so the slabs end cleanly)"""
    Q = P.copy(); Q[:, 0] = np.abs(Q[:, 0])
    eye_d = np.linalg.norm(Q - np.array(EYE_MASK[0]), axis=1)
    nose_d = np.linalg.norm(Q - np.array(NOSE_MASK[0]), axis=1)
    return (smoothstep(0.0, 0.08, f["skirt"]) * smoothstep(0.0, 0.05, -f["front"] + (f["own_head"] > 0) * 9.0)
            * smoothstep(0.0, 0.04, -f["own_drip"]) * smoothstep(0.0, 0.04, -f["own_brow"])
            * np.where(f["own_head"] > 0, smoothstep(0.0, 0.07, -f["mouth"]), 1.0)
            * np.where(f["own_tail"] > 0, smoothstep(0.0, 0.06, f["tailu"])
                       * np.clip(np.interp(P[:, 1], _tl[:, 0], _tl[:, 2]) / TAIL_SLAB_R, 0.0, 1.0) ** 2, 1.0)
            * smoothstep(EYE_MASK[1], EYE_MASK[1] + 0.08, eye_d) * smoothstep(NOSE_MASK[1], NOSE_MASK[1] + 0.06, nose_d))


_RELIEF = PLATE_RELIEF * (2.0 * np.array([FP.hash01(c, 4.4) for c in range(NSEED)]) - 1.0)


def slab_offset(P):
    """RAISED STONE SLABS (in the LOW and the HIGH alike): each plate a domed / bevelled slab standing PLATE_PROUD (scaled by
    its plate size) over the crack channel, rising with an ease-out over PLATE_DOME x its spacing from the crack edge; the
    crack band itself sinks CRACK_CHANNEL at its centre line. -> (offset along the outward normal, rock weight)"""
    f = body_fields(P)
    cell = np.rint(f["cell"]).astype(int) % NSEED
    h = f["crk"] + CRACK_W
    sp = SEED_SP[cell]
    proud = PLATE_PROUD * np.clip(sp / PLATE_S, 0.4, 1.0)
    e = np.clip((h - CRACK_W) / (PLATE_DOME * sp), 0.0, 1.0)
    ease = 1.0 - (1.0 - e) ** 2
    off = np.where(h < CRACK_W, -CRACK_CHANNEL * (1.0 - h / CRACK_W), (proud + _RELIEF[cell]) * ease)
    w = rock_weight(f, P)
    return off * w, w


def sdf_normals(P):
    n = np.zeros_like(P)
    for j_, ax in enumerate(np.eye(3)):
        n[:, j_] = sd_body(P + 1e-4 * ax) - sd_body(P - 1e-4 * ax)
    return n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]


_off_h, _w_h = slab_offset(CV_h)
CV_h = CV_h + sdf_normals(CV_h) * _off_h[:, None]
CV_h[:, 2] = np.maximum(CV_h[:, 2], 0.0)
CV_B, CF_B, SEAM_N = mirror_weld(CV_h, CF_h)
FV_B = {k: np.concatenate([v, v]) for k, v in FV_h.items()}
FN_B = np.vstack([FN_h, FN_h * MIRX])


def assign_regions(FV):
    cell = np.rint(FV["cell"]).astype(int) % NSEED
    reg = np.array([SEED_TONE[c] for c in cell], dtype=object)
    reg[FV["crk"] < 0] = "crack"
    reg[FV["own_brow"] > 0] = "brow"
    head = FV["own_head"] > -TOL
    reg[head & (FV["mouth"] > 0)] = "molten"
    reg[(FV["own_tail"] > 0) & (FV["tailu"] < 0)] = "molten"
    reg[(FV["skirt"] < 0) & (FV["own_tail"] < 0)] = "lava"
    panel = (FV["front"] > 0) & ~head & (FV["own_leg"] < 0) & (FV["own_tail"] < 0)
    reg[panel] = "molten"
    reg[panel & (FV["mid"] > 0)] = "molten_mid"
    reg[panel & (FV["core"] > 0)] = "molten_core"
    reg[FV["own_drip"] > 0] = "drip"
    reg[FV["pool"] < 0] = "pool"
    return reg


reg_b = assign_regions(FV_B)
report["plates"] = {"seeds_half": NSEED, "seeds_total": len(SEEDS), "spacing": {"body": PLATE_S, "neck": PLATE_S_NECK,
                    "head": PLATE_S_HEAD, "tail": list(PLATE_S_TAIL)}, "tones": {t: SEED_TONE.count(t) for t, _ in PLATE_TONES},
                    "crack_half_width": CRACK_W, "proud": PLATE_PROUD, "dome": PLATE_DOME, "channel": CRACK_CHANNEL,
                    "low_offset": {"max_out": round(float(_off_h.max()), 4), "max_in": round(float(-_off_h.min()), 4),
                                   "p90_out_on_rock": round(float(np.percentile(_off_h[_w_h > 0.99], 90)), 4)
                                   if np.any(_w_h > 0.99) else 0.0},
                    "rule": "Voronoi plates of Poisson-disc seeds on the +X half + their mirrors; every Voronoi bisector is "
                            "split into the shell exactly (pair-linear), then the crack band h < CRACK_W is iso-cut; each "
                            "plate is displaced into a raised domed slab in the LOW and the HIGH alike"}
report["iso_cuts"] = {"body": {"cuts": log_b, "half_tris_after": NH, "tris_after": len(CF_B), "seam_verts": SEAM_N,
                               "seconds": round(time.time() - t_, 1)}}
print("CUTS", json.dumps(report["iso_cuts"]["body"]["cuts"]), len(CF_B), "seeds", NSEED, json.dumps(report["plates"]["low_offset"]))

# ---- the HIGH (bake source): the same raised slabs (so the normal bake agrees with the low's slab geometry)
t_ = time.time()
_off_g, _w_g = slab_offset(HV_B)
HV_G = HV_B + sdf_normals(HV_B) * _off_g[:, None]
HV_G[:, 2] = np.maximum(HV_G[:, 2], 0.0)
report["bevel"] = {"rule": "the HIGH carries the same slab displacement as the LOW", "max_offset": round(float(np.abs(_off_g).max()), 4),
                   "rock_fraction_of_high_verts": round(float((_w_g > 0.5).mean()), 4), "seconds": round(time.time() - t_, 1)}
dev_low = deviation(HV_G, HF_B, CV_B, CF_B)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic) -> X >= 0 half "
                              "-> cuts -> slab displacement -> mirror + weld (deviation vs the equally displaced high)" % SDF_STEP,
                    **dev_low}
assert np.all(np.array(dev_low["bbox_low_over_high"]) > 0.97), dev_low
print("RETOPO", json.dumps(report["retopo"]))
report["sdf"]["seconds"] = round(time.time() - t_sdf, 1)

# =========================================================================== 3. closed parts
PARTS = []
PARTS.append(FP.Part("body", CV_B, CF_B, list(reg_b)))
PARTS[-1].body = True


def face_frame_side(n_):
    """(e_u forward-ish, e_v up) on a side-facing surface (n ~ +X)"""
    e_v = unit(np.array([0.0, 0.0, 1.0]) - n_ * n_[2])
    e_u = unit(np.cross(n_, e_v))                   # n x up = forward (-Y) for n = +X
    return e_u, e_v


FACE_INFO = {}
_eo, _en = cast(sd_body, (1.6, EYE_Y, EYE_Z), (-1.0, 0.0, 0.0))
e_u, e_v = face_frame_side(_en)
_eye_parts = []
V, F, R = FP.plate_frame(np.array(EYE_SOCKET), _eo, e_u, e_v, _en, EYE_PROUD[0], 0.06, 0.004, "eye_socket")
_eye_parts.append(FP.Part("eye_socket.L", V, F, R, bone="head"))
V, F, R = FP.plate_frame(FP.circle(IRIS_R, 16) + np.array([0.01, 0.0]), _eo, e_u, e_v, _en, EYE_PROUD[1], 0.05, 0.006, "eye")
_eye_parts.append(FP.Part("eye.L", V, F, R, bone="head"))
V, F, R = FP.plate_frame(FP.circle(1.0, 12, PUPIL_R[0], PUPIL_R[1]) + np.array([0.01, 0.0]), _eo, e_u, e_v, _en,
                         EYE_PROUD[2], 0.05, 0.004, "pupil")
_eye_parts.append(FP.Part("pupil.L", V, F, R, bone="head"))
V, F, R = FP.plate_frame(FP.circle(GLINT[2], 8) + np.array(GLINT[:2]), _eo, e_u, e_v, _en, EYE_PROUD[3], 0.05, 0.003, "glint")
_eye_parts.append(FP.Part("glint.L", V, F, R, bone="head"))
FACE_INFO["eye.L"] = {"centre": _eo.round(4).tolist(), "normal": _en.round(3).tolist()}
_no, _nn = cast(sd_body, (NOSE_X, -4.5, NOSE_Z), (0.0, 1.0, 0.0))
_nu = unit(np.cross([0.0, 0.0, 1.0], _nn)); _nv = unit(np.cross(_nn, _nu))
V, F, R = FP.plate_frame(FP.circle(NOSE_R, 8), _no, _nu, _nv, _nn, 0.012, 0.04, 0.006, "face_line")
_eye_parts.append(FP.Part("nostril.L", V, F, R, bone="head"))
FACE_INFO["nostril.L"] = {"centre": _no.round(4).tolist()}
_mp, _mn = [], []
p0_, n0_ = cast(sd_body, (0.0, -4.5, float(np.interp(-3.2, _mth[:, 0], _mth[:, 1]))), (0.0, 1.0, 0.0))
_mp.append(p0_); _mn.append(n0_)
for y_ in np.linspace(-2.95, MOUTH_Y1 - 0.25, 14):
    p_, n_ = cast(sd_body, (1.6, y_, float(np.interp(y_, _mth[:, 0], _mth[:, 1]))), (-1.0, 0.0, 0.0))
    _mp.append(p_); _mn.append(n_)
V, F, R = FP.ribbon(_mp, _mn, MOUTH_W, 0.010, 0.04, "face_line")
_eye_parts.append(FP.Part("mouth.L", V, F, R, bone="head"))
FACE_INFO["mouth.L"] = {"ends": [np.round(_mp[0], 4).tolist(), np.round(_mp[-1], 4).tolist()]}
for p_ in _eye_parts:
    PARTS.append(p_)
for p_ in _eye_parts:
    PARTS.append(FP.mirror_part(p_, p_.name[:-2] + ".R"))

# flame crest tongues (opaque emissive, own material)
CREST_INFO = []
_crest_L = []
for i, (reg, x_, y_, z_, tilt, taz, L_, r_, flat, wave, ph, lob) in enumerate(CREST):
    t_r, a_r = math.radians(tilt), math.radians(taz)
    d = np.array([math.sin(t_r) * math.cos(a_r), math.sin(t_r) * math.sin(a_r), math.cos(t_r)])
    if x_ == 0.0:
        assert abs(d[0]) < 1e-9 and lob == 0, ("a centre crest tongue must lie in the mirror plane", i)
    V, F, R, sp = FP.tube_tongue((x_, y_, z_), d, (0.0, 1.0, 0.0), L_, r_, CREST_NS, CREST_SEG, wave, ph, reg, flat=flat,
                                 wide_s=0.25, tip_pow=CREST_TIP_POW, lobes=lob, lobe_a=0.10)
    p = FP.Part("crest.%d" % i + (".L" if x_ > 0 else ""), V, F, R, bone="crest")
    p.crest = True
    PARTS.append(p)
    if x_ > 0:
        _crest_L.append(p)
    CREST_INFO.append({"layer": reg, "x": x_, "tip": sp[-1].round(3).tolist(), "length": L_, "radius": r_})
for p_ in _crest_L:
    q_ = FP.mirror_part(p_, p_.name[:-2] + ".R")
    q_.crest = True
    PARTS.append(q_)

# =========================================================================== 4. assemble + centre
ISL = [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
assert lo0[2] > -1e-4, "something dips below the floor: min z %.5f" % lo0[2]
SHIFT = np.array([0.0, (lo0[1] + hi0[1]) / 2, lo0[2]])          # X: the build is symmetric about X = 0 already
assert abs(lo0[0] + hi0[0]) < 1e-6, ("not centred on X", lo0[0], hi0[0])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, MATI, RANGE = [], [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    MATI += [1 if getattr(p, "crest", False) else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
assert abs(VALL[:, 2].min()) < 1e-9 and VALL[RANGE["body"][0]:RANGE["body"][1], 2].min() < 1e-9, "the pool must sit on the floor"
assert VALL[:, 2].min() >= -1e-9, "nothing dips below the floor"
REG = ["plate_a", "plate_b", "plate_c", "brow", "crack", "molten", "molten_mid", "molten_core", "lava", "drip", "pool",
       "eye_socket", "eye",
       "pupil", "glint", "face_line", "crest_outer", "crest_inner"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
CREST_REG = {"crest_outer", "crest_inner"}
assert all((RNAME[i] in CREST_REG) == (MATI[i] == 1) for i in range(len(RNAME))), "crest region off the crest material"


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    FCc[:, 0] = np.abs(FCc[:, 0])                     # mirror-symmetric shade jitter
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
    mat.use_backface_culling = True                      # opaque closed solids: single-sided (glTF doubleSided false)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


GLOW_REGIONS = {"crack", "molten", "molten_mid", "molten_core", "lava", "drip", "pool", "eye", "crest_outer", "crest_inner"}
GLOW_ORDER = ["lava", "crack", "drip", "pool", "molten", "molten_mid", "molten_core", "eye", "crest_outer", "crest_inner"]


def glow_check(pal):
    """the sheet draws glow on the magma cracks, the molten chest / jaw / tail underside, the melting base, the drips, the
    pool, the eyes and the crest only; tiers lava < crack < drip < pool < molten < molten_mid < molten_core < eye <
    crest_outer < crest_inner; peak under the cap"""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    peak = {n: max(pal["regions"][n]["emission"]) / 255.0 * tier[n] * pal["material"].get("emission_strength", 1.0)
            for n in tier}
    ok_set = set(tier) == GLOW_REGIONS
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "peak_x_strength": {k: round(v, 4) for k, v in peak.items()},
            "cap": GLOW_CAP, "only_drawn_glow": ok_set,
            "grade_pass": ok_set and all(tier[a] < tier[b] for a, b in zip(GLOW_ORDER[:-1], GLOW_ORDER[1:])),
            "cap_pass": all(v <= GLOW_CAP for v in peak.values())}


MAT_BODY = make_mat(UNIT + "_body")
MAT_CREST = make_mat(UNIT + "_crest")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), ("palette regions != mesh regions", sorted(set(pal_default["regions"]) ^ set(REG)))
report["glow"] = {"default": glow_check(pal_default)}
_gc = report["glow"]["default"]
assert _gc["only_drawn_glow"] and _gc["grade_pass"] and _gc["cap_pass"], _gc

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_CREST)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
shade = jitter(VALL, FALL)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre at snout height -> the snout tip (straight -Y)
anchor = np.array([0.0, HEAD_C[1], SNOUT_C[2]]) - SHIFT
landmark = cast(sd_body, (0.0, -4.5, SNOUT_C[2]), (0.0, 1.0, 0.0))[0] - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre at snout height -> the snout tip on the mirror plane (the face looks -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

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

# ---- mirror test (house rule): every rest vertex mirrored across X = 0 -> distance to the nearest point of the mesh
_bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mir = np.array([_bvh_all.find_nearest(Vector((-p[0], p[1], p[2])))[3] for p in VALL])
report["mirror_test"] = {"max_abs_asymmetry": round(float(_mir.max()), 7), "p99": round(float(np.percentile(_mir, 99)), 7),
                         "per_piece_max": {k_: round(float(_mir[v_[0]:v_[1]].max()), 6) for k_, v_ in RANGE.items()
                                           if float(_mir[v_[0]:v_[1]].max()) > 1e-5},
                         "rule": "every rest vertex mirrored across X = 0 -> distance to the nearest point of the mesh"}
_rmir = {}
_fc_all = np.array([VALL[f].mean(0) for f in FALL])
_bvh_fc = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mis = 0
for i_f in range(0, len(FALL), 7):
    c_ = _fc_all[i_f]
    loc, nrm, j_f, dist = _bvh_fc.find_nearest(Vector((-c_[0], c_[1], c_[2])))
    if j_f is not None and RNAME[j_f] != RNAME[i_f]:
        _mis += 1
report["mirror_test"]["region_mismatch_sampled"] = {"faces_sampled": len(range(0, len(FALL), 7)), "mismatched": _mis}
print("MIRROR", json.dumps(report["mirror_test"]))

tris_main = tri_count_F(FALL)
_pp = {}
for p in PARTS:
    key = p.name.split(".")[0]
    _pp[key] = _pp.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "per_part_family": _pp,
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The SDF body (shell + pool, refined along the plate "
                            "cracks + every region cut) carries the largest share; the flame crest tongues and the face "
                            "decals the rest." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]), "parts": len(open_e),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every part is closed: the SDF body (mirror-welded), plates, ribbons, crest tongues"}
assert not report["open_edges"]["open_parts"], report["open_edges"]
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_crest_top_z": {"front": 4.55, "side": 4.52},
                     "sheet_width_units": {"front_body": 2.42, "front_pool_min": 3.0},
                     "sheet_depth_note": "the sheet's side view is 6.28 u wide with the tail curled; straight back the "
                                         "tail adds ~2.4 u of depth (house rule)",
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
low["conquest_source"] = "from scratch: design/reference/lava_komodo-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = "none: every material opaque (the crest is an opaque emissive mesh on its own material)"
_cr = np.vstack([p.V for p in PARTS if getattr(p, "crest", False)])
_head = np.array([[-0.75, -3.35, 2.80], [0.75, -1.30, 4.65]])
_melt = np.array([[-1.75, -3.15, 0.0], [0.30, 0.40, 1.50]])
_tailb = np.array([[-0.85, 2.10, 0.0], [0.85, _tl[-1, 0] + 0.1, 1.10]])
low["conquest_focus"] = json.dumps({
    "head": [(_head[0] - SHIFT).tolist(), (_head[1] - SHIFT).tolist()],
    "crest": [(_cr.min(0) - SHIFT - 0.05).tolist(), (_cr.max(0) - SHIFT + 0.05).tolist()],
    "melt": [(_melt[0] - SHIFT).tolist(), (_melt[1] - SHIFT).tolist()],
    "tail": [(_tailb[0] - SHIFT).tolist(), (_tailb[1] - SHIFT).tolist()],
    "views": {"head": [80.0, 6.0], "crest": [60.0, 14.0], "melt": [35.0, 12.0], "tail": [125.0, 28.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "plates", "tris", "measure", "facing",
                                                            "open_edges", "mirror_test", "regions_area_share", "glow",
                                                            "bevel")}, default=str))
    print("FACE", json.dumps(FACE_INFO))
    print("CREST", json.dumps(CREST_INFO))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake normal + AO (high = grooved SDF high + closed parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [], []
o_ = 0


def add_high(Vh, Fh):
    global o_
    PH_V.append(Vh - SHIFT); PH_F.extend([[i + o_ for i in f] for f in Fh]); o_ += len(Vh)


add_high(HV_G, HF_B)
for p in PARTS:
    if getattr(p, "body", False) or getattr(p, "crest", False):
        continue                                     # the body low is replaced by its grooved high; the crest never occludes
    add_high(p.V, p.F)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
BAKE_CAGE = max(0.05, round(3.0 * max(report["retopo"]["high_to_low_sampled"]["p99"], report["retopo"]["low_to_high"]["p99"])
                            + 0.01, 4))
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
BAKE_NODES = {}
for mat_ in (MAT_BODY, MAT_CREST):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
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
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, 16)):
    for mat_ in (MAT_BODY, MAT_CREST):
        mat_.node_tree.nodes.active = BAKE_NODES[mat_.name][idx]
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
body_faces = np.array(MATI) == 0
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the body's worst retopo p99 (vs the slab-displaced high) + 0.01 (floor 0.05)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "the grooved body SDF high + every closed part except the crest tongues",
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
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k != "pixel_sha"}))
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
nt = MAT_BODY.node_tree
tn, ta = BAKE_NODES[MAT_BODY.name]
for n_ in BAKE_NODES[MAT_CREST.name]:
    MAT_CREST.node_tree.nodes.remove(n_)
bsdf = nt.nodes["Principled BSDF"]
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(nt.nodes["col"].outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
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
                     "provenance": "pixel-sampled from the sheet's six palette chips (intake swatches) + drawn-surface "
                                   "samples (see the palette notes)"}
report["crest"] = {"tongues": CREST_INFO}
report["face"] = FACE_INFO
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


SJ = [np.array(p, float) for p in SPINE_J]
BONES = [("hips", P_(SJ[0]), P_(SJ[1]), "root"), ("spine.001", P_(SJ[1]), P_(SJ[2]), "hips"),
         ("spine.002", P_(SJ[2]), P_(SJ[3]), "spine.001"), ("spine.003", P_(SJ[3]), P_(SJ[4]), "spine.002"),
         ("neck", P_(SJ[4]), P_(NECK_TOP), "spine.003"), ("head", P_(NECK_TOP), P_(HEAD_TIP), "neck"),
         ("crest", P_((0.0, -1.90, 3.75)), P_((0.0, -1.85, 4.50)), "head")]
for side, sg in (("L", 1.0), ("R", -1.0)):
    m_ = np.array([sg, 1.0, 1.0])
    BONES += [("leg_%s.upper" % side, P_(np.array(LEG_S) * m_), P_(np.array(LEG_E) * m_), "spine.003"),
              ("leg_%s.lower" % side, P_(np.array(LEG_E) * m_), P_(np.array(LEG_F) * m_), "leg_%s.upper" % side)]
_tj = [(0.0, y_, float(tail_cz(np.array([y_]))[0])) for y_ in TAIL_J]
for i in range(4):
    BONES.append(("tail.%03d" % (i + 1), P_(_tj[i]), P_(_tj[i + 1]), "hips" if i == 0 else "tail.%03d" % i))
CONNECT = {"spine.001", "spine.002", "spine.003", "neck", "head", "leg_L.lower", "leg_R.lower", "tail.002", "tail.003", "tail.004"}
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
a_, b_ = RANGE["body"]
Vw = VALL[a_:b_] + SHIFT
X_, Y_, zz = Vw[:, 0], Vw[:, 1], Vw[:, 2]
G = body_groups(Vw)
G.pop("drip")                                            # the drips ride whatever they hang from
own = own_fields(G)
mb = lambda k: smoothstep(-MEMBER_BLEND, MEMBER_BLEND, own[k])
m_head, m_neck, m_leg, m_tail, m_pool = mb("head"), mb("neck"), mb("leg"), mb("tail"), mb("pool")
m_rest = np.clip(1.0 - m_head - m_neck - m_leg - m_tail - m_pool, 0.0, 1.0)
s1 = smoothstep(SPINE_SPLIT[0] - SPINE_JW, SPINE_SPLIT[0] + SPINE_JW, -Y_ + 2 * SPINE_SPLIT[0])   # 1 in front of the split
s2 = smoothstep(SPINE_SPLIT[1] - SPINE_JW, SPINE_SPLIT[1] + SPINE_JW, -Y_ + 2 * SPINE_SPLIT[1])
s3 = smoothstep(SPINE_SPLIT[2] - SPINE_JW, SPINE_SPLIT[2] + SPINE_JW, -Y_ + 2 * SPINE_SPLIT[2])
Wb = np.zeros((len(Vw), len(DEFORM)))
Wb[:, J["hips"]] += m_rest * (1.0 - s1) + m_pool
Wb[:, J["spine.001"]] += m_rest * (s1 - s2)
Wb[:, J["spine.002"]] += m_rest * (s2 - s3)
Wb[:, J["spine.003"]] += m_rest * s3
Wb[:, J["neck"]] += m_neck
Wb[:, J["head"]] += m_head
el = smoothstep(LEG_E[2] - LEG_JW, LEG_E[2] + LEG_JW, zz)
for s, sg in (("L", 1.0), ("R", -1.0)):
    on = (X_ * sg > 0).astype(float) + (np.abs(X_) < 1e-9) * 0.5
    Wb[:, J["leg_%s.upper" % s]] += m_leg * on * el
    Wb[:, J["leg_%s.lower" % s]] += m_leg * on * (1.0 - el)
t_s = [smoothstep(TAIL_J[k] - TAIL_JW, TAIL_J[k] + TAIL_JW, Y_) for k in range(1, 4)]
Wb[:, J["tail.001"]] += m_tail * (1.0 - t_s[0])
Wb[:, J["tail.002"]] += m_tail * (t_s[0] - t_s[1])
Wb[:, J["tail.003"]] += m_tail * (t_s[1] - t_s[2])
Wb[:, J["tail.004"]] += m_tail * t_s[2]
anc = (1.0 - smoothstep(ANCHOR_Z[0], ANCHOR_Z[1], zz)) * (1.0 - m_tail)   # the melting base rides hips (never animated)
Wb = Wb * (1.0 - anc)[:, None]
Wb[:, J["hips"]] += anc
Wt[a_:b_] = Wb
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "body", False):
        continue
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
                  "rule": "body: membership by SDF ownership (head / neck / legs / tail / pool, +-%.2f blend), the rest hips | "
                          "spine.001 | .002 | .003 split along Y at %s; legs upper | lower at the elbow; tail.001-.004 along "
                          "Y; the body below z %s fades onto hips (the melting base + the pool never move); face decals "
                          "1.0 head; crest tongues 1.0 crest" % (MEMBER_BLEND, list(SPINE_SPLIT), list(ANCHOR_Z))}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """world-axis rotations (deg; in order, about the bone head, relative to the parent's posed frame) -> basis quat"""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
S = math.sin


def crest_pose(clip, u, extra=()):
    amp, (h1, h2, h3), mean = CREST_FLICK[clip]
    flick = 0.5 * S(TAU * h1 * u + 0.3) + 0.3 * S(TAU * h2 * u + 1.1) + 0.2 * S(TAU * h3 * u + 2.0)
    Lf = 1.0 + mean + amp * flick
    Wf = 1.0 - CREST_WIDTH_K * (Lf - 1.0)
    rots = list(extra) + [("X", CREST_WOBBLE * S(TAU * (h1 + 2) * u + 0.4)), ("Y", CREST_WOBBLE * S(TAU * (h2 + 2) * u + 1.7))]
    return rots, (Wf, Lf, Wf), Lf


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        br = 0.5 + 0.5 * S(TAU * 2 * u)                  # 0..1: the chest only ever rises (never toward the floor)
        P["spine.002"] = (None, [("X", IDLE_BREATH[0] * br)], None)
        P["spine.003"] = ((0.0, 0.0, IDLE_BREATH[1] * br), [], None)
        P["head"] = (None, [("X", IDLE_HEAD * S(TAU * 2 * u - 1.0))], None)
        for k in range(4):
            P["tail.%03d" % (k + 1)] = (None, [("Z", TAIL_IDLE * S(TAU * u - 0.5 * k))], None)
        rots, sc, Lf = crest_pose(clip, u)
    else:
        w = S(TAU * u)
        P["spine.001"] = (None, [("Z", WALK_SPINE * w)], None)
        P["spine.002"] = (None, [("Z", -WALK_SPINE * w)], None)
        P["spine.003"] = (None, [("Z", WALK_SPINE * w)], None)
        P["neck"] = (None, [("Z", -0.5 * WALK_SPINE * w)], None)
        P["head"] = (None, [("Z", -WALK_HEAD * w), ("X", 0.8 * S(TAU * 2 * u - 1.2))], None)
        reach = {"L": max(0.0, S(TAU * u)) ** 1.3, "R": max(0.0, S(TAU * u + math.pi)) ** 1.3}
        for s in ("L", "R"):
            a = WALK_REACH * reach[s]
            P["leg_%s.upper" % s] = (None, [("X", -a)], None)        # forward + up (the leg slopes forward)
            P["leg_%s.lower" % s] = (None, [("X", -0.5 * a)], None)
        for k in range(4):
            P["tail.%03d" % (k + 1)] = (None, [("Z", -WALK_TAIL * S(TAU * u - 0.6 * k))], None)
        rots, sc, Lf = crest_pose(clip, u, extra=[("X", WALK_CREST_TRAIL)])
    P["crest"] = (None, rots, sc)
    return P, {"crest_len": Lf}


NEW_ACTS = {}
key_rows = []
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1
        P, _ = pose_at(cn, f % N)
        for bn in DEFORM:
            loc, rots, sc = P.get(bn, (None, [], None))
            pb = rig.pose.bones[bn]
            pb.location = local_loc(bn, loc) if loc is not None else Vector((0.0, 0.0, 0.0))
            pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
            pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)
            for ch in ("location", "rotation_quaternion", "scale"):
                pb.keyframe_insert(ch, frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion) + list(pb.scale))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["loop_seconds"] = N / K.FPS
    NEW_ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


BV = np.arange(*RANGE["body"])
_vpool = np.zeros(len(VALL), bool)
for fi, f in enumerate(FALL):
    if RNAME[fi] == "pool":
        _vpool[f] = True
POOLV = np.nonzero(_vpool & (Wt[:, J["hips"]] > 0.999))[0]          # the pool that rides hips alone (not under the tail)
assert len(POOLV) > 50
samples, clip_rep = [], {}
for cn, N in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    first = last = None
    rows = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "root": float((rig.matrix_world @ pbs["root"].head).length),
                     "crest_len": float(pbs["crest"].scale[1]),
                     "pool_move": float(np.linalg.norm(C[POOLV] - VALL[POOLV], axis=1).max()),
                     "pool_minz": float(C[POOLV, 2].min())})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "pool_max_move": round(float(g_("pool_move").max()), 6), "pool_min_z": round(float(g_("pool_minz").min()), 6),
                    "crest_length_scale": [round(float(g_("crest_len").min()), 4), round(float(g_("crest_len").max()), 4)]}
    assert clip_rep[cn]["clearance_min_z"] > -1e-3, ("dips below the floor", cn, clip_rep[cn])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("lava_komodo v1: root > hips > spine.001 > .002 > .003 > neck > head > crest; spine.003 > "
                       "leg_{L,R}.upper > .lower; hips > tail.001 > .002 > .003 > .004")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given the Lava Komodo's movement or height; open "
                               "question): idle = 4 s breathing (the chest rises) + a crest flicker (scale pulse) + a tail "
                               "sway; walk = 2 s in-place lizard crawl-shuffle (spine S-wave %.0f deg, alternating front-leg "
                               "reaches %.0f deg, tail sway %.0f deg); hips never move, so the pool + the melting base stay "
                               "on the floor" % (WALK_SPINE, WALK_REACH, WALK_TAIL))
low["conquest_locomotion"] = "in-place crawl-shuffle placeholder: hips static, the pool + melting base exactly on z 0"
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

# =========================================================================== 7. glb
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
              "structure": "armature identity + skinned 'lava_komodo' (opaque body primitive + opaque emissive crest "
                           "primitive); natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
