"""Duskmaw v2 (hero, Conquest character_id 'monster'): the shadow-figure rework, one headless run.

    blender --background source-copies/hero-monster.blend --factory-startup --python improve/duskmaw_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette only: no bake, no rig -- fast look loop)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Artist spec (design/review-log.md, 2026-09-25 "Duskmaw v2 feedback" + "Duskmaw v2 addendum", verbatim there):
  keep the mouth silhouette + an idle CHOMP; the shadow legs' bottom near-uniform, "like a shadow coming out of the
  ground", locomotion GLIDES; simplify the face (Aku / Father (KND) / Darkrai: a crisp shadowy figure); red around the
  chest/leg mouth piece so it reads as the devouring mouth; the mouth CLOSED from behind ("like an actual mouth").

Reads (never writes): source-copies/hero-monster.blend (the 553k-tri sculpt, opened; the brief's 'monster.blend').
Outputs:
    improved/duskmaw.blend + .json          hero-tier low, UVs, baked normal/AO, region palette (no rig)
    improved/textures/duskmaw_{normal,ao}.png
    rigged/duskmaw.blend + .json            + jawed-totem rig (11 deform bones + contract root), clips idle (chomp) + walk (glide)
    rigged/duskmaw.glb                      identity-scale export (natural scale)
v1 (the 5 carried shipped clips attack/hit/death included) stays in git history (commit before this rework).

Pipeline:
  1. sculpt main shell -> origin (XY bbox centre, floor z = 0). Front = -Y.
  2. SDF REBUILD (Blender geometry-node SDF grids, voxel VOX): sculpt minus the ground legs (below Z_CUT) and minus its
     back teeth/skirt (behind THETA_C), union a procedural LOWER BODY (a radial solid: rounded floor hem -> flare ->
     skirt -> pinched waist -> into the chest) from which the THROAT (ellipsoid CAV x the body's inside, open only in
     the front THETA_OPEN) is carved; then a MASKED mean smoothing (face, lower body, back waist) blended in SDF space.
     Result = the new high surface (bake source) -- closed: the mouth has a back wall, no see-through.
  3. LOW: collapse decimation (deterministic) -> main shell -> re-centre; the eye patch is refined (edges <= EYE_EDGE,
     new vertices projected onto the high surface) so the eye outlines are crisp.
  4. region FIELDS on the low + ISO-CONTOUR CUTS (v1 machinery): eyes, throat membership, red-lip geodesic band,
     teeth (height persistence on the sculpt-derived jaws), ember spike tips, shadow-base line.
  5. regions -> palettes.store_regions; paint from palettes/duskmaw/default.json; Smart UV; bake normal + AO from the
     rebuilt high.
  6. RIG (jawed totem, local): base (ground ring, never keyed) -> sway (lean pivot) -> jaw (lower jaw) | spine (upper
     jaw + chest) -> chest -> head -> crown, arm+blade x2, + contract 'root'. Analytic weights: an upper-jaw field
     (sculpt-derived chest = 1, sculpt-derived lower teeth = 0, the rest by height) splits the mouth.
     Clips (keyed every frame from closed-form curves, seam = 0 by construction): idle = float + one CHOMP per loop;
     walk = GLIDE (no stepping: forward lean, float bob, arms/crown trailing; the ground ring never moves).
  7. identity-scale glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, heapq
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402  (read-only use)
import palettes as PAL  # noqa: E402  (read-only use)

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun). Lengths are sculpt units
# (the figure is ~30.9 units tall; natural scale, no refit). Angles: degrees from the FRONT (-Y), both sides.
UNIT = "duskmaw"
CHAR_ID = "monster"
TRI_BUDGET = [25000, 35000]           # declared hero-tier window (contract tri_budget)
LOW_TRIS = 24000                      # "mesh detail" (decimation target before the colour cuts + eye refinement)
VOX = 0.07                            # "surface resolution": SDF voxel size of the rebuilt high surface
SDF_BAND = 12                         # SDF narrow band (voxels) -- wide enough for the masked smoothing to move surfaces
# ---- shadow base (feedback 2: "the bottom should almost be uniform like a shadow coming out of the ground")
Z_CUT = 4.0                           # "leg cut": the sculpt's ground legs/spikes are replaced below this height
CUT_OUTSET = 0.2                      # the new body meets the cut sculpt skirt this far outside it (no ledge)
Z_BT = 4.6                            # skirt reference band top (the body follows the skirt section Z_CUT..Z_BT)
BT_INSET = 0.3                        # the body sits this far inside the skirt above the cut (the sculpt skin wins)
HEM_R = 0.45                          # "hem roundness": rounded floor edge radius
FLARE = 0.45                          # "base flare": how far the shadow spreads at the floor (x the skirt radius)
FLARE_POW = 2.0                       # "flare curve": higher = the spread hugs the floor
BASE_ROUND = 0.7                      # "base uniformity": 0 = floor ring follows the skirt lobes, 1 = one round ring
CORE_W = 16                           # "back smoothness": back/sides follow the skirt section averaged over +-CORE_W bins (2.5 deg each)
# ---- closed mouth (feedback 5: "the mouth from the back side of it should be closed")
THETA_OPEN = 70.0                     # "mouth width": half-angle of the open front
THETA_C = 100.0                       # "closed back": the sculpt's back teeth + back skirt are replaced behind this angle
D_BLEND = 15.0                        # ... blended over +- this many degrees (the cut follows the body surface there)
D_TOP, D_RMIN, D_RMAX = 9.0, 2.0, 8.5  # ... up to this height, between these radii (the throat column + hanging arms kept)
Z_W0, Z_W1 = 5.0, 5.8                 # waist starts from the skirt section in this band ...
Z_TOP = 11.2                          # ... and ends inside the chest at this height
CH_INSET = 0.35                       # ... inset under the chest skin
PINCH, PINCH_B = 0.2, 0.1             # "waist pinch" front/sides and back (fraction of the radius at mid-waist). Back was
                                      # 0.45: that notch behind the waist let light through (3.6 % of the back-hemisphere
                                      # rays) once the glide trailed the hanging hands out of the way -- the back stays full
PINCH_TH = (95.0, 130.0)              # ... the back pinch ramps in over these angles
FRONT_INSET = 0.35                    # the waist hides inside the sculpt lips in the open front (the lips are the sculpt's)
CAV = (0.0, -2.2, 7.8)                # "throat": ellipsoid carved behind the lips (centre, relative to the waist axis x/y) ...
CAV_R = (5.0, 3.8, 2.2)               # ... semi-axes x / y / z
T_WALL = 1.0                          # "cheek wall": the throat stays this far inside the body surface (no see-through)
# ---- masked smoothing (SDF mean, width SM_W voxels x SM_IT iterations, blended by the masks below)
SM_W, SM_IT = 3, 14
FACE_Z = (14.6, 15.4, 20.0, 20.8)     # "face smoothing" height ramps (up, down) -- feedback 3: simplify the face
FACE_RAD = (4.2, 5.2)                 # ... radius ramp from the head axis
FACE_AX_Y = 0.0
LOW_Z = (0.3, 1.2, 4.9, 5.7)          # "base smoothing" height ramps (the skirt lobes melt into one shadow)
LOW_RAD = (10.5, 11.5)                # ... never the hanging hands
WAIST_TH = (82.0, 98.0)               # "back waist smoothing" angle ramp ...
WAIST_Z = (4.8, 5.6, 10.2, 11.0)      # ... height ramps ...
WAIST_RAD = (6.6, 7.3)                # ... radius ramp (the lower arms stay crisp)
WAIST_K = 0.8                         # ... strength
# ---- eyes (Aku / Father / Darkrai: the eyes ARE the face) -- (x, z) on the front of the face, mirrored
EYE_IN = (0.42, 18.72)                # "eye inner corner"
EYE_OUT = (2.05, 19.42)               # "eye outer corner" (higher = angrier slant)
EYE_TOP = 0.10                        # "brow arch": top edge bulge (units)
EYE_BOT = 0.46                        # "eye height": bottom edge depth (units)
EYE_POW = (0.9, 0.65)                 # top / bottom edge shape exponents (lower = fuller toward the corners)
EYE_EDGE = 0.1                       # the eye patch is refined to edges no longer than this before the cut
EYE_BOX = (2.7, 16.6, 20.4)           # refinement box: |x| < 2.7, z in [16.6, 20.4], front-facing ...
EYE_NEAR = 0.12                       # ... and only faces within this of an eye / grin outline (or inside)
GRIN_Z = (17.25, 0.18, 0.55)          # variant "grin" (palette 'grin' only; default paints it as the face):
GRIN_W = 1.55                         # ... centre z, band half-height, corner lift, half-width
# ---- recolour (feedback 4: "red around its chest/leg mouth piece")
LIP_W = 0.65                          # "red lip width": geodesic band framing the mouth opening, outer surface
LIP_TH = 95.0                         # ... only within this angle of the front
TEETH_TH = 75.0                       # "teeth reach": ember teeth only within this angle of the front (the first v2 preview
                                      # lit tooth tips at ~85 deg that showed as two orange dots from BEHIND)
THROAT_R = 1.9                        # the throat column inside the maw (glows)
THROAT_BACK = 0.4                     # cavity faces behind the axis + this glow as throat; the rest is the inner mouth
CAV_TOL = 0.03                        # cavity membership tolerance (ellipsoid value 1 + CAV_TOL)
BASE_Z = 3.4                          # "shadow base line": below this the base region (darkens toward the floor)
BASE_DARK = 0.55                      # ... shade multiplier at the floor
SPIKE_TIPS = True                     # ember tips on the arm / hat / hand spikes (dim; the palette 'tips' region)
SEED_Z_FRAC = 0.45                    # tip field seed ring height (x H)
TIP_LEN, TIP_ABS, MIN_SPIKE, SPIKE_RMIN = 0.5, (0.8, 4.0), 1.0, 0.15   # tip burn fraction / clamp / min spike / min radius (x H)
MIN_TOOTH = 0.45                      # a tooth rises this far above its root (height persistence)
TOOTH_LEN, TOOTH_ABS = 0.4, (0.3, 0.8)   # "ember teeth": outer fraction of each tooth that burns, clamped
SCULPT_TOL = 0.12                     # a low vertex within this of the sculpt surface is sculpt-derived (teeth search) ...
JAW_RMAX = 8.0                        # ... inside this radius (the hanging hands are not teeth) ...
UP_JAW_Z, LO_JAW_Z = (8.1, 11.0), (5.4, 8.6)   # ... upper jaw (chest points) / lower jaw (skirt peaks) height windows
CUT_SNAP = 0.18                       # iso-cut snap (no slivers)
BAKE_CAGE = 0.15
BAKE_RES = (2048, 1024)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest hero ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- rig (fractions of the height H as v1's skirted totem where kept)
BASE_BONE = (0.02, 0.10)              # ground ring bone z range (x H) -- never keyed: the shadow stays planted
SWAY_Z = (1.4, 4.4)                   # lean pivot bone (units)
JAW_Z = (4.4, 7.4)                    # lower jaw bone
SPINE_Z0 = 8.6                        # upper jaw / spine head (the mouth line)
Z_FR = {"chest": (0.52, 0.68), "head": (0.68, 0.82), "crown": (0.82, 0.99)}
ARM_FR = {"shoulder_x": 0.10, "elbow_x": 0.55, "tip_x": 0.98, "z_sh": 0.60, "z_el": 0.63, "z_tip": 0.58}
W_BAND = 1.0                          # joint softness (upper chain)
BASE_W = (0.2, 3.2)                   # ground ring -> sway blend heights
JAW_W = (4.4, 5.6)                    # sway -> jaw blend heights
U_Z = (7.2, 9.6)                      # upper-jaw share by height for non-sculpt surfaces (throat walls, column)
U_SMOOTH = 10                         # upper-jaw field smoothing iterations
ARM_IN, ARM_OUT, ARM_ZMIN, ARM_ZMAX = 0.16, 0.26, 0.49, 0.66
HAND_R, HAND_ZTOP, HAND_Y, HAND_ZLOW = (7.4, 8.6), (12.0, 13.5), 3.2, 3.8
HAND_MIX = {"spine": 0.6, "arm": 0.1, "blade": 0.3}
# ---- clips (feedback 1: idle chomp; feedback 2: glide)
IDLE_N = 49                           # idle frames 1..49 (48-frame loop, 2.0 s)
CHOMP = {"open_f": (6, 16), "shut_f": (16, 19), "hold_f": (19, 23), "rest_f": (23, 36)}   # beat (frames)
CHOMP_OPEN = 0.9                      # "chomp wind-up": extra gap at the open peak (units)
CHOMP_SHUT_GAP = -0.45                # side teeth tip-to-tip gap when shut (units; negative = the fangs interlock). The
                                      # centre teeth sit 1.56 apart at rest vs the side pairs' 0.97: at +0.05 the first full
                                      # run's bite left the centre of the mouth 0.64 open (the throat still showed at the bite)
CHOMP_UPPER = 0.6                     # share of the travel done by the upper jaw (chest drops) vs the lower jaw (rises)
CHOMP_ARMS = 9.0                      # arms flare on the bite (deg)
IDLE_BOB = 0.18                       # idle float bob of the upper body (units)
WALK_N = 33                           # glide frames 1..33 (32-frame loop, 1.33 s)
LEAN_DEG, LEAN_OSC = 7.0, 1.5         # "glide lean" forward + its oscillation (deg)
GLIDE_ROLL = 2.0                      # side drift (deg)
GLIDE_BOB = 0.25                      # float bob (units, 2 per loop)
ARM_TRAIL, ARM_FLUTTER = 14.0, 5.0    # arms trail back + flutter (deg)
CROWN_TRAIL = 6.0                     # crown/hat trails back like a flame (deg)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
# --out-root <dir>: a determinism TWIN build writes the same tree (improved/, rigged/) under <dir> instead of the project
OUT_ROOT = argv[argv.index("--out-root") + 1] if "--out-root" in argv else ROOT
for d_ in ("improved", "rigged"):
    os.makedirs(os.path.join(OUT_ROOT, d_), exist_ok=True)
OUT_IMPROVED = os.path.join(OUT_ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(OUT_ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(OUT_ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(OUT_ROOT, "improved", "textures")
# the normal-bake buffer for duskmaw_bake_diff.py (temp dir, never the project): main build 'main', twins by folder name
NORMAL_NPY = os.path.join(__import__("tempfile").gettempdir(), "duskmaw_normal_%s.npy" %
                          ("main" if OUT_ROOT == ROOT else os.path.basename(os.path.normpath(OUT_ROOT))))
report = {"unit": UNIT, "version": "v2 shadow figure", "conquest_character_id": CHAR_ID, "source": bpy.data.filepath,
          "tier": "hero", "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0, "overrides": OVERRIDES}
scene = bpy.context.scene


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V, float).tolist(), [], F)
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


def keep_main_island(V, F, min_frac):
    """Drop connected pieces smaller than min_frac of the vertices (sculpt crumbs / SDF specks)."""
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
    lab, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_lab = cnt >= min_frac * n
    keep_v = keep_lab[inv]
    remap = -np.ones(n, dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    order_ = np.argsort(-cnt, kind="stable")
    pieces_ = [{"verts": int(cnt[k]), "kept": bool(keep_lab[k]), "bbox": [V[inv == k].min(0).round(2).tolist(), V[inv == k].max(0).round(2).tolist()]}
               for k in order_[:6]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum()),
                           "largest_dropped": int(cnt[~keep_lab].max()) if (~keep_lab).any() else 0, "largest_pieces": pieces_}


def circ_smooth(a, w):
    if w <= 0:
        return a.copy()
    k = np.ones(2 * w + 1) / (2 * w + 1)
    return np.convolve(np.concatenate([a[-w:], a, a[:w]]), k, mode="valid")


# =========================================================================== 1. sculpt -> origin
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
src = bpy.data.objects["Sphere"]
SV, SF = mesh_arrays(src.data, src.matrix_world)
report["tris_source"] = tri_count(src.data)
SV, SF, crumbs = keep_main_island(SV, SF, 0.01)
report["source_cleanup"] = crumbs
lo, hi = SV.min(0), SV.max(0)
SHIFT = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
SV = SV - SHIFT
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
# the waist axis: centre of the throat column between the skirt and the chest (sculpt)
_rs = np.hypot(SV[:, 0], SV[:, 1])
_col = (SV[:, 2] > 6.2) & (SV[:, 2] < 8.9) & (_rs < 2.0)
AX = SV[_col, :2].mean(0)
report["waist_axis_sculpt_frame"] = AX.round(4).tolist()

# =========================================================================== 2. SDF rebuild
t_sdf = time.time()
NT = 144
TH = np.linspace(-math.pi, math.pi, NT, endpoint=False)
dS = SV[:, :2] - AX
rS = np.hypot(dS[:, 0], dS[:, 1]); thS = np.arctan2(dS[:, 0], -dS[:, 1])
HANDS_S = (rS > HAND_R[0]) & (np.abs(dS[:, 1]) < HAND_Y) & (SV[:, 2] > HAND_ZLOW)


def section_r(z0, z1, rmax):
    m = (SV[:, 2] >= z0) & (SV[:, 2] < z1) & (rS < rmax) & ~HANDS_S
    b = ((thS[m] + math.pi) / (2 * math.pi) * NT).astype(int) % NT
    out = np.zeros(NT)
    np.maximum.at(out, b, rS[m])
    return out


def ss1(e0, e1, x):
    return float(smoothstep(e0, e1, x))


OPEN_W = 1.0 - smoothstep(math.radians(THETA_OPEN - 10), math.radians(THETA_OPEN + 10), np.abs(TH))
BACK_W = smoothstep(math.radians(THETA_C - D_BLEND), math.radians(THETA_C + D_BLEND), np.abs(TH))
PINCH_T = PINCH + (PINCH_B - PINCH) * smoothstep(math.radians(PINCH_TH[0]), math.radians(PINCH_TH[1]), np.abs(TH))


def lobe_mix(z0, z1, inset):
    raw = section_r(z0, z1, 10.0)
    return (circ_smooth(raw, 1) - inset) * (1 - BACK_W) + (circ_smooth(raw, CORE_W) - inset) * BACK_W


R_bt = lobe_mix(Z_CUT, Z_BT, BT_INSET)
R_cut = lobe_mix(Z_CUT - 0.3, Z_CUT + 0.3, -CUT_OUTSET)
R_gnd = (1 - BASE_ROUND) * R_bt * (1 + FLARE) + BASE_ROUND * R_bt.mean() * (1 + FLARE)
R_w0 = lobe_mix(Z_W0, Z_W1, BT_INSET)
R_ch = circ_smooth(section_r(Z_TOP - 0.3, Z_TOP + 0.3, 9.3), 3) - CH_INSET


def R_body(z):
    """procedural lower body radius per angle bin at height z (sculpt frame)"""
    if z <= Z_CUT:
        zc = max(z, HEM_R)
        w = ((Z_CUT - zc) / (Z_CUT - HEM_R)) ** FLARE_POW
        R = R_cut + (R_gnd - R_cut) * w
        if z < HEM_R:                                   # rounded hem: a quarter circle at the floor edge
            R = R - HEM_R + math.sqrt(max(HEM_R ** 2 - (HEM_R - max(z, 0.0)) ** 2, 0.0))
        return R
    if z <= Z_BT:
        t = ss1(Z_CUT, Z_BT, z)
        return R_cut * (1 - t) + R_bt * t
    if z <= Z_W0:
        t = ss1(Z_BT, Z_W0, z)
        return R_bt * (1 - t) + R_w0 * t
    tl = (z - Z_W0) / (Z_TOP - Z_W0)
    t = ss1(0.0, 1.0, tl)
    fi = FRONT_INSET * ss1(0.0, 0.12, tl)
    return (R_w0 * (1 - t) + R_ch * t) * (1 - PINCH_T * math.sin(math.pi * min(tl, 1.0)) ** 2) * (1 - fi * OPEN_W)


def R_inner(z):
    return (R_body(z) - T_WALL) * (1 - OPEN_W) + 14.0 * OPEN_W


def radial_solid(Rfun, zs):
    rings = []
    for z in zs:
        R = Rfun(z)
        rings.append(np.stack([AX[0] + R * np.sin(TH), AX[1] - R * np.cos(TH), np.full(NT, z)], 1))
    V = np.concatenate(rings + [np.array([[AX[0], AX[1], zs[0]], [AX[0], AX[1], zs[-1]]])])
    F = []
    nr = len(zs)
    for k in range(nr - 1):
        for i in range(NT):
            a, b = k * NT + i, k * NT + (i + 1) % NT
            F.append([a, b, b + NT, a + NT])
    cb, ct = nr * NT, nr * NT + 1
    for i in range(NT):
        F.append([cb, (i + 1) % NT, i])
        F.append([ct, (nr - 1) * NT + i, (nr - 1) * NT + (i + 1) % NT])
    return V, F


def ellipsoid(c, ab, nu=64, nv=40):
    V, F = [], []
    for j in range(1, nv):
        ph = math.pi * j / nv
        for i in range(nu):
            t = 2 * math.pi * i / nu
            V.append((c[0] + ab[0] * math.sin(ph) * math.cos(t), c[1] + ab[1] * math.sin(ph) * math.sin(t), c[2] + ab[2] * math.cos(ph)))
    V += [(c[0], c[1], c[2] + ab[2]), (c[0], c[1], c[2] - ab[2])]
    top, bot = len(V) - 2, len(V) - 1
    for j in range(nv - 2):
        for i in range(nu):
            a, b = j * nu + i, j * nu + (i + 1) % nu
            F.append([a, a + nu, b + nu, b])
    for i in range(nu):
        F.append([top, i, (i + 1) % nu])
        F.append([bot, (nv - 2) * nu + (i + 1) % nu, (nv - 2) * nu + i])
    return V, F


def cylinder(c, rad, z0, z1, nu=64):
    V = [(c[0] + rad * math.cos(2 * math.pi * i / nu), c[1] + rad * math.sin(2 * math.pi * i / nu), z) for z in (z0, z1) for i in range(nu)]
    V += [(c[0], c[1], z0), (c[0], c[1], z1)]
    F = [[i, (i + 1) % nu, nu + (i + 1) % nu, nu + i] for i in range(nu)]
    F += [[2 * nu, (i + 1) % nu, i] for i in range(nu)] + [[2 * nu + 1, nu + i, nu + (i + 1) % nu] for i in range(nu)]
    return V, F


def param_box(fun, nu, nv, nw):
    """closed quad surface of the (u, v, w) unit box mapped through fun"""
    V, F, idx = [], [], {}

    def vid(i, j, k):
        key = (i, j, k)
        if key not in idx:
            idx[key] = len(V); V.append(fun(i / nu, j / nv, k / nw))
        return idx[key]
    for (a, b, na, nb, fix, val, flip) in (("u", "v", nu, nv, "w", 0, True), ("u", "v", nu, nv, "w", nw, False),
                                           ("u", "w", nu, nw, "v", 0, False), ("u", "w", nu, nw, "v", nv, True),
                                           ("v", "w", nv, nw, "u", 0, True), ("v", "w", nv, nw, "u", nu, False)):
        for i in range(na):
            for j in range(nb):
                q = []
                for (di, dj) in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    co_ = {a: i + di, b: j + dj, fix: val}
                    q.append(vid(co_["u"], co_["v"], co_["w"]))
                F.append(q[::-1] if flip else q)
    return V, F


TH_D0 = math.radians(THETA_C - D_BLEND)


def d_fun(u, v, w):
    th = TH_D0 + u * (2 * math.pi - 2 * TH_D0)
    th = (th + math.pi) % (2 * math.pi) - math.pi
    z = (Z_CUT - 0.1) + w * (D_TOP - (Z_CUT - 0.1))
    i = int(round((th + math.pi) / (2 * math.pi) * NT)) % NT
    full = ss1(TH_D0, math.radians(THETA_C + D_BLEND), abs(th))
    rin = (R_body(z)[i] - 0.05) * (1 - full) + D_RMIN * full
    rr = rin + v * (D_RMAX - rin)
    return (AX[0] + rr * math.sin(th), AX[1] - rr * math.cos(th), z)


def obj_tmp(name, V, F):
    m = bpy.data.meshes.new(name); m.from_pydata([tuple(map(float, v)) for v in V], [], F); m.update(); m.validate()
    o = bpy.data.objects.new(name, m); scene.collection.objects.link(o); o.hide_render = True
    return o


CAVW = (AX[0] + CAV[0], AX[1] + CAV[1], CAV[2])          # throat ellipsoid centre, sculpt frame
zs_body = np.concatenate([[-0.3 * VOX], np.linspace(0.0, HEM_R, 8)[1:], np.linspace(HEM_R, Z_W0, 40)[1:], np.linspace(Z_W0, Z_TOP, 40)[1:]])
tmp_objs = [obj_tmp("sdf_sculpt", SV, SF), obj_tmp("sdf_body", *radial_solid(R_body, zs_body)),
            obj_tmp("sdf_cav", *ellipsoid(CAVW, CAV_R)), obj_tmp("sdf_inner", *radial_solid(R_inner, np.linspace(Z_W0, Z_TOP + 1.0, 40))),
            obj_tmp("sdf_legcut", *cylinder(AX, 16.0, -2.0, Z_CUT)), obj_tmp("sdf_backcut", *param_box(d_fun, 90, 6, 24))]
o_s, o_b, o_c, o_i, o_x, o_d = tmp_objs
ng = bpy.data.node_groups.new("duskmaw_sdf", "GeometryNodeTree")
ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
NN = ng.nodes
g_out = NN.new("NodeGroupOutput")


def sdf(o):
    oi = NN.new("GeometryNodeObjectInfo"); oi.inputs["Object"].default_value = o; oi.transform_space = "ORIGINAL"
    ms = NN.new("GeometryNodeMeshToSDFGrid"); ms.inputs["Voxel Size"].default_value = VOX; ms.inputs["Band Width"].default_value = SDF_BAND
    ng.links.new(oi.outputs["Geometry"], ms.inputs["Mesh"])
    return ms.outputs["SDF Grid"]


def sdf_bool(op, g1, g2s):
    b = NN.new("GeometryNodeSDFGridBoolean"); b.operation = op
    multi = [i for i in b.inputs if i.is_multi_input and i.enabled][0]
    ng.links.new(g1, b.inputs["Grid 1"] if op == "DIFFERENCE" else multi)
    for g in g2s:
        ng.links.new(g, multi)
    return b.outputs["Grid"]


def nmath(op, x, y=None):
    m = NN.new("ShaderNodeMath"); m.operation = op
    ng.links.new(x, m.inputs[0])
    if y is not None:
        if isinstance(y, (int, float)):
            m.inputs[1].default_value = float(y)
        else:
            ng.links.new(y, m.inputs[1])
    return m.outputs[0]


def nband(src_, a, b, c, d):
    """smoothstep up over [a, b], down over [c, d] (field)"""
    def mr(lo_, hi_, inv):
        m = NN.new("ShaderNodeMapRange"); m.interpolation_type = "SMOOTHSTEP"; m.clamp = True
        ng.links.new(src_, m.inputs["Value"])
        m.inputs["From Min"].default_value = lo_; m.inputs["From Max"].default_value = hi_
        m.inputs["To Min"].default_value = 1.0 if inv else 0.0; m.inputs["To Max"].default_value = 0.0 if inv else 1.0
        return m.outputs["Result"]
    return nmath("MULTIPLY", mr(a, b, False), mr(c, d, True))


A_ = sdf_bool("DIFFERENCE", sdf(o_s), [sdf(o_x), sdf(o_d)])                # sculpt minus legs minus back teeth
C_ = sdf_bool("INTERSECT", sdf(o_c), [sdf(o_i)])                            # the throat: ellipsoid inside the body walls
W_ = sdf_bool("DIFFERENCE", sdf(o_b), [C_])                                 # procedural lower body, throat carved
U_ = sdf_bool("UNION", A_, [W_])
mean_ = NN.new("GeometryNodeSDFGridMean"); mean_.inputs["Width"].default_value = SM_W; mean_.inputs["Iterations"].default_value = SM_IT
ng.links.new(U_, mean_.inputs["Grid"])
pos = NN.new("GeometryNodeInputPosition")
sep = NN.new("ShaderNodeSeparateXYZ"); ng.links.new(pos.outputs[0], sep.inputs[0])
X_, Y_, Z_ = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
yf = nmath("SUBTRACT", Y_, float(FACE_AX_Y))
rad_f = nmath("SQRT", nmath("ADD", nmath("MULTIPLY", X_, X_), nmath("MULTIPLY", yf, yf)))
xa, ya = nmath("SUBTRACT", X_, float(AX[0])), nmath("SUBTRACT", Y_, float(AX[1]))
rad_a = nmath("SQRT", nmath("ADD", nmath("MULTIPLY", xa, xa), nmath("MULTIPLY", ya, ya)))
th_a = nmath("ABSOLUTE", nmath("DEGREES", nmath("ARCTAN2", xa, nmath("MULTIPLY", ya, -1.0))))
m_face = nmath("MULTIPLY", nband(Z_, *FACE_Z), nband(rad_f, -1.0, -0.5, *FACE_RAD))
m_low = nmath("MULTIPLY", nband(Z_, *LOW_Z), nband(rad_a, -1.0, -0.5, *LOW_RAD))
m_w = nmath("MULTIPLY", nmath("MULTIPLY", nband(th_a, WAIST_TH[0], WAIST_TH[1], 400.0, 401.0), nband(Z_, *WAIST_Z)), float(WAIST_K))
m_w = nmath("MULTIPLY", m_w, nband(rad_a, -1.0, -0.5, *WAIST_RAD))
mask = nmath("MAXIMUM", nmath("MAXIMUM", m_face, m_low), m_w)
s1 = NN.new("GeometryNodeSampleGrid"); ng.links.new(U_, s1.inputs["Grid"]); ng.links.new(pos.outputs[0], s1.inputs["Position"])
s2 = NN.new("GeometryNodeSampleGrid"); ng.links.new(mean_.outputs["Grid"], s2.inputs["Grid"]); ng.links.new(pos.outputs[0], s2.inputs["Position"])
mix = NN.new("ShaderNodeMix"); mix.data_type = "FLOAT"; mix.clamp_factor = True
ng.links.new(mask, mix.inputs["Factor"]); ng.links.new(s1.outputs["Value"], mix.inputs[2]); ng.links.new(s2.outputs["Value"], mix.inputs[3])
ftg = NN.new("GeometryNodeFieldToGrid"); ftg.grid_items.new("FLOAT", "sdf")
ng.links.new(sdf_bool("UNION", U_, [mean_.outputs["Grid"]]), ftg.inputs["Topology"]); ng.links.new(mix.outputs[0], ftg.inputs["sdf"])
g2m = NN.new("GeometryNodeGridToMesh"); g2m.inputs["Threshold"].default_value = 0.0; g2m.inputs["Adaptivity"].default_value = 0.0
ng.links.new(ftg.outputs["sdf"], g2m.inputs["Grid"])
ng.links.new(g2m.outputs["Mesh"], g_out.inputs[0])
host = obj_tmp("sdf_host", [], [])
md_ = host.modifiers.new("sdf", "NODES"); md_.node_group = ng
HV, HF = evaluated_arrays(host)
for o in tmp_objs + [host]:
    me_ = o.data
    bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.meshes.remove(me_)
bpy.data.node_groups.remove(ng)
n_raw = len(HV)
HV, HF, hspecks = keep_main_island(HV, HF, 0.5)         # the outer shell only (see report: inner shells + specks dropped)


def signed_volume(V, F):
    vol = 0.0
    for f in F:
        a = V[f[0]]
        for k in range(1, len(f) - 1):
            vol += float(np.dot(a, np.cross(V[f[k]], V[f[k + 1]]))) / 6.0
    return vol


vol_h = signed_volume(HV, HF)
if vol_h < 0:                                             # grid-to-mesh winds the shell inward: flip to outward normals
    HF = [f[::-1] for f in HF]
report["sdf_rebuild"] = {"method": "geometry-node SDF grids: (sculpt - legs(z<Z_CUT) - back teeth/skirt(|theta|>THETA_C)) U "
                                   "(procedural lower body - throat(ellipsoid x body interior, open |theta|<THETA_OPEN)); masked "
                                   "SDF mean (face / lower body / back waist) mixed in by Field-to-Grid; grid -> mesh",
                         "voxel": VOX, "band_voxels": SDF_BAND, "raw_verts": n_raw,
                         "inner_shells_note": "Field-to-Grid writes only the band voxels, so thick parts get an inverted inner "
                                              "shell ~SDF_BAND voxels inside; it never touches the outer surface and is dropped "
                                              "with the specks (only the largest shell is kept)", "high_faces": len(HF), "specks_dropped": hspecks,
                         "seconds": round(time.time() - t_sdf, 1), "winding_flipped": bool(vol_h < 0), "enclosed_volume": round(abs(vol_h), 3),
                         "profiles_sculpt_frame": {"R_ground_min_max": [round(float(R_gnd.min()), 3), round(float(R_gnd.max()), 3)],
                                                   "R_waist_mid_min_max": [round(float(R_body(0.5 * (Z_W0 + Z_TOP)).min()), 3),
                                                                           round(float(R_body(0.5 * (Z_W0 + Z_TOP)).max()), 3)]}}
print("SDF", json.dumps(report["sdf_rebuild"]))

# =========================================================================== 3. low: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, LOW_TRIS / tri_count(tmp.data))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_main_island(LV, LF, 0.01)
lo2, hi2 = LV.min(0), LV.max(0)
S2 = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
LV = LV - S2; HV = HV - S2; SV = SV - S2
AX = AX - S2[:2]
CAVW = (CAVW[0] - S2[0], CAVW[1] - S2[1], CAVW[2] - S2[2])
ZOFF = -S2[2]                                    # sculpt-frame heights -> final frame: z + ZOFF
SHIFT = SHIFT + S2
HIGH = new_obj(UNIT + "_high", HV, HF)
size = LV.max(0) - LV.min(0)
H = float(size[2])
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["natural"] = {"height": round(H, 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "footprint": round(fp, 4), "units": "sculpt units (natural proportions, no refit)",
                     "origin_shift_sculpt_units": SHIFT.round(5).tolist(),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint",
                                                     "ceilings": [CELL_MAX_H, CELL_MAX_FP]}}
report["retopo"] = {"method": "collapse decimation of the SDF surface (deterministic) -> main shell",
                    "decimated_tris": int(sum(len(f) - 2 for f in LF)), "specks_dropped": specks, "seconds": round(time.time() - t, 1)}
bvh_high = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_high.find_nearest(Vector(p))[3] for p in LV])
report["retopo"]["low_to_high_distance"] = {"mean": round(float(dev.mean()), 4), "p99": round(float(np.percentile(dev, 99)), 4),
                                            "max": round(float(dev.max()), 4), "pct_of_height_p99": round(100 * float(np.percentile(dev, 99)) / H, 3)}

# ---- eye patch refinement (crisp eye outlines on the smooth face)
t = time.time()


def bm_from(V, F):
    """bmesh from arrays; exact duplicate faces (decimation leftovers) are skipped and counted"""
    b_ = bmesh.new()
    for p in V:
        b_.verts.new(p)
    b_.verts.ensure_lookup_table()
    dup = 0
    for f in F:
        try:
            b_.faces.new([b_.verts[i] for i in f])
        except ValueError:
            dup += 1
    b_.verts.index_update()
    return b_, dup


bm, dup0 = bm_from(LV, LF)
bm.normal_update()


def eye_field(x, zsc):
    """front-projected eye lens field (> 0 inside): |x| mirrored about the waist axis, zsc in the sculpt frame"""
    x = np.abs(np.asarray(x, float) - AX[0]); zsc = np.asarray(zsc, float)
    ux = np.array(EYE_OUT) - np.array(EYE_IN); Le = float(np.linalg.norm(ux)); ux = ux / Le
    vx = np.array([-ux[1], ux[0]])
    vx = -vx if vx[1] < 0 else vx
    pe_ = np.stack([x - EYE_IN[0], zsc - EYE_IN[1]], -1)
    u = pe_ @ ux; v = pe_ @ vx
    se = np.clip(u / Le, 0.0, 1.0)
    f_ = np.minimum(EYE_TOP * np.sin(math.pi * se) ** EYE_POW[0] - v, v + EYE_BOT * np.sin(math.pi * se) ** EYE_POW[1])
    return np.where((u > 0) & (u < Le), f_, -np.minimum(np.abs(u), np.abs(u - Le)) - 0.01)


def grin_field(x, zsc):
    x = np.abs(np.asarray(x, float) - AX[0]); zsc = np.asarray(zsc, float)
    gs_ = np.clip(x / GRIN_W, 0.0, 1.0)
    return np.where(x < GRIN_W, GRIN_Z[1] * (1 - gs_ ** 3) - np.abs(zsc - (GRIN_Z[0] + GRIN_Z[2] * gs_ ** 2)), -(x - GRIN_W) - 0.01)


def in_eye_box(f):
    c = f.calc_center_median()
    if not (abs(c.x - AX[0]) < EYE_BOX[0] and EYE_BOX[1] + ZOFF < c.z < EYE_BOX[2] + ZOFF and f.normal.y < -0.1 and c.y < AX[1] - 1.0):
        return False
    return max(float(eye_field(c.x, c.z - ZOFF)), float(grin_field(c.x, c.z - ZOFF))) > -EYE_NEAR


n_before = len(bm.faces)
for it in range(6):
    faces = [f for f in bm.faces if in_eye_box(f)]
    edges = sorted({e for f in faces for e in f.edges if e.calc_length() > EYE_EDGE}, key=lambda e: e.index)
    if not edges:
        break
    bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=False)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.edges.index_update(); bm.faces.index_update()
    bm.normal_update()
moved = 0
for v in bm.verts:
    if v.index >= len(LV):
        loc = bvh_high.find_nearest(v.co)[0]
        if loc is not None:
            v.co = loc; moved += 1
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["eye_refinement"] = {"duplicate_faces_skipped": dup0, "iterations": it, "faces_added": len(LF) - n_before, "verts_projected": moved,
                            "edge_max": EYE_EDGE, "seconds": round(time.time() - t, 1)}

# =========================================================================== 4. region fields (per low vertex)
low0 = new_obj("fields_tmp", LV, LF)
M0 = K.MeshData(low0)
NV = np.empty(len(LV) * 3); low0.data.vertex_normals.foreach_get("vector", NV); NV = NV.reshape(-1, 3)
bpy.data.objects.remove(low0, do_unlink=True)
dxy = LV[:, :2] - AX
r = np.hypot(dxy[:, 0], dxy[:, 1])
th_deg = np.degrees(np.abs(np.arctan2(dxy[:, 0], -dxy[:, 1])))
zl = LV[:, 2]
zs_ = zl - ZOFF                                               # heights in the sculpt frame (the constants' frame)
elen = np.linalg.norm(LV[M0.ev[:, 0]] - LV[M0.ev[:, 1]], axis=1)
adj = [[] for _ in range(M0.n)]
for (a, b), l_ in zip(M0.ev, elen):
    adj[a].append((b, l_)); adj[b].append((a, l_))


def dijkstra(seeds, limit=np.inf):
    g_ = np.full(M0.n, np.inf)
    pq = [(0.0, int(v)) for v in seeds]
    for _, v in pq:
        g_[v] = 0.0
    heapq.heapify(pq)
    while pq:
        d_, v = heapq.heappop(pq)
        if d_ > g_[v] or d_ > limit:
            continue
        for u, l_ in adj[v]:
            nd = d_ + l_
            if nd < g_[u]:
                g_[u] = nd; heapq.heappush(pq, (nd, u))
    return g_


def persistence(fv, mask, min_pers):
    """0-dim persistence of the maxima of fv over the mesh graph restricted to mask (v1 build). Returns per vertex the
    owning LIVE tip (-1 outside mask) and that tip's persistence; basins below min_pers fold into their merge target."""
    idx = np.nonzero(mask)[0]
    order = idx[np.argsort(-fv[idx], kind="stable")]
    par = -np.ones(M0.n, dtype=np.int64)
    bas_max, own, merged = {}, -np.ones(M0.n, dtype=np.int64), {}

    def uf(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for v in order:
        roots_ = {uf(u) for u, _ in adj[v] if par[u] >= 0}
        par[v] = v
        if not roots_:
            bas_max[v] = v; own[v] = v
            continue
        rl = sorted(roots_, key=lambda q: -fv[bas_max[q]])
        keep = rl[0]
        own[v] = bas_max[keep]
        for q in rl[1:]:
            merged[bas_max[q]] = (bas_max[keep], float(fv[v]))
            par[q] = keep
        par[v] = keep
    pers_ = {tp: float(fv[tp]) - sg for tp, (_, sg) in merged.items()}
    for tp in set(bas_max.values()) - set(merged):
        pers_[tp] = float(fv[tp] - fv[idx].min())

    def live_tip(tp):
        while pers_[tp] < min_pers and tp in merged:
            tp = merged[tp][0]
        return tp
    cache = {}
    tipv = -np.ones(M0.n, dtype=np.int64)
    for v in idx:
        o = int(own[v])
        if o not in cache:
            cache[o] = live_tip(o)
        tipv[v] = cache[o]
    plen = np.array([pers_[int(tp)] if tp >= 0 else 0.0 for tp in tipv])
    return tipv, plen, pers_


def burn_field(fv, tipv, plen, frac, clamp_, min_pers, valid=None):
    ok = (tipv >= 0) & (plen >= min_pers)
    if valid is not None:
        ok &= valid
    burn_ = np.clip(frac * plen, clamp_[0], clamp_[1])
    f_ = np.where(ok, fv - (fv[np.maximum(tipv, 0)] - burn_), -1.0)
    return f_, np.where(ok, plen, 0.0), sorted({int(tp) for tp in tipv[ok]})


def tip_rows(tl, plen_d):
    return [[round(float(v), 2) for v in LV[i]] + [round(plen_d[i], 2)] for i in sorted(tl, key=lambda q: -plen_d[q])]


t = time.time()
# ---- spike tips (v1 rule: geodesic distance from a torso ring; persistence per spike)
ring = (np.abs(zl - SEED_Z_FRAC * H) < 0.35) & (r < 0.25 * H)
g = dijkstra(np.nonzero(ring)[0])
g[~np.isfinite(g)] = 0.0
spk, spk_plen, spk_pers = persistence(g, np.ones(M0.n, bool), MIN_SPIKE)
tipf, spk_len, tips = burn_field(g, spk, spk_plen, TIP_LEN, TIP_ABS, MIN_SPIKE,
                                 valid=(r[np.maximum(spk, 0)] > SPIKE_RMIN * H) & (zs_[np.maximum(spk, 0)] > BASE_Z + 1.0))
if not SPIKE_TIPS:
    tipf[:] = -1.0; spk_len[:] = 0.0; tips = []
# ---- teeth: height persistence on the SCULPT-derived jaw surfaces in the open front
bvh_s = BVHTree.FromPolygons(SV.tolist(), SF)
dsc = np.array([bvh_s.find_nearest(Vector(p))[3] for p in LV])
sculpt_ok = dsc < SCULPT_TOL
front_ok = (th_deg < TEETH_TH) & (r > THROAT_R) & (r < JAW_RMAX)
up_m = sculpt_ok & front_ok & (zs_ > UP_JAW_Z[0]) & (zs_ < UP_JAW_Z[1])
seed_up = np.nonzero(up_m & (zs_ > 10.5))[0]
upper = np.zeros(M0.n, bool)
stack = list(seed_up); upper[seed_up] = True
while stack:
    v = stack.pop()
    for u, _ in adj[v]:
        if up_m[u] and not upper[u]:
            upper[u] = True; stack.append(u)
low_m = sculpt_ok & front_ok & (zs_ > LO_JAW_Z[0]) & (zs_ < LO_JAW_Z[1]) & ~upper
lo_tip, lo_plen, lo_pers = persistence(zl, low_m, MIN_TOOTH)
up_tip, up_plen, up_pers = persistence(-zl, upper, MIN_TOOTH)
tlo, tlo_len, teeth_lo = burn_field(zl, lo_tip, lo_plen, TOOTH_LEN, TOOTH_ABS, MIN_TOOTH)
tup, tup_len, teeth_up = burn_field(-zl, up_tip, up_plen, TOOTH_LEN, TOOTH_ABS, MIN_TOOTH)
# ---- throat membership: inside the carved ellipsoid and inside the body walls
e_val = ((LV[:, 0] - CAVW[0]) / CAV_R[0]) ** 2 + ((LV[:, 1] - CAVW[1]) / CAV_R[1]) ** 2 + ((LV[:, 2] - CAVW[2]) / CAV_R[2]) ** 2
bins_ = ((np.arctan2(dxy[:, 0], -dxy[:, 1]) + math.pi) / (2 * math.pi) * NT).astype(int) % NT
Rin_v = np.array([R_inner(float(z_))[b_] for z_, b_ in zip(np.clip(zs_, Z_W0, Z_TOP + 1.0), bins_)])
def cav_field(P):
    """> 0 inside the mouth. CONTINUOUS (min of smooth terms) so its iso-cut is a clean line: a hard -1 step here
    put the cut at arbitrary edge fractions (the ragged lip edge of the first v2 preview)."""
    d_ = P[:, :2] - AX
    r_ = np.hypot(d_[:, 0], d_[:, 1])
    z_s = P[:, 2] - ZOFF
    e_ = ((P[:, 0] - CAVW[0]) / CAV_R[0]) ** 2 + ((P[:, 1] - CAVW[1]) / CAV_R[1]) ** 2 + ((P[:, 2] - CAVW[2]) / CAV_R[2]) ** 2
    zq = np.clip(z_s, Z_W0, Z_TOP + 1.0)
    zi = np.clip(np.round((zq - Z_W0) / (Z_TOP + 1.0 - Z_W0) * (len(RIN_TAB) - 1)).astype(int), 0, len(RIN_TAB) - 1)
    bi = ((np.arctan2(d_[:, 0], -d_[:, 1]) + math.pi) / (2 * math.pi) * NT).astype(int) % NT
    f_ = np.minimum(1.0 + CAV_TOL - e_, 0.5 * (RIN_TAB[zi, bi] + 0.2 - r_))
    f_ = np.minimum(f_, np.minimum(z_s - (Z_W0 - 0.6), Z_TOP + 0.5 - z_s))
    return np.maximum(f_, -0.99)


RIN_TAB = np.array([R_inner(float(z_)) for z_ in np.linspace(Z_W0, Z_TOP + 1.0, 97)])
cavf = cav_field(LV)
in_cav_v = cavf > 0
# ---- red lip: Euclidean distance to the mouth's inside, sampled on the dense rebuilt HIGH surface (smooth band edge;
# graph distances on the low are edge-path jaggy), front only
cav_h = HV[cav_field(HV) > 0]
kd_cav = KDTree(len(cav_h))
for i, p in enumerate(cav_h):
    kd_cav.insert(p, i)
kd_cav.balance()
dcav = np.array([kd_cav.find(p)[2] for p in LV]) if len(cav_h) else np.full(M0.n, 99.0)
dcav = np.where(in_cav_v, 0.0, np.minimum(dcav, 3.0 * LIP_W))
lipf = LIP_W - dcav - 0.3 * np.maximum(th_deg - LIP_TH, 0.0) - 3.0 * np.maximum(4.5 - zs_, 0.0) - 3.0 * np.maximum(zs_ - 11.8, 0.0)
lipf = np.maximum(lipf, -0.99)                              # continuous everywhere: the iso-cut draws every edge of the band
# ---- eyes + grin (front-projected (x, z) fields on the face)
face_front = (NV[:, 1] < -0.1) & (LV[:, 1] < AX[1] - 1.0) & (zs_ > EYE_BOX[1]) & (zs_ < EYE_BOX[2])
eyef = np.where(face_front, np.maximum(eye_field(LV[:, 0], zs_), -0.99), -1.0)
grinf = np.where(face_front, np.maximum(grin_field(LV[:, 0], zs_), -0.99), -1.0)
report["fields_seconds"] = round(time.time() - t, 1)

# =========================================================================== iso-contour cuts
bm, dup1 = bm_from(LV, LF)
FIELDS = {"z": zl, "r": r, "th": th_deg, "tip": tipf, "gtip": spk_len, "tlo": tlo, "glo": tlo_len, "tup": tup, "gup": tup_len,
          "cav": cavf, "yb": LV[:, 1] - AX[1] - THROAT_BACK, "lip": lipf, "eye": eyef, "grin": grinf, "ady": np.abs(LV[:, 1] - AX[1])}
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
    nsplit = 0
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
        nsplit += 1
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
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": nsplit, "face_connects": len(pairs)}


pos_ = lambda k: (lambda a, b: a[LAY[k]] > -0.999 and b[LAY[k]] > -0.999)
spike = lambda a, b: a[LAY["gtip"]] > MIN_SPIKE and b[LAY["gtip"]] > MIN_SPIKE
tooth_lo = lambda a, b: a[LAY["glo"]] >= MIN_TOOTH and b[LAY["glo"]] >= MIN_TOOTH
tooth_up = lambda a, b: a[LAY["gup"]] >= MIN_TOOTH and b[LAY["gup"]] >= MIN_TOOTH
not_hand = lambda a, b: all(not (v[LAY["r"]] > HAND_R[0] and v[LAY["ady"]] < HAND_Y) for v in (a, b))
in_mouth = lambda a, b: a[LAY["cav"]] > -1e-4 and b[LAY["cav"]] > -1e-4     # (cav is continuous: cut only inside the mouth)
CUTS = [("z", BASE_Z + ZOFF, not_hand), ("z", 0.9 * H, None), ("tip", 0.0, spike), ("cav", 0.0, pos_("cav")),
        ("yb", 0.0, in_mouth), ("r", THROAT_R, in_mouth),
        ("lip", 0.0, pos_("lip")), ("tlo", 0.0, tooth_lo), ("tup", 0.0, tooth_up), ("eye", 0.0, pos_("eye")), ("grin", 0.0, pos_("grin"))]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.faces.index_update()
nf = len(bm.faces)
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1),
                      "rule": "edge split at the field's iso-value (snap within CUT_SNAP of a vertex) + face connects (v1 machinery)"}
