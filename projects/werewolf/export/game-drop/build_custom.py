"""Custom protagonist on the MPFB2 base (draft mode, docs/lane-conventions.md "Two speeds").

Builds, end to end and headless:
  * the approved MPFB2 base body (pilot macros, 1.8825 m) + likeness face targets toward
    design/refs/form-a-front.png,
  * CC0 MakeHuman system assets: textured skin, low-poly eyes, card eyebrows/eyelashes, hair,
  * forge-modelled garments written as real MPFB clothes (MHCLO + OBJ via ClothesService's
    MakeClothes matching, with delete groups) and fitted back through HumanService.add_mhclo_asset:
      - short zip-front leather jacket (open collar, waist-length hem, cuffs, zip strips, stitching),
      - straight-leg jeans (waistband, fly, pockets, in/outseams, hem stitching),
  * low-poly CC0 ankle boots, a CC0 tee, palette colours from palette.json,
  * garment poke-through checks in the A-pose + tri breakdown + UV metrics -> report json.

build : blender --background --factory-startup --python build_custom.py -- build <out.blend> <report.json>
render: blender --background --factory-startup <out.blend> --python build_custom.py -- render <prefix> [stills|turntable|all]
"""
import bpy, bmesh, sys, os, json, math, heapq, uuid, addon_utils
from mathutils import Vector
from mathutils import noise as mnoise
from mathutils.bvhtree import BVHTree

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERE = os.path.dirname(os.path.abspath(__file__))
FORGE = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

TARGET_H = 1.8825
MACRO = {"gender": 1.0, "age": 0.5, "muscle": 0.68, "weight": 0.48, "proportions": 0.75, "height": 0.6}
RACE = {"caucasian": 0.75, "asian": 0.05, "african": 0.2}

# Likeness toward form-a-front.png: long rectangular face, squared jaw, high cheekbones over hollow
# cheeks, low heavy brooding brow, narrowed lids, long straight narrow nose, thin lips, strong neck.
LIKENESS = {
    "head/head-rectangular": 0.35,
    "chin/chin-width-incr": 0.2, "chin/chin-prominent-incr": 0.3, "chin/chin-height-incr": 0.2,
    "chin/chin-bones-incr": 0.3,
    "cheek/l-cheek-bones-incr": 0.35, "cheek/r-cheek-bones-incr": 0.35,
    "cheek/l-cheek-volume-decr": 0.4, "cheek/r-cheek-volume-decr": 0.4,
    "eyebrows/eyebrows-trans-down": 0.3, "eyebrows/eyebrows-angle-down": 0.35,
    "eyebrows/eyebrows-trans-forward": 0.3,
    "eyes/l-eye-height2-decr": 0.3, "eyes/r-eye-height2-decr": 0.3,
    "nose/nose-scale-vert-incr": 0.2, "nose/nose-width1-decr": 0.2, "nose/nose-point-width-decr": 0.3,
    "nose/nose-hump-decr": 0.2,
    "mouth/mouth-upperlip-volume-decr": 0.3, "mouth/mouth-lowerlip-volume-decr": 0.2,
    "mouth/mouth-angles-down": 0.15,
    "neck/measure-neck-circ-incr": 0.2,
}

SKIN = "skins/toigo_light_skin_male_bronze/toigo_light_skin_male_bronze.mhmat"   # warmest CC0 male skin, ref skin lit tone 191,144,126
BODYPARTS = [("eyes/low-poly", "Eyes"), ("eyebrows/eyebrow001", "Eyebrows"),
             ("eyelashes/eyelashes01", "Eyelashes"),
             # hair = dark cap (short04) under messy alpha cards (cortu_short_messy_hair): the closest
             # CC0 read of the reference's dark tousled fringe (compared against culturalibre_hair_05,
             # short02, short03, faydaen_hair_1 in face closeups)
             ("hair/short04", "Hair"), ("hair/cortu_short_messy_hair", "Hair")]
HAIR = ["short04", "cortu_short_messy_hair"]
CC0_CLOTHES = ["clothes/elvs_crude_t-shirt_male", "clothes/toigo_ankle_boots_male"]
GARMENT_DIRS = {"jacket": "forge_protagonist_leather_jacket", "jeans": "forge_protagonist_jeans"}

TORSO = {"pelvis", "spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r"}
ARM_UP = {"upperarm_l", "upperarm_r"}
ARM_LO = {"lowerarm_l", "lowerarm_r"}
NO_JACKET = {"neck_01", "head"}
LEGS = {"thigh_l", "thigh_r", "calf_l", "calf_r"}
FEET = {"foot_l", "foot_r", "ball_l", "ball_r"}


def _smooth(e0, e1, x):
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lrgb(srgb, a=1.0):
    return (lin(srgb[0]), lin(srgb[1]), lin(srgb[2]), a)


