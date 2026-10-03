"""Fire Sprite / Demon -- the MODEL (from-scratch Conquest unit, no source sculpt), one headless run.

    blender --background --factory-startup --python improve/firesprite_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-09-26 "NEW UNIT: Fire Sprite / Demon" + design/reference/firesprite-character-sheet.webp
(three-view: ornamental dark-charcoal crown with two horns + diamond boss, flame rising through its open top; gnarled
angular FIRE WAND; body "pure hellfire, fluid / flowing, no solid form"; glowing eye holes; palette two charcoals, two
oranges, dark red) + design/reference/firesprite-sketch.webp (the artist's drawing: SQUARE one-piece head+torso block wearing
the crown, jagged hole eyes + zigzag hole mouth, ragged flame arms, two flame legs). ONE binding deviation from the sheet
(artist verbatim): "make the body more square shape so the head and torso blend as one piece like this other reference".

SCOPE (v1.2, the movement wave): the model + the real clips. Artist 2026-09-26: "movement is floaty steps" -> 'idle'
(grounded stance, living fire: licks / crown flame / wand flame flicker with stretch, body sway + breath, the free arm
drifting, feet planted) and 'walk' (in place: each step pushes off into a drift with hang time and settles softly on the
other foot). Flame-flow wildness is still artist-open: FLAME_WILD is the master knob. Casting = attack wave, deferred.
v1.2 body (artist 2026-09-26): the block funnels into the legs (HIP, CROTCH, LEG_K), arms wider + longer to the drawn
outline. v1.3 walk (artist 2026-09-26: "legs remains stiff more cartoon movement than realistic and faster like a little
creature hopping around"): the floaty steps are replaced by a STIFF-LEGGED CARTOON HOP (HOP_* knobs; knee locked, all
the give in the block's squash / stretch on the new 'hips' > 'body' split); the idle is unchanged.

UNITS: 'sheet units' -- 1 unit = 100 px of the reference sheet's FRONT view (orthographic), floor z = 0 at the sheet's
foot line (y 605 px): z = (605 - y_px) / 100; x = (x_px - 225) / 100 (the character axis). Natural proportions; cell fit
is report-only (scale policy 2026-09-25). Design frame: -Y is the front, +X the unit's LEFT (L), -X its right (R, wand hand).

Pipeline:
  1. BODY = one SDF (magmoo_sdf banded grid + smooth unions): the tapered rounded BLOCK (head + torso one piece), ragged
     flame LICKS (tapered bezier cone chains) up the sides, rising off the back and hanging off the hem, two flame ARMS
     (the R hand grips the wand; the L hand ends in claw licks), two flame LEGS with toe licks -- all smooth-unioned into
     one fire mass (limbs FUSED, no gaps) -- minus the two angled jagged EYE pockets and the zigzag MOUTH pocket carved
     into the flat front (2D polygon prisms), clamped flat on the floor. Marching tets -> collapse decimation.
  2. BODY REGIONS on the low shell (iso-cut, clean colour lines): pocket walls / floors (glowing holes), licks + limb tips
     (dark red), the face shadow (the sheet's dark face under the crown, wavy lower edge), bright flame tongues rising
     from the hem round the block, the rest orange.
  3. BODY CORE: a smaller hot block inside (the translucent shell shows it through: brighter interior tier).
  4. CROWN: solid SDF: flared band hugging the block's top edge (open on top), trim ridges on both edges, a bevelled
     diamond boss front AND back with a raised inner diamond, two crescent horns. Regions by part ownership.
  5. CROWN FLAME: multi-tongue SDF flame rising through the open crown (outer + nested hot core), tips cut by arc.
  6. WAND (own object 'firesprite_wand', own bones): kinked angular shaft (straight cone runs, hard joints), broken twig
     nubs, a claw of prongs cradling the WAND FLAME (outer + core, same flame language).
  7. palette (palettes.py regions -> Col / Glow + Col alpha), Smart UV, bake normal + AO (wired into the crown only).
  8. RIG: root > hips (non-deform) > body > crown > crown_flame.{0,1}; body > arm.{L,R}.{0,1,2}; arm.R.2 > wand >
     wand_flame; hips > leg.{L,R}.{0,1,2} (2 = the foot; never scaled); body > one bone per body lick (lick.side / back /
     hem). Clips idle (leg IK) + walk (stiff-leg hop, closed-form legs), integer harmonics, floor guard. glb + skin.
"""
import bpy, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K            # noqa: E402  (read-only use)
import palettes as PAL        # noqa: E402  (read-only use)
import magmoo_sdf as SD       # noqa: E402  (read-only use: the SDF kit)
import firesprite_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, floor z = 0 at the foot line)
UNIT = "firesprite"
CHAR_ID = "firesprite"                # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit (see report 'tier_rationale')
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
NODE_EPS = 0.02                       # SDF grid values closer than this x step to zero are pushed off (no node crossings)
                                      #   v1.2: 0.10 -> 0.02 (still 0 zero-area tris, measured; half the body retopo p99)
# ---- body block (the sketch's square one-piece head + torso)
BLOCK_HALF = (0.58, 0.40)             # "body width / depth" (half; sheet face 1.1 wide, band 1.25)
BLOCK_Z = (0.95, 2.98)                # "body bottom / top" (the hips / the crown seat)
BLOCK_TAPER = 0.90                    # "hem narrowing": hem half-width as a fraction of the top's
BLOCK_ROUND = 0.15                    # "corner rounding" of the block
BODY_STEP, BODY_BAND = 0.016, 0.30    # "body surface finish": SDF step, far-field band. v1.2: 0.06 -> 0.30 -- the band
                                      #   must cover the largest smooth-union k (LEG_K): a band below k clips the leg's
                                      #   field inside the blend and the slanted hip funnel came out as a grid-aligned
                                      #   STAIRCASE (measured: alternating (0,-1,0) / (0,0,-1) faces, 16 mm treads)
BODY_TRIS = 7200                      # "body mesh detail": decimation target of the fire shell
# ---- flame licks on the block: sides (per side: z, y, reach out, rise, root radius), back (x, root z, tip dx, tip z, r),
#      hem (azimuth deg from the front, hang length, root radius)
SIDE_LICKS = [(1.16, -0.10, 0.30, 0.48, 0.14), (1.50, 0.14, 0.28, 0.54, 0.13), (2.28, -0.08, 0.26, 0.50, 0.12),
              (2.60, 0.14, 0.22, 0.44, 0.11)]
SIDE_R_DZ = 0.07                      # the right side's licks sit this much higher (no mirror twin look)
BACK_LICKS = [(-0.30, 2.15, -0.12, 3.25, 0.15), (0.04, 2.30, 0.06, 3.52, 0.17), (0.33, 2.10, 0.14, 3.18, 0.14)]
HEM_LICKS = [(70.0, 0.40, 0.13), (100.0, 0.44, 0.13), (130.0, 0.34, 0.12),
             (-135.0, 0.40, 0.13), (-105.0, 0.46, 0.13), (-72.0, 0.36, 0.12)]
                                      # v1.2: the front (+-40) and back (160, -165) hem licks dropped with the hip funnel
LICK_K = 0.05                         # licks melt into the block over this (smooth union)
LICK_DEEP_T = 0.52                    # "dark-red lick tips": the outer part of every lick past this fraction
LICK_SHARP = 1.4                      # "lick tip whip": lick radius falls as (1 - u)^this (flame tongue, not a thorn)
# ---- arms (R = wand hand, -X; L = free claw hand, +X)
# v1.1 (artist 2026-09-26: "longer arms that dont really have elbows just flames coming out of his torso"):
# both arms lengthened into smooth elbowless flame sweeps; the free hand drops far lower and further out
# v1.2 (artist 2026-09-26: "make the arms a little bit shorter now and a little bit fatter" + a red outline on the v1.1
# front render = the target silhouette: broad flame sweeps from the shoulders down past the hem to about shin height):
# free hand (1.38, -0.10, 0.68) -> (1.16, -0.08, 0.60) (less reach out, down to the shin), ctrl (1.12, 0.02, 1.78) ->
# (1.02, 0.02, 1.70); the wand hand drops down the staff with it (HAND_Z 1.62 -> 0.80); radii root 0.16 -> 0.21,
# tip 0.07/0.075 -> 0.095, hands 0.10/0.11 -> 0.13/0.14
ARM_R = {"shoulder": (-0.50, -0.02, 2.02), "ctrl": (-1.00, -0.16, 1.62), "r": (0.21, 0.095), "hand_r": 0.14}
ARM_L = {"shoulder": (0.50, 0.0, 2.02), "ctrl": (1.02, 0.02, 1.70), "hand": (1.16, -0.08, 0.60), "r": (0.21, 0.095),
         "hand_r": 0.13}
ARM_LICKS = [(0.25, (0.14, 0.05, 0.32), 0.09), (0.50, (0.16, 0.0, 0.30), 0.085), (0.75, (0.12, -0.04, 0.24), 0.07)]   # (arc frac, tip offset (out, y,
                                      #   up), root radius) -- the ragged flames trailing up off each arm
CLAW = [((0.10, -0.06, -0.30), 0.070), ((0.0, -0.11, -0.34), 0.072), ((-0.08, 0.02, -0.26), 0.060)]   # L hand claw licks
ARM_K = 0.10
ARM_DEEP_T = {"L": 0.84, "R": 9.0}    # the free hand darkens at its tip; the wand hand stays orange (it grips)
# ---- legs
# v1.1 (artist 2026-09-26: "skinnier and pointier legs"): root radius 0.29 -> 0.21, tip 0.05 -> 0.015, sharp 0.85 -> 1.0
LEG = {"hip": (0.29, 0.0, 1.22), "ctrl": (0.35, -0.03, 0.55), "tip": (0.31, -0.06, -0.03), "r": (0.21, 0.015), "sharp": 1.0}
TOES = [((0.33, -0.05, 0.28), (0.48, -0.15, 0.02), 0.06), ((0.25, 0.06, 0.32), (0.17, 0.20, 0.04), 0.05)]
# v1.2 (artist 2026-09-26: "make the torso blend better with the legs so it doesn't look like a rectangle on top of
# legs"): LEG_K 0.18 -> 0.30 (+ the legs melt in BEFORE the licks), a hip funnel, a crotch notch, the front / back hem
# licks dropped (they drew the hem line)
LEG_K = 0.30                          # "torso-leg blend": legs melt into the block over this (the sketch's block SPLITS
                                      #   into two legs); keep BODY_BAND >= LEG_K
LEG_DEEP_T = 0.70
HIP = (1.60, 0.46, 0.23)              # "hip funnel": (top z, half width, half depth at the block bottom) -- the block's
                                      #   lower part narrows (linear, one facet) into the leg roots, front AND side;
                                      #   None = straight (v1.1: half width 0.52, half depth 0.40 at the hem)
CROTCH = (1.45, 0.14, 0.05)           # "crotch notch": (apex z, half width at the block bottom, melt k) -- an inverted V
                                      #   cut up into the block between the legs (the sketch's block splitting into legs);
                                      #   None = no notch
# ---- face: glowing HOLES carved into the flat front (sketch: jagged eyes, zigzag mouth; sheet: angled eyes)
POCKET_DEPTH = 0.10                   # "hole depth"
EYE_L = [(0.08, 2.40), (0.40, 2.55), (0.42, 2.36), (0.36, 2.24), (0.31, 2.31), (0.24, 2.19), (0.19, 2.28), (0.12, 2.22),
         (0.08, 2.30)]                # "eye shape" (x, z) of the unit's LEFT eye: angry slant (inner corner low) + the
                                      #   sketch's jagged drips along the bottom; the right eye mirrors it
MOUTH_X, MOUTH_Z = 0.34, (1.74, 2.02)  # "mouth width (half) / bottom, top"
MOUTH_TEETH, MOUTH_TOOTH = (5, 4), 0.085   # "zigzag": teeth on the top / bottom edge, tooth depth
MOUTH_SAG = (0.06, 0.03)              # the mouth corners pinch in (top edge drops, bottom edge rises toward the ends)
# ---- colour zoning on the block
FACE_SHADOW = False                   # "dark face": the sheet's dark ember face under the crown (eyes + mouth inside it).
                                      #   OFF per artist 2026-09-26: "get rid of the mask and just have his face be the
                                      #   same as the body texture/color wise"
SH_Z0, SH_WAVE = 1.60, (0.07, 9.0, 0.6)   # its wavy lower edge (height, (amplitude, frequency, phase))
SH_THETA = 0.95                       # how far round the block it wraps (rad from the front centre)
TONGUES = [(0, 0.50, 9), (26, 0.66, 9), (-24, 0.60, 9), (52, 0.95, 11), (-55, 0.90, 11), (90, 1.45, 15),
           (-92, 1.38, 17), (125, 1.20, 15), (-128, 1.25, 15), (158, 1.50, 14), (-160, 1.35, 14), (180, 0.90, 12)]
                                      # "bright flame tongues" rising from the hem: (azimuth deg, height, half-width deg)
TONGUE_WOB = (0.10, 5.0)              # tongue wobble (rad amplitude, per-unit-height frequency)
SH_PEAKS = [(-0.47, 0.30, 0.07), (-0.22, 0.12, 0.05), (0.0, 0.10, 0.05), (0.24, 0.12, 0.05), (0.47, 0.26, 0.07)]
                                      # orange flame points licking UP into the dark face: (x, height, half-width)
