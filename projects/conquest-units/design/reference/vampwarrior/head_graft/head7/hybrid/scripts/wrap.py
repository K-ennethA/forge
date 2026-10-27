"""wrap.py -- <mpfb_base.npz> <lips_split.json.gz> <src.npz> <src_landmarks.json> <src_eyecontour.npz> <out.npz>
(e) the MPFB hm08 template head + neck wrapped onto the source head: landmark affine + thin-plate warp (eye apertures by
angle-matched contours, mouth, nose, chin, top, back, ears, neck), then closest-point projection + relax (eye sockets and the
mouth bag kept off the surface), eye margin loops pinned to the source aperture contour; two UV-sphere eyeballs fitted to the
source eye cover."""
import sys, os, json, gzip, math, numpy as np, bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
from mathutils import Vector

a = sys.argv[sys.argv.index("--") + 1:]
Zm = np.load(a[0]); LIPS = set(int(k) for k in json.load(gzip.open(a[1])).keys())
Zs = np.load(a[2]); SL = json.load(open(a[3])); EC = np.load(a[4]); outp = a[5]
CUT = 5.65
V, F, VT, FT, isb = Zm["V"], Zm["F"], Zm["VT"], Zm["FT"], Zm["isbody"]
fm = isb[F].all(1) & (V[F][:, :, 1].min(1) > CUT)
fidx = np.nonzero(fm)[0]
Fq = F[fm]; FTq = FT[fm]
u = np.unique(Fq); rm = -np.ones(len(V), np.int64); rm[u] = np.arange(len(u))
Fq = rm[Fq]
P0 = np.stack([V[u, 0], -V[u, 2], V[u, 1]], 1)            # hm08 -> f-frame
nv = len(P0)
E = np.concatenate([Fq[:, [0, 1]], Fq[:, [1, 2]], Fq[:, [2, 3]], Fq[:, [3, 0]]])
E = np.unique(np.sort(E, 1), axis=0)
nbr = [[] for _ in range(nv)]
for i, j in E:
    nbr[i].append(j); nbr[j].append(i)

# ---------------------------------------------------------------- hm08 landmarks
rep = {}
eyes = []
for nm in ("leye", "reye"):
    Ep = np.stack([V[Zm[nm], 0], -V[Zm[nm], 2], V[Zm[nm], 1]], 1)
    c = Ep.mean(0); R = float(np.linalg.norm(Ep - c, axis=1).mean())
    d = np.linalg.norm(P0 - c, axis=1) / R
    sock = (d < 0.97)
    near = np.nonzero((d < 1.3) & ~sock)[0]
    margin = np.array([i for i in near if any(sock[j] for j in nbr[i]) and d[i] < 1.08 and P0[i, 1] < c[1] - 0.2 * R])
    ang = np.arctan2(P0[margin, 2] - c[2], P0[margin, 0] - c[0])
    margin = margin[np.argsort(ang)]
    sk = set(np.nonzero(sock)[0].tolist()); st = list(sk); ms = set(margin.tolist())
    while st:
        i = st.pop()
        for j in nbr[i]:
            if j not in sk and j not in ms and np.linalg.norm(P0[j] - c) < 1.0 * R:
                sk.add(j); st.append(j)
    eyes.append({"c": c, "R": R, "margin": margin, "socket": np.array(sorted(sk), np.int64)})
    rep[nm] = {"centre": c.round(4).tolist(), "R": round(R, 4), "margin_verts": int(len(margin)), "socket_verts": int(len(sk))}
print("HM_EYES", json.dumps(rep))
lipv = np.unique(rm[F[[i for i in fidx if i in LIPS]]])
lipv = lipv[lipv >= 0]
LP = P0[lipv]
J = json.loads(str(Zm["J"])); mz = J["joint-mouth"][1]
HL = {}
HL["mouth_l"] = LP[np.argmax(LP[:, 0])]; HL["mouth_r"] = LP[np.argmin(LP[:, 0])]
mid = LP[np.abs(LP[:, 0]) < 0.03]
# the lip line height: the deepest (largest y, inner) mid lip vertex
mz = 0.5 * (LP[np.argmax(LP[:, 0]), 2] + LP[np.argmin(LP[:, 0]), 2])
print("HM_LIPS", len(lipv), "mid", len(mid), "lipline z", round(float(mz), 3), "mid z range", mid[:, 2].min().round(3), mid[:, 2].max().round(3))
upm = mid[mid[:, 2] > mz]; lom = mid[mid[:, 2] <= mz]
HL["upperlip_mid"] = upm[np.argmin(upm[:, 1])]; HL["lowerlip_mid"] = lom[np.argmin(lom[:, 1])]
fy = min(HL["upperlip_mid"][1], HL["lowerlip_mid"][1])
upf = upm[upm[:, 1] < fy + 0.12]; lof = lom[lom[:, 1] < fy + 0.12]
HL["lipline_mid"] = np.array([0.0, 0.5 * (upf[np.argmin(upf[:, 2]), 1] + lof[np.argmax(lof[:, 2]), 1]),
                              0.5 * (upf[:, 2].min() + lof[:, 2].max())])
