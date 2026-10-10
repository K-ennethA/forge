"""Aquaoct -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/aquaoct_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNITS x4: Bombee, Aquaoct, Canine Bat Hybrid, Raven Rogue (artist sheets)"
(section AQUAOCT) + the sheet design/reference/aquaoct-character-sheet.png (intake: design/reference/aquaoct/intake.json).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. Brief: improve/aquaoct_v1_brief.md (v1 DRAFT).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet, measured off the two TRUE orthos (intake ortho_frame.views =
front, side):
  FRONT view (the unit faces the viewer; image right = +X = the unit's LEFT, L):
        X = (x_px - 234.5) / 100 ; z = (790 - y_px) / 100
  SIDE  view (faces image-left = the unit's front, -Y; seen from +X):
        Y = (x_px - 984.5) / 100 ; z = (791 - y_px) / 100
The intake's front axis is x 242.5 (the bbox centre); the drawing's head egg is centred at x 234.5 (silhouette rows
z 3.6-4.6: e.g. y 386 spans x 117-352) and the tentacle span at x 233 (24-450 less the asymmetric tips), so the mirror
plane X = 0 sits at x 234.5 and conquest_focus.axis_xy puts the sheet's x 242.5 axis 0.08 u to the model's +X. The front
view is drawn a little turned (the face sits ~0.3 u image-left of the egg, the egg top leans ~0.4 u image-right): the
model is mirror-symmetric (house default) and centres the face under the egg.

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the tall glossy EGG head tilted back 38 deg (fitted to
     the side-view outline), the FACE bulge at its lower-front pole, the head BASE (mantle) under it, the scalloped
     BEARD frill (an ellipsoid + rounded lobes), the goggle MASK (two raised rings with the eye holes, the bridge with its
     lower lobe, side straps to the round strap buttons, the two small top tabs) and EIGHT leaf-like TENTACLES (sweeps of
     an arched elliptical cross-section with soft wavy edges along catmull spines, smooth-unioned into the base); flat
     on the floor under the two standing tentacle tips. Marching tets = the HIGH (bake source); collapse decimation =
     the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: part ownership (egg / face / base / beard / mask / tentacles),
     the tentacle top / underside (the elliptical angle round the leaf section: aqua top, cream-white under, the cream
     wrapping the front edge).
  3. PARTS (aquaoct_parts.py, closed): the rounded darker-teal head SPOTS as conforming domed decals (cast from the
     sheet-measured spot centres; v1.1), two big round EYES (pupil + teal iris dome) in the mask rings, their white
     HIGHLIGHTS, the two strap-button LENSES, the small BEAK under the mask lobe.
  4. palette (palettes.py regions -> Col / Glow; no glow), Smart UV, bake normal + AO from the SDF high + parts at seed 0.
  5. RIG: root > body > head; body > tent.<kind>.<L|R>.{0,1,2} (8 vine chains x 3 bones). Clips (PLACEHOLDER, movement
     not yet given by the artist): idle = tentacle sway (de-synced waves, 4 s), walk = in-place tentacle-shuffle glide
     (a ring wave round the skirt + alternating standing-tentacle steps, 2 s). glb export.
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
import aquaoct_parts as FP    # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "aquaoct"
CHAR_ID = "aquaoct"                   # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit (brief)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.28                       # must exceed the largest smooth-union k (BASE_K 0.20) + 2 steps (kit hazard)
BODY_TRIS = 15500                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
SHEET_AXIS_X = 0.08                   # the intake's front axis (x 242.5) sits this far toward +X of the mirror plane (x 234.5)
# ---- head EGG: an ellipsoid tilted back about X, least-squares fitted to the SIDE-view outline (rows y 240-445, 3 px
#   apart + the top cap; mean residual 0.007 u): centre (Y 0.52, z 4.10), tilt 38 deg, semi-axes 1.59 (long) / 1.04;
#   the half-WIDTH is the FRONT view's: rows y 371-416 span x 117-352 (1.18). Top: front y 236 (z 5.54), side y 240.
EGG_C = (0.0, 0.52, 4.10)             # "head position"
EGG_TILT = 38.0                       # "head tilt back" (deg from vertical, top toward +Y)
EGG_R = (1.18, 1.59, 1.04)            # "head size": half width X / long semi-axis / short (front-back) semi-axis
EGG_TAPER = 0.06                      # "egg shape": the perpendicular radii grow this share toward the top (broad top)
# ---- FACE bulge at the egg's lower-front pole (the mask sits on it): SIDE view the mask / beard front at x 888-905
#   (Y -0.97..-0.80) at y 460-560; FRONT view the mask spans x 130-276 at y 462-530
FACE_C, FACE_R, FACE_K = (0.0, -0.50, 2.98), (0.60, 0.52, 0.45), 0.16   # v1.1 gate: shallower (bottom z 2.53, just
                                      #   under the mask) so no jaw block shows below the mask; eye surface Y -0.82 kept
# ---- head BASE (mantle) under the egg, where the tentacles join: SIDE view y 500-600 spans x 905-1070 under the egg
BASE_C, BASE_R, BASE_K = (0.0, 0.0, 2.55), (0.72, 0.68, 0.50), 0.20
# ---- BEARD frill (white, scalloped) under the mask: FRONT view x 155-245 (X -0.80..+0.10 about the drawn face centre
#   x 203 -> half width 0.45), y 528-585; SIDE view x 905-965 (Y -0.80..-0.20), scallop bottoms y 565-575 (z 2.2)
#   v1.1 gate ("a SMALL soft scalloped frill hanging just under the mask centre"): narrower than the mask (0.68 u wide
#   vs the mask's 1.46), short (z 2.69 tucked under the mask -> lobe bottoms ~2.12), four rounded drop-shaped lobes
#   hanging below its edge
BEARD_C, BEARD_R, BEARD_K = (0.0, -0.66, 2.52), (0.32, 0.19, 0.17), 0.06
BEARD_LOBES = [(-0.255, 0.085), (-0.085, 0.09), (0.085, 0.09), (0.255, 0.085)]   # "beard scallops": (X, r)
BEARD_LOBE_Z, LOBE_K = 2.36, 0.03     # ray height for the scallop row (front view lobe bottoms y 565-585)
LOBE_PROUD = 0.50                     # the scallops stand out of the beard by this share of their radius
LOBE_DROP = 1.25                      # lobes are rounded drops: vertical radius x this
# ---- goggle MASK + EYES. Eye centres: FRONT view x 157.5 / 248 (half span 0.455 about the face centre x 203), y 491-493
#   (z 2.98); SIDE view eye centre x 924 @ y 470 (z 3.21): the views disagree by 0.23 u, EYE_Z splits them
EYE_X, EYE_Z = 0.455, 3.08            # "eye placement"
EYE_TURN = 0.45                       # eye axis = the face normal blended this far toward straight ahead (-Y)
MASK_RING_R = 0.27                    # "mask ring size": outer radius round each eye (front ring x 130-185 about x 157)
MASK_PROUD = 0.085                    # "mask raise": how far the band stands off the face
MASK_ROUND = 0.035                    # rounded band edges
EYE_R, PUPIL_R = 0.145, 0.09          # "eye size": eye disc radius (drawn r 14-15 px) / dark pupil (drawn r 8-9 px)
EYE_TOP, EYE_DOME = 0.055, 0.035      # eye dome top above the face surface / dome height (recessed in the ring)
HIGHLIGHT_R, HIGHLIGHT_OFF = 0.045, (0.055, -0.035)   # "eye highlight": radius, offset (up, toward image-left -X)
BRIDGE_Z, BRIDGE_R = 2.95, 0.15       # mask bridge between the eyes: front view top dip y 478 (z 3.12) / bottom y 528
LOBE_Z, LOBE_R = 2.76, 0.085          # the bridge's lower central lobe (front view down to y 530, z 2.60)
STRAP_R = 0.095                       # "strap width" (half): the band from each eye ring back to its side button
BUTTON_Y, BUTTON_Z = -0.13, 2.88      # "strap button": SIDE view centre x 971 @ y 501
BUTTON_R, BUTTON_HOLE = 0.15, 0.085   # button ring outer radius / lens radius (side view ring 0.15, lens 11 px)
TAB_X, TAB_Z, TAB_LEN = 0.60, 3.30, 0.17   # "mask tabs": small pointed tabs at the ring tops (front x 145 / 263 @ y 447)
TAB_DIR = (0.35, 0.45, 1.0)           # tab lean (out, back, up)
MASK_K = 0.03
BEAK_Z, BEAK_LEN, BEAK_R = 2.66, 0.05, 0.035   # "beak": the small beak-like mouth under the mask lobe
BEAK_DIR = (0.0, -0.45, -0.89)
# ---- head SPOTS: (view, x_px, y_px, r_px) measured on the sheet; each is cast onto the egg along its view direction
#   (front -> +Y, side -> -X, back -> -Y; back view x about its egg centre x 638.5, height on the front's floor 790:
#   the back egg top y 235 = the front's 236)
SPOTS = [("front", 246.5, 320, 25), ("front", 286.5, 276, 15), ("front", 311.5, 329, 8), ("front", 155, 349, 11),
         ("front", 183, 318, 7), ("front", 148, 403, 6), ("front", 249, 401, 12),
         ("side", 1047.5, 322.5, 24), ("side", 1098.5, 276.5, 15), ("side", 1113.5, 326.5, 8), ("side", 992.5, 307.5, 9),
         ("side", 953.5, 345, 8), ("side", 1012.5, 389, 13),
         ("back", 628.5, 341, 30), ("back", 588.5, 286, 15), ("back", 558.5, 349, 10), ("back", 697, 324, 9),
         ("back", 719, 370, 12)]
SPOT_SCALE = 1.0                      # "spot size" multiplier
SPOT_UP, SPOT_DOME, SPOT_SINK = 0.006, 0.004, 0.02   # v1.1: spots are closed conforming DECALS (round at any mesh
                                      #   density): top offset / dome / buried bottom
SPOT_SEGS = (14, 20)                  # rim segments for spots under / over 0.12 u radius
# ---- TENTACLES: per kind (L side; R = mirror): azimuth (deg from the front toward +X), spine control points (r, z) in
#   the radial plane (r from the axis through Y_AX), width / thickness scales, leaf roll 'twist' (root, tip deg; + turns
#   the aqua top toward the front: the sheet draws every tentacle broad in BOTH orthos, so the leaves are rolled), a
#   tangential bow. Measured off the FRONT view (r = X / sin(az)): crown = the upper pair (tips x 22 / 420 @ y 492 / 465,
#   curling up), side = the middle pair (tips x 27 / 448 @ y 660 / 695), front = the lower outer pair (tip x 60 @ y 745;
#   SIDE view front tip x 865 @ y 730), stand = the inner pair it stands on (tips x 175 / 300 @ y 778-788; SIDE view the
#   floor tips x 970-1000)
Y_AX = 0.0
TENTACLES = {
    "crown": {"az": 133.0, "ctrl": [(0.30, 2.95), (0.95, 3.00), (1.70, 3.08), (2.30, 3.10), (2.62, 3.20), (2.80, 3.34)],
              "hw": 1.0, "ht": 0.95, "twist": (35.0, 50.0), "bow": 0.10, "seed": 1.0},
    "side": {"az": 120.0, "ctrl": [(0.30, 2.62), (0.95, 2.55), (1.65, 2.15), (2.08, 1.65), (2.25, 1.20), (2.24, 0.92)],
             "hw": 1.0, "ht": 1.0, "twist": (35.0, 55.0), "bow": -0.10, "seed": 2.0},
    "front": {"az": 48.0, "ctrl": [(0.28, 2.36), (0.80, 2.15), (1.45, 1.70), (1.85, 1.15), (2.00, 0.72), (1.98, 0.46)],
              "hw": 0.95, "ht": 1.0, "twist": (15.0, 35.0), "bow": 0.08, "seed": 3.0},
    "stand": {"az": 90.0, "ctrl": [(0.20, 2.30), (0.50, 1.95), (0.72, 1.35), (0.78, 0.75), (0.72, 0.30), (0.70, 0.03)],
              "hw": 0.95, "ht": 1.0, "twist": (40.0, 60.0), "bow": 0.0, "seed": 4.0},
}
TENT_HW = [(0.0, 0.18), (0.15, 0.34), (0.35, 0.48), (0.60, 0.45), (0.82, 0.30), (1.0, 0.05)]   # "tentacle width" (half)
TENT_HT = [(0.0, 0.17), (0.15, 0.14), (0.35, 0.10), (0.60, 0.08), (0.82, 0.06), (1.0, 0.035)]  # "tentacle thickness"
TENT_DROOP = 0.35                     # "leaf arch": the edges curl down by this share of the half width
TENT_WAVES = (3.0, 0.07)              # "soft wavy edges": waves per edge, depth (share of the width)
TENT_K = 0.14                         # tentacle roots melt into the base over this
UNDER_TAU = 0.0                       # top / underside colour line on the leaf's normal coordinate (0 = the rim)
UNDER_BIAS = 0.9                      # "cream edge": the underside wraps over the leaf's FRONT edge (W+) by this share of
                                      #   the thickness and recedes on the back edge (the sheet shows one cream edge each)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig
OWN_TAU = 0.08                        # soft part ownership for weights
TENT_BONES = 3
# ---- clips (PLACEHOLDER: movement not yet given -- OPEN-QUESTIONS N1). 24 fps; integer harmonics per loop.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BOB = 0.010                      # body rise x H (2 cyc, rising only: the standing tips never dig into the floor)
IDLE_SWAY = (1.2, 1.0)                # body roll / head nod deg
TENT_IDLE_LIFT = {"crown": (2.0, 7.0), "side": (2.0, 6.0), "front": (1.5, 4.0), "stand": (0.6, 1.2)}   # "tentacle sway"
TENT_IDLE_SWING = {"crown": (1.5, 5.0), "side": (1.5, 4.0), "front": (1.0, 3.0), "stand": (0.4, 0.8)}  # (root, tip deg)
TENT_IDLE_LAG = 0.8                   # wave lag per chain bone (rad): the sway travels root -> tip
WALK_N = 48                           # "glide loop": 2 s
WALK_BOB = 0.012                      # body rise x H per shuffle (2 per loop)
WALK_LEAN = 4.0                       # forward lean deg
WALK_RING = (3.0, 9.0)                # "shuffle ripple": a lift wave running round the skirt (root, tip deg), 2 cyc
WALK_TRAIL = (2.0, 6.0)               # "glide trail": the tentacles sweep back (root, tip deg)
WALK_STEP = 12.0                      # "standing steps": the standing pair alternately lift + reach forward (deg)
FLOOR_EPS = 1e-4

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
          "design/reference/aquaoct-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (front + side); floor z = 0 at the "
                   "standing tentacle tips", "overrides": OVERRIDES}
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

# =========================================================================== 1. body shell SDF
t_sdf = time.time()
SIDES = (("L", 1.0), ("R", -1.0))
_tl = math.radians(EGG_TILT)
EGG_U = np.array([0.0, math.sin(_tl), math.cos(_tl)])        # long axis (top tilted back)
EGG_V = np.array([0.0, math.cos(_tl), -math.sin(_tl)])       # front-back axis in the side plane
EGG_CC = np.array(EGG_C, float)
EGG_TOP = EGG_CC + EGG_U * EGG_R[1]


def sd_egg(P):
    Q = P - EGG_CC
    s = Q @ EGG_U; t = Q @ EGG_V
    f = 1.0 + EGG_TAPER * np.clip(s / EGG_R[1], -1.0, 1.0)
    L = np.stack([Q[:, 0] / f, s, t / f], 1)
    return SD.sd_ellipsoid(L, (0.0, 0.0, 0.0), EGG_R) * f


def sd_face(P):
    return SD.sd_ellipsoid(P, FACE_C, FACE_R)


def sd_base(P):
    return SD.sd_ellipsoid(P, BASE_C, BASE_R)


def sd_core(P):
    """egg + face + base, smooth-unioned in the grid's order (the anchor surface for the mask / eyes / spots)"""
    return SD.smin(SD.smin(sd_egg(P), sd_face(P), FACE_K), sd_base(P), BASE_K)


