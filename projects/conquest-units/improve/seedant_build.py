"""Seedant -- a potted plant creature, from scratch (firefly pattern: SDF organic shell + closed parametric parts), one
headless run.

    blender --background --factory-startup --python improve/seedant_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + material only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNIT: Seedant (artist reference sheet)" + the sheet
design/reference/seedant-character-sheet.webp (intake: design/reference/seedant/intake.json). Artist (verbatim): "create the
model for this reference it should follow the pattern and design done for the firefly jet creature we previously did".

UNITS: sheet units -- 1 unit = 100 px of the sheet's orthographic views. Front view: z = (746 - y_px) / 100,
x = (x_px - 219) / 100; side view (camera on +X, the face looks image-left = -Y): z = (743 - y_px) / 100,
y = (x_px - 958) / 100. Natural proportions; cell fit is report-only (scale policy 2026-09-25).

Pipeline (firefly order, BUILD_CONTRACT.md):
  1. SHELL = one SDF: the green gumdrop body (head + body one piece, flat-cut inside the soil) smooth-unioned with the
     two LEAF ARMS (flattened leaf-lobes wrapping the pot: oriented ellipsoids along the rim ellipse x a path that leaves the body at rim height, rides OVER the pot
     rim lip and hangs down the outer wall). Marching tets at SDF_STEP = the HIGH; collapse decimation = the LOW.
  2. REGIONS on the low shell: body / arm by part ownership (+ the optional pale highlight chip), iso-cut boundaries.
  3. PARTS (seedant_parts.py, all closed): the POT (one oval lathe: foot, tapered wall, rolled rim band, worn lip,
     inner wall, soil disc), the two RED LEAVES (lens blade folded on the midrib, sheared vein grid), the SPROUT CURL
     and two ARM-TIP CURLS (tapering tubes seated on the SDF), the FACE (closed conforming decals ray-projected along
     the front view onto the low shell: domed black eyes + white glints, the open smile + pink tongue).
  4. palette (palettes.py; no emission, opaque), Smart UV, bake normal + AO from HIGH (+ parts).
  5. RIG root > pot > body > head; head > sprout.0,1 / leaf.{L,R}.0,1,2 / eye.{L,R}; body > arm.{L,R}.0,1,2.
     Clips idle + walk (PLACEHOLDER, artist question S1). glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K           # noqa: E402  (read-only use)
import palettes as PAL       # noqa: E402  (read-only use)
import magmoo_sdf as SD      # noqa: E402  (read-only use)
import seedant_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, floor z = 0 at the pot base)
UNIT = "seedant"
CHAR_ID = "seedant"                   # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit (review-log 2026-10-09)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.015                      # "surface finish": SDF sampling step of the shell (the bake high)
SDF_BAND = 0.18                       # must exceed ARM_BLEND: a clipped far value inside a smooth-union box must
                                      #   not touch the surface (0.08 left box-edge seams on the face at x +-0.27)
BODY_TRIS = 6000                      # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- pot: ONE closed lathe, (r, z) from the base pole up the outside, over the lip, down the inside, the soil to the
# axis. r = the FRONT-view half width (sheet front, measured per 10 px row: foot y 736-746, wall 1.20 @ y 720 -> 1.50 @
# y 590, rim band 1.68 @ y 570 -> 1.83 @ y 490, rim top y 476); the side view's pot is 0.70-0.76 of the front width at
# every height (side: rim 1.39 @ y 500, wall 1.09 @ y 600, 0.89 @ y 700) while the body is NOT narrower there -> the pot
# is OVAL in plan: Y radius = POT_DEPTH x X radius. Set 1.0 for a round pot.
POT_DEPTH = 1.0                       # "pot roundness": side width / front width. Artist 2026-10-09: "round pot" (v1.2 was
                                      #   the sheet-measured oval 0.74; the sheet side view draws 0.70-0.76)
POT_SEG = 64                          # "pot smoothness": segments around
POT_PROFILE = [(0.0, 0.0), (0.92, 0.0), (1.05, 0.03), (1.12, 0.10), (1.16, 0.20), (1.205, 0.32),         # foot
               (1.255, 0.46), (1.35, 0.86), (1.45, 1.26), (1.49, 1.46), (1.505, 1.54),                   # tapered wall
               (1.56, 1.605), (1.63, 1.63), (1.665, 1.67), (1.69, 1.75),                                  # band undercut
               (1.71, 1.86), (1.75, 2.06), (1.79, 2.26), (1.815, 2.44), (1.83, 2.56), (1.825, 2.62),     # rolled band
               (1.80, 2.67), (1.76, 2.70), (1.69, 2.72), (1.62, 2.71), (1.575, 2.68),                     # worn top lip
               (1.55, 2.62), (1.535, 2.50), (1.52, 2.42), (1.50, 2.40),                                   # inner wall
               (1.0, 2.41), (0.5, 2.42), (0.0, 2.42)]                                                     # soil disc
POT_REG = (["pot"] * 10 + ["pot_rim"] * 10 + ["pot_lip"] * 6 + ["pot_inner"] * 3 + ["soil"] * 3)
RIM_TOP = 2.72                        # rim lip top (sheet front y 474-476)
RIM_MID_R = 1.69                      # the lip's centre line (front-view X radius; Y = x POT_DEPTH)
POT_JITTER = 0.07                     # "clay texture": per-face tone jitter +- on the pot regions (subtle texture)
# ---- body: one gumdrop (head + body, no neck), half-WIDTH profile (z, r) from the sheet front view (y 338 top; per 10
# px: y 350 0.33, y 380 0.63, y 420 0.80, y 430 0.85; below y 440 the arms cover it); depth = width x BODY_DEPTH
BODY_FLOOR = 2.20                     # the body's bottom pole (rounded), buried in the soil (soil top 2.40-2.42)
BODY_PROFILE = [(BODY_FLOOR, 0.0), (2.24, 0.62), (2.34, 0.84), (2.50, 0.91), (2.75, 0.93), (3.00, 0.895), (3.16, 0.85),
                (3.26, 0.81), (3.36, 0.78), (3.46, 0.745), (3.56, 0.69), (3.66, 0.63), (3.76, 0.565), (3.86, 0.49),
                (3.96, 0.36), (4.03, 0.22), (4.07, 0.10), (4.085, 0.0)]
BODY_DEPTH = 0.95                     # "body depth": side depth / front width (side view: front edge y -0.83, back +0.98 @ y 460)
BODY_Y = 0.05                         # body centre sits this far back (side view centre x 965 vs axis 958)
# ---- leaf arms (gate 2026-10-09: PLUMP FLATTENED LEAF-LOBES, not tubes): each arm is a lobe that WRAPS the pot. A
# centre path in the vertical plane through the pot axis at azimuth ARM_AZ (deg from the front toward each side, on the
# rim ellipse); at every station the lobe spreads laterally ALONG THE RIM ELLIPSE (each lateral sample sits at the same
# offset from the rim at its own azimuth), so it lies flattened against the wall: thin along the wall normal, wide along
# it. Path points (a, b, lateral half width (arc length), half thickness): a = outward along the rim's horizontal
# normal from the lip centre line, b = up from RIM_TOP. The root is buried in the body's lower side at rim height.
ARM_AZ = 44.0                         # "arm placement": the sheet views disagree (front: lobe x 0.49..1.50 = az 17..46;
                                      #   side: lobe y -0.71..+0.24 = az 57..100). Artist 2026-10-09 "plumper": 50 -> 44 with
                                      #   the lobes widened (front reads fuller; side offset grows a little, accepted)
# ARM_PATH v1.3 "plumper" (artist 2026-10-09): from the lip on, lateral half width x 1.45 and half thickness x 1.18 vs
# v1.2 (previews x1.25/1.10 vs x1.45/1.18 against the sheet front: x1.45 matches its broad mittens)
ARM_PATH = [(-0.62, 0.12, 0.22, 0.2), (-0.36, 0.17, 0.25, 0.17),
            (-0.08, 0.175, 0.3915, 0.1652), (0.15, 0.1, 0.4205, 0.1534),
            (0.26, -0.12, 0.4495, 0.1534), (0.25, -0.4, 0.3915, 0.1475),
            (0.19, -0.66, 0.2465, 0.1298), (0.12, -0.86, 0.087, 0.0944)]
ARM_NV, ARM_NL = 22, 5                # lobe ellipsoids: stations along the path / lateral samples per side
ARM_EDGE_THIN = 0.55                  # "lobe edge": the lobe thins toward its side edges by this share (lens section)
ARM_K = 0.07                          # smoothing inside the lobe. Orchestrator gate 2026-10-09: 0.03 left the 22 x 11
                                      #   ellipsoid tiling visible as a quilted cell pattern (baked into the normal map);
                                      #   previews at 0.03 / 0.07 / 0.11: 0.07 melts it, 0.11 starts inflating the lobe
ARM_BLEND = 0.14                      # "root blend": smooth union arm <-> body
ARM_STANDOFF = 0.062                  # "arm rests on the rim": every lobe ellipsoid stands off the pot by this along its own
                                      #   outward normal (compensates smooth-union swell + lobe thickness; does not thin it).
                                      #   0.03 for v1.2 (k 0.07 swell 0.0297); v1.3 plumper lobes (thickness x 1.18) measured
                                      #   -0.032 at 0.03 -> previews 0.062: +0.0055 low / +0.0011 high, 0.068: +0.0107 / +0.0073
# ---- red leaves (sheet front: tips x +-0.83 @ y 250, outer edge 1.27 @ y 360; side: tip y +0.70, blade y 0.25..1.10
# @ y 350, base y 0.68 @ y 520): midrib knots for the +X leaf (mirrored for -X), base inside the body
LEAF_MIDRIB = [(0.62, 0.55, 2.95), (0.76, 0.58, 3.40), (0.85, 0.60, 3.95), (0.88, 0.64, 4.50), (0.83, 0.72, 5.01)]
LEAF_YAW = 52.0                       # "leaf facing": the blade spans inner-back -> outer-front (reads ~0.8 wide front AND side)
LEAF_HW = 0.60                        # "leaf width" (half, at the widest, ~40 % up)
LEAF_SHAPE = 0.6                      # "leaf belly": widest point (s^LEAF_SHAPE = 0.5 -> s 0.31; lower = wider low down)
LEAF_THICK = 0.035                    # lens half-thickness at the midrib
LEAF_FOLD = 0.22                      # "midrib fold": edges rise this x |w| x half width
LEAF_VEINS = [0.24, 0.36, 0.48, 0.60, 0.72, 0.84]   # "side veins": row positions along the blade
LEAF_VEIN_W, LEAF_VEIN_REACH, LEAF_SHEAR = 0.013, 0.86, 1.6   # vein row width / how far out / vein slant toward the tip
LEAF_MID_W = 0.05                     # midrib strip half width (relative)
LEAF_ROWS = 22
LEAF_COLS = [0.2, 0.4, 0.6, 0.8, 0.92, 1.0]
LEAF_JITTER = 0.03
# ---- sprout curl (sheet front: x -0.20..0.18, z 3.80..4.24; side: a loop at y -0.56..-0.12): a tube path in a
# vertical plane through SPROUT_C yawed SPROUT_YAW toward the unit's left; 2D points (in-plane x, z) from the base
# (buried in the head) up the left, over the top, winding in to the fiddlehead centre
SPROUT_C = (0.0, -0.33, 4.02)
SPROUT_YAW = 35.0                     # "sprout turn": 0 = the curl faces straight front (a line from the side)
SPROUT_PTS = [(0.02, -0.30), (-0.08, -0.20), (-0.17, -0.05), (-0.15, 0.10), (-0.05, 0.18), (0.08, 0.17), (0.15, 0.08),
              (0.13, -0.02), (0.05, -0.05), (0.02, 0.01), (0.05, 0.04)]
SPROUT_R = (0.078, 0.018)             # "sprout thickness": tube radius base -> tip
# ---- arm-tip curls (sheet front: curl centre x +-1.10 @ y 549, ~50 px across): spirals on the arm's outer surface
CURL_S = 0.86                         # where on the arm (arc fraction shoulder -> tip)
CURL_R = (0.125, 0.022)               # spiral radius outer -> inner
CURL_TURNS = 1.45
CURL_TUBE = (0.034, 0.016)            # curl tube radius outer -> inner
CURL_SEAT = 0.45                      # tube centre sits this x its radius above the arm surface (seated, not floating)
# ---- face: closed decals projected along the front view (sheet front px, face centre x 224.75 drawn 0.06 right of the
# axis -- placed on the midline here): eyes centre y 418 -> z 3.28, x +-0.233; 17 x 43 px ovals
EYE_X, EYE_Z, EYE_R = 0.233, 3.28, (0.080, 0.212)
EYE_DOME = 0.020                      # "eye gloss dome"
GLINT_DX, GLINT_DZ, GLINT_R = 0.015, 0.055, (0.033, 0.085)   # white highlight (towards the face centre, high)
MOUTH_Z, MOUTH_W = 2.98, 0.075        # mouth centre / half width (sheet y 441..457, x 218..233)
MOUTH_TOP, MOUTH_TOP_MID, MOUTH_BOT = 0.068, 0.045, 0.088   # smile: corners up, middle lower; round bottom
TONGUE_Z, TONGUE_R = 2.935, (0.046, 0.030)
DECAL_LIFT, DECAL_SINK = 0.006, 0.02  # decal edge height above the shell / depth below it
DECAL_SEG = 40
# ---- optional pale highlight chip (swatch 201,222,148) on the upper head
HIGHLIGHT = True
HIGHLIGHT_C, HIGHLIGHT_R = (-0.36, -0.42, 3.74), (0.20, 0.30, 0.13)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_FLOOR = {"body": 0.45, "body_shade": 0.45, "arm": 0.45, "curl": 0.5, "sprout": 0.5, "leaf": 0.45, "leaf_vein": 0.45,
            "leaf_midrib": 0.45, "eye": 0.7, "eye_glint": 0.7, "mouth": 0.7, "tongue": 0.7, "pot": 0.45, "pot_rim": 0.4,
            "pot_lip": 0.4, "pot_inner": 0.3, "soil": 0.3}   # "AO floor" per region (house style: nothing bakes to
                                      #   black grime; v1 had ~5 % of AO texels at 0.0, see report bake.ao_classes)
# ---- rig
NECK_Z = (2.85, 3.30)                 # body / head weight blend band on the shell
LEAF_BONES, ARM_BONES, SPROUT_BONES = 3, 3, 2
# ---- clips (PLACEHOLDER: artist question S1 -- movement intent not given). 24 fps; integer cycles per loop.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_HEAD_ROLL = (2.2, 0.6)           # "sway": head roll deg at 1 cyc + 3 cyc
IDLE_HEAD_PITCH = 1.3                 # head nod deg at 2 cyc
IDLE_HEAD_YAW = 1.5                   # head look-around deg at 1 cyc
IDLE_BREATH = (0.006, 0.012)          # "breathing": body / head uniform scale growth (>= 1 only: arms move AWAY from the pot)
IDLE_LEAF = {"X": (2.0, 6.0, 2, 0.7), "Y": (1.5, 5.0, 1, 0.6), "B": (2.0, 7.0, 3, 0.9)}   # "leaf rustle" (root, tip, cyc, lag)
LEAF_PHASE = {"L": 0.0, "R": 2.1}     # de-synced chains (never a mirrored flap)
IDLE_TAP = (3.0, 8.0)                 # "arm tap": outward lift deg on arm.1 / arm.2 (always >= 0: never into the wall)
TAP_U = {"L": (0.14, 0.26), "R": (0.60, 0.72)}   # tap times (loop fraction): a double tap per side
TAP_W = 0.065                         # tap duration (loop fraction, half width)
IDLE_SPROUT = (3.0, 8.0, 2, 0.8)
BLINK_U, BLINK_W, BLINK_MIN = 0.45, 0.035, 0.08   # one blink: when / half width / eye height at the closed frame
WALK_N = 48                           # "hop loop": 2 s
HOPS = 2                              # hops per loop (1 Hz)
HOP_H = 0.30                          # "hop height" (~6 % of the height)
HOP_CONTACT = 0.30                    # share of each hop on the ground
SQUASH, STRETCH = 0.10, 0.08          # pot squash on land / stretch at take-off (volume kept: sides 1 / sqrt(h))
WALK_LEAN = 5.0                       # forward lean in the air deg (scaled by the lift: the base never dips below z 0)
WALK_HEAD_LAG = 4.0                   # head pitch follow-through deg
WALK_LEAF = {"X": (4.0, 10.0, 2, 0.8), "B": (1.5, 4.0, 2, 0.9)}
WALK_ARM = (3.0, 7.0)                 # arm follow-through outward lift on landing deg (bone 1 / bone 2)

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
          "design/reference/seedant-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px of the reference sheet (orthographic views); floor z = 0 at the pot base",
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

# =========================================================================== 1. shell SDF (body + leaf arms)
t_sdf = time.time()
_pz = np.array([p[0] for p in BODY_PROFILE]); _pr = np.array([p[1] for p in BODY_PROFILE])
_dense = SD.catmull(np.stack([np.zeros_like(_pz), _pz, _pr], 1), 12)
BZ, BR = _dense[:, 1], np.clip(_dense[:, 2], 0.0, None)
order_ = np.argsort(BZ, kind="stable"); BZ, BR = BZ[order_], BR[order_]


def body_r(z):
    return np.interp(z, BZ, BR, left=0.0, right=0.0)


def sd_body(P):
    d = np.hypot(P[:, 0], (P[:, 1] - BODY_Y) / BODY_DEPTH) - body_r(P[:, 2])
    return np.maximum(d, np.maximum(BZ[0] - P[:, 2], P[:, 2] - BZ[-1]))


def rim_point(t_deg, sg, r=RIM_MID_R):
    """the rim ellipse at azimuth parameter t (deg from the front toward side sg) -> (point xy, outward normal, tangent)"""
    t = math.radians(t_deg)
    p = np.array([sg * r * math.sin(t), -r * POT_DEPTH * math.cos(t)])
    n = unit([sg * math.sin(t) / 1.0, -math.cos(t) / POT_DEPTH])
    tg = unit([sg * math.cos(t), POT_DEPTH * math.sin(t)])
    return p, n, tg


def ell_speed(t_deg, r=RIM_MID_R):
    t = math.radians(t_deg)
    return math.hypot(r * math.cos(t), r * POT_DEPTH * math.sin(t)) * math.pi / 180.0     # arc length per degree


ARMS = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    pxy, nxy, txy = rim_point(ARM_AZ, sg)
    Rc = np.array([pxy[0], pxy[1], RIM_TOP])
    n_out = np.array([nxy[0], nxy[1], 0.0])
    S_ax = np.array([txy[0], txy[1], 0.0])
    ab = SD.catmull(np.array([(a, 0.0, b) for a, b, _, _ in ARM_PATH]), 10)
    al = SD.arclen(ab)
    knot_s = np.array([al[min(i * 10, len(al) - 1)] for i in range(len(ARM_PATH))])
    tt = np.linspace(0.0, al[-1], ARM_NV)
    A2 = np.interp(tt, al, ab[:, 0]); B2 = np.interp(tt, al, ab[:, 2])
    dA = np.gradient(A2); dB = np.gradient(B2)
    HW = np.interp(tt, knot_s, [p[2] for p in ARM_PATH])
    HT = np.interp(tt, knot_s, [p[3] for p in ARM_PATH])
    sp_v = float(al[-1]) / (ARM_NV - 1)
    ell = []
    for i in range(ARM_NV):
        for j in range(-ARM_NL, ARM_NL + 1):
            u = j / ARM_NL
            td = ARM_AZ + u * HW[i] / ell_speed(ARM_AZ)
            pj, nj, tj = rim_point(td, sg)
            nj3 = np.array([nj[0], nj[1], 0.0]); L3 = np.array([tj[0], tj[1], 0.0])
            c = np.array([pj[0], pj[1], RIM_TOP]) + nj3 * A2[i] + np.array([0, 0, 1.0]) * B2[i]
            T3 = unit(nj3 * dA[i] + np.array([0, 0, 1.0]) * dB[i])
            Q3 = unit(np.cross(T3, L3)); Q3 = Q3 if np.dot(Q3, nj3) + Q3[2] >= 0 else -Q3
            rl = max(1.6 * HW[i] / ARM_NL, 0.04)
            ht = HT[i] * (1.0 - ARM_EDGE_THIN * u * u)
            c = c + Q3 * ARM_STANDOFF
            ell.append((c, (rl, max(1.5 * sp_v, ht), ht), np.stack([L3, T3, Q3])))
    C = np.array([Rc + n_out * a_ + np.array([0, 0, 1.0]) * b_ for a_, b_ in zip(A2, B2)])
    dense = np.array([Rc + n_out * q[0] + np.array([0, 0, 1.0]) * q[2] for q in ab])
    ARMS[side] = {"Rc": Rc, "n_out": n_out, "S": S_ax, "C": C, "ell": ell,
                  "lo": np.min([e[0] for e in ell], 0) - 0.3, "hi": np.max([e[0] for e in ell], 0) + 0.3,
                  "dense": SD.resample(dense, 400), "length": float(al[-1])}


def sd_arm(P, side):
    """smooth union of the lobe's oriented ellipsoids, each evaluated only inside its own box; a point outside every box
    gets the bound min |P - c| - max radius (a true over-estimate-free fallback, so gradients / projection stay valid)"""
    A = ARMS[side]
    d = np.full(len(P), 1e9)
    hit = np.zeros(len(P), bool)
    for c, rad, fr in A["ell"]:
        r_ = max(rad) + 2 * ARM_K
        m = np.all(np.abs(P - c) <= r_, axis=1)
        if not m.any():
            continue
        hit |= m
        e = SD.sd_ellipsoid(P[m], c, rad, frame=fr)
        d[m] = np.where(d[m] > 1e8, e, SD.smin(np.minimum(d[m], 1e3), e, ARM_K))
    far = ~hit
    if far.any():
        Pf = P[far]
        f_ = np.full(len(Pf), 1e9)
        for c, rad, fr in A["ell"]:
            f_ = np.minimum(f_, np.linalg.norm(Pf - c, axis=1) - max(rad))
        d[far] = f_
    return d


def sd_arms(P):
    return np.minimum(sd_arm(P, "L"), sd_arm(P, "R"))


def sd_shell(P):
    return SD.smin(sd_body(P), sd_arms(P), ARM_BLEND)


def sd_grad(fn, P, h=1e-3):
    g_ = np.zeros_like(P)
    for k in range(3):
        e = np.zeros(3); e[k] = h
        g_[:, k] = (fn(P + e) - fn(P - e)) / (2 * h)
    return g_


def project_sdf(fn, P, iters=10):
    P = np.array(P, float)
    for _ in range(iters):
        d = fn(P); gg = sd_grad(fn, P)
        P = P - (d / np.maximum((gg * gg).sum(1), 1e-12))[:, None] * gg
    return P, np.array([unit(v) for v in sd_grad(fn, P)])


PART_SDF = {"body": sd_body, "arm": sd_arms}
GROUPS = list(PART_SDF)
_arm_pts = np.vstack([np.array([e[0] for e in ARMS[s]["ell"]]) for s in ARMS])
_arm_r = 0.45
g = SD.Grid((min(-0.95, _arm_pts[:, 0].min() - _arm_r), min(-1.0, _arm_pts[:, 1].min() - _arm_r),
             min(BODY_FLOOR - 0.05, _arm_pts[:, 2].min() - _arm_r)),
            (max(0.95, _arm_pts[:, 0].max() + _arm_r), 1.05, BZ[-1] + 0.05), SDF_STEP, SDF_BAND)
g.apply(sd_body, (-0.95, BODY_Y - 0.95, BODY_FLOOR - 0.02), (0.95, BODY_Y + 0.95, BZ[-1] + 0.01), 0.0)
for side in ("L", "R"):
    g.apply(lambda P, s_=side: sd_arm(P, s_), ARMS[side]["lo"], ARMS[side]["hi"], ARM_BLEND)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "grid": g.n.tolist(), "ops": g.ops, "high_tris": len(HF), "high_verts": len(HV),
                 "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
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
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "seconds": round(time.time() - t, 1)}
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))

# =========================================================================== 3. region fields + iso cuts on the shell
DGR = np.stack([PART_SDF[gname](LV) for gname in GROUPS], 1)


def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


HLC, HLR = np.array(HIGHLIGHT_C, float), np.array(HIGHLIGHT_R, float)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["hl"] = 1.0 - np.linalg.norm((LV - HLC) / HLR, axis=1)
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
in_body = lambda a, b: a[LAY["f_body"]] > -TOL and b[LAY["f_body"]] > -TOL
CUTS = [("f_arm", 0.0, None)] + ([("hl", 0.0, in_body)] if HIGHLIGHT else [])
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array(["body" if GROUPS[o] == "body" else "arm" for o in owner], dtype=object)
if HIGHLIGHT:
    shell_reg[(owner == 0) & (FVAL["hl"] > 0)] = "body_shade"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}
bvh_cut = BVHTree.FromPolygons(CV.tolist(), CF)

# =========================================================================== 4. parts
PARTS = []
# ---- the pot (pot + soil, one closed lathe; oval: lathe u = +Y scaled by POT_DEPTH, v = -X)
assert len(POT_REG) == len(POT_PROFILE) - 1, "one region per pot profile segment"
V, F, R = FP.lathe(POT_PROFILE, POT_REG, POT_SEG, (0, 0, 0), (0, 0, 1), su=POT_DEPTH, sv=1.0)
PARTS.append(FP.Part("pot", V, F, R, bone="pot"))
POT_P = PARTS[-1]
bvh_pot = BVHTree.FromPolygons(POT_P.V.tolist(), POT_P.F)


def pot_signed(P):
    """signed distance of points to the pot solid (nearest pot face; sign by its outward normal)"""
    out = np.empty(len(P))
    for i, p in enumerate(P):
        loc, nrm, _, d = bvh_pot.find_nearest(Vector(p))
        out[i] = d if (Vector(p) - loc).dot(nrm) >= 0 else -d
    return out


# ---- red leaves
LEAF = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    knots = np.array([(sg * x, y, z) for x, y, z in LEAF_MIDRIB])
    Cm = SD.resample(SD.catmull(knots, 16), 120)
    yaw = math.radians(LEAF_YAW)
    fn_ = np.array([sg * math.sin(yaw), math.cos(yaw), 0.0])
    V, F, R, S_ = FP.leaf_blade(Cm, fn_, lambda s: LEAF_HW * math.sin(math.pi * min(max(s, 0.0), 1.0) ** LEAF_SHAPE),
                                lambda s: LEAF_THICK * math.sin(math.pi * min(max(s, 0.0), 1.0)) ** 0.5,
                                LEAF_FOLD, LEAF_ROWS, LEAF_COLS, LEAF_MID_W, LEAF_VEINS, LEAF_VEIN_W, LEAF_VEIN_REACH,
                                LEAF_SHEAR)
    Lm = float(SD.arclen(Cm)[-1])
    inside = sd_body(Cm) < 0
    s_exit = float(SD.arclen(Cm)[int(np.argmin(inside))]) if (~inside).any() else 0.0     # where the midrib leaves the body
    p = FP.Part("leaf." + side, V, F, R)
    p.chain = ("leaf." + side, S_ * Lm, Lm, "head", LEAF_BONES, s_exit)
    PARTS.append(p)
    LEAF[side] = {"midrib": Cm, "length": Lm, "s_exit": s_exit}
# ---- sprout curl
yw = math.radians(SPROUT_YAW)
e1 = np.array([math.cos(yw), math.sin(yw), 0.0]); e2 = np.array([0.0, 0.0, 1.0])
SPc = np.array(SPROUT_C, float)
sp2 = SD.catmull(np.array([(x, 0.0, z) for x, z in SPROUT_PTS]), 10)
SPC = np.array([SPc + e1 * q[0] + e2 * q[2] for q in sp2])
SPC = SD.resample(SPC, 56)
spl = SD.arclen(SPC); spf = spl / spl[-1]
V, F, R, S_ = FP.tube(SPC, SPROUT_R[0] + (SPROUT_R[1] - SPROUT_R[0]) * spf ** 1.3, 8, "sprout", cap0=0.5, cap1=1.6,
                      up_hint=(0.0, -1.0, 0.0))
sp_inside = sd_body(SPC) < 0
sp_exit = float(spl[int(np.argmin(sp_inside))]) if (~sp_inside).any() else 0.0
p = FP.Part("sprout", V, F, R)
p.chain = ("sprout", S_ * spl[-1], float(spl[-1]), "head", SPROUT_BONES, sp_exit)
PARTS.append(p)
# ---- arm-tip curls: a spiral in the arm's tangent plane at CURL_S, each point re-projected onto the shell SDF and
#      lifted CURL_SEAT x the tube radius along the SDF normal
CURL = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    A = ARMS[side]
    dl = SD.arclen(A["dense"])
    i_c = int(np.searchsorted(dl, CURL_S * dl[-1]))
    i_c = min(i_c, len(dl) - 2)
    c0 = A["dense"][i_c]
    tg_ = unit(A["dense"][i_c + 1] - A["dense"][i_c])
    q_ = unit(np.cross(tg_, A["S"])); q_ = q_ if np.dot(q_, A["n_out"]) + q_[2] >= 0 else -q_
    (cs,), (cn,) = project_sdf(sd_shell, (c0 + q_ * 0.4)[None])
    u1 = unit(tg_ - cn * np.dot(tg_, cn)); u2 = np.cross(cn, u1)
    P2 = FP.spiral_2d(CURL_R[0], CURL_R[1], CURL_TURNS, 90.0, 60, sense=sg)
    P3 = cs[None] + P2[:, 0:1] * u1 + P2[:, 1:2] * u2 * sg
    rr = CURL_TUBE[0] + (CURL_TUBE[1] - CURL_TUBE[0]) * np.linspace(0, 1, len(P3))
    Ps, Ns = project_sdf(sd_shell, P3)
    path = Ps + Ns * (rr * CURL_SEAT)[:, None]
    V, F, R, S_ = FP.tube(path, rr, 7, "curl", cap0=0.6, cap1=1.0, up_hint=tuple(cn))
    PARTS.append(FP.Part("curl." + side, V, F, R, bone="arm.%s.%d" % (side, ARM_BONES - 1)))
    CURL[side] = {"centre": cs, "normal": cn}
# ---- face decals: ray-projected along the front view (+Y) onto the low shell; offsets along the smooth SDF normal


def ray_front(xz):
    out, nn = [], []
    for x, z in xz:
        hit = bvh_cut.ray_cast(Vector((x, -3.0, z)), Vector((0.0, 1.0, 0.0)))
        assert hit[0] is not None, ("decal ray missed the shell", x, z)
        out.append(np.array(hit[0]))
    out = np.array(out)
    nn = np.array([unit(v) for v in sd_grad(sd_shell, out)])
    return out, nn


def ellipse(rx, rz, n):
    th = 2 * math.pi * np.arange(n) / n
    return np.stack([rx * np.cos(th), rz * np.sin(th)], 1)


def make_decal(name, region, bone, centre_xz, outline, n_ring, height):
    """height(P_xz world, rho) -> (top offset, bottom offset) along the normal"""
    cx, cz = centre_xz

    def lift(P2, rho):
        W2 = P2 + np.array([cx, cz])
        hit, nn = ray_front(W2)
        top_o, bot_o = height(W2, rho)
        return hit + nn * top_o[:, None], hit + nn * bot_o[:, None], nn
    V, F, n_top = FP.decal(outline, n_ring, lift)
    PARTS.append(FP.Part(name, V, F, [region] * len(F), bone=bone))
    PARTS[-1].n_top = n_top
    return V


def eye_h(W2, ex):
    rho_e = np.sqrt(((W2[:, 0] - ex) / EYE_R[0]) ** 2 + ((W2[:, 1] - EYE_Z) / EYE_R[1]) ** 2)
    return DECAL_LIFT + EYE_DOME * np.clip(1.0 - rho_e ** 2, 0.0, 1.0)


FACE = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    ex = sg * EYE_X
    make_decal("eye." + side, "eye", "eye." + side, (ex, EYE_Z), ellipse(EYE_R[0], EYE_R[1], DECAL_SEG), 4,
               lambda W2, rho, ex=ex: (eye_h(W2, ex), np.full(len(W2), -DECAL_SINK)))
    gx = ex - sg * GLINT_DX
    make_decal("eye_glint." + side, "eye_glint", "eye." + side, (gx, EYE_Z + GLINT_DZ),
               ellipse(GLINT_R[0], GLINT_R[1], 28), 2,
               lambda W2, rho, ex=ex: (eye_h(W2, ex) + 0.004 + 0.004 * (1 - rho ** 2), eye_h(W2, ex) * 0.5))
    FACE["eye." + side] = (ex, EYE_Z)
_mo = []
for k in range(DECAL_SEG):
    th = 2 * math.pi * k / DECAL_SEG
    x = MOUTH_W * math.cos(th)
    if math.sin(th) >= 0:                     # top edge: corners up, the middle lower (the smile)
        y = MOUTH_TOP_MID + (MOUTH_TOP - MOUTH_TOP_MID) * (x / MOUTH_W) ** 2
        y *= min(1.0, math.sin(th) * 6.0) if abs(math.cos(th)) > 0.97 else 1.0
    else:
        y = MOUTH_BOT * math.sin(th)
    _mo.append((x, y))
make_decal("mouth", "mouth", "head", (0.0, MOUTH_Z), np.array(_mo), 3,
           lambda W2, rho: (np.full(len(W2), DECAL_LIFT), np.full(len(W2), -DECAL_SINK)))
make_decal("tongue", "tongue", "head", (0.0, TONGUE_Z), ellipse(TONGUE_R[0], TONGUE_R[1], 28), 2,
           lambda W2, rho: (DECAL_LIFT + 0.003 + 0.004 * (1 - rho ** 2), np.zeros(len(W2))))
MOUTH_HIT, _ = ray_front(np.array([[0.0, MOUTH_Z]]))
EYE_HIT = {s: ray_front(np.array([FACE["eye." + s]]))[0][0] for s in ("L", "R")}

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, RANGE, FALL_START = [], [], [], {}, {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    FALL_START[name] = len(FALL)
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    nv_ += len(V)
VALL = np.vstack(VALL)
HV_s = HV - SHIFT
REG = ["body", "body_shade", "arm", "curl", "sprout", "leaf", "leaf_vein", "leaf_midrib", "eye", "eye_glint", "mouth",
       "tongue", "pot", "pot_rim", "pot_lip", "pot_inner", "soil"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)


def jitter(V, F, amp):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 1.0 - amp + 2.0 * amp * j


def make_mat(name):
    """one opaque material: Col -> Base Color, Glow -> Emission Color (all zero: no glow on the sheet)"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                       # opaque closed solids: single-sided
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
glow_any = [n for n, v in pal_default["regions"].items() if "emission" in v]
assert not glow_any, ("seedant has no glow on the sheet", glow_any)

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = np.ones(len(FALL))
FCALL = np.array([np.mean(VALL[f], 0) for f in FALL])
jit_pot = jitter(VALL, FALL, POT_JITTER)
jit_leaf = jitter(VALL, FALL, LEAF_JITTER)
is_pot = np.isin(rid, [REG.index(r) for r in ("pot", "pot_rim", "pot_lip", "pot_inner", "soil")])
is_leaf = np.isin(rid, [REG.index(r) for r in ("leaf", "leaf_vein", "leaf_midrib")])
shade[is_pot] = jit_pot[is_pot]
shade[is_leaf] = jit_leaf[is_leaf]
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the mouth centre (on the midline)
anchor = np.array([0.0, BODY_Y, 3.40]) - SHIFT
landmark = MOUTH_HIT[0] - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (body axis, z 3.40) -> the mouth centre on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

