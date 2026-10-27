"""wrap2.py -- <bakeoff dir> : the HYBRID head fit (MPFB hm08 head topology, the source's shape).
  1. template head + neck (quads), f-frame; eye pockets + lid margin loops (upper / lower), lip contact line RIPPED open
     (upper / lower lip loops), mouth bag.
  2. landmark warp: affine + thin plate on: lid margins (upper / lower, matched by x between the corners) -> the source
     eye opening (inside the liner); lip contact line -> the source lip line; vermilion borders -> the source lip borders;
     nose tip, chin, top, back, ear tip / outer / lobe, neck ring.
  3. projection onto the source (closest hit along the normal, else nearest, capped) + Laplacian-detail-preserving relax,
     lid margins + lip lines pinned; pockets / bag follow smoothly.
  -> s3/hyb_fit.npz + s3/hyb_fit.json"""
import sys, os, json, gzip, numpy as np, bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
from mathutils import Vector

BD = sys.argv[sys.argv.index("--") + 1]
J_ = lambda *p: os.path.join(BD, *p)
Zm = np.load(J_("s2", "mpfb_base.npz"))
Zs = np.load(J_("s2", "edit.npz"))
SL = json.load(open(J_("s3", "src_landmarks.json")))
EC = np.load(J_("s2", "edit_eyecontour.npz"))
SM = np.load(J_("s3", "src_mouth.npz"))
UPF = set(np.load(J_("s3", "hm_lip_up_faces.npy")).tolist()); LOF = set(np.load(J_("s3", "hm_lip_lo_faces.npy")).tolist())
CUT = 5.65
V, F, VT, FT, isb = Zm["V"], Zm["F"], Zm["VT"], Zm["FT"], Zm["isbody"]
fm = isb[F].all(1) & (V[F][:, :, 1].min(1) > CUT)
fidx = np.nonzero(fm)[0]
Fq = F[fm].copy(); FTq = FT[fm]
u = np.unique(Fq); rm = -np.ones(len(V), np.int64); rm[u] = np.arange(len(u))
Fq = rm[Fq]
P0 = np.stack([V[u, 0], -V[u, 2], V[u, 1]], 1)
nv0 = len(P0)
rep = {}
up_f = np.array([i for i, f in enumerate(fidx) if f in UPF]); lo_f = np.array([i for i, f in enumerate(fidx) if f in LOF])


def adjacency(Fq, n):
    nb = [set() for _ in range(n)]
    for f in Fq:
        for k in range(4):
            nb[f[k]].add(f[(k + 1) % 4]); nb[f[(k + 1) % 4]].add(f[k])
    return nb


# ---------------------------------------------------------------- lips: contact line + rip
def fedges(fs):
    s = set()
    for f in Fq[fs]:
        for k in range(4):
            s.add(tuple(sorted((int(f[k]), int(f[(k + 1) % 4])))))
    return s


contact_e = fedges(up_f) & fedges(lo_f)
cv = np.unique(np.array(list(contact_e)))
cx = P0[cv, 0]
corners = [cv[np.argmin(cx)], cv[np.argmax(cx)]]
inner = [int(i) for i in cv if i not in corners]
dup = {i: nv0 + k for k, i in enumerate(inner)}
P0 = np.vstack([P0, P0[inner]])
# faces below the lip line (template up axis = f z) take the duplicates
lipz = float(P0[cv, 2].mean())
Fc = P0[Fq].mean(1)
lower_side = set(lo_f.tolist())
for fi_ in range(len(Fq)):
    if fi_ in lower_side or (Fc[fi_, 2] < lipz and np.isin(Fq[fi_], inner).any() and fi_ not in set(up_f.tolist())):
        for k in range(4):
            if int(Fq[fi_, k]) in dup:
                Fq[fi_, k] = dup[int(Fq[fi_, k])]