SH_SIDE_WAVE = (0.12, 10.0)           # the face shadow's side edges wave too (rad amplitude, per-unit-height frequency)
POCKET_REC = 0.006                    # a hole face must sit this far behind the front plane (no rim-to-rim slivers)
# ---- hot core inside the block: a FLAME shape (base blob + rising tongues), seen through the translucent shell
CORE_BASE, CORE_BASE_R = (0.0, 0.0, 1.45), (0.36, 0.20, 0.34)
CORE_TONGUES = [([(0.0, 0.0, 0.10), (0.08, 0.0, 0.50), (-0.05, 0.0, 0.85), (0.02, 0.0, 1.10)], 0.20),
                ([(0.18, 0.0, 0.05), (0.30, 0.0, 0.35), (0.22, 0.0, 0.60), (0.30, 0.0, 0.80)], 0.13),
                ([(-0.18, 0.0, 0.05), (-0.30, 0.0, 0.32), (-0.22, 0.0, 0.55), (-0.30, 0.0, 0.74)], 0.13)]
CORE_STEP, CORE_TRIS = 0.02, 700
# ---- crown (solid, dark charcoal)
CROWN_Z = (2.62, 3.06)                # "band bottom / top"
CROWN_OFF, CROWN_T = 0.012, 0.07      # band gap off the block / thickness
CROWN_FLARE = 0.10                    # "band flare": outward lean per unit height
CROWN_TRIM = (0.045, 0.018)           # "edge trim" ridge height / extra thickness
BOSS = {"zc": 2.76, "a": 0.20, "b": 0.36, "proud": 0.075, "bevel": 0.35}   # "diamond boss" (half w, half h, stand-off)
GEM = {"scale": 0.50, "proud": 0.022, "bevel": 0.30}                       # the raised inner diamond
HORN = {"z": 2.95, "ctrl": (0.40, -0.02, 0.16), "tip": (0.16, -0.06, 0.80), "r": 0.13}   # "horns" (offsets from the root)
CROWN_STEP, CROWN_TRIS = 0.010, 2800
# ---- crown flame (rising through the open crown): base blob + tongues (root, ctrl, tip offsets from the base, radius)
CF_BASE, CF_BASE_R = (0.0, 0.02, 2.96), (0.46, 0.32, 0.24)
CF_TONGUES = [([(0.0, 0.0, 0.05), (0.10, -0.02, 0.45), (-0.06, 0.02, 0.85), (0.04, 0.04, 1.30)], 0.30),
              ([(0.20, 0.0, 0.02), (0.40, 0.0, 0.32), (0.34, 0.02, 0.60), (0.46, 0.03, 0.90)], 0.18),
              ([(-0.20, 0.0, 0.02), (-0.42, 0.02, 0.28), (-0.36, 0.04, 0.52), (-0.48, 0.04, 0.78)], 0.17),
              ([(0.0, 0.12, 0.02), (0.05, 0.30, 0.35), (-0.04, 0.36, 0.60), (0.02, 0.46, 0.85)], 0.16),
              ([(0.12, -0.12, 0.04), (0.26, -0.20, 0.28), (0.20, -0.18, 0.46), (0.28, -0.20, 0.62)], 0.11),
              ([(-0.14, -0.12, 0.04), (-0.30, -0.16, 0.24), (-0.22, -0.14, 0.40), (-0.30, -0.14, 0.54)], 0.10)]
                                      # "crown flame tongues": S-curve paths (offsets from the base) + root radius
FLAME_CORE = (0.55, 0.68, 3)          # "flame core": radius scale, length scale, tongues kept
FLAME_SHARP = 1.5                     # "flame tip whip": tongue radius falls as (1 - u)^this
FLAME_K = 0.10
FLAME_TIP_T = 0.60                    # "flame tips": the outer part of every tongue past this fraction (cooler red)
CF_STEP, CF_TRIS, CF_CORE_TRIS = 0.016, 1500, 450
# ---- wand (own object + bones): kinked shaft (t along the axis, offsets on the two perpendiculars, radius)
WAND_BOTTOM, WAND_TOP = (-0.72, -0.22, 0.10), (-1.20, -0.48, 3.02)   # sheet: butt near the floor, leaning out + forward
HAND_Z = 0.80                         # "grip height" (the R hand sits on the wand axis here; v1.1 1.62, v1.2 down the
                                      #   staff with the longer arm outline)
WAND_KINKS = [(0.00, 0.0, 0.0, 0.018), (0.05, 0.010, 0.0, 0.040), (0.17, -0.035, 0.020, 0.052),
              (0.30, 0.030, -0.025, 0.056), (0.44, -0.030, 0.015, 0.058), (0.58, 0.035, 0.020, 0.056),
              (0.72, -0.025, -0.030, 0.058), (0.85, 0.030, 0.010, 0.062), (0.95, -0.010, 0.0, 0.070), (1.0, 0.0, 0.0, 0.066)]
WAND_NUBS = [(0.26, 60.0, 0.16, 0.034), (0.50, 200.0, 0.14, 0.030), (0.68, 320.0, 0.18, 0.034)]   # broken twig stubs
WAND_PRONGS = [(20.0, 0.25, 0.46), (110.0, 0.22, 0.40), (200.0, 0.26, 0.48), (290.0, 0.21, 0.38)]   # the claw round the
                                      #   flame: (azimuth deg, spread, height)
WAND_PRONG_R = 0.045
WAND_STEP, WAND_TRIS = 0.008, 1400
WF_LIFT, WF_BASE_R = 0.22, (0.14, 0.12, 0.12)   # wand flame: base above the shaft top, base blob radii
WF_TONGUES = [([(0.0, 0.0, 0.04), (0.07, -0.02, 0.36), (-0.05, 0.0, 0.66), (0.02, 0.0, 0.98)], 0.17),
              ([(0.10, 0.0, 0.02), (0.22, 0.0, 0.22), (0.17, 0.02, 0.40), (0.25, 0.02, 0.58)], 0.10),
              ([(-0.10, 0.0, 0.02), (-0.22, 0.02, 0.20), (-0.16, 0.0, 0.34), (-0.24, 0.0, 0.50)], 0.10),
              ([(0.0, 0.09, 0.02), (0.03, 0.18, 0.22), (-0.02, 0.20, 0.38), (0.03, 0.24, 0.54)], 0.09)]
WF_STEP, WF_TRIS, WF_CORE_TRIS = 0.012, 700, 250
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig
ARM_BONES, LEG_BONES, CF_BONES = 3, 3, 2   # v1.2: legs 2 -> 3 bones -- the bottom one is the FOOT (kept flat while
                                      #   planted: a 2-bone flame leg tilts its toes into the floor whenever the knee bends)
OWN_TAU = 0.05                        # limb / block weight blend softness (SDF ownership)
LICK_BONES = True                     # one bone per body lick (side / back / hem) so the licks waver and trail
LICK_RAMP = (0.12, 0.65)              # a lick bone's weight ramps 0 -> 1 over this spine fraction (the root stays on body)
# ---- MOVEMENT (artist 2026-09-26: "movement is floaty steps"). Every clip is in place (root at the origin; Conquest
#      glides the unit); every periodic term is an integer harmonic of its loop -> exact seams.
# FLAME FLOW -- artist-open ("flame-flow wildness"); this lane's proposal: moderate constant upward flicker on the body
#      licks + crown / wand flames, streaming harder in the drift of each step. One master knob scales all of it:
FLAME_WILD = 1.0                      # "flame wildness": 0 = still, 1 = the proposed moderate flicker, 2 = wild
FLICKER_HZ = (1.75, 2.75)             # "flicker rates" (Hz; each loop snaps them to its nearest integer harmonics)
LICK_FLICKER = (7.0, 0.10)            # body licks: sway deg, stretch fraction (x FLAME_WILD)
CF_FLICKER = (4.0, 9.0, 0.08)         # crown flame: sway deg root / tip, stretch fraction (x FLAME_WILD)
WF_FLICKER = (6.0, 0.09)              # wand flame: sway deg, stretch fraction (x FLAME_WILD)
STREAM = {"lick": 16.0, "crown": 12.0, "wand": 12.0, "stretch": 0.10}   # walk: extra trail-back deg (and stretch)
                                      #   pulsed through the air of each hop (x FLAME_WILD)
# IDLE -- grounded stance, living fire
IDLE_N = 96                           # "idle loop": 4 s
IDLE_DROP, IDLE_BOB = 0.025, 0.015    # soft knees: the body sits this far below rest; breathing dips this much more (2 / loop)
IDLE_SWAY = (1.2, 0.8, 1.5)           # body roll / pitch / yaw deg
IDLE_SHIFT = 0.012                    # body side-shift over the planted feet
IDLE_ARM = {"L": (2.5, 7.0), "R": (0.5, 1.0)}   # arm drift root / tip deg (the free flame arm drifts; the wand arm is still)
KNEE_POLE = (0.35, 1.0)               # idle leg IK: knee bend direction in the body frame (outward, forward)
# WALK v1.3 -- CARTOON HOP (artist 2026-09-26: "the walking animation should have its legs remains stiff more cartoon
#      movement than realistic and faster like a little creature hopping around"). The legs are STIFF pegs (no knee:
#      thigh + shin locked straight, pivoting only at the hip; the foot bone only keeps the sole flat on the floor); all
#      the give is the BODY: squash on the landing / anticipation, stretch at takeoff. The squash lives on 'body' (the
#      block + crown + arms + licks), scaled about the hip line; the legs hang off 'hips' and never scale.
HOP_STYLE = "together"                # "hop style": "together" = both stiff legs take off + land together (pogo / bunny
                                      #   hop -- the default: with no knees a two-footed spring is the clearest hop read);
                                      #   "alternate" = land on one stiff leg, kick the other forward (skip-hop)
WALK_N = 24                           # "walk loop": 1 s = two hops -> 0.5 s per hop, cadence 120 hops / min (v1.2: 60)
HOP_LEN = 0.50                        # "hop length": ground covered per hop; implied speed = HOP_LEN / hop time
CONTACT = 1.0 / 3.0                   # "contact share": fraction of each hop the feet are on the floor (land > squash > push)
HOP_H = 0.30                          # "hop height": foot clearance at the top of the hop
SQUASH = 0.20                         # "landing squash": block height lost at the deepest squash (volume kept: the width
                                      #   grows by 1 / sqrt(height scale))
STRETCH = 0.14                        # "takeoff stretch": block height gained at the takeoff instant
FALL_STRETCH = 0.05                   # "fall stretch": the block stretches a little again dropping into the landing
SQUASH_PEAK = 0.40                    # where in the contact the squash bottoms out (0 = on impact, 1 = at takeoff)
LEG_SPLAY = 4.0                       # "landing splay": the stiff legs spread outward this many deg with the squash
LEG_SWING = 7.0                       # stiff legs trail back after takeoff / reach forward before landing deg (hip only)
ALT_KICK = 28.0                       # HOP_STYLE "alternate": the free stiff leg kicks forward this many deg
FOOT_POINT = 12.0                     # the pointed foot tips trail back in the air deg (flat on the floor)
HOP_LEAN = (2.0, 8.0)                 # body pitch forward deg: at landing / at takeoff (rights itself through the air)
HOP_ROLL, HOP_YAW = 4.0, 5.0          # "hop wobble": roll / twist deg, alternating L / R per hop, in the air only (the feet
                                      #   land flat); "alternate" rolls over the stance leg instead
ARM_FLAP = 24.0                       # "arm flap": arms fling up + out at takeoff, splay with the squash deg (tip)
ARM_TRAIL = 10.0                      # arms trail back in the air deg
ARM_LAG = 0.05                        # each arm bone lags the one above by this hop fraction (the flap whips)
WAND_ARM = 0.35                       # the wand arm flaps this fraction of the free arm (it steadies the staff)
WAND_CARRY = (22.0, 20.0)             # walk: the wand arm carries the staff forward deg, staff tilted forward deg (the butt
                                      #   clears the floor through the takeoff stretch)
