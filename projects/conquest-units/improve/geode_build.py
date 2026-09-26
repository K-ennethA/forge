"""Geode (hero) build: winding fix + CRYSTAL FACETING + emerald glow palette + part rig + PROPOSED idle, one run.

    blender --background source-copies/hero-gem_knight.blend --factory-startup --python improve/geode_build.py -- \
        [--skins default,amethyst] [--set CONST=value ...]

Outputs (the opened source copy is never saved over; save_as_mainfile copy=True):
    improved/geode.blend + .json      faceted hero mesh, UVs, palette regions + default paint (no rig)
    rigged/geode.blend + .json        + one rigid bone per part, electricity arcs (shape-key flicker), 'idle' v2 + 'walk'
    rigged/geode__<skin>.blend        one per extra --skins entry: palette swap only (palettes/geode/<skin>.json)

Artist context (design/review-log.md 2026-09-25): "geode is facing the correct way he is a sentient diamond
rock creature"; "geode should be colored like an emerald-ish glowy color"; hero tier approved, natural
proportions (cell fit report-only). Hero survey: 7 floating parts, 17,980 source tris, three parts carry
NEGATIVE object scales (inside-out once applied), no materials, garbage UVs on the torso; a normal/AO bake
adds nothing at this density -> no bake; the identity is the facets.

Pipeline:
  1. parts to world space; negative-determinant parts get their winding reversed (signed volume gate).
  2. CRYSTAL FACETING, per part:
       torso / arms / legs  VSA (variational shape approximation, Cohen-Steiner et al. 2004): L2,1 Lloyd
                            partition into FACETS[part] planar proxies, then anchor + chain extraction ->
                            one planar-ish ngon per facet with straight crystal edges (vertices = least-squares
                            intersections of the adjacent facet planes). The right arm/leg are the left ones
                            carried by the source's own relative transform (the sculpt's R parts are rotated
                            copies), so the pair is cut identically.
       head / core          GEM CUT: intersection of support half-spaces over rows of directions around the
                            part's axis (brilliant-cut rows) -> exact planar convex facets; the head's eye row
                            puts two facets at +-22.5 deg off the front: the eyes.
     then every facet edge is chamfered (BEVEL_W) -> the "edge planes" / crystal seams, and triangulated.
  3. colour regions -> palettes.store_regions; paint from palettes/geode/default.json (Col + Glow, the
     Vineweave pattern: Glow feeds Emission Color). Inner facets = facets sunk below the part's convex hull.
  2d. ELECTRICITY ARCS (motion v2): per ARC_GAPS entry, ARC_STRANDS jagged triangular-tube strands from part A's surface
     to part B's (ends buried ARC_SINK deep), ARC_VARIANTS shapes per strand on one vertex layout; painted from the
     palette region ARC_REGION (gated to be the top glow tier in every skin).
  4. rig: root + one bone per part; legs are children of the BODY (v2). Rigid weights (1.0, one influence) on the parts;
     arc vertices blend part A -> part B along the strand. Arc variants 1.. become shape keys 'arc_flicker_<k>'.
  5. motion v2 (artist 2026-09-25: "geode walks and its limbs are connected statically, maybe have some elecriticty
     wiring them together, so it walks pretty normally, can floatish"):
       idle  whole-body float + fore/aft sway about the foot line, core contraction + glow pulse, arc flicker; head, arms
             and legs keyed at identity (static on the body).
       walk  stiff biped: leg crystals hinge at the hip in antiphase, body rolls over the stance leg, shoulders
             counter-twist, float at mid-stance, soft-min foot contacts; in place (root never keyed).
     Arc flicker = shape-key swaps on CONSTANT keys in a Key slot of each clip's action (glTF morph weights, which
     Godot imports natively; emission-strength keys need KHR_animation_pointer - see design/godot-import-notes.md).
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, heapq, ast
import numpy as np
from mathutils import Matrix, Vector, Quaternion, Euler, geometry
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402
import palettes as PAL  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun)
UNIT = "geode"
FACETS = {"torso": 220, "arm": 44, "leg": 44}     # "facet count" per cut part (fewer = bigger gem facets)
VSA_ITERS = 14                                      # facet layout relaxation passes
LABEL_SMOOTH = 0                                    # facet border vote passes (measured: changes 0 faces here -- the sawtooth was chain tolerance)
CHAIN_TOL = 0.025                                   # "facet edge straightness" (fraction of part size; bigger = straighter)
QEF_LAMBDA = 1.0                                    # how hard facet corners hold the sculpt vs the facet planes
PLANARIZE_ITERS = 0                                 # "facet flatness" passes (measured: 6 passes flatten torso 0.0094 -> 0.0018 but arm folds 3 -> 72; off)
PLANARIZE_MU = 0.15                                 # how far one flatness pass may move a corner
POKE_REPAIR = False                                 # fan folded facets from their centre (measured 2026-09-25: 93 -> 101 folds on the torso; off)
EYE_SCALE = 0.42                                    # "eye size": inset gem, fraction of its facet
EYE_DEPTH = 0.012                                   # "eye sink" (fraction of the head size)
GEMCUT_ROWS = {                                     # "gem cut" rows: (elevation deg, facets in the row, azimuth offset deg)
    "head": [(-80, 1, 0.0), (-55, 8, 22.5), (-28, 8, 0.0), (0, 8, 22.5), (20, 8, 0.0), (42, 8, 22.5), (68, 8, 0.0)],
    "core": [(-62, 8, 0.0), (-30, 8, 22.5), (0, 8, 0.0), (30, 8, 22.5), (62, 8, 0.0)]}
EYE_ROW = ("head", 3)                               # which gem-cut row carries the eyes (the two facets nearest the front)
BEVEL_W = 0.05                                      # "seam width": chamfer on every facet edge (sculpt units; 0 = knife-sharp)
MIN_EDGE = 0.12                                     # facet edges shorter than this collapse before the chamfer (sculpt units)
WELD_DIST = 0.004                                  # corners closer than this merge after the chamfer (sculpt units; invisible)
MIN_TRI_AREA = 2e-6                                 # a triangle below this 3D area is degenerate (gate: 0 left)
INNER_DEPTH = 0.045                                 # "inner facets": sunk deeper than this (fraction of part size) below the hull -> glow
CROWN_NZ, PAVILION_NZ = 0.30, -0.30                 # facet families: up-facing crown / side girdle / down-facing pavilion
FACET_JITTER = 0.26                                 # per-facet value variation (sparkle), +-half this
TRI_BUDGET = [4000, 20000]                          # declared window (contract tri_budget): identity = facets, not density
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9                  # Conquest hero ceilings -- REPORT ONLY (scale policy 2026-09-25)
# --------------------------------------------------------------------------- ARTIST KNOBS: motion v2 (2026-09-25)
# Artist: "geode walks and its limbs are connected statically, maybe have some elecriticty wiring them together, so it
# walks pretty normally, can floatish". Scale note: 1 sculpt unit ~= 7.5 cm in game (shipped height 1.8 m / 24.04).
# ---- electricity arcs (thin jagged glowing strands bridging each gap between the static parts)
ARC_GAPS = [("torso", "head", "neck"), ("torso", "arm.L", "shoulder"), ("torso", "arm.R", "shoulder"),
            ("torso", "core"), ("core", "leg.L"), ("core", "leg.R")]
#            "which gaps get arcs" (legs hang below the core: 2.4 vs 4.4 to the torso). Optional 3rd field = anchor
#            mode (artist 2026-09-25): "neck" = the head arc goes straight down like a neck (plumb anchor pair);
#            "shoulder" = the torso end attaches up in the shoulder band instead of the horizontal nearest pair.
ARC_NECK_PLUMB_W = 8.0                              # "how strictly vertical the neck arc is" (horizontal-offset weight)
ARC_NECK_CENTER_W = 4.0                             # "how strongly the neck sits on the chest centerline" (x=0 pull)
ARC_SHOULDER_FRAC = 0.80                            # "how high on the torso the arm arcs attach" (0 = torso bottom, 1 = top)
ARC_SHOULDER_W = 2.0                                # "how strongly the arm arcs snap to that height"
ARC_STRANDS = 2                                     # "arc count per gap" (strands)
ARC_SEGMENTS = 8                                    # "arc kinks": segments per strand (tris per strand = 6 x this + 2)
ARC_JAG = 0.16                                      # "jag amplitude": lightning wander, fraction of the strand length ...
ARC_JAG_MAX = 0.42                                  # ... capped at this (sculpt units) so long arcs stay arcs, not loops
ARC_BOW = 0.6                                       # how far the paired strands of one gap bow apart (x jag amplitude)
ARC_RADIUS = 0.07                                   # "arc thickness" (strand radius, sculpt units; ~5 mm in game)
ARC_SPREAD = 0.7                                    # how far apart the strands of one gap land (sculpt units)
ARC_SINK = 0.18                                     # how deep each strand end buries into its part (sculpt units)
ARC_VARIANTS = 3                                    # pre-built arc shapes per gap (shape-key swaps = the flicker)
ARC_FLICKER_HZ = 12.0                               # "flicker rate": arc shape swaps per second (snaps, CONSTANT keys)
ARC_REGION = "arc"                                  # palette region the arcs paint from (palettes/geode/<skin>.json)
ARC_GLOW_TIER = "top"                               # "glow tier": the arc region's glow must outshine every other region
#                                                     in every built skin (gated); the colour/strength live in the palette
# ---- idle v2 (whole creature is one rigid assembly: no part drifts; the core pulses in place)
IDLE_FRAMES = 120                                   # idle loop length (24 fps -> 5 s)
IDLE_FLOAT = 0.32                                   # "idle float": whole-body lift off the ground (sculpt units; ~2.4 cm)
IDLE_SWAY_DEG = 0.8                                 # "idle sway": fore/aft rock about the foot line
IDLE_CORE_PULSE = 0.07                              # core contraction (scale 1 -> 1 - this; never grows past rest)
IDLE_GLOW_PULSE = 0.45                              # glow flare on each core beat (emission strength x (1 + this))
# ---- walk (stiff crystal biped: legs hinge at the hip, no knees; the body rocks to sell the step)
WALK_FRAMES = 32                                    # "walk cycle": frames per stride (2 steps) at 24 fps -> 1.33 s, 90 steps/min
WALK_LEG_SWING_DEG = 20.0                           # "step size": leg-crystal pitch each way about the hip
WALK_ROLL_DEG = 4.0                                 # "side rock": body roll over the stance leg (lifts the swing foot)
WALK_TORSO_YAW_DEG = 3.0                            # "shoulder counter-twist" (arms read as swinging, still rigid)
WALK_LEAN_DEG = 2.0                                 # constant forward lean (about the ground point)
WALK_FLOAT = 0.24                                   # "walk float": extra whole-body lift at mid-stance (sculpt units)
WALK_CONTACT_SOFT = 0.06                            # "soft contacts": soft-min temperature of the foot solve (sculpt units)
WALK_CORE_PULSE = 0.05                              # core contraction on each footfall
WALK_GLOW_PULSE = 0.30                              # glow flare on each footfall

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SKINS = ["default"]
if "--skins" in argv:
    SKINS = argv[argv.index("--skins") + 1].split(",")
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
report = {"unit": UNIT, "source": bpy.data.filepath, "tier": "hero", "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0,
          "overrides": OVERRIDES}
TAU = 2 * math.pi


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def signed_volume(V, T):
    V = np.asarray(V); T = np.asarray(T)
    return float(np.einsum("ij,ij->i", V[T[:, 0]], np.cross(V[T[:, 1]], V[T[:, 2]])).sum() / 6.0)


def tri_normals(V, T):
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], 0.5 * a


# =========================================================================== 1. source parts (world, winding)
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
SRC = {"Sphere": "torso", "Sphere.001": "core", "Sphere.006": "head",
       "Sphere.002": "leg.L", "Sphere.004": "leg.R", "Sphere.003": "arm.L", "Sphere.005": "arm.R"}
PARTS = ["torso", "head", "core", "arm.L", "arm.R", "leg.L", "leg.R"]
src = {}
wind = {}
for on, pn in SRC.items():
    o = bpy.data.objects[on]
    me = o.data
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    M = np.array(o.matrix_world)
    W = co @ M[:3, :3].T + M[:3, 3]
    tris = []
    for p in me.polygons:
        vs = list(p.vertices)
        for k in range(1, len(vs) - 1):
            tris.append([vs[0], vs[k], vs[k + 1]])
    T = np.array(tris, dtype=np.int64)
    det = float(np.linalg.det(M[:3, :3]))
    vol0 = signed_volume(W, T)
    if det < 0:
        T = T[:, ::-1].copy()
    vol1 = signed_volume(W, T)
    src[pn] = {"V": W, "T": T, "local": co, "M": M, "obj": on}
    wind[pn] = {"object": on, "scale": [round(v, 4) for v in o.scale], "det": round(det, 4),
                "signed_volume_as_stored": round(vol0, 3), "signed_volume_fixed": round(vol1, 3),
                "winding_reversed": det < 0, "tris_src": int(len(T))}
    assert vol1 > 0, "winding fix failed on %s" % pn
report["winding"] = wind
report["tris_source"] = int(sum(w["tris_src"] for w in wind.values()))
# R limbs are the L sculpt rotated (same mesh data, rot z = pi): cut L once, carry it by the relative transform
CARRY = {}
for r_, l_ in (("leg.R", "leg.L"), ("arm.R", "arm.L")):
    same = src[r_]["local"].shape == src[l_]["local"].shape and np.allclose(src[r_]["local"], src[l_]["local"], atol=1e-5)
    rel = src[r_]["M"] @ np.linalg.inv(src[l_]["M"])
    CARRY[r_] = rel if same else None
report["carried_parts"] = {r_: {"from": l_, "same_mesh": CARRY[r_] is not None,
                                "relative_det": round(float(np.linalg.det(CARRY[r_][:3, :3])), 6) if CARRY[r_] is not None else None}
                           for r_, l_ in (("leg.R", "leg.L"), ("arm.R", "arm.L"))}


# =========================================================================== 2a. VSA faceting
def face_adjacency(T):
    ef = {}
    for f, (a, b, c) in enumerate(T):
        for u, v in ((a, b), (b, c), (c, a)):
            ef.setdefault((min(u, v), max(u, v)), []).append(f)
    nb = [[] for _ in range(len(T))]
    bad = 0
    for fs in ef.values():
        if len(fs) == 2:
            nb[fs[0]].append(fs[1]); nb[fs[1]].append(fs[0])
        else:
            bad += 1
    return nb, bad


def vsa_partition(V, T, K, iters):
    N, A = tri_normals(V, T)
    C = V[T].mean(1)
    nb, bad = face_adjacency(T)
    assert bad == 0, "source part not closed-manifold (%d edges)" % bad
    m = len(T)
    # deterministic farthest-point seeds (start at the highest face)
    seeds = [int(np.argmax(C[:, 2]))]
    d = np.linalg.norm(C - C[seeds[0]], axis=1)
    for _ in range(K - 1):
        s = int(np.argmax(d)); seeds.append(s)
        d = np.minimum(d, np.linalg.norm(C - C[s], axis=1))
    prox = N[seeds].copy()

    def flood(seeds, prox):
        lab = -np.ones(m, dtype=np.int64)
        heap = []
        for k, s in enumerate(seeds):
            lab[s] = k
            for g in nb[s]:
                heapq.heappush(heap, (float(A[g] * ((N[g] - prox[k]) ** 2).sum()), g, k))
        while heap:
            e, f, k = heapq.heappop(heap)
            if lab[f] >= 0:
                continue
            lab[f] = k
            for g in nb[f]:
                if lab[g] < 0:
                    heapq.heappush(heap, (float(A[g] * ((N[g] - prox[k]) ** 2).sum()), g, k))
        return lab
    lab = flood(seeds, prox)
    hist = []
    for _ in range(iters):
        acc = np.zeros((K, 3))
        np.add.at(acc, lab, A[:, None] * N)
        prox = acc / np.maximum(np.linalg.norm(acc, axis=1), 1e-30)[:, None]
        E = A * ((N - prox[lab]) ** 2).sum(1)
        hist.append(float(E.sum()))
        order = np.lexsort((E, lab))
        first = np.ones(m, bool); first[1:] = lab[order][1:] != lab[order][:-1]
        seeds = [int(f) for f in order[first]]
        lab = flood(seeds, prox)
    # boundary clean-up: a face whose neighbours (2 of 3) agree on another facet joins it -- removes the sawtooth the
    # dense source quads leave where two facets have similar normals; then split any facet the vote cut in two
    changed = []
    for _ in range(LABEL_SMOOTH):
        nl = lab.copy()
        for f in range(m):
            a_, b_, c_ = (lab[g] for g in nb[f]) if len(nb[f]) == 3 else (lab[f],) * 3
            for L in (a_, b_, c_):
                if L != lab[f] and (a_ == L) + (b_ == L) + (c_ == L) >= 2:
                    nl[f] = L
                    break
        changed.append(int((nl != lab).sum()))
        lab = nl
    comp = -np.ones(m, dtype=np.int64)
    newlab = lab.copy()
    nextk = int(lab.max()) + 1
    sizes = {}
    for f0 in range(m):
        if comp[f0] >= 0:
            continue
        stack = [f0]; comp[f0] = f0; members = [f0]
        while stack:
            f = stack.pop()
            for g in nb[f]:
                if comp[g] < 0 and lab[g] == lab[f0]:
                    comp[g] = f0; stack.append(g); members.append(g)
        sizes.setdefault(int(lab[f0]), []).append(members)
    split = 0
    for L, groups in sizes.items():
        groups.sort(key=len, reverse=True)
        for g in groups[1:]:
            newlab[g] = nextk; nextk += 1; split += 1
    lab = newlab
    K = nextk
    acc = np.zeros((K, 3)); np.add.at(acc, lab, A[:, None] * N)
    prox = acc / np.maximum(np.linalg.norm(acc, axis=1), 1e-30)[:, None]
    wsum = np.bincount(lab, A, minlength=K)
    cen = np.zeros((K, 3)); np.add.at(cen, lab, A[:, None] * C)
    cen /= np.maximum(wsum, 1e-30)[:, None]
    dplane = (prox * cen).sum(1)
    return lab, prox, dplane, {"error_first_last": [round(hist[0], 5), round(hist[-1], 5)] if hist else None,
                               "label_smooth_changed": changed, "facets_split_after_vote": split,
                               "facets_nonempty": int((wsum > 0).sum())}


def vsa_extract(V, T, lab, prox, dplane, tol, tol_scale=None):
    """anchor + chain extraction -> (verts, facet polygons [list of cycles], facet id per polygon, stats).
    tol_scale: {chain index: multiplier} -- the fold repair tightens the chains around a folded facet."""
    tol_scale = tol_scale or {}
    nV = len(V)
    vreg = [set() for _ in range(nV)]
    for f, t in enumerate(T):
        for v in t:
            vreg[v].add(int(lab[f]))
    he = {}
    for f, (a, b, c) in enumerate(T):
        he[(a, b)] = f; he[(b, c)] = f; he[(c, a)] = f
    bgraph = {}
    rhalf = {}
    for (u, v), f in he.items():
        g = he[(v, u)]
        if lab[g] != lab[f]:
            bgraph.setdefault(u, set()).add(v)
            rhalf.setdefault(int(lab[f]), []).append((u, v))
    anchor = set(v for v in bgraph if len(vreg[v]) >= 3 or len(bgraph[v]) != 2)
    seen = set()
    chains = []
    for a in sorted(anchor):
        for b in sorted(bgraph[a]):
            if (a, b) in seen:
                continue
            path = [a, b]; prev, cur = a, b
            while cur not in anchor:
                nxt = [w for w in bgraph[cur] if w != prev][0]
                prev, cur = cur, nxt; path.append(cur)
            for i in range(len(path) - 1):
                seen.add((path[i], path[i + 1])); seen.add((path[i + 1], path[i]))
            chains.append(path)
    loops_free = 0
    for u in sorted(bgraph):
        for w in sorted(bgraph[u]):
            if (u, w) in seen:
                continue
            path = [u, w]; prev, cur = u, w
            while cur != u:
                nxt = [x for x in bgraph[cur] if x != prev][0]
                prev, cur = cur, nxt; path.append(cur)
            for i in range(len(path) - 1):
                seen.add((path[i], path[i + 1])); seen.add((path[i + 1], path[i]))
            anchor.add(u); chains.append(path); loops_free += 1

    def seg_d(P, a, b):
        ab = b - a
        t = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-18), 0, 1)
        return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)

    kept = set(anchor)
    chain_keep = []
    chain_regs = []
    for ci, path in enumerate(chains):
        P = V[path]
        f0, f1 = he[(path[0], path[1])], he[(path[1], path[0])]
        chain_regs.append((int(lab[f0]), int(lab[f1])))
        ctol = tol * tol_scale.get(ci, 1.0)
        keep = {0, len(path) - 1}
        if path[0] == path[-1]:
            j = int(np.argmax(np.linalg.norm(P - P[0], axis=1)))
            keep.add(j)
            lo_, hi_ = (0, j) if j > len(path) - 1 - j else (j, len(path) - 1)
            if hi_ - lo_ >= 2:
                jj = lo_ + 1 + int(np.argmax(seg_d(P[lo_ + 1:hi_], P[lo_], P[hi_])))
                keep.add(jj)
        stack = sorted(keep)
        segs = list(zip(stack[:-1], stack[1:]))
        while segs:
            lo_, hi_ = segs.pop()
            if hi_ - lo_ < 2:
                continue
            dd = seg_d(P[lo_ + 1:hi_], P[lo_], P[hi_])
            j = int(np.argmax(dd))
            if dd[j] > ctol:
                jj = lo_ + 1 + j
                keep.add(jj); segs += [(lo_, jj), (jj, hi_)]
        chain_keep.append(keep)
    # two chains simplified to the same anchor-anchor segment -> force a middle vertex on all but one
    pair = {}
    for ci, (path, keep) in enumerate(zip(chains, chain_keep)):
        if len(keep) == 2 and len(path) > 2:
            pair.setdefault((min(path[0], path[-1]), max(path[0], path[-1])), []).append(ci)
    forced = 0
    for key, cis in pair.items():
        for ci in cis[1:]:
            path = chains[ci]; P = V[path]
            j = 1 + int(np.argmax(seg_d(P[1:-1], P[0], P[-1])))
            chain_keep[ci].add(j); forced += 1
    for path, keep in zip(chains, chain_keep):
        for j in keep:
            kept.add(path[j])
    # positions: least-squares meet of the adjacent facet planes, held to the sculpt by QEF_LAMBDA
    NP = {}
    for v in kept:
        rs = sorted(vreg[v])
        A_ = QEF_LAMBDA * np.eye(3); b_ = QEF_LAMBDA * V[v]
        for r in rs:
            n_ = prox[r]
            A_ = A_ + np.outer(n_, n_); b_ = b_ + n_ * dplane[r]
        NP[v] = np.linalg.solve(A_, b_)
    # facet cycles (region boundary half-edges, region winding) filtered to kept vertices
    polys = facet_cycles(rhalf, kept)
    # planarize: refit each facet's plane to its own corners, re-meet the planes (held to the current spot)
    members = {}
    for r, cycles in polys:
        for c in cycles:
            for v in c:
                members.setdefault(v, set()).add(r)
    for _ in range(PLANARIZE_ITERS):
        pl = {}
        for r, cycles in polys:
            P = np.array([NP[v] for c in cycles for v in c])
            c0 = P.mean(0)
            n_ = np.linalg.svd(P - c0)[2][-1]
            if n_ @ prox[r] < 0:
                n_ = -n_
            pl[r] = (n_, float(n_ @ c0))
        for v, rs in members.items():
            A_ = PLANARIZE_MU * np.eye(3); b_ = PLANARIZE_MU * NP[v]
            for r in rs:
                n_, d_ = pl[r]
                A_ = A_ + np.outer(n_, n_); b_ = b_ + n_ * d_
            NP[v] = np.linalg.solve(A_, b_)
    return NP, polys, chain_regs, {"anchors": len(anchor), "chains": len(chains), "anchor_free_loops": loops_free,
                                   "forced_splits": forced, "kept_verts": len(kept)}


def facet_cycles(rhalf, kept):
    polys = []
    for r, hs in sorted(rhalf.items()):
        nxt = {}
        for u, v in hs:
            nxt.setdefault(u, []).append(v)
        used = set()
        cycles = []
        for u0, v0 in hs:
            if (u0, v0) in used:
                continue
            cyc = [u0]; used.add((u0, v0)); cur = v0
            guard = 0
            while cur != u0 and guard < 100000:
                cyc.append(cur)
                cands = [w for w in nxt[cur] if (cur, w) not in used]
                if not cands:
                    break
                used.add((cur, cands[0])); cur = cands[0]; guard += 1
            cycles.append([c for c in cyc if c in kept])
        cycles = [c for c in cycles if len(c) >= 3]
        if cycles:
            polys.append((r, cycles))
    return polys


def build_bm_from_facets(NP, polys, prox):
    """facet polygons -> bmesh (ngon per facet; multi-cycle / pinched facets tessellated) with int layer 'facet'."""
    bm = bmesh.new()
    fl = bm.faces.layers.int.new("facet")
    bv = {}
    for v, p in NP.items():
        bv[v] = bm.verts.new(Vector(p))
    tessellated = 0
    failed = 0
    for r, cycles in polys:
        ok = False
        if len(cycles) == 1 and len(set(cycles[0])) == len(cycles[0]):
            try:
                f = bm.faces.new([bv[i] for i in cycles[0]])
                f[fl] = r; ok = True
            except ValueError:
                ok = False
        if not ok:
            loops = [[Vector(NP[i]) for i in c] for c in cycles]
            flat = [i for c in cycles for i in c]
            tris = geometry.tessellate_polygon(loops)
            tessellated += 1
            for t in tris:
                vs = [flat[k] for k in t]
                n_ = np.cross(NP[vs[1]] - NP[vs[0]], NP[vs[2]] - NP[vs[0]])
                if n_ @ prox[r] < 0:
                    vs = vs[::-1]
                try:
                    f = bm.faces.new([bv[i] for i in vs]); f[fl] = r
                except ValueError:
                    failed += 1
    return bm, {"tessellated_facets": tessellated, "failed_faces": failed}


def folded_facets(bm, prox):
    """{facet: folded tri count} of a triangulated COPY (tri normal against its facet's proxy normal)."""
    b2 = bm.copy()
    bmesh.ops.triangulate(b2, faces=b2.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
    fl2 = b2.faces.layers.int.get("facet")
    b2.normal_update()
    bad = {}
    for f in b2.faces:
        r = f[fl2]
        if r >= 0 and f.normal.dot(Vector(prox[r])) < 0:
            bad[r] = bad.get(r, 0) + 1
    b2.free()
    return bad


# =========================================================================== 2b. gem cut
def gem_dirs(rows, front=np.array([0.0, -1.0, 0.0])):
    out = []
    for ri, (el, n, off) in enumerate(rows):
        e = math.radians(el)
        for k in range(n):
            az = math.radians(off + 360.0 * k / n)
            # az 0 = the front (-Y); +az turns toward +X
            d = np.array([math.cos(e) * math.sin(az), -math.cos(e) * math.cos(az), math.sin(e)])
            out.append((ri, k, d / np.linalg.norm(d), off + 360.0 * k / n))
    return out


def gem_cut(V, T, rows):
    dirs = gem_dirs(rows)
    c = V.mean(0)
    R = float(np.linalg.norm(V - c, axis=1).max()) * 3.0
    bm = bmesh.new()
    fl = bm.faces.layers.int.new("facet")
    bmesh.ops.create_cube(bm, size=2 * R, matrix=Matrix.Translation(Vector(c)))
    for f in bm.faces:
        f[fl] = -1
    for i, (ri, k, d, az) in enumerate(dirs):
        h = float((V @ d).max())
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-7, plane_co=Vector(d * h), plane_no=Vector(d), clear_outer=True)
        cut = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge) and e.is_valid]
        if cut:
            nf = bmesh.ops.contextual_create(bm, geom=cut)["faces"]
            for f in nf:
                f[fl] = i
                if f.normal.dot(Vector(d)) < 0:
                    f.normal_flip()
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=R * 1e-4)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=R * 1e-5)
    left = sum(1 for f in bm.faces if f[fl] == -1)
    assert left == 0, "gem cut left %d bounding-box faces (rows do not close the part)" % left
    # the support planes circumscribe the sculpt (+19..30% volume): scale about the centroid to the sculpt's volume
    k = (signed_volume(V, T) / bm.calc_volume(signed=True)) ** (1.0 / 3.0)
    cv = Vector(c)
    for v in bm.verts:
        v.co = cv + (v.co - cv) * k
    return bm, dirs, k


