"""Fogeist -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/fogeist_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x2: Goo Shield, Fogeist" (section FOGEIST) + the sheet
design/reference/fogeist-character-sheet.png (two rows: SIDE on top, FRONT + BACK below) measured on its single-row copy
design/reference/fogeist-character-sheet-row.png (same pixel scale, common floor; intake design/reference/fogeist/
intake.json: --cuts 660,1340, axes front 309 / back 823 / side 1535). Contract: improve/templates/new_unit/
BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the FRONT / BACK views (they fix the scale; brief). Front = -Y, +X = the
unit's LEFT (front view image right), floor z = 0 at the puddle bottom.
  FRONT view (row sheet):  X = (x_px - 309) / 100 ; z = (478 - y_px) / 100      (face centre x 310.7: the eye midpoint)
  SIDE view (row sheet, the face on image LEFT): drawn LARGER than the front / back (SIDE_K below), so
                           Y = (x_px - 1535) / (100 SIDE_K) ; z = (480 - y_px) / (100 SIDE_K)
  The HEAD's own side profile is drawn at a different ratio again (HEAD_K): head constants map the side about the side
  eye (1468.1, 241.8) px: Y = EYE_Y + (x_px - 1468.1) * HEAD_K / 100 ; z = EYE_Z - (y_px - 241.8) * HEAD_K / 100.
  BACK view: a rear THREE-QUARTER drawing (the crest + tail run ~3.7 u to image right): used for heights + the body's
  back width only.
The views disagree (a painterly sheet); each constant says which view it follows; every disagreement is in the report's
'sheet_deviations'.

Pipeline:
  1. MIST BODY = ONE SDF of smooth-unioned soft lobes: the puddled base (layered flat lobes on the floor), the reclining
     haunch, chest + front lobes, neck, the hooded head (head + snout ellipsoids, a hood ellipsoid cut open at the face,
     two hood flaps), the MIST CREST (a rolling chain of cloud lobes drifting back from the crown + lateral wings), the
     TRAILING TAIL (a chain of flattening lobes along the floor), seeded blunt cloud BULGES on the upper surfaces of the
     crest + body (the layering), and the WISPS (chains of closed flattened blobs) the crest + tail break into; clipped flat by
     the floor. Marching tets = the HIGH (bake source); collapse decimation = the LOW shell; the face zone densified.
  2. Regions on the shell, every boundary iso-cut: core vs mist (group ownership = the smooth-union crease), wisp
     (ownership), the two eyes (front-projected ovals on the smooth face), and SOFT TONE BANDS: the core by height +
     a large-scale facing term (lit / mid / shade), the crest by facing + height (lit / mid / deep). Tone fields use a
     low-frequency normal (the bulge-free base SDF's gradient at a 0.2 u step) and tiny same-tone islands are merged
     into their neighbour (no polka dots).
  3. palette (palettes.py regions -> Col + Col alpha; no glow): the core OPAQUE, the crest + wisps on an alpha-blended
     material (Eevee BLENDED, no transparency overlap: smooth see-through, NEVER dithered grain), flat shaded, Smart UV,
     bake normal + AO from the SDF high (normal against the low's FLAT faces, wired into both materials), AO floored.
  4. RIG: root > body > chest > neck > head; head > crest.001 > .002 > .003; body > tail.001 > .002 > .003 > .004.
     Clips PLACEHOLDER (movement not given): idle = slow mist breathing swell + crest drift wave; walk = an in-place
     drifting glide (the body stays low on the floor, waves travel back along the tail + crest). glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K               # noqa: E402  (read-only use)
import palettes as PAL           # noqa: E402  (read-only use)
import magmoo_sdf as SD          # noqa: E402  (read-only use: the SDF kit)
import fogeist_parts as FP       # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = FRONT-view px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "fogeist"
CHAR_ID = "fogeist"                   # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.03                       # "mist surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.40                       # BAND >= max smooth-union k (CREST_K 0.30) + 2 steps (contract hazard note)
BODY_TRIS = 15000                     # "mist mesh detail": the shell's decimation target (before densify + colour cuts)
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- SCALE RECONCILIATION (brief: the side is drawn larger; the front / back fix the scale).
#   Heights above the floor, front vs side (row sheet px): eye centre front y 300.0 -> 1.78 u vs side y 241.8 -> 2.382
#   at 100 px/u = x1.34; crest top front y 190 (2.88) / intake 2.95 vs side 3.95 = x1.34; head top front y 209 (2.69) vs
#   side y 165 (3.15) = x1.17. The side's BODY / CREST / TAIL are mapped at SIDE_K = 1.34 (the eye + crest-top ratio).
#   The HEAD itself is NOT drawn 1.34x larger: head top -> chin front 209 -> ~338 = 129 px vs side 165 -> 284 = 119 px
#   (x0.92); eye height front 30 px (y 285-314 / 284-314) vs side 25 px (y 230-254) (x0.83); head top -> eye centre 91
#   vs 77 px (x0.85). The head is built at the FRONT's size; its side profile maps at HEAD_K = 129 / 119 = 1.08.
SIDE_K = 1.34                         # side-view px per front-view px (body / crest / tail layout)
HEAD_K = 1.08                         # front / side head-height ratio (the head's side profile)
# ---- EYES (front view, blob bboxes lum < 120): L (image left) x 276-291 y 285-313 c (283.8, 300.0); R x 330-345 y 284-314
#   c (337.6, 299.7) -> face centre x 310.7 (1.7 px right of the intake axis 309: built about the face centre), |x| 0.269,
#   z 1.78; half 0.08 x 0.15. Side view eye c (1468.1, 241.8) -> Y = (1468.1 - 1535) / 134 = -0.50.
EYE_C = (0.269, 1.78)
EYE_AB = (0.080, 0.150)               # "eye size": half-width, half-height (front-projected oval)
EYE_GATE_Y = -0.30                    # the ovals cut the front surface only (in front of this y)
FACE_SUBDIV = 2                       # "eye outline detail": local subdivision rounds round the eyes
FACE_ZONE = 0.10                      # the densified zone reaches this far outside each oval
# ---- HEAD (front: hood top y 209 -> z 2.69; hood outer edge x 232 / 392 at y 300-325 -> |x| 0.78-0.83; hood flaps
#   bottom y 333 -> z 1.45 at x 240-265 / 360-385; face oval x 262-362 (half 0.50), top y 237 (z 2.41), opening into the
#   neck at y ~338. Side about the eye at HEAD_K: snout front x 1437 (Y -0.835) at y 246 (z 1.74); forehead (1451, 202)
#   -> (-0.68, 2.21); crown (1514, 165) -> (0.00, 2.61); back-top (1580, 184) -> (0.71, 2.40); hood back (1605, 260) ->
#   (0.98, 1.58); chin (1475, 283) -> (-0.43, 1.34); snout underside (1450, 280) -> (-0.70, 1.37).
HEAD_C, HEAD_R = (0.0, 0.06, 2.00), (0.60, 0.90, 0.66)        # "head": the rounded inner head (the face's ground)
SNOUT_C, SNOUT_R = (0.0, -0.45, 1.62), (0.42, 0.42, 0.28)     # "snout": the side view's rounded muzzle under the eyes
HOOD_C, HOOD_R = (0.0, 0.22, 2.04), (0.80, 0.82, 0.65)        # "hood": top 2.69 (front), back 1.04, sides 0.80
HOOD_P0, HOOD_N = (0.0, -0.43, 2.12), (0.0, -0.617, -0.787)   # hood opening plane: the side view's hood edge line
#   (1490, 212) -> (1595, 305) px, i.e. (Y -0.43, z 2.12) -> (0.87, 1.10); the hood keeps the side away from the face
FLAP_C, FLAP_R = (0.60, 0.30, 1.72), (0.22, 0.48, 0.30)       # "hood flaps" (L, mirrored): bottom z 1.42, |x| to 0.82
HEAD_K_BLEND, SNOUT_K, HOOD_K, FLAP_K = 0.18, 0.15, 0.12, 0.10   # smooth-union k (hood lip 0.12: v1 run 1 at 0.06 left a
#   deep pinched crease at the face edge that baked as dark normal / AO smudges)
# ---- NECK (front: neck lines x 287 / 340 at y 350-375 -> half 0.26, z 1.03-1.28; side: neck front x 1480 y 318 ->
#   (-0.41, 1.21), x 1485 y 290 -> (-0.37, 1.42))
NECK_A, NECK_B, NECK_RAD = (0.0, -0.10, 1.00), (0.0, -0.12, 1.62), (0.30, 0.27)
NECK_K = 0.18
# ---- CHEST + front lobes (front: chest x 245-380 at y 400 -> half 0.68 at z 0.78; outer lobe lines x 205 / 415 at
#   y 430-455 -> |x| ~1.05, z 0.25-0.48. Side: chest front x 1460 y 355 -> (-0.56, 0.93), x 1440 y 382 -> (-0.71, 0.73),
#   x 1420 y 400 -> (-0.86, 0.60))
CHEST_C, CHEST_R = (0.0, -0.18, 0.55), (0.92, 0.62, 0.60)
PAW_C, PAW_R = (0.85, -0.48, 0.25), (0.42, 0.52, 0.28)       # front lobes (L, mirrored)
CHEST_K = 0.22
# ---- BODY: the reclining haunch (side: the big lobe x 1560-1830 -> Y 0.19-2.20, top y 330-339 -> z 1.05-1.12; back
#   view body under the crest x 796-990 at y 350-370 -> half-width ~0.97) + the upper back joining neck to haunch.
HAUNCH_C, HAUNCH_R = (0.0, 1.05, 0.42), (1.00, 1.15, 0.70)
BACK_C, BACK_R = (0.0, 0.45, 0.92), (0.55, 0.55, 0.35)
BODY_K = 0.28
# ---- PUDDLE: layered flat lobes on the floor (front: x 129-498 at y 470 -> -1.80 / +1.89, z 0.08; image-left lobes
#   x 85-200 at y 430-475 -> |x| 1.1-2.2, up to z ~0.48; side: front edge x 1395-1400 at y 430-456 -> Y -1.04,
#   z 0.18-0.37). (centre, radii); x > 0 entries are mirrored.
PUDDLE = [((0.0, -0.62, 0.10), (0.95, 0.42, 0.22)),
          ((1.10, -0.30, 0.15), (0.75, 0.55, 0.28)),
          ((1.42, 0.55, 0.15), (0.55, 0.80, 0.30)),
          ((1.15, 1.75, 0.12), (0.55, 0.85, 0.24))]
PUDDLE_K = 0.22
# ---- TAIL (side at SIDE_K, top edge per column: x 1740 y 339 -> (1.53, 1.05); 1900 353 -> (2.72, 0.95); 1940 376 ->
#   (3.02, 0.78); 2000 353 -> (3.47, 0.95); 2060 322 -> (3.92, 1.18); 2100 382 -> (4.22, 0.73); 2140 410 -> (4.51, 0.52);
#   2220 387 -> (5.11, 0.69); 2300 415 -> (5.71, 0.49); 2400 430 -> (6.46, 0.37); tip 2440 444 -> (6.75, 0.27). Bottom on
#   the floor to x ~2080, lifted after: 2300 456 -> 0.18, 2400 451 -> 0.22. WIDTH: no true view draws it (the back view
#   is a rear 3/4) -> tapered from the haunch's ~1.0 half-width (back view).
TAIL = [((0.0, 2.00, 0.40), (0.95, 0.75, 0.55)),
        ((0.0, 2.90, 0.35), (0.85, 0.60, 0.46)),
        ((0.0, 3.55, 0.42), (0.74, 0.55, 0.52)),
        ((0.0, 3.95, 0.62), (0.52, 0.40, 0.55)),
        ((0.0, 4.55, 0.30), (0.60, 0.62, 0.30)),
        ((0.0, 5.15, 0.42), (0.46, 0.55, 0.26)),
        ((0.0, 5.75, 0.33), (0.34, 0.45, 0.17))]
TAIL_K = 0.25
# ---- MIST CREST (side at SIDE_K: top x 1480 y 141 -> (-0.41, 2.53); 1540 112 -> (0.04, 2.75); 1640 136 -> (0.78, 2.57);
#   1740 105 -> (1.53, 2.80); 1900 85 -> (2.72, 2.95); 1960 104 -> (3.17, 2.81); 2000 143 -> (3.47, 2.51); 2100 176 ->
#   (4.22, 2.27); 2160 229 -> (4.66, 1.87). Bottom 1640-1960 y 303-328 -> z 1.13-1.32; 1980-2060 y 264-267 -> 1.60;
#   2100-2160 y 282-285 -> 1.46-1.48. FRONT half-widths (left / right of the axis, symmetrised: the views disagree on
#   which side it drifts to): z 2.68 -0.40 / +1.01; z 2.28 -1.08 / +1.92; z 1.78 -1.73 / +2.49; z 1.58 -1.83 / +2.40;
#   z 1.38 -1.70 / +2.36 -> built ~0.7 / ~1.3 / ~2.1 / ~2.1 / ~2.0 (lobes narrow on top, wide low WINGS).
CREST = [((0.0, 0.25, 2.50), (0.55, 0.55, 0.36)),            # crown: rises off the crown behind the forehead (top 2.86)
         ((0.0, 0.85, 2.05), (1.25, 0.70, 0.62)),
         ((0.0, 1.60, 2.05), (1.45, 0.75, 0.75)),
         ((0.0, 2.65, 2.10), (1.45, 0.85, 0.85)),
         ((0.0, 3.45, 2.15), (1.20, 0.60, 0.65)),
         ((0.0, 4.10, 2.05), (0.90, 0.55, 0.42)),
         ((0.0, 4.65, 1.78), (0.60, 0.45, 0.28))]
WINGS = [((1.35, 1.40, 1.65), (0.75, 0.90, 0.50)),            # low lateral wings (front: widest, ~2.1, at z 1.4-1.8;
         ((1.30, 2.60, 1.68), (0.72, 0.90, 0.50))]            #   mirrored)
CREST_K, CROWN_K = 0.30, 0.20
# ---- CLOUD LAYERING: seeded blunt bulges on the upper surfaces (subtle: the sheet's soft layered lobes)
BULGE_SEED = 5                        # "cloud pattern" seed (any integer re-rolls the bulges; deterministic)
CREST_BULGES = (1.3, (0.36, 0.56), (0.07, 0.15), -0.45)   # per unit area, radius range, protrusion range, min normal z
BODY_BULGES = (1.6, (0.22, 0.36), (0.04, 0.09), 0.25)
BULGE_K = 0.24                        # deep blend: no pinched creases between bulges
# ---- WISPS: chains of flattened tapering blobs (lobed ribbons; FP.wisp_blobs): (group, control points, blob count,
#   half-width root -> tip, half-thickness root -> tip, mirrored). Crest end (side x 2120-2320, y 208-272 -> (4.37..5.87,
#   1.6..2.03)); tail end (x 2320-2440, y 406-453 -> (5.87..6.75, 0.2..0.55)); the tail-top wisp (x 2080-2120, y 323-358
#   -> (4.07..4.37, 0.91..1.17)). Strand heights (side): crest wisp x 2200 y 208-252 -> 0.33 u, x 2280 y 225-245 ->
#   0.15 u -> round-ish lumpy sections (never flat ribbons: edge-on they read as spikes). The front view's lateral wisps
#   are NOT modelled as strands (a first pass read as horns); the low wings carry that width.
WISPS = [("crest", [(0.0, 4.45, 2.00), (0.05, 5.05, 2.05), (0.0, 5.50, 1.90), (-0.05, 5.90, 1.66)], 7, (0.28, 0.14),
          (0.22, 0.12), False),
         ("crest", [(0.38, 4.35, 1.66), (0.46, 4.80, 1.58), (0.50, 5.12, 1.64)], 4, (0.20, 0.13), (0.15, 0.10), True),
         ("tail", [(0.0, 5.70, 0.34), (0.05, 6.15, 0.31), (0.0, 6.50, 0.28), (-0.05, 6.80, 0.27)], 6, (0.28, 0.15),
          (0.16, 0.10), False),
         ("tail", [(0.20, 5.62, 0.42), (0.26, 5.90, 0.50), (0.28, 6.15, 0.48)], 3, (0.16, 0.11), (0.12, 0.09), True),
         ("tail", [(0.0, 4.05, 0.95), (0.0, 4.30, 1.00), (0.0, 4.55, 0.90)], 3, (0.28, 0.16), (0.14, 0.10), False)]
WISP_K = 0.10
# ---- TONES (soft regions painted by height / facing on the low-frequency base normal; never per-lobe spots)
NZ_H = 0.20                           # finite-difference step of the tone normal (bigger = smoother tone bands)
CORE_TONE = (0.35, 0.10, 1.25, 0.28)  # core field z + a*nz - b*max(y - 1.5, 0): (a, b, lit above, shade below)
CREST_TONE = (0.35, 0.45, -0.35)      # crest field nz + a*(z - 2.0): (a, lit above, deep below)
MIN_ISLAND = 0.05                     # "tone island floor": a same-tone patch under this area (u^2) joins its neighbour
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_SAMPLES = 48
AO_FLOOR = 0.62                       # "AO floor": baked AO is remapped to [AO_FLOOR, 1] (no black grime on pale mist)
BAKE_CAGE_FLOOR = 0.04                # bake cage floor (v1 run 1: the 0.02 floor over a 0.003 p99 left crack lines in the
                                      #   face's normal map where flat low faces sit outside the high)
# ---- rig
BODY_BONE = ((0.0, 0.60, 0.0), (0.0, -0.15, 0.40))   # body bone head ON the floor (its swell never dips below z 0)
COLUMN_Z = ((0.25, 0.60), (1.00, 1.20), (1.35, 1.55))   # body -> chest -> neck -> head weight blend bands
CREST_PTS = [(-0.10, 2.40), (1.70, 2.05), (3.40, 2.10), (5.90, 1.70)]          # crest chain (y, z)
TAIL_PTS = [(1.60, 0.42), (2.90, 0.40), (4.20, 0.42), (5.40, 0.36), (6.80, 0.30)]  # tail chain (y, z)
TAIL_PARENT_S0 = -0.50                # the tail chain's weights start blending from the body this far before its root
OWN_BLEND = 0.10                      # ownership blend half-width between the crest / column / body-tail weight sets
# ---- clips (PLACEHOLDER: the artist has not given Fogeist's movement or height). 24 fps; integer harmonics per loop ->
#   frame N+1 == frame 1. Rest pose: the puddle bottom on the floor (z 0); nothing ever goes below it.
IDLE_N = 120                          # "idle loop": 5 s
IDLE_SWELL = (0.022, 2)               # "mist breathing": body swell +- (uniform, about the floor point), breaths per loop
IDLE_HEAD = (1.2, 1.0)                # head bob deg (X, lagging the breath) / sway deg (Z, 1 cyc)
IDLE_CREST_X = (1.0, 3.0, 2, 0.9)     # "crest drift": rise / fall wave (root deg, tip deg, cycles, lag rad per bone)
IDLE_CREST_Z = (1.0, 3.5, 1, 0.9)     #   side drift wave
IDLE_TAIL = (1.0, 3.0, 1, 0.9)        # tail sway (Z)
WALK_N = 48                           # "glide loop": 2 s
WALK_TAIL = (3.0, 8.0, 1, 1.0)        # "glide wave": tail side S-wave (root deg, tip deg, cycles, lag) travelling back
WALK_LIFT = (0.03, 2, 1.1)            # tail lift hump (u, >= 0), humps per loop, phase lag per bone
WALK_CREST_X = (1.5, 4.5, 2, 1.0)     # crest streaming wave (rise / fall)
WALK_CREST_Z = (1.5, 5.0, 1, 1.0)     # crest side wave
WALK_LEAN = (2.0, 1.0)                # chest forward lean deg + its pulse deg (2 cyc); head counter-nod keeps the gaze

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
          "design/reference/fogeist-character-sheet.png, measured on fogeist-character-sheet-row.png)", "tier": "regular",
          "tri_budget": TRI_BUDGET, "units": "sheet units: 1 unit = 100 FRONT-view px; side mapped at SIDE_K %.2f (body / "
          "crest / tail) and HEAD_K %.2f (head profile); floor z = 0 at the puddle bottom" % (SIDE_K, HEAD_K),
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

# =========================================================================== 1. mist body: one SDF
t_sdf = time.time()
MIRROR = np.array([-1.0, 1.0, 1.0])
OPS = []           # (name, group, fn, lo, hi, k, base): applied to the grid IN THIS ORDER; base = part of the tone SDF


def add_ell(name, group, c, r, k, base=True):
    c = np.asarray(c, float); r = np.asarray(r, float)
    OPS.append((name, group, lambda P, c=c, r=r: SD.sd_ellipsoid(P, c, r), c - r, c + r, k, base))


def add_pair(name, group, c, r, k, base=True):
    add_ell(name + ".L", group, c, r, k, base)
    add_ell(name + ".R", group, np.asarray(c, float) * MIRROR, r, k, base)


for i, (c_, r_) in enumerate(PUDDLE):
    (add_pair if c_[0] > 0 else add_ell)("puddle.%d" % i, "body", c_, r_, PUDDLE_K if OPS else 0.0)
add_ell("haunch", "body", HAUNCH_C, HAUNCH_R, BODY_K)
add_ell("back", "body", BACK_C, BACK_R, BODY_K)
add_ell("chest", "chest", CHEST_C, CHEST_R, CHEST_K)
add_pair("paw", "chest", PAW_C, PAW_R, CHEST_K)
_na, _nb = np.array(NECK_A, float), np.array(NECK_B, float)
OPS.append(("neck", "neck", lambda P: SD.sd_round_cone(P, _na, _nb, NECK_RAD[0], NECK_RAD[1]),
            np.minimum(_na, _nb) - max(NECK_RAD), np.maximum(_na, _nb) + max(NECK_RAD), NECK_K, True))
add_ell("head", "head", HEAD_C, HEAD_R, HEAD_K_BLEND)
add_ell("snout", "head", SNOUT_C, SNOUT_R, SNOUT_K)
_hc, _hr = np.array(HOOD_C, float), np.array(HOOD_R, float)
_hp0, _hn = np.array(HOOD_P0, float), unit(HOOD_N)


def sd_hood(P):
    """the hood: an ellipsoid shell around the crown / back / sides, cut open at the face by the side view's hood-edge plane"""
    return np.maximum(SD.sd_ellipsoid(P, _hc, _hr), (P - _hp0) @ _hn)