def ray_hit(fn, o, d, t_max=8.0, dt=0.005):
    """first entry of the ray o + t d into fn < 0 (bisected to 1e-7) -> (point, outward unit normal)"""
    o = np.asarray(o, float); d = unit(d)
    ts = np.arange(0.0, t_max, dt)
    v = fn(o[None] + ts[:, None] * d[None])
    i = int(np.argmax(v < 0))
    assert v[i] < 0, "ray missed the surface"
    a, b = ts[max(i - 1, 0)], ts[i]
    for _ in range(40):
        m = 0.5 * (a + b)
        if fn((o + m * d)[None])[0] < 0:
            b = m
        else:
            a = m
    p = o + 0.5 * (a + b) * d
    e = 1e-4
    g = np.array([fn((p + e * ax)[None])[0] - fn((p - e * ax)[None])[0] for ax in np.eye(3)])
    return p, unit(g)


def mir(p, sg):
    return np.array([sg * p[0], p[1], p[2]], float)


def sd_capped_cyl(P, a, b, r):
    a = np.asarray(a, float); b = np.asarray(b, float)
    ba = b - a; pa = P - a
    baba = float(ba @ ba); paba = pa @ ba
    x = np.linalg.norm(pa * baba - np.outer(paba, ba), axis=1) - r * baba
    y = np.abs(paba - baba * 0.5) - baba * 0.5
    x2 = x * x; y2 = y * y * baba
    d = np.where(np.maximum(x, y) < 0.0, -np.minimum(x2, y2), np.where(x > 0, x2, 0.0) + np.where(y > 0, y2, 0.0))
    return np.sign(d) * np.sqrt(np.abs(d)) / baba


