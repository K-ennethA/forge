"""Geode (hero) build: winding fix + CRYSTAL FACETING + emerald glow palette + part rig + PROPOSED idle, one run.

    blender --background source-copies/hero-gem_knight.blend --factory-startup --python improve/geode_build.py -- \
        [--skins default,amethyst] [--set CONST=value ...]

Outputs (the opened source copy is never saved over; save_as_mainfile copy=True):
    improved/geode.blend + .json      faceted hero mesh, UVs, palette regions + default paint (no rig)
    rigged/geode.blend + .json        + one rigid bone per floating part, PROPOSED 'idle' (no walk: gait unknown)
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
  4. rig: root + one bone per rigid part, rigid weights (1.0, one influence).
  5. PROPOSED idle: floating parts hover / orbit out of phase, core contracts + flares; legs never keyed.
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
IDLE_FRAMES = 120                                   # idle loop length (24 fps -> 5 s)
IDLE_BODY_BOB = 0.10                                # torso float (sculpt units, model is ~24.6 tall)
IDLE_BODY_TILT_DEG = 0.8                            # torso sway
IDLE_HEAD_BOB = 0.07                                # head float relative to the torso (gap at rest ~0.2)
IDLE_HEAD_TILT_DEG = 2.0                            # head nod / look
IDLE_ARM_ORBIT = 0.10                               # arm orbit radius (x/y circle) relative to the torso
IDLE_ARM_BOB = 0.12                                 # arm float relative to the torso
IDLE_ARM_SWING_DEG = 2.5                            # arm pendulum swing about the shoulder end
IDLE_ARM_PHASE = 0.55 * math.pi                     # right arm lags the left by this (rad): out of phase
IDLE_CORE_PULSE = 0.07                              # core contraction (scale 1 -> 1 - this; never grows past rest)
IDLE_CORE_SINK = 0.06                               # core sinks by up to this while contracting (never rises past rest)
IDLE_CORE_YAW_DEG = 10.0                            # core slow turn wobble
IDLE_GLOW_PULSE = 0.45                              # glow flare on each core beat (emission strength x (1 + this))

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
REG = ["pavilion", "girdle", "crown", "edge", "inner", "eye", "core", "core_edge"]
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
# per-facet value jitter (sparkle): one value per facet, so each facet reads as one plane
h = (np.sin((FFID + 7.0) * 12.9898) * 43758.5453) % 1.0
shade = np.where(FFID >= 0, 1.0 + FACET_JITTER * (h - 0.5), 1.0)
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
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
    BONES.append(("leg." + side, "root", tuple(c + ax * s.max()), tuple(tip)))
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
for pn in PARTS:
    vg = low.vertex_groups.new(name=BONE_OF[pn])
    vg.add([int(i) for i in np.nonzero(vpart == PARTS.index(pn))[0]], 1.0, "REPLACE")
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig
rep["weights"] = {"rule": "rigid: every vertex 1.0 to its part's bone (one influence, no blending)",
                  "per_bone_verts": {BONE_OF[pn]: int((vpart == PARTS.index(pn)).sum()) for pn in PARTS}}

# =========================================================================== 5. PROPOSED idle
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
N = IDLE_FRAMES
REST = {b.name: b.matrix_local.to_3x3() for b in arm_data.bones}


def loc_local(bn, off):
    """armature-space offset -> the bone's own rest frame (pose location is expressed there)."""
    return REST[bn].inverted() @ Vector(off)


def rot_local(bn, axis, deg):
    q = Quaternion(Vector(axis), math.radians(deg))
    R3 = REST[bn]
    return (R3.inverted() @ q.to_matrix() @ R3).to_quaternion()


def sn(t, k=1, ph=0.0):
    return math.sin(TAU * k * t + ph)


def beat(t):
    """0 at rest, 1 at the peak; two beats per loop, smooth (integer harmonic -> seam-closed)."""
    return 0.5 - 0.5 * math.cos(TAU * 2 * t)


