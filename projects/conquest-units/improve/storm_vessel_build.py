"""Storm Vessel -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/storm_vessel_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x3: Shield Rodent, Storm Vessel, Volcano Turtle" (section STORM VESSEL)
+ the sheet design/reference/storm_vessel-character-sheet.png (intake: design/reference/storm_vessel/intake.json). Contract:
improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. FRONT + BACK are true orthos; the SIDE is drawn at a 3/4 slant:
  FRONT view (the unit's left on image right):   X = (x_px - 255) / 100   ; z = (811 - y_px) / 100
  BACK view (mirrored into world X):             X = -(x_px - 668) / 100  ; z = (811 - y_px) / 100
  SIDE view (3/4, front on image left):          image-right = 0.40 X + 0.914 Y  (66 deg off the front: the core frame
                                                 disc is drawn 66 px wide x 163 tall -> cos 0.40, and the L hand's centre
                                                 sits at side x 1168 = 0.83 right of the axis vs the front's X 2.07 -> 0.40)
Floor z = 0 at the BOLT TIP (it floats). X +ve = the unit's LEFT (L), front = -Y. Colour classes off the sheet pixels: blue
(B > 180, B - R > 25), white (all > 200-225), grey, orange (R > 200, B < 120); 1-3 px row scans; circle fits by least squares.
Depth (Y) is read off the 3/4 side view by removing the X share (0.40 X) of a measured side-view half-width -- an UPPER
BOUND (the brief: the side is 3/4).

Pipeline:
  1. BODY = ONE SDF: the ORB (an ellipsoid: the orthos draw a circle r 1.64, the 3/4 side's narrower silhouette bounds the
     depth to 1.38) smooth-union the short NECK collar (an elliptic lathe of the measured profile), minus the open neck's
     cavity. Marching tets = the HIGH (bake source); collapse decimation = the LOW shell.
  2. REGIONS: the shell is one region (body); the glowing neck interior is a closed pool disc on the cavity floor (the
     decimated floor holds no vertex an iso-cut could use).
  3. PARTS (storm_vessel_parts.py, every one closed): the CORE (segmented ring frame with 10 seam strips + the glowing
     inset dome + its filament strands); the NECK SPRAY (9 thin glowing strands); the FLOATING HEAD (white half-dome face
     with the two oval eyes iso-cut -- the one iso-cut region + the visor band ring with its two front tabs + two horn tips); the FLOATING ARMS
     (band ring + white dome hand with three tick marks + the glowing burst disc + 11 burst strands, R mirrored to L); the
     LIGHTNING TAIL (the FRONT ortho's zig-zag outline extruded, orange edge band + yellow-orange inner) + 9 spark crackles.
  4. palette (palettes.py regions -> Col / Glow; glow on the core, filaments, neck interior, arm bursts, bolt + sparks only,
     tiers under the 2.0 cap), Smart UV, bake normal + AO from the SDF high + the parts at HIGH_DENSITY.
  5. RIG: root > body > head, arm_L, arm_R, tail.001 > tail.002. Clips PLACEHOLDER (movement + hover height not given):
     idle = hover bob, head + arms bobbing out of phase, a small scale-pulse flicker; walk = in-place hover-drift lean.
     glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K              # noqa: E402  (read-only use)
import palettes as PAL          # noqa: E402  (read-only use)
import magmoo_sdf as SD         # noqa: E402  (read-only use: the SDF kit)
import storm_vessel_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "storm_vessel"
CHAR_ID = "storm_vessel"              # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.15                       # BAND >= max smooth-union k (NECK_K 0.08) + 2 steps (contract hazard note)
BODY_TRIS = 6000                      # "body mesh detail": the shell's decimation target (4200 left the neck lip lumpy: v1 run 3)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- ORB ("big glossy vessel-blue orb"): least-squares circle fits to the blue outline rows clear of the arms (y 325-411
#   + 560-627): FRONT centre (254.5, 462) r 165.9 px, BACK (668.4, 457.3) r 161.2 -> centre z (811 - 460) / 100 = 3.51 ->
#   3.50, r 1.635 -> 1.64. DEPTH (3/4 SIDE): the orb's side half-width 1.42 (right edge x 1228 at y 450 vs the axis
#   1085) = sqrt((0.40 * 1.64)^2 + (0.914 * D)^2) -> D 1.38 (upper bound; the core's drawn position, side frame centre x 982,
#   gives ~1.2). The neck's side width (0.635 vs the front's 0.73 half) gives the SAME ratio 0.85: one Y scale for the body.
ORB_C = (0.0, 0.0, 3.50)              # "orb centre"
ORB_R = 1.64                          # "orb radius" (X, Z)
ORB_RY = 1.38                         # "orb depth" (half, Y) -- 1.64 = a true sphere (one knob)
# ---- NECK ("short open neck rim on top, a collar lip"): FRONT + BACK half-widths averaged (front centred +0.045, back
#   -0.03: built centred), rows (z, half-width): y 309 z 5.02 0.75 (the base fillet: NECK_K) / y 301 5.10 0.66 / y 297
#   5.14 0.63 (waist) / y 289 5.22 0.68 / y 281 5.30 0.73 (the rolled lip) / the lip top ellipse y 269-289 -> plane z 5.38.
NECK_ROWS = [(4.85, 0.66), (5.06, 0.665), (5.14, 0.63), (5.20, 0.655), (5.26, 0.705), (5.31, 0.725), (5.35, 0.715),
             (5.38, 0.69)]
NECK_K = 0.08                         # "neck / orb blend" (the drawn fillet r 0.75 at z 5.02)
CAV_R, CAV_FLOOR, CAV_K = 0.55, 5.20, 0.03   # "neck opening": inner radius (the lip ring 0.17 wide), interior floor z
POOL_R, POOL_T = 0.52, 0.035         # "neck interior glow": a glowing pool disc on the cavity floor (r, height above the
                                      #   floor); the cavity wall stays blue
# ---- CORE ("glowing core set into the front centre, segmented metal ring frame"): FRONT radial medians about (255, 467)
#   (centre z 3.44): orange ball r < 40 px, pale glass lip r 40-47 (220,192,172), grey frame r 47-69 (152,158,169),
#   outline r 69-71, body r >= 72. Deep centre r < 10 px (253,73,1).
CORE_Z = 3.44                         # "core height"
FRAME_IN, FRAME_OUT = 0.47, 0.70      # "ring frame" radii
FRAME_PROUD, FRAME_SINK = 0.06, 0.10  # frame front stands this proud of the orb surface / back buried this deep
FRAME_SEAMS = 10                      # "frame segments" (CORE DETAIL: 10 seams, one at 12 o'clock and one at 6)
SEAM_HW = 0.022                       # seam strip half-width (rad): the drawn lines ~2.5 px at r 58 px
CORE_DEEP_R, CORE_RIM_R = 0.10, 0.42  # "core centre" / "glass lip" radii
CORE_BULGE = 0.10                     # "core dome": the energy ball domes out this far at its centre (the side view's bulge)
CORE_FIL_N = 10                       # "core filaments": strands on the ball (CORE DETAIL draws ~10)
CORE_FIL_R = 0.013                    # ... strand radius (the drawn strands ~3 px)
# ---- NECK SPRAY ("glowing energy filaments spray up from the opening"): FRONT strand tips (head-detail zoom) at X -0.36 /
#   -0.16 / 0.05 / 0.26 / 0.46 (centred), z 5.50-5.57, from the opening's centre (z ~5.28); 3/4 SIDE draws 6-7 more round.
SPRAY_N = 9
SPRAY_TIP_R = [0.42, 0.24, 0.45, 0.20, 0.40, 0.28, 0.44, 0.22, 0.36]   # tip radius from the axis (per strand, round)
SPRAY_TIP_Z = [5.52, 5.58, 5.50, 5.60, 5.53, 5.57, 5.51, 5.59, 5.55]
SPRAY_R = 0.017                       # strand radius
# ---- HEAD ("floating head: a blue visor band with two horn tips over a white half-dome face, two eyes facing forward"):
#   FRONT (centred: the front head sits +0.085 right of the orb axis, the back +0.02 -- built on the axis)
#   face white rows: y 209 z 6.02 half 0.68 / y 221 5.90 0.57 / y 233 5.78 0.41 / y 241 5.70 0.22 / bottom y 246 z 5.65;
#   back agrees (0.68 / 0.50 at 5.82 / 0.24 at 5.70) -> a half-ellipsoid: top z 6.10, r 0.69, depth 0.45 (5.90: 0.62,
#   5.82: 0.54, 5.74: 0.41 vs drawn 0.57-0.62 / 0.50-0.53 / 0.36-0.39). Round in plan (the 3/4 side's 0.87 bound holds).
FACE_TOP, FACE_R, FACE_D = 6.10, 0.69, 0.45
HEAD_TOP = 6.42                       # the dome continues as a short cylinder under the band to this flat white top (the
                                      #   sheet never shows the top of the head: REPORTED)
EYES = [(-0.32, 5.926), (0.32, 5.926)]   # FRONT eye blobs (sum < 60) centres x 231.8 / 295.7 (mid +0.0875, centred), y 218.4
EYE_HW, EYE_HH = 0.060, 0.125         # ... 12 x 25 px each (incl. the ink)
#   VISOR band: FRONT blue rows (centred) y 189 z 6.22 half 0.855 / y 201 6.10 0.82; band bottom at the face centre y 197
#   (z 6.14 -> 6.12), its top between the horns y 164 (z 6.47); BACK half 0.955 (built to the front, the face view).
VISOR_IN, VISOR_OUT_BOT, VISOR_OUT_TOP = 0.70, 0.86, 0.92
VISOR_BOT, VISOR_TOP = 6.12, 6.47
TAB_PHI, TAB_W, TAB_D = 55.0, 18.0, 0.14   # "visor tabs": the band's lower edge drops to points at the face's sides (FRONT
                                      #   x 190 / 330 = X +-0.7 -> 55 deg off the front on r 0.86; down to y 213, z 5.98)
#   HORNS: FRONT tips (162, 150) / (365, 153) -> X -0.93 / 1.10, z 6.61 / 6.58 (centred +-1.01); BACK +-1.1 -> 1.06, z 6.60;
#   rising from the band's top-outer corner (|X| 0.80, z 6.36), flicking up-outward.
HORN_PATH = [(0.80, 6.36), (0.93, 6.44), (1.02, 6.52), (1.06, 6.62)]
HORN_R, HORN_FLAT = 0.085, 0.55       # "horn size": root radius, section flattening in Y (a fin-like tip)
# ---- ARMS ("floating arm pieces: a blue band ring round a white dome hand, glowing filament burst toward the body"):
#   FRONT R arm (image left) band corners (41,419) (66,417) (95,552) (69,554): centre (67.5, 485.5) -> X -1.875, z 3.255;
#   diameter 1.38 (r 0.69), thickness 0.26 along its axis, tilted 12 deg (bottom 29 px nearer the body over 135 px) -> the
#   ring axis points out-and-DOWN 12 deg. Hand white x 16-59 y 425-529: a hemisphere r 0.52 based at the band's centre
#   plane (apex X -2.39 = 1.875 + 0.515). Burst disc (orange 250,79,5) x 70-107 -> X -1.85..-1.48: 0.26 out of the band's
#   body-side face, spanning the band's height (r 0.66). Pale ring on it ~ r 0.40-0.50. The L arm is built mirrored.
ARM_C = (1.875, 0.0, 3.255)           # "arm position" (L; R mirrored)
ARM_TILT = 12.0                       # deg: the ring axis out-and-down
BAND_RI, BAND_RO, BAND_HT = 0.52, 0.69, 0.13   # "arm band" inner / outer radius, half-thickness
HAND_R = 0.52                         # "hand dome" radius
GLOW_R, GLOW_H = 0.66, 0.26           # "burst disc" radius + how far it domes toward the body (the drawn disc overlaps the
                                      #   orb outline by ~0.14: kept as drawn)
GLOW_RING = (0.40, 0.50)              # its pale ring (drawn segmented: built as one continuous ring, REPORTED)
TICKS = [(50.0, 15.0, 38.0), (0.0, 15.0, 38.0), (-60.0, 15.0, 38.0)]   # "hand marks": (azimuth from the front toward up,
                                      #   polar from the apex start, end) deg -- FRONT ticks at z 3.42 / 3.23 / 3.07 near the apex
TICK_HW = 3.5                         # tick half-width (deg)
BURST_N = 11                          # "burst strands" per arm (FRONT draws ~10-12 per side)
BURST_R = 0.016
# ---- TAIL ("jagged lightning tail attached under the body"): the FRONT ortho's bolt outline (R > 190, B < 140, opening
#   7x7 -> largest blob -> Moore trace -> Douglas-Peucker 2.5 px), (X, z); the eroded spike tips restored from the raw mask
#   (3x3 opening): spike tops (0.39, 1.54) / (0.80, 1.18); the tip from the bbox (y 812, x 384) -> (1.29, 0.0). The top edge
#   is raised from the drawn 1.82 into the orb (2.0: the orb's bottom is 1.86 at the axis).
BOLT_XZ = [(-0.25, 2.00), (0.22, 2.00), (0.26, 1.36), (0.39, 1.54), (0.68, 0.92), (0.80, 1.18), (1.29, 0.00), (0.84, 0.63),
           (0.745, 0.33), (0.41, 0.92), (0.29, 0.50), (-0.20, 1.25)]
BOLT_EDGE = 0.05                      # "bolt edge band": the drawn orange-red rim ~5 px
BOLT_T = 0.12                         # "bolt thickness" (half). NOT MEASURABLE: the 3/4 side draws the bolt face-on (a
                                      #   2D symbol); its top width bounds the half-depth to <= 0.24. REPORTED
SPARKS = [((-0.67, 1.73), (-0.15, 1.13), 9, -0.16), ((0.37, 1.76), (0.52, 1.48), 5, 0.16), ((0.80, 1.46), (0.93, 1.11), 6, -0.16),
          ((1.18, 0.98), (1.07, 0.68), 5, 0.16), ((-0.23, 0.89), (0.03, 0.78), 5, -0.16), ((0.10, 0.78), (0.12, 0.58), 4, 0.16),
          ((0.25, 0.54), (0.30, 0.21), 6, -0.16), ((0.58, 0.58), (0.62, 0.34), 4, 0.16), ((0.88, 0.44), (1.05, 0.08), 6, -0.16)]
#   "spark crackles": FRONT 3x zoom endpoints (start XZ, end XZ, kinks, Y offset) -- simplified jagged strands (REPORTED)
SPARK_R, SPARK_AMP = 0.011, 0.035
# ---- bake / material
BAKE_RES = (1024, 512)
HIGH_DENSITY = 3                      # the parts' bake high: rows + columns x this (smooth normal map, no facets)
HIGH_SMOOTH = 4                       # Taubin passes over the SDF shell's bake high (lambda 0.5 / mu -0.53): the marching-tets
                                      #   facet noise baked as speckle on the orb + neck (v1 run 2 head close-up)
AO_SAMPLES = 64                       # AO bake samples (16 left grain on the glossy orb: v1 run 2)
AO_FLOOR = {"clean": 1.0, "other": 0.55}   # AO floors per region class (style guide): the white face / hands / eyes and
                                      #   every glowing region stay clean (no AO), the rest never bakes darker than 0.55
SPECULAR = 0.50                       # "glossy": the sheet paints a glossy orb
# ---- clips (PLACEHOLDER: the artist has not given Storm Vessel's movement or hover height). 24 fps; integer harmonics.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_LIFT = (0.14, 0.34)              # "hover bob": body lift range (u), 2 bobs per loop (rest pose = the floor); the floor
                                      #   clearance: the tail sway + roll + the bolt flicker can lower the tip ~0.12
HEAD_BOB, ARM_BOB = 0.06, 0.05        # "floating pieces": head / arms extra bob (u), out of phase with the body
IDLE_PITCH, IDLE_ROLL = 1.0, 0.8      # "slight wander" deg
FLICKER = (0.04, 0.02)                # "filament flicker": scale pulse on tail.002 (bolt + sparks) / on the arm pieces
TAIL_SWAY = 2.0                       # tail sway deg
WALK_N = 48                           # "drift loop": 2 s
WALK_LIFT, WALK_BOB = 0.20, 0.03
WALK_PITCH, WALK_PITCH_OSC, WALK_ROLL = 8.0, 1.0, 0.8
WALK_TRAIL = (0.06, 0.05, 6.0)        # head / arms trail back (u, +Y), tail trails back (deg)

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
          "design/reference/storm_vessel-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; front + back true orthos, side a 3/4 slant (66 deg); floor z = 0 at "
                   "the bolt tip (it floats)",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
SY = ORB_RY / ORB_R                   # the body's Y scale (orb + neck share it)
TAU = 2.0 * math.pi
MIRX = np.array([-1.0, 1.0, 1.0])


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


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


def dens_params(ts, k):
    """insert k-1 evenly spaced parameters between consecutive entries (the bake high's denser copy of a part)"""
    ts = list(ts)
    if k <= 1:
        return np.array(ts, float)
    out = []
    for a, b in zip(ts[:-1], ts[1:]):
        out += [a + (b - a) * q / k for q in range(k)]
    return np.array(out + [ts[-1]], float)


def dens_phis(ph, k):
    ph = list(ph) + [ph[0] + TAU]
    return dens_params(ph, k)[:-1]


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body SDF: orb (ellipsoid) + neck - cavity
t_sdf = time.time()
OC = np.array(ORB_C, float)
NECK = FP.Profile([(z, -r, r, r) for z, r in NECK_ROWS])


def to_s(P):
    Q = np.array(np.atleast_2d(P), float)
    Q[:, 1] = Q[:, 1] / SY
    return Q


def sd_orb(P):
    return (np.linalg.norm(to_s(P) - OC, axis=1) - ORB_R) * SY        # scaled-space distance x SY (conservative)


def sd_neck(P):
    return NECK.sd(to_s(P)) * SY


def sd_cav(P):
    Q = to_s(P)
    return np.maximum(np.hypot(Q[:, 0], Q[:, 1]) - CAV_R, CAV_FLOOR - Q[:, 2]) * SY


def sd_body(P):
    return SD.smax(SD.smin(sd_orb(P), sd_neck(P), NECK_K), -sd_cav(P), CAV_K)


_lo = np.array([-ORB_R, -ORB_RY, OC[2] - ORB_R]) - 0.06
_hi = np.array([ORB_R, ORB_RY, NECK_ROWS[-1][0]]) + 0.06
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
g.apply(sd_orb, OC - np.array([ORB_R, ORB_RY, ORB_R]), OC + np.array([ORB_R, ORB_RY, ORB_R]), 0.0)
_nr = max(r for _, r in NECK_ROWS)
g.apply(sd_neck, np.array([-_nr, -_nr * SY, NECK_ROWS[0][0]]), np.array([_nr, _nr * SY, NECK_ROWS[-1][0]]), NECK_K)
g.apply(sd_cav, np.array([-CAV_R, -CAV_R * SY, CAV_FLOOR]), np.array([CAV_R, CAV_R * SY, NECK_ROWS[-1][0] + 0.1]), CAV_K,
        mode="subtract")
t_poly = time.time()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "seconds_fields": round(t_poly - t_sdf, 1),
                 "seconds": round(time.time() - t_sdf, 1)}
