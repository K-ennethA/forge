"""Proxy finish, LIGHT mode -- the Blender half (config "mode": "light"; proxy_finish_build.run dispatches here). The
artist's pick on the generate-and-finish panels (2026-10-11) was the RAW proxy's look for gemwing ("in trying to fix the
symmetry and make changes we broke some things, like the mouth"), so this keeps the proxy's geometry and texture and
only makes them shippable:

  1. STAGE 1 (proxy_finish.py stage_light): a watertight remesh of the proxy on a fine grid (no smoothing / symmetrise /
     straighten / cuts) + the proxy mesh and texture in the frame (refloor: the floor at the FOOT contact, the front
     height of the sheet).
  2. HIGH = marching tets of the whole grid; specks dropped. LOW = a uniform collapse decimation to `pre_tris`, then the
     final one to `body_tris` with the `dense` boxes (head / face) weighted: the weight factor is CALIBRATED (bisection)
     so each box ends with `density` x the rest's triangles per area. No use_symmetry, no mirror-averaging.
  3. ONE palette region (light.region, white): Col tints the baked albedo. Smart UV; bakes against the FLAT low
     (BUILD_CONTRACT step 5) from the PROXY ITSELF (not the remesh): normal, AO, and the proxy texture as albedo (EMIT).
     The material: Col x basecolor (= albedo x AO, one texture so the glb carries it as baseColorTexture), normal map.
  4. (opt-in light.yaw: a proxy built turned as a whole is turned back about Z as one rigid body, yaw_whole.)
     REPOSE (opt-in, config "repose"; proxy_finish_repose.py): the posed chains (a curled tail, a turned head) are
     straightened with bones and the deformation is applied as the rest mesh; the bakes ride along on the UVs. Stretch,
     self-intersection, rigid-face and mirror numbers in the report. Then the opt-in light.belly (belly_push): a smooth
     fuller belly on the finished low, arms swung out rigidly, legs kept (absent key = no change).
  5. centring, contract props, focus close-ups (face + tail), then the shared rig / clips / glb
     (proxy_finish_build.rig_clips_export) on the straightened joints.
"""
import bpy, sys, os, math, json, time, tempfile, subprocess, types
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import palettes as PAL           # noqa: E402  (read-only use)
import magmoo_sdf as SD          # noqa: E402  (read-only use)
import proxy_finish as PF        # noqa: E402
import proxy_finish_build as PFB  # noqa: E402
import proxy_finish_repose as PR  # noqa: E402
import proxy_finish_parts as PFP  # noqa: E402

sha = PFB.sha


def obj_from_arrays(name, V, F, link=True):
    """triangle mesh object straight from arrays (foreach_set: a 3 M-triangle high in seconds, not minutes)"""
    V = np.asarray(V, np.float64); F = np.asarray(F, np.int64)
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(V)); me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(3 * len(F)); me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
    me.polygons.add(len(F)); me.polygons.foreach_set("loop_start", np.arange(0, 3 * len(F), 3, dtype=np.int32))
    me.update(calc_edges=True)
    ob = bpy.data.objects.new(name, me)
    if link:
        bpy.context.scene.collection.objects.link(ob)
    return ob


def tri_arrays(me):
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    assert (lt == 3).all(), "not a triangle mesh"
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    return co.reshape(-1, 3), lv.reshape(-1, 3)


def decimate(V, F, ratio, vg=None, factor=0.0):
    ob = obj_from_arrays("dec_tmp", V, F)
    dm = ob.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = float(min(1.0, ratio))
    if vg is not None and factor > 0:
        g = ob.vertex_groups.new(name="dense")
        for val in np.unique(vg):
            ids = np.nonzero(vg == val)[0].tolist()
            g.add(ids, float(val), "REPLACE")
        dm.vertex_group = "dense"; dm.vertex_group_factor = float(factor)
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    V2, F2 = tri_arrays(me)
    bpy.data.meshes.remove(me)
    m_ = ob.data
    bpy.data.objects.remove(ob, do_unlink=True); bpy.data.meshes.remove(m_)
    return V2, F2


def in_boxes(P, boxes):
    m = np.zeros(len(P), bool)
    for b in boxes:
        lo, hi = np.asarray(b["box"][0], float), np.asarray(b["box"][1], float)
        m |= np.all((P >= lo) & (P <= hi), axis=1)
    return m


def tri_area(V, F):
    T = V[F]
    return 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)


def nonmanifold_edges(F):
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    _, c = np.unique(e, axis=0, return_counts=True)
    return int((c == 1).sum()), int((c > 2).sum())


def mirror_to_surface(V, F, plane_x=0.0):
    bvh = BVHTree.FromPolygons(V.tolist(), F.tolist())
    Vm = V.copy(); Vm[:, 0] = 2 * plane_x - Vm[:, 0]
    d = np.array([bvh.find_nearest(Vector(p))[3] for p in Vm])
    return {"plane_x_u": round(float(plane_x), 4), "max_u": round(float(d.max()), 5),
            "p99_u": round(float(np.percentile(d, 99)), 5), "p90_u": round(float(np.percentile(d, 90)), 5),
            "mean_u": round(float(d.mean()), 5),
            "rule": "every vertex mirrored in X about the plane -> distance to the nearest surface (0 = exactly symmetric)"}


def self_intersections(V, F, face_mask=None):
    """triangle pairs that intersect and share no vertex (BVH overlap)"""
    bvh = BVHTree.FromPolygons(V.tolist(), F.tolist())
    pairs = bvh.overlap(bvh)
    n = 0; n_m = 0
    Fs = [set(f) for f in F.tolist()]
    for a, b in pairs:
        if a < b and not (Fs[a] & Fs[b]):
            n += 1
            if face_mask is not None and (face_mask[a] or face_mask[b]):
                n_m += 1
    return n, n_m


def _smooth(e0, e1, x):
    t_ = np.clip((x - e0) / max(e1 - e0, 1e-12), 0.0, 1.0)
    return t_ * t_ * (3.0 - 2.0 * t_)


def recolour_albedo(pc, R, me, specs):
    """opt-in light.recolour (artist 2026-10-12, Sand Drill Worm: "mouth dark red"): per spec, the albedo texels of a
    CUP-shaped cavity (a mouth) are repainted with a depth gradient, the proxy's own texture detail kept as a luminance
    ratio. The mask is GEOMETRY: every low triangle is rasterised into the albedo's UV grid; a texel is in the cavity when
    its 3D point lies `rim_band`+ below the rim plane (`plane_point`, unit `axis` out of the mouth), within `radius` of
    the axis, above `depth` + 0.15, and its face does not face outward (normal . radial < normal_max_out). Bright texels
    (the teeth) keep their colour: weight 1 - smoothstep(keep_bright). The UV margin texels next to a repainted texel
    take its value (dilation, `margin_px` rings). pc: (R*R, 4) sRGB floats, edited in place -> report"""
    uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 3, 2)
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv); Ft = lv.reshape(-1, 3)
    T3 = co[Ft]
    nrm = np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    owner = np.full(R * R, -1, np.int64)            # every texel inside some triangle (for the margin dilation)
    img = pc.reshape(R, R, 4)
    out = []
    for sp in specs:
        c0 = np.asarray(sp["plane_point"], float); ax = np.asarray(sp["axis"], float); ax /= np.linalg.norm(ax)
        rad = float(sp["radius"]); dep = float(sp["depth"]); band = float(sp.get("rim_band", 0.06))
        nmax = float(sp.get("normal_max_out", 0.3)); kb = sp.get("keep_bright", [115, 150])
        stops = sorted(sp["stops"], key=lambda r: r[0])
        st_t = np.array([r[0] for r in stops], float); st_c = np.array([r[1] for r in stops], float) / 255.0
        gam = float(sp.get("detail_gamma", 0.6))
        rel = T3.mean(1) - c0
        sc = rel @ ax
        cand = np.nonzero((sc < 0.1) & (sc > -(dep + 0.4)) & (np.linalg.norm(rel - sc[:, None] * ax, axis=1) < rad + 0.3))[0]
        tex_idx, tex_w, tex_t = [], [], []
        for f in cand:
            t2 = uv[f] * R
            x0, y0 = np.floor(t2.min(0)).astype(int); x1, y1 = np.ceil(t2.max(0)).astype(int)
            xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, R)) + 0.5, np.arange(max(y0, 0), min(y1, R)) + 0.5)
            Pq = np.stack([xs.ravel(), ys.ravel()], 1)
            if not len(Pq):
                continue
            a_, b_, c_ = t2
            den = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
            if abs(den) < 1e-12:
                continue
            l0 = ((b_[1] - c_[1]) * (Pq[:, 0] - c_[0]) + (c_[0] - b_[0]) * (Pq[:, 1] - c_[1])) / den
            l1 = ((c_[1] - a_[1]) * (Pq[:, 0] - c_[0]) + (a_[0] - c_[0]) * (Pq[:, 1] - c_[1])) / den
            l2 = 1.0 - l0 - l1
            ins = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
            if not ins.any():
                continue
            B_ = np.stack([l0[ins], l1[ins], l2[ins]], 1)
            X3 = B_ @ T3[f]
            r3 = X3 - c0
            s3 = r3 @ ax
            q3 = r3 - s3[:, None] * ax
            rr = np.linalg.norm(q3, axis=1)
            ru = q3 / np.maximum(rr, 1e-9)[:, None]
            d3 = -s3
            ok = (rr < rad) & (d3 < dep + 0.15) & ((ru @ nrm[f]) < nmax)
            w3 = _smooth(0.0, band, d3) * ok
            ii = (Pq[ins, 1].astype(int)) * R + Pq[ins, 0].astype(int)
            owner[ii] = f
            keep = w3 > 0
            tex_idx.append(ii[keep]); tex_w.append(w3[keep]); tex_t.append(np.clip((d3[keep] - band) / max(dep - band, 1e-9), 0, 1))
        if not tex_idx:
            out.append({"name": sp.get("name"), "texels": 0}); continue
        ii = np.concatenate(tex_idx); ww = np.concatenate(tex_w); tt = np.concatenate(tex_t)
        o_, first = np.unique(ii, return_index=True)
        ii, ww, tt = ii[first], ww[first], tt[first]
        src = pc[ii, :3].copy()
        Y = src.mean(1) * 255.0
        wb = 1.0 - _smooth(float(kb[0]), float(kb[1]), Y)          # the teeth (bright) keep their colour
        ww = ww * wb
        tgt = np.stack([np.interp(tt, st_t, st_c[:, k]) for k in range(3)], 1)
        Ym = float(np.median(Y[ww > 0.5])) if (ww > 0.5).any() else float(np.median(Y))
        detail = np.clip((np.maximum(Y, 1.0) / max(Ym, 1.0)) ** gam, 0.7, 1.3)
        new_ = np.clip(tgt * detail[:, None], 0, 1)
        pc[ii, :3] = src * (1 - ww[:, None]) + new_ * ww[:, None]
        # margin: UV texels outside every triangle take the repainted neighbour (rings of 4-neighbour dilation)
        painted = np.zeros(R * R, bool); painted[ii[ww > 0.5]] = True
        free = owner < 0
        nm_ = 0
        for _ in range(int(sp.get("margin_px", 3))):
            pm = painted.reshape(R, R)
            nb = np.zeros((R, R), bool); acc = np.zeros((R, R, 3)); cnt = np.zeros((R, R))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                sh = np.roll(pm, (dy, dx), (0, 1)); col = np.roll(img[..., :3], (dy, dx), (0, 1))
                acc += col * sh[..., None]; cnt += sh; nb |= sh
            grow = (nb & ~pm & free.reshape(R, R)).ravel()
            if not grow.any():
                break
            g2 = np.nonzero(grow)[0]
            pc[g2, :3] = (acc.reshape(-1, 3)[g2] / cnt.ravel()[g2][:, None])
            painted[g2] = True; nm_ += len(g2)
        out.append({"name": sp.get("name"), "texels_in_mask": int(len(ii)), "texels_painted_ge_half": int((ww > 0.5).sum()),
                    "teeth_kept_texels": int(((wb < 0.5) & (np.concatenate(tex_w)[first] > 0.5)).sum()),
                    "margin_texels": nm_, "median_src_rgb": np.round(np.median(src[ww > 0.5], 0) * 255).tolist()
                    if (ww > 0.5).any() else None,
                    "median_new_rgb": np.round(np.median(pc[ii[ww > 0.5], :3], 0) * 255).tolist() if (ww > 0.5).any() else None,
                    "stops": stops, "faces_considered": int(len(cand))})
    return out


def despeckle(img_px, R, me, spec, channels=3):
    """opt-in light.despeckle (artist 2026-10-12, Sand Drill Worm v2: "a disjoint on the arms where there is leftover"):
    inside the `capsules` region (sheet units, the build frame; texels whose 3D point lies within `pad` of a capsule and
    below `zmax`), texels darker than `frac` x the region's median luminance are bake hits on the proxy soup's INNER faces
    next to a cut / re-joined part; they are refilled from the region's valid 4-neighbours ring by ring (fallback: the
    median). img_px: (R*R, 4) floats edited in place -> report"""
    uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 3, 2)
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv); T3 = co[lv.reshape(-1, 3)]
    caps = [c["capsule"] for c in spec["capsules"]]
    pad = float(spec.get("pad", 0.04)); zmax = float(spec.get("zmax", 1e9))

    def sdu(X):
        return np.min([PF.sd_capsule(X, *c) for c in caps], axis=0)
    cen = T3.mean(1)
    cand = np.nonzero((sdu(cen) < pad + 0.1) & (cen[:, 2] < zmax + 0.1))[0]
    idx = []
    for f in cand:
        t2 = uv[f] * R
        x0, y0 = np.floor(t2.min(0)).astype(int); x1, y1 = np.ceil(t2.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, R)) + 0.5, np.arange(max(y0, 0), min(y1, R)) + 0.5)
        Pq = np.stack([xs.ravel(), ys.ravel()], 1)
        if not len(Pq):
            continue
        a_, b_, c_ = t2
        den = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den) < 1e-12:
            continue
        l0 = ((b_[1] - c_[1]) * (Pq[:, 0] - c_[0]) + (c_[0] - b_[0]) * (Pq[:, 1] - c_[1])) / den
        l1 = ((c_[1] - a_[1]) * (Pq[:, 0] - c_[0]) + (a_[0] - c_[0]) * (Pq[:, 1] - c_[1])) / den
        ins = (l0 >= -1e-6) & (l1 >= -1e-6) & (1.0 - l0 - l1 >= -1e-6)
        if not ins.any():
            continue
        X3 = np.stack([l0[ins], l1[ins], 1.0 - l0[ins] - l1[ins]], 1) @ T3[f]
        ok = (sdu(X3) < pad) & (X3[:, 2] < zmax)
        idx.append((Pq[ins][ok, 1].astype(int)) * R + Pq[ins][ok, 0].astype(int))
    if not idx:
        return {"texels_region": 0}
    reg = np.unique(np.concatenate(idx))
    L = img_px[reg, :channels].mean(1)
    med = float(np.median(L))
    bad = L < float(spec.get("frac", 0.6)) * med
    nbad = int(bad.sum())
    inreg = np.zeros(R * R, bool); inreg[reg] = True
    good = np.zeros(R * R, bool); good[reg[~bad]] = True
    todo = np.zeros(R * R, bool); todo[reg[bad]] = True
    img = img_px.reshape(R, R, -1)
    rings = 0
    while todo.any() and rings < int(spec.get("rings", 24)):
        g2 = good.reshape(R, R); acc = np.zeros((R, R, channels)); cnt = np.zeros((R, R))
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(g2, (dy, dx), (0, 1))
            acc += np.roll(img[..., :channels], (dy, dx), (0, 1)) * sh[..., None]; cnt += sh
        grow = todo & (cnt.ravel() > 0)
        if not grow.any():
            break
        gi = np.nonzero(grow)[0]
        img_px[gi, :channels] = acc.reshape(-1, channels)[gi] / cnt.ravel()[gi][:, None]
        good[gi] = True; todo[gi] = False
        rings += 1
    left = int(todo.sum())
    if left:
        img_px[np.nonzero(todo)[0], :channels] = np.median(img_px[reg[~bad], :channels], 0)
    return {"texels_region": int(len(reg)), "region_median": round(med, 4), "texels_refilled": nbad, "rings": rings,
            "left_to_median": left}


def normal_flatten(px, R, me, spec):
    """opt-in light.normal_flatten (new units only; absent = no change), Spring Pig v3.2: a re-surfaced patch whose
    normal bake hit hidden proxy faces behind it (goggle bezels behind a cut snout box) prints tilted / back-facing
    tangent normals that shade as dark blotches although the albedo is clean. Texels whose 3D point (on the low) lies in
    one of `boxes` ([[x0, y0, z0], [x1, y1, z1]], sheet units, the build frame) and whose tangent blue channel is below
    `blue_below` are set flat (0.5, 0.5, 1.0). `edge_tol` (barycentric) also takes the UV-margin texels just outside a
    triangle. px: (R*R, 4) floats edited in place -> report"""
    uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 3, 2)
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv); T3 = co[lv.reshape(-1, 3)]
    boxes = [(np.asarray(b[0], float), np.asarray(b[1], float)) for b in spec["boxes"]]
    thr = float(spec.get("blue_below", 0.8)); tol = float(spec.get("edge_tol", 0.02))
    cen = T3.mean(1)
    cand = np.zeros(len(T3), bool)
    for b0, b1 in boxes:
        cand |= np.all((cen >= b0 - 0.05) & (cen <= b1 + 0.05), 1)
    hit = np.zeros(R * R, bool)
    for f in np.nonzero(cand)[0]:
        t2 = uv[f] * R
        x0, y0 = np.floor(t2.min(0)).astype(int); x1, y1 = np.ceil(t2.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, R)) + 0.5, np.arange(max(y0, 0), min(y1, R)) + 0.5)
        Pq = np.stack([xs.ravel(), ys.ravel()], 1)
        if not len(Pq):
            continue
        a_, b_, c_ = t2
        den = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den) < 1e-12:
            continue
        l0 = ((b_[1] - c_[1]) * (Pq[:, 0] - c_[0]) + (c_[0] - b_[0]) * (Pq[:, 1] - c_[1])) / den
        l1 = ((c_[1] - a_[1]) * (Pq[:, 0] - c_[0]) + (a_[0] - c_[0]) * (Pq[:, 1] - c_[1])) / den
        ins = (l0 >= -tol) & (l1 >= -tol) & (1.0 - l0 - l1 >= -tol)
        if not ins.any():
            continue
        X3 = np.stack([l0[ins], l1[ins], 1.0 - l0[ins] - l1[ins]], 1) @ T3[f]
        inb = np.zeros(len(X3), bool)
        for b0, b1 in boxes:
            inb |= np.all((X3 >= b0) & (X3 <= b1), 1)
        hit[(Pq[ins][inb, 1].astype(int)) * R + Pq[ins][inb, 0].astype(int)] = True
    bad = hit & (px[:, 2] < thr)
    px[bad, :3] = (0.5, 0.5, 1.0)
    return {"texels_in_boxes": int(hit.sum()), "texels_flattened": int(bad.sum()), "blue_below": thr, "edge_tol": tol,
            "boxes": spec["boxes"], "why": spec.get("why")}


def _box_weight(P, b0, b1, margin):
    """1 inside the box [b0, b1], smoothstep to 0 over `margin` (scalar or per axis) outside it"""
    m = np.broadcast_to(np.asarray(margin, float), (3,))
    w = np.ones(len(P))
    for k in range(3):
        mk = max(float(m[k]), 1e-9)
        t = np.where(P[:, k] < b0[k], (P[:, k] - (b0[k] - mk)) / mk,
                     np.where(P[:, k] > b1[k], ((b1[k] + mk) - P[:, k]) / mk, 1.0))
        t = np.clip(t, 0.0, 1.0)
        w = w * t * t * (3.0 - 2.0 * t)
    return w


def _sstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _turn_regions(spec):
    """-> [(phi0, phi1, phi_margin, z0, z1, z_margin, turn, pivot)] in radians; `mirror_x` adds the mirrored sector"""
    out = []
    for r in spec["regions"]:
        f0, f1 = (math.radians(float(v)) for v in r["phi_deg"])
        pm = math.radians(float(r.get("phi_margin_deg", 25.0)))
        z0, z1 = (float(v) for v in r["z"]); zm = float(r.get("z_margin", 0.25))
        tn = math.radians(float(r["turn_deg"])); piv = np.asarray(r.get("pivot", [0.0, 0.0]), float)
        out.append((f0, f1, pm, z0, z1, zm, tn, piv))
        if r.get("mirror_x"):
            out.append((-f1, -f0, pm, z0, z1, zm, -tn, np.array([-piv[0], piv[1]])))
    return out


def _turn_dphi(phi, z, regs):
    d = np.zeros(len(phi))
    for f0, f1, pm, z0, z1, zm, tn, piv in regs:
        d += tn * _sstep((phi - (f0 - pm)) / pm) * _sstep(((f1 + pm) - phi) / pm) *             _sstep((z - (z0 - zm)) / zm) * _sstep(((z1 + zm) - z) / zm)
    return d


def _polar(P, piv):
    x, y = P[:, 0] - piv[0], P[:, 1] - piv[1]
    return np.hypot(x, y), np.arctan2(x, -y)       # phi = 0 straight ahead (-y), + toward +x


def _turn_forward(P, regs):
    piv = regs[0][7]
    r, phi = _polar(P, piv)
    ph2 = phi + _turn_dphi(phi, P[:, 2], regs)
    return np.stack([piv[0] + r * np.sin(ph2), piv[1] - r * np.cos(ph2), P[:, 2]], 1)


def _trilinear(G, lo, step, P):
    g = (P - lo) / step
    sh = np.array(G.shape) - 1
    g = np.clip(g, 0.0, sh - 1e-6)
    i = np.floor(g).astype(np.int64); f = g - i
    out = np.zeros(len(P))
    for dx in (0, 1):
        wx = f[:, 0] if dx else 1.0 - f[:, 0]
        for dy in (0, 1):
            wy = f[:, 1] if dy else 1.0 - f[:, 1]
            for dz in (0, 1):
                wz = f[:, 2] if dz else 1.0 - f[:, 2]
                out += wx * wy * wz * G[i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz]
    return out


