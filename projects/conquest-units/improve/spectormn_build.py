"""Spectormn -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/spectormn_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-11 "NEW UNITS x6" (section SPECTORMN) + the sheet
design/reference/spectormn-character-sheet.png (intake: design/reference/spectormn/intake.json; orthos FRONT + BACK, the
SIDE is a 3/4 view). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft
speed: one pass, render, report). House rule: the rest pose is MIRROR-SYMMETRIC about X = 0.

UNITS: 'sheet units' -- 1 unit = 100 sheet px. Front = -Y, +X = the unit's LEFT (front view image right).
  FRONT view: X = (x_px - 232) / 100 ; z = (819 - y_px) / 100
  BACK view (mirrored into world X): X = -(x_px - 656) / 100 ; z = (821 - y_px) / 100
  SIDE view: a 3/4 drawing (face on image left): depths + the mist's backward trail are ESTIMATES read off it.
Floor z = 0 at the lowest mist wisp (it floats: nothing else comes near the floor).
Measurements: service venv PIL on the sheet; 'white' = R > 185, G > 175, B > 170, R - B < 40; 'red' = R > 110,
R - G > 70, R - B > 60 (components closed + filled, PCA for oval axes / tilt); 'mist' = |rgb - background 30,30,33| > 26
and R - G <= 60 (the chain glow halo excluded).

Pipeline:
  1. CLOTH GHOST = ONE SDF: an ELLIPTIC LATHE of the measured profile (pointed hood -> shoulders -> slim waist -> the robe
     skirt widening into the mist, trailing back), modulated by soft vertical FOLD ridges (integer cos harmonics of the
     front angle: symmetric, never ringed); smooth-union the shoulder drapes, the two SLEEVES (round cones shoulder ->
     elbow -> wrist, hanging down and out) and the mist WISPS (chains of closed flattened blobs flowing to the floor).
     Marching tets = the HIGH; the eyes + chest slits are SUNKEN into the high (vertices inside each front-projected
     oval pushed in along the normal by a smooth dish); collapse decimation = the LOW; the face zone densified.
  2. Regions on the shell, every boundary iso-cut: CLOTH vs MIST (the TATTERED HEM: seeded pointed tatters hanging off
     the hem line; sleeves + shoulders are cloth), the two EYES + three SLITS (glowing), FOLD shadows (the fold grooves +
     the sleeve / drape undersides), the mist's alpha BANDS by height (veil -> hi -> lo -> fade) and its purple / pink-red
     STREAKS (vertical, from the same even angle harmonics). Tiny same-tone islands merge into a neighbour (no polka dots).
  3. Parts (closed, mirrored L -> R): a dark wrist SHACKLE ring round each sleeve end; from each shackle TWO hanging
     strands of 8 glowing red CHAIN LINKS (stadium tubes, link planes alternating 90 deg) = 16 links per wrist.
  4. palette (palettes.py regions -> Col + Col alpha / Glow): cloth + glow + metal OPAQUE (material spectormn_cloth); the
     mist regions on spectormn_mist = Eevee BLENDED alpha (no dither grain), no transparency overlap, no transparent
     shadow (fogeist approach). Flat shaded, Smart UV, bake normal + AO from the high (+ the closed parts).
  5. RIG: root > body > chest > head; chest > arm_upper.L/R > arm_fore.L/R > chain.L/R.001 > .002; body > mist.001 >
     mist.002. Clips PLACEHOLDER (movement not given): idle = hover bob + chain sway + mist drift; walk = a hover drift
     lean with trailing chains + mist. glb export.
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
import spectormn_parts as FP     # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "spectormn"
CHAR_ID = "spectormn"                 # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "cloth surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.30                       # BAND >= max smooth-union k (SHOULDER_K 0.18) + 2 steps (contract hazard note)
BODY_TRIS = 9500                      # "cloth mesh detail": the shell's decimation target (before densify + colour cuts)
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- BODY LATHE rows (z, half-width, half-depth, y-centre). Half-widths = the FRONT white-cloth runs (body only, the
#   arms split off below z 4.6), cross-checked with the mirrored BACK (agrees to ~0.03): hood tip front y 146 (z 6.73),
#   y 161 -12..14 px (0.13), y 176 -23..28 (0.255), y 191 -28..38 (0.33), y 206 (0.40), y 221 (0.46), y 236 -47..55
#   (0.51), y 251 (0.55); shoulders y 266 -70..71 (0.70) .. y 311 (0.96: the shoulder + upper arm, built by the SLEEVE +
#   SHOULDER lobes, the lathe stays ~0.70); waist (between the arms) y 386 -0.59..0.56, y 401 -0.45..0.55, y 416
#   -0.46..0.54, y 446 -0.61..0.69, y 461 -0.66..0.66; then the mist mass (non-background, the chain halo excluded)
#   y 480 +-0.82, y 520 +-0.90, y 560 -0.97..1.04, y 620 -1.14.., y 660 -1.34.., y 740 -1.12..0.77 (+ wisps to 1.73),
#   y 760 -1.09..1.44, y 780 -0.55..1.31. Half-DEPTHS: the 3/4 side only (no true side ortho): head 0.82 x the width
#   (3/4 width at z 5.79 is 0.83 vs front 1.02), torso ~0.40-0.47, the skirt 0.70 x its width. Y-CENTRE: the side view
#   draws the lower mist trailing BACK (its 3/4 mist spans -0.61..0.86 at z 2.95 but 0.37..1.68 at z 1.75): ~0.5 at
#   the bottom (estimate).
BODY_ROWS = [(6.73, 0.00, 0.00, 0.00), (6.58, 0.13, 0.12, 0.00), (6.43, 0.25, 0.22, 0.00), (6.28, 0.33, 0.29, 0.00),
             (6.13, 0.40, 0.35, 0.00), (5.98, 0.46, 0.40, 0.00), (5.83, 0.51, 0.45, 0.00), (5.68, 0.55, 0.48, 0.00),
             (5.53, 0.60, 0.50, 0.00), (5.30, 0.66, 0.51, 0.00), (5.05, 0.70, 0.51, 0.00), (4.80, 0.70, 0.50, 0.00),
             (4.55, 0.64, 0.48, 0.00), (4.30, 0.56, 0.45, 0.00), (4.10, 0.52, 0.44, 0.00), (3.90, 0.55, 0.45, 0.00),
             (3.65, 0.65, 0.50, 0.01), (3.40, 0.80, 0.58, 0.02), (3.10, 0.88, 0.63, 0.04), (2.80, 0.96, 0.69, 0.07),
             (2.50, 1.04, 0.75, 0.11), (2.35, 1.06, 0.77, 0.13), (2.25, 0.00, 0.00, 0.14)]
#   The ROBE lathe ends just below the deepest tatter tip (HEM_TOP - HEM_DEPTH[1] = 2.50): the TATTERED HEM clips it.
# ---- MIST lathe (z, half-width, half-depth, y-centre): the translucent column the robe's tatters hang over. It starts
#   INSIDE the robe (pole z 3.75) and runs ~0.10-0.12 u inside the robe's surface down the tatter band (so the opaque
#   tatter points stand proud of it and break the outline), then widens as the front mist mass does (y 560 -0.97..1.04,
#   y 620 -1.14, y 660 -1.34, y 740 +-1.12 + strands to 1.73) and closes at z 0.55; the STRANDS carry it to the floor.
MIST_ROWS = [(3.75, 0.00, 0.00, 0.00), (3.60, 0.50, 0.38, 0.01), (3.40, 0.66, 0.47, 0.02), (3.10, 0.76, 0.53, 0.04),
             (2.80, 0.85, 0.59, 0.07), (2.50, 0.93, 0.65, 0.11), (2.20, 1.01, 0.71, 0.16), (1.90, 1.09, 0.77, 0.22),
             (1.60, 1.17, 0.83, 0.28), (1.30, 1.23, 0.87, 0.35), (1.00, 1.22, 0.87, 0.42), (0.80, 1.10, 0.79, 0.46),
             (0.62, 0.90, 0.65, 0.49), (0.48, 0.62, 0.46, 0.51), (0.40, 0.30, 0.24, 0.52), (0.36, 0.00, 0.00, 0.52)]
# ---- FOLDS ("soft vertical fold ridges"): radial modulation = amp(z) x fold_field(theta_f, z); terms (m harmonic,
#   weight, height variation, its wave number, phase). Integer m: even in theta_f -> mirror-symmetric, smooth at both
#   midlines. amp: 0 above the chest (z FOLD_Z[0]), FOLD_AMP[0] on the robe by FOLD_Z[1], FOLD_AMP[1] in the mist by
#   FOLD_Z[3], fading out at the bottom pole (FOLD_Z[4..5]).
FOLD_TERMS = [(7, 1.0, 0.35, 1.3, 0.4), (11, 0.6, 0.45, 2.1, 1.9), (4, 0.45, 0.30, 0.9, 2.7)]
FOLD_AMP = (0.045, 0.060)             # "fold depth": robe / mist (u)
FOLD_Z = (4.50, 3.95, 2.80, 2.00, 0.70, 0.40)
# ---- SHOULDER drapes (front: the sheet falls from the hood y 251 (0.55) to the shoulders y 296 (0.89) and y 311 (0.96))
SHOULDER_C, SHOULDER_R = (0.46, 0.0, 5.13), (0.42, 0.40, 0.30)    # L; mirrored
SHOULDER_K = 0.18
# ---- SLEEVES (front, arm runs left of the body: centre / half-width at y 371 (z 4.48) 1.03 / 0.25, y 401 (4.18)
#   1.16 / 0.23, y 416 (4.03) 1.245 / 0.235, y 431 (3.88) 1.31 / 0.20, y 446 (3.73) 1.40 / 0.165, y 461 (3.58) 1.47 /
#   0.14 -> a straight arm 26 deg off vertical; perpendicular radius = 0.9 x the run half-width. Shoulder extrapolated to
#   z 5.10 (x 0.73), wrist z 3.40 (x 1.57). Forward lean (y) from the 3/4 side: the wrists hang in front of the body.)
ARM_S, ARM_E, ARM_W = (0.73, 0.00, 5.10), (1.12, -0.10, 4.30), (1.57, -0.25, 3.40)    # shoulder, elbow, wrist (L)
ARM_R = (0.27, 0.23, 0.14)            # "sleeve thickness": radius at shoulder / elbow / wrist
SLEEVE_TIP = (0.07, 0.11)             # the gathered cloth poking through the shackle: length past the wrist, radius
ARM_K, ARM_BODY_K = 0.06, 0.14
# ---- MIST STRANDS ("flowing wisps reaching toward the floor"): thin TAPERING strands of closed rounded blobs (never a
#   knife edge: section ratio <= 1.6) hanging off the mist's lower body, flaring out then curling, of varied length
#   (front: the mist is widest, +-1.73, at z 0.8-1.0 (y 720-740), back in to +-1.4 by z 0.59 (y 760), curling to the floor
#   y 780-819). Seeded over theta_f [0, 180] deg (the two midline strands are their own mirrors), mirrored.
WISP_SEED = 11                        # "mist strand pattern" seed (any integer re-rolls it; deterministic)
WISP_N = 11                           # strands per half (front midline .. back midline) -> 20 round the mist
WISP_ROOT_Z = (1.00, 1.60)            # strand root height range (on the mist body, 0.92 x its radius)
WISP_TIP_Z = (0.06, 0.48)             # "strand length": tip height range (the lowest strand defines the floor)
WISP_FLARE = (1.00, 1.14)             # mid-strand radius factor (outward flare) range
WISP_CURL = 22.0                      # tip curl round the mist (deg, alternating sign)
WISP_NBLOB = 7                        # blobs per strand
WISP_W = (0.17, 0.07)                 # "strand width": half-width root -> tip
WISP_T = (0.11, 0.055)                # half-thickness root -> tip
WISP_K = 0.07
# ---- EYES (front, red blobs; PCA): L (image left) c (224.7, 231.6) semi-axes 19.7 x 8.5 px tilt -1.4 deg; R c (257.1,
#   231.9) 19.8 x 6.0 px tilt -3.4 deg -> face centre x 240.9 (0.09 to image right of the body axis 232: the face is
#   drawn slightly turned; built about X = 0), |x| 0.162, z 5.873; half 0.072 x 0.198; tilt ~0 (the two disagree in
#   sign) -> 0. The FACE / EYES panel: oval, sunken, soft glow.
EYE_C = (0.162, 5.873)
EYE_AB = (0.072, 0.198)               # "eye size": half-width, half-height (front-projected oval)
EYE_DEPTH = 0.050                     # "eye socket depth": the dish the oval is sunk by (along the surface normal)
# ---- CHEST SLITS (front, red blobs; PCA): c (209.3, 343.7) 27.8 x 5.5 px tilt +12.6; c (241.2, 343.7) 28.5 x 4.1 px
#   tilt -0.4; c (270.0, 339.8) 24.3 x 4.2 px tilt -8.0 (tops lean OUTWARD both sides) -> about the face centre x 240.9:
#   outer |x| 0.304 (z 4.773, half 0.048 x 0.260, tilt 10.3 avg), centre x 0 (z 4.753, half 0.042 x 0.285).
SLITS = [((0.0, 4.753), (0.042, 0.285), 0.0), ((0.304, 4.773), (0.048, 0.260), 10.3)]   # (c, half, tilt deg); outer mirrored
SLIT_DEPTH = 0.040                    # "slit depth"
FEATURE_GATE_Y = -0.10                # the ovals cut / sink the FRONT surface only (in front of this y)
FACE_SUBDIV = 1                       # "eye / slit outline detail": local subdivision rounds round each oval
FACE_ZONE = 0.06                      # the densified zone reaches this far outside each oval
# ---- TATTERED HEM (front: the white cloth breaks up below y 476 (z 3.43); tatter tips at x 0.44-0.49 z 2.53-2.68, x 0.0
#   z 2.83, x -0.32 z 2.98; back: tips z 2.25-3.45). Tatters over theta_f [0, pi] (front -> back midline), mirrored.
HEM_TOP = 3.45                        # "hem line": the cloth is solid above this
HEM_SEED = 7                          # "tatter pattern" seed (any integer re-rolls it; deterministic)
HEM_N = 7                             # tatters per half (front midline .. back midline): 12 points round the robe
HEM_DEPTH = (0.45, 0.95)              # "tatter length" range (u)
HEM_WIDTH = (0.28, 0.38)              # tatter half-width range (rad of theta_f)
HEM_POWER = 1.4                       # > 1: concave sides -> pointed tips
#   GEOMETRY (gate 2026-10-11): the robe solid is CLIPPED at z = HEM_TOP - hem_drop(theta_f): the opaque cloth ends in
#   real hanging points (with a cut underside) over the inset mist column, so the outline breaks into points
# ---- TONES
NZ_H = 0.12                           # finite-difference step of the low-frequency tone normal (the lathe + arms SDF)
FOLD_T = 0.18                         # "fold shadow width": groove where -fold_field > FOLD_T (robe below the chest)
UNDER_NZ = 0.50                       # the sleeve / drape UNDERSIDES (tone normal z < -UNDER_NZ) take the fold shadow
STREAK_TERMS = [(7, 1.0, 3.6, 0.60), (11, 0.8, 2.4, -0.90), (17, 0.5, 0.3, 1.20)]   # (m, weight, phase, lean / u)
STREAK_PINK = 0.35                    # "pink-red streak share": pink where streak_field > STREAK_PINK
STREAK_PURPLE = -0.35                 # "purple streak share": purple where streak_field < STREAK_PURPLE; lavender between
#   the mist's colour + alpha GRADIENT down the height is per corner (palette material 'mist_grade'): no band steps
MIN_ISLAND = 0.03                     # "tone island floor": a same-tone patch under this area (u^2) joins its neighbour
# ---- CHAIN (front, left wrist: shackle dark blobs c (58,459) (80,481) (35,504) (94,509) -> x -1.67, z ~3.38; the red
#   chain component x 27-104, y 479-710 -> x -2.05..-1.28, z 3.40..1.09: TWO strands hang from the shackle, centres x
#   52 / 85 px (-1.80 / -1.47) with FREE ends (y ~700 / ~690); ~8 links per strand, links alternate face-on / edge-on
#   (CHAIN RESTRAINTS panel). Back view: x 457-522 / 784-857 (0.65-0.73 wide). Built from the L wrist, mirrored.)
SHACKLE = (0.17, 0.050, 0.02)         # "shackle size": ring centre-line radius, tube radius, offset up the arm from the wrist
CHAIN_LINKS = 8                       # links per strand (2 strands per wrist = 16)
LINK = (0.175, 0.090, 0.040)          # "link size": half-length (to the end-curve centre-line), half-width, wire radius
LINK_PITCH = 0.258                    # link spacing = 2 x (half-length - 1.15 x wire): the links nest without touching
LINK_SEG = (5, 6)                     # samples per link end-curve, tube sides
STRAND_SPLAY = (-0.16, 0.16)          # strand bottom x offset from the wrist: outer / inner strand (front x -1.80 / -1.47)
STRAND_BOTTOM_Z = 1.15                # strand path bottom (sheet chain bottom z 1.09)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_SAMPLES = 32
AO_FLOOR = 0.62                       # "AO floor": baked AO remapped to [AO_FLOOR, 1] (no grime on white cloth)
BAKE_CAGE_FLOOR = 0.04
# ---- rig
COLUMN_EDGES = ((1.05, 1.35), (2.30, 2.65), (4.30, 4.55), (5.40, 5.70))   # mist.002 | mist.001 | body | chest | head
OWN_BLEND = 0.06                      # sleeve ownership blend half-width (u) across the sleeve / body crease
ARM_ROOT_T = (-0.05, 0.15)            # chest -> arm_upper blend along the arm (fraction shoulder -> wrist)
ELBOW_BLEND = 0.08                    # arm_upper -> arm_fore blend half-width (fraction) round the elbow
# ---- clips (PLACEHOLDER: the artist has not given Spectormn's movement or height). 24 fps; integer harmonics per loop
#   -> frame N+1 == frame 1. Rest: the lowest wisp on the floor (z 0); every clip lifts (never below it).
IDLE_N = 96                           # "idle loop": 4 s
IDLE_LIFT = (0.20, 0.10, 2)           # "hover bob": base lift + bob height (u, >= 0), bobs per loop
IDLE_SWAY = (1.2, 1.0)                # body roll deg (Y) / chest breath deg (X)
IDLE_HEAD = (1.5, 2.0)                # head tilt deg (Y) / turn deg (Z)
IDLE_ARM = 2.0                        # arm sway deg (X), lagging the bob
IDLE_CHAIN = (5.0, 3.0, 1.1)          # "chain sway": swing deg fore-aft (X) / sideways (Y) per bone, lag rad per bone
IDLE_MIST = (2.0, 3.0, 0.9)           # "mist drift": deg on mist.001 / mist.002 (X + Y waves), lag rad
WALK_N = 48                           # "drift loop": 2 s
WALK_LIFT = (0.45, 0.04)              # mean lift + bob (2 per loop)
WALK_LEAN = (7.0, 1.0)                # "drift lean": body forward deg + its pulse deg; the head counter-nods half
WALK_ARM = 6.0                        # sleeves trail back deg
WALK_CHAIN = (14.0, 8.0, 3.0)         # "trailing chains": chain.001 / .002 trail deg (back) + their wobble deg
WALK_MIST = (6.0, 8.0, 2.0)           # mist.001 / .002 trail deg + wobble deg

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
          "design/reference/spectormn-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 sheet px (front + back true orthos; the side is a 3/4 view); floor z = 0 "
                   "at the lowest mist wisp (it floats)", "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
MIRROR = np.array([-1.0, 1.0, 1.0])


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


def vertex_normals(V, F):
    N = np.zeros_like(V)
    Fa = np.asarray(F)
    fn = np.cross(V[Fa[:, 1]] - V[Fa[:, 0]], V[Fa[:, 2]] - V[Fa[:, 0]])
    for k in range(3):
        np.add.at(N, Fa[:, k], fn)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. cloth ghost: one SDF
t_sdf = time.time()
LATHE = FP.Profile(BODY_ROWS)


def fold_amp(z):
    return (FOLD_AMP[0] * smoothstep(FOLD_Z[0], FOLD_Z[1], z) + (FOLD_AMP[1] - FOLD_AMP[0]) * smoothstep(FOLD_Z[2], FOLD_Z[3], z)) \
        * smoothstep(FOLD_Z[5], FOLD_Z[4], z)


def sd_lathe(P):
    return LATHE.sd(P)


MISTP = FP.Profile(MIST_ROWS)
TAT = FP.tatters(HEM_SEED, HEM_N, HEM_DEPTH, HEM_WIDTH)


def hem_clip(P, th):
    """> 0 below the tattered hem line (the robe is cut away there)"""
    return (HEM_TOP - FP.hem_drop(th, TAT, HEM_POWER)) - P[:, 2]


def sd_robe_base(P):
    return np.maximum(LATHE.sd(P), hem_clip(P, LATHE.theta_f(P)))


def sd_body(P):
    """the robe lathe minus the soft fold ridges (radial modulation, even in theta_f), CLIPPED by the tattered hem"""
    th = LATHE.theta_f(P)
    return np.maximum(LATHE.sd(P) - fold_amp(P[:, 2]) * FP.fold_field(th, P[:, 2], FOLD_TERMS), hem_clip(P, th))


def sd_mist_base(P):
    return MISTP.sd(P)


def sd_mist(P):
    """the inset mist column with the same soft flowing ripples (the mist amplitude)"""
    th = MISTP.theta_f(P)
    return MISTP.sd(P) - fold_amp(P[:, 2]) * FP.fold_field(th, P[:, 2], FOLD_TERMS)


OPS = []           # (name, group, fn, lo, hi, k, base): applied to the grid IN THIS ORDER; base = part of the tone SDF
_rows = np.array(BODY_ROWS)
_bl = np.array([-_rows[:, 1].max(), (_rows[:, 3] - _rows[:, 2]).min(), _rows[:, 0].min()]) - 0.1
_bh = np.array([_rows[:, 1].max(), (_rows[:, 3] + _rows[:, 2]).max(), _rows[:, 0].max()]) + 0.1
OPS.append(("body", "body", sd_body, _bl, _bh, 0.0, True))


def add_ell(name, group, c, r, k, base=True, frame=None):
    c = np.asarray(c, float); r = np.asarray(r, float)
    m = float(r.max())
    OPS.append((name, group, lambda P, c=c, r=r, fr=frame: SD.sd_ellipsoid(P, c, r, fr), c - m, c + m, k, base))


def add_cone(name, group, a, b, ra, rb, k, base=True):
    a = np.asarray(a, float); b = np.asarray(b, float)
    m = max(ra, rb)
    OPS.append((name, group, lambda P, a=a, b=b, ra=ra, rb=rb: SD.sd_round_cone(P, a, b, ra, rb),
                np.minimum(a, b) - m, np.maximum(a, b) + m, k, base))


for sg, sd_ in ((1.0, "L"), (-1.0, "R")):
    M_ = np.array([sg, 1.0, 1.0])
    add_ell("shoulder." + sd_, "shoulder", np.array(SHOULDER_C) * M_, SHOULDER_R, SHOULDER_K)
for sg, sd_ in ((1.0, "L"), (-1.0, "R")):
    M_ = np.array([sg, 1.0, 1.0])
    S_, E_, W_ = (np.array(p_) * M_ for p_ in (ARM_S, ARM_E, ARM_W))
    d_ = unit(W_ - E_)
    add_cone("arm_upper." + sd_, "arm", S_, E_, ARM_R[0], ARM_R[1], ARM_BODY_K)
    add_cone("arm_fore." + sd_, "arm", E_, W_, ARM_R[1], ARM_R[2], ARM_K)
    add_cone("sleeve_tip." + sd_, "arm", W_, W_ + d_ * SLEEVE_TIP[0], ARM_R[2], SLEEVE_TIP[1], ARM_K)
_mr = np.array(MIST_ROWS)
OPS.append(("mist", "mist", sd_mist, np.array([-_mr[:, 1].max(), (_mr[:, 3] - _mr[:, 2]).min(), _mr[:, 0].min()]) - 0.1,
            np.array([_mr[:, 1].max(), (_mr[:, 3] + _mr[:, 2]).max(), _mr[:, 0].max()]) + 0.1, 0.0, True))   # hard union:
#   the tatter gaps stay open (a smooth union would web the points to the mist)
N_BASE = len(OPS)
WISP_BLOBS, WISP_INFO = [], []
_wr = np.random.default_rng(WISP_SEED)
_wth = np.linspace(0.0, 180.0, WISP_N)
for i, th_d in enumerate(_wth):
    if 0 < i < WISP_N - 1:
        th_d += _wr.uniform(-0.3, 0.3) * (180.0 / (WISP_N - 1))
    z0_ = _wr.uniform(*WISP_ROOT_Z); z1_ = _wr.uniform(*WISP_TIP_Z); fl_ = _wr.uniform(*WISP_FLARE)
    curl = WISP_CURL * (1.0 if i % 2 == 0 else -1.0) * (0.0 if i in (0, WISP_N - 1) else 1.0)
    WISP_INFO.append({"theta_deg": round(float(th_d), 1), "root_z": round(z0_, 3), "tip_z": round(z1_, 3),
                      "flare": round(fl_, 3)})
    for sg in ((1.0, -1.0) if 0 < i < WISP_N - 1 else (1.0,)):
        ctrl = []
        for t_, f_ in ((0.0, 0.90), (0.40, fl_), (0.75, fl_), (1.0, 0.5 * (fl_ + 0.9))):
            z_ = z0_ + (z1_ - z0_) * t_
            th = math.radians(th_d + curl * t_ ** 2)
            zr_ = max(z_, 0.70)                                  # radius read at >= the mist's last full row
            w_ = float(np.interp(zr_, MISTP.z, MISTP.w)); d_ = float(np.interp(zr_, MISTP.z, MISTP.d))
            yc_ = float(np.interp(zr_, MISTP.z, MISTP.yc))
            ctrl.append((sg * f_ * w_ * math.sin(th), yc_ - f_ * d_ * math.cos(th), z_))
        C_ = np.array(ctrl)
        th0 = math.radians(th_d)
        face = unit(np.array([sg * math.sin(th0), -math.cos(th0), 0.0]))      # broad side outward (radial)
        for j, (c_, r_, fr_) in enumerate(FP.wisp_blobs(C_, WISP_NBLOB, WISP_W, WISP_T, face)):
            lo_, hi_ = FP.blob_box(c_, r_)
            WISP_BLOBS.append((c_, r_))
            OPS.append(("strand.%d%s.%d" % (i, "R" if sg < 0 else "", j), "wisp",
                        lambda P, c_=c_, r_=r_, fr_=fr_: SD.sd_ellipsoid(P, c_, r_, fr_), lo_, hi_, WISP_K, False))
GROUPS = ["body", "shoulder", "arm", "mist", "wisp"]
assert {o[1] for o in OPS} == set(GROUPS), sorted({o[1] for o in OPS})


def sd_ops(P, ops):
    d = None
    for _, _, fn, _, _, k, _ in ops:
        v = fn(P)
        d = v if d is None else SD.smin(d, v, k)
    return d


def sd_base(P):
    """the fold- and strand-free ghost (clipped robe + shoulders + sleeves + mist column): the tones' ground"""
    ops = [("robe", "body", sd_robe_base, None, None, 0.0, True)] + OPS[1:N_BASE - 1] + \
          [("mist", "mist", sd_mist_base, None, None, 0.0, True)]
    return sd_ops(P, ops)