OPS.append(("hood", "head", sd_hood, _hc - _hr, _hc + _hr, HOOD_K, True))
add_pair("flap", "head", FLAP_C, FLAP_R, FLAP_K)
for i, (c_, r_) in enumerate(CREST):
    add_ell("crest.%d" % i, "crest", c_, r_, CROWN_K if i == 0 else CREST_K)
for i, (c_, r_) in enumerate(WINGS):
    add_pair("wing.%d" % i, "crest", c_, r_, CREST_K)
for i, (c_, r_) in enumerate(TAIL):
    add_ell("tail.%d" % i, "tail", c_, r_, TAIL_K)
N_BASE = len(OPS)


def sd_ops(P, ops):
    d = None
    for _, _, fn, _, _, k, _ in ops:
        v = fn(P)
        d = v if d is None else SD.smin(d, v, k)
    return np.maximum(d, -P[:, 2])                 # the floor (grid.floor)


def sd_base(P):
    """the bulge- and wisp-free mist body: the low-frequency ground the tones read their facing from"""
    return sd_ops(P, OPS[:N_BASE])


def off_face(S, N):
    """bulges keep off the face / hood front (the eyes float on smooth ground)"""
    return ~((S[:, 1] < 0.2) & (S[:, 2] < 2.55) & (np.abs(S[:, 0]) < 1.0))


