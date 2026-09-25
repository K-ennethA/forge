"""Custom protagonist on the MPFB2 base (draft mode, docs/lane-conventions.md "Two speeds").

Builds, end to end and headless:
  * the approved MPFB2 base body (pilot macros, 1.8825 m) + likeness face targets toward
    design/refs/form-a-front.png,
  * CC0 MakeHuman system assets: textured skin, low-poly eyes, card eyebrows/eyelashes,
  * forge-modelled garments written as real MPFB clothes (MHCLO + OBJ via ClothesService's
    MakeClothes matching, with delete groups) and fitted back through HumanService.add_mhclo_asset.
    v2 (2026-09-25 artist review, "not like a skin suit") DRAPES them: each garment gets its own
    silhouette first (hulled slices hung from the shoulder line / seat, sleeve cylinders, straight
    leg tubes, crotch gusset), then the fit binding - the MHCLO offsets keep the looseness:
      - boxy zip-front leather jacket (open front standing off the chest, collar, zip strips,
        stitching, chest zip pocket, sleeves bunching at elbow and cuff),
      - straight-leg jeans (waistband, fly, pockets, yoke, knee bunching, stacking over the boots),
  * a forge-modelled low-poly hair mass (scalp shell + clumped directional locks), fitted as an
    MPFB hair asset (v1's CC0 cap + alpha cards were rejected),
  * low-poly CC0 ankle boots, a CC0 tee, palette colours from palette.json,
  * v3 (2026-09-25 "outfit v2 verdict") DIFFERENTIAL fit: hulled-slice sleeves tight over the
    biceps, easing through the elbow, a little tighter than the body drape to a snug cuff (torso
    drape unchanged); the jeans seat as one designed, monotone ease curve from the fitted seat top
    into a monotone straight-leg taper,
  * garment poke-through checks + the PER-ZONE MINIMUM-CLEARANCE gate (CLEARANCE_ZONES: declared
    zones, floors + median bands, v2 rejected baselines; the seat ease curve scored for
    monotonic smoothness) in the A-pose + hair crown coverage + tri breakdown + UV metrics.

build     : blender --background --factory-startup --python build_custom.py -- build <out.blend> <report.json>
render    : blender --background --factory-startup <out.blend> --python build_custom.py -- render <prefix> [stills|turntable|all]
clearance : blender --background --factory-startup <any.blend> --python build_custom.py -- clearance <out.json>
sidebyside: blender --background --factory-startup --python build_custom.py -- sidebyside <left.png> <right.png> <out.png>
grid      : blender --background --factory-startup --python build_custom.py -- grid <out.png> <a.png> <b.png> [...]   (2 columns)
render modes: all | stills | turntable | face | fit (jacket_closeup + arm) | compare (arm + seat)
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
             ("eyelashes/eyelashes01", "Eyelashes")]
# hair: v1's CC0 cap (short04) + alpha cards (cortu_short_messy_hair) was rejected by the artist
# 2026-09-25; v2 hair is modelled in build_hair() and fitted as an MPFB hair asset.
# Loose garments expose more of the hidden body through their openings: keep more skin rings
# around each opening (hem, cuffs, collar) out of the delete group.
DELETE_MARGIN_RINGS = 4
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


def _smoother(e0, e1, x):
    """C2 smootherstep: zero slope AND curvature at both ends (no visible kink where a blend ends)."""
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * t * (t * (6 * t - 15) + 10)


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

    def near(self, p):
        """Nearest skin point, its normal and the dominant bone of that skin face."""
        loc, n, idx, _ = self.tree.find_nearest(p)
        return loc, n, self.fdom[self.body_faces[idx]]

    def part_tree(self, doms, zlo=-1e9, zhi=1e9):
        fs = [self.faces[fi] for fi in self.body_faces
              if self.fdom[fi] in doms and zlo <= self.center(fi).z <= zhi]
        return BVHTree.FromPolygons(self.co, fs)


class Layer:
    """A garment already on the body: a candidate point must sit `margin` further from the body
    than the layer does locally (signed distance measured against the body, whose normals are
    reliable, instead of trusting the CC0 garment's own winding). `margin` may be a function of
    the candidate point (v3: the tight sleeve sits a leather thickness over the tee sleeve)."""

    def __init__(self, obj, body, margin, reach=0.035, zmax=None):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        co = [obj.matrix_world @ v.co for v in me.vertices]
        self.tree = BVHTree.FromPolygons(co, [tuple(p.vertices) for p in me.polygons])
        ev.to_mesh_clear()
        self.body, self.margin, self.reach, self.zmax = body, margin, reach, zmax

    def m(self, p):
        return self.margin(p) if callable(self.margin) else self.margin

    def need(self, p):
        if self.zmax is not None and p.z > self.zmax:
            return None
        loc, _, _, d = self.tree.find_nearest(p, self.reach)
        if loc is None:
            return None
        s_layer, _, _ = self.body.sd(loc)
        return s_layer + self.m(p)

    def push(self, p):
        """Clear the layer along ITS OWN outward normal (oriented away from the body): where a
        layer bridges a skin crease (the tee across the armpit) the body normal points into the
        crease and the need() test alone cannot lift a garment over the bridge."""
        if self.zmax is not None and p.z > self.zmax:
            return p
        loc, n, _, _ = self.tree.find_nearest(p, self.reach)
        if loc is None:
            return p
        _, bloc, bn = self.body.sd(loc)
        if n.dot(bn) < 0:
            n = -n
        s = (p - loc).dot(n)
        mg = self.m(p)
        if s < mg:
            return p + n * (mg - s)
        return p


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
            p = p + n * (need - s)
            moved += 1
        for L in layers:
            p = L.push(p)
        P[i] = p
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
# DRAPE (v2, 2026-09-25 "not like a skin suit"): each garment gets its OWN silhouette first -
# convex-hulled slices that fall straight down from where the cloth hangs (shoulder line / seat),
# sleeve cylinders and leg tubes wider than the limb - and only then the MPFB fit binding (MHCLO
# offsets store any distance, so looseness survives the refit). v1 offset the skin along its
# normals and therefore tracked every curve by construction.
# ----------------------------------------------------------------------------------------------
def _hull2d(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _ray_poly(poly, ang):
    """Distance from the origin along `ang` to the boundary of an origin-containing polygon."""
    d0, d1 = math.cos(ang), math.sin(ang)
    best = None
    n = len(poly)
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        e0, e1 = b[0] - a[0], b[1] - a[1]
        den = d0 * e1 - d1 * e0
        if abs(den) < 1e-12:
            continue
        t = (a[0] * e1 - a[1] * e0) / den
        s = (a[0] * d1 - a[1] * d0) / den
        if t > 0 and -1e-9 <= s <= 1 + 1e-9:
            best = t if best is None else min(best, t)
    return best


class Envelope:
    """Radial silhouette around a vertical axis (ax, ay): per horizontal slice the convex hull of
    the outermost skin (cloth bridges every concavity: spine groove, sternum, waist), then hung:
    below z_fall a slice is never narrower than any slice between it and z_fall (the cloth falls
    straight from the widest point above - chest/shoulder blades for the jacket, seat for jeans)."""

    def __init__(self, tree, ax, ay, zs, z_fall, n_theta=90, rmax=0.5):
        self.ax, self.ay, self.zs = ax, ay, list(zs)
        self.n = n_theta
        self.th = [2 * math.pi * k / n_theta for k in range(n_theta)]
        R = []
        for z in self.zs:
            pts = []
            for a in self.th:
                d = Vector((math.cos(a), math.sin(a), 0.0))
                o = Vector((ax, ay, z)) + d * rmax
                hit, _, _, dist = tree.ray_cast(o, -d, rmax)
                if hit is not None:
                    r = rmax - dist
                    pts.append((r * math.cos(a), r * math.sin(a)))
            hull = _hull2d(pts) if len(pts) >= 8 else None
            R.append([(_ray_poly(hull, a) if hull else None) for a in self.th])
        valid = [i for i, row in enumerate(R) if all(r is not None for r in row)]
        for i, row in enumerate(R):
            if i not in valid:
                j = min(valid, key=lambda v: abs(v - i))
                R[i] = list(R[j])
        top = max(i for i, z in enumerate(self.zs) if z <= z_fall)
        for i in range(top - 1, -1, -1):
            R[i] = [max(a, b) for a, b in zip(R[i], R[i + 1])]
        self.R = R
        self.dz = self.zs[1] - self.zs[0]

    def r(self, z, a):
        fi = (z - self.zs[0]) / self.dz
        i0 = max(0, min(len(self.zs) - 2, int(math.floor(fi))))
        tz = max(0.0, min(1.0, fi - i0))
        fa = (a % (2 * math.pi)) / (2 * math.pi) * self.n
        k0 = int(math.floor(fa)) % self.n
        k1 = (k0 + 1) % self.n
        ta = fa - math.floor(fa)
        r0 = self.R[i0][k0] * (1 - ta) + self.R[i0][k1] * ta
        r1 = self.R[i0 + 1][k0] * (1 - ta) + self.R[i0 + 1][k1] * ta
        return r0 * (1 - tz) + r1 * tz

    def target(self, b, ease):
        a = math.atan2(b.y - self.ay, b.x - self.ax)
        rb = math.hypot(b.x - self.ax, b.y - self.ay)
        R = max(self.r(b.z, a), rb) + ease
        d = Vector((math.cos(a), math.sin(a), 0.0))
        return Vector((self.ax, self.ay, b.z)) + d * R, d


def _bins(pts_t, lo, hi, n):
    out = [[] for _ in range(n)]
    for t, p in pts_t:
        k = int((t - lo) / (hi - lo) * n)
        if 0 <= k < n:
            out[k].append(p)
    return out


# ----------------------------------------------------------------------------------------------
# v3 SLEEVE (2026-09-25 artist, "outfit v2 verdict": "the bicep area of the leather jacket should be
# tight and not loose like the rest of the material since they have muscular build and a little
# tighter on the forearms and wrist but not skin tight"). v2's sleeve was a straight cylinder of the
# widest arm section + 19 mm (bicep median clearance ~30-39 mm: loose). v3 is DIFFERENTIAL: per
# slice along each limb segment the cross-section is the convex hull of the arm skin (the leather
# bridges the small concavities between muscle bellies but reads the muscle), offset by an ease
# PROFILE along the arm - tight over the biceps, easing out through the elbow (bunching kept),
# a little tighter than the body drape along the forearm, tapering snug to the cuff. The zone
# boundaries are the SAME declared rules the clearance gate scores (jacket_zone).
# ----------------------------------------------------------------------------------------------
SLEEVE_EASE = {"bicep": 0.0050, "elbow": 0.0120, "forearm": 0.0095, "wrist": 0.0085}
# enforce() floors per zone (skin) and the tee margin under the sleeve (a leather thickness over
# the CC0 tee sleeve instead of the torso's 7 mm air layer) - each a little above the gate floor
# (MPFB refit drifts <= 0.2 mm, see refit_max_dev_mm)
SLEEVE_DMIN = {"bicep": 0.0046, "elbow": 0.0046, "forearm": 0.0086, "wrist": 0.0086}
SLEEVE_TEE_MARGIN = 0.0015
ZONE_T = {"bicep_end": 0.85, "elbow_end": 0.15, "forearm_end": 0.75}   # t along shoulder->elbow / elbow->wrist
HAND_PREFIX = ("hand", "thumb", "index", "middle", "ring", "pinky")


def _seg_t(bones, a, b, p):
    P0, P1 = bones[a][0], bones[b][0]
    A = P1 - P0
    return (p - P0).dot(A) / A.length_squared


def jacket_zone(b, dom, bones):
    """Declared jacket zone of a skin point b (dominant bone dom): bicep / elbow / forearm / wrist
    along the arm bones, torso otherwise. Shared by the build (ease, floors) and the gate."""
    if dom in ARM_UP:
        s = dom[-1]
        return "bicep" if _seg_t(bones, "upperarm_" + s, "lowerarm_" + s, b) < ZONE_T["bicep_end"] else "elbow"
    if dom in ARM_LO:
        s = dom[-1]
        t = _seg_t(bones, "lowerarm_" + s, "hand_" + s, b)
        if t < ZONE_T["elbow_end"]:
            return "elbow"
        return "forearm" if t < ZONE_T["forearm_end"] else "wrist"
    if dom and dom.startswith(HAND_PREFIX):
        return "wrist"
    return "torso"


def _poly_centroid(poly):
    a = cx = cy = 0.0
    n = len(poly)
    for k in range(n):
        x0, y0 = poly[k]
        x1, y1 = poly[(k + 1) % n]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    return (cx / (3 * a), cy / (3 * a))


class Sleeve:
    """v3 differential sleeve: two hulled-slice tubes (upper arm, forearm) blended across the elbow
    bisector plane. Slice k of a segment = convex hull of the arm skin within the slab around t_k
    (projected into the plane normal to the bone), re-centred on a smoothed centreline; the cloth
    radius at (t, angle) = hull ray + ease(t). The forearm tube runs on past the region's cuff to
    the wrist (extra length -> cuff stack), as in v2."""
    NB, NTH = 25, 48

    def __init__(self, body, s, t_region_end=0.96):
        self.S = body.bones["upperarm_" + s][0]
        self.E = body.bones["lowerarm_" + s][0]
        self.W = body.bones["hand_" + s][0]
        self.t_end = t_region_end
        e = SLEEVE_EASE
        ease_up = lambda t: e["bicep"] + (e["elbow"] - e["bicep"]) * _smooth(0.70, 1.0, t)
        ease_lo = lambda t: (e["elbow"] + (e["forearm"] - e["elbow"]) * _smooth(0.0, 0.30, t)
                             + (e["wrist"] - e["forearm"]) * _smooth(0.55, 0.90, t))
        idx = [i for i in range(len(body.co)) if body.in_body[i] and body.dom[i] in ("upperarm_" + s, "lowerarm_" + s)]
        arm_tree = body.part_tree({"upperarm_" + s, "lowerarm_" + s})
        self.segs = []
        for P0, P1, efn in ((self.S, self.E, ease_up), (self.E, self.W, ease_lo)):
            A = P1 - P0
            L = A.length
            A = A / L
            e1 = A.cross(Vector((0.0, 0.0, 1.0)))
            if e1.length < 1e-6:
                e1 = A.cross(Vector((1.0, 0.0, 0.0)))
            e1.normalize()
            e2 = A.cross(e1).normalized()
            ts = [-0.1 + 1.2 * k / (self.NB - 1) for k in range(self.NB)]
            half = 1.5 * (ts[1] - ts[0])
            loc = []
            for i in idx:
                r = body.co[i] - P0
                t = r.dot(A) / L
                u = r - A * r.dot(A)
                if u.length < 0.13:
                    loc.append((t, u.dot(e1), u.dot(e2)))
            # the basemesh arm rings are sparse and oblique to the bone: a vertex slab gives sliver
            # hulls. Sample each slice's true cross-section by casting rays in the slice plane
            # inward onto the arm surface (as Envelope does for the torso), then hull the hits.
            hulls = []
            for tk in ts:
                near_ = [(x, y) for t, x, y in loc if abs(t - tk) <= half]
                if len(near_) < 6:
                    hulls.append(None)
                    continue
                c0 = (sum(x for x, _ in near_) / len(near_), sum(y for _, y in near_) / len(near_))
                O = P0 + A * (tk * L) + e1 * c0[0] + e2 * c0[1]
                hits = []
                for a in range(self.NTH):
                    ang = 2 * math.pi * a / self.NTH
                    dv = e1 * math.cos(ang) + e2 * math.sin(ang)
                    hit, _, _, _ = arm_tree.ray_cast(O + dv * 0.13, -dv, 0.13)
                    if hit is not None:
                        q = hit - P0
                        hits.append((q.dot(e1), q.dot(e2)))
                hulls.append(_hull2d(hits) if len(hits) >= 0.75 * self.NTH else None)
            good = [k for k, h in enumerate(hulls) if h and len(h) >= 3]
            for k in range(self.NB):
                if not (hulls[k] and len(hulls[k]) >= 3):
                    hulls[k] = hulls[min(good, key=lambda g: abs(g - k))]
            raw_c = [_poly_centroid(h) for h in hulls]
            cen = []
            for k in range(self.NB):
                ks = range(max(0, k - 2), min(self.NB, k + 3))
                cen.append((sum(raw_c[j][0] for j in ks) / len(ks), sum(raw_c[j][1] for j in ks) / len(ks)))
            th = [2 * math.pi * a / self.NTH for a in range(self.NTH)]
            R = []
            for k, h in enumerate(hulls):
                cx, cy = cen[k]
                sh = [(x - cx, y - cy) for x, y in h]
                row = [_ray_poly(sh, a) for a in th]
                if any(r is None for r in row):          # smoothed centre fell outside: use the raw one
                    cen[k] = raw_c[k]
                    sh = [(x - raw_c[k][0], y - raw_c[k][1]) for x, y in h]
                    row = [_ray_poly(sh, a) or 0.0 for a in th]
                R.append(row)
            # one [1 2 1] pass along the arm: the hull rows are slab samples, not a surface
            Rs = [[(R[max(0, k - 1)][a] + 2 * R[k][a] + R[min(self.NB - 1, k + 1)][a]) / 4 for a in range(self.NTH)]
                  for k in range(self.NB)]
            self.segs.append({"P0": P0, "A": A, "L": L, "e1": e1, "e2": e2, "ts": ts, "cen": cen, "R": Rs,
                              "ease": efn})
        up, lo = self.segs
        bic = [max(up["R"][k]) for k, t in enumerate(up["ts"]) if 0.35 <= t <= 0.8]
        fore = [max(lo["R"][k]) for k, t in enumerate(lo["ts"]) if 0.15 <= t <= 0.75]
        self.info = {"hull_r_max_bicep_mm": round(max(bic) * 1000, 1), "hull_r_max_forearm_mm": round(max(fore) * 1000, 1),
                     "hull_r_wrist_mm": round(max(lo["R"][min(range(self.NB), key=lambda k: abs(lo["ts"][k] - 1.0))]) * 1000, 1),
                     "ease_mm": {k: round(v * 1000, 1) for k, v in SLEEVE_EASE.items()}}
        self.elbow_n = (up["A"] + lo["A"]).normalized()

    def _look(self, sg, t):
        f = (t - sg["ts"][0]) / (sg["ts"][1] - sg["ts"][0])
        k = max(0, min(self.NB - 2, int(math.floor(f))))
        return k, max(0.0, min(1.0, f - k))

    def _centre(self, sg, t):
        k, w = self._look(sg, t)
        cx = sg["cen"][k][0] * (1 - w) + sg["cen"][k + 1][0] * w
        cy = sg["cen"][k][1] * (1 - w) + sg["cen"][k + 1][1] * w
        return sg["P0"] + sg["A"] * (t * sg["L"]) + sg["e1"] * cx + sg["e2"] * cy

    def _radius(self, sg, t, ang):
        k, w = self._look(sg, t)
        fa = (ang % (2 * math.pi)) / (2 * math.pi) * self.NTH
        a0 = int(math.floor(fa)) % self.NTH
        a1 = (a0 + 1) % self.NTH
        ta = fa - math.floor(fa)
        r0 = sg["R"][k][a0] * (1 - ta) + sg["R"][k][a1] * ta
        r1 = sg["R"][k + 1][a0] * (1 - ta) + sg["R"][k + 1][a1] * ta
        return r0 * (1 - w) + r1 * w

    def _seg(self, sg, b, n, lower):
        t = (b - sg["P0"]).dot(sg["A"]) / sg["L"]
        ta = t
        if lower and t > 0.7:
            ta = 0.7 + (t - 0.7) * (1.0 - 0.7) / (self.t_end - 0.7)   # stretch the cuff to the wrist
        u = b - self._centre(sg, t)
        u -= sg["A"] * u.dot(sg["A"])
        ru = u.length
        if ru < 1e-6:
            u = n - sg["A"] * n.dot(sg["A"])
        ang = math.atan2(u.dot(sg["e2"]), u.dot(sg["e1"]))
        d = (sg["e1"] * math.cos(ang) + sg["e2"] * math.sin(ang)).normalized()
        ez = sg["ease"](t)
        R = max(self._radius(sg, ta, ang) + ez, ru + 0.6 * ez)    # never inside a bulge the hull missed
        return self._centre(sg, ta) + d * R, d, t

    def target(self, b, n):
        tu, uu, t_up = self._seg(self.segs[0], b, n, False)
        tl, ul, t_lo = self._seg(self.segs[1], b, n, True)
        w = _smooth(-0.035, 0.035, (b - self.E).dot(self.elbow_n))
        p = tu.lerp(tl, w)
        u = uu.lerp(ul, w).normalized()
        return p, u, (t_up, t_lo, w)



class LegTube:
    """A jeans leg = a straight tube from the hip to the ankle around the leg's centroid line (not
    leg-shaped): radius from the thigh at the crotch tapering to the knee, then straight to a hem
    wide enough to stack over the boot, never closer than `ease_min` to the widest local section."""

    def __init__(self, body, s, z_c, hem, knee_z, ease_top, ease_low, bridge=0.12, monotone=False):
        # the skin rings of the basemesh leg are sparse (~7 verts per 2 cm slice): centre each
        # slice on its bounding-box middle (a vertex centroid is biased toward the denser side),
        # then smooth that centreline heavily - the tube follows the leg's overall line, not its
        # knee/calf wobble
        idx = [i for i in range(len(body.co)) if body.in_body[i] and body.dom[i] in ("thigh_" + s, "calf_" + s)]
        self.z0, self.z1, self.nb = hem - 0.02, z_c + 0.02, 30
        bins = _bins([(body.co[i].z, body.co[i]) for i in idx], self.z0, self.z1, self.nb)
        self.bz = [self.z0 + (k + 0.5) * (self.z1 - self.z0) / self.nb for k in range(self.nb)]
        raw = []
        for bp in bins:
            if bp:
                raw.append(((min(p.x for p in bp) + max(p.x for p in bp)) / 2, (min(p.y for p in bp) + max(p.y for p in bp)) / 2))
            else:
                raw.append(None)
        good = [k for k, c in enumerate(raw) if c is not None]
        for k in range(self.nb):
            if raw[k] is None:
                j = min(good, key=lambda g: abs(g - k))
                raw[k] = raw[j]
        dzb = self.bz[1] - self.bz[0]
        hw = max(1, int(round(0.12 / dzb)))
        self.cx, self.cy = [], []
        for k in range(self.nb):
            ks = range(max(0, k - hw), min(self.nb, k + hw + 1))
            self.cx.append(sum(raw[j][0] for j in ks) / len(ks))
            self.cy.append(sum(raw[j][1] for j in ks) / len(ks))
        self.s = 1 if s == "l" else -1
        rad = []
        for k, bp in enumerate(bins):
            rad.append(max((math.hypot(p.x - self.cx[k], p.y - self.cy[k]) for p in bp), default=0.0))
        for k in range(self.nb):
            if not bins[k]:
                rad[k] = max(rad[j] for j in range(max(0, k - 1), min(self.nb, k + 2)))
        self.rad = rad

        def rwin(z, h):
            return max((r for zz, r in zip(self.bz, rad) if abs(zz - z) <= h), default=0.0)

        # straight-leg profile: the cloth bridges every bulge within `bridge` above/below (no calf
        # or knee shape), the thigh tapers to the knee, below the knee it drops straight - the
        # widest shin section + ease carried all the way to the hem so it stacks over the boot
        R_top = max(r for zz, r in zip(self.bz, rad) if z_c - 0.08 <= zz <= z_c) + ease_top
        R_low = max(r for zz, r in zip(self.bz, rad) if hem + 0.05 <= zz <= knee_z) + ease_low
        prof = []
        if monotone:
            # v3: the thigh is ONE straight taper crotch -> knee. v2 took max(taper, bridged local
            # bulge): the bridge window reached the buttock underside, so the tube bulged under the
            # seat and pinched back in 60-100 mm lower (the second dip in v2's seat ease curve).
            # Instead raise the taper's top radius just enough that it clears every thigh section
            # below the seat by ease_top - one scalar, so the profile stays linear and monotone.
            f = lambda z: min(1.0, (z - knee_z) / (z_c - knee_z))
            for zz, r in zip(self.bz, rad):
                if knee_z + 0.02 <= zz <= z_c - 0.03 and f(zz) > 1e-3:
                    R_top = max(R_top, R_low + (r + ease_top - R_low) / f(zz))
            for z in self.bz:
                prof.append(R_low + (R_top - R_low) * f(z) if z >= knee_z else R_low)
        else:
            for z in self.bz:
                if z >= knee_z:
                    lin_ = R_low + (R_top - R_low) * min(1.0, (z - knee_z) / (z_c - knee_z))
                    prof.append(max(lin_, rwin(z, bridge) + ease_top))
                else:
                    prof.append(R_low)
        self.prof = prof
        self.R_top, self.R_low = R_top, R_low
        self.info = {"R_top_mm": round(R_top * 1000, 1), "R_below_knee_mm": round(R_low * 1000, 1),
                     "thigh_r_mm": round((R_top - ease_top) * 1000, 1), "shin_calf_r_max_mm": round((R_low - ease_low) * 1000, 1)}

    def line(self, z):
        f = (z - self.bz[0]) / (self.bz[1] - self.bz[0])
        k = max(0, min(self.nb - 2, int(math.floor(f))))
        t = max(0.0, min(1.0, f - k))
        return Vector((self.cx[k] * (1 - t) + self.cx[k + 1] * t, self.cy[k] * (1 - t) + self.cy[k + 1] * t, z))

    def R(self, z):
        f = (z - self.bz[0]) / (self.bz[1] - self.bz[0])
        k = max(0, min(self.nb - 2, int(math.floor(f))))
        t = max(0.0, min(1.0, f - k))
        return self.prof[k] * (1 - t) + self.prof[k + 1] * t

    def target(self, b):
        c = self.line(b.z)
        u = Vector((b.x - c.x, b.y - c.y, 0.0))
        if u.length < 1e-6:
            u = Vector((self.s, 0, 0))
        u.normalize()
        p = Vector((c.x, c.y, b.z)) + u * self.R(b.z)
        if p.x * self.s < 0.006:            # the two tubes meet at the crotch seam, never cross
            p.x = self.s * 0.006
        return p, u


def taubin(bmr, P, iters, lam=0.5, mu=-0.53, fixed=()):
    bnd = {v for v in bmr.verts if v.is_boundary}
    nbrs = {}
    for v in bmr.verts:
        if v in bnd:
            nbrs[v.index] = [e.other_vert(v).index for e in v.link_edges if e.is_boundary]
        else:
            nbrs[v.index] = [e.other_vert(v).index for e in v.link_edges]
    idxs = [v.index for v in bmr.verts if v.index not in fixed]
    for _ in range(iters):
        for f in (lam, mu):
            Q = list(P)
            for i in idxs:
                nb = nbrs[i]
                if nb:
                    avg = sum((P[j] for j in nb), Vector()) / len(nb)
                    Q[i] = P[i] + (avg - P[i]) * f
            P[:] = Q


def drape_targets(bmr, body, fn):
    """Per garment vertex: nearest skin point b (+normal, dominant bone) -> fn(v, b, n, dom)."""
    P, D, B = [], [], []
    for v in bmr.verts:
        b, n, dom = body.near(v.co)
        p, d = fn(v, b, n, dom)
        P.append(p)
        D.append(d)
        B.append((b, n, dom))
    return P, D, B


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

    # densify on the skin positions, then drape every vertex (old and new) from its skin point
    rep["jeans_densify"] = densify(bmr, axis_of)
    groups = boundary_groups(bmr, NAMES)
    waist_edges, hem_edges = groups.get("waist", []), groups.get("hem", [])
    waist_v = {v for e in waist_edges for v in e.verts}
    hem_v = {v for e in hem_edges for v in e.verts}

    # crotch apex = lowest skin point of the region on the midline
    z_c = min(body.co[v[src]].z for v in bmr.verts if abs(body.co[v[src]].x) < 0.02)
    knee_z = (knee["l"].z + knee["r"].z) / 2
    tubes = {s: LegTube(body, s, z_c, HEM, knee_z, ease_top=0.011, ease_low=0.015, bridge=0.08, monotone=True)
             for s in "lr"}
    htree = body.part_tree({"pelvis", "spine_01", "thigh_l", "thigh_r"}, z_c - 0.04, 1.16)
    ay = sum(body.co[i].y for i in range(len(body.co)) if body.in_body[i] and body.dom[i] == "pelvis") / \
        max(1, sum(1 for i in range(len(body.co)) if body.in_body[i] and body.dom[i] == "pelvis"))
    zs = [z_c - 0.02 + 0.01 * k for k in range(int((1.14 - z_c) / 0.01) + 2)]
    hipenv = Envelope(htree, 0.0, ay, zs, z_fall=1.10)
    EASE_HIP = 0.013
    ANCHOR = 0.008
    # v3 SEAT FALL (2026-09-25 artist: "the butt area is tight and then goes loose and is not
    # fluid"). v2 hung the seat envelope straight down from the buttock apex and handed it to the
    # leg tube over a 60 mm window at the crotch: the cloth stood 28 mm off the skin under the seat,
    # then snapped in to 18 mm within 30 mm of height, back out to 29 and in again to 23 (the leg
    # tube's own bridged bulge). v3 designs the transition CURVE instead of blending two shapes:
    #   * the rear hand-over from the seat envelope to the leg tube runs from the seat apex to the
    #     crotch as one C2 (smootherstep) blend (front/sides keep v2's 60 mm window),
    #   * over the rear fall (seat apex -> crotch, buttock columns, cleft excluded) the cloth sits
    #     on a designed ease level E(z): EASE_SEAT at the apex rising C2-smoothly to EASE_FALL at
    #     the crotch - each vertex marched out along its drape direction until it is E(z) off the
    #     skin (the level set bridges the gluteal fold with a rounded fill, it does not track it),
    #   * the leg tube is a monotone taper (LegTube monotone), so the fall's end ease carries on
    #     down the thigh without a second pinch.
    EASE_SEAT = 0.0105
    EASE_FALL = 0.0200
    lmk = seat_landmarks(body)
    SEAT_Z = lmk["seat_z"]
    E_seat = lambda z: EASE_SEAT + (EASE_FALL - EASE_SEAT) * _smoother(SEAT_Z, z_c - 0.01, z)
    rep["jeans_drape"] = {"crotch_z": round(z_c, 4), "knee_z": round(knee_z, 4), "hip_axis_y": round(ay, 4),
                          "ease_hip_mm": EASE_HIP * 1000, "ease_seat_mm": EASE_SEAT * 1000, "ease_fall_mm": EASE_FALL * 1000,
                          "anchor_mm": ANCHOR * 1000,
                          "seat_fall_window_z": [round(z_c - 0.01, 4), round(SEAT_Z, 4)],
                          "design_ease_curve_mm": [[round(z, 2), round(E_seat(z) * 1000, 1)]
                                                   for z in [SEAT_Z - 0.02 * k for k in range(8)]],
                          "tube": {s: t.info for s, t in tubes.items()}}

    def w_seat(b):
        # rear buttock columns from just above the seat apex down to the crotch. The cleft band
        # stays on the hull (bridged, v2): a level set there dips into the crease and folds the
        # cloth over at the cleft base (measured: 8 body points outside the jeans).
        return back_w(b) * _smooth(SEAT_Z + 0.01, SEAT_Z - 0.03, b.z) * _smooth(z_c - 0.04, z_c + 0.01, b.z) *             _smooth(0.015, 0.045, abs(b.x))

    def level(q0, d, e):
        """Move the (ordered) drape target q0 along its drape direction onto the E level: inward
        while it stands further than e off the skin, outward while it is closer / inside. Starting
        from the drape target (not the skin point) keeps neighbour order across the gluteal fold."""
        sd0 = body.sd(q0)[0]
        step = -0.001 if sd0 > e else 0.001
        q = q0
        for _ in range(90):
            nq = q + d * step
            s_ = body.sd(nq)[0]
            if step < 0 and s_ < e:
                break
            q = nq
            if step > 0 and s_ >= e:
                break
        return q

    def back_w(b):
        dy = b.y - ay
        r = math.hypot(b.x, dy)
        return _smooth(0.0, 0.6, dy / r) if r > 1e-6 else 0.0

    def fn(v, b, n, dom):
        s = "l" if b.x > 0 else "r"
        pt, ut = tubes[s].target(b)
        bw = back_w(b)
        ph, uh = hipenv.target(b, EASE_HIP + (EASE_SEAT - EASE_HIP) * bw)
        rxy = math.hypot(b.x, b.y - ay)
        z_hi = (z_c + 0.05) + (SEAT_Z - (z_c + 0.05)) * bw
        # rear: one C2 fall from the seat apex; front/sides: v2's smoothstep window, unchanged
        wz = _smooth(z_c - 0.01, z_hi, b.z) * (1 - bw) + _smoother(z_c - 0.01, z_hi, b.z) * bw
        w_hip = wz * _smooth(0.015, 0.045, rxy)
        free = pt.lerp(ph, w_hip)
        d = ut.lerp(uh, w_hip).normalized()
        ws = w_seat(b)
        if ws > 0.0:
            free = free.lerp(level(free, d, E_seat(b.z)), ws)
        w_free = _smooth(WAIST(b) - 0.008, WAIST(b) - 0.045, b.z)
        p = (b + n * ANCHOR).lerp(free, w_free)
        return p, d.lerp(n, 1 - w_free).normalized()

    P, D, B = drape_targets(bmr, body, fn)
    for v in waist_v:
        P[v.index].z = WAIST(P[v.index])
    for v in hem_v:
        P[v.index].z = HEM
    taubin(bmr, P, 3)
    for v in waist_v:
        P[v.index].z = WAIST(P[v.index])
    for v in hem_v:
        P[v.index].z = HEM
    # crotch drop: between the legs the half-gap of the thighs caps any sideways ease, so the
    # seam hangs below the body's crotch (a real jeans crotch drop) instead of hugging it
    # The seam line is the lower convex hull of the body's midline profile (side view): it bridges
    # the perineum and the gluteal cleft like a gusset, then hangs CROTCH_DROP below it.
    CROTCH_DROP = 0.018
    mid = sorted((round(body.co[i].y, 4), body.co[i].z) for i in range(len(body.co))
                 if body.in_body[i] and abs(body.co[i].x) < 0.035 and z_c - 0.02 < body.co[i].z < z_c + 0.16)
    low = []
    for pt in mid:
        while len(low) >= 2 and ((low[-1][0] - low[-2][0]) * (pt[1] - low[-2][1]) -
                                 (low[-1][1] - low[-2][1]) * (pt[0] - low[-2][0])) <= 0:
            low.pop()
        low.append(pt)

    def seam_z(y):
        if y <= low[0][0] or y >= low[-1][0]:
            return None
        for (y0_, z0_), (y1_, z1_) in zip(low[:-1], low[1:]):
            if y0_ <= y <= y1_:
                return z0_ + (z1_ - z0_) * (y - y0_) / max(1e-9, y1_ - y0_)
        return None

    for v in bmr.verts:
        b = B[v.index][0]
        if abs(b.x) >= 0.06 or b.z > z_c + 0.14:
            continue
        sz = seam_z(P[v.index].y)
        if sz is None:
            continue
        w = (1.0 - abs(b.x) / 0.06) ** 0.7
        zt = sz - CROTCH_DROP
        if P[v.index].z > zt:
            P[v.index].z = P[v.index].z + (zt - P[v.index].z) * w
    rep["jeans_drape"]["crotch_drop_mm"] = CROTCH_DROP * 1000
    rep["jeans_drape"]["crotch_seam_hull_pts"] = len(low)
    # folds, outward along the drape direction only: knee bunching (strongest behind the knee),
    # denim stacking over the boots above the hem, low-frequency irregularity
    for v in bmr.verts:
        if v in waist_v:            # the hem ring takes the stack too (no elastic-cuff pinch)
            continue
        i = v.index
        p, d = P[i], D[i]
        b = B[i][0]
        if b.z > z_c:
            a = 0.002 * (0.5 + 0.5 * mnoise.noise(p * 7.0))
            P[i] = p + d * a
            continue
        s = "l" if b.x > 0 else "r"
        c = tubes[s].line(p.z)
        ang = math.atan2(p.y - c.y, (p.x - c.x) * tubes[s].s)
        back = max(0.0, math.sin(ang))           # +y = behind the knee
        kz = p.z - knee[s].z
        a = (0.004 + 0.004 * back) * math.exp(-(kz / 0.06) ** 2) * \
            (0.5 + 0.5 * math.sin(p.z / 0.024 * 2 * math.pi + 2.0 * ang + 1.3 * mnoise.noise(p * 5.0)))
        st = max(0.0, 1 - (p.z - HEM) / 0.19)
        a += 0.011 * st * (0.5 + 0.5 * math.sin((p.z - HEM) / 0.042 * 2 * math.pi + 1.6 * ang
                                                  + 1.8 * mnoise.noise(p * 6.0)))
        a += 0.003 * (0.5 + 0.5 * mnoise.noise(p * 8.0))
        P[i] = p + d * a
    # floors: the waistband anchor keeps v2's 8 mm; below it the declared gate floor (10 mm, +0.3 mm
    # for the MPFB refit drift) is CONSTRUCTED rather than hoped for (v2's front-crotch vertex held
    # 10.9 mm by luck over an 8 mm floor), lifted toward the designed ease on the rear fall
    WS = [w_seat(B[i][0]) for i in range(len(P))]
    WF = [_smooth(WAIST(B[i][0]) - 0.008, WAIST(B[i][0]) - 0.045, B[i][0].z) for i in range(len(P))]
    dmin = lambda i: max(0.008 + 0.0023 * WF[i], 0.008 + max(0.0, E_seat(B[i][0].z) - 0.001 - 0.008) * WS[i])
    enforce(P, [v.index for v in bmr.verts], body, dmin, layers)
    for v in bmr.verts:
        v.co = P[v.index]
    bmr.normal_update()
    for f in bmr.faces:
        f[kind] = 0

    # stitch / detail paths (vertex attribute 'stitch')
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

    # rims: waistband and hem turn inward (fabric thickness), quads only
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
    Z_YOKE = shoulder["l"].z - 0.065           # the shoulder line the jacket hangs from

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

    rep["jacket_densify"] = densify(bmr, axis_of)
    groups = boundary_groups(bmr, NAMES)
    rep["jacket_boundary_edges_dense"] = {k: len(v) for k, v in groups.items()}
    hem_v = {v for e in groups.get("hem", []) for v in e.verts}
    front_v = {v for e in groups.get("front", []) for v in e.verts}
    cuff_v = {v for e in groups.get("cuff", []) for v in e.verts}

    # torso silhouette: hulled slices hung from the shoulder line; sleeves: cylinders
    ttree = body.part_tree(TORSO | LEGS, HEM - 0.06, 1.60)
    tv = [body.co[i] for i in range(len(body.co)) if body.in_body[i] and body.dom[i] in TORSO and HEM <= body.co[i].z <= Z_YOKE]
    ay = sum(p.y for p in tv) / len(tv)
    zs = [HEM - 0.03 + 0.01 * k for k in range(int((1.58 - HEM) / 0.01) + 2)]
    env = Envelope(ttree, 0.0, ay, zs, z_fall=Z_YOKE)
    sleeves = {s: Sleeve(body, s) for s in "lr"}
    ANCHOR = 0.010

    def ease(z):
        return 0.019 + 0.008 * _smooth(Z_YOKE, HEM, z)   # a touch more flare toward the hem

    pits = {s: armpit(body, s) for s in "lr"}

    def w_free_of(b):
        # the jacket hangs from the shoulder line and closes around the armhole: full drape only
        # outside the declared anchor bands (the same radii the clearance gate exempts)
        wz = _smooth(Z_YOKE + 0.05, Z_YOKE + 0.005, b.z)
        wd = min(min(_smooth(0.05, ANCHOR_R["shoulder"] - 0.005, (b - shoulder[s]).length),
                     _smooth(0.03, ANCHOR_R["armpit"] - 0.005, (b - pits[s]).length)) for s in "lr")
        return wz * wd

    # v3: under the sleeve zones the leather sits a leather thickness over the tee sleeve (the
    # torso keeps the 7 mm air layer); blended off the anchor band like the skin floors
    def tee_margin(p):
        b, n, dom = body.near(p)
        if jacket_zone(b, dom, body.bones) in SLEEVE_DMIN:
            return 0.007 + (SLEEVE_TEE_MARGIN - 0.007) * w_free_of(b)
        return 0.007
    layers[0].margin = tee_margin

    def fn(v, b, n, dom):
        wf = w_free_of(b)
        if dom in ARM_UP or dom in ARM_LO:
            free, d, _ = sleeves[dom[-1]].target(b, n)
        else:
            free, d = env.target(b, ease(b.z))
        p = (b + n * ANCHOR).lerp(free, wf)
        return p, d.lerp(n, 1 - wf).normalized()

    P, D, B = drape_targets(bmr, body, fn)
    rep["jacket_drape"] = {"axis_y": round(ay, 4), "z_yoke": round(Z_YOKE, 4), "anchor_mm": ANCHOR * 1000,
                           "ease_mm": [round(ease(Z_YOKE) * 1000, 1), round(ease(HEM) * 1000, 1)],
                           "sleeve": {s: sl.info for s, sl in sleeves.items()}}

    def snap():
        for v in hem_v:
            P[v.index].z = HEM
        for v in front_v - hem_v:
            P[v.index].x = math.copysign(GAP, P[v.index].x)
        for chain, closed in chains([e for e in groups.get("cuff", [])]):
            s = "l" if P[chain[0].index].x > 0 else "r"
            ax = (wrist[s] - elbow[s]).normalized()
            t0 = sum((P[v.index] - wrist[s]).dot(ax) for v in chain) / len(chain)
            for v in chain:
                P[v.index] = P[v.index] - ax * ((P[v.index] - wrist[s]).dot(ax) - t0)

    snap()
    taubin(bmr, P, 3)
    # the anchored armhole/shoulder band is a skin offset over a concave crease (normals converge
    # and the offset crumples): relax it the v1 way (Laplacian + skin/tee clearance) before folds
    anchored = {v.index for v in bmr.verts if w_free_of(B[v.index][0]) < 0.95}
    # in the armpit core the crease is narrower than two clearances: let the membrane bridge it
    # below the apex (small skin floor there; the tee push still keeps it over the tee)
    # v3: the sleeve zones carry their own (lower) skin floors, reached smoothly off the anchor band
    zone = [jacket_zone(b, dom, body.bones) for b, n, dom in B]
    wfree = [w_free_of(b) for b, n, dom in B]

    def dmin_j(i):
        b = B[i][0]
        if min((b - pits[s]).length for s in "lr") < 0.07:
            return 0.004
        z = zone[i]
        if z in SLEEVE_DMIN:
            return 0.012 + (SLEEVE_DMIN[z] - 0.012) * wfree[i]
        return 0.012

    relax(bmr, 40, 0.5, anchored, body, dmin_j, layers, P)
    snap()
    # leather folds, outward along the drape direction only (cannot create poke-through): sleeve
    # bunching at the elbow crook and a stacked cuff, soft vertical drape ripples at the hem and
    # sides of the boxy body, low-frequency irregularity
    for v in bmr.verts:
        if v.is_boundary:
            continue
        i = v.index
        p, d = P[i], D[i]
        b, n, dom = B[i]
        a = 0.0
        if dom in ARM_UP or dom in ARM_LO:
            sl = sleeves[dom[-1]]
            _, _, (t_up, t_lo, w) = sl.target(b, n)
            up = sl.segs[0]
            r = p - (up["P0"] + up["A"] * ((p - up["P0"]).dot(up["A"])))
            ang = math.atan2(r.z, r.y)
            ez = (b - sl.E).dot(sl.elbow_n)
            # v3: the elbow bunching is kept at v2's amplitude but held to the crook (sigma 50 mm,
            # v2 70 mm spilled folds up onto the biceps); the cuff stack is lower (snug cuff)
            a += 0.0065 * math.exp(-(ez / 0.05) ** 2) * \
                (0.5 + 0.5 * math.sin(ez / 0.03 * 2 * math.pi + 2.2 * ang + 1.5 * mnoise.noise(p * 6.0)))
            cz = max(0.0, (t_lo - 0.62) / (sl.t_end - 0.62)) if w > 0.5 else 0.0
            a += 0.0040 * min(1.0, cz) * (0.5 + 0.5 * math.sin(t_lo * sl.segs[1]["L"] / 0.032 * 2 * math.pi
                                                                 + 1.7 * ang + 1.2 * mnoise.noise(p * 5.0)))
            a += 0.0012 * (0.5 + 0.5 * mnoise.noise(p * 9.0))   # leather stretched over muscle: less lumpy
        else:
            low = _smooth(HEM + 0.22, HEM, p.z)
            a += 0.005 * low * (0.5 + 0.5 * math.sin(math.atan2(p.y - ay, p.x) * 9.0 + 3.0 * mnoise.noise(p * 3.0)))
            a += 0.003 * (0.5 + 0.5 * mnoise.noise(p * 9.0))
        P[i] = p + d * (a * w_free_of(b))       # no folds on the anchored band (converging normals crumple)
    dmin = dmin_j
    enforce(P, [v.index for v in bmr.verts], body, dmin, layers)
    # armpit hollow: above the apex the torso and arm skins close into a dome tighter than the
    # clearance, and per-vertex pushes along converging normals crumple the leather into the tee.
    # Smooth the core flat, then move it along ONE shared direction (down, out of the hollow)
    # until it clears skin and tee.
    core = {s: [v.index for v in bmr.verts if (B[v.index][0] - pits[s]).length < 0.075] for s in "lr"}
    for s, idxs in core.items():
        if not idxs:
            continue
        out = Vector((0.0, -0.1, -1.0)).normalized()     # the hollow opens downward
        taubin(bmr, P, 12, lam=0.5, mu=0.0, fixed=set(range(len(P))) - set(idxs))   # plain Laplacian: unfold
        tee_layer = layers[0]
        lifted = 0
        for i in idxs:
            p = P[i]
            for _ in range(60):
                s_b, _, _ = body.sd(p)
                ok = s_b >= 0.006
                if ok:
                    loc, n_, _, _ = tee_layer.tree.find_nearest(p, 0.03)
                    if loc is not None:
                        _, _, bn = body.sd(loc)
                        if n_.dot(bn) < 0:
                            n_ = -n_
                        ok = (p - loc).dot(n_) >= 0.006
                if ok:
                    break
                p = p + out * 0.001
                lifted += 1
            P[i] = p
        rep.setdefault("jacket_armpit_lift_total_mm", {})[s] = lifted   # summed 1 mm steps over the core verts
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
        cand = [v for v in allv if abs((v.co - S).dot(ax) + 0.005) < 0.012 and (v.co - S).length < 0.19]
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
            e_ = min(front_v, key=lambda w: (w.co.z - a.co.z) ** 2 + (0 if w.co.x * sgn > 0 else 1.0))
            paths["yoke_front_" + s] = dijkstra_path(bmr, a, e_)
        # slanted side welt pocket near the hem
        p0 = pick(allv, sgn * 0.14, HEM + 0.05, "front", body)
        paths["welt_" + s] = dijkstra_path(bmr, p0, pick(allv, sgn * 0.14, HEM + 0.16, "front", body))
    # back yoke across the shoulder blades
    bl = min((v for v in armhole if v.co.x > 0 and v.normal.y > 0), key=lambda v: abs(v.co.z - 1.47), default=None)
    br = min((v for v in armhole if v.co.x < 0 and v.normal.y > 0), key=lambda v: abs(v.co.z - 1.47), default=None)
    if bl and br:
        paths["yoke_back"] = loop_walk(bl, Vector((-1, 0, 0)), lambda w: w in armhole and w.co.x < 0)
    for k, p in paths.items():
        stitch |= set(p)
    # chest zip pocket, wearer's left (+X), slanted
    z0 = pick(allv, 0.075, 1.40, "front", body)
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
    return bmr, stitch, zipl, region_src, {"hem": HEM, "gap_half": GAP, "z_yoke": Z_YOKE}


# ----------------------------------------------------------------------------------------------
# HAIR (v2): a modelled low-poly mass replacing the CC0 cap + alpha cards (artist: "the hair is
# wrong"). Reference form-a-front.png: dark tousled mop, volume on top, swept toward the wearer's
# right from a part on the left, locks falling over the forehead to the brow, sides over the ear
# tops. Construction: (1) a thick scalp shell hung on the skull (rows from the hairline to the
# crown, thickness = volume map + lumpy noise) so the crown is always covered; (2) clumped
# directional locks - tapered diamond-section blades grown over the shell along a flow field
# (part -> sweep, fringe forward/down, sides/back down), riding the shell, drooping with length.
# Faceted (flat) locks on a smooth shell: readable clump shapes at this style tier.
# ----------------------------------------------------------------------------------------------
def build_hair(body, brow_top, rep, seed=11):
    import random
    rnd = random.Random(seed)
    htree = body.part_tree({"head"})
    hv = [body.co[i] for i in range(len(body.co)) if body.in_body[i] and body.dom[i] == "head" and body.co[i].z > brow_top]
    yf = min(p.y for p in hv)
    yb = max(p.y for p in hv)
    H0 = Vector((0.0, (yf + yb) / 2 + 0.004, brow_top + 0.012))

    def d_of(al, ph):
        return Vector((math.sin(ph) * math.sin(al), -math.sin(ph) * math.cos(al), math.cos(ph)))

    def skin(d):
        o = H0 + d * 0.35
        hit, n, _, dist = htree.ray_cast(o, -d, 0.35)
        return hit, n

    def z_hair(al):
        """Hairline height by azimuth (0 = front, pi/2 = wearer's left, pi = back)."""
        a = abs(((al + math.pi) % (2 * math.pi)) - math.pi)
        keys = [(0.0, brow_top + 0.047), (0.8, brow_top + 0.030), (1.25, brow_top - 0.004),
                (1.75, brow_top - 0.020), (2.4, brow_top - 0.065), (math.pi, brow_top - 0.080)]
        for (a0, z0), (a1, z1) in zip(keys[:-1], keys[1:]):
            if a <= a1:
                return z0 + (z1 - z0) * (a - a0) / (a1 - a0)
        return keys[-1][1]

    def phi_h(al):
        lo, hi = 0.15, 2.7
        zt = z_hair(al)
        for _ in range(30):
            m = (lo + hi) / 2
            p, _ = skin(d_of(al, m))
            if p is not None and p.z > zt:
                lo = m
            else:
                hi = m
        return lo

    def vol(al, f):
        """Shell thickness: full on the crown/top, thinner at the hairline, fuller on the part's
        far side (the sweep piles hair toward the wearer's right)."""
        a = abs(((al + math.pi) % (2 * math.pi)) - math.pi)
        edge = 0.009 if a < 0.9 else (0.012 if a < 2.2 else 0.013)
        top = 0.030 + 0.006 * max(0.0, -math.sin(al))
        return top + (edge - top) * (f ** 1.6)

    NA, NR = 32, 8
    shell = bmesh.new()
    tl = shell.verts.layers.float.new("hair_t")
    grid, phs = [], []
    for ia in range(NA):
        al = 2 * math.pi * ia / NA
        phs.append(phi_h(al))
    for k in range(NR):                     # k = 0 hairline ... NR-1 near the crown
        f = 1.0 - k / NR
        row = []
        for ia in range(NA):
            al = 2 * math.pi * ia / NA
            ph = phs[ia] * f
            d = d_of(al, ph)
            p, _ = skin(d)
            if p is None:
                p = H0 + d * 0.09
            th = vol(al, f) * (1.0 + 0.28 * mnoise.noise(d * 6.0 + Vector((3.1, 0.7, 1.3))))
            row.append(shell.verts.new(p + d * th))
        grid.append(row)
    ptop, _ = skin(Vector((0, 0, 1)))
    pole = shell.verts.new(ptop + Vector((0, 0, 1)) * vol(0.0, 0.0))
    # hairline rim tucked to the skin (thickness at the edge instead of a paper-thin border)
    rim = []
    for ia in range(NA):
        d = d_of(2 * math.pi * ia / NA, phs[ia])
        p, _ = skin(d)
        rim.append(shell.verts.new((p if p is not None else H0 + d * 0.09) + d * 0.0015))
    rows = [rim] + grid
    for k in range(len(rows) - 1):
        for ia in range(NA):
            j = (ia + 1) % NA
            shell.faces.new((rows[k][ia], rows[k][j], rows[k + 1][j], rows[k + 1][ia]))
    for ia in range(NA):
        shell.faces.new((grid[-1][ia], grid[-1][(ia + 1) % NA], pole))
    for f in shell.faces:
        f.smooth = True
    shell_outer = BVHTree.FromBMesh(shell)

    def radial_floor(p, h):
        d = (p - H0).normalized()
        s, _ = skin(d)
        if s is None:
            return p
        need = (s - H0).length + h
        return H0 + d * max(need, (p - H0).length)

    def shell_h(p):
        loc, _, _, dist = shell_outer.find_nearest(p)
        d = (p - H0).normalized()
        s, _ = skin(d)
        if loc is None or s is None:
            return 0.006
        return max(0.005, (loc - H0).length - (s - H0).length)

    # flow field: part on the wearer's left-front; hair sweeps from it toward the wearer's right
    PART = d_of(0.55, 0.55)

    def flow(p, grp):
        d = (p - H0).normalized()
        if grp == "fringe":
            v = Vector((-0.5, -1.0, -0.7))
        elif grp == "top":
            # one consistent sweep across the crown (forward + toward the wearer's right), a little
            # spread away from the part so the part side lifts
            away = d - PART
            v = Vector((-0.75, -0.65, 0.0)) + away.normalized() * 0.35
        elif grp == "side":
            v = Vector((-0.1 * math.copysign(1, p.x), 0.3, -1.0))
        else:
            v = Vector((-0.15, 0.2, -1.0))
        v = v - d * v.dot(d)
        return v.normalized() if v.length > 1e-6 else Vector((0, 0, -1))

    groups = []
    for _ in range(18):
        groups.append(("fringe", rnd.uniform(-1.0, 0.95), rnd.uniform(0.55, 0.95), rnd.uniform(0.05, 0.12)))
    for _ in range(36):
        groups.append(("top", rnd.uniform(0, 2 * math.pi), rnd.uniform(0.03, 0.62), rnd.uniform(0.08, 0.12)))
    for sgn in (1, -1):
        for _ in range(11):
            groups.append(("side", sgn * rnd.uniform(0.95, 2.15), rnd.uniform(0.5, 0.92), rnd.uniform(0.05, 0.075)))
    for _ in range(26):
        groups.append(("back", rnd.uniform(2.1, 4.2), rnd.uniform(0.4, 0.92), rnd.uniform(0.055, 0.085)))

    locks = bmesh.new()
    ll = locks.verts.layers.float.new("hair_t")
    NS = 4
    n_locks = 0
    for grp, al, f, L in groups:
        ph = phi_h(al) * f
        d0 = d_of(al, ph)
        s, _ = skin(d0)
        if s is None:
            continue
        p = s + d0 * (vol(al, f) * 0.55)
        if grp == "fringe":             # end above the brow instead of sliding along it
            L = min(L, max(0.03, (p.z - (brow_top + 0.012)) * 1.35))
        w0 = {"fringe": 0.030, "top": 0.036, "side": 0.027, "back": 0.032}[grp] * rnd.uniform(0.85, 1.15)
        flat = 0.3 if grp in ("fringe", "top") else 0.36
        jit = Vector((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))) * 0.4
        pts = [p]
        step = L / NS
        for k in range(1, NS + 1):
            fk = k / NS
            g = Vector((0, 0, -1)) * (0.25 + 0.9 * fk * fk) * (0.35 if grp == "top" else 1.0)
            v = (flow(pts[-1], grp) + jit * (0.5 + fk) + g)
            dd = (pts[-1] - H0).normalized()
            v = (v - dd * v.dot(dd) * 0.85).normalized()
            q = pts[-1] + v * step
            lift = (0.003 + 0.005 * math.sin(math.pi * fk)) if grp == "top" else 0.0015
            q = radial_floor(q, shell_h(q) * 0.75 + lift)
            if grp == "fringe" and q.z < brow_top + 0.008:
                q.z = brow_top + 0.008
            pts.append(q)
        prev_ring = None
        root_ring = None
        for k, c in enumerate(pts[:-1]):
            fk = k / NS
            fwd = (pts[k + 1] - c).normalized()
            rad = (c - H0).normalized()
            side = fwd.cross(rad).normalized()
            up = side.cross(fwd).normalized()
            w = w0 * (1.0 - 0.6 * fk ** 1.5)          # blunt clump ends, not spikes
            t = flat * w
            twist = 0.35 * math.sin(3.0 * fk + al)
            sd_ = side * math.cos(twist) + up * math.sin(twist)
            ud_ = up * math.cos(twist) - side * math.sin(twist)
            ring = [locks.verts.new(c + sd_ * (w / 2)), locks.verts.new(c + ud_ * (t / 2)),
                    locks.verts.new(c - sd_ * (w / 2)), locks.verts.new(c - ud_ * (t / 2))]
            for vv in ring:
                vv[ll] = fk
            if prev_ring is None:
                root_ring = ring
            else:
                for m in range(4):
                    locks.faces.new((prev_ring[m], prev_ring[(m + 1) % 4], ring[(m + 1) % 4], ring[m]))
            prev_ring = ring
        tip = locks.verts.new(pts[-1])
        tip[ll] = 1.0
        for m in range(4):
            locks.faces.new((prev_ring[m], prev_ring[(m + 1) % 4], tip))
        locks.faces.new(list(reversed(root_ring)))
        n_locks += 1
    for f_ in locks.faces:
        f_.smooth = True
    # merge the two parts into one mesh
    me = bpy.data.meshes.new("forge_hair_build")
    shell.to_mesh(me)
    tmp = bpy.data.meshes.new("tmp_locks")
    locks.to_mesh(tmp)
    obj = bpy.data.objects.new("forge_hair_build", me)
    lob = bpy.data.objects.new("tmp_locks", tmp)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.scene.collection.objects.link(lob)
    shell.free()
    locks.free()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    lob.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()
    bmr = bmesh.new()
    bmr.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bmr, faces=list(bmr.faces))
    bmesh.ops.triangulate(bmr, faces=list(bmr.faces))   # MPFB cross-refs need one face size per mesh
    bmr.to_mesh(obj.data)
    bmr.free()
    obj.data.update()
    obj.data.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")
    rep["hair"] = {"method": "modelled shell + clumped locks", "locks": n_locks, "shell_grid": [NA, NR],
                   "verts": len(obj.data.vertices), "faces": len(obj.data.polygons),
                   "head_centre": [round(c, 4) for c in H0], "seed": seed}
    return obj


