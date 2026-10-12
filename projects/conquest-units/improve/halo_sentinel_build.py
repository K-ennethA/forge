"""Halo Sentinel -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/halo_sentinel_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-11 "NEW UNITS x6" (section HALO SENTINEL) + the sheet
design/reference/halo_sentinel-character-sheet.png (intake: design/reference/halo_sentinel/intake.json, --cuts 545,1030).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (docs/lane-conventions.md draft speed: one pass, render,
report). Siblings read for idioms: fogeist (BLENDED alpha), eye_totem (smooth iso-cut eye faces), storm_vessel (floating
parts + glow tiers), lava_komodo (mirror-halved + welded symmetric shell).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. FRONT + BACK + SIDE are drawn as orthos (the side faces image-LEFT
and reads slightly turned: its two blue panels do not overlap):
  FRONT (the unit's left on image right):  X = (x_px - 288) / 100 ; z = (769 - y_px) / 100
  BACK  (mirrored into world X):           X = -(x_px - 752) / 100 ; z = (781 - y_px) / 100
  SIDE  (front = image left = -Y):         Y = (x_px - 1150) / 100 ; z = (778 - y_px) / 100  (drawn ~3 % taller: 6.27 vs 6.08)
The FRONT fixes the scale + heights; depths come off the SIDE. Floor z = 0 at the SKIRT HEM (it floats: the sheet's
shadow sits below). Front = -Y, X +ve = the unit's LEFT (L). HOUSE RULE: the rest pose is MIRROR-SYMMETRIC about X = 0.
Measurements: per-row runs of colour classes (service venv PIL): magenta R>90 G<75 R-B>30; blue B>R+25 B>90; orange
R>190 50<G<170 B<90; cream R,G,B > 200/160/170.

Pipeline:
  1. BODY = ONE SDF (smooth unions): round HEAD, broad rounded TORSO (+ two shoulder masses), two WING-LIKE ARM paddles
     (ellipsoids along the arm axis: out, down and a little back), the tapering BELLY LOBE. Marching tets = the HIGH (bake
     source); collapse decimation = the LOW; the LOW is mirror-HALVED (X >= 0), its two eye zones densified + projected
     onto the exact SDF (smooth eye faces), iso-cut, then mirror-WELDED (exact symmetry).
  2. REGIONS (iso-cuts, front-projected as the FRONT view draws them): the head EYE (dark rim / cream almond / orange
     pupil); the BELLY EMBLEM (crimson ring / pale-pink ring / cream / orange pupil).
  3. PARTS (closed): the HALO (torus, pitched as the front view draws it), two HORN arrows (stem tube + arrowhead cone)
     curving down from its sides toward the head; the JELLY SKIRT (v2, artist verdict 2026-10-11: one cone SHELL joined
     across the back, wrapping round toward the front, open at the front centre; light stripes + the deep inner wall
     iso-cut; mirror-halved + welded).
  4. palette (Col / Glow + Col alpha): body opaque; the skirt on the BLENDED alpha material (fogeist: no grain, no overlap,
     solid shadow); glow on the halo + horns + pupils (faint), tiers under the 2.0 cap. Smart UV, bake normal + AO from the
     SDF high + the RIBBED skirt high (the watery ribs live in the normal map only) + the parts.
  5. RIG: root > body > head > halo; body > wing_L.001 > .002, wing_R...; body > skirt_back.001 > .002, skirt_L..., skirt_R... Clips
     PLACEHOLDER (movement + hover height are open questions): idle = hover bob + halo sway/bob + wing sway; walk =
     in-place hover-drift lean. glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                 # noqa: E402  (read-only use)
import palettes as PAL             # noqa: E402  (read-only use)
import magmoo_sdf as SD            # noqa: E402  (read-only use: the SDF kit)
import halo_sentinel_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "halo_sentinel"
CHAR_ID = "halo_sentinel"             # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "body surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.40                       # BAND >= max smooth-union k (LOBE_K 0.30) + 2 steps (contract hazard note)
BODY_TRIS = 9000                      # "body mesh detail": the shell's decimation target (full shell, before halving)
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- HEAD (FRONT: magenta half-width 0.30 at y 200 (z 5.69), 0.61 at y 260 (z 5.09-5.19) -> circle centre z 5.14,
#   r 0.61, top 5.75 (outline y ~196); SIDE: head front Y -0.79 at y 250 (z 5.28 side), back ~ +0.40 -> centre Y -0.20)
HEAD_C, HEAD_R = (0.0, -0.20, 5.14), (0.61, 0.60, 0.61)
HEAD_K = 0.08                         # "chin crease": small blend (the sheet draws the head as a distinct ball)
# ---- TORSO (FRONT between the arm creases: half-width 1.06 at z 4.09-3.89, 1.00 at 3.69, 0.91 at 3.59, 0.77 at 3.49,
#   0.58 at 3.39; SIDE: chest front Y -0.96 at y 380-390 (z ~3.9)) + SHOULDERS (FRONT outline above the arms: 0.90 at
#   z 4.99, 1.03 at 4.89, 1.14 at 4.79, 1.26 at 4.69, 1.33 at 4.59; SIDE: shoulder top-back Y ~0.55 at z ~5.0)
TORSO_C, TORSO_R = (0.0, -0.10, 4.05), (1.08, 0.86, 0.95)
SHOULDER_C, SHOULDER_R = (0.75, 0.05, 4.45), 0.55            # (L; mirrored)
SHOULDER_K = 0.20
# ---- WING-LIKE ARMS (L; mirrored). FRONT upper edge (2.12, 4.09) (2.29, 3.89) (2.43, 3.69) (2.55, 3.49) (2.64, 3.29)
#   (2.70, 3.09) (2.73, 2.89); lower edge (1.33, 3.59) (1.48, 3.39) (1.68, 3.19) (1.92, 2.99) (2.22, 2.79) (2.44, 2.69):
#   perpendicular width ~0.85-0.9 mid-arm, rounded tip at (2.6-2.7, 2.7-2.8). SIDE: the arm hangs BACK to Y 1.16 at
#   z 3.0-3.2 (y 460-480), its tip ~Y 1.1 at z 2.8. An ellipsoid along root -> tip (its tip curvature b^2 / a ~0.17 =
#   the drawn rounded end; widest mid-arm as drawn).
ARM_ROOT, ARM_TIP = (1.15, 0.10, 4.50), (2.68, 0.80, 2.70)
ARM_R = (0.48, 0.46)                  # "arm thickness": half-width across the arm (front plane) / in depth
ARM_K = 0.18                          # "armpit blend" (the sheet keeps a crease at the shoulder)
# ---- BELLY LOBE (FRONT half-width 0.33 at z 3.1, 0.42 at 2.69, 0.50 at 2.29, 0.52 at 2.09, 0.485 at 1.69, 0.445 at
#   1.49, 0.38 at 1.29, 0.22 at 0.99, tip y 685 (z 0.84); SIDE: centre Y ~-0.50 (front -0.95 with the emblem at z 2.3,
#   back -0.05), half-depth 0.45 at z 2.3, tip Y -0.6): an ellipsoid (max width at z ~2.0, rounded tip at z 0.85)
LOBE_C, LOBE_R = (0.0, -0.50, 1.95), (0.52, 0.42, 1.10)
LOBE_K = 0.30                         # "belly neck" blend into the torso (front: 0.33-0.36 wide neck at z 2.9-3.1)
LOBE_NECK = ((0.0, -0.45, 2.60), 0.40, (0.0, -0.40, 3.50), 0.32)   # "belly neck": round cone lobe -> torso (FRONT
                                      #   half-width 0.33-0.36 at z 2.9-3.1; SIDE Y -0.72..-0.16 at z 3.2)
# ---- HEAD EYE (FRONT column x 288: rim y 217-284 (z 5.52-4.85), cream y 220-280, orange y 238-265 (z 5.31-5.04);
#   row y 251: rim x 276 / 300 (+-0.12), cream +-0.10, orange x 284-292 (+-0.045))
EYE_C = (0.0, 5.185)                  # "eye position" (x, z)
EYE_RIM_AB = (0.12, 0.335)            # "eye outline": the dark rim's outer half-width / half-height
EYE_WHITE_AB = (0.10, 0.31)           # "eye size": the cream almond
EYE_ALMOND = 0.70                     # "almond pointiness": 0.5 = ellipse, 1 = lens
PUPIL_C, PUPIL_AB = (0.0, 5.17), (0.05, 0.14)                # "pupil": the orange oval
# ---- BELLY EMBLEM (FRONT column x 288: crimson y 502-506 + 630-642, pale pink y 506-526 + 610-630, cream y 530-554 +
#   586-606, orange y 558-582; row y 568: crimson +-0.35, pink +-0.235, cream +-0.115, orange -0.05..0.065)
BELLY_C = (0.0, 1.98)
BELLY_RING_AB = (0.35, 0.72)          # crimson outer ring (outer edge)
BELLY_PINK_AB = (0.235, 0.64)         # pale-pink ring (outer edge)
BELLY_WHITE_AB = (0.115, 0.40)        # cream oval
BELLY_PUPIL_C, BELLY_PUPIL_AB = (0.0, 1.99), (0.058, 0.14)
EYE_DENSE = {"head": (0.04, 2), "belly": (0.04, 1), "belly_pupil": (0.04, 2)}   # densify zone pad (u) + rounds
# ---- HALO (FRONT: orange x -0.95..1.02 (R + r = 0.98), ends at z ~5.69 / 5.79 (centre z ~5.79), top y 162 (z 6.07),
#   ellipse half-height ~0.25 -> pitch asin(0.25 / 0.98) ~ 14.5 deg; the LOWER arc is hidden behind the head between the
#   horns = the BACK arc: the ring is pitched FRONT-UP (see deviations: the side view draws it front-down). SIDE: centre
#   Y ~-0.18. Tube: the band reads 6-8 px thick at the top (halo3x crop) -> r 0.045)
HALO_C = (0.0, -0.18, 5.79)           # "halo height / position"
HALO_R0, HALO_TUBE = 0.93, 0.045      # "halo size" / "halo thickness"
HALO_PITCH = 14.5                     # "halo tilt" deg, front edge up
HALO_SEG = (64, 8)
# ---- HORNS (L; mirrored): from the ring's side point, a stem curving down + in, then an arrowhead cone pointing at the
#   head. FRONT: arrowhead tips at (-0.38, 5.36) / (0.37, 5.37) (halo3x crop), base ~0.17 wide; SIDE: the arrowhead at
#   Y -0.25..0 in front of the head's side. The tips are held 6 % outside the head ellipsoid (they never touch it).
HORN_CTRL = (0.95, -0.24, 5.52)       # stem bend control point
HORN_BASE = (0.78, -0.30, 5.45)       # arrowhead base centre
HORN_TIP = (0.60, -0.36, 5.33)        # arrowhead tip
HORN_STEM_R = (0.034, 0.028)          # stem radius ring -> arrowhead
HORN_HEAD_R = 0.085                   # "arrowhead width" (half)
# ---- JELLY SKIRT (v2, artist 2026-10-11: "have the skirt connect in the back and curve around the front a little
#   more"): ONE translucent cone SHELL, joined across the back, wrapping round the sides toward the front, OPEN at the
#   front centre (the belly lobe + emblem show; the front still reads as two panels flanking the belly).
#   Outer surface rows (z, half-width rx, centre y cy, half-depth ry): rx = the BACK view's cone outline (+-0.68 at
#   z 3.51, 1.02 at 2.91, 1.40 at 2.11, 1.77 at 1.31, 1.88 at 0.91; = the FRONT pillar outer edges 0.76 / 0.99 / 1.41 /
#   1.80 / 2.03); the back edge cy + ry = the SIDE view's (0.20 at z 3.3, 0.72 at 2.48, 1.03 at 1.48, 1.18 at 0.88); the
#   front cy - ry ~ the side view's far-pillar front (-0.43 at the bottom) -> -0.5 at the hem.
SKIRT_ROWS = [(3.80, 0.62, 0.02, 0.34), (3.30, 0.74, 0.04, 0.40), (2.90, 1.00, 0.10, 0.50), (2.10, 1.40, 0.22, 0.66),
              (1.30, 1.78, 0.33, 0.80), (0.90, 1.96, 0.38, 0.86), (0.00, 2.25, 0.45, 0.95), (-0.40, 2.38, 0.48, 1.00)]
SKIRT_TOP = 3.75                      # the skirt's top cap (buried inside the torso)
SKIRT_T = 0.30                        # "skirt thickness": the shell wall (u)
# "hem": z_hem(x) = HEM_H (|x| / HEM_X)^HEM_P -- the BACK view's hem: 0.11 at x 0.79, 0.31 at 1.33, 0.51 at 1.63, 0.71
# at 1.81, 0.91 at 1.91 (lowest at the back centre, rising to the sides); the FRONT view's slanted panel bottoms (outer
# corner 2.03, 0.87) follow the same curve
HEM_H, HEM_X, HEM_P = 0.90, 1.91, 3.0
# "front opening": the shell is cut away in front (y < cy) where |x| < the gap half-width, linear in z between these
GAP_HALF = ((3.30, 0.40), (0.0, 0.72))   # (z, half-width): at the torso / at the hem (v1 pillars' inner edges: 0.43 /
                                         #   1.19 -> the skirt wraps in to 0.72 at the hem, near the belly lobe +-0.52)
SKIRT_K = 0.05                        # hem + opening edge rounding
SKIRT_STEP = 0.025                    # skirt SDF step
SKIRT_TRIS = 4600                     # "skirt mesh detail" (whole shell, before halving + the stripe cuts)
# "watery stripes" (softer + fewer than v1): light stripes on the OUTER surface centred at these angles (0 = front,
# 90 = the unit's left, 180 = back) with this half-width (deg); the INNER surface is the deep tone
STRIPES = ((180.0, 8.0), (115.0, 7.0), (-115.0, 7.0))
RIB_AMP, RIB_WL, RIB_BOW = 0.003, 0.17, 0.07   # "watery ribs" (normal map only): ripple amplitude (v1 run 1 at 0.010 read as stacked rings), wavelength, the
                                               #   ribs' downward bow toward the stripes (u)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_SAMPLES = 48
AO_FLOOR = {"body": 0.50, "clean": 0.90, "jelly": 0.70}   # baked AO remapped to [floor, 1] per region class
BAKE_CAGE_FLOOR = 0.04
SPECULAR = 0.25                       # "sheen" (the sheet's body has soft highlights)
# ---- rig
SKIRT_CHAINS = {"skirt_back": [(0.0, 0.20, 3.40), (0.0, 0.80, 1.80), (0.0, 1.30, 0.20)],    # (v2) three 2-bone chains
                "skirt_L": [(0.62, 0.05, 3.40), (1.30, 0.25, 1.80), (1.95, 0.40, 0.55)]}     # (skirt_R = skirt_L mirrored)
SKIRT_AZ_BLEND = (25.0, 80.0)         # azimuth from the back (deg): fully skirt_back below the first, fully side above
SKIRT_PARENT_S0 = -0.30               # the skirt weights start blending from the body this far ABOVE the chains (buried)
HEAD_OWN_BLEND, ARM_OWN_BLEND = 0.05, 0.08
# ---- clips (PLACEHOLDER: the artist has not given the movement or hover height). 24 fps; integer harmonics per loop ->
#   frame N+1 == frame 1. Rest pose: the skirt hem on the floor (z 0); the clips only lift.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_LIFT = (0.06, 0.24)              # "hover bob": body lift between these (u), 2 bobs per loop (min > 0: the roll wander
                                      #   tips a hem corner ~0.035 down)
IDLE_WANDER = (1.0, 0.8)              # body pitch / roll wander deg (1 cyc)
IDLE_HEAD = 1.5                       # head nod deg (2 cyc, lagging the bob)
IDLE_HALO = (0.035, 10.0)             # "halo bob" (u, 2 cyc, lagging) / "halo sway" (deg about its own axis, 1 cyc)
IDLE_WING = (3.0, 5.0, 1, 0.7)        # "wing sway" (root deg, tip deg, cyc, lag) -- mirrored L/R (a slow symmetric flap)
IDLE_SKIRT = (1.0, 1.5, 1, 0.6)       # skirt sway fore-aft (root, tip deg, cyc, lag)
WALK_N = 48                           # "drift loop": 2 s
WALK_LIFT, WALK_BOB = 0.18, 0.03      # mean lift + bob (2 cyc)
WALK_PITCH, WALK_PITCH_OSC = 8.0, 1.0  # "lean into the drift": forward deg + wobble (2 cyc)
WALK_HEAD_COUNTER = 0.5               # the head undoes this share of the lean (gaze stays level-ish)
WALK_HALO_TRAIL = 6.0                 # halo trails back (deg)
WALK_WING_TRAIL = (6.0, 4.0)          # wings sweep back (root, tip deg about Z)
WALK_SKIRT_TRAIL = (5.0, 3.0)         # the skirt trails back (root, tip deg about X)

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
          "design/reference/halo_sentinel-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; front fixes scale + heights, side gives depths; floor z = 0 at the "
                   "skirt hem (it floats)", "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
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
    return V[keep_v], F2, {"pieces": int(len(cnt)), "dropped_verts": int((~keep_v).sum())}


def decimate(V, F, target):
    tmp = new_obj("dec_src", V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / len(F))
    LV_, LF_ = evaluated_arrays(tmp)
    me_ = tmp.data
    bpy.data.objects.remove(tmp, do_unlink=True); bpy.data.meshes.remove(me_)
    return keep_islands(LV_, LF_, 0.01)


def deviation(HV_, HF_, LV_, LF_):
    bh = BVHTree.FromPolygons(HV_.tolist(), HF_)
    d1 = np.array([bh.find_nearest(Vector(p))[3] for p in LV_])
    bl = BVHTree.FromPolygons(LV_.tolist(), LF_)
    hs = np.arange(0, len(HV_), max(1, len(HV_) // 20000))
    d2 = np.array([bl.find_nearest(Vector(HV_[i]))[3] for i in hs])
    bb_h = np.ptp(HV_, 0); bb_l = np.ptp(LV_, 0)
    return {"low_to_high": {"mean": round(float(d1.mean()), 5), "p99": round(float(np.percentile(d1, 99)), 5),
                            "max": round(float(d1.max()), 5)},
            "high_to_low_sampled": {"p99": round(float(np.percentile(d2, 99)), 5), "max": round(float(d2.max()), 5)},
            "bbox_low_over_high": (bb_l / bb_h).round(5).tolist()}


def mirror_halve(V, F):
    """lava_komodo / crystal_canine technique: marching tets (Kuhn diagonal) + collapse decimation are not mirror-exact, so
    the low keeps its X >= 0 half (bisected at X = 0); the half is densified + cut, then mirror_weld() adds its mirror."""
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


def mirror_weld(V, F, seam_tol=1e-6):
    """half (X >= 0, open on the seam) -> the closed symmetric mesh: seam vertices (X == 0) shared, mirror faces reversed"""
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


class Cutter:
    """firefly's iso-cutter on one bmesh with per-vertex float layers (the snap moves the FIELD only)"""

    def __init__(self, V, F, fields):
        self.bm = bm = bmesh.new()
        for p in V:
            bm.verts.new(p)
        bm.verts.ensure_lookup_table()
        for f in F:
            bm.faces.new([bm.verts[i] for i in f])
        bm.verts.index_update()
        self.LAY = {k: bm.verts.layers.float.new(k) for k in fields}
        for v in bm.verts:
            for k, arr in fields.items():
                v[self.LAY[k]] = float(arr[v.index])

    def cut(self, key, tau, gate=None):
        bm, LAY = self.bm, self.LAY
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

    def finish(self):
        bm = self.bm
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bm.verts.index_update(); bm.faces.index_update()
        V = np.array([v.co[:] for v in bm.verts])
        F = [[v.index for v in f.verts] for f in bm.faces]
        FV = {k: np.array([np.mean([v[L] for v in f.verts]) for f in bm.faces]) for k, L in self.LAY.items()}
        bm.free()
        return V, F, FV


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body: one SDF
t_sdf = time.time()
_HC, _HR = np.array(HEAD_C, float), np.array(HEAD_R, float)
_TC, _TR = np.array(TORSO_C, float), np.array(TORSO_R, float)
_SC = np.array(SHOULDER_C, float)
_LC, _LR = np.array(LOBE_C, float), np.array(LOBE_R, float)
_AA, _AT = np.array(ARM_ROOT, float), np.array(ARM_TIP, float)