_crest_lobes = [(np.asarray(c, float), np.asarray(r, float)) for c, r in CREST[1:]] + \
               [(np.asarray(c, float) * m, np.asarray(r, float)) for c, r in WINGS for m in (np.ones(3), MIRROR)]
_body_lobes = [(np.asarray(HAUNCH_C, float), np.asarray(HAUNCH_R, float))] + \
              [(np.asarray(c, float), np.asarray(r, float)) for c, r in TAIL[:5]]
BULGES = {"crest": FP.lobe_bulges(_crest_lobes, CREST_BULGES[0], CREST_BULGES[1], CREST_BULGES[2], BULGE_SEED,
                                  CREST_BULGES[3], sd_base, keep=off_face),
          "body": FP.lobe_bulges(_body_lobes, BODY_BULGES[0], BODY_BULGES[1], BODY_BULGES[2], BULGE_SEED + 1,
                                 BODY_BULGES[3], sd_base, keep=off_face)}
for grp, bl in (("crest", BULGES["crest"]), ("tail", BULGES["body"])):
    for j, (c_, r_) in enumerate(zip(bl["C"], bl["R"])):
        gg_ = grp if grp == "crest" else ("body" if c_[1] < 1.6 else "tail")
        OPS.append(("bulge.%s.%d" % (grp, j), gg_, lambda P, c_=c_, r_=r_: SD.sd_sphere(P, c_, r_), c_ - r_, c_ + r_,
                    BULGE_K, False))
