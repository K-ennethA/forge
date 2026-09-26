"""Supaoctto (the octopus superhero) through the shared pipeline, one headless run.

    blender --background source-copies/newunit-supaoctto.blend --factory-startup --python improve/supaoctto_build.py -- \
        [--preview <out.blend>]          (geometry + webs + UV + regions + palette only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the two-run determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Reads (never writes): source-copies/newunit-supaoctto.blend (opened). Render meshes: Body (torso + 2 arm tentacles + 2 leg
tentacles, one shell, NEGATIVE z scale), Cape (4 cape tentacles joined at a shoulder yoke, a Y-mirror modifier makes the
two halves), Head (the mantle), Mask-Goggles, Icosphere (the mouth). 'Mask' is hidden in the view layer (the artist's
unused alternative to Mask-Goggles) and is left out, as the survey did. No materials, no rig (the sibling _rig.blend's
armature was not used: its bones cover the Body only).

Outputs:
    improved/supaoctto.blend + .json        regular-tier retopo + built water webs, UVs, baked normal/AO, region palette
    improved/textures/supaoctto_{normal,ao}.png
    rigged/supaoctto.blend + .json          + biped/tentacle rig, clips idle + walk
    rigged/supaoctto.glb                    identity-scale export (natural scale)

Artist (design/review-log.md, verbatim): "supaccotto is a sideview and not facing front"; "the cape is tentancles so they
should move a bit, they are its tentacles that have evolved into a cape so have less movement than its main arm and leg
tentacles, essentially it functions as a cape, and in between the cape tentacles we want them connected by water. he
walks upright on two legs". Scale policy 2026-09-25: natural proportions, cell fit report-only. Idle + locomotion only.

Pipeline:
  1. every render mesh to world space (evaluated: the Cape's mirror; the Body's negative scale flips its winding back),
     crumbs dropped, YAW_FIX_DEG = 180 applied to the DATA (x, y) -> (-x, -y): the goggles + mouth sat at +Y.
  2. FIXES on the sculpt data (reported): the shorter leg tentacle's tip is stretched along its own axis to the other
     leg's floor level (the sculpt's tips differ by ~0.54 units: one foot hovered); the cape's bottom band is lifted
     (smooth vertical ease) so the cape tips hang CAPE_TIP_CLEAR above the floor instead of 0.36 below the feet.
  3. ANATOMY by geodesic ring tracing (Dijkstra from each tentacle tip, rings of TRACE_DS; a ring whose spread jumps by
     TRACE_MERGE_K has left the tentacle): 2 arm tentacles + 2 leg tentacles on the Body, 4 cape tentacles on the Cape
     (outer + inner per side; what is left of the Cape is the shoulder yoke). Ring centroids = the chain centrelines.
  4. WATER WEBS (new geometry, the sculpt has none): a thin closed sheet between each pair of neighbouring cape tentacles
     (outer.R|inner.R, inner.R|inner.L, inner.L|outer.L), edges buried along the two tentacle centrelines, a scalloped free
     edge, a slight outward billow, pushed clear of the body.
  5. LOW: body shell (Body + Head + goggles + mouth) and cape shell remeshed + collapse-decimated SEPARATELY (the cape
     must not fuse to the back), webs appended. Floor + XY centre to the origin.
  6. regions: part Voronoi + iso-contour cuts (duskmaw's cutter) -> palettes.store_regions; paint palettes/supaoctto.
  7. Smart UV + pack; bake normal + AO from the 175k-tri sculpt (web texels reset to flat: water has no sculpt detail).
  8. RIG: root (contract) > pelvis > spine > chest > head; legs thigh/shin/foot (analytic 2-bone IK, stance tips planted);
     arms 4-bone tentacle chains; cape 4 x 5-bone chains; each web a 3-bone mid chain (ripple). Weights analytic.
  9. CLIPS: closed-form periodic curves keyed every frame (integer cycles -> the seam is exact). Cape amplitudes are
     deliberately below the arm/leg tentacles' (the artist's hierarchy), measured and reported.
 10. identity-scale glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, heapq, tempfile
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402  (read-only use)
import palettes as PAL  # noqa: E402  (read-only use)

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun). Lengths are sculpt units, final
# frame: front -Y, floor (the leg-tentacle tips at rest) z = 0. The sculpt is ~20.6 units tall; natural scale.
UNIT = "supaoctto"
CHAR_ID = "supaoctto"                 # roster id: not yet in Conquest (ship path deferred by the artist)
YAW_FIX_DEG = 180.0                   # "facing fix": applied to the mesh DATA (goggles + mouth were at +Y)
VOXEL = 0.06                          # "retopo resolution" (voxel remesh size before decimation)
LOW_TRIS_BODY = 6200                  # "body detail" (decimation target, body shell: torso/arms/legs/head/goggles/mouth)
LOW_TRIS_CAPE = 2600                  # "cape detail" (decimation target, the 4 cape tentacles + yoke)
TRI_BUDGET = [3000, 12000]            # declared tier: regular unit (smooth blob sculpt, no fine detail arguing hero)
CRUMB_FRAC = 0.01                     # sculpt crumbs smaller than this fraction of their part are dropped
LEG_EQUALIZE = True                   # "level the feet": stretch the shorter leg tentacle's tip to the other's floor level
LEG_EQ_SPAN = 2.0                     # ... the stretch acts on this much of the leg above its tip
CAPE_TIP_CLEAR = 0.40                 # "cape hem height": the lowest cape tip hangs this far above the floor at rest
                                      #   (0.25 measured a 0.062 floor dip in idle: the soft-knee crouch + sway lower the hem)
CAPE_LIFT_SPAN = 4.0                  # ... the lift eases in over this much of the cape's bottom
TRACE_DS = 0.4                        # anatomy trace ring width
TRACE_MERGE_K = 1.3                   # a ring spreading this much past the last three has left the tentacle
TRACE_MIN_RINGS = 8                   # (tip clubs/paddles grow fast: the merge test starts after this many rings)
ARM_ROOT_EXT = 0.8                    # "shoulder pivot": the arm chain starts this far inside the armpit ring
LEG_ROOT_EXT = 0.6                    # "hip pivot": the leg chain starts this far above the crotch ring
# water webs between the cape tentacles
WEB_V_ROOT = 0.0                      # "web top": starts this fraction down the tentacles (0 = at the yoke)
WEB_V_EDGE = 0.62                     # "web depth": reaches this fraction down the tentacles at the tentacles ...
WEB_SCALLOP = 0.16                    # ... and this much less at the middle of each gap (the scalloped free edge)
WEB_BILLOW = 0.10                     # "web billow": outward bulge, x the gap width
WEB_THICK = 0.06                      # sheet thickness (closed thin shell: renders from both sides in any engine)
WEB_CLEAR = 0.15                      # minimum clearance from the body surface (the web is pushed back until clear)
WEB_NU, WEB_NW = 8, 12                # web grid: across the gap, down the web
WEB_RIM_W = 0.88                      # "water edge": web faces past this fraction of the depth are the bright rim
# colour regions
LENS_K = 0.62                         # "goggle lens size" (fraction of each goggle's half-extent ellipse)
ARM_TIP_FRAC = 0.80                   # "hand tip": the arm tentacle past this fraction of its length
LEG_TIP_FRAC = 0.72                   # "foot tip": the leg tentacle past this fraction of its length
CAPE_TIP_FRAC = 0.88                  # "cape tip": the cape tentacle past this fraction of its length
UNDER_T = 0.35                        # "cape lining": cape surface facing the body more than this (normal . inward)
EMBLEM_Z_FRAC = 0.66                  # "chest emblem height" (hip -> neck)
EMBLEM_R = 0.85                       # "chest emblem size" (radius on the chest surface)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
BAKE_CAGE = 0.08                      # bake cage extrusion (low->sculpt p99 ~0.04)
BAKE_RES = (1024, 512)                # normal, AO texture sizes
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-unit ceilings -- REPORT ONLY (scale policy 2026-09-25)
# rig + weights
SPINE_FRACS = (0.0, 0.35, 0.70, 1.0)  # pelvis / spine / chest joints, hip -> neck
ARM_BONES, CAPE_BONES, WEB_BONES = 4, 5, 3
LEG_KNEE_FRAC, LEG_ANKLE_FRAC = 0.45, 0.80
NECK_BAND = 0.30                      # "neck softness": head/body blend half-width across the head-part boundary
ROOT_BLEND = {"arm": 1.0, "leg": 0.9, "cape": 1.2}    # parent -> chain blend length at each chain's root
# clips (24 fps; every periodic term has an integer number of cycles per clip, so the loop seam is exact)
IDLE_N = 96                           # idle: 4 s loop
IDLE_REACH_K = 0.985                  # "idle knee soften": stance reach (x rest reach) -- both tips planted on z = 0
IDLE_BREATH = 0.05                    # breathing dip (2 per loop)
IDLE_SWAY_DEG, IDLE_BREATH_DEG = 1.2, 0.8
IDLE_HEAD_YAW_DEG, IDLE_HEAD_NOD_DEG = 4.0, 1.5
IDLE_ARM_DEG = (3.0, 9.0)             # "arm tentacle drift": per-bone amplitude root -> tip (travelling curl wave)
IDLE_ARM_LAG = 0.7                    # wave lag per bone (rad)
IDLE_CAPE_DEG = (0.8, 2.4)            # "cape sway": per-bone amplitude root -> tip (a cape: well under the arms)
IDLE_CAPE_LAG = 0.5
IDLE_CAPE_BIAS = 0.8                  # "cape breeze": fore/aft sway biased backward by this x the amplitude (the cape hangs
                                      #   back, so swinging it toward vertical would lower its tips into the floor)
IDLE_WEB_DEG = (2.0, 6.0)             # "web ripple": mid-chain amplitude root -> edge, 3 ripples per loop
IDLE_WEB_LAG = 0.9
WALK_N = 28                           # walk: 1.167 s per stride (2 steps) -> 102.9 steps/min
WALK_STEP_FRAC = 0.26                 # "step size": stance-foot half-travel as a fraction of the leg's reach
WALK_REACH_K = 0.975                  # stance leg reach (x rest reach): knees stay soft
WALK_LIFT_FRAC = 0.12                 # "foot lift" (x leg reach)
WALK_FOOT_CURL_DEG = 25.0             # swing-phase tentacle-tip curl
WALK_PELVIS_YAW_DEG, WALK_PELVIS_ROLL_DEG = 5.0, 2.5
WALK_CHEST_COUNTER_DEG = 7.0          # shoulders counter-twist the pelvis
WALK_LEAN_DEG = 3.0                   # "upright": only a slight forward lean
WALK_ARM_SWING_DEG = 12.0             # arm tentacle counter-swing at the shoulder ...
WALK_ARM_FOLLOW_DEG = 5.0             # ... and the lagging follow-through wave in each further bone
WALK_ARM_LAG = 0.6
WALK_CAPE_TRAIL_DEG = 2.0             # "cape trail": constant backward drape per bone while walking
WALK_CAPE_DEG = (0.8, 2.2)            # cape flutter per bone root -> tip (2 per stride)
WALK_CAPE_LAG = 0.7
WALK_WEB_BILLOW_DEG = 3.0             # webs belly back while walking
WALK_WEB_DEG = (2.0, 5.0)             # web ripple per bone
WALK_WEB_LAG = 0.9

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
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": bpy.data.filepath, "tier": "regular",
          "tri_budget": TRI_BUDGET, "yaw_fix_deg": YAW_FIX_DEG, "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}                                # the determinism digest: every array a consumer receives
TAU = 2.0 * math.pi


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


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], F)
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


def islands(V, F):
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
    return np.unique(roots, return_inverse=True, return_counts=True)


def keep_islands(V, F, min_frac):
    """Drop connected pieces smaller than min_frac of the vertices (sculpt crumbs / remesh specks)."""
    lab, inv, cnt = islands(V, F)
    keep_lab = cnt >= min_frac * len(V)
    keep_v = keep_lab[inv]
    remap = -np.ones(len(V), dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum())}


def decimate(V, F, target):
    tmp = new_obj("dec_src", V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / tri_count_F(F))
    V2, F2 = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    return V2, F2


def join(parts):
    Vs, Fs, o_ = [], [], 0
    for V, F in parts:
        Vs.append(V); Fs += [[i + o_ for i in f] for f in F]; o_ += len(V)
    return np.vstack(Vs), Fs


def rotm(axis, deg):
    return K._rot(axis, math.radians(deg))


def Rx(d):
    return rotm((1, 0, 0), d)


def Ry(d):
    return rotm((0, 1, 0), d)


def Rz(d):
    return rotm((0, 0, 1), d)


# =========================================================================== 1. sculpt parts -> world, crumbs, yaw fix
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
PART_OF = {"Body": "body", "Head": "head", "Mask-Goggles": "goggles", "Icosphere": "mouth", "Cape": "cape"}
BODY_GROUPS = ["body", "head", "goggles", "mouth"]
NAME_OF = {v: k for k, v in PART_OF.items()}
RY = np.diag([-1.0, -1.0, 1.0]) if abs(YAW_FIX_DEG - 180.0) < 1e-9 else np.array(Matrix.Rotation(math.radians(YAW_FIX_DEG), 3, "Z"))
src_objs = sorted([o for o in scene.objects if o.type == "MESH" and not o.hide_render and o.visible_get()], key=lambda o: o.name)
left_out = sorted(o.name for o in scene.objects if o.type == "MESH" and o not in src_objs)
assert sorted(o.name for o in src_objs) == sorted(PART_OF), [o.name for o in src_objs]
dg0 = bpy.context.evaluated_depsgraph_get()
PARTS = {}
report["source_parts"] = {}
tris_source = 0
for o in src_objs:
    M = np.array(o.matrix_world)
    me_e = bpy.data.meshes.new_from_object(o.evaluated_get(dg0))
    V, F = mesh_arrays(me_e, M)
    bpy.data.meshes.remove(me_e)
    det = float(np.linalg.det(M[:3, :3]))
    if det < 0:                                  # negative scale: world-space winding is inverted -> flip back
        F = [f[::-1] for f in F]
    tris_source += tri_count_F(F)
    V, F, cr = keep_islands(V, F, CRUMB_FRAC)
    V = V @ RY.T
    PARTS[PART_OF[o.name]] = (V, F)
    report["source_parts"][o.name] = {"group": PART_OF[o.name], "tris_evaluated": tri_count_F(F), "det": round(det, 4),
                                      "winding_flipped": det < 0, "modifiers": [m.type for m in o.modifiers], "crumbs": cr}
report["source_left_out"] = {n: "hidden in the view layer (artist's unused alternative to Mask-Goggles)" for n in left_out}
report["tris_source"] = tris_source
for o in list(bpy.data.objects):             # the source objects never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)

# =========================================================================== 2. fixes on the sculpt data
BV, BF = PARTS["body"]
BV = BV.copy()
leg_tip_i = {}
for sg in (1, -1):
    m = BV[:, 0] * sg > 0.3
    leg_tip_i[sg] = int(np.argmin(np.where(m, BV[:, 2], np.inf)))
zt = {sg: float(BV[leg_tip_i[sg], 2]) for sg in (1, -1)}
long_sg = 1 if zt[1] <= zt[-1] else -1
short_sg = -long_sg
need = zt[short_sg] - zt[long_sg]
fix = {"leg_tip_z_before": {"+X": round(zt[1], 4), "-X": round(zt[-1], 4)}, "difference": round(need, 4)}
if LEG_EQUALIZE and need > 1e-4:
    tip = BV[leg_tip_i[short_sg]].copy()
    z0 = tip[2] + LEG_EQ_SPAN
    side = BV[:, 0] * short_sg > 0.3
    sl = side & (np.abs(BV[:, 2] - z0) < 0.1)
    c0 = BV[sl].mean(0)
    a = (tip - c0) / np.linalg.norm(tip - c0)
    Dlen = float((tip - c0) @ a)
    db = 0.5 * Dlen
    delta = need / (-a[2])
    kk = delta / (Dlen - db / 2)
    region = side & (BV[:, 2] < z0 + 0.5)
    d = (BV - c0) @ a
    f = np.where(d <= 0, 0.0, np.where(d < db, d * d / (2 * db), d - db / 2))
    BV = BV + np.where(region[:, None], (kk * f)[:, None] * a[None, :], 0.0)
    fix.update({"stretched_leg": "+X" if short_sg > 0 else "-X", "axis": a.round(4).tolist(), "tip_moved": round(float(delta), 4),
                "stretch_factor_past_blend": round(1 + kk, 4), "span": LEG_EQ_SPAN,
                "leg_tip_z_after": round(float(BV[leg_tip_i[short_sg], 2]), 4),
                "rule": "vertices of that leg past a plane LEG_EQ_SPAN above the tip slide along the leg axis, C1 ramp"})
PARTS["body"] = (BV, BF)
floor_hi = float(BV[:, 2].min())
CV_, CF_ = PARTS["cape"]
CV_ = CV_.copy()
zmin_c = float(CV_[:, 2].min())
lift = (floor_hi + CAPE_TIP_CLEAR) - zmin_c
if lift > 0:
    CV_[:, 2] += lift * smoothstep(zmin_c + CAPE_LIFT_SPAN, zmin_c, CV_[:, 2])
PARTS["cape"] = (CV_, CF_)
fix["cape_bottom_lift"] = {"cape_min_z_before_rel_floor": round(zmin_c - floor_hi, 4), "lift": round(lift, 4),
                           "span": CAPE_LIFT_SPAN, "cape_min_z_after_rel_floor": round(float(CV_[:, 2].min()) - floor_hi, 4)}
report["sculpt_fixes"] = fix


# =========================================================================== 3. anatomy: geodesic ring tracing
def graph(V, F):
    E = set()
    for f in F:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            E.add((a, b) if a < b else (b, a))
    E = np.array(sorted(E), dtype=np.int64)
    w = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    src = np.concatenate([E[:, 0], E[:, 1]]); dst = np.concatenate([E[:, 1], E[:, 0]]); ww = np.concatenate([w, w])
    o = np.argsort(src, kind="stable")
    ptr = np.concatenate([[0], np.cumsum(np.bincount(src, minlength=len(V)))])
    return ptr.tolist(), dst[o].tolist(), ww[o].tolist()


def dijkstra(G, n, s0):
    ptr, nb, ww = G
    d = [math.inf] * n
    d[s0] = 0.0
    h = [(0.0, s0)]
    while h:
        dd, v = heapq.heappop(h)
        if dd > d[v]:
            continue
        for j in range(ptr[v], ptr[v + 1]):
            u = nb[j]; nd = dd + ww[j]
            if nd < d[u]:
                d[u] = nd; heapq.heappush(h, (nd, u))
    return np.array(d)


def trace(V, G, tip):
    d = dijkstra(G, len(V), tip)
    rings, k, stop = [], 0, "end"
    while True:
        m = (d >= k * TRACE_DS) & (d < (k + 1) * TRACE_DS)
        if not m.any():
            break
        c = V[m].mean(0); r = float(np.linalg.norm(V[m] - c, axis=1).max())
        if k >= TRACE_MIN_RINGS and r > TRACE_MERGE_K * float(np.median([q[1] for q in rings[-3:]])):
            stop = "merged at ring %d (spread %.3f)" % (k, r)
            break
        rings.append((c, r)); k += 1
    members = np.nonzero(d < k * TRACE_DS)[0]
    poly = np.array([V[tip]] + [c for c, _ in rings])[::-1]          # root -> tip
    return poly, members, {"rings": k, "stop": stop, "tip": V[tip].round(4).tolist(), "root": poly[0].round(4).tolist(),
                           "median_radius": round(float(np.median([q[1] for q in rings])), 4)}


def resample(poly, ext=0.0, step=0.05):
    P = np.array(poly, float)
    for _ in range(2):                         # light smoothing of the ring centroids (ends kept)
        P[1:-1] = (P[:-2] + P[1:-1] + P[2:]) / 3.0
    if ext > 0:
        d0 = P[1] - P[0]; d0 /= np.linalg.norm(d0)
        P = np.vstack([P[0] - d0 * ext, P])
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(s[-1])
    ss = np.linspace(0.0, L, max(2, int(math.ceil(L / step)) + 1))
    return np.stack([np.interp(ss, s, P[:, k]) for k in range(3)], 1), ss


def make_chain(name, kind, side, poly, members, ext, info):
    P, s = resample(poly, ext)
    return {"name": name, "kind": kind, "side": side, "P": P, "s": s, "L": float(s[-1]), "members": members, "info": info}


def chain_at(ch, s):
    s = np.clip(np.asarray(s, float), 0.0, ch["L"])
    return np.stack([np.interp(s, ch["s"], ch["P"][:, k]) for k in range(3)], -1)


t = time.time()
Gb = graph(BV, BF)
CHAINS = {}
BLAB = np.zeros(len(BV), dtype=np.int32)             # 0 torso, 2/3 arm L/R, 4/5 leg L/R
LAB_ID = {"arm.L": 2, "arm.R": 3, "leg.L": 4, "leg.R": 5}
for side, sg in (("L", 1.0), ("R", -1.0)):            # final frame: the character's left = +X
    m = BV[:, 0] * sg > 0.3
    hand = int(np.argmax(np.where(m, np.abs(BV[:, 0]), -1)))
    foot = int(np.argmin(np.where(m, BV[:, 2], np.inf)))
    for kind, tip, ext in (("arm", hand, ARM_ROOT_EXT), ("leg", foot, LEG_ROOT_EXT)):
        poly, mem, info = trace(BV, Gb, tip)
        nm = kind + "." + side
        CHAINS[nm] = make_chain(nm, kind, side, poly, mem, ext, info)
        BLAB[mem] = LAB_ID[nm]
Gc = graph(CV_, CF_)
CLAB = np.full(len(CV_), 20, dtype=np.int32)          # 20 = the shoulder yoke; 10 + c = cape chain c
CAPE_NAMES = ["cape_outer.R", "cape_inner.R", "cape_inner.L", "cape_outer.L"]     # -X .. +X
overlap = 0
for side, sg in (("L", 1.0), ("R", -1.0)):
    m = CV_[:, 0] * sg > 0.3
    inner = int(np.argmin(np.where(m, CV_[:, 2], np.inf)))
    outer = int(np.argmax(np.where(m, np.abs(CV_[:, 0]), -1)))
    for kind, tip in (("cape_inner", inner), ("cape_outer", outer)):
        poly, mem, info = trace(CV_, Gc, tip)
        nm = kind + "." + side
        CHAINS[nm] = make_chain(nm, "cape", side, poly, mem, 0.0, info)
        overlap += int((CLAB[mem] != 20).sum())
        CLAB[mem] = 10 + CAPE_NAMES.index(nm)
report["anatomy"] = {"method": "geodesic rings from each tentacle tip (Dijkstra on the sculpt's edge graph), ring width "
                               "TRACE_DS; stop when a ring's spread exceeds TRACE_MERGE_K x the median of the last 3",
                     "chains": {n: {**c["info"], "length": round(c["L"], 4), "members": int(len(c["members"]))} for n, c in CHAINS.items()},
                     "cape_member_overlap": overlap, "cape_yoke_vertices": int((CLAB == 20).sum()),
                     "body_torso_vertices": int((BLAB == 0).sum()), "seconds": round(time.time() - t, 1),
                     "found": "Body = torso + 2 arm tentacles (bent, paddle tips) + 2 leg tentacles (pointed); Cape = 4 cape "
                              "tentacles (outer + inner per side) joined at a shoulder yoke; NO webs/membranes in the sculpt"}

# =========================================================================== 4. water webs (new geometry)
t = time.time()
BODY_HIGH_V, BODY_HIGH_F = join([PARTS["body"], PARTS["head"]])
bvh_body_hi = BVHTree.FromPolygons(BODY_HIGH_V.tolist(), BODY_HIGH_F)


def signed_dist(bvh, p):
    loc, nrm, _, d = bvh.find_nearest(Vector(p))
    return d if (Vector(p) - loc).dot(nrm) >= 0 else -d


WEBS = [("web.R", "cape_outer.R", "cape_inner.R"), ("web.C", "cape_inner.R", "cape_inner.L"), ("web.L", "cape_inner.L", "cape_outer.L")]
WEB = {}
web_rep = {}
for wn, an, bn in WEBS:
    A, B = CHAINS[an], CHAINS[bn]
    NU, NW = WEB_NU, WEB_NW
    us = np.linspace(0.0, 1.0, NU + 1); ws = np.linspace(0.0, 1.0, NW + 1)
    G = np.zeros((NU + 1, NW + 1, 3)); VV = np.zeros((NU + 1, NW + 1)); GAP = np.zeros((NU + 1, NW + 1))
    for i, u in enumerate(us):
        vedge = WEB_V_EDGE - WEB_SCALLOP * math.sin(math.pi * u)
        v = WEB_V_ROOT + ws * (vedge - WEB_V_ROOT)
        pa = chain_at(A, v * A["L"]); pb = chain_at(B, v * B["L"])
        G[i] = (1 - u) * pa + u * pb
        VV[i] = v
        GAP[i] = np.linalg.norm(pb - pa, axis=1)

    def grid_normals(G):
        du = np.gradient(G, axis=0); dw = np.gradient(G, axis=1)
        n = np.cross(du, dw)
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
        if n[..., 1].mean() < 0:              # orient toward +Y (behind the character: the web's outer face)
            n = -n
        return n
    N0 = grid_normals(G)
    UU, WW = np.meshgrid(us, ws, indexing="ij")
    G = G + (WEB_BILLOW * GAP * np.sin(np.pi * UU) * smoothstep(0.0, 0.35, WW))[..., None] * N0
    # clearance push: interior points inside / too close to the body move out along the web normal; offsets smoothed
    O = np.zeros((NU + 1, NW + 1))
    N1 = grid_normals(G)
    for it in range(8):
        P_ = G + O[..., None] * N1
        pushed = False
        for i in range(1, NU):
            for j in range(NW + 1):
                sd = signed_dist(bvh_body_hi, P_[i, j])
                if sd < WEB_CLEAR:
                    O[i, j] += WEB_CLEAR - sd; pushed = True
        for _ in range(2):
            Os = O.copy()
            Os[1:-1, 1:-1] = np.maximum(O[1:-1, 1:-1], 0.25 * (O[:-2, 1:-1] + O[2:, 1:-1] + O[1:-1, :-2] + O[1:-1, 2:]))
            O = Os
        if not pushed:
            break
    G = G + O[..., None] * N1
    minclear = min(signed_dist(bvh_body_hi, G[i, j]) for i in range(1, NU) for j in range(NW + 1))
    N2 = grid_normals(G)
    # thin closed shell: layer 0 = outer (+n), layer 1 = inner (-n), border stitched
    L0 = G + N2 * (WEB_THICK / 2); L1 = G - N2 * (WEB_THICK / 2)
    idx = lambda l, i, j: l * (NU + 1) * (NW + 1) + i * (NW + 1) + j
    Vw = np.vstack([L0.reshape(-1, 3), L1.reshape(-1, 3)])
    UVW = np.vstack([np.stack([UU.ravel(), WW.ravel(), VV.ravel()], 1)] * 2)
    Fw = []
    for i in range(NU):
        for j in range(NW):
            Fw.append([idx(0, i, j), idx(0, i + 1, j), idx(0, i + 1, j + 1), idx(0, i, j + 1)])
            Fw.append([idx(1, i, j + 1), idx(1, i + 1, j + 1), idx(1, i + 1, j), idx(1, i, j)])
    border = [(i, 0) for i in range(NU)] + [(NU, j) for j in range(NW)] + [(i, NW) for i in range(NU, 0, -1)] + [(0, j) for j in range(NW, 0, -1)]
    for k in range(len(border)):
        (i0, j0), (i1, j1) = border[k], border[(k + 1) % len(border)]
        Fw.append([idx(0, i0, j0), idx(1, i0, j0), idx(1, i1, j1), idx(0, i1, j1)])
    bm = bmesh.new()
    for p in Vw:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in Fw:
        bm.faces.new([bm.verts[i] for i in f])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.verts.index_update()
    Fw = [[v.index for v in f.verts] for f in bm.faces]
    manifold = all(e.is_manifold for e in bm.edges)
    bm.free()
    WEB[wn] = {"V": Vw, "F": Fw, "UVW": UVW, "A": an, "B": bn, "mid": G[NU // 2].copy(), "across": None}
    web_rep[wn] = {"between": [an, bn], "tris": tri_count_F(Fw), "verts": int(len(Vw)), "closed_manifold": manifold,
                   "gap_width_range": [round(float(GAP.min()), 3), round(float(GAP.max()), 3)],
                   "max_clearance_push": round(float(O.max()), 4), "min_body_clearance": round(float(minclear), 4)}
report["webs"] = {"added": "3 water webs (the sculpt has none): thin closed sheets between neighbouring cape tentacles",
                  "per_web": web_rep, "seconds": round(time.time() - t, 1),
                  "shape": {"v_root": WEB_V_ROOT, "v_edge": WEB_V_EDGE, "scallop": WEB_SCALLOP, "billow": WEB_BILLOW,
                            "thickness": WEB_THICK, "clearance": WEB_CLEAR, "grid": [WEB_NU, WEB_NW]}}

# =========================================================================== 5. low: two shells (remesh + decimate) + webs
t = time.time()


def shell(groups, target):
    V, F = join([PARTS[g] for g in groups])
    tmp = new_obj("remesh_src", V, F)
    rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = VOXEL; rm.use_smooth_shade = False
    RV, RF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF = decimate(RV, RF, target)
    LV, LF, sp = keep_islands(LV, LF, 0.01)
    return LV, LF, {"remesh_tris": tri_count_F(RF), "decimated_tris": tri_count_F(LF), "specks_dropped": sp}


SBV, SBF, rb_ = shell(BODY_GROUPS, LOW_TRIS_BODY)
SCV, SCF, rc_ = shell(["cape"], LOW_TRIS_CAPE)
allV = np.vstack([SBV, SCV] + [w["V"] for w in WEB.values()])
lo2, hi2 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
SBV = SBV - SHIFT; SCV = SCV - SHIFT
PARTS = {k: (v - SHIFT, f) for k, (v, f) in PARTS.items()}
BV = PARTS["body"][0]; CV_ = PARTS["cape"][0]
BODY_HIGH_V = BODY_HIGH_V - SHIFT
for c in CHAINS.values():
    c["P"] = c["P"] - SHIFT
for w in WEB.values():
    w["V"] = w["V"] - SHIFT; w["mid"] = w["mid"] - SHIFT
H = float(hi2[2] - lo2[2])
size = hi2 - lo2
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
GAME_M_PER_UNIT = k_fit
report["natural"] = {"height": round(H, 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "footprint": round(fp, 4), "units": "sculpt units (natural proportions, no refit)",
                     "origin_shift_sculpt_units_after_yaw": SHIFT.round(5).tolist(),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint",
                                                     "ceilings": [CELL_MAX_H, CELL_MAX_FP]}}
report["retopo"] = {"method": "body shell (Body+Head+goggles+mouth joined) and cape shell each: voxel remesh (%.3f) -> collapse "
                              "decimation (deterministic); remeshed separately so the cape never fuses to the back" % VOXEL,
                    "body_shell": rb_, "cape_shell": rc_, "seconds": round(time.time() - t, 1)}
# retopo fidelity: low -> sculpt (every low vertex to the nearest sculpt surface of its shell's parts)
_bgV, _bgF = join([PARTS[g] for g in BODY_GROUPS])
bvh_bodyg = BVHTree.FromPolygons(_bgV.tolist(), _bgF)
bvh_cape = BVHTree.FromPolygons(PARTS["cape"][0].tolist(), PARTS["cape"][1])
dev_b = np.array([bvh_bodyg.find_nearest(Vector(p))[3] for p in SBV])
dev_c = np.array([bvh_cape.find_nearest(Vector(p))[3] for p in SCV])
dev = np.concatenate([dev_b, dev_c])
PB = {g: BVHTree.FromPolygons(PARTS[g][0].tolist(), PARTS[g][1]) for g in PARTS}
bvh_lowb = BVHTree.FromPolygons(SBV.tolist(), SBF)
bvh_lowc = BVHTree.FromPolygons(SCV.tolist(), SCF)


def inside(bvh, p):
    loc, nrm, _, d = bvh.find_nearest(Vector(p))
    return loc is not None and (Vector(p) - loc).dot(nrm) < 0


vis_rows, all_vis = {}, []
for g in sorted(PARTS):
    V = PARTS[g][0]
    idx_ = np.arange(0, len(V), max(1, len(V) // 4000))
    others = [o for o in PB if o != g and (o in BODY_GROUPS) == (g in BODY_GROUPS)]
    vis = [i for i in idx_ if not any(inside(PB[o], V[i]) for o in others)]
    bl = bvh_lowc if g == "cape" else bvh_lowb
    d_ = np.array([bl.find_nearest(Vector(V[i]))[3] for i in vis])
    all_vis.append(d_)
    vis_rows[g] = {"sampled": int(len(idx_)), "visible": len(vis), "p99": round(float(np.percentile(d_, 99)), 4),
                   "max": round(float(d_.max()), 4)}
all_vis = np.concatenate(all_vis)
report["retopo"]["low_to_sculpt_distance"] = {"mean": round(float(dev.mean()), 4), "p99": round(float(np.percentile(dev, 99)), 4),
                                              "max": round(float(dev.max()), 4), "pct_of_height_p99": round(100 * float(np.percentile(dev, 99)) / H, 3),
                                              "body_shell_p99": round(float(np.percentile(dev_b, 99)), 4),
                                              "cape_shell_p99": round(float(np.percentile(dev_c, 99)), 4)}
report["retopo"]["visible_sculpt_to_low_distance"] = {"mean": round(float(all_vis.mean()), 4), "p99": round(float(np.percentile(all_vis, 99)), 4),
                                                      "max": round(float(all_vis.max()), 4), "per_part": vis_rows,
                                                      "rule": "sculpt vertices (subsampled ~4k per part) not inside another part of the same shell -> nearest low surface of that shell"}

# =========================================================================== landmarks (final frame)
leg_roots = [CHAINS["leg.L"]["P"][0], CHAINS["leg.R"]["P"][0]]
Z_HIP = float(np.mean([p[2] for p in leg_roots]))
HB_V = PARTS["body"][0]; HH_V = PARTS["head"][0]
Z_NECK = 0.5 * (float(HB_V[:, 2].max()) + float(HH_V[:, 2].min()))
Z_TOP = float(HH_V[:, 2].max())
torso_hi = (BLAB == 0) & (np.abs(HB_V[:, 0]) < 1.5)


def torso_y(z):
    m = torso_hi & (np.abs(HB_V[:, 2] - z) < 0.3)
    return float(HB_V[m, 1].mean()) if m.any() else float(HB_V[torso_hi, 1].mean())


SPINE_Z = [Z_HIP + f * (Z_NECK - Z_HIP) for f in SPINE_FRACS]
SPINE_J = [np.array([0.0, torso_y(z), z]) for z in SPINE_Z]
Y_AXIS = float(np.mean([p[1] for p in SPINE_J]))

# =========================================================================== fields (per low shell vertex)
t = time.time()
SV = np.vstack([SBV, SCV]); SF = SBF + [[i + len(SBV) for i in f] for f in SCF]
nb_, nc_ = len(SBV), len(SCV)
shell_id = np.concatenate([np.zeros(nb_), np.ones(nc_)])
DGd = np.array([[PB[g].find_nearest(Vector(p))[3] for g in BODY_GROUPS] for p in SBV])


def voronoi(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]


VOR = {g: np.concatenate([voronoi(DGd, j), np.full(nc_, -1.0)]) for j, g in enumerate(BODY_GROUPS)}
# goggle lenses: an ellipse per goggle (centre + half extents of that side's goggle part)
GV = PARTS["goggles"][0]
LENS = {}
for sg in (1.0, -1.0):
    m = GV[:, 0] * sg > 0.3
    lo_, hi_ = GV[m].min(0), GV[m].max(0)
    LENS[sg] = ((lo_ + hi_) / 2, (hi_ - lo_) / 2)
lens_f = np.array([math.hypot((p[0] - LENS[1.0 if p[0] >= 0 else -1.0][0][0]) / LENS[1.0 if p[0] >= 0 else -1.0][1][0],
                              (p[2] - LENS[1.0 if p[0] >= 0 else -1.0][0][2]) / LENS[1.0 if p[0] >= 0 else -1.0][1][2]) for p in SV])
# chain membership + arc fraction: nearest sculpt vertex's traced label, arc = nearest centreline sample
kd_b = KDTree(len(BV))
for i, p in enumerate(BV):
    kd_b.insert(p, i)
kd_b.balance()
kd_c = KDTree(len(CV_))
for i, p in enumerate(CV_):
    kd_c.insert(p, i)
kd_c.balance()
CH_KD = {}
for n_, c in CHAINS.items():
    kd = KDTree(len(c["P"]))
    for i, p in enumerate(c["P"]):
        kd.insert(p, i)
    kd.balance()
    CH_KD[n_] = kd
ID_TO_CH = {2: "arm.L", 3: "arm.R", 4: "leg.L", 5: "leg.R"}
ID_TO_CH.update({10 + i: n for i, n in enumerate(CAPE_NAMES)})


def label_and_arc(V, is_cape):
    lab = np.array([(CLAB if is_cape else BLAB)[(kd_c if is_cape else kd_b).find(p)[1]] for p in V], dtype=np.int32)
    s = np.full(len(V), -1.0)
    for i, p in enumerate(V):
        ch = ID_TO_CH.get(int(lab[i]))
        if ch:
            s[i] = float(CHAINS[ch]["s"][CH_KD[ch].find(p)[1]])
    return lab, s


lab_b, s_b = label_and_arc(SBV, False)
lab_c, s_c = label_and_arc(SCV, True)
LAB0 = np.concatenate([lab_b, lab_c]); S0 = np.concatenate([s_b, s_c])
frac = np.array([S0[i] / CHAINS[ID_TO_CH[int(LAB0[i])]]["L"] if int(LAB0[i]) in ID_TO_CH else -1.0 for i in range(len(SV))])
armf = np.where(np.isin(LAB0, [2, 3]) & (shell_id == 0), frac, -1.0)
legf = np.where(np.isin(LAB0, [4, 5]) & (shell_id == 0), frac, -1.0)
capef = np.where((LAB0 >= 10) & (LAB0 < 20) & (shell_id == 1), frac, -1.0)
# cape lining: the smoothed low vertex normal against the horizontal direction to the body axis
cme = bpy.data.meshes.new("tmp_cape"); cme.from_pydata(SCV.tolist(), [], SCF); cme.update()
cn = np.empty(nc_ * 3); cme.vertex_normals.foreach_get("vector", cn); cn = cn.reshape(-1, 3)
bpy.data.meshes.remove(cme)
inward = np.stack([-SCV[:, 0], Y_AXIS - SCV[:, 1], np.zeros(nc_)], 1)
inward /= np.maximum(np.linalg.norm(inward, axis=1, keepdims=True), 1e-9)
under = np.concatenate([np.full(nb_, -2.0), (cn * inward).sum(1)])
# chest emblem: centre = the most forward torso-surface vertex near the midline at the emblem height
Z_EMB = Z_HIP + EMBLEM_Z_FRAC * (Z_NECK - Z_HIP)
cand = (shell_id[:nb_] == 0) & (lab_b == 0) & (np.abs(SBV[:, 0]) < 0.35) & (np.abs(SBV[:, 2] - Z_EMB) < 0.35) & (VOR["body"][:nb_] > 0)
EMB_C = SBV[np.nonzero(cand)[0][np.argmin(SBV[cand, 1])]].copy()
emb = np.concatenate([np.linalg.norm(SBV - EMB_C, axis=1), np.full(nc_, 99.0)])
emb = np.where(np.concatenate([SBV[:, 1] < EMB_C[1] + 1.2, np.zeros(nc_, bool)]), emb, 99.0)
FIELDS = {"fb": VOR["body"], "fh": VOR["head"], "fg": VOR["goggles"], "fm": VOR["mouth"], "lens": lens_f, "armf": armf,
          "legf": legf, "capef": capef, "under": under, "emb": emb, "shell": shell_id}
report["fields_seconds"] = round(time.time() - t, 1)

# =========================================================================== iso-contour cuts (duskmaw's cutter)
bm = bmesh.new()
for p in SV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in SF:
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


TOL = 0.02
in_b = lambda v: v[LAY["shell"]] < 0.5
in_c = lambda v: v[LAY["shell"]] > 0.5
g_body = lambda a, b: in_b(a) and in_b(b)
g_gog = lambda a, b: g_body(a, b) and a[LAY["fg"]] > -TOL and b[LAY["fg"]] > -TOL
g_arm = lambda a, b: g_body(a, b) and a[LAY["armf"]] >= 0 and b[LAY["armf"]] >= 0
g_leg = lambda a, b: g_body(a, b) and a[LAY["legf"]] >= 0 and b[LAY["legf"]] >= 0
g_emb = lambda a, b: g_body(a, b) and a[LAY["fb"]] > -TOL and b[LAY["fb"]] > -TOL and a[LAY["emb"]] < 3 * EMBLEM_R and b[LAY["emb"]] < 3 * EMBLEM_R
g_cape = lambda a, b: in_c(a) and in_c(b)
g_ctip = lambda a, b: g_cape(a, b) and a[LAY["capef"]] >= 0 and b[LAY["capef"]] >= 0
CUTS = [("fh", 0.0, g_body), ("fg", 0.0, g_body), ("fm", 0.0, g_body), ("lens", LENS_K, g_gog), ("armf", ARM_TIP_FRAC, g_arm),
        ("legf", LEG_TIP_FRAC, g_leg), ("emb", EMBLEM_R, g_emb), ("under", UNDER_T, g_cape), ("capef", CAPE_TIP_FRAC, g_ctip)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
straddle = {}
for key, tau, gate in CUTS:
    L = LAY[key]; eps = 1e-5 * max(1.0, abs(tau)); n_ = 0
    for f in bm.faces:
        vs = list(f.verts)
        if gate is not None and not all(gate(vs[i], vs[(i + 1) % len(vs)]) for i in range(len(vs))):
            continue
        vals = [v[L] for v in vs]
        if min(vals) < tau - eps and max(vals) > tau + eps:
            n_ += 1
    straddle["%s=%.3f" % (key, tau)] = n_
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1),
                      "rule": "edge split at the field's iso-value (snap within CUT_SNAP of a vertex) + face connects; "
                              "fields interpolate linearly along split edges; no face straddles any boundary afterwards",
                      "straddling_faces_after_cut": straddle}
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
VFIELD = {k: np.array([v[LAY[k]] for v in bm.verts]) for k in LAY}
bm.free()

# =========================================================================== regions + final mesh (cut shells + webs)
REG = ["skin", "head", "arm_tip", "leg_tip", "emblem", "mask", "lens", "mouth", "cape", "cape_under", "cape_tip",
       "membrane", "membrane_rim"]
R_ = {n: i for i, n in enumerate(REG)}
nf_c = len(CF)
fsh = FVAL["shell"] > 0.5
owner = np.argmax(np.stack([FVAL["fb"], FVAL["fh"], FVAL["fg"], FVAL["fm"]], 1), 1)
rid = np.full(nf_c, R_["skin"], dtype=np.int32)
rid[~fsh & (owner == 1)] = R_["head"]
rid[~fsh & (owner == 2)] = R_["mask"]
rid[~fsh & (owner == 2) & (FVAL["lens"] < LENS_K)] = R_["lens"]
rid[~fsh & (owner == 3)] = R_["mouth"]
bodyf = ~fsh & (owner == 0)
rid[bodyf & (FVAL["armf"] > ARM_TIP_FRAC)] = R_["arm_tip"]
rid[bodyf & (FVAL["legf"] > LEG_TIP_FRAC)] = R_["leg_tip"]
rid[bodyf & (FVAL["emb"] < EMBLEM_R)] = R_["emblem"]
rid[fsh] = R_["cape"]
rid[fsh & (FVAL["under"] > UNDER_T)] = R_["cape_under"]
rid[fsh & (FVAL["capef"] > CAPE_TIP_FRAC)] = R_["cape_tip"]
FV = [CV]; FF = list(CF); o_ = len(CV)
WEB_VRANGE = {}
web_face_w = []
for wn in ("web.R", "web.C", "web.L"):
    w = WEB[wn]
    WEB_VRANGE[wn] = (o_, o_ + len(w["V"]))
    FV.append(w["V"]); FF += [[i + o_ for i in f] for f in w["F"]]
    web_face_w += [float(min(w["UVW"][i, 1] for i in f)) for f in w["F"]]      # min: whole grid rows, no zig-zag
    o_ += len(w["V"])
FV = np.vstack(FV)
web_face_w = np.array(web_face_w)
rid = np.concatenate([rid, np.where(web_face_w > WEB_RIM_W, R_["membrane_rim"], R_["membrane"]).astype(np.int32)])
N_WEB_TRIS = int(len(web_face_w))
low_me = bpy.data.meshes.new(UNIT)
low_me.from_pydata(FV.tolist(), [], FF)
low_me.update()
low = bpy.data.objects.new(UNIT, low_me)
scene.collection.objects.link(low)
me = low.data
nf = len(me.polygons)
assert nf == len(rid)
report["tris_final"] = int(sum(len(f) - 2 for f in FF))
report["tris_breakdown"] = {"body_shell_and_cape_shell_after_cuts": tri_count_F(CF), "webs": N_WEB_TRIS}
report["region_rule"] = {
    "part_voronoi": "body-shell vertex owner = the nearest source part (Body / Head / Mask-Goggles / mouth); boundaries cut at 0",
    "lens": "goggle surface inside LENS_K of that goggle's half-extent ellipse (x, z)", "mask": "the rest of the goggles",
    "arm_tip / leg_tip": "the traced arm / leg tentacle past ARM_TIP_FRAC / LEG_TIP_FRAC of its centreline",
    "emblem": "chest disc of radius EMBLEM_R around the most forward torso point at EMBLEM_Z_FRAC (hip -> neck)",
    "cape_under": "cape surface whose smoothed normal faces the body axis more than UNDER_T (the sucker-side lining)",
    "cape_tip": "cape tentacle past CAPE_TIP_FRAC", "membrane": "the built water webs", "membrane_rim": "web faces past WEB_RIM_W of the depth (the free edge)"}

# cavity shade + deterministic per-face value jitter (improve_unit.py rule), cavity from the high sculpt
t = time.time()
HV_all, HF_all = join([PARTS[g] for g in sorted(PARTS)])
HIGH = new_obj(UNIT + "_high", HV_all, HF_all)
kd_h = KDTree(len(HV_all))
for i, p in enumerate(HV_all):
    kd_h.insert(p, i)
kd_h.balance()
hme = HIGH.data
ev_h = np.empty(len(hme.edges) * 2, dtype=np.int64); hme.edges.foreach_get("vertices", ev_h); ev_h = ev_h.reshape(-1, 2)
nv_h = np.empty(len(hme.vertices) * 3); hme.vertex_normals.foreach_get("vector", nv_h); nv_h = nv_h.reshape(-1, 3)
deg = np.bincount(ev_h.ravel(), minlength=len(HV_all)).astype(float)


def nmean(X):
    s_ = np.zeros_like(X)
    for k in range(X.shape[1]):
        s_[:, k] = np.bincount(ev_h[:, 0], X[ev_h[:, 1], k], minlength=len(HV_all)) + np.bincount(ev_h[:, 1], X[ev_h[:, 0], k], minlength=len(HV_all))
    return s_ / np.maximum(deg, 1)[:, None]


el = np.linalg.norm(HV_all[ev_h[:, 0]] - HV_all[ev_h[:, 1]], axis=1).mean()
cav = ((nmean(HV_all) - HV_all) * nv_h).sum(1) / el
for _ in range(6):
    cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
cav = np.clip(cav / (np.percentile(np.abs(cav), 95) + 1e-9), -1, 1)
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
fcav = np.array([np.mean([cav[j] for (_, j, _) in kd_h.find_n(p, 8)]) for p in FC])
fcav[nf_c:] = 0.0                                     # webs: no sculpt beneath them
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k
report["cavity_seconds"] = round(time.time() - t, 1)
fa = np.empty(nf); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == R_[n]].sum() / fa.sum()), 4) for n in REG}

# facing landmark (direction-free): the head (mantle) bbox centre -> the goggles' centroid, before and after the yaw fix
HVf = PARTS["head"][0]
anchor = (HVf.min(0) + HVf.max(0)) / 2
landmark = PARTS["goggles"][0].mean(0)
dvec = landmark - anchor
src_d = RY.T @ dvec
mouth_c = PARTS["mouth"][0].mean(0)
report["facing"] = {"rule": "head (mantle) bbox centre -> centroid of the Mask-Goggles part (the mouth sits below it on the same side)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "source_angle_from_minusY_deg": round(math.degrees(math.atan2(src_d[0], -src_d[1])), 2),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2),
                    "mouth_angle_from_minusY_deg": round(math.degrees(math.atan2((mouth_c - anchor)[0], -(mouth_c - anchor)[1])), 2),
                    "cape_centroid_y": round(float(PARTS["cape"][0][:, 1].mean()), 4)}

# =========================================================================== flat + UV
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
low["conquest_tier"] = "regular"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = YAW_FIX_DEG
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"
low["conquest_locomotion"] = "upright biped walk on the two leg tentacles; cape tentacles + water webs are secondary motion"

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris_final", "tris_breakdown", "retopo", "regions_faces", "regions_area_share",
                                                             "facing", "iso_cuts", "natural", "sculpt_fixes", "anatomy", "webs")}))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 7. bake (normal + AO from the sculpt parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
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
web_faces = np.isin(rid, [R_["membrane"], R_["membrane_rim"]])
web_tx_n = texels_of(web_faces, RN)
web_tx_a = texels_of(web_faces, RA)
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = (px[:, 2] > 0.25) & ~web_tx_n
cov_a = (np.abs(pa[:, 0] - pa[:, 1]) < 0.02) & ~web_tx_a
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
sculpt_tex_n = alltex_n & ~web_tx_n
sculpt_tex_a = alltex_a & ~web_tx_a
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "web_texels_reset_flat": {"normal": int(web_tx_n.sum()), "ao": int(web_tx_a.sum())},
    "normal_baked_pct_of_sculpt_uv_texels": round(100 * float(cov_n[sculpt_tex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & sculpt_tex_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(devn[cov_n & sculpt_tex_n].mean()), 4),
    "ao_baked_pct_of_sculpt_uv_texels": round(100 * float(cov_a[sculpt_tex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & sculpt_tex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & sculpt_tex_a, 0], 5)), 4)})
per_region = {}
for n_ in REG:
    fm_ = rid == R_[n_]
    if fm_.any() and n_ not in ("membrane", "membrane_rim"):
        tx = texels_of(fm_, RN)
        per_region[n_] = {"texels": int(tx.sum()), "baked_pct": round(100 * float(cov_n[tx].mean()), 2),
                          "normal_dev_mean": round(float(devn[tx & cov_n].mean()), 4) if (tx & cov_n).any() else None}
bstats["per_region_normal"] = per_region
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; pa[web_tx_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


# hash the 8-bit quantization (the shipped artifact is the 8-bit PNG), and dump the float buffers for the runner's bake
# tolerance gate (vampito/duskmaw pattern: a per-process Cycles tie-break can flip a few texels by one 8-bit step).
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), "supaoctto_normal_twin.npy" if DIGEST_ONLY else "supaoctto_normal_main.npy"), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), "supaoctto_ao_twin.npy" if DIGEST_ONLY else "supaoctto_ao_main.npy"), pa[:, :3])
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["high_tris"] = report["tris_source"]
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
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
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()),
                "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
DIG["geometry_colour"] = report["digest_geometry_colour"]; DIG["uv"] = report["uv"]["uv_sha"]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "no shipped colours exist (the source blend is material-less, no Conquest glb): authored"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 8. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
W_ = LVf.copy()
NVS = len(CV)                                          # shell vertices (cut); webs follow
is_web = np.zeros(len(W_), bool); is_web[NVS:] = True
vshell = np.concatenate([VFIELD["shell"], np.full(len(W_) - NVS, 2.0)])
# per-vertex label + arc on the final (cut) shells: nearest sculpt vertex's label, nearest centreline sample
labF = np.full(len(W_), -1, dtype=np.int32); sF = np.full(len(W_), -1.0)
for i in range(NVS):
    cape_ = VFIELD["shell"][i] > 0.5
    lab_ = int((CLAB if cape_ else BLAB)[(kd_c if cape_ else kd_b).find(W_[i])[1]])
    labF[i] = lab_
    ch = ID_TO_CH.get(lab_)
    if ch:
        sF[i] = float(CHAINS[ch]["s"][CH_KD[ch].find(W_[i])[1]])
# leg tips (the IK contact points): the lowest final vertex of each leg
TIP = {}
for side in ("L", "R"):
    m = labF == LAB_ID["leg." + side]
    TIP[side] = W_[np.nonzero(m)[0][np.argmin(W_[m, 2])]].copy()
HAND_TIP = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    m = labF == LAB_ID["arm." + side]
    HAND_TIP[side] = W_[np.nonzero(m)[0][np.argmax(W_[m, 0] * sg)]].copy()

BONES = []                                             # (name, head, tail, parent)
BONES += [("pelvis", SPINE_J[0], SPINE_J[1], "root"), ("spine", SPINE_J[1], SPINE_J[2], "pelvis"),
          ("chest", SPINE_J[2], SPINE_J[3], "spine"), ("head", SPINE_J[3], np.array([0.0, SPINE_J[3][1], Z_TOP]), "chest")]
CH_BONES = {}
for side in ("L", "R"):
    c = CHAINS["leg." + side]
    kn = chain_at(c, LEG_KNEE_FRAC * c["L"]); an = chain_at(c, LEG_ANKLE_FRAC * c["L"])
    BONES += [("thigh." + side, c["P"][0], kn, "pelvis"), ("shin." + side, kn, an, "thigh." + side), ("foot." + side, an, TIP[side], "shin." + side)]
    CH_BONES["leg." + side] = (["thigh." + side, "shin." + side, "foot." + side], np.array([0.0, LEG_KNEE_FRAC, LEG_ANKLE_FRAC, 1.0]) * c["L"])
for side in ("L", "R"):
    c = CHAINS["arm." + side]
    kn = np.linspace(0.0, c["L"], ARM_BONES + 1)
    pts = chain_at(c, kn); pts[-1] = HAND_TIP[side]
    names = ["arm.%s.%d" % (side, k) for k in range(ARM_BONES)]
    for k in range(ARM_BONES):
        BONES.append((names[k], pts[k], pts[k + 1], "chest" if k == 0 else names[k - 1]))
    CH_BONES["arm." + side] = (names, kn)
for cn_ in CAPE_NAMES:
    c = CHAINS[cn_]
    kn = np.linspace(0.0, c["L"], CAPE_BONES + 1)
    pts = chain_at(c, kn)
    names = ["%s.%d" % (cn_, k) for k in range(CAPE_BONES)]
    for k in range(CAPE_BONES):
        BONES.append((names[k], pts[k], pts[k + 1], "chest" if k == 0 else names[k - 1]))
    CH_BONES[cn_] = (names, kn)
for wn in ("web.R", "web.C", "web.L"):
    mid = WEB[wn]["mid"]
    seg = np.linalg.norm(np.diff(mid, axis=0), axis=1); sm = np.concatenate([[0.0], np.cumsum(seg)])
    Lm = float(sm[-1])
    kn = np.linspace(0.0, Lm, WEB_BONES + 1)
    pts = np.stack([np.interp(kn, sm, mid[:, k]) for k in range(3)], 1)
    names = ["%s.%d" % (wn, k) for k in range(WEB_BONES)]
    for k in range(WEB_BONES):
        BONES.append((names[k], pts[k], pts[k + 1], "chest" if k == 0 else names[k - 1]))
    CH_BONES[wn] = (names, kn)
    WEB[wn]["Lmid"] = Lm
    A_, B_ = CHAINS[WEB[wn]["A"]], CHAINS[WEB[wn]["B"]]
    ax = chain_at(B_, WEB_V_ROOT * B_["L"]) - chain_at(A_, WEB_V_ROOT * A_["L"]); ax /= np.linalg.norm(ax)
    d_ = pts[-1] - pts[0]; d_ /= np.linalg.norm(d_)
    nrm_ = np.cross(pts[1] - pts[0], ax)
    if np.cross(ax, d_) @ np.array([0.0, 1.0, 0.0]) < 0:  # +angle pushes the web's middle outward (+Y, away from the back)
        ax = -ax
    WEB[wn]["across"] = ax
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
Hh = float(W_[:, 2].max())
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.06 * Hh); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_); e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = False
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
PARENT = {b[0]: b[3] for b in BONES}
HEADP = {b[0]: np.array(b[1], float) for b in BONES}
REST4 = {b.name: np.array(b.matrix_local) for b in arm_data.bones}


def hat(s, mids):
    s = np.asarray(s, float); n = len(mids)
    W = np.zeros((len(s), n))
    lo = s <= mids[0]; W[lo, 0] = 1.0
    hi = s >= mids[-1]; W[hi, n - 1] = 1.0
    mid = ~lo & ~hi
    if mid.any():
        k = np.clip(np.searchsorted(mids, s[mid]) - 1, 0, n - 2)
        u = (s[mid] - mids[k]) / (mids[k + 1] - mids[k])
        ix = np.nonzero(mid)[0]
        W[ix, k] = 1 - u; W[ix, k + 1] = u
    return W


# ---- analytic weights (<= 4 influences)
nV = len(W_)
Wt = np.zeros((nV, len(DEFORM)))
# torso: hat over pelvis / spine / chest by height; head blended in across the head-part boundary
sp_mid = np.array([(SPINE_Z[k] + SPINE_Z[k + 1]) / 2 for k in range(3)])
WT = np.zeros((nV, len(DEFORM)))
WT[:, [J["pelvis"], J["spine"], J["chest"]]] = hat(W_[:, 2], sp_mid)
headf = np.concatenate([np.maximum.reduce([VFIELD["fh"], VFIELD["fg"], VFIELD["fm"]]), np.full(nV - NVS, -1.0)])
headness = smoothstep(-NECK_BAND, NECK_BAND, headf) * (vshell == 0)
WT = WT * (1 - headness)[:, None]
WT[:, J["head"]] += headness
CHEST1 = np.zeros(len(DEFORM)); CHEST1[J["chest"]] = 1.0


def chain_weights(ch_name, s, parentW, blend_start, blend_len):
    names, kn = CH_BONES[ch_name]
    mids = (kn[:-1] + kn[1:]) / 2
    Wc = hat(s, mids)
    tt = smoothstep(blend_start, blend_start + blend_len, s)
    W = parentW * (1 - tt)[:, None]
    for k, n in enumerate(names):
        W[:, J[n]] += Wc[:, k] * tt
    return W


body_v = (vshell == 0)
Wt[body_v] = WT[body_v]
blend_rep = {}
for side in ("L", "R"):
    for kind in ("arm", "leg"):
        chn = kind + "." + side
        m = body_v & (labF == LAB_ID[chn])
        s_ = sF[m]
        sb = float(np.percentile(s_, 5))
        Wt[m] = chain_weights(chn, s_, WT[m], sb, ROOT_BLEND[kind])
        blend_rep[chn] = {"members": int(m.sum()), "root_blend_from_s": round(sb, 4)}
cape_v = vshell == 1
Wt[cape_v] = CHEST1
for i_c, cn_ in enumerate(CAPE_NAMES):
    m = cape_v & (labF == 10 + i_c)
    Wt[m] = chain_weights(cn_, sF[m], np.tile(CHEST1, (int(m.sum()), 1)), 0.0, ROOT_BLEND["cape"])
    blend_rep[cn_] = {"members": int(m.sum())}
# webs: side chains' weights at the same arc fraction, mixed into the web's own mid chain across the gap
for wn in ("web.R", "web.C", "web.L"):
    a0, a1 = WEB_VRANGE[wn]
    U, Wd, Vv = WEB[wn]["UVW"][:, 0], WEB[wn]["UVW"][:, 1], WEB[wn]["UVW"][:, 2]
    A_, B_ = WEB[wn]["A"], WEB[wn]["B"]
    n_ = a1 - a0
    WA = chain_weights(A_, Vv * CHAINS[A_]["L"], np.tile(CHEST1, (n_, 1)), 0.0, ROOT_BLEND["cape"])
    WB = chain_weights(B_, Vv * CHAINS[B_]["L"], np.tile(CHEST1, (n_, 1)), 0.0, ROOT_BLEND["cape"])
    names, kn = CH_BONES[wn]
    WM = chain_weights(wn, Wd * WEB[wn]["Lmid"], np.tile(CHEST1, (n_, 1)), 0.0, float(kn[1]) * 0.5)
    mA = smoothstep(0.0, 0.5, U); mB = smoothstep(1.0, 0.5, U)
    Wweb = np.where((U <= 0.5)[:, None], WA * (1 - mA)[:, None] + WM * mA[:, None], WB * (1 - mB)[:, None] + WM * mB[:, None])
    Wt[a0:a1] = Wweb
Wt = np.where(Wt > 1e-4, Wt, 0.0)
infl_pre = int((Wt > 0).sum(1).max())
capped = int(((Wt > 0).sum(1) > 4).sum())
if infl_pre > 4:
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
rep["weights"] = {"max_influences": int(infl.max()), "max_influences_before_cap": infl_pre, "vertices_capped_to_4": capped,
                  "unweighted": int((infl == 0).sum()), "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "chains": blend_rep,
                  "rule": "torso = hat over pelvis/spine/chest by height, head blended over the head-part Voronoi +-NECK_BAND; "
                          "arm/leg/cape vertices = their traced chain, hat weights along the centreline arc, blended from the "
                          "parent (torso weights / chest) over ROOT_BLEND at the chain root; webs = the two side chains at the "
                          "same arc fraction, mixed into the web's own mid chain toward the middle of the gap"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig


# ---- clips: FK in numpy (deform transforms D_b, armature space), keyed as pose-bone bases every frame
def Tr(h, R):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = h - R @ h
    return M


def Tt(v):
    M = np.eye(4); M[:3, 3] = v
    return M


LEG = {}
for side in ("L", "R"):
    a = HEADP["shin." + side] - HEADP["thigh." + side]
    b = TIP[side] - HEADP["shin." + side]
    LEG[side] = {"a": a, "b": b, "r0": float(np.hypot(a[1] + b[1], a[2] + b[2])), "la": float(np.hypot(a[1], a[2])),
                 "lb": float(np.hypot(b[1], b[2]))}
LEG_R0 = float(np.mean([LEG[s]["r0"] for s in LEG]))
PLANT_Z = 0.0                                          # both leg-tentacle tips plant on the contract floor
STEP_A = WALK_STEP_FRAC * LEG_R0
LIFT = WALK_LIFT_FRAC * LEG_R0


def ik_leg(side, hip_p, ty, tz):
    a, b = LEG[side]["a"], LEG[side]["b"]
    la, lb = LEG[side]["la"], LEG[side]["lb"]
    d = np.array([ty - hip_p[1], tz - hip_p[2]]); Dn = float(np.linalg.norm(d))
    Dc = float(np.clip(Dn, abs(la - lb) + 1e-6, (la + lb) * (1 - 1e-4)))
    ang_d = math.atan2(d[1], d[0])
    g = math.acos(float(np.clip((la * la + Dc * Dc - lb * lb) / (2 * la * Dc), -1.0, 1.0)))
    best = None
    for sgn in (1.0, -1.0):                               # knee forward (toward -Y, the front)
        aa = ang_d + sgn * g
        kn = la * np.array([math.cos(aa), math.sin(aa)])
        if best is None or kn[0] < best[1][0]:
            best = (aa, kn)
    aa, kn = best
    tip2 = Dc * np.array([math.cos(ang_d), math.sin(ang_d)])
    bb = math.atan2(tip2[1] - kn[1], tip2[0] - kn[0])
    p1 = math.degrees(aa - math.atan2(a[2], a[1])); p2 = math.degrees(bb - math.atan2(b[2], b[1]))
    R1, R2 = Rx(p1), Rx(p2)
    knee_p = hip_p + R1 @ a
    D_th = Tt(hip_p) @ Tr(np.zeros(3), R1) @ Tt(-HEADP["thigh." + side])
    D_sh = Tt(knee_p) @ Tr(np.zeros(3), R2) @ Tt(-HEADP["shin." + side])
    return D_th, D_sh, Dn - Dc


def hip_rest(side, Rp):
    return HEADP["pelvis"] + Rp @ (HEADP["thigh." + side] - HEADP["pelvis"])


def pelvis_drop(Rp, contacts, reach_k):
    best = math.inf
    for side, ty in contacts:
        h0 = hip_rest(side, Rp)
        r_t = reach_k * LEG[side]["r0"]
        dy = ty - h0[1]
        hz = math.sqrt(max(r_t * r_t - dy * dy, 1e-9))
        best = min(best, (PLANT_Z + hz) - h0[2])
    return best


def chain_rel(D, names, rots):
    for k, n in enumerate(names):
        D[n] = D[PARENT[n]] @ Tr(HEADP[n], rots[k])


def pose(clip, f):
    """Deform transforms of frame f (0-based within the period) -> ({bone: 4x4}, info)."""
    N = IDLE_N if clip == "idle" else WALK_N
    t = f / N
    D = {"root": np.eye(4)}
    info = {}
    if clip == "idle":
        Rp = np.eye(3)
        dz0 = min(0.0, pelvis_drop(Rp, [("L", TIP["L"][1]), ("R", TIP["R"][1])], IDLE_REACH_K))
        off = np.array([0.0, 0.0, dz0 - IDLE_BREATH * (0.5 - 0.5 * math.cos(TAU * 2 * t))])
        D["pelvis"] = Tt(off) @ Tr(HEADP["pelvis"], Rp)
        D["spine"] = D["pelvis"] @ Tr(HEADP["spine"], Ry(IDLE_SWAY_DEG * math.sin(TAU * t)) @ Rx(IDLE_BREATH_DEG * math.sin(TAU * 2 * t)))
        D["chest"] = D["spine"] @ Tr(HEADP["chest"], Ry(0.6 * IDLE_SWAY_DEG * math.sin(TAU * t - 0.5)) @ Rx(-0.5 * IDLE_BREATH_DEG * math.sin(TAU * 2 * t)))
        D["head"] = D["chest"] @ Tr(HEADP["head"], Rz(IDLE_HEAD_YAW_DEG * math.sin(TAU * t + 0.8)) @ Rx(IDLE_HEAD_NOD_DEG * math.sin(TAU * 2 * t + 0.7)))
        for side in ("L", "R"):
            D["thigh." + side], D["shin." + side], err = ik_leg(side, D["pelvis"][:3, :3] @ HEADP["thigh." + side] + D["pelvis"][:3, 3], TIP[side][1], PLANT_Z)
            D["foot." + side] = D["shin." + side] @ Tr(HEADP["foot." + side], np.eye(3))
            info["ik_err_" + side] = err
        for side, ph in (("L", 0.0), ("R", 2.4)):
            names, _ = CH_BONES["arm." + side]
            sg = 1.0 if side == "L" else -1.0
            rots = [Rx(K.vine_wave(t, k, ARM_BONES, IDLE_ARM_DEG[0], IDLE_ARM_DEG[1], IDLE_ARM_LAG, 1, ph)) @
                    Ry(sg * 0.6 * K.vine_wave(t, k, ARM_BONES, IDLE_ARM_DEG[0], IDLE_ARM_DEG[1], IDLE_ARM_LAG, 2, ph + 1.1))
                    for k in range(ARM_BONES)]
            chain_rel(D, names, rots)
        for c_i, cn_ in enumerate(CAPE_NAMES):
            names, _ = CH_BONES[cn_]
            ph = 1.1 * c_i
            bias = [IDLE_CAPE_BIAS * (IDLE_CAPE_DEG[0] + (IDLE_CAPE_DEG[1] - IDLE_CAPE_DEG[0]) * k / (CAPE_BONES - 1)) for k in range(CAPE_BONES)]
            rots = [Rx(bias[k] + K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], IDLE_CAPE_LAG, 1, ph)) @
                    Ry(0.5 * K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], IDLE_CAPE_LAG, 1, ph + 2.0))
                    for k in range(CAPE_BONES)]
            chain_rel(D, names, rots)
        for w_i, wn in enumerate(("web.R", "web.C", "web.L")):
            names, _ = CH_BONES[wn]
            rots = [rotm(WEB[wn]["across"], K.vine_wave(t, k, WEB_BONES, IDLE_WEB_DEG[0], IDLE_WEB_DEG[1], IDLE_WEB_LAG, 3, 0.9 * w_i))
                    for k in range(WEB_BONES)]
            chain_rel(D, names, rots)
        return D, info
    # ---------------------------------------------------------------- walk
    c1 = math.cos(TAU * t)
    yaw = -WALK_PELVIS_YAW_DEG * c1                       # left hip forward while the left foot is forward (t = 0)
    roll = -WALK_PELVIS_ROLL_DEG * math.sin(TAU * t)      # the swing side dips a little
    Rp = Rz(yaw) @ Ry(roll)
    tgt, contacts = {}, []
    for side, t0 in (("L", 0.0), ("R", 0.5)):
        u = (t - t0) % 1.0
        if u <= 0.5:                                      # stance: the tip slides front -> back (the ground passing under)
            p = u / 0.5
            tgt[side] = (TIP[side][1] - STEP_A + 2 * STEP_A * p, PLANT_Z, 0.0)
            contacts.append((side, tgt[side][0]))
        else:                                             # swing: back -> front, lifted, the tentacle tip curling
            p = (u - 0.5) / 0.5
            tgt[side] = (TIP[side][1] + STEP_A * math.cos(math.pi * p), PLANT_Z + LIFT * math.sin(math.pi * p),
                         WALK_FOOT_CURL_DEG * math.sin(math.pi * p))
            if p >= 1.0 - 1e-9:
                contacts.append((side, tgt[side][0]))
    dz = pelvis_drop(Rp, contacts, WALK_REACH_K)
    D["pelvis"] = Tt(np.array([0.0, 0.0, dz])) @ Tr(HEADP["pelvis"], Rp)
    D["spine"] = D["pelvis"] @ Tr(HEADP["spine"], Rx(0.5 * WALK_LEAN_DEG) @ Ry(-0.5 * roll))
    D["chest"] = D["spine"] @ Tr(HEADP["chest"], Rz(-yaw + WALK_CHEST_COUNTER_DEG * c1) @ Rx(0.5 * WALK_LEAN_DEG) @ Ry(-0.3 * roll))
    D["head"] = D["chest"] @ Tr(HEADP["head"], Rz(-0.6 * WALK_CHEST_COUNTER_DEG * c1) @ Rx(-WALK_LEAN_DEG + 1.0 * math.sin(TAU * 2 * t)))
    for side in ("L", "R"):
        hip_p = D["pelvis"][:3, :3] @ HEADP["thigh." + side] + D["pelvis"][:3, 3]
        ty, tz, curl = tgt[side]
        D["thigh." + side], D["shin." + side], err = ik_leg(side, hip_p, ty, tz)
        D["foot." + side] = D["shin." + side] @ Tr(HEADP["foot." + side], Rx(curl))
        info["ik_err_" + side] = err
    for side in ("L", "R"):
        names, _ = CH_BONES["arm." + side]
        sg = 1.0 if side == "L" else -1.0                 # left arm back (+Rx) while the left foot is forward
        rots = [Rx(sg * (WALK_ARM_SWING_DEG * c1 if k == 0 else WALK_ARM_FOLLOW_DEG * math.cos(TAU * t - k * WALK_ARM_LAG)))
                @ Ry(sg * 1.5 * math.sin(TAU * t - k * WALK_ARM_LAG)) for k in range(ARM_BONES)]
        chain_rel(D, names, rots)
    for c_i, cn_ in enumerate(CAPE_NAMES):
        names, _ = CH_BONES[cn_]
        ph = 0.9 * c_i
        rots = [Rx(WALK_CAPE_TRAIL_DEG + K.vine_wave(t, k, CAPE_BONES, WALK_CAPE_DEG[0], WALK_CAPE_DEG[1], WALK_CAPE_LAG, 2, ph)) @
                Ry(0.4 * K.vine_wave(t, k, CAPE_BONES, WALK_CAPE_DEG[0], WALK_CAPE_DEG[1], WALK_CAPE_LAG, 1, ph + 1.3))
                for k in range(CAPE_BONES)]
        chain_rel(D, names, rots)
    for w_i, wn in enumerate(("web.R", "web.C", "web.L")):
        names, _ = CH_BONES[wn]
        rots = [rotm(WEB[wn]["across"], WALK_WEB_BILLOW_DEG + K.vine_wave(t, k, WEB_BONES, WALK_WEB_DEG[0], WALK_WEB_DEG[1], WALK_WEB_LAG, 2, 0.9 * w_i))
                for k in range(WEB_BONES)]
        chain_rel(D, names, rots)
    info.update({"pelvis_dz": dz, "targets": tgt})
    return D, info


ORDER = ["root"] + DEFORM
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"


def basis(D, n):
    return np.linalg.inv(REST4[n]) @ np.linalg.inv(D[PARENT[n]]) @ D[n] @ REST4[n]


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
NEW_ACTS, clip_rep, key_rows, REL = {}, {}, [], {}
ik_worst = {}
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    REL[cn] = {n: [] for n in DEFORM}
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1 (integer cycles -> exact seam)
        D, info = pose(cn, f % N)
        for side in ("L", "R"):
            ik_worst[(cn, side)] = max(ik_worst.get((cn, side), 0.0), abs(info["ik_err_" + side]))
        for n in DEFORM:
            Bm = basis(D, n)
            q = Matrix(Bm[:3, :3].tolist()).to_quaternion(); q.normalize()
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = rig.pose.bones[n]
            pb.location = Vector(Bm[:3, 3]); pb.rotation_quaternion = q
            pb.keyframe_insert("location", frame=f + 1)
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion))
            if f < N:
                Rrel = (np.linalg.inv(D[PARENT[n]]) @ D[n])[:3, :3]
                REL[cn][n].append(Rrel)
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["contact_cycles"] = 1                             # the contact sheet samples one full loop (walk: one stride)
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


def rot_angle(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return math.degrees(math.acos(float(np.clip(c, -1.0, 1.0))))


GROUPS_M = {"arm": [n for n in DEFORM if n.startswith("arm.")],
            "leg": [n for n in DEFORM if n.split(".")[0] in ("thigh", "shin", "foot")],
            "cape": [n for n in DEFORM if n.startswith("cape_")],
            "web": [n for n in DEFORM if n.startswith("web.")]}
tip_vid = {side: int(np.nonzero((np.abs(W_ - TIP[side]).sum(1) < 1e-9))[0][0]) for side in ("L", "R")}
arm_vids = np.nonzero(np.isin(labF, [2, 3]))[0]
web_vids = np.arange(NVS, nV)
samples = []
pose_check = 0.0
for cn, N in CLIPS.items():
    act = NEW_ACTS[cn]
    K.assign_action(rig, act)
    first = last = None
    minz, root_off = 1e9, 0.0
    tips, arm_web_gap = [], 1e9
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
            D1, _ = pose(cn, 0)                      # the numpy FK must equal Blender's evaluated pose
            for n in DEFORM:
                pose_check = max(pose_check, float(np.abs(np.array(rig.pose.bones[n].matrix) - D1[n] @ REST4[n]).max()))
        if f == N + 1:
            last = C
        minz = min(minz, float(C[:, 2].min()))
        root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        tips.append([C[tip_vid["L"]].copy(), C[tip_vid["R"]].copy()])
        if f % 2 == 1:
            kd_w = KDTree(len(web_vids))
            for i, v in enumerate(web_vids):
                kd_w.insert(C[v], i)
            kd_w.balance()
            arm_web_gap = min(arm_web_gap, min(kd_w.find(C[v])[2] for v in arm_vids[::3]))
    tips = np.array(tips)
    seam = float(np.linalg.norm(first - last, axis=1).max())
    # motion amplitude per bone: half the largest rotation between any two frames of the loop (constant offsets excluded)
    amp = {}
    for n in DEFORM:
        R_ = REL[cn][n]
        amp[n] = 0.5 * max(rot_angle(R_[i], R_[j]) for i in range(0, len(R_), 2) for j in range(i + 1, len(R_), 2))
    grp = {g: round(float(np.mean([amp[n] for n in ns])), 3) for g, ns in GROUPS_M.items()}
    row = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 4), "cyclic": True,
           "seam_units": round(seam, 8), "clearance_min_z": round(minz, 4), "root_offset_max": round(root_off, 8),
           "ik_unreachable_max": {s: round(ik_worst[(cn, s)], 6) for s in ("L", "R")},
           "mean_bone_amplitude_deg": grp,
           "cape_vs_arm_ratio": round(grp["cape"] / grp["arm"], 3), "cape_vs_leg_ratio": round(grp["cape"] / max(grp["leg"], 1e-9), 3),
           "per_bone_amplitude_deg": {n: round(v, 3) for n, v in amp.items()},
           "arm_to_web_min_gap": round(float(arm_web_gap), 4)}
    if cn == "walk":
        half = N // 2
        gait = {}
        for si, side in enumerate(("L", "R")):
            st0 = 0 if side == "L" else half
            stance = [(st0 + k) % N for k in range(half + 1)]
            swing = [(st0 + half + k) % N for k in range(1, half)]
            y = tips[:, si, 1]; z = tips[:, si, 2]
            gait[side] = {"tip_y_touchdown": round(float(y[stance[0]]), 4), "tip_y_liftoff": round(float(y[stance[-1]]), 4),
                          "step_length": round(float(y[stance[-1]] - y[stance[0]]), 4),
                          "stance_tip_z_range": [round(float(z[stance].min()), 5), round(float(z[stance].max()), 5)],
                          "stance_tip_x_slip": round(float(np.ptp(tips[stance, si, 0])), 5),
                          "swing_tip_z_max": round(float(z[swing].max()), 4)}
        step = float(np.mean([gait[s]["step_length"] for s in gait]))
        speed = 2.0 * step / (N / K.FPS)
        row.update({"gait": gait, "step_length_units": round(step, 4), "stride_units": round(2 * step, 4),
                    "cadence_steps_per_min": round(2 * 60.0 * K.FPS / N, 2),
                    "implied_ground_speed": {"units_per_s": round(speed, 4), "body_heights_per_s": round(speed / H, 4),
                                             "leg_reaches_per_s": round(speed / LEG_R0, 4),
                                             "m_per_s_at_cell_fit_report_only": round(speed * GAME_M_PER_UNIT, 4),
                                             "rule": "stride (2 x mean stance-tip travel) / cycle time"},
                    "step_half_travel": round(STEP_A, 4), "foot_lift": round(LIFT, 4), "leg_reach_rest": round(LEG_R0, 4)})
        assert step > 0, "walk runs backwards (stance tip must slide toward +Y, the character faces -Y)"
    else:
        row["stance_tip_z_range"] = {s: [round(float(tips[:, i, 2].min()), 5), round(float(tips[:, i, 2].max()), 5)] for i, s in enumerate(("L", "R"))}
        row["stance_tip_xy_slip"] = {s: round(float(np.linalg.norm(tips[:, i, :2] - tips[0, i, :2], axis=1).max()), 5) for i, s in enumerate(("L", "R"))}
    clip_rep[cn] = row
    print("CLIP", cn, json.dumps({k: v for k, v in row.items() if k != "per_bone_amplitude_deg"}))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["fk_vs_blender_pose_max_abs"] = pose_check
rep["motion_hierarchy"] = {"rule": "mean over each chain group's bones of half the largest rotation between two frames of the "
                                   "loop (relative to the parent; constant drape offsets excluded)",
                           "idle": clip_rep["idle"]["mean_bone_amplitude_deg"], "walk": clip_rep["walk"]["mean_bone_amplitude_deg"],
                           "cape_vs_arm": {"idle": clip_rep["idle"]["cape_vs_arm_ratio"], "walk": clip_rep["walk"]["cape_vs_arm_ratio"]},
                           "cape_vs_leg_walk": clip_rep["walk"]["cape_vs_leg_ratio"]}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rep["landmarks"] = {"hip_z": round(Z_HIP, 4), "neck_z": round(Z_NECK, 4), "head_top_z": round(Z_TOP, 4),
                    "leg_tips": {s: TIP[s].round(4).tolist() for s in TIP}, "emblem_centre": EMB_C.round(4).tolist()}
rig["conquest_rig"] = ("supaoctto v1: root (contract) > pelvis > spine > chest > head; thigh/shin/foot per leg (analytic IK); "
                       "arm.L/R.0-3; cape_outer/inner.L/R.0-4; web.R/C/L.0-2 (water-web mid chains)")
low["conquest_clips"] = list(CLIPS)
low["conquest_clip_status"] = "idle + walk (in place, upright biped); no attack/hit/death (artist: idle + locomotion only)"
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for a in list(bpy.data.actions):
    if a.name not in CLIPS:
        bpy.data.actions.remove(a)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris_final": report["tris_final"], "seconds": round(time.time() - T0, 1)}, open(DIGEST_ONLY, "w"), indent=1)
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 10. identity-scale glb
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
                           "%.5f reaches the regular cell (report-only)" % k_fit}
rig.animation_data.action = None
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "motion_hierarchy", "digest", "seconds")}))
sys.stdout.flush()
os._exit(0)
