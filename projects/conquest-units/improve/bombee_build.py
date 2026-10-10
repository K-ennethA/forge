"""Bombee -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/bombee_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)"
(section BOMBEE) + the sheet design/reference/bombee-character-sheet.png (intake: design/reference/bombee/intake.json).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The sheet's FRONT is drawn three-quarter, so every constant is
measured off the two TRUE orthos (intake ortho_frame.views = back, side):
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 1000.5) / 100 ; z = (714 - y_px) / 100
  BACK view (the unit's right on image right):          X = -(x_px - 612) / 100  ; z = (694 - y_px) / 100 + 0.21
(1000.5 / 612 = the body ball's own centre columns: side ball x 831-1170, back ball x 456-768; 714 = the side floor
line at the spike tips; the back view's floor line 694 is the BALL BOTTOM, which the side view puts 0.21 above the
spike tips, hence + 0.21). X +ve = the unit's LEFT (L). The drawn views disagree in places (a painterly sheet, not a CAD
turnaround: e.g. the stinger sits 0.6 ABOVE the ball centre in the side + 3/4 front views but ~0.8 BELOW it in the back
view; the wings' pointed roots are drawn OVER the body in both the back and the side view); each such constant says
which view it follows and why.

Pipeline:
  1. BODY = one SDF: a near-spherical ball (an ellipsoid with a fuller top half). Marching tets = the HIGH (bake
     source); collapse decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: the cream FACE disc on the front (a bent cut: tilted forward at
     the top, upright below the eyes, as the side view draws it) and the black / yellow BANDS = planes across the body
     axis (tilted BAND_TILT, rear up) so from the back they read as concentric rings round the stinger.
  3. PARTS (bombee_parts.py): the conical STINGER on the rear pole of the band axis, four conical foot SPIKES on the
     underside (their tips are the floor), closed conforming face DECALS (eye = dark rim + iris + pupil + highlight,
     smile line, blush), and two leaf / petal WINGS seated on the top of the ball: a single-surface membrane (wing
     material: alphaMode BLEND double-sided, slightly translucent, the firefly smoke path) + a closed tan diamond-lattice
     of vein ribbons + a closed brown rim tube.
  4. palette (palettes.py regions -> Col / Glow + Col alpha), Smart UV, bake normal + AO from the SDF high + closed parts.
  5. RIG: root > body > wing.{L,R}.up + stinger (v1.1: the small lower wings and their bones are gone). Clips (PLACEHOLDER, movement not given by the artist):
     idle = hover bob + wing buzz; walk = in-place hover-drift (nose-down lean, faster buzz). Rest pose on the floor
     (spike tips z 0); both clips lift HOVER_FRAC x H. glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
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
import bombee_parts as FP     # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "bombee"
CHAR_ID = "bombee"                    # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step of the body ball (the bake high)
SDF_BAND = 0.08                       # one primitive, no smooth union (k 0): BAND >= max k + 2 steps
BODY_TRIS = 8000                      # "body mesh detail": the ball's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- the BALL: SIDE view x 831-1170 at y 540-560 (half-depth 1.70), bottom y 693 (z 0.21), top over the eye column
#   x 960 at y 380 (z 3.34 -> top half-height 1.68 at Y -0.40); BACK view x 456-768 (half-width 1.56)
BODY_C = (0.0, 0.0, 1.69)             # ball centre (side view widest rows y 540-560)
BODY_R = (1.56, 1.70)                 # "body width / depth" (half X from the back view, half Y from the side view)
BODY_RZ = (1.68, 1.48)                # "body height": top / bottom half-heights (the ball sits fuller on top)
# ---- the FACE disc: side view face/black boundary (Y, z): (-1.05, 2.94) row 420, (-0.36, 1.69) row 545,
#   (-0.32, 1.14) row 600 -> face where -Y > FACE_Y0 + FACE_BEND * softplus(z - FACE_Z0)
FACE_Y0, FACE_BEND, FACE_Z0, FACE_SOFT = 0.25, 0.573, 1.55, 0.2
# ---- BANDS: planes across the body axis. The axis runs through (0, 0, ZC + BAND_LIFT), tilted BAND_TILT deg rear-up so
#   its rear pole = the stinger (side view: stinger base centre y 485 -> z 2.29; tip rises ~11 deg, base x 1160 -> 1236).
BAND_TILT, BAND_LIFT = 10.0, 0.31
# band edges as Y at the ball's equator, front -> back: black | yellow | black | yellow | black disc round the stinger.
#   SIDE view row 545: black/yellow 0.43, yellow/black 1.13, black/yellow 1.57; BACK view rings round the stinger at
#   radii 0.68 / 1.11 / 1.43 (-> Y 1.49 / 1.17 / 0.66). The two views disagree by about one band; the edges split them.
BAND_Y = (0.43, 0.95, 1.30, 1.50)
# ---- stinger (cone, cream-tan) on the band axis' rear pole: side view base half-height ~0.37 (y 450-525), tip x 1236
#   (0.76 out); back view base x 579-641 (half-width 0.31)
STINGER_R, STINGER_LEN, STINGER_SINK = 0.33, 0.80, 0.25
STINGER_SEG = 32
# ---- foot spikes (four small cones on the underside; their tips are the floor). SIDE view: front tip x 862 (Y -1.38),
#   rear tip x 1080 (Y +0.80); BACK view tips x 455 / 769 (|X| 1.57) -- split as four equal spikes at the diagonals.
SPIKE_AZ = (60.0, 120.0)              # "spike placement": deg from the front toward each side (front pair, rear pair)
                                      #   (v0 55 / 125 at reach 1.25 hid them under the ball in the back view)
SPIKE_REACH = 1.40                    # tip distance from the body axis (horizontal): back |X| 1.21, side |Y| 0.70
SPIKE_TILT = 50.0                     # spike axis: deg from straight down toward outward
SPIKE_R, SPIKE_SINK = 0.19, 0.15      # "spike size": base radius (side rear spike base ~45 px), buried length
SPIKE_SEG = 20
SPIKE_TOP = True                      # "top spikes" (artist 2026-10-09, markup bombee-v11-topspikes-annotation.png):
                                      #   4 more spikes = the foot spikes reflected across the ball's horizontal centre
                                      #   plane (tip at z 2 x BODY_C z, same azimuth / reach / tilt mirrored up); each
                                      #   base is re-seated on the (fuller) top surface by the same walk-back
# ---- face decals: positions cast horizontally onto the shell from the body axis at (azimuth from the front, z).
#   Azimuths from the 3/4 FRONT view, which fits ONE camera yaw (-36 deg) to all five features: smile x 107, eyes x 62 /
#   175, blushes x 50 / 199 (ball x 36-380, centre 208, half 172) -> eyes +-23 deg, blush +-32 deg. (The SIDE view's eye
#   at Y -1.23 would put them at +-42 deg: v0 tried 40 and the front read wall-eyed.) Heights split the two views:
#   eye z 1.80 side / 1.70 front, smile 1.43 / 1.50, blush 1.49 / 1.32. Panel: dark rim, brown iris, dark pupil, white
#   highlight upper-front. 3/4 front eye (facing camera) 29 x 52 px
EYE_AZ, EYE_Z = 23.0, 1.75            # "eye placement" (mirrored)
EYE_R, EYE_SQUASH = 0.16, 1.62        # "eye size": half-width, height / width (oval, 0.26 half-height)
IRIS_K, PUPIL_K = 0.74, 0.42          # iris / pupil as a fraction of the eye
HI_R, HI_OFF = 0.052, (0.12, 0.05)    # "eye shine": highlight radius, offset (up, toward the face midline)
BLUSH_AZ, BLUSH_Z = 32.0, 1.41        # "blush placement" (side view blob x 890-919 y 555-576; 3/4 front x 183-215 y 565-586)
BLUSH_R, BLUSH_SQUASH = 0.11, (1.0, 1.5)   # "blush size": half-height 0.11, half-width 0.165 (front view 33 x 22 px)
SMILE_Z, SMILE_W, SMILE_SAG = 1.47, 0.13, 0.05    # "smile": centre height, half-span, how far the middle dips
SMILE_LINE = 0.016                    # smile line half-width (the drawn line is ~3 px)
DECAL_UP, DECAL_DN = 0.006, 0.03      # decal top offset / sink
EYE_DOME = 0.02                       # the eye bulges this much (shiny); the stacked iris/pupil/highlight ride on it
# ---- wings: TWO leaf / petal wings (pointed root, rounded tip). v1.1 artist verdict (review-log 2026-10-09 "Bombee v1
#   verdict": "get rid of these extra wings on bombee and the main wings staying should [not] clip in as much and should
#   be more out like the red"; markups design/reference/bombee-v1-lowerwing-annotation.png = the two small lower wings
#   REMOVED, bombee-v1-wingroot-annotation.png = the petal drawn wholly ABOVE / outside the body line, taller and
#   reaching further back). v1 sank the root at (0.26, -0.22, 2.40) and the blade left the ball 1.00 u up its axis
#   (33.1 % of the membrane buried); v1.1 seats the root ON the top surface with a shallow embed and raises + splays the
#   petal so it stands clear of the ball.
UPWING_ROOT_XY = (0.48, 0.05)         # "wing root": where each petal sits on the top of the ball (X, Y; L side, mirrored)
UPWING_EMBED = 0.06                   # "wing seat": the pointed root sinks this far into the ball (just attached, no gap)
UPWING_SPLAY = 22.0                   # "wing splay": deg the petal leans OUTWARD from vertical (front / back views)
UPWING_LEAN = 30.0                    # "wing lean": deg the petal leans BACK from vertical (side view; the red outline
                                      #   reaches further back than v1)
UPWING_LEN = 1.70                     # "wing length" (root -> rounded tip)
UPWING_HW = 0.90                      # "wing width" (half)
UPWING_FACE = (1.0, -1.0, 0.0)        # the leaf's width direction (diagonal: reads broad from the back AND the side)
UPWING_CUP = 0.10                     # edges curl by this x the half-width (a soft petal, not a flat card)
UPWING_POINT = 1.0                    # root taper exponent (> 0.8 = slimmer near the seat: at 0.8 the membrane grazed the
                                      #   ball 8 mm just outside the seat; 1.0 clears it by 0.05). Root X 0.48 keeps the
                                      #   petals 0.06 off the midline at the inward-most walk stroke (13 deg; 0.40 crossed)
WING_NS, WING_NT = 22, 10             # membrane grid (stations, columns)
WING_RIM_R, WING_RIM_N = 0.014, 6     # "wing outline" tube radius / sides
WING_RIM_ST = 40                      # outline stations
VEIN_GAP = 0.34                       # "vein spacing"
VEIN_ANG = 36.0                       # vein lattice angle to the leaf axis (diamonds)
VEIN_HW, VEIN_HT = 0.012, 0.006       # vein ribbon half-width / half-thickness (straddles the membrane)
VEIN_ST = 8                           # stations per vein (conform to the cupped membrane)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- clips (PLACEHOLDER: the artist has not given the movement; review-log "a flyer: placeholder idle = hover bob +
#   wing buzz, walk = hover-drift"). 24 fps; integer harmonics per loop -> frame N+1 == frame 1.
HOVER_FRAC = 0.10                     # "hover height": both clips lift the body this fraction of the rest height
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BOB = (0.014, 0.004)             # "hover bob": z amplitude x H at 2 cyc + a 3 cyc overtone
IDLE_PITCH, IDLE_ROLL, IDLE_YAW = (2.0, 0.6), (1.5, 0.5), 1.5   # wander deg (1 cyc + overtone)
BUZZ_IDLE, BUZZ_WALK = 24, 12         # "wing buzz": cycles per loop (both 6 Hz: 4 frames per stroke at 24 fps)
UP_OPEN, UP_BUZZ, UP_SWIRL = 8.0, 16.0, 6.0     # wings: mean open deg (outward), stroke deg (about Y), fore-aft swirl
                                                #   deg; inward-most = UP_BUZZ x WALK_BUZZ_K - UP_OPEN (gated: the
                                                #   two petals never cross the midline, never enter the ball)
STING_IDLE = 3.0                      # stinger wag deg
WALK_N = 48                           # "drift loop": 2 s
WALK_BOB = 0.008                      # z amplitude x H at 2 cyc
WALK_PITCH, WALK_PITCH_OSC, WALK_ROLL = 10.0, 1.0, 1.5   # "lean into the drift": nose-down deg, its wobble, roll
WALK_BUZZ_K = 1.3                     # strokes grow by this in the drift
WALK_SWEEP = 2.0                      # wings sweep back deg (v1.1: 8 + the 7.8 deg swirl tipped the back-leaning petal 5.6 mm
                                      #   into the ball; the 30 deg UPWING_LEAN already reads as swept)
STING_TRAIL = 4.0                     # stinger trails down-back deg

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
          "design/reference/bombee-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (back + side); floor z = 0 at the spike tips",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
SIDES = (("L", 1.0), ("R", -1.0))


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


def mirror_part(V, F):
    """the R twin of an L part: x -> -x, windings reversed (outward stays outward)"""
    V2 = np.asarray(V, float).copy(); V2[:, 0] *= -1.0
    return V2, [list(f[::-1]) for f in F]


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body ball SDF
t_sdf = time.time()
BC = np.array(BODY_C, float)


def sd_body(P):
    """IQ's bound-correct ellipsoid with a fuller top half (radius z = BODY_RZ[0] above the centre, [1] below)"""
    Q = np.atleast_2d(P) - BC
    r = np.stack([np.full(len(Q), BODY_R[0]), np.full(len(Q), BODY_R[1]),
                  np.where(Q[:, 2] > 0.0, BODY_RZ[0], BODY_RZ[1])], 1)
    k0 = np.linalg.norm(Q / r, axis=1)
    k1 = np.linalg.norm(Q / (r * r), axis=1)
    return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -min(BODY_R + BODY_RZ))


