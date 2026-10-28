"""vampwarrior_rawhead_gutterfill.py -- <tex_island.png> <tex_padded.png> <raw.npz> <vis.npz> <coverage_lab.npy> <out.png> [raw_islands.npy] :
3D-aware gutter fill in the two eye boxes. A decimated face that straddles two raw UV islands samples past its island's
border (into the gutter); plain nearest-colour padding then smears that island's edge colour (the red iris wedges, white
spikes). Here each eye-box gutter texel within 40 px of a VISIBLE raw face's island is given a 3D position by extending
that face's UV->3D map, and takes the colour of the nearest visible raw texel in 3D (texture tex_island). Island texels
are not touched (byte-identical), so the raw mapping is unchanged."""
import sys, numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree
TI = np.asarray(Image.open(sys.argv[1]).convert("RGB")); TP = np.asarray(Image.open(sys.argv[2]).convert("RGB")).copy()
R = np.load(sys.argv[3]); V, F, U = R["V"], R["F"], R["FUV"]; VIS = np.load(sys.argv[4])["vis"]; LB = np.load(sys.argv[5])
th, tw = TI.shape[:2]
C = V[F].mean(1)
sel = np.nonzero((C[:, 1] < -0.06) & (np.abs(np.abs(C[:, 0]) - 0.13) < 0.11) & (C[:, 2] > 0.03) & (C[:, 2] < 0.14))[0]
FID = np.full((th, tw), -1, np.int64); POS = np.full((th, tw, 3), np.nan)
for f in sel:
    uv = U[f] * np.array([tw, th]); a, b, c = uv
    x0, x1 = int(np.floor(uv[:, 0].min())), int(np.ceil(uv[:, 0].max())); y0, y1 = int(np.floor(uv[:, 1].min())), int(np.ceil(uv[:, 1].max()))
    gx, gy = np.meshgrid(np.arange(max(x0, 0), min(x1 + 1, tw)), np.arange(max(y0, 0), min(y1 + 1, th)))
    q = np.stack([gx.ravel() + 0.5, gy.ravel() + 0.5], 1)
    v0, v1, v2 = b - a, c - a, q - a
    den = v0[0] * v1[1] - v1[0] * v0[1]
    if abs(den) < 1e-12:
        continue
    l1 = (v2[:, 0] * v1[1] - v1[0] * v2[:, 1]) / den; l2 = (v0[0] * v2[:, 1] - v2[:, 0] * v0[1]) / den; l0 = 1 - l1 - l2
    m = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    FID[gy.ravel()[m], gx.ravel()[m]] = f
    POS[gy.ravel()[m], gx.ravel()[m]] = l0[m, None] * V[F[f, 0]] + l1[m, None] * V[F[f, 1]] + l2[m, None] * V[F[f, 2]]
cov = (LB != -1) | (FID >= 0)
dist, (ny, nx) = ndimage.distance_transform_edt(~cov, return_indices=True)
nf = FID[ny, nx]
g = (~cov) & (dist <= 40) & (nf >= 0)
g &= VIS[np.maximum(nf, 0)]
gy, gx = np.nonzero(g); fs = nf[gy, gx]
q = np.stack([gx + 0.5, gy + 0.5], 1)
uv = U[fs] * np.array([tw, th]); a, b, c = uv[:, 0], uv[:, 1], uv[:, 2]
v0, v1, v2 = b - a, c - a, q - a
den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]; den = np.where(np.abs(den) < 1e-12, 1e-12, den)
l1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den; l2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den; l0 = 1 - l1 - l2
P3 = l0[:, None] * V[F[fs, 0]] + l1[:, None] * V[F[fs, 1]] + l2[:, None] * V[F[fs, 2]]
src = (FID >= 0) & VIS[np.maximum(FID, 0)]
sy, sx = np.nonzero(src)
tree = cKDTree(POS[sy, sx])
d, i = tree.query(P3)
near = d < 0.002                       # only where a visible raw texel really is there in 3D; else keep plain padding
TP[gy[near], gx[near]] = TI[sy[i[near]], sx[i[near]]]
Image.fromarray(TP).save(sys.argv[6])
# texel island map for vampwarrior_rawhead_uvextend.py: island id in the eye boxes, -2 other covered texels, -1 gutter
ISL = np.load(sys.argv[7]) if len(sys.argv) > 7 else None
if ISL is not None:
    TI_ = np.where(FID >= 0, ISL[np.maximum(FID, 0)], np.where(LB != -1, -2, -1)).astype(np.int32)
    np.save(sys.argv[6].replace(".png", "_texisl.npy"), TI_)
print("GUTTERFILL texels", len(gy), "filled", int(near.sum()), "3D dist p50 / p99", np.round(np.percentile(d, [50, 99]), 4).tolist())