low_me = bpy.data.meshes.new(UNIT)
bm.to_mesh(low_me)
bm.free()
low = bpy.data.objects.new(UNIT, low_me)
scene.collection.objects.link(low)
me = low.data
for nm in list(me.attributes.keys()):
    if nm in LAY:
        me.attributes.remove(me.attributes[nm])
report["tris_final"] = tri_count(me)
assert report["tris_final"] == nf

# =========================================================================== regions
REG = ["body", "base", "tips", "lip", "maw_inner", "maw_throat", "teeth", "eyes", "grin"]
R_ = {n: i for i, n in enumerate(REG)}
zc, rc = FVAL["z"], FVAL["r"]
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
rid = np.full(nf, R_["body"], dtype=np.int32)
hand_c = (rc > HAND_R[0]) & (FVAL["ady"] < HAND_Y)
rid[(zc < BASE_Z + ZOFF) & ~hand_c] = R_["base"]
rid[((FVAL["tip"] > 0.0) & (FVAL["gtip"] > MIN_SPIKE)) | (zc > 0.9 * H)] = R_["tips"]
rid[FVAL["lip"] > 0.0] = R_["lip"]
in_cav = FVAL["cav"] > 0.0
rid[in_cav] = R_["maw_inner"]
behind = FVAL["yb"] > 0.0
rid[in_cav & (behind | (rc < THROAT_R))] = R_["maw_throat"]
teeth = ((FVAL["tlo"] > 0.0) & (FVAL["glo"] >= MIN_TOOTH)) | ((FVAL["tup"] > 0.0) & (FVAL["gup"] >= MIN_TOOTH))
rid[teeth] = R_["teeth"]
rid[FVAL["eye"] > 0.0] = R_["eyes"]
rid[FVAL["grin"] > 0.0] = R_["grin"]
report["region_rule"] = {
    "base": "face height < BASE_Z (the shadow base; shade darkens to BASE_DARK at the floor), hands excluded",
    "tips": "outer TIP_LEN of every persistent spike (v1 geodesic-persistence rule) + the hat apex (> 0.9 H) -- dim embers",
    "lip": "outer surface within geodesic LIP_W of the mouth's inside, |theta| < LIP_TH -- the red frame of the devouring mouth",
    "maw_inner": "inside the carved throat ellipsoid (tolerance CAV_TOL) and the body walls: chest underside, floor, teeth insides",
    "maw_throat": "mouth faces behind the waist axis (+THROAT_BACK) or on the throat column (r < THROAT_R): the glowing throat",
    "teeth": "height-persistence maxima (lower jaw) / minima (upper jaw) of the SCULPT-derived jaw surfaces in the open front; "
             "outer TOOTH_LEN burns (ember teeth)",
    "eyes": "front-projected lens field on the smoothed face (EYE_IN -> EYE_OUT, EYE_TOP / EYE_BOT), iso-cut",
    "grin": "variant-only crescent under the eyes (default palette paints it as body)",
    "waist_axis": AX.round(4).tolist(), "throat_centre": [round(float(c), 4) for c in CAVW]}
