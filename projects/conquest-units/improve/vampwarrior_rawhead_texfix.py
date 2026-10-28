"""vampwarrior_rawhead_texfix.py -- <hybrid_work_v2_dir> <out_png> <out_json> : paint-out-only clean-up of the RAW head7 texture
(edit_tex_orig.png = the glb texture, unchanged UVs). Derived from hybrid/scripts_v2/texedit.py steps 1 + 3, but in the RAW
(unwarped) frame and WITHOUT v2's added liner band / lash / crease strokes and WITHOUT the skin recolour:
  1. eye zones: grey smears under / beside the eyes and grey / peach patches on the upper-lid crease are blended back to the
     local clean skin (Lab), EXCEPT the opening, the upper liner + wing, the brows, and a thin dark lower lash line
     (dark texels within LOWER_KEEP of the lower opening edge).
  2. ghost lines above the brows (forehead) are painted out.
  3. (RH_LOWER_BAND=<width>, with RH_LOWER_KEEP=0) the broken lower lash bits are replaced by one clean soft grey line
     (the raw smear's colour, L-4) following the SMOOTHED lower opening contour; it also covers the sawtooth
     white / skin border there. Width tapers to 35 % at the corners. RH_BAND_ALL=<d>: also paint over any colour
     within d above the smooth contour (stray white / iris wedges); RH_BAND_EXT=<f>: run the line f x eye width past
     the corners along the corner chord.
Edits are made 'in 3D' through the baked texel positions s2/edit_pos.npy (unwarped f-frame) + s2/edit_pos_lab.npy."""
import sys, os, json, numpy as np
from PIL import Image
from scipy import ndimage
sys.path.insert(0, r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units\improve")
import proxy_finish as PF
W, OUTP, OUTJ = sys.argv[1], sys.argv[2], sys.argv[3]
LOWER_KEEP = float(os.environ.get("RH_LOWER_KEEP", "0.0018"))
J = lambda *p: os.path.join(W, *p)
P = np.load(J("s2", "edit_pos.npy")); LB = np.load(J("s2", "edit_pos_lab.npy"))
T0 = np.asarray(Image.open(J("s2", "edit_tex_orig.png")).convert("RGB")).astype(np.float32)
H_, W_ = T0.shape[:2]
ok = (LB == 0) & np.isfinite(P[..., 0])
iy, ix = np.nonzero(ok)
Pk = P[iy, ix].astype(np.float64); Ck = T0[iy, ix].astype(np.float64); Lk = PF.srgb_to_lab(Ck)
rep = {}
SK0 = np.array([190.0, 178.0, 175.0]); LAB0 = PF.srgb_to_lab(SK0[None])[0]
clean = np.linalg.norm(Lk - LAB0, axis=1) < 3.0
G = 0.02
gi = np.floor((Pk + 0.6) / G).astype(int); gs = int(1.2 / G) + 1
acc = np.zeros((gs, gs, gs, 3)); cnt = np.zeros((gs, gs, gs))
np.add.at(acc, (gi[clean, 0], gi[clean, 1], gi[clean, 2]), Lk[clean]); np.add.at(cnt, (gi[clean, 0], gi[clean, 1], gi[clean, 2]), 1)
for k in range(3):
    acc[..., k] = ndimage.gaussian_filter(acc[..., k], 1.0)
cnt = ndimage.gaussian_filter(cnt, 1.0)
cleanLab = np.where(cnt[..., None] > 1e-3, acc / np.maximum(cnt[..., None], 1e-9), LAB0)
CL = cleanLab[gi[:, 0], gi[:, 1], gi[:, 2]]
dE = np.linalg.norm(Lk - CL, axis=1); dL = Lk[:, 0] - CL[:, 0]
dark = Lk[:, 0] < 48
front = Pk[:, 1] < -0.02
EC = np.load(J("s2", "edit_eyecontour_orig.npz"))
RES = 0.0004


def split(cont):
    i0 = np.argmin(cont[:, 0]); i1 = np.argmax(cont[:, 0]); p0, p1 = cont[i0], cont[i1]
    t = (cont[:, 0] - p0[0]) / (p1[0] - p0[0]); zl = p0[2] + t * (p1[2] - p0[2])
    up = cont[cont[:, 2] >= zl]; lo = cont[cont[:, 2] < zl]
    return p0, p1, up[np.argsort(up[:, 0])], lo[np.argsort(lo[:, 0])]


def binned(Pc, x):
    e = np.linspace(Pc[:, 0].min(), Pc[:, 0].max(), 25)
    c, z = [], []
    for a, b in zip(e[:-1], e[1:]):
        m = (Pc[:, 0] >= a) & (Pc[:, 0] <= b)
        if m.any():
            c.append(0.5 * (a + b)); z.append(np.median(Pc[m, 2]))
    return np.interp(x, c, z)


def raster_dilate(sel, region, r):
    x0, x1 = Pk[region, 0].min() - 0.01, Pk[region, 0].max() + 0.01
    z0, z1 = Pk[region, 2].min() - 0.01, Pk[region, 2].max() + 0.01
    nx, nz = int((x1 - x0) / RES) + 1, int((z1 - z0) / RES) + 1
    jj = ((Pk[:, 0] - x0) / RES).astype(int); ii = ((z1 - Pk[:, 2]) / RES).astype(int)
    img = np.zeros((nz, nx), bool); s = sel & region
    img[np.clip(ii[s], 0, nz - 1), np.clip(jj[s], 0, nx - 1)] = True
    rr = max(1, int(round(r / RES))); yy, xx = np.mgrid[-rr:rr + 1, -rr:rr + 1]
    img = ndimage.binary_dilation(img, structure=(xx * xx + yy * yy) <= rr * rr)
    out = np.zeros(len(Pk), bool); inb = region & (ii >= 0) & (ii < nz) & (jj >= 0) & (jj < nx)
    out[inb] = img[ii[inb], jj[inb]]
    return out


blendw = np.zeros(len(Pk)); eyezone_all = np.zeros(len(Pk), bool)
BAND = float(os.environ.get("RH_LOWER_BAND", "0")); BAND_UP = 0.0015
BAND_ALL = float(os.environ.get("RH_BAND_ALL", "0"))      # also cover ANY colour (white / iris specks) this far above the smooth contour
BAND_EXT = float(os.environ.get("RH_BAND_EXT", "0"))      # extend the line past the corners (fraction of the eye width)
band_a = np.zeros(len(Pk)); band_lab = np.zeros((len(Pk), 3))
for k in (0, 1):
    p0, p1, up, lo = split(EC["eye%d" % k])
    side = 1 if p0[0] > 0 else -1
    x, z = Pk[:, 0], Pk[:, 2]
    t = (x - p0[0]) / (p1[0] - p0[0]); zl = p0[2] + t * (p1[2] - p0[2])
    xc = np.clip(x, p0[0], p1[0]); zup = binned(up, xc); zlo = binned(lo, xc)
    inside_x = (t > 0) & (t < 1)
    zup = np.where(inside_x, zup, zl); zlo = np.where(inside_x, zlo, zl)
    to = np.where(side > 0, t, 1 - t)
    zone = front & (x > p0[0] - (0.05 if side < 0 else 0.02)) & (x < p1[0] + (0.05 if side > 0 else 0.02)) & \
        (z > zlo - 0.030) & (z < zup + 0.045) & (Pk[:, 1] < -0.08)
    eyezone_all |= zone
    opening = inside_x & (z < zup) & (z > zlo)
    liner_old = zone & dark & (z >= zl - 0.001) & (z - zup <= 0.012) & ~opening & (to > -0.02) & (to < 1.55)
    brow = zone & (Lk[:, 0] < 36) & (z - zup > 0.010)
    lowline = zone & dark & inside_x & (zlo - z >= 0) & (zlo - z <= LOWER_KEEP)
    prot = raster_dilate(liner_old, zone, 0.0012) | raster_dilate(brow, zone, 0.0025) | raster_dilate(lowline, zone, 0.0004)
    w = np.clip((dE - 3.5) / 3.5, 0, 1)
    w = np.where(zone & ~opening & ~prot, w, 0.0)
    blendw = np.maximum(blendw, w)
    if BAND > 0:      # a clean soft lower lid line along the SMOOTHED lower opening contour (replaces the broken lash bits)
        tt = np.clip(t, 0, 1); Wb = BAND * (0.35 + 0.65 * np.sqrt(np.sin(np.pi * tt)))
        inx = (t > -BAND_EXT) & (t < 1 + BAND_EXT)
        zlo_e = np.where(inside_x, zlo, zl)
        if BAND_EXT > 0:      # beyond the corners follow the corner-to-corner chord, thin
            Wb = np.where(inside_x, Wb, BAND * 0.35)
        dz = zlo_e - z
        smear = zone & (w > 0.5) & (dz > 0)
        sl = np.median(Lk[smear], axis=0); sl[0] -= 4.0
        chroma = np.hypot(Lk[:, 1], Lk[:, 2])
        greyish = (Lk[:, 0] < 70) & (chroma < 18)
        below = zone & inx & (dz >= 0) & (dz <= Wb)
        above = zone & inside_x & (dz < 0) & (((-dz <= BAND_UP) & greyish) | (-dz <= BAND_ALL))
        a = np.where(below, np.clip((Wb - dz) / (0.45 * Wb), 0, 1), 0.0) + np.where(above & ~below, 1.0, 0.0)
        if BAND_EXT > 0:
            a = a * np.clip(np.minimum(t + BAND_EXT, 1 + BAND_EXT - t) / 0.04, 0, 1)
        else:
            a = a * np.clip(np.minimum(tt, 1 - tt) / 0.04, 0, 1)          # fade in over the corners
        upd = a > band_a
        band_a[upd] = a[upd]; band_lab[upd] = sl
        rep["eye%d_band" % k] = {"texels": int((a > 0.5).sum()), "colour_lab": sl.round(1).tolist()}
    rep["eye%d" % k] = {"zone_texels": int(zone.sum()), "protected": int((prot & zone).sum()), "painted_out": int((w > 0.5).sum())}
fz = front & (np.abs(Pk[:, 0]) > 0.04) & (np.abs(Pk[:, 0]) < 0.32) & (Pk[:, 2] > 0.11) & (Pk[:, 2] < 0.30) & (Pk[:, 1] < -0.0)
browall = fz & (Lk[:, 0] < 36)
bprot = raster_dilate(browall, fz, 0.0028)
gw = np.where(fz & ~bprot & ~eyezone_all, np.clip((-dL - 1.5) / 2.0, 0, 1), 0.0)
blendw = np.maximum(blendw, gw)
rep["ghost_painted"] = int((gw > 0.5).sum())
outLab = Lk * (1 - blendw[:, None]) + CL * blendw[:, None]
outLab = outLab * (1 - band_a[:, None]) + band_lab * band_a[:, None]
newC = PF.lab_to_srgb(outLab).astype(np.float64)
T1 = T0.copy(); T1[iy, ix] = np.clip(newC, 0, 255)
_cov = np.zeros((H_, W_), bool); _cov[iy, ix] = True; _cov |= (LB > 0)
# gutters: carry only the paint-out DELTA of the nearest island texel (<= 16 px), so untouched areas stay byte-identical
_dist, (_gy, _gx) = ndimage.distance_transform_edt(~_cov, return_indices=True)
_D = T1 - T0
_g = (~_cov) & (_dist <= 16)
T1[_g] = np.clip(T0[_g] + _D[_gy[_g], _gx[_g]], 0, 255)
Image.fromarray(np.round(T1).astype(np.uint8)).save(OUTP)
rep["paint_out_texels"] = int((blendw > 0.5).sum()); rep["lower_keep"] = LOWER_KEEP
fm = front & (np.abs(Pk[:, 0]) < 0.25) & (Pk[:, 2] > -0.3) & (Pk[:, 2] < 0.3)
rep["face_skin_median_srgb"] = np.median(Ck[fm & clean], axis=0).round(1).tolist()
json.dump(rep, open(OUTJ, "w"), indent=1)
print("TEXFIX", json.dumps(rep))