def move_region(F, lo, step, PV, spec):
    """opt-in light.move_region (new units only; absent = no change), Spring Pig v3.2 ("move the eyes a bit closer to
    center"): a part of the PROXY on a round head (a goggle lens + its brass ring + the strap end) TURNED about the
    vertical axis through `pivot` (x, y) by `turn_deg`, so it slides along the head surface (same distance from the axis,
    same height: it stays on the strap line) - a straight translation would sink it into / lift it off the round head.
    Per region: the sector `phi_deg` [a, b] (0 = straight ahead -y, + toward +x) x `z` [z0, z1] turns fully, faded to
    no turn over `phi_margin_deg` / `z_margin` (no fold while |turn| < margin / 1.5); `mirror_x` adds the mirrored
    sector (the turn reversed). One pivot for all regions. The proxy vertices (the bake source: the texture moves with
    them) take the forward turn; the remesh grid is resampled through the inverse turn (fixed point on the angle,
    `iters`) with trilinear sampling. `landmarks` (points) are reported moved. -> F, PV, report"""
    regs = _turn_regions(spec)
    piv = regs[0][7]
    it = int(spec.get("iters", 80))
    zlo = min(r_[3] - r_[5] for r_ in regs); zhi = max(r_[4] + r_[5] for r_ in regs)
    shp = np.array(F.shape)
    k0 = int(np.clip(np.floor((zlo - lo[2]) / step) - 1, 0, shp[2] - 1)); k1 = int(np.clip(np.ceil((zhi - lo[2]) / step) + 2, 1, shp[2]))
    F0 = F.copy()
    G = np.stack(np.meshgrid(np.arange(shp[0]), np.arange(shp[1]), np.arange(k0, k1), indexing="ij"), -1).reshape(-1, 3)
    X = lo + G * step
    r, phi = _polar(X, piv)
    keep = np.abs(_turn_dphi(phi, X[:, 2], regs)) > 1e-12
    G, X, r, phi = G[keep], X[keep], r[keep], phi[keep]
    ps = phi.copy()
    for _ in range(it):
        ps = phi - _turn_dphi(ps, X[:, 2], regs)
    res = np.abs(ps + _turn_dphi(ps, X[:, 2], regs) - phi) * r
    S = np.stack([piv[0] + r * np.sin(ps), piv[1] - r * np.cos(ps), X[:, 2]], 1)
    F[G[:, 0], G[:, 1], G[:, 2]] = _trilinear(F0, lo, step, S)
    PV2 = _turn_forward(PV, regs)
    dv = np.linalg.norm(PV2 - PV, axis=1)
    lm = {}
    for nm, q in (spec.get("landmarks") or {}).items():
        q_ = np.asarray(q, float)[None]
        lm[nm] = {"from": [round(float(v), 4) for v in q_[0]], "to": [round(float(v), 4) for v in _turn_forward(q_, regs)[0]]}
    return F, PV2, {"regions": len(regs), "grid_nodes_resampled": int(len(G)),
                    "inverse_residual_max_u": round(float(res.max()) if len(res) else 0.0, 6),
                    "proxy_verts_moved": int((dv > 1e-9).sum()), "proxy_max_move_u": round(float(dv.max()), 4),
                    "landmarks": lm, "why": spec.get("why")}


def smooth_region(V, F, spec):
    """opt-in light.smooth_region (new units only; absent = no change), Spring Pig v3.2 ("smooth out the face"): the
    LOW's vertices inside `boxes` (faded over `margin`) smoothed by `iterations` of umbrella Laplacian steps (`lambda`,
    then the optional Taubin `mu` step against shrinkage), before the UVs and the bake, so the bake sees one smooth
    face. -> V, report"""
    w = np.zeros(len(V))
    mg = spec.get("margin", 0.1)
    for b in spec["boxes"]:
        w = np.maximum(w, _box_weight(V, np.asarray(b[0], float), np.asarray(b[1], float), mg))
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    E = np.unique(np.sort(E, 1), axis=0)
    deg = np.bincount(E.ravel(), minlength=len(V)).astype(float)

    def lap(X):
        acc = np.zeros_like(X)
        np.add.at(acc, E[:, 0], X[E[:, 1]]); np.add.at(acc, E[:, 1], X[E[:, 0]])
        return acc / np.maximum(deg, 1.0)[:, None] - X
    lam = float(spec.get("lambda", 0.5)); mu = float(spec.get("mu", 0.0))
    V1 = V.copy()
    for _ in range(int(spec.get("iterations", 10))):
        V1 = V1 + lam * w[:, None] * lap(V1)
        if mu:
            V1 = V1 + mu * w[:, None] * lap(V1)
    d = np.linalg.norm(V1 - V, axis=1)
    return V1, {"verts_weighted": int((w > 0).sum()), "max_move_u": round(float(d.max()), 4),
                "mean_move_u_weighted": round(float(d[w > 0].mean()), 5) if (w > 0).any() else 0.0,
                "iterations": int(spec.get("iterations", 10)), "lambda": lam, "mu": mu, "why": spec.get("why")}


def albedo_blend(pc, R, me, spec):
    """opt-in light.smooth_region.albedo_blend: the albedo texels inside the smooth_region boxes (faded over `margin`)
    that pass the colour select (sRGB mean >= `min_mean`, G - B <= `max_g_minus_b`, 0..255: the skin, not the strap /
    brass / glass / nostrils) take the 3D-neighbourhood mean of the selected texels (cells `radius_u` / 2, 3x3x3 box,
    trilinear) -> a colour seam across a re-surfaced patch blended out. pc: (R*R, 4) floats edited in place -> report"""
    ab = spec["albedo_blend"]
    boxes = [(np.asarray(b[0], float), np.asarray(b[1], float)) for b in spec["boxes"]]
    mg = spec.get("margin", 0.1); mgv = np.broadcast_to(np.asarray(mg, float), (3,))
    uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 3, 2)
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv); T3 = co[lv.reshape(-1, 3)]
    cen = T3.mean(1)
    cand = np.zeros(len(T3), bool)
    for b0, b1 in boxes:
        cand |= np.all((cen >= b0 - mgv - 0.05) & (cen <= b1 + mgv + 0.05), 1)
    tol = float(ab.get("edge_tol", 0.02))
    idx, pts = [], []
    for f in np.nonzero(cand)[0]:
        t2 = uv[f] * R
        x0, y0 = np.floor(t2.min(0)).astype(int); x1, y1 = np.ceil(t2.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, R)) + 0.5, np.arange(max(y0, 0), min(y1, R)) + 0.5)
        Pq = np.stack([xs.ravel(), ys.ravel()], 1)
        if not len(Pq):
            continue
        a_, b_, c_ = t2
        den = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den) < 1e-12:
            continue
        l0 = ((b_[1] - c_[1]) * (Pq[:, 0] - c_[0]) + (c_[0] - b_[0]) * (Pq[:, 1] - c_[1])) / den
        l1 = ((c_[1] - a_[1]) * (Pq[:, 0] - c_[0]) + (a_[0] - c_[0]) * (Pq[:, 1] - c_[1])) / den
        ins = (l0 >= -tol) & (l1 >= -tol) & (1.0 - l0 - l1 >= -tol)
        if not ins.any():
            continue
        pts.append(np.stack([l0[ins], l1[ins], 1.0 - l0[ins] - l1[ins]], 1) @ T3[f])
        idx.append((Pq[ins, 1].astype(int)) * R + Pq[ins, 0].astype(int))
    if not idx:
        return {"texels": 0}
    ii = np.concatenate(idx); X = np.concatenate(pts)
    ii, first = np.unique(ii, return_index=True); X = X[first]
    w = np.zeros(len(X))
    for b0, b1 in boxes:
        w = np.maximum(w, _box_weight(X, b0, b1, mg))
    src = pc[ii, :3].astype(np.float64)
    c255 = src * 255.0
    sel = (c255.mean(1) >= float(ab.get("min_mean", 120))) & (c255[:, 1] - c255[:, 2] <= float(ab.get("max_g_minus_b", 15)))
    h = float(ab.get("radius_u", 0.04)) / 2.0
    lo3 = X.min(0) - 2 * h
    gi = np.floor((X - lo3) / h).astype(np.int64)
    shp = gi.max(0) + 3
    S = np.zeros(tuple(shp) + (3,)); C = np.zeros(tuple(shp))
    np.add.at(S, (gi[sel, 0], gi[sel, 1], gi[sel, 2]), src[sel]); np.add.at(C, (gi[sel, 0], gi[sel, 1], gi[sel, 2]), 1.0)
    S2 = np.zeros_like(S); C2 = np.zeros_like(C)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                S2 += np.roll(S, (dx, dy, dz), (0, 1, 2)); C2 += np.roll(C, (dx, dy, dz), (0, 1, 2))
    q = (X - lo3) / h - 0.5
    cnt = _trilinear(C2, np.zeros(3), 1.0, q)
    mean = np.stack([_trilinear(S2[..., k], np.zeros(3), 1.0, q) for k in range(3)], 1) / np.maximum(cnt, 1e-9)[:, None]
    ok = sel & (cnt > 0.5) & (w > 0)
    wv = (w * float(ab.get("strength", 1.0)))[ok, None]
    pc[ii[ok], :3] = src[ok] * (1.0 - wv) + mean[ok] * wv
    return {"texels_in_region": int(len(ii)), "texels_selected": int(sel.sum()), "texels_blended": int(ok.sum()),
            "radius_u": 2 * h, "why": ab.get("why")}


def glow3d_regions(me, V, F, specs, pal, base_region, albedo=None, init=None):
    """opt-in light.glow3d (artist 2026-10-12, Vulture Creature v1 verdict: "can we add green glow in the eyes and then
    the skull necklace make it a green spectre glow"): faces of the low are moved from the one light region into GLOW
    palette regions (palettes/<unit>/default.json: rgb white = the baked albedo kept, `emission` x `emission_scale` ->
    the Glow corner colour -> Emission Color, glTF COLOR_1), so the glow is a palette swap like every house unit (skins
    and the repaint proof keep working). Per spec: the weight of each face centroid = PF._region_w (spheres / boxes /
    exclude, feather) x an optional Lab `select` on the BAKED albedo at the face's UV centroid (e.g. the bright pendant
    bone, not the dark cord); `levels` [[min_weight, region], ...] (the highest threshold wins; e.g. a core and a
    halo). Faces already claimed by an earlier spec keep it. V = the stage-1 frame (the config's coordinates)."""
    cen = V[F].mean(1)
    names = [base_region]
    rid = np.zeros(len(F), np.int32)
    if init is not None:              # (names, rid) already stored (light.parts regions): kept; glow claims base faces only
        names = list(init[0]); rid = np.asarray(init[1], np.int32).copy()
    sel_uv = None
    if albedo is not None:
        uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv)
        sel_uv = uv.reshape(-1, 3, 2).mean(1)
    rows = []
    for sp in specs:
        w = PF._region_w(cen, sp)
        sel_used = False
        if sp.get("select"):
            if albedo is None:
                w = np.zeros_like(w) if sp.get("select_required", True) else w
            else:
                pc, R = albedo
                ix = np.clip((sel_uv[:, 0] * R).astype(int), 0, R - 1); iy = np.clip((sel_uv[:, 1] * R).astype(int), 0, R - 1)
                rgb = np.clip(pc[iy * R + ix, :3], 0, 1) * 255.0
                w = w * PF._lab_select_w(PF.srgb_to_lab(rgb), sp["select"])
                sel_used = True
        for thr, reg in sorted(sp["levels"], key=lambda r: -float(r[0])):
            assert reg in pal["regions"], "palette lacks the glow region %r" % reg
            assert "emission" in pal["regions"][reg], "glow region %r has no emission" % reg
            if reg not in names:
                names.append(reg)
            m = (w >= float(thr)) & (rid == 0)
            rid[m] = names.index(reg)
            rows.append({"spec": sp.get("name"), "region": reg, "min_weight": float(thr), "faces": int(m.sum()),
                         "albedo_select": sel_used, "why": sp.get("why")})
    PAL.store_regions(me, names, rid, np.ones(len(F)))
    paint = PAL.paint(me, pal)
    A = tri_area(V, F)
    return {"faces": {n: int((rid == k).sum()) for k, n in enumerate(names)},
            "area_share": {n: round(float(A[rid == k].sum() / A.sum()), 5) for k, n in enumerate(names)},
            "rows": rows, "paint": paint, "face_region_sha": sha(rid),
            "rule": "faces -> glow palette regions (emission -> Glow -> Emission Color); rgb white keeps the baked albedo"}


def mirror_copy(F, lo, step, PV, PFc, PUV, PTI, spec):
    """opt-in light.mirror_copy (ember_rex v1.1: Pixal3D built ONE leg because the sheet's back + side views draw one):
    a part that exists on one side only is COPIED to the other. Source = the union of `boxes` [[lo, hi], ...] (stage-1
    frame, i.e. after fit_side_length / refloor / midline_x); plane x = `plane_x` (default 0.0 = the body midline once
    midline_x has been applied); reflection x' = 2 * plane_x - x.
      - SDF: inside each MIRRORED box the reflected grid is unioned in: F = min(F, max(F_reflected, sd_box_mirrored)),
        so the copy joins the surface BEFORE the watertight remesh (marching tets); the box faces cap it flat.
      - bake source: proxy triangles whose centroid lies in a source box are appended reflected (winding reversed so
        the normals stay outward), with their own UVs / texture index, so normal / AO / albedo bake from the copy too.
    The original geometry is never moved or removed. -> F, PV, PFc, PUV, PTI, report"""
    p = float(spec.get("plane_x", 0.0))
    boxes = [(np.asarray(b[0], float), np.asarray(b[1], float)) for b in spec["boxes"]]
    F = F.copy()
    shp = np.array(F.shape)
    xs = lo[0] + step * np.arange(F.shape[0])
    nodes_set = 0
    for blo, bhi in boxes:
        mlo = np.array([2 * p - bhi[0], blo[1], blo[2]]); mhi = np.array([2 * p - blo[0], bhi[1], bhi[2]])
        g = 3 * step
        i0 = np.clip(np.floor((mlo - g - lo) / step).astype(int), 0, shp - 1)
        i1 = np.clip(np.ceil((mhi + g - lo) / step).astype(int) + 1, 0, shp)
        # the reflected grid on the destination sub-grid: linear interpolation along x (y / z nodes coincide)
        fx = (2 * p - xs[i0[0]:i1[0]] - lo[0]) / step
        j0 = np.floor(fx).astype(int); t_ = fx - j0
        ok = (j0 >= 0) & (j0 + 1 < F.shape[0])
        j0c = np.clip(j0, 0, F.shape[0] - 2)
        A = F[j0c, i0[1]:i1[1], i0[2]:i1[2]]; B = F[j0c + 1, i0[1]:i1[1], i0[2]:i1[2]]
        Fm = A * (1 - t_)[:, None, None] + B * t_[:, None, None]
        Fm[~ok] = np.inf
        axes = [lo[d] + step * np.arange(i0[d], i1[d]) for d in range(3)]
        X, Y, Zg = np.meshgrid(*axes, indexing="ij")
        sdb = PF.sd_box(np.stack([X.ravel(), Y.ravel(), Zg.ravel()], 1), mlo, mhi).reshape(X.shape)
        sl = tuple(slice(a_, b_) for a_, b_ in zip(i0, i1))
        new = np.minimum(F[sl], np.maximum(Fm, sdb))
        nodes_set += int(np.count_nonzero((new < 0) & (F[sl] >= 0)))
        F[sl] = new
    cen = PV[PFc].mean(1)
    sel = np.zeros(len(PFc), bool)
    for blo, bhi in boxes:
        sel |= np.all((cen >= blo) & (cen <= bhi), 1)
    fs = PFc[sel]
    used = np.unique(fs)
    remap = np.full(len(PV), -1, np.int64); remap[used] = len(PV) + np.arange(len(used))
    Vn = PV[used].copy(); Vn[:, 0] = 2 * p - Vn[:, 0]
    Fn = remap[fs][:, [0, 2, 1]]
    PV2 = np.vstack([PV, Vn]); PFc2 = np.vstack([PFc, Fn]).astype(PFc.dtype)
    PUV2 = np.vstack([PUV, PUV[used]]); PTI2 = np.concatenate([PTI, PTI[sel]]).astype(PTI.dtype)
    rep = {"plane_x_u": p, "boxes": spec["boxes"], "faces_copied": int(sel.sum()), "verts_copied": int(len(used)),
           "grid_nodes_added_inside": nodes_set, "why": spec.get("why"),
           "rule": "proxy faces with centroid in a box reflected x' = 2 * plane_x - x (winding reversed) into the bake "
                   "source; the reflected SDF unioned inside the mirrored boxes before the remesh; originals untouched"}
    return F, PV2, PFc2, PUV2, PTI2, rep


def add_cylinders(F, lo, step, specs):
    """opt-in light.cylinders (spring_pig v3.1, artist 2026-10-17: "the snout needs to be cynlidrincal"): capped
    cylinders smooth-unioned into the remesh grid (before marching tets), for a part that must be a true cylinder with a
    FLAT end (a capsule's end is a hemisphere). Per spec: `centre` [x, y, z] (stage-1 frame), `axis` (any length),
    `radius`, `half_length`, `rim` (the rounding radius of both end rims, 0 = sharp), `k` (smooth-union width, default
    0.02). Rounded-cylinder SDF: q = (radial - (radius - rim), |axial| - (half_length - rim)),
    d = |max(q, 0)| + min(max(q), 0) - rim. Never mirrored. -> F, report rows"""
    F = F.copy()
    shp = np.array(F.shape)
    rows = []
    for sp in specs:
        c = np.asarray(sp["centre"], float); ax = np.asarray(sp["axis"], float); ax = ax / np.linalg.norm(ax)
        r = float(sp["radius"]); h = float(sp["half_length"]); rr = float(sp.get("rim", 0.0)); k = float(sp.get("k", 0.02))
        ext = np.sqrt(np.maximum(1.0 - ax * ax, 0.0)) * r + np.abs(ax) * h      # the cylinder's AABB half extent
        g = k + 3 * step
        want0 = np.floor((c - ext - g - lo) / step).astype(int); want1 = np.ceil((c + ext + g - lo) / step).astype(int) + 1
        i0 = np.clip(want0, 0, shp - 1); i1 = np.clip(want1, 0, shp)
        axes = [lo[d] + step * np.arange(i0[d], i1[d]) for d in range(3)]
        X, Y, Zg = np.meshgrid(*axes, indexing="ij")
        P = np.stack([X.ravel(), Y.ravel(), Zg.ravel()], 1) - c
        s_ = P @ ax
        rad_ = np.linalg.norm(P - s_[:, None] * ax, axis=1)
        q = np.stack([rad_ - (r - rr), np.abs(s_) - (h - rr)], 1)
        d = (np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(1), 0.0) - rr).reshape(X.shape)
        sl = tuple(slice(a_, b_) for a_, b_ in zip(i0, i1))
        cur = F[sl]
        hh = np.clip(0.5 + 0.5 * (d - cur) / k, 0.0, 1.0)
        new = d * (1 - hh) + cur * hh - k * hh * (1 - hh)
        rows.append({"centre": c.tolist(), "axis": ax.round(6).tolist(), "radius": r, "half_length": h, "rim": rr, "k": k,
                     "nodes_inside": int(np.count_nonzero(d < 0)),
                     "grid_nodes_added_inside": int(np.count_nonzero((new < 0) & (cur >= 0))),
                     "clipped_by_grid": bool(np.any(want0 < 0) or np.any(want1 > shp)), "why": sp.get("why")})
        F[sl] = new
    return F, rows


def _trilinear(F, lo, step, P):
    """F sampled at points P (N x 3) by trilinear interpolation; +inf outside the grid"""
    shp = np.array(F.shape)
    g = (P - lo) / step
    i0 = np.floor(g).astype(np.int64)
    t_ = g - i0
    ok = np.all((i0 >= 0) & (i0 + 1 < shp), 1)
    i0 = np.clip(i0, 0, shp - 2)
    out = np.zeros(len(P))
    for dx in (0, 1):
        wx = t_[:, 0] if dx else 1 - t_[:, 0]
        for dy in (0, 1):
            wy = t_[:, 1] if dy else 1 - t_[:, 1]
            for dz in (0, 1):
                wz = t_[:, 2] if dz else 1 - t_[:, 2]
                out += wx * wy * wz * F[i0[:, 0] + dx, i0[:, 1] + dy, i0[:, 2] + dz]
    out[~ok] = np.inf
    return out