# ----------------------------------------------------------------------------------------------
# body geometry snapshot (rest pose, all masks off so indices match the basemesh)
# ----------------------------------------------------------------------------------------------
class Body:
    def __init__(self, bm, rig):
        masks = [(m, m.show_viewport) for m in bm.modifiers if m.type == "MASK"]
        for m, _ in masks:
            m.show_viewport = False
        dg = bpy.context.evaluated_depsgraph_get()
        ev = bm.evaluated_get(dg)
        me = ev.to_mesh()
        mw = bm.matrix_world
        self.co = [mw @ v.co for v in me.vertices]
        self.nrm = [(mw.to_3x3() @ v.normal).normalized() for v in me.vertices]
        ev.to_mesh_clear()
        for m, s in masks:
            m.show_viewport = s
        gi = {g.index: g.name for g in bm.vertex_groups}
        bones = {b.name for b in rig.data.bones}
        body_idx = bm.vertex_groups["body"].index
        n = len(self.co)
        self.in_body = [False] * n
        self.dom = [None] * n
        for v in bm.data.vertices:
            best = (0.0, None)
            for g in v.groups:
                if g.group == body_idx and g.weight > 0.5:
                    self.in_body[v.index] = True
                nm = gi[g.group]
                if nm in bones and g.weight > best[0]:
                    best = (g.weight, nm)
            self.dom[v.index] = best[1]
        uvd = bm.data.uv_layers.active.data
        self.faces, self.face_uv = [], []
        for p in bm.data.polygons:
            self.faces.append(tuple(p.vertices))
            self.face_uv.append([tuple(uvd[li].uv) for li in p.loop_indices])
        self.body_faces = [i for i, f in enumerate(self.faces) if all(self.in_body[v] for v in f)]
        self.body_face_set = set(self.body_faces)
        self.tree = BVHTree.FromPolygons(self.co, [self.faces[i] for i in self.body_faces])
        self.edge_faces = {}
        for fi in self.body_faces:
            f = self.faces[fi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                self.edge_faces.setdefault((min(a, b), max(a, b)), []).append(fi)
        self.fdom = {}
        for fi in self.body_faces:
            cnt = {}
            for v in self.faces[fi]:
                cnt[self.dom[v]] = cnt.get(self.dom[v], 0) + 1
            self.fdom[fi] = max(cnt.items(), key=lambda kv: (kv[1], str(kv[0])))[0]
        self.bones = {b.name: (rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local)
                      for b in rig.data.bones}

    def center(self, fi):
        f = self.faces[fi]
        return sum((self.co[v] for v in f), Vector()) / len(f)

    def fnormal(self, fi):
        f = self.faces[fi]
        a, b, c = self.co[f[0]], self.co[f[1]], self.co[f[2]]
        return (b - a).cross(c - a).normalized()

    def sd(self, p):
        loc, n, _, _ = self.tree.find_nearest(p)
        return (p - loc).dot(n), loc, n


class Layer:
    """A garment already on the body: a candidate point must sit `margin` further from the body
    than the layer does locally (signed distance measured against the body, whose normals are
    reliable, instead of trusting the CC0 garment's own winding)."""

    def __init__(self, obj, body, margin, reach=0.035, zmax=None):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        co = [obj.matrix_world @ v.co for v in me.vertices]
        self.tree = BVHTree.FromPolygons(co, [tuple(p.vertices) for p in me.polygons])
        ev.to_mesh_clear()
        self.body, self.margin, self.reach, self.zmax = body, margin, reach, zmax

    def need(self, p):
        if self.zmax is not None and p.z > self.zmax:
            return None
        loc, _, _, d = self.tree.find_nearest(p, self.reach)
        if loc is None:
            return None
        s_layer, _, _ = self.body.sd(loc)
        return s_layer + self.margin


def enforce(P, idxs, body, dmin_fn, layers):
    moved = 0
    for i in idxs:
        p = P[i]
        s, loc, n = body.sd(p)
        need = dmin_fn(i)
        for L in layers:
            r = L.need(p)
            if r is not None and r > need:
                need = r
        if s < need:
            P[i] = p + n * (need - s)
            moved += 1
    return moved


# ----------------------------------------------------------------------------------------------
# region shell helpers
# ----------------------------------------------------------------------------------------------
def region_mesh(body, faces):
    """bmesh copy of the chosen body faces; int layer 'src' = basemesh vertex index."""
    bmr = bmesh.new()
    src = bmr.verts.layers.int.new("src")
    uvl = bmr.loops.layers.uv.new("UVMap")
    vmap = {}
    for fi in faces:
        vs = []
        for vi in body.faces[fi]:
            if vi not in vmap:
                bv = bmr.verts.new(body.co[vi])
                bv[src] = vi
                vmap[vi] = bv
            vs.append(vmap[vi])
        f = bmr.faces.new(vs)
        f.smooth = True
        for loop, uv in zip(f.loops, body.face_uv[fi]):
            loop[uvl].uv = uv
    # keep the largest connected component
    bmr.faces.ensure_lookup_table()
    seen, comps = set(), []
    for f in bmr.faces:
        if f in seen:
            continue
        stack, comp = [f], []
        seen.add(f)
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    drop = [f for c in comps[1:] for f in c]
    if drop:
        bmesh.ops.delete(bmr, geom=drop, context="FACES")
    loose = [v for v in bmr.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bmr, geom=loose, context="VERTS")
    bmr.verts.index_update()
    bmr.faces.index_update()
    bmr.verts.ensure_lookup_table()
    bmr.faces.ensure_lookup_table()
    return bmr


def boundary_class(bmr, body, classify):
    """Classify each boundary edge by the body face it was cut from."""
    src = bmr.verts.layers.int["src"]
    region_src = {tuple(sorted(v[src] for v in f.verts)) for f in bmr.faces}
    out = {}
    for e in bmr.edges:
        if not e.is_boundary:
            continue
        a, b = e.verts[0][src], e.verts[1][src]
        cand = [fi for fi in body.edge_faces.get((min(a, b), max(a, b)), [])
                if tuple(sorted(body.faces[fi])) not in region_src]
        out[e] = classify(cand[0]) if cand else "open"
    return out


def chains(edges):
    """Order a set of boundary edges into vertex chains (open or closed)."""
    adj = {}
    for e in edges:
        a, b = e.verts
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    used, result = set(), []
    starts = [v for v, nb in adj.items() if len(nb) == 1] + list(adj.keys())
    for s in starts:
        if s in used:
            continue
        chain, cur, prev = [s], s, None
        used.add(s)
        while True:
            nxt = [w for w in adj[cur] if w is not prev and w not in used]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            used.add(cur)
            chain.append(cur)
        closed = len(chain) > 2 and chain[0] in adj[chain[-1]]
        result.append((chain, closed))
    return result


def graph_dist(bmr, seeds, maxd=6):
    d = {v: 0 for v in seeds}
    frontier = list(seeds)
    for k in range(1, maxd + 1):
        nf = []
        for v in frontier:
            for e in v.link_edges:
                w = e.other_vert(v)
                if w not in d:
                    d[w] = k
                    nf.append(w)
        frontier = nf
    return d


def dijkstra_path(bmr, a, b, allowed=None):
    dist = {a: 0.0}
    prev = {}
    pq = [(0.0, a.index, a)]
    while pq:
        d, _, v = heapq.heappop(pq)
        if v is b:
            break
        if d > dist.get(v, 1e9):
            continue
        for e in v.link_edges:
            w = e.other_vert(v)
            if allowed is not None and w not in allowed:
                continue
            nd = d + e.calc_length()
            if nd < dist.get(w, 1e9):
                dist[w] = nd
                prev[w] = v
                heapq.heappush(pq, (nd, w.index, w))
    path, cur = [b], b
    while cur is not a and cur in prev:
        cur = prev[cur]
        path.append(cur)
    return path[::-1]


def loop_walk(start, direction, stop, limit=200):
    """Follow a mesh edge loop (straight across valence-4 verts) from start, first step taken
    along the edge best aligned with direction, until stop(v) or the boundary."""
    e = max(start.link_edges, key=lambda x: (x.other_vert(start).co - start.co).normalized().dot(direction))
    path, v = [start], start
    for _ in range(limit):
        w = e.other_vert(v)
        path.append(w)
        if stop(w) or w.is_boundary:
            break
        fe = set(e.link_faces)
        cand = [x for x in w.link_edges if x is not e and not (set(x.link_faces) & fe)]
        if len(cand) != 1:
            d = (w.co - v.co).normalized()
            cand = sorted((x for x in w.link_edges if x is not e),
                          key=lambda x: -(x.other_vert(w).co - w.co).normalized().dot(d))
        v, e = w, cand[0]
    return path


def pick(verts, x, z, side, body):
    """Nearest region vertex to (x, z) on the front (-Y facing) or back (+Y facing) surface."""
    src = None
    best, bv = 1e9, None
    for v in verts:
        n = v.normal
        if side == "front" and n.y > -0.25:
            continue
        if side == "back" and n.y < 0.25:
            continue
        d = (v.co.x - x) ** 2 + (v.co.z - z) ** 2
        if d < best:
            best, bv = d, v
    return bv


def relax(bmr, iters, fac, movable, body, dmin_fn, layers, P):
    bnd = {v for v in bmr.verts if v.is_boundary}
    nbrs = {}
    for v in bmr.verts:
        if v in bnd:
            nbrs[v.index] = [e.other_vert(v).index for e in v.link_edges if e.is_boundary]
        else:
            nbrs[v.index] = [e.other_vert(v).index for e in v.link_edges]
    idxs = [v.index for v in bmr.verts if v.index in movable]
    for _ in range(iters):
        Q = list(P)
        for i in idxs:
            nb = nbrs[i]
            if nb:
                avg = sum((P[j] for j in nb), Vector()) / len(nb)
                Q[i] = P[i].lerp(avg, fac)
        P[:] = Q
        enforce(P, idxs, body, dmin_fn, layers)


def add_strip(bmr, ring, newpos, kind_layer, kind, closed=False):
    """Extrude an ordered vertex chain into a quad strip ending at newpos (list of Vectors)."""
    new = [bmr.verts.new(p) for p in newpos]
    faces = []
    n = len(ring)
    for k in range(n if closed else n - 1):
        k2 = (k + 1) % n
        quad = [ring[k], ring[k2], new[k2], new[k]]
        try:
            f = bmr.faces.new(quad)
        except ValueError:
            continue
        f[kind_layer] = kind
        f.smooth = True
        faces.append(f)
    return new, faces


def edge_rings(bmr):
    """Quad edge rings (edges linked through opposite sides of quads)."""
    def opposite(f, e):
        vs = set(e.verts)
        for x in f.edges:
            if not (set(x.verts) & vs):
                return x
        return None

    seen, rings = set(), []
    for e0 in bmr.edges:
        if e0 in seen:
            continue
        ring = [e0]
        seen.add(e0)
        for f0 in list(e0.link_faces):
            cur, f = e0, f0
            while f is not None and len(f.verts) == 4:
                nxt = opposite(f, cur)
                if nxt is None or nxt in seen:
                    break
                ring.append(nxt)
                seen.add(nxt)
                fs = [g for g in nxt.link_faces if g is not f]
                cur, f = nxt, (fs[0] if fs else None)
        rings.append(ring)
    return rings


def densify(bmr, axis_of, thresh=0.55):
    """Double resolution ALONG the body/limb axis by loop-cutting whole edge rings whose edges run
    along that axis. Cutting complete rings keeps an all-quad mesh (each quad gets 0, 2 or 4 cuts)."""
    src = bmr.verts.layers.int["src"]
    orig = bmr.verts.layers.int.new("orig")
    for v in bmr.verts:
        v[orig] = 1
    cut = []
    rings = edge_rings(bmr)
    for ring in rings:
        sc, n = 0.0, 0
        for e in ring:
            d = e.verts[1].co - e.verts[0].co
            if d.length < 1e-9:
                continue
            sc += abs(d.normalized().dot(axis_of(e.verts[0])))
            n += 1
        if n and sc / n > thresh:
            cut += ring
    bmesh.ops.subdivide_edges(bmr, edges=cut, cuts=1, use_grid_fill=True)
    for _ in range(3):
        for v in bmr.verts:
            if v[orig] == 0:
                nb = [e.other_vert(v) for e in v.link_edges if e.other_vert(v)[orig] > 0]
                if nb:
                    v[src] = nb[0][src]
                    v[orig] = 2
    bmr.verts.index_update()
    bmr.faces.index_update()
    bmr.verts.ensure_lookup_table()
    bmr.normal_update()
    return {"rings": len(rings), "cut_edges": len(cut), "non_quads": sum(1 for f in bmr.faces if len(f.verts) != 4)}


def mark_boundary(bmr, bc, codes):
    lay = bmr.edges.layers.int["bcls"]
    for e, k in bc.items():
        e[lay] = codes[k]
    return lay


def boundary_groups(bmr, names):
    lay = bmr.edges.layers.int["bcls"]
    g = {}
    for e in bmr.edges:
        if e.is_boundary and e[lay]:
            g.setdefault(names[e[lay]], []).append(e)
    return g


def orient_outward(bmr, body):
    bmesh.ops.recalc_face_normals(bmr, faces=list(bmr.faces))
    bmr.normal_update()
    score = 0.0
    for f in bmr.faces:
        c = f.calc_center_median()
        s, loc, n = body.sd(c)
        score += f.normal.dot((c - loc).normalized() if (c - loc).length > 1e-6 else n)
    if score < 0:
        for f in bmr.faces:
            f.normal_flip()
        bmr.normal_update()
    return score


# ----------------------------------------------------------------------------------------------
# JEANS
# ----------------------------------------------------------------------------------------------
def build_jeans(body, layers, rep):
    pel = body.bones["pelvis"][0]
    y0 = pel.y
    WAIST = lambda c: 1.068 + 0.08 * (c.y - y0)
    HEM = 0.105

    def keep(fi):
        d = body.fdom[fi]
        if d is None or d in FEET or d in NO_JACKET or not (d in TORSO or d in LEGS):
            return False
        c = body.center(fi)
        return HEM <= c.z <= WAIST(c)

    sel = [fi for fi in body.body_faces if keep(fi)]
    bmr = region_mesh(body, sel)
    src = bmr.verts.layers.int["src"]
    kind = bmr.faces.layers.int.new("kind")

    def classify(fi):
        c = body.center(fi)
        return "hem" if c.z < 0.5 else "waist"

    region_src = [tuple(v[src] for v in f.verts) for f in bmr.faces]
    bmr.edges.layers.int.new("bcls")   # before any BMEdge refs are held: a new layer invalidates them
    bc = boundary_class(bmr, body, classify)
    mark_boundary(bmr, bc, {"waist": 1, "hem": 2, "open": 9})
    NAMES = {1: "waist", 2: "hem", 9: "open"}
    waist_edges = [e for e, k in bc.items() if k == "waist"]
    hem_edges = [e for e, k in bc.items() if k == "hem"]
    waist_v = {v for e in waist_edges for v in e.verts}
    hem_v = {v for e in hem_edges for v in e.verts}

    knee = {s: body.bones["calf_" + s][0] for s in "lr"}
    ankle = {s: body.bones["foot_" + s][0] for s in "lr"}
    hip = {s: body.bones["thigh_" + s][0] for s in "lr"}

    def axis_of(v):
        d = body.dom[v[src]]
        if d in ("thigh_l", "thigh_r"):
            return (knee[d[-1]] - hip[d[-1]]).normalized()
        if d in ("calf_l", "calf_r"):
            return (ankle[d[-1]] - knee[d[-1]]).normalized()
        return Vector((0, 0, 1))

    def offset(v):
        z = v.co.z
        return 0.010 + 0.005 * min(1.0, max(0.0, (1.06 - z) / 0.25))

    P = []
    for v in bmr.verts:
        vi = v[src]
        P.append(body.co[vi] + body.nrm[vi] * offset(v))
    # straight leg below the knee: radius floor measured from the knee->ankle axis
    R_KNEE, R_HEM = 0.068, 0.070
    for v in bmr.verts:
        p = P[v.index]
        side = "l" if p.x > 0 else "r"
        k, a = knee[side], ankle[side]
        ax = (k - a)
        L = ax.length
        ax.normalize()
        t = (p - a).dot(ax) / L        # 0 at ankle, 1 at knee
        if t > 1.0 + 0.08 / L or abs(p.x) < 0.03:
            continue
        w = 1.0 if t <= 1.0 else max(0.0, 1.0 - (t - 1.0) * L / 0.08)
        c = a + ax * (t * L)
        r = p - c
        r -= ax * r.dot(ax)
        rt = R_HEM + (R_KNEE - R_HEM) * min(1.0, max(0.0, t))
        if r.length < rt and r.length > 1e-6:
            P[v.index] = p + r.normalized() * ((rt - r.length) * w)
    dmin = lambda i: 0.008
    movable = {v.index for v in bmr.verts}
    relax(bmr, 10, 0.45, movable, body, dmin, layers, P)
    # snap waist to the tilted plane and hem to a level plane
    for v in waist_v:
        P[v.index].z = WAIST(P[v.index])
    for v in hem_v:
        P[v.index].z = HEM
    for v in bmr.verts:
        v.co = P[v.index]
    rep["jeans_densify"] = densify(bmr, axis_of)
    groups = boundary_groups(bmr, NAMES)
    waist_edges, hem_edges = groups.get("waist", []), groups.get("hem", [])
    waist_v = {v for e in waist_edges for v in e.verts}
    hem_v = {v for e in hem_edges for v in e.verts}
    P = [v.co.copy() for v in bmr.verts]
    relax(bmr, 2, 0.3, {v.index for v in bmr.verts}, body, dmin, layers, P)
    for v in waist_v:
        P[v.index].z = WAIST(P[v.index])
    for v in hem_v:
        P[v.index].z = HEM
    # denim stacking above the hem, back-of-knee folds, low-frequency irregularity (outward only)
    for v in bmr.verts:
        if v.is_boundary:
            continue
        vi = v[src]
        p, n = P[v.index], body.nrm[vi]
        ang = math.atan2(n.y, n.x)
        a = 0.005 * max(0.0, 1 - (p.z - HEM) / 0.16) * (0.5 + 0.5 * math.sin((p.z - HEM) / 0.032 * 2 * math.pi + 2 * ang))
        side = "l" if p.x > 0 else "r"
        if n.y > 0.3:
            a += 0.003 * max(0.0, 1 - abs(p.z - knee[side].z) / 0.07) * (0.5 + 0.5 * math.sin(p.z / 0.025 * 2 * math.pi))
        a += 0.002 * (0.5 + 0.5 * mnoise.noise(p * 8.0))
        P[v.index] = p + n * a
    enforce(P, [v.index for v in bmr.verts], body, dmin, layers)
    for v in bmr.verts:
        v.co = P[v.index]
    bmr.normal_update()
    for f in bmr.faces:
        f[kind] = 0

    # stitch / detail paths (vertex attribute 'stitch')
    region_v = set(bmr.verts)
    stitch = set()
    dw = graph_dist(bmr, waist_v)
    dh = graph_dist(bmr, hem_v)
    stitch |= {v for v, d in dw.items() if d in (1, 4)}
    stitch |= {v for v, d in dh.items() if d == 2}
    wz = 1.064
    paths = {}
    for sgn, s in ((1, "l"), (-1, "r")):
        side_v = [v for v in bmr.verts if v.co.x * sgn > 0.01]
        top_ring = [v for v in side_v if abs(v.co.z - (wz - 0.05)) < 0.02]
        hem_ring = [v for v in hem_v if v.co.x * sgn > 0]
        out_top = max(top_ring, key=lambda v: abs(v.co.x))
        out_hem = max(hem_ring, key=lambda v: abs(v.co.x))
        in_hem = min(hem_ring, key=lambda v: abs(v.co.x))
        crotch = min((v for v in bmr.verts if abs(v.co.x) < 0.035 and v.co.z < 0.95), key=lambda v: v.co.z)
        paths["outseam_" + s] = dijkstra_path(bmr, out_top, out_hem)
        paths["inseam_" + s] = dijkstra_path(bmr, crotch, in_hem)
        # front pocket curve
        a = pick(side_v, sgn * 0.085, wz - 0.045, "front", body)
        m = pick(side_v, sgn * 0.13, wz - 0.095, "front", body)
        b = min(paths["outseam_" + s], key=lambda v: abs(v.co.z - (wz - 0.135)))
        paths["pocket_" + s] = dijkstra_path(bmr, a, m) + dijkstra_path(bmr, m, b)[1:]
        # back pocket outline
        corners = [pick(side_v, sgn * 0.045, wz - 0.10, "back", body), pick(side_v, sgn * 0.15, wz - 0.09, "back", body),
                   pick(side_v, sgn * 0.145, wz - 0.24, "back", body), pick(side_v, sgn * 0.095, wz - 0.27, "back", body),
                   pick(side_v, sgn * 0.048, wz - 0.24, "back", body)]
        bp = []
        for k in range(5):
            seg = dijkstra_path(bmr, corners[k], corners[(k + 1) % 5])
            bp += seg if not bp else seg[1:]
        paths["backpocket_" + s] = bp
    # yoke V across the back and the J fly on the front
    yl = min(paths["outseam_l"], key=lambda v: abs(v.co.z - (wz - 0.07)))
    yr = min(paths["outseam_r"], key=lambda v: abs(v.co.z - (wz - 0.07)))
    yc = pick(list(bmr.verts), 0.0, wz - 0.115, "back", body)
    paths["yoke"] = dijkstra_path(bmr, yl, yc) + dijkstra_path(bmr, yc, yr)[1:]
    f0 = pick(list(bmr.verts), 0.035, wz - 0.05, "front", body)
    f1 = pick(list(bmr.verts), 0.035, wz - 0.15, "front", body)
    f2 = pick(list(bmr.verts), 0.0, wz - 0.19, "front", body)
    paths["fly"] = dijkstra_path(bmr, f0, f1) + dijkstra_path(bmr, f1, f2)[1:]
    for k, p in paths.items():
        stitch |= set(p)
    seam_paths = paths["outseam_l"] + paths["outseam_r"] + paths["inseam_l"] + paths["inseam_r"]

    # rims: waistband and hem turn inward (fabric thickness), quads only
    n_base = len(bmr.verts)
    for group, depth in ((waist_edges, 0.005), (hem_edges, 0.005)):
        for chain, closed in chains(group):
            newpos = []
            for v in chain:
                s, loc, n = body.sd(v.co)
                newpos.append(v.co - n * depth)
            add_strip(bmr, chain, newpos, kind, 3, closed=closed)
    rep["jeans_paths"] = {k: len(v) for k, v in paths.items()}
    return bmr, stitch, set(), region_src, {"waist": wz, "hem": HEM}


# ----------------------------------------------------------------------------------------------
# JACKET
# ----------------------------------------------------------------------------------------------
def build_jacket(body, layers, rep):
    pel = body.bones["pelvis"][0]
    HEM = pel.z - 0.015
    GAP = 0.040
    elbow = {s: body.bones["lowerarm_" + s][0] for s in "lr"}
    wrist = {s: body.bones["hand_" + s][0] for s in "lr"}
    shoulder = {s: body.bones["upperarm_" + s][0] for s in "lr"}
    neck = body.bones["neck_01"][0]

    def t_fore(c, s):
        ax = wrist[s] - elbow[s]
        return (c - elbow[s]).dot(ax) / ax.length_squared

    def is_gap(fi):
        c = body.center(fi)
        n = body.fnormal(fi)
        return n.y < -0.2 and abs(c.x) < GAP and c.z < neck.z

    def keep(fi):
        d = body.fdom[fi]
        if d is None or d in NO_JACKET or d in FEET:
            return False
        c = body.center(fi)
        if d in ARM_LO:
            return t_fore(c, d[-1]) <= 0.96
        if not (d in TORSO or d in ARM_UP or d in LEGS):
            return False
        if c.z < HEM:
            return False
        return not is_gap(fi)

    sel = [fi for fi in body.body_faces if keep(fi)]
    bmr = region_mesh(body, sel)
    src = bmr.verts.layers.int["src"]
    kind = bmr.faces.layers.int.new("kind")

    def classify(fi):
        d = body.fdom[fi]
        c = body.center(fi)
        if d in NO_JACKET:
            return "neck"
        if d in ARM_LO or (d and d.startswith(("hand", "thumb", "index", "middle", "ring", "pinky"))):
            return "cuff"
        if c.z < HEM + 0.01:
            return "hem"
        if is_gap(fi):
            return "front"
        return "neck" if c.z > 1.45 else "hem"

    region_src = [tuple(v[src] for v in f.verts) for f in bmr.faces]
    bmr.edges.layers.int.new("bcls")   # before any BMEdge refs are held: a new layer invalidates them
    bc = boundary_class(bmr, body, classify)
    CODES = {"hem": 1, "front": 2, "cuff": 3, "neck": 4, "open": 9}
    NAMES = {v: k for k, v in CODES.items()}
    mark_boundary(bmr, bc, CODES)
    groups = {}
    for e, k in bc.items():
        groups.setdefault(k, []).append(e)
    rep["jacket_boundary_edges"] = {k: len(v) for k, v in groups.items()}

    def part(v):
        return body.dom[v[src]]

    def axis_of(v):
        d = part(v)
        if d in ARM_UP or d in ARM_LO:
            return (wrist[d[-1]] - shoulder[d[-1]]).normalized()
        return Vector((0, 0, 1))

    def offset(v):
        d = part(v)
        z = v.co.z
        if d in ARM_UP or d in ARM_LO:
            s = d[-1]
            ax = wrist[s] - shoulder[s]
            t = max(0.0, min(1.0, (v.co - shoulder[s]).dot(ax) / ax.length_squared))
            return 0.016 + 0.005 * t
        return 0.020 + 0.012 * min(1.0, max(0.0, (1.30 - z) / (1.30 - HEM)))

    P = []
    for v in bmr.verts:
        vi = v[src]
        P.append(body.co[vi] + body.nrm[vi] * offset(v))
    dmin = lambda i: 0.012
    movable = {v.index for v in bmr.verts}
    relax(bmr, 12, 0.5, movable, body, dmin, layers, P)

    hem_v = {v for e in groups.get("hem", []) for v in e.verts}
    front_v = {v for e in groups.get("front", []) for v in e.verts}
    cuff_v = {v for e in groups.get("cuff", []) for v in e.verts}
    neck_v = {v for e in groups.get("neck", []) for v in e.verts}
    for v in hem_v:
        P[v.index].z = HEM
    for v in front_v - hem_v:
        P[v.index].x = math.copysign(GAP, P[v.index].x)
    for chain, closed in chains(groups.get("cuff", [])):
        s = "l" if P[chain[0].index].x > 0 else "r"
        ax = (wrist[s] - elbow[s]).normalized()
        t0 = sum((P[v.index] - wrist[s]).dot(ax) for v in chain) / len(chain)
        for v in chain:
            P[v.index] = P[v.index] - ax * ((P[v.index] - wrist[s]).dot(ax) - t0)
    for v in bmr.verts:
        v.co = P[v.index]
    rep["jacket_densify"] = densify(bmr, axis_of)
    groups = boundary_groups(bmr, NAMES)
    rep["jacket_boundary_edges_dense"] = {k: len(v) for k, v in groups.items()}
    hem_v = {v for e in groups.get("hem", []) for v in e.verts}
    front_v = {v for e in groups.get("front", []) for v in e.verts}
    cuff_v = {v for e in groups.get("cuff", []) for v in e.verts}
    P = [v.co.copy() for v in bmr.verts]
    relax(bmr, 2, 0.3, {v.index for v in bmr.verts}, body, dmin, layers, P)
    for v in hem_v:
        P[v.index].z = HEM
    for v in front_v - hem_v:
        P[v.index].x = math.copysign(GAP, P[v.index].x)
    # leather folds (outward only, so they cannot create poke-through): sleeve bunching above
    # the cuff, crook-of-elbow folds, waist side ripples, low-frequency irregularity
    for v in bmr.verts:
        if v.is_boundary:
            continue
        i, vi = v.index, v[src]
        d, p, n = body.dom[vi], P[v.index], body.nrm[vi]
        a = 0.0
        if d in ARM_UP or d in ARM_LO:
            s = d[-1]
            ax = wrist[s] - shoulder[s]
            L = ax.length
            t = max(0.0, min(1.0, (p - shoulder[s]).dot(ax) / (L * L)))
            r = p - (shoulder[s] + ax * t)
            ang = math.atan2(r.z, r.y)
            a += 0.0045 * _smooth(0.62, 0.93, t) * (0.5 + 0.5 * math.sin(t * L / 0.035 * 2 * math.pi + 1.7 * ang))
            te = (elbow[s] - shoulder[s]).dot(ax) / (L * L)
            a += 0.004 * max(0.0, 1 - abs(t - te) / 0.07) * (0.5 + 0.5 * math.sin(t * L / 0.03 * 2 * math.pi + 2.3 * ang))
        else:
            side = min(1.0, max(0.0, (abs(n.x) - 0.35) / 0.4))
            low = max(0.0, 1 - (p.z - HEM) / 0.14)
            a += 0.003 * side * low * (0.5 + 0.5 * math.sin((p.z - HEM) / 0.028 * 2 * math.pi + 30 * p.y))
        a += 0.0025 * (0.5 + 0.5 * mnoise.noise(p * 9.0))
        P[i] = p + n * a
    enforce(P, [v.index for v in bmr.verts], body, dmin, layers)
    for v in bmr.verts:
        v.co = P[v.index]
    bmr.normal_update()
    for f in bmr.faces:
        f[kind] = 0

    # stitching + zip pocket lines
    stitch, zipl = set(), set()
    dh = graph_dist(bmr, hem_v)
    dc = graph_dist(bmr, cuff_v)
    dfr = graph_dist(bmr, front_v - hem_v)
    stitch |= {v for v, d in dh.items() if d == 4}
    stitch |= {v for v, d in dc.items() if d == 4}
    stitch |= {v for v, d in dfr.items() if d == 1 and v.co.z > HEM + 0.02}
    # armhole seam: a closed edge path around the plane through the shoulder joint, normal to the
    # upper arm (top of shoulder -> front armpit -> underarm -> back armpit)
    paths = {}
    armhole = set()
    allv = list(bmr.verts)
    for s in "lr":
        S = shoulder[s]
        ax = (elbow[s] - S).normalized()
        cand = [v for v in allv if abs((v.co - S).dot(ax) + 0.005) < 0.012 and (v.co - S).length < 0.17]
        if len(cand) < 4:
            continue
        top = max(cand, key=lambda v: v.co.z)
        bot = min(cand, key=lambda v: v.co.z)
        fr = min(cand, key=lambda v: v.co.y)
        bk = max(cand, key=lambda v: v.co.y)
        loop = dijkstra_path(bmr, top, fr)
        for a_, b_ in ((fr, bot), (bot, bk), (bk, top)):
            loop += dijkstra_path(bmr, a_, b_)[1:]
        paths["armhole_" + s] = loop
        armhole |= set(loop)
    for sgn, s in ((1, "l"), (-1, "r")):
        # front yoke: armhole-front to the front edge, just under the collar bone
        a = min((v for v in armhole if v.co.x * sgn > 0 and v.normal.y < 0), key=lambda v: abs(v.co.z - 1.45), default=None)
        # end on the front edge at the same height so the path runs along one mesh row (no staircase)
        if a:
            paths["yoke_front_" + s] = loop_walk(a, Vector((-sgn, 0, 0)), lambda w: w in front_v)
        # slanted side welt pocket near the hem
        p0 = pick(allv, sgn * 0.115, HEM + 0.05, "front", body)
        p1 = pick(allv, sgn * 0.115, HEM + 0.16, "front", body)
        paths["welt_" + s] = loop_walk(p0, Vector((0, 0, 1)), lambda w: w.co.z > HEM + 0.16)
    # back yoke across the shoulder blades
    bl = min((v for v in armhole if v.co.x > 0 and v.normal.y > 0), key=lambda v: abs(v.co.z - 1.47), default=None)
    br = min((v for v in armhole if v.co.x < 0 and v.normal.y > 0), key=lambda v: abs(v.co.z - 1.47), default=None)
    if bl and br:
        paths["yoke_back"] = loop_walk(bl, Vector((-1, 0, 0)), lambda w: w in armhole and w.co.x < 0)
    for k, p in paths.items():
        stitch |= set(p)
    # chest zip pocket, wearer's left (+X), slanted
    z0 = pick(allv, 0.075, 1.40, "front", body)
    z1 = pick(allv, 0.165, z0.co.z, "front", body)
    zp = loop_walk(z0, Vector((1, 0, 0)), lambda w: w.co.x > 0.165)
    zipl |= set(zp)
    paths["chest_zip"] = zp

    # front zip strips (metal teeth), toward the centre line, then an inward rim
    for chain, closed in chains(groups.get("front", [])):
        if chain[0].co.z > chain[-1].co.z:
            chain = chain[::-1]
        sgn = 1 if chain[0].co.x > 0 else -1
        newpos = [v.co + Vector((-sgn * 0.009, 0, 0)) for v in chain]
        zr, _ = add_strip(bmr, chain, newpos, kind, 2)
        rim = []
        for v in zr:
            s, loc, n = body.sd(v.co)
            rim.append(v.co - n * 0.006)
        add_strip(bmr, zr, rim, kind, 3)
    # hem + cuff rims (leather thickness)
    for grp in ("hem", "cuff"):
        for chain, closed in chains(groups.get(grp, [])):
            newpos = []
            for v in chain:
                s, loc, n = body.sd(v.co)
                newpos.append(v.co - n * 0.007)
            add_strip(bmr, chain, newpos, kind, 3, closed=closed)
    # collar: stand -> crest -> fall -> edge, points at the front
    nchains = [c for c, closed in chains(groups.get("neck", []))]
    nchains.sort(key=len, reverse=True)
    collar_stitch = []
    if nchains:
        ch = nchains[0]
        ycen = sum(v.co.y for v in ch) / len(ch)
        arc = [0.0]
        for k in range(1, len(ch)):
            arc.append(arc[-1] + (ch[k].co - ch[k - 1].co).length)
        total = arc[-1]
        up = Vector((0, 0, 1))
        rings = {k: [] for k in ("stand", "crest", "fall", "edge", "rim")}
        for k, v in enumerate(ch):
            o = Vector((v.co.x, v.co.y - ycen, 0))
            o = o.normalized() if o.length > 1e-6 else Vector((0, 1, 0))
            e_arc = min(arc[k], total - arc[k])
            w = max(0.0, 1.0 - e_arc / 0.06)
            h = 1.0 - 0.45 * w
            fwd = Vector((0, -1, 0))
            base = v.co
            rings["stand"].append(base + up * (0.028 * h) + o * 0.004)
            rings["crest"].append(base + up * (0.034 * h) + o * 0.014)
            rings["fall"].append(base + up * (0.018 * h) + o * 0.030 + up * (-0.022 * w) + fwd * (0.006 * w))
            rings["edge"].append(base + up * (0.004 * h) + o * 0.042 + up * (-0.045 * w) + fwd * (0.012 * w))
        prev = ch
        for nm in ("stand", "crest", "fall", "edge"):
            prev, _ = add_strip(bmr, prev, rings[nm], kind, 1)
            if nm == "fall":
                collar_stitch = prev
        rim = [v.co - up * 0.004 - Vector((v.co.x, v.co.y - ycen, 0)).normalized() * 0.003 for v in prev]
        add_strip(bmr, prev, rim, kind, 3)
    stitch |= set(collar_stitch)
    rep["jacket_paths"] = {k: len(v) for k, v in paths.items()}
    return bmr, stitch, zipl, region_src, {"hem": HEM, "gap_half": GAP}


# ----------------------------------------------------------------------------------------------
# UVs, object creation, MHCLO write
# ----------------------------------------------------------------------------------------------
def finish_uvs(obj, seam_verts_paths):
    me = obj.data
    bmr = bmesh.new()
    bmr.from_mesh(me)
    uvl = bmr.loops.layers.uv.active
    kind = bmr.faces.layers.int.get("kind")
    for e in bmr.edges:
        if e.is_boundary or len(e.link_faces) != 2:
            continue
        f1, f2 = e.link_faces
        if kind is not None and f1[kind] != f2[kind]:
            e.seam = True
            continue
        for v in e.verts:
            u1 = [l[uvl].uv for l in f1.loops if l.vert is v][0]
            u2 = [l[uvl].uv for l in f2.loops if l.vert is v][0]
            if (u1 - u2).length > 1e-5:
                e.seam = True
    idx = {v.index: v for v in bmr.verts}
    bmr.verts.ensure_lookup_table()
    for path in seam_verts_paths:
        for a, b in zip(path[:-1], path[1:]):
            e = bmr.edges.get((bmr.verts[a], bmr.verts[b]))
            if e:
                e.seam = True
    bmr.to_mesh(me)
    bmr.free()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.004)
    bpy.ops.uv.pack_islands(margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")


def uv_report(me):
    try:
        if FORGE not in sys.path:
            sys.path.insert(0, os.path.join(FORGE, "addon"))
        from forge.tools.verify import uv_metrics
        r = uv_metrics(me)
        return {k: (v.get("value") if isinstance(v, dict) and "value" in v else v)
                for k, v in (r or {}).items() if k != "layers"}
    except Exception as ex:
        return {"error": repr(ex)}


def bm_to_object(bmr, name, stitch, zipl):
    bmr.verts.index_update()
    bmr.faces.index_update()
    me = bpy.data.meshes.new(name)
    bmr.to_mesh(me)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    st = me.attributes.new("stitch", "FLOAT", "POINT")
    zp = me.attributes.new("zipline", "FLOAT", "POINT")
    sset = {v.index for v in stitch if v.is_valid}
    zset = {v.index for v in zipl if v.is_valid}
    st.data.foreach_set("value", [1.0 if i in sset else 0.0 for i in range(len(me.vertices))])
    zp.data.foreach_set("value", [1.0 if i in zset else 0.0 for i in range(len(me.vertices))])
    return obj


def write_mhclo_garment(M, basemesh, obj, key, delete_verts, rep):
    """MakeClothes route: every garment vertex in the 'body' group, matched to basemesh faces
    by ClothesService (legacy MC2 barycentric + offset), delete group = covered body verts."""
    HumanService, ClothesService, LocationService = M["HumanService"], M["ClothesService"], M["LocationService"]
    me = obj.data
    kinds = [a.value for a in me.attributes["kind"].data] if "kind" in me.attributes else [0] * len(me.polygons)
    side = {
        "kind": kinds,
        "stitch": [a.value for a in me.attributes["stitch"].data],
        "zipline": [a.value for a in me.attributes["zipline"].data],
        "n_verts": len(me.vertices), "n_faces": len(me.polygons),
        "co": [tuple(round(c, 6) for c in v.co) for v in me.vertices],
    }
    vg = obj.vertex_groups.new(name="body")
    vg.add(list(range(len(me.vertices))), 1.0, "REPLACE")
    del_name = "forge_delete_" + key
    if del_name in basemesh.vertex_groups:
        basemesh.vertex_groups.remove(basemesh.vertex_groups[del_name])
    dg = basemesh.vertex_groups.new(name=del_name)
    dg.add(sorted(delete_verts), 1.0, "REPLACE")
    name = GARMENT_DIRS[key]
    props = {"name": name, "author": "forge (werewolf custom-character lane)", "license": "CC0",
             "description": "forge-modelled %s for the werewolf protagonist" % key,
             "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "forge/werewolf/" + name))}
    mh = ClothesService.create_mhclo_from_clothes_matching(basemesh, obj, properties_dict=props,
                                                           delete_group=del_name, allow_exact=False)
    folder = os.path.join(LocationService.get_user_data("clothes"), name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".mhclo")
    ref = ClothesService.get_reference_scale(basemesh)
    mh.write_mhclo(path, reference_scale=ref, also_export_mhmat=False, also_export_obj=True)
    json.dump(side, open(os.path.join(folder, name + ".forge.json"), "w"))
    basemesh.vertex_groups.remove(basemesh.vertex_groups[del_name])
    mesh = obj.data
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    rep.setdefault("mhclo", {})[key] = {"path": path, "verts": side["n_verts"], "faces": side["n_faces"],
                                        "delete_verts": len(delete_verts)}
    return path, side


def delete_verts_for(body, bmr_src_ids, region_faces_src, margin_rings=2):
    """Body verts of the covered region, minus `margin_rings` rings from its boundary."""
    vset = set()
    for f in region_faces_src:
        vset.update(f)
    edges = {}
    for f in region_faces_src:
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            key = (min(a, b), max(a, b))
            edges[key] = edges.get(key, 0) + 1
    bnd = {v for (a, b), c in edges.items() if c == 1 for v in (a, b)}
    adj = {}
    for (a, b) in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    near = set(bnd)
    frontier = set(bnd)
    for _ in range(margin_rings):
        nf = set()
        for v in frontier:
            for w in adj.get(v, ()):
                if w not in near:
                    near.add(w)
                    nf.add(w)
        frontier = nf
    return sorted(vset - near)


# ----------------------------------------------------------------------------------------------
# materials
# ----------------------------------------------------------------------------------------------
def _nodes(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return nt, bsdf


def _noise(nt, scale, detail=6.0, coords="Object"):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = scale
    nz.inputs["Detail"].default_value = detail
    nt.links.new(tc.outputs[coords], nz.inputs["Vector"])
    return nz


def _mixc(nt, fac_sock, a, b):
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    if isinstance(a, tuple):
        m.inputs[6].default_value = a
    else:
        nt.links.new(a, m.inputs[6])
    if isinstance(b, tuple):
        m.inputs[7].default_value = b
    else:
        nt.links.new(b, m.inputs[7])
    nt.links.new(fac_sock, m.inputs[0])
    return m.outputs[2]


def _ramp(nt, sock, p0, p1):
    r = nt.nodes.new("ShaderNodeMapRange")
    r.inputs["From Min"].default_value = p0
    r.inputs["From Max"].default_value = p1
    nt.links.new(sock, r.inputs["Value"])
    return r.outputs[0]


def _mul(nt, a, b):
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = "MULTIPLY"
    nt.links.new(a, m.inputs[0])
    if isinstance(b, float):
        m.inputs[1].default_value = b
    else:
        nt.links.new(b, m.inputs[1])
    return m.outputs[0]


def _attr(nt, name):
    a = nt.nodes.new("ShaderNodeAttribute")
    a.attribute_type = "GEOMETRY"
    a.attribute_name = name
    return a.outputs["Fac"]


def _stitch_overlay(nt, color_sock, thread, attr="stitch", width=(0.92, 0.98), dash_scale=700.0):
    line = _ramp(nt, _attr(nt, attr), width[0], width[1])
    dash = _ramp(nt, _noise(nt, dash_scale, 0.0).outputs["Fac"], 0.46, 0.54)
    return _mixc(nt, _mul(nt, line, dash), color_sock, thread), line


def mat_leather(pal):
    mat = bpy.data.materials.new("forge_leather")
    nt, bsdf = _nodes(mat)
    base = lrgb((34, 26, 24))
    wear = lrgb((84, 56, 44))
    lining = lrgb((22, 18, 17))
    wn = _noise(nt, 7.0, 10.0)
    wf = _ramp(nt, wn.outputs["Fac"], 0.58, 0.80)
    col = _mixc(nt, wf, base, wear)
    col, _ = _stitch_overlay(nt, col, lrgb((96, 78, 62)))
    zl = _ramp(nt, _attr(nt, "zipline"), 0.72, 0.92)
    col = _mixc(nt, zl, col, lrgb((92, 82, 68)))
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    col = _mixc(nt, geo.outputs["Backfacing"], col, lining)
    nt.links.new(col, bsdf.inputs["Base Color"])
    rough = _ramp(nt, _noise(nt, 18.0, 4.0).outputs["Fac"], 0.3, 0.7)
    rmix = nt.nodes.new("ShaderNodeMapRange")
    rmix.inputs["To Min"].default_value = 0.40
    rmix.inputs["To Max"].default_value = 0.62
    nt.links.new(rough, rmix.inputs["Value"])
    nt.links.new(rmix.outputs[0], bsdf.inputs["Roughness"])
    met = _mul(nt, zl, 0.9)
    nt.links.new(met, bsdf.inputs["Metallic"])
    grain = nt.nodes.new("ShaderNodeTexVoronoi")
    grain.inputs["Scale"].default_value = 420.0
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["Object"], grain.inputs["Vector"])
    wr = _noise(nt, 14.0, 3.0)
    b1 = nt.nodes.new("ShaderNodeBump")
    b1.inputs["Strength"].default_value = 0.12
    b1.inputs["Distance"].default_value = 0.0006
    nt.links.new(grain.outputs["Distance"], b1.inputs["Height"])
    b2 = nt.nodes.new("ShaderNodeBump")
    b2.inputs["Strength"].default_value = 0.28
    b2.inputs["Distance"].default_value = 0.004
    nt.links.new(wr.outputs["Fac"], b2.inputs["Height"])
    nt.links.new(b1.outputs[0], b2.inputs["Normal"])
    nt.links.new(b2.outputs[0], bsdf.inputs["Normal"])
    return mat


def mat_metal():
    mat = bpy.data.materials.new("forge_zip_metal")
    nt, bsdf = _nodes(mat)
    bsdf.inputs["Base Color"].default_value = lrgb((96, 86, 72))
    bsdf.inputs["Metallic"].default_value = 1.0
    bsdf.inputs["Roughness"].default_value = 0.4
    tc = nt.nodes.new("ShaderNodeTexCoord")
    wv = nt.nodes.new("ShaderNodeTexWave")
    wv.bands_direction = "Z"
    wv.inputs["Scale"].default_value = 180.0
    nt.links.new(tc.outputs["Object"], wv.inputs["Vector"])
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = 0.6
    nt.links.new(wv.outputs["Fac"], b.inputs["Height"])
    nt.links.new(b.outputs[0], bsdf.inputs["Normal"])
    return mat


def mat_edge(name, srgb, rough):
    mat = bpy.data.materials.new(name)
    nt, bsdf = _nodes(mat)
    bsdf.inputs["Base Color"].default_value = lrgb(srgb)
    bsdf.inputs["Roughness"].default_value = rough
    return mat


def mat_denim(pal):
    mat = bpy.data.materials.new("forge_denim")
    nt, bsdf = _nodes(mat)
    base = lrgb((34, 33, 38))
    fade = lrgb((66, 64, 72))
    fn = _noise(nt, 4.0, 8.0)
    ff = _ramp(nt, fn.outputs["Fac"], 0.5, 0.75)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tw = nt.nodes.new("ShaderNodeTexWave")
    tw.bands_direction = "DIAGONAL"
    tw.inputs["Scale"].default_value = 520.0
    nt.links.new(tc.outputs["Object"], tw.inputs["Vector"])
    twf = _mul(nt, tw.outputs["Fac"], 0.35)
    col = _mixc(nt, ff, base, fade)
    col = _mixc(nt, twf, col, lrgb((20, 20, 24)))
    col, _ = _stitch_overlay(nt, col, lrgb((92, 84, 70)), width=(0.91, 0.975), dash_scale=800.0)
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    col = _mixc(nt, geo.outputs["Backfacing"], col, lrgb((28, 28, 34)))
    nt.links.new(col, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.82
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = 0.15
    b.inputs["Distance"].default_value = 0.0008
    nt.links.new(tw.outputs["Fac"], b.inputs["Height"])
    wr = _noise(nt, 22.0, 3.0)
    b2 = nt.nodes.new("ShaderNodeBump")
    b2.inputs["Strength"].default_value = 0.25
    b2.inputs["Distance"].default_value = 0.003
    nt.links.new(wr.outputs["Fac"], b2.inputs["Height"])
    nt.links.new(b.outputs[0], b2.inputs["Normal"])
    nt.links.new(b2.outputs[0], bsdf.inputs["Normal"])
    return mat


def tint(obj, srgb, rough=None, gain=2.2):
    col = [lin(c) for c in srgb]
    for slot in obj.material_slots:
        m = slot.material
        if not m or not m.use_nodes:
            continue
        nt = m.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        inp = bsdf.inputs["Base Color"]
        srcs = inp.links[0].from_socket if inp.links else None
        hs = nt.nodes.new("ShaderNodeHueSaturation")
        hs.inputs["Saturation"].default_value = 0.0
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        mix.inputs[7].default_value = (min(1, col[0] * gain), min(1, col[1] * gain), min(1, col[2] * gain), 1)
        if srcs is not None:
            nt.links.new(srcs, hs.inputs["Color"])
            nt.links.new(hs.outputs[0], mix.inputs[6])
        else:
            mix.inputs[6].default_value = (0.5, 0.5, 0.5, 1)
        nt.links.new(mix.outputs[2], inp)
        if rough is not None:
            ri = bsdf.inputs["Roughness"]
            for l in list(ri.links):
                nt.links.remove(l)
            ri.default_value = rough


def desaturate(obj, sat):
    for slot in obj.material_slots:
        nt = slot.material.node_tree
        tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
        if tex is None:
            continue
        for l in list(tex.outputs["Color"].links):
            hs = nt.nodes.new("ShaderNodeHueSaturation")
            hs.inputs["Saturation"].default_value = sat
            nt.links.new(tex.outputs["Color"], hs.inputs["Color"])
            nt.links.new(hs.outputs[0], l.to_socket)


def apply_garment_materials(obj, side, mats):
    me = obj.data
    assert len(me.vertices) == side["n_verts"] and len(me.polygons) == side["n_faces"], "OBJ round trip changed topology"
    me.materials.clear()
    for m in mats:
        me.materials.append(m)
    slot_of_kind = {0: 0, 1: 0, 2: 1, 3: 2}
    me.polygons.foreach_set("material_index", [slot_of_kind[k] for k in side["kind"]])
    for nm in ("stitch", "zipline"):
        a = me.attributes.get(nm) or me.attributes.new(nm, "FLOAT", "POINT")
        a.data.foreach_set("value", side[nm])
    k = me.attributes.get("kind") or me.attributes.new("kind", "INT", "FACE")
    k.data.foreach_set("value", side["kind"])
    for p in me.polygons:
        p.use_smooth = True
    # the OBJ round trip carries flat custom normals; the garment is smooth-shaded quads
    if me.has_custom_normals:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    if "sharp_face" in me.attributes:
        me.attributes.remove(me.attributes["sharp_face"])


# ----------------------------------------------------------------------------------------------
# checks
# ----------------------------------------------------------------------------------------------
def eval_points(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    pts = [obj.matrix_world @ v.co for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    kinds = None
    if "kind" in me.attributes and len(me.attributes["kind"].data) == len(polys):
        kinds = [a.value for a in me.attributes["kind"].data]
    ev.to_mesh_clear()
    return pts, polys, kinds


def garment_shell(obj, interior_rings=2, inset=0.0):
    pts, polys, kinds = eval_points(obj)
    if inset:
        vn = [Vector() for _ in pts]
        for f in polys:
            a, b, c = pts[f[0]], pts[f[1]], pts[f[2]]
            nn = (b - a).cross(c - a)
            for v in f:
                vn[v] += nn
        pts = [p - n.normalized() * inset for p, n in zip(pts, vn)]
    edge_count = {}
    for f in polys:
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            key = (min(a, b), max(a, b))
            edge_count[key] = edge_count.get(key, 0) + 1
    adj = {}
    for (a, b) in edge_count:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    near = {v for (a, b), c in edge_count.items() if c == 1 for v in (a, b)}
    frontier = set(near)
    for _ in range(interior_rings):
        nf = set()
        for v in frontier:
            for w in adj.get(v, ()):
                if w not in near:
                    near.add(w)
                    nf.add(w)
        frontier = nf
    interior = [i for i, f in enumerate(polys)
                if not any(v in near for v in f) and (kinds is None or kinds[i] in (0, 1))]
    tree = BVHTree.FromPolygons(pts, polys)
    itree = BVHTree.FromPolygons(pts, [polys[i] for i in interior])
    return {"pts": pts, "polys": polys, "tree": tree, "itree": itree, "interior": len(interior)}


def poke_check(points, shell, reach=0.03, tol=0.0005):
    """Count points lying OUTSIDE a garment's interior surface (in front of its outward normal,
    within `reach`, projecting onto the face rather than past an edge)."""
    bad, worst = 0, 0.0
    for p in points:
        loc, n, _, d = shell["itree"].find_nearest(p, reach)
        if loc is None:
            continue
        v = p - loc
        s = v.dot(n)
        if s > tol and v.length > 1e-9 and s / v.length > 0.82:
            bad += 1
            worst = max(worst, s)
    return {"outside": bad, "worst_mm": round(worst * 1000, 2), "tested": len(points)}


def inside_check(shell, body_tree, reach=0.03, tol=0.0005):
    """Count garment vertices lying INSIDE the visible body surface."""
    bad, worst = 0, 0.0
    for p in shell["pts"]:
        loc, n, _, d = body_tree.find_nearest(p, reach)
        if loc is None:
            continue
        v = p - loc
        s = v.dot(n)
        if s < -tol and v.length > 1e-9 and -s / v.length > 0.82:
            bad += 1
            worst = max(worst, -s)
    return {"inside_body": bad, "worst_mm": round(worst * 1000, 2), "tested": len(shell["pts"])}


def tris(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    m = ev.to_mesh()
    m.calc_loop_triangles()
    n = len(m.loop_triangles)
    ev.to_mesh_clear()
    return n


# ----------------------------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------------------------
def build(out_blend, report_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert addon_utils.enable("bl_ext.user_default.mpfb", default_set=True, handle_error=None) is not None
    from bl_ext.user_default.mpfb.services.humanservice import HumanService
    from bl_ext.user_default.mpfb.services.targetservice import TargetService
    from bl_ext.user_default.mpfb.services.locationservice import LocationService
    from bl_ext.user_default.mpfb.services.clothesservice import ClothesService
    from bl_ext.user_default.mpfb.entities.objectproperties import HumanObjectProperties
    from bl_ext.user_default.mpfb.entities.meshcrossref import MeshCrossRef
    M = {"HumanService": HumanService, "ClothesService": ClothesService, "LocationService": LocationService}
    DATA = LocationService.get_user_data()
    REP = {"mpfb_user_data": DATA}
    pal = json.load(open(os.path.join(HERE, "palette.json")))["regions"]

    macro = TargetService.get_default_macro_info_dict()
    macro.update(MACRO)
    macro["race"] = dict(RACE)
    bm = HumanService.create_human(macro_detail_dict=macro)
    bm.name = "Protagonist"

    def bounds(obj):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        zs = [(obj.matrix_world @ v.co).z for v in me.vertices]
        ev.to_mesh_clear()
        return min(zs), max(zs)

    lo_v, hi_v = 0.5, 1.0
    for it in range(8):
        lo, hi = bounds(bm)
        h = hi - lo
        if abs(h - TARGET_H) < 0.004:
            break
        if h < TARGET_H:
            lo_v = macro["height"]
        else:
            hi_v = macro["height"]
        macro["height"] = (lo_v + hi_v) / 2
        HumanObjectProperties.set_value("height", macro["height"], entity_reference=bm)
        TargetService.reapply_macro_details(bm)

    tdir = LocationService.get_mpfb_data("targets")
    applied, missing = {}, []
    for rel, w in LIKENESS.items():
        p = os.path.join(tdir, rel + ".target.gz")
        if os.path.exists(p):
            TargetService.load_target(bm, p, weight=w)
            applied[rel] = w
        else:
            missing.append(rel)
    REP["likeness_targets"] = applied
    REP["likeness_missing"] = missing
    lo, hi = bounds(bm)
    bm.location.z -= lo
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    bm.select_set(True)
    bpy.context.view_layer.objects.active = bm
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    lo, hi = bounds(bm)
    REP["height_m"] = round(hi - lo, 4)
    REP["macro"] = macro

    rig = HumanService.add_builtin_rig(bm, "game_engine", import_weights=True)
    REP["rig"] = {"name": "game_engine", "bones": len(rig.data.bones)}

    # xref cache (topology only, shape independent) so MakeClothes matching reads it
    cache_dir = LocationService.get_user_cache("basemesh_xref")
    if not os.path.exists(os.path.join(cache_dir, "faces_by_vertex.npy")):
        MeshCrossRef(bm, after_modifiers=False, build_faces_by_group_reference=True, cache_dir=cache_dir,
                     write_cache=True, read_cache=False)

    # skin + body parts (CC0 MakeHuman system assets)
    HumanService.set_character_skin(os.path.join(DATA, SKIN), bm, skin_type="ENHANCED_SSS")
    objs = {}
    import glob
    for rel, kind in BODYPARTS:
        f = sorted(glob.glob(os.path.join(DATA, rel, "*.mhclo")))[0]
        o = HumanService.add_mhclo_asset(f, bm, asset_type=kind, subdiv_levels=0, material_type="MAKESKIN")
        objs[os.path.basename(rel) if kind == "Hair" else kind.lower()] = o
    for rel in CC0_CLOTHES:
        f = sorted(glob.glob(os.path.join(DATA, rel, "*.mhclo")))[0]
        o = HumanService.add_mhclo_asset(f, bm, asset_type="Clothes", subdiv_levels=0, material_type="MAKESKIN")
        objs[os.path.basename(rel)] = o
    tee, boots = objs["elvs_crude_t-shirt_male"], objs["toigo_ankle_boots_male"]

    body = Body(bm, rig)
    REP["landmarks"] = {k: [round(c, 4) for c in body.bones[k][0]] for k in
                        ("pelvis", "neck_01", "upperarm_l", "lowerarm_l", "hand_l", "calf_l", "foot_l")}

    # --- jeans (under the tee hem, over the boots) ---
    tee_top_layer = Layer(tee, body, 0.004, zmax=1.09)
    boot_layer = Layer(boots, body, 0.006)
    jb, j_st, j_zp, j_region, j_info = build_jeans(body, [tee_top_layer, boot_layer], REP)
    j_del = delete_verts_for(body, None, j_region)
    orient_outward(jb, body)
    jobj = bm_to_object(jb, "forge_jeans_build", j_st, j_zp)
    jb.free()
    finish_uvs(jobj, [])
    REP.setdefault("uv", {})["jeans_build"] = uv_report(jobj.data)
    j_path, j_side = write_mhclo_garment(M, bm, jobj, "jeans", j_del, REP)

    jeans = HumanService.add_mhclo_asset(j_path, bm, asset_type="Clothes", subdiv_levels=0, material_type="MAKESKIN")
    jeans.name = "Protagonist.jeans"
    apply_garment_materials(jeans, j_side, [mat_denim(pal), mat_edge("forge_denim_edge", (30, 30, 35), 0.85),
                                            mat_edge("forge_denim_inner", (26, 26, 30), 0.9)])

    # --- jacket (over tee + jeans) ---
    body = Body(bm, rig)
    jl = [Layer(tee, body, 0.007), Layer(jeans, body, 0.008)]
    kb, k_st, k_zp, k_region, k_info = build_jacket(body, jl, REP)
    k_del = delete_verts_for(body, None, k_region)
    orient_outward(kb, body)
    kobj = bm_to_object(kb, "forge_jacket_build", k_st, k_zp)
    kb.free()
    finish_uvs(kobj, [])
    REP["uv"]["jacket_build"] = uv_report(kobj.data)
    k_path, k_side = write_mhclo_garment(M, bm, kobj, "jacket", k_del, REP)
    jacket = HumanService.add_mhclo_asset(k_path, bm, asset_type="Clothes", subdiv_levels=0, material_type="MAKESKIN")
    jacket.name = "Protagonist.jacket"
    apply_garment_materials(jacket, k_side, [mat_leather(pal), mat_metal(), mat_edge("forge_leather_edge", (30, 24, 22), 0.5)])
    REP["garment_info"] = {"jeans": j_info, "jacket": k_info}

    # fitting fidelity: MPFB refit vs the modelled positions
    for key, obj, side in (("jeans", jeans, j_side), ("jacket", jacket, k_side)):
        pts, _, _ = eval_points(obj)
        dev = max((Vector(a) - b).length for a, b in zip(side["co"], pts))
        REP.setdefault("refit_max_dev_mm", {})[key] = round(dev * 1000, 3)
        REP["uv"][key + "_fitted"] = uv_report(obj.data)

    # palette tints on the CC0 assets
    tint(tee, tuple(pal["shirt"]["srgb"]), gain=2.0)
    tint(boots, (40, 36, 34), rough=0.45, gain=2.0)
    for h in HAIR:
        tint(objs[h], tuple(pal["hair"]["srgb"]), rough=0.55, gain=3.0)
    tint(objs["eyebrows"], (30, 26, 24), gain=2.0)
    tint(objs["eyelashes"], (22, 20, 20), gain=2.0)
    desaturate(objs["eyes"], 0.55)   # brown.mhmat iris reads red-brown under the warm key
    # MakeHuman brow/lash cards sit <1 mm off the skin (MH z_depth convention) and lose the EEVEE
    # depth test; lift them along their own normals with a modifier so a refit keeps the lift.
    for key, lift in (("eyebrows", 0.0015), ("eyelashes", 0.0008)):
        o = objs[key]
        pts, polys, _ = eval_points(o)
        sgn = 0.0
        for f in polys:
            a, b, c = pts[f[0]], pts[f[1]], pts[f[2]]
            nn = (b - a).cross(c - a)
            s, loc, bn = body.sd(a)
            sgn += nn.dot(bn)
        m = o.modifiers.new("forge_card_lift", "DISPLACE")
        m.mid_level = 0.0
        m.strength = lift if sgn >= 0 else -lift
        REP.setdefault("card_lift_m", {})[key] = m.strength

    # --- checks (rest A-pose) ---
    dg = bpy.context.evaluated_depsgraph_get()
    ev = bm.evaluated_get(dg)
    me = ev.to_mesh()
    vis = [bm.matrix_world @ v.co for v in me.vertices]
    vtree = BVHTree.FromPolygons(vis, [tuple(p.vertices) for p in me.polygons])
    ev.to_mesh_clear()
    shells = {k: garment_shell(o) for k, o in (("jacket", jacket), ("jeans", jeans), ("tee", tee))}
    tee_pts = shells["tee"]["pts"]
    boot_pts, _, _ = eval_points(boots)
    jeans_pts = shells["jeans"]["pts"]
    REP["fit_checks"] = {
        "body_outside_jacket": poke_check(vis, shells["jacket"]),
        "body_outside_jeans": poke_check(vis, shells["jeans"]),
        "body_outside_tee": poke_check(vis, shells["tee"]),
        "jacket_inside_body": inside_check(shells["jacket"], vtree),
        "jeans_inside_body": inside_check(shells["jeans"], vtree),
        "tee_outside_jacket": poke_check(tee_pts, shells["jacket"]),
        "tee_outside_jeans": poke_check(tee_pts, shells["jeans"]),
        "jeans_outside_jacket": poke_check(jeans_pts, shells["jacket"]),
        "boots_outside_jeans": poke_check(boot_pts, shells["jeans"]),
        "interior_faces": {k: s["interior"] for k, s in shells.items()},
        # negative control: the same test against the jacket/jeans pulled 25 mm inward must fire
        "control_body_outside_jacket_inset25mm": poke_check(vis, garment_shell(jacket, inset=0.025)),
        "control_body_outside_jeans_inset25mm": poke_check(vis, garment_shell(jeans, inset=0.025)),
    }

    counts = {}
    for o in bpy.data.objects:
        if o.type == "MESH":
            counts[o.name] = tris(o)
    counts["TOTAL"] = sum(counts.values())
    REP["tris"] = counts
    REP["objects"] = [(o.name, o.type, o.parent.name if o.parent else None) for o in bpy.data.objects]
    REP["masks_on_body"] = [(m.name, m.vertex_group) for m in bm.modifiers if m.type == "MASK"]
    os.makedirs(os.path.dirname(out_blend), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out_blend, compress=True)
    json.dump(REP, open(report_path, "w"), indent=1, default=str)
    print("REPORT", json.dumps({k: REP.get(k) for k in ("height_m", "tris", "fit_checks", "refit_max_dev_mm")}, indent=1, default=str))


# ----------------------------------------------------------------------------------------------
# render (same look-dev class as mpfb_pilot_*: EEVEE, key/fill/rim suns, grey world, dark floor)
# ----------------------------------------------------------------------------------------------
def render(prefix, mode="all"):
    prefix = os.path.abspath(prefix)   # Blender resolves bare relative render paths against the drive root
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((1e9, 1e9, 1e9))
    hi = -lo
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        for v in me.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
        ev.to_mesh_clear()
    height = hi.z - lo.z
    centre = (lo + hi) / 2
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    try:
        scene.eevee.taa_render_samples = 64
    except Exception:
        pass
    world = bpy.data.worlds.new("look")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.6

    def light(name, energy, rot, color=(1, 1, 1)):
        d = bpy.data.lights.new(name, "SUN")
        d.energy = energy
        d.color = color
        o = bpy.data.objects.new(name, d)
        scene.collection.objects.link(o)
        o.rotation_euler = [math.radians(a) for a in rot]

    light("key", 3.2, (50, 0, 330))
    light("fill", 1.0, (65, 0, 395), (0.85, 0.9, 1.0))
    light("rim", 2.0, (60, 0, 190))
    fme = bpy.data.meshes.new("floor")
    fme.from_pydata([(-3, -3, 0), (3, -3, 0), (3, 3, 0), (-3, 3, 0)], [], [(0, 1, 2, 3)])
    floor = bpy.data.objects.new("floor", fme)
    scene.collection.objects.link(floor)
    fm = bpy.data.materials.new("floor")
    fm.use_nodes = True
    fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
    fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
    fme.materials.append(fm)
    cam_d = bpy.data.cameras.new("cam")
    cam_d.lens = 50
    cam = bpy.data.objects.new("cam", cam_d)
    scene.collection.objects.link(cam)
    scene.camera = cam
    target = Vector((centre.x, centre.y, lo.z + height * 0.5))
    dist = height * 1.75

    def aim(angle, elev=0.08, tgt=None, d=None):
        tgt = tgt or target
        d = d or dist
        a = math.radians(angle)
        cam.location = tgt + Vector((math.sin(a) * d, -math.cos(a) * d, height * elev))
        cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()

    def still(tag, res=(1024, 1024)):
        scene.render.resolution_x, scene.render.resolution_y = res
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = "%s_%s.png" % (prefix, tag)
        bpy.ops.render.render(write_still=True)
        print("WROTE", scene.render.filepath)

    if mode in ("stills", "all"):
        aim(0.0)
        still("front")
        aim(40.0)
        still("threequarter")
        aim(180.0)
        still("back")
    if mode in ("stills", "all", "face"):
        cam_d.lens = 85
        aim(25.0, elev=0.0, tgt=Vector((centre.x, centre.y, hi.z - 0.12)), d=0.95)
        still("face")
        cam_d.lens = 50
    if mode in ("stills", "all", "fit"):
        # A-pose garment-fit closeup: collar, zip, chest pocket, sleeve join
        cam_d.lens = 85
        aim(30.0, elev=0.02, tgt=Vector((0.08, centre.y, 1.38)), d=1.2)
        still("jacket_fit")
        cam_d.lens = 50
    if mode in ("turntable", "all"):
        n = 72
        scene.frame_start, scene.frame_end = 1, n
        scene.render.fps = 24
        scene.render.resolution_x = scene.render.resolution_y = 640
        pivot = bpy.data.objects.new("pivot", None)
        scene.collection.objects.link(pivot)
        pivot.location = target
        aim(0.0)
        cam.parent = pivot
        cam.location = cam.location - target
        pivot.rotation_euler = (0, 0, 0)
        pivot.keyframe_insert("rotation_euler", frame=1)
        pivot.rotation_euler = (0, 0, math.radians(360 * (n - 1) / n))
        pivot.keyframe_insert("rotation_euler", frame=n)
        act = pivot.animation_data.action
        try:
            for layer in act.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for fc in bag.fcurves:
                            for k in fc.keyframe_points:
                                k.interpolation = "LINEAR"
        except AttributeError:
            for fc in getattr(act, "fcurves", []):
                for k in fc.keyframe_points:
                    k.interpolation = "LINEAR"
        if hasattr(scene.render.image_settings, "media_type"):
            scene.render.image_settings.media_type = "VIDEO"
        scene.render.image_settings.file_format = "FFMPEG"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.ffmpeg.constant_rate_factor = "HIGH"
        scene.render.filepath = prefix + "_turntable.mp4"
        bpy.ops.render.render(animation=True)
        print("WROTE", scene.render.filepath)


if __name__ == "__main__":
    if ARGS and ARGS[0] == "build":
        build(ARGS[1], ARGS[2])
    elif ARGS and ARGS[0] == "render":
        render(ARGS[1], ARGS[2] if len(ARGS) > 2 else "all")
    else:
        print(__doc__)