_lo = np.min([o[3] for o in OPS], 0) - 0.05
_hi = np.max([o[4] for o in OPS], 0) + 0.05
_xh = math.ceil(max(-_lo[0], _hi[0]) / SDF_STEP) * SDF_STEP       # X-symmetric grid nodes (mirror test)
_lo[0], _hi[0] = -_xh, _xh
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
for nm, gr, fn, lo, hi, k, b in OPS:
    g.apply(fn, lo, hi, k)
_gx = g.lo[0] + g.step * np.arange(g.n[0])
report["grid_x_symmetry"] = round(float(np.abs(_gx + _gx[::-1]).max()), 9)
t_poly = time.time()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
del g
assert crumbs["pieces"] == 1 or crumbs["dropped_verts"] < 0.001 * len(HV), ("cloth pieces fell off", crumbs)


def feature_ovals():
    """(name, region, cx, cz, a, b, tilt, depth): the two eyes + three chest slits, mirror pairs explicit"""
    out = [("eye.L", "eye", EYE_C[0], EYE_C[1], EYE_AB[0], EYE_AB[1], 0.0, EYE_DEPTH),
           ("eye.R", "eye", -EYE_C[0], EYE_C[1], EYE_AB[0], EYE_AB[1], 0.0, EYE_DEPTH)]
    for (c_, ab_, tl_) in SLITS:
        if abs(c_[0]) < 1e-9:
            out.append(("slit.C", "slit", 0.0, c_[1], ab_[0], ab_[1], 0.0, SLIT_DEPTH))
        else:
            out.append(("slit.L", "slit", c_[0], c_[1], ab_[0], ab_[1], tl_, SLIT_DEPTH))
            out.append(("slit.R", "slit", -c_[0], c_[1], ab_[0], ab_[1], -tl_, SLIT_DEPTH))
    return out