def _catmull(pts, n_per=24):
    """centripetal-free (uniform) Catmull-Rom through pts (M x 3), end points doubled -> a dense polyline"""
    P = np.vstack([pts[:1], pts, pts[-1:]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0.0, 1.0, n_per, endpoint=False):
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(P[-2])
    return np.asarray(out)


def _fill_cavity(Fb, wall, axis):
    """the hollow light skin's cavity inside a box sub-grid, filled: nodes not reachable from the box's side faces
    (the 4 faces not crossed by `axis`) through free nodes (F >= wall) are the inside -> boolean mask"""
    free = Fb >= wall
    reach = np.zeros_like(free)
    ax = int(np.argmax(np.abs(axis)))
    for d in range(3):
        if d == ax:
            continue
        for e in (0, -1):
            sl = [slice(None)] * 3; sl[d] = e
            reach[tuple(sl)] = free[tuple(sl)]
    for _ in range(4 * max(Fb.shape)):
        r2 = reach.copy()
        r2[1:] |= reach[:-1]; r2[:-1] |= reach[1:]
        r2[:, 1:] |= reach[:, :-1]; r2[:, :-1] |= reach[:, 1:]
        r2[:, :, 1:] |= reach[:, :, :-1]; r2[:, :, :-1] |= reach[:, :, 1:]
        r2 &= free
        if np.array_equal(r2, reach):
            break
        reach = r2
    return (Fb >= 0) & ~reach


def segment_array(F, lo, step, PV, PFc, PUV, PTI, spec):
    """opt-in light.segment_array (drakescale_beast v2.1, artist 2026-10-17: "lets just extend and duplicate exisiting
    tails and end in a point"; house rule: a tail is lengthened by DUPLICATING the generated tail segment, never a
    smooth tube). Source = the proxy inside `box` [lo, hi] (stage-1 frame) around one generated segment whose axis runs
    `axis` [from, to] (from = the body end, to = the free end; length L = |to - from|). N = `count` copies are laid end
    to end along `curve` (control points, uniform Catmull-Rom): copy i (1..N) is uniformly scaled s_i = taper^i, starts
    at arc length a_i (a_1 = `start_arc`, default 0; a_(i+1) = a_i + s_i L (1 - `overlap`)) and is turned so its axis
    lies on the chord curve(a_i) -> curve(a_i + s_i L) (any rotation: the axis turned onto the chord by the shortest
    arc, so a source axis and curve in one x = const plane only turn about X). The LAST copy is squashed to a POINT:
    its cross-section shrinks linearly along its axis from 1 to `tip_frac` (default 0.02). Map (source -> copy):
        q = p - from;  a = q . u;  f = 1 (+ the tip ramp);  p' = c_i + s_i R_i (a u + f (q - a u))
      - SDF: per copy the snapshot grid (F before the op) is pulled back through the inverse map, restricted to the
        box (max with the box SDF), scaled by s_i f and smooth-unioned into F (k = `k`, default 0.03) before the
        remesh; `floor_z` (optional) keeps every copy above that plane (a tail lying on the floor is flattened there).
      - bake source: the proxy faces whose centroid lies in the box are appended per copy through the same map, with
        their own UVs / texture index (winding kept: the map is orientation-preserving), like light.mirror_copy.
        Then every bake face (copied or original) in the copies' reach that has solid on BOTH sides at
        +-`drop_interior_u` (default 0.06) along its normal - buried under the next copy's shingle or inside the
        array - leaves the bake source so it cannot print the wrong colour.
    The original geometry is never moved or removed. -> F, PV, PFc, PUV, PTI, report"""
    t0_ = time.time()
    blo, bhi = (np.asarray(spec["box"][0], float), np.asarray(spec["box"][1], float))
    A0, A1 = (np.asarray(spec["axis"][0], float), np.asarray(spec["axis"][1], float))
    L = float(np.linalg.norm(A1 - A0)); u = (A1 - A0) / L
    N = int(spec["count"]); taper = float(spec.get("taper", 0.88)); ov = float(spec.get("overlap", 0.2))
    tip = float(spec.get("tip_frac", 0.02)); k = float(spec.get("k", 0.03)); tipl = float(spec.get("tip_length", 1.0))
    s_first = float(spec.get("first_scale", taper))
    fz = spec.get("floor_z")
    curve = _catmull(np.asarray(spec["curve"], float), int(spec.get("samples_per_span", 32)))
    seg = np.linalg.norm(np.diff(curve, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])

    def at(s_):
        s_ = float(np.clip(s_, 0.0, arc[-1]))
        j = int(np.clip(np.searchsorted(arc, s_) - 1, 0, len(seg) - 1))
        w = (s_ - arc[j]) / max(seg[j], 1e-12)
        return curve[j] * (1 - w) + curve[j + 1] * w

    def rot(a, b):                       # the shortest-arc rotation taking unit a onto unit b (Rodrigues)
        v = np.cross(a, b); c = float(np.dot(a, b)); s = float(np.linalg.norm(v))
        if s < 1e-12:
            return np.eye(3) if c > 0 else -np.eye(3)
        Kx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
        ang = math.atan2(s, c)
        return np.eye(3) + math.sin(ang) * Kx + (1 - math.cos(ang)) * (Kx @ Kx)

    copies = []
    a_ = float(spec.get("start_arc", 0.0))
    for i in range(1, N + 1):
        s_i = s_first * taper ** (i - 1)
        ln = s_i * L * (tipl if i == N else 1.0)
        c_ = at(a_); e_ = at(a_ + ln)
        ch = e_ - c_
        if np.linalg.norm(ch) < 1e-9:
            break
        R = rot(u, ch / np.linalg.norm(ch))
        copies.append({"i": i, "s": s_i, "c": c_, "R": R, "arc": a_, "last": i == N, "ax": tipl if i == N else 1.0})
        a_ += s_i * L * (1.0 - ov)
    if copies:
        copies[-1]["last"] = True

    def fwd(P, cp):                      # source -> copy
        q = P - A0
        a = q @ u
        f = np.ones(len(P))
        if cp["last"]:
            f = 1.0 + (tip - 1.0) * np.clip(a / L, 0.0, 1.0)
        loc = cp["ax"] * a[:, None] * u + f[:, None] * (q - a[:, None] * u)
        return cp["c"] + cp["s"] * loc @ cp["R"].T

    F = F.copy()
    shp = np.array(F.shape)
    filled = 0
    if spec.get("fill", True):          # the light skin is hollow: the segment's cavity is filled (in the source and,
        b0 = np.clip(np.floor((blo - lo) / step).astype(int), 0, shp - 1)     # so the cut root end is closed, in place)
        b1 = np.clip(np.ceil((bhi - lo) / step).astype(int) + 1, 0, shp)
        sb = tuple(slice(a0, b0_) for a0, b0_ in zip(b0, b1))
        cav = _fill_cavity(F[sb], float(spec.get("fill_wall_u", step)), A1 - A0)
        filled = int(cav.sum())
        F[sb] = np.where(cav, -step, F[sb])
    F0 = F.copy()
    corners = np.array([[x, y, z] for x in (blo[0], bhi[0]) for y in (blo[1], bhi[1]) for z in (blo[2], bhi[2])])
    nodes_added = 0
    rows = []
    reach_lo, reach_hi = np.full(3, np.inf), np.full(3, -np.inf)
    for cp in copies:
        cc = fwd(corners, dict(cp, last=False))           # the unsquashed copy's box bounds the squashed one
        g = k + 3 * step
        clo, chi = cc.min(0) - g, cc.max(0) + g
        reach_lo, reach_hi = np.minimum(reach_lo, clo), np.maximum(reach_hi, chi)
        i0 = np.clip(np.floor((clo - lo) / step).astype(int), 0, shp - 1)
        i1 = np.clip(np.ceil((chi - lo) / step).astype(int) + 1, 0, shp)
        axes = [lo[d] + step * np.arange(i0[d], i1[d]) for d in range(3)]
        X, Y, Zg = np.meshgrid(*axes, indexing="ij")
        Pw = np.stack([X.ravel(), Y.ravel(), Zg.ravel()], 1)
        ql = ((Pw - cp["c"]) @ cp["R"]) / cp["s"]          # copy-local, source scale (R orthonormal: R^-1 = R^T)
        a = (ql @ u) / cp["ax"]
        ql = ql - (ql @ u)[:, None] * u + a[:, None] * u
        f = np.ones(len(Pw))
        if cp["last"]:
            f = np.maximum(1.0 + (tip - 1.0) * np.clip(a / L, 0.0, 1.0), tip)
        Ps = A0 + a[:, None] * u + (ql - a[:, None] * u) / f[:, None]
        d = np.maximum(_trilinear(F0, lo, step, Ps), PF.sd_box(Ps, blo, bhi)) * cp["s"] * f * min(1.0, cp["ax"])
        if fz is not None:
            d = np.maximum(d, float(fz) - Pw[:, 2])
        d = np.where(np.isfinite(d), d, 1e3).reshape(X.shape)     # outside the grid = far outside
        sl = tuple(slice(a0, b0) for a0, b0 in zip(i0, i1))
        cur = F[sl]
        hh = np.clip(0.5 + 0.5 * (d - cur) / k, 0.0, 1.0)
        new = d * (1 - hh) + cur * hh - k * hh * (1 - hh)
        nodes_added += int(np.count_nonzero((new < 0) & (cur >= 0)))
        F[sl] = new
        rows.append({"i": cp["i"], "scale": round(cp["s"], 4), "arc_u": round(cp["arc"], 4),
                     "start": cp["c"].round(4).tolist(), "squashed_to_point": bool(cp["last"]),
                     "clipped_by_grid": bool(np.any(np.floor((clo - lo) / step) < 0) or np.any(np.ceil((chi - lo) / step) + 1 > shp))})
    # bake source: the box's proxy faces appended per copy
    cen = PV[PFc].mean(1)
    sel = np.all((cen >= blo) & (cen <= bhi), 1)
    fs = PFc[sel]
    used = np.unique(fs)
    remap = np.full(len(PV), -1, np.int64); remap[used] = np.arange(len(used))
    n_src = len(PFc)
    Vs, Fs_, UVs, TIs = [PV], [PFc], [PUV], [PTI]
    off = len(PV)
    for cp in copies:
        Vs.append(fwd(PV[used], cp)); Fs_.append(remap[fs] + off); UVs.append(PUV[used]); TIs.append(PTI[sel])
        off += len(used)
    PV = np.vstack(Vs); PFc = np.vstack(Fs_).astype(PFc.dtype); PUV = np.vstack(UVs)
    PTI = np.concatenate(TIs).astype(PTI.dtype)
    # buried bake faces (solid on both sides) in the copies' reach + the box leave the bake source
    thr = float(spec.get("drop_interior_u", 0.06))
    T_ = PV[PFc]
    cen = T_.mean(1)
    rlo, rhi = np.minimum(reach_lo, blo), np.maximum(reach_hi, bhi)
    inr = np.all((cen >= rlo) & (cen <= rhi), 1)
    nrm = np.cross(T_[:, 1] - T_[:, 0], T_[:, 2] - T_[:, 0])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]
    deep = np.zeros(len(PFc), bool)
    ii = np.flatnonzero(inr)
    deep[ii] = (_trilinear(F, lo, step, cen[ii] + thr * nrm[ii]) < 0) & (_trilinear(F, lo, step, cen[ii] - thr * nrm[ii]) < 0)
    is_copy = np.arange(len(PFc)) >= n_src
    PFc, PTI = PFc[~deep], PTI[~deep]
    rep = {"box": spec["box"], "axis": spec["axis"], "segment_length_u": round(L, 4), "count": len(copies),
           "taper": taper, "first_scale": s_first, "overlap": ov, "tip_frac": tip, "tip_length": tipl, "k": k, "cavity_nodes_filled": filled, "floor_z": fz,
           "curve_length_u": round(float(arc[-1]), 4),
           "array_length_u": round(float(copies[-1]["arc"] + copies[-1]["s"] * L * tipl) if copies else 0.0, 4),
           "tip_point": (fwd(A1[None], copies[-1])[0].round(4).tolist() if copies else None),
           "copies": rows, "faces_per_copy": int(sel.sum()), "faces_appended": int(sel.sum()) * len(copies),
           "grid_nodes_added_inside": nodes_added,
           "bake_faces_dropped_buried": {"copies": int((deep & is_copy).sum()), "proxy": int((deep & ~is_copy).sum()),
                                         "thr_u": thr},
           "seconds": round(time.time() - t0_, 1), "why": spec.get("why"),
           "rule": "the proxy segment in `box` copied N times along `curve` (shortest-arc turn onto each chord, uniform "
                   "scale taper^i, the last copy's cross-section ramped to tip_frac = a point); each copy's pulled-back "
                   "SDF smooth-unioned into the grid before the remesh; its faces + UVs appended to the bake source; "
                   "bake faces buried on both sides dropped; originals untouched"}
    return F, PV, PFc, PUV, PTI, rep


def yaw_whole(V, F, spec):
    """opt-in light.yaw (raven_shade v2, artist 2026-10-17: "our characters should be symmetrical"): Pixal3D copied the
    sheet's 3/4 FRONT by building the WHOLE creature turned about Z (body, cloak, legs and head share one symmetry plane
    ~30 deg off the X = 0 plane), which no bone of the contract rig can undo (the repose turns chains, not the root). The
    finished low is turned as ONE rigid body, i.e. the root orientation: after the bake (the texture rides on the UVs),
    before the repose, so the rig, repose targets, rigid boxes and focus boxes are given in the turned frame (stage-1
    boxes - dense, recolour3d - stay in the stage-1 frame). Absent key = no change (new units only).
        p' = Rz(deg) (p - about) + to        (about, to: [x, y]; z untouched, so floor contact and height are kept)"""
    a = math.radians(float(spec["deg"]))
    ab = np.array([float(spec.get("about", [0.0, 0.0])[0]), float(spec.get("about", [0.0, 0.0])[1]), 0.0])
    to = np.array([float(spec.get("to", [0.0, 0.0])[0]), float(spec.get("to", [0.0, 0.0])[1]), 0.0])
    V1 = (np.asarray(V, float) - ab) @ PR.Rz(a).T + to
    rep = {"deg": float(spec["deg"]), "about": ab[:2].tolist(), "to": to[:2].tolist(),
           "mirror_after": mirror_to_surface(V1, F, 0.0), "why": spec.get("why"),
           "rule": "p' = Rz(deg) (p - about) + to on every vertex of the finished low (one rigid turn about Z, the root "
                   "orientation; no vertex moves relative to another)"}
    return V1, rep