# ---- arm-on-rim clearance (rest): every arm shell vertex + curl vertex vs the pot solid
_arm_faces = np.nonzero(rid[:len(CF)] == REG.index("arm"))[0]
ARM_V = np.unique(np.array([v for fi in _arm_faces for v in CF[fi]]))           # shell-local == global (shell first)
CURL_V = np.concatenate([np.arange(*RANGE["curl.L"]), np.arange(*RANGE["curl.R"])])
cl_arm = pot_signed(VALL[ARM_V] + SHIFT)
cl_curl = pot_signed(VALL[CURL_V] + SHIFT)
cl_hi = pot_signed(HV[(sd_arms(HV) < sd_body(HV))])
report["arm_rim_clearance"] = {
    "rule": "signed distance from every arm vertex to the pot solid (nearest pot face, sign by its outward normal); "
            "> 0 = outside the pot. The arms rest ON the rim: min ~ 0 and never below -0.005",
    "low_arm_min": round(float(cl_arm.min()), 4),
    "low_arm_min_at_sheet_xyz": (VALL[ARM_V][int(np.argmin(cl_arm))] + SHIFT).round(3).tolist(), "low_arm_p01": round(float(np.percentile(cl_arm, 1)), 4),
    "low_arm_below_-0.005": int((cl_arm < -0.005).sum()), "high_arm_min": round(float(cl_hi.min()), 4),
    "high_arm_below_-0.005": int((cl_hi < -0.005).sum()), "curl_min": round(float(cl_curl.min()), 4),
    "arm_verts": int(len(ARM_V))}