def idle_pose(t):
    P = {}
    P["body"] = (loc_local("body", (0, 0, IDLE_BODY_BOB * sn(t))),
                 rot_local("body", (1, 0, 0), IDLE_BODY_TILT_DEG * sn(t, 1, 0.9)) @ rot_local("body", (0, 1, 0), 0.6 * IDLE_BODY_TILT_DEG * sn(t, 1, 2.2)),
                 None)
    P["head"] = (loc_local("head", (0, 0, IDLE_HEAD_BOB * sn(t, 1, -0.9))),
                 rot_local("head", (1, 0, 0), IDLE_HEAD_TILT_DEG * sn(t, 1, -1.3)) @ rot_local("head", (0, 0, 1), 1.6 * IDLE_HEAD_TILT_DEG * sn(t, 1, 0.5)),
                 None)
    for side, sg, ph in (("L", 1.0, 0.0), ("R", -1.0, IDLE_ARM_PHASE)):
        off = (sg * IDLE_ARM_ORBIT * (math.cos(TAU * t + ph) - 1.0) * 0.5, IDLE_ARM_ORBIT * sn(t, 1, ph), IDLE_ARM_BOB * sn(t, 1, ph - 0.6))
        P["arm." + side] = (loc_local("arm." + side, off), rot_local("arm." + side, (0, 1, 0), -sg * IDLE_ARM_SWING_DEG * sn(t, 1, ph + 0.4)), None)
    b = beat(t)
    s = 1.0 - IDLE_CORE_PULSE * b
    P["core"] = (loc_local("core", (0, 0, -IDLE_CORE_SINK * b)), rot_local("core", (0, 0, 1), IDLE_CORE_YAW_DEG * sn(t)), (s, s, s))
    return P


act = bpy.data.actions.new("idle")
act.use_fake_user = True
K.assign_action(rig, act)
KEYED = ["body", "head", "arm.L", "arm.R", "core"]
prevq = {}
for f in range(1, N + 2):
    t = ((f - 1) / N) % 1.0
    P = idle_pose(t)
    for n in KEYED:
        loc, q, sc = P[n]
        if n in prevq and prevq[n].dot(q) < 0:
            q.negate()
        prevq[n] = q.copy()
        pose[n].location = loc; pose[n].rotation_quaternion = q
        pose[n].keyframe_insert("location", frame=f, group=n)
        pose[n].keyframe_insert("rotation_quaternion", frame=f, group=n)
        if sc is not None:
            pose[n].scale = sc
            pose[n].keyframe_insert("scale", frame=f, group=n)
act.use_frame_range = True
act.frame_start, act.frame_end = 1, N + 1
act.use_cyclic = True
# glow flare: emission strength keyed into the SAME 'idle' action on a node-tree slot (the Mycothrall pattern)
sock = bsdf.inputs["Emission Strength"]
base_es = sock.default_value
glow_rep = {}
try:
    K.assign_action(nt, act)
    for f in range(1, N + 2):
        t = ((f - 1) / N) % 1.0
        sock.default_value = base_es * (1.0 + IDLE_GLOW_PULSE * beat(t))
        sock.keyframe_insert("default_value", frame=f)
    glow_rep = {"socket": "Principled BSDF.Emission Strength", "base": base_es,
                "law": "base * (1 + %.2f * (0.5 - 0.5 cos(2 pi 2t)))  (in step with the core contraction)" % IDLE_GLOW_PULSE,
                "slots": [s_.identifier for s_ in act.slots]}
except Exception as exc:  # noqa: BLE001
    glow_rep = {"error": repr(exc)}
nt.animation_data.action = None
sock.default_value = base_es

# ---- measure over the loop
legs = np.nonzero(np.isin(vpart, [PARTS.index("leg.L"), PARTS.index("leg.R")]))[0]


def coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