def eye_inset(bm, dirs, rows_part, diag):
    """sink two small eye gems into the eye-row facets nearest the front; returns their new facet ids."""
    fl = bm.faces.layers.int.get("facet")
    cand = sorted((abs(((az + 180.0) % 360.0) - 180.0), j) for j, (ri, k, d, az) in enumerate(dirs) if ri == EYE_ROW[1])
    pick = [j for _, j in cand[:2]]
    bm.normal_update()
    faces = [f for f in bm.faces if f[fl] in pick]
    bmesh.ops.inset_individual(bm, faces=faces, thickness=1e-4 * diag, depth=0.0, use_even_offset=True)
    new_ids = []
    for f in faces:
        assert f.is_valid
        c = f.calc_center_median(); n = f.normal.copy()
        for v in f.verts:
            v.co = c + (v.co - c) * EYE_SCALE - n * (EYE_DEPTH * diag)
        nid = len(dirs) + len(new_ids)
        new_ids.append((nid, f[fl]))
        f[fl] = nid
    return new_ids, [abs(((dirs[j][3] + 180.0) % 360.0) - 180.0) for j in pick]


# =========================================================================== 2c. per part: facet, chamfer, triangulate
def finish_part(bm, name):
    """chamfer facet edges, triangulate; -> V, T, facet id per tri, chamfer flag per tri, stats."""
    fl = bm.faces.layers.int.get("facet")
    cl = bm.faces.layers.int.new("chamfer")
    # Blender's bevel clamp is GLOBAL: one short facet edge (gem-cut tips, short chain hops) shrinks every seam on the
    # part to ~0 (measured: the head's seams vanished). Collapse facet edges shorter than MIN_EDGE first.
    ne0 = len(bm.edges)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=MIN_EDGE)
    short_collapsed = ne0 - len(bm.edges)
    bm.normal_update()
    nonman = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    fedges = [e for e in bm.edges if len(e.link_faces) == 2 and e.link_faces[0][fl] != e.link_faces[1][fl]]
    n_facets = len(set(f[fl] for f in bm.faces))
    st = {"facets": n_facets, "facet_edges": len(fedges), "nonmanifold_edges_before_bevel": nonman,
          "short_edges_collapsed": short_collapsed,
          "shortest_facet_edge": round(min((e.calc_length() for e in fedges), default=0.0), 4)}
    fedge_len = sum(e.calc_length() for e in fedges)
    if BEVEL_W > 0 and fedges:
        res = bmesh.ops.bevel(bm, geom=fedges, offset=BEVEL_W, offset_type="OFFSET", profile_type="SUPERELLIPSE",
                              segments=1, profile=0.5, affect="EDGES", clamp_overlap=True, material=-1,
                              loop_slide=True, mark_seam=False, mark_sharp=False, harden_normals=False,
                              face_strength_mode="NONE", miter_outer="SHARP", miter_inner="SHARP", spread=0.1,
                              vmesh_method="ADJ")
        for f in res["faces"]:
            f[cl] = 1
        st["chamfer_faces"] = len(res["faces"])
        # effective seam width = chamfer area / facet-edge length (bevel's global clamp can shrink it silently)
        ch_area = sum(f.calc_area() for f in res["faces"])
        st["seam_width_effective"] = round(ch_area / max(fedge_len, 1e-9), 4)
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
    # bevel corners where many facet planes meet leave near-zero triangles (3D area ~5e-7; 156 zero-area UV faces
    # on the first gate): weld them away, then re-triangulate whatever the weld merged
    nv0, nf0 = len(bm.verts), len(bm.faces)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=WELD_DIST)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=WELD_DIST)
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
    tiny = [f for f in bm.faces if f.calc_area() < MIN_TRI_AREA]
    if tiny:
        bmesh.ops.dissolve_degenerate(bm, edges=list({e for f in tiny for e in f.edges}), dist=WELD_DIST * 4)
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
    st["weld"] = {"verts_removed": nv0 - len(bm.verts), "faces_removed": nf0 - len(bm.faces),
                  "tiny_tris_after_weld": sum(1 for f in bm.faces if f.calc_area() < MIN_TRI_AREA)}
    bm.verts.index_update()
    V = np.array([v.co[:] for v in bm.verts])
    T = np.array([[v.index for v in f.verts] for f in bm.faces], dtype=np.int64)
    fid = np.array([f[fl] for f in bm.faces], dtype=np.int64)
    ch = np.array([f[cl] for f in bm.faces], dtype=np.int64)
    st["nonmanifold_edges_final"] = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    return V, T, fid, ch, st