report["tip_field"] = {"spikes_found": len(tips), "spikes_xyz_len": tip_rows(tips, spk_pers)[:24],
                       "lower_teeth_xyz_len": tip_rows(teeth_lo, lo_pers), "upper_teeth_xyz_len": tip_rows(teeth_up, up_pers),
                       "sculpt_derived_verts": int(sculpt_ok.sum()), "upper_jaw_verts": int(upper.sum()), "lower_jaw_verts": int(low_m.sum())}

# ---- cavity shade from the rebuilt high + deterministic per-face jitter + the base's floor darkening
t = time.time()
kd_h = KDTree(len(HV))
for i, p in enumerate(HV):
    kd_h.insert(p, i)
kd_h.balance()
hme = HIGH.data
ev_h = np.empty(len(hme.edges) * 2, dtype=np.int64); hme.edges.foreach_get("vertices", ev_h); ev_h = ev_h.reshape(-1, 2)
nv_h = np.empty(len(hme.vertices) * 3); hme.vertex_normals.foreach_get("vector", nv_h); nv_h = nv_h.reshape(-1, 3)
deg = np.bincount(ev_h.ravel(), minlength=len(HV)).astype(float)


def nmean(X):
    s_ = np.zeros_like(X)
    for k in range(X.shape[1]):
        s_[:, k] = np.bincount(ev_h[:, 0], X[ev_h[:, 1], k], minlength=len(HV)) + np.bincount(ev_h[:, 1], X[ev_h[:, 0], k], minlength=len(HV))
    return s_ / np.maximum(deg, 1)[:, None]