nv = len(P0)
nbr = adjacency(Fq, nv)
lip_up_line = np.array(sorted([corners[0]] + inner + [corners[1]], key=lambda i: P0[i, 0]))
lip_lo_line = np.array(sorted([corners[0]] + [dup[i] for i in inner] + [corners[1]], key=lambda i: P0[i, 0]))
E = np.unique(np.sort(np.concatenate([Fq[:, [0, 1]], Fq[:, [1, 2]], Fq[:, [2, 3]], Fq[:, [3, 0]]]), 1), axis=0)
cnt = {}
for f in Fq:
    for k in range(4):
        e = tuple(sorted((int(f[k]), int(f[(k + 1) % 4])))); cnt[e] = cnt.get(e, 0) + 1
rep["rip"] = {"contact_verts": int(len(cv)), "duplicated": int(len(inner)),
              "open_edges_after": int(sum(1 for c in cnt.values() if c == 1))}
# vermilion borders: lip-set boundary edges on the outer (front) side
def border(fs):
    c = {}
    for f in Fq[fs]:
        for k in range(4):
            e = tuple(sorted((int(f[k]), int(f[(k + 1) % 4])))); c[e] = c.get(e, 0) + 1
    vv = np.unique(np.array([e for e, n in c.items() if n == 1]))
    return vv
bu = border(up_f); bl = border(lo_f)
bu = bu[(P0[bu, 1] < -1.45) & (P0[bu, 2] > lipz + 0.02)]
bl = bl[(P0[bl, 1] < -1.45) & (P0[bl, 2] < lipz - 0.02)]
# bag: flood from inside the mouth (template), bounded
seed = int(np.argmin(np.linalg.norm(P0 - np.array([0.0, -1.0, 6.45]), axis=1)))
inbox = (np.abs(P0[:, 0]) < 0.55) & (P0[:, 2] > 6.0) & (P0[:, 2] < 7.0) & (P0[:, 1] < -0.3)
bag = {seed}; st = [seed]
while st:
    i = st.pop()
    for j in nbr[i]:
        if j not in bag and inbox[j] and P0[j, 1] > -1.36:
            bag.add(j); st.append(j)
bag = np.array(sorted(bag))
rep["lips"] = {"upper_border_verts": int(len(bu)), "lower_border_verts": int(len(bl)), "bag_verts": int(len(bag)),
               "lip_line_verts": int(len(lip_up_line))}

# ---------------------------------------------------------------- eyes: pocket, margin (upper / lower), rings
eyes = []
for nm in ("leye", "reye"):
    Ep = np.stack([V[Zm[nm], 0], -V[Zm[nm], 2], V[Zm[nm], 1]], 1)
    c = Ep.mean(0); R = float(np.linalg.norm(Ep - c, axis=1).mean())
    d = np.linalg.norm(P0 - c, axis=1) / R
    pocket = set(np.nonzero(d < 0.97)[0].tolist())
    ring = {i: 0 for i in pocket}; fr = list(pocket)
    for r in range(1, 8):
        nf = []
        for i in fr:
            for j in nbr[i]:
                if j not in ring:
                    ring[j] = r; nf.append(j)
        fr = nf
    m1 = np.array([i for i, r in ring.items() if r == 1])
    ce = P0[m1].mean(0)
    c0 = m1[np.argmin(P0[m1, 0])]; c1 = m1[np.argmax(P0[m1, 0])]
    # upper / lower: above / below the corner-to-corner line
    t = (P0[m1, 0] - P0[c0, 0]) / (P0[c1, 0] - P0[c0, 0])
    zl = P0[c0, 2] + t * (P0[c1, 2] - P0[c0, 2])
    upv = m1[(P0[m1, 2] >= zl) & (m1 != c0) & (m1 != c1)]; lov = m1[(P0[m1, 2] < zl) & (m1 != c0) & (m1 != c1)]
    upv = upv[np.argsort(P0[upv, 0])]; lov = lov[np.argsort(P0[lov, 0])]
    eyes.append({"c": c, "R": R, "pocket": np.array(sorted(pocket)), "ring": ring, "corners": (int(c0), int(c1)),
                 "up": upv, "lo": lov})
    rep[nm] = {"pocket": len(pocket), "margin_up": int(len(upv)), "margin_lo": int(len(lov))}