first = last = None
core_s = (9.0, 0.0)
leg_move = 0.0
min_z = 1e9
stretch = 0.0
worst = {}
travel = {pn: 0.0 for pn in PARTS}
ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
for f in range(1, N + 2):
    scene.frame_set(f)
    C = coords()
    if f == 1:
        first = C
    if f == N + 1:
        last = C
    leg_move = max(leg_move, float(np.linalg.norm(C[legs] - W[legs], axis=1).max()))
    min_z = min(min_z, float(C[:, 2].min()))
    ext_lo = np.minimum(ext_lo, C.min(0)); ext_hi = np.maximum(ext_hi, C.max(0))
    for i, pn in enumerate(PARTS):
        travel[pn] = max(travel[pn], float(np.linalg.norm(C[vpart == i] - W[vpart == i], axis=1).max()))
    for pb in pose:
        if pb.name != "core":                  # the core's scale IS the pulse (reported separately)
            stretch = max(stretch, abs(pb.length - pb.bone.length) / pb.bone.length)
        else:
            core_s = (min(core_s[0], pb.scale[0]), max(core_s[1], pb.scale[0]))
    if (f - 1) % 4 == 0:
        for k_, v_ in clearance(C, PAIRS).items():
            w_ = worst.setdefault(k_, {"overlap_tri_pairs_max": 0, "min_gap": 1e9, "min_gap_frame": None})
            w_["overlap_tri_pairs_max"] = max(w_["overlap_tri_pairs_max"], v_["overlap_tri_pairs"])
            if v_["min_gap"] < w_["min_gap"]:
                w_["min_gap"], w_["min_gap_frame"] = v_["min_gap"], f
rep["idle"] = {"status": "PROPOSED - the artist judges", "action": "idle", "frames": N + 1, "cycle_frames": N,
               "cycle_s": round(N / K.FPS, 3), "keyed_bones": KEYED, "never_keyed": ["root", "leg.L", "leg.R"],
               "seam_residual_mm_sculpt": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
               "legs_move_max": round(leg_move, 9), "min_z": round(min_z, 6), "bone_stretch_max_pct_excl_core": round(stretch * 100, 6),
               "core_scale_range": [round(core_s[0], 5), round(core_s[1], 5)],
               "part_travel_max": {k: round(v, 4) for k, v in travel.items()},
               "clearance_over_loop_every_4th_frame": worst,
               "extent": {"width": round(float(ext_hi[0] - ext_lo[0]), 4), "depth": round(float(ext_hi[1] - ext_lo[1]), 4),
                          "height": round(float(ext_hi[2] - ext_lo[2]), 4)},
               "glow_pulse": glow_rep,
               "motion": {"body_bob": IDLE_BODY_BOB, "body_tilt_deg": IDLE_BODY_TILT_DEG, "head_bob": IDLE_HEAD_BOB,
                          "head_tilt_deg": IDLE_HEAD_TILT_DEG, "arm_orbit": IDLE_ARM_ORBIT, "arm_bob": IDLE_ARM_BOB,
                          "arm_swing_deg": IDLE_ARM_SWING_DEG, "arm_phase_rad": round(IDLE_ARM_PHASE, 4),
                          "core_pulse": IDLE_CORE_PULSE, "core_sink": IDLE_CORE_SINK, "core_yaw_deg": IDLE_CORE_YAW_DEG,
                          "glow_pulse": IDLE_GLOW_PULSE, "harmonics": [1, 2]}}
rig.animation_data.action = None
scene.frame_set(1)
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_start, scene.frame_end = 1, N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local],
                 "length": round(b.length, 4)} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rig["conquest_rig"] = "rigid-parts v1 (one bone per floating part)"
low["conquest_clips"] = ["idle"]
low["conquest_clip_status"] = "idle PROPOSED; walk NOT AUTHORED (gait pending artist); no attacks"
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True)

# =========================================================================== skins (palette swap)
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "palette_files": pal_default["files"]}}
for skin in SKINS:
    if skin == "default":
        continue
    pal = PAL.load(UNIT, skin)
    counts = PAL.paint(me, pal)
    PAL.apply_material(mat, pal)
    out = OUT_RIGGED[:-6] + "__" + skin + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    _cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
    rep["skins"][skin] = {"file": out, "palette": PAL.table(pal), "palette_files": pal["files"],
                          "col_sha": hashlib.sha256(np.round(_cd, 5).tobytes()).hexdigest()[:16], "regions_faces": counts}
PAL.paint(me, pal_default); PAL.apply_material(mat, pal_default)
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "seconds")}))
print("IDLE", json.dumps(rep["idle"]))
sys.stdout.flush()
os._exit(0)