# sink the eyes + slits into the HIGH: a smooth dish along the vertex normal, front surface only
_HN = vertex_normals(HV, HF)
_sink = np.zeros(len(HV))
for nm, rg, cx, cz, ea, eb, tl, dp in feature_ovals():
    f_ = FP.oval_tilted(HV, cx, cz, ea, eb, tl)
    m_ = (HV[:, 1] < FEATURE_GATE_Y) & (np.abs(HV[:, 2] - cz) < eb + 0.2)
    _sink = np.maximum(_sink, np.where(m_, dp * smoothstep(-0.06, 0.35, f_), 0.0))
HV = HV - _HN * _sink[:, None]
report["strands"] = WISP_INFO
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": [int(v) for v in np.round((_hi - _lo) / SDF_STEP)],
                 "ops": len(OPS), "high_tris": len(HF), "high_verts": len(HV), "crumbs": crumbs,
                 "high_min_z": round(float(HV[:, 2].min()), 6), "wisp_blobs": len(WISP_BLOBS),
                 "sunk_high_verts": int((_sink > 1e-6).sum()), "seconds_fields": round(t_poly - t_sdf, 1),
                 "seconds": round(time.time() - t_sdf, 1)}
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
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
OVALS = feature_ovals()


def _in_zone(co):
    if co[1] >= FEATURE_GATE_Y:
        return False
    P_ = np.array([co])
    return any(FP.oval_tilted(P_, cx, cz, ea + FACE_ZONE, eb + FACE_ZONE, tl)[0] > 0.0 for _, _, cx, cz, ea, eb, tl, _ in OVALS)