def ray_surface(o, d, t_max=4.0):
    """first exit of the ray o + t d (o inside the ball) through the ball surface (bisection on the SDF)"""
    o = np.asarray(o, float); d = unit(d)
    lo_, hi_ = 0.0, t_max
    for _ in range(60):
        mid = 0.5 * (lo_ + hi_)
        if sd_body(o + d * mid)[0] < 0.0:
            lo_ = mid
        else:
            hi_ = mid
    return o + d * (0.5 * (lo_ + hi_)), 0.5 * (lo_ + hi_)


_lo = BC - np.array([BODY_R[0], BODY_R[1], BODY_RZ[1]]) - 0.04
_hi = BC + np.array([BODY_R[0], BODY_R[1], BODY_RZ[0]]) + 0.04
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
g.apply(sd_body, _lo, _hi, 0.0)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
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

# =========================================================================== 3. region fields + iso cuts on the shell
TAU_B = math.radians(BAND_TILT)
BAND_A = BC + np.array([0.0, 0.0, BAND_LIFT])
BAND_D = np.array([0.0, math.cos(TAU_B), math.sin(TAU_B)])
U_LEVELS = [y * math.cos(TAU_B) - BAND_LIFT * math.sin(TAU_B) for y in BAND_Y]   # equator Y -> axial coordinate