cut = {}
facet_normals = {}
facet_stats = {}
for pn in PARTS:
    if pn in CARRY and CARRY[pn] is not None:
        continue
    V, T = src[pn]["V"], src[pn]["T"]
    diag = float(np.linalg.norm(V.max(0) - V.min(0)))
    t0 = time.time()
    if pn in GEMCUT_ROWS:
        bm, dirs, kvol = gem_cut(V, T, GEMCUT_ROWS[pn])
        prox = np.array([d for (_, _, d, _) in dirs])
        st = {"method": "gem cut (support half-spaces, volume-matched)", "rows": GEMCUT_ROWS[pn], "planes": len(dirs),
              "volume_match_scale": round(kvol, 4)}
        cut[pn] = {"dirs": dirs, "eye_ids": []}
        if pn == EYE_ROW[0]:
            new_ids, eye_az = eye_inset(bm, dirs, GEMCUT_ROWS[pn], diag)
            prox = np.vstack([prox] + [prox[old][None] for _, old in new_ids])
            cut[pn]["eye_ids"] = [nid for nid, _ in new_ids]
            st["eyes"] = {"row": EYE_ROW[1], "azimuth_off_front_deg": [round(a, 1) for a in eye_az], "scale": EYE_SCALE,
                          "sink_frac": EYE_DEPTH}
    else:
        key = pn.split(".")[0]
        lab, prox, dpl, vst = vsa_partition(V, T, FACETS[key], VSA_ITERS)
        NP, polys, cregs, est = vsa_extract(V, T, lab, prox, dpl, CHAIN_TOL * diag)
        bm, bst = build_bm_from_facets(NP, polys, prox)
        bad = folded_facets(bm, prox)
        rep_ = {"folded_tris_ngon_triangulation": int(sum(bad.values())), "folded_facets": len(bad)}
        if bad and POKE_REPAIR:
            # a warped / notched facet whose ear-clip folds is fanned from its centre instead (star facet)
            fl_ = bm.faces.layers.int.get("facet")
            bmesh.ops.poke(bm, faces=[f for f in bm.faces if f[fl_] in bad], offset=0.0, center_mode="MEAN_WEIGHTED")
            bad2 = folded_facets(bm, prox)
            rep_.update({"poked_facets": len(bad), "folded_tris_after_poke": int(sum(bad2.values()))})
        st = {"method": "VSA L2,1 + anchor/chain extraction (+ planarize, + poke repair of folded facets)", "proxies": FACETS[key],
              **vst, **est, **bst, "chain_tol": round(CHAIN_TOL * diag, 4), "fold_repair": rep_}
        cut[pn] = {}
    Vp, Tp, fid, ch, fst = finish_part(bm, pn)
    st.update(fst)
    st["seconds"] = round(time.time() - t0, 2)
    cut[pn].update({"V": Vp, "T": Tp, "fid": fid, "ch": ch, "prox": prox})
    facet_stats[pn] = st
