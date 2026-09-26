"""Duskmaw (hero, Conquest character_id 'monster') re-run through the shared pipeline, one headless run.

    blender --background source-copies/hero-monster.blend --factory-startup --python improve/duskmaw_build.py -- \
        [--preview <out.blend>]          (geometry + UV + regions + palette only: no bake, no rig -- fast look loop)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Reads (never writes): source-copies/hero-monster.blend (the 553k sculpt, opened), source-copies/hero-monster_rigged.blend
(the SHIPPED rig, its 5 approved clips and 4 approved materials; appended in memory for the retarget + palette fidelity
check, removed before any save).
Outputs:
    improved/duskmaw.blend + .json          hero-tier retopo, UVs, baked normal/AO, region palette (no rig)
    improved/textures/duskmaw_{normal,ao}.png
    rigged/duskmaw.blend + .json            + skirted-totem rig (13 totem bones + contract root), the 5 carried clips
    rigged/duskmaw.glb                      IDENTITY-SCALE export (no scaled parent node; natural scale) -- the portrait fix

Artist identity (design/review-log.md, verbatim): "duskmaw is a shadowy monster ... its thematic is meant to be its chest
and bottom leg spikes form a mouth when looking at it". Scale policy (2026-09-25): natural proportions; cell fit is
report-only until ship. The shipped palette RGBs and the 5 clips (idle, walk, attack, hit, death) are APPROVED and kept.

Pipeline:
  1. sculpt main shell (46 sculpt crumbs of 8-16 verts dropped) to the origin: XY bbox centre, floor at z = 0. Front = -Y
     (the sculpted face under the hat; the shipped glb imports front -Y too -- roster yaw 180 is what showed its back).
  2. LOW: voxel remesh (single manifold shell) + collapse decimation (deterministic), tiny remesh islands dropped.
  3. region FIELDS on the low (per vertex): height, radius from the waist axis, inward-facing (normal . -r_hat),
     downward-facing, and spike TIPNESS (protrusion off a Laplacian-smoothed body -> geodesic distance from the body core
     -> fraction of each spike's own length, steepest-ascent to its tip).
  4. ISO-CONTOUR CUTS: every region boundary is cut into the mesh along the field's iso-line (edge splits at the
     interpolated crossing + face connects), so colour edges are smooth lines, not the per-face sawtooth of the shipped
     4-material paint. Regions are then read per face (no face straddles a boundary).
  5. regions -> palettes.store_regions; paint from palettes/duskmaw/default.json (shipped RGBs).
  6. Smart UV + pack; bake normal + AO from the 553k sculpt.
  7. RIG (skirted-totem archetype, implemented locally -- rigkit has no such archetype; candidate for porting): the prior
     art's bone fractions on the new bounds (13 totem bones; the shipped deforming 'root' becomes 'base') + a contract
     'root' (non-deforming, at the origin, never keyed). Analytic weights: axis z-bands, arm/blade segments, 4 skirt
     sectors (<= 4 influences). Clips: the shipped fcurves copied key-for-key ('root' -> 'base'; location keys scaled by
     H_new / H_old) -- motion carried, then measured against the shipped rig frame by frame.
  8. identity-scale glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, heapq
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
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun). Lengths are sculpt units
# (the sculpt is ~30.9 units tall; natural scale, no refit).
UNIT = "duskmaw"
CHAR_ID = "monster"
VOXEL = 0.07                          # "retopo resolution" (voxel remesh size before decimation)
LOW_TRIS = 27000                      # "mesh detail" (decimation target before the colour-boundary cuts)
TRI_BUDGET = [25000, 35000]           # declared hero-tier window (contract tri_budget)
ROOTS_FRAC = 0.30                     # "skirt colour line" -- roots below this fraction of the height (shipped rule)
CROWN_FRAC = 0.90                     # "burning crown apex" above this fraction of the height (shipped rule)
SEED_Z_FRAC = 0.45                    # tip field: geodesic distances are measured from a ring round the torso at this height
TIP_LEN = 0.65                        # "ember tip length" -- outer fraction of each spike that burns ...
TIP_ABS = (1.0, 6.5)                  # ... clamped to this burn length (sculpt units)
MIN_SPIKE = 1.0                       # spikes shorter than this (sculpt units, geodesic persistence) do not burn
SPIKE_RMIN = 0.15                     # tips nearer the vertical axis than this x H are not spikes (skirt underside centre; the crown burns by height)
JAW_Z = (3.8, 13.0)                   # "teeth": the mouth region searched for the chest points (upper jaw) and skirt peaks (lower jaw) ...
JAW_RMAX = 7.7                        # ... out to this radius from the waist axis
MIN_TOOTH = 0.5                       # a tooth must rise this far above its notch (height persistence, sculpt units)
TOOTH_LEN = 0.4                       # "ember fang tips": outer fraction of each tooth that burns ...
TOOTH_ABS = (0.35, 1.1)               # ... clamped to this burn length
TOOTH_RMIN = 3.0                      # teeth whose tip sits closer than this to the waist axis are the throat, not teeth
ROOTS_R = 8.5                         # the lower hands (outside this radius, above ROOTS_ZLOW) keep the body colour
ROOTS_ZLOW = 3.8                      # ... the skirt fins below this height stay roots
HAND_Y = 3.2                          # the hands hang in the side plane: within this of the waist axis in Y (skirt fins lie outside)
MAW_Z = (5.2, 10.4)                   # "mouth band": skirt valleys .. just above the chest's lower edge
THROAT_R = 2.4                        # "throat" radius around the waist axis inside the mouth band
MAW_ROUT = 6.2                        # "maw walls reach": the red stops this far from the waist axis (the lower arms stay dark)
MAW_IN = 0.35                         # "inner maw walls": skirt/chest faces turned toward the throat more than this
MAW_DOWN = 0.45                       # "chest underside": faces pointing down more than this inside the mouth band
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
BAKE_CAGE = 0.15                      # bake cage extrusion (sculpt units)
BAKE_RES = (2048, 1024)               # normal, AO texture sizes
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest hero ceilings -- REPORT ONLY (scale policy 2026-09-25)
# skirted-totem archetype (prior art tools/blender/autorig_skirted_totem.py, fractions of height H / width W)
Z_FR = {"root": (0.06, 0.30), "spine": (0.30, 0.52), "chest": (0.52, 0.68), "head": (0.68, 0.82), "crown": (0.82, 0.99)}
ARM_FR = {"shoulder_x": 0.10, "elbow_x": 0.55, "tip_x": 0.98, "z_sh": 0.60, "z_el": 0.63, "z_tip": 0.58}  # x in W/2 except shoulder (W)
SKIRT_FR = {"head_r": 0.08, "tail_r": 0.38, "head_z": 0.16, "tail_z": 0.05}
# weights
W_BAND = 1.0                          # "joint softness": z-band blend half-width between axis bones
ARM_IN, ARM_OUT = 0.16, 0.26          # arm starts leaving the torso at |x| = ARM_IN*W .. fully arm at ARM_OUT*W
ARM_ZMAX = 0.66                       # arms never claim above the head joint (the hat stays on the head)
ARM_ZMIN = 0.49                       # ... nor below this (the lower hands hang from the spine, as shipped)
SKIRT_R = (1.4, 4.4)                  # skirt sector weights ramp in over this radius from the waist axis
SKIRT_ZTOP = (8.2, 9.0)               # ... and fade out over this height (the torso above belongs to the axis)
HAND_R = (7.4, 8.6)                   # the lower arms + spiked hands ramp in over this radius (outside the skirt, above ROOTS_ZLOW) ...
HAND_ZTOP = (12.0, 13.5)              # ... below this height, and ride
HAND_MIX = {"spine": 0.6, "arm": 0.1, "blade": 0.3}   # the SHIPPED heat weights' own-side mix there (spine .56 / arm .09 / blade .29)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OLD_BLEND = os.path.join(ROOT, "source-copies", "hero-monster_rigged.blend")
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": bpy.data.filepath, "tier": "hero",
          "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0, "overrides": OVERRIDES}
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


def keep_main_island(V, F, min_frac):
    """Drop connected pieces smaller than min_frac of the vertices (sculpt crumbs / remesh specks)."""
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
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum()),
                           "largest_dropped": int(cnt[~keep_lab].max()) if (~keep_lab).any() else 0}


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
HIGH = new_obj(UNIT + "_high", SV, SF)
size = SV.max(0) - SV.min(0)
H = float(size[2])
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["natural"] = {"height": round(H, 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "footprint": round(fp, 4), "units": "sculpt units (natural proportions, no refit)",
                     "origin_shift_sculpt_units": SHIFT.round(5).tolist(),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint",
                                                     "ceilings": [CELL_MAX_H, CELL_MAX_FP]},
                     "shipped_glb_height_m": 1.8}

# =========================================================================== 2. low: voxel remesh + collapse decimation
t = time.time()
tmp = new_obj("remesh_src", SV, SF)
rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = VOXEL; rm.use_smooth_shade = False
RV, RF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
tmp = new_obj("dec_src", RV, RF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, LOW_TRIS / tri_count(tmp.data))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_main_island(LV, LF, 0.01)
# re-centre on the LOW's own bounds (contract feet_origin: the delivered mesh's bbox centre + floor at the origin)
lo2, hi2 = LV.min(0), LV.max(0)
S2 = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
LV = LV - S2
SV = SV - S2
SHIFT = SHIFT + S2
HIGH.data.vertices.foreach_set("co", SV.ravel()); HIGH.data.update()
report["natural"]["origin_shift_sculpt_units"] = SHIFT.round(5).tolist()
report["retopo"] = {"method": "voxel remesh (%.3f) -> collapse decimation (deterministic) -> main shell" % VOXEL,
                    "remesh_tris": int(sum(len(f) - 2 for f in RF)), "decimated_tris": int(sum(len(f) - 2 for f in LF)),
                    "specks_dropped": specks, "seconds": round(time.time() - t, 1)}
# retopo fidelity: low vertices -> high surface
bvh_high = BVHTree.FromPolygons(SV.tolist(), SF)
dev = np.array([bvh_high.find_nearest(Vector(p))[3] for p in LV])
report["retopo"]["low_to_high_distance"] = {"mean": round(float(dev.mean()), 4), "p99": round(float(np.percentile(dev, 99)), 4),
                                            "max": round(float(dev.max()), 4), "pct_of_height_p99": round(100 * float(np.percentile(dev, 99)) / H, 3)}

# =========================================================================== 3. region fields (per low vertex)
low0 = new_obj("fields_tmp", LV, LF)
M0 = K.MeshData(low0)
NV = np.empty(len(LV) * 3); low0.data.vertex_normals.foreach_get("vector", NV); NV = NV.reshape(-1, 3)
bpy.data.objects.remove(low0, do_unlink=True)
# waist axis: centre of the column between the skirt and the chest
col_m = (LV[:, 2] > MAW_Z[0] + 1.0) & (LV[:, 2] < MAW_Z[1] - 1.5) & (np.hypot(LV[:, 0], LV[:, 1]) < 2.0)
AXIS = LV[col_m, :2].mean(0) if col_m.any() else np.zeros(2)
dxy = LV[:, :2] - AXIS
r = np.hypot(dxy[:, 0], dxy[:, 1])
rhat = dxy / np.maximum(r, 1e-9)[:, None]
f_in = -(NV[:, 0] * rhat[:, 0] + NV[:, 1] * rhat[:, 1])
f_down = -NV[:, 2]
# spike tips: geodesic distance g from a ring round the torso middle; every spike tip is a maximum of g. A persistence
# sweep (vertices by descending g, union-find of basins) gives each maximum its spike length = g(tip) - g(saddle where
# its basin merges into a taller one); basins shorter than MIN_SPIKE are folded into the basin they merge into (sculpt
# noise). Field tipf = g - (g_tip - burn length) per vertex, burn length = TIP_LEN x spike length clamped to TIP_ABS.
t = time.time()
elen = np.linalg.norm(LV[M0.ev[:, 0]] - LV[M0.ev[:, 1]], axis=1)
adj = [[] for _ in range(M0.n)]
for (a, b), l_ in zip(M0.ev, elen):
    adj[a].append((b, l_)); adj[b].append((a, l_))
ring = (np.abs(LV[:, 2] - SEED_Z_FRAC * H) < 0.35) & (r < 0.25 * H)
g = np.full(M0.n, np.inf)
pq = [(0.0, int(v)) for v in np.nonzero(ring)[0]]
for _, v in pq:
    g[v] = 0.0
heapq.heapify(pq)
while pq:
    d_, v = heapq.heappop(pq)
    if d_ > g[v]:
        continue
    for u, l_ in adj[v]:
        nd = d_ + l_
        if nd < g[u]:
            g[u] = nd; heapq.heappush(pq, (nd, u))
g[~np.isfinite(g)] = 0.0
def persistence(fv, mask, min_pers):
    """0-dim persistence of the maxima of fv over the mesh graph restricted to mask. Returns per vertex the owning
    LIVE tip (-1 outside mask) and that tip's persistence; basins below min_pers fold into the basin they merge into."""
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