# ---- mask anchors (cast onto the core surface)
EYE = {}
for s, sg in SIDES:
    S, n = ray_hit(sd_core, (sg * EYE_X, -4.0, EYE_Z), (0, 1, 0))
    ax = unit((1.0 - EYE_TURN) * n + EYE_TURN * np.array([0.0, -1.0, 0.0]))
    EYE[s] = {"surface": S, "normal": n, "axis": ax}
BRIDGE_S, BRIDGE_N = ray_hit(sd_core, (0.0, -4.0, BRIDGE_Z), (0, 1, 0))
LOBE_S, LOBE_N = ray_hit(sd_core, (0.0, -4.0, LOBE_Z), (0, 1, 0))
BUTTON = {}
for s, sg in SIDES:
    S, n = ray_hit(sd_core, (sg * 4.0, BUTTON_Y, BUTTON_Z), (-sg, 0, 0))
    BUTTON[s] = {"surface": S, "normal": n}
STRAPS = {}
for s, sg in SIDES:
    E, B = EYE[s]["surface"], BUTTON[s]["surface"]
    pts = []
    for tt in np.linspace(0.0, 1.0, 7):
        q = E * (1 - tt) + B * tt
        o_ = np.array([0.0, -0.15, q[2]])
        S, n = ray_hit(sd_core, o_ + unit(q - o_) * 3.0, -(q - o_))
        pts.append(S - n * (STRAP_R - MASK_PROUD))
    STRAPS[s] = pts