def write_mhclo_hair(M, basemesh, obj, rep):
    HumanService, ClothesService, LocationService = M["HumanService"], M["ClothesService"], M["LocationService"]
    me = obj.data
    side = {"hair_t": [a.value for a in me.attributes["hair_t"].data] if "hair_t" in me.attributes else [0.0] * len(me.vertices),
            "smooth": [p.use_smooth for p in me.polygons],
            "n_verts": len(me.vertices), "n_faces": len(me.polygons),
            "co": [tuple(round(c, 6) for c in v.co) for v in me.vertices]}
    vg = obj.vertex_groups.new(name="body")
    vg.add(list(range(len(me.vertices))), 1.0, "REPLACE")
    name = "forge_protagonist_hair"
    props = {"name": name, "author": "forge (werewolf custom-character lane)", "license": "CC0",
             "description": "forge-modelled low-poly hair mass for the werewolf protagonist",
             "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "forge/werewolf/" + name))}
    mh = ClothesService.create_mhclo_from_clothes_matching(basemesh, obj, properties_dict=props,
                                                           delete_group=None, allow_exact=False)
    folder = os.path.join(LocationService.get_user_data("hair"), name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".mhclo")
    mh.write_mhclo(path, reference_scale=ClothesService.get_reference_scale(basemesh),
                   also_export_mhmat=False, also_export_obj=True)
    json.dump(side, open(os.path.join(folder, name + ".forge.json"), "w"))
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(me)
    rep.setdefault("mhclo", {})["hair"] = {"path": path, "verts": side["n_verts"], "faces": side["n_faces"]}
    return path, side


def mat_hair(pal):
    mat = bpy.data.materials.new("forge_hair")
    nt, bsdf = _nodes(mat)
    root = lrgb((14, 11, 10))
    tip = lrgb(tuple(min(255, int(c * 1.25)) for c in pal["hair"]["srgb"]))
    t = _attr(nt, "hair_t")
    col = _mixc(nt, _ramp(nt, t, 0.1, 1.0), root, tip)
    nz = _noise(nt, 26.0, 4.0)
    col = _mixc(nt, _mul(nt, _ramp(nt, nz.outputs["Fac"], 0.45, 0.75), 0.5), col, lrgb((8, 7, 7)))
    nt.links.new(col, bsdf.inputs["Base Color"])
    rr = nt.nodes.new("ShaderNodeMapRange")          # matte under-mass (shell, roots), sheen on the tips
    rr.inputs["To Min"].default_value = 0.66
    rr.inputs["To Max"].default_value = 0.42
    nt.links.new(t, rr.inputs["Value"])
    nt.links.new(rr.outputs[0], bsdf.inputs["Roughness"])
    try:
        bsdf.inputs["Specular IOR Level"].default_value = 0.65
    except KeyError:
        pass
    # fine clump breakup only (a banded wave read as zig-zag stripes across the locks)
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = 0.18
    b.inputs["Distance"].default_value = 0.0015
    nt.links.new(_noise(nt, 140.0, 2.0).outputs["Fac"], b.inputs["Height"])
    nt.links.new(b.outputs[0], bsdf.inputs["Normal"])
    return mat


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


# PER-ZONE MINIMUM-CLEARANCE gate. v2 (2026-09-25 "not like a skin suit") scored ONE min/median per
# garment; v3 (2026-09-25 "outfit v2 verdict": biceps tight, forearm/wrist a little tighter than the
# body drape, seat one continuous fall) scores DECLARED zones - the lower sleeve floors are the
# design, not a failure. Air gap = unsigned distance from each cloth-shell vertex (kind 0: no
# rims/zip/collar) to the nearest SKIN point (full basemesh, masks off), rest A-pose. Zones are
# assigned by declared landmark rules (jacket_zone / jeans_zone), identical for every version, so a
# rejected baseline is scored by the same ruler. First match wins: anchor band (exempt, v2 rules
# unchanged) -> zone rules. A zone passes when every vertex holds min_mm and the zone median lies in
# median_mm [lo, hi] (None = open). Floors: jacket torso = tee (~3 mm) + leather (~1.5 mm) + a
# visible air gap >= 10 mm -> 15 min / 30 median (v2, unchanged); sleeve bands are the artist's
# numbers; jeans leg = denim (~1 mm) + visible gap at the tightest point -> 10 min / 25 median (v2,
# unchanged); seat top fitted 8-12. The seat_fall zone is scored by its ease CURVE (below).
CLEARANCE_ZONES = {
    "jacket": {
        "bicep":   {"rule": "upperarm-dominant skin, t < %.2f shoulder->elbow" % ZONE_T["bicep_end"],
                    "min_mm": 4.0, "median_mm": [4.0, 8.0],
                    "rejected_baseline_v2": {"n": 394, "min": 16.5, "median": 34.6}},
        "elbow":   {"rule": "upperarm t >= %.2f or lowerarm t < %.2f (eases through, bunching)" % (ZONE_T["bicep_end"], ZONE_T["elbow_end"]),
                    "min_mm": 4.0, "median_mm": [4.0, 20.0],
                    "rejected_baseline_v2": {"n": 342, "min": 17.2, "median": 43.3}},
        "forearm": {"rule": "lowerarm t in [%.2f, %.2f) elbow->wrist" % (ZONE_T["elbow_end"], ZONE_T["forearm_end"]),
                    "min_mm": 8.0, "median_mm": [8.0, 14.0],
                    "rejected_baseline_v2": {"n": 408, "min": 23.7, "median": 32.6}},
        "wrist":   {"rule": "lowerarm t >= %.2f, or hand-dominant skin" % ZONE_T["forearm_end"],
                    "min_mm": 8.0, "median_mm": [8.0, 12.0],
                    "rejected_baseline_v2": {"n": 266, "min": 23.5, "median": 32.1}},
        "torso":   {"rule": "everything else outside the anchor band (boxy drape, v2 stands)",
                    "min_mm": 15.0, "median_mm": [30.0, None]},
    },
    "jeans": {
        "seat_top":  {"rule": "rear-facing skin (n.y > 0.3, |x| < 0.16) at/above the seat apex",
                      "min_mm": 8.0, "median_mm": [8.0, 12.0]},
        "seat_fall": {"rule": "rear-facing skin from the seat apex down to crotch - 100 mm",
                      "min_mm": 8.0, "median_mm": [None, None]},
        "hip":       {"rule": "front/sides above the crotch", "min_mm": 10.0, "median_mm": [None, None]},
        "leg":       {"rule": "below the crotch, not seat", "min_mm": 10.0, "median_mm": [25.0, None]},
    },
}
# Seat EASE CURVE (the artist's "tight and then goes loose and is not fluid"): per 1 cm height step
# down the buttock columns (0.04 <= |x| <= 0.13, seat_top + seat_fall vertices), the median air gap
# of the vertices within +-10 mm. From the seat top to the fall's end it must be MONOTONIC
# (never fall more than max_dip_mm below its running maximum going down - the per-bin sampling
# noise of a 4-36 vertex median) and SMOOTH (no step larger than max_step_mm per cm: the
# fitted-to-leg rise is ~15 mm over ~20 cm, ~0.75 mm/cm on average; 3 mm/cm allows a 4x local rate
# but not v2's collapse). Its end must reach the leg floor (end_min_mm).
# Rejected baseline v2: 13.1 (z 1.01) rising to 27.7 (0.94), collapsing to 18.0 (0.91: the hung
# seat envelope handed to the leg tube over 60 mm), back up to 28.8 (0.85), down to 22.7 (0.80);
# worst drawdown 9.7 mm, worst step 5.5 mm/cm -> FAIL.
SEAT_CURVE = {"abs_x_m": [0.04, 0.13], "bin_m": 0.01, "half_window_m": 0.010,
              "max_dip_mm": 1.5, "max_step_mm": 3.0, "end_min_mm": 10.0}
CLEAR_BINS_MM = [0, 5, 10, 15, 20, 30, 40, 60, 80, 1e9]
ANCHOR_R = {"shoulder": 0.12, "armpit": 0.10}


def armpit(body, s):
    """Armpit apex: the lowest point of the skin seam between the upper-arm and torso regions
    (vertices shared by an upperarm-dominant face and a torso-dominant face)."""
    arm, tor = set(), set()
    for fi in body.body_faces:
        d = body.fdom[fi]
        if d == "upperarm_" + s:
            arm.update(body.faces[fi])
        elif d in TORSO:
            tor.update(body.faces[fi])
    S = body.bones["upperarm_" + s][0]
    cand = [body.co[i] for i in arm & tor if (body.co[i] - S).length < 0.25]
    return min(cand, key=lambda p: p.z)


def seat_landmarks(body):
    """Crotch apex = lowest midline skin point (|x| < 20 mm) of the trunk/legs above the knees;
    seat apex = the rearmost skin point of the buttocks (pelvis/thigh-dominant, 30-150 mm off the
    midline, between the crotch and the waist)."""
    knee_z = (body.bones["calf_l"][0].z + body.bones["calf_r"][0].z) / 2
    z_c = min(body.co[i].z for i in range(len(body.co)) if body.in_body[i] and abs(body.co[i].x) < 0.02
              and body.dom[i] in (TORSO | LEGS) and body.co[i].z > knee_z + 0.1)
    butt = [body.co[i] for i in range(len(body.co)) if body.in_body[i]
            and body.dom[i] in ("pelvis", "thigh_l", "thigh_r") and 0.03 < abs(body.co[i].x) < 0.15
            and z_c < body.co[i].z < 1.12]
    apex = max(butt, key=lambda p: p.y)
    return {"crotch_z": z_c, "seat_z": apex.z, "seat_y": apex.y, "fall_end_z": z_c - 0.10}


def _landmarks(rig, body):
    b = {n: rig.matrix_world @ rig.data.bones[n].head_local for n in ("upperarm_l", "upperarm_r", "pelvis")}
    b["armpit_l"], b["armpit_r"] = armpit(body, "l"), armpit(body, "r")
    b.update(seat_landmarks(body))
    return b


def anchor_band(key, p, lm):
    if key == "jacket":
        if p.z >= lm["upperarm_l"].z - 0.065:
            return True
        return any((p - lm["upperarm_" + s]).length < ANCHOR_R["shoulder"] or
                   (p - lm["armpit_" + s]).length < ANCHOR_R["armpit"] for s in "lr")
    if key == "jeans":
        return p.z >= 1.068 + 0.08 * (p.y - lm["pelvis"].y) - 0.05
    return False


def jeans_zone(p, b, n, lm):
    back = n.y > 0.3 and abs(b.x) < 0.16
    if back and p.z >= lm["seat_z"]:
        return "seat_top"
    if back and p.z >= lm["fall_end_z"]:
        return "seat_fall"
    return "leg" if p.z < lm["crotch_z"] else "hip"


def _pct(xs, q):
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f = int(math.floor(k))
    c = min(len(xs) - 1, f + 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _summ(xs):
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    hist = {}
    for lo_, hi_ in zip(CLEAR_BINS_MM[:-1], CLEAR_BINS_MM[1:]):
        hist["%g-%s" % (lo_, "inf" if hi_ > 1e8 else "%g" % hi_)] = sum(1 for x in xs if lo_ <= x < hi_)
    hist["<0"] = sum(1 for x in xs if x < 0)
    return {"n": len(xs), "min": round(xs[0], 1), "p5": round(_pct(xs, 0.05), 1),
            "median": round(_pct(xs, 0.5), 1), "p95": round(_pct(xs, 0.95), 1),
            "max": round(xs[-1], 1), "hist_mm": hist}


def seat_curve(samples, lm):
    """samples: (z, gap_mm, |x|) of seat_top + seat_fall vertices -> the ease-vs-height curve + verdict."""
    c = SEAT_CURVE
    col = [(z, d) for z, d, ax in samples if c["abs_x_m"][0] <= ax <= c["abs_x_m"][1]]
    if not col:
        return {"curve": [], "pass": False}
    z = math.floor(max(z for z, _ in col) / c["bin_m"]) * c["bin_m"]
    curve = []
    while z >= lm["fall_end_z"] - 1e-9:
        ds = sorted(d for zz, d in col if abs(zz - z) <= c["half_window_m"])
        if ds:
            curve.append([round(z, 3), len(ds), round(_pct(ds, 0.5), 1)])
        z -= c["bin_m"]
    steps = [round(b[2] - a[2], 1) for a, b in zip(curve[:-1], curve[1:])]
    run, worst_dip = -1e9, 0.0
    for _, _, e in curve:                     # drawdown below the running maximum, top -> down
        run = max(run, e)
        worst_dip = min(worst_dip, e - run)
    worst_step = max([0.0] + [abs(s) for s in steps])
    ok = (-worst_dip <= c["max_dip_mm"] and worst_step <= c["max_step_mm"] and curve[-1][2] >= c["end_min_mm"])
    return {"curve_z_n_median_mm": curve, "steps_mm": steps, "worst_dip_mm": round(worst_dip, 1),
            "worst_step_mm": round(worst_step, 1), "rule": dict(c), "pass": bool(ok)}


def clearance_stats(body, obj, key, lm):
    pts, polys, kinds = eval_points(obj)
    shell = set()
    for i, f in enumerate(polys):
        if kinds is None or kinds[i] == 0:
            shell.update(f)
    zones = {z: [] for z in CLEARANCE_ZONES[key]}
    anch, free, tight, seat = [], [], [], []
    for i in sorted(shell):
        p = pts[i]
        s, loc, n = body.sd(p)
        d = (p - loc).length * (1 if s >= 0 else -1) * 1000.0
        if anchor_band(key, p, lm):
            anch.append(d)
            continue
        _, _, dom = body.near(p)
        z = jacket_zone(loc, dom, body.bones) if key == "jacket" else jeans_zone(p, loc, n, lm)
        zones[z].append(d)
        free.append(d)
        tight.append((d, z, p))
        if z in ("seat_top", "seat_fall"):
            seat.append((p.z, d, abs(p.x)))
    out_z, ok = {}, True
    for z, xs in zones.items():
        g = CLEARANCE_ZONES[key][z]
        sm = _summ(xs)
        lo, hi = g["median_mm"]
        zok = sm["n"] > 0 and sm["min"] >= g["min_mm"] and (lo is None or sm["median"] >= lo) and \
            (hi is None or sm["median"] <= hi)
        worst = sorted((t for t in tight if t[1] == z), key=lambda t: t[0])[:3]
        out_z[z] = {"gate": {"min_mm": g["min_mm"], "median_mm": g["median_mm"]}, "stats": sm, "pass": bool(zok),
                    "tightest": [[round(d, 1)] + [round(c, 3) for c in p] for d, _, p in worst]}
        ok = ok and zok
    res = {"zones": out_z, "anchor_band": _summ(anch), "free_all": _summ(free),
           "anchor_fraction": round(len(anch) / max(1, len(anch) + len(free)), 3)}
    if key == "jeans":
        res["seat_curve"] = seat_curve(seat, lm)
        ok = ok and res["seat_curve"]["pass"]
    res["pass"] = bool(ok)
    return res


def clearance(blend_report):
    """Score the loaded .blend (any version) against the per-zone minimum-clearance gate."""
    bm = bpy.data.objects["Protagonist"]
    rig = bpy.data.objects["Protagonist.rig"]
    body = Body(bm, rig)
    lm = _landmarks(rig, body)
    out = {k: clearance_stats(body, bpy.data.objects["Protagonist." + k], k, lm) for k in ("jacket", "jeans")}
    out["landmarks"] = {k: (round(v, 4) if isinstance(v, float) else [round(c, 4) for c in v]) for k, v in lm.items()}
    json.dump(out, open(blend_report, "w"), indent=1)
    print("CLEARANCE", json.dumps({k: {"pass": v["pass"], **({z: [r["stats"].get("min"), r["stats"].get("median"), r["pass"]]
                                                               for z, r in v["zones"].items()})}
                                   for k, v in out.items() if k != "landmarks"}, indent=1))
    return out



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
    j_del = delete_verts_for(body, None, j_region, DELETE_MARGIN_RINGS)
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
    k_del = delete_verts_for(body, None, k_region, DELETE_MARGIN_RINGS)
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

    # --- hair (modelled mass, fitted as an MPFB hair asset) ---
    brow_top = max(p.z for p in eval_points(objs["eyebrows"])[0])
    hobj = build_hair(body, brow_top, REP)
    h_path, h_side = write_mhclo_hair(M, bm, hobj, REP)
    hair = HumanService.add_mhclo_asset(h_path, bm, asset_type="Hair", subdiv_levels=0, material_type="MAKESKIN")
    hair.name = "Protagonist.hair"
    hme = hair.data
    assert len(hme.vertices) == h_side["n_verts"] and len(hme.polygons) == h_side["n_faces"], "OBJ round trip changed hair topology"
    hme.materials.clear()
    hme.materials.append(mat_hair(pal))
    a = hme.attributes.get("hair_t") or hme.attributes.new("hair_t", "FLOAT", "POINT")
    a.data.foreach_set("value", h_side["hair_t"])
    if hme.has_custom_normals:
        bpy.ops.object.select_all(action="DESELECT")
        hair.select_set(True)
        bpy.context.view_layer.objects.active = hair
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    if "sharp_face" in hme.attributes:
        hme.attributes.remove(hme.attributes["sharp_face"])
    hme.polygons.foreach_set("use_smooth", h_side["smooth"])

    # fitting fidelity: MPFB refit vs the modelled positions
    for key, obj, side in (("jeans", jeans, j_side), ("jacket", jacket, k_side), ("hair", hair, h_side)):
        pts, _, _ = eval_points(obj)
        dev = max((Vector(a) - b).length for a, b in zip(side["co"], pts))
        REP.setdefault("refit_max_dev_mm", {})[key] = round(dev * 1000, 3)
        REP["uv"][key + "_fitted"] = uv_report(obj.data)

    # palette tints on the CC0 assets
    tint(tee, tuple(pal["shirt"]["srgb"]), gain=2.0)
    tint(boots, (40, 36, 34), rough=0.45, gain=2.0)
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
    # minimum-clearance gate (drape, not skin suit) against the full skin, anchor bands declared
    REP["clearance"] = {k: clearance_stats(body, o, k, _landmarks(rig, body)) for k, o in (("jacket", jacket), ("jeans", jeans))}
    # hair: crown coverage = skin points within 60 deg of straight up from the head centre whose
    # outward radial ray meets the hair mass (no scalp showing through the crown)
    hp, hpolys, _ = eval_points(hair)
    htree_obj = BVHTree.FromPolygons(hp, hpolys)
    H0 = Vector(REP["hair"]["head_centre"])
    crown = [p for p, ok in zip(body.co, body.in_body) if ok and (p - H0).length > 1e-6
             and (p - H0).normalized().z > math.cos(math.radians(60))]
    hit = sum(1 for p in crown if htree_obj.ray_cast(p + (p - H0).normalized() * 0.0005, (p - H0).normalized(), 0.2)[0] is not None)
    REP["hair_crown_coverage"] = {"skin_points": len(crown), "covered": hit, "fraction": round(hit / max(1, len(crown)), 4)}

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
    print("REPORT", json.dumps({k: REP.get(k) for k in ("height_m", "tris", "fit_checks", "refit_max_dev_mm", "clearance",
                                                        "hair", "hair_crown_coverage", "jacket_drape", "jeans_drape")},
                               indent=1, default=str))


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
        # A-pose garment-fit closeup: collar, zip, chest pocket, sleeve join and (v3) the left
        # biceps in frame
        cam_d.lens = 85
        aim(30.0, elev=0.02, tgt=Vector((0.15, centre.y, 1.37)), d=1.35)
        still("jacket_closeup")
        cam_d.lens = 50
    if mode in ("stills", "all", "fit", "compare"):
        # v3: A-pose arm closeup - the left sleeve from the front-outside, shoulder to cuff, so the
        # biceps read (tight), the elbow bunching and the forearm/cuff taper are all in one frame
        cam_d.lens = 85
        aim(35.0, elev=0.0, tgt=Vector((0.37, -0.06, 1.34)), d=1.15)
        still("arm")
        cam_d.lens = 50
    if mode in ("stills", "all", "compare"):
        # v3: seat closeup from behind-left - the jeans' rear silhouette from the waistband over
        # the seat into the thigh (the fall the artist flagged)
        cam_d.lens = 85
        aim(125.0, elev=0.0, tgt=Vector((0.0, 0.03, 0.9)), d=1.55)
        still("seat")
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


def grid(out_png, pngs, ncols=2, gap=8):
    """Compose same-size renders row-major into an ncols grid with thin dark separators."""
    import numpy as np
    ims = [bpy.data.images.load(os.path.abspath(p)) for p in pngs]
    w, h = ims[0].size
    assert all(tuple(im.size) == (w, h) for im in ims), "renders must share a resolution"
    a = [np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4) for im in ims]
    rows_n = (len(a) + ncols - 1) // ncols
    W, H = ncols * w + (ncols - 1) * gap, rows_n * h + (rows_n - 1) * gap
    out = np.zeros((H, W, 4), dtype=np.float32)
    out[..., :3] = 0.08
    out[..., 3] = 1.0
    for k, im in enumerate(a):
        r, c = divmod(k, ncols)
        y0 = H - (r + 1) * h - r * gap          # Blender pixel rows run bottom-up
        out[y0:y0 + h, c * (w + gap):c * (w + gap) + w] = im
    o = bpy.data.images.new("grid", W, H)
    o.pixels = out.ravel()
    o.filepath_raw = os.path.abspath(out_png)
    o.file_format = "PNG"
    o.save()
    print("WROTE", out_png)


def side_by_side(left_png, right_png, out_png, gap=8):
    """Compose two same-size renders left|right (v1 | v2) with a thin dark separator."""
    import numpy as np
    ims = [bpy.data.images.load(os.path.abspath(p)) for p in (left_png, right_png)]
    w, h = ims[0].size
    assert tuple(ims[1].size) == (w, h), "renders must share a resolution"
    a = [np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4) for im in ims]
    sep = np.zeros((h, gap, 4), dtype=np.float32)
    sep[..., 3] = 1.0
    sep[..., :3] = 0.08
    out = np.concatenate([a[0], sep, a[1]], axis=1)
    o = bpy.data.images.new("sbs", out.shape[1], out.shape[0])
    o.pixels = out.ravel()
    o.filepath_raw = os.path.abspath(out_png)
    o.file_format = "PNG"
    o.save()
    print("WROTE", out_png)


if __name__ == "__main__":
    if ARGS and ARGS[0] == "build":
        build(ARGS[1], ARGS[2])
    elif ARGS and ARGS[0] == "render":
        render(ARGS[1], ARGS[2] if len(ARGS) > 2 else "all")
    elif ARGS and ARGS[0] == "clearance":
        clearance(ARGS[1])
    elif ARGS and ARGS[0] == "sidebyside":
        side_by_side(ARGS[1], ARGS[2], ARGS[3])
    elif ARGS and ARGS[0] == "grid":
        grid(ARGS[1], ARGS[2:])
    else:
        print(__doc__)