print("TEMPLATE", json.dumps(rep))

# ---------------------------------------------------------------- source targets
S = {k: np.array(v) for k, v in SL.items() if v is not None}
if EC["c0"][0] > 0:
    sc = [EC["c0"], EC["c1"]]; scont = [EC["eye0"], EC["eye1"]]
else:
    sc = [EC["c1"], EC["c0"]]; scont = [EC["eye1"], EC["eye0"]]


def split_contour(cont):
    i0 = np.argmin(cont[:, 0]); i1 = np.argmax(cont[:, 0])
    p0, p1 = cont[i0], cont[i1]
    t = (cont[:, 0] - p0[0]) / (p1[0] - p0[0])
    zl = p0[2] + t * (p1[2] - p0[2])
    upc = cont[cont[:, 2] >= zl]; loc = cont[cont[:, 2] < zl]
    return p0, p1, upc[np.argsort(upc[:, 0])], loc[np.argsort(loc[:, 0])]


def clean_poly(P, nb=32, win=5):
    """a noisy point band sorted by x -> nb binned medians, moving-average smoothed (ends kept)"""
    P = P[np.argsort(P[:, 0])]
    edges = np.linspace(P[0, 0], P[-1, 0], nb + 1)
    out = []
    for a_, b_ in zip(edges[:-1], edges[1:]):
        m = (P[:, 0] >= a_) & (P[:, 0] <= b_)
        if m.any():
            out.append(np.median(P[m], axis=0))
    out = np.array(out)
    sm = out.copy()
    h = win // 2
    for i in range(1, len(out) - 1):
        lo_, hi_ = max(0, i - h), min(len(out), i + h + 1)
        sm[i, 1:] = out[lo_:hi_, 1:].mean(0)
    return sm


def along(poly, n_or_t):
    """points on a polyline (sorted by x) at normalised arc length t"""
    seg = np.linalg.norm(np.diff(poly, axis=0), axis=1); s = np.concatenate([[0], np.cumsum(seg)]); s /= s[-1]
    return np.stack([np.interp(n_or_t, s, poly[:, k]) for k in range(3)], 1)


def arclen_t(P):
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1); s = np.concatenate([[0], np.cumsum(seg)]); return s / s[-1]


hc = 0.5 * (eyes[0]["c"] + eyes[1]["c"]); hcs = 0.5 * (sc[0] + sc[1])
# template landmarks
mid = P0[lip_up_line]
HL = {"nose_tip": None}
ez = eyes[0]["c"][2]
mm = (np.abs(P0[:, 0]) < 0.03) & (P0[:, 2] < ez) & (P0[:, 2] > lipz + 0.1) & (P0[:, 1] < -1.3)
HL["nose_tip"] = P0[mm][np.argmin(P0[mm][:, 1])]
mm = (np.abs(P0[:, 0]) < 0.03) & (P0[:, 1] < -0.9) & (np.arange(nv) < nv0)
HL["chin"] = P0[mm][np.argmin(P0[mm][:, 2])]
HL["top"] = P0[np.argmax(P0[:, 2])]
band = np.abs(P0[:, 2] - ez) < 0.08; HL["back"] = P0[band][np.argmax(P0[band][:, 1])]
for s_, nm in ((1, "l"), (-1, "r")):
    earm = (s_ * P0[:, 0] > 0.72) & (np.abs(P0[:, 2] - ez) < 0.7)
    HL["ear_" + nm] = P0[earm][np.argmax(s_ * P0[earm][:, 0])]
    HL["eartip_" + nm] = P0[earm][np.argmax(P0[earm][:, 2])]
    HL["earlobe_" + nm] = P0[earm][np.argmin(P0[earm][:, 2])]