print("CLEARANCE", json.dumps(report["arm_rim_clearance"]))

# ---- flat + UV
me.shade_flat()
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
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), **{p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. Simple soft forms (one gumdrop shell, an oval "
                            "pot, two leaves, three curls, face decals) -> lands in the low half of the window."
                            % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": {k: v for k, v in open_e.items() if v == 0},
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell = marching tets of one SDF; pot = one lathe with "
                                "poles; leaves = lens blades with pole ends; curls/sprout = capped tubes; decals = "
                                "top + bottom + wall)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1)},
                     "sheet_reference": {"front_height_u": 5.01, "front_width_u": 3.68, "side_width_u": 2.80},
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
low["conquest_source"] = "from scratch: design/reference/seedant-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color: all zero (no glow)"


def rng_pts(*names):
    return np.vstack([VALL[RANGE[n][0]:RANGE[n][1]] for n in names])


_face = np.vstack([rng_pts("eye.L", "eye.R", "mouth", "sprout"), (np.array([0.75, -0.9, 2.75]) - SHIFT)[None],
                   (np.array([-0.75, -0.9, 4.3]) - SHIFT)[None]])
_leaf = rng_pts("leaf.L")
_leaf = _leaf[_leaf[:, 2] > 3.2 - SHIFT[2]]
_arm = np.vstack([VALL[ARM_V][VALL[ARM_V][:, 0] > 0], rng_pts("curl.L")])
_potf = rng_pts("pot")
_potf = _potf[(_potf[:, 0] < -0.2) & (_potf[:, 2] > 1.0)]
low["conquest_focus"] = json.dumps({"face": [_face.min(0).tolist(), _face.max(0).tolist()],
                                    "leaf": [_leaf.min(0).tolist(), _leaf.max(0).tolist()],
                                    "arm": [_arm.min(0).tolist(), _arm.max(0).tolist()],
                                    "pot": [_potf.min(0).tolist(), _potf.max(0).tolist()],
                                    "views": {"face": [0.0, 6.0], "leaf": [125.0, 10.0], "arm": [28.0, 4.0],
                                              "pot": [-35.0, 24.0]},
                                    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "arm_rim_clearance", "centre_shift")}))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + parts)
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
nt = MAT_BODY.node_tree
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
nf = len(me.polygons)
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
ls_ = np.concatenate([[0], np.cumsum(lt_)[:-1]])
UVl = UVn.reshape(-1, 2)