WISP_BLOBS = []
for i, (grp, ctrl, nb_, rx_, rz_, mirror) in enumerate(WISPS):
    for m in ((np.ones(3), MIRROR) if mirror else (np.ones(3),)):
        for j, (c_, r_, fr_) in enumerate(FP.wisp_blobs(np.asarray(ctrl, float) * m, nb_, rx_, rz_)):
            lo_, hi_ = FP.blob_box(c_, r_)
            WISP_BLOBS.append((c_, r_))
            OPS.append(("wisp.%s.%d%s.%d" % (grp, i, "R" if m[0] < 0 else "", j), grp + "_wisp",
                        lambda P, c_=c_, r_=r_, fr_=fr_: SD.sd_ellipsoid(P, c_, r_, fr_), lo_, hi_, WISP_K, False))
GROUPS = ["body", "chest", "neck", "head", "tail", "crest", "crest_wisp", "tail_wisp"]
CORE_G = ["body", "chest", "neck", "head", "tail"]
MIST_G = ["crest", "crest_wisp", "tail_wisp"]
WISP_G = ["crest_wisp", "tail_wisp"]
assert {o[1] for o in OPS} == set(GROUPS), sorted({o[1] for o in OPS})
_lo = np.min([o[3] for o in OPS], 0) - 0.05
_hi = np.max([o[4] for o in OPS], 0) + 0.05
_lo[2] = -0.2
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
for nm, gr, fn, lo, hi, k, b in OPS:
    g.apply(fn, lo, hi, k)
g.floor(0.0)
t_poly = time.time()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "high_min_z": round(float(HV[:, 2].min()), 6),
                 "bulges": {k: int(len(v["R"])) for k, v in BULGES.items()}, "bulge_seed": BULGE_SEED,
                 "wisp_blobs": len(WISP_BLOBS), "wisps": len(WISPS), "seconds_fields": round(t_poly - t_sdf, 1),
                 "seconds": round(time.time() - t_sdf, 1)}