for _ in range(FACE_SUBDIV):
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
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks,
                    "face_densify": {"rounds": FACE_SUBDIV, "zone": FACE_ZONE, "new_verts": len(LV) - _nv0},
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(), "seconds": round(time.time() - t, 1)}
assert np.all(_bb_l / _bb_h > 0.98), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(BAKE_CAGE_FLOOR, round(3.0 * float(np.percentile(dev2, 99)), 4))
print("RETOPO", json.dumps(report["retopo"]))

# =========================================================================== 3. region fields + iso cuts on the shell
DGR = {}
for gname in GROUPS:
    fs = [fn(LV) for nm, gr, fn, lo, hi, k, b in OPS if gr == gname]
    DGR[gname] = np.min(np.stack(fs, 1), 1)


def own(groups, P_dgr=None):
    D = DGR if P_dgr is None else P_dgr
    mine = np.min(np.stack([D[g_] for g_ in groups], 1), 1)
    rest = np.min(np.stack([D[g_] for g_ in GROUPS if g_ not in groups], 1), 1)
    return rest - mine


def tone_normal(P):
    h = NZ_H
    G = np.stack([sd_base(P + h * e) - sd_base(P - h * e) for e in np.eye(3)], 1)
    return G / np.maximum(np.linalg.norm(G, axis=1, keepdims=True), 1e-9)