el = np.linalg.norm(HV[ev_h[:, 0]] - HV[ev_h[:, 1]], axis=1).mean()
cav = ((nmean(HV) - HV) * nv_h).sum(1) / el
for _ in range(6):
    cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
cav = np.clip(cav / (np.percentile(np.abs(cav), 95) + 1e-9), -1, 1)
fcav = np.array([np.mean([cav[j] for (_, j, _) in kd_h.find_n(p, 8)]) for p in FC])
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
bmask = rid == R_["base"]
shade[bmask] *= BASE_DARK + (1 - BASE_DARK) * smoothstep(0.0, BASE_Z + ZOFF, FC[bmask, 2])
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k
report["cavity_seconds"] = round(time.time() - t, 1)
fa = np.empty(nf); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == R_[n]].sum() / fa.sum()), 4) for n in REG}

# facing landmark (the mouth is the identity: "chest and bottom leg spikes form a mouth"): waist axis at mouth
# height -> area centroid of the red lip framing the opening; the eyes' centroid is reported beside it
lipm = rid == R_["lip"]
anchor = np.array([AX[0], AX[1], float(np.average(FC[lipm, 2], weights=fa[lipm]))])
landmark = np.average(FC[lipm], axis=0, weights=fa[lipm])
dvec = landmark - anchor
eyem = rid == R_["eyes"]
eye_c = np.average(FC[eyem], axis=0, weights=fa[eyem]) if eyem.any() else None
report["facing"] = {"rule": "waist axis (at the lip's mean height) -> area centroid of the red lip region that frames the mouth opening",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2),
                    "eyes_centroid": eye_c.round(4).tolist() if eye_c is not None else None}