spk, spk_plen, spk_pers = persistence(g, np.ones(M0.n, bool), MIN_SPIKE)
tip_r = r[np.maximum(spk, 0)]
tipf, spk_len, tips = burn_field(g, spk, spk_plen, TIP_LEN, TIP_ABS, MIN_SPIKE, valid=tip_r > SPIKE_RMIN * H)
# the mouth's teeth (lore: "chest and bottom leg spikes form a mouth"): height persistence inside each jaw
# jaws = the connected pieces of the mouth region once the throat pillar is removed: the piece reaching highest is the
# chest (upper jaw), the piece reaching lowest is the skirt (lower jaw)
jaw_m = (LV[:, 2] > JAW_Z[0]) & (LV[:, 2] < JAW_Z[1]) & (r > THROAT_R) & (r < JAW_RMAX)
lab = -np.ones(M0.n, dtype=np.int64)
ncomp = 0
for s0 in np.nonzero(jaw_m)[0]:
    if lab[s0] >= 0:
        continue
    stack = [s0]; lab[s0] = ncomp
    while stack:
        v = stack.pop()
        for u, _ in adj[v]:
            if jaw_m[u] and lab[u] < 0:
                lab[u] = ncomp; stack.append(u)
    ncomp += 1
comp_top = int(lab[np.nonzero(jaw_m)[0][np.argmax(LV[jaw_m, 2])]])
comp_bot = int(lab[np.nonzero(jaw_m)[0][np.argmin(LV[jaw_m, 2])]])
up_jaw = lab == comp_top
low_jaw = lab == comp_bot
report["jaws"] = {"pieces": ncomp, "upper_jaw_verts": int(up_jaw.sum()), "lower_jaw_verts": int(low_jaw.sum()),
                  "separate": comp_top != comp_bot,
                  "upper_z": [round(float(LV[up_jaw, 2].min()), 3), round(float(LV[up_jaw, 2].max()), 3)],
                  "lower_z": [round(float(LV[low_jaw, 2].min()), 3), round(float(LV[low_jaw, 2].max()), 3)]}