ez = eyes[0]["c"][2]
mm = (np.abs(P0[:, 0]) < 0.03) & (P0[:, 2] < ez) & (P0[:, 2] > HL["upperlip_mid"][2])
HL["nose_tip"] = P0[mm][np.argmin(P0[mm][:, 1])]
mm = (np.abs(P0[:, 0]) < 0.03) & (P0[:, 1] < -0.9)
HL["chin"] = P0[mm][np.argmin(P0[mm][:, 2])]
HL["top"] = P0[np.argmax(P0[:, 2])]
band = np.abs(P0[:, 2] - ez) < 0.08; HL["back"] = P0[band][np.argmax(P0[band][:, 1])]
mm = np.abs(P0[:, 2] - ez) < 0.6
HL["ear_l"] = P0[mm][np.argmax(P0[mm][:, 0])]; HL["ear_r"] = P0[mm][np.argmin(P0[mm][:, 0])]
print("HM_LM", json.dumps({k: np.round(v, 3).tolist() for k, v in HL.items()}))

# ---------------------------------------------------------------- source numbers
S = {k: np.array(v) for k, v in SL.items()}
if EC["c0"][0] > 0:
    sc = [EC["c0"], EC["c1"]]; scont = [EC["eye0"], EC["eye1"]]
else:
    sc = [EC["c1"], EC["c0"]]; scont = [EC["eye1"], EC["eye0"]]
hc = 0.5 * (eyes[0]["c"] + eyes[1]["c"]); hcs = 0.5 * (sc[0] + sc[1])
sx = (sc[0][0] - sc[1][0]) / (eyes[0]["c"][0] - eyes[1]["c"][0])
sz = (hcs[2] - S["chin"][2]) / (hc[2] - HL["chin"][2])
sy = (S["back"][1] - S["nose_tip"][1]) / (HL["back"][1] - HL["nose_tip"][1])
scl = np.array([sx, sy, sz])
off_y = 0.0


def aff(P):
    return (P - hc) * scl + hcs


P1 = aff(P0)
rep["affine_scale"] = scl.round(4).tolist()
src_pts, dst_pts = [], []
for k in ("mouth_l", "mouth_r", "upperlip_mid", "lowerlip_mid", "lipline_mid", "nose_tip", "chin", "top", "back", "ear_l", "ear_r"):
    src_pts.append(aff(HL[k][None])[0]); dst_pts.append(S[k])
NC = 24
eye_targets = []
for e, c_s, cont in zip(eyes, sc, scont):
    mP = aff(P0[e["margin"]])
    ce = mP.mean(0)
    am = np.arctan2(mP[:, 2] - ce[2], mP[:, 0] - ce[0])
    asr = np.arctan2(cont[:, 2] - c_s[2], cont[:, 0] - c_s[0])
    tgt = np.array([cont[np.argmin(np.abs(np.angle(np.exp(1j * (asr - t)))))] for t in am])
    eye_targets.append(tgt)
    sel = np.linspace(0, len(mP) - 1, min(NC, len(mP))).astype(int)
    src_pts += list(mP[sel]); dst_pts += list(tgt[sel])
    cc = aff(e["c"][None])[0]
    src_pts.append(cc); dst_pts.append(cc + (c_s - ce) * np.array([1.0, 0.5, 1.0]))
low = P1[:, 2] < P1[:, 2].min() + 0.03
cand = np.nonzero(low)[0]
for k, sg in (("neck_f", (0, -1)), ("neck_b", (0, 1)), ("neck_l", (1, 0)), ("neck_r", (-1, 0))):
    i = cand[np.argmax(sg[0] * P1[cand, 0] + sg[1] * P1[cand, 1])]
    src_pts.append(P1[i]); dst_pts.append(S[k] + np.array([0.0, 0.0, P1[i, 2] - S[k][2]]))