del g
assert crumbs["pieces"] == 1 or crumbs["dropped_verts"] < 0.001 * len(HV), ("mist pieces fell off", crumbs)
assert HV[:, 2].min() > -1e-3, "mist high below the floor"
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation + face densify
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(),
                    "seconds": round(time.time() - t, 1)}
assert np.all(_bb_l / _bb_h > 0.98), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(BAKE_CAGE_FLOOR, round(3.0 * float(np.percentile(dev2, 99)), 4))


def face_ovals():
    return [("eye.L", EYE_C[0], EYE_C[1]) + tuple(EYE_AB), ("eye.R", -EYE_C[0], EYE_C[1]) + tuple(EYE_AB)]


bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
for _ in range(FACE_SUBDIV):
    def _in_zone(co):
        return co[1] < EYE_GATE_Y and any(FP.ellipse_front(np.array([co]), cx, cz, ea + FACE_ZONE, eb + FACE_ZONE)[0] > 0.0
                                          for _, cx, cz, ea, eb in face_ovals())
    zf = [f for f in bm.faces if any(_in_zone(v.co) for v in f.verts)]
    ze = list({e for f in zf for e in f.edges})
    bmesh.ops.subdivide_edges(bm, edges=ze, cuts=1, use_grid_fill=True)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.ensure_lookup_table()
for v in bm.verts[_nv0:]:
    v.co = bvh_hi.find_nearest(v.co)[0]
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["retopo"]["face_densify"] = {"rounds": FACE_SUBDIV, "zone": FACE_ZONE, "new_verts": len(LV) - _nv0,
                                    "tris_after": tri_count_F(LF)}
print("RETOPO", json.dumps(report["retopo"]))

# =========================================================================== 3. region fields + iso cuts on the shell
DGR = {}
for gname in GROUPS:
    fs = [fn(LV) for nm, gr, fn, lo, hi, k, b in OPS if gr == gname]
    DGR[gname] = np.min(np.stack(fs, 1), 1)


def own(groups):
    """> 0 where the given groups own the surface (the smooth-union crease is the 0 line)"""
    mine = np.min(np.stack([DGR[g_] for g_ in groups], 1), 1)
    rest = np.min(np.stack([DGR[g_] for g_ in GROUPS if g_ not in groups], 1), 1)
    return rest - mine


def tone_normal(P):
    h = NZ_H
    G = np.stack([sd_base(P + h * e) - sd_base(P - h * e) for e in np.eye(3)], 1)
    return G / np.maximum(np.linalg.norm(G, axis=1, keepdims=True), 1e-9)


NB = tone_normal(LV)
FIELDS = {"mist": own(MIST_G), "wisp": own(WISP_G),
          "tone_core": LV[:, 2] + CORE_TONE[0] * NB[:, 2] - CORE_TONE[1] * np.maximum(LV[:, 1] - 1.5, 0.0),
          "tone_crest": NB[:, 2] + CREST_TONE[0] * (LV[:, 2] - 2.0)}
for nm, cx, cz, ea, eb in face_ovals():
    f_ = FP.ellipse_front(LV, cx, cz, ea, eb)
    FIELDS[nm] = np.where((LV[:, 1] < EYE_GATE_Y) & (np.abs(LV[:, 2] - cz) < 0.5), f_, -1.0)
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


def iso_cut(key, tau, gate=None):
    L = LAY[key]
    eps = 1e-5 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < CUT_SNAP:
                a[L] = tau
            elif tt > 1 - CUT_SNAP:
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


# the snap moves the FIELD value only; snapped vertices keep their position (the cut line sits on the vertex)
TOL = 0.01
is_mist = lambda a, b: a[LAY["mist"]] > -TOL and b[LAY["mist"]] > -TOL
is_core = lambda a, b: a[LAY["mist"]] < TOL and b[LAY["mist"]] < TOL
is_cloud = lambda a, b: is_mist(a, b) and a[LAY["wisp"]] < TOL and b[LAY["wisp"]] < TOL
off_eyes = lambda a, b: is_core(a, b) and all(a[LAY[nm]] < TOL and b[LAY[nm]] < TOL for nm, *_ in face_ovals())
CUTS = [("mist", 0.0, None), ("wisp", 0.0, is_mist)] + [(nm, 0.0, is_core) for nm, *_ in face_ovals()] + \
       [("tone_core", CORE_TONE[2], off_eyes), ("tone_core", CORE_TONE[3], off_eyes),
        ("tone_crest", CREST_TONE[1], is_cloud), ("tone_crest", CREST_TONE[2], is_cloud)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
FADJ = [[] for _ in CF]
for e in bm.edges:
    lf = [f.index for f in e.link_faces]
    if len(lf) == 2:
        L_ = e.calc_length()
        FADJ[lf[0]].append((lf[1], L_)); FADJ[lf[1]].append((lf[0], L_))
FAREA = np.array([f.calc_area() for f in bm.faces])
bm.free()
mist_f = FVAL["mist"] > 0.0
shell_reg = np.full(len(CF), "core", dtype=object)
shell_reg[FVAL["tone_core"] > CORE_TONE[2]] = "core_lit"
shell_reg[FVAL["tone_core"] < CORE_TONE[3]] = "core_shade"
shell_reg[mist_f] = "crest"
shell_reg[mist_f & (FVAL["tone_crest"] > CREST_TONE[1])] = "crest_lit"
shell_reg[mist_f & (FVAL["tone_crest"] < CREST_TONE[2])] = "crest_deep"
shell_reg[mist_f & (FVAL["wisp"] > 0.0)] = "wisp"
for nm, *_ in face_ovals():
    shell_reg[(~mist_f) & (FVAL[nm] > 0.0)] = "eye"


# ---- tone-island merge (lesson 2026-10-10: no polka-dot regions): a connected same-tone patch smaller than MIN_ISLAND
#      joins the neighbouring tone it shares the longest border with (same material class only; eyes / wisps untouched)
CLASS = {"core_lit": 0, "core": 0, "core_shade": 0, "crest_lit": 1, "crest": 1, "crest_deep": 1, "wisp": 2, "eye": 3}


def merge_islands(reg):
    merged = 0
    for _ in range(6):
        seen = np.zeros(len(reg), bool)
        changed = 0
        for s0 in range(len(reg)):
            if seen[s0] or CLASS[reg[s0]] >= 2:
                continue
            comp, stack = [s0], [s0]
            seen[s0] = True
            while stack:
                a = stack.pop()
                for b, _ in FADJ[a]:
                    if not seen[b] and reg[b] == reg[s0]:
                        seen[b] = True; comp.append(b); stack.append(b)
            if FAREA[comp].sum() >= MIN_ISLAND:
                continue
            border = {}
            cs = set(comp)
            for a in comp:
                for b, L_ in FADJ[a]:
                    if b not in cs and CLASS[reg[b]] == CLASS[reg[s0]]:
                        border[reg[b]] = border.get(reg[b], 0.0) + L_
            if border:
                new = max(sorted(border), key=lambda k: border[k])
                for a in comp:
                    reg[a] = new
                changed += 1
        merged += changed
        if not changed:
            break
    return merged


_tone_before = {n: int((shell_reg == n).sum()) for n in CLASS}
n_merged = merge_islands(shell_reg)
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF),
                      "tone_islands_merged": n_merged, "faces_before_merge": _tone_before}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 4. assemble + centre (one closed shell)