def face_field(P):
    d = P[:, 2] - FACE_Z0
    sp = 0.5 * (d + np.sqrt(d * d + FACE_SOFT * FACE_SOFT))
    return -(P[:, 1] - BC[1]) - FACE_Y0 - FACE_BEND * sp


def axial(P):
    return (P - BAND_A) @ BAND_D


FIELDS = {"face": face_field(LV), "u": axial(LV)}
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


TOL = 0.01
off_face = lambda a, b: a[LAY["face"]] < TOL and b[LAY["face"]] < TOL
CUTS = [("face", 0.0, None)] + [("u", lv_, off_face) for lv_ in U_LEVELS]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
band_idx = np.searchsorted(np.array(U_LEVELS), FVAL["u"])         # 0 .. 4 front -> back
shell_reg = np.where(band_idx % 2 == 0, "black", "yellow").astype(object)
shell_reg[FVAL["face"] > 0] = "face"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF),
                      "band_axial_levels": [round(x, 4) for x in U_LEVELS]}

# =========================================================================== 4. parts
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def caster(bvh):
    def cast(origin, d, dist=6.0):
        loc, nrm, idx, dd = bvh.ray_cast(Vector(origin), Vector(d), dist)
        if loc is None:
            loc, nrm, idx, dd = bvh.find_nearest(Vector(origin))
        return np.array(loc), np.array(nrm)

    def project(pts, n):
        return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])
    return cast, project