def _read_glb_mesh(path):
    """one-file glTF binary -> (V (n, 3) as stored, F (m, 3), UV (n, 2), base-colour image (h, w, 3) uint8 top-down);
    numpy + bpy only (Blender's python has no PIL / scipy): the accessors are read straight from the BIN chunk and the
    embedded PNG is decoded by bpy.data.images.load. Node transforms other than identity are refused."""
    import struct
    raw = open(path, "rb").read()
    n0 = struct.unpack("<I", raw[12:16])[0]
    doc = json.loads(raw[20:20 + n0].decode())
    o1 = 20 + n0
    n1 = struct.unpack("<I", raw[o1:o1 + 4])[0]
    blob = raw[o1 + 8:o1 + 8 + n1]
    CT = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
    NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def acc(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        dt = np.dtype(CT[a["componentType"]]); nc = NC[a["type"]]
        assert bv.get("byteStride") in (None, dt.itemsize * nc), "interleaved accessor"
        o = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.frombuffer(blob, dt, a["count"] * nc, o).reshape(a["count"], nc)
    for nd in doc.get("nodes") or []:
        assert not any(k in nd for k in ("matrix", "translation", "rotation", "scale")), "node transform in head glb"
    V, F, UV, img_i = [], [], [], None
    off = 0
    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            p_ = acc(prim["attributes"]["POSITION"]).astype(np.float64)
            f_ = acc(prim["indices"]).reshape(-1, 3).astype(np.int64)
            u_ = acc(prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
            m_ = doc["materials"][prim["material"]]
            src = doc["textures"][m_["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
            assert img_i in (None, src), "more than one base-colour texture"
            img_i = src
            V.append(p_); F.append(f_ + off); UV.append(u_); off += len(p_)
    im = doc["images"][img_i]
    bv = doc["bufferViews"][im["bufferView"]]
    png = blob[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    open(tmp, "wb").write(png)
    bim = bpy.data.images.load(tmp, check_existing=False)
    bim.colorspace_settings.name = "sRGB"
    w_, h_ = bim.size
    px = np.empty(w_ * h_ * 4, np.float32); bim.pixels.foreach_get(px)
    bpy.data.images.remove(bim)
    os.remove(tmp)
    tex = np.clip(np.rint(px.reshape(h_, w_, 4)[::-1, :, :3] * 255.0), 0, 255).astype(np.uint8)
    return np.concatenate(V), np.concatenate(F), np.concatenate(UV), tex


def _graft_tone(tex, tone):
    """the graft texture's tone, texture only (light.head_graft.tone): CIELab, per texel, L* through the piecewise-linear
    `L_map` [[L_src, L_dst], ...] and a* / b* shifted by `ab_shift_by_L` [[L, da, db], ...] (interpolated at the SOURCE
    L*), so every texel keeps its own detail (the shift is the per-band median difference measured offline against the
    sheet); texels matching `keep` (a Lab range, e.g. the orange eye) are left as generated"""
    flat = tex.reshape(-1, 3).astype(np.float64)
    Lb = PF.srgb_to_lab(flat)
    L0 = Lb[:, 0].copy()
    out = Lb.copy()
    if tone.get("L_map"):
        lm = np.asarray(tone["L_map"], float)
        out[:, 0] = np.interp(L0, lm[:, 0], lm[:, 1])
    if tone.get("ab_shift_by_L"):
        ab = np.asarray(tone["ab_shift_by_L"], float)
        out[:, 1] += np.interp(L0, ab[:, 0], ab[:, 1])
        out[:, 2] += np.interp(L0, ab[:, 0], ab[:, 2])
    w = np.ones(len(flat)) * float(tone.get("strength", 1.0))
    if tone.get("keep"):
        w = w * (1.0 - PF._lab_select_w(Lb, tone["keep"]))
    new = PF.lab_to_srgb(out)
    res = np.clip(np.rint(flat * (1 - w[:, None]) + new * w[:, None]), 0, 255).astype(np.uint8).reshape(tex.shape)
    kept = int(((1.0 - w) > 0.5).sum())
    return res, {"texels": int(len(flat)), "texels_kept_as_generated": kept,
                 "L_p10_50_90_before": np.round(np.percentile(L0, [10, 50, 90]), 2).tolist(),
                 "L_p10_50_90_after": np.round(np.percentile(PF.srgb_to_lab(res.reshape(-1, 3).astype(float))[:, 0],
                                                             [10, 50, 90]), 2).tolist()}


def _graft_cut_sd(P, c):
    """signed distance (negative inside) of a light.head_graft.cuts shape: {"box": [lo, hi]} | {"capsule": [a, b, r]} |
    {"sphere": [c, r]} (sheet units, body frame)"""
    if "capsule" in c:
        return PF.sd_capsule(P, *c["capsule"])
    if "sphere" in c:
        return np.linalg.norm(P - np.asarray(c["sphere"][0], float), axis=1) - float(c["sphere"][1])
    return PF.sd_box(P, *c["box"])


def head_graft(F, lo, step, PV, PFc, PUV, PTI, tex_key, tex_proxy, spec, band_u, inflate_u, pad=True):
    """opt-in light.head_graft (drakescale_beast v2, artist 2026-10-17: "compare this head to the ref, its way too
    compressed and looks smooshed where we cant see eyes or any other face details"): a SEPARATE Pixal3D head (generated
    from the sheet's HEAD DETAIL panel, `glb`) replaces the proxy's own head. The proxy's head is removed beforehand by
    ordinary light.cuts (stage 1: grid + bake source). Absent key = no change (new units only).
      1. frame: the head glb's coordinates p (as stored) -> q = Rx(roll) Ry(yaw) p - (0, 0, mid_z): the snout along -q0,
         up +q1, its own mirror plane q2 = 0 (yaw / roll / mid_z measured offline by a mirror search on the face).
      2. the GOOD half q2 >= 0 (the side the sheet drew) is kept and MIRRORED across q2 = 0: the SDF is evaluated at the
         folded point (q0, q1, |q2|), so the head is exactly symmetric; the face is the generated one, never re-sculpted.
      3. body = (s q2, s (cos b q0 - sin b q1) + ty, s (sin b q0 + cos b q1) + tz): one similarity (scale s, pitch b,
         `place` [ty, tz]) fitted offline to the sheet's side-view landmarks (snout tip, eye, chin); +q2 -> +x keeps it
         a proper rotation (no reflection).
      4. clip: the head block's back (a flat cut where the panel ended) is removed beyond q0 > `clip_q0` (smooth max,
         `clip_k`), so the cap sits inside the neck.
      5. SDF: sign = inside the head mesh (ray-parity occupancy grid in the glb frame, majority of the 3 axes, cell
         `occ_step`), magnitude = BVH distance x s (clamped to the grid band), minus the stage-1 inflate (the proxy's
         surface sits min_half_thickness x step outside it); smooth UNION into the grid (`k`) before the watertight
         remesh, so the neck blends. The grid is padded if the head reaches beyond it.
      6. bake source: the good half's triangles (centroid q2 >= 0, q0 <= clip_q0) + their mirror (winding reversed),
         with the head texture (optionally toned, _graft_tone) packed BESIDE the proxy texture in one atlas (UVs
         remapped); bake-source faces buried deeper than `drop_interior_u` inside the new grid (the head block's
         buried neck stump, the proxy neck faces under the head) and graft faces floating more than `drop_outside_u`
         outside it (opt-in) leave the bake source so the cage stays tight.
      opt-in shaping (absent = none): `stump` {q1, to_q1}: the cross-section at q1 continued straight down (head frame)
      to to_q1 (a throat where the panel cut the head block's bottom); `taper` {from_q0, to_q0, shrink_u}: the SDF
      offset by shrink_u x smoothstep over q0 from..to (the block's back shrinks into the neck instead of a flat cap).
      opt-in (vampwarrior v6: a 4-view head kept WHOLE incl. its hair; absent = unchanged): `shell_u` (body units): every
      node within shell_u (+ the stage-1 inflate) of the head's surface is inside (the SDF unioned with the thickened
      surface band |d| - inflate - shell_u), so a head built of THIN or HOLLOW walls (head5: ray parity finds 2 % of its
      box inside; 475 open / 836 non-manifold edges) is sampled consistently by a grid finer than the wall (the light.fine
      grid otherwise aliases the walls into a lattice of pits), and a near node the parity calls inside while it lies in
      front of its nearest face ((p - nearest) . n >= 0: a parity ray line flipped by a hole) is outside;
      `cuts`: box / capsule / sphere volumes (body frame,
      mirrored in x like the fold) where the graft is removed (its own collar / bust under a head kept with its hair);
      its triangles there leave the bake source too.
    -> F, lo, PV, PFc, PUV, PTI, atlas (h, w, 3), report"""
    t0_ = time.time()
    path = os.path.join(ROOT, spec["glb"])
    import hashlib
    hsha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if spec.get("sha256_prefix"):
        assert hsha.startswith(spec["sha256_prefix"]), "head glb sha %s != %s" % (hsha[:12], spec["sha256_prefix"])
    Vg, Fg_, UVg, texg = _read_glb_mesh(path)
    a, r = math.radians(float(spec["yaw_deg"])), math.radians(float(spec.get("roll_deg", 0.0)))
    Ry = np.array([[math.cos(a), 0.0, -math.sin(a)], [0.0, 1.0, 0.0], [math.sin(a), 0.0, math.cos(a)]])
    Rx_ = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(r), -math.sin(r)], [0.0, math.sin(r), math.cos(r)]])
    RH = Rx_ @ Ry
    mz = float(spec.get("mid_z", 0.0))
    s = float(spec["scale"]); b = math.radians(float(spec.get("pitch_deg", 0.0)))
    cb, sb = math.cos(b), math.sin(b)
    ty, tz = [float(v) for v in spec["place"]]
    xc = float(spec.get("clip_q0", 1e9)); kc = float(spec.get("clip_k", 0.0))

    def to_q(P):
        return P @ RH.T - np.array([0.0, 0.0, mz])

    def q_to_body(Q):
        return np.stack([s * Q[:, 2], s * (cb * Q[:, 0] - sb * Q[:, 1]) + ty, s * (sb * Q[:, 0] + cb * Q[:, 1]) + tz], 1)

    def body_to_q(Bp):
        u_ = (Bp[:, 1] - ty) / s; w_ = (Bp[:, 2] - tz) / s
        return np.stack([cb * u_ + sb * w_, -sb * u_ + cb * w_, Bp[:, 0] / s], 1)
    # welded copy of the head mesh (the glb splits vertices at UV seams) for the BVH / parity
    qk = np.round(Vg / 1e-6).astype(np.int64)
    _, first, inv = np.unique(qk, axis=0, return_index=True, return_inverse=True)
    Vw = Vg[first]; Fw = inv.ravel()[Fg_]
    bvh = BVHTree.FromPolygons(Vw.tolist(), Fw.tolist())
    # occupancy grid (glb frame) by ray parity, majority over the 3 axes
    hs = float(spec.get("occ_step", 0.004))
    glo = Vw.min(0) - 2 * hs
    gn = np.ceil((Vw.max(0) + 2 * hs - glo) / hs).astype(int) + 1
    votes = np.zeros(tuple(gn), np.int8)
    ncast = 0
    for ax in range(3):
        o1, o2 = [d for d in range(3) if d != ax]
        dvec = Vector([1.0 if d == ax else 0.0 for d in range(3)])
        pos = glo[ax] + hs * np.arange(gn[ax])
        span = float(gn[ax] * hs + 4 * hs)
        for i in range(gn[o1]):
            for j in range(gn[o2]):
                org = [0.0, 0.0, 0.0]
                org[ax] = glo[ax] - 2 * hs; org[o1] = glo[o1] + hs * i; org[o2] = glo[o2] + hs * j
                o_ = Vector(org)
                ts = []
                t_acc = 0.0
                while True:
                    hit = bvh.ray_cast(o_, dvec, span - t_acc)
                    ncast += 1
                    if hit[0] is None:
                        break
                    th = (hit[0] - Vector(org)).length
                    if not ts or th - ts[-1] > 1e-7:
                        ts.append(th)
                    t_acc = th + 1e-6
                    o_ = Vector(org) + dvec * t_acc
                if len(ts) >= 2:
                    par = np.searchsorted(np.asarray(ts), pos - (glo[ax] - 2 * hs)) % 2 == 1
                    idx = [slice(None)] * 3
                    idx[o1] = i; idx[o2] = j
                    votes[tuple(idx)] += par.astype(np.int8)
    occ = votes >= 2

    def inside_glb(P):
        ij = np.rint((P - glo) / hs).astype(np.int64)
        ok = np.all((ij >= 0) & (ij < gn), 1)
        out = np.zeros(len(P), bool)
        ijc = np.clip(ij, 0, gn - 1)
        out[ok] = occ[ijc[ok, 0], ijc[ok, 1], ijc[ok, 2]]
        return out
    # the graft's body-frame box (good half + mirror, clipped), then the grid sub-block (padded if needed)
    Qv = to_q(Vw)
    keepv = (Qv[:, 2] >= 0) & (Qv[:, 0] <= xc)
    Bv = q_to_body(Qv[keepv])
    Bv = np.vstack([Bv, Bv * np.array([-1.0, 1.0, 1.0])])
    k = float(spec.get("k", 0.05))
    g_ = k + 3 * step + band_u
    blo, bhi = Bv.min(0) - g_, Bv.max(0) + g_
    pad_lo = np.maximum(np.ceil((lo - blo) / step).astype(int), 0)
    hi_grid = lo + step * (np.array(F.shape) - 1)
    pad_hi = np.maximum(np.ceil((bhi - hi_grid) / step).astype(int), 0)
    if pad and (pad_lo.any() or pad_hi.any()):
        F = np.pad(F, [(int(pad_lo[d]), int(pad_hi[d])) for d in range(3)], mode="constant",
                   constant_values=float(np.abs(F).max()))
        lo = lo - step * pad_lo
    shp = np.array(F.shape)
    i0 = np.clip(np.floor((blo - lo) / step).astype(int), 0, shp - 1)
    i1 = np.clip(np.ceil((bhi - lo) / step).astype(int) + 1, 0, shp)
    axes = [lo[d] + step * np.arange(i0[d], i1[d]) for d in range(3)]
    X, Y, Zg = np.meshgrid(*axes, indexing="ij")
    Pn = np.stack([X.ravel(), Y.ravel(), Zg.ravel()], 1)
    Qn = body_to_q(Pn)
    Qn[:, 2] = np.abs(Qn[:, 2])                          # the fold: the good half, mirrored
    st = spec.get("stump")
    below = np.zeros(len(Qn))
    if st:                                               # opt-in: the throat's cross-section at q1 = stump.q1 continued
        q1s, q1e = float(st["q1"]), float(st["to_q1"])   # straight down (head frame) to to_q1, so the neck below the
        below = np.maximum(q1e - Qn[:, 1], 0.0)          # head block's flat bottom is filled (capped at to_q1)
        Qn[:, 1] = np.maximum(Qn[:, 1], q1s)
    q0_raw = Qn[:, 0].copy()
    Pg = (Qn + np.array([0.0, 0.0, mz])) @ RH            # back to the glb frame (RH orthonormal)
    ins = inside_glb(Pg)
    dmax = band_u / s
    dist = np.full(len(Pg), dmax)
    shell_on = spec.get("shell_u") is not None
    n_veto = 0
    for n_, p_ in enumerate(Pg):
        h_ = bvh.find_nearest(Vector(p_), dmax)
        if h_[0] is not None:
            dist[n_] = h_[3]
            if shell_on and ins[n_] and (Vector(p_) - h_[0]).dot(h_[1]) >= 0.0:
                ins[n_] = False                          # opt-in: parity "inside" vetoed in front of a face
                n_veto += 1
    gval = np.where(ins, -dist, dist) * s - float(inflate_u)
    if spec.get("shell_u") is not None:                  # opt-in: the thickened surface band is inside too
        gval = np.minimum(gval, dist * s - float(inflate_u) - float(spec["shell_u"]))
    if st:
        gval = np.where(below > 0, np.maximum(gval, below * s), gval)   # capped below to_q1 only
    tp = spec.get("taper")
    if tp:                                               # opt-in: the head block's back shrinks smoothly into the neck
        t_ = np.clip((q0_raw - float(tp["from_q0"])) / (float(tp["to_q0"]) - float(tp["from_q0"])), 0.0, 1.0)
        gval = gval + float(tp["shrink_u"]) * t_ * t_ * (3.0 - 2.0 * t_)
    cq2 = spec.get("clip_q2")
    if cq2 is not None:                                  # opt-in: the side cut (|q2| = the folded half width)
        kq = float(spec.get("clip_q2_k", kc))
        dq = s * (Qn[:, 2] - float(cq2))
        if kq > 0:
            hq = np.clip(0.5 - 0.5 * (gval - dq) / kq, 0.0, 1.0)
            gval = gval * (1 - hq) + dq * hq + kq * hq * (1 - hq)
        else:
            gval = np.maximum(gval, dq)
    if xc < 1e8:
        dc = s * (Qn[:, 0] - xc)
        if kc > 0:
            hh = np.clip(0.5 - 0.5 * (gval - dc) / kc, 0.0, 1.0)     # smooth max(gval, dc)
            gval = gval * (1 - hh) + dc * hh + kc * hh * (1 - hh)
        else:
            gval = np.maximum(gval, dc)
    gcut = spec.get("cuts") or []
    if gcut:                                             # opt-in: graft parts removed (body frame, mirrored in x)
        Pm = np.stack([np.abs(Pn[:, 0]), Pn[:, 1], Pn[:, 2]], 1)
        for c_ in gcut:
            gval = np.maximum(gval, -_graft_cut_sd(Pm, c_))
    gval = gval.reshape(X.shape)
    sl = tuple(slice(a_, b_) for a_, b_ in zip(i0, i1))
    cur = F[sl]
    hh = np.clip(0.5 + 0.5 * (gval - cur) / k, 0.0, 1.0)
    new = gval * (1 - hh) + cur * hh - k * hh * (1 - hh)
    nodes_added = int(np.count_nonzero((new < 0) & (cur >= 0)))
    F = F.copy()
    F[sl] = new
    # bake source: the good half's triangles + their mirror, in the atlas
    tex_g = texg
    tone_rep = None
    if spec.get("tone"):
        tex_g, tone_rep = _graft_tone(texg, spec["tone"])
    hp, wp = tex_proxy.shape[:2]; hg, wg = tex_g.shape[:2]
    Ha = max(hp, hg)
    atlas = np.zeros((Ha, wp + wg, 3), np.uint8)
    atlas[:hp, :wp] = tex_proxy; atlas[:hg, wp:] = tex_g
    PUV = PUV.copy()
    PUV[:, 0] = np.clip(PUV[:, 0], 0.0, 1.0) * wp / (wp + wg); PUV[:, 1] = np.clip(PUV[:, 1], 0.0, 1.0) * hp / Ha
    Qg = to_q(Vg)
    cq = Qg[Fg_].mean(1)
    fsel = (cq[:, 2] >= 0) & (cq[:, 0] <= xc)
    if cq2 is not None:                                  # the side cut leaves the bake source too
        fsel &= cq[:, 2] <= float(cq2) + float(spec.get("clip_q2_bake_pad", 0.02))
    if gcut:                                             # the cut graft parts leave the bake source too
        Bc = q_to_body(cq)
        Bc[:, 0] = np.abs(Bc[:, 0])
        for c_ in gcut:
            fsel &= _graft_cut_sd(Bc, c_) >= 0.0
    fs = Fg_[fsel]
    used = np.unique(fs)
    remap = np.full(len(Vg), -1, np.int64); remap[used] = np.arange(len(used))
    Bg = q_to_body(Qg[used])
    uvg = np.stack([(wp + np.clip(UVg[used, 0], 0.0, 1.0) * wg) / (wp + wg), np.clip(UVg[used, 1], 0.0, 1.0) * hg / Ha], 1)
    n0 = len(PV)
    fA = remap[fs] + n0
    fB = remap[fs][:, [0, 2, 1]] + n0 + len(used)
    PV = np.vstack([PV, Bg, Bg * np.array([-1.0, 1.0, 1.0])])
    PUV = np.vstack([PUV, uvg, uvg])
    PFc = np.vstack([PFc, fA, fB]).astype(PFc.dtype)
    PTI = np.concatenate([PTI, np.full(2 * len(fs), int(tex_key), PTI.dtype)])
    # buried bake-source faces (in the graft's block) leave the bake source
    thr = float(spec.get("drop_interior_u", 0.06))
    cen = PV[PFc].mean(1)
    inblk = np.all((cen >= blo) & (cen <= bhi), 1)
    ij = np.clip(np.rint((cen - lo) / step).astype(np.int64), 0, shp - 1)
    fc_ = F[ij[:, 0], ij[:, 1], ij[:, 2]]
    deep = inblk & (fc_ < -thr)
    is_graft = np.arange(len(PFc)) >= len(PFc) - 2 * len(fs)
    thr_out = spec.get("drop_outside_u")          # opt-in: graft faces left floating OUTSIDE the final surface (the
    if thr_out is not None:                       # taper shrank the block's back away) leave the bake source, so the
        out_ = is_graft & (fc_ > float(thr_out))  # cage (3 x p99 proxy-to-low) stays tight
    else:
        out_ = np.zeros(len(PFc), bool)
    PFc, PTI = PFc[~(deep | out_)], PTI[~(deep | out_)]
    lm = {}
    for nm, q_ in (spec.get("landmarks_q") or {}).items():
        lm[nm] = q_to_body(np.asarray([q_], float))[0].round(4).tolist()
    Bk = q_to_body(Qv[keepv])
    rep = {"glb": spec["glb"], "glb_sha256": hsha[:16], "glb_tris": int(len(Fg_)), "glb_verts_welded": int(len(Vw)),
           "frame": {"yaw_deg": float(spec["yaw_deg"]), "roll_deg": float(spec.get("roll_deg", 0.0)), "mid_z": mz},
           "scale": s, "pitch_deg": float(spec.get("pitch_deg", 0.0)), "place_yz": [ty, tz],
           "clip_q0": xc, "clip_k": kc, "clip_q2": cq2, "k": k, "inflate_u": round(float(inflate_u), 4),
           "shell_u": spec.get("shell_u"), "parity_vetoed": n_veto, "cuts": len(gcut),
           "body_bbox": [Bv.min(0).round(4).tolist(), Bv.max(0).round(4).tolist()],
           "good_half_bbox_x": [round(float(Bk[:, 0].min()), 4), round(float(Bk[:, 0].max()), 4)],
           "landmarks_body": lm, "occupancy": {"cell": hs, "grid": gn.tolist(), "rays": ncast,
                                                "inside_cells": int(occ.sum()),
                                                "vote_disagree_cells": int(((votes > 0) & (votes < 3)).sum())},
           "grid_pad": {"lo": pad_lo.tolist(), "hi": pad_hi.tolist()}, "nodes_evaluated": int(len(Pn)),
           "grid_nodes_added_inside": nodes_added, "faces_appended": int(2 * len(fs)),
           "bake_faces_dropped_buried": {"graft": int((deep & is_graft).sum()), "proxy": int((deep & ~is_graft).sum()),
                                         "thr_u": thr},
           "bake_faces_dropped_outside": {"graft": int(out_.sum()), "thr_u": thr_out},
           "atlas": [int(Ha), int(wp + wg)], "tone": tone_rep, "seconds": round(time.time() - t0_, 1),
           "why": spec.get("why"),
           "rule": "head glb -> its own mirror frame -> the good half folded (|q2|) = an exactly symmetric head; one "
                   "similarity to the sheet side view; SDF smooth-unioned into the grid before the remesh; the good "
                   "half's triangles + mirror into the bake source with the head texture in an atlas"}
    return F, lo, PV, PFc, PUV, PTI, atlas, rep


def belly_push(V, W, B, rig, spec):
    """opt-in light.belly (storm_bear v2, artist 2026-10-17: "can we make storm bear fatter, bigger belly"): a smooth
    outward push of the belly / lower torso on the FINISHED low (after the bake and the repose, like the repose itself),
    so the baked texture rides along on the UVs. Absent key = no change (new units only).
      g(z)  = smoothstep(1 - |z - cz| / rz), rz = z_radius[0] below the navel cz, z_radius[1] above it (a round profile)
      front = -push_front x g x clamp((cy - y) / y_full, 0, 1) x smoothstep(1 - |x - cx| / front_x_radius)   (y only;
              behind cy nothing moves forward: the back / coat stays)
      side  = push_side x g x clamp((x - cx) / x_full, -1, 1) x (1 - smoothstep over side_back_ramp [y0, y1] of y)
              (the sides; the coat behind y1 is not widened)
    Chains named in `exclude` (the legs) keep their shape: the field is scaled by 1 - their skin weight. Chains named in
    `swing` (the arms) are swung OUT as rigid bodies about their root joint (a rotation about y, angle =
    atan(push_side x swing_gain / (root_z - cz))) by linear blend skinning with the same weights, so they clear the
    wider belly and keep their shape; the rig's chain joints get the same rotation. W, B: the repose's skin weights and
    bones (or the contract rig's). Deterministic numpy. -> V_new, rig_new, report"""
    V = np.asarray(V, float)
    c = np.asarray(spec["centre"], float)
    rz = spec.get("z_radius", [0.8, 0.8])
    rz = [float(rz), float(rz)] if not isinstance(rz, (list, tuple)) else [float(rz[0]), float(rz[1])]
    a_f, a_s = float(spec.get("push_front", 0.0)), float(spec.get("push_side", 0.0))
    yf, xf = float(spec.get("y_full", 0.6)), float(spec.get("x_full", 0.9))
    fxr = float(spec.get("front_x_radius", 1e9))
    sbr = spec.get("side_back_ramp")

    def ss(t):
        t = np.clip(t, 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)
    dz = V[:, 2] - c[2]
    g = ss(1.0 - np.abs(dz) / np.where(dz < 0, rz[0], rz[1]))
    D = np.zeros_like(V)
    D[:, 1] = -a_f * g * np.clip((c[1] - V[:, 1]) / yf, 0.0, 1.0) * ss(1.0 - np.abs(V[:, 0] - c[0]) / fxr)
    hb = 1.0 - ss((V[:, 1] - float(sbr[0])) / (float(sbr[1]) - float(sbr[0]))) if sbr else 1.0
    D[:, 0] = a_s * g * np.clip((V[:, 0] - c[0]) / xf, -1.0, 1.0) * hb

    def chain_ids(ch):
        return [j for j, b in enumerate(B) if b[0].rsplit(".", 2)[0] == ch or b[0].rsplit(".", 1)[0] == ch]
    ex_ids = [j for ch in spec.get("exclude") or [] for j in chain_ids(ch)]
    sw_ids = [j for ch in spec.get("swing") or [] for j in chain_ids(ch)]
    keep = np.clip(1.0 - W[:, ex_ids + sw_ids].sum(1), 0.0, 1.0) if ex_ids + sw_ids else np.ones(len(V))
    Dk = D * keep[:, None]
    V1 = V + Dk
    mv = np.linalg.norm(Dk, axis=1)
    rep = {"centre": c.tolist(), "z_radius": rz, "push_front_u": a_f, "push_side_u": a_s, "y_full": yf, "x_full": xf,
           "front_x_radius": fxr, "side_back_ramp": sbr, "field_max_u": round(float(mv.max()), 4),
           "verts_pushed": int((mv > 1e-4).sum()), "excluded_chains": spec.get("exclude") or [], "why": spec.get("why")}
    rig = dict(rig)
    if sw_ids:
        gain = float(spec.get("swing_gain", 1.0))
        rows, Mw, chains = [], {}, []
        MXv = np.array([-1.0, 1.0, 1.0])
        for ch in rig.get("chains") or []:
            ch = dict(ch)
            if ch["name"] in (spec.get("swing") or []):
                root = np.asarray(ch["points"][0], float)          # the L side (x > 0); R = its mirror
                ang = math.atan2(a_s * gain, max(root[2] - c[2], 1e-6))
                for s, sg in (("L", -1.0), ("R", 1.0)):           # L: the hand goes +x, R: -x
                    src = ch.get("points_R") if (s == "R" and ch.get("points_R")) else None
                    r_ = np.asarray(src[0], float) if src else (root * MXv if s == "R" else root)
                    cb, sb_ = math.cos(sg * ang), math.sin(sg * ang)
                    Ry = np.array([[cb, 0.0, sb_], [0.0, 1.0, 0.0], [-sb_, 0.0, cb]])
                    Ms = PR.affine(Ry, r_, r_)
                    for j, b in enumerate(B):
                        if b[5] == s and b[0].rsplit(".", 2)[0] == ch["name"]:
                            Mw[j] = Ms
                    if s == "L":
                        ch["points"] = [PR.apply_aff(Ms, np.asarray(p, float)[None])[0].round(5).tolist() for p in ch["points"]]
                    elif src:
                        ch["points_R"] = [PR.apply_aff(Ms, np.asarray(p, float)[None])[0].round(5).tolist() for p in src]
                rows.append({"chain": ch["name"], "swing_out_deg": round(math.degrees(ang), 3),
                             "root": root.round(4).tolist(), "lever_u": round(float(root[2] - c[2]), 4)})
            chains.append(ch)
        rig["chains"] = chains
        dS = np.zeros_like(V)
        for j, M_ in Mw.items():
            w = W[:, j]
            nz = w > 0
            if nz.any():
                dS[nz] += w[nz, None] * (PR.apply_aff(M_, V1[nz]) - V1[nz])
        V1 = V1 + dS
        rep["swing"] = rows
        rep["swing_max_move_u"] = round(float(np.linalg.norm(dS, axis=1).max()), 4)
    return V1, rig, rep


def belly_metrics(V, W, B, cz, limb_chains=("arm", "leg"), band=0.06):
    """the torso (vertices whose limb-chain skin weight < 0.5) in the band |z - cz| < band: front-view width (x p1..p99)
    and side-view depth (|x| < 0.6: y p1..p99, belly front to the back of the coat), and the belly front y"""
    a = [j for j, b in enumerate(B) if b[0].split(".")[0] in limb_chains]
    t = (np.abs(V[:, 2] - cz) < band) & ((W[:, a].sum(1) if a else 0.0) < 0.5)
    T = V[t]
    c = T[np.abs(T[:, 0]) < 0.6]
    x0, x1 = np.percentile(T[:, 0], [1, 99])
    y0, y1 = np.percentile(c[:, 1], [1, 99])
    return {"z_u": round(float(cz), 4), "width_x_u": round(float(x1 - x0), 4), "depth_y_u": round(float(y1 - y0), 4),
            "belly_front_y_u": round(float(y0), 4)}


# ======================================================================================================================
# v5.2 opt-ins (vampwarrior, artist 2026-10-25 "why is it look unpolished and choppy"): light.uv, light.smooth,
# light.paint_project. Absent keys = no change.
# ======================================================================================================================
def _face_arrays(me):
    uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 3, 2)
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    return uv, co[lv.reshape(-1, 3)]


def _raster_faces(me, face_ids, R, edge_tol=0.0):
    """texels of the given triangles in the R x R UV grid -> (texel index, 3D point, face id)"""
    uv, T3 = _face_arrays(me)
    ii_, pp_, ff_ = [], [], []
    for f in face_ids:
        t2 = uv[f] * R
        x0, y0 = np.floor(t2.min(0)).astype(int) - 1; x1, y1 = np.ceil(t2.max(0)).astype(int) + 1
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, R)) + 0.5, np.arange(max(y0, 0), min(y1, R)) + 0.5)
        Pq = np.stack([xs.ravel(), ys.ravel()], 1)
        if not len(Pq):
            continue
        a_, b_, c_ = t2
        den = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den) < 1e-12:
            continue
        l0 = ((b_[1] - c_[1]) * (Pq[:, 0] - c_[0]) + (c_[0] - b_[0]) * (Pq[:, 1] - c_[1])) / den
        l1 = ((c_[1] - a_[1]) * (Pq[:, 0] - c_[0]) + (a_[0] - c_[0]) * (Pq[:, 1] - c_[1])) / den
        l2 = 1.0 - l0 - l1
        ins = (l0 >= -edge_tol) & (l1 >= -edge_tol) & (l2 >= -edge_tol)
        if not ins.any():
            continue
        B_ = np.stack([l0[ins], l1[ins], l2[ins]], 1)
        ii_.append(Pq[ins, 1].astype(int) * R + Pq[ins, 0].astype(int)); pp_.append(B_ @ T3[f]); ff_.append(np.full(int(ins.sum()), f))
    if not ii_:
        return np.zeros(0, int), np.zeros((0, 3)), np.zeros(0, int)
    return np.concatenate(ii_), np.vstack(pp_), np.concatenate(ff_)


def uv_stats(me, R):
    """UV island count (faces joined across an edge whose two UV endpoints agree), coverage (UV area share), and the
    texel density (texels per u) of the whole mesh"""
    uv, T3 = _face_arrays(me)
    n = len(T3)
    par = np.arange(n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv); Fv = lv.reshape(-1, 3)
    key = {}
    for f in range(n):
        for k in range(3):
            a, b = int(Fv[f, k]), int(Fv[f, (k + 1) % 3])
            ua, ub = tuple(np.round(uv[f, k], 6)), tuple(np.round(uv[f, (k + 1) % 3], 6))
            e = (a, b) if a < b else (b, a)
            uvk = (ua, ub) if a < b else (ub, ua)
            if e in key:
                g, uvg = key[e]
                if uvg == uvk:
                    ra, rb = find(f), find(g)
                    if ra != rb:
                        par[ra] = rb
            else:
                key[e] = (f, uvk)
    roots = len({find(f) for f in range(n)})
    a_uv = 0.5 * np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) -
                        (uv[:, 2, 0] - uv[:, 0, 0]) * (uv[:, 1, 1] - uv[:, 0, 1]))
    a3 = 0.5 * np.linalg.norm(np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0]), axis=1)
    return {"islands": int(roots), "coverage_pct": round(100 * float(a_uv.sum()), 2),
            "texels_per_u": round(float(np.sqrt(a_uv.sum() * R * R / max(a3.sum(), 1e-12))), 1)}, a_uv, a3


def _face_pairs(LF):
    nf = len(LF)
    ek = np.sort(np.stack([LF[:, [0, 1, 2]].ravel(), LF[:, [1, 2, 0]].ravel()], 1), 1)
    ff = np.repeat(np.arange(nf), 3)
    o = np.lexsort((ek[:, 1], ek[:, 0])); eks = ek[o]; ffs = ff[o]
    same = np.all(eks[1:] == eks[:-1], 1)
    return ffs[:-1][same], ffs[1:][same]


def _components(nf, pa, pb, lab_):
    """connected components of the faces joined across shared edges with equal label (min-label propagation)"""
    par = np.arange(nf)
    m = lab_[pa] == lab_[pb]
    a_, b_ = pa[m], pb[m]
    for _ in range(200):
        lo_ = np.minimum(par[a_], par[b_])
        if not ((par[a_] != lo_) | (par[b_] != lo_)).any():
            break
        np.minimum.at(par, a_, lo_); np.minimum.at(par, b_, lo_)
        par = par[par]
    _, cid = np.unique(par, return_inverse=True)
    return cid


def _uv_charts(LV, LF, fid, min_area, iters=60):
    """box charts for the faces outside the named islands: label = the dominant axis of the face normal (+-x, +-y,
    +-z), connected components per label, charts smaller than min_area (u^2) merged into their largest neighbouring
    chart (a sliver next to a named island joins it; no two small charts swap) until none is left; named islands keep
    their own label. fid is updated in place for the slivers that joined a named island. -> chart id per face"""
    nf = len(LF)
    Nf = np.cross(LV[LF[:, 1]] - LV[LF[:, 0]], LV[LF[:, 2]] - LV[LF[:, 0]])
    A = 0.5 * np.linalg.norm(Nf, axis=1)
    Nf = Nf / np.maximum(2 * A, 1e-12)[:, None]
    dirs = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
    lab = np.where(fid >= 0, 100 + fid, np.argmax(Nf @ dirs.T, 1))
    pa, pb = _face_pairs(LF)                              # face pairs sharing an edge

    def comps(lab_):
        return _components(nf, pa, pb, lab_)
    it = 0
    for it in range(iters):
        cid = comps(lab)
        nc = int(cid.max()) + 1
        area = np.bincount(cid, weights=A, minlength=nc)
        named_c = np.zeros(nc, bool); named_c[cid[fid >= 0]] = True
        small = (area < min_area) & ~named_c
        if not small.any():
            break
        ca, cb = cid[pa], cid[pb]
        nb = ca != cb
        ca, cb = np.concatenate([ca[nb], cb[nb]]), np.concatenate([cb[nb], ca[nb]])
        ok = small[ca] & ((~small[cb]) | (area[cb] > area[ca]) | ((area[cb] == area[ca]) & (cb > ca)))   # no swaps
        ca, cb = ca[ok], cb[ok]
        if not len(ca):
            break
        best = np.full(nc, -1); besta = np.full(nc, -1.0)
        order = np.lexsort((-area[cb], ca))
        firsts = np.ones(len(order), bool); firsts[1:] = ca[order][1:] != ca[order][:-1]
        best[ca[order][firsts]] = cb[order][firsts]
        lab_c = np.zeros(nc, lab.dtype); lab_c[cid] = lab
        mv = (best >= 0)
        lab = np.where(mv[cid], lab_c[np.maximum(best, 0)][cid], lab)
        into_named = mv[cid] & (lab >= 100) & (fid < 0)       # a sliver merged into a named island joins its unwrap
        fid[into_named] = lab[into_named] - 100
    cid = comps(lab)
    return cid, {"charts": int(cid.max()) + 1, "merge_iters": it + 1, "min_area_u2": float(min_area)}


