"""Proxy finish REPOSE (pure numpy; proxy_finish_light.py calls it inside Blender, the venv can test it): the sheet's
POSE (a tail curled to one side, a head turned) is undone with BONES, never by reshaping geometry (artist 2026-10-11, on
the generate-and-finish panels: "is it symmetric just posed? can we move it to be symmetric?").

    1. the rig's joints (config, stage-1 frame; a chain may say {"auto": {...}}: slab centroids tracked along the mesh)
    2. skin: every vertex weighted over the bone segments (the contract rig's rule: exp(-((d - d_min) / (falloff H))^2),
       side-aware, <= 4, normalised); `rigid` boxes pin a region to one bone (the face: weight 1, ramped out over a
       margin), so the face moves as a RIGID body and is never deformed
    3. pose: each target chain's bones get a world YAW (about +Z) that turns their horizontal heading to the target
       (+y = straight back for a tail, -y = straight forward for a head); a bone shorter than min_horizontal of its
       length horizontally inherits its parent's yaw. Yaw about Z keeps every z: floor contact and height are untouched.
       `lift_to_floor`: if the straightened chain dips below the floor (a tail curl that lay lower than the feet), the
       whole chain pitches up about its root by the smallest angle that clears z = 0 (bisection).
    4. APPLY: linear blend skinning of the low's vertices = the new rest mesh (bake the deformation); the rig's joints
       move with their bones (the contract rig is built on the straightened joints)
    5. metrics: per-edge stretch (after / before length), the rigid regions' deviation from their rigid transform
"""
import math

import numpy as np


def _unit(v):
    v = np.asarray(v, float)
    return v / max(float(np.linalg.norm(v)), 1e-12)


def Rz(phi):
    c, s = math.cos(phi), math.sin(phi)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def Rx(b):
    c, s = math.cos(b), math.sin(b)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def affine(R, a, b):
    """p -> b + R (p - a) as (3x3, 3)"""
    R = np.asarray(R, float)
    return R, np.asarray(b, float) - R @ np.asarray(a, float)


def apply_aff(M, P):
    R, t = M
    return np.asarray(P, float) @ R.T + t


def compose(M2, M1):
    """M2 after M1"""
    R2, t2 = M2; R1, t1 = M1
    return R2 @ R1, R2 @ t1 + t2


IDENT = (np.eye(3), np.zeros(3))


