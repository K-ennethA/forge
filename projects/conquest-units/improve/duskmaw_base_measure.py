"""Duskmaw base / tendril / arm / neck measurement (read-only; opens blends, never saves).

    blender --background --factory-startup --python duskmaw_base_measure.py -- <out.json> <label>=<blend> [...]

v2 feedback 2 ("the bottom should almost be uniform like a shadow coming out of the ground") and v3 ("we also lost the
shadowy extensions on the bottom", "the arms look like they got too skinny around the mid part", "a skinnier neck"),
measured at the rest pose about the model's XY origin (the contract's bbox centre), in 72 bins of 5 degrees round the
vertical axis. Points = every mesh edge sampled at 9 points (vertices alone leave long decimated floor edges' bins empty).
  floor_ring_radius   per bin, the outermost point radius among points below FLOOR_Z (where the body meets the floor);
                      reported as mean / min / max and CV = std / mean (0 = a perfect circle).
  floor_contact_bins  % of bins with any point below CONTACT_Z (the body reaches the floor all the way round).
  rim_z_std           per bin, the height of the outermost point below RIM_ZMAX (the skirt's reach); std over bins.
  tendrils            the hem's zigzag as flames standing proud of the skirt, in 1-degree bins of the outermost
                      radius at each height z (0.1 steps from TEND_Z0 to TEND_ZMAX): a flame is an angular interval
                      between a rising and a falling EDGE (radius step >= TEND_STEP across 2 degrees; the body's smooth
                      lobes never step that sharply), at most TEND_MAXW degrees wide. Flames found at TEND_Z0 (they rise
                      out of the ground) are tracked upward through overlapping intervals; a flame's height = the
                      highest z it is still found at. Reported: count (flames reaching TEND_Z0 + TEND_MIN_H), heights.
  mid_arm_radius      the hanging arms' mid/elbow segment: points with |x| in ARM_X (the torso side .. the hand),
                      heights ARM_Z (below the upper arms), |y| < 3.5, per side; the arm axis = the line through the
                      segment's 0.2-wide x-slice centroids; radius = median point distance to that axis.
  neck_min_width      the thinnest x-extent of the neck column (|x| < 3.2) over the heights NECK_Z (0.1 slices).
Hands excluded from the base metrics (radius > 7.4 within 3.2 of the side plane above z 3.8 -- the hanging lower arms).
"""
import bpy, sys, json, math
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
FLOOR_Z, CONTACT_Z, RIM_ZMAX, NB = 0.5, 0.05, 6.0, 72
TEND_Z0, TEND_ZMAX, TEND_STEP, TEND_MAXW, TEND_MIN_H = 1.0, 6.0, 0.35, 30, 0.5
ARM_X = (7.6, 9.3)
ARM_Z = (5.0, 14.0)
NECK_Z = (15.0, 23.5)
res = {"rule": __doc__.strip().split("\n\n")[1], "blends": {}}
for label, path in (a.split("=", 1) for a in argv[1:]):
    bpy.ops.wm.open_mainfile(filepath=path)
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            o.data.pose_position = "REST"
    bpy.context.view_layer.update()
    ob = max((o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render), key=lambda o: len(o.data.polygons))
    V0 = np.empty(len(ob.data.vertices) * 3); ob.data.vertices.foreach_get("co", V0); V0 = V0.reshape(-1, 3)
    M = np.array(ob.matrix_world); V0 = V0 @ M[:3, :3].T + M[:3, 3]
    # dense sampling along every edge (decimated meshes have long floor edges: vertices alone leave empty 5-degree bins)
    E = np.empty(len(ob.data.edges) * 2, dtype=np.int64); ob.data.edges.foreach_get("vertices", E); E = E.reshape(-1, 2)
    V = np.concatenate([V0[E[:, 0]] * (1 - t) + V0[E[:, 1]] * t for t in np.linspace(0.0, 1.0, 9)])
    r = np.hypot(V[:, 0], V[:, 1])
    hand = (r > 7.4) & (np.abs(V[:, 1]) < 3.2) & (V[:, 2] > 3.8)
    th = np.arctan2(V[:, 0], -V[:, 1])
    b = ((th + math.pi) / (2 * math.pi) * NB).astype(int) % NB
    ring, contact, rimz = [], 0, []
    for k in range(NB):
        m = (b == k) & ~hand
        fl = m & (V[:, 2] < FLOOR_Z)
        ring.append(float(r[fl].max()) if fl.any() else 0.0)
        contact += bool((m & (V[:, 2] < CONTACT_Z)).any())
        rm = m & (V[:, 2] < RIM_ZMAX)
        if rm.any():
            i = np.nonzero(rm)[0][np.argmax(r[rm])]
            rimz.append(float(V[i, 2]))
    ring = np.array(ring)
    # ---- tendrils: flames tracked upward from TEND_Z0
    b1 = ((th + math.pi) / (2 * math.pi) * 360).astype(int) % 360
    sel = ~hand & (V[:, 2] < TEND_ZMAX + 0.1)

    def flames_at(z_):
        m = sel.copy(); m[sel] = np.abs(V[sel, 2] - z_) < 0.06
        out = np.full(360, -np.inf)
        np.maximum.at(out, b1[m], r[m])
        ok = np.isfinite(out)
        if ok.sum() < 30:
            return []
        idx = np.arange(360)
        out = np.interp(idx, idx[ok], out[ok], period=360)
        d = np.roll(out, -1) - np.roll(out, 1)                 # radius step across 2 degrees
        ups = [i for i in range(360) if d[i] >= TEND_STEP and d[i - 1] < TEND_STEP]
        downs = [i for i in range(360) if d[i] <= -TEND_STEP and d[(i + 1) % 360] > -TEND_STEP]
        iv = []
        for u in ups:
            nxt = [(dn - u) % 360 for dn in downs if 0 < (dn - u) % 360 <= TEND_MAXW]
            if nxt:
                iv.append((u, u + min(nxt)))                   # [start, end] in unwrapped degrees
        return iv

    def overlap(a, b):
        for s_ in (-360, 0, 360):
            if a[0] <= b[1] + s_ and b[0] + s_ <= a[1]:
                return True
        return False
    zs = np.arange(TEND_Z0, TEND_ZMAX + 1e-9, 0.1)
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
    ph = [tk["top"] for tk in real]
    Ht = float(V[:, 2].max() - V[:, 2].min())
    tend = {"count": len(real), "flames_found_at_z0": len(tracks),
            "height_mean": round(float(np.mean(ph)), 3) if ph else 0.0, "height_max": round(float(np.max(ph)), 3) if ph else 0.0,
            "height_min": round(float(np.min(ph)), 3) if ph else 0.0,
            "height_mean_pct_H": round(100 * float(np.mean(ph)) / Ht, 2) if ph else 0.0,
            "angles_deg_at_z0": [int(round(0.5 * (tk["start"][0] + tk["start"][1]))) % 360 - 180 for tk in real],
            "heights": [round(v, 2) for v in ph]}
    # ---- mid-arm radius (hanging arms' mid / elbow segment), per side
    arm = {}
    for s_, sg in (("L", 1), ("R", -1)):
        m = (sg * V[:, 0] > ARM_X[0]) & (sg * V[:, 0] < ARM_X[1]) & (V[:, 2] > ARM_Z[0]) & (V[:, 2] < ARM_Z[1]) & (np.abs(V[:, 1]) < 3.5)
        P = V[m]
        if len(P) < 20:
            continue
        # the arm axis: a line through the x-slice centroids (0.2 slices; robust for a short thick segment where a
        # PCA of the slab can flip to the slab's own long direction)
        xs_ = np.arange(ARM_X[0] + 0.1, ARM_X[1], 0.2)
        C_ = np.array([P[np.abs(sg * P[:, 0] - x_) < 0.1].mean(0) for x_ in xs_ if (np.abs(sg * P[:, 0] - x_) < 0.1).sum() >= 6])
        c = C_.mean(0)
        a = np.linalg.svd(C_ - c, full_matrices=False)[2][0]
        d = np.linalg.norm((P - c) - np.outer((P - c) @ a, a), axis=1)
        arm[s_] = {"radius_median": round(float(np.median(d)), 4), "points": int(len(P)), "axis_dir": a.round(3).tolist(),
                   "centre": c.round(3).tolist()}
    # ---- neck
    widths = []
    for z_ in np.arange(NECK_Z[0], NECK_Z[1] + 1e-9, 0.1):
        m = (np.abs(V0[:, 2] - z_) < 0.12) & (np.abs(V0[:, 0]) < 3.2)
        if m.sum() >= 6:
            widths.append((float(np.ptp(V0[m, 0])), float(z_)))
    nk = min(widths) if widths else (0.0, 0.0)
    row = {"file": path, "height": round(Ht, 4),
           "floor_ring_radius": {"mean": round(float(ring.mean()), 4), "min": round(float(ring.min()), 4),
                                 "max": round(float(ring.max()), 4), "cv": round(float(ring.std() / max(ring.mean(), 1e-9)), 4)},
           "floor_contact_bins_pct": round(100.0 * contact / NB, 2),
           "rim_z": {"mean": round(float(np.mean(rimz)), 4), "std": round(float(np.std(rimz)), 4),
                     "min": round(float(np.min(rimz)), 4), "max": round(float(np.max(rimz)), 4)},
           "tendrils": tend, "mid_arm_radius": arm,
           "neck_min_width": {"width": round(nk[0], 4), "at_z": round(nk[1], 2), "pct_of_height": round(100 * nk[0] / Ht, 2)}}
    res["blends"][label] = row
    print("BASE", label, json.dumps(row))
json.dump(res, open(OUT, "w"), indent=1)
print("DONE")