FLOOR_EPS = 0.0005                    # floor guard: any foot whose mesh dips below z 0 is lifted by its dip + this
CONTACT_TOL = 0.002                   # a foot counts as planted within this of the floor (report)

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
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": "none: from scratch (sheet "
          "design/reference/firesprite-character-sheet.webp + sketch design/reference/firesprite-sketch.webp)",
          "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px of the sheet's front view; floor z = 0 at the foot line (sheet y 605)",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


unit = FP.unit


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


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

SDF_LOG, HIGH = {}, {}


def sdf_mesh(name, lo, hi, step, band, ops, floor=False):
    """ops: [(fn, lo, hi, k, mode)] -> the polygonised high (V, F) with crumbs dropped + audit."""
    t = time.time()
    g = FP.Grid(lo, hi, step, band)
    for fn, a, b, k, mode in ops:
        g.apply(fn, a, b, k, mode)
    if floor:
        g.floor(0.0)
    exact0 = int((g.F == 0.0).sum())
    # crossings ON a grid node make coincident vertices -> zero-area triangles that stall the collapse decimator
    # (first preview: body 9885, crown 38705 of them -> junk lows). Push |F| < NODE_EPS * step off the node; the surface
    # moves by at most that (0.1 step = 1.6 mm on the body).
    eps_ = NODE_EPS * step
    near0 = np.abs(g.F) < eps_
    g.F = np.where(near0, np.where(g.F < 0.0, -eps_, eps_), g.F)
    V, T = SD.polygonise(g)
    F = [list(map(int, f)) for f in T]
    V, F, crumbs = FP.keep_islands(V, F, 0.02)
    Ta = np.asarray(F)
    area = 0.5 * np.linalg.norm(np.cross(V[Ta[:, 1]] - V[Ta[:, 0]], V[Ta[:, 2]] - V[Ta[:, 0]]), axis=1)
    e = np.sort(np.vstack([Ta[:, [0, 1]], Ta[:, [1, 2]], Ta[:, [2, 0]]]), 1)
    _, ec = np.unique(e, axis=0, return_counts=True)
    SDF_LOG[name] = {"step": step, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(F), "high_verts": len(V),
                     "crumbs": crumbs, "open_edges": int((ec == 1).sum()), "nonmanifold_edges": int((ec > 2).sum()),
                     "zero_area_tris": int((area < 1e-12).sum()), "grid_exact_zero": exact0,
                     "nodes_pushed_off_surface": int(near0.sum()),
                     "seconds": round(time.time() - t, 1)}
    HIGH[name] = (V, F)
    return V, F


RETOPO = {}


def lowpoly(name, V, F, target):
    t = time.time()
    tmp = new_obj("dec_" + name, V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / len(F))
    LV, LF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF, specks = FP.keep_islands(LV, LF, 0.01)
    bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
    hs = np.arange(0, len(V), max(1, len(V) // 15000))
    dev2 = np.array([bvh_lo.find_nearest(Vector(V[i]))[3] for i in hs])
    RETOPO[name] = {"target": target, "tris": tri_count_F(LF), "specks": specks, "open_edges": FP.open_edges(LF),
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "seconds": round(time.time() - t, 1)}
    return LV, LF


def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where column j owns the surface


# =========================================================================== 1. BODY: block + licks + limbs - face holes
BW, BD = BLOCK_HALF
ZB0, ZB1 = BLOCK_Z
HZ, ZC = (ZB1 - ZB0) / 2.0, (ZB1 + ZB0) / 2.0
FACE_Y = -BD


def hip_t(z):
    """0 at the block bottom -> 1 at the top of the hip funnel. LINEAR (one flat slanted facet + a crease): a smoothstep
    funnel decimated into thin horizontal flat-shaded strips that read as stripes (v1.2 preview)."""
    return np.clip((np.asarray(z, float) - ZB0) / (HIP[0] - ZB0), 0.0, 1.0) if HIP else np.ones_like(np.asarray(z, float))


def hx_of(z):
    base = BW * (BLOCK_TAPER + (1.0 - BLOCK_TAPER) * np.clip((np.asarray(z, float) - ZB0) / (ZB1 - ZB0), 0.0, 1.0))
    return HIP[1] + (base - HIP[1]) * hip_t(z) if HIP else base


def hy_of(z):
    return HIP[2] + (BD - HIP[2]) * hip_t(z) if HIP else np.full(np.shape(z), BD)


def sd_block(P):
    half = np.stack([hx_of(P[:, 2]), hy_of(P[:, 2]), np.full(len(P), HZ)], 1)
    return FP.sd_round_box(P, (0.0, 0.0, ZC), half, BLOCK_ROUND)


if CROTCH:                                   # sides through (0, apex) and (+-half width, ZB0), run on 0.3 below the block
    _wb = CROTCH[1] * (CROTCH[0] - ZB0 + 0.3) / (CROTCH[0] - ZB0)
    CROTCH_TRI = np.array([(0.0, CROTCH[0]), (-_wb, ZB0 - 0.3), (_wb, ZB0 - 0.3)])


def sd_crotch(P):
    """the inverted-V notch between the legs (a triangle in x-z, through the whole depth)."""
    return FP.sd_poly2d(P[:, [0, 2]], CROTCH_TRI)


def sd_torso(P):
    """the block with the crotch notch cut (hard) -- the ownership / weight field of the block group."""
    return np.maximum(sd_block(P), -sd_crotch(P)) if CROTCH else sd_block(P)


def theta_of(P):
    """azimuth round the block, 0 at the front centre (-Y), +pi/2 at the unit's left side (+X)."""
    return np.arctan2(P[:, 0] / hx_of(P[:, 2]), -P[:, 1] / hy_of(P[:, 2]))


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---- the elements (every flame lick / limb is a tapered cone chain with a spine: ownership, dark tips, weights)
EL = []


def add_el(name, group, cones_pts, deep_t, k):
    cones, pts = cones_pts
    EL.append({"name": name, "group": group, "cones": cones, "spine": np.asarray(pts, float), "deep_t": deep_t, "k": k})


for side, sg in (("L", 1.0), ("R", -1.0)):
    for i, (z, y, reach, rise, r0) in enumerate(SIDE_LICKS):
        z_ = z + (0.0 if side == "L" else SIDE_R_DZ)
        y_ = y if side == "L" else -0.6 * y
        root = np.array([sg * (float(hx_of(z_)) - 0.06), y_, z_])
        path = [root, root + np.array([sg * reach * 0.6, 0.0, rise * 0.12]),
                root + np.array([sg * reach * 0.95, 0.025, rise * 0.5]), root + np.array([sg * reach * 0.7, 0.05, rise])]
        add_el("lick.side.%s.%d" % (side, i), "block", FP.lick_path(path, r0, n=12, rtip=0.012, sharp=LICK_SHARP),
               LICK_DEEP_T, LICK_K)
for i, (x, z0, dx, z1, r0) in enumerate(BACK_LICKS):
    path = [(x, BD - 0.06, z0), (x + 0.3 * dx, BD + 0.22, z0 + 0.3 * (z1 - z0)), (x + 0.8 * dx, BD + 0.30, z0 + 0.65 * (z1 - z0)),
            (x + dx, BD + 0.40, z1)]
    add_el("lick.back.%d" % i, "block", FP.lick_path(path, r0, n=12, rtip=0.012, sharp=LICK_SHARP), LICK_DEEP_T, LICK_K)
for i, (az, ln, r0) in enumerate(HEM_LICKS):
    d = np.array([math.sin(math.radians(az)), -math.cos(math.radians(az))])
    hxb, hyb = float(hx_of(ZB0)), float(hy_of(ZB0))
    s_ = 1.0 / max(abs(d[0]) / hxb, abs(d[1]) / hyb)
    root = np.array([d[0] * s_ * 0.90, d[1] * s_ * 0.90, ZB0 + 0.14])
    out3, perp = np.array([d[0], d[1], 0.0]), np.array([-d[1], d[0], 0.0]) * (1.0 if i % 2 else -1.0)
    path = [root, root + out3 * 0.10 + np.array([0, 0, -0.30 * ln]),
            root + out3 * 0.05 + perp * 0.05 + np.array([0, 0, -0.65 * ln]), root + out3 * 0.14 - perp * 0.02 + np.array([0, 0, -ln])]
    add_el("lick.hem.%d" % i, "block", FP.lick_path(path, r0, n=10, rtip=0.012, sharp=LICK_SHARP), LICK_DEEP_T, LICK_K)

# wand axis first (the R hand sits on it)
WB, WT = np.array(WAND_BOTTOM, float), np.array(WAND_TOP, float)
WAX = unit(WT - WB)
HAND_T = (HAND_Z - WB[2]) / (WT[2] - WB[2])
HAND_R_PT = WB + HAND_T * (WT - WB)
ARM_SPINE = {}
for side, sg, A in (("R", -1.0, ARM_R), ("L", 1.0, ARM_L)):
    sh_ = np.array(A["shoulder"], float)
    hand = HAND_R_PT if side == "R" else np.array(A["hand"], float)
    cones, pts = FP.lick(sh_, np.array(A["ctrl"], float), hand, A["r"][0], n=12, rtip=A["r"][1], sharp=1.0)
    hb = hand + np.array([0.0, 0.0, 0.012])
    cones = cones + [(hand, hb, A["hand_r"], A["hand_r"])]
    EL.append({"name": "arm." + side, "group": "arm." + side, "cones": cones, "spine": pts, "deep_t": ARM_DEEP_T[side],
               "k": ARM_K})
    ARM_SPINE[side] = pts
    al = SD.arclen(pts)
    for j, (fr, off, r0) in enumerate(ARM_LICKS):
        root = np.array([np.interp(fr * al[-1], al, pts[:, k]) for k in range(3)])
        o_ = np.array([sg * off[0], off[1], off[2]])
        path = [root, root + o_ * np.array([0.5, 0.3, 0.2]), root + o_ * np.array([0.9, 0.6, 0.6]), root + o_ * np.array([0.8, 1.0, 1.0])]
        add_el("lick.arm.%s.%d" % (side, j), "arm." + side, FP.lick_path(path, r0, n=10, rtip=0.01, sharp=LICK_SHARP),
               LICK_DEEP_T, ARM_K * 0.5)
    if side == "L":
        for j, (off, r0) in enumerate(CLAW):
            tip = hand + np.array(off)
            ctrl = hand + np.array(off) * np.array([0.5, 0.5, 0.45])
            add_el("claw.L.%d" % j, "arm.L", FP.lick(hand, ctrl, tip, r0, n=8, rtip=0.01, sharp=1.0), 0.45, 0.04)
LEG_SPINE = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    m = np.array([sg, 1.0, 1.0])
    cones, pts = FP.lick(np.array(LEG["hip"]) * m, np.array(LEG["ctrl"]) * m, np.array(LEG["tip"]) * m, LEG["r"][0], n=12,
                         rtip=LEG["r"][1], sharp=LEG["sharp"])
    EL.append({"name": "leg." + side, "group": "leg." + side, "cones": cones, "spine": pts, "deep_t": LEG_DEEP_T, "k": LEG_K})
    LEG_SPINE[side] = pts
    for j, (root, tip, r0) in enumerate(TOES):
        root = np.array(root) * m; tip = np.array(tip) * m
        add_el("toe.%s.%d" % (side, j), "leg." + side, FP.lick(root, 0.5 * (root + tip) + np.array([0, 0, 0.04]), tip, r0,
                                                                n=7, rtip=0.01, sharp=1.0), 0.40, 0.05)


def mouth_outline():
    W = MOUTH_X
    z0, z1 = MOUTH_Z
    nt, nb = MOUTH_TEETH
    top = []
    for k, x in enumerate(np.linspace(-W, W, 2 * nt + 1)):
        top.append((x, z1 - MOUTH_SAG[0] * (x / W) ** 2 - (MOUTH_TOOTH if k % 2 else 0.0)))
    bot = []
    for k, x in enumerate(np.linspace(W, -W, 2 * nb + 1)):
        bot.append((x, z0 + MOUTH_SAG[1] * (x / W) ** 2 + (MOUTH_TOOTH if k % 2 else 0.0)))
    return np.array(top + bot)


EYE_Lp = np.array(EYE_L, float)
EYE_Rp = (EYE_Lp * np.array([-1.0, 1.0]))[::-1]
MOUTH = mouth_outline()
HOLES = {"eye.L": EYE_Lp, "eye.R": EYE_Rp, "mouth": MOUTH}


def sd_holes2d(P):
    Q = P[:, [0, 2]]
    return np.minimum.reduce([FP.sd_poly2d(Q, V) for V in HOLES.values()])


def sd_pockets(P):
    return np.maximum(sd_holes2d(P), P[:, 1] - (FACE_Y + POCKET_DEPTH))


t_body = time.time()
_boxes = [FP.cones_box(e["cones"]) for e in EL]
BODY_LO = np.minimum.reduce([b[0] for b in _boxes] + [np.array([-BW, -BD, 0.0])]) - 0.06
BODY_HI = np.maximum.reduce([b[1] for b in _boxes] + [np.array([BW, BD, ZB1])]) + 0.06
BODY_LO[2] = -0.06
ops = [(sd_block, (-BW, -BD, ZB0), (BW, BD, ZB1), 0.0, "union")]
if CROTCH:
    ops.append((sd_crotch, (-CROTCH[1] * 3.0, -BD - 0.05, ZB0 - 0.05), (CROTCH[1] * 3.0, BD + 0.05, CROTCH[0] + 0.05),
                CROTCH[2], "subtract"))
# v1.2: the legs (+ toes) melt into the block FIRST, the licks after -- a large LEG_K fillet taken after the hem licks
# swelled them into round lumps at the leg roots (v1.2 preview)
for e in sorted(EL, key=lambda e_: 0 if e_["group"].startswith("leg.") else 1):
    lo_, hi_ = FP.cones_box(e["cones"])
    ops.append((lambda P, c=e["cones"]: FP.cones_sdf(P, c), lo_, hi_, e["k"], "union"))
_hl = np.vstack(list(HOLES.values()))
ops.append((sd_pockets, (_hl[:, 0].min() - 0.02, -BD - 0.30, _hl[:, 1].min() - 0.02),
            (_hl[:, 0].max() + 0.02, FACE_Y + POCKET_DEPTH + 0.02, _hl[:, 1].max() + 0.02), 0.006, "subtract"))
HV_body, HF_body = sdf_mesh("body", BODY_LO, BODY_HI, BODY_STEP, BODY_BAND, ops, floor=True)
LV, LF = lowpoly("body", HV_body, HF_body, BODY_TRIS)

# ---- body region fields on the low shell
GROUPS = ["block", "arm.L", "arm.R", "leg.L", "leg.R"]


def group_sdf(P):
    """(n, len(GROUPS)) SDF per weight group (the block group = the block + its side / back / hem licks)."""
    D = np.full((len(P), len(GROUPS)), 1e9)
    D[:, 0] = sd_torso(P)
    for e in EL:
        j = GROUPS.index(e["group"])
        D[:, j] = np.minimum(D[:, j], FP.cones_sdf(P, e["cones"]))
    return D


def element_fields(P):
    """owner element (block = -1) -> deep = arc fraction past the element's dark-tip threshold (block: -1)."""
    D = np.stack([sd_torso(P)] + [FP.cones_sdf(P, e["cones"]) for e in EL], 1)
    own = np.argmin(D, 1)
    deep = np.full(len(P), -1.0)
    for j, e in enumerate(EL):
        m = own == j + 1
        if m.any():
            t_, _, _ = FP.spine_param(P[m], e["spine"])
            deep[m] = t_ - e["deep_t"]
    own_block = np.delete(D, 0, axis=1).min(1) - D[:, 0]
    return deep, own_block


def tongue_field(P):
    th = theta_of(P); z = P[:, 2]
    best = np.full(len(P), -1.0)
    for i, (deg, h, wdeg) in enumerate(TONGUES):
        t = (z - ZB0) / h
        c = math.radians(deg) + TONGUE_WOB[0] * np.sin(TONGUE_WOB[1] * z + 1.7 * i) * np.clip(t, 0, 1)
        w = math.radians(wdeg) * np.clip(1.0 - t, 0.0, 1.0) ** 0.8
        best = np.maximum(best, w - np.abs(wrap(th - c)) - 1e-3)
    return best


def shadow_field(P):
    x = P[:, 0]
    zl = SH_Z0 + SH_WAVE[0] * np.sin(SH_WAVE[1] * x + SH_WAVE[2])
    for xc, h, w in SH_PEAKS:
        zl = zl + h * np.clip(1.0 - np.abs(x - xc) / w, 0.0, 1.0) ** 1.5
    th_edge = SH_THETA + SH_SIDE_WAVE[0] * np.sin(SH_SIDE_WAVE[1] * P[:, 2])
    return np.minimum(P[:, 2] - zl, 0.5 * (th_edge - np.abs(theta_of(P))))


deep, own_block = element_fields(LV)
FIELDS = {"f_pocket": -sd_pockets(LV) - sd_block(LV), "y_pocket": LV[:, 1] - (FACE_Y + POCKET_DEPTH * 0.45),
          "y_rec": LV[:, 1] - (FACE_Y + POCKET_REC),
          "deep": deep, "own_block": own_block, "tongue": tongue_field(LV), "shadow": shadow_field(LV)}
IC = FP.IsoCutter(LV, LF, FIELDS, CUT_SNAP)
TOL = 0.01
cut_log = [IC.cut("f_pocket", 0.0), IC.cut("y_pocket", 0.0, IC.gate_pos("f_pocket", TOL)), IC.cut("deep", 0.0)]
if FACE_SHADOW:
    cut_log.append(IC.cut("shadow", 0.0, IC.gate_pos("own_block", TOL)))
cut_log.append(IC.cut("tongue", 0.0, IC.gate_pos("own_block", TOL)))
SV, SF, FV = IC.finish()
sreg = np.array(["fire"] * len(SF), dtype=object)
blk = FV["own_block"] > 0
sreg[blk & (FV["tongue"] > 0)] = "fire_bright"
if FACE_SHADOW:
    sreg[blk & (FV["shadow"] > 0)] = "fire_shadow"
sreg[FV["deep"] > 0] = "fire_deep"
pk = (FV["f_pocket"] > 0) & (FV["y_rec"] > 0)
sreg[pk] = "hole_wall"
sreg[pk & (FV["y_pocket"] > 0)] = "hole_glow"
report["body"] = {"cuts": cut_log, "shell_tris_after_cuts": len(SF), "elements": len(EL),
                  "limb_join": "FUSED: arms (k %.2f) and legs (k %.2f) smooth-union into the block -- one fire mass, no gaps"
                               % (ARM_K, LEG_K), "seconds": round(time.time() - t_body, 1)}

# =========================================================================== 2. body core (hot interior)
_cb, _cr = np.array(CORE_BASE), np.array(CORE_BASE_R)
_cels = [FP.lick_path([_cb + np.array(q) for q in path], r0, n=12, rtip=0.01, sharp=1.5)[0] for path, r0 in CORE_TONGUES]
_clo = np.minimum.reduce([FP.cones_box(c)[0] for c in _cels] + [_cb - _cr]) - 0.03
_chi = np.maximum.reduce([FP.cones_box(c)[1] for c in _cels] + [_cb + _cr]) + 0.03
HV_core, HF_core = sdf_mesh("core", _clo, _chi, CORE_STEP, 0.08,
                            [(lambda P: SD.sd_ellipsoid(P, _cb, _cr), _cb - _cr, _cb + _cr, 0.0, "union")] +
                            [(lambda P, c=c: FP.cones_sdf(P, c), *FP.cones_box(c), 0.10, "union") for c in _cels])
CV_core, CF_core = lowpoly("core", HV_core, HF_core, CORE_TRIS)

# =========================================================================== 3. crown (solid)
CZ0, CZ1 = CROWN_Z
HXT = float(hx_of(CZ0))


def band_off(z):
    return CROWN_OFF + CROWN_FLARE * (np.asarray(z, float) - CZ0)


def plan_d(P):
    return FP.sd_round_rect(P[:, :2], (HXT, BD), BLOCK_ROUND)


def sd_band(P):
    o = band_off(P[:, 2])
    shell = np.abs(plan_d(P) - o - CROWN_T / 2) - CROWN_T / 2
    return np.maximum(shell, np.maximum(CZ0 - P[:, 2], P[:, 2] - CZ1))


def sd_trim(P):
    o = band_off(P[:, 2])
    shell = np.abs(plan_d(P) - o - CROWN_T / 2) - (CROWN_T / 2 + CROWN_TRIM[1])
    h = CROWN_TRIM[0]
    top = np.maximum(CZ1 - h - P[:, 2], P[:, 2] - CZ1)
    bot = np.maximum(CZ0 - P[:, 2], P[:, 2] - (CZ0 + h))
    return np.maximum(shell, np.minimum(top, bot))


BOSS_Y = BD + float(band_off(BOSS["zc"])) + CROWN_T          # the band's outer face at the boss height


def sd_boss(P):
    return np.minimum(*[FP.sd_diamond(P, (0.0, f * BOSS_Y, BOSS["zc"]), BOSS["a"], BOSS["b"], BOSS["proud"], BOSS["bevel"], f)
                        for f in (-1.0, 1.0)])


def sd_gem(P):
    s = GEM["scale"]
    a, b = BOSS["a"] * s, BOSS["b"] * s
    return np.minimum(*[FP.sd_diamond(P, (0.0, f * (BOSS_Y + BOSS["proud"]), BOSS["zc"]), a, b, GEM["proud"], GEM["bevel"], f)
                        for f in (-1.0, 1.0)])


HORNS = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    root = np.array([sg * (HXT + float(band_off(HORN["z"])) + CROWN_T * 0.5), 0.0, HORN["z"]])
    m = np.array([sg, 1.0, 1.0])
    HORNS[side] = FP.lick(root, root + np.array(HORN["ctrl"]) * m, root + np.array(HORN["tip"]) * m, HORN["r"], n=14,
                          rtip=0.012, sharp=1.0)


def sd_horns(P):
    return np.minimum(FP.cones_sdf(P, HORNS["L"][0]), FP.cones_sdf(P, HORNS["R"][0]))


CROWN_PARTS = {"band": sd_band, "trim": sd_trim, "boss": sd_boss, "gem": sd_gem, "horn": sd_horns}
CROWN_REG = {"band": "crown", "trim": "crown_trim", "boss": "crown_boss", "gem": "crown_gem", "horn": "horn"}
_ro = HXT + float(band_off(CZ1)) + CROWN_T + CROWN_TRIM[1] + 0.02
_hb = [FP.cones_box(HORNS[s_][0]) for s_ in ("L", "R")]
CR_LO = np.minimum(np.array([-_ro, -(BD + _ro - HXT) - BOSS["proud"] - GEM["proud"], BOSS["zc"] - BOSS["b"]]), np.minimum(*[b[0] for b in _hb])) - 0.04
CR_HI = np.maximum(np.array([_ro, (BD + _ro - HXT) + BOSS["proud"] + GEM["proud"], CZ1]), np.maximum(*[b[1] for b in _hb])) + 0.04
_bb = (np.array([-_ro, -(BD + _ro - HXT), CZ0 - 0.01]), np.array([_ro, BD + _ro - HXT, CZ1 + 0.01]))
_bossb = (np.array([-BOSS["a"], -BOSS_Y - BOSS["proud"] - GEM["proud"], BOSS["zc"] - BOSS["b"]]),
          np.array([BOSS["a"], BOSS_Y + BOSS["proud"] + GEM["proud"], BOSS["zc"] + BOSS["b"]]))
crown_ops = [(sd_band, *_bb, 0.0, "union"), (sd_trim, *_bb, 0.004, "union"), (sd_boss, *_bossb, 0.008, "union"),
             (sd_gem, *_bossb, 0.003, "union"), (sd_horns, np.minimum(*[b[0] for b in _hb]), np.maximum(*[b[1] for b in _hb]),
                                                   0.02, "union")]
HV_crown, HF_crown = sdf_mesh("crown", CR_LO, CR_HI, CROWN_STEP, 0.04, crown_ops)
CRV, CRF = lowpoly("crown", HV_crown, HF_crown, CROWN_TRIS)
_names = list(CROWN_PARTS)
DCR = np.stack([CROWN_PARTS[n](CRV) for n in _names], 1)
IC = FP.IsoCutter(CRV, CRF, {"f_" + n: own_field(DCR, j) for j, n in enumerate(_names)}, CUT_SNAP)
crown_cuts = [IC.cut("f_" + n, 0.0) for n in _names if n != "band"]
CRV, CRF, FVc = IC.finish()
_own = np.argmax(np.stack([FVc["f_" + n] for n in _names], 1), 1)
creg = np.array([CROWN_REG[_names[o]] for o in _own], dtype=object)
report["crown"] = {"cuts": crown_cuts, "boss_face_y": round(BOSS_Y, 4), "band_z": list(CROWN_Z), "open_top": True}


# =========================================================================== 4. flames (crown flame; wand flame later)
def flame(name, base, base_r, tongues, step, tris, core_tris):
    """-> dict(outer=(V, F, regions), core=(V, F, regions)); tongue tips (arc past FLAME_TIP_T) = flame_tip."""
    base = np.asarray(base, float)
    out = {}
    for layer in ("outer", "core"):
        rs, ls, nk = (1.0, 1.0, len(tongues)) if layer == "outer" else FLAME_CORE
        br = np.array(base_r) * rs
        els = [FP.lick_path([base + np.array(p) * np.array([rs, rs, ls]) for p in path], r0 * rs, n=14, rtip=0.01,
                            sharp=FLAME_SHARP) for path, r0 in tongues[:nk]]
        lo = np.minimum.reduce([FP.cones_box(c)[0] for c, _ in els] + [base - br]) - 0.03
        hi = np.maximum.reduce([FP.cones_box(c)[1] for c, _ in els] + [base + br]) + 0.03
        ops_ = [(lambda P, br=br: SD.sd_ellipsoid(P, base, br), base - br, base + br, 0.0, "union")]
        ops_ += [(lambda P, c=c: FP.cones_sdf(P, c), *FP.cones_box(c), FLAME_K * rs, "union") for c, _ in els]
        HVf, HFf = sdf_mesh("%s.%s" % (name, layer), lo, hi, step * (1.0 if layer == "outer" else 1.25), 0.05, ops_)
        Vf, Ff = lowpoly("%s.%s" % (name, layer), HVf, HFf, tris if layer == "outer" else core_tris)
        if layer == "core":
            out[layer] = (Vf, Ff, ["flame_core"] * len(Ff))
            continue
        D = np.stack([SD.sd_ellipsoid(Vf, base, br)] + [FP.cones_sdf(Vf, c) for c, _ in els], 1)
        own = np.argmin(D, 1)
        tip = np.full(len(Vf), -1.0)
        for j, (c, pts) in enumerate(els):
            m_ = own == j + 1
            if m_.any():
                tip[m_] = FP.spine_param(Vf[m_], pts)[0] - FLAME_TIP_T
        ic = FP.IsoCutter(Vf, Ff, {"tip": tip}, CUT_SNAP)
        c_ = ic.cut("tip", 0.0)
        Vf, Ff, FVf = ic.finish()
        out[layer] = (Vf, Ff, list(np.where(FVf["tip"] > 0, "flame_tip", "flame_outer")))
        out["tip_cut"] = c_
        out["tongues"] = [pts for _, pts in els]
    return out


CFL = flame("crown_flame", CF_BASE, CF_BASE_R, CF_TONGUES, CF_STEP, CF_TRIS, CF_CORE_TRIS)

# =========================================================================== 5. wand (own object)
_p1 = unit(np.cross(WAX, [0.0, 0.0, 1.0]) if abs(WAX[2]) < 0.99 else [1.0, 0.0, 0.0])
_p2 = np.cross(WAX, _p1)


def wand_pt(t, o1=0.0, o2=0.0):
    return WB + t * (WT - WB) + o1 * _p1 + o2 * _p2


SHAFT = FP.chain([wand_pt(t, a, b) for t, a, b, _ in WAND_KINKS], [r for *_, r in WAND_KINKS])
NUBS = []
for t, az, ln, r0 in WAND_NUBS:
    root = wand_pt(t)
    d = unit(math.cos(math.radians(az)) * _p1 + math.sin(math.radians(az)) * _p2 + 0.9 * WAX)
    NUBS.append(FP.lick(root, root + d * ln * 0.5, root + d * ln, r0, n=5, rtip=0.008, sharp=1.0))
WTOP = wand_pt(WAND_KINKS[-1][0], WAND_KINKS[-1][1], WAND_KINKS[-1][2])
PRONGS = []
for az, spread, h in WAND_PRONGS:
    out_ = math.cos(math.radians(az)) * _p1 + math.sin(math.radians(az)) * _p2
    root = WTOP - WAX * 0.06
    PRONGS.append(FP.lick(root, root + out_ * spread + WAX * h * 0.35, root + out_ * spread * 0.40 + WAX * h,
                          WAND_PRONG_R, n=7, rtip=0.008, sharp=1.0))
WAND_PARTS = {"shaft": [SHAFT[0]], "prong": [c for c, _ in NUBS + PRONGS]}


def sd_wand_group(P, grp):
    return np.minimum.reduce([FP.cones_sdf(P, c) for c in WAND_PARTS[grp]])


_wc = SHAFT[0] + [x for c in WAND_PARTS["prong"] for x in c]
W_LO, W_HI = FP.cones_box(_wc, 0.03)
wand_ops = [(lambda P: FP.cones_sdf(P, SHAFT[0]), *FP.cones_box(SHAFT[0]), 0.0, "union")]
wand_ops += [(lambda P, c=c: FP.cones_sdf(P, c), *FP.cones_box(c), 0.006, "union") for c in WAND_PARTS["prong"]]
HV_wand, HF_wand = sdf_mesh("wand", W_LO, W_HI, WAND_STEP, 0.03, wand_ops)
WV, WF = lowpoly("wand", HV_wand, HF_wand, WAND_TRIS)
DW = np.stack([sd_wand_group(WV, "shaft"), sd_wand_group(WV, "prong")], 1)
IC = FP.IsoCutter(WV, WF, {"f_prong": own_field(DW, 1)}, CUT_SNAP)
wand_cut = IC.cut("f_prong", 0.0)
WV, WF, FVw = IC.finish()
wreg = list(np.where(FVw["f_prong"] > 0, "wand_prong", "wand_bark"))
WF_BASE = WTOP + np.array([0.0, 0.0, WF_LIFT])
WFL = flame("wand_flame", WF_BASE, WF_BASE_R, WF_TONGUES, WF_STEP, WF_TRIS, WF_CORE_TRIS)
report["wand"] = {"bottom": WB.tolist(), "top": WTOP.round(4).tolist(), "hand": HAND_R_PT.round(4).tolist(),
                  "length": round(float(np.linalg.norm(WTOP - WB)), 4), "prong_cut": wand_cut,
                  "rule": "own object 'firesprite_wand' + own bones (wand, wand_flame; wand parented to the R hand bone "
                          "arm.R.2): hide or swap the node"}

# =========================================================================== 6. assemble + centre
# main object: cores first (drawn before the translucent shells), then the shells, then the crown
MAIN = [("core", CV_core, CF_core, ["fire_core"] * len(CF_core), 0),
        ("crown_flame.core", *CFL["core"], 0),
        ("shell", SV, SF, list(sreg), 0),
        ("crown_flame.outer", *CFL["outer"], 0),
        ("crown", CRV, CRF, list(creg), 1)]
WAND = [("wand", WV, WF, wreg, 0), ("wand_flame.core", *WFL["core"], 1), ("wand_flame.outer", *WFL["outer"], 1)]
allV = np.vstack([p[1] for p in MAIN + WAND])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
report["body_axis_offset_from_origin"] = {"x": round(float(-SHIFT[0]), 4), "y": round(float(-SHIFT[1]), 4),
                                          "why": "the contract centres the WHOLE bbox (wand included) on the origin"}


def pack(pieces):
    V, F, R, M, RANGE = [], [], [], [], {}
    n = 0
    for name, V_, F_, R_, mi in pieces:
        RANGE[name] = (n, n + len(V_))
        V.append(np.asarray(V_) - SHIFT)
        F += [[i + n for i in f] for f in F_]
        R += list(R_); M += [mi] * len(F_)
        n += len(V_)
    return np.vstack(V), F, R, M, RANGE


VM, FM, RM, MM, RANGE_M = pack(MAIN)
VW, FW, RW, MW, RANGE_W = pack(WAND)
REG_M = ["fire_shadow", "fire_deep", "fire", "fire_bright", "fire_core", "hole_wall", "hole_glow", "flame_tip", "flame_outer",
         "flame_core", "crown", "crown_trim", "crown_boss", "crown_gem", "horn"]
REG_W = ["wand_bark", "wand_prong", "flame_tip", "flame_outer", "flame_core"]
assert set(RM) <= set(REG_M), sorted(set(RM) - set(REG_M))
assert set(RW) <= set(REG_W), sorted(set(RW) - set(REG_W))
rid_m = np.array([REG_M.index(r) for r in RM], dtype=np.int32)
rid_w = np.array([REG_W.index(r) for r in RW], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.95 + 0.10 * j


# ---- materials
def make_mat(name, kind):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    if kind == "fire":
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"          # Eevee: order-independent see-through (glTF alphaMode BLEND)
        mat.use_transparent_shadow = True
    mat.use_backface_culling = True                      # every piece is a closed solid: single-sided (glTF doubleSided false)
    return mat


def apply_alpha(mat, pal):
    n_ = mat.node_tree.nodes.get("alpha_factor")
    if n_ is not None:
        n_.inputs[1].default_value = float(pal["material"].get("alpha", 1.0))


def paint_alpha(me, pal):
    names, rid_, _ = PAL.read_regions(me)
    a_reg = np.array([float(pal["regions"][n].get("alpha", 1.0)) for n in names], np.float32)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ca = me.color_attributes["Col"]
    cd = np.empty(len(me.loops) * 4, dtype=np.float32); ca.data.foreach_get("color", cd)
    cd = cd.reshape(-1, 4); cd[:, 3] = np.repeat(a_reg[rid_], lt)
    ca.data.foreach_set("color", cd.ravel())
    return {n: float(a) for n, a in zip(names, a_reg)}


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        paint_alpha(o.data, pal)
    for mat_ in (MAT_FIRE, MAT_CROWN, MAT_WAND):
        PAL.apply_material(mat_, pal); apply_alpha(mat_, pal)
    return out


def srgb_lum(rgb):
    c = PAL.srgb_to_linear(rgb)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


GRADE_BODY = ["fire_shadow", "fire_deep", "fire", "fire_bright", "fire_core", "hole_wall", "hole_glow"]
GRADE_FLAME = ["flame_tip", "flame_outer", "flame_core"]
HOTTEST = ["hole_glow", "flame_core"]


def glow_tiers(pal, hue_window=None):
    """hellfire gate: along each grade (body: shadow < dark red < orange < bright orange < core < hole wall < hole floor;
    flame: tip < outer < core) BOTH the emission tier and the base-colour luminance rise strictly; the hottest regions
    (eye/mouth hole floors, flame cores) out-shine every other region; default skin: every fire hue in the red-orange-
    yellow window."""
    import colorsys
    R_ = pal["regions"]
    tier = {n: float(R_[n].get("emission_scale", 0.0)) for n in GRADE_BODY + GRADE_FLAME}
    lum = {n: round(srgb_lum(R_[n]["rgb"]), 4) for n in R_}
    ok = {}
    for gname, gl in (("body", GRADE_BODY), ("flame", GRADE_FLAME)):
        ok[gname + "_emission_rises"] = all(tier[a] < tier[b] for a, b in zip(gl, gl[1:]))
        ok[gname + "_luminance_rises"] = all(lum[a] < lum[b] for a, b in zip(gl, gl[1:]))
    others = [n for n in R_ if n not in HOTTEST]
    ok["hottest_outshine_all"] = min(lum[h] for h in HOTTEST) > max(lum[n] for n in others)
    hues = {}
    for n in GRADE_BODY + GRADE_FLAME:
        h_, s_, v_ = colorsys.rgb_to_hsv(*[c / 255.0 for c in R_[n]["rgb"]])
        hues[n] = round(h_ * 360.0, 1)
    if hue_window is not None:
        ok["hue_in_window"] = all(hue_window[0] <= h <= hue_window[1] for h in hues.values())
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "luminance": {n: lum[n] for n in GRADE_BODY + GRADE_FLAME},
            "hue_deg": hues, "hue_window": hue_window, "gates": ok, "pass": all(ok.values())}


MAT_FIRE = make_mat(UNIT + "_fire", "fire")
MAT_CROWN = make_mat(UNIT + "_crown", "solid")
MAT_WAND = make_mat(UNIT + "_wand", "solid")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default, (0.0, 60.0))}
assert report["glow_tiers"]["default"]["pass"], report["glow_tiers"]

low = new_obj(UNIT, VM, FM)
low.data.materials.append(MAT_FIRE); low.data.materials.append(MAT_CROWN)
low.data.polygons.foreach_set("material_index", np.array(MM, dtype=np.int32))
PAL.store_regions(low.data, REG_M, rid_m, jitter(VM, FM))
wand = new_obj(UNIT + "_wand", VW, FW)
wand.data.materials.append(MAT_WAND); wand.data.materials.append(MAT_FIRE)
wand.data.polygons.foreach_set("material_index", np.array(MW, dtype=np.int32))
PAL.store_regions(wand.data, REG_W, rid_w, jitter(VW, FW))
report["regions_faces"] = repaint([low, wand], pal_default)
report["alpha"] = {"main": paint_alpha(low.data, pal_default), "wand": paint_alpha(wand.data, pal_default),
                   "material_alpha": pal_default["material"].get("alpha", 1.0)}
low.data.update(); wand.data.update()
fa = np.empty(len(low.data.polygons)); low.data.polygons.foreach_get("area", fa)
report["regions_area_share_main"] = {n: round(float(fa[rid_m == j].sum() / fa.sum()), 4) for j, n in enumerate(REG_M)}
low.data["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE_M.items()})
wand.data["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE_W.items()})

# facing landmark: the crown centre -> the front diamond boss's proud face on the midline
anchor = np.array([0.0, 0.0, BOSS["zc"]]) - SHIFT
landmark = np.array([0.0, -(BOSS_Y + BOSS["proud"]), BOSS["zc"]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "crown centre -> front diamond boss face on the midline (the sheet's front boss; the eye "
                            "and mouth holes are on the same face)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

# ---- flat + UV (both objects)
for ob_ in (low, wand):
    ob_.data.shade_flat()
    bpy.context.view_layer.objects.active = ob_
    for o in scene.objects:
        o.select_set(o is ob_)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")

tris_m, tris_w = tri_count_F(FM), tri_count_F(FW)
report["tris"] = {"total": tris_m + tris_w, "main": tris_m, "wand_object": tris_w,
                  **{n: tri_count_F(F_) for n, _, F_, _, _ in MAIN + WAND}}
report["tier_rationale"] = ("REGULAR role (a line caster, not a boss or hero): window [%d, %d], the firefly's regular "
                            "tier. The sheet's read needs the ragged flame silhouette (licks, claw, toes), carved "
                            "jagged holes, an ornamental crown (trim, bevelled bosses, horns) and two multi-tongue "
                            "flames with nested cores -- ~2x crowd density, landing mid-window." % tuple(TRI_BUDGET))
report["sdf"] = SDF_LOG
report["retopo"] = RETOPO
report["open_edges"] = {n: FP.open_edges(F_) for n, _, F_, _, _ in MAIN + WAND}
lo_a = np.vstack([VM, VW]).min(0); hi_a = np.vstack([VM, VW]).max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1)},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
for ob_ in (low, wand):
    ob_["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "regular"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch: design/reference/firesprite-character-sheet.webp + firesprite-sketch.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; fire "
                                 "material (body shell, core, crown flame, wand flame) alphaMode BLEND single-sided; crown + "
                                 "wand opaque")
wand["conquest_toggle"] = "the wand (and its flame) is its own node on its own bones: hide or swap it"
# focus boxes for the close-ups (world, after the centre shift)
_face = np.vstack([np.c_[V_[:, 0], np.full(len(V_), FACE_Y), V_[:, 1]] for V_ in HOLES.values()])
_face = np.vstack([_face.min(0) - np.array([0.12, 0.35, 0.14]), _face.max(0) + np.array([0.12, 0.0, 0.12])]) - SHIFT
_crown = np.vstack([CRV.min(0), CRV.max(0), np.array([0.0, 0.0, CZ1 + 0.25])]) - SHIFT
_wt = np.vstack([wand_pt(0.62), WTOP + np.array([0.0, 0.0, 1.2])])
_wtop = np.vstack([_wt.min(0) - 0.12, _wt.max(0) + 0.12]) - SHIFT
low["conquest_focus"] = json.dumps({"face": [_face.min(0).tolist(), _face.max(0).tolist()],
                                    "crown": [_crown.min(0).tolist(), _crown.max(0).tolist()],
                                    "wand": [_wtop.min(0).tolist(), _wtop.max(0).tolist()],
                                    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o not in (low, wand):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share_main", "glow_tiers", "body")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 7. bake normal + AO (main object; crown wired)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F, o_ = [], [], 0
for nm in ("body", "core", "crown", "crown_flame.outer", "crown_flame.core"):
    V_, F_ = HIGH[nm]
    PH_V.append(V_ - SHIFT); PH_F += [[i + o_ for i in f] for f in F_]; o_ += len(V_)
HIGHO = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
BAKE_CAGE = max(0.02, round(3.0 * max(RETOPO[n]["high_to_low_sampled"]["p99"] for n in ("crown", "body")), 4))
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
for mat_ in (MAT_FIRE, MAT_CROWN):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
wand.hide_render = True
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me = low.data
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
    o.select_set(o is HIGHO or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, 16)):
    for mat_ in (MAT_FIRE, MAT_CROWN):
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
crown_faces = np.array(MM) == 1
tex_n = texels_of(crown_faces, RN)
tex_a = texels_of(crown_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance of the crown / body (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "every main-object SDF high (body, core, crown, crown flame outer + core)",
    "wired_into": "the crown material only (the fire keeps flat colour + glow + alpha; the wand is flat-shaded colour)",
    "crown_uv_texels_normal": int(tex_n.sum()),
    "normal_baked_pct_of_crown_texels": round(100 * float(cov_n[tex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & tex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_crown_texels": round(100 * float(cov_a[tex_a].mean()), 2),
    "ao_mean_crown": round(float(pa[cov_a & tex_a, 0].mean()), 4),
    "ao_p05_crown": round(float(np.percentile(pa[cov_a & tex_a, 0], 5)), 4)})
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
nt = MAT_CROWN.node_tree
tn, ta = BAKE_NODES[MAT_CROWN.name]
for n_ in BAKE_NODES[MAT_FIRE.name]:
    MAT_FIRE.node_tree.nodes.remove(n_)
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
hm_ = HIGHO.data
bpy.data.objects.remove(HIGHO, do_unlink=True); bpy.data.meshes.remove(hm_)
wand.hide_render = False
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


DIG["geometry_colour_uv"] = geometry_digest([low, wand])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "the sheet's five palette chips (sampled: charcoal 65,55,56 / 81,72,72, orange 249,121,53 "
                                   "/ 250,143,61, dark red 160,45,28) + the sheet's pale-yellow eye glow and dark face"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 8. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


def resample(pts, n):
    al = SD.arclen(pts)
    return [np.array([np.interp(x, al, pts[:, k]) for k in range(3)]) for x in np.linspace(0, al[-1], n + 1)]


CF_TOP = float(max(p[-1][2] for p in CFL["tongues"]))
HIP_LINE_Z = float(np.mean([LEG_SPINE[s][0][2] for s in ("L", "R")]))   # the leg roots: the squash pivot (v1.3)
# v1.3: 'hips' (non-deform) carries the travel + tilt; 'body' (the block and everything on it) only squashes / stretches,
# about its head ON the hip line; the legs hang off 'hips', so they never scale (stiff legs).
BONES = [("hips", P_((0, 0, 1.0)), P_((0, 0, HIP_LINE_Z)), "root"),
         ("body", P_((0, 0, HIP_LINE_Z)), P_((0, 0, 2.6)), "hips"),
         ("crown", P_((0, 0, BOSS["zc"])), P_((0, 0, CZ1 + 0.05)), "body")]
_cfz = np.linspace(CF_BASE[2], CF_TOP, CF_BONES + 1)
for k in range(CF_BONES):
    BONES.append(("crown_flame.%d" % k, P_((0, 0, _cfz[k])), P_((0, 0, _cfz[k + 1])), "crown" if k == 0 else "crown_flame.%d" % (k - 1)))
CHAIN_PTS = {}
for side in ("L", "R"):
    CHAIN_PTS["arm." + side] = resample(ARM_SPINE[side], ARM_BONES)
    CHAIN_PTS["leg." + side] = resample(LEG_SPINE[side], LEG_BONES)
for ch, pts in sorted(CHAIN_PTS.items()):
    for k in range(len(pts) - 1):
        root_p = "hips" if ch.startswith("leg") else "body"
        BONES.append(("%s.%d" % (ch, k), P_(pts[k]), P_(pts[k + 1]), root_p if k == 0 else "%s.%d" % (ch, k - 1)))
WF_TOP = float(max(p[-1][2] for p in WFL["tongues"]))
BONES.append(("wand", P_(HAND_R_PT), P_(WTOP), "arm.R.%d" % (ARM_BONES - 1)))
BONES.append(("wand_flame", P_(WF_BASE), P_(np.array([WF_BASE[0], WF_BASE[1], WF_TOP])), "wand"))
LICK_EL = [e for e in EL if e["group"] == "block" and e["name"].startswith("lick.")] if LICK_BONES else []
for e in LICK_EL:                                        # one bone per body lick: root -> tip, parent body (unconnected)
    BONES.append((e["name"], P_(e["spine"][0]), P_(e["spine"][-1]), "body"))
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
    e.use_connect = nm[-1].isdigit() and not nm.endswith(".0") and not nm.startswith("lick.")
    e.use_deform = nm != "hips"
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES if b[0] != "hips"]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights, main object (<= 4 influences)
Wt = np.zeros((len(VM), len(DEFORM)))
a_, b_ = RANGE_M["shell"]
Pw = VM[a_:b_] + SHIFT                                   # back to the design frame
GD = group_sdf(Pw)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
Ws = np.zeros((b_ - a_, len(DEFORM)))
Ws[:, J["body"]] += Wg[:, 0]
LIMB_W = {}
for gi, grp in enumerate(GROUPS[1:], start=1):
    side = grp.split(".")[1]
    spine = ARM_SPINE[side] if grp.startswith("arm") else LEG_SPINE[side]
    nb = ARM_BONES if grp.startswith("arm") else LEG_BONES
    _, s_arc, _ = FP.spine_param(Pw, spine)
    L_ = float(SD.arclen(spine)[-1])
    Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, nb)
    Ws[:, J["body"]] += Wg[:, gi] * Wv[:, 0]
    for k in range(nb):
        Ws[:, J["%s.%d" % (grp, k)]] += Wg[:, gi] * Wv[:, k + 1]
    LIMB_W[grp] = {"length": round(L_, 4), "dominant_verts": int((np.argmax(Wg, 1) == gi).sum())}
if LICK_EL:                                              # the block share splits onto the nearest lick's bone out its spine
    DL = np.stack([FP.cones_sdf(Pw, e["cones"]) for e in LICK_EL], 1)
    jn = np.argmin(DL, 1)
    sig = smoothstep(-0.02, 0.02, sd_torso(Pw) - DL[np.arange(len(Pw)), jn])
    tl = np.zeros(len(Pw))
    for j, e in enumerate(LICK_EL):
        m = jn == j
        if m.any():
            tl[m] = FP.spine_param(Pw[m], e["spine"])[0]
    wl = Wg[:, 0] * sig * smoothstep(LICK_RAMP[0], LICK_RAMP[1], tl)
    Ws[:, J["body"]] -= wl
    for j, e in enumerate(LICK_EL):
        m = jn == j
        Ws[m, J[e["name"]]] += wl[m]
    LIMB_W["licks"] = {"bones": len(LICK_EL), "verts_on_a_lick_bone": int((wl > 1e-4).sum())}
Wt[a_:b_] = Ws
a_, b_ = RANGE_M["core"]; Wt[a_:b_, J["body"]] = 1.0
a_, b_ = RANGE_M["crown"]; Wt[a_:b_, J["crown"]] = 1.0
CF_L = CF_TOP - CF_BASE[2]
for nm in ("crown_flame.core", "crown_flame.outer"):
    a_, b_ = RANGE_M[nm]
    s_ = np.clip(VM[a_:b_, 2] + SHIFT[2] - CF_BASE[2], 0, CF_L)
    Wv = K.vine_weights(s_, CF_L, CF_BONES)
    Wt[a_:b_, J["crown"]] += Wv[:, 0]
    for k in range(CF_BONES):
        Wt[a_:b_, J["crown_flame.%d" % k]] += Wv[:, k + 1]
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
# wand object: the shaft rides 'wand', the flame 'wand_flame'
for n in ("wand", "wand_flame"):
    wand.vertex_groups.new(name=n)
a_, b_ = RANGE_W["wand"]
wand.vertex_groups["wand"].add(list(range(a_, b_)), 1.0, "REPLACE")
wand.vertex_groups["wand_flame"].add(list(range(RANGE_W["wand_flame.core"][0], RANGE_W["wand_flame.outer"][1])), 1.0, "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "limbs": LIMB_W, "wand_object": "shaft 100% 'wand', flame 100% 'wand_flame'",
                  "rule": "fire shell: soft SDF ownership (tau %.2f) between the block (+ its licks) and each limb; a "
                          "limb's share runs down its chain by rigkit.vine_weights arc-length hat weights (the root "
                          "blends into body); the block share splits onto the nearest body lick's bone out along its "
                          "spine (LICK_RAMP); core -> body; crown -> crown (rigid); crown flame -> crown_flame chain by "
                          "height (root blends into crown)" % OWN_TAU}
for ob_ in (low, wand):
    ob_.parent = rig
    ob_.matrix_parent_inverse = Matrix.Identity(4)
    am = ob_.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- CLIPS: idle (grounded living fire) + walk (v1.3 STIFF-LEGGED CARTOON HOP, artist 2026-09-26). Both in place (root
# at the origin), closed-form in the loop phase u with integer harmonics only (exact seams). Idle legs: analytic 2-bone
# IK onto planted foot targets. Walk legs: RIGID (knee locked straight), posed in closed form: each hip pivot angle is
# solved so a planted foot tracks the floor exactly, and the hips height is solved so the feet land exactly on z 0 (no
# IK, no guard needed). A FLOOR GUARD still lifts any foot whose deformed mesh dips below z 0 (it must stay idle).
# Pass 1 poses + guards every frame with no action bound; pass 2 keys the stored channels; pass 3 measures the clips
# exactly as the contract checker plays them.
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
TAU2 = 2 * math.pi
REST_R = {b.name: b.matrix_local.to_3x3() for b in arm_data.bones}
REST_H = {b.name: Vector(b.head_local) for b in arm_data.bones}
REST_T = {b.name: Vector(b.tail_local) for b in arm_data.bones}
HIPS_HEAD = REST_H["hips"]                  # == the v1.2 body head: the idle's tilt pivot is unchanged
UP, BACK, FWD = Vector((0.0, 0.0, 1.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, -1.0, 0.0))
LICK_NAMES = [e["name"] for e in LICK_EL]
SCALE_BONES = ["body", "crown_flame.0", "crown_flame.1", "wand_flame"] + LICK_NAMES
LOC_BONES = ["hips"]
KEY_BONES = [b.name for b in arm_data.bones if b.name != "root"]


def rotm(axis, deg):
    return Matrix.Rotation(math.radians(deg), 3, Vector(axis).normalized())


def bdir(bn):
    return (REST_T[bn] - REST_H[bn]).normalized()


def toward(bn, target, deg):
    """rotation (parent frame) swinging bone bn's rest direction toward 'target' by deg (negative = away)."""
    ax = bdir(bn).cross(Vector(target))
    return rotm(ax, deg) if ax.length > 1e-6 else Matrix.Identity(3)


def perp_axes(bn):
    d = bdir(bn)
    e1 = d.cross(UP)
    if e1.length < 0.2:
        e1 = d.cross(Vector((1.0, 0.0, 0.0)))
    e1.normalize()
    return e1, d.cross(e1).normalized()


def harmonics(loop_s):
    return tuple(max(1, int(round(hz * loop_s))) for hz in FLICKER_HZ)


def flick(u, h, ph):
    """flame flicker in [-1, 1]: two integer harmonics of the loop (seam exact), phase-shifted per flame."""
    return 0.6 * math.sin(TAU2 * h[0] * u + ph) + 0.4 * math.sin(TAU2 * h[1] * u + 1.9 * ph + 0.7)


LEGS = {}
for side in ("L", "R"):
    b0_, b1_, b2_ = "leg.%s.0" % side, "leg.%s.1" % side, "leg.%s.%d" % (side, LEG_BONES - 1)
    LEGS[side] = {"H": REST_H[b0_], "a": (REST_H[b1_] - REST_H[b0_]).length, "b": (REST_T[b1_] - REST_H[b1_]).length,
                  "r0": bdir(b0_), "r1": bdir(b1_), "foot": REST_T[b2_].copy(), "ankle": REST_H[b2_].copy(),
                  "sg": 1.0 if side == "L" else -1.0}
assert LEG_BONES == 3, "the leg IK is hip -> knee -> ankle + a foot bone"


def solve_leg(side, Db, off, tip, F):
    """analytic 2-bone IK (thigh + shin onto the ankle) + the foot bone held at world rotation F (identity = flat, as at
    rest) -> (D0, D1, D2 parent-frame rotations, reach shortfall); the knee bends toward KNEE_POLE."""
    L = LEGS[side]
    target = tip - F @ (L["foot"] - L["ankle"])
    Hp = HIPS_HEAD + off + Db @ (L["H"] - HIPS_HEAD)
    dv = target - Hp
    d = dv.length
    un = dv / d
    a, b = L["a"], L["b"]
    dc = min(max(d, abs(a - b) + 1e-4), (a + b) * (1.0 - 1e-5))
    x = (a * a - b * b + dc * dc) / (2.0 * dc)
    h = math.sqrt(max(a * a - x * x, 0.0))
    p = Db @ Vector((L["sg"] * KNEE_POLE[0], -KNEE_POLE[1], 0.0))
    p = (p - un * p.dot(un)).normalized()
    knee = Hp + un * x + p * h
    v0 = (knee - Hp).normalized()
    v1 = (Hp + un * dc - knee).normalized()
    D0 = L["r0"].rotation_difference(Db.transposed() @ v0).to_matrix()
    D1 = L["r1"].rotation_difference((Db @ D0).transposed() @ v1).to_matrix()
    return D0, D1, (Db @ D0 @ D1).transposed() @ F, d - dc


def flames_pose(u, h, envl):
    """crown flame, wand flame, body licks: constant upward flicker (sway + stretch) x FLAME_WILD, plus the walk's
    stream (envl(lag) in [0, 1], 0 in the idle): trail back + stretch at the top of each drift."""
    W = FLAME_WILD
    out = {}
    for k in range(CF_BONES):
        bn = "crown_flame.%d" % k
        amp = CF_FLICKER[0] + (CF_FLICKER[1] - CF_FLICKER[0]) * k / max(CF_BONES - 1, 1)
        e1, e2 = perp_axes(bn)
        D = rotm(e1, W * amp * flick(u, h, 0.3 + 0.9 * k)) @ rotm(e2, W * 0.7 * amp * flick(u, h, 1.4 + 0.9 * k))
        st = envl(0.04 * (k + 1))
        out[bn] = (toward(bn, BACK, W * STREAM["crown"] * st * (0.6 + 0.4 * k)) @ D, None,
                   1.0 + W * (CF_FLICKER[2] * flick(u, h, 2.2 + 1.1 * k) + STREAM["stretch"] * st * (k == 0)))
    e1, e2 = perp_axes("wand_flame")
    D = rotm(e1, W * WF_FLICKER[0] * flick(u, h, 0.4)) @ rotm(e2, W * 0.7 * WF_FLICKER[0] * flick(u, h, 2.6))
    st = envl(0.05)
    out["wand_flame"] = (toward("wand_flame", BACK, W * STREAM["wand"] * st) @ D, None,
                         1.0 + W * (WF_FLICKER[1] * flick(u, h, 1.0) + STREAM["stretch"] * st))
    for j, bn in enumerate(LICK_NAMES):
        e1, e2 = perp_axes(bn)
        ph = 1.7 * j + 0.5
        D = rotm(e1, W * LICK_FLICKER[0] * flick(u, h, ph)) @ rotm(e2, W * 0.7 * LICK_FLICKER[0] * flick(u, h, ph + 2.1))
        st = envl(0.02 + 0.06 * ((j * 0.618) % 1.0))
        out[bn] = (toward(bn, BACK, W * STREAM["lick"] * st) @ D, None,
                   1.0 + W * (LICK_FLICKER[1] * flick(u, h, ph + 0.9) + STREAM["stretch"] * st))
    return out


def idle_pose(u):
    h = harmonics(IDLE_N / K.FPS)
    Db = (rotm(UP, IDLE_SWAY[2] * math.sin(TAU2 * u + 2.0)) @ rotm((1, 0, 0), IDLE_SWAY[1] * math.sin(TAU2 * 2 * u + 1.2))
          @ rotm((0, 1, 0), IDLE_SWAY[0] * math.sin(TAU2 * u + 0.3)))
    off = Vector((IDLE_SHIFT * math.sin(TAU2 * u + 0.3), 0.0,
                  -IDLE_DROP - IDLE_BOB * (0.5 - 0.5 * math.cos(TAU2 * 2 * u))))
    pose = {"hips": (Db, off, None)}
    for side, sg in (("L", 1.0), ("R", -1.0)):
        r_, t_ = IDLE_ARM[side]
        for k in range(ARM_BONES):
            bn = "arm.%s.%d" % (side, k)
            a1 = K.vine_wave(u, k, ARM_BONES, r_, t_, 0.7, 1, phase=0.0 if side == "L" else 2.0)
            a2 = K.vine_wave(u, k, ARM_BONES, 0.6 * r_, 0.6 * t_, 0.7, 2, phase=1.1 if side == "L" else 0.4)
            pose[bn] = (toward(bn, BACK, a1) @ toward(bn, (sg, 0, 0), a2), None, None)
    pose.update(flames_pose(u, h, lambda lag: 0.0))
    return pose, {s: (LEGS[s]["foot"].copy(), Matrix.Identity(3)) for s in ("L", "R")}


# ---- WALK v1.3: the stiff-legged cartoon hop. One hop = phase s in [0, 1): CONTACT (land > squash > push; planted feet
# slide back at the glide speed, in place) then AIR (a parabola of height HOP_H). Two hops per loop (the wobble
# alternates L / R; "alternate" lands on L, then on R). Legs: knee (leg.*.1) locked at rest; the hip pivot is solved so a
# planted ankle tracks the floor; the foot bone only holds the sole flat (contact) or lets the pointed tip trail (air).
HOP_S = WALK_N / 2.0 / K.FPS                                  # seconds per hop
HALF_SLIDE = HOP_LEN * CONTACT / 2.0                          # a planted foot slides +-this under the hip (in place)
assert HOP_STYLE in ("together", "alternate"), HOP_STYLE
for side in ("L", "R"):
    LEGS[side]["leg"] = LEGS[side]["ankle"] - LEGS[side]["H"]   # the rigid hip -> ankle vector (knee locked straight)


def sfl(e0, e1, x):
    return float(smoothstep(e0, e1, x))


def peak_warp(v, p):
    """[0, 1] -> [0, 1] with p -> 0.5 (moves a sin(pi v) peak to v = p)."""
    return v ** (math.log(0.5) / math.log(p)) if v > 0.0 else 0.0


def hop_split(s):
    """-> (in contact, v = contact progress | w = air progress)."""
    return (True, s / CONTACT) if s <= CONTACT else (False, (s - CONTACT) / (1.0 - CONTACT))


def squash_y(s):
    """block height scale: fall stretch at impact -> SQUASH (bottoming at SQUASH_PEAK of the contact) -> STRETCH at the
    takeoff instant -> relaxed by mid-air -> FALL_STRETCH into the next landing. Width = 1 / sqrt(height): volume kept."""
    c, x = hop_split(s)
    if c:
        return (1.0 - SQUASH * math.sin(math.pi * peak_warp(x, SQUASH_PEAK)) + STRETCH * sfl(0.5, 1.0, x)
                + FALL_STRETCH * (1.0 - sfl(0.0, 0.3, x)))
    return 1.0 + STRETCH * (1.0 - sfl(0.0, 0.5, x)) + FALL_STRETCH * sfl(0.55, 1.0, x)


def squash_env(s):
    c, x = hop_split(s)
    return math.sin(math.pi * peak_warp(x, SQUASH_PEAK)) if c else 0.0


def air_env(s):
    c, x = hop_split(s)
    return 0.0 if c else math.sin(math.pi * x) ** 2


def air_z(s):
    c, x = hop_split(s)
    return 0.0 if c else HOP_H * 4.0 * x * (1.0 - x)


def lean_env(s):
    c, x = hop_split(s)
    return sfl(0.35, 1.0, x) if c else 1.0 - sfl(0.15, 0.95, x)


def flap_env(s):
    """arm lift: splay out with the squash, fling up at takeoff, float back down through the air."""
    c, x = hop_split(s)
    if c:
        return 0.35 * math.sin(math.pi * peak_warp(x, SQUASH_PEAK)) + sfl(0.55, 1.0, x)
    return 1.0 - sfl(0.0, 0.85, x)


def planted(side, k):
    return HOP_STYLE == "together" or side == ("L" if k == 0 else "R")


def body_rot(u):
    k, s = int(2.0 * u) % 2, (2.0 * u) % 1.0
    sgk = 1.0 if k == 0 else -1.0
    pitch = HOP_LEAN[0] + (HOP_LEAN[1] - HOP_LEAN[0]) * lean_env(s)
    if HOP_STYLE == "together":                            # wobble in the air only: both stiff legs land flat
        roll, yaw = HOP_ROLL * sgk * air_env(s), HOP_YAW * sgk * air_env(s)
    else:                                                  # lean over the stance leg (+ roll tips the top toward +X = L)
        roll = HOP_ROLL * math.cos(TAU2 * (u - CONTACT / 4.0))
        yaw = HOP_YAW * math.sin(TAU2 * (u - CONTACT / 4.0))
    return rotm(UP, yaw) @ rotm((1, 0, 0), pitch) @ rotm((0, 1, 0), roll)


def hip_pt(side, Db, off):
    return HIPS_HEAD + off + Db @ (LEGS[side]["H"] - HIPS_HEAD)


def leg_world(side, theta, splay):
    """world rotation of a stiff leg: hip pitch theta deg (+ = foot back) after an outward splay deg."""
    return rotm((1, 0, 0), theta) @ rotm((0, 1, 0), -LEGS[side]["sg"] * splay)


def vault_theta(side, Db, splay, y_t):
    """the hip pitch (deg) putting the rigid leg's ankle at world y = y_t (a planted foot tracking the floor)."""
    v = rotm((0, 1, 0), -LEGS[side]["sg"] * splay) @ LEGS[side]["leg"]
    d = y_t - hip_pt(side, Db, Vector((0.0, 0.0, 0.0))).y
    R_ = math.hypot(v.y, v.z)
    return math.degrees(-math.acos(max(-1.0, min(1.0, d / R_))) - math.atan2(v.z, v.y))


def contact_theta(side, k, v):
    """(hip pitch, splay) of a leg at contact progress v of hop k."""
    if not planted(side, k):
        return -ALT_KICK, 0.0
    s = v * CONTACT
    splay = LEG_SPLAY * squash_env(s)
    y_t = LEGS[side]["ankle"].y - HALF_SLIDE + 2.0 * HALF_SLIDE * v
    return vault_theta(side, body_rot((k + s) / 2.0), splay, y_t), splay


def hop_legs(u):
    """-> ({side: leg world rotation}, {side: foot world rotation}, hips offset, {side: (pitch, splay)})."""
    k, s = int(2.0 * u) % 2, (2.0 * u) % 1.0
    c, x = hop_split(s)
    Db = body_rot(u)
    Lw, Fw, ang = {}, {}, {}
    for side in ("L", "R"):
        if c:
            th, sp = contact_theta(side, k, x)
        else:                                              # air: takeoff angle -> next landing angle, trailing back
            th0, _ = contact_theta(side, k, 1.0)           #   first, reaching forward last (hip only)
            th1, _ = contact_theta(side, (k + 1) % 2, 0.0)
            th, sp = th0 + (th1 - th0) * sfl(0.0, 1.0, x) + LEG_SWING * math.sin(TAU2 * x), 0.0
        Lw[side] = leg_world(side, th, sp)
        Fw[side] = rotm((1, 0, 0), FOOT_POINT * air_env(s))
        ang[side] = (th, sp)
    # the hips height puts the lowest (planted) ankle exactly at its rest height = sole on z 0, + the hop parabola
    need = [LEGS[sd]["ankle"].z - (hip_pt(sd, Db, Vector((0.0, 0.0, 0.0))) + Lw[sd] @ LEGS[sd]["leg"]).z
            for sd in ("L", "R") if (not c) or planted(sd, k)]
    return Db, Lw, Fw, Vector((0.0, 0.0, max(need) + air_z(s))), ang


ARM_SHARE = [(ARM_BONES - j) / (ARM_BONES * (ARM_BONES + 1) / 2.0) for j in range(ARM_BONES)]   # root-heavy, sums to 1


def walk_pose(u):
    h = harmonics(WALK_N / K.FPS)
    s = (2.0 * u) % 1.0
    Db, Lw, Fw, off, _ = hop_legs(u)
    sy = squash_y(s)
    pose = {"hips": (Db, off, None), "body": (None, None, (1.0 / math.sqrt(sy), sy, 1.0 / math.sqrt(sy)))}
    for side in ("L", "R"):
        pose["leg.%s.0" % side] = (Db.transposed() @ Lw[side], None, None)      # the knee leg.*.1 stays at rest
        pose["leg.%s.%d" % (side, LEG_BONES - 1)] = (Lw[side].transposed() @ Fw[side], None, None)

    def envl(lag):
        return air_env((s - lag) % 1.0)
    for side, sg in (("L", 1.0), ("R", -1.0)):
        f_ = 1.0 if side == "L" else WAND_ARM
        for kb in range(ARM_BONES):
            bn = "arm.%s.%d" % (side, kb)
            sl = (s - ARM_LAG * kb) % 1.0
            D = (toward(bn, BACK, f_ * ARM_TRAIL * ARM_SHARE[kb] * air_env(sl))
                 @ toward(bn, (sg, 0, 0), f_ * ARM_FLAP * ARM_SHARE[kb] * flap_env(sl)))
            if side == "R" and kb == 0:
                D = toward(bn, FWD, WAND_CARRY[0]) @ D
            pose[bn] = (D, None, None)
    pose["wand"] = (toward("wand", FWD, WAND_CARRY[1]), None, None)
    pose.update(flames_pose(u, h, envl))
    return pose, None


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


_dom = np.argmax(Wt, 1)
SOLE = {s: np.nonzero(_dom == J["leg.%s.%d" % (s, LEG_BONES - 1)])[0] for s in ("L", "R")}   # the foot bone's verts


def apply_pose(pose):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        D, off, sy = pose.get(bn, (None, None, None))
        R = REST_R[bn]
        q = (R.transposed() @ D @ R).to_quaternion() if D is not None else Quaternion()
        if q.w < 0.0:
            q.negate()
        pb.rotation_quaternion = q
        pb.location = (R.transposed() @ off) if off is not None else Vector((0.0, 0.0, 0.0))
        pb.scale = (1.0, 1.0, 1.0) if sy is None else tuple(sy) if isinstance(sy, tuple) else (1.0, sy, 1.0)


def bake_clip(name, N, pose_fn):
    """pass 1 (no action bound: nothing overrides the pose) + pass 2 (key the stored channels, frame N+1 = frame 1)."""
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames, guard = [], []
    for f in range(N):
        pose, feet = pose_fn(f / N)                           # feet None = stiff legs already posed (the hop)
        Db, off = pose["hips"][0], pose["hips"][1]
        lift = {"L": 0.0, "R": 0.0}
        for it in range(8):
            short = {"L": 0.0, "R": 0.0}
            if feet is None:                                  # rigid legs: the guard can only lift the hips
                pose["hips"] = (Db, off + Vector((0.0, 0.0, max(lift.values()))), None)
            else:
                for side in ("L", "R"):
                    tip_, F_ = feet[side]
                    D0, D1, D2, short[side] = solve_leg(side, Db, off, tip_ + Vector((0.0, 0.0, lift[side])), F_)
                    pose["leg.%s.0" % side] = (D0, None, None)
                    pose["leg.%s.1" % side] = (D1, None, None)
                    pose["leg.%s.2" % side] = (D2, None, None)
            apply_pose(pose)
            bpy.context.view_layer.update()
            C = eval_coords(low)
            dips = {s: float(C[SOLE[s], 2].min()) for s in ("L", "R")}
            if min(dips.values()) >= -1e-6:                   # (float noise of an exactly-rest foot is not a dip)
                break
            if feet is None:
                d_ = -min(dips.values()) + FLOOR_EPS
                lift = {s: lift[s] + d_ for s in ("L", "R")}
                continue
            for s in ("L", "R"):
                if dips[s] < -1e-6:
                    lift[s] += -dips[s] + FLOOR_EPS
        frames.append({bn: (rig.pose.bones[bn].rotation_quaternion.copy(), rig.pose.bones[bn].location.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
        guard.append({"lift": lift, "iters": it + 1, "reach_short": short})
    act_ = bpy.data.actions.new(name)
    act_.use_fake_user = True
    K.assign_action(rig, act_)
    rows = []
    for f in range(N + 1):
        fr = frames[f % N]
        for bn in KEY_BONES:
            pb = rig.pose.bones[bn]
            q, l_, sc = fr[bn]
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            if bn in LOC_BONES:
                pb.location = l_
                pb.keyframe_insert("location", frame=f + 1)
            if bn in SCALE_BONES:
                pb.scale = sc
                pb.keyframe_insert("scale", frame=f + 1)
            rows.append(list(q) + list(l_) + list(sc))
    for fc in K.action_fcurves(act_):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act_.use_frame_range = True
    act_.frame_start, act_.frame_end = 1, N + 1
    act_.use_cyclic = True
    rig.animation_data.action = None
    return act_, rows, guard


def measure_clip(act_, N):
    """pass 3: play the bound action frame by frame (as the checker does) -> per-frame arrays."""
    K.assign_action(rig, act_)
    out = {"minz_main": [], "minz_wand": [], "sole": {"L": [], "R": []}, "sole_y": {"L": [], "R": []}, "tails": {},
           "root": 0.0, "samples": []}
    TRACK = ["crown_flame.1", "wand_flame"] + LICK_NAMES
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low); Cw = eval_coords(wand)
        out["samples"] += [C[::7], Cw[::5]]
        if f == 1:
            out["first"] = (C, Cw)
        if f == N + 1:
            out["last"] = (C, Cw)
        out["minz_main"].append(float(C[:, 2].min())); out["minz_wand"].append(float(Cw[:, 2].min()))
        for s in ("L", "R"):
            out["sole"][s].append(float(C[SOLE[s], 2].min()))
            out["sole_y"][s].append(float(C[SOLE[s], 1].mean()))
        for bn in TRACK:
            out["tails"].setdefault(bn, []).append(np.array(rig.pose.bones[bn].tail))
        pbs = rig.pose.bones
        for s in ("L", "R"):                       # leg stiffness as played: knee + ankle angles, hip -> ankle length
            y0, y1, y2 = (pbs["leg.%s.%d" % (s, j)].matrix.col[1].to_3d().normalized() for j in range(3))
            out.setdefault("knee_deg", {}).setdefault(s, []).append(math.degrees(y0.angle(y1, 0.0)))
            out.setdefault("ankle_deg", {}).setdefault(s, []).append(math.degrees(y1.angle(y2, 0.0)))
            out.setdefault("leg_len", {}).setdefault(s, []).append((pbs["leg.%s.2" % s].head - pbs["leg.%s.0" % s].head).length)
        out.setdefault("crown_z", []).append(float(pbs["crown"].head.z))
        out.setdefault("body_sy", []).append(float(pbs["body"].scale[1]))
        out["root"] = max(out["root"], (rig.matrix_world @ rig.pose.bones["root"].head).length)
    rig.animation_data.action = None
    out["seam_main_mm"] = round(float(np.linalg.norm(out["first"][0] - out["last"][0], axis=1).max()) * 1000, 6)
    out["seam_wand_mm"] = round(float(np.linalg.norm(out["first"][1] - out["last"][1], axis=1).max()) * 1000, 6)
    return out


def excursion_mm(pts):
    P_a = np.array(pts)
    return round(float(np.linalg.norm(P_a - P_a.mean(0), axis=1).max()) * 1000, 1)


t_clip = time.time()
act_idle, rows_idle, guard_idle = bake_clip("idle", IDLE_N, idle_pose)
act_walk, rows_walk, guard_walk = bake_clip("walk", WALK_N, walk_pose)
DIG["keys"] = sha(np.array(rows_idle + rows_walk))
M_I = measure_clip(act_idle, IDLE_N)
M_W = measure_clip(act_walk, WALK_N)
DIG["clip_samples"] = sha(np.concatenate(M_I["samples"] + M_W["samples"]))
act = act_idle
FL_W = {"FLAME_WILD": FLAME_WILD, "flicker_hz": list(FLICKER_HZ), "lick_sway_deg_stretch": list(LICK_FLICKER),
        "crown_sway_deg_root_tip_stretch": list(CF_FLICKER), "wand_sway_deg_stretch": list(WF_FLICKER)}


def clip_common(M, N, guard):
    return {"frames": [1, N + 1], "seconds": N / K.FPS, "cyclic": True, "seam_main_mm": M["seam_main_mm"],
            "seam_wand_mm": M["seam_wand_mm"], "min_z_all": round(min(min(M["minz_main"]), min(M["minz_wand"])), 5),
            "min_z_main": round(min(M["minz_main"]), 5), "min_z_wand": round(min(M["minz_wand"]), 5),
            "root_offset_max": round(M["root"], 8),
            "floor_guard": {"frames_lifted": int(sum(1 for g in guard if max(g["lift"].values()) > 0)),
                            "max_lift_mm": round(1000 * max(max(g["lift"].values()) for g in guard), 2),
                            "max_iters": max(g["iters"] for g in guard),
                            "max_reach_short_mm": round(1000 * max(max(g["reach_short"].values()) for g in guard), 2)}}


# idle numbers
sole_i = {s: np.array(M_I["sole"][s]) for s in ("L", "R")}
idle_rep = clip_common(M_I, IDLE_N, guard_idle)
idle_rep.update({"status": "REAL: grounded stance, living fire", "flame_flow": FL_W,
                 "flicker_harmonics_per_loop": list(harmonics(IDLE_N / K.FPS)),
                 "feet_planted_sole_min_z_mm": {s: [round(1000 * float(sole_i[s].min()), 2), round(1000 * float(sole_i[s].max()), 2)]
                                                for s in ("L", "R")},
                 "sole_drift_y_mm": {s: round(1000 * float(np.ptp(M_I["sole_y"][s])), 2) for s in ("L", "R")},
                 "body": {"drop": IDLE_DROP, "breath_dip": IDLE_BOB, "sway_roll_pitch_yaw_deg": list(IDLE_SWAY),
                          "side_shift": IDLE_SHIFT},
                 "arm_drift_root_tip_deg": IDLE_ARM,
                 "tip_excursion_mm": {"crown_flame_tip": excursion_mm(M_I["tails"]["crown_flame.1"]),
                                      "wand_flame_tip": excursion_mm(M_I["tails"]["wand_flame"]),
                                      "body_lick_tips_mean": round(float(np.mean([excursion_mm(M_I["tails"][n]) for n in LICK_NAMES])), 1)
                                      if LICK_NAMES else None}})
# walk numbers (v1.3 hop)
sole_w = {s: np.array(M_W["sole"][s]) for s in ("L", "R")}
contact = {s: sole_w[s] <= CONTACT_TOL for s in ("L", "R")}
air = ~contact["L"] & ~contact["R"]
slide = []
for s in ("L", "R"):
    y_ = np.array(M_W["sole_y"][s])
    for f in range(WALK_N):
        if contact[s][f] and contact[s][f + 1]:
            slide.append((y_[f + 1] - y_[f]) * K.FPS)
sy_w = np.array(M_W["body_sy"][:WALK_N])
ang_w = [hop_legs(f / WALK_N)[4] for f in range(WALK_N)]
pitch_w = [a[s][0] for a in ang_w for s in ("L", "R")]
crown_rest = float(REST_H["crown"].z)
clear_w = np.minimum(sole_w["L"], sole_w["R"])
walk_rep = clip_common(M_W, WALK_N, guard_walk)
walk_rep.update({
    "status": "REAL v1.3: stiff-legged cartoon hop (artist 2026-09-26 'legs remains stiff more cartoon movement than "
              "realistic and faster like a little creature hopping around')", "hop_style": HOP_STYLE, "flame_flow": FL_W,
    "stream_in_air": STREAM, "flicker_harmonics_per_loop": list(harmonics(WALK_N / K.FPS)),
    "hops_per_loop": 2, "hop_seconds": HOP_S, "cadence_hops_per_min": round(60.0 / HOP_S, 2),
    "hop_length": HOP_LEN, "implied_speed_units_per_s": round(HOP_LEN / HOP_S, 4),
    "implied_speed_game_m_per_s_at_cell_fit_scale": round(HOP_LEN / HOP_S * k_fit, 4),
    "contact_share": round(CONTACT, 4), "stance_slide_per_foot": round(2 * HALF_SLIDE, 4),
    "measured_planted_foot_speed_units_per_s": {"mean": round(float(np.mean(slide)), 4), "min": round(float(np.min(slide)), 4),
                                                 "max": round(float(np.max(slide)), 4)} if slide else None,
    "contact_frames": {s: [int(f + 1) for f in np.nonzero(contact[s][:WALK_N])[0]] for s in ("L", "R")},
    "air": {"airborne_frames_per_cycle": int(air[:WALK_N].sum()),
            "air_time_per_hop_s": round(float(air[:WALK_N].sum()) / 2 / K.FPS, 4),
            "design_air_s": round((1.0 - CONTACT) * HOP_S, 4),
            "hop_height_design": HOP_H, "foot_clearance_peak_measured": round(float(clear_w.max()), 4)},
    "squash_stretch": {"body_height_scale_min": round(float(sy_w.min()), 4), "body_height_scale_max": round(float(sy_w.max()), 4),
                       "squash_pct": round(100 * (1 - float(sy_w.min())), 1), "stretch_pct": round(100 * (float(sy_w.max()) - 1), 1),
                       "width_at_squash_pct": round(100 * (1 / math.sqrt(float(sy_w.min())) - 1), 1),
                       "pivot": "the hip line z %.3f (legs never scale)" % float(REST_H["body"].z),
                       "crown_drop_at_squash": round(crown_rest - float(min(M_W["crown_z"])), 4),
                       "crown_rise_max": round(float(max(M_W["crown_z"])) - crown_rest, 4),
                       "per_frame_body_height_scale": [round(float(v), 4) for v in M_W["body_sy"]]},
    "stiff_legs": {"knee_deg_max": {s: round(max(M_W["knee_deg"][s]) - min(M_W["knee_deg"][s]), 4) for s in ("L", "R")},
                   "knee_note": "range of the thigh-shin angle over the clip (the knee bone is never posed: 0 = locked)",
                   "ankle_flex_deg_max": {s: round(max(abs(a - math.degrees(bdir("leg.%s.1" % s).angle(bdir("leg.%s.2" % s), 0.0)))
                                                           for a in M_W["ankle_deg"][s]), 3) for s in ("L", "R")},
                   "ankle_note": "max |shin-foot angle - its rest angle|: the foot bone only holds the sole flat / trails",
                   "hip_to_ankle_length_dev_mm": {s: round(1000 * float(np.ptp(M_W["leg_len"][s])), 4) for s in ("L", "R")},
                   "hip_pitch_deg_range": [round(min(pitch_w), 2), round(max(pitch_w), 2)],
                   "splay_deg_max": round(max(a[s][1] for a in ang_w for s in ("L", "R")), 2),
                   "foot_point_deg": FOOT_POINT, "leg_swing_deg": LEG_SWING},
    "sole_min_z_mm": {s: round(1000 * float(sole_w[s].min()), 2) for s in ("L", "R")},
    "lean_deg": list(HOP_LEAN), "wobble_roll_yaw_deg": [HOP_ROLL, HOP_YAW], "arm_flap_trail_deg": [ARM_FLAP, ARM_TRAIL],
    "tip_excursion_mm": {"crown_flame_tip": excursion_mm(M_W["tails"]["crown_flame.1"]),
                         "wand_flame_tip": excursion_mm(M_W["tails"]["wand_flame"])},
    "wand_carry_deg": list(WAND_CARRY)})
rep["clips"] = {"idle": idle_rep, "walk": walk_rep, "seconds": round(time.time() - t_clip, 1),
                "rule": "in place (root at the origin, Conquest glides); closed-form, integer harmonics (exact seams); "
                        "idle legs = analytic 2-bone IK onto planted feet; walk legs = rigid (knee locked), hip pivot + "
                        "hips height solved in closed form so planted soles sit exactly on z 0; floor guard on both "
                        "(no foot mesh below z 0)"}
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("firesprite v1.3 (stiff-leg hop): root > hips (non-deform: travel + tilt) > body (squash / stretch "
                       "about the hip line) > crown > crown_flame.{0,1}; body > arm.{L,R}.{0,1,2}; arm.R.2 > wand > "
                       "wand_flame; hips > leg.{L,R}.{0,1,2 foot} (never scaled); body > lick.{side.L.0-3, side.R.0-3, "
                       "back.0-2, hem.0-5} (one bone per body flame lick)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("idle = grounded living fire (4 s); walk = stiff-legged cartoon hop (1 s, two hops, %s); "
                               "both in place" % HOP_STYLE)
low["conquest_locomotion"] = ("STIFF-LEGGED CARTOON HOP (artist 2026-09-26): a little creature hopping on locked-straight "
                              "flame legs; the block squashes on landing, stretches at takeoff")
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris": report["tris"]["total"], "seconds": round(time.time() - T0, 1),
               "overrides": OVERRIDES, "walk": rep["clips"]["walk"]}, open(DIGEST_ONLY, "w"), indent=1,
              default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 9. glb + skins
for o in scene.objects:
    o.select_set(o in (rig, low, wand))
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, act)
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
                            "colour_sets": sorted(k for k in p["attributes"] if k.startswith("COLOR"))} for n, p in prims],
            "nodes": sorted(n.get("name") for n in js.get("nodes", [])),
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0,
            "extensionsUsed": js.get("extensionsUsed", [])}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'firesprite' (fire BLEND + crown opaque primitives) + skinned "
                           "'firesprite_wand' (own node: bark opaque + flame BLEND); natural scale; report-only cell fit "
                           "%.5f" % k_fit}
geo0 = geometry_digest([low, wand])
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]}}
pal_v = PAL.load(UNIT, "soulfire")
gt_v = glow_tiers(pal_v)
assert gt_v["pass"], gt_v
counts_v = repaint([low, wand], pal_v)
out_v = OUT_RIGGED[:-6] + "__soulfire.blend"
bpy.ops.wm.save_as_mainfile(filepath=out_v, copy=True, compress=True, relative_remap=False)
rep["skins"]["soulfire"] = {"file": out_v, "palette": PAL.table(pal_v), "palette_files": pal_v["files"], "glow_tiers": gt_v,
                            "region_faces": counts_v, "geometry_colour_uv_digest": geometry_digest([low, wand])}
repaint([low, wand], pal_default)
rep["skins"]["repaint_proof"] = {"rule": "a skin is a pure palette swap: same regions/faces/vertices; default restored",
                                 "default_digest_before": geo0, "default_digest_after_restore": geometry_digest([low, wand])}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}, default=str))
print("CLIP", json.dumps(rep["clips"]["idle"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