NB = tone_normal(LV)
TH = LATHE.theta_f(LV)
groove = -FP.fold_field(TH, LV[:, 2], FOLD_TERMS) * smoothstep(FOLD_Z[0], FOLD_Z[1], LV[:, 2]) - FOLD_T
FIELDS = {"mist": own(["mist", "wisp"]),                    # > 0: the mist column / strands own the surface (the
          #                                                     hard-union crease under the tatters is the 0 line)
          "shade": np.maximum(groove, -NB[:, 2] - UNDER_NZ),
          "stripe": FP.streak_field(MISTP.theta_f(LV), LV[:, 2], STREAK_TERMS)}
for nm, rg, cx, cz, ea, eb, tl, dp in OVALS:
    f_ = FP.oval_tilted(LV, cx, cz, ea, eb, tl)
    FIELDS[nm] = np.where((LV[:, 1] < FEATURE_GATE_Y) & (np.abs(LV[:, 2] - cz) < eb + 0.2), f_, -1.0)
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
is_cloth = lambda a, b: a[LAY["mist"]] < TOL and b[LAY["mist"]] < TOL
off_feat = lambda a, b: is_cloth(a, b) and all(a[LAY[nm]] < TOL and b[LAY[nm]] < TOL for nm, *_ in OVALS)
CUTS = [("mist", 0.0, None)] + [(nm, 0.0, is_cloth) for nm, *_ in OVALS] + [("shade", 0.0, off_feat)] + \
       [("stripe", STREAK_PINK, is_mist), ("stripe", STREAK_PURPLE, is_mist)]
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
shell_reg = np.full(len(CF), "cloth", dtype=object)
shell_reg[FVAL["shade"] > 0.0] = "cloth_fold"
for nm, rg, *_ in OVALS:
    shell_reg[(~mist_f) & (FVAL[nm] > 0.0)] = rg
shell_reg[mist_f] = "mist_lavender"
shell_reg[mist_f & (FVAL["stripe"] > STREAK_PINK)] = "mist_pink"
shell_reg[mist_f & (FVAL["stripe"] < STREAK_PURPLE)] = "mist_purple"

# ---- tone-island merge (lesson 2026-10-10: no polka-dot regions): a connected same-tone patch smaller than MIN_ISLAND
#      joins the neighbouring tone it shares the longest border with, inside its own class (cloth tones; one class per
#      mist alpha band, so a merge never moves a face across an alpha band); glow features untouched
CLASS = {"cloth": 0, "cloth_fold": 0, "mist_lavender": 1, "mist_purple": 1, "mist_pink": 1, "eye": 9, "slit": 10}


def merge_islands(reg):
    merged = 0
    for _ in range(6):
        seen = np.zeros(len(reg), bool)
        changed = 0
        for s0 in range(len(reg)):
            if seen[s0] or CLASS[reg[s0]] >= 9:
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

# =========================================================================== 4. parts: shackles + chains (L built, R mirrored)
PARTS = []
_S, _E, _W = (np.array(p_, float) for p_ in (ARM_S, ARM_E, ARM_W))
ARM_DIR = unit(_W - _E)
SHK_C = _W - ARM_DIR * SHACKLE[2]
V_, F_, R_ = FP.torus(SHACKLE[0], SHACKLE[1], 24, 8, SHK_C, ARM_DIR, up_hint=(1.0, 0.0, 0.0), region="shackle")
PARTS.append(FP.Part("shackle.L", V_, F_, R_, bone="arm_fore.L"))
_a, _u, _v = FP.frame_from_axis(ARM_DIR, (1.0, 0.0, 0.0))     # _u: the ring's outer (+X-ward) direction
STRANDS = {}
for nm_, sgn_, splay in (("outer", 1.0, STRAND_SPLAY[0]), ("inner", -1.0, STRAND_SPLAY[1])):
    top = SHK_C + sgn_ * SHACKLE[0] * _u                    # the hanging point: on the shackle ring's centre-line
    x0 = top[0]
    xb = _W[0] - splay                                      # STRAND_SPLAY in the sheet's image-left (R wrist) sense:
    #                                                         outer -0.16 -> +0.16 further out on the L side
    ctrl = [top, top + np.array([0.0, 0.0, -0.30]) + np.array([(xb - x0) * 0.35, 0.0, 0.0]),
            np.array([x0 + (xb - x0) * 0.80, top[1], 0.5 * (top[2] + STRAND_BOTTOM_Z)]),
            np.array([xb, top[1], STRAND_BOTTOM_Z])]
    V_, F_, R_, S_, info = FP.chain_strand(ctrl, CHAIN_LINKS, LINK_PITCH, LINK[0], LINK[1], LINK[2], (0.0, 1.0, 0.0),
                                           LINK_SEG[0], LINK_SEG[1], end_region="chain_iron")
    p_ = FP.Part("chain.L." + nm_, V_, F_, R_)
    p_.chain = ("L", S_, info["length"])
    PARTS.append(p_)
    STRANDS[nm_] = {"top": top, "bottom": np.array([xb, top[1], STRAND_BOTTOM_Z]), "info": info}
for p_ in list(PARTS):                                    # the R side = the exact mirror of the L side
    V_, F_ = FP.mirror_part(p_.V, p_.F)
    q_ = FP.Part(p_.name[:-2] + ".R" if p_.name.endswith(".L") else p_.name.replace(".L.", ".R."), V_, F_, p_.R,
                 bone=(p_.bone[:-2] + ".R") if p_.bone else None)
    if getattr(p_, "chain", None):
        q_.chain = ("R", p_.chain[1], p_.chain[2])
    PARTS.append(q_)
CHAIN_LEN = STRANDS["outer"]["info"]["length"]
report["chain"] = {"links_per_strand": CHAIN_LINKS, "strands_per_wrist": 2, "links_per_wrist": 2 * CHAIN_LINKS,
                   "pitch": LINK_PITCH, "link_half_len_w_wire": list(LINK),
                   "strand_length": round(CHAIN_LEN, 4),
                   "strands": {k: {"top": v["top"].round(4).tolist(), "bottom": v["bottom"].round(4).tolist(),
                                   "path_length": round(v["info"]["path_length"], 4)} for k, v in STRANDS.items()},
                   "shackle": {"centre": SHK_C.round(4).tolist(), "R0": SHACKLE[0], "tube": SHACKLE[1]},
                   "rule": "two strands of %d closed stadium links per wrist hanging from the shackle ring (the sheet's "
                           "two free-hanging strands); link planes alternate 90 deg (even links face the front)" % CHAIN_LINKS}

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
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
for name, (a_, b_) in RANGE.items():                       # nothing dips below the floor (lesson 2026-10-10)
    assert VALL[a_:b_, 2].min() > -1e-6, (name, float(VALL[a_:b_, 2].min()))
HV_s = HV - SHIFT
REG = ["cloth", "cloth_fold", "eye", "slit", "mist_lavender", "mist_purple", "mist_pink", "shackle", "chain_link",
       "chain_iron"]
MIST_REG = {"mist_lavender", "mist_purple", "mist_pink"}
GLOW_REG = {"eye", "slit", "chain_link"}
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
        # BLENDED (fogeist: dithered alpha renders as grain, lesson 2026-10-10); one transparent layer only (no overlap:
        # the closed mist's far side never sorts over its near side); a solid (not stochastic see-through) shadow
        mat.surface_render_method = "BLENDED"
        if hasattr(mat, "use_transparency_overlap"):
            mat.use_transparency_overlap = False
        if hasattr(mat, "use_transparent_shadow"):
            mat.use_transparent_shadow = False
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