TABS = {}
for s, sg in SIDES:
    S, n = ray_hit(sd_core, (sg * TAB_X, -4.0, TAB_Z), (0, 1, 0))
    TABS[s] = (S - n * 0.03, S + unit(mir(TAB_DIR, sg)) * TAB_LEN)


def mask_parts(P):
    """the goggle mask: rings (holes cut) + bridge + lobe + straps + button rings + tabs, one primitive"""
    d = np.full(len(P), 1e9)
    holes = np.full(len(P), 1e9)
    for s, _ in SIDES:
        E, ax = EYE[s]["surface"], EYE[s]["axis"]
        d = SD.smin(d, sd_capped_cyl(P, E - ax * 0.16, E + ax * (MASK_PROUD - MASK_ROUND), MASK_RING_R - MASK_ROUND)
                    - MASK_ROUND, 0.02)
        holes = np.minimum(holes, sd_capped_cyl(P, E - ax * 0.4, E + ax * 0.5, EYE_R + 0.008))
        B, bn = BUTTON[s]["surface"], BUTTON[s]["normal"]
        d = SD.smin(d, sd_capped_cyl(P, B - bn * 0.14, B + bn * (MASK_PROUD - MASK_ROUND), BUTTON_R - MASK_ROUND)
                    - MASK_ROUND, 0.02)
        holes = np.minimum(holes, sd_capped_cyl(P, B - bn * 0.4, B + bn * 0.5, BUTTON_HOLE + 0.006))
        sp = STRAPS[s]
        for a, b in zip(sp[:-1], sp[1:]):
            d = SD.smin(d, SD.sd_round_cone(P, a, b, STRAP_R, STRAP_R), 0.03)
        ta, tb = TABS[s]
        d = SD.smin(d, SD.sd_round_cone(P, ta, tb, 0.06, 0.014), 0.03)
    # bridge: L eye -> the midline -> R eye (each joint sunk so the band top stands MASK_PROUD off the face)
    bc = BRIDGE_S - BRIDGE_N * (BRIDGE_R - MASK_PROUD)
    for s, _ in SIDES:
        E, n_ = EYE[s]["surface"], EYE[s]["normal"]
        d = SD.smin(d, SD.sd_round_cone(P, E - n_ * (BRIDGE_R - MASK_PROUD) + np.array([0, 0, BRIDGE_Z - EYE_Z]), bc,
                                        BRIDGE_R, BRIDGE_R), 0.03)
    d = SD.smin(d, SD.sd_sphere(P, LOBE_S - LOBE_N * (LOBE_R - MASK_PROUD), LOBE_R), 0.05)
    return np.maximum(d, -holes)


def mask_box():
    pts = [EYE[s]["surface"] for s, _ in SIDES] + [BUTTON[s]["surface"] for s, _ in SIDES] + [BRIDGE_S, LOBE_S] + \
          [p for s, _ in SIDES for p in STRAPS[s]] + [p for s, _ in SIDES for p in TABS[s]]
    A = np.array(pts)
    return A.min(0) - 0.32, A.max(0) + 0.32


def sd_beard(P):
    d = SD.sd_ellipsoid(P, BEARD_C, BEARD_R)
    return d


BEARD_LOBE_C = []
for x_, r_ in BEARD_LOBES:
    S, n = ray_hit(lambda P: SD.smin(sd_core(P), sd_beard(P), BEARD_K), (x_, -4.0, BEARD_LOBE_Z + 0.06), (0, 1, 0))
    BEARD_LOBE_C.append((S - n * r_ * (1.0 - LOBE_PROUD) + np.array([0.0, 0.0, -0.07]), r_))


def sd_lobe(P, c, r):
    return SD.sd_ellipsoid(P, c, (r, r, r * LOBE_DROP))


def sd_beard_all(P):
    d = sd_beard(P)
    for c, r in BEARD_LOBE_C:
        d = SD.smin(d, sd_lobe(P, c, r), LOBE_K)
    return d