Xs = np.array(src_pts); Yd = np.array(dst_pts)
n = len(Xs)
K = np.linalg.norm(Xs[:, None] - Xs[None], axis=-1)
Pm = np.hstack([np.ones((n, 1)), Xs])
A = np.zeros((n + 4, n + 4)); A[:n, :n] = K + 1e-6 * np.eye(n); A[:n, n:] = Pm; A[n:, :n] = Pm.T
bvec = np.zeros((n + 4, 3)); bvec[:n] = Yd
W = np.linalg.solve(A, bvec)


def tps(P):
    Kp = np.linalg.norm(P[:, None] - Xs[None], axis=-1)
    return Kp @ W[:n] + np.hstack([np.ones((len(P), 1)), P]) @ W[n:]


P2 = np.vstack([tps(P1[i:i + 2000]) for i in range(0, nv, 2000)])
rep["tps_landmarks"] = int(n)
rep["tps_residual_max"] = round(float(np.abs(tps(Xs) - Yd).max()), 6)

# ---------------------------------------------------------------- projection + relax
Fs = Zs["F"][Zs["flab"] == 0]
tS = U.bvh(Zs["V"], Fs)
skip = np.zeros(nv, bool)
for e in eyes:
    skip[e["socket"]] = True
pin = {}
for e, t in zip(eyes, eye_targets):
    for i, p in zip(e["margin"], t):
        pin[int(i)] = p
mw = 0.5 * (S["mouth_l"][0] - S["mouth_r"][0]) + 0.01
bag = (np.abs(P2[:, 0]) < mw) & (P2[:, 2] < S["upperlip_mid"][2] + 0.01) & (P2[:, 2] > S["lowerlip_mid"][2] - 0.02) & \
      (P2[:, 1] > S["lipline_mid"][1] + 0.012)
skip |= bag
rep["skip_verts"] = {"socket": int(sum(len(e["socket"]) for e in eyes)), "mouth_bag": int(bag.sum())}


def vnormals(Q):
    Nn = np.zeros_like(Q)
    for k in range(4):
        a_, b_, c_ = Fq[:, k], Fq[:, (k + 1) % 4], Fq[:, (k - 1) % 4]
        np.add.at(Nn, a_, np.cross(Q[b_] - Q[a_], Q[c_] - Q[a_]))
    return Nn / np.maximum(np.linalg.norm(Nn, axis=1), 1e-12)[:, None]


deg = np.array([len(x) for x in nbr])
rows = np.concatenate([[i] * len(x) for i, x in enumerate(nbr)]).astype(np.int64); cols = np.concatenate(nbr).astype(np.int64)
pins = np.array(sorted(pin.keys()), np.int64)
Q = P2.copy()
mv = ~skip; mv[pins] = False
mvi = np.nonzero(mv)[0]
NIT = 14
Nn = vnormals(Q)
sgn = 1.0 if float(((Q - Q.mean(0)) * Nn).sum(1).mean()) > 0 else -1.0
rep["hm_normals_outward"] = bool(sgn > 0)
feat = (np.abs(np.abs(P2[:, 0]) - 0.125) < 0.09) & (np.abs(P2[:, 2] - 0.092) < 0.05) & (P2[:, 1] < -0.1)
feat |= (np.abs(P2[:, 0]) < 0.11) & (P2[:, 2] > -0.17) & (P2[:, 2] < -0.06) & (P2[:, 1] < -0.15)
rep["feature_verts_nearest"] = int(feat.sum())
for it in range(NIT):
    Nn = sgn * vnormals(Q)
    for i in mvi:
        h = (None,)
        if not feat[i]:
            o = Vector(Q[i] + 0.025 * Nn[i]); h = tS.ray_cast(o, Vector(-Nn[i]), 0.05)
        if h[0] is None:
            h = tS.find_nearest(Vector(Q[i]))
        if h[0] is not None:
            Q[i] = np.array(h[0])
    for i, p in pin.items():
        Q[i] = p
    if it < NIT - 1:
        Nn = vnormals(Q)
        avg = np.zeros_like(Q); np.add.at(avg, rows, Q[cols]); avg /= deg[:, None]
        dlt = avg - Q
        dlt = dlt - (dlt * Nn).sum(1)[:, None] * Nn * (0.0 if it < NIT - 4 else 0.7)
        Q[mv] = Q[mv] + 0.6 * dlt[mv]