for r_, rel in CARRY.items():
    if rel is None:
        continue
    l_ = r_.replace(".R", ".L")
    c = cut[l_]
    cut[r_] = {"V": c["V"] @ rel[:3, :3].T + rel[:3, 3], "T": c["T"].copy(), "fid": c["fid"].copy(), "ch": c["ch"].copy(),
               "prox": c["prox"] @ rel[:3, :3].T}
    facet_stats[r_] = {"method": "carried from %s (source relative transform, rot z 180)" % l_, **{k: facet_stats[l_][k] for k in
                                                                                                  ("facets", "facet_edges")}}

# per-part quality: signed volume vs source, folds (tri normal vs its facet normal), facet planarity, deviation
for pn in PARTS:
    c = cut[pn]
    V, T = c["V"], c["T"]
    n_, a_ = tri_normals(V, T)
    fac = (c["fid"] >= 0) & (c["ch"] == 0)          # facet tris (chamfers sit between two facets by design)
    fn = c["prox"][np.clip(c["fid"], 0, None)]
    dots = (n_ * fn).sum(1)
    folds = fac & (dots < 0.0)
    chamfer_suspect = (c["ch"] == 1) & (c["fid"] >= 0) & (dots < 0.0)
    diag = float(np.linalg.norm(src[pn]["V"].max(0) - src[pn]["V"].min(0)))
    # planarity: per facet, max |distance to its best-fit plane| over its non-chamfer verts
    plan = 0.0
    for f_ in np.unique(c["fid"][(c["fid"] >= 0) & (c["ch"] == 0)]):
        vv = np.unique(T[(c["fid"] == f_) & (c["ch"] == 0)].ravel())
        P = V[vv] - V[vv].mean(0)
        if len(P) > 3:
            plan = max(plan, float(np.abs(P @ np.linalg.svd(P)[2][-1]).max()))
    # deviation from the sculpt: faceted verts -> nearest source surface
    bvh = BVHTree.FromPolygons(src[pn]["V"].tolist(), src[pn]["T"].tolist())
    dev = np.array([bvh.find_nearest(Vector(p))[3] for p in V])
    facet_stats[pn].update({
        "tris": int(len(T)), "signed_volume": round(signed_volume(V, T), 3),
        "volume_ratio_vs_source": round(signed_volume(V, T) / signed_volume(src[pn]["V"], src[pn]["T"]), 4),
        "folded_tris": int(folds.sum()), "folded_area_frac": round(float(a_[folds].sum() / a_.sum()), 6),
        "chamfer_tris_facing_away_from_their_facet": int(chamfer_suspect.sum()),
        "facet_planarity_max_frac": round(plan / diag, 5),
        "surface_dev_mean_frac": round(float(dev.mean() / diag), 5), "surface_dev_max_frac": round(float(dev.max() / diag), 5),
        "diag": round(diag, 4)})
report["faceting"] = facet_stats
for p in PARTS:
    print("FACETS", p, json.dumps({k: facet_stats[p].get(k) for k in (
        "facets", "tris", "fold_repair", "folded_tris", "folded_area_frac", "chamfer_tris_facing_away_from_their_facet", "volume_ratio_vs_source",
        "facet_planarity_max_frac", "surface_dev_mean_frac", "surface_dev_max_frac")}))

# =========================================================================== join + origin
LV, LF, FPART, FFID, FCH, off = [], [], [], [], [], 0
FPROX = []
fid_base = 0
for pi, pn in enumerate(PARTS):
    c = cut[pn]
    LV.append(c["V"]); LF.append(c["T"] + off); off += len(c["V"])
    FPART.append(np.full(len(c["T"]), pi)); FCH.append(c["ch"])
    FFID.append(np.where(c["fid"] >= 0, c["fid"] + fid_base, -1))
    FPROX.append(c["prox"])
    fid_base += len(c["prox"])
LV = np.vstack(LV); LF = np.vstack(LF); FPART = np.concatenate(FPART); FFID = np.concatenate(FFID); FCH = np.concatenate(FCH)
FPROX = np.vstack(FPROX)
lo, hi = LV.min(0), LV.max(0)
SHIFT = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
LV = LV - SHIFT
for pn in PARTS:
    src[pn]["V"] = src[pn]["V"] - SHIFT
