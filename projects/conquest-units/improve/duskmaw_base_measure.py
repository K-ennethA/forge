"""Duskmaw base / tendril / arm / neck / trunk measurement (read-only; opens blends, never saves).

    blender --background --factory-startup --python duskmaw_base_measure.py -- <out.json> <label>=<blend> [...]

Measured at the rest pose about the model's XY origin (the contract's bbox centre), in 72 bins of 5 degrees round the
vertical axis. Points = every mesh edge sampled at 9 points (vertices alone leave long decimated floor edges' bins empty).
  floor_ring_radius   the CORE floor ring: per bin, the outermost point radius among points below FLOOR_Z, over the bins
                      no floor tendril crosses (no floor point beyond RING_MAX there); mean / min / max, CV = std / mean.
  floor_contact_bins  % of bins with any point below CONTACT_Z (the body reaches the floor all the way round).
  up_spikes           v3's hem zigzag detector (flames standing proud of the skirt, tracked upward from TEND_Z0 in
                      1-degree bins of the outermost radius; a flame = a rising + a falling radius EDGE >= TEND_STEP across
                      2 degrees, at most TEND_MAXW degrees wide): v4's tendrils lie on the floor, so this should find ~none.
  floor_tendrils      v4: connected pieces of the mesh beyond RING_MAX (radius) below TEND_ZMAX (the floor tendrils cut off
                      at the core ring; pieces under MIN_PIECE vertices ignored): count, reach radius (outermost point),
                      length beyond RING_MAX, max height (the curled-up tips), angle of each piece's root.
  arms                v4 (blend property conquest_arm_L/R = root, shoulder, elbow, wrist): median point distance to the
                      segment axis in 0.8-long slabs at the upper arm's middle, just below the elbow, and near the wrist.
                      Blends without it (v3): v3's hanging-arm segment (|x| in ARM_X, z in ARM_Z) -- the mid/elbow radius.
  trunk_width         the front-view trunk width (the central contiguous x-interval of an exact horizontal plane slice, arms
                      and tendrils fall off it as separate intervals) at height fractions of H; + the side-view depth
                      (same in y); + the narrowest width between 0.15 H and 0.45 H (the waist).
  neck_min_width      the thinnest x-extent of the neck column (|x| < 3.2) over the heights NECK_Z (0.1 slices).
"""
import bpy, sys, json, math
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
FLOOR_Z, CONTACT_Z, RIM_ZMAX, NB = 0.5, 0.05, 6.0, 72
TEND_Z0, TEND_ZMAX_SPK, TEND_STEP, TEND_MAXW, TEND_MIN_H = 1.0, 6.0, 0.35, 30, 0.5
RING_MAX, TEND_ZMAX, MIN_PIECE = 10.0, 3.3, 30
ARM_X = (7.6, 9.3)
ARM_Z = (5.0, 14.0)
NECK_Z = (15.0, 32.0)
H_FRACS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
res = {"rule": __doc__.strip().split("\n\n")[1], "blends": {}}


def plane_segments(V, T, z0):
    """exact slice of the triangle mesh (V, T) by the plane z = z0: the (n, 2, 3) segments"""
    d = V[T, 2] - z0                                        # (m, 3) signed heights of each triangle's corners
    s = np.sign(d); s[s == 0] = 1e-9
    m = (s.min(1) < 0) & (s.max(1) > 0)
    segs = []
    for tri, dd in zip(T[m], d[m]):
        P = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if (dd[i] < 0) != (dd[j] < 0):
                t_ = dd[i] / (dd[i] - dd[j])
                P.append(V[tri[i]] + (V[tri[j]] - V[tri[i]]) * t_)
        if len(P) == 2:
            segs.append(P)
    return np.array(segs).reshape(-1, 2, 3)


def central_interval(segs, k, gap=0.3):
    """the contiguous interval around 0 (axis k: 0 = x, 1 = y) of the union of the slice segments' extents; a gap wider
    than `gap` ends it (arms / tendrils / wisps fall off as separate intervals)"""
    if len(segs) == 0:
        return 0.0, 0.0
    iv = sorted(zip(segs[:, :, k].min(1), segs[:, :, k].max(1)))
    merged = []
    for a, b in iv:
        if merged and a <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in merged:
        if a <= 0.0 <= b:
            return a, b
    return min(merged, key=lambda q: min(abs(q[0]), abs(q[1])))