assert comp_top != comp_bot, "chest and skirt are bridged inside the mouth region: %r" % report["jaws"]
lo_tip, lo_plen, lo_pers = persistence(LV[:, 2], low_jaw, MIN_TOOTH)
up_tip, up_plen, up_pers = persistence(-LV[:, 2], up_jaw, MIN_TOOTH)
tlo, tlo_len, teeth_lo = burn_field(LV[:, 2], lo_tip, lo_plen, TOOTH_LEN, TOOTH_ABS, MIN_TOOTH, valid=r[np.maximum(lo_tip, 0)] > TOOTH_RMIN)
tup, tup_len, teeth_up = burn_field(-LV[:, 2], up_tip, up_plen, TOOTH_LEN, TOOTH_ABS, MIN_TOOTH, valid=r[np.maximum(up_tip, 0)] > TOOTH_RMIN)
report["tip_field"] = {"seed": "ring at SEED_Z_FRAC x H round the torso middle", "spikes_found": len(tips),
                       "spikes_xyz_len": tip_rows(tips, spk_pers),
                       "lower_teeth_xyz_len": tip_rows(teeth_lo, lo_pers), "upper_teeth_xyz_len": tip_rows(teeth_up, up_pers),
                       "seconds": round(time.time() - t, 1)}

# =========================================================================== 4. iso-contour cuts
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
FIELDS = {"z": LV[:, 2], "r": r, "fin": f_in, "fdown": f_down, "tip": tipf, "gtip": spk_len,
          "tlo": tlo, "glo": tlo_len, "tup": tup, "gup": tup_len, "ady": np.abs(LV[:, 1] - AXIS[1])}
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
    # pass 1: snap crossings that land near a vertex onto that vertex (no slivers)
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < CUT_SNAP:
                a[L] = tau
            elif tt > 1 - CUT_SNAP:
                b[L] = tau
    # pass 2: split the remaining crossings at the interpolated point
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
    # pass 3: connect the contour vertices across every straddling face
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