report["origin_shift_sculpt_units"] = SHIFT.round(5).tolist()
size = LV.max(0) - LV.min(0)
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / size[2], CELL_MAX_FP / fp)
report["natural"] = {"height": round(float(size[2]), 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "units": "sculpt units (natural proportions, no refit)",
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(float(size[2] * k_fit), 4),
                                                     "bound_by": "height" if CELL_MAX_H / size[2] <= CELL_MAX_FP / fp else "footprint"},
                     "shipped_glb_height_m": 1.8}

# rest clearance between parts (BVH overlap + nearest vertex distance)
pmask = {pn: np.unique(LF[FPART == i].ravel()) for i, pn in enumerate(PARTS)}


def part_bvh(Vall, i):
    Ti = LF[FPART == i]
    return BVHTree.FromPolygons(Vall.tolist(), Ti.tolist(), all_triangles=True)


def clearance(Vall, pairs):
    B = {i: part_bvh(Vall, i) for i in range(len(PARTS))}
    out = {}
    for a, b in pairs:
        ov = len(B[a].overlap(B[b]))
        va = Vall[pmask[PARTS[a]]]
        vb = Vall[pmask[PARTS[b]]]
        da = min(B[b].find_nearest(Vector(p))[3] for p in va)
        db = min(B[a].find_nearest(Vector(p))[3] for p in vb)
        out[PARTS[a] + "|" + PARTS[b]] = {"overlap_tri_pairs": ov, "min_gap": round(float(min(da, db)), 4)}
    return out


PAIRS = [(0, 1), (0, 2), (0, 3), (0, 4), (2, 5), (2, 6), (0, 5), (0, 6), (3, 5), (4, 6)]
report["rest_clearance"] = clearance(LV, PAIRS)
print("CLEAR", json.dumps(report["rest_clearance"]))

# =========================================================================== 2d. electricity arcs (motion v2)
# Per gap: ARC_STRANDS strands between the two parts' facing surfaces (nearest vertex pair, strands spread across the
# plane the camera sees), each a jagged polyline swept by a triangular tube. ARC_VARIANTS shapes per strand share one
# vertex layout: variant 0 is the basis, the others become shape keys (the flicker swaps them on CONSTANT keys).
ARC_PI = len(PARTS)                                  # FPART index of the arc faces (not a rigid part)
PART_V = {pn: LV[pmask[pn]] for pn in PARTS}


def arc_frame(dn):
    ref = np.array([0.0, -1.0, 0.0]) if abs(dn[1]) < 0.9 else np.array([0.0, 0.0, 1.0])
    e1 = np.cross(dn, ref); e1 /= np.linalg.norm(e1)
    return e1, np.cross(dn, e1)


def arc_path(a, b, e1, e2, seed, side):
    """lightning, not a spring: irregular kink spacing, a Brownian-bridge wander (ends pinned) plus a small alternating
    zig so every vertex is a kink; paired strands bow apart (side = -1 / +1) so they read as two strands."""
    rng = np.random.default_rng(seed)
    M = ARC_SEGMENTS
    d = b - a
    L = float(np.linalg.norm(d))
    amp = min(ARC_JAG * L, ARC_JAG_MAX)
    seg = rng.uniform(0.55, 1.45, M)
    tk = np.concatenate([[0.0], np.cumsum(seg)]) / seg.sum()
    w = np.cumsum(np.vstack([np.zeros((1, 2)), rng.normal(0.0, 1.0, (M, 2))]), axis=0)
    w -= tk[:, None] * w[-1]
    w /= max(float(np.abs(w).max()), 1e-9)
    zig = np.array([0.0] + [(-1.0) ** k * rng.uniform(0.25, 0.55) for k in range(1, M)] + [0.0])
    off1 = amp * (0.75 * w[:, 0] + 0.45 * zig + side * ARC_BOW * np.sin(np.pi * tk))
    off2 = amp * 0.5 * w[:, 1]
    return a + tk[:, None] * d + off1[:, None] * e1 + off2[:, None] * e2


def arc_tube(P, e2):
    M = len(P) - 1
    out = []
    for k in range(M + 1):
        tng = P[min(k + 1, M)] - P[max(k - 1, 0)]; tng /= np.linalg.norm(tng)
        n1 = np.cross(tng, e2)
        if np.linalg.norm(n1) < 1e-6:
            n1 = np.cross(tng, np.array([0.0, 0.0, 1.0]))
        n1 /= np.linalg.norm(n1); n2 = np.cross(tng, n1)
        rr = ARC_RADIUS * (0.6 + 0.4 * math.sin(math.pi * k / M))
        for q in range(3):
            an = TAU * q / 3
            out.append(P[k] + rr * (math.cos(an) * n1 + math.sin(an) * n2))
    return np.array(out)


def arc_faces(base, M):
    F = [[base + 2, base + 1, base + 0]]                 # start cap (faces back along the strand)
    for k in range(M):
        for q in range(3):
            v00, v01 = base + 3 * k + q, base + 3 * k + (q + 1) % 3
            v10, v11 = base + 3 * (k + 1) + q, base + 3 * (k + 1) + (q + 1) % 3
            F += [[v00, v01, v11], [v00, v11, v10]]
    F.append([base + 3 * M, base + 3 * M + 1, base + 3 * M + 2])
    return F


ARC_VV = [[] for _ in range(ARC_VARIANTS)]          # per variant slot (0 = basis): arc vertex positions
ARC_F, ARC_W, ARC_GAP_OF_V = [], [], []             # faces (local idx), (partA, partB, s) per vertex, gap per vertex
arc_rep = []
nb_ = 0
for gi, gap in enumerate(ARC_GAPS):
    pa, pb = gap[0], gap[1]
    anchor_mode = gap[2] if len(gap) > 2 else None
    A, B = PART_V[pa], PART_V[pb]
    d2 = ((A[:, None, :] - B[None, :, :]) ** 2).sum(-1)
    if anchor_mode == "neck":
        d2 = d2 + ((A[:, None, :2] - B[None, :, :2]) ** 2).sum(-1) * ARC_NECK_PLUMB_W \
                + (A[:, None, 0] ** 2 + B[None, :, 0] ** 2) * ARC_NECK_CENTER_W
    elif anchor_mode == "shoulder":
        z_t = A[:, 2].min() + ARC_SHOULDER_FRAC * (A[:, 2].max() - A[:, 2].min())
        d2 = d2 + (((A[:, 2] - z_t) * ARC_SHOULDER_W) ** 2)[:, None]
    ia, ib = np.unravel_index(int(np.argmin(d2)), d2.shape)
    a0, b0 = A[ia], B[ib]
    dn0 = (b0 - a0) / np.linalg.norm(b0 - a0)
    e1g, _ = arc_frame(dn0)
    strands = []
    for j in range(ARC_STRANDS):
        off = (j - (ARC_STRANDS - 1) / 2.0) * ARC_SPREAD * e1g
        a = A[int(np.argmin(np.linalg.norm(A - (a0 + off), axis=1)))]
        b = B[int(np.argmin(np.linalg.norm(B - (b0 + off), axis=1)))]
        dn = (b - a) / np.linalg.norm(b - a)
        ae, be = a - dn * ARC_SINK, b + dn * ARC_SINK
        e1, e2 = arc_frame(dn)
        side = 0.0 if ARC_STRANDS == 1 else 2.0 * (j / (ARC_STRANDS - 1)) - 1.0
        shapes = [arc_tube(arc_path(ae, be, e1, e2, 7919 * gi + 131 * j + v, side), e2) for v in range(ARC_VARIANTS)]
        # latin assignment: slot k shows variant (gi + k) % V, so every swap changes every gap's shape
        for k in range(ARC_VARIANTS):
            ARC_VV[k].append(shapes[(gi + k) % ARC_VARIANTS])
        ARC_F.extend(arc_faces(nb_, ARC_SEGMENTS))
        for kk in range(ARC_SEGMENTS + 1):
            for q in range(3):
                ARC_W.append((pa, pb, kk / ARC_SEGMENTS)); ARC_GAP_OF_V.append(gi)
        nb_ += 3 * (ARC_SEGMENTS + 1)
        strands.append({"surface_gap": round(float(np.linalg.norm(b - a)), 4), "length_with_sink": round(float(np.linalg.norm(be - ae)), 4),
                        "jag_amp": round(min(ARC_JAG * float(np.linalg.norm(be - ae)), ARC_JAG_MAX), 4)})
    arc_rep.append({"gap": pa + "|" + pb, "anchor": anchor_mode, "nearest_gap": round(float(np.sqrt(d2[ia, ib])), 4),
                    "anchor_a": [round(float(v), 3) for v in a0], "anchor_b": [round(float(v), 3) for v in b0],
                    "strands": strands})
ARC_VV = [np.vstack(v) for v in ARC_VV]
ARC_F = np.array(ARC_F, dtype=np.int64)
n_arc_tris = len(ARC_F)
# closed-tube orientation gate: every strand's signed volume > 0 in every variant (outward faces)
_per = len(ARC_F) // (len(ARC_GAPS) * ARC_STRANDS)
arc_vol_min = min(signed_volume(Vv, ARC_F[s * _per:(s + 1) * _per]) for Vv in ARC_VV for s in range(len(ARC_GAPS) * ARC_STRANDS))
assert arc_vol_min > 0, "arc tube inside-out (signed volume %.3g)" % arc_vol_min
nmain_v = len(LV)
LV = np.vstack([LV, ARC_VV[0]])
LF = np.vstack([LF, ARC_F + nmain_v])
FPART = np.concatenate([FPART, np.full(n_arc_tris, ARC_PI)])
FFID = np.concatenate([FFID, np.full(n_arc_tris, -1)])
FCH = np.concatenate([FCH, np.zeros(n_arc_tris, dtype=FCH.dtype)])
ARC_VIDX = np.arange(nmain_v, len(LV))
report["arcs"] = {"gaps": arc_rep, "strands_per_gap": ARC_STRANDS, "segments": ARC_SEGMENTS, "variants": ARC_VARIANTS,
                  "tris": int(n_arc_tris), "verts": int(len(ARC_VIDX)), "radius": ARC_RADIUS, "jag": ARC_JAG, "jag_max": ARC_JAG_MAX, "bow": ARC_BOW,
                  "sink": ARC_SINK, "min_strand_signed_volume": round(float(arc_vol_min), 6), "region": ARC_REGION,
                  "flicker": "shape-key swap: variant 0 = basis, %d shape keys, CONSTANT keys at %.0f Hz" % (ARC_VARIANTS - 1, ARC_FLICKER_HZ)}