sx = (sc[0][0] - sc[1][0]) / (eyes[0]["c"][0] - eyes[1]["c"][0])
sz = (hcs[2] - S["chin"][2]) / (hc[2] - HL["chin"][2])
sy = (S["back"][1] - S["nose_tip"][1]) / (HL["back"][1] - HL["nose_tip"][1])
scl = np.array([sx, sy, sz])
aff = lambda P: (P - hc) * scl + hcs
P1 = aff(P0)
rep["affine_scale"] = scl.round(4).tolist()
src_pts, dst_pts, pins = [], [], {}
for k in ("nose_tip", "chin", "top", "back", "ear_l", "ear_r", "eartip_l", "eartip_r", "earlobe_l", "earlobe_r"):
    if k in S and HL.get(k) is not None:
        src_pts.append(aff(HL[k][None])[0]); dst_pts.append(S[k])
# eyes: corners + upper / lower margins by arc length
for e, c_s, cont in zip(eyes, sc, scont):
    p0, p1, upc, loc = split_contour(cont)
    c0, c1 = e["corners"]
    for chain, tgt in ((e["up"], upc), (e["lo"], loc)):
        full = np.concatenate([[c0], chain, [c1]])
        t = arclen_t(P1[full])
        T = along(clean_poly(np.vstack([p0, tgt, p1]), 24, 5), t)
        # open the lid line a little beyond the liner's inner edge (the lid rim + waterline otherwise eat ~1/3 of it)
        cc2 = 0.5 * (p0 + p1); dv = T - cc2; dv[:, 1] = 0.0
        dn = np.linalg.norm(dv, axis=1, keepdims=True); dv = dv / np.maximum(dn, 1e-9)
        EXP = float(os.environ.get("HYB_EYE_EXPAND", "0.004"))
        T = T + EXP * dv * np.clip(dn / 0.01, 0, 1)
        for q_, (i, p) in enumerate(zip(full, T)):
            pins[int(i)] = p
            if q_ in (0, len(full) // 2, len(full) - 1):
                src_pts.append(P1[i]); dst_pts.append(p)
    cc = aff(e["c"][None])[0]
    src_pts.append(cc); dst_pts.append(cc + (c_s - P1[np.concatenate([e["up"], e["lo"]])].mean(0)) * np.array([1.0, 0.5, 1.0]))
# lip line (both rip sides on the source lip line), corners = source mouth corners
slit = clean_poly(SM["slit"], 30, 5)
lolip = clean_poly(SM["lolip"], 24, 5) if "lolip" in SM.files else slit
for line in (lip_up_line, lip_lo_line):
    t = arclen_t(P1[line])
    T = along(slit if line is lip_up_line else lolip, t)
    for q_, (i, p) in enumerate(zip(line, T)):
        pins[int(i)] = p
        if line is lip_up_line and q_ in (0, len(line) // 4, len(line) // 2, 3 * len(line) // 4, len(line) - 1):
            src_pts.append(P1[i]); dst_pts.append(p)
# vermilion borders (landmarks only)
for bv, poly in ((bu, SM["up"]), (bl, SM["lo"])):
    bv = bv[np.argsort(P1[bv, 0])]
    poly = poly[np.argsort(poly[:, 0])]
    t = (P1[bv, 0] - P1[bv, 0].min()) / (P1[bv, 0].max() - P1[bv, 0].min())
    T = along(poly, t)
    sel = np.linspace(0, len(bv) - 1, min(3, len(bv))).astype(int)[1:-1]
    for i in sel:
        src_pts.append(P1[bv[i]]); dst_pts.append(T[i])
low = np.nonzero(P1[:, 2] < P1[:, 2].min() + 0.03)[0]
for k, sg in (("neck_f", (0, -1)), ("neck_b", (0, 1)), ("neck_l", (1, 0)), ("neck_r", (-1, 0))):
    i = low[np.argmax(sg[0] * P1[low, 0] + sg[1] * P1[low, 1])]
    src_pts.append(P1[i]); dst_pts.append(S[k] + np.array([0.0, 0.0, P1[i, 2] - S[k][2]]))
Xs = np.array(src_pts); Yd = np.array(dst_pts)
# dedupe coincident sources (the rip duplicates share positions)
_, keep = np.unique(np.round(Xs, 7), axis=0, return_index=True); keep = np.sort(keep); Xs, Yd = Xs[keep], Yd[keep]
n = len(Xs)
K = np.linalg.norm(Xs[:, None] - Xs[None], axis=-1)
Pm = np.hstack([np.ones((n, 1)), Xs])
A = np.zeros((n + 4, n + 4)); A[:n, :n] = K + 2e-3 * np.eye(n); A[:n, n:] = Pm; A[n:, :n] = Pm.T
bvec = np.zeros((n + 4, 3)); bvec[:n] = Yd
W = np.linalg.solve(A, bvec)
tps = lambda P: np.linalg.norm(P[:, None] - Xs[None], axis=-1) @ W[:n] + np.hstack([np.ones((len(P), 1)), P]) @ W[n:]
P2 = np.vstack([tps(P1[i:i + 1500]) for i in range(0, nv, 1500)])
rep["tps_landmarks"] = int(n)

# ---------------------------------------------------------------- projection + detail-preserving relax
Fs = Zs["F"][Zs["flab"] == 0]
tS = U.bvh(Zs["V"], Fs)
skip = np.zeros(nv, bool); skip[bag] = True
for e in eyes:
    skip[e["pocket"]] = True
pin_i = np.array(sorted(pins.keys()), np.int64)
# soft = the lid / lip skirts near the pinned loops: never projected (the source has crevices / lash rims there), only
# relaxed with the template's own Laplacian between the pinned loop and the projected skin
soft = np.zeros(nv, bool)
for e in eyes:
    for i, r in e["ring"].items():
        if 2 <= r <= 7:
            soft[i] = True
fr = set(lip_up_line.tolist()) | set(lip_lo_line.tolist()); seen = set(fr)
for r in range(2):
    nf = set()
    for i in fr:
        for j in nbr[i]:
            if j not in seen:
                nf.add(j); seen.add(j)
    for j in nf:
        soft[j] = True
    fr = nf
soft[skip] = False; soft[pin_i] = False
rep["soft_verts"] = int(soft.sum())
mv = ~skip; mv[pin_i] = False; mv[soft] = False
mvi = np.nonzero(mv)[0]
deg = np.array([len(x) for x in nbr])
rows = np.concatenate([[i] * len(x) for i, x in enumerate(nbr)]).astype(np.int64)
cols = np.concatenate([list(x) for x in nbr]).astype(np.int64)


def lap(Q):
    a_ = np.zeros_like(Q); np.add.at(a_, rows, Q[cols]); return a_ / deg[:, None] - Q


def vnormals(Q):
    Nn = np.zeros_like(Q)
    for k in range(4):
        a_, b_, c_ = Fq[:, k], Fq[:, (k + 1) % 4], Fq[:, (k - 1) % 4]
        np.add.at(Nn, a_, np.cross(Q[b_] - Q[a_], Q[c_] - Q[a_]))
    return Nn / np.maximum(np.linalg.norm(Nn, axis=1), 1e-12)[:, None]


Dp = np.zeros_like(P2); fixed = np.zeros(nv, bool)
for i, p in pins.items():
    Dp[i] = p - P2[i]; fixed[i] = True
for _ in range(60):                               # harmonic spread of the pin residuals (decaying away from the pins)
    a_ = np.zeros_like(Dp); np.add.at(a_, rows, Dp[cols]); a_ /= deg[:, None]
    Dp[~fixed] = 0.97 * a_[~fixed]
P2 = P2 + Dp
L0 = lap(P2)
Q = P2.copy()
# smoothing weight of the projection displacement: high near the lid / lip loops and the nostrils (the source has
# crevices / lash rims there that a template vertex must not dive into), low on open skin
near = np.zeros(nv)
for e in eyes:
    for i, r in e["ring"].items():
        if 1 <= r <= 9:
            near[i] = max(near[i], 1.0 - (r - 1) / 9.0)
fr = set(lip_up_line.tolist()) | set(lip_lo_line.tolist()); seen = set(fr)
for r in range(1, 5):
    nf = set()
    for i in fr:
        for j in nbr[i]:
            if j not in seen:
                nf.add(j); seen.add(j)
    for j in nf:
        near[j] = max(near[j], 1.0 - (r - 1) / 4.0)
    fr = nf
nose = (np.abs(P2[:, 0]) < 0.075) & (P2[:, 2] > -0.07) & (P2[:, 2] < 0.04) & (P2[:, 1] < -0.24)
near[nose] = np.maximum(near[nose], 0.7)
alpha = 0.25 + 0.7 * near
mvp = ~skip; mvp[pin_i] = False; mvp[soft & (near > 0)] = False
lidsoft = soft.copy()
for _li in range(2):
    pass
NIT = 14
for it in range(NIT):
    D = np.zeros_like(Q)
    for i in np.nonzero(mvp)[0]:
        h = tS.find_nearest(Vector(Q[i]))
        if h[0] is not None:
            D[i] = np.array(h[0]) - Q[i]
    for _ in range(3):
        a_ = np.zeros_like(D); np.add.at(a_, rows, D[cols]); a_ /= deg[:, None]
        D[mvp] = (1 - alpha[mvp, None]) * D[mvp] + alpha[mvp, None] * a_[mvp]
    Q[mvp] = Q[mvp] + D[mvp]
    if it < NIT - 2:                               # keep the template's own detail a little (lid / lip shapes)
        dl = lap(Q) - L0
        Q[mvp] = Q[mvp] + (0.15 + 0.5 * near[mvp, None]) * dl[mvp]
# open skin: exact surface at the end
far = mvp & (near < 0.05)
for i in np.nonzero(far)[0]:
    h = tS.find_nearest(Vector(Q[i]))
    if h[0] is not None:
        Q[i] = np.array(h[0])
soft[:] = False
soft |= lidsoft & ~np.isin(np.arange(nv), np.concatenate([lip_up_line, lip_lo_line]))
# keep the lip skirts projected (they were fine); only the lid skirts are interpolated
lipsk = np.zeros(nv, bool)
for e in eyes:
    pass
# untangle: faces folded against their neighbours -> local pure-Laplacian relax (pins kept), then reproject open skin
def face_normals(Q):
    n_ = np.cross(Q[Fq[:, 2]] - Q[Fq[:, 0]], Q[Fq[:, 3]] - Q[Fq[:, 1]])
    return n_ / np.maximum(np.linalg.norm(n_, axis=1), 1e-12)[:, None]
vf = [[] for _ in range(nv)]
for fi_, f in enumerate(Fq):
    for v_ in f:
        vf[v_].append(fi_)
untangled = []
for rnd in range(12):
    fn = face_normals(Q)
    vn = np.zeros_like(Q)
    for k in range(4):
        np.add.at(vn, Fq[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    fold = np.array([min(float(fn[fi_] @ vn[v_]) for v_ in f) for fi_, f in enumerate(Fq)]) < 0.2
    fold &= ~np.array([all(skip[v_] for v_ in f) for f in Fq])
    untangled.append(int(fold.sum()))
    if not fold.any():
        break
    sel = np.zeros(nv, bool); sel[np.unique(Fq[fold])] = True
    for i in np.nonzero(sel)[0]:
        for j in nbr[i]:
            sel[j] = True
    sel[pin_i] = False; sel[skip] = False
    for _ in range(8):
        a_ = np.zeros_like(Q); np.add.at(a_, rows, Q[cols]); a_ /= deg[:, None]
        Q[sel] = a_[sel]
    for i in np.nonzero(sel & (near < 0.3))[0]:
        h = tS.find_nearest(Vector(Q[i]))
        if h[0] is not None:
            Q[i] = np.array(h[0])
rep["folded_faces_per_round"] = untangled
for _ in range(60):                                # the soft skirts: Laplacian interpolation with the template detail
    a_ = np.zeros_like(Q); np.add.at(a_, rows, Q[cols]); a_ /= deg[:, None]
    Q[soft] = (a_ - L0)[soft]
# pockets / bag follow the surrounding displacement smoothly
D = Q - P2
for _ in range(40):
    a_ = np.zeros_like(D); np.add.at(a_, rows, D[cols]); a_ /= deg[:, None]
    D[skip] = a_[skip]
Q[skip] = P2[skip] + D[skip]
dist = U.dists(tS, Q[mv])
rep["fit_to_src_p50_p99_max"] = [round(float(np.percentile(dist, 50)), 5), round(float(np.percentile(dist, 99)), 5), round(float(dist.max()), 5)]

# ---------------------------------------------------------------- eyeballs: radius from the template, centre fitted
ecov = np.load(J_("s2", "edit_eyecover.npy"))
Fcv = Zs["F"][ecov]; cen = Zs["V"][Fcv].mean(1)
balls = []
for e, c_s in zip(eyes, sc):
    side = np.sign(c_s[0])
    Pc = Zs["V"][np.unique(Fcv[np.sign(cen[:, 0]) == side])]
    R = e["R"] * sx
    c = Pc.mean(0) + np.array([0.0, R * 0.9, 0.0])
    for _ in range(200):
        dv = Pc - c; dn = np.linalg.norm(dv, axis=1)
        c = c + 0.5 * ((dn - R)[:, None] * (dv / dn[:, None])).mean(0)
    # the lid margins must ride on / outside the ball: push the centre back if a margin vertex is inside
    mg = np.concatenate([e["up"], e["lo"], list(e["corners"])])
    dm = np.linalg.norm(Q[mg] - c, axis=1)
    pen = float(R + 0.0008 - dm.min())
    if pen > 0:
        c = c + np.array([0.0, pen, 0.0])
    balls.append((c, R))
    e["ball"] = (c, R)
rep["eyeballs"] = [{"centre": c.round(5).tolist(), "R": round(R, 5)} for c, R in balls]
# per-eye ring index (blink falloff)
ringarr = np.full((2, nv), -1, np.int64)
for k, e in enumerate(eyes):
    for i, r in e["ring"].items():
        ringarr[k, i] = r
UVq = VT[FTq]
np.savez_compressed(J_("s3", "hyb_fit.npz"), V=Q, Fq=Fq, UVq=UVq, P_tps=P2, P0=P0, nv0=nv0,
                    lip_up_line=lip_up_line, lip_lo_line=lip_lo_line, bag=bag, up_f=up_f, lo_f=lo_f,
                    eye_up_l=eyes[0]["up"], eye_lo_l=eyes[0]["lo"], eye_up_r=eyes[1]["up"], eye_lo_r=eyes[1]["lo"],
                    eye_corners=np.array([eyes[0]["corners"], eyes[1]["corners"]]),
                    pocket_l=eyes[0]["pocket"], pocket_r=eyes[1]["pocket"], ring=ringarr,
                    ball_c=np.array([b[0] for b in balls]), ball_r=np.array([b[1] for b in balls]),
                    tps_X=Xs, tps_W=W, aff_hc=hc, aff_hcs=hcs, aff_scl=scl, lipz=lipz)
json.dump(rep, open(J_("s3", "hyb_fit.json"), "w"), indent=1)
print("FIT", json.dumps(rep))