ISL = [("shell", CV, CF, list(shell_reg), None)]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
for name, V, F, R, p in ISL:                                # nothing dips below the floor (lesson 2026-10-10)
    assert V[:, 2].min() > -1e-3, (name, float(V[:, 2].min()))
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
REG = ["core_lit", "core", "core_shade", "crest_lit", "crest", "crest_deep", "wisp", "eye"]
MIST_REG = {"crest_lit", "crest", "crest_deep", "wisp"}
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
MATI = [1 if r in MIST_REG else 0 for r in RNAME]

def make_mat(name, kind):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                      # closed solids: single-sided (glTF doubleSided false)
    if kind == "mist":
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        # BLENDED (not firefly / goo_shield's DITHERED: dithered alpha renders as grain, lesson 2026-10-10); one
        # transparent layer only (no overlap: the closed mist's far side never sorts over its near side)
        mat.surface_render_method = "BLENDED"
        if hasattr(mat, "use_transparency_overlap"):
            mat.use_transparency_overlap = False
        if hasattr(mat, "use_transparent_shadow"):
            mat.use_transparent_shadow = False      # a stochastic see-through shadow renders as grain: solid shadow
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
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
            if s_.material.name == UNIT + "_mist":
                s_.material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = \
                    float(pal["material"]["mist_roughness"])
    return out


def glow_check(pal):
    """no glow (brief): no region may carry emission"""
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    return {"skin": pal["skin"], "emissive_regions": lit, "pass": not lit, "rule": "no glow (brief)"}


MAT_CORE = make_mat(UNIT + "_core", "core")
MAT_MIST = make_mat(UNIT + "_mist", "mist")
pal_default = PAL.load(UNIT, "default")
report["glow"] = {"default": glow_check(pal_default)}
assert report["glow"]["default"]["pass"], report["glow"]
for n in REG:                                             # translucency lives on the mist material only
    assert (n in MIST_REG) == ("alpha" in pal_default["regions"][n]), ("alpha on the wrong material", n)

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_CORE); me.materials.append(MAT_MIST)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
PAL.store_regions(me, REG, rid, np.ones(len(FALL)))       # no per-face jitter: flat paint (no speckle on the mist)
report["regions_faces"] = repaint([low], pal_default)
report["alpha"] = {"main": paint_alpha(me, pal_default), "material_alpha": pal_default["material"].get("alpha", 1.0),
                   "mist_render_method": MAT_MIST.surface_render_method,
                   "mist_transparency_overlap": bool(getattr(MAT_MIST, "use_transparency_overlap", None))}
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre at eye height -> the midpoint of the two eye patches (on the midline, -Y)
_eye_f = np.nonzero(rid == REG.index("eye"))[0]
_eye_c = np.array([VALL[FALL[i]].mean(0) for i in _eye_f])
EYES = {"L": _eye_c[_eye_c[:, 0] > 0].mean(0), "R": _eye_c[_eye_c[:, 0] < 0].mean(0)}
assert len(_eye_c[_eye_c[:, 0] > 0]) and len(_eye_c[_eye_c[:, 0] < 0]), "an eye failed to cut"
anchor = np.array([0.0, HEAD_C[1], EYE_C[1]]) - SHIFT
landmark = 0.5 * (EYES["L"] + EYES["R"])
dvec = landmark - anchor
report["facing"] = {"rule": "head centre at eye height -> the midpoint of the two eyes (the face looks -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "eyes_world": {k: v.round(4).tolist() for k, v in EYES.items()},
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

tris_main = tri_count_F(FALL)
_fc = np.array([CV[f].mean(0) for f in CF])
_dg = np.stack([np.min(np.stack([fn(_fc) for nm, gr, fn, lo, hi, k, b in OPS if gr == g_], 1), 1) for g_ in GROUPS], 1)
_fown = np.argmin(_dg, 1)
report["tris"] = {"total": tris_main, "window": TRI_BUDGET,
                  "per_part": {"mist_shell (one closed SDF shell)": tri_count_F(CF)},
                  "per_part_by_owner_group": {g_: int((_fown == j).sum()) for j, g_ in enumerate(GROUPS)},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. One smooth mist shell carries the whole "
                            "budget (soft rounded lobes need density to read round under flat shading; the crest is "
                            "the largest surface); the face zone is densified for the eye outlines." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"per_part": open_e, "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "one closed SDF shell (the wisps are closed tubes smooth-unioned into it)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet": {"front_height": 2.95, "back_height": 2.93, "side_height_drawn": 3.95,
                               "side_height_at_SIDE_K": round(3.95 / SIDE_K, 3), "front_width": 5.35,
                               "side_length_drawn": 10.6, "side_length_at_SIDE_K": round(10.6 / SIDE_K, 3)},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
report["sheet_deviations"] = [
    "SCALE: the side view is mapped at SIDE_K 1.34 (eye-centre height above the floor 2.382 / 1.78 and crest top 3.95 / "
    "2.95); the side's HEAD is not drawn 1.34x (head top->chin x0.92, eye height x0.83, head top->eye x0.85), so the head "
    "is built at the front's size with its side profile at HEAD_K 1.08 -- in the side ortho the head reads ~1.2-1.5x the "
    "sheet's and the whole side row does not overlay 1:1 (by design: the front / back fix the scale)",
    "HEAD TOP: front 2.69 (hood top y 209) vs side at SIDE_K 2.35 (y 165): built 2.69 (front)",
    "SNOUT: the side draws the eye 0.335 u (31 px x HEAD_K) behind the snout front; on the built head (front-view eye "
    "spacing on a 0.60-wide head) the eye lands ~0.18 u behind it",
    "HOOD FLAPS: front bottom z 1.45 (y 333) vs side ~1.27 at SIDE_K (y 309): built 1.42 (front)",
    "CREST DRIFT: the front view biases the crest to image right (+X: -1.83 / +2.40 at z 1.58) while the back view "
    "(a rear three-quarter) streams it ~3.7 u to image right (-X): contradictory lateral drift -> built straight back "
    "(+Y, the side view's direction) and SYMMETRIC (half-widths averaged); lateral wisps mirrored",
    "BACK VIEW: drawn as a rear three-quarter (crest + tail run off to image right), not a true ortho: used for heights "
    "(crest top 2.90) and the body's back width (~0.97 half) only; no back-view overlay row exists (intake ortho_frame "
    "views = front, side)",
    "TAIL WIDTH: no true view draws it (hidden behind the puddle in front; 3/4 in back): tapered 0.95 -> 0.34 half-width "
    "from the back view's body width",
    "WISPS: closed lobed blobs (chains of flattened tapering ellipsoids, smooth-unioned), not volumetrics; the sheet's "
    "many fine curling strands are reduced to 8 lumpy blob strands (3 crest-end, 5 tail incl. mirrored pairs)",
    "FRONT LATERAL WISPS: the front view's image-left low wisps (x 107-122, y 380-450) and image-right trailing wisps "
    "(x 456-612) are not modelled as strands (they read as horns in a first pass); the crest's low wings carry the "
    "front view's width (~2.1 half at z 1.4-1.8)",
    "LIGHTING: the sheet lights the crest brighter on image right (front crest R 163 vs L 118); tones are painted "
    "symmetric (height / facing only)",
    "EYES: simple dark ovals (chip 55,66,82); the sheet's soft reflection band inside each eye and the paler halo "
    "round it are not modelled",
    "INK LINES: the thin contour lines (hood edge, chest / haunch outlines) are not modelled (brief)",
    "TRANSLUCENCY: per-region alpha (%s) on a closed shell, one blended layer: the sheet's soft feathered wisp edges "
    "are hard silhouette edges here; the mist casts a solid (not see-through) shadow" % " / ".join(
        "%s %.2f" % (n, pal_default["regions"][n]["alpha"]) for n in REG if n in MIST_REG),
]
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
low["conquest_source"] = "from scratch: design/reference/fogeist-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = ("natural proportions in sheet units (1 = 100 front-view sheet px; the larger side view "
                                "mapped at 1/%.2f); game scales at import (cell fit report-only)" % SIDE_K)
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; all zero (no glow)"
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; mist "
                                 "material alphaMode BLEND single-sided (Eevee BLENDED, no overlap); core + eyes opaque")
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_FOC = {"face_eyes": ([-1.0, -1.0, 1.15], [1.0, 0.3, 2.85]),
        "mist_crest": ([-2.2, -0.4, 1.1], [2.2, 4.8, 3.0]),
        "body_mass": ([-1.6, -1.1, 0.0], [1.6, 2.4, 1.6]),
        "trailing_tail": ([-1.0, 2.6, 0.0], [1.0, 6.9, 1.3])}