def grade_mist(me, pal):
    """the mist's smooth GRADIENT down the height, per CORNER (no band steps): every mist corner's colour is mixed toward
    the light lavender-white region by mix(z) (light_mix[0] at z >= light_z[0] -> light_mix[1] at z <= light_z[1],
    linear), and its alpha = region alpha x (alpha_bottom + (1 - alpha_bottom) x ramp(z)) (1 at z >= alpha_z[0] ->
    alpha_bottom at z <= alpha_z[1], linear). Palette material 'mist_grade' holds the numbers (skins can re-grade)."""
    gd = pal["material"]["mist_grade"]
    names, rid_, _ = PAL.read_regions(me)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    z = co[lv, 2]
    rl = np.repeat(rid_, lt)
    mist_ids = [j for j, n in enumerate(names) if n in MIST_REG]
    m = np.isin(rl, mist_ids)
    ca = me.color_attributes["Col"]
    cd = np.empty(len(me.loops) * 4, dtype=np.float32); ca.data.foreach_get("color", cd); cd = cd.reshape(-1, 4)
    light = PAL.srgb_to_linear(pal["regions"][gd["light_region"]]["rgb"])
    (m0, m1), (z0, z1) = gd["light_mix"], gd["light_z"]
    mix = m1 + (m0 - m1) * np.clip((z - z1) / (z0 - z1), 0.0, 1.0)
    cd[m, :3] = cd[m, :3] * (1.0 - mix[m, None]) + light[None, :] * mix[m, None]
    (a0, a1), ab = gd["alpha_z"], gd["alpha_bottom"]
    a_reg = np.array([float(pal["regions"][n].get("alpha", 1.0)) for n in names], np.float32)
    ramp = np.clip((z - a1) / (a0 - a1), 0.0, 1.0)
    cd[m, 3] = a_reg[rl[m]] * (ab + (1.0 - ab) * ramp[m])
    ca.data.foreach_set("color", cd.ravel())
    return {"mist_corners": int(m.sum()), "alpha_range": [round(float(cd[m, 3].min()), 4), round(float(cd[m, 3].max()), 4)],
            "light_mix_range": [round(float(mix[m].min()), 4), round(float(mix[m].max()), 4)], "grade": gd}


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        paint_alpha(o.data, pal)
        out[o.name + ".mist_grade"] = grade_mist(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
            if s_.material.name == UNIT + "_mist":
                s_.material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = \
                    float(pal["material"]["mist_roughness"])
    return out


GLOW_CAP = 2.0                         # emissive palette ceiling (phone cap, import notes s7)
GLOW_GRADE = ["chain_link", "slit", "eye"]   # tiers rise chains < slits < eyes


def glow_check(pal):
    import colorsys
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    pk = {n: max(c / 255.0 for c in pal["regions"][n]["emission"]) * pal["regions"][n].get("emission_scale", 1.0)
          * pal["material"].get("emission_strength", 1.0) for n in lit}
    hues = {n: round(colorsys.rgb_to_hsv(*[c / 255.0 for c in pal["regions"][n]["emission"]])[0] * 360.0, 1) for n in lit}
    tiers = {n: pal["regions"][n].get("emission_scale", 0.0) for n in GLOW_GRADE}
    ok = set(lit) == GLOW_REG and max(pk.values() or [0.0]) <= GLOW_CAP and \
        all(tiers[a] < tiers[b] for a, b in zip(GLOW_GRADE, GLOW_GRADE[1:])) and \
        all(h >= 345.0 or h <= 10.0 for h in hues.values())
    return {"skin": pal["skin"], "glowing_regions": lit, "peak_x_strength": {k: round(v, 4) for k, v in pk.items()},
            "cap": GLOW_CAP, "tiers": tiers, "grade": GLOW_GRADE, "emission_hue_deg": hues, "red_hue_window": [345, 10],
            "ok": ok}


MAT_CLOTH = make_mat(UNIT + "_cloth", "cloth")
MAT_MIST = make_mat(UNIT + "_mist", "mist")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)   # palette <-> regions 1:1
report["glow"] = {"default": glow_check(pal_default)}
assert report["glow"]["default"]["ok"], report["glow"]
for n in REG:                                             # translucency lives on the mist material only
    assert (n in MIST_REG) == ("alpha" in pal_default["regions"][n]), ("alpha on the wrong material", n)

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_CLOTH); me.materials.append(MAT_MIST)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
PAL.store_regions(me, REG, rid, np.ones(len(FALL)))       # no per-face jitter: flat paint (no speckle, no dirty noise)
report["regions_faces"] = repaint([low], pal_default)
report["alpha"] = {"main": paint_alpha(me, pal_default), "material_alpha": pal_default["material"].get("alpha", 1.0),
                   "mist_render_method": MAT_MIST.surface_render_method,
                   "mist_transparency_overlap": bool(getattr(MAT_MIST, "use_transparency_overlap", None)),
                   "mist_transparent_shadow": bool(getattr(MAT_MIST, "use_transparent_shadow", None))}
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the hood's centre at eye height -> the midpoint of the two eye patches (on the midline, -Y)
_eye_f = np.nonzero(rid == REG.index("eye"))[0]
_eye_c = np.array([VALL[FALL[i]].mean(0) for i in _eye_f])
assert len(_eye_c[_eye_c[:, 0] > 0]) and len(_eye_c[_eye_c[:, 0] < 0]), "an eye failed to cut"
for nm, rg, cx, cz, *_ in OVALS:
    _fc = np.array([VALL[FALL[i]].mean(0) for i in np.nonzero(rid == REG.index(rg))[0]])
    assert np.any(np.abs(_fc[:, 0] - (cx - SHIFT[0])) < 0.05), ("a feature failed to cut", nm)
EYES = {"L": _eye_c[_eye_c[:, 0] > 0].mean(0), "R": _eye_c[_eye_c[:, 0] < 0].mean(0)}
anchor = np.array([0.0, 0.0, EYE_C[1]]) - SHIFT
landmark = 0.5 * (EYES["L"] + EYES["R"])
dvec = landmark - anchor
report["facing"] = {"rule": "hood centre at eye height -> the midpoint of the two eyes (the face looks -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "eyes_world": {k: v.round(4).tolist() for k, v in EYES.items()},
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

# ---- mirror test (house rule): every vertex's X-mirror image vs the nearest point of the mesh
bvh_all = BVHTree.FromPolygons(VALL.tolist(), FALL)
_md = np.array([bvh_all.find_nearest(Vector((-p[0], p[1], p[2])))[3] for p in VALL])
_sh = slice(*RANGE["shell"])
report["mirror_test"] = {"about": "world X = 0 (after the centring shift %.6f)" % SHIFT[0],
                         "max_abs_asymmetry": round(float(_md.max()), 5),
                         "p99": round(float(np.percentile(_md, 99)), 5),
                         "shell_max": round(float(_md[_sh].max()), 5),
                         "parts_max": round(float(np.delete(_md, np.arange(*RANGE["shell"])).max()), 7),
                         "rule": "rest pose mirror-symmetric (house rule); the shell's residual = marching-tet "
                                 "triangulation + collapse decimation (fields and grid nodes are exactly symmetric)"}
print("MIRROR", json.dumps(report["mirror_test"]))

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
                  "per_part": {"shell (one closed SDF shell)": tri_count_F(CF), **{p.name: p.tris() for p in PARTS}},
                  "per_part_by_owner_group": {g_: int((_fown == j).sum()) for j, g_ in enumerate(GROUPS)},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. One smooth cloth / mist shell carries most of "
                            "the budget (folds, hem tatters, mist bands need density under flat shading; the face zone is "
                            "densified for the eye / slit outlines); 32 closed chain links + 2 shackles take the rest."
                            % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"per_part": open_e, "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "one closed SDF shell + closed tori (shackles) + closed stadium tubes (links)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet": {"front_height": 6.74, "back_height": 6.78, "front_width": 4.35, "back_width": 4.09,
                               "front_width_without_mist_halo_to_chain_edges": "x -2.05 .. 1.96 (chains)"},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