cast_c, project_c = caster(bvh_c)


def face_anchor(az_deg, z):
    """horizontal ray from outside toward the body axis at azimuth az (0 = front, + toward +X) -> surface point + a
    smoothed normal (mean of a small ring of hits)."""
    az = math.radians(az_deg)
    dvec = np.array([math.sin(az), -math.cos(az), 0.0])
    S, _ = cast_c(np.array([0.0, 0.0, z]) + dvec * 3.0, -dvec)
    ns = []
    for da, dz in ((0, 0), (4, 0), (-4, 0), (0, 0.05), (0, -0.05)):
        a2 = math.radians(az_deg + da)
        d2 = np.array([math.sin(a2), -math.cos(a2), 0.0])
        ns.append(cast_c(np.array([0.0, 0.0, z + dz]) + d2 * 3.0, -d2)[1])
    return S, unit(np.mean(ns, 0))


PARTS = []
FACE = {}
# ---- eyes: dark rim -> iris -> pupil -> highlight, each stacked decal projected onto the one below it
for s, sg in SIDES:
    S, n = face_anchor(sg * EYE_AZ, EYE_Z)
    V, F, R = FP.dome_decal(S, n, EYE_R, project_c, DECAL_UP, DECAL_DN, EYE_DOME, 4, 28, "eye", squash=(EYE_SQUASH, 1.0))
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="body"))
    _, proj_e = caster(BVHTree.FromPolygons(V.tolist(), F))
    V2, F2, R2 = FP.dome_decal(S, n, EYE_R * IRIS_K, proj_e, 0.004, 0.012, 0.004, 3, 24, "eye_iris",
                               squash=(EYE_SQUASH, 1.0), offset=(-0.02, 0.0))
    PARTS.append(FP.Part("iris." + s, V2, F2, R2, bone="body"))
    _, proj_i = caster(BVHTree.FromPolygons(V2.tolist(), F2))
    V3, F3, R3 = FP.dome_decal(S, n, EYE_R * PUPIL_K, proj_i, 0.004, 0.010, 0.003, 2, 20, "eye",
                               squash=(EYE_SQUASH, 1.0), offset=(-0.03, 0.0))
    PARTS.append(FP.Part("pupil." + s, V3, F3, R3, bone="body"))
    _, proj_p = caster(BVHTree.FromPolygons(np.vstack([V2, V3]).tolist(), F2 + [[i + len(V2) for i in f] for f in F3]))
    # the highlight sits up and toward -X on BOTH eyes (+e2 = n x up points to -X on either side): one light, like the sheet
    V4, F4, R4 = FP.dome_decal(S, n, HI_R, proj_p, 0.004, 0.010, 0.006, 2, 16, "eye_hi",
                               offset=(HI_OFF[0], HI_OFF[1]))
    PARTS.append(FP.Part("eye_hi." + s, V4, F4, R4, bone="body"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": n.round(4).tolist()}
    S, n = face_anchor(sg * BLUSH_AZ, BLUSH_Z)
    V, F, R = FP.dome_decal(S, n, BLUSH_R, project_c, DECAL_UP, DECAL_DN, 0.004, 3, 24, "blush", squash=BLUSH_SQUASH)
    PARTS.append(FP.Part("blush." + s, V, F, R, bone="body"))
    FACE["blush." + s] = {"surface": S.round(4).tolist(), "normal": n.round(4).tolist()}
# ---- smile: a closed ribbon along a U arc on the face midline
S, n = face_anchor(0.0, SMILE_Z)
e1 = unit(np.array([0.0, 0.0, 1.0]) - n * n[2]); e2 = np.cross(n, e1)
_ts = np.linspace(-1.0, 1.0, 13)
_pts = np.array([S + e2 * (SMILE_W * t_) + e1 * (SMILE_SAG * t_ * t_) for t_ in _ts])
C_sm = project_c(_pts, n)
N_sm = np.repeat(n[None], len(C_sm), 0)
T_sm = np.gradient(C_sm, axis=0)
Q_sm = np.array([unit(np.cross(n, t_)) for t_ in T_sm])
V, F, R = FP.ribbon(C_sm, N_sm, Q_sm, SMILE_LINE * (0.55 + 0.45 * np.sin(np.pi * (_ts + 1) / 2)), DECAL_UP, DECAL_DN, "mouth")
PARTS.append(FP.Part("mouth", V, F, R, bone="body"))
FACE["mouth"] = {"surface": S.round(4).tolist(), "normal": n.round(4).tolist()}
# ---- stinger on the band axis' rear pole
ST_BASE, _ = ray_surface(BAND_A, BAND_D)
prof = [(0.0, STINGER_LEN), (0.035, STINGER_LEN - 0.07), (STINGER_R * 0.55, STINGER_LEN * 0.50),
        (STINGER_R * 0.88, STINGER_LEN * 0.20), (STINGER_R, 0.08), (STINGER_R, 0.0), (STINGER_R * 0.9, -0.10),
        (0.0, -STINGER_SINK)]
V, F, R = FP.lathe(prof, ["stinger"] * (len(prof) - 1), STINGER_SEG, ST_BASE, BAND_D)
PARTS.append(FP.Part("stinger", V, F, R, bone="stinger"))
ST_TIP = ST_BASE + BAND_D * STINGER_LEN
# ---- foot spikes: tip on the floor at SPIKE_REACH, axis SPIKE_TILT from straight down, base where it meets the ball
SPIKES = {}
for k, az0 in enumerate(SPIKE_AZ):
  for vz in ((-1.0, 1.0) if SPIKE_TOP else (-1.0,)):          # -1 = the foot spikes, +1 = their mirror on the top
    for s, sg in SIDES:
        az = math.radians(sg * az0)
        h = np.array([math.sin(az), -math.cos(az), 0.0])
        tilt = math.radians(SPIKE_TILT)
        ax = unit(h * math.sin(tilt) + np.array([0.0, 0.0, vz]) * math.cos(tilt))
        tip = h * SPIKE_REACH + np.array([0.0, 0.0, 0.0 if vz < 0 else 2.0 * BODY_C[2]])
        lo_, hi_ = 0.0, 3.0                                   # walk back from the tip until inside the ball
        for _ in range(60):
            mid = 0.5 * (lo_ + hi_)
            if sd_body(tip - ax * mid)[0] > 0.0:
                lo_ = mid
            else:
                hi_ = mid
        Lsp = 0.5 * (lo_ + hi_)
        base = tip - ax * Lsp
        prof = [(0.0, Lsp), (0.025, Lsp - 0.05), (SPIKE_R * 0.6, Lsp * 0.45), (SPIKE_R, 0.0), (SPIKE_R * 0.85, -0.08),
                (0.0, -SPIKE_SINK)]
        V, F, R = FP.lathe(prof, ["spike"] * (len(prof) - 1), SPIKE_SEG, base, ax)
        nm = "spike.%s.%s%s" % (s, "front" if k == 0 else "rear", "" if vz < 0 else ".top")
        PARTS.append(FP.Part(nm, V, F, R, bone="body"))
        SPIKES[nm] = {"tip": tip.round(4).tolist(), "base": base.round(4).tolist(), "visible_length": round(Lsp, 4)}
# ---- wings
WINGS, WING_INFO = {}, {}


def wing_parts(name, leaf, s0, ns, nt, rim_st, gap, vein_st, seed):
    Vm, Fm, Rm, _ = leaf.membrane(s0, ns, nt, "wing")
    Po, No, _ = leaf.outline(s0, rim_st)
    Vr, Fr, Rr = FP.tube_loop(Po, No, WING_RIM_R, WING_RIM_N, "wing_rim")
    Vv, Fv, Rv, _, nv = leaf.veins(s0, gap, VEIN_ANG, FP.hash01(3, seed), WING_RIM_R * 0.6, vein_st, VEIN_HW, VEIN_HT,
                                   "wing_vein")
    return [(name + ".membrane", Vm, Fm, Rm, True), (name + ".veins", Vv, Fv, Rv, False), (name + ".rim", Vr, Fr, Rr, False)], nv


_wr, _ = ray_surface(np.array([UPWING_ROOT_XY[0], UPWING_ROOT_XY[1], BC[2]]), (0.0, 0.0, 1.0))   # seat on the top
_wa = unit((math.tan(math.radians(UPWING_SPLAY)), math.tan(math.radians(UPWING_LEAN)), 1.0))
up_leaf = FP.Leaf(_wr - _wa * UPWING_EMBED, _wa, UPWING_FACE, UPWING_LEN, UPWING_HW, UPWING_CUP, point_pow=UPWING_POINT)


def buried(leaf):
    """membrane area inside the ball (fine-grid membrane, face centroids) + the axis length inside it"""
    V_, F_, _, _ = leaf.membrane(0.0, 300, 80, "w")
    A_ = np.array([0.5 * np.linalg.norm(np.cross(V_[f[1]] - V_[f[0]], V_[f[2]] - V_[f[0]])) +
                   (0.5 * np.linalg.norm(np.cross(V_[f[2]] - V_[f[0]], V_[f[3]] - V_[f[0]])) if len(f) == 4 else 0.0)
                   for f in F_])
    d_ = sd_body(np.array([V_[f].mean(0) for f in F_]))
    ts_ = np.linspace(0.0, leaf.L, 4001)
    da_ = sd_body(leaf.root[None] + np.outer(ts_, leaf.A))
    return {"membrane_area": round(float(A_.sum()), 4), "buried_area": round(float(A_[d_ < 0].sum()), 4),
            "buried_pct": round(100 * float(A_[d_ < 0].sum() / A_.sum()), 2),
            "buried_axis_length": round(float(ts_[np.nonzero(da_ >= 0)[0][0]]), 4),
            "max_depth_inside": round(float(-min(0.0, d_.min())), 4)}


pieces, nveins = wing_parts("wing.L.up", up_leaf, 0.0, WING_NS, WING_NT, WING_RIM_ST, VEIN_GAP, VEIN_ST, 5.0)
for s, sg in SIDES:
    bone = "wing.%s.up" % s
    for nm, V, F, R, is_mem in pieces:
        if s == "R":
            V, F = mirror_part(V, F)
            nm = nm.replace("wing.L.", "wing.R.")
        PARTS.append(FP.Part(nm, V, F, R, bone=bone, wing=is_mem))
    WINGS[bone] = {"pivot": _wr * np.array([sg, 1.0, 1.0]),
                   "tip": (up_leaf.root + up_leaf.A * up_leaf.L) * np.array([sg, 1.0, 1.0])}
_wv = np.vstack([p_[1] for p_ in pieces])
WING_INFO["up"] = {"length": round(up_leaf.L, 4), "half_width": up_leaf.HW, "veins_per_wing": nveins,
                   "root_seat_L": np.round(_wr, 4).tolist(), "embed": UPWING_EMBED,
                   "axis_L": np.round(up_leaf.A, 4).tolist(), "splay_deg": UPWING_SPLAY, "lean_deg": UPWING_LEAN,
                   "tip_L": np.round(up_leaf.root + up_leaf.A * up_leaf.L, 4).tolist(),
                   "leaf_normal_L": np.round(up_leaf.N, 4).tolist(),
                   "buried": buried(up_leaf),
                   "buried_v1": {"membrane_area": 2.4759, "buried_area": 0.8192, "buried_pct": 33.1,
                                 "buried_axis_length": 1.0001, "rule": "v1 leaf (root (0.26, -0.22, 2.40) -> tip (0.74, "
                                 "0.70, 4.25), half-width 0.82) measured by the same fine-grid probe"},
                   "all_wing_verts_min_sd_outside_embed": round(float(sd_body(_wv[np.linalg.norm(_wv - _wr, axis=1) >
                                                                                  3 * UPWING_EMBED]).min()), 4),
                   "lowest_point_z": round(float(_wv[:, 2].min()), 4), "lower_wings": "REMOVED (v1.1 artist verdict)"}

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, MATI, RANGE = [], [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    MATI += [1 if (p is not None and p.wing) else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
MATI = np.array(MATI, dtype=np.int32)
HV_s = HV - SHIFT
REG = ["face", "black", "yellow", "stinger", "spike", "eye", "eye_iris", "eye_hi", "mouth", "blush",
       "wing", "wing_vein", "wing_rim"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.97 + 0.06 * j


def make_mat(name, kind):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    if kind == "wing":
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"          # Eevee: order-independent see-through (glTF alphaMode BLEND)
        mat.use_backface_culling = False                 # single-surface membrane: double-sided
        mat.use_transparent_shadow = True
    else:
        mat.use_backface_culling = True                  # opaque closed solids: single-sided (glTF doubleSided false)
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
    return {n: float(a) for n, a in zip(names, a_reg) if a < 1.0}


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        paint_alpha(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
    return out


MAT_BODY = make_mat(UNIT + "_body", "body")
MAT_WING = make_mat(UNIT + "_wing", "wing")
pal_default = PAL.load(UNIT, "default")
_lit = sorted(n for n, v in pal_default["regions"].items() if "emission" in v)
report["glow"] = {"glowing_regions": _lit, "rule": "no glow: the sheet draws none (Glow attribute all zero)"}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_WING)
me.polygons.foreach_set("material_index", MATI)
shade = jitter(VALL, FALL)
_flat = np.isin(rid, [REG.index(r) for r in ("eye", "eye_iris", "eye_hi", "mouth", "blush", "wing", "wing_vein", "wing_rim")])
shade[_flat] = 1.0                                   # decals + wings stay clean (no value jitter)
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
report["alpha"] = {"translucent_regions": paint_alpha(me, pal_default), "material_alpha": pal_default["material"].get("alpha", 1.0)}
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the ball centre -> the face's front point on the midline
anchor = BC - SHIFT
landmark = np.array([0.0, BC[1] - BODY_R[1], BC[2]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "ball centre -> the face disc's front point on the midline (the eyes / smile face -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["spikes"] = SPIKES
report["stinger"] = {"base": ST_BASE.round(4).tolist(), "tip": ST_TIP.round(4).tolist(), "axis": BAND_D.round(4).tolist()}
report["wings"] = WING_INFO

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
_part_tris = {}
for p in PARTS:
    key = p.name.split(".")[0]
    if key == "wing":
        key = "wings_" + p.name.split(".")[-1]
    elif key in ("eye", "iris", "pupil", "eye_hi"):
        key = "eyes"
    elif key == "spike":
        key = "spikes"
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The smooth ball needs few triangles; the wings "
                            "carry the budget (membrane grid + closed vein lattice + rim tube per wing) so the vein "
                            "lines read as clean geometry, not texture." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every solid is closed (shell, stinger, spikes, decals, vein ribbons, rim tubes); open = "
                                "the two single-surface wing membranes (double-sided BLEND wing material)"
                                }
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
_sh = CV - SHIFT
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "sheet_height_px": {"side": 437, "back": 409, "front_3q": 428}},
                     "ball": {"width": round(float(np.ptp(_sh[:, 0])), 4), "depth": round(float(np.ptp(_sh[:, 1])), 4),
                              "height": round(float(np.ptp(_sh[:, 2])), 4), "bottom_z": round(float(_sh[:, 2].min()), 4),
                              "sheet": "back width 3.12, side depth 3.39, side height 3.13 (y 380-693), bottom 0.21"},
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
low["conquest_source"] = "from scratch: design/reference/bombee-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; wing "
                                 "material alphaMode BLEND double-sided (membranes only), body material opaque")
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_wl = np.vstack([VALL[RANGE[nm][0]:RANGE[nm][1]] for nm in ("wing.L.up.membrane", "wing.L.up.rim")])
_st = np.vstack([VALL[RANGE["stinger"][0]:RANGE["stinger"][1]]])
_axis_y = 0.21                                        # the sheet's side-view axis x 1021.5 px = Y +0.21 (ball centre 1000.5)
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-1.25, -1.85, 0.75]) - SHIFT).tolist(), (np.array([1.25, -0.55, 2.75]) - SHIFT).tolist()],
    "wing": [(_wl.min(0) - 0.1).tolist(), (_wl.max(0) + 0.1).tolist()],
    "stripes": [(np.array([0.3, -1.0, 0.4]) - SHIFT).tolist(), (np.array([1.6, 1.75, 3.2]) - SHIFT).tolist()],
    "stinger": [(_st.min(0) - 0.35).tolist(), (_st.max(0) + 0.35).tolist()],
    "views": {"face": [-25.0, 4.0], "wing": [125.0, 18.0], "stripes": [90.0, 5.0], "stinger": [130.0, 6.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(_axis_y - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "face", "spikes", "stinger", "wings")},
                                default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF ball + closed parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
for name, V, F, R, p in ISL[1:]:
    if p.wing:
        continue                                     # membranes never occlude the body (they are see-through)
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
for mat_ in (MAT_BODY, MAT_WING):
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
    for mat_ in (MAT_BODY, MAT_WING):
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
body_faces = MATI == 0
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF ball (marching tets) + every closed part; wing membranes excluded (see-through)",
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
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
# wire the textures into the BODY material only (the wing membranes keep flat colour + alpha)
nt = MAT_BODY.node_tree
tn, ta = BAKE_NODES[MAT_BODY.name]
for n_ in BAKE_NODES[MAT_WING.name]:
    MAT_WING.node_tree.nodes.remove(n_)
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
                     "provenance": "pixel-sampled from the sheet's chips + drawn surfaces (see the palette notes)"}
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


