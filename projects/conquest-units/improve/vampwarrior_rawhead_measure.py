"""vampwarrior_rawhead_measure.py -- <source.npz> <vis.npz> <out.json> <cand1.npz> [cand2.npz ...]
(the bake-off measure.py, fold off; the source->candidate samples are drawn ONLY from raw faces that can be seen
(vis.npz 'vis', above the neck cut) so the hidden inner wall and the removed collar do not count; adds an 'all' region.)
Head metrics vs the source (f-frame, glb units): per-region drift both ways, eye-opening area (front, eyeball-labelled
pixels), mouth slit (lip-line crease), fang area, front depth L1 in the eye / mouth boxes."""
import sys, os, json, numpy as np
sys.path.insert(0, r"C:/Users/kenne/OneDrive/Desktop/git/forge/projects/conquest-units/design/reference/vampwarrior/head_graft/head7/bakeoff/py")
import bl_util as U
a = sys.argv[sys.argv.index("--") + 1:]
src, VISP, outp, cands = a[0], a[1], a[2], a[3:]; fold = False
VISM = np.load(VISP)["vis"]; ZCUT = float(np.load(VISP)["zc"])
ECOV = np.load(src.replace(".npz", "_eyecover.npy"))
Z = np.load(src); V, F, flab, FUV, tex = Z["V"], Z["F"], Z["flab"], Z["FUV"], Z["tex"]
MZ = 0.0035
ECS = [V[np.unique(F[flab == k])].mean(0) for k in (1, 2)]
ER = 0.0675
# fang faces: white texels in the mouth box
C = V[F].mean(1)
uv = FUV.mean(1); th, tw = tex.shape[:2]
col = tex[np.clip((uv[:, 1] * th).astype(int), 0, th - 1), np.clip((uv[:, 0] * tw).astype(int), 0, tw - 1)].astype(float)
mouthbox = (np.abs(C[:, 0]) < 0.1) & (C[:, 2] > -0.175) & (C[:, 2] < -0.07) & (C[:, 1] < -0.2)
fang = mouthbox & (col.mean(1) > 185) & (col.max(1) - col.min(1) < 40)
FL = np.zeros(len(F), np.int64)          # 0 skin, 1 eyeball, 2 fang
FL[flab > 0] = 1; FL[fang] = 2; FL[ECOV] = 3          # 3 = the visible eye inside the liner ring
if fold:
    keep = C[:, 0] >= MZ
    Fk = F[keep]; FLk = FL[keep]
    Vm = V.copy(); Vm[:, 0] = 2 * MZ - Vm[:, 0]
    V = np.vstack([V, Vm]); F = np.vstack([Fk, Fk[:, [0, 2, 1]] + len(Vm)]); FL = np.concatenate([FLk, FLk])
    ECS = [e for e in ECS if e[0] > 0]; ECS = ECS + [ECS[0] * np.array([-1, 1, 1]) + np.array([2 * MZ, 0, 0])]
C = V[F].mean(1)


def regions(P):
    R = {}
    R["all"] = np.ones(len(P), bool)
    R["face"] = (P[:, 1] < -0.1) & (P[:, 2] > -0.37) & (P[:, 2] < 0.25) & (np.abs(P[:, 0]) < 0.3)
    e = np.zeros(len(P), bool)
    for c in ECS:
        e |= (np.abs(P[:, 0] - c[0]) < 0.09) & (np.abs(P[:, 2] - c[2]) < 0.055) & (P[:, 1] < -0.1)
    R["eyes"] = e
    R["mouth"] = (np.abs(P[:, 0]) < 0.1) & (P[:, 2] > -0.175) & (P[:, 2] < -0.07) & (P[:, 1] < -0.18)
    R["jaw"] = (P[:, 2] > -0.37) & (P[:, 2] < -0.17) & (np.abs(P[:, 0]) < 0.25) & (P[:, 1] < -0.05)
    return R


tS = U.bvh(V, F)
# source vertex sample (area-uniform points on the source faces)
rng = np.random.default_rng(0)
A = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
A = A * (VISM & (V[F][:, :, 2].min(1) > ZCUT + 0.003))
fi = rng.choice(len(F), 60000, p=A / A.sum()); r1, r2 = rng.random(60000), rng.random(60000)
sq = np.sqrt(r1); bc = np.stack([1 - sq, sq * (1 - r2), sq * r2], 1)
PS = np.einsum("nk,nkd->nd", bc, V[F[fi]])
RS = regions(PS)


def front_maps(tree, Vc=None, Fc=None):
    out = {}
    for i, c in enumerate(ECS):
        H, FI, xs, zs = U.ortho_front(tree, c[0] - 0.09, c[0] + 0.09, c[2] - 0.05, c[2] + 0.05, 0.0005)
        out["eye%d" % i] = H
    H, FI, xs, zs = U.ortho_front(tree, -0.1, 0.1, -0.175, -0.07, 0.0005)
    out["mouth"] = H
    H, FI, xs, zs = U.ortho_front(tree, -0.08, 0.08, 0.2, 0.28, 0.002)
    out["forehead"] = H
    return out