def sd_head(P):
    return SD.sd_ellipsoid(P, _HC, _HR)


def sd_torso(P):
    return SD.sd_ellipsoid(P, _TC, _TR)


def sd_shoulders(P):
    """the two shoulder masses as ONE op (a pair min: the composite stays exactly mirror-symmetric)"""
    return np.minimum(SD.sd_sphere(P, _SC, SHOULDER_R), SD.sd_sphere(P, _SC * MIRX, SHOULDER_R))


def sd_arm_one(P, m):
    return FP.sd_ellipsoid_axis(P, _AA * m, _AT * m, ARM_R[0], ARM_R[1])


def sd_arms(P):
    return np.minimum(sd_arm_one(P, np.ones(3)), sd_arm_one(P, MIRX))


def sd_lobe(P):
    return SD.smin(SD.sd_ellipsoid(P, _LC, _LR), SD.sd_round_cone(P, LOBE_NECK[0], LOBE_NECK[2], LOBE_NECK[1], LOBE_NECK[3]),
                   0.15)


# (name, group, fn, lo, hi, k) applied IN THIS ORDER (sd_body reproduces the grid's composite exactly)
_arm_lo = np.minimum(_AA, _AT) - max(ARM_R) - 0.05
_arm_hi = np.maximum(_AA, _AT) + max(ARM_R) + 0.05
OPS = [("torso", "torso", sd_torso, _TC - _TR, _TC + _TR, 0.0),
       ("shoulders", "torso", sd_shoulders, _SC * MIRX - SHOULDER_R, _SC + SHOULDER_R, SHOULDER_K),
       ("lobe", "lobe", sd_lobe, _LC - _LR, np.array([0.52, 0.0, 3.85]), LOBE_K),
       ("arms", "arm", sd_arms, np.array([-_arm_hi[0], _arm_lo[1], _arm_lo[2]]), _arm_hi, ARM_K),
       ("head", "head", sd_head, _HC - _HR, _HC + _HR, HEAD_K)]