report["sheet_deviations"] = [
    "SYMMETRY (house rule): the sheet's face + chest features sit 0.09 u to image right of the body axis (eye midpoint "
    "x 240.9 vs axis 232: the face drawn slightly turned) -> built centred on X = 0; the front mist's lower wisps drift "
    "to the unit's left (+X: front z 0.39 -0.55..1.31, back z 0.21 -1.50..0.51 mirrored) -> built symmetric",
    "SIDE VIEW is a 3/4 drawing, not an ortho: every half-depth (head 0.82 x width, torso 0.40-0.47, skirt 0.70 x width), "
    "the sleeves' forward hang (wrist y -0.25) and the mist's backward trail (y-centre 0 -> +0.55 at the bottom) are "
    "estimates read off it",
    "CHAINS: the brief says 'a hanging loop ... closed torus-link chain, 12-16 links per loop'; the SHEET draws, at each "
    "wrist, TWO strands hanging from the shackle with FREE ends (front x 52 / 85 px, bottoms y ~690-710) -> built as the "
    "sheet draws: 2 strands x %d closed links = %d per wrist (each link a closed tube; the strands do not join)" % (
        CHAIN_LINKS, 2 * CHAIN_LINKS),
    "CHAIN COLOUR: the first link (at the shackle) and the free end link of each strand are dark iron (chain_iron, "
    "38,39,43), the rest glow red, as drawn; the sheet's darker shading of the edge-on links is not painted",
    "CHAIN PLANE: strands separate in X (front x -1.80 / -1.47) and hang straight; the sheet's slight curl / twist of the "
    "strands is not modelled",
    "TATTERS: GEOMETRY -- the robe solid is clipped by a ring of %d seeded pointed tatters (mirror-symmetric, tips z "
    "%.2f-%.2f) hanging over an inset mist column; the tatters are solid wedges of the robe (no thin double-sided flaps), "
    "their cut undersides take the fold shadow" % (2 * HEM_N - 2, HEM_TOP - HEM_DEPTH[1], HEM_TOP - HEM_DEPTH[0]),
    "MIST: a closed rippled column + %d strands of %d closed blobs (fogeist approach), BLENDED alpha graded per corner down "
    "the height (no band steps), lavender-white with purple / pink-red streaks (streak regions are hard-edged region "
    "boundaries); the sheet's feathered smoke edges and fine curls are not modelled; the mist casts a solid shadow" % (
        2 * WISP_N - 2, WISP_NBLOB),
    "EYES / SLITS: one flat glowing red region each, sunk %.3f / %.3f u; the sheet's brighter inner core, dark-red rim and "
    "soft halo are not modelled" % (EYE_DEPTH, SLIT_DEPTH),
    "FOLDS: soft vertical ridges + groove shadows on the robe below the chest and in the mist, plus underside shadow on "
    "the sleeves / drapes; the sheet's diagonal draping creases on the hood, shoulders and sleeves are not modelled",
    "SLEEVE ENDS: the sheet shows no hands; each sleeve ends in a gathered rounded tuft just through the shackle",
    "GLOW HALOS: the sheet's red glow aura round the chains / eyes is not geometry (emission only)",
    "INK LINES: the thin dark contour lines are line art, not modelled",
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
low["conquest_source"] = "from scratch: design/reference/spectormn-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = ("natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit "
                                "report-only)")
low["conquest_emission_channel"] = ("Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; eyes, chest "
                                    "slits and chain links glow red (tiers chain < slit < eye, under the 2.0 cap)")
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; mist "
                                 "material alphaMode BLEND single-sided (Eevee BLENDED, no overlap); cloth, glow and metal "
                                 "opaque")
_FOC = {"face_eyes": ([-0.75, -0.9, 5.35], [0.75, 0.5, 6.80]),
        "chest_slits": ([-0.75, -0.9, 4.20], [0.75, 0.5, 5.35]),
        "chain_restraint": ([1.15, -0.8, 1.00], [2.15, 0.3, 3.75]),
        "ghost_mist": ([-1.9, -1.0, 0.0], [1.9, 1.8, 3.6])}
