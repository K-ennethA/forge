"""vampwarrior_rawhead_eyefix.py -- <in_tex.png> <texel_pos.npy> <texel_lab.npy> <eyecontour.npz> <out_tex.png> <out.json> [raw.npz [vis.npz]] :
(hidden raw faces' texels are left alone and kept out of the colour statistics)
eye-zone-only texture clean-up on top of albedo_fix_v3 (raw UVs). Works 'in 3D' through the texel position map (front
view x / z). Per eye:
  1. iris: iris colours outside the opening or down in the lower-lid line are stray (the red wedges) -> handled by 2 / 3
     (line / skin); the iris inside the opening is untouched;
  2. lower lid line: ONE continuous soft grey line along the SMOOTHED lower opening contour (moving-average of the binned
     contour), from 0.0012 above it (any colour) to W below (soft lower edge), continued thin along the corner chord
     0.012 past each corner;
  3. below the line and around the corners (under the corner chord, so the liner wing is untouched): anything that is not
     skin (white flakes, grey bits) -> the local skin colour;
  4. inside the opening, grey lid bits below the upper liner -> sclera white.
Only texels in the two eye boxes change (asserted)."""
import sys, json, numpy as np
from PIL import Image
sys.path.insert(0, r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units\improve")
import proxy_finish as PF
T0 = np.asarray(Image.open(sys.argv[1]).convert("RGB")).astype(np.float64)
P = np.load(sys.argv[2]); LB = np.load(sys.argv[3]); EC = np.load(sys.argv[4]); OUTP, OUTJ = sys.argv[5], sys.argv[6]
W_BAND = 0.006; U_BAND = 0.0012; EXT = 0.012
# texel positions in the eye boxes re-rasterised from the RAW mesh (the posbake map came from the warped 'edit' head,
# which is off by up to ~0.01 around the eyes)
if len(sys.argv) > 7:
    R = np.load(sys.argv[7]); RV, RF, RU = R["V"], R["F"], R["FUV"]
    th_, tw_ = T0.shape[:2]
    Cf = RV[RF].mean(1)
    sel_f = np.nonzero((Cf[:, 1] < -0.06) & (np.abs(np.abs(Cf[:, 0]) - 0.13) < 0.11) & (Cf[:, 2] > 0.03) & (Cf[:, 2] < 0.14))[0]
    VISF = np.load(sys.argv[8])["vis"] if len(sys.argv) > 8 else np.ones(len(RF), bool)
    P = P.copy(); LB = LB.copy(); nras = 0
    for f in sel_f:
        uv = RU[f] * np.array([tw_, th_]); a_, b_, c_ = uv
        x0_, x1_ = int(np.floor(uv[:, 0].min())), int(np.ceil(uv[:, 0].max())); y0_, y1_ = int(np.floor(uv[:, 1].min())), int(np.ceil(uv[:, 1].max()))
        gx_, gy_ = np.meshgrid(np.arange(max(x0_, 0), min(x1_ + 1, tw_)), np.arange(max(y0_, 0), min(y1_ + 1, th_)))
        q = np.stack([gx_.ravel() + 0.5, gy_.ravel() + 0.5], 1)
        v0, v1, v2 = b_ - a_, c_ - a_, q - a_
        den = v0[0] * v1[1] - v1[0] * v0[1]
        if abs(den) < 1e-12:
            continue
        l1 = (v2[:, 0] * v1[1] - v1[0] * v2[:, 1]) / den; l2 = (v0[0] * v2[:, 1] - v2[:, 0] * v0[1]) / den; l0 = 1 - l1 - l2
        m = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not m.any():
            continue
        pos = l0[m, None] * RV[RF[f, 0]] + l1[m, None] * RV[RF[f, 1]] + l2[m, None] * RV[RF[f, 2]]
        P[gy_.ravel()[m], gx_.ravel()[m]] = pos; LB[gy_.ravel()[m], gx_.ravel()[m]] = 0 if VISF[f] else -2
        nras += int(m.sum()) if VISF[f] else 0
    print("RASTER eye-box faces", len(sel_f), "texels", nras, flush=True)
ok = (LB == 0) & np.isfinite(P[..., 0])
iy, ix = np.nonzero(ok)
Pk = P[iy, ix].astype(np.float64); Ck = T0[iy, ix]; Lk = PF.srgb_to_lab(Ck)
chroma = np.hypot(Lk[:, 1], Lk[:, 2])
x, y, z = Pk[:, 0], Pk[:, 1], Pk[:, 2]
out = Lk.copy(); changed = np.zeros(len(Pk), bool); rep = {}


def smooth_curve(pts, xs, nb=28, win=5):
    e = np.linspace(pts[:, 0].min(), pts[:, 0].max(), nb + 1); c, v = [], []
    for a, b in zip(e[:-1], e[1:]):
        m = (pts[:, 0] >= a) & (pts[:, 0] <= b)
        if m.any():
            c.append(0.5 * (a + b)); v.append(np.median(pts[m, 2]))
    c, v = np.array(c), np.array(v)
    k = np.ones(win) / win; vp = np.r_[np.full(win // 2, v[0]), v, np.full(win // 2, v[-1])]
    vs = np.convolve(vp, k, mode="valid")
    vs[0], vs[-1] = v[0], v[-1]
    return np.interp(xs, c, vs)


for k in (0, 1):
    cont = EC["eye%d" % k]
    i0 = np.argmin(cont[:, 0]); i1 = np.argmax(cont[:, 0]); p0, p1 = cont[i0], cont[i1]
    t = (x - p0[0]) / (p1[0] - p0[0]); chord = p0[2] + t * (p1[2] - p0[2])
    up = cont[cont[:, 2] >= p0[2] + (cont[:, 0] - p0[0]) / (p1[0] - p0[0]) * (p1[2] - p0[2])]
    lo = cont[cont[:, 2] < p0[2] + (cont[:, 0] - p0[0]) / (p1[0] - p0[0]) * (p1[2] - p0[2])]
    xc = np.clip(x, p0[0], p1[0])
    inside = (t > 0) & (t < 1)
    zlo = np.where(inside, smooth_curve(lo, xc), chord)
    zup = np.where(inside, smooth_curve(up, xc), chord)
    box = (y < -0.08) & (x > p0[0] - 0.035) & (x < p1[0] + 0.035) & (z > min(p0[2], p1[2]) - 0.04) & (z < max(p0[2], p1[2]) + 0.03)
    opening = box & inside & (z > zlo) & (z < zup)
    skin_ref = box & inside & (z < zlo - 0.015) & (z > zlo - 0.03)
    skin = np.median(Lk[skin_ref], axis=0)
    white_ref = opening & (Lk[:, 0] > 80) & (chroma < 12)
    white = np.median(Lk[white_ref], axis=0)
    band_ref = box & inside & (z < zlo) & (z > zlo - 0.003) & (Lk[:, 0] < 40)
    band = np.median(Lk[band_ref], axis=0) if band_ref.sum() > 50 else np.array([29.5, 1.8, 2.8])
    # 1. iris circle
    irisc = opening & (chroma > 28) & (Lk[:, 1] > 15)
    Q = Pk[irisc][:, [0, 2]]
    keep = np.ones(len(Q), bool)
    for _ in range(6):
        cx, cz = np.median(Q[keep], axis=0)
        d = np.hypot(Q[:, 0] - cx, Q[:, 1] - cz)
        r = np.percentile(d[keep], 97)
        keep = d <= r * 1.02
    dI = np.hypot(x - cx, z - cz)
    # stray = iris colour outside the opening or down in the lower-lid line (the circle fit is reported only: the irises
    # are not circles where the lids cut them, so a circle test chops real iris)
    stray = box & (chroma > 28) & (Lk[:, 1] > 15) & ~(opening & (z > zlo + 0.0015))
    st_in = stray & opening
    out[st_in] = white; changed |= st_in
    # 4. grey lid bits inside the opening (below the upper liner) -> white
    grey_in = opening & (z < zup - 0.004) & (z > zlo + U_BAND) & (Lk[:, 0] > 28) & (Lk[:, 0] < 78) & (chroma < 14) & ~irisc
    out[grey_in] = white; changed |= grey_in
    # 2. the lower line
    tt = np.clip(t, 0, 1)
    Wb = W_BAND * (0.35 + 0.65 * np.sqrt(np.sin(np.pi * tt)))
    span = (x > p0[0] - EXT) & (x < p1[0] + EXT)
    Wb = np.where(inside, Wb, W_BAND * 0.35)
    dz = zlo - z
    a_core = box & span & (dz >= -np.where(inside, U_BAND, 0.0006)) & (dz <= Wb)
    a = np.where(a_core, np.clip((Wb - dz) / (0.45 * Wb), 0, 1), 0.0)
    xo = np.minimum(x - (p0[0] - EXT), (p1[0] + EXT) - x)
    a *= np.clip(xo / 0.004, 0, 1)
    sel = a > 0
    out[sel] = out[sel] * (1 - a[sel, None]) + band * a[sel, None]; changed |= sel
    # 3. below the line / around the corners, under the chord: non-skin -> skin
    reg = box & (x > p0[0] - 0.03) & (x < p1[0] + 0.03) & (dz > Wb * 0.55) & (z < np.minimum(zlo, chord) - 0.0015)
    dE = np.linalg.norm(Lk - skin, axis=1)
    wsk = np.where(reg & (Lk[:, 0] > 36), np.clip((dE - 4.0) / 4.0, 0, 1), 0.0)
    wsk = np.maximum(wsk, np.where(stray & ~opening, 1.0, 0.0))
    wsk *= (1 - a)
    s2 = wsk > 0
    out[s2] = out[s2] * (1 - wsk[s2, None]) + skin * wsk[s2, None]; changed |= s2
    rep["eye%d" % k] = {"iris_c": [round(float(cx), 4), round(float(cz), 4)], "iris_r": round(float(r), 4),
                        "stray_iris": int(stray.sum()), "grey_in_opening": int(grey_in.sum()), "line_texels": int((a > 0.5).sum()),
                        "skin_fixed": int((wsk > 0.5).sum()), "skin_lab": skin.round(1).tolist(), "white_lab": white.round(1).tolist(),
                        "band_lab": band.round(1).tolist()}
newC = np.clip(PF.lab_to_srgb(out), 0, 255)
T1 = T0.copy(); T1[iy[changed], ix[changed]] = newC[changed]
Image.fromarray(np.round(T1).astype(np.uint8)).save(OUTP)
rep["changed_texels"] = int(changed.sum())
rep["changed_bbox_3d"] = [Pk[changed].min(0).round(3).tolist(), Pk[changed].max(0).round(3).tolist()]
json.dump(rep, open(OUTJ, "w"), indent=1)
print("EYEFIX", json.dumps(rep))