z_in = lambda a, b: MAW_Z[0] - 1e-4 <= a[LAY["z"]] <= MAW_Z[1] + 1e-4 and MAW_Z[0] - 1e-4 <= b[LAY["z"]] <= MAW_Z[1] + 1e-4
spike = lambda a, b: a[LAY["gtip"]] > MIN_SPIKE and b[LAY["gtip"]] > MIN_SPIKE
tooth_lo = lambda a, b: a[LAY["glo"]] >= MIN_TOOTH and b[LAY["glo"]] >= MIN_TOOTH
tooth_up = lambda a, b: a[LAY["gup"]] >= MIN_TOOTH and b[LAY["gup"]] >= MIN_TOOTH
in_z = lambda v: ROOTS_ZLOW - 1e-4 <= v[LAY["z"]] <= ROOTS_FRAC * H + 1e-4
in_r = lambda v: v[LAY["r"]] >= ROOTS_R - 1e-4
in_y = lambda v: v[LAY["ady"]] <= HAND_Y + 1e-4
roots_side = lambda a, b: all(in_z(v) and in_y(v) for v in (a, b))
roots_out = lambda a, b: all(in_r(v) and in_y(v) for v in (a, b))
hand_plane = lambda a, b: all(in_r(v) and in_z(v) for v in (a, b))
CUTS = [("z", ROOTS_FRAC * H, None), ("z", CROWN_FRAC * H, None), ("z", MAW_Z[0], None), ("z", MAW_Z[1], None),
        ("r", THROAT_R, z_in), ("r", MAW_ROUT, z_in), ("fin", MAW_IN, z_in), ("fdown", MAW_DOWN, z_in), ("tip", 0.0, spike),
        ("r", ROOTS_R, roots_side), ("z", ROOTS_ZLOW, roots_out), ("ady", HAND_Y, hand_plane), ("tlo", 0.0, tooth_lo), ("tup", 0.0, tooth_up)]
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
bm.faces.index_update()
nf = len(bm.faces)
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
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
REG = ["body", "roots", "flame", "maw_throat", "maw_inner"]
R_ = {n: i for i, n in enumerate(REG)}
zc, rc, fin_c, fdn_c, tip_c, gt_c = (FVAL[k] for k in ("z", "r", "fin", "fdown", "tip", "gtip"))
rid = np.full(nf, R_["body"], dtype=np.int32)
hand_c = (rc > ROOTS_R) & (zc > ROOTS_ZLOW) & (FVAL["ady"] < HAND_Y)
rid[(zc < ROOTS_FRAC * H) & ~hand_c] = R_["roots"]
band = (zc > MAW_Z[0]) & (zc < MAW_Z[1])
rid[band & (rc < MAW_ROUT) & ((fin_c > MAW_IN) | (fdn_c > MAW_DOWN))] = R_["maw_inner"]
rid[band & (rc < THROAT_R)] = R_["maw_throat"]
teeth = ((FVAL["tlo"] > 0.0) & (FVAL["glo"] >= MIN_TOOTH)) | ((FVAL["tup"] > 0.0) & (FVAL["gup"] >= MIN_TOOTH))
flame = ((tip_c > 0.0) & (gt_c > MIN_SPIKE)) | (zc > CROWN_FRAC * H) | teeth
rid[flame] = R_["flame"]
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
# boundary straddle audit: a face whose vertex field values fall on both sides of a threshold would be a sawtooth step
report["region_rule"] = {
    "roots": "face height < ROOTS_FRAC x H (shipped rule, now an iso-line), except the lower hands (radius > ROOTS_R above ROOTS_ZLOW, within HAND_Y of the side plane)",
    "teeth": "ember tips on the mouth's teeth: height-persistence maxima of the skirt (lower jaw) and minima "
             "of the chest (upper jaw) -- the two pieces of JAW_Z x (THROAT_R, JAW_RMAX), outer TOOTH_LEN clamped to TOOTH_ABS",
    "flame": "outer TIP_LEN of every persistent spike (geodesic persistence >= MIN_SPIKE, burn clamped to TIP_ABS), or height > CROWN_FRAC x H (shipped intent: ember-tipped spikes + crown apex)",
    "maw_throat": "inside the mouth band MAW_Z, radius from the waist axis < THROAT_R",
    "maw_inner": "inside MAW_Z, faces turned toward the throat (> MAW_IN) or downward (chest underside, > MAW_DOWN)",
    "axis_xy": AXIS.round(4).tolist(), "mouth_band_z": list(MAW_Z)}

# cavity shade + deterministic per-face value jitter (improve_unit.py rule), cavity from the high sculpt
t = time.time()
kd_h = KDTree(len(SV))
for i, p in enumerate(SV):
    kd_h.insert(p, i)
kd_h.balance()
hme = HIGH.data
ev_h = np.empty(len(hme.edges) * 2, dtype=np.int64); hme.edges.foreach_get("vertices", ev_h); ev_h = ev_h.reshape(-1, 2)
nv_h = np.empty(len(hme.vertices) * 3); hme.vertex_normals.foreach_get("vector", nv_h); nv_h = nv_h.reshape(-1, 3)
deg = np.bincount(ev_h.ravel(), minlength=len(SV)).astype(float)


def nmean(X):
    s_ = np.zeros_like(X)
    for k in range(X.shape[1]):
        s_[:, k] = np.bincount(ev_h[:, 0], X[ev_h[:, 1], k], minlength=len(SV)) + np.bincount(ev_h[:, 1], X[ev_h[:, 0], k], minlength=len(SV))
    return s_ / np.maximum(deg, 1)[:, None]


el = np.linalg.norm(SV[ev_h[:, 0]] - SV[ev_h[:, 1]], axis=1).mean()
cav = ((nmean(SV) - SV) * nv_h).sum(1) / el
for _ in range(6):
    cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
cav = np.clip(cav / (np.percentile(np.abs(cav), 95) + 1e-9), -1, 1)
fcav = np.array([np.mean([cav[j] for (_, j, _) in kd_h.find_n(p, 8)]) for p in FC])
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k
report["cavity_seconds"] = round(time.time() - t, 1)

# mouth-illusion front read: the maw's front-visible share (faces whose normal faces the -Y camera)
maw = np.isin(rid, [R_["maw_throat"], R_["maw_inner"]])
fa = np.empty(nf); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == R_[n]].sum() / fa.sum()), 4) for n in REG}
report["maw"] = {"faces": int(maw.sum()), "area": round(float(fa[maw].sum()), 3),
                 "front_facing_area": round(float(fa[maw & (FN[:, 1] < -0.2)].sum()), 3),
                 "z_range": [round(float(FC[maw, 2].min()), 3), round(float(FC[maw, 2].max()), 3)] if maw.any() else None,
                 "shipped_mouth_was": "79 faces at z 20.55-22.01 (hat-brim underside / face under the hat), y -4.94..-3.99 -- repainted body"}