GROUPS = ["torso", "lobe", "arm", "head"]


def sd_body(P):
    d = None
    for _, _, fn, _, _, k in OPS:
        v = fn(P)
        d = v if d is None else SD.smin(d, v, k)
    return d


def group_d(P):
    out = {}
    for _, gr, fn, _, _, _ in OPS:
        v = fn(P)
        out[gr] = v if gr not in out else np.minimum(out[gr], v)
    return out


def own(Dg, gname):
    """> 0 where group gname owns the surface (the smooth-union crease is the 0 line)"""
    rest = np.min(np.stack([Dg[g_] for g_ in GROUPS if g_ != gname], 1), 1)
    return rest - Dg[gname]


_lo = np.min([o[3] for o in OPS], 0) - 0.06
_hi = np.max([o[4] for o in OPS], 0) + 0.06
_lo[0] = -max(abs(_lo[0]), abs(_hi[0])); _hi[0] = -_lo[0]
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
for nm, gr, fn, lo, hi, k in OPS:
    g.apply(fn, lo, hi, k)
t_poly = time.time()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "seconds_fields": round(t_poly - t_sdf, 1)}
del g
assert crumbs["pieces"] == 1 or crumbs["dropped_verts"] < 0.001 * len(HV), ("body pieces fell off", crumbs)


def eye_zone(P, pad):
    """front surface points within pad of the head eye / belly emblem ovals: {'head', 'belly', 'belly_pupil'} masks"""
    hz = (P[:, 1] < HEAD_C[1] - 0.25) & (FP.oval_front(P, EYE_C[0], EYE_C[1], EYE_RIM_AB[0] + pad, EYE_RIM_AB[1] + pad) > 0)
    bz = (P[:, 1] < LOBE_C[1] - 0.15) & (FP.oval_front(P, BELLY_C[0], BELLY_C[1], BELLY_RING_AB[0] + pad,
                                                        BELLY_RING_AB[1] + pad) > 0)
    pz = (P[:, 1] < LOBE_C[1] - 0.15) & (FP.oval_front(P, BELLY_PUPIL_C[0], BELLY_PUPIL_C[1], BELLY_PUPIL_AB[0] + pad,
                                                        BELLY_PUPIL_AB[1] + pad) > 0)
    return {"head": hz, "belly": bz, "belly_pupil": pz}