# flipped quads vs the source normals
Nq = np.cross(Q[Fq[:, 1]] - Q[Fq[:, 0]], Q[Fq[:, 3]] - Q[Fq[:, 0]])
Cq = Q[Fq].mean(1)
flip = 0
for k in range(len(Fq)):
    h = tS.find_nearest(Vector(Cq[k]))
    if h[0] is not None and float(np.dot(Nq[k], np.array(h[1]))) < 0:
        flip += 1
rep["quads_flipped_vs_source"] = int(flip)
D = Q - P2
for _ in range(30):
    avg = np.zeros_like(D); np.add.at(avg, rows, D[cols]); avg /= deg[:, None]
    D[skip] = avg[skip]
Q[skip] = P2[skip] + D[skip]
dist = U.dists(tS, Q[~skip])
rep["head_to_src_p50_p99"] = [round(float(np.percentile(dist, 50)), 5), round(float(np.percentile(dist, 99)), 5)]

# ---------------------------------------------------------------- eyeballs fitted to the source eye cover
ecov = np.load(a[2].replace(".npz", "_eyecover.npy"))
Fc = Zs["F"][ecov]
cen = Zs["V"][Fc].mean(1)
balls = []
for e, c_s in zip(eyes, sc):
    side = np.sign(c_s[0])
    Pc = Zs["V"][np.unique(Fc[np.sign(cen[:, 0]) == side])]
    R = e["R"] * sx
    c = Pc.mean(0) + np.array([0.0, R * 0.9, 0.0])
    for _ in range(100):
        dv = Pc - c; dn = np.linalg.norm(dv, axis=1)
        c = c + 0.5 * ((dn - R)[:, None] * (dv / dn[:, None])).mean(0)
    balls.append((c, R))
rep["eyeballs"] = [{"centre": c.round(4).tolist(), "R": round(R, 4)} for c, R in balls]

Ft = np.vstack([Fq[:, [0, 1, 2]], Fq[:, [0, 2, 3]]])
UVq = VT[FTq]
FUV = np.vstack([UVq[:, [0, 1, 2]], UVq[:, [0, 2, 3]]]).copy(); FUV[..., 1] = 1 - FUV[..., 1]
Vall = [Q]; Fall = [Ft]; FUVall = [FUV]; off = nv
for bi, (c, R) in enumerate(balls):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=R, location=(0, 0, 0))
    ob = bpy.context.active_object
    me = ob.data
    Vb = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", Vb); Vb = Vb.reshape(-1, 3)
    Vb = np.stack([Vb[:, 0], -Vb[:, 2], Vb[:, 1]], 1) + c          # pole along -y (front)
    me.calc_loop_triangles()
    Tb = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("vertices", Tb); Tb = Tb.reshape(-1, 3)
    Lb = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("loops", Lb)
    UVb = np.empty(len(me.loops) * 2); me.uv_layers[0].data.foreach_get("uv", UVb); UVb = UVb.reshape(-1, 2)[Lb.reshape(-1, 3)]
    UVb = UVb.copy(); UVb[..., 1] = 1 - UVb[..., 1]
    Vall.append(Vb); Fall.append(Tb + off); FUVall.append(UVb); off += len(Vb)
    bpy.data.objects.remove(ob)
Vo = np.vstack(Vall); Fo = np.vstack(Fall); FUVo = np.vstack(FUVall)
part = np.concatenate([np.zeros(len(Ft), int)] + [np.full(len(f_), i + 1) for i, f_ in enumerate(Fall[1:])])
np.savez_compressed(outp, V=Vo, F=Fo, FUV=FUVo, part=part, Fq=Fq, nhead=nv,
                    margin_l=eyes[0]["margin"], margin_r=eyes[1]["margin"], socket_l=eyes[0]["socket"],
                    socket_r=eyes[1]["socket"], lipv=lipv, bag=np.nonzero(bag)[0], P_tps=P2)
rep["tris"] = int(len(Fo)); rep["head_quads"] = int(len(Fq))
json.dump(rep, open(outp.replace(".npz", "_wrap.json"), "w"), indent=1)
print("WRAP", json.dumps(rep))