# facing landmark (direction-free): the sculpted face under the hat -- concavity-weighted centroid of the face band
fb = (SV[:, 2] > 0.52 * H) & (SV[:, 2] < 0.72 * H) & (np.hypot(SV[:, 0], SV[:, 1]) < 0.16 * H)
anchor = SV[fb].mean(0)
w_ = np.clip(cav[fb], 0.0, None) ** 2 + 1e-12
landmark = anchor.copy()
landmark[:2] = anchor[:2] + ((SV[fb, :2] - anchor[:2]) * w_[:, None]).sum(0) / w_.sum()
dvec = landmark - anchor
report["facing"] = {"rule": "face band (52-72% H, r < 16% H) centroid -> its concavity-weighted (smoothed cavity+ ^2: the eye sockets and teeth recesses of the sculpted face) centroid",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2)}

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

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris_final", "retopo", "regions_faces", "regions_area_share", "maw", "facing", "iso_cuts", "jaws",
                                                             "region_rule")}))
    print("TIPS", json.dumps(report["tip_field"]))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake (normal + AO from the 553k sculpt)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
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
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(devn[cov_n & alltex_n].mean()), 4),
    "ao_baked_pct_of_uv_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
per_region = {}
for n_ in REG:
    fm_ = rid == R_[n_]
    if fm_.any():
        tx = texels_of(fm_, RN)
        per_region[n_] = {"texels": int(tx.sum()), "baked_pct": round(100 * float(cov_n[tx].mean()), 2),
                          "normal_dev_mean": round(float(devn[tx & cov_n].mean()), 4) if (tx & cov_n).any() else None}
bstats["per_region_normal"] = per_region
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


bstats["pixel_sha"] = {"normal": sha(px[:, :3]), "ao": sha(pa[:, :1])}
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["high_tris"] = report["tris_source"]
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
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()),
                "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