BONES = [("body", P_(BC), P_(BC + np.array([0.0, 0.0, 1.0])), "root")]
for s, _ in SIDES:
    for wn in ("up",):
        b = "wing.%s.%s" % (s, wn)
        pv, tp = WINGS[b]["pivot"], WINGS[b]["tip"]
        BONES.append((b, P_(pv), P_(pv + 0.6 * (tp - pv)), "body"))
BONES.append(("stinger", P_(ST_BASE), P_(ST_TIP), "body"))
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

# ---- weights: every piece is rigid on one bone (the ball, decals, spikes on body; each wing's membrane + veins + rim
#      on its wing bone; the stinger on its own bone)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Wt[a_:b_, J["body"]] = 1.0
for p in PARTS:
    a_, b_ = RANGE[p.name]
    Wt[a_:b_, J[p.bone]] = 1.0
infl = (Wt > 0).sum(1)
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    idx_ = np.nonzero(Wt[:, j] > 0)[0]
    if len(idx_):
        vg.add([int(i) for i in idx_], 1.0, "REPLACE")
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_verts": {n: int((Wt[:, j] > 0).sum()) for j, n in enumerate(DEFORM)},
                  "rule": "rigid pieces: shell + decals + spikes 1.0 body; wing.{L,R}.up membrane/veins/rim 1.0 "
                          "on their wing bone (pivot = the root seat on the top of the ball); stinger 1.0 stinger"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]