# ======================================================================================================================
# 1. joints
# ======================================================================================================================
def auto_chain(V, spec):
    """slab centroids along spec['axis'] from 'from' to 'to', tracked from 'start' within 'radius' (in the plane across
    the axis), z in [zmin, zmax]; resampled by arc length to n bones (n + 1 joints, joint 0 = start)"""
    ax = {"x": 0, "y": 1, "z": 2}[spec.get("axis", "y")]
    oth = [k for k in range(3) if k != ax]
    a0, a1 = float(spec["from"]), float(spec["to"])
    ns = int(spec.get("slabs", 24))
    r = float(spec.get("radius", 0.6))
    zmin, zmax = float(spec.get("zmin", -1e9)), float(spec.get("zmax", 1e9))
    start = np.asarray(spec["start"], float)
    c = start[oth].copy()
    poly = [start.copy()]
    edges = np.linspace(a0, a1, ns + 1)
    sel0 = (V[:, 2] >= zmin) & (V[:, 2] <= zmax)
    for i in range(ns):
        lo_, hi_ = min(edges[i], edges[i + 1]), max(edges[i], edges[i + 1])
        s = sel0 & (V[:, ax] >= lo_) & (V[:, ax] < hi_)
        s &= np.linalg.norm(V[:, oth] - c, axis=1) <= r
        if s.sum() >= 3:
            c = V[s][:, oth].mean(0)
            p = np.zeros(3); p[ax] = 0.5 * (lo_ + hi_); p[oth] = c
            poly.append(p)
    poly = np.array(poly)
    win = int(spec.get("smooth", 5))            # moving average of the centroids (joint 0 stays): no zig-zag bends
    if win > 1 and len(poly) > win:
        pad = np.vstack([poly[1:2].repeat(win // 2, 0), poly[1:], poly[-1:].repeat(win // 2, 0)])
        ker = np.ones(win) / win
        sm = np.stack([np.convolve(pad[:, k], ker, mode="valid") for k in range(3)], 1)
        poly = np.vstack([poly[:1], sm[:len(poly) - 1]])
    deg = spec.get("fit")                      # a polynomial across the axis (the tail as ONE smooth curve, no wobble)
    if deg and len(poly) > int(deg) + 2:
        t_ = poly[:, ax]
        w_ = np.ones(len(poly)); w_[0] = 100.0  # through the start
        tt = np.linspace(t_[0], t_[-1], 4 * len(poly))
        fit_ = np.zeros((len(tt), 3)); fit_[:, ax] = tt
        for k in oth:
            fit_[:, k] = np.polyval(np.polyfit(t_, poly[:, k], int(deg), w=w_), tt)
        poly = fit_
    seg = np.linalg.norm(np.diff(poly, axis=0), axis=1)
    s_ = np.concatenate([[0.0], np.cumsum(seg)])
    n = int(spec.get("n", 6))
    t = np.linspace(0.0, s_[-1], n + 1)
    out = np.stack([np.interp(t, s_, poly[:, k]) for k in range(3)], 1)
    return out.round(5).tolist(), {"polyline_pts": int(len(poly)), "length_u": round(float(s_[-1]), 4)}


def resolve_auto(rig, V):
    """rig config with every {"auto": spec} chain replaced by explicit points (the low's vertices, stage-1 frame)"""
    rig = dict(rig or {})
    rep = {}
    chains = []
    for ch in rig.get("chains") or []:
        ch = dict(ch)
        if "auto" in ch and not ch.get("points"):
            ch["points"], rep[ch["name"]] = auto_chain(V, ch["auto"])
            rep[ch["name"]]["points"] = ch["points"]
        chains.append(ch)
    rig["chains"] = chains
    return rig, rep


def bones_of(rig):
    """the contract rig's deform bones (name, head, tail, parent, kind, side): spine then chains (.L / .R mirrored), the
    same list proxy_finish_build.rig_clips_export builds (before its centring shift)"""
    spine = [np.asarray(p, float) for p in rig["spine"]]
    sn = rig.get("spine_names") or ["spine.%d" % k for k in range(len(spine) - 1)]
    B = []
    for k in range(len(spine) - 1):
        B.append((sn[k], spine[k], spine[k + 1], "root" if k == 0 else sn[k - 1], "spine", "M"))
    for ch in rig.get("chains") or []:
        pts = [np.asarray(p, float) for p in ch["points"]]
        for s in (("L", "R") if ch.get("mirror", True) else ("M",)):
            P_ = [p * np.array([-1.0, 1.0, 1.0]) if s == "R" else p for p in pts]
            base = ch["name"] + ("" if s == "M" else "." + s)
            for k in range(len(P_) - 1):
                par = ch.get("parent", sn[0]) if k == 0 else "%s.%d" % (base, k - 1)
                B.append(("%s.%d" % (base, k), P_[k], P_[k + 1], par, ch.get("kind", "chain"), s))
    return B


# ======================================================================================================================
# 2. skin
# ======================================================================================================================
def seg_d(P, a, b):
    ab = b - a; ap = P - a
    h = np.clip((ap @ ab) / max(float(ab @ ab), 1e-12), 0, 1)
    return np.linalg.norm(ap - h[:, None] * ab, axis=1)


def weights(P, B, H, falloff=0.06, rigid=None, bone_falloff=None, bone_radius=None):
    """(n, nb) weights: the contract rig's distance falloff, side-aware, <= 4, normalised; then each rigid box pins its
    bone: w_bone = max(w_bone, ramp) where ramp = 1 inside the box, -> 0 at `margin` outside, the others scaled to 1 - w"""
    D = np.stack([seg_d(P, b[1], b[2]) for b in B], 1)
    for j, b in enumerate(B):
        if b[5] == "L":
            D[P[:, 0] < -0.02 * H, j] = np.inf
        elif b[5] == "R":
            D[P[:, 0] > 0.02 * H, j] = np.inf
    taper = {}
    for j, r in (bone_radius or {}).items():       # a chain's reach: vertices beyond it never follow the chain
        r, tp = (r, 0.35 * r) if not isinstance(r, (list, tuple)) else r
        taper[j] = np.clip((r - D[:, j]) / max(tp, 1e-9), 0.0, 1.0)
        D[D[:, j] > r, j] = np.inf
    dmin = D.min(1, keepdims=True)
    sig = np.array([(bone_falloff or {}).get(j, falloff) for j in range(len(B))]) * H
    W = np.exp(-((D - dmin) / sig[None, :]) ** 2)
    for j, tp in taper.items():                   # ... and fade out before it (no tear at the reach)
        W[:, j] *= tp * tp * (3 - 2 * tp)
    W = np.where(W > 1e-3, W, 0.0)
    if (W > 0).sum(1).max() > 4:
        idx = np.argsort(-W, 1, kind="stable")[:, 4:]
        np.put_along_axis(W, idx, 0.0, 1)
    W /= np.maximum(W.sum(1), 1e-30)[:, None]
    names = [b[0] for b in B]
    rig_rep = []
    for rb in rigid or []:
        j = names.index(rb["bone"])
        lo, hi = np.asarray(rb["box"][0], float), np.asarray(rb["box"][1], float)
        m = float(rb.get("margin", 0.15))
        out = np.linalg.norm(np.maximum(np.maximum(lo - P, P - hi), 0.0), axis=1)   # distance outside the box
        ramp = np.clip(1.0 - out / max(m, 1e-9), 0.0, 1.0)
        ramp = ramp * ramp * (3 - 2 * ramp)
        w_new = np.maximum(W[:, j], ramp)
        others = W.copy(); others[:, j] = 0.0
        osum = others.sum(1)
        scale = np.where(osum > 0, (1.0 - w_new) / np.maximum(osum, 1e-30), 0.0)
        W = others * scale[:, None]
        W[:, j] = np.where(osum > 0, w_new, 1.0)
        rig_rep.append({"bone": rb["bone"], "verts_rigid": int((ramp >= 1.0).sum()), "verts_ramped": int(((ramp > 0) & (ramp < 1)).sum()),
                        "margin_u": m})
        if (W > 0).sum(1).max() > 4:          # keep <= 4 after the pin (drop the smallest, renormalise)
            idx = np.argsort(-W, 1, kind="stable")[:, 4:]
            np.put_along_axis(W, idx, 0.0, 1)
            W /= np.maximum(W.sum(1), 1e-30)[:, None]
    return W, rig_rep


# ======================================================================================================================
# 3. pose
# ======================================================================================================================
def _heading(target):
    return {"+y": math.pi / 2, "-y": -math.pi / 2, "+x": 0.0, "-x": math.pi}[target]


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def target_bones(B, tgt):
    names = [b[0] for b in B]
    if "chain" in tgt:
        ids = [j for j, b in enumerate(B) if b[0].rsplit(".", 1)[0] == tgt["chain"] and b[5] == "M"]
    else:
        ids = [names.index(n) for n in tgt["bones"]]
    return ids


def pose_matrices(B, targets, Vlow=None, W=None, floor_z=0.0):
    """-> {bone index: affine}, report. Bones not named stay identity; a chain parented to a moved bone follows it."""
    M = {j: IDENT for j in range(len(B))}
    names = [b[0] for b in B]
    rep = []
    moved = set()
    for tgt in targets:
        ids = target_bones(B, tgt)
        if not ids:
            raise ValueError("repose target %r names no bones" % (tgt,))
        amount = float(tgt.get("amount", 1.0))
        min_h = float(tgt.get("min_horizontal", 0.35))
        at = _heading(tgt.get("heading", "+y"))
        P = [np.asarray(B[ids[0]][1], float)] + [np.asarray(B[j][2], float) for j in ids]
        Pn = [P[0].copy()]
        phis, rows = [], []
        phi_prev = 0.0
        dl = P[-1] - P[-2]
        phi_last = _wrap(at - math.atan2(dl[1], dl[0])) * amount
        for k, j in enumerate(ids):
            d = P[k + 1] - P[k]
            h = math.hypot(d[0], d[1])
            if tgt.get("distribute"):           # a head turn: the LAST bone's correction spread up the chain (a neck twist)
                phi = phi_last * (k + 1) / len(ids)
            elif h >= min_h * float(np.linalg.norm(d)):
                phi = _wrap(at - math.atan2(d[1], d[0])) * amount
            else:
                phi = phi_prev
            phis.append(phi); phi_prev = phi
            Pn.append(Pn[-1] + Rz(phi) @ d)
            M[j] = affine(Rz(phi), P[k], Pn[k])
            rows.append({"bone": names[j], "yaw_deg": round(math.degrees(phi), 3),
                         "heading_before_deg": round(math.degrees(math.atan2(d[1], d[0])), 3),
                         "horizontal_frac": round(h / max(float(np.linalg.norm(d)), 1e-12), 3)})
        beta = 0.0
        lift = None
        if tgt.get("lift_to_floor") and Vlow is not None and W is not None:
            dom = W[:, ids].sum(1) >= 0.5

            def zmin_at(b):
                Mb = dict(M)
                L = affine(Rx(b), Pn[0], Pn[0])
                for j in ids:
                    Mb[j] = compose(L, M[j])
                Vn = skin(Vlow[dom], W[dom], B, Mb)
                return float(Vn[:, 2].min())
            z0 = zmin_at(0.0)
            if z0 < floor_z:
                b_lo, b_hi = 0.0, math.radians(float(tgt.get("lift_max_deg", 40.0)))
                if zmin_at(b_hi) < floor_z:
                    raise ValueError("lift_to_floor: %.1f deg does not clear the floor" % math.degrees(b_hi))
                for _ in range(40):
                    mid = 0.5 * (b_lo + b_hi)
                    if zmin_at(mid) < floor_z:
                        b_lo = mid
                    else:
                        b_hi = mid
                beta = b_hi
            lift = {"chain_min_z_before_u": round(z0, 4), "pitch_up_deg": round(math.degrees(beta), 3),
                    "chain_min_z_after_u": round(zmin_at(beta), 4), "floor_z": floor_z,
                    "rule": "the straightened chain pitched up about its root by the smallest angle clearing the floor"}
            L = affine(Rx(beta), Pn[0], Pn[0])
            for j in ids:
                M[j] = compose(L, M[j])
            Pn = [Pn[0]] + [apply_aff(L, p) for p in Pn[1:]]
        moved.update(ids)
        rep.append({"target": tgt.get("chain") or tgt.get("bones"), "heading": tgt.get("heading", "+y"),
                    "amount": amount, "bones": rows, "lift": lift,
                    "joints_before": np.round(np.array(P), 4).tolist(), "joints_after": np.round(np.array(Pn), 4).tolist()})
    # descendants of a moved bone follow it rigidly (in bone order: parents come first)
    for j, b in enumerate(B):
        if j not in moved and b[3] in names and names.index(b[3]) in moved:
            M[j] = M[names.index(b[3])]
            moved.add(j)
    return M, rep


def skin(P, W, B, M):
    out = np.zeros_like(np.asarray(P, float))
    for j in range(len(B)):
        w = W[:, j]
        nz = w > 0
        if nz.any():
            out[nz] += w[nz, None] * apply_aff(M[j], P[nz])
    return out


def repose_rig(rig, B, M):
    """the rig config with every joint moved by its bone (spine joint k = head of spine bone k; the last = its tail)"""
    names = [b[0] for b in B]
    rig = dict(rig)
    sn = rig.get("spine_names") or ["spine.%d" % k for k in range(len(rig["spine"]) - 1)]
    sp = []
    for k, p in enumerate(rig["spine"]):
        j = names.index(sn[min(k, len(sn) - 1)])
        sp.append(apply_aff(M[j], np.asarray(p, float)[None])[0].round(5).tolist())
    rig["spine"] = sp
    chains = []
    for ch in rig.get("chains") or []:
        ch = dict(ch)
        base = ch["name"] + ("" if not ch.get("mirror", True) else ".L")
        pts = []
        for k, p in enumerate(ch["points"]):
            j = names.index("%s.%d" % (base, min(k, len(ch["points"]) - 2)))
            q = np.asarray(p, float)
            pts.append(apply_aff(M[j], q[None])[0].round(5).tolist())
        ch["points"] = pts
        ch.pop("auto", None)
        chains.append(ch)
    rig["chains"] = chains
    return rig


# ======================================================================================================================
# 5. metrics
# ======================================================================================================================
def edge_stretch(V0, V1, F, mask_v=None):
    F = np.asarray(F, np.int64)
    E = np.unique(np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1), axis=0)
    l0 = np.linalg.norm(V0[E[:, 0]] - V0[E[:, 1]], axis=1)
    l1 = np.linalg.norm(V1[E[:, 0]] - V1[E[:, 1]], axis=1)
    r = l1 / np.maximum(l0, 1e-12)
    moved = np.linalg.norm(V1 - V0, axis=1) > 1e-7
    sel = moved[E[:, 0]] | moved[E[:, 1]]
    if mask_v is not None:
        sel &= mask_v[E[:, 0]] | mask_v[E[:, 1]]
    rs = r[sel] if sel.any() else np.ones(1)
    k = int(np.argmax(r * sel)) if sel.any() else 0
    return {"edges_moved": int(sel.sum()), "max": round(float(rs.max()), 4), "p99": round(float(np.percentile(rs, 99)), 4),
            "p999": round(float(np.percentile(rs, 99.9)), 4), "min": round(float(rs.min()), 4),
            "over_1.25": int((rs > 1.25).sum()), "under_0.8": int((rs < 0.8).sum()),
            "max_at_u": np.round(0.5 * (V1[E[k, 0]] + V1[E[k, 1]]), 3).tolist(),
            "rule": "edge length after / before the repose, edges with a moved vertex"}


def rigid_check(V0, V1, W, B, M, rigid):
    names = [b[0] for b in B]
    out = []
    for rb in rigid or []:
        j = names.index(rb["bone"])
        m = W[:, j] >= 1.0 - 1e-12
        if m.any():
            d = np.linalg.norm(V1[m] - apply_aff(M[j], V0[m]), axis=1)
            out.append({"bone": rb["bone"], "verts": int(m.sum()), "max_dev_from_rigid_u": float(d.max())})
    return out


def run_repose(Vlow, Flow, rig, cfg_repose, H, falloff=0.06, floor_z=0.0):
    """-> (V_new, rig_new (explicit points, straightened), report, extras)"""
    rig, auto_rep = resolve_auto(rig, Vlow)
    B = bones_of(rig)
    rigid = cfg_repose.get("rigid") or []
    bf, br = {}, {}
    for tgt in cfg_repose.get("targets") or []:
        for j in target_bones(B, tgt):
            if "falloff" in tgt:
                bf[j] = float(tgt["falloff"])
            if "radius" in tgt:
                br[j] = tgt["radius"] if isinstance(tgt["radius"], (list, tuple)) else float(tgt["radius"])
    W, rig_rep = weights(Vlow, B, H, float(cfg_repose.get("falloff", falloff)), rigid, bf, br)
    M, prep = pose_matrices(B, cfg_repose.get("targets") or [], Vlow, W, floor_z)
    V1 = skin(Vlow, W, B, M)
    moved_ids = sorted({j for j in range(len(B)) if not (np.allclose(M[j][0], np.eye(3)) and np.allclose(M[j][1], 0))})
    dom = W[:, moved_ids].sum(1) >= 0.5 if moved_ids else np.zeros(len(Vlow), bool)
    rep = {"auto_chains": auto_rep, "bones": [b[0] for b in B], "rigid": rig_rep, "targets": prep,
           "moved_bones": [B[j][0] for j in moved_ids], "verts_moved": int((np.linalg.norm(V1 - Vlow, axis=1) > 1e-7).sum()),
           "max_displacement_u": round(float(np.linalg.norm(V1 - Vlow, axis=1).max()), 4),
           "stretch_all": edge_stretch(Vlow, V1, Flow), "rigid_check": rigid_check(Vlow, V1, W, B, M, rigid),
           "rule": "LBS with the contract rig's weights (+ rigid pins), yaw-only straightening (+ lift), applied as rest"}
    for tgt in cfg_repose.get("targets") or []:
        ids = target_bones(B, tgt)
        mv = W[:, ids].sum(1) > 0
        rep["stretch_" + str(tgt.get("chain") or "_".join(tgt.get("bones")))] = edge_stretch(Vlow, V1, Flow, mv)
    rig_new = repose_rig(rig, B, M)
    return V1, rig_new, rep, {"W": W, "B": B, "M": M, "dom_moved": dom}