low["conquest_focus"] = json.dumps({
    **{k: [(np.array(a) - SHIFT).tolist(), (np.array(b) - SHIFT).tolist()] for k, (a, b) in _FOC.items()},
    "views": {"face_eyes": [0.0, 6.0], "mist_crest": [70.0, 18.0], "body_mass": [75.0, 8.0], "trailing_tail": [100.0, 10.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "iso_cuts", "tris", "measure", "facing",
                                                            "open_edges", "regions_area_share", "glow", "alpha")},
                                default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake normal + AO (high = the mist SDF)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
HIGH = new_obj(UNIT + "_high", HV_s, HF)
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
for mat_ in (MAT_CORE, MAT_MIST):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_flat()                                   # FLAT reference: the normal map carries flat face -> smooth high
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
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, AO_SAMPLES)):
    for mat_ in (MAT_CORE, MAT_MIST):
        mat_.node_tree.nodes.active = BAKE_NODES[mat_.name][idx]
    scene.cycles.samples = samples
    t = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the shell high->low sampled p99 distance (floor BAKE_CAGE_FLOOR %.2f)" % BAKE_CAGE_FLOOR,
    "resolution": {"normal": RN, "ao": RA}, "ao_samples": AO_SAMPLES,
    "high_tris": len(HF), "high_rule": "the mist SDF high (one shell)",
    "normal_reference": "FLAT low faces (the mist reads smooth under the flat_shaded contract); wired into both materials",
    "uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_dev_p50_p95": [round(float(np.percentile(devn[cov_n & alltex_n], 50)), 4),
                           round(float(np.percentile(devn[cov_n & alltex_n], 95)), 4)],
    "ao_baked_pct_of_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_raw_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_raw_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4),
    "ao_floor": AO_FLOOR})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0
pa[:, :3] = AO_FLOOR + (1.0 - AO_FLOOR) * pa[:, :3]
img_ao.pixels.foreach_set(pa.ravel())
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
# AO + normal into both materials (the whole unit is one smooth SDF shell)
for mat_ in (MAT_CORE, MAT_MIST):
    nt = mat_.node_tree
    tn, ta = BAKE_NODES[mat_.name]
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
                     "provenance": "pixel-sampled from the sheet's five palette chips + drawn-surface samples (see the "
                                   "palette notes)"}
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


NCREST, NTAIL = len(CREST_PTS) - 1, len(TAIL_PTS) - 1
BONES = [("body", P_(BODY_BONE[0]), P_(BODY_BONE[1]), "root"),
         ("chest", P_(BODY_BONE[1]), P_((0.0, -0.15, 1.05)), "body"),
         ("neck", P_((0.0, -0.15, 1.05)), P_((0.0, -0.12, 1.45)), "chest"),
         ("head", P_((0.0, -0.12, 1.45)), P_((0.0, -0.05, 2.60)), "neck")]
for k in range(NCREST):
    (y0, z0), (y1, z1) = CREST_PTS[k], CREST_PTS[k + 1]
    BONES.append(("crest.%03d" % (k + 1), P_((0.0, y0, z0)), P_((0.0, y1, z1)), "head" if k == 0 else "crest.%03d" % k))
for k in range(NTAIL):
    (y0, z0), (y1, z1) = TAIL_PTS[k], TAIL_PTS[k + 1]
    BONES.append(("tail.%03d" % (k + 1), P_((0.0, y0, z0)), P_((0.0, y1, z1)), "body" if k == 0 else "tail.%03d" % k))
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
    e.use_connect = False                          # the tail bones take location keys (the glide lift)
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences): three weight sets blended by soft group ownership (no seam at a group crease)
Pw = CV.copy()                                     # pre-shift coords (the shell is the only island)
DG2 = np.stack([np.min(np.stack([fn(Pw) for nm, gr, fn, lo, hi, k, b in OPS if gr == g_], 1), 1) for g_ in GROUPS], 1)


def own2(groups, among=GROUPS):
    ii = [GROUPS.index(g_) for g_ in groups]; jj = [GROUPS.index(g_) for g_ in among if g_ not in groups]
    return DG2[:, jj].min(1) - DG2[:, ii].min(1)


g_crest = smoothstep(-OWN_BLEND, OWN_BLEND, own2(["crest", "crest_wisp"]))
g_col = smoothstep(-OWN_BLEND, OWN_BLEND, own2(["chest", "neck", "head"], ["chest", "neck", "head", "body", "tail",
                                                                           "tail_wisp"]))