def texel_faces(res):
    """per-texel owning face index (-1 = no face; UV islands do not overlap), one rasterising pass"""
    lab = np.full(res * res, -1, dtype=np.int64)
    for fi in range(nf):
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
            lab[ys.ravel()[ins] * res + xs.ravel()[ins]] = fi
    return lab


TEXF = {}


def texels_of(mask_faces, res):
    if res not in TEXF:
        TEXF[res] = texel_faces(res)
    lab = TEXF[res]
    return (lab >= 0) & np.asarray(mask_faces, bool)[np.maximum(lab, 0)]


tb = time.time()
for o in scene.objects:
    o.select_set(o is HIGH or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt.nodes.active = node
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
all_faces = np.ones(nf, bool)
alltex_n = texels_of(all_faces, RN)
alltex_a = texels_of(all_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part",
    "uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
# ---- AO per texel class: HIDDEN faces (decal undersides + walls, part faces buried in the shell, shell / soil faces
#      buried in the pot or the body) vs VISIBLE; then the per-region AO floor on the region's own texels
FC_s = FCALL + SHIFT
hid = np.zeros(nf, bool)
for p in PARTS:
    f0 = FALL_START[p.name]
    nfp = len(p.F)
    if getattr(p, "n_top", None) is not None:
        hid[f0 + p.n_top:f0 + nfp] = True
    elif p.name != "pot":
        hid[f0:f0 + nfp] |= sd_shell(FC_s[f0:f0 + nfp]) < -0.01
f0, nfp = FALL_START["shell"], len(CF)
hid[f0:f0 + nfp] |= (FC_s[f0:f0 + nfp, 2] < RIM_TOP) & (pot_signed(FC_s[f0:f0 + nfp]) < -0.005)
f0, nfp = FALL_START["pot"], len(POT_P.F)
hid[f0:f0 + nfp] |= sd_shell(FC_s[f0:f0 + nfp]) < -0.01
vis_a = texels_of(~hid, RA); hid_a = texels_of(hid, RA) & ~vis_a
ok_a = cov_a & alltex_a
zero = ok_a & (pa[:, 0] <= 0.02)
cls = {"hidden_faces": int(hid.sum()), "visible_faces": int((~hid).sum()),
       "ao_le_0.02_texels": int(zero.sum()), "ao_le_0.02_pct": round(100 * float(zero.sum() / ok_a.sum()), 2),
       "ao_le_0.02_on_hidden_only_texels": int((zero & hid_a).sum()),
       "ao_le_0.02_on_visible_texels": int((zero & vis_a).sum()),
       "visible_ao_p05_before_floor": round(float(np.percentile(pa[ok_a & vis_a, 0], 5)), 4),
       "visible_ao_p01_before_floor": round(float(np.percentile(pa[ok_a & vis_a, 0], 1)), 4), "per_region": {}}
for j, n in enumerate(REG):
    m_ = texels_of((rid == j) & ~hid, RA) & ok_a
    if m_.any():
        cls["per_region"][n] = {"texels": int(m_.sum()), "p05": round(float(np.percentile(pa[m_, 0], 5)), 4),
                                "le_0.02": int((m_ & zero).sum())}
    if n in AO_FLOOR:
        mf = texels_of(rid == j, RA) & ok_a
        pa[mf, 0:3] = np.maximum(pa[mf, 0:3], AO_FLOOR[n])
cls["floor"] = dict(AO_FLOOR)
cls["visible_ao_p05_after_floor"] = round(float(np.percentile(pa[ok_a & vis_a, 0], 5)), 4)
cls["visible_ao_p01_after_floor"] = round(float(np.percentile(pa[ok_a & vis_a, 0], 1)), 4)
bstats["ao_classes"] = cls
print("AO_CLASSES", json.dumps(cls))
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
                     "provenance": "pixel-sampled from the sheet (chips + masked medians of the drawn surfaces)"}
report["leaves"] = {s: {"length": round(v["length"], 4), "s_exit": round(v["s_exit"], 4)} for s, v in LEAF.items()}
report["arms"] = {s: {"rim_point": (v["Rc"] - SHIFT).round(4).tolist(), "n_out": v["n_out"].round(4).tolist(),
                      "length": round(v["length"], 4)} for s, v in ARMS.items()}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 7. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
S0 = SHIFT


def P_(p):
    return np.asarray(p, float) - S0


BONES = [("pot", P_((0, 0, 0)), P_((0, 0, 1.2)), "root"),
         ("body", P_((0, BODY_Y, 2.40)), P_((0, BODY_Y, 3.05)), "pot"),
         ("head", P_((0, BODY_Y, 3.05)), P_((0, BODY_Y, 4.0)), "body")]
for side in ("L", "R"):
    BONES.append(("eye." + side, P_(EYE_HIT[side]), P_(EYE_HIT[side] + np.array([0, 0, 0.25])), "head"))
CHAIN_PTS = {}


def chain_points(C, s0, nb):
    al = SD.arclen(C)
    tt = np.linspace(s0, al[-1], nb + 1)
    return [P_([np.interp(x, al, C[:, k]) for k in range(3)]) for x in tt]


ARM_EXIT = {}
for side in ("L", "R"):
    D = ARMS[side]["dense"]
    ins = sd_body(D) < 0
    ARM_EXIT[side] = float(SD.arclen(D)[int(np.argmin(ins))])
    CHAIN_PTS["arm." + side] = (chain_points(D, ARM_EXIT[side], ARM_BONES), "body")
    CHAIN_PTS["leaf." + side] = (chain_points(LEAF[side]["midrib"], LEAF[side]["s_exit"], LEAF_BONES), "head")
CHAIN_PTS["sprout"] = (chain_points(SPC, sp_exit, SPROUT_BONES), "head")
for chain, (pts, parent) in sorted(CHAIN_PTS.items()):
    for k in range(len(pts) - 1):
        BONES.append(("%s.%d" % (chain, k), pts[k], pts[k + 1], parent if k == 0 else "%s.%d" % (chain, k - 1)))
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
    e.use_connect = nm[-1].isdigit() and not nm.endswith(".0")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
SV = VALL[a_:b_] + S0
wh = smoothstep(NECK_Z[0], NECK_Z[1], SV[:, 2])
W_bh = np.zeros((len(SV), len(DEFORM)))
W_bh[:, J["head"]] = wh; W_bh[:, J["body"]] = 1.0 - wh
f_arm_v = own_field(np.stack([sd_body(SV), sd_arms(SV)], 1), 1)
Wsh = W_bh.copy()
for side, sg in (("L", 1.0), ("R", -1.0)):
    D = ARMS[side]["dense"]
    al = SD.arclen(D)
    seg = np.argmin(((SV[:, None, :] - D[None, ::4, :]) ** 2).sum(2), 1) * 4          # coarse nearest knot
    s_v = al[np.clip(seg, 0, len(al) - 1)]
    Lc = al[-1] - ARM_EXIT[side]
    Wv = K.vine_weights(np.clip(s_v - ARM_EXIT[side], 0, Lc), Lc, ARM_BONES)
    W_arm = np.zeros_like(W_bh)
    W_arm[:, J["body"]] = Wv[:, 0]
    for k in range(ARM_BONES):
        W_arm[:, J["arm.%s.%d" % (side, k)]] = Wv[:, k + 1]
    a_side = smoothstep(-0.04, 0.06, f_arm_v) * (np.sign(SV[:, 0]) == sg)
    Wsh = (1.0 - a_side)[:, None] * Wsh + a_side[:, None] * W_arm
Wt[a_:b_] = Wsh
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if p.chain is not None:
        chain, s_arc, L_, parent, nb, s0 = p.chain
        Lc = L_ - s0
        Wv = K.vine_weights(np.clip(s_arc - s0, 0, Lc), Lc, nb)
        Wt[a_:b_, J[parent]] += Wv[:, 0]
        for k in range(nb):
            Wt[a_:b_, J["%s.%d" % (chain, k)]] += Wv[:, k + 1]
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
                  "rule": "pot (pot + soil) 100% pot; shell: head above the NECK_Z blend, body below; arm-owned shell "
                          "blends (by SDF ownership) into rigkit.vine_weights along the arm path from where it leaves the "
                          "body (parent body); curls 100% on the arm tip bone; leaves + sprout vine weights from where "
                          "they leave the body (parent head); eye + glint decals 100% eye.{L,R}; mouth + tongue head"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: idle + walk (PLACEHOLDER), closed-form periodic curves keyed every frame on every deform bone
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """World-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> the bone's basis
    quaternion. Axis 'B' = the bone's own rest direction; a 3-vector = that world axis."""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        if isinstance(axis, str):
            av = Vector(REST[bone][:, 1]) if axis == "B" else Vector(AXV[axis])
        else:
            av = Vector(tuple(axis))
        Rw = Matrix.Rotation(math.radians(deg), 3, av) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def wave(u, k, n, spec, phase):
    a0, a1, cyc, lag = spec
    return K.vine_wave(u, k, n, a0, a1, lag, cyc, phase)


def pulse(u, u0, w):
    """smooth periodic bump: 1 at u0, 0 beyond +- w (loop-wrapped)"""
    d = (u - u0 + 0.5) % 1.0 - 0.5
    x = min(1.0, abs(d) / w)
    return math.cos(0.5 * math.pi * x) ** 2


# the outward arm rotation: about the rim tangent S; sign so the arm tip swings AWAY from the pot wall
ARM_OUT = {}
for side in ("L", "R"):
    b2 = arm_data.bones["arm.%s.%d" % (side, ARM_BONES - 1)]
    v_ = np.array(b2.tail_local - b2.head_local)
    S_ = ARMS[side]["S"]
    Rp = np.array(Matrix.Rotation(math.radians(5.0), 3, Vector(tuple(S_))))
    ARM_OUT[side] = S_ if np.dot(Rp @ v_ - v_, ARMS[side]["n_out"]) > 0 else -S_


def hop_terms(u):
    """walk: per-hop phase h, lift z (>= 0), vertical scale"""
    h = (u * HOPS) % 1.0
    air = h >= HOP_CONTACT
    lift = HOP_H * math.sin(math.pi * (h - HOP_CONTACT) / (1.0 - HOP_CONTACT)) if air else 0.0
    sq = SQUASH * (pulse(h, 0.06, 0.12) + 0.6 * pulse(h, 0.22, 0.07))
    st = STRETCH * pulse(h, 0.36, 0.10)
    return h, lift, 1.0 + st - sq


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    info = {}
    if clip == "idle":
        roll = IDLE_HEAD_ROLL[0] * math.sin(TAU * u) + IDLE_HEAD_ROLL[1] * math.sin(TAU * 3 * u + 0.7)
        pitch = IDLE_HEAD_PITCH * math.sin(TAU * 2 * u - 0.9)
        yaw = IDLE_HEAD_YAW * math.sin(TAU * u + 2.0)
        br = 0.5 * (1.0 - math.cos(TAU * 2 * u))
        sb, shd = 1.0 + IDLE_BREATH[0] * br, 1.0 + IDLE_BREATH[1] * br
        P["pot"] = (None, [], None)
        P["body"] = (None, [], (sb, sb, sb))
        P["head"] = (None, [("Y", roll), ("X", pitch), ("Z", yaw)], (shd, shd, shd))
        bl = 1.0 - (1.0 - BLINK_MIN) * pulse(u, BLINK_U, BLINK_W)
        for side in ("L", "R"):
            P["eye." + side] = (None, [], (1.0, bl, 1.0))
            ph = LEAF_PHASE[side]
            sg = 1.0 if side == "L" else -1.0
            for k in range(LEAF_BONES):
                P["leaf.%s.%d" % (side, k)] = (None, [("X", wave(u, k, LEAF_BONES, IDLE_LEAF["X"], ph)),
                                                      ("Y", sg * wave(u, k, LEAF_BONES, IDLE_LEAF["Y"], ph + 1.1)),
                                                      ("B", sg * wave(u, k, LEAF_BONES, IDLE_LEAF["B"], ph + 2.3))], None)
            tap = max(pulse(u, TAP_U[side][0], TAP_W), pulse(u, TAP_U[side][1], TAP_W))
            P["arm.%s.0" % side] = (None, [], None)
            P["arm.%s.1" % side] = (None, [(ARM_OUT[side], IDLE_TAP[0] * tap)], None)
            P["arm.%s.2" % side] = (None, [(ARM_OUT[side], IDLE_TAP[1] * tap)], None)
        for k in range(SPROUT_BONES):
            P["sprout.%d" % k] = (None, [("X", wave(u, k, SPROUT_BONES, IDLE_SPROUT, 0.4)),
                                         ("Y", wave(u, k, SPROUT_BONES, IDLE_SPROUT, 1.9))], None)
        info = {"blink": bl}
    else:
        h, lift, sy = hop_terms(u)
        sx = 1.0 / math.sqrt(sy)
        air = lift / HOP_H
        P["pot"] = ((0.0, 0.0, lift), [("X", -WALK_LEAN * air)], (sx, sy, sx))
        land = pulse(h, 0.05, 0.14)
        P["body"] = (None, [], None)
        P["head"] = (None, [("X", -WALK_HEAD_LAG * land + 1.5 * WALK_HEAD_LAG * pulse(h, 0.40, 0.12))], None)
        for side in ("L", "R"):
            sg = 1.0 if side == "L" else -1.0
            P["eye." + side] = (None, [], None)
            ph = LEAF_PHASE[side] * 0.5
            for k in range(LEAF_BONES):
                P["leaf.%s.%d" % (side, k)] = (None, [("X", wave(u, k, LEAF_BONES, WALK_LEAF["X"], ph)),
                                                      ("B", sg * wave(u, k, LEAF_BONES, WALK_LEAF["B"], ph + 1.3))], None)
            flap = pulse(h, 0.08, 0.16)
            P["arm.%s.0" % side] = (None, [], None)
            P["arm.%s.1" % side] = (None, [(ARM_OUT[side], WALK_ARM[0] * flap)], None)
            P["arm.%s.2" % side] = (None, [(ARM_OUT[side], WALK_ARM[1] * flap)], None)
        for k in range(SPROUT_BONES):
            P["sprout.%d" % k] = (None, [("X", wave(u, k, SPROUT_BONES, (4.0, 10.0, 2, 0.9), 0.3))], None)
        info = {"lift": lift, "sy": sy, "h": h}
    return P, info


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
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


POT_R = RANGE["pot"]
PROBE_V = np.concatenate([ARM_V, CURL_V])
samples = []
clip_rep = {}
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
        row = {"min_z": float(C[:, 2].min()), "pot_z": float(pbs["pot"].head.z),
               "root": float((rig.matrix_world @ pbs["root"].head).length)}
        if (f - 1) % 2 == 0:
            bv = BVHTree.FromPolygons(C[POT_R[0]:POT_R[1]].tolist(), POT_P.F)
            dmin = 1e9
            for i in PROBE_V[::2]:
                loc, nrm, _, d = bv.find_nearest(Vector(C[i]))
                sd_ = d if (Vector(C[i]) - loc).dot(nrm) >= 0 else -d
                dmin = min(dmin, sd_)
            row["arm_pot_min"] = dmin
        rows.append(row)
    R_ = rows[:-1]
    gz = lambda k: np.array([r[k] for r in R_ if k in r])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "status": "PLACEHOLDER (artist question S1: movement intent not given)",
                    "seam_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "min_z": round(float(gz("min_z").min()), 5),
                    "pot_lift": [round(float(gz("pot_z").min()), 4), round(float(gz("pot_z").max()), 4)],
                    "arm_pot_clearance_min": round(float(gz("arm_pot_min").min()), 4),
                    "arm_pot_clearance_rest": report["arm_rim_clearance"]["low_arm_min"]}
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
rig["conquest_rig"] = ("seedant v1: root > pot > body > head; head > sprout.0,1 / leaf.{L,R}.0,1,2 / eye.{L,R}; "
                       "body > arm.{L,R}.0,1,2 (the tip curl rides arm.*.2)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (artist question S1 open): idle = head sway + breathing, leaf rustle "
                               "(de-synced chains), arm-tip taps on the pot wall (outward only), one blink (4 s); walk = "
                               "in-place POT HOP x2 (squash on land / stretch at take-off, leaf + arm follow-through; "
                               "root never moves) (2 s)")
low["conquest_locomotion"] = "potted hopper (PLACEHOLDER): in-place pot hop, the game glides the unit"
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

# =========================================================================== 8. glb (default skin only: none requested)
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
                            "colour_sets": sorted(k for k in p["attributes"] if k.startswith("COLOR"))} for n, p in prims],
            "nodes": sorted(n.get("name") for n in js.get("nodes", [])),
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0,
            "extensionsUsed": js.get("extensionsUsed", [])}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'seedant' (one opaque primitive); natural scale; report-only "
                           "cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default)},
                "extra": "none requested (OPEN-QUESTIONS S4): no repaint proof needed"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