# =========================================================================== flat + UV
me.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

# =========================================================================== material
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
PAL.apply_material(mat, pal_default)
me.materials.append(mat)
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "hero"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"
low["conquest_version"] = "duskmaw v2 (shadow figure)"

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris_final", "sdf_rebuild", "retopo", "eye_refinement", "regions_faces",
                                                             "regions_area_share", "facing", "tip_field")}))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake (normal + AO from the rebuilt high)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
# the NORMAL bake is not byte-deterministic (per-process Cycles tie-break, the vampito finding): two full v2 runs differed
# ONLY there (geometry / colour / UV / AO / rig identical), and a single-thread bake (threads_mode FIXED, 1) did not
# change that (tried 2026-09-26, reverted). duskmaw_run.ps1 -Determinism gates it with duskmaw_bake_diff.py instead.
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
assert (lt_ == 3).all(), "low must be all triangles"
UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for tri in UVn[mask_faces]:
        p = tri * res
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
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "normal_baked_pct_of_uv_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_dev_mean": round(float(devn[cov_n & alltex_n].mean()), 4),
    "ao_baked_pct_of_uv_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


bstats["pixel_sha"] = {"normal": sha(px[:, :3]), "ao": sha(pa[:, :1])}
np.save(NORMAL_NPY, px[:, :3])
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["high"] = "the SDF-rebuilt surface (%d faces)" % len(HF)
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGH.data
bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True

uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV after the iso cuts (66 deg, margin 0.004), repacked", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()), "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"], "skins": PAL.list_skins(UNIT)}


# ---- see-through proof (ray casts through the waist core, every 15 degrees round the figure)
def see_through(V, F, axis_xy, z_band=(6.0, 9.2), half_w=2.5, step=0.1):
    bvh = BVHTree.FromPolygons(np.asarray(V).tolist(), F)
    rows = {}
    for deg_ in range(0, 360, 15):
        a = math.radians(deg_)
        d = Vector((-math.sin(a), math.cos(a), 0.0))                  # the ray travels from the camera at yaw deg_ inward
        e1 = Vector((math.cos(a), math.sin(a), 0.0))
        start = Vector((axis_xy[0], axis_xy[1], 0.0)) - d * 60.0
        n_ = miss = 0
        for u in np.arange(-half_w, half_w + 1e-9, step):
            for z in np.arange(z_band[0], z_band[1] + 1e-9, step):
                o = start + e1 * float(u) + Vector((0, 0, float(z)))
                hit = bvh.ray_cast(o, d, 120.0)
                n_ += 1
                miss += hit[0] is None
        rows[deg_] = round(100.0 * miss / n_, 2)
    return rows


LFf = [list(p.vertices) for p in me.polygons]
st = see_through(LVf, LFf, AX, z_band=(6.0 + ZOFF, 9.2 + ZOFF))
report["see_through"] = {"rule": "rays through the waist core (|u| <= 2.5 units about the waist axis, z 6.0-9.2 = the mouth band) "
                                 "from 24 yaws (0 = the front camera, 180 = behind); value = % of rays that pass straight through",
                         "pct_rays_through_by_yaw": st, "max_pct": max(st.values())}
