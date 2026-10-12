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
  4. REPOSE (opt-in, config "repose"; proxy_finish_repose.py): the posed chains (a curled tail, a turned head) are
     straightened with bones and the deformation is applied as the rest mesh; the bakes ride along on the UVs. Stretch,
     self-intersection, rigid-face and mirror numbers in the report.
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


def run_light(unit, cfg, raw_cfg, PREVIEW, DIGEST_ONLY, SOLID, T0):
    scene = bpy.context.scene
    lc = cfg["light"]
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
    if lc.get("cuts"):                  # opt-in light.cuts: the removed part leaves the BAKE source too (else its texture,
        Pc_ = PV[PFc].mean(1)           # AO shadow and the cage distance would still see the part that is gone)
        cut_in = np.zeros(len(PFc), bool)
        for c_ in lc["cuts"]:
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
    print("STAGE1", round(time.time() - t, 1), "s grid", F.shape, "step", step)

    # ================================================================================================ 2. HIGH + LOW
    t = time.time()
    Vh, Th = SD.polygonise(types.SimpleNamespace(F=F, lo=lo, step=step))
    Vh, Th, crumbs = PFB.keep_islands(Vh, Th, float(lc["speck_frac"]))
    oe_h, nm_h = nonmanifold_edges(Th)
    report["high"] = {"tris": int(len(Th)), "verts": int(len(Vh)), "specks_dropped": crumbs, "open_edges": oe_h,
                      "nonmanifold_edges": nm_h, "rule": "marching tets of the whole remesh grid (no mirror)",
                      "seconds": round(time.time() - t, 1)}
    print("HIGH", json.dumps(report["high"]))
    t = time.time()
    Vp_, Fp_ = decimate(Vh, Th, float(lc["pre_tris"]) / len(Th))
    target = int(cfg["body_tris"])
    dense = RC.get("dense") or []
    calib = None
    if dense:
        dens = float(np.mean([b.get("density", 2.0) for b in dense]))
        A = tri_area(Vp_, Fp_)
        inb = in_boxes(Vp_[Fp_].mean(1), dense)
        a = float(A[inb].sum() / A.sum())
        want = a * dens / (a * dens + (1.0 - a))
        vg = np.where(in_boxes(Vp_, dense), 0.02, 1.0)

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
    LV, LF, specks = PFB.keep_islands(LV, LF, 0.001)
    oe_l, nm_l = nonmanifold_edges(LF)
    # deviation from the PROXY (the artist's pick), both ways
    bvh_p = BVHTree.FromPolygons(PV.tolist(), PFc.tolist())
    d_lp = np.array([bvh_p.find_nearest(Vector(p))[3] for p in LV])
    bvh_l = BVHTree.FromPolygons(LV.tolist(), LF.tolist())
    ss = np.arange(0, len(PV), max(1, len(PV) // 40000))
    if lc.get("cuts"):                  # only the vertices of the KEPT proxy faces (a cut part is not a deviation)
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
    print("LOW", json.dumps(report["low"]))
    BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(d_pl, 99)), 4))
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
    PAL.store_regions(me, [REGION], np.zeros(len(LF), np.int32), np.ones(len(LF)))
    report["regions_faces"] = PAL.paint(me, pal_default)
    PAL.apply_material(mat, pal_default)
    me.update()
    me.shade_flat()
    bpy.context.view_layer.objects.active = low
    for o in scene.objects:
        o.select_set(o is low)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003, area_weight=0.0,
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
            bpy.ops.uv.smart_project(angle_limit=math.radians(float(uvfix.get("angle_deg", 30.0))), island_margin=0.003,
                                     area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.pack_islands(rotate=True, margin=0.003)
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
    bpy.ops.uv.pack_islands(rotate=True, margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")
    mir_before = mirror_to_surface(LV, LF, 0.0)

    # ================================================================================================ 4. bake (FLAT low)
    imgs = {}
    if not PREVIEW:
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
            arr = Z[texkeys[0]]
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
        for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16), ("EMIT", tc, 1)):
            nt.nodes.active = node
            scene.cycles.samples = samples
            t = time.time()
            r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE,
                                     max_ray_distance=BAKE_CAGE * 2.0, margin=16, use_clear=False)
            bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
        px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
        pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
        pc = np.empty(RC_ * RC_ * 4, dtype=np.float32); img_c.pixels.foreach_get(pc); pc = pc.reshape(-1, 4)
        cov_n = px[:, 2] > 0.25
        cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
        cov_c = ~((np.abs(pc[:, 0] - 1.0) < 1e-3) & (pc[:, 1] < 1e-3) & (np.abs(pc[:, 2] - 1.0) < 1e-3))
        devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
        px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
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
        mean_c = pc[cov_c, :3].mean(0) if cov_c.any() else np.full(3, 0.5)
        pc[~cov_c, :3] = mean_c; img_c.pixels.foreach_set(pc.ravel())
        if lc.get("recolour"):              # opt-in (new units only): targeted texture recolours on a GEOMETRY mask
            report["recolour"] = recolour_albedo(pc, RC_, me, lc["recolour"])
            img_c.pixels.foreach_set(pc.ravel())
        if lc.get("despeckle"):             # opt-in (new units only): dark bake hits on the soup's inner faces, refilled
            dsp = lc["despeckle"]
            report["despeckle"] = {"albedo": despeckle(pc, RC_, me, dsp, 3), "ao": despeckle(pa, RA, me, dsp, 1)}
            pa[:, 1] = pa[:, 0]; pa[:, 2] = pa[:, 0]
            img_c.pixels.foreach_set(pc.ravel()); img_ao.pixels.foreach_set(pa.ravel())
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
        np.save(os.path.join(tempfile.gettempdir(), "%s_normal_%s.npy" % (unit, tag)), px[:, :3])
        np.save(os.path.join(tempfile.gettempdir(), "%s_ao_%s.npy" % (unit, tag)), pa[:, :1])
        np.save(os.path.join(tempfile.gettempdir(), "%s_albedo_%s.npy" % (unit, tag)), pc[:, :3])
        report["bake"] = bstats
        DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
        DIG["bake_albedo"] = bstats["pixel_sha"]["albedo"]
        imgs = {"normal": img_n, "ao": img_ao, "albedo": img_c, "basecolor": img_b}
        if not DIGEST_ONLY:
            os.makedirs(TEX_DIR, exist_ok=True)
            for k_, img in imgs.items():
                img.filepath_raw = os.path.join(TEX_DIR, "%s_%s.png" % (unit, k_)); img.file_format = "PNG"
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

    # ================================================================================================ 5. repose
    t = time.time()
    rig_cfg = RC.get("rig") or {}
    V0 = LV.copy()
    rp = RC.get("repose") or {}
    if rp.get("targets"):
        V1, rig_cfg, rrep, ex = PR.run_repose(LV, LF, rig_cfg, rp, Hh, float(rig_cfg.get("falloff", 0.06)), 0.0)
        fm_ = ex["dom_moved"][LF].any(1)
        si0 = self_intersections(V0, LF, fm_)
        si1 = self_intersections(V1, LF, fm_)
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
        tail_dom = ex["W"][:, tail_ids].sum(1) >= 0.5 if tail_ids else np.zeros(len(LV), bool)
        rigid_m = np.zeros(len(LV), bool)
        for rb in rp.get("rigid") or []:
            j = [b[0] for b in ex["B"]].index(rb["bone"])
            rigid_m |= ex["W"][:, j] >= 1.0 - 1e-12
    else:
        rig_cfg, _auto = PR.resolve_auto(rig_cfg, LV)
        report["repose"] = {"enabled": False, "mirror": mir_before, "auto_chains": _auto}
        tail_dom = rigid_m = np.zeros(len(LV), bool)

    # ================================================================================================ 6. centre + props
    lo0, hi0 = LV.min(0), LV.max(0)
    SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
    report["centre_shift"] = SHIFT.round(6).tolist()
    report["floor"] = {"min_z_after_repose_u": round(float(lo0[2]), 5),
                       "rule": "stage 1 floored the FEET at z 0; the centring puts the lowest point at 0"}
    VALL = LV - SHIFT
    me.vertices.foreach_set("co", VALL.astype(np.float32).ravel()); me.update()
    FALL = LF.tolist()
    RANGE = {"shell": (0, len(VALL))}
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
    tris_main = len(FALL)
    report["tris"] = {"total": tris_main, "shell": tris_main}
    oe_f, nm_f = nonmanifold_edges(LF)
    report["open_edges"] = {"per_island": {"shell": oe_f}, "total": oe_f, "nonmanifold_edges": nm_f,
                            "rule": "the watertight remesh, decimated (closed)"}
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
        print("PREVIEW", json.dumps({k: report.get(k) for k in ("high", "low", "tris", "finish")}))
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
    PFB.rig_clips_export(unit, cfg, RC2, report, DIG, low, me, VALL, RANGE, [], VALL, H, SHIFT, pal_default, set_tex_paths,
                         geometry_digest, OUT_IMPROVED, OUT_RIGGED, OUT_GLB, DIGEST_ONLY, T0)