def custom_uv(low, me, LV, LF, spec, R, report, nbf=None):
    """opt-in light.uv: the named `islands` (boxes; faces by centroid, optional facing_min on -y) are each unwrapped as
    their own island(s) with seams only at their border (ANGLE_BASED unwrap), the rest as box CHARTS (method "charts",
    default: dominant-axis charts, small ones merged, `chart_min_area`; LSCM) or a smart project at `angle_deg`, then the named islands are scaled to `density` x the rest's texel density and everything is packed
    (concave shapes, `margin`). Called in edit mode right after the default unwrap (which it replaces)."""
    import bmesh
    bpy.ops.object.mode_set(mode="OBJECT")
    before = uv_stats(me, R)[0]
    bpy.ops.object.mode_set(mode="EDIT")
    C = LV[LF].mean(1)
    Nf = np.cross(LV[LF[:, 1]] - LV[LF[:, 0]], LV[LF[:, 2]] - LV[LF[:, 0]])
    Nf /= np.maximum(np.linalg.norm(Nf, axis=1), 1e-12)[:, None]
    fid = np.full(len(LF), -1)
    for k, isl in enumerate(spec.get("islands") or []):
        m = in_boxes(C, [isl]) & (fid < 0)
        if nbf is not None:                # shell faces only (light.parts keep the smart project)
            m &= np.arange(len(LF)) < int(nbf)
        if isl.get("facing_min") is not None:
            m &= (-Nf[:, 1]) > float(isl["facing_min"])
        fid[m] = k
    pa_, pb_ = _face_pairs(LF)                       # each named island keeps only its big connected pieces
    cidn = _components(len(LF), pa_, pb_, fid)
    cnt = np.bincount(cidn)
    fid[(fid >= 0) & (cnt[cidn] < int(spec.get("named_min_faces", 40)))] = -1
    margin = float(spec.get("margin", 0.004))
    bm = bmesh.from_edit_mesh(me)
    bm.faces.ensure_lookup_table(); bm.edges.ensure_lookup_table()
    for e in bm.edges:
        lf = [f.index for f in e.link_faces]
        e.seam = len({int(fid[i]) for i in lf}) > 1
    bmesh.update_edit_mesh(me)

    def select(mask):
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            f.select_set(bool(mask[f.index]))
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)
    chart_rep = None
    if spec.get("method", "charts") == "charts":   # box charts: seams at every chart border, one LSCM unwrap of all
        cid, chart_rep = _uv_charts(LV, LF, fid, float(spec.get("chart_min_area", 0.05)))
        bm.edges.ensure_lookup_table()
        for e in bm.edges:
            lf = [f.index for f in e.link_faces]
            e.seam = len({int(cid[i]) for i in lf}) > 1
        bmesh.update_edit_mesh(me)
        select(fid < 0)
        bpy.ops.uv.unwrap(method=spec.get("chart_method", "ANGLE_BASED"), margin=margin)
    else:
        select(fid < 0)
        bpy.ops.uv.smart_project(angle_limit=math.radians(float(spec.get("angle_deg", 85.0))), island_margin=margin,
                                 area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    rows = []
    for k, isl in enumerate(spec.get("islands") or []):
        if not (fid == k).any():
            rows.append({"name": isl.get("name"), "faces": 0}); continue
        if isl.get("method", "front") == "front":   # a planar FRONT projection (x, z): one island, no unwrap folds
            bm = bmesh.from_edit_mesh(me); bm.faces.ensure_lookup_table(); ul_ = bm.loops.layers.uv.active
            for fi in np.nonzero(fid == k)[0]:
                for l in bm.faces[int(fi)].loops:
                    co_ = l.vert.co
                    l[ul_].uv = (float(co_[0]), float(co_[2]))
            bmesh.update_edit_mesh(me)
        else:
            select(fid == k)
            bpy.ops.uv.unwrap(method=isl.get("method"), margin=margin)
        rows.append({"name": isl.get("name"), "faces": int((fid == k).sum())})
    # density: named islands scaled about their own UV centroid to density x the rest's texels per u
    bm = bmesh.from_edit_mesh(me); bm.faces.ensure_lookup_table()
    ul = bm.loops.layers.uv.active
    UVf = np.array([[list(l[ul].uv) for l in f.loops] for f in bm.faces])
    a_uv = 0.5 * np.abs((UVf[:, 1, 0] - UVf[:, 0, 0]) * (UVf[:, 2, 1] - UVf[:, 0, 1]) -
                        (UVf[:, 2, 0] - UVf[:, 0, 0]) * (UVf[:, 1, 1] - UVf[:, 0, 1]))
    a3 = tri_area(LV, LF)
    rest = fid < 0
    d_rest = np.sqrt(a_uv[rest].sum() / max(a3[rest].sum(), 1e-12)) if rest.any() else 1.0
    for k, isl in enumerate(spec.get("islands") or []):
        m = fid == k
        if not m.any():
            continue
        d_k = np.sqrt(a_uv[m].sum() / max(a3[m].sum(), 1e-12))
        s_ = float(isl.get("density", 1.0)) * d_rest / max(d_k, 1e-12)
        c_ = UVf[m].reshape(-1, 2).mean(0)
        for fi in np.nonzero(m)[0]:
            for l in bm.faces[int(fi)].loops:
                l[ul].uv = (c_ + (np.array(l[ul].uv) - c_) * s_).tolist()
        rows[k]["scale"] = round(float(s_), 4)
    bmesh.update_edit_mesh(me)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=margin, shape_method=spec.get("shape_method", "CONCAVE"))
    bpy.ops.object.mode_set(mode="OBJECT")
    after, a_uv2, a32 = uv_stats(me, R)
    for k, isl in enumerate(spec.get("islands") or []):
        m = fid == k
        if m.any():
            rows[k]["texels_per_u"] = round(float(np.sqrt(a_uv2[m].sum() * R * R / max(a32[m].sum(), 1e-12))), 1)
    bpy.ops.object.mode_set(mode="EDIT")
    rep = {"before": before, "after": after, "islands_named": rows, "margin": margin, "charts": chart_rep,
           "angle_deg": float(spec.get("angle_deg", 85.0)), "rule": "named boxes unwrapped alone (seams at their border), "
           "the rest smart-projected wide, named islands at density x the rest, concave pack (opt-in light.uv)"}
    report["uv"] = rep
    print("UV", json.dumps({k: rep[k] for k in ("before", "after", "islands_named", "charts")}))
    return fid


def dilate_fill(pc, R, cov, rings):
    """uncovered albedo texels take their covered 4-neighbours' mean, ring by ring (so no global-mean colour bleeds into
    an island edge); what is left after `rings` takes the covered mean"""
    img = pc.reshape(R, R, 4)
    have = cov.reshape(R, R).copy()
    n_ = 0
    for _ in range(int(rings)):
        acc = np.zeros((R, R, 3)); cnt = np.zeros((R, R))
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(have, (dy, dx), (0, 1)); col = np.roll(img[..., :3], (dy, dx), (0, 1))
            acc += col * sh[..., None]; cnt += sh
        grow = (~have) & (cnt > 0)
        if not grow.any():
            break
        img[grow, :3] = acc[grow] / cnt[grow][:, None]
        have |= grow; n_ += int(grow.sum())
    left = ~have
    if left.any():
        img[left, :3] = img[have, :3].mean(0)
    return {"texels_dilated": n_, "left_to_mean": int(left.sum()), "rings": int(rings)}


def smooth_texels(px, pa, RN, RA, me, face_ids, spec):
    """light.smooth on the bakes: the smooth faces' normal-map texels set neutral (the map was baked against the FLAT
    low; under smooth shading it would print the facets back), their AO softened by `ao_blur` masked 3x3 passes"""
    rep = {}
    ii, _, _ = _raster_faces(me, face_ids, RN, edge_tol=0.02)
    ii = np.unique(ii)
    px[ii, :3] = (0.5, 0.5, 1.0)
    rep["normal_texels_neutral"] = int(len(ii))
    nb = int(spec.get("ao_blur", 3))
    if nb:
        ia, _, _ = _raster_faces(me, face_ids, RA, edge_tol=0.02)
        ia = np.unique(ia)
        msk = np.zeros(RA * RA, bool); msk[ia] = True; msk = msk.reshape(RA, RA)
        A = pa[:, 0].reshape(RA, RA).astype(np.float64).copy()
        for _ in range(nb):
            p_ = np.pad(A, 1, mode="edge")
            Bx = sum(p_[1 + dy:1 + dy + RA, 1 + dx:1 + dx + RA] for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9.0
            A = np.where(msk, Bx, A)
        pa[:, 0] = pa[:, 1] = pa[:, 2] = A.ravel().astype(np.float32)
        rep["ao_texels_blurred"] = int(msk.sum()); rep["ao_blur_passes"] = nb
    return rep


def paint_project(pc, R, me, specs):
    """opt-in light.paint_project: an image (the sheet face panel) projected onto the BAKED albedo: every texel of a low
    face in `box` facing the front (-n_y over `facing` [f0, f1], smoothstep) maps its 3D (x, z) through the affine that
    takes the three `pairs` [[src u, v px], [dst x, z]] dst -> src, samples the image (bilinear), tones it
    (_graft_tone `tone`) and blends it in by facing x the `ellipse_px` mask {c, r, feather} in source px"""
    out = []
    uvl, T3 = _face_arrays(me)
    N = np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0]); N /= np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]
    C = T3.mean(1)
    for sp in specs:
        img = bpy.data.images.load(os.path.join(ROOT, sp["image"]), check_existing=True)
        W, H = img.size
        S = np.empty(W * H * 4, np.float32); img.pixels.foreach_get(S); S = S.reshape(H, W, 4)[::-1, :, :3]
        pr = sp["pairs"]
        Dm = np.array([[p[1][0], p[1][1], 1.0] for p in pr]); Sm = np.array([[p[0][0], p[0][1]] for p in pr])
        A = np.linalg.solve(Dm, Sm)                       # [x z 1] @ A = [u v]
        lo, hi = np.asarray(sp["box"][0], float), np.asarray(sp["box"][1], float)
        f0, f1 = [float(v) for v in sp.get("facing", [0.15, 0.45])]
        cand = np.nonzero(np.all((C >= lo) & (C <= hi), 1) & (-N[:, 1] > f0))[0]
        ii, P3, ff = _raster_faces(me, cand, R, edge_tol=0.01)
        if not len(ii):
            out.append({"name": sp.get("name"), "texels": 0}); continue
        uvp = np.stack([P3[:, 0], P3[:, 2], np.ones(len(P3))], 1) @ A
        u, v = uvp[:, 0], uvp[:, 1]
        u0 = np.clip(np.floor(u).astype(int), 0, W - 2); v0 = np.clip(np.floor(v).astype(int), 0, H - 2)
        du = np.clip(u - u0, 0, 1)[:, None]; dv = np.clip(v - v0, 0, 1)[:, None]
        col = (S[v0, u0] * (1 - du) * (1 - dv) + S[v0, u0 + 1] * du * (1 - dv) + S[v0 + 1, u0] * (1 - du) * dv +
               S[v0 + 1, u0 + 1] * du * dv)
        if sp.get("tone"):
            c8 = np.clip(np.rint(col * 255), 0, 255).astype(np.uint8).reshape(-1, 1, 3)
            col = _graft_tone(c8, sp["tone"])[0].reshape(-1, 3).astype(np.float64) / 255.0
        fw = np.clip((-N[ff, 1] - f0) / max(f1 - f0, 1e-9), 0, 1); fw = fw * fw * (3 - 2 * fw)
        el = sp.get("ellipse_px")
        if el:
            d = np.sqrt(((u - el["c"][0]) / el["r"][0]) ** 2 + ((v - el["c"][1]) / el["r"][1]) ** 2)
            t_ = np.clip((d - 1.0) * min(el["r"]) / max(float(el.get("feather", 10)), 1e-9), 0, 1)
            ew = 1.0 - t_ * t_ * (3 - 2 * t_)
        else:
            ew = np.ones(len(u))
        w = fw * ew * float(sp.get("strength", 1.0))
        o_ = np.lexsort((-w, ii)); first = np.ones(len(o_), bool); first[1:] = ii[o_][1:] != ii[o_][:-1]
        k_ = o_[first]
        pc[ii[k_], :3] = pc[ii[k_], :3] * (1 - w[k_, None]) + col[k_] * w[k_, None]
        out.append({"name": sp.get("name"), "faces": int(len(cand)), "texels": int(len(k_)),
                    "texels_ge_half": int((w[k_] > 0.5).sum()), "affine_dst_to_src": np.round(A, 4).tolist(),
                    "why": sp.get("why")})
    return out


# opt-in light.fine (vampwarrior / duskmaw v5.4, artist 2026-10-25 "clumpy, like poorly sculpted"): stage 1
# (proxy_finish.build_fine) remeshes boxes on a grid `div` times finer; here each fine grid is BLENDED into the coarse
# field over `blend_voxels` (coarse voxels) inside its border and the two remeshes are STITCHED crack-free:
#   - the coarse cells inside a box are not contoured; the fine grid covers them (its nodes: every div-th = a coarse node);
#   - on the box faces the fine values ARE the coarse grid's own piecewise-linear field on its Kuhn tets (magmoo_sdf's
#     0 -> 7 split): a Kuhn grid refined by an integer factor refines the coarse Kuhn tets, so the fine contour on a box
#     face lies exactly on the coarse contour segments (more vertices, same line);
#   - every coarse crossing on a box face has exactly one fine twin (on the fine sub-edge of the same coarse edge): the
#     twins are welded, and each coarse border triangle is fanned through the fine border vertices between its two
#     twins (the T-junctions), so the high is one closed manifold (open / non-manifold edges reported).
def _push(F, step):
    e = SD.NODE_EPS * step
    return np.where(np.abs(F) < e, np.where(F < 0, -e, e), F)


def _crossing_cells(Fp):
    nx, ny, nz = Fp.shape
    mn = Fp[:-1, :-1, :-1].copy(); mx = mn.copy()
    for dx, dy, dz in SD._CORNERS[1:]:
        v_ = Fp[dx:nx - 1 + dx, dy:ny - 1 + dy, dz:nz - 1 + dz]
        np.minimum(mn, v_, out=mn); np.maximum(mx, v_, out=mx)
    return (mn < 0) & (mx >= 0)


def _mt_cells(Fp, lo, step, cells):
    """magmoo_sdf.polygonise's marching tets (same tables, edge welding and orientation) over the listed cells of an
    already-pushed grid -> V, T, and per vertex its grid edge (lo node id, hi node id)"""
    nx, ny, nz = Fp.shape
    ci, cj, ck = cells
    gid = lambda i, j, k: (i * ny + j) * nz + k
    corner_g = np.stack([gid(ci + dx, cj + dy, ck + dz) for dx, dy, dz in SD._CORNERS], 1)
    Ff = Fp.ravel()
    tris_pairs = []
    for t in SD._TETS:
        g4 = corner_g[:, t]
        v4 = Ff[g4]
        code = ((v4 < 0).astype(np.int64) * (1 << np.arange(4))).sum(1)
        for cd, tris in SD._CASES.items():
            m = code == cd
            if not m.any():
                continue
            G = g4[m]
            for tri in tris:
                tris_pairs.append(np.stack([np.stack([G[:, a], G[:, b]], 1) for a, b in tri], 1))
    TP = np.concatenate(tris_pairs, 0)
    lo_ = np.minimum(TP[..., 0], TP[..., 1]); hi_ = np.maximum(TP[..., 0], TP[..., 1])
    NP = nx * ny * nz
    key = lo_.astype(np.int64) * NP + hi_
    uk, inv = np.unique(key.ravel(), return_inverse=True)
    ea, eb = uk // NP, uk % NP

    def pos(g):
        i = g // (ny * nz); j = (g // nz) % ny; k = g % nz
        return lo + step * np.stack([i, j, k], 1)
    va, vb = Ff[ea], Ff[eb]
    t_ = va / (va - vb)
    V = pos(ea) + t_[:, None] * (pos(eb) - pos(ea))
    T = inv.reshape(-1, 3)
    ins_end = np.where(Ff[TP[..., 0]] < 0, TP[..., 0], TP[..., 1])
    out_end = np.where(Ff[TP[..., 0]] < 0, TP[..., 1], TP[..., 0])
    grad = (pos(out_end.ravel()) - pos(ins_end.ravel())).reshape(-1, 3, 3).sum(1)
    nrm = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    flip = np.einsum("ij,ij->i", nrm, grad) < 0
    T[flip] = T[flip][:, ::-1]
    ok = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])
    return V, T[ok], ea, eb


def _kuhn_at(Fc, gi, u):
    """the coarse grid's piecewise-LINEAR field on its Kuhn tets (the field marching tets contours) at cell gi (N, 3)
    + fraction u (N, 3) in [0, 1]"""
    order = np.argsort(-u, axis=1, kind="stable")
    s_ = np.take_along_axis(u, order, 1)
    E = np.eye(3, dtype=np.int64)
    p1 = gi + E[order[:, 0]]; p2 = p1 + E[order[:, 1]]; p3 = gi + 1
    f = lambda q: Fc[q[:, 0], q[:, 1], q[:, 2]]
    return (1 - s_[:, 0]) * f(gi) + (s_[:, 0] - s_[:, 1]) * f(p1) + (s_[:, 1] - s_[:, 2]) * f(p2) + s_[:, 2] * f(p3)


def _directed_boundary(T):
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    srt = np.sort(e, 1)
    _, inv, cnt = np.unique(srt, axis=0, return_inverse=True, return_counts=True)
    return e[cnt[inv.ravel()] == 1]


def polygonise_fine(F, lo, step, FINE):
    """the coarse grid + the light.fine grids -> one closed high (V, T), report (see the block comment above)"""
    t0_ = time.time()
    Fc = _push(F, step)
    nc_ = np.array(Fc.shape)
    NPc = int(np.prod(nc_))
    cross = _crossing_cells(Fc)
    for f_ in FINE:
        i0, i1 = f_["i0"], f_["i1"]
        cross[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]] = False
    Vc, Tc, eac, ebc = _mt_cells(Fc, lo, step, np.nonzero(cross))
    del cross
    ckey = eac * NPc + ebc                            # sorted (np.unique) -> searchsorted
    cb = _directed_boundary(Tc)                       # coarse border edges (all on box faces)
    VS, TS, nv = [Vc], [], len(Vc)
    expand = {}
    rows = []
    for f_ in FINE:
        div, i0, i1 = f_["div"], f_["i0"], f_["i1"]
        blend = float(f_["spec"].get("blend_voxels", 2.0))
        Ff = f_["F"].copy()
        fn = np.array(Ff.shape)
        # the blend band: fine field -> the coarse Kuhn field at the box faces (smoothstep over blend_voxels)
        ax = [np.minimum(np.arange(fn[d]), fn[d] - 1 - np.arange(fn[d])) / div for d in range(3)]
        dmin = np.minimum(np.minimum(ax[0][:, None, None], ax[1][None, :, None]), ax[2][None, None, :])
        bi = np.nonzero(dmin < blend)
        fq = np.stack(bi, 1) / div
        a_ = np.minimum(np.floor(fq).astype(np.int64), (i1 - i0 - 1)[None, :])
        kv = _kuhn_at(Fc, a_ + i0[None, :], fq - a_)
        tb = np.clip(dmin[bi] / blend, 0.0, 1.0)
        w = tb * tb * (3.0 - 2.0 * tb)
        Ff[bi] = (1.0 - w) * kv + w * Ff[bi]
        del dmin, fq, a_, kv
        Ffp = _push(Ff, f_["step"])
        del Ff
        Vf, Tf, eaf, ebf = _mt_cells(Ffp, f_["lo"], f_["step"], np.nonzero(_crossing_cells(Ffp)))
        del Ffp
        # fine vertices on a sub-edge of a coarse edge -> that coarse edge's key (its twin)
        fa = np.stack(np.unravel_index(eaf, fn), 1); fb = np.stack(np.unravel_index(ebf, fn), 1)
        dd = fb - fa
        r = fa % div
        rmax = np.where(dd == 1, r, -1).max(1)
        on = np.all(np.where(dd == 0, r == 0, r == rmax[:, None]), 1) & (dd.min(1) >= 0)
        Pa = i0[None, :] + (fa - rmax[:, None] * dd) // div
        Pb = Pa + dd
        gk = lambda q: (q[:, 0] * nc_[1] + q[:, 1]) * nc_[2] + q[:, 2]
        twin = np.full(len(Vf), -1, np.int64)
        kk = gk(Pa) * NPc + gk(Pb)
        pos_ = np.clip(np.searchsorted(ckey, kk), 0, len(ckey) - 1)
        hit = on & (ckey[pos_] == kk)
        twin[hit] = pos_[hit]
        fbd = _directed_boundary(Tf)
        nxt = np.full(len(Vf), -1, np.int64)
        outdeg = np.bincount(fbd[:, 0], minlength=len(Vf))
        nxt[fbd[:, 0]] = fbd[:, 1]
        bverts = np.unique(fbd)
        anchors = bverts[twin[bverts] >= 0]
        # coarse vertex -> its fine twin (this box)
        c2f = {int(twin[a]): int(a) for a in anchors}
        keep = np.ones(len(Vf), bool); keep[anchors] = False
        newid = np.full(len(Vf), -1, np.int64)
        newid[keep] = nv + np.arange(int(keep.sum()))
        newid[anchors] = twin[anchors]
        ok_e, fail_e, walked = 0, 0, 0
        for a, b in cb:
            a, b = int(a), int(b)
            if a not in c2f or b not in c2f or (a, b) in expand:
                continue
            fa_, fb_ = c2f[a], c2f[b]
            cur, path, good = fb_, [], False
            for _ in range(100000):
                if outdeg[cur] != 1:
                    break
                cur = int(nxt[cur])
                if cur < 0:
                    break
                if twin[cur] >= 0:
                    good = cur == fa_
                    break
                path.append(cur)
            if good:
                expand[(a, b)] = [int(newid[m]) for m in path[::-1]]
                ok_e += 1; walked += len(path)
            else:
                fail_e += 1
        VS.append(Vf[keep]); TS.append(newid[Tf])
        nv += int(keep.sum())
        rows.append({"name": f_["name"], "div": div, "fine_tris": int(len(Tf)), "fine_border_verts": int(len(bverts)),
                     "welded_twins": int(len(anchors)), "border_edges_stitched": ok_e, "border_edges_failed": fail_e,
                     "t_junction_verts": walked, "blend_voxels": blend,
                     "border_verts_unaccounted": int(len(bverts) - len(anchors) - walked)})
    # coarse border triangles fanned through the stitched T-junction vertices
    Tc2 = []
    m_ = np.zeros(len(Tc), bool)
    for ti, (a, b, c) in enumerate(Tc.tolist()):
        es = [(a, b, c), (b, c, a), (c, a, b)]
        hitn = [e for e in es if (e[0], e[1]) in expand and expand[(e[0], e[1])]]
        if not hitn:
            continue
        m_[ti] = True
        x, y, z = hitn[0]                        # apex = the corner opposite the first stitched edge
        poly = [x] + expand[(x, y)] + [y] + expand.get((y, z), []) + [z] + expand.get((z, x), [])
        k_ = poly.index(z)
        poly = poly[k_:] + poly[:k_]             # apex first
        for i in range(1, len(poly) - 1):
            Tc2.append((poly[0], poly[i], poly[i + 1]))
    T = np.vstack([Tc[~m_], np.array(Tc2, np.int64).reshape(-1, 3)] + TS)
    V = np.vstack(VS)
    oe, nm = nonmanifold_edges(T)
    rep = {"boxes": rows, "coarse_tris": int(len(Tc)), "coarse_border_edges": int(len(cb)),
           "coarse_tris_fanned": int(m_.sum()), "tris": int(len(T)), "open_edges": oe, "nonmanifold_edges": nm,
           "seconds": round(time.time() - t0_, 1),
           "rule": "coarse cells inside the boxes not contoured; fine grid blended to the coarse Kuhn-linear field at "
                   "the box faces; coarse/fine twins welded, coarse border triangles fanned through the T-junctions"}
    return V, T, rep