print("SEE_THROUGH", json.dumps(report["see_through"]))
bpy.context.preferences.filepaths.save_version = 0
set_tex_paths("//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig (jawed totem)
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS, "v1_note": "v1's carried shipped clips (attack/hit/death) are dropped "
       "from v2 (artist scope: idle + locomotion); they remain in git history with the v1 build"}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
Mw = K.MeshData(low)
W_ = Mw.W
lo_n, hi_n = W_.min(0), W_.max(0)
H_new = float(hi_n[2] - lo_n[2])
Wd = float(hi_n[0] - lo_n[0])
cx, cy = float(AX[0]), float(AX[1])                       # the rig stands on the waist axis
zf = lambda f: lo_n[2] + H_new * f
BONES = [("base", (cx, cy, zf(BASE_BONE[0])), (cx, cy, zf(BASE_BONE[1])), "root", False),
         ("sway", (cx, cy, SWAY_Z[0] + ZOFF), (cx, cy, SWAY_Z[1] + ZOFF), "base", False),
         ("jaw", (cx, cy, JAW_Z[0] + ZOFF), (cx, cy, JAW_Z[1] + ZOFF), "sway", False),
         ("spine", (cx, cy, SPINE_Z0 + ZOFF), (cx, cy, zf(Z_FR["chest"][0])), "sway", False),
         ("chest", (cx, cy, zf(Z_FR["chest"][0])), (cx, cy, zf(Z_FR["chest"][1])), "spine", True),
         ("head", (cx, cy, zf(Z_FR["head"][0])), (cx, cy, zf(Z_FR["head"][1])), "chest", True),
         ("crown", (cx, cy, zf(Z_FR["crown"][0])), (cx, cy, zf(Z_FR["crown"][1])), "head", True)]
aw = Wd * 0.5
for sgn, s in ((1, "L"), (-1, "R")):
    sh = (cx + sgn * Wd * ARM_FR["shoulder_x"], cy, zf(ARM_FR["z_sh"]))
    el_ = (cx + sgn * aw * ARM_FR["elbow_x"], cy, zf(ARM_FR["z_el"]))
    tp = (cx + sgn * aw * ARM_FR["tip_x"], cy, zf(ARM_FR["z_tip"]))
    BONES.append(("arm." + s, sh, el_, "chest", False))
    BONES.append(("blade." + s, el_, tp, "arm." + s, True))
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.06 * H_new); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p, c) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_)
    e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = c
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
BH = {b[0]: (np.array(b[1]), np.array(b[2])) for b in BONES}

# ---- analytic weights (<= 4 influences)
x_, y_, z_ = W_[:, 0] - cx, W_[:, 1] - cy, W_[:, 2]
zsk = z_ - ZOFF
rr = np.hypot(x_, y_)
# upper-jaw field: sculpt-derived chest = 1, sculpt-derived lower teeth = 0, the rest by height; lightly smoothed
kd_low0 = KDTree(len(LV))
for i, p in enumerate(LV):
    kd_low0.insert(p, i)
kd_low0.balance()
near0 = np.array([kd_low0.find(p)[1] for p in W_])
u_up = smoothstep(U_Z[0], U_Z[1], zsk)
u_up = np.where(upper[near0] & (zsk > 6.5), 1.0, u_up)
u_up = np.where(low_m[near0] & (zsk < 9.0), 0.0, u_up)
for _ in range(U_SMOOTH):
    u_up = 0.5 * u_up + 0.5 * Mw.neighbour_mean(u_up[:, None])[:, 0]
u_up = np.where(zsk > U_Z[1] + 1.0, 1.0, np.where(zsk < U_Z[0] - 1.8, 0.0, u_up))
# lower chain: base (ground ring) -> sway -> jaw
w_base = 1.0 - smoothstep(BASE_W[0], BASE_W[1], zsk)
w_jaw = (1.0 - w_base) * smoothstep(JAW_W[0], JAW_W[1], zsk)
w_sway = 1.0 - w_base - w_jaw
# upper chain: spine -> chest -> head -> crown by height
joints = [BH["chest"][0][2], BH["head"][0][2], BH["crown"][0][2]]
names_up = ["spine", "chest", "head", "crown"]
up_w = np.zeros((Mw.n, 4))
prev = np.ones(Mw.n)
for k_, zj in enumerate(joints):
    s_ = smoothstep(zj - W_BAND, zj + W_BAND, z_)
    up_w[:, k_] = prev * (1 - s_)
    prev = prev * s_
up_w[:, 3] = prev
# arms (v1 rule) and the hanging lower arms + hands (v1 HAND_MIX)
side_ = np.sign(x_)
ax_ = np.abs(x_)
armness = smoothstep(ARM_IN * Wd, ARM_OUT * Wd, ax_) * smoothstep(ARM_ZMIN * H_new - W_BAND, ARM_ZMIN * H_new + W_BAND, z_) * \
    (1 - smoothstep(ARM_ZMAX * H_new - W_BAND, ARM_ZMAX * H_new + W_BAND, z_))
elbow_x = abs(BH["arm.L"][1][0] - cx)
bladeness = smoothstep(elbow_x - 1.2 * W_BAND, elbow_x + 1.2 * W_BAND, ax_)
handness = smoothstep(HAND_R[0], HAND_R[1], rr) * smoothstep(HAND_ZLOW - 0.5 + ZOFF, HAND_ZLOW + 0.5 + ZOFF, z_) * \
    (1 - smoothstep(HAND_ZTOP[0] + ZOFF, HAND_ZTOP[1] + ZOFF, z_)) * (1 - armness) * (1 - smoothstep(HAND_Y - 0.8, HAND_Y + 0.8, np.abs(y_)))
rest_w = 1 - armness - handness
Wt = np.zeros((Mw.n, len(DEFORM)))
for k_, nm in enumerate(names_up):
    Wt[:, J[nm]] += up_w[:, k_] * u_up * rest_w
Wt[:, J["base"]] += w_base * (1 - u_up) * rest_w
Wt[:, J["sway"]] += w_sway * (1 - u_up) * rest_w
Wt[:, J["jaw"]] += w_jaw * (1 - u_up) * rest_w
Wt[:, J["spine"]] += handness * HAND_MIX["spine"]
L_ = side_ > 0
for sgn_m, s in ((L_, "L"), (~L_, "R")):
    Wt[sgn_m, J["arm." + s]] += (armness * (1 - bladeness) + handness * HAND_MIX["arm"])[sgn_m]
    Wt[sgn_m, J["blade." + s]] += (armness * bladeness + handness * HAND_MIX["blade"])[sgn_m]
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx = np.argsort(-Wt, 1, kind="stable")[:, 4:]
    np.put_along_axis(Wt, idx, 0.0, 1)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "ground_ring_base_weight_min": round(float(Wt[z_ < lo_n[2] + 0.05, J["base"]].min()), 4),
                  "rule": "upper-jaw field u (sculpt-derived chest = 1, sculpt-derived lower teeth = 0, else smoothstep U_Z, "
                          "U_SMOOTH neighbour passes): u x (spine/chest/head/crown by height) + (1-u) x (base -> sway -> jaw by "
                          "height, BASE_W / JAW_W); arms + hanging hands as v1 (ARM_*, HAND_*, HAND_MIX)"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig

# ---- clips: closed-form curves keyed on every frame (the last frame equals the first: seam 0 by construction)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST3 = {b.name: Matrix([list(r_[:3]) for r_ in b.matrix_local[:3]]) for b in arm_data.bones}


def world_rot(bone, axis, deg_):
    """pose quaternion (bone local) for a rotation of deg_ about a WORLD axis at the bone head"""
    B = REST3[bone]
    Rw = Matrix.Rotation(math.radians(deg_), 3, Vector(axis))
    return (B.inverted() @ Rw @ B).to_quaternion()


def world_loc(bone, v):
    """pose location (bone local) for a WORLD translation v"""
    return REST3[bone].inverted() @ Vector(v)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def chomp_curve(f):
    """jaw gap offset (units, + = wider than rest) for idle frame f: wind-up -> snap shut -> hold -> ease back"""
    a0, a1 = CHOMP["open_f"]; s0, s1 = CHOMP["shut_f"]; h0, h1 = CHOMP["hold_f"]; r0, r1 = CHOMP["rest_f"]
    shut = -(GAP_REST - CHOMP_SHUT_GAP)
    if f <= a0:
        return 0.0
    if f <= a1:
        return CHOMP_OPEN * ease((f - a0) / (a1 - a0))
    if f <= s1:
        u = (f - s0) / (s1 - s0)
        return CHOMP_OPEN + (shut - CHOMP_OPEN) * (u ** 2)          # accelerating snap
    if f <= h1:
        return shut
    if f <= r1:
        return shut * (1 - ease((f - r0) / (r1 - r0)))
    return 0.0