# ---- tentacles
TENTS = []
for kind, kp in TENTACLES.items():
    for s, sg in SIDES:
        TENTS.append(FP.Tentacle("tent.%s.%s" % (kind, s), sg * kp["az"], kp["ctrl"], Y_AX, TENT_HW, TENT_HT, kp["hw"],
                                 kp["ht"], TENT_DROOP, (sg * kp["twist"][0], sg * kp["twist"][1]), sg * kp["bow"],
                                 (TENT_WAVES[0], TENT_WAVES[1], kp["seed"]), swap_edges=(sg < 0)))
        TENTS[-1].kind, TENTS[-1].side = kind, s
TNAMES = [t.name for t in TENTS]

GROUPS = ["egg", "face", "base", "beard", "mask", "tent"]
GROUP_REGION = {"egg": "head", "face": "head", "base": "mantle", "beard": "beard", "mask": "mask", "tent": "tent_top"}


def tent_cols(P):
    return np.stack([t.eval(P)["d"] for t in TENTS], 1)


def group_sdf(P):
    TC = tent_cols(P)
    cols = [sd_egg(P), sd_face(P), sd_base(P), sd_beard_all(P), mask_parts(P), TC.min(1)]
    return np.stack(cols, 1), TC


boxes = []
_egg_ext = max(EGG_R) * 1.1
boxes.append((EGG_CC - _egg_ext, EGG_CC + _egg_ext))
for t in TENTS:
    boxes.append(t.bbox(0.05))
GLO = np.min([b[0] for b in boxes], 0) - 0.05
GHI = np.max([b[1] for b in boxes], 0) + 0.05
GLO[2] = -0.06
g = SD.Grid(GLO, GHI, SDF_STEP, SDF_BAND)
g.apply(sd_egg, EGG_CC - _egg_ext, EGG_CC + _egg_ext, 0.0)
g.apply(sd_face, np.array(FACE_C) - FACE_R, np.array(FACE_C) + FACE_R, FACE_K)
g.apply(sd_base, np.array(BASE_C) - BASE_R, np.array(BASE_C) + BASE_R, BASE_K)
g.apply(sd_beard, np.array(BEARD_C) - BEARD_R, np.array(BEARD_C) + BEARD_R, BEARD_K)
for c, r in BEARD_LOBE_C:
    g.apply(lambda P, c=c, r=r: sd_lobe(P, c, r), c - r * LOBE_DROP, c + r * LOBE_DROP, LOBE_K)
for t in TENTS:
    lo_, hi_ = t.bbox(0.0)
    g.apply(lambda P, t=t: t.eval(P)["d"], lo_, hi_, TENT_K)
_mlo, _mhi = mask_box()
g.apply(mask_parts, _mlo, _mhi, MASK_K)
g.floor(0.0)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "tentacles": len(TENTS),
                 "high_bbox": [HV.min(0).round(4).tolist(), HV.max(0).round(4).tolist()],
                 "seconds": round(time.time() - t_sdf, 1)}
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
_fk = [tuple(sorted(f)) for f in LF]
_fc = {}
for k_ in _fk:
    _fc[k_] = _fc.get(k_, 0) + 1
LF = [f for f, k_ in zip(LF, _fk) if _fc[k_] == 1]            # collapse leftovers: doubled faces (a zero-volume fold)
DOUBLED = int(sum(c for c in _fc.values() if c > 1))
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_hb = HV.max(0) - HV.min(0); _lb = LV.max(0) - LV.min(0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_low_over_high": (_lb / _hb).round(4).tolist(),
                    "seconds": round(time.time() - t, 1)}
assert min(_lb / _hb) > 0.97, ("decimation folded the shell", report["retopo"])
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))
print("RETOPO", json.dumps(report["retopo"]))


# =========================================================================== 3. region fields + iso cuts on the shell
def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


SPOT_C, SPOT_N = [], []
for view, xp, yp, rp in SPOTS:
    if view == "front":
        o_, d_ = ((xp - 234.5) / 100.0, -4.0, (790 - yp) / 100.0), (0, 1, 0)
    elif view == "side":
        o_, d_ = (4.0, (xp - 984.5) / 100.0, (791 - yp) / 100.0), (-1, 0, 0)
    else:
        o_, d_ = (-(xp - 638.5) / 100.0, 6.0, (790 - yp) / 100.0), (0, -1, 0)
    S, n_ = ray_hit(sd_egg, o_, d_, t_max=10.0)
    SPOT_C.append((S, rp / 100.0 * SPOT_SCALE))
    SPOT_N.append(n_)


def tside_field(P, TC):
    j = np.argmin(TC, 1)
    out = np.zeros(len(P))
    for k, t in enumerate(TENTS):
        m = j == k
        if m.any():
            ev_ = t.eval(P[m])
            # sin(theta) - BIAS cos(theta) of the elliptical angle round the leaf section: constant along the leaf, so the
            # colour line runs parallel to the edge (v1.1 gate: the raw n'/ht field is steep at the rim -> jagged cuts)
            out[m] = (ev_["tside"] - UNDER_BIAS * ev_["wfrac"]) / np.maximum(np.hypot(ev_["tside"], ev_["wfrac"]), 1e-9)
    return out


DGR, TCL = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["tside"] = tside_field(LV, TCL)
FIELDS["z"] = LV[:, 2].copy()
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
gate_of = lambda gname: (lambda a, b: a[LAY["f_" + gname]] > -TOL and b[LAY["f_" + gname]] > -TOL)
CUTS = [("f_" + gname, 0.0, None) for gname in GROUPS if gname != "egg"]
CUTS += [("tside", UNDER_TAU, gate_of("tent"))]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array([GROUP_REGION[GROUPS[o]] for o in owner], dtype=object)
own = lambda gname: owner == GROUPS.index(gname)
shell_reg[own("tent") & (FVAL["tside"] < UNDER_TAU)] = "tent_under"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: eyes, highlights, button lenses, beak
PARTS = []
FACE = {}
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def project(pts, n):
    out = []
    for p in pts:
        loc, nrm, idx, dd = bvh_c.ray_cast(Vector(p + n * 0.4), Vector(-n), 1.2)
        if loc is None:
            loc = bvh_c.find_nearest(Vector(p))[0]
        out.append(np.array(loc))
    return np.array(out)