def fine_rebalance(F, lo, step, Vp, Fp, LV, LF, target, boxes, lc, calibrate=True):
    """light.fine: the denser high must not change the boxes' share of the FINAL low. Reference = the same light
    pipeline on the COARSE grid alone (marching tets, specks, the uniform pre_tris collapse, the one-sided dense
    calibration when light.dense is set: share = max(want, the uncalibrated share)), i.e. exactly the share the build
    has without light.fine. The fine strands / teeth hold more quadric error, so the calibrated collapse over-feeds the
    boxes and starves the body (vampwarrior: 0.73 vs 0.41). When the share is off by more than `fine_share_tol` the low
    is redone from the pre_tris mesh in two LOCKED passes (vertex group weight 0 = locked, factor 1): the rest locked
    while the boxes collapse to share x body_tris, then the boxes locked while the rest collapses to body_tris.
    -> LV, LF, report"""
    t0_ = time.time()
    Vc, Tc = SD.polygonise(types.SimpleNamespace(F=F, lo=lo, step=step))
    Vc, Tc, _ = PFB.keep_islands(Vc, Tc, float(lc["speck_frac"]))
    Vpc, Fpc = decimate(Vc, Tc, float(lc["pre_tris"]) / len(Tc))
    del Vc, Tc
    A = tri_area(Vpc, Fpc)
    a = float(A[in_boxes(Vpc[Fpc].mean(1), boxes)].sum() / A.sum())
    dens = float(np.mean([b_.get("density", 2.0) for b_ in boxes]))
    want = a * dens / (a * dens + (1.0 - a))
    vgc = np.where(in_boxes(Vpc, boxes), 0.02, 1.0)
    for b_ in boxes:
        if "vg_weight" in b_:
            vgc = np.where(in_boxes(Vpc, [b_]), float(b_["vg_weight"]), vgc)

    def sh(V_, F_, vg, f):
        V2, F2 = decimate(V_, F_, target / len(F_), vg, f)
        return float(in_boxes(V2[F2].mean(1), boxes).mean())
    if calibrate:                        # the build's own one-sided calibration, on the coarse pipeline
        s_ref = sh(Vpc, Fpc, vgc, 1e-7)
        if s_ref < want:
            f_lo, f_hi, best = -7.0, 1.0, None
            for _ in range(int(lc.get("calib_iters", 9))):
                mid = 0.5 * (f_lo + f_hi)
                s_ = sh(Vpc, Fpc, vgc, 10 ** mid)
                if s_ < want:
                    f_lo = mid
                else:
                    f_hi = mid; best = s_
            s_ref = best if best is not None else sh(Vpc, Fpc, vgc, 10 ** f_hi)
    else:
        V2, F2 = decimate(Vpc, Fpc, target / len(Fpc))
        s_ref = float(in_boxes(V2[F2].mean(1), boxes).mean())
    del Vpc, Fpc
    s_cal = float(in_boxes(LV[LF].mean(1), boxes).mean())
    rep = {"reference_share": round(s_ref, 4), "want_share": round(want, 4), "calibrated_share": round(s_cal, 4)}
    if abs(s_cal - s_ref) > float(lc.get("fine_share_tol", 0.01)):
        inb = in_boxes(Vp[Fp].mean(1), boxes)
        n_in = int(round(s_ref * target))
        r1 = (len(Fp) - int(inb.sum()) + n_in) / len(Fp)
        V1, F1 = decimate(Vp, Fp, r1, np.where(in_boxes(Vp, boxes), 1.0, 0.0), 1.0) if r1 < 1.0 else (Vp, Fp)
        LV, LF = decimate(V1, F1, target / len(F1), np.where(in_boxes(V1, boxes), 0.0, 1.0), 1.0)
        rep["two_pass"] = {"box_tris_wanted": n_in, "pass1_tris": int(len(F1)),
                           "pass1_box_tris": int(in_boxes(V1[F1].mean(1), boxes).sum())}
    rep["got_share"] = round(float(in_boxes(LV[LF].mean(1), boxes).mean()), 4)
    rep["seconds"] = round(time.time() - t0_, 1)
    rep["rule"] = ("the dense boxes' share of the final low held to the coarse-only pipeline's share, so light.fine "
                   "changes WHERE the box triangles go, not how many")
    return LV, LF, rep


def pre_decimate_fine(Vh, Th, pre_tris, FINE, lc):
    """light.fine: the first (pre_tris) collapse keeps the fine boxes' share of the HIGH's triangles (their div^2
    tessellation), so the final calibrated collapse (light.dense) starts from the fine detail; the dense vertex-group
    factor is bisected (log10 in [-7, 1]) only when the uniform collapse falls below that share"""
    boxes = [{"box": [f_["lo"].tolist(), (f_["lo"] + f_["step"] * (np.array(f_["F"].shape) - 1)).tolist()]} for f_ in FINE]
    want = float(in_boxes(Vh[Th].mean(1), boxes).mean())
    vg = np.where(in_boxes(Vh, boxes), 0.02, 1.0)

    def share(f):
        V2, F2 = decimate(Vh, Th, float(pre_tris) / len(Th), vg, f)
        return float(in_boxes(V2[F2].mean(1), boxes).mean()), V2, F2
    f_lo, f_hi = -7.0, 1.0
    n_it = int(lc.get("fine_pre_iters", 6))
    kf = lc.get("fine_pre_factor")      # opt-in (absent = the bisection below, unchanged): the factor a previous build's
    if kf is not None:                  # bisection landed on (report low.fine.pre.factor_log10), tried first
        res = (f_hi - f_lo) / 2 ** n_it
        k_ = int(round((float(kf) - f_lo) / res))
        kn = {"log10": float(kf), "accepted": False}
        if abs(f_lo + k_ * res - float(kf)) < 1e-9 and 0 < k_ < 2 ** n_it:   # a grid point the bisection can land on
            fk = f_lo + k_ * res
            s_k, Vk, Fk = share(10 ** fk)
            s_b = share(10 ** (fk - res))[0] if s_k >= want else None
            kn.update(trials=[(round(fk - res, 3), None if s_b is None else round(s_b, 4)), (round(fk, 3), round(s_k, 4))])
            if s_b is not None and s_b < want:   # the bisection's final bracket [fk - res, fk]: the same pick, 2 trials
                kn["accepted"] = True
                return Vk, Fk, {"want_share": round(want, 4), "got_share": round(s_k, 4), "trials": kn["trials"],
                                "factor_log10": round(fk, 3), "known_factor": kn,
                                "rule": "light.fine_pre_factor: the known factor kept since it brackets the share "
                                        "(share(f - step) < want <= share(f)), the cell the bisection ends in "
                                        "(the share rises with the factor, the bisection's own premise)"}
    s0, Vp, Fp = share(10 ** f_lo)
    rows = [(f_lo, round(s0, 4))]
    if s0 < want:
        for _ in range(n_it):
            mid = 0.5 * (f_lo + f_hi)
            s_, V2, F2 = share(10 ** mid)
            rows.append((round(mid, 3), round(s_, 4)))
            if s_ < want:
                f_lo = mid
            else:
                f_hi = mid; Vp, Fp, s0 = V2, F2, s_
        if s0 < want:
            s0, Vp, Fp = share(10 ** f_hi)
            rows.append((f_hi, round(s0, 4)))
    rep = {"want_share": round(want, 4), "got_share": round(s0, 4), "trials": rows,
           "rule": "pre_tris collapse with the fine boxes weighted 0.02 (vs 1); factor bisected until the boxes "
                   "keep the high's triangle share"}
    if kf is not None:                  # the known factor was off (or not a bisection grid point): bisected as before
        rep["known_factor"] = kn
        rep["factor_log10"] = rows[0][0] if len(rows) == 1 else round(f_hi, 3)
    return Vp, Fp, rep


# opt-in fast iteration (build flags; absent = no change, nothing read or written):
#   --geo-cache            the HIGH + LOW step (remesh, the pre / dense / fine decimations, smooth_region) saved and reused
#                          on an exact input match (builds are deterministic): ~4-5 min of a light.fine unit's build
#   --retexture            (implies --geo-cache) the raw bakes saved before the 2D texture steps and reused on an exact
#                          match: a 2D step tweak (despeckle / recolour / dilate / paint_project / albedo_blend / ao_fill /
#                          smooth) re-runs the 2D steps only; normal + AO and the albedo are keyed apart, so a stage-1
#                          texture tweak (recolour3d, erase3d, face_paint) re-bakes the albedo alone
#   --preview-bake <blend> a --preview that bakes, at reduced settings (bake_res [2048, 1024], albedo_res 2048,
#                          ao_samples 16, never above the config; the UVs as the full build), textures packed into the
#                          preview blend (+ <blend>_textures/), no rig / clips
# The keys hash the exact inputs (arrays in full, the config keys the step reads, the kit sources, the Blender version);
# entries live in %TEMP%/forge_light_cache, the newest CACHE_KEEP per unit + kind.
CACHE_DIR = os.path.join(tempfile.gettempdir(), "forge_light_cache")
CACHE_KEEP = 2
_Q8 = (lambda q: q.astype(np.float32) * np.float32(1.0 / 255.0), lambda q: q.astype(np.float32) / np.float32(255.0))


def _cache_key(*parts):
    import hashlib
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, np.ndarray):
            a = np.ascontiguousarray(p)
            h.update(("%s%s" % (a.dtype.str, a.shape)).encode()); h.update(a.data)
        else:
            h.update(json.dumps(p, sort_keys=True, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)).encode())
        h.update(b"|")
    return h.hexdigest()[:24]


def _kit_sources():
    """the kit code a cached step runs (any edit = a new key) + the Blender version"""
    import hashlib
    h = hashlib.sha256(bpy.app.version_string.encode())
    for m_ in (__file__, PFB.__file__, SD.__file__):
        h.update(open(m_, "rb").read())
    return h.hexdigest()[:24]


def _cache_load(unit, kind, key):
    p_ = os.path.join(CACHE_DIR, "%s__%s__%s.npz" % (unit, kind, key))
    if not os.path.exists(p_):
        return None
    try:
        with np.load(p_, allow_pickle=False) as Z_:
            return {k: Z_[k] for k in Z_.files}
    except Exception as e_:
        print("CACHE_UNREADABLE", p_, e_)
        return None


def _cache_save(unit, kind, key, **arrs):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p_ = os.path.join(CACHE_DIR, "%s__%s__%s.npz" % (unit, kind, key))
    tmp = p_[:-4] + ".%d.tmp" % os.getpid()
    with open(tmp, "wb") as fh:
        np.savez(fh, **arrs)
    try:
        os.replace(tmp, p_)             # atomic: the main build and its digest twin may write the same key
    except OSError:                     # (Windows: the twin is reading it right now; its copy is the same entry)
        os.remove(tmp)
    pre = "%s__%s__" % (unit, kind)
    old = sorted((f_ for f_ in os.listdir(CACHE_DIR) if f_.startswith(pre) and f_.endswith(".npz")),
                 key=lambda f_: os.path.getmtime(os.path.join(CACHE_DIR, f_)), reverse=True)
    for f_ in old[CACHE_KEEP:]:
        try:
            os.remove(os.path.join(CACHE_DIR, f_))
        except OSError:
            pass
    return p_


def _q8_pack(a):
    """an 8-bit image's float pixels -> (uint8, mode) when they convert back bit-exactly, else (the floats, -1)"""
    q = np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)
    for m_, f_ in enumerate(_Q8):
        if np.array_equal(f_(q), a):
            return q, m_
    return a, -1


def _q8_unpack(a, m_):
    return _Q8[int(m_)](a) if int(m_) >= 0 else a