# rest gap between the facing front teeth (upper minima vs lower maxima nearest in xy)
lo_tips = np.array([LV[i] for i in teeth_lo]) if teeth_lo else np.zeros((0, 3))
up_tips = np.array([LV[i] for i in teeth_up]) if teeth_up else np.zeros((0, 3))
pairs = []
for p in up_tips:
    if len(lo_tips) == 0:
        break
    dxy_ = np.hypot(lo_tips[:, 0] - p[0], lo_tips[:, 1] - p[1])
    j = int(np.argmin(dxy_))
    if dxy_[j] < 1.2 and p[2] > lo_tips[j, 2]:
        pairs.append((p, lo_tips[j], float(dxy_[j])))
pairs.sort(key=lambda q: (q[0][2] - q[1][2]))
GAP_REST = float(pairs[0][0][2] - pairs[0][1][2]) if pairs else 0.8
rep["teeth_pairs"] = [{"upper": q[0].round(3).tolist(), "lower": q[1].round(3).tolist(), "xy_offset": round(q[2], 3),
                       "gap": round(float(q[0][2] - q[1][2]), 3)} for q in pairs]


def key_clip(name, n_frames, pose_fn):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(1, n_frames + 1):
        scene.frame_set(f)
        for pb in rig.pose.bones:
            pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
        pose = pose_fn(f, (f - 1) / (n_frames - 1))
        for bn, (loc, q) in pose.items():
            pb = rig.pose.bones[bn]
            if loc is not None:
                pb.location = loc
            if q is not None:
                pb.rotation_quaternion = q
        for bn in DEFORM:
            if bn == "base":
                continue                       # the ground ring never moves
            pb = rig.pose.bones[bn]
            pb.keyframe_insert("location", frame=f, group=bn)
            pb.keyframe_insert("rotation_quaternion", frame=f, group=bn)
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, n_frames
    act.use_cyclic = True
    return act


def qmul(*qs):
    out = Quaternion()
    for q in qs:
        out = out @ q
    return out


def idle_pose(f, t):
    g = chomp_curve(f)                                   # + = open wider
    up = CHOMP_UPPER * g; dn = (1 - CHOMP_UPPER) * g
    bob = IDLE_BOB * math.sin(2 * math.pi * t)
    bite = max(0.0, -g) / max(GAP_REST - CHOMP_SHUT_GAP, 1e-6)          # 0..1 on the bite
    windup = max(0.0, g) / max(CHOMP_OPEN, 1e-6)
    P = {"spine": (world_loc("spine", (0, 0, up + bob)), None),
         "jaw": (world_loc("jaw", (0, 0, -dn)), None),
         "head": (None, world_rot("head", (1, 0, 0), -3.0 * windup + 2.0 * bite)),
         "crown": (None, world_rot("crown", (0, 1, 0), 2.0 * math.sin(2 * math.pi * t)))}
    for s, sg in (("L", 1), ("R", -1)):
        flare = CHOMP_ARMS * (bite - 0.5 * windup)
        P["arm." + s] = (None, world_rot("arm." + s, (0, 1, 0), -sg * (flare + 2.0 * math.sin(2 * math.pi * t))))
        P["blade." + s] = (None, world_rot("blade." + s, (0, 1, 0), -sg * 0.6 * flare))
    return P


def walk_pose(f, t):
    w = 2 * math.pi * t
    lean = LEAN_DEG + LEAN_OSC * math.sin(w)
    P = {"sway": (None, qmul(world_rot("sway", (0, 1, 0), GLIDE_ROLL * math.sin(w)), world_rot("sway", (1, 0, 0), lean))),
         "spine": (world_loc("spine", (0, 0, GLIDE_BOB * math.sin(2 * w))), None),
         "head": (None, world_rot("head", (1, 0, 0), -0.5 * lean)),
         "crown": (None, world_rot("crown", (1, 0, 0), -CROWN_TRAIL - 2.0 * math.sin(w - 0.8)))}
    for s, sg in (("L", 1), ("R", -1)):
        P["arm." + s] = (None, qmul(world_rot("arm." + s, (0, 0, 1), sg * (ARM_TRAIL + ARM_FLUTTER * math.sin(w - 0.6))),
                                    world_rot("arm." + s, (0, 1, 0), -sg * 3.0 * math.sin(2 * w - 1.0))))
        P["blade." + s] = (None, world_rot("blade." + s, (0, 0, 1), sg * (0.5 * ARM_TRAIL + ARM_FLUTTER * math.sin(w - 1.4))))
    return P


NEW_ACTS = {"idle": key_clip("idle", IDLE_N, idle_pose), "walk": key_clip("walk", WALK_N, walk_pose)}
rig.animation_data.action = None


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


# tooth-tip vertex handles on the final mesh (nearest low vertices to the rest pair)
kd_w = KDTree(len(W_))
for i, p in enumerate(W_):
    kd_w.insert(p, i)
kd_w.balance()
pair_v = [(kd_w.find(Vector(q[0]))[1], kd_w.find(Vector(q[1]))[1]) for q in pairs[:2]]
ground = W_[:, 2] < lo_n[2] + 0.05
clip_rep = {}
for cn, act in NEW_ACTS.items():
    K.assign_action(rig, act)
    f0, f1 = 1, int(round(act.frame_range[1]))
    first = last = None
    gaps, gslide, gz, rootoff, spine_dz, jaw_dz, sway_deg = [], 0.0, 0.0, 0.0, [], [], []
    for f in range(f0, f1 + 1):
        scene.frame_set(f)
        C = eval_coords(low)
        if f == f0:
            first = C
        if f == f1:
            last = C
        if pair_v:
            gaps.append(min(float(C[a, 2] - C[b, 2]) for a, b in pair_v))
        gslide = max(gslide, float(np.linalg.norm((C - W_)[ground, :2], axis=1).max()))
        gz = max(gz, float(np.abs(C[ground, 2] - W_[ground, 2]).max()))
        rootoff = max(rootoff, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        spine_dz.append(float((rig.pose.bones["spine"].head - arm_data.bones["spine"].head_local)[2]))
        jaw_dz.append(float((rig.pose.bones["jaw"].head - arm_data.bones["jaw"].head_local)[2]))
        ys_ = rig.pose.bones["spine"].matrix.to_3x3() @ Vector((0, 1, 0))
        sway_deg.append(math.degrees(math.atan2(-ys_[1], ys_[2])))
    seam = float(np.linalg.norm(first - last, axis=1).max())
    row = {"frames": [f0, f1], "loop_frames": f1 - f0, "seconds": round((f1 - f0) / K.FPS, 3), "cyclic": True,
           "seam_units_x1000": round(seam * 1000, 4), "ground_ring_slide_max": round(gslide, 6), "ground_ring_lift_max": round(gz, 6),
           "root_offset_max": round(rootoff, 8),
           "spine_dz_range": [round(min(spine_dz), 4), round(max(spine_dz), 4)], "jaw_dz_range": [round(min(jaw_dz), 4), round(max(jaw_dz), 4)],
           "upper_body_forward_lean_deg_range": [round(min(sway_deg), 2), round(max(sway_deg), 2)]}
    if gaps:
        row["front_teeth_gap"] = {"rest": round(gaps[0], 4), "max_open": round(max(gaps), 4), "min_shut": round(min(gaps), 4),
                                  "frame_open": int(np.argmax(gaps)) + f0, "frame_shut": int(np.argmin(gaps)) + f0,
                                  "travel": round(max(gaps) - min(gaps), 4), "travel_pct_H": round(100 * (max(gaps) - min(gaps)) / H_new, 3)}
    clip_rep[cn] = row
    print("CLIP", cn, json.dumps(row))
rep["clips"] = clip_rep
rep["clip_rules"] = {
    "idle": "float bob (IDLE_BOB, 1 per loop) + ONE chomp per loop: wind-up open CHOMP_OPEN over open_f, accelerating snap to a "
            "tip-to-tip gap CHOMP_SHUT_GAP over shut_f, hold, ease back over rest_f; the upper jaw (spine) does CHOMP_UPPER of "
            "the travel, the lower jaw (jaw bone) the rest; arms flare CHOMP_ARMS on the bite",
    "walk": "GLIDE: no stepping; sway leans the figure forward LEAN_DEG (+-LEAN_OSC) with GLIDE_ROLL side drift, float bob "
            "GLIDE_BOB (2 per loop), arms trail ARM_TRAIL + flutter, crown trails CROWN_TRAIL; the ground ring (base) is never "
            "keyed -- in place, the game moves the unit",
    "keys": "every frame, LINEAR, closed-form curves (integer harmonics / beats that end at rest): last frame == first frame"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "duskmaw v2: jawed totem (base/sway/jaw | spine/chest/head/crown, arm+blade x2) + contract root"
low["conquest_clips"] = list(NEW_ACTS)
low["conquest_clip_status"] = "v2: idle (float + chomp) and walk (glide), authored; v1's carried clips live in git history"
for a in list(bpy.data.actions):
    if a.name not in NEW_ACTS:
        bpy.data.actions.remove(a)
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 7. identity-scale glb
for o in scene.objects:
    o.select_set(o is rig or o is low)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, NEW_ACTS["idle"])
t = time.time()
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False)
rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "structure": "armature object identity (no scale), mesh child identity, natural scale; game model_scale "
                           "%.5f reaches the 1.8 m cell (report-only)" % k_fit}
rig.animation_data.action = None
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
rep["digest_rig"] = hashlib.sha256(np.round(Wt, 6).astype(np.float32).tobytes() +
                                   np.array([[v for b in rep["bones"] for v in b["head"] + b["tail"]]], np.float32).tobytes()).hexdigest()[:16]
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "seconds")}))
sys.stdout.flush()
os._exit(0)