SPOT_KEPT, SPOT_DROPPED = [], []
for k, (c, r) in sorted(enumerate(SPOT_C), key=lambda kv: -kv[1][1]):      # largest first; drop any that would overlap
    if any(np.linalg.norm(c - SPOT_C[j][0]) < r + SPOT_C[j][1] + 0.02 for j in SPOT_KEPT):
        SPOT_DROPPED.append(k); continue
    SPOT_KEPT.append(k)
for k in sorted(SPOT_KEPT):
    c, r = SPOT_C[k]
    segs = SPOT_SEGS[1] if r >= 0.12 else SPOT_SEGS[0]
    V, F, R = FP.dome_decal(c, SPOT_N[k], r, project, SPOT_UP, SPOT_SINK, SPOT_DOME, 2, segs, "spot")
    PARTS.append(FP.Part("spot.%02d" % k, V, F, R, bone="head"))
for s, sg in SIDES:
    E, ax = EYE[s]["surface"], EYE[s]["axis"]
    V, F, R = FP.eye_dome(E, ax, EYE_R, PUPIL_R, EYE_TOP, EYE_DOME, 0.12)
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    a_, up_, side_ = FP.frame_from_axis(ax)
    xm = unit(np.array([-1.0, 0.0, 0.0]) - ax * float(ax[0] * -1.0))      # toward image-left (-X), across the eye
    hc = E + ax * (EYE_TOP - 0.004) + up_ * HIGHLIGHT_OFF[0] + xm * (-HIGHLIGHT_OFF[1])
    V, F, R = FP.lens_dome(hc, ax, HIGHLIGHT_R, 0.012, 0.02, "eye_highlight", segs=12)
    PARTS.append(FP.Part("highlight." + s, V, F, R, bone="head"))
    B, bn = BUTTON[s]["surface"], BUTTON[s]["normal"]
    V, F, R = FP.lens_dome(B + bn * (MASK_PROUD * 0.45), bn, BUTTON_HOLE, 0.03, 0.12, "button", segs=16)
    PARTS.append(FP.Part("button." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": E.round(4).tolist(), "axis": ax.round(4).tolist()}
    FACE["button." + s] = {"surface": B.round(4).tolist(), "normal": bn.round(4).tolist()}
BEAK_S, BEAK_N = ray_hit(lambda P: SD.smin(sd_core(P), sd_beard_all(P), BEARD_K), (0.0, -4.0, BEAK_Z), (0, 1, 0))
V, F, R = FP.beak(BEAK_S - BEAK_N * 0.01, unit(BEAK_DIR), BEAK_LEN, BEAK_R, 0.06)
PARTS.append(FP.Part("beak", V, F, R, bone="head"))
FACE["beak"] = BEAK_S.round(4).tolist()

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
HV_s = HV - SHIFT
REG = ["head", "spot", "mantle", "beard", "mask", "beak", "eye_iris", "eye_pupil", "eye_highlight", "button",
       "tent_top", "tent_under"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.97 + 0.06 * j


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                  # opaque closed body: single-sided (glTF doubleSided false)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


def glow_check(pal):
    """the sheet draws no glow: every region zero Glow"""
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    return {"skin": pal["skin"], "glowing_regions": lit, "no_glow": not lit}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
report["glow"] = glow_check(pal_default)
assert report["glow"]["no_glow"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, jitter(VALL, FALL))
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head-base centre -> the mask bridge front on the midline
anchor = np.array([0.0, Y_AX, BRIDGE_Z]) - SHIFT
landmark = BRIDGE_S + BRIDGE_N * MASK_PROUD - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head-base axis (0, Y_AX) at the bridge height -> the mask bridge front on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["spots"] = {"decals": [{"view": v_[0], "px": [v_[1], v_[2]], "r_px": v_[3], "centre": (c - SHIFT).round(4).tolist(),
                               "kept": k in SPOT_KEPT} for k, (v_, (c, r)) in enumerate(zip(SPOTS, SPOT_C))],
                   "kept": len(SPOT_KEPT), "dropped_overlapping": [SPOTS[k] for k in SPOT_DROPPED]}

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
    key = {"eye": "eyes", "highlight": "eye_highlights", "button": "button_lenses", "beak": "beak",
           "spot": "spot_decals"}[p.name.split(".")[0]]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
_shell_reg_tris = {}
for r_ in sorted(set(shell_reg)):
    _shell_reg_tris[r_] = int((shell_reg == r_).sum())
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "shell_by_region": _shell_reg_tris, "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The eight wide leaf tentacles carry most of the "
                            "surface (and the budget); the face reads from the iso-cut mask band + closed eye domes." %
                            tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, eyes, highlights, button lenses, beak, spot decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "sheet_front": {"height_px": 555, "width_px": 426},
                                             "sheet_side": {"height_px": 552, "width_px": 323}},
                     "egg_share_of_height": round(float(2.0 * math.hypot(EGG_R[1] * math.cos(_tl), EGG_R[2] * math.sin(_tl))
                                                        / H), 4),
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
low["conquest_source"] = "from scratch: design/reference/aquaoct-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (no glow regions)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_crown = [t for t in TENTS if t.name == "tent.crown.L"][0]
_tc = _crown.C
low["conquest_focus"] = json.dumps({
    "face": [(np.array([-0.85, -1.15, 2.05]) - SHIFT).tolist(), (np.array([0.85, -0.35, 3.65]) - SHIFT).tolist()],
    "head_spot": [(EGG_CC - np.array([1.2, 1.2, 0.2]) - SHIFT).tolist(), (EGG_TOP + np.array([1.2, 0.6, 0.05]) - SHIFT).tolist()],
    "mask_eye": [(np.array([0.1, -1.05, 2.55]) - SHIFT).tolist(), (np.array([0.85, 0.05, 3.6]) - SHIFT).tolist()],
    "tentacle": [(_tc.min(0) - 0.3 - SHIFT).tolist(), (_tc.max(0) + 0.3 - SHIFT).tolist()],
    "views": {"face": [0.0, 5.0], "head_spot": [25.0, 25.0], "mask_eye": [80.0, 3.0], "tentacle": [60.0, 15.0]},
    "axis_xy": [float(SHEET_AXIS_X - SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "face")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
NO_HIGH = ("eye.", "highlight.", "button.")     # v1.1 gate (mask-rim speckles): the eye / button domes sit 0.008 inside
                                                # their ring holes, closer than the 0.02 cage, so the ring walls baked the
                                                # domes' normals. Recessed domes stay out of the high (their own bake would
                                                # be flat anyway: low == high) and their texels are set flat / AO 1.
for name, V, F, R, p in ISL[1:]:
    if name.startswith(NO_HIGH):
        continue
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
nt_ = MAT_BODY.node_tree
tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
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
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
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
_fo, FRANGE = 0, {}
for name, V, F, R, p in ISL:
    FRANGE[name] = (_fo, _fo + len(F)); _fo += len(F)
nohigh_faces = np.zeros(nf, bool)
for name, (fa_, fb_) in FRANGE.items():
    if name.startswith(NO_HIGH):
        nohigh_faces[fa_:fb_] = True
body_faces = ~nohigh_faces
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
nh_n = texels_of(nohigh_faces, RN) & ~alltex_n
nh_a = texels_of(nohigh_faces, RA) & ~alltex_a
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
px[~cov_n | nh_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a | nh_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["recessed_domes_flat"] = {"parts": list(NO_HIGH), "texels_normal": int(nh_n.sum()), "texels_ao": int(nh_a.sum()),
                                 "rule": "kept out of the high; texels set flat normal / AO 1 (coverage % excludes them)"}
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


BONES = [("body", P_((0, Y_AX, 2.12)), P_((0, Y_AX, 2.90)), "root"),
         ("head", P_((0, Y_AX, 2.90)), P_(EGG_TOP), "body")]
CHAIN = {}
for t_ in TENTS:
    v_ = sd_core(t_.C)
    i0 = int(np.argmax(v_ > 0))                                   # first spine station outside the head / base
    s_surf = float(t_.arc[i0])
    ks = np.linspace(s_surf, t_.L, TENT_BONES + 1)
    pts = [np.array([np.interp(x, t_.arc, t_.C[:, k]) for k in range(3)]) for x in ks]
    for k in range(TENT_BONES):
        BONES.append(("%s.%d" % (t_.name, k), P_(pts[k]), P_(pts[k + 1]), "body" if k == 0 else "%s.%d" % (t_.name, k - 1)))
    CHAIN[t_.name] = {"s_surf": s_surf, "length": t_.L}
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
    e.use_connect = nm[-1] in "12" and nm.startswith("tent.")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
GD, TCW = group_sdf(Pw)
GD8 = np.hstack([GD[:, :5], TCW])                                  # egg, face, base, beard, mask, 8 tentacles
Wg = np.exp(-(GD8 - GD8.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
Ws = np.zeros((b_ - a_, len(DEFORM)))
Ws[:, J["head"]] += Wg[:, 0] + Wg[:, 1] + Wg[:, 4]
Ws[:, J["body"]] += Wg[:, 2] + Wg[:, 3]
TENT_W = {}
for k, t_ in enumerate(TENTS):
    col = Wg[:, 5 + k]
    ev = t_.eval(Pw)
    s0 = CHAIN[t_.name]["s_surf"]
    Lc = t_.L - s0
    Wv = K.vine_weights(np.clip(ev["s"] - s0, -0.3, Lc), Lc, TENT_BONES, parent_s0=-0.15)
    Ws[:, J["body"]] += col * Wv[:, 0]
    for kb in range(TENT_BONES):
        Ws[:, J["%s.%d" % (t_.name, kb)]] += col * Wv[:, kb + 1]
    TENT_W[t_.name] = {"length": round(t_.L, 4), "root_arc": round(s0, 4), "dominant_verts": int((np.argmax(Wg, 1) == 5 + k).sum())}
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
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
                  "tentacles": TENT_W,
                  "rule": "shell: soft SDF ownership (tau %.2f): egg + face + mask -> head, base + beard -> body, each "
                          "tentacle -> rigkit.vine_weights along its own spine from where it leaves the base (the root "
                          "blends into body); eyes, highlights, button lenses, beak rigid on head" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame with no action bound (pass 1, floor
#      guard lifting the body if any vertex dips below z 0), keyed from the stored channels (pass 2, frame N+1 = frame 1),
#      measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]
KEY_BONES = DEFORM
ZUP = np.array([0.0, 0.0, 1.0]); XAX = np.array([1.0, 0.0, 0.0]); YAX = np.array([0.0, 1.0, 0.0])
RING = sorted(TENTS, key=lambda t_: (t_.az % TAU))                # round the skirt from the front through +X
RING_IX = {t_.name: i for i, t_ in enumerate(RING)}
TPH = {t_.name: 2.39996 * i for i, t_ in enumerate(TENTS)}         # golden-angle phases: no two tentacles in step


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def world_rot(rots):
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(axis)) @ Rw
    return Rw


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ world_rot(rots) @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def tent_rots(t_, k, lift, swing, trail=0.0):
    """lift > 0 lowers the tentacle in its radial plane (about its tangential axis), swing > 0 turns it toward +X side
    mirror-correct (about Z, signed by side), trail > 0 sweeps it back (about world X)"""
    sg = 1.0 if t_.side == "L" else -1.0
    return [(t_.TG, lift), (ZUP, sg * swing), (XAX, trail)]


def idle_pose(u):
    P = {}
    rise = HH * IDLE_BOB * 0.5 * (1.0 - math.cos(TAU * 2 * u))
    P["body"] = ((0.0, 0.0, rise), [(YAX, IDLE_SWAY[0] * math.sin(TAU * u + 0.4))], None)
    P["head"] = (None, [(XAX, IDLE_SWAY[1] * math.sin(TAU * 2 * u - 0.9))], None)
    for t_ in TENTS:
        ph = TPH[t_.name]
        la, sa = TENT_IDLE_LIFT[t_.kind], TENT_IDLE_SWING[t_.kind]
        for k in range(TENT_BONES):
            lift = K.vine_wave(u, k, TENT_BONES, la[0], la[1], TENT_IDLE_LAG, 1, ph)
            swing = K.vine_wave(u, k, TENT_BONES, sa[0], sa[1], TENT_IDLE_LAG, 2, ph + 1.3)
            P["%s.%d" % (t_.name, k)] = (None, tent_rots(t_, k, lift, swing), None)
    return P, {"lift": rise}


def walk_pose(u):
    """in-place tentacle-shuffle glide: two shuffles per loop. The body rises with each shuffle and leans into the
    glide; a lift wave runs round the skirt (2 cycles); every tentacle trails back; the standing pair steps
    alternately (lift + reach forward, then plant)."""
    P = {}
    rise = HH * WALK_BOB * 0.5 * (1.0 - math.cos(TAU * 2 * u))
    P["body"] = ((0.0, 0.0, rise), [(XAX, WALK_LEAN), (YAX, 1.2 * math.sin(TAU * u))], None)
    P["head"] = (None, [(XAX, -0.6 * WALK_LEAN + 1.0 * math.sin(TAU * 2 * u - 1.2))], None)
    for t_ in TENTS:
        ring_ph = TAU * RING_IX[t_.name] / len(TENTS)
        for k in range(TENT_BONES):
            fk = k / max(TENT_BONES - 1, 1)
            lift = K.vine_wave(u, k, TENT_BONES, WALK_RING[0], WALK_RING[1], 0.7, 2, -2.0 * ring_ph)
            trail = WALK_TRAIL[0] + (WALK_TRAIL[1] - WALK_TRAIL[0]) * fk
            if t_.kind == "stand":
                st = math.sin(TAU * u + (0.0 if t_.side == "L" else math.pi))
                step = max(0.0, st) ** 2                              # swing half: lift + reach forward
                lift = -WALK_STEP * step * (1.0 if k == 0 else 0.5)
                trail = WALK_TRAIL[0] * (1.0 - step) - 0.6 * WALK_STEP * step * (1.0 if k == 0 else 0.3)
            P["%s.%d" % (t_.name, k)] = (None, tent_rots(t_, k, lift, 0.0, trail), None)
    return P, {"lift": rise}


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def apply_pose(P, guard_z):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        loc, rots, sc = P.get(bn, (None, [], None))
        dw = np.zeros(3) if loc is None else np.asarray(loc, float)
        if bn == "body":
            dw = dw + np.array([0.0, 0.0, guard_z])
        pb.location = local_loc(bn, dw)
        pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
        pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)


def bake_clip(name, N, pose_fn):
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames, guard, info = [], [], []
    for f in range(N):
        P, inf = pose_fn(f / N)
        gz = 0.0
        for it in range(6):
            apply_pose(P, gz)
            bpy.context.view_layer.update()
            mz = float(eval_coords(low)[:, 2].min())
            if mz >= -1e-6:
                break
            gz += -mz + FLOOR_EPS
        frames.append({bn: (rig.pose.bones[bn].location.copy(), rig.pose.bones[bn].rotation_quaternion.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
        guard.append(gz); info.append(inf)
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    rows = []
    for f in range(N + 1):
        fr = frames[f % N]
        for bn in KEY_BONES:
            pb = rig.pose.bones[bn]
            pb.location, pb.rotation_quaternion, pb.scale = fr[bn]
            for ch in ("location", "rotation_quaternion", "scale"):
                pb.keyframe_insert(ch, frame=f + 1)
            rows.append(list(fr[bn][0]) + list(fr[bn][1]) + list(fr[bn][2]))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["loop_seconds"] = N / K.FPS
    rig.animation_data.action = None
    return act, rows, guard, info


CLIPS = {"idle": (IDLE_N, idle_pose), "walk": (WALK_N, walk_pose)}
NEW_ACTS, key_rows, GUARD, INFO = {}, [], {}, {}
t_clip = time.time()
for cn, (N, fn) in CLIPS.items():
    NEW_ACTS[cn], rows, GUARD[cn], INFO[cn] = bake_clip(cn, N, fn)
    key_rows += rows
DIG["keys"] = sha(np.array(key_rows))
TIPB = {t_.name: "%s.%d" % (t_.name, TENT_BONES - 1) for t_ in TENTS}
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
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
                     "tips": {n_: list(pbs[b_].tail) for n_, b_ in TIPB.items()},
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - REST_H["body"][2]
    tips = {n_: np.array([r["tips"][n_] for r in R_]) for n_ in TIPB}
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "lowest_point_range": [round(float(g_("minz").min()), 4), round(float(g_("minz").max()), 4)],
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "tip_p2p": {n_: round(float(np.linalg.norm(v.max(0) - v.min(0))), 4) for n_, v in tips.items()}}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["clip_seconds"] = round(time.time() - t_clip, 1)
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("aquaoct v1: root > body > head; body > tent.{crown,side,front,stand}.{L,R}.{0,1,2} "
                       "(8 vine chains x 3 bones)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (movement not yet given by the artist, OPEN-QUESTIONS N1): idle = tentacle "
                               "sway (4 s: de-synced root->tip waves per tentacle, a small body rise + roll, head nod); "
                               "walk = in-place tentacle-shuffle glide (2 s: forward lean, a lift wave running round the "
                               "skirt, tentacles trailing back, the standing pair stepping alternately; root still)")
low["conquest_locomotion"] = "walker: in-place tentacle-shuffle glide (the game glides the unit)"
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
              "structure": "armature identity + skinned 'aquaoct' (one opaque body material); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief 2026-10-09)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