def project_to_sdf(V, mask, iters=4, h=0.002):
    """Newton-project the masked vertices onto sd_body's zero set (removes the marching-tet ripple on the eye faces:
    eye_totem's smooth-eye-face lesson). -> V', moved, max move"""
    V = np.array(V, float)
    P = V[mask].copy()
    for _ in range(iters):
        d = sd_body(P)
        G = np.zeros_like(P)
        for k in range(3):
            e = np.zeros(3); e[k] = h
            G[:, k] = (sd_body(P + e) - sd_body(P - e)) / (2 * h)
        G /= np.maximum(np.linalg.norm(G, axis=1, keepdims=True), 1e-12)
        P -= d[:, None] * G
    mv = float(np.linalg.norm(P - V[mask], axis=1).max()) if mask.any() else 0.0
    V[mask] = P
    return V, int(mask.sum()), mv


_ez = eye_zone(HV, 0.12)
HV, _n_hp, _mv_hp = project_to_sdf(HV, _ez["head"] | _ez["belly"])
report["sdf"]["eye_face_projected_high"] = {"verts": _n_hp, "max_move": round(_mv_hp, 5)}
report["sdf"]["seconds"] = round(time.time() - t_sdf, 1)
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: decimate -> halve -> densify
t = time.time()
LV0, LF0, specks = decimate(HV, HF, BODY_TRIS)
dev_raw = deviation(HV, HF, LV0, LF0)
assert np.all(np.array(dev_raw["bbox_low_over_high"]) > 0.98), dev_raw    # the hull-fold hazard (BUILD_CONTRACT)
LVh, LFh = mirror_halve(LV0, LF0)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
bm = bmesh.new()
for p in LVh:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LFh:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
_dense_log = []
for rnd in range(max(r for _, r in EYE_DENSE.values())):
    zf = []
    for f in bm.faces:
        Pf = np.array([v.co[:] for v in f.verts])
        hit = False
        for key, (pad, rr) in EYE_DENSE.items():
            if rnd < rr and eye_zone(Pf, pad)[key].any():
                hit = True
                break
        if hit:
            zf.append(f)
    ze = list({e for f in zf for e in f.edges})
    bmesh.ops.subdivide_edges(bm, edges=ze, cuts=1, use_grid_fill=True)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    _dense_log.append({"round": rnd + 1, "faces": len(zf), "tris_after": len(bm.faces)})
bm.verts.ensure_lookup_table()
for v in bm.verts[_nv0:]:
    seam_ = abs(v.co.x) < 1e-7
    v.co = bvh_hi.find_nearest(v.co)[0]
    if seam_:
        v.co.x = 0.0
bm.verts.index_update()
LVh = np.array([v.co[:] for v in bm.verts])
LFh = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
_seam = np.abs(LVh[:, 0]) < 1e-7
_ezl = eye_zone(LVh, 0.12)
LVh, _n_lp, _mv_lp = project_to_sdf(LVh, _ezl["head"] | _ezl["belly"])
LVh[_seam, 0] = 0.0
LVh[:, 0] = np.maximum(LVh[:, 0], 0.0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic) -> X >= 0 half "
                              "-> eye-zone densify (pulled onto the high) + Newton projection onto the SDF -> iso-cuts -> "
                              "mirror weld" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF0), "specks": specks, "raw_low_vs_high": dev_raw,
                    "half_tris": tri_count_F(LFh), "eye_densify": {"zones": EYE_DENSE, "rounds": _dense_log,
                                                                   "new_verts": len(LVh) - _nv0},
                    "eye_face_projected_low": {"verts": _n_lp, "max_move": round(_mv_lp, 5)},
                    "seconds": round(time.time() - t, 1)}

# =========================================================================== 3. eye regions: iso-cuts on the half
Dg = group_d(LVh)


def eye_fields(P):
    hf = P[:, 1] < HEAD_C[1] - 0.25
    bf = P[:, 1] < LOBE_C[1] - 0.15
    neg = -1.0
    return {"eye_rim": np.where(hf, FP.almond_front(P, EYE_C[0], EYE_C[1], EYE_RIM_AB[0], EYE_RIM_AB[1], EYE_ALMOND), neg),
            "eye_white": np.where(hf, FP.almond_front(P, EYE_C[0], EYE_C[1], EYE_WHITE_AB[0], EYE_WHITE_AB[1], EYE_ALMOND), neg),
            "pupil": np.where(hf, FP.oval_front(P, PUPIL_C[0], PUPIL_C[1], *PUPIL_AB), neg),
            "belly_ring": np.where(bf, FP.oval_front(P, BELLY_C[0], BELLY_C[1], *BELLY_RING_AB), neg),
            "belly_pink": np.where(bf, FP.oval_front(P, BELLY_C[0], BELLY_C[1], *BELLY_PINK_AB), neg),
            "belly_white": np.where(bf, FP.oval_front(P, BELLY_C[0], BELLY_C[1], *BELLY_WHITE_AB), neg),
            "belly_pupil": np.where(bf, FP.oval_front(P, BELLY_PUPIL_C[0], BELLY_PUPIL_C[1], *BELLY_PUPIL_AB), neg)}


FIELDS = eye_fields(LVh)
FIELDS.update({"own_" + g_: own(Dg, g_) for g_ in GROUPS})
cu = Cutter(LVh, LFh, FIELDS)
TOL = 0.004
L_ = cu.LAY
inside = lambda key: (lambda a, b: a[L_[key]] > -TOL and b[L_[key]] > -TOL)
on_head = lambda a, b: a[L_["own_head"]] > -0.05 and b[L_["own_head"]] > -0.05
CUTS = [("eye_rim", 0.0, on_head), ("eye_white", 0.0, inside("eye_rim")), ("pupil", 0.0, inside("eye_white")),
        ("belly_ring", 0.0, None), ("belly_pink", 0.0, inside("belly_ring")), ("belly_white", 0.0, inside("belly_pink")),
        ("belly_pupil", 0.0, inside("belly_white"))]
t = time.time()
cut_log = [cu.cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
CVh, CFh, FVh = cu.finish()
CVh[np.abs(CVh[:, 0]) < 1e-7, 0] = 0.0
CV, CF, SEAM_N = mirror_weld(CVh, CFh)
FV = {k: np.concatenate([v, v]) for k, v in FVh.items()}
shell_reg = np.full(len(CF), "body", dtype=object)
hd = FV["own_head"] > -0.05
shell_reg[hd & (FV["eye_rim"] > 0)] = "eye_rim"
shell_reg[hd & (FV["eye_white"] > 0)] = "eye_white"
shell_reg[hd & (FV["pupil"] > 0)] = "pupil"
shell_reg[FV["belly_ring"] > 0] = "belly_ring"
shell_reg[FV["belly_pink"] > 0] = "belly_pink"
shell_reg[FV["belly_white"] > 0] = "eye_white"
shell_reg[FV["belly_pupil"] > 0] = "pupil"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF),
                      "seam_verts": SEAM_N}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 4. parts: halo + horns + jelly skirt
PARTS = []
_pa = math.radians(HALO_PITCH)
HALO_N = np.array([0.0, math.sin(_pa), math.cos(_pa)])           # front-up pitch: the normal leans back
V, F, R = FP.halo_ring(HALO_C, HALO_N, HALO_R0, HALO_TUBE, HALO_SEG[0], HALO_SEG[1], "halo")
PARTS.append(FP.Part("halo", V, F, R, bone="halo"))
_ring_side = np.array(HALO_C) + HALO_R0 * np.array([1.0, 0.0, 0.0])
_stem = SD.bezier(_ring_side, HORN_CTRL, HORN_BASE, 12)
_stem_r = np.linspace(HORN_STEM_R[0], HORN_STEM_R[1], len(_stem))
V1, F1, R1 = FP.tube(_stem, _stem_r, 8, "halo")
_hax = unit(np.array(HORN_TIP) - np.array(HORN_BASE))
_hl = float(np.linalg.norm(np.array(HORN_TIP) - np.array(HORN_BASE)))
V2, F2, R2 = FP.lathe([(0.0, _hl), (HORN_HEAD_R, 0.0), (HORN_HEAD_R * 0.55, -0.025), (0.0, -0.03)], ["halo"] * 3, 12,
                      HORN_BASE, _hax)