def run_light(unit, cfg, raw_cfg, PREVIEW, DIGEST_ONLY, SOLID, T0, FAST=None):
    scene = bpy.context.scene
    lc = cfg["light"]
    FAST = FAST or {}
    OUT_IMPROVED = os.path.join(ROOT, "improved", unit + ".blend")
    OUT_RIGGED = os.path.join(ROOT, "rigged", unit + ".blend")
    OUT_GLB = os.path.join(ROOT, "rigged", unit + ".glb")
    TEX_DIR = os.path.join(ROOT, "improved", "textures")
    TRI_BUDGET = list(cfg["tri_budget"])
    CELL_MAX_H, CELL_MAX_FP = cfg["cell_max"]
    report = {"unit": unit, "conquest_character_id": cfg["char_id"] or unit, "tier": cfg["tier"], "tri_budget": TRI_BUDGET,
              "pattern": "proxy finish LIGHT (proxy_finish.py stage_light + proxy_finish_light.py): the frozen Pixal3D "
                         "sheet proxy kept as generated (geometry + texture), made watertight, decimated, baked, "
                         "optionally reposed with bones", "config": raw_cfg,
              "units": "sheet units: 1 unit = 100 px of the reference sheet; floor z = 0"}
    DIG = {}
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)

    # ================================================================================================ 1. stage 1
    t = time.time()
    tag = "twin" if DIGEST_ONLY else "main"
    npz = SOLID or os.path.join(tempfile.gettempdir(), "%s_solid_%s.npz" % (unit, tag))
    if not SOLID:
        vpy = next(p for p in PFB.VPY_CANDIDATES if os.path.exists(p))
        env = dict(os.environ); env["PYTHONDONTWRITEBYTECODE"] = "1"
        r = subprocess.run([vpy, "-P", os.path.join(HERE, "proxy_finish.py"), "solid", unit, "--out", npz],
                           capture_output=True, text=True, cwd=HERE, env=env, creationflags=PF.NO_WINDOW)
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit("STAGE1 FAILED")
        report["stage1_stdout"] = r.stdout.splitlines()[:1]
    Z = np.load(npz)
    meta = json.loads(bytes(Z["meta"]).decode())
    assert meta.get("mode") == "light", "stage 1 did not run in light mode"
    F = Z["F"].astype(np.float64); lo = Z["lo"].astype(np.float64); step = float(Z["step"][0])
    regs, RC = meta["regs"], meta["resolved"]
    report["stage1"] = meta["report"]
    DIG["solid_grid"] = sha(F[::2, ::2, ::2])
    PV, PFc, PUV, PTI = Z["prox_V"], Z["prox_F"], Z["prox_UV"], Z["prox_texi"]
    FINE = []                           # opt-in light.fine (stage 1 build_fine): finer grids stitched into the remesh
    for i_, fm in enumerate(meta.get("fine") or []):
        FINE.append({"name": fm["name"], "div": int(fm["div"]), "i0": np.asarray(fm["i0"], np.int64),
                     "i1": np.asarray(fm["i1"], np.int64), "lo": np.asarray(fm["lo"], float), "step": float(fm["step"]),
                     "spec": fm["spec"], "F": Z["fine_%d" % i_].astype(np.float64)})
    if FINE:
        assert not lc.get("move_region") and not lc.get("segment_array"), \
            "light.fine: move_region / segment_array are not carried into the fine grids"
    if PF.light_cuts(lc):               # opt-in light.cuts: the removed part leaves the BAKE source too (else its texture,
        Pc_ = PV[PFc].mean(1)           # AO shadow and the cage distance would still see the part that is gone)
        cut_in = np.zeros(len(PFc), bool)
        for c_ in PF.light_cuts(lc):
            if "capsule" in c_:
                d_ = PF.sd_capsule(Pc_, *c_["capsule"])
            elif "sphere" in c_:
                d_ = np.linalg.norm(Pc_ - np.asarray(c_["sphere"][0], float), axis=1) - float(c_["sphere"][1])
            else:
                d_ = PF.sd_box(Pc_, *c_["box"])
            cut_in |= d_ < 0
        PFc, PTI = PFc[~cut_in], PTI[~cut_in]
        report["proxy_faces_cut"] = {"faces_removed": int(cut_in.sum()), "faces_kept": int(len(PFc)),
                                     "rule": "proxy faces whose centroid lies inside a light.cuts volume leave the bake "
                                             "source (normal / AO / albedo) and the proxy-distance metrics"}
    if lc.get("move_region"):           # opt-in light.move_region (new units only): a proxy part (a goggle lens + ring +
        F, PV, mv_rep = move_region(F, lo, step, PV, lc["move_region"])   # strap end) moved before the remesh + bake
        report["move_region"] = mv_rep
        DIG["move_region_grid"] = sha(F[::2, ::2, ::2])
        print("MOVE_REGION", json.dumps({k: mv_rep[k] for k in ("grid_nodes_resampled", "inverse_residual_max_u",
                                                                 "proxy_verts_moved", "proxy_max_move_u", "landmarks")}))
    if lc.get("mirror_copy"):           # opt-in light.mirror_copy (new units only): a one-sided part (a single leg) copied
        for f_ in FINE:                 # light.fine: the same copy in every fine grid (grid only)
            f_["F"] = mirror_copy(f_["F"], f_["lo"], f_["step"], PV, PFc, PUV, PTI, lc["mirror_copy"])[0]
        F, PV, PFc, PUV, PTI, mc_rep = mirror_copy(F, lo, step, PV, PFc, PUV, PTI, lc["mirror_copy"])   # to the other side
        report["mirror_copy"] = mc_rep
        DIG["mirror_copy_grid"] = sha(F[::2, ::2, ::2])
        print("MIRROR_COPY", json.dumps({k: mc_rep[k] for k in ("faces_copied", "verts_copied", "grid_nodes_added_inside")}))
    bake_tex = None
    if lc.get("head_graft"):            # opt-in light.head_graft (new units only): a separately generated head, its good
        _tk = sorted(k for k in Z.files if k.startswith("tex_"))[0]     # half mirrored, unioned at the neck
        for f_ in FINE:                 # light.fine: the graft SDF in every fine grid (its step; never padded)
            f_["F"] = head_graft(f_["F"], f_["lo"], f_["step"], PV, PFc, PUV, PTI, int(_tk[4:]), Z[_tk], lc["head_graft"],
                                 float(lc["band_voxels"]) * f_["step"], float(lc["min_half_thickness"]) * f_["step"],
                                 pad=False)[0]
        lo_hg0 = lo.copy()
        F, lo, PV, PFc, PUV, PTI, bake_tex, hg_rep = head_graft(
            F, lo, step, PV, PFc, PUV, PTI, int(_tk[4:]), Z[_tk], lc["head_graft"],
            float(lc["band_voxels"]) * step, float(lc["min_half_thickness"]) * step)
        report["head_graft"] = hg_rep
        if FINE:                        # the coarse grid may have been padded: the boxes keep their place
            sh_ = np.rint((lo_hg0 - lo) / step).astype(np.int64)
            for f_ in FINE:
                f_["i0"] = f_["i0"] + sh_; f_["i1"] = f_["i1"] + sh_
        DIG["head_graft_grid"] = sha(F[::2, ::2, ::2])
        print("HEAD_GRAFT", json.dumps({k: hg_rep[k] for k in ("scale", "pitch_deg", "place_yz", "body_bbox", "occupancy",
                                                                "grid_nodes_added_inside", "faces_appended",
                                                                "bake_faces_dropped_buried", "landmarks_body", "seconds")}))
    if lc.get("cylinders"):             # opt-in light.cylinders (new units only): capped cylinders (flat ends) unioned
        F, cy_rep = add_cylinders(F, lo, step, lc["cylinders"])          # into the grid before the remesh
        for f_ in FINE:
            f_["F"] = add_cylinders(f_["F"], f_["lo"], f_["step"], lc["cylinders"])[0]
        report["cylinders"] = cy_rep
        DIG["cylinders_grid"] = sha(F[::2, ::2, ::2])
        print("CYLINDERS", json.dumps([{k: r_[k] for k in ("nodes_inside", "grid_nodes_added_inside", "clipped_by_grid")}
                                       for r_ in cy_rep]))
    if lc.get("segment_array"):         # opt-in light.segment_array (new units only): a generated segment (a tail's scaled
        F, PV, PFc, PUV, PTI, sa_rep = segment_array(F, lo, step, PV, PFc, PUV, PTI, lc["segment_array"])   # root)
        report["segment_array"] = sa_rep                                 # duplicated along a curve, tapering to a point
        DIG["segment_array_grid"] = sha(F[::2, ::2, ::2])
        print("SEGMENT_ARRAY", json.dumps({k: sa_rep[k] for k in ("count", "segment_length_u", "array_length_u",
                                                                   "tip_point", "cavity_nodes_filled", "faces_appended",
                                                                   "grid_nodes_added_inside", "bake_faces_dropped_buried")}))
    print("STAGE1", round(time.time() - t, 1), "s grid", F.shape, "step", step)

    # ================================================================================================ 2. HIGH + LOW
    t = time.time()
    if FINE:
        DIG["fine_grids"] = sha(np.concatenate([f_["F"][::2, ::2, ::2].ravel() for f_ in FINE]))
    GEO, geo_key = None, None
    if FAST.get("geo_cache"):           # opt-in --geo-cache: the same inputs give the same HIGH + LOW (deterministic)
        geo_key = _cache_key("geo", _kit_sources(), F, lo, step, PV, PFc,
                             [[f_["name"], f_["div"], f_["i0"], f_["i1"], f_["lo"], f_["step"], f_["spec"]] for f_ in FINE],
                             *[f_["F"] for f_ in FINE], int(cfg["body_tris"]), RC.get("dense"), PF.light_cuts(lc),
                             {k: lc.get(k) for k in ("speck_frac", "pre_tris", "fine_pre_iters", "fine_pre_factor",
                                                     "calib_iters", "fine_share_tol")},
                             {k: v for k, v in (lc.get("smooth_region") or {}).items() if k != "albedo_blend"})
        GEO = _cache_load(unit, "geo", geo_key)
    if GEO is not None:
        LV, LF, BAKE_CAGE = GEO["LV"], GEO["LF"], float(GEO["bake_cage"])
        report.update(json.loads(str(GEO["report"])))
        Hh = float(PV[:, 2].max() - PV[:, 2].min())
        report["geometry_cache"] = {"hit": True, "key": geo_key, "seconds": round(time.time() - t, 1),
                                    "rule": "--geo-cache: HIGH + LOW reused (the report's high / low seconds are the build "
                                            "that saved it)"}
        print("GEO_CACHE hit", geo_key, report["geometry_cache"]["seconds"], "s")
        print("HIGH", json.dumps(report["high"]))
        print("LOW", json.dumps(report["low"]))
    else:
        if FINE:                            # light.fine: coarse + fine remeshes, blended and stitched (one closed high)
            Vh, Th, fine_rep = polygonise_fine(F, lo, step, FINE)
            print("FINE_STITCH", json.dumps({k: fine_rep[k] for k in ("boxes", "tris", "open_edges", "nonmanifold_edges",
                                                                        "seconds")}))
        else:
            Vh, Th = SD.polygonise(types.SimpleNamespace(F=F, lo=lo, step=step))
        Vh, Th, crumbs = PFB.keep_islands(Vh, Th, float(lc["speck_frac"]))
        oe_h, nm_h = nonmanifold_edges(Th)
        report["high"] = {"tris": int(len(Th)), "verts": int(len(Vh)), "specks_dropped": crumbs, "open_edges": oe_h,
                          "nonmanifold_edges": nm_h, "rule": "marching tets of the whole remesh grid (no mirror)",
                          "seconds": round(time.time() - t, 1)}
        if FINE:
            report["high"]["fine"] = fine_rep
        print("HIGH", json.dumps(report["high"]))
        t = time.time()
        if FINE:                            # light.fine: the pre collapse keeps the fine boxes' share of the high
            Vp_, Fp_, fine_pre = pre_decimate_fine(Vh, Th, int(lc["pre_tris"]), FINE, lc)
        else:
            Vp_, Fp_ = decimate(Vh, Th, float(lc["pre_tris"]) / len(Th))
        target = int(cfg["body_tris"])
        dense = RC.get("dense") or []
        if FINE:                            # light.fine boxes with a "density" join the dense calibration (same rule)
            dense = list(dense) + [dict({"box": f_["spec"]["box"], "density": float(f_["spec"]["density"])},
                                        **({"vg_weight": f_["spec"]["vg_weight"]} if "vg_weight" in f_["spec"] else {}))
                                   for f_ in FINE if "density" in f_["spec"]]
        calib = None
        if dense:
            dens = float(np.mean([b.get("density", 2.0) for b in dense]))
            A = tri_area(Vp_, Fp_)
            inb = in_boxes(Vp_[Fp_].mean(1), dense)
            a = float(A[inb].sum() / A.sum())
            want = a * dens / (a * dens + (1.0 - a))
            vg = np.where(in_boxes(Vp_, dense), 0.02, 1.0)
            for b_ in dense:                 # opt-in per box "vg_weight" (new units only; list order, later boxes win): a
                if "vg_weight" in b_:        # small box (eye sockets) held denser than the rest; absent = 0.02 for all (unchanged)
                    vg = np.where(in_boxes(Vp_, [b_]), float(b_["vg_weight"]), vg)

            def share(f):
                V2, F2 = decimate(Vp_, Fp_, target / len(Fp_), vg, f)
                return float(in_boxes(V2[F2].mean(1), dense).mean()), V2, F2
            f_lo, f_hi = -7.0, 1.0               # log10 of the vertex-group factor
            s0, LV, LF = share(10 ** f_lo)
            rows = [(round(f_lo, 3), round(s0, 4))]
            if s0 < want:
                for _ in range(int(lc.get("calib_iters", 9))):
                    mid = 0.5 * (f_lo + f_hi)
                    s_, V2, F2 = share(10 ** mid)
                    rows.append((round(mid, 3), round(s_, 4)))
                    if s_ < want:
                        f_lo = mid
                    else:
                        f_hi = mid; LV, LF, s0 = V2, F2, s_
                if s0 < want:                    # never reached: the densest tried
                    s0, LV, LF = share(10 ** f_hi)
            calib = {"density": dens, "box_area_frac": round(a, 4), "target_tri_share": round(want, 4),
                     "got_tri_share": round(s0, 4), "factor_log10": round(f_hi if s0 >= want else f_lo, 3),
                     "trials": rows, "rule": "dense boxes weighted 0.02 (vs 1) in the collapse decimation; the factor bisected "
                                             "until the boxes hold density x the rest's triangles per area"}
        else:
            LV, LF = decimate(Vp_, Fp_, target / len(Fp_))
        if FINE:                            # light.fine: the boxes keep the share the coarse-only build gives them
            rb_boxes = dense or [{"box": [f_["lo"].tolist(), (f_["lo"] + f_["step"] * (np.array(f_["F"].shape) - 1)).tolist()]}
                                 for f_ in FINE]
            LV, LF, fine_rb = fine_rebalance(F, lo, step, Vp_, Fp_, LV, LF, target, rb_boxes, lc, bool(dense))
            if calib is not None:
                calib["got_tri_share"] = fine_rb["got_share"]
            print("FINE_REBALANCE", json.dumps({k: fine_rb[k] for k in ("reference_share", "want_share", "calibrated_share", "got_share", "seconds")}))
        LV, LF, specks = PFB.keep_islands(LV, LF, 0.001)
        if lc.get("smooth_region"):         # opt-in light.smooth_region (new units only): the low smoothed in boxes (a seam)
            LV, sm_rep = smooth_region(LV, LF, lc["smooth_region"])
            report["smooth_region"] = sm_rep
            print("SMOOTH_REGION", json.dumps({k: sm_rep[k] for k in ("verts_weighted", "max_move_u", "mean_move_u_weighted")}))
        oe_l, nm_l = nonmanifold_edges(LF)
        # deviation from the PROXY (the artist's pick), both ways
        bvh_p = BVHTree.FromPolygons(PV.tolist(), PFc.tolist())
        d_lp = np.array([bvh_p.find_nearest(Vector(p))[3] for p in LV])
        bvh_l = BVHTree.FromPolygons(LV.tolist(), LF.tolist())
        ss = np.arange(0, len(PV), max(1, len(PV) // 40000))
        if PF.light_cuts(lc):               # only the vertices of the KEPT proxy faces (a cut part is not a deviation)
            used_ = np.unique(PFc)
            ss = used_[np.arange(0, len(used_), max(1, len(used_) // 40000))]
        d_pl = np.array([bvh_l.find_nearest(Vector(PV[i]))[3] for i in ss])
        Hh = float(PV[:, 2].max() - PV[:, 2].min())
        report["low"] = {"method": "collapse decimation: uniform to %d, then %d with the dense boxes calibrated" % (
                         lc["pre_tris"], target), "pre_tris": int(len(Fp_)), "tris": int(len(LF)), "verts": int(len(LV)),
                         "specks": specks, "open_edges": oe_l, "nonmanifold_edges": nm_l, "dense": calib,
                         "low_to_proxy": {"mean": round(float(d_lp.mean()), 5), "p99": round(float(np.percentile(d_lp, 99)), 5),
                                          "max": round(float(d_lp.max()), 5)},
                         "proxy_to_low_sampled": {"mean": round(float(d_pl.mean()), 5),
                                                  "p99": round(float(np.percentile(d_pl, 99)), 5), "max": round(float(d_pl.max()), 5)},
                         "p99_over_height": round(float(np.percentile(d_pl, 99)) / Hh, 5),
                         "seconds": round(time.time() - t, 1)}
        if FINE:
            fbx = [{"box": [f_["lo"].tolist(), (f_["lo"] + f_["step"] * (np.array(f_["F"].shape) - 1)).tolist()]} for f_ in FINE]
            report["low"]["fine"] = {"pre": fine_pre, "rebalance": fine_rb,
                                     "high_share": round(float(in_boxes(Vh[Th].mean(1), fbx).mean()), 4),
                                     "low_share": round(float(in_boxes(LV[LF].mean(1), fbx).mean()), 4),
                                     "dense_boxes_added": [f_["name"] for f_ in FINE if "density" in f_["spec"]]}
        print("LOW", json.dumps(report["low"]))
        BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(d_pl, 99)), 4))
        if geo_key:
            _cache_save(unit, "geo", geo_key, LV=LV, LF=LF, bake_cage=np.float64(BAKE_CAGE),
                        report=np.array(json.dumps({k: v for k, v in report.items() if k in ("high", "low", "smooth_region")},
                                                   default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))))
            report["geometry_cache"] = {"hit": False, "key": geo_key, "saved": True}
            print("GEO_CACHE saved", geo_key)
    NB, NBF = len(LV), len(LF)          # the body (shell) part of the low; opt-in light.parts are appended after it
    PARTS_L, PREG = [], []
    if lc.get("parts"):                 # opt-in light.parts (new units only): closed procedural parts (a weapon) from the
        import importlib                # unit's own parts module, separate islands rigid on a config bone
        pv_, pf_ = [LV], [LF]
        o_ = NB
        for sp_ in lc["parts"]:
            fn_ = getattr(importlib.import_module(sp_["module"]), sp_["func"])
            Vq, Fq, Rq, info_ = fn_(sp_.get("args") or {})
            part = PFP.Part(sp_["name"], Vq, Fq, Rq, bone=sp_.get("bone"))
            part.range = (o_, o_ + len(part.V)); o_ += len(part.V)
            pv_.append(part.V); pf_.append(np.asarray(part.F, np.int64) + part.range[0])
            PREG += part.R
            PARTS_L.append(part)
            report.setdefault("parts", {})[part.name] = dict(info_, tris=part.tris(), open_edges=PFP.open_edges(part.F),
                                                             bone=part.bone, why=sp_.get("why"))
            print("PART", part.name, json.dumps(report["parts"][part.name]))
        LV = np.vstack(pv_); LF = np.vstack(pf_)
        DIG["parts"] = sha(LV[NB:])
    Vh = Th = Vp_ = Fp_ = None           # free the high (3 M tris)

    # ================================================================================================ 3. low object
    REGION = lc["region"]
    pal_default = PAL.load(unit, "default")
    assert REGION in pal_default["regions"], "palette %s lacks the light region %r (white)" % (unit, REGION)
    mat = bpy.data.materials.new(unit + "_body"); mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300); vg_.name = "glow"
    nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True
    low = obj_from_arrays(unit, LV, LF)
    me = low.data
    me.materials.append(mat)
    if PARTS_L:
        REG_L = [REGION] + [r_ for r_ in dict.fromkeys(PREG) if r_ != REGION]
        rid_l = np.concatenate([np.zeros(NBF, np.int32), np.array([REG_L.index(r_) for r_ in PREG], np.int32)])
        missing_ = [r_ for r_ in REG_L if r_ not in pal_default["regions"]]
        assert not missing_, "palette %s lacks the part regions %s" % (unit, missing_)
        PAL.store_regions(me, REG_L, rid_l, np.ones(len(LF)))
    else:
        PAL.store_regions(me, [REGION], np.zeros(len(LF), np.int32), np.ones(len(LF)))
    report["regions_faces"] = PAL.paint(me, pal_default)
    SMOOTH_IDS = np.zeros(0, np.int64); SMOOTH_NAMES = []
    smc = lc.get("smooth")
    if smc:                             # opt-in light.smooth: boxed shell faces -> named (white) smooth palette regions
        names_s, rid_s, sh_s = PAL.read_regions(me)
        names_s = list(names_s)
        Cs_ = LV[LF].mean(1)
        for r_ in smc["regions"]:
            assert r_["name"] in pal_default["regions"], "palette %s lacks the smooth region %r" % (unit, r_["name"])
            if r_["name"] not in names_s:
                names_s.append(r_["name"])
            m_ = in_boxes(Cs_, [{"box": b_} for b_ in r_["boxes"]]) & (rid_s == names_s.index(REGION))
            m_[NBF:] = False
            rid_s[m_] = names_s.index(r_["name"])
            SMOOTH_NAMES.append(r_["name"])
        PAL.store_regions(me, names_s, rid_s, sh_s)
        report["regions_faces"] = PAL.paint(me, pal_default)
        SMOOTH_IDS = np.nonzero(np.isin(rid_s, [names_s.index(n_) for n_ in SMOOTH_NAMES]))[0]
        report["smooth"] = {"regions": SMOOTH_NAMES, "faces": int(len(SMOOTH_IDS)),
                            "rule": "boxed shell faces in white palette regions (Col x the baked texture as before), "
                                    "use_smooth after the flat bake, conquest_smooth_regions (opt-in light.smooth)"}
    PAL.apply_material(mat, pal_default)
    me.update()
    me.shade_flat()
    bpy.context.view_layer.objects.active = low
    for o in scene.objects:
        o.select_set(o is low)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    UVM = float(lc.get("uv_island_margin", 0.003))   # opt-in (new units only): a many-island feather low wastes the
    #                                                  atlas on margins at 0.003 (6215 islands: 4.7 % texel use)
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=UVM, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    uvfix = lc.get("uv_fix_overlap")    # opt-in (new units only; absent = the gemwing / holly UVs, unchanged)
    if uvfix:
        # a ridged low (drill rings) FOLDS inside a 66 deg island: the folded faces share texels and the bake of one
        # overwrites the other (sand printed on a drill band). The overlapping faces alone are re-unwrapped at a
        # tighter angle (their own small islands); every other island keeps its 66 deg layout; then one pack
        import bmesh
        ts = scene.tool_settings
        sync0 = ts.use_uv_select_sync
        ts.use_uv_select_sync = True
        bm = bmesh.from_edit_mesh(low.data)
        passes = []
        for _pass in range(int(uvfix.get("passes", 3))):
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.uv.select_overlap(extend=False)
            bm.faces.ensure_lookup_table()
            nsel = sum(1 for f_ in bm.faces if f_.select)
            passes.append(nsel)
            if not nsel:
                break
            bpy.ops.uv.smart_project(angle_limit=math.radians(float(uvfix.get("angle_deg", 30.0))), island_margin=UVM,
                                     area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.pack_islands(rotate=True, margin=UVM)
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.uv.select_overlap(extend=False)
        bm.faces.ensure_lookup_table()
        left = sum(1 for f_ in bm.faces if f_.select)
        bpy.ops.mesh.select_all(action="SELECT")
        ts.use_uv_select_sync = sync0
        report["uv_fix_overlap"] = {"overlapping_faces_per_pass": passes, "left": left,
                                    "angle_deg": float(uvfix.get("angle_deg", 30.0)),
                                    "rule": "faces in overlapping UV (folds inside a 66 deg island) re-unwrapped alone at "
                                            "angle_deg, the rest kept; opt-in light.uv_fix_overlap"}
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=UVM)
    if lc.get("uv"):                    # opt-in light.uv: named single islands + a wide smart project + concave pack
        custom_uv(low, me, LV, LF, lc["uv"], int(lc["albedo_res"]), report, NBF)
    bpy.ops.object.mode_set(mode="OBJECT")
    mir_before = mirror_to_surface(LV, LF, 0.0)

    # ================================================================================================ 4. bake (FLAT low)
    imgs = {}
    if not PREVIEW or FAST.get("preview_bake"):
        try:
            import addon_utils
            addon_utils.enable("cycles", default_set=False, persistent=False)
        except Exception:
            pass
        tb = time.time()
        HIGH = obj_from_arrays(unit + "_proxy_high", PV, PFc)
        hm = HIGH.data
        uvl = hm.uv_layers.new(name="UVMap")
        uv_c = PUV[PFc.ravel()]
        uvl.data.foreach_set("uv", np.stack([uv_c[:, 0], 1.0 - uv_c[:, 1]], 1).astype(np.float32).ravel())
        hmat = bpy.data.materials.new("proxy_tex"); hmat.use_nodes = True
        hnt = hmat.node_tree
        for n_ in list(hnt.nodes):
            if n_.bl_idname != "ShaderNodeOutputMaterial":
                hnt.nodes.remove(n_)
        em = hnt.nodes.new("ShaderNodeEmission"); em.inputs["Strength"].default_value = 1.0
        hnt.links.new(em.outputs["Emission"], hnt.nodes["Material Output"].inputs["Surface"])
        texkeys = sorted(k for k in Z.files if k.startswith("tex_"))
        if texkeys:
            arr = Z[texkeys[0]] if bake_tex is None else bake_tex    # light.head_graft: proxy + head texture atlas
            h_, w_ = arr.shape[:2]
            src = bpy.data.images.new("proxy_texture", w_, h_, alpha=False)
            src.colorspace_settings.name = "sRGB"
            px_ = np.concatenate([arr[::-1].astype(np.float32) / 255.0, np.ones((h_, w_, 1), np.float32)], 2)
            src.pixels.foreach_set(px_.ravel())
            ti = hnt.nodes.new("ShaderNodeTexImage"); ti.image = src; ti.interpolation = "Linear"
            hnt.links.new(ti.outputs["Color"], em.inputs["Color"])
            report["albedo_source"] = {"texture": texkeys[0], "size": [w_, h_],
                                       "faces_textured": int((PTI == int(texkeys[0][4:])).sum()), "faces": int(len(PTI))}
        hm.materials.append(hmat)
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"; scene.cycles.use_denoising = False; scene.cycles.seed = 0
        RN, RA = cfg["bake_res"]; RC_ = int(lc["albedo_res"])
        AO_S = int(lc.get("ao_samples", 16))
        if FAST.get("preview_bake"):    # --preview-bake: reduced settings, never above the config (the UVs are unchanged)
            RN, RA, RC_, AO_S = min(RN, 2048), min(RA, 1024), min(RC_, 2048), min(AO_S, 16)
        img_n = bpy.data.images.new(unit + "_normal", RN, RN, alpha=False)
        img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
        img_ao = bpy.data.images.new(unit + "_ao", RA, RA, alpha=False)
        img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
        img_c = bpy.data.images.new(unit + "_albedo", RC_, RC_, alpha=False)
        img_c.colorspace_settings.name = "sRGB"; img_c.generated_color = (1.0, 0.0, 1.0, 1.0)
        tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
        ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
        tc = nt.nodes.new("ShaderNodeTexImage"); tc.image = img_c; tc.location = (-900, 300)
        low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
        low.visible_transmission = low.visible_volume_scatter = False
        me.shade_flat()                    # FLAT reference (BUILD_CONTRACT step 5)
        for o in scene.objects:
            o.select_set(o is HIGH or o is low)
        bpy.context.view_layer.objects.active = low
        bstats = {"reference": "flat", "source": "the proxy mesh itself (not the remesh)"}
        ao_low = bool(lc.get("ao_from_low"))   # opt-in (new units only): AO from the clean watertight LOW itself
        RAW, raw_keys = {}, None
        if FAST.get("retexture"):       # opt-in --retexture: the raw bakes (before every 2D step) of these exact inputs
            uv_k = np.empty(len(me.loops) * 2, np.float32); me.uv_layers.active.data.foreach_get("uv", uv_k)
            k_na = _cache_key("bake_nao", _kit_sources(), LV, LF, uv_k, PV, PFc, BAKE_CAGE, RN, RA, AO_S, ao_low,
                              lc.get("ao_distance"))
            k_c = _cache_key("bake_albedo", k_na, PUV, RC_, texkeys[:1],
                             (Z[texkeys[0]] if bake_tex is None else bake_tex) if texkeys else None)
            raw_keys = {"nao": ("bake_nao_%d_%d" % (RN, RA), k_na), "albedo": ("bake_albedo_%d" % RC_, k_c)}
            for part_, (kind_, key_) in raw_keys.items():
                Zr = _cache_load(unit, kind_, key_)
                if Zr is not None:
                    st_ = json.loads(str(Zr["bstats"]))
                    for typ_, nm_ in (("NORMAL", "normal"), ("AO", "ao"), ("EMIT", "albedo")):
                        if nm_ in Zr:
                            RAW[typ_] = _q8_unpack(Zr[nm_], Zr[nm_ + "_q8"])
                            bstats[typ_] = st_[typ_]
                    if "ao_distance" in st_:
                        bstats["ao_distance"] = st_["ao_distance"]
            print("RAW_BAKE_CACHE", json.dumps({"hit": sorted(RAW), "keys": {k: v[1] for k, v in raw_keys.items()}}))
        for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, AO_S), ("EMIT", tc, 1)):
            if typ in RAW:
                continue
            nt.nodes.active = node
            scene.cycles.samples = samples
            t = time.time()
            ao_dist = lc.get("ao_distance")   # opt-in (new units only): the AO ray length in sheet units (absent = the
            if typ == "AO" and ao_dist and scene.world is not None:   # world default, unchanged for older units)
                ao_d0 = scene.world.light_settings.distance
                scene.world.light_settings.distance = float(ao_dist)
            if typ == "AO" and ao_low:
                # the proxy soup's inner shells / inward faces occlude every AO ray at some bake origins (black texels,
                # ~15 % of a crystal-mane unit's AO map); the low is watertight and outward, so its own AO is clean
                vis0 = (low.visible_camera, low.visible_diffuse, low.visible_glossy, low.visible_shadow,
                        low.visible_transmission, low.visible_volume_scatter)
                low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
                low.visible_transmission = low.visible_volume_scatter = True
                HIGH.hide_render = True
                HIGH.select_set(False)
                r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=False, margin=16, use_clear=False)
                HIGH.hide_render = False
                HIGH.select_set(True)
                (low.visible_camera, low.visible_diffuse, low.visible_glossy, low.visible_shadow,
                 low.visible_transmission, low.visible_volume_scatter) = vis0
            else:
                r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE,
                                         max_ray_distance=BAKE_CAGE * 2.0, margin=16, use_clear=False)
            if typ == "AO" and ao_dist and scene.world is not None:
                scene.world.light_settings.distance = ao_d0
                bstats["ao_distance"] = {"u": float(ao_dist), "world_default": round(float(ao_d0), 4)}
            bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
        if ao_low:
            bstats["ao_source"] = "the low itself (opt-in light.ao_from_low), %d samples" % AO_S
        if "NORMAL" in RAW:
            px = RAW["NORMAL"].copy()
        else:
            px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
        if "AO" in RAW:
            pa = RAW["AO"].copy()
        else:
            pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
        if "EMIT" in RAW:
            pc = RAW["EMIT"].copy()
        else:
            pc = np.empty(RC_ * RC_ * 4, dtype=np.float32); img_c.pixels.foreach_get(pc); pc = pc.reshape(-1, 4)
        if raw_keys:                    # --retexture: the raw bakes saved (only the passes baked this time)
            for part_, typs_ in (("nao", (("NORMAL", "normal", px), ("AO", "ao", pa))), ("albedo", (("EMIT", "albedo", pc),))):
                if all(t_ in RAW for t_, _, _ in typs_):
                    continue
                arrs_ = {}
                for t_, nm_, a_ in typs_:
                    arrs_[nm_], arrs_[nm_ + "_q8"] = _q8_pack(a_)
                st_ = {t_: bstats[t_] for t_, _, _ in typs_}
                if part_ == "nao" and "ao_distance" in bstats:
                    st_["ao_distance"] = bstats["ao_distance"]
                _cache_save(unit, raw_keys[part_][0], raw_keys[part_][1], bstats=np.array(json.dumps(st_)), **arrs_)
            bstats["raw_bake_cache"] = {"reused": sorted(RAW), "keys": {k: v[1] for k, v in raw_keys.items()},
                                        "rule": "--retexture: Cycles skipped for the reused passes; every 2D step re-run"}
        cov_n = px[:, 2] > 0.25
        cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
        cov_c = ~((np.abs(pc[:, 0] - 1.0) < 1e-3) & (pc[:, 1] < 1e-3) & (np.abs(pc[:, 2] - 1.0) < 1e-3))
        devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
        px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
        if lc.get("normal_flatten"):        # opt-in (new units only; absent = no change): tilted normals on a re-surfaced
            report["normal_flatten"] = normal_flatten(px, RN, me, lc["normal_flatten"])   # patch (hidden proxy faces) set flat
            img_n.pixels.foreach_set(px.ravel())
        pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
        ao_fill = lc.get("ao_fill_below")   # opt-in (new units only; absent = the gemwing / holly bakes, unchanged)
        if ao_fill:
            # AO texels at ~0 are bake origins INSIDE the proxy soup (interior shells / fused arms: every AO ray is
            # occluded), not real crevices; they print as black blocks through basecolor = albedo x AO. Refill them
            # from their covered, valid 3x3 neighbours, ring by ring (numpy only, deterministic)
            A2 = pa[:, 0].reshape(RA, RA).astype(np.float64).copy()
            cov2 = cov_a.reshape(RA, RA)
            bad = cov2 & (A2 < float(ao_fill))
            nbad0 = int(bad.sum())
            good = cov2 & ~bad
            val = np.where(good, A2, 0.0); wgt = good.astype(np.float64)

            def box3(x):
                p_ = np.pad(x, 1, mode="edge")
                return sum(p_[1 + dy:1 + dy + RA, 1 + dx:1 + dx + RA] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
            rings = 0
            while bad.any() and rings < int(lc.get("ao_fill_rings", 64)):
                s_, n_ = box3(val), box3(wgt)
                newly = bad & (n_ > 0)
                if not newly.any():
                    break
                A2[newly] = s_[newly] / n_[newly]
                val = np.where(newly, A2, val); wgt = np.where(newly, 1.0, wgt)
                bad = bad & ~newly
                rings += 1
            if bad.any():
                A2[bad] = float(A2[good].mean()) if good.any() else 1.0
            pa[:, 0] = pa[:, 1] = pa[:, 2] = A2.ravel().astype(np.float32)
            img_ao.pixels.foreach_set(pa.ravel())
            report["ao_fill"] = {"below": float(ao_fill), "texels_filled": nbad0, "of_covered": int(cov2.sum()),
                                 "rings": rings, "left_to_mean": int(bad.sum()),
                                 "rule": "AO < below = a bake origin inside the proxy soup: refilled from the covered "
                                         "valid 3x3 neighbours ring by ring (opt-in light.ao_fill_below)"}
        if len(SMOOTH_IDS):                 # light.smooth: neutral normal map + softened AO on the smooth faces
            report["smooth"]["bake"] = smooth_texels(px, pa, RN, RA, me, SMOOTH_IDS, smc)
            img_n.pixels.foreach_set(px.ravel()); img_ao.pixels.foreach_set(pa.ravel())
        if (lc.get("uv") or {}).get("dilate_px"):   # opt-in: uncovered texels from their neighbours, not the global mean
            report["uv"]["dilate"] = dilate_fill(pc, RC_, cov_c, int(lc["uv"]["dilate_px"]))
            img_c.pixels.foreach_set(pc.ravel())
        mean_c = pc[cov_c, :3].mean(0) if cov_c.any() else np.full(3, 0.5)
        pc[~cov_c, :3] = mean_c; img_c.pixels.foreach_set(pc.ravel())
        if lc.get("paint_project"):         # opt-in: the sheet face panel projected onto the baked albedo
            report["paint_project"] = paint_project(pc, RC_, me, lc["paint_project"])
            img_c.pixels.foreach_set(pc.ravel())
            print("PAINT_PROJECT", json.dumps([{k: r_[k] for k in r_ if k != "why"} for r_ in report["paint_project"]]))
        if lc.get("recolour"):              # opt-in (new units only): targeted texture recolours on a GEOMETRY mask
            report["recolour"] = recolour_albedo(pc, RC_, me, lc["recolour"])
            img_c.pixels.foreach_set(pc.ravel())
        if lc.get("despeckle"):             # opt-in (new units only): dark bake hits on the soup's inner faces, refilled
            dsp = lc["despeckle"]
            report["despeckle"] = {"albedo": despeckle(pc, RC_, me, dsp, 3), "ao": despeckle(pa, RA, me, dsp, 1)}
            pa[:, 1] = pa[:, 0]; pa[:, 2] = pa[:, 0]
            img_c.pixels.foreach_set(pc.ravel()); img_ao.pixels.foreach_set(pa.ravel())
        if (lc.get("smooth_region") or {}).get("albedo_blend"):   # opt-in: the albedo seam across a re-surfaced patch
            report["albedo_blend"] = albedo_blend(pc, RC_, me, lc["smooth_region"])
            img_c.pixels.foreach_set(pc.ravel())
            print("ALBEDO_BLEND", json.dumps({k: v for k, v in report["albedo_blend"].items() if k != "why"}))
        # basecolor = albedo (sRGB) x AO (linear), back to sRGB: the one base-colour texture the glb carries
        ao_up = np.asarray(pa[:, 0]).reshape(RA, RA)
        if RA != RC_:
            k_ = RC_ // RA
            ao_up = np.repeat(np.repeat(ao_up, k_, 0), k_, 1) if RC_ % RA == 0 else \
                ao_up[(np.arange(RC_) * RA // RC_)][:, (np.arange(RC_) * RA // RC_)]
        lin = np.where(pc[:, :3] <= 0.04045, pc[:, :3] / 12.92, ((pc[:, :3] + 0.055) / 1.055) ** 2.4)
        lin = lin * ao_up.reshape(-1, 1)
        srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(np.maximum(lin, 0), 1 / 2.4) - 0.055)
        img_b = bpy.data.images.new(unit + "_basecolor", RC_, RC_, alpha=False)
        img_b.colorspace_settings.name = "sRGB"
        img_b.pixels.foreach_set(np.hstack([np.clip(srgb, 0, 1), np.ones((len(srgb), 1))]).astype(np.float32).ravel())
        q8 = lambda a: np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)
        bstats.update({"cage_extrusion": BAKE_CAGE, "resolution": {"normal": RN, "ao": RA, "albedo": RC_},
                       "high_tris": int(len(PFc)), "normal_coverage_pct": round(100 * float(cov_n.mean()), 2),
                       "albedo_coverage_pct": round(100 * float(cov_c.mean()), 2),
                       "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n] > 0.05).mean()), 4),
                       "ao_mean": round(float(pa[cov_a, 0].mean()), 4) if cov_a.any() else None,
                       "pixel_sha": {"normal": sha(q8(px[:, :3])), "ao": sha(q8(pa[:, :1])), "albedo": sha(q8(pc[:, :3]))},
                       "seconds": round(time.time() - tb, 1)})
        if not PREVIEW:                     # (--preview-bake: the main / twin bake-diff inputs are left alone)
            np.save(os.path.join(tempfile.gettempdir(), "%s_normal_%s.npy" % (unit, tag)), px[:, :3])
            np.save(os.path.join(tempfile.gettempdir(), "%s_ao_%s.npy" % (unit, tag)), pa[:, :1])
            np.save(os.path.join(tempfile.gettempdir(), "%s_albedo_%s.npy" % (unit, tag)), pc[:, :3])
        report["bake"] = bstats
        DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
        DIG["bake_albedo"] = bstats["pixel_sha"]["albedo"]
        imgs = {"normal": img_n, "ao": img_ao, "albedo": img_c, "basecolor": img_b}
        if not DIGEST_ONLY:
            tex_dir = PREVIEW[:-6] + "_textures" if PREVIEW else TEX_DIR   # --preview-bake: beside the preview blend
            os.makedirs(tex_dir, exist_ok=True)
            for k_, img in imgs.items():
                img.filepath_raw = os.path.join(tex_dir, "%s_%s.png" % (unit, k_)); img.file_format = "PNG"
                img.save(); img.pack()
        # material: Col x basecolor (albedo x AO) -> Base Color; normal map
        nt.nodes.remove(ta); nt.nodes.remove(tc)
        tb_ = nt.nodes.new("ShaderNodeTexImage"); tb_.image = img_b; tb_.location = (-900, 300)
        mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
        mul.inputs["Factor"].default_value = 1.0
        ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
        oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
        nt.links.new(vc.outputs["Color"], ia); nt.links.new(tb_.outputs["Color"], ib)
        nt.links.new(oc, bsdf.inputs["Base Color"])
        nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
        nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        scene.render.engine = "BLENDER_EEVEE"
        bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm)
        low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
        low.visible_transmission = low.visible_volume_scatter = True
        print("BAKE", json.dumps({k: bstats[k] for k in ("cage_extrusion", "normal_coverage_pct", "albedo_coverage_pct",
                                                          "pixel_sha", "seconds")}))

    if PARTS_L:
        pmat = bpy.data.materials.new(unit + "_parts"); pmat.use_nodes = True
        pnt = pmat.node_tree; pbs = pnt.nodes["Principled BSDF"]
        pvc = pnt.nodes.new("ShaderNodeVertexColor"); pvc.layer_name = "Col"; pvc.name = "col"
        pvg = pnt.nodes.new("ShaderNodeVertexColor"); pvg.layer_name = "Glow"; pvg.name = "glow"
        pnt.links.new(pvc.outputs["Color"], pbs.inputs["Base Color"]); pnt.links.new(pvg.outputs["Color"], pbs.inputs["Emission Color"])
        pmat.use_backface_culling = True
        PAL.apply_material(pmat, pal_default)
        me.materials.append(pmat)
        mi_ = np.zeros(len(LF), np.int32); mi_[NBF:] = len(me.materials) - 1
        me.polygons.foreach_set("material_index", mi_); me.update()
        report["parts_material"] = {"name": pmat.name, "faces": int(len(LF) - NBF),
                                    "rule": "light.parts faces: Col (palette regions) -> Base Color, no baked texture"}
    gl = lc.get("glow3d")                # opt-in (new units only; absent = one region, Glow all off, unchanged)
    if gl:
        report["glow3d"] = glow3d_regions(me, LV, LF, gl, pal_default, REGION,
                                          (pc, RC_) if imgs else None,
                                          init=tuple(PAL.read_regions(me)[:2]) if (PARTS_L or len(SMOOTH_IDS)) else None)
        report["regions_faces"] = report["glow3d"]["paint"]
        DIG["glow3d"] = report["glow3d"]["face_region_sha"]
        print("GLOW3D", json.dumps({k: report["glow3d"][k] for k in ("faces", "rows")}))

    # ================================================================================================ 5. repose
    t = time.time()
    if lc.get("yaw"):                   # opt-in light.yaw (new units only; absent = no change): the whole low turned about
        LV, yrep = yaw_whole(LV, LF, lc["yaw"])   # Z as one rigid body (the root orientation), before the repose
        me.vertices.foreach_set("co", LV.astype(np.float32).ravel()); me.update()
        yrep["mirror_before"] = mir_before
        report["yaw"] = yrep
        DIG["yaw"] = sha(LV)
        print("YAW", json.dumps({k: yrep[k] for k in ("deg", "about", "to", "mirror_before", "mirror_after")}))
    rig_cfg = RC.get("rig") or {}
    V0 = LV.copy()
    rp = RC.get("repose") or {}
    if rp.get("targets"):
        V1, rig_cfg, rrep, ex = PR.run_repose(LV[:NB], LF[:NBF], rig_cfg, rp, Hh, float(rig_cfg.get("falloff", 0.06)), 0.0)
        fm_ = ex["dom_moved"][LF[:NBF]].any(1)
        si0 = self_intersections(V0[:NB], LF[:NBF], fm_)
        si1 = self_intersections(V1, LF[:NBF], fm_)
        if PARTS_L:                     # light.parts: each part moves rigidly with its bone (identity if the bone is unposed)
            bn_ = [b[0] for b in ex["B"]]
            Vq_ = LV[NB:].copy()
            for part in PARTS_L:
                if part.bone in bn_:
                    a_, b_ = part.range
                    Vq_[a_ - NB:b_ - NB] = PR.apply_aff(ex["M"][bn_.index(part.bone)], LV[a_:b_])
            V1 = np.vstack([V1, Vq_])
        rrep["self_intersections"] = {"before": {"all": si0[0], "moved_region": si0[1]},
                                      "after": {"all": si1[0], "moved_region": si1[1]},
                                      "rule": "intersecting triangle pairs sharing no vertex (BVH overlap)"}
        me.vertices.foreach_set("co", V1.astype(np.float32).ravel()); me.update()
        LV = V1
        rrep["mirror_before"] = mir_before
        rrep["mirror_after"] = mirror_to_surface(LV, LF, 0.0)
        rrep["seconds"] = round(time.time() - t, 1)
        report["repose"] = rrep
        DIG["repose"] = sha(V1)
        print("REPOSE", json.dumps({k: rrep[k] for k in rrep if k.startswith(("stretch", "self", "mirror", "rigid",
                                                                                   "max_disp"))}))
        for tg in rrep["targets"]:
            print("REPOSE_TARGET", json.dumps({k: tg[k] for k in ("target", "bones", "lift")}))
        tail_ids = [j for j, b in enumerate(ex["B"]) if b[0] in set(rrep["moved_bones"]) and b[4] == "tail"]
        tail_dom = ex["W"][:, tail_ids].sum(1) >= 0.5 if tail_ids else np.zeros(NB, bool)
        rigid_m = np.zeros(NB, bool)
        for rb in rp.get("rigid") or []:
            j = [b[0] for b in ex["B"]].index(rb["bone"])
            rigid_m |= ex["W"][:, j] >= 1.0 - 1e-12
        tail_dom = np.concatenate([tail_dom, np.zeros(len(LV) - NB, bool)])
        rigid_m = np.concatenate([rigid_m, np.zeros(len(LV) - NB, bool)])
    else:
        rig_cfg, _auto = PR.resolve_auto(rig_cfg, LV)
        report["repose"] = {"enabled": False, "mirror": mir_before, "auto_chains": _auto}
        tail_dom = rigid_m = np.zeros(len(LV), bool)
    if lc.get("belly"):                 # opt-in light.belly (new units only): a fuller belly on the finished low; the
        assert not PARTS_L, "light.belly + light.parts: not supported together"
        bl = lc["belly"]                # texture rides on the UVs; arms swung out rigidly, legs kept
        if rp.get("targets"):
            W_b, B_b = ex["W"], ex["B"]
        else:
            B_b = PR.bones_of(rig_cfg)
            W_b, _ = PR.weights(LV, B_b, Hh, float(rig_cfg.get("falloff", 0.06)))
        Vb0 = LV.copy()
        V2, rig_cfg, brep = belly_push(Vb0, W_b, B_b, rig_cfg, bl)
        czs = [float(z) for z in bl.get("measure_z") or [bl["centre"][2]]]
        brep["metrics"] = [{"before": belly_metrics(Vb0, W_b, B_b, z), "after": belly_metrics(V2, W_b, B_b, z)} for z in czs]
        for m_ in brep["metrics"]:
            m_["width_gain_pct"] = round(100.0 * (m_["after"]["width_x_u"] / m_["before"]["width_x_u"] - 1.0), 2)
            m_["depth_gain_pct"] = round(100.0 * (m_["after"]["depth_y_u"] / m_["before"]["depth_y_u"] - 1.0), 2)
        bm_ = (np.linalg.norm(V2 - Vb0, axis=1) > 1e-7)[LF].any(1)
        sb0 = self_intersections(Vb0, LF, bm_)
        sb1 = self_intersections(V2, LF, bm_)
        brep["self_intersections"] = {"before": {"all": sb0[0], "moved_region": sb0[1]},
                                      "after": {"all": sb1[0], "moved_region": sb1[1]}}
        brep["stretch"] = PR.edge_stretch(Vb0, V2, LF)
        me.vertices.foreach_set("co", V2.astype(np.float32).ravel()); me.update()
        LV = V2
        brep["mirror_after"] = mirror_to_surface(LV, LF, 0.0)
        report["belly"] = brep
        DIG["belly"] = sha(V2)
        print("BELLY", json.dumps({k: brep[k] for k in ("field_max_u", "verts_pushed", "swing", "metrics",
                                                         "self_intersections", "mirror_after") if k in brep}))

    # ================================================================================================ 6. centre + props
    lo0, hi0 = LV.min(0), LV.max(0)
    SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
    report["centre_shift"] = SHIFT.round(6).tolist()
    report["floor"] = {"min_z_after_repose_u": round(float(lo0[2]), 5),
                       "rule": "stage 1 floored the FEET at z 0; the centring puts the lowest point at 0"}
    VALL = LV - SHIFT
    me.vertices.foreach_set("co", VALL.astype(np.float32).ravel()); me.update()
    FALL = LF.tolist()
    RANGE = {"shell": (0, NB), **{p_.name: p_.range for p_ in PARTS_L}}
    me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})
    report["regions_area_share"] = {REGION: 1.0}
    fcfg = RC.get("facing") or {}
    if fcfg.get("anchor") and fcfg.get("landmark"):
        anchor = np.array(fcfg["anchor"]) - SHIFT; landmark = np.array(fcfg["landmark"]) - SHIFT
        frule = fcfg.get("rule", "config anchor -> landmark")
    else:
        mid = np.abs(VALL[:, 0]) < 0.06
        anchor = np.array([0.0, float(VALL[:, 1].mean()), float(VALL[:, 2].mean())])
        j = np.argmin(np.where(mid, VALL[:, 1], np.inf))
        landmark = VALL[j].copy()
        frule = "shell centroid -> the front-most midline point of the shell (proxy finish default)"
    dvec = landmark - anchor
    report["facing"] = {"rule": frule, "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                        "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
    me.shade_flat()
    if len(SMOOTH_IDS):                 # light.smooth: the smooth faces (after every flat-shading pass)
        sm_ = np.zeros(len(me.polygons), bool); sm_[SMOOTH_IDS] = True
        me.polygons.foreach_set("use_smooth", sm_); me.update()
        me["conquest_smooth_regions"] = list(SMOOTH_NAMES)
    tris_main = len(FALL)
    report["tris"] = {"total": tris_main, "shell": int(NBF), **{p_.name: p_.tris() for p_ in PARTS_L}}
    oe_f, nm_f = nonmanifold_edges(LF)
    report["open_edges"] = {"per_island": {"shell": nonmanifold_edges(LF[:NBF])[0],
                                           **{p_.name: PFP.open_edges(p_.F) for p_ in PARTS_L}},
                            "total": oe_f, "nonmanifold_edges": nm_f,
                            "rule": "the watertight remesh, decimated (closed)" + (" + closed light.parts" if PARTS_L else "")}
    H = float(VALL[:, 2].max() - VALL[:, 2].min())
    lo_a, hi_a = VALL.min(0), VALL.max(0)
    fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
    k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
    report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                         "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                         "export_cell_fit_report_only": {"scale": round(k_fit, 5)}}
    mir_final = mirror_to_surface(VALL, LF, float((lo_a[0] + hi_a[0]) / 2))
    report["finish"] = {"open_edges": oe_f, "tris": tris_main, "mirror": mir_final,
                        "tri_budget": TRI_BUDGET, "in_budget": TRI_BUDGET[0] <= tris_main <= TRI_BUDGET[1],
                        "mode": "light"}
    print("FINISH", json.dumps(report["finish"]))
    low["conquest_unit"] = unit
    low["conquest_character_id"] = cfg["char_id"] or unit
    low["conquest_tier"] = cfg["tier"]
    low["conquest_tri_budget"] = TRI_BUDGET
    low["conquest_max_height"] = CELL_MAX_H
    low["conquest_max_footprint"] = CELL_MAX_FP
    low["conquest_yaw_fix_deg"] = 0.0
    low["conquest_front_anchor"] = anchor.tolist()
    low["conquest_front_landmark"] = landmark.tolist()
    low["conquest_facing_rule"] = frule
    low["conquest_finish_mode"] = "light" + (" + repose" if rp.get("targets") else "")
    low["conquest_source"] = ("proxy finish LIGHT: frozen Pixal3D sheet proxy %s (sha %s) kept as generated (watertight "
                              "remesh, decimated, texture baked%s); sheet %s"
                              % (report["stage1"]["proxy"]["glb"], report["stage1"]["proxy"]["sha256"][:12],
                                 ", posed chains straightened with bones" if rp.get("targets") else "",
                                 "design/reference/%s-character-sheet" % unit))
    low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
    low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
    low["conquest_albedo"] = ("baked texture improved/textures/%s_basecolor.png (proxy albedo x AO) x Col (palette region "
                              "'%s', white = as generated)" % (unit, REGION))
    head_pts = VALL[VALL[:, 2] > 0.72 * H]
    feet_pts = VALL[VALL[:, 2] < 0.16 * H]
    focus = {"head": [head_pts.min(0).tolist(), head_pts.max(0).tolist()],
             "feet": [feet_pts.min(0).tolist(), feet_pts.max(0).tolist()]}
    fviews = {}
    if rigid_m.any():
        P_ = VALL[rigid_m]; focus["face"] = [P_.min(0).tolist(), P_.max(0).tolist()]; fviews["face"] = [0.0, 6.0]
        report["focus_face_centre"] = (0.5 * (P_.min(0) + P_.max(0))).round(4).tolist()
    if tail_dom.any():
        P_ = VALL[tail_dom]; focus["tail"] = [P_.min(0).tolist(), P_.max(0).tolist()]; fviews["tail"] = [120.0, 35.0]
    for nm, box in (RC.get("focus") or {}).items():
        focus[nm] = [(np.array(box[0]) - SHIFT).tolist(), (np.array(box[1]) - SHIFT).tolist()]
    for p_ in PARTS_L:
        P_ = VALL[p_.range[0]:p_.range[1]]; focus[p_.name] = [P_.min(0).tolist(), P_.max(0).tolist()]
    if fviews:
        focus["views"] = fviews
    ax_y = 0.0
    if "side" in regs:
        r_ = regs["side"]
        intake = json.load(open(os.path.join(ROOT, "design", "reference", unit, "intake.json")))
        ax_px = intake["views"]["side"]["axis_x_px"]
        ax_y = (ax_px - r_["ox"]) / (PF.PPU * r_["h_sign"]) - SHIFT[1]
    focus["axis_xy"] = [float(-SHIFT[0]), float(ax_y)]
    low["conquest_focus"] = json.dumps(focus)
    if PREVIEW:
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
        np.savez(PREVIEW[:-6] + "_prerepose.npz", V=V0, F=LF, rig=json.dumps(RC.get("rig") or {}),
                 repose=json.dumps(RC.get("repose") or {}), H=Hh)
        print("PREVIEW", json.dumps({k: report.get(k) for k in ("high", "low", "tris", "finish") + (("bake",) if imgs else ())}))
        sys.stdout.flush(); os._exit(0)

    def set_tex_paths(rel_prefix):
        for k_, img in imgs.items():
            img.filepath = rel_prefix + "%s_%s.png" % (unit, k_)

    def geometry_digest(obs):
        import hashlib
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
    report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
    bpy.context.preferences.filepaths.save_version = 0
    if not DIGEST_ONLY:
        set_tex_paths("//textures/")
        os.makedirs(os.path.dirname(OUT_IMPROVED), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
        json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1,
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
        dbg = os.path.join(ROOT, "renders", unit, "_light")
        os.makedirs(dbg, exist_ok=True)
        np.savez(os.path.join(dbg, "prerepose_low.npz"), V=V0 - SHIFT, F=LF, shift=SHIFT)
    print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))
    RC2 = dict(RC); RC2["rig"] = rig_cfg
    PFB.rig_clips_export(unit, cfg, RC2, report, DIG, low, me, VALL, RANGE, PARTS_L, VALL, H, SHIFT, pal_default, set_tex_paths,
                         geometry_digest, OUT_IMPROVED, OUT_RIGGED, OUT_GLB, DIGEST_ONLY, T0)