for label, path in (a.split("=", 1) for a in argv[1:]):
    bpy.ops.wm.open_mainfile(filepath=path)
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            o.data.pose_position = "REST"
    bpy.context.view_layer.update()
    ob = max((o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render), key=lambda o: len(o.data.polygons))
    V0 = np.empty(len(ob.data.vertices) * 3); ob.data.vertices.foreach_get("co", V0); V0 = V0.reshape(-1, 3)
    M = np.array(ob.matrix_world); V0 = V0 @ M[:3, :3].T + M[:3, 3]
    E = np.empty(len(ob.data.edges) * 2, dtype=np.int64); ob.data.edges.foreach_get("vertices", E); E = E.reshape(-1, 2)
    V = np.concatenate([V0[E[:, 0]] * (1 - t) + V0[E[:, 1]] * t for t in np.linspace(0.0, 1.0, 9)])
    Ht = float(V[:, 2].max() - V[:, 2].min())
    r = np.hypot(V[:, 0], V[:, 1])
    hand = (r > 7.4) & (np.abs(V[:, 1]) < 3.2) & (V[:, 2] > 3.8)
    th = np.arctan2(V[:, 0], -V[:, 1])
    b = ((th + math.pi) / (2 * math.pi) * NB).astype(int) % NB
    ring, contact, rimz, tbins = [], 0, [], 0
    for k in range(NB):
        m = (b == k) & ~hand
        fl = m & (V[:, 2] < FLOOR_Z)
        if fl.any() and r[fl].max() >= RING_MAX - 0.05:
            tbins += 1                                          # a floor tendril crosses this bin: not the core ring
        elif fl.any():
            ring.append(float(r[fl].max()))
        contact += bool((m & (V[:, 2] < CONTACT_Z)).any())
        rm = m & (V[:, 2] < RIM_ZMAX)
        if rm.any():
            i = np.nonzero(rm)[0][np.argmax(r[rm])]
            rimz.append(float(V[i, 2]))
    ring = np.array(ring) if ring else np.zeros(1)
    # ---- v3's up-spike (hem flame) detector
    b1 = ((th + math.pi) / (2 * math.pi) * 360).astype(int) % 360
    sel = ~hand & (V[:, 2] < TEND_ZMAX_SPK + 0.1)

    def flames_at(z_):
        m = sel.copy(); m[sel] = np.abs(V[sel, 2] - z_) < 0.06
        out = np.full(360, -np.inf)
        np.maximum.at(out, b1[m], r[m])
        ok = np.isfinite(out)
        if ok.sum() < 30:
            return []
        idx = np.arange(360)
        out = np.interp(idx, idx[ok], out[ok], period=360)
        d = np.roll(out, -1) - np.roll(out, 1)
        ups = [i for i in range(360) if d[i] >= TEND_STEP and d[i - 1] < TEND_STEP]
        downs = [i for i in range(360) if d[i] <= -TEND_STEP and d[(i + 1) % 360] > -TEND_STEP]
        iv = []
        for u in ups:
            nxt = [(dn - u) % 360 for dn in downs if 0 < (dn - u) % 360 <= TEND_MAXW]
            if nxt:
                iv.append((u, u + min(nxt)))
        return iv

    def overlap(a, b_):
        for s_ in (-360, 0, 360):
            if a[0] <= b_[1] + s_ and b_[0] + s_ <= a[1]:
                return True
        return False
    zs = np.arange(TEND_Z0, TEND_ZMAX_SPK + 1e-9, 0.1)
    tracks = [{"iv": iv, "start": iv, "top": float(TEND_Z0)} for iv in flames_at(TEND_Z0)]
    for z_ in zs[1:]:
        cur = flames_at(z_)
        for tk in tracks:
            if tk.get("dead"):
                continue
            hit = [iv for iv in cur if overlap(tk["iv"], iv)]
            if hit:
                tk["iv"] = hit[0]; tk["top"] = float(z_)
            else:
                tk["dead"] = True
    real = [tk for tk in tracks if tk["top"] >= TEND_Z0 + TEND_MIN_H]
    spikes = {"count": len(real), "heights": [round(tk["top"], 2) for tk in real]}
    # ---- v4 floor tendrils: connected pieces beyond the core ring, near the floor (mesh vertices + edges)
    rv = np.hypot(V0[:, 0], V0[:, 1])
    out_v = (rv > RING_MAX) & (V0[:, 2] < TEND_ZMAX)
    par = np.arange(len(V0))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for a, c in E:
        if out_v[a] and out_v[c]:
            ra, rc_ = find(a), find(c)
            if ra != rc_:
                par[ra] = rc_
    ids = np.nonzero(out_v)[0]
    roots = np.array([find(i) for i in ids]) if len(ids) else np.zeros(0, int)
    pieces = []
    for rt in np.unique(roots):
        pv = ids[roots == rt]
        if len(pv) < MIN_PIECE:
            continue
        P = V0[pv]; rp = rv[pv]
        root = P[np.argmin(rp)]
        pieces.append({"verts": int(len(pv)), "reach_radius": round(float(rp.max()), 3), "beyond_ring_max": round(float(rp.max() - RING_MAX), 3),
                       "max_height": round(float(P[:, 2].max()), 3),
                       "root_angle_deg": round(math.degrees(math.atan2(root[0], -root[1])), 1)})
    pieces.sort(key=lambda q: q["root_angle_deg"])
    ftend = {"count": len(pieces), "reach_radius_min_max": [min((q["reach_radius"] for q in pieces), default=0.0),
                                                           max((q["reach_radius"] for q in pieces), default=0.0)],
             "max_height_max": max((q["max_height"] for q in pieces), default=0.0), "pieces": pieces}
    # ---- arms
    arm = {}
    if "conquest_arm_L" in ob.keys():
        for s_ in ("L", "R"):
            Pk = np.array(ob["conquest_arm_" + s_]).reshape(-1, 3)
            rows = {}
            for nm, (i0, i1, tt) in {"upper_arm_mid": (1, 2, 0.5), "forearm_below_elbow": (2, 3, 0.15), "forearm_near_wrist": (2, 3, 0.85)}.items():
                a, c = Pk[i0], Pk[i1]
                ax = (c - a) / np.linalg.norm(c - a)
                p0 = a + (c - a) * tt
                q = V - p0
                along = q @ ax
                perp = np.linalg.norm(q - np.outer(along, ax), axis=1)
                m = (np.abs(along) < 0.4) & (perp < 2.2)
                rows[nm] = round(float(np.median(perp[m])), 4) if m.sum() > 20 else None
            arm[s_] = rows
    else:
        for s_, sg in (("L", 1), ("R", -1)):
            m = (sg * V[:, 0] > ARM_X[0]) & (sg * V[:, 0] < ARM_X[1]) & (V[:, 2] > ARM_Z[0]) & (V[:, 2] < ARM_Z[1]) & (np.abs(V[:, 1]) < 3.5)
            P = V[m]
            if len(P) < 20:
                continue
            xs_ = np.arange(ARM_X[0] + 0.1, ARM_X[1], 0.2)
            C_ = np.array([P[np.abs(sg * P[:, 0] - x_) < 0.1].mean(0) for x_ in xs_ if (np.abs(sg * P[:, 0] - x_) < 0.1).sum() >= 6])
            c = C_.mean(0)
            a = np.linalg.svd(C_ - c, full_matrices=False)[2][0]
            d = np.linalg.norm((P - c) - np.outer((P - c) @ a, a), axis=1)
            arm[s_] = {"mid_elbow_segment": round(float(np.median(d)), 4)}
    # ---- trunk width / depth profile
    zmin = float(V[:, 2].min())
    trunk = {}
    me_ = ob.data
    me_.calc_loop_triangles()
    TRI = np.empty(len(me_.loop_triangles) * 3, dtype=np.int64); me_.loop_triangles.foreach_get("vertices", TRI); TRI = TRI.reshape(-1, 3)
    for fr in H_FRACS:
        z0 = zmin + fr * Ht
        sg_ = plane_segments(V0, TRI, z0)
        xa, xb = central_interval(sg_, 0); ya, yb = central_interval(sg_, 1)
        trunk["%.2f" % fr] = {"z": round(z0, 3), "width": round(xb - xa, 3), "depth": round(yb - ya, 3)}
    ws = []
    for z0 in np.arange(zmin + 0.15 * Ht, zmin + 0.45 * Ht, 0.1):
        xa, xb = central_interval(plane_segments(V0, TRI, z0), 0)
        ws.append((xb - xa, z0))
    wmin = min(ws)
    # ---- neck
    widths = []
    for z_ in np.arange(NECK_Z[0], NECK_Z[1] + 1e-9, 0.1):
        m = (np.abs(V0[:, 2] - z_) < 0.12) & (np.abs(V0[:, 0]) < 3.2)
        if m.sum() >= 6:
            widths.append((float(np.ptp(V0[m, 0])), float(z_)))
    nk = min(widths) if widths else (0.0, 0.0)
    row = {"file": path, "height": round(Ht, 4),
           "floor_ring_radius": {"mean": round(float(ring.mean()), 4), "min": round(float(ring.min()), 4), "max": round(float(ring.max()), 4),
                                 "cv": round(float(ring.std() / max(ring.mean(), 1e-9)), 4), "bins_used": int(len(ring)), "tendril_bins": tbins},
           "floor_contact_bins_pct": round(100.0 * contact / NB, 2),
           "rim_z": {"mean": round(float(np.mean(rimz)), 4), "std": round(float(np.std(rimz)), 4),
                     "min": round(float(np.min(rimz)), 4), "max": round(float(np.max(rimz)), 4)},
           "up_spikes": spikes, "floor_tendrils": ftend, "arms": arm,
           "trunk_width_by_height_frac": trunk,
           "waist_min_width_0.15-0.45H": {"width": round(wmin[0], 3), "at_z": round(wmin[1], 2), "pct_of_height": round(100 * wmin[0] / Ht, 2)},
           "neck_min_width": {"width": round(nk[0], 4), "at_z": round(nk[1], 2), "pct_of_height": round(100 * nk[0] / Ht, 2)}}
    res["blends"][label] = row
    print("BASE", label, json.dumps(row))
json.dump(res, open(OUT, "w"), indent=1)
print("DONE")