p_ = FP.Part("horn_stem.L", V1, F1, R1, bone="halo"); PARTS.append(p_); PARTS.append(FP.mirror_part(p_, "horn_stem.R"))
p_ = FP.Part("horn_tip.L", V2, F2, R2, bone="halo"); PARTS.append(p_); PARTS.append(FP.mirror_part(p_, "horn_tip.R"))
_hd_tip = float(np.linalg.norm((np.array(HORN_TIP) - _HC) / _HR))
assert _hd_tip > 1.03, ("a horn tip touches the head", _hd_tip)

# ---- jelly SKIRT (v2): one SDF cone SHELL (outer per-height ellipses, wall SKIRT_T), capped in the torso, cut by the
#      hem curve at the bottom and by the front opening; symmetric SDF -> decimate -> X >= 0 half -> stripe / inner cuts
#      -> mirror weld (exact symmetry)
t = time.time()
PR = FP.PillarRows([(z_, -rx_, rx_, cy_ - ry_, cy_ + ry_) for z_, rx_, cy_, ry_ in SKIRT_ROWS])
_gz = sorted(GAP_HALF)


def gap_half(z):
    return np.interp(z, [g_[0] for g_ in _gz], [g_[1] for g_ in _gz])


def sd_skirt_outer(P, ribs=False):
    d = PR.sd(P)
    if ribs:
        th = np.radians(PR.theta(P))
        bow = RIB_BOW * np.max(np.stack([np.cos(th - math.radians(c_)) for c_, _ in STRIPES], 1), 1)
        d = d - RIB_AMP * np.sin(2 * math.pi * (P[:, 2] + bow) / RIB_WL)
    return d


def sd_skirt(P, ribs=False):
    d = np.abs(sd_skirt_outer(P, ribs) + 0.5 * SKIRT_T) - 0.5 * SKIRT_T          # the shell wall (onion)
    d = np.maximum(d, P[:, 2] - SKIRT_TOP)
    ax = np.abs(P[:, 0])
    zh = HEM_H * (ax / HEM_X) ** HEM_P
    slope = HEM_H * HEM_P * ax ** (HEM_P - 1) / HEM_X ** HEM_P
    d = SD.smax(d, (zh - P[:, 2]) / np.sqrt(1.0 + slope * slope), SKIRT_K)        # the hem
    cx, cy, rx, ry = PR.at(P[:, 2])
    d = SD.smax(d, np.minimum(gap_half(P[:, 2]) - ax, cy - P[:, 1]), SKIRT_K)     # the front opening
    return d


_sk_lo = np.array([-max(r[1] for r in SKIRT_ROWS) - 0.1, min(r[2] - r[3] for r in SKIRT_ROWS) - 0.1, -0.1])
_sk_hi = np.array([-_sk_lo[0], max(r[2] + r[3] for r in SKIRT_ROWS) + 0.1, SKIRT_TOP + 0.1])
SK_HIGH = {}
for ribs in (False, True):
    gp = SD.Grid(_sk_lo, _sk_hi, SKIRT_STEP, 0.2)
    gp.apply(lambda P, r_=ribs: sd_skirt(P, r_), _sk_lo, _sk_hi, 0.0)
    V_, F_ = SD.polygonise(gp)
    F_ = [list(map(int, f)) for f in F_]
    V_, F_, cr_ = keep_islands(V_, F_, 0.02)
    SK_HIGH[ribs] = (V_, F_, cr_)
    del gp
assert SK_HIGH[False][2]["pieces"] == 1, ("the skirt must be ONE piece (joined across the back)", SK_HIGH[False][2])
PV, PF = SK_HIGH[False][0], SK_HIGH[False][1]
SLV0, SLF0, p_specks = decimate(PV, PF, SKIRT_TRIS)
p_dev = deviation(PV, PF, SLV0, SLF0)
assert np.all(np.array(p_dev["bbox_low_over_high"]) > 0.98), p_dev
SLVh, SLFh = mirror_halve(SLV0, SLF0)


def wrapd(a):
    return np.abs((a + 180.0) % 360.0 - 180.0)


def skirt_fields(P):
    th = PR.theta(P)
    outer = sd_skirt_outer(P) + 0.5 * SKIRT_T                  # > 0 on the outer surface, < 0 on the inner wall
    light = np.max(np.stack([w_ - wrapd(th - c_) for c_, w_ in STRIPES], 1), 1)
    return {"outer": outer, "light": light}


cp = Cutter(SLVh, SLFh, skirt_fields(SLVh))
_on_outer = lambda a, b: a[cp.LAY["outer"]] > -0.01 and b[cp.LAY["outer"]] > -0.01
p_cut_log = [cp.cut("outer", 0.0), cp.cut("light", 0.0, _on_outer)]
PCVh, PCFh, PFVh = cp.finish()
PCVh[np.abs(PCVh[:, 0]) < 1e-7, 0] = 0.0
PCV, PCF, SK_SEAM = mirror_weld(PCVh, PCFh)
PFV = {k: np.concatenate([v, v]) for k, v in PFVh.items()}
p_reg = np.full(len(PCF), "jelly", dtype=object)
p_reg[(PFV["outer"] > 0) & (PFV["light"] > 0)] = "jelly_light"
p_reg[PFV["outer"] < 0] = "jelly_deep"
p_ = FP.Part("skirt", PCV, PCF, list(p_reg), bone="skirt")
p_.jelly = True
PARTS.append(p_)
# the opening + wrap, measured on the built shell (outer surface faces: the front edge = the outer-surface vertex nearest
# the midline in front of the centre)
_ov = PCV[(skirt_fields(PCV)["outer"] > -0.01)]
_cx, _cy, _rx, _ry = PR.at(_ov[:, 2])
_fr = _ov[_ov[:, 1] < _cy]


def _gap_at(z0, dz=0.08):
    m_ = np.abs(_fr[:, 2] - z0) < dz
    return round(2.0 * float(np.abs(_fr[m_, 0]).min()), 3) if m_.any() else None


_hem_z = float(PCV[:, 2].min())
_edge = _fr[np.abs(_fr[:, 2] - 0.30) < 0.08]
_e = _edge[np.argmin(np.abs(_edge[:, 0]))]
_ecx, _ecy, _erx, _ery = PR.at(np.array([_e[2]]))
_wrap = 360.0 - 2.0 * float(wrapd(np.degrees(np.arctan2(_e[0] / _erx[0], -(_e[1] - _ecy[0]) / _ery[0]))))
report["skirt"] = {"step": SKIRT_STEP, "high_tris": len(PF), "high_tris_ribbed": len(SK_HIGH[True][1]),
                   "crumbs": SK_HIGH[False][2], "decimated_tris": tri_count_F(SLF0), "specks": p_specks,
                   "low_vs_high": p_dev, "cuts": p_cut_log, "tris_after_cuts": tri_count_F(PCF), "seam_verts": SK_SEAM,
                   "opening_width": {"at_top_z3.3": _gap_at(3.3), "at_z2.0": _gap_at(2.0), "at_z1.0": _gap_at(1.0),
                                     "at_hem_z0.3": _gap_at(0.3), "v1_pillar_gap_at_hem": 2.38},
                   "wrap_deg_at_z0.3": round(_wrap, 1), "front_edge_at_z0.3": _e.round(3).tolist(),
                   "seconds": round(time.time() - t, 1),
                   "rule": "one cone shell (outer per-height ellipses from the measured rows, wall %.2f, top cap in the "
                           "torso, hem z = %.2f (|x| / %.2f)^%.0f, front opening |x| < %s for y < centre), rounded k %.2f; "
                           "joined across the back; mirror-halved + welded; ribs in the bake high only (normal map)" % (
                               SKIRT_T, HEM_H, HEM_X, HEM_P, GAP_HALF, SKIRT_K)}