del g
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation
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
assert np.all(_bb_l / _bb_h > 0.99), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))
print("RETOPO", json.dumps(report["retopo"]))


# =========================================================================== 3. iso cuts (shell + parts)
def iso_cut(bm, LAY, key, tau, gate=None):
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


def cut_mesh(V, F, fields, cuts_fn):
    """-> (V', F', per-face mean of every field, cut log); cuts_fn(LAY) -> [(key, tau, gate)]"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {k: bm.verts.layers.float.new(k) for k in fields}
    for v in bm.verts:
        for k, arr in fields.items():
            v[LAY[k]] = float(arr[v.index])
    log = [iso_cut(bm, LAY, k_, tau_, gate_) for k_, tau_, gate_ in cuts_fn(LAY)]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    CV = np.array([v.co[:] for v in bm.verts])
    CF = [[v.index for v in f.verts] for f in bm.faces]
    FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
    bm.free()
    return CV, CF, FVAL, log


CV, CF = LV, LF                       # the shell carries one region (body): the glowing neck interior is its own closed
shell_reg = np.full(len(CF), "body", dtype=object)   # pool part (a decimated cavity floor holds no vertex to cut on)
report["iso_cuts"] = {"shell": [], "shell_tris_after_cuts": len(CF)}


# =========================================================================== 4. parts (all closed)
def orb_front_y(x, z):
    """the orb's front (-Y) surface at (x, z): the ellipsoid (the core sits far from the neck blend)"""
    s = 1.0 - (np.asarray(x, float) ** 2 + (np.asarray(z, float) - OC[2]) ** 2) / ORB_R ** 2
    return -ORB_RY * np.sqrt(np.maximum(s, 0.0))


