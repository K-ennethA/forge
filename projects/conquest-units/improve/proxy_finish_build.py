"""Proxy finish -- the BLENDER half (run through the unit's stub improve/<unit>_build.py; proxy_finish.py has the
design notes). One headless run, the firefly pattern's outputs / props / clips / glb contract (BUILD_CONTRACT.md):

  1. STAGE 1 (proxy_finish.py solid, service venv, a subprocess): the clean symmetric SDF grid + colour sources. Both
     digest twins run it (main / twin npz in %TEMP%), so the grid sha is in the digest.
  2. HIGH = marching tets (magmoo_sdf.polygonise, read-only) of the +X HALF of the grid, mirrored and welded on the
     plane x = 0: the high is EXACTLY mirror-symmetric (the Kuhn split is not, so a whole-grid polygonise would not be).
  3. COLOUR on the high: per face a vote from every sheet view that sees it (ortho z-buffer, weight = facing), the
     proxy's drift-corrected texture chips (both mirror partners), summed with the mirror face's votes; a majority
     filter; islands under `min_island_area` merged into their longest-border neighbour (chips in `keep_small` - eyes,
     buttons - use the smaller `keep_small_area`): no speckle, no polka dots.
  4. LOW = Blender COLLAPSE decimation (deterministic, use_symmetry X) to `body_tris`, region borders protected by a
     vertex-group weight (the triangles go where the colour edges are); regions moved to the low as soft per-vertex
     indicators (area-weighted high labels within a radius), every border ISO-CUT into the mesh: smooth region lines.
  5. PARTS (proxy_finish_parts.py): the procedural closed replacements the config asks for (blades / fans / starbursts /
     decals), mirrored .L/.R.
  6. palette regions (palettes/<unit>/default.json) -> Col / Glow, FLAT shaded, Smart UV, bake normal + AO from the
     high (+ parts) against the FLAT low (BUILD_CONTRACT step 5: shade_flat BEFORE bake, never smooth).
  7. RIG from the config's joints (sheet px or units; spine + mirrored chains) under the contract 'root'; weights by
     nearest-bone distance falloff (<= 4, side-aware), parts rigid; PLACEHOLDER idle / walk clips (integer harmonics,
     every deform bone keyed, LINEAR, cyclic); every contract prop; glb export; digest parts.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, tempfile, subprocess, types
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K               # noqa: E402  (read-only use)
import palettes as PAL           # noqa: E402  (read-only use)
import magmoo_sdf as SD          # noqa: E402  (read-only use: polygonise = marching tets)
import proxy_finish as PF        # noqa: E402
import proxy_finish_parts as PP  # noqa: E402

VPY_CANDIDATES = [os.path.join(PF.REPO, "service", ".venv", "Scripts", "python.exe"),
                  r"C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"]
CUT_SNAP = 0.18


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mesh_arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], [list(map(int, f)) for f in F])
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def keep_islands(V, F, min_frac):
    F = np.asarray(F, np.int64)
    n = len(V)
    p = components(n, np.concatenate([F[:, 0], F[:, 1]]), np.concatenate([F[:, 1], F[:, 2]]))
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)
    keep_v = (cnt >= min_frac * n)[inv]
    remap = -np.ones(n, dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    kf = keep_v[F[:, 0]]
    return V[keep_v], remap[F[kf]], {"pieces": int(len(cnt)), "kept": int((cnt >= min_frac * n).sum()),
                                     "dropped_verts": int((~keep_v).sum())}


def components(n, ea, eb):
    """connected components (min label) by hooking + pointer jumping (numpy, O(log n) rounds)"""
    p = np.arange(n)
    while True:
        pa, pb = p[ea], p[eb]
        lo_ = np.minimum(pa, pb); hi_ = np.maximum(pa, pb)
        q = p.copy()
        np.minimum.at(q, hi_, lo_)
        while True:
            r = q[q]
            if np.array_equal(r, q):
                break
            q = r
        if np.array_equal(q, p):
            return p
        p = q


def face_adjacency(F):
    """(fa, fb, edge (va, vb)) for every interior edge of a triangle list"""
    F = np.asarray(F, np.int64)
    nf = len(F)
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    fid = np.tile(np.arange(nf), 3)
    Es = np.sort(E, 1)
    key = Es[:, 0] * (int(Es.max()) + 1) + Es[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = ks[1:] == ks[:-1]
    i = order[:-1][same]; j = order[1:][same]
    return fid[i], fid[j], Es[i]


# ======================================================================================================================
def run(unit):
    T0 = time.time()
    scene = bpy.context.scene
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
    DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
    SOLID = argv[argv.index("--solid") + 1] if "--solid" in argv else None
    cfg, raw_cfg = PF.load_cfg(unit)
    if cfg["mode"] == "light":                    # the proxy as generated, made shippable (+ optional bone repose)
        import proxy_finish_light as PFL
        return PFL.run_light(unit, cfg, raw_cfg, PREVIEW, DIGEST_ONLY, SOLID, T0)
    CC = cfg["colour"]
    OUT_IMPROVED = os.path.join(ROOT, "improved", unit + ".blend")
    OUT_RIGGED = os.path.join(ROOT, "rigged", unit + ".blend")
    OUT_GLB = os.path.join(ROOT, "rigged", unit + ".glb")
    TEX_DIR = os.path.join(ROOT, "improved", "textures")
    TRI_BUDGET = list(cfg["tri_budget"])
    CELL_MAX_H, CELL_MAX_FP = cfg["cell_max"]
    report = {"unit": unit, "conquest_character_id": cfg["char_id"] or unit, "tier": cfg["tier"], "tri_budget": TRI_BUDGET,
              "pattern": "proxy finish (proxy_finish.py + proxy_finish_build.py): a frozen Pixal3D sheet proxy as the "
                         "base, finished to the contract", "config": raw_cfg,
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
        vpy = next(p for p in VPY_CANDIDATES if os.path.exists(p))
        env = dict(os.environ); env["PYTHONDONTWRITEBYTECODE"] = "1"
        r = subprocess.run([vpy, "-P", os.path.join(HERE, "proxy_finish.py"), "solid", unit, "--out", npz],
                           capture_output=True, text=True, cwd=HERE, env=env, creationflags=PF.NO_WINDOW)
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit("STAGE1 FAILED")
        report["stage1_stdout"] = r.stdout.splitlines()[:2]
    Z = np.load(npz)
    meta = json.loads(bytes(Z["meta"]).decode())
    F = Z["F"].astype(np.float64); lo = Z["lo"].astype(np.float64); step = float(Z["step"][0])
    regs, RC, names, chips = meta["regs"], meta["resolved"], meta["chip_names"], meta["chips"]
    report["stage1"] = meta["report"]
    DIG["solid_grid"] = sha(F[::2, ::2, ::2])
    print("STAGE1", round(time.time() - t, 1), "s grid", F.shape, "step", step)

    # ================================================================================================ 2. HIGH (exact mirror)
    t = time.time()
    m = (F.shape[0] - 1) // 2
    gh = types.SimpleNamespace(F=np.ascontiguousarray(F[m:]), lo=np.array([0.0, lo[1], lo[2]]), step=step)
    Vh, Th = SD.polygonise(gh)
    Vh, Th, crumbs = keep_islands(Vh, Th, 0.001)
    nR = len(Th)
    Vm = Vh * np.array([-1.0, 1.0, 1.0])
    on_plane = Vh[:, 0] == 0.0
    idx_m = np.arange(len(Vh)) + len(Vh)
    idx_m[on_plane] = np.nonzero(on_plane)[0]
    HV = np.vstack([Vh, Vm])
    HF = np.vstack([Th, idx_m[Th[:, ::-1]]])
    used = np.zeros(len(HV), bool); used[HF.ravel()] = True
    remap = -np.ones(len(HV), np.int64); remap[used] = np.arange(int(used.sum()))
    HV = HV[used]; HF = remap[HF]
    MIRF = np.concatenate([np.arange(nR) + nR, np.arange(nR)])        # face j <-> its mirror face
    oe = PP.open_edges(HF.tolist())
    report["high"] = {"tris": int(len(HF)), "verts": int(len(HV)), "half_tris": int(nR), "crumbs": crumbs,
                      "open_edges": oe, "plane_verts": int(on_plane.sum()),
                      "rule": "polygonise the +X half grid, mirror, weld on x = 0 (exact symmetry)",
                      "seconds": round(time.time() - t, 1)}
    print("HIGH", json.dumps(report["high"]))

    # ================================================================================================ 3. colour on the high
    t = time.time()
    T3 = HV[HF]
    FC = T3.mean(1)
    FNr = np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0])
    FA = 0.5 * np.linalg.norm(FNr, axis=1)
    FN = FNr / np.maximum(2 * FA, 1e-18)[:, None]
    merge = CC.get("merge") or {}
    reg_names = []
    for n_ in names:
        tgt = merge.get(n_, n_)
        if tgt not in reg_names:
            reg_names.append(tgt)
    chip_to_reg = np.array([reg_names.index(merge.get(n_, n_)) for n_ in names])
    NR = len(reg_names)
    votes = np.zeros((len(HF), NR))
    view_rep = {}
    facing = meta["facing"]
    cam_dir = {"front": np.array([0.0, -1.0, 0.0]), "back": np.array([0.0, 1.0, 0.0]),
               "side": np.array([1.0, 0.0, 0.0]) if facing == "left" else np.array([-1.0, 0.0, 0.0])}
    B6 = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5], [1 / 3, 1 / 3, 1 / 3]])
    SP6 = np.einsum("kj,fjd->fkd", B6, T3).reshape(-1, 3)
    for v, info in meta["sheet_label_views"].items():
        lab = Z["lab_" + v]; ox0, oy0 = info["origin"]
        reg = regs[v]
        d = cam_dir[v]
        w = FN @ d
        depth_s = -(SP6 @ d)                           # smaller = closer to the camera
        uv_s = np.rint(PF.model_to_px(reg, SP6)).astype(np.int64)
        u0, v0 = uv_s.min(0); u1, v1 = uv_s.max(0)
        zb = np.full((v1 - v0 + 1, u1 - u0 + 1), np.inf)
        np.minimum.at(zb, (uv_s[:, 1] - v0, uv_s[:, 0] - u0), depth_s)
        uv_c = np.rint(PF.model_to_px(reg, FC)).astype(np.int64)
        dc = -(FC @ d)
        vis = dc <= zb[uv_c[:, 1] - v0, uv_c[:, 0] - u0] + 1.5 * step
        cand = (w > CC["facing_min"]) & vis
        iy = uv_c[:, 1] - oy0; ix = uv_c[:, 0] - ox0
        inb = (iy >= 0) & (iy < lab.shape[0]) & (ix >= 0) & (ix < lab.shape[1])
        L = np.full(len(HF), -1)
        L[inb] = lab[iy[inb], ix[inb]]
        ok = cand & (L >= 0)
        np.add.at(votes, (np.nonzero(ok)[0], chip_to_reg[L[ok]]), w[ok])
        view_rep[v] = {"faces_voted": int(ok.sum()), "visible": int((cand).sum())}
    kd = KDTree(len(Z["samp_p"]))
    for i, p in enumerate(Z["samp_p"]):
        kd.insert(p.tolist(), i)
    kd.balance()
    sc = Z["samp_chip"]
    pw = float(CC["proxy_weight"])
    pv = np.zeros(len(HF), np.int64)
    for i, c in enumerate(FC):
        _co, j, dist = kd.find(c.tolist())
        pv[i] = sc[j] if dist < 3 * step else -1
    accent = [i for i, n_ in enumerate(names) if n_ in CC.get("keep_small", [])]
    okp = (pv >= 0) & ~np.isin(pv, accent)          # accents (eyes, buttons) must be SHEET-attested: no proxy vote
    np.add.at(votes, (np.nonzero(okp)[0], chip_to_reg[pv[okp]]), pw)
    votes = votes + votes[MIRF]                         # symmetric: each face + its mirror partner
    lab_h = np.argmax(votes, 1)
    none = votes.max(1) <= 0
    lab_h[none] = int(np.bincount(lab_h[~none], minlength=NR).argmax()) if (~none).any() else 0
    report["colour_votes"] = {"views": view_rep, "proxy_faces": int(okp.sum()), "unvoted_faces": int(none.sum()),
                              "regions": reg_names, "merge": merge}
    # cleanup on the high: majority filter, then island merge
    fa, fb, _e = face_adjacency(HF)
    for _ in range(int(CC["majority_iters"])):
        H1 = np.zeros((len(HF), NR)); H1[np.arange(len(HF)), lab_h] = 1.5
        np.add.at(H1, (fa, lab_h[fb]), 1.0); np.add.at(H1, (fb, lab_h[fa]), 1.0)
        lab_h = np.argmax(H1 + H1[MIRF], 1)          # symmetric: the face and its mirror decide together
    keep_small = [reg_names.index(merge.get(n_, n_)) for n_ in CC.get("keep_small", []) if merge.get(n_, n_) in reg_names]
    ev = HV[_e[:, 0]] - HV[_e[:, 1]]
    elen = np.linalg.norm(ev, axis=1)
    merged_islands = 0
    for _round in range(12):
        same = lab_h[fa] == lab_h[fb]
        isl = components(len(HF), fa[same], fb[same])
        area = np.bincount(isl, FA, minlength=len(HF))
        thr = np.where(np.isin(lab_h, keep_small), CC["keep_small_area"], CC["min_island_area"])
        small_face = area[isl] < thr
        if not small_face.any():
            break
        bd = ~same
        A_ = np.concatenate([fa[bd], fb[bd]]); B_ = np.concatenate([fb[bd], fa[bd]]); Lb = np.concatenate([elen[bd], elen[bd]])
        sel = small_face[A_]
        key = isl[A_[sel]] * NR + lab_h[B_[sel]]
        uk, inv = np.unique(key, return_inverse=True)
        tot = np.bincount(inv, Lb[sel])
        isl_k = uk // NR; lab_k = uk % NR
        order = np.lexsort((-tot, isl_k))
        first = np.ones(len(order), bool); first[1:] = isl_k[order][1:] != isl_k[order][:-1]
        best = dict(zip(isl_k[order][first].tolist(), lab_k[order][first].tolist()))
        tgt = np.array([best.get(int(i), -1) for i in isl])
        ch = small_face & (tgt >= 0)
        merged_islands += int(len(np.unique(isl[ch])))
        lab_h = np.where(ch, tgt, lab_h)
        lab_h = np.where(lab_h == lab_h[MIRF], lab_h, np.minimum(lab_h, lab_h[MIRF]))
    # small sheet features the low cannot carry (eyes, buttons): each such high island -> a closed conforming DECAL
    # (centre / normal / ellipse from the island's area-weighted PCA), and the island takes its neighbour's colour
    same = lab_h[fa] == lab_h[fb]
    isl = components(len(HF), fa[same], fb[same])
    area = np.bincount(isl, FA, minlength=len(HF))
    dchips = [reg_names.index(merge.get(n_, n_)) for n_ in CC.get("decal_chips", CC.get("keep_small", []))
              if merge.get(n_, n_) in reg_names]
    dmax = float(CC.get("decal_max_area", 0.08)); dmin = float(CC.get("decal_min_area", 0.006))
    DECALS = []
    reg_lab = PF.srgb_to_lab(np.array([chips[names.index(rn)] if rn in names else chips[0] for rn in reg_names], float))
    roots = np.unique(isl[np.isin(lab_h, dchips)]) if dchips else np.array([], int)
    n_speck = 0
    for r0 in roots:
        if not (area[r0] <= dmax):
            continue
        fm = isl == r0
        bdm = (fm[fa] != fm[fb])
        inside_a = fm[fa] & bdm
        other = np.concatenate([lab_h[fb[inside_a]], lab_h[fa[fm[fb] & bdm]]])
        wl = np.concatenate([elen[inside_a], elen[fm[fb] & bdm]])
        hist = np.bincount(other, wl, minlength=NR) if len(other) else np.zeros(NR)
        nbr = int(np.argmax(hist)) if len(other) else int(lab_h[fm][0])
        purity = float(hist.max() / max(hist.sum(), 1e-12))
        contrast = float(np.linalg.norm(reg_lab[lab_h[fm][0]] - reg_lab[nbr]))
        if area[r0] < dmin or contrast < float(CC.get("decal_min_contrast", 25.0)) or                 purity < float(CC.get("decal_min_purity", 0.8)):
            lab_h[fm] = nbr; n_speck += 1      # a low-contrast speck (a dark fleck inside a leaf): merged, no decal
            continue
        A_ = FA[fm].sum()
        c_ = (FC[fm] * FA[fm, None]).sum(0) / A_
        n_ = (FN[fm] * FA[fm, None]).sum(0)
        if np.linalg.norm(n_) / A_ < float(CC.get("decal_flatness", 0.7)):
            continue                # a curved patch (a berry, a bead): stays a region, never a flat decal
        n_ /= max(np.linalg.norm(n_), 1e-12)
        Q = FC[fm] - c_; Q = Q - np.outer(Q @ n_, n_)
        ev, evec = np.linalg.eigh((Q * FA[fm, None]).T @ Q / A_)
        o_ = np.argsort(ev)[::-1]
        major, minor = (2.0 * math.sqrt(max(ev[o_[0]], 0.0)), 2.0 * math.sqrt(max(ev[o_[1]], 0.0)))
        up_ = evec[:, o_[0]]
        if up_[2] < 0:
            up_ = -up_
        fill = float(A_ / max(math.pi * max(minor, 1e-6) * max(major, 1e-6), 1e-12))
        DECALS.append({"region": reg_names[lab_h[fm][0]], "at": c_, "normal": n_, "a": max(minor, 0.012),
                       "b": max(major, 0.012), "up": up_, "area": float(A_), "purity": round(purity, 3),
                       "contrast": round(contrast, 1), "fill": round(fill, 3),
                       "flatness": round(float(np.linalg.norm((FN[fm] * FA[fm, None]).sum(0)) / A_), 3)})
        lab_h[fm] = nbr
    DECALS.sort(key=lambda d: (round(float(d["at"][2]), 4), round(float(d["at"][0]), 4)))
    same = lab_h[fa] == lab_h[fb]
    isl = components(len(HF), fa[same], fb[same])
    report["decal_specks_merged"] = n_speck
    report["decals_auto"] = [{"region": d["region"], "at": np.round(d["at"], 4).tolist(), "size_u": [round(d["a"], 4),
                              round(d["b"], 4)], "high_area_u2": round(d["area"], 5), "purity": d["purity"],
                              "contrast": d["contrast"], "fill": d["fill"], "flatness": d["flatness"]} for d in DECALS]
    print("DECALS", json.dumps(report["decals_auto"]))
    report["colour_high"] = {"islands": int(len(np.unique(isl))), "merged_islands": merged_islands,
                             "area_share": {reg_names[r]: round(float(FA[lab_h == r].sum() / FA.sum()), 4) for r in range(NR)},
                             "symmetric_labels": bool(np.array_equal(lab_h, lab_h[MIRF])),
                             "seconds": round(time.time() - t, 1)}
    print("COLOUR_HIGH", json.dumps(report["colour_high"]))

    # ================================================================================================ 4. LOW
    t = time.time()
    bnd_v = np.zeros(len(HV), bool)
    bd = lab_h[fa] != lab_h[fb]
    bnd_v[_e[bd].ravel()] = True
    tmp = new_obj("dec_src", HV, HF)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, cfg["body_tris"] / len(HF))
    dm.use_symmetry = True; dm.symmetry_axis = "X"
    protect = float(CC.get("border_protect", 0.0))
    if protect > 0:                 # SOFT: weight 0 would lock an edge outright (Blender skips both-zero edges)
        vg = tmp.vertex_groups.new(name="dec_w")
        wv = np.where(bnd_v, 0.25, 1.0)
        for val in (0.25, 1.0):
            ids = np.nonzero(wv == val)[0].tolist()
            if ids:
                vg.add(ids, val, "REPLACE")
        dm.vertex_group = "dec_w"; dm.vertex_group_factor = protect
    dg = bpy.context.evaluated_depsgraph_get()
    me_l = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    LV, LF = mesh_arrays(me_l)
    bpy.data.meshes.remove(me_l)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LF = np.array(LF, np.int64)
    LV, LF, specks = keep_islands(LV, LF, 0.001)
    bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF.tolist())
    dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
    bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF.tolist())
    _hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
    dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
    Vmir = LV * np.array([-1.0, 1.0, 1.0])
    mir = np.array([bvh_lo.find_nearest(Vector(p))[3] for p in Vmir])
    report["low"] = {"method": "collapse decimation (deterministic, use_symmetry X; colour-border protection %.3f)"
                               % protect,
                     "tris": int(len(LF)), "verts": int(len(LV)), "specks": specks,
                     "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                     "max": round(float(dev.max()), 5)},
                     "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                     "mirror_vertex_to_surface": {"max": round(float(mir.max()), 6), "p99": round(float(np.percentile(mir, 99)), 6)},
                     "bbox_low_over_high": np.round((LV.max(0) - LV.min(0)) / (HV.max(0) - HV.min(0)), 4).tolist(),
                     "seconds": round(time.time() - t, 1)}
    print("LOW", json.dumps(report["low"]))
    BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))

    # regions onto the low: soft indicators (area-weighted high labels within rho of each low vertex) + iso cuts
    t = time.time()
    kh = KDTree(len(FC))
    for i, c in enumerate(FC):
        kh.insert(c.tolist(), i)
    kh.balance()
    le = np.linalg.norm(LV[LF[:, 0]] - LV[LF[:, 1]], axis=1)
    rho = float(CC.get("transfer_radius", 0.5)) * float(np.median(le))
    IND = np.zeros((len(LV), NR))
    for i, p in enumerate(LV):
        hits = kh.find_range(p.tolist(), rho)
        if not hits:
            hits = [kh.find(p.tolist())]
        ids = np.array([h[1] for h in hits]); ww = FA[ids]
        np.add.at(IND[i], lab_h[ids], ww)
    IND /= np.maximum(IND.sum(1, keepdims=True), 1e-18)
    va = np.concatenate([LF[:, 0], LF[:, 1], LF[:, 2]]); vb = np.concatenate([LF[:, 1], LF[:, 2], LF[:, 0]])
    deg = np.bincount(va, minlength=len(LV)) + np.bincount(vb, minlength=len(LV))
    for _ in range(int(CC["boundary_smooth_iters"])):
        S = np.zeros_like(IND)
        np.add.at(S, va, IND[vb]); np.add.at(S, vb, IND[va])
        IND = 0.5 * IND + 0.5 * S / np.maximum(deg, 1)[:, None]
    present = [r for r in range(NR) if (IND.argmax(1) == r).any()]
    bm = bmesh.new()
    for p in LV:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in LF:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {}
    for r in range(NR):
        LAY["i%d" % r] = bm.verts.layers.float.new("i%d" % r)
    for v in bm.verts:
        for r in range(NR):
            v[LAY["i%d" % r]] = float(IND[v.index, r])
    OWN = {}
    for r in present:
        L_ = bm.verts.layers.float.new("own%d" % r); OWN[r] = L_
    for v in bm.verts:
        vals = [v[LAY["i%d" % r]] for r in range(NR)]
        for r in present:
            v[OWN[r]] = vals[r] - max(vals[k] for k in range(NR) if k != r)
    ALL = dict(LAY); ALL.update({"own%d" % r: OWN[r] for r in present})

    def iso_cut(L, tau=0.0):
        eps = 1e-6
        side = lambda v: 0 if abs(v[L] - tau) <= eps else (1 if v[L] > tau else -1)
        for e in bm.edges:
            a, b = e.verts
            sa, sb = side(a), side(b)
            if sa * sb < 0:
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0]
        for e in cuts:
            a, b = e.verts
            tt = (tau - a[L]) / (b[L] - a[L])
            vals = {k: a[ALL[k]] * (1 - tt) + b[ALL[k]] * tt for k in ALL}
            _, nv = bmesh.utils.edge_split(e, a, tt)
            for k in ALL:
                nv[ALL[k]] = vals[k]
            nv[L] = tau
        on_verts, seen = [], set()  # face order (deterministic), never a set's hash order
        for f in bm.faces:
            vs = list(f.verts)
            sides = [side(v) for v in vs]
            if 1 in sides and -1 in sides:
                cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
                if len(cv) == 2:
                    for v in cv:
                        if v not in seen:
                            seen.add(v); on_verts.append(v)
        if on_verts:                # one call: every face holding two on-contour verts is split between them
            bmesh.ops.connect_verts(bm, verts=on_verts)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        return len(cuts)
    order_r = sorted(present, key=lambda r: -float(IND[:, r].sum()))
    facet_mode = CC.get("mode", "regions") == "facet"   # facet: one flat palette tone per low facet (crystal units)
    cut_log = {} if facet_mode else {reg_names[r]: iso_cut(OWN[r]) for r in order_r[1:]}
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    CV = np.array([v.co[:] for v in bm.verts])
    CF = np.array([[v.index for v in f.verts] for f in bm.faces], np.int64)
    FI = np.array([[np.mean([v[LAY["i%d" % r]] for v in f.verts]) for r in range(NR)] for f in bm.faces])
    bm.free()
    shell_lab = np.argmax(FI, 1)
    # last guard: a low island smaller than 3 faces joins its neighbour (no single-face dots after the cut)
    fa2, fb2, _e2 = face_adjacency(CF)
    T2 = CV[CF]
    FA2 = 0.5 * np.linalg.norm(np.cross(T2[:, 1] - T2[:, 0], T2[:, 2] - T2[:, 0]), axis=1)
    for _ in range(4):
        same2 = shell_lab[fa2] == shell_lab[fb2]
        isl2 = components(len(CF), fa2[same2], fb2[same2])
        cnt2 = np.bincount(isl2, minlength=len(CF))
        sm = cnt2[isl2] < 3
        if not sm.any():
            break
        nb = np.full(len(CF), -1)
        nb[fa2[~same2]] = shell_lab[fb2[~same2]]; nb[fb2[~same2]] = shell_lab[fa2[~same2]]
        shell_lab = np.where(sm & (nb >= 0), nb, shell_lab)
    same2 = shell_lab[fa2] == shell_lab[fb2]
    isl2 = components(len(CF), fa2[same2], fb2[same2])
    report["regions_low"] = {"transfer_radius_u": round(rho, 4), "iso_cut_edges": cut_log, "tris_after_cuts": int(len(CF)),
                             "islands": int(len(np.unique(isl2))),
                             "area_share": {reg_names[r]: round(float(FA2[shell_lab == r].sum() / FA2.sum()), 4)
                                            for r in range(NR)},
                             "seconds": round(time.time() - t, 1)}
    print("REGIONS_LOW", json.dumps(report["regions_low"]))
    bvh_shell = BVHTree.FromPolygons(CV.tolist(), CF.tolist())

    # ================================================================================================ 5. parts
    def project(pts, n):
        out = []
        for p in pts:
            hit = bvh_shell.ray_cast(Vector(p + n * 1.0), Vector(-n), 3.0)
            out.append(np.array(hit[0]) if hit[0] is not None else p)
        return np.array(out)
    PARTS = []
    for spec in RC.get("parts") or []:
        kind = spec["type"]
        sides = ("L", "R") if spec.get("mirror", True) else ("M",)
        for s in sides:
            if kind == "blade":
                V, Fp_, R = PP.blade(spec["base"], spec["tip"], spec["radius"], **spec.get("shape", {}))
            elif kind == "fan":
                V, Fp_, R = PP.fan(spec["root"], spec["tips"] if "tips" in spec else spec["points"], spec["radius"],
                                   **spec.get("shape", {}))
            elif kind == "starburst":
                c_ = np.asarray(spec["centre"], float)
                if spec.get("project", False):      # seat the burst on the shell along -normal
                    c_ = project(c_[None], PP.unit(spec.get("normal", [0, -1, 0])))[0]
                V, Fp_, R = PP.starburst(c_, spec.get("normal", [0, -1, 0]), spec["count"], spec["length"],
                                         spec["radius"], **spec.get("shape", {}))
            elif kind == "decal":
                V, Fp_, R = PP.decal(spec["at"], spec.get("normal", [0, -1, 0]), spec["size"][0], spec["size"][1],
                                     project, region=spec["region"], **spec.get("shape", {}))
            else:
                raise ValueError(kind)
            V = np.asarray(V, float)
            if s == "R":
                V = V * np.array([-1.0, 1.0, 1.0]); Fp_ = [f[::-1] for f in Fp_]
            nm = spec["name"] + ("" if s == "M" else "." + s)
            bone = spec.get("bone")
            if bone:
                bone = bone.replace("{S}", s if s != "M" else "L")
            PARTS.append(PP.Part(nm, V, Fp_, R, bone=bone, kind=kind))
    dsh = CC.get("decal_shape", {})
    for k, d in enumerate(DECALS):
        V, Fp_, R = PP.decal(d["at"], d["normal"], d["a"], d["b"], project, region=d["region"], up=d["up"],
                             t_up=dsh.get("t_up", 0.014), t_dn=dsh.get("t_dn", 0.04), dome=dsh.get("dome", 0.008))
        PARTS.append(PP.Part("decal_%s.%d" % (d["region"], k), V, Fp_, R, bone=None, kind="decal_auto"))
    report["parts"] = {p.name: {"tris": p.tris(), "open_edges": PP.open_edges(p.F), "kind": p.kind, "bone": p.bone}
                       for p in PARTS}

    # ================================================================================================ 6. assemble + centre
    pal_default = PAL.load(unit, "default")
    shell_reg = [reg_names[r] for r in shell_lab]
    ISL = [("shell", CV, CF.tolist(), shell_reg, None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
    allV = np.vstack([i[1] for i in ISL])
    lo0, hi0 = allV.min(0), allV.max(0)
    SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
    report["centre_shift"] = SHIFT.round(6).tolist()
    VALL, FALL, RNAME, RANGE = [], [], [], {}
    nv_ = 0
    for name, V, Fs, R, p in ISL:
        RANGE[name] = (nv_, nv_ + len(V))
        VALL.append(V - SHIFT); FALL += [[i + nv_ for i in f] for f in Fs]; RNAME += list(R); nv_ += len(V)
    VALL = np.vstack(VALL)
    REG = [r for r in pal_default["regions"] if r in set(RNAME)]
    missing = sorted(set(RNAME) - set(pal_default["regions"]))
    assert not missing, "palette %s lacks regions %s" % (unit, missing)
    rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)

    def make_mat(name):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes["Principled BSDF"]
        vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
        vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300); vg_.name = "glow"
        nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
        nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
        mat.use_backface_culling = True
        return mat
    MAT_BODY = make_mat(unit + "_body")
    low = new_obj(unit, VALL, FALL)
    me = low.data
    me.materials.append(MAT_BODY)
    PAL.store_regions(me, REG, rid, np.ones(len(FALL)))
    report["regions_faces"] = PAL.paint(me, pal_default)
    for s_ in low.material_slots:
        PAL.apply_material(s_.material, pal_default)
    me.update()
    fa_ = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa_)
    report["regions_area_share"] = {n_: round(float(fa_[rid == j].sum() / fa_.sum()), 4) for j, n_ in enumerate(REG)}
    me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})
    # facing: shell centroid (midline) -> the front-most midline point
    shellV = VALL[RANGE["shell"][0]:RANGE["shell"][1]]
    fcfg = RC.get("facing") or {}
    if fcfg.get("anchor") and fcfg.get("landmark"):
        anchor = np.array(fcfg["anchor"]) - SHIFT; landmark = np.array(fcfg["landmark"]) - SHIFT
        frule = fcfg.get("rule", "config anchor -> landmark")
    else:
        mid = np.abs(shellV[:, 0]) < 0.06
        anchor = np.array([0.0, float(shellV[:, 1].mean()), float(shellV[:, 2].mean())])
        j = np.argmin(np.where(mid, shellV[:, 1], np.inf))
        landmark = shellV[j].copy()
        frule = "shell centroid -> the front-most midline point of the shell (proxy finish default)"
    dvec = landmark - anchor
    report["facing"] = {"rule": frule, "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                        "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
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
    tris_main = len(FALL)
    report["tris"] = {"total": tris_main, "shell": int(len(CF)), **{p.name: p.tris() for p in PARTS}}
    open_e = {name: PP.open_edges(Fs) for name, V, Fs, R, p in ISL}
    report["open_edges"] = {"per_island": open_e, "total": int(sum(open_e.values())),
                            "rule": "every piece closed (marching-tets shell + closed parts)"}
    H = float(VALL[:, 2].max() - VALL[:, 2].min())
    lo_a, hi_a = VALL.min(0), VALL.max(0)
    fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
    k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
    report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                         "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                         "export_cell_fit_report_only": {"scale": round(k_fit, 5)}}
    bvh_main = BVHTree.FromPolygons(VALL.tolist(), FALL)
    mir2 = np.array([bvh_main.find_nearest(Vector(p * np.array([-1.0, 1.0, 1.0]) + np.array([2 * (lo_a[0] + hi_a[0]) / 2, 0, 0])))[3]
                     for p in VALL])
    report["finish"] = {"open_edges": report["open_edges"]["total"], "tris": tris_main,
                        "mirror": {"max_u": round(float(mir2.max()), 6), "p99_u": round(float(np.percentile(mir2, 99)), 6),
                                   "rule": "every vertex mirrored in X -> distance to the nearest surface of the unit"},
                        "tri_budget": TRI_BUDGET, "in_budget": TRI_BUDGET[0] <= tris_main <= TRI_BUDGET[1]}
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
    low["conquest_source"] = ("proxy finish: frozen Pixal3D sheet proxy %s (sha %s) finished by proxy_finish.py; sheet %s"
                              % (report["stage1"]["proxy"]["glb"], report["stage1"]["proxy"]["sha256"][:12],
                                 "design/reference/%s-character-sheet" % unit))
    low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
    low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
    # focus close-ups + the sheet axis in world X / Y
    sv = shellV
    head_pts = sv[sv[:, 2] > 0.72 * H]
    feet_pts = sv[sv[:, 2] < 0.16 * H]
    focus = {"head": [head_pts.min(0).tolist(), head_pts.max(0).tolist()],
             "feet": [feet_pts.min(0).tolist(), feet_pts.max(0).tolist()]}
    for nm, box in (RC.get("focus") or {}).items():
        focus[nm] = [(np.array(box[0]) - SHIFT).tolist(), (np.array(box[1]) - SHIFT).tolist()]
    groups = {}
    for p in PARTS:
        g_ = p.name.split(".")[0]
        a_, b_ = RANGE[p.name]
        groups.setdefault(g_, []).append(VALL[a_:b_])
    for g_, vs in list(groups.items())[:2]:
        P_ = np.vstack(vs)
        focus[g_] = [P_.min(0).tolist(), P_.max(0).tolist()]
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
        print("PREVIEW", json.dumps({k: report.get(k) for k in ("high", "low", "regions_low", "tris", "finish", "parts")}))
        sys.stdout.flush(); os._exit(0)

    # ================================================================================================ 7. bake (FLAT low)
    try:
        import addon_utils
        addon_utils.enable("cycles", default_set=False, persistent=False)
    except Exception:
        pass
    PH_V, PH_F = [HV - SHIFT], [HF]
    o_ = len(HV)
    for name, V, Fs, R, p in ISL[1:]:
        PH_V.append(V - SHIFT); PH_F.append(np.array(Fs) + o_); o_ += len(V)
    HIGH = new_obj(unit + "_high", np.vstack(PH_V), np.vstack(PH_F))
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.use_denoising = False
    scene.cycles.seed = 0
    bk = scene.render.bake
    bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
    bk.margin = 16; bk.use_clear = False
    RN, RA = cfg["bake_res"]
    img_n = bpy.data.images.new(unit + "_normal", RN, RN, alpha=False)
    img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
    img_ao = bpy.data.images.new(unit + "_ao", RA, RA, alpha=False)
    img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
    nt = MAT_BODY.node_tree
    tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
    low.visible_transmission = low.visible_volume_scatter = False
    me.shade_flat()                    # FLAT reference (BUILD_CONTRACT step 5): the map carries flat face -> smooth high
    tb = time.time()
    for o in scene.objects:
        o.select_set(o is HIGH or o is low)
    bpy.context.view_layer.objects.active = low
    bstats = {"reference": "flat"}
    for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
        nt.nodes.active = node
        scene.cycles.samples = samples
        t = time.time()
        r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
        bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
    px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
    pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
    cov_n = px[:, 2] > 0.25
    cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
    devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
    bstats.update({"cage_extrusion": BAKE_CAGE, "resolution": {"normal": RN, "ao": RA}, "high_tris": int(sum(len(f) for f in PH_F)),
                   "normal_coverage_pct": round(100 * float(cov_n.mean()), 2),
                   "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n] > 0.05).mean()), 4),
                   "ao_mean": round(float(pa[cov_a, 0].mean()), 4) if cov_a.any() else None})
    px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
    pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
    bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                           "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
    np.save(os.path.join(tempfile.gettempdir(), "%s_normal_%s.npy" % (unit, tag)), px[:, :3])
    np.save(os.path.join(tempfile.gettempdir(), "%s_ao_%s.npy" % (unit, tag)), pa[:, :1])
    bstats["seconds"] = round(time.time() - tb, 1)
    report["bake"] = bstats
    DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
    if not DIGEST_ONLY:
        os.makedirs(TEX_DIR, exist_ok=True)
        for img, nm in ((img_n, unit + "_normal.png"), (img_ao, unit + "_ao.png")):
            img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
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
        for img, nm in ((img_n, unit + "_normal.png"), (img_ao, unit + "_ao.png")):
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
    report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
    bpy.context.preferences.filepaths.save_version = 0
    if not DIGEST_ONLY:
        set_tex_paths("//textures/")
        bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
        json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1,
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

    rig_clips_export(unit, cfg, RC, report, DIG, low, me, VALL, RANGE, PARTS, sv, H, SHIFT, pal_default, set_tex_paths,
                     geometry_digest, OUT_IMPROVED, OUT_RIGGED, OUT_GLB, DIGEST_ONLY, T0)


def rig_clips_export(unit, cfg, RC, report, DIG, low, me, VALL, RANGE, PARTS, sv, H, SHIFT, pal_default, set_tex_paths,
                     geometry_digest, OUT_IMPROVED, OUT_RIGGED, OUT_GLB, DIGEST_ONLY, T0):
    """stages 8-9, shared by the full and the light finish: the rig from the config's joints (RC['rig'], stage-1 frame;
    SHIFT = the centring shift), weights, PLACEHOLDER clips, digest, rigged blend, glb, repaint proof. Exits the process."""
    scene = bpy.context.scene
    # ================================================================================================ 8. rig
    rep = {"unit": unit, "source": OUT_IMPROVED, "fps": K.FPS, "finish": report["finish"]}
    scene.render.fps = K.FPS; scene.render.fps_base = 1.0
    rcfg = RC.get("rig") or {}
    spine = rcfg.get("spine")
    if not spine:                                     # auto: shell slab centroids from 18 % to 96 % of the height
        zs = np.linspace(0.18, 0.96, 4) * H
        spine = []
        for z_ in zs:
            sl = sv[np.abs(sv[:, 2] - z_) < 0.06 * H]
            spine.append([0.0, float(sl[:, 1].mean()) if len(sl) else 0.0, float(z_)])
        spine = [list(np.array(p) + SHIFT) for p in spine]
    SPN = [np.array(p, float) - SHIFT for p in spine]
    snames = rcfg.get("spine_names") or ["spine.%d" % k for k in range(len(SPN) - 1)]
    BONES = []                                        # (name, head, tail, parent, kind, side)
    for k in range(len(SPN) - 1):
        BONES.append((snames[k], SPN[k], SPN[k + 1], "root" if k == 0 else snames[k - 1], "spine", "M"))
    for ch in rcfg.get("chains") or []:
        pts = [np.array(p, float) - SHIFT for p in ch["points"]]
        sides = ("L", "R") if ch.get("mirror", True) else ("M",)
        for s in sides:
            P_ = [p * np.array([-1.0, 1.0, 1.0]) if s == "R" else p for p in pts]
            base = ch["name"] + ("" if s == "M" else "." + s)
            for k in range(len(P_) - 1):
                par = ch.get("parent", snames[0]) if k == 0 else "%s.%d" % (base, k - 1)
                BONES.append(("%s.%d" % (base, k), P_[k], P_[k + 1], par, ch.get("kind", "chain"), s))
    arm_data = bpy.data.armatures.new(unit + "_rig")
    rig = bpy.data.objects.new(unit + "_rig", arm_data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    for o in scene.objects:
        o.select_set(o is rig)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.4); eb.use_deform = False; eb.roll = 0.0
    for (nm, h_, t_, p_, kind, s) in BONES:
        e = arm_data.edit_bones.new(nm)
        e.head = Vector(h_); e.tail = Vector(t_); e.roll = 0.0
        e.parent = arm_data.edit_bones[p_]
        e.use_connect = False
        e.use_deform = True
    bpy.ops.object.mode_set(mode="OBJECT")
    DEFORM = [b[0] for b in BONES]
    J = {n_: j for j, n_ in enumerate(DEFORM)}
    KIND = {b[0]: b[4] for b in BONES}
    SIDE = {b[0]: b[5] for b in BONES}

    def seg_d(P_, a, b):
        ab = b - a; ap = P_ - a
        h = np.clip((ap @ ab) / max(float(ab @ ab), 1e-12), 0, 1)
        return np.linalg.norm(ap - h[:, None] * ab, axis=1)
    Wt = np.zeros((len(VALL), len(DEFORM)))
    a_, b_ = RANGE["shell"]
    Ps = VALL[a_:b_]
    D = np.stack([seg_d(Ps, b[1], b[2]) for b in BONES], 1)
    for j, b in enumerate(BONES):                     # side-aware: a .L bone never pulls the -X half (and .R the +X)
        if b[5] == "L":
            D[Ps[:, 0] < -0.02 * H, j] = np.inf
        elif b[5] == "R":
            D[Ps[:, 0] > 0.02 * H, j] = np.inf
    dmin = D.min(1, keepdims=True)
    sig = float(rcfg.get("falloff", 0.06)) * H
    Wt[a_:b_] = np.exp(-((D - dmin) / sig) ** 2)
    for p in PARTS:
        a_, b_ = RANGE[p.name]
        bn = p.bone
        if bn not in J:
            c_ = VALL[a_:b_].mean(0)
            dd = [seg_d(c_[None], b[1], b[2])[0] + (np.inf if (b[5] == "L" and c_[0] < -0.02 * H) or
                                                    (b[5] == "R" and c_[0] > 0.02 * H) else 0.0) for b in BONES]
            bn = DEFORM[int(np.argmin(dd))]
            p.bone = bn
        Wt[a_:b_, J[bn]] = 1.0
    Wt = np.where(Wt > 1e-3, Wt, 0.0)
    if (Wt > 0).sum(1).max() > 4:
        idx_ = np.argsort(-Wt, 1, kind="stable")[:, 4:]
        np.put_along_axis(Wt, idx_, 0.0, 1)
    Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
    low.vertex_groups.clear()
    for j, n_ in enumerate(DEFORM):
        vg_ = low.vertex_groups.new(name=n_)
        for i in np.nonzero(Wt[:, j] > 0)[0]:
            vg_.add([int(i)], float(Wt[i, j]), "REPLACE")
    infl = (Wt > 0).sum(1)
    DIG["weights"] = sha(Wt)
    rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                      "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                      "per_bone_dominant": {n_: int((np.argmax(Wt, 1) == j).sum()) for j, n_ in enumerate(DEFORM)},
                      "parts": {p.name: p.bone for p in PARTS},
                      "rule": "shell: exp(-((d - d_min) / (%.2f H))^2) over bone-segment distances, side-aware, <= 4, "
                              "normalised; parts rigid on their bone" % float(rcfg.get("falloff", 0.06))}
    low.parent = rig
    low.matrix_parent_inverse = Matrix.Identity(4)
    am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

    # ---- PLACEHOLDER clips (the artist has not given the movement intent)
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
    REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
    AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
    TAU = 2.0 * math.pi
    ccl = cfg.get("clips") or {}
    IDLE_N = int(ccl.get("idle_frames", 48)); WALK_N = int(ccl.get("walk_frames", 24))
    BOB = float(ccl.get("walk_bob", 0.012)); ROLL = float(ccl.get("walk_roll", 3.0))
    SWING = float(ccl.get("leg_swing", 18.0)); ARM = float(ccl.get("arm_swing", 10.0))
    BREATHE = float(ccl.get("breathe_deg", 1.2))
    PHASE = {}
    for ch in rcfg.get("chains") or []:
        for s in (("L", "R") if ch.get("mirror", True) else ("M",)):
            base = ch["name"] + ("" if s == "M" else "." + s)
            ph = ch.get("phase", {"L": 0.0, "R": 0.5, "M": 0.0})
            PHASE[base] = float(ph.get(s, 0.0) if isinstance(ph, dict) else ph)

    def local_loc(bone, dw):
        return Vector(REST[bone].T @ np.asarray(dw, float))

    def local_quat(bone, rots):
        Rw = Matrix.Identity(3)
        for axis, deg in rots:
            Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
        R0 = Matrix(REST[bone].tolist())
        q = (R0.transposed() @ Rw @ R0).to_quaternion()
        q.normalize()
        if q.w < 0:
            q.negate()
        return q

    def pose_at(clip, f):
        N = IDLE_N if clip == "idle" else WALK_N
        u = f / N
        P_ = {}
        nsp = len(snames)
        for k, bn in enumerate(snames):
            if clip == "idle":
                a = BREATHE * (1.6 if k == nsp - 1 else 1.0)
                P_[bn] = (None, [("X", a * math.sin(TAU * u - 0.5 * k))], None)
            else:
                rots = [("Y", (ROLL if k == 0 else -0.4 * ROLL / max(nsp - 1, 1)) * math.sin(TAU * u))]
                loc = (0.0, 0.0, BOB * H * (0.5 - 0.5 * math.cos(TAU * 2 * u))) if k == 0 else None
                P_[bn] = (loc, rots, None)
        for (bn, h_, t_, par, kind, s) in BONES[len(snames):]:
            base, kk = bn.rsplit(".", 1)
            kk = int(kk)
            ph = TAU * PHASE.get(base, 0.0)
            sg = -1.0 if s == "R" else 1.0
            nb = sum(1 for b in BONES if b[0].rsplit(".", 1)[0] == base)
            if clip == "idle":
                amp = {"tail": (2.0, 6.0, "Z"), "wing": (2.0, 4.0, "X"), "ear": (2.0, 5.0, "X"), "arm": (1.5, 3.0, "X"),
                       "leg": (0.0, 0.0, "X")}.get(kind, (1.0, 3.0, "X"))
                ang = K.vine_wave(u, kk, nb, amp[0], amp[1], 0.6, 1 if kind != "ear" else 2, ph)
                P_[bn] = (None, [(amp[2], ang * (sg if amp[2] == "Z" else 1.0))], None)
            else:
                if kind == "leg":
                    ang = SWING * math.sin(TAU * u + ph) if kk == 0 else 0.5 * SWING * max(0.0, math.sin(TAU * u + ph + 0.8))
                    P_[bn] = (None, [("X", ang)], None)
                elif kind == "arm":
                    P_[bn] = (None, [("X", -ARM * math.sin(TAU * u + ph) * (1.0 if kk == 0 else 0.4))], None)
                elif kind == "tail":
                    P_[bn] = (None, [("Z", K.vine_wave(u, kk, nb, 3.0, 8.0, 0.7, 1, ph))], None)
                elif kind == "wing":
                    P_[bn] = (None, [("X", K.vine_wave(u, kk, nb, 2.0, 4.0, 0.6, 1, ph))], None)
                else:
                    P_[bn] = (None, [("X", K.vine_wave(u, kk, nb, 1.5, 3.0, 0.6, 2, ph))], None)
        return P_
    CLIPS = {"idle": IDLE_N, "walk": WALK_N}
    NEW_ACTS = {}
    key_rows = []
    for cn, N in CLIPS.items():
        act = bpy.data.actions.new(cn)
        act.use_fake_user = True
        K.assign_action(rig, act)
        for f in range(N + 1):
            P_ = pose_at(cn, f % N)
            for bn in DEFORM:
                loc, rots, sc_ = P_.get(bn, (None, [], None))
                pb = rig.pose.bones[bn]
                pb.location = local_loc(bn, loc) if loc is not None else Vector((0.0, 0.0, 0.0))
                pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
                pb.scale = sc_ if sc_ is not None else (1.0, 1.0, 1.0)
                for chn in ("location", "rotation_quaternion", "scale"):
                    pb.keyframe_insert(chn, frame=f + 1)
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
        dg_ = bpy.context.evaluated_depsgraph_get()
        ev_ = ob.evaluated_get(dg_)
        m_ = ev_.to_mesh()
        co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
        ev_.to_mesh_clear()
        M_ = np.array(ob.matrix_world)
        return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]
    samples = []
    clip_rep = {}
    for cn, N in CLIPS.items():
        K.assign_action(rig, NEW_ACTS[cn])
        first = last = None
        minz = []
        for f in range(1, N + 2):
            scene.frame_set(f)
            C = eval_coords(low)
            samples.append(C[::11])
            minz.append(float(C[:, 2].min()))
            if f == 1:
                first = C
            if f == N + 1:
                last = C
        clip_rep[cn] = {"frames": [1, N + 1], "seconds": round(N / K.FPS, 3), "cyclic": True,
                        "seam_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                        "clearance_min_z": round(min(minz), 4)}
    DIG["clip_samples"] = sha(np.concatenate(samples))
    rep["clips"] = clip_rep
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
    scene.frame_set(1)
    scene.frame_start, scene.frame_end = 1, IDLE_N + 1
    rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                     "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]}
                    for b in arm_data.bones]
    rep["bone_count"] = len(arm_data.bones)
    rig["conquest_rig"] = "proxy finish: root > %s; chains %s" % (" > ".join(snames),
                                                                  ", ".join(sorted({b[0].rsplit(".", 1)[0] for b in BONES[len(snames):]})))
    low["conquest_clips"] = ["idle", "walk"]
    low["conquest_clip_status"] = ("PLACEHOLDER (movement intent not given by the artist): idle = %d-frame breathe + "
                                   "chain sway; walk = %d-frame in-place bob / roll + leg / arm / tail swing"
                                   % (IDLE_N, WALK_N))
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

    # ================================================================================================ 9. glb + repaint proof
    for o in scene.objects:
        o.select_set(o in (rig, low))
    bpy.context.view_layer.objects.active = rig
    K.assign_action(rig, NEW_ACTS["idle"])
    t = time.time()
    import export_glb as _EG
    _EG.add_glow_attr([low])
    bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                              export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                              export_skins=True, export_def_bones=False, export_attributes=True)
    rig.animation_data.action = None
    rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1)}
    geo0 = geometry_digest([low])
    PAL.paint(me, pal_default)
    rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default)},
                    "repaint_proof": {"default_digest_before": geo0, "after_repaint": geometry_digest([low])}}
    rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
    rep["seconds"] = round(time.time() - T0, 1)
    json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds", "clips")}))
    sys.stdout.flush()
    os._exit(0)