print("SKIRT", json.dumps(report["skirt"]))

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
_low_piece = min(ISL, key=lambda i: i[1][:, 2].min())[0]
assert _low_piece == "skirt", ("the lowest point must be the skirt hem", _low_piece)
SHIFT = np.array([0.0, (lo0[1] + hi0[1]) / 2, lo0[2]])          # X: symmetric about X = 0 by construction
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, RANGE, MATI = [], [], [], {}, []
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    MATI += [1 if (p is not None and getattr(p, "jelly", False)) else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
assert abs(float(VALL[:, 2].min())) < 1e-9 and float(VALL[:, 2].min()) >= 0.0, "rest pose: min z 0 at the skirt hem"
for name, (a_, b_) in RANGE.items():                       # nothing dips below the floor (lesson 2026-10-10)
    assert VALL[a_:b_, 2].min() >= -1e-9, (name, float(VALL[a_:b_, 2].min()))
HV_s = HV - SHIFT
REG = ["body", "eye_rim", "eye_white", "pupil", "belly_ring", "belly_pink", "halo", "jelly", "jelly_light", "jelly_deep"]
JELLY_REG = {"jelly", "jelly_light", "jelly_deep"}
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
# no region borrows a field: every iso-cut region owns ONLY faces its own field claims, and every field paints
_OWN = {"eye_rim": ["eye_rim"], "eye_white": ["eye_white", "belly_white"], "pupil": ["pupil", "belly_pupil"],
        "belly_ring": ["belly_ring"], "belly_pink": ["belly_pink"]}
for r_, fs_ in _OWN.items():
    m_ = shell_reg == r_
    assert np.all(np.max(np.stack([FV[f_][m_] for f_ in fs_], 1), 1) > 0.0), "region %s has faces outside its field" % r_
    for f_ in fs_:
        assert np.any(FV[f_] > 0.0), "field %s never paints" % f_
assert np.all(PFV["light"][p_reg == "jelly_light"] > 0) and np.all(PFV["outer"][p_reg == "jelly_light"] > 0) and     np.all(PFV["outer"][p_reg == "jelly_deep"] < 0), "stripe borrows"


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
    bsdf.inputs["Specular IOR Level"].default_value = SPECULAR
    if kind == "jelly":
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        # BLENDED (fogeist: dithered alpha renders as grain, lesson 2026-10-10); one transparent layer (no overlap: the
        # closed skirt shell's far side never sorts over its near side); a solid shadow (a stochastic one renders as grain)
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


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        paint_alpha(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
            if s_.material.name == UNIT + "_jelly":
                s_.material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = \
                    float(pal["material"]["jelly_roughness"])
    return out


GLOW_ALLOWED = {"halo", "pupil"}      # the brief: the halo (+ its horns) and the eye pupils (faint)
GLOW_CAP = 2.0


def glow_tiers(pal):
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    es = float(pal["material"].get("emission_strength", 1.0))
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "emission_strength": es,
            "only_drawn_glow": sorted(tier) == sorted(GLOW_ALLOWED), "cap": GLOW_CAP,
            "cap_pass": all(v <= GLOW_CAP and v * es <= GLOW_CAP for v in tier.values()),
            "max_effective": round(max(tier.values()) * es, 3)}


MAT_BODY = make_mat(UNIT + "_body", "body")
MAT_JELLY = make_mat(UNIT + "_jelly", "jelly")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)   # palette <-> regions 1:1
for n in REG:                                             # translucency lives on the jelly material only
    assert (n in JELLY_REG) == ("alpha" in pal_default["regions"][n]), ("alpha on the wrong material", n)
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
_gt = report["glow_tiers"]["default"]
assert _gt["only_drawn_glow"] and _gt["cap_pass"], _gt

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_JELLY)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
PAL.store_regions(me, REG, rid, np.ones(len(FALL)))       # no per-face jitter: clean smooth body, clean jelly
report["regions_faces"] = repaint([low], pal_default)
report["alpha"] = {"main": paint_alpha(me, pal_default), "material_alpha": pal_default["material"].get("alpha", 1.0),
                   "jelly_render_method": MAT_JELLY.surface_render_method,
                   "jelly_transparency_overlap": bool(getattr(MAT_JELLY, "use_transparency_overlap", None))}
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre at eye height -> the eye centre on the head front (on the midline)
_ys = np.linspace(HEAD_C[1] - HEAD_R[1] - 0.2, HEAD_C[1], 4001)
_pp = np.stack([np.zeros_like(_ys), _ys, np.full_like(_ys, EYE_C[1])], 1)
_yf = float(_ys[np.argmax(sd_body(_pp) <= 0.0)])
anchor = np.array([0.0, HEAD_C[1], EYE_C[1]]) - SHIFT
landmark = np.array([0.0, _yf, EYE_C[1]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre at eye height -> the eye centre on the head front (the face looks -Y)",
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
_fc_all = np.array([VALL[f].mean(0) for f in FALL])
_mis = 0
for i_f in range(0, len(FALL), 7):
    c_ = _fc_all[i_f]
    _, _, j_f, _ = _bvh_all.find_nearest(Vector((-c_[0], c_[1], c_[2])))
    if j_f is not None and RNAME[j_f] != RNAME[i_f]:
        _mis += 1
report["mirror_test"] = {"max_abs_asymmetry": round(float(_mir.max()), 8), "p99": round(float(np.percentile(_mir, 99)), 8),
                         "per_piece_max": {k_: round(float(_mir[v_[0]:v_[1]].max()), 8) for k_, v_ in RANGE.items()},
                         "region_mismatch_sampled": {"faces_sampled": len(range(0, len(FALL), 7)), "mismatched": _mis},
                         "rule": "every rest vertex mirrored across X = 0 -> distance to the nearest point of the mesh"}
print("MIRROR", json.dumps(report["mirror_test"]))

tris_main = tri_count_F(FALL)
_FCs = np.array([np.mean(CV[f], 0) for f in CF])
_dgs = group_d(_FCs)
_fown = np.argmin(np.stack([_dgs[g_] for g_ in GROUPS], 1), 1)
report["tris"] = {"total": tris_main, "window": TRI_BUDGET, "shell": tri_count_F(CF),
                  "per_part": {"body shell (head + torso + arms + belly lobe, one SDF)": tri_count_F(CF),
                               **{p.name: p.tris() for p in PARTS}},
                  "shell_by_owner_group": {g_: int((_fown == j).sum()) for j, g_ in enumerate(GROUPS)},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role: window [%d, %d]. The smooth body shell (~%d decimated tris + the densified "
                            "eye faces) carries most of it; the jelly skirt shell (~%d decimated + cuts), the halo ring and "
                            "the horn arrows the rest." % (TRI_BUDGET[0], TRI_BUDGET[1], BODY_TRIS, SKIRT_TRIS))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"per_piece": open_e, "closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is closed: the mirror-welded SDF shell, the mirror-welded skirt shell, the halo torus, "
                                "the pole-capped horn stems and arrowhead cones"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet": {"front": "H 6.08 W 5.33 (intake)", "back": "H 6.18 W 4.78 (cut at x 545)",
                               "side": "H 6.27 W 2.25"},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
report["sheet_deviations"] = [
    "SKIRT (v2, artist verdict 2026-10-11): the FRONT + SIDE draw two leg pillars, the BACK one wide cone skirt; the artist "
    "chose ONE skirt joined across the back that wraps further round toward the front. Built: one cone SHELL (wall %.2f) on "
    "the back view's outline (+-0.74 at z 3.3 -> 1.96 at 0.9), hem rising from the back centre (z 0) to the sides (0.9 at "
    "|x| 1.91) as the back view draws, OPEN in front: opening %s wide at z 3.3 / %s at z 1.0 / %s at z 0.3 (v1 pillar gap "
    "2.38 at the hem), wrapping %.0f deg of 360 round the unit. Through the opening the front sees the skirt's deep inner "
    "back wall behind the belly lobe (the sheet's front shows background there)" % (
        SKIRT_T, report["skirt"]["opening_width"]["at_top_z3.3"], report["skirt"]["opening_width"]["at_z1.0"],
        report["skirt"]["opening_width"]["at_hem_z0.3"], report["skirt"]["wrap_deg_at_z0.3"]),
    "SKIRT HEM: the front view's panel bottoms reach the floor at their inner corner (1.19, 0.0); the built hem follows the "
    "back view (low at the back centre, %.2f at |x| 1.19), so the front panels' inner bottoms sit higher than drawn" % (
        HEM_H * (1.19 / HEM_X) ** HEM_P),
    "HALO TILT: the FRONT view (and both detail panels) hide the ring's LOWER arc behind the head between the horns, which "
    "only a FRONT-UP pitch allows (the lower arc is the back arc); the SIDE view draws the front end LOWER (front-down). "
    "Built front-up %.1f deg (the front view: ellipse half-height 0.25 of 0.98); the side view's tilt is mirrored" % HALO_PITCH,
    "HALO ROLL: the front view's ring ends sit at z 5.69 (image left) / 5.79 (image right): a ~6 deg roll; built level "
    "(house rule: symmetric rest pose)",
    "HORNS: the front view draws the arrowhead tips at x +-0.38 (z 5.36), inside the head's silhouette (the drawing "
    "overlaps them onto the head); built at x +-%.2f, Y %.2f, held 6 %% outside the head ellipsoid so they never "
    "intersect it; the drawn arrowheads are flat barbed shapes, built as closed cones" % (HORN_TIP[0], HORN_TIP[1]),
    "SIDE VIEW: drawn ~3 % larger (H 6.27 vs 6.08) and slightly turned (its two blue panels do not overlap: the far one shows "
    "0.4 u in front of the near one); depths are taken off it unscaled, heights off the FRONT",
    "WINGS: the side view hangs the arm paddle BACK to Y ~1.1 at its tip; the front reads it sideways-down. Built along "
    "(%.2f, %.2f, %.2f) -> (%.2f, %.2f, %.2f) (out, down and back), round in section (%.2f x %.2f)" % (
        tuple(ARM_ROOT) + tuple(ARM_TIP) + tuple(ARM_R)),
    "SKIRT TEXTURE: the drawn x-ray-like ribs + spine are NOT geometry (lesson: no ribbed / stacked shapes): three soft "
    "light stripes on the outer surface (back centre + two back-sides), the inner wall in the deep tone, the ribs in the "
    "baked normal map only (amplitude %.3f u, wavelength %.2f u)" % (RIB_AMP, RIB_WL),
    "TRANSLUCENCY: per-region alpha on the closed skirt shell, one BLENDED layer: the sheet's inner refractions / caustics "
    "are not modelled; the skirt casts a solid shadow",
    "EYE COLOURS: the drawn eye + emblem centres are near-white (head 250,241,236; belly 252,232,224), not the 234,191,204 "
    "chip; the drawn tones are used (palette notes). The belly's crimson outer ring (drawn 200,30,99) is not a palette chip",
    "INK LINES + PAINTED SHADING: the black contour lines and the sheet's painted light / shade on the magenta body are "
    "not modelled (one flat body tone; the renderer shades it)",
    "HEAD EYE RIM: the drawn rim is the ink outline (~2 px); built as a thin dark band 0.02 u wide",
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
low["conquest_source"] = "from scratch: design/reference/halo_sentinel-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = ("Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base "
                                   "Color (the halo + horns glow, the pupils glow faintly)")
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; jelly "
                                 "material alphaMode BLEND single-sided (Eevee BLENDED, no overlap); body opaque")


def _box(*names):
    A_ = np.vstack([VALL[RANGE[n][0]:RANGE[n][1]] for n in names])
    return [A_.min(0).tolist(), A_.max(0).tolist()]


low["conquest_focus"] = json.dumps({
    "face_eye": [(np.array([-0.75, -1.0, 4.55]) - SHIFT).tolist(), (np.array([0.75, 0.2, 5.85]) - SHIFT).tolist()],
    "halo_horn": _box("halo", "horn_tip.L", "horn_tip.R"),
    "arm_wing": [(np.array([0.9, -0.6, 2.4]) - SHIFT).tolist(), (np.array([3.2, 1.4, 4.9]) - SHIFT).tolist()],
    "lower_body_leg": _box("skirt"),
    "views": {"face_eye": [0.0, 4.0], "halo_horn": [0.0, 12.0], "arm_wing": [-20.0, 6.0], "lower_body_leg": [25.0, 4.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "mirror_test", "regions_area_share", "glow_tiers", "alpha",
                                                            "centre_shift")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
# the bake high: the body SDF high + the RIBBED skirt high + the halo / horns as built
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
_rv, _rf = SK_HIGH[True][0], SK_HIGH[True][1]
_hip = [(_rv, _rf)] + [(p.V, p.F) for p in PARTS if not getattr(p, "jelly", False)]
for V, F in _hip:
    PH_V.append(np.asarray(V) - SHIFT); PH_F += [[i + o_ for i in f] for f in F]; o_ += len(V)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
_pdev = deviation(SK_HIGH[True][0], SK_HIGH[True][1], PCV, PCF)
BAKE_CAGE = max(BAKE_CAGE_FLOOR, round(3.0 * max(dev_raw["high_to_low_sampled"]["p99"],
                                                 _pdev["high_to_low_sampled"]["p99"]), 4))
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
for mat_ in (MAT_BODY, MAT_JELLY):
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
    for mat_ in (MAT_BODY, MAT_JELLY):
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
_jel_tex = texels_of(np.array(MATI) == 1, RN)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the worst high->low sampled p99 (body shell, ribbed skirt; floor %.2f)"
                                              % BAKE_CAGE_FLOOR,
    "resolution": {"normal": RN, "ao": RA}, "ao_samples": AO_SAMPLES,
    "high_tris": len(PH_F), "high_rule": "body SDF high (eye faces projected smooth) + the RIBBED skirt SDF high + "
                                         "halo / horns as built",
    "normal_reference": "FLAT low faces; wired into both materials",
    "uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_dev_p50_p95": [round(float(np.percentile(devn[cov_n & alltex_n], 50)), 4),
                           round(float(np.percentile(devn[cov_n & alltex_n], 95)), 4)],
    "jelly_normal_dev_p50_p95": [round(float(np.percentile(devn[cov_n & _jel_tex], 50)), 4),
                                 round(float(np.percentile(devn[cov_n & _jel_tex], 95)), 4)],
    "ao_baked_pct_of_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_raw_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_raw_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4),
    "ao_floor": AO_FLOOR})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0
_cls = np.array(["jelly" if r in JELLY_REG else ("body" if r == "body" else "clean") for r in RNAME])
for cl_, fl_ in AO_FLOOR.items():
    tx_ = texels_of(_cls == cl_, RA)
    pa[tx_, :3] = fl_ + (1.0 - fl_) * pa[tx_, :3]
img_ao.pixels.foreach_set(pa.ravel())
bstats["ao_p05_after_floor"] = round(float(np.percentile(pa[alltex_a, 0], 5)), 4)
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
for mat_ in (MAT_BODY, MAT_JELLY):
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
                     "provenance": "pixel-sampled from the sheet's six chips + drawn-surface medians (palette notes)"}
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


_amid = 0.5 * (_AA + _AT)
BONES = [("body", P_((0.0, -0.05, 3.60)), P_((0.0, -0.05, 4.60)), "root"),
         ("head", P_((0.0, HEAD_C[1], 4.65)), P_((0.0, HEAD_C[1], 5.75)), "body"),
         ("halo", P_(HALO_C), P_(np.array(HALO_C) + 0.4 * HALO_N), "head")]
for side, m in (("L", np.ones(3)), ("R", MIRX)):
    BONES += [("wing_%s.001" % side, P_(_AA * m), P_(_amid * m), "body"),
              ("wing_%s.002" % side, P_(_amid * m), P_(_AT * m), "wing_%s.001" % side)]
SK_CH = {"skirt_back": np.array(SKIRT_CHAINS["skirt_back"], float), "skirt_L": np.array(SKIRT_CHAINS["skirt_L"], float),
         "skirt_R": np.array(SKIRT_CHAINS["skirt_L"], float) * MIRX}
for ch, pts in SK_CH.items():
    BONES += [(ch + ".001", P_(pts[0]), P_(pts[1]), "body"), (ch + ".002", P_(pts[1]), P_(pts[2]), ch + ".001")]
CONNECT = {"wing_L.002", "wing_R.002", "skirt_back.002", "skirt_L.002", "skirt_R.002"}
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
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
Dw = group_d(Pw)
g_head = smoothstep(-HEAD_OWN_BLEND, HEAD_OWN_BLEND, own(Dw, "head"))
g_arm = smoothstep(-ARM_OWN_BLEND, ARM_OWN_BLEND, own(Dw, "arm")) * (1.0 - g_head)
_ax = unit(_AT - _AA); _Larm = float(np.linalg.norm(_AT - _AA))
Ws = np.zeros((len(Pw), len(DEFORM)))
Ws[:, J["head"]] += g_head
Ws[:, J["body"]] += 1.0 - g_head - g_arm
for side, m, sel in (("L", np.ones(3), Pw[:, 0] >= 0.0), ("R", MIRX, Pw[:, 0] < 0.0)):
    s_arm = (Pw * m - _AA) @ _ax
    Wv = K.vine_weights(np.clip(s_arm, -1.0, _Larm), _Larm, 2, parent_s0=-0.10)
    for j_, bn in enumerate(["body", "wing_%s.001" % side, "wing_%s.002" % side]):
        Ws[sel, J[bn]] += g_arm[sel] * Wv[sel, j_]
Wt[a_:b_] = Ws
for name, (a_, b_) in RANGE.items():
    if name == "shell":
        continue
    p = PARTS[[q.name for q in PARTS].index(name)]
    if name == "skirt":
        Pp = VALL[a_:b_] + SHIFT
        phb = wrapd(PR.theta(Pp) - 180.0)                         # azimuth from the back centre (deg)
        w_back = 1.0 - smoothstep(SKIRT_AZ_BLEND[0], SKIRT_AZ_BLEND[1], phb)
        for ch, wsel in (("skirt_back", w_back), ("skirt_L", (1.0 - w_back) * (Pp[:, 0] >= 0.0)),
                         ("skirt_R", (1.0 - w_back) * (Pp[:, 0] < 0.0))):
            pts = SK_CH[ch]
            Lc = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
            s_c = (pts[0, 2] - Pp[:, 2]) / (pts[0, 2] - pts[-1, 2]) * Lc
            Wv = K.vine_weights(np.clip(s_c, -1.0, Lc + 1.0), Lc, 2, parent_s0=SKIRT_PARENT_S0)
            for j_, bn in enumerate(["body", ch + ".001", ch + ".002"]):
                Wt[a_:b_, J[bn]] += wsel[:, None][:, 0] * Wv[:, j_]
    else:
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
                  "rule": "shell: head by soft head ownership (+-%.2f), arms by soft arm ownership (+-%.2f) -> "
                          "rigkit.vine_weights along the arm axis from the body, the rest body (incl. the belly lobe); "
                          "skirt: blended by azimuth (skirt_back within %.0f deg of the back centre, the side chains past "
                          "%.0f) x vine_weights down each chain from the body (the buried top rides the body); halo + "
                          "horns rigid on 'halo'" % (HEAD_OWN_BLEND, ARM_OWN_BLEND, SKIRT_AZ_BLEND[0], SKIRT_AZ_BLEND[1])}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (every deform bone in BOTH clips); integer cycles per loop
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0), "HALO": tuple(HALO_N)}
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