def seam_marks():
    return [math.radians(90.0 + 360.0 * k / FRAME_SEAMS) for k in range(FRAME_SEAMS)]


def build_frame(d=1):
    """segmented ring frame: a closed ring (torus topology), section loop in (r, front/back), Y following the orb surface;
    columns with exact seam strips; seams own the front-face cells inside a strip"""
    marks = seam_marks()
    ph = FP.phis_with(80, marks, [SEAM_HW] * len(marks))
    ph = dens_phis(ph, d)
    rs = dens_params([FRAME_IN, FRAME_IN + (FRAME_OUT - FRAME_IN) / 3, FRAME_IN + 2 * (FRAME_OUT - FRAME_IN) / 3, FRAME_OUT], d)
    loop = [(r, 0) for r in rs] + [(FRAME_OUT, 1), (FRAME_IN, 1)]          # (r, side) 0 front, 1 back
    P = np.zeros((len(loop), len(ph), 3))
    for i, (r, sd_) in enumerate(loop):
        x = r * np.cos(ph); z = CORE_Z + r * np.sin(ph)
        P[i, :, 0] = x; P[i, :, 2] = z
        P[i, :, 1] = orb_front_y(x, z) + (FRAME_SINK if sd_ else -FRAME_PROUD)
    V, F, FIJ = FP.grid_mesh(P, loop_rows=True)
    nfront = len(rs) - 1
    R = []
    for (i, j) in FIJ:
        a0 = ph[j]; a1 = ph[(j + 1) % len(ph)] + (TAU if j + 1 == len(ph) else 0.0)
        am = 0.5 * (a0 + a1)
        in_seam = any(abs(((am - m + math.pi) % TAU) - math.pi) < SEAM_HW for m in marks)
        R.append("frame_seam" if (in_seam and i < nfront) else "frame")
    return V, F, R