def signed(P):
    """nearest source face label + signed distance (along the face normal) per point"""
    from mathutils import Vector
    lab = np.full(len(P), -1); sd = np.full(len(P), np.nan)
    for i, p in enumerate(P):
        if np.isfinite(p[0]):
            h = tS.find_nearest(Vector(p))
            if h[0] is not None:
                lab[i] = FL[h[2]]; sd[i] = (Vector(p) - h[0]).dot(h[1])
    return lab, sd


def eye_open(mp):
    hf = mp["forehead"].reshape(-1, 3); _, sdf = signed(hf)
    delta = float(np.nanmedian(sdf))
    px = []
    for i in range(len(ECS)):
        lab, sd = signed(mp["eye%d" % i].reshape(-1, 3))
        px.append(int(((lab == 3) & (np.abs(sd - delta) < 0.004)).sum()))
    return px, delta


def label_hits(H):
    P = H.reshape(-1, 3); ok = np.isfinite(P[:, 0])
    fidx, d = U.nearest_face(tS, np.where(ok[:, None], P, np.nan))
    lab = np.where(ok & (d < 0.012) & (fidx >= 0), FL[np.maximum(fidx, 0)], -1)
    return lab.reshape(H.shape[:2])


srcmaps = front_maps(tS)
srclab = {k: label_hits(v) for k, v in srcmaps.items()}
SRC_EYE = eye_open(srcmaps)[0]


def slit(H):
    xs = np.arange(-0.1, 0.1, 0.0005); zs = np.arange(-0.07, -0.175, -0.0005)
    cols = np.abs(xs) < 0.045; rows = (zs < -0.095) & (zs > -0.125)
    Y = H[..., 1][np.ix_(rows, cols)]
    rec = []
    for j in range(Y.shape[1]):
        y = Y[:, j]
        if not np.isfinite(y).any():
            rec.append(0.1); continue
        y = np.where(np.isfinite(y), y, np.nanmax(y) + 0.05)
        base = 0.5 * (y[:4].mean() + y[-4:].mean())
        rec.append(float(y.max() - base))
    rec = np.array(rec)
    return {"open_frac": round(float((rec > 0.01).mean()), 3), "recess_mean": round(float(rec.mean()), 4)}


def metrics(Vc, Fc, name):
    tC = U.bvh(Vc, Fc)
    m = {"name": name, "tris": int(len(Fc)), "verts": int(len(Vc))}
    dsc = U.dists(tC, PS)                                  # source -> cand
    fidx, dcs = U.nearest_face(tS, Vc)                     # cand -> source
    cpos = np.array([tS.find_nearest(__import__("mathutils").Vector(p))[0][:] for p in Vc])
    RC = regions(cpos)
    foreign = dcs > 0.05
    m["foreign_verts"] = int(foreign.sum())
    dr = {}
    for k in RS:
        a_ = dsc[RS[k]]; b_ = dcs[RC[k] & ~foreign]
        dr[k] = {"s2c_p50": round(float(np.percentile(a_, 50)), 5), "s2c_p99": round(float(np.percentile(a_, 99)), 5),
                 "c2s_p50": round(float(np.percentile(b_, 50)), 5) if len(b_) else None,
                 "c2s_p99": round(float(np.percentile(b_, 99)), 5) if len(b_) else None}
    m["drift"] = dr
    mp = front_maps(tC)
    px, delta = eye_open(mp)
    eye = [{"px": p, "src_px": s_, "pct": round(100.0 * p / max(s_, 1), 1)} for p, s_ in zip(px, SRC_EYE)]
    m["eye_open"] = eye; m["skin_offset"] = round(delta, 5)
    m["eye_open_pct_mean"] = round(float(np.mean([e["pct"] for e in eye])), 1)
    lab = label_hits(mp["mouth"]); s_ = int((srclab["mouth"] == 2).sum())
    m["fang_px_pct"] = round(100.0 * (lab == 2).sum() / max(s_, 1), 1)
    m["slit"] = slit(mp["mouth"])
    l1 = {}
    for k in mp:
        ys, yc = srcmaps[k][..., 1], mp[k][..., 1]; ok = np.isfinite(ys) & np.isfinite(yc)
        l1[k] = round(float(np.abs(ys[ok] - yc[ok]).mean()), 5)
    m["depth_L1"] = l1
    np.savez_compressed(outp.replace(".json", "_%s_maps.npz" % name), **{k: v for k, v in mp.items()})
    return m


res = {"source": src, "fold": fold, "eye_centres": [c.round(4).tolist() for c in ECS], "fang_faces": int((FL == 2).sum()),
       "source_slit": slit(srcmaps["mouth"]), "source_eye_px": SRC_EYE,
       "cands": []}
np.savez_compressed(outp.replace(".json", "_source_maps.npz"), **srcmaps)
for cp in cands:
    Zc = np.load(cp)
    nm = os.path.basename(cp).replace(".npz", "")
    m = metrics(Zc["V"], Zc["F"], nm)
    print("METRIC", json.dumps(m))
    res["cands"].append(m)
json.dump(res, open(outp, "w"), indent=1)
print("SOURCE", json.dumps({k: res[k] for k in ("eye_centres", "fang_faces", "source_slit", "source_eye_px")}))