HOVER = HOVER_FRAC * HH


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
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
        z = HH * (IDLE_BOB[0] * math.sin(TAU * 2 * u) + IDLE_BOB[1] * math.sin(TAU * 3 * u + 0.9))
        pitch = IDLE_PITCH[0] * math.sin(TAU * u) + IDLE_PITCH[1] * math.sin(TAU * 3 * u + 0.5)
        roll = IDLE_ROLL[0] * math.sin(TAU * u + 1.3) + IDLE_ROLL[1] * math.sin(TAU * 2 * u + 2.1)
        yaw = IDLE_YAW * math.sin(TAU * u + 2.4)
        cyc, kb, sweep = BUZZ_IDLE, 1.0, 0.0
        sting = [("Z", STING_IDLE * math.sin(TAU * u + 0.4)), ("X", 0.6 * STING_IDLE * math.sin(TAU * 2 * u + 1.0))]
    else:
        z = HH * WALK_BOB * math.sin(TAU * 2 * u)
        pitch = WALK_PITCH + WALK_PITCH_OSC * math.sin(TAU * 2 * u - 1.0)
        roll = WALK_ROLL * math.sin(TAU * u + 0.6)
        yaw = 0.0
        cyc, kb, sweep = BUZZ_WALK, WALK_BUZZ_K, WALK_SWEEP
        sting = [("X", -STING_TRAIL + 1.5 * math.sin(TAU * 2 * u - 0.6)), ("Z", 1.5 * math.sin(TAU * u + 0.4))]
    P["body"] = ((0.0, 0.0, HOVER + z), [("X", pitch), ("Y", roll), ("Z", yaw)], None)
    ph = TAU * cyc * u
    for s, sg in SIDES:
        P["wing.%s.up" % s] = (None, [("X", -sweep + kb * UP_SWIRL * math.sin(ph + math.pi / 2)),
                                      ("Y", sg * (UP_OPEN + kb * UP_BUZZ * math.sin(ph)))], None)
    P["stinger"] = (None, sting, None)
    return P, {"lift": HOVER + z, "pitch": pitch, "roll": roll}


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