zz, yy = Pw[:, 2], Pw[:, 1]
s1, s2, s3 = (smoothstep(a, b, zz) for a, b in COLUMN_Z)
W_col = np.stack([1.0 - s1, s1 - s2, s2 - s3, s3], 1)                     # body, chest, neck, head
_cp = np.array(CREST_PTS); _cl = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(_cp[:, 0]), np.diff(_cp[:, 1])))])
CREST_LEN = float(_cl[-1])
s_cr = np.interp(yy, _cp[:, 0], _cl, left=-1.0, right=CREST_LEN)
W_cr = K.vine_weights(np.clip(s_cr, -1.0, CREST_LEN), CREST_LEN, NCREST, parent_s0=-0.2)   # col 0 = head
_tp = np.array(TAIL_PTS); _tl = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(_tp[:, 0]), np.diff(_tp[:, 1])))])
TAIL_LEN = float(_tl[-1])
s_tl = np.interp(yy, _tp[:, 0], _tl, left=0.0, right=TAIL_LEN) + np.minimum(yy - _tp[0, 0], 0.0)
W_tl = K.vine_weights(np.clip(s_tl, TAIL_PARENT_S0 - 1.0, TAIL_LEN), TAIL_LEN, NTAIL, parent_s0=TAIL_PARENT_S0)  # col 0 = body
Wt = np.zeros((len(Pw), len(DEFORM)))
for j, nm in enumerate(["body", "chest", "neck", "head"]):
    Wt[:, J[nm]] += (1.0 - g_crest) * g_col * W_col[:, j]
Wt[:, J["body"]] += (1.0 - g_crest) * (1.0 - g_col) * W_tl[:, 0]
for k in range(NTAIL):
    Wt[:, J["tail.%03d" % (k + 1)]] += (1.0 - g_crest) * (1.0 - g_col) * W_tl[:, k + 1]
Wt[:, J["head"]] += g_crest * W_cr[:, 0]
for k in range(NCREST):
    Wt[:, J["crest.%03d" % (k + 1)]] += g_crest * W_cr[:, k + 1]
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
                  "rule": "three sets blended by soft group ownership (+-OWN_BLEND across the smooth-union crease): "
                          "head / neck / chest groups -> body > chest > neck > head by height (COLUMN_Z); body + tail "
                          "groups -> rigkit.vine_weights along the tail chain from the body; crest + crest wisps -> "
                          "vine_weights along the crest chain from the head"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly. Uniform scale only (glTF-safe: no shear).
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
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
CRESTS = ["crest.%03d" % (k + 1) for k in range(NCREST)]
TAILS = ["tail.%03d" % (k + 1) for k in range(NTAIL)]


def wv(u, k, n, spec, phase=0.0):
    return K.vine_wave(u, k, n, spec[0], spec[1], spec[3], spec[2], phase)


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        sp, nb = IDLE_SWELL
        s1_ = 1.0 + sp * math.sin(TAU * nb * u)                       # the whole mist swells about its floor point
        P["body"] = (None, [], (s1_, s1_, s1_))
        P["chest"] = (None, [("X", 0.6 * IDLE_HEAD[0] * math.sin(TAU * nb * u - 0.6))], None)
        P["neck"] = (None, [("X", 0.5 * IDLE_HEAD[0] * math.sin(TAU * nb * u - 1.0))], None)
        P["head"] = (None, [("X", IDLE_HEAD[0] * math.sin(TAU * nb * u - 1.4)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u))],
                     None)
        for k, bn in enumerate(CRESTS):
            P[bn] = (None, [("X", wv(u, k, NCREST, IDLE_CREST_X)), ("Z", wv(u, k, NCREST, IDLE_CREST_Z, 0.5))], None)
        for k, bn in enumerate(TAILS):
            P[bn] = (None, [("Z", wv(u, k, NTAIL, IDLE_TAIL, 0.3))], None)
    else:
        lean, pulse = WALK_LEAN
        P["body"] = (None, [], None)
        P["chest"] = (None, [("X", -lean - pulse * math.sin(TAU * 2 * u))], None)
        P["neck"] = (None, [("X", 0.5 * pulse * math.sin(TAU * 2 * u - 0.8))], None)
        P["head"] = (None, [("X", lean + 0.5 * pulse * math.sin(TAU * 2 * u - 1.6))], None)   # the gaze stays level
        for k, bn in enumerate(CRESTS):
            P[bn] = (None, [("X", wv(u, k, NCREST, WALK_CREST_X)), ("Z", wv(u, k, NCREST, WALK_CREST_Z, 0.4))], None)
        A, nw, lag = WALK_LIFT
        lifts = [A * (0.5 - 0.5 * math.cos(TAU * nw * u - k * lag)) for k in range(NTAIL)]   # >= 0: never below
        prev = 0.0
        for k, bn in enumerate(TAILS):
            P[bn] = ((0.0, 0.0, lifts[k] - prev), [("Z", wv(u, k, NTAIL, WALK_TAIL))], None)
            prev = lifts[k]
    return P


NEW_ACTS = {}
key_rows = []
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1
        P = pose_at(cn, f % N)
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


TIPV = np.nonzero(yy > 6.0)[0]
CRV = np.nonzero(g_crest > 0.5)[0]
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
        rows.append({"minz": float(C[:, 2].min()), "tail_tip_move": float(np.linalg.norm(C[TIPV] - VALL[TIPV], axis=1).max()),
                     "crest_move": float(np.linalg.norm(C[CRV] - VALL[CRV], axis=1).max()),
                     "head_z": float(pbs["head"].head.z), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "tail_tip_max_move": round(float(g_("tail_tip_move").max()), 4),
                    "crest_max_move": round(float(g_("crest_move").max()), 4),
                    "head_z_range": [round(float(g_("head_z").min()), 4), round(float(g_("head_z").max()), 4)]}
    assert clip_rep[cn]["clearance_min_z"] > -2e-3, ("a clip dips below the floor", cn, clip_rep[cn])
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
rig["conquest_rig"] = ("fogeist v1: root > body > chest > neck > head; head > crest.001 > crest.002 > crest.003; "
                       "body > tail.001 > tail.002 > tail.003 > tail.004")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Fogeist's movement or height; open question): "
                               "idle = 5 s slow mist breathing swell (+-%.1f %% about the floor point, 2 breaths) + a crest "
                               "drift wave travelling back + a gentle tail sway; walk = 2 s in-place drifting glide (the "
                               "body stays low on the floor: a side S-wave + a %.2f u lift hump travel down the tail, the "
                               "crest streams, the chest leans into the glide)" % (100 * IDLE_SWELL[0], WALK_LIFT[0]))
low["conquest_locomotion"] = "drifting-glide placeholder: rest pose puddle bottom z 0 per contract; nothing goes below it"
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
                           "roughness": m.get("pbrMetallicRoughness", {}).get("roughnessFactor"),
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "attributes": sorted(p["attributes"])} for n, p in prims],
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'fogeist' (opaque core/eye primitive + alpha-blend mist "
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