print("ARCS", json.dumps({k: report["arcs"][k] for k in ("tris", "verts", "min_strand_signed_volume")}))

# =========================================================================== mesh object
scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
me = bpy.data.meshes.new(UNIT)
me.from_pydata(LV.tolist(), [], LF.tolist())
me.update()
low = bpy.data.objects.new(UNIT, me)
scene.collection.objects.link(low)
me.shade_flat()
nf = len(me.polygons)
assert nf == len(LF)
report["tris_final"] = nf
report["tris_per_part"] = {pn: int((FPART == i).sum()) for i, pn in enumerate(PARTS)}
report["tris_per_part"]["arcs"] = int((FPART == ARC_PI).sum())

# UV: Smart UV on the joined faceted mesh + pack (the contract's uv_health; no bake at this density)
bpy.context.view_layer.objects.active = low
low.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003, area_weight=0.0, correct_aspect=True,
                         scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.003)
bpy.ops.object.mode_set(mode="OBJECT")
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn); UVn = UVn.reshape(-1, 3, 2)
uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV (66 deg, margin 0.003) + pack, whole joined faceted mesh", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()), "uv_sha": sha(UVn)}

# =========================================================================== 3. regions
REG = ["pavilion", "girdle", "crown", "edge", "inner", "eye", "core", "core_edge", ARC_REGION]
R_ = {n: i for i, n in enumerate(REG)}
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
rid = np.full(nf, R_["girdle"], dtype=np.int32)
fnz = np.where(FFID >= 0, FPROX[np.clip(FFID, 0, None), 2], 0.0)
rid[(FFID >= 0) & (fnz > CROWN_NZ)] = R_["crown"]
rid[(FFID >= 0) & (fnz < PAVILION_NZ)] = R_["pavilion"]
# inner facets: facet centroid sunk below its part's convex hull (direction-free concavity; the Vineweave eye rule)
inner_rep = {}
facet_depth = {}
for i, pn in enumerate(PARTS):
    Vp = LV[pmask[pn]]
    bmh = bmesh.new()
    for p in Vp:
        bmh.verts.new(p)
    hull = bmesh.ops.convex_hull(bmh, input=list(bmh.verts))
    junk = {id(g): g for g in hull["geom_interior"] + hull["geom_unused"] if isinstance(g, bmesh.types.BMVert)}
    bmesh.ops.delete(bmh, geom=list(junk.values()), context="VERTS")
    bvh_h = BVHTree.FromBMesh(bmh)
    bmh.free()
    diag = float(np.linalg.norm(Vp.max(0) - Vp.min(0)))
    fm = (FPART == i) & (FFID >= 0) & (FCH == 0)
    ids = np.unique(FFID[fm])
    n_in = 0
    for f_ in ids:
        sel = fm & (FFID == f_)
        c_ = FC[sel].mean(0)
        dpt = bvh_h.find_nearest(Vector(c_))[3] / diag
        facet_depth[int(f_)] = dpt
        if dpt > INNER_DEPTH:
            rid[sel] = R_["inner"]; n_in += 1
    inner_rep[pn] = {"facets": int(len(ids)), "inner_facets": n_in,
                     "depth_p95_frac": round(float(np.percentile([facet_depth[int(f)] for f in ids], 95)), 4) if len(ids) else 0}
report["inner_rule"] = {"rule": "facet centroid deeper than INNER_DEPTH x part diagonal below the part's convex hull",
                        "inner_depth": INNER_DEPTH, "per_part": inner_rep}
# eyes: the two facets of the head's eye row nearest the front (-Y)
hp = PARTS.index("head")
head_base = sum(len(cut[p]["prox"]) for p in PARTS[:hp])
eye_ids = [head_base + i for i in cut["head"]["eye_ids"]]
eye_f = np.isin(FFID, eye_ids) & (FCH == 0)
rid[eye_f] = R_["eye"]
# chamfers = edge planes / seams; the core gem is its own glow family
rid[(FCH == 1)] = R_["edge"]
cp = PARTS.index("core")
rid[(FPART == cp) & (FCH == 0)] = R_["core"]
rid[(FPART == cp) & (FCH == 1)] = R_["core_edge"]
rid[FPART == ARC_PI] = R_[ARC_REGION]
# per-facet value jitter (sparkle): one value per facet, so each facet reads as one plane
h = (np.sin((FFID + 7.0) * 12.9898) * 43758.5453) % 1.0
shade = np.where(FFID >= 0, 1.0 + FACET_JITTER * (h - 0.5), 1.0)
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")


def glow_tier(pal):
    """the palette's glow tiers are its emission_scale values (edge 0.22 < inner 0.75 < core 1.0 < eye 1.2 < core_edge
    1.4); 'top' = the arc region's scale is strictly the highest. (A luminance gate was tried first and measured wrong:
    it forced the violet arcs to scale 2.4, which clips to white under the Standard view transform.) Luminance of
    emission x scale is reported alongside for information."""
    tier, lum = {}, {}
    for n, v in pal["regions"].items():
        if "emission" in v:
            tier[n] = float(v.get("emission_scale", 1.0))
            g = PAL.srgb_to_linear(v["emission"]) * tier[n]
            lum[n] = round(float(0.2126 * g[0] + 0.7152 * g[1] + 0.0722 * g[2]), 4)
    rank = sorted(tier, key=lambda n: -tier[n])
    top = bool(rank) and rank[0] == ARC_REGION and all(tier[n] < tier[ARC_REGION] for n in rank[1:])
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "rank": rank, "arc_is_top_tier": top,
            "glow_luminance_info": lum, "gate": ARC_GLOW_TIER, "pass": bool(ARC_GLOW_TIER != "top" or top)}


report["arc_glow_tier"] = {"default": glow_tier(pal_default)}
assert report["arc_glow_tier"]["default"]["pass"], "arc glow tier gate: %r" % report["arc_glow_tier"]["default"]
report["regions_faces"] = PAL.paint(me, pal_default)
report["eye_rule"] = {"rule": "head gem-cut row %d: the two facets whose azimuth is nearest the front (-Y) each get a "
                              "sunk inset gem (EYE_SCALE of the facet, sunk EYE_DEPTH) = the eyes" % EYE_ROW[1],
                      "facet_ids": eye_ids, "faces": int(eye_f.sum()), **facet_stats["head"].get("eyes", {})}
anchor = FC[FPART == hp].mean(0)
landmark = FC[eye_f].mean(0)
dvec = landmark - anchor
report["facing"] = {"rule": "head faces centroid -> eye facets centroid", "anchor": anchor.round(4).tolist(),
                    "landmark": landmark.round(4).tolist(), "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2)}

# =========================================================================== material (Col -> base, Glow -> emission)
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
PAL.apply_material(mat, pal_default)
if "metallic" in pal_default["material"]:
    bsdf.inputs["Metallic"].default_value = pal_default["material"]["metallic"]
me.materials.append(mat)

LVf = np.array([v.co[:] for v in me.vertices])
low["conquest_unit"] = UNIT
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
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set) -> Emission Color; Col -> Base Color"
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 4. rig (one rigid bone per part)
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
W = LVf
vpart = np.zeros(len(W), dtype=np.int64)
for i, pn in enumerate(PARTS):
    vpart[pmask[pn]] = i
vpart[ARC_VIDX] = -1                                  # arc strands: blended between their two parts' bones (below)


def pv(pn):
    return W[vpart == PARTS.index(pn)]


def pca_axis(P):
    c = P.mean(0)
    ax = np.linalg.svd(P - c)[2][0]
    return c, ax / np.linalg.norm(ax)


BONES = []
tor = pv("torso"); tc = tor.mean(0)
BONES.append(("body", "root", (0.0, tc[1], tc[2]), (0.0, tc[1], tc[2] + 0.3 * (tor[:, 2].max() - tor[:, 2].min()))))
hd = pv("head")
BONES.append(("head", "body", (0.0, hd[:, 1].mean(), hd[:, 2].min()), (0.0, hd[:, 1].mean(), hd[:, 2].max())))
co_ = pv("core"); cc = co_.mean(0)
BONES.append(("core", "body", tuple(cc), (cc[0], cc[1], cc[2] + 0.5 * (co_[:, 2].max() - co_[:, 2].min()))))
for side in ("L", "R"):
    a = pv("arm." + side)
    c, ax = pca_axis(a)
    if ax[2] < 0:
        ax = -ax
    s = (a - c) @ ax
    top, bot = c + ax * s.max(), c + ax * s.min()
    BONES.append(("arm." + side, "body", tuple(top), tuple(bot)))
    lg = pv("leg." + side)
    tip = lg[int(np.argmin(lg[:, 2]))]
    c, ax = pca_axis(lg)
    if ax[2] < 0:
        ax = -ax
    s = (lg - c) @ ax
    # v2: legs are children of the BODY (static connection; the walk hinges them at the hip = the bone head)
    BONES.append(("leg." + side, "body", tuple(c + ax * s.max()), tuple(tip)))
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
H_top = float(W[:, 2].max())
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.12 * H_top); eb.use_deform = False
for (n, p, h, t) in BONES:
    e = arm_data.edit_bones.new(n)
    e.head = Vector(h); e.tail = Vector(t)
    e.parent = arm_data.edit_bones[p]
    e.use_deform = True; e.use_connect = False
    along = (Vector(t) - Vector(h)).normalized()
    e.align_roll(Vector((0.0, -1.0, 0.0)) if abs(along.y) < 0.9 else Vector((0.0, 0.0, 1.0)))
bpy.ops.object.mode_set(mode="OBJECT")
BONE_OF = {"torso": "body", "head": "head", "core": "core", "arm.L": "arm.L", "arm.R": "arm.R", "leg.L": "leg.L", "leg.R": "leg.R"}
low.vertex_groups.clear()
VG = {}
for pn in PARTS:
    vg = VG[pn] = low.vertex_groups.new(name=BONE_OF[pn])
    vg.add([int(i) for i in np.nonzero(vpart == PARTS.index(pn))[0]], 1.0, "REPLACE")