low["conquest_focus"] = json.dumps({
    **{k: [(np.array(a) - SHIFT).tolist(), (np.array(b) - SHIFT).tolist()] for k, (a, b) in _FOC.items()},
    "views": {"face_eyes": [0.0, 4.0], "chest_slits": [0.0, 4.0], "chain_restraint": [25.0, 6.0], "ghost_mist": [20.0, 8.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "iso_cuts", "tris", "measure", "facing",
                                                            "open_edges", "regions_area_share", "glow", "alpha",
                                                            "mirror_test", "chain", "centre_shift", "regions_faces")},
                                default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + closed parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
for name, V, F, R, p in ISL[1:]:
    PH_V.append(V - SHIFT); PH_F += [[i + o_ for i in f] for f in F]; o_ += len(V)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
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
for mat_ in (MAT_CLOTH, MAT_MIST):
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
    for mat_ in (MAT_CLOTH, MAT_MIST):
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
    "high_tris": len(PH_F), "high_rule": "the cloth SDF high (eyes + slits sunk) + every closed part",
    "normal_reference": "FLAT low faces (the cloth reads smooth under the flat_shaded contract); wired into both materials",
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
for mat_ in (MAT_CLOTH, MAT_MIST):
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
                     "provenance": "pixel-sampled from the sheet's six palette chips + drawn-surface samples (see the "
                                   "palette notes)"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 7. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


_ch_top = 0.5 * (STRANDS["outer"]["top"] + STRANDS["inner"]["top"])
_ch_bot = 0.5 * (STRANDS["outer"]["bottom"] + STRANDS["inner"]["bottom"])
_ch_mid = 0.5 * (_ch_top + _ch_bot)
COL_PTS = [(0.0, 0.50, 0.10), (0.0, 0.30, 1.20), (0.0, 0.12, 2.45), (0.0, 0.0, 4.40), (0.0, 0.0, 5.50), (0.0, 0.0, 6.70)]
BONES = [("body", P_(COL_PTS[2]), P_(COL_PTS[3]), "root"),
         ("chest", P_(COL_PTS[3]), P_(COL_PTS[4]), "body"),
         ("head", P_(COL_PTS[4]), P_(COL_PTS[5]), "chest"),
         ("mist.001", P_(COL_PTS[2]), P_(COL_PTS[1]), "body"),
         ("mist.002", P_(COL_PTS[1]), P_(COL_PTS[0]), "mist.001")]
for sg, sd_ in ((1.0, "L"), (-1.0, "R")):
    M_ = np.array([sg, 1.0, 1.0])
    BONES += [("arm_upper." + sd_, P_(_S * M_), P_(_E * M_), "chest"),
              ("arm_fore." + sd_, P_(_E * M_), P_(_W * M_), "arm_upper." + sd_),
              ("chain.%s.001" % sd_, P_(_ch_top * M_), P_(_ch_mid * M_), "arm_fore." + sd_),
              ("chain.%s.002" % sd_, P_(_ch_mid * M_), P_(_ch_bot * M_), "chain.%s.001" % sd_)]
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
    e.use_connect = False
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a0, a1 = RANGE["shell"]
Pw = CV.copy()                                     # pre-shift shell coords
DG2 = {g_: np.min(np.stack([fn(Pw) for nm, gr, fn, lo, hi, k, b in OPS if gr == g_], 1), 1) for g_ in GROUPS}
g_arm = smoothstep(-OWN_BLEND, OWN_BLEND, own(["arm"], DG2))
zz = Pw[:, 2]
s1, s2, s3, s4 = (smoothstep(a, b, zz) for a, b in COLUMN_EDGES)
W_col = {"mist.002": 1.0 - s1, "mist.001": s1 - s2, "body": s2 - s3, "chest": s3 - s4, "head": s4}
_ax = np.abs(Pw[:, 0])
_SW = _W - _S
t_arm = ((np.stack([_ax, Pw[:, 1], Pw[:, 2]], 1) - _S) @ _SW) / float(_SW @ _SW)
t_elb = float((_E - _S) @ _SW) / float(_SW @ _SW)
c_root = 1.0 - smoothstep(ARM_ROOT_T[0], ARM_ROOT_T[1], t_arm)
f_elb = smoothstep(t_elb - ELBOW_BLEND, t_elb + ELBOW_BLEND, t_arm)
side = np.where(Pw[:, 0] >= 0.0, "L", "R")
for nm, w in W_col.items():
    Wt[a0:a1, J[nm]] += (1.0 - g_arm) * w
Wt[a0:a1, J["chest"]] += g_arm * c_root
for sd_ in ("L", "R"):
    m_ = side == sd_
    Wt[a0:a1, J["arm_upper." + sd_]] += np.where(m_, g_arm * (1.0 - c_root) * (1.0 - f_elb), 0.0)
    Wt[a0:a1, J["arm_fore." + sd_]] += np.where(m_, g_arm * (1.0 - c_root) * f_elb, 0.0)
for p in PARTS:
    b0, b1 = RANGE[p.name]
    if p.bone:
        Wt[b0:b1, J[p.bone]] = 1.0
    else:
        sd_, S_, L_ = p.chain
        Wc = K.vine_weights(S_, L_, 2, parent_s0=-0.10)          # col 0 = arm_fore
        Wt[b0:b1, J["arm_fore." + sd_]] = Wc[:, 0]
        Wt[b0:b1, J["chain.%s.001" % sd_]] = Wc[:, 1]
        Wt[b0:b1, J["chain.%s.002" % sd_]] = Wc[:, 2]
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
                  "rule": "shell: column bones by height (mist.002 | mist.001 | body | chest | head, COLUMN_EDGES) "
                          "blended by sleeve ownership (+-OWN_BLEND) into chest -> arm_upper -> arm_fore along the arm; "
                          "shackles 100 % arm_fore; chain links rigkit.vine_weights along each strand (arm_fore -> "
                          "chain.001 -> chain.002)"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly.
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


def pose_at(clip, f):
    """world-axis rotations about each bone's head (X: + swings a hanging part BACK / tips the top FORWARD; Y, Z
    mirrored on the R side)"""
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        base, bob, nb = IDLE_LIFT
        lift = base + bob * (0.5 - 0.5 * math.cos(TAU * nb * u))           # >= base > 0: never below the rest floor
        P["body"] = ((0.0, 0.0, lift), [("Y", IDLE_SWAY[0] * math.sin(TAU * u))], None)
        P["chest"] = (None, [("X", IDLE_SWAY[1] * math.sin(TAU * nb * u - 0.7))], None)
        P["head"] = (None, [("Y", IDLE_HEAD[0] * math.sin(TAU * u - 0.9)), ("Z", IDLE_HEAD[1] * math.sin(TAU * u + 0.4))],
                     None)
        a_c, b_c, lag = IDLE_CHAIN
        for sd_, sg in (("L", 1.0), ("R", -1.0)):
            P["arm_upper." + sd_] = (None, [("X", IDLE_ARM * math.sin(TAU * nb * u - 1.0))], None)
            P["arm_fore." + sd_] = (None, [("X", 0.6 * IDLE_ARM * math.sin(TAU * nb * u - 1.5))], None)
            for k in range(2):
                P["chain.%s.%03d" % (sd_, k + 1)] = (None, [("X", a_c * math.sin(TAU * nb * u - 1.8 - k * lag)),
                                                            ("Y", sg * b_c * math.sin(TAU * u - 0.5 - k * lag))], None)
        m1, m2, lag = IDLE_MIST
        for k, (bn, a_) in enumerate((("mist.001", m1), ("mist.002", m2))):
            P[bn] = (None, [("X", a_ * math.sin(TAU * u - k * lag)), ("Y", a_ * math.sin(TAU * 2 * u + 0.6 - k * lag))],
                     None)
    else:
        lift = WALK_LIFT[0] + WALK_LIFT[1] * math.sin(TAU * 2 * u)
        lean, pulse = WALK_LEAN
        P["body"] = ((0.0, 0.0, lift), [("X", lean + pulse * math.sin(TAU * 2 * u))], None)
        P["chest"] = (None, [], None)
        P["head"] = (None, [("X", -0.5 * lean - 0.5 * pulse * math.sin(TAU * 2 * u - 0.8))], None)
        c1, c2, wob = WALK_CHAIN
        for sd_, sg in (("L", 1.0), ("R", -1.0)):
            P["arm_upper." + sd_] = (None, [("X", WALK_ARM + 1.5 * math.sin(TAU * 2 * u - 0.6))], None)
            P["arm_fore." + sd_] = (None, [("X", 0.5 * WALK_ARM + 1.0 * math.sin(TAU * 2 * u - 1.0))], None)
            P["chain.%s.001" % sd_] = (None, [("X", c1 + wob * math.sin(TAU * 2 * u - 1.4)),
                                              ("Y", sg * 0.6 * wob * math.sin(TAU * u))], None)
            P["chain.%s.002" % sd_] = (None, [("X", c2 + wob * math.sin(TAU * 2 * u - 2.4)),
                                              ("Y", sg * 0.6 * wob * math.sin(TAU * u - 0.8))], None)
        m1, m2, wob = WALK_MIST
        P["mist.001"] = (None, [("X", m1 + wob * math.sin(TAU * 2 * u - 1.0)), ("Y", 0.6 * wob * math.sin(TAU * u))], None)
        P["mist.002"] = (None, [("X", m2 + wob * math.sin(TAU * 2 * u - 2.0)), ("Y", 0.6 * wob * math.sin(TAU * u - 0.9))],
                         None)
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


CHV = np.concatenate([np.arange(*RANGE[p.name]) for p in PARTS if getattr(p, "chain", None)])
MSV = np.nonzero(np.r_[VALL[a0:a1, 2] < 1.2, np.zeros(len(VALL) - a1, bool)])[0]
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
        rows.append({"minz": float(C[:, 2].min()), "chain_move": float(np.linalg.norm(C[CHV] - VALL[CHV], axis=1).max()),
                     "chain_dy": float((C[CHV, 1] - VALL[CHV, 1]).mean()),
                     "mist_move": float(np.linalg.norm(C[MSV] - VALL[MSV], axis=1).max()),
                     "lift": float(C[:, 2].min() - 0.0),
                     "head_y": float((rig.matrix_world @ pbs["head"].tail).y),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "chain_max_move": round(float(g_("chain_move").max()), 4),
                    "chain_mean_dy_range": [round(float(g_("chain_dy").min()), 4), round(float(g_("chain_dy").max()), 4)],
                    "mist_max_move": round(float(g_("mist_move").max()), 4),
                    "head_tip_y_range": [round(float(g_("head_y").min()), 4), round(float(g_("head_y").max()), 4)]}
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
rig["conquest_rig"] = ("spectormn v1: root > body > chest > head; chest > arm_upper.L/R > arm_fore.L/R > chain.L/R.001 > "
                       ".002; body > mist.001 > mist.002")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Spectormn's movement or height; open question): "
                               "idle = 4 s hover bob (+%.2f..+%.2f u, 2 bobs) + body / head sway + chain sway (lagging "
                               "pendulum) + mist drift waves; walk = 2 s in-place hover drift: the body leans %.0f deg into "
                               "the drift, sleeves, chains (%.0f / %.0f deg) and mist (%.0f / %.0f deg) trail back" % (
                                   IDLE_LIFT[0], IDLE_LIFT[0] + IDLE_LIFT[1], WALK_LEAN[0], WALK_CHAIN[0], WALK_CHAIN[1],
                                   WALK_MIST[0], WALK_MIST[1]))
low["conquest_locomotion"] = "hover placeholder: rest pose lowest mist wisp z 0 per contract; every clip lifts off it"
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

# =========================================================================== 8. glb
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
              "structure": "armature identity + skinned 'spectormn' (opaque cloth / glow / metal primitive + alpha-blend "
                           "mist primitive); natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