def build_core_dome(d=1):
    """the glowing inset ball: front rows domed CORE_BULGE out of the orb surface, back rows buried; regions by radius"""
    rs = dens_params([0.0, 0.05, CORE_DEEP_R, 0.17, 0.24, 0.31, 0.37, CORE_RIM_R, 0.445, FRAME_IN], d)
    ph = dens_phis(np.linspace(0.0, TAU, 40, endpoint=False), d)
    rows = [(r, 0) for r in rs] + [(FRAME_IN, 1), (0.0, 1)]
    P = np.zeros((len(rows), len(ph), 3))
    for i, (r, sd_) in enumerate(rows):
        x = r * np.cos(ph); z = CORE_Z + r * np.sin(ph)
        P[i, :, 0] = x; P[i, :, 2] = z
        P[i, :, 1] = orb_front_y(x, z) + (FRAME_SINK if sd_ else -CORE_BULGE * (1.0 - (r / FRAME_IN) ** 2))
    V, F, FIJ = FP.grid_mesh(P)
    R = []
    for (i, j) in FIJ:
        if i >= len(rs) - 1:
            R.append("core"); continue
        rm = 0.5 * (rows[i][0] + rows[i + 1][0])
        R.append("core_deep" if rm < CORE_DEEP_R else ("core_rim" if rm > CORE_RIM_R else "core"))
    return V, F, R


def dome_front_y(x, z):
    r = np.hypot(x, np.asarray(z) - CORE_Z)
    return orb_front_y(x, z) - CORE_BULGE * (1.0 - np.minimum(r / FRAME_IN, 1.0) ** 2)


def build_core_fils():
    Vs, Fs = [], []
    for k in range(CORE_FIL_N):
        a0 = math.radians(14.0 + 360.0 * k / CORE_FIL_N)
        sg = 1.0 if k % 2 == 0 else -1.0
        s = np.linspace(0.0, 1.0, 12)
        r = 0.07 + 0.31 * s
        ang = a0 + sg * (0.28 * np.sin(1.5 * math.pi * s) + 0.45 * smoothstep(0.80, 1.0, s))   # a wave + the end hook
        x = r * np.cos(ang); z = CORE_Z + r * np.sin(ang)
        C = np.stack([x, dome_front_y(x, z) - 0.006, z], 1)
        rad = CORE_FIL_R * np.sin(np.pi * np.clip(s * 1.1, 0, 1)) ** 0.35
        rad[0] = rad[-1] = 0.0
        Vs.append(FP.tube(C, rad, 4, up=(0.0, -1.0, 0.0)))
    return Vs


def build_spray():
    Vs = []
    for k in range(SPRAY_N):
        a = math.radians(-90.0 + 360.0 * k / SPRAY_N + 17.0)
        rt, zt = SPRAY_TIP_R[k], SPRAY_TIP_Z[k]
        ctrl = np.array([(0.06, CAV_FLOOR - 0.04), (0.12, CAV_FLOOR + 0.10), (0.55 * rt, 5.40), (0.85 * rt, zt - 0.05),
                         (rt, zt)])
        D = SD.catmull(np.stack([ctrl[:, 0], np.zeros(len(ctrl)), ctrl[:, 1]], 1), 4)
        C = np.stack([D[:, 0] * math.cos(a), D[:, 0] * math.sin(a) * SY, D[:, 2]], 1)
        s = np.linspace(0.0, 1.0, len(C))
        rad = SPRAY_R * (1.0 - 0.55 * s)
        rad[0] = rad[-1] = 0.0
        Vs.append(FP.tube(C, rad, 5))
    return Vs


def face_rows(d=1):
    ts = dens_params([0, 12, 24, 36, 44, 50, 55, 60, 65, 70, 75, 80, 85, 90], d)      # deg from the bottom pole
    rows = [(FACE_R * math.sin(math.radians(t_)), FACE_TOP - FACE_D * math.cos(math.radians(t_))) for t_ in ts]
    rows += [(FACE_R, 0.5 * (FACE_TOP + HEAD_TOP)), (FACE_R, HEAD_TOP), (0.0, HEAD_TOP)]
    return rows


def face_phis(d=1):
    dense = np.radians(np.arange(-45.0, 45.0 + 1e-9, 3.0))
    coarse = np.radians(np.arange(54.0, 306.0 + 1e-9, 9.0))
    return dens_phis(np.sort(np.concatenate([np.mod(dense, TAU), coarse])), d)