TIPV = {}
for b in ("wing.L.up", "wing.R.up"):
    a_, b_ = RANGE[b + ".rim"]
    Vr_ = VALL[a_:b_]
    TIPV[b] = a_ + int(np.argmax(np.linalg.norm(Vr_ - P_(WINGS[b]["pivot"]), axis=1)))
WV = {s: np.concatenate([np.arange(*RANGE["wing.%s.up.%s" % (s, k)]) for k in ("membrane", "veins", "rim")])
      for s, _ in SIDES}
_seat = {s: np.linalg.norm(VALL[WV[s]] - P_(WINGS["wing.%s.up" % s]["pivot"]), axis=1) > 3 * UPWING_EMBED for s, _ in SIDES}
BODY_REST = Matrix(arm_data.bones["body"].matrix_local)
samples, clip_rep = [], {}
for cn, N in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    first = last = None
    rows = []
    wing_pen, wing_mid = [], []
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
        Mb = np.array(BODY_REST @ pbs["body"].matrix.inverted())          # posed -> body rest frame
        for s, sg in SIDES:
            Cw = C[WV[s]] @ Mb[:3, :3].T + Mb[:3, 3] + SHIFT                # sheet coords, body frame
            wing_pen.append(float(sd_body(Cw[_seat[s]]).min()))             # < 0 = the petal enters the ball
            wing_mid.append(float((sg * Cw[:, 0]).min()))                   # < 0 = the petal crosses the midline
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "pitch": math.degrees(math.atan2(-by.y, by.z)), "roll": math.degrees(math.atan2(by.x, by.z)),
                     "root": float((rig.matrix_world @ pbs["root"].head).length),
                     **{"tip_" + b: list(C[i]) for b, i in TIPV.items()}})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - arm_data.bones["body"].head_local.z
    tips = {b: np.array([r["tip_" + b] for r in R_]) for b in TIPV}
    zl, zr = tips["wing.L.up"][:, 2], tips["wing.R.up"][:, 2]
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 4),
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "pitch_deg": [round(float(g_("pitch").min()), 3), round(float(g_("pitch").max()), 3)],
                    "roll_deg": [round(float(g_("roll").min()), 3), round(float(g_("roll").max()), 3)],
                    "wing_tip_p2p_xyz": {b: (v.max(0) - v.min(0)).round(4).tolist() for b, v in tips.items()},
                    "upper_tips_z_LR_corr": round(float(np.corrcoef(zl, zr)[0, 1]), 4),
                    "wing_min_sd_outside_seat": round(min(wing_pen), 4),
                    "wing_min_dist_to_midline": round(min(wing_mid), 4),
                    "wing_gate": bool(min(wing_pen) > 0.0 and min(wing_mid) > 0.0),
                    "buzz_hz": round((BUZZ_IDLE if cn == "idle" else BUZZ_WALK) / (N / K.FPS), 3)}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
    assert clip_rep[cn]["wing_gate"], (cn, clip_rep[cn]["wing_min_sd_outside_seat"], clip_rep[cn]["wing_min_dist_to_midline"])
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hover"] = {"hover_frac_H": HOVER_FRAC, "hover_height_units": round(HOVER, 4), "rest_height_units": HH,
                "rule": "rest pose on the floor (contract feet_origin, spike tips z 0); both clips key body.location z = "
                        "HOVER_FRAC x H + the bob; the contract root never moves"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "bombee v1.1: root > body > wing.{L,R}.up + stinger (every piece rigid on one bone; no lower wings)"
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given bombee's movement; review-log 2026-10-09 "
                               "'a flyer: placeholder idle = hover bob + wing buzz, walk = hover-drift'): idle = 4 s "
                               "hover (bob + pitch/roll wander, 6 Hz wing buzz, stinger wag); walk = 2 s in-place "
                               "hover-drift (10 deg nose-down lean, bigger 6 Hz buzz, wings swept back)")
low["conquest_locomotion"] = "hover (flyer): rest pose on the floor per contract (spike tips z 0); both clips lift HOVER_FRAC x H"
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
              "structure": "armature identity + skinned 'bombee' (body opaque + wing BLEND double-sided primitives); "
                           "natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default)},
                "rule": "default skin only (brief: no alternate palettes for v1)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("HOVER", json.dumps(rep["hover"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