bpy.context.preferences.filepaths.save_version = 0
set_tex_paths("//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)

# palette fidelity vs the SHIPPED materials (appended read-only from the shipped rigged blend, removed before saving)
with bpy.data.libraries.load(OLD_BLEND, link=False) as (src_d, dst_d):
    dst_d.objects = ["Monster", "MonsterRig"]
OLD_MESH = bpy.data.objects["Monster"]; OLD_RIG = bpy.data.objects["MonsterRig"]
old_coll = bpy.data.collections.new("shipped_ref"); scene.collection.children.link(old_coll)
old_coll.objects.link(OLD_MESH); old_coll.objects.link(OLD_RIG)
MAP = {"body": "summoner_body", "roots": "summoner_roots", "flame": "summoner_flame", "maw_throat": "summoner_mouth",
       "maw_inner": "summoner_mouth"}
fid = {}
worst = 0.0
for reg, mname in MAP.items():
    om = next(s.material for s in OLD_MESH.material_slots if s.material and s.material.name.startswith(mname))
    ob_ = om.node_tree.nodes.get("Principled BSDF")
    ship_base = np.array(ob_.inputs["Base Color"].default_value[:3])
    ship_em = np.array(ob_.inputs["Emission Color"].default_value[:3]) * ob_.inputs["Emission Strength"].default_value
    pr = pal_default["regions"][reg]
    pb = PAL.srgb_to_linear(pr["rgb"])
    pe = PAL.srgb_to_linear(pr["emission"]) * pr.get("emission_scale", 1.0) * pal_default["material"].get("emission_strength", 1.0) \
        if "emission" in pr else np.zeros(3)
    d_b = float(np.abs(pb - ship_base).max()); d_e = float(np.abs(pe - ship_em).max())
    worst = max(worst, d_b, d_e)
    fid[reg] = {"shipped_material": om.name, "shipped_base_linear": ship_base.round(4).tolist(), "palette_base_linear": pb.round(4).tolist(),
                "base_max_abs_delta": round(d_b, 5), "shipped_emission_linear": ship_em.round(4).tolist(),
                "palette_emission_linear": pe.round(4).tolist(), "emission_max_abs_delta": round(d_e, 5),
                "shipped_roughness": round(ob_.inputs["Roughness"].default_value, 3)}
report["palette_fidelity"] = {"regions": fid, "worst_linear_delta": round(worst, 5),
                              "rule": "palette sRGB ints -> linear (palettes.srgb_to_linear) vs the shipped Principled values; "
                                      "deltas are 8-bit sRGB quantisation only",
                              "roughness_note": "palettes.py carries ONE material roughness (0.87); shipped per-material "
                                                "roughness was body 0.85 / roots 0.92 / flame 0.90 / mouth 0.60"}
old_coll.hide_render = True
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig (skirted-totem archetype, local)
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def skirted_totem_bones(lo_, hi_):
    """Prior-art bone layout (tools/blender/autorig_skirted_totem.py) as a pure function of the mesh bounds.
    Returns [(name, head, tail, parent, connect)], parents before children; the deforming trunk base is 'base'
    (the shipped rig called it 'root'; 'root' is now the contract's static, non-deforming origin bone)."""
    Hh = hi_[2] - lo_[2]; Ww = hi_[0] - lo_[0]
    cx, cy = (lo_[0] + hi_[0]) / 2, (lo_[1] + hi_[1]) / 2
    z = lambda f: lo_[2] + Hh * f
    out = []
    chain = [("base", "root"), ("spine", "base"), ("chest", "spine"), ("head", "chest"), ("crown", "head")]
    for nm, par in chain:
        a, b = Z_FR["root" if nm == "base" else nm]
        out.append((nm, (cx, cy, z(a)), (cx, cy, z(b)), par, nm != "base"))
    aw = Ww * 0.5
    for sgn, s in ((1, "L"), (-1, "R")):
        sh = (cx + sgn * Ww * ARM_FR["shoulder_x"], cy, z(ARM_FR["z_sh"]))
        el_ = (cx + sgn * aw * ARM_FR["elbow_x"], cy, z(ARM_FR["z_el"]))
        tp = (cx + sgn * aw * ARM_FR["tip_x"], cy, z(ARM_FR["z_tip"]))
        out.append(("arm." + s, sh, el_, "chest", False))
        out.append(("blade." + s, el_, tp, "arm." + s, True))
    for nm, (dx, dy) in (("skirt.F", (0, -1)), ("skirt.B", (0, 1)), ("skirt.L", (1, 0)), ("skirt.R", (-1, 0))):
        out.append((nm, (cx + dx * Ww * SKIRT_FR["head_r"], cy + dy * Ww * SKIRT_FR["head_r"], z(SKIRT_FR["head_z"])),
                    (cx + dx * Ww * SKIRT_FR["tail_r"], cy + dy * Ww * SKIRT_FR["tail_r"], z(SKIRT_FR["tail_z"])), "base", False))
    return out


Mw = K.MeshData(low)
W_ = Mw.W
lo_n, hi_n = W_.min(0), W_.max(0)
H_new = float(hi_n[2] - lo_n[2])
BONES = skirted_totem_bones(lo_n, hi_n)
# the shipped rig's bone layout, for the layout comparison
old_me = OLD_MESH.data
OW = np.array([OLD_MESH.matrix_world @ v.co for v in old_me.vertices])
lo_o, hi_o = OW.min(0), OW.max(0)
H_old = float(hi_o[2] - lo_o[2]); W_old = float(hi_o[0] - lo_o[0])
c_old = np.array([(lo_o[0] + hi_o[0]) / 2, (lo_o[1] + hi_o[1]) / 2, lo_o[2]])
c_new = np.array([(lo_n[0] + hi_n[0]) / 2, (lo_n[1] + hi_n[1]) / 2, lo_n[2]])
RATIO = H_new / H_old
OLDNAME = {"base": "root"}
lay_dev = {}
for (nm, h, t_, p, c) in BONES:
    ob_b = OLD_RIG.data.bones[OLDNAME.get(nm, nm)]
    for tag, pn, po in (("head", h, ob_b.head_local), ("tail", t_, ob_b.tail_local)):
        dn = (np.array(pn) - c_new) / H_new; do = (np.array(po) - c_old) / H_old
        lay_dev[nm + "." + tag] = float(np.linalg.norm(dn - do))
rep["archetype"] = {"name": "skirted_totem (local; rigkit has no such archetype -- candidate for porting)",
                    "source": "Conquest tools/blender/autorig_skirted_totem.py fractions (read-only prior art)",
                    "H_new": round(H_new, 4), "H_shipped_rig_mesh": round(H_old, 4), "height_ratio": round(RATIO, 5),
                    "W_new": round(float(hi_n[0] - lo_n[0]), 4), "W_shipped": round(W_old, 4),
                    "layout_vs_shipped_max_pct_H": round(100 * max(lay_dev.values()), 3),
                    "layout_vs_shipped_worst": max(lay_dev, key=lay_dev.get)}
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
# roll parity with the shipped bones (both built with roll 0 from head/tail): bone-local axes must match
roll_dev = 0.0
for (nm, h, t_, p, c) in BONES:
    mn_ = np.array(arm_data.bones[nm].matrix_local)[:3, :3]
    mo_ = np.array(OLD_RIG.data.bones[OLDNAME.get(nm, nm)].matrix_local)[:3, :3]
    roll_dev = max(roll_dev, float(np.degrees(np.arccos(np.clip((np.trace(mn_.T @ mo_) - 1) / 2, -1, 1)))))
rep["archetype"]["bone_frame_vs_shipped_max_deg"] = round(roll_dev, 4)
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
BH = {b[0]: (np.array(b[1]), np.array(b[2])) for b in BONES}

# ---- analytic weights (<= 4 influences)
x, y, z = W_[:, 0] - c_new[0], W_[:, 1] - c_new[1], W_[:, 2]
Wd = float(hi_n[0] - lo_n[0])
Wt = np.zeros((Mw.n, len(DEFORM)))
# axis chain by height (2-bone z blends at each joint)
joints = [BH["spine"][0][2], BH["chest"][0][2], BH["head"][0][2], BH["crown"][0][2]]
axis_names = ["base", "spine", "chest", "head", "crown"]
axis_w = np.zeros((Mw.n, 5))
prev = np.ones(Mw.n)
for k_, zj in enumerate(joints):
    s_ = smoothstep(zj - W_BAND, zj + W_BAND, z)
    axis_w[:, k_] = prev * (1 - s_)
    prev = prev * s_
axis_w[:, 4] = prev
# arms: leave the torso over |x| ARM_IN..ARM_OUT (x W), inside the arm height window; arm -> blade across the elbow
side = np.sign(x)
ax_ = np.abs(x)
armness = smoothstep(ARM_IN * Wd, ARM_OUT * Wd, ax_) * smoothstep(ARM_ZMIN * H_new - W_BAND, ARM_ZMIN * H_new + W_BAND, z) * \
    (1 - smoothstep(ARM_ZMAX * H_new - W_BAND, ARM_ZMAX * H_new + W_BAND, z))
elbow_x = abs(BH["arm.L"][1][0] - c_new[0])
bladeness = smoothstep(elbow_x - 1.2 * W_BAND, elbow_x + 1.2 * W_BAND, ax_)
# skirt: 4 sectors by angle, ramp in by radius from the waist axis, fade out above the skirt
dx_, dy_ = W_[:, 0] - AXIS[0], W_[:, 1] - AXIS[1]
rr = np.hypot(dx_, dy_)
skirtness = smoothstep(SKIRT_R[0], SKIRT_R[1], rr) * (1 - smoothstep(SKIRT_ZTOP[0], SKIRT_ZTOP[1], z))
ang = np.arctan2(dy_, dx_)
sec = {"skirt.L": 0.0, "skirt.B": math.pi / 2, "skirt.R": math.pi, "skirt.F": -math.pi / 2}
sw = np.stack([np.maximum(np.cos(ang - a0), 0.0) ** 2 for a0 in sec.values()], 1)
sw /= np.maximum(sw.sum(1), 1e-9)[:, None]
# lower arms + spiked hands (below the arm window, outside the skirt): the shipped heat weights' own-side mix
handness = smoothstep(HAND_R[0], HAND_R[1], rr) * smoothstep(ROOTS_ZLOW - 0.5, ROOTS_ZLOW + 0.5, z) * \
    (1 - smoothstep(HAND_ZTOP[0], HAND_ZTOP[1], z)) * (1 - armness) * (1 - smoothstep(HAND_Y - 0.8, HAND_Y + 0.8, np.abs(dy_)))
skirtness = skirtness * (1 - handness)
rest_w = 1 - armness - handness
for k_, nm in enumerate(axis_names):
    Wt[:, J[nm]] += axis_w[:, k_] * rest_w * (1 - skirtness)
for k_, nm in enumerate(sec):
    Wt[:, J[nm]] += sw[:, k_] * skirtness * rest_w
L_ = side > 0
Wt[:, J["spine"]] += handness * HAND_MIX["spine"]
for sgn_m, s in ((L_, "L"), (~L_, "R")):
    Wt[sgn_m, J["arm." + s]] += (armness * (1 - bladeness) + handness * HAND_MIX["arm"])[sgn_m]
    Wt[sgn_m, J["blade." + s]] += (armness * bladeness + handness * HAND_MIX["blade"])[sgn_m]
Wt = np.where(Wt > 1e-4, Wt, 0.0)
# keep the 4 largest
if (Wt > 0).sum(1).max() > 4:
    idx = np.argsort(-Wt, 1)[:, 4:]
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
                  "rule": ("axis base/spine/chest/head/crown by height (smoothstep +-W_BAND at each joint); arms claim "
                           "|x| > ARM_IN..ARM_OUT x W inside ARM_ZMIN..ARM_ZMAX x H, arm -> blade across the elbow; skirt "
                           "sectors F/B/L/R by cos^2 of the angle about the waist axis, ramped in over SKIRT_R and out over "
                           "SKIRT_ZTOP; lower arms + hands (HAND_R, above ROOTS_ZLOW, below HAND_ZTOP) take HAND_MIX = the shipped heat weights' own-side mix; the skirt PEAKS (the lore's 'bottom leg spikes') ride the skirt sectors -- they are the legs")}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig

# ---- clips: the shipped fcurves, key for key ('root' -> 'base', location keys x RATIO)
for pb in rig.pose.bones:
    pb.rotation_mode = "XYZ"
old_acts = {}
# the appended OLD_RIG object pulled its NLA actions in: gather the shipped actions by name
for a in sorted(bpy.data.actions, key=lambda a_: a_.name):
    base_nm = a.name.split(".")[0]
    if base_nm in ("idle", "walk", "attack", "hit", "death") and base_nm not in old_acts:
        old_acts[base_nm] = a
for nm_, a in old_acts.items():
    a.name = "shipped_" + nm_
CLIPS = ["idle", "walk", "attack", "hit", "death"]
LOOPS = {"idle", "walk"}
clip_rep = {}


def copy_fcurves(old, new, target):
    n_fc = 0
    for fc in K.action_fcurves(old):
        dp = fc.data_path
        for nn_, on_ in OLDNAME.items():          # OLDNAME maps new -> shipped; the carry goes shipped -> new
            dp = dp.replace('pose.bones["%s"]' % on_, 'pose.bones["%s"]' % nn_)
        sc_ = RATIO if dp.endswith(".location") else 1.0
        nfc = new.fcurve_ensure_for_datablock(target, dp, index=fc.array_index, group_name=dp.split('"')[1] if '"' in dp else "")
        nfc.keyframe_points.clear()
        nfc.keyframe_points.add(len(fc.keyframe_points))
        for kp, nk in zip(fc.keyframe_points, nfc.keyframe_points):
            nk.co = (kp.co[0], kp.co[1] * sc_)
            nk.interpolation = kp.interpolation
            nk.easing = kp.easing
            nk.handle_left_type = kp.handle_left_type; nk.handle_right_type = kp.handle_right_type
            nk.handle_left = (kp.handle_left[0], kp.handle_left[1] * sc_)
            nk.handle_right = (kp.handle_right[0], kp.handle_right[1] * sc_)
        nfc.extrapolation = fc.extrapolation
        nfc.update()
        n_fc += 1
    return n_fc


NEW_ACTS = {}
for cn in CLIPS:
    old = old_acts[cn]
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    nfc = copy_fcurves(old, act, rig)
    f0, f1 = old.frame_range
    act.use_frame_range = True
    act.frame_start, act.frame_end = f0, f1
    act.use_cyclic = cn in LOOPS
    NEW_ACTS[cn] = act
    clip_rep[cn] = {"fcurves": nfc, "frames": [int(f0), int(f1)], "cyclic": cn in LOOPS,
                    "keys": sorted(set(int(round(k.co[0])) for fc in K.action_fcurves(act) for k in fc.keyframe_points))}
rig.animation_data.action = None
K.assign_action(OLD_RIG, None)
if OLD_RIG.animation_data:
    for tr in OLD_RIG.animation_data.nla_tracks:
        tr.mute = True


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


# correspondences new -> shipped (rest, normalised by height about each floor centre)
OWn = (OW - c_old) / H_old
kd_o = KDTree(len(OWn))
for i, p in enumerate(OWn):
    kd_o.insert(p, i)
kd_o.balance()
Wn = (W_ - c_new) / H_new
corr = np.array([kd_o.find(p)[1] for p in Wn])
corr_d = np.array([kd_o.find(p)[2] for p in Wn])
contact = W_[:, 2] < 0.005 * H_new + lo_n[2]
OLD_VG = [g_.name for g_ in OLD_MESH.vertex_groups]
OLD_WT = np.zeros((len(old_me.vertices), len(OLD_VG)))
for v_ in old_me.vertices:
    for g_ in v_.groups:
        OLD_WT[v_.index, g_.group] = g_.weight