def build_face(d=1):
    P = FP.revolve(face_rows(d), face_phis(d), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    V, F, FIJ = FP.grid_mesh(P)
    return V, F


def eye_field(P):
    P = np.atleast_2d(P)
    f = np.full(len(P), -1.0)
    for ex, ez in EYES:
        e = min(EYE_HW, EYE_HH) * (1.0 - np.sqrt(((P[:, 0] - ex) / EYE_HW) ** 2 + ((P[:, 2] - ez) / EYE_HH) ** 2))
        f = np.maximum(f, e)
    return np.where(P[:, 1] < -0.2, f, -1.0)


def visor_bot(phi):
    """band's lower edge z at azimuth phi (from the front, toward +X): the two tabs at +-TAB_PHI"""
    z = np.full(np.shape(phi), VISOR_BOT, float)
    for c in (math.radians(TAB_PHI), -math.radians(TAB_PHI)):
        dd = np.abs(((np.asarray(phi) - c + math.pi) % TAU) - math.pi)
        z = z - TAB_D * np.maximum(0.0, 1.0 - dd / math.radians(TAB_W))
    return z


def build_visor(d=1):
    marks = [math.radians(TAB_PHI), -math.radians(TAB_PHI)]
    ph = FP.phis_with(72, marks + marks, [0.0, 0.0, math.radians(TAB_W), math.radians(TAB_W)])
    ph = dens_phis(ph, d)
    zb = visor_bot(ph)
    loop = [("in", "b"), ("out_b", "b"), ("out_m", "m"), ("out_t", "t"), ("in", "t")]
    rr = {"in": VISOR_IN, "out_b": VISOR_OUT_BOT, "out_m": 0.5 * (VISOR_OUT_BOT + VISOR_OUT_TOP) + 0.012, "out_t": VISOR_OUT_TOP}
    P = np.zeros((len(loop), len(ph), 3))
    for i, (rk, zk) in enumerate(loop):
        z = zb if zk == "b" else (np.full(len(ph), VISOR_TOP) if zk == "t" else 0.5 * (zb + VISOR_TOP))
        P[i, :, 0] = rr[rk] * np.sin(ph); P[i, :, 1] = -rr[rk] * np.cos(ph); P[i, :, 2] = z
    V, F, FIJ = FP.grid_mesh(P, loop_rows=True)
    return V, F


def build_horn(d=1):
    hp = np.array(HORN_PATH)
    D = SD.catmull(np.stack([hp[:, 0], np.zeros(len(hp)), hp[:, 1]], 1), 4 * d)
    s = np.linspace(0.0, 1.0, len(D))
    rad = HORN_R * np.sqrt(np.clip(1.0 - s, 0, 1)) * smoothstep(0.0, 0.08, s)
    rad[0] = rad[-1] = 0.0
    return FP.tube(D, rad, 6 * d, flat=HORN_FLAT, up=(0.0, 1.0, 0.0))


# ---- arms: built for the L arm (+X) in its local frame (axis a out-and-down; azimuth from the front -Y toward 'up')
ARM_A = np.array([math.cos(math.radians(ARM_TILT)), 0.0, -math.sin(math.radians(ARM_TILT))])
ARM_O = np.array(ARM_C, float)
_a, _u, _v = FP.frame_of(ARM_A, (0.0, -1.0, 0.0))
ARM_UPSIGN = 1.0 if _v[2] > 0 else -1.0          # azimuth +ve toward world up


def arm_revolve(rows, phis):
    return FP.revolve(rows, ARM_UPSIGN * np.asarray(phis), ARM_O, ARM_A, (0.0, -1.0, 0.0))


def build_band(d=1):
    ph = dens_phis(np.linspace(0.0, TAU, 40, endpoint=False), d)
    loop = [(BAND_RI, -BAND_HT), (BAND_RO, -BAND_HT), (BAND_RO + 0.012, 0.0), (BAND_RO, BAND_HT), (BAND_RI, BAND_HT)]
    V, F, _ = FP.grid_mesh(arm_revolve(loop, ph), loop_rows=True)
    return V, F


def build_hand(d=1):
    marks = [math.radians(t_[0]) for t_ in TICKS]
    ph = dens_phis(FP.phis_with(40, marks, [math.radians(TICK_HW)] * len(marks)), d)
    th0 = sorted(set([0.0, 8.0, 15.0, 22.0, 30.0, 38.0, 48.0, 58.0, 68.0, 78.0, 90.0] + [t_[1] for t_ in TICKS] + [t_[2] for t_ in TICKS]))
    th = dens_params(th0, d)
    rows = [(HAND_R * math.sin(math.radians(t_)), HAND_R * math.cos(math.radians(t_))) for t_ in th] + [(0.0, 0.0)]
    V, F, FIJ = FP.grid_mesh(arm_revolve(rows, ph))
    R = []
    for (i, j) in FIJ:
        if i >= len(th) - 1:
            R.append("hand"); continue
        tm = 0.5 * (th[i] + th[i + 1])
        a0 = ph[j]; a1 = ph[(j + 1) % len(ph)] + (TAU if j + 1 == len(ph) else 0.0)
        am = 0.5 * (a0 + a1)
        hit = any(t0 < tm < t1 and abs(((am - math.radians(az) + math.pi) % TAU) - math.pi) < math.radians(TICK_HW)
                  for az, t0, t1 in TICKS)
        R.append("hand_mark" if hit else "hand")
    return V, F, R


def build_glow(d=1):
    ph = dens_phis(np.linspace(0.0, TAU, 40, endpoint=False), d)
    tr = [math.degrees(math.asin(GLOW_RING[0] / GLOW_R)), math.degrees(math.asin(GLOW_RING[1] / GLOW_R))]
    ts = dens_params(sorted(set([0.0, 12.0, 24.0, tr[0], tr[1], 62.0, 76.0, 90.0])), d)
    rows = [(GLOW_R * math.sin(math.radians(t_)), -BAND_HT - GLOW_H * math.cos(math.radians(t_))) for t_ in ts]
    rows += [(0.0, -BAND_HT + 0.01)]
    V, F, FIJ = FP.grid_mesh(arm_revolve(rows, ph))
    R = []
    for (i, j) in FIJ:
        if i >= len(ts) - 1:
            R.append("arm_glow"); continue
        tm = 0.5 * (ts[i] + ts[i + 1])
        R.append("arm_glow_ring" if tr[0] < tm < tr[1] else "arm_glow")
    return V, F, R


def build_burst():
    Vs = []
    for k in range(BURST_N):
        az = TAU * k / BURST_N + 0.2
        sg = 1.0 if k % 2 == 0 else -1.0
        s = np.linspace(0.0, 1.0, 12)
        r = 0.45 + 0.50 * s
        h = -BAND_HT - 0.14 - 0.14 * s - 0.04 * s * s
        azs = az + sg * 0.12 * np.sin(2.0 * math.pi * s)
        C = np.array([arm_revolve([(r[q], h[q])], [azs[q]])[0, 0] for q in range(len(s))])
        rad = BURST_R * (1.0 - 0.6 * s)
        rad[0] = rad[-1] = 0.0
        Vs.append(FP.tube(C, rad, 4))
    return Vs


def build_sparks():
    Vs = []
    for k, (a, b, n, y) in enumerate(SPARKS):
        C = FP.jag_path((a[0], y, a[1]), (b[0], y, b[1]), n, SPARK_AMP, (0.0, 1.0, 0.0), phase=0.7 * k)
        D = []
        for q in range(len(C) - 1):                          # 3 points per straight jag segment (sharp kinks kept)
            for tq in (0.0, 0.5):
                D.append(C[q] * (1 - tq) + C[q + 1] * tq)
        D.append(C[-1])
        D = np.array(D)
        rad = np.full(len(D), SPARK_R); rad[0] = rad[-1] = 0.0
        Vs.append(FP.tube(D, rad, 4))
    return Vs


def build_pool(d=1):
    rows = [(0.0, CAV_FLOOR + POOL_T), (0.6 * POOL_R, CAV_FLOOR + POOL_T), (POOL_R - 0.02, CAV_FLOOR + POOL_T - 0.006),
            (POOL_R, CAV_FLOOR + POOL_T - 0.02), (POOL_R, CAV_FLOOR - 0.03), (0.0, CAV_FLOOR - 0.03)]
    ph = dens_phis(np.linspace(0.0, TAU, 36, endpoint=False), d)
    P = FP.revolve(rows, ph, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    P[..., 1] *= SY
    V, F, _ = FP.grid_mesh(P)
    return V, F


def merge(meshes, region):
    V, F, R, o = [], [], [], 0
    for m in meshes:
        V.append(np.asarray(m[0])); F += [[i + o for i in f] for f in m[1]]
        R += (list(m[2]) if len(m) > 2 else [region] * len(m[1]))
        o += len(m[0])
    return np.vstack(V), F, R


PARTS = []
_v, _f, _r = build_frame(); PARTS.append(FP.Part("core_frame", _v, _f, _r, bone="body"))
_v, _f, _r = build_core_dome(); PARTS.append(FP.Part("core_dome", _v, _f, _r, bone="body"))
_v, _f, _r = merge(build_core_fils(), "core_fil"); PARTS.append(FP.Part("core_filaments", _v, _f, _r, bone="body"))
_v, _f, _r = merge(build_spray(), "filament"); PARTS.append(FP.Part("neck_spray", _v, _f, _r, bone="body"))
_v, _f = build_pool(); PARTS.append(FP.Part("neck_pool", _v, _f, ["neck_glow"] * len(_f), bone="body"))
_fv, _ff = build_face()
_vv, _vf = build_visor()
_hv, _hf = build_horn()
PARTS.append(FP.Part("head_face", _fv, _ff, ["face"] * len(_ff), bone="head"))
PARTS.append(FP.Part("visor", _vv, _vf, ["visor"] * len(_vf), bone="head"))
PARTS.append(FP.Part("horn.L", _hv, _hf, ["visor"] * len(_hf), bone="head"))
PARTS.append(FP.Part("horn.R", _hv * MIRX, [f[::-1] for f in _hf], ["visor"] * len(_hf), bone="head"))
_bv, _bf = build_band()
_hdv, _hdf, _hdr = build_hand()
_gv, _gf, _gr = build_glow()
_uv, _uf, _ur = merge(build_burst(), "filament")
for sfx, bn, M in ((".L", "arm_L", None), (".R", "arm_R", MIRX)):
    for nm, (V_, F_, R_) in (("arm_band", (_bv, _bf, ["arm_band"] * len(_bf))), ("hand", (_hdv, _hdf, _hdr)),
                             ("arm_glow", (_gv, _gf, _gr)), ("burst", (_uv, _uf, _ur))):
        if M is None:
            PARTS.append(FP.Part(nm + sfx, V_, F_, R_, bone=bn))
        else:
            PARTS.append(FP.Part(nm + sfx, V_ * M, [f[::-1] for f in F_], R_, bone=bn))
_tv, _tf, _tr, BOLT_INFO = FP.bolt_plate(BOLT_XZ, BOLT_EDGE, BOLT_T)
PARTS.append(FP.Part("bolt", _tv, _tf, _tr, bone="tail"))
_v, _f, _r = merge(build_sparks(), "spark"); PARTS.append(FP.Part("sparks", _v, _f, _r, bone="tail"))
print("T parts_built", round(time.time() - T0, 1))
report["parts_built"] = {p.name: {"tris_before_cuts": p.tris(), "open_edges": FP.open_edges(p.F)} for p in PARTS}

# eyes: iso-cut into the face dome (front-projected ovals; the dome's front sector carries 3-deg columns)
part_log = {}
for p in PARTS:
    if p.name != "head_face":
        continue
    fld = {"eye": eye_field(p.V)}
    V2, F2, FV2, lg = cut_mesh(p.V, p.F, fld, lambda LAY: [("eye", 0.0, None)])
    assert np.any(FV2["eye"] > 0.0), "eye field never positive on the face"
    p.V, p.F, p.R = V2, F2, np.where(FV2["eye"] > 0.0, "eye", "face").tolist()
    p.info["eye_faces"] = int(np.sum(FV2["eye"] > 0.0))
    EYE_FV = FV2["eye"]
    part_log[p.name] = lg
report["iso_cuts"]["parts"] = part_log
print("CUTS parts", json.dumps(part_log))

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
_tip = float(PARTS[[p.name for p in PARTS].index("bolt")].V[:, 2].min())
_below = [(i[0], round(float(i[1][:, 2].min()), 4)) for i in ISL if i[0] != "bolt" and i[1][:, 2].min() < _tip + 0.02]
assert not _below, "a part dips to / below the floor (the bolt tip's rest z %.4f): %s" % (_tip, _below)
assert abs(_tip - lo0[2]) < 1e-9, "the bolt tip is the lowest point"
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
assert abs(float(VALL[:, 2].min())) < 1e-9, "rest pose: the bolt tip is the floor (min z 0)"
HV_s = HV - SHIFT
REG = ["body", "neck_glow", "frame", "frame_seam", "core", "core_deep", "core_rim", "core_fil", "filament", "visor",
       "face", "eye", "arm_band", "hand", "hand_mark", "arm_glow", "arm_glow_ring", "bolt", "bolt_hi", "spark"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "declared palette regions with no faces: %s" % _empty
# no region borrows a field: the eye (the one iso-cut region) owns only faces its field claims, and the field paints
_hp = PARTS[[p.name for p in PARTS].index("head_face")]
assert np.all(EYE_FV[np.array(_hp.R) == "eye"] > 0.0) and not np.any(EYE_FV[np.array(_hp.R) == "face"] > 0.0), "eye borrows"
assert np.any(EYE_FV > 0.0), "eye field never positive"
_eye_x = [np.mean(_hp.V[f], 0)[0] for f, r_ in zip(_hp.F, _hp.R) if r_ == "eye"]
assert min(_eye_x) < -0.2 and max(_eye_x) > 0.2, "both eyes cut"


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                  # opaque closed pieces: single-sided (glTF doubleSided false)
    bsdf.inputs["Specular IOR Level"].default_value = SPECULAR
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


GLOW_ALLOWED = {"neck_glow", "core", "core_deep", "core_rim", "core_fil", "filament", "arm_glow", "arm_glow_ring", "bolt",
                "bolt_hi", "spark"}
GLOW_CAP = 2.0


def glow_tiers(pal):
    """the sheet draws glow on the core, the filaments / neck spray, the arm bursts, the bolt + sparks only; every tier
    (and tier x emission_strength) under the 2.0 cap"""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    es = float(pal["material"].get("emission_strength", 1.0))
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "emission_strength": es,
            "only_drawn_glow": sorted(tier) == sorted(GLOW_ALLOWED), "cap": GLOW_CAP,
            "cap_pass": all(v <= GLOW_CAP and v * es <= GLOW_CAP for v in tier.values()),
            "max_effective": round(max(tier.values()) * es, 3)}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)   # palette <-> regions 1:1
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
_gt = report["glow_tiers"]["default"]
assert _gt["only_drawn_glow"] and _gt["cap_pass"], _gt

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = np.ones(len(FALL))                       # no value jitter: the sheet's orb is glossy + clean (jitter = mottling)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the orb centre -> the core's front point
anchor = OC - SHIFT
landmark = np.array([0.0, float(dome_front_y(0.0, CORE_Z)), CORE_Z]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "orb centre -> the glowing core's front point (the core + the eyes face -Y)",
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

tris_main = tri_count_F(FALL)
_shell_tris = tri_count_F(CF)
report["tris"] = {"total": tris_main, "window": TRI_BUDGET, "shell": _shell_tris,
                  "shell_before_cuts": report["retopo"]["decimated_tris"],
                  "per_part": {"body shell (orb + neck)": _shell_tris, **{p.name: p.tris() for p in PARTS}},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role: window [%d, %d]. The orb + neck shell is light (~%d decimated tris); the core "
                            "(frame + dome + strands), the floating head (face, visor, horns), the two floating arms "
                            "(band, hand, burst disc, strands), the bolt + sparks and the neck spray (closed parts) carry "
                            "the rest." % (TRI_BUDGET[0], TRI_BUDGET[1], BODY_TRIS))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"per_piece": open_e, "closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (SDF shell; lathes / rings / tubes with welded poles; "
                                "the extruded bolt); the eyes are an iso-cut region, the seams / ticks / rings / core zones "
                                "are grid-aligned regions, the neck glow is its own pool part (no decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1)},
                     "sheet": {"front": "H 6.62 W 4.62", "back": "H 6.60 W 4.12", "side (3/4)": "H 6.63 W 3.05"},
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
low["conquest_source"] = "from scratch: design/reference/storm_vessel-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"


def _box(*names):
    A_ = np.vstack([VALL[RANGE[n][0]:RANGE[n][1]] for n in names])
    return [A_.min(0).tolist(), A_.max(0).tolist()]


low["conquest_focus"] = json.dumps({
    "head_detail": _box("head_face", "visor", "horn.L", "horn.R"),
    "core_detail": _box("core_frame", "core_dome"),
    "arm_detail": _box("arm_band.R", "hand.R", "arm_glow.R", "burst.R"),
    "tail_detail": _box("bolt", "sparks"),
    "views": {"head_detail": [0.0, 4.0], "core_detail": [0.0, 0.0], "arm_detail": [-25.0, 4.0], "tail_detail": [0.0, 2.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow_tiers", "centre_shift")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
_hd = HIGH_DENSITY
_hiparts = [build_frame(_hd)[:2], build_core_dome(_hd)[:2], merge(build_core_fils(), "x")[:2], merge(build_spray(), "x")[:2],
            build_pool(_hd), build_face(_hd), build_visor(_hd), build_horn(_hd)]
_hiparts.append((_hiparts[-1][0] * MIRX, [f[::-1] for f in _hiparts[-1][1]]))
_arm_hi = [build_band(_hd), build_hand(_hd)[:2], build_glow(_hd)[:2], merge(build_burst(), "x")[:2]]
_hiparts += _arm_hi + [(V_ * MIRX, [f[::-1] for f in F_]) for V_, F_ in _arm_hi]
_hiparts += [FP.bolt_plate(BOLT_XZ, BOLT_EDGE, BOLT_T)[:2], merge(build_sparks(), "x")[:2]]
def taubin(V, F, n, lam=0.5, mu=-0.53):
    Fa = np.asarray(F, dtype=np.int64)
    E = np.concatenate([Fa[:, [0, 1]], Fa[:, [1, 2]], Fa[:, [2, 0]]])
    E = np.concatenate([E, E[:, ::-1]])
    deg = np.bincount(E[:, 0], minlength=len(V)).astype(float)
    V = np.array(V, float)
    for _ in range(n):
        for w in (lam, mu):
            S = np.zeros_like(V); np.add.at(S, E[:, 0], V[E[:, 1]])
            V = V + w * (S / np.maximum(deg, 1)[:, None] - V)
    return V


HV_bake = taubin(HV_s, HF, HIGH_SMOOTH)
report["bake_high_smooth"] = {"passes": HIGH_SMOOTH, "max_move": round(float(np.linalg.norm(HV_bake - HV_s, axis=1).max()), 5)}
PH_V, PH_F = [HV_bake], [list(f) for f in HF]
o_ = len(HV_s)
for V, F in _hiparts:
    PH_V.append(np.asarray(V) - SHIFT); PH_F += [[i + o_ for i in f] for f in F]; o_ += len(V)
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
nt_ = MAT_BODY.node_tree
tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_flat()                       # bake AGAINST THE FLAT low the game shows (flat_shaded rule)
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
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, AO_SAMPLES)):
    nt_.nodes.active = node
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
body_faces = np.ones(nf, bool)
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets: orb + neck - cavity) + every part re-generated at %dx rows + columns (the smooth curvature the flat low faces bake their normals from; tubes + bolt as built)" % HIGH_DENSITY,
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0
_clean = np.isin(rid, [REG.index(r) for r in sorted(GLOW_ALLOWED | {"face", "eye", "hand", "hand_mark"})])
_tex_clean = texels_of(_clean, RA)
pa[_tex_clean, :3] = np.maximum(pa[_tex_clean, :3], AO_FLOOR["clean"])
pa[~_tex_clean, :3] = np.maximum(pa[~_tex_clean, :3], AO_FLOOR["other"])
bstats["ao_floor"] = {"rule": AO_FLOOR, "clean_texels": int(_tex_clean.sum()),
                      "ao_p05_after_floor": round(float(np.percentile(pa[alltex_a, 0], 5)), 4)}
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
bsdf = nt_.nodes["Principled BSDF"]
mul = nt_.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt_.links.new(nt_.nodes["col"].outputs["Color"], ia); nt_.links.new(ta.outputs["Color"], ib)
nt_.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt_.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt_.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt_.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
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
                     "provenance": "pixel-sampled from the drawn surfaces + the sheet's six chips, cited per region "
                                   "(see the palette notes)"}
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


TAIL_PTS = np.array([(0.0, 0.0, 1.95), (0.62, 0.0, 0.95), (1.29, 0.0, 0.0)])     # bolt centreline: orb bottom -> mid -> tip
ARM_HEAD = ARM_O - ARM_A * (BAND_HT + GLOW_H)
ARM_TAIL = ARM_O + ARM_A * HAND_R
BONES = [("body", P_(OC), P_(OC + np.array([0.0, 0.0, 0.6])), "root"),
         ("head", P_((0.0, 0.0, FACE_TOP - FACE_D)), P_((0.0, 0.0, VISOR_TOP)), "body"),
         ("arm_L", P_(ARM_HEAD), P_(ARM_TAIL), "body"),
         ("arm_R", P_(ARM_HEAD * MIRX), P_(ARM_TAIL * MIRX), "body"),
         ("tail.001", P_(TAIL_PTS[0]), P_(TAIL_PTS[1]), "body"),
         ("tail.002", P_(TAIL_PTS[1]), P_(TAIL_PTS[2]), "tail.001")]
CONNECT = {"tail.002"}
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

# ---- weights (<= 4 influences): every detached piece rigid on its own bone; the bolt + sparks blend down the tail chain
TAIL_PARENT_S0 = -0.15                # the body -> tail.001 blend starts this far above the bolt top (along the chain)
Wt = np.zeros((len(VALL), len(DEFORM)))
OWNER = {"shell": "body"}
for p in PARTS:
    OWNER[p.name] = p.bone
_seg = np.linalg.norm(np.diff(TAIL_PTS, axis=0), axis=1)
_Lt = float(_seg.sum())


def chain_s(P):
    """arc-length parameter of the nearest point on the tail centreline"""
    best_d = np.full(len(P), np.inf); best_s = np.zeros(len(P)); s0 = 0.0
    for k in range(len(TAIL_PTS) - 1):
        a = TAIL_PTS[k]; b = TAIL_PTS[k + 1]; ab = b - a
        tt = np.clip(((P - a) @ ab) / float(ab @ ab), 0.0, 1.0)
        d = np.linalg.norm(P - (a + tt[:, None] * ab), axis=1)
        m = d < best_d
        best_d[m] = d[m]; best_s[m] = s0 + tt[m] * _seg[k]
        s0 += _seg[k]
    return best_s


for name, (a_, b_) in RANGE.items():
    bn = OWNER[name]
    if bn == "tail":
        Pw = VALL[a_:b_] + SHIFT
        Wv = K.vine_weights(chain_s(Pw), _Lt, 2, parent_s0=TAIL_PARENT_S0)
        Wt[a_:b_, J["body"]] = Wv[:, 0]; Wt[a_:b_, J["tail.001"]] = Wv[:, 1]; Wt[a_:b_, J["tail.002"]] = Wv[:, 2]
    else:
        Wt[a_:b_, J[bn]] = 1.0
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
                  "rule": "rigid ownership: shell + core + neck spray = body; face, visor, horns = head; band, hand, burst "
                          "disc + strands = arm_L / arm_R; bolt + sparks: rigkit.vine_weights along the bolt centreline "
                          "(body -> tail.001 from %.2f above the top, tail.001 -> tail.002)" % -TAIL_PARENT_S0}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (every deform bone in BOTH clips); integer cycles per loop
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
HH = report["measure"]["height"]


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
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        ph_b = TAU * 2 * u
        w_ = 0.5 * (1.0 - math.cos(ph_b))
        z = IDLE_LIFT[0] + (IDLE_LIFT[1] - IDLE_LIFT[0]) * w_
        P["body"] = ((0.0, 0.0, z), [("X", IDLE_PITCH * math.sin(TAU * u + 0.5)), ("Y", IDLE_ROLL * math.sin(TAU * u + 1.9))], None)
        P["head"] = ((0.0, 0.0, HEAD_BOB * math.sin(ph_b - 1.3)), [("Z", 2.0 * math.sin(TAU * u + 0.3))], None)
        fl = 1.0 + FLICKER[1] * math.sin(TAU * 6 * u)
        P["arm_L"] = ((0.0, 0.0, ARM_BOB * math.sin(ph_b - 2.2)), [], (fl, fl, fl))
        fr = 1.0 + FLICKER[1] * math.sin(TAU * 6 * u + 1.7)
        P["arm_R"] = ((0.0, 0.0, ARM_BOB * math.sin(ph_b - 2.9)), [], (fr, fr, fr))
        P["tail.001"] = (None, [("Y", TAIL_SWAY * math.sin(TAU * u + 0.9)), ("X", 0.6 * TAIL_SWAY * math.sin(ph_b - 1.8))], None)
        ft = 1.0 + FLICKER[0] * math.sin(TAU * 8 * u)
        P["tail.002"] = (None, [("Y", TAIL_SWAY * math.sin(TAU * u + 0.2))], (ft, ft, ft))
    else:
        z = WALK_LIFT + WALK_BOB * math.sin(TAU * 2 * u)
        P["body"] = ((0.0, 0.0, z), [("X", WALK_PITCH + WALK_PITCH_OSC * math.sin(TAU * 2 * u - 1.0)),
                                     ("Y", WALK_ROLL * math.sin(TAU * u + 0.6))], None)
        P["head"] = ((0.0, WALK_TRAIL[0], 0.03 * math.sin(TAU * 2 * u - 1.4)), [], None)
        P["arm_L"] = ((0.0, WALK_TRAIL[1], 0.03 * math.sin(TAU * 2 * u - 2.0)), [], None)
        P["arm_R"] = ((0.0, WALK_TRAIL[1], 0.03 * math.sin(TAU * 2 * u - 2.6)), [], None)
        P["tail.001"] = (None, [("X", -WALK_TRAIL[2] + 1.5 * math.sin(TAU * 2 * u - 2.2)), ("Y", 1.5 * math.sin(TAU * u + 1.6))], None)
        ft = 1.0 + FLICKER[0] * math.sin(TAU * 4 * u)
        P["tail.002"] = (None, [("X", -0.5 * WALK_TRAIL[2] + 1.5 * math.sin(TAU * 2 * u - 2.8))], (ft, ft, ft))
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


_bodyA, _bodyB = RANGE["shell"]
_headA, _headB = RANGE["head_face"]
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
        by = pbs["body"].matrix.col[1].xyz
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "head_gap": float(C[_headA:_headB, 2].min() - C[_bodyA:_bodyB, 2].max()),
                     "pitch": math.degrees(math.atan2(-by.y, by.z)), "roll": math.degrees(math.atan2(by.x, by.z)),
                     "root": float((rig.matrix_world @ pbs["root"].head).length),
                     "tail_tip": list(pbs["tail.002"].tail)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - arm_data.bones["body"].head_local.z
    tt_ = np.array([r["tail_tip"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "head_gap_min": round(float(g_("head_gap").min()), 4),
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "pitch_deg": [round(float(g_("pitch").min()), 3), round(float(g_("pitch").max()), 3)],
                    "roll_deg": [round(float(g_("roll").min()), 3), round(float(g_("roll").max()), 3)],
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist()}
    # nothing dips below the floor in any clip frame (it floats: the rest pose touches z 0, the clips only lift)
    assert clip_rep[cn]["clearance_min_z"] >= -1e-4, ("below the floor", cn, clip_rep[cn]["clearance_min_z"])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hover"] = {"rest_height_units": HH, "idle_lift_units": list(IDLE_LIFT), "walk_lift_units": WALK_LIFT,
                "rule": "rest pose: the bolt tip on the floor (contract feet_origin, z 0); idle keys body.location z = "
                        "IDLE_LIFT[0] .. [1] (slow bob), walk WALK_LIFT + bob; the contract root never moves. The in-game "
                        "hover height is the artist's open question"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "storm_vessel v1: root > body > head, arm_L, arm_R, tail.001 > tail.002"
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Storm Vessel's movement or hover height; open "
                               "question): idle = 4 s hover bob (body +%.2f..%.2f u, 0.5 Hz), head + arms bobbing out of "
                               "phase, a small scale-pulse flicker on the arm pieces (+-%.0f%%) and the bolt's lower half "
                               "+ sparks (+-%.0f%%), tail sway; walk = 2 s in-place hover-drift (%.0f deg forward lean; head "
                               "+ arms trailing, tail trailing)" % (IDLE_LIFT[0], IDLE_LIFT[1], 100 * FLICKER[1],
                                                                    100 * FLICKER[0], WALK_PITCH))
low["conquest_locomotion"] = "hover (floats): rest pose bolt tip z 0 per contract; the clips lift the body"
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
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "attributes": sorted(p["attributes"])} for n, p in prims],
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'storm_vessel' (one opaque body primitive); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("HOVER", json.dumps(rep["hover"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