def wv(u, k, spec, phase=0.0):
    return K.vine_wave(u, k, 2, spec[0], spec[1], spec[3], spec[2], phase)


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        ph_b = TAU * 2 * u
        z = IDLE_LIFT[0] + (IDLE_LIFT[1] - IDLE_LIFT[0]) * 0.5 * (1.0 - math.cos(ph_b))
        P["body"] = ((0.0, 0.0, z), [("X", IDLE_WANDER[0] * math.sin(TAU * u + 0.5)),
                                     ("Y", IDLE_WANDER[1] * math.sin(TAU * u + 1.9))], None)
        P["head"] = (None, [("X", IDLE_HEAD * math.sin(ph_b - 1.2))], None)
        P["halo"] = ((0.0, 0.0, IDLE_HALO[0] * math.sin(ph_b - 1.6)), [("HALO", IDLE_HALO[1] * math.sin(TAU * u))], None)
        for side, sg in (("L", 1.0), ("R", -1.0)):
            for k in range(2):
                P["wing_%s.%03d" % (side, k + 1)] = (None, [("Y", sg * wv(u, k, IDLE_WING, -0.8))], None)
                P["skirt_%s.%03d" % (side, k + 1)] = (None, [("X", wv(u, k, IDLE_SKIRT, 0.4)),
                                                             ("Y", sg * 0.5 * wv(u, k, IDLE_SKIRT, 1.3))], None)
        for k in range(2):
            P["skirt_back.%03d" % (k + 1)] = (None, [("X", wv(u, k, IDLE_SKIRT, 0.1))], None)
    else:
        z = WALK_LIFT + WALK_BOB * math.sin(TAU * 2 * u)
        lean = WALK_PITCH + WALK_PITCH_OSC * math.sin(TAU * 2 * u - 1.0)
        P["body"] = ((0.0, 0.0, z), [("X", lean)], None)
        P["head"] = (None, [("X", -WALK_HEAD_COUNTER * lean)], None)
        P["halo"] = ((0.0, 0.0, 0.02 * math.sin(TAU * 2 * u - 1.8)), [("X", -WALK_HALO_TRAIL - 1.5 * math.sin(TAU * 2 * u - 2.0))],
                     None)
        for side, sg in (("L", 1.0), ("R", -1.0)):
            for k in range(2):
                P["wing_%s.%03d" % (side, k + 1)] = (None, [("Z", sg * (WALK_WING_TRAIL[k] + 1.5 * math.sin(TAU * 2 * u - 1.2 - 0.7 * k)))],
                                                     None)
                P["skirt_%s.%03d" % (side, k + 1)] = (None, [("X", WALK_SKIRT_TRAIL[k] + 1.0 * math.sin(TAU * 2 * u - 1.5 - 0.6 * k))],
                                                      None)
        for k in range(2):
            P["skirt_back.%03d" % (k + 1)] = (None, [("X", WALK_SKIRT_TRAIL[k] + 1.0 * math.sin(TAU * 2 * u - 1.8 - 0.6 * k))],
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


_hA, _hB = RANGE["halo"]
_sA, _sB = RANGE["shell"]
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
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "halo_gap": float(C[_hA:_hB, 2].min() - C[_sA:_sB, 2].max()),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - arm_data.bones["body"].head_local.z
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)]}
    assert clip_rep[cn]["clearance_min_z"] >= -1e-4, ("below the floor", cn, clip_rep[cn]["clearance_min_z"])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hover"] = {"rest_height_units": H, "idle_lift_units": list(IDLE_LIFT), "walk_lift_units": WALK_LIFT,
                "rule": "rest pose: the skirt hem on the floor (contract feet_origin, z 0); the clips lift the body; "
                        "the in-game hover height is the artist's open question"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("halo_sentinel v1: root > body > head > halo; body > wing_L.001 > wing_L.002, wing_R.001 > "
                       "wing_R.002; body > skirt_back.001 > skirt_back.002, skirt_L.001 > skirt_L.002, skirt_R.001 > "
                       "skirt_R.002 (v2: the skirt replaces the leg pillars)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Halo Sentinel's movement or hover height; open "
                               "question): idle = 4 s hover bob (body +%.2f..%.2f u, 0.5 Hz) + gentle pitch/roll wander, "
                               "the halo bobbing and swaying +-%.0f deg about its own axis, a slow symmetric wing sway, the "
                               "skirt swaying; walk = 2 s in-place hover-drift (%.0f deg forward lean, the head "
                               "counter-nodding, the halo + wings + skirt trailing back)" % (
                                   IDLE_LIFT[0], IDLE_LIFT[1], IDLE_HALO[1], WALK_PITCH))
low["conquest_locomotion"] = "hover (floats): rest pose skirt hem z 0 per contract; the clips lift the body"
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
              "structure": "armature identity + skinned 'halo_sentinel' (opaque body/halo primitive + alpha-blend jelly "
                           "primitive); natural scale; report-only cell fit %.5f" % k_fit}
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