# arc strand vertex at fraction s along the strand: (1 - s) to part A's bone, s to part B's bone (2 influences, sum 1)
for vi, (pa, pb, s_) in zip(ARC_VIDX, ARC_W):
    if s_ < 1:
        VG[pa].add([int(vi)], 1.0 - s_, "REPLACE")
    if s_ > 0:
        VG[pb].add([int(vi)], s_, "REPLACE")
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig
rep["weights"] = {"rule": "rigid parts: every vertex 1.0 to its part's bone; arc strands: linear blend part A -> part B "
                          "along the strand (<= 2 influences, sum 1) so each arc stays wired to both ends",
                  "per_bone_verts": {BONE_OF[pn]: int((vpart == PARTS.index(pn)).sum()) for pn in PARTS},
                  "arc_verts": int(len(ARC_VIDX))}

# shape keys: arc variants (the flicker). Basis = variant slot 0; key k moves ONLY the arc vertices to slot k.
low.shape_key_add(name="Basis", from_mix=False)
ARC_KEYS = []
for k in range(1, ARC_VARIANTS):
    kb = low.shape_key_add(name="arc_flicker_%d" % k, from_mix=False)
    co = np.empty(len(me.vertices) * 3); kb.data.foreach_get("co", co); co = co.reshape(-1, 3)
    co[ARC_VIDX] = ARC_VV[k]
    kb.data.foreach_set("co", co.ravel())
    kb.value = 0.0
    ARC_KEYS.append(kb.name)
me.shape_keys.use_relative = True
rep["arc_shape_keys"] = ARC_KEYS

# =========================================================================== 5. motion v2: idle + walk
# The creature is ONE rigid assembly: head, arms and core are children of the body and are keyed at identity (static);
# the legs are children of the body and only HINGE about their own head (the hip) in the walk. The body's transform is
# a rotation about the ground point between the feet (rock / lean / sway there leaves the feet in place) plus a
# vertical offset SOLVED each frame so the lowest leg point (soft-min over both legs) lands at the float height.
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: b.matrix_local.to_3x3() for b in arm_data.bones}
HEAD = {b.name: np.array(b.head_local) for b in arm_data.bones}
KEYED = ["body", "head", "core", "arm.L", "arm.R", "leg.L", "leg.R"]   # every deform bone: clips are self-contained
LEG_I = {s: np.nonzero(vpart == PARTS.index("leg." + s))[0] for s in ("L", "R")}
LEG_TIP = {s: int(LEG_I[s][np.argmin(W[LEG_I[s], 2])]) for s in ("L", "R")}
GROUND_PIVOT = np.array([0.0, float(np.mean([W[LEG_TIP[s], 1] for s in ("L", "R")])), 0.0])
GAME_M_PER_UNIT = 1.8 / report["natural"]["height"]


def rotm(axis, deg):
    return np.array(Matrix.Rotation(math.radians(deg), 3, Vector(axis)))


def hinge(R, pivot):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = pivot - R @ pivot
    return M


def apply_m(M, P):
    return P @ M[:3, :3].T + M[:3, 3]


def softmin(a, b, tau):
    m = min(a, b)
    if tau <= 0:
        return m
    return m - tau * math.log(math.exp(-(a - m) / tau) + math.exp(-(b - m) / tau))


def solve_body(Rb, legR, hover, tau):
    """body deform matrix: hinge(Rb, ground pivot), lifted so the soft-min of the two legs' lowest points = hover."""
    Mb = hinge(Rb, GROUND_PIVOT)
    lows = [float(apply_m(Mb @ hinge(legR[s], HEAD["leg." + s]), W[LEG_I[s]])[:, 2].min()) for s in ("L", "R")]
    Mb[2, 3] += hover - softmin(lows[0], lows[1], tau)
    return Mb


def pose_from_deform(bn, M):
    """armature-space deform matrix (rigid, about nothing in particular) -> pose-bone (location, quaternion)."""
    R = M[:3, :3]; h = HEAD[bn]
    dloc = M[:3, 3] - h + R @ h
    R3 = REST[bn]
    q = (R3.inverted() @ Matrix(R.tolist()) @ R3).to_quaternion()
    return R3.inverted() @ Vector(dloc), q


def rel_pose(bn, R):
    """a child hinged about its own head by the armature-space rotation R (relative to its parent)."""
    R3 = REST[bn]
    return Vector((0, 0, 0)), (R3.inverted() @ Matrix(R.tolist()) @ R3).to_quaternion()


def assemble(Mb, legR, core_s):
    P = {"body": pose_from_deform("body", Mb) + ((1.0, 1.0, 1.0),)}
    for n in ("head", "arm.L", "arm.R"):
        P[n] = (Vector((0, 0, 0)), Quaternion(), (1.0, 1.0, 1.0))
    for s in ("L", "R"):
        P["leg." + s] = rel_pose("leg." + s, legR[s]) + ((1.0, 1.0, 1.0),)
    P["core"] = (Vector((0, 0, 0)), Quaternion(), (core_s, core_s, core_s))
    return P


def idle_pose(t):
    """whole-body float (up only, lands softly at t = 0), fore/aft sway about the foot line, core beat x2."""
    hover = IDLE_FLOAT * (0.5 - 0.5 * math.cos(TAU * t))
    Rb = rotm((1, 0, 0), IDLE_SWAY_DEG * math.sin(TAU * t))
    legR = {"L": np.eye(3), "R": np.eye(3)}
    b = 0.5 - 0.5 * math.cos(TAU * 2 * t)
    return assemble(solve_body(Rb, legR, hover, 0.0), legR, 1.0 - IDLE_CORE_PULSE * b), 1.0 + IDLE_GLOW_PULSE * b


def walk_pose(t):
    """stiff biped: legs pitch about the hip in antiphase (left foot front at t = 0), body rolls over the stance leg
    (lifts the swing foot), shoulders counter-twist, constant lean, float at mid-stance, soft-min contacts."""
    uL = math.cos(TAU * t)                                   # +1 = left foot at the front (-Y), -1 = at the back
    legR = {"L": rotm((1, 0, 0), -WALK_LEG_SWING_DEG * uL), "R": rotm((1, 0, 0), WALK_LEG_SWING_DEG * uL)}
    Rb = rotm((0, 0, 1), WALK_TORSO_YAW_DEG * uL) @ rotm((1, 0, 0), WALK_LEAN_DEG) @ rotm((0, 1, 0), WALK_ROLL_DEG * math.sin(TAU * t))
    hover = WALK_FLOAT * (0.5 - 0.5 * math.cos(TAU * 2 * t))    # 0 at each footfall, peak at mid-stance
    b = 0.5 + 0.5 * math.cos(TAU * 2 * t)                       # footfall beat: peaks at t = 0 and 0.5
    return assemble(solve_body(Rb, legR, hover, WALK_CONTACT_SOFT), legR, 1.0 - WALK_CORE_PULSE * b), 1.0 + WALK_GLOW_PULSE * b


sock = bsdf.inputs["Emission Strength"]
base_es = sock.default_value
KEY = me.shape_keys


def author(name, frames, pose_fn, seed):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    glow = []
    for f in range(1, frames + 2):
        t = ((f - 1) / frames) % 1.0
        P, g = pose_fn(t)
        glow.append(g)
        for n in KEYED:
            loc, q, sc = P[n]
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pose[n].location = loc; pose[n].rotation_quaternion = q
            pose[n].keyframe_insert("location", frame=f, group=n)
            pose[n].keyframe_insert("rotation_quaternion", frame=f, group=n)
            if n == "core":
                pose[n].scale = sc
                pose[n].keyframe_insert("scale", frame=f, group=n)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, frames + 1
    act.use_cyclic = True
    out = {}
    # glow flare: emission strength on the material node-tree slot of the SAME action (the v1 / Mycothrall pattern)
    try:
        K.assign_action(nt, act)
        for f, g in enumerate(glow, start=1):
            sock.default_value = base_es * g
            sock.keyframe_insert("default_value", frame=f)
        out["glow"] = {"socket": "Principled BSDF.Emission Strength", "base": base_es,
                       "range": [round(base_es * min(glow), 4), round(base_es * max(glow), 4)]}
    except Exception as exc:  # noqa: BLE001
        out["glow"] = {"error": repr(exc)}
    nt.animation_data.action = None
    sock.default_value = base_es
    # arc flicker: shape-key swaps on the Key slot of the SAME action, CONSTANT keys (electric snaps, no morphing)
    step = max(1, int(round(K.FPS / ARC_FLICKER_HZ)))
    assert frames % step == 0, "flicker step %d does not divide the %d-frame loop" % (step, frames)
    nsteps = frames // step
    rng = np.random.default_rng(seed)
    st = [0]
    for _ in range(1, nsteps):
        st.append((st[-1] + 1 + int(rng.integers(0, max(ARC_VARIANTS - 1, 1)))) % ARC_VARIANTS)
    if nsteps > 2 and ARC_VARIANTS > 2 and st[-1] == st[0]:
        st[-1] = next(v for v in range(ARC_VARIANTS) if v not in (st[0], st[-2]))
    if ARC_KEYS:
        K.assign_action(KEY, act)
        for i in range(nsteps + 1):
            state = st[i % nsteps]
            for k, kn in enumerate(ARC_KEYS, start=1):
                kb = KEY.key_blocks[kn]
                kb.value = 1.0 if state == k else 0.0
                kb.keyframe_insert("value", frame=1 + i * step)
        nconst = 0
        for fc in K.action_fcurves(act):
            if fc.data_path.startswith("key_blocks"):
                for kp in fc.keyframe_points:
                    kp.interpolation = "CONSTANT"; nconst += 1
        KEY.animation_data.action = None
        for kn in ARC_KEYS:
            KEY.key_blocks[kn].value = 0.0
        out["flicker"] = {"method": "shape-key swap (CONSTANT)", "step_frames": step, "hz": round(K.FPS / step, 3),
                          "states": st, "keys_constant": nconst}
    out["slots"] = [s_.identifier for s_ in act.slots]
    return act, out


