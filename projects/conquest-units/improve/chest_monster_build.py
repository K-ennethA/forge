"""Chest Monster -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/chest_monster_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x4: Soot Puff, Winged Cat, Phoenix Hatchling, Chest Monster" (section
CHEST MONSTER) + the sheet design/reference/chest_monster-character-sheet.png (intake: design/reference/chest_monster/
intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. ALL THREE VIEWS ARE 3/4 PERSPECTIVE (no true ortho), so the box is
RECONSTRUCTED (see SHEET RECONSTRUCTION below) and every vertical measure is divided by cos(elevation) = 0.973. Floor
z = 0 at the toe tips (the sheet's floor line, y 751 front / 752 side). Front = -Y (the eye), +X = the unit's LEFT.
The sheet's FRONT view looks from the front-LEFT (it shows the front face + the +X side face on image right); the SIDE
view looks from the left-front (the eye on image LEFT, house convention: front faces image-left); the BACK view shows
the back face + the -X side face.

SHEET RECONSTRUCTION (box W x D, the two yaw angles): an orthographic-ish 3/4 view at yaw t shows a box face of width W
as W cos t and the side face of depth D as D sin t. Measured at the box top just under the lid band (px):
    front view: front face 73 -> 304 = 231, side face 304 -> 379 = 75
    side view:  front face 865 -> 921 = 56, side face 921 -> 1115 = 194
  -> 231^2/W^2 + 75^2/D^2 = 1 and 56^2/W^2 + 194^2/D^2 = 1 -> W = 249 px, D = 199 px (D / W 0.80); front-view yaw
  21.9 deg, side-view yaw 77.0 deg. Elevation from the lid top's receding edges (front view: the side top edge rises
  44 px over D cos t = 185 px, the front top edge 21 px over W sin t = 93 px) -> sin(elev) 0.238 / 0.226 -> elev ~13.5
  deg, vertical scale cos(elev) = 0.973. Heights: near vertical box corner 512 -> 684 = 172 px (front) / 178 (side) ->
  1.80; lid band 69-71 px -> 0.71; box bottom (near corner) 684 -> 67 px above the floor -> 0.66 (the legs dangle from
  there). Positions on the lid (crystal clusters, the smoke burst) are solved from the two views the same way:
  u_front - 226 = 100 (0.928 X + 0.373 Y), u_side - 990 = 100 (0.225 X + 0.974 Y) (box centre at u 226 / 990).

Pipeline:
  1. LEG = ONE SDF (the only organic piece): a teardrop stalk (thin at the top, widening down) smooth-unioned into a
     flattened foot bulb + three clawed toes fanned in one plane (centre toe straight down, the side toes curving out
     and down). Marching tets = the HIGH (bake source); collapse decimation = the LOW leg; brown stalk / red ankle band /
     glowing toe / toe-tip regions iso-cut at fixed heights. The one leg is placed at the four box corners (rotation +
     a slight outward shear: winding and the floor contact are preserved), the fan facing out along each diagonal.
  2. HARD SURFACE (chest_monster_parts.py, closed): the tapered BOX (rounded box, wider at the base), the LID band
     (rounded box overhanging the box all round; its top face its own region), the EYE EMBLEM (vesica plate, chamfered,
     overlapping lid band + box), the IRIS (vesica plate) + GLINT (round plate), two orange DOTS per leg.
  3. FX (translucent, firefly alpha path: Col alpha x material alpha, alphaMode BLEND, double-sided): the CRYSTALS
     (faceted hexagonal prisms, pointed tips, facet tones + white streak cells; one big cluster of 4 left of centre,
     a small cluster of 3 at the +X edge), the MAGIC SMOKE burst (a low-poly core + bipyramid rays), static SPARK flecks.
  4. palette (palettes.py regions -> Col / Glow + Col alpha), flat shaded, Smart UV, bake normal + AO from the leg SDF
     highs + every opaque closed part (the fx parts never occlude: see-through).
  5. RIG: root > box > lid > crystal_big / crystal_small; box > leg_{FL,FR,BL,BR}.{0,1}. Clips PLACEHOLDER (movement
     not given by the artist): idle = lid breathe + crystal shimmer + dangling-leg sway; walk = a scuttle hop. glb.
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
import chest_monster_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100 (verticals / 0.973); front -Y, +X = the unit's
#  left, floor z = 0 at the toe tips)
UNIT = "chest_monster"
CHAR_ID = "chest_monster"             # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- BOX + LID (reconstruction in the docstring)
BOX_Z0 = 0.66                         # "box bottom": front-view near corner bottom y 684 -> (751 - 684) / 100 / 0.973
LID_Z0 = 2.42                         # "lid seam": box visible height 1.77 (front 172 px) / 1.83 (side 178 px) -> 1.76
LID_Z1 = 3.13                         # "lid top": lid band 69-71 px (front corner 443 -> 512, left edge 422 -> 493) -> 0.71
BOX_HALF_TOP = (1.245, 0.995)         # "box width / depth" (half, at the top under the lid): W 249 x D 199 px reconstructed
BOX_BASE_SCALE = 1.06                 # "taper": the base is this x the top (spec: wider at the base; back view left edge
                                      #   475 -> 462 px over the face = ~6 %; the FRONT view draws it the other way, see report)
BOX_TOP_HIDDEN = 0.10                 # the box runs this far up inside the lid band (never seen; closes the seam)
BOX_BEVEL, LID_BEVEL = 0.045, 0.04    # "edge bevel" radii (hard surface, small)
BOX_GRID = ((10, 8, 8), 2)            # box surface grid: interior segments per axis, segments per bevel half
LID_GRID = ((10, 8, 3), 2)
LID_OVERHANG = 0.05                   # "lid overhang" all round (sheet: lid 70 -> 307 vs box 73 -> 304 px, 3-5 px a side)
LID_TOP_NZ = 0.6                      # lid faces whose normal points up more than this take the lid_top tone
# ---- EYE EMBLEM (front view, x = 0.928 cos(yaw) foreshortened): almond 97 px wide -> 1.05; tips y 409 / 590 front,
#   421 / 571 side -> top 3.39 / 3.29, bottom 1.53 / 1.75 (views disagree ~0.2: the mean is built). Centre x +0.05 (on
#   the centreline: built 0).
EYE_C = (0.0, 2.49)                   # "eye placement" (x, z of the almond centre)
EYE_AB = (0.52, 0.80)                 # "eye size": half-width, half-height (the top tip pokes 0.16 above the lid top: drawn)
EYE_PROUD = 0.025                     # the emblem's front face stands this far in front of the lid band
EYE_CHAMFER = 0.02                    # front edge chamfer
EYE_SEG = 28                          # outline points per arc
IRIS_AB = (0.18, 0.33)                # "iris size": 34 x 65 px (pointed-oval, drawn as an almond)
IRIS_PROUD = 0.018
GLINT = (0.025, 0.13, 0.065)          # "highlight": offset x, z from the eye centre (front view (197, 489)), radius
GLINT_PROUD = 0.012
# ---- CRYSTALS: cluster centre on the lid top (solved from front + side views: big (195, 377) / (987, 356) px ->
#   (-0.35, 0.05); small (345, 415) / (1046, 405) -> (1.16, 0.31)); per prism (cluster, base offset, direction, length,
#   radius, spin deg). Big: the tall prism (front tip (217, 206): 1.70 up, leaning +X 0.20), the left one (tip (165, 259),
#   leaning front-left), a steep right one (tip (256, 292), +X ~40 deg; the back view's near-horizontal prism), a small
#   low one at the base. Small: two long prisms leaning out over the +X / back corner (side-view tips 1149 px, past the
#   lid's back edge 1118) + a low stub.
CLUSTERS = {"big": (-0.35, 0.05), "small": (1.10, 0.30)}
CRYSTALS = [("big", (0.00, 0.00), (0.12, 0.01, 1.0), 1.72, 0.27, 10.0),
            ("big", (-0.20, -0.12), (-0.30, -0.30, 1.0), 1.25, 0.21, 35.0),
            ("big", (0.22, 0.02), (0.85, 0.10, 1.0), 0.95, 0.17, 20.0),
            ("big", (-0.30, 0.18), (-0.95, 0.30, 1.0), 0.55, 0.13, 0.0),
            ("small", (0.05, 0.06), (0.42, 0.90, 1.05), 1.35, 0.17, 15.0),
            ("small", (-0.15, -0.10), (0.30, 0.45, 1.0), 0.95, 0.14, 40.0),
            ("small", (0.02, -0.24), (0.55, -0.25, 1.0), 0.50, 0.11, 5.0)]
CRYSTAL_TIP = 0.28                    # "crystal points": the pyramid tip is this share of each prism's length
CRYSTAL_BURY = 0.12                   # the prisms grow from this deep inside the lid
CRYSTAL_LIGHT = (-0.55, -0.45, 0.70)  # facet tone: faces toward this get crystal_light, away crystal_deep (sheet: lit upper-left)
CRYSTAL_TONE_T = 0.35
STREAK_VIEW = (0.37, -0.93, 0.25)     # the white streak sits on the facet(s) facing the sheet's front-view camera
# ---- MAGIC SMOKE burst (front (226, 384) / side (956, 384) px -> (0.15, -0.38); built a touch inboard)
SMOKE_BURST = False                   # "smoke burst" on/off: the core + rays star on the lid (artist 2026-10-10: removed,
                                      #   design/reference/chest_monster-v1-smoke-annotation.png); the sparks stay
SMOKE_C = (0.10, -0.30)               # "smoke placement" on the lid top
SMOKE_CORE = (0.26, 0.26, 0.22)       # "smoke core" radii (sheet: the bright centre ~0.5 across)
SMOKE_LIFT = 0.10                     # the core / ray roots sit this far above the lid top
SMOKE_RAYS = [(0, 8, 0.90), (34, 30, 0.71), (68, 12, 0.80), (101, 52, 0.57), (137, 18, 0.85), (170, 34, 0.69),
              (205, 10, 0.76), (238, 58, 0.55), (272, 20, 0.83), (306, 38, 0.67), (330, 70, 0.53), (52, 76, 0.48),
              (190, 72, 0.51)]       # "smoke rays": (azimuth deg, elevation deg, length) -- the sheet burst spans ~1.4 x 0.75
RAY_W, RAY_T, RAY_MID = 0.15, 0.06, 0.28    # ray diamond half-widths and where along it the diamond sits
SPARKS = (0, 0.028, 0.045, 11)       # "sparks": count, radius range, seed (static flecks around / above the burst) -- count 0: artist 2026-10-10 "turn off the floating spark flecks"
# ---- LEGS (front-view near leg x 280-339, rows y 615-750): top z 1.36 (width 0.04), 1.18 (0.10), 0.94 (0.23); the foot
#   from z ~0.58 (0.55 wide) to the toe tips z 0 (0.60 wide); brown -> red at z 0.58 -> 0.46; dots z 1.03 / 0.89.
LEG_TOP = 1.34                        # "leg length": the stalk's pointed top (on the box's corner edge)
LEG_SPLAY = 0.05                      # the foot sits this far outward of the box base corner (along the diagonal)
STALK = ((LEG_TOP - 0.025, 0.022), (0.56, 0.125))   # "stalk": (z, radius) top -> bottom of the tapering stalk
FOOT = ((0.0, 0.0, 0.40), (0.17, 0.12, 0.16))       # "foot bulb": centre, radii (x = across the toe fan)
TOES = [[(0.0, 0.0, 0.42, 0.100), (0.0, 0.0, 0.20, 0.075), (0.0, 0.0, 0.022, 0.020)],
        [(0.08, 0.0, 0.42, 0.090), (0.20, 0.0, 0.22, 0.070), (0.30, 0.0, 0.045, 0.020)],
        [(-0.08, 0.0, 0.42, 0.090), (-0.20, 0.0, 0.22, 0.070), (-0.30, 0.0, 0.045, 0.020)]]  # "claws": (x, y, z, r) knots
TOE_FLAT = 0.72                       # the toes are flattened across the fan (y) by this
LEG_K = (0.09, 0.05)                  # smooth-union k: stalk -> foot, toes -> foot
LEG_STEP, LEG_BAND = 0.010, 0.13      # SDF step / band (band >= max k + 2 steps, contract hazard note)
LEG_TRIS = 1700                       # "leg mesh detail": decimation target per leg
ANKLE_Z = (0.62, 0.50)                # region cuts: stalk | ankle band | toe
TOE_TIP_Z = 0.09                      # the glowing claw tips below this
LEG_DOTS = ((1.03, 0.034), (0.89, 0.034))   # "leg dots": (z, radius) on the outward face of the stalk
CUT_SNAP = 0.05                       # iso-cut snap (low: the band edges on the coarse leg stay straight)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- clips (PLACEHOLDER: the artist has not given Chest Monster's movement). 24 fps; integer harmonics per loop ->
#   frame N+1 == frame 1. Rest pose: toe tips on the floor (z 0).
IDLE_N = 96                           # "idle loop": 4 s
LID_BREATHE = (2.5, 0.015, 2)         # "lid breathe": hinge-open deg (front lifts), lift u, cycles per loop (0.5 Hz)
CRYSTAL_SHIMMER = (0.025, 1.0, 3)     # "crystal shimmer": scale +-, wobble deg, cycles per loop
LEG_SWAY = (3.0, 2.0, 1, 0.8)         # "dangling-leg sway": stalk deg, extra foot deg, cycles, foot lag (rad); each leg
                                      #   swings in / out along its corner diagonal (the toe fan never dips the floor)
WALK_N = 24                           # "scuttle loop": 1 s
WALK_HOPS = 2                         # hops per loop (even: the leg pairs alternate per hop)
WALK_HOP = 0.12                       # "hop height" u
WALK_SWING = 17.0                     # "leg swing" deg fore-aft, diagonal pairs opposite
WALK_PITCH = 2.0                      # box pitch deg per hop
WALK_LID = 3.0                        # lid jiggle deg (lags the hop)

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
          "design/reference/chest_monster-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; all three views 3/4 perspective (box reconstructed, verticals / 0.973); "
                   "floor z = 0 at the toe tips", "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}
CORNERS = {"FL": (1.0, -1.0), "FR": (-1.0, -1.0), "BL": (1.0, 1.0), "BR": (-1.0, 1.0)}   # (sx, sy): F = -Y, L = +X


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

# =========================================================================== 1. the LEG: one SDF (local frame)
# local frame: z up from the floor, the leg axis on x = y = 0, the toe fan in the x-z plane, local -y = outward
t_sdf = time.time()


def sd_stalk(P):
    return SD.sd_round_cone(P, (0.0, 0.0, STALK[0][0]), (0.0, 0.0, STALK[1][0]), STALK[0][1], STALK[1][1])


def sd_foot(P):
    return SD.sd_ellipsoid(P, FOOT[0], FOOT[1])


def sd_toe(P, knots):
    Q = P * np.array([1.0, 1.0 / TOE_FLAT, 1.0])
    d = np.full(len(P), 1e9)
    for (x0, y0, z0, r0), (x1, y1, z1, r1) in zip(knots[:-1], knots[1:]):
        d = np.minimum(d, SD.sd_round_cone(Q, (x0, y0 / TOE_FLAT, z0), (x1, y1 / TOE_FLAT, z1), r0, r1))
    return d * TOE_FLAT


def sd_toes(P):
    return np.min(np.stack([sd_toe(P, kn) for kn in TOES], 1), 1)


_llo = np.array([-0.40, -0.30, -0.02]); _lhi = np.array([0.40, 0.30, LEG_TOP + 0.03])
g = SD.Grid(_llo, _lhi, LEG_STEP, LEG_BAND)
g.apply(sd_foot, np.array(FOOT[0]) - FOOT[1], np.array(FOOT[0]) + FOOT[1], 0.0)
g.apply(sd_stalk, (-0.15, -0.15, STALK[1][0] - 0.15), (0.15, 0.15, LEG_TOP), LEG_K[0])
for kn in TOES:
    kk = np.array(kn)[:, :3]
    g.apply(lambda P, kn=kn: sd_toe(P, kn), kk.min(0) - 0.12, kk.max(0) + 0.12, LEG_K[1])
HV_L, HF_L = SD.polygonise(g)
HF_L = [list(map(int, f)) for f in HF_L]
HV_L, HF_L, crumbs = keep_islands(HV_L, HF_L, 0.02)
report["sdf"] = {"what": "one dangling leg (stalk + foot + 3 toes), placed x4", "step": LEG_STEP, "band": LEG_BAND,
                 "grid": g.n.tolist(), "ops": len(g.ops), "high_tris_per_leg": len(HF_L), "high_verts_per_leg": len(HV_L),
                 "crumbs": crumbs, "high_min_z": round(float(HV_L[:, 2].min()), 5), "seconds": round(time.time() - t_sdf, 1)}
del g
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low leg: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV_L, HF_L)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, LEG_TRIS / len(HF_L))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV_L.tolist(), HF_L)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
dev2 = np.array([bvh_lo.find_nearest(Vector(p))[3] for p in HV_L])
_bb_h = np.ptp(HV_L, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % LEG_STEP,
                    "decimated_tris_per_leg": tri_count_F(LF), "specks": specks,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(), "seconds": round(time.time() - t, 1)}
assert np.all(_bb_l / _bb_h > 0.97), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))
print("RETOPO", json.dumps(report["retopo"]))

# ---- leg regions: iso-cuts at fixed heights (stalk | ankle band | toe | toe tip)
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
FIELDS = {"z": LV[:, 2].copy()}
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
t = time.time()
cut_log = [iso_cut("z", tau_) for tau_ in (ANKLE_Z[0], ANKLE_Z[1], TOE_TIP_Z)]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
LCV = np.array([v.co[:] for v in bm.verts])
LCF = [[v.index for v in f.verts] for f in bm.faces]
FZ = np.array([np.mean([v[LAY["z"]] for v in f.verts]) for f in bm.faces])
bm.free()
leg_reg = np.full(len(LCF), "leg", dtype=object)
leg_reg[FZ < ANKLE_Z[0]] = "leg_ankle"
leg_reg[FZ < ANKLE_Z[1]] = "toe"
leg_reg[FZ < TOE_TIP_Z] = "toe_tip"
for r_ in ("leg", "leg_ankle", "toe", "toe_tip"):
    assert np.any(leg_reg == r_), "leg region %s is empty" % r_
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "leg_tris_after_cuts": len(LCF)}
print("CUTS", json.dumps(report["iso_cuts"]))


def stalk_r(z):
    (z0, r0), (z1, r1) = STALK
    return r1 + (r0 - r1) * (z - z1) / (z0 - z1)


# =========================================================================== 3. hard-surface parts
PARTS = []
BOX_Z1 = LID_Z0 + BOX_TOP_HIDDEN
V, F, N_ = FP.rounded_box(BOX_HALF_TOP, BOX_Z0, BOX_Z1, BOX_BEVEL, BOX_GRID[0], BOX_GRID[1], BOX_BASE_SCALE)
PARTS.append(FP.Part("box", V, F, ["box"] * len(F), bone="box"))
LID_HALF = (BOX_HALF_TOP[0] + LID_OVERHANG, BOX_HALF_TOP[1] + LID_OVERHANG)
V, F, N_ = FP.rounded_box(LID_HALF, LID_Z0, LID_Z1, LID_BEVEL, LID_GRID[0], LID_GRID[1], 1.0)
PARTS.append(FP.Part("lid", V, F, ["lid_top" if n_[2] > LID_TOP_NZ else "lid" for n_ in N_], bone="lid"))


def box_half_at(z):
    s = 1.0 + (BOX_BASE_SCALE - 1.0) * (BOX_Z1 - z) / (BOX_Z1 - BOX_Z0)
    return np.array(BOX_HALF_TOP) * s


# eye emblem: front face EYE_PROUD in front of the lid band, back face buried in the box top
Y_EYE = -(LID_HALF[1] + EYE_PROUD)
Y_BACK = -(BOX_HALF_TOP[1] - 0.03)
assert -Y_BACK < box_half_at(EYE_C[1] - EYE_AB[1])[1], "eye back face not buried at its bottom tip"
V, F, R = FP.plate(FP.vesica(EYE_AB[0], EYE_AB[1], EYE_SEG), EYE_C, Y_EYE, Y_BACK, EYE_CHAMFER, "eye")
PARTS.append(FP.Part("eye", V, F, R, bone="box"))
V, F, R = FP.plate(FP.vesica(IRIS_AB[0], IRIS_AB[1], 16), EYE_C, Y_EYE - IRIS_PROUD, Y_EYE + 0.01, 0.008, "iris")
PARTS.append(FP.Part("iris", V, F, R, bone="box"))
_gl = np.array([(GLINT[2] * math.cos(2 * math.pi * k / 16), GLINT[2] * math.sin(2 * math.pi * k / 16)) for k in range(16)])
V, F, R = FP.plate(_gl, (EYE_C[0] + GLINT[0], EYE_C[1] + GLINT[1]), Y_EYE - IRIS_PROUD - GLINT_PROUD,
                   Y_EYE - IRIS_PROUD + 0.006, 0.005, "eye_glint")
PARTS.append(FP.Part("eye_glint", V, F, R, bone="box"))

# legs: placement per corner (rotation about z + an outward shear that keeps z: the floor contact survives)
LEGS = {}
for nm, (sx, sy) in sorted(CORNERS.items()):
    out = np.array([sx, sy]) / math.sqrt(2.0)
    yh = -out                                          # local +y -> inward
    xh = np.array([yh[1], -yh[0]])                     # right-handed (det +1)
    base_c = box_half_at(BOX_Z0) * np.array([sx, sy])
    top_c = box_half_at(LEG_TOP) * np.array([sx, sy]) - out * BOX_BEVEL * (math.sqrt(2.0) - 1.0)
    foot_c = base_c + out * LEG_SPLAY
    LEGS[nm] = {"out": out, "xh": xh, "yh": yh, "foot": foot_c, "top": top_c}


def leg_place(P, nm):
    Lg = LEGS[nm]
    P = np.atleast_2d(P)
    w = (P[:, 2] / LEG_TOP)[:, None]
    xy = Lg["foot"] + (Lg["top"] - Lg["foot"]) * w + P[:, :1] * Lg["xh"] + P[:, 1:2] * Lg["yh"]
    return np.column_stack([xy, P[:, 2]])


for nm in sorted(CORNERS):
    p = FP.Part("leg." + nm, leg_place(LCV, nm), LCF, list(leg_reg))
    p.leg = nm
    PARTS.append(p)
    for k, (zd, rd) in enumerate(LEG_DOTS):
        rs = stalk_r(zd)
        c_loc = np.array([[0.0, -rs + rd * 0.35, zd]])    # on the outward (-y local) face, a third buried
        c_w = leg_place(c_loc, nm)[0]
        Lg = LEGS[nm]
        ez = np.array([Lg["out"][0], Lg["out"][1], 0.0])  # flattened along the outward normal
        ex = np.array([Lg["xh"][0], Lg["xh"][1], 0.0])
        ey = np.cross(ez, ex)
        V, F, R = FP.ellipsoid(c_w, (rd, rd, rd * 0.55), 8, 4, "leg_dot", frame=(ex, ey, ez))
        p = FP.Part("leg_dot.%s.%d" % (nm, k), V, F, R)
        p.leg = nm
        PARTS.append(p)

# =========================================================================== 4. fx: crystals, smoke burst, sparks
_Ld = unit(CRYSTAL_LIGHT)


def crystal_tone(n_):
    d = float(np.dot(n_, _Ld))
    return "crystal_light" if d > CRYSTAL_TONE_T else ("crystal_deep" if d < -CRYSTAL_TONE_T else "crystal")


_vw = unit(STREAK_VIEW)
CRYS_INFO = []
for i, (cl, off, dr, L_, r_, spin) in enumerate(CRYSTALS):
    c0 = np.array(CLUSTERS[cl]) + np.array(off)
    base = np.array([c0[0], c0[1], LID_Z1])
    ax = unit(dr)
    # streak facets: the one (r >= 0.13) / two (r >= 0.2) hex faces facing the front-view camera most
    a_, u_, v_ = FP.frame_from_axis(ax, (0.0, 0.0, 1.0) if abs(ax[2]) < 0.95 else (1.0, 0.0, 0.0))
    sp = math.radians(spin)
    fn = [unit(math.cos(sp + (k + 0.5) * math.pi / 3) * u_ + math.sin(sp + (k + 0.5) * math.pi / 3) * v_) for k in range(6)]
    order = np.argsort([-float(np.dot(n_, _vw)) for n_ in fn], kind="stable")
    nstreak = 2 if r_ >= 0.2 else (1 if r_ >= 0.13 else 0)
    V, F, R, _ = FP.crystal_prism(base, ax, L_, r_, CRYSTAL_TIP, spin, CRYSTAL_BURY, crystal_tone,
                                  set(int(k) for k in order[:nstreak]))
    p = FP.Part("crystal.%s.%d" % (cl, i), V, F, R, bone="crystal_" + cl)
    p.fx = True
    PARTS.append(p)
    CRYS_INFO.append({"cluster": cl, "base": base.round(4).tolist(), "axis": ax.round(4).tolist(), "length": L_,
                      "radius": r_, "tip": (base + ax * L_).round(4).tolist(), "streak_faces": nstreak})
SC = np.array([SMOKE_C[0], SMOKE_C[1], LID_Z1 + SMOKE_LIFT])
if SMOKE_BURST:
    V, F, R = FP.ellipsoid(SC, SMOKE_CORE, 12, 6, "smoke_core")
    p = FP.Part("smoke_core", V, F, R, bone="lid"); p.fx = True; PARTS.append(p)
    for k, (az, el, ln) in enumerate(SMOKE_RAYS):
        a_r, e_r = math.radians(az), math.radians(el)
        d = np.array([math.cos(e_r) * math.sin(a_r), -math.cos(e_r) * math.cos(a_r), math.sin(e_r)])
        V, F, R = FP.spike(SC, d, ln, RAY_W, RAY_T, RAY_MID, 0.05, "smoke_ray")
        p = FP.Part("smoke_ray.%02d" % k, V, F, R, bone="lid"); p.fx = True; PARTS.append(p)
rng = np.random.default_rng(SPARKS[3])
SPARK_PTS = []
while len(SPARK_PTS) < SPARKS[0]:
    az = rng.uniform(0, 2 * math.pi); rr = rng.uniform(0.35, 1.25); zz = rng.uniform(0.15, 1.45)
    c_ = SC + np.array([rr * math.sin(az), -rr * math.cos(az) * 0.8, zz])
    if c_[2] < LID_Z1 + 0.1:
        continue
    SPARK_PTS.append(c_)
    V, F, R = FP.octa(c_, rng.uniform(SPARKS[1], SPARKS[2]), "smoke_spark", spin=rng.uniform(0, math.pi))
    p = FP.Part("spark.%02d" % (len(SPARK_PTS) - 1), V, F, R, bone="lid"); p.fx = True; PARTS.append(p)

# =========================================================================== 5. assemble + centre
ISL = [(p.name, p.V, p.F, p.R, p) for p in PARTS]
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
    MATI += [1 if getattr(p, "fx", False) else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
REG = ["box", "lid", "lid_top", "eye", "iris", "eye_glint", "leg", "leg_ankle", "leg_dot", "toe", "toe_tip",
       "crystal_deep", "crystal", "crystal_light", "crystal_streak", "smoke_core", "smoke_ray", "smoke_spark"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_off = (set() if SMOKE_BURST else {"smoke_core", "smoke_ray"}) | (set() if SPARKS[0] else {"smoke_spark"})   # regions switched off on purpose (artist 2026-10-10)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j) and n not in _off]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
FX_REG = {"crystal_deep", "crystal", "crystal_light", "crystal_streak", "smoke_core", "smoke_ray", "smoke_spark"}
assert all((RNAME[i] in FX_REG) == (MATI[i] == 1) for i in range(len(RNAME))), "fx region on an opaque part or vice versa"


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
    if kind == "fx":
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"          # Eevee: order-independent see-through (glTF alphaMode BLEND)
        mat.use_backface_culling = False                 # crystals + smoke read through to their back faces
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
    return {n: float(a) for n, a in zip(names, a_reg)}


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        paint_alpha(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
    return out


GLOW_ALLOWED = {"toe", "toe_tip", "crystal_deep", "crystal", "crystal_light", "crystal_streak", "smoke_core", "smoke_ray",
                "smoke_spark"}
GLOW_CAP = 2.0


def glow_tiers(pal):
    """the sheet draws glow on the crystals, the toes and the smoke only; every tier under the 2.0 cap; inside each
    family the tiers rise (toe < toe tip; crystal deep < body < light < streak; smoke ray < spark < core)."""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    fams = [["toe", "toe_tip"], ["crystal_deep", "crystal", "crystal_light", "crystal_streak"],
            ["smoke_ray", "smoke_spark", "smoke_core"]]
    ok_grade = all(tier[a] < tier[b] for f in fams for a, b in zip(f, f[1:]))
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "families": fams, "grade_pass": ok_grade,
            "only_drawn_glow": sorted(tier) == sorted(GLOW_ALLOWED), "cap": GLOW_CAP,
            "cap_pass": all(v <= GLOW_CAP for v in tier.values())}


MAT_BODY = make_mat(UNIT + "_body", "body")
MAT_FX = make_mat(UNIT + "_fx", "fx")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
_gt = report["glow_tiers"]["default"]
assert _gt["grade_pass"] and _gt["only_drawn_glow"] and _gt["cap_pass"], _gt

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_FX)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
shade = jitter(VALL, FALL)
shade[np.array([not r.startswith("leg") and not r.startswith("toe") for r in RNAME])] = 1.0   # hard surface + fx: flat paint
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
report["alpha"] = {"main": paint_alpha(me, pal_default), "material_alpha": pal_default["material"].get("alpha", 1.0)}
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the box centre -> the eye emblem's front face centre (on the midline, -Y)
anchor = np.array([0.0, 0.0, EYE_C[1]]) - SHIFT
landmark = np.array([EYE_C[0], Y_EYE, EYE_C[1]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "box centre (at eye height) -> the eye emblem's front face centre (the eye faces -Y)",
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
_pp = {}
for p in PARTS:
    key = p.name.split(".")[0] if "." in p.name else p.name
    _pp[key] = _pp.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "per_part_family": _pp,
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The four SDF legs carry most of the budget "
                            "(a smooth teardrop stalk + three claws each), the box / lid are bevelled rounded boxes, the "
                            "crystals are faceted prisms (7), the smoke a core + 13 rays." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": len([k for k, v in open_e.items() if v == 0]),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every part is a closed solid (SDF legs, rounded boxes, plates, prisms, bipyramids, octahedra)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "box_WxDxH": {"top_W": round(2 * BOX_HALF_TOP[0], 4), "top_D": round(2 * BOX_HALF_TOP[1], 4),
                                   "base_W": round(2 * BOX_HALF_TOP[0] * BOX_BASE_SCALE, 4),
                                   "base_D": round(2 * BOX_HALF_TOP[1] * BOX_BASE_SCALE, 4),
                                   "box_H_visible": round(LID_Z0 - BOX_Z0, 4), "lid_H": round(LID_Z1 - LID_Z0, 4),
                                   "lid_W": round(2 * LID_HALF[0], 4), "lid_D": round(2 * LID_HALF[1], 4),
                                   "body_top_z": LID_Z1, "box_bottom_z": BOX_Z0},
                     "sheet_reconstruction": {"method": "3/4 views: face widths W cos t / D sin t in the front + side "
                                              "views solved for W, D and both yaws; verticals / cos(elev 13.5 deg)",
                                              "front_px": [231, 75], "side_px": [56, 194], "W_px": 249, "D_px": 199,
                                              "yaw_front_deg": 21.9, "yaw_side_deg": 77.0, "elev_deg": 13.5},
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
low["conquest_source"] = "from scratch: design/reference/chest_monster-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; fx material "
                                 "(crystals, smoke, sparks) alphaMode BLEND double-sided, body opaque")
# focus boxes for the close-ups (world, after the centre shift) = the sheet's detail panels
_cry = np.vstack([p.V for p in PARTS if p.name.startswith("crystal")])
_eye = np.array([[-EYE_AB[0] - 0.25, Y_EYE - 0.1, EYE_C[1] - EYE_AB[1] - 0.1], [EYE_AB[0] + 0.25, -0.6, EYE_C[1] + EYE_AB[1] + 0.1]])
_smk = np.array([SC - np.array([0.85, 0.85, 0.15]), SC + np.array([0.85, 0.85, 0.95])])
_fl = PARTS[[p.name for p in PARTS].index("leg.FL")].V
low["conquest_focus"] = json.dumps({
    "eye": [(_eye[0] - SHIFT).tolist(), (_eye[1] - SHIFT).tolist()],
    "crystals": [(_cry.min(0) - SHIFT).tolist(), (_cry.max(0) - SHIFT).tolist()],
    "smoke": [(_smk[0] - SHIFT).tolist(), (_smk[1] - SHIFT).tolist()],
    "feet": [(_fl.min(0) - SHIFT - 0.05).tolist(), (_fl.max(0) - SHIFT + 0.05).tolist()],
    "views": {"eye": [10.0, 6.0], "crystals": [22.0, 14.0], "smoke": [22.0, 30.0], "feet": [45.0, 4.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow_tiers")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = leg SDF + opaque parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [], []
o_ = 0
for p in PARTS:
    if getattr(p, "fx", False):
        continue                                     # crystals / smoke / sparks never occlude the body (see-through)
    if p.name.startswith("leg."):
        Vh = leg_place(HV_L, p.leg); Fh = HF_L        # the leg's SDF high, placed like its low
    else:
        Vh, Fh = p.V, p.F
    PH_V.append(Vh - SHIFT); PH_F += [[i + o_ for i in f] for f in Fh]; o_ += len(Vh)
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
for mat_ in (MAT_BODY, MAT_FX):
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
    for mat_ in (MAT_BODY, MAT_FX):
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
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the leg's high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "4 x the leg SDF high + every opaque closed part; fx parts excluded (see-through)",
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
# wire the textures into the BODY material only (the fx keep flat colour + alpha + glow)
nt = MAT_BODY.node_tree
tn, ta = BAKE_NODES[MAT_BODY.name]
for n_ in BAKE_NODES[MAT_FX.name]:
    MAT_FX.node_tree.nodes.remove(n_)
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
report["crystals"] = CRYS_INFO
report["legs"] = {nm: {"top_xy": (np.r_[Lg["top"], 0.0] - SHIFT)[:2].round(4).tolist(),
                       "foot_xy": (np.r_[Lg["foot"], 0.0] - SHIFT)[:2].round(4).tolist()} for nm, Lg in LEGS.items()}
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


LEG_BONES = 2
HINGE = np.array([0.0, LID_HALF[1], LID_Z0])          # the lid hinges on its back bottom edge
BONES = [("box", P_((0.0, 0.0, BOX_Z0)), P_((0.0, 0.0, LID_Z0)), "root"),
         ("lid", P_(HINGE), P_(HINGE + np.array([0.0, 0.0, LID_Z1 - LID_Z0])), "box")]
for cl, c in sorted(CLUSTERS.items()):
    BONES.append(("crystal_" + cl, P_((c[0], c[1], LID_Z1)), P_((c[0], c[1], LID_Z1 + 0.8)), "lid"))
for nm in sorted(CORNERS):
    pts = [leg_place(np.array([[0.0, 0.0, z]]), nm)[0] for z in np.linspace(LEG_TOP, 0.0, LEG_BONES + 1)]
    for k in range(LEG_BONES):
        BONES.append(("leg_%s.%d" % (nm, k), P_(pts[k]), P_(pts[k + 1]), "box" if k == 0 else "leg_%s.%d" % (nm, k - 1)))
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
    e.use_connect = nm.startswith("leg_") and nm.endswith(".1")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 3 influences): rigid parts 1.0 on their bone; each leg (+ its dots) rigkit.vine_weights along the leg
#      axis from its top (box -> leg.0 blend over the top quarter: the stalk stays pinned to the box corner)
Wt = np.zeros((len(VALL), len(DEFORM)))
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "leg", None):
        zl = VALL[a_:b_, 2] + SHIFT[2]
        Wv = K.vine_weights(np.clip(LEG_TOP - zl, 0.0, LEG_TOP), LEG_TOP, LEG_BONES, parent_s0=0.0)
        Wt[a_:b_, J["box"]] += Wv[:, 0]
        for k in range(LEG_BONES):
            Wt[a_:b_, J["leg_%s.%d" % (p.leg, k)]] += Wv[:, k + 1]
    else:
        Wt[a_:b_, J[p.bone]] = 1.0
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx_ = np.argsort(-Wt, 1)[:, 4:]
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
                  "rule": "box + eye/iris/glint 1.0 box; lid + smoke + sparks 1.0 lid; each crystal cluster 1.0 its "
                          "crystal bone (parented to the lid); legs + dots: rigkit.vine_weights along the leg (box -> "
                          "leg.0 blend over the top quarter, leg.0 -> leg.1 between the bone midpoints)"}
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


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """world-axis rotations (axis name or a world vector, deg; in order, about the bone head, relative to the parent's
    posed frame) -> basis quaternion"""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        av = Vector(AXV[axis]) if isinstance(axis, str) else Vector(tuple(float(c) for c in axis))
        Rw = Matrix.Rotation(math.radians(deg), 3, av) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
LEG_PHASE = {"FL": 0.0, "BR": 1.7, "FR": 3.3, "BL": 4.9}   # idle: no two legs in step
PAIR = {"FL": 1.0, "BR": 1.0, "FR": -1.0, "BL": -1.0}     # walk: diagonal pairs swing opposite
CL_PHASE = {"big": 0.0, "small": 2.1}


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        w_ = 0.5 * (1.0 - math.cos(TAU * LID_BREATHE[2] * u))          # 0 .. 1 .. 0, LID_BREATHE[2] breaths per loop
        P["box"] = (None, [], None)
        P["lid"] = ((0.0, 0.0, LID_BREATHE[1] * w_), [("X", -LID_BREATHE[0] * w_)], None)
        for cl in CLUSTERS:
            ph = TAU * CRYSTAL_SHIMMER[2] * u + CL_PHASE[cl]
            s_ = 1.0 + CRYSTAL_SHIMMER[0] * math.sin(ph)
            P["crystal_" + cl] = (None, [("Z", CRYSTAL_SHIMMER[1] * math.sin(ph + 0.9))], (s_, s_, s_))
        for nm in CORNERS:
            ax = np.r_[LEGS[nm]["xh"], 0.0]                              # swing in / out along the corner diagonal
            for k in range(LEG_BONES):
                deg = K.vine_wave(u, k, LEG_BONES, LEG_SWAY[0], LEG_SWAY[1], LEG_SWAY[3], LEG_SWAY[2], LEG_PHASE[nm])
                P["leg_%s.%d" % (nm, k)] = (None, [(ax, deg)], None)
        info = {"lid": w_}
    else:
        H_ = WALK_HOPS
        hop = WALK_HOP * math.sin(math.pi * H_ * u) ** 2                   # H_ hops per loop, on the floor at u = k / H_
        P["box"] = ((0.0, 0.0, hop), [("X", WALK_PITCH * math.sin(TAU * H_ * u))], None)
        P["lid"] = (None, [("X", -WALK_LID * math.sin(math.pi * H_ * u - 0.6) ** 2)], None)
        for cl in CLUSTERS:
            s_ = 1.0 + 0.5 * CRYSTAL_SHIMMER[0] * math.sin(TAU * H_ * u + CL_PHASE[cl])
            P["crystal_" + cl] = (None, [], (s_, s_, s_))
        sw = WALK_SWING * math.sin(math.pi * H_ * u)                       # 0 at every landing; pairs alternate per hop
        for nm in CORNERS:
            P["leg_%s.0" % nm] = (None, [("X", PAIR[nm] * sw)], None)
            P["leg_%s.1" % nm] = (None, [("X", PAIR[nm] * 0.4 * WALK_SWING * math.sin(math.pi * H_ * u - 0.5))], None)
        info = {"hop": hop}
    return P, info


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


LEGV = np.concatenate([np.arange(*RANGE["leg." + nm]) for nm in sorted(CORNERS)])
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
        rows.append({"minz": float(C[:, 2].min()), "minz_legs": float(C[LEGV, 2].min()),
                     "box_z": float(pbs["box"].head.z), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 4),
                    "box_lift": [round(float(g_("box_z").min() - arm_data.bones["box"].head_local.z), 4),
                                 round(float(g_("box_z").max() - arm_data.bones["box"].head_local.z), 4)]}
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
rig["conquest_rig"] = ("chest_monster v1: root > box > lid > crystal_big / crystal_small; box > leg_{FL,FR,BL,BR}.0 > .1 "
                       "(vine weights along each leg)")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Chest Monster's movement; open question): idle = "
                               "4 s lid breathe (hinge %.1f deg, 0.5 Hz) + crystal shimmer + dangling-leg sway; walk = 1 s "
                               "scuttle hop (%d hops of %.2f u, diagonal leg pairs alternating)" % (LID_BREATHE[0], WALK_HOPS, WALK_HOP))
low["conquest_locomotion"] = "scuttle-hop placeholder: rest pose toe tips z 0 per contract"
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
              "structure": "armature identity + skinned 'chest_monster' (opaque body primitive + alpha-blend fx primitive); "
                           "natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