rest_new = W_.copy()
rest_old = OW.copy()
bone_pairs = [(nm, OLDNAME.get(nm, nm)) for nm in DEFORM]
for cn in CLIPS:
    act = NEW_ACTS[cn]; old = old_acts[cn]
    K.assign_action(rig, act); K.assign_action(OLD_RIG, old)
    f0, f1 = int(round(act.frame_range[0])), int(round(act.frame_range[1]))
    first = last = None
    bone_pos = bone_rot = 0.0
    surf = []
    slide_new = slide_old = 0.0
    minz = 1e9
    root_off = 0.0
    base_xy = 0.0
    for f in range(f0, f1 + 1):
        scene.frame_set(f)
        C = eval_coords(low); CO = eval_coords(OLD_MESH)
        if f == f0:
            first = C
        if f == f1:
            last = C
        for nn_, on_ in bone_pairs:
            pn = np.array(rig.pose.bones[nn_].matrix); po = np.array(OLD_RIG.pose.bones[on_].matrix)
            hp = (pn[:3, 3] - c_new) / H_new; ho = (po[:3, 3] - c_old) / H_old
            bone_pos = max(bone_pos, float(np.linalg.norm(hp - ho)))
            Rn = pn[:3, :3] / np.linalg.norm(pn[:3, :3], axis=0); Ro = po[:3, :3] / np.linalg.norm(po[:3, :3], axis=0)
            bone_rot = max(bone_rot, float(np.degrees(np.arccos(np.clip((np.trace(Rn.T @ Ro) - 1) / 2, -1, 1)))))
        dn = (C - rest_new) / H_new
        do = (CO[corr] - rest_old[corr]) / H_old
        surf.append(np.linalg.norm(dn - do, axis=1))
        slide_new = max(slide_new, float(np.linalg.norm((C - rest_new)[contact, :2], axis=1).max()))
        old_contact = rest_old[:, 2] < 0.005 * H_old + lo_o[2]
        slide_old = max(slide_old, float(np.linalg.norm((CO - rest_old)[old_contact, :2], axis=1).max()) * RATIO)
        minz = min(minz, float(C[:, 2].min()))
        root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        base_xy = max(base_xy, float(np.linalg.norm(np.array(rig.pose.bones["base"].head)[:2] - np.array(arm_data.bones["base"].head_local)[:2])))
    surf_v = np.max(np.stack(surf), axis=0)
    surf = np.concatenate(surf)
    worst_v = np.argsort(-surf_v)[:5]
    seam = float(np.linalg.norm(first - last, axis=1).max())
    clip_rep[cn].update({
        "seam_mm_sculpt_units_x1000": round(seam * 1000, 4),
        "seam_pct_H": round(100 * seam / H_new, 5),
        "contact_slide_max": round(slide_new, 4), "contact_slide_pct_H": round(100 * slide_new / H_new, 3),
        "shipped_contact_slide_max_scaled": round(slide_old, 4),
        "min_z": round(minz, 4), "min_z_pct_H": round(100 * minz / H_new, 3),
        "root_offset_max": round(root_off, 8), "base_xy_drift_max": round(base_xy, 6),
        "vs_shipped": {"bone_head_max_pct_H": round(100 * bone_pos, 4), "bone_rot_max_deg": round(bone_rot, 4),
                       "surface_motion_dev_p50_pct_H": round(100 * float(np.percentile(surf, 50)), 3),
                       "surface_motion_dev_p95_pct_H": round(100 * float(np.percentile(surf, 95)), 3),
                       "surface_motion_dev_max_pct_H": round(100 * float(surf.max()), 3),
                       "worst_vertices": [{"xyz": W_[i].round(2).tolist(), "dev_pct_H": round(100 * float(surf_v[i]), 2),
                                           "new_bones": {DEFORM[j]: round(float(Wt[i, j]), 2) for j in np.nonzero(Wt[i] > 0.05)[0]},
                                           "shipped_bones": {OLD_VG[j]: round(float(OLD_WT[corr[i], j]), 2)
                                                             for j in np.nonzero(OLD_WT[corr[i]] > 0.05)[0]},
                                           "corr_dist": round(float(corr_d[i] * H_new), 3)} for i in worst_v]}})
    print("CLIP", cn, json.dumps(clip_rep[cn]))
rep["clips"] = clip_rep
rep["clip_rules"] = {
    "carry": "shipped fcurves copied key-for-key (co, handles, interpolation); pose.bones['root'] -> ['base']; location keys x H_new/H_old",
    "seam": "max vertex distance, first vs last frame of the clip (loops must close; one-shots report their end pose)",
    "slide": "max horizontal displacement of ground-contact vertices (rest z < 0.5% H) over the clip (a skirted glider: the "
             "skirt-flap rotation moves the hem; the shipped value is quoted beside it, scaled to the new height)",
    "in_place": "contract root never moves (root_offset_max); 'base' offsets are the shipped clips' own trunk moves (the "
                "prior art keyed root.location in BONE space: idle/walk 'bob' is a forward sway along -Y, hit a lift, death a drop)",
    "vs_shipped": "per frame: pose-bone heads (normalised by height about each floor centre) and rotations vs the shipped rig; "
                  "surface motion = each new vertex's displacement vs its nearest shipped vertex's displacement (/H)",
    "correspondence_rest_distance_p95_pct_H": round(100 * float(np.percentile(corr_d, 95)), 3)}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_euler = (0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, 48
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "duskmaw v2: skirted totem (base/spine/chest/head/crown, arm+blade x2, skirt F/B/L/R) + contract root"
low["conquest_clips"] = CLIPS
low["conquest_clip_status"] = "the 5 shipped (artist-approved) clips, carried key-for-key onto the rebuilt rig"

# remove the shipped reference data before saving
for a in list(old_acts.values()):
    bpy.data.actions.remove(a)
for o in (OLD_MESH, OLD_RIG):
    d_ = o.data
    bpy.data.objects.remove(o, do_unlink=True)
    if d_.users == 0:
        (bpy.data.meshes if isinstance(d_, bpy.types.Mesh) else bpy.data.armatures).remove(d_)
bpy.data.collections.remove(old_coll)
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for a in list(bpy.data.actions):
    if a.name not in CLIPS:
        bpy.data.actions.remove(a)
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
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "archetype", "seconds")}))
sys.stdout.flush()
os._exit(0)