def deform_m(n):
    pb = pose[n]
    return np.array(pb.matrix) @ np.array(pb.bone.matrix_local.inverted())


def coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


def measure(act, frames):
    K.assign_action(rig, act)
    K.assign_action(KEY, act)
    first = last = None
    min_z = 1e9
    stretch = 0.0
    core_s = [9.0, 0.0]
    static_res = {"head": 0.0, "arm.L": 0.0, "arm.R": 0.0}
    pivot_res = {"leg.L": 0.0, "leg.R": 0.0, "core": 0.0}
    worst = {}
    tips, lows, body_z = [], [], []
    arc_moves = 0.0
    prev_arc = None
    ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
    for f in range(1, frames + 2):
        scene.frame_set(f)
        C = coords()
        if f == 1:
            first = C
        if f == frames + 1:
            last = C
        min_z = min(min_z, float(C[:, 2].min()))
        ext_lo = np.minimum(ext_lo, C.min(0)); ext_hi = np.maximum(ext_hi, C.max(0))
        tips.append([C[LEG_TIP[s]].copy() for s in ("L", "R")])
        lows.append([float(C[LEG_I[s], 2].min()) for s in ("L", "R")])
        Mb = deform_m("body")
        body_z.append(float(apply_m(Mb, HEAD["body"][None])[0, 2]))
        for n in static_res:                            # static parts: their verts ride the body's transform exactly
            Pn = W[vpart == PARTS.index(n)]
            static_res[n] = max(static_res[n], float(np.linalg.norm(apply_m(deform_m(n), Pn) - apply_m(Mb, Pn), axis=1).max()))
        for n in pivot_res:                             # hinged / pulsing parts: their pivot rides the body exactly
            h_ = HEAD[n][None]
            pivot_res[n] = max(pivot_res[n], float(np.linalg.norm(apply_m(deform_m(n), h_) - apply_m(Mb, h_))))
        for pb in pose:
            if pb.name != "core":
                stretch = max(stretch, abs(pb.length - pb.bone.length) / pb.bone.length)
            else:
                core_s = [min(core_s[0], pb.scale[0]), max(core_s[1], pb.scale[0])]
        A_ = C[ARC_VIDX]
        if prev_arc is not None:
            arc_moves = max(arc_moves, float(np.linalg.norm(A_ - prev_arc, axis=1).max()))
        prev_arc = A_
        if (f - 1) % 4 == 0:
            for k_, v_ in clearance(C, PAIRS).items():
                w_ = worst.setdefault(k_, {"overlap_tri_pairs_max": 0, "min_gap": 1e9, "min_gap_frame": None})
                w_["overlap_tri_pairs_max"] = max(w_["overlap_tri_pairs_max"], v_["overlap_tri_pairs"])
                if v_["min_gap"] < w_["min_gap"]:
                    w_["min_gap"], w_["min_gap_frame"] = v_["min_gap"], f
    rig.animation_data.action = None
    KEY.animation_data.action = None
    for kn in ARC_KEYS:
        KEY.key_blocks[kn].value = 0.0
    tips = np.array(tips); lows = np.array(lows)
    return {"frames": frames + 1, "cycle_frames": frames, "cycle_s": round(frames / K.FPS, 4),
            "seam_first_last_max_mm_sculpt": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
            "seam_measured_with": "rig + shape-key (arc flicker) slots bound",
            "min_z": round(min_z, 6), "bone_stretch_max_pct_excl_core": round(stretch * 100, 6),
            "core_scale_range": [round(core_s[0], 5), round(core_s[1], 5)],
            "static_parts_max_drift_vs_body": {k: round(v, 7) for k, v in static_res.items()},
            "pivot_max_drift_vs_body": {k: round(v, 7) for k, v in pivot_res.items()},
            "body_head_z_range": [round(min(body_z), 4), round(max(body_z), 4)],
            "arc_max_vertex_jump_per_frame": round(arc_moves, 4),
            "clearance_over_loop_every_4th_frame": worst,
            "extent": {"width": round(float(ext_hi[0] - ext_lo[0]), 4), "depth": round(float(ext_hi[1] - ext_lo[1]), 4),
                       "height": round(float(ext_hi[2] - ext_lo[2]), 4)}}, tips, lows


t_anim = time.time()
act_idle, idle_extra = author("idle", IDLE_FRAMES, idle_pose, 17)
act_walk, walk_extra = author("walk", WALK_FRAMES, walk_pose, 29)
m_idle, _, lows_i = measure(act_idle, IDLE_FRAMES)
m_walk, tips_w, lows_w = measure(act_walk, WALK_FRAMES)
# walk: foot travel (in place: the stance foot slides back = the ground passing under), contacts, swing lift
Nw = WALK_FRAMES
half = Nw // 2
gait = {}
for si, s in enumerate(("L", "R")):
    st0 = 0 if s == "L" else half                       # left stance t in [0, .5), right stance t in [.5, 1)
    stance = [(st0 + k) % Nw for k in range(half + 1)]
    swing = [(st0 + half + k) % Nw for k in range(1, half)]
    y = tips_w[:, si, 1]
    gait[s] = {"tip_y_at_touchdown": round(float(y[stance[0]]), 4), "tip_y_at_liftoff": round(float(y[stance[-1]]), 4),
               "step_length": round(float(y[stance[-1]] - y[stance[0]]), 4),
               "stance_low_z_max": round(float(lows_w[stance, si].max()), 4),
               "swing_low_z_max": round(float(lows_w[swing, si].max()), 4),
               "swing_low_z_min": round(float(lows_w[swing, si].min()), 4)}
step_len = float(np.mean([gait[s]["step_length"] for s in gait]))
speed_units = 2.0 * step_len / (Nw / K.FPS)
assert step_len > 0, "walk runs backwards (stance foot must slide toward +Y, the character faces -Y)"
m_walk.update({"gait": gait, "step_length_units": round(step_len, 4), "stride_units": round(2 * step_len, 4),
               "cadence_steps_per_min": round(2 * 60.0 * K.FPS / Nw, 2),
               "implied_ground_speed": {"units_per_s": round(speed_units, 4), "m_per_s_at_game_scale": round(speed_units * GAME_M_PER_UNIT, 4),
                                        "game_m_per_unit": round(GAME_M_PER_UNIT, 6),
                                        "rule": "stride (2 x mean stance-foot travel) / cycle time"},
               "lowest_point_each_frame_min_max": [round(float(lows_w.min(1).min()), 5), round(float(lows_w.min(1).max()), 5)],
               "motion": {"leg_swing_deg": WALK_LEG_SWING_DEG, "roll_deg": WALK_ROLL_DEG, "torso_yaw_deg": WALK_TORSO_YAW_DEG,
                          "lean_deg": WALK_LEAN_DEG, "float": WALK_FLOAT, "contact_soft": WALK_CONTACT_SOFT,
                          "core_pulse": WALK_CORE_PULSE, "glow_pulse": WALK_GLOW_PULSE, "harmonics": [1, 2]}, **walk_extra})
m_idle.update({"lowest_point_each_frame_min_max": [round(float(lows_i.min(1).min()), 5), round(float(lows_i.min(1).max()), 5)],
               "motion": {"float": IDLE_FLOAT, "sway_deg": IDLE_SWAY_DEG, "core_pulse": IDLE_CORE_PULSE,
                          "glow_pulse": IDLE_GLOW_PULSE, "harmonics": [1, 2]}, **idle_extra})
rep["idle"] = {"status": "v2 PROPOSED (artist 2026-09-25: static limbs, electricity, float-ish)", "action": "idle", **m_idle}
rep["walk"] = {"status": "v2 PROPOSED (artist 2026-09-25: walks pretty normally, can floatish)", "action": "walk", **m_walk}
rep["anim_seconds"] = round(time.time() - t_anim, 2)
K.assign_action(rig, None)
scene.frame_set(1)
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_start, scene.frame_end = 1, IDLE_FRAMES + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local],
                 "length": round(b.length, 4)} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rig["conquest_rig"] = "rigid-parts v2 (one bone per part; head/arms/core static on the body, legs hinge at the hip)"
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = "idle v2 + walk PROPOSED (motion v2, 2026-09-25); arcs flicker via shape keys; no attacks"
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True)

# =========================================================================== skins (palette swap)
ARC_FACES = np.nonzero(FPART == ARC_PI)[0]


def arc_paint_sample():
    """mean linear Col / Glow over the arc faces' corners, read back from the painted mesh (repaint proof)."""
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
    out = {}
    for nm in ("Col", "Glow"):
        cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes[nm].data.foreach_get("color", cd)
        cd = cd.reshape(-1, 4)
        out[nm] = [round(float(v), 4) for v in cd[ls[ARC_FACES]].mean(0)[:3]]
    return out


rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "palette_files": pal_default["files"],
                            "arc_glow_tier": report["arc_glow_tier"]["default"], "arc_paint_linear": arc_paint_sample()}}
for skin in SKINS:
    if skin == "default":
        continue
    pal = PAL.load(UNIT, skin)
    tier = glow_tier(pal)
    assert tier["pass"], "arc glow tier gate failed for skin %s: %r" % (skin, tier)
    counts = PAL.paint(me, pal)
    PAL.apply_material(mat, pal)
    out = OUT_RIGGED[:-6] + "__" + skin + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    _cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
    rep["skins"][skin] = {"file": out, "palette": PAL.table(pal), "palette_files": pal["files"],
                          "col_sha": hashlib.sha256(np.round(_cd, 5).tobytes()).hexdigest()[:16], "regions_faces": counts,
                          "arc_glow_tier": tier, "arc_paint_linear": arc_paint_sample()}
PAL.paint(me, pal_default); PAL.apply_material(mat, pal_default)
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "seconds")}))
print("IDLE", json.dumps({k: v for k, v in rep["idle"].items() if k != "clearance_over_loop_every_4th_frame"}))
print("WALK", json.dumps({k: v for k, v in rep["walk"].items() if k != "clearance_over_loop_every_4th_frame"}))
print("SKINS_ARC", json.dumps({k: {"arc_paint_linear": v.get("arc_paint_linear"), "arc_top": v.get("arc_glow_tier", {}).get("arc_is_top_tier")}
                               for k, v in rep["skins"].items()}))
sys.stdout.flush()
os._exit(0)
